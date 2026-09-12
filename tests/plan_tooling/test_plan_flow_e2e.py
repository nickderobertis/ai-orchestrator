"""`just plan` drives the whole planning flow, in the order the tooling enforces.

One launch writes the plan; the tail after it reviews that plan, checks it, launches the
one short document a person reviews the plan as, copies both into the destination, and
reports where that destination holds them. What that arrangement is worth is entirely in
those things happening in that order, and none of it can be read off the plan documents
the recipe writes:

* the plan is **reviewed before** the dispatch that writes the document starts, so the
  document is never written about content nothing had read. That is why the document is a
  second *launch* rather than a second node: a run cannot interject a review between its
  own nodes, because a review record is written by this repository's own code and never
  by a dispatched agent;
* the dispatch is really given the plan's qualified id, which is the only way it can find
  the plan at all — nothing hands one launch's output to the next, and a plan is found by
  asking the store rather than by reading a run;
* what that dispatch stores is afterwards **readable back out of the plan store as a
  document of that project**, with the store reporting where it is;
* and both of them land on the **destination**, whose own locations are what the flow
  reports — read back out of that store rather than composed from a project name, because
  a destination decides its own ids.

So the whole flow runs for real: the real `just plan`, the real `scripts/plan.sh` and
`scripts/finish-plan.sh`, the real `just review-plan` and its `oneharness` turn, the real
`orchestrator-check-plan`, `orchestrator-copy-plan` and `orchestrator-plan-locations`, the
real `onepipeline` driver, the real `graphs/design-doc.yaml`, a real registered identity,
and the real `onetaskgraph` at both ends.

**What is stood in for is the paid model's answer, and nothing downstream of it.** A
design-doc dispatch does two things: it writes the document's prose, and it stores that
prose as a document of the plan's project. The first is the model's, and it is scripted
here as a draft in a source of the dispatch's own — the same shape as every other scripted
answer in this suite. The second is `onetaskgraph`'s, and the dispatch really performs it:
the turn runs the store's own command line, in its own working directory, against the
store its own repository's tracked configuration names. So the record this journey reads
back is one the store created — its origin metadata says so, and nothing here composes it
— rather than a file a test planted where the store would have put one.

**The destination is a second local store and never the live board.** A journey that wrote
to the real `plans` board would be its own rate-limit burst and would leave a project
behind on the store every other run of this repository reads.

**Two flows run here, because naming a destination and naming none are two paths.** The
first is driven with `--to`, which is what a caller finishing a plan into a store of their
own types. The second names nothing at all, which is what an operator gets by typing the
recipe and a brief — and there the destination is not read off a flag but resolved by
`scripts/finish-plan.sh` running this repository's own code, so it is the one path through
the handover no flag can stand in for. That second flow runs in a copy of this checkout,
for the reason :func:`_the_board_is_a_directory` gives.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple, NewType, TypedDict, cast

import pytest
from fake_backend import (
    AUTHOR_PLAN_ENV,
    MEMBER_OF_CONFIG,
    PROMPT_LOG_ENV,
    RUN_ON_MARKER_ENV,
)
from nx_workspace import copy_working_tree
from plan_fixture_root import ROOT as FIXTURE_ROOT
from project_fixtures import helper
from published_tools import ONETASKGRAPH_BIN
from scratch_identity import PLANNING_FLOW_ORIGIN, seeded
from waits import timeout as e2e_timeout

from orchestrator import plan_copy, plan_review, plan_store
from orchestrator.criteria_guard import APPENDIX
from orchestrator.project_store import render_plan_project
from orchestrator.root import REPO_ROOT

#: This journey is its own Nx project's, `plan-tooling`, rather than a marker tier of the
#: orchestrator project: it drives a whole real planning flow — the installed
#: `onepipeline`, two real launches, a dispatched lifecycle node, a judged review turn and
#: a real store copy — which is a different cost from the Python suite beside it and is
#: answered by a different set of files. `tests/plan_tooling/project.json` names that set
#: as `planToolingWorkspace`, and `tests/conftest.py` holds these tests to it.

#: The stand-in for the paid model, and the provider binary beneath it. Reached through
#: `helper` rather than from this module's own directory, because a stand-in named at a
#: path this checkout does not have is not a stand-in: oneharness falls through to a real
#: identity and the journey spends real turns while passing.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")

#: The guard covering the identities `ONEHARNESS_BIN_*` cannot reach. `just review-plan`
#: runs inside this flow and spawns a real `oneharness run`, so without it a chain that
#: fell past codex would reach a paid identity and this journey would spend real turns
#: while passing.
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: What the scripted reviewer answers, once, for every task it is given: a pass carrying
#: no findings, because a finding *is* a refused criterion and the verdict schema admits a
#: pass only where it names none. `tests/e2e/fake_codex.py` repeats its last answer.
PASSING_VERDICT = json.dumps({"passes": True, "findings": []})

#: A launching session this journey states rather than inherits, and everything else an
#: enclosing dispatch would otherwise decide for it.
LAUNCHING_SESSION = "e2e-plan-flow"
INHERITED_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: The two aliases `scripts/plan.sh` defaults to, seeded here against a scratch registry
#: rather than overridden per launch: what is under test is the project this recipe writes
#: when nobody tells it anything.
PUBLICATION_ALIAS = "ai-orchestrator"
EXECUTION_ALIAS = "ai-orchestrator-isolated"

#: The source `onetaskgraph.yaml` roots at the shared fixture directory every tier of this
#: suite publishes into, which is the store both the planner stand-in writes the plan to
#: and `just plans` reads the document back out of.
FIXTURE_SOURCE = "test-fixtures"

#: The source both launches of a planning flow write their own generated project into,
#: which is where `onepipeline` then reads the plan it launches from.
AUTHORING_SOURCE = "authoring"

#: The one node `just plan` writes, and the one node its tail writes in a launch of its
#: own. Two projects and two runs, because the review between them is written by this
#: repository's own code and a run cannot interject one between its own nodes.
PLANNER_NODE = "plan"
DESIGN_NODE = "design-doc"

#: What the tail's own run and project are called, derived from the flow's name. Spelled
#: here as the suffix a supervisor reads off the launch rather than imported from the
#: shell that composes it: what this asserts is the id an operator is handed.
DESIGN_RUN_SUFFIX = "-design"

#: The second local Markdown store this flow copies into, standing in for the `plans`
#: board. A plain lowercase name because it is spelled into the store's own
#: `ONETASKGRAPH_SOURCES__…` environment layer as well as onto a command line.
DESTINATION = "destination"

#: The `graphs/*.yaml` member a dispatched node runs as, either side of it.
WORKER_MEMBER = "worker"

#: The journal records this reads, and the label that says which node produced one.
NODE_DISPATCHED = "node-dispatched"
NODE_SETTLED = "node-settled"
NODE_LABEL = "node"

#: The fragment only the design-doc node's task carries, which is what tells the two
#: dispatches apart at the stand-in: both run as `worker`, so the member cannot.
DESIGN_TASK_MARKER = "## What this dispatch owes"

#: The one statement of the document's shape, repository-relative inside this checkout —
#: the dispatch works in the checkout the flow was launched from, which may be a checkout
#: of the repository the *plan* is of, so this is a file it is sent to by name rather
#: than one it finds underfoot.
DESIGN_TEMPLATE = "config/design-doc-template.md"

#: Where that template's lent block ends. The marker's own bytes, because the probe below
#: appends a line *inside* that block and a drifted copy would place it outside.
LIFT_CLOSE = "<!-- end composed-into-the-dispatch -->"

#: What the dispatched task has to require of the architecture: the condition, since a
#: plan inside one repository is exempt, and the demand. Stated here rather than read out
#: of the template, which is the assertion rather than a shortcut — a journey that built
#: its expectation from that file would pass whatever the two said, including saying
#: nothing to each other.
ARCHITECTURE_NAMES_ITS_REPOSITORIES = "Where the plan spans more than one repository"
ARCHITECTURE_NAMES_ITS_REPOSITORIES_DEMAND = (
    "the architecture names the repository each piece lives in"
)

#: The source a design-doc dispatch drafts into before storing: one of its own, named on
#: its own command line rather than in any tracked configuration, which is what an agent's
#: scratch directory is. The plan store is the *destination*, and it is never named here —
#: the dispatch takes it from the configuration its own repository tracks.
DRAFT_SOURCE = "draft"

#: The key `onetaskgraph` stamps on a record it created by copying, naming what it copied.
#: Reading it is how this journey tells a document the store wrote from one a test placed
#: where the store would have written it: nothing here composes this, and a planted file
#: would not carry it.
ORIGIN_KEY = "onetaskgraph.origin"

RunId = NewType("RunId", str)


class JournalEvent(TypedDict):
    """One record the run appended, in the fields this journey reads.

    `ts` is the record's own stamp rather than anything inside its payload: the ordering
    this journey asserts spans two writers — the review record in the plan's task document
    and the dispatch in the run's journal — so what is compared is when each was written.
    """

    ts: str
    kind: str
    labels: dict[str, str]
    payload: dict[str, str]


class TurnRecord(TypedDict):
    """One recorded harness turn. `tests/e2e/fake_backend.py` owns this schema."""

    config: str | None
    prompt: str
    system: str
    environment: dict[str, str | None]


class StandInGoal(TypedDict):
    """The goal a generated plan carries. `onepipeline` owns the contract."""

    text: str


class StandInNode(TypedDict):
    """One node the planner stand-in authors, in the fields it states.

    It carries a `persona` and criteria answering every demand the tracked appendix and
    the shipped `engineer` bar make, because the flow this journey drives *checks* this
    plan before it writes a document about it: a stand-in plan that could not pass `just
    check-plan` would end the flow at that refusal rather than at anything under test.

    `repo` is here because the plan this journey authors deliberately spans two
    repositories: `render_plan_project` turns a `host/owner/name` value into the task
    record's own `repositories`, which is what makes the stored plan a multi-repository
    one rather than a single-repository one with two nodes.
    """

    id: str
    persona: str
    title: str
    task: str
    repo: str


class StandInPlan(TypedDict):
    """The plan document the planner stand-in writes, stated rather than left open.

    `render_plan_project` takes any mapping and copies unknown keys through, so an open
    dict would compile — but this is a document this journey composes in full, and the
    fields it has to get right (a `schema_version` the loader accepts, and tasks carrying
    the fields the store renders) are exactly what a name for it makes checkable.
    """

    schema_version: int
    goal: StandInGoal
    name: str
    tasks: list[StandInNode]


class Stored(NamedTuple):
    """What the planner stand-in authors, and what the design-doc dispatch stores."""

    #: The plan's native id inside the fixture source, and its qualified form.
    project: str
    qualified: str
    #: The plan's two tasks, so the document has rows to point at.
    task_title: str
    second_task_title: str
    #: The repository each of those two tasks lands in. Two different ones, because the
    #: architecture requirement this journey reads is conditional on a plan spanning more
    #: than one — a plan inside one repository is the case the template exempts.
    repository: str
    second_repository: str
    #: The document's native id inside that source, its qualified form, and its file.
    document: str
    document_qualified: str
    document_path: Path


class Planned(NamedTuple):
    """One whole `just plan` flow, and everything read back off it."""

    launch: subprocess.CompletedProcess[str]
    #: The planner run's journal, and the design run's, which are two runs now: the
    #: review between them is written by this repository's own code, so the document is
    #: a second launch rather than a second node.
    journal: list[JournalEvent]
    design_journal: list[JournalEvent]
    turns: list[TurnRecord]
    stored: Stored
    #: The destination's root, so a journey can read what the copy left there.
    destination: Path
    #: The environment the flow ran in, so a read afterwards answers about the same
    #: store configuration the copy wrote through.
    environment: dict[str, str]


def _plan_records(stored: Stored) -> dict[str, str]:
    """The files a planner authoring ``stored``'s plan into the fixture root would write.

    Rendered through this repository's own `render_plan_project`, so the stand-in leaves
    the same records `just plan`'s own writer leaves and the store reads them the same way
    — rather than a hand-written shape only this journey would ever produce.
    """
    plan: StandInPlan = {
        "schema_version": 3,
        "goal": {"text": "Decide the cursor's shape"},
        "name": stored.project,
        "tasks": [
            {
                "id": "decide-the-cursor",
                "persona": "engineer",
                "title": stored.task_title,
                "repo": stored.repository,
                "task": (
                    "## What\n\nAdd the paginated listing and the test that drives it.\n\n"
                    "## Why\n\nAn operator cannot see past the first screen of nodes.\n\n"
                    "## Acceptance criteria\n\n"
                    "- The route accepts a valid request and rejects an invalid one.\n"
                    "- A request-level test drives the route end to end and covers both "
                    "paths.\n"
                    "- Every claim the dispatch makes about the finished work is true of "
                    "the tree as it finally stands.\n\n"
                    f"{(REPO_ROOT / APPENDIX).read_text(encoding='utf-8').strip()}\n"
                ),
            },
            {
                "id": "read-the-cursor",
                "persona": "engineer",
                "title": stored.second_task_title,
                "repo": stored.second_repository,
                "task": (
                    "## What\n\nFollow the stated cursor from the browser view.\n\n"
                    "## Why\n\nThe shape is worth nothing until something reads it.\n\n"
                    "## Acceptance criteria\n\n"
                    "- The view pages on the stated cursor and reports a rejected one.\n"
                    "- A browser-level test drives both of those paths end to end.\n"
                    "- Every claim the dispatch makes about the finished work is true of "
                    "the tree as it finally stands.\n\n"
                    f"{(REPO_ROOT / APPENDIX).read_text(encoding='utf-8').strip()}\n"
                ),
            },
        ],
    }
    return {
        str(FIXTURE_ROOT / relative): content
        for relative, content in render_plan_project(plan, native_id=stored.project).items()
    }


def _document(stored: Stored) -> str:
    """The prose a design-doc dispatch drafted, as a record of the source it drafted into.

    The paid model's answer and nothing else: what makes it a document *of the plan's
    project* in the plan store is the copy the dispatch performs, not this.
    """
    return (
        f'---\ntitle: "Design: {stored.project}"\n'
        f'project: "{stored.project}"\n---\n\n'
        "## What\n\nA paginated node listing.\n\n"
        "## Why\n\nAn operator cannot see past the first screen.\n\n"
        "## Architecture\n\nOne route, one view.\n\n"
        "## Contracts\n\nThe cursor is an opaque token.\n\n"
        "## Acceptance criteria\n\nThe listing pages.\n\n"
        "## Planned tasks\n\n"
        "| Task | What it delivers | Depends on | Where it lives |\n"
        "| --- | --- | --- | --- |\n"
        f"| {stored.task_title} | the route | none | the store's own location |\n"
        f"| {stored.second_task_title} | the view | {stored.task_title} | "
        "the store's own location |\n"
    )


def _staged_draft(tmp_path: Path, stored: Stored) -> Path:
    """Stage the document's prose where the design-doc dispatch drafted it.

    This is the scripted half and the whole of it: the prose a paid model would have
    written, staged before the launch exactly as every other scripted answer in this suite
    is. **Storing it is not staged.** The plan store holds no document until the dispatch
    itself runs the store's own command line, so what the read-back at the end proves is
    that the dispatch ran it.

    Answers the root of the source the draft is a document of, which is what the dispatch
    names on that command line.
    """
    documents = tmp_path / "drafted" / "documents"
    documents.mkdir(parents=True)
    (documents / f"{stored.document}.md").write_text(_document(stored), encoding="utf-8")
    return documents.parent


def _environment(
    tmp_path: Path,
    stored: Stored,
    *,
    destination: str,
    declared_at: Path | None,
    checkout: Path = REPO_ROOT,
) -> dict[str, str]:
    """The environment a flow runs in, against a registry, a runs root and a board of its own.

    ``destination`` is the configured source the flow copies its plan into, and
    ``declared_at`` is the directory this journey declares that source at through the
    store's own environment layer — or `None` when the running checkout's own
    `onetaskgraph.yaml` is what declares it. That second case is the whole of what the
    default-board journey below is about: a source *this* layer declared would answer for
    a name the recipe was told, and what is under test there is the name it resolves when
    it is told none.
    """
    identity = seeded(
        tmp_path,
        publication=PUBLICATION_ALIAS,
        execution=EXECUTION_ALIAS,
        origin=PLANNING_FLOW_ORIGIN,
    )
    environment = dict(os.environ)
    for name in INHERITED_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEVCS_HOME"] = str(identity.home)
    environment.update(identity.environment)
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # And the identities that seam cannot reach; see `tests/e2e/no_paid_provider.py`.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment["FAKE_CODEX_ANSWERS"] = json.dumps([PASSING_VERDICT])
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    # The destination the flow copies into, added through the store's own environment
    # layer rather than through a `--set` flag: that layer is the one every part of the
    # flow sees — the copy, the location read after it, and this journey's own reads —
    # so all of them answer about one configuration.
    if declared_at is not None:
        environment[f"ONETASKGRAPH_SOURCES__{destination.upper()}__PLUGIN"] = "local-md"
        environment[f"ONETASKGRAPH_SOURCES__{destination.upper()}__CONFIG__ROOT"] = str(declared_at)
    # `authoring` is in this list because both launches of this flow read their plan out
    # of it: a run whose plan store cannot see that source reads a project with no tasks.
    environment["ONETASKGRAPH_DEFAULT_SOURCES"] = (
        f"{AUTHORING_SOURCE},{FIXTURE_SOURCE},{destination}"
    )

    authored = tmp_path / "authored-plan.json"
    authored.write_text(json.dumps(_plan_records(stored)), encoding="utf-8")
    environment[AUTHOR_PLAN_ENV] = str(authored)
    keyed = tmp_path / "commands-per-marker.json"
    drafted = _staged_draft(tmp_path, stored)
    keyed.write_text(
        json.dumps(
            {
                DESIGN_TASK_MARKER: [
                    [
                        str(ONETASKGRAPH_BIN),
                        "--set",
                        f"sources.{DRAFT_SOURCE}.plugin=local-md",
                        "--set",
                        f"sources.{DRAFT_SOURCE}.config.root={drafted}",
                        "document",
                        "copy",
                        f"{DRAFT_SOURCE}:{stored.document}",
                        "--to",
                        FIXTURE_SOURCE,
                    ]
                ]
            }
        ),
        encoding="utf-8",
    )
    environment[RUN_ON_MARKER_ENV] = str(keyed)
    return environment


def _just(
    *args: str,
    environment: dict[str, str],
    seconds: float = 600,
    checkout: Path = REPO_ROOT,
) -> subprocess.CompletedProcess[str]:
    """Run one real recipe from ``checkout``, which is this one unless a journey copied it."""
    return subprocess.run(
        ["just", *args],
        cwd=checkout,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


#: The run this module launches under, named once so its plan and its records are
#: unambiguously its own. The tail's own run is derived from it, which is what lets a
#: supervisor holding only this launch's output reach either channel.
RUN = RunId("plan-flow-e2e")
DESIGN_RUN = RunId(f"{RUN}{DESIGN_RUN_SUFFIX}")


@pytest.fixture(scope="module")
def planned(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Planned:
    """Drive one whole planning flow, attached, and hand every question its record.

    One flow for every claim here rather than one each: the ordering, the task the second
    launch's dispatch was given, the document it left in the store, and what the copy put
    on the destination are four readings of one act, and a fixture per claim would pay for
    two whole launches to re-prove them.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("plan-flow")
    unique = f"test-{os.getpid()}-plan-flow"
    stored = Stored(
        project=unique,
        qualified=f"{FIXTURE_SOURCE}:{unique}",
        task_title="feat: page the node listing",
        second_task_title="feat: follow the cursor from the view",
        repository="github.com/nickderobertis/onepipeline",
        second_repository="github.com/nickderobertis/onepipeline-ui",
        document=f"{unique}-document",
        document_qualified=f"{FIXTURE_SOURCE}:{unique}-document",
        document_path=FIXTURE_ROOT / "documents" / f"{unique}-document.md",
    )
    destination = tmp_path / "board"
    # Created rather than left to the first write: a `local-md` source canonicalizes its
    # root when it is built, so an absent one is refused as a broken source rather than
    # populated — which would report a sound copy as a destination that refused it.
    destination.mkdir(parents=True)
    environment = _environment(tmp_path, stored, destination=DESTINATION, declared_at=destination)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    turns = tmp_path / "turns.jsonl"
    environment[PROMPT_LOG_ENV] = str(turns)
    brief = tmp_path / "cursor-shape.md"
    brief.write_text(
        "## What\nDecide the cursor's shape.\n\n"
        f"Plan project: {stored.qualified}\n\n"
        "## Why\nThe view cannot deep-link until it is settled.\n\n"
        "## Acceptance criteria\n- The cursor's shape and its type are stated.\n",
        encoding="utf-8",
    )
    try:
        launch = _just(
            "plan", str(brief), "--name", RUN, "--to", DESTINATION, environment=environment
        )
        assert launch.returncode == 0, f"the flow failed:\n{launch.stdout}\n{launch.stderr}"
        runs = Path(environment["ONEPIPELINE_RUNS_DIR"])
        journal = runs / RUN / "events.jsonl"
        design_journal = runs / DESIGN_RUN / "events.jsonl"
        assert journal.is_file(), f"the launch recorded no journal at {journal}"
        assert design_journal.is_file(), (
            f"the tail recorded no journal at {design_journal}, so the flow never reached "
            f"the launch that writes the document"
        )
        assert turns.is_file(), f"the flow reached no harness turn, so {turns} is absent"
        return Planned(
            launch=launch,
            # `cast` rather than a validating read: `onepipeline` owns the journal's
            # record contract and `JournalEvent` states only the fields read here, so a
            # record missing one fails at the subscript that wanted it.
            journal=[
                cast(JournalEvent, json.loads(line))
                for line in journal.read_text(encoding="utf-8").splitlines()
            ],
            design_journal=[
                cast(JournalEvent, json.loads(line))
                for line in design_journal.read_text(encoding="utf-8").splitlines()
            ],
            turns=[
                cast(TurnRecord, json.loads(line))
                for line in turns.read_text(encoding="utf-8").splitlines()
            ],
            stored=stored,
            destination=destination,
            environment=environment,
        )
    finally:
        for ended in (RUN, DESIGN_RUN):
            _just("stop", ended, environment=environment, seconds=60)
            (REPO_ROOT / ".plans" / "projects" / f"{ended}.md").unlink(missing_ok=True)
            tasks = REPO_ROOT / ".plans" / "tasks" / ended
            if tasks.is_dir():
                for record in tasks.iterdir():
                    record.unlink(missing_ok=True)
                tasks.rmdir()


