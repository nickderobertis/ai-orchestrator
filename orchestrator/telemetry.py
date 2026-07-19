"""One versioned machine-readable index over every recorded run source."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypedDict, cast

from .detail_snapshot import CheckRollup
from .history import (
    HistoryError,
    HistorySession,
    SessionRole,
    all_sessions,
    session_records,
    session_role,
)
from .journal import JOURNAL_NAME, Event, read_events
from .monitor import DetailSnapshot, load_snapshot, run_state
from .runs import (
    RETRY_DISPOSITIONS,
    GraphResultItem,
    RetryDisposition,
    RunId,
    as_result_payload,
    latest_round,
    load_mapping,
    result_state,
    result_state_is_terminal,
)
from .verify import GateAttestation

TELEMETRY_SCHEMA_VERSION = 2
SUPPORTED_HISTORY_SCHEMA_VERSIONS = ("0.2", "0.3", 1, 2)
TelemetryQuality = Literal["complete", "partial", "legacy"]
TelemetrySource = Literal["onejudge", "oneharness", "history_legacy", "journal_legacy"]
FailureClass = Literal[
    "agent", "gate", "checks", "publication", "timeout", "provider", "configuration", "unknown"
]


class TimingRecord(TypedDict):
    agent_seconds: float
    judge_seconds: float
    gate_seconds: float
    publication_wait_seconds: float
    wall_seconds: float
    agent_model_ms: int
    judge_model_ms: int
    tool_ms: int
    idle_orchestration_ms: int
    unattributed_ms: int
    wall_ms: int
    fractions: FractionsRecord


class FractionsRecord(TypedDict):
    agent_model: float
    judge_model: float
    tool: float
    idle_orchestration: float


class UsageValues(TypedDict):
    input_tokens: int | float | None
    output_tokens: int | float | None
    cache_read_tokens: int | float | None
    cache_write_tokens: int | float | None
    cost_usd: int | float | None


class UsageRecord(TypedDict):
    agent: UsageValues
    judge: UsageValues
    total: UsageValues


UsageKey = Literal[
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "cost_usd"
]


class SessionLink(TypedDict):
    session_id: str
    history_id: str | None
    role: SessionRole
    turn_index: int | None


class HistoryRecord(TypedDict, total=False):
    schema_version: str | int
    duration_ms: int
    model_ms: int
    tool_ms: int
    started_at: str
    finished_at: str
    usage: dict[str, object]
    events: list[object]


class MetricsRecord(TypedDict):
    retry_attempts: int
    retry_branch_reuses: int
    recovered_branches: int
    abandoned_branches: int
    no_diff_dispatches: int
    green_to_publication_seconds: list[float]
    turns: dict[int, int]
    usage: UsageValues


class NodeWorkRecord(TypedDict):
    agent_model_ms: int
    judge_model_ms: int
    tool_ms: int
    wall_ms: int


@dataclass(frozen=True)
class Failure:
    classification: FailureClass
    detail: str = ""

    def record(self) -> dict[str, object]:
        result: dict[str, object] = {"class": self.classification}
        if self.detail:
            result["detail"] = self.detail
        return result


@dataclass(frozen=True)
class Provider:
    provider: str
    harness: str = ""
    model: str = ""

    def record(self) -> dict[str, str]:
        result = {"provider": self.provider}
        if self.harness:
            result["harness"] = self.harness
        if self.model:
            result["model"] = self.model
        return result


@dataclass(frozen=True)
class RetryLineageTelemetry:
    supersedes_branch: str
    supersedes_checkpoint: str
    disposition: RetryDisposition

    @classmethod
    def from_value(cls, value: object) -> RetryLineageTelemetry | None:
        if not isinstance(value, dict):
            return None
        branch, checkpoint, disposition = (
            value.get("supersedes_branch"),
            value.get("supersedes_checkpoint"),
            value.get("disposition"),
        )
        if not (
            isinstance(branch, str)
            and branch
            and isinstance(checkpoint, str)
            and checkpoint
            and isinstance(disposition, str)
            and disposition in RETRY_DISPOSITIONS
        ):
            return None
        return cls(branch, checkpoint, cast(RetryDisposition, disposition))

    def record(self) -> dict[str, str]:
        return {
            "supersedes_branch": self.supersedes_branch,
            "supersedes_checkpoint": self.supersedes_checkpoint,
            "disposition": self.disposition,
        }


@dataclass(frozen=True)
class NodeTelemetry:
    node: str
    status: str
    outcome: str = ""
    branch: str = ""
    comparison_remote: str = ""
    comparison_base: str = ""
    checkpoint: str = ""
    commit: str = ""
    retry_lineage: RetryLineageTelemetry | None = None
    gate_attestation: GateAttestation | None = None
    timing: TimingRecord | None = None
    usage: UsageRecord | None = None
    sessions: list[SessionLink] = field(default_factory=list)
    tool_commands: dict[str, int] = field(default_factory=dict)
    turns: int = 0

    def record(self) -> dict[str, object]:
        result: dict[str, object] = {"node": self.node, "status": self.status}
        for name in (
            "outcome",
            "branch",
            "comparison_remote",
            "comparison_base",
            "checkpoint",
            "commit",
        ):
            if value := getattr(self, name):
                result[name] = value
        if self.retry_lineage:
            result["retry_lineage"] = self.retry_lineage.record()
        if self.gate_attestation:
            result["gate_attestation"] = self.gate_attestation.to_record()
        if self.timing is not None:
            result["timing"] = self.timing
        if self.usage is not None:
            result["usage"] = self.usage
        result["sessions"] = self.sessions
        if self.tool_commands:
            result["tool_commands"] = self.tool_commands
        result["turns"] = self.turns
        return result


@dataclass
class RunTelemetry:
    run_id: RunId
    state: str
    phase: str
    last_progress_at: float | None
    last_event: str
    timing: TimingRecord
    nodes: list[NodeTelemetry] = field(default_factory=list)
    providers: list[Provider] = field(default_factory=list)
    failure: Failure | None = None
    check_rollup: CheckRollup = field(default_factory=CheckRollup)
    green_to_publication_seconds: list[float] = field(default_factory=list)
    usage: UsageRecord = field(default_factory=lambda: cast(UsageRecord, {}))
    telemetry_quality: TelemetryQuality = "legacy"
    sources: list[TelemetrySource] = field(default_factory=list)
    node_work_ms: NodeWorkRecord = field(default_factory=lambda: cast(NodeWorkRecord, {}))
    turns: int = 0
    tool_commands: dict[str, int] = field(default_factory=dict)

    def record(self) -> dict[str, object]:
        result: dict[str, object] = {
            "run_id": self.run_id,
            "state": self.state,
            "phase": self.phase,
            "last_event": self.last_event,
            "timing": self.timing,
            "nodes": [node.record() for node in self.nodes],
            "usage": self.usage,
            "telemetry_quality": self.telemetry_quality,
            "sources": self.sources,
            "node_work_ms": self.node_work_ms,
            "turns": self.turns,
        }
        if self.last_progress_at is not None:
            result["last_progress_at"] = self.last_progress_at
        if self.providers:
            result["providers"] = [provider.record() for provider in self.providers]
        if self.failure:
            result["failure"] = self.failure.record()
        if rollup := self.check_rollup.to_record():
            result["check_rollup"] = rollup
        return result


def _phase(event: Event | None, state: str) -> str:
    if event is None:
        return state
    return {
        "verification-started": "gate",
        "verification-finished": "publication" if event.detail.get("ok") else "failed",
        "pr-created": "check-waiting",
        "pr-checks-observed": "check-waiting",
        "pr-merged": "published",
        "publication-finished": "published",
        "human-waiting": "human-waiting",
        "node-started": "agent",
        "step-started": "agent",
    }.get(event.kind, state)


def _failure(item: GraphResultItem) -> Failure | None:
    outcome = str(item.get("outcome", ""))
    detail = str(item.get("detail") or item.get("error") or "")
    if item.get("status") not in {"failed", "not-completed"}:
        return None
    if "timeout" in outcome or "timed out" in detail.lower():
        kind: FailureClass = "timeout"
    elif "gate" in outcome:
        kind = "gate"
    elif "checks" in outcome:
        kind = "checks"
    elif "publication" in outcome or outcome in {"closed", "error"}:
        kind = "publication"
    elif "unexpected runtime failure" in detail.lower():
        kind = "unknown"
    elif detail.lower().endswith("bad config"):
        kind = "configuration"
    elif "provider" in detail.lower():
        kind = "provider"
    elif "config" in detail.lower():
        kind = "configuration"
    elif not outcome:
        kind = "agent"
    else:
        kind = "unknown"
    return Failure(kind, detail)


def _gate_seconds(events: list[Event]) -> float:
    starts: dict[tuple[int, str, str], float] = {}
    total = 0.0
    for event in events:
        key = (event.round, str(event.node or ""), str(event.step or ""))
        if event.kind == "verification-started":
            starts[key] = event.at
        elif event.kind == "verification-finished" and key in starts:
            total += max(0.0, event.at - starts.pop(key))
    return total


def _publication_waits(events: list[Event]) -> list[float]:
    """Pair each green gate with publication without subtracting overlapping work."""
    green: dict[str, float] = {}
    waits: list[float] = []
    for event in events:
        node = str(event.node or "")
        if event.kind == "verification-finished" and event.detail.get("ok") is True:
            green[node] = event.at
        elif event.kind == "publication-finished" and node in green:
            waits.append(max(0.0, event.at - green.pop(node)))
    return waits


def _publication_wait_seconds(events: list[Event], *, active_at: float | None) -> float:
    green: dict[str, float] = {}
    completed = 0.0
    for event in events:
        node = str(event.node or "")
        if event.kind == "verification-finished" and event.detail.get("ok") is True:
            green[node] = event.at
        elif event.kind == "publication-finished" and node in green:
            completed += max(0.0, event.at - green.pop(node))
    if active_at is None:
        return completed
    return completed + sum(max(0.0, active_at - started) for started in green.values())


USAGE_FIELDS: tuple[UsageKey, ...] = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cost_usd",
)


@dataclass(frozen=True)
class _SessionSummary:
    role: Literal["agent", "judge"]
    labels: dict[str, str]
    link: SessionLink
    turns: int
    duration_ms: int
    model_ms: int
    tool_ms: int
    usage: UsageValues
    commands: dict[str, int]
    interval_complete: bool


def _number(value: object) -> int | float | None:
    return (
        value
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
        else None
    )


def _non_negative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _utc_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.endswith(("Z", "+00:00")):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (
        parsed
        if parsed.tzinfo is not None and parsed.utcoffset() == UTC.utcoffset(parsed)
        else None
    )


def _command_class(command: str) -> str:
    first = command.strip().split(maxsplit=1)[0] if command.strip() else "unknown"
    match first:
        case "just":
            return "gate" if "gate" in command.split() else "just"
        case "git":
            return "git"
        case _:
            return first


def _summarize_session(session: HistorySession, records: list[HistoryRecord]) -> _SessionSummary:
    labelled_role = session.labels.get("role")
    if labelled_role is not None and labelled_role not in {"agent", "judge"}:
        raise HistoryError(f"unsupported oneharness session role {labelled_role!r}")
    role = session_role(session)
    usage = cast(UsageValues, {})
    for field_name in USAGE_FIELDS:
        values = [
            (
                _number(raw.get(field_name))
                if field_name == "cost_usd" and isinstance(raw, dict)
                else _non_negative_int(raw.get(field_name))
                if isinstance(raw, dict)
                else None
            )
            for record in records
            for raw in [record.get("usage")]
        ]
        usage[field_name] = (
            sum(cast(list[int | float], values))
            if values and all(v is not None for v in values)
            else None
        )
    model_ms = 0
    tool_ms = 0
    interval_complete = bool(records)
    commands: dict[str, int] = {}
    for record in records:
        schema_version = record.get("schema_version")
        if schema_version is not None and schema_version not in SUPPORTED_HISTORY_SCHEMA_VERSIONS:
            raise HistoryError(f"unsupported oneharness history schema version {schema_version!r}")
        if schema_version not in {"0.3", 2}:
            interval_complete = False
        model = _non_negative_int(record.get("model_ms"))
        tool = _non_negative_int(record.get("tool_ms"))
        if schema_version in {"0.3", 2} and (
            _non_negative_int(record.get("duration_ms")) is None or model is None or tool is None
        ):
            raise HistoryError("oneharness history schema v2 record has invalid required timing")
        if schema_version in {"0.3", 2}:
            start_at = _utc_datetime(record.get("started_at"))
            finish_at = _utc_datetime(record.get("finished_at"))
            duration = record["duration_ms"]
            if (
                start_at is None
                or finish_at is None
                or finish_at < start_at
                or cast(int, model) + cast(int, tool) > duration
            ):
                raise HistoryError("oneharness history schema v2 record has invalid interval")
        if model is None or tool is None:
            interval_complete = False
        else:
            model_ms += round(model)
            tool_ms += round(tool)
        events = record.get("events")
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict) or event.get("kind") != "tool_call":
                continue
            if schema_version in {"0.3", 2}:
                event_start = _utc_datetime(event.get("started_at"))
                raw_finish = event.get("finished_at")
                event_finish = _utc_datetime(raw_finish) if raw_finish is not None else None
                raw_duration = event.get("duration_ms")
                if (
                    not isinstance(event.get("tool_call_id"), str)
                    or not event["tool_call_id"]
                    or event_start is None
                    or (raw_finish is not None and event_finish is None)
                    or (event_finish is not None and event_finish < event_start)
                    or (raw_duration is not None and _non_negative_int(raw_duration) is None)
                    or event.get("status") not in {"completed", "failed", "timeout", "interrupted"}
                ):
                    raise HistoryError("oneharness history schema v2 record has invalid tool event")
            event_duration = _non_negative_int(event.get("duration_ms"))
            if tool is None and event_duration is not None:
                tool_ms += event_duration
            if event.get("name") not in {"command_execution", "bash"}:
                continue
            inputs = event.get("input")
            if isinstance(inputs, dict):
                command = inputs.get("command", inputs.get("cmd"))
                if isinstance(command, str):
                    kind = _command_class(command)
                    commands[kind] = commands.get(kind, 0) + 1
    return _SessionSummary(
        role=role,
        labels=dict(session.labels),
        link=SessionLink(
            session_id=str(session.session_id), history_id=None, role=role, turn_index=None
        ),
        # A normalized history record is one provider invocation (one conversation turn).
        turns=len(records),
        duration_ms=sum(
            record_duration
            for record in records
            if (record_duration := _non_negative_int(record.get("duration_ms"))) is not None
        ),
        model_ms=model_ms,
        tool_ms=tool_ms,
        usage=usage,
        commands=commands,
        interval_complete=interval_complete,
    )


def _party_usage(summaries: list[_SessionSummary], role: str) -> UsageValues:
    party = [summary for summary in summaries if summary.role == role]
    result = cast(UsageValues, {})
    for field_name in USAGE_FIELDS:
        values = [item.usage[field_name] for item in party]
        result[field_name] = (
            sum(cast(list[int | float], values))
            if values and all(value is not None for value in values)
            else None
        )
    return result


def _usage(summaries: list[_SessionSummary]) -> UsageRecord:
    agent = _party_usage(summaries, "agent")
    judge = _party_usage(summaries, "judge")
    total = cast(
        UsageValues,
        {
            key: cast(int | float, agent[key]) + cast(int | float, judge[key])
            if agent[key] is not None and judge[key] is not None
            else None
            for key in USAGE_FIELDS
        },
    )
    return {"agent": agent, "judge": judge, "total": total}


def _timing(
    wall_ms: int, summaries: list[_SessionSummary], gate: float = 0.0, wait: float = 0.0
) -> TimingRecord:
    agent_duration = sum(item.duration_ms for item in summaries if item.role == "agent")
    judge_duration = sum(item.duration_ms for item in summaries if item.role == "judge")
    raw_agent_model = sum(item.model_ms for item in summaries if item.role == "agent")
    raw_judge_model = sum(item.model_ms for item in summaries if item.role == "judge")
    raw_tool = sum(item.tool_ms for item in summaries)
    # Without native intervals, enforce the contract's tool > judge > agent precedence
    # while clipping the sequential sums to the observed row wall.
    tool = min(wall_ms, raw_tool)
    judge_model = min(max(0, wall_ms - tool), raw_judge_model)
    agent_model = min(max(0, wall_ms - tool - judge_model), raw_agent_model)
    measured = agent_model + judge_model + tool
    idle = max(0, wall_ms - measured)
    unattributed = min(
        idle,
        sum(item.duration_ms for item in summaries if not item.interval_complete)
        + max(0, idle - sum(item.duration_ms for item in summaries)),
    )

    def fraction(value: int) -> float:
        return value / wall_ms if wall_ms else 0.0

    return TimingRecord(
        agent_seconds=agent_duration / 1000,
        judge_seconds=judge_duration / 1000,
        gate_seconds=gate,
        publication_wait_seconds=wait,
        wall_seconds=wall_ms / 1000,
        agent_model_ms=agent_model,
        judge_model_ms=judge_model,
        tool_ms=tool,
        idle_orchestration_ms=idle,
        unattributed_ms=unattributed,
        wall_ms=wall_ms,
        fractions=FractionsRecord(
            agent_model=fraction(agent_model),
            judge_model=fraction(judge_model),
            tool=fraction(tool),
            idle_orchestration=fraction(idle),
        ),
    )


def _providers(run_id: RunId, oneharness_bin: str) -> tuple[list[Provider], list[_SessionSummary]]:
    found: list[Provider] = []
    summaries: list[_SessionSummary] = []
    try:
        sessions = all_sessions(oneharness_bin=oneharness_bin)
    except HistoryError as exc:
        if str(exc).startswith("oneharness not found"):
            return found, summaries
        raise
    for session in sessions:
        if session.labels.get("run_id") != run_id:
            continue
        if session.labels.get("role") == "llmlint":
            continue
        records = session_records(session)
        summaries.append(_summarize_session(session, cast(list[HistoryRecord], records)))
        latest = records[-1] if records else {}
        raw_provider = latest.get("provider", "oneharness")
        raw_harness = latest.get("harness", "")
        raw_model = latest.get("model", "")
        if not (
            isinstance(raw_provider, str)
            and raw_provider
            and isinstance(raw_harness, str)
            and isinstance(raw_model, str)
        ):
            continue
        item = Provider(raw_provider, raw_harness, raw_model)
        if item not in found:
            found.append(item)
    return found, summaries


def _node_wall_ms(node: str, events: list[Event], *, active_at: float | None = None) -> int:
    started: float | None = None
    total = 0.0
    for event in events:
        if event.node != node:
            continue
        if event.kind == "node-started":
            started = event.at
        elif event.kind in {"node-settled", "node-failed", "human-waiting"} and started is not None:
            total += max(0.0, event.at - started)
            started = None
    if started is not None and active_at is not None:
        total += max(0.0, active_at - started)
    return round(total * 1000)


def _command_counts(summaries: list[_SessionSummary]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for summary in summaries:
        for name, count in summary.commands.items():
            counts[name] = counts.get(name, 0) + count
    return counts


def _node_record(
    node: str,
    item: GraphResultItem,
    events: list[Event],
    summaries: list[_SessionSummary],
    *,
    active_at: float | None = None,
) -> NodeTelemetry:
    resume = item.get("resume")
    checkpoint = str(resume.get("checkpoint", "")) if isinstance(resume, dict) else ""
    commits = [
        str(event.detail.get("commit"))
        for event in events
        if event.node == node and event.detail.get("commit")
    ]
    attestations = [
        event.detail.get("gate_attestation")
        for event in events
        if event.node == node and event.kind == "verification-finished"
    ]
    attestation = GateAttestation.from_value(attestations[-1] if attestations else None)
    comparisons = [
        event.detail
        for event in events
        if event.node == node and event.kind == "verification-started"
    ]
    comparison_remote = attestation.comparison_remote if attestation else ""
    comparison_base = attestation.comparison_base if attestation else ""
    if comparisons and not comparison_remote:
        remote, base = (
            comparisons[-1].get("comparison_remote"),
            comparisons[-1].get("comparison_base"),
        )
        comparison_remote = remote if isinstance(remote, str) else ""
        comparison_base = base if isinstance(base, str) else ""
    linked = [summary for summary in summaries if summary.labels.get("node") == node]
    return NodeTelemetry(
        node=node,
        status=str(item.get("status", "unknown")),
        outcome=str(item.get("outcome", "")),
        branch=str(item.get("branch", "")),
        comparison_remote=comparison_remote,
        comparison_base=comparison_base,
        checkpoint=checkpoint,
        commit=commits[-1] if commits else "",
        retry_lineage=RetryLineageTelemetry.from_value(item.get("retry_lineage")),
        gate_attestation=attestation,
        timing=_timing(_node_wall_ms(node, events, active_at=active_at), linked),
        usage=_usage(linked),
        sessions=[summary.link for summary in linked],
        tool_commands=_command_counts(linked),
        turns=sum(summary.turns for summary in linked),
    )


def collect_run(
    run_dir: Path, *, now: float | None = None, oneharness_bin: str = "oneharness"
) -> RunTelemetry | None:
    latest = latest_round(run_dir)
    if latest is None:
        return None
    events = read_events(run_dir / JOURNAL_NAME)
    result_path = latest[1] / "result.json"
    if result_path.exists():
        payload = as_result_payload(load_mapping(result_path))
        state = result_state(payload)
        items = payload["results"]
    else:
        state = run_state(run_dir, RunId(run_dir.name)).state
        active_nodes = dict.fromkeys(str(event.node) for event in events if event.node is not None)
        items = {node: GraphResultItem(status="running", kind="agent") for node in active_nodes}
    last = events[-1] if events else None
    providers, summaries = _providers(RunId(run_dir.name), oneharness_bin)
    gate_seconds = _gate_seconds(events)
    current = time.time() if now is None else now
    wall_end = last.at if result_state_is_terminal(state) and last else current
    wall = max(0.0, wall_end - events[0].at) if events else 0.0
    publication_waits = _publication_waits(events)
    wait = _publication_wait_seconds(
        events, active_at=None if result_state_is_terminal(state) else current
    )
    failure = next((found for item in items.values() if (found := _failure(item))), None)
    snapshot: DetailSnapshot = load_snapshot(run_dir)
    node_active_at = None if result_state_is_terminal(state) else current
    nodes = [
        _node_record(node, item, events, summaries, active_at=node_active_at)
        for node, item in items.items()
    ]
    timing = _timing(round(wall * 1000), summaries, gate_seconds, wait)
    native = [summary.interval_complete for summary in summaries]
    # History timing without authoritative onejudge linkage remains partial.
    quality: TelemetryQuality = "partial" if any(native) else "legacy"
    sources: list[TelemetrySource] = []
    if any(native):
        sources.append("oneharness")
    if summaries:
        sources.append("history_legacy")
    if events:
        sources.append("journal_legacy")
    return RunTelemetry(
        run_id=RunId(run_dir.name),
        state=state,
        phase=_phase(last, state),
        last_progress_at=last.at if last else None,
        last_event=last.kind if last else "",
        timing=timing,
        nodes=nodes,
        providers=providers,
        failure=failure,
        check_rollup=snapshot.check_rollup,
        green_to_publication_seconds=publication_waits,
        usage=_usage(summaries),
        telemetry_quality=quality,
        sources=sources,
        node_work_ms=NodeWorkRecord(
            agent_model_ms=sum(cast(TimingRecord, node.timing)["agent_model_ms"] for node in nodes),
            judge_model_ms=sum(cast(TimingRecord, node.timing)["judge_model_ms"] for node in nodes),
            tool_ms=sum(cast(TimingRecord, node.timing)["tool_ms"] for node in nodes),
            wall_ms=sum(cast(TimingRecord, node.timing)["wall_ms"] for node in nodes),
        ),
        turns=sum(summary.turns for summary in summaries),
        tool_commands=_command_counts(summaries),
    )


def _metrics(runs: list[RunTelemetry]) -> MetricsRecord:
    dispositions = [
        node.retry_lineage.disposition
        for run in runs
        for node in run.nodes
        if node.retry_lineage is not None
    ]
    turns: dict[int, int] = {}
    for run in runs:
        turns[run.turns] = turns.get(run.turns, 0) + 1
    usage = cast(UsageValues, {})
    for key in USAGE_FIELDS:
        values = [run.usage["total"][key] for run in runs]
        usage[key] = (
            sum(cast(list[int | float], values))
            if all(value is not None for value in values)
            else None
        )
    return MetricsRecord(
        retry_attempts=len(dispositions),
        retry_branch_reuses=dispositions.count("reused"),
        recovered_branches=dispositions.count("recovered"),
        abandoned_branches=dispositions.count("abandoned"),
        no_diff_dispatches=sum(node.outcome == "no-changes" for run in runs for node in run.nodes),
        green_to_publication_seconds=[
            elapsed for run in runs for elapsed in run.green_to_publication_seconds
        ],
        turns=turns,
        usage=usage,
    )


def _value(value: int | float | None) -> str:
    return "?" if value is None else f"{value:g}"


def _breakdown(runs: list[RunTelemetry]) -> str:
    header = (
        "RUN/NODE              WALL   AGENT       JUDGE       TOOL        IDLE        "
        "UNATTR  TOKENS A/J  CACHE R/W  COST  TURNS QUALITY"
    )
    lines = [header]
    for run in runs:
        rows: list[tuple[str, TimingRecord, UsageRecord, int]] = [
            (str(run.run_id), run.timing, run.usage, run.turns)
        ]
        rows.extend(
            (
                f"  {node.node}",
                cast(TimingRecord, node.timing),
                cast(UsageRecord, node.usage),
                node.turns,
            )
            for node in run.nodes
        )
        for name, timing, usage, turns in rows:
            fractions = timing["fractions"]
            columns = [
                f"{name[:20]:20}",
                f"{timing['wall_ms']:6}ms",
                f"{timing['agent_model_ms']:5} {fractions['agent_model']:5.1%}",
                f"{timing['judge_model_ms']:5} {fractions['judge_model']:5.1%}",
                f"{timing['tool_ms']:5} {fractions['tool']:5.1%}",
                f"{timing['idle_orchestration_ms']:5} {fractions['idle_orchestration']:5.1%}",
                f"{timing['unattributed_ms']:6}",
                f"{_value(usage['agent']['input_tokens'])}/{_value(usage['judge']['input_tokens'])}",
                f"{_value(usage['total']['cache_read_tokens'])}/"
                f"{_value(usage['total']['cache_write_tokens'])}",
                _value(usage["total"]["cost_usd"]),
                str(turns),
                run.telemetry_quality,
            ]
            lines.append(" ".join(columns))
    lines.append(
        "Turn histogram: "
        + ", ".join(f"{turn}={count}" for turn, count in sorted(_metrics(runs)["turns"].items()))
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit the unified run telemetry index as JSON.")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--all", action="store_true", help="include settled runs")
    parser.add_argument("--oneharness-bin", default="oneharness")
    parser.add_argument(
        "--breakdown", action="store_true", help="render a human-readable timing breakdown"
    )
    args = parser.parse_args(argv)
    entries = sorted(args.runs_dir.iterdir()) if args.runs_dir.is_dir() else []
    try:
        records = [
            telemetry
            for entry in entries
            if entry.is_dir()
            if (telemetry := collect_run(entry, oneharness_bin=args.oneharness_bin)) is not None
            and (args.all or not result_state_is_terminal(telemetry.state))
        ]
    except HistoryError as exc:
        print(f"telemetry: {exc}", file=sys.stderr)
        return 2
    if args.breakdown:
        print(_breakdown(records))
        return 0
    print(
        json.dumps(
            {
                "schema_version": TELEMETRY_SCHEMA_VERSION,
                "runs": [record.record() for record in records],
                "metrics": _metrics(records),
            },
            sort_keys=True,
        )
    )
    return 0
