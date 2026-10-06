"""A finalized plan's spike evidence still resolves once the plan is copied onto the board.

The finalize planner links each task to its spikes' reports, by the document ids they were
written under where the plan was drafted. The board mints every copied record an id of its
own, so on the board copy no document carries those ids — but the plan store stamps each copy
with `onetaskgraph.origin`, the record it was copied from, and that is how a report is found
there: the `spike-report` document of the plan's project whose origin names the id the task
links. So `just check-plan` passes the board copy, each task's report resolves inside the
board copy's own project, and the report and branch rules stand as they did where the plan
was drafted — the branch of a spike its engine retried among them.

Everything is real but GitHub's Projects API, which the loopback board `tests/github_board.py`
serves, and the paid model `reviewed` scripts: `just copy-plan` and `just check-plan`, the
installed plan store and engine, this host's templates, and `orchestrator/spike_plan.py`
read in this process against the store the recipes wrote.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

import pytest
from github_board import AUTHORING_ROOT_ENV, AUTHORING_SOURCE, _plan_environment, _serving_board
from project_fixtures import budgeted, no_budgets, reviewed
from test_board_copy_design_approval_e2e import _copied, _on_the_board, _recipe

from orchestrator import design_approval, plan_store, spike_plan
from orchestrator.plan_store import QualifiedProjectId
from orchestrator.root import REPO_ROOT

#: The finalized plan this journey copies, and the repository its tasks change.
PROJECT = "spiked-board"
QUALIFIED = f"{AUTHORING_SOURCE}:{PROJECT}"
REPOSITORY = "github.com/nickderobertis/ai-orchestrator"

CLOSING = (
    "Every claim the dispatch makes about the finished work is true of the tree as it "
    "finally stands."
)


class Linked(NamedTuple):
    """One task of the plan and the spike its `spikes` answer links."""

    node: str
    title: str
    spike: str
    branch: str


#: Two tasks, each building on a spike: the second's spike was retried by the engine, whose
#: retry cut a branch named for itself.
LINKED = (
    Linked("listing", "feat: page the listing", "spike-listing", f"nick/{PROJECT}/spike-listing"),
    Linked("quota", "feat: hold the quota", "spike-quota", f"nick/{PROJECT}/spike-quota-2"),
)


def _environment(root: Path) -> dict[str, str]:
    """The environment a planner renders, reviews and copies the plan of ``root`` in."""
    environment = _plan_environment(root)
    environment["PATH"] = f"{REPO_ROOT / '.venv' / 'bin'}{os.pathsep}{environment['PATH']}"
    environment["ONEPIPELINE_TEMPLATE_ROOT"] = str(REPO_ROOT / "templates")
    return environment


def _rendered(
    environment: Mapping[str, str], template: str, *store: str, answers: Mapping[str, object]
) -> None:
    """The engine's `template resolve` piped into one store verb, as a planner renders."""
    path = Path(environment[AUTHORING_ROOT_ENV]) / f"{template}.json"
    path.write_text(json.dumps(answers), encoding="utf-8")
    made = subprocess.run(
        ["bash", "-c", f'onepipeline template resolve {template} --json | onetaskgraph "$@"', "-"]
        + [*store, "--template-loader", "-", "--answers", str(path), "--no-interactive"],
        cwd=REPO_ROOT,
        env=dict(environment),
        text=True,
        capture_output=True,
        check=False,
    )
    assert made.returncode == 0, made.stdout + made.stderr


