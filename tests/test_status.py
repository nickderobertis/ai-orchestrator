"""Focused tests for status joining and running detection."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest

from orchestrator import gitops
from orchestrator.status import (
    GitState,
    _git_state,
    _human,
    _ledger_for_branch,
    _positional,
    is_running,
    main,
)


def test_running_requires_checked_out_worktree() -> None:
    assert is_running(GitState("feature", "origin/main", [], True))
    assert not is_running(GitState("feature", "origin/main", [], False))
    assert not is_running(None)


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
