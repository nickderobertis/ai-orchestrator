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
  torn final line. `open_journal` truncates that partial record on startup, so a
  reader never has to guess whether the last line is real.

The journal is deliberately additive: nothing else reads it to make decisions, so
a journal failure can never change what a round does.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, get_args

from .coordination import advisory_lock

# Bump when a record's *shape* changes incompatibly. Readers skip records they do
# not understand rather than failing a round that is only being observed.
SCHEMA_VERSION = 1

JOURNAL_NAME = "events.jsonl"

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

EVENT_KINDS: frozenset[str] = frozenset(get_args(EventKind))


class JournalError(Exception):
    """A journal event kind is unknown, or the journal cannot be written."""


@dataclass(frozen=True)
class Event:
    """One versioned, immutable journal record.

    ``node``/``step`` locate the transition in the graph; ``detail`` carries the
    kind-specific payload (a branch name, a gate command, a PR url). ``detail`` is
    intentionally open: the journal *observes* existing execution paths, and
    pinning every payload here would couple it to internals it must not constrain.
    """

    kind: EventKind
    run_id: str
    round: int
    seq: int = 0
    at: float = 0.0
    node: str | None = None
    step: str | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)
    version: int = SCHEMA_VERSION

    def to_record(self) -> dict[str, Any]:
        """Serialize to the on-disk mapping, omitting absent optional locators."""
        record: dict[str, Any] = {
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


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_event(record: Any) -> Event | None:
    """Parse one on-disk record, returning None for anything unrecognized.

    A journal accumulates across versions of this code, so an unknown ``version``
    or ``kind`` is *skipped* rather than fatal: a reader must still be able to read
    the records it does understand.
    """
    if not isinstance(record, dict) or record.get("version") != SCHEMA_VERSION:
        return None
    kind = record.get("kind")
    run_id = record.get("run_id")
    node = record.get("node")
    step = record.get("step")
    detail = record.get("detail", {})
    at = record.get("at")
    if kind not in EVENT_KINDS or not isinstance(run_id, str) or not run_id:
        return None
    if not _is_int(record.get("round")) or not _is_int(record.get("seq")):
        return None
    if isinstance(at, bool) or not isinstance(at, int | float):
        return None
    if node is not None and not isinstance(node, str):
        return None
    if step is not None and not isinstance(step, str):
        return None
    if not isinstance(detail, dict):
        return None
    return Event(
        kind=kind,
        run_id=run_id,
        round=record["round"],
        seq=record["seq"],
        at=float(at),
        node=node,
        step=step,
        detail=detail,
    )


def _durable_prefix(raw: bytes) -> int:
    """Return the byte length of the longest prefix ending in a complete record.

    Appends are newline-terminated and fsynced, so a crash can only leave a
    *trailing* partial line: everything up to and including the final newline is
    durable, and anything after it was never completed.
    """
    end = raw.rfind(b"\n")
    return 0 if end < 0 else end + 1


def reconcile(path: Path) -> int:
    """Drop a torn trailing record; return the number of intact records left.

    Callers hold the journal lock. Doing this once on startup means later appends
    extend a file whose last line is known-complete, instead of concatenating onto
    a half-written one.
    """
    if not path.exists():
        return 0
    raw = path.read_bytes()
    keep = _durable_prefix(raw)
    if keep != len(raw):
        with path.open("r+b") as handle:
            handle.truncate(keep)
            handle.flush()
            os.fsync(handle.fileno())
    return raw[:keep].count(b"\n")


def read_events(path: Path) -> list[Event]:
    """Read every intact, understood record; skip a torn tail and junk lines."""
    if not path.exists():
        return []
    raw = path.read_bytes()
    events: list[Event] = []
    for line in raw[: _durable_prefix(raw)].decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (event := parse_event(record)) is not None:
            events.append(event)
    return events


@dataclass
class Journal:
    """A locked, append-only handle onto one run's ``events.jsonl``.

    ``path`` is derived from a ``run_dir`` the caller already resolved (``run-plan``
    honours ``--runs-dir``), so the journal never re-derives where a run lives and
    can never disagree with the ledger about it.
    """

    path: Path
    run_id: str
    round: int
    seq: int = 0

    @property
    def lock_identity(self) -> str:
        return f"journal:{self.path}"

    def append(
        self,
        kind: EventKind,
        *,
        node: str | None = None,
        step: str | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> Event:
        """Durably append one record and return it.

        ``detail`` is an explicit mapping rather than ``**kwargs`` so that a payload
        key can never collide with this method's own ``kind``/``node``/``step``
        parameters — a lifecycle step legitimately has a ``kind`` of its own.
        """
        if kind not in EVENT_KINDS:
            raise JournalError(f"unknown journal event kind: {kind!r}")
        with advisory_lock(self.lock_identity):
            self.seq += 1
            event = Event(
                kind=kind,
                run_id=self.run_id,
                round=self.round,
                seq=self.seq,
                at=time.time(),
                node=node,
                step=step,
                detail=dict(detail or {}),
            )
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event.to_record(), sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return event

    def events(self) -> list[Event]:
        return read_events(self.path)


def open_journal(run_dir: Path, run_id: str, round_number: int) -> Journal:
    """Open (creating if needed) a run's journal, reconciling any torn tail first.

    Sequence numbers continue from the intact records already on disk, so a
    resumed run keeps one monotonic sequence across rounds.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / JOURNAL_NAME
    journal = Journal(path=path, run_id=run_id, round=round_number)
    with advisory_lock(journal.lock_identity):
        intact = reconcile(path)
    journal.seq = intact
    return journal


@dataclass
class NullJournal:
    """Journal-shaped no-op for an unrecorded round (``run-plan --no-record``)."""

    def append(
        self,
        kind: EventKind,
        *,
        node: str | None = None,
        step: str | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        return None
