"""What this repository says about event phases, and about who answers a landing.

Two `AGENTS.md` sections are held here, and they are together because one adoption
carried both and because the same thing goes wrong with either: a claim written
against a release, left standing after the pin moved past it, that nothing on this
host contradicts. Neither is self-correcting. No repository registered here declares
a release target, so no run produces a release event and a Release-phase sentence is
true or false with no run to say which; and a landing question is asked by a person,
once, about work they are deciding whether to re-dispatch — the one reading nothing
re-reads for them.

`tests/test_release_adoption_guidance.py` is the sibling this sits beside and answers
the question one release earlier: whether the section that explains release *targets*
still says all of it. This one answers the two claims that adoption's successor moved.

**The landing half is the one to read carefully.** The passage it holds is a warning
with two halves that a reader will conflate unless the document keeps them apart —
onevcs 0.14.0 fixed the retry half and left the squash-merge half exactly where it
was — and each half fails differently. Deleting the squash-merge half would tell a
manager that `content comparison` can be trusted, which it cannot at any release.
Keeping the retry half undated would leave the document warning about a defect the
adopted release closed, which costs a manual confirmation on every retried node
forever. So both are required, and each is required to name what it is dated to.
"""

from __future__ import annotations

from typing import NamedTuple

import pytest
from published_tools import PUBLISHED_TOOLS
from test_linked_libraries import Release

from orchestrator.root import REPO_ROOT

#: The document both sections live in. `AGENTS.md` rather than a page under `docs/`
#: because both are things a *manager* acts on mid-run: which phase a filter may name,
#: and whether a `landed:` answer may be believed.
GUIDANCE_DOCUMENT = "AGENTS.md"
#: The section that documents the phase vocabulary.
PHASE_SECTION = "## What phase a change's events belong to"
#: The release that added the phase vocabulary, the retry chain, and the release
#: correlation, as https://github.com/nickderobertis/onevcs/pull/80. A floor rather
#: than an equality, for the reason `CAPABILITY_FLOORS` gives in the sibling module:
#: the release something arrived in never moves, and the pin above it does.
PHASE_FLOOR = Release(0, 14, 0)
#: The release that relays those phases and the correlated releases into a run, as
#: https://github.com/nickderobertis/onepipeline/pull/117.
RELAY_FLOOR = Release(0, 14, 0)

#: Which pin carries which half, so a regression under either fails here rather than
#: leaving the section describing a build this host no longer has.
PHASE_FLOORS: dict[str, Release] = {
    "onevcs.version": PHASE_FLOOR,
    "onepipeline.version": RELAY_FLOOR,
}


class Claim(NamedTuple):
    """One thing a section has to say, and the phrase that says it."""

    #: What the claim is about, for the failure message and the test id.
    subject: str
    phrase: str


#: Every claim the phase section exists to make. Enumerated rather than summarized
#: for the reason the sibling module's list is: nothing on this host exercises the
#: Release phase, so a claim dropped from here is not recoverable by reading a run.
#:
#: The two that are least recoverable, and least guessable, are the last three. The
#: validation-error-versus-drop asymmetry reads as an inconsistency until somebody
#: explains it; where that rule lives is a distinction a manager will get backwards
#: after one command-line events read; and the landing-commit correlation is the
#: reason a consumer must never spell the second stream's address.
PHASE_CLAIMS = (
    Claim("every event carries a phase", "Every `onevcs` event carries a **phase**"),
    Claim("the four phases", "**Development**"),
    Claim("integrate is the second", "**Integrate**"),
    Claim("review is the third", "**Review**"),
    Claim("release is the fourth", "**Release**"),
    Claim("phase is not lifecycle", "spelled `Phase` rather than\n`Lifecycle`"),
    Claim("a phase is named instead of its kinds", "arrives in the read that already wanted it"),
    Claim("the producer stamps it", "**The producer stamps it, and one kind is why.**"),
    Claim("the push split", "a\n`push` of the session's own branch is Development"),
    Claim("a push elsewhere is integrate", "a push of anything else"),
    Claim("only the pusher knows", "only the process that pushed knows which it did"),
    Claim("phase is a filter field", "Phase is a field of the event-filter grammar"),
    Claim(
        "the default is the supported set",
        "**The default is every phase the repository supports",
    ),
    Claim("a local-first identity has no review", "so has **no Review phase**"),
    Claim("no targets means no release phase", "so has **no Release phase**"),
    Claim("both exclusions are live here", "Both\nexclusions are live here"),
    Claim("an unreachable answer widens", "**widens** the set rather than narrowing it"),
    Claim(
        "naming one is an error and being in one is a drop",
        "**Naming an unsupported phase is a validation error; merely being in one is a "
        "silent\ndrop.**",
    ),
    Claim("the refusal names what it has", "the phases it does have"),
    Claim("a dropped event is not announced", "dropped without comment"),
    Claim("the rule lives at the library seam", "**That rule lives at the library seam"),
    Claim("the command line does not scope", "`onevcs\nevents --filter` does not"),
    Claim(
        "the releases reach a run through the public reader",
        "**A session's release events reach a run through the public reader, correlated by\n"
        "landing commit.**",
    ),
    Claim("the other two are on the identity's record", "outside every session"),
    Claim("the landing commit is the only join", "the landing commit is the only thing"),
    Claim(
        "the second address is never derived",
        "never handed\nout, named in a refusal, or derivable",
    ),
    Claim("a retried branch's release follows the chain", "with retries followed"),
    Claim("nothing on this host uses it yet", "no run relays a release\nevent"),
)

