"""One suppression policy, in the one file a worker and its judge both read.

Three sources stated this and no two agreed, and a dispatch that followed one of them
had correct work failed by a judge that had read another. So the policy lives in
`config/dispatch-appendix.md`, the only copy handed to a worker and its judge together,
and `AGENTS.md` points at it instead of restating it.

The second half is what needs a gate: a document asked to stop stating something states
it again the moment somebody writes the obvious sentence. What is checked is the
vocabulary a *statement* of this policy cannot avoid — in the shape
`tests/test_shared_dispatch_bar.py` holds two clauses to one vocabulary — and two
paragraphs are run back through it below, so the check is proven to discriminate rather
than trusted to.

The vocabulary is the requirement rather than terms lifted from the superseded
paragraph, which would only have gated a revert: the sentence that got past that first
attempt shared none of those terms. `substantive reason` is the condition the appendix's
permission turns on, so no statement of the policy reaches a reader without it and a
pointer never needs it.
"""

from __future__ import annotations

import re
from typing import NamedTuple

import pytest

from orchestrator.criteria_guard import APPENDIX
from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: The document that must point rather than state.
GUIDANCE_DOCUMENT = "AGENTS.md"


class Part(NamedTuple):
    """One part of the suppression policy the appendix has to state."""

    #: What this part is, for the test id and the failure message.
    name: str
    #: The load-bearing words that say it, rather than the sentence around them.
    stated_by: re.Pattern[str]
    #: What it says, phrased for the reader of a failure who has to put it back.
    means: str


#: The four parts of the policy, each held on its load-bearing words rather than on a
#: sentence, so a reword is free and a dropped part is not. `\s+` between words because
#: the appendix is hard-wrapped and a phrase routinely straddles two lines.
POLICY = (
    Part(
        "the permission",
        re.compile(r"genuinely\s+misapplied\s+at\s+that\s+site", re.IGNORECASE),
        "a site-scoped `ignore` is permitted where the rule is genuinely misapplied there",
    ),
    Part(
        "the reason it must carry",
        re.compile(r"carries\s+a\s+substantive\s+reason", re.IGNORECASE),
        "that permission holds only for a directive carrying a substantive reason",
    ),
    Part(
        "the prohibition",
        re.compile(r"silencing\s+a\s+finding\s+you\s+have\s+not\s+answered", re.IGNORECASE),
        "what is forbidden is silencing an unanswered finding so that a count moves",
    ),
    Part(
        "the disclosure",
        re.compile(
            r"[Ee]very\s+suppression\s+standing\s+in\s+the\s+finished\s+tree\s+is\s+listed",
            re.IGNORECASE,
        ),
        "every suppression left in the finished tree is listed in the completion report",
    ),
)

#: The words a *statement* of this policy cannot avoid, as opposed to a pointer at one,
#: as `(label, the pattern that finds it)`. Patterns rather than substrings because
#: `AGENTS.md` is hard-wrapped and the phrase that has to be caught most is the one a
#: line break runs through: the restatement this last caught read "a substantive\nreason,
#: at the site", which a substring check walks straight past.
#:
#: The first four are lifted from the paragraph this replaced — the two scopes a
#: permission has to name, and the two halves of the ruling that contradicted it. Those
#: four alone were a gate against a *revert* rather than against a restatement, which is
#: the narrower thing: they recognise the old sentence and nothing else, so a fresh
#: sentence saying the same policy in new words passed. `substantive reason` is the term
#: that closes it, and it closes it generally rather than by naming one wording — it is
#: the requirement itself, the condition the appendix's own permission turns on, so no
#: statement of this policy reaches a reader without it and no pointer at one needs it.
POLICY_VOCABULARY = (
    ("site-scoped", re.compile(r"site-scoped", re.IGNORECASE)),
    ("line-scoped", re.compile(r"line-scoped", re.IGNORECASE)),
    ("the manager's call", re.compile(r"the\s+manager's\s+call", re.IGNORECASE)),
    ("never the worker's", re.compile(r"never\s+the\s+worker's", re.IGNORECASE)),
    ("substantive reason", re.compile(r"substantive\s+reason", re.IGNORECASE)),
)

#: What the pointer has to name. The path rather than the word "appendix", because a
#: reader following this is opening a file.
ONE_SOURCE = "config/dispatch-appendix.md"


