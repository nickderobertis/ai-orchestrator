"""A plan that needs no measurement, planned, finished again, edited and re-reviewed.

One sequence on one bench (`planning_bench`), each step through the real recipe:

1. `just plan <brief> board` drafts a two-task plan with no spikes project. Its `spikes` step
   settles `done` at once — no spikes run is started and no person is asked to approve
   spikes — and its `finalize` guard settles `finalize` `done (not-needed)` with no planner
   or judge dispatched for it, so the run goes on to check, review, design and copy and waits
   at `approve-design` with the plan and its design document on the board copy. Its
   `approve-design` item is a question to the user quoting the plan's title, offering one
   Approve choice, and linking the design document through the `copy` step's published
   `design_url`, which the engine substitutes with where the board copy holds it.
2. `just finish-plan` over the same plan, whose draft is done, creates the `finishing`
   template's members alone and runs them, rewriting the design document. Each of the two
   planning projects loads under the engine's own `plan check`.
3. The board copy is approved while the finishing run's own driver still drives it, which
   the approval attests without adopting, and one reviewed task's criteria are edited on it. The
   orchestrate recipe refuses the plan naming that task; `just review-plan` spends exactly
   one judge-only call, for that task; the orchestrate recipe then launches the plan.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from typing import Any, NamedTuple

import pytest
from drafting import DESIGN_MARKER, FINALIZE_MARKER, brief, drafting, plan_title
from judging_codex import JudgeInput
from planning_bench import (
    BOARD,
    Bench,
    assert_reviewed_on_codex,
    bench,
    judge_inputs,
    judge_only_turns,
    just,
    run,
    runs_of,
    status,
    store_json,
)

pytestmark = [pytest.mark.usefixtures("module_tmpdir"), pytest.mark.xdist_group("planning")]

#: Every member the planning template declares, by node id.
PLANNING_NODES = [
    "refresh",
    "draft",
    "spikes",
    "finalize",
    "spike-evidence",
    "check",
    "review",
    "design",
    "copy",
    "approve-design",
]
#: Every member the finishing template declares.
FINISHING_NODES = [
    "refresh",
    "spikes",
    "spike-evidence",
    "check",
    "review",
    "design",
    "copy",
    "approve-design",
]

NEW_CRITERION = "A request-level test drives the route and reads its JSON body."
#: How the approval question's body links the design document before the engine substitutes it.
DESIGN_LINK = "[Read the design document](${steps.copy.design_url})"


class Approval(NamedTuple):
    """One planning project's rendered approval node, and the engine's load of the project."""

    project: str
    human_item: dict[str, Any]
    checked: subprocess.CompletedProcess[str]


class Sequence(NamedTuple):
    """Everything the sequence did, read back before the bench goes."""

    bench: Bench
    native: str
    planned: subprocess.CompletedProcess[str]
    planning_run: str
    planning: dict[str, object]
    journal: list[dict[str, object]]
    items: list[dict[str, Any]]
    finished: subprocess.CompletedProcess[str]
    finishing_run: str
    finishing: dict[str, object]
    finishing_members: list[str]
    design_reviews_before: int
    design_reviews_after: int
    refused_after_edit: subprocess.CompletedProcess[str]
    reviewed: subprocess.CompletedProcess[str]
    judged: list[dict[str, object]]
    reviewer_codex: list[JudgeInput]
    edited_task: str
    launched: subprocess.CompletedProcess[str]
    planning_approval: Approval
    finishing_approval: Approval
    driven_at_approval: object
    approved: subprocess.CompletedProcess[str]


def _listed(answer: dict[str, object]) -> list[dict[str, Any]]:
    """The records a plan-store list answered, refused by name when it answered no list."""
    items = answer.get("items")
    assert isinstance(items, list), answer
    return items


def _approval(bench: Bench, suffix: str) -> Approval:
    """The one authoring project ending ``suffix``: its `approve-design` item and its check."""
    projects = store_json(bench, "project", "list", "--source", "authoring")
    (project,) = [str(one["id"]) for one in _listed(projects) if suffix in str(one["id"])]
    members = store_json(bench, "task", "list", "--source", "authoring", "--project", project)
    (metadata,) = [
        one["item"]["metadata"]
        for one in _listed(members)
        if one["item"]["metadata"].get("onepipeline.id") == "approve-design"
    ]
    # As `config/onepipeline.workflows.yaml` launches it: tasks rendered from this host's
    # `templates/planning/` files rather than resolved by name.
    checked = run(
        bench,
        *("scripts/onepipeline.sh", "plan", "check", project),
        *("--require-rendered", "false", "--template-root", "templates"),
        seconds=300,
    )
    return Approval(project, metadata["onepipeline.human_item"], checked)


def _nodes(answer: dict[str, object]) -> dict[str, str]:
    nodes = answer.get("nodes")
    assert isinstance(nodes, list), answer
    return {str(node["id"]): str(node.get("status")) for node in nodes if isinstance(node, dict)}


def _only_run(bench: Bench, suffix: str) -> str:
    found = [one for one in runs_of(bench) if one.endswith(suffix)]
    assert len(found) == 1, f"expected one run ending {suffix!r}, found {runs_of(bench)}"
    return found[0]


def _items(bench: Bench, run_id: str) -> list[dict[str, Any]]:
    answered = run(
        bench,
        "scripts/onepipeline.sh",
        "human-items",
        "list",
        "--run",
        run_id,
        "--json",
        seconds=120,
    )
    assert answered.returncode == 0, answered.stdout + answered.stderr
    items = json.loads(answered.stdout)
    assert isinstance(items, list), items
    return items


def _design_renders(bench: Bench, native: str) -> int:
    """How many design writer turns the doubled model answered for this plan."""
    if not bench.turn_log.is_file():
        return 0
    turns = [json.loads(line) for line in bench.turn_log.read_text(encoding="utf-8").splitlines()]
    return sum(
        1
        for turn in turns
        if DESIGN_MARKER in str(turn.get("prompt")) and "judge" not in str(turn.get("config"))
    )


@pytest.fixture(scope="module")
def sequence(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Sequence]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp = tmp_path_factory.mktemp("planning-no-spikes")
    bench_ = bench(tmp, oneharness_bin, "e2e-planning-no-spikes")
    native = f"plan-ns-{os.getpid()}"
    drafting(bench_, native)

    planned = just(bench_, "plan", brief(bench_, native), BOARD)
    # The planning flow's own review step is project mode too, and the first a refused
    # reviewer breaks: named here rather than as the missing design document it leads to.
    assert_reviewed_on_codex(judge_inputs(bench_), planned)
    planning_run = _only_run(bench_, "-planning")
    planning = status(bench_, planning_run)
    journal = [
        json.loads(line)
        for line in (bench_.runs / planning_run / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    items = _items(bench_, planning_run)
    planning_approval = _approval(bench_, f"{native}-planning-")

    before = _design_renders(bench_, native)
    finished = just(bench_, "finish-plan", f"authoring:{native}", BOARD)
    finishing_run = _only_run(bench_, "-finishing")
    finishing = status(bench_, finishing_run)
    projects = store_json(bench_, "project", "list", "--source", "authoring")
    finishing_project = [
        str(one["id"]) for one in _listed(projects) if f"{native}-finishing" in str(one["id"])
    ]
    assert len(finishing_project) == 1, projects
    members = store_json(
        bench_, "task", "list", "--source", "authoring", "--project", finishing_project[0]
    )
    finishing_members = sorted(
        str(one["item"]["metadata"]["onepipeline.id"]) for one in _listed(members)
    )
    after = _design_renders(bench_, native)
    finishing_approval = _approval(bench_, f"{native}-finishing-")

    driven_at_approval = status(bench_, finishing_run)["driven"]
    approved = just(bench_, "approve-design", f"{BOARD}:{native}", finishing_run)
    tasks = store_json(bench_, "task", "list", "--source", BOARD, "--project", f"{BOARD}:{native}")
    edited_task = next(
        str(one["id"])
        for one in _listed(tasks)
        if one["item"]["metadata"].get("onepipeline.id") == "route"
    )
    rendered = run(
        bench_,
        "bash",
        "-c",
        "scripts/onepipeline.sh template resolve plan-task --json | "
        f".venv/bin/onetaskgraph task render {edited_task} --template-loader - "
        "--var 'what=Do route.' --var 'why=Operators need it.' "
        f"--var 'acceptance_criteria=[\"{NEW_CRITERION}\"]' --no-interactive",
    )
    assert rendered.returncode == 0, rendered.stdout + rendered.stderr
    refused = just(bench_, "orchestrate", f"{BOARD}:{native}", "--dag-graph", "off")
    judged_before = len(judge_only_turns(bench_))
    codex_before = len(judge_inputs(bench_))
    reviewed = just(bench_, "review-plan", f"{BOARD}:{native}")
    judged = judge_only_turns(bench_)[judged_before:]
    reviewer_codex = judge_inputs(bench_)[codex_before:]
    launched = just(bench_, "orchestrate", f"{BOARD}:{native}", "--dag-graph", "off")
    yield Sequence(
        bench_,
        native,
        planned,
        planning_run,
        planning,
        journal,
        items,
        finished,
        finishing_run,
        finishing,
        finishing_members,
        before,
        after,
        refused,
        reviewed,
        judged,
        reviewer_codex,
        edited_task,
        launched,
        planning_approval,
        finishing_approval,
        driven_at_approval,
        approved,
    )
    for one in runs_of(bench_):
        if not one.startswith("."):
            just(bench_, "stop", one, seconds=120)


def test_the_design_approval_attests_a_run_its_own_driver_is_still_continuing(
    sequence: Sequence,
) -> None:
    """The engine keeps driving a run past a human attestation and refuses adopting one it
    still drives, so the approval attests the run's `approve-design` node and adopts nothing."""
    approved = sequence.approved
    assert sequence.driven_at_approval is True, sequence.finishing
    assert approved.returncode == 0, approved.stdout + approved.stderr
    assert "adopt" not in (approved.stdout + approved.stderr).lower(), approved.stderr


