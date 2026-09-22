"""The one source of a drafted follow-up's shape: rendered, parsed, and validated here.

A **draft** is an unverified follow-up — something a worker, the monitor, the pacemaker or
the manager noticed and deliberately left out of scope — recorded as a `local-md` task in
the `drafts` source, under a project of its own per run, so it outlives the worktree the
noticing happened in. Nothing about a draft is verified: that is the follow-up agent's job
after the run, and it reads every draft back through this module.

**Three callers, one shape.** `scripts/follow-up-draft.sh` is what every dispatch, the
monitor and the pacemaker run through `$ORCHESTRATOR_FOLLOW_UP_DRAFT`; `just follow-up` is
the manager's spelling of the same script; and the follow-up agent's recipe parses what
they wrote with :func:`parse` or :func:`from_store_item`. So what a draft *is* — its path,
its frontmatter, its body headings, the pointer back to the turns it came from — is decided
by the functions below and by nothing else, and a reader and a writer cannot disagree
about it.

**The stamping is the machinery's, never the agent's.** An agent supplies a title, the
repository the follow-up is about, the paths inside it, and a body. Everything that says
where the draft came from is read off this process instead — the run, the dispatch's
scratch and session, the working directory and its git state, and the node — because a
pointer an agent had to remember is a pointer that is missing exactly when it matters.

**The node is resolved by process ancestry**, because the engine exports no node id to a
dispatch. It records every dispatch it starts under `<runs root>/<run>/dispatches/*.json` —
the node, the process, and the kernel's start time for that process — so the draft's own
process walks up its parents until one is a recorded dispatch. A pid match is accepted only
when the recorded start time is the live process's own: a host that has been up for weeks
has reused every pid a stale entry names, and naming the wrong node is worse than naming
none. When nothing matches, the draft says `unresolved` and is still written.
`tests/test_engine_contracts.py` holds every registry name read here to the engine's own.

**The transcript pointer is a command a reader runs**, taken against onepipeline 0.29.0:

- a node's own turns are `onepipeline transcript <run> <node>`, and a draft whose node is
  unresolved points at `onepipeline transcript <run>`, which reads every node that
  dispatched;
- a dag-scope member records no transcript of its own — `transcript` refuses a member name
  as a node the run has no records for — so a monitor or pacemaker draft points at the run's
  event stream narrowed to that member's `member` label, which the engine's filter grammar
  matches by exact equality: `onepipeline monitor <run> --filter
  '{"include":[{"member":"<member>"}]}'`. `tests/plan_tooling/test_follow_up_drafts_e2e.py`
  runs that command against a recorded run and holds it to showing that member alone;
- a manager draft points at nothing, because a manager's own session is not a record the
  run keeps.

**The root's environment name is not spelled here.** `scripts/follow-up-env.sh` composes
it, and a second composition is refused by `tests/test_follow_up_draft_composition.py`, so
:func:`main` is handed the name by the script that sources that helper.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import count
from pathlib import Path, PurePosixPath
from typing import Literal, NoReturn, TypeGuard, get_args

from orchestrator.plan_store import RECORD_COMPONENT
from orchestrator.project_store import frontmatter, hosted_origin

#: The plan-store source a draft is stored in, as `onetaskgraph.yaml` names it.
SOURCE = "drafts"

#: The version of the record below. A reader refuses any other, because a draft is a stored
#: shape that outlives the code that wrote it.
SCHEMA = 1

#: The metadata key a draft's record sits under, and the one its run's project carries.
DRAFT_KEY = "orchestrator.follow-up-draft"
PROJECT_KEY = "orchestrator.follow-up-drafts"

#: The status every draft and every draft project is written with. A draft is not work
#: anybody has started; verifying it is the follow-up agent's.
STATUS = "todo"

#: The directory under a run's task directory that drafts are written into, and the one
#: reserved beside it for the follow-up agent's verified tickets, which nothing here writes.
DRAFTS = "drafts"
TICKETS = "tickets"

#: How long a title may be, and how much of it a draft's file name carries.
TITLE_LIMIT = 120
SLUG_LIMIT = 48

#: What a draft id's slug is when a title has no letter or digit to make one from.
EMPTY_SLUG = "follow-up"

#: The level-2 headings a draft's body carries, in this order, each with content.
HEADINGS = ("What happened", "Where", "Why it is out of scope", "Evidence")

#: Who may write a draft. `node` is a dispatched worker; `monitor` and `pacemaker` are the
#: dag-scope members that watch a run; `manager` is the session supervising it.
Kind = Literal["node", "monitor", "pacemaker", "manager"]
KINDS: tuple[Kind, ...] = get_args(Kind)

#: The kinds that are a dag-scope member, and so name one.
MEMBER_KINDS = ("monitor", "pacemaker")

#: What a node draft names when no recorded dispatch is an ancestor of the drafting process.
UNRESOLVED = "unresolved"

#: What the stamp reads out of the environment the engine gives a dispatch.
RUN_ID_ENV = "ONEPIPELINE_RUN_ID"
NODE_SCRATCH_ENV = "ONEPIPELINE_NODE_SCRATCH_DIR"
SESSION_ENV = "ONEVCS_SESSION"

#: How `onepipeline` is told where its runs live, and where they are when nothing says.
RUNS_ROOT_ENV = "ONEPIPELINE_RUNS_DIR"
DEFAULT_RUNS_ROOT = "runs"

#: The dispatch registry: where under a run it is, the three fields of an entry this reads,
#: and how an entry spells the kernel start time it recorded. Each, and the two runs-root
#: names above, is held to the installed engine by `tests/test_engine_contracts.py`.
DISPATCH_REGISTRY = "dispatches"
DISPATCH_NODE = "node"
DISPATCH_PID = "pid"
DISPATCH_STARTED = "started"
PROC_STAT_START = "linux-proc-stat:"

#: The engine binary a transcript pointer names.
ENGINE = "onepipeline"

#: What this command calls itself in its diagnostics, and how its usage names it.
PROG = "follow-up-draft"
USAGE_NAME = '"$ORCHESTRATOR_FOLLOW_UP_DRAFT"'

#: Exit statuses: a refusal, which says what to do next, and a write the filesystem refused.
REFUSED = 2
FAILED = 1

#: A dag-scope member name, as a graph document may spell one.
MEMBER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")

#: `drafted_at`, in the one RFC 3339 spelling this writes: UTC, to the second. The pattern
#: holds a stored value to that spelling and the format holds it to a real calendar time.
DRAFTED_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
DRAFTED_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

#: A character no one-line value may carry: a line break forges a frontmatter line, and the
#: rest move a terminal's cursor when a supervisor reads the value back.
CONTROL = re.compile(r"[\x00-\x1f\x7f]")

#: A level-2 ATX heading, and the fence lines inside which a `## ` line is code, not one.
HEADING = re.compile(r"## +(?P<name>.*?)(?: +#+)? *")
FENCE = re.compile(r" {0,3}(?P<marker>`{3,}|~{3,})")

#: The keys of each mapping in the record, in the order they are written.
RECORD_KEYS = (
    "schema",
    "run",
    "author",
    "dispatch",
    "transcript",
    "repository",
    "paths",
    "drafted_at",
)
AUTHOR_KEYS = ("kind", "node", "member")
DISPATCH_KEYS = ("scratch", "session", "cwd", "branch", "head")

#: The one-line lines this module's frontmatter is made of: a top-level field, and an entry
#: of the `metadata` block. Both values are JSON, which is what the renderer writes.
TOP_LEVEL = re.compile(r"(?P<key>[a-z_]+): (?P<value>.+)")
TOP_LEVEL_KEYS = ("title", "status", "project")
METADATA_ENTRY = re.compile(r'  (?P<key>"(?:[^"\\]|\\.)*"): (?P<value>.+)')

#: The sentence every refusal of a stored record ends on, naming where the shape is stated.
SHAPE = f"a draft is written by {USAGE_NAME}, whose --help states the whole shape"


class Refused(ValueError):
    """A draft, or an input to one, without the shape this module writes; the message says why."""


@dataclass(frozen=True)
class Author:
    """Who drafted a follow-up."""

    kind: Kind
    #: The node id for a `node` draft — or :data:`UNRESOLVED` — and `None` for every other.
    node: str | None
    #: The dag-scope member for a `monitor` or `pacemaker` draft, and `None` for every other.
    member: str | None


@dataclass(frozen=True)
class DispatchStamp:
    """Where the drafting process was running, as the machinery read it."""

    scratch: str | None
    session: str | None
    cwd: str
    branch: str | None
    head: str | None


@dataclass(frozen=True)
class Draft:
    """One drafted follow-up, every field validated."""

    title: str
    run: str
    author: Author
    dispatch: DispatchStamp
    repository: str
    paths: tuple[str, ...]
    drafted_at: str
    body: str

    @property
    def transcript(self) -> str | None:
        """The command that reads this draft's turns, derived rather than stored twice."""
        return transcript_for(self.run, self.author)

    @property
    def stem(self) -> str:
        """This draft's id before any collision suffix: its time, then its title's slug."""
        slug = re.sub(r"[^a-z0-9]+", "-", self.title.lower()).strip("-")
        slug = slug[:SLUG_LIMIT].strip("-") or EMPTY_SLUG
        return f"{self.drafted_at.replace('-', '').replace(':', '')}-{slug}"


