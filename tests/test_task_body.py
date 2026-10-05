"""The issue body a task becomes on the `plans` board, measured before the copy.

`tests/plan_tooling/test_check_plan_recipe_e2e.py` drives the real recipe over a plan
whose task's body crosses the limit, one between the thresholds, and one below, and
`tests/plan_tooling/test_design_document_body_limit_e2e.py` drives `just copy-plan` over a
design document past each threshold. What is here is the composition itself — held to the
plugin's `compose_body` in the registered `onetaskgraph` checkout, for a task and for a
design document — and the faces of the reading the recipes reach it through.

The plugin's source is read at the release `config/onetaskgraph.version` pins, or at the
head of a checkout that has not fetched that tag. Either way it is read from the sibling's
checkout, whose tags and tree move with no change here, so every disagreement and every
read that fails — a file gone, unreadable, not UTF-8 or no longer spelling what is held —
is settled by `tests/sibling_facts.py`: failed for a change editing a registration file,
reported as `SiblingDrift` for any other.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NamedTuple

import pytest
from plan_store_pin import adopted_release
from registered_checkouts import UnreadableSibling, sibling_git
from sibling_facts import (
    ENCLOSING_COMPARISON,
    REGISTRATION_FILES,
    SiblingDrift,
    commit_change,
    published_clone,
    registered_checkouts,
    scratch_checkout,
    settle_drift,
)

from orchestrator import plan_store, task_body
from orchestrator.publication_guard import Refusal
from orchestrator.root import REPO_ROOT

#: The plugin whose `compose_body` this module mirrors, the file that spells the slot, and
#: the plugin-api file that spells the keys its `slot_metadata` adds or drops.
PLUGIN_REPOSITORY = "github.com/nickderobertis/onetaskgraph"
PLUGIN_SOURCE = Path("crates") / "onetaskgraph-github-projects" / "src" / "lib.rs"
PLUGIN_API_SOURCE = Path("crates") / "onetaskgraph-plugin-api" / "src" / "work.rs"


def _record(node: str, content: str | None, **metadata: object) -> plan_store.StoreTask:
    return plan_store.StoreTask(
        qualified_id=plan_store.QualifiedTaskId(f"authoring:probe/{node}"),
        node_id=plan_store.NodeId(node),
        title=f"feat: {node}",
        content=content,
        metadata={"onepipeline.id": node, **metadata},
        repositories=[],
        deps=(),
    )


def _sized(size: int) -> str:
    """Content whose composed body under an empty map measures exactly ``size``."""
    return "x" * size


def test_the_body_is_the_content_two_newlines_and_the_sorted_compact_slot() -> None:
    composed = task_body.compose("## What\n\nProbe.", {"z": 1, "a": {"nested": [1, 2]}})

    assert composed == (
        '## What\n\nProbe.\n\n<!-- onetaskgraph.metadata\n{"a":{"nested":[1,2]},"z":1}\n-->'
    )


def test_an_empty_map_carries_no_slot_and_empty_content_no_leading_blank_lines() -> None:
    """The plugin's two edge cases, mirrored: they change where a body's size comes from."""
    assert task_body.compose("Probe.", {}) == "Probe."
    assert task_body.compose(None, {}) == ""
    assert task_body.compose("", {"k": "v"}) == '<!-- onetaskgraph.metadata\n{"k":"v"}\n-->'
    assert task_body.compose(None, {"k": "v"}) == '<!-- onetaskgraph.metadata\n{"k":"v"}\n-->'


def test_a_map_no_record_could_carry_is_refused_by_name_rather_than_raised_through() -> None:
    """Every map that reaches `compose` was decoded from JSON; one that was not says so."""
    with pytest.raises(task_body.BodyError, match="not one the store could carry"):
        task_body.compose("Probe.", {"when": object()})


def test_the_measure_is_characters_rather_than_bytes() -> None:
    """GitHub's refusal counts characters, and an escaped code point would count as six."""
    assert task_body.measure("é", {}) == 1
    assert task_body.measure("", {"k": "é"}) == len('<!-- onetaskgraph.metadata\n{"k":"é"}\n-->')


