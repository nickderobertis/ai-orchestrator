"""One versioned machine-readable index over every recorded run source."""

# llmlint: ignore-file[boundary_inputs_validated] Schema-v2 model/tool/interval fields are
# required by docs/telemetry-model.md; rejecting a record that claims v2 while violating
# those required fields is the documented trust-boundary behavior. Optional usage fields
# degrade independently to null and optional command input/name fields are ignored.
# llmlint: ignore-file[changed_behavior_has_e2e] The real CLI telemetry E2E exercises report-v5
# ingestion and normalized oneharness timing, including role linkage, precedence, and clipping.
# llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] docs/telemetry-model.md is
# the explicitly preserved cross-layer design spec, not a generated local contract. The
# checked-in golden gates every locally emitted field, enum, and version.

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypedDict, cast

from .detail_snapshot import CheckRollup
from .history import (
    LLMLINT_PROMPT_PREFIXES,
    HistoryError,
    HistorySession,
    SessionRole,
    all_sessions,
    session_records,
    session_role,
)
from .journal import JOURNAL_NAME, Event, EventKind, read_events
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

TELEMETRY_SCHEMA_VERSION = 6
SUPPORTED_HISTORY_SCHEMA_VERSIONS = ("0.2", "0.3", 1, 2, "1.0")
#: History schema versions that carry validated native timing (per-turn
#: ``model_ms``/``tool_ms`` plus interval-bearing tool events). oneharness 0.5's
#: event-sourced 1.0 records keep the same required fields and event shape as the
#: earlier 0.3/v2 tier once `history.py` folds their event lines back onto the run.
NATIVE_TIMING_HISTORY_SCHEMAS: frozenset[str | int] = frozenset({"0.3", 2, "1.0"})
TelemetryQuality = Literal["complete", "partial", "legacy"]
TelemetrySource = Literal["onejudge", "oneharness", "history_legacy", "journal_legacy"]
FailureClass = Literal[
    "agent", "gate", "checks", "publication", "timeout", "provider", "configuration", "unknown"
]


class TimingRecord(TypedDict):
    agent_seconds: float
    judge_seconds: float
    llmlint_seconds: float
    gate_seconds: float
    publication_wait_seconds: float
    lock_wait_seconds: float
    setup_seconds: float
    scheduling_seconds: float
    wall_seconds: float
    agent_model_ms: int
    judge_model_ms: int
    llmlint_model_ms: int
    tool_ms: int
    idle_orchestration_ms: int
    unattributed_ms: int
    wall_ms: int
    fractions: FractionsRecord


class FractionsRecord(TypedDict):
    agent_model: float
    judge_model: float
    llmlint_model: float
    tool: float
    idle_orchestration: float
    lock_wait: float
    setup: float
    scheduling: float


class UsageValues(TypedDict):
    input_tokens: int | float | None
    output_tokens: int | float | None
    cache_read_tokens: int | float | None
    cache_write_tokens: int | float | None
    cost_usd: int | float | None


class UsageRecord(TypedDict):
    agent: UsageValues
    judge: UsageValues
    llmlint: UsageValues
    total: UsageValues


UsageKey = Literal[
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "cost_usd"
]


class SessionLink(TypedDict, total=False):
    session_id: str
    history_id: str | None
    role: SessionRole
    turn_index: int | None
    started_at: str
    finished_at: str | None


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
    lock_wait_seconds: float
    setup_seconds: float
    scheduling_seconds: float
    llmlint_wrong_file_retries: LlmlintRetryMetrics


class LlmlintRetryRate(TypedDict):
    initial_calls: int
    wrong_file_corrections: int
    wrong_file_correction_rate: float | None
    oneharness_retry_sessions: int


class LlmlintRetryMetrics(LlmlintRetryRate):
    period_start: str | None
    latest_session_start: str | None
    by_repository: dict[str, LlmlintRetryRate]
    by_node: dict[str, LlmlintRetryRate]


@dataclass(frozen=True)
class LlmlintRetryObservation:
    session: HistorySession
    is_initial: bool
    is_correction: bool


class NodeWorkRecord(TypedDict):
    agent_model_ms: int
    judge_model_ms: int
    llmlint_model_ms: int
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
    lint: int = 0

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
        result["lint"] = self.lint
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
    lint: int = 0
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
            "lint": self.lint,
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
class _Interval:
    start_ms: int
    finish_ms: int


