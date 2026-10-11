"""A design document's Planned tasks rows, derived from the plan's graph.

`orchestrator/planned_tasks.py` derives them.

Every plan here is written into this test process's own `test-fixtures` source the way a
planner's records are — each task carrying its `orchestrator.unit` — and its graph is read
by the pinned plan store's real `onetaskgraph project graph --format json --group-by
orchestrator.unit`, the read `scripts/finish-plan.sh` keeps. So what the rows are held to is
the verb's own document, never a hand-written stand-in for it.
`tests/plan_tooling/plan_flow/test_finish_plan_graph_e2e.py` drives the same module through
the real recipe; what is here is each rule and each refusal, one at a time.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] The
subject is `orchestrator/planned_tasks.py`, this tier's own module, and the boundary it reads is
the plan store and its graph verb, so those are what these tests drive rather than a stand-in for
them — as `tests/test_plan_budgets.py` does beside it. The pinned store `uv.lock` names and the
templates it renders are in `codeWorkspace`, and each store they write is this process's own
fixture root.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import plan_fixture_source
import pytest
from project_fixtures import designed
from published_tools import ONETASKGRAPH_BIN

from orchestrator import planned_tasks
from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

_SEQUENCE = itertools.count()


def _store(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ONETASKGRAPH_BIN), *arguments],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _plan(*tasks: dict[str, object]) -> str:
    """Write a plan of ``tasks`` into this process's fixture source, and answer its id."""
    native = f"rows-{os.getpid()}-{next(_SEQUENCE)}"
    write_plan_project(
        plan_fixture_source.root(), {"name": native, "tasks": list(tasks)}, native_id=native
    )
    return f"{plan_fixture_source.SOURCE}:{native}"


# llmlint: ignore[suppressions_justified] The graph verb's JSON form is onetaskgraph's open
# document; the module under test is what narrows it.
def _graph(project: str) -> dict[str, Any]:
    """The plan's graph, as the pinned store's verb prints it, grouped by unit."""
    read = _store(
        "project", "graph", project, "--format", "json", "--group-by", "orchestrator.unit"
    )
    assert read.returncode == 0, read.stderr
    graph: dict[str, Any] = json.loads(read.stdout)
    return graph


def _ids(project: str) -> dict[str, str]:
    """Each task of ``project`` by its node id, as its qualified id."""
    source, _, native = project.partition(":")
    listed = _store("task", "list", "--source", source, "--project", native, "--json")
    assert listed.returncode == 0, listed.stderr
    return {
        held["item"]["metadata"]["onepipeline.id"]: held["id"]
        for held in json.loads(listed.stdout)["items"]
    }


def _where(project: str) -> dict[str, str]:
    """Each task of ``project`` by its node id, at the path the store reports it at."""
    source, _, native = project.partition(":")
    listed = _store("task", "list", "--source", source, "--project", native, "--json")
    return {
        held["item"]["metadata"]["onepipeline.id"]: held["item"]["location"]["path"]
        for held in json.loads(listed.stdout)["items"]
    }


#: A plan with a fan-out, a fan-in, an edge inside a unit and edges between units, and a
#: title carrying the two characters the Mermaid form escapes.
FOUR = (
    {"id": "store", "title": 'Record the "unit" #1', "unit": "Plan store"},
    {"id": "graph", "title": "Draw the graph", "unit": "Plan store", "deps": ["store"]},
    {"id": "rows", "title": "Derive the rows", "unit": "Design flow", "deps": ["store"]},
    {"id": "doc", "title": "Show the graph", "unit": "Template", "deps": ["rows", "graph"]},
)


