"""The half of `tests/test_cli_surface_drift.py` that scans the whole tree.

It sits in the `tree-scans` project.

These read every document this repository writes for its readers, wherever it sits, so what
they read is every file git tracks and they carry `reads_docs`, which routes them to that
project's whole-workspace `test-docs` target; the tests of that module that read none of it
stay there, in `orchestrator:test`.
"""

from __future__ import annotations

import pytest
from test_cli_surface_drift import ROUND_ERA, Drift, _drift_in, _prose_files, _recipes, _spans

from orchestrator.root import REPO_ROOT


@pytest.mark.reads_docs
def test_no_documented_command_names_a_flag_or_verb_the_pinned_cli_lacks() -> None:
    """Every onevcs and onepipeline command this repository teaches actually exists.

    One test over every document rather than one per file: the prose is read here
    rather than at collection, and a parametrization computed from `docs/` would be
    built in a tier that does not hash it. The whole list is reported at once, because
    an operator correcting drift wants every site, not the first.
    """
    recipes = _recipes()
    drifted: list[Drift] = []
    for document in _prose_files():
        where = str(document.relative_to(REPO_ROOT))
        for span in _spans(document):
            drifted.extend(_drift_in(span, where, recipes))

    assert not drifted, "documented commands the pinned CLIs do not have:\n" + "\n".join(
        f"  - {drift}" for drift in drifted
    )


@pytest.mark.reads_docs
def test_no_round_era_command_survives_in_the_prose() -> None:
    """The retired round vocabulary is gone by name, not merely unreachable.

    The engine reconciles continuously now: there is no verb that advances a run and
    no budget of rounds to spend. The gate above cannot see a retired verb written
    with no flags and no runner prefix — it declines to guess whether such a span is
    an invocation at all — so the names themselves are refused here.
    """
    named: list[str] = []
    for document in _prose_files():
        text = document.read_text(encoding="utf-8")
        where = str(document.relative_to(REPO_ROOT))
        for retired, instead in ROUND_ERA.items():
            if retired in text:
                named.append(f"  - {where} names `{retired}`; {instead}")

    assert not named, "retired round-era vocabulary survives in the prose:\n" + "\n".join(named)
