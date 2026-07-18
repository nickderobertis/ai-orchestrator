"""Persistent run ledger for tracked graph rounds."""

from __future__ import annotations

import os
import re
import socket
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NamedTuple, NewType, NotRequired, TypedDict, cast

from .config import ConfigError, load_yaml
from .coordination import advisory_lock, atomic_json
from .merge import MergePolicy
from .workspace import IdentityKey, RepositoryType, Workflow

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ROUND = re.compile(r"^round-(\d+)$")

# The identifiers a tracked round is addressed by. They are all non-empty strings
# from different namespaces, and they travel together through the ledger, the
# journal, and the history labels — often as adjacent arguments of one call. Naming
# them apart makes passing a step id where a node id belongs a type error instead
# of a plausible-looking recorded line that nothing would ever contradict.
RunId = NewType("RunId", str)
NodeId = NewType("NodeId", str)
StepId = NewType("StepId", str)


class RunLedgerRow(NamedTuple):
    """Summary of the latest completed round for one recorded run."""

    run_id: str
    round: int
    summary: str


class StackBasePayload(TypedDict):
    """Stable serialized form of one typed lifecycle stack anchor."""

    branch: str
    repo: str | None
    identity: IdentityKey | None
    base_branch: str | None
    pr: str | None
    pr_base: NotRequired[str | None]


class ResumePayload(TypedDict):
    """Where a human-gated workstream paused, as recorded for a later round."""

    branch: str
    base_branch: str
    pr_base: str
    checkpoint: str
    completed_steps: list[str]
    pr: str | None
    mode: NotRequired[str]
    source_round: NotRequired[int]


class HumanActionPayload(TypedDict):
    """One ready human action and what completing it releases."""

    ref: str
    task: str
    unblocks: list[str]
    unblocks_publication: bool


class StepResultPayload(TypedDict):
    """One workstream step's recorded outcome."""

    id: str
    kind: str
    persona: str | None
    status: str


class RetryLineagePayload(TypedDict):
    """Serialized fate of a preserved-branch retry."""

    supersedes_branch: str
    supersedes_checkpoint: str
    disposition: Literal["reused", "recovered", "abandoned"]
    reason: NotRequired[str]
    supersedes_round: NotRequired[int]


class GraphResultItem(TypedDict, total=False):
    """Stable recorded fields for one tracked-graph node."""

    kind: str
    status: str
    task: str
    unblocks: list[str]
    blocked_by: list[str]
    human_actions: list[HumanActionPayload]
    completed: bool
    exit_code: int | None
    verdicts: list[Any]
    usage: dict[str, Any]
    repo: str
    branch: str
    base_branch: str
    pr_base: str
    synthetic_stack_base: str | None
    stack_bases: list[StackBasePayload]
    repository_type: RepositoryType | None
    repo_type: RepositoryType | None
    publication_workflow: Workflow | None
    workflow: Workflow | None
    merge_policy: MergePolicy | None
    outcome: str
    ok: bool
    pr: str | None
    detail: str
    follow_ups: str | None
    steps: list[StepResultPayload]
    waiting_steps: list[str]
    resume: ResumePayload | None
    error: str | None
    retry_lineage: RetryLineagePayload


class GraphPayload(TypedDict, total=False):
    """The JSON payload emitted and recorded by run-plan."""

    ok: bool
    state: str
    started_order: list[str]
    results: dict[str, GraphResultItem]
    schema_version: int
    round: int


RepoPlanResultItem = GraphResultItem
RepoPlanPayload = GraphPayload


class HumanCompletion(TypedDict):
    """One attested human action."""

    ref: str
    round: int
    completed_at: str


class RoundStatus(TypedDict, total=False):
    status: Literal["running", "completed"]
    pid: int
    host: str
    started: str
    finished: str


