"""People's board comments on follow-up tickets, each routed to the one run that answers it.

`just follow-ups-answer-comments [--dry-run] [--run RUN-ID | --issue QUALIFIED-ID] [--to SOURCE]
[--since RFC3339] [--detach]`, or `just follow-ups-answer-comments --from <snapshot> [--detach]`,
is how a comment somebody writes on the `followups` board reaches the run that owns the issue
it sits on. `scripts/follow-ups-answer-comments.sh` is a thin script over the commands here:
:func:`gather` reads the board **once**, into a snapshot; :func:`claim` writes one feedback
file per run from that snapshot; the script launches `just follow-ups <run> --feedback FILE
--comments` for every run at once and watches them; and :func:`settle` makes the pass's one
batched board read, verifies every reply, and makes the board writes. This module is the one
statement of which comments are gathered, of the snapshot every later step reads, and of how a
pass writes; the documents point here.

**One board read into a snapshot, then none until settle.** A gathering is one plan-store copy,
by query, into an immutable local Markdown source, and every step after it — a `--from` launch
of a dry run's snapshot, `check-gathering`, the dispatched agent, `check-responses` and settle's
own decisions — reads that copy and never the board or the store's cache. After the snapshot
the board is only written, and read once more: settle's one batched `task show-many`. The one
exception is the store's own named fallback read, made when a write's cached ids are missing
(after `onetaskgraph cache clear`) or rejected as stale, or after a whole copy is refused (the
`repositories` exception below); the recipe names each one it saw and counts it.

The snapshot contract
---------------------

Every reader and writer of a snapshot holds to this, and it is declared here alone:
`follow_up_tickets`, the scripts and the template reach it through this module, or through the
paths, the source name and the lines it writes.

1. **Where it lives.** :data:`SNAPSHOTS_DIRECTORY` under `<drafts root>/feedback/`, one
   directory per gathering named for the board query's instant in :data:`STAMP_FORMAT`, with
   `-<n>` when two gatherings share a second. The leading dot keeps it out of the run-id
   grammar. It is staged under a temporary name and renamed into place whole.
2. **`items/`** (:data:`SNAPSHOT_ITEMS`) is a `local-md` root: **the snapshot source**, named
   :data:`SNAPSHOT_SOURCE` for every process that reads it — this module through
   :func:`snapshot_settings`, every other process through :func:`snapshot_environment`'s two
   variables; this module's own commands declare it `visibility: private`
   (:data:`SNAPSHOT_VISIBILITY`). Only the
   gathering's own copy writes it: one `task copy --to snapshot --comments --no-link` per
   board, by the narrowed query unscoped, or by the ids an `--issue` or `--run` pass reads.
   Each item carries `onetaskgraph.origin` naming its board item and
   `onetaskgraph.copied_version` naming the board version it holds, and each comment carries
   the board comment's id as `origin.id`. Nothing writes it afterwards, so the cache
   refreshing, this pass's own writes and `cache clear` cannot change it. Its status names are
   the family boards' own, read back through :func:`snapshot_status_names`.
3. **`snapshot.json`** (:data:`SNAPSHOT_RECORD`, schema :data:`SNAPSHOT_SCHEMA`) is the
   authoritative record of the pass, :class:`Snapshot` field for field. `queried_at` is the
   instant every watermark, boundary and feedback stamp derives from; `left` holds every
   comment left out with its reason, and an item the copy reported `failed` as an entry with no
   comment: such an item is **not captured**, none of its comments is quoted from this
   snapshot, and a pass from it moves no watermark, so the next gathering reads it again.
4. **`launched.json`** (:data:`SNAPSHOT_LAUNCHED`) is absent until a launch claims the snapshot,
   and written exclusively before any feedback file, holding the claim's instant and whether
   the launch was fresh or `--from`.
5. **`<drafts root>/feedback/pending.json`** (:data:`PENDING`) is the one host-wide record of
   settle writes that did not certainly land, :class:`PendingEntry` per write. `write` is
   `ticket-copy` (a ticket's changed fields copied back onto its item), `priority` (the
   re-estimate: the priority, and the estimate line and record beside it) or `withdrawal`
   (the status). A `ticket-copy` is sent as a `task update` of only its changed fields, but
   for the `repositories` exception below; `state` is `withheld` (the read showed a person's
   edit since the snapshot, so nothing was written), `failed` (rejected, or the response does
   not show the intended value), `partial` (some parts landed) or `unknown` (the outcome was
   lost); `intended` is the value of every field the write sets.
   **Recovery** is one rule, applied by the next pass's settle straight after its batched
   read: an entry whose every intended value the board already holds is cleared and never
   sent again; otherwise one whose unapplied fields a person changed since its
   `seen_version` (for `withheld`, since its `snapshot_version`) is surfaced as a conflict
   and kept unwritten; otherwise it is retried as an `--expect` write of only the unapplied
   fields — by the `repositories` exception where that field is among them — and removed when
   that response shows them landed. A retry rejected, partial or lost updates the entry and keeps
   it.
6. **Feedback files** are `feedback/<run>/<snapshot stamp>.md`, each naming its snapshot on
   the :data:`FEEDBACK_SNAPSHOT` line beside its :data:`FEEDBACK_BOUNDARY` line, and under each
   quoted comment the issue's board id, its snapshot id and its version on the
   :data:`ISSUE_LINE`. :func:`render`, `follow_up_tickets.read_gathering` and the template
   change together.

**A dry run and `--from`.** `--dry-run` gathers, writes the snapshot, prints the selection and
then the exact `just follow-ups-answer-comments --from <snapshot>` that launches it, and writes
nothing else: no feedback file, no `boundary.txt`, no watermark, nothing on the board. `--from`
launches from that snapshot with no board request before its launches, answering exactly the
versions its `selection` records. It is refused beside `--since`, `--run`, `--issue`, `--to`
and `--dry-run`, and refused with nothing written, naming the fresh `--dry-run` to run instead,
for a snapshot that is malformed, already launched, older than
:data:`SNAPSHOT_LAUNCHABLE_FOR`, or one a later overlapping snapshot of the same board family
was launched past — an unscoped one overlaps every snapshot, two scoped ones overlap on the
same run. It is never refused because the board or the cache moved since: what moved is the
next pass's to read. A plain run gathers and launches through the same path with no second
read, and nothing picks up a "latest" snapshot by itself.

What is read
------------

* **`--issue QUALIFIED-ID`** copies that one item by id and lists nothing. Its comments go to
  the run under this drafts root whose ticket's `board_item` binding
  (:data:`follow_up_tickets.BINDING_FIELD`) names it, found by one narrowed listing of the local
  drafts store; an item no run of this host owns — none bound, several bound, or the board's
  record naming another creator or host — is refused with why, and nothing is launched.
* **`--run RUN-ID`** copies, the same way, each item that run's local tickets are bound to, and
  lists no board.
* **Unscoped**, every board of the family `--to` roots (:func:`follow_up_tickets.boards`) is
  copied by the query the store narrows to this host's items, `--commented-since <its since>
  --metadata <record>/host=<this hostname>` (:func:`host_query`). No status filter is sent,
  because a comment on an item at another status is still reported, skipped with its reason.
  The store keeps an item when one of its comments was created or last edited at or after the
  instant, so a comment edited after its reply comes back through the same query.

**`<since>`** is decided per board, in order: `--since` as given; else that board's
**watermark**, the file :data:`WATERMARK` under `<drafts root>/feedback/`; else the earliest
bound local records give a run that has filed tickets here — its recorded boundary, else its
ticket files' last write. A run whose records give neither is **unbounded**, and the command
refuses naming it and asking for `--since`. A scoped read takes no start. **The watermark moves
only in settle**, which a dry run never reaches, and only for an unscoped snapshot with no item
it failed to capture, whose every launched run was checked sound — every reply posted and
verified by the batched read; pending ticket, priority and withdrawal writes do not hold it,
because `pending.json` drives their retry. It moves to `queried_at` less :data:`OVERLAP` for every
board in `boards`, and never backward. So a comment a gathering selected that no reply answers
is selected again.

Routing
-------

A comment goes to the run owning the issue it sits on (:func:`follow_up_tickets.issue_owner`)
and to no other. Each comment read is either selected for that run or left out for exactly one
reason, tried in this order; the first two are never met unscoped:

* **the no-answer rule** — the item carries no follow-up record, so no run answers on it;
* **ticket verified on host** — the record's `host` is not this machine's `hostname`;
* **an owning run no launch takes** — its id is outside `just follow-ups`' run-id grammar;
* **no records for the owning run** — nothing of that run is under this drafts root;
* **item at a status** — only an item at `Proposal` or `Deferred` (store categories `backlog`
  and `draft`; `Proposed` and `Backlog` on Linear) is answered;
* **marked** — a comment whose last line is any run's marker, of either kind, is not a
  person's;
* **bot author** — on a GitHub board, `github-actions` or a login ending `[bot]`; on a Linear
  one, a comment Linear names no user for;
* **answered** — a reply of **any** run names the comment's id in `answers` on the same issue
  and answers the comment's current version. **A reply answers the version its gathering
  quoted**: where this host's local response accounts (:func:`quoted_versions`) map the reply
  to the comment's last-changed instant its feedback quoted, it answers only while the comment
  is no newer than that; a reply with no local account answers when it is no older than the
  comment's last change. A comment edited after the snapshot its reply came from is
  unanswered again, and nothing stored on the board changes shape for it;
* **the boundary** — a comment at or before the owning run's boundary predates replies and
  was answered by the run's other responses.

**The boundary** is the latest of the run's responses that are not replies — its ticket files'
last write, and its evidence comments — taken strictly before its first gathering, computed
when the snapshot is gathered and carried on every feedback file's :data:`FEEDBACK_BOUNDARY`
line; the launch records one it computed in :data:`BOUNDARY_FILE` beside them. It is recorded,
never recomputed: the earliest feedback file's line wins, then that file.

What is written
---------------

Feedback files, a run's :data:`BOUNDARY_FILE` and `launched.json` at the launch; the replies,
by the dispatched agent, each `task comment add <board id> --expect <snapshot version> --json`
recording its id and URL from that response; and in settle, straight after its read and item by
item — the pass's own pending entries first, then this pass's writes — each an `--expect <the
version the read returned>` write sending only the fields it changes:

* the **priority re-estimate**, computed from the snapshot's comments and this pass's verified
  replies, never a board recount; a priority the read shows a person set stands;
* the **one withdrawal** — this run's own proposal whose ticket the comment, or
  investigating it, shows is no longer relevant, whether or not the comment says so, and never
  a deferred one — written only when the read shows the item still at `Proposal`;
* **ticket copies back**, only for tickets whose record changed, and **withheld** — recorded,
  not written — where the read shows the item changed since its snapshot version by anything
  but this pass's replies.

Each is verified from its own mutation response with no extra read, and none is retried within
the pass. A pass that ends with any entry in `pending.json` ends saying **needs attention**,
listing each. Settle writes only to the quoted issues' tickets and keeps the one withdrawal
exception (:data:`follow_up_tickets.WITHDRAWAL_EXCEPTION`), but for recovery: it also retries
or clears the entries `pending.json` already holds, which may name unquoted issues, writing
nothing to them beyond their `intended` values. A board that gives an item no version (a
Linear board) is written without `--expect`, taking the store's own read first; the report
names each such write.

**The residual race, an accepted exception.** Neither backend offers a conditional write, so a
person's edit to a field a settle write sets, landing in the seconds between the batched read
and that item's mutation, is overwritten. Edits before the read are caught and withheld, and
edits to fields the write does not set are never touched. The flow before snapshots carried
the same class of window between its pre-write read and its write.

**The `repositories` exception, the one settle write that is not field-scoped.** The pinned
store's `task update` sets no `repositories`, so a ticket whose `repositories` differ from its
item's — on the first write or among a pending entry's unapplied fields — is written with one
whole `task copy --expect` of the ticket, recorded `ticket-copy` like any ticket write. When the
board refuses that copy, the store reads the item once and its answer does not report it; the
recipe names that read as a fallback (:func:`refused_copy_read`) and counts it with the cache's.
This stands until onetaskgraph ships a field-scoped repositories update — the follow-up drafted
in run `followups-comment-snapshot` as "task update cannot set an item's repositories" — when
both the whole copy and its fallback are retired.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import re
import shlex
import socket
import subprocess
import sys
import tempfile
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import (
    Literal,
    NamedTuple,
    NoReturn,
    TypedDict,
    TypeGuard,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

from orchestrator import follow_up_tickets as tickets
from orchestrator import plan_store
from orchestrator.plan_store import RECORD_COMPONENT, QualifiedTaskId
from orchestrator.project_store import TASKS_DIRECTORY
from orchestrator.root import REPO_ROOT

FEEDBACK_DIRECTORY = "feedback"

MOMENT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"

UNKNOWN_AUTHOR = "not reported by the board"

PROG = "follow-ups-answer-comments"

#: What each command exits with: done; some launched run was not answered soundly, or a write
#: needs attention; refused before anything was launched.
DONE = 0
UNANSWERED = 1
UNRUNNABLE = 2

CommentId = tickets.CommentId

#: A feedback file's name: the stamp of the snapshot it was written from, as that snapshot's
#: directory is named.
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

#: The line, beside the boundary's, naming the snapshot directory a feedback file was written
#: from, which is every later reader's whole source for its quoted issues.
FEEDBACK_SNAPSHOT = '<!-- orchestrator:follow-up-feedback snapshot="{snapshot}" -->'
FEEDBACK_SNAPSHOT_LINE = re.compile(
    "^"
    + re.escape(FEEDBACK_SNAPSHOT).replace(re.escape("{snapshot}"), r'(?P<snapshot>[^"\n]*)')
    + "$",
    re.MULTILINE,
)

#: The label and the value of the one line under each quoted comment naming its issue's board
#: id, its id in the snapshot source and the version the snapshot holds, which the agent's reads
#: and its `--expect` reply take. `follow_up_tickets.QUOTED_METADATA_FIELDS` carries the label.
ISSUE_LABEL = "Issue"
ISSUE_LINE = "{issue}, read from {snapshot_id} at version {version}"
ISSUE_VALUE = re.compile(
    r"(?P<issue>[^\s,]+), read from (?P<snapshot_id>\S+) at version (?P<version>\S+)"
)
#: The version an :data:`ISSUE_LINE` names for an item its board gave none, or whose board's
#: source keeps no cache, which a write's `--expect` needs: such a reply names no `--expect`.
NO_VERSION = "none"

#: Where a gathering records a boundary it computed for a run, beside that run's feedback
#: files, as the one :data:`FEEDBACK_BOUNDARY` line. Not a `.md` name, so no reader of
#: gatherings reads it as one.
BOUNDARY_FILE = "boundary.txt"

#: The board's watermark under the feedback directory. A leading dot keeps it out of the
#: run ids `scripts/plan-brief.sh` admits, so no run's directory can take its name.
WATERMARK = ".watermark-{board}.json"
WATERMARK_SCHEMA = 1

#: The snapshot contract's names, stated once (the module docstring's "The snapshot
#: contract"): the directory every snapshot sits in, a snapshot's `local-md` root, its record,
#: its claim, and the host-wide record of settle writes that did not certainly land.
SNAPSHOTS_DIRECTORY = ".snapshots"
SNAPSHOT_ITEMS = "items"
SNAPSHOT_RECORD = "snapshot.json"
SNAPSHOT_LAUNCHED = "launched.json"
PENDING = "pending.json"
SNAPSHOT_SCHEMA = 1
LAUNCHED_SCHEMA = 1
PENDING_SCHEMA = 1
#: A snapshot directory's name: the query instant, and a counter when two share a second.
SNAPSHOT_NAME = re.compile(r"(?P<stamp>\d{8}T\d{6}Z)(?:-(?P<attempt>\d+))?")
#: How long after its query a snapshot may still be launched with `--from`.
SNAPSHOT_LAUNCHABLE_FOR = timedelta(hours=24)

#: The one name every process reads a snapshot's `items/` under, and the plugin serving it.
SNAPSHOT_SOURCE = "snapshot"
SNAPSHOT_PLUGIN = plan_store.WRITABLE_PLUGIN
#: The snapshot's declared visibility. It holds a copy of every item of the board's family,
#: an item a route sent to a source declared private among them, and the store's public
#: boundary writes a private item only to a source declared private; nothing is copied out of
#: the snapshot, so no copy from it loses the origin a copy into a source not declared private
#: would drop. Declared only on this module's own snapshot commands, never in
#: :func:`snapshot_environment`: any declared visibility turns the store's boundary on, and a
#: process another one starts, which writes its replies to the boards, must not have it on
#: where no screening command is configured.
SNAPSHOT_VISIBILITY = "private"
#: The two variables, at the store's environment layer, that point a process at a snapshot.
SNAPSHOT_PLUGIN_ENV = f"ONETASKGRAPH_SOURCES__{SNAPSHOT_SOURCE.upper()}__PLUGIN"
SNAPSHOT_ROOT_ENV = f"ONETASKGRAPH_SOURCES__{SNAPSHOT_SOURCE.upper()}__CONFIG__ROOT"

#: The store's own keys an item copied into the snapshot carries.
COPIED_VERSION_KEY = "onetaskgraph.copied_version"
ORIGIN_KEY = plan_store.ORIGIN_KEY


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
#: `tests/plan_tooling/follow_ups/test_follow_ups_answer_comments_recipe_e2e.py` holds the
#: two spellings to one grammar.
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

#: The reason a `left` entry records for an item the snapshot's copy reported `failed`.
NOT_CAPTURED = "not captured: the snapshot's copy failed for this item: {error}"


class Unbounded(ValueError):
    """Local records give no start for a first gathering; names every such run."""


class Unowned(ValueError):
    """An `--issue` no run of this host owns; says why."""


class Unlaunchable(ValueError):
    """A snapshot `--from` may not launch; says why and names the fresh dry run instead."""


@dataclass(frozen=True)
class Comment:
    """One comment as the snapshot holds it, every field this reads validated."""

    id: CommentId
    author: str | None
    body: str
    url: str | None
    last_changed: datetime


@dataclass(frozen=True)
class Issue:
    """One item the snapshot holds, and the comments it holds on it."""

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
    #: The plugin of the board it is on, which decides who counts as a bot on it.
    plugin: str | None = None
    #: Its id in the snapshot source, and the board version the snapshot holds it at.
    snapshot_id: str | None = None
    version: str | None = None


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
    """One comment the snapshot holds that no run is sent, and the one reason why.

    ``id`` is `None` for an item the snapshot's copy failed to capture, whose comments it holds
    none of.
    """

    issue: QualifiedTaskId
    id: CommentId | None
    url: str | None
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


def _web_url(value: object) -> str | None:
    """A URL the board reports, kept only when it is one web address a reader can open."""
    return value if isinstance(value, str) and tickets.ITEM_URL.fullmatch(value) else None


def _board_issue(identifier: str, board: str) -> bool:
    """Whether ``identifier`` is one qualified task id of ``board``."""
    matched = tickets.QUALIFIED_ID.fullmatch(identifier)
    return matched is not None and matched["source"] == board


def _one_line(text: str, what: str, qualified: str) -> None:
    """Refuse ``text`` the snapshot holds for ``qualified`` when it spans lines."""
    if {"\n", "\r"} & set(text):
        raise OSError(f"{qualified} reports {what} spanning lines")


def _location_path(location: object, qualified: str) -> str | None:
    """The path an item's location names, which becomes a comment's file URL."""
    path = location.get("path") if isinstance(location, Mapping) else None
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


def feedback_directory(root: Path) -> Path:
    """The feedback directory under a drafts root."""
    return root / FEEDBACK_DIRECTORY


def snapshots_directory(root: Path) -> Path:
    """Where every snapshot of this drafts root sits."""
    return feedback_directory(root) / SNAPSHOTS_DIRECTORY


def snapshot_environment(directory: Path) -> dict[str, str]:
    """The two variables pointing another process's store at ``directory``'s snapshot source."""
    return {
        SNAPSHOT_PLUGIN_ENV: SNAPSHOT_PLUGIN,
        SNAPSHOT_ROOT_ENV: str(directory.absolute() / SNAPSHOT_ITEMS),
    }


def _status_mapping(
    settings: Mapping[str, object], board: str, categories: Collection[str]
) -> dict[str, str]:
    """``board``'s configured status names, each to its category, out of ``settings``.

    A board's mapping is keyed by category, and a category the store configures per kind
    names a task's state under `.task`, which is the one a ticket is held at. A `local-md`
    board's is the other way round, keyed by the name a file writes; an entry of one naming no
    store category among ``categories`` is one it can hold no item at, and is left out.
    """
    prefix = f"sources.{board}.config.status_mapping."
    by_name = settings.get(f"sources.{board}.plugin") == SNAPSHOT_PLUGIN
    names: dict[str, str] = {}
    for key, value in settings.items():
        if not key.startswith(prefix) or not isinstance(value, str):
            continue
        if by_name:
            if value in categories:
                names[key[len(prefix) :].lower()] = value
            continue
        category, _, kind = key[len(prefix) :].partition(".")
        if kind in ("", "task"):
            names[value.lower()] = category
    return names


def _local_md_defaults() -> dict[str, str]:
    """The `local-md` plugin's own default status names, read off the store's schema."""
    shown = subprocess.run(  # noqa: S603 - the locked store beside this interpreter
        [str(plan_store.locked_binary()), "schema"],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    try:
        schema = json.loads(shown.stdout)
        default = schema["plugin_config"][SNAPSHOT_PLUGIN]["properties"]["status_mapping"][
            "default"
        ]
    except (json.JSONDecodeError, KeyError, TypeError):
        default = None
    if not isinstance(default, dict) or not all(
        isinstance(name, str) and isinstance(category, str) for name, category in default.items()
    ):
        raise OSError(
            f"the plan store's schema names no default status mapping for {SNAPSHOT_PLUGIN}: "
            f"{shown.stderr.strip() or shown.stdout[:200]}"
        )
    return dict(default)


def snapshot_status_names(board: str) -> dict[str, str]:
    """Every status name an item of ``board``'s family can be at, each to its store category.

    The snapshot source is a `local-md` folder, which reads a status by name, and a copy
    refuses an item whose status the destination would read as another category. So it is
    handed the family boards' own names, read off the store's resolution of this checkout's
    configuration, over the plugin's defaults — which cover the board names a source leaves
    at their shipped default. :class:`OSError` for two boards giving one name two categories,
    or a name the store's dotted setting path cannot carry.
    """
    settings = plan_store.configured_settings()
    names = _local_md_defaults()
    claimed: dict[str, tuple[str, str]] = {}
    categories = frozenset(names.values())
    for one in tickets.boards(board):
        for name, category in _status_mapping(settings, one, categories).items():
            if name in claimed and claimed[name][1] != category:
                raise OSError(
                    f"the boards {claimed[name][0]!r} and {one!r} read the status {name!r} as "
                    f"{claimed[name][1]!r} and {category!r}, so one snapshot cannot hold both"
                )
            if {".", "=", "\n"} & set(name):
                raise OSError(f"board {one!r} names a status {name!r} no setting path carries")
            claimed[name] = (one, category)
            names[name] = category
    return names


def snapshot_settings(directory: Path, board: str) -> list[str]:
    """The `--set` settings every read or copy this module makes of the snapshot passes.

    The plugin and the root :func:`snapshot_environment` exports, the snapshot's declared
    :data:`SNAPSHOT_VISIBILITY`, and the status names of :func:`snapshot_status_names`, which
    no environment variable can spell.
    """
    root = directory.absolute() / SNAPSHOT_ITEMS
    settings = [
        f"sources.{SNAPSHOT_SOURCE}.plugin={SNAPSHOT_PLUGIN}",
        f"sources.{SNAPSHOT_SOURCE}.config.root={root}",
        f"sources.{SNAPSHOT_SOURCE}.visibility={SNAPSHOT_VISIBILITY}",
    ]
    settings.extend(
        f"sources.{SNAPSHOT_SOURCE}.config.status_mapping.{name}={category}"
        for name, category in sorted(snapshot_status_names(board).items())
    )
    return settings


class SnapshotItem(NamedTuple):
    """One item the snapshot holds, as `snapshot.json` records it."""

    id: QualifiedTaskId
    snapshot_id: QualifiedTaskId
    version: str | None
    title: str
    owner: tickets.RunId | None
    host: str | None
    url: str | None


class Chosen(NamedTuple):
    """One comment the snapshot selected for a run, at the version it holds."""

    issue: QualifiedTaskId
    comment: CommentId
    last_changed: str
    body_sha256: str


class LeftOut(NamedTuple):
    """One comment left out, or an item not captured (``comment`` `None`), and why."""

    issue: QualifiedTaskId
    comment: CommentId | None
    reason: str


class RunBoundary(NamedTuple):
    """One recorded run's boundary instant, and whether this gathering computed it."""

    boundary: str | None
    computed: bool


