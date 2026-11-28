"""The design-doc template's expandable blocks are the form the installed plan store translates.

The design document collapses each reversible decision and group of them, a decision's
examples and details, why a decision or a unit's decisions are high cost, the visual changes
past the featured ones and each before image, the Budgets section's budgets changed for other
reasons and its existing coverage, and the Planned tasks table into expandable blocks — one, a
collapsed visual change's `Before` block, nested in another — and it is read on whichever
tracker its plan lands on: a GitHub Projects board keeps the authored block, and Linear shows
it only as its own collapsible section, a nested block as a section inside a section, which the
plan store writes from the authored form onetaskgraph's README states under *Expandable
blocks*. The template writes the block and the store recognises it, and this is the one check
in this repository that goes red when the two part.

Everything is real but the two trackers' APIs: a design document rendered through this host's
`design-doc` template by the pinned engine's `template resolve` piped into the locked plan
store's `document create`, in a temporary `authoring` folder of Markdown; the locked CLI's
own `project copy` and `document copy` to the loopback GitHub board (`tests/github_board.py`)
and the loopback Linear wire (`tests/linear_board.py`), and on from Linear into a second
folder of Markdown; and the pinned engine's launch-time `template check design-doc --item`
over the Linear copy. A block copied back out of Linear is held to its own lines byte for byte
and to its body as Linear stores that content anywhere (`MEASURED_REWRITES`), because a table's
delimiter row Linear rewrites is Linear's normalization rather than a block drifting; an image's
target is the one thing compared loosely, because each tracker serves the asset at an address of
its own. The same
journey over a template whose blocks drop the blank line after `</summary>` is shown to fail.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] The approved acceptance
criteria of the task that added the Budgets section's blocks name this journey, in its
tracker project, as the check that their Linear translation holds, and it reads the
design-doc template that project's inputs cover. Both cases measured about 4.7 s of wall
time on this host, so a template change rerunning it costs the gate little.
"""

from __future__ import annotations

import base64
import html
import json
import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

import onetaskgraph_user_layer
import pytest
from github_board import AUTHORING_SOURCE, BOARD, _plan_environment, _serving_board
from linear_board import MEASURED_REWRITES, linear_board, linear_stored
from published_tools import ENGINE_BIN, ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The project the journey documents, and its design document's id.
PROJECT = "design-doc-blocks"
DOCUMENT = f"{PROJECT}-design"
PROJECT_TITLE = "Design document blocks"
DOCUMENT_TITLE = "Design document blocks design"
#: The GitHub board every plan of this repository is copied onto.
GITHUB_DESTINATION = "plans"
#: The example user layer's Linear source an organization's plans land on.
LINEAR_DESTINATION = onetaskgraph_user_layer.ORGANIZATION_PLANS
#: The second folder of Markdown the Linear copy is copied back into.
RETURNED = "returned"

#: A one-pixel PNG, the bytes every image asset of the document holds.
PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

#: A featured visual change and a collapsed one, each with a before image.
VISUAL_CHANGES: list[dict[str, object]] = [
    {
        "name": "The preview lists what a copy would write",
        "description": "Each item on its own line.",
        "after": "preview-after.png",
        "before": "preview-before.png",
        "featured": True,
    },
    {
        "name": "`copy` ends with a count",
        "description": "One line after the items.",
        "after": "count-after.png",
        "before": "count-before.png",
    },
]

