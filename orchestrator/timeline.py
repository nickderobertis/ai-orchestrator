"""Fold one run's recorded sources into a single ordered execution timeline.

`read_model` answers "what state is this graph in": it folds the journal through
`project_round` and keeps the *result*, discarding the stream that produced it. This
module answers the other question a reader has — "what happened, in what order, and
how long did each part take" — from the same three recorded sources:

* the **run journal** (`runs/<run-id>/events.jsonl`), read strictly so a corrupt
  authoritative stream fails the read rather than rendering a plausible history;
* the run's **conversations**, one span per dispatched session and one event per turn;
* the persisted **detail snapshot** (`orchestrator.monitor.DetailSnapshot`), which
  carries no timestamps of its own and therefore contributes no ordered item — it
  supplies the observed PR state a still-open publication has not journaled yet.

Three properties make the result usable rather than merely complete:

* **Spans, with a nullable end.** A live run is the normal case, so every span
  models work that has started and may not have finished; ``ended_at`` is ``None``
  exactly when the recorded stream never closed it.
* **Rollups.** A real run records one `lock-wait` per lock acquisition — thousands
  of them against a hundred of everything else. Those kinds collapse to one span per
  node carrying a count and the total seconds they recorded, so the payload stays
  bounded by the graph rather than by contention.
* **References, never bodies.** A transcript, a gate log, and a worker report are
  each larger than this whole payload. Every item points at its heavy content by
  conversation id, recorded artifact path, or PR url, so a consumer fetches only
  what it opens.

The timeline is derived, never authoritative: nothing reads it to make a decision.
"""

# llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] These payload types
# are gated by scripts/check-dag-state-contract.py, which reconciles their field names,
# optionality, and closed value vocabularies against docs/dag-ui/design.md. Structural
# field *types* stay ungated on purpose — see that script's own note.

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

from .config import ConfigError
from .conversations import DagConversation, run_conversations
from .history import HistoryError
from .journal import JOURNAL_NAME, Detail, Event, EventKind
from .monitor import DetailSnapshot, load_snapshot, summarize
from .projection import ProjectionError, read_strict_events
from .read_model import (
    API_VERSION,
    InvalidRunId,
    ProjectionFailed,
    RunNotFound,
    contained_run_dir,
)
from .runs import RunId, validate_run_id

#: What a timeline item points at instead of inlining it. The three artifact kinds
#: are exactly the keys a node or step result records under ``artifacts``, so a
#: consumer resolves one the same way the result view does.
TimelineReferenceKind = Literal[
    "conversation",
    "gate_log",
    "worker_report",
    "oneharness_session",
    "pr",
]

#: The closed span vocabulary. ``rollup`` is the aggregate described above; every
#: other member brackets one recorded activity.
TimelineSpanKind = Literal[
    "round",
    "node",
    "step",
    "dispatch",
    "verification",
    "publication",
    "pr-drafting",
    "conflict-resolution",
    "human-wait",
    "rollup",
]

#: An event's ``kind`` is the journal kind that produced it, except for this one:
#: a conversation turn has no journal record of its own.
CONVERSATION_TURN_KIND = "conversation-turn"

#: Journal kinds recorded once per *lock acquisition* rather than once per graph
#: transition. A run that contends holds thousands of them, so they are aggregated
#: instead of listed. A kind belongs here only when it records its own elapsed
#: ``seconds``, which is what makes the rolled-up total meaningful.
ROLLUP_EVENT_KINDS: frozenset[EventKind] = frozenset({"lock-wait"})

#: Kinds consumed entirely by a span boundary. They are not emitted again as events,
#: because the span they open or close already carries their time, status, and
#: reference. Publication is deliberately absent: its `pr-created` /
#: `publication-finished` records both bound the span *and* remain events inside it,
#: since a reader wants "PR created, then checks, then merged" in recorded order.
_SPAN_BOUNDARY_KINDS: frozenset[EventKind] = frozenset(
    {
        "round-started",
        "round-finished",
        "node-started",
        "node-settled",
        "node-failed",
        "step-started",
        "step-settled",
        "verification-started",
        "verification-finished",
        "pr-drafting-started",
        "pr-drafting-finished",
        "conflict-resolution-started",
        "conflict-resolution-finished",
        "human-waiting",
        "human-attested",
    }
)

