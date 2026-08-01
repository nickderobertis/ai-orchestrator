"""Reap a test session's process tree when the session itself dies uncleanly.

Every other layer of the guard runs *inside* the session, so a SIGKILLed pytest —
how these runs actually end when a supervisor cancels one or the host runs out of
room — runs no teardown at all. So the last layer lives outside. This process is
started by the guard in a session of its own and given the read end of a pipe the
test session holds open: while that pipe is open it samples the session's
descendants and remembers each one, and when it closes it terminates what it
remembered.

Sampling parentage is necessary but not sufficient. A *launch* — what
`dispatch.launch_orchestrator` does, and `just orchestrate` with it — starts its
process in a session of its own and never waits for it, so everything the launched
process goes on to start was never below this session at any instant. No sampling
interval closes that. The claim of last resort is instead one the kernel fixes at
`exec` and a process cannot leave behind: a token unique to this session, exported
into its environment before anything starts and inherited however a descendant
detaches, so one scan at the end finds every survivor whatever became of its
ancestry.

That token is also what bounds the reap. Another session's tree, another
orchestrator's run, anything else on the host — none of them carry it. Parentage
claims are bounded the same way and, in addition, by the start token procfs stamped
each pid with, so a pid since handed to a stranger is left alone.

Between them they cover what this repository has had to reap by hand: the `onejudge`
and `orchestrator.channel` processes found running out of temp directories that had
been deleted a day earlier.

None of that helps if the reaper dies with the session, which is why it detaches
before it starts watching — see `_detach_and_watch`.

Usage: ``leak_reaper.py ROOT_PID SESSION_TOKEN``, with the session's pipe on stdin.
It prints the detached reaper's pid on stdout and exits immediately; that pid is
what the session excludes from its own sweep and waits for at the end.
"""

from __future__ import annotations

import os
import select
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import path for a spawned process
    sys.path.insert(0, str(REPO_ROOT))

# noqa: E402 below — this module is spawned as a script from an arbitrary working
# directory, so the repository has to reach sys.path before either import resolves.
from orchestrator.coordination import ProcessStart, process_start_identity  # noqa: E402
from orchestrator.watchdog import ProcessId, descendants, terminate_processes  # noqa: E402

#: How often the session's tree is sampled, and so the whole width of this reaper's
#: blind spot: a process is claimed within ``POLL_SECONDS`` of appearing, and only a
#: session that dies inside that window can leave one behind.
#:
#: Set for cost rather than for the window, because the leaks this exists for ran
#: for hours. A full procfs walk on a timer runs in the same interpreter as whatever
#: that session is supervising — including dispatch's own liveness sampling, which
#: measures intervals this would otherwise compete with — so it is deliberately
#: infrequent. `process_tree.LINGER` is what keeps the guard's own e2e deterministic
#: against it, and must stay comfortably above this.
POLL_SECONDS = 0.5

#: The variable one test session exports to stamp everything it starts. Read from
#: `/proc/<pid>/environ`, which the kernel fixes at `exec` — so a process cannot shed
#: it by detaching, and a scan at the end finds what parentage lost track of.
SESSION_TOKEN_ENV = "AI_ORCHESTRATOR_LEAK_GUARD_SESSION"


def token_carriers(token: str, *, ignoring: frozenset[ProcessId] = frozenset()) -> set[ProcessId]:
    """Every live process whose inherited environment names this session's token.

    Read as whole NUL-delimited entries rather than as a substring of the block, so
    a value that merely contains the token cannot be mistaken for the variable.
    """
    stamp = f"{SESSION_TOKEN_ENV}={token}".encode()
    found: set[ProcessId] = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = ProcessId(int(entry.name))
        if pid in ignoring:
            continue
        try:
            block = (entry / "environ").read_bytes()
        except OSError:
            # Gone, or another user's. Either way not this session's to account for.
            continue
        if stamp in block.split(b"\0"):
            found.add(pid)
    return found


