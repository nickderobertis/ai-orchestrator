"""The design document's Planned-tasks table, rendered from the store's own graph of a plan.

A plan's two tasks are written to a scratch `local-md` source, the second depending on a task
of another project, so the store's `project graph --format json` answers that task as an
external node. The design document is then rendered from it by the real
`scripts/onepipeline.sh template resolve design-doc` and the locked `onetaskgraph`, as a
design writer renders it, with what each task delivers answered. The table holds one row per
task of the plan — its unit, what it delivers and what it depends on by title — and none for
the external task.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml
from planning_bench import local_source

from orchestrator.root import REPO_ROOT

STORE = str(REPO_ROOT / ".venv" / "bin" / "onetaskgraph")


def _run(environment: dict[str, str], *argv: str, stdin: str | None = None) -> str:
    done = subprocess.run(  # noqa: S603 - this checkout's own wrapper and locked plan store
        list(argv),
        cwd=REPO_ROOT,
        env=environment,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, f"{argv}:\n{done.stdout}\n{done.stderr}"
    return done.stdout


def _task(environment: dict[str, str], project: str, title: str, unit: str, *deps: str) -> str:
    argv = [STORE, "task", "create", "scratch", "--project", project, "--title", title]
    argv += ["--metadata", f"orchestrator.unit={json.dumps(unit)}", "--json", "--no-interactive"]
    for dep in deps:
        argv += ["--depends-on", dep]
    return str(json.loads(_run(environment, *argv, stdin="## What\n\nA task.\n"))["items"][0]["id"])


def test_the_table_holds_each_task_of_the_plan_and_none_outside_it(tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.mkdir()
    environment = os.environ | local_source("scratch", root)
    for project in ("plan", "other"):
        _run(
            environment,
            STORE,
            "project",
            "create",
            "scratch",
            "--id",
            project,
            "--title",
            project,
            "--body-file",
            "/dev/null",
            "--no-interactive",
        )
    outside = _task(environment, "other", "feat: the other plan's schema", "Schema")
    route = _task(environment, "plan", "feat: add the health route", "API")
    docs = _task(environment, "plan", "docs: document the health route", "Docs", route, outside)
    graph = _run(
        environment,
        STORE,
        "project",
        "graph",
        "scratch:plan",
        "--format",
        "json",
        "--group-by",
        "orchestrator.unit",
        "--no-interactive",
    )
    assert outside in graph, graph
    answers = tmp_path / "answers.yaml"
    answers.write_text(
        yaml.safe_dump(
            {
                "what": "W",
                "why": "Y",
                "architecture": "A",
                "units": [],
                "acceptance_criteria": ["Operators can poll the route."],
                "graph": json.loads(graph),
                "delivers": {route: "the route itself", docs: "the operator guide"},
            }
        ),
        encoding="utf-8",
    )
    loader = _run(
        environment,
        str(REPO_ROOT / "scripts" / "onepipeline.sh"),
        "template",
        "resolve",
        "design-doc",
        "--json",
    )
    created = _run(
        environment,
        STORE,
        "document",
        "create",
        "scratch",
        "--project",
        "scratch:plan",
        "--id",
        "plan-design",
        "--title",
        "Design",
        "--template-loader",
        "-",
        "--answers",
        str(answers),
        "--json",
        "--no-interactive",
        stdin=loader,
    )
    assert json.loads(created)["errors"] == [], created
    content = (root / "documents" / "plan-design.md").read_text(encoding="utf-8")
    table = content.split("## Planned tasks", 1)[1]
    assert "<summary>All 2 planned tasks</summary>" in table, table[:600]
    rows = [line for line in table.splitlines() if line.startswith("| ") and "---" not in line]
    cells = {
        row[0]: row
        for row in ([cell.strip() for cell in line.strip("|").split("|")] for line in rows[1:])
    }
    assert set(cells) == {"feat: add the health route", "docs: document the health route"}, rows
    assert cells["feat: add the health route"][1:4] == ["API", "the route itself", "none"], rows
    assert cells["docs: document the health route"][1:3] == ["Docs", "the operator guide"], rows
    assert "feat: add the health route" in cells["docs: document the health route"][3], rows
    assert "feat: the other plan's schema" not in [row[0] for row in cells.values()], rows
