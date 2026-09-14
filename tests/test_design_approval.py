"""What `just approve-design` records, and what a launch refuses without it.

The flow is driven for real in
`tests/plan_tooling/test_approve_design_recipe_e2e.py` — the real recipe, the real store,
and a real `just orchestrate` refused and then let through. What is proven here is the
part of the decision no journey reaches without breaking this checkout: how the key is
composed, what a record that cannot be read means, and how the launch gate reads a command
line it deliberately does not parse.

The key is the half worth stating. It covers the document's own authored content **and**
the tracked template that says what a design document is, so a passing test here is a
claim about both: editing the document loses its approval, and moving the template
invalidates every approval granted under the previous one. A key over the content alone
would leave a standing approval after the bar it was granted under had moved, which is the
same defect the plan-review key exists to prevent one document further up. The other
direction is the one a copied plan pays for: nothing the store the document sits in owns is
in the key, so a record that travels arrives matching what the destination computes.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from orchestrator import design_approval, plan_store
from orchestrator.plan_store import (
    NodeId,
    QualifiedDocumentId,
    QualifiedTaskId,
    StoreDocument,
    StoreTask,
)

#: The bar a document is approved under, stated here rather than read off this
#: checkout's own `config/design-doc-template.md`. Every test below that is not *about*
#: that file takes it, and the reason is a cache key rather than taste: the tier these
#: run in is memoized on the workspace **minus its prose**, so a test reading that file
#: would have to move to the whole-workspace tier — which measures no coverage — to be
#: allowed to. Holding the bar still also makes each of these a claim about the key
#: rather than about what the template currently says.
STATED = design_approval.TemplateFingerprint("a stated design-document template")

#: That same bar after somebody edited `config/design-doc-template.md`. Distinct from
#: :data:`STATED` and meaningless beyond being distinct: what an approval turns on is
#: whether the fingerprint in force is the one it was granted under.
MOVED = design_approval.TemplateFingerprint("a template that has moved")

#: The two things a person reads when they approve a design document, and what
#: :func:`_document` states them as. Named so a test can hold one still while it moves
#: the other, which is what tells the two invalidation paths apart.
APPROVED_TITLE = "Design: demo"
APPROVED_PROSE = "## What\n\nA paginated listing.\n"

#: Where the store says the document below is, when a test does not say otherwise.
LOCATED = {"path": "/test/documents/demo-design.md"}

#: The plan as the store it was drafted in addresses it, and as the board it was copied
#: onto addresses it. The second is opaque because a board mints its own identifiers,
#: which is the whole reason an approval may not be keyed on one.
SOURCE_PROJECT = "authoring:demo"
BOARD_PROJECT = "plans:PVT_kwHOAA"

#: The fields of a store document the key is composed of. The other half of the partition
#: is :data:`EXCLUDED`, and the two are asserted in both directions below, so this pair is
#: a claim this file makes about :func:`~orchestrator.design_approval.approval_key` rather
#: than a note about it: a field moved from one side to the other fails one of them.
COVERED = ("title", "content")

#: Every other field of a store document, taken from :class:`StoreDocument` rather than
#: listed here. Derived because the list is not this file's to keep: a field added to that
#: record tomorrow is outside the key by the same silence that puts these outside it, and
#: a hand-written list would go on passing while saying nothing about it.
EXCLUDED = tuple(
    field.name for field in dataclasses.fields(StoreDocument) if field.name not in COVERED
)

#: A second, different value for each field of a store document. Stated per field because
#: a second value has to be one of that field's own type, and *only* per field: which
#: fields must appear here is :data:`EXCLUDED` and :data:`COVERED`'s to say, and
#: :func:`_otherwise` refuses a field this mapping has no second value for — so a field
#: added to that record fails this module rather than passing through it unexamined.
OTHERWISE: Mapping[str, object] = {
    "qualified_id": QualifiedDocumentId("plans:PVTI_lAHOAA"),
    "title": "Design: something else",
    "content": "## What\n\nSomething else.\n",
    "project": "PVT_kwHOAA",
    "labels": ["design"],
    "repositories": ["nickderobertis/ai-orchestrator"],
    "metadata": {"the destination's own bookkeeping": "authoring:demo-design"},
    "location": {"url": "https://github.invalid/users/x/projects/2?pane=issue&itemId=7"},
}


@pytest.fixture
def template(monkeypatch: pytest.MonkeyPatch) -> design_approval.TemplateFingerprint:
    """Hold the design-document template still, and off this checkout's own prose."""
    monkeypatch.setattr(design_approval, "template_fingerprint", lambda *_arguments: STATED)
    return STATED


