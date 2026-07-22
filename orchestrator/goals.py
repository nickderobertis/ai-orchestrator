"""Shared, crash-safe index of active tracked DAG goals."""

from __future__ import annotations

import argparse
import os
import socket
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NewType, NotRequired, Protocol, TypedDict

from .config import ConfigError, load_yaml
from .coordination import advisory_lock, atomic_json, state_root
from .registry import Registry, RegistryEntry, Slug, _target_identity
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


def parse_goal(data: Mapping[str, Any], *, schema_version: int) -> Goal | None:
    """Validate and normalize the optional versioned plan goal."""
    raw = data.get("goal")
    if raw is None:
        return None
    if schema_version < 4:
        raise ConfigError("'goal' requires schema_version 4; legacy plans must omit the field")
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
    transcript = value.get("transcript")
    return (
        value.get("schema_version") in {4, 5}
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


def register_run(
    *,
    run_id: str,
    run_dir: Path,
    goal: Goal | None,
    identities: list[str],
    pid: int,
    acknowledge_concurrent: bool,
) -> list[ConcurrentAcknowledgement]:
    """Atomically guard and publish one active run."""
    absolute_dir = run_dir.resolve()
    with advisory_lock("runs-index"):
        runs = _load_active()
        _sweep(runs)
        shared: dict[str, list[str]] = {}
        wanted = set(identities)
        for other_id, other in runs.items():
            if Path(other.get("run_dir", "")).resolve() == absolute_dir:
                continue
            overlap = sorted(wanted & set(other.get("identities", [])))
            if overlap:
                shared[other_id] = overlap
        if shared and not acknowledge_concurrent:
            details = []
            for other_id, overlap in shared.items():
                other_goal = runs[other_id].get("goal")
                label = other_goal.get("text") if isinstance(other_goal, dict) else "(no goal)"
                details.append(
                    f"run {other_id!r} goal {label!r}; shared identities: {', '.join(overlap)}"
                )
            raise ConfigError(
                "concurrent project work refused: "
                + "; ".join(details)
                + "; pass --acknowledge-concurrent to proceed"
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
        print(f"{row['run_id']}  {label}")
        print(f"    identities: {identities}")
        print(f"    run dir: {row['run_dir']}")
    return 0