def _member(turn: TurnRecord) -> str | None:
    """Which member took a turn, read the way the stand-in reads it.

    `oneagentgraph` names every member's scratch after it and pins that member's configs
    inside it, so the recorded `--config` is the only thing that says whose turn this is.
    """
    named = MEMBER_OF_CONFIG.search(turn["config"] or "")
    return None if named is None else named.group(1)


def _timestamps(journal: list[JournalEvent], kind: str, node: str) -> list[datetime]:
    """When a run recorded ``kind`` for ``node``, in the order it recorded them.

    The journal's own stamps rather than positions, because the two halves of the
    ordering this journey asserts are recorded by two different writers into two
    different files: the review record goes into the plan's own task document and the
    dispatch goes into the run's journal, so an index in one says nothing about the
    other.

    Parsed rather than compared as text, because the two writers spell an instant
    differently — one ends `Z` and the other `+00:00` — and a string comparison of those
    would answer about the spelling rather than about the order.
    """
    return [
        datetime.fromisoformat(event["ts"])
        for event in journal
        if event.get("kind") == kind and event.get("labels", {}).get(NODE_LABEL) == node
    ]


@pytest.mark.xdist_group("plan-flow")
def test_the_plan_is_reviewed_before_the_dispatch_that_writes_its_document_starts(
    planned: Planned,
) -> None:
    """The ordering the flow exists for, read off the two records that carry it.

    A design document describes a plan, so a document written before anything reviewed
    that plan describes content nobody read — which is the failure the review gate one
    step earlier exists to prevent. It is a *launch* ordering rather than a node
    dependency because it has to be: a review record is written by this repository's own
    code and never by a dispatched agent, so no arrangement of nodes inside one run can
    put one between them.

    Read from the review record's own timestamp against the design run's own dispatch
    record, which are written by the two parties in question. A tree in which the design
    dispatch could start first and be reviewed afterwards fails here, because that
    ordering is the assertion rather than a side effect of it.
    """
    tasks = plan_store.read_tasks(planned.stored.qualified)
    assert tasks, f"the flow copied {planned.stored.qualified} with no tasks at all"
    stamps = []
    for task in tasks:
        record = task.metadata.get(plan_review.RECORD_KEY)
        assert isinstance(record, dict), (
            f"the flow copied a plan whose task {task.node_id} carries no review record "
            f"at all: {task.metadata}"
        )
        stamped = record.get("reviewed_at")
        assert isinstance(stamped, str), record
        stamps.append(datetime.fromisoformat(stamped))
    # The last of them, because what has to precede the dispatch is the whole plan
    # having been read: a document written after one task was reviewed and before the
    # next describes content the review had not reached.
    reviewed_at = max(stamps)

    dispatched = _timestamps(planned.design_journal, NODE_DISPATCHED, DESIGN_NODE)
    assert dispatched, (
        "the design run never dispatched its node, so the flow produced the plan and no document"
    )
    assert reviewed_at < dispatched[0], (
        f"the plan was reviewed at {reviewed_at} and the dispatch that writes the document "
        f"about it started at {dispatched[0]}, so the document describes content the "
        f"review had not yet read"
    )
    assert _timestamps(planned.design_journal, NODE_SETTLED, DESIGN_NODE), (
        "the design-doc node never settled, so the flow did not carry it to an end"
    )


