"""What one complete pre-push gate leaves on the host, recorded by the gate run itself.

``.githooks/pre-push`` runs the gate through ``record_local_direct_gate``
(``scripts/lock-timeout.sh``), which, for a push the hook names, runs it through
``python -m orchestrator.gate_impact run``. That run gives the gate a temporary root of
its own, watches it while the gate runs, and records three facts once it has exited:
how large that root and the scratch registered to it grew, what is left in them, and
which processes descended from the gate outlived it. The root ``budgets.yaml`` holds
every push to them through ``python -m orchestrator.gate_impact report``, which reads
that record back and never measures anything itself. This module is the one source of
the record's shape, the command line and the scratch registry: its writer and its
reader are both here, over the one :class:`Record` declaration.

**The measurement, and its exceptions.** Every other description of these figures
points here rather than restating them.

- *Attribution is the root and the registered scratch, and nothing else.* The gate is
  handed ``TMPDIR`` (``TMP``, ``TEMP``) as ``<root>/tmp``, and
  ``ORCHESTRATOR_GATE_SCRATCH`` as ``<root>/scratch``, the registry a writer that must
  write elsewhere — a socket directory a Unix address could not reach under the root,
  say — names its path in, through :func:`register_scratch` or the ``register``
  command. Nothing else on the host is read, counted or removed: a file a tool writes
  under a literal ``/tmp`` or under ``$HOME`` and registers nowhere is not seen.
- *Peak temporary disk is sampled.* The allocated size, ``du -s --block-size=1``'s
  figure, of ``<root>/tmp`` plus every registered path that exists is taken every
  :data:`SAMPLE_SECONDS` while the gate runs and once as it exits; samples never
  overlap, so a walk longer than the interval delays the next one. An allocation
  created and removed between two samples is not seen.
- *A leaked process is any descendant alive the moment the gate exits.* The recorder is
  a child subreaper, so every orphan the gate leaves is re-parented to it, and the
  snapshot is its whole descendant tree at any depth, zombies excluded, taken the
  moment the gate's own process exits. One that exits a moment later — within the
  :data:`SETTLE_SECONDS` the recorder then waits — is still counted, as ``exited``.
- *Leftovers are counted before any signal.* What is left in ``<root>/tmp`` and in each
  registered path is counted before a leaked process is signalled, so one that removes
  its files when stopped cannot erase them from the record.
- *Registered scratch is removed only after it is counted*, with the root itself, so the
  host is left clean whatever the verdict.
- *A stopped gate is waited for only so long.* A stop the recorder receives — its
  caller's exit, which the kernel reports to it as TERM, among them — is passed to the
  gate, which then has :data:`CANCEL_SECONDS` to exit before the recorder's whole tree is
  sent TERM and then KILL; the record of such a run is of the gate as it was stopped.
- *A zero-byte ``uv-<16 hex>.lock`` at the root's top level is not a leftover*, by
  ruling: it is uv's own environment lock, which ``uv run`` leaves by design. It is
  counted apart, and a non-empty one is a leftover like any other file.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import dataclasses
import enum
import json
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import Any, NewType, TextIO, assert_never

from orchestrator.redaction import redact, secret_values

__all__ = [
    "PUSH_VARIABLE",
    "SCRATCH_VARIABLE",
    "PushId",
    "Leaked",
    "LeftoverEntry",
    "PeakEntry",
    "Record",
    "main",
    "push_file",
    "push_id",
    "register_scratch",
]

#: The record's own version, written into it and required of it.
SCHEMA_VERSION = 1
#: How often the root is sampled while the gate runs, in seconds.
SAMPLE_SECONDS = 5
#: The longest the recorder waits for leaked processes to exit before TERM, in seconds.
SETTLE_SECONDS = 10
#: How long TERM is given before every member still alive is sent KILL, in seconds.
TERM_SECONDS = 5
#: How long a gate is given to stop on its own once the recorder has passed it a stop —
#: its caller's exit among them — before the recorder stops its whole tree, in seconds.
CANCEL_SECONDS = 10
#: How many of the largest top-level entries the record keeps at the peak sample.
PEAK_TOP = 10
#: How many of the largest leftover top-level entries the record keeps.
LEFTOVER_TOP = 20

#: The registry directory every writer outside ``TMPDIR`` names its scratch in.
SCRATCH_VARIABLE = "ORCHESTRATOR_GATE_SCRATCH"
#: The push the hook is gating, which ``report`` reads the record for.
PUSH_VARIABLE = "ORCHESTRATOR_GATE_PUSH"
#: The file onebudgetspec names for a ``reported`` budget's result.
RESULT_VARIABLE = "ONEBUDGETSPEC_RESULT"
#: Where the gate's temporary files go.
TEMP_VARIABLES = ("TMPDIR", "TMP", "TEMP")
#: Scratch locations a dispatch hands its pushes, which writers here prefer over
#: ``TMPDIR``; unset for the gate so they cannot write outside its root.
UNSET_FOR_COMMAND = ("ONEPIPELINE_NODE_SCRATCH_DIR", "PYTEST_DEBUG_TEMPROOT")
#: The prefix of every gate root, kept short: a Unix socket address under the root has
#: 108 bytes in all.
ROOT_PREFIX = "g-"
#: The file name the record is written under, beside ``scripts/lock-timeout.sh``'s.
PUSH_FILE_NAME = "gate-host-impact-push.json"
#: A push name as the hook mints it.
PUSH_NAME = re.compile(r"[A-Za-z0-9._-]+")
#: A push the hook named, as one checked against :data:`PUSH_NAME`.
PushId = NewType("PushId", str)


def push_id(named: str) -> PushId | None:
    """``named`` as a push, or None when it is not one the hook could have minted."""
    return PushId(named) if PUSH_NAME.fullmatch(named) else None


#: uv's environment lock, left at the root's top level by every ``uv run``.
UV_LOCK = re.compile(r"uv-[0-9a-f]{16}\.lock")


class StoppedBy(enum.StrEnum):
    """How a leaked process ended once the gate had exited."""

    #: On its own, within the settle wait.
    EXITED = "exited"
    #: Once sent TERM.
    TERM = "TERM"
    #: Only once sent KILL.
    KILL = "KILL"


class Figure(enum.StrEnum):
    """The three figures ``report`` reads, by the name its command line takes."""

    LEFTOVERS = "leftovers"
    PEAK_TEMP_DISK = "peak-temp-disk"
    LEAKED_PROCESSES = "leaked-processes"


PR_SET_CHILD_SUBREAPER = 36
PR_SET_PDEATHSIG = 1
#: How often the recorder looks again while it waits on processes, in seconds.
POLL_SECONDS = 0.1
#: Signals the recorder passes on to the gate rather than dying of them mid-record.
FORWARDED_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


@dataclass(frozen=True)
class PeakEntry:
    """One of the largest top-level entries at the peak sample."""

    path: str
    bytes: int


@dataclass(frozen=True)
class LeftoverEntry:
    """One leftover top-level entry, with every entry beneath it."""

    path: str
    entries: int
    bytes: int


@dataclass(frozen=True)
class Leaked:
    """One process descended from the gate and alive the moment it exited."""

    pid: int
    ppid: int
    command: str
    stopped_by: StoppedBy


@dataclass(frozen=True)
class Record:
    """One gate run's effect on the host: the one declaration of the record's keys."""

    schema_version: int
    #: Empty for a run that named no push.
    push: PushId
    exit_status: int
    sample_seconds: float
    samples: int
    longest_walk_seconds: float
    peak_bytes: int
    peak_top: list[PeakEntry]
    leftover_entries: int
    leftover_bytes: int
    excluded_uv_locks: int
    leftover_top: list[LeftoverEntry]
    settle_seconds: float
    leaked: list[Leaked]

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), indent=1) + "\n"

    @classmethod
    def from_json(cls, text: str) -> Record:
        """The record ``text`` holds, or :class:`ValueError` saying what is wrong with it."""
        try:
            document = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError(f"it is not JSON ({error})") from error
        values = _fields(cls, document, "the record")
        if values["schema_version"] != SCHEMA_VERSION:
            raise ValueError(
                f"it is schema_version {values['schema_version']}, not {SCHEMA_VERSION}"
            )
        values["peak_top"] = [_entry(PeakEntry, item, "peak_top") for item in values["peak_top"]]
        values["leftover_top"] = [
            _entry(LeftoverEntry, item, "leftover_top") for item in values["leftover_top"]
        ]
        values["leaked"] = [_entry(Leaked, item, "leaked") for item in values["leaked"]]
        return cls(**values)


#: The JSON type each annotation in the record's declaration admits.
_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "int": (int,),
    "float": (int, float),
    "str": (str,),
    "StoppedBy": (str,),
    "PushId": (str,),
}


def _fields(cls: type, document: object, what: str) -> dict[str, Any]:
    """``document``'s values for ``cls``'s fields, checked against the declaration.

    Every number is a count, a size, a duration or a status, so one that is negative or
    not finite is refused rather than read as within a budget.
    """
    if not isinstance(document, dict):
        raise ValueError(f"{what} is not a JSON object")
    declared = {item.name: str(item.type) for item in dataclasses.fields(cls)}
    if set(document) != set(declared):
        missing = sorted(set(declared) - set(document))
        extra = sorted(set(document) - set(declared))
        raise ValueError(f"{what} lacks {missing or 'nothing'} and carries {extra or 'nothing'}")
    values = dict(document)
    for name, annotation in declared.items():
        value = values[name]
        admitted = _JSON_TYPES.get(annotation, (list,))
        if isinstance(value, bool) or not isinstance(value, admitted):
            raise ValueError(f"{what}'s {name} is {value!r}, which is not {annotation}")
        if isinstance(value, int | float) and not (math.isfinite(value) and value >= 0):
            raise ValueError(f"{what}'s {name} is {value!r}, which is no measurement")
        if annotation == "StoppedBy":
            try:
                values[name] = StoppedBy(str(value))
            except ValueError as error:
                raise ValueError(
                    f"{what}'s {name} is {value!r}, which is no way to stop"
                ) from error
    return values


def _entry[T](cls: type[T], item: object, what: str) -> T:
    return cls(**_fields(cls, item, f"an entry of {what}"))


def push_file(environ: dict[str, str] | None = None) -> Path | None:
    """Where the record for a push is kept, or None when there is nowhere host-local.

    ``scripts/lock-timeout.sh``'s ``local_direct_gate_state_file`` rule: an absolute
    ``XDG_STATE_HOME``, else ``$HOME/.local/state``, which must itself be absolute.
    ``tests/test_gate_impact.py`` holds the two to one answer.
    """
    source = os.environ if environ is None else environ
    state = source.get("XDG_STATE_HOME", "")
    if not state.startswith("/"):
        state = source.get("HOME", "") + "/.local/state"
    if not state.startswith("/"):
        return None
    return Path(state) / "ai-orchestrator" / PUSH_FILE_NAME


def root_parent(environ: dict[str, str] | None = None) -> Path:
    """Where a recorder makes its root.

    Inside another recorder's command — ``ORCHESTRATOR_GATE_SCRATCH`` set — inside that
    one's ``TMPDIR``, so the outer recorder counts it. Otherwise under the host's cache,
    outside every registered checkout, and short enough for a socket address under it.
    """
    source = os.environ if environ is None else environ
    if source.get(SCRATCH_VARIABLE) and source.get("TMPDIR", "").startswith("/"):
        return Path(source["TMPDIR"])
    cache = source.get("XDG_CACHE_HOME", "")
    if not cache.startswith("/"):
        cache = source.get("HOME", "") + "/.cache"
    # A relative one would put the gate's root under whatever directory the hook ran in.
    if not cache.startswith("/") or cache == "/.cache":
        raise ValueError("neither XDG_CACHE_HOME nor HOME is an absolute path")
    return Path(cache) / "ai-orchestrator" / "gate-tmp"


def register_scratch(path: str | os.PathLike[str]) -> Path | None:
    """Register ``path`` with the recorder running this process, if one is.

    The path is then counted at every sample and as a leftover, and removed once
    counted. Does nothing, and answers None, when no recorder is running.
    """
    registry = os.environ.get(SCRATCH_VARIABLE)
    if not registry:
        return None
    if not registry.startswith("/"):
        # Relative, it would be a directory under whatever this process runs in, which
        # no recorder reads.
        raise ValueError(f"{SCRATCH_VARIABLE}={registry} is not an absolute path")
    absolute = os.path.abspath(path)
    if "\n" in absolute or "\0" in absolute:
        # One entry is one path ending at its newline, so it could not be read back whole.
        raise ValueError(f"{absolute!r} holds a newline or a NUL")
    if (shared := _shared(Path(absolute))) is not None:
        raise ValueError(f"{absolute} is {shared}, never one gate's own scratch")
    entry = Path(registry) / f"{os.getpid()}-{time.monotonic_ns()}"
    with entry.open("x", encoding="utf-8") as handle:
        handle.write(absolute + "\n")
    return entry


class Unmeasured(Exception):
    """What the gate left could not be counted, and why."""


def registered_paths(scratch: Path, *, strict: bool = False) -> list[Path]:
    """Every path registered in ``scratch``, in registration order, each once.

    A sample reads the registry while writers may still be writing to it, so it passes
    over what it cannot read yet. The count once the gate has exited is ``strict``: a
    registry it cannot list, or an entry it cannot read or that names no absolute path,
    is :class:`Unmeasured`, since attribution missing a registration is no count at all.
    """
    found: list[Path] = []
    try:
        entries = sorted(scratch.iterdir(), key=lambda entry: entry.stat().st_mtime_ns)
    except OSError as error:
        if strict:
            raise Unmeasured(
                f"the scratch registry {scratch} could not be listed ({error})"
            ) from error
        return found
    for entry in entries:
        try:
            value = entry.read_text(encoding="utf-8").removesuffix("\n")
        except (OSError, UnicodeDecodeError) as error:
            if strict:
                raise Unmeasured(f"the registration {entry} could not be read ({error})") from error
            continue
        if not value.startswith("/") or "\0" in value or "\n" in value:
            if strict:
                raise Unmeasured(f"the registration {entry} names {value!r}, no absolute path")
            continue
        if Path(value) not in found:
            found.append(Path(value))
    return found


def _shared(path: Path) -> str | None:
    """Why ``path`` cannot be one gate's own scratch, or None when it can be.

    A directory directly under ``/`` such as ``/tmp``, a sticky world-writable directory
    every user writes into, and a path another user owns are each shared by more than the
    gate that registered them, so neither counting nor removing them is the gate's.
    """
    real = Path(os.path.realpath(path))
    if len(real.parts) < 3:
        return "a top-level directory"
    try:
        info = real.stat()
    except OSError:
        return None
    if stat.S_ISDIR(info.st_mode) and info.st_mode & stat.S_ISVTX and info.st_mode & stat.S_IWOTH:
        return "a directory every user writes into"
    if info.st_uid != os.getuid():
        return "another user's"
    return None


def _counted(paths: list[Path], root: Path, tmp: Path) -> list[Path]:
    """The registered paths to count and remove: none inside the root's ``tmp`` (counted
    there already), none inside another one, never one holding the root or the user's
    home, and never one :func:`_shared` names, such as ``/tmp`` itself.

    Each is judged where it really is, its ``..`` and every link above it resolved, so a
    registration spelled to look elsewhere cannot reach either."""
    home = Path(os.path.realpath(os.environ.get("HOME") or "/"))
    real_root, real_tmp = Path(os.path.realpath(root)), Path(os.path.realpath(tmp))
    kept: list[Path] = []
    for named in paths:
        path = Path(os.path.normpath(named))
        real = Path(os.path.realpath(path))
        if (
            real.is_relative_to(real_tmp)
            or real_root.is_relative_to(real)
            or home.is_relative_to(real)
            or _shared(path) is not None
        ):
            continue
        kept.append(path)
    return [path for path in kept if not any(path != o and path.is_relative_to(o) for o in kept)]


@dataclass(frozen=True)
class Proc:
    """A process as ``/proc`` shows it: its parent, state and start time."""

    pid: int
    ppid: int
    state: str
    started: int


def _stat(pid: int) -> Proc | None:
    # One open and one read, rather than a Path's text read: a walk makes one of these
    # per process on the host, and on a busy host that is thousands of them.
    try:
        descriptor = os.open(f"/proc/{pid}/stat", os.O_RDONLY)
        try:
            raw = os.read(descriptor, 4096).decode(errors="replace")
        finally:
            os.close(descriptor)
    except OSError:
        return None
    fields = raw[raw.rfind(")") + 2 :].split()
    return Proc(pid, int(fields[1]), fields[0], int(fields[19]))


def _table(after: int) -> dict[int, Proc]:
    """Every process on the host, those with a pid above ``after`` read first.

    The kernel hands out pids in ascending order until it wraps, so ``after``'s
    descendants are read before the thousands of unrelated processes a busy host runs,
    any of which a short-lived descendant could otherwise exit behind. The order changes
    only how soon each is read, never which are.
    """
    pids = sorted(
        (int(name) for name in os.listdir("/proc") if name.isdigit()),
        key=lambda pid: (pid <= after, pid),
    )
    table: dict[int, Proc] = {}
    for pid in pids:
        if (proc := _stat(pid)) is not None:
            table[proc.pid] = proc
    return table


class Tree:
    """The recorder's own descendant tree, less the recorder's own sampler commands."""

    def __init__(self) -> None:
        self.root = os.getpid()
        # Re-entrant: the SIGCHLD handler reaps in whichever frame of the main thread it
        # interrupts, which may be one already holding it.
        self._lock = threading.RLock()
        self._excluded: set[int] = set()
        #: Children a waiter of their own reads the status of, such as the gate's process.
        self._waited: set[int] = set()

    def wait_on(self, pid: int) -> None:
        """Leave ``pid``'s status to the waiter that started it, until :meth:`waited`."""
        with self._lock:
            self._waited.add(pid)

    def waited(self, pid: int) -> None:
        with self._lock:
            self._waited.discard(pid)

    def include(self, pid: int) -> None:
        with self._lock:
            self._excluded.discard(pid)

    def communicate(self, process: subprocess.Popen[bytes]) -> tuple[bytes, bytes]:
        """Read one of the recorder's own commands to its end, then count it again.

        An orphan that exited while that command's unread status stood ahead of it was
        left by the reap its exit set off, so it is reaped here.
        """
        try:
            return process.communicate()
        finally:
            self.include(process.pid)
            self.reap()

    def walk(self) -> dict[int, Proc]:
        """Every live (non-zombie) process under the recorder at any depth."""
        table = _table(self.root)
        children: dict[int, list[int]] = defaultdict(list)
        for proc in table.values():
            children[proc.ppid].append(proc.pid)
        with self._lock:
            excluded = set(self._excluded)
        found: dict[int, Proc] = {}
        stack = list(children[self.root])
        while stack:
            pid = stack.pop()
            if pid in excluded or pid in found:
                continue
            stack.extend(children[pid])
            if table[pid].state not in ("Z", "X"):
                found[pid] = table[pid]
        return found

    def reap(self) -> None:
        """Reap every zombie child of the recorder that nothing here waits on.

        The kernel is asked which child has exited, never ``/proc``, so reaping costs the
        same however many unrelated processes the host runs. It is asked without reaping,
        and a child whose own waiter reads its status is left to it and ends the pass:
        the kernel may answer that child again until it is reaped, so its waiter reaps what
        stood behind it once it has read that status. Under the lock a command is started
        and excluded under, so a sampler command that exits at once is never reaped from
        under the waiter that reads its status.
        """
        with self._lock:
            protected = self._waited | self._excluded
            while True:
                try:
                    exited = os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                except ChildProcessError:
                    return
                if exited is None or exited.si_pid in protected:
                    return
                # A zombie its own waiter reaped between the question and here is gone.
                with contextlib.suppress(ChildProcessError):
                    os.waitpid(exited.si_pid, os.WNOHANG)

    def start_excluded(self, command: list[str]) -> subprocess.Popen[bytes]:
        """Start one of the recorder's own commands, excluded from the tree from its start."""
        with self._lock:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self._excluded.add(process.pid)
        return process