def test_every_top_level_task_is_measured_from_its_content_and_its_metadata() -> None:
    """Human nodes become issues too, and a step travels inside its parent's map."""
    plan = {
        "tasks": [
            {"id": "route", "task": "Probe.", "metadata": {"onepipeline.id": "route"}},
            {"id": "approve", "kind": "human", "task": "Approve.", "metadata": {}},
            {
                "id": "lifecycle",
                "metadata": {"onepipeline.steps": [{"id": "one", "task": "Step."}]},
            },
            {"task": "no id, already refused by the loader"},
            {"id": "", "task": "an empty id, likewise"},
            {"id": "odd", "task": 7, "metadata": "not a map"},
        ]
    }

    assert list(task_body.bodies(plan)) == [
        task_body.Body("route", task_body.measure("Probe.", {"onepipeline.id": "route"})),
        task_body.Body("approve", len("Approve.")),
        task_body.Body(
            "lifecycle",
            task_body.measure(None, {"onepipeline.steps": [{"id": "one", "task": "Step."}]}),
        ),
        task_body.Body("odd", 0),
    ]


@pytest.mark.parametrize("plan", ("not a plan", {"name": "no tasks"}, {"tasks": "not a list"}))
def test_a_plan_of_no_readable_shape_measures_nothing(plan: object) -> None:
    assert list(task_body.bodies(plan)) == []
    assert task_body.refusals(plan) == []


def test_a_body_over_the_limit_is_refused_naming_the_size_the_limit_and_what_is_left_out() -> None:
    over = {"tasks": [{"id": "big", "task": _sized(task_body.BODY_LIMIT + 1), "metadata": {}}]}

    (refused,) = task_body.refusals(over)

    assert refused == Refusal(node="big", field="task", reason=refused.reason)
    assert f"{task_body.BODY_LIMIT + 1:,} characters" in refused.reason
    assert f"{task_body.BODY_LIMIT:,}-character limit" in refused.reason
    assert task_body.UNMEASURED in refused.reason
    assert "`just copy-plan`" in refused.reason
    # The raising face walks past the record within the limit and names the one over it,
    # which is the accepted path and the refused one read in a single observable outcome.
    fits = _record("fits", _sized(task_body.BODY_LIMIT - 100))
    with pytest.raises(task_body.BodyError, match=r"^big: task: its composed issue body"):
        task_body.check_records([fits, _record("big", _sized(task_body.BODY_LIMIT + 1))])


def test_a_body_exactly_at_the_limit_is_taken_and_warned_about() -> None:
    """The limit is inclusive: GitHub's message says *maximum is*, not *fewer than*."""
    at = task_body.Body(plan_store.NodeId("edge"), task_body.BODY_LIMIT)

    assert task_body.refusal(at) is None
    warned = task_body.warning(at)
    assert warned is not None
    assert "100%" in warned


@pytest.mark.parametrize(
    ("size", "warned"),
    (
        pytest.param(task_body.WARN_FROM - 1, False, id="just-below"),
        pytest.param(task_body.WARN_FROM, True, id="at-the-threshold"),
        pytest.param(task_body.BODY_LIMIT + 1, False, id="over-the-limit-is-refused-not-warned"),
    ),
)
def test_a_warning_covers_the_threshold_up_to_the_limit_and_nothing_else(
    size: int, warned: bool
) -> None:
    warning = task_body.warning(task_body.Body(plan_store.NodeId("probe"), size))

    assert (warning is not None) is warned
    if warning is not None:
        assert warning.startswith("check-plan: warning: probe: ")
        assert f"{size:,} characters" in warning
        assert f"{100 * size // task_body.BODY_LIMIT}% of the {task_body.BODY_LIMIT:,}" in warning
        assert task_body.UNMEASURED in warning


