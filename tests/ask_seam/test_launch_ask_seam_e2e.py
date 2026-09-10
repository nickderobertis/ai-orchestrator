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
import shlex
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple, NewType, NotRequired, TypedDict, cast

import plan_root_variable
import pytest
from conftest import git
from fake_backend import (
    ASK_QUESTION_ENV,
    ASK_RECORD_ENV,
    AUTHOR_PLAN_ENV,
    DISPATCHED_MEMBER,
    ENVIRONMENT_KEYS_ENV,
    MEMBER_OF_CONFIG,
    PROMPT_LOG_ENV,
    RUN_ON_MARKER_ENV,
)
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from planner_channel import PersistentManager, just, ruling
from project_fixtures import helper, project_from_plan
from published_tools import ONETASKGRAPH_BIN
from scratch_identity import GIT_IDENTITY, seeded
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator import criteria_guard, plan_store
from orchestrator.project_store import render_plan_project
from orchestrator.root import REPO_ROOT

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
    "ORCHESTRATOR_ASK_MANAGER",
    # The plan-authoring root a *planning* launch exports. Read from the one place that
    # composes it rather than spelled here. A journey that kept the enclosing dispatch's
    # would measure a directory some outer launch chose, and a launch that stopped
    # exporting one at all would go on passing.
    plan_root_variable.name(),
    # The operational appendix a *planning* launch exports, cleared for the reason the
    # root above is: a journey that kept an enclosing dispatch's would be measuring text
    # some outer launch's checkout supplied, and a launch that stopped exporting one at
    # all would go on passing on the strength of it.
    criteria_guard.APPENDIX_ENV,
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
    "the run whose channel the question goes to; the wrapper refuses rather than "
    "guessing at one, so an unset one is a question that is never asked",
)
REQUIRED_INPUTS = (ASK_WRAPPER, RUN_ID)

#: Deliberately not one of those. An ask made from the checkout the launch ran from
#: needs none of it, so the wrapper's header calls it optional and this file measures it
#: anyway — the gate above is one-directional for exactly this case. What it buys is the
#: half of the seam a *lifecycle* dispatch depends on: that dispatch's working directory
#: is a session worktree, `onepipeline` looks for a run under a relative `runs` when
#: nothing names one, and this variable is the only thing in the environment that says
#: where the run's records actually are.
NODE_SCRATCH = Input(
    "ONEPIPELINE_NODE_SCRATCH_DIR",
    "the one thing a dispatch carries that names its own run's directory, which "
    "`scripts/ask-manager.sh` walks up to find the runs root from a worktree",
)

#: Not required either, and for a sharper reason than the one above: the wrapper names
#: itself when nothing gives it one, so drift here degrades rather than breaks. What it
#: costs is the difference between an asker that is the *dispatch* and one that is a
#: single invocation of the wrapper — so a question an earlier ask of a dispatch left
#: outstanding stops being taken back over, silently, with every ask still working.
CHANNEL_ASKER = Input(
    "ONEPIPELINE_CHANNEL_ASKER",
    "who a dispatch's `channel serve` sessions listen on behalf of, which is what lets "
    "one still-pending question outlive the listener that raised it",
)

#: Not required of every launch either — only of a *planning* one, which is the launch
#: that dispatches an agent whose whole deliverable is a plan. `onetaskgraph.yaml` roots
#: the `authoring` source relatively and a planner works in a worktree of its own, so
#: without this the plan it authors lands in that worktree's own copy of the directory:
#: nothing outside the worktree reads it, and the worktree is reclaimed with the run.
PLAN_ROOT = Input(
    plan_root_variable.name(),
    "the directory a dispatched planner authors its plan into; a relative source root "
    "resolves against each process's own working directory, so an unset one is a plan "
    "written where the launching checkout never looks",
)

#: Required of a planning launch for the same reason, and it carries a **value** rather
#: than a path. Every dispatched node's task has to hold this text verbatim or
#: `just check-plan` refuses it, and the party that copies it in is the planner — which
#: works in a worktree of its own, while the tracked file lives in the launching checkout.
#: A path would name a file that planner cannot open, which is what the refusal it met
#: named, and a manager appended the appendix to a plan by hand instead.
DISPATCH_APPENDIX = Input(
    criteria_guard.APPENDIX_ENV,
    "the operational appendix a dispatched planner copies into every task it writes; the "
    "tracked file is outside its worktree, so an unset one leaves the planner with a "
    "path it cannot read and a plan every node of which is refused",
)


@pytest.fixture(scope="module", autouse=True)
def this_checkouts_own_plan_root() -> Iterator[None]:
    """Read this checkout's own plan store here, whatever launch this suite runs inside.

    This module both launches planning runs and asks what its own checkout resolves the
    `authoring` source to. A planning launch exports that root into every dispatch it
    makes and this suite runs inside a dispatch, so an enclosing one would otherwise make
    `plan_store.source_root` here answer about *its* checkout — and every expectation
    below would be measured against a directory this launch had nothing to do with.
    """
    with pytest.MonkeyPatch.context() as patched:
        patched.delenv(plan_root_variable.name(), raising=False)
        yield


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
#: `tests/ask_seam/test_ask_manager_e2e.py`'s group, deliberately, rather than one of this
#: module's own. Two groups is two workers, and everything in both is a live planner
#: channel with a manager thread driving real recipes at it — measured, a suite running
#: the two beside each other left that module's wrapper waiting past its own deadline.
#: What has to be serialized is driving a channel, not driving this file's channels.
#:
#: That group is now `tests/e2e/nx_workspace.py`'s, shared with the journeys that
#: re-provision this checkout's toolchain, for the reason stated there: these recipes
#: reach their tools through `uv run`, and `--dist loadgroup` serialises one group name
#: rather than two.
LAUNCH_GROUP = SHARED_TOOLCHAIN_GROUP

