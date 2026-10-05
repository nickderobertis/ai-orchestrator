"""What `just approve-design` records, and what a launch refuses without it.

The flow is driven for real in
`tests/plan_tooling/test_approve_design_recipe_e2e.py` — the real recipe, the real store,
and a real `just orchestrate` refused and then let through. What is proven here is the
part of the decision no journey reaches without breaking this checkout: how the key is
composed, what a record that cannot be read means, and how the launch gate reads a command
line it deliberately does not parse.

The key is the half worth stating. It covers the document's title, the design-doc
template's chain digest as it resolves now, and the body digest the store recorded when it
rendered the document — and a document is keyable only while it *is* that rendering: one
recording no design-doc provenance, rendered from a chain that is not the one in force, or
edited after it was rendered is refused, naming the regenerate that repairs it. So a
passing test here is a claim about both halves: editing the document loses its approval,
and changing the template invalidates every approval granted under the previous one. The
other direction is the one a copied plan pays for: nothing the store the document sits in
owns is in the key, so a record that travels arrives matching what the destination
computes.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from project_fixtures import ONEVCS_HOME, register_stand_in

from orchestrator import design_approval, design_chain, plan_store
from orchestrator.plan_store import (
    NodeId,
    QualifiedDocumentId,
    QualifiedTaskId,
    StoreDocument,
    StoreTask,
)
from orchestrator.root import REPO_ROOT

#: The chain digest a document is approved under, stated here rather than resolved through
#: this checkout's own `templates/`. Every test below that is not *about* the resolve takes
#: it, and the reason is a cache key rather than taste: the tier these run in is memoized on
#: the workspace **minus its prose**, and holding the digest still also makes each of these a
#: claim about the key rather than about what the template currently says.
STATED = design_approval.ChainDigest("sha256:" + "a" * 64)

#: That same chain after somebody changed the template. Distinct from :data:`STATED` and
#: meaningless beyond being distinct: what an approval turns on is whether the digest in
#: force is the one the document was rendered from.
MOVED = design_approval.ChainDigest("sha256:" + "b" * 64)

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


#: The rule and the shape check as shipped, before the fixture below holds them still, for
#: the tests that read them against a real store.
RULE = design_chain.plan_repository
FITS = design_approval.fits_in_place


@pytest.fixture(autouse=True)
def _the_working_directory_layer_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pick no repository's layer for every plan here, and read no stored answers.

    The plans these tests approve exist only in the doubled store reads above, so which
    repository's layer their chain resolves through is answered as the rule answers a plan
    naming none, and a document rendered under another chain is answered as one whose
    stored answers still fit. The rule itself is `tests/test_design_chain.py`'s, and both
    are driven against real stores in `tests/plan_tooling/test_approve_design_recipe_e2e.py`.
    """
    monkeypatch.setattr(design_approval.design_chain, "plan_repository", lambda _project: None)
    monkeypatch.setattr(design_approval, "fits_in_place", lambda *_arguments: True)


@pytest.fixture
def template(monkeypatch: pytest.MonkeyPatch) -> design_approval.ChainDigest:
    """Hold the design-doc chain still, and off this checkout's own templates."""
    monkeypatch.setattr(design_approval, "resolved_digest", lambda *_arguments: STATED)
    return STATED


def _provenance(
    content: str,
    *,
    digest: str = STATED,
    template: str = design_approval.TEMPLATE_REFERENCE,
) -> dict[str, object]:
    """The provenance the plan store records on a rendering of ``content``."""
    body = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
    return {
        design_approval.PROVENANCE: {
            "template": template,
            "digest": digest,
            "body_digest": body,
            "answers_digest": "sha256:" + "c" * 64,
        }
    }


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
    rendered: bool = True,
) -> StoreDocument:
    """One design document as the store reports it, with anything a test states of it.

    Keyword parameters rather than a merged mapping, so what a test may vary is stated
    and typed: the shape a `**overrides` helper takes is `object`, which types the call
    site as accepting anything and needs an escape at the constructor to get back out.
    ``rendered`` gives it the provenance a rendering of ``content`` under :data:`STATED`
    records, under anything ``metadata`` states.
    """
    held = (_provenance(content) if rendered else {}) | dict(metadata or {})
    return StoreDocument(
        qualified_id=QualifiedDocumentId(qualified_id),
        title=title,
        content=content,
        project=project,
        labels=labels or [],
        repositories=repositories or [],
        metadata=held,
        location=location,
    )


