"""Criteria the two widened detectors are driven over, at every level that drives them.

Each entry is taken verbatim out of `docs/plan-review-refusals.json`, where it cost a
real judged review turn. They live here rather than beside either set of tests because
both levels drive them and neither owns them: `tests/test_criteria_guard.py` calls the
detectors directly, and `tests/plan_tooling/test_check_plan_recipe_e2e.py` drives the
same criteria through `just check-plan`, which is where an operator meets a refusal. A
form added here is therefore exercised at both, which is the property a second copy of
this list would quietly lose — and the gap that copy would leave is the branch nobody
thought to write down twice.

Two of the four tuples are refusals and two are acceptances, and the pairing is the
point rather than an accident of grouping. Every widening here is bounded by the same
measurement the corpus states: no task that passed review is newly refused. So a
detector's branches and the sound criteria of its own shape are read together, and a
widening that bought its catch with a false refusal fails on the tuple beside it.
"""

from __future__ import annotations

#: Every distinct fragment the red-before-green branch matches. All six are classified
#: `newly-caught` in the corpus, and `tests/test_plan_review_refusals.py` holds the
#: fragment each of these is refused on to the fragments the corpus recorded — so a
#: widening that stopped catching its own evidence, or a fixture that drifted off it,
#: fails there rather than in the corpus's prose.
RED_BEFORE_GREEN = (
    "- Each assertion is observed failing for the intended reason before it passes.",
    "- Each new refusal is observed failing for its intended reason before it passes.",
    "- Each new assertion is observed failing against the current implementation before it passes.",
    "- A check fails when the two disagree, and it is observed failing against a"
    " deliberately divergent pair before it passes.",
    "- Each new assertion is left failing for its intended reason before the change that"
    " makes it pass.",
    "- Each new refusal is committed failing for the intended reason before the change"
    " that answers it.",
)

#: The same shape written as the property that step produces, which is the correction
#: the refusal asks for. These pass before this widening and after it — a widening whose
#: cost is a sound criterion is not one worth having. The last two name the demand in
#: prose rather than making it, which this check is written to miss: the node whose job
#: is to document that demand has to be able to say the words.
STATES_THE_PROPERTY_INSTEAD = (
    "- Each new test's subject is the behaviour this change adds, so removing that"
    " behaviour fails it.",
    "- A check fails when `sweep`'s retention and a `recoverable` row disagree about one branch.",
    "- The operational notes ask the worker to make each assertion fail first, and the"
    " criteria state what the finished tree carries.",
    "- `docs/plan-review-refusals.md` names the shape a red-before-green demand takes and"
    " what a criterion says instead.",
)

#: The two publications the out-of-dispatch list was caught missing. `is published` was
#: already on it; these differ from it by an adverb and by the number of the subject,
#: which is the whole of why a judged turn was spent on each. Reconciled against the
#: corpus beside `RED_BEFORE_GREEN`, on the fragment each is refused on.
PUBLISHED_WITH_A_WORD_IN_THE_WAY = (
    "- `config/onepipeline-ui.version` names a release whose PyPI distribution and whose"
    " browser bundle are both published at that version.",
    "- The change lands under a commit subject of a type this repository cuts releases"
    " from, so the release carrying it is actually published.",
)

#: The rest of that matcher's own grammar, which the corpus has no entry for and which a
#: reconciliation against it therefore cannot reach. A publication is a publication
#: whatever copula carries it and whatever stands between that copula and the participle,
#: so the branches no refusal has yet been paid for are driven from a criterion written
#: for each — the alternative to leaving them to the first plan that happens to use one.
#: They are deliberately not part of the corpus-reconciled tuples above: a fixture that
#: claims to be evidence has to be evidence, and these are coverage.
PUBLICATION_COPULAS = (
    "- The wheel that carries this fix was published to the registry.",
    "- Both artifacts of that release were published under one version.",
    "- The sibling release must be published before this pin may move.",
    "- The bundle this pin names has been published at that version.",
    "- The wheel and the bundle are each already published at that version.",
)

#: What that tolerance must not swallow. A negated publication states the worker-side
#: precondition this refusal exists to ask for, and the bare phrase never matched one
#: either — so a widening that started refusing these would be refusing the correction
#: it recommends. The tolerance is written to keep that true: no negation may be one of
#: the words it steps over.
PUBLICATION_IN_PROSE = (
    "- Where a release is not published for it, the finished tree records what holds the"
    " pin and what would bring it due.",
    "- This dispatch publishes nothing and pushes nothing.",
    "- The record names which verb lands a finished branch, and says publication is the"
    " lifecycle's.",
)
