"""Drive this checkout's committed plan-store configuration through the real CLI."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar, Literal, NewType, TypedDict

import jsonschema
import plan_root_variable
import pytest
from fake_backend import PROMPT_LOG_ENV
from nx_workspace import shares_workspace_install
from onetaskgraph_release import checkout as throwaway_checkout
from onetaskgraph_release import (
    fetch_double,
    host_target,
    installed_binary,
    release_fixture,
    run_installer,
)
from plan_store_pin import (
    PACING_FLOOR,
    adopted_release,
    held_below_the_pacing_floor,
)
from published_tools import ONETASKGRAPH_BIN
from registered_checkouts import registered_checkouts
from stub_onetaskgraph import LOG_ENV, PASS_SHOWS_ENV, REAL_ENV, STUBBED
from test_orchestrate_launch_e2e import _environment as _launch_environment
from waits import timeout as e2e_timeout

from orchestrator import plan_store
from orchestrator.project_store import PlanDocument, PlanNode, frontmatter, write_plan_project
from orchestrator.root import REPO_ROOT

ADOPTED = (REPO_ROOT / "config" / "onetaskgraph.version").read_text().strip()
#: The fault injector this journey points `onepipeline` at. Its marker and its three
#: steering variables are imported from that module rather than restated, so the log
#: these assertions read cannot drift from the log that file writes.
STUB_ONETASKGRAPH = Path(__file__).resolve().parent / "stub_onetaskgraph.py"
_NodeId = NewType("_NodeId", str)
_PipelineId = NewType("_PipelineId", str)
#: A project's native id inside one source — the `<native>` half of a qualified
#: `<source>:<native>`, and what a stored project answers as its own `id`.
_ProjectId = NewType("_ProjectId", str)
#: The project `_write_local_project` renders, named once so the journeys that read it
#: back and the helper that authors its description agree on one identifier.
LOCAL_PROJECT = _ProjectId("launch")
#: The same project as every recipe here names it: qualified by the `authoring` source
#: `onetaskgraph.yaml` roots at this checkout's `.plans`, which these journeys point
#: elsewhere per run. Derived rather than restated, so the native id has one source.
AUTHORING_SOURCE = "authoring"
LOCAL_QUALIFIED = f"{AUTHORING_SOURCE}:{LOCAL_PROJECT}"
#: Where that source's root is named, for the store and for every process it spawns.
#: One spelling, because a journey pointing it one way and a helper reading it another
#: would leave a plan authored in one directory and approved in a second.
AUTHORING_ROOT_ENV = f"ONETASKGRAPH_SOURCES__{AUTHORING_SOURCE.upper()}__CONFIG__ROOT"
#: The one task that project holds, named once so the copy journey can assert which
#: issues a copy created rather than only how many.
LOCAL_TASK_TITLE = "test: launch local project"


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


def _configured_owner() -> str:
    """The board owner the committed `plans` source names."""
    text = (REPO_ROOT / "onetaskgraph.yaml").read_text(encoding="utf-8")
    named = re.search(r"^\s+owner:\s*(\S+)\s*$", text, re.MULTILINE)
    if named is None:
        raise ValueError("onetaskgraph.yaml names no owner for its `plans` source")
    return named.group(1)


CONFIGURED_OWNER = _configured_owner()
#: A repository under the same owner that the board fixture answers as invisible, which
#: is what GitHub answers for one that does not exist or that the token cannot see.
UNREACHABLE_REPOSITORY = _Repository(owner=CONFIGURED_REPOSITORY.owner, name="not-a-repository")
#: A second repository of the configured owner the fixture knows, so a task naming it is
#: filed somewhere the configured fallback is not — the placement the adopted release
#: buys, which a fixture answering one node id for every lookup could never observe.
SIBLING_REPOSITORY = _Repository(owner=CONFIGURED_REPOSITORY.owner, name="oneharness")
#: A repository under an owner the fixture has never heard of. GitHub files a sub-issue
#: only in a repository of the same owner as its parent issue, so a task naming this one
#: is refused before anything is created rather than looked up and found missing.
FOREIGN_REPOSITORY = _Repository(owner="contoso", name="work")


class _AddedNode(TypedDict):
    id: _NodeId
    task: str
    expects_no_diff: bool


class _AddCommand(TypedDict):
    op: Literal["add"]
    node: _AddedNode


class _LiveEditEnvelope(TypedDict):
    version: Literal[1]
    commands: list[_AddCommand]


@dataclass(frozen=True)
class _Settlement:
    status: str
    outcome: str | None

    @classmethod
    def from_mapping(cls, value: object) -> _Settlement:
        if not isinstance(value, dict):
            raise ValueError("stored task requires a settlement object")
        status = value.get("status")
        outcome = value.get("outcome")
        if not isinstance(status, str) or (outcome is not None and not isinstance(outcome, str)):
            raise ValueError("stored task settlement requires string status and outcome values")
        return cls(status=status, outcome=outcome)


@dataclass(frozen=True)
class _StoredTask:
    pipeline_id: _PipelineId
    settlement: _Settlement

    @classmethod
    def from_item(cls, value: object) -> _StoredTask:
        if not isinstance(value, dict):
            raise ValueError("stored task list item must be an object")
        item = value.get("item")
        if not isinstance(item, dict):
            raise ValueError("stored task list item requires an item object")
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError("stored task requires a metadata object")
        pipeline_id = metadata.get("onepipeline.id")
        if not isinstance(pipeline_id, str):
            raise ValueError("stored task requires a pipeline id and settlement object")
        settlement = _Settlement.from_mapping(metadata.get("onepipeline.settlement"))
        return cls(pipeline_id=_PipelineId(pipeline_id), settlement=settlement)


def _stored_tasks(body: str) -> list[_StoredTask]:
    payload = json.loads(body)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("stored task response requires an items array")
    return [_StoredTask.from_item(item) for item in payload["items"]]


def _stored_project_content(body: str, project_id: _ProjectId) -> str | None:
    """The description one stored project carries, read through the store's own surface.

    Read as the reader rather than off the local-md file, because the property this
    journey is about is what a *consumer* of the plan store sees — an operator opening
    the board, or `just plans` — and the write-back reaches that through the store's
    own contract rather than by editing the file this checkout happened to render.

    A project carrying no description is returned as `None` rather than refused,
    because that is precisely the state the deletion this journey is about leaves
    behind: refusing it here would fail the journey inside its reader, where the
    message is about a response shape, instead of at the comparison that can say the
    description was rewritten.
    """
    payload = json.loads(body)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("stored project response requires an items array")
    for entry in payload["items"]:
        if not isinstance(entry, dict):
            raise ValueError("stored project list item must be an object")
        item = entry.get("item")
        if not isinstance(item, dict):
            raise ValueError("stored project list item requires an item object")
        if item.get("id") != project_id:
            continue
        content = item.get("content")
        if content is not None and not isinstance(content, str):
            raise ValueError(f"stored project {project_id!r} carries a non-string content")
        return content
    raise ValueError(f"the store holds no project {project_id!r}")


#: The node identifiers GitHub gives the things on one board, each a type of its own
#: because the writes under test address different ones: a field value is set on a
#: board item, a sub-issue link names issue content, a status is chosen among one
#: field's options, and `createIssue` takes a repository a board has none of. Spelling
#: them all `str` would let this fixture answer a source that had confused two of them
#: exactly as it answers one that had not, which is the confusion it exists to catch.
_BoardNodeId = NewType("_BoardNodeId", str)
_BoardItemId = NewType("_BoardItemId", str)
_IssueNodeId = NewType("_IssueNodeId", str)
_FieldNodeId = NewType("_FieldNodeId", str)
_FieldOptionId = NewType("_FieldOptionId", str)
_RepositoryNodeId = NewType("_RepositoryNodeId", str)
#: The board field the source owns and reads a copy's origin back out of, and the
#: `Status` field every board carries. A category this board cannot represent refuses
#: the write naming it, so the options below are the `plans` board's own: the three the
#: shipped mapping reaches by name, `Done`, which a closed `done` issue selects by its
#: spelling, and `Needs attention`, which `onetaskgraph.yaml` sends `unknown` to.
ORIGIN_FIELD_NAME = "onetaskgraph.origin"
ORIGIN_FIELD_ID = _FieldNodeId("FIELD_origin")
STATUS_FIELD_ID = _FieldNodeId("FIELD_status")


@dataclass(frozen=True)
class _StatusOption:
    id: _FieldOptionId
    name: str

    def rendered(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name}


# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] The stand-in board answers
# the option `onetaskgraph.yaml` names, because the journey asserts that name reaches the
# wire. Reconciling it against the live board would take the board credential no test may use.
NEEDS_ATTENTION = _StatusOption(id=_FieldOptionId("OPT_attention"), name="Needs attention")
# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]
STATUS_OPTIONS: tuple[_StatusOption, ...] = (
    _StatusOption(id=_FieldOptionId("OPT_todo"), name="Todo"),
    _StatusOption(id=_FieldOptionId("OPT_progress"), name="In Progress"),
    # llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] The live board's own
    # option, answered so the stand-in carries the options the task names; reconciling it
    # against that board would take the board credential no test may use.
    _StatusOption(id=_FieldOptionId("OPT_done"), name="Done"),
    # llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]
    _StatusOption(id=_FieldOptionId("OPT_backlog"), name="Backlog"),
    NEEDS_ATTENTION,
)
#: The node id the fixture answers each repository it knows with, one per `owner/name`.
#: The journeys assert which of these reaches `createIssue`, which is the whole of what
#: naming a repository buys — on the source, whose configured one is the fallback a
#: board has none of its own for, and on an item, whose own `repositories` decides where
#: its issue is created. Distinct ids per repository because a fixture answering one id
#: for every lookup would pass a source that resolved the right repository and then
#: created every issue in the configured one.
REPOSITORY_NODE_IDS: dict[_Repository, _RepositoryNodeId] = {
    CONFIGURED_REPOSITORY: _RepositoryNodeId("R_ai_orchestrator"),
    SIBLING_REPOSITORY: _RepositoryNodeId("R_oneharness"),
}
REPOSITORY_NODE_ID = REPOSITORY_NODE_IDS[CONFIGURED_REPOSITORY]


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

    def field_values(self) -> dict[str, object]:
        """This row's board field values, as both routes to it select them.

        One method rather than a copy per route: the board's own `items` connection and
        an issue's `projectItems` select the same field values, and the whole of what
        the source relies on is that an issue reached either way resolves to one item.
        """
        return {
            "nodes": [
                {
                    "name": "Todo",
                    "field": {
                        "id": STATUS_FIELD_ID,
                        "name": "Status",
                        "options": [option.rendered() for option in STATUS_OPTIONS],
                    },
                }
            ],
            "pageInfo": {"hasNextPage": False},
        }

    def content(self) -> dict[str, object]:
        """The issue itself, as every document that reaches it selects it."""
        return {
            "__typename": "Issue",
            "id": self.content_id,
            "title": self.title,
            "body": self.body,
            "url": f"https://github.com/{self.repository}/issues/{self.item_id}",
            "createdAt": "2026-08-26T00:00:00Z",
            "updatedAt": "2026-08-26T00:00:00Z",
            "state": "OPEN",
            "stateReason": None,
            "repository": {"nameWithOwner": str(self.repository)},
            "parent": None if self.parent_id is None else {"id": self.parent_id},
            "subIssuesSummary": {"total": self.sub_issues},
            "labels": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        }

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
                        "project": {"number": CONFIGURED_PROJECT_NUMBER},
                        "fieldValues": self.field_values(),
                    }
                ],
                "pageInfo": {"hasNextPage": False},
            }
        }


# llmlint: ignore-block[e2e_not_mocked] GitHub's Projects GraphQL API is the one boundary
# these journeys cannot drive for real, and the reason is the subject: driving it means
# writing to a live board and performing the very burst of content-creating mutations the
# pacing journey exists to measure, against an account whose other board holds this
# repository's plans. What is doubled stops at the wire — the installed `onetaskgraph`
# binary, `just plans`, the recipes and the shell around them are all real, and the source
# accepts an `endpoint` for exactly this. The fixture is stateful and answers the source's
# own documents rather than a canned reply, so a source that changed what it asks for
# fails here naming the operation, which is how the adopted release's move off the board
# walk was caught.
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
            title=BOARD_PROJECT_TITLE,
            body="A plan an operator wrote on the board itself.",
            sub_issues=1,
        )
        child = _Issue(
            item_id=_BoardItemId("PVTI_board_task"),
            content_id=_IssueNodeId("I_board_task"),
            title=BOARD_TASK_TITLE,
            body="Its one sub-issue, which is what makes the issue above a project.",
            parent_id=parent.content_id,
        )
        self.issues: list[_Issue] = [parent, child]
        self.created: list[_Issue] = []

    def issues_created_and_kept(self) -> list[_Issue]:
        return [issue for issue in self.created if issue in self.issues]

    def board_response(self) -> dict[str, object]:
        return {
            "data": {
                "owner": {
                    "projectV2": {
                        "id": self.node_id,
                        "title": self.title,
                        "fields": {
                            "nodes": [
                                {
                                    "__typename": "ProjectV2SingleSelectField",
                                    "id": STATUS_FIELD_ID,
                                    "name": "Status",
                                    "options": [option.rendered() for option in STATUS_OPTIONS],
                                },
                                {
                                    "__typename": "ProjectV2Field",
                                    "id": ORIGIN_FIELD_ID,
                                    "name": ORIGIN_FIELD_NAME,
                                },
                            ],
                            "pageInfo": {"hasNextPage": False},
                        },
                        "items": {
                            "nodes": [issue.item() for issue in self.issues],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        },
                    }
                }
            }
        }

    def search_response(self, search: str) -> dict[str, object]:
        """The issues of this board a search finds, narrowed the way GitHub narrows one.

        The `in:title "…"` qualifier is honoured rather than ignored: the source
        compares a title for equality afterwards, so a fixture answering every search
        with the whole board would pass a source that had stopped scoping its search at
        all — and scoping it is the whole of what this read buys over walking the board.
        """
        wanted = _TITLE_QUALIFIER.search(search)
        found = [
            issue
            for issue in self.issues
            if wanted is None or _unescaped(wanted.group("title")) in issue.title
        ]
        return {
            "data": {
                "search": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [issue.board_issue() for issue in found],
                }
            }
        }

    def node_response(self, node_id: object) -> dict[str, object]:
        """One issue by its own node id, or the null GitHub answers an unheld one with."""
        for issue in self.issues:
            if issue.content_id == node_id:
                return {"data": {"node": issue.board_issue()}}
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
            title=title,
            body=body,
            repository=_repository_of(payload.get("repositoryId")),
        )
        self.created.append(created)
        self.issues.append(created)
        return {"data": {"createIssue": {"issue": {"id": created.content_id}}}}

    def _issue(self, content_id: object) -> _Issue:
        for issue in self.issues:
            if issue.content_id == content_id:
                return issue
        raise ValueError(f"the board holds no issue {content_id!r}")

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
        return _Repository(owner=self.string("owner"), name=self.string("name"))

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
    SEARCH = "search"
    ISSUE = "issue"
    SUB_ISSUES = "subIssues"
    REPOSITORY = "repository"
    DEPENDENCIES = "dependencies"
    CREATE_ISSUE = "createIssue"
    ADD_TO_BOARD = "addToBoard"
    UPDATE_FIELD = "updateField"
    ADD_SUB_ISSUE = "addSubIssue"
    UPDATE_ISSUE = "updateIssue"
    DELETE_ISSUE = "deleteIssue"


#: Each operation's marker in the document the source sends, in the order they are
#: tried. The source ships its queries as constants, so the root field is a stable
#: substring of each one and is what tells a board read from the writes that follow it.
_OPERATIONS: dict[str, _Operation] = {
    "repositoryOwner": _Operation.BOARD,
    "search(query:": _Operation.SEARCH,
    "subIssues(first:": _Operation.SUB_ISSUES,
    "node(id:$id){__typename ...BoardIssue}": _Operation.ISSUE,
    "{repository(owner:": _Operation.REPOSITORY,
    "blockedBy(first:": _Operation.DEPENDENCIES,
    "createIssue(": _Operation.CREATE_ISSUE,
    "addProjectV2ItemById(": _Operation.ADD_TO_BOARD,
    "updateProjectV2ItemFieldValue(": _Operation.UPDATE_FIELD,
    "addSubIssue(": _Operation.ADD_SUB_ISSUE,
    "updateIssue(": _Operation.UPDATE_ISSUE,
    "deleteIssue(": _Operation.DELETE_ISSUE,
}

#: The title qualifier the source narrows a board search with, and the two characters
#: GitHub's quoting grammar gives a meaning inside a quoted phrase. Both are the
#: source's own spelling: it escapes a backslash and a double quote before it sends
#: one, so a fixture that read the qualifier literally would find no issue whose title
#: contains either.
_TITLE_QUALIFIER = re.compile(r'in:title "(?P<title>(?:[^"\\]|\\.)*)"')


def _unescaped(quoted: str) -> str:
    """One quoted search phrase, as the title the source was looking for."""
    return quoted.replace('\\"', '"').replace("\\\\", "\\")


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


#: What GitHub answers a burst of content creation with, published as prose at
#: https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api. The
#: source matches on this text rather than on the status, for the reason above.
# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] One copy here and the
# other in GitHub's prose: nothing machine-readable to generate from or reconcile
# against, and observing the real refusal would mean performing the burst that earns it.
# The half this repository can reach — the installed CLI's classifier against this body —
# is what the journey below asserts.
SECONDARY_LIMIT = _Refusal(
    status=403,
    body={
        "message": (
            "You have exceeded a secondary rate limit. Please wait a few minutes "
            "before you try again."
        )
    },
)
# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]
#: The same status with nothing about a limit in it, which is a credential this token
#: lacks. Named beside its sibling because one without the other proves nothing: a
#: source that called every refusal a rate limit would pass the first journey alone.
FORBIDDEN = _Refusal(
    status=403, body={"message": "Resource not accessible by personal access token"}
)
#: GitHub's published ceiling on content-generating requests, per minute (same page as
#: :data:`SECONDARY_LIMIT`), and the shortest interval that cannot exceed it. Written as
#: the division because the number is GitHub's rather than this repository's: a literal
#: 0.75 would read as a figure somebody here chose.
# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] The same boundary as
# the directive above. What this repository can gate — that the installed CLI paces at
# this bound — the journey below asserts over the gaps its copy really left, so a release
# that moved its interval in either direction fails against this number.
CONTENT_CREATION_PER_MINUTE = 80
SHIPPED_MUTATION_INTERVAL = 60 / CONTENT_CREATION_PER_MINUTE
# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


def _mutation_gaps(requests: list[_GraphQLRequest]) -> list[float]:
    """The intervals between consecutive content-creating requests, in seconds.

    Queries are excluded rather than merely uncounted: only the mutations are what
    GitHub's second limiter counts, and a copy interleaves board reads with its writes,
    so a gap measured over every request would be shortened by the reads between them.
    """
    sent = [request.received for request in requests if request.is_mutation]
    return [later - earlier for earlier, later in zip(sent, sent[1:], strict=False)]


class _GitHubFixture(BaseHTTPRequestHandler):
    requests: ClassVar[list[_GraphQLRequest]]
    #: What every request is refused with, or `None` to answer the board normally.
    refusal: ClassVar[_Refusal | None] = None

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
        match operation:
            case _Operation.BOARD:
                return BOARD.board_response()
            case _Operation.SEARCH:
                return BOARD.search_response(request.string("search"))
            case _Operation.ISSUE:
                return BOARD.node_response(request.variables.get("id"))
            case _Operation.SUB_ISSUES:
                return BOARD.sub_issues_response(request.variables.get("id"))
            case _Operation.REPOSITORY:
                return self._repository(request.repository)
            case _Operation.DEPENDENCIES:
                empty = {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}
                return {
                    "data": {"node": {"__typename": "Issue", "blockedBy": empty, "blocking": empty}}
                }
            case _Operation.CREATE_ISSUE:
                return BOARD.create_issue(request.variables)
            case _Operation.ADD_TO_BOARD:
                return BOARD.add_to_board(request.variables)
            case _Operation.UPDATE_ISSUE:
                return {"data": {"updateIssue": {"issue": {"id": request.input_value("id")}}}}
            case _Operation.UPDATE_FIELD:
                return {
                    "data": {
                        "updateProjectV2ItemFieldValue": {
                            "projectV2Item": {"id": request.input_value("itemId")}
                        }
                    }
                }
            case _Operation.ADD_SUB_ISSUE:
                return BOARD.add_sub_issue(request.variables)
            case _Operation.DELETE_ISSUE:
                return BOARD.delete_issue(request.variables)

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
def _serving_board(refusal: _Refusal | None = None) -> Iterator[dict[str, str]]:
    """Serve the board fixture, yielding the environment that points `plans` at it.

    The board is reset per journey rather than shared: it is mutated by the writes a
    copy performs, so a second journey reading the residue of the first would report a
    board somebody else's copy filled in.

    ``refusal`` makes every request come back as one GitHub refusal instead. It is a
    property of the server rather than of a call because what a caller sees is the
    diagnostic the source composes, and the source retries before it composes one.
    """
    BOARD.reset()
    _GitHubFixture.requests = []
    _GitHubFixture.refusal = refusal
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GitHubFixture)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield {
            "GH_PROJECTS_TOKEN": "fixture-token",
            "ONETASKGRAPH_SOURCES__PLANS__CONFIG__ENDPOINT": (
                f"http://127.0.0.1:{server.server_port}/graphql"
            ),
        }
    finally:
        server.shutdown()
        # The finding the two directives answer is about which Nx project owns this
        # file, so it is scoped to the lines that drew it; the file scopes one of the
        # two itself further down, and a second open block for one rule is refused.
        # llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] see above
        # llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] see above
        # Bounded like every other wait here: `shutdown` asks the serving loop to stop
        # and this waits for it, so a handler wedged mid-request would otherwise hold
        # the tier rather than fail it.
        thread.join(timeout=e2e_timeout(60))
        assert not thread.is_alive(), (
            "the fixture's GitHub stand-in was still serving after it was asked to stop, "
            "so a request handler is wedged and this journey's server outlives it"
        )
        # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
        # llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
        server.server_close()
        _GitHubFixture.refusal = None


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

    This is separate from :func:`_plan_environment` because a journey that *launches*
    composes these names on top of `_launch_environment`, whose whole job is isolating a
    launch — its own runs root, its paid-provider guard, the launcher variables it
    deliberately popped. Updating that environment with a whole ambient copy restores
    every one of them. A dispatch of this repository exports `ONEPIPELINE_RUNS_DIR=runs`,
    so the three journeys below that composed the two that way launched into the
    checkout's own shared runs root rather than their own: run concurrently under
    `-n 4`, they minted `launch`, `launch-2` and `launch-3` between them, and the one
    that reads its run id back failed on the name it was given.
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


def _write_local_project(root: Path) -> None:
    """Write the launchable local plan these journeys read, copy and launch.

    Its design document is written and approved here because a launch is refused without
    one — the plan is put in front of a person as that document, and nothing here is
    about that gate. Approved through the real recipe rather than by writing a record by
    hand, so no journey here begins from state the exercised interface cannot produce,
    and the authoring root is named in that command's own environment because the store
    resolves it from there and every journey here points it somewhere of its own.
    """
    write_plan_project(
        root,
        {
            "schema_version": 3,
            "name": LOCAL_PROJECT,
            "tasks": [
                {
                    "id": "probe",
                    "persona": "engineer",
                    "title": LOCAL_TASK_TITLE,
                    "task": "## What\nReply with done.\n\n## Why\nProve launch.\n\n"
                    "## Acceptance criteria\n- The task settles.\n",
                }
            ],
        },
    )
    documents = root / "documents"
    documents.mkdir(parents=True, exist_ok=True)
    (documents / f"{LOCAL_PROJECT}-design.md").write_text(
        frontmatter(
            {"title": f"Design: {LOCAL_PROJECT}", "project": LOCAL_PROJECT},
            "## What\n\nOne probe.\n\n## Why\n\nA launch needs a plan.\n\n"
            "## Architecture\n\nOne node.\n\n## Contracts\n\nNone.\n\n"
            "## Acceptance criteria\n\nThe node settles.\n\n## Planned tasks\n\n"
            "| Task | What it delivers | Depends on | Where it lives |\n"
            "| --- | --- | --- | --- |\n"
            f"| {LOCAL_TASK_TITLE} | the probe | none | {root}/tasks/{LOCAL_PROJECT} |\n",
        ),
        encoding="utf-8",
    )
    recording = subprocess.run(
        ["just", "approve-design", LOCAL_QUALIFIED],
        cwd=REPO_ROOT,
        env={**os.environ, AUTHORING_ROOT_ENV: str(root)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert recording.returncode == 0, (
        f"the fixture could not approve {LOCAL_QUALIFIED}: {recording.stdout}{recording.stderr}"
    )


def test_credentialed_plan_store_reads_local_and_remote_sources(tmp_path: Path) -> None:
    """The committed owner/project pair reaches a fixture through the installed release.

    The board's projects are what this reads back, never the board: the adopted source
    holds many projects on one board as issues and their sub-issues, so the board's own
    title is a container's name and answering `project list` with it would be reporting
    a project nobody wrote.
    """
    _write_local_project(tmp_path)
    with _serving_board() as remote:
        environment = _plan_environment(tmp_path)
        environment.update(remote)
        result = subprocess.run(
            ["just", "plans", "project", "list", "--json"],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    titles = {item["item"]["title"] for item in payload["items"]}
    assert {"launch", BOARD_PROJECT_TITLE}.issubset(titles)
    assert BOARD.title not in titles, (
        "the board is a container of projects and not a project, and this read "
        f"returned its own title among {titles}"
    )
    # The committed pair reaches the fixture as the *scope of a search* rather than as
    # the arguments of a board walk: the adopted release reads a board's projects by
    # searching its issues, so `project:<owner>/<number>` is where those two values now
    # appear on the wire. Asserted as that scope rather than as any board read, because
    # a walk is what this release stopped doing — a journey still demanding one would
    # fail on the improvement.
    searches = [
        request for request in _GitHubFixture.requests if request.operation is _Operation.SEARCH
    ]
    asked = [str(request.operation) for request in _GitHubFixture.requests]
    assert searches, (
        "the adopted release reads this board by searching its issues; the source made "
        f"no such request, and asked instead for {asked}"
    )
    assert all(
        f"project:{CONFIGURED_OWNER}/{CONFIGURED_PROJECT_NUMBER} " in request.string("search")
        for request in searches
    ), (
        "every board search has to be scoped to the configured owner and project "
        f"number, and this read sent {[request.string('search') for request in searches]}"
    )


def test_project_copy_files_its_issues_in_the_configured_repository(tmp_path: Path) -> None:
    """A copy of items naming no repository creates their issues in the one this checkout names.

    That is the fallback, and it is what the `repository` field buys: a board has no
    repository of its own and `createIssue` requires one, so a copy of a project and a
    task that name none either resolves the configured repository's node id and files
    both against it or is refused. The node id the fixture answers with is asserted at
    `createIssue` rather than only at the lookup, because a source that asked for the
    repository and then created its issues somewhere else would pass the lookup
    assertion alone. An item that *does* name a repository is the journey below, which
    is where the adopted release parts from this one.
    """
    _write_local_project(tmp_path)
    with _serving_board() as remote:
        environment = _plan_environment(tmp_path)
        environment.update(remote)
        copied = subprocess.run(
            ["just", "plans", "project", "copy", LOCAL_QUALIFIED, "--to", "plans"],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    assert copied.returncode == 0, copied.stdout + copied.stderr
    lookups = {
        request.repository
        for request in _GitHubFixture.requests
        if request.operation is _Operation.REPOSITORY
    }
    assert lookups == {CONFIGURED_REPOSITORY}, (
        "the copy has to resolve the repository this checkout configures, and it "
        f"looked up {lookups}"
    )
    creations = [
        request
        for request in _GitHubFixture.requests
        if request.operation is _Operation.CREATE_ISSUE
    ]
    assert {request.input_value("repositoryId") for request in creations} == {REPOSITORY_NODE_ID}, (
        "every created issue has to carry the configured repository's own node id"
    )
    assert {issue.title for issue in BOARD.created} == {LOCAL_PROJECT, LOCAL_TASK_TITLE}
    filed = {
        (request.input_value("issueId"), request.input_value("subIssueId"))
        for request in _GitHubFixture.requests
        if request.operation is _Operation.ADD_SUB_ISSUE
    }
    project, task = BOARD.created[0], BOARD.created[1]
    assert filed == {(project.content_id, task.content_id)}, (
        f"a project's tasks are its issue's sub-issues, and this copy filed {filed}"
    )


def _hosted(repository: _Repository) -> str:
    """``repository`` as the normalized origin a task record's `repositories` holds."""
    return f"github.com/{repository}"