def test_warnings_are_read_off_the_records_the_wrapper_re_reads() -> None:
    """Both wrapper paths warn over store records, whose map carries the review record."""
    records = [
        _record("small", "Probe."),
        _record("large", _sized(task_body.WARN_FROM), **{"orchestrator.plan-review": {"key": "k"}}),
        _record("huge", _sized(task_body.BODY_LIMIT + 1)),
    ]

    (warned,) = task_body.warnings(records)

    assert warned.startswith("check-plan: warning: large: ")
    assert list(task_body.record_bodies(records))[1].size > task_body.WARN_FROM, (
        "the review record is part of the map the body carries, so it is measured"
    )


def _document(native: str, content: str, **metadata: object) -> plan_store.StoreDocument:
    return plan_store.StoreDocument(
        qualified_id=plan_store.QualifiedDocumentId(f"authoring:{native}"),
        title=f"Design: {native}",
        content=content,
        project="probe",
        labels=[],
        repositories=[],
        metadata=metadata,
        location=None,
    )


def test_a_document_is_measured_as_its_content_and_its_whole_map_the_way_a_task_is() -> None:
    """The template provenance and the approval are in the map, so they are in the body."""
    provenance = {"onetaskgraph.template": {"template": "onepipeline:design-doc"}}
    approval = {"orchestrator.design-approval": {"key": "k"}}
    designed = _document("probe-design", "## What\n\nProbe.\n", **provenance, **approval)

    (body,) = task_body.document_bodies([designed])

    assert body == task_body.DocumentBody(
        plan_store.QualifiedDocumentId("authoring:probe-design"),
        len(task_body.compose("## What\n\nProbe.\n", {**provenance, **approval})),
    )
    assert body.size > len("## What\n\nProbe.\n") + len(task_body.METADATA_OPEN)


def test_a_document_over_the_limit_is_refused_and_one_from_the_threshold_warned_about() -> None:
    """The task thresholds, inclusive at the limit, with the document's own account."""
    over = _document("over", _sized(task_body.BODY_LIMIT + 1))
    at = _document("at", _sized(task_body.BODY_LIMIT))
    warned = _document("warned", _sized(task_body.WARN_FROM))
    below = _document("below", _sized(task_body.WARN_FROM - 1))
    documents = [over, at, warned, below]

    (refused,) = task_body.document_refusals(documents)
    assert refused.startswith("copy-plan: authoring:over: its composed issue body measures ")
    assert f"{task_body.BODY_LIMIT + 1:,} characters" in refused
    assert f"{task_body.BODY_LIMIT:,}-character limit" in refused
    assert task_body.DOCUMENT_UNMEASURED in refused

    warnings = task_body.document_warnings(documents)
    assert [line.split(": ")[2] for line in warnings] == ["authoring:at", "authoring:warned"]
    assert "100% of the" in warnings[0]
    assert all(task_body.DOCUMENT_UNMEASURED in line for line in warnings)
    assert task_body.document_refusals([below]) == []
    assert task_body.document_warnings([below]) == []


class PluginSource(NamedTuple):
    """One file of the plugin's source, and where it was read."""

    text: str
    read_from: str


