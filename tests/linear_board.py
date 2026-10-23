r"""Stateful loopback Linear wire for real installed plan-store journeys.

Rewrite evidence: onetaskgraph-linear's ``crates/onetaskgraph-linear/src/lib.rs``,
"Ruling: what Linear does to an HTML comment, settled", and this host's incident
where bullets, table delimiters and autolinks changed a copied design document.
Comments retain their bytes; issue descriptions, project and document content do not.


"""

from __future__ import annotations

import copy
import json
import re
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, NewType

from orchestrator import plan_store


@dataclass(frozen=True)
class Rewrite:
    """A named wire normalization, independently extensible by a consumer."""

    name: str
    apply: Callable[[str], str]
    written: str = ""
    stored: str = ""


def _autolink(text: str) -> str:
    pattern = r'https?://[^\s<>"\[\]]+|(?<![\w@/.:\\#=+%\-])(?:[a-zA-Z0-9-]+\.)+(?:ai|app|co|com|dev|edu|gov|info|io|live|me|net|org)\b(?:/[^\s<>"\[\]]*)?'

    def link(match: re.Match[str]) -> str:
        token = match[0].rstrip(".,;:!?)")
        target = token if token.startswith(("http://", "https://")) else "http://" + token
        return f"[{token}](<{target}>)" + match[0][len(token) :]

    return re.sub(pattern, link, text)


def _table(text: str) -> str:
    def delimiter(match: re.Match[str]) -> str:
        cells = [cell.strip() for cell in match[2].split("|")[:-1]]
        rewritten = [
            (":" if cell.startswith(":") else "") + "--" + (":" if cell.endswith(":") else "")
            for cell in cells
        ]
        return match[1] + "| " + " | ".join(rewritten) + " |" + match[3]

    return re.sub(
        r"^([ \t]*)\|((?:[ \t]*:?-+:?[ \t]*\|)+)(\r?)$",
        delimiter,
        text,
        flags=re.M,
    )


# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] This task explicitly
# assigns comparison with real Linear to onetaskgraph's producer-owned live lane,
# real_linear_applies_every_declared_capability_and_leaves_no_residue, rather than this
# host's bar. The wire double deliberately restates those observations independently;
# its example test holds the local statement to its behavior, not to a live workspace.
LINEAR_REWRITES: tuple[Rewrite, ...] = (
    Rewrite("bullet", lambda s: re.sub(r"^(\s*)- ", r"\1* ", s, flags=re.M), "- one", "* one"),
    Rewrite("table", _table, "| --- |", "| -- |"),
    Rewrite("brackets", lambda s: re.sub(r"(?<!\\)([\[\]])", r"\\\1", s), "[x]", r"\[x\]"),
    Rewrite("tilde", lambda s: re.sub(r"(?<!\\)~", r"\\~", s), "~5", r"\~5"),
    Rewrite("emphasis", lambda s: re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"*\1*", s), "_y_", "*y*"),
    Rewrite("quote", lambda s: s.replace('\\"', '"'), 'say \\"hi\\"', 'say "hi"'),
    Rewrite("backslash", lambda s: re.sub(r"(?<!\\)\\([A-Za-z])", r"\\\\\1", s), r"a \d", r"a \\d"),
    Rewrite("comment-close", lambda s: re.sub(r"^-->", r"\\-->", s, flags=re.M), "-->", r"\-->"),
    Rewrite("autolink", _autolink, "example.com", "[example.com](<http://example.com>)"),
)
# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]
r"""Observed rules from onetaskgraph-linear's module documentation,
"Ruling: what Linear does to an HTML comment, settled", and the host incident.

Each rule carries its input (``written``) and exact stored example (``stored``),
so callers can enumerate and exercise the complete list without a second table.
Rules apply outside code spans/fences and existing Markdown links.

LINEAR_REWRITES examples (JSON strings, written -> stored):
bullet: "- one" -> "* one"
table: "| --- |" -> "| -- |"
brackets: "[x]" -> "\\[x\\]"
tilde: "~5" -> "\\~5"
emphasis: "_y_" -> "*y*"
quote: "say \\\"hi\\\"" -> "say \"hi\""
backslash: "a \\d" -> "a \\\\d"
comment-close: "-->" -> "\\-->"
autolink: "example.com" -> "[example.com](<http://example.com>)"
URL: "https://example.com/a." -> "[https://example.com/a](<https://example.com/a>)."
"""


