"""`just check-plan` holds a plan to its budgets, through the real recipe.

Every plan states each budget on the task that owns it — the task's `orchestrator.budgets`
metadata, rendered as its `## Budgets` section — and its plan-level answers as its project's
`orchestrator.plan-budgets` metadata, rendered as its description from the
`plan-description` template. The check refuses a plan that carries no plan-level answers,
whose answers break a rule, or whose bodies are not their records' renderings — naming the
rule — while a plan keeping them all passes, and so does a project of a plan kind exempt from
design approval, with no answers at all.

Everything is real: the plan written through this repository's own record renderer into
this process's own fixture source, each owning task's section rendered by the pinned store
and its record set through the store's `task metadata set`, the description rendered by the
pinned engine's `template resolve` piped into the pinned store's `project create`, the review
recorded by the real `just review-plan` with only the paid provider doubled, and then the
real `just check-plan`, the engine's `plan check` and the registered check it spawns.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] These journeys sit in
`plan-tooling` beside the check-plan and finish-plan journeys they extend, and drive the same
surface those do — the real recipes, the registered check, the installed engine and store —
under the inputs `planToolingWorkspace` names, which `tests/plan_tooling/AGENTS.md` states as
that project's split. A project of their own would be keyed on those same inputs and add only
a target.
"""

from __future__ import annotations

import copy
import itertools
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import plan_fixture_source
import pytest
from project_fixtures import budget_section, budgeted, older_engine, owning, reviewed
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator import design_approval, plan_budgets
from orchestrator.criteria_guard import APPENDIX
from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

_SEQUENCE = itertools.count()

# llmlint: ignore[suppressions_justified] A budgets record is the templates' open JSON answer
# shape, which these journeys build and then break one rule at a time by replacing a nested
# value, so its values are `Any` here by design; the store renders whatever they hold and the
# check under test is what reads it typed.
type Record = dict[str, Any]

#: The one repository the plans here change, and the node that owns the budget.
REPOSITORY = "github.com/nickderobertis/some-service"
NODE = "listing"

#: Criteria answering every demand the appendix and the built-in bar make, so a plan
#: carrying them is refused by nothing but its budgets.
CRITERIA = (
    "- The listing pages at 2,000 nodes and stays within the `listing-latency` budget.\n"
    "- A request-level test drives the listing end to end and covers the last page.\n"
    "- Every claim the dispatch makes about the finished work is true of the tree as it "
    "finally stands."
)

#: The one budget the node owns, breaking no rule.
BUDGET: Record = {
    "id": "listing-latency",
    "name": "Time to the first page",
    "basis": "measured",
    "repository": REPOSITORY,
    "file": "apps/web/budgets.yaml",
    "file_change": "add",
    "measure": "time to the first page in the browser",
    "inner_measure_reason": "",
    "unit": "ms",
    "direction": "max",
    "threshold": 800,
    "workload": "2,000 nodes",
    "evidence": "spike-listing measured 420 ms",
    "command": "bun run measure:listing",
}

#: Plan-level answers that keep every rule.
ANSWERS: Record = {
    "overview": "Page the node listing.",
    "sizing": "2,000 nodes per plan, 3 runs at once.",
    "workload": "2,000 nodes per plan, 3 runs at once.",
    "ten_x_summary": "The listing slows first.",
    "ten_x": "The listing slows first; listing-latency covers it.",
    "checklist": [
        {"concern": "latency", "budget": "listing-latency", "not_applicable": "", "summary": ""},
        {
            "concern": "spend",
            "budget": "",
            "not_applicable": "n/a because it calls no paid API",
            "summary": "It calls no paid API.",
        },
    ],
    "repo_wide_effects": [
        {
            "repository": REPOSITORY,
            "budget": "gate-time",
            "effect": "+20 s",
            "summary": "The gate grows by 20 s.",
        }
    ],
    "realistic_data": [],
    "spike_findings": [],
}


def _broken(**changes: object) -> Record:
    """``ANSWERS`` with each named answer replaced, or its first entry's keys changed."""
    answers = copy.deepcopy(ANSWERS)
    for field, value in changes.items():
        if isinstance(value, dict):
            answers[field][0].update(value)
        else:
            answers[field] = value
    return answers


def _task(section: str) -> str:
    return (
        "## What\n\nPage the node listing.\n\n## Why\n\nAn operator cannot see past the "
        f"first screen.\n\n{section}## Acceptance criteria\n\n{CRITERIA}\n\n"
        f"{(REPO_ROOT / APPENDIX).read_text(encoding='utf-8').strip()}\n"
    )


