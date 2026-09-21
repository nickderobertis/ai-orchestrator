"""Every date in `AGENTS.md` names the check that re-takes it, or says it is history.

A dated measurement of somebody else's software reads as current for as long as it
stands, so the stamp is what makes it dangerous rather than what makes it trustworthy.
The rule `AGENTS.md` states beside this is what is held here: a paragraph carrying an
ISO date either names a test under `tests/` that re-takes it, or classifies the date as
a historical incident in a `<!-- dated-claim: incident <reason> -->` marker.

Every date is caught rather than only the ones written in a recognised frame. Two
narrower detectors both shipped holes — one wanted a measuring verb and missed *"held
one plan at a time until <date>"*; widening to bounded states still passed *"On <date>,
onevcs returned X"* — and English has no closed vocabulary for asserting something, so
the judgment moved from the regex to the author's own classification.

What this cannot do is judge the test a paragraph names: it refuses a paragraph naming
none, and enumerating claim-to-test pairs would only be an allowlist. That gap is the
one left.
"""

from __future__ import annotations

import re
from typing import NamedTuple

import pytest

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: The document under this rule. `AGENTS.md` rather than everything under `docs/`,
#: because this is the file a manager reads mid-run and acts on without going looking
#: for a second opinion — and because `CLAUDE.md` is a symlink to it, so an agent
#: session reads it too.
GUIDANCE_DOCUMENT = "AGENTS.md"

#: What this gate is about: a date. ISO only, which is how this document writes every
#: one of them; a looser shape ("in August") would match prose that dates nothing.
#:
#: Nothing narrows it further, and that is the whole design. Two successive attempts to
#: recognise *which* dated sentences are claims both shipped holes, and the second hole
#: was invisible until somebody went looking — so the date is the trigger and the author
#: says what it is.
DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b")

#: What a paragraph has to name: a path under `tests/` ending `.py`. Checked for
#: existence as well as shape, so a renamed or deleted module fails here rather than
#: leaving a pointer to a check nobody runs.
NAMES_A_TEST = re.compile(r"tests/[\w./-]+\.py")

#: The other way out: the author classifying the date as stamping an event rather than
#: asserting current behaviour. An HTML comment because `AGENTS.md` already carries its
#: `llmlint` suppressions that way, so this is the file's own idiom for "a rule does not
#: apply here, and here is why" rather than a second one invented beside it.
INCIDENT = re.compile(r"<!--\s*dated-claim:\s*incident\s+(?P<reason>[^>]*?)\s*-->")

#: How much reason is a reason. A marker is a suppression, and this repository's own
#: policy for one is that it carries a substantive reason — so a bare `<!-- dated-claim:
#: incident -->`, or a one-word gesture at one, is refused exactly as an unexplained
#: `llmlint: ignore` would be. The bound is a floor on length rather than a judgment of
#: content, which is all a check can do; the judge of the content is the reviewer who
#: reads it next to the claim.
SUBSTANTIVE_REASON = 24


class Frame(NamedTuple):
    """One vocabulary this gate used to decide a dated sentence by, and no longer does."""

    #: What that vocabulary recognised, for the failure message.
    name: str
    #: How it recognised it.
    recognises: re.Pattern[str]


#: The two frames the detector used to decide this by, kept for the regressions alone.
#: They decide nothing now. What they are still good for is proving that the rule reaches
#: past them: the plain dated assertion below matches neither, and has to be refused
#: anyway, which is the property that would be lost if this ever narrowed back to a
#: vocabulary.
FORMER_FRAMES = (
    Frame(
        "a measuring verb",
        re.compile(
            r"\b(?:re-measured|measured|re-driven|driven|re-read|re-taken|re-drove"
            r"|bisected|probed)\b",
            re.IGNORECASE,
        ),
    ),
    Frame(
        "a bounded state",
        re.compile(
            r"\b(?:until|since|as of|from|before|after|up to|through|ending)\s+(?:the\s+)?"
            rf"(?={DATE.pattern})",
            re.IGNORECASE,
        ),
    ),
)

#: Where one sentence ends, for a document that hard-wraps and uses `.` inside version
#: numbers and file names. Split on a terminator followed by a space, having flattened
#: the paragraph first: a version number is never followed by a space inside itself,
#: and the two-space case a paragraph break would produce is gone by flattening.
SENTENCE_END = re.compile(r"(?<=[.;]) ")


def dated_sentences(paragraph: str) -> list[str]:
    """Every sentence of ``paragraph`` carrying an ISO date.

    Flattened before splitting, because this document hard-wraps: a sentence that
    carries its date on one line and its subject on the next is one sentence to a reader
    and would be two to a line-oriented split.
    """
    flat = " ".join(paragraph.split())
    return [sentence for sentence in SENTENCE_END.split(flat) if DATE.search(sentence)]


