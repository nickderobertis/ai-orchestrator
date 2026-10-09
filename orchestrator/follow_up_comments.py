"""People's board comments on follow-up tickets, each routed to the one run that answers it.

`just follow-ups-answer-comments [--dry-run] [--run RUN-ID | --issue QUALIFIED-ID] [--to SOURCE]
[--since RFC3339] [--detach]` is how a comment somebody writes on the `followups` board
reaches the run that owns the issue it sits on. `scripts/follow-ups-answer-comments.sh` is a
thin script over the three commands here: :func:`gather` reads the board and writes one
feedback file per run, the script launches `just follow-ups <run> --feedback FILE --comments`
for every run at once and watches them, and :func:`settle` checks each run's account and
reports every reply. This module is the one statement of which comments are gathered; the
documents point here.

**What is read.** Only the items the pass is about, and never an unrelated item's comments,
because every read spends the board's share of an allowance every session on the host shares.

* **`--issue QUALIFIED-ID`** reads that one item by id, `task show` and its `task comment
  list`, and lists nothing. Its comments go to the run under this drafts root whose ticket's
  `board_item` binding (:data:`follow_up_tickets.BINDING_FIELD`) names it, found by one
  narrowed listing of the local drafts store; an item no run of this host owns — none bound,
  several bound, or the board's record naming another creator or host — is refused with why,
  and nothing is launched.
* **`--run RUN-ID`** reads, the same way, each item that run's local tickets are bound to, and
  lists no board.
* **Unscoped**, every board of the family `--to` roots (:func:`follow_up_tickets.boards`:
  `followups` and the source its route files a `petsinc` root cause's ticket in) is asked one
  query the store narrows to this host's items before any comment is read,
  `task_list(source=[<board>], commented_since=<its since>, metadata=[<record>/host=<this
  hostname>])` (:func:`host_query`), read through :func:`plan_store.every_page` to its last
  page, and `task comment list` for each item that answer holds and no other. No status filter
  is sent, because a comment on an item at another status is still reported, skipped with its
  reason. The store's `commented_since` keeps an item when one of its comments was created or
  last edited at or after the instant, exactly, on every source this reads, so a comment edited
  after its reply comes back through the same query.

**`<since>`** is decided per board, in order: `--since` as given; else that board's
**watermark**, the file :data:`WATERMARK` under `<drafts root>/feedback/`, one per board,
holding the instant the last complete gathering queried at less :data:`OVERLAP`; else the
earliest bound local records give a run that has filed tickets here — its recorded boundary,
else its ticket files' last write. A run whose records give neither is **unbounded**, and the
command refuses naming it and asking for `--since`, rather than ever querying from the epoch.
A scoped read takes no start: it reads its items whole. Only an unscoped gathering that is not
a dry run, and whose every launched run was checked sound by `check-responses` while it
waited, moves the watermark of every board it read; a `--run`, `--issue`, `--dry-run` or
`--detach` invocation that launched anything leaves each byte-identical. So a comment a
gathering selected that no reply answers is selected again: the next query starts no later
than the one that found it.

**Routing.** A comment goes to the run owning the issue it sits on
(:func:`follow_up_tickets.issue_owner`) and to no other. Each comment read is either selected
for that run or left out for exactly one reason, tried in this order; the first two are never
met unscoped, whose query the store narrowed to this host's records:

* **the no-answer rule** — the item carries no follow-up record, so no run answers on it;
* **ticket verified on host** — the record's `host` is not this machine's `hostname`;
* **an owning run no launch takes** — its id is outside `just follow-ups`' run-id grammar;
* **no records for the owning run** — nothing of that run is under this drafts root;
* **item at a status** — only an item at `Proposal` or `Deferred` (store categories `backlog`
  and `draft`; `Proposed` and `Backlog` on Linear) is answered; a person who moved an item on
  has decided about it;
* **marked** — a comment whose last line is any run's marker, of either kind, is not a
  person's;
* **bot author** — on a GitHub board, `github-actions` or a login ending `[bot]`; on a Linear
  one, a comment Linear names no user for, which an integration wrote. Linear records the user
  whose key made the request as a comment's author, so every comment a run posts there carries
  that user's name, and is told apart by its marker, the reason above;
* **answered** — a reply of **any** run names the comment's id in `answers` on the same issue
  and is not older than the comment's last change; a person editing a comment after its reply
  makes it unanswered again, since a comment is dated by its last edit;
* **the boundary** — a comment at or before the owning run's boundary predates replies and
  was answered by the run's other responses.

**The boundary** is the latest of the run's responses that are not replies — its ticket files'
last write, and its evidence comments — taken strictly before its first gathering. It is
recorded, never recomputed: every feedback file records the one its gathering used on a
:data:`FEEDBACK_BOUNDARY` line, and a gathering that computes one for a run writes it to
:data:`BOUNDARY_FILE` beside them, because a re-dispatch copies its ticket again and a
recomputed boundary would lose the response it rested on. The earliest feedback file's line
wins, then that file. An earliest file recording none, written before files recorded it, is
read as the computed boundary with its stamp as the first gathering.

It is computed from the run's own marked comments on the items the pass read, with no other
read. Unscoped, that misses only an evidence comment on an item no run of this host owns, or
one changed before `<since>`: with no watermark, `<since>` is no later than every unrecorded
run's ticket files' last write, which is part of the boundary, and with one, every earlier
window was a complete gathering that recorded a boundary for each run whose issue or marked
comment it returned. A scoped pass reads only the run's own items, so a first gathering made
scoped misses its evidence comments on other runs' items too. Each miss can only move the
boundary earlier, which quotes a comment again rather than ever dropping one. `--since` by
hand rests the rest on the operator. A run with no such response has no boundary, and every
person's comment on its issues counts.

**What is written.** Feedback files under `<drafts root>/feedback/<run-id>/`, a run's
:data:`BOUNDARY_FILE`, and the watermark — never the board, and nothing on a dry run. A
person's move of an item to `Todo`, `Deferred` or `In Progress` is a decision no run undoes, so
the feedback says so again, beside the one status change a comment dispatch may make —
withdrawing this run's own proposal whose ticket the comment, or investigating it, shows is no
longer relevant, whether or not the comment says so, and never a deferred one — in the words of
:data:`follow_up_tickets.WITHDRAWAL_EXCEPTION`, which the feedback task carries too.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import shlex
import socket
import subprocess
import sys
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple, NoReturn

# The SDK's `__all__` exports neither the listing's nor `task show`'s item model, as
# `follow_up_tickets` says beside its own import of the first; the follow-up "Export the SDK's
# query item models (QualifiedTask et al.) beside the QueryResponseOf… schema roots" retires
# both imports.
from onetaskgraph_sdk._generated.query_response_of_qualified_task import QualifiedTask
from onetaskgraph_sdk._generated.task_detail import QualifiedTask as ShownTask

from orchestrator import follow_up_tickets as tickets
from orchestrator import plan_store
from orchestrator.plan_store import RECORD_COMPONENT, QualifiedTaskId
from orchestrator.project_store import TASKS_DIRECTORY

FEEDBACK_DIRECTORY = "feedback"

MOMENT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"

UNKNOWN_AUTHOR = "not reported by the board"

PROG = "follow-ups-answer-comments"

#: What each command exits with: done; some launched run was not answered soundly; refused
#: before anything was launched.
DONE = 0
UNANSWERED = 1
UNRUNNABLE = 2

CommentId = tickets.CommentId

#: A feedback file's name: the stamp of the gathering that wrote it, and a counter when two
#: gatherings shared a second.
FEEDBACK_NAME = re.compile(r"(?P<stamp>\d{8}T\d{6}Z)(?:-(?P<attempt>\d+))?\.md")

#: The line a feedback file records its gathering's boundary on, so a later gathering reads
#: the first one's rather than recomputing it from responses a re-dispatch has since moved;
#: the grammar every reader uses is this one.
NO_BOUNDARY = "none"
FEEDBACK_BOUNDARY = '<!-- orchestrator:follow-up-feedback boundary="{boundary}" -->'
FEEDBACK_BOUNDARY_LINE = re.compile(
    "^"
    + re.escape(FEEDBACK_BOUNDARY).replace(re.escape("{boundary}"), r'(?P<boundary>[^"\n]*)')
    + "$",
    re.MULTILINE,
)

#: Where a gathering records a boundary it computed for a run, beside that run's feedback
#: files, as the one :data:`FEEDBACK_BOUNDARY` line. Not a `.md` name, so no reader of
#: gatherings reads it as one.
BOUNDARY_FILE = "boundary.txt"

#: The board's watermark under the feedback directory. A leading dot keeps it out of the
#: run ids `scripts/plan-brief.sh` admits, so no run's directory can take its name.
WATERMARK = ".watermark-{board}.json"
WATERMARK_SCHEMA = 1


class Watermark(NamedTuple):
    """The watermark file's record, field for field as it is written."""

    schema: int
    board: str
    #: Where the next gathering's query starts.
    since: str
    #: When the gathering that wrote it queried.
    queried_at: str


