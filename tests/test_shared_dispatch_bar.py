"""The clauses every dispatch shares say what is true of every dispatch, and stop there.

`config/onejudge.base.yaml` is where this host tells a dispatch what "done" means:
`agent.instructions` is the preamble its worker reads, and `user.persona` and
`user.done_when` are the review contract and the completion criterion its judge is
handed. What those two say is said of a plan, a report, and a diff alike — so a demand
that is not true of all three fails the dispatches it is false of, whatever they were
actually asked for. Four nodes with finished, gate-green work were failed that way: by a
demand about *how* a criterion must be proven, which only the criterion knows; and by
one about state that exists only after the dispatch has ended, which no worker can reach
from inside its own run.

So the completion bar is read here twice — once for what it says, and once for the
vocabulary it may not say again. The second is the half that keeps working: a literal to
compare against is trivially updated alongside the file it mirrors, while a re-added
demand still carries the words of the class it belongs to. The judge's own review
contract is read against that same vocabulary, because it carried the same over-reach
and is handed to the same model in the same turn.

Nothing here was dropped rather than moved: what those demands carried is now each
node's own `## Acceptance criteria` to state, which `AGENTS.md` gives the plan's author.
One tail is deliberately kept, and `config/onejudge.base.yaml` records beside the clause
why it is not the next thing to remove.

The preamble half below is a different guard on the same file: a dispatch with no
tracked change is told the truth, and one that changed something is not let off.

`tests/e2e/test_orchestrate_launch_e2e.py` proves what this file cannot — that these
clauses are what a real launch hands a real worker and its real judge — from a launch
with only the paid model doubled. This file reads the file itself, because what is under
test here is what the words demand, not whether they arrive.
"""

from __future__ import annotations

import re

import pytest
from shared_dispatch_bar import (
    BASE_CONFIG,
    shared_agent_preamble,
    shared_completion_bar,
    shared_judge_persona,
)

#: The whole shared completion bar, so that changing it is a change to this file too.
#: What is left of it is the task's own criteria plus one clause about the tree the
#: dispatch leaves behind — nothing about how a criterion is proven, and nothing a
#: dispatch cannot settle before it ends.
SHARED_COMPLETION_BAR = (
    "every acceptance criterion stated in the task is met, with every change this "
    "dispatch made committed and nothing half-applied left behind"
)

#: Word stems no clause handed to every judge alike may use, each the surface of one of
#: the two failures above. A proof-method or check-tier word makes the shared clause
#: decide how a criterion is met, which only that criterion knows and which already
#: refused an argument from a repository's own contents. A word naming work that lands
#: after the dispatch points at state no worker can observe while it is still the one
#: that would have to satisfy it.
UNSHAREABLE = (
    "inspection",
    "verification",
    "gate",
    "check",
    "test",
    "lint",
    "coverage",
    "remote",
    "push",
    "pull request",
    "change request",
    "CI",
    "merge",
    "deploy",
    "publish",
)


def _uses(clause: str, stem: str) -> bool:
    """Whether `clause` uses `stem` as a word, in any inflection.

    Matched on a word boundary and an open suffix rather than as a substring: "check"
    has to catch "checks" and "CI" must not be found inside "decision", and a stem
    search that got either wrong would fail a clause that is fine or pass one that is
    not.
    """
    return re.search(rf"\b{re.escape(stem)}\w*", clause, re.IGNORECASE) is not None


def test_the_shared_completion_bar_is_the_tasks_criteria_and_the_commit_tail() -> None:
    """The one bar every dispatch is judged against, in full.

    Read as equality rather than as containment: what makes this clause correct is as
    much what it omits as what it states, and a containment check accepts every demand
    a later edit appends to it.
    """
    assert shared_completion_bar() == SHARED_COMPLETION_BAR, (
        f"{BASE_CONFIG}'s `user.done_when` is no longer the one clause every dispatch "
        "here is judged against; it may only state what is true of a plan, a report, "
        f"and a diff alike:\n{shared_completion_bar()}"
    )


@pytest.mark.parametrize("stem", UNSHAREABLE)
def test_the_shared_completion_bar_demands_nothing_only_some_dispatches_can_meet(
    stem: str,
) -> None:
    """The reduction stated as the class of demand it removed, not as one wording of it.

    This is what survives an edit that changes the literal above and the file together —
    the way a demand comes back is worded fresh, so it is caught by the vocabulary it
    cannot avoid rather than by the sentence it replaced.
    """
    bar = shared_completion_bar()
    assert not _uses(bar, stem), (
        f"{BASE_CONFIG}'s `user.done_when` uses {stem!r}, so the one clause every "
        "dispatch shares decides how a criterion is proven or points at state that "
        f"arrives after the dispatch ends; state it in that node's own "
        f"`## Acceptance criteria` instead:\n{bar}"
    )


@pytest.mark.parametrize("stem", UNSHAREABLE)
def test_the_judge_persona_default_adds_no_demand_of_its_own(stem: str) -> None:
    """The other clause the judge is handed defers to the criteria too.

    `user.done_when` was never the only place a check demand was made of every dispatch
    alike: the default review contract required "the repository's documented checks"
    independently of the criteria, so reducing the bar alone would have left the judge
    told twice to fail work its task never asked to gate — and this clause is the half
    with no `## Acceptance criteria` beside it to be read against.
    """
    persona = shared_judge_persona()
    assert not _uses(persona, stem), (
        f"{BASE_CONFIG}'s `user.persona` uses {stem!r}, so the default review contract "
        "demands something of every dispatch alongside its criteria rather than "
        f"reviewing against them:\n{persona}"
    )


