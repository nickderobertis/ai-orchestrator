"""How a landed plan is found in a destination, and what is refused rather than guessed.

Where a destination holds a copied plan is driven for real in
`tests/plan_tooling/test_finish_plan_recipe_e2e.py`, against the real recipe, the real
store CLI, and a real second source. What is proven here is the correspondence itself and
the three answers it declines to give — a destination that holds no copy of this plan,
one that holds two, and one whose store cannot be read at all. Each of those is a state a
journey could only reach by leaving a destination in it, and each one has to be a refusal
rather than a location: a reviewer sent to the wrong record reads somebody else's plan and
has no way to tell.

The correspondence is the point. A destination names its own records — a board mints a
number where a directory keeps the name — so nothing about the source's id survives the
copy for a reader to compose an address out of. What survives is the origin stamp the
store writes onto every record it creates by copying, and that is what is read here.
"""

from __future__ import annotations

import pytest

from orchestrator import design_approval, plan_locations, plan_store
from orchestrator.plan_store import (
    QualifiedDocumentId,
    QualifiedProjectId,
    StoreDocument,
    StoreProject,
)

#: The plan this module's destination is asked about, and the one it was drafted in.
DRAFTED = "authoring:cursor-shape"


def _project(
    qualified: str,
    origin: str | None = DRAFTED,
    location: dict[str, object] | None = None,
) -> StoreProject:
    """A project in the shape the store reader returns, carrying whatever origin it is given."""
    return StoreProject(
        qualified_id=QualifiedProjectId(qualified),
        title="Deliver the checkout route",
        metadata={} if origin is None else {plan_store.ORIGIN_KEY: origin},
        location=location,
    )


def _document(qualified: str, location: dict[str, object] | None) -> StoreDocument:
    """A design document in the shape the store reader returns."""
    return StoreDocument(
        qualified_id=QualifiedDocumentId(qualified),
        title="Design: the checkout route",
        content="## What\n\nIt.\n",
        project="42",
        labels=[],
        repositories=[],
        metadata={},
        location=location,
    )


def _holds(
    monkeypatch: pytest.MonkeyPatch,
    projects: list[StoreProject],
    documents: list[StoreDocument] | None = None,
) -> None:
    """Answer the destination with ``projects``, and each project's own ``documents``."""
    monkeypatch.setattr(plan_store, "read_projects", lambda _source: list(projects))
    monkeypatch.setattr(plan_store, "read_documents", lambda _project: list(documents or []))


def test_the_landed_plan_is_found_by_the_stamp_the_store_wrote_rather_than_by_its_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The destination's own id is nothing like the source's, and that is the ordinary case.

    A board mints a number for a project a directory would have kept the name of, so a
    reader that composed `<destination>:<the source's native id>` would address a record
    that does not exist — on the one destination this repository actually copies into.
    What ties the two together is the origin stamp, and this reads a destination whose
    every id differs from the source's to prove nothing else is being used.
    """
    _holds(
        monkeypatch,
        [
            _project("board:9", origin="authoring:something-else"),
            _project("board:42", location={"url": "https://example.invalid/board/42"}),
        ],
        [_document("board:57", {"url": "https://example.invalid/board/57"})],
    )

    assert plan_locations.main([DRAFTED, "--in", "board"]) == plan_locations.OK
    reported = capsys.readouterr().out
    assert "board holds the plan at https://example.invalid/board/42" in reported, reported
    assert "board holds its design document at https://example.invalid/board/57" in reported, (
        reported
    )


def test_a_location_the_store_reports_as_a_path_is_reported_as_that_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A directory answers a path where a board answers a link, and both are the store's.

    The two forms are the whole reason nothing here composes a location: one destination
    of this repository is a website and the other is a directory on this machine, and a
    reviewer opens whichever the store named.
    """
    _holds(
        monkeypatch,
        [_project("local:cursor-shape", location={"path": "/plans/projects/cursor-shape.md"})],
        [_document("local:cursor-shape-design", {"path": "/plans/documents/design.md"})],
    )

    assert plan_locations.main([DRAFTED, "--in", "local"]) == plan_locations.OK
    reported = capsys.readouterr().out
    assert "local holds the plan at /plans/projects/cursor-shape.md" in reported, reported
    assert "local holds its design document at /plans/documents/design.md" in reported, reported


