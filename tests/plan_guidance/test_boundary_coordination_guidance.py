"""This host's mixed-plan guidance says what the adopted engine says: one private plan, each
task keeping its own visibility, and nothing to set.

The user wants private work to inform public repositories through a generalized export, and a
planner handed two answers for how to shape that work follows whichever it read last. The
engine owns the answer — `onepipeline boundary guidance`, which `just plan` hands a mixed
brief's planner whole and every boundary refusal carries — so this holds both halves of the
agreement against something real rather than against a remembered sentence:

* the **installed** engine's typed guidance states the one-plan coordination shape, task-owned
  visibility and no setting, so an adoption that moved the engine back to a project-inherited
  classification, or forward to one needing a mode, fails here before a planner reads it;
* every surface of this host that speaks about a mixed plan — the planner injection in
  `scripts/plan.sh`, the planner persona, `docs/orchestration.md` and `AGENTS.md` — carries
  none of the advice that guidance retired: splitting the work into two plans, a private
  project making its public tasks private, or a mode or setting to enable.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

ENGINE = REPO_ROOT / "node_modules" / ".bin" / "onepipeline"

#: The guidance version that carries `coordination` and `example`; a reader of an earlier one
#: knows neither field.
COORDINATING_GUIDANCE = 2

#: Every surface of this host that tells a reader how a mixed plan is shaped.
HOST_SURFACES = (
    "scripts/plan.sh",
    "personas/planner.yaml",
    "docs/orchestration.md",
    "AGENTS.md",
)

#: Advice the adopted guidance retired, each as a reader of the old wording would have met it:
#: the split into two plans, the parent-inheritance rule that forced it, and the screening-off
#: escape and the mode a mock once proposed in its place.
RETIRED_ADVICE = (
    "authored as two",
    "planned as two projects",
    "two plans, one per",
    "a private project makes its public consumer private",
    "tightened by its project",
    "tightens every task under a private project",
    "with store screening off",
    "classification mode",
    "enable the mode",
    "visibility mode",
)


def _guidance() -> dict[str, object]:
    printed = subprocess.run(
        [str(ENGINE), "boundary", "guidance", "--json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert printed.returncode == 0, printed.stderr
    guidance = json.loads(printed.stdout)
    assert isinstance(guidance, dict)
    return guidance


def test_the_adopted_engine_coordinates_one_private_plan_by_each_task_s_own_visibility() -> None:
    """The installed guidance's coordination lines say task-owned visibility, a private home
    holding the whole graph, a neutral projection, and no setting or key to add."""
    guidance = _guidance()
    assert guidance["schema_version"] == COORDINATING_GUIDANCE, guidance
    coordination = guidance["coordination"]
    assert isinstance(coordination, list)
    said = " ".join(str(line) for line in coordination)
    assert "Visibility belongs to each task" in said, said
    assert "no setting or metadata key" in said, said
    assert "canonical board home stays private" in said, said
    assert "no private dependency, backlink" in said, said
    assert "Screening stays on" in said, said
    example = guidance["example"]
    assert isinstance(example, dict)
    tasks = {task["id"]: task for task in example["tasks"]}
    assert {task["visibility"] for task in tasks.values()} == {"private", "public"}
    # The visibility is the example's statement of each task's own, never a key of the plan.
    assert set(example) == {"tasks", "export_edges", "projection"}, example
    assert example["projection"]["dependencies"] == [], example
    assert example["projection"]["backlinks"] == [], example


@pytest.mark.parametrize("surface", HOST_SURFACES)
def test_no_host_surface_carries_advice_the_guidance_retired(surface: str) -> None:
    """Neither a split into two plans, a project tightening its tasks, nor a mode to enable."""
    written = " ".join(Path(REPO_ROOT / surface).read_text(encoding="utf-8").split()).lower()
    for advice in RETIRED_ADVICE:
        assert advice not in written, f"{surface} still advises {advice!r}"


def test_the_planner_injection_quotes_the_guidance_rather_than_restating_it() -> None:
    """`scripts/plan.sh` places the engine's text at `@GUIDANCE@` and spells none of its own
    coordination lines, so the planner reads one statement of the shape."""
    script = (REPO_ROOT / "scripts" / "plan.sh").read_text(encoding="utf-8")
    template = script[script.index("BOUNDARY_NOTE_TEMPLATE=$(") :]
    template = template[: template.index("\nNOTE\n")]
    assert "@GUIDANCE@" in template
    guidance = _guidance()
    restated = [guidance["coordination"], guidance["producer"]]
    assert all(isinstance(lines, list) for lines in restated), guidance
    for line in (line for lines in restated if isinstance(lines, list) for line in lines):
        assert str(line) not in template, f"the injection restates the engine's {line!r}"
    assert "one project in the `authoring` source" in " ".join(template.split())
