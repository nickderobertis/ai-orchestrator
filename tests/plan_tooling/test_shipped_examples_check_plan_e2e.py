"""Every shipped example plan passes `just check-plan`, the check its readers are taught.

The examples are the first thing `README.md` sends a reader to, so an example the check
refuses is one a reader cannot validate without authoring budget answers and re-rendering
tasks first. These journeys run the real recipe — the engine's own loader under
`require_rendered`, then this repository's registered check — over the tracked `examples`
source, and hold every project there but the plan flow's own output to passing with no
entry in `config/budgets-migration.yaml`. The one exemption the examples have is from the two
review-record refusals (`orchestrator/plan_check.py`'s `REVIEW_EXEMPT_SOURCE`), so the same
records are driven under another local source, where those refusals stand, and with one
rule broken in a copy, where every other refusal still applies.

A lifecycle plan is checked and launched only once each repository it names has a registered
checkout, because the loader resolves a task's template through that checkout; `README.md`
states this as the reader's setup beside the examples. The repositories the examples name are
fictional, so each journey models that setup by registering a layerless checkout per origin, in
a registry of its own — the chain a checkout without an override of its own resolves to — and
one journey holds the precondition itself: with nothing registered, the examples naming no
repository pass and every one naming a repository is refused for it.
"""

# llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge, test_tiers_split_by_project_not_by_marker] These journeys sit in `plan-tooling` beside the check-plan journeys they extend and drive the same surface — the real recipe, the registered check, the installed engine and store. They read the tracked `examples/`, which only the whole-workspace `test-docs` target is keyed on, so they carry `reads_docs`, which routes between `plan-tooling`'s own two targets and never out of the project (`tests/conftest.py`'s `READS_DOCS_MARKER`); a project of their own would be keyed on that same input and add only a target.  # noqa: E501

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
from pathlib import Path

import plan_fixture_source
import pytest
from project_fixtures import older_engine, register_stand_in
from waits import timeout as e2e_timeout

from orchestrator import plan_budgets, plan_check, plan_store
from orchestrator.project_store import hosted_origin
from orchestrator.root import REPO_ROOT

#: The source the examples ship in, which the exemption is stated for.
EXAMPLES = plan_check.REVIEW_EXEMPT_SOURCE

#: The variable the plan store reads the examples source's root from, overriding the file.
EXAMPLES_ROOT = f"ONETASKGRAPH_SOURCES__{EXAMPLES.upper()}__CONFIG__ROOT"

#: The one shipped project these journeys do not hold to `check-plan`: it is the plan flow's
#: own output, byte for byte what `just plan examples/planner-brief.example.md` writes — its
#: one node is the manager's hand-written brief, which no template renders, so that launch
#: alone names `--require-rendered false` — and `tests/e2e/test_plan_recipe_e2e.py` holds it
#: to that. It is excluded here rather than exempted in the check.
PLAN_FLOW_OUTPUT = f"{EXAMPLES}:planner-brief-example"

#: The example copied byte for byte into another local source, and its records' paths there.
COPIED = "health-endpoint"

#: What the two review refusals say, which only a project outside the examples source earns.
TASK_REVIEW_REFUSAL = "no review record for its current authored content"
PLAN_REVIEW_REFUSAL = "no plan-level review record"


def _held_examples() -> list[str]:
    projects = [p for p in plan_store.local_projects(EXAMPLES) if p != PLAN_FLOW_OUTPUT]
    assert projects, f"the {EXAMPLES!r} source ships no project, so this proves nothing"
    return projects


