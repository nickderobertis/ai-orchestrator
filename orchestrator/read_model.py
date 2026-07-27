"""Assemble the read-only DAG-UI JSON from the recorded run sources.

This is the pure read model behind the FastAPI surface: it joins the strict round
projection, the machine telemetry index, the per-node result items, the mapped
agent/subagent conversations, the persisted PR/commit detail, and the launching
session provenance into the exact ``RunList`` / ``RunDetail`` / ``Round`` shapes the
DAG UI contract fixes (`docs/dag-ui/design.md`). It reads; it never writes, spawns a
run, or reaches the network beyond the history/GitHub reads its sources already own.

Every input is a separate trust boundary. A missing run is `RunNotFound`; a corrupt
authoritative journal is `ProjectionFailed`; a malformed launch record degrades to
"no launcher" rather than failing the read.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from .config import ConfigError
from .conversations import DagConversation, run_conversations
from .history import HistoryError
from .journal import JOURNAL_NAME
from .launch import read_provenance, validate_launch_id
from .monitor import load_snapshot, snapshot_path
from .projection import (
    NodeState,
    ProjectedPlan,
    ProjectionError,
    project_round,
    read_strict_events,
)
from .runs import (
    GraphPayload,
    GraphResultItem,
    RunId,
    latest_round,
    load_mapping,
    result_state_is_terminal,
    validate_run_id,
)
from .telemetry import TELEMETRY_SCHEMA_VERSION, RunTelemetry, TimingRecord, collect_run

API_VERSION = 1


class ReadError(Exception):
    """Base class for a read the server maps to an HTTP status."""


class InvalidRunId(ReadError):
    """A run identifier failed validation at the trust boundary (422)."""


class RunNotFound(ReadError):
    """No recorded run matches the identifier (404)."""


class InvalidConversationId(ReadError):
    """A conversation identifier failed validation at the trust boundary (422)."""


class ProjectionFailed(ReadError):
    """The authoritative journal cannot be folded into a consistent graph (409)."""


#: Bound on the opaque conversation id accepted from a request path. History session
#: ids are short native identifiers; anything longer, empty, or carrying control
#: characters is rejected here rather than scanned against every discovered session.
_MAX_CONVERSATION_ID = 256


def validate_conversation_id(value: str) -> str:
    """Return a well-formed opaque conversation id, else raise ``InvalidConversationId``."""
    if not value or len(value) > _MAX_CONVERSATION_ID or not value.isprintable():
        raise InvalidConversationId(
            "conversation id must be 1-256 printable characters on a single line"
        )
    return value


class RunLaunch(TypedDict):
    """A run's join to its launching session, per ``docs/dag-ui/design.md``.

    ``launcher_session_id`` is present only when the server's redaction policy is
    configured to expose it, so it is omitted rather than nulled by default.
    """

    launch_id: str
    launcher: str
    launcher_session_id: NotRequired[str]


class RunSummary(TypedDict):
    """One ``RunSummary`` row of the run-list view."""

    run_id: str
    state: str
    phase: str
    last_event: str
    telemetry_quality: str
    timing: TimingRecord
    node_counts: dict[str, int]
    last_progress_at: NotRequired[float]
    launch: NotRequired[RunLaunch]


class RunList(TypedDict):
    """The ``RunList`` envelope served by ``GET /api/v1/runs`` and the SSE snapshot."""

    api_version: int
    telemetry_schema_version: int
    observed_at: str
    runs: list[RunSummary]


class Round(TypedDict):
    """One strict ``RoundProjection`` serialized as the contract's ``Round``."""

    run_id: str
    round: int
    plan: ProjectedPlan
    node_states: dict[str, NodeState]
    node_results: dict[str, GraphResultItem]
    attestations: list[str]
    result: GraphPayload | None
    last_seq: int


class RunDetail(TypedDict):
    """The full ``RunDetail`` served by ``GET /api/v1/runs/{run_id}``."""

    api_version: int
    telemetry_schema_version: int
    observed_at: str
    run: dict[str, Any]
    rounds: list[Round]
    conversations: list[DagConversation]
    details: dict[str, Any]
    logs: NotRequired[dict[str, str]]
    launch: NotRequired[RunLaunch]


def _now(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).isoformat()


def _run_dirs(runs_dir: Path) -> Iterator[Path]:
    if not runs_dir.is_dir():
        return
    for entry in sorted(runs_dir.iterdir()):
        if entry.is_dir():
            yield entry


