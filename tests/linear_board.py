r"""Stateful loopback Linear wire for real installed plan-store journeys.

Issue descriptions, project content and document content are stored the way real Linear
stored them in onetaskgraph's spike `spike-linear-stored-content` (harness commit
``f2344284``, report `linear-stored-content-verification-spike-linear-stored-content-report`):
each write answers the long form as Linear keeps it, which can differ from what was sent.
The plan store predicts none of it; it takes the stored form from each write's own answer.
Comments, titles and every other field keep their bytes.

`MEASURED_REWRITES` lists each change with an example of what is sent and what is kept,
and `linear_stored` applies them; a consumer may add its own `Rewrite` to model one more.
Every request is logged with the items it returned and the item it wrote, and whether
that write was stored other than as sent, so a journey reads its cost off the wire.
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, NewType

from orchestrator import plan_store


@dataclass(frozen=True)
class Rewrite:
    """One change Linear makes to a long form it stores, with an example of it.

    ``written`` is what a write sends and ``stored`` what Linear keeps of it. A ``block``
    rewrite reads whole stretches of text between fenced code blocks; any other reads one
    line at a time, outside code spans, fenced code and Markdown links.
    """

    name: str
    apply: Callable[[str], str]
    written: str = ""
    stored: str = ""
    block: bool = False


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


#: Inline text of one paragraph: anything but a backtick, and no blank line.
_SPAN = r"(?:[^`\n]|\n(?!\s*\n))+"
#: A list item's marker, bulleted or ordered, after any indentation.
_ITEM = r"\s*(?:[*\-+] |\d+[.)] )"


def _list_after_line(text: str) -> str:
    """A blank line between a heading or paragraph line and a bullet list directly after it."""

    def spaced(match: re.Match[str]) -> str:
        line = match[1]
        joined = not line.strip() or re.match(_ITEM, line) or line.startswith((" ", "\t", "|"))
        return match[0] if joined else line + "\n\n"

    return re.sub(r"^([^\n]*)\n(?=[*\-] )", spaced, text, flags=re.M)


def _bold_wrap(text: str) -> str:
    """A `**bold**` run spanning a line break, split into one run per line.

    Delimiters pair left to right within a paragraph, so a run's closing `**` never pairs
    with the next run's opening one.
    """

    def split(match: re.Match[str]) -> str:
        return "\n".join("**" + line + "**" for line in match[1].split("\n"))

    return re.sub(r"\*\*((?:(?!\n[ \t]*\n)[^*])+?)\*\*", split, text)


def _indented_code(text: str) -> str:
    """An indented code block after a blank line, fenced, its indentation dropped."""

    def fenced(match: re.Match[str]) -> str:
        lines = [line[4:] for line in match[2].splitlines()]
        return match[1] + "```\n" + "\n".join(lines) + "\n```\n"

    return re.sub(
        r"((?:\A|\n)[ \t]*\n)((?:    (?!" + _ITEM.removeprefix(r"\s*") + r")[^\n]*(?:\n|\Z))+)",
        fenced,
        text,
    )


# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] Each example is what the
# spike `spike-linear-stored-content` measured real Linear storing (harness f2344284), or the
# earlier long-form observation it re-took. This wire is held to that measurement, not to
# Linear as it evolves: the task writes to no real workspace, and the manager's copy of a
# real plan after adoption is the real-Linear check.
MEASURED_REWRITES: tuple[Rewrite, ...] = (
    Rewrite(
        "lazy-ordered-item",
        lambda s: re.sub(r"^([*\-] [^\n]+)\n(\d+)\. ", r"\1\n  \2\\. ", s, flags=re.M),
        "- first\n3. continuation\n- next",
        "* first\n  3\\. continuation\n* next",
        block=True,
    ),
    Rewrite(
        "list-after-line",
        _list_after_line,
        "## Heading\n- first\n- next\n\nParagraph.\n- another",
        "## Heading\n\n* first\n* next\n\nParagraph.\n\n* another",
        block=True,
    ),
    Rewrite(
        "bold-wrap",
        _bold_wrap,
        "**Closed.** A run\nthat wraps **bold text\nnext line** here.",
        "**Closed.** A run\nthat wraps **bold text**\n**next line** here.",
        block=True,
    ),
    Rewrite(
        "indented-code",
        _indented_code,
        "Before.\n\n    code\n    more\n\nAfter.",
        "Before.\n\n```\ncode\nmore\n```\n\nAfter.",
        block=True,
    ),
    Rewrite(
        "code-span-wrap",
        lambda s: re.sub(
            r"(?<!`)(`+)(" + _SPAN + r")\1(?!`)",
            lambda m: m[1] + " ".join(m[2].splitlines()) + m[1],
            s,
        ),
        "See `code span\nwraps` here.",
        "See `code span wraps` here.",
        block=True,
    ),
    Rewrite(
        "table-slot-separator",
        lambda s: re.sub(
            r"(^\|[^\n]+\|\n)\n+(?=<!-- onetaskgraph.metadata )", r"\1\n", s, flags=re.M
        ),
        "| one |\n\n\n<!-- onetaskgraph.metadata `{}` -->",
        "| one |\n\n<!-- onetaskgraph.metadata `{}` -->",
        block=True,
    ),
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

#: A fenced code block whole, from its opening fence to the matching closing one.
_FENCED = re.compile(r"(^ {0,3}(`{3,}|~{3,})[^\n]*\n.*?^ {0,3}\2[^\n]*(?:\n|$))", re.M | re.S)


def linear_stored(content: str, rewrites: Sequence[Rewrite]) -> str:
    """What Linear keeps of a long form ``content`` written to it, under ``rewrites``."""
    for rule in rewrites:
        if not rule.block:
            continue
        # The split alternates text, a fenced block, and that block's fence characters.
        chunks = _FENCED.split(content)
        content = "".join(
            rule.apply(chunk) if index % 3 == 0 else chunk if index % 3 == 1 else ""
            for index, chunk in enumerate(chunks)
        )
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
            if not rule.block:
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


@dataclass
class Request:
    """One request as the wire answered it.

    ``read`` names every issue, project and document its answer carried, ``written`` the
    one it created or updated, and ``rewritten`` whether a long form that write sent was
    stored other than as sent. ``client`` is the process that sent it, where the host
    names one, so a journey can tell one command's requests from the next command's.
    """

    operation: str
    variables: dict[str, Any]
    client: int | None = None
    read: list[str] = field(default_factory=list)
    written: str | None = None
    rewritten: bool = False


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
        #: The loopback port the wire answers on, once it is serving.
        self.port = 0
        self.rewrites = tuple(rewrites)
        self._issues: dict[NativeId, Issue] = {}
        self._projects: dict[NativeId, Project] = {}
        self._documents: dict[NativeId, Document] = {}
        self._comments: dict[NativeId, Comment] = {}
        self._issue_relations: dict[NativeId, Relation] = {}
        self._project_relations: dict[NativeId, Relation] = {}
        self._requests: list[Request] = []
        #: The request `dispatch` is answering, which a write it makes is logged on.
        self._answering: Request | None = None
        self._assets: dict[str, bytes] = {}
        self._states: list[State] = []
        self._statuses: list[State] = []
        self._arrivals: list[int] = []
        self._limited_for_ms: int | None = None
        self._limited_until_ms: int | None = None
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

    def rate_limit_for(self, window_ms: int) -> None:
        """Answer every GraphQL request rate limited for ``window_ms`` from the first one.

        Linear refuses a spent quota with HTTP 429, an error coded `RATELIMITED`, and its
        reset as epoch milliseconds in `X-RateLimit-Requests-Reset`
        (https://linear.app/developers/rate-limiting); from the reset on, requests are
        answered again. The window opens at the first request rather than when it is set,
        so a caller slow to reach the stand-in still meets the whole of it.
        """
        with self._lock:
            self._limited_for_ms = window_ms
            self._limited_until_ms = None

    def reset_at(self) -> int | None:
        """The epoch milliseconds the rate limit lifts at, once a request has opened it."""
        with self._lock:
            return self._limited_until_ms

    def arrivals(self) -> list[int]:
        """The epoch milliseconds every GraphQL request arrived at, refused or answered."""
        with self._lock:
            return list(self._arrivals)

    def _refusal(self) -> int | None:
        """Record a request's arrival, and the reset it is refused until while one stands."""
        now = time.time_ns() // 1_000_000
        with self._lock:
            self._arrivals.append(now)
            if self._limited_for_ms is not None and self._limited_until_ms is None:
                self._limited_until_ms = now + self._limited_for_ms
            until = self._limited_until_ms
            return until if until is not None and now < until else None

    def create_issue(self, native_id: str, title: str, description: str) -> None:
        """Hold an issue of the team in its first state, written as a person wrote it."""
        with self._lock:
            if not self._states:
                self._states.append(State(id=StateId("task-state-0"), name="Todo"))
            self._write("issue", NativeId(native_id), {"title": title})
            self._issues[NativeId(native_id)].description = description

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
                    item.description = linear_stored(content, self.rewrites)
                case Project() | Document():
                    item.content = linear_stored(content, self.rewrites)
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

    def _long_form(self, sent: str) -> str:
        """What Linear keeps of a long form, noting on this request when it is not ``sent``."""
        stored = linear_stored(sent, self.rewrites)
        if self._answering is not None and stored != sent:
            self._answering.rewritten = True
        return stored

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
                    issue.description = self._long_form(_text(fields, "description"))
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
                    project.content = self._long_form(_text(fields, "content"))
                if "statusId" in fields:
                    project.status = next(s for s in self._statuses if s.id == fields["statusId"])
                item = project
            case "document":
                document = self._documents.setdefault(native, Document(id=native))
                if "title" in fields:
                    document.title = _text(fields, "title")
                if "content" in fields:
                    document.content = self._long_form(_text(fields, "content"))
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
        self,
        query: str,
        variables: dict[str, Any],
        operation_name: str | None,
        client: int | None = None,
    ) -> dict[str, Any]:
        """Resolve exactly one supported root operation; unknown roots are errors."""
        match = re.search(r"\{\s*(\w+)", query)
        if match is None:
            raise ValueError("unsupported operation " + str(operation_name))
        operation = match[1]
        declaration = re.match(r"\s*(?:query|mutation)\s+([A-Za-z_]\w*)", query)
        named = operation_name or (declaration[1] if declaration else operation)
        with self._lock:
            logged = Request(named, copy.deepcopy(variables), client=client)
            self._requests.append(logged)
            self._answering = logged
            try:
                answer = self._resolve(operation, named, variables)
            finally:
                self._answering = None
            written = {logged.written} if logged.written else set()
            logged.read = sorted(_carried(answer) - written)
            return answer

    def _resolve(self, operation: str, named: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Answer one root ``operation``, under the lock `dispatch` holds."""
        resolution = {
            "organization": {"id": "synthetic-workspace"},
            "teams": _connection(
                [
                    {
                        "id": "synthetic-team",
                        "states": _connection([asdict(state) for state in self._states]),
                    }
                ]
            ),
            "projectStatuses": _connection([asdict(status) for status in self._statuses]),
        }
        match operation:
            case "organization":
                return resolution
            case "originIssues":
                # onetaskgraph 0.12.5's batched read ahead of a copy's lookups by origin:
                # the `issues` and `projects` connections, aliased, beside the resolution
                # read, in one request. Narrowed as each connection is below.
                return {
                    "originIssues": _page(
                        [
                            node
                            for node in (self._wire(item) for item in self._issues.values())
                            if _matches(node, variables.get("issuesFilter"))
                        ],
                        variables.get("issuesFirst"),
                    ),
                    "originProjects": _connection(
                        [self._wire(item) for item in self._projects.values()]
                    ),
                    **resolution,
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
                    return {operation: _connection(_in_project(nodes, variables.get("filter")))}
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
                    if kind in ("issue", "project", "document") and self._answering:
                        self._answering.written = native
                    return {
                        operation: {
                            "success": True,
                            kind: self._wire(self._write(kind, native, fields)),
                        }
                    }
        raise ValueError("unsupported operation " + named)


def _client(port: int, served: int) -> int | None:
    """The process holding the loopback connection from ``port`` to this wire's ``served`` port.

    Read off Linux's own socket tables while the request is open: the socket whose local
    port is ``port`` and remote port ``served``, then the process with that socket among
    its descriptors. ``None`` where the host keeps no such table or no process is found.
    """
    inode = None
    for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        if not table.is_file():
            continue
        for line in table.read_text(encoding="ascii").splitlines()[1:]:
            fields = line.split()
            local, remote = (int(end.rpartition(":")[2], 16) for end in fields[1:3])
            if (local, remote) == (port, served):
                inode = fields[9]
    if inode is None:
        return None
    held = f"socket:[{inode}]"
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            descriptors = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            continue
        for descriptor in descriptors:
            # A descriptor another process closes while it is read is skipped alone.
            try:
                if os.readlink(f"/proc/{pid}/fd/{descriptor}") == held:
                    return int(pid)
            except OSError:
                continue
    return None


def _carried(answer: Any) -> set[str]:
    """The id of every issue, project and document an answer carries, at any depth.

    Read off the fields only such an item carries — an issue's `identifier`, a project's
    `name`, a document's `title` beside its `content` — so a reference, a state or a
    comment is not counted as a read of its item.
    """
    found: set[str] = set()
    match answer:
        case dict():
            native = answer.get("id")
            if isinstance(native, str) and (
                "identifier" in answer
                or "name" in answer
                and "content" in answer
                or "title" in answer
                and "content" in answer
            ):
                found.add(native)
            for value in answer.values():
                found |= _carried(value)
        case list():
            for value in answer:
                found |= _carried(value)
    return found


def _matches(node: dict[str, Any], query_filter: Any) -> bool:
    """Whether ``node`` passes a GraphQL filter, read for the members a copy narrows by.

    `and`, `or`, a text field's `contains`, `containsIgnoreCase` and `eq`, and a project's
    `id.eq`; a member this board does not model — the team scope among them, which every
    item here is in — narrows nothing.
    """
    if not isinstance(query_filter, dict):
        return True
    for member, condition in query_filter.items():
        match member:
            case "and":
                if not all(_matches(node, part) for part in condition):
                    return False
            case "or":
                if not any(_matches(node, part) for part in condition):
                    return False
            case "title" | "description":
                text = node.get(member) or ""
                for comparator, value in condition.items():
                    held = {
                        "contains": value in text,
                        "containsIgnoreCase": value.lower() in text.lower(),
                        "eq": text == value,
                    }.get(comparator, True)
                    if not held:
                        return False
            case "project" if "id" in condition:
                if (node.get("project") or {}).get("id") != condition["id"].get("eq"):
                    return False
    return True


def _page(nodes: list[dict[str, Any]], first: Any) -> dict[str, Any]:
    """A connection of at most ``first`` of ``nodes``, saying whether more remain."""
    if not isinstance(first, int) or len(nodes) <= first:
        return _connection(nodes)
    page = _connection(nodes[:first])
    page["pageInfo"]["hasNextPage"] = True
    return page


def _in_project(nodes: list[dict[str, Any]], query_filter: Any) -> list[dict[str, Any]]:
    """The ``nodes`` a filter naming one project's id keeps; every node for any other filter."""
    project_filter = (query_filter or {}).get("project", {})
    if "id" not in project_filter:
        return nodes
    wanted = project_filter["id"]["eq"]
    return [node for node in nodes if (node.get("project") or {}).get("id") == wanted]


@contextmanager
def linear_board(*, rewrites: Sequence[Rewrite] = MEASURED_REWRITES) -> Iterator[LinearBoard]:
    """Serve only loopback, and close the socket and server thread with the context."""
    board = LinearBoard(rewrites)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            reset = board._refusal()
            if reset is not None:
                self.rfile.read(int(self.headers["Content-Length"]))
                refused = json.dumps(
                    {
                        "errors": [
                            {
                                "message": "Rate limit exceeded",
                                "extensions": {"code": "RATELIMITED"},
                            }
                        ]
                    }
                ).encode()
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.send_header("X-RateLimit-Requests-Reset", str(reset))
                self.send_header("Content-Length", str(len(refused)))
                self.end_headers()
                self.wfile.write(refused)
                return
            try:
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                answer: dict[str, Any] = {
                    "data": board.dispatch(
                        payload["query"],
                        payload.get("variables") or {},
                        payload.get("operationName"),
                        _client(int(self.client_address[1]), board.port),
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
    board.port = server.server_port
    board.endpoint = f"http://127.0.0.1:{board.port}/graphql"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield board
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
