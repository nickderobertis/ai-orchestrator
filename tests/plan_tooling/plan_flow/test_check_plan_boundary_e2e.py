"""`just check-plan` over a plan mixing private and public work, with the boundary on.

The engine's boundary guidance — what a planner of a mixed brief is handed — prescribes one
plan: a private `publish: "preserve"` producer, an export edge, and a public consumer whose task
carries the no-private-reference criterion. With the tracked `onetaskgraph.yaml` switching the
store's public boundary on, a plan holding private work is private, so it is stored only in a
source declared private — `authoring`, which the suite's fixture source stands in for here,
declared private as it is. The pinned store gives each task its own classification, which the
private project it is filed under never tightens, so the export consumer stays the public node
the engine requires and `just check-plan` passes the plan. The same two nodes authored as two
plans — the private producer's in the private source, the public consumer's in a public one —
each pass too; but an export edge names nodes of its own plan, so the public plan cannot carry
one from the private plan's producer.

The two repositories are synthetic and the scratch registry's rules make one public and one
private. Only the paid model is stood in, for the reviews the plans need.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] The edge is the module's
placement: these journeys drive `just check-plan` and `just review-plan` over plans rendered
through `templates/`, which read the scripts, templates, `config/` and `orchestrator/` that
`plan-tooling-plan-flow:test`'s key names, beside the guidance module whose helpers they
reuse, so a project of their own would be keyed on the same files.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import boundary_home
import pytest
from project_fixtures import project_from_plan, read_project_plan, reviewed
from test_boundary_guidance_e2e import (
    ENGINE,
    NO_PRIVATE_REFERENCE,
    PRIVATE_REPOSITORIES,
    PUBLIC_REPOSITORIES,
    _mixed_render,
    _registry,
    _run,
)

PRODUCER = "write-the-cursor-example"
CONSUMER = "page-by-an-opaque-cursor"
EXPORT_DIRECTORY = "examples/cursor"


def _producer(tmp_path: Path) -> dict[str, object]:
    return {
        "id": PRODUCER,
        "persona": "engineer",
        "repo": PRIVATE_REPOSITORIES[0],
        "title": "feat: write a generic cursor example",
        "task": _mixed_render(tmp_path / "private", "private"),
        "publish": "preserve",
    }


def _consumer(tmp_path: Path, *, deps: list[str]) -> dict[str, object]:
    return {
        "id": CONSUMER,
        "persona": "engineer",
        "repo": PUBLIC_REPOSITORIES[0],
        "title": "feat: page the listing by an opaque cursor",
        "deps": deps,
        "task": _mixed_render(tmp_path / "public", "public"),
    }


#: The export edge the engine's guidance prescribes, from the producer to the consumer.
EDGE = {
    "from": PRODUCER,
    "to": CONSUMER,
    "source_directory": EXPORT_DIRECTORY,
    "target_directory": EXPORT_DIRECTORY,
}


def _written(tmp_path: Path, name: str, tasks: list[dict[str, object]], **extra: object) -> Path:
    plan: dict[str, object] = {
        "schema_version": 3,
        "goal": {"text": "Page the listing by an opaque cursor"},
        "name": name,
        "tasks": tasks,
        **extra,
    }
    written = tmp_path / f"{name}.json"
    written.write_text(json.dumps(plan), encoding="utf-8")
    return written


#: The suite's drafting source declared private, as the `authoring` source a planner drafts a
#: plan in is: a plan holding private work is private, and is written only where a source is.
DRAFTED_PRIVATE = {"ONETASKGRAPH_SOURCES__TEST_FIXTURES__VISIBILITY": "private"}


@pytest.fixture(scope="module")
def registry(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    return _registry(tmp_path_factory.mktemp("check-plan-boundary"))


@pytest.fixture
def renders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Where the tasks are rendered, this process answering each repository's visibility."""
    boundary_home.answering(monkeypatch, public=PUBLIC_REPOSITORIES, private=PRIVATE_REPOSITORIES)
    for subdirectory in ("private", "public"):
        (tmp_path / subdirectory).mkdir()
    return tmp_path


def test_the_guidance_s_one_plan_shape_passes_its_consumer_public(
    renders: Path, registry: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The producer, the edge and the consumer in one plan, drafted privately: it passes."""
    for name, value in DRAFTED_PRIVATE.items():
        monkeypatch.setenv(name, value)
    tasks = [_producer(renders), _consumer(renders, deps=[PRODUCER])]
    project = project_from_plan(_written(renders, "one-plan", tasks, export_edges=[EDGE]))
    reviewed(project)

    checked = _run("just", "check-plan", project, environment={**registry, **DRAFTED_PRIVATE})

    said = checked.stdout + checked.stderr
    assert checked.returncode == 0, f"the one-plan shape was refused:\n{said}"
    assert "an export consumer must be a public lifecycle node" not in said, said
    tasks = {task["id"]: task for task in read_project_plan(project)["tasks"]}
    assert NO_PRIVATE_REFERENCE in tasks[CONSUMER]["task"]


def test_the_same_nodes_as_two_plans_each_pass_and_no_edge_crosses_them(
    renders: Path, registry: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The private producer's plan and the public consumer's plan each pass on their own; an
    export edge naming the other plan's producer is refused."""
    for name, value in DRAFTED_PRIVATE.items():
        monkeypatch.setenv(name, value)
    producer = project_from_plan(_written(renders, "producer-plan", [_producer(renders)]))
    reviewed(producer)
    produced = _run("just", "check-plan", producer, environment={**registry, **DRAFTED_PRIVATE})
    for name in DRAFTED_PRIVATE:
        monkeypatch.delenv(name)
    consumer = project_from_plan(_written(renders, "consumer-plan", [_consumer(renders, deps=[])]))
    reviewed(consumer)
    consumed = _run("just", "check-plan", consumer, environment=registry)
    crossing = project_from_plan(
        _written(renders, "crossing-plan", [_consumer(renders, deps=[])], export_edges=[EDGE])
    )
    reviewed(crossing)
    crossed = _run("just", "check-plan", crossing, environment=registry)

    assert produced.returncode == 0, produced.stdout + produced.stderr
    assert consumed.returncode == 0, consumed.stdout + consumed.stderr
    assert _summary() not in consumed.stdout + consumed.stderr
    tasks = {task["id"]: task for task in read_project_plan(consumer)["tasks"]}
    assert NO_PRIVATE_REFERENCE in tasks[CONSUMER]["task"]
    assert crossed.returncode != 0, f"an edge from another plan was accepted:\n{crossed.stdout}"
    said = crossed.stdout + crossed.stderr
    assert "its `from` names no node of this plan" in said, said


def _summary() -> str:
    guidance = _run(str(ENGINE), "boundary", "guidance", "--json", environment=dict(os.environ))
    assert guidance.returncode == 0, guidance.stderr
    return str(json.loads(guidance.stdout)["summary"])
