"""Append-only activity journal for one tracked-graph run.

The run ledger (``runs/<run-id>/round-NN/result.json``) records where a round
*ended*. The journal records how it *got there*: one line per observable
transition — nodes and steps starting and settling, branches discovered, gates
run, humans waited on and attested, PRs created, checked, readied and merged.

Two properties make it usable as evidence rather than decoration:

* **Append-only under a lock.** Concurrent lifecycle nodes journal from many
  threads (and `run-plan` invocations from many processes), so every append takes
  the same kind of advisory lock the git operations use, and fsyncs before
  releasing it.
* **Versioned records with a reconciled tail.** A crash mid-append can leave a
  torn final line, and a damaged file can hold records that break the ordering
  the sequence numbers promise. `open_journal` reconciles both on startup, so a
  reader never has to guess whether the last line is real, and a resumed run never
  hands out a sequence that is already on disk.

The journal is deliberately additive: nothing else reads it to make decisions, so
a journal failure can never change what a round does.
"""

from __future__ import annotations

import json
import math
import os
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, Protocol, TypeAlias, get_args

from .coordination import advisory_lock
from .labels import graph_labels
from .runs import NodeId, RunId, StepId

# Bump when a record's *shape* or vocabulary changes, and update
# `tests/golden/static-round-events-v<N>.json` in the same change. Readers skip
# records they do not understand rather than failing a round that is only being
# observed, so every earlier version stays supported and readable.
#
# v6 is additive: the `edit-rejected`, `conflict-resolution-started`, and
# `conflict-resolution-finished` kinds joined the vocabulary, and `edit-committed`
# gained an optional `command` beside its `operations`. A v5 record therefore still
# projects — it simply carries no `command` — and this build's records stay readable
# to a v5 reader as skipped-unknown rather than as corruption.
#
# v7 is additive too: the `publication-failed` kind joined the vocabulary, for a
# publication that ended before any gate could rule on it. A v6 reader skips it as
# unknown, which is exactly the evidence gap it exists to close for a v7 reader.
#
# v8 is additive inside a record rather than in the kind vocabulary: an
# `edit-committed` may now compile a `context-added` operation, the planner note a
# carried-forward node's next dispatch reads. The version is what protects a v7
# reader from it — strict replay refuses a committed operation it cannot fold, so a
# note written at v8 must be skippable as an unknown version rather than met as
# corruption in a round that is otherwise healthy.
#
# v9 is additive as well: `planner-surface-queued` joined the vocabulary, recorded
# when a surface is *sent* rather than when it is delivered. A v8 reader skips it and
# sees exactly what it saw before — which is the gap it closes, because until v9 an
# update nobody read was indistinguishable from an update nobody sent.
#
# v10 is additive inside a record, for the same reason v8 was: an `edit-committed`
# may now compile `node-parked` and `node-requeued` operations, the planner's park
# and resume of one node. Strict replay refuses a committed operation it cannot fold,
# so a park written at v10 must be skippable by a v9 reader as an unknown version
# rather than met as corruption in a round that is otherwise healthy.
SCHEMA_VERSION = 10
SUPPORTED_SCHEMA_VERSIONS = frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9, SCHEMA_VERSION})

JOURNAL_NAME = "events.jsonl"
REQUIRED_EVENT_FIELDS = ("version", "seq", "at", "kind", "run_id", "round")
OPTIONAL_EVENT_FIELDS = ("node", "step", "detail")

# ``RunId``/``NodeId``/``StepId`` come from the ledger rather than being redeclared
# here: the journal lives inside the run directory the ledger names and records the
# same nodes and steps it does, so a second set of types could only ever drift from
# the ledger's.
#
# A detail payload is JSON on the wire, and this is that shape stated in the type
# system rather than surrendered to ``Any``. It stays open at the *leaves* on
# purpose: the journal observes execution paths it must not constrain, so pinning
# one TypedDict per kind here would couple the observer to the internals of
# everything it watches — a lifecycle gaining a field would break the journal
# rather than be recorded by it. Closing the value type still buys the checking
# that matters: a payload can no longer smuggle a `Path`, a dataclass, or any
# other object that would raise at `json.dumps` time, after the round is over and
# the event it was meant to record is gone.
DetailValue: TypeAlias = (
    "str | int | float | bool | None | Sequence[DetailValue] | Mapping[str, DetailValue]"
)
Detail: TypeAlias = "Mapping[str, DetailValue]"

