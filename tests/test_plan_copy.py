"""What `just copy-plan` decides before and after the store's own copy verb runs.
The flow itself — drafting a plan locally, clearing it there, copying it up, and
checking the copy — is driven for real in
`tests/plan_tooling/test_copy_plan_recipe_e2e.py`, against the real recipe and the real
store CLI. What is proven here is the half no journey can reach without breaking this
checkout: a review bar that cannot be composed, and a store CLI that is not installed.
Both are host failures rather than plan failures, and both have to be told apart from a
plan nothing has reviewed — which is the whole distinction this command exists to make.

The delegation is proven here too rather than only end to end, because it is the one
property a green journey could hide: a pre-flight that ran *after* the copy would still
copy a reviewed plan correctly, and would only be caught by the plan nobody reviewed.
"""

from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace

import pytest
from onetaskgraph_sdk import CopyReport

from orchestrator import plan_copy, plan_review, plan_store
from orchestrator.plan_store import (
    NodeId,
    QualifiedDocumentId,
    QualifiedTaskId,
    StoreDocument,
    StoreTask,
)


def _task(node_id: str, metadata: dict[str, object] | None = None) -> StoreTask:
    """A task in the shape the store reader returns, carrying whatever record it is given."""
    return StoreTask(
        qualified_id=QualifiedTaskId(f"authoring:demo/{node_id}"),
        node_id=NodeId(node_id),
        title=f"feat: add the {node_id}",
        content="## What\n\nAdd it.\n",
        metadata=metadata or {},
        repositories=[],
        deps=(),
    )


def _reviewed(node_id: str) -> StoreTask:
    """A task carrying a record for exactly the content it currently states."""
    bare = _task(node_id)
    key = plan_review.review_key(bare, plan_review.bar_fingerprint())
    return _task(node_id, {plan_review.RECORD_KEY: {"key": key, "by": plan_review.BY_REVIEW}})


def _document(native: str) -> StoreDocument:
    """A document in the shape the store reader returns."""
    return StoreDocument(
        qualified_id=QualifiedDocumentId(f"authoring:{native}"),
        title=f"Design: {native}",
        content="## What\n\nIt.\n",
        project="demo",
        labels=[],
        repositories=[],
        metadata={},
        location={"path": f"/test/documents/{native}.md"},
    )


def _reads(monkeypatch: pytest.MonkeyPatch, *tasks: StoreTask, planned: bool = True) -> None:
    """Answer the store's reads with ``tasks``, and a project record reviewed whole or not.

    The plan is read by the same two store calls `just review-plan` reads it by — the
    plan in the engine's shape, and the project record — so both are answered here, at
    the store CLI boundary, with the project record carrying the plan-level key for
    exactly that plan when ``planned`` and nothing when not.
    """
    plan = {
        "name": "demo",
        "goal": {"text": "Deliver it"},
        "tasks": [{"id": t.node_id} for t in tasks],
    }
    key = plan_review.plan_key(plan, plan_review.plan_bar_fingerprint())
    record = {"metadata": {plan_review.RECORD_KEY: {"key": key}} if planned else {}}
    monkeypatch.setattr(plan_store, "read_tasks", lambda _project: list(tasks))
    monkeypatch.setattr(plan_store, "read_plan", lambda _project, _records: plan)
    monkeypatch.setattr(plan_store, "project_record", lambda _project: record)


def _holds(monkeypatch: pytest.MonkeyPatch, *documents: StoreDocument) -> None:
    monkeypatch.setattr(plan_store, "read_documents", lambda _project: list(documents))


