"""Tests for the e2e subprocess and worktree leak guard."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from leak_guard import ResourceLeak, ResourceLeakGuard


def _git(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_guard_reports_and_reaps_a_deliberately_leaked_process(
    resource_leak_guard: ResourceLeakGuard,
) -> None:
    guard = ResourceLeakGuard(popen=resource_leak_guard.popen, grace_seconds=0.2)
    process = guard.spawn(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    with pytest.raises(ResourceLeak, match=rf"live process groups: \[{process.pid}\]"):
        guard.finish()

    assert process.poll() is not None


def test_guard_reports_and_removes_a_deliberately_leaked_worktree(
    tmp_path: Path, resource_leak_guard: ResourceLeakGuard
) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "linked"
    _git("init", "-b", "main", str(repo))
    (repo / "tracked").write_text("seed\n", encoding="utf-8")
    _git("add", "tracked", cwd=repo)
    _git("commit", "-m", "test: seed worktree guard", cwd=repo)
    _git("worktree", "add", "-b", "test/leak", str(worktree), cwd=repo)
    guard = ResourceLeakGuard(popen=resource_leak_guard.popen)
    guard.register_worktree(worktree)

    with pytest.raises(ResourceLeak, match="linked worktrees"):
        guard.finish()

    assert not worktree.exists()
    listed = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert str(worktree) not in listed.stdout


def test_guard_passes_when_registered_resources_are_already_clean(
    tmp_path: Path, resource_leak_guard: ResourceLeakGuard
) -> None:
    guard = ResourceLeakGuard(popen=resource_leak_guard.popen)
    completed = guard.spawn([sys.executable, "-c", "pass"])
    assert completed.wait(timeout=5) == 0
    guard.register_worktree(tmp_path / "never-created")

    guard.finish()
