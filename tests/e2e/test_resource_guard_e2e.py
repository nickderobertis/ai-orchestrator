"""Real-boundary coverage for the e2e resource leak guard."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from resources import ResourceGuard


def test_resource_guard_reports_and_cleans_process_and_worktree(
    tmp_path: Path, bare_origin
) -> None:
    origin = bare_origin()
    checkout = tmp_path / "checkout"
    worktree = tmp_path / "leaked-worktree"
    subprocess.run(["git", "clone", str(origin), str(checkout)], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "worktree", "add", "-b", "leak", str(worktree)],
        check=True,
    )
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    guard = ResourceGuard()
    guard.track_process(process, process_group=process.pid)
    guard.track_worktree(checkout, worktree)

    leaks = guard.leaks()
    assert any(f"process pid={process.pid}" in leak for leak in leaks)
    assert any(f"worktree path={worktree}" in leak for leak in leaks)
    with pytest.raises(AssertionError, match=r"(?s)process pid=.*worktree path="):
        guard.assert_clean()

    guard.cleanup()
    assert guard.leaks() == []
    assert not worktree.exists()


def test_resource_guard_passes_without_resources() -> None:
    guard = ResourceGuard()
    assert guard.leaks() == []
    guard.cleanup()
