"""Which board comments go to which run, the files they are written as, and the watermark.

`tests/plan_tooling/test_follow_ups_answer_comments_recipe_e2e.py` drives the recipe through
real launches. What is proven here is each of the module's three steps in process, against
the installed `onetaskgraph`: a drafts root and a second local store standing in for the
board, both named through the store's environment layer, tickets copied and comments written
through the store's own verbs. Only the malformed answers a real store never gives are put
in the store's place, the way `tests/test_plan_store.py` refuses a listing it cannot account
for.

Comment times are the store's own and whole seconds, so a test that needs one comment to be
older than a response waits for the clock to pass a second between them.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shlex
import socket
import sys
import time
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import follow_up_variables
import pytest

from orchestrator import follow_up_comments as comments
from orchestrator import follow_up_tickets as tickets
from orchestrator import plan_store
from orchestrator.plan_store import WRITABLE_PLUGIN

#: The local store standing in for the board, spelled lowercase because it is spelled into
#: the store's environment layer as well as onto `--to`.
BOARD = "commentboard"
RUN = "listing-run"
OTHER_RUN = "earlier-run"
THIRD_RUN = "unrelated-run"
CAUSE = "cursor-skips-last-page"
SHARED_CAUSE = "sweep-trailer-omits-a-family"
PERSON = "a-reviewer"
HOST = socket.gethostname()
#: A second local store the stand-in board routes a `petsinc` root cause's ticket to, as the
#: committed `followups` routes one to `hellopatient-followups`, and a ticket filed there.
ROUTED = "commentrouted"
ROUTED_REPOSITORY = "github.com/petsinc/hp-api"
ROUTED_CAUSE = "intake-form-drops-a-field"
#: The issue URL Linear reports, as onetaskgraph-linear's recorded fixture spells one.
LINEAR_ISSUE_URL = "https://linear.app/acme/issue/ENG-1"


def test_comment_time_and_url_fallbacks_refuse_missing_answers() -> None:
    with pytest.raises(OSError, match="reports no time"):
        comments.moment(None, "comment")
    with pytest.raises(OSError, match="names no offset"):
        comments.moment(datetime(2026, 1, 1), "comment")
    with pytest.raises(OSError, match="not an RFC 3339 time"):
        comments.moment("yesterday", "comment")
    with pytest.raises(OSError, match="not an RFC 3339 time"):
        comments.moment("20260101T000000+00:00", "comment")

    comment = comments.Comment(
        comments.CommentId("c-1"),
        PERSON,
        "body",
        "https://example.invalid/comment",
        datetime.now(UTC),
    )
    issue = comments.Issue(
        comments.QualifiedTaskId(f"{BOARD}:x"),
        "title",
        tickets.RunId(RUN),
        "/tmp/issue.md",
        "https://example.invalid/issue",
        (comment,),
    )
    assert comments.comment_url(issue, comment) == "https://example.invalid/comment"
    assert comments.comment_url(issue, dataclasses.replace(comment, url=None)).startswith(
        "https://example.invalid/issue#comment-"
    )
    assert comments.comment_url(
        dataclasses.replace(issue, url=None), dataclasses.replace(comment, url=None)
    ).startswith("file:///tmp/issue.md#comment-")
    qualified = comments.QualifiedTaskId(f"{BOARD}:x")
    assert comments._location_path({"path": "/tmp/issue.md"}, qualified) == "/tmp/issue.md"
    assert comments._location_path({}, qualified) is None
    for path in (3, "", "/tmp/one\nfile:///elsewhere"):
        with pytest.raises(OSError, match="which is not a path"):
            comments._location_path({"path": path}, qualified)
    without_urls = dataclasses.replace(issue, url=None, location=None)
    with pytest.raises(OSError, match="no URL and no location"):
        comments.comment_url(without_urls, dataclasses.replace(comment, url=None))


@pytest.fixture
def drafts_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A drafts root the installed store reads, named the way a launch exports it."""
    root = tmp_path / "follow-ups"
    root.mkdir()
    monkeypatch.setenv(follow_up_variables.root_name(), str(root))
    monkeypatch.setenv(follow_up_variables.plugin_name(), WRITABLE_PLUGIN)
    return root


@pytest.fixture
def board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A second local store standing in for the board."""
    root = tmp_path / "board"
    root.mkdir()
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{BOARD.upper()}__PLUGIN", WRITABLE_PLUGIN)
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{BOARD.upper()}__CONFIG__ROOT", str(root))
    return root


def _filed(
    root: Path,
    run: str,
    cause: str,
    body: str = "What the ticket says.",
    *,
    host: str | None = HOST,
    record: bool = True,
    repository: str | None = None,
) -> str:
    """Write ``run``'s ticket for ``cause`` and copy it onto the board, as the agent does.

    ``host`` is the machine the record says verified it, and none is written when it is
    `None`; with ``record`` false the item carries no follow-up record at all. A
    ``repository`` is the one the item names, which a route of the board may file elsewhere.
    """
    path = tickets.ticket_path(root, run, cause)
    path.parent.mkdir(parents=True, exist_ok=True)
    held = f"  {tickets.KEY}:\n    created_by_run: {run}\n" + (
        f"    host: {host}\n" if host is not None else ""
    )
    path.write_text(
        "---\n"
        f'title: "some-service: {cause}"\n'
        'status: "backlog"\n'
        + (f'repositories: ["{repository}"]\n' if repository is not None else "")
        + "metadata:\n"
        + (held if record else "  other: 1\n")
        + "---\n"
        f"{body}\n",
        encoding="utf-8",
    )
    copied = plan_store.sdk(
        plan_store.client().task_copy([tickets.qualified_id(run, cause)], to=BOARD)
    )
    destination = copied.items[0].root.destination
    assert destination is not None, copied
    if record:
        # Bound to the item the copy reached, as `board-status` binds a ticket it copies.
        native = destination.model_dump().partition(":")[2]
        text = path.read_text(encoding="utf-8")
        opening = f"    created_by_run: {run}\n"
        binding = f"    {tickets.BINDING_FIELD}: {native}\n"
        path.write_text(text.replace(opening, opening + binding, 1), encoding="utf-8")
    return destination.model_dump()


def _commented(issue: str, body: str, author: str | None = PERSON) -> str:
    """Add a comment through the store's own verb; its id."""
    path = Path(plan_store.source_root(BOARD)).parent / f"comment-{time.monotonic_ns()}.md"
    path.write_text(body, encoding="utf-8")
    added = plan_store.sdk(
        plan_store.client().task_comment_add(issue, body_file=str(path), author=author)
    )
    path.unlink()
    return added.id.model_dump()


def _edited(issue: str, identifier: str, body: str) -> None:
    path = Path(plan_store.source_root(BOARD)).parent / f"edit-{time.monotonic_ns()}.md"
    path.write_text(body, encoding="utf-8")
    plan_store.sdk(plan_store.client().task_comment_edit(issue, identifier, body_file=str(path)))
    path.unlink()


def _moved(issue: str, category: str) -> None:
    plan_store.sdk(plan_store.client().task_status_set(issue, category))


def _next_second() -> None:
    """Wait until the store's whole-second clock has moved past every time written so far."""
    time.sleep(1.1)


#: A start before every comment a test writes, for the gatherings that name `--since`.
EARLY = "2026-01-01T00:00:00Z"


def _gathered(
    root: Path,
    capsys: pytest.CaptureFixture[str],
    *arguments: str,
    since: str | None = EARLY,
) -> tuple[str, comments.Plan | None]:
    """One `gather` over the stand-in board: its report, and the plan it wrote, if any."""
    plan = root.parent / f"plan-{time.monotonic_ns()}.json"
    status = comments.main(
        [
            "gather",
            "--root",
            str(root),
            "--plan",
            str(plan),
            "--to",
            BOARD,
            *(["--since", since] if since is not None else []),
            *arguments,
        ]
    )
    captured = capsys.readouterr()
    assert status == comments.DONE, captured.err
    written = plan.read_text(encoding="utf-8")
    return captured.out, comments.read_plan(plan, root) if written else None


def _written(
    root: Path, capsys: pytest.CaptureFixture[str], run: str = RUN, *, scoped: bool = True
) -> str:
    """The feedback file one gathering wrote for ``run``: a `--run` one unless not ``scoped``.

    A run whose ticket file is gone has no binding a `--run` gathering reads its item by, so a
    test that removes it gathers unscoped, which narrows by host instead.
    """
    _, plan = _gathered(root, capsys, *(["--run", run] if scoped else []))
    assert plan is not None
    ((named, path),) = [one for one in plan.runs if one.run == run]
    assert path.parent == root / comments.FEEDBACK_DIRECTORY / run, path
    return path.read_text(encoding="utf-8")


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(path): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _line(report: str, url_part: str) -> str:
    (line,) = [line for line in report.splitlines() if url_part in line]
    return line


def _id_line(report: str, issue: str, identifier: str) -> str:
    return _line(report, f"#comment-{identifier} on {issue}")


