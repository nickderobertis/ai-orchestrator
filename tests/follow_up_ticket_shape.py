"""The structure a sound test ticket's body carries, stated once for every suite.

`orchestrator/follow_up_tickets.py` refuses a current ticket whose `## Impact` prose is not its
labelled parts, whose `## Suggested fix` is not an opening paragraph, its contributing
locations and its unit subsections, whose `## Root cause` is not plain prose naming no code,
or which does not close with `## Duplicate search` and then `## Evidence`, so every suite that
writes a sound ticket writes them from here rather than restating them, and a part renamed in
the module renames them all. The older shapes a ticket on the board may still carry are
written from here too, by the functions that take a current body back to them.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from orchestrator import follow_up_tickets as tickets

#: What the workaround costs, by part, for a ticket whose workaround is acceptable.
WORKAROUND_COSTS = (
    "a reader adds one request per listing.",
    "none; the listing is complete.",
    "free, because the failure names the workaround.",
)

#: What a current ticket's product owner's part says: the impact itself, with no label.
OWNER_COST = "One capability fails in some situations: a reader misses a page."
#: What a ticket before schema 11 said there, opening on the area that decides.
PRIOR_OWNER_COST = "The users area decides: one capability fails in some situations."


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
    parts = ("Readers of the listing, and the export built on it.", outcome, OWNER_COST, cost)
    return "\n".join(
        f"**{label}.** {text}" if label != tickets.WORKAROUND_COST else f"**{label}.**\n{text}"
        for label, text in zip(tickets.IMPACT_PARTS, parts, strict=True)
    )


#: The one location the sound ticket's root cause has, as a contributing-location bullet.
LOCATION = "- some-service `src/cursor.py`: stops one page early when the count changes."
#: A schema-11 `## Suggested fix`'s contributing-locations part, as the fix carries it.
LOCATIONS = f"**{tickets.CONTRIBUTING_LOCATIONS}.**\n{LOCATION}"

#: A `## Suggested fix` section's content before schema 11: its opening paragraph, then one
#: unit subsection.
PRIOR_FIX = (
    "Page the listing by cursor, so no page is skipped when the count changes.\n\n"
    "### Listing — `some-service` (`src/cursor.py`)\n\n"
    "`list` pages by the cursor the previous page returned."
)
#: A current `## Suggested fix`: its opening paragraph, its contributing locations, its unit.
FIX = PRIOR_FIX.replace("\n\n### ", f"\n\n{LOCATIONS}\n\n### ", 1)

#: A schema-11 `## Root cause` section's content: the broken guarantee, in the product's terms.
ROOT_CAUSE = "A listing returns every page, however its count changes while it is read."

#: A schema-10 `## Root cause` section's content: its invariant, then one contributing location.
SCHEMA_10_ROOT_CAUSE = (
    f"**{tickets.ROOT_CAUSE_PARTS[0]}.** {ROOT_CAUSE}\n\n"
    f"**{tickets.ROOT_CAUSE_PARTS[1]}.**\n{LOCATION}"
)

#: A `## Duplicate search` section's content: the queries asked, and no near-match.
DUPLICATE_SEARCH = "Queries: `listing skips its last page`, `cursor`.\nnone"


def current_section(heading: str, otherwise: str) -> str:
    """What a sound current ticket carries under ``heading``, else ``otherwise``.

    The sections the current schema holds to a shape of their own are written from here;
    every other heading carries what its suite chose.
    """
    match heading:
        case tickets.ROOT_CAUSE:
            return ROOT_CAUSE
        case tickets.SUGGESTED_FIX:
            return FIX
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
OLDER_ROOT_CAUSE = _plain(SCHEMA_10_ROOT_CAUSE)

#: A level-2 section of a body: its heading and content, up to the next one or the end.
_SECTION = r"\n*## {heading}\n\n.*?(?=\n\n## |\Z)"
#: Where one level-2 section of a body ends and the next begins.
_BREAK = "\n\n## "
#: A contributing-locations part inside a section, up to the blank line after its bullets.
_LOCATIONS = re.compile(rf"\*\*{re.escape(tickets.CONTRIBUTING_LOCATIONS)}\.\*\*\n(?:- [^\n]*\n?)+")


@dataclass
class Section:
    """One level-2 section of a body: its heading, and its content, which a transform edits."""

    heading: str
    content: str


def _split(body: str) -> list[Section]:
    """``body``'s level-2 sections, in order."""
    parts = body.removeprefix("## ").split(_BREAK)
    return [
        Section(*part.split("\n\n", 1)) if "\n\n" in part else Section(part, "") for part in parts
    ]