#: The keys `read_watermark` requires a file to hold exactly, so a file with any other
#: shape is refused whole rather than half-read into a start instant.
WATERMARK_KEYS = Watermark._fields

#: How far a watermark is set back from the instant its gathering queried at. GitHub answers
#: the narrowed query from its issue search, which is an index and eventually consistent
#: (onetaskgraph-github-projects' `src/lib.rs` says so where it reads it), so a comment
#: written moments before one gathering can reach the index after that gathering asked; the
#: next gathering reaching back over it reads that comment again rather than never.
OVERLAP = timedelta(minutes=15)

#: A run id `scripts/follow-ups.sh` launches, which is `scripts/plan-brief.sh`'s
#: `PLAN_SAFE_RUN_ID`: narrower than a store record's component, so an issue whose record names
#: a run outside it is one no launch here could answer.
#: `tests/plan_tooling/test_follow_ups_answer_comments_recipe_e2e.py` holds the two spellings
#: to one grammar.
LAUNCHABLE_RUN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*")

#: The store categories whose items are answered: `Proposal` and `Deferred` on the board,
#: `Proposed` and `Backlog` on Linear.
ANSWERED_CATEGORIES = (tickets.Status.PROPOSED.value, tickets.Status.DEFERRED.value)

#: The GitHub Actions app's login, and the suffix every other app's login carries.
BOT_LOGIN = "github-actions"
BOT_SUFFIX = "[bot]"
#: The plugin of a Linear board, whose comments name their author differently.
LINEAR_PLUGIN = "linear"
#: What a left-out Linear comment with no author is said to be.
LINEAR_BOT = "none named: Linear names no user for an integration's comment"


#: An RFC 3339 date-time, offset included, which is narrower than what `fromisoformat` takes.
RFC3339 = re.compile(r"\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})")


class Unbounded(ValueError):
    """Local records give no start for a first gathering; names every such run."""


class Unowned(ValueError):
    """An `--issue` no run of this host owns; says why."""


@dataclass(frozen=True)
class Comment:
    """One comment as the board holds it, every field this reads validated."""

    id: CommentId
    author: str | None
    body: str
    url: str | None
    last_changed: datetime


@dataclass(frozen=True)
class Issue:
    """One item the narrowed query returned, and the comments the board holds on it."""

    id: QualifiedTaskId
    title: str
    #: The run its follow-up record names as creator, or `None` for an item no run owns.
    owner: tickets.RunId | None
    location: str | None
    url: str | None
    comments: tuple[Comment, ...]
    #: The store category of its status, and the name the board shows for it.
    category: str = tickets.Status.PROPOSED.value
    status: str = tickets.board_option(tickets.Status.PROPOSED)
    #: The machine its ticket was verified on, as its record states it.
    host: str | None = None
    #: The plugin of the board it is on, which decides who counts as a bot there.
    plugin: str | None = None


class Reply(NamedTuple):
    """The latest reply naming one comment: ordered by when it last changed, then its id."""

    last_changed: datetime
    id: CommentId


@dataclass(frozen=True)
class Selected:
    """One person's comment selected as feedback, with what the feedback file names."""

    issue: Issue
    id: CommentId
    url: str
    author: str
    last_changed: datetime
    text: str


@dataclass(frozen=True)
class Left:
    """One comment the query returned that no run is sent, and the one reason why."""

    issue: Issue
    id: CommentId
    url: str
    reason: str


@dataclass(frozen=True)
class Selection:
    """What :func:`select` found: each run's comments in listing order, and the rest."""

    chosen: dict[tickets.RunId, list[Selected]]
    left: list[Left]


def moment(value: datetime | str | None, what: str) -> datetime:
    """A time the board reports, or :class:`OSError` when it reports none."""
    if value is None:
        raise OSError(f"{what} reports no time")
    if isinstance(value, str):
        try:
            if not RFC3339.fullmatch(value):
                raise ValueError(value)
            value = datetime.fromisoformat(value)
        except ValueError:
            raise OSError(f"{what} reports {value!r}, which is not an RFC 3339 time") from None
    if value.tzinfo is None:
        raise OSError(f"{what} reports {value!r}, which names no offset")
    return value.astimezone(UTC)


def instant(value: datetime) -> str:
    """``value`` as the whole-second RFC 3339 instant every reader here spells."""
    return value.astimezone(UTC).strftime(MOMENT_FORMAT)


def _web_url(value: str | None) -> str | None:
    """A URL the board reports, kept only when it is one web address a reader can open."""
    return value if value is not None and tickets.ITEM_URL.fullmatch(value) else None


def _board_issue(identifier: str, board: str) -> bool:
    """Whether ``identifier`` is one qualified task id of ``board``."""
    matched = tickets.QUALIFIED_ID.fullmatch(identifier)
    return matched is not None and matched["source"] == board


def _one_line(text: str, what: str, qualified: QualifiedTaskId) -> None:
    """Refuse ``text`` the board reports for ``qualified`` when it spans lines."""
    if {"\n", "\r"} & set(text):
        raise OSError(f"{qualified} reports {what} spanning lines")


def _location_path(location: Mapping[str, object], qualified: QualifiedTaskId) -> str | None:
    """The path a board item's location names, which becomes a comment's file URL."""
    path = location.get("path")
    if path is None:
        return None
    if isinstance(path, str) and path and not {"\n", "\r"} & set(path):
        return path
    raise OSError(f"{qualified} reports a location path {path!r}, which is not a path")


def host_query(host: str) -> str:
    """The `--metadata` value selecting the board items whose ticket was verified on ``host``.

    The store's own narrowing question for this host's ownership: a run here writes this
    machine's `hostname` into every ticket it verifies, so an item of a run on another host,
    or one carrying no follow-up record, is never returned by it.
    """
    return f"{tickets.KEY}/host={host}"


