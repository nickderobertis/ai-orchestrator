"""Shared, crash-safe index of active tracked DAG goals."""

from __future__ import annotations

import argparse
import os
import socket
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from .config import ConfigError, load_yaml
from .coordination import advisory_lock, atomic_json, state_root
from .registry import Registry, _target_identity
from .runs import slugify


class Goal(TypedDict):
    id: str
    text: str


class ConcurrentAcknowledgement(TypedDict):
    at: str
    runs: list[str]
    identities: list[str]


class ActiveRun(TypedDict):
    run_id: str
    run_dir: str
    goal: Goal | None
    identities: list[str]
    pid: int
    host: str
    started: str
    status: str
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
    goal_id = raw_id.strip() if isinstance(raw_id, str) else slugify(text)
    return {"id": goal_id, "text": text.strip()}


def graph_identities(graph: Any, registry: Registry | None = None) -> list[str]:
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


def _load_active() -> dict[str, ActiveRun]:
    path = _index_path()
    if not path.exists():
        return {}
    raw = load_yaml(path)
    runs = raw.get("runs")
    if raw.get("schema_version") != 1 or not isinstance(runs, dict):
        raise ConfigError(f"invalid runs index: {path}")
    return {str(key): value for key, value in runs.items() if isinstance(value, dict)}  # type: ignore[misc]


def _sweep(runs: dict[str, ActiveRun]) -> None:
    for run_id, entry in list(runs.items()):
        report = Path(entry.get("run_dir", "")) / "orchestrator" / "report.json"
        if (report.is_file() and report.stat().st_size > 0) or _owner_is_provably_dead(entry):
            del runs[run_id]


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
        existing = runs.get(run_id)
        started = existing.get("started", now) if existing else now
        same_run = existing is not None and Path(existing["run_dir"]).resolve() == absolute_dir
        owner_pid = existing["pid"] if same_run and existing is not None else pid
        owner_host = existing["host"] if same_run and existing is not None else socket.gethostname()
        entry: ActiveRun = {
            "run_id": run_id,
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
        runs[run_id] = entry
        atomic_json(_index_path(), {"schema_version": 1, "runs": runs})
        return [*prior, *acknowledgements] if same_run else acknowledgements


def finish_run(run_id: str, run_dir: Path) -> None:
    """Remove a run after its report has been durably recorded."""
    with advisory_lock("runs-index"):
        runs = _load_active()
        entry = runs.get(run_id)
        if entry is not None and Path(entry["run_dir"]).resolve() == run_dir.resolve():
            del runs[run_id]
        _sweep(runs)
        atomic_json(_index_path(), {"schema_version": 1, "runs": runs})


def update_run_owner(run_id: str, run_dir: Path, pid: int) -> None:
    """Transfer a launch reservation to its detached orchestrator process."""
    with advisory_lock("runs-index"):
        runs = _load_active()
        entry = runs.get(run_id)
        if entry is None or Path(entry["run_dir"]).resolve() != run_dir.resolve():
            raise ConfigError(f"active run reservation disappeared for {run_id!r}")
        entry["pid"] = pid
        entry["host"] = socket.gethostname()
        atomic_json(_index_path(), {"schema_version": 1, "runs": runs})


def active_runs() -> list[ActiveRun]:
    """Read active runs, sweeping only owners proven dead."""
    with advisory_lock("runs-index"):
        runs = _load_active()
        _sweep(runs)
        atomic_json(_index_path(), {"schema_version": 1, "runs": runs})
        return [runs[key] for key in sorted(runs)]


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="List active DAG goals across projects.").parse_args(argv)
    rows = active_runs()
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
