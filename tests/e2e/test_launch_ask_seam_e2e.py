"""Every launch this repository makes hands its dispatches the seam an agent asks through.

`scripts/ask-manager.sh` is the one supported way for a dispatched agent to put a
blocking question to its manager, and `personas/planner.yaml` tells an agent to run the
command `$ORCHESTRATOR_ASK_MANAGER` names. A launch that establishes nothing there does
not fail — the agent runs the empty string, or asks a run it is not under — so the seam
is only as good as the environment each launch shape builds, and that environment is
invisible from everywhere but inside a dispatch: a variable reaches one by inheritance
through `onepipeline`, `oneagentgraph`, and `oneharness`, and none of them reports what
it passed on.

So every journey here launches for real — through the real recipe, the real
`scripts/onepipeline.sh`, the real driver, and this host's real `graphs/` — and reads
what a dispatch was given out of the dispatch's own turn. Two of them go further and
have the dispatch *run* the wrapper, because a variable holding a runnable path is not
the same claim as an agent getting an answer back. `tests/e2e/fake_backend.py` stands in
for the paid model alone, and asks with the environment its own turn inherited.

**Journeys here are of two kinds, and they are held to different bars.** A *defect
journey* covers a launch shape that was broken when it was written — every
`just orchestrate` shape, which exported the seam nowhere, and the detached `just plan`
ask, which refused with `ONEPIPELINE_RUN_ID is not set` — and each was observed failing
for that reason before the fix. A *regression guard* covers a shape that already
worked: the attached `just plan` ask, which is the one shape this host had ever proven,
and which is why nothing noticed the rest. Each journey below says which it is; asking a
regression guard to fail first would be asking for the impossible.

Handing a worker its run id is `onepipeline`'s own to do for a `just orchestrate`
launch, whose plan this repository did not write — so what is asserted of those three
shapes is that the id a dispatch was given names **the one run that launch created**,
read off its own runs root. Deriving the id from the plan's `name` instead would be
restating how a run id is minted, which is that repository's to decide and is asserted
nowhere here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple, NewType, TypedDict, cast

import pytest
from fake_backend import (
    ASK_QUESTION_ENV,
    ASK_RECORD_ENV,
    DISPATCHED_MEMBER,
    ENVIRONMENT_KEYS_ENV,
    MEMBER_OF_CONFIG,
    PROMPT_LOG_ENV,
)
from planner_channel import PersistentManager, just, ruling
from scratch_identity import seeded
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The stand-in for the paid model, and the provider binary beneath it — the second is
#: what covers a single-sided member, which runs oneharness in process and so spawns no
#: CLI for the first to be.
FAKE_BACKEND = Path(__file__).resolve().parent / "fake_backend.py"
FAKE_CODEX = Path(__file__).resolve().parent / "fake_codex.py"

#: A launching session these journeys state rather than inherit, and everything else an
#: enclosing dispatch would otherwise decide for them. `ONEPIPELINE_RUN_ID` is the one
#: that matters most: this suite runs inside a dispatch of its own, so a journey that
#: kept it would read the *enclosing* run's id back and call it a launch's doing.
LAUNCHING_SESSION = "e2e-launch-ask-seam"
INHERITED_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "ORCHESTRATOR_ASK_MANAGER",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: A run's own name on the ledger. Distinguished from the prose it is built out of,
#: because what makes a string a run id is where it came from.
RunId = NewType("RunId", str)


class Input(NamedTuple):
    """One thing `scripts/ask-manager.sh` reads before it can ask, and why it needs it."""

    name: str
    why: str


#: Every input the wrapper requires of the environment a launch builds. Named here
#: rather than left implicit so a launch path that stops providing one fails in this
#: file — where the failure says which input and which launch shape — instead of at some
#: agent's first blocking question, which is where all three of this module's defects
#: were finally found.
ASK_WRAPPER = Input(
    "ORCHESTRATOR_ASK_MANAGER",
    "the command an agent runs to ask; the persona tells it to run exactly this, so an "
    "unset one expands to the empty string and the agent has no recourse",
)
RUN_ID = Input(
    "ONEPIPELINE_RUN_ID",
    "the run whose channel the question goes to; the wrapper refuses rather than "
    "guessing at one, so an unset one is a question that is never asked",
)
REQUIRED_INPUTS = (ASK_WRAPPER, RUN_ID)

#: The question a dispatched agent puts, and the answer its manager gives. Compared
#: whole, because what the wrapper owes a caller is the manager's message and nothing
#: else — a wrapper that handed back the wire object would leave every agent parsing
#: JSON out of what was supposed to be an answer.
ASK_QUESTION = "Should the cursor be an opaque token or a node id?"
ANSWER = "An opaque token; the node id would leak the ordering."

#: How long a journey waits for a launch to reach a dispatched turn. Load-scaled like
#: every other hang guard here, and the largest of them because reaching a turn is the
#: part a loaded host slows most.
DISPATCH_SECONDS = 300

#: How long the manager keeps looking for the question. Its own bound rather than the
#: one above, because it is armed after the dispatch has begun: what it waits for is one
#: round trip through the channel, not a run getting under way.
MANAGER_SECONDS = 180

#: How long a journey waits for the dispatch's own ask to finish. Deliberately outside
#: the manager's patience: an ask that has not finished by then has a manager who has
#: already given up, and their account of why says what a deadline of this one's own
#: never could — which is exactly what a shorter wait cost the first time this ran under
#: a full suite.
ANSWERED_SECONDS = MANAGER_SECONDS * 2

#: The reply window the dispatched wrapper is given, load-scaled like the two above and
#: the longest of the three. That ordering is the point: the manager gives up first, the
#: journey notices second, and the wrapper's own timeout is last, so what a failure
#: reports is why nobody answered rather than that nobody did. A fixed window here — the
#: one thing on this page that did not scale — is what reported the wrapper's timeout the
#: first time this ran under a full suite.
ASK_WINDOW_SECONDS = int(e2e_timeout(ANSWERED_SECONDS * 2))

#: One live launch per journey, each with a manager thread beside it, so they are pinned
#: to one xdist worker: several of these racing the rest of a full suite is what turns a
#: round trip through real recipes into one that outlives the window it was given.
#:
#: `tests/e2e/test_ask_manager_e2e.py`'s group, deliberately, rather than one of this
#: module's own. Two groups is two workers, and everything in both is a live planner
#: channel with a manager thread driving real recipes at it — measured, a suite running
#: the two beside each other left that module's wrapper waiting past its own deadline.
#: What has to be serialized is driving a channel, not driving this file's channels.
LAUNCH_GROUP = "ask-manager-channel"

#: The two checkouts the node `just plan` writes names, as `scripts/plan.sh` defaults
#: them. Each launch below seeds a scratch pair under exactly these names, so the
#: recipe's own defaults resolve against a registry of the journey's own.
PUBLICATION_ALIAS = "ai-orchestrator"
EXECUTION_ALIAS = "ai-orchestrator-isolated"

#: The brief a `just plan` launch is made from. Written to a temporary directory rather
#: than taken from `examples/`, so these journeys read none of this repository's prose
#: and stay in the code-only test tier.
BRIEF = """## What
Decide whether the paginated listing's cursor is an opaque token or a node id.

