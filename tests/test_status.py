"""Focused tests for status joining and running detection."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest

from orchestrator import gitops
from orchestrator.journal import open_journal
from orchestrator.runs import NodeId, RunId, StepId
from orchestrator.status import (
    GitState,
    _git_state,
    _human,
    _labelled_locator,
    _ledger_for_branch,
    _positional,
    _settled_nodes,
    is_running,
    main,
)


def test_running_requires_checked_out_worktree() -> None:
    assert is_running(GitState("feature", "origin/main", [], True))
    assert not is_running(GitState("feature", "origin/main", [], False))
    assert not is_running(None)


def test_a_settled_node_outranks_the_worktree_that_outlived_it() -> None:
    """The worktree is evidence; the journal is the record.

    A failed node can keep its checkout — a direct agent works in one it never had
    to remove — and calling that running is the stale picture a supervisor then acts
    on for twenty minutes before checking by hand.
    """
    checked_out = GitState("feature", "origin/main", [], True)
    assert not is_running(checked_out, "failed")
    assert not is_running(checked_out, "done")
    assert is_running(checked_out, None)


def test_only_a_node_level_settlement_settles_the_node(tmp_path: Path) -> None:
    """A step-scoped wait belongs to a node still working through its lifecycle."""
    run_dir = tmp_path / "run-1"
    journal = open_journal(run_dir, RunId("run-1"), 1)
    journal.append("node-started", node=NodeId("api"))
    journal.append("human-waiting", node=NodeId("api"), step=StepId("review"), detail={"ref": "x"})
    journal.append("node-failed", node=NodeId("web"), detail={"status": "failed"})
    journal.append("node-settled", node=NodeId("db"), detail={"status": "done"})
    # A status outside the domain a node can settle in is not passed through: the
    # kind still proves the node is no longer running, and that is all it reports.
    journal.append("node-settled", node=NodeId("odd"), detail={"status": "in-orbit"})
    # Detail is persisted JSON, so a status can arrive as a value that is not even a
    # string. This view degrades on it rather than raising about hashability.
    journal.append("node-settled", node=NodeId("junk"), detail={"status": ["in", "orbit"]})

    assert _settled_nodes(tmp_path, "run-1") == {
        ("1", "web"): "failed",
        ("1", "db"): "done",
        ("1", "odd"): "done",
        ("1", "junk"): "done",
    }
    # A run with no journal at all is simply a run this view knows nothing about.
    assert _settled_nodes(tmp_path, "never-ran") == {}
    # The run id arrives as a history label a subprocess wrote, and becomes a path
    # component, so it is validated rather than trusted.
    assert _settled_nodes(tmp_path, "../run-1") == {}


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        ({"round": "1", "node": "api"}, ("1", "api")),
        # Zero padding is the same round the journal recorded as the integer 1.
        ({"round": "01", "node": "api"}, ("1", "api")),
        ({"round": "0", "node": "api"}, None),
        ({"round": "-1", "node": "api"}, None),
        ({"round": "one", "node": "api"}, None),
        ({"round": "1", "node": ""}, None),
        # An orchestrator's own dispatch is run-scoped and names no node.
        ({"round": "1"}, None),
        ({}, None),
    ],
)
def test_a_dispatch_label_names_a_node_only_inside_the_domain_the_journal_records(
    labels: dict[str, str], expected: tuple[str, str] | None
) -> None:
    """History labels are values a subprocess wrote, so they are checked before they
    are used as a locator rather than trusted as they arrived."""
    assert _labelled_locator(labels) == expected


def test_no_running_tasks_message() -> None:
    assert _human([]) == "No running tasks. Pass N or --all to include recent finished tasks."


def test_git_state_falls_back_to_local_default_branch(tmp_path: Path) -> None:
    repo = tmp_path / "standalone"
    gitops._git(["init", "-b", "main", str(repo)])
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    gitops.add_all(repo)
    gitops.commit(repo, "init")
    state = _git_state(repo)
    assert state is not None
    assert state.base == "main"
    assert state.commits == []


def test_git_state_rejects_non_repository(tmp_path: Path) -> None:
    directory = tmp_path / "not-git"
    directory.mkdir()
    assert _git_state(directory) is None
    assert _git_state(tmp_path / "gone") is None


def test_ledger_ignores_pending_malformed_and_unmatched_rounds(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("not a run", encoding="utf-8")
    (tmp_path / "pending" / "round-01").mkdir(parents=True)
    malformed = tmp_path / "malformed" / "round-01"
    malformed.mkdir(parents=True)
    (malformed / "result.json").write_text("not: [valid", encoding="utf-8")
    unmatched = tmp_path / "unmatched" / "round-01"
    unmatched.mkdir(parents=True)
    (unmatched / "result.json").write_text(
        '{"ok":true,"started_order":[],"results":{}}', encoding="utf-8"
    )
    assert _ledger_for_branch(tmp_path, "feature") is None
    assert _ledger_for_branch(tmp_path, None) is None


def test_status_cli_rejects_limit_and_reports_missing_history_tool(monkeypatch, capsys) -> None:
    with pytest.raises(SystemExit, match="2"):
        main(["0"])
    monkeypatch.setenv("PATH", "")
    assert main([]) == 2
    assert "oneharness not found" in capsys.readouterr().err


def test_positional_splits_a_count_from_a_run_id() -> None:
    """The count this argument started as keeps its meaning; anything else is a run.

    Resolved toward the older reading because a run id may itself be all digits, so
    `just status 5` must not silently change what a documented invocation shows.
    """
    assert _positional(None) == (None, None)
    assert _positional("5") == (5, None)
    assert _positional("harness-followups") == (None, "harness-followups")


def test_status_scopes_its_view_to_one_named_run(tmp_path: Path, monkeypatch, capsys) -> None:
    """Naming a run selects its sessions and its indicators, and nothing else.

    The real journey is `tests/e2e/test_run_views_by_id_e2e.py`, which points the
    real `just status` at a live launch's advertised id. This direct call exists so
    the in-process resolution and rendering count toward the coverage gate, which a
    subprocess CLI invocation cannot contribute.
    """
    runs_dir = tmp_path / "runs"
    for name in ("mine", "theirs"):
        round_dir = runs_dir / name / "round-01"
        round_dir.mkdir(parents=True)
        (round_dir / "plan.json").write_text("{}\n", encoding="utf-8")
        (round_dir / "status.json").write_text(
            json.dumps(
                {"status": "running", "pid": os.getpid() + 10_000_000, "host": socket.gethostname()}
            ),
            encoding="utf-8",
        )

    history_dir = tmp_path / "history"
    history_dir.mkdir()
    monkeypatch.setenv("ONEHARNESS_HISTORY_DIR", str(history_dir))
    assert main(["mine", "--runs-dir", str(runs_dir)]) == 0
    shown = capsys.readouterr().out
    assert "mine: round-01 ABANDONED" in shown
    assert "theirs" not in shown
    assert "No dispatched tasks recorded for run mine." in shown

    assert main(["no-such-run", "--runs-dir", str(runs_dir)]) == 2
    assert "no recorded run 'no-such-run'" in capsys.readouterr().err
