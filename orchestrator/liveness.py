"""Whether a launched orchestrator is *working*, not merely alive.

`runs.launch_claims_a_live_owner` answers ownership: a report was never written and the
recorded pid cannot be shown to be gone. That is necessary and not sufficient. A
launched orchestrator can keep its pid while doing nothing at all — no agent
child process, no planner surface, no ledger write — and every progress view that
reads only the pid reports it as running for as long as it sits there.

This module is the missing half. Liveness here means *observable progress*, from
the three things the harness can honestly observe from outside the process:

* a live descendant of the launched orchestrator or of the round owner it started
  — every process either of them spawns is this run's work, so one existing is
  what a dispatch, a gate, or a git operation in flight looks like from outside.
  *Live*, not merely present: a wedged orchestrator's last act is to leave the
  provider it was talking to unreaped, so the un-collected zombie underneath one is
  the parked launch itself rather than evidence against it (`_parent_map`);
* a planner surface — the durable ``last_surface_at`` the channel pacemaker keeps;
* a ledger write — the run's journal, round status, plan, or recorded result.

A launch with none of the three past `PARKED_AFTER_SECONDS` is *parked*, and every
progress view reports it as such rather than as running. The asymmetry of
`runs.process_may_be_live` is preserved deliberately: each unreadable input
resolves toward "still working", so an unreadable ``/proc`` or channel record can
only ever make a parked launch look busy, never a busy one look parked.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path

from .channel import DEFAULT_HEARTBEAT_INTERVAL
from .config import ConfigError
from .journal import JOURNAL_NAME
from .runs import latest_round, launch_claims_a_live_owner, load_mapping

#: How long a launched orchestrator may show no observable progress before every
#: progress view must report it parked. Sourced from the planner-update pacemaker's
#: own default rather than restated: a healthy run surfaces at least that often, so
#: silence past one whole interval is already a missed update, not slow work — and
#: changing that interval must move this threshold with it.
PARKED_AFTER_SECONDS = DEFAULT_HEARTBEAT_INTERVAL

_PROC = Path("/proc")


@dataclass(frozen=True)
class LaunchLiveness:
    """What a progress view may honestly say about one launched orchestrator."""

    #: The launch owns the run: no report was written and its pid may be live.
    active: bool
    #: A live descendant of the orchestrator or of its round owner. Deliberately
    #: any descendant and not a recognised agent binary: the harness spawns its
    #: work through shells, `just`, `uv`, and git, so requiring a known process
    #: name would report real work as absent. Over-counting is the safe direction.
    live_descendant: bool
    #: Seconds since the newest surface or ledger write, or ``None`` when the run
    #: has recorded nothing a view could time.
    idle_seconds: float | None
    threshold: float

    @property
    def parked(self) -> bool:
        """Alive, with no descendant and nothing recorded past the threshold."""
        return (
            self.active
            and not self.live_descendant
            and self.idle_seconds is not None
            and self.idle_seconds >= self.threshold
        )


#: The one process state that is an exit rather than work; see `_parent_map`.
_ZOMBIE = "Z"


def _parent_map() -> dict[int, int] | None:
    """Every readable *live* pid mapped to its parent, or ``None`` without ``/proc``.

    A zombie is excluded, and that exclusion is what lets this module see the launch
    it was written for. A zombie has already exited; the only thing keeping its entry
    in ``/proc`` is that its parent has not collected it — so an orchestrator wedged
    mid-turn leaves exactly one, a dead provider under a process that will never
    reap it, and counting that as work in flight reports the parked launch as busy
    forever. It is not "a dispatch, a gate, or a git operation in flight" by any
    reading; it is the absence of one.

    The asymmetry holds, because it is about inputs this host cannot read: only a
    state this host read *as* ``Z`` is dropped, and a missing or unparsable one
    still counts toward "still working". Nothing is lost by dropping them either —
    a process is orphaned onto init the moment its parent exits, so no live process
    has a zombie ancestor for this walk to reach it through.
    """
    if not _PROC.is_dir():
        return None
    parents: dict[int, int] = {}
    try:
        entries = list(_PROC.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / "status").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # the process exited between the scan and the read
        parent: int | None = None
        state = ""
        for line in status.splitlines():
            if line.startswith("PPid:"):
                _, _, raw = line.partition(":")
                if raw.strip().isdigit():
                    parent = int(raw.strip())
            elif line.startswith("State:"):
                state = next(iter(line.split()[1:2]), "")
        if parent is not None and state != _ZOMBIE:
            parents[int(entry.name)] = parent
    return parents


# llmlint: ignore[changed_behavior_has_e2e] the descendant/no-descendant journeys both run
# through the real `just runs` against real processes in tests/e2e/test_liveness_e2e.py, and the
# uncollected-child one through a whole wedged `just orchestrate` launch in
# tests/e2e/test_attach_settles_e2e.py. Only the absent-`/proc` fallback is unit-only, and
# necessarily: the e2e drives real CLI subprocesses on this host, which cannot run without the
# ``/proc`` this branch requires to be missing. The corrupt-record fallbacks beside it do run
# through that same real journey.
def has_live_descendant(pids: frozenset[int]) -> bool:
    """Whether any live process on this host descends from one of ``pids``.

    ``True`` is the answer to every uncertainty, exactly as in
    `runs.process_may_be_live`: a host without a readable ``/proc`` cannot prove
    that nothing is running underneath, and reporting a busy orchestrator as
    parked is the worse error.
    """
    if not pids:
        return False
    parents = _parent_map()
    if parents is None:
        return True
    for pid, parent in parents.items():
        seen: set[int] = set()
        while parent > 1 and parent not in seen:
            if parent in pids and pid not in pids:
                return True
            seen.add(parent)
            parent = parents.get(parent, 0)
    return False


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _last_surface_at(run_dir: Path) -> float | None:
    """The channel pacemaker's durable last-surface stamp, when it is usable.

    A persisted file any process may write is a trust boundary, and a stamp only
    known to be *numeric* is not yet usable: `NaN` orders inconsistently against
    the other stamps in `max` and makes every idle comparison false, `inf` makes
    the run look eternally fresh, and `-inf` parks it forever. None of the three
    can be honestly timed, so each is discarded exactly like an unreadable record —
    toward "still working", per this module's asymmetry.
    """
    path = run_dir / "channel" / "heartbeat.json"
    if not path.is_file():
        return None
    try:
        value = load_mapping(path).get("last_surface_at")
    except (ConfigError, OSError):
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _round_owner(run_dir: Path) -> tuple[int | None, tuple[Path, ...]]:
    """The in-flight round's recorded owner pid and the files it writes."""
    latest = latest_round(run_dir)
    if latest is None:
        return None, ()
    _, round_dir = latest
    written = (round_dir / "status.json", round_dir / "plan.json", round_dir / "result.json")
    try:
        state = load_mapping(round_dir / "status.json")
    except (ConfigError, OSError):
        return None, written
    pid = state.get("pid")
    if state.get("status") != "running" or not isinstance(pid, int) or isinstance(pid, bool):
        return None, written
    return pid, written


