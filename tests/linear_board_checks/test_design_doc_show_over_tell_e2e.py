"""The design document shows first and folds the rest, and an earlier answer renders as before.

Past What, Why and Goals, a design document shows each high-cost decision's gist and canonical
change open and folds everything longer under a plain-words summary: its details, why it is high
cost, a unit's reversible decisions by group, and every visual change past the featured ones,
whose before image is the one block nested in another. Each of those is a layout the
`design-doc` template writes, read off the body the real chain renders: this host's template
through the pinned engine's `template resolve design-doc`, piped into the locked plan store's
`document create` in a temporary `authoring` folder of Markdown, and read back with `document
show`.

The same answers rendered through a copy of the template as it stood before this layout
(`fixtures/todays_design_doc/`, a host root of its own) show what the layout checks are worth:
every check of a behaviour the layout introduces fails there. And a unit answered in an earlier
shape renders through both templates to the same bytes, because a stored document keeps the
answers it was written with.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] The approved acceptance
criteria of the task that introduced this layout name this journey, in this tracker project, as
the check of the template's rendered layout, and the project's inputs already cover the
templates and the plan-store pin it reads; its six renderings take seconds.
"""

from __future__ import annotations

import base64
import copy
import json
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import onetaskgraph_user_layer
import pytest
from github_board import AUTHORING_SOURCE, _plan_environment
from published_tools import ENGINE_BIN, ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The host root holding this repository's own templates.
TEMPLATES = REPO_ROOT / "templates"
#: A host root holding the design-doc template as it stood before it showed first.
TODAYS = Path(__file__).parent / "fixtures" / "todays_design_doc"

#: A one-pixel PNG, the bytes every image asset of these documents holds.
PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

#: Five visual changes, four marked featured, so the template features three and folds two.
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
    {
        "name": "A refusal names the item",
        "description": "The item's title, then why.",
        "after": "refusal-after.png",
        "featured": True,
    },
    {
        "name": "The board shows a preview badge",
        "description": "Only while a preview runs.",
        "after": "badge-after.png",
        "before": "badge-before.png",
        "featured": True,
    },
    {
        "name": "The log keeps the preview",
        "description": "Under the run's own heading.",
        "after": "log-after.png",
        "before": "log-before.png",
        "featured": True,
    },
]

