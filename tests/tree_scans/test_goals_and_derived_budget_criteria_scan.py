"""What a goal is, and the checklist rule a task's derived budget criteria retired, each said once.

`templates/plan-task.md.j2`'s `goals` variable is the one statement of what a goal is: a
reviewer, a planner, a design document and a doc point at it rather than restate it, so the
definition has one wording to change. And since a task's budgets derive their own acceptance
criteria, the plan-checklist rule that made a budget owner's criteria name each budget is
retired, replaced by `plan_criteria_do_not_restate_budgets`: a file still naming it would
send a planner or a reviewer to a rule no checklist declares.
"""

from __future__ import annotations

import subprocess

import pytest

from orchestrator.root import REPO_ROOT

#: Every file this scan reads is one git tracks, prose among them.
pytestmark = pytest.mark.reads_docs

#: The words only the definition of a goal uses, and the one file that states it.
GOAL_DEFINITION = "a concrete, checkable outcome that must be true when the work is done"
DEFINED_IN = "templates/plan-task.md.j2"
#: Where the tests quoting the definition to hold it live, which state nothing to a reader.
QUOTED_BY_TESTS = "tests/"

#: The rule `plan_criteria_do_not_restate_budgets` replaced, spelled in two halves so this
#: scan does not name it whole.
RETIRED_RULE = "plan_budget_owner_criteria_" + "name_budget_and_workload"

#: Verbatim records of checklist runs taken before the rule was retired, which name it as the
#: run recorded it and are never edited: `tests/test_plan_checklist_budgets.py` reads them as
#: its workload.
RECORDED_RUNS = "tests/fixtures/plan-checklist-budgets/"


def _tracked() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607 - git from the search path, as the gate runs it
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode()
    return [path for path in listed.split("\0") if path and (REPO_ROOT / path).is_file()]


def _flat(path: str) -> str:
    return " ".join((REPO_ROOT / path).read_bytes().decode("utf-8", "replace").split())


def test_what_a_goal_is_is_stated_once_on_the_plan_task_templates_variable() -> None:
    stating = [
        path
        for path in _tracked()
        if not path.startswith(QUOTED_BY_TESTS) and GOAL_DEFINITION in _flat(path)
    ]
    assert stating == [DEFINED_IN], (
        f"what a goal is is stated in {stating}; it is stated once, in {DEFINED_IN}'s `goals` "
        "variable, and every other file points at it"
    )


def test_no_file_names_the_retired_checklist_rule() -> None:
    naming = [
        path
        for path in _tracked()
        if not path.startswith(RECORDED_RUNS) and RETIRED_RULE in _flat(path)
    ]
    assert not naming, f"{naming} name the retired {RETIRED_RULE}; name its replacement"
