"""`just finish-plan` on its own: the tail of the planning flow, driven from a brief.

A plan edited after it was authored is the case this entry point exists for. The edit
leaves that task carrying no review record for what it now says, so the review below
spends a real judged turn on it — and only once that turn has passed is the document a
person reviews the plan as written, and only then are the two copied into the destination
they are reviewed in.

Everything below the recipe is real: the real `just finish-plan` and `scripts/finish-plan.sh`,
the real `just review-plan` and its `oneharness` turn, the real `orchestrator-check-plan`,
the real `onepipeline` driver launching the document node, the real `orchestrator-copy-plan`
and the store's own copy verb, and real local Markdown stores at both ends.
`tests/e2e/fake_codex.py` stands in for the paid provider alone.

**The refusals are what most of this module is**, and each one is read by its exit status
as well as by what it says. A caller scripting this branches on the status, so a review
refusal that exited like a plan-check refusal would send them to correct the wrong thing —
and a destination that would not take the copy read as either would have them editing a
plan the board simply never received.

**The destination is a second local store and never the live board.** A journey that wrote
to the real `plans` board would be its own rate-limit burst and would leave a project
behind on the store every other run of this repository reads.
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
from conftest import git
from fake_backend import PROMPT_LOG_ENV, RUN_ON_MARKER_ENV
from plan_fixture_root import ROOT as FIXTURE_ROOT
from project_fixtures import helper
from published_tools import ONETASKGRAPH_BIN
from scratch_identity import GIT_IDENTITY, PLANNING_FLOW_ORIGIN, Identity, seeded
from waits import timeout as e2e_timeout

from orchestrator import plan_review, plan_store
from orchestrator.criteria_guard import APPENDIX
from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

#: This journey is its own Nx project's, `plan-tooling`; see
#: `tests/plan_tooling/project.json` and the guard in `tests/conftest.py`.

#: The stand-in for the paid model, the provider binary beneath it, and the guard covering
#: the identities `ONEHARNESS_BIN_*` cannot reach. Reached through `helper` rather than
#: from this module's own directory, because a stand-in named at a path this checkout does
#: not have is not a stand-in: oneharness falls through to a real identity and the journey
#: spends real turns while passing.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: A launching session this journey states rather than inherits, and everything else an
#: enclosing dispatch would otherwise decide for it.
LAUNCHING_SESSION = "e2e-finish-plan"
INHERITED_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: The two aliases `scripts/finish-plan.sh` defaults to, seeded here against a scratch
#: registry rather than overridden per launch: what is under test is the project this
#: recipe writes when nobody tells it anything.
PUBLICATION_ALIAS = "ai-orchestrator"
EXECUTION_ALIAS = "ai-orchestrator-isolated"

#: The source a plan is drafted in here — the shared fixture store every tier of this
#: suite publishes into, which is what stands in for the gitignored `authoring` root.
FIXTURE_SOURCE = "test-fixtures"

#: The source both launches of a planning flow write their own generated project into,
#: which is where `onepipeline` then reads the plan it launches from.
AUTHORING_SOURCE = "authoring"

#: The second local Markdown store this flow copies into, standing in for the `plans`
#: board. A plain lowercase name because it is spelled into the store's own
#: `ONETASKGRAPH_SOURCES__…` environment layer as well as onto a command line.
DESTINATION = "destination"

#: The node the tail's own launch writes, and the suffix its run and project are named by.
DESIGN_NODE = "design-doc"
DESIGN_RUN_SUFFIX = "-design"

#: The fragment only the design-doc dispatch's task carries, which is what tells its turn
#: apart at the stand-in.
DESIGN_TASK_MARKER = "## What this dispatch owes"

#: The source a design-doc dispatch drafts into before storing: one of its own, named on
#: its own command line rather than in any tracked configuration, which is what an agent's
#: scratch directory is.
DRAFT_SOURCE = "draft"

#: The key `onetaskgraph` stamps on a record it created by copying, naming what it copied.
ORIGIN_KEY = "onetaskgraph.origin"

#: The exit statuses `scripts/finish-plan.sh` answers with, which are its whole contract to
#: a caller that reads only the status. Restated here as literals rather than imported from
#: the shell that defines them, because what this module asserts is the number an operator's
#: script branches on: a constant read out of the script under test would make every one of
#: these assert that it equals itself.
OK = 0
REVIEW_REFUSED = 1
UNRUNNABLE = 2
PLAN_REFUSED = 3
LAUNCH_FAILED = 4
COPY_REFUSED = 5

#: Criteria that answer every demand the tracked appendix and the shipped `engineer` bar
#: make, so a plan carrying them is refused by nothing this module is not about.
STATES_ITS_BAR = (
    "- The route accepts a valid request and rejects an invalid one.\n"
    "- A request-level test drives the route end to end and covers both paths.\n"
    "- Every claim the dispatch makes about the finished work is true of the tree as it "
    "finally stands."
)

#: The same criteria with one that `just check-plan` refuses: it rests on somebody else's
#: released artifact, which is not a fact about the finished tree in any wording and which
#: one node already paid for — the worker correctly determined it could not be satisfied,
#: and the node was killed by hand with the rest of its work landed. Criteria *silent*
#: about a demand their bar makes used to stand here, and no longer refuse at all: whether
#: criteria answer a demand is judged by `just review-plan`'s turn, by meaning, because
#: matching the phrase here refused wordings the same review had asked for.
UNLAUNCHABLE = (
    f"{STATES_ITS_BAR}\n"
    "- `config/onetaskgraph.version` names a release that carries both of those fixes."
)

#: What the scripted reviewer answers. The refusal carries **two** findings, because one
#: is the shape the verdict contract was widened away from: a reviewer that saw two
#: defects and could report one sent its author back for a second judged turn.
PASSES = {"passes": True, "findings": []}
REFUSES = {
    "passes": False,
    "findings": [
        {
            "criterion": "the branch publishes",
            "why": "publication happens after the worker settles, so no dispatch reaches it",
        },
        {
            "criterion": "the route is fast",
            "why": "it states no property a judge could find unmet",
        },
    ],
}

RunId = NewType("RunId", str)


class Drafted(NamedTuple):
    """A plan drafted in the fixture store, and the document a dispatch will store for it."""

    #: The plan's native id inside the fixture source, and its qualified form.
    project: str
    qualified: str
    #: The plan's one task, so the document has a row to point at.
    task_title: str
    #: The document's native id inside that source, and the file the store puts it in.
    document: str
    document_path: Path


def _task(criteria: str) -> str:
    return (
        "## What\n\nAdd the paginated listing and the test that drives it.\n\n"
        "## Why\n\nAn operator cannot see past the first screen of nodes.\n\n"
        f"## Acceptance criteria\n\n{criteria}\n\n"
        f"{(REPO_ROOT / APPENDIX).read_text(encoding='utf-8').strip()}\n"
    )


def _draft(name: str, criteria: str = STATES_ITS_BAR) -> Drafted:
    """Draft an unreviewed plan into the fixture store, with no design document yet.

    Deliberately not `project_fixtures.local_project`, which writes a design document and
    approves it: what this flow *produces* is that document, so a plan arriving with one
    already would leave the store holding two and no reader able to say which a person
    approved.

    Written through this repository's own record renderer, so the store reads these
    records the way it reads a planner's own rather than a hand-written shape only this
    journey would produce.
    """
    native = f"test-{os.getpid()}-{name}"
    write_plan_project(
        FIXTURE_ROOT,
        {
            "schema_version": 3,
            "goal": {"text": "Deliver the paginated listing"},
            "name": native,
            "tasks": [
                {
                    "id": "decide-the-cursor",
                    "persona": "engineer",
                    "title": "feat: page the node listing",
                    "task": _task(criteria),
                }
            ],
        },
        native_id=native,
    )
    return Drafted(
        project=native,
        qualified=f"{FIXTURE_SOURCE}:{native}",
        task_title="feat: page the node listing",
        document=f"{native}-document",
        document_path=FIXTURE_ROOT / "documents" / f"{native}-document.md",
    )


def _document(drafted: Drafted) -> str:
    """The prose a design-doc dispatch drafted, as a record of the source it drafted into.

    The paid model's answer and nothing else: what makes it a document *of the plan's
    project* in the plan store is the copy the dispatch performs, not this.
    """
    return (
        f'---\ntitle: "Design: {drafted.project}"\n'
        f'project: "{drafted.project}"\n---\n\n'
        "## What\n\nA paginated node listing.\n\n"
        "## Why\n\nAn operator cannot see past the first screen.\n\n"
        "## Architecture\n\nOne route, one view.\n\n"
        "## Contracts\n\nThe cursor is an opaque token.\n\n"
        "## Acceptance criteria\n\nThe listing pages.\n\n"
        "## Planned tasks\n\n"
        "| Task | What it delivers | Depends on | Where it lives |\n"
        "| --- | --- | --- | --- |\n"
        f"| {drafted.task_title} | the route | none | the store's own location |\n"
    )


def _staged_draft(tmp_path: Path, drafted: Drafted) -> Path:
    """Stage the document's prose where the design-doc dispatch drafted it.

    This is the scripted half and the whole of it. **Storing it is not staged**: the plan
    store holds no document until the dispatch itself runs the store's own command line,
    so what the read-back proves is that the dispatch ran it.
    """
    documents = tmp_path / "drafted" / "documents"
    documents.mkdir(parents=True)
    (documents / f"{drafted.document}.md").write_text(_document(drafted), encoding="utf-8")
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
    git("push", "-q", "origin", "main", cwd=identity.execution, env=identity.environment)
    git("pull", "-q", "--ff-only", cwd=identity.publication, env=identity.environment)


class Bench(NamedTuple):
    """One throwaway host these journeys run a flow on: its environment and its stores."""

    environment: dict[str, str]
    destination: Path
    runs: Path
    tmp_path: Path


def _bench(tmp_path: Path, oneharness_bin: str, *answers: object) -> Bench:
    """The environment a flow runs in, against a registry, a runs root and a board of its own."""
    identity = seeded(
        tmp_path,
        publication=PUBLICATION_ALIAS,
        execution=EXECUTION_ALIAS,
        origin=PLANNING_FLOW_ORIGIN,
    )
    _tracks_the_store(identity)
    destination = tmp_path / "board"
    # Created rather than left to the first write: a `local-md` source canonicalizes its
    # root when it is built, so an absent one is refused as a broken source rather than
    # populated — which would report a sound copy as a destination that refused it.
    destination.mkdir(parents=True)
    environment = dict(os.environ)
    for name in INHERITED_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEVCS_HOME"] = str(identity.home)
    environment.update(identity.environment)
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # And the identities that seam cannot reach; see `tests/e2e/no_paid_provider.py`.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment["FAKE_CODEX_ANSWERS"] = json.dumps([json.dumps(one) for one in answers])
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    environment[f"ONETASKGRAPH_SOURCES__{DESTINATION.upper()}__PLUGIN"] = "local-md"
    environment[f"ONETASKGRAPH_SOURCES__{DESTINATION.upper()}__CONFIG__ROOT"] = str(destination)
    # `authoring` is in this list because the tail *launches* out of it: the project it
    # writes for the design-document node is an authoring project, and a run whose plan
    # store cannot see that source reads a project with no tasks in it.
    environment["ONETASKGRAPH_DEFAULT_SOURCES"] = (
        f"{AUTHORING_SOURCE},{FIXTURE_SOURCE},{DESTINATION}"
    )
    return Bench(
        environment=environment,
        destination=destination,
        runs=Path(environment["ONEPIPELINE_RUNS_DIR"]),
        tmp_path=tmp_path,
    )


def _stores_the_document(bench: Bench, drafted: Drafted) -> None:
    """Script the one command the design-doc dispatch runs to store what it drafted."""
    keyed = bench.tmp_path / f"commands-{drafted.project}.json"
    drafted_root = _staged_draft(bench.tmp_path / drafted.project, drafted)
    keyed.write_text(
        json.dumps(
            {
                DESIGN_TASK_MARKER: [
                    [
                        str(ONETASKGRAPH_BIN),
                        "--set",
                        f"sources.{DRAFT_SOURCE}.plugin=local-md",
                        "--set",
                        f"sources.{DRAFT_SOURCE}.config.root={drafted_root}",
                        "document",
                        "copy",
                        f"{DRAFT_SOURCE}:{drafted.document}",
                        "--to",
                        FIXTURE_SOURCE,
                    ]
                ]
            }
        ),
        encoding="utf-8",
    )
    bench.environment[RUN_ON_MARKER_ENV] = str(keyed)
    bench.environment[PROMPT_LOG_ENV] = str(bench.tmp_path / f"turns-{drafted.project}.jsonl")


def _brief(tmp_path: Path, drafted: Drafted, name: str = "cursor-shape") -> Path:
    """A manager's brief naming the plan this flow is about."""
    brief = tmp_path / f"{name}.md"
    brief.write_text(
        "## What\nDecide the cursor's shape.\n\n"
        f"Plan project: {drafted.qualified}\n\n"
        "## Why\nThe view cannot deep-link until it is settled.\n\n"
        "## Acceptance criteria\n- The cursor's shape and its type are stated.\n",
        encoding="utf-8",
    )
    return brief


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


