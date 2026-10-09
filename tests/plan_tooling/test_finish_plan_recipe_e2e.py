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
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple, NewType, TypedDict, cast

import plan_fixture_source
import plan_root_variable
import pytest
import short_state
from fake_backend import (
    ENVIRONMENT_KEYS_ENV,
    JUDGE_CONFIG_NAME,
    PROMPT_LOG_ENV,
    RUN_ON_MARKER_ENV,
    RecordedTurn,
)
from nx_workspace import answering_this_checkouts_origin, copy_working_tree
from project_fixtures import (
    budget_section,
    budgeted,
    helper,
    no_budgets,
    owning,
    register_stand_in,
)
from published_tools import ONETASKGRAPH_BIN
from scratch_identity import PLANNING_FLOW_ORIGIN, Identity, seeded
from test_approve_design_recipe_e2e import RETIRED_DESIGN, RETIRED_TEMPLATE
from waits import timeout as e2e_timeout

from orchestrator import design_approval, design_chain, plan_review, plan_store
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

#: The two aliases this repository's own identity registers, seeded here against a
#: scratch registry so nothing a flow does reaches this host's own. The node the tail
#: writes is a direct node and opens no session, so the launch itself reaches no
#: registry any more; the scratch one stays because a recipe that started opening
#: sessions again must do so there rather than in the directories live dispatches use.
PUBLICATION_ALIAS = "ai-orchestrator"
EXECUTION_ALIAS = "ai-orchestrator-isolated"

#: The source a plan is drafted in here — this process's own fixture store, which is
#: what stands in for the gitignored `authoring` root.
FIXTURE_SOURCE = plan_fixture_source.SOURCE

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
DESIGN_TASK_MARKER = "Write the design document a person reviews the plan"

#: The pinned engine this checkout installs, which the doubled writer runs as a real agent
#: would: its `template resolve` is what states the design-doc template it renders.
ENGINE_BIN = REPO_ROOT / ".venv" / "bin" / "onepipeline"

#: The provenance key the plan store records a rendering under, and the references the
#: writer's task and its document each record there.
PROVENANCE_KEY = "onetaskgraph.template"
WRITER_TASK_TEMPLATE = "onepipeline:plan-task"
DOCUMENT_TEMPLATE = "onepipeline:design-doc"

#: The key `onetaskgraph` stamps on a record it created by copying, naming what it copied.
ORIGIN_KEY = "onetaskgraph.origin"

#: What a design-doc dispatch drafting into the fixture store names its document, and what
#: one drafting into the authoring store has to name it: the id the tail fixes in its task,
#: which is the one file its judge is pointed at.
FIXTURE_DOCUMENT_SUFFIX = "-document"
DESIGN_DOCUMENT_SUFFIX = "-design"

#: The variable onejudge takes a judge's artifact list from, recorded on every judge-side
#: turn. It is held to the one the launch really sets by the authoring journey below, which
#: asserts the recorded value is the document's path — so a name here that drifted from the
#: script's fails there rather than letting the absence asserted beside it pass vacuously.
JUDGE_ARTIFACTS_ENV = "ONEJUDGE_ARTIFACTS"

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
WRITER_REFUSED = 6
DOCUMENT_REFUSED = 7

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