def test_sdk_copy_options_and_reference_summary_are_rendered(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert plan_copy._copy_options(
        [
            "--dry-run",
            "--recreate",
            "--no-tasks",
            "--match-by",
            "title",
            "--member",
            "a",
            "--set",
            "x=y",
            "--page-size",
            "7",
            "--default-sources",
            "a,b",
        ]
    ) == {
        "dry_run": True,
        "recreate": True,
        "no_tasks": True,
        "match_by": "title",
        "member": ["a"],
        "set": ["x=y"],
        "page_size": 7,
        "default_sources": ["a", "b"],
    }
    with pytest.raises(OSError, match="unsupported"):
        plan_copy._copy_options(["--unknown"])
    with pytest.raises(OSError, match="requires an integer"):
        plan_copy._copy_options(["--page-size", "seven"])
    report = CopyReport.model_validate(
        {
            "items": [],
            "references_rewritten": 1,
            "references_unresolved": 2,
            "references_ambiguous": 3,
        }
    )
    plan_copy._report(report)
    assert "references: 1 rewritten, 2 unresolved (3 ambiguous)" in capsys.readouterr().out


def test_a_moved_sdk_copy_signature_is_refused_before_any_argument_is_translated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The translation is held to the SDK's real parameters, so a release that renames
    one fails here by name rather than passing a flag the verb no longer takes."""

    def project_copy(id: str, to: str, dry_run: bool = False) -> None:  # noqa: A002
        raise AssertionError("never called")

    monkeypatch.setattr(plan_store, "client", lambda: SimpleNamespace(project_copy=project_copy))
    with pytest.raises(OSError, match="project-copy parameters changed"):
        plan_copy._copy_options([])


def test_document_read_failure_is_a_destination_refusal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        plan_store,
        "read_documents",
        lambda _project: (_ for _ in ()).throw(OSError("lost the source")),
    )
    assert plan_copy._documents("authoring:demo", "plans", []) == plan_copy.COPY_REFUSED
    assert "lost the source" in capsys.readouterr().err


def test_a_task_nothing_has_reviewed_refuses_the_copy_before_the_store_is_asked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The refusal names each such task, says how a record is obtained, and copies nothing."""
    _reads(monkeypatch, _reviewed("route"), _task("worker"))

    def never(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("the copy ran despite a task carrying no review record")

    monkeypatch.setattr(plan_copy, "copy", never)

    assert plan_copy.main(["authoring:demo"]) == plan_copy.UNREVIEWED
    refusal = capsys.readouterr().err
    assert "worker" in refusal, refusal
    assert "route" not in refusal, refusal
    assert "just review-plan authoring:demo" in refusal, refusal


def test_a_plan_reviewed_task_by_task_but_never_whole_refuses_the_copy_before_the_store_is_asked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every task carries a record and the project carries none: the copy is refused.

    The plan-level record is what says a reviewer read the plan whole for the adoption
    its goal needs, and a plan copied without it arrives on a board that can never
    give it one. Refused with the per-task exit status, before anything is written, and
    naming the command that records it.
    """
    _reads(monkeypatch, _reviewed("route"), planned=False)

    def never(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("the copy ran despite the project carrying no plan-level record")

    monkeypatch.setattr(plan_copy, "copy", never)

    assert plan_copy.main(["authoring:demo"]) == plan_copy.UNREVIEWED
    refusal = capsys.readouterr().err
    assert "no plan-level one" in refusal, refusal
    assert "just review-plan authoring:demo" in refusal, refusal
    assert "Nothing was copied" in refusal, refusal


def test_a_project_record_the_store_cannot_answer_is_neither_reviewed_nor_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The third outcome again, one read later: the store stopped answering."""
    _reads(monkeypatch, _reviewed("route"))
    monkeypatch.setattr(
        plan_store,
        "project_record",
        lambda _project: (_ for _ in ()).throw(OSError("the store answered nothing")),
    )
    monkeypatch.setattr(plan_copy, "copy", lambda *_: pytest.fail("the copy ran"))

    assert plan_copy.main(["authoring:demo"]) == plan_copy.UNREADABLE
    reported = capsys.readouterr().err
    assert "cannot read the plan-level review record of authoring:demo" in reported, reported
    assert "the store answered nothing" in reported, reported
    assert "nothing was copied" in reported, reported


def test_a_fully_reviewed_plan_reaches_the_store_with_the_caller_s_own_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--to` selects the destination and everything else reaches the copy verb untouched."""
    _reads(monkeypatch, _reviewed("route"))
    seen: dict[str, object] = {}

    def record(project: str, destination: str, passthrough: Sequence[str]) -> int:
        seen.update(project=project, destination=destination, passthrough=list(passthrough))
        return plan_copy.OK

    monkeypatch.setattr(plan_copy, "copy", record)

    assert (
        plan_copy.main(["authoring:demo", "--to", "elsewhere", "--dry-run", "--match-by", "title"])
        == plan_copy.OK
    )
    assert seen == {
        "project": "authoring:demo",
        "destination": "elsewhere",
        "passthrough": ["--dry-run", "--match-by", "title"],
    }


def test_naming_no_destination_copies_onto_the_board_this_repository_plans_against(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reads(monkeypatch, _reviewed("route"))
    seen: list[str] = []
    monkeypatch.setattr(
        plan_copy, "copy", lambda _p, destination, _t: seen.append(destination) or plan_copy.OK
    )

    assert plan_copy.main(["authoring:demo"]) == plan_copy.OK
    assert seen == [plan_copy.BOARD]


def test_a_plan_that_cannot_be_read_is_reported_as_neither_reviewed_nor_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A store that cannot answer is a third outcome, not an unreviewed plan."""

    def refuse(_project: str) -> list[StoreTask]:
        raise OSError('no source named "nope" is configured')

    monkeypatch.setattr(plan_store, "read_tasks", refuse)

    assert plan_copy.main(["nope:demo"]) == plan_copy.UNREADABLE
    reported = capsys.readouterr().err
    assert "cannot read project nope:demo" in reported, reported
    assert "nothing was copied" in reported, reported


def test_a_review_bar_this_checkout_cannot_compose_stops_before_it_judges_anything(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bar composed from files this checkout is missing says so, and copies nothing.

    Unreachable from a journey by construction: the bar is this repository's own tracked
    files, so a recipe run from here always has one.
    """
    _reads(monkeypatch, _task("route"))

    def refuse(_records: Sequence[StoreTask]) -> list[StoreTask]:
        raise OSError("personas/planner.yaml is missing")

    monkeypatch.setattr(plan_review, "unreviewed", refuse)

    assert plan_copy.main(["authoring:demo"]) == plan_copy.UNREADABLE
    reported = capsys.readouterr().err
    assert "cannot fingerprint the review bar" in reported, reported
    assert "just bootstrap" in reported, reported
