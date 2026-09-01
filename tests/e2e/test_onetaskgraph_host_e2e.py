"""Drive this checkout's committed plan-store configuration through the real CLI."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar, Literal, NewType, TypedDict

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
from published_tools import ONETASKGRAPH_BIN
from stub_onetaskgraph import LOG_ENV, PASS_SHOWS_ENV, REAL_ENV, STUBBED
from test_orchestrate_launch_e2e import _environment as _launch_environment

from orchestrator.project_store import frontmatter, write_plan_project
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
#: A repository under the same owner that the board fixture answers as invisible, which
#: is what GitHub answers for one that does not exist or that the token cannot see.
UNREACHABLE_REPOSITORY = _Repository(owner=CONFIGURED_REPOSITORY.owner, name="not-a-repository")


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
#: the write naming it, so the options below are the three the shipped mapping reaches
#: by name; `done` and `cancelled` close the issue instead and need no option.
ORIGIN_FIELD_NAME = "onetaskgraph.origin"
ORIGIN_FIELD_ID = _FieldNodeId("FIELD_origin")
STATUS_FIELD_ID = _FieldNodeId("FIELD_status")


@dataclass(frozen=True)
class _StatusOption:
    id: _FieldOptionId
    name: str

    def rendered(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name}


STATUS_OPTIONS: tuple[_StatusOption, ...] = (
    _StatusOption(id=_FieldOptionId("OPT_backlog"), name="Backlog"),
    _StatusOption(id=_FieldOptionId("OPT_todo"), name="Todo"),
    _StatusOption(id=_FieldOptionId("OPT_progress"), name="In Progress"),
)
#: The node id the fixture answers the configured repository's own lookup with. The
#: journey asserts this reaches `createIssue`, which is the whole of what naming a
#: repository on the source buys: a board has none of its own, so a write without it
#: is refused rather than filed somewhere.
REPOSITORY_NODE_ID = _RepositoryNodeId("R_ai_orchestrator")
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

    def item(self) -> dict[str, object]:
        return {
            "id": self.item_id,
            "fieldValues": {
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
            },
            "content": {
                "__typename": "Issue",
                "id": self.content_id,
                "title": self.title,
                "body": self.body,
                "url": f"https://github.com/{CONFIGURED_REPOSITORY}/issues/{self.item_id}",
                "createdAt": "2026-08-26T00:00:00Z",
                "updatedAt": "2026-08-26T00:00:00Z",
                "state": "OPEN",
                "stateReason": None,
                "repository": {"nameWithOwner": str(CONFIGURED_REPOSITORY)},
                "parent": None if self.parent_id is None else {"id": self.parent_id},
                "subIssuesSummary": {"total": self.sub_issues},
                "labels": {"nodes": [], "pageInfo": {"hasNextPage": False}},
            },
        }


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
        return cls(query=query, variables=variables)

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
    REPOSITORY = "repository"
    DEPENDENCIES = "dependencies"
    CREATE_ISSUE = "createIssue"
    ADD_TO_BOARD = "addToBoard"
    UPDATE_FIELD = "updateField"
    ADD_SUB_ISSUE = "addSubIssue"
    UPDATE_ISSUE = "updateIssue"


#: Each operation's marker in the document the source sends, in the order they are
#: tried. The source ships its queries as constants, so the root field is a stable
#: substring of each one and is what tells a board read from the writes that follow it.
_OPERATIONS: dict[str, _Operation] = {
    "repositoryOwner": _Operation.BOARD,
    "{repository(owner:": _Operation.REPOSITORY,
    "blockedBy(first:": _Operation.DEPENDENCIES,
    "createIssue(": _Operation.CREATE_ISSUE,
    "addProjectV2ItemById(": _Operation.ADD_TO_BOARD,
    "updateProjectV2ItemFieldValue(": _Operation.UPDATE_FIELD,
    "addSubIssue(": _Operation.ADD_SUB_ISSUE,
    "updateIssue(": _Operation.UPDATE_ISSUE,
}

BOARD = _Board()


class _GitHubFixture(BaseHTTPRequestHandler):
    requests: ClassVar[list[_GraphQLRequest]]

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
        length = int(self.headers["Content-Length"])
        request = _GraphQLRequest.from_json(self.rfile.read(length))
        self.requests.append(request)
        body = json.dumps(self._answer(request)).encode()
        self.send_response(200)
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

    @staticmethod
    def _repository(named: _Repository) -> dict[str, object]:
        """What GitHub answers about one repository, and about one it will not show.

        A repository that does not exist, or that the token cannot see, comes back as a
        present and null field rather than as an error — which is the answer the source
        turns into its refusal, so it is the answer this fixture gives.
        """
        if named != CONFIGURED_REPOSITORY:
            return {"data": {"repository": None}}
        return {
            "data": {
                "repository": {"id": REPOSITORY_NODE_ID, "nameWithOwner": str(named)},
            }
        }

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def _serving_board() -> Iterator[dict[str, str]]:
    """Serve the board fixture, yielding the environment that points `plans` at it.

    The board is reset per journey rather than shared: it is mutated by the writes a
    copy performs, so a second journey reading the residue of the first would report a
    board somebody else's copy filled in.
    """
    BOARD.reset()
    _GitHubFixture.requests = []
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
        thread.join()
        server.server_close()


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
    board_reads = [
        request for request in _GitHubFixture.requests if request.operation is _Operation.BOARD
    ]
    assert board_reads
    assert board_reads[0].variables["owner"] == "nickderobertis"
    assert board_reads[0].variables["number"] == 2


def test_project_copy_files_its_issues_in_the_configured_repository(tmp_path: Path) -> None:
    """A copy to `plans` creates its issues in the repository this checkout names.

    That naming is the whole of what the `repository` field buys: a board has no
    repository of its own and `createIssue` requires one, so a copy either resolves the
    configured repository's node id and files every issue against it or is refused. The
    node id the fixture answers with is asserted at `createIssue` rather than only at
    the lookup, because a source that asked for the repository and then created its
    issues somewhere else would pass the lookup assertion alone.
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
            "ONETASKGRAPH_SOURCES__AUTHORING__CONFIG__ROOT": str(tmp_path),
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
    plan it was launched from, and under onepipeline 0.18.3 against onetaskgraph 0.2.17
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
        "version": 1,
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


# llmlint: ignore-end[shell_test_tiers_stay_split]
