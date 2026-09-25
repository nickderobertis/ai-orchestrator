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
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, NamedTuple, NewType, NoReturn, cast

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

#: The placeholders a task template is filled at: values it may name as often as its
#: prose needs them, and sections it names exactly once, since a section rendered twice is
#: two copies of a contract in one task.
PLACEHOLDER = re.compile(r"@([A-Z][A-Z_]*)@")


class Mode(StrEnum):
    """Which dispatch a task is composed for. Each has its own template and its own account.

    **Initial** verifies a run's drafts and puts the tickets that stand on the board; it is
    what a launch with no gathered feedback composes. **Feedback** answers the board
    comments one gathering quoted and does nothing else: no inventory, no verification, no
    accepted-fix comparison, no status decision and no copy for a ticket no quoted comment
    names. Before the split there was one template, and a comment-only re-dispatch re-read
    and re-copied every unrelated ticket **after** its replies were already posted, which
    is how it reached the provider's deadline with the work it was dispatched for done.
    """

    INITIAL = "initial"
    FEEDBACK = "feedback"

    @property
    def values(self) -> tuple[str, ...]:
        """The placeholders this mode's template may name as often as it likes."""
        return _MODE_VALUES[self]

    @property
    def sections(self) -> tuple[str, ...]:
        """The placeholders this mode's template names exactly once."""
        return _MODE_SECTIONS[self]

    @property
    def placeholders(self) -> tuple[str, ...]:
        """Every placeholder this mode's template names, each at least once."""
        return (*self.values, *self.sections)


INITIAL_VALUES = (
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
    "DISPOSITIONS",
    "CHECK_DISPOSITIONS",
)
INITIAL_SECTIONS = (
    "STATUS_VOCABULARY",
    "TICKET_CONTRACT",
    "COMMENT_CONTRACT",
    "DISPOSITION_CONTRACT",
    "REDISPATCH",
    "FEEDBACK",
)
INITIAL_PLACEHOLDERS = (*INITIAL_VALUES, *INITIAL_SECTIONS)

FEEDBACK_VALUES = (
    "RUN",
    "BOARD",
    "CHECKOUT",
    "PLAN_STORE",
    "DRAFTS_ROOT",
    "TICKET_METADATA_KEY",
    "VALIDATE",
    "BOARD_STATUS",
    "COPY",
    "FEEDBACK_FILE",
    "RESPONSES",
    "CHECK_RESPONSES",
)
FEEDBACK_SECTIONS = ("COMMENT_CONTRACT", "RESPONSE_CONTRACT", "FEEDBACK")
FEEDBACK_PLACEHOLDERS = (*FEEDBACK_VALUES, *FEEDBACK_SECTIONS)

_MODE_VALUES = {Mode.INITIAL: INITIAL_VALUES, Mode.FEEDBACK: FEEDBACK_VALUES}
_MODE_SECTIONS = {Mode.INITIAL: INITIAL_SECTIONS, Mode.FEEDBACK: FEEDBACK_SECTIONS}

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

#: **The line a gathered feedback file anchors each comment it quotes on.** The grammar is
#: declared here, beside the markers, because two programs read it: `follow_up_comments`
#: renders it into every `### Comment` section it writes, and the response artifact's
#: validator below reads the comments one feedback file quotes back out of it. A prose
#: heading would have been the alternative, and a heading an agent reflows is a grammar
#: that drifts the first time one does.
QUOTED_COMMENT = '<!-- orchestrator:follow-up-quoted issue="{issue}" comment="{comment}" -->'
QUOTED_COMMENT_LINE = re.compile(
    r'<!-- orchestrator:follow-up-quoted issue="(?P<issue>[^"\s>]+)"'
    r' comment="(?P<comment>[^"\s>]+)" -->'
)
#: What every anchor line opens with, and the heading each comment's own section opens
#: with: one anchor per section is what a reader checks, because an anchor damaged or
#: struck out would otherwise take its comment out of the account and let it pass.
QUOTED_COMMENT_OPENING = "<!-- orchestrator:follow-up-quoted"
QUOTED_COMMENT_HEADING = "### Comment "

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


#: **The account each mode owes.** A follow-up dispatch runs in one of two modes, and each
#: leaves one machine-checkable account of what it did: initial mode a *disposition* per
#: input draft, feedback mode a *response* per quoted comment. Both shapes are declared in
#: what follows and nowhere else — the task templates render their contracts from
#: :func:`disposition_contract` and :func:`response_contract`, and the `check-dispositions`
#: and `check-responses` commands read the written account back through the same constants
#: — so the instruction an agent follows and the validator that refuses it cannot drift.
class Disposition(StrEnum):
    """What one input draft became. Exactly one of these accounts for each.

    Before this, a dropped draft appeared only in the agent's prose, and the one thing that
    ever surfaced a sound draft an agent had quietly omitted was the accidental full
    re-verification a feedback re-dispatch performed. That re-pass is gone; this is what
    replaces it, and it is the reason every draft is named rather than only the dropped ones.
    """

    FILED = "filed"
    NOT_REPRODUCIBLE = "not-reproducible"
    ALREADY_FIXED = "already-fixed"
    TOO_LOW_IMPACT = "too-low-impact"

    @property
    def meaning(self) -> str:
        """What this disposition claims about the draft, as the agent is told it."""
        return _DISPOSITION_MEANINGS[self]

    @property
    def names_root_causes(self) -> bool:
        """Whether this disposition has to link the draft to the root causes it supports."""
        return self is Disposition.FILED


_DISPOSITION_MEANINGS = {
    Disposition.FILED: (
        "its claim holds, and its evidence reached the board: as a ticket this run wrote, or "
        "as this run's evidence comment on another run's open issue for the same root cause. "
        "`root_causes` names every root cause it supports, each the root cause of a ticket "
        "this run holds under its `tickets/` whose `drafts` names this draft — which it keeps "
        "in both cases"
    ),
    Disposition.NOT_REPRODUCIBLE: (
        "its claim does not hold at the basis commit, or nothing in the tree or the "
        "transcript bears it out"
    ),
    Disposition.ALREADY_FIXED: (
        "the basis already carries the fix, or an accepted ticket's fix removes the root "
        "cause too, in which case `detail` names that item by URL"
    ),
    Disposition.TOO_LOW_IMPACT: (
        "its claim holds and nothing follows from it worth a ticket; `detail` says what the "
        "impact is and why it is below the bar"
    ),
}

#: Where a run's dispositions are kept: outside the `tasks/` tree the plan store reads, the
#: way a gathering's feedback is, so nothing here reaches the board as an item.
DISPOSITIONS_DIRECTORY = "dispositions"

#: The version of both artifacts below. A reader refuses any other, since each is a stored
#: shape another program reads.
ARTIFACT_SCHEMA = 1

DISPOSITION_KEYS = ("schema", "run", "drafts", "dispositions")
DISPOSITION_ENTRY_KEYS = ("draft", "disposition", "root_causes", "detail")
RESPONSE_KEYS = ("schema", "run", "feedback", "responses")
RESPONSE_ENTRY_KEYS = ("comment", "issue", "action", "reply")

RESPONSES_SUFFIX = ".responses.json"


class Quoted(NamedTuple):
    """One comment a gathered feedback file quotes, as its anchor line names it.

    A comment is the issue it sits on **and** its id, never the id alone: a board whose
    comment ids are unique only within an issue gives two comments on two issues one id,
    which the local stand-in really does.
    """

    issue: str
    comment: CommentId