def _alive(proc: Proc) -> bool:
    now = _stat(proc.pid)
    return now is not None and now.started == proc.started and now.state not in ("Z", "X")


def _command(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        environ = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        raw, environ = b"", b""
    line = " ".join(part.decode(errors="replace") for part in raw.split(b"\0") if part)
    own = dict(
        item.decode(errors="replace").partition("=")[::2]
        for item in environ.split(b"\0")
        if b"=" in item
    )
    secrets = sorted(
        set(secret_values()) | set(secret_values(own)), key=lambda item: (-len(item[1]), item[0])
    )
    return redact(line, secrets)


def _signal(procs: dict[int, Proc], number: int) -> None:
    for proc in procs.values():
        if _alive(proc):
            with contextlib.suppress(ProcessLookupError):
                os.kill(proc.pid, number)


def _wait_until(done: Callable[[], bool], seconds: float, tree: Tree) -> None:
    deadline = time.monotonic() + seconds
    while True:
        tree.reap()
        if done() or time.monotonic() >= deadline:
            return
        time.sleep(POLL_SECONDS)


def stop_leaked(tree: Tree, leaked: dict[int, Proc]) -> dict[int, StoppedBy]:
    """Stop the whole tree, and say how each leaked process ended.

    It waits up to :data:`SETTLE_SECONDS` for the tree to empty on its own, sends TERM
    to every member still alive, then re-walks it — children started meanwhile
    included — and sends KILL to every member still alive :data:`TERM_SECONDS` later.
    """
    ended: dict[int, StoppedBy] = {}
    _wait_until(lambda: not tree.walk(), SETTLE_SECONDS, tree)
    for pid, proc in leaked.items():
        if not _alive(proc):
            ended[pid] = StoppedBy.EXITED
    _signal(tree.walk(), signal.SIGTERM)
    _wait_until(lambda: not tree.walk(), TERM_SECONDS, tree)
    for pid, proc in leaked.items():
        if pid not in ended and not _alive(proc):
            ended[pid] = StoppedBy.TERM
    _signal(tree.walk(), signal.SIGKILL)
    _wait_until(lambda: not tree.walk(), TERM_SECONDS, tree)
    for pid in leaked:
        ended.setdefault(pid, StoppedBy.KILL)
    return ended


@dataclass
class Sampling:
    """The samples taken so far: how many, the longest walk, and the peak."""

    samples: int = 0
    longest_walk: float = 0.0
    peak_bytes: int = 0
    peak_top: list[PeakEntry] = field(default_factory=list)
    #: Walks that could not read everything, and what the last of them said: each of
    #: those samples is a floor rather than the whole figure.
    incomplete: int = 0
    why: str = ""


@dataclass(frozen=True)
class Walk:
    """One ``du`` walk: the allocated total, each top-level entry's size, and what ``du``
    said if it could not read everything, which makes the total a floor."""

    total: int
    top: list[PeakEntry]
    incomplete: str | None = None


def _du(tmp: Path, registered: list[Path], tree: Tree) -> Walk:
    """Walk ``tmp`` and every registered path that exists, once."""
    existing = [path for path in [tmp, *registered] if os.path.lexists(path)]
    if not existing:
        return Walk(0, [])
    process = tree.start_excluded(
        ["du", "-0", "-a", "-d", "1", "--block-size=1", "--", *map(str, existing)]
    )
    output, errors = tree.communicate(process)
    total = 0
    top: list[PeakEntry] = []
    arguments = {str(path) for path in existing}
    prefix = f"{tmp}/"
    for line in output.decode(errors="replace").split("\0"):
        size, _, path = line.partition("\t")
        if not size.isdigit():
            continue
        if path in arguments:
            total += int(size)
            if path != str(tmp):
                top.append(PeakEntry(path, int(size)))
        elif path.startswith(prefix) and "/" not in path[len(prefix) :]:
            top.append(PeakEntry(path[len(prefix) :], int(size)))
    top.sort(key=lambda entry: (-entry.bytes, entry.path))
    said = errors.decode(errors="replace").strip().splitlines()
    incomplete = (
        (said[-1] if said else f"exit {process.returncode}") if process.returncode else None
    )
    return Walk(total, top[:PEAK_TOP], incomplete)


class Sampler(threading.Thread):
    """Samples the root every :data:`SAMPLE_SECONDS`, never two walks at once."""

    def __init__(self, root: Path, tree: Tree) -> None:
        super().__init__(daemon=True)
        self.root = root
        self.tree = tree
        self.sampling = Sampling()
        self.halt = threading.Event()

    def sample(self) -> None:
        started = time.monotonic()
        tmp = self.root / "tmp"
        registered = _counted(registered_paths(self.root / "scratch"), self.root, tmp)
        walked = _du(tmp, registered, self.tree)
        walk = time.monotonic() - started
        self.sampling.samples += 1
        self.sampling.longest_walk = max(self.sampling.longest_walk, walk)
        if walked.incomplete is not None:
            self.sampling.incomplete += 1
            self.sampling.why = walked.incomplete
        if walked.total > self.sampling.peak_bytes or self.sampling.samples == 1:
            self.sampling.peak_bytes, self.sampling.peak_top = walked.total, walked.top

    def run(self) -> None:
        # Every SIGCHLD goes to the main thread, whose handler reaps what the gate orphans.
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGCHLD})
        while not self.halt.is_set():
            started = time.monotonic()
            self.sample()
            self.halt.wait(max(0.0, SAMPLE_SECONDS - (time.monotonic() - started)))


