"""Bounded cleanup for subprocess trees and linked git worktrees in tests.

Three layers, because a test's process tree escapes in three different ways.

*Registration* is the cheapest and covers what a test starts through ``Popen`` —
including the dispatch path, which reaches it through
``asyncio.create_subprocess_exec``. Each such child leads a session of its own, and
teardown signals that whole group. What a group cannot reach is a process that
left it: anything below the child that calls ``setsid`` for itself — a daemonizing
build tool, a round owner detaching from its launching turn — belongs to no group
but its own from then on, and killing the group it came from does not touch it.

*Descent* covers those, however they were started. The session declares itself a
**child subreaper**, so a process orphaned anywhere below it reparents to the
session rather than to init and stays findable; teardown then sweeps every live
descendant that was not there when the test began. Without the subreaper this
layer would be worthless — the processes that mattered had all reparented away,
which is exactly why walking from a recorded root never found them again.

*Outliving* covers the session's own death. Both layers above run inside the
session and are lost the moment it is killed rather than asked to stop, which is
how a killed pytest left twenty-two processes running out of deleted temp
directories. `leak_reaper` runs outside it and reaps what it watched the session
produce. See that module for what keeps it from touching anything else.

One thing is deliberately not cleaned up: a zombie. Adopting orphans means
inheriting exit statuses nobody collects, so ``ps`` shows ``<defunct>`` entries
under a long session. Reaping them blindly would mean calling ``waitpid`` on
children a live ``Popen`` is still waiting for, and CPython turns that stolen
status into ``returncode`` 0 — a failing subprocess silently reported as passing.
A zombie holds no working directory, no file, and no CPU; that trade is not worth
making, so `process_tree.is_running` treats it as gone instead.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import pytest

from orchestrator.watchdog import (
    ProcessId,
    process_activity,
    process_group_is_running,
    terminate_processes,
)

#: ``PR_SET_CHILD_SUBREAPER`` from ``linux/prctl.h``. Not exposed by the standard
#: library, and stable ABI since Linux 3.4, so the number is the interface.
_PR_SET_CHILD_SUBREAPER = 36

REAPER_SCRIPT = Path(__file__).with_name("leak_reaper.py")


class ResourceLeak(AssertionError):
    """A test left one of its own registered resources alive."""


def set_child_subreaper() -> bool:
    """Make this process inherit its orphaned descendants instead of init.

    Returns whether the kernel accepted it. A session without this still gets the
    other two layers; it simply cannot see a descendant whose parent already died,
    which is most of them.
    """
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        return bool(libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0)
    except (AttributeError, OSError):  # pragma: no cover - non-Linux libc
        return False


def live_descendants(root_pid: int) -> frozenset[ProcessId]:
    """Every running process below ``root_pid``, excluding the root itself."""
    return frozenset(process_activity(ProcessId(root_pid)).pids) - {ProcessId(root_pid)}


@dataclass
class SessionGuard:
    """The session-wide half of the guard: the subreaper role and the outside reaper."""

    root_pid: int
    subreaper: bool
    reaper: subprocess.Popen[bytes] | None

    def excluded(self) -> frozenset[ProcessId]:
        """Processes the per-test sweep must never claim as a test's leak."""
        return frozenset() if self.reaper is None else frozenset({ProcessId(self.reaper.pid)})

    def close(self) -> None:
        """Release the reaper's pipe so it reaps and exits with the session."""
        if self.reaper is None:
            return
        if self.reaper.stdin is not None:
            with suppress(OSError):
                self.reaper.stdin.close()
        with suppress(subprocess.TimeoutExpired):
            self.reaper.wait(timeout=10)
        if self.reaper.poll() is None:  # pragma: no cover - the reaper always exits on EOF
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(self.reaper.pid, signal.SIGKILL)
        self.reaper = None


_SESSION: SessionGuard | None = None


def install_session_guard() -> SessionGuard:
    """Claim orphaned descendants and start the reaper that outlives this session.

    Idempotent: a session that reaches this both as a plugin hook and as a fixture
    installs one guard, not two reapers racing each other over the same tree.
    """
    global _SESSION
    if _SESSION is None:
        subreaper = set_child_subreaper()
        reaper = subprocess.Popen(
            [sys.executable, str(REAPER_SCRIPT), str(os.getpid())],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            # A session of its own: a group kill aimed at this test session must not
            # reach the one process whose whole job is to survive it.
            start_new_session=True,
        )
        _SESSION = SessionGuard(os.getpid(), subreaper, reaper)
    return _SESSION


