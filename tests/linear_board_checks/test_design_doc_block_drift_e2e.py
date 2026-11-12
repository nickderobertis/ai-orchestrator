"""The design-doc template's expandable blocks are the form the installed plan store translates.

The design document collapses each reversible decision, a decision's examples and the
Planned tasks table into expandable blocks, and it is read on whichever tracker its plan lands
on: a GitHub Projects board keeps the authored block, and Linear shows it only as its own
collapsible section, which the plan store writes from the authored form onetaskgraph's README
states under *Expandable blocks*. The template writes the block and the store recognises it,
and this is the one check in this repository that goes red when the two part.

Everything is real but the two trackers' APIs: a design document rendered through this host's
`design-doc` template by the pinned engine's `template resolve` piped into the locked plan
store's `document create`, in a temporary `authoring` folder of Markdown; the locked CLI's
own `project copy` and `document copy` to the loopback GitHub board (`tests/github_board.py`)
and the loopback Linear wire (`tests/linear_board.py`), and on from Linear into a second
folder of Markdown; and the pinned engine's launch-time `template check design-doc --item`
over the Linear copy. A block copied back out of Linear is held to its own lines byte for byte
and to its body as Linear stores that content anywhere (`MEASURED_REWRITES`), because a table's
delimiter row Linear rewrites is Linear's normalization rather than a block drifting. The same
journey over a template whose blocks drop the blank line after `</summary>` is shown to fail.
"""

from __future__ import annotations

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

#: Answers that make the template write every kind of block it writes: two reversible
#: decisions, one titled with backtick code and `&`, a decision with examples whose code part
#: holds backticks, and a planned-tasks table of two rows. Locations name no record, so no
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
        }
    ],
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

#: How many blocks the answers render: two reversible decisions, one examples block and the
#: planned-tasks table.
BLOCKS = 4

#: An authored block whole: `<details>` to `</details>`, each alone on its line.
AUTHORED_BLOCK = re.compile(r"^<details>\n.*?^</details>$", re.MULTILINE | re.DOTALL)
#: The parts of an authored block in the form the store translates.
AUTHORED_PARTS = re.compile(
    r"<details>\n<summary>(?P<summary>[^\n]+)</summary>\n\n(?P<body>.+)\n\n</details>", re.DOTALL
)


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
    answers.write_text(json.dumps(ANSWERS), encoding="utf-8")
    _cli(
        environment,
        *("project", "create", AUTHORING_SOURCE, "--id", PROJECT, "--title", PROJECT_TITLE),
        "--no-interactive",
    )
    _cli(
        environment,
        *("document", "create", AUTHORING_SOURCE, "--project", PROJECT, "--id", DOCUMENT),
        *("--title", DOCUMENT_TITLE, "--answers", str(answers)),
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
    the three entities as their characters, as onetaskgraph's README states.
    """
    parts = AUTHORED_PARTS.fullmatch(block)
    if parts is None:
        return None
    summary = re.sub(r"<code>(.*?)</code>", r"`\1`", parts["summary"])
    summary = html.unescape(re.sub(r"<strong>(.*?)</strong>", r"**\1**", summary))
    return f"+++ {summary}\n\n{linear_stored(parts['body'], MEASURED_REWRITES)}\n\n+++"


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
    authored = AUTHORED_BLOCK.findall(content)
    found: list[str] = []
    if len(authored) != BLOCKS or content.count("<details") != BLOCKS:
        found.append(f"the template wrote {len(authored)} blocks, not {BLOCKS}:\n{content}")

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
            if block not in issue.body
        ]

        (stored,) = linear.documents()
        text = str(stored["content"])
        for block in authored:
            form = _linear_form(block)
            if form is None or form not in text:
                found.append(f"Linear stores no section for this block:\n{block}\n---\n{text}")
        if any(line.startswith("<details") for line in text.splitlines()):
            found.append(f"Linear keeps a `<details>` line:\n{text}")

        linear_document = f"{LINEAR_DESTINATION}:{stored['id']}"
        checked = _engine(
            environment,
            *("template", "check", "design-doc", "--item", linear_document),
            *("--template-root", str(template_root)),
        )
        if checked.returncode != 0:
            found.append(f"the launch-time check refuses the Linear copy:\n{checked.stderr}")

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
            if form is None or form not in back:
                found.append(
                    f"the copy back from Linear does not hold this block:\n{block}\n---\n{back}"
                )
        unchanged = [block for block in authored if _returned_form(block) == block]
        if len(unchanged) != BLOCKS - 1:
            found.append(
                f"only the table's body is one Linear rewrites, yet {unchanged} "
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
