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

import time
from collections.abc import Callable, Sequence
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
    copied = plan_store.store_json(
        ["task", "copy", tickets.qualified_id(run, cause), "--to", BOARD]
    )
    destination = copied["items"][0]["destination"]
    assert isinstance(destination, str), copied
    return destination


def _commented(issue: str, body: str, author: str | None = PERSON) -> str:
    """Add a comment through the store's own verb; its id."""
    path = Path(plan_store.source_root(BOARD)).parent / f"comment-{time.monotonic_ns()}.md"
    path.write_text(body, encoding="utf-8")
    arguments = ["task", "comment", "add", issue, "--body-file", str(path)]
    added = plan_store.store_json([*arguments, *(["--author", author] if author else [])])
    path.unlink()
    identifier = added["id"]
    assert isinstance(identifier, str), added
    return identifier


def _edited(issue: str, identifier: str, body: str) -> None:
    path = Path(plan_store.source_root(BOARD)).parent / f"edit-{time.monotonic_ns()}.md"
    path.write_text(body, encoding="utf-8")
    plan_store.store_json(["task", "comment", "edit", issue, identifier, "--body-file", str(path)])
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
    assert "after the run last responded, at " in written


def test_a_run_that_never_responded_has_every_persons_comment_selected(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    # The ticket file is gone from this host, so nothing says when the run last copied it.
    tickets.ticket_path(drafts_root, RUN, CAUSE).unlink()
    _commented(own, "Written with no author named.\n", None)

    written = _written(drafts_root, capsys)

    assert "while the run has not yet responded on the board" in written
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
    assert "none is a person's newer than its last response at " in captured.err


def test_a_run_whose_every_comment_is_a_runs_own_is_refused_when_it_never_responded(
    drafts_root: Path, board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    tickets.ticket_path(drafts_root, RUN, CAUSE).unlink()
    _commented(own, tickets.render_comment(OTHER_RUN, CAUSE, "Another run's."), None)

    status = comments.main(["feedback", "--root", str(drafts_root), "--board", BOARD, RUN])

    assert status == comments.NOTHING_NEW
    assert "none is a person's that no run's marker owns" in capsys.readouterr().err


def test_two_feedback_files_in_one_second_are_both_kept(drafts_root: Path, board: Path) -> None:
    own = _filed(drafts_root, RUN, CAUSE)
    tickets.ticket_path(drafts_root, RUN, CAUSE).unlink()
    _commented(own, "One comment.\n")
    now = datetime(2026, 1, 1, tzinfo=UTC)

    first = comments.feedback(drafts_root, BOARD, RUN, now)
    second = comments.feedback(drafts_root, BOARD, RUN, now)

    assert (first.name, second.name) == ("20260101T000000Z.md", "20260101T000000Z-2.md")


def _answering(
    listing: dict[str, object], listed: object
) -> Callable[[Sequence[str]], dict[str, object]]:
    def answer(arguments: Sequence[str]) -> dict[str, object]:
        return listing if arguments[:2] == ["task", "list"] else {"comments": listed}

    return answer


def _item(**extra: object) -> dict[str, object]:
    return {"title": "t", "metadata": {tickets.KEY: {"created_by_run": RUN}}, **extra}


COMMENT = {"id": "c-1", "body": "b", "created_at": "2026-01-01T00:00:00Z"}


@pytest.mark.parametrize(
    ("listing", "listed", "expected"),
    [
        ({"items": ["not an object"]}, [], "listed an item without an id and a payload"),
        ({"items": [{"id": f"{BOARD}:x"}]}, [], "listed an item without an id and a payload"),
        ({"items": [{"id": f"{BOARD}:x", "item": _item()}]}, "no", "as something not a list"),
        ({"items": [{"id": f"{BOARD}:x", "item": _item()}]}, [1], "that is not an object"),
        ({"items": [{"id": "elsewhere:x", "item": _item()}]}, [], "which is not one of its ids"),
        ({"items": [{"id": f"{BOARD}:", "item": _item()}]}, [], "which is not one of its ids"),
        ({"items": [{"id": f"{BOARD}:x", "item": _item(title=None)}]}, [], "without a title"),
        (
            {"items": [{"id": f"{BOARD}:x", "item": _item()}]},
            [{**COMMENT, "body": None}],
            "comment 'c-1' on commentboard:x has no text",
        ),
        (
            {"items": [{"id": f"{BOARD}:x", "item": _item()}]},
            [{**COMMENT, "author": 7}],
            "the author of comment 'c-1' on commentboard:x is not a string",
        ),
        (
            {"items": [{"id": f"{BOARD}:x", "item": _item(location={"path": "/b.md"})}]},
            [{**COMMENT, "created_at": None}],
            "reports no time",
        ),
        (
            {"items": [{"id": f"{BOARD}:x", "item": _item(location={"path": "/b.md"})}]},
            [{**COMMENT, "updated_at": "yesterday"}],
            "which is not an RFC 3339 time",
        ),
        (
            {"items": [{"id": f"{BOARD}:x", "item": _item(location={"path": "/b.md"})}]},
            [{**COMMENT, "created_at": "2026-01-01T00:00:00"}],
            "which names no offset",
        ),
        (
            {"items": [{"id": f"{BOARD}:x", "item": _item(location={"path": "/b.md"})}]},
            [{**COMMENT, "id": None}],
            "a comment on commentboard:x without an id",
        ),
        (
            {"items": [{"id": f"{BOARD}:x", "item": _item(location="nowhere")}]},
            [COMMENT],
            "reports no URL and no location",
        ),
    ],
)
def test_a_board_answer_this_cannot_account_for_is_unrunnable(
    listing: dict[str, object],
    listed: object,
    expected: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Another program's records, narrowed at the boundary rather than trusted past it."""
    monkeypatch.setattr(plan_store, "store_json", _answering(listing, listed))

    status = comments.main(["feedback", "--root", str(tmp_path), "--board", BOARD, RUN])

    assert status == comments.UNRUNNABLE
    refusal = capsys.readouterr().err
    assert expected in refusal
    assert "Check that --board names a source" in refusal and refusal.rstrip().endswith("retry")


def test_a_url_the_board_reports_is_the_one_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hosted board names each comment's URL, or at least its issue's."""
    issue = "https://github.com/nickderobertis/some-service/issues/7"
    listing = {
        "items": [
            {"id": f"{BOARD}:hosted", "item": _item(url=issue)},
            {"id": f"{BOARD}:unowned", "item": {"title": "u", "metadata": {}}},
        ]
    }
    own = {**COMMENT, "id": "c-2", "url": f"{issue}#issuecomment-99"}
    monkeypatch.setattr(plan_store, "store_json", _answering(listing, [COMMENT, own]))

    written = comments.feedback(tmp_path, BOARD, RUN, datetime(2026, 1, 2, tzinfo=UTC))

    text = written.read_text(encoding="utf-8")
    assert f"- URL: {issue}#comment-c-1\n" in text
    assert f"- URL: {issue}#issuecomment-99\n" in text


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