def _snapshot(root: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _registry(tmp_path: Path, projects: list[str]) -> dict[str, str]:
    """An environment whose `onevcs` registry holds a stand-in for each origin ``projects`` name."""
    home = tmp_path / "onevcs"
    environment = {**os.environ, "ONEVCS_HOME": str(home)}
    # The suite loads its hand-written plans without `require_rendered`; these journeys are
    # about the examples being renderings, so they check them as a reader's launch would.
    environment.pop(plan_check.REQUIRE_RENDERED_ENV, None)
    origins = sorted(
        {
            hosted_origin(task.repositories[0]) or task.repositories[0]
            for project in projects
            for task in plan_store.read_tasks(project)
            if task.repositories
        }
    )
    for index, origin in enumerate(origins):
        register_stand_in(home, origin, tmp_path / f"stand-in-{index}")
    return environment


def _check_plan(project: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", "check-plan", project],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=e2e_timeout(300),
    )


def _break_the_budget(project: str, environment: dict[str, str]) -> None:
    """Double the health task's budget target in its record, leaving its body as rendered."""
    (task,) = [t for t in plan_store.read_tasks(project) if t.node_id == "health"]
    (budget,) = plan_budgets.parse_budgets(plan_budgets.task_record(task.metadata))
    changed = {**dataclasses.asdict(budget), "threshold": budget.threshold * 2}
    subprocess.run(
        [str(plan_store.locked_binary()), "task", "metadata", "set"]
        + [f"{project}/health", plan_budgets.TASK_RECORD, json.dumps([changed])],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )


def _refusals(checked: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in checked.stderr.splitlines() if line.startswith("check-plan: ")]


@pytest.mark.reads_docs
def test_every_shipped_example_passes_check_plan_with_no_migration_entry(tmp_path: Path) -> None:
    """Each example passes the real recipe as shipped, and none is a migrated plan."""
    projects = _held_examples()
    migrated = {entry.project for entry in plan_budgets.migration_entries()}
    assert not migrated & set(plan_store.local_projects(EXAMPLES)), (
        "config/budgets-migration.yaml names a shipped example, so its budgets are not checked"
    )
    environment = _registry(tmp_path, projects)
    tracked = REPO_ROOT / EXAMPLES
    before = _snapshot(tracked)

    failed = []
    for project in projects:
        checked = _check_plan(project, environment)
        if checked.returncode != 0 or "held to no review record" not in checked.stdout:
            failed.append(f"{project} (exit {checked.returncode}):\n{checked.stderr}")
    assert not failed, "shipped examples `just check-plan` refuses:\n" + "\n".join(failed)
    assert _snapshot(tracked) == before, "checking the examples rewrote a tracked record"


@pytest.mark.reads_docs
def test_an_example_naming_a_repository_is_checked_once_that_repository_is_registered(
    tmp_path: Path,
) -> None:
    """The reader setup the journeys above model, held from its other side.

    Against an empty registry, an example whose tasks name no repository passes as shipped,
    and one naming a repository is refused by the engine's loader for that repository being
    unregistered — the precondition `README.md` tells a reader to meet — and for nothing else.
    """
    environment = {**os.environ, "ONEVCS_HOME": str(tmp_path / "empty-registry")}
    environment.pop(plan_check.REQUIRE_RENDERED_ENV, None)
    named = {
        project: [task for task in plan_store.read_tasks(project) if task.repositories]
        for project in _held_examples()
    }
    assert any(named.values()) and not all(named.values()), named

    for project, tasks in named.items():
        checked = _check_plan(project, environment)
        if not tasks:
            assert checked.returncode == 0, checked.stdout + checked.stderr
            continue
        assert checked.returncode == 1, checked.stdout + checked.stderr
        refusals = [line for line in _refusals(checked) if "check-plan: engine:" in line]
        assert refusals, checked.stderr
        assert all("is not a registered repository" in line for line in refusals), refusals


@pytest.mark.reads_docs
def test_the_same_records_under_another_local_source_are_refused_for_their_review_alone(
    tmp_path: Path,
) -> None:
    """Outside the examples source the review refusals stand, and they are the only ones.

    The example's project and task records are copied byte for byte into this process's own
    `test-fixtures` source, another local source `onetaskgraph.yaml` configures.
    """
    environment = _registry(tmp_path, [f"{EXAMPLES}:{COPIED}"])
    other = plan_fixture_source.root()
    (other / "projects").mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / EXAMPLES / "projects" / f"{COPIED}.md", other / "projects")
    shutil.copytree(REPO_ROOT / EXAMPLES / "tasks" / COPIED, other / "tasks" / COPIED)

    checked = _check_plan(f"{plan_fixture_source.SOURCE}:{COPIED}", environment)

    assert checked.returncode == 1, checked.stdout + checked.stderr
    refusals = _refusals(checked)
    assert any(TASK_REVIEW_REFUSAL in line for line in refusals), checked.stderr
    assert any(PLAN_REVIEW_REFUSAL in line for line in refusals), checked.stderr
    assert all(TASK_REVIEW_REFUSAL in line or PLAN_REVIEW_REFUSAL in line for line in refusals), (
        checked.stderr
    )


