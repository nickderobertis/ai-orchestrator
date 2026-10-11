"""`just check-plan` and `just finish-plan` refuse a task whose unit breaks its rule.

A task's unit is the one record the design document's graph, its Planned tasks table and
its Architecture read, so a task without a valid `orchestrator.unit` is refused before any
design document is written: by `just check-plan`, naming the task and the store's own
`task metadata set` command, and by `just finish-plan`, which stops at that check before
anything is launched. There is no fallback.

Everything is real: the plan written through this repository's own record renderer into
this process's own fixture source, the unit set through the store's own `task metadata set`
as a planner sets it, the review recorded by the real `just review-plan` with only the paid
provider doubled, and the real recipes, the engine's `plan check` and the registered check.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] These journeys sit in
`plan-tooling-plan-flow` beside the finish-plan journeys whose bench they reuse, and drive the
same surface — the real recipes, the registered check, the installed engine and store — under
the inputs `planToolingPlanFlowWorkspace` names, which `tests/plan_tooling/AGENTS.md` states as
that project's split.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
from pathlib import Path

import plan_fixture_source
import pytest
from project_fixtures import budgeted, no_budgets, reviewed
from published_tools import ONETASKGRAPH_BIN
from test_finish_plan_recipe_e2e import PASSES, PLAN_REFUSED, STATES_ITS_BAR, _bench, _task
from waits import timeout as e2e_timeout

from orchestrator import plan_units
from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

_SEQUENCE = itertools.count()

#: The one node of each plan here.
NODE = "page-the-listing"

#: Each way a unit breaks its rule, as the JSON the store is told to hold (`None` for no key
#: at all), and the words the refusal says it in.
BROKEN = {
    "absent": (None, "carries no `orchestrator.unit` metadata"),
    "null": ("null", "holds null under `orchestrator.unit`, which is not a JSON string"),
    "number": ("7", "holds 7 under `orchestrator.unit`, which is not a JSON string"),
    "empty": ('""', "holds an empty string under `orchestrator.unit`"),
    "two lines": (json.dumps("Plan\ngraph"), "a string of more than one line"),
    "61 characters": (json.dumps("x" * 61), "a string of 61 characters under"),
}


def _store(*arguments: str) -> str:
    done = subprocess.run(
        [str(ONETASKGRAPH_BIN), *arguments],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


def _drafted(unit: str | None) -> tuple[str, str]:
    """One reviewed plan whose task holds the JSON ``unit``, and that task's qualified id.

    The unit is set after the review, as a planner may set it on a task that exists: it is
    not part of what a review covers, so the plan reaches the check carrying a record.
    """
    native = f"units-check-{os.getpid()}-{next(_SEQUENCE)}"
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
                    "title": "feat: page the node listing",
                    "task": _task(STATES_ITS_BAR),
                }
            ],
        },
        native_id=native,
    )
    project = f"{plan_fixture_source.SOURCE}:{native}"
    budgeted(plan_fixture_source.SOURCE, native, no_budgets([]))
    reviewed(project)
    listed = json.loads(
        _store(
            "task", "list", "--source", plan_fixture_source.SOURCE, "--project", native, "--json"
        )
    )
    (task,) = [held["id"] for held in listed["items"]]
    if unit is not None:
        _store("task", "metadata", "set", task, plan_units.UNIT_KEY, unit)
    return project, task


def _just(
    *arguments: str, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )


@pytest.mark.parametrize("name", list(BROKEN))
def test_a_unit_breaking_its_rule_is_refused_by_the_check_and_stops_the_tail(
    name: str, tmp_path: Path, oneharness_bin: str
) -> None:
    """The check names the task and the command; the tail stops there and writes nothing."""
    unit, said = BROKEN[name]
    project, task = _drafted(unit)

    checked = _just("check-plan", project)
    assert checked.returncode == 1, checked.stdout + checked.stderr
    (line,) = [one for one in checked.stderr.splitlines() if plan_units.UNIT_KEY in one]
    assert line.startswith(f"check-plan: scripts/plan-check.sh: {NODE}: metadata: "), line
    assert said in line, line
    assert f"`{plan_units.set_command(task)}`" in line, line

    bench = _bench(tmp_path, oneharness_bin, PASSES)
    brief = tmp_path / "brief.md"
    brief.write_text(
        f"## What\nPage the listing.\n\nPlan project: {project}\n\n"
        "## Why\nAn operator cannot see past the first screen.\n\n"
        "## Acceptance criteria\n- The listing pages.\n",
        encoding="utf-8",
    )
    finished = _just(
        "finish-plan",
        str(brief),
        "--name",
        f"units-{os.getpid()}-{next(_SEQUENCE)}",
        "--to",
        "destination",
        environment=bench.environment,
    )
    assert finished.returncode == PLAN_REFUSED, finished.stdout + finished.stderr
    assert f": {NODE}: metadata: " in finished.stderr and said in finished.stderr
    assert "so no design document was launched and nothing was copied" in finished.stderr
    assert "launching run" not in finished.stderr, finished.stderr
    source, _, native = project.partition(":")
    documents = json.loads(
        _store("document", "list", "--source", source, "--project", native, "--json")
    )
    assert documents["items"] == [], documents
    assert not list(bench.destination.rglob("*.md"))


def test_a_one_line_unit_of_sixty_characters_passes_the_check() -> None:
    project, _ = _drafted(json.dumps("x" * plan_units.UNIT_MAX_CHARS))

    checked = _just("check-plan", project)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert plan_units.UNIT_KEY not in checked.stderr, checked.stderr