#: Every test in this module, rather than the launches alone. `repository_credentials_file`
#: below is autouse and `scope="module"`, so it runs once per worker that receives *any*
#: test from here — the refusal journeys included, which launch nothing and so read as free
#: to scatter. They are not: its create is `O_EXCL` against one shared `.env`, so two
#: workers reaching it together is one of them erroring in setup before its test runs.
#:
#: Measured rather than reasoned about. On a developer's checkout a `.env` already exists,
#: every worker takes the appending branch, and the module passes; on a publication clone
#: there is none, all four workers take the creating branch at once, and five tests error
#: in setup and refuse the push. `tests/test_nx_cache_scope.py` holds the rule.
pytestmark = pytest.mark.xdist_group(LAUNCH_GROUP)

#: The two checkouts the node `just plan` writes names, as `scripts/plan.sh` defaults
#: them. Each launch below seeds a scratch pair under exactly these names, so the
#: recipe's own defaults resolve against a registry of the journey's own.
PUBLICATION_ALIAS = "ai-orchestrator"
EXECUTION_ALIAS = "ai-orchestrator-isolated"

#: The plan source `scripts/plan.sh` writes its own project into and a dispatched planner
#: authors into. Named here because the store reports one setting per source and this is
#: the source these journeys read back.
AUTHORING = "authoring"

#: The brief a `just plan` launch is made from. Written to a temporary directory rather
#: than taken from `examples/`, so these journeys read none of this repository's prose
#: and stay in the code-only test tier.
#:
#: The `Plan project:` line is what the recipe's second node is given: a planning launch
#: writes a `design-doc` node beside the planner, and that node has no other way to find
#: the plan it is writing about. A brief without one is refused before anything is
#: dispatched, which for the refusal journeys below would be the wrong refusal — so it is
#: here rather than each of them passing `--no-design-doc` to avoid it.
BRIEF = """## What
Decide whether the paginated listing's cursor is an opaque token or a node id.

Plan project: authoring:cursor-shape

## Why
The browser view cannot deep-link to a page until that is settled.

## Acceptance criteria
- The cursor's shape and its type are stated.
"""


class SettingOrigin(TypedDict):
    """Which layer the plan store says one setting's value came from.

    `variable` is present only for the environment layer, which is the whole reason this
    is read: a root that came from a launch and one that came from the worktree's own
    tracked document are the same directory string wearing two different origins.
    """

    layer: str
    variable: NotRequired[str]


class StoreSetting(TypedDict):
    """One setting of the store's own `config show` answer, in the fields read here.

    `value` stays `object` because that answer carries every setting the store resolves —
    numbers, lists, and strings — and only the one this module asks about is a path. It
    is narrowed where it is read rather than asserted of the whole answer.
    """

    key: str
    value: object
    origin: SettingOrigin


class StoreConfiguration(TypedDict):
    """What a dispatch's own `onetaskgraph config show --json` answers with."""

    settings: list[StoreSetting]


#: What `tests/e2e/fake_backend.py` reads out of the file `RUN_ON_MARKER_ENV` names: each
#: marker, against the argument vectors a turn whose prompt carries it runs in order. An
#: alias rather than a model, because the key is open by construction — it is whatever
#: prose the journey keys on — and the contract is the shape of the value.
RunOnMarker = dict[str, list[list[str]]]

#: The fragment of that brief a dispatched turn's commands are keyed on. It is prose
#: every dispatch of a planning launch carries, which is the point: both nodes a planning
#: launch writes take the brief, both are given the same plan-authoring root, and both
#: answering with that root is the claim rather than a collision.
STORE_MARKER = "Plan project: authoring:cursor-shape"


#: The plan a dispatched planner authors, in the one action a stand-in for the paid model
#: can perform that anything downstream observes. Its content is beside the point — what
#: is read back afterwards is that the launching checkout finds it at all, which is the
#: whole of what an authoring root is for.
AUTHORED_TASK = (
    "## What\n\nAdd the route and the test that drives it.\n\n"
    "## Why\n\nThe user cannot complete a purchase without it.\n\n"
    "## Acceptance criteria\n\n- The route accepts a valid request and rejects an invalid one.\n"
)


def _authored_records(root: Path, native: str) -> dict[str, str]:
    """The records a dispatched planner writes into ``root``, as absolute paths."""
    rendered = render_plan_project(
        {
            "schema_version": 3,
            "name": native,
            "goal": {"text": "Deliver the checkout route"},
            "tasks": [
                {
                    "id": "route",
                    "persona": "engineer",
                    "title": "feat: add the checkout route",
                    "task": AUTHORED_TASK,
                }
            ],
        },
        native_id=native,
    )
    return {str(root / relative): content for relative, content in rendered.items()}


def _reading_the_plan_store(destination: Path) -> list[str]:
    """The command a dispatched turn runs to report what its own plan store resolves.

    `ONETASKGRAPH_BIN` is unset first because `onepipeline` composes it for every
    dispatch and `onetaskgraph` reads every `ONETASKGRAPH_` name as a *setting* — so a
    process that inherits it refuses `bin` as an unknown field and answers nothing at
    all. `orchestrator/plan_store.py` strips it for the same reason; a dispatched agent
    reaching the CLI directly has to do it too.
    """
    return [
        "/bin/sh",
        "-c",
        f"unset ONETASKGRAPH_BIN; {shlex.quote(str(ONETASKGRAPH_BIN))} config show --json "
        f"> {shlex.quote(str(destination))}",
    ]


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
    #: Where the dispatch wrote its own plan store's `config show`, for the launches
    #: that asked it to; `None` for every launch that did not.
    store: Path | None = None
    #: What the launch itself printed, for the one claim only the launcher can make:
    #: where it says it wrote its project. `None` for a launch nothing read the stream of.
    reported: str | None = None


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
    environment[ENVIRONMENT_KEYS_ENV] = ",".join(
        [
            *(required.name for required in REQUIRED_INPUTS),
            NODE_SCRATCH.name,
            CHANNEL_ASKER.name,
            PLAN_ROOT.name,
            DISPATCH_APPENDIX.name,
            CREDENTIAL_NAME,
        ]
    )
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