def _document(
    *,
    qualified_id: str = "authoring:demo-design",
    title: str = APPROVED_TITLE,
    content: str = APPROVED_PROSE,
    project: str | None = "demo",
    labels: list[str] | None = None,
    repositories: list[str] | None = None,
    metadata: Mapping[str, object] | None = None,
    location: Mapping[str, object] | None = LOCATED,
) -> StoreDocument:
    """One design document as the store reports it, with anything a test states of it.

    Keyword parameters rather than a merged mapping, so what a test may vary is stated
    and typed: the shape a `**overrides` helper takes is `object`, which types the call
    site as accepting anything and needs an escape at the constructor to get back out.
    """
    return StoreDocument(
        qualified_id=QualifiedDocumentId(qualified_id),
        title=title,
        content=content,
        project=project,
        labels=labels or [],
        repositories=repositories or [],
        metadata=metadata or {},
        location=location,
    )


def _approved(document: StoreDocument | None = None) -> StoreDocument:
    """``document`` carrying a record for exactly the content it currently states."""
    held = document or _document()
    key = design_approval.approval_key(held, design_approval.template_fingerprint())
    return _document(
        content=held.content,
        title=held.title,
        project=held.project,
        metadata=dict(held.metadata) | {design_approval.RECORD_KEY: {"key": key}},
    )


def _otherwise(document: StoreDocument, field: str) -> StoreDocument:
    """``document`` with ``field`` holding a different value of that field's own type."""
    if field not in OTHERWISE:
        raise AssertionError(
            f"{field!r} is a field of a store document that this module states no second "
            f"value for, so nothing here says whether the approval key covers it"
        )
    return dataclasses.replace(document, **{field: OTHERWISE[field]})


def _elsewhere(document: StoreDocument) -> StoreDocument:
    """``document`` as a *different* store addresses it: every excluded field rewritten.

    Deliberately not a model of what `just copy-plan` does. Which fields a real copy
    rewrites and which it holds is that command's own contract, and it is gated where a
    real copy can be watched — `tests/plan_tooling/test_approve_design_recipe_e2e.py`
    compares a real source record against its real copy field by field. What is stated
    here is only this module's own subject, that the key excludes these, so the document
    below is the hardest case that property has to survive rather than a second statement
    of somebody else's interface. Derived from :data:`EXCLUDED`, so it stays the hardest
    case as that record grows.
    """
    elsewhere = document
    for field in EXCLUDED:
        elsewhere = _otherwise(elsewhere, field)
    # The record travels *in* the metadata map, so a destination writes its bookkeeping
    # beside it rather than over it — the one thing rewriting that field wholesale above
    # would not leave true.
    return dataclasses.replace(
        elsewhere, metadata=dict(elsewhere.metadata) | dict(document.metadata)
    )


def _holds(monkeypatch: pytest.MonkeyPatch, *documents: StoreDocument) -> None:
    monkeypatch.setattr(plan_store, "read_documents", lambda _project: list(documents))


def _project(monkeypatch: pytest.MonkeyPatch, metadata: object) -> None:
    monkeypatch.setattr(plan_store, "project_record", lambda _project: {"metadata": metadata})


def _stamp(*nodes: str) -> Mapping[str, object]:
    """The metadata `scripts/plan.sh` writes onto the project a planning launch makes."""
    return {
        design_approval.PLAN_KIND: {
            design_approval.STAMP_KIND: design_approval.PLANNING,
            design_approval.STAMP_NODES: list(nodes),
        }
    }


def _tasks(monkeypatch: pytest.MonkeyPatch, *node_ids: str) -> None:
    """Answer the project's task listing with one record per ``node_ids`` entry."""
    monkeypatch.setattr(
        plan_store,
        "read_tasks",
        lambda _project: [
            StoreTask(
                qualified_id=QualifiedTaskId(f"authoring:demo-{node_id}"),
                node_id=NodeId(node_id),
                title=node_id,
                content=None,
                metadata={},
                repositories=[],
                deps=(),
            )
            for node_id in node_ids
        ],
    )


@pytest.mark.parametrize("field", COVERED)
def test_the_key_covers_what_a_person_read(field: str) -> None:
    """Half the partition: move one of these and the approval is of something else."""
    base = design_approval.approval_key(_document(), STATED)
    assert design_approval.approval_key(_otherwise(_document(), field), STATED) != base


def test_the_key_covers_the_template_the_document_was_read_against() -> None:
    """And the bar, because an approval is of one document under one statement of the shape."""
    assert design_approval.approval_key(_document(), MOVED) != design_approval.approval_key(
        _document(), STATED
    )


@pytest.mark.parametrize("field", EXCLUDED)
def test_the_key_covers_no_field_but_those(field: str) -> None:
    """The other half, and what lets a record travel between stores at all.

    Every field of a store document that is not what a person read is varied here — the
    set taken from that record rather than listed, so this is a claim about all of them
    and not about the ones somebody remembered. A copy is defined to rewrite the ones the
    destination owns, and a key covering any of them would arrive intact and no longer
    match what the destination computes.

    ``project`` is the one that cost a plan: the same document is a document of
    `some-plan` where it was drafted and of an opaque board identifier once it is copied,
    so a key over it refused every copied plan for want of the approval it was carrying.
    """
    assert design_approval.approval_key(
        _otherwise(_document(), field), STATED
    ) == design_approval.approval_key(_document(), STATED)