## Why
The browser view cannot deep-link to a page until that is settled.

## Acceptance criteria
- The cursor's shape and its type are stated.
"""

#: What the two `just orchestrate` journeys that only read an environment launch without.
#: The observer graph watches a run for its whole life, and every turn it takes is
#: another dispatch of the paid model's stand-in — real cost for a claim it has no
#: bearing on, since what a *worker* is given is settled by the launching process rather
#: than by who is watching.
#:
#: The `just plan` journeys below name none either, and not by a choice of their own:
#: that recipe launches on `--dag-graph off`, because a planning run's output is the very
#: plan a monitor would be comparing it against. So every ask measured here is asked on an
#: unwatched run — which is the shape a planner is actually launched in, and is a claim
#: about the channel in its own right: a blocking question is served by `onepipeline`
#: itself, and an answer never had to get past an observer to reach the asker.
#:
#: Dropping it used to change the environment as well — below onepipeline 0.8.1 the run
#: id reached a dispatch only by leaking out of an *attached* driver's own process after
#: it had started an observer there, so an unwatched launch dispatched a worker that
#: could not ask. That is why the two journeys below launch without one and still measure
#: a run id: on the adopted release the dispatch site composes it, so what carries it is
#: no longer who is watching.
WITHOUT_OBSERVER = ("--dag-graph", "off")

#: Where `scripts/plan.sh` writes what it generates, relative to this checkout.
PLAN_DIRECTORY = REPO_ROOT / "scratch" / "plans"

#: This repository's one entry point to `onepipeline`, used here for the one verb no
#: recipe wraps: completing the human action an adopted run is parked on.
ONEPIPELINE = REPO_ROOT / "scripts" / "onepipeline.sh"


class PlanNode(TypedDict, total=False):
    """One node of a plan these journeys launch, in the fields they give it.

    `total=False` because a plan node is a small union: an agent node carries a
    `persona`, a human action carries `kind`, and only some carry `deps`. Stated rather
    than left as a bare mapping so a launch that has to be *shaped* right — the engine
    refuses a plan it cannot load, and a refused plan dispatches nothing to measure — is
    checked here rather than at the launch.
    """

    id: str
    persona: str
    kind: str
    task: str
    deps: list[str]


class TurnRecord(TypedDict):
    """One recorded harness turn. `tests/e2e/fake_backend.py` owns this schema."""

    config: str | None
    environment: dict[str, str | None]


class AskRecord(TypedDict):
    """What the wrapper answered a dispatched agent. `tests/e2e/fake_backend.py` writes it."""

    wrapper: str | None
    status: int | None
    out: str
    err: str


class Dispatch(NamedTuple):
    """One launch, in what a journey here reads off it."""

    run: RunId
    #: Every turn that served the dispatched node's own member, either side of it.
    worker: list[TurnRecord]
    #: What the dispatch got back from its manager, for the journeys that asked.
    asked: AskRecord | None
    #: The environment that launch ran in, so a journey can launch beside it — the
    #: ledger it wrote into is what makes its run id one another launch would collide
    #: with, and a journey that built its own would collide with nothing.
    environment: dict[str, str]


def _environment(
    tmp_path: Path,
    oneharness_bin: str,
    turns: Path,
    *,
    record: Path | None = None,
) -> dict[str, str]:
    """The environment one launch runs in, and the evidence it is asked to leave."""
    environment = dict(os.environ)
    for name in INHERITED_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    environment[PROMPT_LOG_ENV] = str(turns)
    environment[ENVIRONMENT_KEYS_ENV] = ",".join(required.name for required in REQUIRED_INPUTS)
    if record is not None:
        environment[ASK_QUESTION_ENV] = ASK_QUESTION
        environment[ASK_RECORD_ENV] = str(record)
        environment["ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS"] = str(ASK_WINDOW_SECONDS)
    return environment


def _node(node_id: str, deps: list[str] | None = None) -> PlanNode:
    """One agent node, written in the template every task this repository dispatches uses."""
    node: PlanNode = {
        "id": node_id,
        "persona": "engineer",
        "task": "## What\nReport, changing nothing.\n\n## Why\nThe environment the "
        "dispatch was given is the subject, not the work.\n\n"
        "## Acceptance criteria\n- Reported.",
    }
    if deps is not None:
        node["deps"] = deps
    return node


def _gate(node_id: str) -> PlanNode:
    """The human action a run parks on, so there is an intact ledger to adopt."""
    return {
        "id": node_id,
        "kind": "human",
        "task": "## What\nApprove.\n\n## Why\nPark the run so it can be adopted.\n\n"
        "## Acceptance criteria\n- Approved.",
    }


def _plan(tmp_path: Path, run: RunId, tasks: list[PlanNode]) -> Path:
    """Write one plan document for a launch to run."""
    written = tmp_path / f"{run}.plan.json"
    written.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "goal": {"text": "record what a dispatch of this launch shape was given"},
                "name": run,
                "tasks": tasks,
            }
        ),
        encoding="utf-8",
    )
    return written


def _recorded(turns: Path) -> list[TurnRecord]:
    """Every harness turn recorded so far, as the fake backend wrote them.

    A partially written last line is dropped rather than raised on: this is read while
    the run is still going, and a record is appended by a process nobody is synchronized
    with, so the tail can be half a line at exactly the moment a poll reads it.
    """
    if not turns.is_file():
        return []
    found = []
    for line in turns.read_text(encoding="utf-8").splitlines():
        try:
            found.append(cast(TurnRecord, json.loads(line)))
        except json.JSONDecodeError:
            continue
    return found


def _worker_turns(turns: Path) -> list[TurnRecord]:
    """Every recorded turn that served a dispatched node's member, either side of it.

    Read from the member's own scratch path, which is what says whose turn a record is:
    the dag-scope monitor reaches the same backend, and a journey that counted its turns
    as a dispatch's would prove the launch gave the seam to the wrong process.
    """
    found = []
    for turn in _recorded(turns):
        named = MEMBER_OF_CONFIG.search(turn["config"] or "")
        if named is not None and named.group(1) == DISPATCHED_MEMBER:
            found.append(turn)
    return found


def _await_dispatch(turns: Path, *, seconds: float = DISPATCH_SECONDS) -> list[TurnRecord]:
    """Wait until the launch has dispatched a node, and hand back that dispatch's turns."""
    limit = deadline(seconds)
    while time.monotonic() < limit:
        dispatched = _worker_turns(turns)
        if dispatched:
            return dispatched
        time.sleep(0.5)
    raise AssertionError(f"no node was dispatched within {seconds}s, so {turns} holds no dispatch")