#: Answers in the shape the template now states, for every layout it writes: a unit whose two
#: decisions share one `cost_reason`, show their change and fold their details, with a unit
#: `show` and two reversible groups; a unit whose one decision carries its own
#: `justification`, with an ungrouped run of reversible decisions; and a unit whose
#: `cost_reason` covers its one decision. The decisions' empty `justification`, `summary` and
#: `artifact` and the groups' `title` and `text` are the keys today's template reads, so the
#: same answers render through it too; this template reads none of them where a decision has
#: a `gist` or a group has `items`.
ANSWERS: dict[str, object] = {
    "what": "Preview a copy before it writes.",
    "why": "A copy that lands wrong is undone by hand.",
    "goals": ["A preview writes nothing."],
    "architecture": "The copy planner stops after the plan; the board shows it.",
    "visual_changes": VISUAL_CHANGES,
    "units": [
        {
            "name": "Copy planner",
            "repository": "onetaskgraph",
            "part": "",
            "summary": "A preview stops after the plan and prints it.",
            "show": [
                {"type": "code", "language": "console", "text": "$ copy --dry-run\ncreate  one"},
            ],
            "cost_reason": "Other tools parse the report, so changing it breaks them.",
            "decisions": [
                {
                    "name": "The preview report is the copy report",
                    "justification": "",
                    "summary": "",
                    "artifact": "",
                    "gist": "A preview prints exactly the report a copy prints.",
                    "show": [
                        {"type": "code", "language": "diff", "text": '+ "preview": true'},
                        {"type": "prose", "text": "For example, one task already on the board:"},
                        {"type": "code", "language": "console", "text": "update  one"},
                    ],
                    "details": [
                        {"summary": "Nothing is written", "parts": []},
                        {
                            "summary": "Older readers keep working",
                            "parts": [
                                {"type": "prose", "text": "The report's shape is unchanged."},
                                {"type": "code", "language": "json", "text": '{"items": []}'},
                            ],
                        },
                    ],
                },
                {
                    "name": "A refusal stops the preview",
                    "justification": "",
                    "summary": "",
                    "artifact": "",
                    "gist": "The first refused item ends the preview with its reason.",
                    "show": [{"type": "code", "language": "text", "text": "refused  one"}],
                    "details": [
                        {
                            "summary": "The exit status alone says it was refused",
                            "parts": [{"type": "prose", "text": "It exits 4."}],
                        }
                    ],
                },
            ],
            "reversible": [
                {
                    "group": "Flags",
                    "gist": "one `--dry-run` flag & its short form",
                    "title": "Flags",
                    "text": "Two flags.",
                    "items": [
                        {"title": "Adds `--dry-run` to `copy`", "text": "Spelled as elsewhere."},
                        {"title": "Adds `-n` for it", "text": "The short form."},
                    ],
                },
                {
                    "group": "Output",
                    "gist": "write order",
                    "title": "Output",
                    "text": "Its order.",
                    "items": [{"title": "Prints items in write order", "text": "As written."}],
                },
            ],
        },
        {
            "name": "Board",
            "repository": "onetaskgraph",
            "part": "crates/board",
            "summary": "The board shows a preview badge.",
            "decisions": [
                {
                    "name": "The badge is a board field",
                    "justification": "people filter their boards by it.",
                    "summary": "",
                    "artifact": "",
                    "gist": "A preview sets one field the board shows.",
                    "show": [{"type": "code", "language": "text", "text": "Preview: yes"}],
                    "details": [],
                }
            ],
            "reversible": [
                {
                    "group": "",
                    "gist": "",
                    "title": "Ungrouped",
                    "text": "Its run.",
                    "items": [
                        {"title": "Names the field Preview", "text": "One word."},
                        {"title": "Clears it after the copy", "text": "When it ends."},
                    ],
                }
            ],
        },
        {
            "name": "Copy lock",
            "repository": "onetaskgraph",
            "part": "crates/lock",
            "summary": "A preview holds the board's copy lock while it plans.",
            "cost_reason": "A copy started during a preview waits on this lock.",
            "decisions": [
                {
                    "name": "A preview takes the copy lock",
                    "justification": "",
                    "summary": "",
                    "artifact": "",
                    "gist": "A preview and a copy of one board never overlap.",
                    "show": [{"type": "code", "language": "text", "text": "locked  plans"}],
                    "details": [],
                }
            ],
            "reversible": [
                {
                    "group": "",
                    "gist": "",
                    "title": "Ungrouped",
                    "text": "Its run.",
                    "items": [{"title": "Names the lock after the board", "text": "One name."}],
                }
            ],
        },
        {
            "name": "Copy log",
            "repository": "onetaskgraph",
            "part": "crates/log",
            "summary": "The log keeps each preview under the run's own heading.",
            "show": [{"type": "code", "language": "text", "text": "## Preview\ncreate  one"}],
            "decisions": [],
            "reversible": [
                {
                    "group": "Heading",
                    "gist": "one heading per run",
                    "title": "Heading",
                    "text": "Its heading.",
                    "items": [{"title": "Heads each preview Preview", "text": "One word."}],
                },
                {
                    "group": "Retention",
                    "gist": "kept as long as the run",
                    "title": "Retention",
                    "text": "Its retention.",
                    "items": [{"title": "Keeps it with the run's log", "text": "Not apart."}],
                },
            ],
        },
    ],
    "acceptance_criteria": ["A preview writes nothing."],
}

