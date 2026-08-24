"""What this repository says about sequencing a node behind a dependency's release.

Two of the three halves are now adopted and the third is not, and that asymmetry is
the whole hazard. A section that described an unadopted mechanism could go stale one
way — the day a pin moved past it. This one can go stale **both** ways: a pin moving
past the view floor makes its "still unadopted" half wrong, and a pin regressing below
either adopted floor makes its "in force" half wrong. Neither would be contradicted by
a run, because a host that configures no release target gets the same run either way.
So the claims are enumerated here against the section that owns them, and each pin the
section names is reconciled against `config/` and against the floor carrying its half.

`tests/test_decomposition_guidance.py` is the other half and answers a different
question: *which* document may say a thing. This one answers whether the manager's
document still says all of it, and whether what it says about this host is still
true. `tests/e2e/test_release_adoption_in_force_e2e.py` is the third: it drives the
installed artifacts, which is the only thing that can prove what this host can and
cannot do rather than assert it.
"""

from __future__ import annotations

from typing import NamedTuple

import pytest
from published_tools import PUBLISHED_TOOLS
from test_linked_libraries import Release

from orchestrator.root import REPO_ROOT

#: The sentence the section opens with, and the one every claim below is written
#: under. `tests/e2e/test_release_adoption_in_force_e2e.py` measures it against the
#: installed artifacts and reads it from here, so the prose and the measurement cannot
#: come to be about two different claims.
#:
#: It states both halves in one line deliberately. "In force" and "in use" are the two
#: things a reader of this section most needs kept apart, and a sentence that said only
#: one of them would be read as the other.
IN_FORCE_UNUSED = "**The surface and the modes are in force here; nothing on this host uses them.**"

#: The release that added `onevcs release` and the release-target document behind it,
#: as https://github.com/nickderobertis/onevcs/pull/78. Declared here and read by the
#: journey as well: it is the one version this repository has to know to say whether
#: the surface is in force, and two declarations of it could disagree.
RELEASE_SURFACE_FLOOR = Release(0, 13, 0)
#: The release that carries the adoption modes — https://github.com/nickderobertis/onepipeline/pull/113
#: merged, and this is the first `onepipeline` release cut after it. It had no number
#: when this section was written, which is why the section named the change request;
#: now it has one, and the section names both.
RELEASE_MODES_FLOOR = Release(0, 13, 0)
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
    # The half that is a measurement of this host rather than a description of a
    # mechanism. Each of these was driven against an installed binary, and each is
    # what a reader needs in order to tell "this host can do it" from "this host does
    # it" — which is the one distinction this whole section now turns on.
    Claim(
        "no repository here declares a target",
        "no\nrepository registered here declares a release target",
    ),
    Claim(
        "a target-less repository releases nothing",
        "a repository that declares\nnone releases nothing as far as this mechanism is concerned",
    ),
    Claim("the release-targets document's path", "`$ONEVCS_HOME/releases.yml`"),
    Claim("this host has no such document", "**this host does not have one**"),
    Claim("the verbs were driven", "answers `adoption: fast`"),
    Claim("the modes reach the loader", "unknown variant `bogus`, expected `fast` or `published`"),
    Claim("a bad consumes key is refused", "which is not one of\nthis node's deps"),
    Claim("what remains is configuration", "**So what remains is configuration, not adoption.**"),
)

#: The vocabulary decided for this workstream: these repositories publish artifacts
#: rather than deploying anything, and one word is used in the contract, the code, the
#: configuration, the events, and the prose. Held over both halves because the persona
#: travels and would carry a second word into repositories this document never reaches.
FORBIDDEN_VOCABULARY = "deploy target"

#: The three pins the section names as fact, and the release that carries each one's
#: half of the capability. Declared as floors rather than as equalities, and read in
#: **both** directions now that the three no longer agree: the two adopted halves are
#: true while their pins are *at or past* their floors, and the unadopted one is true
#: while its pin is *below* its own. A move in either direction turns the section from
#: accurate into wrong with nothing else on this host reporting it.
CAPABILITY_FLOORS: dict[str, Release] = {
    "onevcs.version": RELEASE_SURFACE_FLOOR,
    "onepipeline.version": RELEASE_MODES_FLOOR,
    "onepipeline-ui.version": RELEASE_VIEW_FLOOR,
}

#: Which of those pins the section claims is in force, and which it claims is not.
#: Split out rather than inferred from a comparison, because the point of the gate is
#: that the *prose* and the *pins* agree: inferring the claim from the pin would make
#: the assertion vacuously true whatever the section says.
ADOPTED_PINS = ("onevcs.version", "onepipeline.version")
UNADOPTED_PIN = "onepipeline-ui.version"

