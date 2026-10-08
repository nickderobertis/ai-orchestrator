"""The design document is a template this host owns, and the role that writes it points there.

`templates/design-doc.md.j2` is registered as `design-doc` in `templates/templates.yaml`
and states the document: its five sections, each rendered in its own block beside a
`<name>_guidance` block whose Jinja comment says how to write it, and in its front matter
the data shape of every answer. `personas/design-doc.yaml` is the role dispatched to write
and to review one, and it sends both sides to the resolve command their task names instead
of restating any of it — one question gets one answer, and the answer belongs to the file
that holds it. The format is terse prose, complete interfaces.

Everything here is read through the real boundary a writer uses: the pinned engine's
`template resolve` and `template check`, reached through `scripts/onepipeline.sh` the way
a launch reaches them, and the pinned plan store's `template variables` and `template
render`. Nothing is doubled, and no expectation is read from the template it holds.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker,shell_test_tiers_stay_split] Every
file this module reads is in the key its tier is memoized on: `orchestrator:test` is keyed on
`nx.json`'s `codeWorkspace`, which is the whole workspace less `docs/` and `*.md`, so
`templates/design-doc.md.j2`, `templates/templates.yaml`, `personas/design-doc.yaml` and
`scripts/onepipeline.sh` are all in it, and the published CLIs it spawns — through the
wrapper a launch uses, which is the boundary this module is about — are the pinned installs
`uv.lock` names, which is in it too, and so are the signed-off sample's answers and the
body they render to under `tests/fixtures/design_doc/`. A project of its own would be keyed
on the same files.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from published_tools import ONETASKGRAPH_BIN

from orchestrator import plan_budgets
from orchestrator.root import REPO_ROOT

#: The registered name, the file that supplies it on this host, and the role bound to it.
NAME = "design-doc"
TEMPLATE = REPO_ROOT / "templates" / "design-doc.md.j2"
PERSONA = REPO_ROOT / "personas" / "design-doc.yaml"
WRAPPER = REPO_ROOT / "scripts" / "onepipeline.sh"

#: The signed-off sample: answers, and the body the user approved them rendering to, kept
#: byte for byte as the candidate the user signed off rendered it.
SAMPLE = REPO_ROOT / "tests" / "fixtures" / "design_doc"

#: The sha256 of each signed-off sample file as the user signed it off in round three, so a
#: fixture that drifted from what was signed off fails here rather than passing as itself.
SIGNED_OFF = {
    "sample-answers.json": "2a799ade3eed692eeb995d5f384ee86173118a5188231066f361b1efb2f3cc98",
    "sample-body.txt": "71f0caffb18e2c9e48caa6e9d040ad380270baaafe856116411bffc0d5c466b1",
}

#: The five sections, in the order the document carries them, each as the block that renders
#: it and the heading it renders. Stated here rather than read from the template, because a
#: gate that took its expectation from its subject would pass whatever that subject said —
#: which is the whole of what it is here to catch.
SECTIONS = (
    ("what", "What"),
    ("why", "Why"),
    ("architecture", "Architecture"),
    ("acceptance_criteria", "Acceptance criteria"),
    ("planned_tasks", "Planned tasks"),
)

#: The four budget sections, between Architecture and Acceptance criteria, each as the block
#: that renders it and the heading it renders. Each renders only for a plan that answers
#: its budget answers, so a document whose plan carries none reads as it always did.
BUDGET_SECTIONS = (("budgets", "Budgets"),)

#: The block holding what applies to the whole document, ahead of every section.
DOCUMENT_GUIDANCE = "document_guidance"

#: Every block the template defines, in order: the document's guidance, then each section's
#: guidance block directly ahead of the block that renders it.
BLOCKS = (DOCUMENT_GUIDANCE,) + tuple(
    name
    for block, _ in (*SECTIONS[:3], *BUDGET_SECTIONS, *SECTIONS[3:])
    for name in (f"{block}_guidance", block)
)

#: The keys of an object answer, each naming the keys of the objects it lists, or `None`.
type Keys = dict[str, Keys | None]

#: Each variable the template declares: its type, its item type, and for a list of objects
#: the keys every object carries, with the keys of each nested list of objects.
VARIABLES: dict[str, tuple[str, str | None, Keys | None]] = {
    "what": ("text", None, None),
    "why": ("text", None, None),
    "architecture": ("text", None, None),
    "units": (
        "list",
        "object",
        {
            "name": None,
            "repository": None,
            "part": None,
            "summary": None,
            "reversible": {"title": None, "text": None},
            "decisions": {"name": None, "justification": None, "summary": None, "artifact": None},
        },
    ),
    "acceptance_criteria": ("list", "string", None),
    "planned_tasks": (
        "list",
        "object",
        {"task": None, "unit": None, "delivers": None, "depends_on": None, "location": None},
    ),
}

#: The budget answers, which are what `python -m orchestrator.plan_budgets` prints and are
#: optional so a document written for a plan carrying none renders as it did: each
#: variable's type, item type and object keys, and the reason a plan predates budgets.
BUDGET_VARIABLES: dict[str, tuple[str, str | None, Keys | None]] = {
    "predates_budgets": ("text", None, None),
    "plan_budgets": ("object", None, dict.fromkeys(plan_budgets.MODERN_PLAN_VARIABLES)),
    "budgets": (
        "list",
        "object",
        dict.fromkeys(("node", "location", *plan_budgets.MODERN_BUDGET_KEYS)),
    ),
}

#: One phrase per format rule, which only that rule's statement carries. The guidance
#: comments state each exactly once: none would leave a writer and its judge without it,
#: and two is a second answer to drift from the first. No variable description carries
#: one, because the descriptions hold the data shape and the guidance holds the judgment.
RULES = {
    "reader": "technical product manager with no depth in this domain",
    "prose": "no more technical than the plan's own goal statement",
    "length": "Exactness is spent on what is hard to undo",
    "sections": "In this order: What, Why, Architecture, Budgets, Acceptance criteria",
    "budgets copy the writer's answers": "orchestrator.plan_budgets <project>` prints for the",
    "predates budgets": "This plan predates budgets: <reason>",
    "no budget detail": "So this section carries none of that detail",
    "no budget section without a budget change": "renders no Budgets section at all",
    "no alert blocks": "No GitHub alert blocks",
    "guidance is never rendered": "never an HTML comment",
    "what and why": "carry no interface detail and no implementation detail",
    "one subsection per unit": "Every unit that changes gets one subsection",
    "high-cost decisions": "per high-cost decision the unit owns",
    "the literal artifact": "The template owns every language label",
    "reversible bullets": "one bullet per trivial or low-cost decision",
    "no links between sections": "no links between sections",
    "reversibility levels": "defined by who would have to act to undo the decision",
    "the stricter level": "When in doubt, the stricter level",
    "store locations": "never one composed by hand",
}

#: Sample answers, each distinct so the rendering can be read back against them. The first
#: unit is part of a repository and owns a high-cost decision and two reversible ones; the
#: second is a whole repository and owns neither, so it renders its paragraph alone.
ANSWERS: dict[str, object] = {
    "what": "A paginated node listing.",
    "why": "An operator cannot see past the first screen.",
    "architecture": "The engine pages, the view follows.",
    "units": [
        {
            "name": "Listing route",
            "repository": "onepipeline",
            "part": "src/listing.rs",
            "summary": "The route returns one page and a cursor.",
            "reversible": [
                {"title": "Page size", "text": "At most fifty nodes.\nNever configurable."},
                {"title": "Older callers", "text": "A call with no cursor reads page one."},
            ],
            "decisions": [
                {
                    "name": "The cursor",
                    "justification": "outside scripts store it, and each would need changing.",
                    "summary": "An opaque token that never names a node.",
                    "artifact": "```diff\n+cursor: string\n```",
                },
                {
                    "name": "No total count",
                    "justification": "dashboards we do not run would read it.",
                    "summary": "A page never says how many nodes exist.",
                    "artifact": "",
                },
            ],
        },
        {
            "name": "View",
            "repository": "onepipeline-ui",
            "part": "",
            "summary": "The view follows the cursor.",
            "reversible": [],
            "decisions": [],
        },
    ],
    "acceptance_criteria": ["The listing pages.", "The view follows the cursor."],
    "planned_tasks": [
        {
            "task": "feat: page the listing",
            "unit": "Listing route",
            "delivers": "the route",
            "depends_on": "none",
            "location": "https://example.invalid/issues/1",
        },
        {
            "task": "feat: follow the cursor",
            "unit": "View",
            "delivers": "the view",
            "depends_on": "feat: page the listing",
            "location": "/plans/tasks/view.md",
        },
    ],
}

#: What each unit of `ANSWERS` renders as, under the Architecture overview.
UNITS_RENDERED = """\
### Listing route — `onepipeline` (`src/listing.rs`)

