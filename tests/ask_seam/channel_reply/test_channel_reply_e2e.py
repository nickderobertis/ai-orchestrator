"""`just channel-reply` over real runs' channels: this host's wiring of the planner channel.

The recipe is the engine's own submission verb, `onepipeline reply`, and everything it does
with an envelope — binding a verdict by its correlation, refusing one no pending question
holds, routing an envelope's halves, running the recorded bus configuration's validator
before anything is appended, applying an edit to a run nothing drives, waiting on a driver
that holds one — is the installed engine's and bus's, proven by their own suites. What these
journeys prove is the **wiring**: that the recipe reaches that behaviour over runs `just
orchestrate` really launched, under the bus configuration `config/onemessagebus.yaml` those
launches recorded; that the validator the configuration names is this repository's criteria
bar and its pass cache keys on this repository's bar fingerprint; and that the recipe's
whole output is the engine's receipt and exit status. The journeys:

* a `complete` command, and separately a completion verdict, close a settled run whose
  driver has exited: before the reply the real `just unfinished` and the real `Stop` hook
  both hold the session to the run, after it neither does, and the run's journal records
  the completion with the reason sent;
* a ruling bound to a real question by its correlation answers it; the same ruling again,
  one naming a correlation nothing raised, and a commands-only envelope naming a
  correlation are each refused with nothing appended;
* an envelope whose task prose the validator refuses is refused whole with the validator's
  reason — with and without a verdict half, on a run nothing drives and on a run a driver
  holds — and nothing of it is appended, applied, journalled or remembered as a pass;
* a valid edit is applied by the driver holding a run, or by the reply itself where nothing
  drives the run, and is recorded as the planner's when it names no author; an envelope
  read from a file and one read from stdin are both taken;
* an edit the driver has not reconciled within the reply's wait returns `queued` inside
  that bound, says it is not to be sent again, and stays on the run's command queue;
* an undeclared author, and the monitor naming an op it is not granted, are refused;
* a novel whole task the judged review refuses is refused whole with that review's
  findings, after exactly one judged turn;
* a novel whole task whose judged turn answers nothing is not applied, because an unjudged
  envelope never passes;
* an identical envelope sent again under an unchanged bar passes from the pass cache,
  spending no second judged turn.

Only the paid model is doubled, at the `oneharness` seam, exactly as
`tests/ask_seam/ask_manager/test_ask_manager_e2e.py` doubles it. A channel is read through
the bus's own `status` and `subscribe` verbs and through `just channel-next`, never through
its files; a run's journal is read off its `events.jsonl`, as the other launch journeys
read it.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] This *is* the edge: every
journey here spends a real launch and is behind its own — this directory is an Nx project
of its own, keyed on `askSeamChannelReply` and selected by directory, which
`tests/ask_seam/AGENTS.md` states as the settled design and `tests/conftest.py` enforces.
That key names, file by file, what these journeys were measured reading — the validator
script and its module the engine runs under the recorded bus configuration, the base
config and the planner persona the bar it holds an envelope to is fingerprinted over, the
graphs and harness configs a launch and its one dispatch read, and the Stop hook and the
`unfinished` view the completion journeys ask — and nothing wider, so an edit outside it
replays a green this tier already earned and an edit inside it re-runs the journey that
read it.
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
import short_state
from fake_backend import TURN_GATE_ENV, TURN_GATE_REACHED, TURN_GATE_RELEASED
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

#: The settings file whose `Stop` entry the completion journeys run, as the harness runs it.
SETTINGS = REPO_ROOT / ".claude" / "settings.json"

#: What the doubled provider answers a review turn with: a verdict that passes, in the
#: shape `config/plan-review-verdict.schema.json` declares.
PASSING_VERDICT = {"passes": True, "findings": []}

#: A launching session these journeys state rather than inherit: this suite runs inside
#: a dispatch whose own harness session would otherwise own the runs.
LAUNCHING_SESSION = "e2e-channel-reply"

#: Every name a launcher identity reaches `scripts/onepipeline.sh` through, plus the run
#: and the asker the enclosing dispatch belongs to — which `scripts/ask-manager.sh` reads,
#: so a journey that did not clear them would be asking the *outer* run's channel — and
#: the reply wait, which only the journey about it sets.
INHERITED_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "ONEPIPELINE_CHANNEL_ASKER",
    "ONEPIPELINE_REPLY_TIMEOUT_SECONDS",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: What makes this suite run's run ids its own. Keyed on the checkout and this worker's
#: process, because both collide: two checkouts of this repository run at once here, and
#: so do two suites of one checkout.
SUITE = hashlib.sha256(f"{REPO_ROOT}\0{os.getpid()}".encode()).hexdigest()[:8]

#: The agent node every gated plan below carries, and the human gate that holds it. Nothing
#: is ever dispatched from the gated plan — the node exists so an edit has a real node to be
#: addressed to.
WORK_NODE = "work"
GATE_NODE = "gate"

#: The one node of the driven plan, whose worker turn is held at the stand-in's gate for
#: as long as the journeys need a driver holding the run.
HELD_NODE = "held"

#: What `just channel-reply` exits with for every refusal: the engine's `reply` exits 2,
#: whether the channel, the author's grants, the validator or the graph refused.
REPLY_REFUSED = 2

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
    """A launched run to reply on, and the environment every verb needs to see it."""

    environment: dict[str, str]
    run: RunId
    #: The run's channel directory, which every bus verb here is pointed at.
    channel: Path
    #: The run's own directory, whose journal says what the run recorded.
    root: Path


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
    environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp_path))
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


#: A node that settles without a dispatch: no persona, and nothing for it to change.
SETTLES_ON_ITS_OWN = "Report.\n\n## Acceptance criteria\n- Reported."


def _require_just() -> None:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")


def _launched(
    tmp_path: Path,
    environment: dict[str, str],
    run: RunId,
    tasks: list[dict[str, object]],
    *flags: str,
) -> Replying:
    """Launch `tasks` as run `run` through the real `just orchestrate`, and hand it back.

    `--dag-graph off` deliberately: with this host's observer graph attached, the monitor
    and the pacemaker raise surfaces of their own on the same channel, and what is pending
    and what the journal records are part of the subject here.
    """
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
    launch = _just(
        "orchestrate",
        project_from_plan(plan),
        "--dag-graph",
        "off",
        *flags,
        environment=environment,
        seconds=600,
    )
    assert launch.returncode == 0, f"the launch failed:\n{launch.stdout}\n{launch.stderr}"
    runs = Path(environment["ONEPIPELINE_RUNS_DIR"])
    return Replying(environment, run, runs / run / "channel", runs / run)


@pytest.fixture
def replying(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[Replying]:
    """A launched run whose frontier is a human gate, and which nothing drives.

    Nothing is ever dispatched — `work` depends on the gate and nobody attests it — so this
    run costs a launch and no provider turn at all, and the launch's driver hands the run
    back at the gate: from then on nothing drives it, which `_undriven` asks the run itself.
    """
    _require_just()
    run = _named(request, "channel-reply")
    launched = _launched(
        tmp_path,
        _environment(tmp_path),
        run,
        [
            {"id": GATE_NODE, "kind": "human", "task": _task("Approve.")},
            {
                "id": WORK_NODE,
                "persona": "engineer",
                "deps": [GATE_NODE],
                "task": _task("Report."),
            },
        ],
    )
    try:
        yield launched
    finally:
        _just("stop", run, environment=launched.environment, seconds=60)


@pytest.fixture
def settled(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[Replying]:
    """A run that settled complete and whose attached driver has exited.

    One node that settles without a dispatch, launched attached, so the launch returns
    once the run has settled. The run-end hooks are named empty, because the success hook
    would launch a follow-up run this session would then owe as well.
    """
    _require_just()
    run = _named(request, "settled")
    launched = _launched(
        tmp_path,
        _environment(tmp_path),
        run,
        [{"id": "only", "task": SETTLES_ON_ITS_OWN, "expects_no_diff": True}],
        "--success-hook=",
        "--failure-hook=",
    )
    try:
        yield launched
    finally:
        _just("stop", run, environment=launched.environment, seconds=60)


@pytest.fixture(scope="module")
def driven(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Replying]:
    """A run a live driver holds for as long as the journeys need it.

    Its one node is dispatched, and the worker's turn waits at the stand-in's gate until
    the fixture releases it, so the driver stays alive reconciling the run's channel. One
    run serves every journey that needs a driver, each reading what it changed against
    what the run held before it sent anything.
    """
    _require_just()
    tmp_path = tmp_path_factory.mktemp("channel-reply-driven")
    environment = _environment(tmp_path)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    gate = tmp_path / "turn-gate"
    gate.mkdir()
    environment[TURN_GATE_ENV] = str(gate)
    run = RunId(f"channel-reply-{SUITE}-driven")
    try:
        launched = _launched(
            tmp_path,
            environment,
            run,
            [{"id": HELD_NODE, "persona": "engineer", "task": _task("Report.")}],
            "--detach",
            "--success-hook=",
            "--failure-hook=",
        )
        limit = deadline(300)
        while not (gate / TURN_GATE_REACHED).exists():
            assert time.monotonic() < limit, "the held worker's turn never reached its gate"
            time.sleep(0.5)
        yield launched
    finally:
        (gate / TURN_GATE_RELEASED).touch()
        _just("stop", run, environment=environment, seconds=120)


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


def _journal(replying: Replying) -> list[dict[str, Any]]:
    """Every record the run's own journal holds, oldest first.

    `Any` because the journal is the engine's own record, read unvalidated; each journey
    asserts the members it is about.
    """
    written = replying.root / "events.jsonl"
    if not written.exists():
        return []
    return [
        cast(dict[str, Any], json.loads(line))
        for line in written.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _recorded(replying: Replying, *kinds: str) -> list[dict[str, Any]]:
    """The journal's records of the kinds named, oldest first."""
    return [event for event in _journal(replying) if event.get("kind") in kinds]


