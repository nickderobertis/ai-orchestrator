"""Stopping one orchestration run, on evidence the run itself recorded.

Three things make this a verb rather than a `kill` an operator assembles by hand,
and each of them is a way that hand-rolled stop has already gone wrong on this host:

* **Ownership is checked first.** Several planners share a host, and the pattern an
  operator matches in `ps` output knows nothing about whose work it matched. A run
  this caller did not launch — including one nobody can attribute — is refused by
  name, and `--force` says whose work it is about to end before it ends it.
* **The processes come from the run's own records.** The launch and every round
  write their owner's pid; this reads those and walks the tree below them. Nothing
  here matches a command line, so a stop cannot reach a process that merely looks
  like this run's.
* **The tree is walked, not the process group.** An orchestrator's descendants do
  not share its group — a dispatched worker leads a group of its own, and the round
  owner leads its own session — so a `killpg` on the recorded pid leaves the live
  worker running, which is exactly how an orphaned dispatch keeps writing to a
  repository after its orchestrator is gone.

What this deliberately does *not* do is record anything about the run. A stopped
round is abandoned by its own owner, through the same `round_abandonment_guard`
that a Ctrl-C or a teardown signal goes through, so a stopped run is left in the
harness's ordinary reclaimable state and `just runs` names the command that
reclaims it. That is why the round owners are signalled first and given time to
record before anything is escalated.
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import sys
import time
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError
from .launch import RunOwner, caller_identity, read_run_owner
from .runs import (
    abandoned_launch_indicator,
    abandoned_round_indicator,
    load_mapping,
    rounds,
    validate_run_id,
)
from .watchdog import ProcessId, descendants_of

#: How long the recorded owners are given to die under SIGTERM before survivors are
#: escalated. A round owner records its own abandonment inside the handler, and that
#: record is what keeps the round reclaimable, so this is the time that write gets.
DEFAULT_GRACE_SECONDS = 10.0

_POLL_SECONDS = 0.05
_ESCALATION_SECONDS = 2.0


@dataclass(frozen=True)
class RecordedOwner:
    """One process a run recorded as its own, and the record that named it."""

    pid: ProcessId
    #: Which record named it: ``orchestrator`` or ``round-NN``.
    source: str
    host: str

    @property
    def local(self) -> bool:
        return self.host == socket.gethostname()


def _recorded_owner(path: Path, source: str) -> RecordedOwner | None:
    """One ``running`` owner from a status record, or ``None`` when there is none.

    Every unreadable or non-``running`` record answers ``None``: this decides what to
    *signal*, so a record that does not clearly name a working process must never
    contribute a pid. The run views keep their own, separate policy for reporting.
    """
    try:
        state = load_mapping(path)
    except (ConfigError, OSError):
        return None
    pid = state.get("pid")
    host = state.get("host")
    if (
        state.get("status") != "running"
        or not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid < 1
        or not isinstance(host, str)
        or not host
    ):
        return None
    return RecordedOwner(ProcessId(pid), source, host)


def recorded_owners(run_dir: Path) -> tuple[RecordedOwner, ...]:
    """Every process this run recorded as running, newest round last."""
    found = [_recorded_owner(run_dir / "orchestrator" / "status.json", "orchestrator")]
    found.extend(
        _recorded_owner(round_dir / "status.json", f"round-{number:02d}")
        for number, round_dir in rounds(run_dir)
    )
    return tuple(owner for owner in found if owner is not None)


def is_running(pid: ProcessId) -> bool:
    """Whether ``pid`` is still executing on this host.

    A zombie counts as stopped, and has to: it has already exited, and all that is
    outstanding is a parent collecting its status. ``kill(pid, 0)`` succeeds against
    one, so a stop that trusted that alone would wait out its whole grace period and
    then report a process it had successfully ended as a survivor.

    A pid this user may not signal is reported running, in the same direction as
    `runs.process_may_be_live`: claiming a process is gone when it cannot be checked
    is how an orphan goes unnoticed.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return True
    fields = raw[raw.rfind(")") + 2 :].split()
    return not fields or fields[0] != "Z"


