"""Saved SDK payloads at the ordinary copy/read trust boundary, without SDK doubles."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from onetaskgraph_sdk import CopyReport, QueryResponseOfQualifiedProject

from orchestrator import plan_store, spike_brief_flow

FIXTURES = Path(__file__).parent / "fixtures/spike-brief-sdk"


@pytest.mark.parametrize("kind", ["copy-report", "project-show"])
@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "missing",
        "ambiguous",
        "outside",
        "authoring",
        "authoring-agrees",
        "mismatch",
        "unrecorded",
    ],
)
def test_sdk_payload_must_return_one_recorded_plans_project(kind: str, fault: str) -> None:
    raw = json.loads((FIXTURES / (kind + ".json")).read_text())
    key = "destination" if kind == "copy-report" else "id"
    expected: str | None = "plans:board"
    match fault:
        case "missing":
            raw["items"] = []
        case "ambiguous":
            raw["items"] *= 2
        case "outside":
            raw["items"][0][key] = "elsewhere:board"
        case "authoring" | "authoring-agrees":
            raw["items"][0][key] = "authoring:board"
            if fault == "authoring-agrees":
                expected = "authoring:board"
        case "mismatch":
            raw["items"][0][key] = "plans:unrelated"
        case "unrecorded":
            expected = None
    payload = (
        CopyReport.model_validate(raw)
        if kind == "copy-report"
        else QueryResponseOfQualifiedProject.model_validate(raw)
    )
    source = plan_store.QualifiedProjectId("authoring:preview")
    if fault == "none":
        assert spike_brief_flow.recorded_project(payload, source, expected) == "plans:board"
    else:
        with pytest.raises(ValueError, match="expected one plans project recorded as") as refused:
            spike_brief_flow.recorded_project(payload, source, expected)
        assert "SDK returned" in str(refused.value)


def test_a_failed_copy_item_records_no_board_project() -> None:
    """A copy that failed for the project reached no destination, so nothing is recorded."""
    raw = json.loads((FIXTURES / "copy-report.json").read_text())
    failed = {
        "source": "authoring:preview",
        "action": "failed",
        "error": {
            "class": "transient",
            "kind": "rate-limited",
            "message": "secondary rate limit",
            "retry_after_seconds": None,
            "source": "plans",
        },
    }
    source = plan_store.QualifiedProjectId("authoring:preview")
    with pytest.raises(ValueError, match=r"SDK returned \[\]"):
        spike_brief_flow.recorded_project(
            CopyReport.model_validate({"items": [failed]}), source, "plans:board"
        )
    beside = CopyReport.model_validate({"items": [failed, *raw["items"]]})
    assert spike_brief_flow.recorded_project(beside, source, "plans:board") == "plans:board"


def test_a_skipped_copy_item_records_no_board_project() -> None:
    """A copy the store skipped for the project reached no destination, so nothing is recorded."""
    raw = json.loads((FIXTURES / "copy-report.json").read_text())
    skipped = {
        "source": "authoring:preview",
        "action": "skipped",
        "reason": "status proposed is not one of todo",
    }
    source = plan_store.QualifiedProjectId("authoring:preview")
    with pytest.raises(ValueError, match=r"SDK returned \[\]"):
        spike_brief_flow.recorded_project(
            CopyReport.model_validate({"items": [skipped]}), source, "plans:board"
        )
    beside = CopyReport.model_validate({"items": [skipped, *raw["items"]]})
    assert spike_brief_flow.recorded_project(beside, source, "plans:board") == "plans:board"


def test_a_copy_item_of_another_source_records_no_board_project() -> None:
    """Only the item copied from the plan's own source names the board project it reached."""
    raw = json.loads((FIXTURES / "copy-report.json").read_text())
    other = {"source": "authoring:other", "action": "created", "destination": "plans:other"}
    source = plan_store.QualifiedProjectId("authoring:preview")
    with pytest.raises(ValueError, match=r"SDK returned \[\]"):
        spike_brief_flow.recorded_project(
            CopyReport.model_validate({"items": [other]}), source, "plans:board"
        )
    beside = CopyReport.model_validate({"items": [other, *raw["items"]]})
    assert spike_brief_flow.recorded_project(beside, source, "plans:board") == "plans:board"


def test_a_dry_run_copy_item_records_no_board_project() -> None:
    """A copy that would create the project created nothing, so it names no destination."""
    raw = json.loads((FIXTURES / "copy-report.json").read_text())
    would_create = {"source": "authoring:preview", "action": "created", "destination": None}
    source = plan_store.QualifiedProjectId("authoring:preview")
    with pytest.raises(ValueError, match=r"SDK returned \[\]"):
        spike_brief_flow.recorded_project(
            CopyReport.model_validate({"items": [would_create]}), source, "plans:board"
        )
    beside = CopyReport.model_validate({"items": [would_create, *raw["items"]]})
    assert spike_brief_flow.recorded_project(beside, source, "plans:board") == "plans:board"


