"""The spike convention `orchestrator/spike_plan.py` states, read over loaded plans.

`tests/plan_tooling/test_check_plan_spikes_e2e.py` drives every refusal through the real
`just check-plan` over plan-store fixtures, with bodies the pinned store rendered. This holds
the readings that recipe cannot reach on demand: the shapes a loaded plan carries that the
engine's loader never hands over, a stamp read off the project record by kind, and a body
whose evidence section is missing or malformed. The store is answered at its reader's seam,
since what is under test is what this module decides from the answer.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from orchestrator import design_approval, plan_store, spike_plan
from orchestrator.plan_store import NodeId, QualifiedDocumentId, StoreDocument

PROJECT = "authoring:cursor-plan"
REPOSITORY = "github.com/nickderobertis/some-service"


def _stamp(kind: str, *nodes: str) -> Mapping[str, object]:
    return {
        design_approval.PLAN_KIND: {
            design_approval.STAMP_KIND: kind,
            design_approval.STAMP_NODES: list(nodes),
        }
    }


#: What the store records on a spike's report, which travels with every copy of it.
REPORT_PROVENANCE = {"onetaskgraph.template": {"template": "onepipeline:spike-report"}}


def _store(
    monkeypatch: pytest.MonkeyPatch,
    metadata: Mapping[str, object],
    *documents: str,
    others: tuple[str, ...] = (),
) -> None:
    """Answer the project's record with ``metadata``: ``documents`` are reports, ``others`` not."""
    monkeypatch.setattr(plan_store, "project_record", lambda _project: {"metadata": metadata})
    monkeypatch.setattr(
        plan_store,
        "read_documents",
        lambda _project: [
            StoreDocument(
                qualified_id=QualifiedDocumentId(document),
                title=document,
                content="",
                project="cursor-plan",
                labels=[],
                repositories=[],
                metadata=REPORT_PROVENANCE if document in documents else {},
                location=None,
            )
            for document in (*documents, *others)
        ],
    )


def _evidence_line(spike: str, report: str, branch: str) -> str:
    return (
        f"- `{spike}`: report `{report}`, a document of this plan's own project; branch `{branch}`"
    )


def _body(*lines: str) -> str:
    return (
        "## What\n\nPage it.\n\n## Acceptance criteria\n\n- Paged.\n\n"
        f"{spike_plan.EVIDENCE_HEADING}\n\nThis task builds on these spikes:\n\n"
        + "\n".join(lines)
        + "\n\n## Additional info\n\n- `spike-elsewhere`: report `x`, a document of this "
        "plan's own project; branch `y`\n"
    )


def test_the_names_the_convention_composes() -> None:
    assert spike_plan.spikes_project(PROJECT) == "authoring:cursor-plan-spikes"
    assert spike_plan.report_id("spike-listing") == "spike-listing-report"
    assert spike_plan.is_spike("spike-listing")
    assert not spike_plan.is_spike("spike-")
    assert not spike_plan.is_spike("listing-spike-")


def test_evidence_is_read_from_its_own_section_alone() -> None:
    """Lines outside the section, and lines not in the template's words, link nothing."""
    body = _body(
        _evidence_line("spike-listing", "spike-listing-report", "nick/p/spike-listing"),
        "- `spike-quota`: see the quota report",
        _evidence_line("spike-quota", "spike-quota-report", "nick/p/spike-quota"),
    )

    assert spike_plan.evidence(body) == [
        spike_plan.Evidence("spike-listing", "spike-listing-report", "nick/p/spike-listing"),
        spike_plan.Evidence("spike-quota", "spike-quota-report", "nick/p/spike-quota"),
    ]
    assert spike_plan.evidence("## What\n\nNo spikes here.\n") == []


def test_a_spikes_project_refuses_each_node_breaking_the_convention_once_per_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store(monkeypatch, _stamp(design_approval.SPIKES, "spike-a", "measure", "spike-b", "spike-c"))
    document = {
        "tasks": [
            {"id": "spike-a", "repo": REPOSITORY, "publish": "preserve"},
            {"id": "measure", "repo": REPOSITORY, "publish": "preserve"},
            # The reserved metadata key is read where the loaded node states no field.
            {"id": "spike-b", "repo": REPOSITORY, "metadata": {"onepipeline.publish": "preserve"}},
            {"id": "spike-c", "kind": "human", "repo": REPOSITORY, "metadata": None},
            "not a node",
            {"id": None},
        ]
    }

    refused = spike_plan.refusals(spike_plan.spikes_project(PROJECT), document)

    assert [(one.node, one.field) for one in refused] == [
        ("measure", "id"),
        ("spike-c", "repo"),
        ("spike-c", "metadata"),
    ]


