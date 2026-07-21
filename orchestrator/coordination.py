"""Cross-process coordination and crash-safe state writes."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import socket
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from contextvars import ContextVar, Token
from pathlib import Path
from typing import TYPE_CHECKING, Any, NewType, Protocol

if TYPE_CHECKING:
    from .journal import EventKind


class LockTimeout(TimeoutError):
    """A process-shared resource remained owned beyond the bounded wait."""


GitLockIdentity = NewType("GitLockIdentity", str)


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


def _lock_root() -> Path:
    override = os.environ.get("AI_ORCHESTRATOR_HOME")
    if override is not None and not override.strip():
        raise ValueError("AI_ORCHESTRATOR_HOME must not be empty")
    root = Path(override) if override is not None else Path.home() / ".ai-orchestrator"
    return root / "locks"


def lock_path(identity: str) -> Path:
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return _lock_root() / f"{digest}.lock"


def git_lock_identity(common_dir: str | Path) -> GitLockIdentity:
    """Return the advisory-lock identity for a repository's git common directory."""
    return GitLockIdentity(f"git:{Path(common_dir)}")


@contextmanager
def advisory_lock(identity: str, *, timeout: float = 30.0) -> Iterator[None]:
    """Exclusively lock an identity, with owner metadata and a bounded wait."""
    path = lock_path(identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        started = time.monotonic()
        deadline = started + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    waited = max(0.0, time.monotonic() - started)
                    if not identity.startswith("journal:"):
                        observe_harness(
                            "lock-wait",
                            {"identity": identity, "seconds": waited, "acquired": False},
                        )
                    handle.seek(0)
                    owner = handle.read().strip() or "unknown owner"
                    raise LockTimeout(
                        f"timed out after {timeout:g}s waiting for {identity!r}; owner: {owner}"
                    ) from None
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        if not identity.startswith("journal:"):
            observe_harness(
                "lock-wait",
                {
                    "identity": identity,
                    "seconds": max(0.0, time.monotonic() - started),
                    "acquired": True,
                },
            )
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} host={socket.gethostname()} acquired={time.time():.0f}\n")
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
