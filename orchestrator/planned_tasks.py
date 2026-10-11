"""A design document's Planned tasks rows and Architecture units, derived from the plan's graph.

The design document shows a plan three ways — a graph of its tasks, a table of them and an
Architecture section by unit — and a reader trusts the three only when they cannot
disagree. So none of them is written by the document's writer: the graph is the plan
store's own `onetaskgraph project graph <plan> --group-by orchestrator.unit`, the table's
`task`, `unit`, `depends_on` and `location` columns are derived here from that **same
read's** JSON form, and the Architecture's unit names are exactly the set of the rows'
units. The writer authors each row's `delivers` and nothing else of it.

**Rows come from the verb's JSON, never from its Mermaid text and never by title.** One
row per task of the project, in the order of its `nodes`; `task` is the node's `title`,
`unit` its `group`, `depends_on` the titles of the nodes its incoming edges come `from`, in
`edges` order and joined by :data:`JOIN` (a task outside the project as
`<title> (<qualified id>)`, and none at all as :data:`NO_DEPENDENCY`), and `location` the
plan store's location for the task with that node's qualified `id`. Two tasks sharing a
title are two rows, told apart by that id.

`scripts/finish-plan.sh` runs this twice: before the writer, printing the rows the writer's
task quotes, and after it, with `--check-design-document`, holding the stored document to
them — its rows, its units and its graph image — before anything is copied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal, NamedTuple, TypedDict

from orchestrator import design_approval, plan_store, plan_units
from orchestrator.plan_store import QualifiedTaskId

#: The `schema_version` of the graph verb's JSON form this module reads, and the two
#: directions it resolves a layout to.
GRAPH_SCHEMA_VERSION = 1
DIRECTIONS = frozenset({"td", "lr"})

#: How a row's prerequisites are joined, and what a row with none says.
JOIN = "; "
NO_DEPENDENCY = "none"

#: The four columns this module derives; the writer answers the fifth, `delivers`.
type Column = Literal["task", "unit", "depends_on", "location"]
COLUMNS: tuple[Column, ...] = ("task", "unit", "depends_on", "location")

#: The file name the flow renders the graph to, which is the asset name the document holds
#: it under and its `dag_image` answer.
IMAGE_NAME = "plan-dag.png"


class Row(TypedDict):
    """One derived row of the Planned tasks table."""

    task: str
    unit: str
    depends_on: str
    location: str


class Node(NamedTuple):
    """One node of the graph's JSON form, held to its shape."""

    id: QualifiedTaskId
    title: str
    external: bool
    group: str | None


class Edge(NamedTuple):
    """One dependency the graph draws: the prerequisite it comes from, the task it goes to."""

    source: QualifiedTaskId
    target: QualifiedTaskId


class Graph(NamedTuple):
    """The graph's JSON form: its direction, nodes and edges, each held to its shape, in order."""

    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    direction: str = "td"


class GraphError(ValueError):
    """A graph document that is not the verb's JSON form grouped by unit."""


def parse_graph(document: object) -> Graph:
    """``document`` as a :class:`Graph`, or :class:`GraphError` naming what it is not."""
    if not isinstance(document, Mapping) or document.get("schema_version") != GRAPH_SCHEMA_VERSION:
        raise GraphError(
            f"the graph is not a `project graph --format json` document of schema_version "
            f"{GRAPH_SCHEMA_VERSION}"
        )
    if document.get("group_by") != plan_units.UNIT_KEY:
        raise GraphError(
            f"the graph is grouped by {document.get('group_by')!r}, not by "
            f"`{plan_units.UNIT_KEY}`; read it with `--group-by {plan_units.UNIT_KEY}`"
        )
    direction = document.get("direction")
    if not isinstance(direction, str) or direction not in DIRECTIONS:
        raise GraphError(f"the graph is laid out {direction!r}, not one of {sorted(DIRECTIONS)}")
    nodes, edges = document.get("nodes"), document.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise GraphError("the graph carries no `nodes` list or no `edges` list")
    held: list[Node] = []
    for node in nodes:
        match node:
            case {"id": str() as id_, "title": str() as title, "external": bool() as external}:
                group = node.get("group")
                if group is not None and not isinstance(group, str):
                    raise GraphError(f"the graph's node {id_} has a group that is not a string")
                held.append(Node(QualifiedTaskId(id_), title, external, group))
            case _:
                raise GraphError(
                    f"the graph has a node that is not an id, title and external: {node!r}"
                )
    ids = {node.id for node in held}
    if len(ids) != len(held):
        raise GraphError("the graph names one task id on two nodes")
    pairs: list[Edge] = []
    for edge in edges:
        match edge:
            case {"from": str() as source, "to": str() as target} if {source, target} <= ids:
                pairs.append(Edge(QualifiedTaskId(source), QualifiedTaskId(target)))
            case _:
                raise GraphError(
                    f"the graph has an edge that does not join two of its nodes: {edge!r}"
                )
    return Graph(tuple(held), tuple(pairs), direction)


