"""The clauses every dispatch shares say what is true of every dispatch, and stop there.

`config/onejudge.base.yaml` is where this host tells a dispatch what "done" means:
`system_prompt` is the preamble its worker reads, and `user.done_when` is the completion
criterion its judge is handed. What that clause says is said of a plan, a report, and a
diff alike — so a demand that is not true of all three fails the dispatches it is false
of, whatever they were actually asked for. Four nodes with finished, gate-green work were
failed that way: by a demand about *how* a criterion must be proven, which only the
criterion knows; and by one about state that exists only after the dispatch has ended,
which no worker can reach from inside its own run.

So the completion bar is read here twice — once for what it says, and once for the
vocabulary it may not say again. The second is the half that keeps working: a literal to
compare against is trivially updated alongside the file it mirrors, while a re-added
demand still carries the words of the class it belongs to.

`user.persona` used to be read against that same vocabulary and is now held **absent**,
which is a stronger guard than narrowing it ever was. That field is replaced rather than
merged — by a bare name resolving to a role built into the tool exactly as by a path into
`personas/` — and every dispatch names one of the two, so whatever it said reached
nothing. What it said last was a bound on the supervisor's authority, written after two
incidents in which a simulated user issued rulings on the manager's behalf; it was never
in force, and a field that reads like a protection while reaching no dispatch is the kind
a manager stops checking. So the guard is that there is no such field, rather than that
the field says something safe.

Nothing here was dropped rather than moved: what those demands carried is now each
node's own `## Acceptance criteria` to state, which `AGENTS.md` gives the plan's author.
One tail is deliberately kept, and `config/onejudge.base.yaml` records beside the clause
why it is not the next thing to remove.

The preamble half below is a different guard on the same file: a dispatch with no
tracked change is told the truth, and one that changed something is not let off.

That half moved once and is now read the same way the completion bar is — for what it
says, and for the demand it may not make again. It used to order every dispatch to run
the project's complete verification at closeout, independently of its task, so removing
that instruction from the operational appendix alone would have left it in force and the
change would not have landed. What replaced it is the checks that exercise the change,
with the project's full bar named as something that runs downstream; what it keeps is
everything that clause was the only enforcement of — the deterministic tier before each
commit, the refusal to bypass a pre-commit or pre-push hook, and a failing check
iterated on alone.

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
    judge_persona_default,
    shared_agent_preamble,
    shared_completion_bar,
)
from test_dispatch_appendix import DOWNSTREAM, WIDE_BAR, sentence_around

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


def test_the_base_config_states_no_judge_persona_at_all() -> None:
    """The field a dispatch replaces states nothing, because it can reach nothing.

    Held as an absence rather than as a narrowing, and that is the whole of the repair.
    A `user.persona` here is not merged with the role a node names: `oneagentgraph`
    replaces it, whether that role is a file under `personas/` or a bare name resolving
    to one built into the tool, and every dispatch names one of the two. So a clause
    written here is read by nobody — which is how a bound on the supervisor's authority,
    written after two incidents that produced exactly that failure, came to be believed
    in force for as long as it existed while applying to nothing.

    Read in both shapes it could come back in, because the reader behind
    :func:`judge_persona_default` accepts a block scalar and a single quoted line alike.
    """
    stated = judge_persona_default()
    assert stated is None, (
        f"{BASE_CONFIG} states a `user.persona` again. Every dispatch replaces that "
        "field rather than merging it, so nothing there reaches a judge; state a review "
        "contract in the node's own persona or its `task`, where it will be read:\n"
        f"{stated}"
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
    "run only the checks that exercise what you changed",
    "iterate against that check alone, at the narrowest scope it supports",
    "Work is not done while a check you ran is failing",
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
        f"{BASE_CONFIG}'s `system_prompt` states {len(allowing)} sentence(s) hanging on "
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


#: What a project-wide bar is spelled as, what makes a mention of one a statement about
#: who runs it, and how a mention is cut down to the sentence it sits in — all three
#: imported from the appendix's own guard rather than restated. The preamble and the
#: operational notes are two halves of what one dispatch reads, so a bar this file
#: admitted and that one refused would be a contradiction handed to a worker, exactly
#: like the one the appendix used to contain.

#: The demand this clause used to make, in the wordings a re-added one would take. Held
#: as phrases rather than as stems because the preamble legitimately uses every stem
#: inside them: it is the one clause here that names checks at all.
NO_PROJECT_WIDE_DEMAND = (
    "complete verification",
    "full verification",
    "complete gate",
    "whole gate",
    "entire gate",
    "at closeout",
)


@pytest.mark.parametrize("phrase", NO_PROJECT_WIDE_DEMAND)
def test_the_preamble_demands_no_project_wide_verification_of_every_dispatch(
    phrase: str,
) -> None:
    """The removed demand, held gone by the way it would be worded rather than by one line.

    This clause reached every dispatch whatever its task said, which is why the appendix
    could not remove the instruction on its own. A re-added one would be worded fresh, so
    what is checked is the phrasing such a demand cannot avoid: a verification named as
    complete, or a run placed at closeout.
    """
    preamble = " ".join(shared_agent_preamble().split())
    assert phrase.lower() not in preamble.lower(), (
        f"{BASE_CONFIG}'s `system_prompt` demands {phrase!r} of every dispatch again. "
        "That demand is what cost twenty to forty minutes a dispatch to learn what the "
        "merge path reports anyway; what a dispatch owes is the checks that exercise its "
        f"own change:\n{preamble}"
    )


def test_the_preamble_names_a_project_wide_bar_only_as_something_run_downstream() -> None:
    """The positive half: the wide bar is somebody else's, and the clause says whose.

    Without it, a worker told to run less concludes the rest is nobody's and runs it
    anyway — which is the instruction returning through the worker's own judgment rather
    than through this file.
    """
    preamble = shared_agent_preamble()
    mentions = list(WIDE_BAR.finditer(preamble))
    assert mentions, (
        f"{BASE_CONFIG}'s `system_prompt` no longer says a project-wide bar exists at "
        "all, so nothing tells a dispatch who runs what it was told not to"
    )
    for mention in mentions:
        sentence = sentence_around(preamble, mention.start())
        assert any(marker in sentence.lower() for marker in DOWNSTREAM), (
            f"{BASE_CONFIG}'s `system_prompt` names {mention.group(0)!r} in a sentence "
            f"that does not say it runs downstream ({sentence!r}), so it reads as this "
            "dispatch's to run"
        )


def test_the_preamble_still_holds_a_dispatch_that_changed_something_to_every_demand() -> None:
    """No allowance for a document-producing dispatch is reachable by one that changed code.

    Demonstrated by removing every sentence that hangs on having changed nothing and
    reading what is left, which is what a dispatch that changed something is bound by.
    This excludes the rewording that reads as permission — moving the demand to run the
    checks that exercise the change, "work is not done while a check you ran is failing",
    the narrowed iteration, the refusal to bypass a pre-commit or pre-push hook, or
    "never leave finished work uncommitted" inside a sentence that a dispatch with a diff
    could also apply to itself, or softening any of them into something optional for
    everyone.
    """
    binding = _preamble_binding_on_a_dispatch_that_changed_something()
    for demand in DEMANDS_OF_A_DISPATCH_THAT_CHANGED_SOMETHING:
        assert demand in binding, (
            f"{BASE_CONFIG}'s `system_prompt` no longer demands {demand!r} of a dispatch "
            f"that changed something; it survives only inside an allowance for one that did "
            f"not, or is gone:\n{binding}"
        )


def test_the_preamble_tells_a_dispatch_with_no_tracked_change_something_true() -> None:
    """The allowances exist, and each says what IS true of a document-producing dispatch.

    The other direction of the same guard: an allowance that named its condition but
    demanded the checks anyway would leave the preamble as false for that dispatch as it
    was before. So each sentence is read back for what it permits — closing out with no
    checks over code that was never changed, and a clean `git status` being a complete
    outcome rather than a reason to author a file nobody asked for.
    """
    closeout = _preamble_sentence_allowing(NO_TRACKED_CHANGE_ALLOWANCES[0])
    assert "complete without them" in closeout, (
        f"{BASE_CONFIG}'s `system_prompt` names the no-tracked-change condition at "
        f"closeout without saying such a dispatch is complete:\n{closeout}"
    )
    assert "never because running them is slow or inconvenient" in closeout, (
        f"{BASE_CONFIG}'s `system_prompt` lets closeout be skipped without saying what "
        f"may not decide it, so any dispatch can reach the allowance:\n{closeout}"
    )
    committing = _preamble_sentence_allowing(NO_TRACKED_CHANGE_ALLOWANCES[1])
    assert "correct and complete outcome" in committing, (
        f"{BASE_CONFIG}'s `system_prompt` no longer tells a dispatch that touched no "
        f"tracked file that having nothing to commit is a correct outcome:\n{committing}"
    )