@dataclass(frozen=True)
class Allocation:
    """How many entries a path and everything beneath it hold, and their allocated size."""

    entries: int
    bytes: int


def _allocated(path: Path) -> Allocation:
    """``path`` and everything beneath it, links unfollowed."""
    try:
        own = path.lstat()
    except OSError:
        return Allocation(0, 0)
    entries, size = 1, own.st_blocks * 512
    if stat.S_ISDIR(own.st_mode):
        for directory, names, files in os.walk(path):
            for name in names + files:
                # One removed between the listing and here is no longer left.
                with contextlib.suppress(OSError):
                    size += os.lstat(os.path.join(directory, name)).st_blocks * 512
                    entries += 1
    return Allocation(entries, size)


@dataclass(frozen=True)
class Leftovers:
    entries: int
    bytes: int
    excluded_uv_locks: int
    top: list[LeftoverEntry]


def count_leftovers(tmp: Path, registered: list[Path]) -> Leftovers:
    """What is left in ``tmp`` and in each registered path, by top-level entry."""
    top: list[LeftoverEntry] = []
    uv_locks = 0
    try:
        children = sorted(tmp.iterdir())
    except OSError as error:
        raise Unmeasured(
            f"the gate's temporary directory {tmp} could not be listed ({error})"
        ) from error
    for child in children:
        info = child.lstat()
        if UV_LOCK.fullmatch(child.name) and stat.S_ISREG(info.st_mode) and info.st_size == 0:
            uv_locks += 1
            continue
        allocated = _allocated(child)
        top.append(LeftoverEntry(child.name, allocated.entries, allocated.bytes))
    for path in registered:
        allocated = _allocated(path)
        if allocated.entries:
            top.append(LeftoverEntry(str(path), allocated.entries, allocated.bytes))
    top.sort(key=lambda entry: (-entry.bytes, -entry.entries, entry.path))
    return Leftovers(
        sum(entry.entries for entry in top),
        sum(entry.bytes for entry in top),
        uv_locks,
        top[:LEFTOVER_TOP],
    )