def plugin_source_at(
    checkout: Path,
    tag: str,
    source: Path,
    *,
    root: Path = REPO_ROOT,
    comparison: Mapping[str, str] = ENCLOSING_COMPARISON,
) -> PluginSource | None:
    """``source`` in ``checkout``: at ``tag`` where it is fetched, at the head otherwise.

    The pin is this repository's, but what its tag holds in a host's checkout is the
    sibling's live state like its head is, so a file gone from either, unreadable or not
    UTF-8 — and a checkout that cannot be read at all — is settled by
    `tests/sibling_facts.py` against the change at ``root`` and answers None, which ends
    the comparison with nothing to compare. Decoded strictly rather than with replacement,
    because a replaced byte can leave every line the comparison looks for intact.
    """

    def settled(finding: str) -> None:
        settle_drift([finding], root=root, comparison=comparison)

    try:
        tagged = sibling_git(checkout, "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}")
        shown = (
            sibling_git(checkout, "show", f"{tag}:{source.as_posix()}")
            if tagged.returncode == 0
            else None
        )
    except UnreadableSibling as error:
        settled(f"{error}, so the plugin's {source.as_posix()} cannot be reconciled")
        return None
    if shown is not None:
        pinned = f"{checkout} at {tag}, the release config/onetaskgraph.version pins,"
        if shown.returncode != 0:
            settled(
                f"{pinned} has no {source.as_posix()} "
                f"({shown.stderr.decode('utf-8', 'replace').strip()}), so "
                "orchestrator/task_body.py's issue-body composition cannot be reconciled with "
                "the plugin's: point the reconciliation at where that release keeps the file"
            )
            return None
        try:
            return PluginSource(shown.stdout.decode("utf-8"), f"{checkout} at {tag}")
        except UnicodeDecodeError as error:
            settled(
                f"{pinned} holds a {source.as_posix()} that is not UTF-8 ({error}), so "
                "orchestrator/task_body.py's issue-body composition cannot be reconciled with "
                "the plugin's"
            )
            return None
    head = checkout / source
    try:
        text = head.read_bytes().decode("utf-8")
    except OSError as error:
        # Gone, renamed, turned into a directory or unreadable: each is the head moving in
        # the sibling's own repository, so each is settled rather than raised.
        settled(
            f"{checkout} has not fetched {tag} and its head's {source.as_posix()} cannot "
            f"be read ({error.strerror or error}), so orchestrator/task_body.py's "
            "issue-body composition cannot be reconciled with the plugin's: fetch "
            f"{tag} there, or find where the plugin moved the file"
        )
        return None
    except UnicodeDecodeError as error:
        settled(
            f"{checkout} has not fetched {tag} and its head's {source.as_posix()} is not "
            f"UTF-8 ({error}), so orchestrator/task_body.py's issue-body composition cannot "
            f"be reconciled with the plugin's: fetch {tag} there"
        )
        return None
    return PluginSource(text, f"{checkout} at its head")


def _plugin_source(source: Path = PLUGIN_SOURCE) -> PluginSource | None:
    """One file of the registered plugin's source; a skip where the checkout is absent."""
    checkout = registered_checkouts().get(PLUGIN_REPOSITORY)
    if checkout is None:
        pytest.skip(
            f"{PLUGIN_REPOSITORY} has no checkout on this host among those "
            f"`config/onevcs.checkouts` lists, so the slot's delimiters cannot be "
            f"reconciled here"
        )
    return plugin_source_at(checkout, f"v{adopted_release()}", source)


#: The lines of the plugin's `compose_body` that decide what `task_body.compose` mirrors
#: beyond the delimiters: an empty map composes no slot, the map is a `BTreeMap` written
#: by `serde_json::to_string` — key-sorted and compact — and content is joined to the
#: slot by the plugin's `METADATA_SEPARATOR`, held below beside the delimiters. Spelled as
#: the source spells them, so a plugin that changed any of the three fails the gate below
#: rather than being measured against the old shape.
COMPOSITION = (
    "fn compose_body(\n    content: Option<&str>,\n    metadata: &BTreeMap<String, Value>,",
    "    if metadata.is_empty() {\n"
    "        return Ok((!visible.is_empty()).then(|| visible.to_owned()));",
    "    let encoded = serde_json::to_string(metadata)",
    '        format!("{METADATA_OPEN}{encoded}{METADATA_CLOSE}")',
    '        format!("{visible}{METADATA_SEPARATOR}{METADATA_OPEN}{encoded}{METADATA_CLOSE}")',
)


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] `reads_checkouts` moves
# this gate out of every memoized tier into the uncached `orchestrator:test-checkouts`,
# because its subject — the plugin's source in the registered `onetaskgraph` checkout —
# is outside this workspace; `tests/test_host_installs.py` tiers its reconciliations the
# same way for the same reason.
@pytest.mark.reads_checkouts
def test_the_slot_delimiters_and_composition_are_the_ones_the_plugin_spells() -> None:
    """A store that changed its encoding fails here rather than measuring the wrong body.

    The plugin restates the delimiters as two `const` strings rather than sharing them,
    and this module restates them a third time; the plugin's own `check` reconciles its
    two, and this reconciles the third against the source of the release this host pins.
    The separator between prose and slot is the plugin's own `const`, held here the same
    way, and the composition around the three is held line by line, because a slot
    placed or encoded differently measures a different body under the same delimiters.
    """
    found = _plugin_source()
    if found is None:
        return
    # llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] Settled rather than
    # asserted: ai-orchestrator#1529 rules that a check whose subject is a sibling's live state
    # refuses only a push changing one of the five registration files and reports the drift
    # otherwise; `tests/sibling_facts.py`'s `settle_drift` decides it, and this module's
    # fixture tests drive both pushes through it.
    settle_drift(composition_findings(found))
    # llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


