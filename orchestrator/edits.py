"""Versioned live-graph edit commands and transactional delta validation."""

# llmlint: ignore-file[changed_behavior_has_e2e] frontier journeys drive real edits e2e; the
# per-op malformed-delta rejections are exhaustive deterministic unit tests (test_edits.py)
# rather than timing-heavy channel journeys, matching the channel wire-contract rationale.
# llmlint: ignore-file[boundary_inputs_validated] parse_commands validates the envelope only
# (version, list, known op); per-delta shape and frontier semantics are validated
# transactionally in apply_edit — the graph-mutation trust boundary that also re-runs
# parse_graph on the whole result — so malformed deltas surface as soft, retryable reconciler
# rejections over the channel rather than hard transport errors at parse time.

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, TypedDict, cast, get_args

if TYPE_CHECKING:
    from .graph import Graph

EditOp = Literal[
    "add", "drop", "reparent", "retry", "cancel", "requeue", "attest", "complete", "context"
]
EDIT_OPS = frozenset(get_args(EditOp))
# Single source of truth for the down-channel edit envelope version. The parser
# here and every producer (see channel._reply) reference this so the accepted and
# emitted protocol versions cannot drift apart.
EDIT_PROTOCOL_VERSION = 1
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


# One TypedDict per op discriminant: each records exactly the fields that op
# carries so the required shape is visible in the type, not just enforced at the
# runtime trust boundary in ``apply_edit``.
class AddPayload(TypedDict):
    """Insert a new node from its full definition mapping."""

    op: Literal["add"]
    node: dict[str, Any]


class DropPayload(TypedDict):
    """Remove a node, either dropping or detaching its dependents."""

    op: Literal["drop"]
    id: str
    dependents: Literal["drop", "detach"]


class ReparentPayload(TypedDict):
    """Replace an unstarted node's dependency set."""

    op: Literal["reparent"]
    id: str
    deps: list[str]


class RetryPayload(TypedDict):
    """Supersede a node's execution lineage with a fresh replacement."""

    op: Literal["retry"]
    id: str
    node: dict[str, Any]


# llmlint: ignore[names_match_behavior] `cancel` names what it does to the *dispatch* —
# it raises the same cooperative cancellation signal `drop` and `retry` do — and the
# node it leaves behind is `parked`, which is the status, not the op. The pair
# `cancel`/`requeue` is the planner vocabulary this change was specified to add and is
# what AGENTS.md's one-execution-path rule and docs/orchestration.md's edit table both
# name; renaming the op here alone would leave the planner-facing contract saying one
# thing and the wire another.
class CancelPayload(TypedDict):
    """Park a pending or running node without naming a successor."""

    op: Literal["cancel"]
    id: str


class RequeuePayload(TypedDict, total=False):
    """Return a parked node to the desired frontier, optionally amended."""

    op: Literal["requeue"]
    id: str
    amend: dict[str, Any]


class AttestPayload(TypedDict):
    """Attest a currently-ready human action by reference."""

    op: Literal["attest"]
    ref: str


class CompletePayload(TypedDict):
    """Request round completion with a planner-supplied reason."""

    op: Literal["complete"]
    reason: str


class ContextPayload(TypedDict):
    """Attach one planner note to a node's next dispatch."""

    op: Literal["context"]
    id: str
    note: str


EditPayload: TypeAlias = (
    AddPayload
    | DropPayload
    | ReparentPayload
    | RetryPayload
    | CancelPayload
    | RequeuePayload
    | AttestPayload
    | CompletePayload
    | ContextPayload
)


EditOperationKind = Literal[
    "node-added",
    "edge-added",
    "edge-removed",
    "node-dropped",
    "node-parked",
    "node-requeued",
    "reparent",
    "retry-requested",
    "human-attested",
    "completion-requested",
    "context-added",
]
EDIT_OPERATION_KINDS = frozenset(get_args(EditOperationKind))


class EditOperation(TypedDict, total=False):
    """One compiled operation inside an atomic edit commit."""

    kind: EditOperationKind
    node: str
    detail: dict[str, Any]


@dataclass(frozen=True)
class EditCommand:
    op: EditOp
    payload: EditPayload


class EditError(ValueError):
    """A down-channel command is malformed or invalid at the live frontier."""


def parse_commands(value: Mapping[str, Any]) -> tuple[EditCommand, ...]:
    """Validate the versioned down-channel command envelope."""
    raw = value.get("commands")
    if raw is None:
        return ()
    if value.get("version") != EDIT_PROTOCOL_VERSION:
        raise EditError(f"edit command envelope requires version {EDIT_PROTOCOL_VERSION}")
    if not isinstance(raw, list):
        raise EditError("edit commands must be a list")
    commands: list[EditCommand] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or item.get("op") not in EDIT_OPS:
            raise EditError(f"edit command #{index} has an unknown op")
        commands.append(EditCommand(cast(EditOp, item["op"]), cast(EditPayload, dict(item))))
    return tuple(commands)