def stored_form(content: str, rewrites: Sequence[Rewrite]) -> str:
    """Apply wire rules while preserving code and already linked text."""
    protected = re.compile(r"(`+)[^\n]*?\1|!?\[[^\]\n]*\]\([^\n)]*\)|\\[\[\]~]")
    fence: tuple[str, int] | None = None
    result: list[str] = []
    for line in content.splitlines(keepends=True):
        mark = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)", line)
        if fence:
            result.append(line)
            if mark and mark[1][0] == fence[0] and len(mark[1]) >= fence[1] and not mark[2].strip():
                fence = None
            continue
        if mark:
            fence = (mark[1][0], len(mark[1]))
            result.append(line)
            continue
        # Keep line anchors attached to the original line: a fragment after a link
        # or code span must not become a bullet or an opening comment-close line.
        spans: list[str] = []
        marker = "\0"
        while marker in line:
            marker += "\0"

        def protect(match: re.Match[str], held: list[str] = spans, token: str = marker) -> str:
            held.append(match[0])
            return f"{token}{len(held) - 1}{token}"

        rewritten = protected.sub(protect, line)
        for rule in rewrites:
            rewritten = rule.apply(rewritten)

        def restore(match: re.Match[str], held: list[str] = spans) -> str:
            return held[int(match[1])]

        result.append(re.sub(re.escape(marker) + r"(\d+)" + re.escape(marker), restore, rewritten))
    return "".join(result)


def _connection(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "nodes": nodes,
        "pageInfo": {
            "hasNextPage": False,
            "endCursor": None,
            "hasPreviousPage": False,
            "startCursor": None,
        },
    }


NativeId = NewType("NativeId", str)
StateId = NewType("StateId", str)
IssueIdentifier = NewType("IssueIdentifier", str)


@dataclass(frozen=True)
class State:
    id: StateId
    name: str
    type: str = "unstarted"
    position: int = 0


@dataclass(frozen=True)
class Request:
    operation: str
    variables: dict[str, Any]


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(kw_only=True)
class Record:
    id: NativeId
    createdAt: str = field(default_factory=_now)
    updatedAt: str = field(default_factory=_now)


@dataclass(kw_only=True)
class Issue(Record):
    title: str = ""
    description: str = ""
    identifier: IssueIdentifier
    state: State
    priority: int = 0
    project_id: NativeId | None = None


@dataclass(kw_only=True)
class Project(Record):
    name: str = ""
    content: str = ""
    status: State


@dataclass(kw_only=True)
class Document(Record):
    title: str = ""
    content: str = ""
    project_id: NativeId | None = None


@dataclass(kw_only=True)
class Comment(Record):
    body: str = ""
    issue_id: NativeId


@dataclass(kw_only=True)
class Relation(Record):
    source_id: NativeId
    target_id: NativeId
    type: str


def _text(fields: dict[str, Any], key: str) -> str:
    value = fields[key]
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _reference(native_id: NativeId | None) -> dict[str, str] | None:
    return {"id": native_id} if native_id else None