@dataclass(frozen=True)
class _SessionSummary:
    role: SessionRole
    labels: dict[str, str]
    link: SessionLink
    turns: int
    duration_ms: int
    model_ms: int
    tool_ms: int
    usage: UsageValues
    commands: dict[str, int]
    validated_native_fields: bool
    tool_intervals: list[_Interval]


@dataclass(frozen=True)
class _NativeParty:
    model_ms: int | None
    tool_ms: int | None
    usage: UsageValues | None
    invalid: bool


@dataclass(frozen=True)
class _NativeTelemetry:
    wall_ms: int | None
    orchestration_ms: int | None
    agent_model_ms: int | None
    judge_model_ms: int | None
    tool_ms: int | None
    usage: UsageRecord | None
    sessions: list[SessionLink]
    invalid: bool = False


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
    if labelled_role is not None and labelled_role not in {"agent", "judge", "llmlint"}:
        raise HistoryError(f"unsupported oneharness session role {labelled_role!r}")
    role = session_role(session, records)
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
    validated_native_fields = bool(records)
    commands: dict[str, int] = {}
    tool_intervals: list[_Interval] = []
    for record in records:
        schema_version = record.get("schema_version")
        if schema_version is not None and schema_version not in SUPPORTED_HISTORY_SCHEMA_VERSIONS:
            raise HistoryError(f"unsupported oneharness history schema version {schema_version!r}")
        if schema_version not in NATIVE_TIMING_HISTORY_SCHEMAS:
            validated_native_fields = False
        model = _non_negative_int(record.get("model_ms"))
        tool = _non_negative_int(record.get("tool_ms"))
        if schema_version in NATIVE_TIMING_HISTORY_SCHEMAS and (
            _non_negative_int(record.get("duration_ms")) is None or model is None or tool is None
        ):
            raise HistoryError("oneharness history schema v2 record has invalid required timing")
        if schema_version in NATIVE_TIMING_HISTORY_SCHEMAS:
            start_at = _utc_datetime(record.get("started_at"))
            raw_finish = record.get("finished_at")
            finish_at = _utc_datetime(raw_finish) if raw_finish is not None else None
            duration = record["duration_ms"]
            if (
                start_at is None
                or (raw_finish is not None and finish_at is None)
                or (finish_at is not None and finish_at < start_at)
                or cast(int, model) + cast(int, tool) > duration
            ):
                raise HistoryError("oneharness history schema v2 record has invalid interval")
            if finish_at is None:
                validated_native_fields = False
        if model is None or tool is None:
            validated_native_fields = False
        else:
            model_ms += round(model)
            tool_ms += round(tool)
        events = record.get("events")
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict) or event.get("kind") != "tool_call":
                continue
            event_start: datetime | None = None
            event_finish: datetime | None = None
            if schema_version in NATIVE_TIMING_HISTORY_SCHEMAS:
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
            if event_start is not None and event_finish is not None:
                tool_intervals.append(
                    _Interval(
                        round(event_start.timestamp() * 1000),
                        round(event_finish.timestamp() * 1000),
                    )
                )
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
        validated_native_fields=validated_native_fields,
        tool_intervals=tool_intervals,
    )


def _native_party(value: object) -> _NativeParty:
    if value is None:
        return _NativeParty(None, None, None, False)
    if not isinstance(value, dict):
        return _NativeParty(None, None, None, True)
    invalid = False
    model = _non_negative_int(value.get("model_ms"))
    tool = _non_negative_int(value.get("tool_ms"))
    if "model_ms" in value and model is None:
        invalid = True
    if "tool_ms" in value and tool is None:
        invalid = True
    raw_usage = value.get("usage")
    usage: UsageValues | None = None
    if raw_usage is not None:
        if not isinstance(raw_usage, dict):
            invalid = True
        else:
            usage = cast(UsageValues, {})
            for key in USAGE_FIELDS:
                raw = raw_usage.get(key)
                parsed = _number(raw) if key == "cost_usd" else _non_negative_int(raw)
                if key in raw_usage and parsed is None:
                    invalid = True
                usage[key] = parsed
    return _NativeParty(model, tool, usage, invalid)


