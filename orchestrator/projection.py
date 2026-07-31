"""Strict projection of authoritative static-round graph events.

The ordinary journal reader remains deliberately tolerant for monitoring old logs.
This module is the opposite boundary: every durable line must be understood and
ordered before it may influence execution or regenerate compatibility artifacts.
"""

# llmlint: ignore-file[changed_behavior_has_e2e] valid committed edits and atomic replay run through
# the real CLI in test_live_edit_e2e.py; malformed envelopes, impossible post-drop settlements, and
# invalid committed topologies require corrupting the authoritative journal outside that public
# interface, so the strict projection boundary exercises those fail-closed paths directly.

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypedDict, cast, get_args

from .config import ConfigError
from .edits import EDIT_PROTOCOL_VERSION, EditError, parse_commands
from .graph import parse_graph
from .journal import (
    AUDIT_EVENT_KINDS,
    AUTHORITATIVE_EVENT_KINDS,
    COMMITTED_EDIT_COMMAND_FIELD,
    COMMITTED_EDIT_COMMAND_SINCE,
    COMMITTED_EDIT_OPERATIONS_FIELD,
    OPTIONAL_EVENT_FIELDS,
    REQUIRED_EVENT_FIELDS,
    TERMINAL_NODE_RESULT_FIELD,
    Event,
    parse_event,
)
from .plan import PlanError, parse_cross_dag_dependency
from .runs import GraphPayload, GraphResultItem, RunId, as_result_payload


class ProjectionError(ValueError):
    """An authoritative event stream cannot be folded safely."""


# llmlint: ignore[contracts_have_one_source_or_a_drift_gate] the executor keeps run statuses as
# plain strings by the codebase string-status convention (no canonical enum to source from); this
# strict-reader view is drift-gated where it matters — round-finished folding rejects any state that
# disagrees with the recorded result, so a status the executor emits but omits here cannot project.
NodeState = Literal["running", "done", "failed", "waiting", "cancelled"]

#: The states a node can *settle* in — `NodeState` minus the one that means it has
#: not. Named here rather than restated at each reader so a strict fold and a
#: degrading read-only view judge a recorded status against the same domain.
TERMINAL_NODE_STATES: frozenset[str] = frozenset(get_args(NodeState)) - {"running"}


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
    node_results: dict[str, GraphResultItem]
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
    results: dict[str, GraphResultItem] = field(default_factory=dict)
    attestations: list[str] = field(default_factory=list)
    result: GraphPayload | None = None
    dropped_ids: set[str] = field(default_factory=set)