#: Kinds that belong to a publication span. The journal has no `publication-started`:
#: a publication begins when its first observable act is recorded, which is the PR
#: for a remote workflow and the finishing record itself for a local direct merge.
_PUBLICATION_KINDS: frozenset[EventKind] = frozenset(
    {
        "pr-created",
        "pr-ready",
        "pr-checks-observed",
        "pr-merged",
        "publication-finished",
        "publication-failed",
    }
)
_PUBLICATION_CLOSING_KINDS: frozenset[EventKind] = frozenset(
    {"publication-finished", "publication-failed"}
)

#: Artifact keys a node or step result records, in the order a reader wants them:
#: the worker's own report first, then the merge-path gate log, then the raw session.
_ARTIFACT_KINDS: tuple[TimelineReferenceKind, ...] = (
    "worker_report",
    "gate_log",
    "oneharness_session",
)

#: Conversation states that mean the session is over. ``conversations`` folds every
#: recorded status onto this vocabulary, so anything else is still live.
_TERMINAL_CONVERSATION_STATES = frozenset({"completed", "failed", "stopped"})

#: The transport role of a nested lint run, which nests under the dispatch it ran in.
_LLMLINT_ROLE = "llmlint"


class TimelineReference(TypedDict):
    """Where one item's heavy content lives, so the payload can omit it."""

    kind: TimelineReferenceKind
    value: str


class TimelineEvent(TypedDict):
    """One instant recorded inside a span."""

    id: str
    kind: str
    at: str
    node_id: NotRequired[str]
    step_id: NotRequired[str]
    round: NotRequired[int]
    status: NotRequired[str]
    reference: NotRequired[TimelineReference]


class TimelineSpan(TypedDict):
    """One interval of recorded work, and the events observed inside it.

    ``ended_at`` is ``None`` for work the stream never closed — which is what an
    in-flight run looks like, not an error. ``parent_id`` links spans into the tree
    the recorded nesting implies; a span with no parent is run-level.

    ``count`` and ``total_duration_ms`` appear only on a ``rollup`` span, and the
    role pair only on a ``dispatch`` one.
    """

    id: str
    kind: TimelineSpanKind
    label: str
    started_at: str
    ended_at: str | None
    events: list[TimelineEvent]
    parent_id: NotRequired[str]
    node_id: NotRequired[str]
    step_id: NotRequired[str]
    round: NotRequired[int]
    status: NotRequired[str]
    count: NotRequired[int]
    total_duration_ms: NotRequired[int]
    agent_role: NotRequired[str]
    transport_role: NotRequired[str]
    reference: NotRequired[TimelineReference]


class RunTimeline(TypedDict):
    """The ``RunTimeline`` served by ``GET /api/v1/runs/{run_id}/timeline``."""

    api_version: int
    observed_at: str
    run_id: str
    spans: list[TimelineSpan]


