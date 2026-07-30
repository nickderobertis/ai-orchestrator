"""Conservative cleanup and capacity checks for host scratch space."""

from __future__ import annotations

import argparse
import fcntl
import functools
import json
import math
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import ParamSpec, Protocol, TypeVar

from .coordination import ProcessStart, proc_root, process_start_identity

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
#: The families below are produced *by* an active dispatch — one Nx temp install per
#: `nx` invocation, one run directory per pytest session, one effective-config
#: directory per onejudge dispatch — at roughly 8 GB/hour under load. Waiting out
#: `DEFAULT_MIN_AGE_SECONDS` fills the filesystem a day before the first byte becomes
#: eligible, so age is not what makes removing them safe: proven non-reference is.
#: The short age that remains guards only the gap between creating such a directory
#: and the first instant a live process names it — a spawn, measured in
#: milliseconds — plus a procfs scan that raced a fork. Fifteen minutes is orders of
#: magnitude beyond that window and still well inside one round transition, and it
#: deliberately does not touch the conservative default that governs
#: `THIRD_PARTY_PATTERNS`, which have no such reference proof behind them.
UNREFERENCED_MIN_AGE_SECONDS = 15 * 60
#: Every `bunx nx` — which is how `scripts/nx.sh` runs every target — installs a
#: private `nx` into a fresh temp directory and never removes it (~80 MB each).
#: `tmp-*` is far too generic to sweep on its own, so the glob only narrows the scan
#: and the *shape* decides: a manifest declaring one `nx` devDependency and nothing
#: else, an installed `node_modules`, and no content beyond the package manager's own
#: bookkeeping. Anything else is somebody's real work.
NX_INSTALL_PATTERN = "tmp-*"
NX_INSTALL_ENTRIES = frozenset(
    {
        "package.json",
        "node_modules",
        ".npmrc",
        "package-lock.json",
        "bun.lock",
        "bun.lockb",
        "yarn.lock",
        "pnpm-lock.yaml",
    }
)
PYTEST_ROOT_PATTERN = "pytest-of-*"
PYTEST_RUN_PREFIX = "pytest-"
PYTEST_CURRENT_LINK = "pytest-current"
PYTEST_LOCK_NAME = ".lock"
#: pytest's own `tmp_path_retention_count` default: it keeps the newest three run
#: directories per user root and deletes older ones itself. Sweeping the same three
#: would fight that convention and destroy the trees an operator reaches for after a
#: failure, so the sweep starts where pytest's own retention ends.
PYTEST_RETAINED_RUNS = 3
ONEJUDGE_SCRATCH_PATTERN = "onejudge-python-*"
DEFAULT_MIN_FREE_BYTES = 5 * 1024**3
MIN_FREE_BYTES_ENV = "ORCHESTRATOR_MIN_FREE_BYTES"
MAX_INSPECTED_PATHS = 20
CAPACITY_ERROR_MARKER = "scratch-capacity-preflight:"


class ScratchFamilyFinder(Protocol):
    """Find conservatively identified members of one unreferenced scratch family."""

    def __call__(self, root: Path) -> Iterator[Path]: ...


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
    referenced_retained: tuple[Path, ...] = ()
    reference_proof_unavailable: bool = False


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


def _names_a_live_process(name: str) -> bool:
    """Report whether a temp install directory still names the process that made it.

    The creating process stamps its own pid into the name (`tmp-<pid>-<random>`).
    That is a second signal independent of the reference proof: a node process can
    finish installing and then require modules out of the tree with nothing left open
    and nothing naming the path, so the pid is what covers that gap. Pid reuse only
    over-protects here, which is the direction this decision must fail in.
    """
    _, _, remainder = name.partition("-")
    pid, _, _ = remainder.partition("-")
    return pid.isdigit() and process_start_identity(int(pid)) is not None