class Start(NamedTuple):
    """Where one gathering's query starts, and the words saying where that came from."""

    since: datetime
    origin: str


@dataclass(frozen=True)
class Snapshot:
    """`snapshot.json`, field for field, and the directory it was read from."""

    directory: Path
    queried_at: datetime
    board: str
    boards: tuple[str, ...]
    named_board: bool
    scope: tickets.RunId | None
    issue: QualifiedTaskId | None
    starts: Mapping[str, Start]
    store: str
    dry_run: bool
    items: tuple[SnapshotItem, ...]
    boundaries: Mapping[tickets.RunId, RunBoundary]
    selection: Mapping[tickets.RunId, tuple[Chosen, ...]]
    left: tuple[LeftOut, ...]

    @property
    def captured_all(self) -> bool:
        """Whether the copy captured every item its query or its ids named."""
        return all(one.comment is not None for one in self.left)

    def item(self, issue: QualifiedTaskId) -> SnapshotItem | None:
        """The record of ``issue``, if the snapshot holds it."""
        return next((one for one in self.items if one.id == issue), None)

    def settings(self) -> list[str]:
        """The settings reading this snapshot's source."""
        return snapshot_settings(self.directory, self.board)


#: Every key `snapshot.json` carries, which :func:`read_snapshot` holds a file to exactly.
SNAPSHOT_KEYS = (
    "schema",
    "queried_at",
    "board",
    "boards",
    "named_board",
    "scope",
    "issue",
    "starts",
    "store",
    "dry_run",
    "items",
    "boundaries",
    "selection",
    "left",
)


def _snapshot_document(snapshot: Snapshot) -> dict[str, object]:
    return {
        "schema": SNAPSHOT_SCHEMA,
        "queried_at": instant(snapshot.queried_at),
        "board": snapshot.board,
        "boards": list(snapshot.boards),
        "named_board": snapshot.named_board,
        "scope": snapshot.scope,
        "issue": snapshot.issue,
        "starts": {
            board: {"since": instant(start.since), "origin": start.origin}
            for board, start in snapshot.starts.items()
        },
        "store": snapshot.store,
        "dry_run": snapshot.dry_run,
        "items": [one._asdict() for one in snapshot.items],
        "boundaries": {run: one._asdict() for run, one in snapshot.boundaries.items()},
        "selection": {
            run: [one._asdict() for one in chosen] for run, chosen in snapshot.selection.items()
        },
        "left": [one._asdict() for one in snapshot.left],
    }


def _optional_str(value: object) -> TypeGuard[str | None]:
    return value is None or isinstance(value, str)


def _fields(value: object, kinds: Mapping[str, Callable[[object], bool]]) -> bool:
    return (
        isinstance(value, dict)
        and sorted(value) == sorted(kinds)
        and all(check(value[key]) for key, check in kinds.items())
    )


def _is_str(value: object) -> bool:
    return isinstance(value, str)


#: A comment body's SHA-256, as :func:`body_sha256` writes it.
DIGEST = re.compile(r"[0-9a-f]{64}")


def _is_moment(value: object) -> bool:
    return isinstance(value, str) and RFC3339.fullmatch(value) is not None


def _is_qualified(value: object) -> bool:
    return isinstance(value, str) and tickets.QUALIFIED_ID.fullmatch(value) is not None


_ITEM_FIELDS: dict[str, Callable[[object], bool]] = {
    "id": _is_qualified,
    "snapshot_id": lambda value: (
        _is_qualified(value) and str(value).startswith(f"{SNAPSHOT_SOURCE}:")
    ),
    "version": _optional_str,
    "title": _is_str,
    "owner": lambda value: (
        value is None or (isinstance(value, str) and RECORD_COMPONENT.fullmatch(value) is not None)
    ),
    "host": _optional_str,
    "url": _optional_str,
}
_CHOSEN_FIELDS: dict[str, Callable[[object], bool]] = {
    "issue": _is_qualified,
    "comment": lambda value: isinstance(value, str) and bool(value),
    "last_changed": _is_moment,
    "body_sha256": lambda value: isinstance(value, str) and DIGEST.fullmatch(value) is not None,
}
_LEFT_FIELDS: dict[str, Callable[[object], bool]] = {
    "issue": _is_qualified,
    "comment": lambda value: value is None or (isinstance(value, str) and bool(value)),
    "reason": _is_str,
}
_BOUNDARY_FIELDS: dict[str, Callable[[object], bool]] = {
    "boundary": lambda value: value is None or _is_moment(value),
    "computed": lambda value: isinstance(value, bool),
}
_START_FIELDS: dict[str, Callable[[object], bool]] = {"since": _is_moment, "origin": _is_str}