def checked_run(run: str) -> str:
    """``run`` when it can name a draft project, which is one path component."""
    if not RECORD_COMPONENT.fullmatch(run):
        raise Refused(
            f"run id {run!r} cannot name a draft project, which is one path component of "
            "letters, digits and `_.@+-`; pass the id of the run this follow-up came from"
        )
    return run


def checked_title(title: str) -> str:
    """``title`` without its surrounding whitespace, when it is one line a draft can hold."""
    stated = title.strip()
    if not stated:
        raise Refused("the title is empty; pass --title with one line naming the follow-up")
    if CONTROL.search(stated):
        raise Refused(
            "the title carries a line break or another control character; pass one plain "
            "line as --title and move the detail into the body"
        )
    if len(stated) > TITLE_LIMIT:
        raise Refused(
            f"the title is {len(stated)} characters, over the {TITLE_LIMIT} a draft's title "
            "may hold; shorten it and move the detail into the body"
        )
    return stated


def checked_repository(repository: str) -> str:
    """``repository`` as the normalized origin `orchestrator/project_store.py` decides."""
    origin = hosted_origin(repository.strip())
    if origin is None:
        raise Refused(
            f"repository {repository!r} is not a normalized origin; pass the repository the "
            "follow-up is about as host/owner/name, like github.com/nickderobertis/ai-orchestrator"
        )
    return origin


