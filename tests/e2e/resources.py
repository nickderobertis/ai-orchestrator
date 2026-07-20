"""Deterministic cleanup for subprocess and git-worktree e2e resources."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TrackedProcess:
    process: subprocess.Popen[Any]
    process_group: int | None


@dataclass(frozen=True)
class TrackedWorktree:
    checkout: Path
    path: Path


def _group_alive(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_process_tree(tracked: TrackedProcess, *, grace: float = 2.0) -> None:
    """TERM and then KILL a process and every descendant in its isolated group."""
    process = tracked.process
    group = tracked.process_group
    if group is None:
        if process.poll() is None:
            process.terminate()
    else:
        with suppress(ProcessLookupError):
            os.killpg(group, signal.SIGTERM)

    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        process.poll()
        if (group is None and process.returncode is not None) or (
            group is not None and not _group_alive(group)
        ):
            break
        time.sleep(0.01)
    else:
        if group is None:
            if process.poll() is None:
                process.kill()
        else:
            with suppress(ProcessLookupError):
                os.killpg(group, signal.SIGKILL)

    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=grace)


class ResourceGuard:
    """Track only resources created by one test and clean them at its boundary."""

    def __init__(self) -> None:
        self.processes: list[TrackedProcess] = []
        self.worktrees: list[TrackedWorktree] = []

    def track_process(self, process: subprocess.Popen[Any], *, process_group: int | None) -> None:
        self.processes.append(TrackedProcess(process, process_group))

    def track_worktree(self, checkout: Path, path: Path) -> None:
        self.worktrees.append(TrackedWorktree(checkout.resolve(), path.resolve()))

    def leaks(self) -> list[str]:
        leaks = []
        for tracked in self.processes:
            alive = (
                _group_alive(tracked.process_group)
                if tracked.process_group is not None
                else tracked.process.poll() is None
            )
            if alive:
                leaks.append(
                    f"process pid={tracked.process.pid} group={tracked.process_group} "
                    f"args={tracked.process.args!r}"
                )
        for tracked in self.worktrees:
            if tracked.path.exists():
                leaks.append(f"worktree path={tracked.path} checkout={tracked.checkout}")
        return leaks

    def cleanup(self) -> None:
        for tracked in reversed(self.processes):
            terminate_process_tree(tracked)
        for tracked in reversed(self.worktrees):
            if not tracked.path.exists():
                continue
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(tracked.checkout),
                    "worktree",
                    "remove",
                    "--force",
                    str(tracked.path),
                ],
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
        self.assert_clean()

    def assert_clean(self) -> None:
        """Fail with the exact test-owned resources that remain alive."""
        leaks = self.leaks()
        if leaks:
            raise AssertionError("e2e resource cleanup failed:\n" + "\n".join(leaks))
