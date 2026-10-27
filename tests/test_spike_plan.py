"""The spike convention `orchestrator/spike_plan.py` states, read over loaded plans.

`tests/plan_tooling/check_and_review/test_check_plan_spikes_e2e.py` drives every refusal
through the real `just check-plan` over plan-store fixtures, with bodies the pinned store
rendered. This holds the readings that recipe cannot reach on demand: the shapes a loaded
plan carries that the engine's loader never hands over, a stamp read off the project record
by kind, and a body whose evidence section is missing or malformed. The store is answered at
its reader's seam, since what is under test is what this module decides from the answer.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from pathlib import Path

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
    assert (
        spike_plan.report_id("cursor-plan", NodeId("spike-listing"))
        == "cursor-plan-spike-listing-report"
    )
    assert spike_plan.legacy_report_id(NodeId("spike-listing")) == "spike-listing-report"
    assert spike_plan.REPORT_SHAPE == "<plan native id>-<spike id>-report"
    assert spike_plan.report_spike("cursor-plan", "cursor-plan-spike-listing-report") == (
        "spike-listing"
    )
    assert spike_plan.report_spike("cursor-plan", "spike-listing-report") == "spike-listing"
    assert spike_plan.report_spike("cursor-plan", "other-plan-spike-listing-report") is None
    assert spike_plan.report_spike("cursor-plan", "cursor-plan-spike-listing") is None
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
        spike_plan.Evidence(
            NodeId("spike-listing"),
            spike_plan.ReportId("spike-listing-report"),
            spike_plan.Branch("nick/p/spike-listing"),
        ),
        spike_plan.Evidence(
            NodeId("spike-quota"),
            spike_plan.ReportId("spike-quota-report"),
            spike_plan.Branch("nick/p/spike-quota"),
        ),
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


def test_a_report_is_linked_by_its_plan_qualified_id_and_never_by_another_plans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two plans spiking one topic each hold their own report; the old id answers only alone."""
    _store(
        monkeypatch,
        {},
        "authoring:cursor-plan-spike-a-report",
        "authoring:spike-a-report",
        "authoring:spike-b-report",
        "authoring:cursor-plan-spike-x-2-report",
    )
    task = {
        "id": "listing",
        "task": _body(
            _evidence_line("spike-a", "cursor-plan-spike-a-report", "nick/p/spike-a"),
            # The old id where the qualified one is held is not this spike's report.
            _evidence_line("spike-a", "spike-a-report", "nick/p/spike-a"),
            # The old id where only it is held is, for a plan reported before ids named it.
            _evidence_line("spike-b", "spike-b-report", "nick/p/spike-b"),
            # Another plan's id is never this plan's report.
            _evidence_line("spike-b", "other-plan-spike-b-report", "nick/p/spike-b"),
            # A qualified report names its spike, so a branch it could be a retry of is ambiguous.
            _evidence_line("spike-x", "cursor-plan-spike-x-2-report", "nick/p/spike-x-2"),
        ),
    }

    refused = spike_plan.refusals(PROJECT, {"tasks": [task]})

    assert [one.reason.split(";")[0] for one in refused] == [
        "`listing`'s `spikes` answer links the report `spike-a-report` of `spike-a`, which is "
        "not that spike's report among this plan's documents",
        "`listing`'s `spikes` answer links the report `other-plan-spike-b-report` of `spike-b`, "
        "which is not that spike's report among this plan's documents",
        "`listing`'s `spikes` answer links the report `cursor-plan-spike-x-2-report` of "
        "`spike-x`, which is not that spike's report among this plan's documents",
        "`listing`'s `spikes` answer names the branch `nick/p/spike-x-2` for `spike-x`, which "
        "could be a retry of `spike-x` or the branch of the plan's own spike `spike-x-2`",
    ]
    assert "the document `cursor-plan-spike-a-report` it writes there" in refused[0].reason


