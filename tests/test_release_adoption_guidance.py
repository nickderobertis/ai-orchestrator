"""What this repository says about sequencing a node behind a dependency's release.

The capability is upstream and adopted by nothing here, which is the whole hazard.
Prose describing a mechanism no pin on this host reads has nothing to hold it true:
it cannot be measured, it will not be contradicted by a run, and the day somebody
moves a pin it silently changes from "documented, not adopted" to "wrong". So the
claims are enumerated here against the section that owns them, and the three pins the
section names are reconciled against `config/` rather than restated by hand.

`tests/test_decomposition_guidance.py` is the other half and answers a different
question: *which* document may say a thing. This one answers whether the manager's
document still says all of it, and whether what it says about this host is still
true. `tests/e2e/test_release_adoption_not_in_force_e2e.py` is the third: it drives
the installed artifacts, which is the only thing that can prove the "not in force"
claim rather than assert it.
"""

from __future__ import annotations

import re
from typing import NamedTuple

import pytest
from published_tools import PUBLISHED_TOOLS
from test_linked_libraries import Release

from orchestrator.root import REPO_ROOT

#: The sentence the section opens with, and the one every claim below is written
#: under. `tests/e2e/test_release_adoption_not_in_force_e2e.py` measures it against the
#: installed artifacts and reads it from here, so the prose and the measurement cannot
#: come to be about two different claims.
NOT_IN_FORCE = "**All of this ships upstream and none of it is in force here.**"

#: The release that added `onevcs release` and the release-target document behind it,
#: as https://github.com/nickderobertis/onevcs/pull/78. Declared here and read by the
#: journey as well: it is the one version this repository has to know to say that the
#: capability is not in force, and two declarations of it could disagree.
RELEASE_SURFACE_FLOOR = Release(0, 13, 0)
#: The release that added the view showing which release carried each landed node, and
#: every release event, as https://github.com/nickderobertis/onepipeline-ui/pull/36.
RELEASE_VIEW_FLOOR = Release(0, 6, 3)

#: The document and the section that own the manager-facing half.
GUIDANCE_DOCUMENT = "AGENTS.md"
GUIDANCE_SECTION = "## Sequencing a node behind a release"
#: The planner-facing half, which is a system prompt rather than a document and so is
#: read whole. `tests/test_decomposition_guidance.py` is what keeps the two apart.
PLANNER_PERSONA = "personas/planner.yaml"


class Claim(NamedTuple):
    """One thing the section has to say, and the phrase that says it."""

    #: What the claim is about, for the failure message and the test id.
    subject: str
    phrase: str


#: Every claim this section exists to make. Enumerated rather than summarized because
#: each one is a thing a reader would otherwise have to take on trust from a mechanism
#: they cannot run: what a release target is, how each style is answered, what the
#: resolution chain is, what each adoption mode does to a node, and — the three that
#: are least recoverable once lost — that a wait on a person is a wait on a person,
#: that an unanswered probe is not evidence of anything, and that no probe is a gate.
REQUIRED_CLAIMS = (
    Claim("a release target is one artifact", "is one artifact a repository publishes"),
    Claim("a repository has a set of them", "A repository has a *set* of them"),
    Claim("different targets are different waits", "are different waits"),
    Claim("an automated target carries a probe", "An *automated* target carries a **probe**"),
    Claim(
        "a human-step target carries none", "A **human-step** target carries **no\nprobe at all**"
    ),
    Claim("the acknowledgement operation", "onevcs release acknowledge"),
    Claim("what the acknowledgement records", "records the version against the\nlanding commit"),
    Claim("repeating it is a safe no-op", "Repeating it with the same version is a\nsafe no-op"),
    Claim("a conflicting version is refused", "a **conflicting version is refused**"),
    Claim("superseding is explicit", "until `--supersede` explicitly supersedes it"),
    Claim("human-step is unused here", "a member of the vocabulary nothing here uses"),
    Claim("release-plz is why", "releases\nautomatically through release-plz"),
    Claim("the two modes", "either `fast` — launch now"),
    Claim(
        "the four rungs",
        "the node's own `adoption` field, then the\nrepository rung, "
        "then the global rung, then `fast`",
    ),
    Claim("no fifth rung", "There is no fifth rung, no plan-level tier, and no run-only override"),
    Claim(
        "fast adoption writes the block",
        "the framework writes the reference block, so a task must not",
    ),
    Claim("the arrival note", "sent one `context` note naming the versions"),
    Claim("a task must not pin", "instruct a worker to go and find\nand pin a dependency's commit"),
    Claim("published adoption does not launch", "the node does not launch at all"),
    Claim("the wait is indefinite", "no timeout, no deadline, no retry\nbudget"),
    Claim("the wait never fails a node", "**never fails the node**"),
    Claim("the wait is surfaced", "raise a **non-blocking planner surface** naming what it awaits"),
    Claim(
        "the manager decides",
        "keep waiting, flip\nthat node to fast adoption by live edit, or stop the run",
    ),
    Claim("a human-step wait is a wait on a person", "**A human-step wait is a wait on a person"),
    Claim(
        "the harness never acts for a person",
        "performs a human release step, prompts anybody for one",
    ),
    Claim("a probe is not a gate", "A probe is not a gate"),
    Claim("a probe never rules on a change", "it never rules on a\nchange"),
    Claim("not answered is not not released", '"Not answered" is not "not released"'),
    Claim("a held node holds on it", "a held node stays held on the first"),
    Claim(
        "awaiting a human step is a third answer", "**Awaiting a human step is a third\nanswer**"
    ),
)

