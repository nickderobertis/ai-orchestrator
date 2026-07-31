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

from collections.abc import Iterable, Mapping
from typing import Any

#: The order in which `orchestrator.telemetry._timing` hands out the wall clock left
#: over after model and tool time. A category is clipped by everything before it.
WATERFALL = (
    "gate_seconds",
    "lock_wait_seconds",
    "setup_seconds",
    "scheduling_seconds",
    "publication_wait_seconds",
)


def journalled_seconds(events: Iterable[Mapping[str, Any]], kind: str) -> float:
    """Total `seconds` a run journalled under `kind`, read as telemetry reads them."""
    total = 0.0
    for event in events:
        if event.get("kind") != kind:
            continue
        value = event.get("detail", {}).get("seconds")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            total += float(value)
    return total


def clipped_share_seconds(timing: Mapping[str, Any], category: str, journalled: float) -> float:
    """What `timing` owes `category`: its journalled total, clipped by the wall clock left.

    Derived from the record's own published numbers, so it holds on any host: a run
    that had the room reports the whole journalled total, and one that did not reports
    exactly the remainder. A category dropped on the way from the journal to the index
    still reads as zero against a positive remainder, and still fails.
    """
    if category not in WATERFALL:
        raise ValueError(f"{category} is not one of the clipped categories {WATERFALL}")
    budget = int(timing["wall_ms"]) - sum(
        int(timing[measured])
        for measured in ("agent_model_ms", "judge_model_ms", "llmlint_model_ms", "tool_ms")
    )
    for ahead in WATERFALL:
        if ahead == category:
            break
        budget -= round(float(timing[ahead]) * 1000)
    return min(max(0, budget), round(journalled * 1000)) / 1000
