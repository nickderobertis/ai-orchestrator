"""A plan task's `## Goals` section, and the criteria its budgets derive, through the real pipe.

`templates/plan-task.md.j2` declares `goals`, a list of strings rendered as `## Goals` right
after `## Why`, and renders one acceptance criterion per budget the task adds or changes, so
the judge, which is bound only by `## Acceptance criteria`, holds the worker to it without
any criterion restating it. Everything here is read through the boundary a planner uses: the
pinned engine's `template resolve plan-task --json` piped into the pinned plan store's `task
create` and `task render`, and then the real `just check-plan` — the engine's loader under
`require_rendered` and the registered check behind it — over a copy of the shipped examples,
whose source is exempt from the review-record refusals and nothing else.

What a task rendered before `goals` existed looks like is rendered rather than copied: the
template as it stood at :data:`PREVIOUS_PLAN_TASK`, put in a template root of its own beside
every other template in force, through the same pipe.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] These journeys sit in
`plan-tooling-check-and-review` beside the shipped-examples and check-plan journeys they
extend, and drive the same surface — the real recipe, the registered check, the installed
engine and store — under that project's `planToolingCheckAndReviewWorkspace`, which names
the templates and the tracked `examples/` they read. A project of their own would be keyed on
those same inputs and add only a target.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import boundary_home
import plan_fixture_source
import pytest
from project_fixtures import register_stand_in
from waits import timeout as e2e_timeout

from orchestrator import plan_budgets, plan_check, plan_store
from orchestrator.record_shapes import hosted_origin
from orchestrator.root import REPO_ROOT


@pytest.fixture(autouse=True)
def _boundary_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    boundary_home.answering(monkeypatch)


ENGINE = REPO_ROOT / "node_modules" / ".bin" / "onepipeline"
TEMPLATES = REPO_ROOT / "templates"
PLAN_TASK = TEMPLATES / "plan-task.md.j2"

#: The last commit whose plan-task template declared no `goals` and derived no criterion.
PREVIOUS_PLAN_TASK = "b412af54f51570dd2367672fbedb497f4a55d1fb"

#: The source the examples ship in, and the variable the store reads its root from.
EXAMPLES = plan_check.REVIEW_EXEMPT_SOURCE
EXAMPLES_ROOT = f"ONETASKGRAPH_SOURCES__{EXAMPLES.upper()}__CONFIG__ROOT"

#: What the two review refusals say, the only refusals a moved bar is expected to raise.
REVIEW_REFUSALS = ("no review record for its current authored content", "no plan-level review")

#: Two goals, one functional and one non-functional naming the budget behind it.
GOALS = [
    "An operator polling `GET /health` learns the deployed build without reading logs.",
    "A poll answers within the `health-endpoint-p95-latency` budget.",
]

#: The criteria a planner authors for the tasks below.
AUTHORED = [
    "The route accepts a valid request and rejects an invalid one.",
    "A request-level test drives the route end to end and covers both paths.",
]

#: A budget-free task's answers, as a planner gives them before this change.
BUDGET_FREE: dict[str, object] = {
    "what": "Add the paginated listing and the test that drives it.",
    "why": "An operator cannot see past the first screen of nodes.",
    "acceptance_criteria": AUTHORED,
}

REPOSITORY = "github.com/nickderobertis/some-service"


def _budget(
    identifier: str, file: str, change: str, command: str, workload: str
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "id": identifier,
        "name": f"Budget {identifier}",
        "basis": "estimate",
        "repository": REPOSITORY,
        "file": file,
        "file_change": change,
        "measure": f"the figure {identifier} measures, with its breakdown",
        "inner_measure_reason": "",
        "unit": "ms",
        "direction": "max",
        "threshold": 50,
        "workload": workload,
        "evidence": "Estimate from the handler's shape.",
        "command": command,
        "source": "telemetry",
        "check_runtime_seconds": 3,
    }


#: One budget of each kind a task's record can hold, each with a distinct workload.
PROJECT_ADD = _budget(
    "api-p95-latency",
    "services/api/budgets.yaml",
    "add",
    "python scripts/budgets/api_latency.py",
    "200 sequential API requests",
)
PROJECT_CHANGE = _budget(
    "web-first-page",
    "services/web/budgets.yaml",
    "change",
    "python scripts/budgets/web_first_page.py",
    "2,000 nodes behind the cursor",
)
ROOT_ADD = _budget(
    "health-endpoint-p95-latency",
    "budgets.yaml",
    "add",
    "python scripts/budgets/health_latency.py .budgets/health-requests.jsonl",
    "three probes polling every 10 s for five minutes",
)
ROOT_GATE = _budget(
    "gate-time",
    "budgets.yaml",
    "change",
    "scripts/budget-gate-time.sh",
    "one complete pre-push gate on a publishing push",
)
UNCHANGED = _budget(
    "listing-memory",
    "services/api/budgets.yaml",
    "none",
    "python scripts/budgets/listing_memory.py",
    "one listing of 2,000 nodes",
)
FIVE = [PROJECT_ADD, PROJECT_CHANGE, ROOT_ADD, ROOT_GATE, UNCHANGED]


def _run(
    command: list[str],
    environment: dict[str, str] | None = None,
    stdin: str | None = None,
    seconds: int = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=environment if environment is not None else dict(os.environ),
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
        timeout=e2e_timeout(seconds),
    )


def _loader(root: Path = TEMPLATES) -> str:
    resolved = _run(
        [str(ENGINE), "template", "resolve", "plan-task", "--json"],
        {**os.environ, "ONEPIPELINE_TEMPLATE_ROOT": str(root)},
    )
    assert resolved.returncode == 0, resolved.stderr
    return resolved.stdout


def _previous_root(tmp_path: Path) -> Path:
    """A template root differing from this checkout's in the plan-task template alone, as it was."""
    previous = tmp_path / "previous-templates"
    previous.mkdir(exist_ok=True)
    for entry in TEMPLATES.iterdir():
        if entry.name != PLAN_TASK.name and not (previous / entry.name).exists():
            (previous / entry.name).symlink_to(entry)
    shown = _run(["git", "show", f"{PREVIOUS_PLAN_TASK}:templates/{PLAN_TASK.name}"])
    assert shown.returncode == 0, shown.stderr
    assert "goals" not in shown.stdout, "the previous template already declares goals"
    (previous / PLAN_TASK.name).write_text(shown.stdout, encoding="utf-8")
    return previous


