"""The one source of a verified follow-up ticket's shape, and of who owns what on the board.

A run's **drafts** are unverified (`orchestrator/follow_up_drafts.py`). After the run, `just
follow-ups <run-id>` dispatches a follow-up agent that verifies each draft against the trees
it names, groups what stands by root cause, and writes one **ticket** per root cause into the
same `drafts` source — `tasks/<run-id>/tickets/<root-cause>.md` — before copying it onto the
`followups` board, where every session's tickets accumulate. Two stored shapes cross that
seam and both outlive the agent that wrote them, so both are decided here and nowhere else:

* **the ticket** (contract C5): its path, its front matter, its body headings, the board
  item it is bound to, and this module's `copy` — the store's `task copy`, held to that
  binding — that is the only way it reaches the board;
* **ownership on the board** (contract C6): an issue belongs to the run its ticket's
  `created_by_run` names, a comment — evidence or a reply to a person's comment — to the run
  its last-line marker names, and a follow-up run changes nothing that belongs to another run;
* **how a ticket depends on an accepted ticket**: a proposal written as if an accepted
  ticket's fix were already in depends on that ticket as the store's own `depends_on` edge
  — :data:`DEPENDENCY_FIELD`, outside the record, which the board carries natively and
  `task deps` walks from either end — and says in its text where and how, by the accepted
  item's URL. The edge is the one record of the dependency: no record key duplicates it.

**Three readers, one shape.** `scripts/follow-ups.sh` counts a run's drafts and tickets,
composes the agent's task — rendering both contracts into it from :func:`ticket_contract`
and :func:`comment_contract` rather than restating them — and names every ticket that fails
the shape once an attached run settles. The agent validates each ticket it writes through
this module's `validate` command, and decides the status it carries through its
`board-status` command, before copying it. And the journeys read the board back through
:func:`from_store_item` and :func:`comment_owner`.

**The board is the record of the user's decision, and a ticket names its machine.** A ticket
reaches the board as a proposal and a person accepts it there, so the status a copy writes
is read off the board item first (:func:`status_before_copy`) rather than carried over from
the ticket. Evidence is a claim about trees read on one machine, so the record's `host` and
its `## Evidence` section both name the host the verification ran on.

**A ticket is read through the store, never parsed here.** An agent writes the front matter
in whatever YAML style it likes, and what reaches the board is what the installed
`onetaskgraph` reads out of that file — so the store's own reading is the one worth
validating. It is asked with the environment the launch exported, because the `drafts`
source's root is composed by `scripts/follow-up-env.sh` alone.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, NamedTuple, NewType, NoReturn

from onetaskgraph_sdk import CopyReport

# The SDK's `__all__` exports the `QueryResponseOf…` schema roots but not their item models,
# so `QualifiedTask` has no public name; the follow-up "Export the SDK's query item models
# (QualifiedTask et al.) beside the QueryResponseOf… schema roots" retires this import.
from onetaskgraph_sdk._generated.query_response_of_qualified_task import QualifiedTask

from orchestrator import follow_up_drafts as drafts
from orchestrator import plan_store
from orchestrator.plan_store import RECORD_COMPONENT
from orchestrator.project_store import TASKS_DIRECTORY, frontmatter, hosted_origin

#: The plan-store source a ticket is stored in, and the directory beside a run's drafts it
#: is stored under — both the drafts module's, since a ticket sits in the run's draft tree.
SOURCE = drafts.SOURCE
TICKETS = drafts.TICKETS

#: The board a ticket is copied onto when a caller names none, as `onetaskgraph.yaml` names
#: it. `tests/test_plan_source_roots.py` holds the source's five values.
BOARD = "followups"

#: The version of the record below. A reader refuses any other: a ticket is a stored shape
#: that outlives the agent that wrote it. Schema 2 added `host` and the proposal statuses;
#: schema 3 added `repositories`, which files a ticket's issue in its root cause's repository;
#: schema 4 added the body's `## Impact` section; schema 5 removed `## Repository`, which the
#: record's `repository` already says, and replaced `## Suggested fixes` with `## Suggested fix`,
#: stating the one fix, beside an optional `## Rejected fixes` for the others considered.
#: A dependency on an accepted ticket moved no schema: it lives in the store's own
#: :data:`DEPENDENCY_FIELD`, outside this record, so no ticket on the board is of an older
#: shape for it and there is nothing to bring forward. Schema 6 added the optional
#: :data:`BINDING_FIELD`, the board item a ticket corresponds to.
SCHEMA = 6

#: The metadata key a ticket's record sits under, which travels onto the board item.
KEY = "orchestrator.follow-up"


class Status(StrEnum):
    """A ticket's status, as the plan store's category of its board item.

    **The board is the record of the user's decision.** A ticket reaches it as a proposal,
    and a person moving the item to `todo` is what accepts it, so a later agent selects
    accepted tickets by this category alone. A person may instead defer it, which the store
    word `draft` carries: the `followups` source maps `backlog` to the board's `Proposal`
    option and `draft` to its `Deferred` option (`onetaskgraph.yaml`). A withdrawn ticket's
    issue is closed as not planned, and no issue is ever deleted. :func:`status_vocabulary`
    is the one statement of the whole vocabulary an agent reads.

    `queued` is the one category no person moves an item to. A plan node naming the ticket
    in its own `delivers` claims it the moment the launch's first projection lands, and the
    store's relation carries it on from there — so a manager reading the board sees an
    accepted ticket another run has already taken, rather than picking up work in flight.

    A ticket's `status` is written as the category's own word, for every category: the
    adopted plan-store release reads each of them back as itself, so there is no status
    this repository has to spell some other way to be read as the one it means.
    `tests/test_follow_up_tickets.py` reads every word back through the installed store.
    """

    PROPOSED = "backlog"
    ACCEPTED = "todo"
    DEFERRED = "draft"
    # The one member named for its store word rather than for what it means, because the
    # category, the board option and the engine's projected word are all `queued` and the
    # releases this host adopted fix that name. What it means is the meaning below.
    QUEUED = "queued"
    UNDER_WAY = "in-progress"
    FINISHED = "done"
    WITHDRAWN = "cancelled"

    @property
    def meaning(self) -> str:
        """What this status says about a ticket."""
        return _MEANINGS[self]

    @property
    def accepted(self) -> bool:
        """Whether a person accepted the ticket.

        A person accepted every one of these, and only a person moves an item back out of
        acceptance — with the one exception `queued` is: the store's `delivers` relation
        returns a claimed ticket to `todo` when the work that claimed it does not happen,
        which restores the person's decision rather than undoing it.
        """
        return self in (Status.ACCEPTED, Status.QUEUED, Status.UNDER_WAY, Status.FINISHED)

    @property
    def protected_from_withdrawal(self) -> bool:
        """Whether no run withdraws an item at this status, because a person decided on it.

        A person accepted every accepted ticket, whoever moved it on from `todo` afterwards,
        and deferring a ticket is the other such decision: a deferred ticket is not accepted,
        and it is protected from withdrawal all the same.
        """
        return self.accepted or self is Status.DEFERRED

    @property
    def selected(self) -> bool:
        """Whether an agent sent to pick up accepted tickets selects an item at this status.

        Narrower than :attr:`accepted`: a ticket under way or finished was accepted, and is
        not waiting for anybody to pick it up.
        """
        return self is Status.ACCEPTED


_MEANINGS = {
    Status.PROPOSED: "a proposal awaiting the user's decision",
    Status.ACCEPTED: "accepted, and not yet taken up",
    Status.DEFERRED: (
        "deferred for later by a person: not accepted, picked up by no agent, and still "
        "taking new evidence"
    ),
    Status.QUEUED: (
        "accepted, and claimed by a launched DAG whose node has not started, so it returns "
        "to `Todo` if that work does not happen"
    ),
    Status.UNDER_WAY: "accepted and taken up",
    Status.FINISHED: "accepted and finished",
    Status.WITHDRAWN: "withdrawn",
}


class _Place(NamedTuple):
    """Where the board shows a status, and who moves an item there."""

    shown: str
    mover: str


_PLACES = {
    Status.PROPOSED: _Place(
        "Board status `Proposal`", "a follow-up run's first copy of a new ticket"
    ),
    Status.ACCEPTED: _Place(
        "Board status `Todo`", "only a person, which is what accepting a ticket is"
    ),
    Status.DEFERRED: _Place("Board status `Deferred`", "only a person"),
    Status.QUEUED: _Place(
        "Board status `Queued`",
        "no person — a launched run's first projection, over the store's `delivers` relation",
    ),
    Status.UNDER_WAY: _Place(
        "Board status `In Progress`", "a person, or a dispatch whose own task says to"
    ),
    Status.FINISHED: _Place(
        "Closed as completed at Status `Done`", "a person, or a dispatch whose own task says to"
    ),
    Status.WITHDRAWN: _Place(
        "Closed as not planned at Status `Cancelled`",
        "a follow-up run withdrawing its own ticket that nobody accepted or deferred, or a person",
    ),
}


def status_vocabulary() -> str:
    """What each board status means, as every agent and document that describes it reads it.

    One bullet per :class:`Status`: where the board shows it, the store word a ticket is
    written with, what it means, who moves an item there, and whether an agent sent to pick
    up accepted tickets selects it — closing on what such a brief means.
    """
    bullets = []
    for status in Status:
        place = _PLACES[status]
        selection = (
            "**Selected** by an agent sent to pick up accepted tickets, the only status that is"
            if status.selected
            else "Not selected by an agent sent to pick up accepted tickets"
        )
        bullets.append(
            f"- **{place.shown}**, written `{status.value}`: {status.meaning}. Who moves an "
            f"item there: {place.mover}. {selection}.\n"
        )
    return (
        "".join(bullets)
        + '\nA brief to pick up "accepted" follow-up tickets means the items at `Todo` and '
        "nothing else: never an item at `Proposal`, `Deferred`, `Queued` or `In Progress`, "
        "and never a closed one.\n"
    )


def board_option(status: Status) -> str:
    """The board's own option for ``status``, as the vocabulary shows it."""
    matched = re.search(r"`([^`]+)`", _PLACES[status].shown)
    assert matched is not None, status  # noqa: S101 - every place above names its option
    return matched[1]


def accepted_statuses() -> str:
    """The accepted statuses as prose, each as its board option and store word."""
    named = [f"`{board_option(status)}` (`{status.value}`)" for status in Status if status.accepted]
    return ", ".join(named[:-1]) + f" and {named[-1]}"


def accepted_filter() -> str:
    """The `task list` flags selecting the accepted statuses; the flag repeats, one per status."""
    return " ".join(f"--status {status.value}" for status in Status if status.accepted)


#: The identities a ticket names, each a type of its own so that one cannot be passed where
#: another is meant: the run a ticket or comment belongs to, the root cause's slug, the
#: normalized origin a root cause lives in, a commit, a qualified draft id, a qualified id of
#: a board item, and an RFC 3339 UTC time.
RunId = NewType("RunId", str)
RootCause = NewType("RootCause", str)
Origin = NewType("Origin", str)
Commit = NewType("Commit", str)
QualifiedDraftId = NewType("QualifiedDraftId", str)
QualifiedBoardId = NewType("QualifiedBoardId", str)
BoardItemId = NewType("BoardItemId", str)
Timestamp = NewType("Timestamp", str)
Host = NewType("Host", str)


class Basis(NamedTuple):
    """One repository a ticket's claims were verified in, and the commit they were verified at."""

    origin: Origin
    commit: Commit


#: How long a ticket's title may be.
TITLE_LIMIT = 120

#: The keys of a ticket's record, every one present, in the order they are written.
RECORD_KEYS = (
    "schema",
    "root_cause",
    "repository",
    "created_by_run",
    "owning_runs",
    "drafts",
    "basis",
    "verified_at",
    "host",
)