def _records(root: Path) -> list[str]:
    """Every record file a store holds, as paths below its root."""
    return sorted(str(one.relative_to(root)) for one in root.rglob("*.md")) if root.is_dir() else []


def _stop(bench: Bench, *runs: str) -> None:
    """End every run a flow left on this bench's ledger, and take back its projects."""
    for run in runs:
        _just("stop", run, environment=bench.environment, seconds=60)
        (REPO_ROOT / ".plans" / "projects" / f"{run}.md").unlink(missing_ok=True)
        tasks = REPO_ROOT / ".plans" / "tasks" / run
        if tasks.is_dir():
            for record in tasks.iterdir():
                record.unlink(missing_ok=True)
            tasks.rmdir()


class StoredTask(TypedDict):
    """One task as `onetaskgraph task list` reports it, in the two fields this suite reads.

    `repositories` is the record's own top-level field — where a hosted repository is
    named, as its normalized origin — and `metadata` is the map the reserved
    `onepipeline.repo` key would sit in if the record still named it there.
    """

    repositories: list[str]
    metadata: dict[str, object]


class Finished(NamedTuple):
    """One whole `just finish-plan` run, and everything read back off it."""

    finish: subprocess.CompletedProcess[str]
    drafted: Drafted
    bench: Bench
    run: RunId
    #: The design-document node's task as the store reports it — its `repositories` and
    #: its metadata — read before the fixture takes the project back.
    design_task: StoredTask