#: The project whose tasks name where their work lands, the way a plan of this
#: repository is written since a task's `repositories` became the field a copy files by.
PLACED_PROJECT = _ProjectId("placed")
PLACED_QUALIFIED = f"{AUTHORING_SOURCE}:{PLACED_PROJECT}"
#: The creation arms of the placement rule a plan of this repository can write, each as
#: the one normalized origin a task's `repositories` names — or none — keyed to the
#: title its issue is created under so a `createIssue` can be read back per title: the
#: configured repository, where the rule's answer equals the fallback; a sibling, whose
#: placement is what the adopted release changes; and none, the fallback. Several is not
#: here because the renderer a plan is written with names one `repo` per node, so no
#: record this repository produces reaches that arm.
PLACED_TASK_TITLES: dict[str | None, str] = {
    _hosted(CONFIGURED_REPOSITORY): "feat: change the orchestrator",
    _hosted(SIBLING_REPOSITORY): "feat: change the sibling",
    None: "docs: name no repository",
}


@dataclass(frozen=True)
class _PlacementRefusal:
    """One arm of the placement rule's refusals, as a task record would meet it."""

    #: The normalized origin the task's `repositories` names.
    origin: str
    #: Whether the source asks GitHub about that repository before refusing.
    looked_up: bool

    @property
    def slug(self) -> str:
        """The `owner/name` the refusal and the lookup spell the repository as."""
        return self.origin.split("/", 1)[1]