def remove(path: Path) -> bool:
    """Remove ``path`` whole, first giving back owner permissions on every directory a
    test left unreadable. Answers whether it is gone."""
    if not os.path.lexists(path):
        return True
    if path.is_dir() and not path.is_symlink():
        _open_up(path)
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
    return not os.path.lexists(path)


def _open_up(directory: Path) -> None:
    try:
        directory.chmod(directory.lstat().st_mode | stat.S_IRWXU)
        children = list(directory.iterdir())
    except OSError:
        return
    for child in children:
        if child.is_dir() and not child.is_symlink():
            _open_up(child)


def _subreaper() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    # Linux grants this to every process, so the refusal below states the contract only.
    granted = libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0
    if not granted:  # pragma: no cover
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_CHILD_SUBREAPER) failed")


def _status(returncode: int) -> int:
    return 128 - returncode if returncode < 0 else returncode


def _command_environment(root: Path) -> dict[str, str]:
    environment = dict(os.environ)
    for name in UNSET_FOR_COMMAND:
        environment.pop(name, None)
    for name in TEMP_VARIABLES:
        environment[name] = str(root / "tmp")
    environment[SCRATCH_VARIABLE] = str(root / "scratch")
    return environment


class Forwarding:
    """Passes each signal the recorder receives on to the gate, holding any that arrives
    before the gate has started until it has."""

    def __init__(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None
        self.pending: list[int] = []
        #: Set by the first stop, and by the gate's own exit, for :class:`Cancelling`.
        self.wake = threading.Event()

    def __call__(self, number: int, _frame: FrameType | None) -> None:
        self.wake.set()
        if self.process is None:
            self.pending.append(number)
            return
        # One that arrives after the gate was reaped has nothing left to reach.
        with contextlib.suppress(ProcessLookupError):
            self.process.send_signal(number)

    def attach(self, process: subprocess.Popen[bytes]) -> None:
        self.process = process
        for number in self.pending:
            self(number, None)
        self.pending.clear()


class Cancelling(threading.Thread):
    """Bounds a stop the gate does not obey.

    Once a stop has been passed to the gate, it is given :data:`CANCEL_SECONDS` to exit;
    past that every member of the recorder's tree — the gate's own process included — is
    sent TERM, then KILL every :data:`TERM_SECONDS` until the gate's process has exited,
    so the recorder goes on to record and clean up rather than waiting on it forever.
    """

    def __init__(self, tree: Tree, forward: Forwarding) -> None:
        super().__init__(daemon=True)
        self.tree = tree
        self.forward = forward
        self.done = threading.Event()

    def finish(self) -> None:
        self.done.set()
        self.forward.wake.set()

    def run(self) -> None:
        # Every SIGCHLD goes to the main thread, whose handler reaps what the gate orphans.
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGCHLD})
        self.forward.wake.wait()
        if self.done.is_set() or self.done.wait(CANCEL_SECONDS):
            return
        number = signal.SIGTERM
        while not self.done.is_set():
            _signal(self.tree.walk(), number)
            number = signal.SIGKILL
            self.done.wait(TERM_SECONDS)