def _stamp(at: float) -> str | None:
    """One epoch-seconds journal timestamp as RFC 3339 UTC, or ``None`` if unusable.

    The journal already rejects a non-finite or negative ``at``, but a value far
    outside the representable range is still storable and would raise here. An item
    that cannot be placed in time is dropped rather than given an invented one.
    """
    try:
        return datetime.fromtimestamp(at, UTC).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _normalize(value: object) -> str | None:
    """One recorded timestamp string as RFC 3339 UTC, or ``None`` when unparseable.

    History writes its own timestamps and this API promises UTC with an offset, so a
    naive or non-UTC stamp is converted rather than passed through.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _reference(kind: TimelineReferenceKind, value: object) -> TimelineReference | None:
    return {"kind": kind, "value": value} if isinstance(value, str) and value else None


def _artifact_reference(detail: Detail) -> TimelineReference | None:
    """The recorded artifact a settled node or step points at, if it recorded one."""
    result = detail.get("result")
    artifacts = result.get("artifacts") if isinstance(result, Mapping) else None
    if not isinstance(artifacts, Mapping):
        return None
    for kind in _ARTIFACT_KINDS:
        if (reference := _reference(kind, artifacts.get(kind))) is not None:
            return reference
    return None


def _event_reference(detail: Detail) -> TimelineReference | None:
    """The heavy content one journal record points at, in resolution order."""
    if (reference := _reference("pr", detail.get("pr"))) is not None:
        return reference
    if (reference := _reference("gate_log", detail.get("log_path"))) is not None:
        return reference
    return _artifact_reference(detail)


def _status(detail: Detail) -> str | None:
    """The recorded verdict of one journal record, as a typed value not a rendering."""
    for key in ("status", "outcome", "state"):
        value = detail.get(key)
        if isinstance(value, str) and value:
            return value
    ok = detail.get("ok")
    if isinstance(ok, bool):
        return "ok" if ok else "failed"
    completed = detail.get("completed")
    if isinstance(completed, bool):
        return "completed" if completed else "not-completed"
    return None


def _label(detail: Detail, *keys: str, fallback: str) -> str:
    """A capped, control-stripped display label from the first recorded key present."""
    for key in keys:
        value = detail.get(key)
        if isinstance(value, str) and value.strip():
            return summarize(value)
    return fallback


def _seconds(detail: Detail) -> float:
    """One record's own elapsed seconds, matching how telemetry totals them.

    Finiteness is rechecked rather than assumed. `Event` rejects a non-finite detail
    value, but this reads a *rolled-up* total that then crosses into `round()`, where
    an infinity raises — a record that slipped through must degrade the number it
    contributes, never fail the whole read.
    """
    value = detail.get("seconds")
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    ):
        return float(value)
    return 0.0


class _Assembly:
    """Mutable span table for one fold; every span is created and closed through it.

    Spans are keyed by what makes them unique *while open* — a round number, a node
    within a round, a kind within a node and step — rather than by their id, so a
    second `verification-started` for the same node opens a second span instead of
    reopening the first.
    """

    def __init__(self) -> None:
        self._spans: dict[str, TimelineSpan] = {}
        self._open: dict[tuple[str, ...], str] = {}

    def open(
        self,
        key: tuple[str, ...],
        span_id: str,
        *,
        kind: TimelineSpanKind,
        label: str,
        started_at: str,
        ended_at: str | None = None,
        parent_id: str | None = None,
        node_id: str | None = None,
        step_id: str | None = None,
        round_number: int | None = None,
        status: str | None = None,
        agent_role: str | None = None,
        transport_role: str | None = None,
        reference: TimelineReference | None = None,
    ) -> str:
        span: TimelineSpan = {
            "id": span_id,
            "kind": kind,
            "label": label,
            "started_at": started_at,
            "ended_at": ended_at,
            "events": [],
        }
        if parent_id is not None:
            span["parent_id"] = parent_id
        if node_id is not None:
            span["node_id"] = node_id
        if step_id is not None:
            span["step_id"] = step_id
        if round_number is not None:
            span["round"] = round_number
        if status is not None:
            span["status"] = status
        if agent_role is not None:
            span["agent_role"] = agent_role
        if transport_role is not None:
            span["transport_role"] = transport_role
        if reference is not None:
            span["reference"] = reference
        self._spans[span_id] = span
        self._open[key] = span_id
        return span_id

    def find(self, key: tuple[str, ...]) -> str | None:
        return self._open.get(key)

    def get(self, span_id: str) -> TimelineSpan:
        return self._spans[span_id]

    def close(
        self,
        key: tuple[str, ...],
        *,
        ended_at: str,
        status: str | None = None,
        reference: TimelineReference | None = None,
    ) -> str | None:
        """Close the span open at ``key`` and return its id; ``None`` if none is open.

        Ignored rather than raised: a journal legitimately holds a finish whose start
        this build never saw — a resumed run, a round that began under an older
        schema — and a timeline of a partial stream is still a timeline.
        """
        span_id = self._open.pop(key, None)
        if span_id is None:
            return None
        span = self._spans[span_id]
        span["ended_at"] = ended_at
        if status is not None:
            span["status"] = status
        if reference is not None:
            span["reference"] = reference
        return span_id

    def add_event(self, span_id: str, event: TimelineEvent) -> None:
        self._spans[span_id]["events"].append(event)

    def spans(self) -> list[TimelineSpan]:
        """Every span, oldest first, with each span's events in recorded order."""
        for span in self._spans.values():
            span["events"].sort(key=lambda item: (item["at"], item["id"]))
        return sorted(self._spans.values(), key=lambda span: (span["started_at"], span["id"]))


