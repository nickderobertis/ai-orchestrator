"""Drift gate for human-readable references to the adopted onejudge version."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from orchestrator import REPO_ROOT

# DRIFT-GATE: config/onejudge.version is the single source of truth. Keep this
# explicit list aligned with unavoidable human-readable version literals, such
# as links to versioned external documentation.
ONEJUDGE_VERSION_REFERENCE_COUNTS = {
    Path("docs/onejudge-integration.md"): 2,
}
ONEJUDGE_VERSION_REFERENCE = re.compile(
    r"(?:\bonejudge(?:-cli| SDK/CLI)?(?:'s)?(?: version)?[\s`*(=]+|/onejudge/(?:blob/)?)"
    r"v?(?P<version>\d+\.\d+\.\d+)",
    re.IGNORECASE,
)


@pytest.mark.reads_docs
@pytest.mark.parametrize(
    ("relative_path", "expected_count"), ONEJUDGE_VERSION_REFERENCE_COUNTS.items()
)
def test_onejudge_version_references_match_single_source(
    relative_path: Path, expected_count: int, adopted_onejudge_version: str
) -> None:
    """Reject stale onejudge literals in every file covered by this drift gate."""
    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    matches = list(ONEJUDGE_VERSION_REFERENCE.finditer(text))
    referenced_versions = {match.group("version") for match in matches}

    assert len(matches) == expected_count, (
        f"drift gate parsed {len(matches)} of {expected_count} intended onejudge version "
        f"references in {relative_path}"
    )
    assert referenced_versions == {adopted_onejudge_version}, (
        f"{relative_path} references onejudge versions {sorted(referenced_versions)}; "
        f"expected only config/onejudge.version ({adopted_onejudge_version})"
    )


#: Every place the model-precedence measurement is restated, and the sentence each
#: must spell for the adopted release. Three sites rather than one, because the
#: measurement is the *reason* the code has the shape it does — the wrapper names
#: each side's model on its own `oneharness run` AND exports the variable — so the
#: module documenting that seam and the wrapper implementing it each say why. A gate
#: over the reference document alone leaves both of those asserting a precedence
#: measured against the previous release, with the copy an operator is least likely
#: to be reading when they change the code as the only thing that fails.
MODEL_PRECEDENCE_CLAIMS = {
    "docs/onejudge-integration.md": (
        "Measured against the adopted oneharness {version}, a config's "
        "per-harness `model` **beats** the variable"
    ),
    "orchestrator/harnesses.py": (
        "measured against oneharness {version}, a config's per-harness ``model`` beats it"
    ),
    "scripts/oneharness-agent.sh": "oneharness {version} lets that config value beat",
}


@pytest.mark.reads_docs
@pytest.mark.parametrize(("relative_path", "template"), MODEL_PRECEDENCE_CLAIMS.items())
def test_the_model_precedence_claim_names_the_adopted_oneharness(
    relative_path: str, template: str, adopted_oneharness_version: str
) -> None:
    """Which of `--model`, config, and `ONEHARNESS_MODEL` wins is a per-release fact.

    A version literal beside that claim silently becomes an assertion about a release
    nobody measured. Deriving every copy from `config/oneharness.version` turns an
    upgrade into a failure at each one, which is the prompt to re-run the two
    commands the reference section prints — and then to update all three together.
    """
    stated = template.format(version=adopted_oneharness_version)
    # Whitespace-normalized: two of these sentences wrap across lines, and the third
    # would wrap across shell comment markers if it grew.
    written = " ".join((REPO_ROOT / relative_path).read_text(encoding="utf-8").split())

    assert stated in written, (
        f"{relative_path} must state {stated!r}; re-measure the precedence against the "
        "adopted release and update every copy in the same change"
    )


@pytest.mark.reads_docs
def test_telemetry_upgrade_boundary_matches_authoritative_versions(
    adopted_onejudge_version: str, adopted_oneharness_version: str
) -> None:
    """Drift-gate the historical boundary documented for enriched records."""
    text = (REPO_ROOT / "docs" / "telemetry.md").read_text(encoding="utf-8")
    assert f"{adopted_oneharness_version}/{adopted_onejudge_version} upgrade" in text
