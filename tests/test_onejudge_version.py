"""Drift gate for human-readable references to the adopted onejudge version."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from orchestrator.root import REPO_ROOT

# DRIFT-GATE: config/onejudge.version is the single source of truth. Keep this
# explicit list aligned with unavoidable human-readable version literals, such
# as links to versioned external documentation.
ONEJUDGE_VERSION_REFERENCE_COUNTS = {
    Path("docs/onejudge-integration.md"): 2,
    # The dag-scope graph names the release whose onejudge cannot serve the
    # planner channel as a command provider. Dating that observation is what makes
    # it honest, and it is exactly the literal an upgrade has to re-measure.
    Path("graphs/dag-scope.yaml"): 1,
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


#: Human-readable literals of a *published CLI's* adopted release, as tool name →
#: file → how many the file is meant to carry. Same contract as the onejudge gate
#: above and for the same reason: a per-release behaviour claim has to name the
#: release it was measured against, and `config/<tool>.version` is the one source
#: of what that release is. Restating it uncovered is how a claim outlives the
#: bump that invalidated it.
PUBLISHED_VERSION_REFERENCE_COUNTS = {
    "onepipeline": {Path("graphs/node-scope.yaml"): 1},
}


def _published_version_reference(tool: str) -> re.Pattern[str]:
    return re.compile(
        rf"\b{re.escape(tool)}(?:-cli)?(?:'s)?(?: version)?[\s`*(=]+v?(?P<version>\d+\.\d+\.\d+)"
    )


@pytest.mark.reads_docs
@pytest.mark.parametrize(
    ("tool", "relative_path", "expected_count"),
    [
        (tool, path, count)
        for tool, files in PUBLISHED_VERSION_REFERENCE_COUNTS.items()
        for path, count in files.items()
    ],
)
def test_published_cli_version_references_match_single_source(
    tool: str, relative_path: Path, expected_count: int
) -> None:
    """Reject a stale published-CLI literal in every file covered by this drift gate."""
    adopted = (REPO_ROOT / "config" / f"{tool}.version").read_text(encoding="utf-8").strip()
    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    matches = list(_published_version_reference(tool).finditer(text))
    referenced = {match.group("version") for match in matches}

    assert len(matches) == expected_count, (
        f"drift gate parsed {len(matches)} of {expected_count} intended {tool} version "
        f"references in {relative_path}"
    )
    assert referenced == {adopted}, (
        f"{relative_path} references {tool} versions {sorted(referenced)}; "
        f"expected only config/{tool}.version ({adopted})"
    )


#: Every place the model-precedence measurement is restated, and the sentence each
#: must spell for the adopted release. Two sites rather than one, because the
#: measurement is the *reason* the wrapper has the shape it does — it names each
#: side's model on its own `oneharness run` AND exports the variable — so the
#: document describing that seam and the wrapper implementing it each say why. A
#: gate over the reference document alone leaves the wrapper asserting a precedence
#: measured against the previous release, with the copy an operator is least likely
#: to be reading when they change it as the only thing that fails.
MODEL_PRECEDENCE_CLAIMS = {
    "docs/onejudge-integration.md": (
        "Measured against the adopted oneharness {version}, a config's "
        "per-harness `model` **beats** the variable"
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