#: **Which board item a ticket corresponds to.** The one optional key of the record: the
#: native id of the board item the ticket is copied onto, as the store reports it after the
#: `<board>:`. `board-status` and `copy` write it — when the run creates its item or first
#: reaches it, and again, naming the run's own open item, when two board items carry the
#: ticket's origin — and nothing else does. The store's own correspondence cannot be trusted
#: alone: a timed-out copy can leave two items carrying one `onetaskgraph.origin`, and its
#: search by origin then updates whichever it lists first, a withdrawn duplicate included,
#: while the live issue keeps the old body. Beside it the same writes put the store's
#: :data:`ORIGIN_KEY` naming `<board>:<id>` into the ticket's metadata, derived from this key
#: by :func:`render` and never held on its own: an item's origin naming the destination is
#: the store's first rule, so every later copy reaches the bound item directly. Both commands
#: refuse, naming both ids, when the store reports any other destination.
BINDING_FIELD = "board_item"
#: The store's reserved key a copy records the id it was copied from under, and which it
#: follows directly when it names the destination; the plan-store client's one spelling.
ORIGIN_KEY = plan_store.ORIGIN_KEY

#: **How a ticket depends on an accepted ticket.** The store's own top-level front-matter
#: field a dependency is written in — one entry per accepted ticket whose fix changed the
#: ticket, as `{id: <board>:<native id>, item: task}` and nothing else, the `kind` left to
#: its default. The store rewrites the entry to the board's own item on copy (on the
#: `followups` GitHub Projects board, GitHub's native issue dependency, which consults no
#: `kind`, so only the blocking kind is admitted here) and `task deps` walks it from either
#: end. It is read back only through that walk — `task show` carries no `depends_on` — never
#: by parsing the front matter, and it sits outside the `orchestrator.follow-up` record: no
#: record key duplicates it. `tests/test_follow_up_ticket_docs.py` admits the name in the
#: documents through this constant.
DEPENDENCY_FIELD = "depends_on"
#: What each end of such an edge is, and what the edge means, as the store reports them.
DEPENDENCY_ITEM = "task"
DEPENDENCY_KIND = "blocks"
#: A far end as the store addresses it: a non-empty source, a colon, a non-empty native id.
QUALIFIED_ID = re.compile(r"(?P<source>[^:\s]+):(?P<native>\S+)")
#: The `url` the board reports for an accepted item, as a ticket's text links it: a web URL.
ITEM_URL = re.compile(r"https?://\S+")

#: The level-2 headings a ticket's body carries, in this order, each with content.
HEADINGS = (
    "Root cause",
    "Impact",
    "Examples",
    "Evidence",
    "Suggested fix",
    "Owning runs",
)

#: The heading whose section states the one fix a ticket recommends.
SUGGESTED_FIX = "Suggested fix"

#: The one optional heading: at most once, directly after :data:`SUGGESTED_FIX`, with content.
REJECTED_FIXES = "Rejected fixes"

#: Headings an older schema carried, which a current body is refused for carrying.
RETIRED_HEADINGS = ("Repository", "Suggested fixes")
#: The schema that retired them.
RETIRED_AT = 5

#: The heading whose section names the host the verification ran on.
EVIDENCE = "Evidence"

#: The heading whose section states what the root cause costs when it fires: prose, then
#: exactly one of each line below.
IMPACT = "Impact"


class Severity(StrEnum):
    """How bad a root cause's impact is, most severe first.

    Repository-neutral, because a ticket may be filed against any repository: each meaning
    speaks of what the affected repository's users and artifacts suffer.
    """

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def meaning(self) -> str:
        """What this severity says about the impact."""
        return _SEVERITY_MEANINGS[self]

    def above(self, other: Severity) -> bool:
        """Whether this severity is more severe than ``other``."""
        order = tuple(Severity)
        return order.index(self) < order.index(other)


_SEVERITY_MEANINGS = {
    Severity.CRITICAL: (
        "data or work is lost or corrupted, or the affected thing cannot be used at all"
    ),
    Severity.HIGH: (
        "the affected thing fails, or a person must intervene, every time the root cause fires"
    ),
    Severity.MEDIUM: "it costs time, resources or quality, but recovers without a person",
    Severity.LOW: "cosmetic, or rare with negligible cost",
}

#: The three lines an `## Impact` section carries after its prose, in this order, and the
#: workaround that says there is none.
SEVERITY_LINE = "Severity"
WORKAROUND_LINE = "Workaround"
MITIGATED_LINE = "Severity with the workaround"
IMPACT_LINES = (SEVERITY_LINE, WORKAROUND_LINE, MITIGATED_LINE)
NO_WORKAROUND = "none"
IMPACT_LINE = re.compile(r"^- (?P<label>[^:\n]+):(?P<value>[^\n]*)$", re.MULTILINE)
#: What a section holds from its first line on: the three lines in order, and nothing else.
IMPACT_TAIL = re.compile(
    "\n".join(rf"- {re.escape(label)}:[^\n]*" for label in IMPACT_LINES) + r"\s*"
)


def impact_section(prose: str, severity: str, workaround: str, with_workaround: str) -> str:
    """The content of an `## Impact` section: ``prose``, then its three lines.

    The severities are words rather than :class:`Severity` members, so the example ticket
    :func:`ticket_contract` renders can put a placeholder where each goes.
    """
    values = (severity, workaround, with_workaround)
    lines = "".join(
        f"- {label}: {value}\n" for label, value in zip(IMPACT_LINES, values, strict=True)
    )
    return f"{prose.strip()}\n\n{lines}"


#: A root cause's slug, which names the ticket's file.
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

#: A commit a claim was verified at.
COMMIT = re.compile(r"[0-9a-f]{40}")

#: The host a ticket's verification ran on, as `hostname` prints it: at most this many
#: characters, of dot-separated labels, each 1–63 ASCII letters, digits and `-`, neither
#: starting nor ending with `-`. Its shape is checked, never its value.
HOST_LIMIT = 253
HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")

#: A qualified draft id a ticket consumed.
DRAFT_ID = re.compile(rf"{re.escape(SOURCE)}:(?P<run>[^/\s]+)/{re.escape(drafts.DRAFTS)}/[^/\s]+")

#: The last line of an evidence comment a follow-up run owns, and the visible line it opens
#: with — byte for byte what every comment already on the board carries.
COMMENT_MARKER = '<!-- orchestrator:follow-up-comment run="{run}" root_cause="{root_cause}" -->'
COMMENT_OPENING = "Additional evidence from follow-up run `{run}`."

#: The last line of a reply a follow-up run owns, naming the one comment it answers, and the
#: visible line it opens with when the board reports the answered comment's author, or not.
REPLY_MARKER = (
    '<!-- orchestrator:follow-up-comment run="{run}" root_cause="{root_cause}"'
    ' kind="{kind}" answers="{answers}" -->'
)
REPLY_OPENING = "Reply from follow-up run `{run}` to @{author}'s comment: {url}"
REPLY_OPENING_UNATTRIBUTED = "Reply from follow-up run `{run}` to the comment: {url}"

#: Either marker, as a last line: `kind` and `answers` are read, then held to the grammar
#: :func:`comment_owner` states.
COMMENT_MARKER_LINE = re.compile(
    r"<!-- orchestrator:follow-up-comment"
    r' run="(?P<run>[^"\s]+)" root_cause="(?P<root_cause>[^"\s]+)"'
    r'(?: kind="(?P<kind>[^"\s]*)")?(?: answers="(?P<answers>[^"]*)")? -->'
)

#: A comment's id as the board lists it: one or more characters, none of them whitespace,
#: `"` or `>`, so it cannot end the marker attribute or the marker that carries it.
COMMENT_ID = re.compile(r'[^\s">]+')

#: The suffix a ticket's file carries.
TICKET_SUFFIX = ".md"

#: The placeholders the task template is filled at: values it may name as often as its
#: prose needs them, and sections it names exactly once, since a section rendered twice is
#: two copies of a contract in one task.
PLACEHOLDER = re.compile(r"@([A-Z][A-Z_]*)@")
VALUES = (
    "RUN",
    "BOARD",
    "DRAFTS_ROOT",
    "VALIDATE",
    "BOARD_STATUS",
    "BOARD_ITEMS",
    "COPY",
    "CHECKOUT",
    "PLAN_STORE",
    "ACCEPTED_STATUSES",
    "ACCEPTED_FILTER",
)
SECTIONS = ("STATUS_VOCABULARY", "TICKET_CONTRACT", "COMMENT_CONTRACT", "REDISPATCH", "FEEDBACK")
PLACEHOLDERS = (*VALUES, *SECTIONS)

#: This command's name in its diagnostics.
PROG = "follow-up-tickets"

#: Exit statuses: every ticket is sound; a ticket failed the shape; the command could not run.
#: And four of `board-status`'s own, each a ticket not to copy: the board holds its item at a
#: category no ticket carries; a withdrawal a person's decision on the item refuses, whether
#: they accepted it or deferred it; a ticket whose repository is not one of the board's
#: owner, which nothing asks the board about; and a ticket depending on an item the board
#: does not hold as an accepted ticket of another root cause whose URL the ticket names.
#: And one `board-status` and `copy` share: the board item the ticket corresponds to cannot
#: be established as its binding, so nothing is copied.
SOUND = 0
UNSOUND = 1
UNRUNNABLE = 2
UNPLACED = 3
PROTECTED = 4
OUTSIDE_OWNER = 5
NOT_ACCEPTED = 6
MISBOUND = 7

#: The last line of the comment a run leaves on a withdrawn duplicate of its own item, naming
#: the item the ticket is bound to instead. It is no run's comment under :func:`comment_owner`,
#: so nothing edits it afterwards; it is read only to leave it once.
DUPLICATE_MARKER = '<!-- orchestrator:follow-up-duplicate run="{run}" survivor="{survivor}" -->'
DUPLICATE_OPENING = (
    "Withdrawn as a duplicate: this ticket's work continues on {survivor}, and follow-up "
    "run `{run}` copies it there from now on."
)

#: The host every repository a ticket's issue may be filed in lives on.
GITHUB = "github.com"

#: What a dry-run `onetaskgraph task copy` reports for a ticket the board holds no item for,
#: and each action it reports for one whose item the board already holds.
CREATED = "created"
EXISTING = ("updated", "unchanged")


class Refused(ValueError):
    """A ticket, or an input to one, without the shape this module states; says every reason."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = tuple(problems)


@dataclass(frozen=True)
class Ticket:
    """One verified follow-up ticket, every field validated.

    ``depends_on`` names every accepted ticket whose fix this one is written against, as
    the board addresses each, sorted; empty when no accepted fix changed the ticket.
    ``board_item`` is the :data:`BINDING_FIELD`, `None` until a command writes it.
    """

    title: str
    status: Status
    root_cause: RootCause
    repository: Origin
    created_by_run: RunId
    owning_runs: tuple[RunId, ...]
    drafts: tuple[QualifiedDraftId, ...]
    basis: tuple[Basis, ...]
    verified_at: Timestamp
    host: Host
    body: str
    depends_on: tuple[QualifiedBoardId, ...] = ()
    board_item: BoardItemId | None = None


class Edge(NamedTuple):
    """One dependency edge the store reports for a ticket, read from the ticket outward.

    ``to`` is the far end as the store addresses it, ``item`` what that end is, and ``kind``
    what the edge means — the three things :func:`edge_problems` holds to the contract.
    """

    to: str
    item: str
    kind: str


CommentId = NewType("CommentId", str)


class CommentKind(StrEnum):
    """What a follow-up run's comment is: its evidence on another run's issue, or a reply."""

    EVIDENCE = "evidence"
    REPLY = "reply"


class CommentOwner(NamedTuple):
    """What a follow-up run's comment marker names; ``answers`` is a reply's alone."""

    run: RunId
    root_cause: RootCause
    kind: CommentKind = CommentKind.EVIDENCE
    answers: CommentId | None = None


def qualified_id(run: str, root_cause: str) -> str:
    """The ticket of ``run`` for ``root_cause``, as the store addresses it."""
    return f"{SOURCE}:{run}/{TICKETS}/{root_cause}"


def ticket_path(root: Path, run: str, root_cause: str) -> Path:
    """Where that ticket's file is under a `drafts` root."""
    return root / TASKS_DIRECTORY / run / TICKETS / f"{root_cause}{TICKET_SUFFIX}"


def record(ticket: Ticket) -> dict[str, object]:
    """The metadata a ticket is stored under: every key present, the binding once it is written."""
    held: dict[str, object] = {
        "schema": SCHEMA,
        "root_cause": ticket.root_cause,
        "repository": ticket.repository,
        "created_by_run": ticket.created_by_run,
        "owning_runs": list(ticket.owning_runs),
        "drafts": list(ticket.drafts),
        "basis": {entry.origin: entry.commit for entry in ticket.basis},
        "verified_at": ticket.verified_at,
        "host": ticket.host,
    }
    if ticket.board_item is not None:
        held[BINDING_FIELD] = ticket.board_item
    return held