#: Answers that make the template write every kind of block it writes: in a unit answered in
#: the earlier shape, two reversible decisions, one titled with backtick code and `&`, and a
#: decision with examples whose code part holds backticks; in units answered in today's shape, a
#: decision's details block with a code part beside a plain bullet, the High cost to undo block
#: of a unit's `cost_reason` and of a decision's `justification`, a reversible group whose
#: summary holds `<strong>` and `<code>`, and an ungrouped reversible decision; a featured visual
#: change's folded before image and a collapsed visual change holding its after image and a
#: nested `Before` block; and a planned-tasks table of two rows. Locations name no record, so no
#: copy rewrites them.
ANSWERS: Mapping[str, object] = {
    "what": "Preview a copy before it writes.",
    "why": "A copy that lands wrong is undone by hand.",
    "architecture": "The copy planner stops after the plan and prints it.",
    "units": [
        {
            "name": "Copy planner",
            "repository": "onetaskgraph",
            "part": "",
            "summary": "A preview stops after the plan and prints it.",
            "reversible": [
                {
                    "title": "Adds a `--dry-run` flag to `copy`",
                    "text": "The flag is `--dry-run`, as every other verb spells it.",
                },
                {
                    "title": "Prints items in write order & exits `4` on a refusal",
                    "text": "Items print in the order the copy would write them.",
                },
            ],
            "decisions": [
                {
                    "name": "The preview report is the copy report",
                    "justification": "other tools parse the report.",
                    "content": [
                        {"type": "prose", "text": "A preview prints the copy's report."},
                        {"type": "code", "language": "json", "text": '{"items": []}'},
                    ],
                    "examples": [
                        {"type": "prose", "text": "Two tasks, one already on the board:"},
                        {
                            "type": "code",
                            "language": "text",
                            "text": "copy --dry-run   # prints ``` would-create ```",
                        },
                    ],
                }
            ],
        },
        {
            "name": "Preview report",
            "repository": "onetaskgraph",
            "part": "crates/report",
            "summary": "The preview prints the copy's report.",
            "cost_reason": "Other tools parse the report.",
            "reversible": [
                {
                    "group": "Flags",
                    "gist": "one `--dry-run` flag & its short form",
                    "items": [
                        {"title": "Adds `--dry-run`", "text": "As every verb spells it."},
                        {"title": "Adds `-n`", "text": "The short form."},
                    ],
                },
                {
                    "group": "Output",
                    "gist": "items in write order",
                    "items": [{"title": "Prints in write order", "text": "As written."}],
                },
            ],
            "decisions": [
                {
                    "name": "The report keeps its shape",
                    "gist": "A preview prints the report a copy prints.",
                    "show": [{"type": "code", "language": "diff", "text": '+ "preview": true'}],
                    "details": [
                        {"summary": "Nothing is written", "parts": []},
                        {
                            "summary": "Older readers keep working",
                            "parts": [
                                {"type": "prose", "text": "The shape is unchanged."},
                                {"type": "code", "language": "json", "text": '{"items": []}'},
                            ],
                        },
                    ],
                }
            ],
        },
        {
            "name": "Board",
            "repository": "onetaskgraph",
            "part": "",
            "summary": "The board shows a preview badge.",
            "reversible": [
                {
                    "group": "",
                    "gist": "",
                    "items": [{"title": "Names the field Preview", "text": "One word."}],
                }
            ],
            "decisions": [
                {
                    "name": "The badge is a board field",
                    "justification": "people filter their boards by it.",
                    "gist": "A preview sets one field the board shows.",
                    "show": [{"type": "code", "language": "text", "text": "Preview: yes"}],
                    "details": [],
                }
            ],
        },
    ],
    "visual_changes": VISUAL_CHANGES,
    "acceptance_criteria": ["A preview writes nothing."],
    "planned_tasks": [
        {
            "task": "feat: print the preview report",
            "unit": "Copy planner",
            "delivers": "the report",
            "depends_on": "none",
            "location": "the store's own location",
        },
        {
            "task": "feat: add the dry-run flag",
            "unit": "Copy planner",
            "delivers": "the flag",
            "depends_on": "feat: print the preview report",
            "location": "the store's own location",
        },
    ],
}


def _budget(budget_id: str, name: str, file_change: str, **moved: object) -> dict[str, object]:
    """A version-3 budget record as the design-doc writer copies it, linked to its task."""
    record: dict[str, object] = {
        "schema_version": 3,
        "id": budget_id,
        "name": name,
        "basis": "measured",
        "repository": "github.com/example/service",
        "file": "service-budgets.yaml",
        "file_change": file_change,
        "measure": "Seconds a person waits for a preview",
        "inner_measure_reason": "",
        "unit": "seconds",
        "direction": "max",
        "threshold": 5,
        "workload": "A plan of 12 tasks",
        "evidence": "Measured once.",
        "command": "scripts/measure-preview.sh",
        "source": "direct",
        "check_runtime_seconds": 2,
        "node": "preview",
        "location": "the store's own location",
        "anchor": f"budget-{budget_id}",
        "link": f"https://example.invalid/preview#budget-{budget_id}",
    }
    if file_change == "change":
        record["previous"] = {
            key: record[key]
            for key in ("name", "measure", "inner_measure_reason", "unit", "direction")
            + ("threshold", "workload", "source", "command", "check_runtime_seconds")
        } | moved
    return record


