"""A planning run produces the design document as well as the plan, and stores it.

`just plan` writes two nodes now: the planner, and a `design-doc` node depending on it
that reads the finished plan and writes the one short document a person reviews the plan
as. What that arrangement is worth is entirely in three things happening in order, and
none of them can be read off the plan document the recipe writes:

* the second node is dispatched **after** the first has settled, so what it reads is a
  finished plan rather than a half-written one;
* the dispatch is really given the plan's qualified id, which is the only way it can find
  the plan at all — nothing hands one node's output to a later node, and a plan written
  to an ignored path in the planner's own worktree does not outlive the run;
* what that dispatch stores is afterwards **readable back out of the plan store as a
  document of that project**, with the store reporting where it is, which is what puts
  the document beside the plan rather than in a directory only the dispatch knew about.

So the whole launch runs for real: the real `just plan`, the real `scripts/plan.sh`, the
real `onepipeline` driver, the real `graphs/design-doc.yaml`, a real registered identity,
and the real `onetaskgraph` reading the store afterwards through `just plans`.

**What is stood in for is the paid model's answer, and nothing downstream of it.** A
design-doc dispatch does two things: it writes the document's prose, and it stores that
prose as a document of the plan's project. The first is the model's, and it is scripted
here as a draft in a source of the dispatch's own — the same shape as every other scripted
answer in this suite. The second is `onetaskgraph`'s, and the dispatch really performs it:
the turn runs the store's own command line, in its own working directory, against the
store its own repository's tracked configuration names. So the record this journey reads
back is one the store created — its origin metadata says so, and nothing here composes it
— rather than a file a test planted where the store would have put one.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, NamedTuple, NewType, TypedDict, cast

import pytest
from conftest import git
from fake_backend import (
    AUTHOR_PLAN_ENV,
    MEMBER_OF_CONFIG,
    PROMPT_LOG_ENV,
    RUN_ON_MARKER_ENV,
)
from plan_fixture_root import ROOT as FIXTURE_ROOT
from project_fixtures import helper
from published_tools import ONETASKGRAPH_BIN
from scratch_identity import GIT_IDENTITY, Identity, seeded
from waits import timeout as e2e_timeout

from orchestrator.project_store import render_plan_project
from orchestrator.root import REPO_ROOT

#: This journey is its own Nx project's, `plan-tooling`, rather than a marker tier of the
#: orchestrator project: it launches a whole real orchestration run — the installed
#: `onepipeline`, two dispatched lifecycle nodes, a real `oneharness run` per turn — which
#: is a different cost from the Python suite beside it and is answered by a different set
#: of files. `tests/plan_tooling/project.json` names that set as `planToolingWorkspace`,
#: and `tests/conftest.py` holds these tests to it.

#: The stand-in for the paid model, and the provider binary beneath it. Reached through
#: `helper` rather than from this module's own directory, because a stand-in named at a
#: path this checkout does not have is not a stand-in: oneharness falls through to a real
#: identity and the journey spends real turns while passing.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")

#: A launching session this journey states rather than inherits, and everything else an
#: enclosing dispatch would otherwise decide for it.
LAUNCHING_SESSION = "e2e-design-doc-launch"
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

#: The two nodes a planning launch writes.
PLANNER_NODE = "plan"
DESIGN_NODE = "design-doc"

#: The `graphs/*.yaml` member a dispatched node runs as, either side of it.
WORKER_MEMBER = "worker"

#: The journal records this reads, and the label that says which node produced one.
NODE_DISPATCHED = "node-dispatched"
NODE_SETTLED = "node-settled"
NODE_LABEL = "node"

#: The fragment only the design-doc node's task carries, which is what tells the two
#: dispatches apart at the stand-in: both run as `worker`, so the member cannot.
DESIGN_TASK_MARKER = "## What this dispatch owes"

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
    """One record the run appended, in the three fields this journey reads."""

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
    """One node the planner stand-in authors, in the fields it states."""

    id: str
    title: str
    task: str


class StandInPlan(TypedDict):
    """The plan document the planner stand-in writes, stated rather than left open.

    `render_plan_project` takes any mapping and copies unknown keys through, so an open
    dict would compile — but this is a document this journey composes in full, and the
    fields it has to get right (a `schema_version` the loader accepts, one task with the
    three fields the store renders) are exactly what a name for it makes checkable.
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
    #: The plan's one task, so the document has a row to point at.
    task_title: str
    #: The document's native id inside that source, its qualified form, and its file.
    document: str
    document_qualified: str
    document_path: Path


class Planned(NamedTuple):
    """One whole `just plan` launch, and everything read back off it."""

    launch: subprocess.CompletedProcess[str]
    journal: list[JournalEvent]
    turns: list[TurnRecord]
    stored: Stored


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
                "title": stored.task_title,
                "task": (
                    "## What\nDecide the cursor's shape.\n\n"
                    "## Why\nThe view cannot deep-link until it is settled.\n\n"
                    "## Acceptance criteria\n- The cursor's shape and its type are stated.\n"
                ),
            }
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