@pytest.mark.xdist_group("plan-flow")
def test_the_tail_runs_under_a_run_of_its_own_whose_channel_the_launch_names(
    planned: Planned,
) -> None:
    """Two launches means two channels, and a supervisor is handed both.

    Each run's questions are answered on that run's own channel, so a flow that printed
    one id and used another would leave a blocking question queued where nobody is
    watching. Both lines are read off this flow's own output, which is all a supervisor
    has.
    """
    reported = planned.launch.stdout + planned.launch.stderr
    assert f"just channel-next {RUN}" in reported, (
        f"the flow never named the command that answers the planner's questions:\n{reported}"
    )
    assert f"just channel-next {DESIGN_RUN}" in reported, (
        f"the flow never named the command that answers the document dispatch's "
        f"questions, so its channel is one a supervisor has to compose:\n{reported}"
    )


@pytest.mark.xdist_group("plan-flow")
def test_the_design_doc_dispatch_is_given_the_plan_the_brief_named(planned: Planned) -> None:
    """The one thing that dispatch cannot derive reaches it in its own effective prompt.

    A plan lives in a store the dispatch has to be told the address of: nothing hands one
    node's output to a later node, and the planner's own run is over by the time this
    one starts. The plan document says the recipe composed the id into the task; only the
    prompt says the dispatch was given it, which is the half a recipe can get wrong while
    every read of what it wrote still passes.
    """
    # llmlint: ignore-block[tests_mirror_real_usage] The effective prompt is the only
    # place a dispatched task is observable; no published view carries it — `plan.json`
    # says what the recipe composed, which is the half this test exists to look past.
    # The fake backend writes these records itself and `TurnRecord` states the schema it
    # owns, so this reads a test-owned file rather than past somebody else's validation.
    dispatched = [
        turn["prompt"]
        for turn in planned.turns
        if _member(turn) == WORKER_MEMBER and DESIGN_TASK_MARKER in turn["prompt"]
    ]
    # llmlint: ignore-end[tests_mirror_real_usage]
    assert dispatched, (
        "no dispatched turn carried the design-doc node's own task, so nothing here is "
        "about that dispatch at all"
    )
    assert all(planned.stored.qualified in prompt for prompt in dispatched), (
        f"the design-doc dispatch was never given the plan {planned.stored.qualified}, so "
        f"it has no way to find what it is writing about:\n{dispatched[0]!r}"
    )


