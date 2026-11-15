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

**Every journey here began as a defect journey**: it covers a `just orchestrate` launch
shape that was broken when it was written, exporting the seam nowhere, and each was
observed failing for that reason before the fix.

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
import re
import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple, NewType, TypedDict, cast

import pytest
import short_state
from fake_backend import (
    ASK_QUESTION_ENV,
    ASK_RECORD_ENV,
    ASK_TIMEOUT_ENV,
    DISPATCHED_MEMBER,
    ENVIRONMENT_KEYS_ENV,
    MEMBER_OF_CONFIG,
    PROMPT_LOG_ENV,
)
from planner_channel import PersistentManager, just, ruling
from project_fixtures import helper, project_from_plan
from scratch_identity import Identity, seeded
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The engine's human item store, which it keeps beside the runs under a runs root once
#: `.onepipeline/config.yaml` turns human items on; it is no run.
ITEM_STORE = "human-items"

#: The stand-in for the paid model, and the provider binary beneath it — the second is
#: what covers a single-sided member, which runs oneharness in process and so spawns no
#: CLI for the first to be.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")

#: A launching session these journeys state rather than inherit, and everything else an
#: enclosing dispatch would otherwise decide for them. `ONEPIPELINE_RUN_ID` is the one
#: that matters most: this suite runs inside a dispatch of its own, so a journey that
#: kept it would read the *enclosing* run's id back and call it a launch's doing.
LAUNCHING_SESSION = "e2e-launch-ask-seam"
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
    # The enclosing dispatch's session. A lifecycle dispatch carries its own worktree's
    # token, and the engine sets it only on a dispatch it opens a session for — so a
    # direct dispatch of a launch made from inside one inherits the outer token, and the
    # journey asserting a direct dispatch is given no session fails on the suite's own
    # environment rather than on anything the launch did.
    "ONEVCS_SESSION",
    "ORCHESTRATOR_ASK_MANAGER",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    "AI_ORCHESTRATOR_E2E_TOKEN",
)

#: A credential name of this journey's own, never one an operator configures. The
#: checkout's `.env` is this host's real credential surface and may already define
#: `GH_PROJECTS_TOKEN` with a live token; asserting on that name would mean either
#: overwriting the operator's value or reading it into a test, and the loader exports
#: the first definition of a name anyway, so a second one proves nothing. Credential
#: shaped on purpose — it is what `orchestrator/redaction.py` would hide, so a journey
#: that ever printed it prints `<redacted:...>` instead.
CREDENTIAL_NAME = "AI_ORCHESTRATOR_E2E_TOKEN"
CREDENTIAL_VALUE = "dispatch-side-projects-token"

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
    "the run whose channel the question goes to; the shim refuses rather than guessing "
    "at one, so an unset one is a question that is never asked",
)
REQUIRED_INPUTS = (ASK_WRAPPER, RUN_ID)

#: Deliberately not one of those. An ask made from the checkout the launch ran from needs
#: none of it, so the shim's header calls it optional and falls back to the relative `runs`
#: the engine itself reads — and this file measures it anyway, because that fallback is
#: exactly what a *lifecycle* dispatch cannot use: its working directory is a session
#: worktree holding no `runs`, and this variable is the only thing in its environment that
#: says where the run's channel actually is. onepipeline 0.33.0's contract composes it,
#: absolute, into every dispatch of every node (its `docs/contract-divergences.md`, "The
#: dispatch environment").
RUNS_ROOT = Input(
    "ONEPIPELINE_RUNS_DIR",
    "the runs root `scripts/ask-manager.sh` composes the run's channel directory under, "
    "so a relative or absent one from a worktree names a channel nobody launched",
)