#: The plan node that owns the unadopted half, named in the section so its gap reads
#: as a scheduled piece of work rather than a standing property of this host.
UNADOPTED_OWNER = "`adopt-dag-ui`"


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
@pytest.mark.parametrize("version_file", ADOPTED_PINS)
def test_the_pin_that_puts_a_half_in_force_is_at_or_past_its_floor(version_file: str) -> None:
    """The section claims two halves are in force; a pin below its floor ends that.

    The direction this gate reads was reversed by the adoption it exists across, and
    the reversal is the point: while these were unadopted the hazard was a pin moving
    *forward* past a section that denied it, and now it is a pin moving *back* under a
    section that asserts it. A regression here is not hypothetical — a bad revert of
    `config/onepipeline.version` would leave every measured claim in this section
    describing a binary this host no longer has, with no run contradicting it, because
    a host that declares no release target runs identically either way.
    """
    floor = CAPABILITY_FLOORS[version_file]
    adopted = _adopted(version_file)
    assert Release.parse(adopted, f"config/{version_file}") >= floor, (
        f"config/{version_file} reads {adopted}, below the {floor} that carries its "
        f"half of release adoption. {GUIDANCE_DOCUMENT}'s {GUIDANCE_SECTION!r} says "
        "that half is in force here and quotes measurements taken against it; both "
        "are now false, and so is the pin value the section names"
    )


@pytest.mark.reads_docs
def test_the_half_that_is_not_adopted_is_still_the_half_that_is_not_adopted() -> None:
    """The one pin below its floor, and the sentence that has to move with it.

    The mirror of the gate above, and the one that comes due first: `adopt-dag-ui` is
    a live sibling of the plan this section was rewritten under, and the moment it
    lands, this section's "that third half is the one still unadopted" stops being
    true. Failing here is that node arriving, not a defect — the fix is to re-take the
    sentence rather than to relax this.
    """
    floor = CAPABILITY_FLOORS[UNADOPTED_PIN]
    adopted = _adopted(UNADOPTED_PIN)
    assert Release.parse(adopted, f"config/{UNADOPTED_PIN}") < floor, (
        f"config/{UNADOPTED_PIN} reads {adopted}, at or past the {floor} that carries "
        f"the view. {GUIDANCE_DOCUMENT}'s {GUIDANCE_SECTION!r} calls that half the one "
        f"still unadopted and names {UNADOPTED_OWNER} as the node that would move it; "
        "re-take both sentences against what the view now does on this host"
    )


@pytest.mark.reads_docs
def test_the_unadopted_half_is_owned_by_a_node_rather_than_left_standing() -> None:
    """A gap with no owner reads as a property of the host. This one has an owner.

    Written as its own gate rather than folded into the claim list because it is the
    difference between "this host does not have the view" and "this host does not have
    the view yet, and here is what changes that" — and only the second is true.
    """
    assert UNADOPTED_OWNER in _flat(section()), (
        f"{GUIDANCE_DOCUMENT}'s {GUIDANCE_SECTION!r} no longer names {UNADOPTED_OWNER} "
        f"as what moves config/{UNADOPTED_PIN}. A gap named with no owner reads as a "
        "standing property of this host rather than as scheduled work"
    )


@pytest.mark.reads_docs
def test_the_section_dates_the_adoption_modes_to_the_release_that_carried_them() -> None:
    """The half that had no version to name now has one, and names both.

    While the release was uncut the section named the change request alone and this
    gate refused a version beside it, because a guess in this document is
    indistinguishable from a measurement. The release exists now, so the requirement
    inverts: the change request stays — it is the durable reference and the only thing
    that says *what* the release carried — and the version joins it.
    """
    prose = _flat(section())
    assert "https://github.com/nickderobertis/onepipeline/pull/113" in prose, (
        f"{GUIDANCE_DOCUMENT}'s {GUIDANCE_SECTION!r} no longer names the change request "
        "that carries the adoption modes; the release number alone says which build has "
        "them and not what they are"
    )
    modes = _adopted("onepipeline.version")
    assert _flat(f"onepipeline {modes}, the first release cut after it") in prose, (
        f"{GUIDANCE_DOCUMENT}'s {GUIDANCE_SECTION!r} no longer ties the adoption modes "
        f"to onepipeline {modes} and to being the first release cut after change "
        "request 113; one without the other leaves a reader unable to check either"
    )


@pytest.mark.reads_docs
def test_no_sentence_leaves_the_shared_version_to_disambiguate_itself() -> None:
    """`0.13.0` is two adoptions this cycle, so the section names the tool as well.

    `config/onepipeline.version` and `config/onevcs.version` carry the same number for
    the first time. A reader who meets a bare `0.13.0` in a paragraph about release
    adoption cannot tell whether it is the engine CLI's claim or the version-control
    CLI's, and this section makes one of each within three sentences of the other.
    """
    prose = _flat(section())
    assert "the **version-control CLI** at onevcs 0.13.0" in prose, (
        f"{GUIDANCE_DOCUMENT}'s {GUIDANCE_SECTION!r} no longer says which tool carries "
        "the release-targets surface; with both pins at one number the tool is the only "
        "thing that distinguishes the claim"
    )
    assert "the **engine CLI** at onepipeline 0.13.0" in prose, (
        f"{GUIDANCE_DOCUMENT}'s {GUIDANCE_SECTION!r} no longer says which tool carries "
        "the adoption modes; with both pins at one number the tool is the only thing "
        "that distinguishes the claim"
    )
    assert "Those two numbers are equal and are about different tools" in prose, (
        f"{GUIDANCE_DOCUMENT}'s {GUIDANCE_SECTION!r} no longer warns that the two pins "
        "read one number this cycle; a reader who has not been told will infer one "
        "adoption from two"
    )