def _await_answer(
    record: Path, manager: PersistentManager, *, seconds: float = ANSWERED_SECONDS
) -> AskRecord:
    """Wait for the dispatch's own ask to finish, and hand back what it got.

    The file is created to claim the ask and written when it ends, so a poll waits for it
    to be non-empty: between the two is the whole blocking round trip.

    The manager is watched alongside it, because they are the other half of that round
    trip and only one of the two failures is visible from the file: a manager who died
    reading the channel leaves the wrapper blocking for its whole reply window, and
    waiting that out reports a timeout in place of the reason there was nobody to answer.
    """
    limit = deadline(seconds)
    while time.monotonic() < limit:
        if record.is_file() and record.stat().st_size:
            return cast(AskRecord, json.loads(record.read_text(encoding="utf-8")))
        stopped = manager.failure()
        if stopped is not None:
            raise AssertionError(
                f"the manager stopped before the dispatch's question was answered: {stopped}"
            )
        time.sleep(0.5)
    raise AssertionError(f"the dispatch never finished asking, so {record} is empty or absent")


def _await_run(run: RunId, environment: dict[str, str], *, seconds: float = 120) -> None:
    """Wait until the launch has a run to read surfaces on, before anyone starts reading.

    A manager arms their watch on a run id the launch has printed; a thread started at the
    same instant as the launch has no run to name yet, and `just channel-next` refuses one
    it cannot find. That refusal is not an empty queue and is deliberately raised rather
    than polled through — so the wait belongs here, before the manager exists, instead of
    being softened into the reader that also has to notice a channel going wrong.
    """
    root = Path(environment["ONEPIPELINE_RUNS_DIR"]) / run
    limit = deadline(seconds)
    while time.monotonic() < limit:
        if root.is_dir():
            return
        time.sleep(0.2)
    raise AssertionError(f"the launch recorded no run at {root} within {seconds}s")