#: Every claim the landing passage has to make, split by which half of the warning it
#: belongs to. Split rather than listed flat because that is the distinction the whole
#: passage turns on, and a failure that did not say which half was gone would send a
#: reader to re-read the wrong one.
SQUASH_HALF_CLAIMS = (
    Claim("the warning has two halves", "**That warning has two halves"),
    Claim("the squash half is untouched", "*The squash-merge half stands untouched.*"),
    Claim("the deciding module did not move", "is one blob —\n`f8529d72`"),
    Claim(
        "the last tier can never say yes",
        "must never answer `yes` — it is a comparison, not\na record",
    ),
    Claim("a landed branch is an ancestor of nothing", "afterwards an ancestor of nothing"),
    Claim("unknown is undecidable", "`unknown` still means undecidable from history"),
    Claim("the fork-point diff is not the answer", "that measures from the\nfork point"),
    Claim("only presence on the base answers", "Only the files' presence on the base"),
)
LANDING_RETRY_HALF_CLAIMS = (
    Claim("the retry half is fixed", "*The retry half is fixed, and only forward.*"),
    Claim("the link is a retried_by token", "writes a `retried_by` token onto the"),
    Claim("it is written on the older record", "**older** record"),
    Claim("the newest session answers", "the newest session of the chain, whose evidence is the"),
    Claim("a superseded clone stops deciding", "stops a superseded run clone from deciding"),
    Claim("an unfollowable chain stops", "stops rather than falling back"),
    Claim(
        "the link is written at session open",
        "**But that link is written at `session open`",
    ),
    Claim("this host carries none", "**none** carries a\n`retried_by`"),
    Claim("a pre-adoption branch reads the old warning", "read the old\nwarning unchanged"),
    Claim("both answers are unknown there", "treat **both** of that tier's answers as unknown"),
    Claim("no is the dangerous one", "`no` is the dangerous one"),
    Claim("decided by is still read first", "still read before `landed:`"),
)

#: The date the landing passage was re-measured on, held as one string with the
#: release it was taken against and with the word that says it was re-taken.
#:
#: One string rather than three assertions, and that is a repair rather than a
#: tidy-up: asserting the bare date over the document passed while the landing
#: sentence had no date at all, because another section of `AGENTS.md` carries the
#: same date for a different measurement. A stamp is only evidence where it is
#: attached to the claim it stamps.
LANDING_MEASUREMENT_STAMP = "Re-measured {date} on the pinned onevcs {release}:"
LANDING_MEASUREMENT_DATE = "2026-08-24"
#: The reference the measurement was taken against — the incident's own, so a later
#: reader can re-take exactly the measurement this sentence reports.
LANDING_MEASUREMENT_REFERENCE = "https://github.com/nickderobertis/oneagentgraph/pull/73"
#: What that reference still answered, verbatim. Quoted rather than paraphrased: the
#: whole value of the sentence is that a reader can run the verb and compare.
LANDING_MEASUREMENT_ANSWER = "still answers `landed: no`, `decided\nby: content comparison`"


def _text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def flat(prose: str) -> str:
    """Collapse every run of whitespace, so a claim may be quoted as one line.

    The same normalization the sibling module and the pin drift gates compare under,
    so a reflowed paragraph is not a failing gate about nothing.
    """
    return " ".join(prose.split())


