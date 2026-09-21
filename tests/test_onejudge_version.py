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
    # Two links, each spelled once in its text and once in its URL: the command protocol a
    # stand-in speaks, and the judge-list shape a graph member's `judge:` may take.
    Path("docs/onejudge-integration.md"): 4,
    # Which ops a release asks a monitor's judge side, and when — the measurement the
    # completion bar's scoring rests on — said to an operator and to the model that lives
    # under it. The prose half is what a reader acts on and the persona half is what the
    # member is configured by, so a bump that moved either would leave both wrong.
    # The second in the prose names the release whose own source documents
    # `user.settle_on_noop` — the field a monitor settled on its quiet turns would opt out
    # through — and a citation to a file at a version is only worth reading while the
    # version is the one in force, so it joins this gate rather than aging quietly beside
    # the first. The frame shape the retired filter parsed is history there, written with
    # no literal, because a measurement of a deleted file is not re-taken on a bump.
    Path("docs/orchestration.md"): 2,
    Path("personas/orchestrator.yaml"): 1,
    # And the gate that states which onejudge release its op measurement was taken
    # against. A test asserting a per-release behaviour under a release that has moved
    # is the worst kind of green, so its claim is dated here like every other.
    Path("tests/test_planner_channel_personas.py"): 1,
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
    # Both agent-graph documents state the same per-release measurement — which
    # schema versions the build reads, and from which one `{task}` stops being
    # literal — so each carries a literal this gate holds to the pin.
    "oneagentgraph": {Path("graphs/dag-scope.yaml"): 1, Path("graphs/pr-author.yaml"): 1},
    # Seven in the operating manual: which plan schema versions the reconciler reads, what
    # a monitor member's environment carries, what a judge command's does, which
    # dispatches are handed the run they may ask their manager on, where a `context` note
    # is delivered, and the two halves
    # of the write-back's own account — the release below which it deleted the project
    # description an operator authored, and the release its refuse-rather-than-default
    # read was measured against. Those last two are the reason a count is declared here
    # rather than derived: they are literals in prose about a *behaviour* that moved, so
    # a bump has to re-open them exactly as it re-opens the five before them. The eighth is
    # the release whose write-back stopped renaming a destination project and dropping its
    # labels, which is what returned this repository's plans to the board — a third
    # behavioural literal, re-opened by a bump for the same reason as the two above. The
    # ninth is the release whose write-back stopped retrying a refused projection four
    # times a second and started backing it off, which is a fourth behavioural literal and
    # is re-opened by a bump for the same reason: the retry rate an operator is told about
    # is the release's, and a stale number describes an outage nobody would recognise.
    "onepipeline": {Path("docs/orchestration.md"): 9},
}