# llmlint: ignore[suppressions_justified] onetaskgraph owns this open JSON schema; the one
# field read below is narrowed at its subscript.
def _design_documents(held: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The listed documents of a plan recording the `design-doc` template, as the gate reads it."""
    return [
        one
        for one in held
        if (one["item"]["metadata"].get("onetaskgraph.template") or {}).get("template")
        == design_approval.TEMPLATE_REFERENCE
    ]


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


def _task(criteria: str, section: str = "") -> str:
    return (
        "## What\n\nAdd the paginated listing and the test that drives it.\n\n"
        f"## Why\n\nAn operator cannot see past the first screen of nodes.\n\n{section}"
        f"## Acceptance criteria\n\n{criteria}\n\n"
        f"{(REPO_ROOT / APPENDIX).read_text(encoding='utf-8').strip()}\n"
    )


def _draft(
    name: str,
    criteria: str = STATES_ITS_BAR,
    *,
    root: Path | None = None,
    source: str = FIXTURE_SOURCE,
    document_suffix: str = FIXTURE_DOCUMENT_SUFFIX,
    repositories: Sequence[str] = (),
    budgets: Mapping[str, object] | bool = True,
    owned: Sequence[Mapping[str, object]] = (),
) -> Drafted:
    """Draft an unreviewed plan into a local store, with no design document yet.

    The fixture store unless a journey names another: the authoring store is the one a
    planner really writes into, and the one whose design document the tail points its
    judge at.

    Deliberately not `project_fixtures.local_project`, which writes a design document and
    approves it: what this flow *produces* is that document, so a plan arriving with one
    already would leave the store holding two and no reader able to say which a person
    approved.

    Written through this repository's own record renderer, so the store reads these
    records the way it reads a planner's own rather than a hand-written shape only this
    journey would produce. ``repositories`` gives the plan one task per origin, each naming
    that one repository; with none, its one task names no repository.

    The plan carries its plan-level budget answers, as every plan does: one needing no
    budget unless ``budgets`` gives its answers, and none at all when it is ``False``. Its
    first task owns ``owned``: its body carries their `## Budgets` section and its
    `orchestrator.budgets` record holds them.
    """
    native = f"test-{os.getpid()}-{name}"
    # Resolved here rather than as a default argument: this process's own fixture root is
    # stated by a session fixture, which runs long after this module is imported.
    root = plan_fixture_source.root() if root is None else root
    written = write_plan_project(
        root,
        {
            "schema_version": 3,
            "goal": {"text": "Deliver the paginated listing"},
            "name": native,
            "tasks": [
                {
                    "id": "decide-the-cursor" if index == 0 else f"decide-the-cursor-{index}",
                    "persona": "engineer",
                    "title": "feat: page the node listing"
                    if index == 0
                    else f"feat: page the node listing in part {index}",
                    "task": _task(
                        criteria, budget_section(list(owned)) if owned and index == 0 else ""
                    ),
                    **({"repo": repositories[index]} if repositories else {}),
                }
                for index in range(max(1, len(repositories)))
            ],
        },
        native_id=native,
    )
    variable = f"ONETASKGRAPH_SOURCES__{source.upper().replace('-', '_')}__CONFIG__ROOT"
    if owned:
        owning(
            source, written, "decide-the-cursor", list(owned), {**os.environ, variable: str(root)}
        )
    if budgets is not False:
        budgeted(
            source,
            written,
            no_budgets(repositories) if budgets is True else budgets,
            {**os.environ, variable: str(root)},
        )
    return Drafted(
        project=native,
        qualified=f"{source}:{native}",
        task_title="feat: page the node listing",
        document=f"{native}{document_suffix}",
        document_path=root / "documents" / f"{native}{document_suffix}.md",
    )


def _answers(drafted: Drafted) -> dict[str, object]:
    """The design-doc template's answers a design-doc dispatch composed for ``drafted``.

    The paid model's answer and nothing else: what renders them, and what makes the result
    a document *of the plan's project* in the plan store, is the pinned engine's resolve
    piped into the store's own `document create`, which the dispatch runs.
    """
    return {
        "what": "A paginated node listing.",
        "why": "An operator cannot see past the first screen.",
        "architecture": "One route, one view.",
        "units": [
            {
                "name": "Listing",
                "repository": "ai-orchestrator",
                "part": "",
                "summary": "The listing pages behind an opaque cursor.",
                "reversible": [{"title": "The cursor", "text": "An opaque token."}],
                "decisions": [],
            }
        ],
        "acceptance_criteria": ["The listing pages."],
        "planned_tasks": [
            {
                "task": drafted.task_title,
                "unit": "Listing",
                "delivers": "the route",
                "depends_on": "none",
                "location": "the store's own location",
            }
        ],
    }


#: The step a stand-in writer runs to copy the budget command's output into its answers:
#: the file of answers, then the command's printed JSON, merged over it in place.
MERGES_THE_BUDGETS = Path(__file__).resolve().parent / "merge_answers.py"


def _staged_answers(tmp_path: Path, drafted: Drafted) -> Path:
    """Stage the answers where the design-doc dispatch composed them.

    This is the scripted half and the whole of it. **Rendering and storing are not
    staged**: the plan store holds no document until the dispatch itself runs the pinned
    resolve and the store's own command line, so what the read-back proves is that it ran.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    answers = tmp_path / f"{drafted.document}.answers.json"
    answers.write_text(json.dumps(_answers(drafted)), encoding="utf-8")
    return answers


class Bench(NamedTuple):
    """One throwaway host these journeys run a flow on: its environment and its stores."""

    environment: dict[str, str]
    destination: Path
    runs: Path
    tmp_path: Path
    #: The scratch identity of this repository's origin the bench registers.
    identity: Identity


def _bench(tmp_path: Path, oneharness_bin: str, *answers: object) -> Bench:
    """The environment a flow runs in, against a registry, a runs root and a board of its own."""
    identity = seeded(
        tmp_path,
        publication=PUBLICATION_ALIAS,
        execution=EXECUTION_ALIAS,
        origin=PLANNING_FLOW_ORIGIN,
    )
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
    environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp_path))
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
        identity=identity,
    )