def dependency_entries(ticket: Ticket) -> list[dict[str, str]]:
    """The ticket's :data:`DEPENDENCY_FIELD` entries, one per accepted ticket it depends on."""
    return [{"id": held, "item": DEPENDENCY_ITEM} for held in ticket.depends_on]


def render(ticket: Ticket, *, board: str | None = None) -> str:
    """One ticket as the `local-md` record it is stored as.

    No `project`, and `repositories` naming exactly the record's `repository` — derived from
    it rather than held beside it, so nothing written here can make the two differ. The
    store's :data:`DEPENDENCY_FIELD` is written only when the ticket depends on something.
    With ``board``, a bound ticket also carries the store's :data:`ORIGIN_KEY` naming its
    bound item there, derived from the binding the same way.
    """
    fields: dict[str, object] = {
        "title": ticket.title,
        "status": ticket.status.value,
        "repositories": [ticket.repository],
    }
    if ticket.depends_on:
        fields[DEPENDENCY_FIELD] = dependency_entries(ticket)
    metadata: dict[str, object] = {}
    if board is not None and ticket.board_item is not None:
        metadata[ORIGIN_KEY] = f"{board}:{ticket.board_item}"
    metadata[KEY] = record(ticket)
    fields["metadata"] = metadata
    return frontmatter(fields, ticket.body)


def repository_name(origin: str) -> str:
    """The last segment of a normalized origin, which a ticket's title opens with."""
    return origin.rsplit("/", 1)[-1]


def _is_run(value: object) -> bool:
    return isinstance(value, str) and RECORD_COMPONENT.fullmatch(value) is not None


def _is_origin(value: object) -> bool:
    return isinstance(value, str) and hosted_origin(value) == value


def _title_problems(title: object, repository: object) -> list[str]:
    if not isinstance(title, str) or not title.strip():
        return [
            "the title is empty; a ticket's title is "
            "`<repository name>: <the root cause in one line>`"
        ]
    found = []
    if title != title.strip() or drafts.CONTROL.search(title):
        found.append(
            "the title carries surrounding whitespace, a line break or a control character"
        )
    if len(title) > TITLE_LIMIT:
        found.append(
            f"the title is {len(title)} characters, over the {TITLE_LIMIT} a ticket's "
            "title may hold"
        )
    if isinstance(repository, str) and _is_origin(repository):
        prefix = f"{repository_name(repository)}: "
        if not title.startswith(prefix) or not title.removeprefix(prefix).strip():
            found.append(
                f"the title {title!r} does not read `{prefix}<the root cause in one line>`"
            )
    return found


def _runs_problems(runs: object, creator: object) -> list[str]:
    if not isinstance(runs, list) or not runs:
        return ["`owning_runs` is not a non-empty list of run ids"]
    found = [f"`owning_runs` entry {one!r} is not a run id" for one in runs if not _is_run(one)]
    if len({str(one) for one in runs}) != len(runs):
        found.append("`owning_runs` names a run twice")
    if isinstance(creator, str) and creator not in runs:
        found.append(f"`owning_runs` does not include `created_by_run` {creator!r}")
    return found


def _drafts_problems(consumed: object, runs: object) -> list[str]:
    if not isinstance(consumed, list) or not consumed:
        return ["`drafts` is not a non-empty list of qualified draft ids"]
    found = []
    for draft in consumed:
        matched = DRAFT_ID.fullmatch(draft) if isinstance(draft, str) else None
        if matched is None:
            found.append(
                f"`drafts` entry {draft!r} is not a qualified draft id like "
                f"{SOURCE}:<run-id>/{drafts.DRAFTS}/<draft-id>"
            )
        elif isinstance(runs, list) and matched["run"] not in runs:
            found.append(f"`drafts` entry {draft!r} belongs to a run `owning_runs` does not name")
    return found


def _basis_problems(basis: object, repository: object) -> list[str]:
    if not isinstance(basis, Mapping) or not basis:
        return ["`basis` is not a non-empty mapping of normalized origin to commit"]
    found = [
        f"`basis` entry {origin!r}: {commit!r} is not a normalized origin and a 40-character commit"
        for origin, commit in basis.items()
        if not _is_origin(origin) or not isinstance(commit, str) or not COMMIT.fullmatch(commit)
    ]
    if isinstance(repository, str) and repository not in basis:
        found.append(f"`basis` names no commit for the ticket's own repository {repository!r}")
    return found


def _real_time(value: object) -> bool:
    if not isinstance(value, str) or not drafts.DRAFTED_AT.fullmatch(value):
        return False
    try:
        datetime.strptime(value, drafts.DRAFTED_AT_FORMAT)  # noqa: DTZ007 - the spelling is UTC's own `Z`
    except ValueError:
        return False
    return True


def is_host(value: object) -> bool:
    """Whether ``value`` has a hostname's shape; which host it names is never checked."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= HOST_LIMIT
        and all(HOST_LABEL.fullmatch(label) for label in value.split("."))
    )


def _record_problems(
    held: Mapping[str, object], *, run: str | None, root_cause: str | None
) -> list[str]:
    found = []
    # Named before the missing keys, because a ticket of an older schema lacks the keys its
    # successor added, and the schema is what says why.
    if "schema" in held and (type(held["schema"]) is not int or held["schema"] != SCHEMA):
        found.append(
            f"the record is schema {held['schema']!r}, and this reads schema {SCHEMA}; bring "
            "the ticket to the current shape"
        )
    missing = [key for key in RECORD_KEYS if key not in held]
    if missing:
        return [*found, f"the `{KEY}` record is missing {', '.join(missing)}"]
    if unexpected := sorted(key for key in held if key not in (*RECORD_KEYS, BINDING_FIELD)):
        found.append(
            f"the `{KEY}` record carries keys this does not write: {', '.join(unexpected)}"
        )
    stated = held["root_cause"]
    if not isinstance(stated, str) or not SLUG.fullmatch(stated):
        found.append(f"`root_cause` {stated!r} is not a kebab-case slug")
    elif root_cause is not None and stated != root_cause:
        found.append(f"`root_cause` {stated!r} is not the file's root cause {root_cause!r}")
    repository = held["repository"]
    if not _is_origin(repository):
        found.append(
            f"`repository` {repository!r} is not a normalized origin like github.com/owner/name"
        )
    creator = held["created_by_run"]
    if not _is_run(creator):
        found.append(f"`created_by_run` {creator!r} is not a run id")
    elif run is not None and creator != run:
        found.append(
            f"`created_by_run` {creator!r} is not {run!r}, the run whose tickets directory holds it"
        )
    found.extend(_runs_problems(held["owning_runs"], creator))
    found.extend(_drafts_problems(held["drafts"], held["owning_runs"]))
    found.extend(_basis_problems(held["basis"], repository))
    if not _real_time(held["verified_at"]):
        found.append(
            f"`verified_at` {held['verified_at']!r} is not an RFC 3339 UTC time like "
            "YYYY-MM-DDTHH:MM:SSZ"
        )
    if not is_host(held["host"]):
        found.append(
            f"`host` {held['host']!r} is not a hostname: at most {HOST_LIMIT} characters of "
            "dot-separated labels, each 1–63 ASCII letters, digits and `-`, neither starting "
            "nor ending with `-`; write what `hostname` prints"
        )
    if BINDING_FIELD in held and not _is_item_id(held[BINDING_FIELD]):
        found.append(
            f"`{BINDING_FIELD}` {held[BINDING_FIELD]!r} is not a board item's native id; leave "
            "it as `board-status` or `copy` wrote it"
        )
    return found


def _is_item_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"\S+", value) is not None


def _origin_problems(origin: object, held: Mapping[str, object]) -> list[str]:
    """How the store's origin an item carries disagrees with its record.

    A ticket's own origin names the board item it is bound to, and must name its binding;
    one naming the `drafts` source is a board item's, recording the ticket it was copied
    from, and must name the ticket its record describes.
    """
    if origin is None:
        return []
    matched = QUALIFIED_ID.fullmatch(origin) if isinstance(origin, str) else None
    if matched is None:
        return [f"`{ORIGIN_KEY}` {origin!r} is not a qualified id"]
    if matched["source"] == SOURCE:
        creator, cause = held.get("created_by_run"), held.get("root_cause")
        if origin == qualified_id(str(creator), str(cause)):
            return []
        return [
            f"`{ORIGIN_KEY}` names {origin!r}, which is not the ticket its record describes, "
            f"{qualified_id(str(creator), str(cause))!r}"
        ]
    bound = held.get(BINDING_FIELD)
    if matched["native"] == bound:
        return []
    return [
        f"`{ORIGIN_KEY}` names {origin!r}, where the ticket's `{BINDING_FIELD}` binding is "
        f"{bound!r}; leave both as `board-status` or `copy` wrote them"
    ]


def _repositories_problems(repositories: object, repository: object) -> list[str]:
    """How `repositories` is not exactly the record's `repository`, where the issue is created."""
    match repositories:
        case [named]:
            if _is_origin(repository) and named != repository:
                return [
                    f"the ticket's `repositories` names {named!r}, not its record's "
                    f"`repository` {repository!r}; the two name the one repository its issue "
                    "is created in"
                ]
            return []
        case [_, _, *_] as several:
            return [
                f"the ticket's `repositories` names {len(several)} entries; a ticket names "
                "exactly one, its record's `repository`, which is the repository its issue is "
                "created in"
            ]
        case _:
            return [
                "the ticket carries no `repositories`; a ticket names exactly one, its record's "
                "`repository`, which is the repository its issue is created in"
            ]


def _impact_problems(text: str) -> list[str]:
    """Every way an `## Impact` section's content is not prose followed by its three lines."""
    where = f"the body's `## {IMPACT}` section"
    lines = [line for line in IMPACT_LINE.finditer(text) if line["label"] in IMPACT_LINES]
    found = []
    if not (text[: lines[0].start()] if lines else text).strip():
        found.append(
            f"{where} carries no prose before its lines; state there the negative outcome when "
            "the root cause fires, and what it affects"
        )
    values: dict[str, str] = {}
    for label in IMPACT_LINES:
        match [line["value"].strip() for line in lines if line["label"] == label]:
            case []:
                found.append(f"{where} carries no `- {label}:` line")
            case [value]:
                values[label] = value
            case repeated:
                found.append(
                    f"{where} carries its `- {label}:` line {len(repeated)} times, not once"
                )
    if len(values) == len(IMPACT_LINES) and not IMPACT_TAIL.fullmatch(text[lines[0].start() :]):
        found.append(
            f"{where} does not end with its three lines, in this order and with nothing between "
            "or after them: " + ", ".join(f"`- {label}:`" for label in IMPACT_LINES)
        )
    severities: dict[str, Severity] = {}
    for label in (SEVERITY_LINE, MITIGATED_LINE):
        if label not in values:
            continue
        if values[label] in tuple(Severity):
            severities[label] = Severity(values[label])
        else:
            found.append(
                f"{where}'s `- {label}:` line names {values[label]!r}, which is not a "
                "severity; write one of " + ", ".join(f"`{one}`" for one in Severity)
            )
    workaround = values.get(WORKAROUND_LINE)
    if workaround == "":
        found.append(
            f"{where}'s `- {WORKAROUND_LINE}:` line is empty; name the workaround in place and "
            f"how it is applied, or write `{NO_WORKAROUND}`"
        )
    if len(severities) == len((SEVERITY_LINE, MITIGATED_LINE)):
        severity, mitigated = severities[SEVERITY_LINE], severities[MITIGATED_LINE]
        if mitigated.above(severity):
            found.append(
                f"{where}'s severity with the workaround `{mitigated}` is above its severity "
                f"`{severity}`; a workaround never makes the impact worse"
            )
        if workaround == NO_WORKAROUND and mitigated is not severity:
            found.append(
                f"{where}'s workaround is `{NO_WORKAROUND}`, so its severity with the "
                f"workaround `{mitigated}` has to be its severity `{severity}`"
            )
    return found


