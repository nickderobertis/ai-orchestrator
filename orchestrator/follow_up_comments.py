"""A person's new board comments on one run's follow-ups, gathered into one feedback file.

`just follow-ups-handle-comments <run-id>` is how a comment somebody writes on the
`followups` board reaches the run that owns the follow-up it is about, rather than waiting
for a manager to read the board, work out which run owns each issue and write the feedback
by hand. This module decides what that feedback is; `scripts/follow-ups-handle-comments.sh`
hands the file it writes to `just follow-ups <run-id> --feedback FILE`, the one re-dispatch
path, so the follow-up agent that answers works under the ownership and status rules that
path already composes into its task.

**Which issues.** Every board item carrying a follow-up record is read: the issues the run
owns (:func:`follow_up_tickets.issue_owner`), and the other runs' issues it has left a
marked comment on (:func:`follow_up_tickets.comment_owner`). Nothing else on the board is
the run's to answer.

**Which comments: gathered until a reply names them.** This is the one statement of the
rule; the documents point here.

* **Marked comments are never gathered.** A comment whose last line is any run's marker, of
  either kind, this run's or another's, is not a person's.
* **Answered.** A person's comment is answered when one of this run's replies names its id in
  `answers` and that reply is not older than the comment's last change. An answered comment
  is never gathered, and a person editing a comment after its reply makes it unanswered
  again, since a comment is dated by its last edit.
* **The boundary.** Unanswered comments at or before one fixed moment are not gathered: they
  predate replies, and were answered by edits. That moment is the latest of the run's
  responses that are not replies — its ticket files last being written, and its evidence
  comments — taken strictly before the run's **first gathering**. The ticket files stand in
  for the run's last ticket copy, since the follow-up agent writes each one with the status
  `board-status` printed immediately before it copies it, nothing on the board touches them,
  and no copy is recorded anywhere else. The first gathering is the stamp in the name of the
  earliest feedback file under `<drafts root>/feedback/<run-id>/`; a run with no feedback file
  yet takes the latest of those responses overall. A run with no such response has no
  boundary, because feedback silently missed is the failure this exists to end.
* **The boundary is recorded, not recomputed.** Every feedback file records the boundary its
  gathering used on a :data:`FEEDBACK_BOUNDARY` line, and every later gathering reads the one
  the earliest file records: a re-dispatch copies its ticket again, which moves the ticket
  files' last write past the first gathering, so a recomputed boundary would lose the
  response it rested on. An earliest file recording none, written before files recorded it,
  is read as the boundary computed above.
* **Gathered.** Every other person's comment on a relevant issue. After the first gathering,
  whether a comment is gathered depends only on whether a reply names it: ticket copies,
  evidence comments and replies made later hide nothing, so a comment written between one
  gathering and the replies that gathering's dispatch posts is gathered the next time.

**It reads and never writes the board.** The only file it writes is the feedback, under
`<drafts root>/feedback/<run-id>/`, outside the `tasks/` tree the store reads, so the
manager can read what was sent, and the next gathering when the first was. A person's move
of an item to `Todo`, `Deferred` or `In Progress` is a decision no run undoes, so the
feedback says so again beside the re-dispatch rule that copies a ticket carrying the status
the board holds.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import chain
from pathlib import Path
from typing import NamedTuple, NoReturn

from orchestrator import follow_up_tickets as tickets
from orchestrator import plan_store
from orchestrator.plan_store import RECORD_COMPONENT, QualifiedTaskId
from orchestrator.project_store import TASKS_DIRECTORY

FEEDBACK_DIRECTORY = "feedback"

MOMENT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"

UNKNOWN_AUTHOR = "not reported by the board"

PROG = "follow-up-comments"

WRITTEN = 0
UNRUNNABLE = 2
NOTHING_NEW = 3


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


class NothingNew(ValueError):
    """The run has no person's comment left to gather; says which and why."""


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
    """One board item carrying a follow-up record, and the comments the board holds on it."""

    id: QualifiedTaskId
    title: str
    owner: tickets.RunId
    location: str | None
    url: str | None
    comments: tuple[Comment, ...]


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
class Selection:
    """What :func:`select` found for one run."""

    chosen: list[Selected]
    #: The boundary the module states, or ``None`` when the run has none.
    since: datetime | None
    #: The issues read: the ones the run owns or has marked a comment on.
    relevant: list[Issue]


def moment(value: datetime | str | None, what: str) -> datetime:
    """A time the board reports, or :class:`OSError` when it reports none."""
    if value is None:
        raise OSError(f"{what} reports no time")
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            raise OSError(f"{what} reports {value!r}, which is not an RFC 3339 time") from None
    if value.tzinfo is None:
        raise OSError(f"{what} reports {value!r}, which names no offset")
    return value.astimezone(UTC)


