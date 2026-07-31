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
import math
import os
import signal
import socket
import sys
import time
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
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


#: How much later than its own record a process may have started and still be believed
#: to be the one that wrote it. A running owner records itself at once, so the honest
#: order is start-then-record; this only absorbs the clock's granularity and the boot
#: time's rounding, not a pid that came around again minutes or hours later.
_START_SKEW_SECONDS = 60.0


def process_started_at(pid: ProcessId) -> float | None:
    """When ``pid`` began, in epoch seconds, or ``None`` when this host cannot say.

    Derived from the kernel's own boot time plus the process's start ticks rather
    than from anything the process could have written itself, which is what makes it
    usable as an identity: a recycled pid is a *different* process with a later start.
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        boot = next(
            float(line.split()[1])
            for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines()
            if line.startswith("btime ")
        )
    except (OSError, StopIteration, IndexError, ValueError):
        return None
    fields = raw[raw.rfind(")") + 2 :].split()
    if len(fields) < 20:
        return None
    try:
        return boot + float(fields[19]) / os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError):
        return None


def _wrote_its_own_record(pid: ProcessId, started: object) -> bool:
    """Whether ``pid`` can still be the process that recorded itself at ``started``.

    Only a *proven* mismatch answers ``False``: a pid whose process demonstrably began
    after the record that names it is a recycled number, and signalling it would kill
    whatever inherited it rather than this run's owner. Everything this host cannot
    establish — an unparseable stamp, a ``/proc`` it cannot read — answers ``True``,
    because refusing to stop a run on evidence nobody has would leave the orphan the
    whole verb exists to end.
    """
    # This stamp is corroborating evidence for *withholding* a signal, never authority
    # to send one: the pid itself comes from the run's own record and is validated in
    # `_recorded_owner`. Rejecting the record when the stamp is missing or malformed
    # would make a run with a corrupt `status.json` unstoppable by this verb, which
    # puts the planner straight back on the hand-rolled `ps`-and-`kill` that ended
    # another planner's work — the thing this module exists to replace. So the
    # recycling check fails open and the docstring above says so.
    # llmlint: ignore-block[boundary_inputs_validated] see the note above this directive.
    if not isinstance(started, str):
        return True
    try:
        recorded = datetime.fromisoformat(started)
    except ValueError:
        return True
    if recorded.tzinfo is None:
        return True
    # llmlint: ignore-end[boundary_inputs_validated]
    began = process_started_at(pid)
    return began is None or began <= recorded.timestamp() + _START_SKEW_SECONDS


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
    owner = RecordedOwner(ProcessId(pid), source, host)
    if owner.local and not _wrote_its_own_record(owner.pid, state.get("started")):
        return None
    return owner


def recorded_owners(run_dir: Path) -> tuple[RecordedOwner, ...]:
    """Every process this run recorded as running, newest round last."""
    found = [_recorded_owner(run_dir / "orchestrator" / "status.json", "orchestrator")]
    found.extend(
        _recorded_owner(round_dir / "status.json", f"round-{number:02d}")
        for number, round_dir in rounds(run_dir)
    )
    return tuple(owner for owner in found if owner is not None)


def process_may_be_live(pid: ProcessId) -> bool:
    """Whether ``pid`` cannot be shown to be gone from this host.

    Only ``False`` is a certainty, which is what the name says: a pid this user may
    not signal, or one whose ``/proc`` entry cannot be read, answers ``True`` without
    establishing that anything is executing. That is the same asymmetry, and the same
    spelling, as `runs.process_may_be_live` — claiming a process is gone when it
    cannot be checked is how an orphan goes unnoticed.

    A zombie is the one unreadable-looking state answered ``False``, and has to be: it
    has already exited, and all that is outstanding is a parent collecting its status.
    ``kill(pid, 0)`` succeeds against one, so a stop that trusted that alone would wait
    out its whole grace period and then report a process it had ended as a survivor.
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

    #: The pids a signal was actually delivered to — not everything this stop looked
    #: at. A tracked process that had already exited, or one this user may not signal,
    #: never appears here, so the count cannot overstate what the stop did.
    signalled: tuple[ProcessId, ...]
    remaining: tuple[ProcessId, ...]
    escalated: tuple[ProcessId, ...]


