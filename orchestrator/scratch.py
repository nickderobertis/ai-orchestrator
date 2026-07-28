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

from .coordination import ProcessStart, process_start_identity

WATCHDOG_PREFIX = "orchestrator-watchdog-"
WATCHDOG_PATTERN = f"{WATCHDOG_PREFIX}*"
OWNER_LOCK_NAME = "owner.lock"
OWNER_RECORD_LIMIT = 128
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
    watchdog_retained: tuple[Path, ...] = ()


def _open_lock_file(path: Path, *, create: bool) -> int:
    flags = os.O_RDWR | os.O_CLOEXEC | (os.O_CREAT if create else 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(path, flags, 0o600)


@contextmanager
def _scratch_lock(root: Path, *, exclusive: bool, nonblocking: bool = False) -> Iterator[bool]:
    fd = _open_lock_file(root / SCRATCH_LOCK_NAME, create=True)
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


@dataclass(frozen=True)
class _OwnerIdentity:
    """The dispatching process that owns one scratch directory, across pid reuse."""

    pid: int
    process_start: ProcessStart

    def render(self) -> str:
        return f"{self.pid} {self.process_start}"

    @classmethod
    def parse(cls, record: str) -> _OwnerIdentity | None:
        """Return the recorded identity, or ``None`` when it is not identifiable."""
        pid, _, process_start = record.partition(" ")
        try:
            return cls(int(pid), ProcessStart(int(process_start)))
        except ValueError:
            return None

    @classmethod
    def current(cls, pid: int) -> _OwnerIdentity | None:
        process_start = process_start_identity(pid)
        return None if process_start is None else cls(pid, process_start)

    def is_live(self) -> bool:
        return process_start_identity(self.pid) == self.process_start


@contextmanager
def owned_scratch_directory() -> Iterator[Path]:
    """Create a dispatch scratch directory and prove ownership for its full scope.

    The sweeper has to answer "can anything still be using this tree?", and no
    recorded pid answers it: the pid a dispatch records belongs to its *worker*,
    which exits while the dispatcher is still reaping descendants and parsing the
    report out of this directory. An advisory lock held across the whole scope is
    the kernel's own answer — it covers every use of the tree regardless of which
    process outlives which, and it is released only when this dispatcher releases
    the directory or dies. The lock file also records the owning identity, so a
    filesystem that does not honor ``flock`` still cannot strand a live owner.
    """
    with tempfile.TemporaryDirectory(prefix=WATCHDOG_PREFIX) as directory:
        path = Path(directory)
        fd = _open_lock_file(path / OWNER_LOCK_NAME, create=True)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            owner = _OwnerIdentity.current(os.getpid())
            os.write(fd, (str(os.getpid()) if owner is None else owner.render()).encode("utf-8"))
            yield path
        finally:
            os.close(fd)


def _legacy_watchdog_is_reclaimable(path: Path) -> bool:
    """Judge a directory that predates ownership locking by its worker pid alone.

    Only a dispatch already running when the lock was introduced lands here. It
    keeps the strongest identity available for that pid, so such a dispatch
    survives the upgrade instead of losing its scratch mid-run.
    """
    try:
        pid = int((path / "pid").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return process_start_identity(pid) is None


def _watchdog_is_reclaimable(path: Path) -> bool:
    """Return whether no dispatcher can still be using this watchdog directory."""
    try:
        fd = _open_lock_file(path / OWNER_LOCK_NAME, create=False)
    except FileNotFoundError:
        return _legacy_watchdog_is_reclaimable(path)
    except OSError:
        # A symlinked, unreadable, or otherwise unopenable lock proves nothing
        # about the owner, so it can never authorize removal.
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        record = os.read(fd, OWNER_RECORD_LIMIT).decode("utf-8", "replace")
    finally:
        os.close(fd)
    owner = _OwnerIdentity.parse(record)
    return owner is None or not owner.is_live()


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
    skipped: list[Path] = []
    for path in sorted(scratch_root.glob(WATCHDOG_PATTERN)):
        if not path.is_dir() or path.is_symlink():
            continue
        if _watchdog_is_reclaimable(path):
            candidates.add(path)
        else:
            skipped.append(path)

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
                    # Re-prove ownership against the freshest state: discovery ran
                    # before the third-party pass. A dispatch that owned the tree
                    # for either pass is preserved by the e2e above; only the
                    # interleaving *between* the two passes is left, and no
                    # external process can open that window on demand, so the
                    # unit test drives this predicate through it directly.
                    # llmlint: ignore[changed_behavior_has_e2e] in-process interleaving only
                    if not _watchdog_is_reclaimable(path):
                        skipped.append(path)
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
        watchdog_retained=tuple(sorted(skipped)),
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
    if result.watchdog_retained:
        inspection += (
            f"; retained {len(result.watchdog_retained)} watchdog directories "
            "not proven reclaimable"
        )
    print(
        f"sweep-scratch: {action} {len(paths)} directories; "
        f"reclaimed {result.reclaimed_bytes} bytes{inspection}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