def test_another_kinds_stamp_is_no_spikes_project(monkeypatch: pytest.MonkeyPatch) -> None:
    _store(monkeypatch, _stamp(design_approval.PLANNING, "spike-a"))

    (refused,) = spike_plan.refusals(PROJECT, {"tasks": [{"id": "spike-a", "repo": REPOSITORY}]})

    assert (refused.node, refused.field) == ("spike-a", "id")
    assert "belongs only in a project stamped `spikes`" in refused.reason


def test_a_linked_report_is_found_by_its_document_id_or_its_qualified_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store(
        monkeypatch,
        {},
        "authoring:spike-a-report",
        "authoring:spike-b-report",
        "authoring:spike-e-report",
        others=("authoring:spike-d-report",),
    )
    task = {
        "id": "listing",
        "task": _body(
            _evidence_line("spike-a", "spike-a-report", "nick/p/spike-a"),
            _evidence_line("spike-b", "authoring:spike-b-report", "nick/p/spike-b-2"),
            _evidence_line("spike-c", "spike-c-report", "nick/p/spike-c-two"),
            _evidence_line("spike-d", "spike-d-report", "nick/p/spike-d"),
            _evidence_line("spike-a", "spike-e-report", "nick/p/spike-a"),
        ),
    }

    refused = spike_plan.refusals(PROJECT, {"tasks": [task, {"id": "other", "task": None}]})

    assert [(one.node, one.field) for one in refused] == [("listing", "task")] * 4
    # A report the plan does not hold, then its branch, which is no retry of that spike.
    assert "links the report `spike-c-report` of `spike-c`" in refused[0].reason
    assert "names the branch `nick/p/spike-c-two` for `spike-c`" in refused[1].reason
    # A document by the right name that is no spike's report, and another spike's report.
    assert "links the report `spike-d-report` of `spike-d`" in refused[2].reason
    assert "links the report `spike-e-report` of `spike-a`" in refused[3].reason


def test_a_plan_with_no_tasks_list_refuses_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _store(monkeypatch, {})

    assert spike_plan.refusals(PROJECT, {"tasks": "none"}) == []
    assert spike_plan.refusals(PROJECT, []) == []


def _copied_report(minted: str, origin: str | None, provenance: object) -> StoreDocument:
    """A document as a board copy holds it: an id the board minted, and the store's origin."""
    metadata: dict[str, object] = dict(REPORT_PROVENANCE) if provenance else {}
    if origin is not None:
        metadata["onetaskgraph.origin"] = origin
    return StoreDocument(
        qualified_id=QualifiedDocumentId(minted),
        title="Spike report",
        content="",
        project="I_created_0",
        labels=[],
        repositories=[],
        metadata=metadata,
        location=None,
    )


