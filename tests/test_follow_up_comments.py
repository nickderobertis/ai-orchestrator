"""Which board comments go to which run, the files they are written as, and the watermark.

`tests/plan_tooling/follow_ups/test_follow_ups_answer_comments_recipe_e2e.py` drives the
recipe through real launches. What is proven here is each of the module's three steps in
process, against the installed `onetaskgraph`: a drafts root and a second local store
standing in for the board, both named through the store's environment layer, tickets copied
and comments written through the store's own verbs. Only the malformed answers a real store
never gives are put in the store's place, the way `tests/test_plan_store.py` refuses a
listing it cannot account for.

Comment times are the store's own and whole seconds, so a test that needs one comment to be
older than a response waits for the clock to pass a second between them.
"""

from __future__ import annotations

import dataclasses
import json
import re
import shlex
import shutil
import socket
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple, get_type_hints

import follow_up_boundary
import follow_up_variables
import pytest

from orchestrator import follow_up_comments as comments
from orchestrator import follow_up_tickets as tickets
from orchestrator import plan_store
from orchestrator.plan_store import WRITABLE_PLUGIN, QualifiedTaskId

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
        QualifiedTaskId(f"{BOARD}:x"),
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
    follow_up_boundary.enable(monkeypatch, public=(BOARD,))
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
    # A failed outcome carries no destination, so reading it as absent fails the copy here.
    destination = getattr(copied.items[0].root, "destination", None)
    assert destination is not None, copied
    if record:
        # Bound to the item the copy reached, as `board-status` binds a ticket it copies.
        native = destination.model_dump().partition(":")[2]
        text = path.read_text(encoding="utf-8")
        opening = f"    created_by_run: {run}\n"
        binding = f"    {tickets.BINDING_FIELD}: {native}\n"
        path.write_text(text.replace(opening, opening + binding, 1), encoding="utf-8")
    return str(destination.model_dump())


def _commented(issue: str, body: str, author: str | None = PERSON) -> str:
    """Add a comment through the store's own verb; its id."""
    path = Path(plan_store.source_root(BOARD)).parent / f"comment-{time.monotonic_ns()}.md"
    path.write_text(body, encoding="utf-8")
    added = plan_store.sdk(
        plan_store.client().task_comment_add(issue, body_file=str(path), author=author)
    )
    path.unlink()
    return str(added.id.model_dump())


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


class Gathered(NamedTuple):
    """One pass the script's first two steps took: its snapshot, and the plan launched from it."""

    snapshot: comments.Snapshot
    plan: comments.Plan

    @property
    def runs(self) -> tuple[comments.Launch, ...]:
        return self.plan.runs

    @property
    def scope(self) -> str | None:
        return self.snapshot.scope

    @property
    def since(self) -> datetime:
        return min(one.since for one in self.snapshot.starts.values())


def _steps(root: Path, *arguments: str) -> tuple[int, Path, Path]:
    """`gather` with ``arguments``, then `launch` from what it gathered, as the script runs them."""
    intent = root.parent / f"intent-{time.monotonic_ns()}.json"
    plan = root.parent / f"plan-{time.monotonic_ns()}.json"
    status = comments.main(["gather", "--root", str(root), "--intent", str(intent), *arguments])
    if status == comments.DONE:
        status = comments.main(
            ["launch", "--root", str(root), "--intent", str(intent), "--plan", str(plan)]
        )
    return status, intent, plan


def _gathered(
    root: Path,
    capsys: pytest.CaptureFixture[str],
    *arguments: str,
    since: str | None = EARLY,
) -> tuple[str, Gathered | None]:
    """One `gather` and `launch` over the stand-in board: the report, and what was launched.

    ``None`` for a dry run or a host nothing can be owed on, which claim no snapshot.
    """
    status, intent, plan = _steps(
        root, "--to", BOARD, *(["--since", since] if since is not None else []), *arguments
    )
    captured = capsys.readouterr()
    assert status == comments.DONE, captured.err
    named = json.loads(intent.read_text(encoding="utf-8"))["snapshot"]
    if named is None:
        return captured.out, None
    launched = comments.read_plan(plan, root)
    return captured.out, Gathered(comments.read_snapshot(launched.snapshot), launched)


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


def _read_by_id(root: Path, items: list[str]) -> list[comments.Issue]:
    """``items`` copied by id into a snapshot source of their own and read back, as a scoped
    gathering reads them: through the store's own copy, with no host narrowing."""
    directory = root.parent / f"read-{time.monotonic_ns()}"
    (directory / comments.SNAPSHOT_ITEMS).mkdir(parents=True)
    settings = comments.snapshot_settings(directory, BOARD)
    copied = comments.copy_into_snapshot(directory, BOARD, items, settings)
    details = comments.shown_details([copied.held[one] for one in items], settings)
    return [
        comments.snapshot_issue(detail, one, copied.held[one], WRITABLE_PLUGIN)
        for one, detail in zip(items, details, strict=True)
    ]


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
    read = _read_by_id(drafts_root, [elsewhere, hostless, unowned])
    selection = comments.select(read, {}, HOST, {tickets.RunId(RUN)})
    assert selection.chosen == {}
    reasons = {(str(left.issue), left.id): left.reason for left in selection.left}
    assert reasons == {
        (elsewhere, on_elsewhere): "ticket verified on host another-host",
        (hostless, on_hostless): "ticket verified on host (none recorded)",
        (unowned, on_unowned): (
            "the no-answer rule: the item carries no follow-up record, so no run answers it"
        ),
    }