def _drafted(
    answers: Record | None,
    budgets: list[Record] | None = None,
    *,
    record: object = None,
    section: str | None = None,
    stamp: object = None,
    second: list[Record] | None = None,
) -> str:
    """One reviewed plan, its node owning ``budgets`` and its plan-level ``answers`` stated.

    The node's body carries the section its budgets render unless ``section`` is given, and
    its record is ``budgets`` unless ``record`` is; ``second`` adds a second node owning
    those budgets, rendered and recorded alike.
    """
    budgets = [BUDGET] if budgets is None else budgets
    native = f"budgets-check-{os.getpid()}-{next(_SEQUENCE)}"
    shown = section if section is not None else (budget_section(budgets) if budgets else "")
    tasks = [
        {
            "id": NODE,
            "persona": "engineer",
            "repo": f"https://{REPOSITORY}",
            "title": "feat: page the node listing",
            "task": _task(shown),
        }
    ]
    if second is not None:
        tasks.append({**tasks[0], "id": "paging", "title": "feat: page more"})
        tasks[1]["task"] = _task(budget_section(second))
    write_plan_project(
        plan_fixture_source.root(),
        {
            "schema_version": 3,
            "goal": {"text": "Page the node listing"},
            "name": native,
            "tasks": tasks,
        },
        native_id=native,
        project_metadata=None if stamp is None else {design_approval.PLAN_KIND: stamp},
    )
    held = budgets if record is None else record
    if held:
        owning(plan_fixture_source.SOURCE, native, NODE, held)
    if second is not None:
        owning(plan_fixture_source.SOURCE, native, "paging", second)
    if answers is not None:
        budgeted(plan_fixture_source.SOURCE, native, answers)
    return reviewed(f"{plan_fixture_source.SOURCE}:{native}")