def test_a_copys_plan_is_its_drafted_projects_and_its_reports_resolve_through_their_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A board copy is named by the drafted plan, so its reports keep their authoring ids."""
    board = "plans:I_created_0"
    report = _copied_report("plans:I_created_2", "authoring:cursor-plan-spike-a-report", True)
    projects = [
        _drafted_project("elsewhere", {"plans": "plans:I_other"}),
        _drafted_project("unlinked", None),
        _drafted_project("cursor-plan", {"plans": board}),
    ]
    monkeypatch.setattr(plan_store, "read_projects", lambda _source: projects)
    monkeypatch.setattr(plan_store, "read_documents", lambda _project: [report])
    monkeypatch.setattr(plan_store, "project_record", lambda _project: {"metadata": {}})
    task = {
        "id": "listing",
        "task": _body(_evidence_line("spike-a", "cursor-plan-spike-a-report", "n/p/spike-a")),
    }

    assert spike_plan.plan_native(board) == "cursor-plan"
    assert spike_plan.plan_native("authoring:cursor-plan") == "cursor-plan"
    assert spike_plan.plan_native("plans:NEVER-COPIED") == "NEVER-COPIED"
    assert spike_plan.refusals(board, {"tasks": [task]}) == []

    def unreadable(_source: str) -> list[object]:
        raise OSError("the authoring root cannot be read")

    monkeypatch.setattr(plan_store, "read_projects", unreadable)
    assert spike_plan.plan_native(board) == "I_created_0"


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
    # No drafted project links this copy, so it is named by its own native id.
    monkeypatch.setattr(plan_store, "read_projects", lambda _source: [])
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


def test_a_copy_resolves_its_reports_through_their_origin_with_authoring_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The copy's own id names no plan, so each report's recorded origin names it instead."""
    board = "plans:I_created_0"

    def unreadable(_source: str) -> list[object]:
        raise OSError("the authoring root cannot be read")

    visual = _copied_report("plans:I_created_4", "authoring:cursor-plan-spike-visual-report", True)
    stale = _copied_report("plans:I_created_5", "authoring:spike-visual-report", True)
    listing = _copied_report("plans:I_created_6", "authoring:cursor-plan-spike-a-report", True)
    legacy = _copied_report("plans:I_created_7", "authoring:spike-a-report", True)
    retried = _copied_report("plans:I_created_8", "authoring:cursor-plan-spike-x-2-report", True)
    documents = [stale, visual, legacy, listing, retried]
    metadata: dict[str, object] = {}
    monkeypatch.setattr(plan_store, "read_projects", unreadable)
    monkeypatch.setattr(plan_store, "read_documents", lambda _project: documents)
    monkeypatch.setattr(plan_store, "project_record", lambda _project: {"metadata": metadata})

    assert spike_plan.plan_native(board) == "I_created_0"
    # The qualified report wins over the one under the old id, found through its origin.
    assert spike_plan.current_visual_report(board, documents) == visual
    assert spike_plan.current_visual_report(board, [stale]) == stale
    # An explicit selection stays opaque: it names the old report, and the old report it is.
    metadata[spike_plan.VISUAL_REPORT_KEY] = "authoring:spike-visual-report"
    assert spike_plan.current_visual_report(board, documents) == stale
    del metadata[spike_plan.VISUAL_REPORT_KEY]

    task = {
        "id": "listing",
        "task": _body(
            _evidence_line("spike-a", "cursor-plan-spike-a-report", "n/p/spike-a"),
            # The old id where the qualified one is held is not this spike's report.
            _evidence_line("spike-a", "spike-a-report", "n/p/spike-a"),
            # A report held for `spike-x-2` makes that branch ambiguous, as on the drafted plan.
            _evidence_line("spike-x", "cursor-plan-spike-x-report", "n/p/spike-x-2"),
        ),
    }
    refused = spike_plan.refusals(board, {"tasks": [task]})
    assert [one.reason.split(";")[0] for one in refused] == [
        "`listing`'s `spikes` answer links the report `spike-a-report` of `spike-a`, which is "
        "not that spike's report among this plan's documents",
        "`listing`'s `spikes` answer links the report `cursor-plan-spike-x-report` of `spike-x`, "
        "which is not that spike's report among this plan's documents",
        "`listing`'s `spikes` answer names the branch `n/p/spike-x-2` for `spike-x`, which could "
        "be a retry of `spike-x` or the branch of the plan's own spike `spike-x-2`",
    ]
    assert "the document `cursor-plan-spike-a-report` it writes there" in refused[0].reason