def _join(sections: list[Section]) -> str:
    return "## " + _BREAK.join(f"{section.heading}\n\n{section.content}" for section in sections)


def before_schema_11(body: str) -> str:
    """A current ``body`` as a schema-10 ticket carried it.

    Its `## Root cause` is its invariant — the plain guarantee it states — then the locations
    its `## Suggested fix` lists, left as it is where it is already labelled; its fix carries
    no contributing locations, its product owner's part opens on the deciding area, and
    `## Evidence` sits after `## Examples`.
    """
    sections = _split(body)
    fixes = [section for section in sections if section.heading == tickets.SUGGESTED_FIX]
    listed = _LOCATIONS.search(fixes[0].content) if fixes else None
    bullets = listed[0].split("\n", 1)[1].strip() if listed else LOCATION
    for section in sections:
        if section.heading == tickets.ROOT_CAUSE and "**" not in section.content:
            section.content = (
                f"**{tickets.ROOT_CAUSE_PARTS[0]}.** {section.content}\n\n"
                f"**{tickets.ROOT_CAUSE_PARTS[1]}.**\n{bullets}"
            )
        if section.heading == tickets.SUGGESTED_FIX:
            section.content = _LOCATIONS.sub("", section.content).replace("\n\n\n", "\n\n")
        section.content = section.content.replace(OWNER_COST, PRIOR_OWNER_COST)
    evidence = [section for section in sections if section.heading == tickets.EVIDENCE]
    held = [section for section in sections if section.heading != tickets.EVIDENCE]
    at = next(at for at, section in enumerate(held) if section.heading == "Examples") + 1
    return _join(held[:at] + evidence + held[at:])


def before_schema_10(body: str) -> str:
    """A current ``body`` as a ticket recorded before schema 10 carried it.

    Its `## Root cause` is one plain explanation naming where the cause lives — left as it is
    where it already is one — and it carries neither `## Related tickets` nor
    `## Duplicate search`; everything else is as :func:`before_schema_11` leaves it.
    """
    body = before_schema_11(body)
    cause = re.search(rf"(?<=## {tickets.ROOT_CAUSE}\n\n).*?(?=\n\n## |\Z)", body, re.DOTALL)
    assert cause is not None, body
    plain = _plain(cause[0]) if f"**{tickets.ROOT_CAUSE_PARTS[1]}.**" in cause[0] else cause[0]
    held = body[: cause.start()] + plain + body[cause.end() :]
    for heading in (tickets.RELATED_TICKETS, tickets.DUPLICATE_SEARCH):
        held = re.sub(_SECTION.format(heading=heading), "", held, flags=re.DOTALL)
    return held


def after_schema_10(body: str) -> str:
    """A schema-10 ``body`` brought to schema 11, as its run does when it rewrites it.

    Its `## Root cause` becomes the plain guarantee, its locations move to the contributing
    locations of its `## Suggested fix`, its product owner's part loses its label, and
    `## Evidence` moves last.
    """
    sections = _split(body)
    for section in sections:
        if section.heading == tickets.ROOT_CAUSE and section.content == SCHEMA_10_ROOT_CAUSE:
            section.content = ROOT_CAUSE
        if section.heading == tickets.SUGGESTED_FIX and section.content == PRIOR_FIX:
            section.content = FIX
        section.content = section.content.replace(PRIOR_OWNER_COST, OWNER_COST)
    evidence = [section for section in sections if section.heading == tickets.EVIDENCE]
    kept = [section for section in sections if section.heading != tickets.EVIDENCE]
    return _join(kept + evidence)


def _brought(text: str, forward: Callable[[str], str]) -> str:
    """A ticket file's ``text`` with its body, the last part of the file, passed ``forward``."""
    head, body = text.split("\n## ", 1)
    return f"{head}\n{forward(f'## {body}'.rstrip(chr(10)))}\n"


def brought_forward(text: str) -> str:
    """A ticket file's ``text`` with its pre-schema-10 body brought to the current schema.

    Its `## Root cause` becomes the plain guarantee, it gains `## Duplicate search`, and its
    body is then brought from schema 10 by :func:`after_schema_10`.
    """

    def forward(body: str) -> str:
        held = body.replace(OLDER_ROOT_CAUSE, SCHEMA_10_ROOT_CAUSE)
        return after_schema_10(f"{held}\n\n## {tickets.DUPLICATE_SEARCH}\n\n{DUPLICATE_SEARCH}")

    return _brought(text, forward)


def brought_from_schema_10(text: str) -> str:
    """A ticket file's ``text`` with its schema-10 body brought to schema 11."""
    return _brought(text, after_schema_10)