#: The journal kinds a reply writes when it is taken: an edit applied or refused once
#: reconciled, and the verdict half's own records.
WRITTEN_BY_A_REPLY = ("edit-committed", "edit-rejected", "planner-replied", "completion-requested")


def _committed(replying: Replying, mentioning: str) -> list[dict[str, Any]]:
    """The journal's `edit-committed` records whose command mentions `mentioning`."""
    return [
        event
        for event in _recorded(replying, "edit-committed")
        if mentioning in json.dumps(event.get("payload", {}).get("command"))
    ]


#: One node's row in `just results`: the node id two spaces in, then a column gap.
NODE_ROW = re.compile(r"^  (\S+)\s{2,}\S")


def _nodes(replying: Replying) -> list[str]:
    """The run's nodes, in the order `just results` renders them."""
    results = _just("results", replying.run, environment=replying.environment, seconds=60)
    assert results.returncode == 0, results.stderr
    return [
        row.group(1) for line in results.stdout.splitlines()[1:] if (row := NODE_ROW.match(line))
    ]


def _judged_turns(replying: Replying) -> int:
    """How many review turns the doubled provider has been asked for so far.

    Read off the attempt log the stand-in appends to per launch — the only moment a
    journey can prove the provider was reached — because a judged turn spent and a pass
    found in the cache look the same from the recipe's own answer.
    """
    log = Path(replying.environment["FAKE_CODEX_ATTEMPT_LOG"])
    return len(log.read_text(encoding="utf-8").splitlines()) if log.is_file() else 0


