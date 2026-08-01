"""Bounded cleanup for subprocess trees and linked git worktrees in tests.

Layered, because a process tree escapes a test in more ways than one: what the test
starts through ``Popen`` (`ResourceLeakGuard`, which leads each child in a process
group of its own and signals that whole group), what leaves that group by calling
``setsid`` for itself (`SessionGuard`, which samples the session's own tree and
remembers what it saw), what a *launch* starts and never waits for — which parentage
loses within milliseconds and no interval can sample — and the session's death, which
runs no teardown at all. The last two are `leak_reaper`'s, watching from outside the
session: it claims by parentage while the session runs and by this session's inherited
environment token once it is over.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Protocol

import pytest
from leak_reaper import POLL_SECONDS, SESSION_TOKEN_ENV, TreeSampler

from orchestrator.watchdog import ProcessId, process_group_is_running, terminate_processes

REAPER_SCRIPT = Path(__file__).with_name("leak_reaper.py")


class ResourceLeak(AssertionError):
    """A test left one of its own registered resources alive."""


@dataclass
class ReaperHandle:
    """The detached reaper: the pid to leave alone, and the pipe that ends it.

    Not a ``Popen``, because the reaper is deliberately not this session's child —
    it double-forks so init adopts it, which is what keeps a walk of this session's
    tree from collecting it. What is left is a pid nothing can ``wait`` for and the
    write end of the pipe whose closing tells it the session is over.
    """

    pid: ProcessId
    pipe: IO[bytes]

    def close(self, *, timeout: float = 10.0) -> None:
        """Release the pipe and wait for the reap, then insist if it does not come."""
        with suppress(OSError):
            self.pipe.close()
        deadline = time.monotonic() + timeout
        while _is_running(self.pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        if _is_running(self.pid):  # pragma: no cover - the reaper always exits on EOF
            with suppress(ProcessLookupError, PermissionError):
                os.kill(self.pid, signal.SIGKILL)


def _is_running(pid: ProcessId) -> bool:
    """Whether ``pid`` is executing, counting a zombie as gone."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    return raw[raw.rfind(")") + 2 :].split(" ", 1)[0] != "Z"


@dataclass
class SessionGuard:
    """The session-wide half of the guard: the watcher inside, the reaper outside.

    Sampling is as wide as its interval, and what escapes inside one is invisible to
    it forever: a process orphaned and reparented away was never below this session
    when anything looked. Making the session a child subreaper would narrow that, at
    the price of inheriting exit statuses nobody collects — so every check asking
    whether a process is gone would read a zombie as alive. The environment token
    below closes it instead, from outside and without a subreaper.
    """

    root_pid: int
    sampler: TreeSampler
    reaper: ReaperHandle | None
    #: This session's environment stamp, which the reaper scans for once the session
    #: is over. It is what covers a launch: `dispatch.launch_orchestrator` starts its
    #: process detached and returns without waiting, so everything that process goes
    #: on to start never existed below this session and no interval could sample it.
    token: str = ""
    _stopping: threading.Event = field(default_factory=threading.Event)
    _watcher: threading.Thread | None = None

    def start(self) -> None:
        """Begin sampling this session's tree until the session ends.

        The watcher is created with every blockable signal masked, and inherits that
        mask. A signal mask is per-thread, so tests that mask a signal on the main
        thread and then raise it at this process — which is how the round-ownership
        journeys drive their own teardown handlers — were relying on there being only
        one thread to deliver it to. An unmasked helper thread quietly becomes that
        delivery target and dies of the default disposition, taking the session with
        it. Nothing here should ever receive a signal, so it accepts none.
        """
        if self._watcher is not None:
            return
        self._stopping.clear()
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, signal.valid_signals())
        try:
            self._watcher = threading.Thread(target=self._watch, name="leak-guard", daemon=True)
            self._watcher.start()
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)

    def _watch(self) -> None:
        # The event says *stop*, so waiting on it is both the interval between samples
        # and the shutdown signal: it returns early only once close() sets it. Waiting
        # on a "still watching" event instead would return immediately every time and
        # walk /proc in a spin loop for the whole session.
        while True:
            self.sampler.sample()
            if self._stopping.wait(POLL_SECONDS):
                return

    def excluded(self) -> frozenset[ProcessId]:
        """Processes the per-test sweep must never claim as a test's leak."""
        return frozenset() if self.reaper is None else frozenset({self.reaper.pid})

    def close(self) -> None:
        """Stop watching, then release the reaper's pipe so it reaps and exits."""
        self._stopping.set()
        if self._watcher is not None:
            self._watcher.join(timeout=10)
            self._watcher = None
        if self.reaper is None:
            return
        self.reaper.close()
        self.reaper = None


_SESSION: SessionGuard | None = None


def install_session_guard() -> SessionGuard:
    """Start watching this session's tree, and post the reaper that outlives it.

    The token is exported into this session's own environment *before* anything is
    started, so every process the session goes on to start inherits it — including
    the ones a launch detaches and never waits for, which parentage loses within
    milliseconds. It is unique per session, so carrying it is proof of descent from
    this one and from nothing else. A nested session (this suite runs real ones) is
    not additive: one variable holds one token, so the inherited value is replaced
    and everything the nested session execs afterwards carries the inner token alone.
    That matches how the layers divide the work — the nested session posts a reaper
    of its own and is what accounts for its own tree — and the outer session keeps
    both of its claims on it regardless: the nested session process itself carries
    the outer token, fixed in its `/proc` environment at `exec` and unaffected by
    what the interpreter later assigns, and what runs below it is sampled by
    parentage for as long as it is there to sample.

    Idempotent: a session that reaches this both as a plugin hook and as a fixture
    installs one guard, not two reapers racing each other over the same tree.
    """
    global _SESSION
    if _SESSION is None:
        token = f"{os.getpid()}-{uuid.uuid4().hex}"
        os.environ[SESSION_TOKEN_ENV] = token
        _SESSION = SessionGuard(os.getpid(), TreeSampler(os.getpid()), _post_reaper(token), token)
        _SESSION.start()
    return _SESSION