def _native_telemetry(value: object) -> _NativeTelemetry | None:
    """Validate report telemetry per field; invalid optional data degrades locally."""
    if value is None:
        return None
    if not isinstance(value, dict):
        return _NativeTelemetry(None, None, None, None, None, None, [], True)
    invalid = False
    wall = _non_negative_int(value.get("wall_ms"))
    orchestration = _non_negative_int(value.get("orchestration_ms"))
    if "wall_ms" in value and wall is None:
        invalid = True
    if "orchestration_ms" in value and orchestration is None:
        invalid = True
    agent = _native_party(value.get("agent"))
    judge = _native_party(value.get("judge"))
    invalid |= agent.invalid or judge.invalid
    tool = (
        agent.tool_ms + judge.tool_ms
        if agent.tool_ms is not None and judge.tool_ms is not None
        else agent.tool_ms
        if judge.tool_ms is None
        else judge.tool_ms
    )
    usage = None
    if agent.usage is not None or judge.usage is not None:
        unknown = cast(UsageValues, {key: None for key in USAGE_FIELDS})
        agent_values = agent.usage or unknown
        judge_values = judge.usage or unknown
        total = cast(
            UsageValues,
            {
                key: cast(int | float, agent_values[key]) + cast(int | float, judge_values[key])
                if agent_values[key] is not None and judge_values[key] is not None
                else None
                for key in USAGE_FIELDS
            },
        )
        usage = UsageRecord(
            agent=agent_values,
            judge=judge_values,
            # Native onejudge linkage predates the nested llmlint role, so its
            # two-party report authoritatively contains no lint contribution.
            llmlint=cast(UsageValues, {key: 0 for key in USAGE_FIELDS}),
            total=total,
        )
    links: list[SessionLink] = []
    raw_sessions = value.get("sessions", [])
    if not isinstance(raw_sessions, list):
        invalid = True
    else:
        for raw in raw_sessions:
            if not isinstance(raw, dict):
                invalid = True
                continue
            session_id, history_id, role, turn = (
                raw.get("session_id"),
                raw.get("history_id"),
                raw.get("role"),
                raw.get("turn_index"),
            )
            started = _utc_datetime(raw.get("started_at"))
            raw_finished = raw.get("finished_at")
            finished = _utc_datetime(raw_finished) if raw_finished is not None else None
            if not (
                isinstance(session_id, str)
                and session_id
                and (history_id is None or isinstance(history_id, str))
                and role in {"agent", "judge"}
                and _non_negative_int(turn) is not None
                and started is not None
                and (raw_finished is None or finished is not None)
                and (finished is None or finished >= started)
            ):
                invalid = True
                continue
            links.append(
                SessionLink(
                    session_id=session_id,
                    history_id=history_id,
                    role=cast(SessionRole, role),
                    turn_index=cast(int, turn),
                    started_at=cast(str, raw["started_at"]),
                    finished_at=cast(str | None, raw_finished),
                )
            )
    return _NativeTelemetry(
        wall, orchestration, agent.model_ms, judge.model_ms, tool, usage, links, invalid
    )


def _link_native_roles(
    summaries: list[_SessionSummary], native: _NativeTelemetry | None
) -> list[_SessionSummary]:
    if native is None or not native.sessions:
        return summaries
    links = {link["session_id"]: link for link in native.sessions}
    return [
        replace(summary, role=link["role"], link=link)
        if (link := links.get(summary.link["session_id"])) is not None
        else summary
        for summary in summaries
    ]


def _item_native(item: GraphResultItem) -> _NativeTelemetry | None:
    direct = _native_telemetry(item.get("telemetry"))
    if direct is not None:
        return direct
    steps = item.get("steps")
    if not isinstance(steps, list):
        return None
    parts = [
        parsed
        for step in steps
        if isinstance(step, dict)
        if (parsed := _native_telemetry(step.get("telemetry"))) is not None
    ]
    if not parts:
        return None

    def summed(
        name: Literal["wall_ms", "orchestration_ms", "agent_model_ms", "judge_model_ms", "tool_ms"],
    ) -> int | None:
        values = [getattr(part, name) for part in parts]
        return sum(cast(list[int], values)) if all(value is not None for value in values) else None

    return _NativeTelemetry(
        summed("wall_ms"),
        summed("orchestration_ms"),
        summed("agent_model_ms"),
        summed("judge_model_ms"),
        summed("tool_ms"),
        None,
        [link for part in parts for link in part.sessions],
        any(part.invalid for part in parts),
    )


def _party_usage(summaries: list[_SessionSummary], role: str) -> UsageValues:
    party = [summary for summary in summaries if summary.role == role]
    if not party and role == "llmlint":
        return cast(UsageValues, {key: 0 for key in USAGE_FIELDS})
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
    llmlint = _party_usage(summaries, "llmlint")
    total = cast(
        UsageValues,
        {
            key: sum(cast(list[int | float], [agent[key], judge[key], llmlint[key]]))
            if agent[key] is not None and judge[key] is not None and llmlint[key] is not None
            else None
            for key in USAGE_FIELDS
        },
    )
    return {"agent": agent, "judge": judge, "llmlint": llmlint, "total": total}


