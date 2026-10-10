"""Stateful loopback Projects board and request budgets shared by real store journeys.

Only the GitHub wire is simulated: the installed store, follow-up commands and recipes
remain real. Keeping this fixture outside test modules avoids unrelated test cache inputs.
"""

from __future__ import annotations

import bisect
import json
import os
import re
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar, NamedTuple, NewType

import follow_up_variables
from follow_up_ticket_shape import FIX, current_section, impact_prose
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator import follow_up_tickets, plan_store
from orchestrator.root import REPO_ROOT

#: The same project as every recipe here names it: qualified by the `authoring` source
#: `onetaskgraph.yaml` roots at this checkout's `.plans`, which these journeys point
#: elsewhere per run. Derived rather than restated, so the native id has one source.
AUTHORING_SOURCE = "authoring"


#: Where that source's root is named, for the store and for every process it spawns.
#: One spelling, because a journey pointing it one way and a helper reading it another
#: would leave a plan authored in one directory and approved in a second.
AUTHORING_ROOT_ENV = f"ONETASKGRAPH_SOURCES__{AUTHORING_SOURCE.upper()}__CONFIG__ROOT"


#: The credential the tracked `hellopatient` Linear source names.
LINEAR_KEY_ENV = "HELLOPATIENT_LINEAR_API_KEY"
#: Where every board journey points that source: a loopback port nothing listens on, so a
#: request to it is refused on this machine and never reaches the production workspace.
UNSERVED_LINEAR = "http://127.0.0.1:9/graphql"
#: Where the `hellopatient-followups` source's endpoint is named, which the store spells with
#: the source name's hyphen as an underscore.
LINEAR_FOLLOWUPS_ENDPOINT_ENV = "ONETASKGRAPH_SOURCES__HELLOPATIENT_FOLLOWUPS__CONFIG__ENDPOINT"


@dataclass(frozen=True)
class _Repository:
    """One GitHub repository, in the two halves every call about it is spelled with.

    Named rather than carried as a pair because both halves travel together through
    three different spellings — the `owner/name` the configuration file holds, the two
    variables GitHub's own repository lookup takes, and the `nameWithOwner` an issue
    answers — and a pair says nothing about which of the two came first.
    """

    owner: str
    name: str

    @classmethod
    def parse(cls, value: str, source: str) -> _Repository:
        owner, _, name = value.partition("/")
        if not owner or not name or "/" in name:
            raise ValueError(f"{source} names {value!r}, which is not one `owner/name`")
        return cls(owner=owner, name=name)

    def __str__(self) -> str:
        return f"{self.owner}/{self.name}"


def _configured_repository() -> _Repository:
    """The repository the committed `plans` source names.

    Read out of the file under test rather than restated here: what the copy journeys
    assert is that the *configured* repository is the one a create reaches, so a
    fixture holding its own copy of that value would still pass with the file naming
    another repository — or none.
    """
    text = (REPO_ROOT / "onetaskgraph.yaml").read_text(encoding="utf-8")
    named = re.search(r"^\s+repository:\s*(\S+)\s*$", text, re.MULTILINE)
    if named is None:
        raise ValueError("onetaskgraph.yaml names no repository for its `plans` source")
    return _Repository.parse(named.group(1), "onetaskgraph.yaml's `plans` source")


CONFIGURED_REPOSITORY = _configured_repository()


def _configured_project_number() -> int:
    """The board number the committed `plans` source names.

    Read out of the file under test for the reason the repository above is: an issue
    carries its board membership on `projectItems`, and the source keeps only the entry
    whose `project.number` is the one it was configured with. A fixture holding its own
    copy of that number would answer every issue as held by this board however the file
    had been repointed, which is the one thing a membership read exists to tell apart.
    """
    text = (REPO_ROOT / "onetaskgraph.yaml").read_text(encoding="utf-8")
    named = re.search(r"^\s+project_number:\s*(\d+)\s*$", text, re.MULTILINE)
    if named is None:
        raise ValueError("onetaskgraph.yaml names no project_number for its `plans` source")
    return int(named.group(1))


CONFIGURED_PROJECT_NUMBER = _configured_project_number()


def _followups_project_number() -> int:
    """The board number the committed `followups` source names, read for the same reason."""
    text = (REPO_ROOT / "onetaskgraph.yaml").read_text(encoding="utf-8")
    opened = text.index("\n  followups:\n")
    named = re.compile(r"^\s+project_number:\s*(\d+)\s*$", re.MULTILINE).search(text, opened)
    if named is None:
        raise ValueError("onetaskgraph.yaml names no project_number for its `followups` source")
    return int(named.group(1))


FOLLOWUPS_PROJECT_NUMBER = _followups_project_number()


#: A second repository of the configured owner the fixture knows, so a task naming it is
#: filed somewhere the configured fallback is not — the placement the adopted release
#: buys, which a fixture answering one node id for every lookup could never observe.
SIBLING_REPOSITORY = _Repository(owner=CONFIGURED_REPOSITORY.owner, name="oneharness")


#: The node identifiers GitHub gives the things on one board, each a type of its own
#: because the writes under test address different ones: a field value is set on a
#: board item, a sub-issue link names issue content, a status is chosen among one
#: field's options, and `createIssue` takes a repository a board has none of. Spelling
#: them all `str` would let this fixture answer a source that had confused two of them
#: exactly as it answers one that had not, which is the confusion it exists to catch.
_BoardNodeId = NewType("_BoardNodeId", str)


_BoardItemId = NewType("_BoardItemId", str)


_IssueNodeId = NewType("_IssueNodeId", str)


_FieldOptionId = NewType("_FieldOptionId", str)


_RepositoryNodeId = NewType("_RepositoryNodeId", str)


#: The board field the source owns and reads a copy's origin back out of, and the
#: `Status` field every board carries. A category this board cannot represent refuses
#: the write naming it, so the options below are the `plans` board's own: the two the
#: shipped mapping reaches by name, the four `onetaskgraph.yaml` states — `Queued`, and
#: `Done` and `Cancelled`, which a `done` or `cancelled` write selects before it closes
#: the issue, and `Needs attention`, which it sends `unknown` to — and `Backlog`.
ORIGIN_FIELD_NAME = "onetaskgraph.origin"


class _BoardField(StrEnum):
    """The board fields a copy writes, as the ids GitHub answers them under.

    An enum rather than bare constants because what a write *means* is decided by which
    field it names, and that is one dispatch: `_Board.set_field` matches on it once. A bare
    constant cannot say so, because an undotted name in a `case` pattern captures rather
    than compares — so constants would force a repeated conditional in its place.
    """

    STATUS = "FIELD_status"
    ORIGIN = "FIELD_origin"
    PRIORITY = "FIELD_priority"


@dataclass(frozen=True)
class _StatusOption:
    id: _FieldOptionId
    name: str

    def rendered(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name}

    def rendered_in_full(self) -> dict[str, str]:
        """The option as the guarded field setup reads it, colour and description included."""
        return self.rendered() | {"color": "GRAY", "description": ""}


# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] The stand-in board answers
# the option `onetaskgraph.yaml` names, because the journey asserts that name reaches the
# wire. Reconciling it against the live board would take the board credential no test may use.
NEEDS_ATTENTION = _StatusOption(id=_FieldOptionId("OPT_attention"), name="Needs attention")
# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] The option
# `onetaskgraph.yaml` maps `queued` to on both sources, answered by the stand-in so the
# journeys can assert that name reaches the wire. Reconciling it against either live board
# would take the board credential no test may use.
QUEUED = _StatusOption(id=_FieldOptionId("OPT_queued"), name="Queued")
# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] The options
# `onetaskgraph.yaml` sends `done` and `cancelled` to on both sources, answered by the
# stand-in so the journeys can assert that each name reaches the wire beside the close it
# pairs with: a terminal write selects its mapped option and then closes the issue, and a
# board lacking the option refuses by name before either. Reconciling them against either
# live board would take the board credential no test may use.
DONE = _StatusOption(id=_FieldOptionId("OPT_done"), name="Done")


CANCELLED = _StatusOption(id=_FieldOptionId("OPT_cancelled"), name="Cancelled")
# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


STATUS_OPTIONS: tuple[_StatusOption, ...] = (
    _StatusOption(id=_FieldOptionId("OPT_todo"), name="Todo"),
    QUEUED,
    _StatusOption(id=_FieldOptionId("OPT_progress"), name="In Progress"),
    DONE,
    CANCELLED,
    _StatusOption(id=_FieldOptionId("OPT_backlog"), name="Backlog"),
    NEEDS_ATTENTION,
)


# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] The live `followups`
# board's own option, which `onetaskgraph.yaml` sends a new ticket's `backlog` to. The
# stand-in answers it so the journey can assert that name reaches the wire; reconciling it
# against that board would take the board credential no test may use.
PROPOSAL = _StatusOption(id=_FieldOptionId("OPT_proposal"), name="Proposal")
# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] The live `followups`
# board's own option, which `onetaskgraph.yaml` sends a deferred ticket's `draft` to. The
# stand-in answers it so the journeys can assert that name reaches the wire and reads back;
# reconciling it against that board would take the board credential no test may use.
DEFERRED = _StatusOption(id=_FieldOptionId("OPT_deferred"), name="Deferred")
# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


REPOSITORY_NODE_IDS: dict[_Repository, _RepositoryNodeId] = {
    CONFIGURED_REPOSITORY: _RepositoryNodeId("R_ai_orchestrator"),
    SIBLING_REPOSITORY: _RepositoryNodeId("R_oneharness"),
}


def _repository_of(node_id: object) -> _Repository:
    for repository, known in REPOSITORY_NODE_IDS.items():
        if known == node_id:
            return repository
    raise ValueError(f"createIssue names repository {node_id!r}, which no lookup answered")