def _issue(held: QualifiedTask | ShownTask, board: str, plugin: str | None) -> Issue:
    """One item of ``board`` the store answered, with its comments read by id."""
    listed_id = held.id.model_dump()
    if not _board_issue(listed_id, board):
        raise OSError(f"the board {board!r} listed {listed_id!r}, which is not one of its ids")
    qualified = QualifiedTaskId(listed_id)
    item = held.item.model_dump(mode="python")
    metadata = item.get("metadata")
    record = metadata.get(tickets.KEY) if isinstance(metadata, Mapping) else None
    host = record.get("host") if isinstance(record, Mapping) else None
    listed = plan_store.sdk(plan_store.client().task_comment_list(str(qualified))).comments
    location = held.item.location.model_dump(mode="python") if held.item.location else {}
    # A title is written into a feedback file's one-line field, so a line break in it
    # would open a field of its own there, and a status name into the report's one line
    # per comment. An author is too, and the store itself refuses to hold one with a
    # line break.
    _one_line(held.item.title, "a title", qualified)
    _one_line(held.item.status.name, "a status name", qualified)
    # A host is named in the report's reason for leaving a comment out.
    if isinstance(host, str):
        _one_line(host, "a host", qualified)
    path = _location_path(location, qualified)
    return Issue(
        id=qualified,
        title=held.item.title,
        owner=tickets.issue_owner(item),
        location=path,
        url=_web_url(held.item.url),
        comments=tuple(
            Comment(
                id=CommentId(comment.id.model_dump()),
                author=comment.author,
                body=comment.body,
                url=_web_url(comment.url),
                last_changed=moment(
                    comment.updated_at or comment.created_at,
                    f"comment {comment.id.model_dump()!r} on {qualified}",
                ),
            )
            for comment in listed
        ),
        category=held.item.status.category.value,
        status=held.item.status.name,
        host=host if isinstance(host, str) else None,
        plugin=plugin,
    )


def commented_issues(
    board: str, since: datetime, plugin: str | None = None, *, host: str | None = None
) -> list[Issue]:
    """Every item of ``board`` this host's runs own commented on at or after ``since``.

    The one listing of this flow: the store's own `commented_since`, narrowed by
    :func:`host_query` and read to its last page, and a comment listing for each item it
    returned and no other. ``plugin`` is the board's, which decides who counts as a bot on it;
    ``host`` is this machine's `hostname` unless named.
    """
    pages = plan_store.every_page(
        f"the board {board!r} commented on since {instant(since)}",
        plan_store.client().task_list,
        source=[board],
        commented_since=instant(since),
        metadata=[host_query(host or socket.gethostname())],
    )
    return [_issue(held, board, plugin) for page in pages for held in page.items]


def bound_issue(qualified: QualifiedTaskId, plugin: str | None) -> Issue:
    """The one board item ``qualified`` names and its comments, each read by id; no listing."""
    board = str(qualified).partition(":")[0]
    answer = plan_store.complete(
        plan_store.sdk(plan_store.client().task_show(str(qualified), no_comments=True))
    )
    if len(answer.items) != 1:
        raise OSError(f"the board {board!r} answered {len(answer.items)} items for {qualified}")
    (held,) = answer.items
    if held.id.model_dump() != str(qualified):
        raise OSError(
            f"the board {board!r} answered {held.id.model_dump()!r} when asked for {qualified}"
        )
    return _issue(held, board, plugin)


class Binding(NamedTuple):
    """One local ticket of a run here, and the board item its record is bound to, if any."""

    run: tickets.RunId
    ticket: str
    #: The bound item qualified to the board of the family the ticket is filed on.
    item: QualifiedTaskId | None


def bindings(root: Path, board: str, query: str) -> list[Binding]:
    """Every local ticket under ``root`` the drafts store's ``query`` selects, and its binding.

    One narrowed listing of the drafts source, never of a board: ``query`` is a `--metadata`
    value. A ticket the store read from anywhere but a run's tickets directory under ``root``
    is not this root's, and the item a binding names is qualified to the board of ``board``'s
    family the ticket's `repositories` route it to, which is where it was copied.
    """
    pages = plan_store.every_page(
        f"the tickets under {root} selected by {query}",
        plan_store.client().task_list,
        source=[tickets.SOURCE],
        metadata=[query],
    )
    found = []
    for held in (one for page in pages for one in page.items):
        item = held.item.model_dump(mode="python")
        location = held.item.location.model_dump(mode="python") if held.item.location else {}
        located = Path(str(location.get("path", "")))
        if located.parent.parent.parent != root / TASKS_DIRECTORY or (
            located.parent.name != tickets.TICKETS
        ):
            continue
        run = tickets.RunId(located.parent.parent.name)
        record = item.get("metadata")
        held_record = record.get(tickets.KEY) if isinstance(record, Mapping) else None
        native = (
            held_record.get(tickets.BINDING_FIELD) if isinstance(held_record, Mapping) else None
        )
        if native is None:
            found.append(Binding(run, located.stem, None))
            continue
        if not tickets.is_item_id(native):
            raise OSError(
                f"run {run}'s ticket {located.stem} records `{tickets.BINDING_FIELD}` {native!r}, "
                "which is not a board item's native id; run `board-status` on it to bind it again"
            )
        listed = item.get("repositories")
        repositories = [one for one in listed if isinstance(one, str)] if listed else []
        routed = tickets.ticket_board(board, repositories)
        found.append(Binding(run, located.stem, QualifiedTaskId(f"{routed}:{native}")))
    return sorted(found, key=lambda one: (one.run, one.ticket))


def run_bindings(root: Path, board: str, run: tickets.RunId) -> list[Binding]:
    """Every ticket ``run`` filed under ``root``, and the item each is bound to."""
    query = f"{tickets.KEY}/created_by_run={run}"
    return [one for one in bindings(root, board, query) if one.run == run]


def issue_binding(root: Path, board: str, issue: QualifiedTaskId) -> tickets.RunId:
    """The one run under ``root`` whose ticket is bound to ``issue``; :class:`Unowned` else."""
    native = str(issue).partition(":")[2]
    query = f"{tickets.KEY}/{tickets.BINDING_FIELD}={native}"
    runs = sorted({one.run for one in bindings(root, board, query) if one.item == issue})
    if not runs:
        raise Unowned(
            f"--issue {issue}: no run of this host owns it, because no ticket under {root} is "
            f"bound to it (no record's `{tickets.BINDING_FIELD}` names it on that board), so no "
            "run here answers its comments"
        )
    if len(runs) > 1:
        raise Unowned(
            f"--issue {issue}: the tickets of runs {', '.join(runs)} under {root} are all bound "
            "to it, so no one run owns its comments; settle the bindings with `board-status`"
        )
    return runs[0]


def tickets_last_written(root: Path, run: str) -> datetime | None:
    """When any of ``run``'s ticket files under the drafts root was last written, if ever.

    The stand-in for the run's last ticket copy: the follow-up agent writes each ticket with
    the status `board-status` printed immediately before it copies it, nothing on the board
    touches the file, and no copy is recorded anywhere else.
    """
    directory = root / TASKS_DIRECTORY / run / tickets.TICKETS
    times = [path.stat().st_mtime for path in directory.glob(f"*{tickets.TICKET_SUFFIX}")]
    return datetime.fromtimestamp(max(times), UTC) if times else None


def has_records(root: Path, run: str) -> bool:
    """Whether anything of ``run`` is under this drafts root: its drafts project or feedback."""
    return (root / TASKS_DIRECTORY / run).is_dir() or (root / FEEDBACK_DIRECTORY / run).is_dir()


