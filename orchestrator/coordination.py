"""Cross-process coordination and crash-safe state writes."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import socket
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from contextvars import ContextVar, Token
from pathlib import Path
from typing import TYPE_CHECKING, Any, NewType, Protocol, TextIO

if TYPE_CHECKING:
    from .journal import EventKind


class LockTimeout(TimeoutError):
    """A process-shared resource remained owned beyond the bounded wait."""


GitLockIdentity = NewType("GitLockIdentity", str)
ProcessStart = NewType("ProcessStart", int)

LOCK_TIMEOUT_ENV = "ORCHESTRATOR_LOCK_TIMEOUT_SECONDS"
#: Contended identities are queued, not raced, so the wait an operator cares about
#: is "how long may a whole turn take", not "how long until one attempt gives up".
#: Minutes: a gate run inside a merge turn is normal, and failing a dispatch that
#: would simply have been served next is the outcome this bound exists to avoid.
DEFAULT_LOCK_TIMEOUT = 900.0


def lock_timeout_seconds(env: Mapping[str, str] | None = None) -> float:
    """Return the configured watchdog bound for a queued advisory-lock wait."""
    raw = (os.environ if env is None else env).get(LOCK_TIMEOUT_ENV)
    if raw is None:
        return DEFAULT_LOCK_TIMEOUT
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{LOCK_TIMEOUT_ENV} must be a number of seconds") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{LOCK_TIMEOUT_ENV} must be a finite number of seconds above zero")
    return value


def process_start_identity(pid: int) -> ProcessStart | None:
    """Return Linux's same-host process start token, or ``None`` if not live.

    A bare pid is not an identity. The kernel recycles pids, so a recorded pid on
    its own can be reported live forever by an unrelated process that happens to
    inherit the number. Pairing the pid with the start time the kernel stamped on
    that process makes a recorded owner identifiable across such reuse.
    """
    if pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        pass
    try:
        # The parenthesized comm field may contain spaces or parentheses. Fields
        # after its final ')' begin with state (field 3); starttime is field 22.
        # Every unsuitable root — missing, not a directory, or not procfs-shaped —
        # already degrades through the handler below to "not identifiable", the one
        # meaning both callers act on. That answer is not conservative by itself:
        # the merge queue reaps such a ticket and the sweeper may reclaim such
        # scratch, which is why the sweeper also demands an unheld ownership lock.
        # llmlint: ignore[boundary_inputs_validated] test-only procfs seam, handled below
        proc_root = Path(os.environ.get("AI_ORCHESTRATOR_PROC_ROOT", "/proc"))
        fields = (
            (proc_root / str(pid) / "stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        )
        if fields[0] == "Z":
            return None
        return ProcessStart(int(fields[19]))
    except (OSError, IndexError, ValueError):
        return None


class HarnessObserver(Protocol):
    """Receive one typed harness-overhead observation for the active node."""

    def __call__(self, kind: EventKind, detail: Mapping[str, str | float | bool]) -> None: ...


_observer: ContextVar[HarnessObserver | None] = ContextVar("harness_observer", default=None)
_notifying_observer: ContextVar[bool] = ContextVar("notifying_harness_observer", default=False)


def set_harness_observer(observer: HarnessObserver) -> Token[HarnessObserver | None]:
    """Attach node-scoped telemetry to coordination and workspace operations."""
    return _observer.set(observer)


def reset_harness_observer(token: Token[HarnessObserver | None]) -> None:
    """Restore the observer that preceded a node execution."""
    _observer.reset(token)


def observe_harness(kind: EventKind, detail: Mapping[str, str | float | bool]) -> None:
    """Report harness overhead without allowing observation to affect execution."""
    observer = _observer.get()
    if observer is None or _notifying_observer.get():
        return
    token = _notifying_observer.set(True)
    try:
        with suppress(Exception):
            observer(kind, detail)
    finally:
        _notifying_observer.reset(token)


def state_root() -> Path:
    """Return the shared orchestrator state root, honoring its operator override."""
    override = os.environ.get("AI_ORCHESTRATOR_HOME")
    if override is not None and not override.strip():
        raise ValueError("AI_ORCHESTRATOR_HOME must not be empty")
    return Path(override).expanduser() if override is not None else Path.home() / ".ai-orchestrator"


def _lock_root() -> Path:
    return state_root() / "locks"


def lock_path(identity: str) -> Path:
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return _lock_root() / f"{digest}.lock"


def git_lock_identity(common_dir: str | Path) -> GitLockIdentity:
    """Return the advisory-lock identity for a repository's git common directory."""
    return GitLockIdentity(f"git:{Path(common_dir)}")