#: Budget answers that make the Budgets section write both of its blocks: a budget changed
#: only in its measure and command, collapsed among those changed for other reasons, and an
#: unchanged budget, collapsed as existing coverage.
BUDGET_ANSWERS: Mapping[str, object] = {
    "plan_budgets": {
        "schema_version": 2,
        "sizing": "A plan of up to 12 tasks.",
        "ten_x_summary": "A preview of 120 tasks waits longest.",
        "checklist": [],
        "check_runtime_summary": "No check is expensive.",
    },
    "budgets": [
        _budget("preview-seconds", "Preview time", "add"),
        _budget("copy-seconds", "Copy time", "change", measure="Copy time", command="x.sh"),
        _budget("plan-read-seconds", "Plan read time", "none"),
    ],
}

#: How many blocks the answers render outside any other: in the earlier-shape unit two
#: reversible decisions and one examples block; in today's shape a details block, two High cost
#: to undo blocks, two reversible groups and one ungrouped decision; the featured change's
#: before image and the collapsed change; the planned-tasks table; and the Budgets section's
#: other-changes and existing-coverage blocks.
BLOCKS = 14
#: The blocks nested in another: the collapsed visual change's `Before` block.
NESTED = 1
#: The blocks whose body Linear stores in its own form (`MEASURED_REWRITES`): the planned
#: tasks' and the existing coverage's tables, with Linear's delimiter row, and the other
#: changes' and both reversible groups' lists, with Linear's list marker.
REWRITTEN_BLOCKS = 5

#: The parts of an authored block in the form the store translates: its summary, and its body
#: to the last blank line before the block's closing line.
AUTHORED_PARTS = re.compile(
    r"<details>\n<summary>(?P<summary>[^\n]+)</summary>\n\n(?P<body>.+)\n\n</details>", re.DOTALL
)
#: An image reference, whose target each tracker rewrites to the address it serves it at.
IMAGE = re.compile(r"!\[(?P<alt>[^\]\n]*)\]\((?:<[^>\n]*>|[^)\n]*)\)")


def _blocks(content: str) -> list[str]:
    """Each authored block of ``content`` standing outside any other, nested blocks inside it.

    `<details>` to the `</details>` that closes it, each alone on its line.
    """
    lines = content.split("\n")
    found: list[str] = []
    depth = 0
    start = 0
    for at, line in enumerate(lines):
        match line:
            case "<details>":
                start = at if depth == 0 else start
                depth += 1
            case "</details>" if depth:
                depth -= 1
                if depth == 0:
                    found.append("\n".join(lines[start : at + 1]))
            case _:
                pass
    return found


def _images(text: str) -> str:
    """``text`` with every image's target left out, its alt text kept."""
    return IMAGE.sub(lambda match: f"![{match['alt']}](image)", text)