def board_issues(board: str) -> list[Issue]:
    """Every item on ``board`` a follow-up run owns, each with its comments, oldest first."""
    found = []
    pages = plan_store.every_page(
        f"the board {board!r}", plan_store.client().task_list, source=[board]
    )
    for held in chain.from_iterable(page.items for page in pages):
        listed_id = held.id.model_dump()
        if not listed_id.startswith(f"{board}:") or listed_id == f"{board}:":
            raise OSError(f"the board {board!r} listed {listed_id!r}, which is not one of its ids")
        qualified = QualifiedTaskId(listed_id)
        item = held.item.model_dump(mode="python")
        owner = tickets.issue_owner(item)
        if owner is None:
            continue
        listed = plan_store.sdk(plan_store.client().task_comment_list(str(qualified))).comments
        location = held.item.location.model_dump(mode="python") if held.item.location else {}
        found.append(
            Issue(
                id=qualified,
                title=held.item.title,
                owner=owner,
                location=location.get("path"),
                url=held.item.url,
                comments=tuple(
                    Comment(
                        id=CommentId(comment.id.model_dump()),
                        author=comment.author,
                        body=comment.body,
                        url=comment.url,
                        last_changed=moment(
                            comment.updated_at or comment.created_at,
                            f"comment {comment.id.model_dump()!r} on {qualified}",
                        ),
                    )
                    for comment in listed
                ),
            )
        )
    return found


def tickets_last_written(root: Path, run: str) -> datetime | None:
    """When any of ``run``'s ticket files under the drafts root was last written, if ever.

    The stand-in for the run's last ticket copy, for the reason the module states.
    """
    directory = root / TASKS_DIRECTORY / run / tickets.TICKETS
    # A write with no copy after it is not a journey anything here makes: the follow-up
    # agent's contract writes a ticket immediately before copying it, and the journey drives
    # that pairing as the quiet run's last response. Staging a lone write would test a
    # sequence no run performs, while the board keeps no record of a copy to test instead.
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    times = [path.stat().st_mtime for path in directory.glob(f"*{tickets.TICKET_SUFFIX}")]
    return datetime.fromtimestamp(max(times), UTC) if times else None


def comment_url(issue: Issue, comment: Comment) -> str:
    """The comment's own URL; else a URL into the issue that holds it, fragment its id."""
    # Only the hosted `followups` board reports a URL, and reading it needs that board's
    # credential, which every dispatch and journey is denied; the `local-md` stand-in the
    # journey drives reports none. `tests/test_follow_up_comments.py` stands in the store's
    # answer for these two branches, and the journey drives the location branch below.
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    if comment.url is not None:
        return comment.url
    fragment = f"#comment-{comment.id}"
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    if issue.url is not None:
        return issue.url + fragment
    if issue.location is not None:
        return Path(issue.location).absolute().as_uri() + fragment
    raise OSError(f"the board reports no URL and no location for {issue.id} or its comments")


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
    return FEEDBACK_BOUNDARY.format(
        boundary=NO_BOUNDARY if since is None else since.strftime(MOMENT_FORMAT)
    )


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
            stamp = datetime.strptime(matched["stamp"], STAMP_FORMAT).replace(tzinfo=UTC)
            written.append((stamp, int(matched["attempt"] or 1), path))
    if not written:
        return None
    stamp, _, path = min(written)
    return Gathering(stamp, recorded_boundary(path.read_text(encoding="utf-8"), str(path)))


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
    """The boundary as the module states it, for a run whose first gathering recorded none."""
    responses = [] if tickets_written is None else [tickets_written]
    responses.extend(
        comment.last_changed for _, comment, owner in _marked(run, issues) if owner.answers is None
    )
    if first_gathered is not None:
        responses = [moment for moment in responses if moment < first_gathered]
    return Boundary(max(responses) if responses else None)


def select(run: str, issues: Sequence[Issue], boundary: Boundary) -> Selection:
    """The person's comments on ``run``'s issues that the module's gathering rule gathers."""
    marked = _marked(run, issues)
    relevant = [
        issue
        for issue in issues
        if issue.owner == run or any(held is issue for held, _, _ in marked)
    ]
    # A reply is posted on the issue holding the comment it answers, so it is looked up there:
    # a board whose comment ids are unique only within an issue cannot answer the wrong one.
    replied: dict[tuple[QualifiedTaskId, CommentId], datetime] = {}
    for issue, comment, owner in marked:
        if owner.answers is not None:
            key = (issue.id, owner.answers)
            replied[key] = max(comment.last_changed, replied.get(key, comment.last_changed))
    since = boundary.moment
    chosen = []
    for issue in relevant:
        for comment in issue.comments:
            if tickets.comment_owner(comment.body) is not None:
                continue
            answered = replied.get((issue.id, comment.id))
            if answered is not None and answered >= comment.last_changed:
                continue
            if since is not None and comment.last_changed <= since:
                continue
            chosen.append(
                Selected(
                    issue=issue,
                    id=comment.id,
                    url=comment_url(issue, comment),
                    author=comment.author or UNKNOWN_AUTHOR,
                    last_changed=comment.last_changed,
                    text=comment.body,
                )
            )
    return Selection(chosen=chosen, since=since, relevant=relevant)


