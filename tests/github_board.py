"""Stateful loopback Projects board and request budgets shared by real store journeys.

Only the GitHub wire is simulated: the installed store, follow-up commands and recipes
remain real. Keeping this fixture outside test modules avoids unrelated test cache inputs.
"""

from __future__ import annotations

import bisect
import json
import os
import random
import re
import subprocess
import threading
import time
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar, Literal, NamedTuple, NewType

import follow_up_variables
import onetaskgraph_user_layer
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


#: The credential the example user layer's Linear sources name, and the loopback port nothing
#: listens on that a board journey points those sources at (`tests/onetaskgraph_user_layer.py`).
LINEAR_KEY_ENV = onetaskgraph_user_layer.LINEAR_KEY_ENV
UNSERVED_LINEAR = onetaskgraph_user_layer.UNSERVED_LINEAR
#: Where the example's `hiddenco-plans` and `hiddenco-followups` sources' endpoints are named,
#: which the store spells with each source name's hyphen as an underscore.
LINEAR_PLANS_ENDPOINT_ENV = "ONETASKGRAPH_SOURCES__HIDDENCO_PLANS__CONFIG__ENDPOINT"
LINEAR_FOLLOWUPS_ENDPOINT_ENV = "ONETASKGRAPH_SOURCES__HIDDENCO_FOLLOWUPS__CONFIG__ENDPOINT"


def unserved_linear(linear: bool) -> dict[str, str]:
    """What points the example layer's Linear sources at :data:`UNSERVED_LINEAR`, if ``linear``.

    Only a journey whose user layer declares those sources as Linear ones names ``linear``:
    the tracked document declares neither, so an endpoint stated for one would be half a
    source the store refuses, and a layer standing a folder in for one refuses the key.
    """
    if not linear:
        return {}
    return {
        LINEAR_KEY_ENV: "fixture-linear-key",
        LINEAR_PLANS_ENDPOINT_ENV: UNSERVED_LINEAR,
        LINEAR_FOLLOWUPS_ENDPOINT_ENV: UNSERVED_LINEAR,
    }


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


#: The board text field the `followups` source's `metadata_fields` projects a ticket's host
#: onto. Every board here carries it unless a journey takes it away, as the live one does
#: once `sources fields followups --apply` has created it; the `plans` source projects
#: nothing, so carrying it there changes nothing it writes.
HOST_FIELD_NAME = "Host"


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
    HOST = "FIELD_host"


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


#: A synthetic owner whose repositories are private, as the example user layer's
#: `private-plans` source names it: the repository that source's issues are created in, and a
#: second one a private task names. Synthetic, so no journey names a real private repository.
PRIVATE_OWNER = "sample-owner"
PRIVATE_STORE_REPOSITORY = _Repository(owner=PRIVATE_OWNER, name="private-store")
PRIVATE_LIBRARY_REPOSITORY = _Repository(owner=PRIVATE_OWNER, name="hidden-library")
#: A synthetic public repository of the configured owner, which a public task names: under
#: that owner so a `plans` view of it is filed in it beneath the configured repository's
#: project issue, as GitHub files a sub-issue only in a repository of its parent's owner.
PUBLIC_SAMPLE_REPOSITORY = _Repository(owner=CONFIGURED_REPOSITORY.owner, name="open-sample")


REPOSITORY_NODE_IDS: dict[_Repository, _RepositoryNodeId] = {
    CONFIGURED_REPOSITORY: _RepositoryNodeId("R_ai_orchestrator"),
    SIBLING_REPOSITORY: _RepositoryNodeId("R_oneharness"),
    PRIVATE_STORE_REPOSITORY: _RepositoryNodeId("R_private_store"),
    PRIVATE_LIBRARY_REPOSITORY: _RepositoryNodeId("R_hidden_library"),
    PUBLIC_SAMPLE_REPOSITORY: _RepositoryNodeId("R_open_sample"),
}


#: The repositories GitHub answers `"visibility": "private"` for; every other repository the
#: stand-in knows answers `"public"`, as this repository and its sibling are.
PRIVATE_REPOSITORIES: frozenset[_Repository] = frozenset(
    {PRIVATE_STORE_REPOSITORY, PRIVATE_LIBRARY_REPOSITORY}
)


def repository_visibility(repository: _Repository) -> Literal["public", "private"]:
    """What GitHub's REST repository endpoint answers as ``repository``'s `visibility`."""
    return "private" if repository in PRIVATE_REPOSITORIES else "public"


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


class _IssueState(NamedTuple):
    """What an issue's own `updatedAt` moves for: what a person edits on the issue itself."""

    title: str
    body: str
    state: str
    state_reason: str | None
    parent: _IssueNodeId | None
    sub_issues: int
    repository: _Repository


class _RowState(NamedTuple):
    """What a board row's `updatedAt` moves for: the values of its fields."""

    status: str
    priority: str | None
    origin: str | None
    host: str | None


class _Edge(NamedTuple):
    """One relationship an issue's timeline counts: an edge either way, or its board."""

    kind: Literal["blocked-by", "blocking", "board"]
    node: str


#: When every issue of the stand-in was created, and last changed before any comment.
ISSUE_CREATED_AT = "2026-08-26T00:00:00Z"


def _one_second_after(moment: str) -> str:
    """The RFC 3339 instant one second after ``moment``, as GitHub spells one."""
    later = datetime.fromisoformat(moment) + timedelta(seconds=1)
    return later.strftime("%Y-%m-%dT%H:%M:%SZ")


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
    #: The text this row's `Host` field holds, as the last write projecting the record's host
    #: left it — kept and answered back for the reason `origin` is, since the source sends a
    #: projected value again only when the row does not already hold it.
    host: str | None = None
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
    #: When the issue and its board row last changed, and how many relationship events its
    #: timeline holds: three of the five parts of the change token the adopted store builds a
    #: task's `version` from (`docs/plugin-protocol.md` §4.13d), which it then serves its
    #: cache against. Moved by `_observed` whenever what each part covers differs from what it
    #: last answered — however that came about, a write or a journey planting a state on the
    #: fixture — and only ever forward, so a cache entry a journey filled is never served for
    #: an issue that has since changed.
    changed_at: str = field(default=ISSUE_CREATED_AT, compare=False)
    item_changed_at: str = field(default=ISSUE_CREATED_AT, compare=False)
    relationship_events: int = field(default=0, compare=False)
    #: Whether the create that made this issue filed it on its board through `projectV2Ids`,
    #: and the moment on the monotonic clock GitHub places it there — `None` once it is on the
    #: board. Modelled because GitHub answers such a create with no board item and adds the
    #: item some time later (:data:`MEASURED_PLACEMENT_SECONDS`), so a read of the issue's
    #: board items before then finds none, and a later `addProjectV2ItemById` is refused.
    filed_by_create: bool = field(default=False, compare=False)
    placed_at: float | None = field(default=None, compare=False)
    _seen: _IssueState | None = field(default=None, compare=False, repr=False)
    _seen_row: _RowState | None = field(default=None, compare=False, repr=False)
    _seen_edges: frozenset[_Edge] | None = field(default=None, compare=False, repr=False)

    def _observed(self) -> None:
        """Move each token part whose subject changed since it was last answered.

        A first answer records what it saw and moves nothing: that is the issue as created.
        """
        state = _IssueState(
            self.title,
            self.body,
            self.state,
            self.state_reason,
            self.parent_id,
            self.sub_issues,
            self.repository,
        )
        row = _RowState(self.status, self.priority, self.origin, self.host)
        edges = frozenset(
            {_Edge("blocked-by", str(held)) for held in self.blocked_by}
            | {
                _Edge("blocking", str(other.content_id))
                for other in (self.board.issues if self.board is not None else ())
                if self.content_id in other.blocked_by
            }
            | ({_Edge("board", str(self.board.node_id))} if self.board is not None else set())
        )
        if self._seen is not None and state != self._seen:
            self.changed_at = _one_second_after(self.updated_at())
        if self._seen_row is not None and row != self._seen_row:
            self.item_changed_at = _one_second_after(self.item_changed_at)
        self.relationship_events += len(edges ^ (self._seen_edges or frozenset()))
        self._seen, self._seen_row, self._seen_edges = state, row, edges

    @property
    def placed(self) -> bool:
        """Whether this issue is on its board yet, as GitHub's delayed placement leaves it."""
        return self.placed_at is None or time.monotonic() >= self.placed_at

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
        host = (
            []
            if self.host is None
            else [
                {
                    "text": self.host,
                    "field": {"id": _BoardField.HOST, "name": HOST_FIELD_NAME},
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
                *host,
            ],
            "pageInfo": {"hasNextPage": False},
        }

    def content(self) -> dict[str, object]:
        """The issue itself, as every document that reaches it selects it."""
        self._observed()
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
            # The two counts the store's `BoardIssue` fragment selects under these aliases for
            # the change token.
            "commentCount": {"totalCount": len(self.comments)},
            "relationshipEvents": {"totalCount": self.relationship_events},
            "labels": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        }

    def updated_at(self) -> str:
        """When this issue last changed, which GitHub moves when one of its comments changes.

        The board-scoped search answering a `commented_since` query narrows on it, so an issue
        that kept the moment it was created would never be found commented on.
        """
        return max(
            [
                ISSUE_CREATED_AT,
                self.changed_at,
                *(str(comment["updatedAt"]) for comment in self.comments),
            ]
        )

    def held_issue(self) -> dict[str, object]:
        """This issue as the store's by-reference read selects it: its row's `Status` alone.

        The read names the field by name rather than walking every value, so a membership
        carries one `status` member — the option the row is at, with the field it belongs to.
        """
        return self.content() | {
            "projectItems": {
                "nodes": [
                    {
                        "id": self.item_id,
                        "updatedAt": self.item_changed_at,
                        "project": {"id": self.held_by.node_id, "number": self.held_by.number},
                        "status": {
                            "name": self.status,
                            "field": {
                                "id": _BoardField.STATUS,
                                "name": "Status",
                                "options": [option.rendered() for option in self.held_by.options],
                            },
                        },
                    }
                ],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }

    def item(self) -> dict[str, object]:
        """This issue as a row of the board's own `items` connection."""
        content = self.content()
        return {
            "id": self.item_id,
            "updatedAt": self.item_changed_at,
            "fieldValues": self.field_values(),
            "content": content,
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
                        "updatedAt": self.item_changed_at,
                        "project": {"id": self.held_by.node_id, "number": self.held_by.number},
                        "fieldValues": self.field_values(),
                    }
                ],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
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


#: The one write of the source's standalone field-write document, always sent.
SINGLE_WRITE = _BatchedWrite("updateProjectV2ItemFieldValue", "input", None)


#: The six field writes of the source's batched document, in the order it lists them.
BATCHED_WRITES = (
    _BatchedWrite("updateProjectV2ItemFieldValue", "input", "writeFirst"),
    _BatchedWrite("second", "second", "writeSecond"),
    _BatchedWrite("third", "third", "writeThird"),
    _BatchedWrite("fourth", "fourth", "writeFourth"),
    _BatchedWrite("fifth", "fifth", "writeFifth"),
    _BatchedWrite("sixth", "sixth", "writeSixth"),
)


#: The three field clears of the source's batched document, which it runs after its writes.
BATCHED_CLEARS = (
    _BatchedWrite("cleared", "clear", "writeClear"),
    _BatchedWrite("clearedSecond", "clearSecond", "writeClearSecond"),
    _BatchedWrite("clearedThird", "clearThird", "writeClearThird"),
)