def _round_key(round_number: int) -> tuple[str, ...]:
    return ("round", str(round_number))


def _node_key(round_number: int, node: str) -> tuple[str, ...]:
    return ("node", str(round_number), node)


def _step_key(round_number: int, node: str, step: str) -> tuple[str, ...]:
    return ("step", str(round_number), node, step)


def _scoped_key(event: Event, kind: TimelineSpanKind) -> tuple[str, ...]:
    """The key one node-scoped span is open at: its kind within its node and step.

    A human wait is keyed on its node alone. A lifecycle step journals
    `human-waiting` from its own step scope, but an attestation names the node the
    planner attested and never a step — so including the step would leave every
    step-scoped wait permanently open.
    """
    step = "" if kind == "human-wait" else str(event.step or "")
    return (kind, str(event.round), str(event.node or ""), step)


@dataclass
class _Rollup:
    """What one rollup span has absorbed so far, accumulated across the pass."""

    span_id: str
    count: int = 0
    seconds: float = 0.0

    def absorb(self, seconds: float) -> None:
        self.count += 1
        self.seconds += seconds


class _Fold:
    """The journal pass, kept as an object so the span table is threaded once."""

    def __init__(self, assembly: _Assembly) -> None:
        self.assembly = assembly
        #: The running aggregate behind each rollup span, keyed like the span itself.
        self._rollups: dict[tuple[str, ...], _Rollup] = {}
        #: Scoped spans still open at each locator, innermost last. A record made
        #: *during* one of them belongs inside it — `pr-drafting-fallback` is the
        #: reason drafting failed, not a sibling of the drafting it explains. Keyed by
        #: the locator part of `_scoped_key` so a span leaves exactly where it entered.
        self._nested: dict[tuple[str, ...], list[str]] = {}

    def _enter(self, key: tuple[str, ...], span_id: str) -> None:
        self._nested.setdefault(key[1:], []).append(span_id)

    def _leave(self, key: tuple[str, ...], span_id: str | None) -> None:
        open_spans = self._nested.get(key[1:], [])
        if span_id is not None and span_id in open_spans:
            open_spans.remove(span_id)

    def enclosing(self, event: Event, at: str) -> str:
        """The innermost open span an event belongs to, opening the round if needed.

        A scoped span still open at this record's own locator wins: it is nested
        inside the node or step the fallback below would resolve to, and its own
        parent already resolved through that fallback.

        Every journal record carries a round, so this never fails: the round span is
        opened by the first record of that round — which is `node-added` rather than
        `round-started` in a normal stream — and therefore always encloses it.
        """
        locator = (str(event.round), str(event.node or ""), str(event.step or ""))
        if nested := self._nested.get(locator):
            return nested[-1]
        round_id = self.assembly.find(_round_key(event.round))
        if round_id is None:
            round_id = self.assembly.open(
                _round_key(event.round),
                f"round-{event.round}",
                kind="round",
                label=f"round {event.round}",
                started_at=at,
                round_number=event.round,
            )
        if event.node is None:
            return round_id
        node = str(event.node)
        node_id = self.assembly.find(_node_key(event.round, node))
        if node_id is None:
            return round_id
        if event.step is None:
            return node_id
        step_id = self.assembly.find(_step_key(event.round, node, str(event.step)))
        return step_id if step_id is not None else node_id

    def boundary(self, event: Event, index: int, at: str) -> None:
        """Apply one span-opening or span-closing record."""
        detail = event.detail
        node = str(event.node) if event.node is not None else None
        step = str(event.step) if event.step is not None else None
        match event.kind:
            case "round-started":
                self.enclosing(event, at)
            case "round-finished":
                self.assembly.close(
                    _round_key(event.round), ended_at=at, status=_status(detail) or "finished"
                )
            case "node-started" if node is not None:
                self.assembly.open(
                    _node_key(event.round, node),
                    f"node-{event.round}-{node}",
                    kind="node",
                    label=node,
                    started_at=at,
                    parent_id=self.enclosing(event, at),
                    node_id=node,
                    round_number=event.round,
                )
            case "node-settled" | "node-failed" if node is not None:
                self.assembly.close(
                    _node_key(event.round, node),
                    ended_at=at,
                    status=("failed" if event.kind == "node-failed" else _status(detail))
                    or "settled",
                    reference=_artifact_reference(detail),
                )
            case "step-started" if node is not None and step is not None:
                self.assembly.open(
                    _step_key(event.round, node, step),
                    f"step-{event.round}-{node}-{step}",
                    kind="step",
                    label=step,
                    started_at=at,
                    parent_id=self.enclosing(event, at),
                    node_id=node,
                    step_id=step,
                    round_number=event.round,
                )
            case "step-settled" if node is not None and step is not None:
                self.assembly.close(
                    _step_key(event.round, node, step),
                    ended_at=at,
                    status=_status(detail) or "settled",
                    reference=_artifact_reference(detail),
                )
            case "verification-started":
                self._open_scoped(
                    event,
                    index,
                    at,
                    kind="verification",
                    label=_label(detail, "label", fallback="verification"),
                )
            case "verification-finished":
                self._close_scoped(
                    event, at, kind="verification", reference=_event_reference(detail)
                )
            case "pr-drafting-started":
                self._open_scoped(event, index, at, kind="pr-drafting", label="pr drafting")
            case "pr-drafting-finished":
                self._close_scoped(event, at, kind="pr-drafting")
            case "conflict-resolution-started":
                self._open_scoped(
                    event, index, at, kind="conflict-resolution", label="conflict resolution"
                )
            case "conflict-resolution-finished":
                self._close_scoped(event, at, kind="conflict-resolution")
            case "human-waiting":
                self._open_scoped(
                    event,
                    index,
                    at,
                    kind="human-wait",
                    label=_label(detail, "step_kind", fallback=step or node or "human action"),
                    status="waiting",
                )
            case "human-attested":
                self._close_scoped(event, at, kind="human-wait", status="attested")
            case _:  # pragma: no cover - _SPAN_BOUNDARY_KINDS admits nothing else
                return

    def _open_scoped(
        self,
        event: Event,
        index: int,
        at: str,
        *,
        kind: TimelineSpanKind,
        label: str,
        status: str | None = None,
    ) -> None:
        key = _scoped_key(event, kind)
        span_id = self.assembly.open(
            key,
            f"{kind}-{index}",
            kind=kind,
            label=label,
            started_at=at,
            parent_id=self.enclosing(event, at),
            status=status,
            node_id=str(event.node) if event.node is not None else None,
            step_id=str(event.step) if event.step is not None else None,
            round_number=event.round,
        )
        self._enter(key, span_id)

    def _close_scoped(
        self,
        event: Event,
        at: str,
        *,
        kind: TimelineSpanKind,
        status: str | None = None,
        reference: TimelineReference | None = None,
    ) -> None:
        key = _scoped_key(event, kind)
        self._leave(
            key,
            self.assembly.close(
                key,
                ended_at=at,
                status=status or _status(event.detail) or "finished",
                reference=reference,
            ),
        )

    def publication(self, event: Event, index: int, at: str) -> str:
        """The publication span this record belongs to, opening it on first sight."""
        key = ("publication", str(event.round), str(event.node or ""))
        span_id = self.assembly.find(key)
        if span_id is None:
            span_id = self.assembly.open(
                key,
                f"publication-{index}",
                kind="publication",
                label=_label(event.detail, "repo", "branch", fallback="publication"),
                started_at=at,
                parent_id=self.enclosing(event, at),
                node_id=str(event.node) if event.node is not None else None,
                round_number=event.round,
            )
        span = self.assembly.get(span_id)
        if (
            "reference" not in span
            and (reference := _reference("pr", event.detail.get("pr"))) is not None
        ):
            span["reference"] = reference
        if event.kind in _PUBLICATION_CLOSING_KINDS:
            self.assembly.close(
                key,
                ended_at=at,
                status="finished" if event.kind == "publication-finished" else "failed",
            )
        return span_id

    def rollup(self, event: Event, index: int, at: str) -> None:
        """Accumulate one high-frequency record into its node's single rollup span."""
        key = ("rollup", event.kind, str(event.round), str(event.node or ""))
        rollup = self._rollups.get(key)
        if rollup is None:
            rollup = _Rollup(
                self.assembly.open(
                    key,
                    f"rollup-{event.kind}-{index}",
                    kind="rollup",
                    label=event.kind,
                    started_at=at,
                    parent_id=self.enclosing(event, at),
                    node_id=str(event.node) if event.node is not None else None,
                    round_number=event.round,
                )
            )
            self._rollups[key] = rollup
        rollup.absorb(_seconds(event.detail))
        span = self.assembly.get(rollup.span_id)
        span["ended_at"] = at
        span["count"] = rollup.count
        span["total_duration_ms"] = round(rollup.seconds * 1000)

    def event(self, event: Event, index: int, at: str) -> None:
        """Record one ordinary journal record as an event inside its span."""
        span_id = (
            self.publication(event, index, at)
            if event.kind in _PUBLICATION_KINDS
            else self.enclosing(event, at)
        )
        item: TimelineEvent = {
            "id": f"event-{index}",
            "kind": event.kind,
            "at": at,
            "round": event.round,
        }
        if event.node is not None:
            item["node_id"] = str(event.node)
        if event.step is not None:
            item["step_id"] = str(event.step)
        if (status := _status(event.detail)) is not None:
            item["status"] = status
        if (reference := _event_reference(event.detail)) is not None:
            item["reference"] = reference
        self.assembly.add_event(span_id, item)