@pytest.mark.reads_docs
@pytest.mark.parametrize(
    ("broken", "refused"),
    [
        ("budget", "does not carry the `## Budgets` section"),
        ("body", "body differs from its rendering"),
    ],
)
def test_an_example_breaking_another_rule_is_still_refused(
    tmp_path: Path, broken: str, refused: str
) -> None:
    """The exemption lifts the review refusals and nothing else.

    Driven over a copy of the examples, with one rule broken the way a reader editing an
    example would break it: a budget's target changed in its record without regenerating
    the task, or the task's body edited by hand after it was rendered.
    """
    project = f"{EXAMPLES}:health-endpoint"
    environment = _registry(tmp_path, [project])
    copied = tmp_path / EXAMPLES
    shutil.copytree(REPO_ROOT / EXAMPLES, copied)
    environment[EXAMPLES_ROOT] = str(copied)
    record = copied / "tasks" / "health-endpoint" / "health.md"
    if broken == "budget":
        _break_the_budget(project, environment)
    else:
        text = record.read_text(encoding="utf-8")
        record.write_text(text.replace("## Why\n\n", "## Why\n\nEdited by hand. ", 1))

    checked = _check_plan(project, environment)

    assert checked.returncode == 1, checked.stdout + checked.stderr
    refusals = _refusals(checked)
    assert any(refused in line for line in refusals), checked.stderr
    assert not any(
        TASK_REVIEW_REFUSAL in line or PLAN_REVIEW_REFUSAL in line for line in refusals
    ), checked.stderr


@pytest.mark.reads_docs
def test_the_direct_path_holds_the_examples_to_everything_but_a_review_record(
    tmp_path: Path,
) -> None:
    """Against an engine with no `plan check`, an example passes as exempt and a broken one is not.

    The direct path reads no loader, so the rule broken here is one it does read: a budget's
    target changed in its record without regenerating the task.
    """
    project = f"{EXAMPLES}:health-endpoint"
    environment = _registry(tmp_path, [project])
    # llmlint: ignore-block[e2e_not_mocked] The direct path is reached only against an
    # engine carrying no `plan check`, for the reason `older_engine` states.
    environment[plan_check.ENGINE_ENV] = str(older_engine(tmp_path))
    # llmlint: ignore-end[e2e_not_mocked]

    accepted = _check_plan(project, environment)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "as a shipped example it is held to no review record" in accepted.stdout

    copied = tmp_path / EXAMPLES
    shutil.copytree(REPO_ROOT / EXAMPLES, copied)
    environment[EXAMPLES_ROOT] = str(copied)
    _break_the_budget(project, environment)
    refused = _check_plan(project, environment)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "does not carry the `## Budgets` section" in refused.stderr, refused.stderr
    assert TASK_REVIEW_REFUSAL not in refused.stderr, refused.stderr
    assert PLAN_REVIEW_REFUSAL not in refused.stderr, refused.stderr