def _try_lock(path: Path, mode: int) -> TextIO | None:
    """Take the lock only if it is free right now (the fail-fast lease mode)."""
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle, mode | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def _queue_for_lock(path: Path, mode: int, timeout: float) -> TextIO | None:
    """Wait in the kernel's own ``flock`` queue, abandoning the turn after ``timeout``.

    A busy-poll of ``LOCK_NB`` does not queue: every waiter races on each retry, so
    an unlucky one can be passed over indefinitely and then fail while the resource
    was never actually scarce. A blocking ``flock`` puts this process in line.

    Blocking has no timeout of its own, so the wait happens on a helper thread and
    this one watches the clock. If the watchdog fires first, the abandoned turn is
    handed back the instant the kernel grants it: closing the file descriptor
    releases the lock, so the next waiter is served rather than deadlocked behind a
    caller that already gave up.
    """
    guard = threading.Lock()
    granted: list[TextIO] = []
    abandoned = False
    settled = threading.Event()

    def wait_in_line() -> None:
        handle = path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle, mode)
        except OSError:
            handle.close()
            settled.set()
            return
        with guard:
            if abandoned:
                handle.close()
            else:
                granted.append(handle)
        settled.set()

    threading.Thread(target=wait_in_line, name="advisory-lock-wait", daemon=True).start()
    settled.wait(timeout)
    with guard:
        if granted:
            return granted[0]
        abandoned = True
        return None


def _recorded_owner(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip() or "unknown owner"
    except OSError:
        return "unknown owner"


@contextmanager
def advisory_lock(
    identity: str, *, timeout: float | None = None, shared: bool = False
) -> Iterator[None]:
    """Lock an identity, with owner metadata and a bounded queued wait.

    ``timeout`` bounds the whole wait; ``None`` takes it from `lock_timeout_seconds`.
    A non-positive ``timeout`` keeps the fail-fast semantics a lease needs, where
    "someone else owns this" is the answer rather than something to wait out.

    ``shared`` marks occupancy rather than ownership: any number of holders may
    share it, and an exclusive taker fails while even one does. That is what
    answers "is anyone still in here?" for a resource several processes legitimately
    occupy at once — where an exclusive lease would make the second occupant either
    fail or, worse, proceed unprotected.
    """
    seconds = lock_timeout_seconds() if timeout is None else timeout
    mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    path = lock_path(identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    handle = _try_lock(path, mode) if seconds <= 0 else _queue_for_lock(path, mode, seconds)
    waited = max(0.0, time.monotonic() - started)
    reportable = not identity.startswith("journal:")
    if handle is None:
        if reportable:
            observe_harness(
                "lock-wait", {"identity": identity, "seconds": waited, "acquired": False}
            )
        raise LockTimeout(
            f"timed out after {seconds:g}s waiting for {identity!r}; "
            f"owner: {_recorded_owner(path)} (raise {LOCK_TIMEOUT_ENV} if this wait is legitimate)"
        )
    if reportable:
        observe_harness("lock-wait", {"identity": identity, "seconds": waited, "acquired": True})
    with handle:
        if not shared:
            # Only an exclusive holder can honestly name itself the owner; a shared
            # holder is one of several, and stamping its pid would send a waiter
            # after an arbitrary one of them.
            handle.seek(0)
            handle.truncate()
            handle.write(
                f"pid={os.getpid()} host={socket.gethostname()} acquired={time.time():.0f}\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    """Atomically replace a JSON file and durably sync its containing directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary)


def atomic_text(path: Path, value: str) -> None:
    """Atomically replace a UTF-8 text file and durably sync its directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
