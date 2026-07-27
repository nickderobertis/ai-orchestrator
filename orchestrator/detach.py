"""Outliving the turn that launched a recorded round.

A tracked round is long-lived, but the orchestrator agent starts it from inside one
harness turn and then blocks on the planner channel or surfaces an update. Ending
that turn tears down the turn's child process group, and that destroyed two real
runs: the executor took SIGTERM about a minute after dispatching a worker onto a
branch, leaving a claimed round whose `status.json` still said `"status": "running"`
under a pid that no longer existed.

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
import sys
from typing import NoReturn, Protocol


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
    """
    os.setsid()
    print(
        f"{label}: round owner pid {os.getpid()} leads its own session; "
        f"ending the launching turn will not stop it. Stop it with `kill {os.getpid()}`.",
        file=sys.stderr,
        flush=True,
    )
    code = entry(argv)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def _relay_exit(child: int) -> int:
    """Wait for the round owner and report its outcome as this process's own.

    A signalled round reports the familiar 128+N so an exit status still names how it
    ended; the round records the abandonment itself before it goes.
    """
    _, status = os.waitpid(child, 0)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return os.WEXITSTATUS(status)