class TreeSampler:
    """Everything ever seen running below one root, remembered with its identity.

    Sampling rather than walking once at the end is the whole trick. A process that
    leaves its parent — and so the tree — is only reachable *while* its parent is
    still alive, so anything that looks later finds a smaller tree than existed. The
    cost of remembering is one procfs walk per interval; the cost of not is the
    process nobody can find again.

    Claims are never revised. A process already recorded may have exited since, and
    re-reading its identity would either fail or — worse, once the kernel reused the
    number — record a stranger's in its place.
    """

    def __init__(self, root_pid: int) -> None:
        self.root_pid = root_pid
        self._known: dict[ProcessId, ProcessStart | None] = {}
        self._guard = threading.Lock()
        # Some of this suite forks the very process being watched — that is what
        # `run_detached` is — and a fork inherits only the calling thread. A lock the
        # sampling thread happened to hold at that instant would stay held in the
        # child forever, and the child would hang the first time it touched it.
        # Handing the lock across the fork is what CPython does for its own locks.
        os.register_at_fork(
            before=self._guard.acquire,
            after_in_parent=self._guard.release,
            after_in_child=self._guard.release,
        )

    def sample(self) -> None:
        """Record every descendant of the root not already claimed."""
        seen = descendants(ProcessId(self.root_pid))
        with self._guard:
            for pid in seen:
                if pid != self.root_pid and pid != os.getpid() and pid not in self._known:
                    self._known[pid] = process_start_identity(pid)

    def claimed(self) -> frozenset[ProcessId]:
        with self._guard:
            return frozenset(self._known)

    def survivors(self, *, ignoring: frozenset[ProcessId] = frozenset()) -> tuple[ProcessId, ...]:
        """Claimed processes still running as the same process that was claimed."""
        with self._guard:
            claims = dict(self._known)
        return tuple(
            pid
            for pid, start in sorted(claims.items())
            if pid not in ignoring and start is not None and process_start_identity(pid) == start
        )


def watch(
    root_pid: int,
    *,
    token: str | None = None,
    stream: int = 0,
    poll: float = POLL_SECONDS,
) -> tuple[ProcessId, ...]:
    """Track ``root_pid``'s tree until ``stream`` closes, then reap what outlived it.

    Returns the processes it terminated, so a caller driving this in-process can
    assert on the reap rather than on a side effect it has to go looking for.

    ``token`` is this session's environment stamp. The scan for it happens once, here
    at the end, because unlike parentage the stamp does not decay: a survivor still
    carries it however long ago its ancestors left. So the steady-state cost of
    closing the launch-shaped gap is nothing — no extra work per interval, only one
    pass over procfs when the session is already over.

    ``root_pid`` itself is never a candidate. A clean shutdown closes this pipe while
    the session is still running, and the session carries its own stamp.
    """
    sampler = TreeSampler(root_pid)
    while True:
        # Sampled before the wait, so the window a process can hide in is measured
        # from when it appeared rather than from when this reaper happened to start.
        sampler.sample()
        readable, _, _ = select.select([stream], [], [], poll)
        if readable and os.read(stream, 4096) == b"":
            break
    mine = frozenset({ProcessId(os.getpid()), ProcessId(root_pid)})
    stamped = token_carriers(token, ignoring=mine) if token else set()
    reaped = tuple(sorted(set(sampler.survivors(ignoring=mine)) | stamped))
    if reaped:
        terminate_processes(reaped)
    return reaped


def _detach_and_watch(root_pid: int, token: str) -> int:  # pragma: no cover - real fork boundary
    """Get adopted by init, then watch; report the detached pid to the caller.

    ``start_new_session`` moves a process out of its launcher's *group*, which is
    what stops a group kill from reaching it. It does not move it out of the
    launcher's *ancestry*, and ancestry is what actually ends these sessions: a
    stalled dispatch is terminated by signalling every pid a walk of its tree found
    (`orchestrator.watchdog.terminate_processes`), and a reaper still hanging off
    the session under test is in that walk. It died with the session it was posted
    to outlive, and the launch it had claimed ran for another eighteen hours.

    So the reaper forks and lets the intermediate exit. Once init has adopted it, no
    walk from the session reaches it, and the only handle anything retains is the
    pipe — which is the handle whose *closing* is the signal it waits for.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    child = os.fork()
    if child != 0:
        # Printed by the intermediate, because the grandchild's pid is not something
        # the session could otherwise learn: it is nobody's child by the time it runs.
        print(child, flush=True)
        return 0
    # A session and group of its own, so a kill aimed at the intermediate's group —
    # which still names the intermediate's now-dead pid — cannot reach this either.
    os.setsid()
    # The stdout pipe is how the pid was reported and nothing more; holding it open
    # would leave the reader waiting on an EOF that only arrives hours later.
    with open(os.devnull, "wb") as null:
        os.dup2(null.fileno(), 1)
    reaped = watch(root_pid, token=token)
    _report(root_pid, reaped)
    os._exit(0)


def _report(root_pid: int, reaped: tuple[ProcessId, ...]) -> None:
    if reaped:
        print(
            f"leak-reaper: session {root_pid} died leaving {len(reaped)} process(es); "
            f"terminated {', '.join(str(pid) for pid in reaped)}",
            file=sys.stderr,
            flush=True,
        )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or not args[0].isdigit() or not args[1]:
        print("usage: leak_reaper.py ROOT_PID SESSION_TOKEN", file=sys.stderr)
        return 2
    return _detach_and_watch(int(args[0]), args[1])


if __name__ == "__main__":  # pragma: no cover - real subprocess boundary
    raise SystemExit(main())
