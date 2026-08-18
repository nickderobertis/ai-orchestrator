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
    # The filter standing between onejudge's supervisor frame and the planner channel
    # parses that frame's exact shape. Which shape a release writes is per-release, so
    # a bump has to re-measure the parser rather than discover it in a dead monitor.
    Path("scripts/channel-serve.py"): 1,
    # The base config's `user.done_when` is the whole review bar for every dispatch,
    # and it is written to be resolved by the judge against the task. That only works
    # because onejudge hands the criterion over verbatim beside a transcript opening
    # with the task — a per-release behaviour, so the comment names the release it was
    # measured against and joins this gate rather than quietly outliving it.
    Path("config/onejudge.base.yaml"): 1,
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
PUBLISHED_VERSION_REFERENCE_COUNTS: dict[str, dict[Path, int]] = {
    # The dag-scope graph names the release whose schema ceiling bounds the version
    # it could be raised to for the `{task}` placeholder. Both halves of that
    # sentence are per-release measurements — which versions the build reads, and
    # from which one the token stops being literal — so the literal joins this gate
    # rather than quietly outliving the bump that moves the ceiling.
    "oneagentgraph": {Path("graphs/dag-scope.yaml"): 1},
    # Two: the frame shape this filter parses, and the run-id export it deliberately
    # does not read. Both are per-release measurements of the same crate.
    # Four in the operating manual: which plan schema versions the reconciler reads,
    # what a monitor member's environment carries, what a judge command's does, and
    # where a `context` note is delivered.
    "onepipeline": {Path("scripts/channel-serve.py"): 2, Path("docs/orchestration.md"): 4},
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


#: Sentences that assert something about the *adopted* oneharness release itself —
#: which one this repository is on, and what was measured against it — as
#: file → the sentence each must spell. Same contract and same reason as the
#: precedence claims above, but these could not go under the published-CLI gate:
#: that gate requires every `oneharness <version>` in a file to be the adopted one,
#: and this document deliberately names historical floors (0.6.5 for streaming a
#: fallback chain, 0.3.24 for the process-tree timeout) that must NOT move with the
#: pin. Naming the exact sentence is what separates a claim about today's release
#: from a claim about the release something first appeared in.
ADOPTED_ONEHARNESS_CLAIMS = {
    "docs/onejudge-integration.md": (
        "Version {version} is the adopted release",
        "through oneharness {version}, confirmed against the binary",
    ),
}


@pytest.mark.reads_docs
@pytest.mark.parametrize(("relative_path", "templates"), ADOPTED_ONEHARNESS_CLAIMS.items())
def test_claims_about_the_adopted_release_name_the_adopted_release(
    relative_path: str, templates: tuple[str, ...], adopted_oneharness_version: str
) -> None:
    """A version literal beside a per-release claim outlives the bump that invalidated it.

    `Version 0.6.5 is the adopted release` survived two bumps in this document
    precisely because nothing read it. Deriving each sentence from
    `config/oneharness.version` turns the next bump into a failure here, which is the
    prompt to re-measure the claim rather than to retype the number.
    """
    # Whitespace-normalized: these sentences wrap across lines, and a reflow is not
    # a change to what they assert.
    written = " ".join((REPO_ROOT / relative_path).read_text(encoding="utf-8").split())
    for template in templates:
        stated = template.format(version=adopted_oneharness_version)
        assert stated in written, (
            f"{relative_path} must state {stated!r}; re-measure the claim against the "
            "adopted release and update it in the same change"
        )


#: The same contract for onepipeline, whose per-release behaviour this repository
#: measured rather than read: which release the agent side reaches with no `--config`,
#: and therefore which one this host can dispatch under at all. It cannot go under the
#: published-CLI gate either, and for a sharper reason than the oneharness claims: these
#: documents deliberately name a release this repository does NOT adopt — the one whose
#: change to that seam is why the pin is where it is — plus historical "since 0.2.0"
#: statements. Naming the sentence gates the claim about today's release and leaves both
#: of those alone.
ADOPTED_ONEPIPELINE_CLAIMS = {
    "docs/onejudge-integration.md": ("measured against onepipeline {version}",),
    "personas/README.md": ("measured against onepipeline {version}",),
    # What a run names to the agent graph watching it — `ONEPIPELINE_RUN_ID`, set to
    # the run id — restated in five places because the claim is load-bearing in five
    # different arguments: why the pacemaker interpolates `{task}`, why the observer
    # graph is written the way it is, why the planner channel's filter parses a run id
    # out of prose, what an operator should write a member against, and what that
    # filter's own header promises.
    # `tests/e2e/test_orchestrate_launch_e2e.py` re-takes that measurement on a real
    # launch, and this gate holds each restatement to the release it was taken
    # against, so a bump fails at both halves at once.
    #
    # Each site gets its OWN sentence rather than sharing one phrase, because
    # `docs/orchestration.md` carries two of them in sections a reader reaches
    # independently and one shared phrase would gate only whichever came first. A
    # template here names the site it gates.
    "graphs/dag-scope.yaml": (
        "measured against onepipeline {version} on both of that member's sides",
    ),
    "docs/orchestration.md": (
        # The agent-graphs section, on why a member interpolates `{task}`.
        "measured against onepipeline {version} by dumping both sides of a monitor "
        "member's whole environment",
        # The channel-serve section, on what its filter reads and what it leaves.
        "measured against onepipeline {version} in the judge command's own environment",
    ),
    "AGENTS.md": ("measured against onepipeline {version} on a real launch",),
    # Two independent per-release claims share this file. Its header states the
    # request shape each side of the channel writes, so a bump that moved either side
    # would leave it reconciling a frame nobody sends; the second is the run-id export
    # above, which it names and declines to read.
    "scripts/channel-serve.py": (
        "`onepipeline` {version} reads it",
        "measured against onepipeline {version} by dumping this command's whole environment",
    ),
}


@pytest.mark.reads_docs
@pytest.mark.parametrize(("relative_path", "templates"), ADOPTED_ONEPIPELINE_CLAIMS.items())
def test_claims_about_the_adopted_onepipeline_name_the_adopted_release(
    relative_path: str, templates: tuple[str, ...]
) -> None:
    """What a dispatch does is a per-release fact, so each claim names the release.

    These were measured by reading what a real launch produced — the effective
    `onejudge.yaml` a dispatch was given, the `--config` its agent side arrived with,
    and the environment its observer graph was started in. None survives a bump
    unexamined, and the `--config` one is the reason the pin is not simply the newest
    release, so a bump that silently kept the sentence would leave the justification
    for the pin asserting something about a release nobody re-measured.

    Each sentence is required **exactly once**. A restatement that only had to appear
    somewhere in its file would be satisfied by a sibling paragraph in the same
    document, leaving a second site that states the claim ungated; and a duplicate
    left behind by an edit would go unnoticed the same way.
    """
    adopted = (REPO_ROOT / "config" / "onepipeline.version").read_text(encoding="utf-8").strip()
    # Whitespace-normalized: these sentences wrap across lines, and a reflow is not
    # a change to what they assert.
    written = " ".join((REPO_ROOT / relative_path).read_text(encoding="utf-8").split())
    for template in templates:
        stated = template.format(version=adopted)
        assert written.count(stated) == 1, (
            f"{relative_path} states {stated!r} {written.count(stated)} times, not once; "
            "re-measure the claim against the adopted release and update that one site "
            "in the same change"
        )


@pytest.mark.reads_docs
def test_telemetry_upgrade_boundary_matches_authoritative_versions(
    adopted_onejudge_version: str, adopted_oneharness_version: str
) -> None:
    """Drift-gate the historical boundary documented for enriched records."""
    text = (REPO_ROOT / "docs" / "telemetry.md").read_text(encoding="utf-8")
    assert f"{adopted_oneharness_version}/{adopted_onejudge_version} upgrade" in text