def _approved(document: StoreDocument | None = None) -> StoreDocument:
    """``document`` carrying a record for exactly the content it currently states."""
    held = document or _document()
    key = design_approval.approval_key(held, design_approval.resolved_digest())
    return _document(
        content=held.content,
        title=held.title,
        project=held.project,
        metadata=dict(held.metadata) | {design_approval.RECORD_KEY: {"key": key}},
    )


def _otherwise(document: StoreDocument, field: str) -> StoreDocument:
    """``document`` with ``field`` holding a different value of that field's own type.

    Content is moved the one way a rendered document's content moves without refusal — by
    regenerating it, so its provenance names the new body — and metadata by writing beside
    the provenance rather than over it, since a map without it is not a rendering at all.
    """
    if field not in OTHERWISE:
        raise AssertionError(
            f"{field!r} is a field of a store document that this module states no second "
            f"value for, so nothing here says whether the approval key covers it"
        )
    match field, OTHERWISE[field]:
        case "content", str() as regenerated:
            return dataclasses.replace(
                document,
                content=regenerated,
                metadata=dict(document.metadata) | _provenance(regenerated),
            )
        case "metadata", Mapping() as bookkeeping:
            rendering = {design_approval.PROVENANCE: document.metadata[design_approval.PROVENANCE]}
            return dataclasses.replace(document, metadata=dict(bookkeeping) | rendering)
        case _, value:
            return dataclasses.replace(document, **{field: value})


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


def test_the_key_covers_the_template_the_document_was_rendered_from() -> None:
    """And the bar, because an approval is of one document under one template."""
    regenerated = _document(metadata=_provenance(APPROVED_PROSE, digest=MOVED))
    assert design_approval.approval_key(regenerated, MOVED) != design_approval.approval_key(
        _document(), STATED
    )


@pytest.mark.parametrize(
    ("document", "said"),
    [
        (_document(rendered=False), "records no rendering of the design-doc template"),
        (
            _document(metadata={design_approval.PROVENANCE: {"template": "x"}}),
            "records no rendering of the design-doc template",
        ),
        (
            _document(metadata=_provenance(APPROVED_PROSE, template="onepipeline:plan-task")),
            "is a rendering of onepipeline:plan-task, not of onepipeline:design-doc",
        ),
        (
            _document(metadata=_provenance(APPROVED_PROSE, digest=MOVED)),
            "written against a template no longer in force",
        ),
        (
            _document(metadata=_provenance("## What\n\nWhat was rendered.\n")),
            "was edited after it was rendered",
        ),
    ],
    ids=["no provenance", "unreadable provenance", "foreign template", "stale chain", "edited"],
)
def test_a_document_that_is_not_the_rendering_in_force_has_no_key(
    document: StoreDocument, said: str
) -> None:
    """Each is refused naming the regenerate that repairs it, rather than keyed and unapproved."""
    with pytest.raises(design_approval.Unrendered) as refused:
        design_approval.approval_key(document, STATED)
    reason = str(refused.value)
    assert said in reason, reason
    assert "onepipeline template resolve design-doc --json | onetaskgraph document render" in (
        reason
    ), reason
    assert "authoring:demo-design --template-loader -" in reason, reason


#: The content a copy holds once an earlier plan store rewrote its references, under the
#: provenance of the rendering it was copied from.
RECOPIED = _provenance("## What\n\nWhat was rendered, naming /home/someone/tasks/demo.md.\n")


@pytest.mark.parametrize(
    "origin",
    [None, "", 7, "no-source-here"],
    ids=["no origin", "an empty origin", "an origin that is no string", "an unqualified origin"],
)
def test_a_mismatched_document_that_is_no_copy_is_refused_as_edited_as_it_always_was(
    origin: object,
) -> None:
    """A document carrying no readable `onetaskgraph.origin` keeps the refusal it always had."""
    stated = {} if origin is None else {plan_store.ORIGIN_KEY: origin}
    with pytest.raises(design_approval.Unrendered) as refused:
        design_approval.approval_key(_document(metadata=RECOPIED | stated), STATED)
    reason = str(refused.value)
    assert "rendering to sha256:" in reason, reason
    assert "), so it was edited after it was rendered; change its answers and regenerate" in (
        reason
    ), reason
    assert "earlier plan store" not in reason and "just copy-plan" not in reason, reason