EventKind = Literal[
    "node-added",
    "edge-added",
    "edit-committed",
    "edit-rejected",
    "node-dropped",
    "edge-removed",
    "reparent",
    "retry-requested",
    "completion-requested",
    "round-started",
    "round-finished",
    # Sent, and delivered, are two different facts about one surface. `planner-surfaced`
    # is appended only when a planner consumes a surface, so a queue nobody reads leaves
    # no trace at all: a reader of `events.jsonl` cannot tell "nothing was sent" from
    # "two updates were sent and nobody read them". The queued record is what separates
    # them, and it is written at send time whether or not delivery ever happens.
    "planner-surface-queued",
    "planner-surfaced",
    "node-started",
    "node-settled",
    "step-started",
    "step-settled",
    "branch-discovered",
    "merge-gate-coverage",
    # The merge path is the authoritative verifier, so these now bracket the branch
    # push that runs it: `verification-finished` carries the verdict, the bounded
    # output tail, and the preserved gate log the node result points at.
    "verification-started",
    "verification-finished",
    "pr-drafting-started",
    "pr-drafting-finished",
    "pr-drafting-fallback",
    "conflict-resolution-started",
    "conflict-resolution-finished",
    "human-waiting",
    "human-attested",
    "node-failed",
    "pr-created",
    "pr-checks-observed",
    "pr-ready",
    "pr-merged",
    "publication-finished",
    # A publication that ended before any gate could rule on it: a lost base race,
    # a rebuild that could not be built. It carries the output that used to be
    # dropped, and where the whole record was preserved.
    "publication-failed",
    "cleanup-deferred",
    "lock-wait",
    "setup-finished",
    "concurrent-acknowledged",
    "upstream-modified",
    "cross-dag-satisfied",
]

# Typed as the literal it enumerates, so iterating it yields `EventKind` and a
# caller feeding it back to `append` needs no cast.
EVENT_KINDS: frozenset[EventKind] = frozenset(get_args(EventKind))
AUTHORITATIVE_EVENT_KINDS: tuple[EventKind, ...] = (
    "node-added",
    "edge-added",
    "edit-committed",
    "completion-requested",
    "round-started",
    "node-started",
    "human-waiting",
    "node-settled",
    "node-failed",
    "human-attested",
    "round-finished",
)
TERMINAL_NODE_EVENT_KINDS: tuple[EventKind, ...] = (
    "human-waiting",
    "node-settled",
    "node-failed",
)
TERMINAL_NODE_RESULT_FIELD = "result"
TERMINAL_NODE_RESULT_TYPE = "GraphResultItem"
#: `edit-committed` detail: the compiled mutations are required, and the planner
#: command that produced them is optional only because v5 records predate it. Named
#: here so the strict reader, the writer, and the checked-in golden share one source.
COMMITTED_EDIT_OPERATIONS_FIELD = "operations"
COMMITTED_EDIT_COMMAND_FIELD = "command"
COMMITTED_EDIT_COMMAND_TYPE = "EditPayload"
#: The first schema version whose `edit-committed` records carry the command.
COMMITTED_EDIT_COMMAND_SINCE = 6
AUDIT_EVENT_KINDS: frozenset[EventKind] = EVENT_KINDS - frozenset(AUTHORITATIVE_EVENT_KINDS)
ROUND_EVENT_KINDS: frozenset[EventKind] = frozenset(
    {
        "round-started",
        "round-finished",
        "completion-requested",
        "concurrent-acknowledged",
        "planner-surface-queued",
        "planner-surfaced",
    }
)
GRAPH_EVENT_KINDS: frozenset[EventKind] = frozenset(
    {
        "node-added",
        "edge-added",
        "edit-committed",
        # A rejection names a command, not a node: the id it carries may not exist, and
        # a rejected edit changes no node's state. It is a graph event without a locator
        # for the same reason `edit-committed` is.
        "edit-rejected",
        "node-dropped",
        "edge-removed",
        "reparent",
        "retry-requested",
    }
)
STEP_EVENT_KINDS: frozenset[EventKind] = frozenset({"step-started", "step-settled"})