@dataclass(frozen=True)
class Disposed:
    """What one input draft became, every field of the account's entry validated."""

    draft: QualifiedDraftId
    disposition: Disposition
    root_causes: tuple[RootCause, ...]
    detail: str


@dataclass(frozen=True)
class Response:
    """What was done about one quoted comment, every field of the entry validated."""

    comment: Quoted
    action: str
    reply: CommentId


def quoted_comment(issue: str, comment: str) -> str:
    """The anchor line a feedback file quotes one comment under.

    :class:`OSError` for an id outside the grammar the marker's attributes can carry —
    whitespace, a `"` or a `>` would end the attribute or the marker — because both come
    from the board rather than from here, and an anchor that closed early would take its
    comment out of every account that reads this file back.
    """
    for name, value in (("issue", issue), ("comment", comment)):
        if not COMMENT_ID.fullmatch(value):
            raise OSError(
                f"the board named the {name} {value!r}, which a quoted-comment anchor cannot "
                'carry: one or more characters, none of them whitespace, `"` or `>`'
            )
    return QUOTED_COMMENT.format(issue=issue, comment=comment)


@dataclass(frozen=True)
class QuotedMetadata:
    """The fields a gathering copies under each quoted comment, each ``None`` until read.

    Each is checked against the board comment its section's anchor names, so a field the
    gathering did not write as the board reports it is a comment quoted as another.
    """

    url: str | None = None
    author: str | None = None
    last_changed: str | None = None
    issue_title: str | None = None


#: Each metadata field's label on its `- <label>: <value>` line, and the attribute it fills.
QUOTED_METADATA_FIELDS = {
    "URL": "url",
    "Author": "author",
    "Last changed": "last_changed",
    "Issue title": "issue_title",
}


class Gathering(NamedTuple):
    """One gathered feedback file as a reader of its anchors sees it."""

    #: Every comment it quotes, in the order it quotes them.
    quoted: tuple[Quoted, ...]
    #: The text inside each comment's fence, in anchor order; absent fences are refused.
    texts: tuple[str | None, ...]
    #: Metadata copied into the task for each anchor, checked against the named board comment.
    metadata: tuple[QuotedMetadata, ...]
    #: The generator's instructions before the first section, if this file carries them.
    preamble: str
    #: Non-comment instructions or unknown fields inside a comment section.
    unexpected: tuple[str, ...]
    #: A section heading or comment-id field that disagrees with its own anchor.
    mismatched: tuple[str, ...]
    #: How many comment sections carry other than exactly one anchor, counting an anchor
    #: above the first section as one more, and how many anchor lines it carries that are
    #: not one: one anchor under each section is the only reading under which the account
    #: below answers the whole gathering.
    unpaired: int
    malformed: int


def read_gathering(text: str) -> Gathering:
    """A gathered feedback file read for the comments it quotes and the sections it opens.

    A feedback file quotes each person's comment verbatim inside a fence, and a person may
    write anything at all in a comment — an anchor line of this very grammar included — so
    a fenced region is skipped rather than scanned. Without that, one comment quoting the
    feedback file that quoted it would add a comment to the account that the gathering
    never selected, and the response artifact would be refused for not answering it.

    The unpaired sections and the malformed anchors are counted for the opposite failure: a
    single anchor damaged, struck out or moved under another section leaves its comment
    quoted to a reader and invisible here, so the account could omit it and still pass.
    Each anchor is paired with the section it sits under rather than the two being totalled,
    because a section left bare beside one carrying two balances any count.
    """
    found = []
    texts: list[str | None] = []
    metadata: list[QuotedMetadata] = []
    mismatched: list[str] = []
    unexpected: list[str] = []
    unpaired = malformed = 0
    #: The anchors under the section being read, or ``None`` above the first one.
    anchored: int | None = None
    fence = 0
    captured: list[str] = []
    capturing: int | None = None
    heading_issue: str | None = None
    section_anchor: int | None = None
    comment_fields = 0
    for line in text.splitlines():
        stripped = line.strip()
        opened = len(stripped) - len(stripped.lstrip("`"))
        if fence:
            # A fence closes on a line of backticks alone, at least as long as the one that
            # opened it, which is the rule `_fenced` writes its fences under.
            if opened >= fence and opened == len(stripped):
                if capturing is not None:
                    texts[capturing] = "\n".join(captured).rstrip()
                    capturing = None
                fence = 0
            elif capturing is not None:
                captured.append(line)
            continue
        if opened >= 3:
            fence = opened
            if (
                stripped == "`" * opened + "text"
                and section_anchor is not None
                and texts[section_anchor] is None
            ):
                capturing = section_anchor
                captured = []
            elif anchored is not None:
                # Any other fence under a comment section is content the gathering never
                # wrote, and what it holds would reach the dispatch unread by this check.
                unexpected.append(stripped)
            continue
        if stripped.startswith(QUOTED_COMMENT_HEADING):
            unpaired += anchored is not None and anchored != 1
            if anchored == 1 and comment_fields != 1:
                mismatched.append(
                    "its comment section must carry exactly one `- Comment id:` field"
                )
            anchored = 0
            comment_fields = 0
            heading = re.fullmatch(r"### Comment [0-9]+: on `([^`]+)`(?:,.*)?", stripped)
            heading_issue = heading[1] if heading is not None else None
            if heading is None:
                mismatched.append(f"its comment section has no issue in its heading: {stripped}")
            section_anchor = None
            continue
        matched = QUOTED_COMMENT_LINE.fullmatch(stripped)
        if matched is not None:
            found.append(Quoted(matched["issue"], CommentId(matched["comment"])))
            texts.append(None)
            metadata.append(QuotedMetadata())
            section_anchor = len(found) - 1
            if heading_issue is not None and heading_issue != matched["issue"]:
                mismatched.append(
                    f"its comment section names {heading_issue}, but its anchor names "
                    f"{matched['issue']}"
                )
            if anchored is None:
                unpaired += 1
            else:
                anchored += 1
        elif stripped.startswith(QUOTED_COMMENT_OPENING):
            malformed += 1
        elif stripped.startswith("- Comment id: ") and section_anchor is not None:
            comment_fields += 1
            named = stripped.removeprefix("- Comment id: ")
            if named != found[section_anchor].comment:
                mismatched.append(
                    f"its comment section names id {named}, but its anchor names "
                    f"{found[section_anchor].comment}"
                )
        elif (
            stripped.startswith(tuple(f"- {label}: " for label in QUOTED_METADATA_FIELDS))
            and section_anchor is not None
        ):
            label, value = stripped[2:].split(": ", 1)
            field = QUOTED_METADATA_FIELDS[label]
            if getattr(metadata[section_anchor], field) is not None:
                mismatched.append(f"its comment section names {label} more than once")
            metadata[section_anchor] = dataclasses.replace(
                metadata[section_anchor], **{field: value}
            )
        elif stripped and anchored is not None:
            unexpected.append(stripped)
    unpaired += anchored is not None and anchored != 1
    if anchored == 1 and comment_fields != 1:
        mismatched.append("its comment section must carry exactly one `- Comment id:` field")
    first = re.search(r"^### Comment ", text, re.MULTILINE)
    preamble = text[: first.start()].rstrip() if first is not None else text.rstrip()
    return Gathering(
        tuple(found),
        tuple(texts),
        tuple(metadata),
        preamble,
        tuple(unexpected),
        tuple(mismatched),
        unpaired,
        malformed,
    )


