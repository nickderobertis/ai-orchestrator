"""A plan that needs no measurement, planned, finished again, edited and re-reviewed.

One sequence on one bench (`planning_bench`), each step through the real recipe:

1. `just plan <brief> board` drafts a two-task plan with no spikes project. Its `spikes` step
   settles `done` at once — no spikes run is started and no person is asked to approve
   spikes — and the run waits at `approve-design` with the plan and its design document on
   the board copy.
2. `just finish-plan` over the same plan, whose draft is done, creates the `finishing`
   template's members alone and runs them, rewriting the design document.
3. The board copy is approved and one reviewed task's criteria are edited on it. The
   orchestrate recipe refuses the plan naming that task; `just review-plan` spends exactly
   one judge-only call, for that task; the orchestrate recipe then launches the plan.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from typing import NamedTuple

import pytest
from drafting import DESIGN_MARKER, brief, drafting
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
    "spikes",
    "spike-evidence",
    "check",
    "review",
    "design",
    "copy",
    "approve-design",
]

NEW_CRITERION = "A request-level test drives the route and reads its JSON body."


class Sequence(NamedTuple):
    """Everything the sequence did, read back before the bench goes."""

    bench: Bench
    native: str
    planned: subprocess.CompletedProcess[str]
    planning_run: str
    planning: dict[str, object]
    items: list[dict[str, object]]
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


def _nodes(answer: dict[str, object]) -> dict[str, str]:
    nodes = answer.get("nodes")
    assert isinstance(nodes, list), answer
    return {str(node["id"]): str(node.get("status")) for node in nodes if isinstance(node, dict)}


def _only_run(bench: Bench, suffix: str) -> str:
    found = [one for one in runs_of(bench) if one.endswith(suffix)]
    assert len(found) == 1, f"expected one run ending {suffix!r}, found {runs_of(bench)}"
    return found[0]


def _items(bench: Bench, run_id: str) -> list[dict[str, object]]:
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
    items = _items(bench_, planning_run)

    before = _design_renders(bench_, native)
    finished = just(bench_, "finish-plan", f"authoring:{native}", BOARD)
    finishing_run = _only_run(bench_, "-finishing")
    finishing = status(bench_, finishing_run)
    projects = store_json(bench_, "project", "list", "--source", "authoring")
    finishing_project = [
        str(one["id"])
        for one in projects.get("items", [])
        if f"{native}-finishing" in str(one["id"])
    ]
    assert len(finishing_project) == 1, projects
    members = store_json(
        bench_, "task", "list", "--source", "authoring", "--project", finishing_project[0]
    )
    finishing_members = sorted(
        str(one["item"]["metadata"]["onepipeline.id"]) for one in members["items"]
    )
    after = _design_renders(bench_, native)

    approved = just(bench_, "approve-design", f"{BOARD}:{native}", finishing_run)
    assert approved.returncode == 0, approved.stdout + approved.stderr
    tasks = store_json(bench_, "task", "list", "--source", BOARD, "--project", f"{BOARD}:{native}")
    edited_task = next(
        str(one["id"])
        for one in tasks["items"]
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
    )
    for one in runs_of(bench_):
        if not one.startswith("."):
            just(bench_, "stop", one, seconds=120)


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
        one["item"]["metadata"]["onetaskgraph.template"]["template"] for one in documents["items"]
    ]
    assert templates == ["onepipeline:design-doc"], documents
    tasks = store_json(
        sequence.bench, "task", "list", "--source", BOARD, "--project", f"{BOARD}:{sequence.native}"
    )
    assert len(tasks["items"]) == 2, tasks


def test_the_design_document_tabulates_each_planned_task_from_the_stores_graph(
    sequence: Sequence,
) -> None:
    """The Planned-tasks table renders the store's own graph: a row per task, its unit and
    what it depends on by title, every row naming the task it is."""
    copy = f"{BOARD}:{sequence.native}"
    documents = store_json(sequence.bench, "document", "list", "--source", BOARD, "--project", copy)
    (document,) = documents["items"]
    shown = store_json(sequence.bench, "document", "show", str(document["id"]))
    content = str(shown["items"][0]["item"]["content"])
    table = content.split("## Planned tasks", 1)[1]
    rows = [line for line in table.splitlines() if line.startswith("| ") and "---" not in line]
    cells = [[cell.strip() for cell in row.strip("|").split("|")] for row in rows[1:]]
    by_title = {row[0]: row for row in cells}
    assert set(by_title) == {"feat: add the health route", "docs: document the health route"}, rows
    assert by_title["feat: add the health route"][1:4] == ["API", "", "none"], rows
    assert by_title["docs: document the health route"][1:4] == [
        "Docs",
        "",
        "feat: add the health route",
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
