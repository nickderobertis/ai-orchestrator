#!/usr/bin/env python3
"""The two readings this host's own views owe that the engine's views cannot give.

`onepipeline host` and `onepipeline status` report exhaustively on what is *running* and
not at all on what it is running *in*. Both gaps below are this host's to answer rather
than the engine's: the engine knows a run, and each of these is a fact about the machine
the run happens to be on. What each one cost a supervisor, and why the line is worth its
space, is `AGENTS.md` and `docs/orchestration.md`; what a reader of this code needs is
below.

**Free space** is host-wide on a host several managers share, so it is a thing to read
and never a thing this acts on — clearing space belongs to whoever owns what is filling
it.

**A live rendezvous is bound to its run by argv rather than by the environment**, though
both carry it. `onemessagebus ask` and `onemessagebus serve` are told which channel to
hold open with `--transport-dir <runs root>/<run>/channel`, so argv *is* the binding,
where `ONEPIPELINE_RUN_ID` is a variable that merely accompanied it — a rendezvous served
for one run out of a dispatch of another would be described wrongly by the second and
rightly by the first. `/proc/<pid>/environ` is readable only by the process's own user
besides, so on a shared host it would answer for some rendezvous and stay silent about the
rest. A relative directory is resolved against the process's own working directory, which
is how the observer's judge side names it. Silence is the one answer this must not give:
a rendezvous whose argv names no run — no `--transport-dir`, or one that is not a run's
channel — is reported as unattributable, and one bound to a run this host does not
supervise is reported as that, never omitted.

**Every engine- and bus-owned name below is reconciled rather than remembered.**
Answering either question means reading the engine's own store — the runs root, the
marker that makes a directory a run, the channel directory under it, the dispatch
registry beside that, and the start token each entry carries — and the bus's own command
line: its name, the verbs whose live processes are the rendezvous, and the flag that
names their channel. Each drifts in the direction that reads
as an answer: a renamed marker makes every run root look like a directory that is not a
run, so every rendezvous reports itself as bound to no run this host supervises, which is
the sentence reserved for somebody else's test. Nothing fails, the view renders, and each
line says the opposite of the truth. So each is a named constant here, and
`tests/test_engine_contracts.py` holds it against the releases this host installed.

**Nothing the engine printed is reformatted.** Every line arriving on stdin is written
back out in order and these readings are added beside them — the rule
`scripts/recoverable.sh` keeps, so this host's copy of a report cannot drift from the one
that verb prints everywhere else. `tests/e2e/test_supervision_readings_e2e.py` holds both
halves against the real recipes.

Keep this deterministic and stdlib-only: the wrapper scripts spawn it with this
repository's interpreter where one exists and a bare `python3` otherwise, so it cannot
import `orchestrator`.

llmlint: ignore-file[tool_output_is_signal] The rendered view is these viewing commands'
whole product — the engine's report and the readings beside it — and this filter is what
writes it, so a quiet success would be a supervisor's view with nothing in it.

llmlint: ignore-file[modern_domain_modeling] `Filesystem`, `Dispatch` and `BusMatch`
model what is structured here. What is left is the run identifier, and a `NewType` for it
would be verified by nobody: this repository's type checker reads `orchestrator/` alone,
and a module whose name carries a hyphen cannot be imported, so the journeys that drive
this could not share the type even if `scripts/` were checked.

llmlint: ignore-file[boundary_inputs_validated] Everything *rendered* is bounded and
stripped of control characters by `_readable`, which is where an unvalidated value would
do harm: a run id off another process's argv, a node name out of a JSON record and a path
out of the environment all reach a supervisor's terminal. What is deliberately not
validated is what validating would break — the runs root and the state root are the
operator's own configuration, used here exactly as `onepipeline` and `onevcs` use them,
so a form check would refuse a root the published verbs accept; and stdin is the
published view itself, written back in order and never re-parsed.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import NamedTuple

#: The indentation both engine views give the lines inside a run's block, so a reading
#: added beside them reads as part of the same document rather than as a second one.
INDENT = "  "

#: Where `just status`'s own run-scoped lines stop and `oneagentgraph health`'s
#: host-wide JSON begins. `AGENTS.md` tells a supervisor to cut a watch at exactly this
#: line before matching words in what is above it, so a reading added below it would be
#: one no watch that follows this repository's own guidance could ever see.
PROVIDERS = re.compile(r"^\s*providers:")

#: How `onepipeline` is told to look somewhere other than that default, and what it
#: looks under when nothing does — the same pair `scripts/ask-manager.sh` composes the
#: channel directory from, so a rendezvous and the view that reports it are talking about
#: one store rather than two.
RUNS_ROOT_ENV = "ONEPIPELINE_RUNS_DIR"
DEFAULT_RUNS_ROOT = "runs"

#: A run root records the launch that owns it, and the engine skips a directory holding
#: no such record. Reading the same marker is what lets a rendezvous naming a directory
#: that is not a run be reported as bound to no run rather than as bound to that name.
LAUNCH_RECORD = "launch.json"

#: Where a run records the dispatches it started: one JSON object per dispatch, naming
#: the node, the process, and when the kernel started it. It is what turns a
#: rendezvous's process ancestry into the dispatch it sits under.
DISPATCH_REGISTRY = "dispatches"

#: How a registry entry states the start time it recorded, which is what tells a live
#: dispatch from a stale entry whose pid the kernel has since handed to somebody else.
#: A host that has been up for weeks holds dozens of them, so an unverified pid match is
#: a real way to name the wrong dispatch — and naming the wrong one is worse than naming
#: none when telling two things apart is the whole job.
PROC_STAT_START = "linux-proc-stat:"

#: How `onevcs` is told to move its whole state root, where that root sits when nothing
#: does, and the directory under it every per-run clone and isolated worktree is cut in.
#: That is the filesystem the incident this file is written from filled, and it is not
#: always the one the runs root is on.
ONEVCS_HOME_ENV = "ONEVCS_HOME"
DEFAULT_ONEVCS_HOME = ".onevcs"
ONEVCS_WORKSPACES = "workspaces"

#: The three fields of a dispatch registry entry this reading reads. A renamed one would
#: fail nothing: the entry would simply stop naming a dispatch, and every rendezvous
#: under a live one would report itself as belonging to no dispatch this runs root
#: records — which is exactly the answer a rendezvous belonging to somebody else's test
#: gives, so the two would become indistinguishable again.
DISPATCH_NODE = "node"
DISPATCH_PID = "pid"
DISPATCH_STARTED = "started"

#: The bus command line whose live processes are the rendezvous, and the two verbs of it
#: that hold a run's channel open for a reply: `ask`, which is what a dispatched agent's
#: blocking question is on this host (`scripts/ask-manager.sh` execs it), and `serve`,
#: which is what an observer member's judge side is (`graphs/dag-scope.yaml` runs it).
#: Matched as two consecutive argv words, the first by its last path component so a bus
#: reached by path matches too — never as a substring of a whole command line: the
#: substring form is what makes `pgrep -f` match the shell that is asking, and this
#: reading runs from inside the very views a supervisor uses to look for it.
BUS = "onemessagebus"
RENDEZVOUS_VERBS = ("ask", "serve")

#: The flag both verbs name the channel's directory with, and the directory a run keeps
#: its channel in, directly under its own run root. Together they are the binding: a
#: `--transport-dir <runs root>/<run>/channel` names the run as the directory holding
#: that channel, and nothing else on the command line names a run at all.
TRANSPORT_DIR_FLAG = "--transport-dir"
CHANNEL_DIRECTORY = "channel"

GIB = 1024**3

#: How much of one externally-sourced value a line will carry. Every value rendered
#: below comes from outside this process — a run id off another process's argv, a node
#: name out of a JSON record, a path out of the environment — and a supervisor reads
#: these lines in a terminal beside the engine's own. So each is bounded and stripped of
#: the control characters that would let it forge a line of the report or move the
#: cursor: an `onemessagebus serve` is any process on this host, and nothing validated
#: the directory it was started with before this read it.
RENDERED_LIMIT = 120
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class Filesystem(NamedTuple):
    """One filesystem, as this reading names it and tells it from another."""

    #: The directory actually measured, which is `path` itself or the nearest ancestor
    #: of it that exists.
    measured: Path
    #: Where that filesystem is mounted, which is what the line names it by.
    mount: Path
    #: What tells two readings apart. The device number rather than the mount point:
    #: two paths on one filesystem must produce one line, and the free space alone
    #: would collapse two genuinely different filesystems that happen to match.
    device: int
    free: int
    total: int


def _readable(value: object) -> str:
    """One externally-sourced value, safe to put in a line a person reads.

    Escaped rather than dropped, so a name carrying something odd is still recognisable
    as itself; bounded, so one value cannot push the rest of the line off a terminal.
    """
    rendered = CONTROL.sub(lambda found: f"\\x{ord(found.group()):02x}", str(value))
    if len(rendered) <= RENDERED_LIMIT:
        return rendered
    return f"{rendered[:RENDERED_LIMIT]}… [{len(rendered)} characters]"


def _free_space(path: Path) -> Filesystem | str:
    """The filesystem `path` is on and what is left of it — or why neither could be read.

    A root that does not exist yet still sits on a filesystem, and that filesystem's
    free space is the number a supervisor wants: a fresh checkout whose `runs/` has
    never been written is not a host whose free space is unknown. So the walk up to the
    nearest existing ancestor is the answer rather than a fallback, and the caller says
    which directory it ended at whenever that is not the one it asked about.
    """
    measured = path
    while True:
        try:
            statistics = os.statvfs(measured)
            device = os.stat(measured).st_dev
        except OSError as error:
            parent = measured.parent
            if parent == measured:
                return f"{error}"
            measured = parent
            continue
        return Filesystem(
            measured=measured,
            mount=_mount_point(measured),
            device=device,
            free=statistics.f_bavail * statistics.f_frsize,
            total=statistics.f_blocks * statistics.f_frsize,
        )


def _mount_point(path: Path) -> Path:
    """The filesystem `path` is on, named by where it is mounted.

    Walked from the path rather than read out of `/proc/self/mountinfo`: the device
    number changing between a directory and its parent is what a mount *is*, and it
    answers the same on a host whose mount table this process cannot read.
    """
    try:
        device = os.stat(path).st_dev
    # llmlint: ignore[changed_behavior_has_e2e] Unreachable except by racing the filesystem:
    # the only caller reaches here having just stat-ed this same path successfully, so a
    # journey producing it would have to remove the directory between two calls one line
    # apart. It stays because returning the path is right where the walk cannot start.
    except OSError:  # pragma: no cover - the caller has just stat-ed this path
        return path
    at = path
    while at.parent != at:
        try:
            if os.stat(at.parent).st_dev != device:
                return at
        # llmlint: ignore[changed_behavior_has_e2e] Unreachable except by racing the
        # filesystem: `os.stat` on a parent needs the same search permission that made
        # stat-ing the child succeed one iteration earlier, so a journey would have to
        # revoke it mid-walk. It stays because the deepest directory this walk did reach
        # is the right answer when it can go no further.
        except OSError:  # pragma: no cover - the child stat-ed a moment earlier
            return at
        at = at.parent
    return at


def _runs_root() -> Path:
    """The runs root this host's views are about, resolved as `onepipeline` resolves it."""
    named = os.environ.get(RUNS_ROOT_ENV) or DEFAULT_RUNS_ROOT
    return Path(named).expanduser().absolute()