class JournalError(Exception):
    """A journal event violates its contract, or the journal cannot be written."""


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _is_detail_value(value: object) -> bool:
    """Validate the recursive JSON value contract, including finite numbers."""
    match value:
        case None | str() | bool() | int():
            return True
        case float():
            return math.isfinite(value)
        case Mapping():
            return all(
                isinstance(key, str) and _is_detail_value(item) for key, item in value.items()
            )
        case Sequence() if not isinstance(value, str | bytes | bytearray):
            return all(_is_detail_value(item) for item in value)
        case _:
            return False


@dataclass(frozen=True)
class Event:
    """One versioned, immutable journal record.

    ``node``/``step`` locate the transition in the graph; ``detail`` carries the
    kind-specific payload (a branch name, a gate command, a PR url).

    Construction is the contract boundary: ``round`` and ``seq`` count from 1 and
    ``at`` is a real point in time. Enforcing that here rather than at each call
    site means a record read back off disk and a record about to be written are
    held to the same rule, and neither `parse_event` nor `append` can quietly
    admit a `seq` of 0 that would collide with the next append.
    """

    kind: EventKind
    run_id: RunId
    round: int
    seq: int
    at: float
    node: NodeId | None = None
    step: StepId | None = None
    detail: Detail = field(default_factory=dict)
    version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.kind not in EVENT_KINDS:
            raise JournalError(f"unknown journal event kind: {self.kind!r}")
        if not self.run_id:
            raise JournalError("journal event run_id must be a non-empty string")
        if self.node is not None and not self.node:
            raise JournalError("journal event node must be a non-empty string when present")
        if self.step is not None and not self.step:
            raise JournalError("journal event step must be a non-empty string when present")
        if self.kind not in ROUND_EVENT_KINDS | GRAPH_EVENT_KINDS and self.node is None:
            raise JournalError(f"journal event {self.kind!r} requires a node locator")
        if self.kind in STEP_EVENT_KINDS and self.step is None:
            raise JournalError(f"journal event {self.kind!r} requires a step locator")
        if not _is_positive_int(self.round):
            raise JournalError(f"journal event round must be a positive integer: {self.round!r}")
        if not _is_positive_int(self.seq):
            raise JournalError(f"journal event seq must be a positive integer: {self.seq!r}")
        if not math.isfinite(self.at) or self.at < 0:
            raise JournalError(
                f"journal event timestamp must be a finite, non-negative number: {self.at!r}"
            )
        if not _is_detail_value(self.detail):
            raise JournalError("journal event detail must contain only finite JSON values")

    def to_record(self) -> dict[str, DetailValue]:
        """Serialize to the on-disk mapping, omitting absent optional locators."""
        record: dict[str, DetailValue] = {
            "version": self.version,
            "seq": self.seq,
            "at": self.at,
            "kind": self.kind,
            "run_id": self.run_id,
            "round": self.round,
        }
        assert tuple(record) == REQUIRED_EVENT_FIELDS
        if self.node is not None:
            record["node"] = self.node
        if self.step is not None:
            record["step"] = self.step
        if self.detail:
            record["detail"] = dict(self.detail)
        return record


@dataclass(frozen=True)
class JournalOperation:
    """One event kind and payload awaiting a batched durable append."""

    kind: EventKind
    detail: Detail


class JournalSink(Protocol):
    """The append seam a journalled caller depends on.

    Callers take this rather than a concrete ``Journal | NullJournal`` union: they
    only ever append, and a union would have to be widened — at every consumer —
    to admit a sink that is neither, such as an in-memory tap in a test. The
    return is ``Event | None`` because a no-op sink has no record to hand back.
    """

    def append(
        self,
        kind: EventKind,
        *,
        node: NodeId | None = None,
        step: StepId | None = None,
        detail: Detail | None = None,
    ) -> Event | None: ...