def checked_paths(paths: Sequence[str]) -> tuple[str, ...]:
    """``paths`` when every one is a path inside a repository, relative to its root."""
    for path in paths:
        if (
            not path
            or path.startswith("/")
            or CONTROL.search(path)
            or ".." in PurePosixPath(path).parts
        ):
            raise Refused(
                f"path {path!r} is not a path inside the repository; pass each --path "
                "relative to that repository's root, with no `..` and no leading `/`"
            )
    return tuple(paths)


def sections(body: str) -> list[tuple[str, str]]:
    """Every level-2 heading of ``body`` outside a code fence, with the text under it.

    Public because a verified ticket's body is held to its headings the same way, by
    `orchestrator/follow_up_tickets.py`, and a second reading of a fence would be a second
    answer to which `## ` line is a heading.
    """
    found: list[tuple[str, list[str]]] = []
    fence: str | None = None
    for line in body.split("\n"):
        opened = FENCE.match(line)
        if opened is not None:
            marker = opened["marker"]
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
        elif fence is None and (heading := HEADING.fullmatch(line)) is not None:
            found.append((heading["name"], []))
            continue
        if found:
            found[-1][1].append(line)
    return [(name, "\n".join(lines)) for name, lines in found]


def checked_body(body: str) -> str:
    """``body`` trimmed, when it carries every required heading in order, each with content."""
    found = sections(body)
    names = [name for name, _ in found]
    after = 0
    for required in HEADINGS:
        try:
            at = names.index(required, after)
        except ValueError:
            raise Refused(
                f"the body carries no `## {required}` heading"
                + (f" after `## {HEADINGS[HEADINGS.index(required) - 1]}`" if after else "")
                + "; a draft's body carries `## What happened`, `## Where`, `## Why it is out "
                "of scope` and `## Evidence`, in that order, each with content"
            ) from None
        if not found[at][1].strip():
            raise Refused(
                f"the body's `## {required}` section is empty; say under it what the draft "
                "needs a verifier to read"
            )
        after = at + 1
    return body.strip()


def _is_kind(kind: str) -> TypeGuard[Kind]:
    return kind in KINDS


def checked_author(kind: str, node: str | None, member: str | None) -> Author:
    """``kind`` with the node and member that kind names, and nothing it does not."""
    if not _is_kind(kind):
        raise Refused(
            f"{kind!r} names no author kind; pass --as node, monitor, pacemaker or manager"
        )
    checked_member(kind, member)
    # llmlint: ignore[boundary_inputs_validated] A node id's grammar is the engine's, and it
    # accepts `/` in lifecycle ids, so a character set spelled here would refuse real ids.
    # Every caller has already refused an empty or control-bearing value (`_optional_text`,
    # `registered_dispatches`), and `transcript_for` quotes the id with `shlex.join`.
    if (kind == "node") != isinstance(node, str) or node == "":
        raise Refused(f"a {kind} draft names {node!r} as its node; {SHAPE}")
    return Author(kind=kind, node=node, member=member)


