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
from .watchdog import ProcessId, terminate_processes

WATCHDOG_PREFIX = "orchestrator-watchdog-"
WATCHDOG_PATTERN = f"{WATCHDOG_PREFIX}*"
#: The variable every dispatch exports into the environment of everything it starts,
#: naming the agent status directory inside its own watchdog scratch tree. The kernel
#: fixes an environment at ``exec`` and a process cannot shed it, so this is the one
#: piece of ownership evidence that survives being reparented to init — which is what
#: the harness's own leavings look like once their dispatcher is gone. `dispatch.py`
#: writes it; the sweep below reads it back out of ``/proc/<pid>/environ``.
AGENT_STATUS_DIR_ENV = "ORCHESTRATOR_AGENT_STATUS_DIR"
#: The one directory a dispatch stamps that variable with, inside its watchdog tree.
#: The sweep requires the stamp to name exactly this child of exactly one watchdog
#: directory, so a value that merely lands somewhere under the swept root proves
#: nothing and claims nothing.
AGENT_STATUS_DIR_NAME = "agent"
#: The file a streamed agent turn republishes on every observed event, inside that
#: same directory. It is declared here, beside the directory it lives in, because it
#: has two readers that must not drift: `orchestrator.dispatch` names it in the
#: status-file contract the wrapper is drift-gated against, and
#: `orchestrator.activity` reads it back out of this scratch root for the planner
#: views. Neither of those imports the other.
AGENT_ACTIVITY_NAME = "agent.activity"
OWNER_LOCK_NAME = "owner.lock"
OWNER_RECORD_LIMIT = 128
THIRD_PARTY_PATTERNS = (
    "oneharness-sdk-*",
    "oneharness-counter-*",
    "visual-*",
    "screencomp-*",
    "playwright*",
)
DEFAULT_MIN_AGE_SECONDS = 24 * 60 * 60
#: The families below are produced *by* an active dispatch — one Nx temp install per
#: `nx` invocation, one native-binary cache per workspace root, one run directory per
#: pytest session, one effective-config directory per onejudge dispatch — at roughly
#: 8 GB/hour under load. Waiting out
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
#: Nx copies its ~22 MB native binary out of `node_modules` into
#: `<tmp>/nx-native-file-cache-<7 hex>`, where the key is a digest of the *workspace
#: root*, the Nx version, and the username. Every lifecycle worktree is a fresh
#: workspace root, so the key never repeats and the copy is stranded the moment the
#: worktree goes away: 87 GB in two days on this host. Nx does mean to reuse one
#: directory — per workspace root — so this is not a reuse defect to fix upstream of
#: the sweep; it is scratch whose owner is gone. The name is specific enough to sweep
#: on and the shape confirms it: nothing but the `<version>-<binary>.node` copies Nx
#: writes there.
NX_NATIVE_CACHE_PREFIX = "nx-native-file-cache-"
NX_NATIVE_CACHE_PATTERN = f"{NX_NATIVE_CACHE_PREFIX}*"
NX_NATIVE_CACHE_KEY_LENGTH = 7
NX_NATIVE_CACHE_KEY_ALPHABET = frozenset("0123456789abcdef")
NX_NATIVE_CACHE_ENTRY_SUFFIX = ".node"
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


@dataclass(frozen=True)
class ScratchFamily:
    """One reclaimable family, named so the sweep can report that it examined it."""

    name: str
    find: ScratchFamilyFinder


@dataclass(frozen=True)
class SkippedFamily:
    """One family the sweep did not examine, and the reason it could not."""

    name: str
    reason: str

    def render(self) -> str:
        return f"{self.name} ({self.reason})"


WATCHDOG_FAMILY = "watchdog"
THIRD_PARTY_FAMILY = "third-party"
ORPHAN_FAMILY = "dispatch-orphans"
THIRD_PARTY_SKIP_REASON = "lifecycle dispatch active"
REFERENCE_PROOF_SKIP_REASON = "no live process could be proven done with it"
ORPHAN_PROOF_SKIP_REASON = "no usable procfs to prove what a finished dispatch left running"


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
    swept_families: tuple[str, ...] = ()
    skipped_families: tuple[SkippedFamily, ...] = ()
    orphan_candidates: tuple[ProcessId, ...] = ()
    reaped_processes: tuple[ProcessId, ...] = ()


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


