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
creates the agent's task from this host's `follow-up-task` template
(`templates/follow-up-task.md.j2`) — answering it with :func:`answers`, whose examples,
markers and :func:`shape` words are computed here rather than restated in the template's
prose — and names every ticket that fails the shape once an attached run settles. The
agent validates each ticket it writes through this module's `validate` command, and decides
the status it carries through its `board-status` command, before copying it. And the
journeys read the board back through :func:`from_store_item` and :func:`comment_owner`.

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
import tempfile
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
#: :data:`BINDING_FIELD`, the board item a ticket corresponds to. Schema 7 added the required
#: :data:`ESTIMATE_FIELD`, the priority :func:`estimate` computes, the optional
#: :data:`FREQUENCY_FIELD`, and the `## Impact` line stating the estimate and its reason.
SCHEMA = 7
#: Every earlier schema a re-dispatch brings a ticket forward from, enumerated rather than
#: admitted by range, so a record declaring any other schema — zero, negative, a later one or
#: no integer — is refused by name rather than read as an older ticket. `board-status` reads a
#: board item's record of one of these as storing no estimate.
PRIOR_SCHEMAS = (1, 2, 3, 4, 5, 6)
#: The schema just before, which `re-estimate` also reads as carrying no stored estimate and
#: no frequency judgment, bringing it to :data:`SCHEMA` as it writes.
PRIOR_SCHEMA = PRIOR_SCHEMAS[-1]
#: The schemas that added what a ticket of an earlier one lacks: `host`, `repositories`, and
#: the body's `## Impact` section, as :data:`SCHEMA`'s history states; :data:`RETIRED_AT` is
#: the one that retired headings.
HOST_AT, REPOSITORIES_AT, IMPACT_AT = 2, 3, 4

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


#: The open statuses a withdrawal never reaches, because a person decided on them; a closed
#: one is not withdrawn either, and is named as closed rather than listed.
_KEPT_OPEN = [
    f"`{board_option(status)}`"
    for status in Status
    if status.protected_from_withdrawal and not _PLACES[status].shown.startswith("Closed")
]

#: **The one status change a comment dispatch makes**, stated once: the feedback mode of the
#: task template carries it where it says which board changes are forbidden, answered from
#: here as `withdrawal`, and so does the preamble every gathering opens with
#: (`follow_up_comments.render`). It names no command path, because a gathering reaches the
#: task verbatim and is written before any command is resolved.
# llmlint: ignore-block[changed_behavior_has_e2e] This is the task's prose, and what it binds
# is driven where the tooling decides it: `board-status --withdraw` over a real `local-md`
# board refuses every protected status (`tests/test_follow_up_tickets.py`'s
# `test_a_withdrawal_of_a_ticket_a_person_accepted_or_deferred_is_refused_and_moves_nothing`,
# parametrized over each), and `tests/plan_tooling/test_follow_ups_comment_answers_e2e.py`
# drives a comment dispatch withdrawing its own `Proposal` and being refused at `Deferred`.
# Whether a comment clearly retires its ticket is the agent's reading, which no journey scripts.
WITHDRAWAL_EXCEPTION = (
    "**Never change a board item's status except by one withdrawal.** Where a quoted comment "
    "clearly says the ticket of the issue it sits on is not needed — that it is handled "
    "elsewhere, say, or will not be needed — and that issue is this run's own item at "
    f"`{board_option(Status.PROPOSED)}`, withdraw it: run `board-status` with `--withdraw` on "
    "its ticket, write the word it prints as the ticket's `status`, validate the ticket and "
    "copy it, and say in that comment's account entry that the item was withdrawn. A comment "
    "that does not clearly say so is replied to and changes nothing. No item at "
    f"{', '.join(_KEPT_OPEN[:-1])} or {_KEPT_OPEN[-1]}, and no closed one, is ever withdrawn, "
    "and `board-status --withdraw` refuses the ones a person decided on."
)
# llmlint: ignore-end[changed_behavior_has_e2e]


def accepted_statuses() -> str:
    """The accepted statuses as prose, each as its board option and store word."""
    named = [f"`{board_option(status)}` (`{status.value}`)" for status in Status if status.accepted]
    return ", ".join(named[:-1]) + f" and {named[-1]}"


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


class CopyLink(NamedTuple):
    """One entry of the store's :data:`COPIES_KEY`: the item a copy reached at one source."""

    source: str
    item: QualifiedBoardId


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
    "priority_estimate",
)
#: The required key holding the estimate :func:`estimate` computed when the ticket was last
#: written. Only `board-status` and `re-estimate` write it: an agent supplies facts, never a
#: priority, so every priority on the board can be explained from the ticket it sits on.
ESTIMATE_FIELD = "priority_estimate"
#: **Whether the root cause fires consistently.** Optional: absent means no judgment is
#: recorded, which a board item brought forward from schema 6 carries; a ticket about to be
#: copied carries one, judged from its original evidence.
FREQUENCY_FIELD = "frequency"

#: **Which board item a ticket corresponds to.** The one optional key of the record: the
#: native id of the board item the ticket is copied onto, as the store reports it after the
#: `<board>:`. `board-status` and `copy` write it — when the run creates its item or first
#: reaches it, and again, naming the run's own open item, when two board items carry the
#: ticket's origin — and nothing else does. The store's own correspondence cannot be trusted
#: alone: a timed-out copy can leave two items carrying one `onetaskgraph.origin`, and its
#: search by origin then updates whichever it lists first, a withdrawn duplicate included,
#: while the live issue keeps the old body. So the binding is held equal to the store's own
#: link, :data:`COPIES_KEY`, which a copy records on the ticket and follows on the next copy
#: with one read by id, and which the store alone writes: it records it on the item its copy
#: creates. A ticket bound to an item already on the board, which the store's link does not
#: name — the one case the store cannot be told which of several carriers is meant — is
#: steered by the ticket's own :data:`ORIGIN_KEY` naming its binding instead, which the store
#: follows by its origin rule; no ticket whose item a copy creates is given one. Both commands
#: refuse, naming both ids, when the link or the store's destination is any other item.
BINDING_FIELD = "board_item"
#: The keys a record may carry beside :data:`RECORD_KEYS`.
OPTIONAL_KEYS = (BINDING_FIELD, FREQUENCY_FIELD)
#: The front-matter key the store reads a `local-md` task's priority from, which a copy
#: carries onto the board item's `Priority` field.
PRIORITY_FIELD = "priority"
#: The store's reserved key a copy records the id it was copied from under, and which it
#: follows directly when it names the destination; the plan-store client's one spelling.
ORIGIN_KEY = plan_store.ORIGIN_KEY
#: The store's reserved key a copy records, on the ticket it copied, the item it reached at
#: each destination source under, and follows on the next copy: the store's link.
COPIES_KEY = plan_store.COPIES_KEY

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
#: The fourth line of a schema-7 `## Impact` section, after the three above: the priority
#: :func:`estimate` computed and why, as :func:`estimate_line` renders it.
ESTIMATE_LINE = "Priority estimate"
#: What a section holds from its first line on: the three lines in order, then the estimate
#: line when there is one, and nothing else.
IMPACT_TAIL = re.compile(
    "\n".join(rf"- {re.escape(label)}:[^\n]*" for label in IMPACT_LINES)
    + rf"(?:\n- {re.escape(ESTIMATE_LINE)}:[^\n]*)?\s*"
)


def impact_section(prose: str, severity: str, workaround: str, with_workaround: str) -> str:
    """The content of an `## Impact` section: ``prose``, then its three lines.

    The severities are words rather than :class:`Severity` members, so the example ticket
    :func:`ticket_example` renders can put a placeholder where each goes.
    """
    values = (severity, workaround, with_workaround)
    lines = "".join(
        f"- {label}: {value}\n" for label, value in zip(IMPACT_LINES, values, strict=True)
    )
    return f"{prose.strip()}\n\n{lines}"


class Priority(StrEnum):
    """A board item's priority as the store reports it, most urgent first; `none` is no value.

    The store's own vocabulary (onetaskgraph's `Priority`), which a `local-md` ticket carries
    in its front matter and a copy carries onto the board's `Priority` field. An estimate is
    always one of the four levels; `none` is only ever what a board item holds.
    """

    URGENT = "urgent"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


#: The level each severity with the workaround estimates at before any raise.
_BASE_PRIORITY = {
    Severity.CRITICAL: Priority.URGENT,
    Severity.HIGH: Priority.HIGH,
    Severity.MEDIUM: Priority.MEDIUM,
    Severity.LOW: Priority.LOW,
}
#: The four levels an estimate takes, most urgent first.
ESTIMATES = tuple(_BASE_PRIORITY.values())


class Frequency(StrEnum):
    """Whether a root cause fires every time its conditions hold, or only sometimes."""

    CONSISTENT = "consistent"
    INTERMITTENT = "intermittent"


#: How many occurrences raise an estimate one level. An occurrence is the ticket's own
#: evidence, plus each comment :func:`counts_as_occurrence` admits.
RAISE_AT = 3


def estimate(with_workaround: Severity, frequency: Frequency | None, occurrences: int) -> Priority:
    """The priority a ticket's facts estimate, which is the only way one is decided.

    The base is the severity with the workaround, one level for one level. It is raised one
    level, capped at `urgent`, when the root cause fires consistently or has been seen
    :data:`RAISE_AT` or more times, and in no other case.
    """
    base = ESTIMATES.index(_BASE_PRIORITY[with_workaround])
    raised = frequency is Frequency.CONSISTENT or occurrences >= RAISE_AT
    return ESTIMATES[max(base - 1, 0) if raised else base]


def _occurrence_words(occurrences: int) -> str:
    return f"{occurrences} occurrence" + ("" if occurrences == 1 else "s")


#: How each frequency judgment, or its absence, reads in the estimate line.
_FREQUENCY_WORDS = {
    Frequency.CONSISTENT: "fires consistently",
    Frequency.INTERMITTENT: "fires intermittently",
    None: "frequency not judged",
}


def estimate_line(with_workaround: Severity, frequency: Frequency | None, occurrences: int) -> str:
    """The `## Impact` estimate line for these facts: the level, then why.

    The one rendering, which every writer uses and :func:`parse_estimate_line` reads back: it
    names the severity with the workaround, the frequency judgment, the recounted occurrences,
    and whether the raise applied and why.
    """
    level = estimate(with_workaround, frequency, occurrences)
    causes = []
    if frequency is Frequency.CONSISTENT:
        causes.append("it fires consistently")
    if occurrences >= RAISE_AT:
        causes.append(f"it has {RAISE_AT} or more occurrences")
    if not causes:
        raise_words = "not raised"
    elif _BASE_PRIORITY[with_workaround] is Priority.URGENT:
        raise_words = f"already {Priority.URGENT}, so not raised though " + " and ".join(causes)
    else:
        raise_words = "raised one level because " + " and ".join(causes)
    return (
        f"- {ESTIMATE_LINE}: {level} (severity with the workaround {with_workaround}; "
        f"{_FREQUENCY_WORDS[frequency]}; {_occurrence_words(occurrences)}; {raise_words})"
    )


#: An estimate line's facts, read back so :func:`parse_estimate_line` can render it again.
#: The count is bounded so a digit run no recount reaches is no estimate line, rather than a
#: conversion Python refuses past its integer-string limit.
_ESTIMATE_VALUE = re.compile(
    rf"- {re.escape(ESTIMATE_LINE)}: (?P<level>[a-z]+) \(severity with the workaround "
    r"(?P<severity>[a-z]+); (?P<frequency>[a-z ]+); (?P<occurrences>[0-9]{1,6}) occurrences?; "
    r"[^\n]*\)"
)