@pytest.mark.xdist_group("plan-flow")
def test_the_design_doc_dispatch_is_required_to_name_each_pieces_repository(
    planned: Planned,
) -> None:
    """A plan across two repositories owes a reader which change lands where.

    Read where the requirement is consumed, which is the dispatched task itself: the flow
    lifts that criterion out of `config/design-doc-template.md` as it composes the task, so
    the template stays the one statement of what the document is judged on while the
    dispatch still meets the requirement in the text its judge reads. A pointer alone was
    not that — the document that came back obeyed everything except the part nobody had put
    in front of it, naming each piece by role and no repository at all.

    So nothing here opens that template; asserting its own words back out of a real
    launch's prompt is what keeps the two from agreeing by saying nothing to each other.
    The plan is read back from the store too, because the requirement is conditional and a
    journey spanning one repository would exercise the exempt case instead.
    """
    # llmlint: ignore-block[tests_mirror_real_usage] The effective prompt is the only place
    # a dispatched task is observable; no published view carries it. The fake backend
    # writes these records itself and `TurnRecord` states the schema it owns.
    dispatched = [
        turn["prompt"]
        for turn in planned.turns
        if _member(turn) == WORKER_MEMBER and DESIGN_TASK_MARKER in turn["prompt"]
    ]
    # llmlint: ignore-end[tests_mirror_real_usage]
    assert dispatched, (
        "no dispatched turn carried the design-doc node's own task, so nothing here is "
        "about that dispatch at all"
    )

    spanned = {
        repository
        for task in plan_store.read_tasks(planned.stored.qualified)
        for repository in task.repositories
    }
    assert len(spanned) > 1, (
        f"the plan this dispatch was given lands in {sorted(map(str, spanned))}, so it "
        "does not span more than one repository and this journey is exercising the case "
        "the requirement exempts rather than the case it is about"
    )

    for prompt in dispatched:
        flat = " ".join(prompt.split())
        assert DESIGN_TEMPLATE in flat, (
            f"the design-doc dispatch was never sent to {DESIGN_TEMPLATE}, which is the "
            f"one statement of the document's shape:\n{prompt!r}"
        )
        assert ARCHITECTURE_NAMES_ITS_REPOSITORIES in flat, (
            "the design-doc dispatch's own task states no condition on a plan spanning "
            "repositories, so a plan across two of them is dispatched under the same task "
            f"as one inside a single repository:\n{prompt!r}"
        )
        assert ARCHITECTURE_NAMES_ITS_REPOSITORIES_DEMAND in flat, (
            "the design-doc dispatch's own task never asks the architecture to name the "
            "repository each piece lives in, so a reader of a plan across two of them "
            f"cannot tell which change lands where:\n{prompt!r}"
        )