#: A unit in each shape a document written before this layout holds: a flat reversible list,
#: decisions of `content` with and without `examples`, and decisions of `summary` and
#: `artifact`, with an artifact and without.
EARLIER_ANSWERS: dict[str, object] = {
    "what": "Preview a copy.",
    "why": "A copy that lands wrong is undone by hand.",
    "architecture": "One unit.",
    "units": [
        {
            "name": "Copy planner",
            "repository": "onetaskgraph",
            "part": "crates/copy",
            "summary": "A preview stops after the plan.",
            "reversible": [
                {"title": "Adds `--dry-run` & `-n`", "text": "Spelled as elsewhere."},
                {"title": "Prints items in write order", "text": "As written."},
            ],
            "decisions": [
                {
                    "name": "The preview report is the copy report",
                    "justification": "other tools parse the report.",
                    "content": [
                        {"type": "prose", "text": "A preview prints the copy's report."},
                        {"type": "code", "language": "json", "text": '{"items": []}'},
                    ],
                },
                {
                    "name": "A refusal stops the preview",
                    "justification": "scripts read its exit status.",
                    "content": [{"type": "prose", "text": "The first refusal ends it."}],
                    "examples": [
                        {"type": "code", "language": "text", "text": "copy   # prints ``` x ```"}
                    ],
                },
                {
                    "name": "The badge is a board field",
                    "justification": "people filter by it.",
                    "summary": "A preview sets one field.",
                    "artifact": "```diff\n+ Preview: yes\n```",
                },
                {
                    "name": "The field is cleared",
                    "justification": "views sort by it.",
                    "summary": "It is cleared after the copy.",
                    "artifact": "",
                },
            ],
        },
        {
            "name": "Board",
            "repository": "onetaskgraph",
            "part": "",
            "summary": "Nothing else changes.",
            "reversible": [],
            "decisions": [],
        },
    ],
    "acceptance_criteria": ["A preview writes nothing."],
}


def _run(command: Sequence[str], environment: Mapping[str, str], stdin: str | None = None) -> str:
    """Run ``command`` from the repository root, answering its standard output."""
    result = subprocess.run(  # noqa: S603 - the pinned engine or the locked plan-store CLI
        list(command),
        cwd=REPO_ROOT,
        env=dict(environment),
        input=stdin if stdin is not None else "",
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert result.returncode == 0, f"{' '.join(command)}:\n{result.stderr}"
    return result.stdout


def _rendered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, template_root: Path, answers: object
) -> str:
    """``answers`` rendered by ``template_root``'s `design-doc` through the real chain."""
    root = tmp_path / "authoring"
    root.mkdir(parents=True)
    monkeypatch.setenv(
        "XDG_CONFIG_HOME",
        str(
            onetaskgraph_user_layer.installed(
                tmp_path / "config", onetaskgraph_user_layer.without_private_stores()
            )
        ),
    )
    environment = _plan_environment(root)
    resolved = _run(
        [str(ENGINE_BIN), "template", "resolve", "design-doc", "--json"]
        + ["--template-root", str(template_root)],
        environment,
    )
    assets: list[str] = []
    for change in answers.get("visual_changes", []) if isinstance(answers, dict) else []:
        for key in ("after", "before"):
            if key in change:
                image = tmp_path / str(change[key])
                image.write_bytes(PIXEL)
                assets += ["--asset", str(image)]
    answered = tmp_path / "answers.json"
    answered.write_text(json.dumps(answers), encoding="utf-8")
    cli = [str(ONETASKGRAPH_BIN), "--json"]
    _run(
        cli
        + ["project", "create", AUTHORING_SOURCE, "--id", "preview", "--title", "Preview"]
        + ["--no-interactive"],
        environment,
    )
    _run(
        cli
        + ["document", "create", AUTHORING_SOURCE, "--project", "preview"]
        + ["--id", "preview-design", "--title", "Preview design", "--answers", str(answered)]
        + [*assets, "--template-loader", "-", "--no-interactive"],
        environment,
        stdin=resolved,
    )
    shown = json.loads(_run(cli + ["document", "show", "authoring:preview-design"], environment))
    (entry,) = shown["items"]
    content = entry["item"]["content"]
    assert isinstance(content, str), entry
    return content


@dataclass
class Block:
    """One expandable block in the authored form, and where it stands."""

    summary: str
    start: int
    parent: Block | None
    body: str = ""
    children: list[Block] = field(default_factory=list)


@dataclass
class Read:
    """A rendered body as its blocks and the text standing outside every block."""

    body: str
    blocks: list[Block]
    outside: str


