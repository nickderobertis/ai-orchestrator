"""The half of `tests/test_plan_budgets_template.py` that reads this repository's prose.

It sits in the `family-scans` project.

These hold the budget guidance to its authority and read every family for the retired
budgets document. That project's key names each file they read, and `tests/conftest.py`
holds them to it; the tests of that module that read no prose stay there, in
`orchestrator:test`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from test_plan_budgets_template import CHECKER, DESCRIPTION, PARTIAL, RETIRED, _descriptions, _run

from orchestrator import plan_budgets
from orchestrator.root import REPO_ROOT


@pytest.mark.parametrize(
    "path",
    [Path("docs") / "budgets.md", Path("personas") / "planner.yaml"],
    ids=["budgets-doc", "planner"],
)
def test_the_prose_restates_the_models_records_vocabularies_and_caps(path: Path) -> None:
    """`docs/budgets.md` and the planner's persona restate the model, and are held to it."""
    text = " ".join((REPO_ROOT / path).read_text(encoding="utf-8").split())

    for named in (
        plan_budgets.TASK_RECORD,
        plan_budgets.PLAN_RECORD,
        plan_budgets.DESCRIPTION_TEMPLATE,
    ):
        assert f"`{named}`" in text, named
    for vocabulary in (plan_budgets.Basis, plan_budgets.Direction, plan_budgets.FileChange):
        assert ", ".join(f"`{value}`" for value in list(vocabulary)[:-1]) in text, vocabulary
    for cap in (
        plan_budgets.NAME_LIMIT,
        plan_budgets.SUMMARY_LIMIT,
        plan_budgets.TEN_X_SUMMARY_LIMIT,
        plan_budgets.SIZING_LIMIT,
    ):
        assert f"{cap} characters" in text, cap


def test_nothing_reads_renders_or_names_the_retired_budgets_document() -> None:
    registered = yaml.safe_load((REPO_ROOT / "templates" / "templates.yaml").read_text())
    assert "plan-budgets" not in registered["templates"]
    assert not (REPO_ROOT / "templates" / "plan-budgets.md.j2").exists()
    held: list[str] = []
    for root in ("orchestrator", "scripts", "templates", "personas", "docs", "config"):
        for path in sorted((REPO_ROOT / root).rglob("*")):
            if path.is_file() and path.suffix in {".py", ".sh", ".j2", ".md", ".yaml", ".yml"}:
                text = path.read_text(encoding="utf-8")
                if path == plan_budgets.MIGRATION_LIST:
                    # Each entry's reason records when its plan was written, which is
                    # history; the header is what describes the arrangement in force.
                    text = "\n".join(line for line in text.splitlines() if line.startswith("#"))
                held.extend(
                    f"{path.relative_to(REPO_ROOT)}: {found.group(0)}"
                    for found in RETIRED.finditer(text)
                )
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    held.extend(f"AGENTS.md: {found.group(0)}" for found in RETIRED.finditer(agents))
    assert held == []


def test_modern_budget_contract_guidance_is_held_to_the_authority() -> None:
    partial = _descriptions(PARTIAL)["budgets"]
    declared = _descriptions(DESCRIPTION)
    for key in plan_budgets.MODERN_BUDGET_KEYS:
        assert f"`{key}`" in partial
    for source in plan_budgets.Source:
        assert f"`{source}`" in partial
    for path in (REPO_ROOT / "docs/budgets.md", REPO_ROOT / "personas/planner.yaml"):
        text = path.read_text()
        for key in ("schema_version", "source", "check_runtime_seconds", "in_scope"):
            assert key in text, (path, key)
        for source in plan_budgets.Source:
            assert source in text, (path, source)
        for concern in plan_budgets.CANONICAL_ROOT_CONCERNS:
            assert concern in " ".join(text.casefold().split()), (path, concern)
    assert "`in_scope` (boolean)" in declared["checklist"]
    for rule in ("Each repository/budget pair occurs once", "never `none`", "changed repositories"):
        assert rule in declared["repo_wide_effects"], rule


def test_the_breakdown_field_the_guidance_names_is_the_installed_libraries_own() -> None:
    """The reporter's `detail` the budget-level rule names is a field onebudgetspec reports.

    `docs/budgets.md`, the planner's persona and the `budgets` description each send a
    budget's breakdown to that field, so a release that renamed or dropped it fails here
    rather than leaving every planner pointed at a field the check no longer carries.
    """
    ran = _run([str(CHECKER), "schema"])
    assert ran.returncode == 0, ran.stderr
    result = json.loads(ran.stdout)["roots"]["check-report"]["$defs"]["CheckResult"]
    assert "detail" in result["properties"], sorted(result["properties"])
    assert "reported" in result["properties"]["detail"]["description"]
    for text in (
        (REPO_ROOT / "docs" / "budgets.md").read_text(encoding="utf-8"),
        (REPO_ROOT / "personas" / "planner.yaml").read_text(encoding="utf-8"),
        _descriptions(PARTIAL)["budgets"],
    ):
        assert "reporter's `detail`" in " ".join(text.split())


@pytest.mark.parametrize(
    "path",
    [
        "AGENTS.md",
        "docs/budgets.md",
        "docs/orchestration.md",
        "personas/planner.yaml",
        "personas/design-doc.yaml",
        "config/plan-checklist.llmlint.yml",
        "config/budgets-migration.yaml",
    ],
)
def test_current_guidance_points_to_the_single_design_visibility_rule(path: str) -> None:
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    if path == "config/budgets-migration.yaml":
        text = "\n".join(line for line in text.splitlines() if line.startswith("#"))
    assert ("docs/budgets.md" if path == "AGENTS.md" else "budgets_guidance") in text, path
    assert "This plan predates budgets:" not in text, path
    assert "named root effect shows no" not in text, path
    assert "with no budget still shows" not in text, path


@pytest.mark.parametrize(
    "path",
    [
        "AGENTS.md",
        "docs/budgets.md",
        "docs/orchestration.md",
        "personas/planner.yaml",
        "config/plan-checklist.llmlint.yml",
    ],
)
def test_current_planning_guidance_requires_the_budget_need_decision_first(path: str) -> None:
    text = " ".join((REPO_ROOT / path).read_text(encoding="utf-8").casefold().split())
    assert "first decides" in text or "first decide" in text, path
    if path != "AGENTS.md":
        assert "overview alone" in text, path
        assert "existing" in text and "analysis" in text, path
