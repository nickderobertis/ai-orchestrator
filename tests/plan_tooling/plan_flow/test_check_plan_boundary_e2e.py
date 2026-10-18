"""`just check-plan` refuses a mixed plan whose private work reaches a public node unexported.

A planner told about the boundary before it writes — the brief's `Repositories:` line —
is the cheap case. When the mix is discovered later, the plan reaches `just check-plan`
first, and that recipe runs the engine's own plan loader before this repository's
checks, so the refusal is the engine's mixed-shape check and carries the same guidance a
mixed brief's planner is handed (`test_boundary_guidance_e2e.py`): nothing here restates
the engine's rule. The planner corrects the plan inside its own dispatch, and the
corrected plan — a private `publish: "preserve"` producer, an export edge, a public
consumer whose task carries the no-private-reference criterion — passes.

The two repositories are synthetic and the scratch registry's rules make one public and
one private. Only the paid model is stood in, for the review the corrected plan needs.

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


def _plan(tmp_path: Path, *, preserve: bool, edge: bool) -> Path:
    """A two-node mixed plan: a private producer, then a public consumer depending on it."""
    producer: dict[str, object] = {
        "id": PRODUCER,
        "persona": "engineer",
        "repo": PRIVATE_REPOSITORIES[0],
        "title": "feat: write a generic cursor example",
        "task": _mixed_render(tmp_path / "private", "private"),
    }
    if preserve:
        producer["publish"] = "preserve"
    plan: dict[str, object] = {
        "schema_version": 3,
        "goal": {"text": "Page the listing by an opaque cursor"},
        "name": "check-plan-boundary",
        "tasks": [
            producer,
            {
                "id": CONSUMER,
                "persona": "engineer",
                "repo": PUBLIC_REPOSITORIES[0],
                "title": "feat: page the listing by an opaque cursor",
                "deps": [PRODUCER],
                "task": _mixed_render(tmp_path / "public", "public"),
            },
        ],
    }
    if edge:
        plan["export_edges"] = [
            {
                "from": PRODUCER,
                "to": CONSUMER,
                "source_directory": EXPORT_DIRECTORY,
                "target_directory": EXPORT_DIRECTORY,
            }
        ]
    written = tmp_path / "plan.json"
    written.write_text(json.dumps(plan), encoding="utf-8")
    return written


@pytest.fixture(scope="module")
def registry(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    return _registry(tmp_path_factory.mktemp("check-plan-boundary"))


def _summary() -> str:
    guidance = _run(str(ENGINE), "boundary", "guidance", "--json", environment=dict(os.environ))
    assert guidance.returncode == 0, guidance.stderr
    return str(json.loads(guidance.stdout)["summary"])


@pytest.mark.parametrize(
    ("preserve", "edge"),
    [(False, False), (False, True), (True, False)],
    ids=["ordinary-dependency", "producer-not-preserved", "no-export-edge"],
)
def test_a_mixed_plan_missing_the_export_shape_is_refused_with_the_engines_guidance(
    tmp_path: Path, registry: dict[str, str], preserve: bool, edge: bool
) -> None:
    for subdirectory in ("private", "public"):
        (tmp_path / subdirectory).mkdir()
    project = project_from_plan(_plan(tmp_path, preserve=preserve, edge=edge))
    checked = _run("just", "check-plan", project, environment=registry)
    assert checked.returncode != 0, f"a mixed plan was accepted:\n{checked.stdout}"
    assert _summary() in checked.stdout + checked.stderr


def test_the_corrected_plan_passes_and_its_public_task_owes_the_criterion(
    tmp_path: Path, registry: dict[str, str]
) -> None:
    for subdirectory in ("private", "public"):
        (tmp_path / subdirectory).mkdir()
    project = project_from_plan(_plan(tmp_path, preserve=True, edge=True))
    reviewed(project)
    checked = _run("just", "check-plan", project, environment=registry)
    assert checked.returncode == 0, f"{checked.stdout}\n{checked.stderr}"
    assert _summary() not in checked.stdout + checked.stderr
    tasks = {task["id"]: task for task in read_project_plan(project)["tasks"]}
    assert NO_PRIVATE_REFERENCE in tasks[CONSUMER]["task"]
    assert NO_PRIVATE_REFERENCE not in tasks[PRODUCER]["task"]
