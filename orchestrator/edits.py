"""Versioned live-graph edit commands and transactional delta validation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, cast

if TYPE_CHECKING:
    from .graph import Graph

EditOp = Literal["add", "drop", "reparent", "retry", "attest", "complete"]
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True)
class EditCommand:
    op: EditOp
    payload: dict[str, Any]


class EditError(ValueError):
    """A down-channel command is malformed or invalid at the live frontier."""


def parse_commands(value: Mapping[str, Any]) -> tuple[EditCommand, ...]:
    """Validate the versioned down-channel command envelope."""
    raw = value.get("commands")
    if raw is None:
        return ()
    if value.get("version") != 1:
        raise EditError("edit command envelope requires version 1")
    if not isinstance(raw, list):
        raise EditError("edit commands must be a list")
    commands: list[EditCommand] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or item.get("op") not in {
            "add",
            "drop",
            "reparent",
            "retry",
            "attest",
            "complete",
        }:
            raise EditError(f"edit command #{index} has an unknown op")
        commands.append(EditCommand(cast(EditOp, item["op"]), dict(item)))
    return tuple(commands)


def graph_mapping(graph: Graph) -> dict[str, Any]:
    """Serialize the mutable graph through its existing public node contract."""
    tasks: list[dict[str, Any]] = []
    for node in graph.tasks:
        raw = dict(node.definition)
        if not raw:
            raw = {"id": node.id, "kind": node.kind, "task": node.task}
        if node.deps:
            raw["deps"] = list(node.deps)
        else:
            raw.pop("deps", None)
        tasks.append(raw)
    return {"schema_version": 3, "concurrency": graph.concurrency, "tasks": tasks}


def apply_edit(
    graph: Graph,
    command: EditCommand,
    *,
    states: Mapping[str, str],
    attestations: Sequence[str],
) -> tuple[Graph, list[dict[str, Any]]]:
    """Validate one delta against the frontier, returning a new graph and event specs."""
    op, item = command.op, command.payload
    from .graph import parse_graph
    from .plan import PlanError

    mapping = graph_mapping(graph)
    tasks = cast(list[dict[str, Any]], mapping["tasks"])
    by_id = {task["id"]: task for task in tasks}
    events: list[dict[str, Any]] = []
    if op == "add":
        node = item.get("node")
        if not isinstance(node, dict):
            raise EditError("add requires a node mapping")
        tasks.append(dict(node))
        events.append({"kind": "node-added", "detail": {"definition": _definition(node)}})
        for dependency in node.get("deps", []):
            events.append(
                {"kind": "edge-added", "detail": {"from": dependency, "to": node.get("id")}}
            )
    elif op == "reparent":
        node_id, deps = item.get("id"), item.get("deps")
        if not isinstance(node_id, str) or node_id not in by_id:
            raise EditError("reparent requires an existing node id")
        if states.get(node_id) is not None:
            raise EditError("reparent requires an unstarted node")
        if not isinstance(deps, list) or not all(isinstance(dep, str) for dep in deps):
            raise EditError("reparent deps must be a list of node ids")
        old = list(by_id[node_id].get("deps", []))
        by_id[node_id]["deps"] = list(deps)
        for dependency in old:
            events.append({"kind": "edge-removed", "detail": {"from": dependency, "to": node_id}})
        for dependency in deps:
            events.append({"kind": "edge-added", "detail": {"from": dependency, "to": node_id}})
        events.append({"kind": "reparent", "node": node_id, "detail": {"from": old, "to": deps}})
    elif op == "drop":
        node_id, fate = item.get("id"), item.get("dependents")
        if not isinstance(node_id, str) or node_id not in by_id:
            raise EditError("drop requires an existing node id")
        if fate not in {"drop", "detach"}:
            raise EditError("drop must define dependents as 'drop' or 'detach'")
        dependents = [task for task in tasks if node_id in task.get("deps", [])]
        target = by_id[node_id]
        target_repo = target.get("repo")
        unresolved_same_identity = [
            task
            for task in dependents
            if target_repo is not None
            and task.get("repo") == target_repo
            and states.get(task["id"]) != "done"
        ]
        alternative_anchors = [
            task
            for task in tasks
            if task["id"] != node_id
            and task.get("repo") == target_repo
            and states.get(task["id"]) == "done"
        ]
        if unresolved_same_identity and not alternative_anchors:
            raise EditError("drop would remove the last unresolved publication anchor")
        removed = {node_id}
        if fate == "drop":
            pending = [task["id"] for task in dependents]
            while pending:
                candidate = pending.pop()
                if candidate in removed:
                    continue
                removed.add(candidate)
                pending.extend(task["id"] for task in tasks if candidate in task.get("deps", []))
        else:
            for task in dependents:
                task["deps"] = [dep for dep in task.get("deps", []) if dep != node_id]
                events.append(
                    {"kind": "edge-removed", "detail": {"from": node_id, "to": task["id"]}}
                )
        tasks[:] = [task for task in tasks if task["id"] not in removed]
        events.extend(
            {"kind": "node-dropped", "node": dropped, "detail": {"dependents": fate}}
            for dropped in sorted(removed)
        )
    elif op == "retry":
        node_id, node = item.get("id"), item.get("node")
        if not isinstance(node_id, str) or states.get(node_id) not in {
            "running",
            "failed",
            "cancelled",
        }:
            raise EditError("retry requires a settled retryable node")
        if not isinstance(node, dict):
            raise EditError("retry requires a replacement node mapping")
        if node.get("id") in by_id:
            raise EditError("retry replacement id must be new")
        tasks.append(dict(node))
        events.append(
            {"kind": "retry-requested", "node": node_id, "detail": {"replacement": node.get("id")}}
        )
        events.append(
            {"kind": "node-added", "detail": {"definition": _definition(node), "retry_of": node_id}}
        )
        for dependency in node.get("deps", []):
            events.append(
                {"kind": "edge-added", "detail": {"from": dependency, "to": node.get("id")}}
            )
    elif op == "attest":
        ref = item.get("ref")
        if not isinstance(ref, str) or states.get(ref) != "waiting":
            raise EditError("attest requires a currently-ready human action")
        if ref in attestations:
            raise EditError("human action was already attested")
        events.append({"kind": "human-attested", "node": ref, "detail": {"ref": ref}})
    elif op == "complete":
        reason = item.get("reason")
        if not isinstance(reason, str):
            raise EditError("complete requires a string reason")
        events.append({"kind": "run-completed", "detail": {"reason": reason}})
        return graph, events
    try:
        updated = parse_graph(mapping)
    except PlanError as exc:
        raise EditError(str(exc)) from exc
    return updated, events


def _definition(node: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in node.items() if key != "deps" or value == []}
