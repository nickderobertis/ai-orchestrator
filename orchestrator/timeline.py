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
  conversation id, opaque artifact id, or PR url, so a consumer fetches only what
  it opens.

The timeline is derived, never authoritative: nothing reads it to make a decision.
"""

# llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] These payload types
# are gated by scripts/check-dag-state-contract.py, which reconciles their field names,
# optionality, and closed value vocabularies against docs/dag-ui/design.md. Structural
# field *types* stay ungated on purpose — see that script's own note.

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, NamedTuple, NotRequired, TypedDict

from .config import ConfigError
from .conversations import DagConversation, dispatch_start, parse_stamp, run_conversations
from .history import HistoryError
from .journal import JOURNAL_NAME, Detail, Event, EventKind
from .monitor import DetailSnapshot, load_snapshot, summarize
from .projection import ProjectionError, read_strict_events
from .read_model import (
    API_VERSION,
    InvalidRunId,
    ProjectionFailed,
    RunNotFound,
    _artifact_id,
    contained_run_dir,
    make_servable,
)
from .runs import NodeId, RunId, validate_run_id
from .supervisory import (
    DriverState,
    SupervisoryCapture,
    SupervisoryPhase,
    driver_state,
    load_captures,
)
from .telemetry import native_session_groups

TimelineScope = Literal["run"]

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

#: The semantic role of the process driving a tracked run. Its span is the one that
#: carries `phase`, because the phase describes that loop and nothing else.
_DRIVER_ROLE = "orchestrator"

#: The transport role a locally captured supervisory session is served under: a capture
#: stands in for the *agent* side of one onejudge dispatch, which is the side whose
#: harness write failed. The supervisor beside it is its own session and its own record.
_CAPTURE_TRANSPORT_ROLE = "agent"

#: The event kind naming a harness that refused to write a session's history. Recorded
#: as an event inside the served span, so the reason a transcript is missing travels
#: with the span standing in for it rather than being inferred from the absence.
HISTORY_WRITE_FAILURE_KIND = "history-write-failed"

#: The turn status a captured turn is served with. Deliberately not one of the
#: transcript states: this turn is known from the run's own bounded capture, not from a
#: recorded transcript, and a reader must be able to tell the two apart.
_CAPTURED_TURN_STATUS = "captured"

#: The step id `orchestrator.lifecycle.run_repo_task` synthesizes for a workstream
#: that was given one `(persona, task)` rather than a step DAG. It is a name for the
#: node itself, so serving a span for it manufactured a container that held most of a
#: node's time, linked no log, and meant nothing. A plan that *declares* a step with
#: this id keeps its span like any other declared step.
_SYNTHESIZED_STEP_ID = "main"

#: How many lock waits a rollup names individually. The rollup exists because a real
#: run records thousands, and one bar spanning the whole contention window says only
#: "this node contended sometime"; the largest few say when the run actually stalled.
#: Bounded so the payload stays sized by the graph rather than by contention.
ROLLUP_INTERVAL_LIMIT = 20


class TimelineReference(TypedDict):
    """Where one item's heavy content lives, so the payload can omit it."""

    kind: TimelineReferenceKind
    value: str


class VerificationDetail(TypedDict, total=False):
    """Validated, bounded detail exposed for one verification span."""

    ok: bool
    output_tail: str
    artifact_id: str
    # Internal until `run_timeline` replaces it with the opaque artifact id.
    log_path: str


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


class TimelineInterval(TypedDict):
    """One discrete wait a rollup absorbed, so a client can render it as itself."""

    started_at: str
    ended_at: str


