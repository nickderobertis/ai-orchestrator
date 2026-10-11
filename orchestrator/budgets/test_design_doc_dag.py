"""The design-document graph budgets' measurement, `design_doc_dag.py`, driven through real tools.

Each measurement below loads a fixture into a scratch `local-md` store with this repository's
record renderer, reads it through the pinned plan store's real `project graph`, and draws it
with the locked mermaid-cli and `just render-diagram`, exactly as the budgets do; only the
fixture is smaller — four tasks in three units — so a run costs a second or two rather than
the budgets' four large plans. The refusals are each driven by the input that causes them:
a fixture that breaks how it was made, a render that is not a PNG, an SVG stating no size, a
command that fails, and a render script that declares no page width.

llmlint: ignore-file[shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker] The
subject is the budgets' measurement, which `budgets_scoped_to_minimal_tree` keeps under
`orchestrator/`, and what it measures is a render: the store and mermaid-cli it spawns are the
locked installs `uv.lock` and `bun.lock` name, and every file it reads is in `codeWorkspace`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from orchestrator.budgets import design_doc_dag
from orchestrator.budgets.design_doc_dag import Shape, Unmeasured

#: Four tasks in three units: the root its own unit, two rank-1 tasks each starting one, and a
#: rank-2 task joining its first prerequisite's unit — the fixture unit rule, small.
SMALL = Shape((1, 2, 1), 3)
TITLES = (
    "Expose the publication closeout so a reader sees what changed (#1)",
    "Carry the publication closeout on every dispatch this host makes (#2)",
    "Record the publication closeout with one source for its contract (#3)",
    "Add the publication closeout without a second copy of the rule now (#4)",
)


def _tasks() -> list[dict[str, object]]:
    return [
        {"id": "t1", "title": TITLES[0], "depends_on": [], "unit": "Unit 01: root"},
        {"id": "t2", "title": TITLES[1], "depends_on": ["t1"], "unit": "Unit 02: left"},
        {"id": "t3", "title": TITLES[2], "depends_on": ["t1"], "unit": "Unit 03: right"},
        {"id": "t4", "title": TITLES[3], "depends_on": ["t2", "t3"], "unit": "Unit 02: left"},
    ]


#: Nine tasks five across and five ranks deep, so the graph is laid out top-down and wider than
#: a board column: a root, five rank-1 tasks each starting a unit, and a chain below the first
#: joining its unit — wider than the small fixture, so its labels shrink.
WIDE = Shape((1, 5, 1, 1, 1), 6)


def _task(node: str, title: str, deps: list[str], unit: str) -> dict[str, object]:
    return {"id": node, "title": title, "depends_on": deps, "unit": unit}


def _wide() -> list[dict[str, object]]:
    across = [
        _task(f"w{at}", f"{TITLES[1][:-5]} (#{at})", ["w1"], f"Unit 0{at}: across")
        for at in range(2, 7)
    ]
    chain = [
        _task("w7", f"{TITLES[3][:-5]} (#7)", ["w2", "w6"], "Unit 02: across"),
        _task("w8", f"{TITLES[3][:-5]} (#8)", ["w7"], "Unit 02: across"),
        _task("w9", f"{TITLES[3][:-5]} (#9)", ["w8"], "Unit 02: across"),
    ]
    return [_task("w1", TITLES[0], [], "Unit 01: root"), *across, *chain]


def _fixtures(directory: Path, tasks: object = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    held = _tasks() if tasks is None else tasks
    (directory / "plan-4.json").write_text(json.dumps({"tasks": held}), encoding="utf-8")
    (directory / "plan-9.json").write_text(json.dumps({"tasks": _wide()}), encoding="utf-8")
    return directory


def _each(detail: str, pattern: str) -> dict[str, float]:
    """Each fixture's figure in a breakdown, by the fixture it names."""
    return {
        fixture: float(figure) for fixture, figure in re.findall(rf"(plan-\d+): {pattern}", detail)
    }


def test_the_committed_fixtures_are_the_shapes_they_were_made_with() -> None:
    for tasks, shape in design_doc_dag.SHAPES.items():
        made = design_doc_dag.shaped(tasks, shape, design_doc_dag.FIXTURES)
        assert len(made) == tasks
        assert len({task.unit for task in made}) == shape.units


def test_a_label_shrinks_by_the_fitted_width_and_never_grows() -> None:
    """Fitted to the column, a label shrinks by the shown width over the natural width."""
    assert design_doc_dag.label_size(16, 864, 1728, 880) == 8
    # A PNG wider than the column shows at the column's width.
    assert design_doc_dag.label_size(16, 1760, 1760, 880) == 8
    # A picture shown at no less than its natural width keeps its labels' size.
    assert design_doc_dag.label_size(16, 600, 500, 880) == 16


def test_two_fixtures_measure_through_the_real_render_as_their_minimum_and_maximum(
    tmp_path: Path,
) -> None:
    fixtures = _fixtures(tmp_path / "fixtures")
    shapes = {4: SMALL, 9: WIDE}

    label, about = design_doc_dag.measure("label-size", shapes, fixtures)
    width, shown = design_doc_dag.measure("natural-width", shapes, fixtures)

    labels = _each(about, r"([\d.]+) px labels")
    widths = _each(shown, r"(\d+) x \d+ px natural")
    assert set(labels) == set(widths) == {"plan-4", "plan-9"}, (about, shown)
    assert label == min(labels.values()) and width == max(widths.values()), (about, shown)
    assert labels["plan-9"] < labels["plan-4"] <= 16, about
    assert widths["plan-9"] > widths["plan-4"], shown
    assert "plan-4: " in shown and "3 units" in shown and "6 units" in shown, shown


