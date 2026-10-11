"""The half of `tests/test_tracked_path_references.py` that scans the whole tree.

It sits in the `tree-scans` project.

These read every document the gate scans and list every tracked path, so what they read is
every file git tracks and they carry `reads_docs`, which routes them to that project's
whole-workspace `test-docs` target; the tests of that module that read none of it stay
there, in `orchestrator:test`.
"""

from __future__ import annotations

import pytest
from test_tracked_path_references import (
    DANGLING_TREE,
    EXEMPTED_PATHS,
    EXEMPTIONS,
    LEGACY_EXAMPLE,
    Exemption,
    _exists,
    _ignored,
    dangling_references,
    scanned_documents,
)

from orchestrator.root import REPO_ROOT


@pytest.mark.reads_docs
def test_every_checkable_path_the_scanned_documents_name_exists_in_this_checkout() -> None:
    """The gate itself, over the real documents and the real refusal strings.

    "Checkable" is the whole claim: what a candidate is, and the five shapes that are
    not one, are the module docstring's — a path this detector never looks at is not a
    path it says anything about.
    """
    dangling = [
        reference
        for document in scanned_documents()
        for reference in dangling_references(
            (REPO_ROOT / document).read_text(encoding="utf-8"), document
        )
        if reference.path not in EXEMPTED_PATHS
    ]
    assert not dangling, "\n".join(
        f"{reference.site} names {reference.path}, which does not exist" for reference in dangling
    )


@pytest.mark.reads_docs
def test_the_scan_covers_the_repaired_documents_and_skips_the_agents_symlink() -> None:
    """The scope is what makes the gate mean anything, so it is asserted rather than assumed."""
    scanned = set(scanned_documents())
    assert {
        "README.md",
        "AGENTS.md",
        "docs/orchestration.md",
        "orchestrator/criteria_guard.py",
    } <= scanned
    assert "CLAUDE.md" not in scanned, "the AGENTS.md symlink would double every finding"


@pytest.mark.reads_docs
@pytest.mark.parametrize(("site", "text"), DANGLING_TREE)
def test_the_gate_fails_against_the_tree_that_carried_the_stale_example(
    site: str, text: str
) -> None:
    """Each of the six, put back verbatim, is reported by this checkout's own detector."""
    dangling = dangling_references(text, site)

    assert [reference.path for reference in dangling] == [LEGACY_EXAMPLE], (
        f"{site} carrying its pre-repair text was not reported"
    )


@pytest.mark.reads_docs
@pytest.mark.parametrize("exemption", EXEMPTIONS, ids=lambda item: item.path)
def test_an_exemption_that_has_become_a_real_path_is_refused(exemption: Exemption) -> None:
    """An exemption is a claim that a path is absent on purpose, so it is held to it."""
    assert not _ignored(exemption.path), (
        f"{exemption.path} is gitignored, so its presence is host state rather than a "
        "claim about this repository; drop the entry and let the ignore filter answer"
    )
    assert not _exists(exemption.path), (
        f"{exemption.path} now exists, so its exemption — {exemption.reason} — is stale; "
        "delete the entry rather than leaving a line nobody reads again"
    )
