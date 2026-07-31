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


@pytest.mark.reads_docs
def test_telemetry_upgrade_boundary_matches_authoritative_versions(
    adopted_onejudge_version: str, adopted_oneharness_version: str
) -> None:
    """Drift-gate the historical boundary documented for enriched records."""
    text = (REPO_ROOT / "docs" / "telemetry.md").read_text(encoding="utf-8")
    assert f"{adopted_oneharness_version}/{adopted_onejudge_version} upgrade" in text