def _await_store_answer(reported: Path, *, seconds: float = MANAGER_SECONDS) -> None:
    """Wait for the dispatch to report what its own plan store resolved.

    Its own wait rather than the ask's, because the two do not finish together: the
    stand-in serving a dispatch asks first and runs the turn's commands afterwards, so a
    journey that returned when the answer landed would stop the run before the store had
    been read — and read an absent file as a dispatch that resolved nothing.
    """
    limit = deadline(seconds)
    while time.monotonic() < limit:
        if reported.is_file() and reported.stat().st_size:
            return
        time.sleep(0.5)
    raise AssertionError(
        f"the dispatch never reported its own plan store at {reported} within {seconds}s"
    )


def _await_authored_plan(records: Mapping[str, str], *, seconds: float = MANAGER_SECONDS) -> None:
    """Wait for the records a dispatched planner authors to be on disk.

    The records rather than the instruction that asks for them, because the stand-in
    claims that instruction by removing it *before* it writes anything: an absent
    instruction says the authoring was reached and nothing about whether it finished, so
    waiting on it would let this journey stop the run mid-write and then read back a
    store that is missing exactly what it came to read.
    """
    pending = [Path(destination) for destination in records]
    limit = deadline(seconds)
    missing = pending
    while time.monotonic() < limit:
        missing = [path for path in pending if not path.exists()]
        if not missing:
            return
        time.sleep(0.5)
    raise AssertionError(
        f"the dispatch authored no plan within {seconds}s; it never wrote {missing}"
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
        # edge: `tests/ask_seam/` is an Nx project of its own, keyed on
        # `askSeamWorkspace`, which exists precisely so an unrelated edit does not pay
        # for these launches. That key covers the configuration, scripts, personas,
        # graphs and orchestrator code these launches really read, so narrowing it would
        # leave the tier replaying a green across a change one of them exercises — the
        # failure the split was made to end. This change adds no journey and moves none;
        # it makes an existing one wait for a precondition the adopted engine made racy.
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


def _carrying_the_plan_store_configuration(execution: Path) -> None:
    """Give the checkout a planner's worktree is cut from this repository's store document.

    A real planning launch executes in a clone of this repository, so the worktree its
    planner works in carries `onetaskgraph.yaml` — which is what declares the `authoring`
    source's plugin, the half of that source a launch does not establish. The seeded
    identity beside this is a bare repository holding a README, so without it the store
    inside a dispatch would refuse a configuration naming a root for a source no document
    defines, and the one thing the launch *did* establish would be unreadable from the
    only place it was established for.

    Committed and pushed, because the session that cuts the worktree clones this checkout
    and takes its base from the origin they share.
    """
    (execution / "onetaskgraph.yaml").write_bytes((REPO_ROOT / "onetaskgraph.yaml").read_bytes())
    git("add", "-A", cwd=execution)
    git(*GIT_IDENTITY, "commit", "-qm", "chore: carry the plan store configuration", cwd=execution)
    git("push", "-q", "origin", "main", cwd=execution)


def _planned(
    tmp_path: Path,
    oneharness_bin: str,
    run: RunId,
    *detached: str,
    resolved: Path | None = None,
    root: Path | None = None,
    authored: str | None = None,
) -> Dispatch:
    """Launch `just plan` on a brief, have its dispatch ask, and hand back both.

    Attached and detached differ by one flag and by nothing else here, so they are one
    function: what they are being compared on is the environment each leaves behind, and
    a second copy of the launch would be a second chance for the two to differ for a
    reason that is not the flag.

    `resolved` names where the dispatch is to write the plan store's own answer about the
    `authoring` source, which is the other half of what a launch establishes: a variable
    in a dispatch's environment is not the claim that matters, and the store resolving it
    to the launching checkout's directory is. `root` states a plan-authoring root the
    caller has already chosen, so that a launch which resolves one of its own can be told
    apart from one that leaves a caller's alone. `authored` names a project the dispatched
    planner is to write into whichever root is in force, which is the last link: a root
    both sides agree on is worth nothing until a plan written through it comes back.
    """
    turns, record = tmp_path / "turns.jsonl", tmp_path / "asked.json"
    environment = _environment(tmp_path, oneharness_bin, turns, record=record)
    # The plan this recipe writes is a lifecycle node naming the two checkouts it
    # defaults to, so the launch opens a real `onevcs` session — and it may never be
    # this host's, whose registry a session reclaims run roots under. Seeded under those
    # two alias names rather than overridden per launch: what these journeys drive is
    # the recipe as an operator types it, and a `--repo` here would be proving a flag.
    identity = seeded(tmp_path, publication=PUBLICATION_ALIAS, execution=EXECUTION_ALIAS)
    _carrying_the_plan_store_configuration(identity.execution)
    environment["ONEVCS_HOME"] = str(identity.home)
    if root is not None:
        environment[PLAN_ROOT.name] = str(root)
    # Held rather than only written, because the wait below is on these very paths: the
    # stand-in claims the instruction file before it writes them, so the instruction is
    # gone by the time there is anything to read and only the records say authoring is done.
    records: Mapping[str, str] | None = None
    if authored is not None:
        records = _authored_records(root or plan_store.source_root(AUTHORING), authored)
        written = tmp_path / "authored-plan.json"
        written.write_text(json.dumps(records), encoding="utf-8")
        # The stand-in for the paid model performs the one action a real planner performs
        # — it writes its plan into the store — because a stand-in that only reported
        # would leave nothing for the launching checkout to read back. Everything above
        # the provider stays real.
        # llmlint: ignore[tests_mirror_real_usage] see the note above this line
        environment[AUTHOR_PLAN_ENV] = str(written)
    if resolved is not None:
        instruction = tmp_path / "run-on-marker.json"
        keyed: RunOnMarker = {STORE_MARKER: [_reading_the_plan_store(resolved)]}
        instruction.write_text(json.dumps(keyed), encoding="utf-8")
        # llmlint: ignore[tests_mirror_real_usage] The stand-in for the paid model runs
        # the real store CLI in the dispatch's own worktree, which is what a dispatched
        # planner does and the only thing about a turn anything downstream can observe.
        environment[RUN_ON_MARKER_ENV] = str(instruction)
    brief = tmp_path / f"{run}.md"
    brief.write_text(BRIEF, encoding="utf-8")
    generated = PLAN_DIRECTORY / f"{run}.plan.json"

    # `--no-design-doc`, so this launch is the planner and nothing after it. What these
    # journeys read is the environment a launch establishes and the run it establishes it
    # for; the tail is a second launch about the plan the planner writes, and running it
    # here would copy a fixture's plan into whatever destination the flow defaults to.
    recipe = ["just", "plan", str(brief), "--name", run, "--no-design-doc", *detached]
    try:
        with _attached(recipe, environment, tmp_path / "launch.log") as streamed:
            dispatched = _await_dispatch(turns)
            manager = _answering(run, environment)
            answered = _await_answer(record, manager)
            if records is not None:
                _await_authored_plan(records)
            if resolved is not None:
                _await_store_answer(resolved)
            _reaped(manager, answered)
            assert (Path(environment["ONEPIPELINE_RUNS_DIR"]) / run).is_dir(), (
                f"`just plan` printed and exported run '{run}', which is not a run this "
                f"launch created:\n{streamed.read_text(encoding='utf-8')}"
            )
            return Dispatch(
                run=run,
                worker=dispatched,
                asked=answered,
                environment=environment,
                store=resolved,
                reported=streamed.read_text(encoding="utf-8"),
            )
    finally:
        just("stop", run, environment=environment, seconds=60)
        generated.unlink(missing_ok=True)


@pytest.fixture(scope="module")
def plan_attached(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Dispatch:
    """Launch `just plan` attached — the one shape this host had ever proven.

    This is the shape that also reports what its dispatch's own plan store resolves, so
    the two halves of the plan-authoring root — the value a launch exports, and the
    directory the store inside a dispatch then answers with — are read off one launch
    rather than paid for twice.
    """
    _requires_just()
    tmp_path = tmp_path_factory.mktemp("plan-attached")
    return _planned(
        tmp_path,
        oneharness_bin,
        RunId("launch-seam-plan-attached"),
        resolved=tmp_path / "dispatch-config.json",
    )


@pytest.fixture(scope="module")
def plan_root_already_chosen(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A plan-authoring root a caller points this launch at before it starts.

    A genuinely separate writable directory, nowhere near this checkout's own — which is
    the only shape that measures anything. A path merely spelled differently would prove
    the value survived and nothing about where the launch then writes, and a directory
    the checkout would have resolved anyway cannot tell a launch that honoured the
    override from one that ignored it.
    """
    chosen = tmp_path_factory.mktemp("plan-root-already-chosen") / "somebody-elses-plans"
    chosen.mkdir()
    return chosen


#: The project a dispatched planner authors under whichever root is in force. Distinct
#: from the run's own name, because the launch writes a project of that name too and the
#: point of reading this one back is that it is the *planner's* output.
AUTHORED_PROJECT = "launch-seam-chosen-root-authored"


@pytest.fixture(scope="module")
def plan_over_a_chosen_root(
    tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str, plan_root_already_chosen: Path
) -> Dispatch:
    """Launch `just plan` over a root the caller chose, and read the whole path back.

    Its own launch rather than a flag on one above, because what is being measured is a
    launch's *starting environment*: the recipe resolves this checkout's own root
    unconditionally, and only reading the value back out of a dispatch says whether the
    one the caller chose survived that.

    Attached rather than detached, because this journey reads what the dispatch wrote and
    an attached launch is the shape that holds the run open while it does. It carries all
    three halves of the override — the project the launch writes, the root the dispatch's
    own store resolves, and the plan that dispatch authors — off one launch, because they
    are one claim and three launches would be three chances for them to disagree for a
    reason that is not the root.
    """
    _requires_just()
    tmp_path = tmp_path_factory.mktemp("plan-chosen-root")
    return _planned(
        tmp_path,
        oneharness_bin,
        RunId("launch-seam-plan-chosen-root"),
        root=plan_root_already_chosen,
        resolved=tmp_path / "dispatch-config.json",
        authored=AUTHORED_PROJECT,
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
    created = sorted(child.name for child in root.iterdir() if child.is_dir())
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
        "plan_attached",
        "plan_detached",
    ],
)
def test_every_launch_gives_its_dispatch_a_scratch_directory_under_its_own_run(
    shape: str, request: pytest.FixtureRequest
) -> None:
    """The drift gate over the layout the wrapper resolves a runs root from.

    `scripts/ask-manager.sh` walks up from this variable looking for the directory named
    for its run that holds a `launch.json`, and takes that directory's parent as the runs
    root. The walk is written not to assume a depth, but it does depend on the engine
    putting a dispatch's scratch somewhere under the run's own directory — which is
    `onepipeline`'s to change and nothing here owns.

    Measured against a real dispatch of every launch shape rather than restated, because
    the failure mode of drift is silent in exactly the direction that matters: the walk
    would find nothing, the ask would fall back to the relative `runs` it used to use,
    and a lifecycle worker would go back to being refused with no surface raised. Its
    own journeys stand up a scratch directory of this shape by hand — these runs reach
    no dispatch of their own — so this is where that shape is held to the producer.
    """
    # `getfixturevalue` answers `Any` because the fixture is chosen by name at run time;
    # every name this case is parametrized over is a `Dispatch` fixture declared above.
    dispatch = cast(Dispatch, request.getfixturevalue(shape))
    scratch = Path(_given(dispatch, NODE_SCRATCH))
    assert scratch.is_absolute(), (
        f"a dispatch of run {dispatch.run} was given a relative {NODE_SCRATCH.name} "
        f"({scratch}); it is {NODE_SCRATCH.why}, and a relative one names a different "
        "directory for every caller"
    )

    own = Path(dispatch.environment["ONEPIPELINE_RUNS_DIR"]) / dispatch.run
    assert scratch.is_relative_to(own), (
        f"a dispatch of run {dispatch.run} was given {scratch}, which is not under that "
        f"run's own directory {own}; the wrapper finds the runs root by walking up from "
        "it, so an ask from a worktree has nothing left to resolve"
    )
    assert (own / "launch.json").is_file(), (
        f"run {dispatch.run} has no launch record at {own / 'launch.json'}; that file is "
        "what the walk stops at, so a run without one cannot be found from a worktree"
    )


@pytest.mark.parametrize(
    "shape",
    [
        "orchestrate_attached",
        "orchestrate_detached",
        "orchestrate_adopted",
        "plan_attached",
        "plan_detached",
    ],
)
def test_every_launch_gives_its_dispatch_an_asker_to_ask_as(
    shape: str, request: pytest.FixtureRequest
) -> None:
    """The drift gate over the name that keeps one question alive across a re-arm.

    `onepipeline channel serve` is a listener an asker rents rather than the asker
    itself, so `scripts/ask-manager.sh` waits through a succession of them over one
    still-pending question. A session that ends leaves what it raised owed to nobody,
    and only a later session of the **same** asker takes it back — so a dispatch that
    was given none has an ask whose asker is one invocation of the wrapper rather than
    the dispatch, and a question an earlier ask left outstanding is quietly stranded.

    Measured against a real dispatch of every launch shape because that degrade is
    silent in both directions: the wrapper mints its own when nothing gives it one, so
    every ask still returns an answer and nothing anywhere reports the narrowing. What
    the engine composes it *as* is deliberately not asserted — it is opaque and compared
    for equality — only that a dispatch is given one that names somebody.
    """
    dispatch = cast(Dispatch, request.getfixturevalue(shape))
    given = _given(dispatch, CHANNEL_ASKER)
    assert given.strip(), (
        f"a dispatch of run {dispatch.run} was given a blank {CHANNEL_ASKER.name}, which "
        f"is not an identity: `channel serve` refuses one, because a name every session "
        f"matches would take over questions belonging to askers it has never heard of"
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
        "plan_attached",
        "plan_detached",
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


@pytest.mark.parametrize("shape", ["plan_attached", "plan_detached"])
def test_every_plan_launch_gives_its_dispatch_the_plan_authoring_root_of_its_checkout(
    shape: str, request: pytest.FixtureRequest
) -> None:
    """A planning launch's dispatch reads the launching checkout's own plan-authoring root.

    `onetaskgraph.yaml` roots the `authoring` source at the relative `.plans`, and a
    planning launch dispatches its planner into a worktree of its own — so left alone,
    every process resolves that source somewhere different and the plan the planner
    authors lands where the launching checkout never looks. Read back out of the
    dispatch's own turn rather than off the launcher, for the reason the credential
    journey above reads a credential that way: a launcher that exported a name and a
    dispatch that received it are two different facts.

    `cast` because the shape is parametrized: `getfixturevalue` resolves the fixture by
    name at run time, which no static type can follow back to what it returns.
    """
    dispatch = cast(Dispatch, request.getfixturevalue(shape))
    assert Path(_given(dispatch, PLAN_ROOT)) == plan_store.source_root(AUTHORING), (
        f"the dispatch of {dispatch.run} was given a plan-authoring root that is not the "
        "directory this checkout's plan store resolves the `authoring` source to"
    )


def test_a_dispatch_of_a_plan_launch_resolves_the_authoring_source_to_that_root(
    plan_attached: Dispatch,
) -> None:
    """The store *inside* a dispatch answers with the launching checkout's directory.

    A variable in an environment is not the claim that matters: what a dispatched
    planner authors into is whatever its own plan store resolves the `authoring` source
    to, from inside a worktree whose tracked configuration roots that source relatively.
    So the process serving the dispatch runs the real store CLI where the dispatch runs
    and reports what it answered, and this reads that answer back.

    Both halves are asserted, because only the pair means anything: the root, and that
    the store attributes it to the *environment* — a launch that stopped exporting one
    would leave the worktree's own relative root answering, which is a different
    directory reported the same way.
    """
    assert plan_attached.store is not None, "this launch was not asked to report a store"
    assert plan_attached.store.is_file(), (
        f"the dispatch of {plan_attached.run} never reported its own plan store; the "
        "commands a turn runs are reported on the stand-in's stderr"
    )
    # `cast` because this is another program's answer arriving as JSON: the shape is
    # declared above and each field is narrowed as it is read rather than trusted.
    answered = cast(StoreConfiguration, json.loads(plan_attached.store.read_text(encoding="utf-8")))
    key = f"sources.{AUTHORING}.config.root"
    named = [setting for setting in answered["settings"] if setting["key"] == key]
    assert len(named) == 1, (
        f"the store inside a dispatch of {plan_attached.run} reported {len(named)} values "
        f"for {key}, so there is no one root a dispatched planner would author into"
    )
    resolved = named[0]
    root = resolved["value"]
    assert isinstance(root, str), f"the store answered {key} with {root!r} rather than a path"

    assert Path(root) == plan_store.source_root(AUTHORING), (
        f"a dispatch of {plan_attached.run} resolves the {AUTHORING!r} source to {root}, "
        "which is not the directory the launching checkout reads"
    )
    assert resolved["origin"] == {"layer": "environment", "variable": PLAN_ROOT.name}, (
        f"the root a dispatch resolves came from {resolved['origin']} rather than from "
        "the launch, so it is the worktree's own relative root answering"
    )


@pytest.mark.parametrize("shape", ["plan_attached", "plan_detached"])
def test_every_plan_launch_hands_its_dispatch_the_operational_appendix_itself(
    shape: str, request: pytest.FixtureRequest
) -> None:
    """A planning dispatch is given the appendix's text, not a path to it.

    Read back out of the dispatch's own turn rather than off the launcher, for the reason
    the credential journey does: a launcher that exported a name and a dispatch that
    received it are two different facts. What it is compared against is
    `criteria_guard.appendix_text()` — the same function `check_appendix` demands as a
    substring — because a launch that handed over a *second rendering* of that file, one
    trailing newline apart, would look right here and refuse every task the planner wrote.

    `cast` because the shape is parametrized: `getfixturevalue` resolves the fixture by
    name at run time, which no static type can follow back to what it returns.
    """
    dispatch = cast(Dispatch, request.getfixturevalue(shape))

    assert _given(dispatch, DISPATCH_APPENDIX) == criteria_guard.appendix_text(), (
        f"the dispatch of {dispatch.run} was given an appendix that is not the text "
        "`just check-plan` requires of every task, so a planner copying it in verbatim "
        "would still have every node refused"
    )


def test_a_plan_launch_keeps_a_plan_authoring_root_its_caller_already_chose(
    plan_over_a_chosen_root: Dispatch, plan_root_already_chosen: Path
) -> None:
    """A root already in the environment is the root the dispatch reads.

    The launch resolves this checkout's own unconditionally — that resolution is also
    what refuses a root no plan could be authored into — so the only thing that says the
    caller's choice survived it is reading the value back out of a dispatch.
    """
    assert Path(_given(plan_over_a_chosen_root, PLAN_ROOT)) == plan_root_already_chosen, (
        f"the dispatch of {plan_over_a_chosen_root.run} was given a plan-authoring root "
        "its launch resolved, rather than the one its caller had already chosen"
    )


def test_a_plan_launch_writes_its_own_project_under_the_root_its_caller_chose(
    plan_over_a_chosen_root: Dispatch, plan_root_already_chosen: Path
) -> None:
    """The launch's own project is written where the store will then look for it.

    This is the half a launch cannot survive getting wrong, and the one it used to get
    wrong: the recipe wrote its project to a `.plans` relative to the tree it ran in
    while `onepipeline start` resolved the configured root, so a caller who pointed that
    root anywhere else had their plan refused by the launch gate — `no project with that
    id` — moments after the launch reported writing it. The fixture reaching a dispatch
    at all is the other half of the proof, since nothing is dispatched until the gate has
    read this project.
    """
    written = plan_root_already_chosen / "projects" / f"{plan_over_a_chosen_root.run}.md"

    assert written.is_file(), (
        f"the launch of {plan_over_a_chosen_root.run} wrote no project under the root its "
        f"caller chose; {plan_root_already_chosen} holds "
        f"{sorted(path.name for path in plan_root_already_chosen.iterdir())}"
    )
    # And said so, which is the half a file on disk cannot answer: these journeys reuse
    # their run ids, so this checkout's own plan root holds a record of this name from
    # every earlier run of the suite, and a reader that only looked there could not tell
    # a launch that wrote to the wrong place from one that wrote to the right one beside
    # residue. What the launch reports is about this launch alone.
    assert plan_over_a_chosen_root.reported is not None, "this launch's stream was not read"
    assert f"wrote {AUTHORING}:{plan_over_a_chosen_root.run} at {written}" in (
        plan_over_a_chosen_root.reported
    ), (
        "the launch did not report writing its project under the root its caller chose:\n"
        f"{plan_over_a_chosen_root.reported}"
    )


def test_a_dispatch_over_a_chosen_root_resolves_its_store_to_that_root(
    plan_over_a_chosen_root: Dispatch, plan_root_already_chosen: Path
) -> None:
    """Inside the dispatch, the store answers with the directory the caller chose.

    The environment value reaching a dispatch and the dispatch's own store resolving it
    are two facts, and only the second is what a planner authors through. Both halves are
    asserted for the same reason the default-root journey asserts both: a root reported
    from the *file* layer would be the worktree's own relative `.plans`, which is a
    different directory read back the same way.
    """
    assert plan_over_a_chosen_root.store is not None, "this launch reported no store"
    assert plan_over_a_chosen_root.store.is_file(), (
        f"the dispatch of {plan_over_a_chosen_root.run} never reported its own plan store"
    )
    # `cast` for the reason the default-root journey casts: this is another program's
    # answer arriving as JSON, the shape is declared above, and each field is narrowed as
    # it is read rather than trusted.
    answered = cast(
        StoreConfiguration, json.loads(plan_over_a_chosen_root.store.read_text(encoding="utf-8"))
    )
    key = f"sources.{AUTHORING}.config.root"
    named = [setting for setting in answered["settings"] if setting["key"] == key]
    assert len(named) == 1, f"the store reported {len(named)} values for {key}"
    root = named[0]["value"]
    assert isinstance(root, str), f"the store answered {key} with {root!r} rather than a path"

    assert Path(root) == plan_root_already_chosen, (
        f"a dispatch of {plan_over_a_chosen_root.run} resolves the {AUTHORING!r} source to "
        f"{root}, which is not the root its caller chose"
    )
    assert named[0]["origin"] == {"layer": "environment", "variable": PLAN_ROOT.name}, (
        f"the root the dispatch resolved came from {named[0]['origin']} rather than from "
        "the launch, so it is the worktree's own relative root answering"
    )


def test_the_launching_checkout_reads_the_plan_its_dispatch_authored(
    plan_over_a_chosen_root: Dispatch, plan_root_already_chosen: Path
) -> None:
    """The last link: a plan written through that root comes back to the checkout.

    A root both sides agree on is worth nothing until this holds, and it is the whole
    reason the root is configured rather than guessed — the review, the design document
    and the copy onto the board all read the plan the planner authored, and each of them
    reads it from the launching checkout after the dispatch is gone.

    Read through the store rather than off the filesystem, and with this process pointed
    at the same root the launch pointed its dispatch at, because that is what an operator
    who chose the root has: the value is theirs, exported, and every command they then
    run resolves through it.
    """
    project = f"{AUTHORING}:{AUTHORED_PROJECT}"
    with pytest.MonkeyPatch.context() as reading:
        reading.setenv(PLAN_ROOT.name, str(plan_root_already_chosen))
        tasks = plan_store.read_tasks(project)

    assert [task.node_id for task in tasks] == ["route"], (
        f"the launching checkout could not read {project} back out of the root its "
        f"dispatch authored into: {tasks}"
    )


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
    "credentials-env.sh",
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


#: The helpers `scripts/plan.sh` sources before it writes anything. Each establishes
#: environment a dispatch cannot be launched without — the seam an agent puts a question
#: to its manager over, this checkout's own credentials, and the plan-authoring root a
#: dispatched planner writes into — and `plan.sh` reads each out of its helper rather
#: than resolving any of them itself. So a checkout missing one is a launch that cannot
#: establish it at all, which is a different missing piece from a helper that is there
#: but broken, and one that would otherwise surface as a shell error naming a file the
#: operator never asked about.
#: The grammar `scripts/plan.sh` reads its brief and its options through, which is not an
#: environment helper and so is not one of the cases below: it is sourced before any of
#: them, so a checkout without it never reaches the refusal each of these journeys is
#: about. Kept present in every one of them for that reason.
BRIEF_GRAMMAR = "plan-brief.sh"

LAUNCH_ENVIRONMENT_HELPERS = (
    "credentials-env.sh",
    "ask-manager-env.sh",
    "plan-root-env.sh",
    "dispatch-appendix-env.sh",
)


@pytest.mark.parametrize("missing", LAUNCH_ENVIRONMENT_HELPERS)
def test_a_plan_launch_without_a_helper_that_establishes_its_environment_writes_nothing(
    missing: str,
    tmp_path: Path,
) -> None:
    """The recipe refuses a checkout missing either helper, before it writes a plan.

    Driven by running the real recipe from a directory holding the recipe and every
    helper but one, so the refusal proved is the one that helper's absence produces
    rather than whichever check happens to come first.
    """
    scripts = tmp_path / "checkout" / "scripts"
    scripts.mkdir(parents=True)
    alone = scripts / "plan.sh"
    alone.write_bytes((REPO_ROOT / "scripts" / "plan.sh").read_bytes())
    alone.chmod(0o755)
    # `ask-manager.sh` beside them, because it is what the ask-manager helper resolves
    # and refuses over: without it every case past that helper reports its absence
    # instead of the one this case removed.
    kept = {*LAUNCH_ENVIRONMENT_HELPERS, "ask-manager.sh", BRIEF_GRAMMAR} - {missing}
    for present in sorted(kept):
        copied = scripts / present
        copied.write_bytes((REPO_ROOT / "scripts" / present).read_bytes())
        copied.chmod(0o755)
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF, encoding="utf-8")
    working = tmp_path / "working"
    working.mkdir()

    refused = subprocess.run(  # noqa: S603 - the real recipe, from a checkout without the helper
        [str(alone), str(brief), "--name", f"launch-seam-no-{missing.removesuffix('.sh')}"],
        cwd=working,
        env=dict(os.environ),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert refused.returncode != 0, refused.stdout
    assert "required helper is not a readable regular file" in refused.stderr, refused.stderr
    assert missing in refused.stderr, refused.stderr
    assert not (working / "scratch").exists(), (
        "a plan was written for a launch that cannot establish its dispatch environment"
    )


#: Every helper `scripts/plan.sh` sources, so each one's load failure is driven rather
#: than the first one's standing in for the rest. They are sourced in sequence and each
#: establishes a different part of the dispatch environment, so a reader cannot infer one
#: refusal from another: they went out of step exactly once, when the ask-manager source
#: was left to strict mode while the credentials source three lines above it was not, and
#: nothing here noticed because only the credentials half had a journey.
SOURCED_HELPERS = (
    "credentials-env.sh",
    "ask-manager-env.sh",
    "plan-root-env.sh",
    "dispatch-appendix-env.sh",
)


@pytest.mark.parametrize("corrupted", SOURCED_HELPERS)
def test_a_plan_launch_whose_helper_cannot_be_loaded_writes_nothing(
    tmp_path: Path, corrupted: str
) -> None:
    """A helper that is readable and then fails to load still refuses the launch, attributably.

    The check before the source answers "is the file there and readable", which a corrupt
    or half-written helper passes. Left to strict mode, what follows is a bare shell
    syntax error naming a file the operator never asked about and no action to take —
    beside diagnostics that name both. Driven through the real recipe, because the whole
    point is what an operator sees when they run it.
    """
    scripts = tmp_path / "checkout" / "scripts"
    scripts.mkdir(parents=True)
    alone = scripts / "plan.sh"
    alone.write_bytes((REPO_ROOT / "scripts" / "plan.sh").read_bytes())
    alone.chmod(0o755)
    intact = {*SOURCED_HELPERS, "ask-manager.sh", BRIEF_GRAMMAR} - {corrupted}
    for present in sorted(intact):
        copied = scripts / present
        copied.write_bytes((REPO_ROOT / "scripts" / present).read_bytes())
        copied.chmod(0o755)
    (scripts / corrupted).write_text("this is ( not valid bash\n", encoding="utf-8")
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF, encoding="utf-8")
    working = tmp_path / "working"
    working.mkdir()

    name = f"launch-seam-unloadable-{corrupted.removesuffix('.sh')}"
    refused = subprocess.run(  # noqa: S603 - the real recipe, against a helper that cannot load
        [str(alone), str(brief), "--name", name],
        cwd=working,
        env=dict(os.environ),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert refused.returncode != 0, refused.stdout
    assert "could not be loaded" in refused.stderr, refused.stderr
    assert corrupted in refused.stderr, refused.stderr
    assert "just bootstrap" in refused.stderr, refused.stderr
    assert not (working / "scratch").exists(), (
        f"a plan was written for a launch whose {corrupted} never loaded"
    )


#: A credentials file the loader refuses, and the phrase its refusal carries. Both are
#: things an operator really does — a line they typed wrongly, and a path they created as
#: the wrong kind of thing — as opposed to a file the launcher merely cannot resolve.
UNUSABLE_CREDENTIAL_FILES = (
    ("malformed line", "GH_PROJECTS_TOKEN=fine\nnot-an-assignment\n", "malformed credential line"),
    ("not a regular file", None, "is not a readable regular file"),
)


@pytest.mark.parametrize(
    ("label", "written", "expected"),
    UNUSABLE_CREDENTIAL_FILES,
    ids=[row[0] for row in UNUSABLE_CREDENTIAL_FILES],
)
def test_a_plan_launch_over_an_unusable_credentials_file_writes_nothing(
    label: str, written: str | None, expected: str, tmp_path: Path
) -> None:
    """A `.env` the loader refuses stops the plan launch, with the loader's own reason.

    The launcher propagates the loader's exit status rather than carrying on, because a
    dispatch launched without the credentials an operator placed for it fails much later
    and somewhere else — against GitHub, with nothing pointing back at the file. Driven
    through the real recipe over a checkout whose `.env` this journey controls, so what
    is proved is what an operator running `just plan` would see.
    """
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    alone = scripts / "plan.sh"
    alone.write_bytes((REPO_ROOT / "scripts" / "plan.sh").read_bytes())
    alone.chmod(0o755)
    for present in (
        BRIEF_GRAMMAR,
        "credentials-env.sh",
        "ask-manager-env.sh",
        "ask-manager.sh",
        "plan-root-env.sh",
    ):
        copied = scripts / present
        copied.write_bytes((REPO_ROOT / "scripts" / present).read_bytes())
        copied.chmod(0o755)
    credentials = checkout / ".env"
    if written is None:
        credentials.mkdir()
    else:
        credentials.write_text(written, encoding="utf-8")
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF, encoding="utf-8")
    working = tmp_path / "working"
    working.mkdir()

    refused = subprocess.run(  # noqa: S603 - the real recipe, over a `.env` it must refuse
        [str(alone), str(brief), "--name", f"launch-seam-unusable-{label.replace(' ', '-')}"],
        cwd=working,
        env=dict(os.environ),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert refused.returncode != 0, refused.stdout
    assert expected in refused.stderr, refused.stderr
    assert not (working / "scratch").exists(), (
        "a plan was written for a launch whose credentials file the loader refused"
    )


@pytest.mark.parametrize(
    ("label", "make"),
    [
        ("a file", lambda root: root.write_text("not a plan store\n", encoding="utf-8")),
        ("a read-only directory", lambda root: root.mkdir(mode=0o500)),
    ],
)
def test_a_plan_launch_over_an_unusable_plan_authoring_root_writes_nothing(
    label: str, make: Callable[[Path], object], tmp_path: Path
) -> None:
    """A root no plan could be authored into stops the launch before it writes or dispatches.

    The alternative is the failure this whole seam exists to prevent, one step later: the
    launch writes its project, opens a session, dispatches a planner, and that planner
    authors its plan into a directory nothing can write — or worse, into whatever it
    resolves for itself. Refusing costs one line and nothing else has happened yet.

    Driven through the real recipe in this checkout, so the resolution refusing is this
    repository's own rather than one a copied tree could only approximate; what the
    journey supplies is the root, which is the launch's own starting environment.
    """
    root = tmp_path / "plans"
    make(root)
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF, encoding="utf-8")
    working = tmp_path / "working"
    working.mkdir()
    runs = tmp_path / "runs"
    run = f"launch-seam-unusable-root-{label.replace(' ', '-')}"

    try:
        refused = subprocess.run(  # noqa: S603 - the real recipe, over a root it must refuse
            [str(REPO_ROOT / "scripts" / "plan.sh"), str(brief), "--name", run],
            cwd=working,
            env={
                **dict(os.environ),
                PLAN_ROOT.name: str(root),
                "ONEPIPELINE_RUNS_DIR": str(runs),
            },
            text=True,
            capture_output=True,
            timeout=e2e_timeout(120),
            check=False,
        )
    finally:
        if root.is_dir():
            root.chmod(0o700)

    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert str(root) in refused.stderr, refused.stderr
    assert PLAN_ROOT.name in refused.stderr, refused.stderr
    assert not (working / ".plans").exists(), (
        "a plan was written for a launch whose plan-authoring root cannot hold one"
    )
    assert not (runs / run).exists(), (
        "a run was started for a launch whose plan-authoring root cannot hold a plan"
    )