def quoted_comments(text: str) -> list[Quoted]:
    """Every comment a gathered feedback file quotes, in the order it quotes them."""
    return list(read_gathering(text).quoted)


def gathering_on_board(
    gathering: Gathering, run: str, *, check_issue_title: bool = True
) -> list[str]:
    """Every comment a gathering quotes that the board does not hold where it says.

    The syntax alone cannot say a gathering is about anything real: a file written by hand,
    or one left behind when its issues were closed and reopened elsewhere, quotes comments
    the account below would then be held to answering and nobody could. So each issue is
    asked for its comments, and a refusal to answer for an issue is that issue's finding
    rather than an unreadable board.
    """
    from . import follow_up_comments

    found = []
    for issue in sorted({one.issue for one in gathering.quoted}):
        try:
            item = plan_store.task_record(issue)
            listed = plan_store.sdk(plan_store.client().task_comment_list(issue)).comments
        except OSError as exc:
            found.append(f"it quotes {issue}, which the board would not answer for: {exc}")
            continue
        if issue_owner(item) != run and not any(
            may_change_comment(run, comment.body) for comment in listed
        ):
            found.append(
                f"it quotes {issue}, which run {run} neither owns nor marked with its own comment"
            )
        comments = {CommentId(comment.id.model_dump()): comment for comment in listed}
        held = {identifier: comment.body for identifier, comment in comments.items()}
        found.extend(
            f"it quotes comment {one.comment} on {issue}, which that issue does not hold"
            for one in gathering.quoted
            if one.issue == issue and one.comment not in held
        )
        found.extend(
            f"it quotes comment {one.comment} on {issue}, which a run marker owns"
            for one in gathering.quoted
            if one.issue == issue
            and one.comment in held
            and comment_owner(held[one.comment]) is not None
        )
        found.extend(
            f"it quotes comment {one.comment} on {issue} with text different from the board"
            for one, body in zip(gathering.quoted, gathering.texts, strict=True)
            if one.issue == issue and one.comment in held and body != held[one.comment].rstrip()
        )
        location = item.get("location")
        path = location.get("path") if isinstance(location, Mapping) else None
        issue_url = item.get("url")
        for one, fields in zip(gathering.quoted, gathering.metadata, strict=True):
            if one.issue != issue or one.comment not in comments:
                continue
            comment = comments[one.comment]
            author = comment.author or follow_up_comments.UNKNOWN_AUTHOR
            if fields.author != author:
                found.append(
                    f"it quotes comment {one.comment} on {issue} with author {fields.author!r} "
                    f"rather than the board's {author!r}"
                )
            url = follow_up_comments.comment_url_parts(
                issue_url if isinstance(issue_url, str) else None,
                path if isinstance(path, str) else None,
                one.comment,
                comment.url,
                issue,
            )
            if fields.url != url:
                found.append(
                    f"it quotes comment {one.comment} on {issue} with URL "
                    f"{fields.url!r} rather than the board's {url!r}"
                )
            changed = follow_up_comments.moment(
                comment.updated_at or comment.created_at,
                f"comment {one.comment!r} on {issue}",
            ).strftime(follow_up_comments.MOMENT_FORMAT)
            if fields.last_changed != changed:
                found.append(
                    f"it quotes comment {one.comment} on {issue} as last changed "
                    f"{fields.last_changed!r} rather than the board's {changed!r}"
                )
            if check_issue_title and fields.issue_title != item.get("title"):
                found.append(
                    f"it quotes {issue} with title {fields.issue_title!r} rather than "
                    f"the board's {item.get('title')!r}"
                )
    return found


def gathering_problems(gathering: Gathering, feedback: Path, board: str, run: str) -> list[str]:
    """Every way a file's own text is not a gathering of ``board``'s comments.

    What it says about itself, read without the board; :func:`gathering_on_board` is what
    asks whether the comments it names are there.
    """
    found: list[str] = []
    for one, fields in zip(gathering.quoted, gathering.metadata, strict=True):
        missing = [
            label
            for label, field in QUOTED_METADATA_FIELDS.items()
            if getattr(fields, field) is None
        ]
        if missing:
            found.append(
                f"comment {one.comment} on {one.issue} is missing metadata: "
                + ", ".join(sorted(missing))
            )
    # A file with the gathering's instructions stripped is refused like one with them
    # rewritten: the recorded boundary lives there, so nothing else says what was gathered.
    from . import follow_up_comments

    boundary = follow_up_comments.recorded_boundary(gathering.preamble, str(feedback))
    if (
        boundary is None
        or gathering.preamble != follow_up_comments.render(run, board, [], boundary.moment).rstrip()
    ):
        found.append("its instructions before the first comment differ from the gathering")
    found.extend(
        f"its comment section carries an unexpected line: {line}" for line in gathering.unexpected
    )
    if gathering.malformed:
        found.append(
            f"it carries {gathering.malformed} anchor line(s) of the quoted-comment grammar "
            "that are not one, so the comments they were written for are quoted to a reader "
            "and invisible to this account"
        )
    found.extend(gathering.mismatched)
    found.extend(
        f"comment {one.comment} on {one.issue} has no quoted text fence"
        for one, body in zip(gathering.quoted, gathering.texts, strict=True)
        if body is None
    )
    if gathering.unpaired:
        found.append(
            f"{gathering.unpaired} of its comment sections carry other than exactly one "
            "anchor, or an anchor sits above its first section, so a section's comment could "
            "be answered as another's or go unanswered unseen"
        )
    if not gathering.quoted:
        found.append(
            "it quotes no comment, so it is not a gathering this dispatch could have been "
            "given: an account of it would answer nothing and pass"
        )
    found.extend(
        f"it quotes comment {one.comment} on {one.issue} {gathering.quoted.count(one)} times, "
        "and an account of it could be neither complete nor free of duplicates"
        for one in dict.fromkeys(gathering.quoted)
        if gathering.quoted.count(one) > 1
    )
    found.extend(
        f"it quotes comment {one.comment} on {one.issue}, which is not an item of the "
        f"{board!r} board this is reading"
        for one in gathering.quoted
        if not one.issue.startswith(f"{board}:")
    )
    return [f"{feedback.name} is not a gathering: {problem}" for problem in found]


def draft_ids(root: Path, run: str) -> list[QualifiedDraftId]:
    """Every draft ``run`` holds under a `drafts` root, as the store qualifies it, sorted.

    The input set the disposition artifact accounts for. It is read once, by the recipe,
    **before** the dispatch: the agent deletes each draft a ticket consumed, so a set
    re-derived afterwards would be the drafts nothing happened to.
    """
    held = (root / TASKS_DIRECTORY / run / drafts.DRAFTS).glob(f"*{TICKET_SUFFIX}")
    found = []
    # A directory named like a draft is not one, and counting it would hand the dispatch a
    # draft with no file to verify.
    for path in (one for one in held if one.is_file()):
        name = QualifiedDraftId(f"{SOURCE}:{run}/{drafts.DRAFTS}/{path.stem}")
        if DRAFT_ID.fullmatch(name) is None:
            raise OSError(f"draft file {path.name!r} is not a draft id of run {run}")
        found.append(name)
    return sorted(found)