def _read(body: str) -> Read:
    """``body``'s blocks, nesting included, and the lines that stand outside all of them."""
    lines = body.split("\n")
    blocks: list[Block] = []
    open_blocks: list[Block] = []
    outside: list[str] = []
    skip = 0
    for at, line in enumerate(lines):
        if skip:
            skip -= 1
            continue
        match line:
            case "<details>":
                summary = re.fullmatch(r"<summary>(.*)</summary>", lines[at + 1])
                assert summary is not None, f"a block at line {at} has no summary line:\n{body}"
                parent = open_blocks[-1] if open_blocks else None
                block = Block(summary[1], at, parent)
                if parent is not None:
                    parent.children.append(block)
                blocks.append(block)
                open_blocks.append(block)
                skip = 1
            case "</details>":
                block = open_blocks.pop()
                block.body = "\n".join(lines[block.start + 2 : at]).strip("\n")
            case _ if not open_blocks:
                outside.append(line)
            case _:
                pass
    return Read(body, blocks, "\n".join(outside))


def _html(text: str) -> str:
    """``text`` as the template writes it in a summary: entities, and backtick code as HTML."""
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"`([^`]*)`", r"<code>\1</code>", escaped)


def _fenced(language: str, text: str) -> str:
    """A code part as the template fences it: longer than any backtick run it holds."""
    fence = "`" * (3 + text.count("`"))
    return f"{fence}{language}\n{text}\n{fence}"


