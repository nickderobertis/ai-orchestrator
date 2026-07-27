"""Map labelled oneharness history sessions to the ``@oneharness/ui`` shape.

The DAG UI renders every agent and subagent that acted on a node as one
``Conversation`` (worker, judge, check-in, pr-author, orchestrator, …). oneharness
records each of those as a labelled history session; this module folds one such
session's normalized per-turn records into the exact ``Conversation`` object the UI
package exports, wrapped in a ``DagConversation`` envelope that carries the graph
attribution the transcript type deliberately has no field for.

Roles are read *generically*: the semantic role is whatever the session's
``agent_role`` label carries, so a role added by a future dispatch appears here with
no change. Only when that label is absent does this module infer a role from the
transport role, persona, and session name — and it marks that classification
``inferred`` so a consumer can tell an authoritative label from a guess.

Everything here is a trust boundary: history records are parsed defensively by
`history.session_records`, and every field is type-checked before it is copied into
the output.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any, NotRequired, TypedDict

from .history import (
    HistoryError,
    HistorySession,
    SessionRole,
    all_sessions,
    session_records,
    session_role,
)
from .launch import KNOWN_LAUNCHERS
from .runs import RunId

#: History record keys this mapper consumes into a public ``Conversation`` field.
#: Everything else on a record is preserved verbatim under ``turn.unknown`` so no
#: recorded content is silently dropped.
_CONSUMED_RECORD_KEYS = frozenset(
    {
        "session",
        "name",
        "project",
        "timestamp",
        "harness",
        "model",
        "prompt",
        "text",
        "reasoning",
        "thinking",
        "status",
        "failure_kind",
        "usage",
        "events",
        "session_id",
    }
)

#: snake_case history usage key -> camelCase ``ConversationUsage`` key.
_USAGE_KEYS = {
    "input_tokens": "inputTokens",
    "output_tokens": "outputTokens",
    "cache_read_tokens": "cacheReadTokens",
    "cache_write_tokens": "cacheWriteTokens",
    "cost_usd": "costUsd",
}

#: Statuses that mean the invocation cannot be resumed as a live turn.
_NOT_CONTINUABLE = frozenset({"planned", "skipped", "spawn-error"})


class ConversationUsage(TypedDict, total=False):
    """``@oneharness/ui`` ``ConversationUsage`` — every counter optional and nullable."""

    inputTokens: float | None
    outputTokens: float | None
    cacheReadTokens: float | None
    cacheWriteTokens: float | None
    costUsd: float | None


class ConversationToolEvent(TypedDict):
    """``@oneharness/ui`` ``ConversationToolEvent``."""

    index: int
    kind: str
    input: NotRequired[Any]
    name: NotRequired[str | None]
    output: NotRequired[str | None]


class ConversationTurn(TypedDict):
    """``@oneharness/ui`` ``ConversationTurn``."""

    id: str
    user: str
    assistant: str | None
    reasoning: str | None
    harness: str
    model: str | None
    timestamp: str
    status: str
    failureKind: str | None
    usage: ConversationUsage
    tools: list[ConversationToolEvent]
    unknown: dict[str, Any]


class Conversation(TypedDict):
    """``@oneharness/ui`` ``Conversation`` — the transcript the UI package renders."""

    id: str
    name: str
    project: str
    startedAt: str
    harnesses: list[str]
    state: str
    canContinue: bool
    turns: list[ConversationTurn]


class Attribution(TypedDict):
    """Graph locators and roles for one conversation, per ``docs/dag-ui/design.md``.

    The transcript type deliberately has no graph fields, so this envelope carries
    them. Locator keys are omitted rather than nulled when their label is absent.
    """

    transportRole: str
    agentRole: str
    launcher: str
    runId: NotRequired[str]
    nodeId: NotRequired[str]
    stepId: NotRequired[str]
    launchId: NotRequired[str]
    persona: NotRequired[str]
    round: NotRequired[int]
    finishedAt: NotRequired[str | None]
    inferred: NotRequired[bool]


class DagConversation(TypedDict):
    """One transcript plus its graph attribution."""

    conversation: Conversation
    attribution: Attribution


def _status_state(status: str) -> str:
    """Fold a record status onto the UI transcript's coarse state vocabulary."""
    match status:
        case "ok":
            return "completed"
        case "nonzero" | "spawn-error":
            return "failed"
        case "timeout" | "skipped" | "planned":
            return "stopped"
        case _:
            return status


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _first_str(record: Mapping[str, Any], key: str, fallback: str) -> str:
    value = record.get(key)
    return value if isinstance(value, str) and value else fallback


