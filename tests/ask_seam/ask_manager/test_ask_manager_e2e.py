"""A dispatched agent asks its manager over a real run's planner channel, and gets their answer.

`scripts/ask-manager.sh` is the command `ORCHESTRATOR_ASK_MANAGER` names, and it is the
engine's `onepipeline ask` and nothing else: its three invocation forms raise one
`planner-question` on the run's `surfaces` queue, under the bus policy the run's launch
record carries. Everything after the question is on the queue — the correlation it is bound
by, the wait, and the one JSON line that answers — is the engine's and the linked bus's, and
every rule it keeps is proven by their own suites. None of it is re-proved here.

What these journeys prove is the **configuration**: that this host's wiring reaches those
behaviours on a run `just orchestrate` really launched. The question an agent asks is the
surface a manager's own `just channel-next` hands out, carrying the bus's correlation; the
ruling `just channel-reply --correlation` sends is the answer the adapter prints, and
nothing else is; an unanswered question ends in the bus's named `timeout`; and a process
that was never given a run is refused before anything is asked. That the adapter passes
its argv, stdin, answer and status through untouched is held by
`tests/e2e/test_ask_manager_shim_e2e.py`.

Only the paid model is doubled, at the `oneharness` seam, exactly as
`tests/e2e/test_orchestrate_launch_e2e.py` doubles it — and the runs the question journeys
launch reach it only in principle: their frontier is a human gate, so nothing is dispatched.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple, NewType, NotRequired, Protocol, TypedDict, cast

import pytest
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from planner_channel import (
    MANAGER_PATIENCE_SECONDS,
    Manager,
    Surface,
    just,
    next_surface_record,
    ruling,
)
from project_fixtures import helper, project_from_plan
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The shim under test, run as an agent runs it.
ASK_MANAGER = REPO_ROOT / "scripts" / "ask-manager.sh"

#: The stand-in for the paid model, and the provider binary beneath it. The question
#: journeys never reach either — a human gate dispatches nothing — and both are named for
#: the same reason a seatbelt is worn on a short drive: a plan that came to dispatch would
#: otherwise spend real provider quota from a suite.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")

#: A launching session these journeys state rather than inherit: this suite runs inside a
#: dispatch whose own harness session would otherwise own the runs.
LAUNCHING_SESSION = "e2e-ask-manager"

#: Every name a launcher identity reaches `scripts/onepipeline.sh` through, plus what the
#: enclosing dispatch was given to ask with. The shim reads the run, the asker, the node
#: and the reply window out of its environment, so a journey that kept any of these would
#: be asking the *outer* run's channel, as the outer dispatch, about the outer node.
INHERITED_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "ONEPIPELINE_CHANNEL_ASKER",
    "ONEPIPELINE_NODE_SCRATCH_DIR",
    "ORCHESTRATOR_ASK_MANAGER",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: The reply window the timeout journey gives the shim. Short, because waiting it out is
#: the whole of that journey and nothing else about it is slow.
SHORT_WINDOW_SECONDS = 5

#: The reply window the shim is given while a manager looks for the question. Deliberately
#: longer than the manager's own patience, and that ordering is the point: the other way
#: round the shim gave up first, so a journey whose manager was merely slow reported a
#: timeout — a real answer, just not the one it was about — and a journey whose manager
#: genuinely failed reported nothing about why.
ANSWERED_WINDOW_SECONDS = int(e2e_timeout(MANAGER_PATIENCE_SECONDS * 2))

#: A run's own name on the ledger. Every planner-facing verb takes one and the shim reads
#: one out of the environment, so it is distinguished from the prose it is built out of:
#: what makes a string a run id is where it came from.
RunId = NewType("RunId", str)

#: The release the decoy `onetaskgraph` reports. Any release this checkout does not adopt;
#: the journey below holds it to differing from the pin.
DECOY_PIN = "0.0.1"

#: Read rather than restated, so the decoy is held to the real pin.
ADOPTED_ONETASKGRAPH = (
    (REPO_ROOT / "config" / "onetaskgraph.version").read_text(encoding="utf-8").strip()
)

#: The agent node every question plan carries behind its gate. It is never dispatched — it
#: depends on the human gate, which nobody attests — and it is there so the run has work
#: left to hold it open once the gate is reached.
WORK_NODE = "work"


class Asked(NamedTuple):
    """A live run to ask questions on, and the environment every verb needs to see it."""

    environment: dict[str, str]
    run: RunId


def _sandboxed_home(tmp_path: Path) -> dict[str, str]:
    """A `HOME` of this journey's own, carrying a decoy at the once-shared tool path.

    Two reasons, and the second is what this returns rather than merely sets. Host state
    under the real `HOME` — an operator's `~/.config/onetaskgraph/secrets.env`, a Claude or
    codex config directory — reaches a launch these journeys make, so a sandbox is what
    makes them answer about this checkout. And the standalone `onetaskgraph` CLI was
    installed into `$HOME/.local/bin` until provisioning moved it into each checkout's own
    `.venv/bin`: one path the whole host shared, which the canonical checkout reverted below
    this one's pin roughly every 80 seconds, and four cases here failed every time it did.

    So the sandbox plants a *differently pinned* copy there and puts that directory first
    on `PATH`. Resolution then has to be positive rather than accidental: a launch reaches
    `onetaskgraph` through `uv run`, which prepends this checkout's own environment, so the
    decoy is passed over even from the front of the search path. Every launch in this
    module runs against it, and the journey below asserts it.
    """
    home = tmp_path / "home"
    shared_bin = home / ".local" / "bin"
    shared_bin.mkdir(parents=True, exist_ok=True)
    decoy = shared_bin / "onetaskgraph"
    decoy.write_text(
        f"#!/usr/bin/env bash\nprintf '%s\\n' 'onetaskgraph {DECOY_PIN}'\n", encoding="utf-8"
    )
    decoy.chmod(0o755)
    return {
        "HOME": str(home),
        "PATH": os.pathsep.join((str(shared_bin), os.environ["PATH"])),
        # uv resolves this project's environment on every `uv run` and caches that under
        # the real `HOME`. Re-resolving it per journey costs minutes and a network, neither
        # of which any journey here is about.
        "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR") or str(Path.home() / ".cache" / "uv"),
    }


def _environment(tmp_path: Path) -> dict[str, str]:
    """The environment one launched run and the questions asked on it share."""
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
    environment.update(_sandboxed_home(tmp_path))
    return environment


#: Every journey in this module is pinned to one worker, because each is a shim process and
#: a manager thread both waiting on `just` recipes, and several of those racing the rest of
#: a full suite is what turned a several-second round trip into one that outlived the
#: window it was given.
#:
#: `tests/e2e/nx_workspace.py`'s group, because every one of those `just` recipes reaches
#: its tool through `uv run`, which waits on the exclusive lock a journey re-provisioning
#: this checkout holds. A group of this module's own would co-locate these journeys with
#: each other and leave that writer free to run beside them on another worker, which is not
#: a constraint at all: `--dist loadgroup` serialises one group name, never two. One
#: `pytestmark` rather than a decorator per test, so a journey added here cannot miss it;
#: `tests/test_nx_cache_scope.py` holds the rule and records what an ungrouped one cost.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)


@pytest.fixture
def asked(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[Asked]:
    """A launched run whose frontier is a human gate, so its channel outlives the launch.

    `--dag-graph off` deliberately: with this host's observer graph attached, the monitor
    and the pacemaker raise surfaces of their own on the same channel, and a journey
    counting the questions one ask raised would be counting theirs too.

    The launch returns as soon as the gate is reached, which is what makes this cheap: the
    ledger and the channel are live afterwards, and nothing is running to spend anything.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    # Named after the journey, so a run left behind says which one left it — and sanitized
    # to what `onepipeline` mints a run id from unchanged, because a parametrized id carries
    # brackets and spaces that a run id is not.
    named = re.sub(r"[^A-Za-z0-9]+", "-", request.node.name)[-48:].strip("-")
    run = RunId(f"ask-manager-{named}")
    environment = _environment(tmp_path)
    plan = tmp_path / "asked.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "goal": {"text": "hold a channel open for an agent's question"},
                "name": run,
                "tasks": [
                    {
                        "id": "gate",
                        "kind": "human",
                        "task": "## What\nApprove.\n\n## Why\nHold the run.\n\n"
                        "## Acceptance criteria\n- Approved.",
                    },
                    {
                        "id": WORK_NODE,
                        "persona": "engineer",
                        "deps": ["gate"],
                        "task": "## What\nReport.\n\n## Why\nNever reached.\n\n"
                        "## Acceptance criteria\n- Reported.",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    launch = just(
        "orchestrate", project_from_plan(plan), "--dag-graph", "off", environment=environment
    )
    assert launch.returncode == 0, f"the launch failed:\n{launch.stdout}\n{launch.stderr}"
    assert (Path(environment["ONEPIPELINE_RUNS_DIR"]) / run).is_dir(), (
        f"the launch created no run named {run}, so every question below would be asked on "
        f"a channel nobody launched:\n{launch.stdout}\n{launch.stderr}"
    )
    try:
        yield Asked(environment, run)
    finally:
        just("stop", run, environment=environment, seconds=60)


def _ask(
    asked: Asked,
    *arguments: str,
    window: int,
    stdin: str | None = None,
    cwd: Path = REPO_ROOT,
) -> subprocess.Popen[str]:
    """Start the real shim the way a dispatched agent runs it, on the launched run.

    In a session of its own, so `_reaped` can end it with one signal to its group whatever
    it has become: the shim `exec`s the bus, so the process is the asker for as long as the
    question waits.
    """
    environment = dict(asked.environment)
    environment["ONEPIPELINE_RUN_ID"] = asked.run
    asking = subprocess.Popen(  # noqa: S603 - the real shim, as an agent runs it
        [str(ASK_MANAGER), "--timeout", str(window), *arguments],
        cwd=cwd,
        env=environment,
        text=True,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    if stdin is not None and asking.stdin is not None:
        asking.stdin.write(stdin)
        # Closed here rather than by the caller: the shim reads its question to EOF, so the
        # write side has to finish before there is a question at all. The handle goes with
        # it, because `communicate` flushes whatever `stdin` still names and raises on a
        # closed one.
        asking.stdin.close()
        asking.stdin = None
    return asking


def _reaped(asking: subprocess.Popen[str], *, seconds: float = 60) -> tuple[str, str]:
    """End one ask, by its own process group, and hand back what it had printed."""
    # Already gone is the state this is for; the pipes are still drained below, because
    # draining them is also what reaps the process.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(asking.pid, signal.SIGKILL)
    return asking.communicate(timeout=e2e_timeout(seconds))


class Watched(Protocol):
    """Whoever is playing the manager beside an ask, narrowed to what `_finish` reads."""

    def failure(self) -> BaseException | None: ...


#: How often `_finish` looks up from the shim to see whether the manager is still there.
#: Short and unscaled: it is a polling interval rather than a hang guard.
WATCH_INTERVAL_SECONDS = 0.5


def _finish(
    asking: subprocess.Popen[str],
    *,
    seconds: float = 180,
    manager: Watched | None = None,
) -> tuple[int, str, str]:
    """Wait for one ask and hand back what it reported.

    The manager is watched alongside it, because the two are one round trip and only one of
    the two failures is visible from this side: a manager who gave up leaves the shim
    waiting out its whole reply window — longer than the guard here — so waiting it out
    reports a killed ask with nothing on either pipe, in place of the sentence saying why
    nobody answered.
    """
    limit = deadline(seconds)
    while True:
        try:
            out, err = asking.communicate(timeout=WATCH_INTERVAL_SECONDS)
        except subprocess.TimeoutExpired:
            stopped = None if manager is None else manager.failure()
            if stopped is not None:
                out, err = _reaped(asking)
                raise AssertionError(
                    f"the manager stopped before the ask had its answer: {stopped}\n"
                    f"the ask was still waiting, and reported:\n{out}\n{err}"
                ) from None
            if time.monotonic() >= limit:
                out, err = _reaped(asking)
                raise AssertionError(f"the ask never returned:\n{out}\n{err}") from None
            continue
        return asking.returncode, out, err


class ReplyRecord(TypedDict):
    """The reply record the bus binds to a question, in the fields read here."""

    correlation: str
    reply: object


class BusAnswer(TypedDict):
    """The one line `onemessagebus ask` answers with, as its `docs/ask.md` states it.

    `reply` is present on a reply alone — every other answer carries the `answer` word and
    no `reply` member, so a caller reading only the reply reads nothing — and `reason` on a
    refusal alone.
    """

    answer: str
    correlation: NotRequired[str]
    reply: NotRequired[ReplyRecord]
    reason: NotRequired[str]


def _answered(out: str) -> BusAnswer:
    """The shim's stdout as the bus's one answer line, refusing anything more or less."""
    lines = out.splitlines()
    assert len(lines) == 1, (
        f"the ask printed {len(lines)} lines where the bus answers with exactly one, so a "
        f"caller reading its stdout as the answer would read something else too:\n{out}"
    )
    # `cast` rather than a validating read: `onemessagebus` owns this shape, and each field
    # a journey relies on is asserted where it is read.
    return cast(BusAnswer, json.loads(lines[0]))


#: How long a drain may go on handing surfaces out before it is a run producing them rather
#: than a queue emptying.
DRAIN_SECONDS = 120


def _drained(asked: Asked) -> list[Surface]:
    """Every surface still unread on the run, read the way a manager reads one, until none.

    Bounded, because "until none is left" is not a bound: a run that raises surfaces as fast
    as they are consumed empties nothing.
    """
    rest: list[Surface] = []
    limit = deadline(DRAIN_SECONDS)
    while (surface := next_surface_record(asked.run, asked.environment)) is not None:
        rest.append(surface)
        assert time.monotonic() < limit, (
            f"run {asked.run}'s channel was still handing surfaces out after {DRAIN_SECONDS}s: "
            f"{len(rest)} read, the last {surface['message'][:200]!r}"
        )
    return rest


#: The question every round trip asks, and the answer its manager gives.
QUESTION = "Should the test key cover docs, or only the code a test imports?"
ANSWER = "Key it on the whole workspace; the narrower key would replay a stale verdict."

#: The question file the `--file` form names by a RELATIVE path, from a directory that is
#: not this checkout. Distinctive rather than `question.txt`, so a shim that resolved it
#: against the checkout root could not find one there by accident.
QUESTION_FILE = "ask-from-a-worktree.question.txt"


def _asking_directory(tmp_path: Path) -> Path:
    """Where a dispatched agent asks from: a directory that is not this checkout.

    A lifecycle worker starts in its session worktree, so the shim must find this host's
    configuration from its own location and the run's channel from the environment, never
    from the directory it is run in. This one holds no `runs` and no `config`, so a shim
    that resolved either against its working directory would be asking somewhere nothing
    was launched.
    """
    directory = tmp_path / "worktree"
    directory.mkdir()
    (directory / QUESTION_FILE).write_text(f"{QUESTION}\n", encoding="utf-8")
    return directory


#: The three forms every task the dispatch appendix reaches spells, each as an agent would
#: write it: the question unquoted across several arguments, a `--file` path relative to
#: where the agent stands, and the question piped in.
FORMS = ("several arguments", "a relative --file", "stdin")


def _asked_by(asked: Asked, form: str, directory: Path, window: int) -> subprocess.Popen[str]:
    match form:
        case "several arguments":
            return _ask(asked, *QUESTION.split(" "), window=window, cwd=directory)
        case "a relative --file":
            return _ask(asked, "--file", QUESTION_FILE, window=window, cwd=directory)
        case _:
            return _ask(asked, window=window, stdin=f"{QUESTION}\n", cwd=directory)


@pytest.mark.parametrize("form", FORMS)
def test_each_way_of_asking_puts_one_question_to_the_manager_and_returns_only_their_reply(
    asked: Asked, tmp_path: Path, form: str
) -> None:
    """The round trip, through every form, from outside this checkout.

    Everything between the agent and the manager is real: the adapter, the engine's ask over
    `config/onemessagebus.yaml`, the run's channel, `just channel-next`, and `just
    channel-reply --correlation`. What is asserted is the whole of what the configuration
    owes each side.

    The manager is handed exactly one blocking question, reading as the agent wrote it, and
    it carries the correlation their reply binds by — `channel-next` and the shim reach
    the same queue under the same layout, or nothing would be handed out at all. The agent
    is handed exactly one line: the bus's `reply` answer, carrying that correlation and the
    envelope the manager sent, at exit 0. Its stderr is the verb's two announcements — that
    same correlation as the question is queued, and the window it waits — and nothing else.

    And the ask leaves nothing behind for the next reader: once it is answered, the run
    hands out no further surface.
    """
    asking = _asked_by(asked, form, _asking_directory(tmp_path), ANSWERED_WINDOW_SECONDS)
    manager = Manager(asked.run, asked.environment, [lambda _surface: ruling(ANSWER)])

    status, out, err = _finish(asking, manager=manager)
    manager.checked(asker_said=err)

    assert status == 0, f"asking with {form} did not return the manager's answer:\n{out}{err}"
    questions = [surface for surface in manager.surfaces if surface["blocking"]]
    assert len(questions) == 1, (
        f"asking with {form} put {len(questions)} blocking questions in front of the "
        f"manager, where one ask is one question: {manager.surfaces}"
    )
    [question] = questions
    assert question["message"].strip() == QUESTION, (
        f"asking with {form} reached the manager as {question['message']!r} rather than as "
        f"the question the agent asked"
    )
    correlation = question.get("correlation")
    assert correlation, f"the question reached the manager with no correlation: {question}"

    answer = _answered(out)
    assert answer["answer"] == "reply", f"the ask did not answer with a reply:\n{out}"
    assert answer.get("correlation") == correlation, (
        f"the reply the ask returned is bound to {answer.get('correlation')!r}, not to the "
        f"{correlation!r} the manager answered"
    )
    record = answer.get("reply")
    assert record is not None and record["reply"] == json.loads(ruling(ANSWER)), (
        f"the ask returned something other than the envelope the manager sent:\n{out}"
    )
    assert err.splitlines() == [
        f"correlation: {correlation}",
        f"waiting up to {ANSWERED_WINDOW_SECONDS} seconds for the reply",
    ], f"a successful ask reported something beyond the correlation it was queued under:\n{err}"

    left = _drained(asked)
    assert not left, f"an answered ask left surfaces on run {asked.run}'s channel: {left}"


def test_a_question_nobody_answers_ends_in_a_named_timeout_and_a_non_zero_exit(
    asked: Asked, tmp_path: Path
) -> None:
    """An elapsed wait is a timeout, named, and never an answer an agent could act on.

    Nobody reads the channel here. The shim must exit non-zero with the bus's `timeout`
    line — the `answer` word and the correlation, and no `reply` member for a caller to
    mistake for a decision.

    The question is then read off the run the way a manager would, because a timeout alone
    cannot say *where* the ask waited: a shim that asked an empty directory nobody launched
    times out exactly the same way. It is this run's channel that holds the question, as a
    blocking surface under the correlation the timeout named, still standing for a manager
    who comes to it late.
    """
    asking = _ask(
        asked,
        QUESTION,
        window=SHORT_WINDOW_SECONDS,
        cwd=_asking_directory(tmp_path),
    )

    status, out, err = _finish(asking)

    assert status == 1, f"an unanswered ask exited {status}:\n{out}{err}"
    answer = _answered(out)
    assert answer["answer"] == "timeout", f"an unanswered ask did not name a timeout:\n{out}"
    assert "reply" not in answer, f"an unanswered ask still carried a reply:\n{out}"
    correlation = answer.get("correlation")
    assert correlation, f"the timeout named no question it was about:\n{out}"

    standing = [surface for surface in _drained(asked) if surface.get("correlation") == correlation]
    assert len(standing) == 1 and standing[0]["blocking"], (
        f"run {asked.run}'s channel holds {standing} under {correlation}, so the ask that "
        f"timed out was not waiting on this run's channel"
    )
    assert standing[0]["message"].strip() == QUESTION, standing[0]


def test_the_round_trip_survives_a_differently_pinned_tool_at_the_once_shared_path(
    asked: Asked, tmp_path: Path
) -> None:
    """The same round trip, with a wrong-pinned `onetaskgraph` first on the search path.

    Provisioning installed that CLI into `$HOME/.local/bin` until it moved into each
    checkout's own `.venv/bin`, and while the path was shared a sibling checkout reinstalled
    its own release over this one — measured at roughly one reversion every 80 seconds —
    which is what failed four cases here at a time. `_sandboxed_home` plants exactly that: a
    copy reporting a release this checkout does not adopt, at that path, ahead of
    everything else on `PATH`.

    Both halves are asserted, because either alone passes for the wrong reason. That the
    decoy is really there and really disagrees is what says the environment was arranged;
    that the launch in the fixture and the round trip below both succeed anyway is what says
    resolution goes through this checkout's own environment rather than through whichever
    copy the search path reaches first.
    """
    decoy = Path(asked.environment["HOME"]) / ".local" / "bin" / "onetaskgraph"
    reported = subprocess.run(
        [str(decoy)],
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert reported.stdout.strip() == f"onetaskgraph {DECOY_PIN}", (
        f"the decoy at {decoy} reports {reported.stdout.strip()!r}; this journey is about a "
        "wrong-pinned copy being passed over, so there has to be one"
    )
    assert asked.environment["PATH"].split(os.pathsep)[0] == str(decoy.parent), (
        "the decoy is not first on PATH, so passing it over proves nothing about how the "
        f"tool is resolved: {asked.environment['PATH']}"
    )
    assert ADOPTED_ONETASKGRAPH != DECOY_PIN, (
        f"config/onetaskgraph.version now adopts {ADOPTED_ONETASKGRAPH}, which is the "
        "decoy's own release; pick another for DECOY_PIN"
    )

    asking = _ask(asked, QUESTION, window=ANSWERED_WINDOW_SECONDS)
    manager = Manager(asked.run, asked.environment, [lambda _surface: ruling(ANSWER)])

    status, out, err = _finish(asking, manager=manager)
    manager.checked(asker_said=err)

    assert status == 0, (
        "the round trip failed with a wrong-pinned onetaskgraph first on PATH, which is the "
        f"state a sibling checkout used to leave this host in:\n{out}{err}"
    )
    record = _answered(out).get("reply")
    assert record is not None and record["reply"] == json.loads(ruling(ANSWER)), out


def test_an_unset_run_is_refused_by_name_rather_than_guessed_at(asked: Asked) -> None:
    """A process with no run exported is refused, because guessing one asks the wrong run.

    Asked beside a real launched run under the same runs root, with the run variable removed
    entirely: the one run there is exactly what a shim that inferred one would pick, and an
    agent's question on somebody else's channel is one its own manager never sees. So the
    refusal is asserted by name, at the usage exit, with nothing on stdout for a caller to
    read as an answer — and the run's channel is read afterwards to say nothing was asked on
    it.
    """
    environment = dict(asked.environment)
    environment.pop("ONEPIPELINE_RUN_ID", None)

    refused = subprocess.run(  # noqa: S603 - the real shim, with no run exported
        [str(ASK_MANAGER), "--timeout", str(SHORT_WINDOW_SECONDS), QUESTION],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert refused.returncode == 2, f"an ask with no run exited {refused.returncode}"
    assert refused.stdout == "", refused.stdout
    assert "ONEPIPELINE_RUN_ID is not set" in refused.stderr, refused.stderr
    asked_anyway = [s for s in _drained(asked) if QUESTION in s["message"]]
    assert not asked_anyway, (
        f"an ask with no run exported still put its question on run {asked.run}: {asked_anyway}"
    )


class Refusal(NamedTuple):
    """One way of asking that cannot reach a manager, and what the refusal must say."""

    #: What the caller got wrong, for the test id.
    what: str
    #: The environment it reaches the shim with, over the working one.
    overrides: dict[str, str]
    #: The arguments it is called with.
    arguments: tuple[str, ...]
    #: A fragment the reported cause must carry.
    names: str


#: Every way of asking the engine's `ask` refuses before anything is raised. Each is a
#: state a dispatched agent can genuinely be in — a run computed badly, a question that
#: came out empty, a file that is not there, a window that is not a number, a run with no
#: launch record — and each has to say which, because the agent reading the refusal is the
#: one that has to repair it. The adapter decides none of them; the verb does.
REFUSALS = (
    Refusal("an empty run", {"ONEPIPELINE_RUN_ID": ""}, ("Which way?",), "is not set"),
    Refusal("a blank run", {"ONEPIPELINE_RUN_ID": "   "}, ("Which way?",), "is not set"),
    # Arrives from the environment a dispatch was started with, which is somebody else's to
    # write, and becomes part of the channel directory's path.
    Refusal(
        "a climbing run", {"ONEPIPELINE_RUN_ID": "../elsewhere"}, ("Which way?",), "not a run id"
    ),
    Refusal("an empty question", {}, ("   ",), "is blank"),
    Refusal("an unreadable file", {}, ("--file", "/nonexistent/question.txt"), "could not be read"),
    Refusal("a file beside words", {}, ("--file", "one.txt", "two.txt"), "cannot be used with"),
    Refusal("an unknown option", {}, ("--why",), "unexpected argument"),
    Refusal("a window that is not seconds", {}, ("--timeout", "soon", "Which way?"), "'soon'"),
    Refusal("a run with no launch record", {}, ("Which way?",), "launch.json"),
)


@pytest.mark.parametrize("refusal", REFUSALS, ids=lambda row: row.what)
def test_a_question_that_cannot_reach_a_manager_names_its_cause(
    tmp_path: Path, refusal: Refusal
) -> None:
    """Every refusal exits 2, is silent on stdout, and names its cause.

    An agent asking a question is already stuck; a refusal it cannot act on leaves it stuck
    with one more thing to work out. So each names what is wrong, and none leaves anything
    on stdout — a caller reads stdout as the answer. The runs root named here holds the
    run's channel directory, so a refusal that let the ask through would have somewhere to
    ask; nothing may be written there.
    """
    runs = tmp_path / "runs"
    channel = runs / "a-run" / "channel"
    channel.mkdir(parents=True)
    environment = dict(os.environ)
    for name in INHERITED_ENVIRONMENT:
        environment.pop(name, None)
    environment.update(
        {
            "ONEPIPELINE_RUNS_DIR": str(runs),
            "ONEPIPELINE_RUN_ID": "a-run",
            **refusal.overrides,
        }
    )

    refused = subprocess.run(  # noqa: S603 - the real shim, invoked wrongly
        [str(ASK_MANAGER), *refusal.arguments],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert refused.returncode == 2, f"asking with {refusal.what} exited {refused.returncode}"
    assert refused.stdout == "", (
        f"a refused question printed something a caller would read as an answer:\n{refused.stdout}"
    )
    assert refusal.names in refused.stderr, refused.stderr
    looked = subprocess.run(
        [
            "onemessagebus",
            "status",
            "--config",
            str(REPO_ROOT / "config" / "onemessagebus.yaml"),
            "--transport-dir",
            str(channel),
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert looked.returncode == 0, looked.stderr
    # `status` is the bus's own answer, one object per declared queue; only `records` is read.
    held = {queue["queue"]: queue["records"] for queue in json.loads(looked.stdout)}
    assert held and not any(held.values()), (
        f"asking with {refusal.what} was refused and still appended to the run's channel: {held}"
    )


#: The `graphs/node-scope.yaml` member a dispatched plan node runs as, and the
#: `graphs/dag-scope.yaml` member that watches the run. `oneagentgraph` gives every member a
#: scratch directory named after it and pins that member's harness configs inside it, so
#: the recorded `--config` is what says which member a turn belongs to.
WORKER_MEMBER = "worker"
MONITOR_MEMBER = "monitor"
MEMBER_OF_CONFIG = re.compile(r"/members/([^/]+)/")

#: Which environment variables the fake backend is asked to record per turn.
ENVIRONMENT_KEYS_ENV = "FAKE_BACKEND_ENVIRONMENT_KEYS"
PROMPT_LOG_ENV = "FAKE_BACKEND_PROMPT_LOG"

#: The variable the shim composes a run's channel directory from, and the run the journey
#: below launches.
RUN_ID_ENV = "ONEPIPELINE_RUN_ID"
DISPATCHED_RUN = RunId("ask-manager-dispatch-environment")


class TurnRecord(TypedDict):
    """One recorded harness turn, in the terms this journey reads it.

    `tests/e2e/fake_backend.py` writes this file, so the two fields consumed here are stated
    rather than validated: which member the turn served, and what its environment carried.
    """

    config: str | None
    environment: dict[str, str | None]


@pytest.fixture(scope="module")
def dispatched_turns(
    tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str
) -> list[TurnRecord]:
    """Launch one node for real and hand back every harness turn the run reached.

    A whole launch, with this host's observer graph attached, because the claim is about
    what a *dispatch* is given and the only way to see that is to make one: a variable
    reaches an agent by inheritance through `onepipeline`, `oneagentgraph`, and
    `oneharness`, and none of them reports what it passed on.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("ask-manager-dispatch-environment")
    environment = _environment(tmp_path)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    turns = tmp_path / "turns.jsonl"
    environment[PROMPT_LOG_ENV] = str(turns)
    environment[ENVIRONMENT_KEYS_ENV] = RUN_ID_ENV
    plan = tmp_path / "dispatch.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "goal": {"text": "record what a dispatched agent's environment carries"},
                "name": DISPATCHED_RUN,
                "tasks": [
                    {
                        "id": "only",
                        "persona": "engineer",
                        "task": "## What\nReport, changing nothing.\n\n## Why\nThe environment "
                        "the dispatch was given is the subject, not the work.\n\n"
                        "## Acceptance criteria\n- Reported.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    launch = just("orchestrate", project_from_plan(plan), environment=environment, seconds=300)
    try:
        assert launch.returncode == 0, f"the launch did not settle:\n{launch.stdout}{launch.stderr}"
        assert turns.is_file(), f"no harness turn was recorded at {turns}"
        # `cast` rather than a validating read: `tests/e2e/fake_backend.py` writes these
        # lines and `TurnRecord` states the two fields read off them, so a validator here
        # would restate that stand-in's own shape and fail on a turn it grew a field for.
        return [
            cast(TurnRecord, json.loads(line))
            for line in turns.read_text(encoding="utf-8").splitlines()
        ]
    finally:
        just("stop", DISPATCHED_RUN, environment=environment, seconds=60)


def _turns_of(turns: list[TurnRecord], member: str) -> list[TurnRecord]:
    """Every recorded turn that served one graph member."""
    found = []
    for turn in turns:
        named = MEMBER_OF_CONFIG.search(turn["config"] or "")
        if named is not None and named.group(1) == member:
            found.append(turn)
    return found


def test_a_dispatched_agent_is_given_the_run_it_belongs_to(
    dispatched_turns: list[TurnRecord],
) -> None:
    """`ONEPIPELINE_RUN_ID` reaches a dispatched worker holding that run's own id.

    This is the environment fact the whole ask seam rests on. `scripts/ask-manager.sh`
    composes the run's channel directory from it and refuses rather than guessing when it is
    absent, so an agent can only ask at all because a dispatch is given it — and it can only
    ask the *right* run because the value is that run's own id and not an enclosing one's.

    Measured rather than assumed, and measured here rather than in the shim's own journeys,
    which state the run themselves: nothing in a launch reports which variables it passed
    down, so a release that stopped exporting this would leave every dispatched question
    refused with no clue why.
    """
    dispatched = _turns_of(dispatched_turns, WORKER_MEMBER)
    assert dispatched, "the run dispatched no worker, so nothing here is about a dispatch"
    carried = {turn["environment"].get(RUN_ID_ENV) for turn in dispatched}
    assert carried == {DISPATCHED_RUN}, (
        f"a dispatched worker was given {sorted(str(value) for value in carried)} as "
        f"{RUN_ID_ENV}; a question asked from one of those would reach the wrong run"
    )


def test_an_observer_member_cannot_tell_its_run_from_an_enclosing_one_by_that_variable(
    dispatched_turns: list[TurnRecord],
) -> None:
    """The observer graph carries the same variable, so it is no proof of a dispatch.

    Worth pinning because the obvious reading of the fact above is the wrong one. The driver
    exports `ONEPIPELINE_RUN_ID` and the observer graph inherits it — which is also what
    `graphs/dag-scope.yaml`'s monitor judge side composes its `onemessagebus serve
    --transport-dir` from, and what `tests/e2e/test_orchestrate_launch_e2e.py` measures on
    that judge side.

    What that costs is precision, not correctness: reading the variable tells a process
    which run it is *under*, never whether it is the dispatch of a node. So the shim may
    trust the value it finds and must not infer anything from merely finding one — and if a
    release goes back to withholding it, this fails and says to re-read.
    """
    watching = _turns_of(dispatched_turns, MONITOR_MEMBER)
    assert watching, "the run started no monitor, so there is no observer turn to read"
    carried = {turn["environment"].get(RUN_ID_ENV) for turn in watching}
    assert carried == {DISPATCHED_RUN}, (
        f"the observer member was given {sorted(str(value) for value in carried)} as "
        f"{RUN_ID_ENV}; the adopted release exports the run's own id to it, and a change "
        f"here changes what reading that variable proves"
    )