def _well_formed(held: object, directory: Path) -> bool:  # noqa: C901 - one check per field
    """Whether ``held`` is a `snapshot.json` of this schema, every field of its type."""
    if not isinstance(held, dict) or sorted(held) != sorted(SNAPSHOT_KEYS):
        return False
    boards = held["boards"]
    if (
        type(held["schema"]) is not int
        or held["schema"] != SNAPSHOT_SCHEMA
        or not isinstance(held["board"], str)
        or not RECORD_COMPONENT.fullmatch(held["board"])
        or not isinstance(boards, list)
        or not boards
        or boards[0] != held["board"]
        or not all(isinstance(one, str) and RECORD_COMPONENT.fullmatch(one) for one in boards)
        or not isinstance(held["named_board"], bool)
        or not isinstance(held["dry_run"], bool)
        or not isinstance(held["store"], str)
        or not _is_moment(held["queried_at"])
        or not (
            held["scope"] is None
            or (isinstance(held["scope"], str) and LAUNCHABLE_RUN.fullmatch(held["scope"]))
        )
        or not (held["issue"] is None or _is_qualified(held["issue"]))
        or not SNAPSHOT_NAME.fullmatch(directory.name)
    ):
        return False
    starts, items, boundaries = held["starts"], held["items"], held["boundaries"]
    selection, left = held["selection"], held["left"]
    return (
        isinstance(starts, dict)
        and all(board in boards and _fields(one, _START_FIELDS) for board, one in starts.items())
        and isinstance(items, list)
        and all(
            _fields(one, _ITEM_FIELDS) and str(one["id"]).partition(":")[0] in boards
            for one in items
        )
        # One record per item, each copied to a task of its own.
        and len({one["id"] for one in items}) == len(items)
        and len({one["snapshot_id"] for one in items}) == len(items)
        and isinstance(boundaries, dict)
        and all(
            # Recorded for every run with records here, launchable or not.
            RECORD_COMPONENT.fullmatch(run) and _fields(one, _BOUNDARY_FIELDS)
            for run, one in boundaries.items()
        )
        and isinstance(selection, dict)
        and all(
            LAUNCHABLE_RUN.fullmatch(run)
            and isinstance(chosen, list)
            and chosen
            # A comment is selected only on an item the snapshot captured.
            and all(
                _fields(one, _CHOSEN_FIELDS) and one["issue"] in {str(item["id"]) for item in items}
                for one in chosen
            )
            # Each comment selected once, so it is answered once.
            and len({(one["issue"], one["comment"]) for one in chosen}) == len(chosen)
            for run, chosen in selection.items()
        )
        and isinstance(left, list)
        and all(_fields(one, _LEFT_FIELDS) for one in left)
    )


def read_snapshot(directory: Path) -> Snapshot:
    """The snapshot at ``directory``: the one reader of `snapshot.json`.

    :class:`OSError` naming the directory for a record missing, unreadable or of any other
    shape, since a snapshot this cannot trust would launch or settle runs from comments nobody
    gathered.
    """
    path = directory / SNAPSHOT_RECORD
    try:
        held: object = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise OSError(f"{directory} holds no {SNAPSHOT_RECORD}, so it is no snapshot") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as broken:
        raise OSError(f"{path} is not JSON: {broken}") from None
    if not _well_formed(held, directory) or not (directory / SNAPSHOT_ITEMS).is_dir():
        raise OSError(
            f"{directory} is not a schema-{SNAPSHOT_SCHEMA} snapshot: its {SNAPSHOT_RECORD} "
            f"carries none of, or other than, {', '.join(SNAPSHOT_KEYS)} with their types, or "
            f"it holds no {SNAPSHOT_ITEMS}/"
        )
    held = cast(dict[str, object], held)
    starts = cast(dict[str, dict[str, str]], held["starts"])
    return Snapshot(
        directory=directory,
        queried_at=moment(cast(str, held["queried_at"]), str(path)),
        board=cast(str, held["board"]),
        boards=tuple(cast(list[str], held["boards"])),
        named_board=cast(bool, held["named_board"]),
        scope=cast(tickets.RunId | None, held["scope"]),
        issue=cast(QualifiedTaskId | None, held["issue"]),
        starts={
            board: Start(moment(one["since"], str(path)), one["origin"])
            for board, one in starts.items()
        },
        store=cast(str, held["store"]),
        dry_run=cast(bool, held["dry_run"]),
        # Every identifier below was held to its grammar by `_well_formed` above.
        items=tuple(
            SnapshotItem(
                QualifiedTaskId(cast(str, one["id"])),
                QualifiedTaskId(cast(str, one["snapshot_id"])),
                one["version"],
                cast(str, one["title"]),
                cast(tickets.RunId | None, one["owner"]),
                one["host"],
                one["url"],
            )
            for one in cast(list[dict[str, str | None]], held["items"])
        ),
        boundaries={
            tickets.RunId(run): RunBoundary(
                cast(str | None, one["boundary"]), bool(one["computed"])
            )
            for run, one in cast(dict[str, dict[str, object]], held["boundaries"]).items()
        },
        selection={
            tickets.RunId(run): tuple(
                Chosen(
                    QualifiedTaskId(one["issue"]),
                    CommentId(one["comment"]),
                    one["last_changed"],
                    one["body_sha256"],
                )
                for one in chosen
            )
            for run, chosen in cast(dict[str, list[dict[str, str]]], held["selection"]).items()
        },
        left=tuple(
            LeftOut(
                QualifiedTaskId(cast(str, one["issue"])),
                cast(CommentId | None, one["comment"]),
                cast(str, one["reason"]),
            )
            for one in cast(list[dict[str, str | None]], held["left"])
        ),
    )


def snapshot_name(queried_at: datetime, attempt: int) -> str:
    """The name of the ``attempt``-th snapshot queried in the second of ``queried_at``."""
    stamp = queried_at.astimezone(UTC).strftime(STAMP_FORMAT)
    return stamp if attempt == 1 else f"{stamp}-{attempt}"


def _staged(root: Path) -> Path:
    """A fresh staging directory for one snapshot, beside where it will be renamed to."""
    parent = snapshots_directory(root)
    parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".staging-", dir=parent))
    (staged / SNAPSHOT_ITEMS).mkdir()
    return staged


def publish_snapshot(staged: Path, snapshot: Snapshot) -> Snapshot:
    """Write ``snapshot``'s record into ``staged`` and rename it into place whole.

    The one writer of `snapshot.json`. It is renamed to the first free name its query
    instant gives, so two gatherings in one second keep both.
    """
    (staged / SNAPSHOT_RECORD).write_text(
        json.dumps(_snapshot_document(snapshot), indent=2) + "\n", encoding="utf-8"
    )
    attempt = 1
    while True:
        target = staged.parent / snapshot_name(snapshot.queried_at, attempt)
        try:
            # A rename onto a snapshot already there is refused, since that one holds files.
            staged.rename(target)
        except OSError:
            if not target.exists():
                raise
            attempt += 1
            continue
        return dataclasses.replace(snapshot, directory=target)


class StoreAnswer(NamedTuple):
    """One store command's exit status, its JSON document if it printed one, and its stderr."""

    status: int
    document: Mapping[str, object] | None
    stderr: str