def ticketed_or_gathered_runs(root: Path) -> list[tickets.RunId]:
    """Every run that has filed tickets here or been gathered for, the runs a board can owe.

    A run whose drafts were never verified into a ticket owns no issue, so its records say
    nothing about where a first gathering must start.
    """
    found = {
        path.parent.name
        for path in (root / TASKS_DIRECTORY).glob(f"*/{tickets.TICKETS}")
        if path.is_dir()
    } | {path.name for path in (root / FEEDBACK_DIRECTORY).glob("*") if path.is_dir()}
    return sorted(tickets.RunId(run) for run in found if LAUNCHABLE_RUN.fullmatch(run))


def comment_url(issue: Issue, comment: Comment) -> str:
    """The comment's own URL; else a URL into the issue that holds it, fragment its id."""
    return comment_url_parts(issue.url, issue.location, comment.id, comment.url, str(issue.id))


def comment_url_parts(
    issue_url: str | None,
    location: str | None,
    comment_id: str,
    direct_url: str | None,
    issue_id: str,
) -> str:
    """One comment URL from the board fields the gathering and its validator both read."""
    # Only the hosted `followups` board reports a URL, and reading it needs that board's
    # credential, which every dispatch and journey is denied; the `local-md` stand-in the
    # journey drives reports none. `tests/test_follow_up_comments.py` stands in the store's
    # answer for these two branches, and the journey drives the location branch below.
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    if direct_url is not None:
        return direct_url
    fragment = f"#comment-{comment_id}"
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    if issue_url is not None:
        return issue_url + fragment
    if location is not None:
        return Path(location).absolute().as_uri() + fragment
    raise OSError(f"the board reports no URL and no location for {issue_id} or its comments")


class Boundary(NamedTuple):
    """The boundary the module states: a moment, or ``None`` when a run has none."""

    moment: datetime | None


class Gathering(NamedTuple):
    """A run's first gathering: its feedback file's stamp, and the boundary that file records.

    ``recorded`` is ``None`` for a file written before gatherings recorded their boundary.
    """

    stamp: datetime
    recorded: Boundary | None


def boundary_line(since: datetime | None) -> str:
    """The line a feedback file records the boundary its gathering used with."""
    return FEEDBACK_BOUNDARY.format(boundary=NO_BOUNDARY if since is None else instant(since))


def recorded_boundary(text: str, what: str) -> Boundary | None:
    """The boundary a feedback file's text records, or ``None`` when it records none.

    :class:`OSError` naming ``what`` for a recorded value that is neither a moment nor
    :data:`NO_BOUNDARY`, since a boundary this cannot read would move every comment across it.
    """
    matched = FEEDBACK_BOUNDARY_LINE.search(text)
    if matched is None:
        return None
    if matched["boundary"] == NO_BOUNDARY:
        return Boundary(None)
    return Boundary(moment(matched["boundary"], f"the boundary {what} records"))


def first_gathering(root: Path, run: str) -> Gathering | None:
    """``run``'s first gathering, read off its earliest feedback file, if it was ever gathered.

    A file under the directory whose name is not a gathering's stamp was not written here, so
    it is not read as one; two gatherings in one second are ordered by their counter.
    """
    written = []
    for path in (root / FEEDBACK_DIRECTORY / run).glob("*.md"):
        matched = FEEDBACK_NAME.fullmatch(path.name)
        if matched is not None:
            try:
                stamp = datetime.strptime(matched["stamp"], STAMP_FORMAT).replace(tzinfo=UTC)
            except ValueError:
                raise OSError(
                    f"{path} is named for no moment a gathering could have run at"
                ) from None
            written.append((stamp, int(matched["attempt"] or 1), path))
    if not written:
        return None
    stamp, _, path = min(written)
    return Gathering(stamp, recorded_boundary(path.read_text(encoding="utf-8"), str(path)))


def boundary_path(root: Path, run: str) -> Path:
    """Where a gathering records the boundary it computed for ``run``."""
    return root / FEEDBACK_DIRECTORY / run / BOUNDARY_FILE


def stored_boundary(root: Path, run: str) -> Boundary | None:
    """The boundary ``run``'s records hold: its earliest feedback file's, else its file's."""
    first = first_gathering(root, run)
    if first is not None and first.recorded is not None:
        return first.recorded
    path = boundary_path(root, run)
    if not path.is_file():
        return None
    held = recorded_boundary(path.read_text(encoding="utf-8"), str(path))
    if held is None:
        raise OSError(f"{path} records no boundary line; delete it to compute the boundary again")
    return held


def _marked(run: str, issues: Sequence[Issue]) -> list[tuple[Issue, Comment, tickets.CommentOwner]]:
    """Every comment on ``issues`` whose marker names ``run``, of either kind."""
    return [
        (issue, comment, owner)
        for issue in issues
        for comment in issue.comments
        if (owner := tickets.comment_owner(comment.body)) is not None and owner.run == run
    ]


def computed_boundary(
    run: str,
    issues: Sequence[Issue],
    tickets_written: datetime | None,
    first_gathered: datetime | None,
) -> Boundary:
    """The boundary as the module states it, for a run whose records hold none."""
    responses = [] if tickets_written is None else [tickets_written]
    responses.extend(
        comment.last_changed for _, comment, owner in _marked(run, issues) if owner.answers is None
    )
    if first_gathered is not None:
        responses = [moment for moment in responses if moment < first_gathered]
    return Boundary(max(responses) if responses else None)


def run_boundary(root: Path, run: str, issues: Sequence[Issue]) -> tuple[Boundary, bool]:
    """``run``'s boundary, and whether it was computed now and is still to be recorded."""
    stored = stored_boundary(root, run)
    if stored is not None:
        return stored, False
    first = first_gathering(root, run)
    written = tickets_last_written(root, run)
    return computed_boundary(run, issues, written, None if first is None else first.stamp), True


def is_bot(author: str | None, plugin: str | None = None) -> bool:
    """Whether a comment's author is an app rather than a person, on a board of ``plugin``.

    Linear names the user whose key wrote a comment, which a person's key and an integration's
    both are, and names none for an integration writing as itself — the one author on Linear
    no person is (onetaskgraph-linear's ruling on a comment's author). Elsewhere the board
    names an app's login, which carries GitHub's app suffix.
    """
    if plugin == LINEAR_PLUGIN:
        return author is None
    return author is not None and (author == BOT_LOGIN or author.endswith(BOT_SUFFIX))


def appearing_runs(issues: Sequence[Issue]) -> set[tickets.RunId]:
    """Every run owning one of ``issues`` or whose marker is on one: the runs it bounds."""
    owners = {issue.owner for issue in issues if issue.owner is not None}
    marked = {
        owner.run
        for issue in issues
        for comment in issue.comments
        if (owner := tickets.comment_owner(comment.body)) is not None
    }
    return owners | marked