def _adopted(tool: str) -> str:
    """The release `config/<tool>.version` declares, for a claim gated by sentence.

    The count-based gate above resolves this itself from the tool name it is
    parametrized with. The sentence-based gates below each name one tool, so they
    share this rather than each restating the path.
    """
    return (REPO_ROOT / "config" / f"{tool}.version").read_text(encoding="utf-8").strip()


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
#: must spell for the adopted release: a new site is an entry here, so no copy is left
#: asserting a precedence measured against the previous release.
MODEL_PRECEDENCE_CLAIMS = {
    "docs/onejudge-integration.md": (
        "Measured against the adopted oneharness {version}, a config's "
        "per-harness `model` **beats** the variable"
    ),
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
    commands the reference section prints — and then to update every copy together.
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
    "personas/README.md": (
        "measured against onepipeline {version}",
        # Which oneagentgraph a dispatch reads a persona with, which is what decides
        # the shape every file in that directory has to be written in. What that
        # version *is* has its own source and its own gate — the engine wheel's SBOM,
        # read by `tests/test_linked_libraries.py`. This one holds the other half: that
        # the sentence naming it names the engine release it was read from.
        "at onepipeline v{version}. Read that from what the release",
    ),
    # What a run names to the agent graph watching it — `ONEPIPELINE_RUN_ID`, set to
    # the run id — restated in three places because the claim is load-bearing in three
    # different arguments: why the pacemaker interpolates `{task}`, why the observer
    # graph's judge command can compose the run's channel directory, and what an operator
    # should write a member against.
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
        # The judge-side section, on the variables its command composes the run's channel
        # directory from.
        "measured against onepipeline {version} in the judge command's own environment",
        # The asking section, on which launch shapes reach a worker that can ask.
        "every node dispatch of a run carries it as of onepipeline {version}",
    ),
    # The drafting endings, which did not exist below this release: the paragraph
    # states the release the kind arrived in, so a bump has to re-read whether the
    # vocabulary beside it still holds.
    # The drafting endings. The sentence names the ADOPTED release rather than the
    # one the kind arrived in (0.7.5): the two stopped being the same release at
    # 0.8.0, and a gate on "since" would have forced the prose to claim an arrival
    # that never happened.
    "docs/repo-lifecycle.md": (
        "**It is not silent either, on the adopted onepipeline {version}.**",
        # The release every engine-behaviour claim in that document was read at. It is
        # the header a reader checks before trusting any of them, so a bump that left it
        # behind would date the whole document to a release nothing runs.
        "restated: **`onepipeline` v{version}**",
        # The `Node` field that was accepted and read by nothing, and the removed cost
        # analysis: both are statements that a named release does *not* do something,
        # which a stale version number turns into a statement about a release nobody
        # dispatches. The first moved in this bump — `verify_via_ci` is no longer a
        # field at all — which is exactly the re-reading this gate exists to force.
        "not a field of `Node` on onepipeline v{version} and is refused",
        "absent from `onepipeline` v{version} — so every number in it was a",
        # The onepipeline half of the same flipped denial the sibling gate carries.
        # It read "`onepipeline` v{version} has no notion" of a draft change request,
        # and 0.18.x gave it one: a publication held back by an unarrived release
        # settles its node `complete-but-draft`. The sentence moved with the fact,
        # because the paragraph's conclusion — a pause still opens nothing — did not.
        "`onepipeline` v{version} settles the node that made one `complete-but-draft`",
    ),
    # Where the pre-extraction dispatch wrapper's symbols are denied, and where the
    # run-scope telemetry view was re-measured. Both are per-release readings of the
    # crate rather than of anything this repository writes.
    "docs/telemetry.md": ("re-measured against `onepipeline` v{version} on this host's own",),
}


#: The read API's own per-release facts. It is the one engine here whose source this
#: host does not have — no registered checkout, and `onepipeline-api --help` offers
#: only `serve` — so the wire shape below was *measured* off a live run rather than
#: read off a declaration, and nothing can reconcile the field names. What can be
#: held is the freshness: the paragraph names the release it was measured on, and a
#: bump fails here rather than leaving a schema version and five span kinds asserting
#: something about a build nobody re-ran. Its pin is `config/onepipeline-ui.version`,
#: which `scripts/session-setup.sh` installs `onepipeline-api-cli` from.
ADOPTED_READ_API_CLAIMS = {
    "docs/telemetry.md": ("**`onepipeline-api` {version}**, the release",),
    # The view's own half of the same release, which has the same problem one layer
    # further out: what the bundle renders is a per-release fact nothing here can
    # reconcile, and a bundle renders nothing at all when the data behind it is
    # absent — so a stale claim about it is invisible from the browser as well as
    # from the source. Naming the release is what makes a bump re-open the paragraph.
    "docs/dag-ui.md": ("**`onepipeline-ui` {version}**, the release",),
}


