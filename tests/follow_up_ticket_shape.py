"""The structure a sound test ticket's body carries, stated once for every suite.

`orchestrator/follow_up_tickets.py` refuses a current ticket whose `## Impact` prose is not its
labelled parts, whose `## Suggested fix` is not an opening paragraph and its unit
subsections, whose `## Root cause` is not its invariant and contributing locations, or which
does not close with `## Duplicate search`, so every suite that writes a sound ticket writes
them from here rather than restating them, and a part renamed in the module renames them all.
"""

from __future__ import annotations

import re

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


#: A schema-10 `## Root cause` section's content: its invariant, then one contributing location.
ROOT_CAUSE = (
    f"**{tickets.ROOT_CAUSE_PARTS[0]}.** A listing returns every page, however its count "
    "changes while it is read.\n\n"
    f"**{tickets.ROOT_CAUSE_PARTS[1]}.**\n"
    "- some-service `src/cursor.py`: stops one page early when the count changes."
)

#: A schema-10 `## Duplicate search` section's content: the queries asked, and no near-match.
DUPLICATE_SEARCH = "Queries: `listing skips its last page`, `cursor`.\nnone"


def schema_10_section(heading: str, otherwise: str) -> str:
    """What a sound current ticket carries under ``heading``, else ``otherwise``.

    The two sections schema 10 holds to a shape of their own are written from here; every
    other heading carries what its suite chose.
    """
    match heading:
        case tickets.ROOT_CAUSE:
            return ROOT_CAUSE
        case tickets.DUPLICATE_SEARCH:
            return DUPLICATE_SEARCH
        case _:
            return otherwise


def _plain(root_cause: str) -> str:
    """A schema-10 `## Root cause` as one plain explanation: its invariant, then where it lives."""
    invariant, locations = root_cause.split(f"**{tickets.ROOT_CAUSE_PARTS[1]}.**", 1)
    stated = invariant.replace(f"**{tickets.ROOT_CAUSE_PARTS[0]}.**", "").strip()
    places = [line.removeprefix("- ").strip() for line in locations.strip().splitlines()]
    return " ".join((stated, *places))


#: What a ticket recorded before schema 10 said under `## Root cause`: one plain explanation.
OLDER_ROOT_CAUSE = _plain(ROOT_CAUSE)

#: A level-2 section of a body: its heading and content, up to the next one or the end.
_SECTION = r"\n*## {heading}\n\n.*?(?=\n\n## |\Z)"


def before_schema_10(body: str) -> str:
    """``body`` as a ticket recorded before schema 10 carried it.

    Its `## Root cause` is one plain explanation naming where the cause lives — left as it is
    where it already is one — and it carries
    neither `## Related tickets` nor `## Duplicate search`; everything else is as it stands.
    """
    cause = re.search(rf"(?<=## {tickets.ROOT_CAUSE}\n\n).*?(?=\n\n## |\Z)", body, re.DOTALL)
    assert cause is not None, body
    plain = _plain(cause[0]) if f"**{tickets.ROOT_CAUSE_PARTS[1]}.**" in cause[0] else cause[0]
    held = body[: cause.start()] + plain + body[cause.end() :]
    for heading in (tickets.RELATED_TICKETS, tickets.DUPLICATE_SEARCH):
        held = re.sub(_SECTION.format(heading=heading), "", held, flags=re.DOTALL)
    return held


def brought_to_schema_10(text: str) -> str:
    """A ticket file's ``text`` with its pre-schema-10 body brought forward, as its run does.

    Its `## Root cause` becomes the invariant and its contributing locations, and it closes
    with `## Duplicate search`; the body is the last part of the file, so it is appended.
    """
    held = text.replace(OLDER_ROOT_CAUSE, ROOT_CAUSE).rstrip("\n")
    return f"{held}\n\n## {tickets.DUPLICATE_SEARCH}\n\n{DUPLICATE_SEARCH}\n"