#: Not required either, and for a sharper reason than the one above: the shim asks with
#: no `--asker` when nothing gives it one, so drift here degrades rather than breaks. What
#: it costs is the bus taking a question back: a question an ask left abandoned — its
#: listener killed by a tool deadline, say — is re-armed only by a later listener naming
#: the *same* asker, so a dispatch given none has its earlier questions stranded, silently,
#: with every fresh ask still working.
CHANNEL_ASKER = Input(
    "ONEPIPELINE_CHANNEL_ASKER",
    "who a dispatch asks as on the bus, which is what lets a later ask of the same "
    "dispatch take back a question an earlier one left abandoned",
)


#: Not an input of the wrapper at all, and read here because this is the one place a
#: real dispatch's own environment is read per launch shape. `ONEVCS_SESSION` is what
#: lets a lifecycle worker publish its session's change request as a draft
#: (`onevcs publish "$ONEVCS_SESSION" --draft`), which `templates/dispatch-appendix.md`'s
#: carve-out lets it do when its task says so; the engine composes it for every dispatch
#: that runs in a session's worktree and for no direct dispatch, whose worktree is nobody's
#: session. A lifecycle worker whose environment lost it would meet the carve-out with
#: nothing to name, and a direct worker given one would be told it has a session to publish.
SESSION = Input(
    "ONEVCS_SESSION",
    "the session token a lifecycle dispatch's worktree belongs to, which is what the "
    "appendix's draft publication names; a direct dispatch runs in no session and is "
    "given none",
)


@pytest.fixture(scope="module", autouse=True)
def repository_credentials_file() -> Iterator[None]:
    """Give this journey a name in the checkout's own `.env`, beside whatever is there.

    The file is this host's real credential surface, so the journey **adds** a name of
    its own and puts the original bytes back afterwards rather than replacing the file.
    An earlier version skipped when the file already existed, which disabled this
    journey on exactly the hosts where the feature is configured — a green run that
    proved nothing about the thing it is named for.

    Append-and-restore is also the failure-safe order: the operator's own lines are
    never removed, so the worst an interrupted run can leave behind is one extra
    test-only name beside them, never a lost credential.
    """
    credentials = REPO_ROOT / ".env"
    original = credentials.read_bytes() if credentials.exists() else None
    try:
        if original is None:
            descriptor = os.open(credentials, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(f"# e2e host credential\n{CREDENTIAL_NAME}={CREDENTIAL_VALUE}\n")
        else:
            with credentials.open("a", encoding="utf-8") as stream:
                stream.write(f"\n# e2e host credential\n{CREDENTIAL_NAME}={CREDENTIAL_VALUE}\n")
        yield
    finally:
        if original is None:
            credentials.unlink(missing_ok=True)
        else:
            credentials.write_bytes(original)


#: The question a dispatched agent puts, and the answer its manager gives. The answer is
#: compared whole against the envelope the dispatch's ask handed back, because what the
#: shim owes a caller is the bus's reply line carrying the manager's envelope and nothing
#: else.
ASK_QUESTION = "Should the cursor be an opaque token or a node id?"
ANSWER = "An opaque token; the node id would leak the ordering."

#: What every launch's recorded bus configuration must say, read as the values that make
#: it *this checkout's* `config/onemessagebus.yaml` rather than any configuration at all —
#: the layout and the reply window this host measured, with no envelope hook or
#: validator, since the engine's plan-edit hook judges every edit carrying task prose.
#: `tests/test_onemessagebus_config.py` holds each to the file and to the release that
#: loads it; these are restated rather than parsed because nothing in this suite's
#: environment reads YAML, and together they are what a launch that dropped the bus
#: configuration or pointed it elsewhere would lose.
BUS_PROFILE = "planner-channel"
BUS_REPLY_WINDOW_SECONDS = 3000

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
#: This module's own group, named for it alone: it is what keeps the one fixture below
#: that writes this checkout on one worker, and protects no toolchain — these journeys
#: only read the toolchain this checkout provisioned, which `tests/e2e/nx_workspace.py`
#: schedules nowhere.
LAUNCH_GROUP = "ask-seam-launch"

#: Every test in this module, rather than the launches alone. `repository_credentials_file`
#: below is autouse and `scope="module"`, so it runs once per worker that receives *any*
#: test from here — the refusal journeys included, which launch nothing and so read as free
#: to scatter. They are not: its create is `O_EXCL` against one shared `.env`, so two
#: workers reaching it together is one of them erroring in setup before its test runs.
#:
#: Measured rather than reasoned about. On a developer's checkout a `.env` already exists,
#: every worker takes the appending branch, and the module passes; on a publication clone
#: there is none, all four workers take the creating branch at once, and five tests error
#: in setup and refuse the push. `tests/tree_scans/test_nx_cache_scope.py` holds the rule.
pytestmark = [
    pytest.mark.xdist_group(LAUNCH_GROUP),
    # What the recipes these journeys run leave in the temporary directory, by design or
    # from a published tool they run, is this module's own and goes with it
    # (`tests/short_state.py`).
    pytest.mark.usefixtures("module_tmpdir"),
]


#: What the two `just orchestrate` journeys that only read an environment launch without.
#: The observer graph watches a run for its whole life, and every turn it takes is
#: another dispatch of the paid model's stand-in — real cost for a claim it has no
#: bearing on, since what a *worker* is given is settled by the launching process rather
#: than by who is watching.
#:
#: Dropping it used to change the environment as well — below onepipeline 0.8.1 the run
#: id reached a dispatch only by leaking out of an *attached* driver's own process after
#: it had started an observer there, so an unwatched launch dispatched a worker that
#: could not ask. That is why the two journeys below launch without one and still measure
#: a run id: on the adopted release the dispatch site composes it, so what carries it is
#: no longer who is watching.
WITHOUT_OBSERVER = ("--dag-graph", "off")


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
    #: A lifecycle node's two checkouts: the one its work publishes to, and the
    #: registered alias its session clones to cut a worktree from.
    repo: str
    execution_checkout: str


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
    environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp_path))
    environment[PROMPT_LOG_ENV] = str(turns)
    environment[ENVIRONMENT_KEYS_ENV] = ",".join(
        [
            *(required.name for required in REQUIRED_INPUTS),
            RUNS_ROOT.name,
            CHANNEL_ASKER.name,
            SESSION.name,
            CREDENTIAL_NAME,
        ]
    )
    if record is not None:
        environment[ASK_QUESTION_ENV] = ASK_QUESTION
        environment[ASK_RECORD_ENV] = str(record)
        environment[ASK_TIMEOUT_ENV] = str(ASK_WINDOW_SECONDS)
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