def store_command(arguments: Sequence[str]) -> StoreAnswer:
    """Run the locked plan store with ``arguments`` and `--json`, keeping its answer whatever it is.

    A write's own response is what settle verifies it from, and a refused or partial write
    exits non-zero with that response on standard output, which the SDK would discard.
    """
    done = subprocess.run(  # noqa: S603 - the locked store beside this interpreter
        [str(plan_store.locked_binary()), *arguments, "--json", "--no-interactive"],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    try:
        held: object = json.loads(done.stdout) if done.stdout.strip() else None
    except json.JSONDecodeError:
        held = None
    document = cast(Mapping[str, object], held) if isinstance(held, Mapping) else None
    return StoreAnswer(done.returncode, document, done.stderr.strip())


def _setting_words(settings: Sequence[str]) -> list[str]:
    return [word for setting in settings for word in ("--set", setting)]


def _failure_message(answer: StoreAnswer) -> str:
    failure = answer.document.get("failure") if answer.document is not None else None
    message = failure.get("message") if isinstance(failure, Mapping) else None
    return str(message or answer.stderr or f"the store exited {answer.status}")


class Copied(NamedTuple):
    """What one gathering copy put into the snapshot: each item's snapshot id, and each failure."""

    held: dict[str, str]
    failed: dict[str, str]


def copy_into_snapshot(
    staged: Path, board: str, arguments: Sequence[str], settings: Sequence[str]
) -> Copied:
    """One `task copy --to snapshot --comments --no-link`, by query or by ids: what it copied.

    :class:`OSError` when the store refused the copy whole, naming why.
    """
    answer = store_command(
        [
            "task",
            "copy",
            *arguments,
            "--to",
            SNAPSHOT_SOURCE,
            "--comments",
            "--no-link",
            *_setting_words(settings),
        ]
    )
    entries = answer.document.get("items") if answer.document is not None else None
    if not isinstance(entries, list) or answer.status not in (0, 4):
        raise OSError(
            f"the copy of {board!r} into {staged} was refused: {_failure_message(answer)}"
        )
    held: dict[str, str] = {}
    failed: dict[str, str] = {}
    # By query, every source is an item of the board queried; by ids, one of the ids asked for.
    queried = arguments[1] if list(arguments[:1]) == ["--source"] else None
    for entry in entries:
        source = entry.get("source") if isinstance(entry, Mapping) else None
        if not _is_qualified(source):
            raise OSError(f"the copy of {board!r} answered an entry naming no source: {entry!r}")
        source = cast(str, source)
        if source.partition(":")[0] != queried if queried is not None else source not in arguments:
            raise OSError(f"the copy of {board!r} answered {source}, which it was not asked for")
        destination = entry.get("destination")
        if destination is not None and not (
            _is_qualified(destination) and str(destination).startswith(f"{SNAPSHOT_SOURCE}:")
        ):
            raise OSError(
                f"the copy of {board!r} answered {source} copied to {destination!r}, which is "
                f"no task of the snapshot source `{SNAPSHOT_SOURCE}`"
            )
        if source in held or source in failed:
            raise OSError(f"the copy of {board!r} answered {source} more than once")
        if entry.get("action") == "failed" or not isinstance(destination, str):
            error = entry.get("error")
            message = error.get("message") if isinstance(error, Mapping) else error
            failed[source] = " ".join(str(message or "the copy named no error").split())
            continue
        held[source] = destination
    if len(set(held.values())) != len(held):
        raise OSError(f"the copy of {board!r} answered two items copied to one task")
    # By ids, an id the answer left out was captured no more than one it failed.
    for asked in arguments if queried is None else ():
        if asked not in held and asked not in failed:
            failed[asked] = "the copy answered nothing for it"
    return Copied(held, failed)


def shown_details(ids: Sequence[str], settings: Sequence[str] = ()) -> list[Mapping[str, object]]:
    """One `task show-many` over ``ids``: one detail per id, in order, comments included.

    :class:`OSError` for an answer that is not one detail per id; a detail carrying its own
    `errors` is the caller's to read.
    """
    if not ids:
        return []
    answer = store_command(["task", "show-many", *ids, *_setting_words(settings)])
    details = answer.document.get("details") if answer.document is not None else None
    if not isinstance(details, list) or len(details) != len(ids):
        raise OSError(f"`task show-many` of {', '.join(ids)} answered: {_failure_message(answer)}")
    return [one if isinstance(one, Mapping) else {} for one in details]


def detail_item(detail: Mapping[str, object], what: str) -> dict[str, object]:
    """The one item a `task show` detail holds for the task ``what``; :class:`OSError` for an
    error, another count, or an item of another task, whose fields would be read as ``what``'s."""
    items = detail.get("items")
    errors = detail.get("errors")
    if errors or not isinstance(items, list) or len(items) != 1:
        raise OSError(f"{what} could not be read: {errors or items!r}")
    (held,) = items
    item = held.get("item") if isinstance(held, Mapping) else None
    if not isinstance(item, Mapping):
        raise OSError(f"{what} answered no item: {held!r}")
    answered = cast(Mapping[str, object], held).get("id")
    if answered != what:
        raise OSError(f"{what} was answered with the task {answered!r}")
    return dict(item)


def detail_comments(detail: Mapping[str, object], what: str) -> list[Mapping[str, object]]:
    """The comments a `task show` detail of ``what`` carries, oldest first.

    Every follow-up board keeps comments, so a detail carrying none, or a comment naming no id
    or no body, is an answer this cannot read rather than an item nobody commented on:
    :class:`OSError` naming it, since a snapshot missing a comment would read as complete.
    """
    comments = detail.get("comments")
    if not isinstance(comments, list) or not all(
        isinstance(one, Mapping)
        and isinstance(one.get("id"), str)
        and isinstance(one.get("body"), str)
        for one in comments
    ):
        raise OSError(f"the comments of {what} could not be read: {comments!r}")
    return list(comments)


def _record(item: Mapping[str, object]) -> Mapping[str, object]:
    metadata = item.get("metadata")
    held = metadata.get(tickets.KEY) if isinstance(metadata, Mapping) else None
    return held if isinstance(held, Mapping) else {}


#: Every store category an item's status can be at: a ticket's, and the one the store gives a
#: status its vocabulary cannot place.
CATEGORIES = frozenset({*(one.value for one in tickets.Status), "unknown"})


def _status(item: Mapping[str, object]) -> tuple[str, str]:
    """The store category of ``item``'s status and the name its board shows; :class:`OSError`
    for an item answering none, since every write settle decides by status rests on it."""
    status = item.get("status")
    category = status.get("category") if isinstance(status, Mapping) else None
    name = status.get("name") if isinstance(status, Mapping) else None
    if not isinstance(category, str) or category not in CATEGORIES or not _optional_str(name):
        raise OSError(f"the item {item.get('id')!r} answered no status the store names: {status!r}")
    return category, name or ""


def _issue_url(comments: Sequence[Comment]) -> str | None:
    """The board item's own URL, read off a copied comment's address on it."""
    for comment in comments:
        if comment.url is not None and "#" in comment.url:
            return comment.url.partition("#")[0]
    return None


def snapshot_issue(
    detail: Mapping[str, object], board_id: str, snapshot_id: str, plugin: str | None
) -> Issue:
    """One snapshot item, read back through the snapshot source, as :func:`select` reads it.

    Each comment is its board original's: its id, author, address and times are the ones the
    copy recorded as its `origin`.
    """
    item = detail_item(detail, snapshot_id)
    metadata = item.get("metadata")
    origin = metadata.get(ORIGIN_KEY) if isinstance(metadata, Mapping) else None
    if origin != board_id:
        raise OSError(
            f"{snapshot_id} names {origin!r} as its origin, where it was copied from {board_id}"
        )
    title = item.get("title")
    if not isinstance(title, str):
        raise OSError(f"{snapshot_id} answered no title: {title!r}")
    category, status = _status(item)
    host = _record(item).get(tickets.HOST_FIELD)
    _one_line(title, "a title", board_id)
    _one_line(status, "a status name", board_id)
    if isinstance(host, str):
        _one_line(host, "a host", board_id)
    comments = []
    for held in detail_comments(detail, snapshot_id):
        origin_comment = held.get("origin")
        source = origin_comment if isinstance(origin_comment, Mapping) else held
        identifier = source.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise OSError(f"a comment {snapshot_id} holds names no origin id: {held!r}")
        author = source.get("author")
        if isinstance(author, str):
            # The author is quoted on one line of the feedback, which a line break would end.
            _one_line(author, f"comment {identifier!r}'s author", board_id)
        stamp = source.get("updated_at") or source.get("created_at")
        comments.append(
            Comment(
                id=CommentId(identifier),
                author=author if isinstance(author, str) else None,
                body=str(held["body"]),
                url=_web_url(source.get("url")),
                last_changed=moment(
                    stamp if isinstance(stamp, str) else None,
                    f"comment {identifier!r} on {board_id}",
                ),
            )
        )
    path = _location_path(item.get("location"), board_id)
    version = metadata.get(COPIED_VERSION_KEY) if isinstance(metadata, Mapping) else None
    return Issue(
        id=QualifiedTaskId(board_id),
        title=title,
        owner=tickets.issue_owner(item),
        location=path,
        url=_issue_url(comments),
        comments=tuple(comments),
        category=category,
        status=status,
        host=host if isinstance(host, str) else None,
        plugin=plugin,
        snapshot_id=snapshot_id,
        version=version if isinstance(version, str) else None,
    )


def snapshot_issues(snapshot: Snapshot) -> list[Issue]:
    """Every item ``snapshot`` holds, read back through its source; no board request."""
    settings = plan_store.configured_settings()
    details = shown_details([one.snapshot_id for one in snapshot.items], snapshot.settings())
    return [
        snapshot_issue(detail, one.id, one.snapshot_id, _plugin(settings, one.id.partition(":")[0]))
        for one, detail in zip(snapshot.items, details, strict=True)
    ]


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
        native = _record(item).get(tickets.BINDING_FIELD)
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

    The stand-in for the run's last ticket copy: the follow-up agent writes each ticket
    immediately before it is copied, nothing on the board touches the file, and no copy is
    recorded anywhere else.
    """
    directory = root / TASKS_DIRECTORY / run / tickets.TICKETS
    times = [path.stat().st_mtime for path in directory.glob(f"*{tickets.TICKET_SUFFIX}")]
    return datetime.fromtimestamp(max(times), UTC) if times else None


def has_records(root: Path, run: str) -> bool:
    """Whether anything of ``run`` is under this drafts root: its drafts project or feedback."""
    return (root / TASKS_DIRECTORY / run).is_dir() or (feedback_directory(root) / run).is_dir()


def ticketed_or_gathered_runs(root: Path) -> list[tickets.RunId]:
    """Every run that has filed tickets here or been gathered for, the runs a board can owe.

    A run whose drafts were never verified into a ticket owns no issue, so its records say
    nothing about where a first gathering must start.
    """
    found = {
        path.parent.name
        for path in (root / TASKS_DIRECTORY).glob(f"*/{tickets.TICKETS}")
        if path.is_dir()
    } | {path.name for path in feedback_directory(root).glob("*") if path.is_dir()}
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
    """One comment URL from the fields the gathering and its validator both read."""
    if direct_url is not None:
        return direct_url
    fragment = f"#comment-{comment_id}"
    if issue_url is not None:
        return issue_url + fragment
    if location is not None:
        return Path(location).absolute().as_uri() + fragment
    raise OSError(f"the snapshot holds no URL and no location for {issue_id} or its comments")


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


def recorded_snapshot(text: str) -> Path | None:
    """The snapshot directory a feedback file's text names, if it names one."""
    matched = FEEDBACK_SNAPSHOT_LINE.search(text)
    return None if matched is None else Path(matched["snapshot"])


def first_gathering(root: Path, run: str) -> Gathering | None:
    """``run``'s first gathering, read off its earliest feedback file, if it was ever gathered.

    A file under the directory whose name is not a gathering's stamp was not written here, so
    it is not read as one; two gatherings in one second are ordered by their counter.
    """
    written = []
    for path in (feedback_directory(root) / run).glob("*.md"):
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
    """Where a launch records the boundary its snapshot computed for ``run``."""
    return feedback_directory(root) / run / BOUNDARY_FILE


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


def _accounts(root: Path) -> Iterator[tuple[Path, Mapping[str, object]]]:
    """Every response account under ``root``'s feedback, beside the feedback file it answers."""
    for path in sorted(feedback_directory(root).glob(f"*/*{tickets.RESPONSES_SUFFIX}")):
        try:
            held: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(held, Mapping):
            yield path, held


def quoted_versions(root: Path) -> dict[tuple[str, str], datetime]:
    """Each reply this host's local accounts record, by issue and id, to the version it answered.

    The version is the comment's last-changed instant the feedback file the account answers
    quoted. An account, or an entry, this cannot read maps nothing, which leaves its reply to
    the time rule rather than refusing the gathering over another pass's file.
    """
    versions: dict[tuple[str, str], datetime] = {}
    for path, held in _accounts(root):
        feedback = path.with_name(path.name.removesuffix(tickets.RESPONSES_SUFFIX) + ".md")
        if not feedback.is_file():
            continue
        gathering = tickets.read_gathering(feedback.read_text(encoding="utf-8"))
        quoted: dict[tuple[str, str], str | None] = {
            (one.issue, one.comment): fields.last_changed
            for one, fields in zip(gathering.quoted, gathering.metadata, strict=True)
        }
        responses = held.get("responses")
        for entry in responses if isinstance(responses, list) else []:
            if not isinstance(entry, Mapping):
                continue
            issue, comment, reply = entry.get("issue"), entry.get("comment"), entry.get("reply")
            changed = quoted.get((str(issue), str(comment)))
            if isinstance(reply, str) and changed is not None and RFC3339.fullmatch(changed):
                versions[str(issue), reply] = moment(changed, str(feedback))
    return versions


def select(
    issues: Sequence[Issue],
    boundaries: Mapping[tickets.RunId, Boundary],
    host: str,
    recorded: Collection[tickets.RunId],
    answered_at: Mapping[tuple[str, str], datetime] | None = None,
) -> Selection:
    """Every comment on ``issues``, each selected for its owning run or left out once.

    The rule, and the order its reasons are tried in, is the module's. ``answered_at`` is
    :func:`quoted_versions`: the comment version each reply this host's accounts record answered.
    """
    answered_at = answered_at or {}
    # A reply is posted on the issue holding the comment it answers, so it is looked up there:
    # a board whose comment ids are unique only within an issue cannot answer the wrong one.
    replied: dict[tuple[QualifiedTaskId, CommentId], list[tuple[Reply, datetime | None]]] = {}
    for issue in issues:
        for comment in issue.comments:
            owner = tickets.comment_owner(comment.body)
            if owner is None or owner.answers is None:
                continue
            this = (
                Reply(comment.last_changed, comment.id),
                answered_at.get((issue.id, comment.id)),
            )
            replied.setdefault((issue.id, owner.answers), []).append(this)
    chosen: dict[tickets.RunId, list[Selected]] = {}
    left: list[Left] = []
    for issue in issues:
        for comment in issue.comments:
            url = comment_url(issue, comment)
            reason = _left_out(issue, comment, replied, boundaries, host, recorded)
            if reason is not None:
                left.append(Left(issue.id, comment.id, url, reason))
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


def _answering(
    comment: Comment, replies: Sequence[tuple[Reply, datetime | None]]
) -> CommentId | None:
    """The latest reply that answers ``comment`` as it now stands, by the module's rule."""
    answering = [
        reply
        for reply, quoted in replies
        if (
            quoted >= comment.last_changed
            if quoted is not None
            else reply.last_changed >= comment.last_changed
        )
    ]
    return max(answering).id if answering else None


def _left_out(  # noqa: PLR0911, PLR0913 - one return per reason the module names, in its order
    issue: Issue,
    comment: Comment,
    replied: Mapping[tuple[QualifiedTaskId, CommentId], Sequence[tuple[Reply, datetime | None]]],
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
    answered = _answering(comment, replied.get((issue.id, comment.id), ()))
    if answered is not None:
        return f"answered by reply {answered}"
    since = boundaries.get(issue.owner, Boundary(None)).moment
    if since is not None and comment.last_changed <= since:
        return f"at or before run {issue.owner}'s boundary {instant(since)}"
    return None


def _fenced(text: str) -> str:
    """``text`` verbatim inside a fence longer than any backtick run it holds."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text.rstrip()}\n{fence}\n"


def body_sha256(text: str) -> str:
    """The digest a snapshot's selection records a comment's body under."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def issue_line(issue: str, snapshot_id: str, version: str | None) -> str:
    """The value of the :data:`ISSUE_LABEL` line under one quoted comment."""
    return ISSUE_LINE.format(
        issue=issue, snapshot_id=snapshot_id, version=version if version is not None else NO_VERSION
    )


def render(
    run: str,
    board: str,
    chosen: Sequence[Selected],
    since: datetime | None,
    snapshot: Path,
) -> str:
    """The feedback file: what the comments are, what to do, then each comment verbatim."""
    settings = plan_store.configured_settings()
    after = f", each changed after {instant(since)}," if since is not None else ""
    sections = [
        f"{boundary_line(since)}\n{FEEDBACK_SNAPSHOT.format(snapshot=snapshot)}\n\n"
        f"People commented on run `{run}`'s follow-ups on the `{board}` board or a board it "
        f"routes to{after} and no "
        "run's reply answers them yet. Gathered by `just follow-ups-answer-comments` into the "
        f"snapshot at `{snapshot}`, each is quoted verbatim below with its id, its URL, its "
        "author, when it last changed, and its issue's id on the board, in the snapshot source "
        f"`{SNAPSHOT_SOURCE}` and the version the snapshot holds.\n\n"
        "For each quoted comment, in this order:\n\n"
        "1. **Read it** from the snapshot source, never from the board: the issue with "
        f"`task show <its {SNAPSHOT_SOURCE} id> --json` and its comments with `task comment "
        f"list <its {SNAPSHOT_SOURCE} id>`.\n"
        '2. **Act on it** under "Ownership on the board" above: decide whether the ticket of '
        "the issue it sits on should change in light of it and of anything investigating it "
        "found, whether or not it asks for an edit, and make that change in the ticket's file; "
        "the board is written from it once this run settles.\n"
        "3. **Post its one reply**, naming the comment's id, as those rules state, with `task "
        "comment add <its board id> --expect <its version> --json` — after a reply of yours on "
        "that issue, expecting the version that reply's answer carried, and with no `--expect` "
        f"where its version is `{NO_VERSION}` or that answer carried none — saying what changed "
        "in the "
        "ticket and why, or why the ticket stands as it is, and record the reply's id and URL "
        "from that command's answer.\n"
        "4. **Report** the comment's URL beside what you did about it, or why you did "
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
            f"- Issue title: {issue.title}\n"
            f"- {ISSUE_LABEL}: "
            + issue_line(
                str(issue.id),
                str(issue.snapshot_id),
                expectable_version(str(issue.id), issue.version, settings),
            )
            + "\n\n"
            + _fenced(selected.text)
        )
    return "\n".join(sections)


def write_feedback(path: Path, text: str) -> Path:
    """Write one feedback file at ``path``, which nothing may have written before; its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)
    return path


def watermark_path(root: Path, board: str) -> Path:
    """The board's watermark file under the drafts root."""
    return feedback_directory(root) / WATERMARK.format(board=board)


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
    """Advance the board's watermark past a gathering that queried at ``queried_at``; never back.

    Returns where it stands afterwards: ``queried_at`` less :data:`OVERLAP`, or the later start
    a watermark already held.
    """
    since = queried_at - OVERLAP
    held = read_watermark(root, board)
    if held is not None and held >= since:
        return held
    path = watermark_path(root, board)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + ".partial")
    record = Watermark(WATERMARK_SCHEMA, board, instant(since), instant(queried_at))
    staged.write_text(json.dumps(record._asdict(), indent=2) + "\n", encoding="utf-8")
    staged.replace(path)
    return since


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


Say = Callable[[str], None]


def _say(line: str) -> None:
    print(line, flush=True)


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


def from_command(directory: Path) -> str:
    """The exact command that launches the snapshot at ``directory``."""
    return f"just {PROG} --from {shlex.quote(str(directory.absolute()))}"


def dry_run_command(snapshot: Snapshot) -> str:
    """The fresh `--dry-run` that gathers again what ``snapshot`` gathered."""
    words = ["just", PROG, "--dry-run"]
    if snapshot.issue is not None:
        words += ["--issue", snapshot.issue]
    elif snapshot.scope is not None:
        words += ["--run", snapshot.scope]
    if snapshot.named_board:
        words += ["--to", snapshot.board]
    return shlex.join(words)


def _starts(
    root: Path, family: Sequence[str], since: datetime | None, say: Say, board: str
) -> dict[str, Start] | None:
    """Where each board's query starts, or ``None`` when no run here can be owed a comment."""
    starts: dict[str, Start] = {}
    derived: Start | None = None
    for read in family:
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
    return starts


def gather(  # noqa: PLR0913, PLR0915 - each is one flag of the command
    root: Path,
    board: str,
    *,
    named_board: bool = False,
    since: datetime | None = None,
    scope: tickets.RunId | None = None,
    issue: QualifiedTaskId | None = None,
    dry_run: bool = False,
    host: str | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    say: Say = _say,
) -> Snapshot | None:
    """Copy the items this pass is about into a snapshot, and report every comment on them.

    ``board`` roots the family (:func:`follow_up_tickets.boards`). Unscoped, each board is
    copied once, narrowed to this host's items, from a start of its own: ``since``, else its
    watermark, else the start local records derive. A ``scope`` run's bound items, or the one
    ``issue``, are copied by id instead, and listed on no board. Returns the snapshot, or
    ``None`` on a host no comment can be owed on. Writes nothing but the snapshot.
    :class:`Unbounded`, :class:`Unowned` or :class:`OSError` refuse before anything is written.
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
    if scope is None:
        found = _starts(root, family, since, say, board)
        if found is None:
            return None
        starts = found
    version = store_version()
    settings = plan_store.configured_settings()
    staged = _staged(root)
    try:
        queried_at = now()
        snapshot_settings_now = snapshot_settings(staged, board)
        held: dict[str, str] = {}
        failed: dict[str, str] = {}
        if scope is None:
            for read, start in starts.items():
                copied = copy_into_snapshot(
                    staged,
                    read,
                    [
                        "--source",
                        read,
                        "--commented-since",
                        instant(start.since),
                        "--metadata",
                        host_query(host),
                    ],
                    snapshot_settings_now,
                )
                held |= copied.held
                failed |= copied.failed
                say(f"read {read!r} for comments since {instant(start.since)} ({start.origin})")
        else:
            bound = (
                [Binding(scope, "", issue)]
                if issue is not None
                else run_bindings(root, board, scope)
            )
            ids = []
            for binding in bound:
                if binding.item is None:
                    say(
                        f"run {scope}'s ticket {binding.ticket} is bound to no board item; "
                        "nothing read"
                    )
                    continue
                ids.append(str(binding.item))
            if ids:
                copied = copy_into_snapshot(staged, board, ids, snapshot_settings_now)
                held, failed = copied.held, copied.failed
            for one in ids:
                say(
                    f"read {one} and its comments directly, the item run {scope}'s ticket is "
                    "bound to"
                )
        details = shown_details(list(held.values()), snapshot_settings_now)
        issues = [
            snapshot_issue(detail, source, destination, _plugin(settings, source.partition(":")[0]))
            for (source, destination), detail in zip(held.items(), details, strict=True)
        ]
        if issue_run is not None:
            problem = (
                _unowned(issues[0], issue_run, host)
                if issues
                else "the copy could not capture it: "
                + failed.get(str(issue), "it answered nothing")
            )
            if problem is not None:
                raise Unowned(f"--issue {issue}: no run of this host owns it, because {problem}")
        recorded = {run for run in appearing_runs(issues) if has_records(root, run)}
        boundaries: dict[tickets.RunId, RunBoundary] = {}
        moments: dict[tickets.RunId, Boundary] = {}
        for run in sorted(recorded):
            boundary, computed = run_boundary(root, run, issues)
            moments[run] = boundary
            boundaries[run] = RunBoundary(
                None if boundary.moment is None else instant(boundary.moment), computed
            )
        selection = select(issues, moments, host, recorded, quoted_versions(root))
        launching = {
            run: chosen for run, chosen in selection.chosen.items() if scope in (None, run)
        }
        snapshot = publish_snapshot(
            staged,
            Snapshot(
                directory=staged,
                queried_at=queried_at,
                board=board,
                boards=family,
                named_board=named_board,
                scope=scope,
                issue=issue,
                starts=starts,
                store=version,
                dry_run=dry_run,
                items=tuple(
                    SnapshotItem(
                        one.id,
                        QualifiedTaskId(str(one.snapshot_id)),
                        one.version,
                        one.title,
                        one.owner,
                        one.host,
                        one.url,
                    )
                    for one in issues
                ),
                boundaries=boundaries,
                selection={
                    run: tuple(
                        Chosen(
                            one.issue.id,
                            one.id,
                            instant(one.last_changed),
                            body_sha256(one.text),
                        )
                        for one in chosen
                    )
                    for run, chosen in launching.items()
                },
                left=tuple(
                    [LeftOut(one.issue, one.id, one.reason) for one in selection.left]
                    + [
                        LeftOut(QualifiedTaskId(source), None, NOT_CAPTURED.format(error=error))
                        for source, error in sorted(failed.items())
                    ]
                ),
            ),
        )
    except BaseException:
        _discard(staged)
        raise
    # A comment with no address of its own is addressed by its copy's file, which the rename
    # just moved: the report names it where it now is, as every later reader does.
    report_selection(snapshot, _moved(selection, staged, snapshot.directory), failed, say)
    if dry_run:
        say(f"launch exactly this with: {from_command(snapshot.directory)}")
    return snapshot


def _moved(selection: Selection, staged: Path, published: Path) -> Selection:
    """``selection`` with every URL into ``staged`` pointing into ``published`` instead."""
    old, new = staged.absolute().as_uri(), published.absolute().as_uri()

    def moved(url: str | None) -> str | None:
        return None if url is None else url.replace(old, new, 1)

    return Selection(
        chosen={
            run: [dataclasses.replace(one, url=str(moved(one.url))) for one in chosen]
            for run, chosen in selection.chosen.items()
        },
        left=[dataclasses.replace(one, url=moved(one.url)) for one in selection.left],
    )


def _discard(staged: Path) -> None:
    """Remove a staging directory a gathering refused part way through."""
    for path in sorted(staged.rglob("*"), reverse=True):
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()
    staged.rmdir()


def report_selection(
    snapshot: Snapshot, selection: Selection, failed: Mapping[str, str], say: Say
) -> None:
    """Say where each selected comment goes, why the rest are left out, and what was read."""
    verb = "would go to" if snapshot.dry_run else "goes to"
    for run, chosen in selection.chosen.items():
        if snapshot.scope in (None, run):
            for one in chosen:
                say(f"selected: {one.url} on {one.issue.id} {verb} run {run}")
    owners = {one.id: one.owner for one in snapshot.items}
    for left in selection.left:
        if snapshot.scope in (None, owners.get(left.issue)):
            say(f"left out: {left.url} on {left.issue}: {left.reason}")
    for source, error in sorted(failed.items()):
        say(_not_captured(LeftOut(QualifiedTaskId(source), None, NOT_CAPTURED.format(error=error))))
    if not any(snapshot.selection.values()):
        say("nothing to answer: no comment was selected")
    say(f"snapshot: {snapshot.directory}")
    say(_store_line(snapshot))


def _not_captured(left: LeftOut) -> str:
    """The report's line for an item the snapshot's copy could not capture."""
    return f"not captured: {left.issue}: {left.reason.removeprefix('not captured: ')}"


def _store_line(snapshot: Snapshot) -> str:
    if snapshot.scope is not None:
        return (
            f"plan store: {snapshot.store}; read {len(snapshot.items)} item(s) run "
            f"{snapshot.scope}'s tickets are bound to, each directly, and listed none"
        )
    since = min((one.since for one in snapshot.starts.values()), default=snapshot.queried_at)
    return (
        f"plan store: {snapshot.store}; the narrowed query returned {len(snapshot.items)} "
        f"item(s) commented on since {instant(since)}"
    )


class Launch(NamedTuple):
    """One run a snapshot launches, and the feedback file it launches it over."""

    run: tickets.RunId
    feedback: Path


@dataclass(frozen=True)
class Plan:
    """What :func:`claim` hands the script and :func:`settle`: the snapshot, and each launch."""

    snapshot: Path
    detach: bool
    runs: tuple[Launch, ...]


PLAN_FIELDS: dict[str, type] = {"snapshot": str, "detach": bool, "runs": list}


def _plan_document(plan: Plan) -> dict[str, object]:
    return {
        "snapshot": str(plan.snapshot),
        "detach": plan.detach,
        "runs": [[run, str(path)] for run, path in plan.runs],
    }


def _launch(value: object, root: Path) -> bool:
    """Whether ``value`` is a run id and one of that run's feedback files under ``root``."""
    if not (
        isinstance(value, list) and len(value) == 2 and all(isinstance(one, str) for one in value)
    ):  # noqa: PLR2004 - a pair is two
        return False
    run, feedback = value[0], Path(value[1])
    return bool(
        LAUNCHABLE_RUN.fullmatch(run)
        and not {"\t", "\n"} & set(value[1])
        and FEEDBACK_NAME.fullmatch(feedback.name)
        and feedback.parent == feedback_directory(root) / run
    )


def read_plan(path: Path, root: Path) -> Plan:
    """The plan :func:`claim` wrote under ``root``, which this process's own script hands back.

    :class:`OSError` naming the file for anything else, since a plan this cannot read would
    launch or settle runs nobody gathered for.
    """
    # The recipe hands its steps a plan from a directory it made for that one invocation, so
    # no journey can put another file there; `tests/test_follow_up_comments.py` hands the
    # steps every malformed shape in process instead.
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    try:
        held: object = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as broken:
        raise OSError(f"{path} is not a plan `launch` wrote: {broken}") from None
    if (
        not isinstance(held, dict)
        or sorted(held) != sorted(PLAN_FIELDS)
        or not all(isinstance(held[key], kind) for key, kind in PLAN_FIELDS.items())
        or Path(held["snapshot"]).parent != snapshots_directory(root)
        or not all(_launch(one, root) for one in held["runs"])
    ):
        raise OSError(
            f"{path} is not a plan `launch` wrote: it carries none of, or other than, "
            f"{', '.join(PLAN_FIELDS)} with their types"
        )
    return Plan(
        snapshot=Path(held["snapshot"]),
        detach=held["detach"],
        runs=tuple(Launch(tickets.RunId(run), Path(file)) for run, file in held["runs"]),
    )


def _overlaps(one: Snapshot, other: Snapshot) -> bool:
    return one.scope is None or other.scope is None or one.scope == other.scope


def _taken(snapshot: Snapshot) -> tuple[datetime, int]:
    """When ``snapshot`` was taken, ordered as its directory is named: instant, then counter."""
    named = SNAPSHOT_NAME.fullmatch(snapshot.directory.name)
    return snapshot.queried_at, int(named["attempt"] or 1) if named else 1


def launched_record(directory: Path) -> Mapping[str, object] | None:
    """The claim a launch wrote on the snapshot at ``directory``, if one did."""
    path = directory / SNAPSHOT_LAUNCHED
    try:
        held: object = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {"claimed_at": "(unreadable)"}
    return held if isinstance(held, Mapping) else {"claimed_at": "(unreadable)"}


def launchable_problem(snapshot: Snapshot, root: Path, now: datetime) -> str | None:
    """Why ``snapshot`` may not be launched with `--from` now, or ``None`` when it may."""
    claimed = launched_record(snapshot.directory)
    if claimed is not None:
        return f"it was already launched at {claimed.get('claimed_at')}"
    if now - snapshot.queried_at > SNAPSHOT_LAUNCHABLE_FOR:
        return (
            f"it was gathered at {instant(snapshot.queried_at)}, more than "
            f"{SNAPSHOT_LAUNCHABLE_FOR} ago"
        )
    for other_directory in sorted(snapshots_directory(root).iterdir()):
        if other_directory == snapshot.directory or not SNAPSHOT_NAME.fullmatch(
            other_directory.name
        ):
            continue
        if launched_record(other_directory) is None:
            continue
        try:
            other = read_snapshot(other_directory)
        except OSError:
            continue
        if (
            other.board == snapshot.board
            and _taken(other) > _taken(snapshot)
            and _overlaps(snapshot, other)
        ):
            return (
                f"the later snapshot {other_directory}, gathered at {instant(other.queried_at)} "
                "over the same comments, was launched since"
            )
    return None


def claim(  # noqa: PLR0913 - each is one flag of the command
    root: Path,
    directory: Path,
    *,
    fresh: bool,
    detach: bool,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    say: Say = _say,
) -> Plan:
    """Claim the snapshot at ``directory`` and write what its launches answer from it.

    `launched.json` is written exclusively first, then each recorded boundary this snapshot
    computed for a launched run, then the feedback files, from the snapshot alone: no board
    request. A `--from` launch (``fresh`` false) is refused by :class:`Unlaunchable`, with
    nothing written, for every reason the module names.
    """
    if directory.resolve().parent != snapshots_directory(root).resolve():
        raise Unlaunchable(
            f"{directory} is no snapshot under {snapshots_directory(root)}, where this drafts "
            f"root keeps its own; gather a fresh one with `just {PROG} --dry-run`"
        )
    try:
        snapshot = read_snapshot(directory)
    except OSError as exc:
        raise Unlaunchable(
            f"the snapshot is malformed: {exc}; gather a fresh one with `just {PROG} --dry-run`"
        ) from None
    if not fresh:
        problem = launchable_problem(snapshot, root, now())
        if problem is not None:
            raise Unlaunchable(
                f"{directory} may not be launched: {problem}; gather a fresh one with "
                f"`{dry_run_command(snapshot)}`"
            )
    issues: dict[str, Issue] = {str(one.id): one for one in snapshot_issues(snapshot)}
    # Every feedback file is composed, each selected comment held to the version its record
    # names, before anything is written: a snapshot its own source disagrees with is refused
    # with nothing claimed.
    composed = []
    for run, chosen in snapshot.selection.items():
        recorded = snapshot.boundaries.get(run, RunBoundary(None, False))
        since = None if recorded.boundary is None else moment(recorded.boundary, str(directory))
        selected = [_selected(issues[one.issue], one) for one in chosen]
        feedback = feedback_directory(root) / run / f"{directory.name}.md"
        composed.append((run, feedback, render(run, snapshot.board, selected, since, directory)))
    for left in snapshot.left:
        if left.comment is None:
            say(_not_captured(left))
    try:
        with (directory / SNAPSHOT_LAUNCHED).open("x", encoding="utf-8") as handle:
            json.dump(
                {
                    "schema": LAUNCHED_SCHEMA,
                    "claimed_at": instant(now()),
                    "launch": "fresh" if fresh else "from",
                },
                handle,
            )
    except FileExistsError:
        raise Unlaunchable(
            f"{directory} may not be launched: another launch claimed it first; gather a fresh "
            f"one with `{dry_run_command(snapshot)}`"
        ) from None
    # Every boundary this snapshot computed for a run it is about is recorded, whether or not
    # a comment of that run was selected: a later gathering reads it rather than recomputing it
    # from responses a re-dispatch has since moved.
    for run, recorded in snapshot.boundaries.items():
        if (
            recorded.computed
            and snapshot.scope in (None, run)
            and stored_boundary(root, run) is None
        ):
            path = boundary_path(root, run)
            path.parent.mkdir(parents=True, exist_ok=True)
            held = None if recorded.boundary is None else moment(recorded.boundary, str(directory))
            path.write_text(boundary_line(held) + "\n", encoding="utf-8")
    runs = []
    for run, feedback, text in composed:
        write_feedback(feedback, text)
        say(f"wrote run {run}'s comments to {feedback}")
        runs.append(Launch(tickets.RunId(run), feedback))
    if not runs:
        say("nothing to answer: no comment was selected, so nothing was launched")
    return Plan(snapshot=directory, detach=detach, runs=tuple(runs))


def expectable_version(
    issue: str, version: str | None, settings: Mapping[str, object]
) -> str | None:
    """The version a feedback file names for ``issue``: ``None`` where its board keeps no cache.

    A write's `--expect` takes its ids and values from the store's cache, so a reply on a board
    whose source has none sends no `--expect`, which the version :data:`NO_VERSION` says.
    """
    source = issue.partition(":")[0]
    return version if settings.get(f"sources.{source}.cache.enabled") is True else None


def _selected(issue: Issue, chosen: Chosen) -> Selected:
    """The comment ``chosen`` names on ``issue``, held to the version the snapshot recorded."""
    comment = next((one for one in issue.comments if one.id == chosen.comment), None)
    if (
        comment is None
        or body_sha256(comment.body) != chosen.body_sha256
        or instant(comment.last_changed) != chosen.last_changed
    ):
        raise OSError(
            f"the snapshot's source holds comment {chosen.comment} on {issue.id} other than its "
            f"{SNAPSHOT_RECORD} recorded"
        )
    return Selected(
        issue=issue,
        id=comment.id,
        url=comment_url(issue, comment),
        author=comment.author or UNKNOWN_AUTHOR,
        last_changed=comment.last_changed,
        text=comment.body,
    )


def check_command(snapshot: Snapshot, run: str, feedback: Path) -> str:
    """The exact `check-responses` command that answers whether ``run`` answered ``feedback``."""
    return shlex.join(
        [
            sys.executable,
            "-m",
            "orchestrator.follow_up_tickets",
            "check-responses",
            "--board",
            snapshot.board,
            "--feedback",
            str(feedback),
            run,
        ]
    )


Write = Literal["ticket-copy", "priority", "withdrawal"]
State = Literal["withheld", "failed", "partial", "unknown"]
WRITES: tuple[Write, ...] = get_args(Write)
STATES: tuple[State, ...] = get_args(State)


class Projected(TypedDict):
    """Every field a settle write may set, as one item holds it: :func:`project`'s answer."""

    title: str
    content: str
    status: str
    priority: str
    record: dict[str, object]
    repositories: list[str]


class Intended(TypedDict, total=False):
    """The value of every field one settle write sets, each at its type in :class:`Projected`,
    which `tests/test_follow_up_comments.py` holds the two to."""

    title: str
    content: str
    status: str
    priority: str
    record: dict[str, object]
    repositories: list[str]


#: The fields a settle write may set, and the type each holds its value at.
FIELD_TYPES: dict[str, type] = {
    name: get_origin(hint) or hint for name, hint in get_type_hints(Projected).items()
}
FIELDS = tuple(FIELD_TYPES)
#: The fields each write sets, which are all an entry of that write may intend: `task update`
#: sets no `repositories`, which is why a ticket whose list changed is copied whole.
WRITE_FIELDS: dict[Write, frozenset[str]] = {
    "ticket-copy": frozenset({"title", "content", "priority", "record", "repositories"}),
    "priority": frozenset({"priority", "content", "record"}),
    "withdrawal": frozenset({"status"}),
}


def as_intended(write: Write, fields: Mapping[str, object]) -> Intended:
    """``fields`` as what ``write`` intends; :class:`ValueError` for a field it does not set or a
    value of another type than :class:`Intended` declares."""
    for key, value in fields.items():
        if (
            key not in WRITE_FIELDS[write]
            or not isinstance(value, FIELD_TYPES[key])
            or (isinstance(value, list) and not all(isinstance(one, str) for one in value))
        ):
            raise ValueError(f"a {write} write sets no {key} of {value!r}")
    # Every key and the type of every value were checked against `Intended` just above.
    return cast(Intended, dict(fields))


class WritePart(NamedTuple):
    """One part of a write, as the write's own response reports it."""

    part: str
    outcome: str
    error: str | None


@dataclass(frozen=True)
class PendingEntry:
    """One settle write that did not certainly land, as `pending.json` records it."""

    snapshot: str
    run: tickets.RunId
    item: QualifiedTaskId
    write: Write
    state: State
    snapshot_version: str | None
    seen_version: str | None
    intended: Intended
    parts: tuple[WritePart, ...]
    error: str | None
    recorded_at: str


PENDING_ENTRY_KEYS = tuple(field.name for field in dataclasses.fields(PendingEntry))


def pending_path(root: Path) -> Path:
    """The host-wide record of settle writes that did not certainly land."""
    return feedback_directory(root) / PENDING


def _write_part(value: object) -> WritePart | None:
    if (
        not isinstance(value, Mapping)
        or sorted(value) != sorted(WritePart._fields)
        or not isinstance(value["part"], str)
        or not isinstance(value["outcome"], str)
        or not _optional_str(value["error"])
    ):
        return None
    return WritePart(value["part"], value["outcome"], value["error"])


def _pending_entry(value: object) -> PendingEntry | None:
    if not isinstance(value, Mapping) or sorted(value) != sorted(PENDING_ENTRY_KEYS):
        return None
    fields, parts = value["intended"], value["parts"]
    read_parts = [_write_part(one) for one in parts] if isinstance(parts, list) else [None]
    if (
        value["write"] not in WRITES
        or value["state"] not in STATES
        or not all(
            isinstance(value[key], str) and value[key] for key in ("snapshot", "run", "recorded_at")
        )
        or not _is_qualified(value["item"])
        or not _is_moment(value["recorded_at"])
        or not LAUNCHABLE_RUN.fullmatch(value["run"])
        or not all(
            _optional_str(value[key]) for key in ("snapshot_version", "seen_version", "error")
        )
        or not isinstance(fields, Mapping)
        or not fields
        or None in read_parts
    ):
        return None
    try:
        held = as_intended(value["write"], fields)
    except ValueError:
        return None
    return PendingEntry(
        **{**value, "parts": tuple(one for one in read_parts if one is not None), "intended": held}
    )


def read_pending(root: Path) -> list[PendingEntry]:
    """Every entry `pending.json` holds; :class:`OSError` for a file of any other shape."""
    path = pending_path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if path.is_symlink():
            raise OSError(
                f"{path} is a link to nothing; repair it rather than lose a write"
            ) from None
        return []
    except (OSError, UnicodeDecodeError) as broken:
        # A directory, or a file this cannot read, is no record that nothing is pending.
        raise OSError(
            f"{path} could not be read: {broken}; repair it rather than lose a write"
        ) from None
    try:
        held: object = json.loads(text)
    except json.JSONDecodeError as broken:
        raise OSError(f"{path} is not JSON: {broken}") from None
    entries = held.get("entries") if isinstance(held, Mapping) else None
    read = [_pending_entry(one) for one in entries] if isinstance(entries, list) else [None]
    if (
        not isinstance(held, Mapping)
        or held.get("schema") != PENDING_SCHEMA
        or sorted(held) != ["entries", "schema"]
        or None in read
    ):
        raise OSError(
            f"{path} is not a schema-{PENDING_SCHEMA} record of pending writes with entries of "
            f"the keys {', '.join(PENDING_ENTRY_KEYS)}; repair it rather than lose a write"
        )
    return [one for one in read if one is not None]


def write_pending(root: Path, entries: Sequence[PendingEntry]) -> None:
    """Replace `pending.json` with ``entries``, removing it when none remain."""
    path = pending_path(root)
    if not entries:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + ".partial")
    document = {
        "schema": PENDING_SCHEMA,
        "entries": [
            {
                **dataclasses.asdict(one),
                "parts": [part._asdict() for part in one.parts],
                "intended": dict(one.intended),
            }
            for one in entries
        ],
    }
    staged.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    staged.replace(path)