def _drafted_project(native: str, copies: object) -> plan_store.StoreProject:
    """A project of the authoring source, declared private, carrying the copy link ``copies``."""
    return plan_store.StoreProject(
        qualified_id=plan_store.QualifiedProjectId(f"authoring:{native}"),
        title=native,
        metadata={} if copies is None else {plan_store.COPIES_KEY: copies},
        location=None,
    )


def _drafted_report(native: str, copies: object) -> StoreDocument:
    """A report as the authoring source, declared private, holds it, linking a board's copy."""
    metadata: dict[str, object] = dict(REPORT_PROVENANCE)
    if copies is not None:
        metadata[plan_store.COPIES_KEY] = copies
    return dataclasses.replace(
        _copied_report(f"authoring:{native}", None, True), metadata=metadata, project="cursor-plan"
    )


def test_a_report_copied_out_of_private_authoring_resolves_through_the_drafted_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A copy out of a private source carries no origin; authoring's own link names it instead."""
    board = "plans:I_created_0"
    copied = _copied_report("plans:I_created_2", None, True)
    unlinked = _copied_report("plans:I_created_3", None, True)
    projects = [
        _drafted_project("elsewhere", {"plans": "plans:I_other"}),
        _drafted_project("unlinked", None),
        _drafted_project("cursor-plan", {"plans": board}),
    ]
    drafted = [
        _drafted_report("spike-a-report", {"plans": "plans:I_created_2", "odd": 7}),
        _drafted_report("notes", "not a link"),
        _drafted_report("spike-b-report", None),
    ]
    read: list[str] = []

    def documents(project: str) -> list[StoreDocument]:
        read.append(project)
        return drafted if project == "authoring:cursor-plan" else [unlinked, copied]

    monkeypatch.setattr(plan_store, "read_projects", lambda _source: projects)
    monkeypatch.setattr(plan_store, "read_documents", documents)
    monkeypatch.setattr(plan_store, "project_record", lambda _project: {"metadata": {}})
    linked = spike_plan.Evidence(
        NodeId("spike-a"), spike_plan.ReportId("spike-a-report"), spike_plan.Branch("n/p/spike-a")
    )

    links = spike_plan.drafted_ids(board)
    assert links == {"plans:I_created_2": "authoring:spike-a-report"}
    assert spike_plan.report_for(linked, [unlinked, copied]) is None
    assert spike_plan.report_for(linked, [unlinked, copied], links) == copied
    task = {
        "id": "listing",
        "task": _body(_evidence_line("spike-a", "spike-a-report", "n/p/spike-a")),
    }
    assert spike_plan.refusals(board, {"tasks": [task]}) == []
    assert read == ["authoring:cursor-plan", board, "authoring:cursor-plan"]
    # The drafted project's own documents name themselves, and no copy link is read for them.
    assert spike_plan.drafted_ids("authoring:cursor-plan") == {}