def _finalized(root: Path, environment: Mapping[str, str]) -> None:
    """The plan as its finalize planner leaves it: tasks linked to reports the spikes wrote."""
    (root / "projects").mkdir(parents=True)
    # llmlint: ignore[tests_mirror_real_usage] The plan store has no project-create verb, and `personas/planner.yaml` has a planner author a plan as one `projects/<project>.md` record before creating its tasks through the store, so this writes it as a planner does.  # noqa: E501
    (root / "projects" / f"{PROJECT}.md").write_text(
        f'---\ntitle: "{PROJECT}"\nstatus: "todo"\nmetadata:\n'
        '  "onepipeline.schema_version": 3\n'
        '  "onepipeline.goal": {"text": "Page the listing within its quota"}\n---\n\nPlan.\n',
        encoding="utf-8",
    )
    for one in LINKED:
        report = spike_plan.report_id(plan_store.NodeId(one.spike))
        _rendered(
            environment,
            "spike-report",
            "document",
            "create",
            AUTHORING_SOURCE,
            "--project",
            PROJECT,
            "--title",
            f"Spike report: {one.spike}",
            "--id",
            report,
            answers={
                "spike": one.spike,
                "branch": one.branch,
                "harness": "`harness.sh` at the branch's root; run `sh harness.sh`.",
                "method": "Twenty pages of the real listing at 2,000 nodes.",
                "candidates": [
                    {
                        "budget": "listing-latency",
                        "measure": "time to the first page",
                        "workload": "2,000 nodes",
                        "achievable": "420 ms",
                        "limits": "5,000 calls an hour",
                        "consumed": "20 calls",
                    }
                ],
                "findings": [],
            },
        )
        _rendered(
            environment,
            "plan-task",
            "task",
            "create",
            AUTHORING_SOURCE,
            "--project",
            PROJECT,
            "--title",
            one.title,
            f"--metadata=onepipeline.id={json.dumps(one.node)}",
            '--metadata=onepipeline.persona="engineer"',
            "--repository",
            REPOSITORY,
            answers={
                "what": f"Build {one.node} from what its spike measured.",
                "why": "An operator cannot see past the first screen.",
                "acceptance_criteria": [
                    "The route accepts a valid request and rejects an invalid one.",
                    "A request-level test drives the route end to end and covers both paths.",
                    CLOSING,
                ],
                "spikes": [{"spike": one.spike, "report": report, "branch": one.branch}],
            },
        )
    budgeted(AUTHORING_SOURCE, PROJECT, no_budgets([REPOSITORY]), dict(environment))
    reviewed(QUALIFIED)


# llmlint: ignore-block[e2e_not_mocked] GitHub's Projects API is the one boundary doubled, for the reason `tests/github_board.py` gives — driving it writes to the live board this repository plans on — and the paid model `reviewed` scripts is the other; the recipes, the pinned engine and plan store, and the records they write are all real.  # noqa: E501
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `board-copy-approval` is this directory's own leaf project, keyed on `boardCopyApprovalWorkspace`, which names the recipes, scripts and package this journey drives.  # noqa: E501
def test_a_finalized_plan_copied_onto_the_board_resolves_and_checks_each_tasks_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "authoring"
    root.mkdir()
    monkeypatch.setenv(AUTHORING_ROOT_ENV, str(root))
    environment = _environment(root)
    _finalized(root, environment)
    drafted = _recipe(environment, "check-plan", QUALIFIED)
    assert drafted.returncode == 0, drafted.stdout + drafted.stderr

    with _serving_board() as remote:
        on_the_board = {**environment, **remote}
        copied = _recipe(on_the_board, "copy-plan", QUALIFIED, "--to", "plans")
        assert copied.returncode == 0, copied.stdout + copied.stderr
        board = QualifiedProjectId(_copied(copied.stdout)[QUALIFIED])
        checked = _recipe(on_the_board, "check-plan", board)
        _on_the_board(monkeypatch, remote)
        tasks = plan_store.read_tasks(board)
        documents = plan_store.read_documents(board)

    assert checked.returncode == 0, (
        f"`just check-plan` has to pass the board copy {board}, whose reports the board gave "
        f"ids of its own:\n{checked.stdout}{checked.stderr}"
    )
    assert {task.node_id for task in tasks} == {one.node for one in LINKED}
    for task in tasks:
        expected = next(one for one in LINKED if one.node == task.node_id)
        (linked,) = spike_plan.evidence(task.content or "")
        assert (linked.spike, linked.branch) == (expected.spike, expected.branch), linked
        # The board gave the report an id of its own, so the task's id names no document
        # there; the store's record of what the copy came from is what finds it.
        assert all(
            str(held.qualified_id).partition(":")[2] != linked.report for held in documents
        ), [held.qualified_id for held in documents]
        report = spike_plan.report_for(linked, documents)
        assert report is not None, (
            f"{task.node_id}'s report {linked.report} resolves to no document of {board}: "
            f"{[(one.qualified_id, one.metadata.get(plan_store.ORIGIN_KEY)) for one in documents]}"
        )
        assert str(report.project) == board.partition(":")[2], report
        assert design_approval.is_spike_report(report), report.metadata
        assert report.metadata.get(plan_store.ORIGIN_KEY) == f"{AUTHORING_SOURCE}:{linked.report}"
        assert f"# Spike `{expected.spike}`" in report.content, report.content
        assert f"`{expected.branch}`" in report.content, report.content
        # The task tells its worker how to find it there.
        assert "the one whose `onetaskgraph.origin`" in (task.content or "")


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
# llmlint: ignore-end[e2e_not_mocked]