def project(item: Mapping[str, object]) -> Projected:
    """The fields a settle write may set, as ``item`` holds them; :class:`OSError` for one
    answered at another type, which settle would otherwise compare and write as a change."""
    title, content = item.get("title"), item.get("content")
    priority, repositories = item.get(tickets.PRIORITY_FIELD), item.get("repositories")
    if (
        not isinstance(title, str)
        or not _optional_str(content)
        or not _optional_str(priority)
        or not (
            repositories is None
            or (
                isinstance(repositories, list) and all(isinstance(one, str) for one in repositories)
            )
        )
    ):
        raise OSError(f"the item {item.get('id')!r} answered a field settle writes at another type")
    return Projected(
        title=title,
        content=(content or "").rstrip(),
        status=_status(item)[0],
        priority=priority or tickets.Priority.NONE.value,
        record=dict(_record(item)),
        repositories=sorted(repositories or []),
    )


class Outcome(NamedTuple):
    """What one write's own response says: its state (``None`` when it landed), and the rest."""

    state: State | None
    version: str | None
    parts: tuple[WritePart, ...]
    error: str | None
    fallback: str | None


def _response_part(value: object) -> WritePart | None:
    """One part a write's response reports, or ``None`` for one it does not describe."""
    if not isinstance(value, Mapping):
        return None
    part, said, error = value.get("part"), value.get("outcome"), value.get("error")
    if isinstance(error, Mapping):
        error = error.get("message")
    if not isinstance(part, str) or not isinstance(said, str) or not _optional_str(error):
        return None
    return WritePart(part, said, error or None)