The route returns one page and a cursor.

#### The cursor

**Reversibility: high cost** — outside scripts store it, and each would need changing.

An opaque token that never names a node.

```diff
+cursor: string
```

#### No total count

**Reversibility: high cost** — dashboards we do not run would read it.

A page never says how many nodes exist.

**Reversible:**

- **Page size.** At most fifty nodes.
  Never configurable.
- **Older callers.** A call with no cursor reads page one.

### View — `onepipeline-ui`

The view follows the cursor.
"""


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


def _render(loader_document: str, answers: object, tmp_path: Path) -> str:
    """The body the pinned plan store renders `answers` to through `loader_document`."""
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(answers), encoding="utf-8")
    rendered = _run(
        [str(ONETASKGRAPH_BIN), "template", "render", "--template-loader", "-"]
        + ["--answers", str(path), "--no-interactive"],
        loader_document,
    )
    assert rendered.returncode == 0, rendered.stderr
    return rendered.stdout


# llmlint: ignore-block[suppressions_justified] The front matter is onetaskgraph's open template
# contract, parsed from YAML; each caller subscripts the one field it reads, so a moved shape fails
# there rather than at a model of it written here.
def _front_matter(template: Path = TEMPLATE) -> dict[str, Any]:
    text = template.read_text(encoding="utf-8")
    found = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    assert found is not None, f"{template.name} opens with no front matter"
    matter: dict[str, Any] = yaml.safe_load(found.group(1))
    return matter


# llmlint: ignore-end[suppressions_justified]


def _guidance_comments() -> list[str]:
    """Every Jinja comment inside a `*_guidance` block, whitespace folded to one space."""
    source = TEMPLATE.read_text(encoding="utf-8")
    blocks = re.findall(
        r"{%-?\s*block\s+(\w+_guidance)\s*-?%}(.*?){%-?\s*endblock", source, re.DOTALL
    )
    return [
        " ".join(comment.split())
        for _, body in blocks
        for comment in re.findall(r"{#-?(.*?)-?#}", body, re.DOTALL)
    ]


def test_the_launch_environment_resolves_the_template_from_this_hosts_root(loader: str) -> None:
    """The name resolves at the host layer, to this file, and extends nothing of the engine's."""
    stated = json.loads(loader)
    assert stated["reference"] == f"onepipeline:{NAME}", stated
    assert stated["layer"] == "host", stated
    assert stated["role"] == "document", stated
    assert Path(stated["path"]) == TEMPLATE, stated
    # The loader carries the engine's own templates as its search set whatever the entry
    # uses, so what says this one uses none of them is its own source, less its comments,
    # whose guidance shows a repository layer how to extend it.
    source = re.sub(r"{#.*?#}", "", TEMPLATE.read_text(encoding="utf-8"), flags=re.DOTALL)
    assert not re.search(r"{%-?\s*(extends|include|import|from)\b", source), (
        f"{TEMPLATE.name} builds on another template, and the design document is this "
        f"host's own:\n{source}"
    )