def test_a_mismatched_copy_has_no_key_and_is_sent_to_its_origin_beside_the_regenerate() -> None:
    """A copy is refused like any mismatch, naming the re-copy that re-records its digest.

    It is still refused: whatever left the content off its recorded digest, a document that
    is not the rendering its provenance records has no approval key.
    """
    copied = _document(
        qualified_id="plans:I_1",
        metadata=RECOPIED | {plan_store.ORIGIN_KEY: "authoring:demo-design"},
    )
    with pytest.raises(design_approval.Unrendered) as refused:
        design_approval.approval_key(copied, STATED)
    reason = str(refused.value)
    assert "a copy of authoring:demo-design made by an earlier plan store" in reason, reason
    assert "rewrote its references without re-recording its digest" in reason, reason
    assert "`just copy-plan authoring:<project>`" in reason, reason
    assert "or it was edited after it was rendered; change its answers and regenerate" in (
        reason
    ), reason
    assert "plans:I_1 --template-loader -" in reason, reason


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
    """``document`` carrying an approval for its current content."""
    del monkeypatch
    record = {
        "key": design_approval.approval_key(document, design_approval.resolved_digest()),
        "approved_at": "not-read-by-this-test",
    }
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
    in_force: design_approval.ChainDigest,
    monkeypatch: pytest.MonkeyPatch,
    template: design_approval.ChainDigest,
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

    # Moved the one way a rendered document moves without being refused as unrendered:
    # regenerated, so its provenance names the chain in force and the body it now says.
    monkeypatch.setattr(design_approval, "resolved_digest", lambda *_arguments: in_force)
    regenerated = dict(approved.metadata) | _provenance(prose, digest=in_force)
    _holds(
        monkeypatch,
        _elsewhere(_document(title=title, content=prose, metadata=regenerated)),
    )
    reason = design_approval.assess(BOARD_PROJECT).refusal
    assert reason is not None, f"the copy is still approved after {moved} moved"
    assert "carries no approval for what it currently says" in reason
    assert design_approval.RECIPE in reason
    assert "https://github.invalid" in reason, (
        f"the refusal does not say where the travelled document is, so the person who has "
        f"to read it again is sent to wherever it was drafted instead: {reason}"
    )


# llmlint: ignore[test_tiers_split_by_project_not_by_marker, shell_test_tiers_stay_split] It
# is keyed right: `templates/templates.yaml` is in the key this tier is memoized on, and
# `nx.json`'s `codeWorkspace` is the whole workspace less `docs/` and `*.md`; the engine it
# spawns is the pinned install `uv.lock` names — the workspace's own, not a host tool — and
# this test covers `resolved_digest` under the tier's 100% coverage floor.
def test_a_changed_template_resolves_to_a_different_chain_digest(tmp_path: Path) -> None:
    """The digest is the pinned engine's answer through a host root, so the template decides it.

    Taken over two host roots of this test's own rather than this checkout's `templates/`,
    for the reason :data:`STATED` gives — and it says the same thing either way, since what
    is under test is that the template's bytes, resolved by the real engine, decide.
    """
    digests: list[design_approval.ChainDigest] = []
    for shape in ("## What\n\n{{ what }}\n", "## What\n\n{{ what }}, changed\n"):
        root = tmp_path / f"root-{len(digests)}"
        root.mkdir()
        shutil.copyfile(REPO_ROOT / "templates" / "templates.yaml", root / "templates.yaml")
        (root / "design-doc.md.j2").write_text(
            "---\nonetaskgraph_template: 1\nvariables:\n  what:\n    description: w\n"
            "    type: text\n---\n" + shape,
            encoding="utf-8",
        )
        digests.append(design_approval.resolved_digest(root))
    assert all(digest.startswith("sha256:") for digest in digests), digests
    assert digests[0] != digests[1]
    assert design_approval.resolved_digest(tmp_path / "root-0") == digests[0]