@pytest.mark.reads_docs
@pytest.mark.parametrize(("relative_path", "templates"), ADOPTED_READ_API_CLAIMS.items())
def test_claims_about_the_adopted_read_api_name_the_adopted_release(
    relative_path: str, templates: tuple[str, ...]
) -> None:
    """A measurement is dated to what was measured, since nothing else can check it.

    Every other engine claim in this repository is reconciled against a declaration.
    This one cannot be, so the gate holds the next best thing — that the paragraph
    says which build it describes — and a bump turns a silent staleness into a failing
    check that names the paragraph to re-measure.
    """
    adopted = _adopted("onepipeline-ui")
    written = " ".join((REPO_ROOT / relative_path).read_text(encoding="utf-8").split())
    for template in templates:
        stated = template.format(version=adopted)
        assert written.count(stated) == 1, (
            f"{relative_path} states {stated!r} {written.count(stated)} times, not once; "
            f"`onepipeline-api` {adopted} is what this host serves, so re-measure the "
            "timeline against it and update that one site in the same change"
        )


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
    adopted = _adopted("onepipeline")
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


#: Per-release claims about a *sibling* CLI, as tool → file → the sentence each must
#: spell. Same contract and same reason as the onepipeline claims above: a behaviour
#: this repository measured against a pinned sibling, restated in prose that would
#: otherwise survive the bump that invalidated it.
#:
#: It cannot go under the published-CLI count gate, for the reason the oneharness
#: claims cannot: the file deliberately names a release this repository does **not**
#: adopt, and that literal must not move with the pin.
#:
#: **oneagentgraph** decides which *persona shape* this repository may be written in,
#: and the claim gated here is a refusal — the pinned release rejects the other
#: spelling outright, in whichever direction the pin currently points.
#: `personas/README.md` names the superseded 0.2.18 beside it as history. A bump fails
#: here, which is the prompt to re-take that refusal against the new binary and to move
#: the pin, every file in `personas/`, and `config/onejudge.base.yaml` at once — never
#: one without the others, because a CLI certifying a shape the linked reader refuses
#: produces no member at all.
ADOPTED_SIBLING_CLAIMS: dict[str, dict[str, tuple[str, ...]]] = {
    "oneagentgraph": {
        "personas/README.md": (
            "The pinned oneagentgraph {version} refuses the previous shape outright",
        ),
    },
}


@pytest.mark.reads_docs
@pytest.mark.parametrize(
    ("tool", "relative_path", "templates"),
    [
        (tool, path, templates)
        for tool, files in ADOPTED_SIBLING_CLAIMS.items()
        for path, templates in files.items()
    ],
)
def test_claims_about_an_adopted_sibling_name_the_adopted_release(
    tool: str, relative_path: str, templates: tuple[str, ...]
) -> None:
    """Each was measured against the pinned sibling rather than read off a changelog.

    The oneagentgraph one by probing the installed binary three ways — `persona
    validate`, `oneagentgraph validate`, and a real `oneagentgraph run` of a
    one-member graph naming a 0.3.0-shaped persona by path — each with a
    0.2.18-shaped control beside it.

    Each sentence is required **exactly once**, for the reason the onepipeline gate
    above states: a claim satisfied anywhere in its file leaves a second site ungated,
    and a duplicate left behind by an edit goes unnoticed the same way.
    """
    adopted = _adopted(tool)
    # Whitespace-normalized: these sentences wrap across lines, and a reflow is not a
    # change to what they assert.
    written = " ".join((REPO_ROOT / relative_path).read_text(encoding="utf-8").split())
    for template in templates:
        stated = template.format(version=adopted)
        assert written.count(stated) == 1, (
            f"{relative_path} states {stated!r} {written.count(stated)} times, not once; "
            f"re-measure the claim against the adopted {tool} release and update that one "
            "site in the same change"
        )


@pytest.mark.reads_docs
def test_telemetry_upgrade_boundary_matches_authoritative_versions(
    adopted_onejudge_version: str, adopted_oneharness_version: str
) -> None:
    """Drift-gate the historical boundary documented for enriched records."""
    text = (REPO_ROOT / "docs" / "telemetry.md").read_text(encoding="utf-8")
    assert f"{adopted_oneharness_version}/{adopted_onejudge_version} upgrade" in text