def section(heading: str) -> str:
    """One section's prose, and nothing else in the document."""
    prose = _text(GUIDANCE_DOCUMENT)
    assert f"\n{heading}\n" in prose, (
        f"{GUIDANCE_DOCUMENT} no longer carries {heading!r}, which is where this "
        "repository explains what it measured of the adopted engines"
    )
    return prose.split(f"\n{heading}\n", 1)[1].split("\n## ", 1)[0]


def _adopted(version_file: str) -> str:
    """The release `config/<version_file>` declares, read through its one reader."""
    tool = next(tool for tool in PUBLISHED_TOOLS if tool.version_file == version_file)
    return tool.adopted_version


@pytest.mark.reads_docs
@pytest.mark.parametrize("claim", PHASE_CLAIMS, ids=lambda claim: claim.subject)
def test_the_phase_section_makes_every_claim_a_reader_cannot_run_a_run_to_learn(
    claim: Claim,
) -> None:
    """Nothing here produces a release event, so the prose is the only account of one.

    Two of the four phases are exercised by every lifecycle dispatch on this host and
    two are not: `ai-orchestrator` is `local-direct` so no session of it reaches
    Review, and no repository registered here declares a release target so none
    reaches Release. A claim about either dropped from this section is not recoverable
    by reading a run, because there is no run that would contain it.
    """
    assert flat(claim.phrase) in flat(section(PHASE_SECTION)), (
        f"{GUIDANCE_DOCUMENT}'s {PHASE_SECTION!r} no longer says {claim.subject}: the "
        f"phrase {claim.phrase!r} is gone"
    )


@pytest.mark.reads_docs
@pytest.mark.parametrize("version_file", sorted(PHASE_FLOORS))
def test_the_phase_section_names_the_pin_this_checkout_carries(version_file: str) -> None:
    """The section states both pins as fact; `config/` is where that fact lives.

    Restated in the prose because a manager deciding whether a filter may name a phase
    needs the answer in the sentence rather than in a file they would have to open,
    and restating it is only honest while something checks it.
    """
    adopted = _adopted(version_file)
    prose = flat(section(PHASE_SECTION))
    assert f"`config/{version_file}`" in prose, (
        f"{GUIDANCE_DOCUMENT}'s {PHASE_SECTION!r} no longer names `config/{version_file}`, "
        "so a reader cannot tell which pin decides the half it carries"
    )
    assert flat(f"both read {adopted}") in prose, (
        f"{GUIDANCE_DOCUMENT}'s {PHASE_SECTION!r} does not say that the two pins read "
        f"{adopted}, which they do. Re-date the section in the same change that moved "
        "the pin, or it reads as a claim about a host that no longer exists"
    )


@pytest.mark.reads_docs
@pytest.mark.parametrize("version_file", sorted(PHASE_FLOORS))
def test_the_pin_that_puts_the_phase_vocabulary_in_force_is_at_or_past_its_floor(
    version_file: str,
) -> None:
    """A pin below its floor makes every measured sentence in that section false.

    The direction that matters is a *revert*. No repository here declares a release
    target, so a host on the release before this one runs identically — same
    dispatches, same surfaces, same settlements — and nothing in a run would
    contradict a section claiming a phase vocabulary the binary does not have.
    """
    floor = PHASE_FLOORS[version_file]
    adopted = _adopted(version_file)
    assert Release.parse(adopted, f"config/{version_file}") >= floor, (
        f"config/{version_file} reads {adopted}, below the {floor} that carries its half "
        f"of the phase vocabulary. {GUIDANCE_DOCUMENT}'s {PHASE_SECTION!r} says it is in "
        "force here and quotes measurements taken against it; both are now false"
    )


@pytest.mark.reads_docs
def test_the_phase_section_names_both_change_requests_that_carry_it() -> None:
    """The release number says which build has it; the change request says what it is.

    Both, for the reason the sibling module requires both of change request 113: a
    number alone leaves a reader unable to find out what landed, and a change request
    alone leaves them unable to tell whether this host has it.
    """
    prose = flat(section(PHASE_SECTION))
    for change_request in (
        "https://github.com/nickderobertis/onevcs/pull/80",
        "https://github.com/nickderobertis/onepipeline/pull/117",
    ):
        assert change_request in prose, (
            f"{GUIDANCE_DOCUMENT}'s {PHASE_SECTION!r} no longer names {change_request}, so "
            "a reader cannot find out what the release it names actually carried"
        )


