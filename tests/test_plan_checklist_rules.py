"""Each plan check is stated once: in the planner's judge or in the plan checklist.

A plan is reviewed by two judges. The planner's reviewer holds it to the `user.persona`
bar of `personas/planner.yaml`, which keeps only what needs judgment — the request read
clause by clause, a split weighed against the parallelism it buys. The plan checklist,
`config/plan-checklist.llmlint.yml`, holds it to the rules a reader settles one document at
a time. A check stated in both drifts, and two tiers judging one property refuse each
other's wording, so this holds the split: the moved checks stay out of the bar, the ones
that need judgment stay in it, the checklist's own rules are exactly the ones the planner's
judge gave up plus the two that make a plan state what the fragment rules judge, and none of
their wording is copied into the documents that instruct a planner, which name each rule
instead.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
import yaml

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

CONFIG = "config/plan-checklist.llmlint.yml"
PERSONA = "personas/planner.yaml"
BUDGETS_DOC = "docs/budgets.md"

#: The rules the checklist words itself: the nine checks moved out of the planner's judge,
#: then the two that make a plan state the facts a re-declared fragment rule judges.
NEW_RULES = (
    "plan_criteria_satisfiable_inside_the_dispatch",
    "plan_criteria_require_realistic_tests",
    "plan_workload_states_numbers",
    "plan_ten_x_names_a_covering_budget",
    "plan_budget_command_measures_its_figure",
    "plan_budget_basis_matches_evidence",
    "plan_budget_inner_measure_has_reason",
    "plan_criteria_omit_repo_wide_budgets",
    "plan_budget_owner_criteria_name_budget_and_workload",
    "plan_budget_names_its_telemetry_source",
    "plan_budget_measurement_is_decided",
)
COMPLETENESS_RULES = NEW_RULES[-2:]
#: The fragment rules re-declared for plans, whose wording stays upstream.
FRAGMENT_RULES = (
    "budgets_track_product_owner_outcomes",
    "budgets_reuse_gate_telemetry",
    "budgets_scoped_to_minimal_tree",
    "budget_commands_measure_directly",
    "tests_hold_no_nonfunctional_thresholds",
    "expensive_tests_stay_behind_their_own_edge",
)

#: What each check that left the bar would say if it came back, by the words only it uses.
#: Read against the bar lower-cased with its whitespace collapsed.
MOVED_FROM_THE_BAR = {
    "plan_criteria_satisfiable_inside_the_dispatch": ("inside its own dispatch",),
    "plan_criteria_require_realistic_tests": ("mock", "internal state", "snapshot"),
    "plan_workload_states_numbers": ("with numbers",),
    "plan_ten_x_names_a_covering_budget": ("10×", "10x"),
    "plan_budget_command_measures_its_figure": ("no command checks",),
    "plan_budget_basis_matches_evidence": ("basis",),
    "plan_budget_inner_measure_has_reason": ("inward",),
    "plan_criteria_omit_repo_wide_budgets": ("repo-wide", "repository-wide"),
    "plan_budget_owner_criteria_name_budget_and_workload": ("its workload in its criteria",),
    "budgets_track_product_owner_outcomes": ("second unit", "another budget's figure"),
    "budgets_reuse_gate_telemetry": ("scenario of its own", "standalone-measurement"),
    # Left without moving: `orchestrator/plan_budgets.py` refuses that shape itself.
    "every concern answered": ("every concern of the checklist",),
}
#: The checks that need judgment and stay with the reviewer, by a phrase each one states.
STAYS_IN_THE_BAR = {
    "every original goal and constraint": "walk the request clause by clause",
    "no clear way to simplify": "no clear way to simplify",
    "criteria that restate the task": "criteria that restate the task",
    "criteria inspection alone satisfies": "satisfied by inspection",
    "the cheapest-green gap hunt": "an agent looking for the cheapest green",
    "release adoption": "the plan adopts the release that carries it",
    "a likely concern dismissed": "a concern the workload makes likely and the checklist dismissed",
    "an implied budgets-file change": (
        "a budgets-file change the budgets imply and no budget states"
    ),
    "a target quietly loosened": "a requested target quietly loosened rather than escalated",
    "an unrecorded exception": "reject an exception only when it is **unrecorded**",
    "the acceptance standard": "accept nothing until all six hold",
}
#: The invariant of each rule the checklist applies, as the planner's instructions have
#: stated it in their own words: a pointer names the rule and leaves the invariant to the
#: checklist, so none of these belongs in `system_prompt` or `docs/budgets.md`. Read against
#: each document lower-cased with its whitespace collapsed.
RESTATEMENTS = {
    "plan_workload_states_numbers": ("with numbers", "in numbers"),
    "plan_criteria_satisfiable_inside_the_dispatch": (
        "inside its own dispatch",
        "outside its own dispatch",
        "what that dispatch controls",
        "turns on an external event",
    ),
    "plan_criteria_require_realistic_tests": (
        "the way a user reaches it",
        "the real interface",
        "layer under test",
    ),
    "plan_ten_x_names_a_covering_budget": (
        "naming the budget that covers it",
        "names the budget that covers it",
    ),
    "plan_budget_command_measures_its_figure": ("yields the budget's figure",),
    "plan_budget_basis_matches_evidence": ("what the number actually rests on",),
    "plan_budget_inner_measure_has_reason": (
        "only when the outer one cannot be checked",
        "use an inner measure only",
        "as close as you can to where",
        "as close to that impact as you can",
        "where the product owner feels the impact",
    ),
    "plan_criteria_omit_repo_wide_budgets": (
        "never written into a task's criteria",
        "out of every task's criteria",
    ),
    "plan_budget_owner_criteria_name_budget_and_workload": (
        "criteria name the budget",
        "names it in its criteria",
        "numbers reach the acceptance criteria",
        "realistic workload it holds at",
    ),
    "budgets_track_product_owner_outcomes": (
        "product-owner-level outcome",
        "second unit of the same concern",
        "per-step, per-phase",
    ),
    "budgets_reuse_gate_telemetry": (
        "scenario of its own",
        "telemetry the gate's tests already record",
        "telemetry the gate's tests record",
        "the command analyses the telemetry",
    ),
    "budgets_scoped_to_minimal_tree": (
        "kept in that project's tree",
        "budgets file of the project that owns what it measures",
        "must always be checked goes in the root file",
    ),
    "budget_commands_measure_directly": ("runner every budget shares",),
    "tests_hold_no_nonfunctional_thresholds": ("cost figure against a bound",),
    "expensive_tests_stay_behind_their_own_edge": ("graph edge of its own",),
}
#: The facts a plan has to state for the two completeness rules, which the planner's
#: instructions tell it to state: they are what a fragment rule judges, not either rule's
#: invariant, so they stay in `system_prompt` beside the pointers.
COMPLETENESS_FACTS = (
    "the test targets whose runs record what each telemetry budget's command reads",
    "the project that registers it in its budgets file",
    "one way each budget's figure is taken",
)
#: How many consecutive words of a rule's description make a restatement of it.
RESTATED_WORDS = 8


def _flat(prose: str) -> str:
    return " ".join(prose.lower().split())


def _words(prose: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", prose.lower())


def _shingles(prose: str) -> set[tuple[str, ...]]:
    words = _words(prose)
    return {tuple(words[at : at + RESTATED_WORDS]) for at in range(len(words) - RESTATED_WORDS + 1)}


def _text(document: str) -> str:
    return (REPO_ROOT / document).read_text(encoding="utf-8")


def _rules() -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = yaml.safe_load(_text(CONFIG))["rules"]
    return rules


def _persona() -> dict[str, Any]:
    persona: dict[str, Any] = yaml.safe_load(_text(PERSONA))
    return persona


def _bar() -> str:
    return _flat(_persona()["user"]["persona"])


def _instructing_documents() -> dict[str, str]:
    """Every document that instructs a planner or renders what it writes."""
    templates = sorted((REPO_ROOT / "templates").rglob("*"))
    return {
        PERSONA: _text(PERSONA),
        BUDGETS_DOC: _text(BUDGETS_DOC),
        **{
            str(path.relative_to(REPO_ROOT)): path.read_text(encoding="utf-8")
            for path in templates
            if path.is_file()
        },
    }


def test_the_checklist_words_exactly_the_rules_the_planners_judge_gave_up() -> None:
    worded = [rule["name"] for rule in _rules() if not rule.get("override")]
    assert sorted(worded) == sorted(NEW_RULES), (
        f"{CONFIG} words {sorted(worded)} itself, where it words exactly the nine checks "
        "moved out of the planner's judge and the two completeness rules"
    )
    for rule in _rules():
        assert rule.get("agent") == "plan", f"{rule['name']} is not judged under the plan agent"
        if rule.get("override"):
            assert "description" not in rule, (
                f"{rule['name']} re-declares a fragment rule with a description of its own; "
                "its one wording stays upstream"
            )
        else:
            assert str(rule.get("description", "")).strip(), f"{rule['name']} states nothing"


def test_the_fragment_rules_re_declared_for_plans_are_judged() -> None:
    judged = {
        rule["name"]
        for rule in _rules()
        if rule.get("override") and rule.get("relevance") is not False
    }
    assert judged == set(FRAGMENT_RULES), judged


@pytest.mark.parametrize("check", sorted(MOVED_FROM_THE_BAR))
def test_a_moved_check_stays_out_of_the_planners_judge(check: str) -> None:
    bar = _bar()
    back = [phrase for phrase in MOVED_FROM_THE_BAR[check] if phrase in bar]
    assert not back, (
        f"{PERSONA}'s `user.persona` bar states {back}, the check {check!r} the plan "
        "checklist holds; a check stated in both drifts, so leave it to the checklist"
    )


@pytest.mark.parametrize("check", sorted(STAYS_IN_THE_BAR))
def test_a_check_that_needs_judgment_stays_in_the_planners_judge(check: str) -> None:
    assert _flat(STAYS_IN_THE_BAR[check]) in _bar(), (
        f"{PERSONA}'s `user.persona` bar no longer states {check!r}, which needs judgment "
        "no checklist rule can encode"
    )


@pytest.mark.parametrize("rule", NEW_RULES)
def test_a_rules_wording_is_stated_only_in_the_checklist(rule: str) -> None:
    (declared,) = [found for found in _rules() if found["name"] == rule]
    wording = _shingles(str(declared["description"]))
    for document, prose in _instructing_documents().items():
        copied = sorted(wording & _shingles(prose))
        assert not copied, (
            f"{document} restates {rule}'s description ({' '.join(copied[0])!r}); name the "
            f"rule in {CONFIG} instead of copying its wording"
        )


@pytest.mark.parametrize("rule", NEW_RULES + FRAGMENT_RULES)
def test_the_planners_instructions_name_every_rule_the_checklist_applies(rule: str) -> None:
    assert f"`{rule}`" in _persona()["system_prompt"], (
        f"{PERSONA}'s `system_prompt` never names {rule}, so a planner meets it first as a "
        "refusal rather than as an instruction"
    )


@pytest.mark.parametrize("rule", COMPLETENESS_RULES)
def test_the_budgets_page_points_at_each_completeness_rule(rule: str) -> None:
    assert f"`{rule}`" in _text(BUDGETS_DOC), f"{BUDGETS_DOC} does not name {rule}"


@pytest.mark.parametrize("rule", sorted(RESTATEMENTS))
def test_the_planners_instructions_point_at_a_rule_rather_than_restating_it(rule: str) -> None:
    documents = {"system_prompt": _persona()["system_prompt"], BUDGETS_DOC: _text(BUDGETS_DOC)}
    for document, prose in documents.items():
        restated = [phrase for phrase in RESTATEMENTS[rule] if phrase in _flat(prose)]
        assert not restated, (
            f"{document} restates {rule}'s invariant ({restated}); name the rule and leave "
            f"its invariant to {CONFIG}"
        )


@pytest.mark.parametrize("fact", COMPLETENESS_FACTS)
def test_the_planner_is_told_the_facts_the_completeness_rules_ask_for(fact: str) -> None:
    assert fact in _flat(_persona()["system_prompt"]), (
        f"{PERSONA}'s `system_prompt` no longer tells a planner to state {fact!r}"
    )