def test_an_approval_survives_a_document_addressed_wholly_by_another_store() -> None:
    """Every excluded field rewritten at once, which is the state a copied record is in."""
    drafted = _document(
        metadata={design_approval.RECORD_KEY: {"key": "recorded where it was drafted"}}
    )
    assert design_approval.approval_key(
        _elsewhere(drafted), STATED
    ) == design_approval.approval_key(_document(), STATED)


def _recorded_by_approving(
    monkeypatch: pytest.MonkeyPatch, document: StoreDocument
) -> StoreDocument:
    """``document`` carrying the record `just approve-design` writes for it.

    Written by :func:`~orchestrator.design_approval.approve` rather than composed here, so
    what travels below is the record the command actually produces rather than one this
    file knows how to spell — which is the half a hand-built record cannot speak to.
    """
    written: list[object] = []

    def write(_target: StoreDocument, key: str, value: object) -> None:
        assert key == design_approval.RECORD_KEY
        written.append(value)

    monkeypatch.setattr(plan_store, "write_document_metadata", write)
    _holds(monkeypatch, document)
    assert design_approval.approve(SOURCE_PROJECT).held is False
    (record,) = written
    return _document(
        qualified_id=str(document.qualified_id),
        title=document.title,
        content=document.content,
        project=document.project,
        metadata=dict(document.metadata) | {design_approval.RECORD_KEY: record},
    )


@pytest.mark.parametrize(
    ("moved", "title", "prose", "in_force"),
    [
        ("its title", "Design: something else", APPROVED_PROSE, STATED),
        ("its prose", APPROVED_TITLE, "## What\n\nSomething else.\n", STATED),
        ("the template it is read against", APPROVED_TITLE, APPROVED_PROSE, MOVED),
    ],
    ids=["title", "prose", "template"],
)
def test_a_travelled_document_is_unapproved_again_once_what_was_approved_moves(
    moved: str,
    title: str,
    prose: str,
    in_force: design_approval.TemplateFingerprint,
    monkeypatch: pytest.MonkeyPatch,
    template: design_approval.TemplateFingerprint,
) -> None:
    """Surviving a copy is not the same as surviving anything, and this is the other half.

    The key stopped covering what the destination owns so that a record could travel. A
    record that then stood over content nobody had read would be worse than the refusal
    that behaviour replaced — so all three invalidating moves are driven here on the
    **travelled** document rather than on the drafted one: it carries an approval recorded
    against the source while every field the key excludes is the destination's.

    Asked through :func:`~orchestrator.design_approval.refusal`, which is the question a
    launch asks, rather than through the digest, which is only how it answers. A pair of
    keys that differ says nothing about whether a launch would be refused; this does.
    """
    approved = _recorded_by_approving(monkeypatch, _document())
    landed = _elsewhere(approved)

    _project(monkeypatch, {})
    _holds(monkeypatch, landed)
    assert design_approval.assess(BOARD_PROJECT).refusal is None, (
        "the approval recorded where the document was drafted did not survive the copy, "
        "so what follows would be a refusal this test could not attribute"
    )

    monkeypatch.setattr(design_approval, "template_fingerprint", lambda *_arguments: in_force)
    _holds(
        monkeypatch,
        _elsewhere(_document(title=title, content=prose, metadata=approved.metadata)),
    )
    reason = design_approval.assess(BOARD_PROJECT).refusal
    assert reason is not None, f"the copy is still approved after {moved} moved"
    assert "carries no approval for what it currently says" in reason
    assert design_approval.RECIPE in reason
    assert "https://github.invalid" in reason, (
        f"the refusal does not say where the travelled document is, so the person who has "
        f"to read it again is sent to wherever it was drafted instead: {reason}"
    )


def test_a_moved_template_gives_every_document_a_different_key(tmp_path: Path) -> None:
    """The bar is hashed from the file, so a checkout whose template differs answers differently.

    Taken over two checkouts of this journey's own rather than against this one's, for the
    reason :data:`STATED` gives — and it says the same thing either way, since what is
    under test is that the file's bytes decide.
    """
    fingerprints = []
    for shape in ("a shape", "a different shape"):
        root = tmp_path / shape.replace(" ", "-")
        (root / design_approval.TEMPLATE.parent).mkdir(parents=True)
        (root / design_approval.TEMPLATE).write_text(shape, encoding="utf-8")
        fingerprints.append(design_approval.template_fingerprint(root))
    assert fingerprints[0] != fingerprints[1]


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {design_approval.RECORD_KEY: "not an object"},
        {design_approval.RECORD_KEY: {"key": 7}},
    ],
    ids=["absent", "not an object", "no string key"],
)
def test_a_record_this_cannot_read_is_answered_as_no_record(metadata: dict[str, object]) -> None:
    """The safe direction: the document is then unapproved, which is what that means anyway."""
    assert design_approval.recorded(_document(metadata=metadata)) is None


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ({"url": "https://example.invalid/d/1"}, "https://example.invalid/d/1"),
        ({"path": "/test/documents/demo-design.md"}, "/test/documents/demo-design.md"),
        ({"url": "", "path": "/test/p.md"}, "/test/p.md"),
        (None, "authoring:demo-design"),
        ({}, "authoring:demo-design"),
    ],
    ids=["link", "path", "empty link", "none reported", "nothing reported"],
)
def test_where_a_document_is_comes_from_the_store_rather_than_from_here(
    location: dict[str, object] | None, expected: str
) -> None:
    """A link where the store puts it on a website, a path where it is a file on this machine."""
    assert design_approval.located(_document(location=location)) == expected


