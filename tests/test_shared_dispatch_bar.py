"""A dispatch that changed nothing is told the truth; one that changed something is not let off.

Both shared clauses in `config/onejudge.base.yaml` used to demand a green gate over a
finished tree and committed work of *every* dispatch alike. A dispatch whose deliverable
is a plan, a report, or an answer authors no tracked change and runs no gate, so its
judge was handed a criterion it could never meet and its worker was told to do something
there was nothing to do. Both are now guarded on the dispatch having left a tracked
change.

That guard is the one way this could be made worse rather than better, so it is not
merely asserted to exist here. Each test below applies the clause to one of the two
dispatch shapes and reads back what that shape is actually held to: the changed-tree
shape must still be held to every obligation the unguarded clause carried, and the
no-change shape must still be held to a real bar rather than to nothing.

`tests/e2e/test_orchestrate_launch_e2e.py` proves the other half — that these clauses
are what a real launch hands a real worker and its real judge — from a launch with only
the paid model doubled. This file reads the file itself, because what is under test here
is what the words demand, not whether they arrive.
"""

from __future__ import annotations

from shared_dispatch_bar import BASE_CONFIG, shared_agent_preamble, shared_completion_bar

#: Where the completion bar stops speaking of every dispatch and starts speaking only of
#: one that changed something. Finding it is load-bearing: a rewrite that dropped the
#: guard and hedged the obligation instead — "green over any change it made", which a
#: worker satisfies by asserting it made none — has nowhere to split and fails here.
TRACKED_CHANGE_GUARD = "wherever this dispatch left a tracked change"

#: How that guard is settled. Without this the guard would be the worker's to claim, and
#: "I changed nothing" would be the escape hatch rather than an observation of the tree.
GUARD_IS_READ_FROM_THE_REPOSITORY = (
    "read from the repository itself, never from a claim to have changed nothing"
)

#: What the completion bar demanded of every dispatch before the guard existed, and must
#: still demand of one that left a tracked change. Stated as the phrases that carry each
#: obligation, so a rewording that keeps the words but drops an obligation fails.
OBLIGATIONS_OF_A_CHANGED_TREE = (
    "complete verification is green over the finished tree",
    "with every one of those changes committed",
    "nothing half-applied left behind",
)

#: What no guard may reach, because it is true of a dispatch that produced a document
#: exactly as it is of one that produced a diff. If the guard ever swallowed these, a
#: dispatch could clear the whole bar by changing nothing, which is the failure being
#: excluded.
OBLIGATIONS_OF_EVERY_DISPATCH = (
    "every acceptance criterion stated in the task is met",
    "rather than by inspection",
)


def _bar_binding(*, left_a_tracked_change: bool) -> str:
    """The part of the shared completion bar one dispatch shape has to satisfy."""
    bar = shared_completion_bar()
    guard_at = bar.find(TRACKED_CHANGE_GUARD)
    assert guard_at != -1, (
        f"{BASE_CONFIG}'s `user.done_when` no longer guards its gate-and-commit half on "
        f"{TRACKED_CHANGE_GUARD!r}, so what it demands of a dispatch that changed nothing "
        f"cannot be told apart from what it demands of one that did:\n{bar}"
    )
    return bar if left_a_tracked_change else bar[:guard_at]


def test_the_completion_bar_still_holds_a_changed_tree_to_everything_it_held_before() -> None:
    """Guarding the gate-and-commit half weakened nothing for a dispatch that changed code.

    The obligations are checked inside the guarded half specifically, not anywhere in the
    clause: an obligation that drifted out of the guard would still be found by a
    whole-string search while no longer being what a changed tree is held to.
    """
    binding = _bar_binding(left_a_tracked_change=True)
    guarded = binding[binding.find(TRACKED_CHANGE_GUARD) :]
    for obligation in OBLIGATIONS_OF_A_CHANGED_TREE:
        assert obligation in guarded, (
            f"{BASE_CONFIG}'s `user.done_when` no longer demands {obligation!r} of a "
            f"dispatch that left a tracked change:\n{binding}"
        )
    assert GUARD_IS_READ_FROM_THE_REPOSITORY in guarded, (
        f"{BASE_CONFIG}'s `user.done_when` no longer says the guard is settled from the "
        "repository, so a worker's claim to have changed nothing decides it:\n"
        f"{binding}"
    )


def test_the_completion_bar_is_not_cleared_by_a_dispatch_having_changed_nothing() -> None:
    """Changing nothing removes the gate and the commit, and nothing else.

    What a document-producing dispatch is left with has to still be a bar. This excludes
    the rewording that would satisfy the guard while gutting the clause — pushing "every
    acceptance criterion stated in the task is met" or the demand that each be proven by
    running checks rather than by inspection behind the guard too, leaving a dispatch
    that changed nothing with nothing to meet.
    """
    binding = _bar_binding(left_a_tracked_change=False)
    for obligation in OBLIGATIONS_OF_EVERY_DISPATCH:
        assert obligation in binding, (
            f"{BASE_CONFIG}'s `user.done_when` no longer demands {obligation!r} of a "
            f"dispatch that changed nothing tracked:\n{binding}"
        )
    for obligation in OBLIGATIONS_OF_A_CHANGED_TREE:
        assert obligation not in binding, (
            f"{BASE_CONFIG}'s `user.done_when` demands {obligation!r} before its guard, so "
            f"a dispatch with no finished tree is held to it anyway:\n{binding}"
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