def _check(
    project: str, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", "check-plan", project],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )


def _store(*arguments: str) -> None:
    done = subprocess.run(
        [str(ONETASKGRAPH_BIN), *arguments],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr


def _narrower(root: Path) -> dict[str, str]:
    """The environment that takes `just check-plan` down its path for an engine with no
    `plan check`, which reads each task's budgets off the store's records rather than off the
    document the engine hands a registered check."""
    # llmlint: ignore[e2e_not_mocked] The narrower path is reached only against an engine
    # carrying no `plan check`, for the reason `older_engine` states; the recipe, this
    # repository's checks and the store it reads are all real.
    return os.environ | {"ORCHESTRATOR_PLAN_CHECK_ENGINE": str(older_engine(root))}


def test_a_plan_whose_budgets_keep_every_rule_passes(tmp_path: Path) -> None:
    """A task owning a budget passes on both paths, its plan-level review read as current."""
    project = _drafted(ANSWERS)

    checked = _check(project)
    directly = _check(project, _narrower(tmp_path))

    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "budgets" not in checked.stderr, checked.stderr
    assert directly.returncode == 0, directly.stdout + directly.stderr
    assert "carries no `plan check`" in directly.stdout, directly.stdout


def test_a_plan_carrying_no_plan_level_answers_is_refused_naming_the_record() -> None:
    project = _drafted(None)

    checked = _check(project)

    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert f"{project} carries no `orchestrator.plan-budgets` metadata" in checked.stderr
    assert "from the `plan-description` template" in checked.stderr
    assert "config/budgets-migration.yaml" in checked.stderr


@pytest.mark.parametrize(
    ("answers", "budgets", "rule"),
    [
        (
            _broken(checklist={"budget": "", "not_applicable": ""}),
            None,
            "answers neither of `budget` and `not_applicable`",
        ),
        (
            _broken(checklist={"not_applicable": "n/a because it is fast", "summary": "Fast."}),
            None,
            "answers both of `budget` and `not_applicable`",
        ),
        (
            _broken(checklist={"budget": "no-such-budget"}),
            None,
            "names the budget 'no-such-budget', which no task of the plan owns",
        ),
        (_broken(repo_wide_effects=[]), None, f"states no effect for {REPOSITORY}, a repository"),
        (ANSWERS, [{**BUDGET, "id": " "}], "budget 1 has no `id`, and every budget is named"),
        (ANSWERS, [{**BUDGET, "command": ""}], "names no `command`"),
        (ANSWERS, [{**BUDGET, "direction": "under"}], "has the `direction` 'under', which is not"),
        (
            ANSWERS,
            [{**BUDGET, "file_change": "rewrite"}],
            "has the `file_change` 'rewrite', which is not one of add, change, none",
        ),
        (
            ANSWERS,
            [{**BUDGET, "file": "apps/web/budgets.toml"}],
            "goes in the file 'apps/web/budgets.toml', which is not a relative path whose last "
            "component is `budgets.yaml`",
        ),
        (ANSWERS, [{**BUDGET, "basis": "a hunch"}], "has the `basis` 'a hunch', which is not one"),
        (
            ANSWERS,
            [{**BUDGET, "name": "n" * (plan_budgets.NAME_LIMIT + 1)}],
            f"on one line of at most {plan_budgets.NAME_LIMIT} characters",
        ),
        (
            _broken(checklist=[{**ANSWERS["checklist"][1], "summary": "s" * 161}]),
            [],
            f"`summary` is not one line of at most {plan_budgets.SUMMARY_LIMIT} characters",
        ),
        (
            _broken(repo_wide_effects={"summary": ""}),
            None,
            "has no `summary`, and its summary is non-empty exactly when its `effect` is not",
        ),
        (
            _broken(ten_x_summary="t" * (plan_budgets.TEN_X_SUMMARY_LIMIT + 1)),
            None,
            f"`ten_x_summary` answer is not one line of at most {plan_budgets.TEN_X_SUMMARY_LIMIT}",
        ),
        (
            _broken(sizing="s" * (plan_budgets.SIZING_LIMIT + 1)),
            None,
            f"over the {plan_budgets.SIZING_LIMIT} a design document's opening paragraph",
        ),
        (
            _broken(checklist={"owner": "the listing team"}),
            None,
            "plan-level answers are malformed",
        ),
    ],
    ids=[
        "checklist-neither",
        "checklist-both",
        "checklist-unknown-budget",
        "repo-wide-effects",
        "no-id",
        "command",
        "direction",
        "file-change",
        "file",
        "basis",
        "name-cap",
        "summary-cap",
        "summary-presence",
        "ten-x-summary-cap",
        "sizing-cap",
        "malformed-plan-record",
    ],
)
def test_each_budgets_rule_is_refused_naming_the_rule(
    answers: Record, budgets: list[Record] | None, rule: str
) -> None:
    project = _drafted(answers, budgets)

    checked = _check(project)

    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert rule in checked.stderr, checked.stderr


def test_a_budget_id_repeated_across_two_tasks_is_refused() -> None:
    project = _drafted(ANSWERS, second=[BUDGET])

    checked = _check(project)

    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert "the budget id(s) 'listing-latency' repeat across the plan's tasks" in checked.stderr


def test_a_malformed_task_record_is_refused_naming_its_task() -> None:
    project = _drafted(ANSWERS, [], record=[{"id": "listing-latency"}])

    checked = _check(project)

    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert "its `orchestrator.budgets` metadata is malformed" in checked.stderr


def test_a_task_carrying_a_budgets_section_and_no_record_is_refused() -> None:
    no_budget = _broken(checklist=[ANSWERS["checklist"][1]])
    project = _drafted(no_budget, [], section=budget_section([BUDGET]))

    checked = _check(project)

    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert (
        "its body carries a `## Budgets` section but its `orchestrator.budgets` metadata states "
        "no budget"
    ) in checked.stderr


def test_a_task_whose_section_is_not_its_records_rendering_is_refused(tmp_path: Path) -> None:
    """Refused on both paths, each naming the node whose section and record disagree."""
    project = _drafted(ANSWERS, record=[{**BUDGET, "threshold": 900}])
    differs = (
        "its body does not carry the `## Budgets` section its `orchestrator.budgets` metadata "
        "renders"
    )

    checked = _check(project)
    directly = _check(project, _narrower(tmp_path))

    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert differs in checked.stderr, checked.stderr
    assert directly.returncode == 1, directly.stdout + directly.stderr
    assert f"check-plan: {NODE}: " in directly.stderr, directly.stderr
    assert differs in directly.stderr, directly.stderr


def test_a_description_that_is_not_its_records_rendering_is_refused() -> None:
    project = _drafted(ANSWERS)
    _store(
        "project",
        "metadata",
        "set",
        project,
        plan_budgets.PLAN_RECORD,
        json.dumps(_broken(sizing="20,000 nodes per plan.")),
    )

    checked = _check(project)

    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert "is not the `plan-description` rendering of its `orchestrator.plan-budgets`" in (
        checked.stderr
    )


def test_a_description_recording_another_template_is_refused(tmp_path: Path) -> None:
    project = _drafted(ANSWERS)
    native = project.partition(":")[2]
    body = tmp_path / "description.md"
    body.write_text(plan_budgets.render_description(ANSWERS), encoding="utf-8")
    _store(
        "project",
        "create",
        plan_fixture_source.SOURCE,
        "--id",
        native,
        "--title",
        native,
        "--body-file",
        str(body),
        "--no-interactive",
    )

    checked = _check(project)

    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert "not onepipeline:plan-description" in checked.stderr, checked.stderr


def test_a_planning_launchs_project_passes_with_no_budget_answers() -> None:
    """The plan kinds exempt from design approval are exempt here, on the same bound."""
    stamp = {
        design_approval.STAMP_KIND: design_approval.PLANNING,
        design_approval.STAMP_NODES: [NODE],
    }
    project = _drafted(None, [], stamp=stamp)

    checked = _check(project)

    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "budgets" not in checked.stderr, checked.stderr