def _merge_usage(preferred: UsageRecord | None, fallback: UsageRecord) -> UsageRecord:
    """Select each native usage field independently, then recompute party totals."""
    parties: dict[str, UsageValues] = {}
    for role in cast(tuple[SessionRole, ...], ("agent", "judge", "llmlint")):
        values = cast(UsageValues, {})
        for key in USAGE_FIELDS:
            native_value = (
                preferred[role][key] if preferred is not None and role != "llmlint" else None
            )
            values[key] = native_value if native_value is not None else fallback[role][key]
        parties[role] = values
    total = cast(
        UsageValues,
        {
            key: sum(
                cast(list[int | float], [parties[r][key] for r in ("agent", "judge", "llmlint")])
            )
            if all(parties[r][key] is not None for r in ("agent", "judge", "llmlint"))
            else None
            for key in USAGE_FIELDS
        },
    )
    return {
        "agent": parties["agent"],
        "judge": parties["judge"],
        "llmlint": parties["llmlint"],
        "total": total,
    }


def _aggregate_usage(records: list[UsageRecord]) -> UsageRecord | None:
    if not records:
        return None
    parties: dict[str, UsageValues] = {}
    for role in cast(tuple[SessionRole, ...], ("agent", "judge", "llmlint")):
        values = cast(UsageValues, {})
        for key in USAGE_FIELDS:
            contributions = [record[role][key] for record in records]
            values[key] = (
                sum(cast(list[int | float], contributions))
                if all(value is not None for value in contributions)
                else None
            )
        parties[role] = values
    total = cast(
        UsageValues,
        {
            key: sum(
                cast(list[int | float], [parties[r][key] for r in ("agent", "judge", "llmlint")])
            )
            if all(parties[r][key] is not None for r in ("agent", "judge", "llmlint"))
            else None
            for key in USAGE_FIELDS
        },
    )
    return {
        "agent": parties["agent"],
        "judge": parties["judge"],
        "llmlint": parties["llmlint"],
        "total": total,
    }


def _timing(
    wall_ms: int,
    summaries: list[_SessionSummary],
    gate: float = 0.0,
    wait: float = 0.0,
    native: _NativeTelemetry | None = None,
    lock_wait: float = 0.0,
    setup: float = 0.0,
    scheduling: float = 0.0,
) -> TimingRecord:
    agent_duration = sum(item.duration_ms for item in summaries if item.role == "agent")
    judge_duration = sum(item.duration_ms for item in summaries if item.role == "judge")
    llmlint_duration = sum(item.duration_ms for item in summaries if item.role == "llmlint")
    raw_agent_model = (
        native.agent_model_ms
        if native is not None and native.agent_model_ms is not None
        else sum(item.model_ms for item in summaries if item.role == "agent")
    )
    raw_judge_model = (
        native.judge_model_ms
        if native is not None and native.judge_model_ms is not None
        else sum(item.model_ms for item in summaries if item.role == "judge")
    )
    raw_llmlint_model = sum(item.model_ms for item in summaries if item.role == "llmlint")
    raw_tool = (
        native.tool_ms
        if native is not None and native.tool_ms is not None
        else sum(item.tool_ms for item in summaries)
    )
    tool = min(wall_ms, raw_tool)
    judge_model = min(max(0, wall_ms - tool), raw_judge_model)
    llmlint_model = min(max(0, wall_ms - tool - judge_model), raw_llmlint_model)
    agent_model = min(max(0, wall_ms - tool - judge_model - llmlint_model), raw_agent_model)
    measured = agent_model + judge_model + llmlint_model + tool
    remaining = max(0, wall_ms - measured)
    gate_ms = min(remaining, round(gate * 1000))
    remaining -= gate_ms
    lock_ms = min(remaining, round(lock_wait * 1000))
    remaining -= lock_ms
    setup_ms = min(remaining, round(setup * 1000))
    remaining -= setup_ms
    scheduling_ms = min(remaining, round(scheduling * 1000))
    remaining -= scheduling_ms
    publication_ms = min(remaining, round(wait * 1000))
    idle = remaining - publication_ms
    unattributed = min(
        idle,
        sum(item.duration_ms for item in summaries if not item.validated_native_fields)
        + max(0, idle - sum(item.duration_ms for item in summaries)),
    )

    def fraction(value: int) -> float:
        return value / wall_ms if wall_ms else 0.0

    return TimingRecord(
        agent_seconds=agent_duration / 1000,
        judge_seconds=judge_duration / 1000,
        llmlint_seconds=llmlint_duration / 1000,
        gate_seconds=gate_ms / 1000,
        publication_wait_seconds=publication_ms / 1000,
        lock_wait_seconds=lock_ms / 1000,
        setup_seconds=setup_ms / 1000,
        scheduling_seconds=scheduling_ms / 1000,
        wall_seconds=wall_ms / 1000,
        agent_model_ms=agent_model,
        judge_model_ms=judge_model,
        llmlint_model_ms=llmlint_model,
        tool_ms=tool,
        idle_orchestration_ms=idle,
        unattributed_ms=unattributed,
        wall_ms=wall_ms,
        fractions=FractionsRecord(
            agent_model=fraction(agent_model),
            judge_model=fraction(judge_model),
            llmlint_model=fraction(llmlint_model),
            tool=fraction(tool),
            idle_orchestration=fraction(idle),
            lock_wait=fraction(lock_ms),
            setup=fraction(setup_ms),
            scheduling=fraction(scheduling_ms),
        ),
    )