#: The part of an `--expect` write each field a settle write sets rides on, in the words the
#: store's protocol documents (`fields`, then `issue`): the board's project fields carry the
#: priority and the status, and the issue its title and its body, the record's metadata block
#: among it. `repositories` names no part, since only a whole copy sets it.
FIELD_PARTS: dict[str, str] = {
    "title": "issue",
    "content": "issue",
    "record": "issue",
    "priority": "fields",
    "status": "fields",
}


#: What a plain `task copy` answers for a destination that now holds the whole ticket.
COPIED = frozenset({"updated", "unchanged"})


#: The fields a plain `task status set` or `task priority set` answers with, at its top level.
SET_ALONE = frozenset({"status", "priority"})


def _set_alone(answered: Mapping[str, object]) -> dict[str, object]:
    """The fields a single-field write answered with, as :func:`project` reads them."""
    shown: dict[str, object] = {}
    if "status" in answered:
        shown["status"] = _status(answered)[0]
    if "priority" in answered:
        shown["priority"] = answered["priority"]
    return shown


def outcome(
    answer: StoreAnswer, item: str, requested: Mapping[str, object] | None = None
) -> Outcome:
    """Read one write's response: landed, `failed`, `partial` or `unknown`.

    Every part `landed`, covering the part each of the ``requested`` fields rides on
    (:data:`FIELD_PARTS`), is a write that landed — and a write sent with no `--expect`, which
    answers with the task it wrote, landed when that task holds every ``requested`` value; a
    part `unknown`, a part the response does not describe, a success naming no part, or no
    response this can read is `unknown`; some parts landed beside one rejected, or beside a
    requested part the response does not report, is `partial`, as is such a task holding only
    some of the values; anything else `failed`.
    """
    document = answer.document
    if document is None:
        return Outcome("unknown", None, (), answer.stderr or "the store printed no response", None)
    entries = document.get("items")
    held = entries[0] if isinstance(entries, list) and len(entries) == 1 else document
    held = held if isinstance(held, Mapping) else {}
    # The item's own result where its entry carries one, of whatever shape, else the document's.
    result = held["result"] if "result" in held else document.get("result")
    fallback = held.get("fallback") or document.get("fallback")
    reason = fallback.get("reason") if isinstance(fallback, Mapping) else None
    parts_value = result.get("parts") if isinstance(result, Mapping) else None
    read = [_response_part(one) for one in parts_value] if isinstance(parts_value, list) else []
    version = result.get("version") if isinstance(result, Mapping) else None
    version = version if isinstance(version, str) else None
    if result is not None and not (
        isinstance(result, Mapping) and isinstance(parts_value, list) and parts_value
    ):
        # An `--expect` write answers with a result listing the parts it wrote; a result listing
        # none, or of any other shape, is no plain answer either, so nothing says what it wrote.
        return Outcome("unknown", version, (), f"the result was not described: {result!r}", reason)
    if None in read:
        return Outcome("unknown", version, (), f"a part was not described: {parts_value!r}", reason)
    parts = tuple(one for one in read if one is not None)
    outcomes = [one.outcome for one in parts]
    errors = [one.error for one in parts if one.error]
    error = "; ".join(errors) or (None if answer.status in (0, 4) else _failure_message(answer))
    if "unknown" in outcomes:
        return Outcome("unknown", version, parts, error or "a part's answer was lost", reason)
    succeeded = answer.status in (0, 4) and held.get("action") != "failed"
    named = held.get("id") or held.get("destination")
    if succeeded and (named != item if not parts else named is not None and named != item):
        # A write sent with no `--expect` answers with the task it wrote rather than in parts,
        # so one naming no task says nothing about what it wrote; and an answer naming another
        # task than ``item``, in parts or not, is about another write.
        return Outcome(
            "unknown", version, parts, f"the response named {named!r}, not {item}", reason
        )
    # A response in parts reports each mutation sent, so a requested part it does not report is
    # one nothing says landed, however every reported part came out.
    requested = requested or {}
    missing = sorted(
        {FIELD_PARTS[key] for key in requested if key in FIELD_PARTS}
        - {one.part for one in parts if one.outcome == "landed"}
    )
    if parts and missing:
        error = "; ".join([*errors, f"the response reported no landed {', '.join(missing)} part"])
    if succeeded and not parts and requested:
        # A plain write is verified from what its answer shows: an update answers with the
        # task it wrote, and a status or priority write with the field it set, each held to
        # every requested value; a copy names its destination `updated` or `unchanged`, the
        # store's word that the item now holds the whole ticket.
        written = held.get("task")
        alone = {key: held[key] for key in requested if key in SET_ALONE and key in held}
        if isinstance(written, Mapping) or alone:
            try:
                shown: Mapping[str, object] = (
                    project(written) if isinstance(written, Mapping) else _set_alone(alone)
                )
            except OSError as exc:
                return Outcome("unknown", version, parts, str(exc), reason)
            unshown = [key for key, value in requested.items() if shown.get(key) != value]
            if unshown:
                said = held.get("written")
                state: State = "partial" if len(unshown) < len(requested) or said else "failed"
                error = f"the task it answered does not hold the requested {', '.join(unshown)}"
                return Outcome(state, version, parts, error, reason)
        elif held.get("destination") != item or held.get("action") not in COPIED:
            return Outcome(
                "unknown",
                version,
                parts,
                "the response showed neither the task nor its parts",
                reason,
            )
    if succeeded and all(one == "landed" for one in outcomes) and not (parts and missing):
        return Outcome(None, version, parts, None, str(reason) if reason else None)
    if "landed" in outcomes:
        return Outcome("partial", version, parts, error, str(reason) if reason else None)
    return Outcome(
        "failed", version, parts, error or _failure_message(answer), str(reason) if reason else None
    )


def refused_copy_read(arguments: Sequence[str], result: Outcome) -> str | None:
    """The fallback read the store makes after a refused whole copy, which its answer omits.

    The `repositories` exception's `task copy`, refused at a part, is followed by the store's
    one read of the item, reported neither as the answer's `fallback` nor in what it spent.
    ``None`` for any other write, and for an answer that reports its own fallback.
    """
    if list(arguments[:2]) != ["task", "copy"] or result.fallback is not None:
        return None
    if not any(part.outcome == "rejected" for part in result.parts):
        return None
    return "the store read the item once after the board refused its copy"


@dataclass
class Pass:
    """What settle has read and done, and what it reports."""

    root: Path
    board: str
    settings: Mapping[str, object]
    read: dict[str, Mapping[str, object]]
    comments: dict[str, list[Mapping[str, object]]]
    versions: dict[str, str | None]
    pending: list[PendingEntry]
    say: Say
    now: Callable[[], datetime]
    #: Every quoted issue as the pass's own snapshot holds it.
    held: dict[QualifiedTaskId, Held] = dataclasses.field(default_factory=dict)
    fallbacks: int = 0
    writes: int = 0

    def expect(self, item: str) -> list[str]:
        """The `--expect` words for the next write of ``item``, or none where it has no version."""
        version = self.versions.get(item)
        source = item.partition(":")[0]
        if version is None or self.settings.get(f"sources.{source}.cache.enabled") is not True:
            self.say(
                f"  {item}: the board gives it no version to expect, so the store reads it "
                "before writing"
            )
            return []
        return ["--expect", version]

    def send(self, item: str, arguments: Sequence[str], requested: Mapping[str, object]) -> Outcome:
        """Send one write of ``item`` setting the ``requested`` values, and read its response."""
        answer = store_command([*arguments, *self.expect(item)])
        self.writes += 1
        result = outcome(answer, item, requested)
        fallback = result.fallback or refused_copy_read(arguments, result)
        if fallback is not None:
            self.fallbacks += 1
            self.say(f"  fallback read: {item}: {fallback}")
        if result.version is not None:
            self.versions[item] = result.version
        return result