def read_launch_id(run_dir: Path) -> str | None:
    """The non-sensitive ``launch_id`` the run recorded, or ``None`` when absent.

    The run directory stores only this join key; the launcher and the sensitive
    session id live in the out-of-repo provenance record that ``launch_id`` resolves.
    """
    path = run_dir / "launch.json"
    if not path.is_file():
        return None
    try:
        raw = load_mapping(path)
    except (ConfigError, OSError):
        return None
    launch = raw.get("launch")
    return validate_launch_id(launch.get("launch_id")) if isinstance(launch, dict) else None


def resolve_launch(
    run_dir: Path,
    *,
    expose_launcher_session_id: bool = False,
    now: datetime | None = None,
) -> RunLaunch | None:
    """Join a run to its launching session via ``launch_id``.

    Returns the ``launch_id`` and the ``launcher`` resolved from the out-of-repo
    provenance record — ``"unknown"`` when that record is missing, expired, or
    invalid, without disturbing the graph. The launcher session id is included only
    when the caller's redaction policy permits it, since it may be sensitive.
    """
    launch_id = read_launch_id(run_dir)
    if launch_id is None:
        return None
    provenance = read_provenance(launch_id, now=now)
    result: RunLaunch = {
        "launch_id": launch_id,
        "launcher": provenance["launcher"] if provenance is not None else "unknown",
    }
    if expose_launcher_session_id and provenance is not None:
        result["launcher_session_id"] = provenance["launcher_session_id"]
    return result


#: Bytes of any single log tail returned to the UI. A log is a scan aid, not a
#: download; a bounded tail keeps one slow run from streaming an unbounded blob.
_LOG_TAIL_BYTES = 64_000

#: Logs that live *beneath the run directory* and are therefore safe to serve under
#: the configured root. Node gate logs live in ephemeral execution worktrees outside
#: this root and are referenced by the node result's artifact pointers instead.
_RUN_LOGS = {
    "orchestrator_stderr": ("orchestrator", "stderr.log"),
    "gate_log": ("orchestrator", "gate.log"),
}


def _tail(path: Path, max_bytes: int) -> str | None:
    """The last ``max_bytes`` of a log, UTF-8 decoded, or ``None`` when unreadable."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data[-max_bytes:].decode("utf-8", "replace")


def read_logs(run_dir: Path) -> dict[str, str]:
    """Bounded tails of the run's own logs, keyed by name and free of paths."""
    logs: dict[str, str] = {}
    for name, parts in _RUN_LOGS.items():
        tail = _tail(run_dir.joinpath(*parts), _LOG_TAIL_BYTES)
        if tail:
            logs[name] = tail
    return logs


def _node_counts(telemetry: RunTelemetry) -> dict[str, int]:
    return dict(Counter(node.status for node in telemetry.nodes))


def run_summary(
    run_dir: Path,
    telemetry: RunTelemetry,
    *,
    expose_launcher_session_id: bool = False,
    now: datetime | None = None,
) -> RunSummary:
    """One ``RunSummary`` row for the run-list view."""
    summary: RunSummary = {
        "run_id": telemetry.run_id,
        "state": telemetry.state,
        "phase": telemetry.phase,
        "last_event": telemetry.last_event,
        "telemetry_quality": telemetry.telemetry_quality,
        "timing": telemetry.timing,
        "node_counts": _node_counts(telemetry),
    }
    if telemetry.last_progress_at is not None:
        summary["last_progress_at"] = telemetry.last_progress_at
    launch = resolve_launch(run_dir, expose_launcher_session_id=expose_launcher_session_id, now=now)
    if launch is not None:
        summary["launch"] = launch
    return summary


def list_runs(
    runs_dir: Path,
    *,
    include_settled: bool = False,
    oneharness_bin: str = "oneharness",
    expose_launcher_session_id: bool = False,
    now: datetime | None = None,
) -> RunList:
    """The ``RunList``: every watchable run, most recent progress first.

    A run whose telemetry cannot be collected — a corrupt persisted result — is
    skipped rather than failing the whole list, so one bad run never blinds the UI
    to every healthy one.
    """
    summaries: list[RunSummary] = []
    for run_dir in _run_dirs(runs_dir):
        try:
            telemetry = collect_run(run_dir, oneharness_bin=oneharness_bin)
        except (ConfigError, HistoryError):
            continue
        if telemetry is None:
            continue
        if not include_settled and result_state_is_terminal(telemetry.state):
            continue
        summaries.append(
            run_summary(
                run_dir,
                telemetry,
                expose_launcher_session_id=expose_launcher_session_id,
                now=now,
            )
        )
    summaries.sort(key=lambda item: (-(item.get("last_progress_at") or 0.0), item["run_id"]))
    return {
        "api_version": API_VERSION,
        "telemetry_schema_version": TELEMETRY_SCHEMA_VERSION,
        "observed_at": _now(now),
        "runs": summaries,
    }