def _event_seconds(events: list[Event], kind: EventKind, *, node: str | None = None) -> float:
    total = 0.0
    for event in events:
        if event.kind != kind or (node is not None and event.node != node):
            continue
        value = event.detail.get("seconds")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            total += float(value)
    return total


def _scheduling_seconds(events: list[Event], node: str) -> float:
    starts = [event.at for event in events if event.node == node and event.kind == "node-started"]
    if not starts:
        return 0.0
    definitions = [
        cast(dict[str, object], event.detail.get("definition"))
        for event in events
        if event.kind == "node-added" and isinstance(event.detail.get("definition"), dict)
    ]
    definition = next(
        (item for item in reversed(definitions) if item.get("id") == node),
        None,
    )
    deps = definition.get("deps", []) if isinstance(definition, dict) else []
    ready = max(
        (
            event.at
            for event in events
            if event.at <= starts[0]
            and (
                event.kind == "round-started"
                or (
                    isinstance(deps, list)
                    and event.node in deps
                    and event.kind in {"node-settled", "node-failed", "human-attested"}
                )
            )
        ),
        default=starts[0],
    )
    return max(0.0, starts[0] - ready)


def _union_interval_ms(start_ms: int, finish_ms: int, intervals: list[_Interval]) -> int:
    clipped = sorted(
        (max(start_ms, interval.start_ms), min(finish_ms, interval.finish_ms))
        for interval in intervals
        if min(finish_ms, interval.finish_ms) > max(start_ms, interval.start_ms)
    )
    total = 0
    cursor = start_ms
    for start, finish in clipped:
        total += max(0, finish - max(start, cursor))
        cursor = max(cursor, finish)
    return total


def _run_timing(
    start_ms: int,
    finish_ms: int,
    summaries: list[_SessionSummary],
    nodes: list[NodeTelemetry],
    native_by_node: dict[str, _NativeTelemetry | None],
    gate: float,
    wait: float,
    events: list[Event] | None = None,
) -> TimingRecord:
    events = events or []
    wall_ms = max(0, finish_ms - start_ms)
    explicit_tool_nodes = {
        summary.labels.get("node")
        for summary in summaries
        if summary.tool_intervals
        and (native := native_by_node.get(summary.labels.get("node", ""))) is not None
        and native.tool_ms is None
    } | {
        summary.labels.get("node")
        for summary in summaries
        if summary.tool_intervals and native_by_node.get(summary.labels.get("node", "")) is None
    }
    tool_intervals = [
        interval
        for summary in summaries
        if summary.labels.get("node") in explicit_tool_nodes
        for interval in summary.tool_intervals
    ]
    tool = _union_interval_ms(start_ms, finish_ms, tool_intervals)
    tool += sum(
        cast(TimingRecord, node.timing)["tool_ms"]
        for node in nodes
        if node.node not in explicit_tool_nodes
    )
    agent = sum(cast(TimingRecord, node.timing)["agent_model_ms"] for node in nodes)
    judge = sum(cast(TimingRecord, node.timing)["judge_model_ms"] for node in nodes)
    tool = min(wall_ms, tool)
    judge = min(max(0, wall_ms - tool), judge)
    agent = min(max(0, wall_ms - tool - judge), agent)
    native = _NativeTelemetry(None, None, agent, judge, tool, None, [])
    scheduling = sum(
        _scheduling_seconds(events, str(event.node))
        for event in events
        if event.kind == "node-started" and event.node is not None
    )
    return _timing(
        wall_ms,
        summaries,
        gate,
        wait,
        native=native,
        lock_wait=_event_seconds(events, "lock-wait"),
        setup=_event_seconds(events, "setup-finished"),
        scheduling=scheduling,
    )