class LinearBoard:
    """One team's in-memory API, with detached observations for assertions."""

    def __init__(self, rewrites: Sequence[Rewrite]) -> None:
        self.endpoint = ""
        self.rewrites = tuple(rewrites)
        self._issues: dict[NativeId, Issue] = {}
        self._projects: dict[NativeId, Project] = {}
        self._documents: dict[NativeId, Document] = {}
        self._comments: dict[NativeId, Comment] = {}
        self._issue_relations: dict[NativeId, Relation] = {}
        self._project_relations: dict[NativeId, Relation] = {}
        self._requests: list[Request] = []
        self._assets: dict[str, bytes] = {}
        self._states: list[State] = []
        self._statuses: list[State] = []
        self._lock = threading.RLock()

    def environment(self, source: str) -> dict[str, str]:
        settings = plan_store.configured_settings()
        prefix = f"sources.{source}.config."
        key = settings.get(prefix + "api_key_env")
        if not isinstance(key, str):
            raise ValueError(f"{source} has no resolved api_key_env")
        mapping = settings.get(prefix + "status_mapping", {})
        for kind, target in (("task", self._states), ("project", self._statuses)):
            names: list[str] = []
            if isinstance(mapping, dict):
                for value in mapping.values():
                    name = value.get(kind) if isinstance(value, dict) else value
                    if isinstance(name, str):
                        names.append(name)
            for path, value in settings.items():
                tail = path.removeprefix(prefix + "status_mapping.")
                if (
                    path.startswith(prefix + "status_mapping.")
                    and isinstance(value, str)
                    and ("." not in tail or tail.endswith("." + kind))
                ):
                    names.append(value)
            # Several sources can share this team; keep the IDs earlier callers resolved.
            held = {state.name for state in target}
            for name in dict.fromkeys(names):
                if name in held:
                    continue
                position = len(target)
                target.append(
                    State(
                        id=StateId(f"{kind}-state-{position}"),
                        name=name,
                        type="completed" if name.lower() in ("done", "completed") else "unstarted",
                        position=position,
                    )
                )
                held.add(name)
        encoded = source.upper().replace("-", "_")
        return {
            f"ONETASKGRAPH_SOURCES__{encoded}__CONFIG__ENDPOINT": self.endpoint,
            key: "loopback-not-a-production-credential",
        }

    def _wire(self, item: Record) -> dict[str, Any]:
        held: dict[str, Any] = {
            "id": item.id,
            "createdAt": item.createdAt,
            "updatedAt": item.updatedAt,
            "archivedAt": None,
            "url": "https://linear.app/synthetic/" + item.id,
        }
        match item:
            case Issue():
                held.update(
                    title=item.title,
                    description=item.description,
                    identifier=item.identifier,
                    state=asdict(item.state),
                    priority=item.priority,
                    project=_reference(item.project_id),
                    labels=_connection([]),
                )
                kind, relations = "issue", self._issue_relations
                held["comments"] = _connection(
                    [
                        self._wire(comment)
                        for comment in reversed(list(self._comments.values()))
                        if comment.issue_id == item.id
                    ]
                )
            case Project():
                held.update(
                    name=item.name,
                    content=item.content,
                    status=asdict(item.status),
                    labels=_connection([]),
                )
                kind, relations = "project", self._project_relations
            case Document():
                held.update(
                    title=item.title, content=item.content, project=_reference(item.project_id)
                )
                return held
            case Comment():
                held.update(
                    body=item.body,
                    issue=_reference(item.issue_id),
                    user={"displayName": "Synthetic Person"},
                )
                return held
            case Relation():
                held.update(type=item.type)
                return held
            case _:
                raise ValueError(f"unsupported record {type(item).__name__}")
        held["relations"] = _connection(
            [
                {
                    "id": edge.id,
                    "type": edge.type,
                    "related" + kind.title(): _reference(edge.target_id),
                }
                for edge in relations.values()
                if edge.source_id == item.id
            ]
        )
        held["inverseRelations"] = _connection(
            [
                {"id": edge.id, "type": edge.type, kind: _reference(edge.source_id)}
                for edge in relations.values()
                if edge.target_id == item.id
            ]
        )
        return held

    def _observations(self, items: Mapping[NativeId, Record]) -> list[dict[str, Any]]:
        with self._lock:
            result = [self._wire(item) for item in items.values()]
            for item in result:
                raw = item.get("description", item.get("content", ""))
                slot = re.search(r"<!-- onetaskgraph.metadata `(.+?)` -->", raw, re.S)
                item["metadata"] = json.loads(slot[1]) if slot else {}
                if "name" in item:
                    item["title"] = item["name"]
            return result

    def issues(self) -> list[dict[str, Any]]:
        return self._observations(self._issues)

    def projects(self) -> list[dict[str, Any]]:
        return self._observations(self._projects)

    def documents(self) -> list[dict[str, Any]]:
        return self._observations(self._documents)

    def requests(self) -> list[dict[str, Any]]:
        """GraphQL operations and asset HTTP methods, whose variables name their path."""
        with self._lock:
            return [asdict(request) for request in self._requests]

    def edit(self, native_id: str, content: str) -> None:
        native = NativeId(native_id)
        with self._lock:
            item: Issue | Project | Document | None = (
                self._issues.get(native)
                or self._projects.get(native)
                or self._documents.get(native)
            )
            if item is None:
                raise KeyError(native_id)
            match item:
                case Issue():
                    item.description = stored_form(content, self.rewrites)
                case Project() | Document():
                    item.content = stored_form(content, self.rewrites)
            item.updatedAt = _now()

    def _collection(self, kind: str) -> Mapping[NativeId, Record]:
        match kind:
            case "issue":
                return self._issues
            case "project":
                return self._projects
            case "document":
                return self._documents
            case "comment":
                return self._comments
            case "issueRelation":
                return self._issue_relations
            case "projectRelation":
                return self._project_relations
            case _:
                raise ValueError("unsupported operation " + kind)

    def _write(self, kind: str, native: NativeId, fields: dict[str, Any]) -> Record:
        match kind:
            case "issue":
                issue = self._issues.setdefault(
                    native,
                    Issue(
                        id=native,
                        identifier=IssueIdentifier(f"SYN-{len(self._issues) + 1}"),
                        state=self._states[0],
                    ),
                )
                if "title" in fields:
                    issue.title = _text(fields, "title")
                if "description" in fields:
                    issue.description = stored_form(_text(fields, "description"), self.rewrites)
                if "stateId" in fields:
                    issue.state = next(s for s in self._states if s.id == fields["stateId"])
                if "projectId" in fields:
                    issue.project_id = (
                        NativeId(_text(fields, "projectId")) if fields["projectId"] else None
                    )
                if "priority" in fields:
                    priority = fields["priority"]
                    if type(priority) is not int or not 0 <= priority <= 4:
                        raise ValueError("priority must be an integer from 0 to 4")
                    issue.priority = priority
                item: Record = issue
            case "project":
                project = self._projects.setdefault(
                    native, Project(id=native, status=self._statuses[0])
                )
                if "name" in fields:
                    project.name = _text(fields, "name")
                if "content" in fields:
                    project.content = stored_form(_text(fields, "content"), self.rewrites)
                if "statusId" in fields:
                    project.status = next(s for s in self._statuses if s.id == fields["statusId"])
                item = project
            case "document":
                document = self._documents.setdefault(native, Document(id=native))
                if "title" in fields:
                    document.title = _text(fields, "title")
                if "content" in fields:
                    document.content = stored_form(_text(fields, "content"), self.rewrites)
                if "projectId" in fields:
                    document.project_id = (
                        NativeId(_text(fields, "projectId")) if fields["projectId"] else None
                    )
                item = document
            case "comment":
                comment = self._comments.get(native)
                if comment is None:
                    comment = Comment(id=native, issue_id=NativeId(_text(fields, "issueId")))
                    self._comments[native] = comment
                if "body" in fields:
                    comment.body = _text(fields, "body")
                item = comment
            case "issueRelation" | "projectRelation":
                edge_kind = "issue" if kind == "issueRelation" else "project"
                relation = Relation(
                    id=native,
                    source_id=NativeId(_text(fields, edge_kind + "Id")),
                    target_id=NativeId(_text(fields, "related" + edge_kind.title() + "Id")),
                    type=_text(fields, "type"),
                )
                edges = self._issue_relations if edge_kind == "issue" else self._project_relations
                edges[native] = relation
                item = relation
            case _:
                raise ValueError("unsupported operation " + kind)
        item.updatedAt = _now()
        return item

    def dispatch(
        self, query: str, variables: dict[str, Any], operation_name: str | None
    ) -> dict[str, Any]:
        """Resolve exactly one supported root operation; unknown roots are errors."""
        match = re.search(r"\{\s*(\w+)", query)
        if match is None:
            raise ValueError("unsupported operation " + str(operation_name))
        operation = match[1]
        declaration = re.match(r"\s*(?:query|mutation)\s+([A-Za-z_]\w*)", query)
        named = operation_name or (declaration[1] if declaration else operation)
        with self._lock:
            self._requests.append(Request(named, copy.deepcopy(variables)))
            match operation:
                case "organization":
                    return {
                        "organization": {"id": "synthetic-workspace"},
                        "teams": _connection(
                            [
                                {
                                    "id": "synthetic-team",
                                    "states": _connection(
                                        [asdict(state) for state in self._states]
                                    ),
                                }
                            ]
                        ),
                        "projectStatuses": _connection(
                            [asdict(status) for status in self._statuses]
                        ),
                    }
                case "viewer":
                    return {operation: {"id": "synthetic-user"}}
                case "issueLabels" | "projectLabels":
                    return {operation: _connection([])}
                case "fileUpload":
                    url = self.endpoint.removesuffix("/graphql") + "/assets/" + str(uuid.uuid4())
                    return {
                        operation: {
                            "success": True,
                            "uploadFile": {
                                "uploadUrl": url,
                                "assetUrl": url,
                                "headers": [],
                            },
                        }
                    }
            for kind in (
                "issue",
                "project",
                "document",
                "comment",
                "issueRelation",
                "projectRelation",
            ):
                items = self._collection(kind)
                match operation:
                    case requested if requested == kind:
                        item = items.get(NativeId(_text(variables, "id")))
                        return {operation: self._wire(item) if item else None}
                    case requested if requested == kind + "s":
                        nodes = [self._wire(item) for item in items.values()]
                        project_filter = (variables.get("filter") or {}).get("project", {})
                        if "id" in project_filter:
                            nodes = [
                                item
                                for item in nodes
                                if (item.get("project") or {}).get("id")
                                == project_filter["id"]["eq"]
                            ]
                        return {operation: _connection(nodes)}
                    case requested if requested == kind + "Delete":
                        native = NativeId(_text(variables, "id"))
                        match kind:
                            case "issue":
                                self._issues.pop(native, None)
                            case "project":
                                self._projects.pop(native, None)
                            case "document":
                                self._documents.pop(native, None)
                            case "comment":
                                self._comments.pop(native, None)
                            case "issueRelation":
                                self._issue_relations.pop(native, None)
                            case "projectRelation":
                                self._project_relations.pop(native, None)
                        return {operation: {"success": True}}
                    case requested if requested in (kind + "Create", kind + "Update"):
                        fields = variables["input"]
                        if not isinstance(fields, dict):
                            raise ValueError(f"{operation}: input must be an object")
                        raw_native = variables.get("id") or fields.get("id") or str(uuid.uuid4())
                        if not isinstance(raw_native, str):
                            raise ValueError(f"{operation}: id must be a string")
                        native = NativeId(raw_native)
                        if operation.endswith("Update") and native not in items:
                            raise ValueError(f"{operation}: unknown id {native}")
                        return {
                            operation: {
                                "success": True,
                                kind: self._wire(self._write(kind, native, fields)),
                            }
                        }
            raise ValueError("unsupported operation " + named)


@contextmanager
def linear_board(*, rewrites: Sequence[Rewrite] = LINEAR_REWRITES) -> Iterator[LinearBoard]:
    """Serve only loopback, and close the socket and server thread with the context."""
    board = LinearBoard(rewrites)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            try:
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                answer: dict[str, Any] = {
                    "data": board.dispatch(
                        payload["query"],
                        payload.get("variables") or {},
                        payload.get("operationName"),
                    )
                }
            except (ValueError, KeyError, StopIteration) as exc:
                answer = {"errors": [{"message": str(exc)}]}
            body = json.dumps(answer).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_PUT(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            with board._lock:
                board._requests.append(Request("PUT", {"path": self.path}))
                board._assets[self.path] = body
            self.send_response(200)
            self.end_headers()

        def do_GET(self) -> None:
            with board._lock:
                board._requests.append(Request("GET", {"path": self.path}))
                body = board._assets.get(self.path)
            self.send_response(200 if body is not None else 404)
            self.end_headers()
            if body is not None:
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    board.endpoint = f"http://127.0.0.1:{server.server_port}/graphql"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield board
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