def read_strict_events(path: Path, run_id: RunId) -> list[Event]:
    """Read a complete authoritative stream, rejecting junk and future events.

    The sequence rule enforces what strictness is actually for: **no authoritative
    record is missing**. It therefore rejects a gap and a rewind, and tolerates one
    thing that is neither — a *collision*, where two writers allocated the same
    number and both records are physically present, in order. Nothing is lost there,
    and the file's own append order still totally orders the stream.

    That tolerance is not cosmetic. This reader is what `channel-reply` validates a
    live edit against, so treating a collision as fatal ends a planner's supervision
    of a run that is otherwise entirely healthy — every drop, retry and attestation
    refused for the remainder of its life. A journal defect must never be able to do
    that; `journal.claimed_sequence` is what stops new collisions being written.
    """
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
        if record.get("kind") in {
            "node-dropped",
            "edge-removed",
            "reparent",
            "retry-requested",
        }:
            raise ProjectionError(
                f"unknown authoritative event at line {line_number}: {record.get('kind')!r}"
            )
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
        # A collision is this record repeating the number its predecessor was issued.
        # Both are here, in order, so nothing is missing — and `expected` stays where
        # it is, because the writers themselves went on from the shared number.
        collision = bool(events) and event.seq == expected - 1
        if not collision and event.seq != expected:
            raise ProjectionError(
                "authoritative event sequence must be contiguous: "
                f"expected {expected}, got {event.seq}"
            )
        events.append(event)
        if not collision:
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
            case "edit-committed":
                operations = detail.get(COMMITTED_EDIT_OPERATIONS_FIELD)
                if not isinstance(operations, list) or not operations:
                    raise ProjectionError("edit-committed requires non-empty operations")
                # The submitted command is required from the schema version that
                # introduced it and absent from every record written before it, so a
                # v5 log still replays while a current one cannot omit the command
                # that produced its mutations.
                submitted = detail.get(COMMITTED_EDIT_COMMAND_FIELD)
                if submitted is None:
                    if event.version >= COMMITTED_EDIT_COMMAND_SINCE:
                        raise ProjectionError("edit-committed requires the submitted command")
                else:
                    # Validated by the same parser the command passed on the wire, so a
                    # recorded command and an accepted one are held to one contract.
                    # Per-delta shape stays `apply_edit`'s transactional job, exactly as
                    # at submission; a replay re-derives its graph from the compiled
                    # operations rather than from this payload.
                    try:
                        parse_commands({"version": EDIT_PROTOCOL_VERSION, "commands": [submitted]})
                    except EditError as exc:
                        raise ProjectionError(
                            f"edit-committed command must be a known edit payload: {exc}"
                        ) from exc
                snapshot = _copy_builder(builder)
                try:
                    for operation in operations:
                        _fold_edit_operation(builder, operation)
                    _validate_live_topology(builder)
                except ProjectionError:
                    builder = snapshot
                    raise
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
            case "human-waiting" if event.node is not None and event.step is None:
                if event.node not in builder.node_ids:
                    raise ProjectionError(f"human-waiting references unknown node {event.node!r}")
                if event.node in builder.states:
                    raise ProjectionError(f"node {event.node!r} started more than once")
                builder.states[event.node] = "waiting"
                _fold_node_result(builder, event)
            case "node-settled" | "node-failed" if event.node is not None:
                dropped = event.node in builder.dropped_ids
                if not dropped and builder.states.get(event.node) != "running":
                    raise ProjectionError(f"node {event.node!r} settled without one start")
                status = "failed" if event.kind == "node-failed" else detail.get("status")
                # `detail` is persisted JSON, so this value can be a list or an
                # object — which a bare membership test would answer with a
                # `TypeError` about hashability rather than with this reader's own
                # error about what it required.
                if not isinstance(status, str) or status not in TERMINAL_NODE_STATES:
                    raise ProjectionError("node-settled requires a terminal status")
                # The membership test above is the check; the cast only tells the type
                # checker what a `frozenset[str]` cannot, which is that it narrowed.
                builder.states[event.node] = cast(NodeState, status)
                _fold_node_result(builder, event)
                if dropped:
                    if status not in {"done", "cancelled"}:
                        raise ProjectionError(
                            "a dropped running node must settle cancelled or finish publication"
                        )
                    builder.states.pop(event.node, None)
                    builder.results.pop(event.node, None)
            case "human-attested":
                ref = detail.get("ref")
                if not isinstance(ref, str) or not ref:
                    raise ProjectionError("human-attested requires a non-empty ref")
                if event.node is None or event.node not in builder.node_ids:
                    raise ProjectionError("human-attested references an unknown graph node")
                expected_ref = (
                    str(event.node) if event.step is None else f"{event.node}/{event.step}"
                )
                if ref != expected_ref:
                    raise ProjectionError(
                        f"human-attested ref {ref!r} does not match locator {expected_ref!r}"
                    )
                definition = next(node for node in builder.nodes if node["id"] == event.node)
                top_level_human = definition.get("kind") == "human" and event.step is None
                recorded_actions = (
                    [
                        action.get("ref")
                        for action in builder.result["results"]
                        .get(str(event.node), {})
                        .get("human_actions", [])
                        if isinstance(action, dict)
                    ]
                    if builder.result is not None
                    else []
                )
                if not top_level_human and ref not in recorded_actions:
                    raise ProjectionError(
                        f"human-attested target {ref!r} is not a projected human action"
                    )
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
        if target not in by_id or (
            source not in by_id and parse_cross_dag_dependency(source) is None
        ):
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
        dict(builder.results),
        tuple(builder.attestations),
        builder.result,
        last_seq,
    )


def _copy_builder(builder: _RoundBuilder) -> _RoundBuilder:
    return _RoundBuilder(
        meta=dict(builder.meta),
        nodes=[dict(node) for node in builder.nodes],
        node_ids=set(builder.node_ids),
        edges=list(builder.edges),
        states=dict(builder.states),
        results=dict(builder.results),
        attestations=list(builder.attestations),
        result=builder.result,
        dropped_ids=set(builder.dropped_ids),
    )