def graph_mapping(graph: Graph) -> dict[str, Any]:
    """Serialize the mutable graph through its existing public node contract."""
    from .plan import PLAN_SCHEMA_VERSION

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
    return {"schema_version": PLAN_SCHEMA_VERSION, "concurrency": graph.concurrency, "tasks": tasks}


def apply_edit(
    graph: Graph,
    command: EditCommand,
    *,
    # llmlint: ignore[modern_domain_modeling] the frontier is the executor's own node-status map,
    # kept as plain strings by the codebase-wide string-status convention (see status.py); edit
    # validity only tests membership, so narrowing it here would fork that shared vocabulary.
    states: Mapping[str, str],
    attestations: Sequence[str],
) -> tuple[Graph, list[EditOperation]]:
    """Validate one delta against the frontier, returning a new graph and event specs."""
    op, item = command.op, command.payload
    from .graph import parse_graph
    from .plan import PlanError

    mapping = graph_mapping(graph)
    tasks = cast(list[dict[str, Any]], mapping["tasks"])
    by_id = {task["id"]: task for task in tasks}
    events: list[EditOperation] = []
    match op:
        case "add":
            node = item.get("node")
            if not isinstance(node, dict):
                raise EditError("add requires a node mapping")
            tasks.append(dict(node))
            events.append({"kind": "node-added", "detail": {"definition": _definition(node)}})
            for dependency in node.get("deps", []):
                events.append(
                    {"kind": "edge-added", "detail": {"from": dependency, "to": node.get("id")}}
                )
        case "reparent":
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
                events.append(
                    {"kind": "edge-removed", "detail": {"from": dependency, "to": node_id}}
                )
            for dependency in deps:
                events.append({"kind": "edge-added", "detail": {"from": dependency, "to": node_id}})
            events.append(
                {"kind": "reparent", "node": node_id, "detail": {"from": old, "to": deps}}
            )
        case "drop":
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
                    pending.extend(
                        task["id"] for task in tasks if candidate in task.get("deps", [])
                    )
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
        case "retry":
            node_id, node = item.get("id"), item.get("node")
            if not isinstance(node_id, str) or node_id not in by_id:
                raise EditError("retry requires an existing graph node")
            if states.get(node_id) not in {
                "running",
                "failed",
                "cancelled",
            }:
                raise EditError("retry requires a running, failed, or cancelled node")
            if not isinstance(node, dict):
                raise EditError("retry requires a replacement node mapping")
            replacement_id = node.get("id")
            if not isinstance(replacement_id, str) or not replacement_id:
                raise EditError("retry replacement requires a non-empty string id")
            if replacement_id in by_id:
                raise EditError("retry replacement id must be new")
            _validate_retry_pin(replacement_id, node)
            replacement = _pin_retry_branch(dict(node))
            replacement.setdefault("deps", list(by_id[node_id].get("deps", [])))
            direct_dependents = [task for task in tasks if node_id in task.get("deps", [])]
            reset: set[str] = {task["id"] for task in direct_dependents}
            pending = list(reset)
            while pending:
                predecessor = pending.pop()
                for task in tasks:
                    if predecessor in task.get("deps", []) and task["id"] not in reset:
                        reset.add(task["id"])
                        pending.append(task["id"])
            tasks.append(replacement)
            events.append(
                {
                    "kind": "retry-requested",
                    "node": node_id,
                    "detail": {"replacement": replacement_id, "reset": sorted(reset)},
                }
            )
            events.append(
                {
                    "kind": "node-added",
                    "detail": {"definition": _definition(replacement), "retry_of": node_id},
                }
            )
            for dependency in replacement.get("deps", []):
                events.append(
                    {
                        "kind": "edge-added",
                        "detail": {"from": dependency, "to": replacement_id},
                    }
                )
            for dependent in direct_dependents:
                dependent["deps"] = [
                    replacement_id if dependency == node_id else dependency
                    for dependency in dependent.get("deps", [])
                ]
                events.extend(
                    [
                        {
                            "kind": "edge-removed",
                            "detail": {"from": node_id, "to": dependent["id"]},
                        },
                        {
                            "kind": "edge-added",
                            "detail": {"from": replacement_id, "to": dependent["id"]},
                        },
                    ]
                )
        case "cancel":
            node_id = item.get("id")
            if not isinstance(node_id, str) or node_id not in by_id:
                raise EditError("cancel requires an existing node id")
            # The definition is the authoritative record of parking, not the frontier:
            # a node carried into a later round is parked with nothing journalled about
            # it there, so a frontier lookup alone would answer "pending" and let the
            # same node be parked twice.
            if by_id[node_id].get("parked"):
                raise EditError("cancel requires a node that is not already parked")
            if states.get(node_id) not in {None, "running"}:
                raise EditError("cancel requires a pending or running node")
            by_id[node_id]["parked"] = True
            events.append({"kind": "node-parked", "node": node_id, "detail": {}})
        case "requeue":
            node_id, amend = item.get("id"), item.get("amend", {})
            if not isinstance(node_id, str) or node_id not in by_id:
                raise EditError("requeue requires an existing node id")
            if not by_id[node_id].get("parked"):
                raise EditError("requeue requires a parked node")
            if not isinstance(amend, dict):
                raise EditError("requeue amendments must be a mapping")
            # `id` names the node being requeued and `deps` is `reparent`'s to change;
            # letting an amendment rewrite either would make one op silently do the
            # work of another, with no separate record of the rewiring.
            forbidden = [key for key in ("id", "deps") if key in amend]
            if forbidden:
                raise EditError(
                    f"requeue cannot amend {', '.join(map(repr, forbidden))}: "
                    "use 'add' or 'reparent' for that"
                )
            target = by_id[node_id]
            del target["parked"]
            target.update(amend)
            # `amend` is optional on the wire, so it is optional in the record: a bare
            # requeue that carried `"amend": {}` would say the planner amended the node
            # with nothing, and every reader of the journal — including one older than
            # this operation — would have to know that the empty mapping means the same
            # as its absence. Omitted when empty, verbatim when not.
            detail: dict[str, Any] = {"amend": dict(amend)} if amend else {}
            events.append({"kind": "node-requeued", "node": node_id, "detail": detail})
        case "attest":
            ref = item.get("ref")
            if not isinstance(ref, str) or states.get(ref) != "waiting":
                raise EditError("attest requires a currently-ready human action")
            if ref in attestations:
                raise EditError("human action was already attested")
            events.append({"kind": "human-attested", "node": ref, "detail": {"ref": ref}})
        case "context":
            node_id, note = item.get("id"), item.get("note")
            if not isinstance(node_id, str) or node_id not in by_id:
                raise EditError("context requires an existing node id")
            if not isinstance(note, str) or not note.strip():
                raise EditError("context requires a non-empty note")
            # A note is read by a *later* dispatch of the node — a retry this round,
            # or the carried-forward node after the transition. A node that already
            # settled `done` has neither, so the note could reach nobody and the
            # planner is told that rather than left believing it landed.
            if states.get(node_id) == "done":
                raise EditError("context requires a node that can still be dispatched")
            target = by_id[node_id]
            existing = target.get("context")
            if existing is not None and not isinstance(existing, list):
                raise EditError("context requires a node whose 'context' is a list")
            target["context"] = [*(existing or []), note]
            events.append({"kind": "context-added", "node": node_id, "detail": {"note": note}})
        case "complete":
            reason = item.get("reason")
            if not isinstance(reason, str):
                raise EditError("complete requires a string reason")
            events.append({"kind": "completion-requested", "detail": {"reason": reason}})
            return graph, events
    try:
        updated = parse_graph(mapping)
    except PlanError as exc:
        raise EditError(str(exc)) from exc
    return updated, events


