"""Measure the Planned tasks graph a design document shows, over four committed plan shapes.

The measurement behind the `design-doc-dag-label-size` and `design-doc-dag-natural-width`
budgets `orchestrator/budgets.yaml` registers, run by `design-doc-dag-label-size.sh` and
`design-doc-dag-natural-width.sh` beside this file. Each fixture under `fixtures/` here — 10,
21, 48 and 100 tasks copying real plan shapes — is first held to how it was made, then loaded
into a scratch `local-md` store through this repository's own record renderer, read by the
pinned plan store's real `onetaskgraph project graph --format mermaid --direction auto
--group-by orchestrator.unit` exactly as `scripts/finish-plan.sh` reads it, and drawn:

* to a PNG by the real `just render-diagram`, as the flow draws it, for the width a reader
  sees it at; and
* to an SVG by the same locked mermaid-cli under the same committed `config/mermaid/` files,
  font and page width, for its natural width — the `viewBox` before fitting to the page —
  and the size its labels are drawn at.

**How a fixture was made**, and what each is held to before it is measured: every task has
one or two prerequisites from the rank before and a 66-79-character title, the ranks hold the
tasks :data:`SHAPES` states, and each task's `orchestrator.unit` follows the fixture unit rule
— the root task is its own unit, each rank-1 task starts one, and each later task joins its
first prerequisite's unit. A fixture that drifted from that would measure a picture no plan
draws, so it is refused rather than measured.

`label-size` reports the smallest on-screen label size across the fixtures, in pixels: the
label font size times the displayed width (the PNG's, capped at a board column's 880 px)
over the natural width, capped at the font size. `natural-width` reports the largest natural
width. Each reports its figure and each fixture's breakdown through the onebudgetspec SDK, and
never compares with a threshold: `orchestrator/budgets.yaml` is the one statement of each.
Both run whole, through `onebudgetspec check`, as the `orchestrator` project's cached
`budgets` target whenever anything they read moves, which is what proves the figures;
`test_design_doc_dag.py` beside this file drives each step, its refusals and both modes over a
small fixture through the same real tools.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

from onebudgetspec_sdk import report

from orchestrator import plan_units, planned_tasks
from orchestrator.plan_store import NodeId
from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT


class Shape(NamedTuple):
    """How one fixture was made: the tasks each rank holds, and the units the rule gives it."""

    ranks: tuple[int, ...]
    units: int


class FixtureTask(NamedTuple):
    """One task of a fixture, once held to how it was made."""

    id: NodeId
    title: str
    depends_on: tuple[NodeId, ...]
    unit: str


#: Each fixture by its task count.
SHAPES: Mapping[int, Shape] = {
    10: Shape((1, 4, 2, 1, 2), 5),
    21: Shape((1, 13, 4, 2, 1), 14),
    48: Shape((1, 28, 15, 4), 29),
    100: Shape((1, 56, 33, 10), 57),
}
FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: The script whose page width a board column is, and every render here is laid out at.
RENDER_SCRIPT = REPO_ROOT / "scripts" / "render-diagram.sh"

#: The scratch source each fixture is loaded into.
SOURCE = "dagbudget"

#: Where an SVG states its natural size and its labels' font size.
VIEW_BOX = re.compile(r'viewBox="[-\d.]+ [-\d.]+ ([\d.]+) ([\d.]+)"')
FONT_SIZE = re.compile(r"font-size:\s*([\d.]+)px")

#: What a PNG opens with, and where its header states its width and height.
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_HEADER = slice(16, 24)


class Unmeasured(Exception):
    """A measurement that could not be taken, saying what to repair."""


def page_width() -> int:
    """The page width `scripts/render-diagram.sh` declares: a board column's, which a picture is
    fitted to and an SVG is laid out at too."""
    script = RENDER_SCRIPT.read_text(encoding="utf-8")
    found = re.search(r"^PAGE_WIDTH=(\d+)$", script, re.MULTILINE)
    if found is None or not int(found.group(1)):
        raise Unmeasured(
            "scripts/render-diagram.sh declares no positive PAGE_WIDTH; restore it with "
            "'git checkout -- scripts/render-diagram.sh' and retry"
        )
    return int(found.group(1))


def run(command: list[str], repair: str, env: Mapping[str, str] | None = None) -> str:
    """``command``'s output, or :class:`Unmeasured` naming its failure and ``repair``."""
    done = subprocess.run(
        command, cwd=REPO_ROOT, env=env, text=True, capture_output=True, check=False
    )
    if done.returncode != 0:
        raise Unmeasured(
            f"{' '.join(command[:4])} exited {done.returncode}: {done.stderr.strip()}; {repair}"
        )
    return done.stdout