def _cli(environment: Mapping[str, str], *arguments: str, stdin: str | None = None) -> str:
    """Run the locked plan-store CLI with ``arguments``, answering its standard output."""
    result = subprocess.run(  # noqa: S603 - the locked plan-store CLI
        [str(ONETASKGRAPH_BIN), "--json", *arguments],
        cwd=REPO_ROOT,
        env=dict(environment),
        input=stdin,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert result.returncode == 0, f"onetaskgraph {' '.join(arguments)}:\n{result.stderr}"
    return result.stdout


def _engine(environment: Mapping[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the pinned engine with ``arguments``, as the launch's wrapper reaches it."""
    return subprocess.run(  # noqa: S603 - the pinned engine
        [str(ENGINE_BIN), *arguments],
        cwd=REPO_ROOT,
        env=dict(environment),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )


def _rendered(tmp_path: Path, environment: Mapping[str, str], template_root: Path) -> str:
    """Render the design document in `authoring` through ``template_root``'s `design-doc`."""
    resolved = _engine(
        environment,
        *("template", "resolve", "design-doc", "--json", "--template-root", str(template_root)),
    )
    assert resolved.returncode == 0, resolved.stderr
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({**ANSWERS, **BUDGET_ANSWERS}), encoding="utf-8")
    assets: list[str] = []
    for change in VISUAL_CHANGES:
        for key in ("after", "before"):
            image = tmp_path / str(change[key])
            image.write_bytes(PIXEL)
            assets += ["--asset", str(image)]
    _cli(
        environment,
        *("project", "create", AUTHORING_SOURCE, "--id", PROJECT, "--title", PROJECT_TITLE),
        "--no-interactive",
    )
    _cli(
        environment,
        *("document", "create", AUTHORING_SOURCE, "--project", PROJECT, "--id", DOCUMENT),
        *("--title", DOCUMENT_TITLE, "--answers", str(answers), *assets),
        *("--template-loader", "-", "--no-interactive"),
        stdin=resolved.stdout,
    )
    return _content(environment, f"{AUTHORING_SOURCE}:{DOCUMENT}")


def _content(environment: Mapping[str, str], qualified: str) -> str:
    """The document ``qualified`` as the CLI's own `show` reads it back."""
    (entry,) = json.loads(_cli(environment, "document", "show", qualified))["items"]
    content = entry["item"]["content"]
    assert isinstance(content, str), entry
    return content


def _linear_form(block: str) -> str | None:
    """``block`` as Linear stores its collapsible section, or `None` outside the authored form.

    `+++ LSUMMARY`, a blank line, the body as the wire stores any long form, a blank line and
    `+++`: LSUMMARY is the summary with `<code>` written as backticks, `<strong>` as `**` and
    the three entities as their characters, and a block nested in the body is a section of its
    own inside it, as onetaskgraph's README states.
    """
    sections = _sections(block)
    if sections is None:
        return None
    return linear_stored(sections, MEASURED_REWRITES)


def _sections(block: str) -> str | None:
    """``block`` written as Linear's sections, a block nested in its body as one inside it."""
    parts = AUTHORED_PARTS.fullmatch(block)
    if parts is None:
        return None
    summary = re.sub(r"<code>(.*?)</code>", r"`\1`", parts["summary"])
    summary = html.unescape(re.sub(r"<strong>(.*?)</strong>", r"**\1**", summary))
    body = parts["body"]
    for nested in _blocks(body):
        inner = _sections(nested)
        if inner is None:
            return None
        body = body.replace(nested, inner, 1)
    return f"+++ {summary}\n\n{body}\n\n+++"


def _returned_form(block: str) -> str | None:
    """``block`` as a copy back out of Linear writes it, or `None` outside the authored form.

    The authored block's own lines exactly, around its body as Linear stores any long form:
    a body Linear does not rewrite — a sentence, fenced code — comes back byte for byte, and a
    table comes back with the delimiter row Linear keeps (`MEASURED_REWRITES`).
    """
    parts = AUTHORED_PARTS.fullmatch(block)
    if parts is None:
        return None
    body = linear_stored(parts["body"], MEASURED_REWRITES)
    return f"<details>\n<summary>{parts['summary']}</summary>\n\n{body}\n\n</details>"


def _drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, template_root: Path) -> list[str]:
    """Every way the blocks ``template_root`` writes part from what the installed store does."""
    root = tmp_path / "authoring"
    returned = tmp_path / "returned"
    root.mkdir()
    returned.mkdir()
    # The example layer an organization's host declares, less the host's own private boards,
    # which this journey holds no credential for and must not reach.
    home = onetaskgraph_user_layer.installed(
        tmp_path / "config", onetaskgraph_user_layer.without_private_stores()
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    environment = {
        **_plan_environment(root),
        f"ONETASKGRAPH_SOURCES__{RETURNED.upper()}__PLUGIN": "local-md",
        f"ONETASKGRAPH_SOURCES__{RETURNED.upper()}__CONFIG__ROOT": str(returned),
        # The status a Linear project is copied back with is the one Linear names it by.
        f"ONETASKGRAPH_SOURCES__{RETURNED.upper()}__CONFIG__STATUS_MAPPING__PLANNED": "todo",
    }
    content = _rendered(tmp_path, environment, template_root)
    authored = _blocks(content)
    found: list[str] = []
    if len(authored) != BLOCKS or content.count("<details") != BLOCKS + NESTED:
        found.append(f"the template wrote {len(authored)} blocks, not {BLOCKS}:\n{content}")
    nested = [block for block in authored if block.count("<details>") > 1]
    if [block.count("<details>") for block in nested] != [1 + NESTED]:
        found.append(f"the template nests no Before block in a visual change:\n{content}")

    with _serving_board(linear=True) as remote, linear_board() as linear:
        # A copy onto `plans` builds every source its routes name, so the example's other
        # Linear source is pointed at the same loopback team rather than at a dead port.
        environment = {
            **environment,
            **remote,
            **linear.environment(LINEAR_DESTINATION),
            **linear.environment(onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS),
        }
        for destination in (GITHUB_DESTINATION, LINEAR_DESTINATION):
            _cli(
                environment, "project", "copy", f"{AUTHORING_SOURCE}:{PROJECT}", "--to", destination
            )
            _cli(
                environment,
                "document",
                "copy",
                f"{AUTHORING_SOURCE}:{DOCUMENT}",
                "--to",
                destination,
            )

        # A document's issue is titled with the store's own kind prefix before its title.
        (issue,) = [one for one in BOARD.issues if one.title.endswith(DOCUMENT_TITLE)]
        found += [
            f"the GitHub board does not store this block as authored:\n{block}"
            for block in authored
            if _images(block) not in _images(issue.body)
        ]

        (stored,) = linear.documents()
        text = str(stored["content"])
        for block in authored:
            form = _linear_form(block)
            if form is None or _images(form) not in _images(text):
                found.append(f"Linear stores no section for this block:\n{block}\n---\n{text}")
        if any(line.startswith("<details") for line in text.splitlines()):
            found.append(f"Linear keeps a `<details>` line:\n{text}")
        for block in nested:
            form = _linear_form(block)
            inner = form.split("\n\n+++ Before\n\n", 1) if form else []
            if len(inner) != 2 or not inner[1].endswith("\n\n+++\n\n+++"):
                found.append(f"Linear holds no section inside this block's section:\n{form}")

        linear_document = f"{LINEAR_DESTINATION}:{stored['id']}"
        checked = _engine(
            environment,
            *("template", "check", "design-doc", "--item", linear_document),
            *("--template-root", str(template_root)),
        )
        # The one known divergence: the pinned engine links onetaskgraph 0.16.0
        # (`tests/linked_divergences.py`), whose read of Linear's section inside a section is
        # not the authored nested block, so its launch-time check refuses a Linear copy holding
        # one as differing from its rendering. A plan on a GitHub board is unaffected. Once the
        # engine links onetaskgraph 0.17.2 or later the check accepts the copy and this goes red,
        # and the refusal is a finding again like any other.
        known = nested and "body differs from its rendering" in checked.stderr
        if checked.returncode != 0 and not known:
            found.append(f"the launch-time check refuses the Linear copy:\n{checked.stderr}")
        elif nested and checked.returncode == 0:
            found.append(
                "the launch-time check accepts a Linear copy holding a nested block, so the "
                "engine reads nested sections: expect the check to pass and retire this divergence"
            )

        (linear_project,) = linear.projects()
        _cli(
            environment,
            "project",
            "copy",
            f"{LINEAR_DESTINATION}:{linear_project['id']}",
            "--to",
            RETURNED,
        )
        _cli(environment, "document", "copy", linear_document, "--to", RETURNED)
        (returned_document,) = json.loads(
            _cli(environment, "document", "list", "--source", RETURNED)
        )["items"]
        back = _content(environment, str(returned_document["id"]))
        for block in authored:
            form = _returned_form(block)
            if form is None or _images(form) not in _images(back):
                found.append(
                    f"the copy back from Linear does not hold this block:\n{block}\n---\n{back}"
                )
        unchanged = [block for block in authored if _returned_form(block) == block]
        if len(unchanged) != BLOCKS - REWRITTEN_BLOCKS:
            found.append(
                f"only the tables' and the list's bodies are ones Linear rewrites, yet {unchanged} "
                "come back as authored"
            )
    return found


# llmlint: ignore-block[e2e_not_mocked] GitHub's Projects API and Linear's API are the two
# boundaries doubled, because driving either writes to a live tracker; the template, the
# pinned engine, the locked plan-store CLI, its copies and its reads are real.
def test_the_templates_blocks_reach_github_as_authored_linear_as_sections_and_come_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GitHub keeps each block, Linear stores each as `+++`, and the chain back is authored."""
    assert _drift(tmp_path, monkeypatch, REPO_ROOT / "templates") == []


def test_a_template_whose_block_drops_its_blank_line_after_the_summary_fails_the_journey(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A block the store does not recognise reaches Linear as literal text, and is found."""
    root = tmp_path / "drifted-templates"
    shutil.copytree(REPO_ROOT / "templates", root)
    template = root / "design-doc.md.j2"
    source = template.read_text(encoding="utf-8")
    drifted = source.replace("</summary>\n\n", "</summary>\n")
    assert drifted != source, "the template writes no `</summary>` followed by a blank line"
    template.write_text(drifted, encoding="utf-8")

    found = _drift(tmp_path, monkeypatch, root)

    assert any(one.startswith("Linear stores no section for this block") for one in found), found
    assert any(one.startswith("Linear keeps a `<details>` line") for one in found), found


# llmlint: ignore-end[e2e_not_mocked]