def test_rows_follow_the_graphs_nodes_titles_groups_and_edges() -> None:
    project = _plan(*FOUR)
    graph = planned_tasks.parse_graph(_graph(project))
    derived = planned_tasks.rows(graph, planned_tasks.locations(project))
    where = _where(project)

    # The graph's own order — topological, ties by title — rather than the records'.
    assert [row["task"] for row in derived] == [
        'Record the "unit" #1',
        "Derive the rows",
        "Draw the graph",
        "Show the graph",
    ], derived
    assert derived[0] == {
        "task": 'Record the "unit" #1',
        "unit": "Plan store",
        "depends_on": "none",
        "location": where["store"],
    }
    assert derived[1]["depends_on"] == 'Record the "unit" #1', derived
    assert derived[1]["unit"] == "Design flow"
    # Its prerequisites in the graph's edge order, which is by the prerequisite's position.
    assert derived[3]["depends_on"] == "Derive the rows; Draw the graph", derived
    assert [row["location"] for row in derived] == [
        where[node] for node in ("store", "rows", "graph", "doc")
    ]
    assert planned_tasks.units(derived) == ["Plan store", "Design flow", "Template"]
    assert planned_tasks.needs_image(graph)


def test_two_tasks_sharing_a_title_are_two_rows_each_with_its_own_edges_and_location() -> None:
    project = _plan(
        {"id": "root", "title": "Start", "unit": "Core"},
        {"id": "left", "title": "Same title", "unit": "Core", "deps": ["root"]},
        {"id": "right", "title": "Same title", "unit": "Edge"},
    )
    graph = planned_tasks.parse_graph(_graph(project))
    derived = planned_tasks.rows(graph, planned_tasks.locations(project))
    ids, where = _ids(project), _where(project)
    by_id = {node.id: index for index, node in enumerate(planned_tasks.tasks_of(graph))}

    left, right = derived[by_id[ids["left"]]], derived[by_id[ids["right"]]]
    assert (left["task"], left["depends_on"], left["location"]) == (
        "Same title",
        "Start",
        where["left"],
    )
    assert (right["task"], right["depends_on"], right["location"]) == (
        "Same title",
        "none",
        where["right"],
    )


def test_a_prerequisite_outside_the_project_is_named_with_its_id_and_draws_a_graph() -> None:
    outside = _plan({"id": "elsewhere", "title": "Released elsewhere", "unit": "Other"})
    project = _plan(
        {"id": "first", "title": "First", "unit": "Core"},
        {"id": "second", "title": "Second", "unit": "Core"},
    )
    (external,) = _ids(outside).values()
    updated = _store("task", "update", _ids(project)["second"], "--depends-on", external)
    assert updated.returncode == 0, updated.stderr

    graph = planned_tasks.parse_graph(_graph(project))
    derived = planned_tasks.rows(graph, planned_tasks.locations(project))
    assert [row["depends_on"] for row in derived] == [
        "none",
        f"Released elsewhere ({external})",
    ]
    assert planned_tasks.needs_image(graph)


@pytest.mark.parametrize(
    "tasks",
    [
        ({"id": "only", "title": "Only", "unit": "Core"},),
        (
            {"id": "a", "title": "A", "unit": "Core"},
            {"id": "b", "title": "B", "unit": "Core"},
            {"id": "c", "title": "C", "unit": "Edge"},
        ),
    ],
    ids=["one task", "no edge"],
)
def test_a_one_task_or_edgeless_plan_shows_no_graph(tasks: tuple[dict[str, object], ...]) -> None:
    project = _plan(*tasks)
    graph = planned_tasks.parse_graph(_graph(project))
    derived = planned_tasks.rows(graph, planned_tasks.locations(project))

    assert not planned_tasks.needs_image(graph)
    assert [row["depends_on"] for row in derived] == ["none"] * len(tasks)


#: A graph document of the verb's shape, for the refusals no conforming verb prints.
# llmlint: ignore[suppressions_justified] The graph verb's JSON form is onetaskgraph's open
# document, which each case below breaks one field at a time; the module under test narrows it.
SHAPED: dict[str, Any] = {
    "schema_version": 1,
    "project": "s:p",
    "direction": "td",
    "group_by": "orchestrator.unit",
    "nodes": [{"key": "n1", "id": "s:p/a", "title": "A", "external": False, "group": "U"}],
    "edges": [],
}


