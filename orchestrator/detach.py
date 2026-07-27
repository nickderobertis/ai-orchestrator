"""Outliving the turn that launched a recorded round.

A tracked round is long-lived, but the orchestrator agent starts it from inside one
harness turn and then blocks on the planner channel or surfaces an update. Ending
that turn tears down the turn's child process group, which would take the round with
it and leave a claimed round whose `status.json` still says `"status": "running"`
under a pid that no longer exists.

Leaving the process group is necessary but *not sufficient*, which is why this is a
fork and not a bare `setsid`. Two separate things kill a launched round:

* the teardown signals every process in the launching turn's group — `setsid`
  escapes that; and
* `uv run`, which every `just` recipe goes through, forwards the signal it receives
  to its own direct child **by pid** and then escalates to SIGKILL — no session
  change escapes that, so the round must not *be* uv's direct child.

So the entry point forks. The child leaves the session and owns the round; the parent
stays behind for one job — relaying the child's exit status — and it is the parent
that uv signals and the turn's teardown reaches. Killing it leaves the round
reparented to init and still working, with the exit status of a completed round still
reaching a caller that waits for it.

What this costs is job control: a round in its own session is not in its terminal's
foreground group, so Ctrl-C reaches only the relaying parent. The child therefore
announces its own pid, which is the pid to signal to stop a round on purpose.

This is the first of three layers. A killer that walks the process tree still reaches
us, so `runs.round_abandonment_guard` records an abandonment on every catchable
signal, and the run views derive liveness from the recorded owner's pid so even
SIGKILL surfaces as abandoned rather than as running.
"""

from __future__ import annotations

import os
import signal
import sys
import traceback
from typing import NoReturn, Protocol

CRASHED = 70
"""The status a round exits with when an exception escaped it.

Deliberately none of the statuses the round itself returns — 0 complete, 1 unfinished,
2 rejected input — because those all describe a round that reached a conclusion. A
crash reported as 1 reads as an ordinary unfinished round, which is the misreading this
whole module exists to prevent: the caller goes on waiting for a planner decision that
nothing is left alive to ask for. 70 is sysexits' ``EX_SOFTWARE``.
"""


class Entry(Protocol):
    """One round-owning CLI entry point: parse argv, return a process exit code."""

    def __call__(self, argv: list[str] | None = None, /) -> int: ...


def run_detached(entry: Entry, argv: list[str] | None, label: str) -> int:
    """Run a round-owning CLI entry point outside the launching turn's reach.

    Wired only at process entry points, never inside ``main`` itself: ``next-round``
    and the deprecated ``repo-plan`` alias call the executor in-process, and unit
    tests call it directly, so forking there would fork the *caller*.
    """
    # Anything buffered here would otherwise be flushed twice, once per process.
    sys.stdout.flush()
    sys.stderr.flush()
    child = os.fork()
    # llmlint: ignore[changed_behavior_has_e2e] the round side of the fork is proven by
    # tests/e2e/test_round_ownership_e2e.py; a process that ends in `os._exit` cannot
    # report its own coverage, so it is excluded rather than left looking untested.
    if child == 0:  # pragma: no cover - runs only in the forked round, never in a test
        _own_round(entry, argv, label)
    return _relay_exit(child)


def _own_round(  # pragma: no cover - see run_detached
    entry: Entry, argv: list[str] | None, label: str
) -> NoReturn:
    """Lead a new session, run the round, and exit without returning to the caller.

    ``os._exit`` rather than a return: the caller is a forked copy of the launching
    process, so returning would run the rest of *its* program a second time. The
    interpreter's own flushing is skipped along with everything else, so the streams
    are flushed here.

    An escaping exception is the one way control could still leave here, so it is
    caught rather than allowed to unwind: nothing above this frame belongs to the
    round, and a traceback that climbs past the fork is the launching process's
    program running a second time in the child. It is reported and turned into
    ``CRASHED`` instead, so the failure stays diagnosable and the exit status says
    what happened. Two exceptions are not crashes and keep statuses of their own: a
    ``SystemExit`` is a status the round *chose* — `argparse` ends a rejected command
    line that way — and a ``KeyboardInterrupt`` is SIGINT reaching a round before it
    installed a handler for it, which still owes its caller the 128+N every other
    signalled death reports.
    """
    os.setsid()
    print(
        f"{label}: round owner pid {os.getpid()} leads its own session; "
        f"ending the launching turn will not stop it. Stop it with `kill {os.getpid()}`.",
        file=sys.stderr,
        flush=True,
    )
    try:
        code = entry(argv)
    except SystemExit as chosen:
        code = _chosen_status(chosen.code)
    except KeyboardInterrupt:
        code = 128 + int(signal.SIGINT)
    except BaseException:
        traceback.print_exc()
        code = CRASHED
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def _chosen_status(  # pragma: no cover - see run_detached
    code: int | str | None,
) -> int:
    """Turn a ``SystemExit``'s payload into the status the interpreter would have used."""
    match code:
        case None:
            return 0
        case int():
            return code
        case _:
            print(code, file=sys.stderr)
            return 1


def _relay_exit(child: int) -> int:
    """Wait for the round owner and report its outcome as this process's own.

    A signalled round reports the familiar 128+N so an exit status still names how it
    ended; the round records the abandonment itself before it goes.
    """
    _, status = os.waitpid(child, 0)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return os.WEXITSTATUS(status)