def _body_problems(body: object, host: object) -> list[str]:
    if not isinstance(body, str):
        return ["the ticket has no body"]
    found = drafts.sections(body)
    names = [name for name, _ in found]
    if retired := [heading for heading in RETIRED_HEADINGS if heading in names]:
        return [
            "the body carries "
            + " and ".join(f"`## {heading}`" for heading in retired)
            + f", which schema {RETIRED_AT} retired; bring the ticket to the current shape"
        ]
    after = 0
    for required in HEADINGS:
        try:
            at = names.index(required, after)
        except ValueError:
            return [
                f"the body carries no `## {required}` heading in its place; a ticket's "
                "body carries "
                + ", ".join(f"`## {heading}`" for heading in HEADINGS)
                + ", in that order, each with content"
            ]
        if not found[at][1].strip():
            return [f"the body's `## {required}` section is empty"]
        if required == IMPACT and (impact := _impact_problems(found[at][1])):
            return impact
        if required == EVIDENCE and is_host(host) and str(host) not in found[at][1]:
            return [
                f"the body's `## {EVIDENCE}` section does not name the host {host!r} the "
                "record's `host` states; state there the host the verification ran on"
            ]
        after = at + 1
    return _rejected_fixes_problems(found)


def _rejected_fixes_problems(found: Sequence[tuple[str, str]]) -> list[str]:
    """How an optional `## Rejected fixes` section is not once, after the fix, with content."""
    names = [name for name, _ in found]
    held = [at for at, name in enumerate(names) if name == REJECTED_FIXES]
    match held:
        case []:
            return []
        case [at]:
            if at == 0 or names[at - 1] != SUGGESTED_FIX:
                return [
                    f"the body's `## {REJECTED_FIXES}` section is not directly after "
                    f"`## {SUGGESTED_FIX}`; when present, it comes right after the fix"
                ]
            if not found[at][1].strip():
                return [
                    f"the body's `## {REJECTED_FIXES}` section is empty; list each fix "
                    "considered and why it was not chosen, or leave the section out"
                ]
            return []
        case repeated:
            return [
                f"the body carries `## {REJECTED_FIXES}` {len(repeated)} times; it is optional "
                "and appears at most once"
            ]


def edge_problems(edges: Sequence[Edge]) -> list[str]:
    """Every way the edges the store reports for a ticket are not the dependency shape.

    Shape alone, reading no board: each edge ends at a `task`, is of the blocking kind, and
    names a far end `<source>:<native>` with both parts non-empty, where the source is not
    the `drafts` source a ticket lives in; every edge names the one same source; and no far
    end is named twice, since a ticket depends on an accepted ticket once. Which source
    that is, and what the board holds there, is `board-status`'s to check.
    """
    where = f"the ticket's `{DEPENDENCY_FIELD}`"
    found = []
    sources = set()
    for repeated in sorted({edge.to for edge in edges if [e.to for e in edges].count(edge.to) > 1}):
        found.append(f"{where} names {repeated!r} more than once; one entry per accepted ticket")
    for edge in edges:
        if edge.item != DEPENDENCY_ITEM:
            found.append(
                f"{where} entry {edge.to!r} names a {edge.item}, where a dependency names a "
                f"`{DEPENDENCY_ITEM}`: the accepted ticket's board item"
            )
        if edge.kind != DEPENDENCY_KIND:
            found.append(
                f"{where} entry {edge.to!r} is of kind {edge.kind!r}, where a dependency is "
                f"of the `{DEPENDENCY_KIND}` kind, the default; write no `kind`"
            )
        matched = QUALIFIED_ID.fullmatch(edge.to)
        if matched is None:
            found.append(
                f"{where} entry {edge.to!r} is not a qualified id like "
                "`<board>:<native id of the accepted ticket's item>`"
            )
        elif matched["source"] == SOURCE:
            found.append(
                f"{where} entry {edge.to!r} names the `{SOURCE}` source, where a dependency "
                "names an accepted ticket's item on the board it is copied onto"
            )
        else:
            sources.add(matched["source"])
    if len(sources) > 1:
        found.append(
            f"{where} entries name {len(sources)} sources ({', '.join(sorted(sources))}), "
            "where every dependency names the one board the ticket is copied onto"
        )
    return found


def problems(
    item: Mapping[str, object],
    *,
    run: str | None = None,
    root_cause: str | None = None,
    edges: Sequence[Edge] = (),
) -> list[str]:
    """Every way a store item, as `onetaskgraph task show --json` reports it, is not a ticket.

    ``run`` and ``root_cause`` are what the ticket's path says, when it was read from one,
    and ``edges`` what the store's dependency walk reports for it — none for a board item
    read without one. The record's problems come first, so a ticket of an older schema is
    named for its schema before anything its successor added.
    """
    found = []
    metadata = item.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    held = metadata.get(KEY)
    if isinstance(held, Mapping):
        found.extend(_record_problems(held, run=run, root_cause=root_cause))
        found.extend(_origin_problems(metadata.get(ORIGIN_KEY), held))
        repository, host = held.get("repository"), held.get("host")
    else:
        found.append(f"the ticket carries no `{KEY}` metadata record")
        repository = host = None
    if item.get("project") is not None:
        found.append(
            "the ticket carries a `project`; a ticket carries none, so it lands on the "
            "board as a standalone item"
        )
    found.extend(_repositories_problems(item.get("repositories"), repository))
    category = _category(item.get("status"))
    if category not in tuple(Status):
        found.append(
            f"the status is {category!r}, where a ticket's status is "
            + "; ".join(f"`{status}`, {status.meaning}" for status in Status)
            + " — write the one `board-status` prints"
        )
    found.extend(_title_problems(item.get("title"), repository))
    found.extend(_body_problems(item.get("content"), host))
    found.extend(edge_problems(edges))
    return found


def _category(status: object) -> object:
    """The category a store item's status reports, which is what a ticket's status is."""
    return status.get("category") if isinstance(status, Mapping) else status


def from_store_item(
    item: Mapping[str, object],
    *,
    run: str | None = None,
    root_cause: str | None = None,
    edges: Sequence[Edge] = (),
) -> Ticket:
    """The ticket a store item holds, or :class:`Refused` naming every problem.

    ``edges`` is what the store's dependency walk reports for the item; a board item read
    with none reads back depending on nothing, which is what such a read is.
    """
    found = problems(item, run=run, root_cause=root_cause, edges=edges)
    if found:
        raise Refused(found)
    metadata = item["metadata"]
    # `problems` has just proven every shape narrowed here, and says so if it has not.
    assert isinstance(metadata, Mapping)  # noqa: S101
    held = metadata[KEY]
    assert isinstance(held, Mapping)  # noqa: S101
    basis = held["basis"]
    assert isinstance(basis, Mapping)  # noqa: S101
    return Ticket(
        title=str(item["title"]),
        status=Status(str(_category(item["status"]))),
        root_cause=RootCause(str(held["root_cause"])),
        repository=Origin(str(held["repository"])),
        created_by_run=RunId(str(held["created_by_run"])),
        owning_runs=tuple(RunId(str(one)) for one in held["owning_runs"]),
        drafts=tuple(QualifiedDraftId(str(one)) for one in held["drafts"]),
        basis=tuple(
            sorted(
                Basis(Origin(str(origin)), Commit(str(commit))) for origin, commit in basis.items()
            )
        ),
        verified_at=Timestamp(str(held["verified_at"])),
        host=Host(str(held["host"])),
        body=str(item["content"]).strip(),
        depends_on=tuple(sorted(QualifiedBoardId(edge.to) for edge in edges)),
        board_item=BoardItemId(str(held[BINDING_FIELD])) if BINDING_FIELD in held else None,
    )


def ticket_edges(ticket: str) -> list[Edge]:
    """The dependency edges the store reports for ``ticket``, every page, from it outward.

    The one read of a ticket's dependencies: `task show` carries no `depends_on`, so the
    walk is where an entry is read back. A far end qualified to another source is reported
    as named and never followed, which is what lets `validate` touch no board.
    """
    return [
        # llmlint: ignore[boundary_inputs_validated] The store's answer is validated at
        # the boundary by the typed SDK, which parses each edge into its own model — the
        # far end's id a `str` root, its item and the edge's kind each a `StrEnum` — so
        # these are typed reads, not coercions; what each *value* may be is then held by
        # `edge_problems` on every path that reads them.
        Edge(edge.to.id.root, edge.to.kind.value, edge.kind.value)
        for page in plan_store.every_page(
            f"the dependencies of {ticket}", plan_store.client().task_deps, id=ticket
        )
        for edge in page.items
    ]


def located_path(path: Path) -> tuple[str, str]:
    """The run and root cause a ticket file's path names, or :class:`Refused`."""
    run, root_cause = path.parent.parent.name, path.stem
    if (
        path.suffix != TICKET_SUFFIX
        or path.parent.name != TICKETS
        or path.parent.parent.parent.name != TASKS_DIRECTORY
        or not RECORD_COMPONENT.fullmatch(run)
        or not SLUG.fullmatch(root_cause)
    ):
        raise Refused(
            [
                f"{path} is not where a ticket is stored; a ticket is `<drafts root>/"
                f"{TASKS_DIRECTORY}/<run-id>/{TICKETS}/<root-cause>{TICKET_SUFFIX}`, with a "
                "kebab-case root cause"
            ]
        )
    return run, root_cause


def read_ticket(path: Path) -> Ticket:
    """The ticket at ``path``, read through the installed store; :class:`Refused` otherwise."""
    resolved = path.absolute()
    run, root_cause = located_path(resolved)
    ticket = qualified_id(run, root_cause)
    try:
        item = plan_store.task_record(ticket)
        edges = ticket_edges(ticket)
    except OSError as exc:
        raise Refused(
            [
                f"the store could not read {ticket} ({exc}); validate it from the "
                "environment the follow-ups launch exported, which names the drafts root"
            ]
        ) from None
    location = item.get("location")
    stored = location.get("path") if isinstance(location, Mapping) else None
    if not isinstance(stored, str) or Path(stored).resolve() != resolved.resolve():
        raise Refused(
            [
                f"the store reads {ticket} from {stored!r}, not from {resolved}; validate "
                "a ticket under the drafts root the follow-ups launch exported"
            ]
        )
    return from_store_item(item, run=run, root_cause=root_cause, edges=edges)


class Misbound(ValueError):
    """A ticket whose board item cannot be established as its binding; says why, naming ids."""


class Unplaced(ValueError):
    """The board holds a ticket's item at a category no ticket carries, so it is not copied."""

    def __init__(self, category: str) -> None:
        super().__init__(
            f"the board holds this ticket's item at category {category!r}, which no ticket "
            "carries; copy nothing and report it"
        )


class ProtectedFromWithdrawal(ValueError):
    """A withdrawal of a ticket a person accepted or deferred, which only a person undoes."""

    def __init__(self, status: Status) -> None:
        super().__init__(
            f"the board holds this ticket's item at `{status}` ({status.meaning}), which a "
            "person accepted or deferred, so this run never withdraws it: copy nothing, leave "
            "the local ticket as it is, and report that you would have withdrawn it and why"
        )
        self.status = status


class OutsideOwner(ValueError):
    """A ticket whose repository is not one of the board's owner, so its issue is filed nowhere.

    Filing an issue in a third party's public repository is an outward action nobody asked
    for, and the store compares no owner for an item with no parent, which a ticket is.
    """

    def __init__(self, repository: str, owner: str) -> None:
        super().__init__(
            f"the ticket's repository {repository!r} is not a repository of the board's owner "
            f"{owner!r} ({GITHUB}/{owner}/<name>), so its issue is filed nowhere: copy "
            "nothing, never change `repositories` to get it filed, and report it"
        )


class NotAccepted(ValueError):
    """A dependency the board does not hold as an accepted ticket of another root cause.

    Says every failing entry at once: what the ticket names and what the board holds
    there. On it the agent copies nothing for the ticket and re-derives it against the
    board as it now is.
    """

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__(
            "; ".join(problems)
            + "; copy nothing for this ticket, re-derive it against the board as it now is — "
            "removing the entry and the assumption from the text where the item is no longer "
            "accepted — and report what was printed"
        )
        self.problems = tuple(problems)


