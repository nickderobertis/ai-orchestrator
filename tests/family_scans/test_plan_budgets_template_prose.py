"""The budget guidance, held to the templates a plan's budgets are rendered from.

It sits in the `family-scans` project.

`templates/plan-task-budgets.md.j2` and `templates/plan-description.md.j2` are the authority
for a plan's budget records: their variable schemas state the keys, the vocabularies and the
caps the plan store holds every rendering to. `docs/budgets.md` and the planner's persona
restate them, so these hold the restatement to the schemas, and read every family for the
retired budgets document. That project's key names each file they read, and
`tests/conftest.py` holds them to it.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from orchestrator.root import REPO_ROOT

#: The plan description's template, and the partial a task's `## Budgets` renders from.
DESCRIPTION = REPO_ROOT / "templates" / "plan-description.md.j2"
PARTIAL = REPO_ROOT / "templates" / "plan-task-budgets.md.j2"
#: The task metadata key holding its budgets, and the project key holding the plan's answers.
TASK_RECORD = "orchestrator.budgets"
PLAN_RECORD = "orchestrator.plan-budgets"
#: The name the plan description's template is registered under.
DESCRIPTION_TEMPLATE = "plan-description"
#: The two root concerns every plan's checklist answers.
CANONICAL_ROOT_CONCERNS = ("gate time", "change cycle time")
#: The installed budget checker.
CHECKER = REPO_ROOT / ".venv" / "bin" / "onebudgetspec"
#: What the retired budgets document was read, rendered and named by.
RETIRED = re.compile(
    r"plan-budgets\.md\.j2|onepipeline:plan-budgets|\bBUDGETS_(?:TEMPLATE|REFERENCE|SUFFIX)\b"
    r"|budgets_document_id|is_budgets_document|## Record\b|<project>-budgets"
    r"|budgets document"
)
CAP = re.compile(r"at most (\d+) characters")


def _front_matter(template: Path) -> dict[str, dict[str, object]]:
    """The variables ``template`` declares, by name."""
    found = re.match(r"---\n(.*?)\n---\n", template.read_text(encoding="utf-8"), re.DOTALL)
    assert found is not None, template
    variables = yaml.safe_load(found.group(1))["variables"]
    assert isinstance(variables, dict), template
    return variables


def _descriptions(template: Path) -> dict[str, str]:
    """Each variable ``template`` declares, by name, its description on one line."""
    return {
        name: " ".join(str(variable["description"]).split())
        for name, variable in _front_matter(template).items()
    }


def _budget_properties() -> dict[str, dict[str, object]]:
    """The keys one task budget may carry, each with the schema the store holds it to."""
    schema = _front_matter(PARTIAL)["budgets"]["schema"]
    assert isinstance(schema, dict), schema
    properties = schema["items"]["properties"]
    assert isinstance(properties, dict), schema
    return properties


def _enum(key: str) -> list[str]:
    values = _budget_properties()[key]["enum"]
    assert isinstance(values, list), key
    return [str(value) for value in values]


def _caps() -> set[int]:
    """Every character cap the two templates hold a budget answer to."""
    name = _budget_properties()["name"]["maxLength"]
    assert isinstance(name, int)
    stated = {int(cap) for text in _descriptions(DESCRIPTION).values() for cap in CAP.findall(text)}
    return {name} | stated


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - the installed budget checker
        command, cwd=REPO_ROOT, text=True, capture_output=True, check=False
    )


@pytest.mark.parametrize(
    "path",
    [Path("docs") / "budgets.md", Path("personas") / "planner.yaml"],
    ids=["budgets-doc", "planner"],
)
def test_the_prose_restates_the_templates_records_vocabularies_and_caps(path: Path) -> None:
    """`docs/budgets.md` and the planner's persona restate the templates, and are held to them."""
    text = " ".join((REPO_ROOT / path).read_text(encoding="utf-8").split())
    registered = yaml.safe_load((REPO_ROOT / "templates" / "templates.yaml").read_text())
    assert DESCRIPTION_TEMPLATE in registered["templates"]
    assert f"`{TASK_RECORD}`" in " ".join(PARTIAL.read_text(encoding="utf-8").split())
    assert f"`{PLAN_RECORD}`" in " ".join(DESCRIPTION.read_text(encoding="utf-8").split())

    for named in (TASK_RECORD, PLAN_RECORD, DESCRIPTION_TEMPLATE):
        assert f"`{named}`" in text, named
    for vocabulary in ("basis", "direction", "file_change"):
        values = _enum(vocabulary)
        assert ", ".join(f"`{value}`" for value in values[:-1]) in text, vocabulary
    caps = _caps()
    assert {60, 160, 240, 600} <= caps, caps
    for cap in sorted(caps):
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
    for key in _budget_properties():
        assert f"`{key}`" in partial
    for source in _enum("source"):
        assert f"`{source}`" in partial
    for path in (REPO_ROOT / "docs/budgets.md", REPO_ROOT / "personas/planner.yaml"):
        text = path.read_text()
        for key in ("schema_version", "source", "check_runtime_seconds", "in_scope"):
            assert key in text, (path, key)
        for source in _enum("source"):
            assert source in text, (path, source)
        for concern in CANONICAL_ROOT_CONCERNS:
            assert concern in " ".join(text.casefold().split()), (path, concern)
    assert "`in_scope` (boolean)" in declared["checklist"]
    for rule in ("Each repository/budget pair occurs once", "never `none`", "changed repositories"):
        assert rule in declared["repo_wide_effects"], rule


