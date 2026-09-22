"""`just channel-reply` over a real run's channel: this host's wiring of the planner channel.

Everything the channel does with a reply once it is offered — binding a verdict by its
correlation, refusing one no pending question holds, routing an envelope's halves, running
a validator before anything is appended and caching only its passes — is the installed
`onemessagebus` release's, proven by that release's own suite. What these journeys prove is
the **configuration**: that the recipe reaches that behaviour over `config/onemessagebus.yaml`
and the channel directory of a run `just orchestrate` really launched, that the validator
the configuration names is this repository's criteria bar, and that its pass cache keys on
this repository's bar fingerprint. Six journeys:

* a ruling echoing the correlation of a question the manager already answered is refused
  naming that correlation, with nothing appended, after the same ruling answered the real
  question the shim asked — and the bus's own answer is the whole of what the recipe prints;
* a live edit sent while no question is pending reaches the run's `commands` queue through
  `onemessagebus send replies` and is applied — or rejected — by the engine's own driver;
* an envelope whose task prose the validator refuses is refused whole with the validator's
  reason, nothing appended and nothing cached;
* a novel whole task the judged review refuses is refused whole with that review's
  findings, after exactly one judged turn;
* a novel whole task whose judged turn answers nothing is not sent, because an unjudged
  envelope never passes;
* an identical envelope sent again under an unchanged bar passes from the pass cache,
  spending no second judged turn.

Only the paid model is doubled, at the `oneharness` seam, exactly as
`tests/ask_seam/ask_manager/test_ask_manager_e2e.py` doubles it. A channel is read through
the bus's own `status` and `subscribe` verbs and through `just channel-next`, never through
its files.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] This *is* the edge: every
journey here spends a real launch and is behind its own — this directory is an Nx project
of its own, keyed on `askSeamChannelReply` and selected by directory, which
`tests/ask_seam/AGENTS.md` states as the settled design and `tests/conftest.py` enforces.
That key names, file by file, what these journeys were measured reading — the validator
script and its module `just channel-reply` runs under the bus configuration, the base
config and the planner persona the bar it holds an envelope to is fingerprinted over, and
the graphs a launch reads — and nothing wider, so an edit outside it replays a green this
tier already earned and an edit inside it re-runs the journey that read it.
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
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple, NewType, cast

import pytest
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from planner_channel import BUS_CONFIG, next_surface_record, reply, ruling
from project_fixtures import helper, project_from_plan
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The wrapper a dispatched agent asks through, which is what raises the real question.
ASK_MANAGER = REPO_ROOT / "scripts" / "ask-manager.sh"

#: The stand-in for the paid model and the provider binary beneath it.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")

#: The guard over the identities `ONEHARNESS_BIN_*` cannot reach. The judged tier the
#: validator spends a turn of goes through `oneharness.plan-review.toml`'s chain, whose
#: Codex identities are doubled by the stand-in above and whose Claude ones are only
#: reached by a chain that fell through — and where a billed turn and a free one look
#: identical from a journey's assertions, this is what makes the difference loud.
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: What the doubled provider answers a review turn with: a verdict that passes, in the
#: shape `config/plan-review-verdict.schema.json` declares.
PASSING_VERDICT = {"passes": True, "findings": []}

#: A launching session these journeys state rather than inherit: this suite runs inside
#: a dispatch whose own harness session would otherwise own the runs.
LAUNCHING_SESSION = "e2e-channel-reply"

#: Every name a launcher identity reaches `scripts/onepipeline.sh` through, plus the run
#: and the asker the enclosing dispatch belongs to — which `scripts/ask-manager.sh` reads,
#: so a journey that did not clear them would be asking the *outer* run's channel.
INHERITED_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
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
#: ever dispatched from the gated plan — the node exists so an edit has a real node to be
#: addressed to.
WORK_NODE = "work"
GATE_NODE = "gate"

#: What `just channel-reply` exits with when the bus says no to a well-formed reply — a
#: correlation nothing pending holds, or a validator's refusal (`docs/cli.md`'s exit codes).
REPLY_REFUSED = 1

#: The reply window a question is asked under while a journey answers it: long enough that
#: the shim never gives up first and reports the bus's `timeout` instead of the subject.
ASK_WINDOW_SECONDS = int(e2e_timeout(300))

#: `tests/e2e/nx_workspace.py`'s group, applied to the module rather than per journey.
#: Every journey here spends a real `just orchestrate` through a fixture, and every step
#: of the round trip is a `just` recipe blocking on `uv run`, which waits on the
#: exclusive lock a journey re-provisioning this checkout holds.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

#: A onepipeline run id. Distinguished from the prose it is built out of, because what
#: makes a string a run id is where it came from.
RunId = NewType("RunId", str)


class Replying(NamedTuple):
    """A live run to reply on, and the environment every verb needs to see it."""

    environment: dict[str, str]
    run: RunId
    #: The run's channel directory, which every bus verb here is pointed at.
    channel: Path


def _environment(tmp_path: Path) -> dict[str, str]:
    """The environment one launched run and every verb against it share."""
    environment = dict(os.environ)
    for name in INHERITED_ENVIRONMENT:
        environment.pop(name, None)
    environment.pop("VIRTUAL_ENV", None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # And the identities that seam cannot reach; see `PAID_PROVIDER_GUARD`.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    # The judged review turn the validator spends on a novel whole task, scripted to pass;
    # `_judged_turns` counts how many the doubled provider was asked for.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider's answer is scripted.
    environment["FAKE_CODEX_ANSWERS"] = json.dumps([json.dumps(PASSING_VERDICT)])
    environment["FAKE_CODEX_ATTEMPT_LOG"] = str(tmp_path / "review-launches")
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
    *arguments: str, environment: dict[str, str], stdin: str | None = None, seconds: float = 180
) -> subprocess.CompletedProcess[str]:
    """Run one real recipe from this checkout."""
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _named(request: pytest.FixtureRequest, prefix: str) -> RunId:
    """A run id of this journey's own, sanitized to what `onepipeline` mints unchanged."""
    named = re.sub(r"[^A-Za-z0-9]+", "-", request.node.name)[-32:].strip("-")
    return RunId(f"{prefix}-{SUITE}-{named}")