def _records(record: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    """The list of objects ``record`` answers ``key`` with, refused when it is anything else."""
    value = record[key]
    assert isinstance(value, list), (key, value)
    assert all(isinstance(one, Mapping) for one in value), (key, value)
    return value


def _text(record: Mapping[str, object], key: str) -> str:
    """The string ``record`` answers ``key`` with, refused when it is anything else."""
    value = record[key]
    assert isinstance(value, str), (key, value)
    return value


def _part(one: Mapping[str, object]) -> str:
    """One prose or code part as it renders."""
    text = _text(one, "text")
    return text if one["type"] == "prose" else _fenced(_text(one, "language"), text)


def _parts(record: Mapping[str, object], key: str) -> list[str]:
    """Each part ``record`` answers ``key`` with, as it renders."""
    return [_part(one) for one in _records(record, key)]


def _units(answers: Mapping[str, object]) -> list[Mapping[str, object]]:
    return _records(answers, "units")


def _between(text: str, start: str, end: str | None) -> str:
    """The stretch of ``text`` after ``start`` and before ``end`` (or its end)."""
    assert start in text, f"{start!r} is not in:\n{text}"
    rest = text.split(start, 1)[1]
    return rest.split(end, 1)[0] if end and end in rest else rest


def _named(read: Read, summary: str) -> list[Block]:
    return [block for block in read.blocks if block.summary == summary]


def _decisions(answers: Mapping[str, object]) -> list[Mapping[str, object]]:
    return [one for unit in _units(answers) for one in _records(unit, "decisions")]


def gist_and_show_open(read: Read, answers: Mapping[str, object]) -> list[str]:
    """Each decision's gist, then its `show` parts, stand outside every block under its heading."""
    found = []
    for decision in _decisions(answers):
        section = _between(read.outside, f"#### {decision['name']}\n", "\n#### ")
        shown = [_text(decision, "gist"), *_parts(decision, "show")]
        if not section.lstrip("\n").startswith(shown[0]):
            found.append(f"{decision['name']}: its gist does not open its section:\n{section}")
        if not all(one in section for one in shown):
            found.append(f"{decision['name']}: its show is not open:\n{section}")
    return found


def details_folded(read: Read, answers: Mapping[str, object]) -> list[str]:
    """A details entry with parts is one block under its summary; one without is a bullet."""
    found = []
    for decision in _decisions(answers):
        for entry in _records(decision, "details"):
            if entry["parts"]:
                blocks = _named(read, _html(_text(entry, "summary")))
                body = "\n\n".join(_parts(entry, "parts"))
                if [block.body for block in blocks] != [body]:
                    found.append(f"{entry['summary']}: not one block holding its parts")
            elif f"\n- {entry['summary']}\n" not in read.outside:
                found.append(f"{entry['summary']}: not a plain bullet")
    return found


def high_cost_folded(read: Read, answers: Mapping[str, object]) -> list[str]:
    """A justification, or a unit's `cost_reason` once with its count, folds under the label."""
    found = []
    for unit in _units(answers):
        decisions = _records(unit, "decisions")
        if "cost_reason" in unit:
            covered = f"all {len(decisions)} decisions" if len(decisions) > 1 else "the 1 decision"
            summary = f"<strong>High cost to undo</strong>: {covered} below"
            if [block.body for block in _named(read, summary)] != [unit["cost_reason"]]:
                found.append(f"{unit['name']}: its cost reason is not folded once with its count")
        for decision in decisions:
            if decision["justification"]:
                bodies = [
                    block.body for block in _named(read, "<strong>High cost to undo</strong>")
                ]
                if bodies.count(_text(decision, "justification")) != 1:
                    found.append(f"{decision['name']}: its justification is not folded")
    return found


def unit_show_after_summary(read: Read, answers: Mapping[str, object]) -> list[str]:
    """A unit's `show` renders open straight after its summary, before any decision."""
    found = []
    for unit in _units(answers):
        if "show" in unit:
            section = _between(read.outside, f"\n{unit['summary']}\n", "\n#### ")
            shown = "\n\n".join(_parts(unit, "show"))
            if not section.lstrip("\n").startswith(shown):
                found.append(f"{unit['name']}: its show does not follow its summary:\n{section}")
    return found


def reversible_grouped(read: Read, answers: Mapping[str, object]) -> list[str]:
    """A named group is one block: bold name, count and gist, one bullet per item inside."""
    found = []
    for unit in _units(answers):
        for group in _records(unit, "reversible"):
            if not group["group"]:
                continue
            items = _records(group, "items")
            gist = _html(_text(group, "gist"))
            summary = f"<strong>{group['group']}</strong> ({len(items)}) — {gist}"
            body = "\n".join(f"- **{item['title']}.** {item['text']}" for item in items)
            if [block.body for block in _named(read, summary)] != [body]:
                found.append(f"{group['group']}: not one block of one bullet per item")
    return found


def reversible_heading(read: Read, answers: Mapping[str, object]) -> list[str]:
    """A grouped unit's reversible decisions open under a line counting them all.

    `Reversible decisions` beside high-cost decisions, `Decisions, all reversible` without.
    """
    found = []
    for unit in _units(answers):
        groups = _records(unit, "reversible")
        if not groups:
            continue
        count = sum(len(_records(group, "items")) for group in groups)
        label = (
            "Reversible decisions" if _records(unit, "decisions") else "Decisions, all reversible"
        )
        section = _between(read.outside, f"\n{unit['summary']}\n", "\n### ")
        if f"\n**{label} ({count}):**\n" not in section:
            found.append(f"{unit['name']}: its reversible decisions are not headed {label}")
    return found


def ungrouped_one_block_each(read: Read, answers: Mapping[str, object]) -> list[str]:
    """An ungrouped run renders one block per item, its title the summary and its text the body."""
    found = []
    for unit in _units(answers):
        for group in _records(unit, "reversible"):
            for item in _records(group, "items") if not group["group"] else []:
                titled = _named(read, _html(_text(item, "title")))
                if [block.body for block in titled] != [item["text"]]:
                    found.append(f"{item['title']}: not one block of its own")
    return found


def _featured(changes: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    marked = [one for one in changes if one.get("featured")]
    return marked[:3] if marked else list(changes[:1])


def _visual(answers: Mapping[str, object]) -> list[Mapping[str, object]]:
    return _records(answers, "visual_changes")


def featured_open(read: Read, answers: Mapping[str, object]) -> list[str]:
    """At most three featured changes render open, the after image shown, the before folded."""
    found = []
    section = _between(read.outside, "## Visual changes\n", "\n## Architecture")
    featured = _featured(_visual(answers))
    headings = re.findall(r"^### (.+)$", section, re.MULTILINE)
    if headings != [str(one["name"]) for one in featured]:
        found.append(f"the open changes are {headings}, not the featured {len(featured)}")
    for change in featured:
        after = f"![{change['name']}](./{change['after']})"
        if after not in _between(section, f"### {change['name']}\n", "\n### "):
            found.append(f"{change['name']}: its after image is not open")
        if "before" in change:
            before = f"![Before: {change['name']}](./{change['before']})"
            folded = [block for block in _named(read, "Before") if block.body == before]
            if len(folded) != 1 or folded[0].parent is not None:
                found.append(f"{change['name']}: its before image is not folded beneath it")
    return found


def others_collapsed(read: Read, answers: Mapping[str, object]) -> list[str]:
    """Every other change is one block holding its description and after image, before nested."""
    found = []
    featured = _featured(_visual(answers))
    for change in [one for one in _visual(answers) if one not in featured]:
        blocks = _named(read, _html(str(change["name"])))
        if len(blocks) != 1 or blocks[0].parent is not None:
            found.append(f"{change['name']}: not one collapsed block")
            continue
        (block,) = blocks
        after = f"![{change['name']}](./{change['after']})"
        if not block.body.startswith(f"{change['description']}\n\n{after}"):
            found.append(f"{change['name']}: its block does not hold its description and after")
        nested = [one.summary for one in block.children]
        if nested != (["Before"] if "before" in change else []):
            found.append(f"{change['name']}: its block nests {nested}")
        elif "before" in change and block.children[0].body != (
            f"![Before: {change['name']}](./{change['before']})"
        ):
            found.append(f"{change['name']}: its Before block does not hold the before image")
    return found


def nests_only_before(read: Read, answers: Mapping[str, object]) -> list[str]:
    """No block opens inside another but a non-featured visual change's `Before` block."""
    del answers
    return [
        f"{block.summary} nests in {block.parent.summary}"
        for block in read.blocks
        if block.parent is not None
        and (block.summary != "Before" or block.parent.parent is not None)
    ]


Check = Callable[[Read, Mapping[str, object]], list[str]]
#: The checks of what this layout introduces, each of which today's template fails.
INTRODUCED: tuple[Check, ...] = (
    gist_and_show_open,
    details_folded,
    high_cost_folded,
    unit_show_after_summary,
    reversible_grouped,
    reversible_heading,
    featured_open,
    others_collapsed,
)
#: The checks of what it keeps, which today's template need not fail.
PRESERVED: tuple[Check, ...] = (ungrouped_one_block_each, nests_only_before)


def _none_featured(answers: Mapping[str, object]) -> dict[str, object]:
    """``answers`` with no visual change marked featured."""
    unmarked = copy.deepcopy(dict(answers))
    unmarked["visual_changes"] = [
        {key: value for key, value in change.items() if key != "featured"}
        for change in _visual(answers)
    ]
    return unmarked


def _findings(body: str, answers: Mapping[str, object], checks: Sequence[Check]) -> list[str]:
    read = _read(body)
    return [f"{check.__name__}: {one}" for check in checks for one in check(read, answers)]


def test_the_design_doc_shows_each_change_open_and_folds_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every layout check holds on the template's rendering, and on one featuring none."""
    body = _rendered(tmp_path / "marked", monkeypatch, TEMPLATES, ANSWERS)
    assert _findings(body, ANSWERS, INTRODUCED + PRESERVED) == [], body
    unmarked = _none_featured(ANSWERS)
    plain = _rendered(tmp_path / "unmarked", monkeypatch, TEMPLATES, unmarked)
    assert _findings(plain, unmarked, (featured_open, others_collapsed, nests_only_before)) == []


def test_todays_template_fails_every_check_of_what_the_layout_introduces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each introduced check finds something on the same answers rendered the way they were."""
    body = _rendered(tmp_path / "marked", monkeypatch, TODAYS, ANSWERS)
    passing = [check.__name__ for check in INTRODUCED if not check(_read(body), ANSWERS)]
    assert passing == [], body
    unmarked = _none_featured(ANSWERS)
    plain = _rendered(tmp_path / "unmarked", monkeypatch, TODAYS, unmarked)
    assert featured_open(_read(plain), unmarked), plain
    assert others_collapsed(_read(plain), unmarked), plain


def test_a_unit_in_an_earlier_shape_renders_byte_for_byte_as_todays_template_renders_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stored document keeps its answers, so a regeneration of it must not move its body."""
    now = _rendered(tmp_path / "now", monkeypatch, TEMPLATES, EARLIER_ANSWERS)
    before = _rendered(tmp_path / "before", monkeypatch, TODAYS, EARLIER_ANSWERS)
    assert "**Reversible:**" in before
    assert "<summary>Examples</summary>" in before
    assert "+ Preview: yes" in before
    assert now == before