def select(
    issues: Sequence[Issue],
    boundaries: Mapping[tickets.RunId, Boundary],
    host: str,
    recorded: Collection[tickets.RunId],
) -> Selection:
    """Every comment on ``issues``, each selected for its owning run or left out once.

    The rule, and the order its reasons are tried in, is the module's.
    """
    # A reply is posted on the issue holding the comment it answers, so it is looked up there:
    # a board whose comment ids are unique only within an issue cannot answer the wrong one.
    replied: dict[tuple[QualifiedTaskId, CommentId], Reply] = {}
    for issue in issues:
        for comment in issue.comments:
            owner = tickets.comment_owner(comment.body)
            if owner is None or owner.answers is None:
                continue
            key, this = (issue.id, owner.answers), Reply(comment.last_changed, comment.id)
            replied[key] = max(replied.get(key, this), this)
    chosen: dict[tickets.RunId, list[Selected]] = {}
    left: list[Left] = []
    for issue in issues:
        for comment in issue.comments:
            url = comment_url(issue, comment)
            reason = _left_out(issue, comment, replied, boundaries, host, recorded)
            if reason is not None:
                left.append(Left(issue, comment.id, url, reason))
                continue
            assert issue.owner is not None  # noqa: S101 - `_left_out` leaves out every other
            chosen.setdefault(issue.owner, []).append(
                Selected(
                    issue=issue,
                    id=comment.id,
                    url=url,
                    author=comment.author or UNKNOWN_AUTHOR,
                    last_changed=comment.last_changed,
                    text=comment.body,
                )
            )
    return Selection(chosen=chosen, left=left)


def _left_out(  # noqa: PLR0911, PLR0913 - one return per reason the module names, in its order
    issue: Issue,
    comment: Comment,
    replied: Mapping[tuple[QualifiedTaskId, CommentId], Reply],
    boundaries: Mapping[tickets.RunId, Boundary],
    host: str,
    recorded: Collection[tickets.RunId],
) -> str | None:
    """The one reason ``comment`` is left out, or ``None`` when its owning run is sent it."""
    if issue.owner is None:
        return "the no-answer rule: the item carries no follow-up record, so no run answers it"
    if issue.host != host:
        return f"ticket verified on host {issue.host or '(none recorded)'}"
    if not LAUNCHABLE_RUN.fullmatch(issue.owner):
        return f"owning run {issue.owner} is not a run id `just follow-ups` launches"
    if issue.owner not in recorded:
        return f"no records for owning run {issue.owner} here"
    if issue.category not in ANSWERED_CATEGORIES:
        return f"item at {issue.status}"
    if (marker := tickets.comment_owner(comment.body)) is not None:
        return f"marked by run {marker.run}"
    if is_bot(comment.author, issue.plugin):
        return f"bot author {comment.author or LINEAR_BOT}"
    answered = replied.get((issue.id, comment.id))
    if answered is not None and answered.last_changed >= comment.last_changed:
        return f"answered by reply {answered.id}"
    since = boundaries.get(issue.owner, Boundary(None)).moment
    if since is not None and comment.last_changed <= since:
        return f"at or before run {issue.owner}'s boundary {instant(since)}"
    return None


def _fenced(text: str) -> str:
    """``text`` verbatim inside a fence longer than any backtick run it holds."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text.rstrip()}\n{fence}\n"


def render(run: str, board: str, chosen: Sequence[Selected], since: datetime | None) -> str:
    """The feedback file: what the comments are, what to do, then each comment verbatim."""
    after = f", each changed after {instant(since)}," if since is not None else ""
    sections = [
        f"{boundary_line(since)}\n\n"
        f"People commented on run `{run}`'s follow-ups on the `{board}` board or a board it "
        f"routes to{after} and no "
        "run's reply answers them yet. Gathered by `just follow-ups-answer-comments`, each "
        "is quoted verbatim below with its id, its URL, its author and when it last "
        "changed.\n\n"
        "For each quoted comment, in this order:\n\n"
        '1. **Act on it** under "Ownership on the board" above: decide whether the ticket of '
        "the issue it sits on should change in light of it and of anything investigating it "
        "found, whether or not it asks for an edit, make that change, and perform whatever else "
        "it calls for.\n"
        "2. **Post its one reply**, naming the comment's id, as those rules state, and saying "
        "what changed in the ticket and why, or why the ticket stands as it is.\n"
        "3. **Report** the comment's URL beside what you did about it, or why you did "
        "nothing.\n\n"
        f"{tickets.WITHDRAWAL_EXCEPTION} Never touch a ticket or an issue no comment above "
        "names: a person's move to `Todo`, `Deferred` (Linear's `Backlog`) or `In Progress` "
        "stands whenever they made it, and this dispatch answers these comments and nothing "
        "else.\n"
    ]
    for number, selected in enumerate(chosen, start=1):
        issue = selected.issue
        whose = "this run's issue" if issue.owner == run else f"run `{issue.owner}`'s issue"
        sections.append(
            f"{tickets.QUOTED_COMMENT_HEADING}{number}: on `{issue.id}`, {whose}\n\n"
            # The anchor the response artifact's validator reads this gathering's comments
            # back out of, in this order. Its grammar is `follow_up_tickets`', beside the
            # comment markers, because that module's `check-responses` is its one reader.
            f"{tickets.quoted_comment(str(issue.id), str(selected.id))}\n\n"
            f"- Comment id: {selected.id}\n"
            f"- URL: {selected.url}\n"
            f"- Author: {selected.author}\n"
            f"- Last changed: {instant(selected.last_changed)}\n"
            f"- Issue title: {issue.title}\n\n" + _fenced(selected.text)
        )
    return "\n".join(sections)


def write_feedback(
    root: Path,
    run: str,
    board: str,
    chosen: Sequence[Selected],
    since: datetime | None,
    now: datetime,
) -> Path:
    """Write one gathering of ``run``'s comments under ``root``, named for ``now``; its path."""
    directory = root / FEEDBACK_DIRECTORY / run
    directory.mkdir(parents=True, exist_ok=True)
    stamp = now.astimezone(UTC).strftime(STAMP_FORMAT)
    text = render(run, board, chosen, since)
    attempt = 1
    while True:
        path = directory / (f"{stamp}.md" if attempt == 1 else f"{stamp}-{attempt}.md")
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(text)
        except FileExistsError:
            attempt += 1
            continue
        return path


def watermark_path(root: Path, board: str) -> Path:
    """The board's watermark file under the drafts root."""
    return root / FEEDBACK_DIRECTORY / WATERMARK.format(board=board)


def read_watermark(root: Path, board: str) -> datetime | None:
    """The instant the board's watermark says the next gathering queries from, if it has one.

    :class:`OSError` naming the file for any other shape, since a watermark this cannot read
    would move the start of every later query.
    """
    path = watermark_path(root, board)
    if not path.is_file():
        return None
    try:
        held: object = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as broken:
        raise OSError(f"the watermark {path} is not JSON: {broken}") from None
    if (
        not isinstance(held, dict)
        or sorted(held) != sorted(WATERMARK_KEYS)
        or type(held["schema"]) is not int
        or held["schema"] != WATERMARK_SCHEMA
        or held["board"] != board
        or not isinstance(held["since"], str)
        or not isinstance(held["queried_at"], str)
    ):
        raise OSError(
            f"the watermark {path} is not schema {WATERMARK_SCHEMA} for {board!r} with the keys "
            f"{', '.join(WATERMARK_KEYS)}; repair it, or delete it to derive the start from "
            "local records"
        )
    queried = moment(held["queried_at"], f"the watermark {path}")
    since = moment(held["since"], f"the watermark {path}")
    if since > queried:
        raise OSError(
            f"the watermark {path} starts at {held['since']}, after the gathering it records "
            f"queried at {held['queried_at']}; delete it to derive the start from local records"
        )
    return since


def write_watermark(root: Path, board: str, queried_at: datetime) -> datetime:
    """Advance the board's watermark past a complete gathering that queried at ``queried_at``."""
    since = queried_at - OVERLAP
    path = watermark_path(root, board)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + ".partial")
    record = Watermark(WATERMARK_SCHEMA, board, instant(since), instant(queried_at))
    staged.write_text(json.dumps(record._asdict(), indent=2) + "\n", encoding="utf-8")
    staged.replace(path)
    return since