def composition_findings(found: PluginSource) -> list[str]:
    """Every way ``found`` composes an issue body other than `task_body.compose` mirrors."""
    source, read_from = found
    findings = [
        f"{read_from} no longer composes an issue body the way orchestrator/task_body.py "
        f"mirrors; it lacks:\n{line}"
        for line in COMPOSITION
        if line not in source
    ]
    spelled = {
        found["name"]: found["value"]
        for found in re.finditer(
            r'const (?P<name>METADATA_(?:OPEN|CLOSE|SEPARATOR)): &str = "(?P<value>[^"]*)";',
            source,
        )
    }
    try:
        decoded = {name: value.encode().decode("unicode_escape") for name, value in spelled.items()}
    except UnicodeDecodeError:
        decoded = spelled  # an escape Python cannot read is a spelling that differs below

    if decoded != {
        "METADATA_OPEN": task_body.METADATA_OPEN,
        "METADATA_CLOSE": task_body.METADATA_CLOSE,
        "METADATA_SEPARATOR": task_body.METADATA_SEPARATOR,
    }:
        findings.append(
            f"{read_from} spells the slot as {spelled}, and orchestrator/task_body.py does not"
        )
    return findings


#: The lines of the plugin's `slot_metadata` that add or drop the keys
#: `task_body.UNMEASURED_KEYS` names, and where each key's spelling lives: the origin key
#: is the plugin's own constant, the other three are the plugin-api's.
EXCLUSIONS = (
    (PLUGIN_SOURCE, 'const ORIGIN_KEY: &str = "onetaskgraph.origin";'),
    (PLUGIN_SOURCE, "    metadata.remove(ORIGIN_KEY);"),
    (PLUGIN_SOURCE, "            ItemKind::METADATA_KEY.to_owned(),"),
    (PLUGIN_SOURCE, "        metadata.remove(Repository::METADATA_KEY);"),
    (PLUGIN_SOURCE, "        metadata.remove(DependencyEdge::RECORDED_KEY);"),
    (PLUGIN_API_SOURCE, 'pub const METADATA_KEY: &\'static str = "onetaskgraph.repositories";'),
    (PLUGIN_API_SOURCE, 'pub const RECORDED_KEY: &\'static str = "onetaskgraph.depends_on";'),
    (PLUGIN_API_SOURCE, 'pub const METADATA_KEY: &\'static str = "onetaskgraph.item_kind";'),
)