def test_a_project_holding_no_document_names_the_command_that_records_an_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _holds(monkeypatch)
    with pytest.raises(OSError, match="holds no design document"):
        design_approval.design_document("authoring:demo")


def test_a_project_holding_several_documents_is_refused_rather_than_guessed_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An approval recorded against the wrong one of two reads as sound from every side."""
    _holds(
        monkeypatch,
        _document(),
        _document(qualified_id=QualifiedDocumentId("authoring:demo-notes")),
    )
    with pytest.raises(OSError, match="holds 2 documents"):
        design_approval.design_document("authoring:demo")


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (_stamp("plan", "design-doc"), frozenset({"plan", "design-doc"})),
        (_stamp(), None),
        ({design_approval.PLAN_KIND: {"kind": "something else", "nodes": ["plan"]}}, None),
        ({design_approval.PLAN_KIND: {"kind": "planning"}}, None),
        ({design_approval.PLAN_KIND: {"kind": "planning", "nodes": "plan"}}, None),
        ({design_approval.PLAN_KIND: {"kind": "planning", "nodes": [7]}}, None),
        ({design_approval.PLAN_KIND: {"kind": "planning", "nodes": ["plan", "plan"]}}, None),
        ({design_approval.PLAN_KIND: design_approval.PLANNING}, None),
        ({}, None),
        ("not an object", None),
    ],
    ids=[
        "the two nodes a planning launch writes",
        "a launch that named no node",
        "another kind",
        "no nodes named",
        "nodes that are not a list",
        "a node that is not a string",
        "a node claimed twice",
        "the bare marker the exemption used to be granted on",
        "says nothing",
        "unreadable",
    ],
)
def test_the_nodes_a_planning_launch_dispatches_are_read_off_the_project_itself(
    metadata: object, expected: frozenset[str] | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recognised from what the project states, never from its shape.

    A stamp this cannot read claims no exemption, and a stamp naming **no** node is
    answered with those rather than as an exemption over an empty project: it bounds the
    exemption to nothing, and a launch this cannot bound is one it must not exempt.

    A node claimed twice is in that list for a reason of its own: a launch dispatches each
    of its nodes once, so a repeated id is not a set of ids any launch wrote. Collapsing it
    would make the exemption report a launch the stamp does not describe, and counting it
    would let a repeated claim stand in for a node the project holds and the stamp does not.

    The bare `"planning"` string is in that list for the one reason worth stating — it is
    the shape the stamp had while the exemption was scoped to the project, so a project
    stamped by an older `just plan` names no nodes and is gated like any other rather than
    keeping an exemption nothing can bound.
    """
    _project(monkeypatch, metadata)
    assert design_approval.planning_launch("authoring:demo") == expected


def test_approving_records_the_key_and_repeating_it_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, template: design_approval.TemplateFingerprint
) -> None:
    """A retried command and a second person running it are both harmless."""
    written: list[tuple[str, object]] = []

    def write(document: StoreDocument, key: str, value: object) -> None:
        assert document.qualified_id == "authoring:demo-design"
        written.append((key, value))

    monkeypatch.setattr(plan_store, "write_document_metadata", write)
    _holds(monkeypatch, _document())
    answered = design_approval.approve("authoring:demo")
    assert answered.held is False
    assert answered.location == "/test/documents/demo-design.md"
    (key, value) = written[0]
    assert key == design_approval.RECORD_KEY
    assert isinstance(value, dict)
    assert value["key"] == design_approval.approval_key(
        _document(), design_approval.template_fingerprint()
    )
    assert "approved_at" in value

    _holds(monkeypatch, _approved())
    assert design_approval.approve("authoring:demo").held is True
    assert len(written) == 1


def test_the_launch_is_refused_until_the_document_it_holds_is_the_one_approved(
    monkeypatch: pytest.MonkeyPatch, template: design_approval.TemplateFingerprint
) -> None:
    """Approved, then edited, then unapproved again — the whole point of keying on content."""
    _project(monkeypatch, {})
    _holds(monkeypatch, _approved())
    assert design_approval.assess("authoring:demo").refusal is None

    edited = _approved()
    _holds(monkeypatch, _document(metadata=edited.metadata, content="## What\n\nMore.\n"))
    reason = design_approval.assess("authoring:demo").refusal
    assert reason is not None
    assert "carries no approval for what it currently says" in reason
    assert design_approval.RECIPE in reason