def _watch_caller(caller: int, forward: Forwarding) -> None:
    """Have the recorder sent TERM the moment the process that started it exits.

    A recorder whose caller is gone — a push given up, a hook or a test killed — would
    otherwise run its gate on, re-parented to init, for as long as the gate lasts; with
    TERM it passes the stop to the gate, :class:`Cancelling` bounds how long a gate that
    ignores it is waited for, and it records and cleans up as it always does. A caller
    already gone before the request is made is answered the same way, at once.
    """
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
    if os.getppid() != caller:
        forward(signal.SIGTERM, None)


def _write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` whole, then move it into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.{os.getpid()}")
    staging.write_text(text, encoding="utf-8")
    os.replace(staging, path)


def _make_root() -> Path:
    parent = root_parent()
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix=ROOT_PREFIX, dir=parent))
    (root / "tmp").mkdir()
    (root / "scratch").mkdir()
    return root


@dataclass(frozen=True)
class Exited:
    """What was true the moment the gate exited, before anything was signalled."""

    status: int
    leaked: dict[int, Proc]
    commands: dict[int, str]
    sampling: Sampling


def _run_gate(command: Sequence[str], root: Path, tree: Tree, caller: int) -> Exited:
    """Run the gate, sampling the root, and take what is owed the moment it exits."""
    sampler = Sampler(root, tree)
    forward = Forwarding()
    previous = {number: signal.signal(number, forward) for number in FORWARDED_SIGNALS}
    try:
        _watch_caller(caller, forward)
        process = subprocess.Popen(list(command), env=_command_environment(root))
    except OSError:
        for number, handler in previous.items():
            signal.signal(number, handler)
        raise
    tree.wait_on(process.pid)
    forward.attach(process)
    cancelling = Cancelling(tree, forward)

    # An orphan re-parented here is reaped the moment it exits, as init would reap it:
    # a zombie left until the next look reads as alive to anything that asks, and a
    # test killing a process and then asking whether it is gone would be answered wrong.
    def reap(_number: int, _frame: FrameType | None) -> None:
        tree.reap()

    previous[signal.SIGCHLD] = signal.signal(signal.SIGCHLD, reap)
    try:
        sampler.start()
        cancelling.start()
        # Waited on without being reaped, so the snapshot below is taken the moment the
        # gate's own process exits, with its pid still its own.
        os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
        cancelling.finish()
        cancelling.join()
        leaked = tree.walk()
        commands = {pid: _command(pid) for pid in leaked}
        sampler.halt.set()
        sampler.join()
        # The last walk is this thread's own, so nothing may reap it from under it here.
        signal.signal(signal.SIGCHLD, previous.pop(signal.SIGCHLD))
        sampler.sample()
        status = _status(process.wait())
        tree.waited(process.pid)
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    return Exited(status, leaked, commands, sampler.sampling)


def run(command: Sequence[str], *, push: PushId | None, summary: Path | None, err: TextIO) -> int:
    """Run ``command`` as the gate, record its effect on the host, and return its status."""
    caller = os.getppid()
    try:
        root = _make_root()
    except ValueError as error:
        print(
            f"gate-impact: {error}, so there is nowhere host-local for the gate's root and "
            "the gate was not run; export an absolute one, then push again",
            file=err,
        )
        return 1
    except OSError as error:
        print(
            f"gate-impact: no root for the gate could be made under {root_parent()} ({error}), "
            "so the gate was not run; make that a writable directory with free space, "
            "then push again",
            file=err,
        )
        return 1
    _subreaper()
    tree = Tree()
    try:
        exited = _run_gate(command, root, tree, caller)
    except OSError as error:
        remove(root)
        print(f"gate-impact: the gate {list(command)} could not be started ({error})", file=err)
        return 127
    tmp = root / "tmp"
    try:
        registered = _counted(registered_paths(root / "scratch", strict=True), root, tmp)
        leftovers: Leftovers | None = count_leftovers(tmp, registered)
    except Unmeasured as failure:
        # Still stopped and cleaned up as far as can be read, but recorded as nothing: a
        # missing record refuses the push's budgets as unmeasured, where a partial count
        # would admit it.
        registered, leftovers = _counted(registered_paths(root / "scratch"), root, tmp), None
        print(
            f"gate-impact: {failure}, so what the gate left could not be counted and no "
            "record was written; its budgets refuse the push as unmeasured. Find what in "
            "the gate removes or locks its own temporary directory or registry, and fix it",
            file=err,
        )
    ended = stop_leaked(tree, exited.leaked)
    written: list[Path] = []
    if leftovers is not None:
        written = _record(
            Record(
                schema_version=SCHEMA_VERSION,
                push=push or PushId(""),
                exit_status=exited.status,
                sample_seconds=SAMPLE_SECONDS,
                samples=exited.sampling.samples,
                longest_walk_seconds=round(exited.sampling.longest_walk, 3),
                peak_bytes=exited.sampling.peak_bytes,
                peak_top=exited.sampling.peak_top,
                leftover_entries=leftovers.entries,
                leftover_bytes=leftovers.bytes,
                excluded_uv_locks=leftovers.excluded_uv_locks,
                leftover_top=leftovers.top,
                settle_seconds=SETTLE_SECONDS,
                leaked=[
                    Leaked(pid, proc.ppid, exited.commands[pid], ended[pid])
                    for pid, proc in sorted(exited.leaked.items())
                ],
            ),
            push=push,
            summary=summary,
            err=err,
        )
    for path in registered:
        if not remove(path):
            print(f"gate-impact: the registered scratch {path} could not be removed", file=err)
    if not remove(root):
        print(f"gate-impact: the gate's root {root} could not be removed", file=err)
    sampling = exited.sampling
    if sampling.incomplete:
        print(
            f"gate-impact: {sampling.incomplete} of {sampling.samples} sample walk(s) "
            f"could not read everything (du said: {sampling.why}), so the peak is "
            "a floor; an unreadable directory the gate left is what usually causes it",
            file=err,
        )
    left = (
        "leftovers unmeasured"
        if leftovers is None
        else f"{leftovers.entries} leftover entr{'y' if leftovers.entries == 1 else 'ies'} "
        f"({leftovers.bytes} B, {leftovers.excluded_uv_locks} uv lock(s) not counted)"
    )
    print(
        f"gate-impact: exit {exited.status}; peak {sampling.peak_bytes} B over "
        f"{sampling.samples} sample(s), longest walk {sampling.longest_walk:.3g}s; {left}; "
        f"{len(exited.leaked)} leaked process(es){'; recorded at ' if written else ''}"
        f"{', '.join(map(str, written))}",
        file=err,
    )
    return exited.status


def _record(
    record: Record, *, push: PushId | None, summary: Path | None, err: TextIO
) -> list[Path]:
    """Write the record where it was asked for, and answer where it was written."""
    destinations: list[Path] = []
    if push:
        destination = push_file()
        if destination is None:
            print(
                "gate-impact: neither XDG_STATE_HOME nor HOME is an absolute path, so this "
                "push's gate impact could not be recorded and its budgets cannot be checked; "
                "export an absolute one and push again",
                file=err,
            )
        else:
            destinations.append(destination)
    if summary is not None:
        destinations.append(summary)
    written: list[Path] = []
    for destination in destinations:
        try:
            _write(destination, record.to_json())
        except OSError as error:
            print(
                f"gate-impact: the record could not be written to {destination} ({error}); "
                f"make {destination.parent} a writable directory, then run it again",
                file=err,
            )
        else:
            written.append(destination)
    return written


#: What every refusal for a missing or unusable record ends with.
AGAIN = (
    "the hook records it as it runs the gate, so repair what kept it from recording "
    "(its gate-impact line above says) and push again"
)


class Refused(Exception):
    """A measurement ``report`` cannot answer, with the remedy."""


@dataclass(frozen=True)
class Measurement:
    """One figure as onebudgetspec's result reports it."""

    value: float
    detail: str


def _measure(figure: Figure, record: Record) -> Measurement:
    """The value and detail ``figure`` reports from ``record``."""
    match figure:
        case Figure.LEFTOVERS:
            largest = "; ".join(
                f"{entry.path} ({entry.entries} entries, {entry.bytes} B)"
                for entry in record.leftover_top
            )
            return Measurement(
                record.leftover_entries,
                f"{record.leftover_bytes} B left; {record.excluded_uv_locks} zero-byte uv "
                f"lock(s) not counted; largest: {largest or 'none'}",
            )
        case Figure.PEAK_TEMP_DISK:
            largest = "; ".join(f"{entry.path} ({entry.bytes} B)" for entry in record.peak_top)
            return Measurement(
                record.peak_bytes / 1024**3,
                f"{record.peak_bytes} B at the peak of {record.samples} sample(s) every "
                f"{record.sample_seconds:g}s, longest walk {record.longest_walk_seconds:g}s; "
                f"largest at the peak: {largest or 'none'}",
            )
        case Figure.LEAKED_PROCESSES:
            processes = "; ".join(
                f"pid {process.pid} (parent {process.ppid}, stopped by {process.stopped_by}): "
                f"{process.command[:300]}"
                for process in record.leaked
            )
            return Measurement(
                len(record.leaked),
                f"alive when the gate exited, waited up to {record.settle_seconds:g}s: "
                f"{processes or 'none'}",
            )
        # The type checker holds the cases above to every figure, so none reaches here.
        case _:  # pragma: no cover
            assert_never(figure)


def report(figure: Figure) -> None:
    """Write ``figure`` of this push's record as onebudgetspec's result, or refuse."""
    named = os.environ.get(PUSH_VARIABLE, "")
    if not named:
        raise Refused(
            f"no push is named in {PUSH_VARIABLE}; this budget is measured only by "
            ".githooks/pre-push, from the gate it has just run for that push, so push to measure it"
        )
    push = push_id(named)
    if push is None:
        raise Refused(
            f"{PUSH_VARIABLE}={named} is not a push the hook names, which is letters, digits, "
            "dots, dashes and underscores; push to measure this budget rather than naming "
            "a push by hand"
        )
    result = os.environ.get(RESULT_VARIABLE, "")
    if not result:
        raise Refused(
            f"{RESULT_VARIABLE} names no result file; run this as the budget's command, "
            "through onebudgetspec check"
        )
    path = push_file()
    if path is None:
        raise Refused(
            "neither XDG_STATE_HOME nor HOME is an absolute path, so there is nowhere this "
            "push's gate could have been recorded; export an absolute one and push again"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise Refused(
            f"no gate record could be read at {path} ({error.strerror}), so this push's "
            f"gate impact is unknown; {AGAIN}"
        ) from error
    except UnicodeDecodeError as error:
        raise Refused(f"the gate record at {path} is unusable: it is not UTF-8; {AGAIN}") from error
    try:
        record = Record.from_json(text)
    except ValueError as error:
        raise Refused(f"the gate record at {path} is unusable: {error}; {AGAIN}") from error
    if record.push != push:
        raise Refused(
            f"the gate record at {path} is for an earlier push ({record.push or 'none named'}), "
            f"not this one ({push}), so this push's gate impact is unknown; {AGAIN}"
        )
    measured = _measure(figure, record)
    try:
        Path(result).write_text(json.dumps(dataclasses.asdict(measured)) + "\n", encoding="utf-8")
    except OSError as error:
        raise Refused(
            f"the result could not be written to {result}, the file onebudgetspec named "
            f"({error.strerror}); check that its directory is writable and has free space, "
            "then push again"
        ) from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.gate_impact",
        description=__doc__.splitlines()[0] if __doc__ else None,
    )
    commands = parser.add_subparsers(dest="verb", required=True)
    running = commands.add_parser(
        "run", help="run a gate command and record its effect on the host"
    )
    running.add_argument("--push", help="the push the hook names; the record goes to its push file")
    running.add_argument("--summary", type=Path, help="also write the record to this file")
    running.add_argument("command", nargs=argparse.REMAINDER, help="-- COMMAND [ARG...]")
    reporting = commands.add_parser(
        "report", help="write one figure of this push's record as a budget result"
    )
    reporting.add_argument("figure", type=Figure, choices=list(Figure))
    registering = commands.add_parser("register", help="register scratch with the running recorder")
    registering.add_argument("path")
    return parser


def main(argv: Sequence[str] | None = None, err: TextIO | None = None) -> int:
    err = sys.stderr if err is None else err
    arguments = _parser().parse_args(argv)
    try:
        match arguments.verb:
            case "run":
                return _run_command(arguments, err)
            case "report":
                report(arguments.figure)
            case "register":
                _register(arguments.path)
            # The parser admits no other verb.
            case _:  # pragma: no cover
                assert_never(arguments.verb)
    except Refused as refusal:
        print(f"gate-impact: {refusal}", file=err)
        return 1
    return 0


def _register(path: str) -> None:
    try:
        register_scratch(path)
    except (OSError, ValueError) as error:
        raise Refused(
            f"{path} could not be registered in {os.environ.get(SCRATCH_VARIABLE)} "
            f"({getattr(error, 'strerror', None) or error}); the recorder that exported it "
            "has to be running for its registry to take a path"
        ) from error


def _run_command(arguments: argparse.Namespace, err: TextIO) -> int:
    command: list[str] = arguments.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        print("gate-impact: run needs a command after --", file=err)
        return 2
    push = None if arguments.push is None else push_id(arguments.push)
    if arguments.push is not None and push is None:
        print(
            f"gate-impact: --push {arguments.push} is not a push the hook names, which is "
            "letters, digits, dots, dashes and underscores",
            file=err,
        )
        return 2
    return run(command, push=push, summary=arguments.summary, err=err)


if __name__ == "__main__":
    sys.exit(main())