@pytest.mark.reads_checkouts
def test_the_keys_the_measurement_leaves_out_are_the_ones_the_plugin_adds_or_drops() -> None:
    """What a refusal says it leaves out is what the plugin's `slot_metadata` really moves.

    Each key is held to the source that spells it and to the line that adds or removes it
    around the copy, so a plugin that stopped routing the origin elsewhere, or started
    writing dependency edges into the slot, fails here rather than leaving every refusal
    describing a figure it no longer is.
    """
    read: dict[Path, PluginSource] = {}
    for source in dict.fromkeys(source for source, _ in EXCLUSIONS):
        found = _plugin_source(source)
        if found is None:
            return
        read[source] = found
    # llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] Settled rather than
    # asserted: ai-orchestrator#1529 rules that a check whose subject is a sibling's live state
    # refuses only a push changing one of the five registration files and reports the drift
    # otherwise; `tests/sibling_facts.py`'s `settle_drift` decides it, and this module's
    # fixture tests drive both pushes through it.
    settle_drift(
        [
            f"{read[source].read_from} no longer spells what orchestrator/task_body.py "
            f"says the measurement leaves out; it lacks:\n{line}"
            for source, line in EXCLUSIONS
            if line not in read[source].text
        ]
    )
    # llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]
    spelled = {key for _, line in EXCLUSIONS for key in task_body.UNMEASURED_KEYS if key in line}
    assert spelled == set(task_body.UNMEASURED_KEYS), (
        f"UNMEASURED_KEYS names {set(task_body.UNMEASURED_KEYS) - spelled}, which no held "
        f"line of the plugin spells"
    )


#: The lines of the plugin that put a design document through the task's composition: its
#: `write_document` hands `write_item` the document's own content and metadata map, and
#: `write_item` — the one write of all three kinds — composes the body from them with
#: `compose_body` after `slot_metadata`, which for a document drops the item-kind marker
#: rather than adding it. A plugin that composed a document's body any other way fails
#: here rather than leaving `just copy-plan` measuring a body the board never sees.
DOCUMENT_COMPOSITION = (
    "    async fn write_document(&self, write: &ItemWrite<Document>) "
    "-> Result<NativeId, SourceError> {",
    "            &Incoming {\n"
    "                written: Written::Document,\n"
    "                title: &write.item.title,\n"
    "                content: write.item.content.as_deref(),\n"
    "                labels: &write.item.labels,\n"
    "                metadata: &write.item.metadata,",
    "        let slot = slot_metadata(incoming, own_repository.as_ref(), &fallback);\n"
    "        let body = compose_body(incoming.content, &slot)?;",
    "        BoardKind::Document => metadata.remove(ItemKind::METADATA_KEY),",
)


@pytest.mark.reads_checkouts
def test_a_document_s_body_is_composed_by_the_plugin_exactly_as_a_task_s_is() -> None:
    """What `task_body.document_bodies` measures is what the board's document write sends.

    Held line by line, beside the composition the task gate above holds: a document that
    reached the board some other way — its own composer, a slot of its own, content
    re-rendered on the way — would be measured here against a body it never becomes.
    """
    found = _plugin_source()
    if found is None:
        return
    # llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] Settled rather than
    # asserted: ai-orchestrator#1529 rules that a check whose subject is a sibling's live state
    # refuses only a push changing one of the five registration files and reports the drift
    # otherwise; `tests/sibling_facts.py`'s `settle_drift` decides it, and this module's
    # fixture tests drive both pushes through it.
    settle_drift(
        [
            f"{found.read_from} no longer writes a design document's body the way "
            f"orchestrator/task_body.py measures it; it lacks:\n{line}"
            for line in DOCUMENT_COMPOSITION
            if line not in found.text
        ]
    )
    # llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]


#: The tag a fixture checkout of the plugin is read at, as the pin names a release.
FIXTURE_TAG = "v0.0.1"


def _plugin_fixture(tmp_path: Path, files: Mapping[str, str | bytes]) -> Path:
    """A real checkout of the plugin's repository whose tree carries ``files`` alone."""
    return scratch_checkout(tmp_path / "onetaskgraph", PLUGIN_REPOSITORY, files)


def test_a_plugin_file_gone_from_an_untagged_head_fails_a_registration_change(
    tmp_path: Path,
) -> None:
    checkout = _plugin_fixture(tmp_path, {"README.md": "moved\n"})
    clone = published_clone(tmp_path / "gated")
    commit_change(clone, "config/onevcs.checkouts")

    with pytest.raises(pytest.fail.Exception) as failed:
        plugin_source_at(checkout, FIXTURE_TAG, PLUGIN_SOURCE, root=clone, comparison={})

    assert "this change edits config/onevcs.checkouts" in str(failed.value)
    assert f"has not fetched {FIXTURE_TAG}" in str(failed.value)
    assert PLUGIN_SOURCE.as_posix() in str(failed.value)
    assert str(checkout) in str(failed.value)