@pytest.mark.parametrize(
    ("change", "said"),
    [
        ({"schema_version": 2}, "schema_version 1"),
        ({"group_by": None}, "read it with `--group-by orchestrator.unit`"),
        ({"direction": "auto"}, "laid out 'auto', not one of"),
        ({"direction": ["td"]}, r"laid out \['td'\], not one of"),
        ({"nodes": None}, "no `nodes` list"),
        ({"nodes": [{"id": "s:p/a"}]}, "a node that is not an id, title and external"),
        (
            {"nodes": [{"id": "s:p/a", "title": "A", "external": False, "group": 3}]},
            "a group that is not a string",
        ),
        ({"nodes": [*SHAPED["nodes"], *SHAPED["nodes"]]}, "one task id on two nodes"),
        ({"edges": [{"from": "s:p/a", "to": "s:p/z"}]}, "does not join two of its nodes"),
    ],
)
def test_a_document_that_is_not_the_verbs_grouped_form_is_refused(
    change: dict[str, object], said: str
) -> None:
    with pytest.raises(planned_tasks.GraphError, match=said):
        planned_tasks.parse_graph({**SHAPED, **change})
    with pytest.raises(planned_tasks.GraphError, match="schema_version"):
        planned_tasks.parse_graph("not a document")


def test_a_task_with_no_unit_or_no_location_has_no_row() -> None:
    ungrouped = planned_tasks.parse_graph(
        {**SHAPED, "nodes": [{**SHAPED["nodes"][0], "group": None}]}
    )
    with pytest.raises(planned_tasks.GraphError, match="task metadata set s:p/a orchestrator.unit"):
        planned_tasks.rows(ungrouped, {"s:p/a": "here"})
    with pytest.raises(planned_tasks.GraphError, match="holds no task s:p/a"):
        planned_tasks.rows(planned_tasks.parse_graph(SHAPED), {})


ROWS: list[planned_tasks.Row] = [
    {"task": "A", "unit": "U", "depends_on": "none", "location": "/a.md"},
    {"task": "B", "unit": "V", "depends_on": "A", "location": "/b.md"},
]


def _written(*changes: dict[str, str]) -> list[dict[str, str]]:
    return [{**row, "delivers": "it", **change} for row, change in zip(ROWS, changes, strict=True)]


@pytest.mark.parametrize(
    ("held", "said"),
    [
        ("rows", "holds no `planned_tasks` list"),
        (_written({}, {"depends_on": "none"}), "row 2 ('B') states the `depends_on` 'none'"),
        (_written({}, {})[:1], "stops at row 1, and the plan's graph gives row 2 to 'B'"),
        (list(reversed(_written({}, {}))), "row 1 ('A') states the `task` 'B'"),
        (_written({"unit": "W"}, {}), "row 1 ('A') states the `unit` 'W', where"),
        (_written({}, {"location": "/a.md"}), "row 2 ('B') states the `location` '/a.md'"),
        ([*_written({}, {}), "extra"], "has 3 rows, and the plan's graph has 2 tasks"),
        (["not a row", *_written({}, {})[1:]], "row 1 ('A') states the `task` None"),
    ],
)
def test_the_first_row_or_column_that_parts_from_the_graph_is_named(
    held: object, said: str
) -> None:
    (difference,) = planned_tasks.row_differences(held, ROWS)
    assert said in difference, difference


def test_rows_restating_the_graph_and_adding_only_delivers_part_from_nothing() -> None:
    assert planned_tasks.row_differences(_written({}, {}), ROWS) == []


def test_a_unit_on_one_side_only_malformed_or_repeated_is_named() -> None:
    assert planned_tasks.unit_differences([{"name": "U"}, {"name": "V"}], ROWS) == []
    assert planned_tasks.unit_differences(
        [{"name": "U"}, {"name": "Nobody's"}, "not a unit", {"name": ["V"]}, {"name": "U"}], ROWS
    ) == [
        "its Architecture `units` entry 3 names no unit",
        "its Architecture `units` entry 4 names no unit",
        "its Architecture `units` name 'U' twice",
        'its Architecture `units` name "Nobody\'s", which no task of the plan changes',
        "its Architecture `units` do not name 'V', which a task of the plan changes",
    ]
    assert planned_tasks.unit_differences(None, ROWS)[0] == "it holds no Architecture `units` list"


