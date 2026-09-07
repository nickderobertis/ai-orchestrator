"""The steps after a plan is authored exist once, and both entry points reach that copy.

`just plan` and `just finish-plan` are one flow with two ways in: the first runs the
planner and hands over, the second is run on its own when a plan was edited after it was
authored. What they must never be is two implementations of the same five steps — review,
check, launch the document, copy, report — because two copies drift, and the drift is
invisible from either side until an operator gets a different answer depending on which
command they typed.

`tests/e2e/test_delegated_recipes_e2e.py` proves the positive half by driving both entry
points and reading the command lines each reaches, in order. What that cannot see is a
*second* implementation appearing beside the first: a `just plan` that reached the right
verbs by running them itself would satisfy every one of those rows. So this reads the
launcher's own source for the verbs it must not name, which is the half a behavioural
journey is structurally unable to answer.

It is deterministic and reads shell rather than prose, so it stays in the code tier: what
it holds is which file implements a step, and every one of those files is a tracked script
this package's own tier is keyed on.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.root import REPO_ROOT

#: The launcher that runs the planner, and the one that implements everything after it.
PLANNER = REPO_ROOT / "scripts" / "plan.sh"
TAIL = REPO_ROOT / "scripts" / "finish-plan.sh"

#: The grammar both of them read a brief and their shared options through. It is a third
#: file rather than a copy in each, for the reason this whole module is about: a brief
#: accepted by one entry point and refused by the other is the same drift one step earlier.
GRAMMAR = REPO_ROOT / "scripts" / "plan-brief.sh"

#: The steps of the tail, named as the launcher would have to name them to run one. Three
#: are this repository's own recipes and the fourth is the command behind the one step no
#: recipe wraps, which is where each is *implemented* — so a `just plan` naming any of them
#: would be a second copy of that step, reachable only from one of the two entry points.
TAIL_STEPS = (
    "review-plan",
    "check-plan",
    "copy-plan",
    "orchestrator-plan-locations",
)


def _commands(script: Path) -> str:
    """``script`` with its comment lines dropped, so this reads what it runs.

    Both launchers explain the flow at length in prose, and every step of the tail is
    named in `scripts/plan.sh`'s own header — as an account of what happens after it hands
    over, which is exactly what a reader of that file needs. Reading the prose as an
    implementation would make this gate refuse the documentation it wants.
    """
    return "\n".join(
        line
        for line in script.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def test_the_planner_launcher_implements_no_step_of_the_tail() -> None:
    """`just plan` runs the planner and hands over; it re-runs none of what follows.

    Every one of these steps is about the plan a planner wrote, and each has a refusal of
    its own an operator has to be able to act on. A second copy in the launcher would
    answer the same way on the day it was written and differently the first time either
    moved — and which of the two an operator met would depend on whether they had typed
    `just plan` or `just finish-plan`.
    """
    launcher = _commands(PLANNER)
    named = [step for step in TAIL_STEPS if step in launcher]
    assert not named, (
        f"{PLANNER.name} names {named}, which are steps {TAIL.name} implements; a launcher "
        f"that runs one of them itself is a second copy of that step, reachable only from "
        f"one of the two entry points"
    )


def test_the_planner_launcher_reaches_the_tail_by_running_it() -> None:
    """The handover is a call to the one implementation rather than a repetition of it."""
    assert TAIL.name in _commands(PLANNER), (
        f"{PLANNER.name} never runs {TAIL.name}, so a `just plan` stops at the planner and "
        f"the rest of the flow is nobody's"
    )


def test_every_step_of_the_tail_is_implemented_in_the_tail() -> None:
    """And the one implementation is where the delegation table says it is.

    The mirror of the first test: proving the launcher names none of these is worth
    nothing unless something does, and a step that had moved out of both files would pass
    that test while the flow no longer performed it at all.
    """
    tail = _commands(TAIL)
    missing = [step for step in TAIL_STEPS if step not in tail]
    assert not missing, (
        f"{TAIL.name} names none of {missing}, so those steps of the flow are implemented "
        f"nowhere and a plan reaches its destination without them"
    )


def test_both_entry_points_read_a_brief_through_the_one_grammar() -> None:
    """What a brief is, and what the shared options mean, is stated once.

    Each entry point is handed the same brief by the same operator, so a section required
    by one and not the other — or a `--repo` that clears its execution checkout in one and
    not the other — is a flow that accepts a command line halfway. The grammar is a
    sourced helper for that reason, and this is what says both still source it.
    """
    for launcher in (PLANNER, TAIL):
        source = _commands(launcher)
        assert GRAMMAR.name in source, (
            f"{launcher.name} does not source {GRAMMAR.name}, so it reads a brief and its "
            f"options by a grammar of its own"
        )
        assert "plan_options_parse" in source, (
            f"{launcher.name} sources {GRAMMAR.name} but parses its options elsewhere"
        )