def shaped(tasks: int, shape: Shape, fixtures: Path) -> list[FixtureTask]:
    """The fixture of ``tasks`` tasks under ``fixtures``, held to ``shape``, as its tasks."""
    named = fixtures / f"plan-{tasks}.json"
    restore = f"restore it with 'git checkout -- orchestrator/budgets/fixtures/{named.name}'"
    document = json.loads(named.read_text(encoding="utf-8"))
    held = document.get("tasks") if isinstance(document, dict) else None
    if not isinstance(held, list) or len(held) != tasks:
        raise Unmeasured(f"{named} does not hold {tasks} tasks; {restore}")
    rank: dict[str, int] = {}
    made: list[FixtureTask] = []
    for task in held:
        match task:
            case {
                "id": str() as node,
                "title": str() as title,
                "depends_on": list() as deps,
                "unit": str() as unit,
            } if (
                66 <= len(title) <= 79
                and len(deps) <= 2
                and all(isinstance(dep, str) and dep in rank for dep in deps)
                and plan_units.problem(unit) is None
            ):
                made.append(
                    FixtureTask(NodeId(node), title, tuple(NodeId(dep) for dep in deps), unit)
                )
            case _:
                raise Unmeasured(f"{named} holds a task not shaped as a fixture task; {restore}")
        rank[node] = 1 + max(rank[dep] for dep in deps) if deps else 0
    unit_of = {task.id: task.unit for task in made}
    for task in made:
        if any(rank[dep] != rank[task.id] - 1 for dep in task.depends_on) or (
            rank[task.id] > 1 and task.unit != unit_of[task.depends_on[0]]
        ):
            raise Unmeasured(f"{named}'s task {task.id} breaks the fixture rule; {restore}")
    starting = [task.unit for task in made if rank[task.id] <= 1]
    counted = tuple(sorted(rank.values()).count(at) for at in range(len(shape.ranks)))
    if (
        counted != shape.ranks
        or len(set(starting)) != len(starting)
        or len(set(unit_of.values())) != shape.units
    ):
        raise Unmeasured(f"{named}'s ranks or units are not the ones it was made with; {restore}")
    return made


def load(tasks: int, shape: Shape, fixtures: Path, store: Path) -> str:
    """Load the fixture of ``tasks`` tasks into the scratch store, and answer its project id."""
    native = f"plan-{tasks}"
    write_plan_project(
        store,
        {
            "name": native,
            "tasks": [
                {
                    "id": task.id,
                    "title": task.title,
                    "unit": task.unit,
                    "deps": list(task.depends_on),
                    "task": "## What\n\nA fixture task.\n",
                }
                for task in shaped(tasks, shape, fixtures)
            ],
        },
        native_id=native,
    )
    return f"{SOURCE}:{native}"


def png_size(png: Path) -> tuple[int, int]:
    """The width and height a PNG's header states, or :class:`Unmeasured` for no PNG."""
    held = png.read_bytes()
    if len(held) < PNG_HEADER.stop:
        held = b""
    wide = int.from_bytes(held[PNG_HEADER][:4], "big")
    high = int.from_bytes(held[PNG_HEADER][4:], "big")
    if not held.startswith(PNG_SIGNATURE) or held[12:16] != b"IHDR" or not wide or not high:
        raise Unmeasured(
            f"{png} is not a PNG with a width and a height; re-provision the renderer with "
            "'just bootstrap' and retry"
        )
    return wide, high


def svg_size(svg: Path, named: str) -> tuple[float, float, float]:
    """The natural width and height an SVG states and its label font size, all positive."""
    drawn = svg.read_text(encoding="utf-8")
    box, font = VIEW_BOX.search(drawn), FONT_SIZE.search(drawn)
    natural = float(box.group(1)) if box else 0.0
    tall = float(box.group(2)) if box else 0.0
    size = float(font.group(1)) if font else 0.0
    if not natural or not tall or not size:
        raise Unmeasured(
            f"the SVG of {named} states no positive viewBox or label font size, so "
            "mermaid-cli drew nothing measurable; re-provision with 'just bootstrap', "
            "or restore config/mermaid/ from this checkout's history, and retry"
        )
    return natural, tall, size