def _task(what: str) -> str:
    """One node's prose, in the template every plan this repository ships uses."""
    return f"## What\n{what}\n\n## Why\nHold the run.\n\n## Acceptance criteria\n- Done."


@pytest.fixture
def replying(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[Replying]:
    """A launched run whose frontier is a human gate, so its channel outlives the launch.

    `--dag-graph off` deliberately: with this host's observer graph attached, the monitor
    and the pacemaker raise surfaces of their own on the same channel, and what is pending
    is part of the subject here. Nothing is ever dispatched — `work` depends on the gate
    and nobody attests it — so this run costs a launch and no provider turn at all.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    run = _named(request, "channel-reply")
    environment = _environment(tmp_path)
    plan = tmp_path / f"{run}.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "goal": {"text": "hold a channel open for a manager's reply"},
                "name": run,
                "tasks": [
                    {"id": GATE_NODE, "kind": "human", "task": _task("Approve.")},
                    {
                        "id": WORK_NODE,
                        "persona": "engineer",
                        "deps": [GATE_NODE],
                        "task": _task("Report."),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    launch = _just(
        "orchestrate",
        project_from_plan(plan),
        "--dag-graph",
        "off",
        environment=environment,
        seconds=600,
    )
    assert launch.returncode == 0, f"the launch failed:\n{launch.stdout}\n{launch.stderr}"
    try:
        yield Replying(environment, run, tmp_path / "runs" / run / "channel")
    finally:
        _just("stop", run, environment=environment, seconds=60)


def _bus(replying: Replying, *arguments: str, seconds: float = 60) -> str:
    """One reading verb of the bus over this run's channel, its stdout on success."""
    read = subprocess.run(
        [
            "onemessagebus",
            *arguments,
            "--config",
            str(BUS_CONFIG),
            "--transport-dir",
            str(replying.channel),
        ],
        cwd=REPO_ROOT,
        env=replying.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )
    assert read.returncode == 0, f"`onemessagebus {' '.join(arguments)}` failed:\n{read.stderr}"
    return read.stdout


def _records(replying: Replying, queue: str) -> int:
    """How many records the run's `queue` holds, as the bus's own `status` counts them.

    The count is of the log, not of what is still waiting, so a record a driver has since
    claimed still counts: "nothing was appended" is this number not moving.
    """
    # `cast` rather than a validating read: `status` is the bus's own answer, one object
    # per queue named, and the one member read here is converted where it is read.
    (status,) = cast(list[dict[str, Any]], json.loads(_bus(replying, "status", queue)))
    return int(status["records"])


def _pending(replying: Replying) -> object:
    """The question the run's `surfaces` queue holds pending, per the bus, or `None`."""
    # `cast` for the reason `_records` gives: the bus owns `status`'s shape, and `pending`
    # is handed back as the untyped value it is, for the caller to compare.
    (status,) = cast(list[dict[str, Any]], json.loads(_bus(replying, "status", "surfaces")))
    return status["pending"]


def _judged_turns(replying: Replying) -> int:
    """How many review turns the doubled provider has been asked for so far.

    Read off the attempt log the stand-in appends to per launch — the only moment a
    journey can prove the provider was reached — because a judged turn spent and a pass
    found in the cache look the same from the recipe's own answer.
    """
    log = Path(replying.environment["FAKE_CODEX_ATTEMPT_LOG"])
    return len(log.read_text(encoding="utf-8").splitlines()) if log.is_file() else 0


# `Any` in the envelopes and commands below because an envelope is the channel's open JSON
# contract: its values are whatever JSON each op carries, and the bus, the validator and the
# engine each read the members they own.
def _send(
    replying: Replying, envelope: dict[str, Any], environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Send one envelope as a manager types it: piped into `just channel-reply <run>`.

    `environment` replaces the run's own for this one send, which is how a journey scripts
    what the doubled reviewer answers the validator's judged turn.
    """
    return _just(
        "channel-reply",
        replying.run,
        environment=replying.environment if environment is None else environment,
        stdin=json.dumps(envelope),
        seconds=300,
    )


def _novel_task(replying: Replying, subject: str) -> str:
    """A whole task nothing holds a pass for, which the deterministic bar takes.

    Carrying the run's own id and `subject`, so no earlier send anywhere — from this checkout
    or from another journey of this run — can already have cleared it: the judged turn is
    owed, which is what the journeys using it are about.
    """
    return "\n\n".join(
        (
            f"## What\n\nAdd the {subject} run {replying.run} needs and the test that drives it.",
            "## Why\n\nThe user cannot complete a purchase without it.",
            "## Acceptance criteria\n\n- The finished tree carries an assertion whose "
            "subject is that behaviour, so removing it fails.",
            "## Additional info\n\nRun `just test` over what you changed, and commit it.",
        )
    )


def _adding(node: str, task: str) -> dict[str, Any]:
    """An envelope adding one engineer node behind the gate, with `task` as its prose."""
    return {
        "version": 2,
        "commands": [
            {
                "op": "add",
                "node": {"id": node, "persona": "engineer", "deps": [GATE_NODE], "task": task},
            }
        ],
    }


def _answer_line(sent: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    """The bus's answer, which is the whole of a successful send's stdout: one JSON line."""
    printed = sent.stdout.splitlines()
    assert len(printed) == 1, (
        f"a successful reply printed {len(printed)} line(s) where the bus's own answer is "
        f"the whole of the output:\n{sent.stdout}"
    )
    # `cast` rather than a validating read: the bus owns this shape, and each journey
    # asserts the members it is about.
    return cast(dict[str, Any], json.loads(printed[0]))


def _note(node: str, text: str) -> dict[str, Any]:
    """One `note` command, spelled with the `addressee` the op requires."""
    return {"op": "note", "id": node, "addressee": "worker", "text": text}


def _reaped(started: subprocess.Popen[str]) -> None:
    """End a process group this journey started, whether or not it has already ended."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(os.getpgid(started.pid), signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired, ValueError):
        started.communicate(timeout=e2e_timeout(60))


ANSWER = "Key it on the whole workspace; the narrower key would replay a stale verdict."


def test_a_ruling_echoing_a_correlation_no_pending_question_holds_is_refused_naming_it(
    replying: Replying,
) -> None:
    """The manager's mistake a correlation exists to catch: answering the same question twice.

    The shim asks a real blocking question on the run's channel; the manager reads it
    through `just channel-next`, which hands out the correlation the bus stamped on it, and
    answers with `--correlation`. That answer is the one the asking agent reads back, and
    the bus's own `{answered, correlation, sent}` line is the whole of what the recipe
    printed. The same ruling sent again names a correlation nothing pending holds any
    more, so it is refused naming it, and nothing is appended to `replies`.
    """
    environment = dict(replying.environment)
    environment["ONEPIPELINE_RUN_ID"] = replying.run
    asking = subprocess.Popen(  # noqa: S603 - the real wrapper, as an agent runs it
        [str(ASK_MANAGER), "--timeout", str(ASK_WINDOW_SECONDS), "Should the test key cover docs?"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        limit = deadline(120)
        question = None
        while question is None:
            assert time.monotonic() < limit, "the shim's question never reached channel-next"
            surface = next_surface_record(replying.run, replying.environment)
            if surface is not None and surface.get("correlation") and surface["blocking"]:
                question = surface
            else:
                time.sleep(0.5)
        correlation = question["correlation"]

        answered = reply(replying.run, replying.environment, ruling(ANSWER), correlation)

        assert answered.returncode == 0, f"{answered.stdout}{answered.stderr}"
        receipt = _answer_line(answered)
        assert receipt["correlation"] == correlation, receipt
        assert receipt["answered"]["record"]["correlation"] == correlation, receipt
        assert [sent["queue"] for sent in receipt["sent"]] == ["replies"], receipt
        out, err = asking.communicate(timeout=e2e_timeout(120))
        assert asking.returncode == 0, f"the asking agent read no reply:\n{err}{out}"
        read_back = json.loads(out)
        assert read_back["answer"] == "reply", read_back
        assert read_back["reply"]["reply"]["message"] == ANSWER, read_back

        before = _records(replying, "replies")
        again = reply(replying.run, replying.environment, ruling(ANSWER), correlation)

        assert again.returncode == REPLY_REFUSED, (
            f"a ruling for a question already answered exited {again.returncode}:\n"
            f"{again.stdout}{again.stderr}"
        )
        assert f"no pending ask carries the correlation {correlation}" in again.stderr, (
            f"the refusal does not name the correlation it could not bind:\n{again.stderr}"
        )
        assert again.stdout == "", again.stdout
        assert _records(replying, "replies") == before, "a refused ruling was appended anyway"
    finally:
        _reaped(asking)


def _adopted(replying: Replying, *, seconds: float = 300) -> None:
    """Attach a driver to the run, once the launch's own has let go, and let it drain.

    `just orchestrate --adopt` is for a run nothing is driving and refuses one that still
    is, so only that refusal is retried; the adopted driver reconciles the run's command
    queue and returns once the run is parked on its gate again.
    """
    limit = deadline(seconds)
    refused = ""
    while time.monotonic() < limit:
        adopted = _just(
            "orchestrate", "--adopt", replying.run, environment=replying.environment, seconds=300
        )
        if adopted.returncode == 0:
            return
        refused = adopted.stdout + adopted.stderr
        assert "is still being driven" in refused, f"the adoption failed:\n{refused}"
        time.sleep(1.0)
    raise AssertionError(f"run {replying.run} was still being driven:\n{refused}")


def _outcome(replying: Replying, envelope_id: int) -> dict[str, Any]:
    """The engine's answer to the command envelope `envelope_id`, read through the bus."""
    until = json.dumps({"field": "id", "equals": envelope_id})
    streamed = _bus(
        replying, "subscribe", "command-outcomes", "--until", until, "--timeout", "120", seconds=180
    )
    # The last line is the one `--until` admitted. `cast` rather than a validating read:
    # the record is the engine's own command outcome, and each journey asserts the
    # members it is about.
    return cast(dict[str, Any], json.loads(streamed.splitlines()[-1])["record"])


def test_a_live_edit_sent_with_no_question_pending_reaches_the_engine(replying: Replying) -> None:
    """Most of a run has nothing pending, and a manager steers it all the same.

    An envelope carrying commands and no verdict binds to no question, so the recipe sends
    it with `onemessagebus send replies`, which the layout routes to the run's `commands`
    queue — where `reply` would refuse it for having nothing to bind to. The bus's own
    `{queue, position, id}` line is the whole of stdout. Then the engine decides it: a
    driver attached to the run applies the note addressed to the run's own node and
    rejects the one addressed to a node the run does not have, each answered on
    `command-outcomes` against the envelope id the send printed.
    """
    assert _pending(replying) is None, "the premise is a run with no question pending"
    before = _records(replying, "commands")

    applied = _send(replying, {"version": 2, "commands": [_note(WORK_NODE, "the base moved")]})
    rejected = _send(replying, {"version": 2, "commands": [_note("nobody", "the base moved")]})

    for sent in (applied, rejected):
        assert sent.returncode == 0, f"a live edit was refused:\n{sent.stdout}{sent.stderr}"
    queued = [_answer_line(sent) for sent in (applied, rejected)]
    assert [line["queue"] for line in queued] == ["commands", "commands"], queued
    assert _records(replying, "commands") == before + 2

    _adopted(replying)

    took = _outcome(replying, int(queued[0]["id"]))
    assert took["applied"] is True, f"the engine did not apply the note to its node: {took}"
    refused = _outcome(replying, int(queued[1]["id"]))
    assert refused["applied"] is False, f"the engine applied a note to no node: {refused}"
    assert "nobody" in json.dumps(refused), refused


#: The amendment this refusal was written from, verbatim as it was sent during the run it
#: cost: the checks it names run on the host after publication, so the agent step it binds
#: has ended before any of them start.
UNSATISFIABLE_AMENDMENT = (
    "The finished branch merges cleanly into its base and its change request's "
    "required checks pass."
)


def test_task_prose_the_validator_refuses_is_refused_whole_with_its_reason(
    replying: Replying,
) -> None:
    """The validator the configuration names is this host's criteria bar, and it binds.

    An amendment resting on the merge path's verdict, with a sound note riding beside it,
    is refused **whole** by the bus with the validator's own reason on stderr: nothing is
    appended to `commands`, and no judged turn is spent on a text the free tier refuses.
    The note sent on its own then lands.
    """
    riding = _note(WORK_NODE, "a note riding beside an amendment")
    before = _records(replying, "commands")

    refused = _send(
        replying,
        {
            "version": 2,
            "commands": [{"op": "amend", "id": WORK_NODE, "text": UNSATISFIABLE_AMENDMENT}, riding],
        },
    )

    assert refused.returncode == REPLY_REFUSED, (
        f"an envelope carrying an unsatisfiable amendment exited {refused.returncode}:\n"
        f"{refused.stdout}{refused.stderr}"
    )
    assert "the amendment for node 'work'" in refused.stderr, refused.stderr
    assert "required checks pass" in refused.stderr, refused.stderr
    assert "nothing was sent" in refused.stderr, refused.stderr
    assert refused.stdout == "", refused.stdout
    assert _records(replying, "commands") == before, "a refused envelope reached the queue"
    assert _judged_turns(replying) == 0, "a text the free tier refuses was put to the judge"

    landed = _send(replying, {"version": 2, "commands": [riding]})

    assert landed.returncode == 0, f"{landed.stdout}{landed.stderr}"
    assert _answer_line(landed)["queue"] == "commands"
    assert _records(replying, "commands") == before + 1


#: What the doubled reviewer finds in a task it refuses, in the verdict schema's shape. The
#: `why` is distinctive so the refusal can be read for the reviewer's own words.
JUDGED_FINDING = {
    "criterion": "- The finished tree carries an assertion whose subject is that behaviour",
    "why": "names no journey a reader could run to watch that assertion fail",
}


def test_a_novel_task_the_judged_review_refuses_is_refused_whole_with_its_findings(
    replying: Replying,
) -> None:
    """The judged tier binds through the bus as the deterministic one does.

    A novel whole task clears the free tier and is put to one judged turn, and here the
    reviewer refuses it. The bus reads the validator's refusal and appends nothing: the
    recipe exits refused with the review's finding on stderr, and the run's `commands`
    queue does not move. And the refusal is not remembered as a pass: the same envelope,
    sent again to a reviewer that passes it, is judged again before it lands.
    """
    envelope = _adding("reviewed", _novel_task(replying, "refund"))
    environment = {
        **replying.environment,
        "FAKE_CODEX_ANSWERS": json.dumps(
            [json.dumps({"passes": False, "findings": [JUDGED_FINDING]})]
        ),
    }
    before = _records(replying, "commands")

    refused = _send(replying, envelope, environment)

    assert refused.returncode == REPLY_REFUSED, f"{refused.stdout}{refused.stderr}"
    assert "was refused by its judged review" in refused.stderr, refused.stderr
    assert JUDGED_FINDING["why"] in refused.stderr, refused.stderr
    assert "nothing was sent" in refused.stderr, refused.stderr
    assert refused.stdout == "", refused.stdout
    assert _judged_turns(replying) == 1, "the refusal did not come from one judged turn"
    assert _records(replying, "commands") == before, "a refused envelope reached the queue"

    passed = _send(replying, envelope)

    assert passed.returncode == 0, f"{passed.stdout}{passed.stderr}"
    assert _judged_turns(replying) == 2, "the refused envelope was passed without being judged"
    assert _records(replying, "commands") == before + 1


def test_a_novel_task_whose_judged_turn_answers_nothing_is_never_sent(
    replying: Replying,
) -> None:
    """No verdict is not a pass: the validator exits unjudged, and the bus sends nothing.

    Every launch of the doubled reviewer dies after it starts, so the review chain ends
    with no verdict at all. `orchestrator.envelope_review` exits unjudged rather than
    reading that either way, and the bus holds an unjudged envelope back exactly as it
    holds a refused one: nothing is appended, and the recipe says the prose could not be
    judged, not that it was refused. Nor is anything remembered: the same envelope, sent
    again once the reviewer answers, is judged before it lands.
    """
    envelope = _adding("unjudged", _novel_task(replying, "invoice"))
    environment = {**replying.environment, "FAKE_CODEX_UNAVAILABLE_ATTEMPTS": "1000"}
    before = _records(replying, "commands")

    unjudged = _send(replying, envelope, environment)

    assert unjudged.returncode == REPLY_REFUSED, f"{unjudged.stdout}{unjudged.stderr}"
    assert "could not be judged" in unjudged.stderr, unjudged.stderr
    assert "was refused" not in unjudged.stderr, unjudged.stderr
    assert unjudged.stdout == "", unjudged.stdout
    reached = _judged_turns(replying)
    assert reached >= 1, "the reviewer was never reached, so nothing was tested"
    assert _records(replying, "commands") == before, "an unjudged envelope reached the queue"

    answered = _send(replying, envelope)

    assert answered.returncode == 0, f"{answered.stdout}{answered.stderr}"
    assert _judged_turns(replying) == reached + 1, (
        "the unjudged envelope was passed without being judged once the reviewer answered"
    )
    assert _records(replying, "commands") == before + 1


def test_an_identical_envelope_under_an_unchanged_bar_passes_from_the_cache(
    replying: Replying,
) -> None:
    """One judged turn per envelope under one bar, whatever number of times it is sent.

    A novel whole task spends the judged turn once, and the bus records the pass under
    `config/onemessagebus.yaml`'s cache. The same envelope sent again under the same bar
    is passed from that record: the doubled reviewer is not asked again, while both sends
    reach the run's `commands` queue. What a manager sees of the cache is exactly that — a
    second send that costs no turn — so that is what is asserted, and nothing under the
    cache directory is read. Its record is keyed on content unique to this run, so no
    other send can ever be passed by it.
    """
    envelope = _adding("cached", _novel_task(replying, "route"))
    before = _records(replying, "commands")

    first = _send(replying, envelope)

    assert first.returncode == 0, f"a sound whole task was refused:\n{first.stderr}"
    assert _judged_turns(replying) == 1, "the first send did not spend exactly one turn"

    second = _send(replying, envelope)

    assert second.returncode == 0, f"{second.stdout}{second.stderr}"
    assert _judged_turns(replying) == 1, (
        "the identical envelope under an unchanged bar was judged again rather than "
        "passed from the record the first send left"
    )
    assert _records(replying, "commands") == before + 2
    assert _answer_line(second)["queue"] == "commands"