def _entry(  # noqa: PLR0913 - every field of one record
    snapshot: Snapshot,
    run: tickets.RunId,
    item: QualifiedTaskId,
    write: Write,
    state: State,
    seen_version: str | None,
    intended: Mapping[str, object],
    result: Outcome | None,
    now: datetime,
) -> PendingEntry:
    held = snapshot.item(item)
    return PendingEntry(
        snapshot=str(snapshot.directory),
        run=run,
        item=item,
        write=write,
        state=state,
        snapshot_version=None if held is None else held.version,
        seen_version=seen_version,
        intended=as_intended(write, intended),
        parts=() if result is None else result.parts,
        error=None if result is None else result.error,
        recorded_at=instant(now),
    )


def _update_words(item: str, fields: Mapping[str, object], scratch: Path) -> list[str]:
    """`task update` writing exactly ``fields`` of ``item``."""
    words = ["task", "update", item]
    if "title" in fields:
        words += ["--title", str(fields["title"])]
    if "content" in fields:
        body = scratch / f"content-{len(list(scratch.iterdir()))}.md"
        body.write_text(str(fields["content"]) + "\n", encoding="utf-8")
        words += ["--body-file", str(body)]
    if "record" in fields:
        words += ["--metadata", f"{tickets.KEY}={json.dumps(fields['record'])}"]
    if "priority" in fields:
        words += ["--priority", str(fields["priority"])]
    if "status" in fields:
        words += ["--status", str(fields["status"])]
    return words


def _copy_words(run: str, issue: str, item: Mapping[str, object]) -> list[str]:
    """`task copy` of ``run``'s ticket of ``issue`` onto the item the store links it to.

    The one write that sets an item's `repositories`, which `task update` cannot; ``item`` is
    the read of ``issue`` naming the ticket's root cause.
    """
    cause = _record(item).get("root_cause")
    if (
        not isinstance(cause, str)
        or not tickets.SLUG.fullmatch(cause)
        or (tickets.issue_owner(item) != run)
    ):
        raise OSError(f"{issue} carries no root cause of a ticket of run {run}'s to copy")
    return ["task", "copy", tickets.qualified_id(run, cause), "--to", issue.partition(":")[0]]


def _snapshot_projection(entry: PendingEntry) -> Projected | None:
    """The fields of ``entry``'s item as its snapshot held them, if that snapshot is still here."""
    try:
        snapshot = read_snapshot(Path(entry.snapshot))
        held = snapshot.item(entry.item)
        if held is None:
            return None
        (detail,) = shown_details([held.snapshot_id], snapshot.settings())
        return project(detail_item(detail, held.snapshot_id))
    except OSError:
        return None


def recover(state: Pass, entry: PendingEntry, scratch: Path) -> PendingEntry | None:
    """Apply the recovery rule to one carried entry; the entry kept, or ``None`` once cleared."""
    item = state.read.get(entry.item)
    if item is None:
        state.say(f"  pending {entry.write} of {entry.item}: the board did not answer for it; kept")
        return entry
    now = project(item)
    unapplied = {key: value for key, value in entry.intended.items() if now.get(key) != value}
    if not unapplied:
        state.say(f"  pending {entry.write} of {entry.item}: the board holds it; cleared")
        return None
    since = entry.snapshot_version if entry.state == "withheld" else entry.seen_version
    if since is None or state.versions.get(entry.item) != since:
        baseline = _snapshot_projection(entry)
        changed = [
            key for key in unapplied if baseline is None or now.get(key) != baseline.get(key)
        ]
        if changed:
            state.say(
                f"  pending {entry.write} of {entry.item}: conflict: a person changed "
                f"{', '.join(changed)} since, so nothing was written; kept"
            )
            return entry
    try:
        arguments = (
            _copy_words(entry.run, entry.item, item)
            if "repositories" in unapplied
            else _update_words(entry.item, unapplied, scratch)
        )
    except OSError as exc:
        state.say(f"  pending {entry.write} of {entry.item}: {exc}, so nothing was written; kept")
        return entry
    result = state.send(entry.item, arguments, unapplied)
    if result.state is None:
        state.say(
            f"  pending {entry.write} of {entry.item}: retried {', '.join(unapplied)}; landed"
        )
        return None
    state.say(f"  pending {entry.write} of {entry.item}: retry {result.state}: {result.error}")
    return dataclasses.replace(
        entry,
        state=result.state,
        intended=as_intended(entry.write, unapplied),
        seen_version=state.versions.get(entry.item),
        parts=result.parts,
        error=result.error,
        recorded_at=instant(state.now()),
    )


class Verified(NamedTuple):
    """One run's replies as the batched read holds them: its problems, and each reply's body."""

    problems: list[str]
    bodies: dict[str, list[str]]
    urls: list[str]


def verify_replies(
    run: str,
    held_before: Mapping[QualifiedTaskId, Held],
    answered: Sequence[tickets.Response],
    state: Pass,
) -> Verified:
    """Whether the batched read holds each reply ``answered`` names, as its account says.

    Each reply is on its issue, is run ``run``'s reply to that comment carrying the verdict its
    response states, and is the one reply of ``run`` to that comment since the snapshot.
    """
    problems: list[str] = []
    bodies: dict[str, list[str]] = {}
    urls: list[str] = []
    for one in answered:
        issue = one.comment.issue
        held = {str(comment.get("id")): comment for comment in state.comments.get(issue, ())}
        before = (
            {identifier for identifier, _ in held_before[issue].comments}
            if issue in held_before
            else set()
        )
        since = [
            identifier
            for identifier, comment in held.items()
            if identifier not in before
            and (owner := tickets.comment_owner(str(comment.get("body")))) is not None
            and owner.run == run
            and owner.answers == one.comment.comment
        ]
        reply = held.get(one.reply)
        owner = tickets.comment_owner(str(reply.get("body"))) if reply is not None else None
        if (
            reply is None
            or owner is None
            or owner.run != run
            or owner.answers != one.comment.comment
        ):
            problems.append(
                f"the board holds no reply {one.reply} of run {run} answering comment "
                f"{one.comment.comment} on {issue}"
            )
            continue
        if len(since) != 1:
            problems.append(
                f"the board holds {len(since)} replies of run {run} answering comment "
                f"{one.comment.comment} on {issue} since the snapshot, and it owes exactly one"
            )
            continue
        if one.reply not in since:
            problems.append(
                f"the response to comment {one.comment.comment!r} names the reply {one.reply}, "
                f"which predates the snapshot, and the board holds run {run}'s reply to it since "
                f"as {since[0]}"
            )
            continue
        if owner.verdict is not one.verdict:
            problems.append(
                f"the response to comment {one.comment.comment!r} states the verdict "
                f"`{one.verdict}`, and its reply {one.reply}'s marker states "
                + ("none" if owner.verdict is None else f"`{owner.verdict}`")
            )
            continue
        item = state.read.get(issue, {})
        cause = _record(item).get("root_cause")
        if one.verdict is tickets.Verdict.CONFIRMS and owner.root_cause != cause:
            problems.append(
                f"the response to comment {one.comment.comment!r} confirms, and its reply "
                f"{one.reply}'s marker names the root cause `{owner.root_cause}`, where {issue}'s "
                f"record describes {cause!r}; it confirms nothing there"
            )
            continue
        bodies.setdefault(issue, []).append(str(reply.get("body")))
        location = item.get("location")
        path = location.get("path") if isinstance(location, Mapping) else None
        urls.append(
            comment_url_parts(
                _web_url(item.get("url")),
                path if isinstance(path, str) else None,
                one.reply,
                _web_url(reply.get("url")) or one.url,
                issue,
            )
        )
    return Verified(problems, bodies, urls)


class Held(NamedTuple):
    """One item as the snapshot holds it: its fields, and each comment's board id and body."""

    fields: Projected
    comments: tuple[tuple[str, str], ...]


def _origin_id(comment: Mapping[str, object]) -> str:
    """The board id of a comment the snapshot holds: its origin's, which the copy recorded."""
    origin = comment.get("origin")
    identifier = origin.get("id") if isinstance(origin, Mapping) else None
    return identifier if isinstance(identifier, str) and identifier else str(comment["id"])


def snapshot_held(
    snapshot: Snapshot, issues: Collection[QualifiedTaskId]
) -> dict[QualifiedTaskId, Held]:
    """Each of ``issues`` the snapshot holds, read back through its source in one local read."""
    named = [one for one in snapshot.items if one.id in issues]
    details = shown_details([one.snapshot_id for one in named], snapshot.settings())
    held = {}
    for one, detail in zip(named, details, strict=True):
        comments = tuple(
            (_origin_id(comment), str(comment["body"]))
            for comment in detail_comments(detail, one.snapshot_id)
        )
        held[one.id] = Held(project(detail_item(detail, one.snapshot_id)), comments)
    return held


def settle_item(  # noqa: PLR0913 - the pass, and the one issue it writes
    state: Pass,
    snapshot: Snapshot,
    run: tickets.RunId,
    issue: QualifiedTaskId,
    replies: Sequence[str],
    scratch: Path,
) -> None:
    """Make this pass's writes to one quoted issue, straight after the batched read.

    The ticket of ``issue`` that ``run`` owns is decided from the read — the board's status
    kept, its estimate recounted off the snapshot's comments and ``replies``, its priority
    following the estimate only where the board still holds the stored one — and copied with
    only its changed fields (whole, where its `repositories` changed), or withheld where a
    person edited the item since the snapshot; a
    withdrawal its file asks for is a status write while the read shows the item at
    `Proposal`. With no ticket of ``run``'s here, a confirming reply's re-estimate is written
    onto the item alone: its priority, then its estimate line and record.
    """
    # A quoted issue the snapshot holds whose replies the read verified: both are there.
    item, held = state.read[issue], state.held[issue]
    bodies = [body for _, body in held.comments] + list(replies)
    reads = tickets.BoardReads(items={issue: item}, bodies={issue: tuple(bodies)})
    cause = _record(item).get("root_cause")
    path = (
        tickets.ticket_path(state.root, run, str(cause))
        if tickets.SLUG.fullmatch(str(cause))
        else None
    )
    if tickets.issue_owner(item) == run and path is not None and path.is_file():
        _settle_ticket(state, snapshot, run, issue, item, held, path, reads, scratch)
    else:
        # Every comment quoted on it carries a verified reply, which is the occurrence count
        # this re-estimate is owed.
        _re_estimate(state, snapshot, run, issue, item, reads, scratch)


def _settle_ticket(  # noqa: PLR0913 - the pass, the issue, and what decides its ticket
    state: Pass,
    snapshot: Snapshot,
    run: tickets.RunId,
    issue: QualifiedTaskId,
    item: Mapping[str, object],
    held: Held,
    path: Path,
    reads: tickets.BoardReads,
    scratch: Path,
) -> None:
    """Write ``run``'s ticket of ``issue`` — its changed fields, or whole — then any withdrawal."""
    now = project(item)
    local = tickets.read_ticket(path, pending=True)
    withdrawing = local.status is tickets.Status.WITHDRAWN
    category = str(now["status"])
    board_status = tickets.Status(category) if category in tuple(tickets.Status) else local.status
    native = issue.partition(":")[2]
    placement = tickets.Placement(tickets.BoardItemId(native), category, item)
    # The copy keeps the board's status, so a person's move stands; the withdrawal is a
    # write of its own, below, decided on the status the read shows.
    decided = tickets.estimate_before_copy(
        path,
        issue.partition(":")[0],
        placement,
        dataclasses.replace(local, status=board_status),
        reads,
    )
    wanted = project(
        {
            "title": decided.title,
            "content": decided.body,
            "status": {"category": board_status.value},
            "priority": decided.priority.value,
            "metadata": {tickets.KEY: tickets.record(decided)},
            "repositories": list(decided.repositories),
        }
    )
    # The ticket's binding to its item is the ticket's own: the item a first copy created holds
    # none until a later copy, which is no reason to write it.
    changes = {
        key: wanted[key]
        for key in ("title", "content", "priority", "record", "repositories")
        if _comparable(key, wanted[key]) != _comparable(key, now[key])
    }
    # Written as the item's changed fields alone rather than as a `task copy`, which undoes
    # every part when one is refused: a part that landed stays landed, and a retry sends only
    # what did not. `task update` sets no `repositories`, so a ticket whose list differs from the
    # item's — one brought forward from before the field — is copied whole instead.
    write: Write = "ticket-copy"
    edited = [key for key in ("title", "content", "record") if held.fields.get(key) != now[key]]
    if changes and edited:
        state.pending.append(
            _entry(
                snapshot,
                run,
                issue,
                write,
                "withheld",
                state.versions.get(issue),
                changes,
                None,
                state.now(),
            )
        )
        state.say(
            f"  {issue}: {write} withheld: the board's {', '.join(edited)} changed since the "
            "snapshot"
        )
    elif "repositories" in changes:
        # The copy writes the ticket's file as it stands, so the file is the decided ticket.
        path.write_text(tickets.render(decided), encoding="utf-8")
        _write_once(state, snapshot, run, issue, write, changes, _copy_words(run, issue, item))
    elif changes:
        arguments = _update_words(issue, changes, scratch)
        _write_once(state, snapshot, run, issue, write, changes, arguments)
    if not withdrawing:
        return
    if category != tickets.Status.PROPOSED.value:
        state.say(f"  {issue}: not withdrawn: the board holds it at {_status(item)[1]}")
        return
    withdrawal = {"status": tickets.Status.WITHDRAWN.value}
    arguments = ["task", "status", "set", issue, tickets.Status.WITHDRAWN.value]
    if _write_once(state, snapshot, run, issue, "withdrawal", withdrawal, arguments):
        withdrawn = dataclasses.replace(decided, status=tickets.Status.WITHDRAWN)
        path.write_text(tickets.render(withdrawn), encoding="utf-8")


def _comparable(key: str, value: object) -> object:
    """``value`` of field ``key`` as a change to it is judged: a record less its binding."""
    if key == "record" and isinstance(value, Mapping):
        return {name: held for name, held in value.items() if name != tickets.BINDING_FIELD}
    return value


def _re_estimate(  # noqa: PLR0913 - the pass, the issue, and what decides its estimate
    state: Pass,
    snapshot: Snapshot,
    run: tickets.RunId,
    issue: QualifiedTaskId,
    item: Mapping[str, object],
    reads: tickets.BoardReads,
    scratch: Path,
) -> None:
    """Re-estimate ``issue``, which no ticket of ``run`` here answers for, onto the item alone."""
    now = project(item)
    try:
        decided = tickets.re_estimate_decision(issue, item, reads)
    except tickets.Refused as refusal:
        state.say(f"  {issue}: not re-estimated: {refusal}")
        return
    # The priority is its own write, sent first: one not certainly landed is recorded in
    # `pending.json` as a `priority` write, which the next pass retries alone. The estimate line
    # and the record are written after it either way, as a `priority` write of their own.
    priority = decided.priority.value
    if priority != now["priority"]:
        arguments = ["task", "priority", "set", issue, priority]
        _write_once(state, snapshot, run, issue, "priority", {"priority": priority}, arguments)
    estimate: dict[str, object] = {}
    if decided.content.rstrip() != now["content"]:
        estimate["content"] = decided.content.rstrip()
    if decided.record != now["record"]:
        estimate["record"] = decided.record
    if estimate:
        arguments = _update_words(issue, estimate, scratch)
        _write_once(state, snapshot, run, issue, "priority", estimate, arguments)


