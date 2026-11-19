"""The design document's Planned-tasks table, rendered from the store's own graph of a plan.

A plan's two tasks are written to a scratch `local-md` source, the second depending on a task
of another project, so the store's `project graph --format json` answers that task as an
external node. The design document is then rendered from it by the real
`scripts/onepipeline.sh template resolve design-doc` and the locked `onetaskgraph`, as a
design writer renders it, with what each task delivers answered. The table holds one row per
task of the plan — its unit, what it delivers and what it depends on by title — and none for
the external task, inside one expandable block. A plan of one task or none, and a legacy
`planned_tasks` answer of one row or none, shows its table with no expandable block at all.

Each row's Where-it-lives cell is the location the store's graph reports for the task, written
alone, and a dependency on a task of the plan links its title to that location, so the store's
own `project copy` onto the loopback GitHub board (`tests/github_board.py`) points both at the
issue of the task's copy. A dependency outside the plan stays a plain title, a title holding
`[`, `]` and `|` stays one link in one cell, and a graph answered before nodes carried a
location renders its rows as it always did.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import yaml
from github_board import BOARD, _serving_board
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


def _store(tmp_path: Path) -> tuple[dict[str, str], Path]:
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
    return environment, root


def _graph(environment: dict[str, str]) -> dict[str, object]:
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
    return dict(json.loads(graph))


def _planned_tasks(
    environment: dict[str, str], root: Path, tmp_path: Path, answers: dict[str, object]
) -> str:
    """Render the design document from `answers` and return its Planned-tasks section."""
    written = tmp_path / "answers.yaml"
    base: dict[str, object] = {
        "what": "W",
        "why": "Y",
        "architecture": "A",
        "units": [],
        "acceptance_criteria": ["Operators can poll the route."],
    }
    written.write_text(yaml.safe_dump(base | answers), encoding="utf-8")
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
        str(written),
        "--json",
        "--no-interactive",
        stdin=loader,
    )
    assert json.loads(created)["errors"] == [], created
    content = (root / "documents" / "plan-design.md").read_text(encoding="utf-8")
    return content.split("## Planned tasks", 1)[1]


def _block_lines(section: str) -> list[str]:
    return [
        line
        for line in section.splitlines()
        if line.startswith(("<details>", "<summary>", "</details>"))
    ]


def _inside_block(section: str, count: int) -> str:
    """The section's table as it sits inside its one expandable block, opening to closing."""
    assert _block_lines(section) == [
        "<details>",
        f"<summary>All {count} planned tasks</summary>",
        "</details>",
    ], section[:600]
    return section.split("</summary>", 1)[1].split("</details>", 1)[0]


#: A column delimiter: a `|` no backslash escapes.
_DELIMITER = re.compile(r"(?<!\\)\|")


def _rows(section: str) -> dict[str, list[str]]:
    rows = [line for line in section.splitlines() if line.startswith("| ") and "---" not in line]
    assert rows[0] == "| Task | Unit | What it delivers | Depends on | Where it lives |", rows
    return {
        row[0]: row
        for row in (
            [cell.strip() for cell in _DELIMITER.split(line.strip())[1:-1]] for line in rows[1:]
        )
    }


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] This module's Planned-tasks
# journeys sit in the planning project, which keys its inputs on `templates/`, `scripts/` and
# `config/` whole. Those are the trees these renders read: the design-doc template,
# `scripts/onepipeline.sh template resolve`, and the pins it resolves through.
def test_the_table_holds_each_task_of_the_plan_and_none_outside_it(tmp_path: Path) -> None:
    environment, root = _store(tmp_path)
    outside = _task(environment, "other", "feat: the other plan's schema", "Schema")
    route = _task(environment, "plan", "feat: add the health route", "API")
    docs = _task(environment, "plan", "docs: document the health route", "Docs", route, outside)
    graph = _graph(environment)
    assert outside in json.dumps(graph), graph
    table = _planned_tasks(
        environment,
        root,
        tmp_path,
        {"graph": graph, "delivers": {route: "the route itself", docs: "the operator guide"}},
    )
    cells = _rows(_inside_block(table, 2))
    assert set(cells) == {"feat: add the health route", "docs: document the health route"}, cells
    assert cells["feat: add the health route"][1:4] == ["API", "the route itself", "none"], cells
    assert cells["docs: document the health route"][1:3] == ["Docs", "the operator guide"], cells
    assert "feat: add the health route" in cells["docs: document the health route"][3], cells
    assert "feat: the other plan's schema" not in [row[0] for row in cells.values()], cells


def test_a_plan_of_one_task_or_none_shows_its_table_without_an_expandable_block(
    tmp_path: Path,
) -> None:
    environment, root = _store(tmp_path)
    empty = _planned_tasks(environment, root, tmp_path, {"graph": _graph(environment)})
    assert _block_lines(empty) == [], empty[:600]
    assert _rows(empty) == {}, empty[:600]
    outside = _task(environment, "other", "feat: the other plan's schema", "Schema")
    route = _task(environment, "plan", "feat: add the health route", "API", outside)
    graph = _graph(environment)
    assert outside in json.dumps(graph), graph
    table = _planned_tasks(
        environment, root, tmp_path, {"graph": graph, "delivers": {route: "the route itself"}}
    )
    assert _block_lines(table) == [], table[:600]
    cells = _rows(table)
    assert list(cells) == ["feat: add the health route"], cells
    assert cells["feat: add the health route"][1:3] == ["API", "the route itself"], cells