def _post_reaper(token: str) -> ReaperHandle:
    """Start the reaper and wait for it to report the pid init has adopted.

    The intermediate is this session's child and exits at once; the reaper itself is
    a grandchild nothing here can wait for, and that is the point — see
    `leak_reaper._detach_and_watch`. Its pid comes back over stdout because there is
    no other way to learn it, and it is needed twice: to keep the per-test sweep off
    it, and to know when the reap is finished at the end of the session.
    """
    intermediate = subprocess.Popen(
        [sys.executable, str(REAPER_SCRIPT), str(os.getpid()), token],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        # A session of its own: a group kill aimed at this test session must not
        # reach the one process whose whole job is to survive it.
        start_new_session=True,
    )
    assert intermediate.stdin is not None and intermediate.stdout is not None
    reported = intermediate.stdout.readline()
    intermediate.stdout.close()
    intermediate.wait(timeout=30)
    if not reported.strip().isdigit():
        # Loud, because a session that quietly ran without one is exactly the state
        # this guard exists to make impossible.
        intermediate.stdin.close()
        raise ResourceLeak(f"the leak reaper did not report a pid; it said {reported!r}")
    return ReaperHandle(ProcessId(int(reported)), intermediate.stdin)


def remove_session_guard() -> None:
    """Tear the session-wide guard down, and let the next session install its own."""
    global _SESSION
    if _SESSION is not None:
        _SESSION.close()
        _SESSION = None
        # Withdrawn with the guard: a token still exported after its reaper has gone
        # would be inherited by processes nothing is left watching for.
        os.environ.pop(SESSION_TOKEN_ENV, None)


#: Helpers a Python session starts for itself and ends with. They appear mid-run,
#: the first time a test uses `multiprocessing`, and then live for the rest of the
#: session by design — each watches a pipe and exits when the interpreter does,
#: which is precisely not the thing this guard is looking for. Only CPython spawns
#: these, so naming them is exact rather than a heuristic.
_SESSION_HELPERS = ("multiprocessing.resource_tracker", "multiprocessing.forkserver")


def _command_line(pid: ProcessId) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return " ".join(raw.decode("utf-8", errors="replace").split("\0")).strip()


def _is_session_helper(pid: ProcessId) -> bool:
    command = _command_line(pid)
    return any(helper in command for helper in _SESSION_HELPERS)


def _describe(pid: ProcessId) -> str:
    """Name a leaked process, not just its number.

    A pid alone tells whoever reads the failure nothing about what leaked, and by
    the time they look the process is gone. The command line is the one thing that
    makes the report actionable.
    """
    command = _command_line(pid)
    return f"{pid} ({command[:400]})" if command else f"{pid} (<gone>)"


class PopenFactory(Protocol):
    """Callable subprocess-construction boundary used by the guard.

    Typed as loosely as `subprocess.Popen` itself is called: this stands in for the
    real constructor at a seam every caller reaches with its own argument shape, so
    narrowing it here would only be narrower than the thing it replaces.
    """

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
            inherited=session.sampler.claimed() | session.excluded(),
        )

    def _survivors(self) -> tuple[ProcessId, ...]:
        """Live descendants this test started, whatever they were started through.

        The registered groups are already gone by the time this runs, so what is
        left is what registration never covered: a subprocess started outside
        ``Popen``, and anything below it that left both its group and its ancestry.
        The session's continuous sampling is what makes those visible at all — by
        teardown they are nobody's descendants any more.
        """
        if self.session is None:
            return ()
        self.session.sampler.sample()
        return tuple(
            pid
            for pid in self.session.sampler.survivors(ignoring=self.inherited)
            if not _is_session_helper(pid)
        )

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
        # `killpg` succeeding is not proof of life: it counts a zombie whose parent
        # has not collected it yet, and a group holding only those would otherwise
        # read as a leak that no amount of signalling could clear.
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
        # Described before they are killed: afterwards there is nothing left to name,
        # and a report that says only "pid 2766469" tells the reader nothing.
        described = [_describe(pid) for pid in survivors]
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
                details.append("live descendants: " + ", ".join(described))
            if linked_worktrees:
                details.append("linked worktrees: " + ", ".join(map(str, linked_worktrees)))
            details.extend(cleanup_errors)
            raise ResourceLeak("test resource leak detected; " + "; ".join(details))


@pytest.fixture(scope="session", autouse=True)
def session_leak_guard() -> Iterator[SessionGuard]:
    """Claim this session's orphans and post the reaper that outlives it.

    Session-scoped and autouse so it stands before the first test's fixtures: a
    process that comes and goes before the watching starts was never sampled, and
    nothing later can reconstruct where it went.
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
        # Replaces `subprocess.Popen` for every caller in the process, so it has to
        # accept exactly what that constructor accepts and nothing narrower.
        return guard.spawn(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", tracked_popen)
    yield guard
    if e2e_test:
        for git_file in tmp_path.rglob(".git"):
            if guard.is_linked_worktree(git_file.parent):
                guard.register_worktree(git_file.parent)
    guard.finish()