#: A project somebody wrote on the board by hand, carrying one sub-issue. A board issue
#: is a project when it has sub-issues or the source's own item-kind marker, so a board
#: whose items were empty would answer `project list` with nothing — the board's own
#: title is not a project and is never read as one.
BOARD_PROJECT_TITLE = "Board-authored plan"


BOARD_TASK_TITLE = "Read the board back"


@dataclass
class _Issue:
    """One issue on the board, under the two node ids GitHub gives it.

    `item_id` is the board's row and `content_id` is the issue itself, and the writes
    address different ones: a field value is set on the row, while a sub-issue link and
    an issue update name the content. Collapsing them into one id would let a fixture
    pass a source that confused the two.
    """

    item_id: _BoardItemId
    content_id: _IssueNodeId
    #: The issue's number in its repository, which GitHub answers on every issue and the
    #: adopted plan store reads as a board task's `key`: an issue answering none is data
    #: the source refuses to represent, so each one here carries its own.
    number: int
    title: str
    body: str
    parent_id: _IssueNodeId | None = None
    sub_issues: int = 0
    #: Where this issue lives, which is what its `repository.nameWithOwner` answers. A
    #: created issue takes the repository its `createIssue` named, because the source
    #: reads a parent's repository back off the board to place a task naming none and to
    #: refuse one under another owner — so an issue that answered the configured
    #: repository whatever it was created in would hide both from the journeys.
    repository: _Repository = CONFIGURED_REPOSITORY
    #: Whether this issue is closed, and why, as the last write left it. Applied rather than
    #: only recorded for the reason `status` gives: `done` and `cancelled` are closed states
    #: on a board, each selecting its option and then closing the issue, so an issue that
    #: answered `OPEN` for ever would report a finished ticket as unfinished to every reader
    #: that asks the store rather than the wire.
    state: str = "OPEN"
    state_reason: str | None = None
    #: The text this row's `onetaskgraph.origin` field holds, as the last copy wrote it. Kept
    #: and answered back the way GitHub does, because that value is how a re-copy finds the
    #: item it already made — a row that forgot it would have every re-copy create a
    #: replacement, and a journey about updating in place would prove nothing.
    origin: str | None = None
    #: The Status option this row is at: where a person last moved it, or where the last
    #: write put it. Applied rather than only recorded, because a read-back is what the
    #: store's own `delivers` relation decides on — it computes a delivered ticket's status
    #: and writes nothing when the board already reads that way, so a board that answered
    #: the option it started at would report every release as `unchanged`.
    status: str = "Todo"
    #: The issues blocking this one, as the copy's `addBlockedBy` left them. Recorded and
    #: answered back rather than dropped, because a plan's `deps` travel onto a board as this
    #: relation alone: a fixture that took the write and then answered the read empty would
    #: hand the engine a plan whose nodes all run at once.
    blocked_by: list[_IssueNodeId] = field(default_factory=list)
    #: The `Priority` option this row holds, `None` for no value, as the last write left it.
    priority: str | None = None
    #: The comments on this issue, oldest first, as GitHub lists an issue's `comments`.
    comments: list[dict[str, object]] = field(default_factory=list)
    #: The board holding this issue, set by that board as it takes the issue on. Two boards
    #: are served at once for the launch journey — this repository's `plans` and its
    #: `followups` — and both the Status options a row answers with and the project number
    #: its membership is filed under are the *holding* board's, so an issue that read a
    #: module-level board would answer for whichever was reset last.
    board: _Board | None = None

    @property
    def held_by(self) -> _Board:
        """The board holding this issue, which every answer about its row is that board's."""
        if self.board is None:
            raise ValueError(f"issue {self.content_id!r} was never taken on by a board")
        return self.board

    def priority_value(self) -> dict[str, object] | None:
        """This row's `Priority` value as GitHub selects one, or `None` for no value."""
        if self.priority is None:
            return None
        return {
            "name": self.priority,
            "field": {
                "id": _BoardField.PRIORITY,
                "name": "Priority",
                "options": [option.rendered() for option in self.held_by.priorities],
            },
        }

    def field_values(self) -> dict[str, object]:
        """This row's board field values, as both routes to it select them.

        One method rather than a copy per route: the board's own `items` connection and
        an issue's `projectItems` select the same field values, and the whole of what
        the source relies on is that an issue reached either way resolves to one item.
        """
        origin = (
            []
            if self.origin is None
            else [
                {
                    "text": self.origin,
                    "field": {"id": _BoardField.ORIGIN, "name": ORIGIN_FIELD_NAME},
                }
            ]
        )
        held = self.priority_value()
        priority = [] if held is None else [held]
        return {
            "nodes": [
                {
                    "name": self.status,
                    "field": {
                        "id": _BoardField.STATUS,
                        "name": "Status",
                        "options": [option.rendered() for option in self.held_by.options],
                    },
                },
                *priority,
                *origin,
            ],
            "pageInfo": {"hasNextPage": False},
        }

    def content(self) -> dict[str, object]:
        """The issue itself, as every document that reaches it selects it."""
        return {
            "__typename": "Issue",
            "id": self.content_id,
            # llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] GitHub's schema
            # has no machine-readable copy here. The installed plan store is what this is
            # reconciled against on every journey — it refuses an issue answering no number —
            # and its producer holds the document to a pinned schema in its `tests/schema.rs`.
            "number": self.number,
            # llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]
            "title": self.title,
            "body": self.body,
            "url": f"https://github.com/{self.repository}/issues/{self.number}",
            "createdAt": ISSUE_CREATED_AT,
            "updatedAt": self.updated_at(),
            "state": self.state,
            "stateReason": self.state_reason,
            "repository": {"nameWithOwner": str(self.repository)},
            "parent": None if self.parent_id is None else {"id": self.parent_id},
            "subIssuesSummary": {"total": self.sub_issues},
            "labels": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        }

    def updated_at(self) -> str:
        """When this issue last changed, which GitHub moves when one of its comments changes.

        The board-scoped search answering a `commented_since` query narrows on it, so an issue
        that kept the moment it was created would never be found commented on.
        """
        return max([ISSUE_CREATED_AT, *(str(comment["updatedAt"]) for comment in self.comments)])

    def item(self) -> dict[str, object]:
        """This issue as a row of the board's own `items` connection."""
        return {
            "id": self.item_id,
            "fieldValues": self.field_values(),
            "content": self.content(),
        }

    def board_issue(self) -> dict[str, object]:
        """This issue as the source reaches it away from the board's item connection.

        The board half rides along on `projectItems` — the row's own id and its field
        values — which is what lets a search, a node-id read and a sub-issue read all
        resolve to the item a board walk would have produced. The membership is filed
        under the configured board's number, because an entry for any other board is
        one this source is required not to answer for.
        """
        return self.content() | {
            "projectItems": {
                "nodes": [
                    {
                        "id": self.item_id,
                        "project": {"id": self.held_by.node_id, "number": self.held_by.number},
                        "fieldValues": self.field_values(),
                    }
                ],
                "pageInfo": {"hasNextPage": False},
            }
        }


# llmlint: ignore-block[e2e_not_mocked] The task requires a loopback GitHub board: a
# real API would read or write the live followups board shared by all runs. This
# stateful wire fixture answers the installed store's real GraphQL documents; the
# store CLI, follow-up commands, recipes and shell remain real.
class _BatchedWrite(NamedTuple):
    """One field write the source's batched document may carry, under an alias of its own."""

    #: The root field's alias in the answer, which is where the source reads it back.
    alias: str
    #: The variable carrying the write's input.
    variable: str
    #: The boolean variable its `@include(if:)` reads, or `None` for the write always sent.
    flag: str | None

    def included(self, variables: dict[str, object]) -> bool:
        return self.flag is None or variables.get(self.flag) is True


#: The three field writes of the source's batched document, in the order it lists them.
BATCHED_WRITES = (
    _BatchedWrite("updateProjectV2ItemFieldValue", "input", None),
    _BatchedWrite("second", "second", "writeSecond"),
    _BatchedWrite("third", "third", "writeThird"),
)


