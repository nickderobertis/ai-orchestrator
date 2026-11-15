"""A planned task carries the instruction to read the lint rules before implementing.

Rendered the way a planner renders one — the engine's resolution of `plan-task` piped into
the plan store's `task create --template-loader -` — by the real `scripts/onepipeline.sh`
and the locked `onetaskgraph`, into a scratch `local-md` source of the test's own, and its
operational notes read back from the stored record.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from planning_bench import local_source

from orchestrator.root import REPO_ROOT

#: The planned task's answers: the two the template requires, and one criterion.
PLANNED_TASK_ANSWERS = (
    "what: Add a health endpoint to the service.\n"
    "why: Operators need to know the service is up before they route traffic to it.\n"
    "acceptance_criteria:\n"
    "  - The endpoint answers 200 while the service is serving.\n"
)
#: The instruction a planned task's operational notes give before any implementation,
#: whitespace collapsed, as the rendered body states it.
READ_THE_RULES_FIRST = (
    "**Read the lint rules for what you will change before you change it.** Before "
    "implementing, run `llmlint rules --files <the paths you expect to change>` in the "
    "repository you work in"
)


def test_a_task_rendered_as_a_planner_renders_it_tells_its_worker_to_read_the_rules_first(
    tmp_path: Path,
) -> None:
    """The operational notes a dispatch reads come from the task as the store rendered it.

    A planner writes a task by piping the engine's resolution of `plan-task` into the plan
    store's `task create --template-loader -`, and that rendered body is what a worker and
    its judge are handed; the appendix reaches it only through the template's include. So
    a task is rendered that way here, by the real `scripts/onepipeline.sh` and the locked
    `onetaskgraph`, into a scratch `local-md` source, and its notes are read for the
    instruction to run `llmlint rules --files` before implementing: an include dropped, a
    loader resolving another template, or the paragraph rewritten away each fails it.
    """
    root = tmp_path / "store"
    root.mkdir()
    environment = os.environ | local_source("scratch", root)
    loader = subprocess.run(  # noqa: S603 - this repository's own wrapper over the engine
        [str(REPO_ROOT / "scripts" / "onepipeline.sh"), "template", "resolve", "plan-task"]
        + ["--json"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    ).stdout
    store = str(REPO_ROOT / ".venv" / "bin" / "onetaskgraph")
    project = subprocess.run(  # noqa: S603 - the locked plan store, on a scratch source
        [store, "project", "create", "scratch", "--id", "probe", "--title", "Probe"]
        + ["--no-interactive"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert project.returncode == 0, project.stdout + project.stderr
    answers = tmp_path / "answers.yaml"
    answers.write_text(PLANNED_TASK_ANSWERS, encoding="utf-8")
    created = subprocess.run(  # noqa: S603 - the locked plan store, on a scratch source
        [store, "task", "create", "scratch", "--project", "probe", "--title", "Health"]
        + ["--template-loader", "-", "--answers", str(answers), "--no-interactive", "--json"],
        cwd=tmp_path,
        env=environment,
        input=loader,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert created.returncode == 0, created.stdout + created.stderr
    assert json.loads(created.stdout)["errors"] == [], created.stdout
    (task,) = (root / "tasks").glob("*.md")
    body = task.read_text(encoding="utf-8")
    notes = " ".join(body.split("### Operational notes for this host", 1)[1].split())
    assert READ_THE_RULES_FIRST in notes, (
        f"a task rendered from `plan-task` no longer tells its worker, in its operational "
        f"notes, {READ_THE_RULES_FIRST!r}:\n{body}"
    )