#: The vocabulary decided for this workstream: these repositories publish artifacts
#: rather than deploying anything, and one word is used in the contract, the code, the
#: configuration, the events, and the prose. Held over both halves because the persona
#: travels and would carry a second word into repositories this document never reaches.
FORBIDDEN_VOCABULARY = "deploy target"

#: The three pins the section names as fact, and — where there is one to name — the
#: release that carries that pin's half of the capability. Declared as floors rather
#: than as equalities: what makes the section's "none of the three is adopted" claim
#: true is that each pin is *below* the release carrying its half, and what makes it
#: worth gating is that a pin moving past one turns the section from accurate into
#: wrong with nothing else on the host reporting it.
#:
#: `onepipeline` has no floor on purpose. Its half merged as change request 113 and
#: the release carrying it had not been cut when this was written, so a floor here
#: would be an invented version number. The engine's own SBOM is what answers for it,
#: in the journey named in this module's docstring, which reads the `onevcs` that
#: engine links rather than a number nobody has published.
CAPABILITY_FLOORS: dict[str, Release | None] = {
    "onevcs.version": RELEASE_SURFACE_FLOOR,
    "onepipeline.version": None,
    "onepipeline-ui.version": RELEASE_VIEW_FLOOR,
}


def _text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def section() -> str:
    """The owning section's prose, and nothing else in the document."""
    prose = _text(GUIDANCE_DOCUMENT)
    assert f"\n{GUIDANCE_SECTION}\n" in prose, (
        f"{GUIDANCE_DOCUMENT} no longer carries {GUIDANCE_SECTION!r}, which is where "
        "this repository explains a mechanism nothing on this host runs"
    )
    return prose.split(f"\n{GUIDANCE_SECTION}\n", 1)[1].split("\n## ", 1)[0]


def _flat(prose: str) -> str:
    """Collapse every run of whitespace, so a claim may be quoted as one line."""
    return " ".join(prose.split())


def _adopted(version_file: str) -> str:
    """The release `config/<version_file>` declares, read through its one reader."""
    tool = next(tool for tool in PUBLISHED_TOOLS if tool.version_file == version_file)
    return tool.adopted_version


@pytest.mark.reads_docs
@pytest.mark.parametrize("claim", REQUIRED_CLAIMS, ids=lambda claim: claim.subject)
def test_the_section_makes_every_claim_a_reader_cannot_run_the_mechanism_to_learn(
    claim: Claim,
) -> None:
    assert _flat(claim.phrase) in _flat(section()), (
        f"{GUIDANCE_DOCUMENT}'s {GUIDANCE_SECTION!r} no longer says {claim.subject}: the "
        f"phrase {claim.phrase!r} is gone. Nothing on this host runs this mechanism, so "
        "a claim dropped from here is not recoverable by reading a run"
    )


