"""Shared, crash-safe index of active tracked DAG goals."""

from __future__ import annotations

import argparse
import os
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NewType, NotRequired, Protocol, TypedDict

from .config import ConfigError, load_yaml
from .coordination import advisory_lock, atomic_json, state_root
from .registry import Registry, RegistryEntry, Slug, _target_identity
from .report_contract import ONEJUDGE_REPORT_SCHEMA_VERSIONS
from .runs import RunId, slugify

GoalId = NewType("GoalId", str)
RUNS_INDEX_SCHEMA_VERSION = 1


class Goal(TypedDict):
    id: GoalId
    text: str


class ConcurrentAcknowledgement(TypedDict):
    at: str
    runs: list[str]
    identities: list[str]


class ActiveRun(TypedDict):
    run_id: RunId
    run_dir: str
    goal: Goal | None
    identities: list[str]
    pid: int
    host: str
    started: str
    status: Literal["active"]
    acknowledgements: NotRequired[list[ConcurrentAcknowledgement]]


def normalize_goal(raw: object) -> Goal:
    """Validate one goal mapping and supply the id it may omit.

    The id rule lives here alone: an explicit ``id`` wins, and a goal without one takes
    the slug of its own text. Plan loading normalizes through this on the way in, and
    the read boundary normalizes through it on the way out, because a journal written
    before goals carried an id records `{"text": ...}` verbatim and is served verbatim.
    One goal text must name one goal id on either side of that change, which it cannot
    if each side derives its own.
    """
    if not isinstance(raw, Mapping):
        raise ConfigError("'goal' must be a mapping with non-empty 'text' and optional 'id'")
    unexpected = set(raw) - {"id", "text"}
    if unexpected:
        raise ConfigError("'goal' has unknown field(s): " + ", ".join(sorted(unexpected)))
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ConfigError("'goal.text' must be a non-empty string")
    raw_id = raw.get("id")
    if raw_id is not None and (not isinstance(raw_id, str) or not raw_id.strip()):
        raise ConfigError("'goal.id' must be a non-empty string when provided")
    goal_id = GoalId(raw_id.strip() if isinstance(raw_id, str) else slugify(text))
    return {"id": goal_id, "text": text.strip()}


def parse_goal(data: Mapping[str, Any], *, schema_version: int) -> Goal | None:
    """Validate and normalize the optional versioned plan goal."""
    raw = data.get("goal")
    if raw is None:
        return None
    if schema_version < 4:
        raise ConfigError("'goal' requires schema_version 4; legacy plans must omit the field")
    return normalize_goal(raw)


class RegistryEntries(Protocol):
    entries: dict[Slug, RegistryEntry]


def graph_identities(graph: Any, registry: RegistryEntries | None = None) -> list[str]:
    """Return canonical identities targeted by lifecycle nodes only."""
    selected = registry or Registry()
    return sorted(
        {
            str(_target_identity(selected.entries, node.repo))
            for node in graph.tasks
            if node.lifecycle is not None and node.repo is not None
        }
    )


def _index_path() -> Path:
    return state_root() / "runs-index.json"


# llmlint: ignore[changed_behavior_has_e2e] a second kernel hostname cannot be
# produced through the CLI in the local e2e environment; serialized cross-host
# entries and their retention are covered at the unit boundary.
def _owner_is_provably_dead(entry: Mapping[str, Any]) -> bool:
    if entry.get("host") != socket.gethostname():
        return False
    pid = entry.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid < 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _valid_acknowledgements(value: object) -> bool:
    if value is None:
        return True
    return isinstance(value, list) and all(
        isinstance(item, Mapping)
        and set(item) == {"at", "runs", "identities"}
        and isinstance(item.get("at"), str)
        and bool(item["at"])
        and isinstance(item.get("runs"), list)
        and bool(item["runs"])
        and all(isinstance(run_id, str) and bool(run_id) for run_id in item["runs"])
        and isinstance(item.get("identities"), list)
        and bool(item["identities"])
        and all(isinstance(identity, str) and bool(identity) for identity in item["identities"])
        for item in value
    )