def _create(answers: dict[str, object], tmp_path: Path, root: Path = TEMPLATES) -> str:
    """Create one task from ``answers`` through `resolve | task create`, and return its body."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(answers), encoding="utf-8")
    project = f"goals-{tmp_path.name}"[:60].lower()
    created = _run(
        [str(plan_store.locked_binary()), "task", "create", plan_fixture_source.SOURCE]
        + ["--project", project, "--title", "feat: page the listing"]
        + ["--template-loader", "-", "--answers", str(path), "--no-interactive", "--json"],
        stdin=_loader(root),
    )
    assert created.returncode == 0, created.stdout + created.stderr
    (item,) = json.loads(created.stdout)["items"]
    return str(item["item"]["content"])


# llmlint: ignore[suppressions_justified] `task show --json` is onetaskgraph's open item shape;
# each caller reads the one field it asserts on, so a moved shape fails there.
def _show(identifier: str, environment: dict[str, str] | None = None) -> dict[str, Any]:
    """One task's item as the pinned store shows it."""
    shown = _run(
        [str(plan_store.locked_binary()), "task", "show", identifier, "--json"], environment
    )
    assert shown.returncode == 0, shown.stderr
    (item,) = json.loads(shown.stdout)["items"]
    held: dict[str, Any] = item["item"]
    return held


def _headings(body: str) -> list[str]:
    return re.findall(r"^## .+$", body, re.MULTILINE)