@pytest.mark.reads_docs
@pytest.mark.parametrize("half", (GUIDANCE_DOCUMENT, PLANNER_PERSONA))
def test_neither_half_speaks_of_deploying_what_these_repositories_publish(half: str) -> None:
    """One word for one thing, in the prose as in the contract, the code, and the events."""
    assert FORBIDDEN_VOCABULARY not in _text(half).lower(), (
        f"{half} says {FORBIDDEN_VOCABULARY!r}; these repositories publish artifacts — a "
        "crate, a wheel, a package — and 'release target' is the one word this "
        "workstream's contract, configuration, and events all use"
    )


@pytest.mark.reads_docs
@pytest.mark.parametrize("version_file", sorted(CAPABILITY_FLOORS))
def test_the_pin_the_section_names_is_the_pin_this_checkout_carries(version_file: str) -> None:
    """The section states three pins as fact; `config/` is where that fact lives.

    Stated in the prose because a reader arriving in six months has to be able to tell
    "documented, shipped upstream, not adopted here" from "this is what happens when
    you run a plan today", and a pointer to a file they would have to open answers the
    second question a beat too late. Restating it is only honest while it is checked.
    """
    adopted = _adopted(version_file)
    assert _flat(f"`config/{version_file}` reads {adopted}") in _flat(section()), (
        f"{GUIDANCE_DOCUMENT}'s {GUIDANCE_SECTION!r} does not say that "
        f"`config/{version_file}` reads {adopted}, which it does. Re-date the section "
        "in the same change that moved the pin, or it reads as a claim about a host "
        "that no longer exists"
    )


@pytest.mark.reads_docs
@pytest.mark.parametrize(
    "version_file", sorted(name for name, floor in CAPABILITY_FLOORS.items() if floor)
)
def test_the_pin_that_would_put_this_in_force_has_not_moved(version_file: str) -> None:
    """The section claims none of this is adopted; a pin at or past its floor ends that.

    A failure here is not a defect in the pin — adopting is the point — it is this
    section coming due. Everything in it is written as upstream behaviour rather than
    as a measurement of this host, and the moment a pin reaches its floor that framing
    is what has to change, along with the sentence naming the pin's value.
    """
    floor = CAPABILITY_FLOORS[version_file]
    assert floor is not None
    adopted = _adopted(version_file)
    assert Release.parse(adopted, f"config/{version_file}") < floor, (
        f"config/{version_file} reads {adopted}, at or past the {floor} that carries "
        f"its half of release adoption. {GUIDANCE_DOCUMENT}'s {GUIDANCE_SECTION!r} "
        "says none of this is in force here and that no measurement of it exists on "
        "this host; both sentences "
        "are now due, and so is the pin value the section names"
    )


@pytest.mark.reads_docs
def test_the_section_dates_the_unreleased_half_without_inventing_a_version() -> None:
    """The one half with no version to name, and the sentence that must not acquire one.

    Its change request is merged and the release carrying it was not published when
    this was written. A number written here would be a guess, and a guess in this
    document is indistinguishable from a measurement — so the section names the change
    request and says where the number comes from instead.
    """
    prose = _flat(section())
    assert "https://github.com/nickderobertis/onepipeline/pull/113" in prose, (
        f"{GUIDANCE_DOCUMENT}'s {GUIDANCE_SECTION!r} no longer names the change request "
        "that carries the adoption modes; it is the only durable reference there is "
        "until the release carrying it is published"
    )
    assert "carried by the first `onepipeline` release cut after it" in prose, (
        f"{GUIDANCE_DOCUMENT}'s {GUIDANCE_SECTION!r} no longer says which release "
        "carries the adoption modes in a form that survives the release being cut"
    )
    invented = re.search(r"`onepipeline` release cut after it[^.]*?\b0\.\d+\.\d+", prose)
    assert invented is None, (
        f"{GUIDANCE_DOCUMENT}'s {GUIDANCE_SECTION!r} names a version for the onepipeline "
        f"half ({invented.group(0) if invented else ''}); take it from the registry when "
        "the release exists, and re-date the whole section at the same time"
    )