def written_tickets(root: Path, run: str) -> dict[str, tuple[QualifiedDraftId, ...] | None]:
    """Every root cause ``run`` holds a ticket for under a `drafts` root, with its `drafts`.

    ``None`` stands for a ticket the store will not read: `check-run` refuses that ticket
    in its own words, and nothing here can say which drafts it carries.
    """
    held = (root / TASKS_DIRECTORY / run / TICKETS).glob(f"*{TICKET_SUFFIX}")
    found: dict[str, tuple[QualifiedDraftId, ...] | None] = {}
    # Only a file is a ticket: a directory named like one would let a `filed` disposition
    # pass the local-ticket check with nothing written.
    for path in (one for one in held if one.is_file()):
        try:
            found[path.stem] = read_ticket(path).drafts
        except Refused:
            found[path.stem] = None
    return found


def filed_board_problems(root: Path, run: str, causes: Collection[str], board: str) -> list[str]:
    """Filed root causes whose ticket or evidence comment did not reach ``board``.

    An account filing nothing has nothing on the board to check, so the board is not read.
    """
    if not causes:
        return []
    items = board_items(board)
    found = []
    for cause in sorted(causes):
        ticket = read_ticket(ticket_path(root, run, cause))
        origin = qualified_id(run, cause)
        carried = False
        for item in items:
            metadata = item.item.metadata or {}
            if (
                metadata.get(ORIGIN_KEY) == origin
                and metadata_owner(metadata) == run
                and ticket.board_item == _native(item.id.root, board)
            ):
                carried = True
                break
            held = metadata.get(KEY)
            if not isinstance(held, Mapping) or held.get("root_cause") != cause:
                continue
            comments = plan_store.sdk(plan_store.client().task_comment_list(item.id.root)).comments
            if any(
                (owner := comment_owner(comment.body)) is not None
                and owner.run == run
                and owner.root_cause == cause
                and owner.kind is CommentKind.EVIDENCE
                for comment in comments
            ):
                carried = True
                break
        if not carried:
            found.append(
                f"the filed root cause {cause} has a local ticket but no bound item or "
                f"evidence comment of run {run} on {board}, so its evidence did not reach the board"
            )
    return found


def dispositions_path(root: Path, run: str) -> Path:
    """Where ``run``'s disposition artifact is kept under a `drafts` root."""
    return root / DISPOSITIONS_DIRECTORY / f"{run}.json"


def _drafts_recorded(held: object, run: str) -> tuple[list[QualifiedDraftId], list[str]]:
    """An artifact's recorded input set, and every way what it holds is not one.

    The set is written by this host and then sits under a root a dispatch can write, so it
    is read as an input rather than trusted: a name that is not a draft id of this run, or
    one recorded twice, makes the account's own subject something other than the drafts
    this dispatch was handed.
    """
    if not isinstance(held, list) or any(not isinstance(name, str) for name in held):
        return [], ["its `drafts` is not a list of draft ids"]
    recorded = [QualifiedDraftId(str(name)) for name in held]
    found = []
    for name in recorded:
        matched = DRAFT_ID.fullmatch(name)
        if matched is None or matched["run"] != run:
            found.append(f"its `drafts` names {name!r}, which is not a draft id of run {run}")
    found.extend(
        f"its `drafts` names {name} more than once"
        for name in dict.fromkeys(recorded)
        if recorded.count(name) > 1
    )
    return recorded, found