def parse_event(record: object) -> Event | None:
    """Parse one on-disk record, returning None for anything unrecognized.

    A journal accumulates across versions of this code, so an unknown ``version``
    or ``kind`` is *skipped* rather than fatal: a reader must still be able to read
    the records it does understand. This checks the record's *shape* — the record
    is untrusted JSON, so nothing about it is guaranteed — and lets `Event` rule on
    the *values*, so a stored ``seq`` of 0 or a ``NaN`` timestamp is rejected by the
    same contract that governs a fresh append.
    """
    if not isinstance(record, dict):
        return None
    version = record.get("version")
    if not _is_int(version) or version not in SUPPORTED_SCHEMA_VERSIONS:
        return None
    kind = record.get("kind")
    run_id = record.get("run_id")
    node = record.get("node")
    step = record.get("step")
    detail = record.get("detail", {})
    at = record.get("at")
    if kind not in EVENT_KINDS or not isinstance(run_id, str):
        return None
    if not _is_int(record.get("round")) or not _is_int(record.get("seq")):
        return None
    if isinstance(at, bool) or not isinstance(at, int | float):
        return None
    if node is not None and (not isinstance(node, str) or not node):
        return None
    if step is not None and (not isinstance(step, str) or not step):
        return None
    if not isinstance(detail, dict):
        return None
    try:
        return Event(
            kind=kind,
            run_id=RunId(run_id),
            round=record["round"],
            seq=record["seq"],
            at=float(at),
            node=None if node is None else NodeId(node),
            step=None if step is None else StepId(step),
            detail=detail,
            version=version,
        )
    except JournalError:
        # Well-shaped but out of contract (a non-positive round/seq, a non-finite
        # timestamp). That record is corrupt rather than merely from the future, so
        # skip it exactly like junk instead of failing a round that is only being
        # observed.
        return None


def _parse_line(line: str) -> Event | None:
    """Parse one journal line; None if it is junk or a record we do not understand."""
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    return parse_event(record)


def claimed_sequence(record: object, run_id: RunId) -> int | None:
    """The sequence a stored line claims for ``run_id``, whatever this build makes of it.

    Deliberately independent of `parse_event`: a record's *readability* and its
    *claim on a sequence number* are different questions. A line written by a newer
    `SCHEMA_VERSION`, or carrying a kind this build has never heard of, is still
    physically on disk holding its number, so re-issuing that number is a collision
    even though nothing here can read the record. Only the shape the claim itself
    rests on is checked: this run's id, and a sequence that could have been issued.
    """
    if not isinstance(record, dict) or record.get("run_id") != run_id:
        return None
    seq = record.get("seq")
    return seq if _is_positive_int(seq) else None


def _durable_prefix(raw: bytes) -> int:
    """Return the byte length of the longest prefix ending in a complete record.

    Appends are newline-terminated and fsynced, so a crash can only leave a
    *trailing* partial line: everything up to and including the final newline is
    durable, and anything after it was never completed.
    """
    end = raw.rfind(b"\n")
    return 0 if end < 0 else end + 1


def _truncate(path: Path, raw: bytes, keep: int) -> None:
    if keep == len(raw):
        return
    with path.open("r+b") as handle:
        handle.truncate(keep)
        handle.flush()
        os.fsync(handle.fileno())


def _events_in(raw: bytes) -> Iterator[Event]:
    """Yield every record in a durable prefix that this build can read and trust."""
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        if (event := _parse_line(line)) is not None:
            yield event


@dataclass(frozen=True)
class Reconciliation:
    """What startup found on disk, and the sequence a resumed journal continues from.

    ``last_seq`` is the highest sequence *stored for this run*, which is the number
    the next append must exceed. It is deliberately not the record count. A journal
    legitimately holds lines that are not this run's readable records — blanks, junk
    from a partial disk failure, records a newer build wrote, a stored record whose
    own values are out of contract — and counting lines instead of reading sequences
    re-issues a number that is already on disk, which is exactly the collision the
    sequence exists to prevent.

    It is equally deliberate that this counts every line that *claims* a sequence for
    this run rather than every line this build can *read*. `records` is the readable
    set; the high-water mark is not. A long-lived executor reconciling against a
    journal a newer build appended to — the planner's own `channel-next` runs from
    whatever checkout is current, while the orchestrator keeps the build it launched
    with — would otherwise skip that unreadable record and hand its number out a
    second time, which is how one run's ledger came to hold two events at ``seq``
    104 and lost the planner every live edit after it.

    It is a maximum rather than a running check that each sequence is one greater
    than the last, because concurrent appenders make gaps and ties real: `append`
    allocates from a per-process counter under the write lock, so two `run-plan`
    processes journaling the same run can each write a seq the other also wrote. A
    maximum keeps every later append clear of all of them.
    """

    records: int
    last_seq: int


