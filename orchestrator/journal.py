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
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, TypeAlias, get_args

from .coordination import advisory_lock
from .runs import NodeId, RunId, StepId

# Bump when a record's *shape* changes incompatibly. Readers skip records they do
# not understand rather than failing a round that is only being observed.
SCHEMA_VERSION = 1

JOURNAL_NAME = "events.jsonl"

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
    "round-started",
    "round-finished",
    "node-started",
    "node-settled",
    "step-started",
    "step-settled",
    "branch-discovered",
    "verification-started",
    "verification-finished",
    "human-waiting",
    "human-attested",
    "node-failed",
    "pr-created",
    "pr-checks-observed",
    "pr-ready",
    "pr-merged",
]

# Typed as the literal it enumerates, so iterating it yields `EventKind` and a
# caller feeding it back to `append` needs no cast.
EVENT_KINDS: frozenset[EventKind] = frozenset(get_args(EventKind))


class JournalError(Exception):
    """A journal event violates its contract, or the journal cannot be written."""


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


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
        if not _is_positive_int(self.round):
            raise JournalError(f"journal event round must be a positive integer: {self.round!r}")
        if not _is_positive_int(self.seq):
            raise JournalError(f"journal event seq must be a positive integer: {self.seq!r}")
        if not math.isfinite(self.at) or self.at < 0:
            raise JournalError(
                f"journal event timestamp must be a finite, non-negative number: {self.at!r}"
            )

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
        if self.node is not None:
            record["node"] = self.node
        if self.step is not None:
            record["step"] = self.step
        if self.detail:
            record["detail"] = dict(self.detail)
        return record


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
    if not isinstance(record, dict) or record.get("version") != SCHEMA_VERSION:
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
    every journal lossy across an upgrade. Such lines are instead *excluded* — from
    ``records``, from ``last_seq``, and from `read_events` — which already denies them
    any influence on the run. The same holds for another run's id appearing here:
    it cannot happen through `open_journal` (a journal is opened at the run directory
    its id names), so it means the file was corrupted or hand-edited, and skipping
    those records is strictly safer than deleting whatever they turn out to be.
    """
    if not path.exists():
        return Reconciliation(records=0, last_seq=0)
    raw = path.read_bytes()
    durable = _durable_prefix(raw)
    _truncate(path, raw, durable)
    records = 0
    last_seq = 0
    for event in _events_in(raw[:durable]):
        if event.run_id != run_id:
            continue
        records += 1
        last_seq = max(last_seq, event.seq)
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
        return f"journal:{self.path}"

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
        if kind not in EVENT_KINDS:
            raise JournalError(f"unknown journal event kind: {kind!r}")
        with advisory_lock(self.lock_identity):
            event = Event(
                kind=kind,
                run_id=self.run_id,
                round=self.round,
                seq=self.seq + 1,
                at=time.time(),
                node=node,
                step=step,
                detail=dict(detail or {}),
            )
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event.to_record(), sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self.seq = event.seq
        return event

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