class TimelineSpan(TypedDict):
    """One interval of recorded work, and the events observed inside it.

    ``ended_at`` is ``None`` for work the stream never closed — which is what an
    in-flight run looks like, not an error. ``parent_id`` links spans into the tree
    the recorded nesting implies; a span with no parent is run-level.

    ``count``, ``total_duration_ms`` and ``intervals`` appear only on a ``rollup``
    span, and the role pair and ``dispatch_id`` only on a ``dispatch`` one. ``phase``
    appears only on the launched orchestrator's own dispatch span, where it says which
    part of its loop the run's recorded state places the driver in.
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
    intervals: NotRequired[list[TimelineInterval]]
    agent_role: NotRequired[str]
    transport_role: NotRequired[str]
    dispatch_id: NotRequired[str]
    reference: NotRequired[TimelineReference]
    detail: NotRequired[VerificationDetail]
    phase: NotRequired[SupervisoryPhase]


class RunTimeline(TypedDict):
    """The ``RunTimeline`` served by ``GET /api/v2/runs/{run_id}/timeline``."""

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
    naive or non-UTC stamp is converted rather than passed through. The parse is
    `conversations.parse_stamp`, which is the one reading every consumer of a
    recorded stamp shares.
    """
    parsed = parse_stamp(value)
    return parsed.isoformat() if parsed is not None else None


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


def _verification_detail(detail: Detail) -> VerificationDetail:
    """Select and validate only verification fields in the public contract."""
    result: VerificationDetail = {}
    if isinstance(ok := detail.get("ok"), bool):
        result["ok"] = ok
    if isinstance(output := detail.get("output_tail"), str):
        result["output_tail"] = output
    if isinstance(path := detail.get("log_path"), str) and path:
        result["log_path"] = path
    return result


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
        dispatch_id: str | None = None,
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
        if dispatch_id is not None:
            span["dispatch_id"] = dispatch_id
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
        detail: VerificationDetail | None = None,
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
        if detail is not None:
            span["detail"] = detail.copy()
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


class _Wait(NamedTuple):
    """One absorbed wait: how long it lasted, and when the record closing it was made.

    Ordered longest-last by the ``seconds`` it carries, which is what lets a rollup
    keep the largest few by dropping its minimum.
    """

    seconds: float
    at: float


@dataclass
class _Rollup:
    """What one rollup span has absorbed so far, accumulated across the pass.

    ``waits`` keeps only the longest few, because a client renders those and the
    payload must not grow with contention. Each is derived from the record's own
    elapsed seconds ending at the moment it was recorded: a lock wait is journalled
    once it has been served, so the record's ``at`` is when the waiting stopped.
    """

    span_id: str
    count: int = 0
    seconds: float = 0.0
    waits: list[_Wait] = field(default_factory=list)

    def absorb(self, seconds: float, at: float) -> None:
        self.count += 1
        self.seconds += seconds
        if seconds <= 0:
            return
        self.waits.append(_Wait(seconds, at))
        if len(self.waits) > ROLLUP_INTERVAL_LIMIT:
            self.waits.remove(min(self.waits))

    def intervals(self) -> list[TimelineInterval]:
        """The kept waits as intervals, oldest first, dropping any unplaceable one.

        Ordered by when each wait *began*, like every other span in this payload —
        the waits kept are of different lengths, so ordering them by the moment they
        were recorded would hand a client a list it has to sort again to draw.
        """
        found: list[TimelineInterval] = []
        for wait in self.waits:
            start, end = _stamp(wait.at - wait.seconds), _stamp(wait.at)
            if start is not None and end is not None:
                found.append({"started_at": start, "ended_at": end})
        return sorted(found, key=lambda interval: (interval["started_at"], interval["ended_at"]))