@pytest.mark.reads_docs
@pytest.mark.parametrize("claim", SQUASH_HALF_CLAIMS, ids=lambda claim: claim.subject)
def test_the_landing_passage_keeps_the_half_no_release_has_fixed(claim: Claim) -> None:
    """The squash-merge half is true at every release, and deleting it is the danger.

    `crates/onevcs/src/landed.rs` is one blob at v0.11.0, v0.13.0 and v0.14.0 alike, so
    nothing about the four tiers has moved since the incident this passage records. A
    reader who took onevcs 0.14.0's retry fix as making `content comparison`
    trustworthy would be wrong in the direction that re-dispatches merged work, which
    is the failure the whole passage exists to prevent.
    """
    assert flat(claim.phrase) in flat(_text(GUIDANCE_DOCUMENT)), (
        f"{GUIDANCE_DOCUMENT}'s landing passage no longer says {claim.subject}: the "
        f"phrase {claim.phrase!r} is gone. That half of the warning is true at every "
        "release and is not what the retry chain fixed"
    )


@pytest.mark.reads_docs
@pytest.mark.parametrize("claim", LANDING_RETRY_HALF_CLAIMS, ids=lambda claim: claim.subject)
def test_the_landing_passage_states_what_the_retry_chain_did_and_did_not_fix(
    claim: Claim,
) -> None:
    """The half that moved, including the half of *that* which has not reached here.

    The retry chain is written at `session open`, onto the record a new session
    supersedes, so it exists only for sessions opened from this adoption forward. A
    passage that said the defect was fixed without saying that would have a manager
    believe a `landed: no` about a branch retried last week.
    """
    assert flat(claim.phrase) in flat(_text(GUIDANCE_DOCUMENT)), (
        f"{GUIDANCE_DOCUMENT}'s landing passage no longer says {claim.subject}: the "
        f"phrase {claim.phrase!r} is gone"
    )


@pytest.mark.reads_docs
def test_the_landing_passage_dates_its_measurement_to_the_release_it_was_taken_on() -> None:
    """A re-measurement that does not say what it ran on is a claim, not a measurement.

    All four together — the release, the date, the reference, and the answer — because
    each alone invites the wrong reading. The release without the date does not say
    whether the measurement survived the last change under it; the date without the
    release does not say what was running; the answer without the reference cannot be
    re-taken by the next reader who doubts it.
    """
    prose = flat(_text(GUIDANCE_DOCUMENT))
    adopted = _adopted("onevcs.version")

    stamp = LANDING_MEASUREMENT_STAMP.format(date=LANDING_MEASUREMENT_DATE, release=adopted)
    assert flat(stamp) in prose, (
        f"{GUIDANCE_DOCUMENT}'s landing passage no longer says {stamp!r}. The date and "
        f"the release are asserted as one string because {LANDING_MEASUREMENT_DATE} "
        "appears elsewhere in this document for a different measurement, so a bare date "
        "is satisfied by a section that is not this one"
    )
    assert LANDING_MEASUREMENT_REFERENCE in prose, (
        f"{GUIDANCE_DOCUMENT}'s landing passage no longer names "
        f"{LANDING_MEASUREMENT_REFERENCE}, which is the reference the measurement was "
        "taken against and the only way a later reader re-takes it"
    )
    assert flat(LANDING_MEASUREMENT_ANSWER) in prose, (
        f"{GUIDANCE_DOCUMENT}'s landing passage no longer quotes what that reference "
        "answered. The verb's own words are the point: a paraphrase cannot be compared "
        "against a fresh run of it"
    )


@pytest.mark.reads_docs
def test_the_landing_passage_is_not_dated_to_a_release_this_host_does_not_run() -> None:
    """The stale-claim direction, which is the one nothing else here would catch.

    The passage used to be dated to onevcs 0.13.0 and to say the four tiers had not
    moved since 0.11.0. Both sentences survived a pin bump once already. What this
    refuses is the specific residue of that: a re-measurement sentence naming a release
    that is neither the pin nor a release the passage is deliberately comparing against.
    """
    prose = flat(_text(GUIDANCE_DOCUMENT))
    adopted = _adopted("onevcs.version")
    stale = [
        release
        for release in ("0.11.0", "0.12.0", "0.13.0")
        if release != adopted and flat(f"on the pinned onevcs {release}") in prose
    ]
    assert not stale, (
        f"{GUIDANCE_DOCUMENT}'s landing passage reports a re-measurement on the pinned "
        f"onevcs {stale}, and config/onevcs.version reads {adopted}. A measurement is "
        "re-taken and re-dated in the same change that moves the pin, never inherited"
    )