@pytest.mark.xdist_group("plan-flow")
def test_the_stored_document_reads_back_as_a_document_of_the_plans_own_project(
    planned: Planned,
) -> None:
    """The document is where the plan is, and the store says where that is.

    This is the whole point of storing it rather than reporting it: a person reviewing a
    plan finds the document beside it, and the pointer they follow is the store's own
    answer rather than a path somebody composed. Read through `just plans`, which is this
    host's plan-store surface, so what passes here is what an operator would see.

    The location is read as well as the record, because a document nothing can say the
    whereabouts of is one a reviewer cannot open — and a `local-md` source answers a
    **path**, which is one of the two forms this repository's rows are written from.

    And the origin the store stamped is read, which is what makes this a reading of the
    dispatch rather than of the fixture. Nothing here writes into the plan store: the
    prose was staged in a source of the dispatch's own, and this record exists because the
    dispatch ran `onetaskgraph document copy` against the store its repository names. That
    stamp is the store's own account of having done it, and a file placed where the store
    would have put one carries nothing of the kind.
    """
    stored = planned.stored
    assert stored.document_path.is_file(), (
        f"the design-doc dispatch left no document at {stored.document_path}"
    )
    listed = _just(
        "plans",
        "document",
        "list",
        "--source",
        FIXTURE_SOURCE,
        "--project",
        stored.project,
        "--json",
        environment=dict(os.environ),
        seconds=120,
    )
    assert listed.returncode == 0, f"the store could not be read:\n{listed.stderr}"
    # llmlint: ignore[suppressions_justified] onetaskgraph owns this open JSON schema; the
    # fields read below are narrowed at each subscript.
    payload = cast(dict[str, Any], json.loads(listed.stdout))
    found = [record for record in payload["items"] if record["id"] == stored.document_qualified]
    assert found, (
        f"{stored.document_qualified} is not a document of project {stored.project}; the "
        f"store answered {[record['id'] for record in payload['items']]}"
    )
    item = found[0]["item"]
    assert item["project"] == stored.project, item
    assert item["location"] == {"path": str(stored.document_path)}, (
        f"the store reports the document at {item['location']!r}; a reviewer following "
        f"that pointer does not reach {stored.document_path}"
    )
    assert item["metadata"].get(ORIGIN_KEY) == f"{DRAFT_SOURCE}:{stored.document}", (
        f"the store records this document's origin as {item['metadata'].get(ORIGIN_KEY)!r} "
        f"rather than the draft the dispatch copied, so the record in the plan store was "
        "not written by the store's own copy of that draft"
    )