def _stores_the_document(bench: Bench, drafted: Drafted, repository: str | None = None) -> None:
    """Script the one command the design-doc dispatch runs to render and store its answers.

    ``repository`` is the origin the resolve command its task names carries, for a plan
    whose tasks all name that one repository.
    """
    keyed = bench.tmp_path / f"commands-{drafted.project}.json"
    answers = _staged_answers(bench.tmp_path / drafted.project, drafted)
    source, _, project = drafted.qualified.partition(":")
    keyed.write_text(
        json.dumps(
            {
                DESIGN_TASK_MARKER: [
                    [
                        "bash",
                        "-c",
                        # The pinned engine's resolve, piped into the pinned store's own
                        # create, into the plan's own source and project: what the task
                        # tells the dispatch to run, run where the dispatch runs it.
                        # The budget answers are what the task's budget command prints,
                        # copied into the answers as the writer copies them.
                        'printed=$(uv run python -m orchestrator.plan_budgets "$3:$4") &&'
                        ' python3 "$9" "$7" "$printed" &&'
                        ' "$1" template resolve design-doc ${8:+--repository "$8"} --json'
                        ' | "$2" document create "$3"'
                        ' --project "$4" --title "$5" --id "$6" --template-loader -'
                        ' --answers "$7" --no-interactive',
                        "store-the-document",
                        str(ENGINE_BIN),
                        str(ONETASKGRAPH_BIN),
                        source,
                        project,
                        f"Design: {drafted.project}",
                        drafted.document,
                        str(answers),
                        repository or "",
                        str(MERGES_THE_BUDGETS),
                    ]
                ]
            }
        ),
        encoding="utf-8",
    )
    bench.environment[RUN_ON_MARKER_ENV] = str(keyed)
    bench.environment[PROMPT_LOG_ENV] = str(bench.tmp_path / f"turns-{drafted.project}.jsonl")
    bench.environment[ENVIRONMENT_KEYS_ENV] = JUDGE_ARTIFACTS_ENV


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
    """End every run a flow left on this bench's ledger, and take back its projects.

    The design project's task is taken back by the path the store reports for it, because
    the store names a task it creates rather than this module.
    """
    for run in runs:
        _just("stop", run, environment=bench.environment, seconds=60)
        listed = subprocess.run(
            [str(ONETASKGRAPH_BIN), "task", "list", "--source", AUTHORING_SOURCE]
            + ["--project", run, "--json"],
            cwd=REPO_ROOT,
            env=bench.environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )
        if listed.returncode == 0:
            for held in json.loads(listed.stdout)["items"]:
                path = (held["item"].get("location") or {}).get("path")
                if path:
                    Path(path).unlink(missing_ok=True)
        (REPO_ROOT / ".plans" / "projects" / f"{run}.md").unlink(missing_ok=True)


class StoredTask(TypedDict):
    """One task as `onetaskgraph task list` reports it, in the two fields this suite reads.

    `repositories` is the record's own top-level field — where a hosted repository is
    named, as its normalized origin — and `metadata` is the map the reserved
    `onepipeline.repo` key would sit in if the record still named it there.
    """

    repositories: list[str]
    metadata: dict[str, object]
    content: str


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
    # llmlint: ignore[suppressions_justified] onetaskgraph owns this open JSON schema; the three
    # fields `StoredTask` names are subscripted by every reader, so a record without one fails
    # loudly there.
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
    held = _design_documents(cast(dict[str, Any], json.loads(documents.stdout))["items"])
    assert len(held) == 1, f"the destination holds {len(held)} documents of this plan: {held}"
    assert f"holds its design document at {held[0]['item']['location']['path']}" in reported, (
        reported
    )


@pytest.mark.xdist_group("finish-plan")
def test_the_design_document_node_names_no_repository_because_it_is_a_direct_node(
    finished: Finished,
) -> None:
    """The tail's node is a direct node, so its record names no repository anywhere.

    Launched with its defaults, so what is read is the record the recipe writes when
    nobody tells it anything: no `repositories`, nothing on the reserved
    `onepipeline.repo` key, and no execution checkout. It is a direct node for the reason
    the planner's is one — it writes a document into the plan store and never a commit,
    and the adopted engine settles a lifecycle dispatch that commits nothing `failed` as
    `empty-branch` while the declaration that would accept the empty branch settles the
    node without dispatching it (https://github.com/nickderobertis/onepipeline/issues/238).
    """
    assert finished.design_task["repositories"] == [], finished.design_task
    metadata = finished.design_task["metadata"]
    assert "onepipeline.repo" not in metadata, metadata
    assert "onepipeline.execution_checkout" not in metadata, metadata


def _criteria(content: str) -> str:
    """The block a task's `## Acceptance criteria` heading opens, up to the next heading."""
    _, _, after = content.partition("## Acceptance criteria\n")
    assert after, f"the task carries no acceptance criteria heading:\n{content}"
    return after.split("\n## ", 1)[0]


#: Each obligation the writer's task owes its judge, as a fragment its criteria have to
#: carry. Stated here rather than read out of `scripts/finish-plan.sh`, so a recipe that
#: stopped composing one fails this rather than agreeing with itself.
WRITER_OBLIGATIONS = (
    # Answers in each variable's shape, meeting every rule the chain's guidance states.
    "The document answers every variable `onepipeline template resolve design-doc --json | "
    "onetaskgraph template variables --template-loader -` lists",
    "in the shape each variable\u2019s description states",
    "every rule the guidance comments of the chain `onepipeline template resolve design-doc "
    "--json` resolves state",
    # Stored through the pinned resolve piped into the store, as that plan's document.
    "`onepipeline template resolve design-doc --json` piped into `onetaskgraph document "
    "create --id ",
    "--template-loader - --no-interactive` wrote it, which replaces a document the store "
    "already holds by that id whole",
    # Every row's location is the store's own answer.
    "location is the location the plan store reports for that task",
    # And where the store put it is reported.
    "This dispatch reports where the store put the document",
)