# llmlint: ignore[shell_test_tiers_stay_split] Not a shell suite and not a host tool: the
# engine is the workspace's own locked install, in this tier's key, and this test covers
# `resolved_digest`'s refusal under the tier's 100% coverage floor.
def test_a_host_root_that_registers_no_design_doc_template_is_refused_by_name(
    tmp_path: Path,
) -> None:
    """The engine's own refusal is carried through, so no approval is keyed on nothing."""
    with pytest.raises(OSError, match="`onepipeline template resolve design-doc` refused"):
        design_approval.resolved_digest(tmp_path)


def test_an_unprovisioned_checkout_is_told_to_bootstrap_rather_than_keyed_on_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checkout with no pinned engine cannot say what the template resolves to."""
    monkeypatch.setattr(design_approval, "REPO_ROOT", tmp_path)
    with pytest.raises(OSError, match="`just bootstrap`"):
        design_approval.resolved_digest(tmp_path)


@pytest.mark.parametrize(
    "stated",
    [
        "not json",
        '{"reference": "onepipeline:design-doc"}',
        '{"reference": "onepipeline:design-doc", "digest": 7}',
        '{"reference": "onepipeline:design-doc", "digest": ""}',
        '{"reference": "onepipeline:design-doc", "digest": "sha256:not-hex"}',
        '{"reference": "onepipeline:plan-task", "digest": "sha256:' + "a" * 64 + '"}',
    ],
    ids=["not json", "no digest", "not a string", "empty", "not a sha256 digest", "another"],
)
def test_a_resolve_that_states_no_digest_is_refused_rather_than_keyed_on(
    stated: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The engine is the published CLI this boundary delegates to, so it alone is doubled."""
    engine = tmp_path / ".venv" / "bin" / "onepipeline"
    engine.parent.mkdir(parents=True)
    # llmlint: ignore[e2e_not_mocked] Only the published engine this module spawns is doubled, to
    # state an answer the real one never gives.  # noqa: E501
    engine.write_text(f"#!/bin/sh\ncat <<'END'\n{stated}\nEND\n", encoding="utf-8")
    engine.chmod(0o755)
    monkeypatch.setattr(design_approval, "REPO_ROOT", tmp_path)
    with pytest.raises(OSError, match="stated no digest"):
        design_approval.resolved_digest(tmp_path)