#: How many slots the adopted store's batched copy documents carry: its `MUTATION_BATCH`
#: creates and closes, its `WRITE_BATCH` field values and blocked-by edges — of each kind, in
#: a request carrying both — its `DELETE_BATCH` deletes, its `ORIGIN_BATCH` origins and its
#: `COPY_DETAIL_BATCH` created items.
COPY_WRITE_SLOTS = 11
COPY_WIDE_SLOTS = 19
COPY_DELETE_SLOTS = 5
COPY_ORIGIN_SLOTS = 10
COPY_READ_SLOTS = 21

#: How long after its create was answered GitHub placed each child issue a create filed on a
#: board with `projectV2Ids`, in milliseconds: the feasibility spike's ten samples of eight
#: children each (`board-copy-gaps-2026-10-24-spike-feasibility-report`, its `probe-placement`
#: runs), in the order measured — 0.45 to 1.84 seconds, and never in the create's own answer.
#: The same samples onetaskgraph's loopback board replays, as its `MEASURED_PLACEMENT_MILLIS`
#: in `crates/onetaskgraph-e2e-support/src/fixtures.rs`, which
#: `tests/board_copy_quota_handover/test_board_copy_quota_handover.py` holds this copy to at the
#: release `config/onetaskgraph.version` names.
MEASURED_PLACEMENT_MILLIS = (
    *(453,) * 7,
    1423,
    *(482,) * 7,
    1622,
    *(509,) * 7,
    1555,
    *(548,) * 7,
    1553,
    *(577,) * 7,
    1526,
    *(537,) * 8,
    *(523,) * 7,
    1837,
    *(614,) * 8,
    *(626,) * 7,
    1692,
    *(568,) * 8,
)
#: Those delays in seconds, as a board's `placement_delays` takes them.
MEASURED_PLACEMENT_SECONDS = tuple(millis / 1000 for millis in MEASURED_PLACEMENT_MILLIS)


