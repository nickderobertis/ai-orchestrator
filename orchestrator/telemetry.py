"""One versioned machine-readable index over every recorded run source."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypedDict, cast

from .detail_snapshot import CheckRollup
from .history import HistoryError, session_records, worker_sessions
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
)
from .verify import GateAttestation

TELEMETRY_SCHEMA_VERSION = 1
FailureClass = Literal[
    "agent", "gate", "checks", "publication", "timeout", "provider", "configuration", "unknown"
]


class TimingRecord(TypedDict):
    agent_seconds: float
    gate_seconds: float
    publication_wait_seconds: float
    wall_seconds: float


class MetricsRecord(TypedDict):
    retry_attempts: int
    retry_branch_reuses: int
    recovered_branches: int
    abandoned_branches: int
    no_diff_dispatches: int
    green_to_publication_seconds: list[float]


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

    def record(self) -> dict[str, object]:
        result: dict[str, object] = {
            "run_id": self.run_id,
            "state": self.state,
            "phase": self.phase,
            "last_event": self.last_event,
            "timing": self.timing,
            "nodes": [node.record() for node in self.nodes],
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


def _providers(run_id: RunId, oneharness_bin: str) -> tuple[list[Provider], float]:
    found: list[Provider] = []
    elapsed = 0.0
    try:
        sessions = worker_sessions(oneharness_bin=oneharness_bin)
    except HistoryError:
        return found, elapsed
    for session in sessions:
        if session.labels.get("run_id") != run_id:
            continue
        records = session_records(session)
        elapsed += sum(
            value / 1000
            for record in records
            if isinstance((value := record.get("duration_ms")), int) and not isinstance(value, bool)
        )
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
    return found, elapsed


def _node_record(node: str, item: GraphResultItem, events: list[Event]) -> NodeTelemetry:
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
    providers, agent_seconds = _providers(RunId(run_dir.name), oneharness_bin)
    gate_seconds = _gate_seconds(events)
    current = time.time() if now is None else now
    wall = (
        max(0.0, (last.at if state == "complete" and last else current) - events[0].at)
        if events
        else 0.0
    )
    publication_waits = _publication_waits(events)
    wait = sum(publication_waits)
    failure = next((found for item in items.values() if (found := _failure(item))), None)
    snapshot: DetailSnapshot = load_snapshot(run_dir)
    return RunTelemetry(
        run_id=RunId(run_dir.name),
        state=state,
        phase=_phase(last, state),
        last_progress_at=last.at if last else None,
        last_event=last.kind if last else "",
        timing=TimingRecord(
            agent_seconds=agent_seconds,
            gate_seconds=gate_seconds,
            publication_wait_seconds=wait,
            wall_seconds=wall,
        ),
        nodes=[_node_record(node, item, events) for node, item in items.items()],
        providers=providers,
        failure=failure,
        check_rollup=snapshot.check_rollup,
        green_to_publication_seconds=publication_waits,
    )


def _metrics(runs: list[RunTelemetry]) -> MetricsRecord:
    dispositions = [
        node.retry_lineage.disposition
        for run in runs
        for node in run.nodes
        if node.retry_lineage is not None
    ]
    return MetricsRecord(
        retry_attempts=len(dispositions),
        retry_branch_reuses=dispositions.count("reused"),
        recovered_branches=dispositions.count("recovered"),
        abandoned_branches=dispositions.count("abandoned"),
        no_diff_dispatches=sum(node.outcome == "no-changes" for run in runs for node in run.nodes),
        green_to_publication_seconds=[
            elapsed for run in runs for elapsed in run.green_to_publication_seconds
        ],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit the unified run telemetry index as JSON.")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--all", action="store_true", help="include completed runs")
    parser.add_argument("--oneharness-bin", default="oneharness")
    args = parser.parse_args(argv)
    records = (
        [
            telemetry
            for entry in sorted(args.runs_dir.iterdir())
            if args.runs_dir.is_dir() and entry.is_dir()
            if (telemetry := collect_run(entry, oneharness_bin=args.oneharness_bin)) is not None
            and (args.all or telemetry.state != "complete")
        ]
        if args.runs_dir.is_dir()
        else []
    )
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
