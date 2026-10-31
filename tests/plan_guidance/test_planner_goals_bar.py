"""The planner's reviewer holds criteria, plan size and budgets to a plan's stated Goals.

`personas/planner.yaml`'s `user.persona` is the bar both the planner's own judge and `just
review-plan` read. Adding the Goals demands to it is additive: every demand the six points
made before is still there, word for word, which is read here clause by clause against the
bar as it stood at :data:`BEFORE_GOALS` — taken from git rather than copied, so the
comparison cannot drift from what the bar actually said.
"""

from __future__ import annotations

import re
import subprocess

import yaml

from orchestrator.root import REPO_ROOT

PERSONA = "personas/planner.yaml"

#: The last commit whose bar stated no Goals demand.
BEFORE_GOALS = "b412af54f51570dd2367672fbedb497f4a55d1fb"

#: What the bar now asks of Goals, each by the words that state it.
GOALS_DEMANDS = {
    "the goals cover the request": "The tasks' stated Goals must cover the request faithfully",
    "a simpler plan, sought": "Actively look for a simpler plan that still reaches every goal",
    "a simplification, named": "name the simplification concretely",
    "no simplification relaxes a goal": (
        "Never propose a simplification that works only by relaxing or dropping a goal"
    ),
    "criteria and budgets prove each goal": (
        "every stated goal is proven by the task's criteria and budgets taken together"
    ),
    "a budget proves its goal": "a budget counting as proof of the goal it backs",
    "no pairing": "Assume no pairing",
    "many to many": "one criterion may prove several goals, and one goal may need several",
    "no passing combination misses a goal": (
        "no combination of every criterion and every budget passing may leave one of them unmet"
    ),
    "a duplicated budget is a pointer": "`plan_criteria_do_not_restate_budgets`",
    "no goals is never refused": "and is never refused for that",
    "no goals is named only in another refusal": (
        "Mention that it states no Goals only inside the reasons of a review that refuses the "
        "task for something else; a passing review carries no such observation"
    ),
    "no goals falls back": "held to the goal its What and Why state",
    "non-functional goals are budgeted": "every non-functional goal is backed by a budget",
    "budgets trace to goals": "every budget the plan adds or changes traces to a goal",
}


def _flat(prose: str) -> str:
    return " ".join(prose.split())


def _bar(text: str) -> str:
    persona = yaml.safe_load(text)
    assert isinstance(persona, dict), "the persona is not a mapping"
    user = persona.get("user")
    assert isinstance(user, dict), "the persona declares no `user` mapping"
    bar = user.get("persona")
    assert isinstance(bar, str), "the persona's `user.persona` is not prose"
    return _flat(bar)


def _before() -> str:
    shown = subprocess.run(
        ["git", "show", f"{BEFORE_GOALS}:{PERSONA}"],  # noqa: S607 - git from the search path
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return _bar(shown.stdout)


def _clauses(bar: str) -> list[str]:
    """Each sentence of ``bar``, split at a full stop, colon or semicolon ending a clause."""
    return [clause.strip() for clause in re.split(r"(?<=[.:;])\s+", bar) if clause.strip()]


def test_every_demand_the_six_points_made_before_goals_still_stands_word_for_word() -> None:
    before = _clauses(_before())
    assert len(before) > 20, before
    now = _bar((REPO_ROOT / PERSONA).read_text(encoding="utf-8"))
    dropped = [clause for clause in before if clause not in now]
    assert not dropped, f"the bar no longer states {dropped}"


def test_the_bar_states_every_goals_demand() -> None:
    now = _bar((REPO_ROOT / PERSONA).read_text(encoding="utf-8"))
    missing = [name for name, phrase in GOALS_DEMANDS.items() if _flat(phrase) not in now]
    assert not missing, f"the bar does not state {missing}"
    assert all(_flat(phrase) not in _before() for phrase in GOALS_DEMANDS.values()), (
        "a Goals demand was already in the bar before goals existed, so this proves nothing"
    )


#: How the coverage check reads a task stating no Goals: from its What and Why, so a plan
#: whose request those cover in full is not refused for stating none.
COVERAGE_WITHOUT_GOALS = (
    "Read what a task delivers from its `## Goals` where it states them, and from its What "
    "and Why where it states none",
    "a plan whose tasks state no Goals but whose What and Why deliver every outcome of the "
    "request is not refused for that",
)

#: The wording that called any outcome absent from every task's `## Goals` a hole, which
#: refused a plan of tasks stating no Goals however fully their What and Why covered it.
COVERAGE_BY_GOALS_ALONE = "an outcome the request asks for that no task's `## Goals` states"


def test_goals_coverage_reads_a_task_stating_no_goals_from_its_what_and_why() -> None:
    """A request covered in full by tasks stating no Goals is no hole under point 1."""
    now = _bar((REPO_ROOT / PERSONA).read_text(encoding="utf-8"))
    (point_one,) = re.findall(r"1\. \*\*It achieves every original goal.*?(?= 2\. \*\*)", now)
    missing = [one for one in COVERAGE_WITHOUT_GOALS if _flat(one) not in point_one]
    assert not missing, f"point 1 does not state {missing}"
    assert _flat(COVERAGE_BY_GOALS_ALONE) not in now, (
        "point 1 still calls an outcome no task's `## Goals` states a hole, refusing a plan "
        "whose tasks state no Goals however fully their What and Why cover the request"
    )