def checked_member(kind: Kind, member: str | None) -> None:
    """Refuse a ``member`` that ``kind`` does not name, or a missing one it does."""
    if kind in MEMBER_KINDS:
        if member is None:
            raise Refused(
                f"--as {kind} needs --member naming the dag-scope member drafting it: "
                "`monitor` for the monitor, `check-in` for the pacemaker"
            )
        if not MEMBER_NAME.fullmatch(member):
            raise Refused(
                f"--member {member!r} is not a dag-scope member name; pass the member's name "
                "as its graph document spells it"
            )
    elif member is not None:
        raise Refused(
            f"--member names a dag-scope member, and --as {kind} is not one; drop --member, "
            "or pass --as monitor or --as pacemaker"
        )


def transcript_for(run: str, author: Author) -> str | None:
    """The command that reads the turns ``author`` drafted from, or `None` for a manager."""
    if author.kind == "manager":
        return None
    if author.member is not None:
        narrowed = json.dumps({"include": [{"member": author.member}]}, separators=(",", ":"))
        return shlex.join([ENGINE, "monitor", run, "--filter", narrowed])
    arguments = [ENGINE, "transcript", run]
    if author.node is not None and author.node != UNRESOLVED:
        arguments.append(author.node)
    return shlex.join(arguments)


def record(draft: Draft) -> dict[str, object]:
    """The metadata a draft is stored under, every key present."""
    return {
        "schema": SCHEMA,
        "run": draft.run,
        "author": {
            "kind": draft.author.kind,
            "node": draft.author.node,
            "member": draft.author.member,
        },
        "dispatch": {
            "scratch": draft.dispatch.scratch,
            "session": draft.dispatch.session,
            "cwd": draft.dispatch.cwd,
            "branch": draft.dispatch.branch,
            "head": draft.dispatch.head,
        },
        "transcript": draft.transcript,
        "repository": draft.repository,
        "paths": list(draft.paths),
        "drafted_at": draft.drafted_at,
    }


def render(draft: Draft) -> str:
    """One draft as the `local-md` record it is stored as."""
    return frontmatter(
        {
            "title": draft.title,
            "status": STATUS,
            "project": draft.run,
            "metadata": {DRAFT_KEY: record(draft)},
        },
        draft.body,
    )


def render_project(run: str) -> str:
    """The project every draft of ``run`` belongs to."""
    return frontmatter(
        {
            "title": f"Follow-ups drafted in run {run}",
            "status": STATUS,
            "metadata": {PROJECT_KEY: {"schema": SCHEMA, "run": run}},
        },
        f"Unverified follow-ups drafted during run {run}.",
    )


def parse(text: str) -> Draft:
    """A draft record as :func:`render` writes it, validated; :class:`Refused` otherwise."""
    lines = text.split("\n")
    if lines[0] != "---":
        raise Refused(f"the draft record does not open with a `---` fence; {SHAPE}")
    try:
        closing = lines.index("---", 1)
    except ValueError:
        raise Refused(f"the draft record's frontmatter is never closed; {SHAPE}") from None
    fields: dict[str, object] = {}
    metadata: dict[str, object] = {}
    in_metadata = False
    for line in lines[1:closing]:
        if line == "metadata:" and not in_metadata:
            in_metadata = True
            continue
        matched = (METADATA_ENTRY if in_metadata else TOP_LEVEL).fullmatch(line)
        if matched is None:
            raise Refused(f"the draft record holds a line this cannot read, {line!r}; {SHAPE}")
        held, known = (metadata, (DRAFT_KEY,)) if in_metadata else (fields, TOP_LEVEL_KEYS)
        try:
            value = json.loads(matched["value"])
            key = json.loads(matched["key"]) if in_metadata else matched["key"]
        except json.JSONDecodeError:
            raise Refused(
                f"the draft record holds a value that is not JSON, {line!r}; {SHAPE}"
            ) from None
        # Refused rather than kept or overwritten: a record stating a field twice, or one
        # this never writes, is not the record a reader of the kept value thinks it is.
        if key not in known:
            raise Refused(f"the draft record holds a field this does not write, {key!r}; {SHAPE}")
        if key in held:
            raise Refused(f"the draft record states its {key!r} field twice; {SHAPE}")
        held[key] = value
    return _validated(
        title=fields.get("title"),
        status=fields.get("status"),
        project=fields.get("project"),
        metadata=metadata,
        body="\n".join(lines[closing + 1 :]),
    )


