"""The tracked catalog's `just` recipes, against the repositories those personas review.

`tests/persona_recipes.py` states the failure this reconciles away. What is checked
here is the tracked prose: for each persona that names a repository, `just --dump` in
that repository's registered checkout decides whether every recipe the file names
exists. `tests/e2e/test_persona_review_bar_e2e.py` is the other half — it asks the same
question of the bar a real supervisor was actually handed.

This is why the reconciliation belongs to the uncached tier. Its input is a registered
checkout outside this workspace, which no `nx.json` key covers, so a memoized green
would be a verdict on whatever that repository's recipes looked like when it was
recorded — and a rename is precisely what it exists to catch.

What it finds is that repository's live state, so it is settled by
`tests/sibling_facts.py`: a change editing a registration file fails for it, and any
other change — a persona's own edit included — has it reported as `SiblingDrift`.
`tests/test_sibling_drift.py` drives a real report through both.
"""

from __future__ import annotations

import pytest
from persona_recipes import (
    PERSONA_ROOT,
    RepoPersona,
    checkout_of,
    repo_specific_personas,
    undefined_recipes,
)
from sibling_facts import settle_drift


def test_the_catalog_still_holds_a_repo_specific_persona_that_names_a_recipe() -> None:
    """A reconciliation that resolved nothing would pass silently forever.

    The check below is parametrized over what the catalog holds, so an empty catalog —
    or a persona layout that stopped matching — would collect no cases and report the
    same green as a catalog that reconciled cleanly.
    """
    personas = repo_specific_personas()
    assert personas, (
        f"no repo-specific persona was found under {PERSONA_ROOT}; if the catalog's "
        "layout changed, repo_specific_personas must change with it or the "
        "reconciliation below silently covers nothing"
    )
    assert any(persona.recipes for persona in personas), (
        "no repo-specific persona names a `just` recipe any more, so the "
        "reconciliation below has nothing to reconcile"
    )


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker, contracts_have_one_source_or_a_drift_gate] `reads_checkouts`  # noqa: E501
# is this repository's uncached tier: `orchestrator:test-checkouts` runs exactly `-m
# reads_checkouts` and `just check` includes it. A project of its own, so `nx affected` can skip it,
# is the opposite of what this needs, because its subject is a sibling's checkout outside the
# workspace and a memo keyed on this workspace would replay what that checkout said when it was
# recorded. Settled rather than asserted because ai-orchestrator#1529 rules, and this check's task
# states as a criterion, that a check whose subject is a sibling's live state refuses only a push
# changing one of the five registration files and reports the drift otherwise;
# `tests/sibling_facts.py`'s `settle_drift` is the one place that is decided, and
# `tests/test_sibling_drift.py` drives both pushes through it.
@pytest.mark.reads_checkouts
@pytest.mark.parametrize("persona", repo_specific_personas(), ids=lambda persona: persona.named)
def test_a_repo_specific_persona_only_names_recipes_its_repository_defines(
    persona: RepoPersona,
) -> None:
    """The drift gate: a persona's prose, against the justfile that owns the fact.

    Settled rather than asserted, for the reason the module docstring gives.
    """
    checkout = checkout_of(persona.repository)
    if checkout is None:
        pytest.skip(
            f"this host holds no registered checkout of {persona.repository}, so "
            f"{persona.named} cannot be reconciled against it here"
        )

    report = undefined_recipes(persona, checkout)
    settle_drift([report] if report is not None else [])


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker, contracts_have_one_source_or_a_drift_gate]  # noqa: E501