def test_the_command_reports_its_figure_through_the_sdk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    result = tmp_path / "result.json"
    monkeypatch.setenv("ONEBUDGETSPEC_RESULT", str(result))
    monkeypatch.setattr(design_doc_dag, "SHAPES", {4: SMALL})
    monkeypatch.setattr(design_doc_dag, "FIXTURES", _fixtures(tmp_path / "fixtures"))

    assert design_doc_dag.main(["natural-width"]) == 0
    reported = json.loads(result.read_text(encoding="utf-8"))
    assert reported["value"] > 0 and reported["detail"].startswith("plan-4: "), reported

    assert design_doc_dag.main(["sideways"]) == 2
    assert "name the one figure to measure" in capsys.readouterr().err


def _set(index: int, key: str, value: object) -> object:
    """The small fixture's tasks with one field of one task changed."""
    tasks = _tasks()
    tasks[index][key] = value
    return tasks


#: Each way a fixture can drift from how it was made, as the tasks it then holds.
BROKEN: dict[str, object] = {
    "not a list": "tasks",
    "too few": _tasks()[:3],
    "not a task": [*_tasks()[:3], "t4"],
    "short title": _set(0, "title", "Too short"),
    "unknown prerequisite": _set(1, "depends_on", ["t9"]),
    "a rank skipped": _set(3, "depends_on", ["t2", "t1"]),
    "a later task in another unit": _set(3, "unit", "Unit 03: right"),
    "two starters share a unit": _set(2, "unit", "Unit 02: left"),
}


@pytest.mark.parametrize(
    ("change", "said"),
    [
        ("not a list", "does not hold 4 tasks"),
        ("too few", "does not hold 4 tasks"),
        ("not a task", "holds a task not shaped as a fixture task"),
        ("short title", "holds a task not shaped as a fixture task"),
        ("unknown prerequisite", "holds a task not shaped as a fixture task"),
        ("a rank skipped", "task t4 breaks the fixture rule"),
        ("a later task in another unit", "task t4 breaks the fixture rule"),
        ("two starters share a unit", "ranks or units are not the ones it was made with"),
    ],
)
def test_a_fixture_that_drifted_from_how_it_was_made_is_refused_naming_its_repair(
    tmp_path: Path, change: str, said: str
) -> None:
    fixtures = _fixtures(tmp_path, BROKEN[change])
    with pytest.raises(Unmeasured, match=said) as refused:
        design_doc_dag.shaped(4, SMALL, fixtures)
    drifted = "plan-4.json"
    assert f"git checkout -- orchestrator/budgets/fixtures/{drifted}" in str(refused.value)


def test_a_fixture_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    (tmp_path / "plan-4.json").write_text("[]", encoding="utf-8")
    with pytest.raises(Unmeasured, match="does not hold 4 tasks"):
        design_doc_dag.shaped(4, SMALL, tmp_path)


def test_a_render_that_is_not_a_png_with_a_size_is_refused(tmp_path: Path) -> None:
    written = tmp_path / "plan-4.png"
    for held in (b"<svg>not a png</svg>", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x01"):
        written.write_bytes(held)
        with pytest.raises(Unmeasured, match="is not a PNG with a width and a height"):
            design_doc_dag.png_size(written)


@pytest.mark.parametrize(
    "drawn",
    [
        '<svg viewBox="0 0 0 10"><style>p{font-size:16px}</style></svg>',
        '<svg viewBox="0 0 640 480"></svg>',
        "<svg></svg>",
    ],
)
def test_an_svg_stating_no_positive_size_is_refused(tmp_path: Path, drawn: str) -> None:
    written = tmp_path / "plan-4.svg"
    written.write_text(drawn, encoding="utf-8")
    with pytest.raises(Unmeasured, match="states no positive viewBox or label font size"):
        design_doc_dag.svg_size(written, "plan-4")


def test_a_failing_command_is_refused_naming_its_exit_and_repair() -> None:
    with pytest.raises(Unmeasured, match=r"sh -c exit 3 exited 3: .*; repair it"):
        design_doc_dag.run(["sh", "-c", "exit 3"], "repair it")


@pytest.mark.parametrize("declared", ["", "PAGE_WIDTH=0\n"], ids=["none", "zero"])
def test_a_render_script_declaring_no_positive_page_width_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declared: str
) -> None:
    script = tmp_path / "render-diagram.sh"
    script.write_text(f"#!/usr/bin/env bash\n{declared}", encoding="utf-8")
    monkeypatch.setattr(design_doc_dag, "RENDER_SCRIPT", script)
    with pytest.raises(Unmeasured, match="declares no positive PAGE_WIDTH"):
        design_doc_dag.page_width()


@pytest.mark.parametrize(
    ("fixtures", "said"),
    [
        ("drifted", "does not hold 4 tasks"),
        ("absent", "provision the checkout with 'just bootstrap'"),
    ],
)
def test_the_command_ends_unmeasured_naming_why_and_the_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fixtures: str,
    said: str,
) -> None:
    monkeypatch.setattr(design_doc_dag, "SHAPES", {4: SMALL})
    held = _fixtures(tmp_path, []) if fixtures == "drifted" else tmp_path / "absent"
    monkeypatch.setattr(design_doc_dag, "FIXTURES", held)

    assert design_doc_dag.main(["label-size"]) == 1
    stated = capsys.readouterr().err
    assert stated.startswith("design-doc-dag: the label-size of the Planned tasks graph"), stated
    assert said in stated, stated