def board_owner(board: str) -> str | None:
    """The owner ``board`` is configured with, or `None` for a source that configures none.

    Read through the store's own configuration, the way a source's root is. A source with no
    owner — a `local-md` store — files no issue in any repository, so it bounds nothing.
    """
    owner = plan_store.configured_settings().get(f"sources.{board}.config.owner")
    if owner is not None and (not isinstance(owner, str) or not owner):
        raise OSError(f"source {board!r} configures an owner {owner!r} that names no account")
    return owner


def _held_record(item: Mapping[str, object]) -> Mapping[str, object]:
    """The `orchestrator.follow-up` record a store item carries, or an empty one."""
    metadata = item.get("metadata")
    held = metadata.get(KEY) if isinstance(metadata, Mapping) else None
    return held if isinstance(held, Mapping) else {}


def ticket_repository(ticket: str, item: Mapping[str, object]) -> str:
    """The normalized origin the stored ticket's record names, off the item the store read."""
    repository = _held_record(item).get("repository")
    if not isinstance(repository, str) or not _is_origin(repository):
        raise Refused(
            [
                f"{ticket} names no `repository` in its `{KEY}` record that is a normalized "
                "origin; validate the ticket before asking the board about it"
            ]
        )
    return repository


def dependency_problems(ticket: str, item: Mapping[str, object], board: str) -> list[str]:
    """Every dependency of ``ticket`` the board does not hold as the contract says.

    Each edge the store reports for the ticket is resolved against ``board``: it is refused
    when its far end names another source; when the board holds the item at a category
    outside the accepted ones; when the item carries no `orchestrator.follow-up` record
    naming a `root_cause` slug, which is what makes a board item a follow-up ticket at
    all; when that `root_cause` is the ticket's own — an accepted item for the same root
    cause takes this run's evidence as a comment, so an edge onto it is the two readings
    merging; when the board reports no `url` for it; or when that URL is absent from the
    ticket's body, which is where the text says what the accepted fix changed. An item
    the store cannot show is an :class:`OSError`, as every store failure is, and edges
    without the shape `validate` holds are :class:`Refused` before any is followed, since
    the far end of a mis-shaped edge is nothing this asks a board about.
    """
    body = item.get("content")
    text = body if isinstance(body, str) else ""
    own_cause = _held_record(item).get("root_cause")
    accepted = ", ".join(f"`{status}`" for status in Status if status.accepted)
    edges = ticket_edges(ticket)
    shaped = edge_problems(edges)
    if edges and (not isinstance(own_cause, str) or not SLUG.fullmatch(own_cause)):
        shaped.append(f"{ticket} names no `root_cause` slug in its `{KEY}` record")
    if shaped:
        raise Refused([*shaped, "validate the ticket before asking the board about it"])
    found = []
    for edge in edges:
        entry = f"the `{DEPENDENCY_FIELD}` entry {edge.to!r}"
        matched = QUALIFIED_ID.fullmatch(edge.to)
        if matched is None or matched["source"] != board:
            found.append(f"{entry} names a source other than the board `{board}`")
            continue
        try:
            far = plan_store.task_record(edge.to)
        except OSError as exc:
            raise OSError(f"{entry} names an item the board cannot show: {exc}") from exc
        held = str(_category(far.get("status")))
        if held not in tuple(Status) or not Status(held).accepted:
            found.append(
                f"{entry} names an item the board holds at {held!r}, not at an accepted "
                f"status ({accepted}), so its fix is not assumed"
            )
        far_cause = _held_record(far).get("root_cause")
        if not isinstance(far_cause, str) or not SLUG.fullmatch(far_cause):
            found.append(
                f"{entry} names an item carrying no `{KEY}` record with a `root_cause`, so it "
                "is no follow-up ticket; a dependency names an accepted ticket of another "
                "root cause"
            )
        elif far_cause == own_cause:
            found.append(
                f"{entry} names an item for the ticket's own root cause {own_cause!r}; an "
                "accepted item for the same root cause takes this run's evidence as a comment "
                "and is never depended on"
            )
        url = far.get("url")
        if not isinstance(url, str) or not ITEM_URL.fullmatch(url):
            found.append(
                f"{entry} names an item the board reports no `url` for that is a web URL "
                f"({url!r}), so the ticket's text cannot link it"
            )
        elif url not in text:
            found.append(
                f"{entry} names an item whose URL {url} the ticket's body never names; say "
                "where and how that fix changed this ticket, with that URL"
            )
    return found


def board_items(
    board: str, *, search: str | None = None, statuses: Sequence[str] = ()
) -> list[QualifiedTask]:
    """Every item of ``board`` the store lists for one query, every page, in listing order.

    The one board listing the follow-up agent is given. A plan-store listing is a page —
    `task list` answers the store's ``page_size`` and a ``next`` cursor while more remain —
    and the task once handed the agent that bare command for the board's inventory, the
    duplicate search and the accepted listing, describing each bounded answer as the whole
    board: the live board held 102 items and an unpaged listing answered 50, so a duplicate
    search read half the board and an ownership decision was made from it. This reads
    through :func:`plan_store.every_page`, which follows ``next`` until the store answers
    none, refuses a cursor it has already followed, and refuses a page any source could not
    answer. ``search`` is the store's own `--search`, a case-insensitive substring over
    titles and bodies; ``statuses`` its `--status` categories, every one when empty. Each
    entry is the store's own typed model, which `board-items` prints as `task list --json`
    lists it under `items`.
    """
    query: dict[str, object] = {"source": [board]}
    if search is not None:
        query["search"] = search
    if statuses:
        query["status"] = list(statuses)
    pages = plan_store.every_page(f"the items of {board!r}", plan_store.client().task_list, **query)
    return [held for page in pages for held in page.items]


def under_owner(repository: str, owner: str) -> bool:
    """Whether a normalized origin is `github.com/<owner>/<name>`."""
    host, named_owner, _name = repository.split("/")
    return host == GITHUB and named_owner == owner


def _native(qualified: str, board: str) -> BoardItemId:
    """The native id of a board item the store names qualified to ``board``."""
    matched = QUALIFIED_ID.fullmatch(qualified)
    if matched is None or matched["source"] != board:
        raise OSError(f"the store named {qualified!r}, which is not an item of {board!r}")
    return BoardItemId(matched["native"])


class Placement(NamedTuple):
    """The board item a ticket's copy reaches and the category the board holds it at.

    Both `None` for a ticket the board holds no item for, which a copy would create.
    """

    item: BoardItemId | None
    category: str | None


#: What one ticket's copy did to its item: the store's copy actions but `orphaned`, which a
#: copy reports for a destination item its source no longer holds, never for the one copied.
type CopyAction = Literal["created", "updated", "unchanged"]


class Copied(NamedTuple):
    """What one ticket's copy did, and to which item."""

    action: CopyAction
    item: BoardItemId


def _planned(ticket: str, board: str) -> Placement:
    """Where a copy of ``ticket`` onto ``board`` goes, dry-run.

    Asked of the store's own copy, because what the store reports is where the next copy
    writes.
    """
    planned = plan_store.sdk(plan_store.client().task_copy([ticket], to=board, dry_run=True))
    entries = planned.items
    entry = entries[0] if len(entries) == 1 else None
    outcome = entry.root if entry else None
    action = outcome.action if outcome else None
    destination = outcome.destination.root if outcome and outcome.destination else None
    if action == CREATED:
        return Placement(None, None)
    if action not in EXISTING or destination is None:
        raise OSError(
            f"the dry-run copy of {ticket} onto {board} answered {entries!r}, naming neither "
            "a new item nor an existing one"
        )
    item = plan_store.task_record(destination)
    return Placement(_native(destination, board), str(_category(item.get("status"))))


def board_category(ticket: str, board: str) -> str | None:
    """The category ``board`` holds ``ticket``'s item at, or `None` when it holds no item.

    Read off the store's own dry-run copy, which reports whether the copy would create an
    item or reach an existing one, and names that one.
    """
    return _planned(ticket, board).category


def _carriers(ticket: str, board: str) -> list[QualifiedTask]:
    """Every item of ``board`` whose store origin is ``ticket``, in listing order."""
    return [
        held for held in board_items(board) if (held.item.metadata or {}).get(ORIGIN_KEY) == ticket
    ]


def _survivor(ticket: Ticket, carriers: Sequence[QualifiedTask], board: str) -> QualifiedTask:
    """The one open item of the run's own among ``carriers``, or :class:`Misbound`.

    Every other carrier must already be withdrawn: one a person closed as completed, or any
    second open one, is a decision this run does not make for them.
    """
    closed = (Status.FINISHED.value, Status.WITHDRAWN.value)
    named = ", ".join(held.id.root for held in carriers)
    open_own = [
        held
        for held in carriers
        if held.item.status.category.value not in closed
        and metadata_owner(held.item.metadata or {}) == ticket.created_by_run
    ]
    if len(open_own) != 1:
        raise Misbound(
            f"{len(carriers)} items of {board!r} carry this ticket's origin ({named}), and "
            f"{len(open_own)} of them is an open item of run {ticket.created_by_run!r}, where "
            "exactly one must be: copy nothing and report it"
        )
    (survivor,) = open_own
    for held in carriers:
        if held is not survivor and held.item.status.category.value != Status.WITHDRAWN.value:
            raise Misbound(
                f"{len(carriers)} items of {board!r} carry this ticket's origin ({named}), and "
                f"{held.id.root} is not withdrawn beside the open {survivor.id.root}: copy "
                "nothing and report it"
            )
    return survivor


def _note_duplicate(duplicate: QualifiedTask, survivor: QualifiedTask, run: str) -> None:
    """Leave the withdrawn ``duplicate`` the comment naming ``survivor``, once.

    The store offers no edit of an item's origin — `metadata set` refuses the reserved
    `onetaskgraph` namespace — so a withdrawn duplicate keeps the origin it carries. What
    stops it corresponding is the binding, which every later copy follows instead; this
    comment says so on the closed issue, for the person who finds it.
    """
    named = survivor.item.url or survivor.id.root
    marker = DUPLICATE_MARKER.format(run=run, survivor=survivor.id.root)
    listed = plan_store.sdk(plan_store.client().task_comment_list(duplicate.id.root)).comments
    if any(comment.body.strip().endswith(marker) for comment in listed):
        return
    body = f"{DUPLICATE_OPENING.format(survivor=named, run=run)}\n\n{marker}\n"
    plan_store.sdk(plan_store.client().task_comment_add(duplicate.id.root, body=body))


def bind(path: Path, board: str, item: BoardItemId) -> Ticket:
    """Write ``item`` as the ticket's binding, and the store's origin naming it, into ``path``.

    Nothing is written when the ticket already carries both as they would be written.
    """
    ticket = dataclasses.replace(read_ticket(path), board_item=item)
    rendered = render(ticket, board=board)
    if path.read_text(encoding="utf-8") != rendered:
        path.write_text(rendered, encoding="utf-8")
    return ticket


def correspond(path: Path, board: str) -> Placement:
    """Establish the item ``board`` holds for the ticket at ``path``, and the category it holds.

    The board is read for every item
    carrying the ticket's origin: two or more are resolved to the run's own open item, which
    becomes the binding, and each withdrawn one is left a comment naming it; one, for a
    ticket not yet bound, becomes the binding. A binding is written only to an item that
    carries the ticket's origin. Then the store's dry-run copy is asked where the copy goes,
    and :class:`Misbound` refuses any destination that is not the binding, naming both.
    """
    ticket = read_ticket(path)
    run, root_cause = located_path(path.absolute())
    identifier = qualified_id(run, root_cause)
    carriers = _carriers(identifier, board)
    bound = ticket.board_item
    held = {_native(entry.id.root, board): entry for entry in carriers}
    if len(carriers) > 1:
        survivor = _survivor(ticket, carriers, board)
        for entry in carriers:
            if entry is not survivor:
                _note_duplicate(entry, survivor, ticket.created_by_run)
        bound = _native(survivor.id.root, board)
    elif bound is None and carriers:
        bound = next(iter(held))
    if bound is not None and bound in held:
        bind(path, board, bound)
    destination, category = _planned(identifier, board)
    if bound is not None and (destination != bound or bound not in held):
        reported = (
            f"reports {board}:{destination} as where this ticket is copied"
            if destination is not None
            else "would create a new item for this ticket"
        )
        carried = "" if bound in held else ", an item that does not carry this ticket's origin"
        raise Misbound(
            f"the store {reported}, where its `{BINDING_FIELD}` binding is {board}:{bound}"
            f"{carried}: copy nothing and report it"
        )
    return Placement(bound, category)


