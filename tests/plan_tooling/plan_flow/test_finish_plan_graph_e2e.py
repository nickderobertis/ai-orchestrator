"""`just finish-plan` opens a design document's Planned tasks with the plan's own graph.

The flow reads the plan's graph once, through the plan store's real `onetaskgraph project
graph --group-by orchestrator.unit`, as Mermaid and as JSON; renders the Mermaid with the
real `just render-diagram` for a plan of more than one task with a dependency edge; quotes
the rows `orchestrator/planned_tasks.py` derives from the JSON, their units and that image
into the writer's task; and, once the writer has stored the document, holds its Planned
tasks, its Architecture units and its graph image to that same read before anything is
copied. A document that parts from the graph is refused with the document-refused status,
naming the first difference, and nothing reaches the destination.

The journeys run in a copy of this checkout, because the wrapper runs the engine at its
checkout's own installed command, the one `orchestrator.npm_installs` resolves. Everything there
is real — `scripts/finish-plan.sh`, the review turn's recipe, the check, the graph verb,
mermaid-cli and Chromium, the store at both ends, the template, the row module and `just
approve-design` — except two seams. **The engine's `start` is doubled**, by
`tests/plan_tooling/plan_flow/finish_plan_writer_engine.py`,
which runs the commands the writer's turn runs, because the paid model is the one boundary
these journeys may not reach; and for the two journeys about a graph read that fails, the copy's
store is a wrapper that hands one form's read to the real verb **over a project id the store
does not hold**, so the failure and its words are the real verb's. `tests/e2e/fake_codex.py`
stands in for the provider of the review turn.

llmlint: ignore-file[e2e_not_mocked, tests_mirror_real_usage] The engine's `start` is doubled
because a real one dispatches the paid writer; the failing-read wrapper steers the real verb at
an id the store does not hold, since a plan the check accepts is one both reads answer for, so
no plan alone can make one read fail and not the other. Every recipe, script, store, template,
render and check around them is the real one.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split] The
journeys copy the tracked tree, which reads all of it, so they sit in `plan-tooling-plan-flow`'s
whole-workspace `test-docs` target, as `test_finish_plan_diagram_e2e.py`'s journeys do.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] `reads_docs` chooses between
`plan-tooling-plan-flow`'s own two targets and routes nothing out of the project, as
`tests/plan_tooling/AGENTS.md` states.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple, TypedDict

import plan_root_variable
import pytest
from nx_workspace import (
    REAL_ENGINE,
    answering_this_checkouts_origin,
    copy_working_tree,
    double_the_engine,
    share_this_checkouts_install,
)
from project_fixtures import budgeted, no_budgets
from test_finish_plan_recipe_e2e import (
    AUTHORING_SOURCE,
    COPIES_THE_ROWS,
    DESIGN_TASK_MARKER,
    DESTINATION,
    DOCUMENT_REFUSED,
    MERGES_THE_BUDGETS,
    OK,
    PASSES,
    STATES_ITS_BAR,
    UNRUNNABLE,
    Copy,
    RunId,
    _bench,
    _task,
)
from visual_images import TYPICAL, write_noise_png
from waits import timeout as e2e_timeout

from orchestrator.plan_store import NodeId, QualifiedProjectId
from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

# What the recipes these journeys run leave in the temporary directory, by design or
# from a published tool they run, is this module's own and goes with it
# (`tests/short_state.py`).
pytestmark = [pytest.mark.reads_docs, pytest.mark.usefixtures("module_tmpdir")]

#: The stand-in `start` the copied checkout's engine hands that one verb to.
STAND_IN = Path(__file__).resolve().parent / "finish_plan_writer_engine.py"

#: The copied store's wrapper: every call is the real verb's, and the flow's own grouped
#: graph read in the form `FAKE_GRAPH_ABSENT_FORMAT` names is handed to it over a project id
#: the store does not hold. An ungrouped graph read — the one a plan's task dependencies are
#: read through — is the real verb's, so the refusal is the flow's read and nothing before it.
GRAPH_WRAPPER = """#!/usr/bin/env bash
real="$(dirname "$0")/onetaskgraph-real"
if [ "${1:-} ${2:-}" = "project graph" ] && [ -n "${FAKE_GRAPH_ABSENT_FORMAT:-}" ]; then
    arguments=("$@")
    grouped=no
    absent=no
    for ((at = 0; at < ${#arguments[@]}; at++)); do
        named="${arguments[at + 1]:-}"
        [ "${arguments[at]}" = --group-by ] && grouped=yes
        if [ "${arguments[at]}" = --format ] && [ "$named" = "$FAKE_GRAPH_ABSENT_FORMAT" ]; then
            absent=yes
        fi
    done
    if [ "$grouped" = yes ] && [ "$absent" = yes ]; then
        arguments[2]="${arguments[2]}-absent"
        exec "$real" "${arguments[@]}"
    fi
fi
exec "$real" "$@"
"""

#: The image a Planned tasks section opens with for a plan that shows its graph.
GRAPH_IMAGE = "![Dependency graph of the plan's tasks](./plan-dag.png)"

#: Where the flow says it keeps the graph it read.
KEPT = re.compile(r"the graph of \S+ this flow reads is kept in (\S+)")

_SEQUENCE = itertools.count()


def _checkout(tmp_path: Path) -> Copy:
    """A copy of this checkout whose engine doubles `start` and whose store is wrapped.

    The engine wrapper hands every call to the stand-in, which hands every verb but `start`
    to the real installed command; `just check-plan` reads the roles a
    persona name resolves to out of that binary's own bytes.
    """
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    copy_working_tree(checkout)
    answering_this_checkouts_origin(checkout)
    shared = share_this_checkouts_install(checkout)
    # The copy's own links to the two tools, replaced by doubles; this checkout's stay.
    bins = checkout / ".venv" / "bin"
    double_the_engine(checkout, STAND_IN)
    store = bins / "onetaskgraph"
    store.rename(store.with_name("onetaskgraph-real"))
    store.write_text(GRAPH_WRAPPER, encoding="utf-8")
    store.chmod(0o755)
    return Copy(checkout, shared)


@pytest.fixture(scope="module")
def checkout(tmp_path_factory: pytest.TempPathFactory) -> Copy:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    return _checkout(tmp_path_factory.mktemp("finish-plan-graph"))


class Task(NamedTuple):
    """One task of a plan here: its node id, title, unit and prerequisites."""

    id: NodeId
    title: str
    unit: str
    deps: tuple[NodeId, ...] = ()


#: Four tasks in three units: a fan-out from the first, a fan-in to the last, an edge inside
#: the `Plan store` unit and edges between units, and a title carrying `"` and `#`.
FOUR = (
    Task(NodeId("store"), 'feat: record the "unit" key #1', "Plan store"),
    Task(NodeId("graph"), "feat: draw the grouped graph", "Plan store", (NodeId("store"),)),
    Task(NodeId("rows"), "feat: derive the planned rows", "Design flow", (NodeId("store"),)),
    Task(
        NodeId("show"),
        "feat: show the graph in the document",
        "Template",
        (NodeId("graph"), NodeId("rows")),
    ),
)
ONE = (Task(NodeId("only"), "feat: page the node listing", "Listing"),)
EDGELESS = (
    Task(NodeId("first"), "feat: page the listing", "Listing"),
    Task(NodeId("second"), "feat: sort the listing", "Listing"),
    Task(NodeId("third"), "feat: filter the view", "View"),
)
SAME_TITLE = (
    Task(NodeId("root"), "feat: start the flow", "Core"),
    Task(NodeId("left"), "feat: the same title", "Core", (NodeId("root"),)),
    Task(NodeId("right"), "feat: the same title", "Edge"),
)


def _plan(tasks: Sequence[Task]) -> QualifiedProjectId:
    """Draft ``tasks`` in this process's private authoring source and answer the plan id."""
    native = f"graph-{os.getpid()}-{next(_SEQUENCE)}"
    write_plan_project(
        Path(os.environ[plan_root_variable.name()]),
        {
            "schema_version": 3,
            "goal": {"text": "Show the plan's graph in its design document"},
            "name": native,
            "tasks": [
                {
                    "id": task.id,
                    "persona": "engineer",
                    "title": task.title,
                    "unit": task.unit,
                    "task": _task(STATES_ITS_BAR),
                    **({"deps": list(task.deps)} if task.deps else {}),
                }
                for task in tasks
            ],
        },
        native_id=native,
    )
    budgeted(AUTHORING_SOURCE, native, no_budgets([]))
    return QualifiedProjectId(f"{AUTHORING_SOURCE}:{native}")


#: The staged answers every writer here starts from: the rows, units and image are copied
#: from its task over these by `copy_quoted_rows.py`, and the budgets by `merge_answers.py`.
STAGED: dict[str, object] = {
    "what": "Show the plan's graph above its table.",
    "why": "A reviewer takes the plan in at a glance.",
    "architecture": "The plan store draws the graph; the flow renders it.",
    "units": [],
    "acceptance_criteria": ["The graph opens the Planned tasks section."],
    "planned_tasks": [],
}

#: The writer's turn: copy the budgets, then what its task quotes, then apply this journey's
#: change to the answers and to the asset, and store the document as its task says to.
WRITER = (
    "set -euo pipefail"
    '; printed=$(uv run python -m orchestrator.plan_budgets "$3:$4")'
    '; python3 "$8" "$7" "$printed"'
    '; asset=$(python3 "$9" "$7" "$2" "${10}")'
    '; python3 -c "${11}" "$7"'
    '; case "${12}" in keep) ;; none) asset="" ;; *) asset="${12}" ;; esac'
    '; "$1" template resolve design-doc --json'
    ' | "$2" document create "$3" --project "$4" --title "$5" --id "$6"'
    ' --template-loader - --answers "$7" --no-interactive ${asset:+--asset "$asset"}'
)

#: A change to the answers that changes nothing.
UNCHANGED = "pass"


class Start(TypedDict):
    """One `start` the doubled engine logged: the project, the commands run, the settlement."""

    project: str
    ran: int
    settlement: str


class Canonical(TypedDict):
    """What the row module prints for a graph: its rows, their units, and whether it shows."""

    rows: list[dict[str, str]]
    units: list[str]
    image: bool


class Finished(NamedTuple):
    """One whole tail, and what was read back off it."""

    finish: subprocess.CompletedProcess[str]
    project: QualifiedProjectId
    run: RunId
    graph: Path | None
    starts: list[Start]
    environment: dict[str, str]


def _finish(
    checkout: Copy,
    tmp_path: Path,
    oneharness_bin: str,
    tasks: Sequence[Task],
    *,
    change: str = UNCHANGED,
    asset: str = "keep",
    environment: dict[str, str] | None = None,
) -> Finished:
    """Draft a plan of ``tasks`` and drive the whole tail over it in the copied checkout.

    ``change`` is Python run over the writer's answers file (``a`` is the answers, written
    back afterwards); ``asset`` is `keep` for the image the task names, `none` for no asset,
    or a path given in its place.
    """
    project = _plan(tasks)
    source, _, native = project.partition(":")
    bench = _bench(tmp_path, oneharness_bin, PASSES)
    run = RunId(f"graph-run-{os.getpid()}-{next(_SEQUENCE)}")
    answers = tmp_path / f"{native}.answers.json"
    answers.write_text(json.dumps(STAGED), encoding="utf-8")
    bins = checkout.root / ".venv" / "bin"
    changed = (
        "import json, sys; path = sys.argv[1]; a = json.load(open(path))"
        f"\n{change}\njson.dump(a, open(path, 'w'))"
    )
    turn = tmp_path / f"turn-{native}.json"
    turn.write_text(
        json.dumps(
            {
                DESIGN_TASK_MARKER: [
                    [
                        "bash",
                        "-c",
                        WRITER,
                        "store-the-document",
                        str(REAL_ENGINE),
                        str(bins / "onetaskgraph-real"),
                        source,
                        native,
                        f"Design: {native}",
                        f"{native}-design",
                        str(answers),
                        str(MERGES_THE_BUDGETS),
                        str(COPIES_THE_ROWS),
                        f"{run}-design",
                        changed,
                        asset,
                    ]
                ]
            }
        ),
        encoding="utf-8",
    )
    log = tmp_path / f"starts-{native}.jsonl"
    bench.environment.update(
        {
            "FAKE_ENGINE_REAL": str(REAL_ENGINE),
            "FAKE_ENGINE_STORE": str(bins / "onetaskgraph-real"),
            "FAKE_ENGINE_WRITER_TURN": str(turn),
            "FAKE_ENGINE_LOG": str(log),
            **checkout.environment,
            # Git may reach no remote: a read that would wait on the network refuses instead.
            "GIT_ALLOW_PROTOCOL": "file",
            **(environment or {}),
        }
    )
    brief = tmp_path / f"{native}.md"
    brief.write_text(
        f"## What\nShow the plan's graph.\n\nPlan project: {project}\n\n"
        "## Why\nA reviewer takes a plan in at a glance.\n\n"
        "## Acceptance criteria\n- The graph opens the Planned tasks section.\n",
        encoding="utf-8",
    )
    finish = subprocess.run(
        ["just", "finish-plan", str(brief), "--name", run, "--to", DESTINATION],
        cwd=checkout.root,
        env=bench.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(900),
        check=False,
    )
    kept = KEPT.search(finish.stderr)
    starts = (
        [json.loads(line) for line in log.read_text("utf-8").splitlines()] if log.exists() else []
    )
    return Finished(
        finish,
        project,
        run,
        Path(kept.group(1)) if kept else None,
        starts,
        {**bench.environment, "DESTINATION_ROOT": str(bench.destination)},
    )


# llmlint: ignore[suppressions_justified] onetaskgraph owns these open JSON answers — listings,
# document reads and stored template answers — and each journey subscripts the fields it reads,
# so a moved shape fails there rather than at a model of the store's schema written here.
def _store(environment: dict[str, str], *arguments: str) -> Any:
    """One command of this checkout's pinned store under ``environment``, as JSON."""
    ran = subprocess.run(
        [str(REPO_ROOT / ".venv" / "bin" / "onetaskgraph"), *arguments, "--json"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert ran.returncode == 0, (arguments, ran.stdout, ran.stderr)
    return json.loads(ran.stdout)


# llmlint: ignore[suppressions_justified] The stored answers are the design-doc template's open
# answer document and the assets the store's own listing, read by key as `_store` says.
def _document(finished: Finished) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """The plan's stored design document: its content, its answers, and its assets by name."""
    document = f"{finished.project}-design"
    (shown,) = _store(finished.environment, "document", "show", document)["items"]
    assets = _store(finished.environment, "document", "show", document)["assets"]
    answers = _store(finished.environment, "document", "answers", document)
    return (
        str(shown["item"]["content"]),
        answers,
        {asset["name"]: Path(asset["path"]).read_bytes() for asset in assets},
    )


def _canonical(finished: Finished) -> Canonical:
    """What the row module derives from the graph the flow kept."""
    assert finished.graph is not None, finished.finish.stderr
    derived = subprocess.run(
        [sys.executable, "-m", "orchestrator.planned_tasks", finished.project]
        + ["--graph", str(finished.graph / "plan-graph.json")],
        cwd=REPO_ROOT,
        env=finished.environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert derived.returncode == 0, derived.stderr
    answer: Canonical = json.loads(derived.stdout)
    return answer


def _columns(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{key: row[key] for key in ("task", "unit", "depends_on", "location")} for row in rows]


def _opening(content: str) -> str:
    """The first line under the Planned tasks heading that is not blank."""
    section = content.split("## Planned tasks\n", 1)[1]
    return next(line for line in section.splitlines() if line.strip())


def _nothing_copied(finished: Finished) -> None:
    assert not list(Path(finished.environment["DESTINATION_ROOT"]).rglob("*.md"))


def _no_launch(finished: Finished) -> None:
    """Nothing was launched and no design document was written."""
    assert finished.starts == [], finished.starts
    assert "launching run" not in finished.finish.stderr, finished.finish.stderr
    source, _, native = finished.project.partition(":")
    documents = _store(finished.environment, "document", "list", "--source", source)
    assert not [one for one in documents["items"] if one["item"].get("project") == native]


def _subgraphs(mermaid: str) -> dict[str, str]:
    """Each node key the Mermaid text draws inside a subgraph, by that subgraph's label."""
    inside: dict[str, str] = {}
    label: str | None = None
    for line in mermaid.splitlines():
        opened = re.fullmatch(r'  subgraph g\d+\["(.*)"\]', line)
        if opened:
            label = opened.group(1)
        elif line == "  end":
            label = None
        elif label is not None and (node := re.fullmatch(r"    (n\d+)\[.*", line)):
            inside[node.group(1)] = label
    return inside


@pytest.mark.xdist_group("finish-plan-graph-accepted")
def test_an_edged_plans_document_opens_with_the_graph_the_flow_read_and_rendered(
    checkout: Copy, tmp_path: Path, oneharness_bin: str
) -> None:
    """The kept reads are the verb's own; the picture, the table and the units are one graph."""
    finished = _finish(checkout, tmp_path, oneharness_bin, FOUR)
    assert finished.finish.returncode == OK, finished.finish.stderr
    assert finished.graph is not None
    assert [start["settlement"] for start in finished.starts] == ["complete"]

    mermaid = (finished.graph / "plan-graph.mmd").read_text("utf-8")
    kept_json = (finished.graph / "plan-graph.json").read_text("utf-8")
    graph = json.loads(kept_json)
    for form, kept in (("mermaid", mermaid), ("json", kept_json)):
        fresh = subprocess.run(
            [str(REPO_ROOT / ".venv" / "bin" / "onetaskgraph"), "project", "graph"]
            + [finished.project, "--format", form, "--group-by", "orchestrator.unit"],
            cwd=REPO_ROOT,
            env=finished.environment,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        assert fresh == kept, form
    assert len(re.findall(r"^ +n\d+\[", mermaid, re.MULTILINE)) == len(FOUR), mermaid
    edges = sum(len(task.deps) for task in FOUR)
    assert len(re.findall(r" --> ", mermaid)) == edges, mermaid
    assert len(graph["nodes"]) == len(FOUR) and len(graph["edges"]) == edges, graph
    unit_of = {task.title: task.unit for task in FOUR}
    inside = _subgraphs(mermaid)
    for node in graph["nodes"]:
        assert node["group"] == unit_of[node["title"]], node
        assert inside[node["key"]] == unit_of[node["title"]], (node, mermaid)

    content, answers, assets = _document(finished)
    assert _opening(content) == GRAPH_IMAGE, content
    assert assets["plan-dag.png"] == (finished.graph / "plan-dag.png").read_bytes()
    canonical = _canonical(finished)
    assert _columns(answers["planned_tasks"]) == canonical["rows"], answers["planned_tasks"]
    assert {row["unit"] for row in answers["planned_tasks"]} == set(unit_of.values())
    assert sorted(unit["name"] for unit in answers["units"]) == sorted(set(unit_of.values()))

    native = finished.project.partition(":")[2]
    copied = [
        one
        for one in _store(finished.environment, "document", "list", "--source", DESTINATION)[
            "items"
        ]
        if one["item"].get("project") == native
    ]
    (design,) = [one for one in copied if str(one["id"]).endswith(f"{native}-design")]
    shown = _store(finished.environment, "document", "show", str(design["id"]))
    assert _opening(str(shown["items"][0]["item"]["content"])) == GRAPH_IMAGE
    (held,) = [asset for asset in shown["assets"] if asset["name"] == "plan-dag.png"]
    assert Path(held["path"]).read_bytes() == assets["plan-dag.png"]
    approved = subprocess.run(
        ["just", "approve-design", f"{DESTINATION}:{native}"],
        cwd=checkout.root,
        env=finished.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )
    assert approved.returncode == 0, approved.stdout + approved.stderr


@pytest.mark.xdist_group("finish-plan-graph-accepted")
def test_two_tasks_sharing_a_title_are_two_rows_and_a_swapped_location_is_refused(
    checkout: Copy, tmp_path: Path, oneharness_bin: str
) -> None:
    """Identity is the task's id: each row keeps its own prerequisites and its own location."""
    finished = _finish(checkout, tmp_path / "accepted", oneharness_bin, SAME_TITLE)
    assert finished.finish.returncode == OK, finished.finish.stderr
    _, answers, _ = _document(finished)
    source, _, native = finished.project.partition(":")
    listed = _store(finished.environment, "task", "list", "--source", source, "--project", native)
    where = {
        held["item"]["metadata"]["onepipeline.id"]: held["item"]["location"]["path"]
        for held in listed["items"]
    }
    same = [row for row in answers["planned_tasks"] if row["task"] == "feat: the same title"]
    assert sorted((row["depends_on"], row["location"]) for row in same) == sorted(
        [("feat: start the flow", where["left"]), ("none", where["right"])]
    ), same

    swapped = _finish(
        checkout,
        tmp_path / "swapped",
        oneharness_bin,
        SAME_TITLE,
        change=(
            "same = [r for r in a['planned_tasks'] if r['task'] == 'feat: the same title']\n"
            "same[0]['location'], same[1]['location'] = same[1]['location'], same[0]['location']"
        ),
    )
    assert swapped.finish.returncode == DOCUMENT_REFUSED, swapped.finish.stderr
    assert re.search(
        r"its `planned_tasks` row \d \('feat: the same title'\) states the `location`",
        swapped.finish.stderr,
    ), swapped.finish.stderr
    _nothing_copied(swapped)


@pytest.mark.xdist_group("finish-plan-graph-accepted")
@pytest.mark.parametrize("tasks", [ONE, EDGELESS], ids=["one task", "no edge"])
def test_a_plan_whose_graph_only_repeats_its_table_shows_none(
    checkout: Copy, tmp_path: Path, oneharness_bin: str, tasks: tuple[Task, ...]
) -> None:
    finished = _finish(checkout, tmp_path, oneharness_bin, tasks)
    assert finished.finish.returncode == OK, finished.finish.stderr
    assert finished.graph is not None and not (finished.graph / "plan-dag.png").exists()

    content, answers, assets = _document(finished)
    assert _opening(content).startswith("| Task |"), content
    assert "plan-dag.png" not in assets and answers.get("dag_image", "") == ""
    assert _columns(answers["planned_tasks"]) == _canonical(finished)["rows"]
    assert {row["task"]: row["unit"] for row in answers["planned_tasks"]} == {
        task.title: task.unit for task in tasks
    }
    native = finished.project.partition(":")[2]
    approved = subprocess.run(
        ["just", "approve-design", f"{DESTINATION}:{native}"],
        cwd=checkout.root,
        env=finished.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )
    assert approved.returncode == 0, approved.stdout + approved.stderr


#: Each way a writer's stored document can part from the plan's graph, as the change made to
#: its answers and the difference the flow names.
DISAGREEMENTS = {
    "a dependency missing from one row": (
        "a['planned_tasks'][3]['depends_on'] = a['planned_tasks'][3]['depends_on'].split('; ')[0]",
        "its `planned_tasks` row 4 ('feat: show the graph in the document') states the "
        "`depends_on` 'feat: derive the planned rows', where the plan's graph gives "
        "'feat: derive the planned rows; feat: draw the grouped graph'",
    ),
    "a task absent from the table": (
        "del a['planned_tasks'][1]",
        "its `planned_tasks` row 2 ('feat: derive the planned rows') states the `task` "
        "'feat: draw the grouped graph'",
    ),
    "two rows in the wrong order": (
        "rows = a['planned_tasks']; rows[1], rows[2] = rows[2], rows[1]",
        "its `planned_tasks` row 2 ('feat: derive the planned rows') states the `task` "
        "'feat: draw the grouped graph'",
    ),
    "a row naming another unit": (
        "a['planned_tasks'][0]['unit'] = 'Elsewhere'",
        "its `planned_tasks` row 1 ('feat: record the \"unit\" key #1') states the `unit` "
        "'Elsewhere', where the plan's graph gives 'Plan store'",
    ),
    "a unit no task changes": (
        "a['units'].append({**a['units'][0], 'name': 'Nobody'})",
        "its Architecture `units` name 'Nobody', which no task of the plan changes",
    ),
    "a row no task gives": (
        "a['planned_tasks'].append(dict(a['planned_tasks'][0]))",
        "its `planned_tasks` has 5 rows, and the plan's graph has 4 tasks; row 5 is one no "
        "task of the plan gives",
    ),
    "a unit a task changes left out": (
        "a['units'] = [u for u in a['units'] if u['name'] != 'Template']",
        "its Architecture `units` do not name 'Template', which a task of the plan changes",
    ),
    "a unit named twice": (
        "a['units'].append(dict(a['units'][0]))",
        "its Architecture `units` name 'Plan store' twice",
    ),
}


@pytest.mark.xdist_group("finish-plan-graph-refused")
@pytest.mark.parametrize("case", list(DISAGREEMENTS))
def test_a_table_or_units_that_part_from_the_graph_are_refused_and_nothing_is_copied(
    checkout: Copy, tmp_path: Path, oneharness_bin: str, case: str
) -> None:
    change, said = DISAGREEMENTS[case]
    finished = _finish(checkout, tmp_path, oneharness_bin, FOUR, change=change)

    assert finished.finish.returncode == DOCUMENT_REFUSED, finished.finish.stderr
    assert f"planned-tasks: {finished.project}-design: {said}" in finished.finish.stderr
    assert "parts from its plan's graph" in finished.finish.stderr
    _nothing_copied(finished)


def _other_png(tmp_path: Path) -> Path:
    """A PNG under the graph's name whose bytes are not the graph the flow rendered."""
    other = tmp_path / "other" / "plan-dag.png"
    other.parent.mkdir(parents=True)
    write_noise_png(other, 11, TYPICAL)
    return other


@pytest.mark.xdist_group("finish-plan-graph-refused")
def test_a_graph_image_that_is_not_the_rendered_one_or_is_missing_is_refused(
    checkout: Copy, tmp_path: Path, oneharness_bin: str
) -> None:
    differs = _finish(
        checkout, tmp_path / "differs", oneharness_bin, FOUR, asset=str(_other_png(tmp_path))
    )
    assert differs.finish.returncode == DOCUMENT_REFUSED, differs.finish.stderr
    assert "its `plan-dag.png` asset differs from the graph the flow rendered" in (
        differs.finish.stderr
    )
    _nothing_copied(differs)

    missing = _finish(
        checkout,
        tmp_path / "missing",
        oneharness_bin,
        FOUR,
        change="a['dag_image'] = ''",
        asset="none",
    )
    assert missing.finish.returncode == DOCUMENT_REFUSED, missing.finish.stderr
    assert "it answers `dag_image` '', and a plan of more than one task with a dependency" in (
        missing.finish.stderr
    )
    _nothing_copied(missing)


@pytest.mark.xdist_group("finish-plan-graph-refused")
def test_a_one_task_plans_document_carrying_a_graph_image_is_refused(
    checkout: Copy, tmp_path: Path, oneharness_bin: str
) -> None:
    finished = _finish(
        checkout,
        tmp_path,
        oneharness_bin,
        ONE,
        change="a['dag_image'] = 'plan-dag.png'",
        asset=str(_other_png(tmp_path)),
    )
    assert finished.finish.returncode == DOCUMENT_REFUSED, finished.finish.stderr
    assert "it answers `dag_image` 'plan-dag.png', and a plan of one task or of no" in (
        finished.finish.stderr
    )
    _nothing_copied(finished)


@pytest.mark.xdist_group("finish-plan-graph-refused")
def test_with_no_browser_the_flow_stops_before_any_launch_naming_its_provisioning(
    checkout: Copy, tmp_path: Path, oneharness_bin: str
) -> None:
    empty = tmp_path / "no-browsers"
    empty.mkdir()
    finished = _finish(
        checkout,
        tmp_path,
        oneharness_bin,
        FOUR,
        environment={"PLAYWRIGHT_BROWSERS_PATH": str(empty)},
    )
    assert finished.finish.returncode == UNRUNNABLE, finished.finish.stderr
    assert f"render-diagram: no Chromium at {empty}" in finished.finish.stderr
    assert "just bootstrap" in finished.finish.stderr
    assert "could not be rendered to" in finished.finish.stderr
    _no_launch(finished)


@pytest.mark.xdist_group("finish-plan-graph-refused")
@pytest.mark.parametrize("form", ["mermaid", "json"])
def test_a_graph_read_that_fails_stops_the_flow_naming_the_read_and_its_repair(
    checkout: Copy, tmp_path: Path, oneharness_bin: str, form: str
) -> None:
    finished = _finish(
        checkout,
        tmp_path,
        oneharness_bin,
        FOUR,
        environment={"FAKE_GRAPH_ABSENT_FORMAT": form},
    )
    assert finished.finish.returncode == UNRUNNABLE, finished.finish.stderr
    said = finished.finish.stderr
    assert f"the --format {form} read of the graph of {finished.project} failed" in said, said
    assert (
        f"'uv run --no-sync onetaskgraph project graph {finished.project} --group-by "
        f"orchestrator.unit --format {form}' prints the graph"
    ) in said, said
    _no_launch(finished)