def _document(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


def states_policy(prose: str) -> list[str]:
    """Every policy-vocabulary term ``prose`` uses.

    Takes the prose rather than reading a file, so the two paragraphs kept below can be
    run through the same check — which is the only way to know this discriminates.
    """
    return [label for label, pattern in POLICY_VOCABULARY if pattern.search(prose)]


@pytest.mark.parametrize("part", POLICY, ids=lambda row: row.name)
def test_the_appendix_states_the_whole_suppression_policy(part: Part) -> None:
    """All four parts, in the file every dispatched task carries.

    Parametrized rather than asserted together, because the parts fail differently: a
    permission with no reason attached is a licence, a prohibition with no permission
    beside it is the categorical instruction that failed a node, and a policy nobody has
    to disclose leaves a manager reading a green tree with no idea what is silenced in
    it.
    """
    appendix = _document(str(APPENDIX))
    assert part.stated_by.search(appendix), (
        f"{APPENDIX} no longer says {part.means}. It is the one source of this policy, "
        "and a part missing from it is a part a worker and its judge will each supply "
        "for themselves"
    )


def test_the_guidance_document_states_no_suppression_policy_of_its_own() -> None:
    """`AGENTS.md` points instead of restating, held on the vocabulary of a restatement.

    A second statement of this policy does not have to disagree with the first to do the
    damage: it only has to be reachable by one reader and not the other. So what is
    refused is any wording that decides the question here, whichever way it decides it.
    """
    used = states_policy(_document(GUIDANCE_DOCUMENT))
    assert not used, (
        f"{GUIDANCE_DOCUMENT} states suppression policy in its own words again "
        f"({', '.join(used)}), so a worker and its judge can read two sources and get "
        f"two answers. State it in {ONE_SOURCE}, which is the copy a dispatched task "
        "carries, and point at it here"
    )


def test_the_guidance_document_points_at_the_one_source() -> None:
    """Saying nothing is only half of it: a reader has to be sent somewhere.

    Dropping the subject entirely would pass the check above and leave the question
    unanswered in the document a manager reads, which is how it came to be answered
    three times in the first place.
    """
    prose = _document(GUIDANCE_DOCUMENT)
    mentions = [
        paragraph
        for paragraph in prose.split("\n\n")
        if re.search(r"\bsuppress\w*", paragraph, re.IGNORECASE)
    ]
    assert mentions, (
        f"{GUIDANCE_DOCUMENT} no longer mentions suppression at all, so a manager "
        f"reading it has no reason to look in {ONE_SOURCE} for the policy"
    )
    for paragraph in mentions:
        assert ONE_SOURCE in paragraph, (
            f"{GUIDANCE_DOCUMENT} raises suppression in a paragraph that does not name "
            f"{ONE_SOURCE}, so it opens the question here and answers it nowhere:\n"
            f"{paragraph}"
        )


#: The paragraph this replaced, verbatim as `AGENTS.md` carried it. Kept so the check
#: above is proven against the text it was written for rather than against a synthetic
#: example: a vocabulary that no longer recognised the real wording would pass over the
#: rewrite and over a straight revert alike.
SUPERSEDED_POLICY_PARAGRAPH = (
    "`llmlint.yml` is a legitimate deliverable when a task names it; otherwise a worker\n"
    "fixes the code or adds a justified site-scoped `ignore` directive, and reports a\n"
    "rule that looks wrong or misapplied instead of editing it. Deciding when a marginal\n"
    "finding stops being worth another gate cycle—landing with a justified line-scoped\n"
    "suppression plus a tracked follow-up—is the manager's call from that surfaced\n"
    "report, never the worker's by suppressing.\n"
)


#: The sentence this last caught, verbatim as `AGENTS.md` carried it. It is not the
#: superseded paragraph returning — it was written *after* the rewrite, by an author who
#: had read it, about a different subject entirely: the `<!-- dated-claim: incident -->`
#: marker, which is a suppression and so raised the question of what a suppression owes.
#: Pointing at the one source was not enough for it; it went on to say what would be
#: found there, and saying that is what put a second copy of the requirement in the
#: document that had just been emptied of one.
#:
#: Kept because it is the real shape of the failure this gate is for. The superseded
#: paragraph is what a revert looks like and the four terms lifted from it caught that;
#: this is what a restatement looks like, it shares not one of those four terms, and it
#: stood in `AGENTS.md` through a green run of this file.
RESTATED_POLICY_SENTENCE = (
    "that. The marker is a suppression and is held to what\n"
    "[`config/dispatch-appendix.md`](config/dispatch-appendix.md) holds one to: a substantive\n"
    "reason, at the site, that a reader sees beside the claim it excuses.\n"
)


def test_the_check_refuses_a_restatement_that_is_not_the_old_paragraph() -> None:
    """A pointer that goes on to say what will be found is a second copy of the policy.

    Three properties, and the middle one is why this test exists rather than a line
    added to the one below. The sentence *is* refused. It is refused by a term the
    superseded paragraph does not carry, so the vocabulary is answering the policy
    rather than remembering one wording. And it names the one source while doing it —
    so the pointer check passes it, and this is the only thing standing between a
    document that points and a document that points and then answers anyway.
    """
    used = states_policy(RESTATED_POLICY_SENTENCE)

    assert used, (
        f"{GUIDANCE_DOCUMENT} could carry this sentence again and pass:\n{RESTATED_POLICY_SENTENCE}"
    )
    assert not set(used) & set(states_policy(SUPERSEDED_POLICY_PARAGRAPH)), (
        "this restatement is only refused by a term the superseded paragraph also uses, "
        "so the gate is recognising that one paragraph rather than the policy; every "
        f"term it fires on here is one of those: {used}"
    )
    assert ONE_SOURCE in RESTATED_POLICY_SENTENCE, (
        "this sentence does not name the one source, so the pointer check would have "
        "caught it and it proves nothing about what that check misses"
    )


def test_the_check_refuses_the_paragraph_it_replaced() -> None:
    """The gate discriminates the rewrite from what stood before it.

    Both of the superseded paragraph's halves are refused, and separately: it permitted
    a suppression and then withheld the permission, and a check that caught only one of
    those would pass a document that had kept the other.
    """
    used = states_policy(SUPERSEDED_POLICY_PARAGRAPH)

    assert "site-scoped" in used and "line-scoped" in used, (
        "the permitting half of the superseded paragraph is no longer recognised as a "
        f"statement of policy, so this gate would pass it back into {GUIDANCE_DOCUMENT}:"
        f"\n{used}"
    )
    assert "the manager's call" in used and "never the worker's" in used, (
        "the withholding half of the superseded paragraph is no longer recognised, so "
        "the ruling that contradicted the permission could return unnoticed:\n"
        f"{used}"
    )
    assert ONE_SOURCE not in SUPERSEDED_POLICY_PARAGRAPH, (
        "the superseded paragraph names the one source, so the pointer check above "
        "would have passed it and proves nothing about the rewrite"
    )