def from_store_item(item: Mapping[str, object]) -> Draft:
    """A draft as `onetaskgraph task show --json` reports its `item`, validated."""
    status = item.get("status")
    if isinstance(status, Mapping):
        status = status.get("name")
    metadata = item.get("metadata")
    return _validated(
        title=item.get("title"),
        status=status,
        project=item.get("project"),
        metadata=metadata if isinstance(metadata, Mapping) else {},
        body=item.get("content"),
    )


def _mapping(value: object, keys: Sequence[str], what: str) -> Mapping[str, object]:
    """``value`` when it is a mapping holding exactly ``keys``."""
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise Refused(f"the draft record's {what} is not a mapping of {', '.join(keys)}; {SHAPE}")
    return value


def _optional_text(value: object, what: str) -> str | None:
    """A stored one-line stamp: `None`, or a string the writer could have stamped."""
    if value is not None and not isinstance(value, str):
        raise Refused(f"the draft record's {what} is neither a string nor null; {SHAPE}")
    # The writer stamps an empty or control-bearing value as null (`_one_line`), so a stored
    # one is a record that did not come from it.
    if value is not None and (not value or CONTROL.search(value)):
        raise Refused(f"the draft record's {what} is empty or carries a control character; {SHAPE}")
    return value


def _real_time(value: str) -> bool:
    """Whether ``value``, already in the stored spelling, names a real calendar time."""
    try:
        datetime.strptime(value, DRAFTED_AT_FORMAT)  # noqa: DTZ007 - the spelling is UTC's own `Z`
    except ValueError:
        return False
    return True


def _validated(
    *,
    title: object,
    status: object,
    project: object,
    metadata: Mapping[str, object],
    body: object,
) -> Draft:
    """Every field of a stored draft held to the shape :func:`render` writes."""
    if not isinstance(title, str) or not isinstance(body, str):
        raise Refused(f"the draft record has no string title and body; {SHAPE}")
    if status != STATUS:
        raise Refused(f"the draft record's status is {status!r} rather than {STATUS!r}; {SHAPE}")
    held = _mapping(metadata.get(DRAFT_KEY), RECORD_KEYS, f"`{DRAFT_KEY}` metadata")
    if type(held["schema"]) is not int or held["schema"] != SCHEMA:
        raise Refused(
            f"the draft record is schema {held['schema']!r}, and this reads schema {SCHEMA}; "
            f"{SHAPE}"
        )
    run = held["run"]
    if not isinstance(run, str) or run != project:
        raise Refused(f"the draft record's run {run!r} is not its project {project!r}; {SHAPE}")
    stated_author = _mapping(held["author"], AUTHOR_KEYS, "author")
    kind = stated_author["kind"]
    if not isinstance(kind, str):
        raise Refused(f"the draft record's author kind is not a string; {SHAPE}")
    stated_dispatch = _mapping(held["dispatch"], DISPATCH_KEYS, "dispatch")
    cwd = stated_dispatch["cwd"]
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise Refused(f"the draft record's dispatch cwd is not an absolute path; {SHAPE}")
    repository = held["repository"]
    if not isinstance(repository, str) or hosted_origin(repository) != repository:
        raise Refused(f"the draft record's repository is not a normalized origin; {SHAPE}")
    paths = held["paths"]
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        raise Refused(f"the draft record's paths are not a list of strings; {SHAPE}")
    drafted_at = held["drafted_at"]
    if (
        not isinstance(drafted_at, str)
        or not DRAFTED_AT.fullmatch(drafted_at)
        or not _real_time(drafted_at)
    ):
        raise Refused(f"the draft record's drafted_at is not an RFC 3339 UTC time; {SHAPE}")
    if checked_title(title) != title:
        raise Refused(f"the draft record's title is not trimmed; {SHAPE}")
    draft = Draft(
        title=title,
        run=checked_run(run),
        author=checked_author(
            kind,
            _optional_text(stated_author["node"], "author node"),
            _optional_text(stated_author["member"], "author member"),
        ),
        dispatch=DispatchStamp(
            scratch=_optional_text(stated_dispatch["scratch"], "dispatch scratch"),
            session=_optional_text(stated_dispatch["session"], "dispatch session"),
            cwd=cwd,
            branch=_optional_text(stated_dispatch["branch"], "dispatch branch"),
            head=_optional_text(stated_dispatch["head"], "dispatch head"),
        ),
        repository=repository,
        paths=checked_paths(paths),
        drafted_at=drafted_at,
        body=checked_body(body),
    )
    if held["transcript"] != draft.transcript:
        raise Refused(
            f"the draft record's transcript {held['transcript']!r} is not the read its author "
            f"has, {draft.transcript!r}; {SHAPE}"
        )
    return draft