def _launch_pid(run_dir: Path) -> int | None:
    try:
        value = load_mapping(run_dir / "orchestrator" / "status.json").get("pid")
    except (ConfigError, OSError):
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def observe_launch(
    run_dir: Path,
    *,
    parked_after: float = PARKED_AFTER_SECONDS,
    now: float | None = None,
) -> LaunchLiveness:
    """Observe one launched orchestrator's progress, not merely its pid."""
    active = (run_dir / "launch.json").is_file() and launch_claims_a_live_owner(run_dir)
    if not active:
        return LaunchLiveness(False, False, None, parked_after)
    owner_pid, round_files = _round_owner(run_dir)
    pids = {pid for pid in (_launch_pid(run_dir), owner_pid) if pid is not None and pid > 1}
    stamps = [
        stamp
        for stamp in (
            _mtime(run_dir / JOURNAL_NAME),
            _last_surface_at(run_dir),
            *(_mtime(path) for path in round_files),
            _mtime(run_dir / "orchestrator" / "status.json"),
        )
        if stamp is not None
    ]
    newest = max(stamps, default=None)
    current = time.time() if now is None else now
    return LaunchLiveness(
        active=True,
        live_descendant=has_live_descendant(frozenset(pids)),
        idle_seconds=None if newest is None else max(0.0, current - newest),
        threshold=parked_after,
    )


def parked_indicator(
    run_dir: Path,
    *,
    parked_after: float = PARKED_AFTER_SECONDS,
    now: float | None = None,
) -> str | None:
    """One line naming a parked launch, the threshold it passed, and what to do."""
    liveness = observe_launch(run_dir, parked_after=parked_after, now=now)
    if not liveness.parked:
        return None
    idle = int(liveness.idle_seconds or 0.0)
    return (
        f"PARKED (alive with no child process, planner surface, or ledger write for "
        f"{idle // 60}m{idle % 60:02d}s, past the {parked_after:g}s threshold); "
        f"inspect with: just monitor {run_dir.name}"
    )