def _usage(value: object) -> ConversationUsage:
    """Rename known usage counters, preserving ``null`` and omitting absent ones."""
    usage: ConversationUsage = {}
    if not isinstance(value, dict):
        return usage
    for snake, camel in _USAGE_KEYS.items():
        if snake not in value:
            continue
        raw = value[snake]
        if raw is None or (
            isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(raw)
        ):
            # `camel` is reconciled against ConversationUsage's fields by
            # check-dag-state-contract, which mypy cannot see through a dict lookup.
            usage[camel] = raw  # type: ignore[literal-required]
    return usage


def _reasoning(record: Mapping[str, Any]) -> str | None:
    """The first non-empty reasoning/thinking, JSON-encoding a structured value."""
    for key in ("reasoning", "thinking"):
        raw = record.get(key)
        if isinstance(raw, str) and raw:
            return raw
        if isinstance(raw, (dict, list)) and raw:
            return json.dumps(raw, indent=2, sort_keys=True)
    return None


def _tools(record: Mapping[str, Any]) -> list[ConversationToolEvent]:
    """Every recorded event as a generic, always-visible tool event."""
    events = record.get("events")
    if not isinstance(events, list):
        return []
    tools: list[ConversationToolEvent] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        kind = event.get("kind")
        tool: ConversationToolEvent = {
            "index": index,
            "kind": kind if isinstance(kind, str) else "unknown",
        }
        if "input" in event:
            tool["input"] = event["input"]
        if "name" in event:
            tool["name"] = _str_or_none(event["name"])
        if "output" in event:
            tool["output"] = _str_or_none(event["output"])
        tools.append(tool)
    return tools


def _unknown(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.items()
        if isinstance(key, str) and key not in _CONSUMED_RECORD_KEYS
    }


def _turn(session_key: str, index: int, record: Mapping[str, Any]) -> ConversationTurn:
    status = record.get("status")
    status_text = status if isinstance(status, str) else "unknown"
    return {
        "id": f"{session_key}-{index}",
        "user": _first_str(record, "prompt", ""),
        "assistant": _str_or_none(record.get("text")),
        "reasoning": _reasoning(record),
        "harness": _first_str(record, "harness", ""),
        "model": _str_or_none(record.get("model")),
        "timestamp": _first_str(record, "timestamp", ""),
        "status": _status_state(status_text),
        "failureKind": _str_or_none(record.get("failure_kind")),
        "usage": _usage(record.get("usage")),
        "tools": _tools(record),
        "unknown": _unknown(record),
    }


def _harnesses(records: list[dict[str, Any]]) -> list[str]:
    ordered: list[str] = []
    for record in records:
        harness = record.get("harness")
        if isinstance(harness, str) and harness and harness not in ordered:
            ordered.append(harness)
    return ordered


def _can_continue(records: list[dict[str, Any]]) -> bool:
    last = records[-1] if records else {}
    return bool(last.get("session_id")) and _str_or_none(last.get("status")) not in _NOT_CONTINUABLE


def conversation(session: HistorySession, records: list[dict[str, Any]]) -> Conversation:
    """Fold a session's normalized records into one ``@oneharness/ui`` Conversation."""
    first = records[0] if records else {}
    last = records[-1] if records else {}
    session_key = _first_str(first, "session", str(session.session_id))
    last_status = last.get("status")
    return {
        "id": session_key,
        "name": _first_str(first, "name", session.name),
        "project": _first_str(first, "project", str(session.project)),
        "startedAt": _first_str(first, "timestamp", session.started),
        "harnesses": _harnesses(records),
        "state": _status_state(last_status if isinstance(last_status, str) else "unknown"),
        "canContinue": _can_continue(records),
        "turns": [_turn(session_key, index, record) for index, record in enumerate(records)],
    }