def test_the_planning_run_holds_the_planning_templates_members(sequence: Sequence) -> None:
    assert sequence.planned.returncode == 0, sequence.planned.stdout + sequence.planned.stderr
    assert sorted(_nodes(sequence.planning)) == sorted(PLANNING_NODES), sequence.planning


def test_a_plan_with_no_spikes_runs_no_spikes_and_asks_nobody_about_them(
    sequence: Sequence,
) -> None:
    nodes = _nodes(sequence.planning)
    assert nodes["spikes"] == "done", nodes
    assert all(nodes[one] == "done" for one in PLANNING_NODES[:-1]), nodes
    assert nodes["approve-design"] == "waiting", nodes
    assert not [one for one in runs_of(sequence.bench) if one.endswith("-spikes")], runs_of(
        sequence.bench
    )
    titles = [str(entry["item"]["title"]) for entry in sequence.items]
    assert not [title for title in titles if "spike" in title.lower()], titles
    assert [title for title in titles if "design" in title.lower()], titles


def _node(answer: dict[str, object], node_id: str) -> dict[str, object]:
    nodes = answer.get("nodes")
    assert isinstance(nodes, list), answer
    (found,) = [node for node in nodes if isinstance(node, dict) and node.get("id") == node_id]
    return found


def _finalize_events(journal: list[dict[str, object]]) -> list[dict[str, object]]:
    """Every journal record labelled with the `finalize` node, the engine's and any graph's."""
    return [
        event
        for event in journal
        if isinstance(labels := event.get("labels"), dict)
        and "finalize" in (labels.get("node"), labels.get("onepipeline.node"))
    ]