def label_size(size: float, png_width: int, natural: float, column: int) -> float:
    """The on-screen size of a label drawn at ``size`` once its picture is fitted to ``column``.

    The picture shows at the PNG's width capped at the column, and its labels shrink by that
    width over its natural one; a picture narrower than its natural width never grows them.
    """
    return min(size, size * min(png_width, column) / natural)


def measure(mode: str, shapes: Mapping[int, Shape], fixtures: Path) -> tuple[float, str]:
    """The figure ``mode`` names across the fixtures ``shapes`` names, and each's breakdown."""
    with tempfile.TemporaryDirectory(prefix="budget-design-doc-dag-") as scratch:
        work = Path(scratch)
        store = work / "store"
        store.mkdir()
        env = {
            **os.environ,
            f"ONETASKGRAPH_SOURCES__{SOURCE.upper()}__PLUGIN": "local-md",
            f"ONETASKGRAPH_SOURCES__{SOURCE.upper()}__CONFIG__ROOT": str(store),
        }
        provision = "provision the checkout with 'just bootstrap' and retry"
        browser = run(
            ["node", "-e", 'process.stdout.write(require("playwright").chromium.executablePath())'],
            provision,
        )
        puppeteer = work / "puppeteer.json"
        puppeteer.write_text(json.dumps({"executablePath": browser}), encoding="utf-8")
        figures: list[float] = []
        parts: list[str] = []
        for tasks, shape in shapes.items():
            project = load(tasks, shape, fixtures, store)
            read = [str(REPO_ROOT / ".venv" / "bin" / "onetaskgraph"), "project", "graph"]
            read += [project, "--direction", "auto", "--group-by", plan_units.UNIT_KEY]
            mermaid = work / f"plan-{tasks}.mmd"
            mermaid.write_text(run([*read, "--format", "mermaid"], provision, env), "utf-8")
            graph = json.loads(run([*read, "--format", "json"], provision, env))
            read_graph = planned_tasks.parse_graph(graph)
            svg = work / f"plan-{tasks}.svg"
            run(
                [str(REPO_ROOT / "node_modules" / ".bin" / "mmdc"), "--quiet"]
                + ["--input", str(mermaid), "--output", str(svg), "--width", str(page_width())]
                + ["--configFile", str(REPO_ROOT / "config" / "mermaid" / "config.json")]
                + ["--cssFile", str(REPO_ROOT / "config" / "mermaid" / "render.css")]
                + ["--puppeteerConfigFile", str(puppeteer)],
                provision,
                {**os.environ, "FONTCONFIG_FILE": str(REPO_ROOT / "config/mermaid/fonts.conf")},
            )
            natural, tall, size = svg_size(svg, f"plan-{tasks}")
            if mode == "natural-width":
                units = len({node.group for node in read_graph.nodes if node.group})
                figures.append(natural)
                parts.append(
                    f"plan-{tasks}: {natural:.0f} x {tall:.0f} px natural, "
                    f"{read_graph.direction}, {units} units"
                )
                continue
            png = work / f"plan-{tasks}.png"
            run(
                ["just", "--justfile", str(REPO_ROOT / "justfile"), "render-diagram"]
                + [str(mermaid), str(png)],
                "repair what render-diagram names, then retry",
            )
            wide, high = png_size(png)
            label = label_size(size, wide, natural, page_width())
            figures.append(label)
            parts.append(
                f"plan-{tasks}: {label:.1f} px labels, PNG {wide} x {high} px, "
                f"{png.stat().st_size} bytes"
            )
        value = min(figures) if mode == "label-size" else max(figures)
        return round(value, 1), "; ".join(parts)


def main(argv: list[str]) -> int:
    if argv not in (["label-size"], ["natural-width"]):
        print(
            "design-doc-dag: name the one figure to measure, label-size or natural-width; run "
            "it as the budget's command through 'onebudgetspec check'",
            file=sys.stderr,
        )
        return 2
    try:
        report(*measure(argv[0], SHAPES, FIXTURES))
    except Unmeasured as exc:
        said = str(exc)
    except (OSError, ValueError) as exc:
        said = (
            f"{exc}; restore orchestrator/budgets/fixtures/ from this checkout's history and "
            "provision the checkout with 'just bootstrap', then retry"
        )
    else:
        return 0
    print(
        f"design-doc-dag: the {argv[0]} of the Planned tasks graph could not be measured: {said}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