# llmlint: ignore-block[changed_behavior_has_e2e] malformed shared-index states
# cannot be produced through the command surface; the serialized IO boundary is unit-proven.
def _load_active() -> dict[str, ActiveRun]:
    path = _index_path()
    if not path.exists():
        return {}
    raw = load_yaml(path)
    if not isinstance(raw, Mapping):
        raise ConfigError(f"invalid runs index: {path}")
    runs = raw.get("runs")
    if (
        set(raw) != {"schema_version", "runs"}
        or raw.get("schema_version") != RUNS_INDEX_SCHEMA_VERSION
        or not isinstance(runs, dict)
    ):
        raise ConfigError(f"invalid runs index: {path}")
    validated: dict[str, ActiveRun] = {}
    for key, value in runs.items():
        if not isinstance(key, str) or not isinstance(value, Mapping):
            raise ConfigError(f"invalid runs index entry: {key!r}")
        run_id = value.get("run_id")
        run_dir = value.get("run_dir")
        goal = value.get("goal")
        identities = value.get("identities")
        pid = value.get("pid")
        host = value.get("host")
        started = value.get("started")
        status = value.get("status")
        acknowledgements = value.get("acknowledgements")
        if (
            not set(value).issubset(
                {
                    "run_id",
                    "run_dir",
                    "goal",
                    "identities",
                    "pid",
                    "host",
                    "started",
                    "status",
                    "acknowledgements",
                }
            )
            or not isinstance(run_id, str)
            or not run_id
            or run_id != key
            or not isinstance(run_dir, str)
            or not run_dir
            or not Path(run_dir).is_absolute()
            or goal is not None
            and (
                not isinstance(goal, Mapping)
                or set(goal) != {"id", "text"}
                or not isinstance(goal.get("id"), str)
                or not goal.get("id")
                or not isinstance(goal.get("text"), str)
                or not goal.get("text")
            )
            or not isinstance(identities, list)
            or any(not isinstance(identity, str) or not identity for identity in identities)
            or not isinstance(pid, int)
            or isinstance(pid, bool)
            or pid < 1
            or not isinstance(host, str)
            or not host
            or not isinstance(started, str)
            or not started
            or status != "active"
            or not _valid_acknowledgements(acknowledgements)
        ):
            raise ConfigError(f"invalid runs index entry: {key!r}")
        entry: ActiveRun = {
            "run_id": RunId(run_id),
            "run_dir": run_dir,
            "goal": (
                None if goal is None else {"id": GoalId(str(goal["id"])), "text": str(goal["text"])}
            ),
            "identities": identities,
            "pid": pid,
            "host": host,
            "started": started,
            "status": "active",
        }
        if acknowledgements is not None:
            entry["acknowledgements"] = [
                {
                    "at": str(item["at"]),
                    "runs": list(item["runs"]),
                    "identities": list(item["identities"]),
                }
                for item in acknowledgements
            ]
        validated[key] = entry
    return validated


# llmlint: ignore-end[changed_behavior_has_e2e]


def _has_valid_final_report(run_dir: str) -> bool:
    report = Path(run_dir) / "orchestrator" / "report.json"
    if not report.is_file() or report.stat().st_size == 0:
        return False
    try:
        value = load_yaml(report)
    except (ConfigError, OSError):
        return False
    if not isinstance(value, Mapping):
        return False
    transcript = value.get("transcript")
    return (
        value.get("schema_version") in ONEJUDGE_REPORT_SCHEMA_VERSIONS
        and isinstance(transcript, Mapping)
        and isinstance(transcript.get("messages"), list)
        and isinstance(value.get("stopped_early"), bool)
    )


# llmlint: ignore-block[changed_behavior_has_e2e] normal CLI completion cleanup is
# e2e; process-death and corrupt/durable report boundary branches are unit-proven.
def _sweep(runs: dict[str, ActiveRun]) -> None:
    for run_id, entry in list(runs.items()):
        if _has_valid_final_report(entry["run_dir"]) or _owner_is_provably_dead(entry):
            del runs[run_id]


# llmlint: ignore-end[changed_behavior_has_e2e]


#: How a registered run that shares an identity looks from outside its own process.
#: `live` and `parked` both mean the recorded owner still holds its pid here; only
#: `live` means it is doing work. `unobservable` is a registration this host cannot
#: rule on at all — another host's, or one whose owner is gone without the report
#: that would have retired it.
ConcurrentState = Literal["live", "parked", "unobservable"]


@dataclass(frozen=True)
class ConcurrentRun:
    """Another registered run sharing identities with the one being looked at.

    Liveness is observed here and never stored. A recorded "this run was alive"
    is false the instant its process exits, and the whole point of this type is to
    stop a planner reasoning about a machine state that is not the real one.
    """

    run_id: RunId
    goal: str
    identities: tuple[str, ...]
    pid: int
    host: str
    state: ConcurrentState

    @property
    def live(self) -> bool:
        return self.state == "live"

    def describe(self) -> str:
        """One line naming this run, its owner, and what is shared — by state."""
        shared = ", ".join(self.identities)
        match self.state:
            case "live":
                subject = f"run {self.run_id!r} is LIVE (owner pid {self.pid} on {self.host})"
            case "parked":
                subject = (
                    f"run {self.run_id!r} holds pid {self.pid} on {self.host} but shows no "
                    "progress (PARKED)"
                )
            case _:
                subject = (
                    f"run {self.run_id!r} is registered but not observable here "
                    f"(recorded owner pid {self.pid} on {self.host})"
                )
        return f"{subject} goal {self.goal!r}; shared identities: {shared}"