def round_record(events: list[Any], run_id: RunId, round_number: int) -> Round:
    """Serialize one strict ``RoundProjection`` as the contract's ``Round``."""
    projection = project_round(events, run_id, round_number)
    return {
        "run_id": projection.run_id,
        "round": projection.round,
        "plan": projection.plan,
        "node_states": projection.node_states,
        "node_results": projection.node_results,
        "attestations": list(projection.attestations),
        "result": projection.result,
        "last_seq": projection.last_seq,
    }


def _rounds(run_dir: Path, run_id: RunId) -> list[Round]:
    """Project every started round, or fail the whole detail on a corrupt stream."""
    try:
        events = read_strict_events(run_dir / JOURNAL_NAME, run_id)
        started = sorted({event.round for event in events if event.kind == "round-started"})
        return [round_record(events, run_id, number) for number in started]
    except ProjectionError as exc:
        raise ProjectionFailed(str(exc)) from exc


def run_detail(
    runs_dir: Path,
    run_id: str,
    *,
    oneharness_bin: str = "oneharness",
    expose_launcher_session_id: bool = False,
    now: datetime | None = None,
) -> RunDetail:
    """The full ``RunDetail`` for one run: telemetry, rounds, and conversations."""
    try:
        validated = validate_run_id(run_id)
    except ConfigError as exc:
        raise InvalidRunId(str(exc)) from exc
    run_dir = runs_dir / validated
    if not run_dir.is_dir() or latest_round(run_dir) is None:
        raise RunNotFound(f"no recorded run {validated!r}")
    try:
        telemetry = collect_run(run_dir, oneharness_bin=oneharness_bin)
    except ConfigError as exc:
        raise ProjectionFailed(str(exc)) from exc
    if telemetry is None:  # pragma: no cover - latest_round already proved a round exists
        raise RunNotFound(f"no recorded run {validated!r}")
    detail: RunDetail = {
        "api_version": API_VERSION,
        "telemetry_schema_version": TELEMETRY_SCHEMA_VERSION,
        "observed_at": _now(now),
        "run": telemetry.record(),
        "rounds": _rounds(run_dir, validated),
        "conversations": run_conversations(validated, oneharness_bin=oneharness_bin),
        "details": load_snapshot(run_dir).to_record(),
    }
    if logs := read_logs(run_dir):
        detail["logs"] = logs
    launch = resolve_launch(run_dir, expose_launcher_session_id=expose_launcher_session_id, now=now)
    if launch is not None:
        detail["launch"] = launch
    return detail


def run_conversation(
    runs_dir: Path,
    run_id: str,
    conversation_id: str,
    *,
    oneharness_bin: str = "oneharness",
) -> DagConversation:
    """One complete ``DagConversation`` addressed by its session id."""
    try:
        validated = validate_run_id(run_id)
    except ConfigError as exc:
        raise InvalidRunId(str(exc)) from exc
    wanted = validate_conversation_id(conversation_id)
    run_dir = runs_dir / validated
    if not run_dir.is_dir():
        raise RunNotFound(f"no recorded run {validated!r}")
    for conversation in run_conversations(validated, oneharness_bin=oneharness_bin):
        if conversation["conversation"]["id"] == wanted:
            return conversation
    raise RunNotFound(f"no conversation {wanted!r} in run {validated!r}")


def _file_token(path: Path) -> tuple[int, int]:
    """Size and modification time of one served file; ``(0, 0)`` when it is absent."""
    try:
        stat = path.stat()
    except OSError:
        return 0, 0
    return stat.st_size, stat.st_mtime_ns


def run_signature(run_dir: Path) -> tuple[int, ...]:
    """A cheap change token over every run-directory input ``run_detail`` serves.

    The SSE layer polls this to decide whether a run changed without re-projecting
    it. Journal byte length advances on every appended authoritative event and the
    round number advances when a new round lands, but the monitor's PR/check snapshot
    and the run's logs are written *outside* that event stream — watching only the
    journal would leave the UI showing a stale PR status with no invalidation to
    correct it.

    Conversations are deliberately out of scope: they live in oneharness history
    rather than under this root, and the contract invalidates them with
    ``conversation.changed`` rather than a run signature.
    """
    latest = latest_round(run_dir)
    round_number = latest[0] if latest is not None else 0
    watched = (
        run_dir / JOURNAL_NAME,
        snapshot_path(run_dir),
        run_dir / "launch.json",
        *(run_dir.joinpath(*parts) for parts in _RUN_LOGS.values()),
    )
    tokens: tuple[int, ...] = ()
    for path in watched:
        tokens += _file_token(path)
    return (round_number, *tokens)
