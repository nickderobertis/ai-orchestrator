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
from pathlib import Path
from typing import Any


class LockTimeout(TimeoutError):
    """A process-shared resource remained owned beyond the bounded wait."""


def _lock_root() -> Path:
    override = os.environ.get("AI_ORCHESTRATOR_HOME")
    if override is not None and not override.strip():
        raise ValueError("AI_ORCHESTRATOR_HOME must not be empty")
    root = Path(override) if override is not None else Path.home() / ".ai-orchestrator"
    return root / "locks"


def lock_path(identity: str) -> Path:
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return _lock_root() / f"{digest}.lock"


def git_lock_identity(common_dir: str | Path) -> str:
    """Return the advisory-lock identity for a repository's git common directory."""
    return f"git:{Path(common_dir)}"


@contextmanager
def advisory_lock(identity: str, *, timeout: float = 30.0) -> Iterator[None]:
    """Exclusively lock an identity, with owner metadata and a bounded wait."""
    path = lock_path(identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    handle.seek(0)
                    owner = handle.read().strip() or "unknown owner"
                    raise LockTimeout(
                        f"timed out after {timeout:g}s waiting for {identity!r}; owner: {owner}"
                    ) from None
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
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