_LEGACY_ROWS = [
    {
        "task": "feat: add the health route",
        "unit": "API",
        "delivers": "the route itself",
        "depends_on": "",
        "location": "scratch:plan#1",
    },
    {
        "task": "docs: document the health route",
        "unit": "Docs",
        "delivers": "the operator guide",
        "depends_on": "feat: add the health route",
        "location": "scratch:plan#2",
    },
]


def test_a_legacy_answer_of_one_row_shows_no_block_and_of_two_rows_one(tmp_path: Path) -> None:
    environment, root = _store(tmp_path)
    empty = _planned_tasks(environment, root, tmp_path, {"planned_tasks": []})
    assert _block_lines(empty) == [], empty[:600]
    assert _rows(empty) == {}, empty[:600]
    one = _planned_tasks(environment, root, tmp_path, {"planned_tasks": _LEGACY_ROWS[:1]})
    assert _block_lines(one) == [], one[:600]
    assert list(_rows(one)) == ["feat: add the health route"], one[:600]
    two = _planned_tasks(environment, root, tmp_path, {"planned_tasks": _LEGACY_ROWS})
    cells = _rows(_inside_block(two, 2))
    assert list(cells) == ["feat: add the health route", "docs: document the health route"]


def _located(graph: dict[str, object], task: str) -> str:
    """The location the store's ``graph`` reports for ``task``, as its one string."""
    nodes = graph["nodes"]
    assert isinstance(nodes, list), graph
    (node,) = [one for one in nodes if one["id"] == task]
    (where,) = dict(node["location"]).values()
    return str(where)


# llmlint: ignore-block[e2e_not_mocked] GitHub's Projects API is the one boundary doubled,
# because a copy onto it writes to a live board; the template, the pinned engine's `template
# resolve`, the locked plan store's `document create`, `project graph` and `project copy` are real.
def test_the_board_copy_links_each_task_and_in_plan_dependency_to_its_issue(
    tmp_path: Path,
) -> None:
    environment, root = _store(tmp_path)
    outside = _task(environment, "other", "feat: the other plan's schema", "Schema")
    route = _task(environment, "plan", "feat: add the health route", "API")
    docs = _task(environment, "plan", "docs: document the health route", "Docs", route, outside)
    graph = _graph(environment)
    table = _planned_tasks(
        environment,
        root,
        tmp_path,
        {"graph": graph, "delivers": {route: "the route itself", docs: "the operator guide"}},
    )
    authored = _rows(_inside_block(table, 2))
    assert authored["feat: add the health route"][4] == _located(graph, route), authored
    assert authored["docs: document the health route"][3] == (
        f"[feat: add the health route]({_located(graph, route)}); feat: the other plan's schema"
    ), authored

    with _serving_board() as remote:
        board = environment | remote
        _run(board, STORE, "project", "copy", "scratch:plan", "--to", "plans", "--documents")
        issues = {one.title: one for one in BOARD.issues}
        (design,) = [one for title, one in issues.items() if title.endswith("Design")]
        address = {
            title: str(issues[title].content()["url"])
            for title in ("feat: add the health route", "docs: document the health route")
        }
        copied = design.body.split("## Planned tasks", 1)[1]

    cells = _rows(_inside_block(copied, 2))
    for title, url in address.items():
        assert cells[title][4] == url, (title, cells)
    assert cells["docs: document the health route"][3] == (
        f"[feat: add the health route]({address['feat: add the health route']}); "
        "feat: the other plan's schema"
    ), cells
    assert str(root) not in copied, copied
    assert "scratch:" not in copied, copied


# llmlint: ignore-end[e2e_not_mocked]


def test_a_title_holding_brackets_and_a_bar_stays_one_link_in_one_cell(tmp_path: Path) -> None:
    environment, root = _store(tmp_path)
    route = _task(environment, "plan", "feat: add [the] health | route", "API")
    _task(environment, "plan", "docs: document the health route", "Docs", route)
    graph = _graph(environment)
    table = _planned_tasks(environment, root, tmp_path, {"graph": graph})
    cells = _rows(_inside_block(table, 2))
    assert all(len(row) == 5 for row in cells.values()), cells
    assert cells["docs: document the health route"][3] == (
        f"[feat: add \\[the\\] health \\| route]({_located(graph, route)})"
    ), cells


def test_a_graph_whose_nodes_carry_no_location_renders_its_rows_as_before(
    tmp_path: Path,
) -> None:
    environment, root = _store(tmp_path)
    outside = _task(environment, "other", "feat: the other plan's schema", "Schema")
    route = _task(environment, "plan", "feat: add the health route", "API")
    docs = _task(environment, "plan", "docs: document the health route", "Docs", route, outside)
    graph = _graph(environment)
    nodes = graph["nodes"]
    assert isinstance(nodes, list), graph
    stored = graph | {"nodes": [{k: v for k, v in one.items() if k != "location"} for one in nodes]}
    table = _planned_tasks(
        environment,
        root,
        tmp_path,
        {"graph": stored, "delivers": {route: "the route itself", docs: "the operator guide"}},
    )
    rows = [line for line in _inside_block(table, 2).splitlines() if line.startswith("| ")]
    assert rows[2:] == [
        f"| feat: add the health route | API | the route itself | none | {route} |",
        "| docs: document the health route | Docs | the operator guide | "
        f"feat: add the health route; feat: the other plan's schema | {docs} |",
    ], rows


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