def remove_session_guard() -> None:
    """Tear the session-wide guard down, and let the next session install its own."""
    global _SESSION
    if _SESSION is not None:
        _SESSION.close()
        _SESSION = None


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
    #: The session whose descendants this guard may sweep, and the pid it may not.
    #: ``None`` disables the sweep, which is what a guard constructed inside a test
    #: to drive `finish` wants: it should account for what it was given, not for
    #: everything the surrounding session happens to be running.
    session: SessionGuard | None = None
    #: Descendants already running when the test started. They are somebody else's —
    #: a session fixture's, an earlier test's still-settling child — so a sweep that
    #: claimed them would report one test's leak against the next one to run.
    inherited: frozenset[ProcessId] = field(default_factory=frozenset)

    @classmethod
    def for_test(
        cls, popen: PopenFactory, session: SessionGuard, *, grace_seconds: float = 5.0
    ) -> ResourceLeakGuard:
        """Build a guard that also owns everything this test is about to start."""
        return cls(
            popen=popen,
            grace_seconds=grace_seconds,
            session=session,
            inherited=live_descendants(session.root_pid) | session.excluded(),
        )

    def _survivors(self) -> tuple[ProcessId, ...]:
        """Live descendants this test started, whatever they were started through.

        The registered groups are already gone by the time this runs, so what is
        left is what registration never covered: a subprocess started outside
        ``Popen``, and anything below it that left both its group and its ancestry.
        The subreaper is what makes them visible at all.
        """
        if self.session is None:
            return ()
        return tuple(sorted(live_descendants(self.session.root_pid) - self.inherited))

    def _settled_survivors(self) -> tuple[ProcessId, ...]:
        """The survivors that are still there once shutdown has had its chance.

        A tree that was told to stop takes a moment to go, and the last thing to
        leave is often a process whose whole job is to outlive the others. Reporting
        that instant as a leak would turn every ordinary teardown into a flake, so
        the sweep waits the same grace the registered groups get — and pays that wait
        only when there is something to wait for.
        """
        survivors = self._survivors()
        deadline = time.monotonic() + self.grace_seconds
        while survivors and time.monotonic() < deadline:
            time.sleep(0.1)
            survivors = self._survivors()
        return survivors

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
        # `killpg` succeeding is not proof of life: it counts the zombies this
        # session now inherits as a subreaper, and a group holding only those would
        # otherwise read as a leak that no amount of signalling could clear.
        return process_group_is_running(ProcessId(pid))

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
        # Swept after the registered groups are stopped, so a process this test
        # started through Popen is accounted for as the group it is, not twice — and
        # after the groups have had their chance to take their own children with them.
        survivors = self._settled_survivors()
        if survivors:
            terminate_processes(survivors)
        for path in linked_worktrees:
            try:
                self._remove_worktree(path)
            except (OSError, subprocess.SubprocessError) as error:
                cleanup_errors.append(f"could not remove worktree {path}: {error}")
        if live_groups or linked_worktrees or survivors or cleanup_errors:
            details = []
            if live_groups:
                details.append(f"live process groups: {live_groups}")
            if survivors:
                details.append(f"live descendants: {list(survivors)}")
            if linked_worktrees:
                details.append("linked worktrees: " + ", ".join(map(str, linked_worktrees)))
            details.extend(cleanup_errors)
            raise ResourceLeak("test resource leak detected; " + "; ".join(details))


@pytest.fixture(scope="session", autouse=True)
def session_leak_guard() -> Iterator[SessionGuard]:
    """Claim this session's orphans and post the reaper that outlives it.

    Session-scoped and autouse so it stands before the first test's fixtures: a
    descendant that appears before the subreaper role is claimed reparents to init
    and is beyond every later layer's reach.
    """
    guard = install_session_guard()
    yield guard
    remove_session_guard()


@pytest.fixture(autouse=True)
def resource_leak_guard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    request: pytest.FixtureRequest,
    session_leak_guard: SessionGuard,
) -> Iterator[ResourceLeakGuard]:
    """Reap complete subprocess trees and report test-owned resource leaks."""
    original_popen = subprocess.Popen
    e2e_test = "e2e" in Path(str(request.node.path)).parts
    guard = ResourceLeakGuard.for_test(original_popen, session_leak_guard)

    def tracked_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
        return guard.spawn(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", tracked_popen)
    yield guard
    if e2e_test:
        for git_file in tmp_path.rglob(".git"):
            if guard.is_linked_worktree(git_file.parent):
                guard.register_worktree(git_file.parent)
    guard.finish()