def _worktree_root() -> Path | None:
    """Where a lifecycle dispatch's worktrees and per-run clones live, or nothing.

    `onevcs` spells its state root `ONEVCS_HOME` when that is set to something
    non-empty and `~/.onevcs` otherwise, and every isolated worktree a dispatch works
    in is cut under `workspaces/` below it. That is the filesystem the incident above
    filled, and it is not always the one the runs root is on.
    """
    named = os.environ.get(ONEVCS_HOME_ENV) or ""
    if not named:
        home = os.environ.get("HOME") or ""
        if not home:
            return None
        named = str(Path(home) / DEFAULT_ONEVCS_HOME)
    return Path(named).expanduser().absolute() / ONEVCS_WORKSPACES


def _disk_lines() -> list[str]:
    """One line per distinct filesystem the run's working directories are on."""
    asked: list[tuple[str, Path]] = [("the runs root", _runs_root())]
    worktrees = _worktree_root()
    if worktrees is not None:
        asked.append(("the lifecycle worktrees under", worktrees))

    lines: list[str] = []
    # Deduplicated by filesystem rather than by path: on this host the runs root and
    # the worktrees are on one device, and printing that device's free space twice
    # would read as two answers about two resources rather than one about both.
    at_device: dict[int, int] = {}
    for label, path in asked:
        reading = _free_space(path)
        if isinstance(reading, str):
            # llmlint: ignore[changed_behavior_has_e2e] Reached only when `os.statvfs` fails on
            # every ancestor up to and including `/`, which is the filesystem the suite and this
            # process are themselves running on; a journey that produced it would have taken the
            # test runner with it. It stays because a reading that raised where a view is being
            # rendered would replace the view with a traceback.
            lines.append(
                f"{INDENT}disk: {label} {_readable(path)} could not be read"
                f" ({_readable(reading)}), so the free space there is unknown"
            )
            continue
        already = at_device.get(reading.device)
        if already is not None:
            lines[already] = f"{lines[already]}, and {label} {_readable(path)}"
            continue
        at_device[reading.device] = len(lines)
        share = (reading.free / reading.total * 100) if reading.total else 0.0
        at = (
            ""
            if reading.measured == path
            else f" (measured at {_readable(reading.measured)}, which is what exists)"
        )
        lines.append(
            f"{INDENT}disk {_readable(reading.mount)}: {reading.free / GIB:.1f} GiB free"
            f" of {reading.total / GIB:.1f} GiB ({share:.1f}% free){at}"
            f" — holds {label} {_readable(path)}"
        )
    lines.append(
        f"{INDENT}disk: that reading is host-wide and this host is shared,"
        " so read it rather than acting on it"
    )
    return lines