def _criteria(body: str) -> list[str]:
    """Each bullet of the task's own `## Acceptance criteria` section, in order."""
    section = body.split("## Acceptance criteria\n", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^- (.+)$", section, re.MULTILINE)


def test_the_store_lists_goals_as_an_optional_list_of_strings_saying_what_a_goal_is() -> None:
    listed = _run(
        [str(plan_store.locked_binary()), "template", "variables", "--template-loader", "-"]
        + ["--json"],
        stdin=_loader(),
    )
    assert listed.returncode == 0, listed.stderr
    (goals,) = [one for one in json.loads(listed.stdout)["variables"] if one["name"] == "goals"]
    assert (goals["type"], goals.get("items"), goals["required"]) == ("list", "string", False)
    assert goals["default"] == []
    description = " ".join(goals["description"].split())
    for phrase in (
        "a concrete, checkable outcome that must be true when the work is done",
        "distinct from the Why",
        "from the acceptance criteria",
        "speed, latency, CPU, memory, disk, harness quota or cost",
        "names the `id` of the budget behind it",
        "one a task of the plan owns, or an existing root budget",
        "by acceptance criteria, by budgets, or by both",
        "relate many to many, with no pairing assumed",
    ):
        assert phrase in description, f"{phrase!r} is not in:\n{description}"


def test_two_goals_render_right_after_why_ahead_of_budgets_and_criteria(tmp_path: Path) -> None:
    body = _create({**BUDGET_FREE, "goals": GOALS, "budgets": [ROOT_ADD]}, tmp_path)

    headings = _headings(body)
    assert headings[:5] == [
        "## What",
        "## Why",
        "## Goals",
        "## Budgets",
        "## Acceptance criteria",
    ], headings
    section = body.split("## Goals\n", 1)[1].split("\n## ", 1)[0]
    assert section == "\n" + "".join(f"- {goal}\n" for goal in GOALS), section


@pytest.mark.parametrize("goals", ["absent", "empty"])
def test_a_budget_free_task_with_no_goals_renders_what_the_previous_template_rendered(
    tmp_path: Path, goals: str
) -> None:
    """Compared against a rendering under the template as it stood, never a copy of the new one."""
    answers = dict(BUDGET_FREE) if goals == "absent" else {**BUDGET_FREE, "goals": []}
    before = _create(BUDGET_FREE, tmp_path / "before", _previous_root(tmp_path))
    now = _create(answers, tmp_path / "now")

    assert now == before
    assert "## Goals" not in now


def test_five_budgets_derive_one_criterion_each_for_the_four_the_task_adds_or_changes(
    tmp_path: Path,
) -> None:
    body = _create({**BUDGET_FREE, "budgets": FIVE}, tmp_path)

    criteria = _criteria(body)
    assert criteria[: len(AUTHORED)] == AUTHORED, criteria
    derived = criteria[len(AUTHORED) :]
    assert len(derived) == 4, derived
    for criterion, budget in zip(derived, FIVE[:4], strict=True):
        assert f"Budget `{budget['id']}` is registered in `{budget['file']}`" in criterion
        assert f"({budget['workload']})" in criterion, criterion
        assert "as `## Budgets` states it" in criterion, criterion
    assert all(UNCHANGED["id"] not in criterion for criterion in criteria), criteria

    for project in derived[:2]:
        assert "gets onebudgetspec's own verdict for that budget within its target" in project
        assert "the same rule the gate applies and no stricter or looser" in project
        assert "only warns on that budget (one labelled `host-variable`)" in project
        assert "an overrun does not fail this criterion" in project
        assert "the measured figure, the threshold and the host load" in project
        assert "complete gate" not in project, project

    for root in derived[2:]:
        assert "with its command, and that command has run over the finished tree" in root
        assert "the figure it measured, the threshold and onebudgetspec's verdict" in root
        assert "no within-target result is demanded" in root, root
        assert "within its target" not in root, root
        assert (
            "Where its registered command is the repository's complete gate, or its figure is "
            "taken only by running that gate, only its registration with its command is "
            "required and no figure is demanded"
        ) in root, root


def test_a_task_owning_only_an_unchanged_budget_derives_no_criterion(tmp_path: Path) -> None:
    body = _create({**BUDGET_FREE, "budgets": [UNCHANGED]}, tmp_path)
    assert _criteria(body) == AUTHORED


def _environment(tmp_path: Path, project: str) -> dict[str, str]:
    """An environment reading a copy of the examples, a stand-in registered per origin."""
    home = boundary_home.declared_home(tmp_path / "onevcs")
    environment = boundary_home.environment(None, home, transports=False)
    environment.pop(plan_check.REQUIRE_RENDERED_ENV, None)
    copied = tmp_path / EXAMPLES
    shutil.copytree(REPO_ROOT / EXAMPLES, copied)
    environment[EXAMPLES_ROOT] = str(copied)
    origins = sorted(
        {
            hosted_origin(task.repositories[0]) or task.repositories[0]
            for task in plan_store.read_tasks(project)
            if task.repositories
        }
    )
    for index, origin in enumerate(origins):
        register_stand_in(home, origin, tmp_path / f"stand-in-{index}")
    return environment


def _check_plan(project: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return _run(["just", "check-plan", project], environment, seconds=300)


def _refusals(checked: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in checked.stderr.splitlines() if line.startswith("check-plan: ")]


def _render(
    identifier: str,
    environment: dict[str, str],
    root: Path = TEMPLATES,
    answers: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Regenerate one task through `resolve | task render`, from its stored answers."""
    extra = ["--answers", str(answers)] if answers is not None else []
    return _run(
        [
            "bash",
            "-c",
            "set -euo pipefail; "
            '"$1" template resolve plan-task --json | "$2" task render "$3" '
            '--template-loader - --no-interactive "${@:4}"',
            "render",
            str(ENGINE),
            str(plan_store.locked_binary()),
            identifier,
            *extra,
        ],
        {**environment, "ONEPIPELINE_TEMPLATE_ROOT": str(root)},
    )


def test_a_task_rendered_before_goals_is_refused_then_regenerates_byte_identical(
    tmp_path: Path,
) -> None:
    """The moved chain refuses an old rendering; its stored answers regenerate it as it was."""
    project = f"{EXAMPLES}:plan-example"
    identifier = f"{project}/api"
    environment = _environment(tmp_path, project)
    # The record as it was shipped before `goals` existed, whose stored answers name none,
    # rendered again under the chain of that time.
    record = tmp_path / EXAMPLES / "tasks" / "plan-example" / "api.md"
    shipped = _run(["git", "show", f"{PREVIOUS_PLAN_TASK}:{EXAMPLES}/tasks/plan-example/api.md"])
    assert shipped.returncode == 0, shipped.stderr
    record.write_text(shipped.stdout, encoding="utf-8")
    rendered = _render(identifier, environment, _previous_root(tmp_path))
    assert rendered.returncode == 0, rendered.stdout + rendered.stderr
    stored = _show(identifier, environment)
    assert stored["metadata"][plan_budgets.TASK_RECORD] == [], "the task owns a budget"
    answered = _run([str(plan_store.locked_binary()), "task", "answers", identifier], environment)
    assert answered.returncode == 0, answered.stderr
    assert "goals" not in answered.stdout, answered.stdout
    before = stored["content"]

    refused = _check_plan(project, environment)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert any("template changed since rendering" in line for line in _refusals(refused)), (
        refused.stderr
    )

    regenerated = _render(identifier, environment)
    assert regenerated.returncode == 0, regenerated.stdout + regenerated.stderr
    assert _show(identifier, environment)["content"] == before

    checked = _check_plan(project, environment)
    unexpected = [
        line
        for line in _refusals(checked)
        if not any(refusal in line for refusal in REVIEW_REFUSALS)
    ]
    assert not unexpected, checked.stderr
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_check_plan_accepts_a_task_rendered_with_five_budgets_and_its_derived_criteria(
    tmp_path: Path,
) -> None:
    project = f"{EXAMPLES}:health-endpoint"
    identifier = f"{project}/health"
    environment = _environment(tmp_path, project)
    store = str(plan_store.locked_binary())
    recorded = _run(
        [store, "task", "metadata", "set", identifier, plan_budgets.TASK_RECORD, json.dumps(FIVE)],
        environment,
    )
    assert recorded.returncode == 0, recorded.stderr
    answers = tmp_path / "budgets.json"
    answers.write_text(json.dumps({"budgets": FIVE}), encoding="utf-8")
    rendered = _render(identifier, environment, answers=answers)
    assert rendered.returncode == 0, rendered.stdout + rendered.stderr
    body = str(_show(identifier, environment)["content"])
    assert sum("is registered in `" in line for line in _criteria(body)) == 4, body

    checked = _check_plan(project, environment)
    unexpected = [
        line
        for line in _refusals(checked)
        if not any(refusal in line for refusal in REVIEW_REFUSALS)
    ]
    assert not unexpected, checked.stderr
    assert checked.returncode == 0, checked.stdout + checked.stderr