def _signal(pids: Iterable[ProcessId], number: int) -> set[ProcessId]:
    """Signal deepest-observed first and report which pids actually took it.

    Sorting by pid descending is an approximation of depth that costs nothing and
    needs no second `/proc` walk; a process already gone, or one this user may not
    signal, is skipped rather than failing the stop. Those skips are why the
    delivered set is returned rather than assumed: a report that counted every pid
    it *intended* to signal would overstate what this stop did.
    """
    delivered: set[ProcessId] = set()
    for pid in sorted(pids, reverse=True):
        with suppress(PermissionError, ProcessLookupError):
            os.kill(pid, number)
            delivered.add(pid)
    return delivered


def _live(pids: Iterable[ProcessId]) -> set[ProcessId]:
    return {pid for pid in pids if process_may_be_live(pid)}


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
    attempted: set[ProcessId] = set()
    # A round owner records its abandonment inside its SIGTERM handler, and that record
    # is what leaves the round reclaimable, so it takes the signal before its own
    # children start dying underneath it.
    rounds_first = {item.pid for item in owners if item.local and item.source != "orchestrator"}
    signalled |= _signal(rounds_first, signal.SIGTERM)
    attempted |= rounds_first
    deadline = time.monotonic() + max(0.0, grace)
    while True:
        tracked |= set(descendants_of(frozenset(_live(tracked))))
        alive = _live(tracked)
        if not alive:
            break
        signalled |= _signal(alive - attempted, signal.SIGTERM)
        attempted |= alive
        if time.monotonic() >= deadline:
            break
        time.sleep(poll)
    escalated = _live(tracked)
    kill_deadline = time.monotonic() + _ESCALATION_SECONDS
    while _live(tracked) and time.monotonic() < kill_deadline:
        tracked |= set(descendants_of(frozenset(_live(tracked))))
        signalled |= _signal(_live(tracked), signal.SIGKILL)
        time.sleep(poll)
    # Reaping is deliberately not attempted: these are not this process's children —
    # a launched orchestrator is reparented away at launch — so a lingering zombie
    # belongs to whoever is left waiting on it, and `process_may_be_live` counts it as gone.
    return StopReport(
        tuple(sorted(signalled)), tuple(sorted(_live(tracked))), tuple(sorted(escalated))
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
    # `argparse` only promises a float here. A non-finite one is not a long grace
    # period: `NaN` makes every deadline comparison false and `inf` never expires, so
    # either would leave a stop polling a live tree forever instead of escalating.
    if not math.isfinite(args.grace) or args.grace < 0:
        parser.error("--grace must be a non-negative, finite number of seconds")
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
    # Neither state below can be reached through the CLI on one host: a second kernel
    # hostname cannot be produced, and this process is never inside a run's tree, since
    # `just orchestrate` detaches its launch into a session of its own. Both run against
    # real records and real pids in tests/test_stop.py, while every state a real launch
    # *can* reach — refusal, forced stop, full teardown, reclaim, and the second stop
    # that finds nothing left — runs through the real recipe in
    # tests/e2e/test_run_ownership_e2e.py.
    # llmlint: ignore-block[changed_behavior_has_e2e] see the note above this directive.
    if unreachable := tuple(item for item in owners if not item.local):
        print(
            "stop: leaving "
            + ", ".join(f"{item.source} pid {item.pid} on {item.host}" for item in unreachable)
            + " alone; this host cannot signal another host's processes",
            file=sys.stderr,
        )
    local = tuple(item for item in owners if item.local)
    if not (live_owners := tuple(item for item in local if process_may_be_live(item.pid))):
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
    # llmlint: ignore-end[changed_behavior_has_e2e]

    report = stop_run(live_owners, grace=args.grace)
    if (status := report_outcome(run_id, report)) != 0:
        return status
    _print_state(run_dir, args.runs_dir)
    return 0


# Reaching the survivor branch needs a process that outlives SIGKILL, which no test can
# produce and no real launch has ever left behind; the rendering runs against a
# constructed report in tests/test_stop.py, and the success side runs through the real
# recipe in tests/e2e/test_run_ownership_e2e.py.
# llmlint: ignore-block[changed_behavior_has_e2e] see the note above this directive.
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


# llmlint: ignore-end[changed_behavior_has_e2e]


def _print_state(run_dir: Path, runs_dir: Path) -> None:
    """Report how the harness now reads this run, including how to reclaim it."""
    indicator = abandoned_round_indicator(run_dir) or abandoned_launch_indicator(run_dir)
    print(f"stop: {run_dir.name}  {indicator}" if indicator else f"stop: {run_dir.name} is stopped")
    print(f"stop: review it with: just results {run_dir.name} --runs-dir {runs_dir}")


if __name__ == "__main__":  # pragma: no cover - real subprocess boundary
    raise SystemExit(main())