@pytest.mark.xdist_group("plan-flow")
def test_the_flow_leaves_the_plan_and_its_one_document_on_the_destination_it_was_given(
    planned: Planned,
) -> None:
    """What a person opens is on the destination, both halves of it.

    The plan alone is not reviewable — a person reads the document, and their approval of
    it is what gates the dispatch — so a flow that copied the project and left the
    document where it was drafted would put a plan on the board that can never be
    approved, and so one that can never be launched. Read as the records the destination
    holds rather than as a report of them, because the report is what is under test in the
    sibling below.
    """
    landed = sorted(
        str(one.relative_to(planned.destination)) for one in planned.destination.rglob("*.md")
    )
    project = planned.stored.project
    assert landed == [
        f"documents/{planned.stored.document}.md",
        f"projects/{project}.md",
        f"tasks/{project}/decide-the-cursor.md",
        f"tasks/{project}/read-the-cursor.md",
    ], f"the flow left {landed} on the destination"


@pytest.mark.xdist_group("plan-flow")
def test_the_flow_reports_where_the_destination_holds_the_project_and_the_document(
    planned: Planned,
) -> None:
    """The two locations a reviewer opens, and they are the store's own answers.

    Composing them is what this must not do: a destination decides its own native ids and
    where its records live — a board mints a number where a directory keeps the name — so
    a location assembled from a project name is one that names nothing on the destination
    this repository actually copies into. What is asserted is therefore that each reported
    location is the one the store reports for the record that landed, read back out of the
    destination through the store's own surface.
    """
    reported = planned.launch.stdout + planned.launch.stderr
    listed = _just(
        "plans",
        "project",
        "list",
        "--source",
        DESTINATION,
        "--json",
        environment=planned.environment,
        seconds=120,
    )
    assert listed.returncode == 0, f"the destination could not be read:\n{listed.stderr}"
    # llmlint: ignore[suppressions_justified] onetaskgraph owns this open JSON schema; the
    # fields read below are narrowed at each subscript.
    projects = cast(dict[str, Any], json.loads(listed.stdout))["items"]
    landed = [
        one
        for one in projects
        if one["item"]["metadata"].get(ORIGIN_KEY) == planned.stored.qualified
    ]
    assert landed, (
        f"the destination holds no project the store records as copied from "
        f"{planned.stored.qualified}; it holds {[one['id'] for one in projects]}"
    )
    where = landed[0]["item"]["location"]["path"]
    assert f"holds the plan at {where}" in reported, (
        f"the flow never reported {where}, which is where the store says the destination "
        f"holds this plan:\n{reported}"
    )

    documents = _just(
        "plans",
        "document",
        "list",
        "--project",
        landed[0]["id"],
        "--json",
        environment=planned.environment,
        seconds=120,
    )
    assert documents.returncode == 0, f"the destination could not be read:\n{documents.stderr}"
    # llmlint: ignore[suppressions_justified] onetaskgraph owns this open JSON schema; the
    # fields read below are narrowed at each subscript.
    held = cast(dict[str, Any], json.loads(documents.stdout))["items"]
    assert len(held) == 1, f"the destination holds {len(held)} documents of this plan: {held}"
    assert f"holds its design document at {held[0]['item']['location']['path']}" in reported, (
        f"the flow never reported where the destination holds the document a person "
        f"reviews this plan as:\n{reported}"
    )


#: The source `scripts/finish-plan.sh` copies into when the caller names none, taken from
#: the one place that states it rather than spelled a second time here. Naming it this way
#: is what makes the journey below about the default rather than about the string `plans`.
DEFAULT_BOARD = plan_copy.BOARD

#: A criterion carrying the two characters a Bash `//` replacement does not leave alone,
#: appended to the *copied* checkout's template so the flow lifts it like any other line.
#: `&` and `\` rather than an arbitrary odd string, because those two are the whole of the
#: hazard the splice in `scripts/finish-plan.sh` is written against; that site states why.
#:
#: Written into the template here rather than asserted of the shipped one, because a
#: template author is who writes this in reality and what is under test is that *whatever*
#: goes between those markers reaches the dispatch verbatim. The shipped criterion carries
#: neither character today, which is why nothing caught this.
LIFT_METACHARACTER_PROBE = (
    "- A lifted criterion reaches the dispatch verbatim: read & write the c:\\plans path."
)