def test_finalize_is_not_needed_and_settles_without_a_dispatch(sequence: Sequence) -> None:
    """The guard reads the plan's empty spikes project and settles finalize `not-needed`.

    Read three ways, so none of them stands alone: the run's status, the journal holding the
    guard's verdict and no dispatch of the node, and the doubled model's turn log holding no
    turn — planner or judge — for the finalize task.
    """
    finalize = _node(sequence.planning, "finalize")
    assert finalize.get("status") == "done", finalize
    assert finalize.get("outcome") == "not-needed", finalize
    assert "no spikes" in str(finalize.get("detail")), finalize

    events = _finalize_events(sequence.journal)
    kinds = [str(event.get("kind")) for event in events]
    (evaluated,) = [event for event in events if event.get("kind") == "guard-evaluated"]
    payload = evaluated.get("payload")
    assert isinstance(payload, dict), evaluated
    assert payload.get("verdict") == "not-needed", evaluated
    assert payload.get("exit_code") == 3, evaluated
    assert "node-dispatched" not in kinds, kinds
    assert all(event.get("source") == "pipeline" for event in events), kinds

    turns = [
        json.loads(line)
        for line in sequence.bench.turn_log.read_text(encoding="utf-8").splitlines()
    ]
    finalized = [turn for turn in turns if FINALIZE_MARKER in str(turn.get("prompt"))]
    assert not finalized, [str(turn.get("config")) for turn in finalized]