def _lifecycle_node(node_id: str, identity: Identity) -> PlanNode:
    """The same node, made a lifecycle one by naming the two checkouts it runs between.

    That is the whole of what makes the engine open an `onevcs` session for a dispatch:
    a `repo` to publish into and an execution checkout to clone from. The alias is the
    directory name `seeded` gave the clone, because that is the alias `onevcs` registers.
    """
    node = _node(node_id)
    node["repo"] = str(identity.publication)
    node["execution_checkout"] = identity.execution.name
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

    They keep reading until this journey says the asker has it, rather than stopping at
    the first answer, because which surface is the dispatch's question is known only once
    it is handed out: a manager who stopped after one read could spend it on a surface the
    run raised about itself. Every answer is bound to the question it was read off, by the
    correlation `just channel-reply --correlation` sends.
    """
    _await_run(run, environment)
    return PersistentManager(
        run,
        environment,
        lambda _surface: ruling(ANSWER),
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
        with _attached(
            ["just", "orchestrate", project_from_plan(plan)],
            environment,
            tmp_path / "launch.log",
        ):
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
        project_from_plan(plan),
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


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] This launch is already
# behind its own edge: this directory is an Nx project of its own, keyed on
# `askSeamLaunch`, which exists precisely so an unrelated edit does not pay for these
# launches. That key names, file by file, the configuration, graphs, personas, scripts and
# orchestrator code these launches were measured reading — a lifecycle one reads more of
# it than a direct one, not less — so a change one of them exercises re-runs them and a
# change none of them reads replays the green they earned.
@pytest.fixture(scope="module")
def orchestrate_lifecycle(
    tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str
) -> Dispatch:
    """Launch `just orchestrate --detach` on a lifecycle node, and have it ask from its worktree.

    The one launch here whose dispatch runs in a session worktree: the node names a
    seeded scratch identity's publication checkout and execution alias, so the engine
    opens a real `onevcs` session under this journey's own `ONEVCS_HOME` and cuts the
    worktree the worker starts in. Detached, for the reason the detached shape above is
    measured at all — what the dispatch is given is composed where the dispatch is made,
    and an attached launcher could add nothing to it afterwards.

    Its dispatch asks, because a worktree is the one place the shim's channel directory
    can go wrong: it holds no `runs` of its own, so a question reaches this run's channel
    only through what the dispatch was given. A manager answers over the run's own
    channel, and the ask's record says whether the answer came back.
    """
    _requires_just()
    tmp_path = tmp_path_factory.mktemp("orchestrate-lifecycle")
    run = RunId("launch-seam-orchestrate-lifecycle")
    turns, record = tmp_path / "turns.jsonl", tmp_path / "asked.json"
    environment = _environment(tmp_path, oneharness_bin, turns, record=record)
    identity = seeded(tmp_path)
    environment["ONEVCS_HOME"] = str(identity.home)
    environment.update(identity.environment)
    plan = _plan(tmp_path, run, [_lifecycle_node("only", identity)])

    launched = just(
        "orchestrate",
        project_from_plan(plan),
        "--detach",
        *WITHOUT_OBSERVER,
        environment=environment,
        seconds=120,
    )
    try:
        assert launched.returncode == 0, f"the launch failed:\n{launched.stdout}{launched.stderr}"
        dispatched = _await_dispatch(turns)
        manager = _answering(run, environment)
        answered = _await_answer(record, manager)
        _reaped(manager, answered)
        return Dispatch(run=run, worker=dispatched, asked=answered, environment=environment)
    finally:
        just("stop", run, environment=environment, seconds=60)


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
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
        project_from_plan(plan),
        "--detach",
        *WITHOUT_OBSERVER,
        environment=environment,
        seconds=120,
    )
    try:
        assert launched.returncode == 0, f"the launch failed:\n{launched.stdout}{launched.stderr}"
        _attest(run, "gate", environment)
        _adopt(run, environment)
        return Dispatch(run=run, worker=_await_dispatch(turns), asked=None, environment=environment)
    finally:
        just("stop", run, environment=environment, seconds=60)


def _adopt(run: RunId, environment: dict[str, str], *, seconds: float = 300) -> None:
    """Attach a fresh driver to a run, once the outgoing one has let go of it.

    `--adopt` is for a run nothing is driving, and it refuses one that still is. The
    launch above detached a driver and the attestation above that gave it something to
    do, so which of the two holds the run at any instant is a race this journey has no
    say in — and the adopted engine widened the window, because a driver on its way out
    now drains the run's command queue **before** releasing ownership rather than after.

    Retried rather than slept past, and on that refusal alone: any other failure is a
    real one and is raised with what the adoption said. The wait is what a manager does
    at a terminal for the same reason, which is why this is a poll rather than a stop —
    stopping the run first would establish the precondition by ending the very driver
    whose handover this journey is about.
    """
    limit = deadline(seconds)
    refused = ""
    while time.monotonic() < limit:
        # llmlint: ignore[expensive_tests_stay_behind_their_own_edge] The journey this
        # helper serves is a real orchestration launch and is already behind its own
        # edge: this directory is an Nx project of its own, keyed on `askSeamLaunch`,
        # which exists precisely so an unrelated edit does not pay for these launches.
        # That key names, file by file, the configuration, scripts, personas, graphs and
        # orchestrator code these launches were measured reading, so a change one of
        # them exercises re-runs them and a change none of them reads replays the green
        # they earned. This helper adds no journey and moves none; it makes an existing
        # one wait for a precondition the adopted engine made racy.
        adopted = just("orchestrate", "--adopt", run, environment=environment, seconds=300)
        if adopted.returncode == 0:
            return
        refused = adopted.stdout + adopted.stderr
        assert "is still being driven" in refused, f"the adoption failed:\n{refused}"
        time.sleep(1.0)
    raise AssertionError(f"run {run} was still being driven after {seconds}s:\n{refused}")


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
    created = sorted(
        child.name for child in root.iterdir() if child.is_dir() and child.name != ITEM_STORE
    )
    assert created == [given], (
        f"a dispatch was told it is under run '{given}', but this launch's runs root "
        f"holds {created}; a question goes to the channel that id names"
    )


@pytest.mark.parametrize(
    "shape",
    [
        "orchestrate_attached",
        "orchestrate_detached",
        "orchestrate_adopted",
        "orchestrate_lifecycle",
    ],
)
def test_every_launch_gives_its_dispatch_the_absolute_runs_root_its_channel_is_under(
    shape: str, request: pytest.FixtureRequest
) -> None:
    """The drift gate over the one input a worktree's ask cannot do without.

    `scripts/ask-manager.sh` names the bus's transport directory as
    `${ONEPIPELINE_RUNS_DIR:-runs}/<run>/channel` and resolves nothing else, so a dispatch
    whose runs root is relative or absent asks under whatever `runs` its working directory
    holds — none, in a session worktree — and its question reaches no manager. onepipeline
    0.33.0 composes it for every dispatch; that is the producer's to change, so it is held
    here against a real dispatch of every launch shape rather than restated.

    Read off the dispatch's own turn: absolute, and naming the root this launch really
    wrote its run's channel under, so the directory the shim composes is one the manager's
    `just channel-next` reads.
    """
    # `getfixturevalue` answers `Any` because the fixture is chosen by name at run time;
    # every name this case is parametrized over is a `Dispatch` fixture declared above.
    dispatch = cast(Dispatch, request.getfixturevalue(shape))
    root = Path(_given(dispatch, RUNS_ROOT))
    assert root.is_absolute(), (
        f"a dispatch of run {dispatch.run} was given a relative {RUNS_ROOT.name} ({root}); "
        f"it is {RUNS_ROOT.why}"
    )
    assert root == Path(dispatch.environment["ONEPIPELINE_RUNS_DIR"]), (
        f"a dispatch of run {dispatch.run} was given {root} as its runs root, where its "
        f"launch wrote the run under {dispatch.environment['ONEPIPELINE_RUNS_DIR']}"
    )
    assert (root / dispatch.run / "channel").is_dir(), (
        f"run {dispatch.run} has no channel directory under {root}, so the directory the "
        "shim composes from what this dispatch was given is not where the run's questions go"
    )


@pytest.mark.parametrize(
    "shape",
    [
        "orchestrate_attached",
        "orchestrate_detached",
        "orchestrate_adopted",
    ],
)
def test_every_launch_gives_its_dispatch_an_asker_to_ask_as(
    shape: str, request: pytest.FixtureRequest
) -> None:
    """The drift gate over the name that lets a dispatch take its own question back.

    `scripts/ask-manager.sh` passes this to `onemessagebus ask --asker`. An ask that ends
    without its answer — a tool deadline killing it, say — leaves its question abandoned
    on the queue, and the bus re-arms an abandoned question only for a later listener
    naming the **same** asker. So a dispatch given none asks as nobody, and a question an
    earlier ask of that dispatch left behind is one no later ask of it can take back.

    Measured against a real dispatch of every launch shape because that degrade is silent:
    the shim asks without an asker when nothing gives it one, so every fresh ask still
    returns an answer and nothing anywhere reports the narrowing. What the engine composes
    it *as* is deliberately not asserted — it is opaque and compared for equality — only
    that a dispatch is given one that names somebody.
    """
    dispatch = cast(Dispatch, request.getfixturevalue(shape))
    given = _given(dispatch, CHANNEL_ASKER)
    assert given.strip(), (
        f"a dispatch of run {dispatch.run} was given a blank {CHANNEL_ASKER.name}, which "
        f"is not an identity: the shim reads a blank one as none given, so the dispatch "
        f"asks as nobody and cannot take back a question it left abandoned"
    )
    # The dispatch's own, never whatever the launcher happened to be running under. This
    # is what makes `INHERITED_ENVIRONMENT` dropping the name load-bearing rather than
    # tidy: a journey that kept it would read the enclosing dispatch's asker back and
    # call it the engine's doing, and that reads exactly like a passing gate.
    assert given != dispatch.environment.get(CHANNEL_ASKER.name), (
        f"a dispatch of run {dispatch.run} was given the same {CHANNEL_ASKER.name} its "
        f"launch ran under, so this measures what leaked in rather than what the engine "
        f"composed, and an engine that stopped composing one would still pass"
    )


@pytest.mark.parametrize(
    "shape",
    [
        "orchestrate_attached",
        "orchestrate_detached",
        "orchestrate_adopted",
    ],
)
def test_every_launch_exports_repository_credentials_onto_its_dispatch(
    shape: str, request: pytest.FixtureRequest
) -> None:
    """Every launch shape puts this checkout's `.env` names in the environment it dispatches under.

    A dispatch inherits the driver's environment, so the launching process is the last
    place a value can reach one — and read back here from the **dispatch's** own turn
    rather than from the launcher, because a launcher that exported a name and a
    dispatch that received it are the two different facts, and only the second is what a
    worker's `onetaskgraph` invocation runs on.

    `cast` because the shape is parametrized: `getfixturevalue` resolves the fixture by
    name at run time, which no static type can follow back to what it returns.
    """
    dispatch = cast(Dispatch, request.getfixturevalue(shape))
    carried = {turn["environment"].get(CREDENTIAL_NAME) for turn in dispatch.worker}
    assert carried == {CREDENTIAL_VALUE}, (
        f"the dispatch of {dispatch.run} read {carried!r} for {CREDENTIAL_NAME}, rather "
        "than the value loaded by its launcher from this checkout's .env"
    )


def test_a_lifecycle_dispatch_is_given_the_session_its_worktree_belongs_to(
    orchestrate_lifecycle: Dispatch,
) -> None:
    """A dispatch in a session worktree is told which session, by the engine that opened it.

    The launch's node is a lifecycle node — it names a seeded identity's two checkouts
    and opens a real `onevcs` session under this journey's own `ONEVCS_HOME` — so what
    its worker is given is read off the worker's own turn: a token in `onevcs`'s own
    spelling that names a session record under that home. That token is what the
    appendix's carve-out has a worker publish its draft under, and a launch whose
    dispatch lost it would leave a worker granted early publication with nothing to
    name. The other launches are deliberately not read for it: their node is direct, so a
    session there would be the defect the journey below this one refuses.
    """
    dispatch = orchestrate_lifecycle
    token = _given(dispatch, SESSION)
    assert re.fullmatch(r"s-[0-9a-f]{12}", token), (
        f"a dispatch of run {dispatch.run} was given {token!r} as {SESSION.name}, which is "
        "not a session token in onevcs's own spelling"
    )
    home = Path(dispatch.environment["ONEVCS_HOME"])
    assert (home / "sessions" / f"{token}.json").is_file(), (
        f"a dispatch of run {dispatch.run} was given session {token}, and {home} holds no "
        "record of it; the token names a session the worker could not publish from"
    )


@pytest.mark.parametrize(
    "shape",
    [
        "orchestrate_attached",
        "orchestrate_detached",
        "orchestrate_adopted",
    ],
)
def test_a_direct_dispatch_is_given_no_session_at_all(
    shape: str, request: pytest.FixtureRequest
) -> None:
    """The other half: a dispatch in no session's worktree names none.

    The node these launches dispatch is direct — it names no repository — so the engine
    opens no session for it and composes no `ONEVCS_SESSION`. And it must not, because the
    appendix's carve-out tells a worker holding one that it may publish a draft from it.
    Read as an absence off every turn of the dispatch rather than as a blank, since a blank
    would be the variable composed with nothing in it.

    `cast` for the reason the journey above gives: a fixture chosen by name at run time
    is one no static type can follow back to what it returns.
    """
    dispatch = cast(Dispatch, request.getfixturevalue(shape))
    assert dispatch.worker, f"run {dispatch.run} dispatched nothing, so nothing here is measured"
    given = {turn["environment"].get(SESSION.name) for turn in dispatch.worker}
    assert given == {None}, (
        f"a direct dispatch of run {dispatch.run} was given {SESSION.name}={given!r}; it "
        "runs in no session's worktree and has nothing to publish a draft from"
    )


@pytest.mark.parametrize("shape", ["orchestrate_attached", "orchestrate_lifecycle"])
def test_a_dispatch_of_a_launch_can_reach_its_manager_with_nothing_set_up_by_hand(
    shape: str, request: pytest.FixtureRequest
) -> None:
    """The whole round trip, from inside a dispatch, for every shape that asks.

    A runnable path in the environment is not the claim that matters; getting an answer
    back is. So the process serving the dispatch runs the command that variable names,
    with nothing but what its own turn inherited and from wherever the engine placed it,
    and a manager answers over the real `just channel-next` and `just channel-reply
    --correlation`. What comes back has to be exactly one line — the bus's `reply` answer,
    carrying the envelope the manager sent — at exit 0.

    Attached `just orchestrate` was a defect journey when first written: it found no
    wrapper. The lifecycle shape is the ask from a session worktree, which holds no `runs`
    of its own, so it reaches this run's channel only through the runs root its dispatch
    was given.
    """
    dispatch = cast(Dispatch, request.getfixturevalue(shape))
    asked = dispatch.asked
    assert asked is not None, f"the {shape} journey did not ask, so there is no answer to read"
    assert asked["status"] == 0, (
        f"a dispatch of run {dispatch.run} could not reach its manager:\n{asked['out']}"
        f"{asked['err']}"
    )
    lines = asked["out"].splitlines()
    assert len(lines) == 1, (
        f"the ask from a dispatch of run {dispatch.run} printed {len(lines)} lines where the "
        f"bus answers with one:\n{asked['out']}"
    )
    # `cast` rather than a validating read: `onemessagebus` owns this line's shape, and the
    # two fields a caller relies on are asserted below.
    answer = cast(dict[str, object], json.loads(lines[0]))
    assert answer.get("answer") == "reply", (
        f"the dispatch of run {dispatch.run} was answered with something other than a "
        f"reply:\n{asked['out']}"
    )
    record = answer.get("reply")
    assert isinstance(record, dict) and record.get("reply") == json.loads(ruling(ANSWER)), (
        f"the manager's envelope is not what reached the dispatch of run {dispatch.run}:\n"
        f"{asked['out']}"
    )
    # The verb's two announcements — the correlation as the question is queued, and the
    # window it waits — and nothing else.
    assert asked["err"].splitlines() == [
        f"correlation: {answer.get('correlation')}",
        f"waiting up to {ASK_WINDOW_SECONDS} seconds for the reply",
    ], f"a successful ask reported something beyond the verb's own on stderr:\n{asked['err']}"


#: The shapes of launch this module makes, each read for the bus configuration its run
#: recorded.
LAUNCH_SHAPES = (
    "orchestrate_attached",
    "orchestrate_detached",
    "orchestrate_adopted",
    "orchestrate_lifecycle",
)


@pytest.mark.parametrize("shape", LAUNCH_SHAPES)
def test_every_launch_records_this_checkouts_bus_configuration_for_its_run(
    shape: str, request: pytest.FixtureRequest
) -> None:
    """The engine a launch starts applies this host's messaging policy to the run's channel.

    `config/onepipeline.launch.yaml`, which `scripts/onepipeline.sh` names on every
    `onepipeline start` — the verb every launch shape reaches — names
    `config/onemessagebus.yaml`, and the engine
    records the configuration it parsed in the run's `launch.json` under `bus_config`. So
    the record is read rather than the argv: it says what the engine actually took, which
    is what decides the authors its replies are held to and the reply window its observer's
    judge side is served under.

    Asserted as the values that make it this checkout's configuration — the layout, no
    envelope validator, and its measured reply window — because an engine given no
    configuration records none, and one given another file records other values. The
    adopted shape is read after its adoption, which names no configuration of its own, so
    it is also the run keeping the configuration its launch retained.
    """
    # `cast` because `getfixturevalue` answers `Any` for a fixture named at run time, and
    # every name in `LAUNCH_SHAPES` is a fixture of this module that yields a `Dispatch`.
    dispatch = cast(Dispatch, request.getfixturevalue(shape))
    launch = Path(dispatch.environment["ONEPIPELINE_RUNS_DIR"]) / dispatch.run / "launch.json"
    assert launch.is_file(), f"run {dispatch.run} left no launch record at {launch}"
    # `cast` rather than a validating read: the engine owns this record, and every field
    # read off it below is asserted where it is read.
    # llmlint: ignore[tests_mirror_real_usage] No view renders the bus configuration the
    # engine parsed, and the argv says only what the launch asked for; the run's launch
    # record is the one place the engine's own reading is kept.
    recorded = cast(dict[str, object], json.loads(launch.read_text(encoding="utf-8")))
    bus = recorded.get("bus_config")
    assert isinstance(bus, dict), (
        f"run {dispatch.run} recorded no bus configuration, so its channel runs under the "
        f"bus's defaults rather than this host's: {sorted(recorded)}"
    )
    assert bus.get("profile") == BUS_PROFILE, bus
    # No bus validator: the engine's plan-edit hook, which the same launch config names,
    # judges every edit carrying task prose.
    assert not bus.get("validators"), bus.get("validators")
    assert not bus.get("hooks"), bus.get("hooks")
    codecs = bus.get("codecs")
    monitor = codecs.get("monitor") if isinstance(codecs, dict) else None
    assert isinstance(monitor, dict), f"run {dispatch.run} recorded no monitor binding: {bus}"
    assert monitor.get("reply_window_seconds") == BUS_REPLY_WINDOW_SECONDS, (
        f"run {dispatch.run} serves its observer's judge side under a reply window of "
        f"{monitor.get('reply_window_seconds')!r}, not this host's {BUS_REPLY_WINDOW_SECONDS}"
    )


#: The scripts a launch runs before it reaches the seam: the entry point itself and the
#: helper it asks who is acting, the definition of which resolvers establish a
#: dispatch's environment and the resolvers it names, the template-root helper every verb
#: sources, the merge-queue bound's helper, and the ask-manager helper. Copied into a
#: checkout of their own so the wrapper they resolve is that checkout's, which is how the
#: refusal below is driven without touching this one.
LAUNCH_SCRIPTS = (
    "onepipeline.sh",
    "launcher-session.sh",
    "dispatch-env.sh",
    "decline-ruling-env.sh",
    "decline-ruling.sh",
    "template-env.sh",
    "credentials-env.sh",
    "ask-manager-env.sh",
    "claude-alt-config-dir.sh",
    "codex-alt-home.sh",
    "lock-timeout.sh",
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
    # The template root every verb names is refused without its registration, before the
    # seam is reached, so the checkout carries this one's.
    registration = tmp_path / "checkout" / "templates" / "templates.yaml"
    registration.parent.mkdir()
    registration.write_bytes((REPO_ROOT / "templates" / "templates.yaml").read_bytes())
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