def _fold_journal(assembly: _Assembly, events: Sequence[Event]) -> None:
    """Apply the journal in recorded order: boundaries open/close, the rest nest."""
    fold = _Fold(assembly)
    for index, event in enumerate(events):
        at = _stamp(event.at)
        if at is None:
            continue
        if event.kind in ROLLUP_EVENT_KINDS:
            fold.rollup(event, index, at)
        elif event.kind in _SPAN_BOUNDARY_KINDS:
            fold.boundary(event, index, at)
        else:
            fold.event(event, index, at)


def _conversation_end(conversation: DagConversation, started_at: str) -> str | None:
    """When a dispatched session stopped, or ``None`` while it is still speaking."""
    finished = _normalize(conversation["attribution"].get("finishedAt"))
    if finished is not None:
        return finished
    transcript = conversation["conversation"]
    if transcript["state"] not in _TERMINAL_CONVERSATION_STATES:
        return None
    stamps = [
        stamp
        for turn in transcript["turns"]
        if (stamp := _normalize(turn["timestamp"])) is not None
    ]
    return max(stamps) if stamps else started_at


class _DispatchIndex:
    """Where a dispatch span attaches, resolved from what the graph actually recorded.

    A conversation carries labels, not span ids, and its labels are a *subset* of the
    graph's locators: a check-in session names a round and no node, and an
    orchestrator session names neither. Resolution therefore degrades outward — step,
    then node, then round, then run level — rather than dropping a transcript that
    cannot be placed exactly.
    """

    def __init__(self, spans: Iterable[TimelineSpan]) -> None:
        self._by_kind: dict[TimelineSpanKind, list[TimelineSpan]] = {}
        for span in spans:
            self.add(span)

    def add(self, span: TimelineSpan) -> None:
        self._by_kind.setdefault(span["kind"], []).append(span)

    def _select(
        self,
        kind: TimelineSpanKind,
        at: str,
        *,
        node_id: str | None = None,
        step_id: str | None = None,
        round_number: int | None = None,
    ) -> TimelineSpan | None:
        """The span of ``kind`` matching the given locators that best contains ``at``.

        Innermost-containing wins, which for spans of one kind means the latest one
        that had already started. A conversation whose start falls in no span at all
        — history and the journal are written by different processes and can disagree
        by milliseconds — attaches to the most recent earlier span rather than being
        orphaned.
        """
        candidates = [
            span
            for span in self._by_kind.get(kind, ())
            if (node_id is None or span.get("node_id") == node_id)
            and (step_id is None or span.get("step_id") == step_id)
            and (round_number is None or span.get("round") == round_number)
        ]
        containing = [
            span
            for span in candidates
            if span["started_at"] <= at and (span["ended_at"] is None or at <= span["ended_at"])
        ]
        pool = containing or [span for span in candidates if span["started_at"] <= at]
        return max(pool, key=lambda span: span["started_at"]) if pool else None

    def graph_parent(self, conversation: DagConversation, at: str) -> str | None:
        """The step, node, or round span a conversation's own labels place it in."""
        attribution = conversation["attribution"]
        node = attribution.get("nodeId")
        step = attribution.get("stepId")
        if node is not None and step is not None:
            found = self._select("step", at, node_id=node, step_id=step)
            if found is not None:
                return found["id"]
        if node is not None:
            found = self._select("node", at, node_id=node)
            if found is not None:
                return found["id"]
        round_number = attribution.get("round")
        if round_number is not None:
            found = self._select("round", at, round_number=round_number)
            if found is not None:
                return found["id"]
        return None

    def dispatch_parent(self, conversation: DagConversation, at: str) -> str | None:
        """Where one conversation hangs: a lint run nests inside the dispatch it ran in.

        `transportRole: "llmlint"` is verification activity *within* a worker
        dispatch, not a sibling agent, so it attaches to that dispatch's span. Only
        when no dispatch for the same node and step can be found does it fall back to
        the graph scope, which is where an unlinkable session belongs anyway.
        """
        attribution = conversation["attribution"]
        if attribution["transportRole"] != _LLMLINT_ROLE:
            return self.graph_parent(conversation, at)
        worker = self._select(
            "dispatch",
            at,
            node_id=attribution.get("nodeId"),
            step_id=attribution.get("stepId"),
        )
        return worker["id"] if worker is not None else self.graph_parent(conversation, at)