def _round_status(status: Literal["running", "completed"]) -> RoundStatus:
    timestamp = datetime.now(UTC).isoformat()
    record = RoundStatus(status=status, pid=os.getpid(), host=socket.gethostname())
    record["started" if status == "running" else "finished"] = timestamp
    return record


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
    with advisory_lock(f"ledger:{run_dir.resolve()}"):
        latest = latest_round(run_dir)
        number = 1 if latest is None else latest[0] + 1
        round_dir = run_dir / f"round-{number:02d}"
        round_dir.mkdir(parents=True, exist_ok=False)
        _write_json(round_dir / "plan.json", plan)
        return number, round_dir


def prepare_round(
    run_dir: Path, plan: dict[str, Any], *, recover: bool = False
) -> tuple[int, Path]:
    """Use a pending plan-only round when identical, otherwise create the next round."""
    with advisory_lock(f"ledger:{run_dir.resolve()}"):
        latest = latest_round(run_dir)
        if latest is not None:
            number, round_dir = latest
            if not (round_dir / "result.json").exists():
                existing = load_mapping(round_dir / "plan.json")
                if existing != plan:
                    raise ConfigError(f"{round_dir} has a pending different plan")
                state_path = round_dir / "status.json"
                if state_path.exists():
                    state = load_mapping(state_path)
                    if not recover or _owner_is_live(state):
                        action = (
                            "the recorded owner is still alive; recovery refused"
                            if recover
                            else "inspect its worktrees, then use --recover if its owner is gone"
                        )
                        raise ConfigError(f"{round_dir} is already running ({state}); {action}")
                atomic_json(state_path, _round_status("running"))
                return number, round_dir
        number = 1 if latest is None else latest[0] + 1
        round_dir = run_dir / f"round-{number:02d}"
        round_dir.mkdir(parents=True, exist_ok=False)
        _write_json(round_dir / "plan.json", plan)
        atomic_json(round_dir / "status.json", _round_status("running"))
        return number, round_dir


def _owner_is_live(state: Mapping[str, Any]) -> bool:
    """Conservatively identify a recorded owner on this host."""
    pid = state.get("pid")
    host = state.get("host")
    if (
        state.get("status") != "running"
        or not isinstance(host, str)
        or not isinstance(pid, int)
        or pid < 1
    ):
        raise ConfigError("running round has invalid owner metadata; recovery refused")
    if host != socket.gethostname():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def write_result(round_dir: Path, result: Mapping[str, Any]) -> None:
    """Persist a repo-plan JSON result for an already-created round."""
    with advisory_lock(f"ledger:{round_dir.parent.resolve()}"):
        path = round_dir / "result.json"
        if path.exists():
            raise ConfigError(f"round already has a result: {path}")
        _write_json(path, result)
        atomic_json(round_dir / "status.json", _round_status("completed"))


def load_mapping(path: Path) -> dict[str, Any]:
    """Load a JSON/YAML mapping used by the ledger."""
    return load_yaml(path)


def status_counts(result: GraphPayload) -> Counter[str]:
    """Count per-node statuses in a tracked-graph result payload."""
    return Counter(item.get("status", "unknown") for item in result["results"].values())


def result_state(result: GraphPayload) -> str:
    """Return recorded state, or derive it for older payloads."""
    state = result.get("state")
    if isinstance(state, str) and state:
        return state
    statuses = set(status_counts(result))
    if statuses & {"failed", "skipped"}:
        return "failed"
    if statuses & {"waiting", "blocked"}:
        return "waiting"
    return "complete"


def human_actions(result: GraphPayload) -> list[HumanActionPayload]:
    """Every ready human action in node order."""
    return [
        action
        for node_id, item in result["results"].items()
        for action in _validated_human_actions(node_id, item.get("human_actions"))
    ]


