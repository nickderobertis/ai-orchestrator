"""Process wrapper and Linux activity probes for dispatch liveness supervision."""

# llmlint: ignore-file[changed_behavior_has_e2e] the real wrapper sanitization journey is
# tests/e2e/test_dispatch_e2e.py::test_bypass_dispatch_scopes_llmlint_wrapper_to_harness_repository.

from __future__ import annotations

import os
import signal
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import NewType

from .coordination import ProcessStart, process_start_identity

ProcessId = NewType("ProcessId", int)


@dataclass(frozen=True)
class ProcessActivity:
    """A process-tree identity and its cumulative observable work counters."""

    pids: tuple[ProcessId, ...]
    cpu_ticks: int
    io_bytes: int


@dataclass(frozen=True)
class ProcessStat:
    """The process relationship and cumulative work read from procfs."""

    parent_pid: ProcessId
    process_group: ProcessId
    state: str
    cpu_ticks: int
    io_bytes: int


def _parse_stat(raw_stat: str, io_fields: list[str]) -> ProcessStat | None:
    closing_delimiter = raw_stat.rfind(")")
    if closing_delimiter < 0:
        return None
    # The parenthesized comm field may itself contain spaces or parentheses.
    fields = raw_stat[closing_delimiter + 2 :].split()
    if len(fields) < 13:
        return None
    try:
        return ProcessStat(
            parent_pid=ProcessId(int(fields[1])),
            process_group=ProcessId(int(fields[2])),
            state=fields[0],
            cpu_ticks=int(fields[11]) + int(fields[12]),
            io_bytes=sum(
                int(line.partition(":")[2])
                for line in io_fields
                if line.startswith(("rchar:", "wchar:", "read_bytes:", "write_bytes:"))
            ),
        )
    except ValueError:
        return None


def _stat(pid: ProcessId) -> ProcessStat | None:
    try:
        raw_stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        io_fields = Path(f"/proc/{pid}/io").read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    return _parse_stat(raw_stat, io_fields)


def _process_ids() -> list[ProcessId]:
    """Every pid procfs currently lists."""
    return [ProcessId(int(entry.name)) for entry in Path("/proc").iterdir() if entry.name.isdigit()]


def process_group_is_running(group_id: ProcessId) -> bool:
    """Whether any process in ``group_id`` is still executing.

    ``killpg(group, 0)`` is not this answer: it counts zombies, and anything that
    made itself a child subreaper keeps its orphans' exit statuses until it collects
    them. A group whose remaining members have all exited is finished, and a caller
    waiting on ``killpg`` would wait for a group that is never going to empty.
    """
    return any(
        record.process_group == group_id and record.state != "Z"
        for pid in _process_ids()
        if (record := _stat(pid)) is not None
    )


def process_group_of(pid: ProcessId) -> ProcessId | None:
    """The process group ``pid`` currently belongs to, or ``None`` if it is gone."""
    record = _stat(pid)
    return record.process_group if record is not None else None


def descendants(root_pid: ProcessId) -> tuple[ProcessId, ...]:
    """Every live process below ``root_pid``, by parentage alone.

    Deliberately cheaper than `process_activity`: parentage lives in
    ``/proc/<pid>/stat``, so a caller that only needs the shape of the tree should
    not also open ``/proc/<pid>/io`` for every process on the host. That second
    read doubles the syscalls of a full walk, and a walk that repeats on a timer
    is competing for the same interpreter as whatever it is watching.
    """
    return descendants_of(frozenset({root_pid}))


def descendants_of(root_pids: frozenset[ProcessId]) -> tuple[ProcessId, ...]:
    """Every live process below any of ``root_pids``, excluding the roots themselves.

    One ``/proc`` scan for the whole set rather than one per root: a caller holding
    a run's several recorded owners — and everything it has already seen under them —
    re-walks on a short timer while it terminates them, and repeating the scan per
    pid would multiply that cost by the size of the tree it is watching.
    """
    parents: dict[ProcessId, ProcessId] = {}
    for pid in _process_ids():
        try:
            raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        except OSError:
            continue
        fields = raw[raw.rfind(")") + 2 :].split()
        if len(fields) >= 2 and fields[0] != "Z":
            parents[pid] = ProcessId(int(fields[1]))
    found = set(root_pids)
    changed = True
    while changed:
        changed = False
        for pid, parent in parents.items():
            if parent in found and pid not in found:
                found.add(pid)
                changed = True
    return tuple(sorted(found - set(root_pids)))


