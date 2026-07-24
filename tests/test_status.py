"""Focused tests for status joining and running detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator import gitops
from orchestrator.status import GitState, _git_state, _human, _ledger_for_branch, is_running, main


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