def tasks_of(graph: Graph) -> list[Node]:
    """The project's own tasks, in the graph's order: every node not outside the project."""
    return [node for node in graph.nodes if not node.external]


def needs_image(graph: Graph) -> bool:
    """Whether the document shows the graph: more than one task, and at least one edge.

    A one-task plan's graph is one box and an edgeless plan's a row of them, each repeating
    the table, so neither has one. An edge from a task outside the project counts.
    """
    return len(tasks_of(graph)) > 1 and bool(graph.edges)


def rows(graph: Graph, locations: Mapping[QualifiedTaskId, str]) -> list[Row]:
    """The canonical rows of ``graph``, each located by its task's qualified id."""
    by_id = {node.id: node for node in graph.nodes}
    derived: list[Row] = []
    for node in tasks_of(graph):
        if node.group is None or plan_units.problem(node.group) is not None:
            raise GraphError(
                f"{node.id} has no unit in the graph; give it one with "
                f"`{plan_units.set_command(node.id)}`, which `just check-plan` asks for"
            )
        if node.id not in locations:
            raise GraphError(f"the plan store holds no task {node.id}, which the graph draws")
        prerequisites = [
            by_id[edge.source].title
            if not by_id[edge.source].external
            else f"{by_id[edge.source].title} ({edge.source})"
            for edge in graph.edges
            if edge.target == node.id
        ]
        derived.append(
            Row(
                task=node.title,
                unit=node.group,
                depends_on=JOIN.join(prerequisites) or NO_DEPENDENCY,
                location=locations[node.id],
            )
        )
    return derived


def units(derived: Sequence[Row]) -> list[str]:
    """The distinct units of ``derived``, in the order each first appears."""
    return list(dict.fromkeys(row["unit"] for row in derived))


def locations(project: str) -> dict[QualifiedTaskId, str]:
    """Where the plan store reports each task of ``project``, by its qualified id.

    The location `python -m orchestrator.plan_budgets` gives a budget's owner too: the
    store's own, or the task's qualified id for a store that reports none.
    """
    return {
        QualifiedTaskId(held.id.model_dump()): plan_store.located(
            held.item.location.model_dump(mode="python") if held.item.location else None,
            held.id.model_dump(),
        )
        for held in plan_store.plan_listing(project)
    }


