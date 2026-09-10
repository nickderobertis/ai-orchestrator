"""What `just channel-reply` refuses, and what its answer says about each note.

Three behaviours, driven against a live run:

* a ruling echoing the correlation token of a blocking question this run has not handed
  out is refused where it is sent, naming that question, leaving the queue as it was; the
  same envelope is accepted once that question is pending. An envelope carrying commands
  beside such a ruling is refused whole; one carrying commands alone is never refused;
* an envelope echoing no such token is never refused, whatever the queue holds. A monitor
  score is exactly that shape and is claimed by a reader at `pending: null`;
* the verb's answer carries, per `note` the envelope sent, the disposition the engine
  recorded for that note — correlated to the note rather than read off the journal's end,
  and `null` where nothing has decided it yet.

Everything here is real: the recipe, the `onepipeline` beneath it, `scripts/ask-manager.sh`
raising real blocking questions, and `scripts/channel-serve.py` raising the monitor's own
surface. Only the paid model is doubled, at the `oneharness` seam, exactly as
`tests/ask_seam/test_ask_manager_e2e.py` doubles it.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, NamedTuple, NewType, TypedDict, TypeVar, cast

import pytest
from fake_backend import AGENT_DELAY_ENV
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from planner_channel import TOKEN
from project_fixtures import helper, project_from_plan
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The three real programs on the far ends of this channel: the wrapper a dispatched
#: agent asks through, the filter the monitor is watched through, and the recipe under
#: test. None is doubled — what each does to the queue is the whole subject.
ASK_MANAGER = REPO_ROOT / "scripts" / "ask-manager.sh"
CHANNEL_SERVE = REPO_ROOT / "scripts" / "channel-serve.py"

#: The stand-in for the paid model and the provider binary beneath it.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")

#: A launching session these journeys state rather than inherit: this suite runs inside
#: a dispatch whose own harness session would otherwise own the runs.
LAUNCHING_SESSION = "e2e-channel-reply"

#: Every name a launcher identity reaches `scripts/onepipeline.sh` through, plus the run
#: the enclosing dispatch belongs to — which `scripts/ask-manager.sh` reads, so a
#: journey that did not clear it would be asking the *outer* run's channel.
INHERITED_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    # The enclosing dispatch's asker. An engine that composes one puts it in every
    # dispatch's environment, so a journey that kept it would measure the *outer*
    # dispatch's name — and a launch of its own would hand that same name to the
    # dispatch it makes, where a gate over the engine composing one would then pass
    # on a value the engine never composed.
    "ONEPIPELINE_CHANNEL_ASKER",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: What makes this suite run's run ids its own. Keyed on the checkout and this worker's
#: process, because both collide: two checkouts of this repository run at once here, and
#: so do two suites of one checkout.
SUITE = hashlib.sha256(f"{REPO_ROOT}\0{os.getpid()}".encode()).hexdigest()[:8]

#: The agent node every plan below carries, and the human gate that holds it. Nothing is
#: ever dispatched from the gated plan — the node exists so a `note` has a real
#: node to be addressed to.
WORK_NODE = "work"
GATE_NODE = "gate"

#: What `just channel-reply` exits with when it refuses. Asserted exactly rather than as
#: "not zero", because a refusal and a crash are both non-zero and only one of them
#: leaves the queue answerable.
REPLY_REFUSED = 2

#: The reply window a question is asked under while a journey looks for it. Long, for the
#: reason `tests/ask_seam/test_ask_manager_e2e.py` states: with it the other way round
#: the wrapper gives up first and the journey reports the channel's timeout refusal
#: rather than its own subject.
ASK_WINDOW_SECONDS = int(e2e_timeout(120))

#: How long a journey waits for a question, a surface, or a suspended reply.
PATIENCE_SECONDS = 45

#: How long the dispatched worker in the suspended-driver journey is held for. Long
#: enough that the run is still live while the driver is stopped and started again.
WORKER_HELD_SECONDS = 240

#: `tests/e2e/nx_workspace.py`'s group, applied to the module rather than per journey.
#: Every journey here spends a real `just orchestrate` through a fixture, and every step
#: of the round trip is a `just` recipe blocking on `uv run`, which waits on the
#: exclusive lock a journey re-provisioning this checkout holds.
#: `tests/test_nx_cache_scope.py` holds that rule and records what an ungrouped one cost.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

#: A onepipeline run id. Distinguished from the prose it is built out of, because what
#: makes a string a run id is where it came from.
RunId = NewType("RunId", str)

#: Whatever a `_waited_for` poll is looking for, so the wait hands back what it found
#: rather than something a caller has to re-narrow.
Found = TypeVar("Found")


class Replying(NamedTuple):
    """A live run to reply on, and the environment every verb needs to see it."""

    environment: dict[str, str]
    run: RunId
    #: Where `onepipeline` keeps this run's durable channel and its journal.
    root: Path


def _environment(tmp_path: Path) -> dict[str, str]:
    """The environment one launched run and every verb against it share."""
    environment = dict(os.environ)
    for name in INHERITED_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    # Host state under the real `HOME` reaches a launch these journeys make, so a
    # sandbox is what makes them answer about this checkout.
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    environment["HOME"] = str(home)
    environment["UV_CACHE_DIR"] = os.environ.get("UV_CACHE_DIR") or str(
        Path.home() / ".cache" / "uv"
    )
    return environment


def _just(
    *arguments: str, environment: dict[str, str], seconds: float = 180
) -> subprocess.CompletedProcess[str]:
    """Run one real recipe from this checkout."""
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _named(request: pytest.FixtureRequest, prefix: str) -> RunId:
    """A run id of this journey's own, sanitized to what `onepipeline` mints unchanged."""
    named = re.sub(r"[^A-Za-z0-9]+", "-", request.node.name)[-32:].strip("-")
    return RunId(f"{prefix}-{SUITE}-{named}")


def _plan(run: RunId, tasks: list[dict[str, object]], tmp_path: Path) -> str:
    """Write one plan and hand back the qualified project id a launch takes."""
    plan = tmp_path / f"{run}.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "goal": {"text": "hold a channel open for a manager's reply"},
                "name": run,
                "tasks": tasks,
            }
        ),
        encoding="utf-8",
    )
    return project_from_plan(plan)


def _task(what: str) -> str:
    """One node's prose, in the template every plan this repository ships uses."""
    return f"## What\n{what}\n\n## Why\nHold the run.\n\n## Acceptance criteria\n- Done."