@dataclass(frozen=True)
class RegisteredDispatch:
    """One entry of a run's dispatch registry that can be checked against a live process."""

    node: str
    #: The kernel start time the entry recorded, without its spelling's prefix.
    started: str


def registered_dispatches(runs_root: Path, run: str) -> dict[int, RegisteredDispatch]:
    """Every dispatch ``run`` records with a start time this can check, by process.

    An entry recording no start time, or one in a spelling this does not read, is left
    out rather than trusted: a pid alone names whichever process the kernel handed it to
    last.
    """
    by_process: dict[int, RegisteredDispatch] = {}
    for entry in sorted((runs_root / run / DISPATCH_REGISTRY).glob("*.json")):
        try:
            recorded = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(recorded, dict):
            continue
        pid = recorded.get(DISPATCH_PID)
        node = recorded.get(DISPATCH_NODE)
        started = recorded.get(DISPATCH_STARTED)
        # llmlint: ignore[boundary_inputs_validated] The engine wrote this node id and owns
        # its grammar, which admits `/`; this refuses what could forge a stored line or move
        # a reader's cursor, and `transcript_for` quotes the id with `shlex.join`.
        if (
            type(pid) is int
            and isinstance(node, str)
            and node
            and not CONTROL.search(node)
            and isinstance(started, str)
            and started.startswith(PROC_STAT_START)
            and started.removeprefix(PROC_STAT_START).isdigit()
        ):
            by_process[pid] = RegisteredDispatch(node, started.removeprefix(PROC_STAT_START))
    return by_process


