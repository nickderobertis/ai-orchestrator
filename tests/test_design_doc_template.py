"""The design document is a template this host owns, and the role that writes it points there.

`templates/design-doc.md.j2` is registered as `design-doc` in `templates/templates.yaml`
and states the document: its six sections, their order, and in its descriptions the reader
and every property the document is judged on. `personas/design-doc.yaml` is the role
dispatched to write and to review one, and it names the template's variables instead of
restating any of it — one question gets one answer, and the answer belongs to the file
that holds it.

Everything here is read through the real boundary a writer uses: the pinned engine's
`template resolve` and `template check`, reached through `scripts/onepipeline.sh` the way
a launch reaches them, and the pinned plan store's `template variables` and `template
render`. Nothing is doubled.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker,shell_test_tiers_stay_split] Every
file this module reads is in the key its tier is memoized on: `orchestrator:test` is keyed on
`nx.json`'s `codeWorkspace`, which is the whole workspace less `docs/` and `*.md`, so
`templates/design-doc.md.j2`, `templates/templates.yaml`, `personas/design-doc.yaml` and
`scripts/onepipeline.sh` are all in it, and the published CLIs it spawns — through the
wrapper a launch uses, which is the boundary this module is about — are the pinned installs
`uv.lock` names, which is in it too. A project of its own would be keyed on the same files.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from published_tools import ONETASKGRAPH_BIN

from orchestrator.root import REPO_ROOT

#: The registered name, the file that supplies it on this host, and the role bound to it.
NAME = "design-doc"
TEMPLATE = REPO_ROOT / "templates" / "design-doc.md.j2"
PERSONA = REPO_ROOT / "personas" / "design-doc.yaml"
WRAPPER = REPO_ROOT / "scripts" / "onepipeline.sh"

#: The six sections, in the order the document carries them. Stated here rather than read
#: from the template, because a gate that took its expectation from its subject would pass
#: whatever that subject said — which is the whole of what it is here to catch.
SECTIONS = ("What", "Why", "Architecture", "Contracts", "Acceptance criteria", "Planned tasks")

#: Each variable the template declares, with the type and item type its answers take.
VARIABLES = {
    "what": ("text", None),
    "why": ("text", None),
    "architecture": ("text", None),
    "contracts": ("list", "string"),
    "acceptance_criteria": ("list", "string"),
    "planned_tasks": ("list", "object"),
}

#: Each judged-on property, by a phrase only its own statement carries. The descriptions
#: have to state each exactly once: none would leave a writer and its judge without it,
#: and two is a second answer to drift from the first.
PROPERTIES = {
    "reader": "technical product manager with no depth in this domain",
    "plain language": "plain language: simple, terse, precise, with no jargon",
    "no easily changed detail": "leaves out detail that is easily changed later",
    "expensive decisions": "decisions that are expensive to reverse",
    "repositories": "Where the plan spans more than one repository",
    "link, do not restate": "rather than restating any of it",
    "store locations": "never one composed by hand",
    "six sections": (
        "six sections in the order What, Why, Architecture, Contracts, Acceptance criteria, "
        "Planned tasks, and no other heading"
    ),
}

#: Sample answers, each distinct so the rendering can be read back against them.
ANSWERS: dict[str, object] = {
    "what": "A paginated node listing.",
    "why": "An operator cannot see past the first screen.",
    "architecture": "One route, one view.",
    "contracts": [
        "**The cursor.** An opaque token.\nIt never names a node.",
        "**The page.** At most fifty nodes.",
    ],
    "acceptance_criteria": ["The listing pages.", "The view follows the cursor."],
    "planned_tasks": [
        {
            "task": "feat: page the listing",
            "delivers": "the route",
            "depends_on": "none",
            "location": "https://example.invalid/issues/1",
        },
        {
            "task": "feat: follow the cursor",
            "delivers": "the view",
            "depends_on": "feat: page the listing",
            "location": "/plans/tasks/view.md",
        },
    ],
}


def _run(command: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=REPO_ROOT, input=stdin, text=True, capture_output=True, check=False
    )


@pytest.fixture(scope="module")
def loader() -> str:
    """The loader document the pinned engine states for `design-doc` through the wrapper."""
    resolved = _run([str(WRAPPER), "template", "resolve", NAME, "--json"])
    assert resolved.returncode == 0, resolved.stderr
    return resolved.stdout


# llmlint: ignore-block[suppressions_justified] The front matter is onetaskgraph's open template
# contract, parsed from YAML; each caller subscripts the one field it reads, so a moved shape fails
# there rather than at a model of it written here.
def _front_matter() -> dict[str, Any]:
    text = TEMPLATE.read_text(encoding="utf-8")
    found = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    assert found is not None, f"{TEMPLATE.name} opens with no front matter"
    matter: dict[str, Any] = yaml.safe_load(found.group(1))
    return matter


# llmlint: ignore-end[suppressions_justified]


def test_the_launch_environment_resolves_the_template_from_this_hosts_root(loader: str) -> None:
    """The name resolves at the host layer, to this file, and extends nothing of the engine's."""
    stated = json.loads(loader)
    assert stated["reference"] == f"onepipeline:{NAME}", stated
    assert stated["layer"] == "host", stated
    assert stated["role"] == "document", stated
    assert Path(stated["path"]) == TEMPLATE, stated
    # The loader carries the engine's own templates as its search set whatever the entry
    # uses, so what says this one uses none of them is its own source.
    source = TEMPLATE.read_text(encoding="utf-8")
    assert not re.search(r"{%-?\s*(extends|include|import|from)\b", source), (
        f"{TEMPLATE.name} builds on another template, and the design document is this "
        f"host's own:\n{source}"
    )