def status_summary(result: GraphPayload) -> str:
    """Render stable per-status counts, waiting actions, and follow-ups."""
    counts = status_counts(result)
    keys = ["done", "waiting", "blocked", "failed", "skipped"]
    keys.extend(sorted(set(counts) - set(keys)))
    summary = ", ".join(f"{counts[key]} {key}" for key in keys if key in counts)
    waiting = [
        f"{action['ref']}: {_first_line(action['task'])} -> {_downstream(action)}"
        for action in human_actions(result)
    ]
    if waiting:
        summary += "; awaiting " + " | ".join(waiting)
    follow_ups = [
        f"{node_id}: {follow_up}"
        for node_id, item in result["results"].items()
        if isinstance((follow_up := item.get("follow_ups")), str) and follow_up.strip()
    ]
    if follow_ups:
        summary += "; follow-ups: " + " | ".join(follow_ups)
    return summary


def _first_line(task: str) -> str:
    line = task.strip().splitlines()[0] if task.strip() else ""
    return line[:60] + ("..." if len(line) > 60 else "")


def _downstream(action: HumanActionPayload) -> str:
    if action.get("unblocks"):
        return "unblocks " + ", ".join(action["unblocks"])
    if action.get("unblocks_publication"):
        return "unblocks workstream publication"
    return "unblocks nothing downstream"


def _validated_human_actions(node_id: str, raw: object) -> list[HumanActionPayload]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError(f"recorded result has invalid human_actions for {node_id}")
    for action in raw:
        if (
            not isinstance(action, dict)
            or not isinstance(action.get("ref"), str)
            or not isinstance(action.get("task"), str)
            or not isinstance(action.get("unblocks"), list)
            or not all(isinstance(ref, str) for ref in action.get("unblocks", []))
            or not isinstance(action.get("unblocks_publication"), bool)
        ):
            raise ConfigError(f"recorded result has invalid human_actions for {node_id}")
    return cast(list[HumanActionPayload], raw)


def load_completions(run_dir: Path) -> list[HumanCompletion]:
    """Read every human completion attestation for a run."""
    path = run_dir / "humans.json"
    if not path.exists():
        return []
    data = load_mapping(path)
    raw = data.get("completions")
    if not isinstance(raw, list) or not all(
        isinstance(item, dict)
        and isinstance(item.get("ref"), str)
        and isinstance(item.get("round"), int)
        and isinstance(item.get("completed_at"), str)
        for item in raw
    ):
        raise ConfigError(f"{path} has an invalid human-completion ledger")
    return cast(list[HumanCompletion], raw)


def record_completions(run_dir: Path, refs: list[str], *, round_number: int) -> None:
    """Append human attestations to the durable ledger."""
    if len(set(refs)) != len(refs):
        raise ConfigError("human task completion refs must be unique")
    with advisory_lock(f"ledger:{run_dir.resolve()}"):
        existing = load_completions(run_dir)
        known = {item["ref"] for item in existing}
        if repeated := [ref for ref in refs if ref in known]:
            raise ConfigError(f"human task(s) already completed: {', '.join(sorted(repeated))}")
        stamp = datetime.now(UTC).isoformat()
        appended = existing + [
            HumanCompletion(ref=ref, round=round_number, completed_at=stamp) for ref in refs
        ]
        atomic_json(run_dir / "humans.json", {"completions": appended})


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


def rounds(run_dir: Path) -> list[tuple[int, Path]]:
    """Every numbered round of a run, oldest first; empty for a non-run directory.

    The round-directory naming is this module's to know. A reader that walks the
    ledger — rather than only asking it for the `latest_round` — gets it from here
    so a second spelling of ``round-NN`` cannot drift out of step with the writer's.
    """
    return sorted(_rounds(run_dir)) if run_dir.is_dir() else []


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    atomic_json(path, value)


def as_result_payload(value: dict[str, Any]) -> GraphPayload:
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
        raise ConfigError("recorded result has an invalid tracked-graph payload")
    for node_id, item in results.items():
        _validated_human_actions(node_id, item.get("human_actions"))
    return cast(GraphPayload, value)


_as_result_payload = as_result_payload