EDGED = planned_tasks.parse_graph(
    {
        **SHAPED,
        "nodes": [
            *SHAPED["nodes"],
            {"key": "n2", "id": "s:p/b", "title": "B", "external": False, "group": "V"},
        ],
        "edges": [{"from": "s:p/a", "to": "s:p/b"}],
    }
)


def test_the_graph_image_is_held_to_the_png_the_flow_rendered(tmp_path: Path) -> None:
    rendered = tmp_path / "plan-dag.png"
    rendered.write_bytes(b"\x89PNG rendered")
    digest = hashlib.sha256(rendered.read_bytes()).hexdigest()
    named = {"dag_image": "plan-dag.png"}

    assert planned_tasks.image_differences(EDGED, named, {"plan-dag.png": digest}, rendered) == []
    assert "answers `dag_image` ''" in planned_tasks.image_differences(EDGED, {}, {}, rendered)[0]
    assert (
        "holds no `plan-dag.png` asset"
        in planned_tasks.image_differences(EDGED, named, {}, rendered)[0]
    )
    assert (
        "differs from the graph the flow rendered"
        in planned_tasks.image_differences(EDGED, named, {"plan-dag.png": "0" * 64}, rendered)[0]
    )
    assert (
        "no rendered `plan-dag.png` was named"
        in planned_tasks.image_differences(EDGED, named, {"plan-dag.png": digest}, None)[0]
    )
    flat = planned_tasks.parse_graph(SHAPED)
    assert planned_tasks.image_differences(flat, {"dag_image": ""}, {}, None) == []
    assert "shows no graph" in planned_tasks.image_differences(flat, named, {}, None)[0]


def test_the_command_prints_the_rows_and_holds_a_written_document_to_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both modes over a real plan, its real graph and a document rendered from the template."""
    project = _plan(*FOUR)
    kept = tmp_path / "plan-graph.json"
    kept.write_text(json.dumps(_graph(project)), encoding="utf-8")
    image = tmp_path / "plan-dag.png"
    image.write_bytes(b"\x89PNG the graph")

    assert planned_tasks.main([project, "--graph", str(kept)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["units"] == ["Plan store", "Design flow", "Template"]
    assert printed["image"] is True
    derived: list[dict[str, str]] = printed["rows"]

    source, _, native = project.partition(":")
    units = [
        {"name": name, "repository": "ai-orchestrator", "part": "", "summary": "It changes."}
        | {"reversible": [], "decisions": []}
        for name in printed["units"]
    ]
    designed(
        source,
        native,
        [{**row, "delivers": "it"} for row in derived],
        answers={"units": units, "dag_image": "plan-dag.png"},
        assets=[image],
    )
    assert planned_tasks.main([project, "--graph", str(kept), "--check-design-document"]) == 1
    assert "no rendered `plan-dag.png` was named" in capsys.readouterr().err
    checked = [project, "--graph", str(kept), "--check-design-document", "--image", str(image)]
    assert planned_tasks.main(checked) == 0

    designed(
        source,
        native,
        [{**row, "delivers": "it"} for row in derived[:-1]],
        answers={"units": units, "dag_image": "plan-dag.png"},
        assets=[image],
    )
    assert planned_tasks.main(checked) == 1
    said = capsys.readouterr().err
    assert f"planned-tasks: {source}:{native}-design: its `planned_tasks` stops at row 3" in said


def test_a_graph_that_cannot_be_read_ends_the_command_unrunnable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "absent.json"
    assert planned_tasks.main(["s:p", "--graph", str(missing)]) == 2
    assert f"the graph at {missing} could not be read" in capsys.readouterr().err