def reconcile(path: Path, run_id: RunId) -> Reconciliation:
    """Repair the journal's tail; report the records kept and the sequence to resume at.

    Callers hold the journal lock. Doing this once on startup means later appends
    extend a file whose last line is known-complete, instead of concatenating onto a
    half-written one.

    Only a **torn trailing line** is removed — a crash mid-append. Appends are
    newline-terminated and fsynced, so anything past the final newline was never
    completed, which makes dropping it unambiguous.

    Nothing else is deleted, and that asymmetry is deliberate. A line this build
    cannot read is not evidence of damage: a reader cannot tell a record written by a
    newer `SCHEMA_VERSION` from a broken one, and truncating on that guess would make
    every journal lossy across an upgrade. Such lines are instead *excluded from
    interpretation* — from ``records`` and from `read_events` — which denies them any
    influence on what the run does. They are **not** excluded from ``last_seq``: a
    number already on disk is taken whether or not this build can read the record
    holding it. The same holds for another run's id appearing here: it cannot happen
    through `open_journal` (a journal is opened at the run directory its id names), so
    it means the file was corrupted or hand-edited, and skipping those records is
    strictly safer than deleting whatever they turn out to be.
    """
    if not path.exists():
        return Reconciliation(records=0, last_seq=0)
    raw = path.read_bytes()
    durable = _durable_prefix(raw)
    _truncate(path, raw, durable)
    records = 0
    last_seq = 0
    for line in raw[:durable].decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (claimed := claimed_sequence(record, run_id)) is not None:
            last_seq = max(last_seq, claimed)
        event = parse_event(record)
        if event is not None and event.run_id == run_id:
            records += 1
    return Reconciliation(records=records, last_seq=last_seq)


def read_events(path: Path) -> list[Event]:
    """Read every intact, understood record; skip a torn tail and junk lines.

    Reading never repairs, so a reader can run against a live journal without taking
    the lock an append holds.
    """
    if not path.exists():
        return []
    raw = path.read_bytes()
    return list(_events_in(raw[: _durable_prefix(raw)]))