def _fold_conversations(assembly: _Assembly, conversations: Sequence[DagConversation]) -> None:
    """Add one dispatch span per conversation and one event per recorded turn.

    Worker dispatches are folded before lint ones so a lint session always finds the
    dispatch it ran under, whatever order history listed them in.
    """
    index = _DispatchIndex(assembly.spans())
    ordered = sorted(
        conversations, key=lambda item: item["attribution"]["transportRole"] == _LLMLINT_ROLE
    )
    for position, conversation in enumerate(ordered):
        transcript = conversation["conversation"]
        attribution = conversation["attribution"]
        started_at = _normalize(transcript["startedAt"])
        if started_at is None:
            continue
        conversation_id = transcript["id"]
        reference: TimelineReference = {"kind": "conversation", "value": conversation_id}
        span_id = assembly.open(
            ("dispatch", conversation_id, str(position)),
            f"dispatch-{conversation_id}",
            kind="dispatch",
            label=summarize(transcript["name"] or attribution["agentRole"]),
            started_at=started_at,
            ended_at=_conversation_end(conversation, started_at),
            parent_id=index.dispatch_parent(conversation, started_at),
            node_id=attribution.get("nodeId"),
            step_id=attribution.get("stepId"),
            round_number=attribution.get("round"),
            status=transcript["state"],
            # Both roles travel with the span so a reader can say *what* a dispatch
            # was — worker, judge, orchestrator, check-in, pr-author, lint — without
            # fetching the transcript behind every row to find out.
            agent_role=attribution["agentRole"],
            transport_role=attribution["transportRole"],
            reference=reference,
        )
        for turn in transcript["turns"]:
            item: TimelineEvent = {
                "id": turn["id"],
                "kind": CONVERSATION_TURN_KIND,
                "at": _normalize(turn["timestamp"]) or started_at,
                "status": turn["status"],
                "reference": reference,
            }
            if (node := attribution.get("nodeId")) is not None:
                item["node_id"] = node
            if (step := attribution.get("stepId")) is not None:
                item["step_id"] = step
            if (round_number := attribution.get("round")) is not None:
                item["round"] = round_number
            assembly.add_event(span_id, item)
        if attribution["transportRole"] != _LLMLINT_ROLE:
            # Only a dispatch a lint run could have run *under* becomes a candidate
            # parent. A worker that lints twice records two lint sessions on one node,
            # and the first is still open while the second starts — indexing it would
            # hang the second lint run off the first instead of off the worker.
            index.add(assembly.get(span_id))