def watchdog_has_a_live_owner(path: Path) -> bool:
    """Whether a dispatcher demonstrably still holds this watchdog directory.

    `_watchdog_is_reclaimable` below asks the same question for the sweeper, which
    decides what it may *delete* — so every uncertainty there resolves toward "keep
    it". This decides what a read-only view may *believe*, so every uncertainty here
    resolves the other way: an absent, unreadable or unparseable lock, and a recorded
    owner that is no longer live, all mean no. It is deliberately not the negation of
    that function, and the two must not be collapsed into one. See
    `orchestrator.activity`, whose publications this qualifies.
    """
    try:
        fd = _open_lock_file(path / OWNER_LOCK_NAME, create=False)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Somebody holds it, which is the kernel's own answer and the strongest
            # one available: a dispatcher keeps this lock for its whole scope.
            return True
        record = os.read(fd, OWNER_RECORD_LIMIT).decode("utf-8", "replace")
    finally:
        os.close(fd)
    owner = _OwnerIdentity.parse(record)
    return owner is not None and owner.is_live()


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


def _dispatch_is_finished(directory: Path) -> bool:
    """Whether the dispatch that owned this watchdog scratch directory is over.

    Two proofs, and the sweep needs either. An *absent* directory is the stronger
    one: `owned_scratch_directory` removes the tree only when its whole scope exits,
    so a name that is gone belonged to a dispatch that ran to completion. A directory
    that is still there is judged by exactly the ownership proof the watchdog family
    already uses, so a dispatcher that is merely slow — or one whose worker exited
    while it was still parsing a report — keeps everything it started.
    """
    if directory.is_symlink():
        return False
    if not directory.exists():
        return True
    return directory.is_dir() and _watchdog_is_reclaimable(directory)


def _stamped_watchdog_directory(environ: bytes, scratch_root: Path) -> Path | None:
    """The watchdog scratch directory a process's inherited environment names.

    Read as whole NUL-delimited entries rather than as a substring, so a value that
    merely contains the variable's name cannot be mistaken for the variable. The value
    then has to be the exact path a dispatch writes — ``<root>/<watchdog dir>/agent``,
    a shape only `owned_scratch_directory` and `run_onejudge` between them produce.
    Anything else under the swept root is somebody's, but there is no evidence it is
    this harness's, and evidence is the whole basis for acting on it.
    """
    stamp = AGENT_STATUS_DIR_ENV.encode("utf-8") + b"="
    for entry in environ.split(b"\0"):
        if not entry.startswith(stamp):
            continue
        named = PurePosixPath(entry[len(stamp) :].decode("utf-8", "replace"))
        try:
            relative = named.relative_to(PurePosixPath(scratch_root))
        except ValueError:
            continue
        parts = relative.parts
        if len(parts) != 2 or parts[1] != AGENT_STATUS_DIR_NAME:
            continue
        if parts[0].startswith(WATCHDOG_PREFIX) and parts[0] != WATCHDOG_PREFIX:
            return scratch_root / parts[0]
    return None


def _self_and_ancestors(root: Path) -> frozenset[ProcessId]:
    """This process and everything above it, which no sweep may ever signal.

    Belt and braces: a sweep running inside a live dispatch already carries that
    dispatch's stamp, and its directory is owned, so the ownership proof retains it.
    Naming the chain outright means a proof that somehow went wrong still cannot make
    the sweep kill the run it is part of.
    """
    chain: set[ProcessId] = set()
    pid = ProcessId(os.getpid())
    while pid > 1 and pid not in chain:
        chain.add(pid)
        try:
            raw = (root / str(pid) / "stat").read_text(encoding="utf-8")
        except OSError:
            break
        fields = raw[raw.rfind(")") + 2 :].split()
        if len(fields) < 2 or not fields[1].isdigit():
            break
        pid = ProcessId(int(fields[1]))
    return frozenset(chain)