@pytest.mark.xdist_group("finish-plan")
def test_the_design_document_nodes_task_is_a_plan_task_rendering_owing_each_obligation(
    finished: Finished,
) -> None:
    """The writer's task was created through the template, and states what it is judged on.

    Its provenance names the engine's `plan-task` template, which only a `task create`
    fed `onepipeline template resolve plan-task --json` records; and its criteria carry
    each obligation of the writer's task — the document's own id among them — while the
    brief's criteria, which are the *plan's*, are not among them.
    """
    provenance = finished.design_task["metadata"].get(PROVENANCE_KEY)
    assert isinstance(provenance, dict), finished.design_task["metadata"]
    assert provenance.get("template") == WRITER_TASK_TEMPLATE, provenance
    criteria = _criteria(finished.design_task["content"])
    for obligation in WRITER_OBLIGATIONS:
        assert obligation in criteria, f"the writer's criteria lack {obligation!r}:\n{criteria}"
    document_id = f"{finished.drafted.project}{DESIGN_DOCUMENT_SUFFIX}"
    assert f"under the document id `{document_id}`" in criteria, criteria
    assert f"`{finished.drafted.qualified}`" in criteria, criteria
    assert "The cursor's shape and its type are stated." not in criteria, (
        f"the brief's own criterion, which is the plan's, reached the writer's:\n{criteria}"
    )


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `plan-tooling` is the leaf
# project keyed on `planToolingWorkspace`, the edge this rule asks for, and every other journey
# of this module already runs behind it. That key names the recipes, scripts, templates and
# package these journeys drive, so narrowing it would memoize a verdict over a tree never run.
#: A second repository's origin, for a plan whose tasks name several. Registered in the
#: bench's scratch registry as a stand-in checkout, never fetched.
OTHER_ORIGIN = "github.com/nickderobertis/onepipeline"

#: A repository layer of the design-doc template, overriding one guidance block of the host
#: template the way a repository overrides the bar its own documents are written to.
LAYER = (
    '{% extends "onepipeline/host/design-doc.md.j2" %}\n'
    "{% block what_guidance %}{# WHAT. Say what changes for this repository's callers. #}"
    "{% endblock %}\n"
)

#: The resolve command a writer's task names, as the task's prose spells it.
RESOLVE_NAMED = re.compile(r"onepipeline template resolve design-doc(?: --repository \S+)? --json")


def _named_resolves(content: str) -> set[str]:
    """Every design-doc resolve command ``content`` names."""
    return set(RESOLVE_NAMED.findall(content))


@pytest.mark.xdist_group("finish-plan")
def test_a_plan_naming_no_repository_has_its_writer_resolve_through_this_directory(
    finished: Finished,
) -> None:
    """The fixture's plan names no repository, so its writer's resolve names none."""
    content = finished.design_task["content"]
    assert _named_resolves(content) == {design_chain.resolve_command(None)}, content
    assert "--repository" not in content, content