def _is_nx_temp_install(path: Path) -> bool:
    """Return whether this directory is one throwaway single-`nx` install and nothing else."""
    if path.is_symlink() or not path.is_dir():
        return False
    try:
        names = {entry.name for entry in path.iterdir()}
    except OSError:
        return False
    if "package.json" not in names or names - NX_INSTALL_ENTRIES:
        return False
    if not (path / "node_modules").is_dir():
        return False
    try:
        manifest = json.loads((path / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    match manifest:
        case {"devDependencies": {"nx": _, **other_dependencies}, **other_fields}:
            return not other_dependencies and not other_fields
        case _:
            return False


def _nx_install_candidates(root: Path) -> Iterator[Path]:
    for path in root.glob(NX_INSTALL_PATTERN):
        if _is_nx_temp_install(path) and not _names_a_live_process(path.name):
            yield path


def _pytest_session_is_live(path: Path) -> bool:
    """Honor pytest's own in-use marker: the `.lock` it writes its session pid into."""
    try:
        record = (path / PYTEST_LOCK_NAME).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return False
    except OSError:
        # pytest treats a lock it cannot read as proof the tree is not deletable,
        # because the same permission failure hides the rest of the directory.
        return True
    return not record.isdigit() or process_start_identity(int(record)) is not None


def _pytest_run_candidates(root: Path) -> Iterator[Path]:
    """Yield numbered pytest run directories, which live one level below the root."""
    for parent in sorted(root.glob(PYTEST_ROOT_PATTERN)):
        if parent.is_symlink() or not parent.is_dir():
            continue
        current = Path(os.path.realpath(parent / PYTEST_CURRENT_LINK))
        try:
            entries = list(parent.iterdir())
        except OSError:
            continue
        numbered: dict[int, Path] = {}
        for entry in entries:
            suffix = entry.name.removeprefix(PYTEST_RUN_PREFIX)
            if suffix == entry.name or not suffix.isdigit():
                continue
            if entry.is_symlink() or not entry.is_dir():
                continue
            numbered[int(suffix)] = entry
        for number in sorted(numbered)[:-PYTEST_RETAINED_RUNS]:
            path = numbered[number]
            if path != current and not _pytest_session_is_live(path):
                yield path


def _onejudge_scratch_candidates(root: Path) -> Iterator[Path]:
    for path in root.glob(ONEJUDGE_SCRATCH_PATTERN):
        if not path.is_symlink() and path.is_dir():
            yield path


#: The authoritative family list and extension point for scratch that an active
#: dispatch keeps producing. Unlike `THIRD_PARTY_PATTERNS`, these are reclaimed while
#: dispatches run, so a family is a candidate *finder* rather than a name glob: each
#: one has to identify its own directories without a pattern wide enough to catch
#: unrelated trees, and to honor whatever retention its producer already applies.
UNREFERENCED_FAMILIES: tuple[ScratchFamilyFinder, ...] = (
    _nx_install_candidates,
    _pytest_run_candidates,
    _onejudge_scratch_candidates,
)


def _process_reference_strings(entry: Path) -> Iterator[str]:
    """Yield every path a single live process names: its argv, its cwd, its open files."""
    with suppress(OSError):
        yield (entry / "cmdline").read_bytes().decode("utf-8", "replace")
    with suppress(OSError):
        yield os.readlink(entry / "cwd")
    try:
        descriptors = sorted((entry / "fd").iterdir())
    except OSError:
        # Another user's process hides its descriptors from this one. It also cannot
        # be using scratch this sweep is able to delete, since `/tmp` is sticky.
        return
    for descriptor in descriptors:
        with suppress(OSError):
            yield os.readlink(descriptor)


def _record_reference(text: str, scratch_root: Path, sink: set[str]) -> None:
    """Protect a named path and every scratch directory containing it."""
    path = PurePosixPath(text)
    root = os.fspath(scratch_root)
    while os.fspath(path) != root and path != path.parent:
        sink.add(os.fspath(path))
        path = path.parent


def _referenced_scratch_paths(scratch_root: Path) -> frozenset[str] | None:
    """Return every path under the scratch root that a live process still names.

    This is what lets these families be reclaimed *during* a dispatch. An mtime
    cutoff answers "was anything written here lately", which is neither necessary nor
    sufficient; asking the kernel who still names a path protects a directory in use
    for one second and releases one abandoned a minute ago.

    ``None`` means the question could not be asked, which is not the same answer as
    "nothing is referenced" and must never be confused with it: an absent, unmounted,
    or misconfigured procfs root would otherwise authorize deleting every live
    dispatch's scratch at once. This process is necessarily alive, so a root that
    cannot show *it* is not a procfs this proof can be built on.
    """
    root = proc_root()
    if not (root / str(os.getpid())).is_dir():
        return None
    marker = os.fspath(scratch_root) + os.sep
    referenced: set[str] = set()
    try:
        entries = sorted(root.iterdir())
    except OSError:  # pragma: no cover - unreadable between the self probe and here
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        for text in _process_reference_strings(entry):
            # One argv or one NUL-joined command line may name several paths.
            index = text.find(marker)
            while index != -1:
                _record_reference(text[index:].split("\0", 1)[0], scratch_root, referenced)
                index = text.find(marker, index + 1)
    return frozenset(referenced)


def _older_than(path: Path, cutoff: float) -> bool:
    try:
        return path.stat().st_mtime < cutoff
    except OSError:
        return False


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
    """Remove definite watchdog orphans, unreferenced harness scratch, and stale known scratch."""
    scratch_root = (root or Path(tempfile.gettempdir())).resolve()
    moment = time.time() if now is None else now
    cutoff = moment - min_age_seconds
    # A caller asking for a shorter age is honored, so `--min-age-hours 0` still means
    # "now"; a longer one never delays a family whose safety comes from non-reference.
    unreferenced_cutoff = moment - min(min_age_seconds, UNREFERENCED_MIN_AGE_SECONDS)
    candidates: set[Path] = set()
    skipped: list[Path] = []
    for path in sorted(scratch_root.glob(WATCHDOG_PATTERN)):
        if not path.is_dir() or path.is_symlink():
            continue
        if _watchdog_is_reclaimable(path):
            candidates.add(path)
        else:
            skipped.append(path)

    referenced = _referenced_scratch_paths(scratch_root)
    referenced_retained: list[Path] = []
    unreferenced: set[Path] = set()
    if referenced is not None:
        for family_candidates in UNREFERENCED_FAMILIES:
            for path in family_candidates(scratch_root):
                if os.fspath(path) in referenced:
                    referenced_retained.append(path)
                elif _older_than(path, unreferenced_cutoff):
                    unreferenced.add(path)
    candidates |= unreferenced

    removed: list[Path] = []
    reclaimed = 0
    fresh: frozenset[str] | None = frozenset()
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
            # Discovery precedes a whole third-party pass, so the non-reference proof
            # is retaken here against the freshest procfs state. Nothing adopts an
            # abandoned directory in these families — every one is named at random by
            # the single process that created it — so the remaining window is a
            # process that made one between the two proofs, which the age covers.
            fresh = _referenced_scratch_paths(scratch_root) if unreferenced else frozenset()
            for path in ordered:
                if path in unreferenced:
                    if fresh is None:
                        # The proof was withdrawn between discovery and removal.
                        continue
                    if os.fspath(path) in fresh:
                        referenced_retained.append(path)
                        continue
                    if not _older_than(path, unreferenced_cutoff):
                        continue
                elif path.match(WATCHDOG_PATTERN):
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
        referenced_retained=tuple(sorted(referenced_retained)),
        reference_proof_unavailable=referenced is None or fresh is None,
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
        help=(
            "minimum age for third-party scratch (default: 24); families proven "
            f"unreferenced by any live process use {UNREFERENCED_MIN_AGE_SECONDS / 60:g} "
            "minutes, or this value when it is shorter"
        ),
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
    if result.referenced_retained:
        inspection += (
            f"; retained {len(result.referenced_retained)} directories referenced by live processes"
        )
    if result.reference_proof_unavailable:
        inspection += (
            f"; harness scratch left alone: no usable procfs at {proc_root()}, so no "
            "live process could be proven done with it"
        )
    print(
        f"sweep-scratch: {action} {len(paths)} directories; "
        f"reclaimed {result.reclaimed_bytes} bytes{inspection}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