def orphaned_dispatch_processes(scratch_root: Path) -> tuple[ProcessId, ...] | None:
    """Every live process a *finished* dispatch left behind, by its environment stamp.

    Parentage cannot answer this. Once a dispatcher dies its descendants are adopted
    by init, and the walk the rest of this harness terminates trees with has nothing
    left to walk — which is how a `scripts/oneharness-agent.sh` came to be resident for
    two days and twenty hours with no way to recognise it as ours. The environment the
    kernel fixed at ``exec`` does survive that, so a stamp naming a watchdog scratch
    directory is ownership evidence, and the same directory's ownership lock is what
    says whether the dispatch behind it is over.

    ``None`` means the question could not be asked — the same distinction
    `_referenced_scratch_paths` draws, and for the same reason: a procfs that cannot
    show this very process is not one any claim may be built on.
    """
    root = proc_root()
    if not (root / str(os.getpid())).is_dir():
        return None
    protected = _self_and_ancestors(root)
    finished: dict[Path, bool] = {}
    orphans: set[ProcessId] = set()
    try:
        entries = sorted(root.iterdir())
    except OSError:  # pragma: no cover - unreadable between the self probe and here
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = ProcessId(int(entry.name))
        if pid in protected:
            continue
        try:
            environ = (entry / "environ").read_bytes()
        except OSError:
            # Gone, or another user's — either way not something this sweep may claim.
            continue
        directory = _stamped_watchdog_directory(environ, scratch_root)
        if directory is None:
            continue
        if directory not in finished:
            finished[directory] = _dispatch_is_finished(directory)
        if finished[directory]:
            orphans.add(pid)
    return tuple(sorted(orphans))


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


def _is_nx_native_file_cache(path: Path) -> bool:
    """Return whether this directory holds nothing but Nx's copied native binaries."""
    key = path.name[len(NX_NATIVE_CACHE_PREFIX) :]
    if len(key) != NX_NATIVE_CACHE_KEY_LENGTH or not NX_NATIVE_CACHE_KEY_ALPHABET.issuperset(key):
        return False
    if path.is_symlink() or not path.is_dir():
        return False
    try:
        entries = list(path.iterdir())
    except OSError:
        return False
    return all(
        entry.name.endswith(NX_NATIVE_CACHE_ENTRY_SUFFIX)
        and not entry.is_symlink()
        and entry.is_file()
        for entry in entries
    )


def _nx_native_cache_candidates(root: Path) -> Iterator[Path]:
    """Yield Nx native-binary caches, whose only live reference is a memory mapping.

    Nx `dlopen`s the copy and keeps no descriptor open, so the memory map is the sole
    channel that names one of these while it is in use — which is why the reference
    proof reads `maps`. Removing a cache a live process already mapped would in fact
    be harmless on Linux (the inode outlives the unlink and the next invocation
    re-copies), but Nx's loader stats the file and *then* loads it, so keeping mapped
    caches is what shuts that narrow window rather than reasoning about it.
    """
    for path in root.glob(NX_NATIVE_CACHE_PATTERN):
        if _is_nx_native_file_cache(path):
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
#: unrelated trees, and to honor whatever retention its producer already applies. The
#: name is what the sweep reports, so a family that was never examined can never be
#: mistaken for one that had nothing to reclaim.
UNREFERENCED_FAMILIES: tuple[ScratchFamily, ...] = (
    ScratchFamily("nx-install", _nx_install_candidates),
    ScratchFamily("nx-native-file-cache", _nx_native_cache_candidates),
    ScratchFamily("pytest-runs", _pytest_run_candidates),
    ScratchFamily("onejudge-scratch", _onejudge_scratch_candidates),
)


#: The procfs files whose whole contents may name a path: argv, the environment a
#: process was handed, and its file-backed memory mappings. `maps` is not redundant
#: with `fd`: a `dlopen`ed library is mapped with no descriptor left behind, so for a
#: running `nx` the mapping is the *only* place its native-binary cache appears.
_REFERENCE_CONTENT_FILES = ("cmdline", "environ", "maps")
#: The procfs links a process resolves to real paths. `cwd` is the only one a real
#: journey can isolate, and the other two are kept anyway rather than leaned on: the
#: kernel maps a running process's executable, so `exe` cannot decide an outcome
#: `maps` has not already decided, and `root` needs a chroot into scratch that this
#: host cannot create (CAP_SYS_CHROOT, with unprivileged user namespaces disabled).
#: Each costs one readlink and neither can do anything but retain more, which is the
#: direction this decision has to fail in — the e2e covers a binary running out of
#: scratch through the channels that can be isolated.
# llmlint: ignore[changed_behavior_has_e2e] `exe` is subsumed by `maps`, `root` needs CAP_SYS_CHROOT
_REFERENCE_LINKS = ("cwd", "root", "exe")
#: One blob may hold many references — NUL-joined argv and environment entries, one
#: mapping per line — so a reference ends at whichever separator comes first.
_REFERENCE_TERMINATORS = ("\0", "\n")