def _design_task(bench: Bench, run: RunId) -> StoredTask:
    """The one task of the design-document project the tail wrote, as the store reports it.

    Through the plan-store CLI rather than off the file, because the settlement write-back
    rewrites a record in the store's own spelling once the run has settled; what the
    store *reports* is the contract. `onetaskgraph` owns the answer; the cast says so, and
    the subscripts fail loudly on a record without the two fields.
    """
    listed = subprocess.run(
        [
            str(ONETASKGRAPH_BIN),
            "task",
            "list",
            "--source",
            AUTHORING_SOURCE,
            "--project",
            f"{run}{DESIGN_RUN_SUFFIX}",
            "--json",
        ],
        cwd=REPO_ROOT,
        env=bench.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert listed.returncode == 0, f"the store could not list the design project:\n{listed.stderr}"
    (task,) = [record["item"] for record in json.loads(listed.stdout)["items"]]
    return cast(StoredTask, task)


#: The flow this module's one successful run is named by, and the run its tail launches.
RUN = RunId("finish-plan-e2e")


@pytest.fixture(scope="module")
def finished(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Finished:
    """Drive the tail once on an unreviewed plan, and hand every question its record.

    One flow for every claim here rather than one each: the judged review, the document
    the dispatch stored, and what the copy left on the destination are three readings of a
    single act, and a fixture per claim would pay for a whole launch to re-prove them.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("finish-plan")
    bench = _bench(tmp_path, oneharness_bin, PASSES)
    drafted = _draft("finish-plan-flow")
    _stores_the_document(bench, drafted)
    brief = _brief(tmp_path, drafted)
    try:
        finish = _just(
            "finish-plan",
            str(brief),
            "--name",
            RUN,
            "--to",
            DESTINATION,
            environment=bench.environment,
        )
        assert finish.returncode == OK, f"the tail failed:\n{finish.stdout}\n{finish.stderr}"
        return Finished(
            finish=finish,
            drafted=drafted,
            bench=bench,
            run=RUN,
            design_task=_design_task(bench, RUN),
        )
    finally:
        _stop(bench, f"{RUN}{DESIGN_RUN_SUFFIX}")


@pytest.mark.xdist_group("finish-plan")
def test_the_tail_reviews_the_plan_before_it_launches_the_dispatch_that_documents_it(
    finished: Finished,
) -> None:
    """The judged turn happened, it was recorded, and it happened first.

    This entry point exists for a plan somebody edited after it was authored, so the
    review here is not the free no-op a planning launch's closeout leaves behind: the task
    carried no record at all, and the record this reads was written by `just review-plan`
    spending a turn on it. That its `reviewed_at` precedes the design run's own dispatch is
    the ordering — a document written before the review describes content nobody read.
    """
    (task,) = plan_store.read_tasks(finished.drafted.qualified)
    record = task.metadata.get(plan_review.RECORD_KEY)
    assert isinstance(record, dict), f"the tail recorded no review at all: {task.metadata}"
    assert record.get("by") == plan_review.BY_REVIEW, (
        f"the record says a {record.get('by')!r} wrote it; this plan was never part of a "
        f"planning run, so the only thing that could have reviewed it is the judged turn"
    )
    stamped = record.get("reviewed_at")
    assert isinstance(stamped, str), record
    reviewed_at = datetime.fromisoformat(stamped)

    journal = finished.bench.runs / f"{RUN}{DESIGN_RUN_SUFFIX}" / "events.jsonl"
    assert journal.is_file(), f"the tail launched no design run, so nothing is at {journal}"
    # llmlint: ignore[suppressions_justified] onepipeline owns this open record contract;
    # the two fields read below are narrowed where they are read.
    dispatched = [
        cast(dict[str, Any], json.loads(line))
        for line in journal.read_text(encoding="utf-8").splitlines()
    ]
    # The record's own stamp rather than anything inside its payload, parsed rather than
    # compared as text: the review record and the journal spell an instant differently —
    # one ends `+00:00` and the other `Z` — so comparing the strings would answer about the
    # spelling instead of about the order.
    started = [
        datetime.fromisoformat(one["ts"])
        for one in dispatched
        if one.get("kind") == "node-dispatched" and one.get("labels", {}).get("node") == DESIGN_NODE
    ]
    assert started, "the design run never dispatched its node"
    assert reviewed_at < started[0], (
        f"the plan was reviewed at {reviewed_at} and the dispatch that documents it started "
        f"at {started[0]}, so the document describes content the review had not read"
    )


@pytest.mark.xdist_group("finish-plan")
def test_the_tail_leaves_the_plan_and_its_one_document_on_the_destination(
    finished: Finished,
) -> None:
    """Both halves land, because the plan alone is not what a person reviews.

    A plan copied without the document a person approves it as arrives on the destination
    with nothing to approve, and a plan that cannot be approved is one no launch will
    start. So the copy carrying the documents beside the project is what makes this flow's
    output usable at all.
    """
    project = finished.drafted.project
    assert _records(finished.bench.destination) == [
        f"documents/{finished.drafted.document}.md",
        f"projects/{project}.md",
        f"tasks/{project}/decide-the-cursor.md",
    ], f"the tail left {_records(finished.bench.destination)} on the destination"


@pytest.mark.xdist_group("finish-plan")
def test_the_tail_reports_the_two_locations_the_destination_itself_answers(
    finished: Finished,
) -> None:
    """The reported locations are the store's own answers about the records that landed.

    Composing them is what this must not do: a destination decides its own native ids and
    where its records live — a board mints a number where a directory keeps the name — so
    a location assembled from a project name names nothing on the destination this
    repository actually copies into.
    """
    reported = finished.finish.stdout + finished.finish.stderr
    listed = _just(
        "plans",
        "project",
        "list",
        "--source",
        DESTINATION,
        "--json",
        environment=finished.bench.environment,
        seconds=120,
    )
    assert listed.returncode == 0, f"the destination could not be read:\n{listed.stderr}"
    # llmlint: ignore[suppressions_justified] onetaskgraph owns this open JSON schema; the
    # fields read below are narrowed at each subscript.
    projects = cast(dict[str, Any], json.loads(listed.stdout))["items"]
    landed = [
        one
        for one in projects
        if one["item"]["metadata"].get(ORIGIN_KEY) == finished.drafted.qualified
    ]
    assert landed, (
        f"the destination holds no project copied from {finished.drafted.qualified}; it "
        f"holds {[one['id'] for one in projects]}"
    )
    assert f"holds the plan at {landed[0]['item']['location']['path']}" in reported, reported

    documents = _just(
        "plans",
        "document",
        "list",
        "--project",
        landed[0]["id"],
        "--json",
        environment=finished.bench.environment,
        seconds=120,
    )
    assert documents.returncode == 0, f"the destination could not be read:\n{documents.stderr}"
    # llmlint: ignore[suppressions_justified] onetaskgraph owns this open JSON schema; the
    # fields read below are narrowed at each subscript.
    held = cast(dict[str, Any], json.loads(documents.stdout))["items"]
    assert len(held) == 1, f"the destination holds {len(held)} documents of this plan: {held}"
    assert f"holds its design document at {held[0]['item']['location']['path']}" in reported, (
        reported
    )


@pytest.mark.xdist_group("finish-plan")
def test_the_design_document_node_names_this_repository_in_its_records_own_field(
    finished: Finished,
) -> None:
    """The tail's node names its repository once, in `repositories`, as the normalized origin.

    Launched with its defaults, so what is read is the record the recipe writes when
    nobody tells it anything: the origin the flow's default names, in the record's own
    top-level field, and nothing on the reserved `onepipeline.repo` key. That key is what
    every plan this host wrote used to carry an alias on, which left every task issue of
    a plan filed in this repository rather than the one the work changed.
    """
    assert finished.design_task["repositories"] == [PLANNING_FLOW_ORIGIN], finished.design_task
    metadata = finished.design_task["metadata"]
    assert "onepipeline.repo" not in metadata, metadata
    assert metadata["onepipeline.execution_checkout"] == EXECUTION_ALIAS, metadata


@pytest.mark.xdist_group("finish-plan")
def test_the_tail_names_the_channel_of_the_run_it_launches(finished: Finished) -> None:
    """A run nobody can reach the channel of is one whose blocking question times out."""
    reported = finished.finish.stdout + finished.finish.stderr
    assert f"just channel-next {RUN}{DESIGN_RUN_SUFFIX}" in reported, reported


@pytest.mark.xdist_group("finish-plan")
def test_a_destination_that_refuses_the_copy_ends_the_flow_at_its_own_status(
    finished: Finished, oneharness_bin: str, tmp_path: Path
) -> None:
    """The board would not take it, which is not the plan being unreviewed.

    Reading one as the other is the whole reason these statuses differ: "nothing has
    reviewed this" is answered by reviewing the plan, and "the destination refused it" by
    repairing the destination and running the command again. This also reads the flow's
    run naming: a second run of the same brief under a name of its own gets that name's
    own tail run, rather than colliding with the one above.
    """
    bench = _bench(tmp_path, oneharness_bin, PASSES)
    _stores_the_document(bench, finished.drafted)
    named = RunId("finish-plan-e2e-refused")
    brief = _brief(tmp_path, finished.drafted)
    try:
        refused = _just(
            "finish-plan",
            str(brief),
            "--name",
            named,
            "--to",
            "no-such-source",
            environment=bench.environment,
        )
        assert refused.returncode == COPY_REFUSED, (
            f"a destination that refused the copy exited {refused.returncode}:\n"
            f"{refused.stdout}{refused.stderr}"
        )
        assert "refused the copy" in refused.stderr, refused.stderr
        assert (bench.runs / f"{named}{DESIGN_RUN_SUFFIX}").is_dir(), (
            "the flow named its tail run from --name rather than from the brief, so a "
            "second flow over one brief would collide with the first"
        )
    finally:
        _stop(bench, f"{named}{DESIGN_RUN_SUFFIX}")


@pytest.mark.xdist_group("finish-plan")
def test_a_refused_review_ends_the_flow_naming_every_criterion_and_launching_nothing(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The review is a gate rather than a step, and a refusal is where the flow stops.

    There is no repair loop here and there will not be one: the planner's own judge is the
    repair loop and it has already run, so what a refusal owes is every refused criterion
    handed back. What it must not do is write a document about the plan anyway — which is
    the exact failure the ordering exists to prevent — so the absence of the tail's run and
    of anything on the destination is as much the assertion as the status is.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path, oneharness_bin, REFUSES)
    drafted = _draft("finish-plan-refused")
    named = RunId("finish-plan-e2e-review-refused")
    brief = _brief(tmp_path, drafted)

    refused = _just(
        "finish-plan",
        str(brief),
        "--name",
        named,
        "--to",
        DESTINATION,
        environment=bench.environment,
    )

    assert refused.returncode == REVIEW_REFUSED, (
        f"a refused review exited {refused.returncode}:\n{refused.stdout}{refused.stderr}"
    )
    for finding in REFUSES["findings"]:
        # llmlint: ignore[suppressions_justified] this module owns the scripted verdict.
        assert cast(dict[str, str], finding)["criterion"] in refused.stderr, refused.stderr
    assert not (bench.runs / f"{named}{DESIGN_RUN_SUFFIX}").exists(), (
        "a refused review still launched the dispatch that writes the document, so the "
        "document describes a plan its own reviewer rejected"
    )
    assert _records(bench.destination) == [], "a refused review still reached the destination"


@pytest.mark.xdist_group("finish-plan")
def test_a_plan_the_pre_launch_check_refuses_costs_no_document_dispatch(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A plan that would not launch is not one to write a document about.

    The check here is the one its own launch makes, so a plan that reaches it green is a
    plan `just orchestrate` will start. Refusing before the document is written is what
    keeps a plan nobody can launch from costing a whole dispatch — and the status is its
    own, because correcting a criterion a bar demands is a different action from
    correcting one a reviewer named.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path, oneharness_bin, PASSES)
    drafted = _draft("finish-plan-unlaunchable", criteria=UNLAUNCHABLE)
    named = RunId("finish-plan-e2e-check-refused")
    brief = _brief(tmp_path, drafted)

    refused = _just(
        "finish-plan",
        str(brief),
        "--name",
        named,
        "--to",
        DESTINATION,
        environment=bench.environment,
    )

    assert refused.returncode == PLAN_REFUSED, (
        f"a plan the check refuses exited {refused.returncode}:\n{refused.stdout}{refused.stderr}"
    )
    assert not (bench.runs / f"{named}{DESIGN_RUN_SUFFIX}").exists(), (
        "a plan the check refused still cost a document dispatch"
    )
    assert _records(bench.destination) == [], (
        "a plan the check refused still reached the destination"
    )


@pytest.mark.xdist_group("finish-plan")
def test_the_opt_out_copies_nothing_and_reports_no_location(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """`--no-design-doc` stops the flow, because a plan with no document cannot be approved.

    Copying one up anyway would put a plan on the board that no `just orchestrate` will
    ever start: the approval of the design document is what gates dispatch, and there
    would be nothing there to approve. So the flow ends successfully having done nothing,
    and says so.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path, oneharness_bin, PASSES)
    drafted = _draft("finish-plan-opted-out")
    named = RunId("finish-plan-e2e-opted-out")

    stopped = _just(
        "finish-plan",
        str(_brief(tmp_path, drafted)),
        "--name",
        named,
        "--to",
        DESTINATION,
        "--no-design-doc",
        environment=bench.environment,
    )

    assert stopped.returncode == OK, f"{stopped.stdout}{stopped.stderr}"
    assert "nothing was copied" in stopped.stderr, stopped.stderr
    # And it says it reviewed nothing, which is what it did: the review a `just plan`
    # leaves behind is that launch's own closeout, and this command run on its own with
    # the opt-out reaches no step at all. A caller reading this as "reviewed and stopped"
    # would take a plan nothing has read to be cleared.
    assert "no plan was reviewed or checked" in stopped.stderr, stopped.stderr
    (task,) = plan_store.read_tasks(drafted.qualified)
    assert plan_review.RECORD_KEY not in task.metadata, (
        "the opt-out spent a judged review turn on a plan it then did nothing with"
    )
    assert not (bench.runs / f"{named}{DESIGN_RUN_SUFFIX}").exists(), stopped.stderr
    assert _records(bench.destination) == [], "the opt-out still reached the destination"
    assert "holds the plan at" not in stopped.stdout, stopped.stdout


class Unrunnable(NamedTuple):
    """One way of asking for a flow that cannot be run at all."""

    what: str
    #: What the caller types after the recipe name, given a brief that is a task.
    arguments: tuple[str, ...]
    #: A fragment the refusal has to carry.
    names: str


#: Everything a flow refuses before it reviews, checks, launches or copies anything. Each
#: is a caller's own invocation rather than a plan's content, so each has to end at the
#: status that means "nothing was judged" — a caller who read one of these as a review
#: refusal would go and edit criteria nobody objected to.
UNRUNNABLE_FLOWS = (
    Unrunnable("a brief that is not a task", ("--missing-brief",), "must be the brief"),
    Unrunnable(
        "the planner's own turn budget",
        ("--max-turns", "40"),
        "--max-turns names the planner's budget",
    ),
    Unrunnable(
        "half a placement",
        ("--direct", "--repo", "other"),
        "carries both or neither",
    ),
    Unrunnable("a run id nothing can use", ("--name", "with.dots"), "is not a run id"),
)


@pytest.mark.xdist_group("finish-plan")
@pytest.mark.parametrize("unrunnable", UNRUNNABLE_FLOWS, ids=lambda row: row.what)
def test_a_flow_that_cannot_run_at_all_is_told_apart_from_every_refusal(
    tmp_path: Path, oneharness_bin: str, unrunnable: Unrunnable
) -> None:
    """Nothing was judged, so the status is not one of the three that judged something.

    A caller reading only the status branches on it, and each of the three refusals names
    a different thing to correct. "This checkout, or this command line, cannot run the
    flow" is a fourth, and folding it into any of them sends somebody to edit a plan that
    is perfectly good.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path, oneharness_bin, PASSES)
    drafted = _draft(f"finish-plan-{unrunnable.what.replace(' ', '-')}")
    brief = _brief(tmp_path, drafted)
    arguments = (
        unrunnable.arguments
        if unrunnable.arguments[0] == "--missing-brief"
        else (str(brief), *unrunnable.arguments)
    )

    refused = _just("finish-plan", *arguments, environment=bench.environment, seconds=120)

    assert refused.returncode == UNRUNNABLE, (
        f"{unrunnable.what} exited {refused.returncode}:\n{refused.stdout}{refused.stderr}"
    )
    assert unrunnable.names in refused.stderr, refused.stderr


@pytest.mark.xdist_group("finish-plan")
def test_a_tail_run_id_something_has_already_taken_is_refused_before_anything_is_reviewed(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The id the flow prints has to be the id the engine mints, or a question queues elsewhere.

    `onepipeline` mints `<name>-2` when a run root of that name exists, so a flow that
    launched under a taken name would print one channel and use another — and a blocking
    question would queue where nobody is watching. Refused before the review, because a
    judged turn spent on a flow that cannot launch is a turn spent for nothing.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path, oneharness_bin, PASSES)
    drafted = _draft("finish-plan-taken")
    named = RunId("finish-plan-e2e-taken")
    (bench.runs / f"{named}{DESIGN_RUN_SUFFIX}").mkdir(parents=True)

    refused = _just(
        "finish-plan",
        str(_brief(tmp_path, drafted)),
        "--name",
        named,
        "--to",
        DESTINATION,
        environment=bench.environment,
        seconds=120,
    )

    assert refused.returncode == UNRUNNABLE, f"{refused.stdout}{refused.stderr}"
    assert f"run '{named}{DESIGN_RUN_SUFFIX}' already exists" in refused.stderr, refused.stderr
    (task,) = plan_store.read_tasks(drafted.qualified)
    assert plan_review.RECORD_KEY not in task.metadata, (
        "a flow refused for a taken run id had already spent a judged review turn"
    )


@pytest.mark.xdist_group("finish-plan")
def test_a_plan_the_review_could_not_read_at_all_is_not_a_refused_review(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """Nothing was judged, so this is not the status that means a reviewer refused it.

    A brief naming a plan in a source this host does not have is the ordinary way to reach
    it — a typo in the one line of a brief anything parses. The review answers that it
    could not read the project, and folding that into the refusal beside it would send an
    operator to correct criteria no reviewer ever read.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path, oneharness_bin, PASSES)
    named = RunId("finish-plan-e2e-unreadable-plan")
    brief = tmp_path / "cursor-shape.md"
    brief.write_text(
        "## What\nDecide the cursor's shape.\n\n"
        "Plan project: no-such-source:cursor-shape\n\n"
        "## Why\nThe view cannot deep-link until it is settled.\n\n"
        "## Acceptance criteria\n- The cursor's shape and its type are stated.\n",
        encoding="utf-8",
    )

    refused = _just(
        "finish-plan",
        str(brief),
        "--name",
        named,
        "--to",
        DESTINATION,
        environment=bench.environment,
        seconds=180,
    )

    assert refused.returncode == UNRUNNABLE, (
        f"a plan the review could not read exited {refused.returncode}:\n"
        f"{refused.stdout}{refused.stderr}"
    )
    assert "could not be reviewed" in refused.stderr, refused.stderr
    assert not (bench.runs / f"{named}{DESIGN_RUN_SUFFIX}").exists(), refused.stderr
    assert _records(bench.destination) == [], "a flow that judged nothing reached the destination"