@pytest.mark.parametrize(
    ("engine", "said"),
    [
        (None, "`just bootstrap`"),
        ("echo 'refused: no such template' >&2; exit 2", "refused: no such template"),
        ('echo \'{"digest": "sha256:short"}\'', "stated no digest"),
    ],
    ids=["no engine", "the engine refuses", "a malformed digest"],
)
def test_an_engine_that_cannot_state_the_digest_refuses_the_approval_and_the_launch(
    engine: str | None,
    said: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Both entry points refuse, naming why, rather than keying an approval on nothing.

    Driven through `just approve-design`'s entry point and the launch gate's, over a store
    holding one rendered document, with the published engine this module spawns doubled.
    """
    if engine is not None:
        doubled = tmp_path / ".venv" / "bin" / "onepipeline"
        doubled.parent.mkdir(parents=True)
        # llmlint: ignore[e2e_not_mocked] Only the published engine this module spawns is
        # doubled, to fail the way the real one fails on a broken checkout.
        doubled.write_text(f"#!/bin/sh\n{engine}\n", encoding="utf-8")
        doubled.chmod(0o755)
    monkeypatch.setattr(design_approval, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        design_approval, "configured_sources", lambda: frozenset({"authoring", "plans"})
    )
    _project(monkeypatch, {})
    _holds(monkeypatch, _document())

    assert design_approval.main(["authoring:demo"]) == 1
    assert said in capsys.readouterr().err
    assert design_approval.gate_main(["authoring:demo"]) == 1
    refused = capsys.readouterr().err
    assert said in refused, refused
    assert "nothing was dispatched" in refused, refused


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


def test_the_launch_is_refused_until_the_document_it_holds_is_the_one_approved(
    monkeypatch: pytest.MonkeyPatch, template: design_approval.ChainDigest
) -> None:
    """Approved, then changed, then unapproved again — the whole point of keying on content.

    Changed both ways a document changes: edited by hand, which is refused as no longer the
    rendering it records and names the regenerate; and regenerated from new answers, which
    is a rendering nobody has approved and names the recipe that approves it.
    """
    _project(monkeypatch, {})
    _holds(monkeypatch, _approved())
    assert design_approval.assess("authoring:demo").refusal is None

    approved = _approved().metadata
    more = "## What\n\nMore.\n"
    _holds(monkeypatch, _document(metadata=approved, content=more))
    edited = design_approval.assess("authoring:demo").refusal
    assert edited is not None
    assert "was edited after it was rendered" in edited
    assert (
        design_approval.REGENERATE.replace(
            design_approval.RESOLVE, design_chain.resolve_command(None)
        ).replace("<id>", "authoring:demo-design")
        in edited
    )

    _holds(monkeypatch, _document(metadata=dict(approved) | _provenance(more), content=more))
    regenerated = design_approval.assess("authoring:demo").refusal
    assert regenerated is not None
    assert "carries no approval for what it currently says" in regenerated
    assert design_approval.RECIPE in regenerated


def test_a_planning_launch_is_exempt_and_the_gate_says_so_rather_than_passing_it_over(
    monkeypatch: pytest.MonkeyPatch, template: design_approval.ChainDigest
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
    monkeypatch: pytest.MonkeyPatch, template: design_approval.ChainDigest
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
    monkeypatch: pytest.MonkeyPatch, template: design_approval.ChainDigest
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
    monkeypatch: pytest.MonkeyPatch, template: design_approval.ChainDigest
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
    monkeypatch: pytest.MonkeyPatch, template: design_approval.ChainDigest
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
    monkeypatch: pytest.MonkeyPatch, template: design_approval.ChainDigest
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
    monkeypatch: pytest.MonkeyPatch, template: design_approval.ChainDigest
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
    monkeypatch: pytest.MonkeyPatch, template: design_approval.ChainDigest
) -> None:
    """The two refusals owe different next actions, so they are told apart in the text."""
    _project(monkeypatch, {})
    _holds(monkeypatch)
    reason = design_approval.assess("authoring:demo").refusal
    assert reason is not None
    assert "holds no design document" in reason


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


# llmlint: ignore[shell_test_tiers_stay_split] The subject is the shipped examples, which only
# this project's whole-workspace target is keyed on; what this adds is a registry entry for
# the one origin an example plan names alone, in a registry under this test's own temporary
# directory, through the `onevcs` this repository's session setup installs at the release
# `config/onevcs.version` pins — no shell suite and no host state.
def _stand_ins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, projects: list[str]) -> None:
    """Register a checkout for every origin the rule picks for one of ``projects``.

    An example plan whose tasks all name one repository resolves through that repository's
    registered checkout, so its approval is answered on a host that registers one: each
    stand-in carries no layer of its own, which is what such a checkout without an override
    is, and it is registered in a registry of this test's own.
    """
    monkeypatch.setattr(design_chain, "plan_repository", RULE)
    home = tmp_path / "onevcs"
    monkeypatch.setenv(ONEVCS_HOME, str(home))
    origins = sorted(o for o in {RULE(project) for project in projects} if o is not None)
    for index, origin in enumerate(origins):
        register_stand_in(home, origin, tmp_path / f"stand-in-{index}")


# llmlint: ignore[test_tiers_split_by_project_not_by_marker] `reads_docs` routes between this
# project's own two targets and never out of it (`tests/conftest.py`'s `READS_DOCS_MARKER`): the
# examples are Markdown the code tier's key leaves out, and the whole-workspace target is the
# one keyed on them.
@pytest.mark.reads_docs
def test_every_shipped_example_carries_a_persons_approval_current_or_left_stale_by_the_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every example carries an approval a person recorded, and only the chain may stale it.

    Approving a design document is a person's act, so no change to this repository records
    one for an example: a change to `templates/design-doc.md.j2` leaves every example's
    approval standing over the chain it was granted under, and the gate refuses each one as
    rendered from a template no longer in force until a person reads the regenerated
    document and runs `just approve-design` again. So an example passes here when its
    approval is current, or when it carries an approval record and the only thing refused is
    the moved chain. An example carrying no approval at all, or refused for anything else —
    no document, an edited body — fails, because nothing but a person could repair that.
    """
    projects = plan_store.local_projects(EXAMPLES)
    assert projects, f"the {EXAMPLES!r} source ships no project, so this proves nothing"
    _stand_ins(tmp_path, monkeypatch, projects)
    unapproved: list[str] = []
    for project in projects:
        assessed = design_approval.assess(project)
        if assessed.refusal is None:
            continue
        document = design_approval.design_document(project)
        stale = (
            design_approval.recorded(document) is not None
            and "that template now resolves to" in assessed.refusal
        )
        if not stale:
            unapproved.append(f"{project}: {assessed.refusal}")
    assert not unapproved, (
        "shipped example projects carry no approval a person recorded, or are refused for "
        "something other than the moved template chain:\n"
        + "\n".join(f"  - {reason}" for reason in unapproved)
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
    monkeypatch: pytest.MonkeyPatch, template: design_approval.ChainDigest
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
    monkeypatch: pytest.MonkeyPatch, template: design_approval.ChainDigest
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
    monkeypatch: pytest.MonkeyPatch, template: design_approval.ChainDigest
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
    template: design_approval.ChainDigest,
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


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] `reads_docs` routes between this
# project's own two targets and never out of it (`tests/conftest.py`'s `READS_DOCS_MARKER`): the
# examples are Markdown the code tier's key leaves out, exactly as the shipped-examples test above
# it reads them.
@pytest.mark.reads_docs
def test_regenerating_a_shipped_example_keeps_its_body_and_approves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each example is a rendering with stored answers, and regenerating it approves nothing.

    Driven through the pinned engine's real resolve piped into the pinned plan store's real
    `document render`, over a copy of the `examples` source so the tracked one is not
    written. The body the stored answers render is the body the document holds, so a
    regenerate moves only the chain it records. Whether the approval then holds is the
    person's record alone: one granted under the chain in force still holds, and one a moved
    chain left stale stays refused, now as a document nobody approved, until a person runs
    `just approve-design` — a regenerate never stands in for that.
    """
    copied = tmp_path / EXAMPLES
    shutil.copytree(REPO_ROOT / EXAMPLES, copied)
    monkeypatch.setenv("ONETASKGRAPH_SOURCES__EXAMPLES__CONFIG__ROOT", str(copied))
    project = f"{EXAMPLES}:health-endpoint"
    document = f"{project}-design"
    _stand_ins(tmp_path, monkeypatch, [project])
    repository = design_chain.plan_repository(project)
    assert repository is not None, "the example this regenerates names its one repository"
    before = design_approval.design_document(project)
    recorded = design_approval.recorded(before)
    assert recorded is not None, "the example carries no approval a person recorded"
    current = design_approval.assess(project).refusal is None

    resolve = subprocess.run(
        [str(REPO_ROOT / ".venv" / "bin" / "onepipeline"), "template", "resolve"]
        + [design_approval.TEMPLATE_NAME, *design_chain.resolve_arguments(repository), "--json"]
        + ["--template-root", str(design_approval.TEMPLATE_ROOT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    render = subprocess.run(
        [str(plan_store.locked_binary()), "document", "render", document]
        + ["--template-loader", "-", "--no-interactive", "--json"],
        cwd=REPO_ROOT,
        input=resolve.stdout,
        capture_output=True,
        text=True,
        check=False,
    )
    assert render.returncode == 0, render.stderr
    answered = json.loads(render.stdout)
    assert answered["changed"] is not current, answered
    assert answered["digest"] == json.loads(resolve.stdout)["digest"]
    after = design_approval.design_document(project)
    assert after.content == before.content, "the stored answers render another body"
    assert design_approval.recorded(after) == recorded, "a regenerate rewrote the approval"
    refusal = design_approval.assess(project).refusal
    if current:
        assert refusal is None, refusal
    else:
        assert refusal is not None and "carries no approval for what it currently says" in (
            refusal
        ), refusal


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]


# llmlint: ignore-block[shell_test_tiers_stay_split] Not a shell suite and not a host tool:
# the engine and the plan-store CLI these spawn are the workspace's own locked installs, in this
# tier's key, the store is each test's own temporary one, and these cover `fits_in_place` and
# the writer repair under the tier's 100% coverage floor, as the resolve tests above do.
def _designed(root: Path, monkeypatch: pytest.MonkeyPatch, answers: Mapping[str, object]) -> str:
    """Render a design document into a real local source under ``root``, and answer its id.

    Rendered through the pinned engine's resolve piped into the pinned store's `document
    create`, the way a writer renders one, from ``answers`` as the store then holds them.
    """
    monkeypatch.setenv("ONETASKGRAPH_SOURCES__SHAPED__PLUGIN", "local-md")
    monkeypatch.setenv("ONETASKGRAPH_SOURCES__SHAPED__CONFIG__ROOT", str(root))
    (root / "projects").mkdir(parents=True)
    (root / "projects" / "demo.md").write_text(
        '---\ntitle: "demo"\nstatus: "todo"\n---\n\nThe plan.\n', encoding="utf-8"
    )
    resolve = subprocess.run(
        [str(REPO_ROOT / ".venv" / "bin" / "onepipeline"), "template", "resolve"]
        + [design_approval.TEMPLATE_NAME, "--json"]
        + ["--template-root", str(design_approval.TEMPLATE_ROOT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    path = root / "answers.json"
    path.write_text(json.dumps(answers), encoding="utf-8")
    created = subprocess.run(
        [str(plan_store.locked_binary()), "document", "create", "shaped", "--project", "demo"]
        + ["--title", "Design: demo", "--id", "demo-design", "--template-loader", "-"]
        + ["--answers", str(path), "--no-interactive"],
        cwd=REPO_ROOT,
        input=resolve.stdout,
        capture_output=True,
        text=True,
        check=False,
    )
    assert created.returncode == 0, created.stderr
    return "shaped:demo"


#: Answers in the shape the template in force declares.
CURRENT: Mapping[str, object] = {
    "what": "A paginated listing.",
    "why": "An operator cannot see past the first screen.",
    "architecture": "One route.",
    "units": [
        {
            "name": "Route",
            "repository": "service",
            "part": "",
            "summary": "The route pages.",
            "reversible": [],
            "decisions": [],
        }
    ],
    "acceptance_criteria": ["The listing pages."],
    "planned_tasks": [
        {
            "task": "feat: page",
            "unit": "Route",
            "delivers": "the route",
            "depends_on": "none",
            "location": "/plans/tasks/page.md",
        }
    ],
}


def _with_retired_answers(root: Path) -> None:
    """Store answers for a variable the template no longer declares beside the current ones.

    What a document rendered under the template this one replaced holds: its stored answers
    are the store's own trailing block of the record, and an answer named for a retired
    variable is the one shape a regenerate in place refuses.
    """
    record = root / "documents" / "demo-design.md"
    text = record.read_text(encoding="utf-8")
    assert "\nwhat: " in text, text
    record.write_text(
        text.replace("\nwhat: ", "\ncontracts:\n- The retired contract.\nwhat: ", 1),
        encoding="utf-8",
    )


def test_a_document_whose_stored_answers_fit_regenerates_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _designed(tmp_path / "store", monkeypatch, CURRENT)
    document = design_approval.design_document(project)
    assert FITS(document) is True


def test_a_document_holding_no_stored_answers_is_left_to_the_answers_supplied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A copy holds no answers, so the store names none, and its regenerate is handed them."""
    root = tmp_path / "store"
    project = _designed(root, monkeypatch, CURRENT)
    record = root / "documents" / "demo-design.md"
    record.write_text(
        record.read_text(encoding="utf-8").split("\n<!-- onetaskgraph:template-answers", 1)[0],
        encoding="utf-8",
    )
    assert FITS(design_approval.design_document(project)) is True


def test_a_document_whose_stored_answers_the_template_no_longer_takes_is_sent_to_the_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regenerating in place would render the retired answers again, so the writer is named."""
    root = tmp_path / "store"
    project = _designed(root, monkeypatch, CURRENT)
    _with_retired_answers(root)
    document = design_approval.design_document(project)
    assert FITS(document) is False
    monkeypatch.setattr(design_approval, "fits_in_place", FITS)
    provenance = document.metadata[design_approval.PROVENANCE]
    assert isinstance(provenance, dict), document.metadata
    stale = dataclasses.replace(
        document,
        metadata=dict(document.metadata)
        | {design_approval.PROVENANCE: {**provenance, "digest": STATED}},
    )
    with pytest.raises(design_approval.Unrendered) as refused:
        design_approval.body_digest(stale, MOVED)
    said = str(refused.value)
    assert "just finish-plan <brief>" in said, said
    assert design_chain.resolve_command(None) in said, said
    assert "document render" not in said, said


# llmlint: ignore-end[shell_test_tiers_stay_split]