def test_the_engine_checks_the_template_through_the_launch_environment() -> None:
    checked = _run([str(WRAPPER), "template", "check", NAME])
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "ok" in checked.stdout, checked.stdout


def test_each_section_and_its_guidance_is_a_block_of_its_own_in_order() -> None:
    """A repository overrides one section, or one section's guidance, by naming its block."""
    source = TEMPLATE.read_text(encoding="utf-8")
    defined = tuple(re.findall(r"{%-?\s*block\s+(\w+)\s*-?%}", source))
    assert defined == BLOCKS, f"{TEMPLATE.name} defines the blocks {defined}"


def test_the_template_declares_each_variable_with_its_type(loader: str) -> None:
    listed = _run(
        [str(ONETASKGRAPH_BIN), "template", "variables", "--template-loader", "-", "--json"],
        loader,
    )
    assert listed.returncode == 0, listed.stderr
    declared = {
        one["name"]: (one["type"], one.get("items"), one.get("required"))
        for one in json.loads(listed.stdout)["variables"]
    }
    assert declared == {
        **{name: (kind, items, True) for name, (kind, items, _) in VARIABLES.items()},
        **{name: (kind, items, False) for name, (kind, items, _) in BUDGET_VARIABLES.items()},
    }, declared


def _keys(shape: Keys) -> list[str]:
    """Every key `shape` names, its nested objects' keys included."""
    return [key for key, nested in shape.items() for key in [key, *_keys(nested or {})]]