def _answering(run: RunId, environment: dict[str, str]) -> PersistentManager:
    """The manager, answering this run's questions for as long as the journey needs.

    Armed once the dispatch has begun rather than at the launch, because a manager's
    patience is a bound on how long they wait for a *question* — and a loaded host can
    spend most of one getting a run as far as dispatching. Measured: under a full suite
    the whole window went on reaching the turn, and the manager gave up at the moment the
    question they were waiting for was finally being asked.

    They keep answering until this journey says the asker has it, and that is measured
    rather than belt-and-braces: on a watched run the monitor reads the same channel and
    claimed the answer, and a wrapper that receives nothing asks again for nothing —
    it re-asks only on a ruling it can see is somebody else's. One answer, sent once, is
    therefore one throw of a race.
    """
    _await_run(run, environment)
    return PersistentManager(
        run,
        environment,
        lambda token: ruling(f"{ANSWER} {token}"),
        seconds=MANAGER_SECONDS,
    )


def _reaped(manager: PersistentManager, answered: AskRecord) -> None:
    """End the watch, and re-raise whatever the manager hit while it kept it.

    Stopped rather than waited out: the answer has landed, or the ask was refused before
    it was ever put. A manager stopped before answering anything is not a failing manager
    — a wrapper that refused its own invocation raises no surface at all, and that
    refusal is the wrapper's own sentence to report rather than a deadline in place of
    it, which is why the reap below only insists when the ask got through.
    """
    manager.stop()
    if answered["status"] == 0:
        manager.checked(asker_said=answered["err"])


def _requires_just() -> None:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")


