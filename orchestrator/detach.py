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

Leaving the session is only half of outliving a dispatch, and the other half is
`reattribute_successor`: escaping the *signals* a turn's teardown sends is no use if
the sweep that runs once the launching step settles terminates the round anyway. It
does, because the environment the kernel fixed at this process's ``exec`` still names
the launcher's dispatch, and that stamp is the sweep's whole proof of a leaked tree.
So a successor takes a status directory of its own and re-``exec``s under it before it
forks. See "The successor contract" in docs/repo-lifecycle.md.

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
import warnings
from pathlib import Path
from typing import NoReturn, Protocol

from .scratch import (
    AGENT_STATUS_DIR_ENV,
    claim_successor_scratch_directory,
    record_successor_owner,
)

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


SUCCESSOR_ENV = "ORCHESTRATOR_DETACHED_SUCCESSOR"
"""Set on the re-``exec``ed side of `reattribute_successor`, and read only there.

An environment variable rather than a flag, because the thing that has to survive is
the ``exec`` itself: nothing in this process image outlives it, so the one channel
from before to after is the environment the new image is handed. It is removed from
``os.environ`` the moment it is read, so a successor this one starts — a `repo-recover`
under a round, say — re-attributes to a directory of its own rather than inheriting a
claim that says its launcher already did.
"""

#: The successor status directory this process is running under, once it has
#: re-attributed to one. Held here rather than re-derived from the environment because
#: only this module can tell a directory it claimed from one it merely inherited, and
#: the forked round owner has to know which of the two it is recording itself into.
_claimed: Path | None = None

#: The interpreter's own warning about forking a process that has threads, matched
#: exactly so that suppressing it can suppress nothing else. Anchored at the start
#: because `warnings` matches a filter's message with `re.match`, and stopping at the
#: fixed prefix because the rest of the text carries the forking process's pid.
_MULTI_THREADED_FORK_WARNING = (
    r"This process \(pid=\d+\) is multi-threaded, use of fork\(\) may lead to deadlocks"
)


def reattribute_successor(label: str) -> None:
    """Re-``exec`` under a status directory of this process's own, and return there.

    **Only ever called at a real process entry point.** It replaces the process image
    with the command line the interpreter was given, so calling it from anywhere that
    is not the top of ``main`` restarts the whole program — and calling it from a test
    restarts the test runner.

    A round owner or a publication driver launched from inside a dispatch inherits that
    dispatch's ``ORCHESTRATOR_AGENT_STATUS_DIR``. Once the launching step settles, the
    scratch sweep reads the stamp, finds the dispatch behind it finished, and reaps
    what it takes for a leaked tree — which is what killed two `repo-recover` runs that
    had already passed their gate, thirty minutes apart, on branches that then had to
    be recovered by hand. Nothing weaker than an ``exec`` fixes it: ``/proc/<pid>/environ``
    is the memory the kernel wrote at ``exec`` and `os.environ` does not touch it, so a
    process cannot shed an inherited stamp in place.

    Re-stamping rather than scrubbing, because the sweep's reach is not the problem —
    the misattribution is. A successor under its own directory is retained while it
    runs and reaped when it is gone, which is what an unstamped process would never be:
    the tree a successor leaves behind is exactly the kind this sweep exists for.

    Returns without doing anything when the process carries no stamp at all, which is
    every launch from an ordinary shell, and when no durable claim could be made — a
    successor that runs unattributed is a sweep that cannot reach it, which is the safe
    direction to fail in.
    """
    global _claimed
    if os.environ.pop(SUCCESSOR_ENV, None) is not None:
        stamped = os.environ.get(AGENT_STATUS_DIR_ENV)
        _claimed = Path(stamped) if stamped else None
        return
    if not os.environ.get(AGENT_STATUS_DIR_ENV):
        return
    status_dir = claim_successor_scratch_directory()
    if status_dir is None:
        return
    environment = {
        **os.environ,
        AGENT_STATUS_DIR_ENV: os.fspath(status_dir),
        SUCCESSOR_ENV: label,
    }
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        os.execve(sys.executable, sys.orig_argv, environment)
    except OSError as failure:  # pragma: no cover - the exec of this same interpreter
        # Reported rather than raised: the work this process was started to do is
        # worth more than the attribution, and an operator who sees this knows the
        # run is back to being killable by the sweep behind its launcher.
        print(
            f"{label}: could not re-exec under its own dispatch attribution ({failure}); "
            "continuing under the launching dispatch's stamp",
            file=sys.stderr,
            flush=True,
        )


def run_detached(entry: Entry, argv: list[str] | None, label: str) -> int:
    """Run a round-owning CLI entry point outside the launching turn's reach.

    Wired only at process entry points, never inside ``main`` itself: ``next-round``
    calls the executor in-process, and unit tests call it directly, so forking there
    would fork the *caller*.
    """
    # Anything buffered here would otherwise be flushed twice, once per process.
    sys.stdout.flush()
    sys.stderr.flush()
    # The child's whole program is `_own_round`, which waits on no state a thread of
    # this process could hold a lock on, so the interpreter's threaded-fork warning
    # does not apply here. Suppressed at this call alone, so the same warning about
    # any other fork still reaches a reader; a child that grew a dependency on an
    # inherited lock would make the warning right again.
    # llmlint: ignore[changed_behavior_has_e2e] a round-owning entry point forks
    # single-threaded, so the interpreter never issues this warning in a real round and
    # no journey can observe it; tests/test_detach.py starts a thread to provoke it.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message=_MULTI_THREADED_FORK_WARNING, category=DeprecationWarning
        )
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
    # Recorded here rather than by the process that claimed the directory, because that
    # process is the relaying parent and the launching turn's teardown kills it. An
    # owner record naming it would go stale the moment the round it protects was left
    # alone, which is the failure `reattribute_successor` exists to prevent.
    if _claimed is not None:
        record_successor_owner(_claimed)
    print(
        f"{label}: pid {os.getpid()} leads its own session; "
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