@pytest.mark.xdist_group("finish-plan")
@pytest.mark.parametrize("plan", ["one layered repository", "several repositories"])
def test_the_writers_task_names_the_resolve_command_the_rule_gives_for_the_plan(
    tmp_path: Path, oneharness_bin: str, plan: str
) -> None:
    """A plan in one repository resolves through that repository's layer, and no other plan does.

    The whole tail on a plan whose tasks all name this repository's origin, whose
    registered checkouts carry a layer extending the host template: the writer's task names
    `--repository <that origin>`, the command it names resolves a chain holding that layer,
    and the document the writer stored records that chain's digest. Then on a plan whose
    tasks name two repositories, whose writer's task names no repository at all.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path, oneharness_bin, PASSES)
    layered = plan == "one layered repository"
    if layered:
        origins: Sequence[str] = (PLANNING_FLOW_ORIGIN, PLANNING_FLOW_ORIGIN)
        for checkout in (bench.identity.publication, bench.identity.execution):
            layer = checkout / ".onepipeline" / "templates" / "design-doc.md.j2"
            layer.parent.mkdir(parents=True, exist_ok=True)
            layer.write_text(LAYER, encoding="utf-8")
        repository: str | None = PLANNING_FLOW_ORIGIN
    else:
        register_stand_in(bench.identity.home, OTHER_ORIGIN, tmp_path / "other")
        origins = (PLANNING_FLOW_ORIGIN, OTHER_ORIGIN)
        repository = None
    drafted = _draft(f"finish-plan-rule-{plan.split()[0]}", repositories=origins)
    _stores_the_document(bench, drafted, repository)
    run = RunId(f"finish-plan-rule-{plan.split()[0]}")
    try:
        finish = _just(
            "finish-plan",
            str(_brief(tmp_path, drafted)),
            "--name",
            run,
            "--to",
            DESTINATION,
            environment=bench.environment,
        )
        assert finish.returncode == OK, f"the tail failed:\n{finish.stdout}\n{finish.stderr}"
        content = _design_task(bench, run)["content"]
    finally:
        _stop(bench, f"{run}{DESIGN_RUN_SUFFIX}")

    named = design_chain.resolve_command(repository)
    assert _named_resolves(content) == {named}, content
    if not layered:
        assert "--repository" not in content, content
        return
    resolved = subprocess.run(
        [str(ENGINE_BIN), *named.split()[1:], "--template-root", str(REPO_ROOT / "templates")],
        cwd=REPO_ROOT,
        env=bench.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert resolved.returncode == 0, resolved.stderr
    chain = json.loads(resolved.stdout)
    assert chain["layer"] == "repository", chain
    assert any(
        entry["layer"] == "repository"
        and entry["path"].endswith(".onepipeline/templates/design-doc.md.j2")
        for entry in chain["chain"]
    ), chain
    (document,) = design_approval.design_documents(
        drafted.qualified, plan_store.read_documents(drafted.qualified)
    )
    provenance = document.metadata[PROVENANCE_KEY]
    assert isinstance(provenance, dict), document.metadata
    assert provenance["digest"] == chain["digest"], (provenance, chain["digest"])


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


def _landed(bench: Bench, drafted: Drafted) -> str:
    """The destination's copy of ``drafted``'s plan, as the store qualifies it."""
    listed = _just(
        "plans",
        "project",
        "list",
        "--source",
        DESTINATION,
        "--json",
        environment=bench.environment,
        seconds=120,
    )
    assert listed.returncode == 0, listed.stderr
    # llmlint: ignore[suppressions_justified] onetaskgraph owns this open JSON schema.
    projects = cast(dict[str, Any], json.loads(listed.stdout))["items"]
    (landed,) = [
        one["id"]
        for one in projects
        if one["item"]["metadata"].get(ORIGIN_KEY) == drafted.qualified
    ]
    return str(landed)


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] This reads the one flow
# the `finished` fixture already drove, behind `plan-tooling`'s own `planToolingWorkspace` edge
# with every other reading of it, and adds two recipe calls and no launch of its own.
@pytest.mark.xdist_group("finish-plan")
def test_the_stored_design_document_is_a_rendering_a_person_can_approve_and_launch(
    finished: Finished,
) -> None:
    """What the writer stored is a design-doc rendering, and the bar downstream of it holds.

    Read on the destination, which is where a person approves it: the copy carries the
    rendering's provenance, the real `just approve-design` records an approval for it, and
    the real launch gate then lets the approved plan through.
    """
    landed = _landed(finished.bench, finished.drafted)
    documents = _just(
        "plans",
        "document",
        "list",
        "--project",
        landed,
        "--json",
        environment=finished.bench.environment,
        seconds=120,
    )
    assert documents.returncode == 0, documents.stderr
    # llmlint: ignore[suppressions_justified] onetaskgraph owns this open JSON schema.
    (held,) = _design_documents(cast(dict[str, Any], json.loads(documents.stdout))["items"])
    provenance = held["item"]["metadata"].get(PROVENANCE_KEY)
    assert isinstance(provenance, dict), held["item"]["metadata"]
    assert provenance.get("template") == DOCUMENT_TEMPLATE, provenance

    approved = _just("approve-design", landed, environment=finished.bench.environment)
    assert approved.returncode == OK, f"{approved.stdout}\n{approved.stderr}"
    assert "recorded the approval" in approved.stdout, approved.stdout
    gated = subprocess.run(
        ["uv", "run", "orchestrator-launch-gate", landed],
        cwd=REPO_ROOT,
        env=finished.bench.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert gated.returncode == OK, gated.stderr


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `plan-tooling` is the leaf
# project keyed on `planToolingWorkspace`, the edge this rule asks for, and every other journey
# of this module already runs behind it. That key names the recipes, scripts, templates and
# package these journeys drive, so narrowing it would memoize a verdict over a tree never run.
@pytest.mark.xdist_group("finish-plan")
def test_a_document_whose_answers_no_longer_fit_is_repaired_by_the_tail_it_is_sent_to(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The refusal's repair, run: `just finish-plan` rewrites the document, which approves.

    The plan holds a document rendered from answers the template in force no longer
    declares, so `just approve-design` refuses it and names `just finish-plan <brief>`. Run
    on the plan's brief, the tail's writer stores the document under the id it already
    holds, as its task says, from answers to the current variables, which replaces it whole;
    the plan and the copy that lands on the destination are then ones `just approve-design`
    records.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path, oneharness_bin, PASSES)
    drafted = _draft("finish-plan-retired")
    retired_root = tmp_path / "retired-templates"
    shutil.copytree(REPO_ROOT / "templates", retired_root)
    (retired_root / "design-doc.md.j2").write_text(RETIRED_TEMPLATE, encoding="utf-8")
    retired_answers = tmp_path / "retired.answers.json"
    retired_answers.write_text(json.dumps(dict(RETIRED_DESIGN)), encoding="utf-8")
    source, _, project = drafted.qualified.partition(":")
    seeded_document = subprocess.run(
        [
            "bash",
            "-c",
            '"$1" template resolve design-doc --template-root "$2" --json'
            ' | "$3" document create "$4" --project "$5" --title "$6" --id "$7"'
            ' --template-loader - --answers "$8" --no-interactive',
            "seed-the-retired-document",
            str(ENGINE_BIN),
            str(retired_root),
            str(ONETASKGRAPH_BIN),
            source,
            project,
            f"Design: {drafted.project}",
            drafted.document,
            str(retired_answers),
        ],
        cwd=REPO_ROOT,
        env=bench.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert seeded_document.returncode == 0, seeded_document.stderr

    refused = _just("approve-design", drafted.qualified, environment=bench.environment)
    assert refused.returncode != OK, f"{refused.stdout}\n{refused.stderr}"
    assert "just finish-plan <brief>" in refused.stderr, refused.stderr

    _stores_the_document(bench, drafted)
    run = RunId("finish-plan-retired")
    try:
        finish = _just(
            "finish-plan",
            str(_brief(tmp_path, drafted)),
            "--name",
            run,
            "--to",
            DESTINATION,
            environment=bench.environment,
        )
        assert finish.returncode == OK, f"the tail failed:\n{finish.stdout}\n{finish.stderr}"
    finally:
        _stop(bench, f"{run}{DESIGN_RUN_SUFFIX}")

    # The plan refused above, and the copy a person approves, both approve now.
    for approving in (drafted.qualified, _landed(bench, drafted)):
        approved = _just("approve-design", approving, environment=bench.environment)
        assert approved.returncode == OK, f"{approving}: {approved.stdout}\n{approved.stderr}"
        assert "recorded the approval" in approved.stdout, approved.stdout


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


def _judge_turns(bench: Bench, drafted: Drafted) -> list[RecordedTurn]:
    """Every turn the design-doc dispatch's judge side took, as the stand-in was handed it.

    The prompt log is the only record of it: the published stream reports that a
    supervisor turn happened and what it ruled, never the prompt it ruled on — which is why
    `tests/e2e/test_design_doc_graph_e2e.py` reads the same record. The review's judged
    turn goes through the codex stand-in and never reaches this log, so every judge-side
    turn here is the design run's.
    """
    log = bench.tmp_path / f"turns-{drafted.project}.jsonl"
    lines = log.read_text(encoding="utf-8").splitlines() if log.is_file() else []
    # Test-owned on both ends: `fake_backend.py` declares the record and writes it.
    turns = [cast(RecordedTurn, json.loads(line)) for line in lines]
    # llmlint: ignore[tests_mirror_real_usage] No view carries a supervisor's delivered prompt.
    judged = [turn for turn in turns if Path(turn["config"] or "").name == JUDGE_CONFIG_NAME]
    assert judged, (
        f"the design run's judge side took no turn at all:\n{json.dumps(turns, indent=2)}"
    )
    return judged


@pytest.mark.xdist_group("finish-plan")
def test_a_plan_outside_the_authoring_store_hands_its_judge_no_artifact(
    finished: Finished,
) -> None:
    """Only the authoring store's root is resolved by the launch, so only it names a file.

    This fixture's plan is held in the fixture store, where its document is stored too, so
    a path under the exported authoring root would name a file that plan never has. The
    judge is handed no artifact list at all rather than a wrong one: neither the document's
    own path nor the one the authoring rule would have composed reaches its prompt.
    """
    authoring_path = f"/documents/{finished.drafted.project}{DESIGN_DOCUMENT_SUFFIX}.md"
    # llmlint: ignore-block[tests_mirror_real_usage] What a judge side is handed is the
    # subject, and no user-facing view carries it: the published stream reports a supervisor
    # turn and its ruling, never its prompt or the environment its harness ran under, so the
    # doubled backend's record is the only reading of it (the one this node's criteria name).
    for turn in _judge_turns(finished.bench, finished.drafted):
        assert turn["environment"].get(JUDGE_ARTIFACTS_ENV) is None, turn["environment"]
        assert authoring_path not in turn["prompt"], turn["prompt"]
        assert str(finished.drafted.document_path) not in turn["prompt"], turn["prompt"]
    # llmlint: ignore-end[tests_mirror_real_usage]


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] This launch is behind the
# edge every other launch of this module already is — `plan-tooling`, the project that owns
# this repository's plan-flow journeys — and this change moves no input of it. What the
# journey proves is `scripts/finish-plan.sh` itself, which is one of the files that edge
# keys on, so a narrower edge would skip it on the change it exists to catch.
@pytest.mark.xdist_group("finish-plan")
def test_the_design_document_judge_is_handed_its_own_projects_document_under_the_authoring_root(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The judge is told where the document is, because git can never show it.

    The authoring root is gitignored, so the judge's only evidence about the tree —
    `git_status` and `git_diff` — reports nothing about a document written there, and one
    dispatch of this role spent its evidence-tool retries asking `git_status` about it. So
    the prompt the judge side is actually handed has to carry the document's own path,
    resolved under the root the launch exported — pointed somewhere other than this
    checkout's own `.plans` here, so a path composed from the checkout could not pass — and
    no other project's document, although another project's is standing in the same flat
    directory.

    The dispatch stores its document under the id the tail's task fixed, and the file
    existing at the path the judge was handed is what says the two agree.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path, oneharness_bin, PASSES)
    authoring = tmp_path / "authoring"
    authoring.mkdir()
    bench.environment[plan_root_variable.name()] = str(authoring)
    drafted = _draft(
        "finish-plan-judge-artifact",
        root=authoring,
        source=AUTHORING_SOURCE,
        document_suffix=DESIGN_DOCUMENT_SUFFIX,
    )
    neighbour = (
        authoring / "documents" / f"test-{os.getpid()}-another-plan{DESIGN_DOCUMENT_SUFFIX}.md"
    )
    neighbour.parent.mkdir(parents=True, exist_ok=True)
    neighbour.write_text(
        '---\ntitle: "Design: another plan"\nproject: "another-plan"\n---\n\nIts own.\n',
        encoding="utf-8",
    )
    _stores_the_document(bench, drafted)
    named = RunId("finish-plan-e2e-judge-artifact")

    try:
        finish = _just(
            "finish-plan",
            str(_brief(tmp_path, drafted)),
            "--name",
            named,
            "--to",
            DESTINATION,
            environment=bench.environment,
        )
    finally:
        _stop(bench, f"{named}{DESIGN_RUN_SUFFIX}")

    assert finish.returncode == OK, f"the tail failed:\n{finish.stdout}\n{finish.stderr}"
    assert drafted.document_path.is_file(), (
        f"the dispatch stored no document at {drafted.document_path}, the path its judge "
        f"is handed; the authoring root holds {_records(authoring)}"
    )
    # llmlint: ignore-block[tests_mirror_real_usage] The prompt a judge side is actually handed
    # is what this node's criteria require captured, and the doubled backend's record is the
    # only place it exists: the published stream never carries a supervisor's prompt or the
    # environment its harness ran under.
    for turn in _judge_turns(bench, drafted):
        assert str(drafted.document_path) in turn["prompt"], (
            f"the judge was not handed {drafted.document_path}, so it has nothing to read "
            f"but what git shows it:\n{turn['prompt']}"
        )
        assert neighbour.name not in turn["prompt"], (
            f"the judge was handed another project's document, {neighbour}:\n{turn['prompt']}"
        )
        assert turn["environment"].get(JUDGE_ARTIFACTS_ENV) == str(drafted.document_path), turn[
            "environment"
        ]
    # llmlint: ignore-end[tests_mirror_real_usage]


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


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
        "a retired placement flag",
        ("--direct",),
        "no longer an option",
    ),
    Unrunnable("a run id nothing can use", ("--name", "with.dots"), "is not a run id"),
    # llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] Two more rows of this
    # module's own refusal table, over the same `just finish-plan` every other row drives;
    # `plan-tooling` is keyed on `planToolingWorkspace`, which names that recipe and its
    # scripts, and each row refuses in under a second before anything is reviewed or launched.
    Unrunnable(
        "a step the tail does not run alone",
        ("--step", "copy", "authoring:some-plan"),
        "one tail step to run alone, review or check; got 'copy'",
    ),
    Unrunnable(
        "a step naming no project",
        ("--step", "check"),
        "--step check runs over exactly one nonempty SOURCE:PROJECT; got 0 argument(s)",
    ),
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
)

#: The invocations that take no brief, so none is put in front of their arguments.
BRIEFLESS = ("--missing-brief", "--step")


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
        if unrunnable.arguments[0] in BRIEFLESS
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


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] An existing journey of `plan-tooling`, updated to the planning flow's new handover; where it runs is that project's split, which `tests/plan_tooling/AGENTS.md` states and this change leaves as it was.  # noqa: E501
@pytest.mark.xdist_group("finish-plan")
def test_a_detached_plan_launch_hands_this_command_back_on_its_own_receipt(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """`--detach` hands back before the plan exists, so carrying the flow on is the operator's.

    Every step here is about the plan the planner writes, and a detached launch returns
    the moment its run is recorded — so running them there would review a project nothing
    has authored. What `just plan` owes instead is the command that carries the flow on —
    to its spikes, its finalize and this tail — once the planner has settled, and it owes it
    **on the one receipt it already prints**: a second success line is noise the next reader
    learns to skip, and this one is known before the launch is even made.

    The name is in that command rather than left to its default, because a caller who
    re-ran it bare would finish the plan under a different run id than the one that
    planned it.

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
        assert f"just plan {brief}" in reported and "--resume spikes" in reported, (
            f"a detached launch never said how to carry on the plan it started:\n{reported}"
        )
        assert f"--name {named}" in reported, (
            f"the handover drops the name this flow's runs are derived from:\n{reported}"
        )
        # No placement: both of the flow's nodes are direct nodes and the flags that once
        # named one are refused, so a handover carrying one would refuse the tail.
        for retired in ("--repo", "--execution-checkout", "--direct"):
            assert retired not in reported, (
                f"the handover names the retired flag {retired}, which the tail refuses:\n"
                f"{reported}"
            )
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


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