def test_the_judge_persona_default_reviews_against_the_acceptance_criteria() -> None:
    """Adding no demand of its own is only half of it; it must still name the bar.

    A contract emptied instead of narrowed would pass every parametrization above and
    leave a dispatch with no persona reviewed against nothing at all.
    """
    persona = shared_judge_persona()
    assert "acceptance criteria" in persona.lower(), (
        f"{BASE_CONFIG}'s `user.persona` no longer points the judge at the task's "
        f"acceptance criteria, which is the only bar it may review against:\n{persona}"
    )


#: The two sentences of the shared preamble that say what a dispatch with no tracked
#: change does instead, each located by the condition it hangs on. Naming the condition
#: rather than the sentence is what keeps these from becoming a copy of the prose: a
#: sentence that stopped naming its condition would be an unguarded permission, and is
#: not found here.
NO_TRACKED_CHANGE_ALLOWANCES = (
    "If you changed nothing tracked in the repository",
    "when you touched none",
)

#: What the preamble demands of a dispatch that did change something, with every
#: allowance above struck out. Each of these was there before the allowances were, and
#: none of them may become reachable through one.
DEMANDS_OF_A_DISPATCH_THAT_CHANGED_SOMETHING = (
    "run the project's complete verification exactly once at closeout and clear any "
    "findings it reports",
    "Work is not done until that run is green",
    "bypassing the project's pre-commit or pre-push verification is not an acceptable "
    "response to a slow or failing check",
    "Never rely on the harness to commit for you or leave finished work uncommitted",
)


def _preamble_sentence_allowing(condition: str) -> str:
    """The one sentence of the shared preamble that hangs on a no-change condition."""
    flowing = " ".join(shared_agent_preamble().split())
    parts = flowing.split(". ")
    sentences = [
        part if index == len(parts) - 1 else f"{part}." for index, part in enumerate(parts)
    ]
    allowing = [sentence for sentence in sentences if condition in sentence]
    assert len(allowing) == 1, (
        f"{BASE_CONFIG}'s `agent.instructions` states {len(allowing)} sentence(s) hanging on "
        f"{condition!r}; exactly one is what makes an allowance for a dispatch that changed "
        "nothing separable from what every dispatch is told"
    )
    return allowing[0]


def _preamble_binding_on_a_dispatch_that_changed_something() -> str:
    """The shared preamble with every no-tracked-change allowance struck out.

    Whole sentences, so an allowance that smuggled a relaxation in beside itself goes
    with it: what is left is what a dispatch that changed code reads, and it has to be
    the whole standing bar on its own.
    """
    binding = " ".join(shared_agent_preamble().split())
    for condition in NO_TRACKED_CHANGE_ALLOWANCES:
        binding = binding.replace(_preamble_sentence_allowing(condition), "")
    return binding


def test_the_preamble_still_holds_a_dispatch_that_changed_something_to_the_whole_bar() -> None:
    """No allowance for a document-producing dispatch is reachable by one that changed code.

    Demonstrated by removing every sentence that hangs on having changed nothing and
    reading what is left, which is what a dispatch that changed something is bound by.
    This excludes the rewording that reads as permission — moving the closeout
    verification, "work is not done until that run is green", the refusal to bypass a
    pre-commit or pre-push gate, or "never leave finished work uncommitted" inside a
    sentence that a dispatch with a diff could also apply to itself, or softening any of
    them into something optional for everyone.
    """
    binding = _preamble_binding_on_a_dispatch_that_changed_something()
    for demand in DEMANDS_OF_A_DISPATCH_THAT_CHANGED_SOMETHING:
        assert demand in binding, (
            f"{BASE_CONFIG}'s `agent.instructions` no longer demands {demand!r} of a dispatch "
            f"that changed something; it survives only inside an allowance for one that did "
            f"not, or is gone:\n{binding}"
        )


def test_the_preamble_tells_a_dispatch_with_no_tracked_change_something_true() -> None:
    """The allowances exist, and each says what IS true of a document-producing dispatch.

    The other direction of the same guard: an allowance that named its condition but
    demanded the gate anyway would leave the preamble as false for that dispatch as it
    was before. So each sentence is read back for what it permits — closing out with no
    verification over a tree that does not exist, and a clean `git status` being a
    complete outcome rather than a reason to author a file nobody asked for.
    """
    closeout = _preamble_sentence_allowing(NO_TRACKED_CHANGE_ALLOWANCES[0])
    assert "complete without it" in closeout, (
        f"{BASE_CONFIG}'s `agent.instructions` names the no-tracked-change condition at "
        f"closeout without saying such a dispatch is complete:\n{closeout}"
    )
    assert "never because running it is slow or inconvenient" in closeout, (
        f"{BASE_CONFIG}'s `agent.instructions` lets closeout be skipped without saying what "
        f"may not decide it, so any dispatch can reach the allowance:\n{closeout}"
    )
    committing = _preamble_sentence_allowing(NO_TRACKED_CHANGE_ALLOWANCES[1])
    assert "correct and complete outcome" in committing, (
        f"{BASE_CONFIG}'s `agent.instructions` no longer tells a dispatch that touched no "
        f"tracked file that having nothing to commit is a correct outcome:\n{committing}"
    )