def copy_ticket(path: Path, board: str) -> Copied:
    """Copy the ticket at ``path`` onto ``board``, onto its bound item alone.

    :func:`correspond` first, so the copy is refused before anything is written unless the
    store's destination is the binding, which the copy then follows through the origin
    written beside it; the item a first copy creates becomes the binding.
    """
    bound = correspond(path, board).item
    run, root_cause = located_path(path.absolute())
    report = plan_store.sdk(
        plan_store.client().task_copy([qualified_id(run, root_cause)], to=board)
    )
    copied = copied_to(report, board, bound)
    if bound is None:
        bind(path, board, copied.item)
    return copied


def copied_to(report: CopyReport, board: str, bound: BoardItemId | None) -> Copied:
    """The action one ticket's copy report names and the item it reached, held to ``bound``.

    The dry-run before the write was held to the binding already, and the write follows the
    same origin; this holds the store's answer to the write too, so a store answering the two
    differently is refused by name rather than bound over.
    """
    entries = report.items
    outcome = entries[0].root if len(entries) == 1 else None
    if outcome is None or outcome.destination is None:
        raise OSError(f"the copy onto {board} answered {entries!r}, naming no item")
    match str(outcome.action):
        case "created" | "updated" | "unchanged" as action:
            copied = Copied(action, _native(outcome.destination.root, board))
        case action:
            raise OSError(
                f"the copy onto {board} answered {action!r} for this ticket, which is no copy of it"
            )
    if bound is not None and copied.item != bound:
        raise Misbound(
            f"the store copied this ticket onto {board}:{copied.item}, where its "
            f"`{BINDING_FIELD}` binding is {board}:{bound}: report it"
        )
    return copied


def status_before_copy(held: str | None, *, withdraw: bool) -> Status:
    """The status a ticket is copied with, given the category the board holds its item at.

    A ticket the board holds no item for is a proposal; one it holds carries the board's
    status, so a copy never undoes a person's decision; and a withdrawal closes an item only
    while nobody has accepted or deferred it. :class:`Unplaced` or :class:`ProtectedFromWithdrawal`
    otherwise.
    """
    if held is None:
        return Status.WITHDRAWN if withdraw else Status.PROPOSED
    if held not in tuple(Status):
        raise Unplaced(held)
    status = Status(held)
    if withdraw and status.protected_from_withdrawal:
        raise ProtectedFromWithdrawal(status)
    return Status.WITHDRAWN if withdraw else status


def comment_marker(run: str, root_cause: str) -> str:
    """The exact last line of an evidence comment ``run`` owns about ``root_cause``."""
    return COMMENT_MARKER.format(run=run, root_cause=root_cause)


def comment_opening(run: str) -> str:
    """The visible first line of an evidence comment ``run`` owns."""
    return COMMENT_OPENING.format(run=run)


def render_comment(run: str, root_cause: str, evidence: str) -> str:
    """A whole comment ``run`` adds to another run's issue: opening, evidence, marker."""
    return f"{comment_opening(run)}\n\n{evidence.strip()}\n\n{comment_marker(run, root_cause)}\n"


def reply_marker(run: str, root_cause: str, answers: str) -> str:
    """The exact last line of ``run``'s reply to the comment whose id is ``answers``.

    ``root_cause`` is the one in the ticket record of the issue the reply is posted on.
    """
    return REPLY_MARKER.format(
        run=run, root_cause=root_cause, kind=CommentKind.REPLY, answers=answers
    )


def reply_opening(run: str, url: str, author: str | None) -> str:
    """The visible first line of ``run``'s reply to the comment at ``url``."""
    if author is None:
        return REPLY_OPENING_UNATTRIBUTED.format(run=run, url=url)
    return REPLY_OPENING.format(run=run, author=author, url=url)


def render_reply(
    run: str, root_cause: str, *, answers: str, url: str, author: str | None, response: str
) -> str:
    """A whole reply ``run`` posts to one comment: opening, response, marker.

    ``author`` is `None` when the board reports none. :class:`Refused` for an id outside the
    grammar a marker carries, since that reply would be no run's comment.
    """
    if not COMMENT_ID.fullmatch(answers):
        raise Refused(
            [
                f"the comment id {answers!r} is not one a reply's marker can carry: one or more "
                'characters, none of them whitespace, `"` or `>`'
            ]
        )
    opening = reply_opening(run, url, author)
    marker = reply_marker(run, root_cause, answers)
    return f"{opening}\n\n{response.strip()}\n\n{marker}\n"


def comment_owner(body: str) -> CommentOwner | None:
    """What a comment's last line names, or `None` when no run owns it.

    Every part is held to its grammar, because the marker is stored text anybody with the
    board's credential can write: a value that is not a run id or a root-cause slug, a `kind`
    other than `reply`, a reply naming no id or one outside :data:`COMMENT_ID`, or an
    evidence marker naming an id, names no run's comment. A marker with no `kind` is an
    evidence comment, which is every comment written before replies existed.
    """
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    matched = COMMENT_MARKER_LINE.fullmatch(lines[-1]) if lines else None
    if (
        matched is None
        or not RECORD_COMPONENT.fullmatch(matched["run"])
        or not SLUG.fullmatch(matched["root_cause"])
    ):
        return None
    owner = CommentOwner(RunId(matched["run"]), RootCause(matched["root_cause"]))
    match matched["kind"], matched["answers"]:
        case None, None:
            return owner
        case CommentKind.REPLY, str(answers) if COMMENT_ID.fullmatch(answers):
            return owner._replace(kind=CommentKind.REPLY, answers=CommentId(answers))
        case _:
            return None


def issue_owner(item: Mapping[str, object]) -> RunId | None:
    """The run a board item's `created_by_run` names, or `None` when no run owns it."""
    metadata = item.get("metadata")
    return metadata_owner(metadata if isinstance(metadata, Mapping) else {})


def metadata_owner(metadata: Mapping[str, object]) -> RunId | None:
    """The run the `created_by_run` of an item's metadata names, or `None` when none does."""
    held = metadata.get(KEY)
    creator = held.get("created_by_run") if isinstance(held, Mapping) else None
    return RunId(creator) if isinstance(creator, str) and _is_run(creator) else None


def may_change_issue(run: str, item: Mapping[str, object]) -> bool:
    """Whether ``run`` may edit or close ``item``: only an issue it created."""
    return issue_owner(item) == run


def may_change_comment(run: str, body: str) -> bool:
    """Whether ``run`` may edit or delete a comment: only one its marker names, of either kind."""
    owner = comment_owner(body)
    return owner is not None and owner.run == run


def may_comment_on(
    run: str, item: Mapping[str, object], kind: CommentKind = CommentKind.EVIDENCE
) -> bool:
    """Whether ``run`` may add a comment of ``kind`` to ``item``.

    Evidence goes only on another run's issue, since a run edits its own issue instead; a
    reply goes on any issue a run owns, since it answers a person wherever they wrote.
    """
    owner = issue_owner(item)
    if kind is CommentKind.REPLY:
        return owner is not None
    return owner is not None and owner != run


#: What each body heading of the example ticket :func:`ticket_contract` renders says to write.
_HEADING_GUIDANCE = (
    "<a simple explanation of the root cause, naming the paths inside the repository where it "
    "lives>",
    "<the negative outcome when the root cause fires, and what it affects: which users, "
    "people, systems or artifacts of the repository. Then the three lines below, each exactly "
    "once, in this order, with nothing between or after them. A severity is one of these "
    "words, most severe first: "
    + "; ".join(f"`{severity}`, {severity.meaning}" for severity in Severity)
    + ". The severity with the workaround is never above the severity, and a workaround of "
    f"exactly `{NO_WORKAROUND}` leaves the two the same>\n\n"
    + impact_section(
        "",
        "<the severity with no workaround applied>",
        "<the workaround in place and how it is applied, or none>",
        "<the severity that remains once the workaround is accounted for>",
    ).strip(),
    "<one or more examples of it>",
    "<the host the verification ran on, exactly as `hostname` printed it; then per draft: "
    "the qualified draft id, its run and node, the verified claim with `path:line` at the "
    "basis commit, and the transcript command from the draft>",
    "<the one fix this ticket recommends: a single change, or a single set of changes that "
    "together remove the root cause, concrete enough that whoever picks it up has nothing left "
    "to choose. Never a list of options or alternatives to choose between>",
    "<every run whose evidence this ticket carries>",
)

#: What the optional `## Rejected fixes` section of the example ticket says to write.
_REJECTED_FIXES_GUIDANCE = (
    "<optional: leave this section out when no other fix was considered. Otherwise each fix that "
    "was considered and not chosen, and why it was rejected>"
)


def _example_body() -> str:
    """The body of the example ticket: every heading with its guidance, the optional one too."""
    sections = []
    for heading, guidance in zip(HEADINGS, _HEADING_GUIDANCE, strict=True):
        sections.append(f"## {heading}\n\n{guidance}")
        if heading == SUGGESTED_FIX:
            sections.append(f"## {REJECTED_FIXES}\n\n{_REJECTED_FIXES_GUIDANCE}")
    return "\n\n".join(sections)