def test_the_engine_checks_the_template_through_the_launch_environment() -> None:
    checked = _run([str(WRAPPER), "template", "check", NAME])
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "ok" in checked.stdout, checked.stdout


def test_the_template_declares_the_six_variables_with_their_types(loader: str) -> None:
    listed = _run(
        [str(ONETASKGRAPH_BIN), "template", "variables", "--template-loader", "-", "--json"],
        loader,
    )
    assert listed.returncode == 0, listed.stderr
    declared = {
        one["name"]: (one["type"], one.get("items"), one.get("required"))
        for one in json.loads(listed.stdout)["variables"]
    }
    assert declared == {name: (kind, items, True) for name, (kind, items) in VARIABLES.items()}, (
        declared
    )


@pytest.mark.parametrize("judged", PROPERTIES, ids=list(PROPERTIES))
def test_the_descriptions_state_each_judged_on_property_exactly_once(judged: str) -> None:
    """The reader, and every property the document is judged on, is stated once."""
    matter = _front_matter()
    descriptions = [matter["description"]] + [
        one["description"] for one in matter["variables"].values()
    ]
    stated = sum(" ".join(one.split()).count(PROPERTIES[judged]) for one in descriptions)
    assert stated == 1, (
        f"the template's descriptions state the {judged!r} property {stated} times; it is "
        f"stated once, in the one place a writer and its judge both read it"
    )


def test_rendering_sample_answers_gives_the_six_sections_the_contracts_and_the_table(
    tmp_path: Path, loader: str
) -> None:
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(ANSWERS), encoding="utf-8")
    rendered = _run(
        [str(ONETASKGRAPH_BIN), "template", "render", "--template-loader", "-"]
        + ["--answers", str(answers), "--no-interactive"],
        loader,
    )
    assert rendered.returncode == 0, rendered.stderr
    body = rendered.stdout
    headings = re.findall(r"^#+ (.+)$", body, re.MULTILINE)
    assert tuple(headings) == SECTIONS, f"the rendering's headings are {headings}:\n{body}"
    contracts = body.split("## Contracts\n", 1)[1].split("\n## ", 1)[0]
    assert contracts.strip().splitlines() == [
        "- **The cursor.** An opaque token.",
        "  It never names a node.",
        "- **The page.** At most fifty nodes.",
    ], contracts
    table = body.split("## Planned tasks\n", 1)[1].strip().splitlines()
    assert table == [
        "| Task | What it delivers | Depends on | Where it lives |",
        "| --- | --- | --- | --- |",
        "| feat: page the listing | the route | none | https://example.invalid/issues/1 |",
        "| feat: follow the cursor | the view | feat: page the listing | /plans/tasks/view.md |",
    ], table