def test_a_plugin_file_gone_from_an_untagged_head_is_reported_for_a_persona_only_change(
    tmp_path: Path,
) -> None:
    checkout = _plugin_fixture(tmp_path, {"README.md": "moved\n"})
    clone = published_clone(tmp_path / "gated", ("personas/crozier/crozier-corpus.yaml",))
    commit_change(clone, "personas/crozier/crozier-corpus.yaml")

    with pytest.warns(SiblingDrift) as reported:
        found = plugin_source_at(checkout, FIXTURE_TAG, PLUGIN_SOURCE, root=clone, comparison={})

    assert found is None
    assert PLUGIN_SOURCE.as_posix() in str(reported[0].message)


def test_a_plugin_file_present_at_an_untagged_head_is_read_as_live(tmp_path: Path) -> None:
    checkout = _plugin_fixture(tmp_path, {PLUGIN_SOURCE.as_posix(): "fn compose_body() {}\n"})

    found = plugin_source_at(checkout, FIXTURE_TAG, PLUGIN_SOURCE)

    assert found == PluginSource("fn compose_body() {}\n", f"{checkout} at its head")


#: The two changes every reading of the plugin's checkout is settled for below: one editing
#: a registration file, which fails, and one editing only a persona, which is reported.
PERSONA = "personas/crozier/crozier-corpus.yaml"
PUSHES = ("config/onevcs.checkouts", PERSONA)


def _pinned_fixture(tmp_path: Path, files: Mapping[str, str | bytes]) -> Path:
    """A plugin checkout whose fetched :data:`FIXTURE_TAG` carries ``files`` alone."""
    checkout = _plugin_fixture(tmp_path, files)
    subprocess.run(["git", "-C", str(checkout), "tag", FIXTURE_TAG], check=True)
    return checkout


def _settled(tmp_path: Path, changed: str, said: str, read: Callable[..., object]) -> object:
    """``read(root=, comparison=)`` against a change editing ``changed``, settled as it should be.

    Answers what ``read`` returned where the change edits no registration file and the
    finding was reported; None where it failed.
    """
    clone = published_clone(tmp_path / "gated", (PERSONA,))
    commit_change(clone, changed)
    if changed in REGISTRATION_FILES:
        with pytest.raises(pytest.fail.Exception, match=f"this change edits {changed}") as failed:
            read(root=clone, comparison={})
        assert said in str(failed.value)
        return None
    with pytest.warns(SiblingDrift) as reported:
        answered = read(root=clone, comparison={})
    assert said in str(reported[0].message)
    return answered


@pytest.mark.parametrize("changed", PUSHES)
def test_a_pinned_release_lacking_the_file_is_settled_by_what_the_change_touches(
    tmp_path: Path, changed: str
) -> None:
    """What the pinned tag holds in this host's checkout is the sibling's, like its head."""
    checkout = _pinned_fixture(tmp_path, {"README.md": "moved\n"})

    found = _settled(
        tmp_path,
        changed,
        f"at {FIXTURE_TAG}, the release config/onetaskgraph.version pins, has no "
        f"{PLUGIN_SOURCE.as_posix()}",
        lambda **settle: plugin_source_at(checkout, FIXTURE_TAG, PLUGIN_SOURCE, **settle),
    )

    assert found is None


