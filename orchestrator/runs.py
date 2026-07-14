"""Persistent run ledger for repo-plan lifecycle rounds."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple, NewType, TypedDict, cast

from .config import ConfigError, load_yaml

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ROUND = re.compile(r"^round-(\d+)$")

RunId = NewType("RunId", str)


class RunLedgerRow(NamedTuple):
    """Summary of the latest completed round for one recorded run."""

    run_id: str
    round: int
    summary: str


class RepoPlanResultItem(TypedDict, total=False):
    """Stable recorded fields for one repo-plan node."""

    status: str
    repo: str
    branch: str
    base_branch: str
    outcome: str
    ok: bool
    pr: str | None
    detail: str
    error: str | None


class RepoPlanPayload(TypedDict):
    """The JSON payload emitted and recorded by repo-plan."""

    ok: bool
    started_order: list[str]
    results: dict[str, RepoPlanResultItem]


def validate_run_id(run_id: str) -> RunId:
    """Validate a run id before using it as a directory name."""
    if run_id in {".", ".."} or not _RUN_ID.fullmatch(run_id):
        raise ConfigError(
            "run id must contain only letters, numbers, '.', '_', or '-' and cannot be '.' or '..'"
        )
    return RunId(run_id)


def slugify(value: str) -> str:
    """Convert a plan name or filename stem to a safe, non-empty run id base."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-_")
    return slug or "repo-plan"


def resolve_run_dir(
    runs_dir: Path, plan: dict[str, Any], plan_path: Path, run_id: str | None
) -> Path:
    """Resolve an explicit run or create a unique id for a fresh invocation."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    if run_id is not None:
        return runs_dir / validate_run_id(run_id)
    name = plan.get("name")
    base = slugify(name if isinstance(name, str) and name.strip() else plan_path.stem)
    candidate = runs_dir / base
    if not candidate.exists():
        return candidate
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return runs_dir / f"{base}-{stamp}"


def latest_round(run_dir: Path) -> tuple[int, Path] | None:
    """Return the highest numbered round directory, ignoring unrelated entries."""
    found = (
        [
            (int(match.group(1)), entry)
            for entry in run_dir.iterdir()
            if entry.is_dir() and (match := _ROUND.fullmatch(entry.name))
        ]
        if run_dir.is_dir()
        else []
    )
    return max(found, default=None, key=lambda item: item[0])


def write_next_plan(run_dir: Path, plan: dict[str, Any]) -> tuple[int, Path]:
    """Create the next numbered round and persist its exact plan mapping."""
    latest = latest_round(run_dir)
    number = 1 if latest is None else latest[0] + 1
    round_dir = run_dir / f"round-{number:02d}"
    round_dir.mkdir(parents=True, exist_ok=False)
    _write_json(round_dir / "plan.json", plan)
    return number, round_dir


def prepare_round(run_dir: Path, plan: dict[str, Any]) -> tuple[int, Path]:
    """Use a pending plan-only round when identical, otherwise create the next round."""
    latest = latest_round(run_dir)
    if latest is not None:
        number, round_dir = latest
        if not (round_dir / "result.json").exists():
            existing = load_mapping(round_dir / "plan.json")
            if existing != plan:
                raise ConfigError(f"{round_dir} has a pending different plan")
            return number, round_dir
    return write_next_plan(run_dir, plan)


def write_result(round_dir: Path, result: Mapping[str, Any]) -> None:
    """Persist a repo-plan JSON result for an already-created round."""
    path = round_dir / "result.json"
    if path.exists():
        raise ConfigError(f"round already has a result: {path}")
    _write_json(path, result)


def load_mapping(path: Path) -> dict[str, Any]:
    """Load a JSON/YAML mapping used by the ledger."""
    return load_yaml(path)


def status_counts(result: RepoPlanPayload) -> Counter[str]:
    """Count per-node statuses in a repo-plan result payload."""
    return Counter(item.get("status", "unknown") for item in result["results"].values())


def status_summary(result: RepoPlanPayload) -> str:
    """Render stable done/failed/skipped counts, plus any unexpected statuses."""
    counts = status_counts(result)
    keys = ["done", "failed", "skipped"]
    keys.extend(sorted(set(counts) - set(keys)))
    return ", ".join(f"{counts[key]} {key}" for key in keys)


def list_runs(runs_dir: Path) -> list[RunLedgerRow]:
    """List runs that have at least one completed round."""
    if not runs_dir.is_dir():
        return []
    rows: list[RunLedgerRow] = []
    for run_dir in sorted(entry for entry in runs_dir.iterdir() if entry.is_dir()):
        completed = [
            (number, path) for number, path in _rounds(run_dir) if (path / "result.json").exists()
        ]
        if not completed:
            continue
        latest = max(completed, key=lambda item: item[0])
        rows.append(
            RunLedgerRow(
                run_id=run_dir.name,
                round=latest[0],
                summary=status_summary(_as_result_payload(load_mapping(latest[1] / "result.json"))),
            )
        )
    return rows


def _rounds(run_dir: Path) -> list[tuple[int, Path]]:
    return [
        (int(match.group(1)), entry)
        for entry in run_dir.iterdir()
        if entry.is_dir() and (match := _ROUND.fullmatch(entry.name))
    ]


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _as_result_payload(value: dict[str, Any]) -> RepoPlanPayload:
    """Validate the stable portion needed when reading a recorded result."""
    ok = value.get("ok")
    started = value.get("started_order")
    results = value.get("results")
    if (
        not isinstance(ok, bool)
        or not isinstance(started, list)
        or not all(isinstance(item, str) for item in started)
        or not isinstance(results, dict)
        or not all(isinstance(key, str) and isinstance(item, dict) for key, item in results.items())
        or not all(isinstance(item.get("status"), str) for item in results.values())
    ):
        raise ConfigError("recorded result has an invalid repo-plan payload")
    return cast(RepoPlanPayload, value)
