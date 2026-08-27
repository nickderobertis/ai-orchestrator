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
LANDING_MEASUREMENT_DATE = "2026-08-25"
#: The ref the verb was actually asked about, so a later reader re-takes exactly the
#: measurement rather than a similar one. The incident's own change request stopped
#: answering here on 2026-08-25 — no session record correlates it any more — and a
#: measurement nobody can re-take is not evidence, whatever it once showed.
LANDING_MEASUREMENT_REF = "onevcs/s-a37f615ff961"
#: The change request that ref's work landed through, which is what makes the answer
#: below dangerous rather than merely uncertain: it merged, and the verb still says no.
LANDING_MEASUREMENT_CHANGE_REQUEST = "https://github.com/nickderobertis/onetaskgraph/pull/15"
#: What that ref still answered, verbatim. Quoted rather than paraphrased: the
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


def _landing_measurement_passage() -> str:
    """The one paragraph the landing re-measurement is reported in, flattened.

    Scoped to the paragraph rather than to the document because every part of a
    measurement has to be attached to it: a ref named in one section and an answer
    quoted in another are two claims a reader cannot pair, and a gate satisfied by
    them separately would pass a passage that reported neither together.

    Delimited by the stamp and the next blank line, which is what a Markdown paragraph
    is here — no heading bounds it, since the passage sits inside a long section.
    """
    stamp = LANDING_MEASUREMENT_STAMP.format(
        date=LANDING_MEASUREMENT_DATE, release=_adopted("onevcs.version")
    )
    prose = _text(GUIDANCE_DOCUMENT)
    paragraphs = [block for block in prose.split("\n\n") if flat(stamp) in flat(block)]
    assert len(paragraphs) == 1, (
        f"{GUIDANCE_DOCUMENT} carries {len(paragraphs)} paragraphs stamped {stamp!r}, "
        "and this gate reconciles exactly one. A re-measurement reported twice is two "
        "claims that can drift apart; reported nowhere is no measurement at all"
    )
    return flat(paragraphs[0])


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
    """The section states each pin as fact; `config/` is where that fact lives.

    Restated in the prose because a manager deciding whether a filter may name a phase
    needs the answer in the sentence rather than in a file they would have to open,
    and restating it is only honest while something checks it.

    Asserted **per pin** rather than as one `both read <version>` phrase, and that is a
    repair rather than a loosening. The shared phrase was written on the adoption where
    `config/onepipeline.version` and `config/onevcs.version` happened to carry the same
    number, and it silently required them to go on doing so: on 2026-08-25 they parted
    — 0.16.1 and 0.15.4 — and no sentence could satisfy it for both. Requiring each pin
    to be named beside its own release says the same thing where they coincide and goes
    on saying it where they do not, which is the case a reader most needs the prose to
    be honest about.
    """
    adopted = _adopted(version_file)
    prose = flat(section(PHASE_SECTION))
    assert f"`config/{version_file}`" in prose, (
        f"{GUIDANCE_DOCUMENT}'s {PHASE_SECTION!r} no longer names `config/{version_file}`, "
        "so a reader cannot tell which pin decides the half it carries"
    )
    assert flat(f"`config/{version_file}` reads {adopted}") in prose, (
        f"{GUIDANCE_DOCUMENT}'s {PHASE_SECTION!r} does not say that `config/{version_file}` "
        f"reads {adopted}, which it does. Re-date the section in the same change that "
        "moved the pin, or it reads as a claim about a host that no longer exists"
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

    `crates/onevcs/src/landed.rs` is one blob at v0.11.0, v0.13.0, v0.14.0 and the
    pinned v0.15.4 alike, so
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

    All five together and in one paragraph — the release, the date, the ref, the change
    request, and the answer — because each alone invites the wrong reading. The release
    without the date does not say whether the measurement survived the last change
    under it; the date without the release does not say what was running; the answer
    without the ref cannot be re-taken by the next reader who doubts it; and the answer
    without the change request reads as uncertainty rather than as the verb
    contradicting a merge that happened.

    In one paragraph because that is what pairs them. Asserted over the whole document,
    each would be satisfied by an unrelated sentence elsewhere in it, and this document
    is long enough that some of them are.
    """
    stamp = LANDING_MEASUREMENT_STAMP.format(
        date=LANDING_MEASUREMENT_DATE, release=_adopted("onevcs.version")
    )
    # `_landing_measurement_passage` has already failed if the stamp is absent or
    # doubled, so what is left to check is that the rest of the measurement is here
    # with it rather than scattered.
    passage = _landing_measurement_passage()

    assert LANDING_MEASUREMENT_REF in passage, (
        f"{GUIDANCE_DOCUMENT}'s paragraph stamped {stamp!r} no longer names "
        f"{LANDING_MEASUREMENT_REF}, which is the ref the verb was asked about and the "
        "only way a later reader re-takes this measurement rather than a similar one"
    )
    assert LANDING_MEASUREMENT_CHANGE_REQUEST in passage, (
        f"{GUIDANCE_DOCUMENT}'s paragraph stamped {stamp!r} no longer names "
        f"{LANDING_MEASUREMENT_CHANGE_REQUEST}, the change request that ref's work "
        "landed through. Without it the quoted answer reads as uncertainty rather than "
        "as the verb contradicting a merge that happened"
    )
    assert flat(LANDING_MEASUREMENT_ANSWER) in passage, (
        f"{GUIDANCE_DOCUMENT}'s paragraph stamped {stamp!r} no longer quotes what that "
        "ref answered. The verb's own words are the point: a paraphrase cannot be "
        "compared against a fresh run of it"
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
        for release in ("0.11.0", "0.12.0", "0.13.0", "0.14.0")
        if release != adopted and flat(f"on the pinned onevcs {release}") in prose
    ]
    assert not stale, (
        f"{GUIDANCE_DOCUMENT}'s landing passage reports a re-measurement on the pinned "
        f"onevcs {stale}, and config/onevcs.version reads {adopted}. A measurement is "
        "re-taken and re-dated in the same change that moves the pin, never inherited"
    )


#: The section the recoverable-branch listing is documented in. Named separately from
#: `PHASE_SECTION` because the two claims this module now holds sit in different
#: sections of the same document: the tiers are explained where the repository
#: explains itself, and the listing is explained with the rest of the command surface.
COMMAND_SURFACE_SECTION = "## Command surface"
#: The section the landing tiers are explained in — the one this document opens with,
#: because what a `landed:` answer is worth is part of what this repository is.
LANDING_TIER_SECTION = "## What this repo is"

#: The pre-correction sentence, verbatim. Held as a refusal rather than as a claim
#: because this is the exact wording the correction replaced, and because a reader
#: repairing a reflowed paragraph would naturally reach for it again: it is shorter,
#: it reads as confident, and nothing about it looks wrong. It is wrong for a remote
#: identity, whose every record of a landing lives in `$ONEVCS_HOME` rather than in
#: the repository, so a branch this host has forgotten comes back into the listing
#: with a `publish-branch` command beside it however long ago it merged.
FLATTENED_RECOVERABLE_CLAIM = "**What it will no longer offer is a branch that already landed.**"

#: Every claim the per-workflow split has to make, and the whole reason this module
#: grew a third subject. Enumerated the way `PHASE_CLAIMS` is, and for a sharper
#: version of the same reason: this host's own state root answers `a recorded landing`
#: for *both* workflows, so no command a manager runs here contradicts a document that
#: has flattened the split back into one sentence. The divergence only appears once the
#: session record is gone — on another host, after a state-root reset, or for a branch
#: no session ever recorded — which is exactly when nobody is in a position to re-derive
#: it.
WORKFLOW_SPLIT_CLAIMS = (
    Claim(
        "the reachable tiers are decided by the workflow",
        "**Which of the four tiers a landing can reach at all is decided by its publication\n"
        "workflow",
    ),
    Claim("onevcs stamps only the commit it writes", "stamps the commit *it* writes"),
    Claim(
        "local-direct stamps the base",
        "the base carries `Orchestrator-Landed-Commit: <the branch's tip>`",
    ),
    Claim(
        "the prefix comes from the rules file", "`trailer_prefix` `config/onevcs.rules.yml` sets"
    ),
    Claim("that trailer outlives this host", "outlives everything this\nhost stores"),
    Claim("a remote base commit carries no trailer", "GitHub's\nsquash carries no trailer"),
    Claim(
        "a remote landing is stamped on the branch",
        "as a `chore: record the landing of <branch>` commit **on the branch**",
    ),
    Claim("nothing reads the branch stamp back", "Nothing reads that one back"),
    Claim(
        "neither remote record survives losing this host's state",
        "**neither of those survives\nthe loss of this host's state**",
    ),
    Claim(
        "the change-request tier needs a recorded change request",
        "the `(#51)` sitting in the base's own subject goes\nunread",
    ),
    Claim(
        "content comparison means a lost record for a remote branch",
        "what a lost record looks like, not what\nan unlanded branch looks like",
    ),
)

#: What the corrected recoverable claim has to keep saying. Both workflow words in the
#: headline, so a reader can tell which of this host's identities it holds for without
#: running anything, and the remote row's own wording, so they recognise it when it
#: appears.
RECOVERABLE_SPLIT_CLAIMS = (
    Claim(
        "the claim names the workflow it holds for",
        "already landed — unconditionally for a `local-direct`\nidentity, and only while this "
        "host's record of the landing survives for a remote\none.**",
    ),
    Claim(
        "what differs is what is left to read",
        "**What that\ninference has left to read is what differs by workflow**",
    ),
    Claim(
        "a local-direct landing is stamped on the base",
        "stamped onto the base by\n`onevcs`'s own squash commit",
    ),
    Claim(
        "a remote landing's records are all host state",
        "every record of it lives in\n`$ONEVCS_HOME`",
    ),
    Claim(
        "a forgotten remote branch is listed again", "is listed again, under `— may\nhave landed`"
    ),
    Claim("a remote row means no record", "reporting that it has no record rather than that"),
)

#: The stamp the workflow-split measurement is reported under. Deliberately a
#: different shape from `LANDING_MEASUREMENT_STAMP` — that one ends at a colon and
#: reports one ref, this one reports a pair — so the two paragraphs cannot satisfy
#: each other's gate.
SPLIT_MEASUREMENT_STAMP = "**Re-measured {date} on the pinned onevcs {release}, over two landings"
SPLIT_MEASUREMENT_DATE = "2026-08-27"
#: Both refs, because the whole measurement is a comparison: either alone is an
#: anecdote about one workflow rather than evidence that the workflow is what decides.
SPLIT_MEASUREMENT_REFS = ("onevcs/s-42dae8f0b0f5", "onevcs/s-a221fd101a0f")
#: The change request the remote half landed through, for the reason the sibling
#: measurement names one: without it the quoted `unknown` reads as uncertainty rather
#: than as the verb having lost a merge that happened.
SPLIT_MEASUREMENT_CHANGE_REQUEST = "https://github.com/nickderobertis/onetaskgraph/pull/51"
#: What each half answered with no session record, verbatim, so a later reader
#: re-takes this measurement rather than a similar one.
SPLIT_MEASUREMENT_ANSWERS = (
    "`decided by: a landing trailer on the base (ed8c396…)`",
    "`landed: unknown`, `decided by:\ncontent comparison`",
)
#: The condition that separates the two answers. Without it the paragraph reports two
#: verbs disagreeing and gives a reader nothing to reproduce.
SPLIT_MEASUREMENT_CONDITION = "and **no session records**"


def _paragraph_containing(needle: str, description: str) -> str:
    """The one paragraph of the guidance document carrying `needle`, flattened.

    Same reason `_landing_measurement_passage` scopes to a paragraph: a ref named in
    one section and an answer quoted in another are two claims a reader cannot pair,
    and a gate satisfied by them separately would pass a passage that reported neither
    together.
    """
    prose = _text(GUIDANCE_DOCUMENT)
    paragraphs = [block for block in prose.split("\n\n") if flat(needle) in flat(block)]
    assert len(paragraphs) == 1, (
        f"{GUIDANCE_DOCUMENT} carries {len(paragraphs)} paragraphs containing "
        f"{description}, and this gate reconciles exactly one. Reported twice is two "
        "claims that can drift apart; reported nowhere is no measurement at all"
    )
    return flat(paragraphs[0])


@pytest.mark.reads_docs
@pytest.mark.parametrize("claim", WORKFLOW_SPLIT_CLAIMS, ids=lambda claim: claim.subject)
def test_the_landing_tiers_say_which_workflow_can_reach_each(claim: Claim) -> None:
    """Which tiers a landing can reach is a property of the workflow that published it.

    `onevcs` stamps the commit it writes and each workflow writes only one of the two:
    a `local-direct` publication writes the base's squash commit and stamps the
    branch's tip onto it, while a remote publication's base commit is the host's and
    carries nothing, leaving the session record and the change request it names — both
    in `$ONEVCS_HOME` — as the whole of the evidence. A document that stated the tiers
    without that split would leave a manager reading `content comparison` on a merged
    remote branch as *not published*, which is the reading that re-dispatches work that
    already landed.
    """
    assert flat(claim.phrase) in flat(section(LANDING_TIER_SECTION)), (
        f"{GUIDANCE_DOCUMENT}'s landing-tier passage no longer says {claim.subject}: "
        f"the phrase {claim.phrase!r} is gone. The split is not recoverable by running "
        "anything here — this host's state root answers `a recorded landing` for both "
        "workflows, and they only diverge once that record is gone"
    )


@pytest.mark.reads_docs
def test_the_recoverable_claim_is_not_flattened_back_to_one_workflow() -> None:
    """The exact pre-correction sentence, refused by name.

    It is the sentence a reflow or a tidy-up reaches for, because it is shorter and
    reads as confident, and it is wrong for every remote identity registered here. A
    manager verified a landing, read this listing, and was offered a `publish-branch`
    command for the branch that had just landed; the document is what stopped them
    following it, and this claim is the half of the document that would not have.
    """
    assert flat(FLATTENED_RECOVERABLE_CLAIM) not in flat(section(COMMAND_SURFACE_SECTION)), (
        f"{GUIDANCE_DOCUMENT}'s {COMMAND_SURFACE_SECTION!r} states "
        f"{FLATTENED_RECOVERABLE_CLAIM!r} as one workflow-independent sentence. It holds "
        "for a `local-direct` identity, whose landing is stamped onto the base in the "
        "repository's own history, and not for a remote one, whose every record lives in "
        "`$ONEVCS_HOME`: say which, or the confident half is the half that gets acted on"
    )


@pytest.mark.reads_docs
@pytest.mark.parametrize("claim", RECOVERABLE_SPLIT_CLAIMS, ids=lambda claim: claim.subject)
def test_the_recoverable_claim_says_which_workflow_it_holds_for(claim: Claim) -> None:
    """Refusing the flat sentence is not enough; the split has to be stated.

    Deleting the claim outright would satisfy the refusal above and leave a reader with
    no account of the listing's evidence at all, which is the failure mode a
    negative-only gate always has.
    """
    assert flat(claim.phrase) in flat(section(COMMAND_SURFACE_SECTION)), (
        f"{GUIDANCE_DOCUMENT}'s {COMMAND_SURFACE_SECTION!r} no longer says "
        f"{claim.subject}: the phrase {claim.phrase!r} is gone"
    )


@pytest.mark.reads_docs
def test_the_workflow_split_is_reported_as_one_reproducible_measurement() -> None:
    """A split asserted without the pair of branches it was taken over is an opinion.

    All of it in one paragraph, for the reason the sibling measurement gives: the two
    refs, the change request the remote half landed through, the condition that
    separates them, and what each answered under it. Any of them stated elsewhere in
    this document is a claim a reader cannot pair with the others, and this document is
    long enough that some of them are.
    """
    stamp = SPLIT_MEASUREMENT_STAMP.format(
        date=SPLIT_MEASUREMENT_DATE, release=_adopted("onevcs.version")
    )
    passage = _paragraph_containing(stamp, f"the stamp {stamp!r}")

    for ref in SPLIT_MEASUREMENT_REFS:
        assert ref in passage, (
            f"{GUIDANCE_DOCUMENT}'s paragraph stamped {stamp!r} no longer names {ref}. "
            "The measurement is a comparison: one ref alone is an anecdote about one "
            "workflow rather than evidence that the workflow is what decides"
        )
    assert SPLIT_MEASUREMENT_CHANGE_REQUEST in passage, (
        f"{GUIDANCE_DOCUMENT}'s paragraph stamped {stamp!r} no longer names "
        f"{SPLIT_MEASUREMENT_CHANGE_REQUEST}, the change request the remote half landed "
        "through. Without it the quoted `unknown` reads as uncertainty rather than as "
        "the verb having lost a merge that happened"
    )
    assert flat(SPLIT_MEASUREMENT_CONDITION) in passage, (
        f"{GUIDANCE_DOCUMENT}'s paragraph stamped {stamp!r} no longer states the "
        "condition the two answers were taken under. Both branches answer `a recorded "
        "landing` on this host as it stands; the split only appears with the session "
        "record gone, so a measurement that omits that is not reproducible"
    )
    for answer in SPLIT_MEASUREMENT_ANSWERS:
        assert flat(answer) in passage, (
            f"{GUIDANCE_DOCUMENT}'s paragraph stamped {stamp!r} no longer quotes "
            f"{answer!r}. The verb's own words are the point: a paraphrase cannot be "
            "compared against a fresh run of it"
        )


class PageClaim(NamedTuple):
    """One thing a page under `docs/` has to say, and the phrase that says it."""

    #: The page, repository-relative.
    page: str
    #: What the claim is about, for the failure message and the test id.
    subject: str
    phrase: str


#: The same split, where the two pages that describe these verbs in detail restate it.
#: Held here rather than in a page-specific module because a split stated in `AGENTS.md`
#: and contradicted three pages away is worse than one stated nowhere: a reader who
#: opens the detailed page trusts it over the summary.
SIBLING_PAGE_CLAIMS = (
    PageClaim(
        "docs/orchestration.md",
        "the recoverable listing's evidence depends on the workflow",
        "**but which evidence there is to drop out on depends on the\n"
        "identity's publication workflow.**",
    ),
    PageClaim(
        "docs/orchestration.md",
        "a forgotten remote branch comes back into the listing",
        "comes back into this listing under\n`— may have landed`",
    ),
    PageClaim(
        "docs/repo-lifecycle.md",
        "the per-workflow tier table has its own section",
        "#### What `decided by:` can reach, per workflow",
    ),
    PageClaim(
        "docs/repo-lifecycle.md",
        "a remote landing's branch trailer is never read back",
        "**That trailer is written and never read\n  back**",
    ),
    PageClaim(
        "docs/repo-lifecycle.md",
        "the local squash carries the landing trailer",
        "`Orchestrator-Landed-Commit: <the branch's tip>` trailer under the rules file's",
    ),
)


@pytest.mark.reads_docs
@pytest.mark.parametrize(
    "claim", SIBLING_PAGE_CLAIMS, ids=lambda claim: f"{claim.page}: {claim.subject}"
)
def test_the_detailed_pages_restate_the_same_split(claim: PageClaim) -> None:
    """`AGENTS.md` is the summary; these two pages are what a reader opens next.

    Both describe the verbs whose answers this split governs — `just recoverable` in
    the orchestration page, `just work-status` and the merge strategies in the lifecycle
    page — and both stated the landing evidence without saying that a remote identity
    keeps all of it outside the repository. A page that goes on saying that outranks the
    summary for the reader who went looking for detail.
    """
    assert flat(claim.phrase) in flat(_text(claim.page)), (
        f"{claim.page} no longer says {claim.subject}: the phrase {claim.phrase!r} is "
        "gone. The "
        "per-workflow split has to hold on the detailed page too, or a reader who opens "
        "it reads the flat claim as the authoritative one"
    )