def _write_once(  # noqa: PLR0913 - one write, and everything its record names
    state: Pass,
    snapshot: Snapshot,
    run: tickets.RunId,
    issue: QualifiedTaskId,
    write: Write,
    intended: Mapping[str, object],
    arguments: Sequence[str],
) -> bool:
    """Send one write; whether its own response shows it landed, recording it pending if not."""
    seen = state.versions.get(issue)
    result = state.send(issue, arguments, intended)
    if result.state is None:
        state.say(f"  {issue}: {write} written ({', '.join(intended)})")
        return True
    state.pending.append(
        _entry(snapshot, run, issue, write, result.state, seen, intended, result, state.now())
    )
    state.say(f"  {issue}: {write} {result.state}: {result.error}")
    return False


def settle(  # noqa: C901, PLR0912, PLR0915 - the pass's one read, then its writes, in order
    root: Path,
    plan: Plan,
    launched: Mapping[str, str],
    *,
    say: Say = _say,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """Check each launched run, make the pass's one batched read, write, and move the watermark.

    The read is one unpinned `task show-many` over every issue a checked run replied on and
    every item `pending.json` names; settle then verifies each reply, recovers each carried
    entry, and makes this pass's writes, item by item. The watermark moves only as the module
    states. Answers :data:`UNANSWERED` when a run was not answered soundly or a write needs
    attention.
    """
    snapshot = read_snapshot(plan.snapshot)
    sound, failed = True, False
    checked: dict[tickets.RunId, list[tickets.Response]] = {}
    for run, feedback in plan.runs:
        follow_up = launched.get(run)
        if follow_up is None:
            say(f"run {run}: its follow-up run did not launch, so {feedback} is unanswered")
            sound, failed = False, True
            continue
        if plan.detach:
            say(
                f"run {run}: launched follow-up run {follow_up}; watch it with: just watch "
                f"{follow_up}; once it settles, check it with: "
                f"{check_command(snapshot, run, feedback)}"
            )
            sound = False
            continue
        try:
            unanswered, answered = tickets.responses_checked(snapshot.board, feedback, run)
        except (OSError, tickets.Refused) as exc:
            unanswered, answered = tickets.Unanswered(feedback, [str(exc)]), []
        if unanswered is not None:
            sound, failed = False, True
            say(f"run {run} (follow-up run {follow_up}): check-responses failed")
            for problem in unanswered.problems:
                say(f"  {unanswered.path}: {problem}")
            continue
        checked[run] = answered
    carried = read_pending(root)
    ids = list(
        dict.fromkeys(
            [one.comment.issue for answered in checked.values() for one in answered]
            + [one.item for one in carried]
        )
    )
    state = Pass(
        root=root,
        board=snapshot.board,
        settings=plan_store.configured_settings(),
        read={},
        comments={},
        versions={},
        pending=[],
        say=say,
        now=now,
        # Read from the snapshot before the board is, so nothing local stands between the
        # batched read and the writes that follow it.
        held=snapshot_held(
            snapshot, {one.issue for chosen in snapshot.selection.values() for one in chosen}
        ),
    )
    if ids:
        for issue, detail in zip(ids, shown_details(ids), strict=True):
            try:
                state.read[issue] = detail_item(detail, issue)
                state.comments[issue] = detail_comments(detail, issue)
                # Every write below projects the item, so one it cannot is named here instead.
                project(state.read[issue])
            except OSError as exc:
                state.read.pop(issue, None)
                state.comments.pop(issue, None)
                say(f"the batched read could not answer for {issue}: {exc}")
                continue
            version = state.read[issue].get("version")
            state.versions[issue] = version if isinstance(version, str) else None
        say(f"verified on the board with one batched read of {len(ids)} item(s)")
    verified: dict[tickets.RunId, Verified] = {}
    for checked_run, answered in checked.items():
        result = verify_replies(checked_run, state.held, answered, state)
        if result.problems:
            sound, failed = False, True
            say(
                f"run {checked_run} (follow-up run {launched[checked_run]}): its replies are not "
                "on the board"
            )
            for problem in result.problems:
                say(f"  {problem}")
            continue
        verified[checked_run] = result
        say(f"run {checked_run} (follow-up run {launched[checked_run]}): check-responses passed")
        for response in answered:
            if response.fallback is not None:
                state.fallbacks += 1
                say(
                    f"  fallback read: {response.comment.issue}: reply {response.reply}: "
                    f"{response.fallback}"
                )
        for url in result.urls:
            say(f"  reply: {url}")
    with tempfile.TemporaryDirectory(prefix="settle-") as held_scratch:
        scratch = Path(held_scratch)
        kept = []
        for entry in carried:
            if (still := recover(state, entry, scratch)) is not None:
                kept.append(still)
        for verified_run, result in verified.items():
            chosen = snapshot.selection.get(verified_run, ())
            for issue in dict.fromkeys(one.issue for one in chosen):
                settle_item(
                    state, snapshot, verified_run, issue, result.bodies.get(issue, []), scratch
                )
    pending = kept + state.pending
    write_pending(root, pending)
    # A dry run never reaches settle: a snapshot it took moves the watermark once it is
    # launched with `--from` and settled sound, as a plain run's does.
    if snapshot.scope is None and sound and snapshot.captured_all:
        for board in snapshot.boards:
            held = read_watermark(root, board)
            advanced = write_watermark(root, board, snapshot.queried_at)
            path = watermark_path(root, board)
            if held is not None and advanced == held:
                say(
                    f"watermark: kept at {instant(held)} in {path}, already past this pass's "
                    "query less the overlap"
                )
            else:
                say(f"watermark: advanced to {instant(advanced)} in {path}")
    elif snapshot.scope is not None:
        say(
            f"watermark: left as it was, because the gathering was scoped to run "
            f"{snapshot.scope}'s comments"
        )
    elif not snapshot.captured_all:
        say("watermark: left as it was, because the snapshot did not capture every item")
    elif plan.detach:
        say("watermark: left as it was, because the launched runs have not been checked")
    else:
        say("watermark: left as it was, because a launched run was not answered soundly")
    say(f"board writes: {state.writes}; fallback reads: {state.fallbacks}")
    if pending:
        say(f"needs attention: {len(pending)} write(s) pending in {pending_path(root)}")
        for one in pending:
            say(
                f"  pending: {one.write} of {one.item} ({one.state})"
                + (f": {one.error}" if one.error else "")
            )
        failed = True
    say(_store_line(snapshot))
    return UNANSWERED if failed else DONE


class _Parser(argparse.ArgumentParser):
    """A parser whose refusals exit with :data:`UNRUNNABLE` and say what to do next."""

    def error(self, message: str) -> NoReturn:
        self.exit(UNRUNNABLE, f"{PROG}: refused: {message}; run it with --help for the contract\n")


#: The flags `--from` may not be named beside: each names a gathering of its own.
GATHERING_FLAGS = ("since", "scope", "issue", "board", "dry_run")


def _parser() -> _Parser:
    parser = _Parser(
        prog=PROG,
        description="Route people's board comments to the runs owning their issues.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    gathering = commands.add_parser("gather", help="copy the board into a snapshot")
    gathering.add_argument("--root", type=Path, required=True, help="the drafts root")
    gathering.add_argument(
        "--intent", type=Path, required=True, help="where to write what to launch"
    )
    gathering.add_argument("--to", dest="board", metavar="SOURCE")
    gathering.add_argument("--since", metavar="RFC3339")
    scoped = gathering.add_mutually_exclusive_group()
    scoped.add_argument("--run", dest="scope", metavar="RUN-ID")
    scoped.add_argument("--issue", metavar="QUALIFIED-ID")
    gathering.add_argument("--dry-run", action="store_true")
    gathering.add_argument("--detach", action="store_true")
    gathering.add_argument("--from", dest="source", type=Path, metavar="SNAPSHOT")
    launching = commands.add_parser("launch", help="claim a snapshot and write its feedback")
    launching.add_argument("--root", type=Path, required=True, help="the drafts root")
    launching.add_argument("--intent", type=Path, required=True)
    launching.add_argument("--plan", type=Path, required=True)
    launches = commands.add_parser("launches", help="print each launch the plan names")
    launches.add_argument("--root", type=Path, required=True, help="the drafts root")
    launches.add_argument("--plan", type=Path, required=True)
    settling = commands.add_parser("settle", help="check each launched run, and write the board")
    settling.add_argument("--root", type=Path, required=True, help="the drafts root")
    settling.add_argument("--plan", type=Path, required=True)
    settling.add_argument("--launched", action="append", default=[], metavar="RUN=FOLLOW-UP")
    environment = commands.add_parser(
        "snapshot-env", help="print the variables pointing a process at a feedback's snapshot"
    )
    environment.add_argument("--feedback", type=Path, required=True)
    return parser


def _refuse(message: str) -> int:
    print(f"{PROG}: refused: {message}", file=sys.stderr)
    return UNRUNNABLE


class Intent(NamedTuple):
    """What `gather` hands `launch`: the snapshot to claim, or none, and how to claim it."""

    snapshot: Path | None
    #: Whether this gathering took the snapshot itself, rather than naming it with `--from`.
    fresh: bool
    detach: bool


def _write_intent(path: Path, intent: Intent) -> None:
    """The one writer of an intent file."""
    snapshot = None if intent.snapshot is None else str(intent.snapshot)
    path.write_text(
        json.dumps({"snapshot": snapshot, "fresh": intent.fresh, "detach": intent.detach}),
        encoding="utf-8",
    )


def read_intent(path: Path) -> Intent:
    """The one reader of an intent file; :class:`OSError` for a file of any other shape, since a
    malformed `fresh` would claim a snapshot past the checks a `--from` launch owes."""
    try:
        held: object = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as broken:
        raise OSError(f"{path} is not JSON: {broken}") from None
    if (
        not isinstance(held, dict)
        or sorted(held) != sorted(Intent._fields)
        or not _optional_str(held["snapshot"])
        or not isinstance(held["fresh"], bool)
        or not isinstance(held["detach"], bool)
    ):
        raise OSError(f"{path} is not an intent: {', '.join(Intent._fields)} with their types")
    snapshot = None if held["snapshot"] is None else Path(held["snapshot"])
    return Intent(snapshot, held["fresh"], held["detach"])


def _gathered(arguments: argparse.Namespace) -> int:  # noqa: PLR0911 - one refusal per flag
    if arguments.source is not None:
        named = [
            f"--{flag.replace('_', '-')}" for flag in GATHERING_FLAGS if getattr(arguments, flag)
        ]
        if named:
            spelled = [{"--scope": "--run", "--board": "--to"}.get(one, one) for one in named]
            return _refuse(
                "--from launches the snapshot it names exactly as gathered, so "
                f"{', '.join(spelled)} cannot be named beside it; gather a fresh one with "
                "--dry-run instead"
            )
        _write_intent(
            arguments.intent, Intent(arguments.source.absolute(), False, arguments.detach)
        )
        return DONE
    scope = arguments.scope
    board = tickets.BOARD if arguments.board is None else arguments.board
    if scope is not None and not LAUNCHABLE_RUN.fullmatch(scope):
        return _refuse(f"--run {scope!r} is not a run id")
    issue = arguments.issue
    if issue is not None and not tickets.QUALIFIED_ID.fullmatch(issue):
        return _refuse(f"--issue {issue!r} is not a qualified id, <source>:<native-id>")
    # The board's name is spelled into its watermark's file name, so it is one path word.
    if not RECORD_COMPONENT.fullmatch(board):
        return _refuse(f"--to {board!r} is not a source name")
    try:
        since = None if arguments.since is None else moment(arguments.since, "--since")
    except OSError as exc:
        return _refuse(str(exc))
    try:
        snapshot = gather(
            arguments.root,
            board,
            named_board=arguments.board is not None,
            since=since,
            scope=scope,
            issue=None if issue is None else QualifiedTaskId(issue),
            dry_run=arguments.dry_run,
        )
    except (Unbounded, Unowned) as refusal:
        return _refuse(f"{refusal}; nothing was launched")
    except OSError as exc:
        return _refuse(
            f"{exc}; nothing was launched. Check that --to names a source this checkout's plan "
            "store configures and can read, then retry"
        )
    launch = None if snapshot is None or arguments.dry_run else snapshot.directory
    _write_intent(arguments.intent, Intent(launch, True, arguments.detach))
    return DONE


def _launched(arguments: argparse.Namespace) -> int:
    try:
        intent = read_intent(arguments.intent)
    except OSError as exc:
        return _refuse(f"{exc}; nothing was written and nothing was launched")
    if intent.snapshot is None:
        arguments.plan.write_text("", encoding="utf-8")
        return DONE
    try:
        plan = claim(arguments.root, intent.snapshot, fresh=intent.fresh, detach=intent.detach)
    except Unlaunchable as refusal:
        return _refuse(f"{refusal}; nothing was written and nothing was launched")
    except OSError as exc:
        return _refuse(str(exc))
    arguments.plan.write_text(json.dumps(_plan_document(plan)), encoding="utf-8")
    return DONE


def _launches(arguments: argparse.Namespace) -> int:
    """One tab-separated line per launch: the run, its feedback file, how the launches are
    followed — `detach` or `attach` — and the `--to` word, empty when none was named."""
    if not arguments.plan.read_text(encoding="utf-8"):
        return DONE
    try:
        plan = read_plan(arguments.plan, arguments.root)
        snapshot = read_snapshot(plan.snapshot)
    except OSError as exc:
        return _refuse(str(exc))
    mode = "detach" if plan.detach else "attach"
    board = f"--to={snapshot.board}" if snapshot.named_board else ""
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
        return _refuse(
            f"--launched takes RUN=FOLLOW-UP, two run ids, and was given {arguments.launched!r}"
        )
    try:
        return settle(arguments.root, read_plan(arguments.plan, arguments.root), launched)
    except OSError as exc:
        return _refuse(str(exc))


def _environment(arguments: argparse.Namespace) -> int:
    """Print `NAME=VALUE` per line: the snapshot source a feedback file names, for a launch."""
    try:
        text = arguments.feedback.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return _refuse(f"{arguments.feedback} could not be read: {exc}")
    directory = recorded_snapshot(text)
    if directory is None:
        return _refuse(f"{arguments.feedback} names no snapshot on its snapshot line")
    for name, value in snapshot_environment(directory).items():
        print(f"{name}={value}")
    return DONE


def main(argv: Sequence[str] | None = None) -> int:
    """The script's steps: gather, launch, list the launches, settle."""
    arguments = _parser().parse_args(argv)
    handlers = {
        "gather": _gathered,
        "launch": _launched,
        "launches": _launches,
        "settle": _settled,
        "snapshot-env": _environment,
    }
    return handlers[arguments.command](arguments)


if __name__ == "__main__":  # pragma: no cover - the module's own command line
    raise SystemExit(main())