class Start(NamedTuple):
    """Where one gathering's query starts, and the words saying where that came from."""

    since: datetime
    origin: str


def derived_start(root: Path) -> Start | None:
    """The earliest bound local records give a run that filed tickets here, or ``None``.

    ``None`` when no run has: nothing on this host can be owed a comment. :class:`Unbounded`
    naming every run whose records give no bound.
    """
    bounds: list[tuple[datetime, str, str]] = []
    unbounded = []
    for run in ticketed_or_gathered_runs(root):
        stored = stored_boundary(root, run)
        if stored is not None and stored.moment is not None:
            bounds.append((stored.moment, run, "recorded boundary"))
        elif (written := tickets_last_written(root, run)) is not None:
            bounds.append((written, run, "ticket files' last write"))
        else:
            unbounded.append(run)
    if unbounded:
        raise Unbounded(
            f"the local records of run(s) {', '.join(unbounded)} under {root} hold no boundary "
            "and no ticket file, so no first start can be derived without querying from the "
            "epoch; run it again with --since <RFC3339>, naming the earliest instant a "
            "comment could be owed"
        )
    if not bounds:
        return None
    earliest, first, kind = min(bounds)
    floored = earliest.replace(microsecond=0)
    return Start(floored, f"derived from local records: run {first}'s {kind}")


def store_version() -> str:
    """What the plan-store CLI this process reads through says it is, as it says it."""
    binary = plan_store.locked_binary()
    answered = subprocess.run(  # noqa: S603 - the locked store beside this interpreter
        [str(binary), "--version"], capture_output=True, text=True, check=False
    )
    said = answered.stdout.strip() if answered.returncode == 0 else ""
    return (said or f"{binary}, which reported no version").splitlines()[0]


class Launch(NamedTuple):
    """One run a gathering launches, and the feedback file it launches it over."""

    run: tickets.RunId
    feedback: Path


class IssueLink(NamedTuple):
    """Where a quoted issue lives, which is what a reply URL on it is built from."""

    url: str | None
    location: str | None


@dataclass(frozen=True)
class Plan:
    """One gathering as :func:`gather` hands it to the script and to :func:`settle`."""

    board: str
    #: Every board of ``board``'s family the gathering read, each of whose watermarks a sound
    #: settlement moves.
    boards: tuple[str, ...]
    #: Whether ``--to`` named the board, so the launches name it the same way.
    named_board: bool
    #: The earliest instant any board was read from.
    since: datetime
    queried_at: datetime
    #: The one owning run a ``--run`` invocation is narrowed to, or ``None``.
    scope: tickets.RunId | None
    detach: bool
    store: str
    items: int
    #: Each run to launch, in order, with its feedback file.
    runs: tuple[Launch, ...]
    #: Each issue a feedback file quotes, and where it lives.
    issues: Mapping[str, IssueLink]


def _plan_document(plan: Plan) -> dict[str, object]:
    return {
        "board": plan.board,
        "boards": list(plan.boards),
        "named_board": plan.named_board,
        "since": instant(plan.since),
        "queried_at": instant(plan.queried_at),
        "scope": plan.scope,
        "detach": plan.detach,
        "store": plan.store,
        "items": plan.items,
        "runs": [[run, str(path)] for run, path in plan.runs],
        "issues": {issue: list(held) for issue, held in plan.issues.items()},
    }


#: The keys `read_plan` requires exactly, each with the JSON type it refuses any other of,
#: so a plan the next step cannot trust is refused before anything launches from it.
PLAN_FIELDS: dict[str, type | tuple[type, ...]] = {
    "board": str,
    "boards": list,
    "named_board": bool,
    "since": str,
    "queried_at": str,
    "scope": (str, type(None)),
    "detach": bool,
    "store": str,
    "items": int,
    "runs": list,
    "issues": dict,
}


def _pair(value: object, kinds: type | tuple[type, ...]) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2  # noqa: PLR2004 - a pair is two
        and all(isinstance(one, kinds) for one in value)
    )


def _launch(value: object, root: Path) -> bool:
    """Whether ``value`` is a run id and one of that run's feedback files under ``root``."""
    if not _pair(value, str):
        return False
    assert isinstance(value, list)  # noqa: S101 - `_pair` just proved it
    run, feedback = value[0], Path(value[1])
    return bool(
        LAUNCHABLE_RUN.fullmatch(run)
        and not {"\t", "\n"} & set(value[1])
        and FEEDBACK_NAME.fullmatch(feedback.name)
        and feedback.parent == root / FEEDBACK_DIRECTORY / run
    )


def read_plan(path: Path, root: Path) -> Plan:
    """The plan :func:`gather` wrote under ``root``, which this process's own script hands back.

    :class:`OSError` naming the file for anything else, since a plan this cannot read would
    launch or settle runs nobody gathered for. The plan lives in a directory the script made
    for this one invocation, so only a test can hand a step one `gather` did not write.
    """
    # The recipe hands its steps a plan from a directory it made for that one invocation, so
    # no journey can put another file there; `tests/test_follow_up_comments.py` hands the
    # steps every malformed shape in process instead.
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    try:
        held: object = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as broken:
        raise OSError(f"{path} is not a plan `gather` wrote: {broken}") from None
    if (
        not isinstance(held, dict)
        or sorted(held) != sorted(PLAN_FIELDS)
        or not all(isinstance(held[key], kind) for key, kind in PLAN_FIELDS.items())
        or type(held["items"]) is not int
        or bool({"\n", "\r"} & set(held["store"]))
        or not RECORD_COMPONENT.fullmatch(held["board"])
        or not held["boards"]
        or held["boards"][0] != held["board"]
        or not all(
            isinstance(one, str) and RECORD_COMPONENT.fullmatch(one) for one in held["boards"]
        )
        or not (held["scope"] is None or LAUNCHABLE_RUN.fullmatch(held["scope"]))
        or not all(_launch(one, root) for one in held["runs"])
        or not all(
            any(_board_issue(issue, one) for one in held["boards"])
            and _pair(link, (str, type(None)))
            and (link[0] is None or _web_url(link[0]) is not None)
            and (link[1] is None or Path(link[1]).is_absolute())
            for issue, link in held["issues"].items()
        )
    ):
        raise OSError(
            f"{path} is not a plan `gather` wrote: it carries none of, or other than, "
            f"{', '.join(PLAN_FIELDS)} with their types"
        )
    return Plan(
        board=held["board"],
        boards=tuple(held["boards"]),
        named_board=held["named_board"],
        since=moment(held["since"], str(path)),
        queried_at=moment(held["queried_at"], str(path)),
        scope=held["scope"],
        detach=held["detach"],
        store=held["store"],
        items=held["items"],
        runs=tuple(Launch(tickets.RunId(run), Path(file)) for run, file in held["runs"]),
        issues={issue: IssueLink(*link) for issue, link in held["issues"].items()},
    )


Say = Callable[[str], None]


def _say(line: str) -> None:
    print(line, flush=True)


def _store_line(plan: Plan) -> str:
    if plan.scope is not None:
        return (
            f"plan store: {plan.store}; read {plan.items} item(s) run {plan.scope}'s tickets are "
            "bound to, each directly, and listed none"
        )
    return (
        f"plan store: {plan.store}; the narrowed query returned {plan.items} item(s) "
        f"commented on since {instant(plan.since)}"
    )


