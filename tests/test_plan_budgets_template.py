"""A plan's budgets document is a template this host owns, registered as `plan-budgets`.

`templates/plan-budgets.md.j2` is registered in `templates/templates.yaml` as a document
template, declares the seven variables a planner answers, and renders each into a section
of its own, closing on the one-line record `orchestrator/plan_budgets.py` reads the
answers back from. Everything is read through the boundary a planner uses: the pinned
engine's `template resolve` and `template check` through `scripts/onepipeline.sh`, and the
pinned plan store's `template variables` and `template render`.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker,shell_test_tiers_stay_split] Every
file this module reads is in the key its tier is memoized on, for the reason
`tests/test_design_doc_template.py` gives: `templates/`, `scripts/onepipeline.sh` and the
pinned installs `uv.lock` names are all in `codeWorkspace`.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from published_tools import ONETASKGRAPH_BIN

from orchestrator import plan_budgets
from orchestrator.root import REPO_ROOT

NAME = "plan-budgets"
TEMPLATE = REPO_ROOT / "templates" / "plan-budgets.md.j2"
WRAPPER = REPO_ROOT / "scripts" / "onepipeline.sh"

#: Each variable: its type, its item type, and for a list of objects every key each object
#: carries. Stated here rather than read from the template, so a template that dropped one
#: fails rather than passing as itself.
VARIABLES: dict[str, tuple[str, str | None, tuple[str, ...]]] = {
    "workload": ("text", None, ()),
    "checklist": ("list", "object", ("concern", "budget", "not_applicable")),
    "ten_x": ("text", None, ()),
    "budgets": (
        "list",
        "object",
        (
            "id",
            "repository",
            "file",
            "measure",
            "inner_measure_reason",
            "unit",
            "direction",
            "threshold",
            "workload",
            "evidence",
            "command",
            "node",
            "file_change",
        ),
    ),
    "repo_wide_effects": ("list", "object", ("repository", "budget", "effect")),
    "realistic_data": ("list", "object", ("data", "choice", "reason", "artifact")),
    "spike_findings": ("list", "object", ("spike", "finding", "changed")),
}

#: A complete set of answers, every value distinct so each can be found in the rendering.
ANSWERS: dict[str, object] = {
    "workload": "2,000 nodes per plan, 3 runs at once.",
    "checklist": [
        {"concern": "latency", "budget": "listing-latency", "not_applicable": ""},
        {"concern": "spend", "budget": "", "not_applicable": "n/a because it calls no paid API"},
    ],
    "ten_x": "The listing slows first; listing-latency covers it.",
    "budgets": [
        {
            "id": "listing-latency",
            "repository": "github.com/acme/app",
            "file": "apps/web/budgets.yaml",
            "measure": "time to the first page in the browser",
            "inner_measure_reason": "the browser cannot be driven in CI",
            "unit": "ms",
            "direction": "max",
            "threshold": 812.5,
            "workload": "2,000 nodes",
            "evidence": "spike-listing measured 420 ms",
            "command": "bun run measure:listing",
            "node": "listing",
            "file_change": "add",
        }
    ],
    "repo_wide_effects": [
        {"repository": "github.com/acme/app", "budget": "gate-time", "effect": "+20 s"}
    ],
    "realistic_data": [
        {"data": "plan nodes", "choice": "hybrid", "reason": "a sample", "artifact": "nodes.json"}
    ],
    "spike_findings": [
        {"spike": "spike-listing", "finding": "pages at 100", "changed": "paging moved"}
    ],
}

#: The sections a rendering carries, in order, each opening with its heading.
SECTIONS = (
    "Workload",
    "Checklist",
    "At 10x realistic usage",
    "Budgets",
    "Repo-wide effects",
    "Realistic data",
    "Spike findings",
    "Record",
)


def _run(command: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=REPO_ROOT, input=stdin, text=True, capture_output=True, check=False
    )


@pytest.fixture(scope="module")
def loader() -> str:
    resolved = _run([str(WRAPPER), "template", "resolve", NAME, "--json"])
    assert resolved.returncode == 0, resolved.stderr
    return resolved.stdout


def _render(loader: str, answers: object, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(answers), encoding="utf-8")
    return _run(
        [str(ONETASKGRAPH_BIN), "template", "render", "--template-loader", "-"]
        + ["--answers", str(path), "--no-interactive"],
        loader,
    )


def test_the_name_is_registered_as_a_document_template_at_the_host_layer(loader: str) -> None:
    registered = yaml.safe_load((REPO_ROOT / "templates" / "templates.yaml").read_text())
    assert registered["templates"][NAME]["role"] == "document"
    stated = json.loads(loader)
    assert stated["reference"] == plan_budgets.TEMPLATE_REFERENCE, stated
    assert stated["layer"] == "host" and stated["role"] == "document", stated
    assert Path(stated["path"]) == TEMPLATE, stated
    checked = _run([str(WRAPPER), "template", "check", NAME])
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_the_template_declares_the_seven_variables_with_their_shapes(loader: str) -> None:
    listed = _run(
        [str(ONETASKGRAPH_BIN), "template", "variables", "--template-loader", "-", "--json"],
        loader,
    )
    assert listed.returncode == 0, listed.stderr
    declared = {
        one["name"]: (one["type"], one.get("items"), one["required"], one["description"])
        for one in json.loads(listed.stdout)["variables"]
    }
    assert {name: held[:3] for name, held in declared.items()} == {
        name: (kind, items, True) for name, (kind, items, _) in VARIABLES.items()
    }
    assert tuple(declared) == plan_budgets.VARIABLES
    for name, (_, _, keys) in VARIABLES.items():
        missing = [key for key in keys if f"`{key}`" not in declared[name][3]]
        assert not missing, f"{name}'s description never names {missing}"


def test_a_complete_rendering_shows_each_section_and_records_every_answer(
    loader: str, tmp_path: Path
) -> None:
    rendered = _render(loader, ANSWERS, tmp_path)
    assert rendered.returncode == 0, rendered.stderr
    body = rendered.stdout

    assert tuple(re.findall(r"^## (.+)$", body, re.MULTILINE)) == SECTIONS, body
    assert "| latency | listing-latency |  |" in body
    assert "| spend |  | n/a because it calls no paid API |" in body
    assert "### `listing-latency`" in body
    assert "- **Target:** max 812.5 ms" in body
    assert "- **Inner measure, because:** the browser cannot be driven in CI" in body
    assert "- **Command:** `bun run measure:listing`" in body
    assert "- **Owned by node:** `listing`" in body
    assert "| github.com/acme/app | gate-time | +20 s |" in body
    assert "- **plan nodes** — hybrid: a sample (`nodes.json`)" in body
    assert "- **spike-listing** — pages at 100 Changed: paging moved" in body
    found = plan_budgets.RECORD.search(body)
    assert found is not None, body
    assert json.loads(found.group("record")) == ANSWERS


def test_a_plan_proposing_no_budget_says_so_in_each_empty_section(
    loader: str, tmp_path: Path
) -> None:
    empty = {**ANSWERS, "budgets": [], "realistic_data": [], "spike_findings": []}
    rendered = _render(loader, empty, tmp_path)
    assert rendered.returncode == 0, rendered.stderr

    assert "This plan proposes no budget." in rendered.stdout
    assert "This plan makes no data realistic." in rendered.stdout
    assert "No spike finding changed this plan." in rendered.stdout


def test_a_required_answer_left_out_is_refused_by_the_store(loader: str, tmp_path: Path) -> None:
    unanswered = {key: value for key, value in ANSWERS.items() if key != "ten_x"}
    refused = _render(loader, unanswered, tmp_path)
    assert refused.returncode == 2
    assert "ten_x" in refused.stderr


def test_the_templates_name_the_vocabularies_the_check_holds_answers_to() -> None:
    """Each copy of a vocabulary a description states is held to the check's own.

    `orchestrator/plan_budgets.py` is the one source the check refuses by; the planner and
    the design-document writer read the descriptions instead, so a description naming a
    value the check refuses — or leaving out one it takes — is a planner told the wrong rule.
    """
    for template in (TEMPLATE, REPO_ROOT / "templates" / "design-doc.md.j2"):
        found = re.match(r"---\n(.*?)\n---\n", template.read_text(encoding="utf-8"), re.DOTALL)
        assert found is not None, template
        described = " ".join(
            yaml.safe_load(found.group(1))["variables"]["budgets"]["description"].split()
        )
        named = re.search(r"`direction` \(string, (.*?)\)", described)
        assert named is not None, described
        assert re.findall(r"`(\w+)`", named.group(1)) == list(plan_budgets.Direction), described
        changes = re.search(r"`file_change` \(string, (.*?)\)", described)
        assert changes is not None, described
        assert re.findall(r"`(\w+)`", changes.group(1)) == list(plan_budgets.FileChange), described
        assert f"`{plan_budgets.BUDGETS_FILE}`" in described, described
        data = " ".join(
            yaml.safe_load(found.group(1))["variables"]["realistic_data"]["description"].split()
        )
        choices = re.search(r"`choice` \((.*?)\)", data)
        assert choices is not None, data
        assert re.findall(r"`([\w-]+)`", choices.group(1)) == list(plan_budgets.DataChoice), data
