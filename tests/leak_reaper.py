"""Reap a test session's process tree when the session itself dies uncleanly.

Every other layer of the guard runs *inside* the session, so every other layer is
lost the moment the session is killed rather than asked to stop. That is not the
exotic case: a SIGKILLed pytest is how these runs actually end when a supervisor
cancels one or the host runs out of room, and it is how twenty-two onejudge
processes came to be running out of temp directories deleted a day earlier.

So the last layer lives outside the session. This process is started by the guard
in a session of its own and given the read end of a pipe the test session holds
open. While that pipe is open it samples the session's descendants and remembers
each one; when the pipe closes — for any reason, including a death nothing in the
session could have handled — it terminates what it remembered.

It only ever reaps what it watched the session produce, and only while the kernel
still agrees that pid is the same process it sampled: each claim carries the start
token procfs stamped it with, so a pid the kernel has since handed to somebody
else's process is left alone. Nothing else on the host is a candidate.

Usage: ``leak_reaper.py ROOT_PID``, with the session's pipe on stdin.
"""

from __future__ import annotations

import os
import select
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import path for a spawned process
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.coordination import ProcessStart, process_start_identity  # noqa: E402
from orchestrator.watchdog import ProcessId, process_activity, terminate_processes  # noqa: E402

#: How often the session's tree is sampled, and so the whole width of this reaper's
#: blind spot: a process is claimed within ``POLL_SECONDS`` of appearing, and only a
#: session that dies inside that window can leave one behind. The leaks this exists
#: for ran for minutes, so the interval is set to keep the cost of a full procfs
#: walk invisible when a dozen sessions run at once rather than to chase that window
#: to zero. A caller that needs the guarantee deterministically — the guard's own
#: e2e — waits this long after the process appears.
POLL_SECONDS = 0.25


def sample(root_pid: int, known: dict[ProcessId, ProcessStart | None]) -> None:
    """Record every descendant of ``root_pid`` this reaper has not already claimed.

    Claims are never revised. A process already recorded may have exited since, and
    re-reading its identity would either fail or — worse, after the kernel reused
    the number — record a stranger's.
    """
    for pid in process_activity(ProcessId(root_pid)).pids:
        if pid != root_pid and pid != os.getpid() and pid not in known:
            known[pid] = process_start_identity(pid)


def survivors(known: dict[ProcessId, ProcessStart | None]) -> tuple[ProcessId, ...]:
    """The claimed processes still running as the same process that was claimed."""
    return tuple(
        pid
        for pid, start in sorted(known.items())
        if start is not None and process_start_identity(pid) == start
    )


def watch(root_pid: int, *, stream: int = 0, poll: float = POLL_SECONDS) -> tuple[ProcessId, ...]:
    """Track ``root_pid``'s tree until ``stream`` closes, then reap what outlived it.

    Returns the processes it terminated, so a caller driving this in-process can
    assert on the reap rather than on a side effect it has to go looking for.
    """
    known: dict[ProcessId, ProcessStart | None] = {}
    while True:
        # Sampled before the wait, so the window a process can hide in is measured
        # from when it appeared rather than from when this reaper happened to start.
        sample(root_pid, known)
        readable, _, _ = select.select([stream], [], [], poll)
        if readable and os.read(stream, 4096) == b"":
            break
    reaped = survivors(known)
    if reaped:
        terminate_processes(reaped)
    return reaped


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or not args[0].isdigit():
        print("usage: leak_reaper.py ROOT_PID", file=sys.stderr)
        return 2
    reaped = watch(int(args[0]))
    if reaped:
        print(
            f"leak-reaper: session {args[0]} died leaving {len(reaped)} process(es); "
            f"terminated {', '.join(str(pid) for pid in reaped)}",
            file=sys.stderr,
            flush=True,
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - real subprocess boundary
    raise SystemExit(main())