def test_a_planning_launch_is_exempt_and_the_gate_says_so_rather_than_passing_it_over(
    monkeypatch: pytest.MonkeyPatch, template: design_approval.TemplateFingerprint
) -> None:
    """Its output is the plan, so the document it is reviewed as does not exist yet.

    The exemption is *reported* rather than answered with the same silence an approval
    gets. That silence is what let an exemption scoped to a project stand unnoticed: from
    the launch's side, dispatching because somebody had read the design document and
    dispatching because nobody had to were the same answer.
    """
    _project(monkeypatch, _stamp("plan", "design-doc"))
    _tasks(monkeypatch, "plan", "design-doc")
    _holds(monkeypatch)
    assessed = design_approval.assess("authoring:demo")
    assert assessed.refusal is None
    assert assessed.exemption is not None
    assert "the plan a planning launch is writing" in assessed.exemption
    assert "design-doc, plan" in assessed.exemption


def test_a_planning_project_holding_work_to_be_executed_is_not_exempt(
    monkeypatch: pytest.MonkeyPatch, template: design_approval.TemplateFingerprint
) -> None:
    """The plan a planner writes into the planning project is not that launch any more.

    This is the whole defect the bound exists for: the exemption was granted to the
    *project*, and a plan stored in the project the planning launch created inherited it —
    so seven nodes of real work across four repositories would have dispatched with the
    design document approved by nobody, and the gate would have said nothing.
    """
    _project(monkeypatch, _stamp("plan", "design-doc"))
    _tasks(monkeypatch, "plan", "design-doc", "adopt-the-release", "close-the-gap")

    # The tasks alone end it, before there is any document in the project to read: the
    # plan's nodes arrive first and the design-doc node writes the document afterwards,
    # so this is the state the project passes through on its way to the one below.
    _holds(monkeypatch)
    strayed = design_approval.assess("authoring:demo")
    assert strayed.exemption is None
    assert strayed.refusal is not None
    assert "holds no design document" in strayed.refusal
    assert "2 task(s) that launch never wrote" in strayed.refusal
    assert "adopt-the-release, close-the-gap" in strayed.refusal

    _holds(monkeypatch, _document())
    assessed = design_approval.assess("authoring:demo")
    assert assessed.exemption is None
    assert assessed.refusal is not None
    assert "carries no approval for what it currently says" in assessed.refusal
    assert "2 task(s) that launch never wrote" in assessed.refusal


def test_a_stamp_claiming_more_than_the_project_holds_does_not_bound_the_exemption(
    monkeypatch: pytest.MonkeyPatch, template: design_approval.TemplateFingerprint
) -> None:
    """The other direction of the agreement, and the one a covering check lets through.

    A stamp naming nodes the project does not hold is bounded to a launch nothing here can
    see — and the bound is checked against what the project holds *now*, so a project that
    is a strict subset of its own stamp goes on being exempt as it grows into those claims.
    That is the same unbounded exemption the tasks half closes, reached from the other end.
    """
    _project(monkeypatch, _stamp("plan", "design-doc", "adopt-the-release"))
    _tasks(monkeypatch, "plan", "design-doc")
    _holds(monkeypatch)
    assessed = design_approval.assess("authoring:demo")
    assert assessed.exemption is None, (
        "a stamp claiming a node the project does not hold still bought the exemption, so "
        "the bound is a covering rather than an agreement"
    )
    assert assessed.refusal is not None
    assert "holds no design document" in assessed.refusal
    assert "claims 1 node(s) the project does not hold" in assessed.refusal
    assert "adopt-the-release" in assessed.refusal
    assert "task(s) that launch never wrote" not in assessed.refusal, (
        "the lapse reports the direction that did not end the exemption"
    )


def test_both_directions_of_the_disagreement_are_reported_at_once(
    monkeypatch: pytest.MonkeyPatch, template: design_approval.TemplateFingerprint
) -> None:
    """A stamp that overlaps the project's tasks without agreeing with them.

    Said apart because they read differently: one is a project that has grown past the
    launch that wrote it, and the other a stamp describing a launch this project is not.
    """
    _project(monkeypatch, _stamp("plan", "adopt-the-release"))
    _tasks(monkeypatch, "plan", "close-the-gap")
    _holds(monkeypatch)
    assessed = design_approval.assess("authoring:demo")
    assert assessed.exemption is None
    assert assessed.refusal is not None
    assert "1 task(s) that launch never wrote (close-the-gap)" in assessed.refusal
    assert "claims 1 node(s) the project does not hold (adopt-the-release)" in assessed.refusal


def test_a_claim_repeated_cannot_stand_in_for_a_node_the_project_holds(
    monkeypatch: pytest.MonkeyPatch, template: design_approval.TemplateFingerprint
) -> None:
    """A stamp of the right length, naming one of the project's two tasks twice.

    The shape a check that counted claims rather than reading them would accept, and the
    one a charitable collapse would answer with an exemption bounded to a launch the stamp
    does not describe. It is read as no claim to the exemption at all, so the project is
    gated like any other rather than reported as a planning launch whose bound has ended.
    """
    _project(monkeypatch, _stamp("plan", "plan"))
    _tasks(monkeypatch, "plan", "design-doc")
    _holds(monkeypatch)
    assessed = design_approval.assess("authoring:demo")
    assert assessed.exemption is None
    assert assessed.refusal is not None
    assert "holds no design document" in assessed.refusal
    assert "is stamped as the plan a planning launch writes" not in assessed.refusal