def _plugin(settings: Mapping[str, object], source: str) -> str | None:
    """The plugin the store configures ``source`` with, which decides who is a bot on it."""
    plugin = settings.get(f"sources.{source}.plugin")
    return plugin if isinstance(plugin, str) else None


def _unowned(issue: Issue, run: tickets.RunId, host: str) -> str | None:
    """Why ``run``, whose ticket here is bound to ``issue``, does not own it on the board.

    ``None`` when it does: the item names ``run`` as its creator and this host as where its
    ticket was verified. ``run`` has records here, since its ticket is what bound it.
    """
    if issue.owner is None:
        return "the item carries no follow-up record naming the run that created it"
    if issue.owner != run:
        return (
            f"the item names run {issue.owner} as its creator, where run {run}'s ticket is "
            "bound to it"
        )
    if issue.host != host:
        return f"its ticket was verified on host {issue.host or '(none recorded)'}, not {host}"
    return None


def gather(  # noqa: PLR0913 - each is one flag of the command
    root: Path,
    board: str,
    *,
    named_board: bool = False,
    since: datetime | None = None,
    scope: tickets.RunId | None = None,
    issue: QualifiedTaskId | None = None,
    dry_run: bool = False,
    detach: bool = False,
    host: str | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    say: Say = _say,
) -> Plan | None:
    """Read the items this pass is about, report every comment on them, and write what to launch.

    ``board`` roots the family (:func:`follow_up_tickets.boards`). Unscoped, each board is
    read once, narrowed to this host's items, from a start of its own: ``since``, else its
    watermark, else the start local records derive. A ``scope`` run's bound items, or the one
    ``issue``, are read by id instead, and listed on no board. Returns the plan the launches
    follow, or ``None`` for a dry run or a host no comment can be owed on, which have nothing
    to launch. :class:`Unbounded`, :class:`Unowned` or :class:`OSError` refuse before anything
    is written.
    """
    family = tickets.boards(board)
    host = host or socket.gethostname()
    issue_run: tickets.RunId | None = None
    if issue is not None:
        if not any(_board_issue(issue, one) for one in family):
            raise Unowned(
                f"--issue {issue}: no run of this host owns it, because it is no item of "
                f"{board!r} or a board it routes to ({', '.join(family)})"
            )
        issue_run = scope = issue_binding(root, board, issue)
    starts: dict[str, Start] = {}
    derived: Start | None = None
    # A scoped pass reads its items whole, so it takes no start and reads no watermark.
    for read in family if scope is None else ():
        if since is not None:
            starts[read] = Start(since, "named with --since")
        elif (held := read_watermark(root, read)) is not None:
            starts[read] = Start(held, f"the watermark {watermark_path(root, read)}")
        elif (derived := derived or derived_start(root)) is not None:
            starts[read] = derived
        else:
            say(
                f"no run under {root} has filed a follow-up ticket, so no comment on {board!r} "
                "or a board it routes to can be owed here; nothing was read and nothing was "
                "launched"
            )
            return None
    version = store_version()
    queried_at = now()
    settings = plan_store.configured_settings()
    issues = []
    if scope is None:
        for read, start in starts.items():
            issues.extend(commented_issues(read, start.since, _plugin(settings, read), host=host))
            say(f"read {read!r} for comments since {instant(start.since)} ({start.origin})")
    else:
        bound = (
            [Binding(scope, "", issue)] if issue is not None else run_bindings(root, board, scope)
        )
        for binding in bound:
            if binding.item is None:
                say(
                    f"run {scope}'s ticket {binding.ticket} is bound to no board item; nothing read"
                )
                continue
            source = str(binding.item).partition(":")[0]
            issues.append(bound_issue(binding.item, _plugin(settings, source)))
            say(
                f"read {binding.item} and its comments directly, the item run {scope}'s ticket "
                "is bound to"
            )
    if issue_run is not None and (problem := _unowned(issues[0], issue_run, host)) is not None:
        raise Unowned(f"--issue {issue}: no run of this host owns it, because {problem}")
    recorded = {run for run in appearing_runs(issues) if has_records(root, run)}
    boundaries: dict[tickets.RunId, Boundary] = {}
    for run in sorted(recorded):
        boundary, computed = run_boundary(root, run, issues)
        boundaries[run] = boundary
        if computed and not dry_run and scope in (None, run):
            path = boundary_path(root, run)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(boundary_line(boundary.moment) + "\n", encoding="utf-8")
    selection = select(issues, boundaries, host, recorded)
    verb = "would go to" if dry_run else "goes to"
    for run, chosen in selection.chosen.items():
        if scope in (None, run):
            for one in chosen:
                say(f"selected: {one.url} on {one.issue.id} {verb} run {run}")
    for left in selection.left:
        if scope in (None, left.issue.owner):
            say(f"left out: {left.url} on {left.issue.id}: {left.reason}")
    launching = {run: chosen for run, chosen in selection.chosen.items() if scope in (None, run)}
    if not launching:
        say("nothing to answer: no comment was selected, so nothing was launched")
    plan = Plan(
        board=board,
        boards=family,
        named_board=named_board,
        since=min((start.since for start in starts.values()), default=queried_at),
        queried_at=queried_at,
        scope=scope,
        detach=detach,
        store=version,
        items=len(issues),
        runs=(),
        issues={},
    )
    if dry_run:
        say(_store_line(plan))
        return None
    runs = []
    for run, chosen in launching.items():
        path = write_feedback(root, run, board, chosen, boundaries[run].moment, queried_at)
        say(f"wrote run {run}'s comments to {path}")
        runs.append(Launch(run, path))
    quoted = {
        str(one.issue.id): IssueLink(one.issue.url, one.issue.location)
        for chosen in launching.values()
        for one in chosen
    }
    return dataclasses.replace(plan, runs=tuple(runs), issues=quoted)


def check_command(plan: Plan, run: str, feedback: Path) -> str:
    """The exact `check-responses` command that answers whether ``run`` answered ``feedback``."""
    return shlex.join(
        [
            sys.executable,
            "-m",
            "orchestrator.follow_up_tickets",
            "check-responses",
            "--board",
            plan.board,
            "--feedback",
            str(feedback),
            run,
        ]
    )


# A board that answered `check-responses` a moment before and then fails this read is one no
# journey can produce without doubling the store; `settle`'s caller refuses it by name.
# llmlint: ignore[changed_behavior_has_e2e] see the note above this line
def reply_urls(plan: Plan, feedback: Path) -> list[str]:
    """Every reply URL the run's account names, read off the issues the gathering quoted."""
    account = tickets.responses_path(feedback)
    document: object = json.loads(account.read_text(encoding="utf-8"))
    responses = document.get("responses") if isinstance(document, dict) else None
    # Read after `check-responses` held the account sound, and still only the entries naming
    # a reply on an issue this gathering quoted are read.
    named = [
        (issue, reply)
        for response in (responses if isinstance(responses, list) else [])
        if isinstance(response, dict)
        and isinstance(issue := response.get("issue"), str)
        and issue in plan.issues
        and isinstance(reply := response.get("reply"), str)
    ]
    urls = []
    for issue, reply in named:
        listed = plan_store.sdk(plan_store.client().task_comment_list(issue)).comments
        held = {comment.id.model_dump(): comment.url for comment in listed}
        issue_url, location = plan.issues.get(issue, IssueLink(None, None))
        direct = _web_url(held.get(reply))
        urls.append(comment_url_parts(issue_url, location, reply, direct, issue))
    return urls