class _Fold:
    """The journal pass, kept as an object so the span table is threaded once."""

    def __init__(self, assembly: _Assembly, declared_steps: Mapping[str, frozenset[str]]) -> None:
        self.assembly = assembly
        #: Step ids each node's own plan definition declares, so a step the lifecycle
        #: synthesized can be told from one the plan asked for.
        self._declared_steps = declared_steps
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
            case "step-started" | "step-settled" if (
                node is not None and step is not None and not self._serves_step(node, step)
            ):
                return
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
                    event,
                    at,
                    kind="verification",
                    reference=_event_reference(detail),
                    detail=_verification_detail(detail),
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

    def _serves_step(self, node: str, step: str) -> bool:
        """Whether this step is one the plan asked for rather than one synthesized.

        A workstream given a single ``(persona, task)`` runs as one `Step("main")`, and
        a span for it is a container the plan never described: it holds the node's
        whole dispatch, links no log of its own, and reads as "Phase: main". Its
        children attach to the node instead. A plan that declares a step by that name
        keeps its span, because then the step really is part of the recorded graph.
        """
        return step != _SYNTHESIZED_STEP_ID or step in self._declared_steps.get(node, frozenset())

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
        detail: VerificationDetail | None = None,
    ) -> None:
        key = _scoped_key(event, kind)
        self._leave(
            key,
            self.assembly.close(
                key,
                ended_at=at,
                status=status or _status(event.detail) or "finished",
                reference=reference,
                detail=detail,
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
        rollup.absorb(_seconds(event.detail), event.at)
        span = self.assembly.get(rollup.span_id)
        span["ended_at"] = at
        span["count"] = rollup.count
        span["total_duration_ms"] = round(rollup.seconds * 1000)
        if intervals := rollup.intervals():
            span["intervals"] = intervals

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


def _declared_steps(events: Sequence[Event]) -> dict[str, frozenset[str]]:
    """The step ids each node's journalled plan definition declares, by node id."""
    declared: dict[str, frozenset[str]] = {}
    for event in events:
        definition = event.detail.get("definition")
        if not isinstance(definition, Mapping):
            continue
        node = definition.get("id")
        steps = definition.get("steps")
        if not isinstance(node, str) or not node or not isinstance(steps, list):
            continue
        declared[node] = frozenset(
            step_id
            for step in steps
            if isinstance(step, Mapping) and isinstance(step_id := step.get("id"), str) and step_id
        )
    return declared


def _fold_journal(assembly: _Assembly, events: Sequence[Event]) -> None:
    """Apply the journal in recorded order: boundaries open/close, the rest nest."""
    fold = _Fold(assembly, _declared_steps(events))
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


def _conversation_start(conversation: DagConversation) -> str | None:
    """When a dispatched session began working, as this API spells a timestamp.

    The rule itself lives in `conversations.dispatch_start`, because the linkage that
    pairs a supervisor with the dispatch it supervised orders sessions by the same
    answer — two starts for one session would pair transcripts one way and draw them
    another.
    """
    started = dispatch_start(conversation)
    return started.isoformat() if started is not None else None


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

    def round_parent(self, round_number: int | None, at: str) -> str | None:
        """The round span containing ``at``, for an item located by round alone.

        A supervisory capture carries the graph locators its dispatch was labelled
        with and nothing finer: a check-in names a round, and the driver's own session
        names neither. Both are legitimately run-level when no round contains them.
        """
        if round_number is None:
            return None
        found = self._select("round", at, round_number=round_number)
        return found["id"] if found is not None else None

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
        started_at = _conversation_start(conversation)
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
            agent_role=attribution["agentRole"],
            transport_role=attribution["transportRole"],
            # One onejudge dispatch is several oneharness sessions, and the served key
            # that groups them is the id of the one they hang off: the agent session
            # itself for the dispatch, and its own id for the supervisor and lint runs
            # that ran inside it.
            dispatch_id=attribution.get("parentConversationId", conversation_id),
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


def _fold_captures(
    assembly: _Assembly,
    captures: Sequence[SupervisoryCapture],
    conversations: Sequence[DagConversation],
) -> None:
    """Serve each supervisory session history did not record, from its local capture.

    The capture is a *fallback*, never a duplicate: a session oneharness recorded is
    already a dispatch span with its own transcript, and serving the capture beside it
    would draw one session twice. A capture is matched to that recorded session by the
    pair its labels and its own record agree on — semantic role and round — because
    that is what identifies a supervisory session: one driver per run, one check-in per
    round.

    What survives that match is a session whose harness refused the history write. It
    is served with the same role, timing, and open-ended liveness a recorded one would
    have, its bounded captured turns as events, and — when the capture recorded one —
    the refusal itself as a `history-write-failed` event, so the reason its transcript
    is missing travels with the span standing in for it.
    """
    served = {
        (item["attribution"]["agentRole"], item["attribution"].get("round"))
        for item in conversations
    }
    index = _DispatchIndex(assembly.spans())
    for capture in captures:
        if (capture.agent_role, capture.round) in served:
            continue
        started_at = _normalize(capture.started_at)
        if started_at is None:
            continue
        span_id = assembly.open(
            ("supervisory", capture.session),
            f"capture-{capture.session}",
            kind="dispatch",
            label=summarize(capture.session),
            started_at=started_at,
            ended_at=_normalize(capture.finished_at),
            parent_id=index.round_parent(capture.round, started_at),
            round_number=capture.round,
            status=capture.status,
            agent_role=capture.agent_role,
            transport_role=_CAPTURE_TRANSPORT_ROLE,
        )
        span = assembly.get(span_id)
        if tail := capture.transcript_tail:
            span["detail"] = {"output_tail": tail}
        for position, turn in enumerate(capture.turns):
            assembly.add_event(
                span_id,
                {
                    "id": f"{span_id}-turn-{position}",
                    "kind": CONVERSATION_TURN_KIND,
                    "at": _normalize(turn["at"]) or started_at,
                    "status": _CAPTURED_TURN_STATUS,
                },
            )
        if capture.history_failure is not None:
            assembly.add_event(
                span_id,
                {
                    "id": f"{span_id}-history-write-failed",
                    "kind": HISTORY_WRITE_FAILURE_KIND,
                    "at": _normalize(capture.finished_at) or started_at,
                    "status": capture.history_failure,
                },
            )


def _apply_driver_phase(spans: Sequence[TimelineSpan], driver: DriverState | None) -> None:
    """Give the driver's own span the phase the run's recorded state places it in.

    The newest one only: a resumed run has more than one driver session, and the phase
    describes what is happening *now*, so labelling an earlier one with it would date a
    finished session by the state of the run that outlived it.
    """
    if driver is None:
        return
    candidates = [
        span
        for span in spans
        if span["kind"] == "dispatch" and span.get("agent_role") == _DRIVER_ROLE
    ]
    if candidates:
        max(candidates, key=lambda span: (span["started_at"], span["id"]))["phase"] = driver.phase


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
    captures: Sequence[SupervisoryCapture] = (),
    driver: DriverState | None = None,
) -> list[TimelineSpan]:
    """Fold the recorded sources into one ordered span tree.

    Pure: it reads nothing and writes nothing. Every input is already validated by
    the reader that produced it, so this is the join, not another trust boundary.

    ``captures`` and ``driver`` are the supervisory tier's own record, and both default
    to absent so a caller that only holds the three history-derived sources gets
    exactly the tree it always did.
    """
    assembly = _Assembly()
    _fold_journal(assembly, events)
    _fold_conversations(assembly, conversations)
    _fold_captures(assembly, captures, conversations)
    spans = assembly.spans()
    _apply_observations(spans, snapshot)
    _apply_driver_phase(spans, driver)
    return spans


def _conversations(
    run_id: RunId, oneharness_bin: str, events: Sequence[Event]
) -> list[DagConversation]:
    """Every conversation of the run; an absent or unreadable store yields none.

    The journal and the snapshot answer most of what a timeline is for, so a machine
    without oneharness history — or one whose store is momentarily unreadable — still
    gets the recorded graph rather than a failed request.
    """
    try:
        return run_conversations(
            run_id,
            oneharness_bin=oneharness_bin,
            native_groups=native_session_groups(events),
        )
    except (HistoryError, ConfigError):
        return []


def run_timeline(
    runs_dir: Path,
    run_id: str,
    *,
    oneharness_bin: str = "oneharness",
    now: datetime | None = None,
    node_id: NodeId | None = None,
    scope: TimelineScope | None = None,
) -> RunTimeline:
    """A scoped ``RunTimeline``: one node, or only run-level items."""
    if node_id is not None and scope is not None:
        raise InvalidRunId("timeline accepts node_id or scope=run, not both")
    if scope not in (None, "run"):
        raise InvalidRunId("timeline scope must be run")
    if node_id == "":
        raise InvalidRunId("invalid node_id")
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
    known_node_ids = {str(event.node) for event in events if event.node is not None}
    for event in events:
        definition = event.detail.get("definition")
        if isinstance(definition, Mapping):
            definition_id = definition.get("id")
            if isinstance(definition_id, str):
                known_node_ids.add(definition_id)
    if node_id is not None and node_id not in known_node_ids:
        raise InvalidRunId("node_id does not name a node in this run")
    captures = load_captures(run_dir)
    spans = assemble(
        events,
        _conversations(validated, oneharness_bin, events),
        load_snapshot(run_dir),
        captures,
        driver_state(run_dir, captures),
    )
    if node_id is not None:
        spans = [span for span in spans if span.get("node_id") == node_id]
    elif scope == "run":
        spans = _run_scope(spans)
    by_path: dict[str, str] = {}
    for event in events:
        result = event.detail.get("result")
        artifacts = result.get("artifacts") if isinstance(result, Mapping) else None
        if isinstance(artifacts, Mapping):
            for kind in _ARTIFACT_KINDS:
                path = artifacts.get(kind)
                if isinstance(path, str) and path:
                    by_path[path] = _artifact_id(kind, path)
    for span in spans:
        candidates: list[TimelineSpan | TimelineEvent] = [span, *span["events"]]
        for item in candidates:
            reference = item.get("reference")
            if reference and reference["kind"] in _ARTIFACT_KINDS:
                path = reference["value"]
                reference["value"] = by_path.get(path, _artifact_id(reference["kind"], path))
        detail = span.get("detail")
        if detail is not None:
            log_path = detail.pop("log_path", None)
            if isinstance(log_path, str):
                detail["artifact_id"] = by_path.get(log_path, _artifact_id("gate_log", log_path))
    timeline: RunTimeline = {
        "api_version": API_VERSION,
        "observed_at": (now or datetime.now(UTC)).isoformat(),
        "run_id": validated,
        "spans": spans,
    }
    # Labels and details here are the same journalled text the run detail serves, so
    # they carry the same unpaired surrogates and would fail the same way.
    make_servable(timeline)
    return timeline


def _run_scope(spans: Sequence[TimelineSpan]) -> list[TimelineSpan]:
    """Return run work and bounded summaries of every node's nested activity."""
    scoped = [deepcopy(span) for span in spans if span.get("node_id") is None]
    nodes = [span for span in spans if span["kind"] == "node"]
    for node in nodes:
        summary = deepcopy(node)
        summary["events"] = []
        scoped.append(summary)
        children = [
            span
            for span in spans
            if span.get("node_id") == node.get("node_id") and span is not node
        ]
        groups: dict[tuple[str, str | None], list[TimelineSpan]] = {}
        for child in children:
            role = child.get("agent_role") if child["kind"] == "dispatch" else None
            groups.setdefault((child["kind"], role), []).append(child)
        for (kind, role), grouped in groups.items():
            first, last = grouped[0], grouped[-1]
            durations = [
                (
                    datetime.fromisoformat(item["ended_at"])
                    - datetime.fromisoformat(item["started_at"])
                ).total_seconds()
                * 1000
                for item in grouped
                if item["ended_at"] is not None
            ]
            rollup: TimelineSpan = {
                "id": f"summary-{node['id']}-{kind}-{role or 'activity'}",
                "kind": "rollup",
                "label": role or kind,
                "parent_id": node["id"],
                "started_at": first["started_at"],
                "ended_at": last["ended_at"],
                "count": len(grouped),
                "total_duration_ms": int(sum(durations)),
                "events": [],
            }
            if (node_id := node.get("node_id")) is not None:
                rollup["node_id"] = node_id
            if (round_number := node.get("round")) is not None:
                rollup["round"] = round_number
            if role is not None:
                rollup["agent_role"] = role
            scoped.append(rollup)
    return sorted(scoped, key=lambda span: (span["started_at"], span["id"]))