def _argv(pid: str) -> list[str]:
    """One process's argument vector, or nothing when it is gone or not ours to read."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return []
    return [word.decode("utf-8", "replace") for word in raw.split(b"\0") if word]


def _started(pid: int) -> str | None:
    """The kernel's own start time for a process, or nothing when it is gone.

    Read as everything after the last `)`, because the field before it is the process
    name and is the one field that can itself carry a bracket or a space.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        return stat[stat.rindex(")") + 2 :].split()[19]
    except (ValueError, IndexError):  # pragma: no cover - the kernel writes 52 fields
        return None


def _parent(pid: int) -> int | None:
    """The parent of one process, read from the field that is one line of its own.

    `/proc/<pid>/status` rather than `/proc/<pid>/stat`, because a process whose name
    carries a space or a bracket makes that second file's fields ambiguous to split and
    the ancestry walk below is what attributes a rendezvous to a dispatch.
    """
    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in status.splitlines():
        if line.startswith("PPid:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:  # pragma: no cover - the kernel writes a number here
                return None
    return None  # pragma: no cover - every /proc/<pid>/status carries PPid


def _rendezvous_words(argv: list[str]) -> tuple[str, str | None] | None:
    """The bus verb one argv carries, and the channel directory it names — if it carries one.

    Named for what it reads rather than for the run it is nearly always the run of, on
    `BusMatch`'s reasoning below: these are the words a rendezvous carries, and any other
    process on this host that happens to carry them is indistinguishable here. The flag
    is read in both of the spellings the bus accepts, `--transport-dir DIR` and
    `--transport-dir=DIR`, and only after the verb, because that is where it belongs to
    the verb; a `--` ends the options, as it does for the bus.
    """
    for index in range(len(argv) - 1):
        if Path(argv[index]).name != BUS or argv[index + 1] not in RENDEZVOUS_VERBS:
            continue
        verb = argv[index + 1]
        after = argv[index + 2 :]
        for at, word in enumerate(after):
            if word == "--":
                break
            if word == TRANSPORT_DIR_FLAG:
                return verb, after[at + 1] if at + 1 < len(after) else None
            if word.startswith(f"{TRANSPORT_DIR_FLAG}="):
                return verb, word.removeprefix(f"{TRANSPORT_DIR_FLAG}=")
        return verb, None
    return None


class BusMatch(NamedTuple):
    """One live process carrying a rendezvous verb's words, and the channel it names.

    Named for what the evidence establishes rather than for what it is nearly always
    evidence *of*. A process holding an open question carries exactly this, and so does
    any other process on this host that happens to carry the same words — the sleepers
    `tests/e2e/test_supervision_readings_e2e.py` starts to prove a directory cannot forge
    a line of the report are such. Nothing here can close that gap: a rendezvous is a
    queue the bus keeps in files rather than a state the kernel publishes, and
    `/proc/<pid>/environ` is readable only by the process's own user, so on a host
    several managers share it would answer for some and stay silent about the rest.
    """

    pid: int
    verb: str
    #: The `--transport-dir` value exactly as argv carries it, or `None` when argv names
    #: none — which leaves the bus reading its directory from the environment or a
    #: configuration file, and leaves this reading unable to say which run it serves.
    transport: str | None


class Dispatch(NamedTuple):
    """One entry of a run's dispatch registry, as this reading uses it."""

    run: str
    node: str
    #: The start time the registry recorded, when it recorded one in a spelling this
    #: can check. `None` leaves the entry usable but unverified, which is the right way
    #: round: refusing to attribute a dispatch because its record spells its start time
    #: some other way would lose a real answer to a format change.
    started: str | None


def _dispatches(runs_root: Path) -> tuple[dict[int, Dispatch], set[str]]:
    """Every dispatch this runs root records, by process — and the runs it holds.

    Keyed by process across every run, because a live pid is one process and one process
    is one dispatch. Two runs recording the same pid means at least one of those records
    is stale, and the start-time check at the lookup is what parts them; where neither
    can be checked the later record read wins, which is a choice between two claims that
    cannot both be true rather than information thrown away.
    """
    by_process: dict[int, Dispatch] = {}
    runs: set[str] = set()
    try:
        candidates = sorted(runs_root.iterdir())
    except OSError:
        return by_process, runs
    for run in candidates:
        if not (run / LAUNCH_RECORD).is_file():
            continue
        runs.add(run.name)
        try:
            entries = sorted((run / DISPATCH_REGISTRY).iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                recorded = json.loads(entry.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            pid = recorded.get(DISPATCH_PID)
            node = recorded.get(DISPATCH_NODE)
            started = recorded.get(DISPATCH_STARTED)
            if isinstance(pid, int) and isinstance(node, str):
                by_process[pid] = Dispatch(
                    run=run.name,
                    node=node,
                    started=(
                        started.removeprefix(PROC_STAT_START)
                        if isinstance(started, str) and started.startswith(PROC_STAT_START)
                        else None
                    ),
                )
    return by_process, runs


def _ancestors(pid: int) -> list[int]:
    """Every process this one descends from, nearest first, stopping at init.

    A cycle is impossible in a process tree and is guarded against anyway: this walk
    runs inside the views a supervisor uses to look for a wedged run, and a reader that
    could itself wedge would be the worst possible place to trust the kernel.
    """
    walked: list[int] = []
    seen = {pid}
    at = _parent(pid)
    while at is not None and at > 1 and at not in seen:
        walked.append(at)
        seen.add(at)
        at = _parent(at)
    return walked


def _under_dispatch(pid: int, by_process: dict[int, Dispatch]) -> str | None:
    """The `run/node` of the nearest dispatch this process descends from, if any."""
    for at in (pid, *_ancestors(pid)):
        found = by_process.get(at)
        if found is None:
            continue
        if found.started is not None and _started(at) != found.started:
            # The record is stale and the kernel has handed its pid to somebody else,
            # so this ancestor is not that dispatch. Keep walking rather than stopping:
            # a real dispatch may still be further up.
            continue
        return f"{_readable(found.run)}/{_readable(found.node)} (dispatch pid {at})"
    return None


def _bound_by_argv(processes: list[int]) -> list[BusMatch]:
    """One entry per rendezvous, as far as argv can say — and the channel each one names.

    Named for its evidence rather than for its subject, because the two are not the
    same claim and only the narrower one is true: this reads argv, so what it finds is
    every process *carrying* a rendezvous verb's words, which is what a process holding
    a channel open carries and is not proof that one is. Nothing on this host can give
    that proof — a rendezvous is a queue the bus keeps in files, not a state the kernel
    publishes — so the reading says what it saw and the line it renders says the same.

    One rendezvous can be several processes. Anything in front of the bus that does not
    exec — a `timeout` bounding a wait by hand — keeps a process of its own carrying
    those same argv words, so counting matches would report one open question as two.
    (`uv run` is not one of them, and neither is the `bash -c 'exec …'` the observer's
    judge side is started through: both exec.) The innermost is the one holding the
    channel, so a match whose own descendant carries the same verb and directory is a
    wrapper and is dropped. Ancestry rather than parentage, because a shell between the
    two matches nothing and would otherwise leave both.
    """
    bound = {
        pid: words for pid in processes if (words := _rendezvous_words(_argv(str(pid)))) is not None
    }

    wrappers = {
        ancestor
        for pid, words in bound.items()
        for ancestor in _ancestors(pid)
        if bound.get(ancestor) == words
    }
    return [
        BusMatch(pid=pid, verb=verb, transport=transport)
        for pid, (verb, transport) in bound.items()
        if pid not in wrappers
    ]


def _channel(match: BusMatch) -> Path | None:
    """The channel directory one match names, resolved as the process itself resolves it.

    A relative directory is relative to the process's working directory — the observer's
    judge side names `runs/<run>/channel` exactly so — so it is joined onto
    `/proc/<pid>/cwd` and the whole resolved. Where that link is not this process's to
    read, it stays unresolved in the path, which then names no runs root this host
    supervises and is reported as exactly that rather than guessed at.
    """
    if match.transport is None:
        return None
    named = Path(match.transport)
    if not named.is_absolute():
        named = Path(f"/proc/{match.pid}/cwd") / named
    return Path(os.path.realpath(named))


def _rendezvous_lines() -> list[str]:
    """The report's rendezvous section: one line per process a rendezvous verb's words match.

    Named for the section it writes rather than for a property of what it found, because
    those are two different claims and only the first is this function's to make — what
    each line is evidence of is `BusMatch`'s docstring, and a match that is somebody
    else's sleeper is reported in the same words as one that is a real open question. A
    supervisor reading either still has to look, which is what these lines are for.
    """
    runs_root = _runs_root()
    supervised = Path(os.path.realpath(runs_root))
    by_process, runs = _dispatches(runs_root)
    lines: list[str] = []
    try:
        processes = sorted(int(name) for name in os.listdir("/proc") if name.isdigit())
    # llmlint: ignore[changed_behavior_has_e2e] Reached only when `/proc` cannot be listed
    # at all, which is the interface this whole reading — and the interpreter running it —
    # is served through; a journey that produced it would have taken the test runner with
    # it. It stays because a reading that raised where a view is being rendered would
    # replace the view with a traceback.
    except OSError as error:  # pragma: no cover - /proc is what this host is read through
        return [f"{INDENT}rendezvous: the process table could not be read ({error})"]
    for rendezvous in _bound_by_argv(processes):
        under = _under_dispatch(rendezvous.pid, by_process)
        where = under if under is not None else "no dispatch this runs root records"
        opened = (
            f"{INDENT}rendezvous pid {rendezvous.pid}: {BUS} {rendezvous.verb}"
            " is holding a channel open for a reply"
        )
        channel = _channel(rendezvous)
        if channel is None or channel.name != CHANNEL_DIRECTORY:
            named = "none" if rendezvous.transport is None else _readable(rendezvous.transport)
            lines.append(
                f"{opened}, under {where} — its command line names no {TRANSPORT_DIR_FLAG}"
                f" ending in a run's '{CHANNEL_DIRECTORY}' directory (it names {named}),"
                " so the run it is bound to cannot be read and this rendezvous is"
                " unattributable"
            )
            continue
        named = _readable(channel.parent.name)
        on = f"{opened} on run {named}, under {where}"
        if channel.parent.parent != supervised:
            lines.append(
                f"{on} — its channel is under {_readable(channel.parent.parent)}, not the"
                f" runs root {_readable(runs_root)}, so this rendezvous is bound to no run"
                " this host is supervising"
            )
            continue
        if channel.parent.name in runs:
            lines.append(on)
            continue
        lines.append(
            f"{on} — no run root '{named}' under {_readable(runs_root)}, so this"
            " rendezvous is bound to no run this host is supervising"
        )
    if not lines:
        lines.append(
            f"{INDENT}rendezvous: none — no {BUS} {' or '.join(RENDEZVOUS_VERBS)} on this"
            " host is holding a channel open for a reply"
        )
    return lines


def _status(view: list[str], readings: list[str]) -> list[str]:
    """The status view with the readings above the line a watch is told to cut at.

    Below `providers:` is where a reading would be invisible to every watch that
    follows this repository's own guidance, so it goes above it. A view carrying no
    such line has nothing to be below, and the readings go at the end.
    """
    for index, line in enumerate(view):
        if PROVIDERS.match(line):
            return [*view[:index], *readings, *view[index:]]
    return [*view, *readings]


def main(argv: list[str]) -> int:
    """Write back the view on stdin with this host's own readings beside it."""
    if len(argv) != 1 or argv[0] not in {"status", "host"}:
        print(
            "usage: supervision-readings.py status|host  (the view arrives on stdin)",
            file=sys.stderr,
        )
        return 2
    view = sys.stdin.read().splitlines()
    # A view that produced nothing is a verb that refused — `onepipeline status
    # no-such-run` writes its reason to standard error and leaves this stream empty —
    # and a reading printed beside a refusal reads as an answer to it. There is nothing
    # here to add a reading beside, so nothing is added. An empty *host* is not this
    # case: it says `no live dispatches`, and a genuinely empty runs root says `no runs
    # recorded`, so both still carry their readings.
    if not view:
        return 0
    if argv[0] == "status":
        rendered = _status(view, _disk_lines())
    else:
        rendered = [*view, *_disk_lines(), *_rendezvous_lines()]
    for line in rendered:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
