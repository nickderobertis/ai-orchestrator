"""Conservative cleanup and capacity checks for host scratch space."""

from __future__ import annotations

import argparse
import fcntl
import functools
import math
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import ParamSpec, TypeVar

WATCHDOG_PATTERN = "orchestrator-watchdog-*"
THIRD_PARTY_PATTERNS = (
    "oneharness-sdk-*",
    "oneharness-counter-*",
    "nx-native-file-cache-*",
    "visual-*",
    "screencomp-*",
    "playwright*",
)
DEFAULT_MIN_AGE_SECONDS = 24 * 60 * 60
DEFAULT_MIN_FREE_BYTES = 5 * 1024**3
MIN_FREE_BYTES_ENV = "ORCHESTRATOR_MIN_FREE_BYTES"
MAX_INSPECTED_PATHS = 20
CAPACITY_ERROR_MARKER = "scratch-capacity-preflight:"
SCRATCH_LOCK_NAME = ".orchestrator-scratch.lock"

P = ParamSpec("P")
R = TypeVar("R")


class ScratchCapacityError(RuntimeError):
    """The scratch filesystem cannot safely support a lifecycle dispatch."""


def capacity_failure_detail(exc: BaseException) -> str | None:
    detail = str(exc).strip()
    if isinstance(exc, ScratchCapacityError) or CAPACITY_ERROR_MARKER in detail:
        return detail
    return None


@dataclass(frozen=True)
class SweepResult:
    removed: tuple[Path, ...]
    reclaimed_bytes: int
    candidates: tuple[Path, ...]
    third_party_skipped: bool = False


@contextmanager
def _scratch_lock(root: Path, *, exclusive: bool, nonblocking: bool = False) -> Iterator[bool]:
    flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(root / SCRATCH_LOCK_NAME, flags, 0o600)
    acquired = False
    try:
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        if nonblocking:
            operation |= fcntl.LOCK_NB
        try:
            fcntl.flock(fd, operation)
            acquired = True
        except BlockingIOError:
            pass
        yield acquired
    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def scratch_dispatch_guarded(function: Callable[P, R]) -> Callable[P, R]:
    """Hold the host scratch shared lock for one lifecycle's full duration."""

    @functools.wraps(function)
    def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
        scratch_root = Path(tempfile.gettempdir()).resolve()
        with _scratch_lock(scratch_root, exclusive=False) as acquired:
            if not acquired:  # pragma: no cover - blocking shared acquisition
                raise RuntimeError("scratch dispatch lock was not acquired")
            return function(*args, **kwargs)

    return guarded