def _owner_state(entry: ActiveRun, parked_after: float | None = None) -> ConcurrentState:
    """Classify one registered owner from what this host can actually observe.

    The entry comes from `_load_active`, which has already validated the owner's
    shape, so this only has to ask what the host can see. A pid it may not signal
    still exists — the same asymmetry `runs.process_may_be_live` keeps — so only a
    pid the kernel says is gone counts as unobservable.

    ``parked_after`` is the caller's own silence threshold, so a view that reports a
    launch parked cannot in the next line report the same launch as a live
    neighbour. It defaults to the pacemaker-derived one every other reader uses.
    """
    from .liveness import PARKED_AFTER_SECONDS, observe_launch

    if entry["host"] != socket.gethostname():
        return "unobservable"
    try:
        os.kill(entry["pid"], 0)
    except ProcessLookupError:
        return "unobservable"
    except PermissionError:
        pass
    run_dir = Path(entry["run_dir"])
    if not (run_dir / "launch.json").is_file():
        # A bare `run-plan` has no launch record to observe progress against, so the
        # owner holding its pid is the whole of the evidence — and it is the same
        # evidence the guard has always refused on.
        return "live"
    threshold = PARKED_AFTER_SECONDS if parked_after is None else parked_after
    return "parked" if observe_launch(run_dir, parked_after=threshold).parked else "live"


def _concurrent(
    runs: Mapping[str, ActiveRun],
    *,
    identities: set[str],
    exclude_dir: Path,
    parked_after: float | None = None,
) -> list[ConcurrentRun]:
    """Every other registered run sharing an identity, classified by observation."""
    found: list[ConcurrentRun] = []
    for other_id, other in runs.items():
        if Path(other["run_dir"]).resolve() == exclude_dir:
            continue
        overlap = tuple(sorted(identities & set(other["identities"])))
        if not overlap:
            continue
        goal = other["goal"]
        found.append(
            ConcurrentRun(
                run_id=RunId(other_id),
                goal=str(goal["text"]) if isinstance(goal, dict) else "(no goal)",
                identities=overlap,
                pid=other["pid"],
                host=other["host"],
                state=_owner_state(other, parked_after),
            )
        )
    return sorted(found, key=lambda item: item.run_id)


def concurrent_runs(run_dir: Path, parked_after: float | None = None) -> list[ConcurrentRun]:
    """Every other registered run sharing an identity with the run at ``run_dir``.

    Read-only, and deliberately unlocked where every other accessor here takes the
    index lock. Those all *write*; this only reads, and the index is replaced
    atomically, so a reader already sees one whole version of it. Queueing behind a
    writer instead would put a bounded wait — up to the lock timeout — inside `just
    status` and `just runs`, which are the views a planner reaches for precisely
    when something else is holding things up.
    """
    absolute = run_dir.resolve()
    runs = _load_active()
    this = next(
        (entry for entry in runs.values() if Path(entry["run_dir"]).resolve() == absolute), None
    )
    if this is None:
        return []
    return _concurrent(
        runs,
        identities=set(this["identities"]),
        exclude_dir=absolute,
        parked_after=parked_after,
    )


def concurrent_indicator(run_dir: Path, parked_after: float | None = None) -> str | None:
    """One line naming the live runs sharing this run's identities, if any.

    Only live ones: a progress view that also listed unobservable registrations
    would report the very thing `--acknowledge-concurrent` exists to launch past as
    though it were a second orchestrator at work.
    """
    try:
        live = [run for run in concurrent_runs(run_dir, parked_after) if run.live]
    except (ConfigError, OSError):
        return None
    if not live:
        return None
    return "CONCURRENT: " + "; ".join(run.describe() for run in live)