def _lends_a_criterion_carrying_metacharacters(checkout: Path) -> None:
    """Append :data:`LIFT_METACHARACTER_PROBE` inside that checkout's own lent block.

    Appended rather than substituted for the shipped criterion, so the sibling journey
    reading the shipped requirement's own words out of a dispatched task keeps its
    subject: both lines are lent, and each is read for a different property.
    """
    template = checkout / DESIGN_TEMPLATE
    text = template.read_text(encoding="utf-8")
    assert text.count(LIFT_CLOSE) == 1, (
        f"the copied {DESIGN_TEMPLATE} carries {text.count(LIFT_CLOSE)} closing markers "
        "rather than one, so this journey cannot say where the lent block ends"
    )
    template.write_text(
        text.replace(LIFT_CLOSE, f"{LIFT_METACHARACTER_PROBE}\n{LIFT_CLOSE}"), encoding="utf-8"
    )


#: The default-board flow's own run, and the tail's derived from it.
DEFAULT_RUN = RunId("plan-flow-default-e2e")
DEFAULT_DESIGN_RUN = RunId(f"{DEFAULT_RUN}{DESIGN_RUN_SUFFIX}")

#: What a journey building a copy of this checkout reads: everything git tracks, since
#: that is what it copies and hands a real tool. So it stays in the whole-workspace tier
#: rather than joining the narrow key this project's other journeys are memoized on.
COPIES_THE_TRACKED_TREE = pytest.mark.reads_docs


class Defaulted(NamedTuple):
    """One whole `just plan` run that named no destination, and what it left behind."""

    launch: subprocess.CompletedProcess[str]
    stored: Stored
    #: The directory the copied checkout calls :data:`DEFAULT_BOARD`.
    board: Path
    #: The checkout the flow ran in, so a read afterwards asks the same configuration.
    checkout: Path
    environment: dict[str, str]
    #: Every turn this flow's stand-in recorded, so what the design-doc dispatch was
    #: really given is readable rather than inferred from the flow having succeeded.
    turns: list[TurnRecord]


def _the_board_is_a_directory(checkout: Path, board: Path) -> None:
    """Point ``checkout``'s own :data:`DEFAULT_BOARD` source at a directory.

    The one substitution this journey makes, and it is the same one every other stand-in
    here is: the live GitHub Projects board, which a test may not write to — a copy of a
    plan-sized project is a burst of content-creating mutations that trips GitHub's
    secondary rate limiter, and what it left behind would sit on the store every other
    run of this repository reads.

    Made in the *checkout's own configuration* rather than through the store's
    environment layer, because that layer cannot do it and because doing it there would
    prove the wrong thing. It cannot: a layered value merges into the file's, so a
    `plugin` of `local-md` arrives beside the four `github-projects` keys and the source
    is refused for them. And it would not be the default under test: a source this
    journey declared is one it named, where what is being driven is the name the recipe
    resolves when nobody names anything.

    The block is located by the shape the tracked file has rather than by a pattern that
    could match half of it, so a configuration that moved fails here — loudly, and before
    a launch — instead of leaving this journey pointed at the real board.
    """
    configuration = checkout / "onetaskgraph.yaml"
    lines = configuration.read_text(encoding="utf-8").splitlines()
    opens = f"  {DEFAULT_BOARD}:"
    assert opens in lines, (
        f"{configuration} declares no {DEFAULT_BOARD!r} source opening {opens!r}, so this "
        f"journey cannot point it at a directory and would drive a real board:\n" + "\n".join(lines)
    )
    start = lines.index(opens)
    end = start + 1
    while end < len(lines) and lines[end].startswith("    "):
        end += 1
    lines[start:end] = [opens, "    plugin: local-md", "    config:", f"      root: {board}"]
    configuration.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _provisioned(checkout: Path) -> None:
    """Give a copied checkout the toolchain a `SessionStart` hook would have given it.

    Two halves, because this repository installs its tools in two ways. The published
    CLIs are project dependencies, so `uv sync --locked` resolves exactly the releases
    this checkout pins. The plan-store CLI is a release archive session setup installs
    into `<root>/.venv/bin`, which a copy never fires the hook for — so the copy is given
    the binary this checkout already pinned, and `scripts/onetaskgraph-install.sh`, which
    every step of the flow runs, then finds its pin already installed and asks the
    network nothing.
    """
    environment = dict(os.environ)
    for named in ("UV_NO_SYNC", "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV"):
        environment.pop(named, None)
    synced = subprocess.run(
        ["uv", "sync", "--locked"],
        cwd=checkout,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )
    assert synced.returncode == 0, f"the copied checkout could not be provisioned:\n{synced.stderr}"
    shutil.copy2(ONETASKGRAPH_BIN, checkout / ".venv" / "bin" / ONETASKGRAPH_BIN.name)