def _pid_is_live(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _watchdog_is_orphaned(path: Path) -> bool:
    try:
        pid = int((path / "pid").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return not _pid_is_live(pid)


def _tree_size(path: Path) -> int:
    total = 0
    try:
        entries = path.rglob("*")
        for entry in entries:
            try:
                if entry.is_file() and not entry.is_symlink():
                    total += entry.stat().st_size
            except FileNotFoundError:
                continue
    except FileNotFoundError:
        pass
    return total


def sweep_scratch(
    root: Path | None = None,
    *,
    min_age_seconds: float = DEFAULT_MIN_AGE_SECONDS,
    dry_run: bool = False,
    now: float | None = None,
) -> SweepResult:
    """Remove definite watchdog orphans and conservatively stale known scratch."""
    scratch_root = (root or Path(tempfile.gettempdir())).resolve()
    cutoff = (time.time() if now is None else now) - min_age_seconds
    candidates: set[Path] = set()
    for path in scratch_root.glob(WATCHDOG_PATTERN):
        if path.is_dir() and not path.is_symlink() and _watchdog_is_orphaned(path):
            candidates.add(path)

    removed: list[Path] = []
    reclaimed = 0
    with _scratch_lock(scratch_root, exclusive=True, nonblocking=True) as can_sweep_third_party:
        if can_sweep_third_party:
            for pattern in THIRD_PARTY_PATTERNS:
                for path in scratch_root.glob(pattern):
                    try:
                        stale = (
                            path.is_dir()
                            and not path.is_symlink()
                            and path.stat().st_mtime < cutoff
                        )
                    except FileNotFoundError:
                        continue
                    if stale:
                        candidates.add(path)

        ordered = tuple(sorted(candidates))
        if not dry_run:
            for path in ordered:
                if path.match(WATCHDOG_PATTERN):
                    if not _watchdog_is_orphaned(path):
                        continue
                else:
                    # Discovery and removal occur under the exclusive lock, so
                    # no lifecycle can start using third-party scratch in between.
                    if not can_sweep_third_party:  # pragma: no cover - construction invariant
                        continue
                    try:
                        if path.stat().st_mtime >= cutoff:
                            continue
                    except FileNotFoundError:
                        continue
                size = _tree_size(path)
                try:
                    shutil.rmtree(path)
                except FileNotFoundError:
                    continue
                removed.append(path)
                reclaimed += size
    return SweepResult(
        tuple(removed),
        reclaimed,
        ordered,
        third_party_skipped=not can_sweep_third_party,
    )


def configured_min_free_bytes(env: dict[str, str] | None = None) -> int:
    raw = (env or os.environ).get(MIN_FREE_BYTES_ENV)
    if raw is None:
        return DEFAULT_MIN_FREE_BYTES
    try:
        value = int(raw)
    except ValueError as exc:
        raise ScratchCapacityError(f"{MIN_FREE_BYTES_ENV} must be an integer byte count") from exc
    if value < 0:
        raise ScratchCapacityError(f"{MIN_FREE_BYTES_ENV} must be at least zero")
    return value


def require_scratch_capacity(
    path: Path | None = None, *, min_free_bytes: int | None = None
) -> None:
    scratch_path = (path or Path(tempfile.gettempdir())).resolve()
    threshold = configured_min_free_bytes() if min_free_bytes is None else min_free_bytes
    available = shutil.disk_usage(scratch_path).free
    if available < threshold:
        raise ScratchCapacityError(
            f"{CAPACITY_ERROR_MARKER} scratch filesystem at {scratch_path} has "
            f"{available} bytes free, below the "
            f"{threshold}-byte dispatch threshold; run `just sweep-scratch` and retry "
            f"(configure with {MIN_FREE_BYTES_ENV})"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sweep orphaned known scratch directories")
    parser.add_argument("--root", type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument(
        "--min-age-hours",
        type=float,
        default=DEFAULT_MIN_AGE_SECONDS / 3600,
        help="minimum age for third-party scratch (default: 24)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not math.isfinite(args.min_age_hours) or args.min_age_hours < 0:
        parser.error("--min-age-hours must be a finite number at least zero")
    if not args.root.is_dir():
        parser.error(f"--root must be an existing directory: {args.root}")
    try:
        result = sweep_scratch(
            args.root,
            min_age_seconds=args.min_age_hours * 3600,
            dry_run=args.dry_run,
        )
    except OSError as exc:
        print(
            f"sweep-scratch: could not remove scratch under {args.root}: {exc}; "
            "check path permissions, inspect with `just sweep-scratch --dry-run`, and retry",
            file=sys.stderr,
        )
        return 1
    action = "would remove" if args.dry_run else "removed"
    paths = result.candidates if args.dry_run else result.removed
    inspection = ""
    if args.dry_run:
        shown = ", ".join(os.fspath(path) for path in paths[:MAX_INSPECTED_PATHS])
        omitted = len(paths) - min(len(paths), MAX_INSPECTED_PATHS)
        inspection = f"; candidates=[{shown}]"
        if omitted:
            inspection += f" ({omitted} more omitted)"
    if result.third_party_skipped:
        inspection += "; third-party sweep skipped: lifecycle dispatch active"
    print(
        f"sweep-scratch: {action} {len(paths)} directories; "
        f"reclaimed {result.reclaimed_bytes} bytes{inspection}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
