"""Process wrapper and Linux activity probes for dispatch liveness supervision."""

# llmlint: ignore-file[changed_behavior_has_e2e] the real wrapper sanitization journey is
# tests/e2e/test_dispatch_e2e.py::test_bypass_dispatch_scopes_llmlint_wrapper_to_harness_repository.

from __future__ import annotations

import os
import signal
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import NewType

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


def process_activity(root_pid: ProcessId) -> ProcessActivity:
    """Return live descendants and cumulative CPU/I/O for ``root_pid``."""
    records: dict[ProcessId, ProcessStat] = {}
    for entry in Path("/proc").iterdir():
        pid = ProcessId(int(entry.name)) if entry.name.isdigit() else None
        if pid is not None and (record := _stat(pid)) is not None and record.state != "Z":
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


def terminate_tree(root_pid: ProcessId) -> None:
    """Best-effort termination of a stalled dispatch and all its descendants."""
    pids = tuple(reversed(process_activity(root_pid).pids))
    for pid in pids:
        with suppress(PermissionError, ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
    time.sleep(0.05)
    for pid in pids:
        with suppress(PermissionError, ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


def terminate_processes(pids: tuple[ProcessId, ...]) -> None:
    """Best-effort termination of a previously observed worker process tree.

    Descendants are reparented as soon as their worker exits, so walking from the
    vanished root can no longer find them.  Callers retain the last affirmative
    tree sample and use it here to reap those now-orphaned processes.
    """
    for pid in reversed(pids):
        with suppress(PermissionError, ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
    time.sleep(0.05)
    for pid in reversed(pids):
        with suppress(PermissionError, ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + 1.5
    pending = set(pids)
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


def terminate_process_group(group_id: ProcessId) -> None:
    """Terminate and reap every process in a dispatch-owned process group."""
    with suppress(PermissionError, ProcessLookupError):
        os.killpg(group_id, signal.SIGTERM)
    time.sleep(0.05)
    with suppress(PermissionError, ProcessLookupError):
        os.killpg(group_id, signal.SIGKILL)
    members: list[ProcessId] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = ProcessId(int(entry.name))
        record = _stat(pid)
        if record is not None and record.process_group == group_id:
            members.append(pid)
    terminate_processes(tuple(members))


def main(argv: list[str] | None = None) -> int:
    """Record this stable pre-exec pid, then replace the wrapper with onejudge."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        print("usage: watchdog PID_FILE COMMAND [ARG ...]", file=sys.stderr)
        return 2
    pid_file = Path(args.pop(0))
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    if os.environ.pop("ORCHESTRATOR_WATCHDOG_UNSET_LLMLINT", None) == "1":
        os.environ.pop("LLMLINT_ONEHARNESS_BIN", None)
    os.execvpe(args[0], args, os.environ)
    return 127  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover - real subprocess boundary
    raise SystemExit(main())