def _process_reference_strings(entry: Path) -> tuple[str, ...]:
    """Return every process reference visible through argv, environment, links, and maps."""
    references: list[str] = []
    for name in _REFERENCE_CONTENT_FILES:
        with suppress(OSError):
            references.append((entry / name).read_bytes().decode("utf-8", "replace"))
    for name in _REFERENCE_LINKS:
        with suppress(OSError):
            references.append(os.readlink(entry / name))
    try:
        descriptors = sorted((entry / "fd").iterdir())
    except OSError:
        return tuple(references)
    for descriptor in descriptors:
        with suppress(OSError):
            references.append(os.readlink(descriptor))
    return tuple(references)


def _reference_at(text: str, index: int) -> str:
    """Return the single path starting at `index`, up to the first separator after it."""
    end = len(text)
    for terminator in _REFERENCE_TERMINATORS:
        stop = text.find(terminator, index)
        if stop != -1:
            end = min(end, stop)
    return text[index:end]


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
            index = text.find(marker)
            while index != -1:
                _record_reference(_reference_at(text, index), scratch_root, referenced)
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

    # Reaped before the reference proof is taken, so a directory whose only remaining
    # claimant was one of these leavings is reclaimable in this same pass rather than
    # protected by the very process the sweep just ended.
    orphans = orphaned_dispatch_processes(scratch_root)
    reaped: tuple[ProcessId, ...] = ()
    if orphans and not dry_run:
        terminate_processes(orphans)
        reaped = orphans

    referenced = _referenced_scratch_paths(scratch_root)
    referenced_retained: list[Path] = []
    unreferenced: set[Path] = set()
    if referenced is not None:
        for family in UNREFERENCED_FAMILIES:
            for path in family.find(scratch_root):
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
            # llmlint: ignore[changed_behavior_has_e2e] The real CLI covers live
            # references; deterministic unit tests drive this in-process-only race.
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
    # Every family lands in exactly one of these two lists, so a report of zero
    # reclaimed bytes always says whether a family had nothing to reclaim or was
    # never examined at all.
    proof_unavailable = referenced is None or fresh is None
    swept_families = [WATCHDOG_FAMILY]
    skipped_families: list[SkippedFamily] = []
    for family in UNREFERENCED_FAMILIES:
        if proof_unavailable:
            skipped_families.append(SkippedFamily(family.name, REFERENCE_PROOF_SKIP_REASON))
        else:
            swept_families.append(family.name)
    if orphans is None:
        skipped_families.append(SkippedFamily(ORPHAN_FAMILY, ORPHAN_PROOF_SKIP_REASON))
    else:
        swept_families.append(ORPHAN_FAMILY)
    if can_sweep_third_party:
        swept_families.append(THIRD_PARTY_FAMILY)
    else:
        skipped_families.append(SkippedFamily(THIRD_PARTY_FAMILY, THIRD_PARTY_SKIP_REASON))
    return SweepResult(
        tuple(removed),
        reclaimed,
        ordered,
        third_party_skipped=not can_sweep_third_party,
        watchdog_retained=tuple(sorted(skipped)),
        referenced_retained=tuple(sorted(referenced_retained)),
        reference_proof_unavailable=proof_unavailable,
        swept_families=tuple(swept_families),
        skipped_families=tuple(skipped_families),
        orphan_candidates=orphans or (),
        reaped_processes=reaped,
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
    # Naming both lists is what keeps a zero honest: whichever list a family is in,
    # the reader can tell "nothing to reclaim" from "never examined".
    inspection += f"; swept families: {', '.join(result.swept_families)}"
    if result.skipped_families:
        inspection += "; skipped families: " + ", ".join(
            family.render() for family in result.skipped_families
        )
    processes = result.orphan_candidates if args.dry_run else result.reaped_processes
    if processes:
        verb = "would reap" if args.dry_run else "reaped"
        inspection += (
            f"; {verb} {len(processes)} process(es) left running by a finished dispatch: "
            + ", ".join(str(pid) for pid in processes[:MAX_INSPECTED_PATHS])
        )
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