def _validate_retry_pin(replacement_id: str, node: Mapping[str, Any]) -> None:
    """Refuse a retry whose branch pin and resume checkpoint name different branches.

    A retry that carries both is answering "which branch does this change live on?"
    twice, and the lifecycle can only honour one — it resumes the branch the
    checkpoint belongs to and ignores the pin. Two answers means the branch the
    planner gets is not the branch it named, so the envelope is refused at
    submission with the disagreement rather than resolved silently in favour of one.
    """
    branch = node.get("branch")
    resume = node.get("resume")
    if branch is None or not isinstance(resume, Mapping):
        return
    resumed = resume.get("branch")
    if isinstance(resumed, str) and isinstance(branch, str) and resumed != branch:
        raise EditError(
            f"retry replacement {replacement_id!r} pins branch {branch!r} but resumes "
            f"branch {resumed!r}; a retry may name only one branch"
        )


def _pin_retry_branch(node: dict[str, Any]) -> dict[str, Any]:
    """Make a planner-named resume the branch pin it already is.

    A retry that states ``resume`` is the planner answering "continue *this* work",
    and the lifecycle reads a branch pin as the promise it must not silently break:
    a preserved branch it cannot adopt fails the dispatch by name instead of being
    swapped for a fresh one. Recording the pin the resume already implies is what
    puts a planner-authored continuation on that side of the line, while a
    continuation the harness carried forward on its own keeps the fallback.

    `_validate_retry_pin` has already refused a node whose two answers disagree, so
    an existing ``branch`` is either the same branch or a deliberate fresh start.
    """
    resume = node.get("resume")
    if node.get("branch") is None and isinstance(resume, Mapping):
        branch = resume.get("branch")
        if isinstance(branch, str) and branch:
            node["branch"] = branch
    return node


def _definition(node: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in node.items() if key != "deps" or value == []}