@pytest.mark.parametrize("fault", ["none", "schema", "board", "foreign-document"])
def test_saved_receipt_requires_its_recorded_local_store_context(fault: str) -> None:
    from orchestrator import spike_brief

    raw = json.loads((FIXTURES / "receipt.json").read_text())
    match fault:
        case "schema":
            raw["schema_version"] = 2
        case "board":
            raw["board_project"] = "plans:unrelated"
        case "foreign-document":
            raw["brief"]["document"] = "authoring:foreign-document"
    if fault == "none":
        assert (
            spike_brief.recorded_context(
                spike_brief.validate(raw), "plans:board", ["authoring:preview-brief"]
            )
            == raw
        )
    else:
        expected = {
            "schema": "integer 1",
            "board": "store correspondence",
            "foreign-document": "does not belong to plan_project",
        }
        with pytest.raises(ValueError, match=expected[fault]):
            spike_brief.recorded_context(
                spike_brief.validate(raw), "plans:board", ["authoring:preview-brief"]
            )


def test_saved_delivery_completion_uses_the_recorded_surfacing_without_mutating_input() -> None:
    # A live history must not be altered to manufacture an interruption. This saved-input
    # step is the permitted boundary; real resume journeys prove the unchanged copy/check-in
    # path. The flow calls this exact completion step before its existing-brief return.
    from orchestrator import spike_brief

    raw = json.loads((FIXTURES / "delivery.json").read_text())
    receipt = spike_brief.validate(json.loads((FIXTURES / "receipt.json").read_text()))
    assert receipt["brief"] is not None
    completed = spike_brief.completed_delivery(raw, receipt["brief"]["surfaced_at"])
    assert "delivered" not in raw
    assert completed == {**raw, "delivered": "2026-10-13T00:00:06Z"}
    assert spike_brief.completed_delivery(completed, "2026-10-13T00:01:00Z") == completed


@pytest.mark.parametrize("state", ["missing", "matching", "stale-digest", "stale-run"])
def test_saved_approval_matches_only_the_current_surfaced_identity(state: str) -> None:
    from orchestrator import spike_brief

    raw = json.loads((FIXTURES / "receipt.json").read_text())
    if state != "missing":
        raw["approval"] = {
            "digest": raw["brief"]["digest"],
            "spikes_run": raw["spikes_run"],
            "approved_at": "2026-10-13T00:00:08Z",
        }
        match state:
            case "stale-digest":
                raw["approval"]["digest"] = "0" * 64
            case "stale-run":
                raw["approval"]["spikes_run"] = "other-spikes"
    assert spike_brief.matching_approval(spike_brief.validate(raw)) == (state == "matching")


@pytest.mark.parametrize("state", ["missing", "foreign", "unsurfaced", "malformed", "ambiguous"])
def test_approval_consumer_refuses_saved_unapprovable_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    from orchestrator import spike_brief

    # These are saved consumer inputs, never a launched run's or flow's state.
    monkeypatch.setenv("ONEPIPELINE_RUNS_DIR", str(tmp_path / "saved-receipts"))
    raw = json.loads((FIXTURES / "receipt.json").read_text())
    if state != "missing":
        for parent in ["preview", "second"] if state == "ambiguous" else ["preview"]:
            raw["parent_run"] = parent
            if state == "foreign":
                raw["plan_project"] = "authoring:another-plan"
            raw["board_project"] = None
            raw["brief"] = None
            path = tmp_path / "saved-receipts" / parent / spike_brief.RECEIPT
            path.parent.mkdir(parents=True)
            path.write_text("{}" if state == "malformed" else json.dumps(raw))
    before = {str(path): path.read_bytes() for path in tmp_path.rglob(spike_brief.RECEIPT)}
    expected = (
        "exactly the version 1 fields"
        if state == "malformed"
        else {
            "unsurfaced": "surfaced brief",
            "ambiguous": "expected one receipt, found 2",
        }.get(state, "expected one receipt, found 0")
    )
    with pytest.raises(ValueError, match=expected):
        spike_brief.approve("authoring:preview", raw["manager_session"])
    assert {str(path): path.read_bytes() for path in tmp_path.rglob(spike_brief.RECEIPT)} == before


