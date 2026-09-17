"""Which of a person's board comments are feedback for a run, and the file they are written as.

`tests/plan_tooling/test_follow_ups_handle_comments_recipe_e2e.py` drives the recipe through a
real launch. What is proven here is the selection one boundary at a time, against the
installed `onetaskgraph`: a drafts root and a second local store standing in for the board,
both named through the store's environment layer, tickets copied and comments written through
the store's own verbs. Only the malformed answers a real store never gives are put in the
store's place, the way `tests/test_plan_store.py` refuses a listing it cannot account for.

Comment times are the store's own and whole seconds, so a test that needs one comment to be
older than a response waits for the clock to pass a second between them.
"""

from __future__ import annotations

import dataclasses
import time
from datetime import UTC, datetime
from pathlib import Path

import follow_up_variables
import pytest

from orchestrator import follow_up_comments as comments
from orchestrator import follow_up_tickets as tickets
from orchestrator import plan_store
from orchestrator.plan_store import WRITABLE_PLUGIN

#: The local store standing in for the board, spelled lowercase because it is spelled into
#: the store's environment layer as well as onto `--board`.
BOARD = "commentboard"
RUN = "listing-run"
OTHER_RUN = "earlier-run"
THIRD_RUN = "unrelated-run"
CAUSE = "cursor-skips-last-page"
SHARED_CAUSE = "sweep-trailer-omits-a-family"
PERSON = "a-reviewer"


def test_comment_time_and_url_fallbacks_refuse_missing_answers() -> None:
    with pytest.raises(OSError, match="reports no time"):
        comments.moment(None, "comment")
    with pytest.raises(OSError, match="names no offset"):
        comments.moment(datetime(2026, 1, 1), "comment")

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


def _filed(root: Path, run: str, cause: str, body: str = "What the ticket says.") -> str:
    """Write ``run``'s ticket for ``cause`` and copy it onto the board, as the agent does."""
    path = tickets.ticket_path(root, run, cause)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f'title: "some-service: {cause}"\n'
        'status: "backlog"\n'
        "metadata:\n"
        f"  {tickets.KEY}:\n"
        f"    created_by_run: {run}\n"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )
    copied = plan_store.sdk(
        plan_store.client().task_copy([tickets.qualified_id(run, cause)], to=BOARD)
    )
    destination = copied.items[0].root.destination
    assert destination is not None, copied
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


def _next_second() -> None:
    """Wait until the store's whole-second clock has moved past every time written so far."""
    time.sleep(1.1)


def _written(root: Path, capsys: pytest.CaptureFixture[str]) -> str:
    status = comments.main(["feedback", "--root", str(root), "--board", BOARD, RUN])
    captured = capsys.readouterr()
    assert status == comments.WRITTEN, captured.err
    path = Path(captured.out.strip())
    assert path.parent == root / comments.FEEDBACK_DIRECTORY / RUN, path
    return path.read_text(encoding="utf-8")


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(path): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def test_only_a_persons_comments_after_the_runs_last_marked_comment_are_selected(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    others = _filed(drafts_root, OTHER_RUN, SHARED_CAUSE)
    unrelated = _filed(drafts_root, THIRD_RUN, "an-unrelated-cause")
    _next_second()
    _commented(own, "Answered before the run last responded.\n")
    earlier = _commented(others, "Also answered before.\n")
    _next_second()
    _commented(others, tickets.render_comment(RUN, SHARED_CAUSE, "This run's evidence."), None)
    _next_second()
    _commented(own, "The examples still miss page 9 — see ```listing.py```.\n")
    _edited(others, earlier, "Edited after the run responded, so it is new.\n")
    _commented(own, tickets.render_comment(OTHER_RUN, CAUSE, "Another run's comment."), None)
    _commented(unrelated, "On an issue this run neither owns nor commented on.\n")
    before = _snapshot(board)

    written = _written(drafts_root, capsys)

    assert written.count("### Comment ") == 2, written
    assert "The examples still miss page 9" in written
    assert "Edited after the run responded, so it is new." in written
    for absent in ("Answered before", "Also answered before", "evidence", "unrelated"):
        assert absent not in written, written
    assert f"- Author: {PERSON}" in written
    assert f"on `{own}`, this run's issue" in written
    assert f"on `{others}`, run `{OTHER_RUN}`'s issue" in written
    # A body carrying a fence is quoted inside a longer one, so it reaches the task whole.
    assert "````text\nThe examples still miss page 9 — see ```listing.py```.\n````" in written
    assert "- URL: file://" in written and "#comment-" in written
    assert "**Never change a board item's status.**" in written
    assert _snapshot(board) == before, "gathering feedback wrote to the board"


def _replied(issue: str, answers: str, cause: str = CAUSE, run: str = RUN) -> str:
    """``run``'s reply to one comment, posted through the store's own verb as the agent posts it."""
    listed = [
        comment.model_dump(mode="json")
        for comment in plan_store.sdk(plan_store.client().task_comment_list(issue)).comments
    ]
    assert isinstance(listed, list), listed
    (answered,) = [one for one in listed if one["id"] == answers]
    reply = tickets.render_reply(
        run,
        cause,
        answers=answers,
        url=f"{issue}#comment-{answers}",
        author=answered["author"],
        response="Copied the ticket again with that in its examples.",
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
            OTHER_RUN, CAUSE, answers=person, url="u", author=PERSON, response="Theirs."
        ),
        tickets.render_reply(
            RUN, CAUSE, answers="a-comment-gone", url="u", author=None, response="Ours."
        ),
    ):
        _commented(own, body, None)

    written = _written(drafts_root, capsys)

    assert written.count("### Comment ") == 1, written
    assert f"- Comment id: {person}\n" in written
    for absent in ("evidence", "Theirs.", "Ours."):
        assert absent not in written, written