def test_a_report_on_a_copy_resolves_through_the_origin_the_store_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A board mints the copy's id; the store's `onetaskgraph.origin` still names the report."""
    report = _copied_report("plans:I_created_2", "authoring:spike-a-report", True)
    unrelated = _copied_report("plans:I_created_3", "authoring:spike-b-report", False)
    monkeypatch.setattr(plan_store, "project_record", lambda _project: {"metadata": {}})
    monkeypatch.setattr(plan_store, "read_documents", lambda _project: [unrelated, report])
    linked = spike_plan.Evidence(
        NodeId("spike-a"), spike_plan.ReportId("spike-a-report"), spike_plan.Branch("n/p/spike-a")
    )
    task = {
        "id": "listing",
        "task": _body(_evidence_line("spike-a", "spike-a-report", "n/p/spike-a")),
    }
    unknown = {
        "id": "other",
        "task": _body(_evidence_line("spike-b", "spike-b-report", "n/p/spike-b")),
    }

    assert spike_plan.report_for(linked, [unrelated, report]) == report
    assert spike_plan.names(report) == {
        "plans:I_created_2",
        "I_created_2",
        "authoring:spike-a-report",
        "spike-a-report",
    }
    # The copy of `spike-b-report` is no spike's report by its provenance, so it links nothing.
    (refused,) = spike_plan.refusals("plans:I_created_0", {"tasks": [task, unknown]})
    assert (refused.node, refused.field) == ("other", "task")
    assert "links the report `spike-b-report` of `spike-b`" in refused.reason


@pytest.mark.parametrize(
    ("branch", "also_linked", "also_reported", "said"),
    [
        ("nick/p/spike-x", None, None, None),
        ("nick/p/spike-x-2", None, None, None),
        ("nick/p/spike-x-12", None, None, None),
        ("nick/p/spike-x-2", "spike-x-2", None, "could be a retry of `spike-x` or the branch"),
        ("nick/p/spike-x-2", None, "spike-x-2", "could be a retry of `spike-x` or the branch"),
        ("nick/p/spike-x-1", None, None, "for a retry of it"),
        ("nick/p/spike-x-02", None, None, "for a retry of it"),
        ("nick/p/spike-x-two", None, None, "for a retry of it"),
        ("spike-x", None, None, "for a retry of it"),
    ],
    ids=[
        "its-own",
        "a-retry",
        "a-later-retry",
        "ambiguous-with-a-linked-spike",
        "ambiguous-with-a-held-report",
        "no-retry-is-numbered-one",
        "a-padded-number",
        "not-a-number",
        "no-prefix",
    ],
)
def test_a_spikes_branch_is_its_own_or_an_unambiguous_retrys(
    monkeypatch: pytest.MonkeyPatch,
    branch: str,
    also_linked: str | None,
    also_reported: str | None,
    said: str | None,
) -> None:
    reports = ["authoring:spike-x-report"]
    if also_reported:
        reports.append(f"authoring:{also_reported}-report")
    _store(monkeypatch, {}, *reports)
    lines = [_evidence_line("spike-x", "spike-x-report", branch)]
    tasks: list[dict[str, object]] = [{"id": "listing", "task": _body(*lines)}]
    if also_linked:
        _store(monkeypatch, {}, *reports, f"authoring:{also_linked}-report")
        tasks.append(
            {
                "id": "quota",
                "task": _body(
                    _evidence_line(also_linked, f"{also_linked}-report", f"nick/p/{also_linked}")
                ),
            }
        )

    refused = [
        one for one in spike_plan.refusals(PROJECT, {"tasks": tasks}) if one.node == "listing"
    ]

    if said is None:
        assert refused == []
    else:
        (one,) = refused
        assert said in one.reason, one.reason
        if "could be a retry" in said:
            assert "`spike-x-2`" in one.reason


def test_an_evidence_entry_not_in_the_templates_words_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An entry the check cannot read would link a spike it never checks."""
    _store(monkeypatch, {}, "authoring:spike-a-report")
    task = {
        "id": "listing",
        "task": _body(
            _evidence_line("spike-a", "spike-a-report", "nick/p/spike-a"),
            "- `spike-quota`: see the quota report",
        ),
    }

    (refused,) = spike_plan.refusals(PROJECT, {"tasks": [task]})

    assert (refused.node, refused.field) == ("listing", "task")
    assert "'- `spike-quota`: see the quota report'" in refused.reason


def test_the_visual_spike_and_its_report_are_reserved_spike_names() -> None:
    """One visual spike per plan, named as a spike, its report named as every report is."""
    assert spike_plan.VISUAL_SPIKE == "spike-visual"
    assert spike_plan.is_spike(spike_plan.VISUAL_SPIKE)
    assert spike_plan.VISUAL_REPORT == "spike-visual-report"
    assert spike_plan.report_id(spike_plan.VISUAL_SPIKE) == spike_plan.VISUAL_REPORT


def test_a_plans_visual_report_is_found_by_its_id_or_the_origin_its_copy_records() -> None:
    """Drafted, the report is `spike-visual-report`; on a board copy, its origin names it."""
    drafted = _copied_report("authoring:spike-visual-report", None, True)
    copied = _copied_report("plans:I_created_4", "authoring:spike-visual-report", True)
    other = _copied_report("authoring:spike-listing-report", None, True)
    unrendered = _copied_report("authoring:spike-visual-report", None, False)
    native = _copied_report("destination:spike-visual-report", "elsewhere:x", True)

    assert spike_plan.visual_report([other, drafted]) == drafted
    assert spike_plan.visual_report([other, copied]) == copied
    assert spike_plan.visual_report([native]) == native
    # A document of that name not rendered from the spike-report template is no report.
    assert spike_plan.visual_report([other, unrendered]) is None
    assert spike_plan.visual_report([]) is None


def test_the_command_names_a_plans_visual_report_or_prints_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _store(monkeypatch, {}, "authoring:spike-visual-report", "authoring:spike-listing-report")
    assert spike_plan.main(["visual-report", PROJECT]) == 0
    assert capsys.readouterr().out == "authoring:spike-visual-report\n"

    _store(monkeypatch, {}, "authoring:spike-listing-report")
    assert spike_plan.main(["visual-report", PROJECT]) == 0
    assert capsys.readouterr().out == ""


def test_the_command_refuses_a_plan_whose_documents_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unreadable(project: str) -> list[StoreDocument]:
        raise OSError(f"no project {project}")

    monkeypatch.setattr(plan_store, "read_documents", unreadable)
    assert spike_plan.main(["visual-report", PROJECT]) == 2
    said = capsys.readouterr()
    assert said.out == ""
    assert f"the documents of {PROJECT} could not be read: no project {PROJECT}" in said.err
