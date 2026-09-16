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

**Which comments.** A comment no run's marker owns is a person's. It is selected when it is
newer than the run's last response, which is the later of two moments this host can read:
the run's last ticket copy, read as the moment its ticket files under the drafts root were
last written, since the follow-up agent writes each one with the status `board-status`
printed immediately before it copies it, nothing on the board touches them, and no copy is
recorded anywhere else — and the latest of the run's own marked comments,
anywhere on the board. A run that has responded in neither way has every person's comment
selected, because feedback silently missed is the failure this exists to end. A comment is
dated by its last edit, so a person editing an earlier comment is new feedback.

**It reads and never writes the board.** The only file it writes is the feedback, under
`<drafts root>/feedback/<run-id>/`, outside the `tasks/` tree the store reads, so the
manager can read what was sent. A person's move of an item to `Todo`, `Deferred` or
`In Progress` is a decision no run undoes, so the feedback says so again beside the
re-dispatch rule that copies a ticket carrying the status the board holds.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NewType, NoReturn

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


CommentId = NewType("CommentId", str)


class NothingNew(ValueError):
    """The run has no person's comment newer than its last response; says which and why."""


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
    url: str
    author: str
    last_changed: datetime
    text: str


@dataclass(frozen=True)
class Selection:
    """What :func:`select` found for one run."""

    chosen: list[Selected]
    #: The run's last response, or ``None`` when it has not responded.
    since: datetime | None
    #: The issues read: the ones the run owns or has marked a comment on.
    relevant: list[Issue]


def moment(value: object, what: str) -> datetime:
    """An RFC 3339 time the board reports, or :class:`OSError` naming ``what`` carried it."""
    if not isinstance(value, str):
        raise OSError(f"{what} reports no time")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise OSError(f"{what} reports {value!r}, which is not an RFC 3339 time") from None
    if parsed.tzinfo is None:
        raise OSError(f"{what} reports {value!r}, which names no offset")
    return parsed.astimezone(UTC)


def _optional_text(value: object, what: str) -> str | None:
    """A string the board may leave out, or :class:`OSError` when it is something else."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise OSError(f"{what} is not a string")
    return value


def read_comment(held: object, issue: QualifiedTaskId) -> Comment:
    """One comment the board listed on ``issue``, or :class:`OSError` naming what is wrong.

    Its time is its last edit, or its creation where the board reports no edit.
    """
    if not isinstance(held, Mapping):
        raise OSError(f"the board listed a comment on {issue} that is not an object")
    identifier, body = held.get("id"), held.get("body")
    if not isinstance(identifier, str) or not identifier:
        raise OSError(f"the board listed a comment on {issue} without an id")
    what = f"comment {identifier!r} on {issue}"
    if not isinstance(body, str):
        raise OSError(f"{what} has no text")
    edited = held.get("updated_at")
    return Comment(
        id=CommentId(identifier),
        author=_optional_text(held.get("author"), f"the author of {what}"),
        body=body,
        url=_optional_text(held.get("url"), f"the URL of {what}"),
        last_changed=moment(held.get("created_at") if edited is None else edited, what),
    )


def board_issues(board: str) -> list[Issue]:
    """Every item on ``board`` a follow-up run owns, each with its comments, oldest first."""
    found = []
    for record in plan_store.paged(["task", "list", "--source", board], "task"):
        match record:
            case {"id": str(listed_id), "item": Mapping() as item}:
                pass
            case _:
                raise OSError(f"the board {board!r} listed an item without an id and a payload")
        if not listed_id.startswith(f"{board}:") or listed_id == f"{board}:":
            raise OSError(f"the board {board!r} listed {listed_id!r}, which is not one of its ids")
        qualified = QualifiedTaskId(listed_id)
        owner = tickets.issue_owner(item)
        if owner is None:
            continue
        title = item.get("title")
        if not isinstance(title, str):
            raise OSError(f"the board listed {qualified} without a title")
        listed = plan_store.store_json(["task", "comment", "list", qualified]).get("comments")
        if not isinstance(listed, list):
            raise OSError(f"the board listed the comments of {qualified} as something not a list")
        location = item.get("location")
        path = location.get("path") if isinstance(location, Mapping) else None
        found.append(
            Issue(
                id=qualified,
                title=title,
                owner=owner,
                location=_optional_text(path, f"the location of {qualified}"),
                url=_optional_text(item.get("url"), f"the URL of {qualified}"),
                comments=tuple(read_comment(one, qualified) for one in listed),
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


def select(run: str, issues: Sequence[Issue], tickets_written: datetime | None) -> Selection:
    """The comments of ``run``'s issues a person wrote after its last response."""
    marked = [
        (issue, comment)
        for issue in issues
        for comment in issue.comments
        if tickets.may_change_comment(run, comment.body)
    ]
    relevant = [
        issue for issue in issues if issue.owner == run or any(held is issue for held, _ in marked)
    ]
    responses = [comment.last_changed for _, comment in marked]
    if tickets_written is not None:
        responses.append(tickets_written)
    since = max(responses) if responses else None
    chosen = []
    for issue in relevant:
        for comment in issue.comments:
            if tickets.comment_owner(comment.body) is not None:
                continue
            if since is not None and comment.last_changed <= since:
                continue
            chosen.append(
                Selected(
                    issue=issue,
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
    """The feedback file: what the comments are, the status rule, then each comment verbatim."""
    after = (
        f"after the run last responded, at {since.strftime(MOMENT_FORMAT)}"
        if since is not None
        else "while the run has not yet responded on the board"
    )
    sections = [
        f"People commented on run `{run}`'s follow-ups on the `{board}` board {after}. "
        f"Gathered by `just follow-ups-handle-comments {run}`, each is quoted verbatim "
        "below with its URL and its author.\n\n"
        "Answer each one within the rules above: change this run's ticket and copy it again "
        "for an issue this run created, and edit this run's one comment for another run's "
        "issue. **Never change a board item's status.** Before every copy, write the status "
        "`board-status` prints, which is the one the board holds the item at, so a person's "
        "move to `Todo`, `Deferred` or `In Progress` stands whenever they made it. Report "
        "each comment's URL beside what you did about it, or why you did nothing.\n"
    ]
    for number, selected in enumerate(chosen, start=1):
        issue = selected.issue
        whose = "this run's issue" if issue.owner == run else f"run `{issue.owner}`'s issue"
        sections.append(
            f"### Comment {number}: on `{issue.id}`, {whose}\n\n"
            f"- URL: {selected.url}\n"
            f"- Author: {selected.author}\n"
            f"- Last changed: {selected.last_changed.strftime(MOMENT_FORMAT)}\n"
            f"- Issue title: {issue.title}\n\n" + _fenced(selected.text)
        )
    return "\n".join(sections)


def feedback(root: Path, board: str, run: str, now: datetime) -> Path:
    """Write ``run``'s new board feedback under ``root``, or :class:`NothingNew` saying why."""
    issues = board_issues(board)
    selection = select(run, issues, tickets_last_written(root, run))
    chosen, since, relevant = selection.chosen, selection.since, selection.relevant
    if not relevant:
        raise NothingNew(
            f"run {run} owns no follow-up issue on the {board!r} board and has left no marked "
            "comment on one, so no comment there is feedback for it"
        )
    if not chosen:
        held = sum(len(issue.comments) for issue in relevant)
        moment_text = (
            f"newer than its last response at {since.strftime(MOMENT_FORMAT)}"
            if since is not None
            else "that no run's marker owns"
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