def process_activity(root_pid: ProcessId) -> ProcessActivity:
    """Return live descendants and cumulative CPU/I/O for ``root_pid``."""
    records: dict[ProcessId, ProcessStat] = {}
    for pid in _process_ids():
        if (record := _stat(pid)) is not None and record.state != "Z":
            records[pid] = record
    selected = {root_pid} if root_pid in records else set()
    changed = True
    while changed:
        changed = False
        for pid, record in records.items():
            if record.parent_pid in selected and pid not in selected:
                selected.add(pid)
                changed = True
    return ProcessActivity(
        tuple(sorted(selected)),
        sum(records[pid].cpu_ticks for pid in selected),
        sum(records[pid].io_bytes for pid in selected),
    )


#: How long a signalled process may take to shut down before it is killed outright.
TERMINATION_GRACE = 0.05
#: How often that grace period asks whether it is still owed.
_TERMINATION_POLL = 0.005


@dataclass(frozen=True)
class ProcessIdentity:
    """A pid paired with the kernel start token that makes it more than a number.

    A bare pid names a slot. Between one signal and the next its process can exit and
    the number can be handed to something unrelated, so anything that signals twice —
    every ``SIGTERM``-then-``SIGKILL`` pair here — has to be able to ask whether it is
    still addressing the same process. The start time the kernel stamped at ``exec``
    answers that, and unlike the dispatch environment stamp it is there for *every*
    process: a parentage-proven descendant that has since ``exec``ed something without
    the stamp still has this.
    """

    pid: ProcessId
    start: ProcessStart


def identify(pid: ProcessId) -> ProcessIdentity | None:
    """Name a live process in a way its successor cannot inherit; ``None`` if it is gone."""
    start = process_start_identity(pid)
    return None if start is None else ProcessIdentity(pid, start)


def _still_itself(identities: tuple[ProcessIdentity, ...]) -> tuple[ProcessIdentity, ...]:
    """Which of ``identities`` still name the very process they were taken from.

    A pid whose process has exited is dropped, and so is one whose number is now held
    by a successor — the two are indistinguishable from the number alone, which is the
    whole point. Zombies drop out here too, as they did when this asked only about
    liveness: they are exit statuses waiting to be collected, not processes to signal.
    """
    return tuple(
        identity
        for identity in identities
        if process_start_identity(identity.pid) == identity.start
    )


def _signal_the_same_processes(
    identities: tuple[ProcessIdentity, ...], sig: int
) -> tuple[ProcessIdentity, ...]:
    """Signal each identity that still names its own process; return those signalled.

    The check is per signal rather than per call, because the caller's *previous*
    signal is the most likely reason an identity has stopped matching.
    """
    delivered = []
    for identity in reversed(_still_itself(identities)):
        with suppress(PermissionError, ProcessLookupError):
            os.kill(identity.pid, sig)
            delivered.append(identity)
    return tuple(reversed(delivered))


def _await_identity_shutdown(identities: tuple[ProcessIdentity, ...]) -> None:
    """Give every signalled process its grace period, and not a moment more.

    ``kill`` returns as soon as the signal is queued, so the ``SIGKILL`` behind it has
    to be held back long enough for a harness that installs a shutdown handler to use
    it. Sleeping that period out unconditionally charges it to the overwhelmingly
    common case instead: a dispatch that finished on its own has no process left to be
    graceful toward, and every teardown on that path paid the full grace anyway. The
    ceiling is unchanged for anything actually still running — this only stops waiting
    once there is nothing left to wait for.
    """
    deadline = time.monotonic() + TERMINATION_GRACE
    remaining = _still_itself(identities)
    while remaining and time.monotonic() < deadline:
        time.sleep(_TERMINATION_POLL)
        remaining = _still_itself(remaining)


