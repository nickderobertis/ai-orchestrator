"""A plan written before human items carries no item metadata and no priority, and still runs.

This plan's own tasks bound for `plans` carry no store priority, because that source maps
none, and a `kind: human` node written before the adoption carries no
`onepipeline.human_item`. Such a plan is the engine's legacy case: it copies onto the `plans`
board as it always did, writing no priority, and once launched from that board copy with human
items on, the engine raises the step's one item from its title and action prose at Low — the
fallback for a node with no store priority — holding that step's work. A run that is stopped
dismisses it, "the run ended", so no item outlives the work it held.

Everything is real but GitHub's Projects API, the loopback double `tests/github_board.py`
serves, and the paid model `reviewed` scripts: the recipes, the installed plan store and
engine, and the engine's live item store under a runs root of the journey's own.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from github_board import BOARD, _serving_board
from project_fixtures import local_project, reviewed
from test_board_copy_design_approval_e2e import _copied, _on_the_board, _recipe

#: The legacy human step, as a plan written before the adoption states one.
GATE = "approve-the-rollout"
ACTION = "Approve the gradual rollout once the dashboards look healthy."

#: The item a node written after the adoption states, as the planner is told to write it.
STATED = {
    "kind": "question",
    "priority": "high",
    "title": "Roll the change out to everyone now?",
    "body": "The canary has run clean for a day; answer yes or say what to wait for.",
}

#: The session the journey launches and supervises as.
MANAGER = "legacy-manager"


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `board-copy-approval` is
# this directory's own leaf project, keyed on `boardCopyApprovalWorkspace`, the edge this rule
# asks for; that key names the recipes, scripts, configuration and package this journey
# drives, so narrowing it would memoize a verdict over a tree never run.
# llmlint: ignore-block[e2e_not_mocked] GitHub's Projects API is the one boundary doubled,
# because driving it writes to the live board this repository plans on, and the paid model
# `reviewed` scripts is the other; the recipes, the pinned engine and plan store, and the
# engine's item store are all real, and the plan's one node is a human step nothing dispatches.
@pytest.mark.parametrize(
    ("stated", "expected"),
    [
        (None, {"kind": "task", "priority": "low"}),
        (STATED, {key: STATED[key] for key in ("kind", "priority", "title", "body")}),
    ],
    ids=["legacy", "stated"],
)
def test_a_human_node_copies_without_a_store_priority_and_raises_the_item_it_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stated: dict[str, str] | None,
    expected: dict[str, str],
) -> None:
    """Copied to `plans` with no priority written; launched, its one item holds its step.

    A legacy node raises a task at Low from its own prose; a node that states its item, as
    the planner now writes every one, raises exactly what it states.
    """
    node: dict[str, object] = {"id": GATE, "kind": "human", "task": ACTION}
    if stated is not None:
        node["human_item"] = stated
    plan = {
        "schema_version": 3,
        "goal": {"text": "roll the change out gradually"},
        "tasks": [node],
    }
    project = reviewed(local_project(json.dumps(plan), "legacy-human"))
    host = tmp_path / "onepipeline-host.yaml"
    host.write_text("human_items:\n  enabled: true\n", encoding="utf-8")
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "CODEX_THREAD_ID")
    }
    environment |= {
        "ONEPIPELINE_CONFIG": str(host),
        "ONEPIPELINE_RUNS_DIR": str(tmp_path / "runs"),
        "ONEPIPELINE_LAUNCHER_SESSION": MANAGER,
    }
    with _serving_board() as remote:
        on_the_board = {**environment, **remote}
        copied = _recipe(on_the_board, "copy-plan", project, "--to", "plans")
        assert copied.returncode == 0, copied.stdout + copied.stderr
        home = _copied(copied.stdout)[project]
        _on_the_board(monkeypatch, remote)
        written = [issue.priority for issue in BOARD.issues_created_and_kept()]
        approved = _recipe(on_the_board, "approve-design", home)
        assert approved.returncode == 0, approved.stdout + approved.stderr
        launched = _recipe(
            on_the_board,
            *("orchestrate", home, "--dag-graph", "off"),
            *("--success-hook=", "--failure-hook="),
        )
        listed = _recipe(on_the_board, "human-items", "list", "--json")
        (item,) = [view["item"] for view in json.loads(listed.stdout)]
        run = str(item["owner"]["run"])
        stopped = _recipe(on_the_board, "stop", run)
        ended = _recipe(on_the_board, "human-items", "list", "--json", "--state", "dismissed")

    assert written and set(written) == {None}, written
    assert launched.returncode in (0, 3), launched.stdout + launched.stderr
    assert {key: item[key] for key in expected} == expected, item
    assert item["block"] == {"scope": "work", "run": run, "node": GATE}, item
    assert item["dedup_key"] == f"run:{run}#{GATE}", item
    if stated is None:
        assert ACTION in str(item["title"]) + str(item["body"]), item
    assert item["owner"]["session"] == MANAGER, item
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    (dismissed,) = [view["item"] for view in json.loads(ended.stdout)]
    assert dismissed["id"] == item["id"], dismissed
    assert dismissed["resolution"]["reason"] == "the run ended", dismissed
    # llmlint: ignore-end[e2e_not_mocked]
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