@pytest.fixture
def replying(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[Replying]:
    """A launched run whose frontier is a human gate, so its channel outlives the launch.

    `--dag-graph off` deliberately: with this host's observer graph attached, the monitor
    and the pacemaker raise surfaces of their own on the same channel, and what is
    pending is the whole subject here. The one monitor surface these journeys need is
    raised by driving the real filter directly, which is what makes it this journey's
    rather than a race.

    Nothing is ever dispatched — `work` depends on the gate and nobody attests it — so
    this run costs a launch and no provider turn at all.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    run = _named(request, "channel-reply")
    environment = _environment(tmp_path)
    project = _plan(
        run,
        [
            {"id": GATE_NODE, "kind": "human", "task": _task("Approve.")},
            {"id": WORK_NODE, "persona": "engineer", "deps": [GATE_NODE], "task": _task("Report.")},
        ],
        tmp_path,
    )
    launch = _just(
        "orchestrate", project, "--dag-graph", "off", environment=environment, seconds=600
    )
    assert launch.returncode == 0, f"the launch failed:\n{launch.stdout}\n{launch.stderr}"
    try:
        yield Replying(environment, run, tmp_path / "runs" / run)
    finally:
        _just("stop", run, environment=environment, seconds=60)


class Queue(NamedTuple):
    """What the channel is holding, read without touching it.

    Read off `runs/<run>/channel/queue.json` rather than through `just channel-next`,
    deliberately: reading a surface is what a manager does *to* the queue, and every
    journey here asks what is still there without consuming it.
    """

    #: The surface a reply binds to, or `None` when nothing has been handed out.
    pending: dict[str, Any] | None
    #: Every surface nobody has read yet.
    waiting: list[dict[str, Any]]


def _queue(replying: Replying) -> Queue:
    """This run's channel queue, as `onepipeline` writes it."""
    # llmlint: ignore[tests_mirror_real_usage] Reading a surface through the verb consumes it.
    written = replying.root / "channel" / "queue.json"
    if not written.is_file():
        return Queue(pending=None, waiting=[])
    # `cast` rather than a validating read: `onepipeline` owns this file's schema, and
    # what these journeys read off it is one object and one list.
    held = cast(dict[str, Any], json.loads(written.read_text(encoding="utf-8")))
    waiting = held.get("waiting")
    return Queue(
        pending=held.get("pending"),
        waiting=waiting if isinstance(waiting, list) else [],
    )


def _committed(replying: Replying) -> list[dict[str, Any]]:
    """Every command this run's journal records as having been committed to the graph.

    The journal rather than a view, because what a refusal has to prove is that an edit
    did **not** reach the graph, and no operator view renders an absence.
    """
    # llmlint: ignore[tests_mirror_real_usage] No operator view renders an edit's absence.
    journal = replying.root / "events.jsonl"
    if not journal.is_file():
        return []
    recorded = []
    for line in journal.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        # `onepipeline` owns this envelope; only `kind` and `payload.command` are read.
        event = cast(dict[str, Any], json.loads(line))
        if event.get("kind") != "edit-committed":
            continue
        payload = event.get("payload")
        if isinstance(payload, dict) and isinstance(payload.get("command"), dict):
            recorded.append(cast(dict[str, Any], payload["command"]))
    return recorded


def _reply(
    replying: Replying, envelope: dict[str, Any], *, seconds: float = 120
) -> subprocess.CompletedProcess[str]:
    """Send one envelope over the live channel, as a manager types it."""
    return subprocess.run(
        ["just", "channel-reply", replying.run],
        cwd=REPO_ROOT,
        env=replying.environment,
        input=json.dumps(envelope),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _asked(replying: Replying, question: str) -> subprocess.Popen[str]:
    """Start the real wrapper the way a dispatched agent runs it.

    Always in a session of its own, so the wrapper and the `channel serve` it starts are
    one process group `_reaped` can end in one signal.
    """
    environment = dict(replying.environment)
    environment["ONEPIPELINE_RUN_ID"] = replying.run
    environment["ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS"] = str(ASK_WINDOW_SECONDS)
    return subprocess.Popen(  # noqa: S603 - the real wrapper, as an agent runs it
        [str(ASK_MANAGER), question],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def _answered(asking: subprocess.Popen[str]) -> tuple[int, str, str]:
    """Wait for the asking wrapper to finish, and hand back what it printed.

    Waited for rather than reaped, because what the agent *read* is the assertion:
    signalling it the moment the reply verb returns kills it in the window between the
    channel taking the answer and the wrapper printing it, which reads from here as a
    manager whose accepted ruling reached nobody — the very failure these journeys are
    about, manufactured by the journey itself.
    """
    out, err = asking.communicate(timeout=e2e_timeout(120))
    return asking.returncode, out, err


def _reaped(started: subprocess.Popen[str]) -> None:
    """End a process group this journey started, whether or not it has already ended.

    The whole group rather than the leader: signalling the wrapper alone orphans the
    `channel serve` it started to init and pins the directory it was started in. Every
    ending is tolerated — a group already gone, and a process whose pipes some earlier
    `communicate` has already drained — because this only ever runs in a `finally`.
    """
    with contextlib.suppress(ProcessLookupError):
        os.killpg(os.getpgid(started.pid), signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired, ValueError):
        started.communicate(timeout=e2e_timeout(60))


def _waited_for(
    what: str, look: Callable[[], Found | None], *, seconds: float = PATIENCE_SECONDS
) -> Found:
    """Poll `look` until it answers something, or fail naming what never arrived."""
    limit = deadline(seconds)
    while True:
        found = look()
        if found is not None:
            return found
        assert time.monotonic() < limit, f"{what} never arrived within the wait"
        time.sleep(0.2)


def _minted(replying: Replying) -> str:
    """The correlation token of the blocking question now waiting on this run's queue."""

    def looked() -> str | None:
        queue = _queue(replying)
        for surface in [*queue.waiting, *([queue.pending] if queue.pending else [])]:
            if not isinstance(surface, dict) or surface.get("blocking") is not True:
                continue
            found = TOKEN.search(surface.get("message") or "")
            if found is not None:
                return found.group(0)
        return None

    return _waited_for("a blocking question carrying a token", looked)


def _handed_out(replying: Replying, wanted: str) -> dict[str, Any]:
    """Read surfaces the way a manager does until the one carrying `wanted` is handed out.

    Read off what `channel-next` *answered* rather than off the queue afterwards, because
    the two disagree by design and only the answer is true of both kinds: handing out a
    **blocking** surface makes it the queue's `pending`, and handing out a non-blocking
    one consumes it and leaves `pending` null.

    Reading down rather than once, because a run raises surfaces of its own — a
    settlement projection it could not take, an edit the reconciler rejected — and those
    are handed out ahead of a surface queued after them.
    """

    def looked() -> dict[str, Any] | None:
        handed = _just("channel-next", replying.run, environment=replying.environment, seconds=60)
        assert handed.returncode == 0, f"reading this run's surfaces failed:\n{handed.stderr}"
        if not handed.stdout.strip():
            return None
        # `cast` rather than a validating read: `onepipeline next` owns this schema and
        # what a manager consumes off it is one surface.
        read = cast(dict[str, Any], json.loads(handed.stdout)).get("surface")
        if isinstance(read, dict) and wanted in (read.get("message") or ""):
            return read
        return None

    return _waited_for(f"a surface carrying {wanted!r}", looked)


#: What a manager writes when there is a real decision to send. Every journey below
#: sends this shape, so what separates an accepted send from a refused one is the state
#: of the queue rather than the envelope's own quality.
def _ruling(message: str) -> dict[str, Any]:
    """One reply envelope carrying a decision, as a manager's answer is spelled."""
    return {"version": 1, "completion": True, "message": message}


def _note(node: str, text: str) -> dict[str, Any]:
    """One `note` command, which is the one lever a manager's note reaches a node by.

    `addressee` is spelled here because the op requires it and never infers it, and the
    two optional axes are left at their defaults — `deliver: live` with `persist: true`
    — which is the shape a manager who says nothing else gets.
    """
    return {"op": "note", "id": node, "addressee": "worker", "text": text}


ANSWER = "Key it on the whole workspace; the narrower key would replay a stale verdict."


def test_a_ruling_for_a_question_nobody_has_read_is_refused_where_it_is_sent(
    replying: Replying,
) -> None:
    """The measured incident, on a real channel: the right token, the wrong moment.

    A ruling sent before its question is handed out reaches nobody however well it is
    written. So it is refused where it is sent, naming the question and the read that
    opens its rendezvous, with the queue left as it was — and the same envelope is then
    the answer that agent reads, which is what makes this a guard rather than a wall.

    Nothing else is pending here, which is the shape the incident took. The journey below
    that answers the wrong one of two pending questions is the other shape.
    """
    assert _queue(replying).pending is None, (
        "this run had a surface pending before the journey did anything, so the refusal "
        "below would be about some other state of the queue"
    )
    asking = _asked(replying, "Should the test key cover docs?")
    try:
        token = _minted(replying)
        assert _queue(replying).pending is None, (
            "the question was handed out before the ruling was sent, so this journey is "
            "the ordinary answered case rather than the one it is about"
        )

        refused = _reply(replying, _ruling(f"{ANSWER} {token}"))

        assert refused.returncode == REPLY_REFUSED, (
            f"a ruling for a question nobody had read exited {refused.returncode} where a "
            f"refusal is {REPLY_REFUSED} — accepted, so it was reported delivered and the "
            f"agent went on waiting:\n{refused.stdout}{refused.stderr}"
        )
        assert "nobody has read yet" in refused.stderr, (
            f"the refusal does not say that the question has not been handed out, which "
            f"is the whole of what went wrong:\n{refused.stderr}"
        )
        assert token in refused.stderr, (
            f"the refusal names no question, so a manager holding several cannot tell "
            f"which one to read:\n{refused.stderr}"
        )
        assert "just channel-next" in refused.stderr, (
            f"the refusal names no way out, so the manager is told their reply was wrong "
            f"without being told how to make it right:\n{refused.stderr}"
        )
        after = _queue(replying)
        assert any(token in (surface.get("message") or "") for surface in after.waiting), (
            f"refusing the ruling consumed the question it was for, so nothing is left "
            f"for the same envelope to answer: {after}"
        )

        _handed_out(replying, token)
        answered = _reply(replying, _ruling(f"{ANSWER} {token}"))

        assert answered.returncode == 0, (
            f"the same ruling was refused once its question was pending, so this is a "
            f"wall rather than a guard:\n{answered.stdout}{answered.stderr}"
        )
        status, out, err = _answered(asking)
    finally:
        _reaped(asking)
    assert TOKEN.sub("", out).strip() == ANSWER, (
        f"the agent that asked did not read the accepted ruling back (exit {status}):\n{err}{out}"
    )


def test_a_commands_only_envelope_still_reaches_the_graph_with_nothing_pending(
    replying: Replying,
) -> None:
    """The exemption this guard must never lose: a live edit, sent with nothing pending.

    A `commands` envelope carries no `completion` and is not trying to answer anything —
    the adopted release routes it to the command path, where it lands on the graph and
    answers zero surfaces. Refusing it would take a manager's steering away for exactly
    as long as a run had nothing queued, which is most of a run.
    """
    assert _queue(replying).pending is None, "the premise is a run with nothing pending"
    edit = {"version": 2, "author": "planner", "commands": [_note(WORK_NODE, "the base moved")]}

    edited = _reply(replying, edit)

    assert edited.returncode == 0, (
        f"a live graph edit was refused on a run with nothing pending, which is when a "
        f"manager most often steers:\n{edited.stdout}{edited.stderr}"
    )
    assert json.loads(edited.stdout)["state"] == "applied", (
        f"the edit reached the verb but did not land on the graph:\n{edited.stdout}"
    )
    assert _note(WORK_NODE, "the base moved") in _committed(replying), (
        "the verb reported the edit applied and the run's own journal does not record it"
    )


#: The op the engine removed when it collapsed the two manager-note ops into one, in
#: the shape a manager who had not moved with the adoption would still be sending it.
#: Spelled here rather than derived, because the whole point is that it is *not* a shape
#: anything in this repository composes any more: nothing could derive it, and a
#: constant nobody reads would be no evidence that the wire refuses it.
REMOVED_NOTE_OP = {"op": "context", "id": WORK_NODE, "note": "the base moved"}


def test_the_removed_manager_note_op_is_refused_by_name_at_the_wire(
    replying: Replying,
) -> None:
    """The intended failure of the adoption, driven on a real run rather than assumed.

    `context` was removed outright rather than aliased onto the op that replaced it, and
    the reply envelope refuses unknown fields — so a caller still sending it is refused
    by that name instead of having its note silently dropped. That refusal is what makes
    the wrong lever unavailable rather than merely discouraged, and it is the half of
    this adoption a reader of the prose cannot otherwise check: an alias would look
    identical from every document in this repository.

    Asserted beside the journey above, which sends the survivor through the same recipe
    against the same run and has it applied. One without the other proves nothing: an
    engine that refused both would pass this alone, and one that accepted both would
    pass that alone.
    """
    refused = _reply(replying, {"version": 2, "commands": [REMOVED_NOTE_OP]})

    assert refused.returncode != 0, (
        f"an envelope carrying the removed `context` op was accepted, so this host is "
        f"running an engine that still has it — or has aliased it onto `note`, which is "
        f"what the collapse deliberately did not do:\n{refused.stdout}{refused.stderr}"
    )
    reported = refused.stderr + refused.stdout
    assert "context" in reported, (
        f"the refusal does not name the op it refused, so a manager sending the lever "
        f"this repository documented until the adoption is told their envelope was bad "
        f"and not which part of it was:\n{reported}"
    )
    assert not any(command.get("op") == "context" for command in _committed(replying)), (
        "the refused envelope reached the graph anyway"
    )


def test_an_envelope_carrying_both_halves_is_refused_whole_rather_than_half_applied(
    replying: Replying,
) -> None:
    """The exemption is the commands half's, not the envelope's.

    An envelope carrying `commands` **and** a `completion` reached the exemption above by
    carrying `commands` at all, so its completion half was waved past every check the
    same verdict alone would have failed.

    Refused whole rather than half-applied: the edit must not have reached the graph, and
    the refusal must say that re-sending the commands on their own still applies it —
    which is then done, and does.
    """
    asking = _asked(replying, "Should the cursor be opaque?")
    try:
        token = _minted(replying)
        riding = _note(WORK_NODE, "a note riding beside a verdict")
        both = {
            "version": 2,
            "completion": True,
            "message": f"{ANSWER} {token}",
            "commands": [riding],
        }

        refused = _reply(replying, both)

        assert refused.returncode == REPLY_REFUSED, (
            f"an envelope carrying a verdict and commands exited {refused.returncode} "
            f"where a refusal is {REPLY_REFUSED}, so its verdict half was reported "
            f"delivered having reached nobody:\n{refused.stdout}{refused.stderr}"
        )
        assert "nobody has read yet" in refused.stderr, refused.stderr
        assert "re-sending those commands on their own still applies them" in refused.stderr, (
            f"the refusal does not tell the manager what happened to the commands half, "
            f"which is the half they can still land:\n{refused.stderr}"
        )
        assert riding not in _committed(replying), (
            "the envelope was refused and its edit reached the graph anyway, so the "
            "refusal half-applied what it declined to send"
        )

        edited = _reply(replying, {"version": 2, "commands": [riding]})

        assert edited.returncode == 0, (
            f"the refusal said re-sending the commands alone would apply them, and it "
            f"did not:\n{edited.stdout}{edited.stderr}"
        )
        assert riding in _committed(replying), "the commands sent on their own did not land"
    finally:
        _reaped(asking)


def test_an_envelope_carrying_both_halves_lands_both_while_its_question_is_pending(
    replying: Replying,
) -> None:
    """The other side of refusing one whole: when it is not refused, neither half is lost.

    An envelope naming the pending question and carrying an edit beside the verdict is
    the ordinary shape of a manager answering and steering in one send. It goes through,
    the agent reads the decision back, and the edit reaches the graph — so the refusal
    that fires when the verdict names a *queued* question is not costing this shape
    anything.
    """
    asking = _asked(replying, "Should the cursor be opaque?")
    try:
        token = _minted(replying)
        _handed_out(replying, token)
        riding = _note(WORK_NODE, "a note riding beside a ruling")

        answered = _reply(
            replying,
            {
                "version": 2,
                "completion": True,
                "message": f"{ANSWER} {token}",
                "commands": [riding],
            },
        )

        assert answered.returncode == 0, (
            f"an envelope naming the pending question was refused:"
            f"\n{answered.stdout}{answered.stderr}"
        )
        status, out, err = _answered(asking)
    finally:
        _reaped(asking)
    assert TOKEN.sub("", out).strip() == ANSWER, (
        f"the agent that asked did not read the accepted ruling back (exit {status}):\n{err}{out}"
    )
    assert riding in _committed(replying), (
        "the accepted envelope's commands half did not reach the graph, so an envelope "
        "carrying both is being half-applied in the other direction"
    )


#: The monitor member's own completion bar, as onejudge states it when it asks the planner
#: to score that member. `scripts/channel-serve.py` quotes it whole into the surface it
#: raises — a planner cannot rule on a bar they were shown half of — so a journey can find
#: that surface by it.
MONITOR_CRITERION = "The monitor reported every drift it observed from the plan."


#: One scoring frame, as onejudge puts one to `scripts/channel-serve.py` once the monitor's
#: conversation has ended. The scoring boundary rather than a turn boundary, because the
#: filter raises no surface at all for a turn the monitor took: prose reaches nobody, and a
#: report reaches the planner only as the `finding` op the monitor issues itself. What is
#: left is this one, and it is the rendezvous where a planner's ruling *is* the answer.
def _scoring_frame() -> str:
    """What onejudge asks the planner once the monitor's conversation has ended.

    It carries no `task`, which is why the filter is given `ONEPIPELINE_RUN_ID`: the
    composed task is the `supervisor` op's source for the run and this op has no other.
    """
    return json.dumps(
        {
            "op": "judge",
            "kind": "boolean",
            "criterion": MONITOR_CRITERION,
            "messages": [
                {"role": "user", "content": "Watch this run and report what you find."},
                {"role": "assistant", "content": "I read the detailed stream."},
            ],
        }
    )


#: The manager's score for that watch, read back out of what the filter relays to onejudge.
#: Its own words rather than a status, because a filter that timed out also exits 0 — with
#: a non-completion of its own — and only the text tells the two apart.
SCORE = "Watch it and report again in ten minutes."


def _relayed(serving: subprocess.Popen[str]) -> str:
    """Whatever the monitor's filter handed onejudge, once it has finished handing it."""
    out, err = serving.communicate(timeout=e2e_timeout(120))
    assert serving.returncode == 0, f"the filter failed instead of relaying a ruling:\n{err}"
    return out


def _tokens(replying: Replying, how_many: int) -> list[str]:
    """Wait until this run's queue holds `how_many` distinct blocking questions."""

    def looked() -> list[str] | None:
        found: list[str] = []
        queue = _queue(replying)
        for surface in [*queue.waiting, *([queue.pending] if queue.pending else [])]:
            if not isinstance(surface, dict) or surface.get("blocking") is not True:
                continue
            carried = TOKEN.search(surface.get("message") or "")
            if carried is not None and carried.group(0) not in found:
                found.append(carried.group(0))
        return found if len(found) >= how_many else None

    return _waited_for(f"{how_many} blocking questions carrying tokens", looked)


def _one_of_them_handed_out(replying: Replying, among: Sequence[str]) -> str:
    """Read surfaces the way a manager does until one of `among` is handed out.

    *Which* one is not asserted, and that is the point. `channel-next` hands a blocking
    surface out ahead of every other kind, but nothing published says which of two
    blocking surfaces it takes first — so a journey that named one in advance would be
    asserting an order the engine never promised, on top of the refusal it is actually
    about. What it needs is one of the two pending and the other still waiting, and
    either way round is that state.

    Only the token it carried is handed back: what a caller does with it is compare it
    against the pair, and being blocking is asserted here because it is a precondition
    of this read rather than something the caller decides.
    """

    def looked() -> str | None:
        handed = _just("channel-next", replying.run, environment=replying.environment, seconds=60)
        assert handed.returncode == 0, f"reading this run's surfaces failed:\n{handed.stderr}"
        if not handed.stdout.strip():
            return None
        # `cast` rather than a validating read: `onepipeline next` owns this schema and
        # what a manager consumes off it is one surface.
        read = cast(dict[str, Any], json.loads(handed.stdout)).get("surface")
        if not isinstance(read, dict):
            return None
        carried = read.get("message") or ""
        for token in among:
            if token in carried:
                assert read.get("blocking") is True, (
                    f"the surface carrying {token} was handed out as a non-blocking one, "
                    f"so nothing is pending and the refusal below would be about some "
                    f"other state of the queue: {read}"
                )
                return token
        return None

    return _waited_for(f"a surface carrying one of {list(among)!r}", looked)


def test_a_verdict_carrying_no_token_is_not_refused_while_a_question_is_pending(
    replying: Replying,
) -> None:
    """The other half of the token bound, and the half a guard most easily loses.

    A monitor score carries no correlation token — nothing but `scripts/ask-manager.sh`
    mints one — so from the queue it is indistinguishable from a manager answering the
    pending question without echoing its token. The two call for opposite treatment and
    nothing here can tell them apart, so this refuses neither: a guard that refused the
    ambiguous case would refuse every monitor score raised while an agent's question sat
    pending, which is the state a supervised run spends most of its time in.

    What that gives up is stated rather than hidden. A manager who really did mean to
    answer the pending question, and left the token out, is no longer told so here; the
    reader that discards their answer is `scripts/ask-manager.sh`, and
    `tests/ask_seam/test_ask_manager_e2e.py` is where that discard is held. The refusal
    this recipe keeps is the one an envelope's own bytes decide — it echoes a queued
    question's token — and never one inferred from what the queue happens to hold.

    Where the accepted verdict then goes is the engine's routing rather than this
    guard's: with a blocking question pending the engine binds a verdict to it. So what
    is asserted is that the recipe did not refuse, and that it said nothing of its own.
    """
    asking = _asked(replying, "Should the test key cover docs?")
    try:
        token = _minted(replying)
        pending = _handed_out(replying, token)
        assert pending.get("blocking") is True, (
            f"the question was not handed out, so this journey is about the queued case "
            f"the refusal above owns rather than the pending one it is about: {pending}"
        )

        scored = _reply(replying, _ruling(SCORE))

        assert scored.returncode == 0, (
            f"a verdict carrying no correlation token was refused while a question was "
            f"pending. A monitor score is exactly that envelope, so this refuses every "
            f"score raised on a run with an agent waiting:\n{scored.stdout}{scored.stderr}"
        )
        assert "channel-reply:" not in scored.stderr, (
            f"the recipe spoke about an envelope it did not refuse, so it is judging one "
            f"it has no way to judge:\n{scored.stderr}"
        )
    finally:
        _reaped(asking)


def test_a_ruling_for_an_unread_question_is_refused_while_another_question_is_pending(
    replying: Replying,
) -> None:
    """The same refusal in its other shape: the manager answered the wrong one of two.

    Two agents ask, the manager reads one and answers the *other*. That ruling binds to
    the question that is pending rather than the one it names, and both agents are worse
    off.

    So it is refused, naming the unread question and what is pending instead — and
    deliberately *not* as a missing token, which is the other refusal this envelope would
    fire: adding the pending question's token would answer that agent with a decision
    meant for somebody else.

    What the agent then reads back is proven by the journeys above rather than here. Two
    live askers are two readers of one queue and a reply is claimed by whichever reaches
    it next, so asserting which of them takes an accepted ruling would be asserting that
    race.

    **The two questions are raised one after the other, and that ordering is what this
    journey used to fail for want of.** Two `onepipeline channel serve` sessions that
    register on one run at the same instant lose one of the two surfaces: both reach
    `channel/surfaces.jsonl` carrying `"id": 0` and the same `queued_at` millisecond
    while `channel/queue.json` keeps only one, so the second agent's question exists
    nowhere a manager can read it. That is an engine defect rather than this journey's,
    and it is not what this journey is about: the refusal needs two blocking questions
    on the queue, not two askers racing to put them there. So the first is waited onto
    the queue before the second is asked — a synchronisation point rather than a retry,
    since every register then happens with no other register in flight.

    Nor is the order they are then handed out in assumed. `channel-next` takes a
    blocking surface ahead of every other kind and says no more than that, so the
    pending one is whichever it answered with and the unread one is the other.
    """
    first = _asked(replying, "Should the test key cover docs?")
    second = None
    try:
        # One at a time, for the reason above: the first question is on the queue before
        # the second session exists, so the two never register at once.
        (already,) = _tokens(replying, 1)
        second = _asked(replying, "Which cursor shape should the route take?")
        both = _tokens(replying, 2)
        assert already in both, (
            f"the question queued first is no longer on the queue beside the second, so "
            f"this journey is not in the two-question state it is about: {both}"
        )
        pending = _one_of_them_handed_out(replying, both)
        (unread,) = [token for token in both if token != pending]

        refused = _reply(replying, _ruling(f"{ANSWER} {unread}"))

        assert refused.returncode == REPLY_REFUSED, (
            f"a ruling naming the question that was NOT handed out exited "
            f"{refused.returncode} where a refusal is {REPLY_REFUSED} — accepted, and "
            f"bound to the other agent's question:\n{refused.stdout}{refused.stderr}"
        )
        assert unread in refused.stderr, (
            f"the refusal names no question, so a manager holding two cannot tell which "
            f"one to read:\n{refused.stderr}"
        )
        assert "is what is pending instead" in refused.stderr, (
            f"the refusal does not say what the verdict would have bound to, which is "
            f"the half that differs from the same refusal on an empty queue:"
            f"\n{refused.stderr}"
        )
        still = _queue(replying)
        assert isinstance(still.pending, dict) and pending in (
            still.pending.get("message") or ""
        ), f"refusing the ruling moved what was pending: {still.pending}"
        assert any(unread in (surface.get("message") or "") for surface in still.waiting), (
            f"refusing the ruling consumed the question it named: {still}"
        )

        _handed_out(replying, unread)
        answered = _reply(replying, _ruling(f"{ANSWER} {unread}"))

        assert answered.returncode == 0, (
            f"the same ruling was refused once its own question was pending, so this is "
            f"a wall rather than a guard:\n{answered.stdout}{answered.stderr}"
        )
    finally:
        if second is not None:
            _reaped(second)
        _reaped(first)


#: The field the verb's own answer carries the note outcomes in, and the entry each note
#: gets. `reached` is the engine's own word, or `None` where nothing has decided it yet.
NOTES_FIELD = "notes"

#: The field it carries which halves the staged envelope held: `verdict` for the answering
#: half and `edits` for how many commands rode beside it. Read off the staged bytes, so it
#: says what was sent rather than what became of it.
HALVES_FIELD = "halves"


class Halves(TypedDict):
    """What that field says one envelope carried: an answering half, and how many edits."""

    #: Whether a verdict half was there at all — its presence, never its value, because a
    #: `completion: false` is a non-completion and this channel routes it as a verdict.
    verdict: bool
    #: How many commands rode beside it.
    edits: int


#: Everything `scripts/channel-reply.sh` merges into the verb's own answer. Subtracted
#: before the engine's own receipt is read, because what that receipt does *not* say is
#: the whole reason the fields above exist.
RECIPE_FIELDS = frozenset({HALVES_FIELD, NOTES_FIELD, "notes_unread"})


def _verb_answer(sent: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    """The verb's answer, which on success is the whole of what this recipe prints.

    Parsed rather than matched as a fragment: the outcome is merged into the receipt
    rather than printed beside it, so what a journey asks is what that one line carries.
    """
    printed = sent.stdout.strip().splitlines()
    assert len(printed) == 1, (
        f"a successful reply printed {len(printed)} line(s) where the verb's own answer "
        f"is the whole of the success output:\n{sent.stdout}"
    )
    # `cast` rather than a validating read: the shape is asserted by the journeys below.
    return cast(dict[str, Any], json.loads(printed[0]))


def _note_outcomes(sent: subprocess.CompletedProcess[str]) -> list[dict[str, Any]]:
    """What that answer says became of each `note` the envelope carried."""
    carried = _verb_answer(sent).get(NOTES_FIELD)
    assert isinstance(carried, list), (
        f"the answer carries no {NOTES_FIELD!r}, so the manager has only the transport "
        f"receipt to go on:\n{sent.stdout}"
    )
    return cast(list[dict[str, Any]], carried)


def test_the_recipe_reports_what_the_engine_recorded_it_did_with_the_note(
    replying: Replying,
) -> None:
    """`delivered` says the engine took the envelope; it says nothing about the note.

    A `note` is taken by a turn of the node's conversation or carried to that node's
    next dispatch, and the engine records which party took it the moment it commits the
    edit — on the run's journal, where a manager reading their own terminal never sees
    it.

    So the recipe reads that outcome back and carries it in the verb's own answer. This
    run's `work` node has never been dispatched, so no turn of it can take the note and
    the default `persist: true` carries it forward, which the engine records as
    `carried`; what is asserted is that the manager is told the node and the engine's own
    word for it, from the command they typed and in one line.
    """
    sent = _reply(replying, {"version": 2, "commands": [_note(WORK_NODE, "the base moved")]})

    assert sent.returncode == 0, f"the note was refused:\n{sent.stdout}{sent.stderr}"
    answer = _verb_answer(sent)
    assert answer.get("state") == "applied", (
        f"the verb's own answer did not survive the merge, so a caller reading the "
        f"receipt is worse off than before:\n{sent.stdout}"
    )
    assert answer[NOTES_FIELD] == [{"node": WORK_NODE, "reached": "carried"}], (
        f"this note reached a node with no turn to take it, so `carried` against "
        f"its own node is what the engine recorded and what the answer has to "
        f"carry:\n{sent.stdout}"
    )
    assert "channel-reply:" not in sent.stderr, (
        f"the recipe printed a second status line of its own beside the verb's answer, "
        f"which is the noise the one-line answer replaced:\n{sent.stderr}"
    )


def _engine_receipt(sent: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    """The verb's own answer with everything this recipe merged into it taken back out.

    `Any` because this one is deliberately unmodelled: the whole subject of the journey
    below is which fields the engine puts here, so a type that named them would be this
    suite asserting the answer it is supposed to be reading.
    """
    return {
        field: value for field, value in _verb_answer(sent).items() if field not in RECIPE_FIELDS
    }


def _halves(sent: subprocess.CompletedProcess[str]) -> Halves:
    """What that answer says the envelope this recipe staged carried."""
    carried = _verb_answer(sent).get(HALVES_FIELD)
    assert isinstance(carried, dict), (
        f"the answer does not say which halves the envelope carried, so a manager who "
        f"answered and steered in one send has one {'state'!r} word for both:\n{sent.stdout}"
    )
    # `cast` because this is a deserialization boundary — JSON off another process's
    # stdout — and the two fields it must hold are what the journeys below compare it by.
    return cast(Halves, carried)


def test_the_answer_names_which_halves_the_envelope_it_staged_carried(
    replying: Replying,
) -> None:
    """What the envelope *carried*, which is a different question from what each half did.

    The engine's own `state` is still one word for the whole envelope — `applied` for
    edits alone and `applied` again for a ruling sent beside them — and the release below
    left it at that, which is what this field was added for. The adopted engine now says
    what each carried half then *did*, in the `verdict` and `commands` keys the journey
    below reads; this field stays because it answers the other half of the question, off
    the staged bytes rather than off the engine's answer.

    What this recipe can prove on its own is the envelope it staged, so that is what it
    reports and all it reports. `verdict` says an answering half was there and `edits`
    counts the commands beside it; neither claims either half landed, which is the
    engine's to say and the reason nothing here waits on it.

    All three shapes through the real recipe against one live run, because the field is
    only worth anything if it parts them: a receipt that said the same thing for a
    verdict-only and a commands-only envelope would be exactly the word it replaces. The
    third of them carries `completion: false`, which is the case the field reports on
    presence rather than value for: a non-completion is the shape
    `scripts/channel-serve.py` sends most, and a receipt keyed on the value would report
    the commonest verdict there is as no verdict at all.
    """
    edits_only = _reply(replying, {"version": 2, "commands": [_note(WORK_NODE, "the base moved")]})

    assert edits_only.returncode == 0, f"the edit was refused:\n{edits_only.stderr}"
    assert _halves(edits_only) == {"verdict": False, "edits": 1}, (
        f"a commands-only envelope is reported as carrying an answering half, or as "
        f"carrying no edits:\n{edits_only.stdout}"
    )
    # The verb's own answer is still the whole of what a reader knowing only the fields
    # before this one looks for, which is what makes the field additive rather than a
    # replacement.
    assert _engine_receipt(edits_only)["state"] == "applied", (
        f"the verb's own answer did not survive the merge:\n{edits_only.stdout}"
    )

    asking = _asked(replying, "Should the cursor be opaque?")
    try:
        token = _minted(replying)
        _handed_out(replying, token)
        verdict_only = _reply(replying, _ruling(f"{ANSWER} {token}"))
    finally:
        _reaped(asking)

    assert verdict_only.returncode == 0, f"the ruling was refused:\n{verdict_only.stderr}"
    assert _halves(verdict_only) == {"verdict": True, "edits": 0}, (
        f"a verdict-only envelope is reported as carrying edits, or as carrying no "
        f"answering half:\n{verdict_only.stdout}"
    )

    riding = [_note(WORK_NODE, "a note riding beside a ruling"), _note(WORK_NODE, "and another")]
    answering = _asked(replying, "Should the test key cover docs?")
    try:
        second = _minted(replying)
        _handed_out(replying, second)
        both = _reply(
            replying,
            {
                "version": 2,
                "completion": False,
                "message": f"{ANSWER} {second}",
                "commands": riding,
            },
        )
    finally:
        _reaped(answering)

    assert both.returncode == 0, f"the envelope carrying both was refused:\n{both.stderr}"
    assert _halves(both) == {"verdict": True, "edits": 2}, (
        f"an envelope carrying both halves is reported as one of them, which is the "
        f"state the engine's own word already leaves a manager in:\n{both.stdout}"
    )
    assert _engine_receipt(both)["state"] == "applied", both.stdout


def test_the_engines_own_receipt_names_each_half_the_envelope_carried(
    replying: Replying,
) -> None:
    """This repository's account of the installed engine, driven against it.

    AGENTS.md, under "Answering on the channel", says an envelope carrying both halves is
    queued as a verdict beside the edits it applies, and that the adopted engine reports
    what each carried half then did. Both clauses are asserted here rather than believed,
    because the first is the reason a manager sends that shape and the second is what a
    manager reads back off it — and a paragraph that was true when it was written is
    exactly the kind that goes on reading true after the engine has moved.

    The queueing: the agent that asked reads the ruling back, and the run's own journal
    records the edit. The reporting: the receipt carries one key per carried half beside
    the `reply` and `state` it always carried, and **presence** is half of what it says —
    an envelope carrying edits alone has no `verdict` key at all, which is a different
    statement from a verdict that was carried and did nothing.

    Asserted whole rather than as a floor, in both directions. A check that only required
    `verdict` to be there would pass an engine that had started saying something else in
    it, and one that only compared the two receipts would pass an engine that reported
    both halves for an envelope carrying one. What fails here is either shape moving,
    which is the prompt to re-read the paragraph in AGENTS.md that quotes them.
    """
    edits_only = _reply(replying, {"version": 2, "commands": [_note(WORK_NODE, "the base moved")]})
    assert edits_only.returncode == 0, f"the edit was refused:\n{edits_only.stderr}"

    riding = _note(WORK_NODE, "a note riding beside a ruling")
    asking = _asked(replying, "Should the cursor be opaque?")
    try:
        token = _minted(replying)
        _handed_out(replying, token)
        both = _reply(
            replying,
            {
                "version": 2,
                "completion": True,
                "message": f"{ANSWER} {token}",
                "commands": [riding],
            },
        )
        assert both.returncode == 0, f"the envelope carrying both was refused:\n{both.stderr}"
        status, out, err = _answered(asking)
    finally:
        _reaped(asking)

    assert TOKEN.sub("", out).strip() == ANSWER, (
        f"the verdict half of an envelope carrying commands beside it reached nobody, so "
        f"it is no longer queued beside them (exit {status}):\n{err}{out}"
    )
    assert riding in _committed(replying), (
        "the commands half of that same envelope did not reach the graph"
    )

    receipt, alone = _engine_receipt(both), _engine_receipt(edits_only)

    assert receipt == {
        "reply": 0,
        "state": "applied",
        "verdict": "delivered",
        "commands": "applied",
    }, (
        f"the engine's own receipt for an envelope carrying both halves is not the object "
        f"AGENTS.md's 'Answering on the channel' quotes. Read what it answers now and "
        f"correct that paragraph in the same change: {receipt}"
    )
    assert alone == {"reply": 0, "state": "applied", "commands": "applied"}, (
        f"the engine's receipt for an envelope carrying edits alone is not the same object "
        f"without the `verdict` key, so an absent key no longer means the half was never "
        f"carried and that paragraph's reading of presence is stale: {alone}"
    )


class Dispatching(NamedTuple):
    """A run with a worker in flight and the attached launch driving it."""

    environment: dict[str, str]
    run: RunId
    root: Path
    #: The attached launch, held so the driver stays alive. Its process group is what
    #: this journey suspends, and it is a group this journey started itself.
    launch: subprocess.Popen[str]


@pytest.fixture
def dispatching(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[Dispatching]:
    """A live run whose driver this journey may stop, because it started it.

    Attached rather than detached: a detached launch's driver exits once the graph has
    nothing to schedule, and a run with no driver has `channel-reply` reconcile the
    envelope itself — which is the very thing the journey below has to prevent.

    Its own session, so the whole launch is one process group. Nothing here ever signals
    a process it did not start.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    run = _named(request, "channel-reply-live")
    environment = _environment(tmp_path)
    environment[AGENT_DELAY_ENV] = str(WORKER_HELD_SECONDS)
    project = _plan(
        run, [{"id": WORK_NODE, "persona": "engineer", "task": _task("Report.")}], tmp_path
    )
    launch = subprocess.Popen(  # noqa: S603 - the real recipe, as an operator runs it
        ["just", "orchestrate", project, "--dag-graph", "off"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        yield Dispatching(environment, run, tmp_path / "runs" / run, launch)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(launch.pid), signal.SIGCONT)
        launch.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            launch.wait(timeout=e2e_timeout(60))
        _just("stop", run, environment=environment, seconds=60)


def _replying(dispatching: Dispatching) -> Replying:
    """The reply-facing view of a live run, so the same helpers serve both fixtures."""
    return Replying(dispatching.environment, dispatching.run, dispatching.root)


def _dispatched(dispatching: Dispatching) -> None:
    """Wait until this run has a worker in flight, so its driver is really working."""

    def looked() -> str | None:
        stream = _just(
            "monitor", dispatching.run, "--all", environment=dispatching.environment, seconds=60
        )
        if stream.returncode == 0 and "node-dispatched" in stream.stdout:
            return stream.stdout
        return None

    _waited_for("a dispatched worker", looked)


#: `onepipeline reply`'s published state for an envelope it accepted and made durable
#: but did not get a reconciler's verdict on in time. That is the window in which a
#: note's outcome is not recorded yet, and it is the engine's own word for it.
QUEUED = "queued"

#: The exit status that goes with it: accepted, durable, unreconciled — which is **`0`**,
#: the same status an applied envelope gets. It was `1` until the engine ruled that a
#: non-zero status from this verb is a rejection to correct and that a queued envelope is
#: neither rejected nor to be sent again, leaving `1` to mean what it means everywhere
#: else in that binary: a run that has not settled.
#:
#: So the status no longer tells the two apart, and every assertion below reads
#: `state` — the receipt's own word, which is the half that survived the renumbering and
#: the half `scripts/channel-reply.sh` keys its advice on.
REPLY_QUEUED = 0


def test_a_note_whose_outcome_is_not_recorded_yet_is_never_reported_as_an_earlier_notes(
    dispatching: Dispatching,
) -> None:
    """The case that falsifies reading the journal by recency, driven on a real run.

    Two notes on one run. The first is committed and its outcome recorded. The second is
    sent with the run's own driver suspended, so the engine accepts it, makes it durable,
    and answers a receipt whose `state` is `queued` — the published accepted-and-durable
    state, and the whole of the window in which an outcome is not yet there. The journal
    then holds exactly one note disposition, and it is the *first* note's.

    A recipe reading the newest outcome, the last, or the only one reports that one and
    tells the manager their second note reached a dispatch. This one has to report that
    this note's outcome is not recorded yet.

    The driver is suspended rather than killed, and it is this journey's own launch: no
    process it did not start is signalled, and it is continued again in teardown.
    """
    _dispatched(dispatching)
    replying = _replying(dispatching)
    first = _note(WORK_NODE, "the first note, whose outcome is recorded")
    # `deliver: next` rather than the default: the default attempts the node's running
    # turn through the two-party delivery seam, and this suite's stand-in provider runs no
    # conversation that seam can reach — so the default would be an edit the engine
    # refuses rather than a note whose disposition it records. `persist` is left at its
    # default, so the note is carried to the node's next dispatch.
    first["deliver"] = "next"

    recorded = _reply(replying, {"version": 2, "commands": [first]})

    assert recorded.returncode == 0, f"the first note was refused:\n{recorded.stderr}"
    assert _note_outcomes(recorded) == [{"node": WORK_NODE, "reached": "carried"}], (
        f"the first note's outcome was not reported, so there is nothing on this "
        f"journal for the second note to be confused with:\n{recorded.stdout}"
    )

    # The recipe under test is still driven only through its published command line. What
    # is suspended is a collaborator, to hold the engine in a state it publishes and that no
    # verb can ask for: `queued` is reached by the reconciler not answering in time, so a
    # journey that waited for that to happen on its own would assert this window only when
    # the host was loaded. The signal goes to this journey's own launch, and is undone in
    # `finally`. The directive stays on the line below so it is in scope for the call.
    # llmlint: ignore[tests_mirror_real_usage] A collaborator is suspended, not the recipe.
    os.killpg(os.getpgid(dispatching.launch.pid), signal.SIGSTOP)
    try:
        second = _note(WORK_NODE, "the second note, whose outcome nothing has decided")
        second["deliver"] = "next"

        queued = _reply(replying, {"version": 2, "commands": [second]}, seconds=180)
    finally:
        os.killpg(os.getpgid(dispatching.launch.pid), signal.SIGCONT)

    assert queued.returncode == REPLY_QUEUED and QUEUED in queued.stdout, (
        f"the second note was reconciled after all, so this journey never reached the "
        f"window it is about:\n{queued.stdout}{queued.stderr}"
    )
    # Accepted and durable is still accepted, so what the envelope carried is as true
    # here as on the reconciled path — and this is the one state where the note's own
    # outcome cannot be given, which is exactly when a manager falls back on it.
    assert _halves(queued) == {"verdict": False, "edits": 1}, (
        f"an accepted-but-unreconciled reply does not say which halves it carried, so "
        f"the receipt is thinnest in the window it is least able to answer:\n{queued.stdout}"
    )
    assert _note_outcomes(queued) == [{"node": WORK_NODE, "reached": None}], (
        f"this note's outcome is not on the journal yet, so `None` against its own node "
        f"is what the answer has to carry. A `reached` here at all is the FIRST note's, "
        f"which is what reading the journal by recency, by its last entry, or by its "
        f"only entry does — and what the manager would act on:\n{queued.stdout}"
    )


def test_a_managers_score_for_the_monitors_watch_reaches_it_with_nothing_pending(
    dispatching: Dispatching,
) -> None:
    """The rendezvous a verdict must never be taken from, and why the guard cannot take it.

    `scripts/channel-serve.py` raises the monitor's own completion bar as a **non-blocking**
    surface with the planner as that member's judge side: the ruling that comes back **is**
    the score. A non-blocking surface is consumed by the read that hands it out rather than
    made pending, so that score is sent into a queue holding nothing at all — exactly the
    state a guard keyed on "nothing is pending" would refuse, for every monitor score of
    every watched run.

    Three assertions, because no one of them alone says it: that the surface carries no
    correlation token bounds the refusal beside this away from it, that the queue really
    is empty says the weaker condition would have fired, and that the filter comes back
    holding the manager's own words says the score arrived rather than was merely
    accepted.

    On a live run rather than the gated one the refusal journeys use, because a settled
    run has every verdict refused by the engine before the guard's answer could be read.
    """
    _dispatched(dispatching)
    replying = _replying(dispatching)
    environment = dict(replying.environment)
    # The scoring frame carries no composed task, so this is the filter's only source for
    # the run — `onepipeline` exports it to both sides of a real observer member.
    environment["ONEPIPELINE_RUN_ID"] = replying.run
    serving = subprocess.Popen(  # noqa: S603 - the real filter, as the monitor reaches it
        [str(CHANNEL_SERVE)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert serving.stdin is not None
        serving.stdin.write(_scoring_frame())
        serving.stdin.close()
        raised = _handed_out(replying, MONITOR_CRITERION)

        assert raised.get("blocking") is not True, (
            f"the monitor's own surface is blocking, so this journey no longer says "
            f"anything about the non-blocking rendezvous it is about: {raised}"
        )
        assert TOKEN.search(raised.get("message") or "") is None, (
            f"the monitor's surface carries an ask-manager correlation token, so the "
            f"refusal for a question nobody has read is no longer bounded away from this "
            f"rendezvous: {raised}"
        )
        read = _queue(replying)
        assert read.pending is None, (
            f"reading the monitor's surface left something pending, so a score sent now "
            f"is not the empty-queue case this journey is about: {read.pending}"
        )

        scored = _reply(replying, _ruling(SCORE))

        assert scored.returncode == 0, (
            f"a manager's score for a monitor turn was refused, which takes the "
            f"supervisory tier's whole answer path away:\n{scored.stdout}{scored.stderr}"
        )
        said = _relayed(serving)
        assert SCORE in said, (
            f"the score was accepted by the channel and the monitor's own filter did not "
            f"receive it, so it reached nobody after all:\n{said}"
        )
    finally:
        _reaped(serving)


def _amend(node: str, text: str) -> dict[str, Any]:
    """One `amend` command, which replaces the binding text of a node's effective task.

    `dict[str, Any]` for the reason `_note` beside it is one: a command is a member of
    `onepipeline reply`'s own open envelope schema, and these journeys compare what they
    sent against what the run's journal recorded as committed — which is that same
    untyped object read back.
    """
    return {"op": "amend", "id": node, "text": text}


#: The amendment this refusal was written from, verbatim as it was sent during the run it
#: cost. The checks it names run on the host after publication, so the agent step it
#: binds has ended before any of them start — and it settled correct, committed,
#: gate-green work as a task failure.
UNSATISFIABLE_AMENDMENT = (
    "The finished branch merges cleanly into its base and its change request's "
    "required checks pass."
)

#: The same correction stated as what the finished tree must carry, which is what the
#: refusal asks its author for.
SOUND_AMENDMENT = (
    "The finished tree carries an assertion whose subject is the behaviour this change "
    "adds, so removing that behaviour fails it."
)


# llmlint: ignore-block[tests_mirror_real_usage] Both of this journey's reads are ones
# no user-facing command can make, for the reasons `_queue` and `_committed` state where
# they are defined: reading a surface through `just channel-next` is what *consumes* it,
# so a journey asking what the channel still holds has to read the file, and no operator
# view renders an edit's **absence**, which is the whole of what a refusal has to prove.
# Everything the refusal itself says is asserted off the recipe's own stderr and status.
def test_an_amendment_a_judge_would_hold_its_worker_to_as_work_is_refused(
    replying: Replying,
) -> None:
    """An amendment is criteria, and this is the only place it can be held to that bar.

    It reaches a node over this channel rather than through the plan store, so `just
    check-plan` never sees one — and until this ran it was the only criteria on this host
    that nothing checked. Written in the minute after a manager reads a failure, which is
    more pressure than a plan is ever written under.

    Refused **whole**, with a `note` riding beside it, because a manager who steers and
    corrects in one send must not have the steering half applied against a correction
    that was declined: the run is left exactly as it was, and re-sending the note alone
    still lands it.
    """
    riding = _note(WORK_NODE, "a note riding beside an amendment")
    before = _queue(replying)

    refused = _reply(
        replying,
        {"version": 2, "commands": [_amend(WORK_NODE, UNSATISFIABLE_AMENDMENT), riding]},
    )

    assert refused.returncode == REPLY_REFUSED, (
        f"an envelope carrying an unsatisfiable amendment exited {refused.returncode} "
        f"where a refusal is {REPLY_REFUSED}, so a judge was handed a bar its worker "
        f"cannot clear:\n{refused.stdout}{refused.stderr}"
    )
    assert "required checks pass" in refused.stderr, refused.stderr
    assert "state that arrives after it is gone" in refused.stderr, refused.stderr
    assert "nothing was sent" in refused.stderr, (
        f"the refusal does not say the rest of the envelope went nowhere, which is what "
        f"stops a manager assuming the note landed:\n{refused.stderr}"
    )
    assert _committed(replying) == [], (
        "the envelope was refused and something in it reached the graph anyway"
    )
    assert _queue(replying) == before, "a refused reply changed what the channel is holding"

    landed = _reply(replying, {"version": 2, "commands": [riding]})

    assert landed.returncode == 0, f"{landed.stdout}{landed.stderr}"
    assert riding in _committed(replying), "the note sent on its own did not land"


# llmlint: ignore-end[tests_mirror_real_usage]


# llmlint: ignore-block[tests_mirror_real_usage] `_committed` reads the run's own journal
# because that is where the engine records an accepted edit, and it is how every journey
# in this module proves one reached the graph; see its definition. The recipe's answer and
# status — which is what a manager sees — are asserted from the process result above it.
def test_a_sound_amendment_is_sent_unchanged(replying: Replying) -> None:
    """The half that makes the refusal worth having: a property reaches the graph.

    A `note` is sent beside it and is *not* refused for naming a route, because the
    refusal above offers that escape by name — an observation belongs in a note, which
    touches no acceptance criterion at all, and a check that refused one too would be
    refusing the correction it recommends.
    """
    amendment = _amend(WORK_NODE, SOUND_AMENDMENT)
    observation = _note(WORK_NODE, "the gate on that branch was `just check` and it is green")

    sent = _reply(replying, {"version": 2, "commands": [amendment, observation]})

    assert sent.returncode == 0, f"a sound amendment was refused:\n{sent.stdout}{sent.stderr}"
    committed = _committed(replying)
    assert amendment in committed, (
        f"the amendment did not reach the graph, so the bar the node's next dispatch is "
        f"judged against is unchanged:\n{sent.stdout}"
    )
    assert observation in committed, "the note beside it was judged as though it were criteria"


# llmlint: ignore-end[tests_mirror_real_usage]


def test_a_reply_nothing_reconciled_says_what_it_is_still_waiting_for(
    dispatching: Dispatching,
) -> None:
    """`queued` and `applied` differ by one word, and the difference is the whole state.

    The engine's reply forks on the run's ownership lock: with that lock held the
    commands are accepted and made durable and **not** reconciled, and they sit in the
    durable queue until something drives the run and drains them. Read from the receipt
    alone, the state a live run passes through in a second and the state a run whose
    driver has died stays in forever are the same object with one word changed.

    The driver is suspended rather than killed, and it is this journey's own launch: no
    process it did not start is signalled, and it is continued again in `finally`. That
    is the one way to reach a state the engine publishes and no verb can ask for.
    """
    _dispatched(dispatching)
    replying = _replying(dispatching)

    # llmlint: ignore[tests_mirror_real_usage] A collaborator is suspended, not the recipe.
    os.killpg(os.getpgid(dispatching.launch.pid), signal.SIGSTOP)
    try:
        held = _note(WORK_NODE, "a note the reconciler will not reach in time")
        held["deliver"] = "next"

        queued = _reply(replying, {"version": 2, "commands": [held]}, seconds=180)
    finally:
        os.killpg(os.getpgid(dispatching.launch.pid), signal.SIGCONT)

    assert queued.returncode == REPLY_QUEUED and QUEUED in queued.stdout, (
        f"this reply was reconciled after all, so the journey never reached the state it "
        f"is about:\n{queued.stdout}{queued.stderr}"
    )
    assert "until something is driving that run" in queued.stderr, (
        f"an accepted-but-unreconciled reply says nothing about what it is waiting for, "
        f"so a manager reading 'queued' cannot tell it from 'applied':\n{queued.stderr}"
    )
    assert f"just orchestrate --adopt {replying.run}" in queued.stderr, (
        f"the one thing a manager does about a run nothing is driving is not named, so "
        f"the sentence reports a state without an action:\n{queued.stderr}"
    )
    assert queued.stdout.strip().startswith("{"), (
        f"the verb's own answer is no longer the whole of this recipe's stdout:\n{queued.stdout}"
    )