def ticket_contract(run: str, board: str) -> str:
    """C5, as the follow-up agent is told it: the shape of the ticket it writes."""
    example = Ticket(
        title="<repository name>: <the root cause in one line>",
        status=Status.PROPOSED,
        root_cause=RootCause("<root-cause>"),
        repository=Origin(
            "<normalized origin the root cause lives in, like github.com/owner/name>"
        ),
        created_by_run=RunId(run),
        owning_runs=(
            RunId(run),
            RunId("<every other run whose evidence this ticket's body carries>"),
        ),
        drafts=(QualifiedDraftId(f"{SOURCE}:{run}/{drafts.DRAFTS}/<draft-id>"),),
        basis=(
            Basis(
                Origin("<normalized origin>"),
                Commit("<the 40-character commit the claims were verified at>"),
            ),
        ),
        verified_at=Timestamp("<now, in RFC 3339 UTC: YYYY-MM-DDTHH:MM:SSZ>"),
        host=Host("<exactly what `hostname` prints on the machine you run on>"),
        body=_example_body(),
        depends_on=(
            QualifiedBoardId(
                f"{board}:<native id of an accepted ticket whose fix changed this one; leave "
                f"`{DEPENDENCY_FIELD}` out when none did>"
            ),
        ),
        board_item=BoardItemId(
            "<written by `board-status` or `copy`, never by you; absent until one writes it>"
        ),
    )
    ticket = qualified_id(run, "<root-cause>")
    headings = ", ".join(f"`## {heading}`" for heading in HEADINGS)
    accepted = ", ".join(f"`{status}`" for status in Status if status.accepted)
    return (
        f"A ticket is a local Markdown task in the `{SOURCE}` source, written to "
        f"`@DRAFTS_ROOT@/{TASKS_DIRECTORY}/{run}/{TICKETS}/<root-cause>{TICKET_SUFFIX}` — "
        f"qualified id `{ticket}` — where `<root-cause>` is a kebab-case slug.\n\n"
        "- It carries **no `project`**, so it lands on the board as a standalone item.\n"
        "- **Its `repositories` names exactly one normalized origin, its record's "
        "`repository`**: the repository the root cause lives in. Its issue is created in "
        "that one repository and added to the board as an item, and that repository must "
        "belong to the board's owner, as `github.com/<owner>/<name>`.\n"
        "- Its title is `<repository name>: <the root cause in one line>`, at most "
        f"{TITLE_LIMIT} characters, where the repository name is the last segment of "
        "`repository`.\n"
        "- **Its status is the board's to decide.** A new ticket is "
        f"`{Status.PROPOSED.value}`, {Status.PROPOSED.meaning}, which the board shows as "
        "`Proposal`; a person moving it to `Todo` is what accepts it. A ticket the board "
        "already holds carries the status the board holds it at, so a copy never undoes that "
        "decision: a ticket the board holds at `Deferred` is copied carrying "
        f"`{Status.DEFERRED.value}`. A ticket this run withdraws is "
        f"`{Status.WITHDRAWN.value}`, which the board holds as closed as not planned, unless "
        "the board shows it as accepted or deferred: this run never withdraws a deferred item "
        "or an accepted one, so copy nothing, leave the local ticket as it is, and report that "
        "you would have withdrawn it and why. No issue is ever deleted.\n"
        f"- **Before every copy, run `@BOARD_STATUS@ --board {board} <path of the ticket>`** "
        "(adding `--withdraw` for a ticket this run withdraws, before you change that ticket), "
        "write the word it prints as the ticket's `status`, and validate the ticket again. It "
        f"exits {SOUND} with that word; {UNPLACED} when the board holds the item at a status "
        f"no ticket carries; {PROTECTED} for a withdrawal of an item the board shows as "
        f"accepted or deferred; {OUTSIDE_OWNER} when the ticket's repository is not one of "
        f"the board's owner, before anything is asked of the board; {NOT_ACCEPTED} when "
        f"a `{DEPENDENCY_FIELD}` entry does not resolve on the board as the dependency rule "
        "below states, naming every such entry and what the board holds; and "
        f"{MISBOUND} when the ticket's board item cannot be established as its binding, as "
        "the binding rule below states. On any of these refusals, copy nothing and report "
        "what it printed.\n"
        "- **A refusal is reported, never worked around.** When `board-status` exits "
        f"{OUTSIDE_OWNER}, or `@COPY@` refuses the ticket — for a repository the "
        "token cannot see, or one GitHub will not create an issue in — copy nothing for that "
        "ticket, never retry it with `repositories` removed or changed to get it filed, and "
        "report what was printed.\n"
        f"- Its front matter carries the `{KEY}` record with every key present, and its body "
        f"the headings {headings}, in that order, each with content. `created_by_run` is "
        "this run, and `owning_runs` includes it.\n"
        f"- **`## {SUGGESTED_FIX}` states one concrete fix**: a single change, or a single set "
        "of changes that together remove the root cause, never a list of options or "
        f"alternatives to choose between. `## {REJECTED_FIXES}` is optional: when another fix "
        f"was considered, it comes directly after `## {SUGGESTED_FIX}` and gives each rejected "
        "fix with why it was rejected; otherwise it is left out.\n"
        "- **`host` is read, never typed.** Run `hostname` on the machine you run on and "
        f"write exactly what it prints, both as `host` and in the `## {EVIDENCE}` section, "
        "never a value you type or recall.\n"
        f"- **A ticket written against an accepted ticket's fix depends on it, as the "
        f"store's own top-level `{DEPENDENCY_FIELD}`** — one entry per accepted ticket whose "
        f"fix changed this ticket, as `{{id: {board}:<native id>, item: {DEPENDENCY_ITEM}}}` "
        "and nothing else, its `kind` left to its default; no entry, and no "
        f"`{DEPENDENCY_FIELD}` at all, when no accepted fix changed it. `<native id>` is the "
        f"accepted item's id as `@BOARD_ITEMS@ --board {board}` reports it, after "
        f"the `{board}:`. The copy carries the entry onto the board as the board's own item "
        "dependency, which `@PLAN_STORE@ task deps` walks from either end, and the edge is "
        f"the one record of it: nothing in the `{KEY}` record repeats it.\n"
        "- **Where the accepted fix changed the ticket, the text says so with the item's "
        f"URL** — the `url` the board reports for the accepted item. In `## {IMPACT}` or "
        f"`## Root cause` for a ticket the fix narrowed, in `## {SUGGESTED_FIX}` for one it "
        f"re-fixed, and in `## {REJECTED_FIXES}` beside the fix it displaced, stating what "
        'is assumed ("assuming the fix in <URL> lands, …"; "chosen because <URL> already '
        '…"). A `Proposal` or `Deferred` item for a clearly related root cause may be '
        f"named as related, by URL, with **no** `{DEPENDENCY_FIELD}` entry and no change to "
        "the ticket's claims.\n"
        f"- **`@VALIDATE@` holds the entries' shape and reads no board**: it refuses a ticket "
        f"any of whose entries is not a `{DEPENDENCY_ITEM}`, is not of the `{DEPENDENCY_KIND}` "
        "kind, is not `<source>:<native id>` with both parts non-empty, names the "
        f"`{SOURCE}` source, names a second source beside the others', or names a far end "
        "another entry already names. "
        f"**`@BOARD_STATUS@` resolves every entry against the board** before every copy and "
        f"exits {NOT_ACCEPTED} when an entry names a source other than `{board}`; names an "
        f"item the board holds outside the accepted statuses ({accepted}); names an item "
        "whose record carries this ticket's own `root_cause` — an accepted item for the "
        "*same* root cause is the same-root-cause path's, which takes this run's evidence as "
        "a comment, and never this rule's; names an item the board reports no `url` for; or "
        "names an item whose URL the ticket's body does not carry. On that refusal copy "
        "nothing for the ticket, re-derive it against the board as it now is — removing the "
        "entry and the assumption from the text where the item is no longer accepted — "
        "validate it again, and report what was printed.\n"
        f"- **`{BINDING_FIELD}` binds the ticket to its board item**: the native id of the "
        f"item it is copied onto, as `@BOARD_ITEMS@ --board {board}` reports it after the "
        f"`{board}:`, in the `{KEY}` record. `@BOARD_STATUS@` and `@COPY@` write it and nothing "
        "else does — when this run creates its item or first reaches it, and again when two "
        "board items carry the ticket's origin, naming this run's own open item, the one "
        "survivor; each withdrawn duplicate is then left a comment naming the survivor. They "
        f"write the store's `{ORIGIN_KEY}` naming `{board}:<that id>` into the ticket's "
        "metadata beside it, which makes every later copy reach that item directly. When you "
        "rewrite a ticket, keep both exactly as they stand. Both commands exit "
        f"{MISBOUND}, naming both ids, when the store reports a destination other than the "
        "binding, or the binding names an item that does not carry the ticket's origin; and "
        "when two or more items carry it but not exactly one is this run's own open item "
        "beside only withdrawn ones.\n"
        f"- It reaches the board only as `@COPY@ --board {board} <path of the ticket>`, which "
        f"copies `{ticket}` onto its bound item and prints the action and the item it reached; "
        f"never as a bare `@PLAN_STORE@ task copy`, which follows whichever item the store "
        "finds first. That copy is the only way an issue is edited.\n\n"
        "The shape, with every placeholder to fill:\n\n"
        f"````markdown\n{render(example, board=board)}````\n"
    )


def comment_contract(run: str, board: str) -> str:
    """C6, as the follow-up agent is told it: who owns what on the board."""
    evidence = comment_marker(run, "<root-cause>")
    reply = reply_marker(run, "<root-cause>", "<comment id>")
    return (
        f"- **Ownership is by run.** An issue on `{board}` belongs to the run its `{KEY}` "
        "record's `created_by_run` names. A comment belongs to the run named in its **last "
        "line**, whichever of the two kinds below it is. This run, "
        f"`{run}`, may create, edit (by copying its ticket again) or close as not planned only "
        f"issues whose `created_by_run` is `{run}`, and may edit or delete only comments whose "
        f"marker names `{run}`. It never changes an issue or a comment belonging to another "
        "run.\n"
        "- **An evidence comment** carries this run's evidence on another run's issue. Its last "
        "line is exactly\n\n"
        f"  `{evidence}`\n\n"
        f"  and its first line is visible to a reader: `{comment_opening(run)}`\n\n"
        "  It goes only on an open issue another run created for the same root cause, which "
        "receives at most **one** from this run — whatever status the board holds it at, so an "
        "item at `Deferred` receives this run's one comment like any other open item. Where "
        "this run's evidence comment is already there, edit it in place with `@PLAN_STORE@ "
        "task comment edit`; never add a second. This run never adds an evidence comment to "
        "an issue it created: it edits that issue by copying its ticket again instead.\n"
        "- **A reply** answers one person's comment. Each comment the feedback below quotes "
        "under a `### Comment` heading, with its id and URL, gets exactly **one** new reply "
        "from this run, on the issue that holds that comment, whichever run owns that issue. "
        "Its last line is exactly\n\n"
        f"  `{reply}`\n\n"
        "  where `<root-cause>` is the `root_cause` in the ticket record of the issue the reply "
        "is posted on, and `answers` is the id of the comment it answers, exactly as the "
        "feedback gives it. Its first line is visible to a reader: "
        f"`{reply_opening(run, '<comment URL>', '<author>')}`, or "
        f"`{reply_opening(run, '<comment URL>', None)}` when the feedback reports no author. "
        "After a blank line comes the response: what this run did about the comment and why, "
        "or why it did nothing; after another blank line, the marker. Post it with "
        "`@PLAN_STORE@ task comment add`, after the actions it reports.\n"
        "- **A reply is never edited to answer a different comment**, since every comment gets "
        "a reply of its own. A reply never counts as this run's one evidence comment, and never "
        "carries evidence in place of the ticket or the evidence comment.\n"
        "- **Reply only to the comments the feedback quotes.** A comment that appears on the "
        "board during this dispatch is left for the next gathering, and feedback the manager "
        "wrote quotes no board comment, so it gets no reply.\n"
    )


#: The section a re-dispatch adds: when the manager sends feedback, or the run already
#: holds tickets from a follow-up agent before this one.
REDISPATCH = """\
## This is a re-dispatch

A follow-up agent has already worked run `@RUN@`'s drafts, so tickets, board issues and
comments of this run may already exist. "Ownership on the board" above binds every change:

- change only what belongs to run `@RUN@`, as those rules say, and nothing belonging to any
  other run;
- an issue run `@RUN@` created is **edited** — change its ticket and copy the ticket again —
  and every comment of run `@RUN@` is added, edited or left as those rules say;
- an existing ticket of run `@RUN@` is copied again carrying the board's status, which
  `@BOARD_STATUS@ --board @BOARD@ <path of the ticket>` prints, never the status the ticket
  held before — the board may have been moved since the last copy;
- a ticket of an older schema is brought to the current shape before it is copied, its
  `repositories` naming its record's `repository`, its `host` read from this machine
  with `hostname`, and its `## Impact` section written from the evidence the ticket
  already carries, re-verifying only a claim that no longer holds; its `## Repository`
  section removed, any path the ticket still needs moved into `## Root cause`; its
  `## Suggested fixes` rewritten as `## Suggested fix`, stating the one fix the ticket's
  evidence supports; and every other option it offered moved into `## Rejected fixes`,
  with why each was not chosen; then it is validated again.
- a ticket's dependencies on accepted tickets, and the claims written against their fixes,
  are re-derived from the board as it now is on every pass — an accepted ticket may have
  appeared, moved or been un-accepted since the last pass, so `depends_on` entries are
  added and removed and the ticket's `## Impact`, `## Root cause`, `## Suggested fix` and
  `## Rejected fixes` re-derived to match, and `@BOARD_STATUS@ --board @BOARD@ <path of
  the ticket>` refusing an entry is the signal to re-derive that ticket before copying it;
  nothing else about an older ticket moves.
"""

#: The heading the manager's feedback goes under, above the feedback itself.
FEEDBACK = """\
## Feedback on the previous follow-up run

The manager's feedback on what the previous follow-up agent of run `@RUN@` produced,
verbatim. Act on it within the rules above.

"""


#: What a word cannot carry and still survive unquoted in the shell a store instruction is
#: run in: everything outside the set `shlex.quote` leaves alone. Every store instruction
#: in the task is shell embedded in Markdown and is read as written, so one of these in the
#: program word — a space, a quote, a backtick, a `$` — makes it several words or shell
#: syntax rather than one path, which is the same defect as a bare name by another route.
_NEEDS_QUOTING = re.compile(r"[^\w@%+=:,./-]", re.ASCII)


def _plan_store_problems(plan_store: str) -> list[str]:
    """Every way the program a composed task writes its store instructions with is not one.

    All three are the same boundary. A value that is not **absolute** leaves the
    dispatch's own search path to decide what a store instruction runs, which is the whole
    defect the full spelling closes; a value that is not an **executable file** on this
    host is one the dispatch would meet as "command not found" after the launch, with the
    task already written and nothing left to repair it; and a value carrying anything the
    shell would not read as one word is a path that reaches the dispatch as something
    other than the program it names, however absolute and executable it is here.
    """
    named = Path(plan_store)
    if not named.is_absolute():
        return [
            f"the plan-store program {plan_store!r} is not an absolute path, so what a "
            "store instruction in this task resolves to would be the dispatch's own "
            "search path's to decide"
        ]
    if not named.is_file() or not os.access(named, os.X_OK):
        return [
            f"the plan-store program {plan_store} is not an executable file on this host, "
            "so every store instruction in this task names a program the dispatch cannot run"
        ]
    if unsafe := _NEEDS_QUOTING.search(plan_store):
        return [
            f"the plan-store program {plan_store!r} carries {unsafe[0]!r}, which the shell "
            "a store instruction runs in does not read as part of one word, so the task "
            "would name something other than that program"
        ]
    return []