def terminate_tree(root_pid: ProcessId) -> None:
    """Best-effort termination of a stalled dispatch and all its descendants."""
    terminate_processes(tuple(process_activity(root_pid).pids))


def terminate_identified_processes(
    identities: tuple[ProcessIdentity, ...], *, externally_waited: tuple[ProcessId, ...] = ()
) -> tuple[ProcessIdentity, ...]:
    """Terminate exactly the processes these identities still name; return those killed.

    For a caller that proved ownership of a set some moments ago and must not have that
    proof quietly decay into a set of numbers: each identity is revalidated immediately
    before the ``SIGTERM`` and again before the ``SIGKILL``, so a process that exited on
    the first signal — the ordinary case for anything with a shutdown handler — is never
    sent a second one, and a successor that inherited its number is never sent a first.

    The returned identities are the ones that had to be escalated: still themselves
    after their grace period, and killed outright.
    """
    _signal_the_same_processes(identities, signal.SIGTERM)
    _await_identity_shutdown(identities)
    escalated = _signal_the_same_processes(identities, signal.SIGKILL)
    pids = tuple(identity.pid for identity in identities)
    _reap(pids, externally_waited)
    return escalated


def terminate_processes(
    pids: tuple[ProcessId, ...], *, externally_waited: tuple[ProcessId, ...] = ()
) -> None:
    """Best-effort termination of a previously observed worker process tree.

    Descendants are reparented as soon as their worker exits, so walking from the
    vanished root can no longer find them.  Callers retain the last affirmative
    tree sample and use it here to reap those now-orphaned processes.

    Identity is taken here, at the moment of the call, which is the best a caller
    holding only numbers can do. One that proved its set earlier should carry the
    identities from that proof and use `terminate_identified_processes`.
    """
    terminate_identified_processes(
        tuple(identity for pid in pids if (identity := identify(pid)) is not None),
        externally_waited=externally_waited,
    )


def _reap(pids: tuple[ProcessId, ...], externally_waited: tuple[ProcessId, ...]) -> None:
    """Collect the exit statuses of any of ``pids`` this process is entitled to.

    Unlike signalling, this needs no identity check: the kernel only lets a process
    wait for its own children, so a number that has moved on to a stranger answers
    ``ChildProcessError`` rather than touching them.
    """
    deadline = time.monotonic() + 1.5
    # asyncio's child watcher owns the direct subprocess it created. Calling
    # waitpid for that process here races the watcher and makes it fabricate
    # return code 255 after ChildProcessError, destroying the child's real status
    # and stderr. Still signal every recorded process, but leave those roots for
    # their registered waiter while reaping orphaned descendants ourselves.
    pending = set(pids).difference(externally_waited)
    while pending and time.monotonic() < deadline:
        for pid in tuple(pending):
            try:
                reaped, _ = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                if not Path(f"/proc/{pid}").exists():
                    pending.remove(pid)
            else:
                if reaped:
                    pending.remove(pid)
        if pending:
            time.sleep(0.01)


def _signal_process_group(group_id: ProcessId, sig: int) -> None:
    """Deliver one signal to whatever is in ``group_id`` at this instant."""
    with suppress(PermissionError, ProcessLookupError):
        os.killpg(group_id, sig)


def _await_group_shutdown(group_id: ProcessId) -> None:
    """The grace `_await_identity_shutdown` gives a set of identities, asked of a group.

    Its membership is the thing being terminated, so nothing here has a list to poll
    and `process_group_is_running` is the whole answer.
    """
    deadline = time.monotonic() + TERMINATION_GRACE
    while process_group_is_running(group_id) and time.monotonic() < deadline:
        time.sleep(_TERMINATION_POLL)