def retaking_tests(paragraph: str) -> list[str]:
    """Every test path ``paragraph`` names that exists in this checkout."""
    return [named for named in NAMES_A_TEST.findall(paragraph) if (REPO_ROOT / named).is_file()]


def incident_reasons(paragraph: str) -> list[str]:
    """Every substantive reason ``paragraph``'s incident markers carry.

    A marker whose reason is missing or too short is not one: it excuses the claim from
    the rule without saying anything a reader could disagree with, which is the escape
    hatch this gate exists to not have.
    """
    return [
        found["reason"]
        for found in INCIDENT.finditer(paragraph)
        if len(found["reason"].strip()) >= SUBSTANTIVE_REASON
    ]


class Unaccompanied(NamedTuple):
    """One dated sentence its paragraph neither gates nor classifies."""

    #: The paragraph it stands in, so a caller can say where the answer belongs.
    paragraph: str
    #: The sentence itself, which is what a failure names: the fix is to that sentence.
    sentence: str


def unaccompanied(document: str) -> list[Unaccompanied]:
    """Each dated sentence in ``document`` with neither a re-taking test nor a reason.

    Takes the document rather than reading it, so the same rule can be applied to prose
    this suite composes — which is how the regressions below prove the gate discriminates
    without waiting for somebody to write a bad paragraph.
    """
    offending: list[Unaccompanied] = []
    for paragraph in document.split("\n\n"):
        if retaking_tests(paragraph) or incident_reasons(paragraph):
            continue
        offending.extend(
            Unaccompanied(paragraph, sentence) for sentence in dated_sentences(paragraph)
        )
    return offending


def test_every_dated_sentence_is_gated_or_classified() -> None:
    """The rule, over the document it governs.

    Reported with the offending sentence rather than with a paragraph index, because the
    fix is to that sentence: name the check that would fail when the release behind it
    moves, classify it as an incident and say why, or take the dated assertion out.
    """
    offending = unaccompanied((REPO_ROOT / GUIDANCE_DOCUMENT).read_text(encoding="utf-8"))
    assert not offending, (
        f"{GUIDANCE_DOCUMENT} carries {len(offending)} dated sentence(s) whose paragraph "
        "neither names a test that re-takes them nor classifies the date as an incident. "
        "Name the check that fails when the release behind it moves, or write "
        "`<!-- dated-claim: incident <reason> -->` in that paragraph saying why the date "
        "stamps something that happened rather than something that is still true:\n"
        + "\n".join(f"  - {found.sentence}" for found in offending)
    )


#: A reading: a measuring verb beside a date, which is the shape the first detector was
#: written for. Kept verbatim as `AGENTS.md` carried it before this gate existed.
SUPERSEDED_READING_CLAIM = (
    "**The destroying half of that incident is now caught, and the rule stands\n"
    "regardless.** onevcs 0.11.1 made session close look for commits the clone holds that\n"
    "the session's branch does not, and onevcs 0.15.4 — the release pinned at\n"
    "the time — was re-driven on 2026-08-25 against the\n"
    "incident's exact shape to check it."
)

#: A bounded state: no measuring verb anywhere in it, and an assertion about how two
#: external tools behave today. Verbatim as `AGENTS.md` carried it while the gate, then
#: reading only the verb list, passed over it.
SUPERSEDED_BOUNDED_STATE_CLAIM = (
    "**That board held one plan at a time until 2026-08-29, and the retreat that worked "
    "around it\n"
    "is over.** Two defects made a second plan on the board corrupt every plan on it, "
    "and both\n"
    "were repaired upstream and adopted here rather than worked around again:"
)

#: A plain dated assertion of tool behaviour, in neither frame: no measuring verb, and a
#: date that stamps an event rather than bounding a state — the shape the *widened* gate
#: still passed, and the one that says a vocabulary was never going to be enough.
PLAIN_DATED_TOOL_CLAIM = (
    "On 2026-08-29, onevcs returned `landed: yes` for every branch this host holds,\n"
    "so a manager can stop asking after a settled node."
)


@pytest.mark.parametrize(
    "claim",
    (SUPERSEDED_READING_CLAIM, SUPERSEDED_BOUNDED_STATE_CLAIM, PLAIN_DATED_TOOL_CLAIM),
    ids=("a reading", "a bounded state", "a plain dated assertion"),
)
def test_the_rule_refuses_a_dated_claim_in_any_shape(claim: str) -> None:
    """All three shapes, refused by one rule rather than by three recognisers.

    The first two are the holes this gate shipped with, kept as the text that really
    stood in `AGENTS.md`. The third is the hole a *list* of shapes always leaves: it
    matches neither former frame, and under either of them it passed.
    """
    offending = unaccompanied(claim)

    assert len(offending) == 1, (
        "a dated claim about an external tool is no longer detected, so the gate over "
        f"{GUIDANCE_DOCUMENT} would pass whatever it carries:\n{claim}"
    )


