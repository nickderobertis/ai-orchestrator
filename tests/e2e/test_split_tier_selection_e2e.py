"""E2E proof that the plan-tooling and drift-gate projects are charged only for what they read.

`plan-tooling` and the root drift gates once sat behind keys so broad that every push re-ran
them: a typo in any document re-ran every drift gate, and a one-line script edit re-ran every
plan journey. Each now lives in a directory-owned project keyed on what its tests read —
`tests/nx_inputs.py`'s `PLAN_TOOLING_PROJECTS` and `DRIFT_GATE_PROJECTS` — with a
whole-workspace `test-docs` target kept only for the journeys that copy the checkout or scan
every tracked document. The offline listing over those keys says which tiers an edit should
reach; this asks real Nx the same question, through the selection `just check` narrows with.

One copy of this checkout serves every edit: the module fixture warms the cache by running
each new tier once, and each journey then makes one representative edit — the spike's
`narrow-script`, `narrow-persona` and `narrow-doc`, `AGENTS.md`, and a document only one
project's narrow key reads — asks `scripts/nx-selection.sh`'s narrowing which projects it
selects, runs every new tier again, and puts the edit back. A tier whose key covers the edit
must be selected and must run; every other one must replay. The expected sets are written
out and reconciled against the declared keys first, so what Nx is asked is the narrowing the
keys claim rather than whatever they happen to say.

`PYTEST_ADDOPTS` shortens what each target runs to collection only, as in
`tests/e2e/test_nx_cache_scope_e2e.py`: the claim is about the key, which Nx computes from
the declared inputs before the command runs and which ambient environment is not part of.

llmlint: ignore-file[e2e_not_mocked] Nothing is faked. The one substitution shortens what a
real target runs without touching the declared inputs this journey is about.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split] The
journey copies the tracked tree, which reads all of it, so no key narrower than the whole
workspace describes it; it sits in the whole-workspace tier beside the journeys whose helpers
it reuses.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] `reads_docs` here marks a
journey that copies the checkout, which is the one thing that marker routes to a
whole-workspace key.

llmlint: ignore-file[tests_mirror_real_usage] The user-facing surface over this is `just
check`, which cannot be driven from inside the suite it runs; the selection it narrows with is
taken from the real `scripts/nx-selection.sh`, as `tests/e2e/test_nx_cache_scope_e2e.py`'s
selection journeys take it.
"""

from __future__ import annotations

from typing import NamedTuple

import pytest
from nx_inputs import (
    DRIFT_GATE_PROJECTS,
    PLAN_TOOLING_PROJECTS,
    covers,
    project_declarations,
    project_root,
    target_input_globs,
)
from nx_workspace import TOOLCHAIN_WRITER_MARKS, WORKSPACE_INSTALL_MARKS
from test_nx_cache_scope_e2e import Selector, copied_checkout, published

# Copying the whole tree is this journey's premise, so it belongs to the whole-workspace tier
# by construction; a writer because `scripts/nx.sh` heals the install of the copy it runs in.
# The group names this module alone, so the one copy its fixture makes is made once rather
# than once per worker that runs one of its tests.
pytestmark = [
    *WORKSPACE_INSTALL_MARKS,
    *TOOLCHAIN_WRITER_MARKS,
    pytest.mark.reads_docs,
    pytest.mark.xdist_group("split-tier-selection"),
]

#: The projects this journey is about: every plan-tooling subject and every drift gate.
NEW_PROJECTS = frozenset(
    subject.project for subject in (*PLAN_TOOLING_PROJECTS, *DRIFT_GATE_PROJECTS)
)


def _new_tiers() -> frozenset[str]:
    """Every test target those projects declare, read from their own `project.json`."""
    declarations = project_declarations()
    return frozenset(
        f"{subject.project}:{target}"
        for subject in (*PLAN_TOOLING_PROJECTS, *DRIFT_GATE_PROJECTS)
        for target in declarations[subject.root]["targets"]
        if target.startswith("test") and target != "test-support"
    )


NEW_TIERS = _new_tiers()
#: The whole-workspace tiers: the journeys that copy this checkout or scan every tracked
#: document, which every edit reaches by construction.
WHOLE_WORKSPACE_TIERS = frozenset(
    {
        "plan-tooling-check-and-review:test-docs",
        "plan-tooling-follow-ups:test-docs",
        "plan-tooling-plan-flow:test-docs",
        "manager-guidance:test-docs",
        "tree-scans:test-docs",
    }
)


