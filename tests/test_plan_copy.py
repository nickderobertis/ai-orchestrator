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

import subprocess
from collections.abc import Sequence

import pytest

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


def _reads(monkeypatch: pytest.MonkeyPatch, *tasks: StoreTask) -> None:
    monkeypatch.setattr(plan_store, "read_tasks", lambda _project: list(tasks))


def _holds(monkeypatch: pytest.MonkeyPatch, *documents: StoreDocument) -> None:
    monkeypatch.setattr(plan_store, "read_documents", lambda _project: list(documents))


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


def test_a_store_cli_this_checkout_does_not_have_is_reported_as_a_host_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Also unreachable from a journey: this checkout provisions the CLI it reads."""

    def missing() -> str:
        raise OSError("onetaskgraph is not installed on PATH")

    monkeypatch.setattr(plan_store, "store_binary", missing)

    assert plan_copy.copy("authoring:demo", "plans", []) == plan_copy.UNREADABLE
    reported = capsys.readouterr().err
    assert "not installed on PATH" in reported, reported
    assert "just bootstrap" in reported, reported


def test_the_store_refusing_the_copy_is_a_different_answer_from_an_unreviewed_plan(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The two refusals a caller has to tell apart carry two exit statuses."""
    monkeypatch.setattr(plan_store, "store_binary", lambda: "/test/onetaskgraph")
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_k: subprocess.CompletedProcess(args=[], returncode=1)
    )

    assert plan_copy.copy("authoring:demo", "plans", []) == plan_copy.COPY_REFUSED
    assert plan_copy.COPY_REFUSED != plan_copy.UNREVIEWED
    reported = capsys.readouterr().err
    assert "the destination refusing the copy rather than the plan being unreviewed" in reported


def test_a_successful_copy_runs_the_store_s_own_verb_and_says_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The verb's per-record report is the product, so this adds no line of its own."""
    invoked: list[tuple[list[str], dict[str, object]]] = []

    def spawn(command: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        invoked.append((list(command), kwargs))
        return subprocess.CompletedProcess(args=list(command), returncode=0)

    monkeypatch.setattr(plan_store, "store_binary", lambda: "/test/onetaskgraph")
    monkeypatch.setattr(subprocess, "run", spawn)
    _holds(monkeypatch)

    assert plan_copy.copy("authoring:demo", "plans", ["--dry-run"]) == plan_copy.OK
    (command, kwargs) = invoked[0]
    assert command == [
        "/test/onetaskgraph",
        "project",
        "copy",
        "authoring:demo",
        "--to",
        "plans",
        "--dry-run",
    ]
    assert kwargs["cwd"] == plan_copy.REPO_ROOT
    assert len(invoked) == 1, (
        "a plan holding no document still asked the store to copy documents, and "
        "`document copy` requires at least one id"
    )
    assert capsys.readouterr().err == ""


def test_the_plans_documents_go_over_after_the_project_lands(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`project copy` carries no document, and the design document is what a person approves.

    So a plan copied without it arrives on the destination with nothing to approve and
    can never be launched. Ordered after the project copy rather than beside it: a
    document is a document *of* a project, and the destination has to hold the project
    before it can hold one.
    """
    invoked: list[list[str]] = []

    def spawn(command: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        invoked.append(list(command))
        return subprocess.CompletedProcess(args=list(command), returncode=0)

    monkeypatch.setattr(plan_store, "store_binary", lambda: "/test/onetaskgraph")
    monkeypatch.setattr(subprocess, "run", spawn)
    _holds(monkeypatch, _document("demo-design"), _document("demo-notes"))

    assert plan_copy.copy("authoring:demo", "plans", []) == plan_copy.OK
    assert invoked[0][:3] == ["/test/onetaskgraph", "project", "copy"]
    assert invoked[1] == [
        "/test/onetaskgraph",
        "document",
        "copy",
        "authoring:demo-design",
        "authoring:demo-notes",
        "--to",
        "plans",
    ]
    assert capsys.readouterr().err == ""


def test_a_trial_run_writes_no_document_either(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--dry-run` says the whole command writes nothing, so it reaches both calls.

    The one flag of the pass-through this command has an opinion about, and the reason
    it has one: a document copy that ignored it would write while the command it belongs
    to reported that it had not.
    """
    invoked: list[list[str]] = []

    def spawn(command: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        invoked.append(list(command))
        return subprocess.CompletedProcess(args=list(command), returncode=0)

    monkeypatch.setattr(plan_store, "store_binary", lambda: "/test/onetaskgraph")
    monkeypatch.setattr(subprocess, "run", spawn)
    _holds(monkeypatch, _document("demo-design"))

    assert plan_copy.copy("authoring:demo", "plans", ["--dry-run", "--recreate"]) == plan_copy.OK
    assert invoked[1][-1] == plan_copy.DRY_RUN
    assert "--recreate" not in invoked[1], (
        "a flag of the project copy's own reached `document copy`, which is a second "
        "reading of the store's flag grammar rather than a pass-through"
    )


def test_documents_that_could_not_be_copied_are_reported_against_the_landed_plan(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The plan landed and its document did not, which is a state an operator has to know.

    Unreachable from a journey without breaking the store between the project copy and
    the document copy, which is what this stands in for.
    """

    def spawn(command: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        returncode = 0 if list(command)[1:3] == ["project", "copy"] else 1
        return subprocess.CompletedProcess(args=list(command), returncode=returncode)

    monkeypatch.setattr(plan_store, "store_binary", lambda: "/test/onetaskgraph")
    monkeypatch.setattr(subprocess, "run", spawn)
    _holds(monkeypatch, _document("demo-design"))

    assert plan_copy.copy("authoring:demo", "plans", []) == plan_copy.COPY_REFUSED
    reported = capsys.readouterr().err
    assert "the plan landed in 'plans'" in reported, reported
    assert "nothing on 'plans' for a person to approve" in reported, reported


def test_documents_that_could_not_be_read_are_reported_against_the_landed_plan(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other half of that state: the plan landed and the store stopped answering."""

    def unreadable(_project: str) -> list[StoreDocument]:
        raise OSError("onetaskgraph exited 1")

    monkeypatch.setattr(plan_store, "store_binary", lambda: "/test/onetaskgraph")
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_k: subprocess.CompletedProcess(args=[], returncode=0)
    )
    monkeypatch.setattr(plan_store, "read_documents", unreadable)

    assert plan_copy.copy("authoring:demo", "plans", []) == plan_copy.COPY_REFUSED
    reported = capsys.readouterr().err
    assert "its documents could not be read" in reported, reported