def test_the_rule_reaches_past_the_frames_it_used_to_decide_by() -> None:
    """The exhaustiveness property, stated as the thing a narrowing would break.

    This is the regression that matters most, because the failure it guards is not a
    missing pattern but a returning idea: that *which* dated sentences are claims can be
    decided by recognising how they are written. The claim below is refused while
    matching neither vocabulary the gate has ever used, so a later edit that reinstated
    either as the trigger fails here — where a test that only asserted "this is refused"
    would pass under a third frame written to admit exactly this sentence.
    """
    for frame in FORMER_FRAMES:
        assert not frame.recognises.search(" ".join(PLAIN_DATED_TOOL_CLAIM.split())), (
            f"the plain dated assertion now matches {frame.name}, so it no longer proves the "
            "rule reaches past the frames; pick a claim neither vocabulary can see:\n"
            f"{PLAIN_DATED_TOOL_CLAIM}"
        )
    assert unaccompanied(PLAIN_DATED_TOOL_CLAIM), (
        "a dated assertion of tool behaviour in neither former frame passed, so the gate "
        "has narrowed back to recognising shapes and the third hole is open again:\n"
        f"{PLAIN_DATED_TOOL_CLAIM}"
    )


def test_a_claim_that_names_its_re_taking_test_is_accepted() -> None:
    """The first remedy, so the gate is not simply refusing every date.

    A real test named in the paragraph passes, which is what says the remedy the failure
    message offers is one an author can actually apply.
    """
    accompanied = (
        SUPERSEDED_READING_CLAIM + " tests/e2e/test_worktree_pool_e2e.py is what re-takes it."
    )

    assert not unaccompanied(accompanied), (
        "a dated claim naming an existing test is still refused, so the rule has no "
        "remedy and an author can only satisfy it by deleting prose"
    )


def test_a_named_test_that_does_not_exist_does_not_satisfy_the_rule() -> None:
    """A pointer at a module nobody has written is not a check.

    The likeliest way this gate rots is a test being renamed or deleted while the
    paragraph that cites it stands — after which the prose reads as gated and is not.
    """
    dangling = (
        SUPERSEDED_READING_CLAIM
        + " tests/e2e/test_a_journey_nobody_wrote_e2e.py is what re-takes it."
    )

    assert unaccompanied(dangling), (
        "a paragraph naming a test path that does not exist satisfied the rule, so a "
        "renamed or deleted module leaves the claim reading as gated while nothing "
        "re-takes it"
    )


#: A dated event with no bearing on how any tool behaves now — the shape that must stay
#: writable, because this document records incidents by their date constantly and none of
#: them goes stale.
INCIDENT_NARRATIVE = (
    "Two workers reached that place from different directions on 2026-08-24, while\n"
    "another manager's dispatch had been live over an hour. One ran `pkill -TERM -x just`."
)


def test_an_incident_is_written_by_classifying_it_rather_than_by_being_recognised() -> None:
    """The second remedy: the author says it is history, and a reader can see them say it.

    Under the vocabulary this replaced, a narrative like the one below passed because it
    carried no measuring verb — which is the same reason two real claims passed. So it is
    refused by default now and admitted by a marker, and the difference between the two
    states is one visible sentence rather than an invisible property of a regex.
    """
    assert unaccompanied(INCIDENT_NARRATIVE), (
        "a dated sentence passed with nothing in its paragraph saying what the date is, "
        "so the gate is deciding by recognition again rather than by classification"
    )

    classified = (
        INCIDENT_NARRATIVE
        + "\n<!-- dated-claim: incident what two workers did on this host on one night; the"
        " signalling rule they broke is stated undated above -->"
    )
    assert not unaccompanied(classified), (
        "a classified incident is still refused, so the only way to keep a dated "
        "forensic record is to delete its date"
    )


@pytest.mark.parametrize(
    "marker",
    (
        "<!-- dated-claim: incident -->",
        "<!-- dated-claim: incident history -->",
        "<!-- dated-claim: incident old news -->",
    ),
    ids=("no reason", "one word", "a gesture"),
)
def test_a_classification_with_no_substantive_reason_is_not_one(marker: str) -> None:
    """The marker is a suppression, so it is held to what this repository holds one to.

    `config/dispatch-appendix.md` is the one source of that policy and it is the same
    sentence: a directive carries a substantive reason saying why the rule is misapplied
    at that site. A marker that only names the rule it silences is the bare `ignore` that
    policy refuses, and it would turn this gate into an escape hatch reachable under
    exactly the time pressure that produces a stale claim.
    """
    assert unaccompanied(f"{INCIDENT_NARRATIVE}\n{marker}"), (
        f"{marker!r} excused a dated claim while saying nothing a reader could disagree "
        "with, so the classification is a hatch rather than a statement"
    )
