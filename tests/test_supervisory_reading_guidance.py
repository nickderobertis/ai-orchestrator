"""Five passages of `AGENTS.md`, each written from a run that acted before it read.

They are grouped here because they are one habit rather than five rules. A manager
acts on a view, a criterion, a title, or a finding, and in each of these the thing it
acted on was **not the thing it thought it was reading**:

* a watch grepped `just status` for terminal words and matched the `oneagentgraph
  health` JSON that view embeds, reporting a quota death eleven seconds into a healthy
  dispatch — the converse of the watch invariant's own rule 2, and the same cost;
* `just check-plan` refused a read-only node for a criterion that told its worker to
  *read* a tracked file, and the way past it made the criterion vaguer — the outcome
  the passage above it calls worse than the gap it was designed around;
* a change request's subject was derived once, at first publication, while three
  further dispatches changed what its branch did, and under `change-auto` that subject
  becomes the base branch's permanent commit message;
* two supervisory findings were grounded exactly as the rules demand, locally true, and
  globally wrong, each missing a fact one level out;
* declining the second of those on the merits left the criterion behind it standing,
  and the node settled `task-failed` on a green complete gate while its judge's own
  `completion_reason` recorded that every criterion was met.

Each claim is enumerated rather than summarized, because each is a thing a manager
would otherwise relearn by paying for it again — which is how all five were learned the
first time. What can be reconciled against something other than this prose is:
`tests/test_engine_contracts.py::test_the_watch_cuts_the_status_view_where_the_health_report_really_starts`
holds the cut against `onepipeline`'s own status view, and the three tests at the
bottom of this module hold each passage's referent — the sentence it says was paid out,
the drafting graph its asymmetry rests on, and the paragraph it points at instead of
restating.
"""

from __future__ import annotations

import re
from typing import NamedTuple

import pytest

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: The manager's own document. Every passage here is manager judgment rather than
#: worker-facing operational text: arming a watch, validating a plan, reading a change
#: request before it merges, and ruling on a finding are all decisions no dispatch
#: makes, so none of this belongs in `config/dispatch-appendix.md`.
MANAGER = "AGENTS.md"

#: The opening phrase of each passage. A claim is matched inside its own paragraph
#: rather than document-wide: a phrase that survived somewhere else would satisfy a
#: whole-file search while the passage that has to carry it was gone.
WATCH_ITEM = "6. **A grep over the whole of `just status`"
CHECK_PLAN = "**That prediction has since been"
SUBJECT = "**The body is re-derived at every publication"
FINDING = "**A supervisory finding earns a check, never an action**"
DECLINED = "**A finding you decline on the merits"

#: The sentence the `check-plan` passage says has been paid out, which is the
#: prediction directly above it.
PREDICTION = "worse than the gap."
#: The drafting passage the subject passage is the counterpart of, and the graph that
#: makes its "the body is re-derived" half true.
DRAFTING = "A remote lifecycle change request's body is drafted by an agent graph"
DRAFTING_GRAPH = "graphs/pr-author.yaml"
#: The paragraph the declined-finding passage points at rather than restating.
AMENDMENT = "**An amendment is criteria"

#: How the justfile declares a recipe, so a `just <recipe>` these passages name can be
#: read back against the ones this repository has.
RECIPE = re.compile(r"^([a-z][a-z0-9-]*)(?:\s+[^:\n]*)?:", re.MULTILINE)
#: A `just <recipe>` as the prose spells it.
NAMED_RECIPE = re.compile(r"`just ([a-z][a-z0-9-]*)")


class Claim(NamedTuple):
    """One thing a passage has to say, and the phrase that says it."""

    #: What the claim is about, for the failure message and the test id.
    subject: str
    #: The passage that has to carry it.
    passage: str
    phrase: str