def open_dispositions(root: Path, run: str) -> Path:
    """Record the drafts a dispatch is about to be given, and return the artifact's path.

    The input set only ever **grows**: a re-dispatch over a run whose first pass consumed
    its drafts would otherwise record an empty input set and take the first pass's account
    with it. Every entry already recorded is left exactly as it stands, so this writes the
    skeleton and never an answer.

    An artifact already there is read as an input, not resumed blindly: one of another
    schema or another run, or whose recorded set is not one, is :class:`Refused` here —
    before the launch, where a person can repair it — rather than rewritten into a
    skeleton that takes the earlier pass's answers with it.
    """
    path = dispositions_path(root, run)
    existing = path.is_file()
    document = _artifact(path) if existing else {"schema": ARTIFACT_SCHEMA, "run": run}
    # The two keys this writes are the two a first pass has yet to hold, so their absence
    # is what an artifact opened for the first time looks like rather than a refusal.
    found = _envelope_problems(
        document,
        run,
        DISPOSITION_KEYS,
        "dispositions",
        optional=() if existing else ("drafts", "dispositions"),
    )
    recorded: list[QualifiedDraftId] = []
    if "drafts" in document:
        recorded, problems = _drafts_recorded(document["drafts"], run)
        found.extend(problems)
    if found:
        raise Refused([f"{path} is not this run's account: {problem}" for problem in found])
    entries = document.get("dispositions")
    expected = sorted({*recorded, *draft_ids(root, run)})
    if isinstance(entries, list):
        for at, entry in enumerate(entries):
            if problems := _entry_problems(entry, DISPOSITION_ENTRY_KEYS, at):
                found.extend(problems)
                continue
            # The entry-shape check above proves this mapping before `_disposed` checks
            # its values; the cast states that narrowing across the helper boundary.
            _, problems = _disposed(cast(Mapping[str, object], entry), expected, at)
            found.extend(problems)
    if found:
        raise Refused([f"{path} is not this run's account: {problem}" for problem in found])
    written = {
        "schema": ARTIFACT_SCHEMA,
        "run": run,
        "drafts": expected,
        "dispositions": entries if isinstance(entries, list) else [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(written, indent=2) + "\n", encoding="utf-8")
    return path


def responses_path(feedback: Path) -> Path:
    """Where the response artifact answering one gathered feedback file is kept, beside it."""
    return feedback.with_name(feedback.name.removesuffix(TICKET_SUFFIX) + RESPONSES_SUFFIX)


def _text(path: Path) -> str:
    """``path`` read as UTF-8, with bytes that are not text refused as the file's own fault.

    Every file this reads is written outside this program — an agent's account, a
    gathering, the tracked template — so a decoding failure is a refusal the command
    reports rather than a traceback out of it.
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as broken:
        raise OSError(f"{path} is not UTF-8 text: {broken}") from None


def _artifact(path: Path) -> Mapping[str, object]:
    """One artifact's JSON document, or :class:`Refused` saying what it is instead.

    The bytes are an agent's, so text that is not UTF-8 is one more way the document is not
    one rather than a traceback out of the command that read it.
    """
    try:
        document: object = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as broken:
        raise Refused([f"{path} is not JSON: {broken}"]) from None
    if not isinstance(document, Mapping):
        raise Refused([f"{path} holds {type(document).__name__}, not an object"])
    return document


def _envelope_problems(
    document: Mapping[str, object],
    run: str,
    keys: Sequence[str],
    entries: str,
    optional: Sequence[str] = (),
) -> list[str]:
    """What is wrong with an artifact's envelope: its schema, its run, and its keys.

    ``optional`` names the keys a caller writes itself, so an account being opened for the
    first time is read for what it claims — its schema and its run — rather than refused
    for the two keys that call is about to put there.
    """
    found = []
    if type(document.get("schema")) is not int or document.get("schema") != ARTIFACT_SCHEMA:
        found.append(
            f"its `schema` is {document.get('schema')!r}, and this reads schema {ARTIFACT_SCHEMA}"
        )
    if document.get("run") != run:
        found.append(f"its `run` is {document.get('run')!r} rather than {run!r}")
    if unknown := sorted(set(document) - set(keys)):
        found.append(f"it carries keys nothing reads: {', '.join(unknown)}")
    if missing := [key for key in keys if key not in document and key not in optional]:
        found.append(f"it is missing keys: {', '.join(missing)}")
    held = document.get(entries)
    if not isinstance(held, list) and not (entries not in document and entries in optional):
        found.append(f"its `{entries}` is not a list")
    return found


def _entry_problems(entry: object, keys: Sequence[str], at: int) -> list[str]:
    """Whether one entry of an artifact is an object carrying exactly the keys it owes."""
    if not isinstance(entry, Mapping):
        return [f"entry {at} is {type(entry).__name__}, not an object"]
    found = []
    if unknown := sorted(set(entry) - set(keys)):
        found.append(f"entry {at} carries keys nothing reads: {', '.join(unknown)}")
    if missing := [key for key in keys if key not in entry]:
        found.append(f"entry {at} is missing keys: {', '.join(missing)}")
    return found


def _prose(entry: Mapping[str, object], key: str, named: str) -> list[str]:
    """Whether one field of an entry is a non-empty line of prose."""
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        return [f"{named} states no `{key}`"]
    return []


def _disposed(
    entry: Mapping[str, object], expected: Sequence[str], at: int
) -> tuple[Disposed | None, list[str]]:
    """One entry read as a :class:`Disposed`, or the reasons it is not one.

    Every field is held to its own grammar, because the account is a document an agent
    wrote and another program reads: a draft nobody handed this dispatch, a word that is no
    disposition, a root cause that is not a slug, and a disposition linking a draft to
    nothing where it must are each a way the account stops accounting for the work.
    """
    draft = entry.get("draft")
    named = f"the disposition of {draft!r}"
    if not isinstance(draft, str) or draft not in expected:
        return None, [
            f"entry {at} names {draft!r}, which is not one of the drafts this dispatch was given"
        ]
    word = entry.get("disposition")
    if not isinstance(word, str) or word not in tuple(Disposition):
        return None, [
            f"{named} is {word!r}, which is not one of "
            + ", ".join(f"`{one.value}`" for one in Disposition)
        ]
    disposition = Disposition(word)
    found = _prose(entry, "detail", named)
    causes = entry.get("root_causes")
    if not isinstance(causes, list) or any(
        not isinstance(cause, str) or not SLUG.fullmatch(cause) for cause in causes
    ):
        found.append(f"{named} states a `root_causes` that is not a list of slugs")
    elif disposition.names_root_causes and not causes:
        found.append(
            f"{named} is `{word}` and names no root cause, so it links the draft to no "
            "ticket or issue"
        )
    elif not disposition.names_root_causes and causes:
        found.append(
            f"{named} is `{word}` and names root causes, which only "
            f"`{Disposition.FILED.value}` does"
        )
    if found:
        return None, found
    assert isinstance(causes, list)  # noqa: S101 - every other shape is a reason returned above
    return Disposed(
        draft=QualifiedDraftId(draft),
        disposition=disposition,
        root_causes=tuple(RootCause(str(cause)) for cause in causes),
        detail=str(entry["detail"]),
    ), []


def disposition_problems(
    document: Mapping[str, object],
    run: str,
    present: Sequence[QualifiedDraftId] = (),
    written: Mapping[str, Collection[str] | None] | None = None,
) -> list[str]:
    """Every way a disposition artifact fails to account for the run's drafts.

    A draft absent from it, or carrying more than one disposition, is named: those are the
    two ways an agent's account stops being one, and both were invisible while the account
    was prose. An entry naming a draft the input set does not hold is named too, since it
    is either a draft nobody handed this dispatch or a name it invented.

    ``present`` is the drafts still on disk when this is read, which the recorded set has
    to hold: the set is written before the dispatch and read after one that can write to
    it, so a draft struck from it would take itself out of the account unseen.

    ``written`` is the root causes this run holds a ticket for, each with the drafts that
    ticket names. A draft of this run a ticket names was consumed by it, so the recorded
    set has to hold it too, though its file is gone. A `filed` draft links to one of them
    or to nothing: a slug alone is only a word, and an account filing a draft under a root
    cause no ticket carries — or under one whose ticket never names it — says its evidence
    reached the board when nothing did.
    """
    found = _envelope_problems(document, run, DISPOSITION_KEYS, "dispositions")
    expected, problems = _drafts_recorded(document.get("drafts"), run)
    found.extend(problems)
    found.extend(
        f"the draft {draft} is one this dispatch holds and its `drafts` does not name, so "
        "the recorded input set is no longer the one this dispatch was given"
        for draft in present
        if draft not in expected
    )
    held = written or {}
    # A consumed draft's file is gone, so `present` cannot hold it; the ticket that
    # consumed it still names it, which is what keeps the set from shrinking unseen.
    own = f"{SOURCE}:{run}/{drafts.DRAFTS}/"
    found.extend(
        f"the draft {draft} is named by this run's ticket for {cause} and its `drafts` does "
        "not name it, so the recorded input set is no longer the one this dispatch was given"
        for cause, carried in sorted(held.items())
        for draft in carried or ()
        if draft.startswith(own) and draft not in expected
    )
    if found:
        return found
    entries = document["dispositions"]
    assert isinstance(entries, list)  # noqa: S101 - the envelope check above just proved it
    recorded: list[Disposed] = []
    for at, entry in enumerate(entries):
        if problems := _entry_problems(entry, DISPOSITION_ENTRY_KEYS, at):
            found.extend(problems)
            continue
        assert isinstance(entry, Mapping)  # noqa: S101 - `_entry_problems` proved it above
        read, problems = _disposed(entry, expected, at)
        found.extend(problems)
        if read is not None:
            recorded.append(read)
    found.extend(
        f"the disposition of {one.draft} files it under the root cause {cause}, and this run "
        f"holds no ticket for it at {TASKS_DIRECTORY}/{run}/{TICKETS}/{cause}{TICKET_SUFFIX}, "
        "so nothing carries its evidence to the board"
        for one in recorded
        for cause in one.root_causes
        if cause not in held
    )
    found.extend(
        f"the disposition of {one.draft} files it under the root cause {cause}, and that "
        "ticket's `drafts` does not name it, so the ticket carries none of its evidence"
        for one in recorded
        for cause in one.root_causes
        if (carried := held.get(cause)) is not None and one.draft not in carried
    )
    counted = Counter(one.draft for one in recorded)
    found.extend(
        f"the draft {draft} carries {counted[draft]} dispositions, and every draft carries one"
        for draft in expected
        if counted[draft] > 1
    )
    found.extend(
        f"the draft {draft} is absent from this account, so nothing says what became of it"
        for draft in expected
        if not counted[draft]
    )
    return found


def _response(
    entry: Mapping[str, object], quoted: Sequence[Quoted], feedback: Path, at: int
) -> tuple[Response | None, list[str]]:
    """One entry read as a :class:`Response`, or the reasons it is not one."""
    comment, issue = entry.get("comment"), entry.get("issue")
    named = f"the response to comment {comment!r}"
    same_id = [one for one in quoted if one.comment == comment]
    if not isinstance(comment, str) or not same_id:
        return None, [f"entry {at} names {comment!r}, which {feedback.name} quotes no comment"]
    held = [one for one in same_id if one.issue == issue]
    if not held:
        return None, [
            f"{named} names the issue {issue!r}, and {feedback.name} quotes that comment on "
            + ", ".join(one.issue for one in same_id)
        ]
    if found := _prose(entry, "action", named) + _prose(entry, "reply", named):
        return None, found
    return Response(
        comment=held[0], action=str(entry["action"]), reply=CommentId(str(entry["reply"]))
    ), []


def response_problems(
    document: Mapping[str, object], run: str, feedback: Path, quoted: Sequence[Quoted]
) -> tuple[list[str], list[Response]]:
    """Every way a response artifact fails to answer one gathering, and what it does answer.

    Order matters, because the feedback's own instruction is to act on each comment in the
    order it is quoted: an account out of order is an account of a different sequence. The
    responses come back so the board read below can be asked about the replies they name
    rather than about the comments alone.
    """
    found = _envelope_problems(document, run, RESPONSE_KEYS, "responses")
    if document.get("feedback") != feedback.name:
        found.append(
            f"its `feedback` is {document.get('feedback')!r} rather than {feedback.name!r}, so "
            "it answers some other gathering"
        )
    if found:
        return found, []
    entries = document["responses"]
    assert isinstance(entries, list)  # noqa: S101 - the envelope check above just proved it
    answered: list[Response] = []
    for at, entry in enumerate(entries):
        if problems := _entry_problems(entry, RESPONSE_ENTRY_KEYS, at):
            found.extend(problems)
            continue
        assert isinstance(entry, Mapping)  # noqa: S101 - `_entry_problems` proved it above
        read, problems = _response(entry, quoted, feedback, at)
        found.extend(problems)
        if read is not None:
            answered.append(read)
    counted = Counter(one.comment for one in answered)
    found.extend(
        f"the comment {one.comment} on {one.issue} carries {counted[one]} responses, and "
        "every quoted comment carries one"
        for one in counted
        if counted[one] > 1
    )
    found.extend(
        f"the comment {one.comment} on {one.issue} is absent from this account, so nothing "
        "says what was done about it"
        for one in quoted
        if not counted[one]
    )
    if not found and [one.comment for one in answered] != list(quoted):
        found.append(
            "the responses are not in the order the feedback quotes the comments: "
            f"{', '.join(one.comment.comment for one in answered)} against "
            f"{', '.join(one.comment for one in quoted)}"
        )
    return found, answered


def replies_posted(run: str, answered: Sequence[Response]) -> list[str]:
    """Every response whose reply the board does not hold, as it reads now.

    The artifact says a reply was posted and gives its id; this asks the board for it. A
    structured account of replies nobody posted is the one failure a shape check cannot
    see, and an id that is not the reply's is an account nobody can follow back to it.
    That the comment each reply answers is still there is :func:`gathering_on_board`'s
    question, which `check-responses` asks first.

    A comment a person edited after this run answered it is gathered again and owed a new
    reply, so only the replies at or after the comment's last change — the gatherer's own
    test of whether a reply answers it — are held to exactly one.
    """
    from . import follow_up_comments

    replies: dict[str, dict[CommentId, dict[CommentId, datetime]]] = {}
    changed: dict[str, dict[CommentId, datetime]] = {}
    for issue in sorted({one.comment.issue for one in answered}):
        held: dict[CommentId, dict[CommentId, datetime]] = {}
        dated: dict[CommentId, datetime] = {}
        for comment in plan_store.sdk(plan_store.client().task_comment_list(issue)).comments:
            identifier = CommentId(comment.id.model_dump())
            dated[identifier] = follow_up_comments.moment(
                comment.updated_at or comment.created_at, f"comment {identifier!r} on {issue}"
            )
            owner = comment_owner(comment.body)
            if owner is not None and owner.run == run and owner.answers is not None:
                held.setdefault(owner.answers, {})[identifier] = dated[identifier]
        replies[issue] = held
        changed[issue] = dated
    found = []
    for one in answered:
        # `gathering_on_board` has proven the comment is on its issue, so it is dated here.
        since = changed[one.comment.issue][one.comment.comment]
        every = replies[one.comment.issue].get(one.comment.comment, {})
        posted = {reply for reply, at in every.items() if at >= since}
        if not posted:
            found.append(
                f"the board holds no reply of run {run} answering comment {one.comment.comment} "
                f"on {one.comment.issue}"
            )
        elif one.reply not in posted:
            found.append(
                f"the response to comment {one.comment.comment!r} names the reply {one.reply}, "
                f"and the board holds run {run}'s reply to it as " + ", ".join(sorted(posted))
            )
        elif len(posted) != 1:
            found.append(
                f"the board holds {len(posted)} replies of run {run} answering comment "
                f"{one.comment.comment} on {one.comment.issue}, and it owes exactly one"
            )
    return found


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


def _keyed(keys: Sequence[str], *values: object) -> dict[str, object]:
    """``values`` under ``keys``, in order, which is how each contract's example is written.

    The example an agent fills in and the keys the validator reads back are one shape, so
    the example is written from those keys rather than beside them: a key added to the
    artifact and not to its example would leave the agent writing a document its own
    validator refuses, and ``strict`` fails here instead.
    """
    return dict(zip(keys, values, strict=True))


def disposition_contract(run: str) -> str:
    """C8, as the initial dispatch is told it: the account it owes for every input draft."""
    entry = _keyed(
        DISPOSITION_ENTRY_KEYS,
        f"{SOURCE}:{run}/{drafts.DRAFTS}/<draft-id>",
        f"<one of {', '.join(one.value for one in Disposition)}>",
        ["<root-cause>"],
        "<one or two sentences: why this draft became this, in your words>",
    )
    example = _keyed(
        DISPOSITION_KEYS,
        ARTIFACT_SCHEMA,
        run,
        ["<written for you; do not add to it, remove from it, or reorder it>"],
        [entry],
    )
    meanings = "".join(f"- **`{one.value}`** — {one.meaning}.\n" for one in Disposition)
    return (
        "**Every draft this dispatch was given is accounted for, exactly once.** The account "
        f"is a JSON document at `@DISPOSITIONS@`, which already exists: its `drafts` is the "
        "input set, written before you were dispatched, and it is not yours to change. Yours "
        "is `dispositions`, one entry per draft in `drafts`:\n\n"
        f"{meanings}\n"
        f"`root_causes` is a list of kebab-case root-cause slugs, one per ticket or open "
        f"issue the draft's evidence reached, each the file name of a ticket under "
        f"`@DRAFTS_ROOT@/{TASKS_DIRECTORY}/{run}/{TICKETS}/`; it is non-empty for "
        f"`{Disposition.FILED.value}` and empty for every other disposition. `detail` is "
        "always stated.\n\n"
        "The shape, with every placeholder to fill:\n\n"
        f"````json\n{json.dumps(example, indent=2)}\n````\n\n"
        "**`@CHECK_DISPOSITIONS@` is what says the account is complete.** Run it, correct the "
        f"document until it reports the account sound, and run it again. It exits {SOUND} "
        f"when every draft carries exactly one disposition with everything that disposition "
        f"owes, and {UNSOUND} naming each draft that is absent, that carries more than one, "
        "or whose disposition links it to no root cause where it must, to one this run "
        "holds no ticket for, or to a ticket whose `drafts` does not name it. This dispatch "
        "is not finished while it refuses.\n"
    )


def response_contract(run: str) -> str:
    """C9, as the feedback dispatch is told it: the account it owes for every quoted comment."""
    entry = _keyed(
        RESPONSE_ENTRY_KEYS,
        "<the comment id, exactly as the comment's section gives it>",
        "<the issue id, exactly as the comment's section gives it>",
        "<what this run did about the comment, or why it did nothing>",
        "<the id `@PLAN_STORE@ task comment add` printed for the reply>",
    )
    example = _keyed(RESPONSE_KEYS, ARTIFACT_SCHEMA, run, "@FEEDBACK_FILE@", [entry])
    return (
        "**Every comment this feedback quotes is accounted for, exactly once, in the order "
        "it is quoted.** The account is a JSON document at `@RESPONSES@`, which you write. "
        "One entry per quoted comment, in that order:\n\n"
        f"````json\n{json.dumps(example, indent=2)}\n````\n\n"
        "`action` is what you did before the reply went up, in your words; `reply` is the id "
        "of the reply you posted for that comment, which is the one the board gave it.\n\n"
        "**`@CHECK_RESPONSES@` is what says the account is complete.** Run it, correct the "
        f"document until it reports the account sound, and run it again. It exits {SOUND} "
        "when every quoted comment carries exactly one response, in order, naming the issue "
        "the feedback quotes it on, and the board holds a reply of this run answering it; "
        f"and {UNSOUND} naming each comment that is absent, answered twice, out of order, or "
        "whose reply the board does not hold. This dispatch is not finished while it "
        "refuses.\n"
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
    mode: Mode = Mode.INITIAL,
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
    feedback_file: Path | None = None,
    redispatch: bool,
    dispositions: Path | None = None,
    check_dispositions: str | None = None,
    responses: Path | None = None,
    check_responses: str | None = None,
) -> str:
    """One dispatch's task: ``template`` with every placeholder of ``mode`` filled, once.

    ``plan_store`` is the plan-store program every store instruction in the task is
    written with, and is refused unless it is an absolute path: the agent reads this task
    in a directory of its own, where a relative or bare name is answered by that
    dispatch's own search path. `scripts/follow-ups.sh` resolves it and says what that
    cost.

    The template is filled in a single pass, so what fills a placeholder is never read
    again for one — which is what brings the feedback into the task verbatim, whatever it
    quotes. The contracts and sections carry the run and the drafts root themselves, so
    those are filled into them first.

    Which placeholders a template owes is ``mode``'s, and a value a mode's template never
    names is not required: :data:`Mode.FEEDBACK`'s template names no ticket contract
    or board-wide listing, which keeps the comment-answering dispatch scoped to the
    tickets its quoted comments name.
    """
    named = PLACEHOLDER.findall(template)
    found = []
    if unknown := sorted(set(named) - set(mode.placeholders)):
        found.append(
            f"the {mode.value} task template names placeholders nothing fills: {', '.join(unknown)}"
        )
    if missing := [name for name in mode.placeholders if name not in named]:
        found.append(
            f"the {mode.value} task template is missing placeholders: {', '.join(missing)}"
        )
    if repeated := sorted(name for name in mode.sections if named.count(name) > 1):
        found.append(
            f"the {mode.value} task template names these more than once: {', '.join(repeated)}"
        )
    found.extend(_plan_store_problems(plan_store))
    found.extend(
        _mode_problems(
            mode, dispositions, check_dispositions, responses, check_responses, feedback_file
        )
    )
    if found:
        raise Refused(found)
    scalars = {
        "RUN": run,
        "BOARD": board,
        "DRAFTS_ROOT": str(drafts_root),
        "TICKET_METADATA_KEY": KEY,
        "VALIDATE": validate,
        "BOARD_STATUS": board_status,
        "BOARD_ITEMS": board_items,
        "COPY": copy,
        "CHECKOUT": str(checkout),
        "PLAN_STORE": plan_store,
        "ACCEPTED_STATUSES": accepted_statuses(),
        "ACCEPTED_FILTER": accepted_filter(),
        "FEEDBACK_FILE": "" if feedback_file is None else feedback_file.name,
        "DISPOSITIONS": str(dispositions),
        "CHECK_DISPOSITIONS": str(check_dispositions),
        "RESPONSES": str(responses),
        "CHECK_RESPONSES": str(check_responses),
    }
    values = {
        **scalars,
        "STATUS_VOCABULARY": status_vocabulary(),
        "TICKET_CONTRACT": _filled(ticket_contract(run, board), scalars),
        "COMMENT_CONTRACT": _filled(comment_contract(run, board), scalars),
        "DISPOSITION_CONTRACT": _filled(disposition_contract(run), scalars),
        "RESPONSE_CONTRACT": _filled(response_contract(run), scalars),
        "REDISPATCH": _filled(REDISPATCH, scalars) if redispatch else "",
        "FEEDBACK": _feedback_section(mode, feedback, scalars),
    }
    return PLACEHOLDER.sub(lambda matched: values[matched[1]], template)


def _feedback_section(mode: Mode, feedback: str | None, scalars: Mapping[str, str]) -> str:
    """What fills `@FEEDBACK@`: the gathering itself in feedback mode, a heading over it in initial.

    A feedback dispatch **is** the gathering, so the file stands as the task's own section
    and a heading calling it "feedback on the previous run" would read as an aside. An
    initial dispatch that carries feedback carries it as one, under that heading.
    """
    if feedback is None:
        return ""
    if mode is Mode.FEEDBACK:
        return feedback.rstrip() + "\n"
    return _filled(FEEDBACK, scalars) + feedback.rstrip() + "\n"


def _mode_problems(
    mode: Mode,
    dispositions: Path | None,
    check_dispositions: str | None,
    responses: Path | None,
    check_responses: str | None,
    feedback_file: Path | None,
) -> list[str]:
    """Every value a mode's own account needs that the caller did not give.

    Each names an artifact the dispatch is held to, so a task composed without one is a
    task whose acceptance criteria name a document at the word ``None``.
    """
    owed = {
        Mode.INITIAL: (
            ("--dispositions", dispositions),
            ("--check-dispositions", check_dispositions),
        ),
        Mode.FEEDBACK: (
            ("--feedback", feedback_file),
            ("--responses", responses),
            ("--check-responses", check_responses),
        ),
    }[mode]
    return [
        f"a task of the {mode.value} mode states its account at {flag}, and none was given"
        for flag, value in owed
        if value is None or (isinstance(value, str) and not value.strip())
    ]


def inventory(root: Path, run: str) -> tuple[int, int]:
    """How many drafts and tickets ``run`` holds under a `drafts` root."""
    base = root / TASKS_DIRECTORY / run
    return (
        len(list((base / drafts.DRAFTS).glob(f"*{TICKET_SUFFIX}"))),
        len(list((base / TICKETS).glob(f"*{TICKET_SUFFIX}"))),
    )


def _gathering_file(value: str) -> Path:
    """A `--feedback` path, refused when it names no file for an account to sit beside."""
    path = Path(value)
    if not path.name:
        raise argparse.ArgumentTypeError(
            f"{value!r} names no file, so no gathering is there and no account sits beside it"
        )
    return path


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
    opened = commands.add_parser(
        "open-dispositions",
        help="record the drafts a dispatch is given, and print its disposition artifact's path",
    )
    opened.add_argument("--root", type=Path, required=True, help=root_help)
    opened.add_argument("run", metavar="RUN-ID")
    disposition_location = commands.add_parser(
        "dispositions-path", help="print a run's disposition artifact path without writing it"
    )
    disposition_location.add_argument("--root", type=Path, required=True, help=root_help)
    disposition_location.add_argument("run", metavar="RUN-ID")
    accounted = commands.add_parser(
        "check-dispositions",
        help="validate that every draft a run was given carries one disposition",
    )
    accounted.add_argument("--root", type=Path, required=True, help=root_help)
    accounted.add_argument(
        "--board", metavar="SOURCE", help="also verify filed evidence on this board"
    )
    accounted.add_argument("run", metavar="RUN-ID")
    gathered = commands.add_parser(
        "check-gathering",
        help="validate that a file quotes comments one board holds, and nothing more",
    )
    gathered.add_argument("--board", required=True, metavar="SOURCE")
    gathered.add_argument("--feedback", type=_gathering_file, required=True, metavar="FILE")
    gathered.add_argument("run", metavar="RUN-ID")
    located = commands.add_parser(
        "responses-path",
        help="print where the response artifact answering one gathering is kept",
    )
    located.add_argument(
        "--feedback",
        type=_gathering_file,
        required=True,
        metavar="FILE",
        help="the gathered feedback file",
    )
    answered = commands.add_parser(
        "check-responses",
        help="validate that every comment one gathering quoted carries one response and a reply",
    )
    answered.add_argument("--board", required=True, metavar="SOURCE")
    answered.add_argument(
        "--feedback",
        type=_gathering_file,
        required=True,
        metavar="FILE",
        help="the gathered feedback file",
    )
    answered.add_argument("run", metavar="RUN-ID")
    task = commands.add_parser("compose", help="print one follow-up dispatch's task")
    task.add_argument(
        "--mode",
        default=Mode.INITIAL.value,
        choices=[one.value for one in Mode],
        help="which dispatch this task is for; the caller decides it, never the feedback's content",
    )
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
    task.add_argument("--dispositions", type=Path, metavar="PATH")
    task.add_argument("--check-dispositions", metavar="COMMAND")
    task.add_argument("--responses", type=Path, metavar="PATH")
    task.add_argument("--check-responses", metavar="COMMAND")
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


def _refused(named: str, problems: Sequence[str]) -> int:
    """Print one artifact's refusals, each on its own line, and answer :data:`UNSOUND`."""
    print(f"{PROG}: {named} does not account for this dispatch:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    return UNSOUND


def _accounted(arguments: argparse.Namespace) -> int:
    """Validate a run's disposition artifact, for the `check-dispositions` command."""
    path = dispositions_path(arguments.root, arguments.run)
    try:
        if not path.is_file():
            return _refused(
                str(path),
                [
                    "it does not exist, so nothing says what became of the drafts this "
                    "dispatch was given"
                ],
            )
        document = _artifact(path)
        problems = disposition_problems(
            document,
            arguments.run,
            draft_ids(arguments.root, arguments.run),
            written_tickets(arguments.root, arguments.run),
        )
        if not problems and arguments.board:
            # `disposition_problems` proved every entry and every root-causes list above.
            # JSON starts as objects, so these casts carry that proof into the board read.
            entries = cast(list[dict[str, object]], document["dispositions"])
            filed = {cause for entry in entries for cause in cast(list[str], entry["root_causes"])}
            problems = filed_board_problems(arguments.root, arguments.run, filed, arguments.board)
    except (OSError, Refused) as exc:
        print(f"{PROG}: refused: {exc}", file=sys.stderr)
        return UNRUNNABLE
    if problems:
        return _refused(str(path), problems)
    print(f"{PROG}: {path} accounts for every draft this dispatch was given")
    return SOUND


def _gathered(arguments: argparse.Namespace) -> int:
    """Validate that a file is a gathering, for the `check-gathering` command.

    The recipe runs it before it composes: a feedback mode task carries that file verbatim
    and its acceptance criteria rest on an account of the comments it quotes, so a file
    that is no gathering is one this dispatch should never be launched over.
    """
    feedback: Path = arguments.feedback
    try:
        gathering = read_gathering(_text(feedback))
        problems = gathering_problems(gathering, feedback, arguments.board, arguments.run)
        if not problems:
            problems = [
                f"{feedback.name} is not a gathering: {problem}"
                for problem in gathering_on_board(gathering, arguments.run)
            ]
    except OSError as exc:
        print(f"{PROG}: refused: {exc}", file=sys.stderr)
        return UNRUNNABLE
    if problems:
        return _refused(str(feedback), problems)
    print(f"{PROG}: {feedback} quotes {len(gathering.quoted)} comment(s) of {arguments.board}")
    return SOUND


def _answered(arguments: argparse.Namespace) -> int:
    """Validate a gathering's response artifact, for the `check-responses` command."""
    feedback: Path = arguments.feedback
    path = responses_path(feedback)
    try:
        gathering = read_gathering(_text(feedback))
        if problems := gathering_problems(gathering, feedback, arguments.board, arguments.run):
            return _refused(str(feedback), problems)
        quoted = gathering.quoted
        if not path.is_file():
            return _refused(
                str(path),
                [
                    "it does not exist, so nothing says what was done about the "
                    f"{len(quoted)} comment(s) {feedback.name} quotes"
                ],
            )
        problems, answered = response_problems(_artifact(path), arguments.run, feedback, quoted)
        # The gathering is held to the board first, so no issue it names is asked for its
        # replies until the board says this run may answer there.
        if not problems:
            problems = gathering_on_board(gathering, arguments.run, check_issue_title=False)
        if not problems:
            problems = replies_posted(arguments.run, answered)
    except (OSError, Refused) as exc:
        print(f"{PROG}: refused: {exc}", file=sys.stderr)
        return UNRUNNABLE
    if problems:
        return _refused(str(path), problems)
    print(f"{PROG}: {path} answers every comment {feedback.name} quotes")
    return SOUND


def _composed(arguments: argparse.Namespace) -> int:
    """Print the follow-up agent's task, for the `compose` command."""
    root: Path = arguments.root
    try:
        template = _text(arguments.template)
        feedback = None if arguments.feedback is None else _text(arguments.feedback)
        mode = Mode(arguments.mode)
        task = compose(
            template,
            mode=mode,
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
            feedback_file=arguments.feedback,
            redispatch=mode is Mode.FEEDBACK
            or feedback is not None
            or inventory(root, arguments.run)[1] > 0,
            dispositions=arguments.dispositions,
            check_dispositions=arguments.check_dispositions,
            responses=arguments.responses,
            check_responses=arguments.check_responses,
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
    """Validate, account, decide a status, list, copy, count or compose, for every caller."""
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
        case "responses-path":
            print(responses_path(arguments.feedback))
            return SOUND
        case _ if not RECORD_COMPONENT.fullmatch(arguments.run):
            print(f"{PROG}: refused: {arguments.run!r} is not a run id", file=sys.stderr)
            return UNRUNNABLE
        case "check-gathering":
            return _gathered(arguments)
        case "inventory":
            held_drafts, held_tickets = inventory(arguments.root, arguments.run)
            print(f"{held_drafts} {held_tickets}")
            return SOUND
        case "dispositions-path":
            print(dispositions_path(arguments.root, arguments.run))
            return SOUND
        case "open-dispositions":
            try:
                print(open_dispositions(arguments.root, arguments.run))
            except (OSError, Refused) as exc:
                print(f"{PROG}: refused: {exc}", file=sys.stderr)
                return UNRUNNABLE
            return SOUND
        case "check-dispositions":
            return _accounted(arguments)
        case "check-responses":
            return _answered(arguments)
        case "check-run":
            tickets = (arguments.root / TASKS_DIRECTORY / arguments.run / TICKETS).glob("*.md")
            return _validated(sorted(tickets))
        case _:
            return _composed(arguments)


if __name__ == "__main__":  # pragma: no cover - the module's own command line
    raise SystemExit(main())