def test_a_planning_project_stops_being_exempt_once_it_holds_a_document_to_read(
    monkeypatch: pytest.MonkeyPatch, template: design_approval.TemplateFingerprint
) -> None:
    """The other half of the bound: the exemption is for a launch with nothing to approve.

    Once the design-doc node has written one there is something a person can read, so the
    exemption has nothing left to stand on and the ordinary refusal applies — and the
    approval that answers it is one command.
    """
    _project(monkeypatch, _stamp("plan", "design-doc"))
    _tasks(monkeypatch, "plan", "design-doc")
    _holds(monkeypatch, _document())
    assessed = design_approval.assess("authoring:demo")
    assert assessed.exemption is None
    assert assessed.refusal is not None
    assert "1 design document(s), so there is something to read" in assessed.refusal

    _holds(monkeypatch, _approved())
    approved = design_approval.assess("authoring:demo")
    assert approved.refusal is None, approved.refusal
    assert approved.exemption is None, (
        "an approved planning project was let through on the exemption rather than on the "
        "approval, so the two are still one answer"
    )


def test_a_project_stamped_by_an_older_planning_launch_is_gated_like_any_other(
    monkeypatch: pytest.MonkeyPatch, template: design_approval.TemplateFingerprint
) -> None:
    """A stamp naming no nodes bounds nothing, so it exempts nothing.

    The safe direction: an exemption this cannot bound to a launch is refused rather than
    granted for the life of the project, which is exactly what it was.
    """
    _project(monkeypatch, {design_approval.PLAN_KIND: design_approval.PLANNING})
    _holds(monkeypatch)
    assessed = design_approval.assess("authoring:demo")
    assert assessed.exemption is None
    assert assessed.refusal is not None
    assert "holds no design document" in assessed.refusal


def test_a_project_with_no_document_is_refused_as_that_rather_than_as_unapproved(
    monkeypatch: pytest.MonkeyPatch, template: design_approval.TemplateFingerprint
) -> None:
    """The two refusals owe different next actions, so they are told apart in the text."""
    _project(monkeypatch, {})
    _holds(monkeypatch)
    reason = design_approval.assess("authoring:demo").refusal
    assert reason is not None
    assert "holds no design document" in reason


def test_the_recipe_reports_what_it_recorded_and_where(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    template: design_approval.TemplateFingerprint,
) -> None:
    monkeypatch.setattr(plan_store, "write_document_metadata", lambda *_a: None)
    _holds(monkeypatch, _document())
    assert design_approval.main(["authoring:demo"]) == 0
    reported = capsys.readouterr().out
    assert "recorded the approval of authoring:demo-design" in reported
    assert str(design_approval.TEMPLATE) in reported

    _holds(monkeypatch, _approved())
    assert design_approval.main(["authoring:demo"]) == 0
    assert "already carries an approval" in capsys.readouterr().out