@pytest.mark.xdist_group("finish-plan")
def test_a_design_document_launch_that_does_not_settle_ends_the_flow_at_its_own_status(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The document was not written, so nothing is copied and the status says which step.

    Reached the way an operator reaches it: a plan store whose configured sources do not
    include the one this flow writes its own generated project into, so the engine reads
    that project and finds no node in it. The plan and its document are unchanged where
    they were drafted, the destination is untouched, and the message names the run to read
    and says to run the command again once the document exists — because everything before
    this step is already recorded and repeating it costs no second judged turn.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path, oneharness_bin, PASSES)
    # The source `scripts/finish-plan.sh` writes its own one-node project into, dropped
    # from what the store answers about: the record is written, and the launch then reads
    # a project with nothing in it.
    bench.environment["ONETASKGRAPH_DEFAULT_SOURCES"] = f"{FIXTURE_SOURCE},{DESTINATION}"
    drafted = _draft("finish-plan-unlaunchable-document")
    named = RunId("finish-plan-e2e-launch-failed")

    try:
        refused = _just(
            "finish-plan",
            str(_brief(tmp_path, drafted)),
            "--name",
            named,
            "--to",
            DESTINATION,
            environment=bench.environment,
        )
    finally:
        # The flow wrote its own generated project into this checkout's plan-authoring
        # root before the launch refused it, so it is taken back here rather than left
        # for the next launch of that name to read.
        _stop(bench, f"{named}{DESIGN_RUN_SUFFIX}")

    assert refused.returncode == LAUNCH_FAILED, (
        f"a design-document launch that did not settle exited {refused.returncode}:\n"
        f"{refused.stdout}{refused.stderr}"
    )
    assert f"run {named}{DESIGN_RUN_SUFFIX} did not settle" in refused.stderr, refused.stderr
    assert _records(bench.destination) == [], "a flow whose document was never written copied"
    # And the review it did spend stands, so running the command again once the launch can
    # settle costs no second judged turn.
    (task,) = plan_store.read_tasks(drafted.qualified)
    assert plan_review.RECORD_KEY in task.metadata, (
        "the review this flow spent was not recorded, so repeating the command would "
        "spend a second judged turn on content something has already read"
    )