@pytest.mark.parametrize(
    "variable",
    [name for name, (_, _, keys) in {**VARIABLES, **BUDGET_VARIABLES}.items() if keys],
    ids=str,
)
def test_an_object_variables_description_names_each_of_its_keys(variable: str) -> None:
    """The description is where a writer learns an object's keys, so it names every one."""
    description = _front_matter()["variables"][variable]["description"]
    keys = {**VARIABLES, **BUDGET_VARIABLES}[variable][2]
    assert keys is not None
    missing = [key for key in _keys(keys) if f"`{key}`" not in description]
    assert not missing, f"{variable}'s description never names {missing}:\n{description}"


@pytest.mark.parametrize("rule", RULES, ids=list(RULES))
def test_the_guidance_states_each_format_rule_exactly_once(rule: str) -> None:
    """Each rule is stated once, in the guidance a writer, its judge and a repository read."""
    stated = sum(comment.count(RULES[rule]) for comment in _guidance_comments())
    assert stated == 1, (
        f"the template's guidance states the {rule!r} rule {stated} times; it is stated "
        f"once, in the one place a writer and its judge both read it"
    )
    matter = _front_matter()
    descriptions = [matter["description"]] + [
        one["description"] for one in matter["variables"].values()
    ]
    assert not any(RULES[rule] in " ".join(one.split()) for one in descriptions), (
        f"a variable description restates the {rule!r} rule, which is the guidance's to state"
    )


def test_rendering_sample_answers_gives_the_sections_the_units_and_the_table(
    tmp_path: Path, loader: str
) -> None:
    body = _render(loader, ANSWERS, tmp_path)
    sections = re.findall(r"^## (.+)$", body, re.MULTILINE)
    assert tuple(sections) == tuple(heading for _, heading in SECTIONS), body
    architecture = body.split("## Architecture\n", 1)[1].split("\n## ", 1)[0]
    overview, units = architecture.strip().split("\n\n", 1)
    assert overview == ANSWERS["architecture"], architecture
    assert units.strip() == UNITS_RENDERED.strip(), units
    table = body.split("## Planned tasks\n", 1)[1].strip().splitlines()
    assert table == [
        "| Task | Unit | What it delivers | Depends on | Where it lives |",
        "| --- | --- | --- | --- | --- |",
        "| feat: page the listing | Listing route | the route | none "
        "| https://example.invalid/issues/1 |",
        "| feat: follow the cursor | View | the view | feat: page the listing "
        "| /plans/tasks/view.md |",
    ], table


def test_no_rendering_carries_guidance_a_comment_or_an_alert_block(
    tmp_path: Path, loader: str
) -> None:
    """Guidance is for the writer and the judge, never for the person reading the document."""
    body = _render(loader, ANSWERS, tmp_path)
    assert "<!--" not in body, body
    assert not re.search(r"^>\s*\[!", body, re.MULTILINE), body
    carried = [rule for rule, phrase in RULES.items() if phrase in " ".join(body.split())]
    assert not carried, f"the rendering carries the guidance for {carried}:\n{body}"


def test_the_signed_off_sample_renders_byte_for_byte(loader: str) -> None:
    """The answers the user signed off render to exactly the body they read."""
    held = {name: hashlib.sha256((SAMPLE / name).read_bytes()).hexdigest() for name in SIGNED_OFF}
    assert held == SIGNED_OFF, f"the sample fixtures are not the files signed off: {held}"
    rendered = _run(
        [str(ONETASKGRAPH_BIN), "template", "render", "--template-loader", "-"]
        + ["--answers", str(SAMPLE / "sample-answers.json"), "--no-interactive"],
        loader,
    )
    assert rendered.returncode == 0, rendered.stderr
    assert rendered.stdout == (SAMPLE / "sample-body.txt").read_text(encoding="utf-8")


