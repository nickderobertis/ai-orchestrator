"""`just check-plan` over a plan mixing private and public work, with the boundary on.

The engine's boundary guidance — what a planner of a mixed brief is handed — prescribes one
private coordinating plan: a private `publish: "preserve"` producer, an export edge, and a
public consumer whose task carries the no-private-reference criterion, each task keeping its
own visibility with nothing set on the plan. With the tracked `onetaskgraph.yaml` switching the
store's public boundary on, a plan holding private work is stored only in a source declared
private — `authoring`, which the suite's fixture source stands in for here, declared private
as it is — and the adopted store classifies each task by its own repositories rather than by
its project, so the consumer stays the public node the engine requires and `just check-plan`
accepts the plan. What the check enforces is the engine's: the same plan whose public consumer
names a private term is refused in the engine's words, and an export edge naming a node of
another plan is refused, so the generalized work crosses only inside its one plan.

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


def test_the_guidance_s_one_plan_shape_passes_with_each_task_keeping_its_own_visibility(
    renders: Path, registry: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The producer, the edge and the consumer in one plan, drafted privately: accepted, the
    consumer public by its own repository and owing the no-private-reference criterion."""
    for name, value in DRAFTED_PRIVATE.items():
        monkeypatch.setenv(name, value)
    tasks = [_producer(renders), _consumer(renders, deps=[PRODUCER])]
    project = project_from_plan(_written(renders, "one-plan", tasks, export_edges=[EDGE]))
    reviewed(project)

    checked = _run("just", "check-plan", project, environment={**registry, **DRAFTED_PRIVATE})

    said = checked.stdout + checked.stderr
    assert checked.returncode == 0, f"the guidance's one-plan shape was refused:\n{said}"
    assert _summary() not in said
    stored = read_project_plan(project)
    # Nothing on the plan sets a visibility: no mode, setting or metadata key.
    for word in ("visibility", "mode", "onepipeline.classification"):
        assert word not in json.dumps(stored.get("metadata", {})), stored
    held = {task["id"]: task for task in stored["tasks"]}
    assert NO_PRIVATE_REFERENCE in held[CONSUMER]["task"]
    assert NO_PRIVATE_REFERENCE not in held[PRODUCER]["task"]


def test_a_public_task_carrying_a_private_term_is_refused_in_the_engine_s_words(
    renders: Path, registry: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one plan with its public consumer's title naming the private repository: refused,
    carrying the engine's own guidance rather than any wording of this host's."""
    for name, value in DRAFTED_PRIVATE.items():
        monkeypatch.setenv(name, value)
    consumer = _consumer(renders, deps=[PRODUCER])
    consumer["title"] = f"feat: page the listing as {PRIVATE_REPOSITORIES[0].split('/', 1)[1]} does"
    tasks = [_producer(renders), consumer]
    project = project_from_plan(_written(renders, "named-plan", tasks, export_edges=[EDGE]))
    reviewed(project)

    checked = _run("just", "check-plan", project, environment={**registry, **DRAFTED_PRIVATE})

    said = checked.stdout + checked.stderr
    assert checked.returncode != 0, f"a public task naming a private term was accepted:\n{said}"
    assert _summary() in said, said


def test_an_export_edge_from_another_plan_s_node_is_refused(
    renders: Path, registry: dict[str, str]
) -> None:
    """A public plan carrying an edge from a producer it does not hold: refused, because an
    export edge names nodes of its own plan."""
    crossing = project_from_plan(
        _written(renders, "crossing-plan", [_consumer(renders, deps=[])], export_edges=[EDGE])
    )
    reviewed(crossing)
    crossed = _run("just", "check-plan", crossing, environment=registry)

    assert crossed.returncode != 0, f"an edge from another plan was accepted:\n{crossed.stdout}"
    said = crossed.stdout + crossed.stderr
    assert "its `from` names no node of this plan" in said, said


def _summary() -> str:
    guidance = _run(str(ENGINE), "boundary", "guidance", "--json", environment=dict(os.environ))
    assert guidance.returncode == 0, guidance.stderr
    return str(json.loads(guidance.stdout)["summary"])