def test_a_copy_no_drafted_project_links_or_authoring_cannot_answer_for_resolves_nothing_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every way the authoring side has no link to give answers no drafted id, never a refusal."""
    board = "plans:I_created_0"
    monkeypatch.setattr(plan_store, "read_projects", lambda _source: [])
    assert spike_plan.drafted_ids(board) == {}

    def unreadable(_where: str) -> list[object]:
        raise OSError("the authoring root cannot be read")

    monkeypatch.setattr(plan_store, "read_projects", unreadable)
    assert spike_plan.drafted_ids(board) == {}

    monkeypatch.setattr(
        plan_store, "read_projects", lambda _source: [_drafted_project("p", {"plans": board})]
    )
    monkeypatch.setattr(plan_store, "read_documents", unreadable)
    assert spike_plan.drafted_ids(board) == {}


def test_the_visual_report_of_a_copy_out_of_private_authoring_is_found_through_its_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no selection and with one naming the drafted report, the board copy is the one."""
    board = "plans:I_created_0"
    copied = _copied_report("plans:I_created_4", None, True)
    drafted = _drafted_report(spike_plan.visual_report_id("p"), {"plans": "plans:I_created_4"})
    monkeypatch.setattr(
        plan_store, "read_projects", lambda _source: [_drafted_project("p", {"plans": board})]
    )
    monkeypatch.setattr(plan_store, "read_documents", lambda _project: [drafted])
    metadata: dict[str, object] = {}
    monkeypatch.setattr(plan_store, "project_record", lambda _project: {"metadata": metadata})

    assert spike_plan.current_visual_report(board, [copied]) == copied
    metadata[spike_plan.VISUAL_REPORT_KEY] = f"authoring:{spike_plan.visual_report_id('p')}"
    assert spike_plan.current_visual_report(board, [copied]) == copied