@pytest.mark.parametrize("changed", PUSHES)
def test_a_pinned_release_composing_otherwise_is_settled_by_what_the_change_touches(
    tmp_path: Path, changed: str
) -> None:
    """A release at the pin that stopped spelling the composition is a finding, not a raise."""
    checkout = _pinned_fixture(tmp_path, {PLUGIN_SOURCE.as_posix(): "fn compose_body() {}\n"})
    found = plugin_source_at(checkout, FIXTURE_TAG, PLUGIN_SOURCE)
    assert found == PluginSource("fn compose_body() {}\n", f"{checkout} at {FIXTURE_TAG}")
    findings = composition_findings(found)

    _settled(
        tmp_path,
        changed,
        f"{checkout} at {FIXTURE_TAG} no longer composes an issue body",
        lambda **settle: settle_drift(findings, **settle),
    )


#: A plugin source whose head is not UTF-8, though every line `COMPOSITION` holds it to is
#: intact: what replacing the bad bytes would read as a source nothing is wrong with.
NOT_UTF_8 = "\n".join(COMPOSITION).encode() + b"\n// \xff\xfe\n"


@pytest.mark.parametrize(
    ("changed", "settles"),
    [("config/onevcs.checkouts", "fails"), ("personas/crozier/crozier-corpus.yaml", "reports")],
)
def test_a_plugin_head_that_is_not_utf_8_is_settled_though_every_line_is_intact(
    tmp_path: Path, changed: str, settles: str
) -> None:
    checkout = _plugin_fixture(tmp_path, {PLUGIN_SOURCE.as_posix(): NOT_UTF_8})
    clone = published_clone(tmp_path / "gated", ("personas/crozier/crozier-corpus.yaml",))
    commit_change(clone, changed)

    if settles == "fails":
        with pytest.raises(pytest.fail.Exception, match=f"this change edits {changed}") as failed:
            plugin_source_at(checkout, FIXTURE_TAG, PLUGIN_SOURCE, root=clone, comparison={})
        assert f"{PLUGIN_SOURCE.as_posix()} is not UTF-8" in str(failed.value)
    else:
        with pytest.warns(SiblingDrift, match=f"{PLUGIN_SOURCE.as_posix()} is not UTF-8"):
            found = plugin_source_at(
                checkout, FIXTURE_TAG, PLUGIN_SOURCE, root=clone, comparison={}
            )
        assert found is None


@pytest.mark.parametrize("changed", PUSHES)
def test_a_pinned_release_whose_file_is_not_utf_8_is_settled_by_what_the_change_touches(
    tmp_path: Path, changed: str
) -> None:
    checkout = _pinned_fixture(tmp_path, {PLUGIN_SOURCE.as_posix(): NOT_UTF_8})

    found = _settled(
        tmp_path,
        changed,
        f"holds a {PLUGIN_SOURCE.as_posix()} that is not UTF-8",
        lambda **settle: plugin_source_at(checkout, FIXTURE_TAG, PLUGIN_SOURCE, **settle),
    )

    assert found is None


@pytest.mark.parametrize(
    ("changed", "settles"),
    [("config/onevcs.checkouts", "fails"), ("personas/crozier/crozier-corpus.yaml", "reports")],
)
def test_a_plugin_head_that_cannot_be_read_is_settled_rather_than_raised(
    tmp_path: Path, changed: str, settles: str
) -> None:
    """A read failing with an OS error is the sibling's live state, settled like any drift."""
    checkout = _plugin_fixture(tmp_path, {PLUGIN_SOURCE.as_posix(): "fn compose_body() {}\n"})
    (checkout / PLUGIN_SOURCE).chmod(0)
    clone = published_clone(tmp_path / "gated", ("personas/crozier/crozier-corpus.yaml",))
    commit_change(clone, changed)

    if settles == "fails":
        with pytest.raises(pytest.fail.Exception, match=f"this change edits {changed}") as failed:
            plugin_source_at(checkout, FIXTURE_TAG, PLUGIN_SOURCE, root=clone, comparison={})
        assert "Permission denied" in str(failed.value)
    else:
        with pytest.warns(SiblingDrift, match="Permission denied"):
            found = plugin_source_at(
                checkout, FIXTURE_TAG, PLUGIN_SOURCE, root=clone, comparison={}
            )
        assert found is None