def test_each_persons_comment_goes_to_the_run_owning_its_issue_and_to_no_other(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    others = _filed(drafts_root, OTHER_RUN, SHARED_CAUSE)
    _next_second()
    marked = _commented(
        others, tickets.render_comment(RUN, SHARED_CAUSE, "This run's evidence."), None
    )
    _next_second()
    on_own = _commented(own, "The examples still miss page 9 — see ```listing.py```.\n")
    on_others = _commented(others, "Does this also hit the nightly sweep?\n")
    before = _snapshot(board)

    report, plan = _gathered(drafts_root, capsys)

    assert plan is not None
    files = dict(plan.runs)
    assert sorted(files) == sorted([RUN, OTHER_RUN])
    assert _id_line(report, own, on_own).endswith(f"goes to run {RUN}")
    assert _id_line(report, others, on_others).endswith(f"goes to run {OTHER_RUN}")
    assert _id_line(report, others, marked).endswith(f": marked by run {RUN}")
    own_file = files[tickets.RunId(RUN)].read_text(encoding="utf-8")
    assert own_file.count("### Comment ") == 1, own_file
    assert f"on `{own}`, this run's issue" in own_file
    # A body carrying a fence is quoted inside a longer one, so it reaches the task whole.
    assert "````text\nThe examples still miss page 9 — see ```listing.py```.\n````" in own_file
    assert "- URL: file://" in own_file and "#comment-" in own_file
    assert tickets.WITHDRAWAL_EXCEPTION in own_file
    # The preamble withdraws a proposal that is no longer relevant, never on an explicit
    # request alone, and never withdraws a deferred one.
    flat_own = " ".join(own_file.split())
    assert "is no longer relevant" in flat_own and "whether or not the comment says so" in flat_own
    assert "is never withdrawn" in flat_own and "clearly says" not in flat_own
    assert "perform whatever action it calls for, or none" not in flat_own
    others_file = files[tickets.RunId(OTHER_RUN)].read_text(encoding="utf-8")
    assert "Does this also hit the nightly sweep?" in others_file
    assert "page 9" not in others_file
    # The anchor `check-responses` reads each gathering's comments back out of.
    for text in (own_file, others_file):
        assert [one.issue for one in tickets.quoted_comments(text)] == re.findall(
            r"^### Comment \d+: on `([^`]+)`", text, re.MULTILINE
        ), text
    assert _snapshot(board) == before, "gathering feedback wrote to the board"


def _replied(issue: str, answers: str, cause: str = CAUSE, run: str = RUN) -> str:
    """``run``'s reply to one comment, posted through the store's own verb as the agent posts it."""
    listed = [
        comment.model_dump(mode="json")
        for comment in plan_store.sdk(plan_store.client().task_comment_list(issue)).comments
    ]
    (answered,) = [one for one in listed if one["id"] == answers]
    reply = tickets.render_reply(
        run,
        cause,
        answers=answers,
        url=f"{issue}#comment-{answers}",
        author=answered["author"],
        response="Copied the ticket again with that in its examples.",
        verdict=tickets.Verdict.DOES_NOT_CONFIRM,
    )
    return _commented(issue, reply, None)


def test_a_comment_any_runs_marker_owns_is_never_selected_whatever_its_kind(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    tickets.ticket_path(drafts_root, RUN, CAUSE).unlink()
    person = _commented(own, "The one person's comment.\n")
    for body in (
        tickets.render_comment(OTHER_RUN, CAUSE, "Another run's evidence."),
        tickets.render_reply(
            RUN,
            CAUSE,
            answers="a-comment-gone",
            url="u",
            author=None,
            response="Ours.",
            verdict=tickets.Verdict.DOES_NOT_CONFIRM,
        ),
    ):
        _commented(own, body, None)

    written = _written(drafts_root, capsys, scoped=False)

    assert written.count("### Comment ") == 1, written
    assert f"- Comment id: {person}\n" in written
    for absent in ("Another run's evidence.", "Ours."):
        assert absent not in written, written


def test_a_reply_of_any_run_answers_a_comment_until_a_person_edits_it_after_the_reply(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    _next_second()
    answered = _commented(own, "Answered by another run's reply.\n")
    unanswered = _commented(own, "Nobody replied to this one.\n", "a-maintainer")
    reply = _replied(own, answered, run=OTHER_RUN)

    report, plan = _gathered(drafts_root, capsys)

    assert _id_line(report, own, answered).endswith(f": answered by reply {reply}")
    assert _id_line(report, own, unanswered).endswith(f"goes to run {RUN}")
    assert plan is not None
    written = plan.runs[0][1].read_text(encoding="utf-8")
    assert written.count("### Comment ") == 1, written
    assert "Answered by another run's reply." not in written

    _next_second()
    _edited(own, answered, "Edited after the reply, so it is asked again.\n")

    edited = _written(drafts_root, capsys)

    assert "Edited after the reply, so it is asked again." in edited
    assert f"- Comment id: {answered}\n" in edited


def test_comments_on_items_no_run_here_answers_are_each_left_out_with_their_one_reason(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    decided = _filed(drafts_root, RUN, "a-decided-cause")
    deferred = _filed(drafts_root, RUN, "a-deferred-cause")
    elsewhere = _filed(drafts_root, RUN, "a-cause-seen-elsewhere", host="another-host")
    hostless = _filed(drafts_root, RUN, "a-cause-of-an-older-schema", host=None)
    gone = _filed(drafts_root, THIRD_RUN, "a-reaped-cause")
    dotted = _filed(drafts_root, "a.dotted.run", "a-dotted-cause")
    unowned = _filed(drafts_root, "no-run", "an-unowned-cause", record=False)
    for path in (drafts_root / "tasks" / THIRD_RUN).rglob("*"):
        if path.is_file():
            path.unlink()
    for path in sorted((drafts_root / "tasks" / THIRD_RUN).rglob("*"), reverse=True):
        path.rmdir()
    (drafts_root / "tasks" / THIRD_RUN).rmdir()
    _moved(decided, tickets.Status.ACCEPTED.value)
    _moved(deferred, tickets.Status.DEFERRED.value)
    _next_second()
    on_decided = _commented(decided, "Moved on already.\n")
    on_deferred = _commented(deferred, "Still worth doing later?\n")
    on_elsewhere = _commented(elsewhere, "Seen on the other host.\n")
    on_hostless = _commented(hostless, "From before hosts were recorded.\n")
    on_gone = _commented(gone, "Nobody here holds this run.\n")
    on_unowned = _commented(unowned, "On an item no run filed.\n")
    on_dotted = _commented(dotted, "Filed by a run no launch takes.\n")
    by_app = _commented(deferred, "Automated note.\n", "dependabot[bot]")
    by_actions = _commented(deferred, "Workflow note.\n", "github-actions")

    report, plan = _gathered(drafts_root, capsys)

    status = plan_store.task_record(decided)["status"]
    assert isinstance(status, dict)
    assert _id_line(report, decided, on_decided).endswith(f": item at {status['name']}")
    assert _id_line(report, deferred, on_deferred).endswith(f"goes to run {RUN}")
    assert _id_line(report, gone, on_gone).endswith(f": no records for owning run {THIRD_RUN} here")
    assert _id_line(report, dotted, on_dotted).endswith(
        ": owning run a.dotted.run is not a run id `just follow-ups` launches"
    )
    assert _id_line(report, deferred, by_app).endswith(": bot author dependabot[bot]")
    assert _id_line(report, deferred, by_actions).endswith(": bot author github-actions")
    # The store narrowed the query to this host's items, so an item verified elsewhere, one
    # recording no host and one carrying no record were never read at all.
    for absent in (elsewhere, hostless, unowned):
        assert absent not in report, report
    assert plan is not None
    assert [run for run, _ in plan.runs] == [RUN]

    # An item read by id is not narrowed by host, so the reasons for those three still hold
    # wherever one is read: in their order, each before every later reason.
    read = {
        item: comments.bound_issue(comments.QualifiedTaskId(item), WRITABLE_PLUGIN)
        for item in (elsewhere, hostless, unowned)
    }
    selection = comments.select(list(read.values()), {}, HOST, {tickets.RunId(RUN)})
    assert selection.chosen == {}
    reasons = {(str(left.issue.id), left.id): left.reason for left in selection.left}
    assert reasons == {
        (elsewhere, on_elsewhere): "ticket verified on host another-host",
        (hostless, on_hostless): "ticket verified on host (none recorded)",
        (unowned, on_unowned): (
            "the no-answer rule: the item carries no follow-up record, so no run answers it"
        ),
    }


def test_a_dry_run_reports_what_would_go_where_and_writes_nothing(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    tickets.ticket_path(drafts_root, RUN, CAUSE).unlink()
    asked = _commented(own, "Please add page 9.\n")
    before = _snapshot(drafts_root)

    report, plan = _gathered(drafts_root, capsys, "--dry-run")

    assert plan is None
    assert _id_line(report, own, asked).endswith(f"would go to run {RUN}")
    assert _snapshot(drafts_root) == before, "a dry run wrote under the drafts root"
    assert report.splitlines()[-1] == (
        f"plan store: {comments.store_version()}; the narrowed query returned 1 item(s) "
        f"commented on since {EARLY}"
    )


def test_a_run_scoped_gathering_names_only_that_runs_comments(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    others = _filed(drafts_root, OTHER_RUN, SHARED_CAUSE)
    _next_second()
    _commented(own, "Ours.\n")
    _commented(others, "Theirs.\n")

    report, plan = _gathered(drafts_root, capsys, "--run", RUN)

    assert plan is not None and plan.scope == RUN
    assert [run for run, _ in plan.runs] == [RUN]
    assert own in report and others not in report


def test_a_run_scoped_gathering_reads_an_item_another_run_owns_and_names_none_of_its_comments(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A `--run` ticket bound to another run's item reads that item, and neither the comment
    selected for its owner nor the one left out is reported or launched under this scope."""
    own = _filed(drafts_root, RUN, CAUSE)
    others = _filed(drafts_root, OTHER_RUN, SHARED_CAUSE)
    _bound(drafts_root, RUN, "bound-to-another-runs-item", _native_of(others))
    _next_second()
    marked = _commented(
        others, tickets.render_comment(RUN, SHARED_CAUSE, "This run's evidence."), None
    )
    _next_second()
    asked = _commented(own, "Ours.\n")
    theirs = _commented(others, "Theirs.\n")

    report, plan = _gathered(drafts_root, capsys, "--run", RUN)

    assert plan is not None and plan.scope == RUN
    assert [run for run, _ in plan.runs] == [RUN]
    assert f"read {others} and its comments directly" in report, report
    assert _id_line(report, own, asked).endswith(f"goes to run {RUN}")
    for unnamed in (theirs, marked):
        assert f"#comment-{unnamed} on {others}" not in report, report


def test_a_comment_written_after_the_first_gathering_is_selected_whatever_responses_follow_it(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The gap between a gathering and its replies: a later copy or reply hides nothing."""
    own = _filed(drafts_root, RUN, CAUSE)
    others = _filed(drafts_root, OTHER_RUN, SHARED_CAUSE)
    _next_second()
    asked = _commented(own, "The first question.\n")
    _next_second()
    first = _written(drafts_root, capsys)
    assert "The first question." in first
    _next_second()
    # Written while that gathering's dispatch works, before any of its responses.
    during = _commented(own, "Written while the dispatch worked.\n")
    _next_second()
    _filed(drafts_root, RUN, CAUSE, "Copied again, answering the first question.")
    evidence = _commented(
        others, tickets.render_comment(RUN, SHARED_CAUSE, "This run's evidence."), None
    )
    _edited(others, evidence, tickets.render_comment(RUN, SHARED_CAUSE, "Evidence, edited."))
    _replied(own, asked)
    before = _snapshot(board)

    second = _written(drafts_root, capsys)

    assert second.count("### Comment ") == 1, second
    assert "Written while the dispatch worked." in second
    assert f"- Comment id: {during}\n" in second
    assert "The first question." not in second
    assert _snapshot(board) == before, "gathering feedback wrote to the board"


def test_the_boundary_is_the_last_response_before_the_first_gathering_and_never_moves(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    _next_second()
    answered_by_copy = _commented(own, "Answered by the copy that follows.\n")
    _next_second()
    _filed(drafts_root, RUN, CAUSE, "Copied again, answering it.")
    written_at = tickets.ticket_path(drafts_root, RUN, CAUSE).stat().st_mtime
    # Recorded as the whole second comment times are, which orders every comment alike.
    boundary = datetime.fromtimestamp(written_at, UTC).replace(microsecond=0)
    _next_second()
    missed = _commented(own, "Gathered, and never replied to.\n")
    _next_second()
    report, plan = _gathered(drafts_root, capsys, "--run", RUN)
    assert plan is not None
    first = plan.runs[0][1].read_text(encoding="utf-8")
    assert "Gathered, and never replied to." in first
    assert "Answered by the copy that follows." not in first
    assert _id_line(report, own, answered_by_copy).endswith(
        f": at or before run {RUN}'s boundary {comments.instant(boundary)}"
    )
    _next_second()
    # Responses after the first gathering: a copy, and a feedback file of a later gathering.
    _filed(drafts_root, RUN, CAUSE, "Copied again after the first gathering.")
    directory = drafts_root / comments.FEEDBACK_DIRECTORY / RUN
    (directory / "notes.md").write_text("Not a gathering's file.\n", encoding="utf-8")
    (first_file,) = directory.glob("2*.md")
    recorded = comments.boundary_line(boundary)
    assert first_file.read_text(encoding="utf-8").splitlines()[0] == recorded
    stamp = datetime.strptime(first_file.stem, comments.STAMP_FORMAT).replace(tzinfo=UTC)
    assert comments.first_gathering(drafts_root, RUN) == (stamp, comments.Boundary(boundary))

    second = _written(drafts_root, capsys)

    assert f"- Comment id: {missed}\n" in second, "a copy after the first gathering hid it"
    assert "Answered by the copy that follows." not in second
    assert f", each changed after {comments.instant(boundary)}," in second
    assert len(list(directory.glob("2*.md"))) == 2
    assert comments.first_gathering(drafts_root, RUN) == (stamp, comments.Boundary(boundary))

    # The record is what decides, not a recomputation that happens to agree with it.
    text = first_file.read_text(encoding="utf-8")
    first_file.write_text(text.replace(recorded, comments.boundary_line(None)), encoding="utf-8")
    _next_second()

    third = _written(drafts_root, capsys)

    assert "Answered by the copy that follows." in third, "the recorded boundary was not read"
    assert third.splitlines()[0] == comments.boundary_line(None)


def test_a_computed_boundary_is_recorded_beside_the_runs_feedback_and_read_from_there(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A run with no comment selected still has the boundary it was computed recorded."""
    own = _filed(drafts_root, RUN, CAUSE)
    others = _filed(drafts_root, OTHER_RUN, SHARED_CAUSE)
    _next_second()
    _commented(own, "Answered by the evidence comment that follows.\n")
    _next_second()
    _commented(others, tickets.render_comment(RUN, SHARED_CAUSE, "This run's evidence."), None)
    (evidence,) = plan_store.sdk(plan_store.client().task_comment_list(others)).comments
    boundary = comments.moment(evidence.updated_at or evidence.created_at, "the evidence")
    path = comments.boundary_path(drafts_root, RUN)

    _gathered(drafts_root, capsys, "--dry-run")
    assert not path.exists(), "a dry run recorded a boundary"
    _gathered(drafts_root, capsys, "--run", OTHER_RUN)
    assert not path.exists(), "a gathering scoped to another run recorded this run's boundary"

    # Unscoped, because the evidence comment the boundary rests on is on another run's item,
    # which a gathering scoped to this run never reads.
    report, plan = _gathered(drafts_root, capsys)

    assert plan is not None and plan.runs == ()
    assert "nothing to answer: no comment was selected, so nothing was launched" in report
    assert path.read_text(encoding="utf-8") == comments.boundary_line(boundary) + "\n"
    # Read from there once it is recorded, whatever the responses since say.
    _next_second()
    _filed(drafts_root, RUN, CAUSE, "Copied again after the boundary was recorded.")
    assert comments.run_boundary(drafts_root, RUN, []) == (comments.Boundary(boundary), False)
    path.write_text("not a boundary\n", encoding="utf-8")
    with pytest.raises(OSError, match="records no boundary line"):
        comments.stored_boundary(drafts_root, RUN)


def test_an_earliest_feedback_file_recording_no_boundary_falls_back_to_the_computed_one(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A file written before files recorded a boundary: responses before its stamp decide."""
    own = _filed(drafts_root, RUN, CAUSE)
    others = _filed(drafts_root, OTHER_RUN, SHARED_CAUSE)
    _next_second()
    _commented(own, "Answered by the evidence comment that follows.\n")
    _next_second()
    evidence = _commented(
        others, tickets.render_comment(RUN, SHARED_CAUSE, "This run's evidence."), None
    )
    (comment,) = plan_store.sdk(plan_store.client().task_comment_list(others)).comments
    listed = comment.model_dump(mode="json")
    assert listed["id"] == evidence
    _next_second()
    missed = _commented(own, "Gathered by the old file, and never replied to.\n")
    _next_second()
    directory = drafts_root / comments.FEEDBACK_DIRECTORY / RUN
    directory.mkdir(parents=True)
    stamp = datetime.now(UTC).replace(microsecond=0)
    old = directory / f"{stamp.strftime(comments.STAMP_FORMAT)}.md"
    old.write_text("People commented on this run's follow-ups.\n", encoding="utf-8")
    assert comments.first_gathering(drafts_root, RUN) == (stamp, None)
    _next_second()
    # A copy after that gathering, which a recomputed boundary with no stamp would rest on.
    _filed(drafts_root, RUN, CAUSE, "Copied again after the old gathering.")

    # Unscoped, because the evidence comment the boundary rests on is on another run's item.
    written = _written(drafts_root, capsys, scoped=False)

    assert f"- Comment id: {missed}\n" in written
    assert "Answered by the evidence comment that follows." not in written
    boundary = comments.moment(listed["updated_at"], "the evidence comment")
    assert written.splitlines()[0] == comments.boundary_line(boundary)


def test_a_boundary_a_feedback_file_records_that_is_no_moment_is_unrunnable(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _commented(_filed(drafts_root, RUN, CAUSE), "A person's comment.\n")
    directory = drafts_root / comments.FEEDBACK_DIRECTORY / RUN
    directory.mkdir(parents=True)
    (directory / "20260101T000000Z.md").write_text(
        comments.FEEDBACK_BOUNDARY.format(boundary="yesterday") + "\n", encoding="utf-8"
    )
    (directory / "20260101T000000Z-2.md").write_text(
        comments.boundary_line(None) + "\n", encoding="utf-8"
    )

    gather = ["gather", "--root", str(drafts_root), "--plan", str(drafts_root.parent / "p.json")]
    status = comments.main([*gather, "--to", BOARD, "--since", EARLY])

    assert status == comments.UNRUNNABLE
    assert "20260101T000000Z.md records reports 'yesterday'" in capsys.readouterr().err
    (directory / "20261399T000000Z.md").write_text("", encoding="utf-8")
    assert comments.main([*gather, "--to", BOARD, "--since", EARLY]) == comments.UNRUNNABLE
    assert "20261399T000000Z.md is named for no moment" in capsys.readouterr().err


def test_the_feedback_file_asks_for_an_action_a_reply_and_a_report_per_comment(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    tickets.ticket_path(drafts_root, RUN, CAUSE).unlink()
    identifier = _commented(own, "Please add page 9.\n")

    written = _written(drafts_root, capsys, scoped=False)
    flat = " ".join(written.split())

    (comment,) = plan_store.sdk(plan_store.client().task_comment_list(own)).comments
    listed = comment.model_dump(mode="json")
    assert f"- Comment id: {identifier}\n- URL: file://" in written
    assert f"- Author: {PERSON}\n- Last changed: {listed['updated_at']}\n" in written
    for said in (
        "Gathered by `just follow-ups-answer-comments`",
        "each is quoted verbatim below with its id, its URL, its author and when it last changed",
        '1. **Act on it** under "Ownership on the board" above: decide whether the ticket of '
        "the issue it sits on should change in light of it and of anything investigating it "
        "found, whether or not it asks for an edit, make that change, and perform whatever else "
        "it calls for.",
        "2. **Post its one reply**, naming the comment's id, as those rules state, and saying "
        "what changed in the ticket and why, or why the ticket stands as it is.",
        "3. **Report** the comment's URL beside what you did about it, or why you did nothing.",
        " ".join(tickets.WITHDRAWAL_EXCEPTION.split())
        + " Never touch a ticket or an issue no comment above names",
    ):
        assert said in flat, said
    # The anchor sits with the comment it names, so the account's order is the file's.
    assert tickets.quoted_comments(written) == [tickets.Quoted(own, identifier)], written
    assert written.index("1. **Act on it**") < written.index("2. **Post its one reply**")
    # A run with no response has no boundary, and an unnamed author is said to be one.
    assert "changed after" not in written


def test_a_comment_before_the_runs_last_ticket_copy_is_not_selected_and_one_after_is(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    _next_second()
    _commented(own, "Answered by the copy that follows.\n")
    _next_second()
    _filed(drafts_root, RUN, CAUSE, "What the ticket says, answering the comment.")
    _next_second()
    _commented(own, "A new question after that copy.\n", None)

    written = _written(drafts_root, capsys)

    assert "A new question after that copy." in written
    assert "Answered by the copy that follows." not in written
    assert ", each changed after " in written
    assert f"- Author: {comments.UNKNOWN_AUTHOR}" in written


def test_two_feedback_files_in_one_second_are_both_kept(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)

    first = comments.write_feedback(tmp_path, RUN, BOARD, [], None, now)
    second = comments.write_feedback(tmp_path, RUN, BOARD, [], None, now)

    assert (first.name, second.name) == ("20260101T000000Z.md", "20260101T000000Z-2.md")


def test_with_no_watermark_the_start_is_the_earliest_bound_local_records_give(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _filed(drafts_root, RUN, CAUSE)
    _next_second()
    _filed(drafts_root, OTHER_RUN, SHARED_CAUSE)
    written = tickets.ticket_path(drafts_root, RUN, CAUSE).stat().st_mtime
    earliest = datetime.fromtimestamp(written, UTC).replace(microsecond=0)
    # A run with only drafts owns no issue, so it bounds nothing.
    (drafts_root / "tasks" / THIRD_RUN / "drafts").mkdir(parents=True)

    report, plan = _gathered(drafts_root, capsys, since=None)

    assert plan is not None and plan.since == earliest
    assert report.splitlines()[0] == (
        f"read {BOARD!r} for comments since {comments.instant(earliest)} (derived from local "
        f"records: run {RUN}'s ticket files' last write)"
    )
    # A recorded boundary is a run's bound before its ticket files are.
    recorded = earliest - timedelta(days=1)
    path = comments.boundary_path(drafts_root, OTHER_RUN)
    path.parent.mkdir(parents=True)
    path.write_text(comments.boundary_line(recorded) + "\n", encoding="utf-8")
    assert comments.derived_start(drafts_root) == comments.Start(
        recorded, f"derived from local records: run {OTHER_RUN}'s recorded boundary"
    )


def test_a_run_whose_records_give_no_bound_is_refused_naming_it_and_since(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _filed(drafts_root, RUN, CAUSE)
    tickets.ticket_path(drafts_root, RUN, CAUSE).unlink()
    plan = drafts_root.parent / "plan.json"

    status = comments.main(
        ["gather", "--root", str(drafts_root), "--plan", str(plan), "--to", BOARD]
    )

    refusal = capsys.readouterr().err
    assert status == comments.UNRUNNABLE
    assert f"run(s) {RUN} under {drafts_root}" in refusal
    assert "run it again with --since <RFC3339>" in refusal
    assert not plan.exists()


def test_a_host_where_no_run_filed_a_ticket_reads_nothing(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report, plan = _gathered(drafts_root, capsys, since=None)

    assert plan is None
    assert "no run under" in report and "nothing was read and nothing was launched" in report


def test_a_watermark_is_read_back_as_written_and_any_other_shape_is_refused(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    queried = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    assert comments.read_watermark(drafts_root, BOARD) is None

    since = comments.write_watermark(drafts_root, BOARD, queried)

    assert since == queried - comments.OVERLAP
    assert comments.read_watermark(drafts_root, BOARD) == since
    path = comments.watermark_path(drafts_root, BOARD)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema": comments.WATERMARK_SCHEMA,
        "board": BOARD,
        "since": comments.instant(since),
        "queried_at": comments.instant(queried),
    }
    _filed(drafts_root, RUN, CAUSE)
    report, plan = _gathered(drafts_root, capsys, since=None)
    assert plan is not None and plan.since == since
    assert f"(the watermark {path})" in report.splitlines()[0]

    wrong_since = {
        "schema": comments.WATERMARK_SCHEMA,
        "board": BOARD,
        "since": 1,
        "queried_at": comments.instant(queried),
    }
    wrong_query = wrong_since | {"since": comments.instant(since), "queried_at": "noon"}
    boolean_schema = wrong_query | {"schema": True, "queried_at": comments.instant(queried)}
    reversed_range = wrong_query | {
        "since": comments.instant(queried),
        "queried_at": comments.instant(since),
    }
    for held in (
        "not json",
        json.dumps({"schema": 0}),
        json.dumps([1]),
        json.dumps(wrong_since),
        json.dumps(wrong_query),
        json.dumps(boolean_schema),
        json.dumps(reversed_range),
    ):
        path.write_text(held, encoding="utf-8")
        with pytest.raises(OSError, match="the watermark"):
            comments.read_watermark(drafts_root, BOARD)


def _answered_run(
    root: Path, capsys: pytest.CaptureFixture[str], *, reply: bool
) -> tuple[comments.Plan, Path, str]:
    """A gathering of one comment on the run's issue, and its account, answered or not."""
    own = _filed(root, RUN, CAUSE)
    tickets.ticket_path(root, RUN, CAUSE).unlink()
    asked = _commented(own, "Please add page 9.\n")
    _, plan = _gathered(root, capsys)
    assert plan is not None
    ((_, feedback),) = plan.runs
    posted = _replied(own, asked) if reply else "a-reply-never-posted"
    tickets.responses_path(feedback).write_text(
        json.dumps(
            {
                "schema": tickets.RESPONSES_SCHEMA,
                "run": RUN,
                "feedback": feedback.name,
                "responses": [
                    {
                        "comment": asked,
                        "issue": own,
                        "action": "Copied the ticket again.",
                        "reply": posted,
                        "verdict": tickets.Verdict.DOES_NOT_CONFIRM.value,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return plan, feedback, posted


def test_a_settled_run_answered_soundly_reports_its_replies_and_advances_the_watermark(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, _, posted = _answered_run(drafts_root, capsys, reply=True)
    lines: list[str] = []

    status = comments.settle(drafts_root, plan, {RUN: "follow-up-1"}, say=lines.append)

    assert status == comments.DONE, lines
    assert lines[0] == f"run {RUN} (follow-up run follow-up-1): check-responses passed"
    assert lines[1].startswith("  reply: file://") and lines[1].endswith(f"#comment-{posted}")
    assert comments.read_watermark(drafts_root, BOARD) == plan.queried_at - comments.OVERLAP
    assert lines[-1].startswith(f"plan store: {plan.store}; the narrowed query returned 1 ")


def test_a_run_not_answered_soundly_is_named_and_leaves_the_watermark_where_it_was(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, feedback, _ = _answered_run(drafts_root, capsys, reply=False)
    lines: list[str] = []

    status = comments.settle(drafts_root, plan, {RUN: "follow-up-1"}, say=lines.append)

    assert status == comments.UNANSWERED
    assert lines[0] == f"run {RUN} (follow-up run follow-up-1): check-responses failed"
    assert any("the board holds no reply of run" in line for line in lines), lines
    assert "watermark: left as it was, because a launched run was not answered soundly" in lines
    assert comments.read_watermark(drafts_root, BOARD) is None
    # A gathering that cannot be read at all is one more way it is not answered.
    feedback.unlink()
    lines.clear()
    assert comments.settle(drafts_root, plan, {RUN: "x"}, say=lines.append) == (comments.UNANSWERED)
    # A run whose launch failed is unanswered too, and says which file it owed.
    lines.clear()
    assert comments.settle(drafts_root, plan, {}, say=lines.append) == comments.UNANSWERED
    assert lines[0] == f"run {RUN}: its follow-up run did not launch, so {feedback} is unanswered"


def test_detached_and_run_scoped_settlements_leave_the_watermark_and_say_why(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, feedback, _ = _answered_run(drafts_root, capsys, reply=True)
    lines: list[str] = []

    detached = dataclasses.replace(plan, detach=True)
    assert comments.settle(drafts_root, detached, {RUN: "fu-1"}, say=lines.append) == comments.DONE

    check = shlex.join(
        [sys.executable, "-m", "orchestrator.follow_up_tickets", "check-responses"]
        + ["--board", BOARD, "--feedback", str(feedback), RUN]
    )
    assert lines[0] == (
        f"run {RUN}: launched follow-up run fu-1; watch it with: just watch fu-1; once it "
        f"settles, check it with: {check}"
    )
    assert "watermark: left as it was, because the launched runs have not been checked" in lines
    lines.clear()
    scoped = dataclasses.replace(plan, scope=tickets.RunId(RUN))
    assert comments.settle(drafts_root, scoped, {RUN: "fu-1"}, say=lines.append) == comments.DONE
    assert (
        f"watermark: left as it was, because the gathering was scoped to run {RUN}'s comments"
        in lines
    )
    assert comments.read_watermark(drafts_root, BOARD) is None


def test_the_script_steps_hand_the_plan_between_them(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`launches` names each launch as the script reads it, and `settle` reads the plan back."""
    plan_path = drafts_root.parent / "plan.json"
    _commented(_filed(drafts_root, RUN, CAUSE), "Please add page 9.\n")
    tickets.ticket_path(drafts_root, RUN, CAUSE).unlink()
    for detach, mode in ((False, "attach"), (True, "detach")):
        assert (
            comments.main(
                ["gather", "--root", str(drafts_root), "--plan", str(plan_path), "--to", BOARD]
                + ["--since", EARLY, *(["--detach"] if detach else [])]
            )
            == comments.DONE
        )
        capsys.readouterr()
        assert (
            comments.main(["launches", "--root", str(drafts_root), "--plan", str(plan_path)])
            == comments.DONE
        )
        (line,) = capsys.readouterr().out.splitlines()
        run, feedback, said, board_word = line.split("\t")
        assert (run, said, board_word) == (RUN, mode, f"--to={BOARD}")
        assert Path(feedback).is_file()

    status = comments.main(
        ["settle", "--root", str(drafts_root), "--plan", str(plan_path), "--launched", f"{RUN}=fu"]
    )

    assert status == comments.DONE
    assert "launched follow-up run fu" in capsys.readouterr().out
    # A dry run hands the script an empty plan, which both later steps read as nothing to do.
    plan_path.write_text("", encoding="utf-8")
    assert (
        comments.main(["launches", "--root", str(drafts_root), "--plan", str(plan_path)])
        == comments.DONE
    )
    assert comments.main(["settle", "--root", str(drafts_root), "--plan", str(plan_path)]) == (
        comments.DONE
    )
    assert capsys.readouterr().out == ""


def test_a_watermark_that_cannot_be_written_is_refused_by_settle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "a-file-not-a-root"
    root.write_text("", encoding="utf-8")
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "board": BOARD,
                "boards": [BOARD],
                "named_board": True,
                "since": EARLY,
                "queried_at": EARLY,
                "scope": None,
                "detach": False,
                "store": "onetaskgraph test",
                "items": 0,
                "runs": [],
                "issues": {},
            }
        ),
        encoding="utf-8",
    )

    status = comments.main(["settle", "--root", str(root), "--plan", str(plan)])

    assert status == comments.UNRUNNABLE
    refused = capsys.readouterr().err
    assert refused.startswith(f"{comments.PROG}: refused: ")
    assert "is not a plan `gather` wrote" not in refused, refused


def test_an_invocation_that_cannot_run_is_its_own_status(
    drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = str(drafts_root.parent / "plan.json")
    gather = ["gather", "--root", str(drafts_root), "--plan", plan]
    assert comments.main([*gather, "--run", "a/b"]) == comments.UNRUNNABLE
    assert "--run 'a/b' is not a run id" in capsys.readouterr().err
    assert comments.main([*gather, "--since", "yesterday"]) == comments.UNRUNNABLE
    assert "--since reports 'yesterday'" in capsys.readouterr().err
    # A board no source configures is the store's refusal, and nothing is launched.
    _filed_without_board = tickets.ticket_path(drafts_root, RUN, CAUSE)
    _filed_without_board.parent.mkdir(parents=True)
    _filed_without_board.write_text("x", encoding="utf-8")
    assert comments.main([*gather, "--to", "no-such-board"]) == comments.UNRUNNABLE
    assert "Check that --to names a source" in capsys.readouterr().err
    with pytest.raises(SystemExit) as refused:
        comments.main(["gather"])
    assert refused.value.code == comments.UNRUNNABLE


def test_naming_no_board_reads_the_followups_board(
    drafts_root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default is `followups`, which no test may read, so the source that refused is named."""
    monkeypatch.delenv("GH_PROJECTS_TOKEN", raising=False)
    # The store falls back to its machine-wide secrets file for a name the environment
    # lacks, and a host that keeps the board's token there would read the board; naming
    # a file that does not exist keeps this host's token out of the verdict.
    monkeypatch.setenv("ONETASKGRAPH_SECRETS_FILE", str(drafts_root.parent / "no-secrets.env"))
    ticket = tickets.ticket_path(drafts_root, RUN, CAUSE)
    ticket.parent.mkdir(parents=True)
    ticket.write_text("x", encoding="utf-8")

    status = comments.main(
        ["gather", "--root", str(drafts_root), "--plan", str(drafts_root.parent / "p.json")]
    )

    assert status == comments.UNRUNNABLE
    assert tickets.BOARD in capsys.readouterr().err


def test_a_plan_or_a_launch_the_script_did_not_write_is_refused_naming_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The steps read only what the one before wrote; anything else is refused by name."""
    plan = tmp_path / "plan.json"
    root = str(tmp_path / "root")
    written = {
        "board": BOARD,
        "boards": [BOARD],
        "named_board": True,
        "since": EARLY,
        "queried_at": EARLY,
        "scope": None,
        "detach": False,
        "store": "onetaskgraph test",
        "items": 0,
        "runs": [],
        "issues": {},
    }
    for broken in (
        "not json",
        json.dumps({key: value for key, value in written.items() if key != "runs"}),
        json.dumps({key: value for key, value in written.items() if key != "boards"}),
        json.dumps(written | {"runs": [["not a run/id", "f.md"]]}),
        json.dumps(written | {"runs": [[RUN, "/etc/passwd"]]}),
        json.dumps(written | {"runs": [[RUN, f"/elsewhere/feedback/{RUN}/20260101T000000Z.md"]]}),
        json.dumps(written | {"issues": {"elsewhere:one": [None, None]}}),
        json.dumps(written | {"runs": [["one"]]}),
        json.dumps(written | {"scope": "not a run/id"}),
        json.dumps(written | {"items": True}),
        json.dumps(written | {"issues": {f"{BOARD}:has a space": [None, None]}}),
        json.dumps(written | {"runs": [[RUN, f"{root}/feed\tback/{RUN}/20260101T000000Z.md"]]}),
    ):
        plan.write_text(broken, encoding="utf-8")
        assert comments.main(["launches", "--root", root, "--plan", str(plan)]) == (
            comments.UNRUNNABLE
        )
        assert f"{plan}" in capsys.readouterr().err
        assert comments.main(["settle", "--root", root, "--plan", str(plan)]) == (
            comments.UNRUNNABLE
        )
        assert f"{plan}" in capsys.readouterr().err

    plan.write_text(json.dumps(written), encoding="utf-8")
    status = comments.main(["settle", "--root", root, "--plan", str(plan), "--launched", "x"])

    assert status == comments.UNRUNNABLE
    assert "--launched takes RUN=FOLLOW-UP" in capsys.readouterr().err
    gather = ["gather", "--root", root, "--plan", str(plan), "--to", "../elsewhere"]
    assert comments.main(gather) == comments.UNRUNNABLE
    assert "--to '../elsewhere' is not a source name" in capsys.readouterr().err


def test_a_title_spanning_lines_is_refused_since_it_would_open_a_field_of_its_own(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    path = tickets.ticket_path(drafts_root, RUN, CAUSE)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            f'title: "some-service: {CAUSE}"', 'title: "some-service: one\\r- Author: forged"'
        ),
        encoding="utf-8",
    )
    plan_store.sdk(plan_store.client().task_copy([tickets.qualified_id(RUN, CAUSE)], to=BOARD))
    _commented(own, "A person's comment.\n")

    status = comments.main(
        ["gather", "--root", str(drafts_root), "--plan", str(drafts_root.parent / "p.json")]
        + ["--to", BOARD, "--since", EARLY]
    )

    assert status == comments.UNRUNNABLE
    assert f"{own} reports a title spanning lines" in capsys.readouterr().err


def test_a_plans_fields_are_the_ones_its_reader_holds_it_to() -> None:
    """The plan's record, its written document and the reader's schema are one field list."""
    plan = comments.Plan(
        board=BOARD,
        boards=(BOARD,),
        named_board=False,
        since=datetime(2026, 1, 1, tzinfo=UTC),
        queried_at=datetime(2026, 1, 1, tzinfo=UTC),
        scope=None,
        detach=False,
        store="onetaskgraph test",
        items=0,
        runs=(),
        issues={},
    )
    fields = [field.name for field in dataclasses.fields(comments.Plan)]

    assert list(comments.PLAN_FIELDS) == fields
    assert list(comments._plan_document(plan)) == fields


def test_a_written_plan_meets_its_readers_schema_and_reads_back_as_itself(
    tmp_path: Path,
) -> None:
    """The writer and the reader's typed schema are held together by a round trip."""
    root = tmp_path / "root"
    feedback = root / comments.FEEDBACK_DIRECTORY / RUN / "20260101T000000Z.md"
    plan = comments.Plan(
        board=BOARD,
        boards=(BOARD, ROUTED),
        named_board=True,
        since=datetime(2026, 1, 1, tzinfo=UTC),
        queried_at=datetime(2026, 1, 1, 0, 15, tzinfo=UTC),
        scope=tickets.RunId(RUN),
        detach=True,
        store="onetaskgraph test",
        items=3,
        runs=(comments.Launch(tickets.RunId(RUN), feedback),),
        issues={
            f"{BOARD}:{RUN}/tickets/{CAUSE}": comments.IssueLink(
                "https://example.invalid/issue", "/tmp/issue.md"
            ),
            f"{ROUTED}:{RUN}/tickets/{ROUTED_CAUSE}": comments.IssueLink(LINEAR_ISSUE_URL, None),
        },
    )
    written = json.loads(json.dumps(comments._plan_document(plan)))
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(written), encoding="utf-8")

    for key, kind in comments.PLAN_FIELDS.items():
        assert isinstance(written[key], kind), (key, written[key])
    assert comments.read_plan(path, root) == plan
    for link in (["not a url", None], [None, "relative/issue.md"]):
        path.write_text(
            json.dumps(written | {"issues": {f"{BOARD}:{RUN}/tickets/{CAUSE}": link}}),
            encoding="utf-8",
        )
        with pytest.raises(OSError, match="is not a plan `gather` wrote"):
            comments.read_plan(path, root)
    # The boards are the family the root names first, each one source name, and every quoted
    # issue is on one of them.
    for boards in ([], [ROUTED, BOARD], [BOARD, "not one word"], [BOARD, 3]):
        path.write_text(json.dumps(written | {"boards": boards}), encoding="utf-8")
        with pytest.raises(OSError, match="is not a plan `gather` wrote"):
            comments.read_plan(path, root)
    path.write_text(json.dumps(written | {"boards": [BOARD]}), encoding="utf-8")
    with pytest.raises(OSError, match="is not a plan `gather` wrote"):
        comments.read_plan(path, root)
    # The store label is printed as the report's last line, so it is held to one.
    path.write_text(json.dumps(written | {"store": "onetaskgraph 1\nforged"}), encoding="utf-8")
    with pytest.raises(OSError, match="is not a plan `gather` wrote"):
        comments.read_plan(path, root)


@pytest.fixture
def routed(board: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A second local store the stand-in board routes every `petsinc` repository to."""
    root = tmp_path / "routed"
    root.mkdir()
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{ROUTED.upper()}__PLUGIN", WRITABLE_PLUGIN)
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{ROUTED.upper()}__CONFIG__ROOT", str(root))
    monkeypatch.setenv(
        f"ONETASKGRAPH_SOURCES__{BOARD.upper()}__ROUTES__0__REPOSITORIES", "github.com/petsinc/*"
    )
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{BOARD.upper()}__ROUTES__0__TO", ROUTED)
    return root


def test_a_gathering_reads_every_board_of_the_family_each_from_its_own_watermark(
    drafts_root: Path, routed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One gathering answers comments on the root board and on the board it routes to.

    The root board has a watermark and the routed one has none yet, so each is read from its
    own start; a sound settlement then gives each board a watermark of its own.
    """
    own = _filed(drafts_root, RUN, CAUSE)
    routed_issue = _filed(drafts_root, RUN, ROUTED_CAUSE, repository=ROUTED_REPOSITORY)
    assert routed_issue.startswith(f"{ROUTED}:"), "the store did not route the petsinc ticket"
    held = comments.write_watermark(drafts_root, BOARD, datetime(2026, 1, 1, tzinfo=UTC))
    _next_second()
    on_own = _commented(own, "Please add page 9.\n")
    on_routed = _commented(routed_issue, "Does the intake form still drop it?\n")

    report, plan = _gathered(drafts_root, capsys, since=None)

    lines = report.splitlines()
    assert lines[0] == (
        f"read {BOARD!r} for comments since {comments.instant(held)} "
        f"(the watermark {comments.watermark_path(drafts_root, BOARD)})"
    )
    assert lines[1].startswith(f"read {ROUTED!r} for comments since ")
    assert lines[1].endswith(f"(derived from local records: run {RUN}'s ticket files' last write)")
    assert _id_line(report, own, on_own).endswith(f"goes to run {RUN}")
    assert _id_line(report, routed_issue, on_routed).endswith(f"goes to run {RUN}")
    assert plan is not None and plan.boards == (BOARD, ROUTED)
    ((_, feedback),) = plan.runs
    quoted = tickets.quoted_comments(feedback.read_text(encoding="utf-8"))
    assert [(one.issue, one.comment) for one in quoted] == [
        (own, on_own),
        (routed_issue, on_routed),
    ]
    replies = [_replied(own, on_own), _replied(routed_issue, on_routed, cause=ROUTED_CAUSE)]
    tickets.responses_path(feedback).write_text(
        json.dumps(
            {
                "schema": tickets.RESPONSES_SCHEMA,
                "run": RUN,
                "feedback": feedback.name,
                "responses": [
                    {
                        "comment": one.comment,
                        "issue": one.issue,
                        "action": "Answered it.",
                        "reply": reply,
                        "verdict": tickets.Verdict.DOES_NOT_CONFIRM.value,
                    }
                    for one, reply in zip(quoted, replies, strict=True)
                ],
            }
        ),
        encoding="utf-8",
    )
    said: list[str] = []

    assert comments.settle(drafts_root, plan, {RUN: "follow-up-1"}, say=said.append) == (
        comments.DONE
    ), said
    for named in (BOARD, ROUTED):
        assert comments.read_watermark(drafts_root, named) == (
            plan.queried_at - comments.OVERLAP
        ), named
    assert sum(line.startswith("watermark: advanced to") for line in said) == 2, said


#: Two comments on one Linear issue as onetaskgraph-linear's recorded fixture
#: (`tests/fixtures/comments.json`) answers them: one a person wrote, which names its author,
#: and one an integration wrote, which Linear names no user for. Each carries the URL Linear
#: reports for a comment.
LINEAR_PERSON = comments.Comment(
    comments.CommentId("c2"),
    "ada",
    "Second, and the newer of this page.",
    "https://linear.app/acme/issue/ENG-1/fixture-issue#comment-c2",
    datetime(2026, 8, 3, 9, 30, tzinfo=UTC),
)
LINEAR_INTEGRATION = comments.Comment(
    comments.CommentId("c1"),
    None,
    "First, written by an integration.\n",
    "https://linear.app/acme/issue/ENG-1/fixture-issue#comment-c1",
    datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
)


def test_on_linear_a_comment_naming_no_user_is_an_integrations_and_a_named_one_a_persons() -> None:
    """Who counts as a bot follows the board's plugin, and each Linear URL is kept as reported.

    On GitHub an author the board does not report is still a person's, quoted as unknown; on
    Linear it is an integration's, left out. A Linear comment's own URL and its issue's are
    web URLs the gathering keeps verbatim, and a reply's URL is that comment URL.
    """
    assert comments.is_bot(None, comments.LINEAR_PLUGIN)
    assert not comments.is_bot("ada", comments.LINEAR_PLUGIN)
    assert not comments.is_bot("dependabot[bot]", comments.LINEAR_PLUGIN)
    assert comments.is_bot("dependabot[bot]", "github-projects")
    assert not comments.is_bot(None, "github-projects")
    assert not comments.is_bot(None)
    issue = comments.Issue(
        comments.QualifiedTaskId(f"{ROUTED}:ENG-1"),
        "hp-api: the intake form drops a field",
        tickets.RunId(RUN),
        None,
        comments._web_url(LINEAR_ISSUE_URL),
        (LINEAR_INTEGRATION, LINEAR_PERSON),
        host=HOST,
        plugin=comments.LINEAR_PLUGIN,
    )

    selection = comments.select([issue], {}, HOST, {tickets.RunId(RUN)})

    ((chosen,),) = selection.chosen.values()
    assert (chosen.id, chosen.author, chosen.url) == ("c2", "ada", LINEAR_PERSON.url)
    (left,) = selection.left
    assert (left.id, left.url, left.reason) == (
        "c1",
        LINEAR_INTEGRATION.url,
        f"bot author {comments.LINEAR_BOT}",
    )
    assert issue.url == LINEAR_ISSUE_URL
    assert comments.comment_url_parts(issue.url, None, "c2", LINEAR_PERSON.url, str(issue.id)) == (
        LINEAR_PERSON.url
    )
    assert tickets.ITEM_URL.fullmatch(LINEAR_PERSON.url or "")


def test_a_boards_configured_plugin_decides_whether_an_unattributed_comment_is_a_persons(
    drafts_root: Path,
    board: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unattributed comment is a person's on the stand-in board and an integration's on Linear.

    The gathering reads each board's plugin from the store's resolved configuration and hands
    it to the author rule. The stand-in is a `local-md` folder, which no configuration can make
    a Linear source, so the one answer naming its plugin is read as `linear` in the second
    gathering — every other setting, the listing and the comments are the store's own — and the
    same two comments are routed differently for that answer alone.
    """
    own = _filed(drafts_root, RUN, CAUSE)
    tickets.ticket_path(drafts_root, RUN, CAUSE).unlink()
    unattributed = _commented(own, "Synced from the intake integration.\n", None)
    person = _commented(own, "Please add page 9.\n")

    report, _ = _gathered(drafts_root, capsys, "--dry-run")

    assert _id_line(report, own, unattributed).endswith(f"would go to run {RUN}")
    assert _id_line(report, own, person).endswith(f"would go to run {RUN}")
    resolved = plan_store.configured_settings()
    assert resolved[f"sources.{BOARD}.plugin"] == WRITABLE_PLUGIN
    monkeypatch.setattr(
        plan_store,
        "configured_settings",
        lambda: {**resolved, f"sources.{BOARD}.plugin": comments.LINEAR_PLUGIN},
    )

    report, _ = _gathered(drafts_root, capsys, "--dry-run")

    assert _id_line(report, own, unattributed).endswith(f": bot author {comments.LINEAR_BOT}")
    assert _id_line(report, own, person).endswith(f"would go to run {RUN}")


# llmlint: ignore-block[budgets_reuse_gate_telemetry, budgets_scoped_to_minimal_tree] The plan that registered `follow-up-scoped-comment-reads` states its measurement source as direct and its command as this test; a store-request count is recorded by no gate's telemetry, and this test, its constants and its crowded board live with the module's other tests, as `orchestrator/budgets.yaml`'s other budgets' do.  # noqa: E501 - llmlint reads a directive's rule list off one line
#: How many unrelated items the crowded board holds, each commented since the watermark.
CROWD = 200
OTHER_HOST = "another-host"


class Request(NamedTuple):
    """One request made through the store client: the method, and what it was asked."""

    method: str
    arguments: tuple[object, ...]
    keywords: dict[str, object]

    def on_board(self) -> bool:
        """Whether it reads the stand-in board: an item or its comments by id, or a page."""
        if self.method == "task_list":
            return BOARD in (self.keywords.get("source") or [])
        return bool(self.arguments) and str(self.arguments[0]).startswith(f"{BOARD}:")


class _Counted:
    """The real store client, recording every request made through it before making it."""

    def __init__(self, real: object, requests: list[Request]):
        self._real = real
        self._requests = requests

    def __getattr__(self, name: str) -> object:
        method = getattr(self._real, name)

        def recorded(*arguments: object, **keywords: object) -> object:
            self._requests.append(Request(name, arguments, keywords))
            return method(*arguments, **keywords)

        return recorded


class BoardReads(NamedTuple):
    """The requests one pass made of the stand-in board, by what each read."""

    items: int
    comment_lists: int
    pages: int

    @property
    def total(self) -> int:
        return self.items + self.comment_lists + self.pages

    def __str__(self) -> str:
        return (
            f"{self.total} ({self.items} item read(s), {self.comment_lists} comment-list "
            f"read(s), {self.pages} listing page(s))"
        )


def _board_reads(requests: list[Request]) -> BoardReads:
    """Count the requests that read the stand-in board: an item, a comment list, or a page."""
    read = [request.method for request in requests if request.on_board()]
    return BoardReads(
        items=read.count("task_show"),
        comment_lists=read.count("task_comment_list"),
        pages=read.count("task_list"),
    )


@pytest.fixture
def counted(monkeypatch: pytest.MonkeyPatch) -> list[Request]:
    """Every request the module makes through its store client, recorded as it is made."""
    requests: list[Request] = []
    real = plan_store.client
    monkeypatch.setattr(plan_store, "client", lambda: _Counted(real(), requests))
    return requests


def _crowd(board: Path, at: datetime) -> None:
    """Put :data:`CROWD` unrelated items on the stand-in board, each carrying one comment.

    Half are another host's tickets and half carry no follow-up record, which is everything a
    board shared by several hosts holds that no run here owns. Written as the `local-md`
    store writes an item and its comment, so the store reads them as its own.
    """
    instant = at.strftime(comments.MOMENT_FORMAT)
    native = instant.replace("-", "").replace(":", "")
    directory = board / "tasks" / "crowd"
    directory.mkdir(parents=True)
    for number in range(CROWD):
        record = (
            f"  {tickets.KEY}:\n    created_by_run: crowd-run-{number}\n    host: {OTHER_HOST}\n"
            if number % 2
            else "  other: 1\n"
        )
        (directory / f"item-{number:03d}.md").write_text(
            "---\n"
            f"title: 'crowd: item {number}'\n"
            "status: backlog\n"
            "metadata:\n"
            f"{record}"
            "---\n"
            "An unrelated item.\n\n"
            "## Comments\n\n"
            f'<!-- onetaskgraph:comment id="{native}-{number}" '
            f'author="{PERSON}" created_at="{instant}" updated_at="{instant}" -->\n'
            f"### {PERSON} — {instant}\n\n"
            "A comment nobody here owes an answer to.\n\n\n"
            "<!-- /onetaskgraph:comment -->\n",
            encoding="utf-8",
        )


def _selected(report: str) -> list[str]:
    return [line for line in report.splitlines() if line.startswith("selected: ")]


def _crowded_passes(
    drafts_root: Path,
    board: Path,
    capsys: pytest.CaptureFixture[str],
    counted: list[Request],
) -> dict[str, BoardReads]:
    """What an unscoped, an `--issue` and a `--run` dry run each read of a crowded board.

    One issue a run here owns has one comment waiting, and :data:`CROWD` unrelated items were
    commented on since the watermark too; each pass selects that one comment.
    """
    own = _filed(drafts_root, RUN, CAUSE)
    _next_second()
    waiting = _commented(own, "Does this also hit page 9?\n")
    _crowd(board, datetime.now(UTC))
    comments.write_watermark(drafts_root, BOARD, datetime(2026, 1, 1, 0, 15, tzinfo=UTC))

    reads: dict[str, BoardReads] = {}
    selected: dict[str, list[str]] = {}
    for name, arguments in (
        ("unscoped", ()),
        ("--issue", ("--issue", own)),
        ("--run", ("--run", RUN)),
    ):
        counted.clear()
        report, plan = _gathered(drafts_root, capsys, "--dry-run", *arguments, since=None)
        assert plan is None
        reads[name] = _board_reads(counted)
        selected[name] = _selected(report)
    assert selected["unscoped"] == [
        f"selected: {_id_line(selected['unscoped'][0], own, waiting).removeprefix('selected: ')}"
    ]
    assert selected["--issue"] == selected["unscoped"] == selected["--run"], selected
    return reads


def test_a_pass_about_one_issue_reads_that_item_alone_however_crowded_the_board(
    drafts_root: Path,
    board: Path,
    capsys: pytest.CaptureFixture[str],
    counted: list[Request],
) -> None:
    """`--issue` and `--run` read one item and its comments; unscoped reads only owned items.

    A pass scoped to the one issue a run here owns, or to that run, selects the comment an
    unscoped pass selects, reads that one item and its comment list by id and lists no page;
    and the unscoped pass, narrowed by host at the store, reads that one item's comments alone.
    """
    reads = _crowded_passes(drafts_root, board, capsys, counted)
    for name in ("--issue", "--run"):
        assert reads[name] == BoardReads(items=1, comment_lists=1, pages=0), (
            name,
            str(reads[name]),
        )
    assert reads["unscoped"].comment_lists == 1, str(reads["unscoped"])
    assert reads["unscoped"].pages == 1, str(reads["unscoped"])


def test_scoped_comment_reads_cost_one_issues_reads_however_crowded_the_board(
    drafts_root: Path,
    board: Path,
    capsys: pytest.CaptureFixture[str],
    counted: list[Request],
) -> None:
    """Reports budget `follow-up-scoped-comment-reads`: what a pass about one issue reads.

    The figure is the larger of the `--issue` and `--run` passes' board requests over the
    crowded board, with each pass's breakdown and the unscoped pass's beside it; comparing it
    with the budget's threshold is onebudgetspec's alone.
    """
    reads = _crowded_passes(drafts_root, board, capsys, counted)
    scoped = max(reads["--issue"].total, reads["--run"].total)
    detail = (
        f"--issue: {reads['--issue']}; --run: {reads['--run']}; the unscoped pass over the "
        f"same board of {CROWD + 1} commented items: {reads['unscoped']}"
    )
    print(f"{scoped} board request(s); {detail}")
    if destination := os.environ.get("ONEBUDGETSPEC_RESULT"):
        Path(destination).write_text(
            json.dumps({"value": scoped, "detail": detail}), encoding="utf-8"
        )


# llmlint: ignore-end[budgets_reuse_gate_telemetry, budgets_scoped_to_minimal_tree]


def _bound(root: Path, run: str, cause: str, native: str | None) -> None:
    """Write ``run``'s ticket for ``cause`` bound to ``native`` on the board, copying nothing."""
    path = tickets.ticket_path(root, run, cause)
    path.parent.mkdir(parents=True, exist_ok=True)
    binding = "" if native is None else f"    {tickets.BINDING_FIELD}: {native}\n"
    path.write_text(
        "---\n"
        f'title: "some-service: {cause}"\n'
        'status: "backlog"\n'
        "metadata:\n"
        f"  {tickets.KEY}:\n    created_by_run: {run}\n{binding}    host: {HOST}\n"
        "---\n"
        "What the ticket says.\n",
        encoding="utf-8",
    )


def _native_of(issue: str) -> str:
    return issue.partition(":")[2]


def test_an_issue_scoped_gathering_sends_its_comments_to_the_run_bound_to_it(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    others = _filed(drafts_root, OTHER_RUN, SHARED_CAUSE)
    _next_second()
    asked = _commented(own, "Ours.\n")
    _commented(others, "Theirs.\n")

    report, plan = _gathered(drafts_root, capsys, "--issue", own)

    assert plan is not None and plan.scope == RUN
    assert [run for run, _ in plan.runs] == [RUN]
    assert list(plan.issues) == [own]
    assert _id_line(report, own, asked).endswith(f"goes to run {RUN}")
    assert others not in report
    assert (
        f"read {own} and its comments directly, the item run {RUN}'s ticket is bound to" in report
    )


def _refused(
    root: Path, capsys: pytest.CaptureFixture[str], *arguments: str, since: str | None = EARLY
) -> str:
    plan = root.parent / f"plan-{time.monotonic_ns()}.json"
    status = comments.main(
        ["gather", "--root", str(root), "--plan", str(plan), "--to", BOARD]
        + (["--since", since] if since is not None else [])
        + list(arguments)
    )
    captured = capsys.readouterr()
    assert status == comments.UNRUNNABLE, captured
    assert not plan.exists(), "a refused gathering wrote a plan to launch from"
    written = list((root / comments.FEEDBACK_DIRECTORY).glob("*/*.md"))
    assert written == [], "a refused gathering wrote feedback"
    return captured.err


def test_an_issue_no_run_of_this_host_owns_is_refused_naming_why_and_nothing_is_launched(
    drafts_root: Path,
    board: Path,
    capsys: pytest.CaptureFixture[str],
    counted: list[Request],
) -> None:
    _filed(drafts_root, RUN, CAUSE)
    stranger = _filed(drafts_root, THIRD_RUN, "a-strangers-cause")
    tickets.ticket_path(drafts_root, THIRD_RUN, "a-strangers-cause").unlink()
    _commented(stranger, "Nobody here holds this.\n")
    counted.clear()

    said = _refused(drafts_root, capsys, "--issue", stranger)

    assert f"--issue {stranger}: no run of this host owns it, because no ticket under " in said
    assert f"is bound to it (no record's `{tickets.BINDING_FIELD}` names it" in said
    assert said.rstrip().endswith("nothing was launched")
    assert _board_reads(counted).total == 0, "an issue no ticket here is bound to was read"

    said = _refused(drafts_root, capsys, "--issue", f"elsewhere:{_native_of(stranger)}")
    assert f"because it is no item of {BOARD!r} or a board it routes to ({BOARD})" in said
    said = _refused(drafts_root, capsys, "--issue", "not-qualified")
    assert "--issue 'not-qualified' is not a qualified id, <source>:<native-id>" in said
    with pytest.raises(SystemExit) as exited:
        _refused(drafts_root, capsys, "--issue", stranger, "--run", RUN)
    assert exited.value.code == comments.UNRUNNABLE
    assert "argument --run: not allowed with argument --issue" in capsys.readouterr().err

    # Bound here, and the board says another run created it, another host verified it, or
    # no run did at all: read once, then refused.
    theirs = _filed(drafts_root, OTHER_RUN, SHARED_CAUSE)
    tickets.ticket_path(drafts_root, OTHER_RUN, SHARED_CAUSE).unlink()
    _bound(drafts_root, RUN, "bound-to-another-runs-item", _native_of(theirs))
    said = _refused(drafts_root, capsys, "--issue", theirs)
    assert (
        f"the item names run {OTHER_RUN} as its creator, where run {RUN}'s ticket is bound to it"
    ) in said
    elsewhere = _filed(drafts_root, RUN, "a-cause-seen-elsewhere", host=OTHER_HOST)
    said = _refused(drafts_root, capsys, "--issue", elsewhere)
    assert f"its ticket was verified on host {OTHER_HOST}, not {HOST}" in said
    unrecorded = _filed(drafts_root, "no-run", "an-unowned-cause", record=False)
    _bound(drafts_root, RUN, "bound-to-an-unrecorded-item", _native_of(unrecorded))
    said = _refused(drafts_root, capsys, "--issue", unrecorded)
    assert "the item carries no follow-up record naming the run that created it" in said

    # Bound to an item the board no longer holds: refused by the store's own answer.
    _bound(drafts_root, RUN, "bound-to-a-deleted-item", "deleted/item")
    said = _refused(drafts_root, capsys, "--issue", f"{BOARD}:deleted/item")
    assert "refused: onetaskgraph: no task with that id" in said, said

    # Two runs' tickets bound to one item: no one run owns its comments.
    _bound(drafts_root, OTHER_RUN, "also-bound", _native_of(unrecorded))
    said = _refused(drafts_root, capsys, "--issue", unrecorded)
    assert f"the tickets of runs {OTHER_RUN}, {RUN} under {drafts_root} are all bound" in said


def test_a_run_scoped_gathering_says_which_of_its_tickets_is_bound_to_no_item(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    _bound(drafts_root, RUN, "never-copied", None)
    # A record outside the run's tickets directory carrying its creator is not a ticket.
    stray = drafts_root / "tasks" / RUN / "drafts" / "a-draft.md"
    stray.parent.mkdir(parents=True)
    stray.write_text(
        "---\ntitle: a draft\nstatus: draft\nmetadata:\n"
        f"  {tickets.KEY}:\n    created_by_run: {RUN}\n    {tickets.BINDING_FIELD}: nowhere\n"
        "---\nA draft.\n",
        encoding="utf-8",
    )
    _next_second()
    asked = _commented(own, "Ours.\n")

    report, plan = _gathered(drafts_root, capsys, "--run", RUN, "--dry-run")

    assert plan is None
    assert f"run {RUN}'s ticket never-copied is bound to no board item; nothing read" in report
    assert _id_line(report, own, asked).endswith(f"would go to run {RUN}")
    assert "nowhere" not in report
    assert report.splitlines()[-1].endswith(
        f"read 1 item(s) run {RUN}'s tickets are bound to, each directly, and listed none"
    ), report

    # A binding that is no native id is refused naming the ticket, before the board is read.
    for malformed in ('"two words"', "[a-list]"):
        _bound(drafts_root, RUN, "never-copied", "placeholder")
        path = tickets.ticket_path(drafts_root, RUN, "never-copied")
        path.write_text(
            path.read_text(encoding="utf-8").replace("placeholder", malformed), encoding="utf-8"
        )
        said = _refused(drafts_root, capsys, "--run", RUN)
        assert f"run {RUN}'s ticket never-copied records `{tickets.BINDING_FIELD}` " in said, said
        assert "which is not a board item's native id" in said, said


def test_an_item_read_by_id_that_the_store_answers_other_than_as_asked_is_refused(
    drafts_root: Path, board: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real store refuses a missing id itself; no item, two, or another item is refused here."""
    own = _filed(drafts_root, RUN, CAUSE)
    shown = plan_store.sdk(plan_store.client().task_show(own, no_comments=True))
    (item,) = shown.items
    other = item.model_copy(update={"id": item.id.__class__(f"{BOARD}:another/item")})
    for items, refusal in (
        ([], f"answered 0 items for {re.escape(own)}"),
        ([item, item], f"answered 2 items for {re.escape(own)}"),
        ([other], f"answered '{BOARD}:another/item' when asked for {re.escape(own)}"),
    ):
        answer = shown.model_copy(update={"items": items})

        def answered(
            awaitable: Coroutine[object, object, object], answer: object = answer
        ) -> object:
            awaitable.close()
            return answer

        monkeypatch.setattr(plan_store, "sdk", answered)
        with pytest.raises(OSError, match=refusal):
            comments.bound_issue(comments.QualifiedTaskId(own), WRITABLE_PLUGIN)


def test_scoped_gatherings_read_a_ticket_bound_on_the_board_its_repository_routes_to(
    drafts_root: Path, routed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A binding names an item of the board the ticket's repositories route it to, and a
    gathering scoped to its run or its issue reads it there, listing neither board."""
    routed_issue = _filed(drafts_root, RUN, ROUTED_CAUSE, repository=ROUTED_REPOSITORY)
    assert routed_issue.startswith(f"{ROUTED}:"), "the store did not route the petsinc ticket"
    _next_second()
    asked = _commented(routed_issue, "Does the intake form still drop it?\n")

    for scope in (("--run", RUN), ("--issue", routed_issue)):
        report, plan = _gathered(drafts_root, capsys, "--dry-run", *scope)

        assert plan is None
        assert _id_line(report, routed_issue, asked).endswith(f"would go to run {RUN}"), report
        assert f"read {routed_issue} and its comments directly" in report, report
        assert report.splitlines()[-1].endswith("each directly, and listed none"), report


def test_a_first_gathering_scoped_to_a_run_quotes_again_what_its_evidence_elsewhere_answered(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Scoped, a run's evidence comment on another run's item is never read, so the boundary
    its first gathering computes and records is the earlier one its own items give: a comment
    that evidence answered is quoted again, never dropped."""
    own = _filed(drafts_root, RUN, CAUSE)
    others = _filed(drafts_root, OTHER_RUN, SHARED_CAUSE)
    _next_second()
    answered = _commented(own, "Answered by the evidence comment that follows.\n")
    _next_second()
    _commented(others, tickets.render_comment(RUN, SHARED_CAUSE, "This run's evidence."), None)
    (evidence,) = plan_store.sdk(plan_store.client().task_comment_list(others)).comments
    evidenced = comments.moment(evidence.updated_at or evidence.created_at, "the evidence")

    written = _written(drafts_root, capsys)

    assert f"- Comment id: {answered}\n" in written
    recorded = comments.recorded_boundary(written, "the feedback file")
    assert recorded is not None and recorded.moment is not None
    assert recorded.moment < evidenced
    assert comments.stored_boundary(drafts_root, RUN) == recorded


def test_a_scoped_gathering_takes_no_start_so_no_watermark_or_unbounded_run_refuses_it(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--run` and `--issue` read their items whole: an unreadable watermark and a run whose
    records give no start, which refuse an unscoped pass, refuse neither."""
    own = _filed(drafts_root, RUN, CAUSE)
    _next_second()
    asked = _commented(own, "Ours.\n")
    watermark = comments.watermark_path(drafts_root, BOARD)
    watermark.parent.mkdir(parents=True)
    watermark.write_text("not a watermark", encoding="utf-8")
    (drafts_root / "tasks" / OTHER_RUN / tickets.TICKETS).mkdir(parents=True)

    assert "is not JSON" in _refused(drafts_root, capsys, "--dry-run", since=None)
    watermark.unlink()
    assert f"run(s) {OTHER_RUN} under" in _refused(drafts_root, capsys, "--dry-run", since=None)
    watermark.write_text("not a watermark", encoding="utf-8")
    for scope in (("--run", RUN), ("--issue", own)):
        report, plan = _gathered(drafts_root, capsys, "--dry-run", *scope, since=None)

        assert plan is None
        assert _id_line(report, own, asked).endswith(f"would go to run {RUN}"), report
        assert "for comments since" not in report, report