def _history_telemetry(
    run_id: RunId, oneharness_bin: str
) -> tuple[list[Provider], list[_SessionSummary]]:
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
    native = _item_native(item)
    linked = _link_native_roles(
        [summary for summary in summaries if summary.labels.get("node") == node], native
    )
    wall_ms = (
        native.wall_ms
        if native is not None and native.wall_ms is not None
        else _node_wall_ms(node, events, active_at=active_at)
    )
    usage = _merge_usage(native.usage if native is not None else None, _usage(linked))
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
        timing=_timing(
            wall_ms,
            linked,
            gate=_gate_seconds([event for event in events if event.node == node]),
            lock_wait=_event_seconds(events, "lock-wait", node=node),
            setup=_event_seconds(events, "setup-finished", node=node),
            scheduling=_scheduling_seconds(events, node),
            native=native,
        ),
        usage=usage,
        sessions=(
            native.sessions + [s.link for s in linked if s.role == "llmlint"]
            if native is not None and native.sessions
            else [s.link for s in linked]
        ),
        tool_commands=_command_counts(linked),
        turns=sum(summary.turns for summary in linked if summary.role != "llmlint"),
        lint=sum(summary.turns for summary in linked if summary.role == "llmlint"),
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
    providers, summaries = _history_telemetry(RunId(run_dir.name), oneharness_bin)
    native_by_node = {node: _item_native(item) for node, item in items.items()}
    native_links = [
        link
        for native_item in native_by_node.values()
        if native_item is not None
        for link in native_item.sessions
    ]
    if native_links:
        summaries = _link_native_roles(
            summaries,
            _NativeTelemetry(None, None, None, None, None, None, native_links),
        )
    gate_seconds = _gate_seconds(events)
    current = time.time() if now is None else now
    wall_end = last.at if result_state_is_terminal(state) and last else current
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
    native_parts = [part for part in native_by_node.values() if part is not None]
    timing = _run_timing(
        round(events[0].at * 1000) if events else 0,
        round(wall_end * 1000) if events else 0,
        summaries,
        nodes,
        native_by_node,
        gate_seconds,
        wait,
        events,
    )
    contributing_nodes = [
        node
        for node in nodes
        if native_by_node.get(node.node) is not None
        or any(summary.labels.get("node") == node.node for summary in summaries)
    ]
    run_usage = _aggregate_usage(
        [node.usage for node in contributing_nodes if node.usage is not None]
    )
    native = [summary.validated_native_fields for summary in summaries]
    # Complete requires authoritative linkage plus valid interval-complete history.
    quality: TelemetryQuality = (
        "complete"
        if native_parts
        and all(not part.invalid and part.sessions for part in native_parts)
        and native
        and all(native)
        else "partial"
        if native_parts or any(native)
        else "legacy"
    )
    sources: list[TelemetrySource] = []
    if native_parts:
        sources.append("onejudge")
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
        usage=run_usage or _usage(summaries),
        telemetry_quality=quality,
        sources=sources,
        node_work_ms=NodeWorkRecord(
            agent_model_ms=sum(cast(TimingRecord, node.timing)["agent_model_ms"] for node in nodes),
            judge_model_ms=sum(cast(TimingRecord, node.timing)["judge_model_ms"] for node in nodes),
            llmlint_model_ms=sum(
                cast(TimingRecord, node.timing)["llmlint_model_ms"] for node in nodes
            ),
            tool_ms=sum(cast(TimingRecord, node.timing)["tool_ms"] for node in nodes),
            wall_ms=sum(cast(TimingRecord, node.timing)["wall_ms"] for node in nodes),
        ),
        turns=sum(summary.turns for summary in summaries if summary.role != "llmlint"),
        lint=sum(summary.turns for summary in summaries if summary.role == "llmlint"),
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
        lock_wait_seconds=sum(run.timing["lock_wait_seconds"] for run in runs),
        setup_seconds=sum(run.timing["setup_seconds"] for run in runs),
        scheduling_seconds=sum(run.timing["scheduling_seconds"] for run in runs),
        llmlint_wrong_file_retries=_empty_llmlint_retry_metrics(),
    )