def test_a_planned_task_answer_holding_a_delimiter_or_a_line_break_stays_in_its_cell(
    tmp_path: Path, loader: str
) -> None:
    """An answer is prose, so a pipe or a newline in it is text, never a column or a row.

    Rendered raw, a task titled with a pipe would split its row into six columns and a
    two-line delivery would end the table mid-row, so the document a person approves would
    no longer say which task lives where.
    """
    body = _render(
        loader,
        {
            **ANSWERS,
            "planned_tasks": [
                {
                    "task": "feat: read a | b",
                    "unit": "View",
                    "delivers": "the route\nand its view",
                    "depends_on": "",
                    "location": "/plans/tasks/pipe.md",
                }
            ],
        },
        tmp_path,
    )
    table = body.split("## Planned tasks\n", 1)[1].strip().splitlines()
    assert table[2:] == [
        "| feat: read a \\| b | View | the route and its view | none | /plans/tasks/pipe.md |"
    ], table


@pytest.mark.parametrize("ending", ["\r\n", "\r"], ids=["crlf", "bare-cr"])
def test_a_planned_task_answer_ending_lines_another_way_stays_on_one_row(
    tmp_path: Path, loader: str, ending: str
) -> None:
    """An answer written on another platform breaks no row: each line ending reads as a space."""
    body = _render(
        loader,
        {
            **ANSWERS,
            "planned_tasks": [
                {
                    "task": "feat: page",
                    "unit": "View",
                    "delivers": f"the route{ending}and its view",
                    "depends_on": "none",
                    "location": "/plans/tasks/page.md",
                }
            ],
        },
        tmp_path,
    )
    table = body.split("## Planned tasks\n", 1)[1].strip().splitlines()
    assert table[2:] == [
        "| feat: page | View | the route and its view | none | /plans/tasks/page.md |"
    ], table


def test_the_persona_points_at_the_resolved_chain_and_restates_none_of_it() -> None:
    """The role's whole statement of the document is a pointer at the template's chain."""
    persona = PERSONA.read_text(encoding="utf-8")
    loaded = yaml.safe_load(persona)
    for side in (loaded["system_prompt"], loaded["user"]["persona"]):
        flat = " ".join(side.split())
        assert "the resolve command" in flat, side
        assert f"onepipeline template resolve {NAME} --json" in flat, side
        assert "onetaskgraph template variables --template-loader -" in flat, side
        assert "_guidance" in flat, side
    flat = " ".join(persona.split())
    restated = [rule for rule, phrase in RULES.items() if phrase in flat]
    assert not restated, f"{PERSONA.name} restates the template's {restated}"
    assert not any(f"## {heading}" in flat for _, heading in SECTIONS), (
        f"{PERSONA.name} restates the document's sections"
    )
    assert not re.search(r"config/[A-Za-z0-9_.-]+\.md", persona), (
        f"{PERSONA.name} names a configuration file as the document's shape"
    )