def test_a_planned_task_answer_holding_a_delimiter_or_a_line_break_stays_in_its_cell(
    tmp_path: Path, loader: str
) -> None:
    """An answer is prose, so a pipe or a newline in it is text, never a column or a row.

    Rendered raw, a task titled with a pipe would split its row into five columns and a
    two-line delivery would end the table mid-row, so the document a person approves would
    no longer say which task lives where.
    """
    answers = tmp_path / "answers.json"
    answers.write_text(
        json.dumps(
            {
                **ANSWERS,
                "planned_tasks": [
                    {
                        "task": "feat: read a | b",
                        "delivers": "the route\nand its view",
                        "depends_on": "",
                        "location": "/plans/tasks/pipe.md",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    rendered = _run(
        [str(ONETASKGRAPH_BIN), "template", "render", "--template-loader", "-"]
        + ["--answers", str(answers), "--no-interactive"],
        loader,
    )
    assert rendered.returncode == 0, rendered.stderr
    table = rendered.stdout.split("## Planned tasks\n", 1)[1].strip().splitlines()
    assert table[2:] == [
        "| feat: read a \\| b | the route and its view | none | /plans/tasks/pipe.md |"
    ], table


def test_the_persona_names_the_templates_variables_and_restates_none_of_it() -> None:
    """The role's whole statement of the document is a pointer at the template."""
    persona = PERSONA.read_text(encoding="utf-8")
    loaded = yaml.safe_load(persona)
    for side in (loaded["system_prompt"], loaded["user"]["persona"]):
        assert (
            f"onepipeline template resolve {NAME} --json | onetaskgraph template variables "
            "--template-loader -" in " ".join(side.split())
        ), side
    flat = " ".join(persona.split())
    restated = [judged for judged, phrase in PROPERTIES.items() if phrase in flat]
    assert not restated, f"{PERSONA.name} restates the template's {restated}"
    assert not any(section in flat for section in ("## What", "## Planned tasks")), (
        f"{PERSONA.name} restates the document's sections"
    )
    assert not re.search(r"config/[A-Za-z0-9_.-]+\.md", persona), (
        f"{PERSONA.name} names a configuration file as the document's shape"
    )


def test_repository_guidance_extends_host_with_a_digest_covering_both_files(
    tmp_path: Path,
) -> None:
    """The installed tools render a repository guidance override without changing the body."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    initialized = _run(["git", "init", str(checkout)])
    assert initialized.returncode == 0, initialized.stderr
    host = tmp_path / "host"
    host.mkdir()
    host_template = host / "design-doc.md.j2"
    host_template.write_bytes(TEMPLATE.read_bytes())
    (host / "templates.yaml").write_bytes((TEMPLATE.parent / "templates.yaml").read_bytes())
    layer = checkout / ".onepipeline" / "templates" / "design-doc.md.j2"
    layer.parent.mkdir(parents=True)
    layer.write_text(
        '{% extends "onepipeline/host/design-doc.md.j2" %}\n'
        "{% block what_guidance %}{# Explain the change in ordinary words. #}{% endblock %}\n",
        encoding="utf-8",
    )

    def resolve(repository: bool) -> dict[str, Any]:
        command = [
            str(WRAPPER),
            "template",
            "resolve",
            NAME,
            "--json",
            "--template-root",
            str(host),
        ]
        if repository:
            command += ["--repo", str(checkout)]
        done = _run(command)
        assert done.returncode == 0, done.stderr
        resolved: dict[str, Any] = json.loads(done.stdout)
        return resolved

    own = resolve(False)
    extending = resolve(True)
    assert extending["layer"] == "repository", extending
    assert len(extending["chain"]) == 2, extending
    assert {entry["path"] for entry in extending["chain"]} == {str(layer), str(host_template)}
    assert extending["digest"] != own["digest"]
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(ANSWERS), encoding="utf-8")
    bodies = []
    for loader_document in (own, extending):
        rendered = _run(
            [
                str(ONETASKGRAPH_BIN),
                "template",
                "render",
                "--template-loader",
                "-",
                "--answers",
                str(answers),
                "--no-interactive",
            ],
            json.dumps(loader_document),
        )
        assert rendered.returncode == 0, rendered.stderr
        bodies.append(rendered.stdout)
    assert bodies[0] == bodies[1]
    host_template.write_text(host_template.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert resolve(True)["digest"] != extending["digest"]
