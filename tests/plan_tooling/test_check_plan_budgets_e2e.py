"""`just check-plan` holds a plan to its budgets document, through the real recipe.

Every plan states its budgets in the project document `<project>-budgets`, rendered from
the `plan-budgets` template, and the check refuses a plan that carries none or whose
document breaks a rule — naming the document and the rule — while a plan whose document
keeps them all passes, and so does a project of a plan kind exempt from design approval,
with no budgets document at all.

Everything is real: the plan written through this repository's own record renderer into
this process's own fixture source, the budgets document rendered by the pinned engine's
`template resolve` piped into the pinned store's `document create`, the review recorded by
the real `just review-plan` with only the paid provider doubled, and then the real `just
check-plan`, the engine's `plan check` and the registered check it spawns.

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
from typing import Any

import plan_fixture_source
import pytest
from project_fixtures import budgeted, reviewed
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator import design_approval
from orchestrator.criteria_guard import APPENDIX
from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

_SEQUENCE = itertools.count()

# llmlint: ignore[suppressions_justified] A budgets record is the template's open JSON answer
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

#: Budgets answers that keep every rule.
ANSWERS: Record = {
    "workload": "2,000 nodes per plan, 3 runs at once.",
    "checklist": [
        {"concern": "latency", "budget": "listing-latency", "not_applicable": ""},
        {"concern": "spend", "budget": "", "not_applicable": "n/a because it calls no paid API"},
    ],
    "ten_x": "The listing slows first; listing-latency covers it.",
    "budgets": [
        {
            "id": "listing-latency",
            "repository": REPOSITORY,
            "file": "apps/web/budgets.yaml",
            "measure": "time to the first page in the browser",
            "inner_measure_reason": "",
            "unit": "ms",
            "direction": "max",
            "threshold": 800,
            "workload": "2,000 nodes",
            "evidence": "spike-listing measured 420 ms",
            "command": "bun run measure:listing",
            "node": NODE,
            "file_change": "add",
        }
    ],
    "repo_wide_effects": [{"repository": REPOSITORY, "budget": "gate-time", "effect": "+20 s"}],
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


def _drafted(answers: Record | None, *, stamp: object = None) -> str:
    """One reviewed plan of one node, with its budgets document when ``answers`` is given."""
    native = f"budgets-check-{os.getpid()}-{next(_SEQUENCE)}"
    task = (
        "## What\n\nPage the node listing.\n\n## Why\n\nAn operator cannot see past the "
        f"first screen.\n\n## Acceptance criteria\n\n{CRITERIA}\n\n"
        f"{(REPO_ROOT / APPENDIX).read_text(encoding='utf-8').strip()}\n"
    )
    write_plan_project(
        plan_fixture_source.root(),
        {
            "schema_version": 3,
            "goal": {"text": "Page the node listing"},
            "name": native,
            "tasks": [
                {
                    "id": NODE,
                    "persona": "engineer",
                    "repo": f"https://{REPOSITORY}",
                    "title": "feat: page the node listing",
                    "task": task,
                }
            ],
        },
        native_id=native,
        project_metadata=None if stamp is None else {design_approval.PLAN_KIND: stamp},
    )
    if answers is not None:
        budgeted(plan_fixture_source.SOURCE, native, answers)
    return reviewed(f"{plan_fixture_source.SOURCE}:{native}")


def _check(project: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", "check-plan", project],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )


def test_a_plan_whose_budgets_document_keeps_every_rule_passes() -> None:
    project = _drafted(ANSWERS)

    checked = _check(project)

    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "budgets" not in checked.stderr, checked.stderr


def test_a_plan_carrying_no_budgets_document_is_refused_naming_the_document() -> None:
    project = _drafted(None)

    checked = _check(project)

    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert f"{project} carries no `{project}-budgets` document" in checked.stderr
    assert "rendered from the `plan-budgets` template" in checked.stderr
    assert "config/budgets-migration.yaml" in checked.stderr


@pytest.mark.parametrize(
    ("answers", "rule"),
    [
        (
            _broken(checklist={"budget": "", "not_applicable": ""}),
            "answers neither of `budget` and `not_applicable`",
        ),
        (
            _broken(checklist={"not_applicable": "n/a because it is already fast"}),
            "answers both of `budget` and `not_applicable`",
        ),
        (
            _broken(checklist={"budget": "no-such-budget"}),
            "names the budget 'no-such-budget', which no entry of `budgets` has as its `id`",
        ),
        (_broken(budgets=ANSWERS["budgets"] * 2), "the budget id(s) 'listing-latency' repeat"),
        (
            _broken(budgets={"node": "nowhere"}),
            "is owned by the node 'nowhere', which is no node of the plan",
        ),
        (
            _broken(budgets={"file_change": "rewrite"}),
            "has the `file_change` 'rewrite', which is not one of add, change, none",
        ),
        (
            _broken(budgets={"file": "apps/web/budgets.toml"}),
            "goes in the file 'apps/web/budgets.toml', which is not a relative path whose last "
            "component is `budgets.yaml`",
        ),
        (_broken(budgets={"direction": "under"}), "has the `direction` 'under', which is not max"),
        (_broken(budgets={"command": ""}), "names no `command`"),
        (
            _broken(repo_wide_effects=[]),
            f"`repo_wide_effects` states no effect for {REPOSITORY}, a repository the plan's "
            "tasks change",
        ),
    ],
    ids=[
        "checklist-neither",
        "checklist-both",
        "checklist-unknown-budget",
        "repeated-id",
        "unknown-node",
        "file-change",
        "file",
        "direction",
        "command",
        "repo-wide-effects",
    ],
)
def test_each_budgets_rule_is_refused_naming_the_document_and_the_rule(
    answers: Record, rule: str
) -> None:
    project = _drafted(answers)

    checked = _check(project)

    assert checked.returncode == 1, checked.stdout + checked.stderr
    refusals = [line for line in checked.stderr.splitlines() if f"{project}-budgets: " in line]
    assert len(refusals) == 1, checked.stderr
    assert rule in refusals[0], refusals[0]


def test_a_planning_launchs_project_passes_with_no_budgets_document() -> None:
    """The plan kinds exempt from design approval are exempt here, on the same bound."""
    stamp = {
        design_approval.STAMP_KIND: design_approval.PLANNING,
        design_approval.STAMP_NODES: [NODE],
    }
    project = _drafted(None, stamp=stamp)
    listed = subprocess.run(
        [str(ONETASKGRAPH_BIN), "document", "list", "--source", plan_fixture_source.SOURCE]
        + ["--project", project.partition(":")[2], "--json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert listed.returncode == 0, listed.stderr
    assert json.loads(listed.stdout)["items"] == [], "the exempt project holds a document"

    checked = _check(project)

    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "budgets" not in checked.stderr, checked.stderr