def read_graph(path: Path) -> Graph:
    """The graph the flow kept at ``path``."""
    try:
        return parse_graph(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise GraphError(f"the graph at {path} could not be read: {exc}") from exc


def row_differences(held: object, expected: Sequence[Row]) -> list[str]:
    """The first place the stored `planned_tasks` answer parts from ``expected``, if any."""
    if not isinstance(held, list):
        return ["it holds no `planned_tasks` list"]
    for position, wanted in enumerate(expected, 1):
        if position > len(held):
            return [
                f"its `planned_tasks` stops at row {len(held)}, and the plan's graph gives row "
                f"{position} to {wanted['task']!r}"
            ]
        written = held[position - 1]
        for column in COLUMNS:
            stated = written.get(column) if isinstance(written, dict) else None
            if stated != wanted[column]:
                return [
                    f"its `planned_tasks` row {position} ({wanted['task']!r}) states the "
                    f"`{column}` {stated!r}, where the plan's graph gives {wanted[column]!r}"
                ]
    if len(held) > len(expected):
        return [
            f"its `planned_tasks` has {len(held)} rows, and the plan's graph has "
            f"{len(expected)} tasks; row {len(expected) + 1} is one no task of the plan gives"
        ]
    return []


def unit_differences(held: object, expected: Sequence[Row]) -> list[str]:
    """Each Architecture unit that is malformed, repeated, or named on one side only."""
    listed = held if isinstance(held, list) else []
    found = [] if isinstance(held, list) else ["it holds no Architecture `units` list"]
    named: list[str] = []
    for position, unit in enumerate(listed, 1):
        match unit:
            case {"name": str(name)} if name in named:
                found.append(f"its Architecture `units` name {name!r} twice")
            case {"name": str(name)}:
                named.append(name)
            case _:
                found.append(f"its Architecture `units` entry {position} names no unit")
    wanted = set(units(expected))
    return (
        found
        + [
            f"its Architecture `units` name {unit!r}, which no task of the plan changes"
            for unit in sorted(set(named) - wanted)
        ]
        + [
            f"its Architecture `units` do not name {unit!r}, which a task of the plan changes"
            for unit in sorted(wanted - set(named))
        ]
    )


def image_differences(
    graph: Graph, answered: Mapping[str, object], held: Mapping[str, str], image: Path | None
) -> list[str]:
    """What separates the document's `dag_image` and its assets from the rendered graph.

    ``held`` is each asset the document holds by name, as the SHA-256 of its bytes.
    """
    named = answered.get("dag_image", "")
    if not needs_image(graph):
        if named:
            return [
                f"it answers `dag_image` {named!r}, and a plan of one task or of no dependency "
                f'edge shows no graph; answer it `""`'
            ]
        return []
    if named != IMAGE_NAME:
        return [
            f"it answers `dag_image` {named!r}, and a plan of more than one task with a "
            f"dependency edge shows the graph the flow rendered, `{IMAGE_NAME}`"
        ]
    # llmlint: ignore[changed_behavior_has_e2e] Unreachable through the plan store as it stands: storing a document whose content references `./plan-dag.png` without that asset is refused by the store itself ("references the asset ./plan-dag.png, which the document … would not hold", measured through `just finish-plan` with the writer giving no asset), so no journey can store one; tests/test_planned_tasks.py drives the guard, which holds a store that ever accepts one.  # noqa: E501
    if IMAGE_NAME not in held:
        return [f"it holds no `{IMAGE_NAME}` asset, the graph the flow rendered"]
    if image is None:
        return [f"no rendered `{IMAGE_NAME}` was named to hold its asset to"]
    if held[IMAGE_NAME] != hashlib.sha256(image.read_bytes()).hexdigest():
        return [f"its `{IMAGE_NAME}` asset differs from the graph the flow rendered at {image}"]
    return []


def design_refusals(project: str, graph: Graph, image: Path | None) -> tuple[str, list[str]]:
    """``project``'s design document, and everything of it the plan's graph refuses."""
    expected = rows(graph, locations(project))
    document = design_approval.design_document(project)
    answered = plan_store.sdk(
        plan_store.client().document_answers(str(document.qualified_id))
    ).model_dump()
    held = {
        asset.name: hashlib.sha256(Path(asset.path).read_bytes()).hexdigest()
        for asset in plan_store.read_document_assets(str(document.qualified_id))
        if asset.path is not None
    }
    found = [
        *row_differences(answered.get("planned_tasks"), expected),
        *unit_differences(answered.get("units"), expected),
        *image_differences(graph, answered, held, image),
    ]
    return str(document.qualified_id), found


def main(argv: Sequence[str] | None = None) -> int:
    """Print a plan's canonical rows, or hold its written design document to them.

    For `scripts/finish-plan.sh`. With no flag, one JSON object at exit 0: `rows`, the
    canonical rows; `units`, their distinct units; and `image`, whether the document shows
    the graph. With `--check-design-document`, nothing at exit 0 when the plan's design
    document agrees with the graph, and each difference on stderr at exit 1. Exit 2 when the
    graph, the plan or its design document cannot be read.
    """
    parser = argparse.ArgumentParser(
        prog="planned-tasks",
        description="Print, or check, the Planned tasks rows a plan's graph gives its document.",
    )
    parser.add_argument("project", metavar="SOURCE:PROJECT")
    parser.add_argument("--graph", type=Path, required=True, metavar="JSON")
    parser.add_argument("--check-design-document", action="store_true")
    parser.add_argument("--image", type=Path, metavar="PNG")
    args = parser.parse_args(argv)
    try:
        graph = read_graph(args.graph)
        if not args.check_design_document:
            derived = rows(graph, locations(args.project))
            answer = {"rows": derived, "units": units(derived), "image": needs_image(graph)}
            print(json.dumps(answer, ensure_ascii=False))
            return 0
        document, differences = design_refusals(args.project, graph, args.image)
    except (OSError, ValueError) as exc:
        print(f"planned-tasks: {exc}", file=sys.stderr)
        return 2
    for difference in differences:
        print(f"planned-tasks: {document}: {difference}", file=sys.stderr)
    return 1 if differences else 0


if __name__ == "__main__":
    raise SystemExit(main())