def settle(
    root: Path,
    plan: Plan,
    launched: Mapping[str, str],
    *,
    say: Say = _say,
) -> int:
    """Report each launched run, check each settled one, and move the watermark if earned."""
    sound, failed = True, False
    for run, feedback in plan.runs:
        follow_up = launched.get(run)
        if follow_up is None:
            say(f"run {run}: its follow-up run did not launch, so {feedback} is unanswered")
            sound, failed = False, True
            continue
        if plan.detach:
            say(
                f"run {run}: launched follow-up run {follow_up}; watch it with: just watch "
                f"{follow_up}; once it settles, check it with: {check_command(plan, run, feedback)}"
            )
            sound = False
            continue
        try:
            unanswered = tickets.responses_unanswered(plan.board, feedback, run)
        except (OSError, tickets.Refused) as exc:
            unanswered = tickets.Unanswered(feedback, [str(exc)])
        if unanswered is not None:
            sound, failed = False, True
            say(f"run {run} (follow-up run {follow_up}): check-responses failed")
            for problem in unanswered.problems:
                say(f"  {unanswered.path}: {problem}")
            continue
        say(f"run {run} (follow-up run {follow_up}): check-responses passed")
        for url in reply_urls(plan, feedback):
            say(f"  reply: {url}")
    if plan.scope is None and sound:
        for board in plan.boards:
            advanced = write_watermark(root, board, plan.queried_at)
            say(f"watermark: advanced to {instant(advanced)} in {watermark_path(root, board)}")
    elif plan.scope is not None:
        say(
            f"watermark: left as it was, because the gathering was scoped to run {plan.scope}'s "
            "comments"
        )
    elif plan.detach:
        say("watermark: left as it was, because the launched runs have not been checked")
    else:
        say("watermark: left as it was, because a launched run was not answered soundly")
    say(_store_line(plan))
    return UNANSWERED if failed else DONE


class _Parser(argparse.ArgumentParser):
    """A parser whose refusals exit with :data:`UNRUNNABLE` and say what to do next."""

    def error(self, message: str) -> NoReturn:
        self.exit(UNRUNNABLE, f"{PROG}: refused: {message}; run it with --help for the contract\n")


def _parser() -> _Parser:
    parser = _Parser(
        prog=PROG,
        description="Route people's board comments to the runs owning their issues.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    gathering = commands.add_parser("gather", help="read the board and write what to launch")
    gathering.add_argument("--root", type=Path, required=True, help="the drafts root")
    gathering.add_argument("--plan", type=Path, required=True, help="where to write the plan")
    # A successful read of the default is a read of the live `followups` board, whose
    # credential no journey holds; `tests/test_follow_up_comments.py` reads that naming no
    # board reaches the `followups` source.
    gathering.add_argument("--to", dest="board", metavar="SOURCE")
    gathering.add_argument("--since", metavar="RFC3339")
    scoped = gathering.add_mutually_exclusive_group()
    scoped.add_argument("--run", dest="scope", metavar="RUN-ID")
    scoped.add_argument("--issue", metavar="QUALIFIED-ID")
    gathering.add_argument("--dry-run", action="store_true")
    gathering.add_argument("--detach", action="store_true")
    launches = commands.add_parser("launches", help="print each launch the plan names")
    launches.add_argument("--root", type=Path, required=True, help="the drafts root")
    launches.add_argument("--plan", type=Path, required=True)
    settling = commands.add_parser("settle", help="check each launched run and report it")
    settling.add_argument("--root", type=Path, required=True, help="the drafts root")
    settling.add_argument("--plan", type=Path, required=True)
    settling.add_argument("--launched", action="append", default=[], metavar="RUN=FOLLOW-UP")
    return parser


def _gathered(arguments: argparse.Namespace) -> int:
    scope = arguments.scope
    board = tickets.BOARD if arguments.board is None else arguments.board
    if scope is not None and not LAUNCHABLE_RUN.fullmatch(scope):
        print(f"{PROG}: refused: --run {scope!r} is not a run id", file=sys.stderr)
        return UNRUNNABLE
    issue = arguments.issue
    if issue is not None and not tickets.QUALIFIED_ID.fullmatch(issue):
        print(
            f"{PROG}: refused: --issue {issue!r} is not a qualified id, <source>:<native-id>",
            file=sys.stderr,
        )
        return UNRUNNABLE
    # The board's name is spelled into its watermark's file name, so it is one path word.
    if not RECORD_COMPONENT.fullmatch(board):
        print(f"{PROG}: refused: --to {board!r} is not a source name", file=sys.stderr)
        return UNRUNNABLE
    try:
        since = None if arguments.since is None else moment(arguments.since, "--since")
    except OSError as exc:
        print(f"{PROG}: refused: {exc}", file=sys.stderr)
        return UNRUNNABLE
    try:
        plan = gather(
            arguments.root,
            board,
            named_board=arguments.board is not None,
            since=since,
            scope=scope,
            issue=None if issue is None else QualifiedTaskId(issue),
            dry_run=arguments.dry_run,
            detach=arguments.detach,
        )
    except (Unbounded, Unowned) as refusal:
        print(f"{PROG}: refused: {refusal}; nothing was launched", file=sys.stderr)
        return UNRUNNABLE
    except OSError as exc:
        print(
            f"{PROG}: refused: {exc}; nothing was launched. Check that --to names a source this "
            "checkout's plan store configures and can read, then retry",
            file=sys.stderr,
        )
        return UNRUNNABLE
    arguments.plan.write_text(
        "" if plan is None else json.dumps(_plan_document(plan)), encoding="utf-8"
    )
    return DONE


def _launches(arguments: argparse.Namespace) -> int:
    """One tab-separated line per launch: the run, its feedback file, how the launches are
    followed — `detach` or `attach` — and the `--to` word, empty when none was named."""
    if not arguments.plan.read_text(encoding="utf-8"):
        return DONE
    try:
        plan = read_plan(arguments.plan, arguments.root)
    except OSError as exc:
        print(f"{PROG}: refused: {exc}", file=sys.stderr)
        return UNRUNNABLE
    mode = "detach" if plan.detach else "attach"
    board = f"--to={plan.board}" if plan.named_board else ""
    for run, path in plan.runs:
        print(f"{run}\t{path}\t{mode}\t{board}")
    return DONE


def _settled(arguments: argparse.Namespace) -> int:
    if not arguments.plan.read_text(encoding="utf-8"):
        return DONE
    # The script composes every pair from a launch it validated, so no journey can hand it
    # a malformed one; `tests/test_follow_up_comments.py` does, in process.
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    launched = dict(pair.partition("=")[::2] for pair in arguments.launched)
    if not all(LAUNCHABLE_RUN.fullmatch(word) for pair in launched.items() for word in pair):
        print(
            f"{PROG}: refused: --launched takes RUN=FOLLOW-UP, two run ids, and was given "
            f"{arguments.launched!r}",
            file=sys.stderr,
        )
        return UNRUNNABLE
    try:
        return settle(arguments.root, read_plan(arguments.plan, arguments.root), launched)
    except OSError as exc:
        print(f"{PROG}: refused: {exc}", file=sys.stderr)
        return UNRUNNABLE


def main(argv: Sequence[str] | None = None) -> int:
    """The script's three steps: gather, list the launches, settle."""
    arguments = _parser().parse_args(argv)
    handlers = {"gather": _gathered, "launches": _launches, "settle": _settled}
    return handlers[arguments.command](arguments)


if __name__ == "__main__":  # pragma: no cover - the module's own command line
    raise SystemExit(main())