def register_run(
    *,
    run_id: str,
    run_dir: Path,
    goal: Goal | None,
    identities: list[str],
    pid: int,
    acknowledge_concurrent: bool,
    report: Callable[[str], None] | None = None,
) -> list[ConcurrentAcknowledgement]:
    """Atomically guard and publish one active run.

    ``report`` receives one notice per genuinely live overlapping run when the
    launch proceeds anyway. `--acknowledge-concurrent` exists to get past a
    registration whose owner is gone; it was never meant to make a second
    orchestrator *at work* on the same identity invisible, which is how two runs
    came to share two checkouts for hours before a hand-traced process tree found
    them.
    """
    absolute_dir = run_dir.resolve()
    with advisory_lock("runs-index"):
        runs = _load_active()
        _sweep(runs)
        concurrent = _concurrent(runs, identities=set(identities), exclude_dir=absolute_dir)
        shared: dict[str, list[str]] = {
            str(item.run_id): list(item.identities) for item in concurrent
        }
        if shared and not acknowledge_concurrent:
            raise ConfigError(
                "concurrent project work refused: "
                + "; ".join(item.describe() for item in concurrent)
                + "; pass --acknowledge-concurrent to proceed"
            )
        for item in concurrent:
            if item.live and report is not None:
                report(
                    f"proceeding alongside a live concurrent run — {item.describe()}; "
                    f"inspect it with: just monitor {item.run_id}"
                )
        now = datetime.now(UTC).isoformat()
        acknowledgements: list[ConcurrentAcknowledgement] = []
        if shared:
            acknowledgements.append(
                {
                    "at": now,
                    "runs": sorted(shared),
                    "identities": sorted(set().union(*map(set, shared.values()))),
                }
            )
        # llmlint: ignore-block[changed_behavior_has_e2e] detached multi-round
        # ownership cannot be driven by standalone run-plan; preservation is unit-proven.
        existing = runs.get(run_id)
        started = existing.get("started", now) if existing else now
        same_run = existing is not None and Path(existing["run_dir"]).resolve() == absolute_dir
        owner_pid = existing["pid"] if same_run and existing is not None else pid
        owner_host = existing["host"] if same_run and existing is not None else socket.gethostname()
        entry: ActiveRun = {
            "run_id": RunId(run_id),
            "run_dir": str(absolute_dir),
            "goal": goal,
            "identities": identities,
            "pid": owner_pid,
            "host": owner_host,
            "started": started,
            "status": "active",
        }
        prior = list(existing.get("acknowledgements", [])) if existing else []
        if prior or acknowledgements:
            entry["acknowledgements"] = [*prior, *acknowledgements]
        # llmlint: ignore-end[changed_behavior_has_e2e]
        runs[run_id] = entry
        atomic_json(_index_path(), {"schema_version": RUNS_INDEX_SCHEMA_VERSION, "runs": runs})
        return [*prior, *acknowledgements] if same_run else acknowledgements


def finish_run(run_id: str, run_dir: Path) -> None:
    """Remove a run after its report has been durably recorded."""
    with advisory_lock("runs-index"):
        runs = _load_active()
        entry = runs.get(run_id)
        if entry is not None and Path(entry["run_dir"]).resolve() == run_dir.resolve():
            del runs[run_id]
        _sweep(runs)
        atomic_json(_index_path(), {"schema_version": RUNS_INDEX_SCHEMA_VERSION, "runs": runs})


# llmlint: ignore[changed_behavior_has_e2e] the detached-process handoff is an
# internal launch transition; its successful and missing-reservation paths are unit-proven.
def update_run_owner(run_id: str, run_dir: Path, pid: int) -> None:
    """Transfer a launch reservation to its detached orchestrator process."""
    with advisory_lock("runs-index"):
        runs = _load_active()
        entry = runs.get(run_id)
        if entry is None or Path(entry["run_dir"]).resolve() != run_dir.resolve():
            raise ConfigError(f"active run reservation disappeared for {run_id!r}")
        entry["pid"] = pid
        entry["host"] = socket.gethostname()
        atomic_json(_index_path(), {"schema_version": RUNS_INDEX_SCHEMA_VERSION, "runs": runs})


def sweep_and_list_active_runs() -> list[ActiveRun]:
    """Read active runs, sweeping only owners proven dead."""
    with advisory_lock("runs-index"):
        runs = _load_active()
        _sweep(runs)
        atomic_json(_index_path(), {"schema_version": RUNS_INDEX_SCHEMA_VERSION, "runs": runs})
        return [runs[key] for key in sorted(runs)]


def find_active_run(run_id: str) -> ActiveRun | None:
    """Resolve one run through the central active-runs index."""
    return next(
        (entry for entry in sweep_and_list_active_runs() if entry["run_id"] == run_id), None
    )


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="List active DAG goals across projects.").parse_args(argv)
    rows = sweep_and_list_active_runs()
    if not rows:
        print("No active DAG goals.")
        return 0
    for row in rows:
        goal = row["goal"]
        label = f"{goal['id']}: {goal['text']}" if goal else "(no goal)"
        identities = ", ".join(row["identities"]) or "(none)"
        # An index entry is a registration, not a running process. Saying which it is
        # here is what separates "another run is working this identity" from "a dead
        # run is still registered" — the two that read identically before.
        state = _owner_state(row)
        owner = f"{state} (owner pid {row['pid']} on {row['host']})"
        print(f"{row['run_id']}  {label}")
        print(f"    identities: {identities}")
        print(f"    owner: {owner}")
        print(f"    run dir: {row['run_dir']}")
    return 0
