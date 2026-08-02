"""How a journalled span becomes the share the telemetry index reports.

`orchestrator.telemetry` divides a run's wall clock into *disjoint* categories, so a
journalled total that no longer fits is clipped to whatever the categories ahead of
it left — to zero on a run whose model, tool, gate, and lock time already accounted
for every millisecond it had. Concurrency alone is enough to produce that: several
nodes each journal their own real seconds against one shared wall clock.

Zero is therefore a legitimate measurement, which makes "this category is positive"
an assertion about how loaded the box was rather than about the code. The contract
those assertions mean to hold is the one here: the spans a run journalled reach the
record, clipped exactly where the model says they are.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal, TypedDict

from orchestrator.journal import Detail, EventKind
from orchestrator.telemetry import TimingRecord

ClippedCategory = Literal[
    "gate_seconds",
    "lock_wait_seconds",
    "setup_seconds",
    "scheduling_seconds",
    "publication_wait_seconds",
]

#: The order in which `orchestrator.telemetry._timing` hands out the wall clock left
#: over after model and tool time, restated here because that function spells it as
#: straight-line code rather than data. `tests/test_telemetry.py`'s
#: `test_the_clipping_order_the_contract_helper_states_is_the_one_timing_uses` is the
#: drift gate that holds the two together.
WATERFALL: tuple[ClippedCategory, ...] = (
    "gate_seconds",
    "lock_wait_seconds",
    "setup_seconds",
    "scheduling_seconds",
    "publication_wait_seconds",
)


class JournalledEvent(TypedDict):
    """One journal record as a reader gets it back off disk."""

    kind: EventKind
    detail: Detail


def journalled_seconds(events: Iterable[JournalledEvent], kind: EventKind) -> float:
    """Total `seconds` a run journalled under `kind`, read as telemetry reads them."""
    total = 0.0
    for event in events:
        if event["kind"] != kind:
            continue
        value = event["detail"].get("seconds")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            total += float(value)
    return total


def clipped_share_seconds(
    timing: TimingRecord, category: ClippedCategory, journalled: float
) -> float:
    """What `timing` owes `category`: its journalled total, clipped by the wall clock left.

    The wall clock still unspent when `category`'s turn came is what the categories
    behind it, plus the idle remainder, ended up holding — so the budget is read back
    out of the record's own decomposition rather than by restating which categories
    came first. A run that had the room reports the whole journalled total; one that
    did not reports exactly the remainder. A span dropped between the journal and the
    index reads as zero against a budget that went to idle instead, and still fails.
    """
    budget = timing["idle_orchestration_ms"] + sum(
        round(timing[later] * 1000) for later in WATERFALL[WATERFALL.index(category) :]
    )
    return min(budget, round(journalled * 1000)) / 1000