class Edit(NamedTuple):
    """One representative edit, and the narrow tiers besides the whole-workspace ones it
    reaches: the projects whose tests read that file."""

    name: str
    path: str
    reaches: frozenset[str]


EDITS = (
    # The spike's `narrow-script`: the follow-up journeys run it, the plan check's journeys'
    # traced read set holds it, and two drift gates read every script.
    Edit(
        "narrow-script",
        "scripts/budget-dispatches.sh",
        frozenset(
            {
                "plan-tooling-follow-ups:test",
                "plan-tooling-check-and-review:test",
                "family-scans:test",
                "plan-guidance:test",
            }
        ),
    ),
    # The spike's `narrow-persona`: read by the gates over every persona and by the plan
    # check's journeys, which validate personas.
    Edit(
        "narrow-persona",
        "personas/researcher.yaml",
        frozenset({"plan-tooling-check-and-review:test", "family-scans:test", "release-pins:test"}),
    ),
    # The spike's `narrow-doc`: only the drift gates that read the budgets page.
    Edit(
        "narrow-doc",
        "docs/budgets.md",
        frozenset({"family-scans:test", "plan-guidance:test"}),
    ),
    # The manager's document, which several drift gates hold to the code.
    Edit(
        "agents-md",
        "AGENTS.md",
        frozenset(
            {
                "dispatch-bar:test",
                "family-scans:test",
                "manager-guidance:test",
                "plan-guidance:test",
                "release-pins:test",
            }
        ),
    ),
    # A document only one project's narrow key reads.
    Edit("one-reader", "docs/telemetry-model.md", frozenset({"family-scans:test"})),
)


@pytest.fixture(scope="module")
def warmed(tmp_path_factory: pytest.TempPathFactory) -> Selector:
    """One published copy of this checkout, with every new tier's verdict recorded for it."""
    selector = published(copied_checkout(tmp_path_factory.mktemp("split-tier-selection")))
    for tier in sorted(NEW_TIERS):
        assert selector.ran_the_command(tier), f"{tier} replayed in a cache nothing had filled"
    return selector


def test_the_expected_reach_is_what_the_declared_keys_say() -> None:
    """The literal sets below are the narrowing claimed, held to the keys that claim it."""
    assert WHOLE_WORKSPACE_TIERS <= NEW_TIERS, sorted(WHOLE_WORKSPACE_TIERS - NEW_TIERS)
    for edit in EDITS:
        covering = {
            tier
            for tier in NEW_TIERS
            if covers(
                target_input_globs(project_root(tier.split(":")[0]), tier.split(":")[1]), edit.path
            )
        }
        assert covering == WHOLE_WORKSPACE_TIERS | edit.reaches, (
            f"{edit.name} ({edit.path}) is covered by {sorted(covering)}; this journey "
            f"expects the whole-workspace tiers and {sorted(edit.reaches)}"
        )


@pytest.mark.parametrize("edit", EDITS, ids=[edit.name for edit in EDITS])
def test_an_edit_selects_and_re_runs_exactly_the_new_tiers_that_read_it(
    warmed: Selector, edit: Edit
) -> None:
    """Nx selects every new project whose key covers the edit and runs those tiers as a
    cache miss, and replays every new tier the edit does not reach."""
    expected = WHOLE_WORKSPACE_TIERS | edit.reaches
    with warmed.planted(edit.path) as reported_by_git:
        assert reported_by_git, f"git does not report the edit to {edit.path}"
        selected = warmed.selected() & NEW_PROJECTS
        assert selected == {tier.split(":")[0] for tier in expected}, (
            f"a diff of {edit.path} alone selected {sorted(selected)} of the new projects; "
            f"the ones whose keys cover it own {sorted(expected)}"
        )
        re_ran = {tier for tier in NEW_TIERS if warmed.ran_the_command(tier)}
        assert re_ran == expected, (
            f"an edit to {edit.path} re-ran {sorted(re_ran)}; the tiers whose keys cover it "
            f"are {sorted(expected)}, and every other one should have replayed"
        )