def test_the_board_copy_holds_the_plan_and_its_design_document(sequence: Sequence) -> None:
    documents = store_json(
        sequence.bench,
        "document",
        "list",
        "--source",
        BOARD,
        "--project",
        f"{BOARD}:{sequence.native}",
    )
    templates = [
        one["item"]["metadata"]["onetaskgraph.template"]["template"] for one in _listed(documents)
    ]
    assert templates == ["onepipeline:design-doc"], documents
    tasks = store_json(
        sequence.bench, "task", "list", "--source", BOARD, "--project", f"{BOARD}:{sequence.native}"
    )
    assert len(_listed(tasks)) == 2, tasks


def test_the_design_document_tabulates_each_planned_task_from_the_stores_graph(
    sequence: Sequence,
) -> None:
    """The Planned-tasks table renders the store's own graph: a row per task, its unit and
    what it depends on by title, linked to where that task lives, every row naming the task it
    is."""
    copy = f"{BOARD}:{sequence.native}"
    documents = store_json(sequence.bench, "document", "list", "--source", BOARD, "--project", copy)
    (document,) = _listed(documents)
    shown = store_json(sequence.bench, "document", "show", str(document["id"]))
    content = str(_listed(shown)[0]["item"]["content"])
    table = content.split("## Planned tasks", 1)[1]
    rows = [line for line in table.splitlines() if line.startswith("| ") and "---" not in line]
    cells = [[cell.strip() for cell in row.strip("|").split("|")] for row in rows[1:]]
    by_title = {row[0]: row for row in cells}
    assert set(by_title) == {"feat: add the health route", "docs: document the health route"}, rows
    assert by_title["feat: add the health route"][1:4] == ["API", "", "none"], rows
    # An in-plan dependency links its title to the location the store reports for that task,
    # which is the same location the dependency's own row says it lives at.
    first_lives = by_title["feat: add the health route"][4]
    assert by_title["docs: document the health route"][1:4] == [
        "Docs",
        "",
        f"[feat: add the health route]({first_lives})",
    ], rows
    assert all(row[4] for row in cells), rows


def test_finish_plan_runs_only_the_finishing_steps_and_rewrites_the_design(
    sequence: Sequence,
) -> None:
    assert sequence.finished.returncode == 0, sequence.finished.stdout + sequence.finished.stderr
    assert sequence.finishing_members == sorted(FINISHING_NODES)
    assert sorted(_nodes(sequence.finishing)) == sorted(FINISHING_NODES), sequence.finishing
    assert _nodes(sequence.finishing)["design"] == "done", sequence.finishing
    assert sequence.design_reviews_after == sequence.design_reviews_before + 1