@pytest.mark.xdist_group("finish-plan")
def test_a_detached_plan_launch_hands_this_command_back_on_its_own_receipt(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """`--detach` hands back before the plan exists, so the tail is the operator's to run.

    Every step here is about the plan the planner writes, and a detached launch returns
    the moment its run is recorded — so running them there would review a project nothing
    has authored. What `just plan` owes instead is the command that finishes the plan once
    the planner has settled, and it owes it **on the one receipt it already prints**: a
    second success line is noise the next reader learns to skip, and this one is known
    before the launch is even made.

    The name and the placement are in that command rather than left to their defaults,
    because a caller who re-ran it bare would finish the plan under a different run id and
    against a different pair of checkouts than the one that planned it.

    It lives beside the tail rather than beside the planner's own journeys because that is
    what it is about, and because a real launch belongs behind this project's own edge.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path, oneharness_bin, PASSES)
    drafted = _draft("finish-plan-handover")
    named = RunId("finish-plan-e2e-detached")
    brief = _brief(tmp_path, drafted)

    launch = _just(
        "plan",
        str(brief),
        "--name",
        named,
        "--to",
        DESTINATION,
        "--detach",
        environment=bench.environment,
        seconds=180,
    )
    try:
        assert launch.returncode == OK, f"the detached launch failed:\n{launch.stderr}"
        reported = launch.stdout + launch.stderr
        assert f"just finish-plan {brief}" in reported, (
            f"a detached launch never said how to finish the plan it started:\n{reported}"
        )
        assert f"--name {named}" in reported, (
            f"the handover drops the name this flow's runs are derived from:\n{reported}"
        )
        # The placement as this launch *resolved* it: the repository by the normalized
        # origin its record carries rather than by the alias the default names, so the
        # tail is handed the value it would resolve to anyway.
        assert (
            f"--repo {PLANNING_FLOW_ORIGIN} --execution-checkout {EXECUTION_ALIAS}" in reported
        ), f"the handover drops the placement this launch resolved:\n{reported}"
        assert f"--to {DESTINATION}" in reported, (
            f"the handover drops the destination this launch was given:\n{reported}"
        )
        # One line, and this is it: the handover rides on the receipt the recipe already
        # owns rather than being printed after the launch.
        owned = [line for line in reported.splitlines() if line.startswith("plan: ")]
        assert len(owned) == 1, (
            f"a successful launch printed {len(owned)} of its own lines: {owned}"
        )
        # And it stopped there: the tail's own run is named from this one, so a ledger
        # holding it would be a detached launch having run steps about a plan that does
        # not exist yet.
        assert not (bench.runs / f"{named}{DESIGN_RUN_SUFFIX}").exists(), reported
        assert _records(bench.destination) == [], "a detached launch reached the destination"
    finally:
        _stop(bench, named)
