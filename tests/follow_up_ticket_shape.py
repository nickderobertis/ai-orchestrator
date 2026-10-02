"""The schema-8 structure a sound test ticket's body carries, stated once for every suite.

`orchestrator/follow_up_tickets.py` refuses a current ticket whose `## Impact` prose is not its
labelled parts and whose `## Suggested fix` is not an opening paragraph and its unit
subsections, so every suite that writes a sound ticket writes them from here rather than
restating them, and a part renamed in the module renames them all.
"""

from __future__ import annotations

from orchestrator import follow_up_tickets as tickets

#: What the workaround costs, by part, for a ticket whose workaround is acceptable.
WORKAROUND_COSTS = (
    "a reader adds one request per listing.",
    "none; the listing is complete.",
    "free, because the failure names the workaround.",
)


def impact_prose(outcome: str, *, workaround: str = "a workaround") -> str:
    """``outcome`` as a schema-8 section's labelled parts, for a ticket with ``workaround``.

    A workaround of `none` says in its last part that there is no acceptable workaround, in
    place of the three cost bullets; any other carries those bullets.
    """
    if workaround == tickets.NO_WORKAROUND:
        cost = f"There is {tickets.NO_ACCEPTABLE_WORKAROUND}: every one costs a person's time."
    else:
        cost = "\n".join(
            f"- {label}: {value}"
            for label, value in zip(tickets.WORKAROUND_COSTS, WORKAROUND_COSTS, strict=True)
        )
    parts = (
        "Readers of the listing, and the export built on it.",
        outcome,
        "The users area decides: one capability fails in some situations.",
        cost,
    )
    return "\n".join(
        f"**{label}.** {text}" if label != tickets.WORKAROUND_COST else f"**{label}.**\n{text}"
        for label, text in zip(tickets.IMPACT_PARTS, parts, strict=True)
    )


#: A `## Suggested fix` section's content: its opening paragraph, then one unit subsection.
FIX = (
    "Page the listing by cursor, so no page is skipped when the count changes.\n\n"
    "### Listing — `some-service` (`src/cursor.py`)\n\n"
    "`list` pages by the cursor the previous page returned."
)