PLACEMENT_REFUSALS: dict[str, _PlacementRefusal] = {
    # GitHub files a sub-issue only in a repository of the same owner as its parent
    # issue, so the owner comparison refuses before anything asks about the repository.
    "another owner": _PlacementRefusal(_hosted(FOREIGN_REPOSITORY), looked_up=False),
    # One the token cannot see comes back null from the lookup the rule has to make.
    "a repository the token cannot see": _PlacementRefusal(
        _hosted(UNREACHABLE_REPOSITORY), looked_up=True
    ),
    # One that is not a GitHub repository at all is refused by its spelling.
    "a repository off GitHub": _PlacementRefusal(
        f"gitlab.com/{FOREIGN_REPOSITORY}", looked_up=False
    ),
}


def _write_placed_project(root: Path, tasks: Mapping[str | None, str]) -> None:
    """Write a project naming no repository whose tasks each name one, or none.

    Written through the same renderer `just plan` writes a plan with, so the record a
    copy reads is the record this repository produces — a task's hosted origin lands in
    its own top-level `repositories`, and a task naming none carries no such field —
    rather than a shape this journey composed by hand. No design document, because the
    store's own copy verb is what this drives and nothing here launches.
    """
    write_plan_project(
        root,
        {
            "schema_version": 3,
            "name": PLACED_PROJECT,
            "tasks": [
                {
                    "id": f"task-{index}",
                    "persona": "engineer",
                    "title": title,
                    "task": "## What\nReply with done.\n\n## Why\nProve placement.\n\n"
                    "## Acceptance criteria\n- The task settles.\n",
                    **({} if origin is None else {"repo": origin}),
                }
                for index, (origin, title) in enumerate(tasks.items())
            ],
        },
    )


# llmlint: ignore[e2e_not_mocked] The one boundary doubled is GitHub's Projects API,
# for the reason the block around `_Board` above gives — driving it for real writes to
# the live board this repository plans on — and everything above the wire is real: the
# installed `onetaskgraph`, `just plans`, and the record the renderer wrote.
def _copy_placed_project(root: Path) -> subprocess.CompletedProcess[str]:
    with _serving_board() as remote:
        environment = _plan_environment(root)
        environment.update(remote)
        return subprocess.run(
            ["just", "plans", "project", "copy", PLACED_QUALIFIED, "--to", "plans"],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )


def _created_titled(title: str) -> _Issue:
    for issue in BOARD.created:
        if issue.title == title:
            return issue
    raise AssertionError(f"the copy created no issue titled {title!r}: {BOARD.created}")


def _looked_up() -> list[_Repository]:
    return [
        request.repository
        for request in _GitHubFixture.requests
        if request.operation is _Operation.REPOSITORY
    ]


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] The same finding under
# its sibling name, answered the same way as the block just below: the placement is the
# module's and predates these two journeys.
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] Same subject and same
# inputs as every journey beside them — the installed plan-store CLI driven against the
# loopback board — so `nx affected` already selects this whole module together, and a
# project of two functions would buy no selection while moving a placement that predates
# them. They run in `orchestrator:test`, keyed `codeWorkspace`, which is the edge every
# other CLI-spawning journey in this file already pays.
def test_project_copy_files_each_task_issue_in_the_repository_its_own_record_names(
    tmp_path: Path,
) -> None:
    """A task's `repositories` decides where its issue is created; none means its project's.

    Read off the fixture's own record of `createIssue` per created title: the task naming
    this repository carries the configured repository's node id, the task naming the
    sibling carries the sibling's, and the task naming none carries the node id of the
    repository the project's own issue was created in — which is the configured one here
    because the project names none, and is asserted as the project issue's rather than
    as the fallback so a source that stopped placing by the parent would fail even where
    the two coincide. Each task issue is then a sub-issue of the
    project issue whatever repository it lives in, which is the cross-repository pairing
    GitHub permits under one owner, and each distinct repository is looked up once for
    the whole command.
    """
    _write_placed_project(tmp_path, PLACED_TASK_TITLES)
    copied = _copy_placed_project(tmp_path)

    assert copied.returncode == 0, copied.stdout + copied.stderr
    assert {issue.title for issue in BOARD.created} == {
        PLACED_PROJECT,
        *PLACED_TASK_TITLES.values(),
    }
    project = _created_titled(PLACED_PROJECT)
    assert project.repository == CONFIGURED_REPOSITORY, (
        "a project naming no repository is created in the configured one, and this one "
        f"was created in {project.repository}"
    )
    created_in = {
        request.input_value("title"): request.input_value("repositoryId")
        for request in _GitHubFixture.requests
        if request.operation is _Operation.CREATE_ISSUE
    }
    expected_in = {
        PLACED_TASK_TITLES[_hosted(CONFIGURED_REPOSITORY)]: CONFIGURED_REPOSITORY,
        PLACED_TASK_TITLES[_hosted(SIBLING_REPOSITORY)]: SIBLING_REPOSITORY,
        PLACED_TASK_TITLES[None]: project.repository,
    }
    for title, repository in expected_in.items():
        assert created_in[title] == REPOSITORY_NODE_IDS[repository], (
            f"{title!r} has to be created in {repository}, and this copy created {created_in}"
        )
    filed = {
        (request.input_value("issueId"), request.input_value("subIssueId"))
        for request in _GitHubFixture.requests
        if request.operation is _Operation.ADD_SUB_ISSUE
    }
    assert filed == {
        (project.content_id, _created_titled(title).content_id)
        for title in PLACED_TASK_TITLES.values()
    }, f"every task issue is a sub-issue of the project's, and this copy filed {filed}"
    lookups = _looked_up()
    expected_lookups = sorted(
        str(repository) for repository in (CONFIGURED_REPOSITORY, SIBLING_REPOSITORY)
    )
    assert sorted(str(repository) for repository in lookups) == expected_lookups, (
        "each distinct repository is resolved once per command, and this copy looked up "
        f"{[str(repository) for repository in lookups]}"
    )


@pytest.mark.parametrize("refusal", PLACEMENT_REFUSALS.values(), ids=PLACEMENT_REFUSALS)
def test_project_copy_is_refused_for_a_task_whose_repository_cannot_hold_its_issue(
    tmp_path: Path, refusal: _PlacementRefusal
) -> None:
    """A task naming a repository its issue cannot be created in is refused by name.

    Three arms, each refused before `createIssue` rather than creating an issue that
    `addSubIssue` would then leave orphaned: a repository under another owner than the
    project issue's, one the token cannot see, and one that is not on GitHub at all. The
    refusal names the task and the repository, the fixture's record holds no
    `createIssue` for that task, and only the arm that has to ask GitHub whether the
    repository is there is looked up — the other two are decided from the origin's own
    spelling. The project's own issue, created before its task was refused, is taken back
    with `deleteIssue`, so the board is left as the copy found it.
    """
    title = "feat: change a repository this copy cannot file in"
    _write_placed_project(tmp_path, {refusal.origin: title})
    copied = _copy_placed_project(tmp_path)

    assert copied.returncode != 0, copied.stdout
    assert title in copied.stderr, copied.stderr
    assert refusal.slug in copied.stderr, copied.stderr
    assert title not in {issue.title for issue in BOARD.created}, (
        "a task whose repository is refused has no issue created for it, and this copy "
        f"created {[issue.title for issue in BOARD.created]}"
    )
    asked = [str(repository) for repository in _looked_up()]
    assert (refusal.slug in asked) is refusal.looked_up, f"this copy asked GitHub about {asked}"
    assert not BOARD.issues_created_and_kept(), (
        "a refused copy takes back the project issue it had created before the refusal, "
        f"and this one left {[issue.title for issue in BOARD.issues_created_and_kept()]}"
    )


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]