@contextmanager
def _attached(recipe: list[str], environment: dict[str, str], streamed: Path) -> Iterator[Path]:
    """Run one attached launch beside the journey, with its stream going to a file.

    A file rather than a pipe, and that is not tidiness: an attached launch streams the
    run's whole merged event feed, and a pipe nobody is draining fills and wedges the
    launch — which stops the run this journey is measuring, at whatever point the buffer
    happened to fill. Yielding the path keeps the stream available to a failure message.

    Killed on the way out rather than waited for. What these journeys measure has already
    happened by then, and an attached launch returns only when the run settles, which for
    a run parked on somebody's blocking question can be much later.
    """
    with streamed.open("w", encoding="utf-8") as sink:
        launch = subprocess.Popen(  # noqa: S603 - the real recipe, launched as an operator does
            recipe,
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
        try:
            yield streamed
        finally:
            launch.kill()
            launch.wait(timeout=e2e_timeout(60))


@pytest.fixture(scope="module")
def orchestrate_attached(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Dispatch:
    """Launch `just orchestrate` attached, and have its dispatch ask its manager for real.

    The launch is a subprocess rather than a blocking call because a manager has to be
    playing beside it: the dispatched agent's question holds the run at
    `awaiting-planner`, which is one of the states an attached launch returns on, so the
    two are live at the same time exactly as they are for an operator watching a run.
    """
    _requires_just()
    tmp_path = tmp_path_factory.mktemp("orchestrate-attached")
    run = RunId("launch-seam-orchestrate-attached")
    turns, record = tmp_path / "turns.jsonl", tmp_path / "asked.json"
    environment = _environment(tmp_path, oneharness_bin, turns, record=record)
    plan = _plan(tmp_path, run, [_node("only")])

    try:
        with _attached(["just", "orchestrate", str(plan)], environment, tmp_path / "launch.log"):
            dispatched = _await_dispatch(turns)
            manager = _answering(run, environment)
            answered = _await_answer(record, manager)
            _reaped(manager, answered)
            return Dispatch(run=run, worker=dispatched, asked=answered, environment=environment)
    finally:
        just("stop", run, environment=environment, seconds=60)


@pytest.fixture(scope="module")
def orchestrate_detached(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Dispatch:
    """Launch `just orchestrate --detach` and read what the driver it left behind dispatched.

    The whole point of this shape: the dispatch is made by the `drive-run` process the
    launch spawns and then returns away from, so what it inherits is what the launching
    process exported and nothing a foreground attach could add afterwards.
    """
    _requires_just()
    tmp_path = tmp_path_factory.mktemp("orchestrate-detached")
    run = RunId("launch-seam-orchestrate-detached")
    turns = tmp_path / "turns.jsonl"
    environment = _environment(tmp_path, oneharness_bin, turns)
    plan = _plan(tmp_path, run, [_node("only")])

    launched = just(
        "orchestrate",
        str(plan),
        "--detach",
        *WITHOUT_OBSERVER,
        environment=environment,
        seconds=120,
    )
    try:
        assert launched.returncode == 0, f"the launch failed:\n{launched.stdout}{launched.stderr}"
        return Dispatch(run=run, worker=_await_dispatch(turns), asked=None, environment=environment)
    finally:
        just("stop", run, environment=environment, seconds=60)


@pytest.fixture(scope="module")
def orchestrate_adopted(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Dispatch:
    """Park a run on a human action, adopt it, and read what the fresh driver dispatched.

    The plan's frontier is a human gate, which is what makes this shape reachable at all:
    the detached launch dispatches nothing and leaves an intact ledger with nothing
    driving it, which is the state `--adopt` is for. The gate is attested before the
    adoption rather than after, because a launch returns as soon as the run is parked on
    it — so an attestation sent afterwards lands on a run nothing is driving again.

    Every dispatched turn read below therefore happened under the adopted driver, which
    is the claim: adoption is a launch too, and a launch that established nothing would
    hand the work it resumes an agent that cannot ask.
    """
    _requires_just()
    tmp_path = tmp_path_factory.mktemp("orchestrate-adopted")
    run = RunId("launch-seam-orchestrate-adopted")
    turns = tmp_path / "turns.jsonl"
    environment = _environment(tmp_path, oneharness_bin, turns)
    plan = _plan(tmp_path, run, [_gate("gate"), _node("work", deps=["gate"])])

    launched = just(
        "orchestrate",
        str(plan),
        "--detach",
        *WITHOUT_OBSERVER,
        environment=environment,
        seconds=120,
    )
    try:
        assert launched.returncode == 0, f"the launch failed:\n{launched.stdout}{launched.stderr}"
        _attest(run, "gate", environment)
        adopted = just("orchestrate", "--adopt", run, environment=environment, seconds=300)
        assert adopted.returncode == 0, f"the adoption failed:\n{adopted.stdout}{adopted.stderr}"
        return Dispatch(run=run, worker=_await_dispatch(turns), asked=None, environment=environment)
    finally:
        just("stop", run, environment=environment, seconds=60)


def _attest(
    run: RunId, reference: str, environment: dict[str, str], *, seconds: float = 120
) -> None:
    """Complete the run's waiting human action, retrying until it is one that can be.

    Retried rather than waited out with a sleep: only an action recorded as `waiting` can
    be attested, and how long a detached launch takes to record it is a property of a
    loaded host. The refusal it gives before then is what this polls on.
    """
    limit = deadline(seconds)
    refusal = ""
    while time.monotonic() < limit:
        attested = subprocess.run(  # noqa: S603 - this repository's own onepipeline entry point
            [str(ONEPIPELINE), "attest", run, reference],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )
        if attested.returncode == 0:
            return
        refusal = attested.stderr + attested.stdout
        time.sleep(0.5)
    raise AssertionError(f"run {run}'s '{reference}' never became attestable:\n{refusal}")


def _planned(tmp_path: Path, oneharness_bin: str, run: RunId, *detached: str) -> Dispatch:
    """Launch `just plan` on a brief, have its dispatch ask, and hand back both.

    Attached and detached differ by one flag and by nothing else here, so they are one
    function: what they are being compared on is the environment each leaves behind, and
    a second copy of the launch would be a second chance for the two to differ for a
    reason that is not the flag.
    """
    turns, record = tmp_path / "turns.jsonl", tmp_path / "asked.json"
    environment = _environment(tmp_path, oneharness_bin, turns, record=record)
    # The plan this recipe writes is a lifecycle node naming the two checkouts it
    # defaults to, so the launch opens a real `onevcs` session — and it may never be
    # this host's, whose registry a session reclaims run roots under. Seeded under those
    # two alias names rather than overridden per launch: what these journeys drive is
    # the recipe as an operator types it, and a `--repo` here would be proving a flag.
    environment["ONEVCS_HOME"] = str(
        seeded(tmp_path, publication=PUBLICATION_ALIAS, execution=EXECUTION_ALIAS).home
    )
    brief = tmp_path / f"{run}.md"
    brief.write_text(BRIEF, encoding="utf-8")
    generated = PLAN_DIRECTORY / f"{run}.plan.json"

    recipe = ["just", "plan", str(brief), "--name", run, *detached]
    try:
        with _attached(recipe, environment, tmp_path / "launch.log") as streamed:
            dispatched = _await_dispatch(turns)
            manager = _answering(run, environment)
            answered = _await_answer(record, manager)
            _reaped(manager, answered)
            assert (Path(environment["ONEPIPELINE_RUNS_DIR"]) / run).is_dir(), (
                f"`just plan` printed and exported run '{run}', which is not a run this "
                f"launch created:\n{streamed.read_text(encoding='utf-8')}"
            )
            return Dispatch(run=run, worker=dispatched, asked=answered, environment=environment)
    finally:
        just("stop", run, environment=environment, seconds=60)
        generated.unlink(missing_ok=True)


@pytest.fixture(scope="module")
def plan_attached(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Dispatch:
    """Launch `just plan` attached — the one shape this host had ever proven."""
    _requires_just()
    return _planned(
        tmp_path_factory.mktemp("plan-attached"),
        oneharness_bin,
        RunId("launch-seam-plan-attached"),
    )


@pytest.fixture(scope="module")
def plan_detached(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Dispatch:
    """Launch `just plan --detach` — the shape this host actually launches a planner in."""
    _requires_just()
    return _planned(
        tmp_path_factory.mktemp("plan-detached"),
        oneharness_bin,
        RunId("launch-seam-plan-detached"),
        "--detach",
    )


def _given(dispatch: Dispatch, required: Input) -> str:
    """The one value every turn of this dispatch was given for a required input."""
    assert dispatch.worker, f"run {dispatch.run} dispatched nothing, so nothing here is measured"
    carried = {turn["environment"].get(required.name) for turn in dispatch.worker}
    assert len(carried) == 1, (
        f"a dispatch of run {dispatch.run} was given more than one {required.name}: "
        f"{sorted(str(value) for value in carried)}"
    )
    value = carried.pop()
    assert value is not None, (
        f"{required.name} never reached a dispatch of run {dispatch.run}, and it is {required.why}"
    )
    return value


@pytest.mark.xdist_group(LAUNCH_GROUP)
@pytest.mark.parametrize(
    "shape", ["orchestrate_attached", "orchestrate_detached", "orchestrate_adopted"]
)
def test_every_orchestrate_launch_gives_its_dispatch_a_wrapper_it_can_run(
    shape: str, request: pytest.FixtureRequest
) -> None:
    """A defect journey, for all three: `just orchestrate` exported the seam nowhere.

    This is the launch path that runs every real plan on this host, and until the export
    moved into `scripts/onepipeline.sh` no dispatch of one had ever been given the seam —
    so a worker reading its persona's instruction to ask found the variable empty, with
    nothing to fall back on and nothing to report it to. Before the fix each of these
    three fails here with the variable unset.

    The path is checked to be *runnable* rather than merely present: a name an agent
    cannot execute is the same failure one step later, and the persona hands that string
    straight to a shell.
    """
    dispatch = cast(Dispatch, request.getfixturevalue(shape))
    wrapper = _given(dispatch, ASK_WRAPPER)
    assert os.access(wrapper, os.X_OK), (
        f"a dispatch of run {dispatch.run} was given {wrapper} to ask through, which is "
        f"not runnable"
    )


@pytest.mark.xdist_group(LAUNCH_GROUP)
@pytest.mark.parametrize(
    "shape", ["orchestrate_attached", "orchestrate_detached", "orchestrate_adopted"]
)
def test_every_orchestrate_launch_gives_its_dispatch_the_run_it_is_under(
    shape: str, request: pytest.FixtureRequest
) -> None:
    """A defect journey for two of the three: only an attached launch used to carry one.

    The wrapper above is half a seam. Without a run id it refuses rather than guessing,
    so the other half is this — and below onepipeline 0.8.1 a dispatch got it only by
    accident of process: an attached driver started its observer in its own process and
    the export leaked into every dispatch it made afterwards, which is why the two shapes
    here that launch **without** an observer are the ones this fails on before the bump.
    The adopted release composes the pair at the dispatch site instead, from the run the
    node belongs to.

    What it is compared against is this launch's own runs root rather than the plan's
    `name`: minting a run id is `onepipeline`'s, and a journey that restated it would
    pass on a release that had stopped exporting anything at all. One run root, and the
    id names it — which is the property an answer depends on, since a question put on
    another run's channel is one this run's manager never sees.
    """
    dispatch = cast(Dispatch, request.getfixturevalue(shape))
    given = _given(dispatch, RUN_ID)
    assert given.strip(), f"{RUN_ID.name} reached the dispatch blank, and it is {RUN_ID.why}"

    root = Path(dispatch.environment["ONEPIPELINE_RUNS_DIR"])
    created = sorted(child.name for child in root.iterdir() if child.is_dir())
    assert created == [given], (
        f"a dispatch was told it is under run '{given}', but this launch's runs root "
        f"holds {created}; a question goes to the channel that id names"
    )


@pytest.mark.xdist_group(LAUNCH_GROUP)
@pytest.mark.parametrize("shape", ["plan_attached", "plan_detached"])
@pytest.mark.parametrize("required", REQUIRED_INPUTS, ids=lambda row: row.name)
def test_every_plan_launch_gives_its_dispatch_each_input_the_wrapper_needs(
    shape: str, required: Input, request: pytest.FixtureRequest
) -> None:
    """Both inputs, both shapes — a defect journey for the detached one, a guard for the other.

    `just plan` writes the plan and owns its `name`, so both halves of the environment
    are this recipe's to establish, and the detached shape had only one of them: measured
    before the fix, a worker there was given no `ONEPIPELINE_RUN_ID` at all, so its first
    question died on `ONEPIPELINE_RUN_ID is not set`. The attached shape passes before
    and after, and is here as the guard on the one path that already worked.

    Parametrized over the inputs rather than asserting them together so a launch path
    that stops providing one is named by the failing case, which is what makes this
    readable from a suite run months from now.
    """
    dispatch = cast(Dispatch, request.getfixturevalue(shape))
    given = _given(dispatch, required)
    assert given.strip(), f"{required.name} reached the dispatch blank, and it is {required.why}"


@pytest.mark.xdist_group(LAUNCH_GROUP)
def test_a_plan_launch_tells_its_dispatch_the_run_it_actually_created(
    plan_detached: Dispatch,
) -> None:
    """The run a dispatch is told it is under is the run the launch made, not another one.

    A question is only answerable on the right channel, and the id is where that goes
    wrong silently: a wrapper handed somebody else's run does not fail, it queues a
    blocking surface on a channel that run's own manager is not watching, and then reads
    back whatever the timeout synthesizes.
    """
    assert _given(plan_detached, RUN_ID) == plan_detached.run


@pytest.mark.xdist_group(LAUNCH_GROUP)
@pytest.mark.parametrize("shape", ["orchestrate_attached", "plan_attached", "plan_detached"])
def test_a_dispatch_of_a_launch_can_reach_its_manager_with_nothing_set_up_by_hand(
    shape: str, request: pytest.FixtureRequest
) -> None:
    """The whole round trip, from inside a dispatch: a defect journey for two, a guard for one.

    A runnable path in the environment is not the claim that matters; getting an answer
    back is. So the process serving the dispatch runs the command that variable names,
    with nothing but what its own turn inherited, and a manager answers it over the real
    `just channel-next` and `just channel-reply`. What comes back has to be the manager's
    message and nothing else — the wrapper's whole contract with a caller.

    Attached `just orchestrate` and detached `just plan` are defect journeys: before the
    fix the first found no wrapper and the second refused with `ONEPIPELINE_RUN_ID is not
    set`. Attached `just plan` is the regression guard, and passes on both sides of it.
    """
    dispatch = cast(Dispatch, request.getfixturevalue(shape))
    asked = dispatch.asked
    assert asked is not None, f"the {shape} journey did not ask, so there is no answer to read"
    assert asked["status"] == 0, (
        f"a dispatch of run {dispatch.run} could not reach its manager:\n{asked['err']}"
    )
    assert ANSWER in asked["out"], (
        f"the manager's answer is not what reached the dispatch of run {dispatch.run}:\n"
        f"{asked['out']}"
    )
    assert asked["err"] == "", f"a successful ask reported something on stderr:\n{asked['err']}"


@pytest.mark.xdist_group(LAUNCH_GROUP)
def test_a_second_plan_launch_under_one_name_is_refused_rather_than_given_another_run(
    plan_detached: Dispatch, tmp_path: Path
) -> None:
    """A defect journey: the second launch used to print and hand out the first run's id.

    `onepipeline` mints a run id from the plan's `name` and, when a run root of that name
    already exists, mints the first free `<name>-2` instead. So the second launch here
    printed `just channel-next <name>` — naming the *first* run, which is live and whose
    manager is somebody else — and, now that the id is exported to the dispatch too,
    would have sent that planner's blocking questions to it.

    Launched against a name a journey above already used, rather than against a run root
    written by hand: what makes this a defect is that an ordinary launch takes the name
    first, and its ledger keeps it for as long as the run is on record.

    The recipe writes the plan and owns its `name`, so it refuses instead of predicting
    what will be minted. Both halves are asserted: the refusal names the collision and
    what to do, and nothing is left behind for the next launch to pick up.
    """
    _requires_just()
    run = plan_detached.run
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF, encoding="utf-8")
    generated = PLAN_DIRECTORY / f"{run}.plan.json"

    refused = just(
        "plan", str(brief), "--name", run, "--detach", environment=plan_detached.environment
    )

    assert refused.returncode != 0, (
        f"a second launch under the name '{run}' was not refused, and the run it "
        f"named is the first one's:\n{refused.stdout}{refused.stderr}"
    )
    reported = refused.stderr + refused.stdout
    assert f"run '{run}' already exists" in reported, reported
    assert "pass --name with a run id nothing has taken yet" in reported, reported
    assert not generated.exists(), (
        f"the refused launch left {generated.name} for the next one to pick up"
    )


#: The scripts a launch runs before it reaches the seam: the entry point itself and
#: the three helpers it sources. Copied into a checkout of their own so the wrapper
#: they resolve is that checkout's, which is how the refusal below is driven without
#: touching this one.
LAUNCH_SCRIPTS = (
    "onepipeline.sh",
    "ask-manager-env.sh",
    "claude-alt-config-dir.sh",
    "codex-alt-home.sh",
)


@pytest.mark.parametrize("verb", ["start", "adopt"])
def test_a_launch_whose_wrapper_it_cannot_run_is_refused_before_anything_starts(
    tmp_path: Path, verb: str
) -> None:
    """A checkout that cannot ask is refused at the launch, not at an agent's question.

    The failure this closes is silent by construction: an agent handed no wrapper, or
    one it cannot execute, does not stop — it runs the empty string or a permission
    error inside its own turn, decides anyway, and the plan comes back wrong after every
    node has run. So the check belongs where a launch can still be refused, and both
    verbs that dispatch take it: `start` and `adopt` alike, since an adopted run resumes
    work that has the same question to ask.

    Driven with the wrapper present but not executable, which is what a checkout
    restored without its modes looks like — and a state `[ -f ]` alone would pass.
    """
    scripts = tmp_path / "checkout" / "scripts"
    scripts.mkdir(parents=True)
    for name in LAUNCH_SCRIPTS:
        copied = scripts / name
        copied.write_bytes((REPO_ROOT / "scripts" / name).read_bytes())
        copied.chmod(0o755)
    unrunnable = scripts / "ask-manager.sh"
    unrunnable.write_bytes((REPO_ROOT / "scripts" / "ask-manager.sh").read_bytes())
    unrunnable.chmod(0o644)
    environment = dict(os.environ)
    for name in INHERITED_ENVIRONMENT:
        environment.pop(name, None)
    # A home of this journey's own: the helpers above resolve both alternate identity
    # directories under it, and one of them creates what it resolves.
    environment["HOME"] = str(tmp_path / "home")
    Path(environment["HOME"]).mkdir()

    refused = subprocess.run(  # noqa: S603 - the real entry point, in a checkout that cannot ask
        [str(scripts / "onepipeline.sh"), verb, "plan.json"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert refused.returncode != 0, f"a launch that cannot ask was not refused:\n{refused.stdout}"
    assert "ask-manager wrapper is not an executable file" in refused.stderr, refused.stderr
    assert "chmod +x" in refused.stderr, f"the refusal states no remedy:\n{refused.stderr}"
    assert not (tmp_path / "runs").exists(), "a run was started by a launch that cannot ask"


@pytest.mark.parametrize("shape", ["a directory nothing may search", "a file"])
def test_a_plan_launch_that_cannot_search_the_ledger_refuses_rather_than_assuming(
    tmp_path: Path, oneharness_bin: str, shape: str
) -> None:
    """A ledger this launch cannot look in is said out loud, not read as an empty one.

    The guarantee above rests on a negative — no run root of this name — and a negative
    is only worth as much as the lookup behind it. A ledger directory that cannot be
    searched answers "no such run" for every name in it, so a launch that trusted that
    would export a run id that is already somebody else's live run, which is the one
    thing the refusal exists to prevent.

    A mode of `000` does not by itself make a directory unsearchable: `root`, and
    anything else holding `CAP_DAC_READ_SEARCH`, is unaffected and there is no portable
    mode that stops them. So the scenario is measured with the same `access(2)` the guard
    consults, and skipped by name where it cannot be built rather than passing as a
    permission test that read a readable ledger.
    """
    _requires_just()
    run = RunId("launch-seam-plan-unsearchable")
    environment = _environment(tmp_path, oneharness_bin, tmp_path / "turns.jsonl")
    ledger = Path(environment["ONEPIPELINE_RUNS_DIR"])
    if shape == "a file":
        # The other way a configured ledger is unusable, and the one no permission can
        # rescue: something that is not a directory at all answers no lookup.
        ledger.write_text("not a ledger\n", encoding="utf-8")
    else:
        ledger.mkdir(parents=True)
        ledger.chmod(0o000)
        if os.access(ledger, os.X_OK):
            ledger.chmod(0o755)
            pytest.skip(
                "this user searches a mode-0 directory, so an unsearchable ledger cannot be set up"
            )
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF, encoding="utf-8")
    generated = PLAN_DIRECTORY / f"{run}.plan.json"

    try:
        refused = just("plan", str(brief), "--name", run, "--detach", environment=environment)

        assert refused.returncode != 0, f"an unsearchable ledger launched:\n{refused.stdout}"
        reported = refused.stderr + refused.stdout
        assert "cannot be searched" in reported, reported
        assert "fix its permissions" in reported, reported
        assert not generated.exists(), "a plan was written for a launch that was refused"
    finally:
        if ledger.is_dir():
            ledger.chmod(0o755)
        generated.unlink(missing_ok=True)


def test_a_plan_launch_without_the_helper_that_establishes_the_seam_writes_nothing(
    tmp_path: Path,
) -> None:
    """The recipe refuses a checkout missing the helper, before it writes a plan.

    `scripts/plan.sh` reads the seam out of `scripts/ask-manager-env.sh` rather than
    resolving the wrapper itself, so a checkout without that helper is a launch that
    cannot establish the seam at all — a different missing piece from a wrapper that is
    there but unrunnable, and one that would otherwise fail as a shell error naming a
    file the operator never asked about. Driven by running the real recipe from a
    directory holding only itself.
    """
    scripts = tmp_path / "checkout" / "scripts"
    scripts.mkdir(parents=True)
    alone = scripts / "plan.sh"
    alone.write_bytes((REPO_ROOT / "scripts" / "plan.sh").read_bytes())
    alone.chmod(0o755)
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF, encoding="utf-8")
    working = tmp_path / "working"
    working.mkdir()

    refused = subprocess.run(  # noqa: S603 - the real recipe, from a checkout without the helper
        [str(alone), str(brief), "--name", "launch-seam-no-helper"],
        cwd=working,
        env=dict(os.environ),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert refused.returncode != 0, refused.stdout
    assert "required helper is not a readable regular file" in refused.stderr, refused.stderr
    assert "ask-manager-env.sh" in refused.stderr, refused.stderr
    assert not (working / "scratch").exists(), "a plan was written for a launch that cannot ask"