@dataclass
class Journal:
    """A locked, append-only handle onto one run's ``events.jsonl``.

    ``path`` is derived from a ``run_dir`` the caller already resolved (``run-plan``
    honours ``--runs-dir``), so the journal never re-derives where a run lives and
    can never disagree with the ledger about it.
    """

    path: Path
    run_id: RunId
    round: int
    seq: int = 0

    @property
    def lock_identity(self) -> str:
        """The one advisory-lock identity for this file, however it was spelled.

        Two writers only exclude each other when they name the same identity, and
        they reach this journal by different routes: the executor is handed an
        absolute ``--runs-dir`` while ``channel-next`` defaults to a relative one.
        Canonicalizing here means a lock is taken on the *file*, not on a spelling
        of its path, so those two can never both be inside `append_batch` at once.
        """
        return f"journal:{self.path.resolve()}"

    def append(
        self,
        kind: EventKind,
        *,
        node: NodeId | None = None,
        step: StepId | None = None,
        detail: Detail | None = None,
    ) -> Event:
        """Durably append one record and return it.

        ``detail`` is an explicit mapping rather than ``**kwargs`` so that a payload
        key can never collide with this method's own ``kind``/``node``/``step``
        parameters — a lifecycle step legitimately has a ``kind`` of its own.

        The record is built before the sequence advances, so an event that fails its
        contract raises without burning a sequence number the file will never hold.
        """
        return self.append_batch(
            [JournalOperation(kind=kind, detail=detail or {})],
            node=node,
            step=step,
        )[0]

    def append_batch(
        self,
        operations: Sequence[JournalOperation],
        *,
        node: NodeId | None = None,
        step: StepId | None = None,
    ) -> list[Event]:
        """Durably append individual events with one lock, open, flush, and fsync.

        Each event remains its own newline-terminated replay record. A crash during
        the write can therefore leave only a valid prefix and, at worst, one torn
        trailing line for reconciliation to discard.
        """
        if not operations:
            raise JournalError("a journal batch requires at least one operation")
        with advisory_lock(self.lock_identity):
            # A planner delivery can append from ``channel-next`` while the graph
            # executor retains its own Journal instance. Refresh under the shared
            # lock so independent writers cannot reuse a stale sequence number.
            self.seq = max(self.seq, reconcile(self.path, self.run_id).last_seq)
            events: list[Event] = []
            for offset, operation in enumerate(operations, start=1):
                if operation.kind not in EVENT_KINDS:
                    raise JournalError(f"unknown journal event kind: {operation.kind!r}")
                events.append(
                    Event(
                        kind=operation.kind,
                        run_id=self.run_id,
                        round=self.round,
                        seq=self.seq + offset,
                        at=time.time(),
                        node=node,
                        step=step,
                        detail=dict(operation.detail),
                    )
                )
            with self.path.open("a", encoding="utf-8") as handle:
                for event in events:
                    handle.write(json.dumps(event.to_record(), sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self.seq = events[-1].seq
        return events

    def events(self) -> list[Event]:
        """Read only records owned by this journal's run.

        Reconciliation already ignores a foreign run id when it establishes the
        sequence high-water mark. Apply the same ownership boundary to readers so
        a corrupt or hand-edited line cannot surface as activity for this run.
        """
        return [event for event in read_events(self.path) if event.run_id == self.run_id]


def open_journal(run_dir: Path, run_id: RunId, round_number: int) -> Journal:
    """Open (creating if needed) a run's journal, reconciling it first.

    Sequence numbers continue from the highest one already stored for this run, so a
    resumed run keeps one monotonic sequence across rounds.
    """
    if not run_id:
        raise JournalError("journal run_id must be a non-empty string")
    if not _is_positive_int(round_number):
        raise JournalError(f"journal round must be a positive integer: {round_number!r}")
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / JOURNAL_NAME
    journal = Journal(path=path, run_id=run_id, round=round_number)
    with advisory_lock(journal.lock_identity):
        state = reconcile(path, run_id)
    journal.seq = state.last_seq
    return journal


@dataclass
class NullJournal:
    """Journal-shaped no-op for an unrecorded round (``run-plan --no-record``)."""

    def append(
        self,
        kind: EventKind,
        *,
        node: NodeId | None = None,
        step: StepId | None = None,
        detail: Detail | None = None,
    ) -> None:
        return None


class NodeSink(Protocol):
    """The scoped seam the code *inside* one node — lifecycle, merge — depends on.

    A node's own code never names itself when it records something. It is handed
    one of these, already bound to its place in the graph, and appends against it;
    `labels` renders that same place for a dispatched subprocess's history entry,
    so a recorded event and a recorded session agree by construction rather than by
    two call sites being kept in step.
    """

    def append(
        self,
        kind: EventKind,
        *,
        node: NodeId | None = None,
        step: StepId | None = None,
        detail: Detail | None = None,
    ) -> Event | None: ...

    def for_step(self, step: StepId) -> NodeSink: ...

    @property
    def labels(self) -> Mapping[str, str]: ...

    @property
    def artifact_dir(self) -> Path | None: ...


@dataclass(frozen=True)
class NodeJournal:
    """A run's journal narrowed to one node, and optionally to one of its steps.

    Concurrent nodes share a round's single `Journal`, and the lifecycle hands its
    sink down through code that has no business knowing which node it serves. That
    is what this exists to make safe: the locators are bound **here**, once, where
    the graph still knows them, so a node physically cannot append an event — or
    label a dispatch — that claims to be a sibling running beside it.

    ``run_id``/``round`` duplicate what the underlying `Journal` already stamps on
    every record. They are carried anyway because `labels` must render the same
    coordinates for a *subprocess*, which never sees the journal; `append` does not
    use them, so the record's run and round still come from the one writer that
    owns the file.
    """

    sink: JournalSink
    node: NodeId
    run_id: RunId | None = None
    round: int | None = None
    step: StepId | None = None

    @property
    def artifact_dir(self) -> Path | None:
        """Stable directory for this node or step's full execution artifacts."""
        if not isinstance(self.sink, Journal) or self.round is None:
            return None
        root = self.sink.path.parent / f"round-{self.round:02d}" / str(self.node)
        return root / str(self.step) if self.step is not None else root

    def __post_init__(self) -> None:
        if not self.node:
            raise JournalError("node journal node must be a non-empty string")
        if self.step is not None and not self.step:
            raise JournalError("node journal step must be a non-empty string when present")
        if self.run_id is not None and not self.run_id:
            raise JournalError("node journal run_id must be a non-empty string when present")
        if self.round is not None and not _is_positive_int(self.round):
            raise JournalError(f"node journal round must be a positive integer: {self.round!r}")

    def for_step(self, step: StepId) -> NodeJournal:
        """Narrow this node's journal to one of its steps."""
        return replace(self, step=step)

    def append(
        self,
        kind: EventKind,
        *,
        node: NodeId | None = None,
        step: StepId | None = None,
        detail: Detail | None = None,
    ) -> Event | None:
        """Append one record located at this scope.

        The locators are accepted only to satisfy `JournalSink`, and only to
        *restate* the binding: passing another node's id is the mistake this scope
        exists to prevent, so it raises rather than being quietly honoured or
        quietly ignored.
        """
        if kind in ROUND_EVENT_KINDS:
            raise JournalError(
                f"journal event {kind!r} is a round transition and cannot be scoped to a node"
            )
        if node is not None and node != self.node:
            raise JournalError(
                f"journal scoped to node {self.node!r} cannot append an event for node {node!r}"
            )
        if step is not None and self.step is not None and step != self.step:
            raise JournalError(
                f"journal scoped to step {self.step!r} cannot append an event for step {step!r}"
            )
        return self.sink.append(
            kind,
            node=self.node,
            step=self.step if step is None else step,
            detail=detail,
        )

    @property
    def labels(self) -> dict[str, str]:
        """This scope as ``ONEHARNESS_HISTORY_LABELS`` for a dispatched subprocess."""
        return graph_labels(
            run_id=self.run_id, round_number=self.round, node=self.node, step=self.step
        )


#: The node id every untracked lifecycle run labels its dispatches with. One fixed
#: name, because there is exactly one node in such a run: the workstream itself.
UNTRACKED_NODE = NodeId("repo-task")
#: Prefix of the synthetic run id an untracked lifecycle run labels with. It names a
#: run that is real work but has no run *directory*, so it is deliberately shaped so
#: nothing mistakes it for one: no recorded run id has this form.
UNTRACKED_RUN_PREFIX = "repo-task-"


@dataclass(frozen=True)
class NullNodeJournal:
    """Node-scoped no-op for a lifecycle run outside any tracked graph.

    A `run_repo_task` reached outside a tracked round — a recovery, or a caller
    driving the unit directly — has no run directory, round, or graph node, so there
    is nothing to record against and `append` stays a no-op.

    It still *labels*, and that is not a contradiction. Recording is about a ledger
    another process reads to make decisions; labelling is about the history sessions
    this workstream produces being findable as one another's company afterwards. A
    dispatch with no labels at all left every session it produced — worker, judge,
    and the lint runs under them — joinable to nothing, which is why measured
    telemetry could see hundreds of sessions and attribute none of them to any work.
    The scope is synthetic and says so: a run id no run directory can have, and one
    node named for the command that made it.
    """

    run_id: RunId = field(
        default_factory=lambda: RunId(f"{UNTRACKED_RUN_PREFIX}{uuid.uuid4().hex[:12]}")
    )
    step: StepId | None = None

    def for_step(self, step: StepId) -> NullNodeJournal:
        return replace(self, step=step)

    def append(
        self,
        kind: EventKind,
        *,
        node: NodeId | None = None,
        step: StepId | None = None,
        detail: Detail | None = None,
    ) -> None:
        return None

    @property
    def labels(self) -> dict[str, str]:
        """This untracked workstream as ``ONEHARNESS_HISTORY_LABELS``."""
        return graph_labels(run_id=self.run_id, node=UNTRACKED_NODE, step=self.step)

    @property
    def artifact_dir(self) -> None:
        return None