def test_the_recipe_refuses_a_project_with_nothing_to_approve(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _holds(monkeypatch)
    assert design_approval.main(["authoring:demo"]) == 1
    assert "holds no design document" in capsys.readouterr().err


def test_the_recipe_refuses_an_argument_that_names_a_project_in_no_store(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one thing asked of the argument, asked before anything is asked of the store.

    Proven by making every store read this command could make an error: the refusal has
    to come from the shape of the argument, because reaching the store at all would fail
    the test rather than answer it. That is the boundary the check is for — a bare project
    name is text somebody typed, and it reaches a subprocess and a store otherwise.
    """

    def never(project: str) -> list[StoreDocument]:
        raise AssertionError(f"an unqualified argument {project!r} reached the plan store")

    monkeypatch.setattr(plan_store, "read_documents", never)
    for argument in ("demo", "authoring:", ":demo", "authoring:a demo", "authoring/demo"):
        assert design_approval.main([argument]) == 1, argument
        refused = capsys.readouterr().err
        assert repr(argument) in refused, refused
        assert "is not a qualified project id" in refused, refused
        assert "`<source>:<project>`" in refused, refused


@pytest.mark.parametrize("argument", [SOURCE_PROJECT, BOARD_PROJECT, "a.source_name-1:x/y"])
def test_a_qualified_id_is_handed_on_as_it_was_typed(argument: str) -> None:
    """The other half: the check refuses a shape and never rewrites one it accepts."""
    assert design_approval.qualified(argument) == argument


def test_the_recipe_takes_no_flag_that_would_get_a_plan_past_the_gate() -> None:
    """There is no escape hatch and none is coming, so the parser has nowhere to put one.

    Asserted on the surface rather than on the absence of a branch, because an escape
    here is reached under exactly the time pressure that makes skipping this a mistake —
    and the first person to want one will reach for a flag rather than for the code.
    """
    for attempted in (["authoring:demo", "--force"], ["authoring:demo", "--skip"]):
        with pytest.raises(SystemExit) as refused:
            design_approval.main(attempted)
        assert refused.value.code == 2


def _sources(monkeypatch: pytest.MonkeyPatch, answer: object) -> None:
    monkeypatch.setattr(plan_store, "store_json", lambda _arguments: {"settings": answer})


def test_the_configured_source_names_are_read_out_of_the_stores_own_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read rather than restated: `onetaskgraph.yaml` is layered under variables and flags."""
    _sources(
        monkeypatch,
        [
            {"key": "sources.authoring.plugin", "value": "local-md"},
            {"key": "sources.plans.config.owner", "value": "nickderobertis"},
            {"key": "default_sources", "value": "authoring"},
            {"key": 7, "value": "not a key"},
            "not an object",
        ],
    )
    assert design_approval.configured_sources() == frozenset({"authoring", "plans"})


def test_a_configuration_without_a_settings_list_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sources(monkeypatch, "not a list")
    with pytest.raises(OSError, match="without a settings list"):
        design_approval.configured_sources()


def _gated(
    monkeypatch: pytest.MonkeyPatch,
    assessments: (
        Mapping[str, design_approval.Assessment] | Callable[[str], design_approval.Assessment]
    ),
) -> None:
    """Configure the gate with `authoring` and `plans` as sources and a canned verdict."""
    monkeypatch.setattr(
        design_approval, "configured_sources", lambda: frozenset({"authoring", "plans"})
    )
    if callable(assessments):
        monkeypatch.setattr(design_approval, "assess", assessments)
    else:
        monkeypatch.setattr(design_approval, "assess", lambda project: assessments[project])


def test_the_gate_asks_about_the_project_a_launch_names_wherever_it_sits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The launch's flag grammar is not restated here, so position decides nothing."""
    asked: list[str] = []

    def answer(project: str) -> design_approval.Assessment:
        asked.append(project)
        return design_approval.Assessment()

    _gated(monkeypatch, answer)
    assert (
        design_approval.gate_main(
            ["--detach", "--dag-graph", "graphs/dag-scope.yaml", "authoring:demo"]
        )
        == 0
    )
    assert asked == ["authoring:demo"]


@pytest.mark.parametrize(
    "argument",
    ["--filter-profile", "graphs/dag-scope.yaml", "elsewhere:demo", "1800", "a=b"],
    ids=["a flag", "a graph ref", "an unconfigured source", "a number", "a setting"],
)
def test_the_gate_asks_nothing_about_an_argument_that_names_no_configured_project(
    argument: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flag value that is not a project is not a project any source answers for."""

    def never(project: str) -> design_approval.Assessment:
        raise AssertionError(f"the gate asked the store about {project!r}")

    _gated(monkeypatch, never)
    assert design_approval.gate_main([argument]) == 0


def test_the_gate_refuses_an_unapproved_project_and_says_nothing_was_dispatched(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _gated(
        monkeypatch,
        {"authoring:demo": design_approval.Assessment(refusal="authoring:demo has no approval")},
    )
    assert design_approval.gate_main(["authoring:demo"]) == 1
    reported = capsys.readouterr().err
    assert "authoring:demo has no approval" in reported
    assert "nothing was dispatched" in reported


def test_the_gate_reports_the_exemption_it_dispatched_on(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An approval and an exemption are two answers, so the launch does not give them one.

    Reported even beside a refusal, because the two projects on one command line are two
    plans: one of them dispatching with nobody having approved anything is worth saying
    whatever happened to the other.
    """
    _gated(
        monkeypatch,
        {
            "authoring:planning": design_approval.Assessment(
                exemption="authoring:planning is the plan a planning launch is writing"
            ),
            "plans:work": design_approval.Assessment(refusal="plans:work has no approval"),
        },
    )
    assert design_approval.gate_main(["authoring:planning", "plans:work"]) == 1
    reported = capsys.readouterr().err
    assert "is the plan a planning launch is writing" in reported
    assert "plans:work has no approval" in reported


def test_a_project_the_store_cannot_answer_for_is_refused_rather_than_skipped(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The engine reads the same project through the same store, so this loses no launch."""

    def unreadable(_project: str) -> design_approval.Assessment:
        raise OSError("onetaskgraph exited 1")

    _gated(monkeypatch, unreadable)
    assert design_approval.gate_main(["plans:demo"]) == 1
    assert "could not be read out of the plan store" in capsys.readouterr().err


def test_a_plan_store_that_cannot_be_read_at_all_is_its_own_exit_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Whether the plan has an approved design document is then unknown, not answered."""

    def unreadable() -> frozenset[str]:
        raise OSError("onetaskgraph is not installed on PATH")

    monkeypatch.setattr(design_approval, "configured_sources", unreadable)
    assert design_approval.gate_main(["authoring:demo"]) == 2
    assert "plan store could not be read" in capsys.readouterr().err


def test_the_gate_reads_the_command_line_it_was_given_when_it_is_handed_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`orchestrator-launch-gate` is spawned by `scripts/onepipeline.sh`, which passes argv."""
    _gated(monkeypatch, {"authoring:demo": design_approval.Assessment()})
    monkeypatch.setattr("sys.argv", ["orchestrator-launch-gate", "authoring:demo"])
    assert design_approval.gate_main() == 0


#: The source this repository ships its example projects in. Every one of them is
#: launchable — `README.md` names three with `just orchestrate` — so every one of them has
#: to carry the approval a launch is now refused without.
EXAMPLES = "examples"


@pytest.mark.reads_docs
def test_every_shipped_example_project_carries_an_approved_design_document() -> None:
    """The examples are launchable, and a launch is refused without this.

    It fails in exactly the two cases the doctrine says it should, and both are somebody's
    to act on rather than defects here: an example project added without a design document,
    and a change to `config/design-doc-template.md`, which invalidates every approval
    granted under the previous shape. The repair for the second is to read each document
    against the new template and run the recipe again — which is what the gate is for.
    """
    projects = plan_store.local_projects(EXAMPLES)
    assert projects, f"the {EXAMPLES!r} source ships no project, so this proves nothing"
    unapproved = [
        assessed.refusal
        for assessed in (design_approval.assess(project) for project in projects)
        if assessed.refusal
    ]
    assert not unapproved, (
        "shipped example projects this repository documents as launchable would be refused "
        "a launch:\n" + "\n".join(f"  - {reason}" for reason in unapproved)
    )


def _follow_ups_stamp(*nodes: str) -> Mapping[str, object]:
    """The metadata `scripts/follow-ups.sh` writes onto the project a follow-ups launch makes."""
    return {
        design_approval.PLAN_KIND: {
            design_approval.STAMP_KIND: design_approval.FOLLOW_UPS,
            design_approval.STAMP_NODES: list(nodes),
        }
    }


def test_a_follow_ups_launch_is_exempt_for_its_one_node_and_the_gate_says_so(
    monkeypatch: pytest.MonkeyPatch, template: design_approval.TemplateFingerprint
) -> None:
    """There is no plan to read that project as, so its one node launches unapproved — said."""
    _project(monkeypatch, _follow_ups_stamp("follow-ups"))
    _tasks(monkeypatch, "follow-ups")
    _holds(monkeypatch)

    assessed = design_approval.assess("authoring:run-1-follow-ups")

    assert assessed.refusal is None
    assert assessed.exemption is not None
    assert "is the project a follow-ups launch writes" in assessed.exemption
    assert "(follow-ups)" in assessed.exemption
    assert "drafted follow-ups" in assessed.exemption
    assert design_approval.stamped_launch("authoring:run-1-follow-ups") == (
        design_approval.StampedLaunch(design_approval.FOLLOW_UPS, frozenset({NodeId("follow-ups")}))
    )
    assert design_approval.planning_launch("authoring:run-1-follow-ups") is None, (
        "a follow-ups stamp is not a planning launch, whatever reads it as one"
    )


def test_a_follow_ups_project_that_gained_a_second_node_is_refused_like_any_other(
    monkeypatch: pytest.MonkeyPatch, template: design_approval.TemplateFingerprint
) -> None:
    _project(monkeypatch, _follow_ups_stamp("follow-ups"))
    _tasks(monkeypatch, "follow-ups", "work-somebody-added")
    _holds(monkeypatch)

    assessed = design_approval.assess("authoring:run-1-follow-ups")

    assert assessed.exemption is None
    assert assessed.refusal is not None
    assert "holds no design document" in assessed.refusal
    assert "1 task(s) that launch never wrote (work-somebody-added)" in assessed.refusal
    assert "stamped as the project a follow-ups launch writes" in assessed.refusal


def test_a_follow_ups_stamp_naming_a_node_the_project_does_not_hold_is_refused(
    monkeypatch: pytest.MonkeyPatch, template: design_approval.TemplateFingerprint
) -> None:
    _project(monkeypatch, _follow_ups_stamp("follow-ups"))
    _tasks(monkeypatch, "something-else")
    _holds(monkeypatch)

    assessed = design_approval.assess("authoring:run-1-follow-ups")

    assert assessed.exemption is None
    assert assessed.refusal is not None
    assert "claims 1 node(s) the project does not hold (follow-ups)" in assessed.refusal


@pytest.mark.parametrize("nodes", [(), ("follow-ups", "second")], ids=["none", "two"])
def test_a_follow_ups_stamp_naming_anything_but_one_node_bounds_nothing(
    monkeypatch: pytest.MonkeyPatch,
    template: design_approval.TemplateFingerprint,
    nodes: tuple[str, ...],
) -> None:
    """That launch writes one node, so a stamp of its kind naming more is no claim at all."""
    _project(monkeypatch, _follow_ups_stamp(*nodes))
    _tasks(monkeypatch, *nodes)
    _holds(monkeypatch)

    assert design_approval.stamped_launch("authoring:run-1-follow-ups") is None
    assessed = design_approval.assess("authoring:run-1-follow-ups")
    assert assessed.exemption is None
    assert assessed.refusal is not None
    assert "follow-ups launch" not in assessed.refusal