@pytest.fixture(scope="module")
def default_board(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Defaulted:
    """Drive one whole `just plan` flow that names no destination at all.

    This is the flow an operator gets by typing the recipe and nothing else, and it is
    the one path through `scripts/plan.sh`'s handover that no other journey reaches: the
    tail is handed no `--to`, so what it copies into is whatever `orchestrator.plan_copy`
    calls the board this repository plans against — resolved by that script running this
    checkout's own code, rather than read off a flag.

    It runs in a **copy** of this checkout for the reason
    :func:`_the_board_is_a_directory` gives: that name has to answer to a directory, and
    the only layer that can make it is the configuration the running checkout tracks.
    Everything else is this checkout — its recipes, its scripts, its personas, its graphs
    and its installed commands, copied file for file.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("plan-flow-default")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    copy_working_tree(checkout)
    board = tmp_path / "board"
    # Created rather than left to the first write, for the reason the sibling fixture's
    # is: a `local-md` source canonicalizes its root when it is built.
    board.mkdir()
    _the_board_is_a_directory(checkout, board)
    _lends_a_criterion_carrying_metacharacters(checkout)
    _provisioned(checkout)

    unique = f"test-{os.getpid()}-plan-flow-default"
    stored = Stored(
        project=unique,
        qualified=f"{FIXTURE_SOURCE}:{unique}",
        task_title="feat: page the node listing",
        second_task_title="feat: follow the cursor from the view",
        repository="github.com/nickderobertis/onepipeline",
        second_repository="github.com/nickderobertis/onepipeline-ui",
        document=f"{unique}-document",
        document_qualified=f"{FIXTURE_SOURCE}:{unique}-document",
        document_path=FIXTURE_ROOT / "documents" / f"{unique}-document.md",
    )
    environment = _environment(
        tmp_path, stored, destination=DEFAULT_BOARD, declared_at=None, checkout=checkout
    )
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    environment[PROMPT_LOG_ENV] = str(tmp_path / "turns.jsonl")
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    brief = tmp_path / "cursor-shape.md"
    brief.write_text(
        "## What\nDecide the cursor's shape.\n\n"
        f"Plan project: {stored.qualified}\n\n"
        "## Why\nThe view cannot deep-link until it is settled.\n\n"
        "## Acceptance criteria\n- The cursor's shape and its type are stated.\n",
        encoding="utf-8",
    )
    try:
        launch = _just(
            "plan", str(brief), "--name", DEFAULT_RUN, environment=environment, checkout=checkout
        )
        assert launch.returncode == 0, f"the flow failed:\n{launch.stdout}\n{launch.stderr}"
        return Defaulted(
            launch=launch,
            stored=stored,
            board=board,
            checkout=checkout,
            environment=environment,
            # Cast rather than validated: `fake_backend.py` is this suite's own file and
            # writes these records itself, so `TurnRecord` states a schema this repository
            # owns both ends of rather than asserting anything about somebody else's.
            turns=[
                cast(TurnRecord, json.loads(line))
                for line in Path(environment[PROMPT_LOG_ENV])
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ],
        )
    finally:
        for ended in (DEFAULT_RUN, DEFAULT_DESIGN_RUN):
            _just("stop", ended, environment=environment, seconds=60, checkout=checkout)


@COPIES_THE_TRACKED_TREE
@pytest.mark.xdist_group("plan-flow")
def test_a_lifted_criterion_reaches_the_dispatch_with_its_metacharacters_intact(
    default_board: Defaulted,
) -> None:
    """Whatever a template author writes between those markers arrives verbatim.

    A corrupted criterion is the one failure nothing downstream can catch: the flow still
    composes, still launches, still succeeds, and the worker is judged against whatever
    reached it. So this writes :data:`LIFT_METACHARACTER_PROBE` into the copied checkout's
    own template and requires those literal bytes back out of the dispatched task. The
    substitution hazard it guards against is stated at the splice in
    `scripts/finish-plan.sh`.

    Read where the criterion is consumed, and opening no template to build its
    expectation, for the reason the sibling journey gives.
    """
    # llmlint: ignore-block[tests_mirror_real_usage] The effective prompt is the only place
    # a dispatched task is observable; no published view carries it. The fake backend
    # writes these records itself and `TurnRecord` states the schema it owns.
    dispatched = [
        turn["prompt"]
        for turn in default_board.turns
        if _member(turn) == WORKER_MEMBER and DESIGN_TASK_MARKER in turn["prompt"]
    ]
    # llmlint: ignore-end[tests_mirror_real_usage]
    assert dispatched, (
        "no dispatched turn carried the design-doc node's own task, so nothing here is "
        "about that dispatch at all"
    )

    for prompt in dispatched:
        assert LIFT_METACHARACTER_PROBE in prompt, (
            "the criterion this flow lifted out of the template did not reach the "
            "dispatched task as it was written, so what the design-doc dispatch is "
            f"judged against is not what the template says:\n{prompt!r}"
        )


@COPIES_THE_TRACKED_TREE
@pytest.mark.xdist_group("plan-flow")
def test_a_flow_that_names_no_destination_copies_into_the_board_this_repository_plans_against(
    default_board: Defaulted,
) -> None:
    """The default a caller gets by typing nothing, driven rather than read off a table.

    A plan on that board and its one design document beside it are what a person opens,
    so a handover that reached the tail without a destination and copied nowhere would
    leave the flow reporting success over a board holding nothing.
    """
    landed = sorted(
        str(one.relative_to(default_board.board)) for one in default_board.board.rglob("*.md")
    )
    project = default_board.stored.project
    assert landed == [
        f"documents/{default_board.stored.document}.md",
        f"projects/{project}.md",
        f"tasks/{project}/decide-the-cursor.md",
        f"tasks/{project}/read-the-cursor.md",
    ], f"the flow left {landed} on the board it copies into when it is told none"


@COPIES_THE_TRACKED_TREE
@pytest.mark.xdist_group("plan-flow")
def test_a_flow_that_names_no_destination_reports_where_that_board_holds_both(
    default_board: Defaulted,
) -> None:
    """And the two locations it ends on are that board's own answers.

    The sibling above reads the records; this reads the report, which is all a supervisor
    who typed `just plan <brief>` and nothing else is handed. Both locations are taken
    back out of the store rather than composed here, for the reason the `--to` flow's own
    reading of them is: a destination decides its own ids and where its records live.
    """
    reported = default_board.launch.stdout + default_board.launch.stderr
    listed = _just(
        "plans",
        "project",
        "list",
        "--source",
        DEFAULT_BOARD,
        "--json",
        environment=default_board.environment,
        seconds=120,
        checkout=default_board.checkout,
    )
    assert listed.returncode == 0, f"the board could not be read:\n{listed.stderr}"
    # llmlint: ignore[suppressions_justified] onetaskgraph owns this open JSON schema; the
    # fields read below are narrowed at each subscript.
    projects = cast(dict[str, Any], json.loads(listed.stdout))["items"]
    landed = [
        one
        for one in projects
        if one["item"]["metadata"].get(ORIGIN_KEY) == default_board.stored.qualified
    ]
    assert landed, (
        f"the board holds no project the store records as copied from "
        f"{default_board.stored.qualified}; it holds {[one['id'] for one in projects]}"
    )
    assert f"holds the plan at {landed[0]['item']['location']['path']}" in reported, (
        f"the flow never reported where the board it defaults to holds this plan:\n{reported}"
    )

    documents = _just(
        "plans",
        "document",
        "list",
        "--project",
        landed[0]["id"],
        "--json",
        environment=default_board.environment,
        seconds=120,
        checkout=default_board.checkout,
    )
    assert documents.returncode == 0, f"the board could not be read:\n{documents.stderr}"
    # llmlint: ignore[suppressions_justified] onetaskgraph owns this open JSON schema; the
    # fields read below are narrowed at each subscript.
    held = cast(dict[str, Any], json.loads(documents.stdout))["items"]
    assert len(held) == 1, f"the board holds {len(held)} documents of this plan: {held}"
    assert f"holds its design document at {held[0]['item']['location']['path']}" in reported, (
        f"the flow never reported where the board it defaults to holds the document a "
        f"person reviews this plan as:\n{reported}"
    )