#: Every claim these five passages exist to make.
REQUIRED_CLAIMS = (
    # The watch that reported a death that never happened.
    Claim("the view is two documents", WATCH_ITEM, "two documents in one stream"),
    Claim("what the second one is about", WATCH_ITEM, "describes the **host** and not the run"),
    Claim("the words it false-matched on", WATCH_ITEM, '"no_plan_quota"'),
    Claim("what it reported", WATCH_ITEM, "eleven seconds into a healthy dispatch"),
    Claim("the cut", WATCH_ITEM, "sed '/^  providers:/,$d'"),
    Claim("cut once, not per grep", WATCH_ITEM, "in one snapshot the whole watch reads"),
    Claim(
        "the reusable half",
        WATCH_ITEM,
        "a view embedding another tool's report is not a line-oriented document",
    ),
    Claim("why it costs what silence costs", WATCH_ITEM, "the words a real death is reported in"),
    Claim("the HARD REQUIREMENT survives it", WATCH_ITEM, "Rule 5 survives the cut"),
    # The false refusal the passage above it predicted.
    Claim("the prediction was paid out", CHECK_PLAN, "has since been paid out"),
    Claim("what kind of node it refused", CHECK_PLAN, "no repository, no branch"),
    Claim("the file it was refused over", CHECK_PLAN, "`.github/pull_request_template.md`"),
    Claim("what the way past it cost", CHECK_PLAN, "makes the criterion vaguer"),
    Claim("what the worker is left doing", CHECK_PLAN, "following a pointer"),
    Claim("the pairing that really cannot be satisfied", CHECK_PLAN, "verb **changes** a tracked"),
    Claim("the verbs that are the opposite case", CHECK_PLAN, "read, quote, cite, follow the"),
    Claim("what to do with the refusal instead", CHECK_PLAN, "rather than as a wording to soften"),
    # The subject that is derived once and becomes permanent.
    Claim("which artifact is re-derived and which is not", SUBJECT, "the subject is derived once"),
    Claim("how the subject is set", SUBJECT, "passes `--title` on `gh pr create`"),
    Claim("and never revisited", SUBJECT, "no path that edits an open change request"),
    Claim("the change request it happened on", SUBJECT, "change request #127"),
    Claim("what the later dispatches did", SUBJECT, "**reverted the bump**"),
    Claim("what the branch net-diffed to", SUBJECT, "two files, neither a manifest"),
    Claim("why the subject becomes permanent", SUBJECT, "with no `--subject`"),
    Claim("what reads it afterwards", SUBJECT, "`release-plz`"),
    Claim("how close it came", SUBJECT, "about ten minutes to spare"),
    Claim("why the required check did not catch it", SUBJECT, "`pr-title` check **passed**"),
    Claim("the rule it produces", SUBJECT, "against its net diff versus the base"),
    Claim("and what reading the commits hides", SUBJECT, "says the pair cancels"),
    # A finding is evidence, and grounding does not make it right.
    Claim("a finding is checked, never applied", FINDING, "earns a check, never an action"),
    Claim(
        "grounding is necessary and not sufficient",
        FINDING,
        "what makes one worth checking rather than what makes it right",
    ),
    Claim("both were grounded", FINDING, "one quoted a diff, one quoted a criterion"),
    Claim("and both were wrong", FINDING, "locally true and globally wrong"),
    Claim("what the first one missed", FINDING, "clearing a judged-tier finding raised against"),
    Claim("what the second one missed", FINDING, "twelve lines above the pin"),
    Claim("what the checks were", FINDING, "One `git show` of a commit message"),
    Claim("what they cost", FINDING, "under a minute each"),
    Claim("what acting would have cost", FINDING, "undo correct work"),
    Claim("neither was noise", FINDING, "would raise almost nothing"),
    Claim("the asymmetry that decides it", FINDING, "wrong instruction to a live worker costs"),
    # And declining one is evidence about the bar.
    Claim("what a declined finding is evidence about", DECLINED, "evidence about the node's bar"),
    Claim("the criterion it left standing", DECLINED, "changed only if the bump actually requires"),
    Claim("what was wrong with it", DECLINED, "a mechanism the manager preferred"),
    Claim("what it contradicted", DECLINED, "a convention the target"),
    Claim("how long the manager held the information", DECLINED, "Forty minutes later"),
    Claim("how the node settled", DECLINED, "`task-failed` on a green complete"),
    Claim("what its judge recorded", DECLINED, "every acceptance"),
    Claim("which lever cannot fix it", DECLINED, "A `context` note cannot reach that"),
    Claim("which ones can", DECLINED, "a `cancel` plus a `requeue` carrying an `amend`"),
    Claim("and what to do about it", DECLINED, "answer it before the judge does"),
)


def _text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _flat(prose: str) -> str:
    """Collapse every run of whitespace, so a claim may be quoted as one line."""
    return " ".join(prose.split())


