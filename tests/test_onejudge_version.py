"""Drift gate for human-readable references to the adopted onejudge version."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from orchestrator import REPO_ROOT

# DRIFT-GATE: config/onejudge.version is the single source of truth. Keep this
# explicit list aligned with the human-readable files that intentionally state
# the adopted onejudge version.
ONEJUDGE_VERSION_REFERENCE_FILES = (
    Path("AGENTS.md"),
    Path("README.md"),
    Path("config/onejudge.base.yaml"),
    Path("docs/onejudge-integration.md"),
    Path("tests/e2e/fake_backend.py"),
)
ONEJUDGE_VERSION_REFERENCE = re.compile(
    r"(?:\bonejudge(?:-cli| SDK/CLI)?(?:'s)?(?: version)?[\s`*(=]+|/onejudge/(?:blob/)?)"
    r"v?(?P<version>\d+\.\d+\.\d+)",
    re.IGNORECASE,
)


@pytest.mark.parametrize("relative_path", ONEJUDGE_VERSION_REFERENCE_FILES)
def test_onejudge_version_references_match_single_source(
    relative_path: Path, adopted_onejudge_version: str
) -> None:
    """Reject stale onejudge literals in every file covered by this drift gate."""
    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    referenced_versions = {
        match.group("version") for match in ONEJUDGE_VERSION_REFERENCE.finditer(text)
    }

    assert referenced_versions, f"drift gate found no onejudge version in {relative_path}"
    assert referenced_versions == {adopted_onejudge_version}, (
        f"{relative_path} references onejudge versions {sorted(referenced_versions)}; "
        f"expected only config/onejudge.version ({adopted_onejudge_version})"
    )