@pytest.mark.parametrize(
    ("fault", "expected"),
    [
        ("board-document", "brief document must belong to plan_project"),
        ("uncopied", "a surfaced brief needs a successful board copy"),
        ("approval-shape", "approval must carry digest, spikes_run and approved_at"),
    ],
)
def test_saved_receipt_refuses_a_brief_or_approval_outside_its_contract(
    fault: str, expected: str
) -> None:
    from orchestrator import spike_brief

    raw = json.loads((FIXTURES / "receipt.json").read_text())
    match fault:
        case "board-document":
            raw["brief"]["document"] = "plans:preview-brief"
        case "uncopied":
            raw["board_project"] = None
        case "approval-shape":
            raw["approval"] = {"digest": raw["brief"]["digest"]}
    with pytest.raises(ValueError, match=expected):
        spike_brief.validate(raw)


def test_a_receipt_saved_under_another_runs_directory_is_refused(tmp_path: Path) -> None:
    from orchestrator import spike_brief

    raw = json.loads((FIXTURES / "receipt.json").read_text())
    raw["board_project"] = None
    raw["brief"] = None
    path = tmp_path / "another-run" / spike_brief.RECEIPT
    path.parent.mkdir()
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="receipt directory does not match parent_run"):
        spike_brief.read(path)


def test_a_second_nonblocking_holder_of_the_receipt_lock_is_told_it_is_held(
    tmp_path: Path,
) -> None:
    from orchestrator import spike_brief

    path = tmp_path / "preview" / spike_brief.RECEIPT
    with spike_brief.locked(path) as first, spike_brief.locked(path, blocking=False) as second:
        assert first and not second
    with spike_brief.locked(path, blocking=False) as released:
        assert released


def test_a_receipt_that_cannot_replace_its_destination_leaves_no_staged_copy(
    tmp_path: Path,
) -> None:
    from orchestrator import spike_brief

    raw = json.loads((FIXTURES / "receipt.json").read_text())
    raw["board_project"] = None
    raw["brief"] = None
    path = tmp_path / "preview" / spike_brief.RECEIPT
    # A directory where the receipt belongs is what a replace cannot write over.
    path.mkdir(parents=True)
    with pytest.raises(OSError):
        spike_brief.write(path, spike_brief.validate(raw))
    assert sorted(entry.name for entry in path.parent.iterdir()) == [spike_brief.RECEIPT]


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        ({"overview": "Measure the interfaces."}, "brief answers must be overview and spikes"),
        ({"overview": "Measure the interfaces.", "spikes": []}, "spikes must be a nonempty list"),
        (
            {"overview": "Measure the interfaces.", "spikes": [{"spike": "spike-agent-0"}]},
            "each spike must carry spike, question and use",
        ),
    ],
)
def test_answers_out_of_the_template_shape_are_refused(
    answers: dict[str, object], expected: str
) -> None:
    from orchestrator import spike_brief

    with pytest.raises(ValueError, match=expected):
        spike_brief.answers(answers, ["spike-agent-0"], "Measure the interfaces.")


@pytest.mark.parametrize("origin", ["authoring:preview", "authoring:another-plan"])
def test_a_shown_board_project_must_be_the_copy_of_its_source(origin: str) -> None:
    raw = json.loads((FIXTURES / "project-show.json").read_text())
    raw["items"][0]["item"]["metadata"][plan_store.ORIGIN_KEY] = origin
    shown = QueryResponseOfQualifiedProject.model_validate(raw)
    source = plan_store.QualifiedProjectId("authoring:preview")
    named = plan_store.QualifiedProjectId("plans:board")
    if origin == source:
        held = spike_brief_flow.held_project(shown, source, named)
        assert (held.qualified_id, held.title) == (named, "Preview")
    else:
        with pytest.raises(ValueError, match="belongs to another source project"):
            spike_brief_flow.held_project(shown, source, named)


@pytest.mark.parametrize("linked", ["plans:board", "plans:another-board", None])
def test_an_unstamped_board_project_is_its_source_s_copy_only_where_its_link_names_it(
    linked: str | None,
) -> None:
    """A copy out of the private `authoring` source carries no origin, so the link decides."""
    raw = json.loads((FIXTURES / "project-show.json").read_text())
    raw["items"][0]["item"]["metadata"].pop(plan_store.ORIGIN_KEY, None)
    shown = QueryResponseOfQualifiedProject.model_validate(raw)
    source = plan_store.QualifiedProjectId("authoring:preview")
    named = plan_store.QualifiedProjectId("plans:board")
    if linked == named:
        held = spike_brief_flow.held_project(shown, source, named, linked)
        assert (held.qualified_id, held.title) == (named, "Preview")
    else:
        with pytest.raises(ValueError, match="belongs to another source project"):
            spike_brief_flow.held_project(shown, source, named, linked)
