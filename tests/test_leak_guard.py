"""Tests for the e2e subprocess and worktree leak guard."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import leak_reaper
import pytest
from leak_guard import ResourceLeak, ResourceLeakGuard, SessionGuard
from process_tree import await_reaped, await_recorded_pid, is_running, write_orphaning_tree


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


def test_guard_sweeps_a_descendant_its_popen_hook_never_saw(
    tmp_path: Path, resource_leak_guard: ResourceLeakGuard, session_leak_guard: SessionGuard
) -> None:
    """The layer that catches what registration cannot: a tree started elsewhere."""
    marker = tmp_path / "worker.pid"
    guard = ResourceLeakGuard.for_test(
        resource_leak_guard.popen, session_leak_guard, grace_seconds=0.5
    )
    # Started through the unpatched constructor and detaching twice over, so neither
    # the guard's registration nor a walk from a recorded root can reach the worker.
    resource_leak_guard.popen(
        [sys.executable, str(write_orphaning_tree(tmp_path)), str(marker), "--worker-detaches"]
    )
    worker = await_recorded_pid(marker)

    with pytest.raises(ResourceLeak, match=rf"live descendants: \[[^]]*{worker}"):
        guard.finish()

    assert await_reaped(worker)


#: A stable two-level tree: the root stays, so its child stays a descendant of it
#: rather than reparenting away. What is being proven here is the reaper's ownership
#: boundary, not its ability to follow an orphan — `write_orphaning_tree` covers that.
_OWNED_TREE = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
    "open(sys.argv[1], 'w').write(str(child.pid))\n"
    "time.sleep(600)\n"
)


def test_the_reaper_leaves_a_process_it_never_watched_alone(tmp_path: Path) -> None:
    """Ownership is the whole contract: only what it watched below its own root.

    Driven through the reaper's real loop, against a real pipe and real processes.
    The stranger is what another live session's tree looks like from the outside —
    running on the same host, and never below the root this reaper was given.
    """
    marker = tmp_path / "child.pid"
    watched = subprocess.Popen([sys.executable, "-c", _OWNED_TREE, str(marker)])
    stranger = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    child = await_recorded_pid(marker)
    read_fd, write_fd = os.pipe()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            reaping = pool.submit(leak_reaper.watch, watched.pid, stream=read_fd, poll=0.05)
            time.sleep(0.5)
            os.close(write_fd)
            reaped = reaping.result(timeout=30)

        assert reaped == (child,)
        assert await_reaped(child)
        assert is_running(stranger.pid)
    finally:
        os.close(read_fd)
        for process in (watched, stranger):
            process.kill()
            process.wait(timeout=10)