def _observed_pr_states(snapshot: DetailSnapshot) -> dict[str, str]:
    """Observed PR state keyed by url, from the persisted monitor snapshot."""
    observed: dict[str, str] = {}
    for record in snapshot.prs.values():
        url = record.get("url")
        state = record.get("state")
        if isinstance(url, str) and url and isinstance(state, str) and state:
            observed[url] = state
    return observed


def _apply_observations(spans: Iterable[TimelineSpan], snapshot: DetailSnapshot) -> None:
    """Give a still-open publication the PR state the monitor last observed.

    The snapshot carries no timestamps, so it contributes no ordered item; what it
    does carry is the only answer to "where is this PR now" for a publication the
    journal has not closed — a merge waiting on checks records nothing further until
    it lands.
    """
    observed = _observed_pr_states(snapshot)
    if not observed:
        return
    for span in spans:
        if span["kind"] != "publication" or span["ended_at"] is not None:
            continue
        reference = span.get("reference")
        if reference is None or reference["kind"] != "pr":
            continue
        if (state := observed.get(reference["value"])) is not None:
            span["status"] = state


def assemble(
    events: Sequence[Event],
    conversations: Sequence[DagConversation],
    snapshot: DetailSnapshot,
) -> list[TimelineSpan]:
    """Fold the three recorded sources into one ordered span tree.

    Pure: it reads nothing and writes nothing. Every input is already validated by
    the reader that produced it, so this is the join, not another trust boundary.
    """
    assembly = _Assembly()
    _fold_journal(assembly, events)
    _fold_conversations(assembly, conversations)
    spans = assembly.spans()
    _apply_observations(spans, snapshot)
    return spans