def test_project_copy_is_refused_when_the_configured_repository_is_unreachable(
    tmp_path: Path,
) -> None:
    """A repository the token cannot see refuses the copy before anything is created."""
    _write_local_project(tmp_path)
    with _serving_board() as remote:
        environment = _plan_environment(tmp_path)
        environment.update(remote)
        environment["ONETASKGRAPH_SOURCES__PLANS__CONFIG__REPOSITORY"] = str(UNREACHABLE_REPOSITORY)
        copied = subprocess.run(
            ["just", "plans", "project", "copy", LOCAL_QUALIFIED, "--to", "plans"],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    assert copied.returncode != 0, copied.stdout
    assert str(UNREACHABLE_REPOSITORY) in copied.stderr, copied.stderr
    assert not BOARD.created, (
        "a repository this destination cannot reach has to refuse before it creates "
        f"anything, and it created {[issue.title for issue in BOARD.created]}"
    )


#: How a refusal journey asks for the report rather than the wait. Empty on a pin below
#: the floor, because the release it would name has no pacing settings at all and asking
#: for one is refused before the copy starts — which is itself asserted below.
_REPORT_PROMPTLY: dict[str, str] = {} if held_below_the_pacing_floor() else {"retry_budget_ms": "0"}


def _refusal_sentence(result: subprocess.CompletedProcess[str]) -> str:
    """The one line of a refused copy that says what went wrong.

    The recipe prints the commands it runs and `just` prints its own failure after, so
    a comparison over whole stderr would be comparing the wrapper's noise as much as
    the store's answer.
    """
    said = [line for line in result.stderr.splitlines() if line.startswith("onetaskgraph:")]
    assert said, f"a refused copy has to say why on stderr: {result.stderr}"
    return said[0]


def _copy_to_the_board(
    root: Path, remote: dict[str, str], **pacing: str
) -> subprocess.CompletedProcess[str]:
    """Copy this journey's local project onto the fixture board, under some pacing.

    One helper because the journeys below differ only in what the fixture answers and
    how fast the source is allowed to write; the copy itself is the same one an
    operator performs with `just copy-plan`, run here at the store verb so what is
    measured is the source's own request stream.
    """
    environment = _plan_environment(root)
    environment.update(remote)
    for setting, value in pacing.items():
        environment[f"ONETASKGRAPH_SOURCES__PLANS__CONFIG__PACING__{setting.upper()}"] = value
    # llmlint: ignore-block[tests_mirror_real_usage] `just plans` is an operator recipe,
    # not a private seam below one: `just copy-plan` adds a review pre-flight and then
    # runs this very verb with its streams uncaptured, so what an operator reads is
    # identical, and its own journey is `tests/plan_tooling/test_copy_plan_recipe_e2e.py`.
    return subprocess.run(
        ["just", "plans", "project", "copy", LOCAL_QUALIFIED, "--to", "plans"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    # llmlint: ignore-end[tests_mirror_real_usage]


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] Same subject and same
# inputs as every journey beside them, so `nx affected` already selects the whole file
# together and a project of two functions would buy no selection — the split-by-cost
# shape this rule is named against. They run in `orchestrator:test`, keyed `codeWorkspace`.
# llmlint: ignore-block[shell_test_tiers_stay_split] Neither is a shell test: both are
# Python journeys spawning the installed plan-store CLI, so there is no shell suite here
# to keep split from anything.
def test_a_secondary_rate_limit_is_told_apart_from_a_credential_this_token_lacks(
    tmp_path: Path,
) -> None:
    """The installed CLI's answer to GitHub's burst limiter, whichever release is pinned.

    Both halves send the same forbidden status and differ only in what the body says,
    which is the whole of the distinction: the copies this host runs were being refused
    by the secondary limiter and reported as a permission problem, so an operator went
    and widened a token that was never the trouble while every retry extended the
    refusal. Driven against the binary this checkout installs rather than read off a
    release note, because the question is about the program this checkout spawns.

    On a pin below :data:`PACING_FLOOR` the two answers are the *same* sentence, and
    asserting that is what would keep such a pin honest — a note saying this host still
    reports a limiter as a credential would otherwise outlive the release that stopped
    it. This host is at that floor, so that branch is not taken and the assertions
    below it are the measurement.
    """
    _write_local_project(tmp_path)
    with _serving_board(refusal=SECONDARY_LIMIT) as remote:
        limited = _copy_to_the_board(tmp_path, remote, **_REPORT_PROMPTLY)
    with _serving_board(refusal=FORBIDDEN) as remote:
        forbidden = _copy_to_the_board(tmp_path, remote, **_REPORT_PROMPTLY)

    assert limited.returncode != 0, limited.stdout
    assert forbidden.returncode != 0, forbidden.stdout
    if held_below_the_pacing_floor():
        assert _refusal_sentence(limited) == _refusal_sentence(forbidden), (
            f"onetaskgraph {adopted_release()} is below the {PACING_FLOOR} that tells "
            "these two apart, so both have to come back as the same credential "
            "sentence; one of them naming a limiter means this pin has reached that "
            "floor and the branch below is the measurement to keep"
        )
        assert "credential" in _refusal_sentence(limited), (
            "a release below the floor reports a burst-limiter refusal as a credential "
            f"problem, and this one said: {_refusal_sentence(limited)}"
        )
        return

    said = limited.stderr.lower()
    assert "secondary rate limit" in said, (
        "a burst-limiter refusal has to name that limiter, or an operator reads it as a "
        f"credential problem: {limited.stderr}"
    )
    assert "pacing.min_mutation_interval_ms" in limited.stderr, (
        "the report has to name the setting that answers this limiter, since polling it "
        f"only extends it: {limited.stderr}"
    )
    assert "rate limit" not in forbidden.stderr.lower(), (
        "a forbidden status saying nothing about a limit is a permission this token "
        f"lacks, and reporting it as a rate limit is the opposite error: {forbidden.stderr}"
    )


def test_a_copy_paces_its_content_creating_mutations(tmp_path: Path) -> None:
    """How fast a copy writes, measured at the far end of the wire.

    A gap between two mutations is only observable where they land, so the fixture is
    what times them; the source's own scheduling is invisible from outside it.

    On a pin below :data:`PACING_FLOOR` what is asserted is the burst — every mutation
    of a copy inside a few milliseconds — and that the pacing setting which would answer
    for it is not a field such a release has. At or past the floor, which is where this
    host is, the shipped interval is asserted instead, read as GitHub's own published
    ceiling on content-generating requests rather than as a number chosen here, with an
    unpaced run as the control: without one a loaded test host would satisfy the paced
    assertion on its own.
    """
    _write_local_project(tmp_path)
    with _serving_board() as remote:
        shipped = _copy_to_the_board(tmp_path, remote)
        as_shipped = _mutation_gaps(_GitHubFixture.requests)

    assert shipped.returncode == 0, shipped.stdout + shipped.stderr
    assert len(as_shipped) >= 2, (
        "a copy of one project and one task sends several content-creating mutations; "
        f"this sent {len(as_shipped) + 1}"
    )

    if held_below_the_pacing_floor():
        assert max(as_shipped) < SHIPPED_MUTATION_INTERVAL / 2, (
            f"onetaskgraph {adopted_release()} sends a copy's mutations as one burst, "
            f"and this one left gaps of up to {max(as_shipped):.3f}s — if it is pacing "
            f"them, the {PACING_FLOOR} behaviour has arrived and the branch below is "
            "the measurement to keep"
        )
        with _serving_board() as remote:
            configured = _copy_to_the_board(tmp_path, remote, min_mutation_interval_ms="0")
        assert configured.returncode != 0, configured.stdout
        assert "'pacing' was unexpected" in configured.stderr, (
            "a release below the floor has no pacing settings at all, so asking for one "
            f"is refused as an unknown property; this said: {configured.stderr}"
        )
        return

    with _serving_board() as remote:
        unpaced = _copy_to_the_board(tmp_path, remote, min_mutation_interval_ms="0")
        burst = _mutation_gaps(_GitHubFixture.requests)

    assert unpaced.returncode == 0, unpaced.stdout + unpaced.stderr
    assert min(burst) < SHIPPED_MUTATION_INTERVAL / 2, (
        "the control has to burst, or the paced assertion below would pass on a slow "
        f"host alone; its shortest gap was {min(burst):.3f}s"
    )
    assert min(as_shipped) >= SHIPPED_MUTATION_INTERVAL * 0.9, (
        "every content-creating mutation has to be spaced by the shipped interval; the "
        f"shortest gap this copy left was {min(as_shipped):.3f}s of "
        f"{SHIPPED_MUTATION_INTERVAL}s"
    )


# llmlint: ignore-end[shell_test_tiers_stay_split]
# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]


def _emitted_schema(root: str) -> Mapping[str, object]:
    """One root of the schema bundle the provisioned CLI emits about its own output.

    Read from the running binary rather than kept here: that bundle is generated from the
    types the CLI serialises, so a document validated against it is validated against the
    release this checkout installed, and a copy of it in this file would drift from the
    next one in silence.
    """
    emitted = subprocess.run(
        [str(ONETASKGRAPH_BIN), "schema"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert emitted.returncode == 0, emitted.stderr
    roots = json.loads(emitted.stdout)["roots"]
    assert root in roots, f"onetaskgraph {ADOPTED} emits no {root} schema: {sorted(roots)}"
    schema = roots[root]
    assert isinstance(schema, dict)
    return schema


# llmlint: ignore-block[modern_domain_modeling] These documents' shape has one source, the
# schema the installed CLI emits, and every read here is validated against it. A typed
# restatement in this file is a second copy of that contract, which
# `contracts_have_one_source_or_a_drift_gate` refused at this site twice, key gate and all.
def _conforming(document: str, root: str) -> dict[str, object]:
    parsed = json.loads(document)
    jsonschema.validate(parsed, _emitted_schema(root))
    assert isinstance(parsed, dict)
    return parsed


# llmlint: ignore-end[modern_domain_modeling]


#: The project the member journey copies, and its two tasks: the one a `--member` copy
#: names and the one it leaves out.
MEMBERS_PROJECT = _ProjectId("members")
NAMED_MEMBER = "named"
UNNAMED_MEMBER = "unnamed"
#: The second committed `local-md` source, which the member journey copies into. Pointed
#: at a directory of the journey's own for the reason the authoring root is.
EXAMPLES_SOURCE = "examples"
EXAMPLES_ROOT_ENV = f"ONETASKGRAPH_SOURCES__{EXAMPLES_SOURCE.upper()}__CONFIG__ROOT"


def _write_members_project(root: Path, revision: str) -> None:
    plan = PlanDocument(
        name=MEMBERS_PROJECT,
        tasks=[
            PlanNode(
                id=member,
                title=f"feat: {member} member",
                task=f"## What\nThe {member} member, {revision}.\n",
            )
            for member in (NAMED_MEMBER, UNNAMED_MEMBER)
        ],
    )
    write_plan_project(root, plan)


def _plans_json(environment: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    """One plan-store verb under `--json`, run as an operator runs it: `just plans`."""
    return subprocess.run(
        ["just", "plans", *args, "--json"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


# llmlint: ignore-block[shell_test_tiers_stay_split] None of these three is a shell test: each
# is a Python journey spawning the installed plan-store CLI, as every journey beside it is,
# and this repository splits its test tiers by pytest marker rather than by Nx project — the
# block above `test_adopted_archive_binary_and_authoring_ignore_are_in_force` gives why.
def test_a_member_copy_writes_the_named_member_and_leaves_the_rest_untouched(
    tmp_path: Path,
) -> None:
    """`project copy --member` between the two committed `local-md` sources.

    The write-back projects one settled node at a time, and what makes that cheap is a
    copy that reads and writes the project and that node alone. So the unnamed member is
    given a change of its own before the member copy runs: a copy that still walked every
    task would carry it into the destination, and its record staying byte-for-byte what
    the first copy wrote is what says it was not written.

    A local source meters no requests, so its report carries no `spent` at all — absent
    rather than zero, which is what tells a caller nothing was metered from a copy that
    cost nothing.
    """
    authoring, examples = tmp_path / "authoring", tmp_path / "examples"
    authoring.mkdir()
    examples.mkdir()
    environment = _plan_environment(authoring)
    environment[EXAMPLES_ROOT_ENV] = str(examples)
    project = f"{AUTHORING_SOURCE}:{MEMBERS_PROJECT}"

    _write_members_project(authoring, "first revision")
    whole = _plans_json(environment, "project", "copy", project, "--to", EXAMPLES_SOURCE)
    assert whole.returncode == 0, whole.stdout + whole.stderr
    unnamed_record = next((examples / "tasks").rglob(f"{UNNAMED_MEMBER}.md"))
    named_record = next((examples / "tasks").rglob(f"{NAMED_MEMBER}.md"))
    unnamed_before = unnamed_record.read_bytes()

    _write_members_project(authoring, "second revision")
    member = _plans_json(
        environment,
        *("project", "copy", project, "--to", EXAMPLES_SOURCE),
        *("--member", f"{project}/{NAMED_MEMBER}"),
    )

    assert member.returncode == 0, member.stdout + member.stderr
    report = _conforming(member.stdout, "CopyReport")
    items = report["items"]
    assert isinstance(items, list)
    assert [item["source"] for item in items] == [project, f"{project}/{NAMED_MEMBER}"], (
        f"a member copy reports the project and the member it names, and nothing else: {items}"
    )
    assert "second revision" in named_record.read_text(encoding="utf-8"), (
        "the named member's change did not reach its destination record"
    )
    unnamed_source = next((authoring / "tasks").rglob(f"{UNNAMED_MEMBER}.md"))
    assert "second revision" in unnamed_source.read_text(encoding="utf-8"), (
        "the unnamed member has no pending change to leave behind"
    )
    assert unnamed_record.read_bytes() == unnamed_before, (
        "a member copy rewrote the destination record of a task it did not name"
    )
    assert "spent" not in report, (
        f"a copy between two local sources meters nothing, so it reports no `spent`: {report}"
    )


def test_a_refused_verb_under_json_writes_a_failure_document(tmp_path: Path) -> None:
    """What a caller reads off stdout when the store refuses, instead of parsing stderr.

    The write-back decides whether to try again from this document's `class`: `refused`
    is an answer repeating the request cannot change, which is the one a caller must not
    spend another attempt on.
    """
    environment = _plan_environment(tmp_path)
    refused = _plans_json(environment, "task", "show", f"{AUTHORING_SOURCE}:absent/task")

    assert refused.returncode == 1, refused.stdout + refused.stderr
    failure = _conforming(refused.stdout, "FailureDocument")["failure"]
    assert isinstance(failure, dict)
    assert failure["class"] == "refused", (
        f"a task the source does not hold is a refusal, and this said {failure}"
    )


# llmlint: ignore-block[e2e_not_mocked] The one boundary doubled is GitHub's Projects API, for
# the reason the block around `_Board` above gives; the installed `onetaskgraph`, `just
# plans` and the committed `plans` source are real, and `spent` is read off their stdout.
def test_a_copy_onto_the_board_reports_what_it_spent(tmp_path: Path) -> None:
    """A board copy says how many requests it sent and what they cost the GraphQL budget.

    Held to the requests the fixture board actually served for this one command, because
    a report that estimated its own count would read as an answer while saying nothing
    about the allowance this host keeps running out of.
    """
    _write_local_project(tmp_path)
    with _serving_board() as remote:
        environment = _plan_environment(tmp_path)
        environment.update(remote)
        copied = _plans_json(environment, "project", "copy", LOCAL_QUALIFIED, "--to", "plans")
        served = len(_GitHubFixture.requests)

    assert copied.returncode == 0, copied.stdout + copied.stderr
    report = _conforming(copied.stdout, "CopyReport")
    spent = report.get("spent")
    assert isinstance(spent, dict), f"a board copy has to report what it spent: {report}"
    assert spent["requests"] == served, (
        f"the copy reported {spent['requests']} requests where the board served {served}"
    )
    budgets = spent["budgets"]
    assert isinstance(budgets, list)
    assert any(entry["budget"] == "graphql" for entry in budgets), (
        f"a board copy spends the GraphQL budget, and its report names only {budgets}"
    )


# llmlint: ignore-end[e2e_not_mocked]
# llmlint: ignore-end[shell_test_tiers_stay_split]


class _ProjectedStatus(StrEnum):
    """Every word onepipeline's settlement write-back projects a node's state onto.

    A copy of the engine's declaration rather than a read of it, so the journey below
    needs no checkout; the check after it is what holds the copy to `ProjectedStatus` at
    the release this host pins.
    """

    TODO = "todo"
    IN_PROGRESS = "in progress"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"
    PROVIDER_FAILED = "provider-failed"
    PARKED = "parked"
    SKIPPED = "skipped"


#: The words onetaskgraph's category vocabulary lacks, so each classifies `unknown` —
#: which the `plans` board refused outright until `onetaskgraph.yaml` gave it an option.
OUTSIDER_STATUSES = frozenset(
    {
        _ProjectedStatus.FAILED,
        _ProjectedStatus.PROVIDER_FAILED,
        _ProjectedStatus.PARKED,
        _ProjectedStatus.SKIPPED,
    }
)
#: The spelling an ambient override of the `plans` source's mapping would take, removed
#: from the journey's environment so the committed file is the mapping applied.
STATUS_MAPPING_ENV_PREFIX = "ONETASKGRAPH_SOURCES__PLANS__CONFIG__STATUS_MAPPING"


def _write_projected_project(root: Path) -> dict[str, _ProjectedStatus]:
    """Write one local task per projected word, returning each task's title and word.

    Rendered through the store writer every plan goes through and then given its word,
    because that writer files every task `todo`; the word is written the way the
    write-back writes it, as the record's own `status`.
    """
    titled = {f"projected {status.value}": status for status in _ProjectedStatus}
    plan = PlanDocument(
        name=LOCAL_PROJECT,
        tasks=[
            PlanNode(id=status.value.replace(" ", "-"), title=title)
            for title, status in titled.items()
        ],
    )
    write_plan_project(root, plan)
    for task in (root / "tasks" / LOCAL_PROJECT).glob("*.md"):
        text = task.read_text(encoding="utf-8")
        title = next(t for t in titled if f"title: {json.dumps(t)}\n" in text)
        word = json.dumps(titled[title].value)
        task.write_text(text.replace('status: "todo"\n', f"status: {word}\n", 1), encoding="utf-8")
    return titled


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] Same subject and same
# inputs as every journey beside it — the installed plan-store CLI driven against the
# loopback board — so `nx affected` already selects this module together, and a project of
# one function would buy no selection. It runs in `orchestrator:test`, keyed `codeWorkspace`.
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] Same site, same reason:
# the edge it pays is the one every other CLI-spawning journey in this file already pays.
def test_every_word_the_write_back_projects_outside_the_categories_reaches_the_board(
    tmp_path: Path,
) -> None:
    """A failed, parked or skipped node is filed under `Needs attention`, never refused.

    The committed `onetaskgraph.yaml` is what is exercised: the copy runs from this
    checkout with only the endpoint and credential pointed at the fixture, and any
    ambient `status_mapping` override is removed so the mapping written there is the one
    applied. A copy writes a task's status through the same classification and mapping a
    settlement write-back does, so each word is fed as a task's status, and what is read
    is the Status option each task's board row is set to.

    The category words ride along as the control for the one property a single option
    for `unknown` could break: that no option is written for two categories.
    """
    titled = _write_projected_project(tmp_path)
    environment = _plan_environment(tmp_path)
    for name in [name for name in environment if name.startswith(STATUS_MAPPING_ENV_PREFIX)]:
        del environment[name]
    # llmlint: ignore[e2e_not_mocked] The live `plans` board is the one boundary this
    # journey must not reach: a write to it lands on the board holding this repository's
    # real plans and needs a credential no test may use. What is doubled stops at the wire
    # — the recipe, the installed CLI and the committed configuration are real — for the
    # reason the fixture's own directive gives.
    with _serving_board() as remote:
        environment.update(remote)
        environment["ONETASKGRAPH_SOURCES__PLANS__CONFIG__PACING__MIN_MUTATION_INTERVAL_MS"] = "0"
        copied = subprocess.run(
            ["just", "plans", "project", "copy", LOCAL_QUALIFIED, "--to", "plans"],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    assert "is disabled for source plans" not in copied.stderr, copied.stderr
    assert copied.returncode == 0, copied.stdout + copied.stderr
    word_of_item = {
        issue.item_id: titled[issue.title] for issue in BOARD.created if issue.title in titled
    }
    assert set(word_of_item.values()) == set(_ProjectedStatus), (
        f"the copy has to file one issue per projected word, and it filed {word_of_item}"
    )
    option_names = {option.id: option.name for option in STATUS_OPTIONS}
    written: dict[str, set[_ProjectedStatus]] = {}
    for request in _GitHubFixture.requests:
        if request.operation is not _Operation.UPDATE_FIELD:
            continue
        if request.input_value("fieldId") != STATUS_FIELD_ID:
            continue
        word = word_of_item.get(_BoardItemId(str(request.input_value("itemId"))))
        if word is None:
            continue
        value = request.input_value("value")
        assert isinstance(value, dict), value
        option = option_names[_FieldOptionId(str(value.get("singleSelectOptionId")))]
        written.setdefault(option, set()).add(word)

    assert written.get(NEEDS_ATTENTION.name, set()) == set(OUTSIDER_STATUSES), (
        f"every word outside the category vocabulary has to be written as the "
        f"{NEEDS_ATTENTION.name!r} option and no category word with them; the copy wrote {written}"
    )
    shared = {option: words for option, words in written.items() if len(words) > 1}
    assert shared.keys() == {NEEDS_ATTENTION.name}, (
        f"only `unknown`'s words may share an option, and the copy wrote {written}"
    )


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]


ONEPIPELINE_IDENTITY = "github.com/nickderobertis/onepipeline"
#: `ProjectedStatus` in onepipeline's `src/writeback.rs`, its variants, and each one's
#: serde rename, which is the word the write-back sends.
PROJECTED_STATUS_DECLARATION = re.compile(r"\nenum ProjectedStatus \{(?P<body>.*?)\n\}", re.DOTALL)
PROJECTED_STATUS_VARIANT = re.compile(r"^\s*([A-Z]\w*),\s*$", re.MULTILINE)
PROJECTED_STATUS_WORD = re.compile(r'#\[serde\(rename = "([^"]+)"\)\]')


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] `reads_checkouts` is not a
# narrower tier of a memoized one: it moves this check into the uncached
# `orchestrator:test-checkouts`, because its subject — onepipeline's source at the pinned
# release — is outside this workspace and no `nx.json` key could name it.
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] Same site, same reason: a
# project edge of its own is what earns a memo, and a memo is what this check must not have.
@pytest.mark.reads_checkouts
def test_the_projected_words_the_board_journey_feeds_are_the_engines_own() -> None:
    """`_ProjectedStatus` is onepipeline's `ProjectedStatus`, word for word.

    Read at the release `config/onepipeline.version` pins, because that is the engine a
    run's write-back comes from; a word the engine adds, drops or respells would
    otherwise leave the journey above proving the mapping for a vocabulary nothing sends.
    A failure rather than a skip when no checkout is held, for the reason
    `tests/test_engine_contracts.py` gives: a drift gate that reconciles nothing passes.
    """
    checkout = registered_checkouts().get(ONEPIPELINE_IDENTITY)
    assert checkout is not None, (
        f"no registered checkout of {ONEPIPELINE_IDENTITY} on this host; clone it into a "
        "path config/onevcs.checkouts lists"
    )
    pinned = f"v{(REPO_ROOT / 'config' / 'onepipeline.version').read_text('utf-8').strip()}"
    shown = subprocess.run(
        ["git", "-C", str(checkout), "show", f"{pinned}:src/writeback.rs"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert shown.returncode == 0, shown.stderr
    declaration = PROJECTED_STATUS_DECLARATION.search(shown.stdout)
    assert declaration is not None, (
        f"onepipeline {pinned} no longer declares `enum ProjectedStatus` in src/writeback.rs"
    )
    variants = PROJECTED_STATUS_VARIANT.findall(declaration.group("body"))
    declared = PROJECTED_STATUS_WORD.findall(declaration.group("body"))
    assert variants and len(declared) == len(variants), (
        f"onepipeline {pinned} declares variants {variants} with renames {declared}; every "
        "variant has to carry the rename this check reads its word from"
    )
    copied = {status.value for status in _ProjectedStatus}
    assert sorted(declared) == sorted(copied), (
        f"onepipeline {pinned} projects {sorted(declared)}, and _ProjectedStatus holds "
        f"{sorted(copied)}: missing {sorted(set(declared) - copied)}, "
        f"invented {sorted(copied - set(declared))}"
    )


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]


def test_missing_remote_credential_keeps_local_plan_launchable(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A missing remote token costs `plans`, not a local project's execution."""
    _write_local_project(tmp_path)
    environment = _plan_environment(tmp_path)
    environment.pop("GH_PROJECTS_TOKEN", None)
    result = subprocess.run(
        ["just", "plans", "project", "list", "--allow-partial", "--json"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "launch" in [item["item"]["title"] for item in payload["items"]]
    assert len(payload["errors"]) == 1
    error = payload["errors"][0]
    assert error["source"] == "plans"
    assert error["error"]["kind"] == "auth"
    assert "GH_PROJECTS_TOKEN" in error["error"]["message"]

    launched_environment = _launch_environment(tmp_path / "execution", oneharness_bin)
    launched_environment.update(
        {
            "ONETASKGRAPH_SECRETS_FILE": environment["ONETASKGRAPH_SECRETS_FILE"],
            plan_root_variable.name(): str(tmp_path),
            PROMPT_LOG_ENV: str(tmp_path / "turns.jsonl"),
        }
    )
    launched_environment.pop("GH_PROJECTS_TOKEN", None)
    launched = subprocess.run(
        [
            "just",
            "orchestrate",
            LOCAL_QUALIFIED,
            "--dag-graph",
            "off",
        ],
        cwd=REPO_ROOT,
        env=launched_environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert (tmp_path / "turns.jsonl").is_file(), "the stored plan reached no execution turn"


#: What `onepipeline`'s settlement write-back prints when it cannot read the payload
#: this checkout's plan store answers with. The engine spawns `onetaskgraph project
#: show` and deserializes its answer strictly, so the `location` onetaskgraph reports
#: for every entity is refused by name — https://github.com/nickderobertis/onepipeline/issues/179.
#: Matched on the engine's own words rather than on a version comparison, because what
#: the journeys below are exempt for is the defect and not a release number: an engine
#: that can read this store never prints this, whatever it is numbered.
_WRITE_BACK_REFUSED_THE_STORE = re.compile(
    r"onetaskgraph write-back failed for .*: unknown field `location`"
)


def _engine_cannot_read_this_store(*launches: subprocess.CompletedProcess[str]) -> bool:
    """Report whether a launch's own driver said it could not read the plan store.

    Kept apart from the exemption below so that the condition — the whole of what
    decides when these journeys measure the projection again — is a plain function a
    test can drive, rather than something only observable by a journey exempting
    itself.
    """
    return any(_WRITE_BACK_REFUSED_THE_STORE.search(launch.stderr) for launch in launches)


def _exempt_while_the_engine_cannot_read_this_store(
    *launches: subprocess.CompletedProcess[str],
) -> None:
    """Stop a write-back journey the installed engine has already refused to perform.

    The two journeys that call this measure what a settlement projects back onto the
    plan it was launched from, and under onepipeline 0.18.4 against onetaskgraph 0.2.18
    no settlement is projected at all: the engine refuses the store's payload for its
    `location` field, leaves the destination exactly as it was, and says so on the
    driver's own stderr. That is an external released binary and not this repository's
    to patch, so these journeys are exempt rather than failing the deterministic tier —
    and exempt *only* while the engine says it cannot read the store, so the day an
    engine carrying the fix is adopted here they measure the projection again with
    nobody having to remember to re-enable them.

    Read off the refusal rather than off `config/onepipeline.version` deliberately: a
    pin comparison would have to be widened by hand at the fixing release and would go
    on exempting these journeys if that guess were wrong, where the engine's own
    sentence is the thing that actually stops being printed.
    """
    if _engine_cannot_read_this_store(*launches):
        pytest.xfail(
            "the installed onepipeline refused this store's `project show` payload "
            "for its `location` field, so no settlement was projected back — "
            "https://github.com/nickderobertis/onepipeline/issues/179"
        )


#: One driver stream, verbatim from an attached `just orchestrate` on this checkout,
#: carrying the refusal the two journeys below are exempt for. Quoted from the producer
#: rather than paraphrased, so a message that moves is a failing check here.
_REFUSING_DRIVER_STREAM = """\
2026-09-01T13:34:00.807Z  graph:-                      run-started
-- launch  0/1 done  ACTIVE  waiting
onetaskgraph write-back failed for 'authoring:launch': unknown field `location`, \
expected one of `title`, `content`, `labels`, `metadata`, `id`, `status`, `url`, \
`created_at`, `updated_at`, `repositories` at line 15 column 18; retrying
2026-09-01T13:34:01.116Z  graph:probe                  node-settled done
-- launch  1/1 done  SETTLED  complete
"""

#: The same stream from a driver that projected the settlement — every line of the one
#: above that is not the refusal. A condition that fired on this would exempt the two
#: journeys for good rather than until the engine is fixed.
_PROJECTING_DRIVER_STREAM = "\n".join(
    line
    for line in _REFUSING_DRIVER_STREAM.splitlines()
    if "onetaskgraph write-back failed" not in line
)


def _driver(stream: str) -> subprocess.CompletedProcess[str]:
    """One finished launch, as the journeys below capture it."""
    return subprocess.CompletedProcess(args=["just", "orchestrate"], returncode=0, stderr=stream)


def test_the_write_back_exemption_lasts_only_while_the_engine_refuses_this_store() -> None:
    """The two journeys below come back the moment a reading engine is installed.

    Their exemption is the one thing between a defect upstream and a suite that has
    quietly stopped measuring the settlement projection, so what it is conditioned on
    is asserted rather than assumed: it holds for a driver that said it could not read
    the store, and it does not hold for one that said nothing of the kind.
    """
    assert _engine_cannot_read_this_store(_driver(_REFUSING_DRIVER_STREAM))
    assert not _engine_cannot_read_this_store(_driver(_PROJECTING_DRIVER_STREAM))
    assert not _engine_cannot_read_this_store(_driver(_PROJECTING_DRIVER_STREAM), _driver(""))
    assert _engine_cannot_read_this_store(
        _driver(_PROJECTING_DRIVER_STREAM), _driver(_REFUSING_DRIVER_STREAM)
    )


#: The accepted field list out of the engine's own refusal. serde names every field it
#: would have taken after `expected one of`, and stops that list at the position report
#: — so the whole set is readable from one refusal, where the unknown field it names is
#: only ever the first one it met.
_ACCEPTED_BY_THE_WRITE_BACK = re.compile(r"expected one of (?P<fields>`[^\n]*?`) at line")


def _fields_the_write_back_accepts(stderr: str) -> set[str] | None:
    """Every field name the installed engine says it would have accepted, or `None`.

    `None` is a driver that refused nothing, which is the answer an engine reading this
    store gives and is why this returns rather than raising: there is no list to read
    because there was no refusal, and that is the outcome this repository is waiting
    for rather than a parse failure.
    """
    named = _ACCEPTED_BY_THE_WRITE_BACK.search(stderr)
    if named is None:
        return None
    return set(re.findall(r"`([^`]+)`", named.group("fields")))


def _fields_the_store_answers_with(body: str, project_id: _ProjectId) -> set[str]:
    """Every key the store puts on one project item, read off its own `--json` answer."""
    payload = json.loads(body)
    for entry in payload["items"]:
        item = entry["item"]
        if item.get("id") == project_id:
            return set(item)
    raise ValueError(f"the store answered with no project {project_id!r}")


def test_the_store_answers_with_exactly_one_field_the_write_back_does_not_accept(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """How far apart the two shapes are, read from both ends rather than inferred.

    The engine deserializes the store's project item under `deny_unknown_fields`, so it
    refuses at the **first** field it does not recognise and names only that one. A
    reader who took the refusal at face value would learn that `location` is unaccepted
    and nothing at all about what stands behind it — and would go on believing the two
    shapes are one field apart while a bump that ended `location` uncovered the next.
    So neither end is inferred from the other: the field set is read off the store's own
    `--json` answer, the accepted set off the `expected one of` list the engine's own
    refusal carries, and the two are compared whole.

    An engine that reads this store refuses nothing and prints no list, which is the
    stronger answer and the one this asserts instead — see
    `_exempt_while_the_engine_cannot_read_this_store` for what ends the refusal.
    """
    _write_local_project(tmp_path)
    environment = _launch_environment(tmp_path / "execution", oneharness_bin)
    environment.update(_prepare_plan_sources(tmp_path))
    environment[PROMPT_LOG_ENV] = str(tmp_path / "turns.jsonl")

    shown = subprocess.run(
        ["just", "plans", "project", "show", LOCAL_QUALIFIED, "--json"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert shown.returncode == 0, shown.stdout + shown.stderr
    answered = _fields_the_store_answers_with(shown.stdout, LOCAL_PROJECT)

    launched = subprocess.run(
        ["just", "orchestrate", LOCAL_QUALIFIED, "--dag-graph", "off"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert launched.returncode == 0, launched.stdout + launched.stderr

    accepted = _fields_the_write_back_accepts(launched.stderr)
    if accepted is None:
        assert not _engine_cannot_read_this_store(launched), (
            "the driver said it could not read this store but named no accepted field "
            f"list, so neither shape can be read from it: {launched.stderr}"
        )
        return

    assert answered - accepted == {"location"}, (
        "the store answers with a field the write-back does not accept beyond "
        f"`location`: {sorted(answered - accepted)} — every one of those refuses a "
        "projection, and a bump that ends `location` alone would uncover the next"
    )
    assert accepted - answered == set(), (
        "the write-back accepts a field this store never answers with: "
        f"{sorted(accepted - answered)}; the two shapes have parted in the other "
        "direction and the refusal above is no longer the whole story"
    )


def test_run_settlements_and_live_edits_reach_the_plan_store(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The real launch writes both execution outcome and a graph addition to its source.

    Exempt while the installed engine cannot read this store at all; see
    `_exempt_while_the_engine_cannot_read_this_store` for what that means and when it
    stops applying.
    """
    _write_local_project(tmp_path)
    environment = _launch_environment(tmp_path / "execution", oneharness_bin)
    environment.update(_prepare_plan_sources(tmp_path))
    environment[PROMPT_LOG_ENV] = str(tmp_path / "turns.jsonl")

    launched = subprocess.run(
        ["just", "orchestrate", LOCAL_QUALIFIED, "--dag-graph", "off"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert launched.returncode == 0, launched.stdout + launched.stderr

    live_edit: _LiveEditEnvelope = {
        "version": 2,
        "commands": [
            {
                "op": "add",
                "node": {
                    "id": _NodeId("record-follow-up"),
                    "task": "Record this follow-up without dispatching.",
                    "expects_no_diff": True,
                },
            }
        ],
    }
    edited = subprocess.run(
        ["just", "channel-reply", "launch"],
        cwd=REPO_ROOT,
        env=environment,
        input=json.dumps(live_edit),
        text=True,
        capture_output=True,
        check=False,
    )
    assert edited.returncode == 0, edited.stdout + edited.stderr

    adopted = subprocess.run(
        ["just", "orchestrate", "--adopt", "launch"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert adopted.returncode == 0, adopted.stdout + adopted.stderr
    _exempt_while_the_engine_cannot_read_this_store(launched, adopted)

    stored = subprocess.run(
        ["just", "plans", "task", "list", "--project", LOCAL_QUALIFIED, "--json"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert stored.returncode == 0, stored.stdout + stored.stderr
    by_pipeline_id = {task.pipeline_id: task for task in _stored_tasks(stored.stdout)}
    assert set(by_pipeline_id) == {"probe", "record-follow-up"}
    assert by_pipeline_id["probe"].settlement == _Settlement(status="done", outcome=None)
    added_settlement = by_pipeline_id["record-follow-up"].settlement
    assert added_settlement.status == "done"
    assert added_settlement.outcome == "no-changes"


#: What a person writes on a board and nothing in a run has any business touching.
#: Deliberately several lines with its own shape, so a comparison that survives it
#: is comparing content rather than emptiness.
AUTHORED_PROJECT_BODY = """Why this board exists, in the operator's own words.

Read the constraint before launching anything: the second dispatch may not start
until the first has landed, and nothing here is safe to re-run twice.
"""


def _author_project_body(root: Path, project: _ProjectId, body: str) -> None:
    """Replace one stored project's description with prose a person wrote.

    Written onto the record rather than through a recipe because this repository has
    no verb that edits a project body — the board is where an operator writes one —
    and what the journey needs is only that the store holds it before the run.
    """
    record = root / "projects" / f"{project}.md"
    frontmatter, _, _ = record.read_text(encoding="utf-8").rpartition("---\n")
    record.write_text(f"{frontmatter}---\n\n{body}", encoding="utf-8")


def test_settlement_write_back_preserves_the_authored_project_description(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A settled run leaves the project body its operator wrote exactly as written.

    The projection a settlement writes back does not own the project description, and
    the release below the adopted one nonetheless replaced it with an empty string:
    the write-back built its shadow project with the body hardcoded empty, and the
    copy that followed is a total replace by contract, so every destination faithfully
    propagated the deletion. Task bodies survived, which is what let it ship.

    Asserted as a *comparison* of the content before and against after, never as the
    content being present: an assertion that the description is non-empty, or that the
    project is still readable, cannot fail on a deletion — and that assertion shape is
    exactly how this reached a release. The settlement is asserted beside it, because
    a run whose projection never reached the store would preserve the description by
    doing nothing at all.

    Exempt while the installed engine cannot read this store at all; see
    `_exempt_while_the_engine_cannot_read_this_store` for what that means and when it
    stops applying.
    """
    _write_local_project(tmp_path)
    _author_project_body(tmp_path, LOCAL_PROJECT, AUTHORED_PROJECT_BODY)
    environment = _launch_environment(tmp_path / "execution", oneharness_bin)
    environment.update(_prepare_plan_sources(tmp_path))
    environment[PROMPT_LOG_ENV] = str(tmp_path / "turns.jsonl")

    def _show_project() -> str | None:
        shown = subprocess.run(
            ["just", "plans", "project", "show", LOCAL_QUALIFIED, "--json"],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert shown.returncode == 0, shown.stdout + shown.stderr
        return _stored_project_content(shown.stdout, LOCAL_PROJECT)

    before = _show_project()
    assert before == AUTHORED_PROJECT_BODY.strip(), (
        "the journey has to start from a description the store really holds, and this "
        f"one reads {before!r}"
    )

    launched = subprocess.run(
        ["just", "orchestrate", LOCAL_QUALIFIED, "--dag-graph", "off"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert launched.returncode == 0, launched.stdout + launched.stderr
    _exempt_while_the_engine_cannot_read_this_store(launched)

    stored = subprocess.run(
        ["just", "plans", "task", "list", "--project", LOCAL_QUALIFIED, "--json"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert stored.returncode == 0, stored.stdout + stored.stderr
    settled = {task.pipeline_id: task.settlement for task in _stored_tasks(stored.stdout)}
    assert settled == {"probe": _Settlement(status="done", outcome=None)}, (
        "the write-back has to have reached this project for its preservation to be "
        f"the thing under test, and the store reports {settled}"
    )

    after = _show_project()
    assert after == before, (
        f"the settlement rewrote the project description its operator authored to "
        f"{after!r}; the write-back owns the node projection and nothing else on that "
        "record"
    )


#: The `project show` invocations that pass through the fault injector untouched. One:
#: the launch's own read of the plan it is about to run, which has to succeed or there
#: is no settlement to measure. Every read after it belongs to the write-back worker.
LAUNCH_PLAN_READS = 1


# llmlint: ignore-block[e2e_not_mocked, tests_mirror_real_usage] A partial read is a
# real condition of the real surface — the sibling journey above asserts one that a
# missing `GH_PROJECTS_TOKEN` produces — but it cannot be arranged *at the write-back's
# own read and not at the launch's*, because the two are the same command against the
# same source and a store broken for real breaks the run being measured. So this
# substitutes the one delegated published CLI at `ONETASKGRAPH_BIN`, the seam
# `onepipeline` spawns it through, and nothing above it: the recipe, the wrapper, the
# engine, its write-back worker and the store on disk all stay real, `just plans` reads
# the result through the installed binary because that recipe never consults this
# variable, and the injector runs the real CLI for every call and adds one `errors`
# entry to the answer it actually returned.
def _injected_partial_read(log: Path) -> dict[str, str]:
    """Point `onepipeline`'s plan-store calls at the fault injector, and log them."""
    assert ONETASKGRAPH_BIN.is_file(), (
        f"this checkout's own onetaskgraph is missing at {ONETASKGRAPH_BIN} — run 'just bootstrap'"
    )
    return {
        "ONETASKGRAPH_BIN": str(STUB_ONETASKGRAPH),
        REAL_ENV: str(ONETASKGRAPH_BIN),
        LOG_ENV: str(log),
        PASS_SHOWS_ENV: str(LAUNCH_PLAN_READS),
    }


# llmlint: ignore-end[e2e_not_mocked, tests_mirror_real_usage]


def _stub_invocations(log: Path) -> list[str]:
    """Every plan-store call the engine made, in order, as the injector saw them."""
    return log.read_text(encoding="utf-8").splitlines() if log.is_file() else []


def test_a_refused_destination_read_settles_the_run_and_writes_nothing(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A destination read the store cannot answer stops the projection and nothing else.

    The recovery path beside
    `test_settlement_write_back_preserves_the_authored_project_description`, and the
    half that says why the preservation is worth anything: the write-back reads its
    destination before it builds the shadow it copies over, and a read it cannot trust
    has to end the projection rather than fall back to a default. A read that defaulted
    would be a read that deletes — the shadow would carry an empty description and the
    copy is a total replacement by contract — so this is the same defect arriving
    through the other door.

    Both halves are asserted, because either alone passes for the wrong reason. That
    the record is untouched is not evidence on its own: a run that never projected at
    all leaves exactly that. So the injector's own log is read for what the engine
    *did* — that the launch's plan read went through, that the write-back's read was
    the one refused, and that no `project copy` was ever reached — and the record is
    compared byte for byte beside it.

    Below the refusal this fails three times over, each for its own reason:
    onepipeline 0.16.2 performs no destination read at all, so nothing is injected
    into, `project copy` runs, and the record comes back rewritten with an empty body.
    """
    _write_local_project(tmp_path)
    _author_project_body(tmp_path, LOCAL_PROJECT, AUTHORED_PROJECT_BODY)
    record = tmp_path / "projects" / f"{LOCAL_PROJECT}.md"
    before = record.read_bytes()

    log = tmp_path / "plan-store-calls.log"
    environment = _launch_environment(tmp_path / "execution", oneharness_bin)
    environment.update(_prepare_plan_sources(tmp_path))
    environment.update(_injected_partial_read(log))
    environment[PROMPT_LOG_ENV] = str(tmp_path / "turns.jsonl")

    launched = subprocess.run(
        ["just", "orchestrate", LOCAL_QUALIFIED, "--dag-graph", "off"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert launched.returncode == 0, launched.stdout + launched.stderr
    # Asserted before the run id, because the run id is what a lost isolation shows up
    # as and the name alone cannot say why. `ONEPIPELINE_RUNS_DIR` is relative and this
    # repository's own dispatches export it, so a launch that took the ambient value ran
    # into the checkout's shared runs root, where a sibling journey of this module has
    # already taken the name and this one is handed `launch-2` or `launch-3` instead.
    assert (tmp_path / "execution" / "runs" / LOCAL_PROJECT).is_dir(), (
        "this launch did not run in its own runs root, so the run id below is whatever "
        "was free in a root it shares with every other launch on this checkout"
    )
    assert json.loads(launched.stdout.splitlines()[-1]) == {
        "run_id": LOCAL_PROJECT,
        "settlement": "complete",
    }, (
        "a store that cannot answer the write-back's read must not decide the run; "
        f"this one settled as {launched.stdout.splitlines()[-1]!r}"
    )

    invocations = _stub_invocations(log)
    passed_through = [line for line in invocations if line.startswith("project show")]
    refused = [line for line in invocations if line.startswith(f"{STUBBED} project show")]
    assert len(passed_through) == LAUNCH_PLAN_READS, (
        "the launch's own plan read has to go through for there to be a run at all; "
        f"the injector saw {invocations}"
    )
    assert refused, (
        "the write-back has to have read its destination for its refusal to be the "
        f"thing under test; the injector saw {invocations}"
    )
    assert not [line for line in invocations if "project copy" in line], (
        "a refused destination read must end the projection before anything is "
        f"written, and this run reached a copy: {invocations}"
    )

    assert record.read_bytes() == before, (
        "the refused projection rewrote the record anyway; a destination read it "
        "cannot trust has to leave the store exactly as it found it"
    )


#: The pin the second checkout below adopts. Any release `ADOPTED` is not; each
#: checkout's archive is built here rather than fetched, so only the difference matters.
OTHER_PIN = "0.0.1"


# llmlint: ignore-block[e2e_not_mocked, tests_mirror_real_usage] Two things are reached
# past here and both are the boundary rather than the layer under test. The archive fetch
# is doubled at `curl`, which is the crossing to GitHub: the installer, its checksum
# verification, its destination and its verifier are all the real ones, and fetching two
# real releases of two different pins would make this journey a network test of somebody
# else's release page. And the entry point is `install_onetaskgraph` / `verify_onetaskgraph`
# rather than the whole of `session-setup.sh`, because the property under test is
# *two checkouts at two pins* — the public entry point provisions the checkout it is run
# from, so a second pin can only be reached by running it twice, which is exactly what
# this does. `tests/e2e/test_onetaskgraph_host_e2e.py` also drives the public path whole,
# in `test_adopted_archive_binary_and_authoring_ignore_are_in_force`.
def test_two_checkouts_at_two_pins_do_not_revert_each_other(tmp_path: Path) -> None:
    """Each checkout provisions and verifies its own pin, and neither touches the other's.

    This is the defect the destination change is for, driven end to end. Provisioning
    used to install the archive into `$HOME/.local/bin` — one path for the whole host —
    while `verify_onetaskgraph` demanded the *reading* checkout's pin, so the last
    installer to run won and every other checkout verified a binary it had not asked
    for. A `SessionStart` hook fires on every session start and resume, so the
    reversions arrived in bursts rather than once.

    Both checkouts are provisioned from this repository's real `install_onetaskgraph`,
    in the order that used to break it — the second one's install is what reverted the
    first — and both are read back afterwards. One `HOME` is deliberately shared
    between them: it is the thing they used to collide in, so a journey that gave each
    its own would pass with the collision still there.
    """
    root = tmp_path
    home = root / "shared-home"
    first = throwaway_checkout(root / "first", version=ADOPTED)
    second = throwaway_checkout(root / "second", version=OTHER_PIN)

    installed_first = run_installer(first, release_fixture(root, ADOPTED), home)
    assert installed_first.returncode == 0, (
        f"the checkout pinned to {ADOPTED} could not provision:\n"
        f"{installed_first.stdout}{installed_first.stderr}"
    )
    installed_second = run_installer(second, release_fixture(root, OTHER_PIN), home)
    assert installed_second.returncode == 0, (
        f"the checkout pinned to {OTHER_PIN} could not provision:\n"
        f"{installed_second.stdout}{installed_second.stderr}"
    )

    for repo, pin in ((first, ADOPTED), (second, OTHER_PIN)):
        reported = subprocess.run(
            [str(installed_binary(repo)), "--version"],
            text=True,
            capture_output=True,
            check=False,
        )
        assert reported.stdout.strip() == f"onetaskgraph {pin}", (
            f"{repo.name} adopts {pin} and its own .venv/bin holds "
            f"{reported.stdout.strip()!r}; the other checkout's install reverted it"
        )
        verified = subprocess.run(
            ["bash", "-c", "source scripts/session-setup.sh\nverify_onetaskgraph"],
            cwd=repo,
            env={**os.environ, "HOME": str(home)},
            text=True,
            capture_output=True,
            check=False,
        )
        assert verified.returncode == 0, (
            f"{repo.name} adopts {pin} and its own verification refuses the binary "
            f"it provisioned; the other checkout at {OTHER_PIN if pin == ADOPTED else ADOPTED} "
            f"is live beside it:\n{verified.stdout}{verified.stderr}"
        )

    assert not (home / ".local" / "bin" / "onetaskgraph").exists(), (
        "provisioning wrote to the host-shared $HOME/.local/bin, which is the one "
        "path two checkouts at two pins can overwrite each other on"
    )


def _heal(
    repo: Path, home: Path, serving: Path | None, root: Path
) -> subprocess.CompletedProcess[str]:
    """Run the real self-heal in ``repo``, with only the archive fetch doubled.

    `curl` is doubled as an executable at the front of `PATH` rather than as a shell
    function, because the entry point under test is a script and a function would not
    survive into it. ``serving`` of ``None`` is a `curl` that always fails, which is
    what makes "this run reached no network" an assertion rather than a hope.
    """
    return subprocess.run(
        ["bash", "scripts/onetaskgraph-install.sh"],
        cwd=repo,
        env={
            **os.environ,
            "HOME": str(home),
            "PATH": os.pathsep.join((str(fetch_double(root, serving)), os.environ["PATH"])),
        },
        text=True,
        capture_output=True,
        check=False,
    )


def test_a_checkout_with_no_plan_store_cli_provisions_its_own(tmp_path: Path) -> None:
    """A fresh worktree or publication clone heals itself rather than reading nothing.

    The destination change put this CLI in `<root>/.venv/bin` so a checkout reads the
    release it pinned, and that closed one hazard by opening another: `.venv` is
    ignored state, nothing but this repository puts a binary there, and session setup
    runs on a `SessionStart` hook that a publication's own clone never fires. The gate
    a publication ran in that clone then resolved no CLI at all, and every recipe and
    test that reads the plan store failed on the missing file — which is the shape of
    the two self-heals `scripts/nx.sh` already performs for Bun and for uv.

    Both runs are asserted, because the first alone would pass for a wrapper that
    re-downloads on every invocation — which is what would put a fetch in front of
    every Nx target. The second runs with a `curl` that cannot succeed, so an install
    that reached the network could not have exited 0.
    """
    home = tmp_path / "home"
    home.mkdir()
    repo = throwaway_checkout(tmp_path / "checkout", version=ADOPTED)
    archive = release_fixture(tmp_path, ADOPTED, target=host_target())

    assert not installed_binary(repo).exists(), (
        "the throwaway checkout already carries a CLI, so healing it proves nothing"
    )
    healed = _heal(repo, home, archive, tmp_path)
    assert healed.returncode == 0, (
        f"a checkout with no plan store CLI could not provision one:\n"
        f"{healed.stdout}{healed.stderr}"
    )
    reported = subprocess.run(
        [str(installed_binary(repo)), "--version"], text=True, capture_output=True, check=False
    )
    assert reported.stdout.strip() == f"onetaskgraph {ADOPTED}", (
        f"the self-heal left {reported.stdout.strip()!r} at {installed_binary(repo)}, "
        f"and this checkout adopts {ADOPTED}"
    )

    again = _heal(repo, home, None, tmp_path)
    assert again.returncode == 0, (
        "the second run of the self-heal fetched rather than reading the binary it "
        f"had already installed:\n{again.stdout}{again.stderr}"
    )


def test_the_plans_recipe_heals_a_checkout_that_has_none(tmp_path: Path) -> None:
    """`just plans` answers in a checkout no session setup has ever run in.

    The recipe reads `<root>/.venv/bin/onetaskgraph` directly, so the destination
    change made it the first thing to fail where nothing had provisioned. Driven
    through the real recipe rather than through the script it calls: that the wiring
    exists is the half a journey over the installer alone cannot see.
    """
    home = tmp_path / "home"
    home.mkdir()
    repo = throwaway_checkout(tmp_path / "checkout", version=ADOPTED, recipes=True)
    archive = release_fixture(tmp_path, ADOPTED, target=host_target())

    read = subprocess.run(
        ["just", "plans", "--version"],
        cwd=repo,
        env={
            **os.environ,
            "HOME": str(home),
            "PATH": os.pathsep.join((str(fetch_double(tmp_path, archive)), os.environ["PATH"])),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert read.returncode == 0, (
        f"`just plans` failed in a checkout carrying no CLI:\n{read.stdout}{read.stderr}"
    )
    assert read.stdout.strip() == f"onetaskgraph {ADOPTED}", (
        f"`just plans --version` answered {read.stdout.strip()!r} in a checkout adopting {ADOPTED}"
    )


# llmlint: ignore-end[e2e_not_mocked, tests_mirror_real_usage]


# llmlint: ignore-block[shell_test_tiers_stay_split] This repository runs one Nx project
# and splits its test tiers by pytest marker over four keys `nx.json` declares, which is a
# settled design rather than an omission: `orchestrator:test`, `test-docs`, `test-recipes`
# and `test-checkouts` each key on what their tests read, `tests/conftest.py` fails a test
# that reads outside its own tier's key, and `tests/test_nx_cache_scope.py` holds the four
# selectors to a partition of the suite. A second Nx project for host-tool journeys would
# add a key nothing enforces beside the ones that are enforced.
@shares_workspace_install
def test_adopted_archive_binary_and_authoring_ignore_are_in_force() -> None:
    """This checkout's own provisioning puts the pinned binary in force, and .plans is ignored.

    Scheduled rather than merely run: this is the one journey here that provisions
    **this** checkout, so it holds the exclusive lock on `<root>/.venv` that every other
    `just` recipe in this suite waits on through `uv run`, and it rewrites the
    `.venv/bin` those recipes resolve their tools from. `shares_workspace_install` is
    the constraint that already exists for that resource; `tests/e2e/nx_workspace.py`
    carries its reason and the measurement behind it.

    Provisioned first rather than merely observed, and now the reading is a claim about
    *this* checkout rather than about the host. The installer used to put the archive in
    `$HOME/.local/bin`, one path every checkout on the host shares, while
    `verify_onetaskgraph` demanded the reading checkout's own pin — so a bare read of
    that path measured whichever checkout provisioned last, and the canonical checkout
    reinstalled its own release over this one mid-gate. The destination is this
    checkout's own `.venv/bin` now, which is what makes the assertion below say
    something about the tree it is running in.
    """
    provisioned = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "session-setup.sh")],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert provisioned.returncode == 0, provisioned.stdout + provisioned.stderr
    version = subprocess.run(
        [str(ONETASKGRAPH_BIN), "--version"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert version.stdout.strip() == f"onetaskgraph {ADOPTED}", (
        f"{version.stdout.strip()} is installed at {ONETASKGRAPH_BIN} after this "
        f"checkout provisioned, which adopts {ADOPTED}"
    )
    assert f"ready (onetaskgraph: {ADOPTED} at {ONETASKGRAPH_BIN})" in provisioned.stderr, (
        "session setup's own success line does not name the per-checkout destination "
        "it provisioned, which is the one thing an operator reads to tell this "
        f"checkout's binary from the host-shared path it replaced:\n{provisioned.stderr}"
    )
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", ".plans/projects/example.md"],
        cwd=REPO_ROOT,
        check=False,
    )
    assert ignored.returncode == 0


def _read_back(destination: Path, qualified: str) -> Mapping[str, object]:
    """The metadata the real store reads out of an edited record.

    Asserting on the bytes says the edit looks right; asserting on this says the store can
    still read what was written, which is the property that matters — a record the writer
    left unparseable fails the walk over every task beside it, not only its own.
    """
    read = subprocess.run(
        [
            str(ONETASKGRAPH_BIN),
            *("--set", "sources.destination.plugin=local-md"),
            *("--set", f"sources.destination.config.root={destination}"),
            *("task", "show", f"destination:{qualified}", "--json"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert read.returncode == 0, read.stderr
    metadata = json.loads(read.stdout)["items"][0]["item"]["metadata"]
    assert isinstance(metadata, dict)
    return metadata


def test_the_plain_rendering_the_store_copies_into_is_one_this_host_can_still_edit(
    tmp_path: Path,
) -> None:
    """The drift gate over the metadata rendering `plan_store.write_metadata` edits.

    Two producers write the records this host reads. `orchestrator/project_store.py`
    renders a JSON-quoted metadata key; the plan store's own copy — the path a settlement
    write-back takes back onto the plan a run was launched from — renders a plain YAML
    key. `plan_store` reads both, and a reader that restates a format the other module
    owns drifts from it in silence, so the plain fixture here is produced by running the
    real store rather than hand-authored from what it is believed to emit.

    A copy whose rendering this host can no longer edit fails here, where the cause is
    named, rather than at the next review record with only a refusal to go on.
    """
    source, destination = tmp_path / "source", tmp_path / "destination"
    destination.mkdir()
    write_plan_project(
        source,
        {
            "name": "drift",
            "tasks": [
                {"id": "route", "title": "feat: route", "task": "body", "persona": "engineer"}
            ],
        },
    )
    copied = subprocess.run(
        [
            str(ONETASKGRAPH_BIN),
            *("--set", "sources.source.plugin=local-md"),
            *("--set", f"sources.source.config.root={source}"),
            *("--set", "sources.destination.plugin=local-md"),
            *("--set", f"sources.destination.config.root={destination}"),
            *("task", "copy", "source:drift/route", "--to", "destination"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert copied.returncode == 0, copied.stderr

    record = next((destination / "tasks").rglob("*.md"))
    rendered = record.read_text(encoding="utf-8")
    assert "  onetaskgraph.origin: " in rendered, (
        f"the copy no longer records its origin as a plain entry:\n{rendered}"
    )
    assert "  onepipeline.id: route" in rendered, (
        f"the store no longer renders a plain metadata key; this gate exists to catch "
        f"that, and `plan_store` reads what it renders:\n{rendered}"
    )

    plan_store.write_metadata(record, "orchestrator.plan-review", {"key": "abc"})
    written = record.read_text(encoding="utf-8")
    assert '  "orchestrator.plan-review": {"key": "abc"}' in written
    assert "  onepipeline.id: route" in written
    read_back = _read_back(destination, "drift/route")
    assert read_back["orchestrator.plan-review"] == {"key": "abc"}
    assert read_back["onepipeline.id"] == "route"


def test_a_nested_entry_the_store_renders_is_one_this_host_can_still_edit(
    tmp_path: Path,
) -> None:
    """The drift gate over the other rendering: an entry whose value is a block.

    A settlement projects entries whose values are mappings onto the task it ran, and they
    come back rendered as a key with its contents on the lines beneath. `plan_store` has to
    edit around one without disturbing it.

    Nothing here writes that shape by hand. `plan_store` writes the value as the JSON it
    renders, the real store re-renders it as a block when it copies the record, and the
    writer is then asked to add a record beside it — so the nested rendering under test is
    the store's own, and a store that stopped producing it fails here rather than leaving
    the parser describing a format nobody emits.
    """
    source, destination = tmp_path / "source", tmp_path / "destination"
    destination.mkdir()
    write_plan_project(
        source,
        {
            "name": "nested",
            "tasks": [
                {"id": "route", "title": "feat: route", "task": "body", "persona": "engineer"}
            ],
        },
    )
    authored = next((source / "tasks").rglob("*.md"))
    plan_store.write_metadata(authored, "a.settled.entry", {"inner": "value"})

    copied = subprocess.run(
        [
            str(ONETASKGRAPH_BIN),
            *("--set", "sources.source.plugin=local-md"),
            *("--set", f"sources.source.config.root={source}"),
            *("--set", "sources.destination.plugin=local-md"),
            *("--set", f"sources.destination.config.root={destination}"),
            *("task", "copy", "source:nested/route", "--to", "destination"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert copied.returncode == 0, copied.stderr

    record = next((destination / "tasks").rglob("*.md"))
    rendered = record.read_text(encoding="utf-8")
    assert "  a.settled.entry:\n    inner: value" in rendered, (
        f"the store no longer renders a mapping value as a block; this gate exists to catch "
        f"that, and `plan_store` edits around what it renders:\n{rendered}"
    )

    plan_store.write_metadata(record, "orchestrator.plan-review", {"key": "abc"})
    written = record.read_text(encoding="utf-8")
    assert "  a.settled.entry:\n    inner: value" in written
    assert '  "orchestrator.plan-review": {"key": "abc"}' in written
    read_back = _read_back(destination, "nested/route")
    assert read_back["a.settled.entry"] == {"inner": "value"}
    assert read_back["orchestrator.plan-review"] == {"key": "abc"}


def test_a_block_scalar_the_store_renders_is_one_this_host_can_still_edit(
    tmp_path: Path,
) -> None:
    """The drift gate over the third rendering: a value carried on the lines beneath it.

    A settlement records its detail as prose spanning several lines, and the store renders a
    value of that shape as a block scalar — a key stating only the indicator, with the text
    itself more deeply indented below. `plan_store` has to leave one whole and go on reading
    the entries after it. The prose carries a paragraph break, because a blank line read as
    the end of the metadata block is what puts a review record inside somebody's sentence.

    Nothing here writes that shape by hand either. `plan_store` writes the value as the JSON
    string it is, the real store re-renders it as a block scalar when it copies the record,
    and the writer is then asked to add a record beside it.
    """
    source, destination = tmp_path / "source", tmp_path / "destination"
    destination.mkdir()
    write_plan_project(
        source,
        {
            "name": "scalar",
            "tasks": [
                {"id": "route", "title": "feat: route", "task": "body", "persona": "engineer"}
            ],
        },
    )
    authored = next((source / "tasks").rglob("*.md"))
    plan_store.write_metadata(authored, "a.settled.detail", "first line\n\nthird line\n")

    copied = subprocess.run(
        [
            str(ONETASKGRAPH_BIN),
            *("--set", "sources.source.plugin=local-md"),
            *("--set", f"sources.source.config.root={source}"),
            *("--set", "sources.destination.plugin=local-md"),
            *("--set", f"sources.destination.config.root={destination}"),
            *("task", "copy", "source:scalar/route", "--to", "destination"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert copied.returncode == 0, copied.stderr

    record = next((destination / "tasks").rglob("*.md"))
    rendered = record.read_text(encoding="utf-8")
    assert "  a.settled.detail: |\n    first line\n\n    third line\n" in rendered, (
        f"the store no longer renders a multi-line value as a block scalar; this gate exists "
        f"to catch that, and `plan_store` edits around what it renders:\n{rendered}"
    )
    assert "  onepipeline.id: route" in rendered, (
        f"the entry after the block scalar is what proves the writer resumed reading past "
        f"it; the store no longer renders one:\n{rendered}"
    )

    plan_store.write_metadata(record, "orchestrator.plan-review", {"key": "abc"})
    written = record.read_text(encoding="utf-8")
    assert "  a.settled.detail: |\n    first line\n\n    third line\n" in written
    assert "  onepipeline.id: route" in written
    assert '  "orchestrator.plan-review": {"key": "abc"}' in written
    read_back = _read_back(destination, "scalar/route")
    assert read_back["a.settled.detail"] == "first line\n\nthird line\n"
    assert read_back["orchestrator.plan-review"] == {"key": "abc"}


# llmlint: ignore-end[shell_test_tiers_stay_split]