def test_repository_guidance_extends_host_with_a_digest_covering_both_files(
    tmp_path: Path,
) -> None:
    """The installed tools render a repository guidance override without changing the body.

    The repository layer extends this tree's own `templates/`, the host root a launch names;
    only the last step, which changes the host template's text, does so on a copy of that
    root, so no tracked file is written.
    """
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    initialized = _run(["git", "init", str(checkout)])
    assert initialized.returncode == 0, initialized.stderr
    layer = checkout / ".onepipeline" / "templates" / "design-doc.md.j2"
    layer.parent.mkdir(parents=True)
    layer.write_text(
        '{% extends "onepipeline/host/design-doc.md.j2" %}\n'
        "{% block what_guidance %}{# Explain the change in ordinary words. #}{% endblock %}\n",
        encoding="utf-8",
    )

    # llmlint: ignore[suppressions_justified] The loader document is the engine's open JSON
    # contract, read here for three fields — `layer`, `chain` and `digest` — each subscripted
    # where it is read, so a moved shape fails there rather than at a model of it written here.
    def resolve(repository: bool, root: Path | None = None) -> dict[str, Any]:
        command = [str(WRAPPER), "template", "resolve", NAME, "--json"]
        if root is not None:
            command += ["--template-root", str(root)]
        if repository:
            command += ["--repo", str(checkout)]
        done = _run(command)
        assert done.returncode == 0, done.stderr
        resolved: dict[str, Any] = json.loads(done.stdout)
        return resolved

    own = resolve(False)
    extending = resolve(True)
    assert own["chain"] == [{"name": TEMPLATE.name, "layer": "host", "path": str(TEMPLATE)}], own
    assert extending["layer"] == "repository", extending
    assert [entry["path"] for entry in extending["chain"]] == [str(layer), str(TEMPLATE)], extending
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
    host = tmp_path / "host"
    host.mkdir()
    host_template = host / TEMPLATE.name
    host_template.write_bytes(TEMPLATE.read_bytes())
    (host / "templates.yaml").write_bytes((TEMPLATE.parent / "templates.yaml").read_bytes())
    assert resolve(True, host)["digest"] == extending["digest"], (
        "a byte copy of the host root resolves to another digest, so the change below would "
        "prove nothing about the text"
    )
    host_template.write_text(host_template.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert resolve(True, host)["digest"] != extending["digest"]


#: #1568's real budget answers, converted to the shape `python -m orchestrator.plan_budgets`
#: prints, and the summary they render to, which is what the user reads and approves.
BUDGET_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "budgets" / "issue-1568.json"
SUMMARY = REPO_ROOT / "tests" / "fixtures" / "budgets" / "issue-1568-summary.txt"


# llmlint: ignore[suppressions_justified] The fixture is the open JSON answer shape the writer copies, read by key and nested index throughout, so its values are `Any` here as the decoded document they are; `orchestrator/plan_budgets.py`'s parsers are its typed reading.  # noqa: E501
def _budget_answers() -> dict[str, Any]:
    held: dict[str, Any] = json.loads(BUDGET_FIXTURE.read_text(encoding="utf-8"))
    return held


def _section(body: str) -> str:
    """The `## Budgets` section of ``body``, up to the next section's heading."""
    return "## Budgets\n" + body.split("## Budgets\n", 1)[1].split("\n## ", 1)[0] + "\n"


def test_the_1568_fixture_holds_the_shape_the_budget_measures() -> None:
    """9 budgets, 6 concerns answered n/a, 4 repositories, one realistic-data choice."""
    answers = _budget_answers()
    plan = answers["plan_budgets"]

    assert set(answers) == set(plan_budgets.DESIGN_ANSWERS)
    assert len(answers["budgets"]) == 9
    assert sum(1 for entry in plan["checklist"] if entry["not_applicable"]) == 6
    effects = plan["repo_wide_effects"]
    assert len({entry["repository"] for entry in effects}) == 1
    assert sum(1 for entry in effects if entry["effect"] != "none") == 1
    assert len(plan["realistic_data"]) == 1 and plan["spike_findings"] == []
    owned = {entry["node"]: [] for entry in answers["budgets"]}
    for entry in answers["budgets"]:
        owned[entry["node"]].append(
            plan_budgets.parse_budgets(
                [{key: value for key, value in entry.items() if key not in {"node", "location"}}]
            )[0]
        )
    legacy = json.loads(BUDGET_FIXTURE.with_name("issue-1568-legacy.json").read_text())
    repositories = {e["repository"] for e in legacy["plan_budgets"]["repo_wide_effects"]}
    assert len(repositories) == 4
    plan_tasks = {"tasks": [{"id": str(i), "repo": repo} for i, repo in enumerate(repositories)]}
    assert plan_budgets.plan_rule_refusals(plan_budgets.parse_plan(plan), owned, plan_tasks) == []


def test_the_1568_fixture_renders_the_summary_a_person_approves(
    tmp_path: Path, loader: str
) -> None:
    """Sizing, the 10× line, one flat table, then one line per not-budgeted concern and so on."""
    body = _render(loader, {**ANSWERS, **_budget_answers()}, tmp_path)
    section = _section(body)

    assert section == SUMMARY.read_text(encoding="utf-8"), section
    sections = re.findall(r"^## (.+)$", body, re.MULTILINE)
    assert sections == ["What", "Why", "Architecture", "Budgets", "Acceptance criteria"] + [
        "Planned tasks"
    ], sections
    table = [line for line in section.splitlines() if line.startswith("|")]
    assert table[0] == "| Budget | Target | Basis | How it is measured | Check runtime |"
    for index, (entry, row) in enumerate(
        zip(_budget_answers()["budgets"], table[2:], strict=True), 1
    ):
        mode = entry.get("source", "not recorded")
        runtime = (
            f"≈{entry['check_runtime_seconds']} s"
            if entry.get("schema_version") == 2
            else "not recorded"
        )
        assert row == (
            f"| [{entry['name']}][{index}] | ≤{entry['threshold']} {entry['unit']} | "
            f"{entry['basis']} | {mode} | {runtime} |"
        ), row
    for entry in _budget_answers()["budgets"]:
        for detail in ("workload", "evidence", "command", "file", "measure"):
            assert str(entry[detail]) not in section, (entry["id"], detail)
    assert "No effect on" not in section and "Realistic data" not in section


def test_a_minimum_budget_and_a_cell_breaking_answer_stay_one_row(
    tmp_path: Path, loader: str
) -> None:
    answers = _budget_answers()
    first = {**answers["budgets"][0], "direction": "min", "name": "Pages | per\nsecond"}
    body = _render(loader, {**ANSWERS, **answers, "budgets": [first]}, tmp_path)

    (row,) = [line for line in _section(body).splitlines() if line.startswith("| [Pages")]
    assert row.startswith("| [Pages \\| per second][1] | ≥2 requests | measured | "), row


def test_a_plan_that_predates_budgets_renders_one_line_in_place_of_the_summary(
    tmp_path: Path, loader: str
) -> None:
    reason = "It was approved before plans carried budgets."
    body = _render(loader, {**ANSWERS, "predates_budgets": reason}, tmp_path)

    assert _section(body) == f"## Budgets\n\nThis plan predates budgets: {reason}\n\n"


@pytest.mark.parametrize(
    "budgets",
    ["none", "absent"],
)
def test_informative_omissions_and_effects_render_without_a_feature_budget_change(
    tmp_path: Path, loader: str, budgets: str
) -> None:
    """Omissions and root effects remain visible even without feature registration."""
    answers = _budget_answers()
    unchanged = [{**entry, "file_change": "none"} for entry in answers["budgets"]]
    held = {**answers, "budgets": unchanged if budgets == "none" else []}
    body = _render(loader, {**ANSWERS, **held}, tmp_path)

    assert "## Budgets" in body
    assert "Not budgeted" in body and "Repo-wide effects" in body
    assert "Realistic data" not in _section(body)
    assert ("| Budget |" in body) == (budgets == "none")


def test_spike_findings_render_one_line_each_only_when_there_are_any(
    tmp_path: Path, loader: str
) -> None:
    answers = _budget_answers()
    findings = [
        {"spike": "spike-a", "finding": "f", "changed": "c", "summary": "Uploads take 0.8 s."},
        {"spike": "spike-b", "finding": "f", "changed": "c", "summary": "Linear allows 3."},
    ]
    plan = {**answers["plan_budgets"], "spike_findings": findings}
    section = _section(_render(loader, {**ANSWERS, **answers, "plan_budgets": plan}, tmp_path))

    assert section.endswith(
        "**Spike findings:**\n\n- Uploads take 0.8 s.\n- Linear allows 3.\n\n"
    ), section


def test_a_repository_with_an_effect_is_never_also_listed_as_unaffected(
    tmp_path: Path, loader: str
) -> None:
    """One repository stating an effect and a `none` is affected; two named alike list once."""
    answers = _budget_answers()
    effects = [
        {"repository": "github.com/acme/app", "budget": "gate-time", "effect": "+5 s"}
        | {"summary": "The gate grows by 5 s."},
        {"repository": "github.com/acme/app", "budget": "cycle-time", "effect": "none"}
        | {"summary": ""},
        {"repository": "github.com/acme/docs", "budget": "", "effect": "none", "summary": ""},
        {"repository": "gitlab.com/other/docs", "budget": "", "effect": "none", "summary": ""},
    ]
    plan = {**answers["plan_budgets"], "repo_wide_effects": effects}
    section = _section(_render(loader, {**ANSWERS, **answers, "plan_budgets": plan}, tmp_path))

    (line,) = [one for one in section.splitlines() if one.startswith("**Repo-wide effects:**")]
    assert line == "**Repo-wide effects:** `app` / `gate-time`: The gate grows by 5 s.", line


def test_modern_architecture_description_restates_the_reader_models() -> None:
    from orchestrator import design_approval

    description = yaml.safe_load(TEMPLATE.read_text().split("---\n", 2)[1])["variables"]["units"][
        "description"
    ]
    for model in (
        design_approval.ModernDecision,
        design_approval.ProsePart,
        design_approval.CodePart,
    ):
        for key in model.__annotations__:
            assert f"`{key}`" in description or f"{key}:" in description, key
    for tag in design_approval.PART_KEYS:
        assert f'"{tag}"' in description
    assert "letters, digits, underscore, plus or hyphen" in description
    assert "nonempty ordered list" in description
