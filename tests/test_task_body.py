"""The issue body a task becomes on the `plans` board, measured before the copy.

`tests/plan_tooling/test_check_plan_recipe_e2e.py` drives the real recipe over a plan
whose task's body crosses the limit, one between the thresholds, and one below. What is
here is the composition itself — held to the plugin's `compose_body` in the registered
`onetaskgraph` checkout — and the two faces of the reading the recipe reaches it through.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from plan_store_pin import adopted_release
from registered_checkouts import registered_checkouts

from orchestrator import plan_store, task_body
from orchestrator.publication_guard import Refusal

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


def _plugin_source(source: Path = PLUGIN_SOURCE) -> tuple[str, str]:
    """One file of the plugin's source and where it was read; a skip where the checkout is absent.

    At the tag `config/onetaskgraph.version` pins where the checkout has fetched it, and
    at the checkout's own head otherwise — a checkout that has not fetched the tag is
    still evidence about the encoding, and the second value says which was read.
    """
    checkout = registered_checkouts().get(PLUGIN_REPOSITORY)
    if checkout is None:
        pytest.skip(
            f"{PLUGIN_REPOSITORY} has no checkout on this host among those "
            f"`config/onevcs.checkouts` lists, so the slot's delimiters cannot be "
            f"reconciled here"
        )
    tag = f"v{adopted_release()}"
    shown = subprocess.run(
        ["git", "-C", str(checkout), "show", f"{tag}:{source.as_posix()}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if shown.returncode == 0:
        return shown.stdout, f"{checkout} at {tag}"
    return (checkout / source).read_text(encoding="utf-8"), f"{checkout} at its head"


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
    source, read_from = _plugin_source()
    for line in COMPOSITION:
        assert line in source, (
            f"{read_from} no longer composes an issue body the way orchestrator/task_body.py "
            f"mirrors; it lacks:\n{line}"
        )
    spelled = {
        found["name"]: found["value"]
        for found in re.finditer(
            r'const (?P<name>METADATA_(?:OPEN|CLOSE|SEPARATOR)): &str = "(?P<value>[^"]*)";',
            source,
        )
    }
    decoded = {name: value.encode().decode("unicode_escape") for name, value in spelled.items()}

    assert decoded == {
        "METADATA_OPEN": task_body.METADATA_OPEN,
        "METADATA_CLOSE": task_body.METADATA_CLOSE,
        "METADATA_SEPARATOR": task_body.METADATA_SEPARATOR,
    }, f"{read_from} spells the slot as {spelled}, and orchestrator/task_body.py does not"


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
    read: dict[Path, tuple[str, str]] = {}
    for source, line in EXCLUSIONS:
        if source not in read:
            read[source] = _plugin_source(source)
        text, read_from = read[source]
        assert line in text, (
            f"{read_from} no longer spells what orchestrator/task_body.py says the "
            f"measurement leaves out; it lacks:\n{line}"
        )
    spelled = {key for _, line in EXCLUSIONS for key in task_body.UNMEASURED_KEYS if key in line}
    assert spelled == set(task_body.UNMEASURED_KEYS), (
        f"UNMEASURED_KEYS names {set(task_body.UNMEASURED_KEYS) - spelled}, which no held "
        f"line of the plugin spells"
    )


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