def batched_slot_drift(query: str) -> list[str]:
    """How the batched field document the store sent differs from the slots modelled above.

    Read off the document itself on every batched write, so a release that adds, drops or
    renames a slot fails the journey that met it, naming the slot, rather than having its
    writes applied or ignored by a model of the release before it.
    """
    spelled = [
        f"{'' if slot.alias == SINGLE_WRITE.alias else slot.alias + ':'}"
        f"updateProjectV2ItemFieldValue(input:${slot.variable}) @include(if:${slot.flag})"
        for slot in BATCHED_WRITES
    ] + [
        f"{slot.alias}:clearProjectV2ItemFieldValue(input:${slot.variable}) "
        f"@include(if:${slot.flag})"
        for slot in BATCHED_CLEARS
    ]
    drift = [f"the batched field document lacks `{slot}`" for slot in spelled if slot not in query]
    sent = query.count("@include(")
    if sent != len(spelled):
        drift.append(
            f"the batched field document carries {sent} slots and the board models {len(spelled)}"
        )
    return drift


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
    #: What a created issue's node ids start from, so two boards served at once never answer
    #: one id for two issues.
    created_prefix: ClassVar[str] = "created"

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
        #: Each user attachment uploaded to it, by the path GitHub serves it at.
        self.attachments: dict[str, bytes] = {}
        #: Whether the board carries the `Host` text field, which one journey takes away.
        self.has_host_field = True
        #: How many of an issue's comments one page answers, or `None` for every one on the
        #: first page: GitHub pages an issue's comments, and a reader that reads past the first
        #: page reads an issue that can change between its pages.
        self.comment_page: int | None = None
        #: Whether a comment a write adds is dated by the wall clock, as GitHub dates one, rather
        #: than by the board's own counter: what a journey reasoning about when things happened
        #: — a boundary, a watermark — needs, and what a priced journey's fixed answers do not.
        self.real_time = False
        #: What the board keeps of a comment an edit sends, from the body it held and the body
        #: sent, and answers that edit with: the body sent, as GitHub keeps it, unless a journey
        #: stands in an edit the board answers without applying it.
        self.stored_comment: Callable[[str, str], str] = lambda _held, sent: sent
        #: How long after its create GitHub places each issue a create files on this board
        #: through `projectV2Ids`, each create's in turn and cycled, or empty to place every
        #: one at once: a journey measuring a copy sets :data:`MEASURED_PLACEMENT_SECONDS`.
        self.placement_delays: tuple[float, ...] = ()
        #: How many placement delays this board has handed out, which picks the next one.
        self.placements = 0
        #: Whether the Project is public, which is what GitHub answers as its `public` field:
        #: the `plans` board is, and a host's private board is not.
        self.public = True

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
                    "dataType": "TEXT",
                },
                *(
                    [
                        {
                            "__typename": "ProjectV2Field",
                            "id": _BoardField.HOST,
                            "name": HOST_FIELD_NAME,
                            "dataType": "TEXT",
                        }
                    ]
                    if self.has_host_field
                    else []
                ),
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

    def project_visibility_response(self, variables: Mapping[str, object]) -> dict[str, object]:
        """Whether the Project is public, as GitHub answers its `ProjectV2.public` field.

        A number this board is not served under is a Project GitHub cannot find: a null
        `projectV2` and an error naming it, as GitHub answers one.
        """
        # llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] The read is the
        # installed plan store's own `PROJECT_VISIBILITY`, reconciled on every journey: an
        # answer under any other root is a board it reports as unreadable.
        if variables.get("number") != self.number:
            return {
                "data": {"visibility": {"projectV2": None}},
                "errors": [
                    {
                        "type": "NOT_FOUND",
                        "message": f"Could not resolve to a ProjectV2 with the number "
                        f"{variables.get('number')}.",
                    }
                ],
            }
        return {"data": {"visibility": {"projectV2": {"public": self.public}}}}
        # llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]

    def board_response(self) -> dict[str, object]:
        return {
            "data": {
                "owner": {
                    "projectV2": {
                        "id": self.node_id,
                        "title": self.title,
                        "fields": self._fields(),
                        "items": {
                            "nodes": [issue.item() for issue in self.issues if issue.placed],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        },
                    }
                }
            }
        }

    def closed_items_response(self, variables: dict[str, object]) -> dict[str, object]:
        """The board's rows whose issue is closed, as the reconcile's `is:closed` read asks.

        Only the filter the store sends is understood, and any other is refused rather than
        answered with every row: the reconcile's whole claim is that it reads the closed
        items and never the board, and a fixture answering the open rows too would hide a
        store that had stopped narrowing. Paged as `search_response` pages.
        """
        if variables.get("filter") != "is:closed":
            raise ValueError(f"the closed-items read sent another filter: {variables!r}")
        closed = [issue for issue in self.issues if issue.state == "CLOSED"]
        after, first = variables.get("after"), variables.get("first")
        start = int(after) if isinstance(after, str) else 0
        end = start + first if isinstance(first, int) else len(closed)
        return {
            "data": {
                "closedItems": {
                    "projectV2": {
                        "id": self.node_id,
                        "fields": self._fields(),
                        "items": {
                            "nodes": [issue.item() for issue in closed[start:end]],
                            "pageInfo": {
                                "hasNextPage": end < len(closed),
                                "endCursor": str(end) if end < len(closed) else None,
                            },
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

    def origin_lookup_response(
        self, variables: dict[str, object], first: object = None
    ) -> dict[str, object]:
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
        searched = self.search_response(search, first)["data"]
        if not isinstance(searched, dict):
            raise ValueError("the origin search returned no data object")
        # Paged as the search is when the request names a page size — the batched lookup asks
        # for a page of one carrier — so a second carrier of one origin says there is more.
        rows = [issue for issue in self.issues if issue.origin == origin and issue.placed]
        page = rows[:first] if isinstance(first, int) else rows
        return {
            "data": {
                "originItems": {
                    "projectV2": {
                        "items": {
                            "nodes": [issue.item() for issue in page],
                            "pageInfo": {
                                "hasNextPage": len(page) < len(rows),
                                "endCursor": str(len(page)) if len(page) < len(rows) else None,
                            },
                        }
                    }
                },
                **searched,
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
                return {
                    "data": {
                        "node": issue.board_issue()
                        | {
                            "boards": {"nodes": [{"project": project}]},
                            "blockedBy": self._blockers(issue),
                        }
                    }
                }
        return {"data": {"node": None}}

    def members_page(self, project: _Issue, first: object) -> tuple[list[_Issue], bool]:
        """A page of at most ``first`` issues filed under ``project``, and whether more follow."""
        members = [held for held in self.issues if held.parent_id == project.content_id]
        page = members[:first] if isinstance(first, int) else members
        return page, len(page) < len(members)

    def document_in_project_response(self, variables: dict[str, object]) -> dict[str, object]:
        """One document by its id, as :meth:`node_response` reads one, with its project beside it.

        The project issue the document is filed under, whole, and a page of that project's
        members, each without a page of what blocks it — what a guarded metadata write reads to
        hold the document to who may read its project. A document filed under nothing answers
        `project` as GitHub answers an issue with no parent: null.
        """
        answered = self.node_response(variables.get("id"))
        data = answered["data"]
        assert isinstance(data, dict)
        node = data["node"]
        if not isinstance(node, dict):
            return answered
        issue = self._issue(variables.get("id"))
        if issue.parent_id is None:
            node["project"] = None
            return answered
        project = self._issue(issue.parent_id)
        page, more = self.members_page(project, variables.get("memberFirst"))
        node["project"] = project.board_issue() | {
            "subIssues": {
                "pageInfo": {"hasNextPage": more, "endCursor": str(len(page)) if more else None},
                "nodes": [member.board_issue() for member in page],
            }
        }
        return answered

    def issues_by_reference_response(self, variables: dict[str, object]) -> dict[str, object]:
        """The issues one batch names by repository and number, each with its row's `Status`.

        What the adopted store reads to complete the issues a landing closes: up to fifty
        aliases `r<n>`, the slots it has no reference for repeating its last one. An issue this
        board does not hold is answered as GitHub answers one it cannot resolve — a null
        `issue` and a `NOT_FOUND` error at that alias — which the store reads as absent here.
        """
        data: dict[str, object] = {}
        errors: list[dict[str, object]] = []
        slot = 0
        while f"owner{slot}" in variables:
            alias = f"r{slot}"
            wanted = _Repository(
                owner=str(variables[f"owner{slot}"]), name=str(variables[f"name{slot}"])
            )
            number = variables[f"number{slot}"]
            held = [
                issue
                for issue in self.issues
                if issue.repository == wanted and issue.number == number
            ]
            if held:
                data[alias] = {"issue": held[0].held_issue()}
            else:
                data[alias] = {"issue": None}
                errors.append(
                    {
                        "type": "NOT_FOUND",
                        "path": [alias, "issue"],
                        "message": f"Could not resolve to an Issue with the number of {number}.",
                    }
                )
            slot += 1
        return {"data": data, **({"errors": errors} if errors else {})}

    def sub_issues_response(self, node_id: object) -> dict[str, object]:
        """One issue's sub-issues, which is how this board reports a project's tasks.

        Answered as GitHub answers the store's members listing: the project issue's own fields
        and what blocks it, beside each member with its own fields and what blocks it, every
        one of which the store's query selects. A listing answered without them reads to the
        store as members it has to read again, one by one.
        """
        for issue in self.issues:
            if issue.content_id != node_id:
                continue
            children = [held for held in self.issues if held.parent_id == issue.content_id]
            return {
                "data": {
                    "node": issue.board_issue()
                    | {
                        "blockedBy": self._blockers(issue),
                        "subIssues": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                child.board_issue() | {"blockedBy": self._blockers(child)}
                                for child in children
                            ],
                        },
                    }
                }
            }
        return {"data": {"node": None}}

    def _blockers(self, issue: _Issue) -> dict[str, object]:
        """The one page of what blocks ``issue``, as a `blockedBy` connection answers it."""
        return {
            "nodes": [self._issue(held).content() for held in issue.blocked_by],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }

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
            item_id=_BoardItemId(f"PVTI_{self.created_prefix}_{len(self.created)}"),
            content_id=_IssueNodeId(f"I_{self.created_prefix}_{len(self.created)}"),
            number=3 + len(self.created),
            title=title,
            body=body,
            repository=_repository_of(payload.get("repositoryId")),
        )
        created.board = self
        # Filed on this board by the create itself, as a copy's `projectV2Ids` does — and,
        # where the board models it, placed only the measured delay after the answer.
        filed = payload.get("projectV2Ids")
        created.filed_by_create = isinstance(filed, list) and self.node_id in filed
        if created.filed_by_create and self.placement_delays:
            delay = self.placement_delays[self.placements % len(self.placement_delays)]
            self.placements += 1
            created.placed_at = time.monotonic() + delay
        # Filed under its project by the create itself, as a copy's `parentIssueId` does.
        if (parent := payload.get("parentIssueId")) is not None:
            held = self._issue(parent)
            created.parent_id = held.content_id
            held.sub_issues += 1
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
        issue = self._issue(payload.get("contentId"))
        # GitHub files an issue once: one its create has placed already is refused rather
        # than answered with the item it holds.
        if issue.filed_by_create and issue.placed:
            return {
                "data": {"addProjectV2ItemById": None},
                "errors": [{"message": "Content already exists in this project"}],
            }
        issue.placed_at = None
        return {"data": {"addProjectV2ItemById": {"item": {"id": issue.item_id}}}}

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
            case _BoardField.HOST:
                self._row(item_id).host = self._text(value)
            case _BoardField.PRIORITY:
                self._row(item_id).priority = self._option_named(value, self.priorities)
        return item_id

    def _cleared(self, payload: object) -> object:
        """Clear one field value on a row: a priority of `none`, or a host no longer recorded."""
        if not isinstance(payload, dict):
            raise ValueError("clearProjectV2ItemFieldValue requires an input object")
        item_id = payload.get("itemId")
        match payload.get("fieldId"):
            case _BoardField.PRIORITY:
                self._row(item_id).priority = None
            case _BoardField.HOST:
                self._row(item_id).host = None
            case _:
                raise ValueError(f"only Priority and Host are cleared here, not {payload!r}")
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
        for clear in BATCHED_CLEARS:
            if clear.included(variables):
                cleared = self._cleared(variables.get(clear.variable))
                answer[clear.alias] = {"projectV2Item": {"id": cleared}}
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
        """Create the board's `Priority` field, or its missing `Host` text field.

        `Priority` takes the options the setup sends, in its order. `Host` is answered with
        nothing, as GitHub answers a text field to a document selecting only a single-select
        field's fragment.
        """
        match variables.get("input"):
            case {"dataType": "TEXT", "name": str(name)} if (
                name == HOST_FIELD_NAME and not self.has_host_field
            ):
                self.has_host_field = True
                return {"data": {"createProjectV2Field": {"projectV2Field": {}}}}
            case {"name": "Priority", "singleSelectOptions": list(options)} if not self.priorities:
                pass
            case payload:
                raise ValueError(
                    f"only a missing Host text field or a Priority field with options is "
                    f"created here, once: {payload!r}"
                )
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

    def _comment_page(self, issue: _Issue, after: object = None) -> dict[str, object]:
        """One page of ``issue``'s comments after the cursor ``after``, as GitHub pages them."""
        start = int(str(after)) if isinstance(after, str) and after.isdigit() else 0
        size = self.comment_page or max(len(issue.comments), 1)
        end = start + size
        more = end < len(issue.comments)
        return {
            "nodes": issue.comments[start:end],
            "pageInfo": {"hasNextPage": more, "endCursor": str(end) if more else None},
        }

    def comments_response(self, node_id: object, after: object = None) -> dict[str, object]:
        """One issue's comments, oldest first, as its `comments` connection lists them."""
        listed = self._comment_page(self._issue(node_id), after)
        return {"data": {"node": {"__typename": "Issue", "comments": listed}}}

    def _detail(
        self, node_id: object, comments: bool, after: object = None
    ) -> dict[str, object] | None:
        """One issue by its own node id with, when asked, the page of its comments after
        ``after``: the first, unless a reader paging them names its cursor."""
        for issue in self.issues:
            if issue.content_id == node_id:
                answered = issue.board_issue()
                if comments:
                    answered["comments"] = self._comment_page(issue, after)
                return answered
        return None

    def copy_details_response(self, variables: dict[str, object]) -> dict[str, object]:
        """A copy's batch of the tasks it carries: each with its comments and its `blockedBy`."""
        slots = sorted(int(name[2:]) for name in variables if re.fullmatch(r"id\d+", name))
        answer: dict[str, object] = {}
        for n in slots:
            detail = self._detail(variables[f"id{n}"], comments=True)
            if detail is not None:
                detail["blockedBy"] = self._blockers(self._issue(detail["id"]))
            answer[f"i{n}"] = detail
        # Which issues the project `$project` holds, read in the same request while
        # `$children` asks: what a project copy's orphan check is answered by.
        if variables.get("children") is True:
            project = self._issue(variables.get("project"))
            answer["children"] = {
                "id": project.content_id,
                "subIssues": {
                    "nodes": [
                        {"id": held.content_id, "title": held.title}
                        for held in self.issues
                        if held.parent_id == project.content_id
                    ],
                    "pageInfo": {"hasNextPage": False},
                },
            }
        return {"data": answer}

    def write_batch(
        self,
        parts: Sequence[_BatchPart],
        writes: Mapping[_Operation, Callable[[dict[str, object]], dict[str, object]]],
    ) -> Callable[[dict[str, object]], dict[str, object]]:
        """A copy's aliased writes: each sent slot of each part applied, in the order sent.

        A part's slot `<n>` carries its input as `$<prefix><n>` and is sent only while
        `$<send><n>` holds, so only those are applied and answered — under `<prefix><n>`, as
        the single write's own root answers — which is what GitHub does with an
        `@include(if:)`, running a document's mutation fields in the order it lists them.
        """

        def answered(variables: dict[str, object]) -> dict[str, object]:
            answer: dict[str, object] = {}
            for part in parts:
                for n in part.sent(variables):
                    data = writes[part.write]({"input": variables[f"{part.prefix}{n}"]})["data"]
                    assert isinstance(data, dict)
                    answer[f"{part.prefix}{n}"] = data[part.root]
            return {"data": answer}

        return answered

    def created_items_response(self, variables: dict[str, object]) -> dict[str, object]:
        """The board item of each issue a copy created, with its `Status`, by aliased node reads.

        Slot `<n>` reads the issue `$id<n>` names only while `$read<n>` holds.
        """
        slots = sorted(int(name[4:]) for name in variables if re.fullmatch(r"read\d+", name))
        answer: dict[str, object] = {}
        for n in slots:
            if variables[f"read{n}"] is not True:
                continue
            issue = self._issue(variables[f"id{n}"])
            placed = [
                {
                    "id": issue.item_id,
                    "project": {"id": self.node_id},
                    "status": {"name": issue.status},
                }
            ]
            answer[f"i{n}"] = {
                "id": issue.content_id,
                "projectItems": {"nodes": placed if issue.placed else []},
            }
        return {"data": answer}

    def whole_answer(
        self, root: str, write: Callable[[dict[str, object]], dict[str, object]]
    ) -> Callable[[dict[str, object]], dict[str, object]]:
        """``write``, answered as its whole twin answers: with the issue it left behind.

        Applied first and rendered after, so the issue answered carries the change token its
        write moved — the version the store advances its cache entry to.
        """

        def answered(variables: dict[str, object]) -> dict[str, object]:
            data = write(variables)["data"]
            assert isinstance(data, dict)
            payload = data[root]
            assert isinstance(payload, dict)
            match root:
                case "addComment":
                    subject = payload["subject"]
                    assert isinstance(subject, dict)
                    payload["subject"] = self._detail(subject["id"], comments=True)
                case "updateIssue":
                    issue = payload["issue"]
                    assert isinstance(issue, dict)
                    payload["issue"] = self._detail(issue["id"], comments=True)
                case "updateIssueComment":
                    comment = payload["issueComment"]
                    assert isinstance(comment, dict)
                    comment["issue"] = self._detail(
                        self._commented(comment["id"])[0].content_id, comments=True
                    )
                case _:
                    item = payload["projectV2Item"]
                    assert isinstance(item, dict)
                    content = self._row(item["id"]).content_id
                    item["content"] = self._detail(content, comments=True)
            return {"data": {root: payload}}

        return answered

    def detail_response(self, node_id: object, after: object = None) -> dict[str, object]:
        """One issue and a page of its comments, which `task show` reads in one request."""
        return {"data": {"node": self._detail(node_id, comments=True, after=after)}}

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
        moment = (
            datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            if self.real_time
            else f"2026-08-26T00:{self.commented // 60:02d}:{self.commented % 60:02d}Z"
        )
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

    def _commented(self, comment_id: object) -> tuple[_Issue, dict[str, object]]:
        """The issue holding the comment ``comment_id`` names, and that comment."""
        for issue in self.issues:
            for comment in issue.comments:
                if comment["id"] == comment_id:
                    return issue, comment
        raise ValueError(f"the board holds no comment {comment_id!r}")

    def comment_issue_response(self, comment_id: object) -> dict[str, object]:
        """Which issue one comment is on, or the null GitHub answers an unheld node with."""
        try:
            issue, comment = self._commented(comment_id)
        except ValueError:
            return {"data": {"node": None}}
        node = {"__typename": "IssueComment", "id": comment["id"], "body": comment["body"]}
        return {"data": {"node": node | {"issue": {"id": issue.content_id}}}}

    def update_comment(self, variables: dict[str, object]) -> dict[str, object]:
        """Replace one comment's body, as GitHub's `updateIssueComment` does, moving its time."""
        payload = variables.get("input")
        if not isinstance(payload, dict) or not isinstance(payload.get("body"), str):
            raise ValueError("updateIssueComment requires an input object with a body")
        _issue, comment = self._commented(payload.get("id"))
        self.commented += 1
        comment["body"] = self.stored_comment(str(comment["body"]), payload["body"])
        comment["updatedAt"] = (
            datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            if self.real_time
            else f"2026-08-26T00:{self.commented // 60:02d}:{self.commented % 60:02d}Z"
        )
        return {"data": {"updateIssueComment": {"issueComment": dict(comment)}}}

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
    #: The document's `data`, as the fixture sent it; `None` until it answers, or when it
    #: answered with no data.
    data: object = None
    #: When the fixture answered, on the monotonic clock requests are stamped with; `None`
    #: until it answers.
    at: float | None = None


#: One GraphQL token: a quoted string, a spread, a name or variable, or one punctuator.
_GRAPHQL_TOKEN = re.compile(
    r'"(?:[^"\\]|\\.)*"|\.\.\.|\$?[_A-Za-z][_0-9A-Za-z]*|-?\d[\d.]*|[{}():@!=\[\],]'
)


class _Directive(StrEnum):
    """The two directives that decide whether a selection is answered at all."""

    INCLUDE = "include"
    SKIP = "skip"


@dataclass(frozen=True)
class _Condition:
    """An `@include` or `@skip` on a selection, and the `if` argument it reads."""

    directive: _Directive
    #: A variable, spelled `$<name>`, or a literal `true` or `false`.
    argument: str

    def holds(self, variables: Mapping[str, object]) -> bool:
        """Whether the selection it sits on is selected under ``variables``."""
        if self.argument.startswith("$"):
            value = bool(variables.get(self.argument[1:]))
        else:
            value = self.argument == "true"
        return value if self.directive is _Directive.INCLUDE else not value


@dataclass(frozen=True)
class _Field:
    """A field, answered under ``key`` — its alias or its name — with its own selection set."""

    key: str
    condition: _Condition | None
    children: tuple[_Selection, ...]


@dataclass(frozen=True)
class _Spread:
    """A named fragment spread, `...Name`."""

    fragment: str
    condition: _Condition | None


@dataclass(frozen=True)
class _Inline:
    """An inline fragment, applying on the type ``on`` names or, naming none, on any."""

    on: str | None
    condition: _Condition | None
    children: tuple[_Selection, ...]


#: One selection of a GraphQL selection set, as :func:`unanswered_selections` walks it.
type _Selection = _Field | _Spread | _Inline


class _SelectionParser:
    """Reads a GraphQL document's operation and its fragments into selection trees.

    Arguments are read past rather than kept, because what a selection answers under does not
    depend on them; a directive is kept only as `@include` or `@skip`, the two that decide
    whether a selection is answered at all.
    """

    def __init__(self, document: str) -> None:
        self.tokens = _GRAPHQL_TOKEN.findall(document)
        self.at = 0

    def peek(self) -> str | None:
        return self.tokens[self.at] if self.at < len(self.tokens) else None

    def take(self, expected: str | None = None) -> str:
        token = self.peek()
        if token is None or (expected is not None and token != expected):
            raise ValueError(f"GraphQL document: expected {expected!r}, read {token!r}")
        self.at += 1
        return token

    def past_balanced(self, opening: str, closing: str) -> list[str]:
        """Read past one balanced group opening at ``opening``, answering its inner tokens."""
        self.take(opening)
        inner: list[str] = []
        depth = 1
        while depth:
            token = self.take()
            depth += {opening: 1, closing: -1}.get(token, 0)
            if depth:
                inner.append(token)
        return inner

    def document(self) -> tuple[tuple[_Selection, ...], dict[str, tuple[_Selection, ...]]]:
        """The operation's selection set and each fragment's, by fragment name."""
        operation: tuple[_Selection, ...] | None = None
        fragments: dict[str, tuple[_Selection, ...]] = {}
        while (token := self.peek()) is not None:
            match token:
                case "fragment":
                    self.take()
                    name = self.take()
                    self.take("on")
                    self.take()
                    fragments[name] = self.selections()
                case "query" | "mutation" | "subscription":
                    self.take()
                    while self.peek() not in ("{", "(", None):
                        self.take()
                    if self.peek() == "(":
                        self.past_balanced("(", ")")
                    operation = self.selections()
                case _:
                    operation = self.selections()
        if operation is None:
            raise ValueError("GraphQL document carries no operation")
        return operation, fragments

    def condition(self) -> _Condition | None:
        """Read past the directives here, answering the `@include` or `@skip` among them."""
        decided: _Condition | None = None
        while self.peek() == "@":
            self.take()
            name = self.take()
            arguments = self.past_balanced("(", ")") if self.peek() == "(" else []
            match name, arguments:
                case (("include" | "skip"), ["if", ":", argument]):
                    decided = _Condition(_Directive(name), argument)
        return decided

    def selections(self) -> tuple[_Selection, ...]:
        self.take("{")
        selected: list[_Selection] = []
        while (token := self.take()) != "}":
            match token, self.peek():
                case "...", "on":
                    self.take()
                    on = self.take()
                    condition = self.condition()
                    selected.append(_Inline(on, condition, self.selections()))
                case "...", "{" | "@":
                    condition = self.condition()
                    selected.append(_Inline(None, condition, self.selections()))
                case "...", _:
                    selected.append(_Spread(self.take(), self.condition()))
                case _:
                    key = token
                    if self.peek() == ":":
                        self.take()
                        self.take()
                    if self.peek() == "(":
                        self.past_balanced("(", ")")
                    condition = self.condition()
                    children = self.selections() if self.peek() == "{" else ()
                    selected.append(_Field(key, condition, children))
        return tuple(selected)


def _keys(selection: _Inline) -> set[str]:
    """The response keys ``selection``'s own selection set names directly."""
    return {child.key for child in selection.children if isinstance(child, _Field)}


def unanswered_selections(query: str, variables: Mapping[str, object], data: object) -> list[str]:
    """Each field ``query`` selects that ``data`` does not answer, as a dotted path.

    Read off the document the store sent rather than off a list kept here, so a release whose
    query selects one field more is a path named here the moment a journey meets it. A field
    answered `null` is answered; a list is walked element by element; an inline fragment
    applies where the object answers its `__typename` as that type, or, naming none, answers a
    field that fragment alone among its siblings selects — a union's member, such as one board
    field value, answers only the fragment its type matches; and a selection its `@include` or
    `@skip` leaves out is not owed.
    """
    operation, fragments = _SelectionParser(query).document()
    missing: list[str] = []

    def applies(
        inline: _Inline, siblings: tuple[_Selection, ...], answered: dict[str, object]
    ) -> bool:
        typename = answered.get("__typename")
        if inline.on is None or typename == inline.on:
            return True
        others = {
            key
            for sibling in siblings
            if isinstance(sibling, _Inline) and sibling is not inline and sibling.on is not None
            for key in _keys(sibling)
        }
        return typename is None and bool((_keys(inline) - others) & answered.keys())

    def walk(selections: tuple[_Selection, ...], answered: object, path: str) -> None:
        match answered:
            case None:
                return
            case list():
                for index, element in enumerate(answered):
                    walk(selections, element, f"{path}[{index}]")
                return
            case dict():
                pass
            case _:
                missing.append(f"{path} (answered {answered!r}, not an object)")
                return
        for selection in selections:
            if selection.condition is not None and not selection.condition.holds(variables):
                continue
            match selection:
                case _Spread(fragment=fragment):
                    walk(fragments[fragment], answered, path)
                case _Inline(children=children) if applies(selection, selections, answered):
                    walk(children, answered, path)
                case _Field(key=key) if key not in answered:
                    missing.append(f"{path}.{key}".lstrip("."))
                case _Field(key=key, children=children) if children:
                    walk(children, answered[key], f"{path}.{key}")

    walk(operation, data, "")
    return missing


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
        # A whole twin carries the comment page its answer selects, whose marker is tried
        # before any mutation's, so it is told by its own mutation's marker first.
        whole = self.is_mutation and WHOLE_ISSUE_SELECTION in self.query
        for marker, operation in _OPERATIONS.items():
            if whole and operation in _WHOLE_TWINS and marker in self.query:
                return _WHOLE_TWINS[operation]
        for marker, operation in _OPERATIONS.items():
            if marker in self.query:
                return operation
        raise ValueError(f"no fixture operation answers {self.query!r}")

    @property
    def repository(self) -> _Repository:
        """The repository this request looks up: alone, or beside the board's fields."""
        if self.operation in (
            _Operation.CREATION_CONTEXT,
            _Operation.ORIGIN_LOOKUPS,
            _Operation.ORIGIN_LOOKUPS_WITH_MEMBERS,
        ):
            return _Repository(
                owner=self.string("repositoryOwner"), name=self.string("repositoryName")
            )
        return _Repository(owner=self.string("owner"), name=self.string("name"))

    @property
    def looks_up_repository(self) -> bool:
        """Whether this request resolves a repository's node id, which a create needs."""
        return self.operation in (
            _Operation.REPOSITORY,
            _Operation.CREATION_CONTEXT,
            _Operation.ORIGIN_LOOKUPS,
            _Operation.ORIGIN_LOOKUPS_WITH_MEMBERS,
        )

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
    PROJECT_VISIBILITY = "projectVisibility"
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
    COMMENT_ISSUE = "commentIssue"
    UPDATE_COMMENT = "updateComment"
    UPDATE_COMMENT_WHOLE = "updateCommentWhole"
    ADD_SUB_ISSUE = "addSubIssue"
    ADD_BLOCKED_BY = "addBlockedBy"
    UPDATE_ISSUE = "updateIssue"
    DELETE_ISSUE = "deleteIssue"
    ISSUE_COPY_DETAILS = "issueCopyDetails"
    ADD_COMMENT_WHOLE = "addCommentWhole"
    UPDATE_ISSUE_WHOLE = "updateIssueWhole"
    UPDATE_FIELD_WHOLE = "updateFieldWhole"
    CLEAR_FIELD_WHOLE = "clearFieldWhole"
    ORIGIN_LOOKUPS = "originLookups"
    ORIGIN_LOOKUPS_WITH_MEMBERS = "originLookupsWithMembers"
    DOCUMENT_IN_PROJECT = "documentInProject"
    CREATED_BOARD_ITEMS = "createdBoardItems"
    CREATE_ISSUES = "createIssues"
    WRITE_FIELD_VALUES = "writeFieldValues"
    CLOSE_ISSUES = "closeIssues"
    UPDATE_ISSUES = "updateIssues"
    ADD_BLOCKED_BYS = "addBlockedBys"
    WRITE_FIELDS_AND_EDGES = "writeFieldsAndEdges"
    DELETE_ISSUES = "deleteIssues"
    CLOSED_ITEMS = "closedItems"
    ISSUES_BY_REFERENCE = "issuesByReference"


#: Each operation's marker in the document the source sends, in the order they are
#: tried. The source ships its queries as constants, so the root field is a stable
#: substring of each one and is what tells a board read from the writes that follow it.
_OPERATIONS: dict[str, _Operation] = {
    # A copy's aliased documents, each before every marker its slots also carry: the batched
    # origin lookup carries the board-fields read, the repository read and a search; the
    # created-item read an aliased node read; and each write batch its single mutation. The
    # screened document copy's lookup carries the plain lookup's slots beside its project's
    # members, so it is told by those first.
    "members:node(id:$project)": _Operation.ORIGIN_LOOKUPS_WITH_MEMBERS,
    "@include(if:$ask0)": _Operation.ORIGIN_LOOKUPS,
    # Before the comment listing's and the issue update's markers, which a comment edit's
    # whole twin and its plain document carry inside its own: the edit of one comment, and
    # the read of which issue a comment is on that the store sends before an edit it makes
    # without its cache.
    "updateIssueComment(": _Operation.UPDATE_COMMENT,
    "... on IssueComment{": _Operation.COMMENT_ISSUE,
    "@include(if:$read0)": _Operation.CREATED_BOARD_ITEMS,
    "create0:createIssue(": _Operation.CREATE_ISSUES,
    # Before both single-kind batches, whose first slots this request of both kinds carries.
    "$sendField0": _Operation.WRITE_FIELDS_AND_EDGES,
    "field0:updateProjectV2ItemFieldValue(": _Operation.WRITE_FIELD_VALUES,
    "close0:updateIssue(": _Operation.CLOSE_ISSUES,
    "update0:updateIssue(": _Operation.UPDATE_ISSUES,
    "block0:addBlockedBy(": _Operation.ADD_BLOCKED_BYS,
    "delete0:deleteIssue(": _Operation.DELETE_ISSUES,
    # Before the sub-issue listing's marker and the item read's, both of which it carries: a
    # document read by its id with the project it is filed under and that project's members.
    "project:parent{": _Operation.DOCUMENT_IN_PROJECT,
    # Before the sub-issue listing's marker, which this batch's `children` read of its
    # project also carries, and the batched detail read's own, which its aliases carry: a
    # copy's read of the tasks it carries, each with its comments unconditionally and its
    # `blockedBy`.
    "i0:node(id:$id0){__typename ...BoardIssue ... on Issue{comments(first:$first){nodes{": (
        _Operation.ISSUE_COPY_DETAILS
    ),
    # Before every other marker: the store's read of the issues a landing closes, each by its
    # repository and number, which carries `repository(owner:` and `projectItems(` beside its
    # own fragment.
    "fragment HeldIssue on Issue{": _Operation.ISSUES_BY_REFERENCE,
    "closedItems:repositoryOwner": _Operation.CLOSED_ITEMS,
    # Before the board read's own marker and the search's, both of which this document
    # also contains: it narrows the board's items by a field filter and searches beside it.
    "originItems:repositoryOwner": _Operation.ORIGIN_LOOKUP,
    # Before the board-fields read's own marker, which this document carries beside the
    # repository it reads in the same request.
    "repository(owner:$repositoryOwner": _Operation.CREATION_CONTEXT,
    # Before the board read's own marker, which this aliased root also contains.
    "boardFields:repositoryOwner": _Operation.BOARD_FIELDS,
    # Before the board read's own marker, which this aliased root also contains: whether the
    # Project is public, read once per copy before a write to a source declared private.
    "visibility:repositoryOwner(": _Operation.PROJECT_VISIBILITY,
    "optionId field{": _Operation.FIELD_SNAPSHOT,
    "createProjectV2Field(": _Operation.CREATE_FIELD,
    # Before the board read's own marker, which this root also carries: the adopted store's
    # read of what one item depends on asks for the board's id beside it, to place a draft.
    "board:repositoryOwner(": _Operation.DEPENDENCIES,
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


#: What the adopted store selects under a mutation that answers with the whole issue it left
#: behind — its `whole_issue!` — and the twin each plain mutation is sent as while a write the
#: store's cache expects runs (its `graphql::whole_twin`). A twin applies exactly what its
#: plain document does; only its answer is wider.
WHOLE_ISSUE_SELECTION = "__typename ...BoardIssue ... on Issue{comments(first:$first){nodes{"
_WHOLE_TWINS: dict[_Operation, _Operation] = {
    _Operation.ADD_COMMENT: _Operation.ADD_COMMENT_WHOLE,
    _Operation.UPDATE_COMMENT: _Operation.UPDATE_COMMENT_WHOLE,
    _Operation.UPDATE_ISSUE: _Operation.UPDATE_ISSUE_WHOLE,
    _Operation.UPDATE_FIELD: _Operation.UPDATE_FIELD_WHOLE,
    _Operation.CLEAR_FIELD: _Operation.CLEAR_FIELD_WHOLE,
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


@dataclass(frozen=True)
class RestRequest:
    """One REST request the loopback took, as its method, path, credential and JSON body.

    Kept apart from the GraphQL log because nothing the plan store sends goes here: this is
    the request a caller holding the board credential sends GitHub directly, such as `gh api
    -X PATCH repos/<owner>/<name>/issues/<number> -f state=closed`, recorded so a journey can
    show the write reached the wire rather than the fixture's own state.
    """

    method: str
    path: str
    authorization: str | None
    body: dict[str, object]
    #: When the loopback took it, on the monotonic clock the GraphQL log stamps, so a journey
    #: can order a REST read against the GraphQL writes around it.
    received: float = field(default_factory=time.monotonic, compare=False)
    #: When the loopback answered a read, as the moment its answer was composed; `None` for a
    #: request logged before its answer.
    answered_at: float | None = field(default=None, compare=False)


class _BatchPart(NamedTuple):
    """One kind of write a copy's aliased batch carries, and how its slots are spelled.

    Slot `<n>` carries its input as `$<prefix><n>`, is sent only while `$<send><n>` holds, and
    is the single write `write` — the root field `root` — answered under `<prefix><n>`.
    """

    prefix: str
    send: str
    root: str
    write: _Operation
    slots: int

    def spelled(self, n: int) -> str:
        """How slot ``n`` of this part reads in the document the store sends."""
        return f"{self.prefix}{n}:{self.root}(input:${self.prefix}{n}) @include(if:${self.send}{n})"

    def sent(self, variables: Mapping[str, object]) -> list[int]:
        """The slots of this part ``variables`` send, in slot order."""
        return [n for n in range(self.slots) if variables.get(f"{self.send}{n}") is True]


_FIELD_PART = _BatchPart(
    "field", "send", "updateProjectV2ItemFieldValue", _Operation.UPDATE_FIELD, COPY_WIDE_SLOTS
)
_BLOCK_PART = _BatchPart(
    "block", "send", "addBlockedBy", _Operation.ADD_BLOCKED_BY, COPY_WIDE_SLOTS
)

#: Each aliased write batch a copy sends, by the operation the batch arrives as: the kinds of
#: write it carries, in the order GitHub runs them — a request of field values and edges runs
#: every field value before the first edge.
COPY_BATCHES: dict[_Operation, tuple[_BatchPart, ...]] = {
    _Operation.CREATE_ISSUES: (
        _BatchPart("create", "send", "createIssue", _Operation.CREATE_ISSUE, COPY_WRITE_SLOTS),
    ),
    _Operation.WRITE_FIELD_VALUES: (_FIELD_PART,),
    _Operation.CLOSE_ISSUES: (
        _BatchPart("close", "send", "updateIssue", _Operation.UPDATE_ISSUE, COPY_WRITE_SLOTS),
    ),
    # A re-copy's rewrites of the issues it already holds, each the input its own single
    # update would have sent.
    _Operation.UPDATE_ISSUES: (
        _BatchPart("update", "send", "updateIssue", _Operation.UPDATE_ISSUE, COPY_WRITE_SLOTS),
    ),
    _Operation.ADD_BLOCKED_BYS: (_BLOCK_PART,),
    _Operation.WRITE_FIELDS_AND_EDGES: (
        _FIELD_PART._replace(send="sendField"),
        _BLOCK_PART._replace(send="sendBlock"),
    ),
    _Operation.DELETE_ISSUES: (
        _BatchPart("delete", "send", "deleteIssue", _Operation.DELETE_ISSUE, COPY_DELETE_SLOTS),
    ),
}


class _CopyRead(NamedTuple):
    """One of a copy's batched reads: how slot `<n>` is spelled, and how many slots it sends."""

    alias: str
    slots: int


COPY_READS: dict[_Operation, _CopyRead] = {
    _Operation.ORIGIN_LOOKUPS: _CopyRead(
        "o{n}:repositoryOwner(login:$owner) @include(if:$ask{n})", COPY_ORIGIN_SLOTS
    ),
    _Operation.ORIGIN_LOOKUPS_WITH_MEMBERS: _CopyRead(
        "o{n}:repositoryOwner(login:$owner) @include(if:$ask{n})", COPY_ORIGIN_SLOTS
    ),
    _Operation.CREATED_BOARD_ITEMS: _CopyRead(
        "i{n}:node(id:$id{n}) @include(if:$read{n})", COPY_READ_SLOTS
    ),
}


def copy_read_drift(operation: _Operation, query: str) -> list[str]:
    """How a batched copy read the store sent differs from the slots modelled above.

    Read off the document on every such read, as `copy_batch_drift` reads a batched write,
    so a release that resizes a batch fails the journey that met it rather than having the
    slots past the modelled count go unanswered.
    """
    read = COPY_READS[operation]
    alias, count = read.alias, read.slots
    missing = [alias.format(n=n) for n in range(count) if alias.format(n=n) not in query]
    drift = [f"the batched copy read lacks `{slot}`" for slot in missing]
    if alias.format(n=count) in query:
        drift.append(f"the batched copy read carries more than {count} slots")
    return drift


def copy_batch_drift(operation: _Operation, query: str) -> list[str]:
    """How a batched copy write the store sent differs from the slots modelled above.

    Read off the document on every such write, as `batched_slot_drift` reads the field
    document, so a release that resizes or renames a batch fails the journey that met it.
    """
    parts = COPY_BATCHES[operation]
    spelled = [part.spelled(n) for part in parts for n in range(part.slots)]
    drift = [f"the batched copy document lacks `{slot}`" for slot in spelled if slot not in query]
    sent = query.count("@include(")
    if sent != len(spelled):
        drift.append(f"the batched copy document carries {sent} slots, not {len(spelled)}")
    return drift


class _GitHubFixture(BaseHTTPRequestHandler):
    requests: ClassVar[list[_GraphQLRequest]]
    #: Every REST write the loopback took, in order, beside the GraphQL log above.
    rest_requests: ClassVar[list[RestRequest]] = []
    #: What every request is refused with, or `None` to answer the board normally.
    refusal: ClassVar[_Refusal | None] = None
    #: One operation answered with a GraphQL error while every other is served, or `None`:
    #: a write the store makes that GitHub refuses between two it accepted.
    refused_operation: ClassVar[_Operation | None] = None
    #: How many of `refused_operation` are served before the one refused, so a refusal can
    #: land on a later write of the same kind — the record's, after the body's.
    refused_after: ClassVar[int] = 0
    #: What a person does on the board the moment the next request of an operation arrives,
    #: before it is answered — an edit landing between a caller's read and its write. Each
    #: runs once, for the first such request.
    before_operation: ClassVar[dict[_Operation, Callable[[], None]]] = {}
    #: One operation whose response is lost: the connection closes with no answer, after the
    #: write was applied (`"after"`) or before it reached the board (`"before"`), or `None`.
    dropped_operation: ClassVar[tuple[_Operation, Literal["before", "after"]] | None] = None
    #: The board this handler answers for. A class attribute rather than an argument
    #: because the stdlib constructs a handler per request; a second board is served by
    #: a subclass of this one carrying its own board and its own request log, which is
    #: what lets one journey serve `plans` and `followups` at once without either
    #: board's items showing up on the other.
    board: ClassVar[_Board]

    #: Where GitHub takes a user attachment and serves it back, as the source reaches it on
    #: a loopback endpoint: uploaded beside the GraphQL endpoint, then read at the URL the
    #: upload answered with, after the numeric id of the issue's repository is read by REST.
    ATTACHMENTS = "/user-attachments/assets"
    REPOSITORIES = re.compile(r"^/repos/(?P<owner>[^/]+)/(?P<name>[^/?]+)$")
    #: GitHub's REST issue endpoint, the one `PATCH` updating an issue's state is sent to.
    ISSUE = re.compile(r"^/repos/(?P<owner>[^/]+)/(?P<name>[^/]+)/issues/(?P<number>[0-9]+)$")

    def do_PATCH(self) -> None:  # noqa: N802 - stdlib callback name
        """Update one issue as GitHub's REST endpoint does: its state and why it closed.

        Only the issue changes — its board row's Status is a project field, which no issue
        endpoint writes — so a close sent here leaves the card at the option it held.
        """
        named = self.ISSUE.match(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(payload, dict):
            self._send(422, {"message": "the body must be a JSON object"})
            return
        self.rest_requests.append(
            RestRequest("PATCH", self.path, self.headers.get("Authorization"), payload)
        )
        if named is None:
            self._send(404, {"message": f"no endpoint at {self.path}"})
            return
        if self.headers.get("Authorization") is None:
            self._send(401, {"message": "Requires authentication"})
            return
        repository = _Repository(owner=named["owner"], name=named["name"])
        held = [
            issue
            for issue in self.board.issues
            if issue.repository == repository and issue.number == int(named["number"])
        ]
        if not held:
            self._send(404, {"message": "Not Found"})
            return
        (issue,) = held
        if isinstance(state := payload.get("state"), str):
            issue.state = state.upper()
            reason = payload.get("state_reason")
            issue.state_reason = (
                str(reason).upper() if issue.state == "CLOSED" and reason is not None else None
            )
        self._send(
            200,
            {
                "number": issue.number,
                "state": issue.state.lower(),
                "state_reason": issue.state_reason.lower() if issue.state_reason else None,
            },
        )

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        named = self.REPOSITORIES.match(self.path)
        if named is not None:
            received = time.monotonic()
            answer = self._repository_rest(_Repository(owner=named["owner"], name=named["name"]))
            self.rest_requests.append(
                RestRequest(
                    "GET",
                    self.path,
                    self.headers.get("Authorization"),
                    {},
                    received=received,
                    answered_at=time.monotonic(),
                )
            )
            self._send(*answer)
            return
        held = self.board.attachments.get(self.path)
        if held is None:
            self._send(404, {"message": f"no attachment at {self.path}"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(held)))
        self.end_headers()
        self.wfile.write(held)

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
        length = int(self.headers["Content-Length"])
        if self.path.startswith(f"{self.ATTACHMENTS}?"):
            served = f"{self.ATTACHMENTS}/{len(self.board.attachments) + 1}"
            self.board.attachments[served] = self.rfile.read(length)
            address = self.server.server_address
            if not isinstance(address, tuple):
                raise ValueError("the attachment server requires a TCP address")
            host, port = address[:2]
            self._send(201, {"url": f"http://{host!s}:{port}{served}"})
            return
        request = _GraphQLRequest.from_json(self.rfile.read(length))
        self.requests.append(request)
        if self.refusal is not None:
            self._send(self.refusal.status, self.refusal.body)
            return
        try:
            operation: _Operation | None = request.operation
        except ValueError:
            operation = None
        edit = _GitHubFixture.before_operation.pop(operation, None) if operation else None
        if edit is not None:
            edit()
        dropped = _GitHubFixture.dropped_operation
        if dropped is not None and dropped[0] is operation:
            _GitHubFixture.dropped_operation = None
            if dropped[1] == "after":
                self._answer(request)
            # The connection closes without an answer: after the write was applied for `after`,
            # before it reached the board for `before`.
            self.close_connection = True
            return
        answer = self._answer(request)
        request.answered.data = answer.get("data")
        request.answered.at = time.monotonic()
        self._send(200, answer)

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
        # A write the store sends in an aliased batch is refused with the batch carrying it,
        # so a refusal named for a single write still lands on a copy that batches it.
        carried = {part.write for part in COPY_BATCHES.get(operation, ())}
        # And a write sent as its whole twin, which also answers its issue, is the same write.
        plain = {whole: write for write, whole in _WHOLE_TWINS.items()}.get(operation)
        if self.refused_operation is not None and self.refused_operation in {
            operation,
            *carried,
            plain,
        }:
            if _GitHubFixture.refused_after == 0:
                return {"errors": [{"message": f"{operation} refused by the fixture"}]}
            _GitHubFixture.refused_after -= 1
        match operation:
            case operation if operation in COPY_BATCHES:
                drift = copy_batch_drift(operation, request.query)
            case operation if operation in COPY_READS:
                drift = copy_read_drift(operation, request.query)
            case _:
                drift = []
        if drift:
            return {"errors": [{"message": "; ".join(drift)}]}
        match operation:
            case _Operation.BOARD:
                return self.board.board_response()
            case _Operation.CLOSED_ITEMS:
                return self.board.closed_items_response(request.variables)
            case _Operation.BOARD_FIELDS:
                return self.board.board_fields_response()
            case _Operation.PROJECT_VISIBILITY:
                return self.board.project_visibility_response(request.variables)
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
            case _Operation.ISSUES_BY_REFERENCE:
                return self.board.issues_by_reference_response(request.variables)
            case _Operation.ISSUE_DETAIL:
                return self.board.detail_response(
                    request.variables.get("id"), request.variables.get("after")
                )
            case _Operation.ISSUE_DETAILS:
                return self.board.details_response(request.variables)
            case _Operation.SUB_ISSUES:
                return self.board.sub_issues_response(request.variables.get("id"))
            case _Operation.COMMENTS:
                return self.board.comments_response(
                    request.variables.get("id"), request.variables.get("after")
                )
            case _Operation.ADD_COMMENT:
                return self.board.add_comment(request.variables)
            case _Operation.COMMENT_ISSUE:
                return self.board.comment_issue_response(request.variables.get("id"))
            case _Operation.UPDATE_COMMENT:
                return self.board.update_comment(request.variables)
            case _Operation.UPDATE_COMMENT_WHOLE:
                return self.board.whole_answer("updateIssueComment", self.board.update_comment)(
                    request.variables
                )
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
                if drift := batched_slot_drift(request.query):
                    return {"errors": [{"message": "; ".join(drift)}]}
                return self.board.set_fields(request.variables)
            case _Operation.ADD_SUB_ISSUE:
                return self.board.add_sub_issue(request.variables)
            case _Operation.ADD_BLOCKED_BY:
                return self.board.add_blocked_by(request.variables)
            case _Operation.DELETE_ISSUE:
                return self.board.delete_issue(request.variables)
            case _Operation.ISSUE_COPY_DETAILS:
                return self.board.copy_details_response(request.variables)
            case _Operation.ADD_COMMENT_WHOLE:
                return self.board.whole_answer("addComment", self.board.add_comment)(
                    request.variables
                )
            case _Operation.UPDATE_ISSUE_WHOLE:
                return self.board.whole_answer("updateIssue", self.board.update_issue)(
                    request.variables
                )
            case _Operation.UPDATE_FIELD_WHOLE:
                return self.board.whole_answer(
                    "updateProjectV2ItemFieldValue", self.board.set_field
                )(request.variables)
            case _Operation.CLEAR_FIELD_WHOLE:
                return self.board.whole_answer(
                    "clearProjectV2ItemFieldValue", self.board.clear_field
                )(request.variables)
            case _Operation.ORIGIN_LOOKUPS | _Operation.ORIGIN_LOOKUPS_WITH_MEMBERS:
                return self._origin_lookups(request)
            case _Operation.DOCUMENT_IN_PROJECT:
                return self.board.document_in_project_response(request.variables)
            case _Operation.CREATED_BOARD_ITEMS:
                return self.board.created_items_response(request.variables)
            case operation if operation in COPY_BATCHES:
                writes = {
                    _Operation.CREATE_ISSUE: self.board.create_issue,
                    _Operation.UPDATE_FIELD: self.board.set_field,
                    _Operation.UPDATE_ISSUE: self.board.update_issue,
                    _Operation.ADD_BLOCKED_BY: self.board.add_blocked_by,
                    _Operation.DELETE_ISSUE: self.board.delete_issue,
                }
                return self.board.write_batch(COPY_BATCHES[operation], writes)(request.variables)
        raise ValueError(f"no fixture answer for {operation}")

    def _origin_lookups(self, request: _GraphQLRequest) -> dict[str, object]:
        """A copy's first read: the board's fields, its repository, and up to ten origins.

        Each asked slot `<n>` is :meth:`_Board.origin_lookup_response` for its own filter and
        search, answered under `o<n>` and `s<n>`; a slot not asked is not answered, as GitHub
        answers an alias its `@include(if:)` leaves out.
        """
        variables = request.variables
        fields = self.board.board_fields_response()["data"]
        assert isinstance(fields, dict)
        named = _Repository(
            owner=request.string("repositoryOwner"), name=request.string("repositoryName")
        )
        known = REPOSITORY_NODE_IDS.get(named)
        answer: dict[str, object] = {
            **fields,
            "repository": None
            if known is None
            else {
                "id": known,
                "databaseId": list(REPOSITORY_NODE_IDS).index(named) + 1,
                "nameWithOwner": str(named),
            },
        }
        slots = sorted(int(name[3:]) for name in variables if re.fullmatch(r"ask\d+", name))
        for n in slots:
            if variables[f"ask{n}"] is not True:
                continue
            one = self.board.origin_lookup_response(
                {"filter": variables[f"filter{n}"], "search": variables[f"search{n}"]},
                variables.get("lookupFirst"),
            )["data"]
            assert isinstance(one, dict)
            answer[f"o{n}"] = one["originItems"]
            answer[f"s{n}"] = one["search"]
        # A screened document copy's lookup also reads its project's members, each trimmed to
        # what classifying it reads.
        if request.operation is _Operation.ORIGIN_LOOKUPS_WITH_MEMBERS:
            project = self.board._issue(variables.get("project"))
            page, more = self.board.members_page(project, variables.get("memberFirst"))
            answer["members"] = {
                "subIssues": {
                    "pageInfo": {
                        "hasNextPage": more,
                        "endCursor": str(len(page)) if more else None,
                    },
                    "nodes": [
                        {
                            "id": member.content_id,
                            "body": member.body,
                            "parent": {"id": project.content_id},
                            "repository": {"nameWithOwner": str(member.repository)},
                        }
                        for member in page
                    ],
                }
            }
        return {"data": answer}

    @staticmethod
    def _repository_rest(named: _Repository) -> tuple[int, dict[str, object]]:
        """What GitHub's REST repository endpoint answers about ``named``: its numeric id and
        its `visibility`, with the older `private` flag beside it, or a 404 for a repository
        it does not know or the token cannot see, as GitHub answers one."""
        if named not in REPOSITORY_NODE_IDS:
            return 404, {"message": "Not Found"}
        visibility = repository_visibility(named)
        return 200, {
            "id": list(REPOSITORY_NODE_IDS).index(named) + 1,
            "full_name": str(named),
            "visibility": visibility,
            "private": visibility != "public",
        }

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
    *,
    linear: bool = False,
    placement: tuple[float, ...] = (),
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
    ``linear`` says the caller's user layer declares the example's Linear sources, which
    are then pointed at a port nothing serves (:func:`unserved_linear`). ``placement`` is
    how long after its create each issue a create files on the board appears there
    (:attr:`_Board.placement_delays`); empty, every one is placed at once.
    """
    _GitHubFixture.refusal = refusal
    try:
        with _served(_GitHubFixture, BOARD, options, number) as endpoint:
            BOARD.placement_delays = placement
            yield {
                "GH_PROJECTS_TOKEN": "fixture-token",
                "ONETASKGRAPH_SOURCES__PLANS__CONFIG__ENDPOINT": endpoint,
                "ONETASKGRAPH_SOURCES__FOLLOWUPS__CONFIG__ENDPOINT": endpoint,
                # A copy onto `plans` or `followups` builds every source their routes name,
                # which a Linear source refuses to do without its key; a journey whose user
                # layer routes to the example's Linear sources says so, and a task routed
                # there fails on the refused connection instead of reaching a workspace.
                **unserved_linear(linear),
            }
    finally:
        _GitHubFixture.refusal = None
        _GitHubFixture.refused_operation = None
        _GitHubFixture.refused_after = 0
        _GitHubFixture.before_operation = {}
        _GitHubFixture.dropped_operation = None


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
    handler.rest_requests = []
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


class _FollowUpsFixture(_GitHubFixture):
    """The second board, served beside the first so one journey can reach both.

    A subclass rather than a second instance because the stdlib builds a handler per
    request and takes the class: its own `board` and its own `requests` are what keep
    `followups`'s items off `plans` and let each board's traffic be read apart from the
    other's. One journey needs that — a launch claims its plan's items on one board and
    the ticket a node delivers on the other — and a single board answering both sources
    would file every item on whichever number it was configured with.
    """

    requests: ClassVar[list[_GraphQLRequest]] = []


#: The board the `followups` source reaches when both are served, kept beside the `plans`
#: one so a journey reads each board's own items rather than one shared list.
FOLLOWUPS_BOARD = _Board()


@contextmanager
def _serving_both_boards() -> Iterator[dict[str, str]]:
    """Serve `plans` and `followups` as two boards, yielding the environment for both.

    Two servers rather than one, because the two sources are configured with different
    project numbers and an issue's board membership is answered under the number of the
    board holding it: one server would file every item under one number, and the source
    configured with the other would find none of its own.
    """
    with (
        _served(_GitHubFixture, BOARD, STATUS_OPTIONS, CONFIGURED_PROJECT_NUMBER) as plans,
        _served(
            _FollowUpsFixture, FOLLOWUPS_BOARD, FOLLOWUPS_OPTIONS, FOLLOWUPS_PROJECT_NUMBER
        ) as followups,
    ):
        yield {
            "GH_PROJECTS_TOKEN": "fixture-token",
            "ONETASKGRAPH_SOURCES__PLANS__CONFIG__ENDPOINT": plans,
            "ONETASKGRAPH_SOURCES__FOLLOWUPS__CONFIG__ENDPOINT": followups,
            "ONETASKGRAPH_SOURCES__PLANS__CONFIG__PACING__MIN_MUTATION_INTERVAL_MS": "0",
            "ONETASKGRAPH_SOURCES__FOLLOWUPS__CONFIG__PACING__MIN_MUTATION_INTERVAL_MS": "0",
        }


# llmlint: ignore-end[e2e_not_mocked]


class _PrivateBoard(_Board):
    """A host's private Projects board: its Project not public, and holding nothing until a
    copy files something on it, since a private board shares no item with `plans`."""

    node_id: ClassVar[_BoardNodeId] = _BoardNodeId("PVT_private_fixture")
    title: ClassVar[str] = "Private plans"
    created_prefix: ClassVar[str] = "private"

    def reset(self) -> None:
        super().reset()
        self.issues = []
        self.public = False


class _PrivateBoardFixture(_GitHubFixture):
    """The private board, served beside `plans` with its own board and its own request logs,
    for the reason :class:`_FollowUpsFixture` is: each board's traffic is read apart."""

    requests: ClassVar[list[_GraphQLRequest]] = []
    rest_requests: ClassVar[list[RestRequest]] = []


#: The board a host's private GitHub Projects source reaches when it is served.
PRIVATE_BOARD = _PrivateBoard()


@contextmanager
def _serving_private_board(number: int) -> Iterator[str]:
    """Serve :data:`PRIVATE_BOARD` under the project ``number`` its source is configured
    with, yielding the endpoint that source is pointed at."""
    with _served(_PrivateBoardFixture, PRIVATE_BOARD, STATUS_OPTIONS, number) as endpoint:
        yield endpoint


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


def _slots(request: _GraphQLRequest, part: _BatchPart) -> list[dict[str, object]]:
    """The input of each slot of ``part`` the aliased batch ``request`` sent, in slot order."""
    inputs = [request.variables.get(f"{part.prefix}{n}") for n in part.sent(request.variables)]
    return [payload for payload in inputs if isinstance(payload, dict)]


def _write_inputs(
    operation: _Operation, requests: list[_GraphQLRequest] | None = None
) -> list[dict[str, object]]:
    """The input of every ``operation`` write among ``requests``, alone or a slot of a batch.

    A copy sends its creates, field writes, closes and blocked-by edges as aliased batches,
    and a single write of the same kind elsewhere, so a reader asking what was written —
    or that nothing was — counts both, or a batch would read as no write at all.
    """
    written: list[dict[str, object]] = []
    for request in _GitHubFixture.requests if requests is None else requests:
        if request.operation is operation:
            payload = request.variables.get("input")
            if isinstance(payload, dict):
                written.append(payload)
        for part in COPY_BATCHES.get(request.operation, ()):
            if part.write is operation:
                written.extend(_slots(request, part))
    return written


def _field_writes(requests: list[_GraphQLRequest]) -> list[dict[str, object]]:
    """The input of every field-value write among ``requests``, batched or standalone.

    The source writes one field with `updateProjectV2ItemFieldValue` alone, and several of
    a copy's together as aliases of one request, each included by its `write…` flag; either
    way, each write GitHub would apply is one input here, in the order the request lists it.
    """
    written: list[dict[str, object]] = []
    for request in requests:
        for part in COPY_BATCHES.get(request.operation, ()):
            if part.write is _Operation.UPDATE_FIELD:
                written.extend(_slots(request, part))
        carried = {
            _Operation.UPDATE_FIELD: (SINGLE_WRITE,),
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
        carried = {
            _Operation.CLEAR_FIELD: (_BatchedWrite("clearProjectV2ItemFieldValue", "input", None),),
            _Operation.UPDATE_FIELDS: BATCHED_CLEARS,
        }.get(request.operation, ())
        for clear in carried:
            payload = request.variables.get(clear.variable)
            if clear.included(request.variables) and isinstance(payload, dict):
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
        for search in _searches(request)
        if _FIELD_QUALIFIER.search(search) is None
    ]
    return walked, unnarrowed


def _searches(request: _GraphQLRequest) -> list[str]:
    """Every board search ``request`` sends: its own, or each asked slot's of a batched lookup."""
    match request.operation:
        case _Operation.SEARCH | _Operation.ORIGIN_LOOKUP:
            return [request.string("search")]
        case _Operation.ORIGIN_LOOKUPS | _Operation.ORIGIN_LOOKUPS_WITH_MEMBERS:
            asked = [name[3:] for name, value in request.variables.items() if name[:3] == "ask"]
            return [
                request.string(f"search{slot}")
                for slot in asked
                if slot.isdigit() and request.variables[f"ask{slot}"] is True
            ]
        case _:
            return []


#: How many items the `plans` board is held to when a copy or an approval is measured at its
#: scale: it held 1,986 when the copy-speed spike timed a copy there, so 2,000 is that board
#: rounded up rather than a size picked to make a cost look small.
PLANS_BOARD_SCALE = 2_000
#: How many tasks each plan another person filed holds, beside its project's own issue.
POPULATED_PLAN_TASKS = 9


def populate(board: _Board, total: int, seed: int = 0) -> None:
    """Fill ``board`` with plans other people filed until it holds ``total`` items.

    Generated from ``seed`` rather than committed, and never copied from a real board: each
    plan is a project issue and :data:`POPULATED_PLAN_TASKS` sub-issues, every task carrying
    the origin a copy of it would stamp, so a read that walks the board, lists its projects,
    or narrows by origin meets what it would meet on a board that has taken years of copies.
    """
    generated = random.Random(seed)
    words = ("listing", "cursor", "board", "copy", "gate", "route", "view", "store", "plan")
    plan = 0
    while len(board.issues) < total:
        parent = _Issue(
            item_id=_BoardItemId(f"PVTI_filed_plan_{plan}"),
            content_id=_IssueNodeId(f"I_filed_plan_{plan}"),
            number=10_000 + len(board.issues),
            title=f"Plan {plan}: {' '.join(generated.choices(words, k=3))}",
            body=f"A plan filed on the board, {generated.randrange(10**6)}.",
            origin=f"authoring:filed-plan-{plan}",
        )
        board.issues.append(parent)
        parent.board = board
        for task in range(min(POPULATED_PLAN_TASKS, total - len(board.issues))):
            child = _Issue(
                item_id=_BoardItemId(f"PVTI_filed_task_{plan}_{task}"),
                content_id=_IssueNodeId(f"I_filed_task_{plan}_{task}"),
                number=10_000 + len(board.issues),
                title=f"feat: {' '.join(generated.choices(words, k=4))}",
                body=f"A task of plan {plan}, {generated.randrange(10**6)}.",
                parent_id=parent.content_id,
                origin=f"authoring:filed-plan-{plan}-task-{task}",
            )
            board.issues.append(child)
            child.board = board
            parent.sub_issues += 1
        plan += 1


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
#: :data:`LARGEST_PAGE` but for a project's member listing, priced at the store's own
#: `MEMBER_PAGE_SIZE` of 23 rows. A search is priced per page from its entry by
#: :func:`_search_points`.
REQUEST_PRICES: dict[_Operation, DocumentPrice] = {
    _Operation.SEARCH: DocumentPrice("SEARCH_ISSUES", 5),
    _Operation.ISSUE: DocumentPrice("ISSUE", 1),
    _Operation.SUB_ISSUES: DocumentPrice("SUB_ISSUES", 1),
    _Operation.BOARD: DocumentPrice("BOARD", 2),
    _Operation.ORIGIN_LOOKUP: DocumentPrice("ORIGIN_LOOKUP", 1),
    _Operation.BOARD_FIELDS: DocumentPrice("BOARD_FIELDS", 1),
    _Operation.PROJECT_VISIBILITY: DocumentPrice("PROJECT_VISIBILITY", 1),
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
    _Operation.COMMENT_ISSUE: DocumentPrice("COMMENT_ISSUE", 1),
    _Operation.UPDATE_COMMENT: DocumentPrice("UPDATE_COMMENT", 1),
    _Operation.UPDATE_COMMENT_WHOLE: DocumentPrice("UPDATE_COMMENT_WHOLE", 1),
    _Operation.ISSUE_COPY_DETAILS: DocumentPrice("ISSUE_COPY_DETAILS", 1),
    _Operation.ADD_COMMENT_WHOLE: DocumentPrice("ADD_COMMENT_WHOLE", 1),
    _Operation.UPDATE_ISSUE_WHOLE: DocumentPrice("UPDATE_ISSUE_WHOLE", 1),
    _Operation.UPDATE_FIELD_WHOLE: DocumentPrice("UPDATE_FIELD_WHOLE", 1),
    _Operation.CLEAR_FIELD_WHOLE: DocumentPrice("CLEAR_FIELD_WHOLE", 1),
    _Operation.CLOSED_ITEMS: DocumentPrice("CLOSED_ITEMS", 2),
    _Operation.ORIGIN_LOOKUPS: DocumentPrice("ORIGIN_LOOKUPS", 1),
    _Operation.ORIGIN_LOOKUPS_WITH_MEMBERS: DocumentPrice("ORIGIN_LOOKUPS_WITH_MEMBERS", 1),
    _Operation.DOCUMENT_IN_PROJECT: DocumentPrice("DOCUMENT_IN_PROJECT", 1),
    _Operation.CREATED_BOARD_ITEMS: DocumentPrice("CREATED_BOARD_ITEMS", 1),
    _Operation.CREATE_ISSUES: DocumentPrice("CREATE_ISSUES", 1),
    _Operation.WRITE_FIELD_VALUES: DocumentPrice("WRITE_FIELD_VALUES", 1),
    _Operation.CLOSE_ISSUES: DocumentPrice("CLOSE_ISSUES", 1),
    _Operation.UPDATE_ISSUES: DocumentPrice("UPDATE_ISSUES", 1),
    _Operation.ADD_BLOCKED_BYS: DocumentPrice("ADD_BLOCKED_BYS", 1),
    _Operation.WRITE_FIELDS_AND_EDGES: DocumentPrice("WRITE_FIELDS_AND_EDGES", 1),
    _Operation.DELETE_ISSUES: DocumentPrice("DELETE_ISSUES", 1),
}


class RequestKind(StrEnum):
    """What kind of GitHub request a document is, as the copy-speed spike timed each one."""

    ITEM_READ = "item read"
    BOARD_PAGE = "board page"
    SEARCH_PAGE = "search page"
    BATCHED_READ = "read batching several items"
    MUTATION = "mutation"
    ALIASED_MUTATIONS = "request of up to 11 aliased mutations"
    ASSET_UPLOAD = "asset upload"
    ASSET_REPOSITORY_READ = "asset upload's repository read"
    ASSET_VERIFICATION = "asset upload's verification"


#: The mean latency of each request kind, in milliseconds, as the copy-speed spike measured it
#: against GitHub (report `board-copy-speed-2026-10-18-spike-copy-timing-report`, its latency
#: table): a board or search page as timed on the `plans` board's 1,986 items, and a read
#: batching several items, which the spike did not time, charged as its measured test-board
#: page of 100 items. A project's member listing is one issue's `subIssues`, a single page of
#: up to 23 members, so it is one item's read and charged as one, which is how the approved
#: `plan-approval-seconds` evidence charges the approval's two listings. A journey that may
#: not spend the real board's token adds these to its real elapsed time for each request the
#: double logged, rather than waiting them out.
REQUEST_LATENCY_MS: dict[RequestKind, int] = {
    RequestKind.ITEM_READ: 554,
    RequestKind.BOARD_PAGE: 6_948,
    RequestKind.SEARCH_PAGE: 6_791,
    RequestKind.BATCHED_READ: 4_551,
    RequestKind.MUTATION: 1_082,
    RequestKind.ALIASED_MUTATIONS: 3_700,
    RequestKind.ASSET_UPLOAD: 927,
    RequestKind.ASSET_REPOSITORY_READ: 526,
    RequestKind.ASSET_VERIFICATION: 1_140,
}

#: How many aliased mutations one :data:`RequestKind.ALIASED_MUTATIONS` charge covers.
ALIASED_MUTATIONS_PER_CHARGE = 11

#: The kind of each document the double answers, for :data:`REQUEST_LATENCY_MS`.
REQUEST_KINDS: dict[_Operation, RequestKind] = {
    _Operation.BOARD: RequestKind.BOARD_PAGE,
    _Operation.FIELD_SNAPSHOT: RequestKind.BOARD_PAGE,
    _Operation.SEARCH: RequestKind.SEARCH_PAGE,
    _Operation.ISSUE_DETAILS: RequestKind.BATCHED_READ,
    _Operation.ISSUE_COPY_DETAILS: RequestKind.BATCHED_READ,
    _Operation.ORIGIN_LOOKUPS: RequestKind.BATCHED_READ,
    _Operation.ORIGIN_LOOKUPS_WITH_MEMBERS: RequestKind.BATCHED_READ,
    _Operation.CREATED_BOARD_ITEMS: RequestKind.BATCHED_READ,
    _Operation.ORIGIN_LOOKUP: RequestKind.ITEM_READ,
    _Operation.ISSUE: RequestKind.ITEM_READ,
    _Operation.DOCUMENT_IN_PROJECT: RequestKind.ITEM_READ,
    _Operation.SUB_ISSUES: RequestKind.ITEM_READ,
    _Operation.ISSUE_DETAIL: RequestKind.ITEM_READ,
    _Operation.COMMENTS: RequestKind.ITEM_READ,
    _Operation.COMMENT_ISSUE: RequestKind.ITEM_READ,
    _Operation.DEPENDENCIES: RequestKind.ITEM_READ,
    _Operation.REPOSITORY: RequestKind.ITEM_READ,
    _Operation.BOARD_FIELDS: RequestKind.ITEM_READ,
    _Operation.PROJECT_VISIBILITY: RequestKind.ITEM_READ,
    _Operation.CREATION_CONTEXT: RequestKind.ITEM_READ,
    _Operation.CREATE_ISSUES: RequestKind.ALIASED_MUTATIONS,
    _Operation.WRITE_FIELD_VALUES: RequestKind.ALIASED_MUTATIONS,
    _Operation.CLOSE_ISSUES: RequestKind.ALIASED_MUTATIONS,
    _Operation.UPDATE_ISSUES: RequestKind.ALIASED_MUTATIONS,
    _Operation.ADD_BLOCKED_BYS: RequestKind.ALIASED_MUTATIONS,
    _Operation.WRITE_FIELDS_AND_EDGES: RequestKind.ALIASED_MUTATIONS,
    _Operation.DELETE_ISSUES: RequestKind.ALIASED_MUTATIONS,
}


def request_latency_ms(request: _GraphQLRequest) -> int:
    """What ``request`` would have taken on GitHub, by :data:`REQUEST_LATENCY_MS`.

    Every write the table does not batch is one mutation; an aliased batch is charged once
    per :data:`ALIASED_MUTATIONS_PER_CHARGE` of the slots it sent. A read the table does not
    name fails the journey rather than being charged as something it is not.
    """
    operation = request.operation
    kind = REQUEST_KINDS.get(operation)
    if kind is None:
        assert request.is_mutation, f"no latency kind for {operation}: {request.query}"
        return REQUEST_LATENCY_MS[RequestKind.MUTATION]
    if kind is RequestKind.ALIASED_MUTATIONS:
        sent = sum(
            1 for name, value in request.variables.items() if name[:4] == "send" and value is True
        )
        charges = max(1, -(-sent // ALIASED_MUTATIONS_PER_CHARGE))
        return charges * REQUEST_LATENCY_MS[kind]
    return REQUEST_LATENCY_MS[kind]


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
#: Each audited interpreter also records its own start and, at exit, its end, and each store
#: process it launched records its end when that interpreter reaps it, so a journey can say
#: how long every store invocation and the program around them lasted. The reaping hooks are
#: installed at an interpreter's first store launch, once the module that reaps it is loaded,
#: so an interpreter that launches no store imports nothing for them.
STORE_CALL_AUDIT = """\
import atexit, json, os, pathlib, sys, time
trace = pathlib.Path(os.environ["FOLLOW_UP_BUDGET_TRACE"])
def record(kind, argv):
    # The system-wide monotonic clock the board stand-in stamps each request with.
    entry = {"kind": kind, "argv": argv, "at": time.monotonic(), "pid": os.getpid()}
    with trace.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry) + "\\n")
record("interpreter", sys.orig_argv)
atexit.register(record, "interpreter-exit", sys.orig_argv)
if "orchestrator.follow_up_tickets" in sys.orig_argv:
    record("module", sys.orig_argv)
def store(argv):
    return pathlib.Path(argv[0]).name == "onetaskgraph"
hooked = set()
def hook_reaping():
    subprocess = sys.modules["subprocess"]
    if "subprocess" not in hooked:
        hooked.add("subprocess")
        reap = subprocess.Popen._handle_exitstatus
        def handled(self, *args, **keywords):
            first = self.returncode is None
            answer = reap(self, *args, **keywords)
            if first and store(list(self.args)):
                record("store-exit", list(self.args))
            return answer
        subprocess.Popen._handle_exitstatus = handled
    transports = sys.modules.get("asyncio.base_subprocess")
    if transports is not None and "asyncio" not in hooked:
        hooked.add("asyncio")
        exited = transports.BaseSubprocessTransport._process_exited
        def process_exited(self, returncode):
            if store(list(self._proc.args)):
                record("store-exit", list(self._proc.args))
            return exited(self, returncode)
        transports.BaseSubprocessTransport._process_exited = process_exited
def audit(event, args):
    if event == "subprocess.Popen" and pathlib.Path(args[0]).name == "onetaskgraph":
        hook_reaping()
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


def closed_through_rest(issue: _Issue, environment: Mapping[str, str], reason: str) -> RestRequest:
    """Close ``issue`` the way a person on github.com, or an agent holding the board
    credential, does: one `PATCH` to GitHub's REST issue endpoint, which writes the issue
    and no board field. ``environment`` names the `followups` endpoint and the credential;
    the request the loopback logged for it is returned."""
    endpoint = environment[f"{FOLLOWUPS_ENV_PREFIX}CONFIG__ENDPOINT"].removesuffix("/graphql")
    path = f"/repos/{issue.repository.owner}/{issue.repository.name}/issues/{issue.number}"
    sent = len(_GitHubFixture.rest_requests)
    request = urllib.request.Request(  # noqa: S310 - the loopback's own http:// endpoint
        f"{endpoint}{path}",
        data=json.dumps({"state": "closed", "state_reason": reason}).encode(),
        headers={
            "Authorization": f"Bearer {environment['GH_PROJECTS_TOKEN']}",
            "Content-Type": "application/json",
        },
        method="PATCH",
    )
    with urllib.request.urlopen(request, timeout=30) as answer:  # noqa: S310 - as above
        assert answer.status == 200, answer.status
    (logged,) = _GitHubFixture.rest_requests[sent:]
    assert (logged.method, logged.path) == ("PATCH", path), logged
    return logged


def writes_naming(
    issue: _Issue, requests: Sequence[_GraphQLRequest] | None = None
) -> list[_GraphQLRequest]:
    """Every write among ``requests`` — by default, all the board was sent since its log was
    last cleared — that names ``issue``'s row or the issue itself: a Status write addresses
    the row, a closure the content."""
    names = (f'"{issue.item_id}"', f'"{issue.content_id}"')
    return [
        request
        for request in (_GitHubFixture.requests if requests is None else requests)
        if request.is_mutation and any(name in json.dumps(request.variables) for name in names)
    ]


def planted_comment(
    issue: _Issue, body: str, author: str | None, at: str | None = None
) -> dict[str, object]:
    """A comment a person wrote on GitHub, dated ``at`` or now: what a write here never adds.

    The board answers every comment a write adds as the token's own account, so a person or an
    app commenting on the live board is planted on the row instead, signed as they would be.
    """
    issue.held_by.commented += 1
    moment = at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    identifier = f"IC_planted_{issue.held_by.commented}"
    comment: dict[str, object] = {
        "id": identifier,
        "author": None if author is None else {"login": author},
        "createdAt": moment,
        "updatedAt": moment,
        "body": body,
        "url": f"{issue.content()['url']}#issuecomment-{identifier}",
    }
    issue.comments.append(comment)
    return comment


def planted_edit(issue: _Issue, identifier: str, body: str, at: str | None = None) -> None:
    """A person's edit of one comment on GitHub, which the board answers no mutation for."""
    (comment,) = [one for one in issue.comments if one["id"] == identifier]
    comment["body"] = body
    comment["updatedAt"] = at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