class EstimateFacts(NamedTuple):
    """What an estimate line states: its level and the three facts it was computed from."""

    level: Priority
    with_workaround: Severity
    frequency: Frequency | None
    occurrences: int


def parse_estimate_line(line: str) -> EstimateFacts | None:
    """The facts ``line`` states, or `None` when it is not what :func:`estimate_line` renders.

    Read back by rendering again: a line is accepted only when it is byte for byte the line
    its own facts render, so a hand-edited level, count or reason is not an estimate line.
    """
    matched = _ESTIMATE_VALUE.fullmatch(line.strip())
    if matched is None or matched["severity"] not in tuple(Severity):
        return None
    judged = {words: frequency for frequency, words in _FREQUENCY_WORDS.items()}
    if matched["frequency"] not in judged:
        return None
    severity, frequency = Severity(matched["severity"]), judged[matched["frequency"]]
    occurrences = int(matched["occurrences"])
    if estimate_line(severity, frequency, occurrences) != line.strip():
        return None
    return EstimateFacts(
        estimate(severity, frequency, occurrences), severity, frequency, occurrences
    )


#: The `## Impact` heading and the lines a body's estimate line is placed among.
_IMPACT_HEADING = re.compile(rf"^## {re.escape(IMPACT)}[ \t]*$", re.MULTILINE)
_NEXT_HEADING = re.compile(r"^## ", re.MULTILINE)
_ESTIMATE_AT = re.compile(rf"^- {re.escape(ESTIMATE_LINE)}:[^\n]*$", re.MULTILINE)
_MITIGATED_AT = re.compile(rf"^- {re.escape(MITIGATED_LINE)}:(?P<value>[^\n]*)$", re.MULTILINE)


def _impact_span(body: str) -> tuple[int, int] | None:
    """Where ``body``'s `## Impact` section's content starts and ends, or `None` without one."""
    heading = _IMPACT_HEADING.search(body)
    if heading is None:
        return None
    following = _NEXT_HEADING.search(body, heading.end())
    return heading.end(), following.start() if following else len(body)


def stated_with_workaround(body: str) -> Severity | None:
    """The severity with the workaround ``body``'s `## Impact` section states, if it states one."""
    span = _impact_span(body)
    matched = _MITIGATED_AT.search(body, *span) if span else None
    value = matched["value"].strip() if matched else None
    return Severity(value) if value in tuple(Severity) else None


def with_estimate_line(body: str, line: str) -> str:
    """``body`` with its `## Impact` estimate line replaced by ``line``, and nothing else changed.

    A body carrying no estimate line — a schema-6 body — takes ``line`` directly after its
    `- Severity with the workaround:` line. :class:`Refused` for a body with neither.
    """
    span = _impact_span(body)
    if span is not None and (held := _ESTIMATE_AT.search(body, *span)) is not None:
        return body[: held.start()] + line + body[held.end() :]
    mitigated = _MITIGATED_AT.search(body, *span) if span else None
    if mitigated is None:
        raise Refused(
            [
                f"the body carries no `## {IMPACT}` section with a `- {MITIGATED_LINE}:` line, "
                "so there is no severity to estimate a priority from"
            ]
        )
    return body[: mitigated.end()] + "\n" + line + body[mitigated.end() :]


def follows_estimate(
    held: Priority | None, stored: Priority | None, estimated: Priority
) -> Priority:
    """**The lockstep rule**: the priority a ticket's board item is to carry.

    ``held`` is the priority the board item holds, `None` when there is no item yet;
    ``stored`` the estimate its record stored when it was last written, `None` for a schema-6
    record. The priority follows the new estimate while the board holds the stored one — or,
    with nothing stored, while it holds none — and is otherwise left as a person set it: a
    person who puts it back to the estimate hands it back to the estimate.
    """
    if held is None:
        return estimated
    following = held is (Priority.NONE if stored is None else stored)
    return estimated if following else held


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

#: The last line of a reply a follow-up run owns, naming the one comment it answers and
#: whether it judged that comment to confirm the root cause, and the visible line it opens
#: with when the board reports the answered comment's author, or not.
REPLY_MARKER = (
    '<!-- orchestrator:follow-up-comment run="{run}" root_cause="{root_cause}"'
    ' kind="{kind}" answers="{answers}" verdict="{verdict}" -->'
)
REPLY_OPENING = "Reply from follow-up run `{run}` to @{author}'s comment: {url}"
REPLY_OPENING_UNATTRIBUTED = "Reply from follow-up run `{run}` to the comment: {url}"

#: Either marker, as a last line: `kind`, `answers` and `verdict` are read, then held to the
#: grammar :func:`comment_owner` states.
COMMENT_MARKER_LINE = re.compile(
    r"<!-- orchestrator:follow-up-comment"
    r' run="(?P<run>[^"\s]+)" root_cause="(?P<root_cause>[^"\s]+)"'
    r'(?: kind="(?P<kind>[^"\s]*)")?(?: answers="(?P<answers>[^"]*)")?'
    r'(?: verdict="(?P<verdict>[^"]*)")? -->'
)

#: A comment's id as the board lists it: one or more characters, none of them whitespace,
#: `"` or `>`, so it cannot end the marker attribute or the marker that carries it.
COMMENT_ID = re.compile(r'[^\s">]+')

#: The suffix a ticket's file carries.
TICKET_SUFFIX = ".md"


class Mode(StrEnum):
    """Which dispatch a task is rendered for; each has its own account, and the template branches.

    **Initial** verifies a run's drafts and puts the tickets that stand on the board; it is
    what a launch with no gathered feedback renders. **Feedback** answers the board
    comments one gathering quoted and does nothing else: no inventory, no verification, no
    accepted-fix comparison, no status decision and no copy for a ticket no quoted comment
    names. Before the split there was one task, and a comment-only re-dispatch re-read
    and re-copied every unrelated ticket **after** its replies were already posted, which
    is how it reached the provider's deadline with the work it was dispatched for done.
    """

    INITIAL = "initial"
    FEEDBACK = "feedback"


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
    ``priority_estimate`` is the :data:`ESTIMATE_FIELD`, `None` only on a ticket read before
    `board-status` first writes it; ``frequency`` the :data:`FREQUENCY_FIELD`, `None` when no
    judgment is recorded; and ``priority`` the front matter's :data:`PRIORITY_FIELD`.
    ``origin`` and ``links`` are the store's own :data:`ORIGIN_KEY` and :data:`COPIES_KEY`, as
    read, so a rewrite of the ticket carries them through unchanged; ``links`` is sorted by
    source, empty until a copy records one.
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
    priority_estimate: Priority | None = None
    frequency: Frequency | None = None
    priority: Priority = Priority.NONE
    origin: plan_store.QualifiedTaskId | None = None
    links: tuple[CopyLink, ...] = ()

    def link(self, board: str) -> BoardItemId | None:
        """The native id of the item the store's link names on ``board``, or `None`."""
        for link in self.links:
            if link.source == board:
                return BoardItemId(link.item.removeprefix(f"{board}:"))
        return None


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


class Verdict(StrEnum):
    """Whether a reply's run judged the comment it answers to confirm the ticket's root cause.

    A confirming comment is one more occurrence of the root cause, which :func:`estimate`
    counts; a reply whose marker carries no verdict counts as not confirming.
    """

    CONFIRMS = "confirms"
    DOES_NOT_CONFIRM = "does-not-confirm"


class CommentOwner(NamedTuple):
    """What a follow-up run's comment marker names; ``answers`` and ``verdict`` are a reply's."""

    run: RunId
    root_cause: RootCause
    kind: CommentKind = CommentKind.EVIDENCE
    answers: CommentId | None = None
    verdict: Verdict | None = None


def qualified_id(run: str, root_cause: str) -> str:
    """The ticket of ``run`` for ``root_cause``, as the store addresses it."""
    return f"{SOURCE}:{run}/{TICKETS}/{root_cause}"


def ticket_path(root: Path, run: str, root_cause: str) -> Path:
    """Where that ticket's file is under a `drafts` root."""
    return root / TASKS_DIRECTORY / run / TICKETS / f"{root_cause}{TICKET_SUFFIX}"


def record(ticket: Ticket) -> dict[str, object]:
    """The metadata a ticket is stored under: every key present, each optional one once set."""
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
    if ticket.priority_estimate is not None:
        held[ESTIMATE_FIELD] = str(ticket.priority_estimate)
    if ticket.board_item is not None:
        held[BINDING_FIELD] = ticket.board_item
    if ticket.frequency is not None:
        held[FREQUENCY_FIELD] = str(ticket.frequency)
    return held


def dependency_entries(ticket: Ticket) -> list[dict[str, str]]:
    """The ticket's :data:`DEPENDENCY_FIELD` entries, one per accepted ticket it depends on."""
    return [{"id": held, "item": DEPENDENCY_ITEM} for held in ticket.depends_on]


def render(ticket: Ticket) -> str:
    """One ticket as the `local-md` record it is stored as.

    No `project`, and `repositories` naming exactly the record's `repository` — derived from
    it rather than held beside it, so nothing written here can make the two differ. The
    store's :data:`DEPENDENCY_FIELD` is written only when the ticket depends on something,
    and its :data:`ORIGIN_KEY` and :data:`COPIES_KEY` only as the ticket holds them, each in
    the one-line form the store itself writes into a `local-md` record.
    """
    fields: dict[str, object] = {
        "title": ticket.title,
        "status": ticket.status.value,
        PRIORITY_FIELD: str(ticket.priority),
        "repositories": [ticket.repository],
    }
    if ticket.depends_on:
        fields[DEPENDENCY_FIELD] = dependency_entries(ticket)
    metadata: dict[str, object] = {}
    if ticket.links:
        metadata[COPIES_KEY] = dict(ticket.links)
    if ticket.origin is not None:
        metadata[ORIGIN_KEY] = ticket.origin
    metadata[KEY] = record(ticket)
    fields["metadata"] = metadata
    return frontmatter(fields, ticket.body)


def repository_name(origin: str) -> str:
    """The last segment of a normalized origin, which a ticket's title opens with."""
    return origin.rsplit("/", 1)[-1]


def _is_run(value: object) -> bool:
    return isinstance(value, str) and RECORD_COMPONENT.fullmatch(value) is not None