def test_an_edited_task_is_refused_at_launch_naming_it(sequence: Sequence) -> None:
    refused = sequence.refused_after_edit
    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert f"{sequence.edited_task}: plan-review is void (content-moved)" in refused.stderr, (
        refused.stderr
    )
    assert "design-approval" not in refused.stderr, refused.stderr


def test_review_plan_judges_only_the_edited_task(sequence: Sequence) -> None:
    assert sequence.reviewed.returncode == 0, sequence.reviewed.stdout + sequence.reviewed.stderr
    assert len(sequence.judged) == 1, [str(turn.get("prompt"))[-400:] for turn in sequence.judged]
    assert NEW_CRITERION in str(sequence.judged[0]["prompt"])


def test_review_plan_runs_its_reviewer_on_codex_in_its_non_repository_scratch(
    sequence: Sequence,
) -> None:
    """Project mode: the reviewer's Codex turn runs where the review laid the plan out,
    which is no repository, and the Codex double answers it rather than refusing it."""
    assert_reviewed_on_codex(sequence.reviewer_codex, sequence.reviewed)


def test_the_re_reviewed_plan_launches(sequence: Sequence) -> None:
    launched = sequence.launched
    assert launched.returncode == 0, launched.stdout + launched.stderr
    settled = json.loads(launched.stdout.strip().splitlines()[-1])
    assert settled["settlement"] == "complete", settled


@pytest.mark.parametrize("which", ["planning", "finishing"])
def test_each_planning_project_loads_and_asks_the_user_to_approve_its_design(
    sequence: Sequence, which: str
) -> None:
    """The rendered `approve-design` item is a question to the user, linking the document.

    The engine's own `plan check` accepts the project — the placeholder names the node's
    one dependency, `copy`, and a question may offer choices — and the item names no copy
    id and no command for the manager to run.
    """
    approval = getattr(sequence, f"{which}_approval")
    checked = approval.checked
    assert checked.returncode == 0, checked.stdout + checked.stderr
    item = approval.human_item
    assert item["kind"] == "question", item
    assert item["priority"] == "medium", item
    (choice,) = item["choices"]
    assert (choice["id"], choice["title"]) == ("approve", "Approve"), item
    title = plan_title(sequence.native)
    assert item["title"] == f'Approve the design "{title}" or suggest changes', item
    body = str(item["body"])
    assert DESIGN_LINK in body, body
    assert title in body, body
    assert "just " not in body and "`" not in body, body
    assert f"{BOARD}:" not in body and "authoring:" not in body, body
    assert "onetaskgraph" not in body, body


def test_the_raised_question_links_where_the_board_copy_holds_the_design_document(
    sequence: Sequence,
) -> None:
    """The engine substitutes the copy step's `design_url` into the item it raises.

    The bench's board is a local Markdown source, whose copy records the document's path as
    its location, so the link the user is handed is that file.
    """
    documents = store_json(
        sequence.bench,
        "document",
        "list",
        "--source",
        "authoring",
        "--project",
        f"authoring:{sequence.native}",
    )
    (design,) = [
        one
        for one in _listed(documents)
        if one["item"]["metadata"]["onetaskgraph.template"]["template"] == "onepipeline:design-doc"
    ]
    location = design["item"]["metadata"]["onetaskgraph.copy_locations"][BOARD]["location"]
    asked = [
        entry["item"]
        for entry in sequence.items
        if str(entry["item"].get("title", "")).startswith("Approve the design")
    ]
    assert len(asked) == 1, sequence.items
    body = json.dumps(asked[0])
    assert f"[Read the design document]({location['path']})" in body, body
    assert "${steps." not in body, body