def _conversations(run_id: RunId, oneharness_bin: str) -> list[DagConversation]:
    """Every conversation of the run; an absent or unreadable store yields none.

    The journal and the snapshot answer most of what a timeline is for, so a machine
    without oneharness history — or one whose store is momentarily unreadable — still
    gets the recorded graph rather than a failed request.
    """
    try:
        return run_conversations(run_id, oneharness_bin=oneharness_bin)
    except (HistoryError, ConfigError):
        return []


def run_timeline(
    runs_dir: Path,
    run_id: str,
    *,
    oneharness_bin: str = "oneharness",
    now: datetime | None = None,
) -> RunTimeline:
    """The ``RunTimeline`` for one run: every span and event, oldest first."""
    try:
        validated = validate_run_id(run_id)
    except ConfigError as exc:
        raise InvalidRunId(str(exc)) from exc
    run_dir = contained_run_dir(runs_dir, validated)
    if run_dir is None:
        raise RunNotFound(f"no recorded run {validated!r}")
    try:
        events = read_strict_events(run_dir / JOURNAL_NAME, validated)
    except ProjectionError as exc:
        raise ProjectionFailed(str(exc)) from exc
    return {
        "api_version": API_VERSION,
        "observed_at": (now or datetime.now(UTC)).isoformat(),
        "run_id": validated,
        "spans": assemble(
            events, _conversations(validated, oneharness_bin), load_snapshot(run_dir)
        ),
    }
