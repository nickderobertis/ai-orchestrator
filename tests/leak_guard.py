"""Bounded cleanup for subprocess trees and linked git worktrees in tests."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


class ResourceLeak(AssertionError):
    """A test left one of its own registered resources alive."""


class PopenFactory(Protocol):
    """Callable subprocess-construction boundary used by the guard."""

    def __call__(self, *args: Any, **kwargs: Any) -> subprocess.Popen[Any]: ...


@dataclass
class ResourceLeakGuard:
    """Track test-owned process groups and worktrees, then clean them boundedly."""

    popen: PopenFactory = subprocess.Popen
    grace_seconds: float = 5.0
    processes: list[subprocess.Popen[Any]] = field(default_factory=list)
    worktrees: set[Path] = field(default_factory=set)

    def spawn(self, *args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
        """Start and register a subprocess as leader of a fresh process group."""
        if kwargs.get("start_new_session") is False or kwargs.get("process_group", -1) not in (
            -1,
            None,
        ):
            raise ValueError("guarded subprocesses must use a fresh session")
        kwargs["start_new_session"] = True
        process = self.popen(*args, **kwargs)
        process.terminate = (  # type: ignore[method-assign]  # Make legacy teardown group-safe.
            lambda: self._signal_group(process.pid, signal.SIGTERM)
        )
        process.kill = (  # type: ignore[method-assign]  # Make legacy teardown group-safe.
            lambda: self._signal_group(process.pid, signal.SIGKILL)
        )
        self.processes.append(process)
        return process

    @staticmethod
    def _signal_group(pid: int, sig: signal.Signals) -> None:
        """Signal a test-owned process group, tolerating an already-clean exit."""
        with suppress(ProcessLookupError):
            os.killpg(pid, sig)

    def register_worktree(self, path: str | Path) -> None:
        """Register an exact linked-worktree path created for this test."""
        self.worktrees.add(Path(path).resolve())

    @staticmethod
    def is_linked_worktree(path: Path) -> bool:
        """Distinguish a linked-worktree pointer from unrelated ``.git`` files."""
        git_file = path / ".git"
        if not git_file.is_file():
            return False
        try:
            pointer = git_file.read_text(encoding="utf-8").strip()
        except OSError:
            return False
        return pointer.startswith("gitdir: ") and "/worktrees/" in pointer

    @staticmethod
    def _group_alive(pid: int) -> bool:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _wait_group(self, process: subprocess.Popen[Any], deadline: float) -> bool:
        pid = process.pid
        while self._group_alive(pid) and time.monotonic() < deadline:
            process.poll()
            time.sleep(0.02)
        process.poll()
        return not self._group_alive(pid)

    def _stop_group(self, process: subprocess.Popen[Any]) -> None:
        pid = process.pid
        if not self._group_alive(pid):
            process.poll()
            return
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + self.grace_seconds
        if not self._wait_group(process, deadline):
            with suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGKILL)
            if not self._wait_group(process, time.monotonic() + self.grace_seconds):
                raise ResourceLeak(f"process group {pid} survived SIGTERM and SIGKILL")
        try:
            process.wait(timeout=self.grace_seconds)
        except subprocess.TimeoutExpired as error:
            raise ResourceLeak(f"process {pid} was not reaped after its group exited") from error

    def _remove_worktree(self, path: Path) -> None:
        if not path.exists() or not self.is_linked_worktree(path):
            return
        probe = self.popen(
            ["git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        stdout, _ = probe.communicate(timeout=self.grace_seconds)
        if probe.returncode == 0:
            common = stdout.strip()
            remove = self.popen(
                ["git", f"--git-dir={common}", "worktree", "remove", "--force", str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            remove.communicate(timeout=self.grace_seconds)
        if path.exists():
            shutil.rmtree(path)

    def finish(self) -> None:
        """Clean registered resources and report everything found leaked."""
        settle_deadline = time.monotonic() + min(1.0, self.grace_seconds)
        while time.monotonic() < settle_deadline:
            for process in self.processes:
                process.poll()
            if not any(self._group_alive(process.pid) for process in self.processes):
                break
            time.sleep(0.02)
        live_groups = [process.pid for process in self.processes if self._group_alive(process.pid)]
        linked_worktrees = [path for path in self.worktrees if self.is_linked_worktree(path)]
        cleanup_errors: list[str] = []
        for process in reversed(self.processes):
            try:
                self._stop_group(process)
            except ResourceLeak as error:
                cleanup_errors.append(str(error))
        for path in linked_worktrees:
            try:
                self._remove_worktree(path)
            except (OSError, subprocess.SubprocessError) as error:
                cleanup_errors.append(f"could not remove worktree {path}: {error}")
        if live_groups or linked_worktrees or cleanup_errors:
            details = []
            if live_groups:
                details.append(f"live process groups: {live_groups}")
            if linked_worktrees:
                details.append("linked worktrees: " + ", ".join(map(str, linked_worktrees)))
            details.extend(cleanup_errors)
            raise ResourceLeak("test resource leak detected; " + "; ".join(details))