@dataclass(frozen=True)
class StopReport:
    """What one stop signalled, and what outlived it."""

    signalled: tuple[ProcessId, ...]
    remaining: tuple[ProcessId, ...]
    escalated: tuple[ProcessId, ...]


def _signal(pids: Iterable[ProcessId], number: int) -> None:
    """Signal deepest-observed first, so a supervisor cannot respawn what just died.

    Sorting by pid descending is an approximation of depth that costs nothing and
    needs no second `/proc` walk; a process already gone, or one this user may not
    signal, is skipped rather than failing the stop.
    """
    for pid in sorted(pids, reverse=True):
        with suppress(PermissionError, ProcessLookupError):
            os.kill(pid, number)


def _live(pids: Iterable[ProcessId]) -> set[ProcessId]:
    return {pid for pid in pids if is_running(pid)}


def stop_run(
    owners: tuple[RecordedOwner, ...],
    *,
    grace: float = DEFAULT_GRACE_SECONDS,
    poll: float = _POLL_SECONDS,
) -> StopReport:
    """Terminate every recorded owner and everything below it.

    The tracked set only ever grows. A descendant reparents to init the moment its
    own parent exits, and a walk from the recorded roots can no longer reach it — so
    every pid seen under a root at any point during the teardown stays on the list
    to be signalled and confirmed gone, which is what keeps a worker from outliving
    the orchestrator that dispatched it.
    """
    tracked = run_tree(owners)
    signalled: set[ProcessId] = set()
    # A round owner records its abandonment inside its SIGTERM handler, and that record
    # is what leaves the round reclaimable, so it takes the signal before its own
    # children start dying underneath it.
    rounds_first = {item.pid for item in owners if item.local and item.source != "orchestrator"}
    _signal(rounds_first, signal.SIGTERM)
    signalled |= rounds_first
    deadline = time.monotonic() + max(0.0, grace)
    while True:
        tracked |= set(descendants_of(frozenset(_live(tracked))))
        alive = _live(tracked)
        if not alive:
            break
        _signal(alive - signalled, signal.SIGTERM)
        signalled |= alive
        if time.monotonic() >= deadline:
            break
        time.sleep(poll)
    escalated = _live(tracked)
    kill_deadline = time.monotonic() + _ESCALATION_SECONDS
    while _live(tracked) and time.monotonic() < kill_deadline:
        tracked |= set(descendants_of(frozenset(_live(tracked))))
        _signal(_live(tracked), signal.SIGKILL)
        time.sleep(poll)
    # Reaping is deliberately not attempted: these are not this process's children —
    # a launched orchestrator is reparented away at launch — so a lingering zombie
    # belongs to whoever is left waiting on it, and `is_running` counts it as gone.
    return StopReport(
        tuple(sorted(tracked)), tuple(sorted(_live(tracked))), tuple(sorted(escalated))
    )


def run_tree(owners: tuple[RecordedOwner, ...]) -> set[ProcessId]:
    """Every live process this host can reach from a run's recorded owners."""
    roots = frozenset(item.pid for item in owners if item.local)
    return set(roots) | set(descendants_of(roots))


def _describe(owner: RunOwner) -> str:
    """Name a run's owner for a refusal, without printing its session id.

    Only ever called about a run the caller does not own, so there is no "you" case
    to render: the two things a planner can be looking at are another planner's run
    and a run nobody can attribute.
    """
    if owner.identity is None:
        return "no recorded launcher (unknown is not the same as yours)"
    return f"another planner ({owner.identity.label})"