def test_a_replied_comment_is_not_selected_until_a_person_edits_it_after_the_reply(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    others = _filed(drafts_root, OTHER_RUN, SHARED_CAUSE)
    _commented(others, tickets.render_comment(RUN, SHARED_CAUSE, "This run's evidence."), None)
    _next_second()
    answered = _commented(own, "Answered by a reply.\n")
    unanswered = _commented(others, "Nobody replied to this one.\n", "a-maintainer")
    _replied(own, answered)

    written = _written(drafts_root, capsys)

    assert written.count("### Comment ") == 1, written
    assert "Nobody replied to this one." in written, "a reply naming another comment hid it"
    assert f"- Comment id: {unanswered}\n- URL: " in written
    assert "Answered by a reply." not in written

    _next_second()
    _edited(own, answered, "Edited after the reply, so it is asked again.\n")

    edited = _written(drafts_root, capsys)

    assert "Edited after the reply, so it is asked again." in edited
    assert f"- Comment id: {answered}\n" in edited


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
    _commented(own, "Answered by the copy that follows.\n")
    _next_second()
    _filed(drafts_root, RUN, CAUSE, "Copied again, answering it.")
    written_at = tickets.ticket_path(drafts_root, RUN, CAUSE).stat().st_mtime
    # Recorded as the whole second comment times are, which orders every comment alike.
    boundary = datetime.fromtimestamp(written_at, UTC).replace(microsecond=0)
    _next_second()
    missed = _commented(own, "Gathered, and never replied to.\n")
    _next_second()
    first = _written(drafts_root, capsys)
    assert "Gathered, and never replied to." in first
    assert "Answered by the copy that follows." not in first
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
    assert f", each changed after {boundary.strftime(comments.MOMENT_FORMAT)}," in second
    assert len(list(directory.glob("2*.md"))) == 2
    assert comments.first_gathering(drafts_root, RUN) == (stamp, comments.Boundary(boundary))

    # The record is what decides, not a recomputation that happens to agree with it.
    text = first_file.read_text(encoding="utf-8")
    first_file.write_text(text.replace(recorded, comments.boundary_line(None)), encoding="utf-8")
    _next_second()

    third = _written(drafts_root, capsys)

    assert "Answered by the copy that follows." in third, "the recorded boundary was not read"
    assert third.splitlines()[0] == comments.boundary_line(None)


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

    written = _written(drafts_root, capsys)

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

    status = comments.main(["feedback", "--root", str(drafts_root), "--board", BOARD, RUN])

    assert status == comments.UNRUNNABLE
    assert "20260101T000000Z.md records reports 'yesterday'" in capsys.readouterr().err


def test_the_feedback_file_asks_for_an_action_a_reply_and_a_report_per_comment(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    tickets.ticket_path(drafts_root, RUN, CAUSE).unlink()
    identifier = _commented(own, "Please add page 9.\n")

    written = _written(drafts_root, capsys)
    flat = " ".join(written.split())

    (comment,) = plan_store.sdk(plan_store.client().task_comment_list(own)).comments
    listed = comment.model_dump(mode="json")
    assert f"- Comment id: {identifier}\n- URL: file://" in written
    assert f"- Author: {PERSON}\n- Last changed: {listed['updated_at']}\n" in written
    for said in (
        "each is quoted verbatim below with its id, its URL, its author and when it last changed",
        '1. **Act on it** under "Ownership on the board" above: perform whatever action it calls '
        "for, or none.",
        "2. **Post its one reply**, naming the comment's id, as those rules state.",
        "3. **Report** the comment's URL beside what you did about it, or why you did nothing.",
        "**Never change a board item's status.** Before every copy, write the status "
        "`board-status` prints",
    ):
        assert said in flat, said
    assert "edit this run's one comment" not in flat, "the feedback restates an ownership rule"
    assert written.index("1. **Act on it**") < written.index("2. **Post its one reply**")


def test_a_comment_before_the_runs_last_ticket_copy_is_not_selected_and_one_after_is(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    _next_second()
    _commented(own, "Answered by the copy that follows.\n")
    _next_second()
    _filed(drafts_root, RUN, CAUSE, "What the ticket says, answering the comment.")
    _next_second()
    _commented(own, "A new question after that copy.\n")

    written = _written(drafts_root, capsys)

    assert "A new question after that copy." in written
    assert "Answered by the copy that follows." not in written
    assert ", each changed after " in written


def test_a_run_that_never_responded_has_every_persons_comment_selected(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    # The ticket file is gone from this host, so nothing says when the run last copied it.
    tickets.ticket_path(drafts_root, RUN, CAUSE).unlink()
    _commented(own, "Written with no author named.\n", None)

    written = _written(drafts_root, capsys)

    assert "changed after" not in written
    assert f"- Author: {comments.UNKNOWN_AUTHOR}" in written


def test_a_run_owning_nothing_on_the_board_is_refused_naming_it(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _commented(_filed(drafts_root, OTHER_RUN, SHARED_CAUSE), "Not about this run.\n")

    status = comments.main(["feedback", "--root", str(drafts_root), "--board", BOARD, RUN])

    captured = capsys.readouterr()
    assert status == comments.NOTHING_NEW
    assert f"run {RUN} owns no follow-up issue on the {BOARD!r} board" in captured.err
    assert "nothing was launched" in captured.err
    assert not (drafts_root / comments.FEEDBACK_DIRECTORY).exists()


def test_a_run_with_no_new_comment_is_refused_naming_it_and_its_last_response(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    _commented(own, tickets.render_comment(OTHER_RUN, CAUSE, "Another run's."), None)
    _next_second()
    _filed(drafts_root, RUN, CAUSE, "Copied again, after every comment.")

    status = comments.main(["feedback", "--root", str(drafts_root), "--board", BOARD, RUN])

    captured = capsys.readouterr()
    assert status == comments.NOTHING_NEW
    assert f"run {RUN} has no new feedback: of the 1 comment(s) on the 1 issue(s)" in captured.err
    assert (
        "none is a person's that no reply of this run answers and that changed after "
        in captured.err
    )


def test_a_run_whose_every_comment_is_a_runs_own_is_refused_when_it_never_responded(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    tickets.ticket_path(drafts_root, RUN, CAUSE).unlink()
    _commented(own, tickets.render_comment(OTHER_RUN, CAUSE, "Another run's."), None)

    status = comments.main(["feedback", "--root", str(drafts_root), "--board", BOARD, RUN])

    assert status == comments.NOTHING_NEW
    refusal = capsys.readouterr().err
    assert "none is a person's that no reply of this run answers" in refusal
    assert "changed after" not in refusal


def test_two_feedback_files_in_one_second_are_both_kept(drafts_root: Path, board: Path) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    tickets.ticket_path(drafts_root, RUN, CAUSE).unlink()
    _commented(own, "One comment.\n")
    now = datetime(2026, 1, 1, tzinfo=UTC)

    first = comments.feedback(drafts_root, BOARD, RUN, now)
    second = comments.feedback(drafts_root, BOARD, RUN, now)

    assert (first.name, second.name) == ("20260101T000000Z.md", "20260101T000000Z-2.md")


def test_an_invocation_that_cannot_run_is_its_own_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert comments.main(["feedback", "--root", str(tmp_path), "--board", BOARD, "a/b"]) == (
        comments.UNRUNNABLE
    )
    assert "'a/b' is not a run id" in capsys.readouterr().err
    with pytest.raises(SystemExit) as refused:
        comments.main(["feedback"])
    assert refused.value.code == comments.UNRUNNABLE