def _fold_edit_operation(builder: _RoundBuilder, operation: object) -> None:
    if not isinstance(operation, dict) or not isinstance(operation.get("kind"), str):
        raise ProjectionError("edit operation must contain a kind")
    kind = operation["kind"]
    detail = operation.get("detail", {})
    node = operation.get("node")
    if not isinstance(detail, dict):
        raise ProjectionError("edit operation detail must be a mapping")
    match kind:
        case "node-added":
            definition = detail.get("definition")
            if not isinstance(definition, dict) or not isinstance(definition.get("id"), str):
                raise ProjectionError("node-added requires a node definition")
            if definition["id"] in builder.node_ids:
                raise ProjectionError(f"duplicate node-added for {definition['id']!r}")
            builder.node_ids.add(definition["id"])
            builder.nodes.append(dict(definition))
        case "edge-added" | "edge-removed":
            source, target = detail.get("from"), detail.get("to")
            if not isinstance(source, str) or not isinstance(target, str):
                raise ProjectionError(f"{kind} endpoints must be strings")
            edge = (source, target)
            if kind == "edge-added":
                if edge in builder.edges:
                    raise ProjectionError("duplicate edge-added")
                builder.edges.append(edge)
            else:
                if edge not in builder.edges:
                    raise ProjectionError("edge-removed references an absent edge")
                builder.edges.remove(edge)
        case "node-dropped":
            if not isinstance(node, str) or node not in builder.node_ids:
                raise ProjectionError("node-dropped references an unknown node")
            builder.node_ids.remove(node)
            builder.dropped_ids.add(node)
            builder.nodes = [definition for definition in builder.nodes if definition["id"] != node]
            builder.edges = [edge for edge in builder.edges if node not in edge]
            builder.states.pop(node, None)
            builder.results.pop(node, None)
        case "human-attested":
            ref = detail.get("ref")
            if not isinstance(ref, str) or ref != node or builder.states.get(ref) != "waiting":
                raise ProjectionError("human-attested target is not currently waiting")
            if ref in builder.attestations:
                raise ProjectionError(f"human action {ref!r} was attested more than once")
            builder.attestations.append(ref)
            builder.states[ref] = "done"
        case "reparent" | "retry-requested" | "completion-requested":
            return
        case _:
            raise ProjectionError(f"unknown committed edit operation {kind!r}")


def _validate_live_topology(builder: _RoundBuilder) -> None:
    by_id = {node["id"]: dict(node) for node in builder.nodes}
    for node in by_id.values():
        node["deps"] = [source for source, target in builder.edges if target == node["id"]]
    try:
        parse_graph({**builder.meta, "tasks": list(by_id.values())})
    except PlanError as exc:
        raise ProjectionError(str(exc)) from exc


def project_run(path: Path, run_id: RunId, round_number: int) -> RoundProjection:
    return project_round(read_strict_events(path, run_id), run_id, round_number)


def _fold_node_result(builder: _RoundBuilder, event: Event) -> None:
    """Validate and retain a v2 terminal node payload when one is present."""
    raw = event.detail.get(TERMINAL_NODE_RESULT_FIELD)
    if raw is None:
        if event.version >= 2:
            raise ProjectionError(f"{event.kind} requires a serialized node result")
        return
    if not isinstance(raw, dict) or event.node is None:
        raise ProjectionError(f"{event.kind} has an invalid serialized node result")
    status = raw.get("status")
    if status != builder.states.get(str(event.node)):
        raise ProjectionError(f"{event.kind} has an invalid serialized node result status")
    match status:
        case "done":
            state = "complete"
        case "waiting":
            state = "waiting"
        case _:
            state = "failed"
    payload = {
        "schema_version": 2,
        "ok": status == "done",
        "state": state,
        "started_order": [str(event.node)],
        "results": {str(event.node): raw},
    }
    try:
        validated = as_result_payload(payload)
    except ConfigError as exc:
        raise ProjectionError(f"{event.kind} has an invalid serialized node result: {exc}") from exc
    builder.results[str(event.node)] = validated["results"][str(event.node)]