def test_one_drafted_plan_reads_the_drafting_source_once_for_every_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The visual report, the plan's drafted id and a later link check share one read.

    An approval asks what the drafting source holds of its copy for its reports, its plan's
    drafted id and its design document; one :class:`spike_plan.DraftedPlan` handed to each
    lists the drafting source's projects and the drafted plan's documents once, however often
    it is asked, and an unasked one reads nothing.
    """
    board = "plans:I_created_0"
    copied = _copied_report("plans:I_created_4", None, True)
    drafted = _drafted_report(spike_plan.visual_report_id("p"), {"plans": "plans:I_created_4"})
    read: list[str] = []

    def projects(source: str) -> list[plan_store.StoreProject]:
        read.append(f"projects of {source}")
        return [_drafted_project("p", {"plans": board})]

    def documents(project: str) -> list[plan_store.StoreDocument]:
        read.append(f"documents of {project}")
        return [drafted]

    monkeypatch.setattr(plan_store, "read_projects", projects)
    monkeypatch.setattr(plan_store, "read_documents", documents)
    monkeypatch.setattr(plan_store, "project_record", lambda _project: {"metadata": {}})

    spike_plan.DraftedPlan(board)
    assert read == []
    lookup = spike_plan.DraftedPlan(board)
    assert spike_plan.current_visual_report(board, [copied], lookup=lookup) == copied
    assert lookup.links() == {"plans:I_created_4": f"authoring:{spike_plan.visual_report_id('p')}"}
    assert spike_plan.plan_native(board, lookup) == "p"
    assert read == ["projects of authoring", "documents of authoring:p"], read


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
    assert spike_plan.visual_report_id("cursor-plan") == "cursor-plan-spike-visual-report"
    assert spike_plan.report_id("cursor-plan", spike_plan.VISUAL_SPIKE) == (
        spike_plan.visual_report_id("cursor-plan")
    )


def test_a_plans_visual_report_is_found_by_its_id_or_the_origin_its_copy_records() -> None:
    """Drafted, the report is `<plan>-spike-visual-report`; on a board copy, its origin names it."""
    plan = "cursor-plan"
    drafted = _copied_report("authoring:cursor-plan-spike-visual-report", None, True)
    copied = _copied_report("plans:I_created_4", "authoring:cursor-plan-spike-visual-report", True)
    other = _copied_report("authoring:cursor-plan-spike-listing-report", None, True)
    unrendered = _copied_report("authoring:cursor-plan-spike-visual-report", None, False)
    native = _copied_report("destination:cursor-plan-spike-visual-report", "elsewhere:x", True)
    another_plans = _copied_report("authoring:other-plan-spike-visual-report", None, True)
    legacy = _copied_report("authoring:spike-visual-report", None, True)

    assert spike_plan.visual_report(plan, [other, drafted]) == drafted
    assert spike_plan.visual_report(plan, [other, copied]) == copied
    assert spike_plan.visual_report(plan, [native]) == native
    # Another plan's visual report is never this plan's.
    assert spike_plan.visual_report(plan, [another_plans]) is None
    # The id reports had before they named their plan answers only where the qualified is absent.
    assert spike_plan.visual_report(plan, [other, legacy]) == legacy
    assert spike_plan.visual_report(plan, [legacy, drafted]) == drafted
    # A document of that name not rendered from the spike-report template is no report.
    assert spike_plan.visual_report(plan, [other, unrendered]) is None
    assert spike_plan.visual_report(plan, []) is None


def test_the_command_names_a_plans_visual_report_or_prints_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _store(
        monkeypatch,
        {},
        "authoring:cursor-plan-spike-visual-report",
        "authoring:cursor-plan-spike-listing-report",
    )
    assert spike_plan.main(["visual-report", PROJECT]) == 0
    assert capsys.readouterr().out == "authoring:cursor-plan-spike-visual-report\n"

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


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (None, "null"),
        (3, "non-string"),
        (" ", "blank"),
        ("native", "malformed"),
        ("a:", "malformed"),
        (":b", "malformed"),
        ("a:missing", "missing"),
    ],
)
def test_explicit_visual_selection_never_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    value: object,
    reason: str,
) -> None:
    _store(monkeypatch, {spike_plan.VISUAL_REPORT_KEY: value}, "authoring:spike-visual-report")
    with pytest.raises(OSError, match=reason):
        spike_plan.current_visual_report(PROJECT, plan_store.read_documents(PROJECT))


@pytest.mark.parametrize(
    "selected",
    ["authoring:cursor-plan-spike-visual-revision-report", "authoring:report:opaque"],
)
def test_selection_returns_the_actual_held_copy_and_refuses_ambiguity(
    monkeypatch: pytest.MonkeyPatch,
    selected: str,
) -> None:
    import dataclasses

    _store(monkeypatch, {spike_plan.VISUAL_REPORT_KEY: selected})
    report = dataclasses.replace(
        _copied_report("board:reminted", selected, True), project="cursor-plan"
    )
    assert spike_plan.current_visual_report(PROJECT, [report]) is report
    with pytest.raises(OSError, match="ambiguous"):
        spike_plan.current_visual_report(PROJECT, [report, report])
    with pytest.raises(OSError, match="wrong-project"):
        spike_plan.current_visual_report(PROJECT, [dataclasses.replace(report, project="other")])
    with pytest.raises(OSError, match="wrong-kind"):
        spike_plan.current_visual_report(
            PROJECT, [dataclasses.replace(report, metadata={plan_store.ORIGIN_KEY: selected})]
        )


def test_a_copys_plan_may_have_been_drafted_in_every_default_source_declared_private() -> None:
    """The tracked configuration's private default sources, `authoring` first, and no other."""
    assert spike_plan.drafting_sources() == (spike_plan.AUTHORING, "test-fixtures")


def test_a_drafting_side_the_store_cannot_resolve_links_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A store whose configuration cannot be read answers no drafted project, never a refusal.

    The real store, under a user layer of this test's own that is not valid YAML.
    """
    layer = tmp_path / "onetaskgraph" / "config.yaml"
    layer.parent.mkdir()
    layer.write_text("sources: [unclosed\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with pytest.raises(OSError, match="not valid YAML"):
        plan_store.configured_settings()
    assert spike_plan.drafted_project("plans:I_created_0") is None
    assert spike_plan.plan_native("plans:I_created_0") == "I_created_0"
