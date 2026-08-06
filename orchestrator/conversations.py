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

# llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] These payload types
# are gated by scripts/check-dag-state-contract.py, which reconciles their field names,
# optionality, and closed value vocabularies against the authoritative declarations.
# Structural field *types* stay ungated on purpose — see that script's own note.

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple, NotRequired, TypedDict

from .history import (
    HistoryError,
    HistorySession,
    agent_role,
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

#: snake_case history timing key -> the ``ConversationTurn`` field it becomes, split
#: by what the value is. A turn used to carry only ``timestamp``, which is when the
#: record was *written*, so a reader had no width for a turn at all and these
#: measurements sat unread in ``turn.unknown``. They are consumed *per record* rather
#: than listed above, because a value this mapper cannot serve as timing has to stay
#: where every other unconsumed key does.
_TIMESTAMP_KEYS = {"started_at": "startedAt", "finished_at": "finishedAt"}
_DURATION_KEYS = {"duration_ms": "durationMs", "model_ms": "modelMs", "tool_ms": "toolMs"}

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
    """``@oneharness/ui`` ``ConversationTurn``.

    The five timing fields are optional in the pinned declaration and are emitted
    exactly when the record carries them — a present ``null`` is preserved, since
    that is what a harness reporting no measured wall interval records.
    """

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
    startedAt: NotRequired[str | None]
    finishedAt: NotRequired[str | None]
    durationMs: NotRequired[float | None]
    modelMs: NotRequired[float | None]
    toolMs: NotRequired[float | None]


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
    parentConversationId: NotRequired[str]
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
        match record.get(key):
            case str() as text if text:
                return text
            case dict() | list() as structured if structured:
                return json.dumps(structured, indent=2, sort_keys=True)
            case _:
                continue
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


def _unknown(record: Mapping[str, Any], consumed: frozenset[str]) -> dict[str, Any]:
    return {
        key: value for key, value in record.items() if isinstance(key, str) and key not in consumed
    }


def _timing(record: Mapping[str, Any], turn: ConversationTurn) -> frozenset[str]:
    """Copy the record's own measured timing onto the turn; report what it consumed.

    A malformed value is never *served* as timing: this is a trust boundary, and a
    turn whose width came from a string would be a rendered lie about the run. It is
    not dropped either — an unconsumed key stays in ``turn.unknown`` with everything
    else this mapper did not understand, which is the promise that keeps a recorded
    field visible rather than silently gone.
    """
    consumed: set[str] = set()
    for key, field in _TIMESTAMP_KEYS.items():
        if key not in record:
            continue
        raw = record[key]
        # Served in this API's own RFC 3339 UTC spelling, parsed rather than copied:
        # a recorded value that is not a timestamp would reach a client as one and
        # fail its whole payload, so it stays unconsumed instead.
        stamp = parse_stamp(raw)
        if raw is None or stamp is not None:
            # `field` is reconciled against ConversationTurn by check-dag-state-contract,
            # which mypy cannot see through a dict lookup.
            turn[field] = stamp.isoformat() if stamp is not None else None  # type: ignore[literal-required]
            consumed.add(key)
    for key, field in _DURATION_KEYS.items():
        if key not in record:
            continue
        raw = record[key]
        if raw is None or (
            isinstance(raw, (int, float))
            and not isinstance(raw, bool)
            and math.isfinite(raw)
            and raw >= 0
        ):
            turn[field] = raw  # type: ignore[literal-required]
            consumed.add(key)
    return frozenset(consumed)


def _turn(session_key: str, index: int, record: Mapping[str, Any]) -> ConversationTurn:
    status = record.get("status")
    status_text = status if isinstance(status, str) else "unknown"
    turn: ConversationTurn = {
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
        "unknown": {},
    }
    turn["unknown"] = _unknown(record, _CONSUMED_RECORD_KEYS | _timing(record, turn))
    return turn


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
    """Fold a session's normalized records into one ``@oneharness/ui`` Conversation.

    The id is the session's own ``session_id``, never a record's ``session`` field:
    that field is per-record data two distinct sessions can share, and a colliding id
    would make ``run_conversation`` return whichever one it scanned first.
    """
    first = records[0] if records else {}
    last = records[-1] if records else {}
    session_key = str(session.session_id)
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


def attribution(session: HistorySession, records: list[dict[str, Any]]) -> Attribution:
    """Graph locators and semantic role for one conversation.

    Each locator is assigned by its literal key rather than through a label->field
    table so ``Attribution`` type-checks; the pairing is the whole mapping contract
    and is short enough to read directly.
    """
    transport_role = session_role(session, records)
    role, inferred = agent_role(session, transport_role)
    launcher = _label(session, "launcher")
    result: Attribution = {
        "transportRole": transport_role,
        "agentRole": role,
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


def _native_parents(groups: Sequence[Sequence[Mapping[str, Any]]]) -> dict[str, str]:
    """Child session id -> the agent session of the same onejudge dispatch.

    One onejudge dispatch is two oneharness sessions, and onejudge itself records
    which: each group is one report's own ``telemetry.sessions`` linkage, already
    validated by the collector that read it. The agent side is the dispatch — the
    supervisor exists to review it — so every other session in the group hangs off it.
    """
    parents: dict[str, str] = {}
    for group in groups:
        ordered = sorted(group, key=lambda link: _turn_index(link.get("turn_index")))
        parent = next(
            (
                str(link["session_id"])
                for link in ordered
                if link.get("role") == "agent" and link.get("session_id")
            ),
            None,
        )
        if parent is None:
            continue
        for link in ordered:
            session_id = link.get("session_id")
            if isinstance(session_id, str) and session_id and session_id != parent:
                parents[session_id] = parent
    return parents


def _turn_index(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


class _Locator(NamedTuple):
    """Where in the graph one conversation says it ran, as its labels name it."""

    run_id: str | None
    node_id: str | None
    step_id: str | None


class _Candidate(NamedTuple):
    """One dispatch a supervised session could have run under, and when it began."""

    started: datetime
    conversation: DagConversation


def _locator(attribution_value: Attribution) -> _Locator:
    return _Locator(
        attribution_value.get("runId"),
        attribution_value.get("nodeId"),
        attribution_value.get("stepId"),
    )


def parse_stamp(value: object) -> datetime | None:
    """One recorded timestamp as an aware UTC datetime, or ``None`` when unusable.

    History writes its own timestamps in its own spellings, so every reader that
    orders or compares them has to agree on one: a naive stamp is read as UTC and an
    offset one is converted, which is also what the read API promises to serve.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return (parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def dispatch_start(conversation_value: DagConversation) -> datetime | None:
    """When a dispatched session actually began working.

    A transcript's ``startedAt`` is its first record's ``timestamp``, and a history
    record is written when its turn *finishes* — so reading it as a start places a
    whole session at the moment its first turn ended. The first turn's own
    ``startedAt`` is the answer where a harness measured one; otherwise the turn is
    placed by subtracting its measured duration from when it was recorded, which is
    the only wall interval a claude-code session reports at all. With neither, the
    recorded start stands, since a session has to be placed somewhere.
    """
    transcript = conversation_value["conversation"]
    recorded = parse_stamp(transcript["startedAt"])
    turns = transcript["turns"]
    if not turns:
        return recorded
    first = turns[0]
    if (started := parse_stamp(first.get("startedAt"))) is not None:
        return started
    duration = first.get("durationMs")
    finished = parse_stamp(first["timestamp"]) or recorded
    if (
        finished is None
        or not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or not math.isfinite(duration)
        or duration < 0
    ):
        return recorded
    return finished - timedelta(milliseconds=duration)


def _ends_at(conversation_value: DagConversation) -> datetime | None:
    """When a transcript stopped, or ``None`` while it is still speaking."""
    finished = parse_stamp(conversation_value["attribution"].get("finishedAt"))
    if finished is not None:
        return finished
    stamps = [
        stamp
        for turn in conversation_value["conversation"]["turns"]
        if (stamp := parse_stamp(turn["timestamp"])) is not None
    ]
    return max(stamps) if stamps else None


def _labelled_parent(child: DagConversation, candidates: Sequence[DagConversation]) -> str | None:
    """The dispatch a supervised session ran under, by labels and recorded order.

    The fallback for history recorded before onejudge linked its own sessions. It is
    exact about labels and never guesses across them: the child's run and node must
    match, and its step too when it carries one. Among those, the containing dispatch
    wins, and a child that falls in none of them takes the most recent earlier one —
    history and the journal are written by different processes and disagree by
    milliseconds, which is not a reason to orphan a transcript.
    """
    wanted = _locator(child["attribution"])
    started = dispatch_start(child)
    if wanted.run_id is None or wanted.node_id is None or started is None:
        return None
    matches = [
        _Candidate(start, candidate)
        for candidate in candidates
        if candidate["attribution"]["transportRole"] == "agent"
        and (found := _locator(candidate["attribution"])).run_id == wanted.run_id
        and found.node_id == wanted.node_id
        and (wanted.step_id is None or found.step_id == wanted.step_id)
        and (start := dispatch_start(candidate)) is not None
        and start <= started
    ]
    containing = [
        candidate
        for candidate in matches
        if (end := _ends_at(candidate.conversation)) is None or started <= end
    ]
    pool = containing or matches
    if not pool:
        return None
    return max(pool, key=lambda found: found.started).conversation["conversation"]["id"]


def link_parents(
    conversations: list[DagConversation],
    native_groups: Sequence[Sequence[Mapping[str, Any]]] = (),
) -> None:
    """Name each supervised session's own dispatch, in place.

    A judge or lint session is not a sibling of the worker it supervised: it is the
    other half of one onejudge dispatch. Serving that parent is what lets a reader
    group "one onejudge session" from its two oneharness ones without re-deriving the
    pairing from names and timestamps.
    """
    native = _native_parents(native_groups)
    by_id = {item["conversation"]["id"]: item for item in conversations}
    for child in conversations:
        if child["attribution"]["transportRole"] == "agent":
            continue
        parent = native.get(child["conversation"]["id"])
        if parent is None or parent not in by_id:
            parent = _labelled_parent(child, conversations)
        if parent is not None and parent != child["conversation"]["id"]:
            child["attribution"]["parentConversationId"] = parent


def run_conversations(
    run_id: RunId,
    *,
    oneharness_bin: str = "oneharness",
    native_groups: Sequence[Sequence[Mapping[str, Any]]] = (),
) -> list[DagConversation]:
    """Every labelled conversation that acted on ``run_id``, oldest session first.

    A run is joined by the ``run_id`` history label the dispatch stamped, plus the
    orchestrator session named ``orchestrator-<run_id>`` (whose own dispatch predates
    graph labels). A missing history store degrades to an empty list rather than
    failing the read: the projection and telemetry views do not depend on it.

    ``native_groups`` is onejudge's own per-dispatch session linkage, read from the
    run's recorded results by the caller that already holds them. It is the authority
    for `attribution.parentConversationId`; a caller with none gets the labelled
    fallback, which is all history recorded before that linkage existed can offer.
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
    link_parents(conversations, native_groups)
    return conversations