def process_started(pid: int) -> str | None:
    """The kernel's own start time for a process, or `None` when it is gone.

    Everything after the last `)`, because the field before it is the process name and is
    the one field that can itself carry a bracket or a space.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    _, closed, after = stat.rpartition(")")
    fields = after.split() if closed else []
    return fields[19] if len(fields) > 19 else None  # pragma: no branch - the kernel writes 52


def process_parent(pid: int) -> int | None:
    """The parent of a process, or `None` when it is gone."""
    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in status.splitlines():
        if line.startswith("PPid:"):
            stated = line.split(":", 1)[1].strip()
            return int(stated) if stated.isdigit() else None
    return None  # pragma: no cover - every /proc/<pid>/status carries PPid


def lineage(pid: int) -> list[int]:
    """``pid`` and every process it descends from, nearest first, stopping short of init."""
    walked = [pid]
    at = process_parent(pid)
    while at is not None and at > 1 and at not in walked:
        walked.append(at)
        at = process_parent(at)
    return walked


def resolve_node(run: str, *, runs_root: Path | None, pid: int) -> str:
    """The node whose recorded dispatch ``pid`` descends from, or :data:`UNRESOLVED`.

    A runs root of `None` is one this process could not name, and so holds no registry.
    """
    if runs_root is None:
        return UNRESOLVED
    registered = registered_dispatches(runs_root, run)
    for at in lineage(pid):
        found = registered.get(at)
        if found is not None and process_started(at) == found.started:
            return found.node
    return UNRESOLVED


def runs_root() -> Path | None:
    """The runs root this process's engine records its runs under, or `None`.

    `None` when the environment names one carrying a control character, which is not a
    path the engine wrote runs under; falling back to the default instead would read a
    different store as if it were this one.
    """
    named = os.environ.get(RUNS_ROOT_ENV) or DEFAULT_RUNS_ROOT
    if CONTROL.search(named):
        return None
    return Path(named).expanduser().absolute()


def _git(cwd: Path, *arguments: str) -> str | None:
    """One line git answers about ``cwd``, or `None` when it answers nothing."""
    try:
        answered = subprocess.run(  # noqa: S603 - git, reading the directory a draft came from
            ["git", "-C", str(cwd), *arguments],  # noqa: S607 - git as the drafting process finds it
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None
    stated = answered.stdout.strip()
    return stated if answered.returncode == 0 and stated else None


def _one_line(value: str | None) -> str | None:
    """An environment value fit to stamp on a draft, or `None` for an absent or unfit one.

    The environment is the drafting process's, and a value carrying a line break or
    another control character is not the path or token it claims to be — so it is left
    unstamped rather than persisted into a record a verifier then reads.
    """
    return value if value and not CONTROL.search(value) else None


def stamp_dispatch(cwd: Path) -> DispatchStamp:
    """Where this process is running, read off its environment and its working directory."""
    return DispatchStamp(
        scratch=_one_line(os.environ.get(NODE_SCRATCH_ENV)),
        session=_one_line(os.environ.get(SESSION_ENV)),
        cwd=str(cwd),
        branch=_git(cwd, "symbolic-ref", "--quiet", "--short", "HEAD"),
        head=_git(cwd, "rev-parse", "--verify", "--quiet", "HEAD"),
    )


def _publish(directory: Path, name: str, content: str) -> bool:
    """Create ``directory/name`` holding ``content``, whole, unless it already exists.

    Written to a temporary file first and linked into place, because a `local-md` source
    reads every record in a directory and a reader that met a half-written one would refuse
    the whole walk; and linked rather than renamed, because a link refuses an existing name
    where a rename would replace it.
    """
    handle, temporary = tempfile.mkstemp(dir=directory, prefix=".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as opened:
            opened.write(content)
        try:
            os.link(temporary, directory / name)
        except FileExistsError:
            return False
        return True
    finally:
        os.unlink(temporary)


def _names(stem: str) -> Iterator[str]:
    yield stem
    for suffix in count(2):  # pragma: no branch - a count never runs out
        yield f"{stem}-{suffix}"


def write(root: Path, draft: Draft) -> tuple[str, Path]:
    """Store ``draft`` under ``root``, never over another; its qualified id and its path.

    The draft first and its run's project after, because a `local-md` source that finds a
    project opens the task directory below it and refuses one that is not there.
    """
    directory = root / "tasks" / draft.run / DRAFTS
    directory.mkdir(parents=True, exist_ok=True)
    rendered = render(draft)
    name = next(name for name in _names(draft.stem) if _publish(directory, f"{name}.md", rendered))
    projects = root / "projects"
    projects.mkdir(exist_ok=True)
    _publish(projects, f"{draft.run}.md", render_project(draft.run))
    return f"{SOURCE}:{draft.run}/{DRAFTS}/{name}", directory / f"{name}.md"


def ensure_draft_root(root_env: str) -> Path:
    """The directory the launch exported for drafts, made writable, or a refusal."""
    named = os.environ.get(root_env, "")
    if not named:
        raise Refused(
            f"the draft root is unset, because {root_env} names no directory; draft from "
            "inside a launch, which exports it, or through `just follow-up`, which exports "
            "it the way a launch does"
        )
    if CONTROL.search(named):
        raise Refused(
            f"the draft root {named!r} carries a control character, so it is not the directory "
            f"a launch exports; point {root_env} at that directory"
        )
    root = Path(named)
    if not root.is_absolute():
        raise Refused(
            f"the draft root {named!r} is not an absolute path, so it would resolve against "
            f"this process's working directory; point {root_env} at the directory a launch "
            "exports"
        )
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise Refused(
            f"the draft root {root} could not be created ({exc}); point {root_env} at a "
            "directory this process may write into"
        ) from None
    if not os.access(root, os.W_OK | os.X_OK):
        raise Refused(
            f"the draft root {root} is not a directory this process may write into; point "
            f"{root_env} at one it may"
        )
    return root


class _Parser(argparse.ArgumentParser):
    """A parser whose refusals exit with :data:`REFUSED` and say what to do next."""

    def error(self, message: str) -> NoReturn:
        self.exit(REFUSED, f"{PROG}: refused: {message}; run it with --help for the contract\n")


HELP = f"""\
Record one unverified draft of a NON-BLOCKING follow-up: something you noticed and
deliberately left out of scope, which can wait to be verified after this run. It is
stored as a local Markdown task in the `{SOURCE}` plan source, under the run's own draft
project, and outlives the worktree you are in.

Only a follow-up that can wait belongs here. Anything blocking — a decision fork, a
constraint that cannot be met, a finding the manager should act on now — goes over the
channel immediately instead, and is never drafted in its place: a worker asks through
"$ORCHESTRATOR_ASK_MANAGER", the monitor raises a `finding`, and the pacemaker says it in
its check-in update.

