"""A plan's budgets render from two templates this host owns, each held to one data model.

`templates/plan-task-budgets.md.j2` is the partial `templates/plan-task.md.j2` includes after a
task's `## Why`: the `## Budgets` section of the budgets the task owns. `templates/plan-
description.md.j2` is registered in `templates/templates.yaml` as `plan-description`, a
`project` template: the plan's own description, carrying its plan-level budget answers in
full. `orchestrator/plan_budgets.py` states the shape both render once, and every restatement
of it — the templates' variable descriptions, `docs/budgets.md` and `personas/planner.yaml` —
is held to it here. Everything is read through the boundary a planner uses: the pinned
engine's `template resolve` and `template check` through `scripts/onepipeline.sh`, and the
pinned plan store's `template variables` and `template render`.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker,shell_test_tiers_stay_split] Every
file this module reads is in the key its tier is memoized on, for the reason
`tests/test_design_doc_template.py` gives: `templates/`, `scripts/onepipeline.sh` and the
pinned installs `uv.lock` names are all in `codeWorkspace`. The tests reading this
repository's prose carry `reads_docs`.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import plan_fixture_source
import pytest
import yaml
from published_tools import ONETASKGRAPH_BIN

from orchestrator import plan_budgets
from orchestrator.root import REPO_ROOT

DESCRIPTION = REPO_ROOT / "templates" / "plan-description.md.j2"
PARTIAL = plan_budgets.TASK_PARTIAL
DESIGN_DOC = REPO_ROOT / "templates" / "design-doc.md.j2"
WRAPPER = REPO_ROOT / "scripts" / "onepipeline.sh"

#: A budget whose every value is distinct, so each can be found in the rendering.
BUDGET: dict[str, object] = {
    "id": "listing-latency",
    "name": "Time to the first page",
    "basis": "published docs",
    "repository": "github.com/acme/app",
    "file": "apps/web/budgets.yaml",
    "file_change": "change",
    "measure": "time to the first page in the browser",
    "inner_measure_reason": "the browser cannot be driven in CI",
    "unit": "ms",
    "direction": "max",
    "threshold": 812.5,
    "workload": "2,000 nodes behind the cursor",
    "evidence": "spike-listing measured 420 ms",
    "command": "bun run measure:listing",
}

#: Plan-level answers whose every value is distinct, so each can be found in the rendering.
ANSWERS: dict[str, object] = {
    "overview": "Page the node listing, so an operator sees past the first screen.",
    "sizing": "2,000 nodes per plan, 3 runs at once.",
    "workload": "2,000 nodes per plan, 3 runs at once, each node a 2 KB record.",
    "ten_x_summary": "The first page slows first.",
    "ten_x": "At 20,000 nodes the first page slows first; `listing-latency` covers it.",
    "checklist": [
        {"concern": "latency", "budget": "listing-latency", "not_applicable": "", "summary": ""},
        {
            "concern": "spend",
            "budget": "",
            "not_applicable": "n/a because it calls no paid API at all",
            "summary": "No paid API.",
        },
    ],
    "repo_wide_effects": [
        {
            "repository": "github.com/acme/app",
            "budget": "gate-time",
            "effect": "+20 s of journeys",
            "summary": "The gate grows by 20 s.",
        }
    ],
    "realistic_data": [
        {
            "data": "plan nodes",
            "choice": "hybrid",
            "reason": "a sample of real ones, widened by a generator",
            "artifact": "nodes.json",
            "summary": "Real nodes, widened.",
        }
    ],
    "spike_findings": [
        {
            "spike": "spike-listing",
            "finding": "pages at 100 hold.",
            "changed": "paging moved to 100",
            "summary": "Pages of 100.",
        }
    ],
}


def _run(command: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=REPO_ROOT, input=stdin, text=True, capture_output=True, check=False
    )


def _resolved(name: str) -> str:
    resolved = _run([str(WRAPPER), "template", "resolve", name, "--json"])
    assert resolved.returncode == 0, resolved.stderr
    return resolved.stdout


@pytest.fixture(scope="module")
def loader() -> str:
    return _resolved(plan_budgets.DESCRIPTION_TEMPLATE)


@pytest.fixture(scope="module")
def task_loader() -> str:
    return _resolved("plan-task")


def _render(loader: str, answers: object, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(answers), encoding="utf-8")
    return _run(
        [str(ONETASKGRAPH_BIN), "template", "render", "--template-loader", "-"]
        + ["--answers", str(path), "--no-interactive"],
        loader,
    )


def _declared(loader: str) -> dict[str, dict[str, object]]:
    listed = _run(
        [str(ONETASKGRAPH_BIN), "template", "variables", "--template-loader", "-", "--json"],
        loader,
    )
    assert listed.returncode == 0, listed.stderr
    return {one["name"]: one for one in json.loads(listed.stdout)["variables"]}


def _descriptions(template: Path) -> dict[str, str]:
    """Each variable ``template`` declares, by name, its description on one line."""
    found = re.match(r"---\n(.*?)\n---\n", template.read_text(encoding="utf-8"), re.DOTALL)
    assert found is not None, template
    return {
        name: " ".join(str(variable["description"]).split())
        for name, variable in yaml.safe_load(found.group(1))["variables"].items()
    }


def test_the_description_is_registered_as_a_project_template_at_the_host_layer(
    loader: str,
) -> None:
    registered = yaml.safe_load((REPO_ROOT / "templates" / "templates.yaml").read_text())
    assert registered["templates"][plan_budgets.DESCRIPTION_TEMPLATE]["role"] == "project"
    stated = json.loads(loader)
    assert stated["reference"] == plan_budgets.DESCRIPTION_REFERENCE, stated
    assert stated["layer"] == "host" and stated["role"] == "project", stated
    assert Path(stated["path"]) == DESCRIPTION, stated
    checked = _run([str(WRAPPER), "template", "check", plan_budgets.DESCRIPTION_TEMPLATE])
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_the_description_declares_the_plan_level_answers_in_the_models_order(
    loader: str,
) -> None:
    declared = _declared(loader)

    assert tuple(declared) == plan_budgets.MODERN_PLAN_VARIABLES
    for name in plan_budgets.PLAN_TEXTS:
        assert (declared[name]["type"], declared[name]["required"]) == ("text", True), name
    for name, model in plan_budgets.PLAN_LISTS.items():
        assert (declared[name]["type"], declared[name]["items"]) == ("list", "object"), name
        described = " ".join(str(declared[name]["description"]).split())
        missing = [key for key in model.__dataclass_fields__ if f"`{key}`" not in described]
        assert not missing, f"{name}'s description never names {missing}"
    assert declared["realistic_data"]["default"] == []
    assert declared["spike_findings"]["default"] == []


def test_a_description_renders_the_overview_then_every_answer_in_full(
    loader: str, tmp_path: Path
) -> None:
    rendered = _render(loader, ANSWERS, tmp_path)
    assert rendered.returncode == 0, rendered.stderr
    body = rendered.stdout

    assert body.startswith(f"{ANSWERS['overview']}\n\n## Budgets\n"), body
    for text in ("sizing", "workload", "ten_x_summary", "ten_x"):
        assert str(ANSWERS[text]) in body, text
    for name in plan_budgets.PLAN_LISTS:
        entries = ANSWERS[name]
        assert isinstance(entries, list)
        for entry in entries:
            for key, value in entry.items():
                if key != "summary" and value:
                    assert str(value) in body, (name, key)
    assert "- **latency:** covered by `listing-latency`." in body
    assert "- **spend:** not budgeted. n/a because it calls no paid API at all" in body


def test_a_description_with_no_data_or_finding_says_so(loader: str, tmp_path: Path) -> None:
    empty = {key: value for key, value in ANSWERS.items() if key not in plan_budgets.PLAN_LISTS} | {
        "checklist": [],
        "repo_wide_effects": [],
    }
    rendered = _render(loader, empty, tmp_path)
    assert rendered.returncode == 0, rendered.stderr

    assert "This plan makes no data realistic." in rendered.stdout
    assert "No spike finding changed this plan." in rendered.stdout


def test_a_required_plan_answer_left_out_is_refused_by_the_store(
    loader: str, tmp_path: Path
) -> None:
    unanswered = {key: value for key, value in ANSWERS.items() if key != "ten_x_summary"}
    refused = _render(loader, unanswered, tmp_path)
    assert refused.returncode == 2
    assert "ten_x_summary" in refused.stderr


def test_the_task_template_declares_the_partials_budgets_variable(task_loader: str) -> None:
    declared = _declared(task_loader)
    stated = json.loads(task_loader)

    assert "plan-task-budgets.md.j2" in [one["name"] for one in stated["chain"]]
    budgets = declared["budgets"]
    assert (budgets["type"], budgets["items"], budgets["required"], budgets["default"]) == (
        "list",
        "object",
        False,
        [],
    )
    assert budgets["declared_in"] == "plan-task-budgets.md.j2"


def _task(task_loader: str, tmp_path: Path, budgets: object = None) -> str:
    answers: dict[str, object] = {
        "what": "Page the listing.",
        "why": "An operator cannot see past the first screen.",
        "acceptance_criteria": ["The listing pages."],
    }
    if budgets is not None:
        answers["budgets"] = budgets
    rendered = _render(task_loader, answers, tmp_path)
    assert rendered.returncode == 0, rendered.stderr
    return rendered.stdout


def test_a_task_owning_budgets_renders_each_in_full_after_its_why(
    task_loader: str, tmp_path: Path
) -> None:
    inward = {**BUDGET, "id": "listing-memory", "name": "Memory", "inner_measure_reason": ""}
    body = _task(task_loader, tmp_path, [BUDGET, {**inward, "direction": "min"}])

    headings = re.findall(r"^## (.+)$", body, re.MULTILINE)
    assert headings[:4] == ["What", "Why", "Budgets", "Acceptance criteria"], headings
    section = body[body.index("## Budgets\n") : body.index("## Acceptance criteria")]
    assert "### Time to the first page" in section and "### Memory" in section
    assert "- **Id:** `listing-latency`" in section
    assert "- **Measure:** time to the first page in the browser" in section
    assert "- **Inner measure, because:** the browser cannot be driven in CI" in section
    assert section.count("Inner measure, because") == 1, "an empty reason renders no line"
    assert "- **Target:** at most 812.5 ms" in section
    assert "- **Target:** at least 812.5 ms" in section
    assert "- **Basis:** published docs" in section
    assert "- **Workload:** 2,000 nodes behind the cursor" in section
    assert "- **Evidence:** spike-listing measured 420 ms" in section
    assert "- **Command:** `bun run measure:listing`" in section
    assert (
        "- **Registered in:** `apps/web/budgets.yaml` of `github.com/acme/app`, an entry this "
        "task changes"
    ) in section
    # The section the check renders alone is exactly what the task carries.
    alone = _run(
        [str(ONETASKGRAPH_BIN), "template", "render", str(PARTIAL)]
        + ["--search-path", str(plan_budgets.TEMPLATE_ROOT), "--answers", "-", "--no-interactive"],
        json.dumps({"budgets": [BUDGET, {**inward, "direction": "min"}]}),
    )
    assert alone.returncode == 0, alone.stderr
    assert alone.stdout in body


def test_a_task_owning_no_budget_renders_no_budgets_section(
    task_loader: str, tmp_path: Path
) -> None:
    for budgets in (None, []):
        body = _task(task_loader, tmp_path, budgets)
        assert "## Budgets" not in body
        assert "## Why\n\nAn operator cannot see past the first screen.\n\n## Acceptance" in body


def test_a_command_already_using_backticks_keeps_its_delimiters(
    task_loader: str, tmp_path: Path
) -> None:
    command = "Run `bun run measure:listing` after seeding the listing."
    body = _task(task_loader, tmp_path, [{**BUDGET, "command": command}])
    assert f"- **Command:** {command}\n" in body
    assert f"- **Command:** `{command}`" not in body


#: A budget written to the guidance the `budgets` description gives: one product-owner-level
#: figure whose `measure` names the breakdown its analysis reports, and whose `command`
#: analyses what the gate's tests recorded rather than running a scenario of its own.
ANALYSED: dict[str, object] = {
    **BUDGET,
    "id": "sync-quota-points",
    "name": "Quota spent by one sync",
    "measure": (
        "API points one full sync spends against the hourly quota, broken down in the "
        "reporter's detail by phase, with each phase's requests beside its points"
    ),
    "inner_measure_reason": "",
    "unit": "points",
    "command": "uv run python budgets/analyse_sync.py",
    "schema_version": 2,
    "source": "telemetry",
    "check_runtime_seconds": 2,
}


def test_the_budget_level_guidance_reaches_a_planner_rendering_a_task_through_the_real_pipe(
    tmp_path: Path,
) -> None:
    """What a planner reads and what it renders, both through the pipe the persona names.

    A planner learns what `measure` and `command` hold from the `budgets` variable of the
    loader the pinned engine resolves for `plan-task`, read by the pinned plan store, and
    regenerates a task by piping that same loader into `onetaskgraph task render`. So the
    guidance is read off that loader rather than off the partial's source, and the task is
    created and regenerated in this process's own `test-fixtures` store.
    """
    loader = _resolved("plan-task")
    described = " ".join(str(_declared(loader)["budgets"]["description"]).split())
    assert "the breakdown its analysis reports in the onebudgetspec reporter's `detail`" in (
        described
    )
    assert "which are telemetry rather than budgets of their own" in described
    assert (
        "the command that analyses the telemetry the gate's tests record and reports through "
        "the onebudgetspec SDK"
    ) in described
    assert "it runs no scenario of its own only to measure" in described
    assert "the command that performs the measurement" not in described

    answers = tmp_path / "answers.json"
    answers.write_text(
        json.dumps(
            {
                "what": "Sync every issue from the tracker.",
                "why": "An operator's board misses issues past the first page.",
                "acceptance_criteria": ["Every issue arrives."],
            }
        ),
        encoding="utf-8",
    )
    created = _run(
        [str(ONETASKGRAPH_BIN), "task", "create", plan_fixture_source.SOURCE]
        + ["--project", f"budget-level-{tmp_path.name}", "--template-loader", "-"]
        + ["--title", f"sync every issue {tmp_path.name}", "--answers", str(answers)]
        + ["--no-interactive"],
        loader,
    )
    assert created.returncode == 0, created.stderr
    qualified = created.stdout.strip()
    budgets = tmp_path / "budgets.json"
    budgets.write_text(json.dumps({"budgets": [ANALYSED]}), encoding="utf-8")
    regenerated = _run(
        [str(ONETASKGRAPH_BIN), "task", "render", qualified, "--template-loader", "-"]
        + ["--answers", str(budgets), "--no-interactive", "--json"],
        loader,
    )
    assert regenerated.returncode == 0, regenerated.stderr
    shown = _run([str(ONETASKGRAPH_BIN), "task", "show", qualified, "--json"])
    assert shown.returncode == 0, shown.stderr
    (item,) = json.loads(shown.stdout)["items"]
    content = item["item"]["content"]
    section = content[content.index("## Budgets\n") : content.index("## Acceptance criteria")]
    assert f"- **Measure:** {ANALYSED['measure']}\n" in section
    assert "- **Command:** `uv run python budgets/analyse_sync.py`" in section
    assert "- **Measurement source:** telemetry" in section
    provenance = item["item"]["metadata"]["onetaskgraph.template"]
    assert provenance["digest"] == json.loads(loader)["digest"], provenance


def test_the_partials_description_states_the_models_keys_and_vocabularies() -> None:
    """Each copy of a key, a vocabulary or a cap a description states is held to the model's.

    `orchestrator/plan_budgets.py` is the one source the check refuses by; the planner and
    the design-document writer read the descriptions instead, so a description naming a value
    the check refuses — or leaving out one it takes — is a planner told the wrong rule.
    """
    described = _descriptions(PARTIAL)["budgets"]

    named = re.findall(r"`(\w+)` \(", described)
    assert tuple(named) == plan_budgets.MODERN_BUDGET_KEYS, named
    assert "`threshold` (a finite number)" in described
    for field, vocabulary in (
        ("direction", plan_budgets.Direction),
        ("file_change", plan_budgets.FileChange),
        ("basis", plan_budgets.Basis),
    ):
        stated = re.search(rf"`{field}` \(string, (.*?)\)", described)
        assert stated is not None, field
        assert re.findall(r"`([\w ]+)`", stated.group(1)) == list(vocabulary), field
    assert f"`{plan_budgets.BUDGETS_FILE}`" in described
    assert f"at most {plan_budgets.NAME_LIMIT} characters" in described
    assert f"`{plan_budgets.TASK_RECORD}`" in described


def test_the_descriptions_answers_state_the_models_caps_and_vocabularies() -> None:
    described = _descriptions(DESCRIPTION)

    assert f"at most {plan_budgets.SIZING_LIMIT} characters" in described["sizing"]
    assert f"at most {plan_budgets.TEN_X_SUMMARY_LIMIT} characters" in described["ten_x_summary"]
    for name in plan_budgets.PLAN_LISTS:
        assert f"at most {plan_budgets.SUMMARY_LIMIT} characters" in described[name], name
    assert f"never `{plan_budgets.NO_EFFECT}`" in (described["repo_wide_effects"])
    choices = re.search(r"`choice` \((.*?)\)", described["realistic_data"])
    assert choices is not None
    assert re.findall(r"`([\w-]+)`", choices.group(1)) == list(plan_budgets.DataChoice)


def test_the_design_documents_budget_answers_are_the_writers_three() -> None:
    described = _descriptions(DESIGN_DOC)

    assert set(plan_budgets.DESIGN_ANSWERS) <= set(described)
    for name in plan_budgets.PLAN_VARIABLES:
        assert f"`{name}`" in described["plan_budgets"], name
    assert f"`{plan_budgets.PLAN_RECORD}`" in described["plan_budgets"]
    assert f"`{plan_budgets.TASK_RECORD}`" in described["budgets"]


@pytest.mark.reads_docs
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


#: What the retired budgets document was reached by: its template file, its registration, its
#: reference, the constants and helpers naming it, and the record line it closed on.
RETIRED = re.compile(
    r"plan-budgets\.md\.j2|onepipeline:plan-budgets|\bBUDGETS_(?:TEMPLATE|REFERENCE|SUFFIX)\b"
    r"|budgets_document_id|is_budgets_document|## Record\b|<project>-budgets"
    r"|budgets document"
)


@pytest.mark.reads_docs
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


@pytest.mark.reads_docs
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


#: The installed onebudgetspec CLI, the authority for the result a budget's analysis writes.
CHECKER = REPO_ROOT / ".venv" / "bin" / "onebudgetspec"


@pytest.mark.reads_docs
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