def _filled(text: str, values: Mapping[str, str]) -> str:
    """``text`` with each placeholder ``values`` names filled, and every other left as is."""
    return PLACEHOLDER.sub(lambda matched: values.get(matched[1], matched[0]), text)


def compose(
    template: str,
    *,
    run: str,
    board: str,
    drafts_root: Path,
    validate: str,
    board_status: str,
    board_items: str,
    copy: str,
    checkout: Path,
    plan_store: str,
    feedback: str | None,
    redispatch: bool,
) -> str:
    """The follow-up agent's task: ``template`` with every placeholder filled, once.

    ``plan_store`` is the plan-store program every store instruction in the task is
    written with, and is refused unless it is an absolute path: the agent reads this task
    in a directory of its own, where a relative or bare name is answered by that
    dispatch's own search path. `scripts/follow-ups.sh` resolves it and says what that
    cost.

    The template is filled in a single pass, so what fills a placeholder is never read
    again for one — which is what brings the manager's feedback into the task verbatim,
    whatever it quotes. The contracts and sections carry the run and the drafts root
    themselves, so those are filled into them first.
    """
    named = PLACEHOLDER.findall(template)
    found = []
    if unknown := sorted(set(named) - set(PLACEHOLDERS)):
        found.append(f"the task template names placeholders nothing fills: {', '.join(unknown)}")
    if missing := [name for name in PLACEHOLDERS if name not in named]:
        found.append(f"the task template is missing placeholders: {', '.join(missing)}")
    if repeated := sorted(name for name in SECTIONS if named.count(name) > 1):
        found.append(f"the task template names these more than once: {', '.join(repeated)}")
    found.extend(_plan_store_problems(plan_store))
    if found:
        raise Refused(found)
    scalars = {
        "RUN": run,
        "BOARD": board,
        "DRAFTS_ROOT": str(drafts_root),
        "VALIDATE": validate,
        "BOARD_STATUS": board_status,
        "BOARD_ITEMS": board_items,
        "COPY": copy,
        "CHECKOUT": str(checkout),
        "PLAN_STORE": plan_store,
        "ACCEPTED_STATUSES": accepted_statuses(),
        "ACCEPTED_FILTER": accepted_filter(),
    }
    values = {
        **scalars,
        "STATUS_VOCABULARY": status_vocabulary(),
        "TICKET_CONTRACT": _filled(ticket_contract(run, board), scalars),
        "COMMENT_CONTRACT": _filled(comment_contract(run, board), scalars),
        "REDISPATCH": _filled(REDISPATCH, scalars) if redispatch else "",
        "FEEDBACK": (
            "" if feedback is None else _filled(FEEDBACK, scalars) + feedback.rstrip() + "\n"
        ),
    }
    return PLACEHOLDER.sub(lambda matched: values[matched[1]], template)


def inventory(root: Path, run: str) -> tuple[int, int]:
    """How many drafts and tickets ``run`` holds under a `drafts` root."""
    base = root / TASKS_DIRECTORY / run
    return (
        len(list((base / drafts.DRAFTS).glob(f"*{TICKET_SUFFIX}"))),
        len(list((base / TICKETS).glob(f"*{TICKET_SUFFIX}"))),
    )


class _Parser(argparse.ArgumentParser):
    """A parser whose refusals exit with :data:`UNRUNNABLE` and say what to do next."""

    def error(self, message: str) -> NoReturn:
        self.exit(UNRUNNABLE, f"{PROG}: refused: {message}; run it with --help for the contract\n")


def _parser() -> _Parser:
    parser = _Parser(
        prog=PROG,
        description="Validate follow-up tickets, and compose the follow-up agent's task.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    root_help = "the drafts root the launch exported"
    validate = commands.add_parser("validate", help="validate ticket files through the store")
    validate.add_argument("paths", nargs="+", type=Path, metavar="PATH")
    check = commands.add_parser("check-run", help="validate every ticket a run holds")
    check.add_argument("--root", type=Path, required=True, help=root_help)
    check.add_argument("run", metavar="RUN-ID")
    placed = commands.add_parser(
        # The follow-up task has this command persist the binding and note withdrawn
        # duplicates before it answers; `board-status` is the name the template, the
        # journeys and AGENTS.md already publish, and the help below states both writes.
        # llmlint: ignore[names_match_behavior] see the note above this line
        "board-status",
        help=(
            "print the status a ticket is copied with, decided from the board's item, after "
            "writing the ticket's binding to that item and noting any withdrawn duplicate"
        ),
    )
    placed.add_argument("--board", required=True, metavar="SOURCE")
    placed.add_argument(
        "--withdraw", action="store_true", help="decide for a ticket this run withdraws"
    )
    placed.add_argument("path", type=Path, metavar="PATH")
    copied = commands.add_parser(
        "copy", help="copy a ticket onto the board item it is bound to, and nowhere else"
    )
    copied.add_argument("--board", required=True, metavar="SOURCE")
    copied.add_argument("path", type=Path, metavar="PATH")
    listing = commands.add_parser(
        "board-items",
        help="print every item of a board one query selects, every page, as one JSON result",
    )
    listing.add_argument("--board", required=True, metavar="SOURCE")
    listing.add_argument("--search", metavar="TEXT", help="keep items whose title or body has it")
    listing.add_argument(
        "--status",
        action="append",
        default=[],
        choices=[status.value for status in Status],
        metavar="CATEGORY",
        help="keep items at this status; repeat for several",
    )
    commands.add_parser("statuses", help="print what each board status means")
    count = commands.add_parser("inventory", help="print how many drafts and tickets a run holds")
    count.add_argument("--root", type=Path, required=True, help=root_help)
    count.add_argument("run", metavar="RUN-ID")
    task = commands.add_parser("compose", help="print the follow-up agent's task")
    task.add_argument("--template", type=Path, required=True)
    task.add_argument("--root", type=Path, required=True, help=root_help)
    task.add_argument("--run", required=True, metavar="RUN-ID")
    task.add_argument("--board", required=True, metavar="SOURCE")
    task.add_argument("--validate", required=True, metavar="COMMAND")
    task.add_argument("--board-status", required=True, metavar="COMMAND")
    task.add_argument("--board-items", required=True, metavar="COMMAND")
    task.add_argument("--copy", required=True, metavar="COMMAND")
    task.add_argument("--checkout", type=Path, required=True, help="the launching checkout")
    task.add_argument(
        "--plan-store",
        required=True,
        metavar="PATH",
        help=(
            "the absolute path of the plan-store program every store instruction in the "
            "task is written with"
        ),
    )
    task.add_argument("--feedback", type=Path, metavar="FILE")
    return parser


def _validated(paths: Sequence[Path]) -> int:
    status = SOUND
    for path in paths:
        try:
            read_ticket(path)
        except Refused as refusal:
            status = UNSOUND
            print(f"{PROG}: {path} is not a sound ticket:", file=sys.stderr)
            for problem in refusal.problems:
                print(f"  - {problem}", file=sys.stderr)
            continue
        print(f"{PROG}: {path} is a sound ticket")
    return status


def _composed(arguments: argparse.Namespace) -> int:
    """Print the follow-up agent's task, for the `compose` command."""
    root: Path = arguments.root
    try:
        template = arguments.template.read_text(encoding="utf-8")
        feedback = None if arguments.feedback is None else arguments.feedback.read_text("utf-8")
        task = compose(
            template,
            run=arguments.run,
            board=arguments.board,
            drafts_root=root,
            validate=arguments.validate,
            board_status=arguments.board_status,
            board_items=arguments.board_items,
            copy=arguments.copy,
            checkout=arguments.checkout,
            plan_store=arguments.plan_store,
            feedback=feedback,
            redispatch=feedback is not None or inventory(root, arguments.run)[1] > 0,
        )
    except (OSError, Refused) as exc:
        print(f"{PROG}: refused: {exc}", file=sys.stderr)
        return UNRUNNABLE
    sys.stdout.write(task)
    return SOUND


def _listed(arguments: argparse.Namespace) -> int:
    """Print every item a board query selects, for the `board-items` command."""
    try:
        items = board_items(arguments.board, search=arguments.search, statuses=arguments.status)
    except OSError as exc:
        print(f"{PROG}: refused: {exc}", file=sys.stderr)
        return UNRUNNABLE
    json.dump({"items": [held.model_dump(mode="json") for held in items]}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return SOUND


def _placed(arguments: argparse.Namespace) -> int:
    """Print the status a ticket is copied with, for the `board-status` command."""
    try:
        run, root_cause = located_path(arguments.path.absolute())
        ticket = qualified_id(run, root_cause)
        owner = board_owner(arguments.board)
        item = plan_store.task_record(ticket)
        if owner is not None and not under_owner(
            repository := ticket_repository(ticket, item), owner
        ):
            raise OutsideOwner(repository, owner)
        if unheld := dependency_problems(ticket, item, arguments.board):
            raise NotAccepted(unheld)
        held = correspond(arguments.path, arguments.board).category
        status = status_before_copy(held, withdraw=arguments.withdraw)
    except OutsideOwner as refusal:
        print(f"{PROG}: {arguments.path}: {refusal}", file=sys.stderr)
        return OUTSIDE_OWNER
    except NotAccepted as refusal:
        print(f"{PROG}: {arguments.path}: {refusal}", file=sys.stderr)
        return NOT_ACCEPTED
    except Unplaced as refusal:
        print(f"{PROG}: {arguments.path}: {refusal}", file=sys.stderr)
        return UNPLACED
    except ProtectedFromWithdrawal as refusal:
        print(f"{PROG}: {arguments.path}: {refusal}", file=sys.stderr)
        return PROTECTED
    except Misbound as refusal:
        print(f"{PROG}: {arguments.path}: {refusal}", file=sys.stderr)
        return MISBOUND
    except (OSError, Refused) as exc:
        print(f"{PROG}: refused: {exc}", file=sys.stderr)
        return UNRUNNABLE
    print(status.value)
    return SOUND


def _copied(arguments: argparse.Namespace) -> int:
    """Copy a ticket onto its bound board item, for the `copy` command."""
    try:
        copied = copy_ticket(arguments.path, arguments.board)
    except Misbound as refusal:
        print(f"{PROG}: {arguments.path}: {refusal}", file=sys.stderr)
        return MISBOUND
    except (OSError, Refused) as exc:
        print(f"{PROG}: refused: {exc}", file=sys.stderr)
        return UNRUNNABLE
    destination = f"{arguments.board}:{copied.item}"
    json.dump({"action": copied.action, "destination": destination}, sys.stdout)
    sys.stdout.write("\n")
    return SOUND


def main(argv: Sequence[str] | None = None) -> int:
    """Validate, decide a status, list, copy, count or compose, for the recipe and the agent."""
    arguments = _parser().parse_args(argv)
    match arguments.command:
        case "validate":
            return _validated(arguments.paths)
        case "board-status":
            return _placed(arguments)
        case "board-items":
            return _listed(arguments)
        case "copy":
            return _copied(arguments)
        case "statuses":
            sys.stdout.write(status_vocabulary())
            return SOUND
        case _ if not RECORD_COMPONENT.fullmatch(arguments.run):
            print(f"{PROG}: refused: {arguments.run!r} is not a run id", file=sys.stderr)
            return UNRUNNABLE
        case "inventory":
            held_drafts, held_tickets = inventory(arguments.root, arguments.run)
            print(f"{held_drafts} {held_tickets}")
            return SOUND
        case "check-run":
            tickets = (arguments.root / TASKS_DIRECTORY / arguments.run / TICKETS).glob("*.md")
            return _validated(sorted(tickets))
        case _:
            return _composed(arguments)


if __name__ == "__main__":  # pragma: no cover - the module's own command line
    raise SystemExit(main())