def _report_targets(run_id: str, owners: tuple[RecordedOwner, ...]) -> None:
    for owner in owners:
        where = "" if owner.local else f" on {owner.host} — not reachable from this host"
        print(f"stop: {run_id} {owner.source} owner pid {owner.pid}{where}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stop a tracked orchestration run this session launched."
    )
    parser.add_argument("run_id")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument(
        "--force",
        action="store_true",
        help="stop a run this session did not launch, after reporting who owns it",
    )
    parser.add_argument(
        "--grace",
        type=float,
        default=DEFAULT_GRACE_SECONDS,
        metavar="SECONDS",
        help="how long recorded owners may take SIGTERM before survivors are killed "
        f"(default: {DEFAULT_GRACE_SECONDS:g})",
    )
    args = parser.parse_args(argv)
    if args.grace < 0:
        parser.error("--grace must not be negative")
    try:
        run_id = validate_run_id(args.run_id)
    except ConfigError as exc:
        print(f"stop: {exc}", file=sys.stderr)
        return 2
    run_dir = args.runs_dir / run_id
    if not run_dir.is_dir():
        print(f"stop: no recorded run {run_id!r} under {args.runs_dir}", file=sys.stderr)
        return 2

    caller = caller_identity()
    owner = read_run_owner(run_dir)
    if not owner.is_(caller):
        mine = caller.label if caller is not None else "this session has no launcher provenance"
        if not args.force:
            print(
                f"stop: refusing to stop {run_id}: it was launched by "
                f"{_describe(owner)}, not by you ({mine}). Confirm with its planner, "
                f"or override with: just stop {run_id} --runs-dir {args.runs_dir} --force",
                file=sys.stderr,
            )
            return 2
        print(f"stop: --force: {run_id} was launched by {_describe(owner)}; you are {mine}")
        print("stop: this will stop the following recorded processes and everything below them:")
        _report_targets(run_id, recorded_owners(run_dir))

    owners = recorded_owners(run_dir)
    if unreachable := tuple(item for item in owners if not item.local):
        print(
            "stop: leaving "
            + ", ".join(f"{item.source} pid {item.pid} on {item.host}" for item in unreachable)
            + " alone; this host cannot signal another host's processes",
            file=sys.stderr,
        )
    local = tuple(item for item in owners if item.local)
    if not (live_owners := tuple(item for item in local if is_running(item.pid))):
        print(f"stop: {run_id} has no recorded process still running; nothing to stop")
        _print_state(run_dir, args.runs_dir)
        return 0
    if os.getpid() in run_tree(live_owners):
        print(
            f"stop: this process is itself part of {run_id}'s process tree; "
            "stop the run from outside it",
            file=sys.stderr,
        )
        return 2

    report = stop_run(live_owners, grace=args.grace)
    if (status := report_outcome(run_id, report)) != 0:
        return status
    _print_state(run_dir, args.runs_dir)
    return 0


def report_outcome(run_id: str, report: StopReport) -> int:
    """Render what one stop did, and answer whether it finished the job.

    A survivor is a failure with a status of its own rather than a warning under a
    success: something the harness cannot signal is still holding this run's
    worktrees, and relaunching over it is how two dispatches end up in one checkout.
    """
    print(
        f"stop: {run_id} signalled {len(report.signalled)} process(es) "
        f"({len(report.escalated)} needed SIGKILL)"
    )
    if not report.remaining:
        return 0
    pids = ", ".join(str(pid) for pid in report.remaining)
    print(
        f"stop: {run_id} left {len(report.remaining)} process(es) running: {pids}. "
        "Inspect them before relaunching this run.",
        file=sys.stderr,
    )
    return 1


def _print_state(run_dir: Path, runs_dir: Path) -> None:
    """Report how the harness now reads this run, including how to reclaim it."""
    indicator = abandoned_round_indicator(run_dir) or abandoned_launch_indicator(run_dir)
    print(f"stop: {run_dir.name}  {indicator}" if indicator else f"stop: {run_dir.name} is stopped")
    print(f"stop: review it with: just results {run_dir.name} --runs-dir {runs_dir}")


if __name__ == "__main__":  # pragma: no cover - real subprocess boundary
    raise SystemExit(main())