def test_a_record_the_store_reports_no_location_for_is_named_by_its_own_address(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A store that says where nothing is still leaves a reviewer an id they can ask about.

    The fallback is the record's own qualified id rather than silence or a composed guess:
    it addresses the record on every surface the store has, which is what somebody has to
    hand when they cannot simply open it.
    """
    _holds(
        monkeypatch,
        [_project("board:42", location=None)],
        [_document("board:57", None)],
    )

    assert plan_locations.main([DRAFTED, "--in", "board"]) == plan_locations.OK
    reported = capsys.readouterr().out
    assert "board holds the plan at board:42" in reported, reported
    assert "board holds its design document at board:57" in reported, reported


def test_a_destination_holding_no_copy_of_this_plan_is_refused_rather_than_answered(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing to point a reviewer at is a refusal, and it names the plan that is missing.

    The state is reachable: a copy that was refused halfway, or a destination somebody
    repointed between the copy and this read. Answering it with the first project the
    destination happens to hold would send a reviewer to an unrelated plan.
    """
    _holds(monkeypatch, [_project("board:9", origin="authoring:something-else")])

    assert plan_locations.main([DRAFTED, "--in", "board"]) == plan_locations.UNREADABLE
    refusal = capsys.readouterr().err
    assert "holds no project the store records as copied from authoring:cursor-shape" in refusal, (
        refusal
    )


def test_a_destination_holding_two_copies_of_one_plan_is_refused_and_names_both(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Which one a reviewer should open is not this command's to guess.

    Two copies of one plan in one destination is a state somebody has to resolve —
    a `--recreate` beside a record that was already there is how it arrives — and
    reporting whichever this walked into first is the answer that reads as sound from
    every side afterwards.
    """
    _holds(monkeypatch, [_project("board:42"), _project("board:88")])

    assert plan_locations.main([DRAFTED, "--in", "board"]) == plan_locations.UNREADABLE
    refusal = capsys.readouterr().err
    assert "board:42" in refusal and "board:88" in refusal, refusal
    assert "is not this command's to guess" in refusal, refusal


def test_a_destination_whose_store_cannot_be_read_is_the_same_refusal_as_a_missing_copy(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One status for every way this cannot answer, because the next action is one thing.

    The copy has already landed and reported what it wrote by the time this runs, so what
    an operator does about a destination that will not answer is read that report and ask
    the store themselves — the same thing they do about a destination that holds no copy.
    """

    def refuses(_source: str) -> list[StoreProject]:
        raise OSError("the board answered nothing")

    monkeypatch.setattr(plan_store, "read_projects", refuses)

    assert plan_locations.main([DRAFTED, "--in", "board"]) == plan_locations.UNREADABLE
    refusal = capsys.readouterr().err
    assert "the board answered nothing" in refusal, refusal
    assert "per-record report" in refusal, refusal


def test_a_landed_plan_with_no_design_document_is_refused_by_the_reader_that_owns_that_question(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The document half is `orchestrator/design_approval.py`'s question, asked once.

    Which of a project's documents is *the* design document, and what several of them
    mean, is decided there because that is what an approval is recorded against. Asking it
    a second way here would be a second answer, and the two would disagree the first time
    either moved.
    """
    _holds(monkeypatch, [_project("board:42")], [])

    assert plan_locations.main([DRAFTED, "--in", "board"]) == plan_locations.UNREADABLE
    assert "design document" in capsys.readouterr().err


def test_an_unqualified_plan_id_is_refused_before_the_destination_is_asked_anything(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bare native id names a project in no store, so it can match no origin stamp.

    Refused as the malformed id it is rather than reported as a destination holding no
    copy: the second reading sends an operator to look at the board when what is wrong is
    the argument they typed.
    """

    def never(_source: str) -> list[StoreProject]:
        raise AssertionError("the destination was read for an unqualified plan id")

    monkeypatch.setattr(plan_store, "read_projects", never)

    assert plan_locations.main(["cursor-shape", "--in", "board"]) == plan_locations.UNREADABLE
    assert "qualified" in capsys.readouterr().err


def test_the_two_locations_are_read_through_the_one_renderer_the_store_answer_has(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A project, a task and a document are all located the same way, so it is read once.

    `orchestrator/design_approval.py` renders a document's location for its own refusals
    and this renders a project's; a second reading of that same `location` object would
    answer differently the day the store grows a third form.
    """
    document = _document("board:57", {"url": "https://example.invalid/board/57"})
    assert design_approval.located(document) == plan_store.located(
        document.location, str(document.qualified_id)
    )
    assert plan_store.located({"path": "/x"}, "fallback") == "/x"
    assert plan_store.located({"url": ""}, "fallback") == "fallback"
    assert plan_store.located(None, "fallback") == "fallback"