def _fenced(text: str) -> str:
    """``text`` verbatim inside a fence longer than any backtick run it holds."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text.rstrip()}\n{fence}\n"


def render(run: str, board: str, chosen: Sequence[Selected], since: datetime | None) -> str:
    """The feedback file: what the comments are, what to do, then each comment verbatim."""
    after = f", each changed after {since.strftime(MOMENT_FORMAT)}," if since is not None else ""
    sections = [
        f"{boundary_line(since)}\n\n"
        f"People commented on run `{run}`'s follow-ups on the `{board}` board{after} and no "
        f"reply of this run answers them yet. Gathered by `just follow-ups-handle-comments "
        f"{run}`, each is quoted verbatim below with its id, its URL, its author and when it "
        "last changed.\n\n"
        "For each quoted comment, in this order:\n\n"
        '1. **Act on it** under "Ownership on the board" above: perform whatever action it '
        "calls for, or none.\n"
        "2. **Post its one reply**, naming the comment's id, as those rules state.\n"
        "3. **Report** the comment's URL beside what you did about it, or why you did "
        "nothing.\n\n"
        "**Never change a board item's status.** Before every copy, write the status "
        "`board-status` prints, which is the one the board holds the item at, so a person's "
        "move to `Todo`, `Deferred` or `In Progress` stands whenever they made it.\n"
    ]
    for number, selected in enumerate(chosen, start=1):
        issue = selected.issue
        whose = "this run's issue" if issue.owner == run else f"run `{issue.owner}`'s issue"
        sections.append(
            f"### Comment {number}: on `{issue.id}`, {whose}\n\n"
            f"- Comment id: {selected.id}\n"
            f"- URL: {selected.url}\n"
            f"- Author: {selected.author}\n"
            f"- Last changed: {selected.last_changed.strftime(MOMENT_FORMAT)}\n"
            f"- Issue title: {issue.title}\n\n" + _fenced(selected.text)
        )
    return "\n".join(sections)


def feedback(root: Path, board: str, run: str, now: datetime) -> Path:
    """Write ``run``'s new board feedback under ``root``, or :class:`NothingNew` saying why."""
    issues = board_issues(board)
    first = first_gathering(root, run)
    if first is not None and first.recorded is not None:
        boundary = first.recorded
    else:
        written = tickets_last_written(root, run)
        boundary = computed_boundary(run, issues, written, None if first is None else first.stamp)
    selection = select(run, issues, boundary)
    chosen, since, relevant = selection.chosen, selection.since, selection.relevant
    if not relevant:
        raise NothingNew(
            f"run {run} owns no follow-up issue on the {board!r} board and has left no marked "
            "comment on one, so no comment there is feedback for it"
        )
    if not chosen:
        held = sum(len(issue.comments) for issue in relevant)
        moment_text = "that no reply of this run answers" + (
            f" and that changed after {since.strftime(MOMENT_FORMAT)}" if since is not None else ""
        )
        raise NothingNew(
            f"run {run} has no new feedback: of the {held} comment(s) on the {len(relevant)} "
            f"issue(s) it owns or commented on in {board!r}, none is a person's {moment_text}"
        )
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


class _Parser(argparse.ArgumentParser):
    """A parser whose refusals exit with :data:`UNRUNNABLE` and say what to do next."""

    def error(self, message: str) -> NoReturn:
        self.exit(UNRUNNABLE, f"{PROG}: refused: {message}; run it with --help for the contract\n")


def _parser() -> _Parser:
    parser = _Parser(
        prog=PROG,
        description="Write one run's new board comments as the feedback its re-dispatch takes.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    compose = commands.add_parser("feedback", help="write the feedback file and print its path")
    compose.add_argument("--root", type=Path, required=True, help="the drafts root")
    # A successful read of the default is a read of the live `followups` board, whose
    # credential no journey holds; the recipe's journey,
    # `tests/plan_tooling/test_follow_ups_handle_comments_recipe_e2e.py`, drives it naming
    # no board and reads that it reached the `followups` source.
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    compose.add_argument("--board", default=tickets.BOARD, metavar="SOURCE")
    compose.add_argument("run", metavar="RUN-ID")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Write the feedback file and print its path, for the recipe."""
    arguments = _parser().parse_args(argv)
    if not RECORD_COMPONENT.fullmatch(arguments.run):
        print(f"{PROG}: refused: {arguments.run!r} is not a run id", file=sys.stderr)
        return UNRUNNABLE
    try:
        path = feedback(arguments.root, arguments.board, arguments.run, datetime.now(UTC))
    except NothingNew as refusal:
        print(
            f"{PROG}: {refusal}; nothing was launched. Run it again once somebody comments "
            "on one of those issues",
            file=sys.stderr,
        )
        return NOTHING_NEW
    except OSError as exc:
        print(
            f"{PROG}: refused: {exc}; nothing was launched. Check that --board names a source "
            "this checkout's plan store configures and can read (`just plans task list --source "
            f"{arguments.board}`), then retry",
            file=sys.stderr,
        )
        return UNRUNNABLE
    print(path)
    return WRITTEN


if __name__ == "__main__":  # pragma: no cover - the module's own command line
    raise SystemExit(main())
