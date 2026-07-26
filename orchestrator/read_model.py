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
from typing import Any

from .config import ConfigError
from .conversations import run_conversations
from .history import HistoryError
from .journal import JOURNAL_NAME
from .monitor import load_snapshot
from .projection import ProjectionError, project_round, read_strict_events
from .runs import RunId, latest_round, load_mapping, result_state_is_terminal, validate_run_id
from .telemetry import TELEMETRY_SCHEMA_VERSION, RunTelemetry, collect_run

API_VERSION = 1

#: The launcher harnesses a persisted ``launch.json`` may name. Kept local rather
#: than imported from ``dispatch`` so the read path does not pull the whole dispatch
#: /onejudge-SDK stack into a viewing process. ``dispatch.LAUNCHER_KINDS`` is the
#: write-side source; ``test_read_model.test_launcher_kinds_match_dispatch`` is the
#: drift gate that fails if the two ever diverge.
_LAUNCHER_KINDS = frozenset({"claude-code", "codex", "unknown"})


class ReadError(Exception):
    """Base class for a read the server maps to an HTTP status."""


class InvalidRunId(ReadError):
    """A run identifier failed validation at the trust boundary (422)."""


class RunNotFound(ReadError):
    """No recorded run matches the identifier (404)."""


class ProjectionFailed(ReadError):
    """The authoritative journal cannot be folded into a consistent graph (409)."""


def _now(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).isoformat()


def _run_dirs(runs_dir: Path) -> Iterator[Path]:
    if not runs_dir.is_dir():
        return
    for entry in sorted(runs_dir.iterdir()):
        if entry.is_dir():
            yield entry


def read_launcher(run_dir: Path) -> dict[str, Any] | None:
    """The validated launching-session provenance, or ``None`` when absent/invalid.

    A launch record is a convenience join key, never a decision input, so a missing
    or malformed one degrades to "unknown launcher" rather than failing the read.
    """
    path = run_dir / "launch.json"
    if not path.is_file():
        return None
    try:
        raw = load_mapping(path)
    except (ConfigError, OSError):
        return None
    launcher = raw.get("launcher")
    if not isinstance(launcher, dict):
        return None
    kind = launcher.get("kind")
    if not isinstance(kind, str) or kind not in _LAUNCHER_KINDS:
        return None
    result: dict[str, Any] = {"kind": kind}
    session_id = launcher.get("session_id")
    if isinstance(session_id, str) and session_id:
        result["session_id"] = session_id
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


def run_summary(run_dir: Path, telemetry: RunTelemetry) -> dict[str, Any]:
    """One ``RunSummary`` row for the run-list view."""
    summary: dict[str, Any] = {
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
    if (launcher := read_launcher(run_dir)) is not None:
        summary["launcher"] = launcher
    return summary


def list_runs(
    runs_dir: Path,
    *,
    include_settled: bool = False,
    oneharness_bin: str = "oneharness",
    now: datetime | None = None,
) -> dict[str, Any]:
    """The ``RunList``: every watchable run, most recent progress first.

    A run whose telemetry cannot be collected — a corrupt persisted result — is
    skipped rather than failing the whole list, so one bad run never blinds the UI
    to every healthy one.
    """
    summaries: list[dict[str, Any]] = []
    for run_dir in _run_dirs(runs_dir):
        try:
            telemetry = collect_run(run_dir, oneharness_bin=oneharness_bin)
        except (ConfigError, HistoryError):
            continue
        if telemetry is None:
            continue
        if not include_settled and result_state_is_terminal(telemetry.state):
            continue
        summaries.append(run_summary(run_dir, telemetry))
    summaries.sort(key=lambda item: (-(item.get("last_progress_at") or 0.0), item["run_id"]))
    return {
        "api_version": API_VERSION,
        "telemetry_schema_version": TELEMETRY_SCHEMA_VERSION,
        "observed_at": _now(now),
        "runs": summaries,
    }


def round_record(events: list[Any], run_id: RunId, round_number: int) -> dict[str, Any]:
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


def _rounds(run_dir: Path, run_id: RunId) -> list[dict[str, Any]]:
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
    now: datetime | None = None,
) -> dict[str, Any]:
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
    detail: dict[str, Any] = {
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
    if (launcher := read_launcher(run_dir)) is not None:
        detail["launcher"] = launcher
    return detail


def run_conversation(
    runs_dir: Path,
    run_id: str,
    conversation_id: str,
    *,
    oneharness_bin: str = "oneharness",
) -> dict[str, Any]:
    """One complete ``DagConversation`` addressed by its session id."""
    try:
        validated = validate_run_id(run_id)
    except ConfigError as exc:
        raise InvalidRunId(str(exc)) from exc
    run_dir = runs_dir / validated
    if not run_dir.is_dir():
        raise RunNotFound(f"no recorded run {validated!r}")
    for conversation in run_conversations(validated, oneharness_bin=oneharness_bin):
        if conversation["conversation"]["id"] == conversation_id:
            return conversation
    raise RunNotFound(f"no conversation {conversation_id!r} in run {validated!r}")


def run_signature(run_dir: Path) -> tuple[int, int]:
    """A cheap change token for one run: latest round number and journal size.

    The SSE layer polls this to decide whether a run changed without re-projecting
    it. Journal byte length advances on every appended authoritative event, and the
    round number advances when a new round directory lands.
    """
    latest = latest_round(run_dir)
    round_number = latest[0] if latest is not None else 0
    journal = run_dir / JOURNAL_NAME
    try:
        size = journal.stat().st_size
    except OSError:
        size = 0
    return round_number, size