def test_a_dry_run_reports_what_would_go_where_and_writes_only_its_snapshot(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    tickets.ticket_path(drafts_root, RUN, CAUSE).unlink()
    asked = _commented(own, "Please add page 9.\n")
    before = _snapshot(drafts_root)

    report, plan = _gathered(drafts_root, capsys, "--dry-run")

    assert plan is None
    assert _id_line(report, own, asked).endswith(f"would go to run {RUN}")
    # The snapshot is the one thing it writes: no feedback file, no boundary, no watermark.
    (snapshot,) = comments.snapshots_directory(drafts_root).iterdir()
    written = {path: held for path, held in _snapshot(drafts_root).items() if path not in before}
    assert written and all(Path(path).is_relative_to(snapshot) for path in written), written
    assert comments.launched_record(snapshot) is None, "a dry run claimed its snapshot"
    assert report.splitlines()[-2] == (
        f"plan store: {comments.store_version()}; the narrowed query returned 1 item(s) "
        f"commented on since {EARLY}"
    )
    assert report.splitlines()[-1] == (
        f"launch exactly this with: just {comments.PROG} --from {snapshot}"
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

    gather = ["gather", "--root", str(drafts_root), "--intent", str(drafts_root.parent / "i.json")]
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
    snapshot = comments.recorded_snapshot(written)
    assert snapshot is not None and comments.read_snapshot(snapshot).items[0].id == own
    (held,) = comments.read_snapshot(snapshot).items
    for said in (
        "Gathered by `just follow-ups-answer-comments` into the snapshot at "
        f"`{snapshot}`, each is quoted verbatim below with its id, its URL, its author, when it "
        "last changed, and its issue's id on the board, in the snapshot source `snapshot` and "
        "the version the snapshot holds.",
        "1. **Read it** from the snapshot source, never from the board: the issue with `task "
        "show <its snapshot id> --json` and its comments with `task comment list <its snapshot "
        "id>`.",
        '2. **Act on it** under "Ownership on the board" above: decide whether the ticket of '
        "the issue it sits on should change in light of it and of anything investigating it "
        "found, whether or not it asks for an edit, and make that change in the ticket's file; "
        "the board is written from it once this run settles.",
        "3. **Post its one reply**, naming the comment's id, as those rules state, with `task "
        "comment add <its board id> --expect <its version> --json` — after a reply of yours on "
        "that issue, expecting the version that reply's answer carried, and with no `--expect` "
        f"where its version is `{comments.NO_VERSION}` or that answer carried none — saying what "
        "changed in the ticket and why, or why the ticket stands as it is, and record the reply's "
        "id and URL from that command's answer.",
        "4. **Report** the comment's URL beside what you did about it, or why you did nothing.",
        " ".join(tickets.WITHDRAWAL_EXCEPTION.split())
        + " Never touch a ticket or an issue no comment above names",
        # The stand-in board's source keeps no cache, which a reply's `--expect` needs, so the
        # feedback names no version though the snapshot holds one.
        f"- Issue: {own}, read from {held.snapshot_id} at version {comments.NO_VERSION}",
    ):
        assert said in flat, said
    # The anchor sits with the comment it names, so the account's order is the file's.
    assert tickets.quoted_comments(written) == [
        tickets.Quoted(QualifiedTaskId(own), tickets.CommentId(identifier))
    ], written
    assert written.index("2. **Act on it**") < written.index("3. **Post its one reply**")
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


def test_two_snapshots_in_one_second_are_both_kept_and_a_feedback_file_is_never_rewritten(
    drafts_root: Path, board: Path, tmp_path: Path
) -> None:
    _commented(_filed(drafts_root, RUN, CAUSE), "Please add page 9.\n")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    said: list[str] = []
    since = datetime(2025, 1, 1, tzinfo=UTC)

    first = comments.gather(drafts_root, BOARD, since=since, now=lambda: now, say=said.append)
    second = comments.gather(drafts_root, BOARD, since=since, now=lambda: now, say=said.append)

    assert first is not None and second is not None
    assert (first.directory.name, second.directory.name) == (
        "20260101T000000Z",
        "20260101T000000Z-2",
    )
    feedback = comments.write_feedback(tmp_path / "f.md", "first")
    with pytest.raises(FileExistsError):
        comments.write_feedback(feedback, "second")
    assert feedback.read_text(encoding="utf-8") == "first"


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

    status, intent, _ = _steps(drafts_root, "--to", BOARD)

    refusal = capsys.readouterr().err
    assert status == comments.UNRUNNABLE
    assert f"run(s) {RUN} under {drafts_root}" in refusal
    assert "run it again with --since <RFC3339>" in refusal
    assert not intent.exists()
    assert not comments.snapshots_directory(drafts_root).exists() or not any(
        comments.snapshots_directory(drafts_root).iterdir()
    )


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
    root: Path,
    capsys: pytest.CaptureFixture[str],
    *arguments: str,
    reply: bool,
    fallback: str | None = None,
) -> tuple[Gathered, Path, str]:
    """A gathering of one comment on the run's issue, and its account, answered or not."""
    own = _filed(root, RUN, CAUSE)
    tickets.ticket_path(root, RUN, CAUSE).unlink()
    asked = _commented(own, "Please add page 9.\n")
    _, plan = _gathered(root, capsys, *arguments)
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
                        # A folder of Markdown answers a comment with no address of its own.
                        "reply_url": None,
                        "reply_fallback": fallback,
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

    status = comments.settle(drafts_root, plan.plan, {RUN: "follow-up-1"}, say=lines.append)

    assert status == comments.DONE, lines
    assert lines[0] == "verified on the board with one batched read of 1 item(s)"
    assert lines[1] == f"run {RUN} (follow-up run follow-up-1): check-responses passed"
    assert lines[2].startswith("  reply: file://") and lines[2].endswith(f"#comment-{posted}")
    queried = plan.snapshot.queried_at
    assert comments.read_watermark(drafts_root, BOARD) == queried - comments.OVERLAP
    assert "board writes: 0; fallback reads: 0" in lines
    assert lines[-1].startswith(
        f"plan store: {plan.snapshot.store}; the narrowed query returned 1 "
    )


def test_a_reply_that_took_a_fallback_read_is_named_and_counted(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reason = "the store read the item once to find the comment's thread"
    plan, _, posted = _answered_run(drafts_root, capsys, reply=True, fallback=reason)
    lines: list[str] = []

    status = comments.settle(drafts_root, plan.plan, {RUN: "follow-up-1"}, say=lines.append)

    assert status == comments.DONE, lines
    assert lines[1] == f"run {RUN} (follow-up run follow-up-1): check-responses passed"
    assert any(
        line.startswith("  fallback read: ") and line.endswith(f": reply {posted}: {reason}")
        for line in lines
    ), lines
    assert "board writes: 0; fallback reads: 1" in lines


def test_a_run_not_answered_soundly_is_named_and_leaves_the_watermark_where_it_was(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, feedback, posted = _answered_run(drafts_root, capsys, reply=False)
    lines: list[str] = []

    status = comments.settle(drafts_root, plan.plan, {RUN: "follow-up-1"}, say=lines.append)

    assert status == comments.UNANSWERED
    # The account is sound on its own: what it names is not on the board, which the one
    # batched read is what says.
    assert lines[1] == f"run {RUN} (follow-up run follow-up-1): its replies are not on the board"
    assert any(f"the board holds no reply {posted} of run" in line for line in lines), lines
    assert "watermark: left as it was, because a launched run was not answered soundly" in lines
    assert comments.read_watermark(drafts_root, BOARD) is None
    # A gathering that cannot be read at all is one more way it is not answered.
    feedback.unlink()
    lines.clear()
    assert comments.settle(drafts_root, plan.plan, {RUN: "x"}, say=lines.append) == (
        comments.UNANSWERED
    )
    assert lines[0] == f"run {RUN} (follow-up run x): check-responses failed"
    # A run whose launch failed is unanswered too, and says which file it owed.
    lines.clear()
    assert comments.settle(drafts_root, plan.plan, {}, say=lines.append) == comments.UNANSWERED
    assert lines[0] == f"run {RUN}: its follow-up run did not launch, so {feedback} is unanswered"


def test_detached_and_run_scoped_settlements_leave_the_watermark_and_say_why(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, feedback, _ = _answered_run(drafts_root, capsys, reply=True)
    lines: list[str] = []

    detached = dataclasses.replace(plan.plan, detach=True)
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
    assert comments.read_watermark(drafts_root, BOARD) is None
    lines.clear()
    _next_second()
    own = plan.snapshot.items[0].id
    # Bound again, so a gathering scoped to the run reads its item by that binding.
    _bound(drafts_root, RUN, CAUSE, _native_of(own))
    asked = _commented(own, "And page 10?\n")
    _, scoped = _gathered(drafts_root, capsys, "--run", RUN)
    assert scoped is not None and scoped.scope == RUN
    ((_, scoped_feedback),) = scoped.runs
    reply = _replied(own, asked)
    tickets.responses_path(scoped_feedback).write_text(
        json.dumps(
            {
                "schema": tickets.RESPONSES_SCHEMA,
                "run": RUN,
                "feedback": scoped_feedback.name,
                "responses": [
                    {
                        "comment": asked,
                        "issue": own,
                        "action": "Answered it.",
                        "reply": reply,
                        "reply_url": None,
                        "reply_fallback": None,
                        "verdict": tickets.Verdict.DOES_NOT_CONFIRM.value,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert comments.settle(drafts_root, scoped.plan, {RUN: "fu-2"}, say=lines.append) == (
        comments.DONE
    ), lines
    assert (
        f"watermark: left as it was, because the gathering was scoped to run {RUN}'s comments"
        in lines
    )
    assert comments.read_watermark(drafts_root, BOARD) is None


def test_the_script_steps_hand_the_plan_between_them(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`launches` names each launch as the script reads it, and `settle` reads the plan back."""
    _commented(_filed(drafts_root, RUN, CAUSE), "Please add page 9.\n")
    tickets.ticket_path(drafts_root, RUN, CAUSE).unlink()
    for detach, mode in ((False, "attach"), (True, "detach")):
        status, _, plan_path = _steps(
            drafts_root, "--to", BOARD, "--since", EARLY, *(["--detach"] if detach else [])
        )
        assert status == comments.DONE
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
    status, intent, plan_path = _steps(drafts_root, "--to", BOARD, "--since", EARLY, "--dry-run")
    assert status == comments.DONE
    assert json.loads(intent.read_text(encoding="utf-8"))["snapshot"] is None
    capsys.readouterr()
    assert plan_path.read_text(encoding="utf-8") == ""
    assert (
        comments.main(["launches", "--root", str(drafts_root), "--plan", str(plan_path)])
        == comments.DONE
    )
    assert comments.main(["settle", "--root", str(drafts_root), "--plan", str(plan_path)]) == (
        comments.DONE
    )
    assert capsys.readouterr().out == ""


def test_a_watermark_that_cannot_be_written_is_refused_by_settle(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A gathering that selects nothing settles sound, so its next step is the watermark.
    _filed(drafts_root, RUN, CAUSE)
    status, _, plan_path = _steps(drafts_root, "--to", BOARD, "--since", EARLY)
    assert status == comments.DONE
    capsys.readouterr()
    plan = comments.read_plan(plan_path, drafts_root)
    held = comments.watermark_path(drafts_root, BOARD)
    held.mkdir(parents=True)

    status = comments.main(
        ["settle", "--root", str(drafts_root), "--plan", str(plan_path)]
        + [f"--launched={run}=fu" for run, _ in plan.runs]
    )

    assert status == comments.UNRUNNABLE
    refused = capsys.readouterr().err
    assert refused.startswith(f"{comments.PROG}: refused: ")
    assert "is not a plan `launch` wrote" not in refused, refused


def test_an_invocation_that_cannot_run_is_its_own_status(
    drafts_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    intent = str(drafts_root.parent / "intent.json")
    gather = ["gather", "--root", str(drafts_root), "--intent", intent]
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
        ["gather", "--root", str(drafts_root), "--intent", str(drafts_root.parent / "i.json")]
    )

    assert status == comments.UNRUNNABLE
    assert tickets.BOARD in capsys.readouterr().err


def test_a_plan_or_a_launch_the_script_did_not_write_is_refused_naming_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The steps read only what the one before wrote; anything else is refused by name."""
    plan = tmp_path / "plan.json"
    root = str(tmp_path / "root")
    snapshot = (
        f"{root}/{comments.FEEDBACK_DIRECTORY}/{comments.SNAPSHOTS_DIRECTORY}/20260101T000000Z"
    )
    written = {"snapshot": snapshot, "detach": False, "runs": []}
    for broken in (
        "not json",
        json.dumps({key: value for key, value in written.items() if key != "runs"}),
        json.dumps(written | {"snapshot": "/elsewhere/20260101T000000Z"}),
        json.dumps(written | {"runs": [["not a run/id", "f.md"]]}),
        json.dumps(written | {"runs": [[RUN, "/etc/passwd"]]}),
        json.dumps(written | {"runs": [[RUN, f"/elsewhere/feedback/{RUN}/20260101T000000Z.md"]]}),
        json.dumps(written | {"runs": [["one"]]}),
        json.dumps(written | {"detach": "no"}),
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
    # A plan naming a snapshot that is not there is refused by the snapshot's own reader.
    assert comments.main(["launches", "--root", root, "--plan", str(plan)]) == comments.UNRUNNABLE
    assert f"{snapshot} holds no {comments.SNAPSHOT_RECORD}" in capsys.readouterr().err
    gather = ["gather", "--root", root, "--intent", str(plan), "--to", "../elsewhere"]
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

    status, _, _ = _steps(drafts_root, "--to", BOARD, "--since", EARLY)

    assert status == comments.UNRUNNABLE
    assert f"{own} reports a title spanning lines" in capsys.readouterr().err
    assert list(comments.snapshots_directory(drafts_root).iterdir()) == [], (
        "a refused gathering left its staged snapshot behind"
    )


def test_a_plans_fields_are_the_ones_its_reader_holds_it_to() -> None:
    """The plan's record, its written document and the reader's schema are one field list."""
    plan = comments.Plan(snapshot=Path("/x"), detach=False, runs=())
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
        snapshot=comments.snapshots_directory(root) / "20260101T000000Z",
        detach=True,
        runs=(comments.Launch(tickets.RunId(RUN), feedback),),
    )
    written = json.loads(json.dumps(comments._plan_document(plan)))
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(written), encoding="utf-8")

    for key, kind in comments.PLAN_FIELDS.items():
        assert isinstance(written[key], kind), (key, written[key])
    assert comments.read_plan(path, root) == plan


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
    follow_up_boundary.enable(monkeypatch, public=(BOARD,), private=(ROUTED,))
    return root


@pytest.mark.parametrize("passed_since", [False, True], ids=["alone", "a-later-pass-moved-it"])
def test_a_gathering_reads_every_board_of_the_family_each_from_its_own_watermark(
    drafts_root: Path, routed: Path, capsys: pytest.CaptureFixture[str], passed_since: bool
) -> None:
    """One gathering answers comments on the root board and on the board it routes to.

    The root board has a watermark and the routed one has none yet, so each is read from its
    own start; a sound settlement then gives each board a watermark of its own. Where a later
    pass settled first and moved the root board's watermark past this one's query, it stays
    where that pass left it, and the report says it was kept rather than advanced.
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
    assert plan is not None and plan.snapshot.boards == (BOARD, ROUTED)
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
                        "reply_url": None,
                        "reply_fallback": None,
                        "verdict": tickets.Verdict.DOES_NOT_CONFIRM.value,
                    }
                    for one, reply in zip(quoted, replies, strict=True)
                ],
            }
        ),
        encoding="utf-8",
    )
    said: list[str] = []
    later = plan.snapshot.queried_at + timedelta(hours=1)
    if passed_since:
        comments.write_watermark(drafts_root, BOARD, later)

    assert comments.settle(drafts_root, plan.plan, {RUN: "follow-up-1"}, say=said.append) == (
        comments.DONE
    ), said
    queried = plan.snapshot.queried_at - comments.OVERLAP
    root_since = later - comments.OVERLAP if passed_since else queried
    assert comments.read_watermark(drafts_root, BOARD) == root_since
    assert comments.read_watermark(drafts_root, ROUTED) == queried
    kept = [line for line in said if line.startswith("watermark: kept at")]
    if passed_since:
        assert kept == [
            f"watermark: kept at {comments.instant(root_since)} in "
            f"{comments.watermark_path(drafts_root, BOARD)}, already past this pass's query less "
            "the overlap"
        ], said
    else:
        assert kept == [], said
    advanced = sum(line.startswith("watermark: advanced to") for line in said)
    assert advanced == (1 if passed_since else 2), said


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
        QualifiedTaskId(f"{ROUTED}:ENG-1"),
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


#: How many unrelated items the crowded board holds, each commented since the watermark.
CROWD = 200
OTHER_HOST = "another-host"


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
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> dict[str, list[str]]:
    """The items an unscoped, an `--issue` and a `--run` dry run each copied off a crowded board.

    One issue a run here owns has one comment waiting, and :data:`CROWD` unrelated items were
    commented on since the watermark too; each pass selects that one comment.
    """
    own = _filed(drafts_root, RUN, CAUSE)
    _next_second()
    waiting = _commented(own, "Does this also hit page 9?\n")
    _crowd(board, datetime.now(UTC))
    comments.write_watermark(drafts_root, BOARD, datetime(2026, 1, 1, 0, 15, tzinfo=UTC))

    copied: dict[str, list[str]] = {}
    selected: dict[str, list[str]] = {}
    for name, arguments in (
        ("unscoped", ()),
        ("--issue", ("--issue", own)),
        ("--run", ("--run", RUN)),
    ):
        report, plan = _gathered(drafts_root, capsys, "--dry-run", *arguments, since=None)
        assert plan is None
        (directory,) = [
            line.removeprefix("snapshot: ")
            for line in report.splitlines()
            if line.startswith("snapshot: ")
        ]
        snapshot = comments.read_snapshot(Path(directory))
        copied[name] = [one.id for one in snapshot.items]
        assert len(list((snapshot.directory / comments.SNAPSHOT_ITEMS).rglob("*.md"))) == len(
            snapshot.items
        ), "the snapshot's source holds items its record does not name"
        # Each snapshot's copy has its own file address, so what is compared is the rest.
        selected[name] = [line.partition("#comment-")[2] for line in _selected(report)]
    assert selected["unscoped"] == [f"{waiting} on {own} would go to run {RUN}"], selected
    assert selected["--issue"] == selected["unscoped"] == selected["--run"], selected
    return copied


def test_a_pass_about_one_issue_reads_that_item_alone_however_crowded_the_board(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--issue` and `--run` copy one item and its comments; unscoped copies only owned items.

    A pass scoped to the one issue a run here owns, or to that run, selects the comment an
    unscoped pass selects and copies that one item by id; and the unscoped pass, narrowed by
    host at the store, copies that one item too, of the board's two hundred commented ones.
    """
    copied = _crowded_passes(drafts_root, board, capsys)
    for name in ("unscoped", "--issue", "--run"):
        assert copied[name] == [f"{BOARD}:{RUN}/tickets/{CAUSE}"], (name, copied[name])


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
    assert [one.id for one in plan.snapshot.items] == [own]
    assert plan.snapshot.issue == own
    assert _id_line(report, own, asked).endswith(f"goes to run {RUN}")
    assert others not in report
    assert (
        f"read {own} and its comments directly, the item run {RUN}'s ticket is bound to" in report
    )


def _refused(
    root: Path, capsys: pytest.CaptureFixture[str], *arguments: str, since: str | None = EARLY
) -> str:
    snapshots = comments.snapshots_directory(root)
    before = sorted(snapshots.iterdir()) if snapshots.exists() else []
    status, intent, plan = _steps(
        root, "--to", BOARD, *(["--since", since] if since is not None else []), *arguments
    )
    captured = capsys.readouterr()
    assert status == comments.UNRUNNABLE, captured
    assert not intent.exists() and not plan.exists(), "a refused gathering wrote what to launch"
    written = list((root / comments.FEEDBACK_DIRECTORY).glob("*/*.md"))
    assert written == [], "a refused gathering wrote feedback"
    after = sorted(snapshots.iterdir()) if snapshots.exists() else []
    assert after == before, "a refused gathering left a snapshot"
    return captured.err


def test_an_issue_no_run_of_this_host_owns_is_refused_naming_why_and_nothing_is_launched(
    drafts_root: Path,
    board: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _filed(drafts_root, RUN, CAUSE)
    stranger = _filed(drafts_root, THIRD_RUN, "a-strangers-cause")
    tickets.ticket_path(drafts_root, THIRD_RUN, "a-strangers-cause").unlink()
    _commented(stranger, "Nobody here holds this.\n")

    said = _refused(drafts_root, capsys, "--issue", stranger)

    assert f"--issue {stranger}: no run of this host owns it, because no ticket under " in said
    assert f"is bound to it (no record's `{tickets.BINDING_FIELD}` names it" in said
    assert said.rstrip().endswith("nothing was launched")
    # Refused before anything was copied, so no snapshot directory was even made.
    assert not comments.snapshots_directory(drafts_root).exists()

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
    assert f"no item with the id {BOARD}:deleted/item" in said, said

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
    assert report.splitlines()[-2].endswith(
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


def test_a_snapshot_item_the_store_answers_other_than_as_copied_is_refused() -> None:
    """A real store answers a copied item as it copied it; an error, another count, or an item
    naming another origin is refused here, since it would quote another item's comments."""
    item = {"title": "t", "status": {"category": "backlog", "name": "backlog"}}
    origin = {"metadata": {comments.ORIGIN_KEY: f"{BOARD}:another/item"}}
    for detail, refusal in (
        ({"items": [], "errors": []}, "could not be read"),
        ({"items": [{"item": item}, {"item": item}]}, "could not be read"),
        ({"items": [{"item": item}], "errors": [{"error": "gone"}]}, "could not be read"),
        ({"items": [{"no": "item"}]}, "answered no item"),
        (
            {"items": [{"id": f"{comments.SNAPSHOT_SOURCE}:one", "item": item | origin}]},
            "names 'commentboard:another/item' as its origin",
        ),
    ):
        with pytest.raises(OSError, match=refusal):
            comments.snapshot_issue(detail, f"{BOARD}:one", f"{comments.SNAPSHOT_SOURCE}:one", None)


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
        assert report.splitlines()[-2].endswith("each directly, and listed none"), report


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


def _previewed(root: Path, capsys: pytest.CaptureFixture[str], *arguments: str) -> Path:
    """A dry run's snapshot: the directory its report names."""
    report, plan = _gathered(root, capsys, "--dry-run", *arguments)
    assert plan is None
    (named,) = [line for line in report.splitlines() if line.startswith("snapshot: ")]
    return Path(named.removeprefix("snapshot: "))


def _launched_from(
    root: Path, capsys: pytest.CaptureFixture[str], snapshot: Path, *arguments: str
) -> tuple[int, str]:
    """`--from` the snapshot, as the recipe takes it: the exit and what it refused with."""
    intent = root.parent / f"intent-{time.monotonic_ns()}.json"
    plan = root.parent / f"plan-{time.monotonic_ns()}.json"
    status = comments.main(
        ["gather", "--root", str(root), "--intent", str(intent), "--from", str(snapshot)]
        + list(arguments)
    )
    if status == comments.DONE:
        status = comments.main(
            ["launch", "--root", str(root), "--intent", str(intent), "--plan", str(plan)]
        )
    return status, capsys.readouterr().err


def test_the_snapshot_is_written_as_its_contract_states_and_read_back_as_itself(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The directory, its copy, its record and its claim, each where and how the module says."""
    own = _filed(drafts_root, RUN, CAUSE)
    tickets.ticket_path(drafts_root, RUN, CAUSE).unlink()
    asked = _commented(own, "Please add page 9.\n")
    snapshot = _previewed(drafts_root, capsys)

    assert snapshot.parent == drafts_root / "feedback" / ".snapshots"
    assert comments.SNAPSHOT_NAME.fullmatch(snapshot.name)
    record = json.loads((snapshot / comments.SNAPSHOT_RECORD).read_text(encoding="utf-8"))
    assert sorted(record) == sorted(comments.SNAPSHOT_KEYS)
    assert record["schema"] == comments.SNAPSHOT_SCHEMA and record["dry_run"] is True
    (item,) = record["items"]
    assert sorted(item) == sorted(comments.SnapshotItem._fields)
    assert item["id"] == own and item["snapshot_id"].startswith(f"{comments.SNAPSHOT_SOURCE}:")
    held = comments.read_snapshot(snapshot)
    assert comments._snapshot_document(held) == record, "the record reads back as itself"
    (chosen,) = held.selection[tickets.RunId(RUN)]
    assert (chosen.issue, chosen.comment) == (own, asked)
    assert chosen.body_sha256 == comments.body_sha256("Please add page 9.\n")
    copied = list((snapshot / comments.SNAPSHOT_ITEMS).rglob("*.md"))
    assert len(copied) == 1
    text = copied[0].read_text(encoding="utf-8")
    assert f"onetaskgraph.origin: {own}" in text and "onetaskgraph.copied_version:" in text
    assert f'id="{asked}"' in text and f'task="{own}"' in text
    assert comments.launched_record(snapshot) is None

    status, _ = _launched_from(drafts_root, capsys, snapshot)

    assert status == comments.DONE
    claimed = comments.launched_record(snapshot)
    assert claimed is not None and claimed["launch"] == "from"
    (feedback,) = (drafts_root / "feedback" / RUN).glob("*.md")
    assert feedback.name == f"{snapshot.name}.md"
    assert (snapshot / comments.SNAPSHOT_LAUNCHED).stat().st_mtime_ns <= feedback.stat().st_mtime_ns
    written = feedback.read_text(encoding="utf-8")
    assert comments.recorded_snapshot(written) == snapshot
    assert item["version"] is not None
    assert f"- Issue: {comments.issue_line(own, item['snapshot_id'], None)}\n" in written
    (selected,) = record["selection"][RUN]
    broken: object
    for broken in (
        [],
        {"schema": 2},
        record | {"schema": 2},
        record | {"items": [{"id": own}]},
        # A selection naming an item the snapshot did not capture, or a comment of no digest
        # or no instant, and a scope or a start that is no run id or no time.
        record | {"items": []},
        record | {"selection": {RUN: [selected | {"body_sha256": "not-a-digest"}]}},
        record | {"selection": {RUN: [selected | {"last_changed": "yesterday"}]}},
        record | {"scope": 3},
        record | {"queried_at": "now"},
        record | {"boundaries": {"a/run": {"boundary": None, "computed": True}}},
        record | {"items": [item | {"id": f"another-board:{own.partition(':')[2]}"}]},
        # An owner that is no run id, and an issue, scoped or left out, that is no task id.
        record | {"items": [item | {"owner": "a/run"}]},
        record | {"issue": "not qualified"},
        record | {"left": [{"issue": "not qualified", "comment": None, "reason": "why"}]},
        record | {"left": [{"issue": own, "comment": "", "reason": "why"}]},
        # One item recorded twice, or one comment selected twice.
        record | {"items": [item, item]},
        record | {"selection": {RUN: [selected, selected]}},
    ):
        (snapshot / comments.SNAPSHOT_RECORD).write_text(json.dumps(broken), encoding="utf-8")
        with pytest.raises(OSError, match="is not a schema-1 snapshot"):
            comments.read_snapshot(snapshot)


def test_check_gathering_holds_a_feedback_file_to_its_snapshot_and_its_source_by_name(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A feedback file, `snapshot.json` and the snapshot source that disagree are refused."""
    own = _filed(drafts_root, RUN, CAUSE)
    tickets.ticket_path(drafts_root, RUN, CAUSE).unlink()
    _commented(own, "Please add page 9.\n")
    _, plan = _gathered(drafts_root, capsys)
    assert plan is not None
    ((_, feedback),) = plan.runs
    snapshot = plan.snapshot.directory
    command = ["check-gathering", "--board", BOARD, "--feedback", str(feedback), RUN]
    assert tickets.main(command) == tickets.SOUND
    capsys.readouterr()
    written = feedback.read_text(encoding="utf-8")
    record = (snapshot / comments.SNAPSHOT_RECORD).read_text(encoding="utf-8")
    (copy,) = (snapshot / comments.SNAPSHOT_ITEMS).rglob("*.md")
    source = copy.read_text(encoding="utf-8")
    (held,) = plan.snapshot.items

    def refused(said: str) -> None:
        assert tickets.main(command) == tickets.UNSOUND
        assert said in capsys.readouterr().err, said

    feedback.write_text(
        written.replace(f"at version {comments.NO_VERSION}", "at version md1|another"),
        encoding="utf-8",
    )
    refused("rather than its snapshot's")
    feedback.write_text(written, encoding="utf-8")
    copy.write_text(source.replace("Please add page 9.", "Please add page 10."), encoding="utf-8")
    refused("with text different from its snapshot")
    copy.write_text(source, encoding="utf-8")
    (snapshot / comments.SNAPSHOT_RECORD).write_text(
        record.replace(comments.body_sha256("Please add page 9.\n"), "0" * 64), encoding="utf-8"
    )
    refused("other than its record selected")
    selected = json.loads(record)["selection"][RUN][0]["last_changed"]
    (snapshot / comments.SNAPSHOT_RECORD).write_text(
        record.replace(selected, "2026-01-01T00:00:00Z"), encoding="utf-8"
    )
    refused("other than its record selected")
    (snapshot / comments.SNAPSHOT_RECORD).write_text("{", encoding="utf-8")
    refused("its snapshot is missing or malformed")
    (snapshot / comments.SNAPSHOT_RECORD).unlink()
    refused(f"{snapshot} holds no {comments.SNAPSHOT_RECORD}")
    feedback.write_text(
        written.replace(comments.FEEDBACK_SNAPSHOT.format(snapshot=snapshot) + "\n", ""),
        encoding="utf-8",
    )
    refused("its instructions before the first comment differ from the gathering")


def test_each_reason_from_refuses_a_snapshot_writes_nothing_and_names_a_fresh_dry_run(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    _commented(own, "Please add page 9.\n")
    first = _previewed(drafts_root, capsys, "--run", RUN)
    second = _previewed(drafts_root, capsys)
    fresh = f"`just {comments.PROG} --dry-run"

    def nothing_written() -> None:
        assert not (drafts_root / "feedback" / RUN).exists()
        assert not comments.boundary_path(drafts_root, RUN).exists()

    for flag in (
        ["--since", EARLY],
        ["--run", RUN],
        ["--issue", own],
        ["--to", BOARD],
        ["--dry-run"],
    ):
        status, said = _launched_from(drafts_root, capsys, second, *flag)
        assert status == comments.UNRUNNABLE, flag
        assert f"{flag[0]} cannot be named beside it" in said, said
    nothing_written()

    # A snapshot another drafts root keeps is no snapshot of this one's to claim.
    elsewhere = drafts_root.parent / "elsewhere" / second.name
    shutil.copytree(second, elsewhere)
    status, said = _launched_from(drafts_root, capsys, elsewhere)
    assert status == comments.UNRUNNABLE
    assert f"{elsewhere} is no snapshot under {comments.snapshots_directory(drafts_root)}" in said
    assert comments.launched_record(elsewhere) is None
    nothing_written()

    held = json.loads((second / comments.SNAPSHOT_RECORD).read_text(encoding="utf-8"))
    (second / comments.SNAPSHOT_RECORD).write_text(json.dumps(held | {"store": 3}), "utf-8")
    status, said = _launched_from(drafts_root, capsys, second)
    assert status == comments.UNRUNNABLE
    assert "the snapshot is malformed" in said and fresh in said
    (second / comments.SNAPSHOT_RECORD).write_text(json.dumps(held), "utf-8")
    nothing_written()

    stale = comments.read_snapshot(first)
    old = dataclasses.replace(
        stale, queried_at=datetime.now(UTC) - comments.SNAPSHOT_LAUNCHABLE_FOR - timedelta(1)
    )
    (first / comments.SNAPSHOT_RECORD).write_text(
        json.dumps(comments._snapshot_document(old)), "utf-8"
    )
    status, said = _launched_from(drafts_root, capsys, first)
    assert status == comments.UNRUNNABLE
    assert f"more than {comments.SNAPSHOT_LAUNCHABLE_FOR} ago" in said
    assert f"{fresh} --run {RUN} --to {BOARD}`" in said, said
    (first / comments.SNAPSHOT_RECORD).write_text(
        json.dumps(comments._snapshot_document(stale)), "utf-8"
    )
    nothing_written()

    status, _ = _launched_from(drafts_root, capsys, second)
    assert status == comments.DONE
    claimed = comments.launched_record(second)
    assert claimed is not None
    feedback = list((drafts_root / "feedback" / RUN).glob("*.md"))

    status, said = _launched_from(drafts_root, capsys, second)
    assert status == comments.UNRUNNABLE
    assert f"it was already launched at {claimed['claimed_at']}" in said and fresh in said
    status, said = _launched_from(drafts_root, capsys, first)
    assert status == comments.UNRUNNABLE
    assert f"the later snapshot {second}" in said and "was launched since" in said, said
    assert list((drafts_root / "feedback" / RUN).glob("*.md")) == feedback


def test_a_dry_run_moves_no_watermark_and_a_sound_from_launch_moves_it_never_backward(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    tickets.ticket_path(drafts_root, RUN, CAUSE).unlink()
    _commented(own, "Please add page 9.\n")
    later = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
    comments.write_watermark(drafts_root, BOARD, later)
    held = comments.watermark_path(drafts_root, BOARD).read_bytes()

    snapshot = _previewed(drafts_root, capsys)
    assert comments.watermark_path(drafts_root, BOARD).read_bytes() == held
    status, _ = _launched_from(drafts_root, capsys, snapshot)
    assert status == comments.DONE
    plan = comments.read_plan(sorted(drafts_root.parent.glob("plan-*.json"))[-1], drafts_root)
    ((_, feedback),) = plan.runs
    tickets.responses_path(feedback).write_text(
        json.dumps(
            {
                "schema": tickets.RESPONSES_SCHEMA,
                "run": RUN,
                "feedback": feedback.name,
                "responses": [
                    {
                        "comment": tickets.quoted_comments(feedback.read_text("utf-8"))[0].comment,
                        "issue": own,
                        "action": "Answered it.",
                        "reply": _replied(
                            own, tickets.quoted_comments(feedback.read_text("utf-8"))[0].comment
                        ),
                        "reply_url": None,
                        "reply_fallback": None,
                        "verdict": tickets.Verdict.DOES_NOT_CONFIRM.value,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    said: list[str] = []

    assert comments.settle(drafts_root, plan, {RUN: "fu"}, say=said.append) == comments.DONE
    assert comments.read_watermark(drafts_root, BOARD) == later - comments.OVERLAP, (
        "a sound launch moved the watermark backward"
    )
    comments.watermark_path(drafts_root, BOARD).unlink()
    assert comments.settle(drafts_root, plan, {RUN: "fu"}, say=said.append) == comments.DONE
    queried = comments.read_snapshot(snapshot).queried_at
    assert comments.read_watermark(drafts_root, BOARD) == queried - comments.OVERLAP, said


def test_a_reply_answers_the_version_its_gathering_quoted_and_one_with_no_account_the_time_rule(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A comment edited after its snapshot is selected again, though its reply came later.

    The reply was posted after the edit, so by its time alone it would answer the edit; this
    host's account says it answered the version the snapshot quoted, which is older. With no
    account of it here, the time rule decides, as for a reply another host posted.
    """
    own = _filed(drafts_root, RUN, CAUSE)
    tickets.ticket_path(drafts_root, RUN, CAUSE).unlink()
    asked = _commented(own, "Please add page 9.\n")
    _, plan = _gathered(drafts_root, capsys)
    assert plan is not None
    ((_, feedback),) = plan.runs
    _next_second()
    _edited(own, asked, "Please add pages 9 and 10.\n")
    _next_second()
    reply = _replied(own, asked)
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
                        "action": "Answered it.",
                        "reply": reply,
                        "reply_url": None,
                        "reply_fallback": None,
                        "verdict": tickets.Verdict.DOES_NOT_CONFIRM.value,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    quoted = comments.moment(
        plan.snapshot.selection[tickets.RunId(RUN)][0].last_changed, "the quoted version"
    )
    assert comments.quoted_versions(drafts_root) == {(own, reply): quoted}

    report, _ = _gathered(drafts_root, capsys, "--dry-run")
    assert _id_line(report, own, asked).endswith(f"would go to run {RUN}"), report

    tickets.responses_path(feedback).unlink()
    report, _ = _gathered(drafts_root, capsys, "--dry-run")
    assert _id_line(report, own, asked).endswith(f": answered by reply {reply}"), report


def _answering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, printed: str, exit: int = 0
) -> None:
    """The store answering every command with ``printed`` and ``exit``: what no real store says."""
    script = tmp_path / f"store-{time.monotonic_ns()}"
    script.write_text(f"#!/bin/sh\ncat <<'EOF'\n{printed}\nEOF\nexit {exit}\n", encoding="utf-8")
    script.chmod(0o755)
    # llmlint: ignore[e2e_not_mocked, tests_mirror_real_usage] Each answer here is one no real store gives — an unreadable document, an entry naming no source, a detail missing for an id — so the published CLI is stood in for at the one seam this module reads it through, `plan_store.locked_binary`, and nothing above it.  # noqa: E501 - a directive is one line
    monkeypatch.setattr(plan_store, "locked_binary", lambda interpreter=None: script)


def test_answers_no_real_store_gives_are_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _answering(tmp_path, monkeypatch, "not json")
    assert comments.store_command(["task", "show", "x"]).document is None
    with pytest.raises(OSError, match="names no default status mapping"):
        comments._local_md_defaults()
    mapping: dict[str, Any] = {
        "plugin_config": {comments.SNAPSHOT_PLUGIN: {"properties": {"status_mapping": {}}}}
    }
    for default in (["backlog"], {"Proposal": 3}):
        mapping["plugin_config"][comments.SNAPSHOT_PLUGIN]["properties"]["status_mapping"] = {
            "default": default
        }
        _answering(tmp_path, monkeypatch, json.dumps(mapping))
        with pytest.raises(OSError, match="names no default status mapping"):
            comments._local_md_defaults()
    _answering(tmp_path, monkeypatch, '{"items": [{"action": "created"}]}')
    with pytest.raises(OSError, match="answered an entry naming no source"):
        comments.copy_into_snapshot(tmp_path, BOARD, [f"{BOARD}:x"], [])
    unasked = {"source": f"{BOARD}:z", "destination": f"{comments.SNAPSHOT_SOURCE}:z"}
    _answering(tmp_path, monkeypatch, json.dumps({"items": [unasked]}))
    with pytest.raises(OSError, match=f"answered {BOARD}:z, which it was not asked for"):
        comments.copy_into_snapshot(tmp_path, BOARD, [f"{BOARD}:x"], [])
    with pytest.raises(OSError, match=f"answered {BOARD}:z, which it was not asked for"):
        comments.copy_into_snapshot(tmp_path, BOARD, ["--source", "another-board"], [])
    twice = {"source": f"{BOARD}:x", "destination": f"{comments.SNAPSHOT_SOURCE}:x"}
    _answering(tmp_path, monkeypatch, json.dumps({"items": [twice, twice]}))
    with pytest.raises(OSError, match=f"answered {BOARD}:x more than once"):
        comments.copy_into_snapshot(tmp_path, BOARD, [f"{BOARD}:x"], [])
    onto_one = [twice, twice | {"source": f"{BOARD}:y"}]
    _answering(tmp_path, monkeypatch, json.dumps({"items": onto_one}))
    with pytest.raises(OSError, match="answered two items copied to one task"):
        comments.copy_into_snapshot(tmp_path, BOARD, [f"{BOARD}:x", f"{BOARD}:y"], [])
    _answering(tmp_path, monkeypatch, json.dumps({"items": [twice]}))
    partly = comments.copy_into_snapshot(tmp_path, BOARD, [f"{BOARD}:x", f"{BOARD}:y"], [])
    assert partly.failed == {f"{BOARD}:y": "the copy answered nothing for it"}, partly
    copied_elsewhere = {"source": f"{BOARD}:x", "destination": f"{BOARD}:y", "action": "created"}
    _answering(tmp_path, monkeypatch, json.dumps({"items": [copied_elsewhere]}))
    with pytest.raises(OSError, match="which is no task of the snapshot source"):
        comments.copy_into_snapshot(tmp_path, BOARD, [f"{BOARD}:x"], [])
    _answering(tmp_path, monkeypatch, '{"failure": {"message": "no such source"}}', exit=1)
    with pytest.raises(OSError, match="was refused: no such source"):
        comments.copy_into_snapshot(tmp_path, BOARD, [f"{BOARD}:x"], [])
    _answering(tmp_path, monkeypatch, '{"details": []}')
    with pytest.raises(OSError, match=f"`task show-many` of {BOARD}:x answered"):
        comments.shown_details([f"{BOARD}:x"])
    assert comments.shown_details([]) == []
    _answering(tmp_path, monkeypatch, "", exit=1)
    lost = comments.outcome(comments.store_command(["task", "update", "x"]), "x")
    assert (lost.state, lost.error) == ("unknown", "the store printed no response")


def test_a_boards_status_names_are_read_off_its_configuration_and_one_two_boards_split_refused(
    board: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved = plan_store.configured_settings()
    prefix = f"sources.{BOARD}.config.status_mapping"
    assert comments._status_mapping(
        {
            f"{prefix}.backlog": "Proposal",
            f"{prefix}.draft.task": "Later",
            f"{prefix}.draft.project": "Idea",
            f"{prefix}.x": 3,
        },
        BOARD,
        {"backlog", "draft"},
    ) == {"proposal": "backlog", "later": "draft"}
    # A `local-md` board's mapping is keyed by name; a value naming no category is held at no
    # item, as a folder standing in for a board configured the other way round reads.
    assert comments._status_mapping(
        {
            f"sources.{BOARD}.plugin": plan_store.WRITABLE_PLUGIN,
            f"{prefix}.Proposal": "backlog",
            f"{prefix}.cancelled": "Canceled",
        },
        BOARD,
        {"backlog", "cancelled"},
    ) == {"proposal": "backlog"}
    assert comments.snapshot_status_names(BOARD)["in progress"] == "in-progress"
    for names, refused in (
        (
            {
                f"sources.{BOARD}.plugin": "github-projects",
                f"{prefix}.backlog": "Shared",
                # The routed board is a `local-md` folder, whose mapping is keyed by name.
                f"sources.{ROUTED}.plugin": plan_store.WRITABLE_PLUGIN,
                f"sources.{ROUTED}.config.status_mapping.Shared": "todo",
            },
            "read the status 'shared' as 'backlog' and 'todo'",
        ),
        (
            {f"sources.{BOARD}.plugin": "github-projects", f"{prefix}.backlog": "a.dotted name"},
            "names a status 'a.dotted name'",
        ),
    ):
        monkeypatch.setattr(
            plan_store,
            "configured_settings",
            lambda names=names: {**resolved, f"sources.{BOARD}.routes.0.to": ROUTED, **names},
        )
        with pytest.raises(OSError, match=re.escape(refused)):
            comments.snapshot_status_names(BOARD)
    with pytest.raises(OSError, match="answered no status the store names"):
        comments._status({})
    with pytest.raises(OSError, match="answered no status the store names"):
        comments._status({"status": {"category": ["backlog"]}})
    assert comments._status({"status": {"category": "draft"}}) == ("draft", "")


def test_a_snapshot_staged_where_none_was_staged_is_refused_rather_than_published(
    drafts_root: Path,
) -> None:
    snapshot = comments.Snapshot(
        directory=drafts_root,
        queried_at=datetime(2026, 1, 1, tzinfo=UTC),
        board=BOARD,
        boards=(BOARD,),
        named_board=True,
        scope=None,
        issue=None,
        starts={},
        store="onetaskgraph test",
        dry_run=False,
        items=(),
        boundaries={},
        selection={},
        left=(),
    )
    # Staged where its parent will take no rename: refused as the filesystem refused it.
    parent = drafts_root / "locked"
    staged = parent / ".staging-x"
    staged.mkdir(parents=True)
    parent.chmod(0o555)
    try:
        with pytest.raises(PermissionError):
            comments.publish_snapshot(staged, snapshot)
    finally:
        parent.chmod(0o755)


def test_local_accounts_this_cannot_read_map_no_reply_and_leave_it_to_the_time_rule(
    drafts_root: Path,
) -> None:
    directory = drafts_root / "feedback" / RUN
    directory.mkdir(parents=True)
    (directory / f"a{tickets.RESPONSES_SUFFIX}").write_text("{", encoding="utf-8")
    (directory / f"b{tickets.RESPONSES_SUFFIX}").write_text("[]", encoding="utf-8")
    (directory / f"c{tickets.RESPONSES_SUFFIX}").write_text("{}", encoding="utf-8")
    (directory / "d.md").write_text(
        f"{tickets.QUOTED_COMMENT_HEADING}1: on `{BOARD}:x`\n\n"
        f"{tickets.quoted_comment(f'{BOARD}:x', 'c-1')}\n\n- Comment id: c-1\n"
        "- Last changed: 2026-01-01T00:00:00Z\n\n```text\nA comment.\n```\n",
        encoding="utf-8",
    )
    (directory / f"d{tickets.RESPONSES_SUFFIX}").write_text(
        json.dumps(
            {
                "responses": [
                    "not an entry",
                    {"issue": f"{BOARD}:x", "comment": "c-1", "reply": "r-1"},
                    {"issue": f"{BOARD}:x", "comment": "c-9", "reply": "r-9"},
                ]
            }
        ),
        encoding="utf-8",
    )

    assert comments.quoted_versions(drafts_root) == {
        (f"{BOARD}:x", "r-1"): datetime(2026, 1, 1, tzinfo=UTC)
    }


def test_the_fresh_dry_run_a_refusal_names_is_the_gathering_the_snapshot_was() -> None:
    held = comments.Snapshot(
        directory=Path("/x"),
        queried_at=datetime(2026, 1, 1, tzinfo=UTC),
        board=BOARD,
        boards=(BOARD,),
        named_board=False,
        scope=tickets.RunId(RUN),
        issue=QualifiedTaskId(f"{BOARD}:x"),
        starts={},
        store="onetaskgraph test",
        dry_run=True,
        items=(),
        boundaries={},
        selection={},
        left=(),
    )

    assert comments.dry_run_command(held) == f"just {comments.PROG} --dry-run --issue {BOARD}:x"
    assert comments.dry_run_command(dataclasses.replace(held, issue=None)) == (
        f"just {comments.PROG} --dry-run --run {RUN}"
    )
    assert comments.dry_run_command(dataclasses.replace(held, issue=None, scope=None)) == (
        f"just {comments.PROG} --dry-run"
    )


def test_a_claim_that_finds_its_snapshot_claimed_or_its_source_moved_is_refused(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    tickets.ticket_path(drafts_root, RUN, CAUSE).unlink()
    _commented(own, "Please add page 9.\n")
    snapshot = _previewed(drafts_root, capsys)
    (copy,) = (snapshot / comments.SNAPSHOT_ITEMS).rglob("*.md")
    copy.write_text(
        copy.read_text(encoding="utf-8").replace("Please add page 9.", "Please add page 10."),
        encoding="utf-8",
    )

    status, said = _launched_from(drafts_root, capsys, snapshot)
    assert status == comments.UNRUNNABLE
    assert "other than its snapshot.json recorded" in said
    # Refused before anything was written, so nothing claims it and no feedback was left.
    assert comments.launched_record(snapshot) is None
    assert not (drafts_root / "feedback" / RUN).exists()

    other = _previewed(drafts_root, capsys)
    (other / comments.SNAPSHOT_LAUNCHED).write_text("{", encoding="utf-8")
    assert comments.launched_record(other) == {"claimed_at": "(unreadable)"}
    (other / comments.SNAPSHOT_LAUNCHED).write_text("[]", encoding="utf-8")
    assert comments.launched_record(other) == {"claimed_at": "(unreadable)"}
    (other / comments.SNAPSHOT_LAUNCHED).unlink()
    with pytest.raises(comments.Unlaunchable, match="another launch claimed it first"):
        comments.claim(drafts_root, other, fresh=True, detach=False, say=lambda _: None)
        comments.claim(drafts_root, other, fresh=True, detach=False, say=lambda _: None)


def test_a_launched_snapshot_whose_record_is_unreadable_is_passed_over_by_the_overlap_check(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    _commented(own, "Please add page 9.\n")
    first = _previewed(drafts_root, capsys)
    later = _previewed(drafts_root, capsys)
    earlier = comments.read_snapshot(first)
    (first / comments.SNAPSHOT_LAUNCHED).write_text("{}", encoding="utf-8")
    assert (
        comments.launchable_problem(comments.read_snapshot(later), drafts_root, datetime.now(UTC))
        is None
    ), "an earlier snapshot launched refused a later one"
    (first / comments.SNAPSHOT_LAUNCHED).unlink()
    del earlier
    (later / comments.SNAPSHOT_LAUNCHED).write_text("{}", encoding="utf-8")
    (later / comments.SNAPSHOT_RECORD).write_text("{", encoding="utf-8")
    (comments.snapshots_directory(drafts_root) / "not-a-snapshot").mkdir()

    assert (
        comments.launchable_problem(comments.read_snapshot(first), drafts_root, datetime.now(UTC))
        is None
    )


def test_pending_writes_of_any_other_shape_are_refused_rather_than_lost(drafts_root: Path) -> None:
    path = comments.pending_path(drafts_root)
    path.parent.mkdir(parents=True)
    entry: dict[str, object] = {
        "snapshot": "/x",
        "run": RUN,
        "item": f"{BOARD}:x",
        "write": "priority",
        "state": "failed",
        "snapshot_version": None,
        "seen_version": "v",
        "intended": {"priority": "high"},
        "parts": [],
        "error": "refused",
        "recorded_at": "2026-01-01T00:00:00Z",
    }
    path.write_text(json.dumps({"schema": 1, "entries": [entry]}), encoding="utf-8")
    (held,) = comments.read_pending(drafts_root)
    assert held.intended == {"priority": "high"}
    for broken in (
        "{",
        json.dumps([]),
        json.dumps({"schema": 2, "entries": []}),
        json.dumps({"schema": 1, "entries": [entry | {"write": "comment"}]}),
        json.dumps({"schema": 1, "entries": [entry | {"intended": {"color": "red"}}]}),
        json.dumps({"schema": 1, "entries": [entry | {"parts": ["landed"]}]}),
        json.dumps({"schema": 1, "entries": [{"item": "x"}]}),
        # A value of another type, a field the write does not set, an empty intent, a part
        # it does not describe, or an item, run or instant that is none.
        json.dumps({"schema": 1, "entries": [entry | {"intended": {"priority": 3}}]}),
        json.dumps({"schema": 1, "entries": [entry | {"intended": {"title": "x"}}]}),
        json.dumps({"schema": 1, "entries": [entry | {"intended": {}}]}),
        json.dumps(
            {
                "schema": 1,
                "entries": [entry | {"write": "ticket-copy", "intended": {"repositories": [3]}}],
            }
        ),
        json.dumps({"schema": 1, "entries": [entry | {"parts": [{"part": 1}]}]}),
        json.dumps({"schema": 1, "entries": [entry | {"item": "x"}]}),
        json.dumps({"schema": 1, "entries": [entry | {"run": "a/run"}]}),
        json.dumps({"schema": 1, "entries": [entry | {"recorded_at": "now"}]}),
    ):
        path.write_text(broken, encoding="utf-8")
        with pytest.raises(OSError, match=str(path)):
            comments.read_pending(drafts_root)
    path.write_bytes(b"\xff")
    with pytest.raises(OSError, match="could not be read"):
        comments.read_pending(drafts_root)
    comments.write_pending(drafts_root, [])
    assert not path.exists()
    assert comments.read_pending(drafts_root) == []
    # Something other than a file where the record lives is no record that nothing is pending.
    path.mkdir()
    with pytest.raises(OSError, match="could not be read.*repair it rather than lose a write"):
        comments.read_pending(drafts_root)
    path.rmdir()
    path.symlink_to(drafts_root / "nothing-here")
    with pytest.raises(OSError, match="is a link to nothing"):
        comments.read_pending(drafts_root)


def test_what_a_pending_write_intends_is_held_to_the_fields_settle_projects() -> None:
    """`Intended` declares each field at the type `Projected` does, and each write's fields."""
    assert get_type_hints(comments.Intended) == get_type_hints(comments.Projected)
    assert set().union(*comments.WRITE_FIELDS.values()) <= set(comments.FIELDS)
    assert set(comments.WRITE_FIELDS) == set(comments.WRITES)
    with pytest.raises(ValueError, match="a priority write sets no title"):
        comments.as_intended("priority", {"title": "x"})


def test_a_write_response_that_does_not_say_what_it_wrote_is_unknown() -> None:
    """A part it does not describe, or a success naming no part and no task, is no landing."""
    written = f"{BOARD}:x"
    undescribed = comments.outcome(
        comments.StoreAnswer(0, {"result": {"parts": [{"part": 1, "outcome": "landed"}]}}, ""),
        written,
    )
    assert undescribed.state == "unknown" and "a part was not described" in str(undescribed.error)
    bare = comments.outcome(comments.StoreAnswer(0, {"result": {"parts": ["landed"]}}, ""), written)
    assert bare.state == "unknown" and "a part was not described" in str(bare.error)
    nested = {"items": [{"destination": written, "action": "updated", "result": ["landed"]}]}
    assert comments.outcome(comments.StoreAnswer(0, nested, ""), written).state == "unknown"
    result: object
    for result in ({"parts": "landed"}, ["landed"], {"parts": []}):
        malformed = comments.outcome(
            comments.StoreAnswer(0, {"id": written, "result": result}, ""), written
        )
        assert malformed.state == "unknown" and "the result was not described" in str(
            malformed.error
        )
    silent = comments.outcome(comments.StoreAnswer(0, {}, ""), written)
    assert (silent.state, silent.error) == ("unknown", f"the response named None, not {written}")
    plain = comments.outcome(comments.StoreAnswer(0, {"id": written, "written": []}, ""), written)
    assert plain.state is None, "a write sent with no --expect answers with the task it wrote"
    other = comments.outcome(
        comments.StoreAnswer(0, {"id": f"{BOARD}:y", "written": []}, ""), written
    )
    assert other.state == "unknown", "a response about another task is no landing of this one"
    refused = comments.outcome(
        comments.StoreAnswer(
            1,
            {
                "result": {
                    "parts": [
                        {"part": "issue", "outcome": "rejected", "error": {"message": "stale"}},
                        {"part": "fields", "outcome": "rejected", "error": None},
                    ]
                }
            },
            "",
        ),
        written,
    )
    assert (refused.state, refused.error) == ("failed", "stale")
    assert refused.parts == (
        comments.WritePart("issue", "rejected", "stale"),
        comments.WritePart("fields", "rejected", None),
    )


def test_a_write_response_reporting_only_some_requested_parts_is_no_landing() -> None:
    """A response whose landed parts leave out a part a requested field rides on is `partial`."""
    written = f"{BOARD}:x"

    def answered(*parts: str) -> comments.StoreAnswer:
        reported = [{"part": part, "outcome": "landed", "fields": {}} for part in parts]
        return comments.StoreAnswer(0, {"id": written, "result": {"parts": reported}}, "")

    requested = {"title": "T", "priority": "high"}
    short = comments.outcome(answered("fields"), written, requested)
    assert short.state == "partial"
    assert short.error == "the response reported no landed issue part"
    assert comments.outcome(answered("fields", "issue"), written, requested).state is None
    assert comments.outcome(answered("fields"), written, {"priority": "high"}).state is None
    assert comments.outcome(answered("issue"), written, {"repositories": ["r"]}).state is None


def test_a_plain_write_answering_a_task_without_every_requested_value_is_no_landing() -> None:
    """A write sent with no `--expect` answers with the task it wrote, held to what was asked."""
    written = f"{BOARD}:x"
    task = {
        "id": "x",
        "title": "T",
        "content": None,
        "priority": "high",
        "status": {"category": "backlog", "name": "Proposal"},
        "metadata": {},
    }

    def answered(said: list[str], **fields: object) -> comments.StoreAnswer:
        return comments.StoreAnswer(0, {"id": written, "task": task | fields, "written": said}, "")

    requested = {"title": "T", "priority": "urgent"}
    short = comments.outcome(answered(["title"]), written, requested)
    assert (short.state, short.error) == (
        "partial",
        "the task it answered does not hold the requested priority",
    )
    assert comments.outcome(answered([], title="Old"), written, requested).state == "failed"
    assert (
        comments.outcome(answered(["title"], priority="urgent"), written, requested).state is None
    )
    odd = comments.outcome(answered(["title"], title=3), written, requested)
    assert odd.state == "unknown" and "at another type" in str(odd.error)
    bare = comments.StoreAnswer(0, {"id": written, "written": ["title"]}, "")
    assert comments.outcome(bare, written, requested) == comments.Outcome(
        "unknown", None, (), "the response showed neither the task nor its parts", None
    )

    def copied(action: str) -> comments.StoreAnswer:
        entry = {"source": "drafts:t", "destination": written, "action": action}
        return comments.StoreAnswer(0, {"items": [entry]}, "")

    withdrawal = {"status": "cancelled"}
    for category, state in (("cancelled", None), ("backlog", "failed")):
        alone = {"id": written, "status": {"category": category, "name": category}}
        said = comments.outcome(comments.StoreAnswer(0, alone, ""), written, withdrawal)
        assert said.state == state, (category, said)
    priority = {"id": written, "priority": "high"}
    assert comments.outcome(
        comments.StoreAnswer(0, priority, ""), written, {"priority": "high"}
    ) == (comments.Outcome(None, None, (), None, None))
    unnamed = {"id": written, "status": {"category": "elsewhere"}}
    lost = comments.outcome(comments.StoreAnswer(0, unnamed, ""), written, withdrawal)
    assert lost.state == "unknown" and "no status the store names" in str(lost.error)

    for action in sorted(comments.COPIED):
        assert comments.outcome(copied(action), written, requested).state is None, action
    assert comments.outcome(copied("created"), written, requested).state == "unknown"


def test_an_item_or_its_comments_answered_in_another_shape_are_refused_by_name() -> None:
    board_id, snapshot_id = f"{BOARD}:x", f"{comments.SNAPSHOT_SOURCE}:x"
    for held in (None, [{"id": "c1"}], [{"id": 1, "body": "b"}], ["c1"]):
        with pytest.raises(OSError, match=f"the comments of {snapshot_id} could not be read"):
            comments.detail_comments({"comments": held}, snapshot_id)
    item = {
        "metadata": {comments.ORIGIN_KEY: board_id},
        "title": "A title",
        "status": {"category": "backlog", "name": "Proposal"},
    }

    def detail(**fields: object) -> dict[str, object]:
        return {"items": [{"id": snapshot_id, "item": item | fields}], "comments": []}

    with pytest.raises(OSError, match=f"{snapshot_id} was answered with the task 'another'"):
        comments.detail_item({"items": [{"id": "another", "item": item}]}, snapshot_id)
    with pytest.raises(OSError, match="answered no status the store names"):
        comments.snapshot_issue(
            detail(status={"category": "nonsense"}), board_id, snapshot_id, None
        )
    with pytest.raises(OSError, match="answered a field settle writes at another type"):
        comments.project(item | {"repositories": "github.com/a/b"})

    with pytest.raises(OSError, match=f"{snapshot_id} answered no title"):
        comments.snapshot_issue(detail(title=None), board_id, snapshot_id, None)
    no_origin = detail() | {"comments": [{"id": "c1", "body": "b", "origin": {"id": ""}}]}
    with pytest.raises(OSError, match=f"a comment {snapshot_id} holds names no origin id"):
        comments.snapshot_issue(no_origin, board_id, snapshot_id, None)
    two_lines = detail() | {"comments": [{"id": "c1", "body": "b", "author": "a\n- URL: x"}]}
    with pytest.raises(OSError, match="comment 'c1''s author spanning lines"):
        comments.snapshot_issue(two_lines, board_id, snapshot_id, None)
    # An item kept nowhere a path names — a board's — reads as located nowhere.
    assert comments.snapshot_issue(detail(), board_id, snapshot_id, None).location is None
    for path in ("", "a\nb", 3):
        with pytest.raises(OSError, match="which is not a path"):
            comments.snapshot_issue(detail(location={"path": path}), board_id, snapshot_id, None)
    no_time = detail() | {"comments": [{"id": "c1", "body": "b", "updated_at": 3}]}
    with pytest.raises(OSError, match="reports no time"):
        comments.snapshot_issue(no_time, board_id, snapshot_id, None)


def test_an_intent_file_of_another_shape_is_refused_and_launches_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    intent = tmp_path / "intent.json"
    plan = tmp_path / "plan.json"
    for broken in ("{", "[]", '{"snapshot": null, "fresh": "yes", "detach": false}'):
        intent.write_text(broken, encoding="utf-8")
        with pytest.raises(OSError, match=str(intent)):
            comments.read_intent(intent)
        status = comments.main(
            ["launch", "--root", str(tmp_path), "--intent", str(intent), "--plan", str(plan)]
        )
        assert status == comments.UNRUNNABLE
        assert "nothing was written and nothing was launched" in capsys.readouterr().err
        assert not plan.exists()


def test_snapshot_env_prints_the_source_a_feedback_file_names_or_refuses_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    feedback = tmp_path / "20260101T000000Z.md"
    feedback.write_text(
        comments.FEEDBACK_SNAPSHOT.format(snapshot="/a/snapshot") + "\n", encoding="utf-8"
    )
    assert comments.main(["snapshot-env", "--feedback", str(feedback)]) == comments.DONE
    assert capsys.readouterr().out.splitlines() == [
        f"{comments.SNAPSHOT_PLUGIN_ENV}={comments.SNAPSHOT_PLUGIN}",
        f"{comments.SNAPSHOT_ROOT_ENV}=/a/snapshot/{comments.SNAPSHOT_ITEMS}",
    ]
    feedback.write_text("No snapshot line.\n", encoding="utf-8")
    assert comments.main(["snapshot-env", "--feedback", str(feedback)]) == comments.UNRUNNABLE
    assert "names no snapshot on its snapshot line" in capsys.readouterr().err
    assert (
        comments.main(["snapshot-env", "--feedback", str(tmp_path / "missing.md")])
        == comments.UNRUNNABLE
    )
    assert "could not be read" in capsys.readouterr().err


def test_a_run_scoped_gathering_whose_tickets_are_all_unbound_copies_nothing(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _bound(drafts_root, RUN, "never-copied", None)

    report, plan = _gathered(drafts_root, capsys, "--run", RUN)

    assert plan is not None and plan.snapshot.items == () and plan.runs == ()
    assert f"run {RUN}'s ticket never-copied is bound to no board item; nothing read" in report


def test_writing_a_title_names_it_and_nothing_else(tmp_path: Path) -> None:
    assert comments._update_words(f"{BOARD}:x", {"title": "A title"}, tmp_path) == [
        "task",
        "update",
        f"{BOARD}:x",
        "--title",
        "A title",
    ]
