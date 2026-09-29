"""A plan copied between local Markdown projects is filed under the project it landed as.

A plan here is re-planned by copying its `authoring` project to a successor, and the store
names the copy `<project>-2`. Every task of a plan this repository writes is filed in a
folder of its own project, so its id is scoped to that project — `demo/first` — and before
https://github.com/nickderobertis/onetaskgraph/issues/2449 a copy kept each task's source
id: the copied tasks were written under the *source* project's folder while naming the
copy as their project, and the next read scoped to the copy refused every one of them as
outside it, until somebody repaired the layout by hand. `config/onetaskgraph.version` names
the release carrying the fix, and this drives the copy the way it happens here — the real
`just plans` recipe over the pinned plan-store CLI — from a project `orchestrator/` wrote,
and holds every copied task's id and file to the project it landed as.

`just copy-plan`'s own destination, the `plans` board, is not what this is about: GitHub
assigns a copied issue its id, so a board copy never took the source's.

Everything is real: the recipe, `scripts/plan-store.sh`, the installed `onetaskgraph`, and
the local Markdown folders it reads and writes, configured through the store's own
`ONETASKGRAPH_` environment layer.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import cast

import pytest
from waits import timeout as e2e_timeout

from orchestrator.project_store import PlanDocument, PlanNode, write_plan_project
from orchestrator.root import REPO_ROOT

#: The source a plan is drafted in, standing in for the gitignored `authoring` root, and a
#: second one standing in for another local folder a plan is moved into.
AUTHORING = "copyauthoring"
ELSEWHERE = "copyelsewhere"
PROJECT = "demo"

PLAN: PlanDocument = {
    "name": PROJECT,
    "tasks": [
        PlanNode(id="first", title="First", task="Do first."),
        PlanNode(id="second", title="Second", task="Do second.", deps=["first"]),
    ],
}


def _source(environment: dict[str, str], name: str, root: Path) -> None:
    environment[f"ONETASKGRAPH_SOURCES__{name.upper()}__PLUGIN"] = "local-md"
    environment[f"ONETASKGRAPH_SOURCES__{name.upper()}__CONFIG__ROOT"] = str(root)


def _plans(environment: dict[str, str], *arguments: str) -> dict[str, object]:
    """Run one plan-store command through `just plans` and answer its JSON."""
    ran = subprocess.run(
        ["just", "plans", *arguments, "--json"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert ran.returncode == 0, f"just plans {' '.join(arguments)}:\n{ran.stdout}\n{ran.stderr}"
    # The plan-store CLI's own `--json` document; its schema is that release's.
    return cast(dict[str, object], json.loads(ran.stdout))


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `plan-tooling` is the leaf
# project keyed on `planToolingWorkspace`, the edge this rule asks for, and the other journeys
# over `just plans` and the plan-store CLI already run behind it. Each case is a second of
# CLI calls into two scratch folders, and that key names the recipe, wrapper script, package
# and pin it drives, so narrowing it would memoize a verdict over a tree never run.
@pytest.mark.parametrize("destination", [AUTHORING, ELSEWHERE], ids=["beside-itself", "elsewhere"])
def test_every_copied_task_belongs_to_the_project_it_was_copied_into(
    tmp_path: Path, destination: str
) -> None:
    """Each copied task's id, `project` and file are the destination project's.

    Beside itself is the incident: the copy lands as `demo-2` in the folder it came from,
    and a task id kept from the source would put it in `tasks/demo/`. Into another folder
    the project keeps its id, and the task must still be filed under it there.
    """
    roots = {AUTHORING: tmp_path / "authoring", ELSEWHERE: tmp_path / "elsewhere"}
    for root in roots.values():
        root.mkdir()
    write_plan_project(roots[AUTHORING], PLAN)
    environment = dict(os.environ)
    for name, root in roots.items():
        _source(environment, name, root)

    copied = _plans(environment, "project", "copy", f"{AUTHORING}:{PROJECT}", "--to", destination)

    outcomes = cast(list[dict[str, str]], copied["items"])
    assert [outcome["action"] for outcome in outcomes] == ["created"] * 3, copied
    landed = outcomes[0]["destination"]
    assert landed.startswith(f"{destination}:"), copied
    project = landed.removeprefix(f"{destination}:")
    if destination == AUTHORING:
        assert project != PROJECT, f"a copy beside its source took the source's id: {copied}"
    tasks = [outcome["destination"] for outcome in outcomes[1:]]
    assert sorted(tasks) == [f"{landed}/first", f"{landed}/second"], copied

    folder = (roots[destination] / "tasks" / project).resolve()
    for task in tasks:
        answered = _plans(environment, "task", "show", task)
        [held] = cast(list[dict[str, dict[str, object]]], answered["items"])
        shown = held["item"]
        assert shown["project"] == project, answered
        location = cast(dict[str, str], shown["location"])["path"]
        assert Path(location).resolve().parent == folder, (
            f"{task} is stored at {location}, not under {folder}"
        )
    edges = _plans(environment, "task", "deps", f"{landed}/second")
    targets = [
        cast(dict[str, dict[str, str]], edge)["to"]["id"]
        for edge in cast(list[object], edges["items"])
    ]
    assert targets == [f"{landed}/first"], edges
    assert sorted(path.name for path in (roots[AUTHORING] / "tasks" / PROJECT).iterdir()) == [
        "first.md",
        "second.md",
    ]


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