def terminate_proven_process_group(
    group_id: ProcessId, *, still_ours: Callable[[ProcessId], bool]
) -> None:
    """`SIGTERM` a group, and `SIGKILL` it only while the caller can still prove it.

    A group id is the pid of its leader, and the kernel reserves that number only for
    as long as some live process names the group — so a proof taken before the
    ``SIGTERM`` says nothing about the instant after it, when the members that
    reserved the number may all have exited. The second signal therefore asks again.

    Nothing is lost by refusing it: a group emptied by the ``SIGTERM`` has nothing
    left for a ``SIGKILL`` to reach, and a group that still holds a process the caller
    can prove is its own is still that caller's to kill. What is avoided is the
    reverse — insisting on a number whose reservation this caller has just released.

    Deliberately no reaping pass: enumerating a group's members *after* killing them
    re-derives a pid list from the same released number. A caller that must reap owns
    an exact set of pids for that, taken while its proof was live.
    """
    _signal_process_group(group_id, signal.SIGTERM)
    _await_group_shutdown(group_id)
    if still_ours(group_id):
        _signal_process_group(group_id, signal.SIGKILL)


def terminate_process_group(
    group_id: ProcessId, *, externally_waited: tuple[ProcessId, ...] = ()
) -> None:
    """Terminate and reap every process in a caller-owned process group.

    For a caller whose ownership of the number is not in question for the length of
    the call — `gitops` and `verify` each hold the group leader as their own live
    child throughout. A caller whose evidence its own signals can destroy wants
    `terminate_proven_process_group` instead.
    """
    _signal_process_group(group_id, signal.SIGTERM)
    _await_group_shutdown(group_id)
    _signal_process_group(group_id, signal.SIGKILL)
    members = [
        pid
        for pid in _process_ids()
        if (record := _stat(pid)) is not None and record.process_group == group_id
    ]
    terminate_processes(tuple(members), externally_waited=externally_waited)


def lead_process_group() -> None:
    """Make this process the leader of a process group of its own.

    Without this the whole dispatched tree sits in whatever group the supervisor was
    launched in, so ``terminate_process_group`` has no group to signal — the recorded
    pid is not a group id, and ``killpg`` answers ESRCH. What is left is walking
    ``/proc`` from the root, and that walk loses a descendant the moment its parent
    exits and it reparents away. A process group is the one handle the kernel keeps
    valid across that reparenting.

    ``setpgid`` rather than ``setsid``: this needs a signalling handle, not a new
    session, and leaving the session alone keeps the tree's controlling terminal — and
    so any tty-aware harness below it — exactly as it was. A process that already
    leads its group gets a harmless success, and anything the kernel refuses leaves
    the inherited group in place, which is no worse than the state this replaces.
    """
    with suppress(OSError):
        os.setpgid(0, 0)


#: Opt-in flag for `lead_process_group`. The wrapper is normally reached by exec from
#: a process spawned for it, but it is also called in-process by its own unit test,
#: and regrouping *that* process would move the whole test session out of its group.
#: Asking for the new group explicitly keeps the side effect where the caller wants it.
OWN_PROCESS_GROUP_FLAG = "--own-process-group"


def main(argv: list[str] | None = None) -> int:
    """Record this stable pre-exec pid, then replace the wrapper with onejudge."""
    args = list(sys.argv[1:] if argv is None else argv)
    own_group = bool(args) and args[0] == OWN_PROCESS_GROUP_FLAG
    if own_group:
        args.pop(0)
    if len(args) < 2:
        print(
            f"usage: watchdog [{OWN_PROCESS_GROUP_FLAG}] PID_FILE COMMAND [ARG ...]",
            file=sys.stderr,
        )
        return 2
    pid_file = Path(args.pop(0))
    if own_group:
        lead_process_group()
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    if os.environ.pop("ORCHESTRATOR_WATCHDOG_UNSET_LLMLINT", None) == "1":
        os.environ.pop("LLMLINT_ONEHARNESS_BIN", None)
    os.execvpe(args[0], args, os.environ)
    return 127  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover - real subprocess boundary
    raise SystemExit(main())