def _paragraph(opener: str) -> str:
    """The one paragraph a claim has to be made in, from its opener to the blank line."""
    document = _text(MANAGER)
    assert opener in document, (
        f"{MANAGER} no longer carries a passage opening {opener!r}; a manager reading it "
        "relearns what it says by paying for it again"
    )
    return opener + document.split(opener, 1)[1].split("\n\n", 1)[0]


@pytest.mark.parametrize("claim", REQUIRED_CLAIMS, ids=lambda claim: claim.subject)
def test_the_manager_is_told_what_each_of_these_runs_cost(claim: Claim) -> None:
    """A claim dropped from here is one that reads as taste once its incident is gone.

    These documents argue from measured incidents on purpose: a rule with nothing
    behind it gets weighed against the convenience of ignoring it, and loses.
    """
    assert _flat(claim.phrase) in _flat(_paragraph(claim.passage)), (
        f"{MANAGER}'s passage opening {claim.passage!r} no longer says {claim.subject}: "
        f"the phrase {claim.phrase!r} is gone"
    )


def test_the_refusal_passage_still_sits_under_the_prediction_it_pays_out() -> None:
    """ "That prediction" has to have a prediction directly in front of it.

    The passage is an addendum to the sentence that says the check is written to miss a
    criterion naming its file in prose rather than to refuse a sound plan, "because a
    false refusal blocks correct work and gets worked around". Separated from it, the
    addendum opens on a dangling referent and reads as a complaint about a tool rather
    than as the designed trade-off coming due.
    """
    assert f"{PREDICTION} {CHECK_PLAN}" in _flat(_text(MANAGER)), (
        f"{MANAGER} no longer places the refusal that was paid out immediately after "
        f"{PREDICTION!r}, so it opens on a prediction the reader cannot see"
    )


def test_the_subject_passage_rests_on_a_drafting_graph_this_repository_has() -> None:
    """ "The body is re-derived" is half the asymmetry, and it has to still be true.

    The passage is an argument from contrast: the body is drafted from the branch's own
    diff at every publication, and the subject is not. Take the drafting graph away and
    the contrast is gone — both artifacts would be stale, and the paragraph would be
    telling a manager to check the one field that is no worse than the other.
    """
    drafting = _paragraph(DRAFTING)
    assert DRAFTING_GRAPH in drafting, (
        f"{MANAGER}'s drafting passage no longer names {DRAFTING_GRAPH}, which is what "
        "re-derives a body per publication and so what the subject is contrasted against"
    )
    assert (REPO_ROOT / DRAFTING_GRAPH).exists(), (
        f"{DRAFTING_GRAPH} is gone from this checkout, so no publication drafts a body "
        f"and {MANAGER}'s claim that the body is re-derived at every publication is false"
    )


def test_the_declined_finding_passage_points_at_a_paragraph_above_it() -> None:
    """The pointer resolves, and resolves *upwards*.

    The passage deliberately does not restate why an amendment is criteria — that is
    written once, with its own incident — so what it owes is a paragraph that is still
    there and still above it. A pointer to a paragraph that moved below reads as a
    forward reference to something the manager has not been told yet.
    """
    document = _text(MANAGER)
    assert AMENDMENT in document, (
        f"{MANAGER} no longer carries {AMENDMENT!r}, which the declined-finding passage "
        "points at instead of restating why an amendment is criteria"
    )
    assert document.index(AMENDMENT) < document.index(DECLINED), (
        f"{MANAGER} now places {AMENDMENT!r} below the passage that says 'the amendment "
        "paragraph above', so the pointer names something the reader has not reached"
    )


def test_every_recipe_these_passages_tell_a_manager_to_run_exists() -> None:
    """A passage may name commands this checkout has, and no others.

    Each of these is read in the middle of something going wrong — a watch firing, a
    plan refused, a change request minutes from merging — which is the worst moment to
    discover that the command in front of you is not a recipe here.
    """
    declared = set(RECIPE.findall(_text("justfile")))
    assert declared, "no recipe parsed out of the justfile, so this gate proves nothing"
    for passage in (WATCH_ITEM, CHECK_PLAN, SUBJECT, FINDING, DECLINED):
        for recipe in NAMED_RECIPE.findall(_paragraph(passage)):
            assert recipe in declared, (
                f"{MANAGER}'s passage opening {passage!r} tells a manager to run `just "
                f"{recipe}`, and the justfile declares no such recipe"
            )