def _is_slug(value: object) -> bool:
    return isinstance(value, str) and SLUG.fullmatch(value) is not None


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
        # DTZ007 is exempt: the spelling is UTC's own `Z`.
        datetime.strptime(value, drafts.DRAFTED_AT_FORMAT)  # noqa: DTZ007
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
    held: Mapping[str, object],
    *,
    run: str | None,
    root_cause: str | None,
    pending: bool = False,
    for_copy: bool = False,
) -> list[str]:
    """Every way a record is not the current one.

    ``pending`` reads it as `board-status` does before writing the estimate: a schema-6
    record, and one with no :data:`ESTIMATE_FIELD` yet, are both what it is about to bring
    forward. ``for_copy`` holds a ticket about to be copied to carrying a frequency judgment.
    """
    found = []
    readable = (SCHEMA, PRIOR_SCHEMA) if pending else (SCHEMA,)
    # Named before the missing keys, because a ticket of an older schema lacks the keys its
    # successor added, and the schema is what says why.
    if "schema" in held and (type(held["schema"]) is not int or held["schema"] not in readable):
        found.append(
            f"the record is schema {held['schema']!r}, and this reads schema {SCHEMA}; bring "
            "the ticket to the current shape"
        )
    owed = [key for key in RECORD_KEYS if not (pending and key == ESTIMATE_FIELD)]
    missing = [key for key in owed if key not in held]
    if missing:
        written = (
            f"; `{ESTIMATE_FIELD}` is written by `board-status`, which reads the ticket without it"
            if ESTIMATE_FIELD in missing
            else ""
        )
        return [*found, f"the `{KEY}` record is missing {', '.join(missing)}{written}"]
    if unexpected := sorted(key for key in held if key not in (*RECORD_KEYS, *OPTIONAL_KEYS)):
        found.append(
            f"the `{KEY}` record carries keys this does not write: {', '.join(unexpected)}"
        )
    if ESTIMATE_FIELD in held and held[ESTIMATE_FIELD] not in ESTIMATES:
        found.append(
            f"`{ESTIMATE_FIELD}` {held[ESTIMATE_FIELD]!r} is not one of "
            + ", ".join(f"`{level}`" for level in ESTIMATES)
            + "; leave it as `board-status` wrote it"
        )
    if FREQUENCY_FIELD in held and held[FREQUENCY_FIELD] not in tuple(Frequency):
        found.append(
            f"`{FREQUENCY_FIELD}` {held[FREQUENCY_FIELD]!r} is not one of "
            + ", ".join(f"`{one}`" for one in Frequency)
        )
    elif for_copy and FREQUENCY_FIELD not in held:
        found.append(
            f"the `{KEY}` record carries no `{FREQUENCY_FIELD}`, and a ticket about to be copied "
            f"carries one: `{Frequency.CONSISTENT}` when the root cause fires every time its "
            f"conditions hold, `{Frequency.INTERMITTENT}` when it fires only sometimes or in "
            "certain situations, judged from the original evidence"
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


def _links_problems(links: object) -> list[str]:
    """How the store's :data:`COPIES_KEY` an item carries is not the link a copy records.

    An object naming, per destination source, the qualified id of the item there; the store
    alone writes it, and :func:`render` only carries it through as the ticket already holds
    it, so any other shape is a hand edit.
    """
    if links is None:
        return []
    if not isinstance(links, Mapping) or not links:
        return [f"`{COPIES_KEY}` {links!r} is not an object naming a copy's destination items"]
    return [
        f"`{COPIES_KEY}` names {linked!r} for {source!r}, which is not an item of {source!r}; "
        "leave it as the store's copy wrote it"
        for source, linked in links.items()
        if not isinstance(linked, str)
        or (matched := QUALIFIED_ID.fullmatch(linked)) is None
        or matched["source"] != source
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


def _impact_problems(text: str, held: Mapping[str, object] | None) -> list[str]:
    """Every way an `## Impact` section's content is not prose followed by its lines.

    ``held`` is the record the estimate line is held to — present exactly once after the three
    severity lines, rendered as :func:`estimate_line` renders it, and stating the record's
    estimate from the severity with the workaround and the record's frequency. `None` reads
    the section as `board-status` does before writing that line, leaving it unchecked.
    """
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
    if held is not None:
        found.extend(_estimate_problems(text, held, severities.get(MITIGATED_LINE)))
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


def _estimate_problems(
    text: str, held: Mapping[str, object], mitigated: Severity | None
) -> list[str]:
    """How an `## Impact` section's estimate line disagrees with the record it sits beside."""
    where = f"the body's `## {IMPACT}` section"
    written = [line[0] for line in _ESTIMATE_AT.finditer(text)]
    if len(written) != 1:
        return [
            f"{where} carries its `- {ESTIMATE_LINE}:` line {len(written)} times, not once; "
            "`board-status` writes it"
        ]
    facts = parse_estimate_line(written[0])
    if facts is None:
        return [
            f"{where}'s `- {ESTIMATE_LINE}:` line is not as `board-status` renders it; leave it "
            "as that command wrote it"
        ]
    found = []
    stored = held.get(ESTIMATE_FIELD)
    if stored in ESTIMATES and facts.level != stored:
        found.append(
            f"{where}'s estimate line states `{facts.level}`, where the record's "
            f"`{ESTIMATE_FIELD}` is `{stored}`"
        )
    if mitigated is not None and facts.with_workaround is not mitigated:
        found.append(
            f"{where}'s estimate line names the severity with the workaround "
            f"`{facts.with_workaround}`, where the section states `{mitigated}`"
        )
    frequency = held.get(FREQUENCY_FIELD)
    if frequency in (None, *Frequency) and facts.frequency != frequency:
        found.append(
            f"{where}'s estimate line states the frequency as "
            f"{_FREQUENCY_WORDS[facts.frequency]!r}, where the record's `{FREQUENCY_FIELD}` is "
            f"{frequency!r}"
        )
    return found


def _body_problems(body: object, host: object, held: Mapping[str, object] | None) -> list[str]:
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
        if required == IMPACT and (impact := _impact_problems(found[at][1], held)):
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


def problems(  # noqa: PLR0913 - each reading of an item is its own keyword
    item: Mapping[str, object],
    *,
    run: str | None = None,
    root_cause: str | None = None,
    edges: Sequence[Edge] = (),
    pending: bool = False,
    for_copy: bool = False,
) -> list[str]:
    """Every way a store item, as `onetaskgraph task show --json` reports it, is not a ticket.

    ``run`` and ``root_cause`` are what the ticket's path says, when it was read from one,
    and ``edges`` what the store's dependency walk reports for it — none for a board item
    read without one. The record's problems come first, so a ticket of an older schema is
    named for its schema before anything its successor added. ``pending`` and ``for_copy``
    are :func:`_record_problems`'s: ``pending`` also leaves the estimate line unchecked.
    """
    found = []
    metadata = item.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    held = metadata.get(KEY)
    if isinstance(held, Mapping):
        found.extend(
            _record_problems(
                held, run=run, root_cause=root_cause, pending=pending, for_copy=for_copy
            )
        )
        found.extend(_origin_problems(metadata.get(ORIGIN_KEY), held))
        found.extend(_links_problems(metadata.get(COPIES_KEY)))
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
    if (priority := item.get(PRIORITY_FIELD, Priority.NONE)) not in tuple(Priority):
        found.append(
            f"the `{PRIORITY_FIELD}` is {priority!r}, which is not one of "
            + ", ".join(f"`{one}`" for one in Priority)
            + "; leave it as `board-status` wrote it"
        )
    found.extend(_title_problems(item.get("title"), repository))
    checked = None if pending or not isinstance(held, Mapping) else held
    found.extend(_body_problems(item.get("content"), host, checked))
    found.extend(edge_problems(edges))
    return found


def _category(status: object) -> object:
    """The category a store item's status reports, which is what a ticket's status is."""
    return status.get("category") if isinstance(status, Mapping) else status


def from_store_item(  # noqa: PLR0913 - each reading of an item is its own keyword
    item: Mapping[str, object],
    *,
    run: str | None = None,
    root_cause: str | None = None,
    edges: Sequence[Edge] = (),
    pending: bool = False,
    for_copy: bool = False,
) -> Ticket:
    """The ticket a store item holds, or :class:`Refused` naming every problem.

    ``edges`` is what the store's dependency walk reports for the item; a board item read
    with none reads back depending on nothing, which is what such a read is. ``pending`` and
    ``for_copy`` are :func:`problems`'s.
    """
    found = problems(
        item, run=run, root_cause=root_cause, edges=edges, pending=pending, for_copy=for_copy
    )
    if found:
        raise Refused(found)
    metadata = item["metadata"]
    # `problems` has just proven every shape narrowed here, and says so if it has not.
    assert isinstance(metadata, Mapping)  # noqa: S101
    held = metadata[KEY]
    assert isinstance(held, Mapping)  # noqa: S101
    basis = held["basis"]
    assert isinstance(basis, Mapping)  # noqa: S101
    links = metadata.get(COPIES_KEY) or {}
    assert isinstance(links, Mapping)  # noqa: S101
    origin = metadata.get(ORIGIN_KEY)
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
        priority_estimate=(Priority(str(held[ESTIMATE_FIELD])) if ESTIMATE_FIELD in held else None),
        frequency=Frequency(str(held[FREQUENCY_FIELD])) if FREQUENCY_FIELD in held else None,
        priority=Priority(str(item.get(PRIORITY_FIELD, Priority.NONE))),
        origin=None if origin is None else plan_store.QualifiedTaskId(str(origin)),
        links=tuple(
            sorted(
                CopyLink(str(source), QualifiedBoardId(str(linked)))
                for source, linked in links.items()
            )
        ),
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


class Stored(NamedTuple):
    """What the store holds for the ticket at one path: its item and its dependency edges.

    One read of a local ticket — `task show` and the `task deps` walk — taken once by a
    command and handed to everything in it that asks about the ticket, so no command reads
    its own ticket twice.
    """

    item: Mapping[str, object]
    edges: list[Edge]


def stored_ticket(path: Path) -> Stored:
    """The store's item and edges for the ticket at ``path``; :class:`Refused` it cannot read."""
    ticket = qualified_id(*located_path(path.absolute()))
    try:
        return Stored(plan_store.task_record(ticket), ticket_edges(ticket))
    except OSError as exc:
        raise Refused(
            [
                f"the store could not read {ticket} ({exc}); validate it from the "
                "environment the follow-ups launch exported, which names the drafts root"
            ]
        ) from None


def ticket_from(
    path: Path, stored: Stored, *, pending: bool = False, for_copy: bool = False
) -> Ticket:
    """The ticket ``stored`` holds for ``path``, held to being read from that path.

    :class:`Refused` for a ticket the store read from anywhere else, or one without the
    shape; ``pending`` and ``for_copy`` are :func:`problems`'s readings of it.
    """
    resolved = path.absolute()
    run, root_cause = located_path(resolved)
    location = stored.item.get("location")
    held = location.get("path") if isinstance(location, Mapping) else None
    if not isinstance(held, str) or Path(held).resolve() != resolved.resolve():
        raise Refused(
            [
                f"the store reads {qualified_id(run, root_cause)} from {held!r}, not from "
                f"{resolved}; validate a ticket under the drafts root the follow-ups launch "
                "exported"
            ]
        )
    return from_store_item(
        stored.item,
        run=run,
        root_cause=root_cause,
        edges=stored.edges,
        pending=pending,
        for_copy=for_copy,
    )


def read_ticket(path: Path, *, pending: bool = False, for_copy: bool = False) -> Ticket:
    """The ticket at ``path``, read through the installed store; :class:`Refused` otherwise.

    ``pending`` and ``for_copy`` are :func:`problems`'s readings of it.
    """
    return ticket_from(path, stored_ticket(path), pending=pending, for_copy=for_copy)


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


def dependency_problems(
    ticket: str, item: Mapping[str, object], board: str, edges: Sequence[Edge] | None = None
) -> list[str]:
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
    the far end of a mis-shaped edge is nothing this asks a board about. ``edges`` is the
    walk a caller already took for the ticket, which is then not taken again.
    """
    body = item.get("content")
    text = body if isinstance(body, str) else ""
    own_cause = _held_record(item).get("root_cause")
    accepted = ", ".join(f"`{status}`" for status in Status if status.accepted)
    edges = ticket_edges(ticket) if edges is None else list(edges)
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


#: The `board-items` flags that narrow a board query, one of which every query names.
NARROWING = ("--search", "--metadata", "--origin")


def board_items(
    board: str,
    *,
    search: str | None = None,
    metadata: Sequence[str] = (),
    origin: str | None = None,
    statuses: Sequence[str] = (),
) -> list[QualifiedTask]:
    """Every item of ``board`` one narrowing query selects, every page, in listing order.

    The one board query the follow-up agent is given, and never a listing of the whole board:
    reading every item of a `github-projects` board spends a share of GitHub's hourly allowance
    that grows with the board, and one ticket's filing once spent about 1,800 of its 5,000
    points. So every query names at least one of the store's native narrowing questions, which
    a board answers without enumerating its items — ``search``, GitHub's board-scoped issue
    search, token-matched and then substring-confirmed, which may not yet list an item written
    seconds ago; ``metadata``, each a `<KEY>[/<SEGMENT>…]=<VALUE>` every item kept holds; and
    ``origin``, the qualified id an item was copied from — and one naming none, or giving
    any of them a blank value, is :class:`Refused`. ``statuses`` keeps the categories named
    among what those select, and never selects on its own. A listing is a page, so this reads
    through
    :func:`plan_store.every_page`, which follows ``next`` until the store answers none,
    refuses a cursor it has already followed, and refuses a page any source could not answer.
    Each entry is the store's own typed model, which `board-items` prints as `task list --json`
    lists it under `items`.
    """
    blank = [
        flag
        for flag, values in (
            ("--search", [] if search is None else [search]),
            ("--metadata", list(metadata)),
            ("--origin", [] if origin is None else [origin]),
        )
        if any(not value.strip() for value in values)
    ]
    if blank:
        raise Refused(
            [
                f"a query of {board!r} gives {', '.join(blank)} a blank value, which narrows "
                "nothing and would list the whole board; name what it looks for"
            ]
        )
    if search is None and not metadata and origin is None:
        raise Refused(
            [
                f"a query of {board!r} names none of {', '.join(NARROWING)}, so it would list "
                "the whole board; name the text, the metadata value or the origin it looks for"
            ]
        )
    query: dict[str, object] = {"source": [board]}
    if search is not None:
        query["search"] = search
    if metadata:
        query["metadata"] = list(metadata)
    if origin is not None:
        query["origin"] = origin
    if statuses:
        query["status"] = list(statuses)
    pages = plan_store.every_page(f"the items of {board!r}", plan_store.client().task_list, **query)
    return [held for page in pages for held in page.items]


def root_cause_query(root_cause: str) -> str:
    """The `--metadata` value selecting the board items a ticket of ``root_cause`` was copied to."""
    return f"{KEY}/root_cause={root_cause}"


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
    """The board item a ticket's copy reaches, the category the board holds it at, and the item.

    All `None` for a ticket the board holds no item for, which a copy would create.
    ``record`` is the item as the store shows it by id — its priority, record and content —
    read in the same step, so the priority decision reads the item the status was read off.
    """

    item: BoardItemId | None
    category: str | None
    record: Mapping[str, object] | None = None


#: What one ticket's copy did to its item: the store's copy actions but `orphaned`, which a
#: copy reports for a destination item its source no longer holds, never for the one copied.
type CopyAction = Literal["created", "updated", "unchanged"]

#: The rules by which a copy of a bound ticket may find its item: the store's link, or the
#: origin a ticket written before the store kept a link carries. A search or a match could
#: reach any item carrying the ticket's origin, so either is refused for a bound ticket.
FOLLOWED = ("link", "origin")


class Copied(NamedTuple):
    """What one ticket's copy did, and to which item."""

    action: CopyAction
    item: BoardItemId


def _carriers(ticket: str, board: str) -> list[QualifiedTask]:
    """Every item of ``board`` whose store origin is ``ticket``: the store's native origin query.

    One narrow question, which the board answers without listing its items and which finds an
    item copied before the store kept a link as well as one copied since.
    """
    return board_items(board, origin=ticket)


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
        and metadata_owner(held.item.metadata) == ticket.created_by_run
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
    stops it corresponding is the binding, and the origin :func:`bind` points at the survivor,
    which every later copy follows instead; this comment says so on the closed issue, for the
    person who finds it.
    """
    named = survivor.item.url or survivor.id.root
    marker = DUPLICATE_MARKER.format(run=run, survivor=survivor.id.root)
    listed = plan_store.sdk(plan_store.client().task_comment_list(duplicate.id.root)).comments
    if any(comment.body.strip().endswith(marker) for comment in listed):
        return
    body = f"{DUPLICATE_OPENING.format(survivor=named, run=run)}\n\n{marker}\n"
    plan_store.sdk(plan_store.client().task_comment_add(duplicate.id.root, body=body))


def bind(
    path: Path,
    board: str,
    item: BoardItemId,
    *,
    pending: bool = False,
    ticket: Ticket | None = None,
) -> Ticket:
    """Write ``item`` as the ticket's binding into ``path``, and steer the store's copy to it.

    The binding is the record's :data:`BINDING_FIELD`. Where the store's own link,
    :data:`COPIES_KEY`, already names ``item`` it is what the next copy follows, and nothing
    else is written; the link is the store's alone to write. Otherwise ``item`` was already on
    the board, and the store's search for the ticket's origin would update whichever carrier
    it lists first, so the ticket's own :data:`ORIGIN_KEY` is pointed at ``item``, which the
    store follows by its origin rule. Nothing is written when the ticket already says all of
    it. ``pending`` reads the ticket as `board-status` does, before it writes the estimate.
    """
    ticket = dataclasses.replace(
        read_ticket(path, pending=pending) if ticket is None else ticket, board_item=item
    )
    if ticket.link(board) != item:
        ticket = dataclasses.replace(ticket, origin=plan_store.QualifiedTaskId(f"{board}:{item}"))
    rendered = render(ticket)
    if path.read_text(encoding="utf-8") != rendered:
        path.write_text(rendered, encoding="utf-8")
    return ticket


def _followed(ticket: Ticket, board: str) -> BoardItemId | None:
    """The board item the ticket names as its own, held to its binding; :class:`Misbound` else.

    That is its :data:`BINDING_FIELD`, else the store's link, and the two must agree: the link
    is where the store's next copy goes, so a link naming another item is a copy onto it.
    """
    linked = ticket.link(board)
    if ticket.board_item is not None and linked is not None and linked != ticket.board_item:
        raise Misbound(
            f"the store's `{COPIES_KEY}` link names {board}:{linked} as where this ticket is "
            f"copied, where its `{BINDING_FIELD}` binding is {board}:{ticket.board_item}: copy "
            "nothing and report it"
        )
    return ticket.board_item or linked


def correspond(
    path: Path,
    board: str,
    *,
    pending: bool = False,
    ticket: Ticket | None = None,
    reads: BoardReads | None = None,
) -> Placement:
    """Establish the item ``board`` holds for the ticket at ``path``, and the category it holds.

    An open bound item is read by id without discovery; its binding and the store's link
    must agree, and it must still carry the ticket's origin. Unbound tickets ask the native
    origin query once. Closed, missing or mismatched bindings use that same recovery path:
    two carriers must have one own open survivor beside withdrawn duplicates, each noted
    once. The item's record is reused when recovery returns to the id already read.
    "pending" selects the pre-estimate reading of a local ticket.
    """
    ticket = read_ticket(path, pending=pending, for_copy=not pending) if ticket is None else ticket
    run, root_cause = located_path(path.absolute())
    identifier = qualified_id(run, root_cause)
    named = _followed(ticket, board)
    reads = BoardReads() if reads is None else reads
    item: Mapping[str, object] | None = None
    missing: OSError | None = None
    if named is not None:
        try:
            item = reads.item(f"{board}:{named}")
        except OSError as exc:
            missing = exc
        if item is not None:
            metadata = item.get("metadata")
            carries = isinstance(metadata, Mapping) and metadata.get(ORIGIN_KEY) == identifier
            category = str(_category(item.get("status")))
            if carries and category not in (Status.FINISHED.value, Status.WITHDRAWN.value):
                bind(path, board, named, pending=pending, ticket=ticket)
                return Placement(named, category, item)
    carriers = _carriers(identifier, board)
    if missing is not None and not carriers:
        raise missing
    held = {_native(entry.id.root, board): entry for entry in carriers}
    bound = named
    if len(carriers) > 1:
        survivor = _survivor(ticket, carriers, board)
        for entry in carriers:
            if entry is not survivor:
                _note_duplicate(entry, survivor, ticket.created_by_run)
        bound = _native(survivor.id.root, board)
    elif bound is None and carriers:
        bound = next(iter(held))
    if bound is None:
        return Placement(None, None)
    if bound not in held:
        raise Misbound(
            f"the ticket names {board}:{bound} as its item, which does not carry this ticket's "
            f"origin {identifier}"
            + (f" where {', '.join(held)} does" if held else "")
            + ": copy nothing and report it"
        )
    bind(path, board, bound, pending=pending, ticket=ticket)
    if bound != named or item is None:
        item = reads.item(f"{board}:{bound}")
    return Placement(bound, str(_category(item.get("status"))), item)


def copy_ticket(path: Path, board: str) -> Copied:
    """Copy the ticket at ``path`` onto ``board``, onto its bound item alone.

    Nothing is read from the board first: the store follows the ticket's own link, or the
    origin a ticket written before the store kept a link carries, with one read by id, and
    :func:`copied_to` holds what it reports to the binding. :class:`Misbound` before anything
    is written for a ticket whose link and binding disagree, whose binding neither its link
    nor its origin names, or that carries a link and no binding — each is `board-status`'s to
    settle first. The item a first copy creates becomes the binding, beside the link the store
    recorded to it.
    """
    ticket = read_ticket(path, for_copy=True)
    bound = _followed(ticket, board)
    if ticket.board_item is None and bound is not None:
        raise Misbound(
            f"the store's `{COPIES_KEY}` link names {board}:{bound} for a ticket with no "
            f"`{BINDING_FIELD}` binding: run `board-status` on it, then copy it"
        )
    if bound is not None and ticket.link(board) is None and ticket.origin != f"{board}:{bound}":
        raise Misbound(
            f"neither the store's `{COPIES_KEY}` link nor the ticket's `{ORIGIN_KEY}` names its "
            f"`{BINDING_FIELD}` binding {board}:{bound}, so the store's copy would go wherever "
            "its search finds this ticket's origin: run `board-status` on it, then copy it"
        )
    run, root_cause = located_path(path.absolute())
    report = plan_store.sdk(
        plan_store.client().task_copy([qualified_id(run, root_cause)], to=board)
    )
    copied = copied_to(report, board, bound)
    if ticket.board_item is None:
        bind(path, board, copied.item)
    return copied


def copied_to(report: CopyReport, board: str, bound: BoardItemId | None) -> Copied:
    """The action one ticket's copy report names and the item it reached, held to ``bound``.

    A bound ticket's copy must have found its item by the ticket's own link or origin and
    reached the binding; any other rule, or any other item, is :class:`Misbound` naming both,
    so a store answering otherwise is refused by name rather than bound over. An unbound
    ticket's copy creates its item.
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
    via = str(getattr(outcome, "via", ""))
    if bound is not None and (copied.item != bound or via not in FOLLOWED):
        raise Misbound(
            f"the store copied this ticket onto {board}:{copied.item}, found by its {via} rule, "
            f"where its `{BINDING_FIELD}` binding is {board}:{bound} and a bound ticket's copy "
            f"follows its {' or '.join(FOLLOWED)}: report it"
        )
    return copied


def stored_estimate(item: Mapping[str, object]) -> Priority | None:
    """The estimate a board item's record stored, or `None` for a :data:`PRIOR_SCHEMAS` record.

    :class:`Refused` for a record of any schema neither those nor :data:`SCHEMA`, and for a
    schema-7 record storing none, or no level: every schema-7 record is written by
    `board-status` or `re-estimate` with one, so such a record is one edited by hand, and
    reading either as an older record would hand a person's priority back to the estimate.
    """
    held = _held_record(item)
    schema = held.get("schema")
    if type(schema) is int and schema in PRIOR_SCHEMAS:
        return None
    if type(schema) is not int or schema != SCHEMA:
        raise Refused(
            [
                f"the board item {item.get('id')!r} carries a `{KEY}` record of schema "
                f"{schema!r}, which no follow-up tool reads; report it rather than copying over it"
            ]
        )
    stored = held.get(ESTIMATE_FIELD)
    if stored not in ESTIMATES:
        raise Refused(
            [
                f"the board item {item.get('id')!r} carries a schema-{SCHEMA} `{KEY}` record "
                f"storing the `{ESTIMATE_FIELD}` {stored!r}, which is no estimate `board-status` "
                "or `re-estimate` writes; report it rather than copying over it"
            ]
        )
    return Priority(str(stored))


# llmlint: ignore[changed_behavior_has_e2e] No board can reach this refusal: the store's
# typed SDK refuses an answer whose priority is outside its vocabulary before this reads the
# item, so the guard stands for a reader handed a mapping by hand, and a unit test drives it.
def held_priority(item: Mapping[str, object]) -> Priority:
    """The priority the store reports a board item at; :class:`OSError` for any other word."""
    priority = item.get(PRIORITY_FIELD, Priority.NONE)
    if priority not in tuple(Priority):
        raise OSError(f"the store reported the priority {priority!r}, which is no priority")
    return Priority(str(priority))


def _record_frequency(held: Mapping[str, object], issue: str) -> Frequency | None:
    """The frequency judgment ``issue``'s record carries, `None` when it carries none.

    :class:`Refused` for a value that is no judgment, which is never read as its absence: a
    record anybody with the board's credential can edit would otherwise lose its raise.
    """
    if FREQUENCY_FIELD not in held:
        return None
    frequency = held[FREQUENCY_FIELD]
    if frequency not in tuple(Frequency):
        raise Refused(
            [
                f"{issue}'s `{KEY}` record carries the `{FREQUENCY_FIELD}` {frequency!r}, which is "
                "not one of " + ", ".join(f"`{one}`" for one in Frequency)
            ]
        )
    return Frequency(str(frequency))


def estimate_before_copy(
    path: Path,
    board: str,
    placement: Placement,
    ticket: Ticket | None = None,
    reads: BoardReads | None = None,
) -> Ticket:
    """Write the ticket's estimate, its `## Impact` estimate line and its priority into ``path``.

    The estimate is :func:`estimate` over the ticket's severity with the workaround, its
    frequency, and the occurrences recounted off the item ``placement`` names; the priority
    is :func:`follows_estimate` over what that item holds and what its record stored. The
    ticket is read as it was before this wrote anything, and written only when it changes.
    """
    ticket = read_ticket(path, pending=True) if ticket is None else ticket
    with_workaround = stated_with_workaround(ticket.body)
    # `read_ticket` has just held the `## Impact` section to its lines, and says so otherwise.
    assert with_workaround is not None  # noqa: S101
    item = placement.record
    issue = None if item is None else f"{board}:{item.get('id')}"
    count = occurrences(issue, ticket.created_by_run, ticket.root_cause, reads)
    estimated = estimate(with_workaround, ticket.frequency, count)
    priority = (
        estimated
        if item is None
        else follows_estimate(held_priority(item), stored_estimate(item), estimated)
    )
    decided = dataclasses.replace(
        ticket,
        priority_estimate=estimated,
        body=with_estimate_line(
            ticket.body, estimate_line(with_workaround, ticket.frequency, count)
        ),
        priority=priority,
    )
    rendered = render(decided)
    if path.read_text(encoding="utf-8") != rendered:
        path.write_text(rendered, encoding="utf-8")
    return decided


class ReEstimated(NamedTuple):
    """What one `re-estimate` decided for a board item: its estimate, count and priority."""

    item: str
    priority_estimate: Priority
    occurrences: int
    priority: Priority


def re_estimate(board: str, issue: str) -> ReEstimated:
    """Recompute one board item's estimate, and persist it and the lockstep rule's decision.

    The narrow write a run makes on an item another run owns, after it comments there: the
    item is read as the board holds it, its occurrences recounted off its comments, and the
    estimate recomputed from its body's severity with the workaround and its record's
    frequency. The priority is written with `task priority set` only when
    :func:`follows_estimate` has it follow to a new value, so a priority a person holds is
    never rewritten; then the content just read, with its estimate line replaced — or added
    after the severity lines where a schema-6 content has none — with `task content set`; and
    last the record, brought to :data:`SCHEMA`, with `task metadata set`. Nothing else about
    the item, its status included, is written. :class:`Refused` for an item that is no
    follow-up ticket this reads.
    """
    matched = QUALIFIED_ID.fullmatch(issue)
    if matched is None or matched["source"] != board:
        raise Refused([f"{issue!r} is not an item of the board {board!r}, as `<board>:<id>`"])
    reads = BoardReads()
    item = reads.item(issue)
    held = _held_record(item)
    creator = held.get("created_by_run")
    schema = held.get("schema")
    if type(schema) is not int or schema not in (SCHEMA, PRIOR_SCHEMA):
        raise Refused(
            [
                f"{issue} carries a `{KEY}` record of schema {schema!r}, and this reads schema "
                f"{PRIOR_SCHEMA} or {SCHEMA}; report it rather than estimating over it"
            ]
        )
    if not _is_run(creator):
        raise Refused(
            [
                f"{issue} carries no `{KEY}` record naming the run that created it, so it is "
                "no follow-up ticket this can estimate"
            ]
        )
    # The whole record is held to its shape before anything is written from it, as
    # `board-status` reads a ticket: a schema-6 record, or one storing no estimate yet, is
    # what this brings forward, and nothing else about it may be unsound.
    if unsound := _record_problems(held, run=None, root_cause=None, pending=True):
        raise Refused([f"{issue}'s `{KEY}` record is not sound: {problem}" for problem in unsound])
    # The item is written below as the ticket its record describes, so it has to be the item
    # copied from that ticket: the store's origin names it in the drafts source, and nothing
    # else identifies it. An origin on another source is a ticket's own, never a board item's.
    metadata = item.get("metadata")
    origin = metadata.get(ORIGIN_KEY) if isinstance(metadata, Mapping) else None
    matched = QUALIFIED_ID.fullmatch(origin) if isinstance(origin, str) else None
    if matched is not None and matched["source"] != SOURCE:
        raise Refused(
            [
                f"{issue} is not the item copied from the ticket its `{KEY}` record describes: "
                f"`{ORIGIN_KEY}` names {origin!r}, which is no `{SOURCE}` ticket"
            ]
        )
    if origin is None or (unsound := _origin_problems(origin, held)):
        raise Refused(
            [
                f"{issue} is not the item copied from the ticket its `{KEY}` record describes: "
                + ("it carries no `" + ORIGIN_KEY + "`" if origin is None else unsound[0])
            ]
        )
    content = item.get("content")
    text = content if isinstance(content, str) else ""
    with_workaround = stated_with_workaround(text)
    span = _impact_span(text)
    if with_workaround is None or span is None:
        raise Refused(
            [
                f"{issue}'s content states no `- {MITIGATED_LINE}:` severity in its "
                f"`## {IMPACT}` section, so there is nothing to estimate from"
            ]
        )
    # The whole body is written back below, so it is held to a ticket's shape first — every
    # section in its place with content, and the `## Impact` section to its lines: prose, the
    # three severity lines once each and in order, and at most one estimate line after them —
    # and a malformed one is refused rather than written back.
    if unsound := _body_problems(text, held.get("host"), None):
        raise Refused([f"{issue}'s content is not sound: {problem}" for problem in unsound])
    frequency = _record_frequency(held, issue)
    count = occurrences(issue, str(creator), str(held["root_cause"]), reads)
    estimated = estimate(with_workaround, frequency, count)
    board_priority = held_priority(item)
    priority = follows_estimate(board_priority, stored_estimate(item), estimated)
    client = plan_store.client()
    # The stored estimate is written last, because it is what the next re-estimate compares
    # the board's priority to: stored first, a refused priority write would leave it beside
    # the old priority, which every later touch would read as a person's and never raise.
    # Written last, a refusal leaves either the old priority beside the old estimate, still
    # following, or the priority already at the new estimate, which a retry leaves as it is.
    if priority is not board_priority:
        plan_store.sdk(client.task_priority_set(issue, priority.value))
    rewritten = with_estimate_line(text, estimate_line(with_workaround, frequency, count))
    if rewritten != text:
        with tempfile.TemporaryDirectory(prefix="re-estimate-") as scratch:
            written = Path(scratch) / "content.md"
            written.write_text(rewritten, encoding="utf-8")
            plan_store.sdk(client.task_content_set(issue, file=str(written)))
    record = {**held, "schema": SCHEMA, ESTIMATE_FIELD: estimated.value}
    plan_store.sdk(client.task_metadata_set(issue, KEY, json.dumps(record)))
    return ReEstimated(issue, estimated, count, priority)


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


def reply_marker(run: str, root_cause: str, answers: str, verdict: str) -> str:
    """The exact last line of ``run``'s reply to the comment whose id is ``answers``.

    ``root_cause`` is the one in the ticket record of the issue the reply is posted on, and
    ``verdict`` whether ``run`` judged that comment to confirm it.
    """
    return REPLY_MARKER.format(
        run=run, root_cause=root_cause, kind=CommentKind.REPLY, answers=answers, verdict=verdict
    )


def reply_opening(run: str, url: str, author: str | None) -> str:
    """The visible first line of ``run``'s reply to the comment at ``url``."""
    if author is None:
        return REPLY_OPENING_UNATTRIBUTED.format(run=run, url=url)
    return REPLY_OPENING.format(run=run, author=author, url=url)


def render_reply(  # noqa: PLR0913 - every part of a reply is its own keyword
    run: str,
    root_cause: str,
    *,
    answers: str,
    url: str,
    author: str | None,
    response: str,
    verdict: Verdict,
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
    marker = reply_marker(run, root_cause, answers, verdict)
    return f"{opening}\n\n{response.strip()}\n\n{marker}\n"


def comment_owner(body: str) -> CommentOwner | None:
    """What a comment's last line names, or `None` when no run owns it.

    Every part is held to its grammar, because the marker is stored text anybody with the
    board's credential can write: a value that is not a run id or a root-cause slug, a `kind`
    other than `reply`, a reply naming no id or one outside :data:`COMMENT_ID`, a verdict
    outside :class:`Verdict`, or an evidence marker naming an id or a verdict, names no run's
    comment. A marker with no `kind` is an evidence comment, which is every comment written
    before replies existed; a reply with no verdict is one written before verdicts existed.
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
    verdict = matched["verdict"]
    match matched["kind"], matched["answers"]:
        case None, None if verdict is None:
            return owner
        case CommentKind.REPLY, str(answers) if COMMENT_ID.fullmatch(answers) and (
            verdict is None or verdict in tuple(Verdict)
        ):
            return owner._replace(
                kind=CommentKind.REPLY,
                answers=CommentId(answers),
                verdict=None if verdict is None else Verdict(verdict),
            )
        case _:
            return None


def counts_as_occurrence(body: str, created_by_run: str, root_cause: str) -> bool:
    """Whether a comment on a ticket's issue counts toward the occurrences of ``root_cause``.

    Decided by what the comment's marker states: an evidence comment of any run but the one
    that created the issue counts, and so does a reply whose verdict is `confirms`, each only
    while its marker names the issue's own root cause. Nothing else does: the creating run's
    own evidence is the ticket's, a reply without a verdict or one not confirming is no
    occurrence, a marker naming another root cause is evidence about that one, and a comment
    no run's marker names — a person's, or a duplicate note — is not a run's judgment.
    """
    owner = comment_owner(body)
    if owner is None or owner.root_cause != root_cause:
        return False
    if owner.kind is CommentKind.REPLY:
        return owner.verdict is Verdict.CONFIRMS
    return owner.run != created_by_run


def occurrences(
    issue: str | None, created_by_run: str, root_cause: str, reads: BoardReads | None = None
) -> int:
    """How many times the root cause of the ticket at ``issue`` has been seen, recounted now.

    One for the ticket's own evidence, plus each comment :func:`counts_as_occurrence` admits,
    read off the board issue's comments at the moment of asking: nothing stores the count,
    so a comment added directly to the board is counted by the next computation. A ticket
    with no board item yet counts one.
    """
    if issue is None:
        return 1
    reads = BoardReads() if reads is None else reads
    return 1 + sum(
        counts_as_occurrence(body, created_by_run, root_cause) for body in reads.comments(issue)
    )


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
#: what follows and nowhere else — the task template renders each contract's example from
#: :func:`disposition_example` and :func:`response_example`, and the `check-dispositions`
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
    def names_root_causes(self) -> bool:
        """Whether this disposition has to link the draft to the root causes it supports."""
        return self is Disposition.FILED


#: Where a run's dispositions are kept: outside the `tasks/` tree the plan store reads, the
#: way a gathering's feedback is, so nothing here reaches the board as an item.
DISPOSITIONS_DIRECTORY = "dispositions"

#: The version of each artifact below. A reader refuses any other, since each is a stored
#: shape another program reads. The response artifact's schema 2 added each entry's
#: `verdict`, the one its reply's marker carries.
DISPOSITIONS_SCHEMA = 1
RESPONSES_SCHEMA = 2

DISPOSITION_KEYS = ("schema", "run", "drafts", "dispositions")
DISPOSITION_ENTRY_KEYS = ("draft", "disposition", "root_causes", "detail")
RESPONSE_KEYS = ("schema", "run", "feedback", "responses")
RESPONSE_ENTRY_KEYS = ("comment", "issue", "action", "reply", "verdict")

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
    verdict: Verdict


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


def read_run_tickets(root: Path, run: str) -> dict[str, Ticket | None]:
    """Read each local ticket once; a refused ticket cannot account for any draft."""
    held = (root / TASKS_DIRECTORY / run / TICKETS).glob(f"*{TICKET_SUFFIX}")
    found: dict[str, Ticket | None] = {}
    for path in (one for one in held if one.is_file()):
        try:
            found[path.stem] = read_ticket(path)
        except Refused:
            found[path.stem] = None
    return found


@dataclass
class BoardReads:
    """The records and comment bodies one account check has already read.

    Scoped to that check alone: a later check recounts comments afresh. Bodies are enough
    for both marker ownership and occurrence recounting; neither asks for comment identity.
    """

    items: dict[str, Mapping[str, object]] = dataclasses.field(default_factory=dict)
    bodies: dict[str, tuple[str, ...]] = dataclasses.field(default_factory=dict)

    def item(self, issue: str) -> Mapping[str, object]:
        """Keep a show's record and comments together, so the recount reuses its comments."""
        if issue not in self.items:
            answer = plan_store.complete(plan_store.sdk(plan_store.client().task_show(issue)))
            if len(answer.items) != 1:
                raise OSError(f"task show for {issue!r} returned {len(answer.items)} records")
            self.items[issue] = answer.items[0].item.model_dump(mode="python")
            if answer.comments is not None:
                self.bodies[issue] = tuple(comment.body for comment in answer.comments)
        return self.items[issue]

    def comments(self, issue: str) -> tuple[str, ...]:
        """Read an item's comments once for ownership and the estimate recount."""
        if issue not in self.bodies:
            self.bodies[issue] = tuple(
                comment.body
                for comment in plan_store.sdk(plan_store.client().task_comment_list(issue)).comments
            )
        return self.bodies[issue]


def filed_board_problems(
    root: Path,
    run: str,
    causes: Collection[str],
    board: str,
    tickets: Mapping[str, Ticket | None],
    details: Sequence[str] = (),
) -> list[str]:
    """Filed causes whose bound item or evidence comment did not reach the board soundly.

    Local tickets, board records and comments are shared within this check. A copied item's
    priority still agrees with its local ticket and every carrier's estimate is recounted.
    """
    found = []
    reads = BoardReads()
    for cause in sorted(causes):
        ticket = tickets[cause]
        if ticket is None:
            # The local shape was refused, so no board evidence can make this ticket sound.
            raise Refused([f"the filed root cause {cause} has no readable local ticket"])
        carrier = _own_carrier(ticket, qualified_id(run, cause), board, reads)
        copied = carrier is not None
        if carrier is None:
            named = tuple(
                dict.fromkeys(
                    match[0].rstrip(".,;)")
                    for detail in details
                    for match in re.finditer(rf"{re.escape(board)}:[^\s`<>\"]+", detail)
                )
            )
            carrier = _evidence_carrier(run, cause, board, reads, named)
        if carrier is None:
            found.append(
                f"the filed root cause {cause} has a local ticket but no bound item or "
                f"evidence comment of run {run} on {board}, so its evidence did not reach the board"
            )
            continue
        found.extend(board_estimate_problems(carrier, reads))
        if copied and (held := held_priority(reads.item(carrier))) != ticket.priority:
            found.append(
                f"{carrier} holds the priority `{held}`, where the ticket this run copied onto it "
                f"carries `{ticket.priority}`; copy the ticket again after `board-status`"
            )
    return found


def _own_carrier(ticket: Ticket, origin: str, board: str, reads: BoardReads) -> str | None:
    """The bound item when its record says it is this run's own copy; else `None`."""
    if ticket.board_item is None:
        return None
    bound = f"{board}:{ticket.board_item}"
    metadata = reads.item(bound).get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    if metadata.get(ORIGIN_KEY) == origin and metadata_owner(metadata) == ticket.created_by_run:
        return bound
    return None


def _evidence_carrier(
    run: str, cause: str, board: str, reads: BoardReads, named: Sequence[str] = ()
) -> str | None:
    """The item carrying this run's evidence marker, among the root-cause query's answers."""
    candidates = list(named)
    if not named:
        for item in board_items(board, metadata=[root_cause_query(cause)]):
            reads.items.setdefault(item.id.root, item.item.model_dump(mode="python"))
            candidates.append(item.id.root)
    for issue in candidates:
        if named and _held_record(reads.item(issue)).get("root_cause") != cause:
            continue
        if any(
            (owner := comment_owner(body)) is not None
            and owner.run == run
            and owner.root_cause == cause
            and owner.kind is CommentKind.EVIDENCE
            for body in reads.comments(issue)
        ):
            return issue
    return None


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
    document = _artifact(path) if existing else {"schema": DISPOSITIONS_SCHEMA, "run": run}
    # The two keys this writes are the two a first pass has yet to hold, so their absence
    # is what an artifact opened for the first time looks like rather than a refusal.
    found = _envelope_problems(
        document,
        run,
        DISPOSITION_KEYS,
        "dispositions",
        DISPOSITIONS_SCHEMA,
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
        "schema": DISPOSITIONS_SCHEMA,
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
    schema: int,
    optional: Sequence[str] = (),
) -> list[str]:
    """What is wrong with an artifact's envelope: its schema, its run, and its keys.

    ``optional`` names the keys a caller writes itself, so an account being opened for the
    first time is read for what it claims — its schema and its run — rather than refused
    for the two keys that call is about to put there.
    """
    found = []
    if type(document.get("schema")) is not int or document.get("schema") != schema:
        found.append(f"its `schema` is {document.get('schema')!r}, and this reads schema {schema}")
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
    found = _envelope_problems(document, run, DISPOSITION_KEYS, "dispositions", DISPOSITIONS_SCHEMA)
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
    found = _prose(entry, "action", named) + _prose(entry, "reply", named)
    verdict = entry.get("verdict")
    if verdict not in tuple(Verdict):
        found.append(
            f"{named} states the verdict {verdict!r}, where every response states one of "
            + ", ".join(f"`{one}`" for one in Verdict)
        )
    if found:
        return None, found
    return Response(
        comment=held[0],
        action=str(entry["action"]),
        reply=CommentId(str(entry["reply"])),
        verdict=Verdict(str(verdict)),
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
    found = _envelope_problems(document, run, RESPONSE_KEYS, "responses", RESPONSES_SCHEMA)
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
    test of whether a reply answers it — are held to exactly one. That reply's marker states
    the verdict its response states, and each issue a `confirms` reply sits on carries the
    estimate its comments, that reply included, recount to (:func:`board_estimate_problems`).
    """
    from . import follow_up_comments

    replies: dict[str, dict[CommentId, dict[CommentId, datetime]]] = {}
    changed: dict[str, dict[CommentId, datetime]] = {}
    # Keyed by issue as well as id: a board's comment ids may be unique within an issue alone.
    verdicts: dict[tuple[str, CommentId], Verdict | None] = {}
    causes: dict[tuple[str, CommentId], RootCause] = {}
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
                verdicts[issue, identifier] = owner.verdict
                causes[issue, identifier] = owner.root_cause
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
        elif (marked := verdicts[one.comment.issue, one.reply]) is not one.verdict:
            found.append(
                f"the response to comment {one.comment.comment!r} states the verdict "
                f"`{one.verdict}`, and its reply {one.reply}'s marker states "
                + ("none" if marked is None else f"`{marked}`")
            )
    if found:
        return found
    confirmed = sorted({one.comment.issue for one in answered if one.verdict is Verdict.CONFIRMS})
    # A confirmation counts toward its issue's recount only when its marker names the root
    # cause the issue's record describes, so one naming any other confirms nothing there.
    cause = {
        issue: _held_record(plan_store.task_record(issue)).get("root_cause") for issue in confirmed
    }
    for one in answered:
        if (
            one.verdict is Verdict.CONFIRMS
            and (named := causes[one.comment.issue, one.reply]) != cause[one.comment.issue]
        ):
            found.append(
                f"the response to comment {one.comment.comment!r} confirms, and its reply "
                f"{one.reply}'s marker names the root cause `{named}`, where {one.comment.issue}'s "
                f"record describes {cause[one.comment.issue]!r}; it confirms nothing there"
            )
    if found:
        return found
    return [problem for issue in confirmed for problem in board_estimate_problems(issue)]


def board_estimate_problems(issue: str, reads: BoardReads | None = None) -> list[str]:
    """How the board item ``issue`` carries an estimate other than its comments recount to now.

    Its record's :data:`ESTIMATE_FIELD` has to be :func:`estimate` over its body's severity
    with the workaround, its record's frequency and the occurrences recounted off its
    comments at this moment; and its `## Impact` estimate line has to be that estimate's line.
    """
    reads = BoardReads() if reads is None else reads
    item = reads.item(issue)
    held = _held_record(item)
    content = item.get("content")
    text = content if isinstance(content, str) else ""
    with_workaround = stated_with_workaround(text)
    creator = held.get("created_by_run")
    schema = held.get("schema")
    if type(schema) is not int or schema != SCHEMA:
        return [
            f"{issue} carries no schema-{SCHEMA} `{KEY}` record: it declares schema "
            f"{schema!r}; run `re-estimate` on it"
        ]
    cause = held.get("root_cause")
    if with_workaround is None or not _is_run(creator) or not _is_slug(cause):
        return [
            f"{issue} carries no schema-{SCHEMA} `{KEY}` record with a severity to estimate "
            "from; run `re-estimate` on it"
        ]
    try:
        frequency = _record_frequency(held, issue)
    except Refused as refusal:
        return list(refusal.problems)
    count = 1 + sum(
        counts_as_occurrence(body, str(creator), str(cause)) for body in reads.comments(issue)
    )
    estimated = estimate(with_workaround, frequency, count)
    found = []
    if held.get(ESTIMATE_FIELD) != estimated:
        found.append(
            f"{issue}'s record stores the estimate {held.get(ESTIMATE_FIELD)!r}, where its "
            f"{_occurrence_words(count)} recount to `{estimated}`; run `re-estimate` on it"
        )
    span = _impact_span(text)
    written = [line[0] for line in _ESTIMATE_AT.finditer(text, *span)] if span else []
    line = estimate_line(with_workaround, frequency, count)
    if written != [line]:
        found.append(
            f"{issue}'s `## {IMPACT}` estimate line reads {written!r}, where its record and "
            f"its {_occurrence_words(count)} render {line!r}; run `re-estimate` on it"
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
    f"exactly `{NO_WORKAROUND}` leaves the two the same. The fourth line, the priority "
    "estimate and why, is written by `board-status` and never by you>\n\n"
    + impact_section(
        "",
        "<the severity with no workaround applied>",
        "<the workaround in place and how it is applied, or none>",
        "<the severity that remains once the workaround is accounted for>",
    )
    + f"- {ESTIMATE_LINE}: <written by `board-status`: the estimate and why>",
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


def ticket_example(run: str, board: str) -> str:
    """The example ticket a verifying dispatch fills in, as the stored record renders it.

    It is :func:`render` over a :class:`Ticket` of placeholders rather than text written
    beside it, so the example an agent copies and the record the validator reads back are
    one shape: a key added to the record reaches the example with it.
    """
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
        # Placeholders where the record holds a word of a vocabulary: `record` and `render`
        # write each through `str`, which is the word for a member and the text for these.
        priority_estimate=cast(Priority, "<written by `board-status`, never by you>"),
        frequency=cast(
            Frequency, f"<`{Frequency.CONSISTENT}` or `{Frequency.INTERMITTENT}`: your judgment>"
        ),
        priority=cast(Priority, "<written by `board-status`, never by you>"),
    )
    return render(example)


def _keyed(keys: Sequence[str], *values: object) -> dict[str, object]:
    """``values`` under ``keys``, in order, which is how each account's example is written.

    The example an agent fills in and the keys the validator reads back are one shape, so
    the example is written from those keys rather than beside them: a key added to the
    artifact and not to its example would leave the agent writing a document its own
    validator refuses, and ``strict`` fails here instead.
    """
    return dict(zip(keys, values, strict=True))


def disposition_example(run: str) -> str:
    """C8's example: the disposition account an initial dispatch owes, as JSON."""
    entry = _keyed(
        DISPOSITION_ENTRY_KEYS,
        f"{SOURCE}:{run}/{drafts.DRAFTS}/<draft-id>",
        f"<one of {', '.join(one.value for one in Disposition)}>",
        ["<root-cause>"],
        "<one or two sentences: why this draft became this, in your words>",
    )
    example = _keyed(
        DISPOSITION_KEYS,
        DISPOSITIONS_SCHEMA,
        run,
        ["<written for you; do not add to it, remove from it, or reorder it>"],
        [entry],
    )
    return json.dumps(example, indent=2)


def response_example(run: str, feedback_file: str, plan_store: str) -> str:
    """C9's example: the response account a feedback dispatch owes, as JSON."""
    entry = _keyed(
        RESPONSE_ENTRY_KEYS,
        "<the comment id, exactly as the comment's section gives it>",
        "<the issue id, exactly as the comment's section gives it>",
        "<what this run did about the comment, or why it did nothing>",
        f"<the id `{plan_store} task comment add` printed for the reply>",
        f"<{Verdict.CONFIRMS} or {Verdict.DOES_NOT_CONFIRM}: the verdict the reply's marker "
        "carries>",
    )
    example = _keyed(RESPONSE_KEYS, RESPONSES_SCHEMA, run, feedback_file, [entry])
    return json.dumps(example, indent=2)


def shape(run: str) -> dict[str, object]:
    """The identifiers and numbers of the stored shapes, as the follow-up task names them.

    What the task's prose says about a ticket, a comment and an account is the template's
    text; the words and numbers the validators here read back are this module's, handed to
    the template as data, so the prose cannot name a field, a word or an exit status the
    validator does not. Each value is one this module decides elsewhere, spelled from it.
    """
    return {
        "drafts_source": SOURCE,
        "drafts_directory": drafts.DRAFTS,
        "tasks_directory": TASKS_DIRECTORY,
        "tickets_directory": TICKETS,
        "ticket_suffix": TICKET_SUFFIX,
        "ticket_id": qualified_id(run, "<root-cause>"),
        "record_key": KEY,
        "title_limit": TITLE_LIMIT,
        "schema": SCHEMA,
        "prior_schema": PRIOR_SCHEMA,
        "proposed": Status.PROPOSED.value,
        "deferred": Status.DEFERRED.value,
        "withdrawn": Status.WITHDRAWN.value,
        "accepted": ", ".join(f"`{status}`" for status in Status if status.accepted),
        "priority_field": PRIORITY_FIELD,
        "estimate_field": ESTIMATE_FIELD,
        "frequency_field": FREQUENCY_FIELD,
        "binding_field": BINDING_FIELD,
        "dependency_field": DEPENDENCY_FIELD,
        "dependency_item": DEPENDENCY_ITEM,
        "dependency_kind": DEPENDENCY_KIND,
        "origin_key": ORIGIN_KEY,
        "copies_key": COPIES_KEY,
        "headings": ", ".join(f"`## {heading}`" for heading in HEADINGS),
        "impact": IMPACT,
        "evidence": EVIDENCE,
        "suggested_fix": SUGGESTED_FIX,
        "retired_headings": list(RETIRED_HEADINGS),
        "rejected_fixes": REJECTED_FIXES,
        "estimate_line": ESTIMATE_LINE,
        "consistent": Frequency.CONSISTENT.value,
        "intermittent": Frequency.INTERMITTENT.value,
        "estimate_levels": ", ".join(
            f"`{severity}` is `{level}`" for severity, level in _BASE_PRIORITY.items()
        ),
        "urgent": Priority.URGENT.value,
        "raise_at": RAISE_AT,
        "confirms": Verdict.CONFIRMS.value,
        "does_not_confirm": Verdict.DOES_NOT_CONFIRM.value,
        "filed": Disposition.FILED.value,
        "not_reproducible": Disposition.NOT_REPRODUCIBLE.value,
        "already_fixed": Disposition.ALREADY_FIXED.value,
        "too_low_impact": Disposition.TOO_LOW_IMPACT.value,
        "exits": {
            "sound": SOUND,
            "unsound": UNSOUND,
            "unplaced": UNPLACED,
            "protected": PROTECTED,
            "outside_owner": OUTSIDE_OWNER,
            "not_accepted": NOT_ACCEPTED,
            "misbound": MISBOUND,
        },
    }


#: What a word cannot carry and still survive unquoted in the shell a store instruction is
#: run in: everything outside the set `shlex.quote` leaves alone. Every store instruction
#: in the task is shell embedded in Markdown and is read as written, so one of these in the
#: program word — a space, a quote, a backtick, a `$` — makes it several words or shell
#: syntax rather than one path, which is the same defect as a bare name by another route.
_NEEDS_QUOTING = re.compile(r"[^\w@%+=:,./-]", re.ASCII)


def _plan_store_problems(plan_store: str) -> list[str]:
    """Every way the program a task writes its store instructions with is not one.

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


def _value_problems(
    drafts_root: Path, checkout: Path, board: str, commands: Mapping[str, str]
) -> list[str]:
    """Every value the task's instructions embed that is not one those instructions can run.

    The two directories are where the dispatch reads drafts and runs every command, so a
    relative one would be answered by whichever directory the dispatch's turn runs in; the
    board is a word every store instruction passes as `--board`, so anything the shell
    would split reaches the dispatch as something other than that source; and a command the
    task tells the agent to run cannot be blank.
    """
    found = [
        f"the {named} {value} is not an absolute path, so the task would name a directory "
        "relative to wherever the dispatch runs"
        for named, value in (("drafts root", drafts_root), ("checkout", checkout))
        if not value.is_absolute()
    ]
    if not board or _NEEDS_QUOTING.search(board):
        found.append(
            f"the board {board!r} is not one word the shell a store instruction runs in "
            "reads as a source name"
        )
    found.extend(
        f"the {named} command is blank, so the task would tell the agent to run nothing"
        for named, command in commands.items()
        if not command.strip()
    )
    return found


def answers(
    *,
    mode: Mode = Mode.INITIAL,
    run: str,
    board: str,
    drafts_root: Path,
    validate: str,
    board_status: str,
    board_items: str,
    copy: str,
    re_estimate: str,
    checkout: Path,
    plan_store: str,
    feedback: str | None,
    feedback_file: Path | None = None,
    redispatch: bool,
    dispositions: Path | None = None,
    check_dispositions: str | None = None,
    responses: Path | None = None,
    check_responses: str | None = None,
) -> dict[str, object]:
    """One dispatch's answers to this host's `follow-up-task` template, for ``mode``.

    What the task says is the template's; these are the values it is rendered with and the
    data computed here — the examples, markers and shape words the validators read back —
    so a value and its validator cannot drift apart. The template renders `mode` and refuses
    any other, and `placement_note` is the caller's own to answer.

    ``plan_store`` is the plan-store program every store instruction in the task is
    written with, and is refused unless it is an absolute path to an executable that the
    shell reads as one word: the agent reads this task in a directory of its own, where a
    relative or bare name is answered by that dispatch's own search path.
    `scripts/follow-ups.sh` resolves it and says what that cost. The other values the
    instructions embed are held to what running them needs (:func:`_value_problems`), and
    each mode's own account is refused absent, since the task's criteria name it.

    The feedback is an answer rather than text spliced into the template, so what it quotes
    — a brace, a placeholder-shaped word — reaches the task verbatim, less its trailing
    whitespace.
    """
    found = _plan_store_problems(plan_store)
    found.extend(
        _value_problems(
            drafts_root,
            checkout,
            board,
            {
                "validate": validate,
                "board-status": board_status,
                "board-items": board_items,
                "copy": copy,
                "re-estimate": re_estimate,
            },
        )
    )
    found.extend(
        _mode_problems(
            mode, dispositions, check_dispositions, responses, check_responses, feedback_file
        )
    )
    if found:
        raise Refused(found)
    answered: dict[str, object] = {
        "mode": mode.value,
        "run": run,
        "board": board,
        "drafts_root": str(drafts_root),
        "checkout": str(checkout),
        "plan_store": plan_store,
        "validate": validate,
        "board_status": board_status,
        "board_items": board_items,
        "copy": copy,
        "re_estimate": re_estimate,
        "shape": shape(run),
        "evidence_marker": comment_marker(run, "<root-cause>"),
        "evidence_opening": comment_opening(run),
        "reply_marker": reply_marker(
            run,
            "<root-cause>",
            "<comment id>",
            f"<{Verdict.CONFIRMS} or {Verdict.DOES_NOT_CONFIRM}>",
        ),
        "reply_opening": reply_opening(run, "<comment URL>", "<author>"),
        "reply_opening_unattributed": reply_opening(run, "<comment URL>", None),
    }
    if feedback is not None:
        answered["feedback"] = feedback.rstrip()
    # `_mode_problems` refused every account value a mode owes that is absent, so each
    # below is present for its mode.
    if mode is Mode.INITIAL:
        answered |= {
            "redispatch": redispatch,
            "accepted_statuses": accepted_statuses(),
            "dispositions": str(dispositions),
            "check_dispositions": str(check_dispositions),
            "ticket_example": ticket_example(run, board),
            "disposition_example": disposition_example(run),
        }
    else:
        named = cast(Path, feedback_file).name
        answered |= {
            "withdrawal": WITHDRAWAL_EXCEPTION,
            "responses": str(responses),
            "check_responses": str(check_responses),
            "feedback_file": named,
            "response_example": response_example(run, named, plan_store),
        }
    return answered


def _mode_problems(
    mode: Mode,
    dispositions: Path | None,
    check_dispositions: str | None,
    responses: Path | None,
    check_responses: str | None,
    feedback_file: Path | None,
) -> list[str]:
    """Every value a mode's own account needs that the caller did not give.

    Each names an artifact the dispatch is held to, so a task rendered without one is a
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
        description="Validate follow-up tickets, and answer the follow-up agent's task template.",
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
        description=(
            "Read an open bound item by id without searching, including on re-dispatch. "
            "An unbound ticket asks once by origin; a closed, missing or mismatched binding "
            "uses that recovery query. Reuse its answer: never repeat an origin search."
        ),
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
    estimated = commands.add_parser(
        "re-estimate",
        help=(
            "recompute one board item's priority estimate from its comments, and write its "
            "record, its estimate line, and its priority where the lockstep rule has it follow"
        ),
    )
    estimated.add_argument("--board", required=True, metavar="SOURCE")
    estimated.add_argument("item", metavar="ITEM", help="the board item, as `<board>:<id>`")
    listing = commands.add_parser(
        "board-items",
        description=(
            "For an unbound ticket, ask once by root cause and once per distinct text question; "
            "reuse answers already obtained by tooling or the agent. Never duplicate-search "
            "a bound ticket. board-status owns its origin query."
        ),
        help=(
            "print every item of a board one narrowing query selects, every page, as one JSON "
            f"result; the query names at least one of {', '.join(NARROWING)}"
        ),
    )
    listing.add_argument("--board", required=True, metavar="SOURCE")
    listing.add_argument(
        "--search",
        metavar="TEXT",
        help=(
            "keep items whose title or body has it: the board's own issue search, token-matched, "
            "which may not yet list an item written seconds ago"
        ),
    )
    listing.add_argument(
        "--metadata",
        action="append",
        default=[],
        metavar="KEY[/SEGMENT...]=VALUE",
        help="keep items holding this metadata value; repeat for several, all held",
    )
    listing.add_argument(
        "--origin", metavar="SOURCE:ID", help="keep items copied from this qualified id"
    )
    listing.add_argument(
        "--status",
        action="append",
        default=[],
        choices=[status.value for status in Status],
        metavar="CATEGORY",
        help="of what the narrowing flags select, keep items at this status; repeat for several",
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
    task = commands.add_parser(
        "answers",
        help="print one follow-up dispatch's answers to the follow-up-task template, as YAML",
    )
    task.add_argument(
        "--mode",
        default=Mode.INITIAL.value,
        choices=[one.value for one in Mode],
        help="which dispatch this task is for; the caller decides it, never the feedback's content",
    )
    task.add_argument("--root", type=Path, required=True, help=root_help)
    task.add_argument("--run", required=True, metavar="RUN-ID")
    task.add_argument("--board", required=True, metavar="SOURCE")
    task.add_argument("--validate", required=True, metavar="COMMAND")
    task.add_argument("--board-status", required=True, metavar="COMMAND")
    task.add_argument("--board-items", required=True, metavar="COMMAND")
    task.add_argument("--copy", required=True, metavar="COMMAND")
    task.add_argument("--re-estimate", required=True, metavar="COMMAND")
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
            read_ticket(path, for_copy=True)
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
        local = read_run_tickets(arguments.root, arguments.run)
        problems = disposition_problems(
            document,
            arguments.run,
            draft_ids(arguments.root, arguments.run),
            {cause: None if ticket is None else ticket.drafts for cause, ticket in local.items()},
        )
        if not problems and arguments.board:
            # `disposition_problems` proved every entry and every root-causes list above.
            # JSON starts as objects, so these casts carry that proof into the board read.
            entries = cast(list[dict[str, object]], document["dispositions"])
            filed = {cause for entry in entries for cause in cast(list[str], entry["root_causes"])}
            problems = filed_board_problems(
                arguments.root,
                arguments.run,
                filed,
                arguments.board,
                local,
                [str(entry["detail"]) for entry in entries],
            )
    except (OSError, Refused) as exc:
        print(f"{PROG}: refused: {exc}", file=sys.stderr)
        return UNRUNNABLE
    if problems:
        return _refused(str(path), problems)
    print(f"{PROG}: {path} accounts for every draft this dispatch was given")
    return SOUND


def _gathered(arguments: argparse.Namespace) -> int:
    """Validate that a file is a gathering, for the `check-gathering` command.

    The recipe runs it before it creates the task: a feedback mode task carries that file verbatim
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


class Unanswered(NamedTuple):
    """Why one gathering's account does not answer it: the file at fault, and each reason."""

    path: Path
    problems: list[str]


def responses_unanswered(board: str, feedback: Path, run: str) -> Unanswered | None:
    """What `check-responses` refuses a gathering's account for, or `None` when it is sound.

    :class:`OSError` or :class:`Refused` when the gathering or the board cannot be read at
    all. `just follow-ups-answer-comments` asks this of every run it settled, in process,
    and the `check-responses` command reports the same answer.
    """
    path = responses_path(feedback)
    gathering = read_gathering(_text(feedback))
    if problems := gathering_problems(gathering, feedback, board, run):
        return Unanswered(feedback, problems)
    quoted = gathering.quoted
    if not path.is_file():
        return Unanswered(
            path,
            [
                "it does not exist, so nothing says what was done about the "
                f"{len(quoted)} comment(s) {feedback.name} quotes"
            ],
        )
    problems, answered = response_problems(_artifact(path), run, feedback, quoted)
    # The gathering is held to the board first, so no issue it names is asked for its
    # replies until the board says this run may answer there.
    if not problems:
        problems = gathering_on_board(gathering, run, check_issue_title=False)
    if not problems:
        problems = replies_posted(run, answered)
    return Unanswered(path, problems) if problems else None


def _answered(arguments: argparse.Namespace) -> int:
    """Validate a gathering's response artifact, for the `check-responses` command."""
    feedback: Path = arguments.feedback
    try:
        unanswered = responses_unanswered(arguments.board, feedback, arguments.run)
    except (OSError, Refused) as exc:
        print(f"{PROG}: refused: {exc}", file=sys.stderr)
        return UNRUNNABLE
    if unanswered is not None:
        return _refused(str(unanswered.path), unanswered.problems)
    print(f"{PROG}: {responses_path(feedback)} answers every comment {feedback.name} quotes")
    return SOUND


def _answered_template(arguments: argparse.Namespace) -> int:
    """Print the answers the follow-up agent's task is rendered from, for `answers`.

    JSON, which is YAML, so the store's `--answers` reads it and every string reaches the
    template byte for byte whatever it quotes.
    """
    root: Path = arguments.root
    try:
        feedback = None if arguments.feedback is None else _text(arguments.feedback)
        mode = Mode(arguments.mode)
        answered = answers(
            mode=mode,
            run=arguments.run,
            board=arguments.board,
            drafts_root=root,
            validate=arguments.validate,
            board_status=arguments.board_status,
            board_items=arguments.board_items,
            copy=arguments.copy,
            re_estimate=arguments.re_estimate,
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
    json.dump(answered, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return SOUND


def _listed(arguments: argparse.Namespace) -> int:
    """Print every item a narrowing board query selects, for the `board-items` command."""
    try:
        items = board_items(
            arguments.board,
            search=arguments.search,
            metadata=arguments.metadata,
            origin=arguments.origin,
            statuses=arguments.status,
        )
    except (OSError, Refused) as exc:
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
        stored = stored_ticket(arguments.path)
        item = stored.item
        if owner is not None and not under_owner(
            repository := ticket_repository(ticket, item), owner
        ):
            raise OutsideOwner(repository, owner)
        if unheld := dependency_problems(ticket, item, arguments.board, stored.edges):
            raise NotAccepted(unheld)
        local = ticket_from(arguments.path, stored, pending=True)
        reads = BoardReads()
        placement = correspond(
            arguments.path, arguments.board, pending=True, ticket=local, reads=reads
        )
        status = status_before_copy(placement.category, withdraw=arguments.withdraw)
        if placement.item is not None:
            local = dataclasses.replace(
                local,
                board_item=placement.item,
                origin=(
                    local.origin
                    if local.link(arguments.board) == placement.item
                    else plan_store.QualifiedTaskId(f"{arguments.board}:{placement.item}")
                ),
            )
        estimate_before_copy(arguments.path, arguments.board, placement, local, reads)
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


def _re_estimated(arguments: argparse.Namespace) -> int:
    """Recompute and persist one board item's estimate, for the `re-estimate` command."""
    try:
        decided = re_estimate(arguments.board, arguments.item)
    except Refused as refusal:
        print(f"{PROG}: {arguments.item}: {refusal}", file=sys.stderr)
        return UNSOUND
    except OSError as exc:
        print(f"{PROG}: refused: {exc}", file=sys.stderr)
        return UNRUNNABLE
    json.dump(
        {
            "item": decided.item,
            ESTIMATE_FIELD: decided.priority_estimate.value,
            "occurrences": decided.occurrences,
            PRIORITY_FIELD: decided.priority.value,
        },
        sys.stdout,
    )
    sys.stdout.write("\n")
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
    """Validate, account, decide a status, list, copy, count or answer, for every caller."""
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
        case "re-estimate":
            return _re_estimated(arguments)
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
            return _answered_template(arguments)


if __name__ == "__main__":  # pragma: no cover - the module's own command line
    raise SystemExit(main())