class _Board:
    """One Projects v2 board, mutated by the writes the source performs against it.

    Stateful because the operations under test are a sequence rather than one call:
    a copy resolves the configured repository, creates an issue in it, files that
    issue on the board and then files the project's tasks under it as sub-issues, and
    each of those reads the board again. A handler that answered one canned document
    would report a board the writes never reached.
    """

    node_id: ClassVar[_BoardNodeId] = _BoardNodeId("PVT_fixture")
    title: ClassVar[str] = "AI Orchestrator"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        parent = _Issue(
            item_id=_BoardItemId("PVTI_board_plan"),
            content_id=_IssueNodeId("I_board_plan"),
            number=1,
            title=BOARD_PROJECT_TITLE,
            body="A plan an operator wrote on the board itself.",
            sub_issues=1,
        )
        child = _Issue(
            item_id=_BoardItemId("PVTI_board_task"),
            content_id=_IssueNodeId("I_board_task"),
            number=2,
            title=BOARD_TASK_TITLE,
            body="Its one sub-issue, which is what makes the issue above a project.",
            parent_id=parent.content_id,
        )
        self.issues: list[_Issue] = [parent, child]
        self.created: list[_Issue] = []
        for issue in self.issues:
            issue.board = self
        #: The Status options this board carries, which one journey narrows.
        self.options: tuple[_StatusOption, ...] = STATUS_OPTIONS
        #: The number of the board being served, which an issue's membership is filed under.
        self.number: int = CONFIGURED_PROJECT_NUMBER
        #: The options of the board's `Priority` field, which it carries none of when empty:
        #: what `sources fields --apply` creates, and nothing else puts there.
        self.priorities: tuple[_StatusOption, ...] = ()
        #: How many comments this board has taken, which numbers the next one.
        self.commented = 0

    def issues_created_and_kept(self) -> list[_Issue]:
        return [issue for issue in self.created if issue in self.issues]

    def board_fields_response(self) -> dict[str, object]:
        """The board's id and field definitions alone, under the root alias the read names.

        What the adopted source asks before a write whose item does not say which board
        field to address: it selects no items, so it answers none.
        """
        # llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] The read is the
        # installed plan store's own, reconciled on every journey: an answer under any other
        # root is a board it reports as not found. GitHub's schema has no machine-readable
        # copy here; the producer holds the query to a pinned schema in its `tests/schema.rs`.
        return {
            "data": {"boardFields": {"projectV2": {"id": self.node_id, "fields": self._fields()}}}
        }
        # llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]

    def _fields(self) -> dict[str, object]:
        """Shared, so the board read and the board-fields read cannot disagree on a field."""
        return {
            "nodes": [
                {
                    "__typename": "ProjectV2SingleSelectField",
                    "id": _BoardField.STATUS,
                    "name": "Status",
                    "options": [option.rendered() for option in self.options],
                },
                {
                    "__typename": "ProjectV2Field",
                    "id": _BoardField.ORIGIN,
                    "name": ORIGIN_FIELD_NAME,
                },
                *(
                    [
                        {
                            "__typename": "ProjectV2SingleSelectField",
                            "id": _BoardField.PRIORITY,
                            "name": "Priority",
                            "options": [option.rendered() for option in self.priorities],
                        }
                    ]
                    if self.priorities
                    else []
                ),
            ],
            "pageInfo": {"hasNextPage": False},
        }

    def board_response(self) -> dict[str, object]:
        return {
            "data": {
                "owner": {
                    "projectV2": {
                        "id": self.node_id,
                        "title": self.title,
                        "fields": self._fields(),
                        "items": {
                            "nodes": [issue.item() for issue in self.issues],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        },
                    }
                }
            }
        }

    def found(self, search: str) -> list[_Issue]:
        """Every issue of this board ``search`` matches, before any page is cut from it."""
        wanted = _FIELD_QUALIFIER.search(search)
        since = _UPDATED_QUALIFIER.search(search)
        return [
            issue
            for issue in self.issues
            if (wanted is None or _found_by(wanted, issue))
            and (since is None or _moment(issue.updated_at()) >= _moment(since["since"]))
        ]

    def search_response(
        self, search: str, first: object = None, after: object = None
    ) -> dict[str, object]:
        """The issues of this board a search finds, narrowed the way GitHub narrows one.

        The `in:title`, `in:body` and `in:title,body` qualifiers and the quoted phrases
        after them are honoured rather than ignored: the source confirms each candidate
        afterwards, so a fixture answering every search with the whole board would pass a
        source that had stopped scoping its search at all — and scoping it is the whole of
        what this read buys over walking the board. Every phrase has to appear in one of
        the named fields, as GitHub requires of every term.
        """
        found = self.found(search)
        # Paged the way GitHub pages a connection when the request names a page size: the
        # cursor is the offset the next page starts at, and `hasNextPage` says whether rows
        # remain. A fixture answering every match on one page would hide a source that asks
        # for a page wider than its answer needs, or one that pages past what it was asked.
        start = int(after) if isinstance(after, str) else 0
        end = start + first if isinstance(first, int) else len(found)
        return {
            "data": {
                "search": {
                    "issueCount": len(found),
                    "pageInfo": {
                        "hasNextPage": end < len(found),
                        "endCursor": str(end) if end < len(found) else None,
                    },
                    "nodes": [issue.board_issue() for issue in found[start:end]],
                }
            }
        }

    def origin_lookup_response(self, variables: dict[str, object]) -> dict[str, object]:
        """The rows whose origin field holds one copy origin, and the search for it in bodies.

        Two reads in one document, as the source sends them: the board's own `items`
        narrowed by its field filter — `onetaskgraph.origin:"<id>"`, matched exactly the way
        GitHub matches a quoted field value — and the board-scoped issue search for the same
        id in the body. The rows answered are only those the filter names, because the point
        of the filter is that the board is not walked: a fixture that answered every row
        would hide a source that had stopped narrowing.
        """
        filtered = _ORIGIN_FILTER.fullmatch(str(variables.get("filter")))
        if filtered is None:
            raise ValueError(f"the origin lookup sent no origin filter: {variables!r}")
        origin = _unescaped(filtered.group("origin"))
        search = variables.get("search")
        if not isinstance(search, str):
            raise ValueError("the origin lookup sent no search")
        return {
            "data": {
                "originItems": {
                    "projectV2": {
                        "items": {
                            "nodes": [
                                issue.item() for issue in self.issues if issue.origin == origin
                            ],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                },
                **self.search_response(search)["data"],
            }
        }

    def node_response(self, node_id: object) -> dict[str, object]:
        """One issue by its own node id, or the null GitHub answers an unheld one with.

        The adopted store's item read carries what a write of the issue needs beside it: the
        field definitions of the board holding it, under the `boards` alias, and the issues
        blocking it, so neither costs a second request.
        """
        for issue in self.issues:
            if issue.content_id == node_id:
                board = issue.held_by
                project = {"id": board.node_id, "number": board.number, "fields": board._fields()}
                blockers = [self._issue(held).content() for held in issue.blocked_by]
                return {
                    "data": {
                        "node": issue.board_issue()
                        | {
                            "boards": {"nodes": [{"project": project}]},
                            "blockedBy": {
                                "nodes": blockers,
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            },
                        }
                    }
                }
        return {"data": {"node": None}}

    def sub_issues_response(self, node_id: object) -> dict[str, object]:
        """One issue's sub-issues, which is how this board reports a project's tasks."""
        for issue in self.issues:
            if issue.content_id != node_id:
                continue
            children = [held for held in self.issues if held.parent_id == issue.content_id]
            return {
                "data": {
                    "node": {
                        "__typename": "Issue",
                        "subIssues": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [child.board_issue() for child in children],
                        },
                    }
                }
            }
        return {"data": {"node": None}}

    def dependencies_response(self, node_id: object) -> dict[str, object]:
        """One issue's dependency edges, both ways, as the copy wrote them."""
        for issue in self.issues:
            if issue.content_id != node_id:
                continue
            blocking = [held for held in self.issues if issue.content_id in held.blocked_by]
            return {
                "data": {
                    "node": {
                        "__typename": "Issue",
                        "blockedBy": self._connection(
                            [self._issue(held) for held in issue.blocked_by]
                        ),
                        "blocking": self._connection(blocking),
                    }
                }
            }
        return {"data": {"node": None}}

    @staticmethod
    def _connection(issues: list[_Issue]) -> dict[str, object]:
        return {
            "nodes": [issue.board_issue() for issue in issues],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }

    def add_blocked_by(self, variables: dict[str, object]) -> dict[str, object]:
        """Record that one issue is blocked by another, which is how a `deps` edge lands."""
        payload = variables.get("input")
        if not isinstance(payload, dict):
            raise ValueError("addBlockedBy requires an input object")
        issue = self._issue(payload.get("issueId"))
        blocking = self._issue(payload.get("blockingIssueId"))
        if blocking.content_id not in issue.blocked_by:
            issue.blocked_by.append(blocking.content_id)
        return {
            "data": {
                "addBlockedBy": {
                    "issue": {"id": issue.content_id},
                    "blockingIssue": {"id": blocking.content_id},
                }
            }
        }

    def create_issue(self, variables: dict[str, object]) -> dict[str, object]:
        payload = variables.get("input")
        if not isinstance(payload, dict):
            raise ValueError("createIssue requires an input object")
        title = payload.get("title")
        body = payload.get("body")
        if not isinstance(title, str) or not isinstance(body, str):
            raise ValueError("createIssue requires a title and a body")
        created = _Issue(
            item_id=_BoardItemId(f"PVTI_created_{len(self.created)}"),
            content_id=_IssueNodeId(f"I_created_{len(self.created)}"),
            number=3 + len(self.created),
            title=title,
            body=body,
            repository=_repository_of(payload.get("repositoryId")),
        )
        created.board = self
        self.created.append(created)
        self.issues.append(created)
        return {
            "data": {
                "createIssue": {
                    "issue": {
                        "id": created.content_id,
                        "number": created.number,
                        "url": created.content()["url"],
                    }
                }
            }
        }

    def _issue(self, content_id: object) -> _Issue:
        for issue in self.issues:
            if issue.content_id == content_id:
                return issue
        raise ValueError(f"the board holds no issue {content_id!r}")

    def update_issue(self, variables: dict[str, object]) -> dict[str, object]:
        """Apply one issue update — its body, its title, and the state a close carries."""
        payload = variables.get("input")
        if not isinstance(payload, dict):
            raise ValueError("updateIssue requires an input object")
        issue = self._issue(payload.get("id"))
        if isinstance(body := payload.get("body"), str):
            issue.body = body
        if isinstance(title := payload.get("title"), str):
            issue.title = title
        if isinstance(state := payload.get("stateInput"), dict):
            issue.state = str(state.get("value"))
            issue.state_reason = str(state.get("stateReason"))
        return {"data": {"updateIssue": {"issue": {"id": issue.content_id}}}}

    def add_to_board(self, variables: dict[str, object]) -> dict[str, object]:
        payload = variables.get("input")
        if not isinstance(payload, dict):
            raise ValueError("addProjectV2ItemById requires an input object")
        return {
            "data": {
                "addProjectV2ItemById": {
                    "item": {"id": self._issue(payload.get("contentId")).item_id}
                }
            }
        }

    def _applied(self, payload: object) -> object:
        """Apply one field write's input to its row, and name the row it wrote."""
        if not isinstance(payload, dict):
            raise ValueError("updateProjectV2ItemFieldValue requires an input object")
        item_id = payload.get("itemId")
        value = payload.get("value")
        match payload.get("fieldId"):
            case _BoardField.STATUS:
                self._row(item_id).status = self._option_named(value)
            case _BoardField.ORIGIN:
                self._row(item_id).origin = self._text(value)
            case _BoardField.PRIORITY:
                self._row(item_id).priority = self._option_named(value, self.priorities)
        return item_id

    def _cleared(self, payload: object) -> object:
        """Clear one field value on a row, which is how a priority of `none` is written."""
        if not isinstance(payload, dict):
            raise ValueError("clearProjectV2ItemFieldValue requires an input object")
        item_id = payload.get("itemId")
        if payload.get("fieldId") != _BoardField.PRIORITY:
            raise ValueError(f"only the Priority field is cleared here, not {payload!r}")
        self._row(item_id).priority = None
        return item_id

    def set_field(self, variables: dict[str, object]) -> dict[str, object]:
        """Set one field value on a row, keeping what an origin or a Status write puts there."""
        item_id = self._applied(variables.get("input"))
        return {
            "data": {
                "updateProjectV2ItemFieldValue": {
                    "projectV2Item": self._written(variables, item_id)
                }
            }
        }

    def _written(self, variables: dict[str, object], item_id: object) -> dict[str, object]:
        """The row a field write answers with, carrying its `Priority` when the write asks.

        A standalone priority write selects the stored value in its own response
        (`fieldValueByName` under `@include(if:$readPriority)`), which is the read-back the
        source takes instead of resolving the issue again.
        """
        written: dict[str, object] = {"id": item_id}
        if variables.get("readPriority") is True:
            written["fieldValueByName"] = self._row(item_id).priority_value()
        return written

    def set_fields(self, variables: dict[str, object]) -> dict[str, object]:
        """Several field writes on one row in one request, as the source batches a copy's.

        The source's document carries every write as an alias of its own, each included only
        when its `write…` flag is set, so only the writes it asked for are applied and only
        their aliases are answered, which is what GitHub does with an `@include(if:)`.
        """
        answer: dict[str, object] = {}
        for write in BATCHED_WRITES:
            if write.included(variables):
                applied = self._applied(variables.get(write.variable))
                answer[write.alias] = {"projectV2Item": {"id": applied}}
        if variables.get("writeClear") is True:
            answer["cleared"] = {"projectV2Item": {"id": self._cleared(variables.get("clear"))}}
        return {"data": answer}

    def snapshot_response(self) -> dict[str, object]:
        """The board's single-select fields in full and every row's value of each.

        What the guarded field setup reads before and after it writes, to plan what is
        missing and to verify that nothing it did not create moved.
        """
        fields = [("Status", _BoardField.STATUS, self.options)]
        if self.priorities:
            fields.append(("Priority", _BoardField.PRIORITY, self.priorities))

        def values(issue: _Issue) -> list[dict[str, object]]:
            held = [("Status", _BoardField.STATUS, self.options, issue.status)]
            if issue.priority is not None:
                held.append(("Priority", _BoardField.PRIORITY, self.priorities, issue.priority))
            return [
                {
                    "name": value,
                    "optionId": next(option.id for option in options if option.name == value),
                    "field": {"id": field_id, "name": name},
                }
                for name, field_id, options, value in held
            ]

        return {
            "data": {
                "owner": {
                    "projectV2": {
                        "id": self.node_id,
                        "fields": {
                            "nodes": [
                                {
                                    "id": field_id,
                                    "name": name,
                                    "options": [option.rendered_in_full() for option in options],
                                }
                                for name, field_id, options in fields
                            ],
                            "pageInfo": {"hasNextPage": False},
                        },
                        "items": {
                            "nodes": [
                                {
                                    "id": issue.item_id,
                                    "fieldValues": {
                                        "nodes": values(issue),
                                        "pageInfo": {"hasNextPage": False},
                                    },
                                }
                                for issue in self.issues
                            ],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        },
                    }
                }
            }
        }

    def create_field(self, variables: dict[str, object]) -> dict[str, object]:
        """Create the board's `Priority` field with the options the setup sends, in its order."""
        payload = variables.get("input")
        if not isinstance(payload, dict) or payload.get("name") != "Priority":
            raise ValueError(f"only the Priority field is created here, not {payload!r}")
        options = payload.get("singleSelectOptions")
        if not isinstance(options, list) or self.priorities:
            raise ValueError(f"a Priority field is created once, with options: {payload!r}")
        self.priorities = tuple(
            _StatusOption(id=_FieldOptionId(f"OPT_priority_{at}"), name=str(option["name"]))
            for at, option in enumerate(options)
        )
        created = {
            "id": _BoardField.PRIORITY,
            "name": "Priority",
            "options": [option.rendered_in_full() for option in self.priorities],
        }
        return {"data": {"createProjectV2Field": {"projectV2Field": created}}}

    def clear_field(self, variables: dict[str, object]) -> dict[str, object]:
        """Clear one field value on a row, as a standalone write answers it."""
        item_id = self._cleared(variables.get("input"))
        return {
            "data": {
                "clearProjectV2ItemFieldValue": {"projectV2Item": self._written(variables, item_id)}
            }
        }

    def comments_response(self, node_id: object) -> dict[str, object]:
        """One issue's comments, oldest first, as its `comments` connection lists them."""
        listed = {
            "nodes": self._issue(node_id).comments,
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }
        return {"data": {"node": {"__typename": "Issue", "comments": listed}}}

    def _detail(self, node_id: object, comments: bool) -> dict[str, object] | None:
        """One issue by its own node id with, when asked, the first page of its comments."""
        for issue in self.issues:
            if issue.content_id == node_id:
                answered = issue.board_issue()
                if comments:
                    answered["comments"] = {
                        "nodes": issue.comments,
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                return answered
        return None

    def detail_response(self, node_id: object) -> dict[str, object]:
        """One issue and its first page of comments, which `task show` reads in one request."""
        return {"data": {"node": self._detail(node_id, comments=True)}}

    def details_response(self, variables: dict[str, object]) -> dict[str, object]:
        """A fixed batch of aliased issue reads, `i<n>` answering the issue `$id<n>` names."""
        comments = variables.get("comments") is True
        slots = sorted(int(name[2:]) for name in variables if re.fullmatch(r"id\d+", name))
        return {
            "data": {f"i{n}": self._detail(variables[f"id{n}"], comments=comments) for n in slots}
        }

    def add_comment(self, variables: dict[str, object]) -> dict[str, object]:
        """Add one comment to an issue, signed as the fixture's token account."""
        payload = variables.get("input")
        if not isinstance(payload, dict) or not isinstance(payload.get("body"), str):
            raise ValueError("addComment requires an input object with a body")
        issue = self._issue(payload.get("subjectId"))
        self.commented += 1
        moment = f"2026-08-26T00:{self.commented // 60:02d}:{self.commented % 60:02d}Z"
        comment: dict[str, object] = {
            "id": f"IC_fixture_{self.commented}",
            "author": {"login": "fixture-token-account"},
            "createdAt": moment,
            "updatedAt": moment,
            "body": payload["body"],
            "url": f"{issue.content()['url']}#issuecomment-{self.commented}",
        }
        issue.comments.append(comment)
        return {
            "data": {
                "addComment": {
                    "subject": {"id": issue.content_id},
                    "commentEdge": {"node": comment},
                }
            }
        }

    def _option_named(self, value: object, options: tuple[_StatusOption, ...] | None = None) -> str:
        """The option a write names, refused when this board's field carries no such option."""
        option = value.get("singleSelectOptionId") if isinstance(value, dict) else None
        named = {held.id: held.name for held in (self.options if options is None else options)}
        if option not in named:
            raise ValueError(f"this board carries no option {option!r} for that field")
        return named[_FieldOptionId(str(option))]

    @staticmethod
    def _text(value: object) -> str:
        """The text an origin write carries, refused when it is not one."""
        text = value.get("text") if isinstance(value, dict) else None
        if not isinstance(text, str):
            raise ValueError(f"the origin field takes a text value, and was sent {value!r}")
        return text

    def _row(self, item_id: object) -> _Issue:
        rows = [issue for issue in self.issues if issue.item_id == item_id]
        if len(rows) != 1:
            raise ValueError(f"the board holds no row {item_id!r}")
        return rows[0]

    def transfer(self, content_id: _IssueNodeId, repository: _Repository) -> _Issue:
        """Move one issue to ``repository``, as `gh issue transfer` has GitHub do.

        What GitHub keeps across a transfer is what a later copy resolves the issue by: the
        issue's node, its board row, and the text that row's origin field holds. Only the
        repository its content answers with changes.
        """
        issue = self._issue(content_id)
        issue.repository = repository
        return issue

    def add_sub_issue(self, variables: dict[str, object]) -> dict[str, object]:
        payload = variables.get("input")
        if not isinstance(payload, dict):
            raise ValueError("addSubIssue requires an input object")
        parent = self._issue(payload.get("issueId"))
        child = self._issue(payload.get("subIssueId"))
        child.parent_id = parent.content_id
        parent.sub_issues += 1
        return {
            "data": {
                "addSubIssue": {
                    "issue": {"id": parent.content_id},
                    "subIssue": {"id": child.content_id},
                }
            }
        }

    def delete_issue(self, variables: dict[str, object]) -> dict[str, object]:
        """Take one issue off the board, which is how a refused copy undoes its creations.

        Recorded as the issue leaving `issues` while staying in `created`, so a journey
        can read both what a copy made and what it then took back.
        """
        payload = variables.get("input")
        if not isinstance(payload, dict):
            raise ValueError("deleteIssue requires an input object")
        deleted = self._issue(payload.get("issueId"))
        self.issues.remove(deleted)
        return {
            "data": {"deleteIssue": {"repository": {"id": REPOSITORY_NODE_IDS[deleted.repository]}}}
        }


@dataclass
class _Answered:
    """What the fixture answered one request with, recorded as it answers."""

    #: For a search, how many issues matched in all, which is what says whether the page
    #: it asked for was wider than the answer needed; `None` for anything else.
    matched: int | None = None


@dataclass(frozen=True)
class _GraphQLRequest:
    """One request the source made, kept as its operation and its own variables.

    The operation is derived from the document rather than sent beside it, because
    the source names none of its operations: routing on the root field is what a
    GraphQL server does with an anonymous document, and it is what lets one handler
    answer a board read and a create in the sequence a copy performs them.
    """

    query: str
    variables: dict[str, object]
    #: When the fixture read this request, on the monotonic clock. Recorded here rather
    #: than derived afterwards because the pacing journey below measures the *gaps*
    #: between the mutations a copy sends, and a gap is only observable at the far end
    #: of the wire — the source's own scheduling is invisible from outside it.
    received: float
    #: What the fixture answered, recorded as it answers.
    answered: _Answered = field(default_factory=lambda: _Answered(), compare=False)

    @classmethod
    def from_json(cls, body: bytes) -> _GraphQLRequest:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("GraphQL request must be an object")
        query = payload.get("query")
        variables = payload.get("variables")
        if not isinstance(query, str):
            raise ValueError("GraphQL request requires a query string")
        if not isinstance(variables, dict):
            raise ValueError("GraphQL request requires a variables object")
        return cls(query=query, variables=variables, received=time.monotonic())

    @property
    def is_mutation(self) -> bool:
        """Whether this document creates content, which is what GitHub's second limiter counts.

        Read off the document the way the source reads it — a GraphQL document whose
        first word is `mutation` — rather than from the operation table above, so a
        document the fixture has not been taught still counts as a write here.
        """
        return self.query.lstrip().startswith("mutation")

    @property
    def operation(self) -> _Operation:
        for marker, operation in _OPERATIONS.items():
            if marker in self.query:
                return operation
        raise ValueError(f"no fixture operation answers {self.query!r}")

    @property
    def repository(self) -> _Repository:
        """The repository this request looks up: alone, or beside the board's fields."""
        if self.operation is _Operation.CREATION_CONTEXT:
            return _Repository(
                owner=self.string("repositoryOwner"), name=self.string("repositoryName")
            )
        return _Repository(owner=self.string("owner"), name=self.string("name"))

    @property
    def looks_up_repository(self) -> bool:
        """Whether this request resolves a repository's node id, which a create needs."""
        return self.operation in (_Operation.REPOSITORY, _Operation.CREATION_CONTEXT)

    def string(self, name: str) -> str:
        value = self.variables.get(name)
        if not isinstance(value, str):
            raise ValueError(f"GraphQL variable {name!r} must be a string")
        return value

    def input_value(self, name: str) -> object:
        payload = self.variables.get("input")
        if not isinstance(payload, dict):
            raise ValueError("GraphQL request has no input object")
        return payload.get(name)


class _Operation(StrEnum):
    BOARD = "board"
    BOARD_FIELDS = "boardFields"
    CREATION_CONTEXT = "creationContext"
    FIELD_SNAPSHOT = "fieldSnapshot"
    CREATE_FIELD = "createField"
    SEARCH = "search"
    ORIGIN_LOOKUP = "originLookup"
    ISSUE = "issue"
    ISSUE_DETAIL = "issueDetail"
    ISSUE_DETAILS = "issueDetails"
    SUB_ISSUES = "subIssues"
    COMMENTS = "comments"
    REPOSITORY = "repository"
    DEPENDENCIES = "dependencies"
    CREATE_ISSUE = "createIssue"
    ADD_TO_BOARD = "addToBoard"
    UPDATE_FIELD = "updateField"
    UPDATE_FIELDS = "updateFields"
    CLEAR_FIELD = "clearField"
    ADD_COMMENT = "addComment"
    ADD_SUB_ISSUE = "addSubIssue"
    ADD_BLOCKED_BY = "addBlockedBy"
    UPDATE_ISSUE = "updateIssue"
    DELETE_ISSUE = "deleteIssue"


#: Each operation's marker in the document the source sends, in the order they are
#: tried. The source ships its queries as constants, so the root field is a stable
#: substring of each one and is what tells a board read from the writes that follow it.
_OPERATIONS: dict[str, _Operation] = {
    # Before the board read's own marker and the search's, both of which this document
    # also contains: it narrows the board's items by a field filter and searches beside it.
    "originItems:repositoryOwner": _Operation.ORIGIN_LOOKUP,
    # Before the board-fields read's own marker, which this document carries beside the
    # repository it reads in the same request.
    "repository(owner:$repositoryOwner": _Operation.CREATION_CONTEXT,
    # Before the board read's own marker, which this aliased root also contains.
    "boardFields:repositoryOwner": _Operation.BOARD_FIELDS,
    "optionId field{": _Operation.FIELD_SNAPSHOT,
    "createProjectV2Field(": _Operation.CREATE_FIELD,
    "repositoryOwner": _Operation.BOARD,
    "search(query:": _Operation.SEARCH,
    "subIssues(first:": _Operation.SUB_ISSUES,
    # Before the dependency read's own marker, which this read of one item also carries: it
    # answers the item's `blockedBy` beside the boards it sits on.
    "boards:projectItems(": _Operation.ISSUE,
    # Both before the comment listing's own marker, which each also carries: the issue read
    # with its first page of comments, alone and in a fixed batch of aliased reads.
    "i0:node(id:$id0)": _Operation.ISSUE_DETAILS,
    "node(id:$id){__typename ...BoardIssue ... on Issue{comments(": _Operation.ISSUE_DETAIL,
    "comments(first:": _Operation.COMMENTS,
    "{repository(owner:": _Operation.REPOSITORY,
    "blockedBy(first:": _Operation.DEPENDENCIES,
    "createIssue(": _Operation.CREATE_ISSUE,
    "addProjectV2ItemById(": _Operation.ADD_TO_BOARD,
    # Before the single field write's own marker, which every write it batches also carries.
    "second:updateProjectV2ItemFieldValue(": _Operation.UPDATE_FIELDS,
    "updateProjectV2ItemFieldValue(": _Operation.UPDATE_FIELD,
    "clearProjectV2ItemFieldValue(": _Operation.CLEAR_FIELD,
    "addComment(": _Operation.ADD_COMMENT,
    "addSubIssue(": _Operation.ADD_SUB_ISSUE,
    "addBlockedBy(": _Operation.ADD_BLOCKED_BY,
    "updateIssue(": _Operation.UPDATE_ISSUE,
    "deleteIssue(": _Operation.DELETE_ISSUE,
}


#: The field qualifier the source narrows a board search with, the quoted phrases after
#: it, and the two characters GitHub's quoting grammar gives a meaning inside a quoted
#: phrase. All are the source's own spelling: it escapes a backslash and a double quote
#: before it sends one, so a fixture that read the qualifier literally would find no issue
#: whose title contains either.
_FIELD_QUALIFIER = re.compile(
    r'in:(?P<fields>title,body|title|body)(?P<phrases>(?:\s+"(?:[^"\\]|\\.)*")+)'
)


_PHRASE = re.compile(r'"(?P<phrase>(?:[^"\\]|\\.)*)"')


#: The qualifier a `commented_since` query narrows the board's issue search with.
_UPDATED_QUALIFIER = re.compile(r"updated:>=(?P<since>\S+)")


#: When every issue of the stand-in was created, and last changed before any comment.
ISSUE_CREATED_AT = "2026-08-26T00:00:00Z"


#: The board field filter an origin lookup narrows the board's items with.
_ORIGIN_FILTER = re.compile(re.escape(ORIGIN_FIELD_NAME) + r':"(?P<origin>(?:[^"\\]|\\.)*)"')


def _moment(value: str) -> datetime:
    """An RFC 3339 instant, as GitHub compares one in an `updated:` qualifier."""
    return datetime.fromisoformat(value)


def _unescaped(quoted: str) -> str:
    """One quoted search phrase, as the text the source was looking for."""
    return quoted.replace('\\"', '"').replace("\\\\", "\\")


def _found_by(wanted: re.Match[str], issue: _Issue) -> bool:
    """Whether every phrase ``wanted`` quotes appears in one of the fields it names."""
    fields = wanted.group("fields").split(",")
    searched = [issue.title if name == "title" else issue.body for name in fields]
    return all(
        any(_unescaped(phrase.group("phrase")) in text for text in searched)
        for phrase in _PHRASE.finditer(wanted.group("phrases"))
    )


BOARD = _Board()


@dataclass(frozen=True)
class _Refusal:
    """One response GitHub refuses a request with, as the status and body it sends.

    Both journeys that use one send the **same** forbidden status and differ only in
    what the body says about itself, because that is the distinction under test: a
    forbidden status carrying none of GitHub's limiter vocabulary really is a token
    that lacks a permission, and one carrying it is the burst limiter. A fixture that
    varied the status too would prove the source reads the status, which is the reading
    that sent operators to change a credential.
    """

    status: int
    body: dict[str, object]


class _GitHubFixture(BaseHTTPRequestHandler):
    requests: ClassVar[list[_GraphQLRequest]]
    #: What every request is refused with, or `None` to answer the board normally.
    refusal: ClassVar[_Refusal | None] = None
    #: One operation answered with a GraphQL error while every other is served, or `None`:
    #: a write the store makes that GitHub refuses between two it accepted.
    refused_operation: ClassVar[_Operation | None] = None
    #: How many of `refused_operation` are served before the one refused, so a refusal can
    #: land on a later write of the same kind — the record's, after the body's.
    refused_after: ClassVar[int] = 0
    #: The board this handler answers for. A class attribute rather than an argument
    #: because the stdlib constructs a handler per request; a second board is served by
    #: a subclass of this one carrying its own board and its own request log, which is
    #: what lets one journey serve `plans` and `followups` at once without either
    #: board's items showing up on the other.
    board: ClassVar[_Board]

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
        length = int(self.headers["Content-Length"])
        request = _GraphQLRequest.from_json(self.rfile.read(length))
        self.requests.append(request)
        if self.refusal is not None:
            self._send(self.refusal.status, self.refusal.body)
            return
        self._send(200, self._answer(request))

    def _send(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _answer(self, request: _GraphQLRequest) -> dict[str, object]:
        """The document this fixture answers one operation with.

        An operation it does not implement is answered as a GraphQL error naming the
        document, so a source that starts making a call this board cannot serve fails
        saying which call rather than on a missing field somewhere downstream.
        """
        try:
            operation = request.operation
        except ValueError as unknown:
            return {"errors": [{"message": str(unknown)}]}
        if operation is self.refused_operation:
            if _GitHubFixture.refused_after == 0:
                return {"errors": [{"message": f"{operation} refused by the fixture"}]}
            _GitHubFixture.refused_after -= 1
        match operation:
            case _Operation.BOARD:
                return self.board.board_response()
            case _Operation.BOARD_FIELDS:
                return self.board.board_fields_response()
            case _Operation.CREATION_CONTEXT:
                fields = self.board.board_fields_response()["data"]
                answered = self._repository(request.repository)["data"]
                assert isinstance(fields, dict) and isinstance(answered, dict)
                return {"data": {**fields, **answered}}
            case _Operation.FIELD_SNAPSHOT:
                return self.board.snapshot_response()
            case _Operation.CREATE_FIELD:
                return self.board.create_field(request.variables)
            case _Operation.SEARCH:
                answer = self.board.search_response(
                    request.string("search"),
                    request.variables.get("first"),
                    request.variables.get("after"),
                )
                request.answered.matched = len(self.board.found(request.string("search")))
                return answer
            case _Operation.ORIGIN_LOOKUP:
                return self.board.origin_lookup_response(request.variables)
            case _Operation.ISSUE:
                return self.board.node_response(request.variables.get("id"))
            case _Operation.ISSUE_DETAIL:
                return self.board.detail_response(request.variables.get("id"))
            case _Operation.ISSUE_DETAILS:
                return self.board.details_response(request.variables)
            case _Operation.SUB_ISSUES:
                return self.board.sub_issues_response(request.variables.get("id"))
            case _Operation.COMMENTS:
                return self.board.comments_response(request.variables.get("id"))
            case _Operation.ADD_COMMENT:
                return self.board.add_comment(request.variables)
            case _Operation.CLEAR_FIELD:
                return self.board.clear_field(request.variables)
            case _Operation.REPOSITORY:
                return self._repository(request.repository)
            case _Operation.DEPENDENCIES:
                return self.board.dependencies_response(request.variables.get("id"))
            case _Operation.CREATE_ISSUE:
                return self.board.create_issue(request.variables)
            case _Operation.ADD_TO_BOARD:
                return self.board.add_to_board(request.variables)
            case _Operation.UPDATE_ISSUE:
                return self.board.update_issue(request.variables)
            case _Operation.UPDATE_FIELD:
                return self.board.set_field(request.variables)
            case _Operation.UPDATE_FIELDS:
                return self.board.set_fields(request.variables)
            case _Operation.ADD_SUB_ISSUE:
                return self.board.add_sub_issue(request.variables)
            case _Operation.ADD_BLOCKED_BY:
                return self.board.add_blocked_by(request.variables)
            case _Operation.DELETE_ISSUE:
                return self.board.delete_issue(request.variables)

    @staticmethod
    def _repository(named: _Repository) -> dict[str, object]:
        """What GitHub answers about one repository, and about one it will not show.

        A repository that does not exist, or that the token cannot see, comes back as a
        present and null field rather than as an error — which is the answer the source
        turns into its refusal, so it is the answer this fixture gives. An owner the
        fixture does not know is answered the same way, because to GitHub a repository
        under an owner that does not exist is one more repository that is not there.
        """
        known = REPOSITORY_NODE_IDS.get(named)
        if known is None:
            return {"data": {"repository": None}}
        return {"data": {"repository": {"id": known, "nameWithOwner": str(named)}}}

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def _serving_board(
    refusal: _Refusal | None = None,
    options: tuple[_StatusOption, ...] = STATUS_OPTIONS,
    number: int = CONFIGURED_PROJECT_NUMBER,
) -> Iterator[dict[str, str]]:
    """Serve the board fixture, yielding the environment that points `plans` at it.

    The board is reset per journey rather than shared: it is mutated by the writes a
    copy performs, so a second journey reading the residue of the first would report a
    board somebody else's copy filled in.

    ``refusal`` makes every request come back as one GitHub refusal instead. It is a
    property of the server rather than of a call because what a caller sees is the
    diagnostic the source composes, and the source retries before it composes one.
    ``options`` are the Status options the board carries, and ``number`` the board number
    the source being served is configured with, which an issue's membership answers under.
    """
    _GitHubFixture.refusal = refusal
    try:
        with _served(_GitHubFixture, BOARD, options, number) as endpoint:
            yield {
                "GH_PROJECTS_TOKEN": "fixture-token",
                "ONETASKGRAPH_SOURCES__PLANS__CONFIG__ENDPOINT": endpoint,
                "ONETASKGRAPH_SOURCES__FOLLOWUPS__CONFIG__ENDPOINT": endpoint,
                # A copy onto `plans` builds the source its route names, `hellopatient`, which
                # refuses to build without its key; a journey that ever routed a task there
                # fails on the refused connection instead of reaching the production workspace.
                LINEAR_KEY_ENV: "fixture-linear-key",
                "ONETASKGRAPH_SOURCES__HELLOPATIENT__CONFIG__ENDPOINT": UNSERVED_LINEAR,
                # The same for the source the `followups` route names, `hellopatient-followups`.
                LINEAR_FOLLOWUPS_ENDPOINT_ENV: UNSERVED_LINEAR,
            }
    finally:
        _GitHubFixture.refusal = None
        _GitHubFixture.refused_operation = None
        _GitHubFixture.refused_after = 0


@contextmanager
def _served(
    handler: type[_GitHubFixture],
    board: _Board,
    options: tuple[_StatusOption, ...],
    number: int,
) -> Iterator[str]:
    """Serve ``board`` through ``handler`` on a loopback port, yielding its endpoint.

    Split out of :func:`_serving_board` so a journey needing two boards at once stands
    the second one up the same way rather than a second way: what a board has to be reset
    to, and how its server is stopped and waited out, are stated once.
    """
    board.reset()
    board.options = options
    board.number = number
    handler.requests = []
    handler.board = board
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/graphql"
    finally:
        server.shutdown()
        # Bounded like every other wait here: `shutdown` asks the serving loop to stop
        # and this waits for it, so a handler wedged mid-request would otherwise hold
        # the tier rather than fail it.
        thread.join(timeout=e2e_timeout(60))
        assert not thread.is_alive(), (
            "the fixture's GitHub stand-in was still serving after it was asked to stop, "
            "so a request handler is wedged and this journey's server outlives it"
        )
        server.server_close()


# llmlint: ignore-end[e2e_not_mocked]


def _prepare_plan_sources(root: Path) -> dict[str, str]:
    """Build every plan source one journey reads under ``root``, and name them.

    Preparing them is two things and the name covers both: the gitignored authoring
    root is *pointed* at ``root``, which the caller has already made, and the credential
    file each source resolves is pointed at one under ``root`` too — away from this
    host's own, which is what keeps a journey off the live board whatever the operator
    has configured.

    Pointing it per run rather than leaving it at `.plans` is what makes these journeys
    say something: a `local-md` source refuses a root it cannot canonicalize and refuses
    it for the whole read, so they would otherwise pass or fail on whether this checkout
    happened to have run session setup.

    Launches compose these overrides onto their isolated execution environment.
    Copying the ambient environment instead would restore the host's runs root and
    paid-provider routing, sending concurrent journeys into shared state.
    """
    return {
        "ONETASKGRAPH_SECRETS_FILE": str(root / "no-secrets.env"),
        AUTHORING_ROOT_ENV: str(root),
    }


def _plan_environment(root: Path) -> dict[str, str]:
    """The environment a read across the default sources runs under.

    A whole ambient copy, because a read is not a launch and has nothing to isolate
    from. A journey that launches takes :func:`_prepare_plan_sources` instead.
    """
    environment = os.environ.copy()
    environment.update(_prepare_plan_sources(root))
    return environment


def _hosted(repository: _Repository) -> str:
    """``repository`` as the normalized origin a task record's `repositories` holds."""
    return f"github.com/{repository}"


#: The reads that walk a board's items, which a query or a copy that knows what it wants
#: has no reason to send: the board read and the Status snapshot are the two documents
#: selecting `ProjectV2.items` unnarrowed.
BOARD_ENUMERATIONS = frozenset({_Operation.BOARD, _Operation.FIELD_SNAPSHOT})


#: The spelling an ambient override of any `followups` setting would take, removed from the
#: journeys' environment so the committed file is the source applied — its mapping and its
#: owner alike.
FOLLOWUPS_ENV_PREFIX = "ONETASKGRAPH_SOURCES__FOLLOWUPS__"


#: The run and root cause of the one ticket the journeys below copy.
PROPOSED_RUN = "proposal-run"


PROPOSED_CAUSE = "ticket-lands-as-a-proposal"


#: The `followups` board's Status options, `Proposal`, `Deferred` and `Queued` among them.
FOLLOWUPS_OPTIONS = (*STATUS_OPTIONS, PROPOSAL, DEFERRED)


def _follow_up_ticket(
    repository: _Repository,
    status: follow_up_tickets.Status = follow_up_tickets.Status.PROPOSED,
    evidence: str = "",
    cause: str = PROPOSED_CAUSE,
    frequency: follow_up_tickets.Frequency = follow_up_tickets.Frequency.INTERMITTENT,
    verified_on: str = "verifier.example",
) -> follow_up_tickets.Ticket:
    """One ticket about ``repository`` as the agent writes it, new and `backlog` by default.

    ``evidence`` is added to its `## Evidence` section, the way a later run adds its own, and
    ``frequency`` is the agent's judgment of the root cause. Its estimate, estimate line and
    priority are `board-status`'s to write, so the ticket carries none of them yet.
    ``verified_on`` is the host its record and evidence name.
    """
    host = follow_up_tickets.Host(verified_on)
    origin = follow_up_tickets.Origin(_hosted(repository))
    medium = follow_up_tickets.Severity.MEDIUM
    impact = follow_up_tickets.impact_section(
        impact_prose("Readers of the board miss the ticket's status.", workaround="none"),
        medium,
        "none",
        medium,
    )
    return follow_up_tickets.Ticket(
        title=f"{repository.name}: {cause.replace('-', ' ')}",
        status=status,
        root_cause=follow_up_tickets.RootCause(cause),
        repository=origin,
        created_by_run=follow_up_tickets.RunId(PROPOSED_RUN),
        owning_runs=(follow_up_tickets.RunId(PROPOSED_RUN),),
        drafts=(follow_up_tickets.QualifiedDraftId(f"drafts:{PROPOSED_RUN}/drafts/noticed"),),
        basis=(follow_up_tickets.Basis(origin, follow_up_tickets.Commit("0" * 40)),),
        verified_at=follow_up_tickets.Timestamp("2026-01-01T00:00:00Z"),
        host=host,
        body="\n\n".join(
            f"## {heading}\n\n"
            + (
                impact
                if heading == follow_up_tickets.IMPACT
                else FIX
                if heading == follow_up_tickets.SUGGESTED_FIX
                else current_section(heading, f"Verified on `{host}` ({heading}).")
            )
            + (f" {evidence}" if evidence and heading == follow_up_tickets.EVIDENCE else "")
            for heading in follow_up_tickets.HEADINGS
        ),
        frequency=frequency,
    )


def _written_ticket(root: Path, ticket: follow_up_tickets.Ticket, text: str | None = None) -> Path:
    """Write ``ticket`` under a drafts root at ``root``, rendered unless ``text`` says otherwise."""
    path = follow_up_tickets.ticket_path(root, ticket.created_by_run, ticket.root_cause)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(follow_up_tickets.render(ticket) if text is None else text, encoding="utf-8")
    return path


def _followups_environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """The environment a `followups` command runs under, and the drafts root it names.

    Every command runs from this checkout, so the committed `onetaskgraph.yaml` decides the
    source's owner and where `backlog` goes; only the endpoint, the credential and the
    drafts root are pointed elsewhere. This checkout's installed CLIs are put first on
    `PATH`, since `board-status` resolves the plan store from it.
    """
    drafts_root = tmp_path / "follow-ups"
    environment = _plan_environment(tmp_path)
    for name in [name for name in environment if name.startswith(FOLLOWUPS_ENV_PREFIX)]:
        del environment[name]
    environment[follow_up_variables.root_name()] = str(drafts_root)
    environment[follow_up_variables.plugin_name()] = plan_store.WRITABLE_PLUGIN
    environment["PATH"] = f"{ONETASKGRAPH_BIN.parent}{os.pathsep}{environment['PATH']}"
    return environment, drafts_root


@contextmanager
def _serving_followups(
    environment: dict[str, str],
    options: tuple[_StatusOption, ...] = FOLLOWUPS_OPTIONS,
    *,
    fields: bool = False,
) -> Iterator[None]:
    """Serve the board fixture as `followups`, carrying ``options``, for ``environment``.

    With ``fields``, the board is first set up the way the operator sets up the live one —
    `just plans sources fields followups --apply`, the command `AGENTS.md` names — which
    creates the `Priority` field from the source's `priority_mapping`.
    """
    with _serving_board(options=options, number=FOLLOWUPS_PROJECT_NUMBER) as remote:
        environment.update(remote)
        environment["ONETASKGRAPH_SOURCES__FOLLOWUPS__CONFIG__PACING__MIN_MUTATION_INTERVAL_MS"] = (
            "0"
        )
        if fields:
            set_up = _followups_command(
                environment,
                ["just", "plans", "sources", "fields", follow_up_tickets.BOARD, "--apply"],
            )
            assert set_up.returncode == 0, set_up.stdout + set_up.stderr
            # Read back through the same verb's read-only plan, as the operator checks it.
            planned = _followups_command(
                environment,
                [str(ONETASKGRAPH_BIN), "sources", "fields", follow_up_tickets.BOARD, "--json"],
            )
            assert planned.returncode == 0, planned.stdout + planned.stderr
            (priority,) = [
                field
                for field in json.loads(planned.stdout)["fields"]
                if field["field"] == "Priority"
            ]
            assert (priority["exists"], priority["missing"]) == (True, []), priority
            assert [option["name"] for option in priority["existing"]] == [
                "Urgent",
                "High",
                "Medium",
                "Low",
            ], "the field setup did not create the Priority field the source maps"
        yield


def _followups_command(
    environment: dict[str, str], command: list[str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - the installed plan-store CLI and this checkout's module
        command,
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )


def _sent(
    operation: _Operation, requests: list[_GraphQLRequest] | None = None
) -> list[_GraphQLRequest]:
    return [
        request
        for request in (_GitHubFixture.requests if requests is None else requests)
        if request.operation is operation
    ]


def _field_writes(requests: list[_GraphQLRequest]) -> list[dict[str, object]]:
    """The input of every field-value write among ``requests``, batched or standalone.

    The source writes one field with `updateProjectV2ItemFieldValue` alone, and several of
    a copy's together as aliases of one request, each included by its `write…` flag; either
    way, each write GitHub would apply is one input here, in the order the request lists it.
    """
    written: list[dict[str, object]] = []
    for request in requests:
        carried = {
            _Operation.UPDATE_FIELD: BATCHED_WRITES[:1],
            _Operation.UPDATE_FIELDS: BATCHED_WRITES,
        }.get(request.operation, ())
        for write in carried:
            payload = request.variables.get(write.variable)
            if write.included(request.variables) and isinstance(payload, dict):
                written.append(payload)
    return written


def _field_clears(requests: list[_GraphQLRequest]) -> list[dict[str, object]]:
    """The input of every field-value clear among ``requests``, batched or standalone."""
    cleared: list[dict[str, object]] = []
    for request in requests:
        name = {_Operation.CLEAR_FIELD: "input", _Operation.UPDATE_FIELDS: "clear"}.get(
            request.operation
        )
        if name == "clear" and request.variables.get("writeClear") is not True:
            continue
        if name is not None and isinstance(payload := request.variables.get(name), dict):
            cleared.append(payload)
    return cleared


def _board_cost(requests: list[_GraphQLRequest]) -> tuple[list[str], list[str]]:
    """What in ``requests`` read more of the board than it asked for.

    The operations that walk the board's items, and every board search — a plain one, or the
    one an origin lookup sends beside its field filter — that names no field qualifier with a
    phrase to narrow it by, which GitHub would answer with every issue on the board.
    """
    walked = [
        str(request.operation) for request in requests if request.operation in BOARD_ENUMERATIONS
    ]
    unnarrowed = [
        search
        for request in requests
        if request.operation in (_Operation.SEARCH, _Operation.ORIGIN_LOOKUP)
        and _FIELD_QUALIFIER.search(search := request.string("search")) is None
    ]
    return walked, unnarrowed


def _follow_up_step(
    environment: dict[str, str], command: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    """One `orchestrator.follow_up_tickets` command, as the composed task has the agent run it."""
    return _followups_command(
        environment,
        [str(ONETASKGRAPH_BIN.parent / "python3"), "-m", "orchestrator.follow_up_tickets"]
        + [command, *arguments],
    )


def _decided_status(environment: dict[str, str], ticket: Path, *extra: str) -> str:
    """Run `board-status`, then write the word it prints as the ticket's `status`, as told."""
    decided = _follow_up_step(
        environment, "board-status", "--board", follow_up_tickets.BOARD, *extra, str(ticket)
    )
    assert decided.returncode == follow_up_tickets.SOUND, decided.stdout + decided.stderr
    word = decided.stdout.strip()
    text = ticket.read_text(encoding="utf-8")
    ticket.write_text(re.sub(r'^status: "[^"]*"$', f'status: "{word}"', text, flags=re.M), "utf-8")
    return word


class RequestCost(NamedTuple):
    """The requests and modelled GitHub points one journey step spent."""

    requests: int
    points: int


class DocumentPrice(NamedTuple):
    """One source document and its price in the upstream PRICES table."""

    document: str
    points: int


#: How many other items the `followups` stand-in holds when one ticket is filed on it: enough
#: that a request walking the board, or a search it answers unnarrowed, is a cost that grows
#: with the board rather than one a single item hides.
OTHER_ITEMS = 60
#: Board checks one follow-up launch's whole validation runs, dispatch through post-settle.
LAUNCH_VALIDATION_BOARD_CHECKS = 1
#: How many other items the realistic run's board holds before the run starts.
REALISTIC_OTHER_ITEMS = 400
#: The budget a realistic follow-up run's board points are, and the one a ticket's whole
#: life is: each journey records its parts under one of them, and `tests/budget_telemetry.py`
#: reports the figure and its breakdown.
RUN_POINTS_BUDGET = "follow-up-run-points"
TICKET_LIFECYCLE_BUDGET = "ticket-lifecycle-points"


#: The source's PRICES entries used by the fixture's operations, each priced upstream at
#: :data:`LARGEST_PAGE`. A search is priced per page from its entry by :func:`_search_points`.
REQUEST_PRICES: dict[_Operation, DocumentPrice] = {
    _Operation.SEARCH: DocumentPrice("SEARCH_ISSUES", 5),
    _Operation.ISSUE: DocumentPrice("ISSUE", 1),
    _Operation.SUB_ISSUES: DocumentPrice("SUB_ISSUES", 5),
    _Operation.BOARD: DocumentPrice("BOARD", 2),
    _Operation.ORIGIN_LOOKUP: DocumentPrice("ORIGIN_LOOKUP", 1),
    _Operation.BOARD_FIELDS: DocumentPrice("BOARD_FIELDS", 1),
    _Operation.CREATION_CONTEXT: DocumentPrice("CREATION_CONTEXT", 1),
    _Operation.ISSUE_DETAIL: DocumentPrice("ISSUE_DETAIL", 1),
    _Operation.ISSUE_DETAILS: DocumentPrice("ISSUE_DETAILS", 1),
    _Operation.FIELD_SNAPSHOT: DocumentPrice("STATUS_OPTIONS_SNAPSHOT", 1),
    _Operation.REPOSITORY: DocumentPrice("REPOSITORY", 1),
    _Operation.DEPENDENCIES: DocumentPrice("ISSUE_DEPENDENCIES", 1),
    _Operation.CREATE_ISSUE: DocumentPrice("CREATE_ISSUE", 1),
    _Operation.ADD_TO_BOARD: DocumentPrice("ADD_TO_BOARD", 1),
    _Operation.UPDATE_ISSUE: DocumentPrice("UPDATE_ISSUE", 1),
    _Operation.UPDATE_FIELD: DocumentPrice("UPDATE_FIELD", 1),
    _Operation.UPDATE_FIELDS: DocumentPrice("UPDATE_FIELDS", 1),
    _Operation.CLEAR_FIELD: DocumentPrice("CLEAR_FIELD", 1),
    _Operation.CREATE_FIELD: DocumentPrice("CREATE_FIELD", 1),
    _Operation.ADD_SUB_ISSUE: DocumentPrice("ADD_SUB_ISSUE", 1),
    _Operation.ADD_BLOCKED_BY: DocumentPrice("ADD_BLOCKED_BY", 1),
    _Operation.DELETE_ISSUE: DocumentPrice("DELETE_ISSUE", 1),
    _Operation.COMMENTS: DocumentPrice("ISSUE_COMMENTS", 1),
    _Operation.ADD_COMMENT: DocumentPrice("ADD_COMMENT", 1),
}


#: The largest `first` GitHub accepts on a connection, which the upstream PRICES table prices
#: every document at.
LARGEST_PAGE = 100
#: Search prices by page size the manager measured on 2026-10-01 against the `followups`
#: board with onetaskgraph CLI 0.2.52; :func:`_search_points` is drift-checked against them.
MEASURED_SEARCH_PAGE_PRICES = {20: 1, 30: 2, 50: 3, 100: 5}


#: The rows the adopted store asks for on a board search's first page — its
#: `SEARCH_PAGE_SIZE`, drift-checked against the adopted tag's source — and the widest page
#: a search whose whole answer fits on one may ask for.
STORE_FIRST_SEARCH_PAGE = 20


class WidePage(NamedTuple):
    """One board search that asked for a page wider than its whole answer."""

    #: The rows it asked for.
    first: int
    #: The rows that matched in all.
    matched: int


def _wide_pages(requests: list[_GraphQLRequest]) -> list[WidePage]:
    """Each board search that asked for a page wider than its answer needed.

    A search is priced by the rows it asks for rather than the rows it gets, so a page wider
    than the store's first-page size is points spent on rows that were never there whenever
    the whole answer fits in that first page.
    """
    wide: list[WidePage] = []
    for request in requests:
        if request.operation is not _Operation.SEARCH:
            continue
        first = request.variables.get("first")
        matched = request.answered.matched
        assert isinstance(first, int) and matched is not None, request
        if first > STORE_FIRST_SEARCH_PAGE and matched <= STORE_FIRST_SEARCH_PAGE:
            wide.append(WidePage(first, matched))
    return wide


def _search_points(first: int) -> int:
    """A search page's points: its largest-page price scaled to ``first``, rounded up."""
    return -(-first * REQUEST_PRICES[_Operation.SEARCH].points // LARGEST_PAGE)


def _request_points(request: _GraphQLRequest) -> int:
    """Price every document encountered; an unpriced document fails the journey."""
    assert request.operation in REQUEST_PRICES, f"unpriced document: {request.query}"
    if request.operation is _Operation.SEARCH:
        first = request.variables["first"]
        assert isinstance(first, int), request.variables
        return _search_points(first)
    return REQUEST_PRICES[request.operation].points


#: Python's audit hook records the real CLI subprocesses without replacing any command.
#: sys.orig_argv also records validators started by the real attached recipe's shell.
STORE_CALL_AUDIT = """\
import json, os, pathlib, sys, time
trace = pathlib.Path(os.environ["FOLLOW_UP_BUDGET_TRACE"])
def record(kind, argv):
    # The system-wide monotonic clock the board stand-in stamps each request with.
    entry = {"kind": kind, "argv": argv, "at": time.monotonic()}
    with trace.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry) + "\\n")
if "orchestrator.follow_up_tickets" in sys.orig_argv:
    record("module", sys.orig_argv)
def audit(event, args):
    if event == "subprocess.Popen" and pathlib.Path(args[0]).name == "onetaskgraph":
        record("store", args[1])
sys.addaudithook(audit)
"""


def _audit_follow_up_calls(root: Path, environment: dict[str, str]) -> Path:
    """Install an observer in test Python processes; every store subprocess stays real."""
    observer = root / "audit"
    observer.mkdir()
    (observer / "sitecustomize.py").write_text(STORE_CALL_AUDIT, encoding="utf-8")
    trace = root / "store-calls.jsonl"
    environment["FOLLOW_UP_BUDGET_TRACE"] = str(trace)
    environment["PYTHONPATH"] = os.pathsep.join((str(observer), str(REPO_ROOT)))
    return trace


def _once_per_store_call(trace: Path) -> list[list[str]]:
    """Assert one show/comment-list per item and one local show/dependency walk per ticket."""
    calls = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    store = [call["argv"] for call in calls if call["kind"] == "store"]
    shows = [argv[3] for argv in store if argv[1:3] == ["task", "show"]]
    comments = [argv[4] for argv in store if argv[1:4] == ["task", "comment", "list"]]
    deps = [argv[3] for argv in store if argv[1:3] == ["task", "deps"]]
    for names in (shows, comments, deps):
        assert len(names) == len(set(names)), names
    assert not set(shows) & set(comments), (shows, comments)
    trace.write_text("", encoding="utf-8")
    return store


def _store_launches(trace: Path) -> list[float]:
    """When each real store process the audited Python launched started, in launch order."""
    calls = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    return [float(call["at"]) for call in calls if call["kind"] == "store"]


def _per_invocation(
    requests: list[_GraphQLRequest], launches: list[float]
) -> list[list[_GraphQLRequest]]:
    """The step's requests grouped by the store invocation that sent each one.

    The store is invoked one process at a time, so a request belongs to the last launch
    before the stand-in received it. Requests received before the first audited launch came
    from the one store command the journey ran itself, outside the audited Python.
    """
    groups: list[list[_GraphQLRequest]] = [[] for _ in range(len(launches) + 1)]
    for request in requests:
        groups[bisect.bisect_right(launches, request.received)].append(request)
    return [group for group in groups if group]


def _bounded_step(
    label: str,
    *,
    bound: bool = False,
    allowed_comments: set[str] | None = None,
    trace: Path | None = None,
    writes: bool = False,
    invocations: Path | None = None,
) -> RequestCost:
    """What the step just taken spent, holding it to one read per item and connection.

    Each comment connection is read once, and so is each item a read-only step resolves. A
    write step runs several store processes (a read, then each write), each resolving the
    item it writes again, so it is held to one resolution per item within each invocation,
    which its ``trace`` of store launches tells apart; ``invocations`` names a launch trace
    to group by alone, when the step's trace also holds launches earlier steps made. With
    ``bound`` the step sends no board search or origin lookup, and no step walks the board
    or asks for a page wider than its answer. What it spent is returned for the journey to
    record, never held to a ceiling here.
    """
    launch_trace = invocations or trace
    launches = _store_launches(launch_trace) if launch_trace is not None else None
    if writes and launches is None:
        raise ValueError(f"{label}: a write step needs the trace of its store invocations")
    if trace is not None:
        _once_per_store_call(trace)
    requests = list(_GitHubFixture.requests)
    cost = RequestCost(len(requests), sum(_request_points(request) for request in requests))
    print(f"{label}: requests={cost.requests}, points={cost.points}")
    for operation in (_Operation.ISSUE, _Operation.COMMENTS):
        scopes = (
            _per_invocation(requests, launches)
            if writes and launches is not None and operation is _Operation.ISSUE
            else [requests]
        )
        for scope in scopes:
            identifiers = [
                request.string("id") for request in scope if request.operation is operation
            ]
            assert all(identifiers.count(one) == 1 for one in identifiers), (
                label,
                operation,
                identifiers,
            )
        identifiers = [
            request.string("id") for request in requests if request.operation is operation
        ]
        if operation is _Operation.COMMENTS and allowed_comments is not None:
            assert set(identifiers) <= allowed_comments, (label, identifiers, allowed_comments)
    if bound:
        assert not _sent(_Operation.SEARCH, requests), label
        assert not _sent(_Operation.ORIGIN_LOOKUP, requests), label
    assert _board_cost(requests) == ([], []), label
    assert _wide_pages(requests) == [], (label, "a search asked for a page wider than its answer")
    _GitHubFixture.requests.clear()
    return cost