The body is read from stdin, never from an argument, and carries these level-2 headings in
this order, each with content:

  ## {HEADINGS[0]}
  ## {HEADINGS[1]}
  ## {HEADINGS[2]}
  ## {HEADINGS[3]}

You supply the title, the repository, the paths and the body. This command stamps the
rest from where it runs:

  run                 --run, or {RUN_ID_ENV}
  author.kind         --as (default: node)
  author.node         for --as node, the dispatch this process descends from, read off the
                      run's dispatch registry under {RUNS_ROOT_ENV}; `{UNRESOLVED}` when none
  author.member       --member, for --as monitor and --as pacemaker
  dispatch.scratch    {NODE_SCRATCH_ENV}
  dispatch.session    {SESSION_ENV}
  dispatch.cwd        this process's working directory
  dispatch.branch     that directory's git branch
  dispatch.head       that directory's HEAD commit
  transcript          the command that reads the turns this draft came from
  drafted_at          now, in UTC

On success it prints one line naming the draft's qualified id and its path, and exits 0.
It refuses with exit {REFUSED}, writing nothing and naming what to do next, when no run can be
named, the draft root is unset or unwritable, the title is empty or longer than
{TITLE_LIMIT} characters, the repository is not a normalized origin, a heading is missing or
empty, or --member does not agree with --as.
"""


def _parser() -> _Parser:
    parser = _Parser(
        prog=USAGE_NAME,
        description=HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--title", required=True, help="one line naming the follow-up")
    parser.add_argument(
        "--repository",
        required=True,
        metavar="HOST/OWNER/NAME",
        help="the normalized origin of the repository the follow-up is about",
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="a path inside that repository the follow-up is about; repeat for several",
    )
    parser.add_argument(
        "--as",
        dest="kind",
        choices=KINDS,
        default="node",
        help="who is drafting: a dispatched node, the monitor, the pacemaker, or the manager",
    )
    parser.add_argument(
        "--member",
        metavar="NAME",
        help="the dag-scope member drafting it; required for --as monitor and --as pacemaker",
    )
    parser.add_argument(
        "--run", metavar="RUN-ID", help=f"the run it came from; default {RUN_ID_ENV}"
    )
    return parser


def prepare(arguments: argparse.Namespace, root_env: str) -> tuple[Draft, Path]:
    """The draft these arguments and this process describe, and the root it goes under.

    Every refusal is decided before anything is written, which is what lets a refused draft
    leave the store exactly as it found it.
    """
    kind: Kind = arguments.kind
    member: str | None = arguments.member
    checked_member(kind, member)
    run = arguments.run or os.environ.get(RUN_ID_ENV) or ""
    if not run:
        raise Refused(
            f"no run can be named; pass --run RUN-ID, or draft from inside a dispatch, which "
            f"carries {RUN_ID_ENV}"
        )
    run = checked_run(run)
    title = checked_title(arguments.title)
    repository = checked_repository(arguments.repository)
    paths = checked_paths(arguments.path)
    if sys.stdin.isatty():
        raise Refused(
            "the body is read from stdin, and stdin is a terminal; pipe in a body carrying "
            "the four headings --help names"
        )
    body = checked_body(sys.stdin.read())
    try:
        cwd = Path.cwd()
    except OSError as exc:
        raise Refused(
            f"this process's working directory could not be read ({exc}); run it from a "
            "directory that exists"
        ) from None
    root = ensure_draft_root(root_env)
    node = resolve_node(run, runs_root=runs_root(), pid=os.getpid()) if kind == "node" else None
    draft = Draft(
        title=title,
        run=run,
        author=checked_author(kind, node, member),
        dispatch=stamp_dispatch(cwd),
        repository=repository,
        paths=paths,
        drafted_at=datetime.now(UTC).strftime(DRAFTED_AT_FORMAT),
        body=body,
    )
    return draft, root


def main(argv: Sequence[str], *, root_env: str) -> int:
    """Draft one follow-up; ``root_env`` is the variable its root is exported under."""
    arguments = _parser().parse_args(argv)
    try:
        draft, root = prepare(arguments, root_env)
    except Refused as refusal:
        print(f"{PROG}: refused: {refusal}", file=sys.stderr)
        return REFUSED
    try:
        qualified, path = write(root, draft)
    except OSError as exc:
        print(
            f"{PROG}: the draft could not be written under {root}: {exc}; repair that "
            "directory, then draft it again",
            file=sys.stderr,
        )
        return FAILED
    print(f"drafted {qualified} at {path}")
    return 0