# `Any` in the envelopes and commands below because an envelope is the channel's open JSON
# contract: its values are whatever JSON each op carries, and the engine, the bus and the
# validator each read the members they own.
def _send(
    replying: Replying,
    envelope: dict[str, Any],
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Send one envelope as a manager types it: piped into `just channel-reply <run>`.

    `environment` replaces the run's own for this one send, which is how a journey scripts
    what the doubled reviewer answers the validator's judged turn, or shortens the wait.
    """
    return _just(
        "channel-reply",
        replying.run,
        *arguments,
        environment=replying.environment if environment is None else environment,
        stdin=json.dumps(envelope),
        seconds=300,
    )


def _receipt(sent: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    """The engine's receipt, which is the whole of a successful send's stdout: one JSON line."""
    assert sent.returncode == 0, f"the reply was refused:\n{sent.stdout}{sent.stderr}"
    printed = sent.stdout.splitlines()
    assert len(printed) == 1, (
        f"a successful reply printed {len(printed)} line(s) where the engine's receipt is "
        f"the whole of the output:\n{sent.stdout}"
    )
    # `cast` rather than a validating read: the engine owns this shape, and each journey
    # asserts the members it is about.
    return cast(dict[str, Any], json.loads(printed[0]))


def _refused(sent: subprocess.CompletedProcess[str]) -> str:
    """Hold a send to a refusal — exit 2 and no receipt — and hand back what it said."""
    assert sent.returncode == REPLY_REFUSED, (
        f"the reply exited {sent.returncode} where a refusal exits {REPLY_REFUSED}:\n"
        f"{sent.stdout}{sent.stderr}"
    )
    assert sent.stdout == "", f"a refused reply printed a receipt anyway: {sent.stdout}"
    return sent.stderr


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


def _adding(node: str, task: str, *deps: str) -> dict[str, Any]:
    """An envelope adding one engineer node, with `task` as its prose."""
    return {
        "version": 3,
        "commands": [
            {
                "op": "add",
                "node": {"id": node, "persona": "engineer", "deps": list(deps), "task": task},
            }
        ],
    }


def _settling(node: str, *deps: str) -> dict[str, Any]:
    """An envelope adding one node that settles without a dispatch, naming no author."""
    return {
        "version": 3,
        "commands": [
            {
                "op": "add",
                "node": {
                    "id": node,
                    "task": SETTLES_ON_ITS_OWN,
                    "expects_no_diff": True,
                    "deps": list(deps),
                },
            }
        ],
    }


def _note(node: str, text: str) -> dict[str, Any]:
    """One `note` command, spelled with the `addressee` the op requires."""
    return {"op": "note", "id": node, "addressee": "worker", "text": text}


def _reaped(started: subprocess.Popen[str]) -> None:
    """End a process group this journey started, whether or not it has already ended."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(os.getpgid(started.pid), signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired, ValueError):
        started.communicate(timeout=e2e_timeout(60))


def _undriven(replying: Replying) -> dict[str, Any]:
    """Wait until the run itself says nothing drives it, and hand back its reading.

    Read off `just status --json`, the engine's one-object reading of the run: `driven` is
    whether anything drives it, `ending` how it ended, and `paused` the decision it waits on.
    The field rather than the printed word, because the word for one standing has moved
    between engine releases while `driven` says the one thing this premise is about.
    """
    limit = deadline(120)
    while True:
        status = _just("status", replying.run, "--json", environment=replying.environment)
        assert status.returncode == 0, f"`just status --json` failed:\n{status.stderr}"
        # `cast` and no validation: the reading is the engine's own document, and each
        # journey asserts the members it is about.
        reading = cast(dict[str, Any], json.loads(status.stdout))
        if reading["driven"] is False:
            return reading
        assert time.monotonic() < limit, f"run {replying.run} is still driven:\n{status.stdout}"
        time.sleep(0.5)


def _unfinished_and_undriven(replying: Replying) -> bool:
    """Whether nothing drives the run and it has not ended — held at its gate, not settled."""
    return _undriven(replying)["ending"] is None


def _asking(replying: Replying, question: str) -> subprocess.Popen[str]:
    """Raise a real blocking question on the run's channel, as a dispatched agent does."""
    environment = dict(replying.environment)
    environment["ONEPIPELINE_RUN_ID"] = replying.run
    return subprocess.Popen(  # noqa: S603 - the real wrapper, as an agent runs it
        [str(ASK_MANAGER), "--timeout", str(ASK_WINDOW_SECONDS), question],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def _question(replying: Replying) -> str:
    """The correlation of the blocking question the run's channel hands a manager next."""
    limit = deadline(120)
    while True:
        assert time.monotonic() < limit, "the shim's question never reached channel-next"
        surface = next_surface_record(replying.run, replying.environment)
        if surface is not None and surface.get("correlation") and surface["blocking"]:
            return str(surface["correlation"])
        time.sleep(0.5)


ANSWER = "Key it on the whole workspace; the narrower key would replay a stale verdict."


def test_a_ruling_binds_to_its_question_and_one_no_pending_question_holds_is_refused(
    driven: Replying,
) -> None:
    """The manager's mistakes a correlation exists to catch, on a run a driver holds.

    The shim asks a real blocking question on the run's channel; the manager reads it
    through `just channel-next`, which hands out the correlation the bus stamped on it, and
    answers with `--correlation`. That answer is the one the asking agent reads back, and
    the engine's one-line receipt is the whole of what the recipe printed. Then three
    refusals, each exit 2 with nothing appended: the same ruling again, whose correlation
    nothing pending holds any more; a ruling naming a correlation nothing ever raised; and
    a commands-only envelope naming a correlation, which has no verdict to answer it with
    and is neither queued nor applied.
    """
    asking = _asking(driven, "Should the test key cover docs?")
    try:
        correlation = _question(driven)

        answered = reply(driven.run, driven.environment, ruling(ANSWER), correlation)

        receipt = _receipt(answered)
        assert receipt["state"] == "delivered" and receipt.get("verdict") == "delivered", receipt
        assert "commands" not in receipt, receipt
        out, err = asking.communicate(timeout=e2e_timeout(120))
        assert asking.returncode == 0, f"the asking agent read no reply:\n{err}{out}"
        read_back = json.loads(out)
        assert read_back["answer"] == "reply", read_back
        assert read_back["reply"]["reply"]["message"] == ANSWER, read_back

        replies, commands = _records(driven, "replies"), _records(driven, "commands")
        written = len(_recorded(driven, *WRITTEN_BY_A_REPLY))
        nodes = _nodes(driven)

        again = _refused(reply(driven.run, driven.environment, ruling(ANSWER), correlation))
        assert correlation in again, f"the refusal does not name the correlation:\n{again}"

        never = f"c-never-raised-{SUITE}"
        unknown = _refused(reply(driven.run, driven.environment, ruling(ANSWER), never))
        assert never in unknown, f"the refusal does not name the correlation:\n{unknown}"

        edit = {"version": 3, "commands": [_note(HELD_NODE, "this note answers nothing")]}
        unbound = _refused(_send(driven, edit, "--correlation", correlation))
        assert "carries no verdict" in unbound, unbound

        assert _records(driven, "replies") == replies, "a refused reply was appended anyway"
        assert _records(driven, "commands") == commands, "a refused edit reached the queue"
        assert len(_recorded(driven, *WRITTEN_BY_A_REPLY)) == written, (
            "a refused reply left a record in the run's journal"
        )
        assert _nodes(driven) == nodes, "a refused reply changed the graph"
    finally:
        _reaped(asking)


#: The amendment this refusal was written from, verbatim as it was sent during the run it
#: cost: the checks it names run on the host after publication, so the agent step it binds
#: has ended before any of them start.
UNSATISFIABLE_AMENDMENT = (
    "The finished branch merges cleanly into its base and its change request's "
    "required checks pass."
)

#: The verdict half that rides beside the refused amendment in its second spelling.
RIDING_VERDICT = {"completion": False, "message": "Amend the bar, then carry on."}


def _refused_whole(replying: Replying, node: str) -> None:
    """Send the unsatisfiable amendment bare and beside a verdict, twice each, and hold all four.

    Each is refused with the validator's own reason and exit 2, and nothing of it reaches
    the run: no record on the command or reply queue, no record in the journal — so no edit
    applied, no verdict delivered, no completion requested — no change to the graph, and no
    judged turn spent on a text the free tier refuses. Nor is any of it remembered as a
    pass: the same envelope sent again is judged again, and refused again for the reason.
    """
    amendment = {"op": "amend", "id": node, "text": UNSATISFIABLE_AMENDMENT}
    bare = {"version": 3, "commands": [amendment]}
    with_verdict = {**bare, **RIDING_VERDICT}
    replies, commands = _records(replying, "replies"), _records(replying, "commands")
    written = len(_recorded(replying, *WRITTEN_BY_A_REPLY))
    nodes = _nodes(replying)
    turns = _judged_turns(replying)

    for envelope in (bare, with_verdict, bare, with_verdict):
        said = _refused(_send(replying, envelope))

        assert f"the amendment for node '{node}'" in said, said
        assert "required checks pass" in said, said

    assert _records(replying, "commands") == commands, "a refused envelope reached the queue"
    assert _records(replying, "replies") == replies, "a refused verdict reached the queue"
    assert len(_recorded(replying, *WRITTEN_BY_A_REPLY)) == written, (
        "a refused envelope left a record in the run's journal"
    )
    assert _nodes(replying) == nodes, "a refused envelope changed the graph"
    assert _judged_turns(replying) == turns, "a text the free tier refuses was put to the judge"


def test_task_prose_the_validator_refuses_is_refused_whole_where_nothing_drives_the_run(
    replying: Replying,
) -> None:
    """The validator the recorded configuration names binds the reply that would apply an edit.

    With nothing driving the run, an accepted edit is applied by the reply itself, so this
    is the path on which a validator that ran after the apply would be no validator at all.
    """
    assert _unfinished_and_undriven(replying), "the premise is a run nothing drives"

    _refused_whole(replying, WORK_NODE)


def test_task_prose_the_validator_refuses_is_refused_whole_on_a_run_a_driver_holds(
    driven: Replying,
) -> None:
    """The same refusal on the queued path: nothing of it reaches the driver's queue."""
    _refused_whole(driven, HELD_NODE)


def _authored_by_the_planner(replying: Replying, node: str) -> str:
    """The edit adding `node` is journalled once, committed, and as the planner's.

    Hands back the journal stream the record was written on, which names the process
    that applied it.
    """
    (committed,) = _committed(replying, node)
    assert committed["payload"]["author"] == "planner", committed
    assert node in _nodes(replying), f"{node} never reached the run's graph"
    return str(committed["stream"])


def _driver_stream(replying: Replying, kind: str) -> str:
    """The journal stream the run's driver wrote its first `kind` record on."""
    return str(_recorded(replying, kind)[0]["stream"])


def test_a_valid_edit_is_applied_by_the_driver_holding_the_run(
    driven: Replying, tmp_path: Path
) -> None:
    """An edit to a driven run is applied by its driver, inside the reply's wait.

    The envelope names no author, so it is the planner's, and it is read from a file named
    on the command line rather than piped in. The reply queues it, because the driver holds
    the run, and the receipt says the commands were applied; the journal's own
    `edit-committed` record says by whom — the planner — and was written by the driver,
    on the stream the driver writes the rest of the run on.
    """
    node = "added-driven"
    envelope = tmp_path / "edit.json"
    envelope.write_text(json.dumps(_settling(node)), encoding="utf-8")
    commands = _records(driven, "commands")

    sent = _just(
        "channel-reply", driven.run, str(envelope), environment=driven.environment, seconds=300
    )

    receipt = _receipt(sent)
    assert receipt["state"] == "applied" and receipt.get("commands") == "applied", receipt
    assert "verdict" not in receipt, receipt
    assert _records(driven, "commands") == commands + 1, "the edit never reached the driver"
    assert _authored_by_the_planner(driven, node) == _driver_stream(driven, "node-dispatched"), (
        "the edit was applied by some process other than the driver holding the run"
    )


def test_a_valid_edit_to_a_run_nothing_drives_is_applied_by_the_reply_itself(
    replying: Replying,
) -> None:
    """With nothing driving the run, the reply is its single writer and applies the edit.

    No adopt, and no queue left waiting for one: the envelope is piped in, the receipt says
    the commands were applied — by this process, so there is no queue id to name and
    nothing is appended to the run's command queue — the journal records the edit as the
    planner's, on a stream other than the one the launch's driver wrote, and the run is
    still one nothing drives.
    """
    assert _unfinished_and_undriven(replying), "the premise is a run nothing drives"
    node = "added-undriven"
    commands = _records(replying, "commands")

    receipt = _receipt(_send(replying, _settling(node, GATE_NODE)))

    assert receipt == {"reply": 0, "state": "applied", "commands": "applied"}, receipt
    assert _records(replying, "commands") == commands, "the edit was queued for a driver"
    assert _authored_by_the_planner(replying, node) != _driver_stream(replying, "node-held"), (
        "the edit was journalled on the launch driver's stream, which had exited"
    )
    assert _unfinished_and_undriven(replying), "the reply left something driving the run"


def test_an_author_the_configuration_does_not_declare_or_grant_is_refused(
    replying: Replying,
) -> None:
    """Who may speak, and what each may ask for, is the recorded configuration's to say.

    The pacemaker is declared nowhere in `config/onemessagebus.yaml`, so an envelope under
    its name is refused for the author. The monitor is declared and is not granted
    `complete`, so an envelope of its asking for one is refused for the op. Neither appends
    anything or leaves a record in the run's journal.
    """
    assert _unfinished_and_undriven(replying), "the premise is a run nothing drives"
    commands = _records(replying, "commands")
    written = len(_recorded(replying, *WRITTEN_BY_A_REPLY))

    undeclared = _refused(
        _send(
            replying,
            {"version": 3, "author": "pacemaker", "commands": [_note(WORK_NODE, "hello")]},
        )
    )
    ungranted = _refused(
        _send(
            replying,
            {
                "version": 3,
                "author": "monitor",
                "commands": [{"op": "complete", "reason": "the monitor thinks it is done"}],
            },
        )
    )

    assert "`pacemaker` is not declared" in undeclared, undeclared
    assert "'complete' is not an op the monitor may issue" in ungranted, ungranted
    assert _records(replying, "commands") == commands, "a refused envelope reached the queue"
    assert len(_recorded(replying, *WRITTEN_BY_A_REPLY)) == written, (
        "a refused envelope left a record in the run's journal"
    )


#: The reply wait the queued journey shortens to, and the margin it allows the recipe, the
#: wrapper and `uv run` around the engine's own wait.
SHORT_REPLY_TIMEOUT_SECONDS = 3
RETURN_MARGIN_SECONDS = 30


def test_an_edit_the_driver_has_not_reconciled_in_time_is_reported_queued(
    driven: Replying,
) -> None:
    """The reply returns `queued` once its wait elapses, rather than hanging.

    A note to the held worker is decided only when that worker's turn takes it, and the
    turn is held at its gate for the life of the run, so the driver holding the run cannot
    reconcile the note within the reply's wait. With the wait shortened through
    `ONEPIPELINE_REPLY_TIMEOUT_SECONDS`, the recipe returns inside that bound, exits 0 with
    a receipt whose `state` is `queued`, and says on stderr that the edit is durable and
    not to be sent again — and the envelope is on the run's command queue under the id its
    receipt named.
    """
    environment = {
        **driven.environment,
        "ONEPIPELINE_REPLY_TIMEOUT_SECONDS": str(SHORT_REPLY_TIMEOUT_SECONDS),
    }
    commands = _records(driven, "commands")
    said = f"a note the held turn has not taken, {SUITE}"

    started = time.monotonic()
    sent = _send(
        driven, {"version": 3, "commands": [_note(HELD_NODE, said)]}, environment=environment
    )
    took = time.monotonic() - started

    receipt = _receipt(sent)
    assert receipt["state"] == "queued" and receipt.get("commands") == "queued", receipt
    assert took >= SHORT_REPLY_TIMEOUT_SECONDS, f"the reply returned after {took:.1f}s"
    assert took < SHORT_REPLY_TIMEOUT_SECONDS + e2e_timeout(RETURN_MARGIN_SECONDS), (
        f"the reply took {took:.1f}s against a {SHORT_REPLY_TIMEOUT_SECONDS}s wait"
    )
    assert "not to be sent again" in sent.stderr, sent.stderr
    assert _records(driven, "commands") == commands + 1, "the queued edit is not on the queue"
    until = json.dumps({"field": "id", "equals": receipt["reply"]})
    streamed = _bus(driven, "subscribe", "commands", "--until", until, "--timeout", "30")
    assert said in streamed.splitlines()[-1], f"the queue holds no such edit:\n{streamed}"


def _stop_hook(replying: Replying) -> subprocess.CompletedProcess[str]:
    """Run the one `Stop` command `.claude/settings.json` registers, as the harness runs it.

    Through a shell, with `CLAUDE_PROJECT_DIR` naming this checkout and a Stop payload on
    standard input naming the launching session, under the bound the settings file gives.
    """
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    (registered,) = [
        hook
        for matcher in settings["hooks"]["Stop"]
        for hook in matcher["hooks"]
        if hook["type"] == "command"
    ]
    payload = {
        "session_id": LAUNCHING_SESSION,
        "transcript_path": "/dev/null",
        "hook_event_name": "Stop",
        "stop_hook_active": False,
    }
    return subprocess.run(  # noqa: S603 - the tracked hook command, run as the harness runs it
        ["/bin/sh", "-c", str(registered["command"])],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        env={**replying.environment, "CLAUDE_PROJECT_DIR": str(REPO_ROOT)},
        cwd=REPO_ROOT,
        timeout=e2e_timeout(int(registered["timeout"])),
    )


def _owed(replying: Replying) -> None:
    """The session owes the run: `just unfinished` lists it, and the Stop hook blocks on it."""
    unfinished = _just("unfinished", environment=replying.environment, seconds=120)
    assert replying.run in unfinished.stdout, (
        f"`just unfinished` does not list the settled run, so nothing here is owed:\n"
        f"{unfinished.stdout}{unfinished.stderr}"
    )
    hooked = _stop_hook(replying)
    assert hooked.returncode == 0, f"the hook failed:\n{hooked.stdout}{hooked.stderr}"
    decision = json.loads(hooked.stdout)
    assert decision.get("decision") == "block", f"the hook let the turn end: {hooked.stdout}"
    assert replying.run in decision["reason"], f"the hook did not name the run: {decision}"


def _no_longer_owed(replying: Replying) -> None:
    """Neither `just unfinished` nor the Stop hook holds the session to the run any more."""
    unfinished = _just("unfinished", environment=replying.environment, seconds=120)
    assert replying.run not in unfinished.stdout + unfinished.stderr, (
        f"`just unfinished` still lists the run:\n{unfinished.stdout}{unfinished.stderr}"
    )
    hooked = _stop_hook(replying)
    assert hooked.returncode == 0, f"the hook failed:\n{hooked.stdout}{hooked.stderr}"
    assert replying.run not in hooked.stdout + hooked.stderr, (
        f"the Stop hook still holds the session to the run:\n{hooked.stdout}{hooked.stderr}"
    )


def _completion_requested(replying: Replying, reason: str) -> None:
    """The run's journal records the completion, with the reason the reply carried."""
    requested = _recorded(replying, "completion-requested")
    assert [event["payload"].get("reason") for event in requested] == [reason], requested


def test_a_complete_command_closes_a_settled_run_whose_driver_has_exited(
    settled: Replying,
) -> None:
    """`complete` is the versioned spelling of the verdict that closes a run, and it closes one.

    The run settled and its attached driver exited, so it is owed: `just unfinished` lists
    it and the Stop hook refuses to end the turn naming it. Nothing is pending on its
    channel. The `complete` command, sent through the recipe with a reason, is applied by
    the reply itself, journalled as the completion the reason states, and the session owes
    the run nothing afterwards — with nothing adopting, acknowledging, stopping or watching
    the run in between.
    """
    assert _undriven(settled)["word"] == "SETTLED", "the premise is a settled run"
    _owed(settled)
    reason = "every node settled done and the report is on the issue"

    receipt = _receipt(
        _send(settled, {"version": 3, "commands": [{"op": "complete", "reason": reason}]})
    )

    assert receipt["state"] == "applied" and receipt.get("commands") == "applied", receipt
    _completion_requested(settled, reason)
    _no_longer_owed(settled)


def test_a_completion_verdict_closes_a_settled_run_whose_driver_has_exited(
    settled: Replying,
) -> None:
    """The verdict a manager is told to close a settled run with, with no ask pending.

    The same before and after as the `complete` command above, through an envelope carrying
    a verdict and nothing else: the receipt says the verdict was delivered — to the run's
    own record, since no question is pending for it to answer.
    """
    assert _undriven(settled)["word"] == "SETTLED", "the premise is a settled run"
    _owed(settled)
    reason = "verified: every node settled done and nothing is left to land"

    receipt = _receipt(_send(settled, {"completion": True, "reason": reason}))

    assert receipt["state"] == "delivered" and receipt.get("verdict") == "delivered", receipt
    assert "commands" not in receipt, receipt
    _completion_requested(settled, reason)
    _no_longer_owed(settled)


#: What the doubled reviewer finds in a task it refuses, in the verdict schema's shape. The
#: `why` is distinctive so the refusal can be read for the reviewer's own words.
JUDGED_FINDING = {
    "criterion": "- The finished tree carries an assertion whose subject is that behaviour",
    "why": "names no journey a reader could run to watch that assertion fail",
}


def test_a_novel_task_the_judged_review_refuses_is_refused_whole_with_its_findings(
    replying: Replying,
) -> None:
    """The judged tier binds through the reply as the deterministic one does.

    A novel whole task clears the free tier and is put to one judged turn, and here the
    reviewer refuses it. Nothing is applied: the recipe exits refused with the review's
    finding on stderr, and the run journals no edit. And the refusal is not remembered as
    a pass: the same envelope, sent again to a reviewer that passes it, is judged again
    before it is applied.
    """
    envelope = _adding("reviewed", _novel_task(replying, "refund"), GATE_NODE)
    environment = {
        **replying.environment,
        "FAKE_CODEX_ANSWERS": json.dumps(
            [json.dumps({"passes": False, "findings": [JUDGED_FINDING]})]
        ),
    }

    said = _refused(_send(replying, envelope, environment=environment))

    assert "was refused by its judged review" in said, said
    assert JUDGED_FINDING["why"] in said, said
    assert _judged_turns(replying) == 1, "the refusal did not come from one judged turn"
    assert not _committed(replying, "reviewed"), "a refused envelope was applied"

    passed = _receipt(_send(replying, envelope))

    assert passed["state"] == "applied", passed
    assert _judged_turns(replying) == 2, "the refused envelope was passed without being judged"
    assert len(_committed(replying, "reviewed")) == 1


def test_a_novel_task_whose_judged_turn_answers_nothing_is_never_applied(
    replying: Replying,
) -> None:
    """No verdict is not a pass: the validator exits unjudged, and nothing is applied.

    Every launch of the doubled reviewer dies after it starts, so the review chain ends
    with no verdict at all. `orchestrator.envelope_review` exits unjudged rather than
    reading that either way, and the reply holds an unjudged envelope back exactly as it
    holds a refused one: nothing is applied, and the recipe says the prose could not be
    judged, not that it was refused. Nor is anything remembered: the same envelope, sent
    again once the reviewer answers, is judged before it is applied.
    """
    envelope = _adding("unjudged", _novel_task(replying, "invoice"), GATE_NODE)
    environment = {**replying.environment, "FAKE_CODEX_UNAVAILABLE_ATTEMPTS": "1000"}

    said = _refused(_send(replying, envelope, environment=environment))

    assert "could not be judged" in said, said
    assert "was refused" not in said, said
    reached = _judged_turns(replying)
    assert reached >= 1, "the reviewer was never reached, so nothing was tested"
    assert not _committed(replying, "unjudged"), "an unjudged envelope was applied"

    answered = _receipt(_send(replying, envelope))

    assert answered["state"] == "applied", answered
    assert _judged_turns(replying) == reached + 1, (
        "the unjudged envelope was passed without being judged once the reviewer answered"
    )


def test_an_identical_envelope_under_an_unchanged_bar_passes_from_the_cache(
    replying: Replying, settled: Replying
) -> None:
    """One judged turn per envelope under one bar, whatever number of times it is sent.

    A novel whole task spends the judged turn once, and its pass is recorded under
    `config/onemessagebus.yaml`'s cache, which is this checkout's and not any run's. The
    same envelope sent again under the same bar — to a second run, where the node it adds
    is new — is passed from that record: the doubled reviewer is not asked again, while
    both sends are applied. What a manager sees of the cache is exactly that, a second send
    that costs no turn, so that is what is asserted, and nothing under the cache directory
    is read. Its record is keyed on content unique to this journey, so no other send can
    ever be passed by it.
    """
    envelope = _adding("cached", _novel_task(replying, "route"))

    first = _receipt(_send(replying, envelope))

    assert first["state"] == "applied", first
    assert _judged_turns(replying) == 1, "the first send did not spend exactly one turn"

    second = _receipt(_send(settled, envelope, environment=replying.environment))

    assert second["state"] == "applied", second
    assert _judged_turns(replying) == 1, (
        "the identical envelope under an unchanged bar was judged again rather than "
        "passed from the record the first send left"
    )
    assert len(_committed(replying, "cached")) == 1
    assert len(_committed(settled, "cached")) == 1