def _tracks_the_store(identity: Identity) -> None:
    """Give the seeded repository this checkout's own plan-store configuration.

    A design-doc dispatch works in a worktree of the repository the plan is of and reaches
    the store the way anything in that worktree does: through the `onetaskgraph.yaml` that
    repository tracks. The seeded identity is a bare stub, so it carries none — and a
    dispatch there would have to be *told* where the plan store is, which is the one thing
    this journey must not tell it if the storing is to be the dispatch's own.
    """
    tracked = identity.execution / "onetaskgraph.yaml"
    tracked.write_bytes((REPO_ROOT / "onetaskgraph.yaml").read_bytes())
    git("add", "onetaskgraph.yaml", cwd=identity.execution)
    git(*GIT_IDENTITY, "commit", "-qm", "chore: configure the plan store", cwd=identity.execution)
    git("push", "-q", "origin", "main", cwd=identity.execution)
    # The publication checkout is a clone of the same origin and must be clean at the base
    # before a dispatch, so it is brought along rather than left a commit behind.
    git("pull", "-q", "--ff-only", cwd=identity.publication)


def _environment(tmp_path: Path, stored: Stored) -> dict[str, str]:
    """The environment this launch runs in, against a registry and a runs root of its own."""
    identity = seeded(tmp_path, publication=PUBLICATION_ALIAS, execution=EXECUTION_ALIAS)
    _tracks_the_store(identity)
    environment = dict(os.environ)
    for name in INHERITED_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEVCS_HOME"] = str(identity.home)
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")

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
    *args: str, environment: dict[str, str], seconds: float = 600
) -> subprocess.CompletedProcess[str]:
    """Run one real recipe from this checkout."""
    return subprocess.run(
        ["just", *args],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


#: The run this module launches under, named once so its plan and its records are
#: unambiguously its own.
RUN = RunId("design-doc-launch-e2e")


@pytest.fixture(scope="module")
def planned(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Planned:
    """Launch one whole planning run, attached, and hand every question its record.

    One launch for every claim here rather than one each: the ordering, the task the
    second dispatch was given, and the document it left in the store are three readings of
    one run, and a fixture per claim would pay for two whole dispatches to re-prove them.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("design-doc-launch")
    unique = f"test-{os.getpid()}-design-doc-launch"
    stored = Stored(
        project=unique,
        qualified=f"{FIXTURE_SOURCE}:{unique}",
        task_title="feat: page the node listing",
        document=f"{unique}-document",
        document_qualified=f"{FIXTURE_SOURCE}:{unique}-document",
        document_path=FIXTURE_ROOT / "documents" / f"{unique}-document.md",
    )
    environment = _environment(tmp_path, stored)
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
        launch = _just("plan", str(brief), "--name", RUN, environment=environment)
        assert launch.returncode == 0, f"the launch failed:\n{launch.stdout}\n{launch.stderr}"
        journal = Path(environment["ONEPIPELINE_RUNS_DIR"]) / RUN / "events.jsonl"
        assert journal.is_file(), f"the launch recorded no journal at {journal}"
        assert turns.is_file(), f"the launch reached no harness turn, so {turns} is absent"
        return Planned(
            launch=launch,
            # `cast` rather than a validating read: `onepipeline` owns the journal's
            # record contract and `JournalEvent` states only the fields read here, so a
            # record missing one fails at the subscript that wanted it.
            journal=[
                cast(JournalEvent, json.loads(line))
                for line in journal.read_text(encoding="utf-8").splitlines()
            ],
            turns=[
                cast(TurnRecord, json.loads(line))
                for line in turns.read_text(encoding="utf-8").splitlines()
            ],
            stored=stored,
        )
    finally:
        _just("stop", RUN, environment=environment, seconds=60)
        (REPO_ROOT / ".plans" / "projects" / f"{RUN}.md").unlink(missing_ok=True)
        tasks = REPO_ROOT / ".plans" / "tasks" / RUN
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


def _positions(planned: Planned, kind: str, node: str) -> list[int]:
    """Where in the journal a run recorded ``kind`` for ``node``, in order."""
    return [
        index
        for index, event in enumerate(planned.journal)
        if event.get("kind") == kind and event.get("labels", {}).get(NODE_LABEL) == node
    ]


@pytest.mark.xdist_group("design-doc-launch")
def test_the_design_doc_node_is_dispatched_only_once_the_planner_node_has_settled(
    planned: Planned,
) -> None:
    """The dependency is what makes the document a reading of a *finished* plan.

    Without it the two nodes are siblings and the document writer races the planner: it
    would open a project that is half-written, or not written at all, and report a
    document nobody could act on. Read from the run's own journal rather than from the
    plan, because the plan can only say the edge was declared — what the engine did with
    it is the claim.
    """
    settled = _positions(planned, NODE_SETTLED, PLANNER_NODE)
    dispatched = _positions(planned, NODE_DISPATCHED, DESIGN_NODE)
    assert settled, "the run never settled the planner node"
    assert dispatched, (
        "the run never dispatched the design-doc node, so the planning launch produced "
        "the plan and no document"
    )
    assert settled[-1] < dispatched[0], (
        f"the design-doc node was dispatched at journal record {dispatched[0]} and the "
        f"planner node settled at {settled[-1]}, so the document was written from a plan "
        "that was not finished"
    )
    assert _positions(planned, NODE_SETTLED, DESIGN_NODE), (
        "the design-doc node never settled, so the run did not carry it to an end"
    )


@pytest.mark.xdist_group("design-doc-launch")
def test_the_design_doc_dispatch_is_given_the_plan_the_brief_named(planned: Planned) -> None:
    """The one thing that dispatch cannot derive reaches it in its own effective prompt.

    A plan lives in a store the dispatch has to be told the address of: nothing hands one
    node's output to a later node, and the planner's own worktree is gone by the time this
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


@pytest.mark.xdist_group("design-doc-launch")
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
