"""Strict projection of authoritative static-round graph events.

The ordinary journal reader remains deliberately tolerant for monitoring old logs.
This module is the opposite boundary: every durable line must be understood and
ordered before it may influence execution or regenerate compatibility artifacts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from .config import ConfigError
from .graph import parse_graph
from .journal import (
    AUDIT_EVENT_KINDS,
    AUTHORITATIVE_EVENT_KINDS,
    OPTIONAL_EVENT_FIELDS,
    REQUIRED_EVENT_FIELDS,
    Event,
    parse_event,
)
from .plan import PlanError
from .runs import GraphPayload, RunId, as_result_payload


class ProjectionError(ValueError):
    """An authoritative event stream cannot be folded safely."""


NodeState = Literal["running", "done", "failed", "waiting"]


class ProjectedPlan(TypedDict, total=False):
    """Serialized plan retained exactly for compatibility artifact regeneration."""

    tasks: list[dict[str, Any]]
    schema_version: int
    concurrency: int
    name: str


@dataclass(frozen=True)
class RoundProjection:
    run_id: RunId
    round: int
    plan: ProjectedPlan
    node_states: dict[str, NodeState]
    attestations: tuple[str, ...]
    result: GraphPayload | None
    last_seq: int


@dataclass
class _RoundBuilder:
    meta: dict[str, Any] = field(default_factory=dict)
    nodes: list[dict[str, Any]] = field(default_factory=list)
    node_ids: set[str] = field(default_factory=set)
    edges: list[tuple[str, str]] = field(default_factory=list)
    states: dict[str, NodeState] = field(default_factory=dict)
    attestations: list[str] = field(default_factory=list)
    result: GraphPayload | None = None


def read_strict_events(path: Path, run_id: RunId) -> list[Event]:
    """Read a complete authoritative stream, rejecting junk and future events."""
    if not path.exists():
        return []
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raw = raw[: raw.rfind(b"\n") + 1] if b"\n" in raw else b""
    events: list[Event] = []
    expected = 1
    for line_number, raw_line in enumerate(raw.splitlines(), 1):
        if not raw_line.strip():
            raise ProjectionError(f"authoritative event line {line_number} is blank")
        try:
            record = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProjectionError(f"malformed authoritative event at line {line_number}") from exc
        replay_kinds = frozenset(AUTHORITATIVE_EVENT_KINDS) | AUDIT_EVENT_KINDS
        if not isinstance(record, dict) or record.get("kind") not in replay_kinds:
            kind = record.get("kind") if isinstance(record, dict) else None
            raise ProjectionError(f"unknown authoritative event at line {line_number}: {kind!r}")
        unexpected = set(record) - set(REQUIRED_EVENT_FIELDS) - set(OPTIONAL_EVENT_FIELDS)
        if unexpected:
            raise ProjectionError(
                f"authoritative event at line {line_number} has unknown fields: "
                + ", ".join(sorted(unexpected))
            )
        event = parse_event(record)
        if event is None:
            raise ProjectionError(f"invalid authoritative event at line {line_number}")
        if event.run_id != run_id:
            raise ProjectionError(
                f"authoritative event at line {line_number} belongs to another run"
            )
        if event.seq != expected:
            raise ProjectionError(
                "authoritative event sequence must be contiguous: "
                f"expected {expected}, got {event.seq}"
            )
        events.append(event)
        expected += 1
    return events


def project_round(events: list[Event], run_id: RunId, round_number: int) -> RoundProjection:
    """Fold one static round and validate its reconstructed whole-plan shape."""
    builder = _RoundBuilder()
    last_seq = 0
    round_started = False
    round_finished = False
    for event in events:
        last_seq = event.seq
        if event.round != round_number:
            continue
        detail = dict(event.detail)
        if (
            round_finished
            and event.kind in AUTHORITATIVE_EVENT_KINDS
            and event.kind != "human-attested"
        ):
            raise ProjectionError(f"authoritative event {event.kind!r} follows round-finished")
        if (
            not round_started
            and event.kind in AUTHORITATIVE_EVENT_KINDS
            and event.kind not in {"node-added", "edge-added", "round-started", "human-attested"}
        ):
            raise ProjectionError(f"authoritative event {event.kind!r} precedes round-started")
        if round_started and event.kind in {"node-added", "edge-added"}:
            raise ProjectionError(f"authoritative event {event.kind!r} follows round-started")
        match event.kind:
            case "node-added":
                node = detail.get("definition")
                if not isinstance(node, dict) or not isinstance(node.get("id"), str):
                    raise ProjectionError("node-added requires a node definition")
                if node["id"] in builder.node_ids:
                    raise ProjectionError(f"duplicate node-added for {node['id']!r}")
                builder.node_ids.add(node["id"])
                builder.nodes.append(dict(node))
            case "edge-added":
                source, target = detail.get("from"), detail.get("to")
                if not isinstance(source, str) or not isinstance(target, str):
                    raise ProjectionError("edge-added requires string 'from' and 'to' references")
                builder.edges.append((source, target))
            case "round-started":
                if round_started:
                    raise ProjectionError("round-started may occur only once")
                meta = detail.get("plan")
                if not isinstance(meta, dict):
                    raise ProjectionError("round-started requires plan metadata")
                builder.meta = dict(meta)
                round_started = True
            case "node-started" if event.node is not None:
                if event.node not in builder.node_ids:
                    raise ProjectionError(f"node-started references unknown node {event.node!r}")
                if event.node in builder.states:
                    raise ProjectionError(f"node {event.node!r} started more than once")
                builder.states[event.node] = "running"
            case "human-waiting" if event.node is not None:
                if event.node not in builder.node_ids:
                    raise ProjectionError(f"human-waiting references unknown node {event.node!r}")
                if event.node in builder.states:
                    raise ProjectionError(f"node {event.node!r} started more than once")
                builder.states[event.node] = "waiting"
            case "node-settled" | "node-failed" if event.node is not None:
                if builder.states.get(event.node) != "running":
                    raise ProjectionError(f"node {event.node!r} settled without one start")
                status = "failed" if event.kind == "node-failed" else detail.get("status")
                if status not in {"done", "failed", "waiting"}:
                    raise ProjectionError("node-settled requires a terminal status")
                builder.states[event.node] = status
            case "human-attested":
                ref = detail.get("ref")
                if not isinstance(ref, str) or not ref:
                    raise ProjectionError("human-attested requires a non-empty ref")
                if ref in builder.attestations:
                    raise ProjectionError(f"human action {ref!r} was attested more than once")
                builder.attestations.append(ref)
            case "round-finished":
                if round_finished:
                    raise ProjectionError("round-finished may occur only once")
                payload = detail.get("result")
                if not isinstance(payload, dict):
                    raise ProjectionError("round-finished requires the projected result")
                try:
                    builder.result = as_result_payload(payload)
                except ConfigError as exc:
                    raise ProjectionError(f"round-finished result is invalid: {exc}") from exc
                topology = builder.node_ids
                unknown_results = sorted(set(builder.result["results"]) - topology)
                if unknown_results:
                    raise ProjectionError(
                        "round-finished result references unknown node(s): "
                        + ", ".join(unknown_results)
                    )
                unknown_started = sorted(set(builder.result["started_order"]) - topology)
                if unknown_started:
                    raise ProjectionError(
                        "round-finished started_order references unknown node(s): "
                        + ", ".join(unknown_started)
                    )
                never_started = sorted(set(builder.result["started_order"]) - set(builder.states))
                if never_started:
                    raise ProjectionError(
                        "round-finished started_order contains node(s) without a start event: "
                        + ", ".join(never_started)
                    )
                for node, state in builder.states.items():
                    recorded = builder.result["results"].get(node)
                    if state == "running" or recorded is None or recorded.get("status") != state:
                        raise ProjectionError(
                            f"round-finished result disagrees with projected node {node!r}"
                        )
                round_finished = True
            case _:
                pass

    if not builder.nodes:
        raise ProjectionError(f"round {round_number} has no node-added events")
    if not round_started:
        raise ProjectionError(f"round {round_number} has no round-started event")
    plan = cast(ProjectedPlan, {**builder.meta, "tasks": builder.nodes})
    by_id = {node["id"]: node for node in builder.nodes}
    for source, target in builder.edges:
        if source not in by_id or target not in by_id:
            raise ProjectionError(f"edge {source!r} -> {target!r} references an unknown node")
        deps = by_id[target].setdefault("deps", [])
        if source in deps:
            raise ProjectionError(f"duplicate edge {source!r} -> {target!r}")
        deps.append(source)
    try:
        parse_graph(dict(plan))
    except PlanError as exc:
        raise ProjectionError(f"projected plan is invalid: {exc}") from exc
    return RoundProjection(
        run_id,
        round_number,
        plan,
        dict(builder.states),
        tuple(builder.attestations),
        builder.result,
        last_seq,
    )


def project_run(path: Path, run_id: RunId, round_number: int) -> RoundProjection:
    return project_round(read_strict_events(path, run_id), run_id, round_number)