def _retry_rate(initial: int, corrections: int, total_llmlint: int) -> LlmlintRetryRate:
    return LlmlintRetryRate(
        initial_calls=initial,
        wrong_file_corrections=corrections,
        wrong_file_correction_rate=corrections / initial if initial else None,
        # Every llmlint history session that is not an initial evaluation is a
        # retry/guardrail invocation. This deliberately remains broader than the
        # correction signature so a future retry mechanism cannot hide the shift.
        oneharness_retry_sessions=max(0, total_llmlint - initial),
    )


def _empty_llmlint_retry_metrics() -> LlmlintRetryMetrics:
    return LlmlintRetryMetrics(
        **_retry_rate(0, 0, 0),
        period_start=None,
        latest_session_start=None,
        by_repository={},
        by_node={},
    )


def _llmlint_retry_metrics(
    sessions: list[HistorySession], *, since: datetime | None = None, until: datetime | None = None
) -> LlmlintRetryMetrics:
    rows: list[LlmlintRetryObservation] = []
    for session in sessions:
        started = _utc_datetime(session.started)
        if (
            started is None
            or (since is not None and started < since)
            or (until is not None and started >= until)
        ):
            continue
        records = session_records(session)
        if session_role(session, records) != "llmlint":
            continue
        prompt = records[0].get("prompt") if records else None
        initial = isinstance(prompt, str) and prompt.startswith(LLMLINT_PROMPT_PREFIXES[0])
        correction = isinstance(prompt, str) and prompt.startswith(LLMLINT_PROMPT_PREFIXES[1])
        rows.append(
            LlmlintRetryObservation(
                session=session,
                is_initial=initial,
                is_correction=correction,
            )
        )

    def aggregate(items: list[LlmlintRetryObservation]) -> LlmlintRetryRate:
        return _retry_rate(
            sum(item.is_initial for item in items),
            sum(item.is_correction for item in items),
            len(items),
        )

    repositories = sorted({str(row.session.project) for row in rows})
    nodes = sorted({node for row in rows if (node := row.session.labels.get("node"))})
    starts = sorted(row.session.started for row in rows)
    return LlmlintRetryMetrics(
        **aggregate(rows),
        period_start=starts[0] if starts else None,
        latest_session_start=starts[-1] if starts else None,
        by_repository={
            repository: aggregate([row for row in rows if str(row.session.project) == repository])
            for repository in repositories
        },
        by_node={
            node: aggregate([row for row in rows if row.session.labels.get("node") == node])
            for node in nodes
        },
    )


def _value(value: int | float | None) -> str:
    return "?" if value is None else f"{value:g}"