def _previous_keys() -> list[str]:
    """The keys a version-3 changed budget's `previous` holds, as the schema requires them."""
    previous = _budget_properties()["previous"]
    assert isinstance(previous, dict), previous
    required = previous["required"]
    assert isinstance(required, list) and set(required) == set(previous["properties"])
    return [str(key) for key in required]


#: Every file restating the budget records, or the design document's reading of them.
RESTATEMENTS = (
    "docs/budgets.md",
    "personas/planner.yaml",
    "personas/design-doc.yaml",
    "templates/planning/design.md.j2",
)


def test_the_version_3_contract_is_restated_wherever_the_records_are() -> None:
    """Version 3, `previous` and its keys, and `check_runtime_summary`, held to the schemas.

    The planner and `docs/budgets.md` restate every key of `previous` and the added-cost
    meaning of `check_runtime_seconds`; the design-doc writer's two statements say both new
    answers travel inside the records it copies. None of them, nor either template, still
    describes the runtime as the whole command with its setup.
    """
    keys = _previous_keys()
    assert "`previous`" in _descriptions(PARTIAL)["budgets"]
    for key in keys:
        assert f"`{key}`" in _descriptions(PARTIAL)["budgets"], key
    assert "check_runtime_summary" in _descriptions(DESCRIPTION)
    assert "at most 240 characters" in _descriptions(DESCRIPTION)["check_runtime_summary"]
    stale = re.compile(r"entire command|whole command|setup/reads", re.IGNORECASE)
    for path in (*RESTATEMENTS, "templates/plan-task-budgets.md.j2"):
        text = " ".join((REPO_ROOT / path).read_text(encoding="utf-8").split())
        assert stale.search(text) is None, (path, stale.search(text))
    for path in RESTATEMENTS:
        text = " ".join((REPO_ROOT / path).read_text(encoding="utf-8").split())
        for named in ("`previous`", "`check_runtime_summary`"):
            assert named in text, (path, named)
    for path in ("docs/budgets.md", "personas/planner.yaml"):
        text = " ".join((REPO_ROOT / path).read_text(encoding="utf-8").split())
        assert "integer literal 3" in text, path
        assert "adds to the repository's checks" in text, path
        assert "exists only to feed" in text, path
        assert ", ".join(f"`{key}`" for key in keys[:-1]) in text, path


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
    ],
)
def test_current_guidance_points_to_the_single_design_visibility_rule(path: str) -> None:
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
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