def _label(session: HistorySession, key: str) -> str | None:
    value = session.labels.get(key)
    return value if isinstance(value, str) and value else None


def _agent_role(session: HistorySession, transport_role: SessionRole) -> tuple[str, bool]:
    """The semantic role, and whether it was inferred rather than labelled.

    A present ``agent_role`` label is authoritative and passed through verbatim so a
    role introduced by a future dispatch needs no change here. The inference path is
    the compatibility fallback for history written before that label existed.
    """
    labelled = _label(session, "agent_role")
    if labelled is not None:
        return labelled, False
    persona = _label(session, "persona") or ""
    name = session.name
    if transport_role == "judge":
        return "judge", True
    if persona == "pr-author" or "pr-author" in name:
        return "pr-author", True
    if name.startswith("orchestrator-") or persona == "orchestrator":
        return "orchestrator", True
    if persona == "check-in" or "check-in" in name:
        return "check-in", True
    # A nested llmlint session is verification activity grouped under the worker.
    return "worker", True


def attribution(session: HistorySession, records: list[dict[str, Any]]) -> Attribution:
    """Graph locators and semantic role for one conversation.

    Each locator is assigned by its literal key rather than through a label->field
    table so ``Attribution`` type-checks; the pairing is the whole mapping contract
    and is short enough to read directly.
    """
    transport_role = session_role(session, records)
    agent_role, inferred = _agent_role(session, transport_role)
    launcher = _label(session, "launcher")
    result: Attribution = {
        "transportRole": transport_role,
        "agentRole": agent_role,
        "launcher": launcher if launcher in KNOWN_LAUNCHERS else "unknown",
    }
    if (value := _label(session, "run_id")) is not None:
        result["runId"] = value
    if (value := _label(session, "node")) is not None:
        result["nodeId"] = value
    if (value := _label(session, "step")) is not None:
        result["stepId"] = value
    if (value := _label(session, "launch_id")) is not None:
        result["launchId"] = value
    if (value := _label(session, "persona")) is not None:
        result["persona"] = value
    if (round_label := _label(session, "round")) is not None and round_label.isdigit():
        result["round"] = int(round_label)
    if records and "finished_at" in records[-1]:
        finished = records[-1]["finished_at"]
        result["finishedAt"] = finished if isinstance(finished, str) else None
    if inferred:
        result["inferred"] = True
    return result


def dag_conversation(session: HistorySession, records: list[dict[str, Any]]) -> DagConversation:
    """One ``DagConversation``: the transcript plus its graph attribution."""
    return {
        "conversation": conversation(session, records),
        "attribution": attribution(session, records),
    }


def run_conversations(
    run_id: RunId, *, oneharness_bin: str = "oneharness"
) -> list[DagConversation]:
    """Every labelled conversation that acted on ``run_id``, oldest session first.

    A run is joined by the ``run_id`` history label the dispatch stamped, plus the
    orchestrator session named ``orchestrator-<run_id>`` (whose own dispatch predates
    graph labels). A missing history store degrades to an empty list rather than
    failing the read: the projection and telemetry views do not depend on it.
    """
    try:
        sessions = all_sessions(oneharness_bin=oneharness_bin)
    except HistoryError as exc:
        if str(exc).startswith("oneharness not found"):
            return []
        raise
    selected = [
        session
        for session in sessions
        if session.labels.get("run_id") == run_id or session.name == f"orchestrator-{run_id}"
    ]
    conversations: list[DagConversation] = []
    for session in sorted(selected, key=lambda item: (item.started, str(item.session_id))):
        try:
            records = session_records(session)
        except HistoryError:
            continue
        conversations.append(dag_conversation(session, records))
    return conversations