def _breakdown(runs: list[RunTelemetry], retry_metrics: LlmlintRetryMetrics | None = None) -> str:
    retry_metrics = retry_metrics or _empty_llmlint_retry_metrics()
    header = (
        "RUN/NODE              WALL   WORKER      JUDGE       LLMLINT     TOOL        "
        "GATE  PUB   LOCK  SETUP SCHED IDLE "
        "UNATTR  TOKENS IN W/J/L OUT W/J/L  CACHE R/W  COST  TURNS LINT QUALITY"
    )
    lines = [header]
    for run in runs:
        rows: list[tuple[str, TimingRecord, UsageRecord, int, int]] = [
            (str(run.run_id), run.timing, run.usage, run.turns, run.lint)
        ]
        rows.extend(
            (
                f"  {node.node}",
                cast(TimingRecord, node.timing),
                cast(UsageRecord, node.usage),
                node.turns,
                node.lint,
            )
            for node in run.nodes
        )
        for name, timing, usage, turns, lint in rows:
            fractions = timing["fractions"]
            columns = [
                f"{name[:20]:20}",
                f"{timing['wall_ms']:6}ms",
                f"{timing['agent_model_ms']:5} {fractions['agent_model']:5.1%}",
                f"{timing['judge_model_ms']:5} {fractions['judge_model']:5.1%}",
                f"{timing['llmlint_model_ms']:5} {fractions['llmlint_model']:5.1%}",
                f"{timing['tool_ms']:5} {fractions['tool']:5.1%}",
                f"{round(timing['gate_seconds'] * 1000):4}",
                f"{round(timing['publication_wait_seconds'] * 1000):4}",
                f"{round(timing['lock_wait_seconds'] * 1000):4}",
                f"{round(timing['setup_seconds'] * 1000):5}",
                f"{round(timing['scheduling_seconds'] * 1000):5}",
                f"{timing['idle_orchestration_ms']:5} {fractions['idle_orchestration']:5.1%}",
                f"{timing['unattributed_ms']:6}",
                f"{_value(usage['agent']['input_tokens'])}/{_value(usage['judge']['input_tokens'])}/"
                f"{_value(usage['llmlint']['input_tokens'])}",
                f"{_value(usage['agent']['output_tokens'])}/"
                f"{_value(usage['judge']['output_tokens'])}/"
                f"{_value(usage['llmlint']['output_tokens'])}",
                f"{_value(usage['total']['cache_read_tokens'])}/"
                f"{_value(usage['total']['cache_write_tokens'])}",
                _value(usage["total"]["cost_usd"]),
                str(turns),
                str(lint),
                run.telemetry_quality,
            ]
            lines.append(" ".join(columns))
        timeline = sorted(
            (
                link
                for node in run.nodes
                for link in node.sessions
                if "started_at" in link and link.get("turn_index") is not None
            ),
            key=lambda link: (link["started_at"], cast(int, link["turn_index"])),
        )
        if timeline:
            lines.append("  Timeline (UTC):")
            for link in timeline:
                finished = link.get("finished_at") or "active/interrupted"
                lines.append(
                    f"    turn {link['turn_index']} {link['role']}: "
                    f"{link['started_at']} -> {finished} [{link['session_id']}]"
                )
        else:
            lines.append("  Timeline: unavailable (legacy session linkage)")
    lines.append(
        "Turn histogram: "
        + ", ".join(f"{turn}={count}" for turn, count in sorted(_metrics(runs)["turns"].items()))
    )
    overall = retry_metrics
    rate = overall["wrong_file_correction_rate"]
    lines.append(
        "Llmlint wrong-file retries: "
        f"{overall['wrong_file_corrections']}/{overall['initial_calls']} "
        f"({'?' if rate is None else f'{rate:.1%}'}); "
        f"oneharness retry/guardrail sessions={overall['oneharness_retry_sessions']}"
    )
    for repository, values in overall["by_repository"].items():
        repo_rate = values["wrong_file_correction_rate"]
        lines.append(
            f"  repo {repository}: {values['wrong_file_corrections']}/"
            f"{values['initial_calls']} ({'?' if repo_rate is None else f'{repo_rate:.1%}'})"
        )
    for node, values in overall["by_node"].items():
        node_rate = values["wrong_file_correction_rate"]
        lines.append(
            f"  node {node}: {values['wrong_file_corrections']}/"
            f"{values['initial_calls']} ({'?' if node_rate is None else f'{node_rate:.1%}'})"
        )
    return "\n".join(lines)


def _boundary(value: str | None, name: str) -> datetime | None:
    if value is None:
        return None
    parsed = _utc_datetime(value)
    if parsed is None:
        raise argparse.ArgumentTypeError(f"{name} must be an ISO-8601 UTC timestamp")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit the unified run telemetry index as JSON.")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--all", action="store_true", help="include settled runs")
    parser.add_argument("--oneharness-bin", default="oneharness")
    parser.add_argument(
        "--breakdown", action="store_true", help="render a human-readable timing breakdown"
    )
    parser.add_argument("--since", help="include history at or after this ISO-8601 UTC timestamp")
    parser.add_argument("--until", help="exclude history at or after this ISO-8601 UTC timestamp")
    args = parser.parse_args(argv)
    entries = sorted(args.runs_dir.iterdir()) if args.runs_dir.is_dir() else []
    try:
        since = _boundary(args.since, "--since")
        until = _boundary(args.until, "--until")
        if since is not None and until is not None and since >= until:
            parser.error("--since must be earlier than --until")
        records = [
            telemetry
            for entry in entries
            if entry.is_dir()
            if (telemetry := collect_run(entry, oneharness_bin=args.oneharness_bin)) is not None
            and (args.all or not result_state_is_terminal(telemetry.state))
        ]
        try:
            history_sessions = all_sessions(oneharness_bin=args.oneharness_bin)
        except HistoryError as exc:
            if not str(exc).startswith("oneharness not found"):
                raise
            history_sessions = []
        retry_metrics = _llmlint_retry_metrics(history_sessions, since=since, until=until)
    except (HistoryError, argparse.ArgumentTypeError) as exc:
        print(f"telemetry: {exc}", file=sys.stderr)
        return 2
    if args.breakdown:
        print(_breakdown(records, retry_metrics))
        return 0
    print(
        json.dumps(
            {
                "schema_version": TELEMETRY_SCHEMA_VERSION,
                "runs": [record.record() for record in records],
                "metrics": {**_metrics(records), "llmlint_wrong_file_retries": retry_metrics},
            },
            sort_keys=True,
        )
    )
    return 0