def _provisioned_copy_listing_no_criterion(tmp_path: Path) -> Path:
    """A copy of this checkout whose tail composes its writer's task with no criterion.

    Everything is this checkout's — its recipes, its scripts, its templates, its installed
    commands, provisioned from the locked installs as a session would provision it — but
    for the one list the tail answers the `plan-task` template's criteria with, emptied in
    the copy. That is the whole of what a composition that renders no criterion is, and
    the copy is the only place it can be made without editing the tree every other tier
    reads.
    """
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    copy_working_tree(checkout)
    # The review's records are keyed under a bar naming this host's own repository, read
    # off the checkout's `origin`; the copy answers this checkout's.
    answering_this_checkouts_origin(checkout)
    script = checkout / "scripts" / "finish-plan.sh"
    # llmlint: ignore-block[tests_mirror_real_usage, e2e_not_mocked] The writer's answers are
    # the recipe's own composition, fixed in its source, so no caller of `just finish-plan`
    # can supply a list with no criterion; the refusal guards that composition, and this
    # node's task requires a journey whose composed answers render none. The copy is the one
    # place the composition can change without editing the tree every other tier reads, and
    # everything the journey then drives — the recipe, the store, the engine's check — is real.
    composed = script.read_text(encoding="utf-8")
    emptied = re.sub(
        r'^    "acceptance_criteria": \[\n.*?^    \],\n',
        '    "acceptance_criteria": [],\n',
        composed,
        count=1,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert emptied != composed, (
        f"the copied {script.name} composes no `acceptance_criteria` list this journey can "
        "empty, so it would launch a writer it could not have refused"
    )
    script.write_text(emptied, encoding="utf-8")
    # llmlint: ignore-end[tests_mirror_real_usage, e2e_not_mocked]
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("UV_NO_SYNC", "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV")
    }
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
    return checkout


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]  # noqa: E501
# tests/plan_tooling/AGENTS.md states this project's split: `reads_docs` routes a journey that
# builds a copy of this checkout to `plan-tooling:test-docs`, keyed on the whole workspace because
# copying the tracked tree reads all of it, and never out of this project. `test_plan_flow_e2e.py`'s
# default-board flow copies and provisions a checkout behind the same marker for the same reason.
@pytest.mark.reads_docs
@pytest.mark.xdist_group("finish-plan")
def test_a_writer_task_that_lists_no_criterion_is_refused_before_anything_is_launched(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The rendering is validated once it is written, and a refusal launches nothing.

    Driven through the real `just finish-plan` of a copied checkout whose composed answers
    carry an empty criteria list: the review and the plan check pass as they would for any
    reviewed plan, the store renders the task, and the engine's check of that stored task
    refuses it — so the flow ends on its own status, says the task lists no acceptance
    criteria, takes back what it wrote, and neither launches a run nor copies anything.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    checkout = _provisioned_copy_listing_no_criterion(tmp_path)
    bench = _bench(tmp_path, oneharness_bin, PASSES)
    authoring = tmp_path / "authoring"
    authoring.mkdir()
    bench.environment[plan_root_variable.name()] = str(authoring)
    drafted = _draft("finish-plan-no-criterion")
    named = RunId("finish-plan-e2e-no-criterion")

    refused = subprocess.run(
        ["just", "finish-plan", str(_brief(tmp_path, drafted)), "--name", named]
        + ["--to", DESTINATION],
        cwd=checkout,
        env=bench.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )

    assert refused.returncode == WRITER_REFUSED, f"{refused.stdout}\n{refused.stderr}"
    assert "lists no acceptance criteria" in refused.stderr, refused.stderr
    assert "no design document was launched and nothing was copied" in refused.stderr, (
        refused.stderr
    )
    assert not (bench.runs / f"{named}{DESIGN_RUN_SUFFIX}").exists(), (
        f"a design run was launched for a task the engine refused:\n{_records(bench.runs)}"
    )
    assert _records(authoring) == [], (
        f"the refused task's project was left where the next run reads it: {_records(authoring)}"
    )
    assert _records(bench.destination) == [], _records(bench.destination)


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]  # noqa: E501


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `plan-tooling` is the leaf
# project keyed on `planToolingWorkspace`, the edge this rule asks for, and every other journey
# of this module already runs behind it. That key names the recipes, scripts, templates and
# package this journey drives, so narrowing it would memoize a verdict over a tree never run.
@pytest.mark.xdist_group("finish-plan")
def test_a_plan_with_no_visual_spike_has_its_writer_answer_no_visual_change(
    finished: Finished,
) -> None:
    """No report to quote, so the task quotes none, and holds the answer to `[]`."""
    content = finished.design_task["content"]
    assert "visual spike" not in content, content
    assert "--asset" not in content, content
    criteria = content.split("## Acceptance criteria", 1)[1].split("\n## ", 1)[0]
    assert "The document\u2019s `visual_changes` answer is `[]`." in criteria, criteria
    assert criteria.count("visual_changes") == 1, criteria


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
