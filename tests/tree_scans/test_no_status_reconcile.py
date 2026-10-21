"""Nothing this repository runs calls the plan store's `task status reconcile`.

The engine hands the store the issues each landing closes, and the store moves their cards
then (`AGENTS.md`, "Follow-ups: drafted while a run works, verified once it ends"), so the
board-wide reconcile is left an on-demand verb a person runs for the lag a closure made outside
the stack leaves. A recipe, script, template, graph, persona, config or package module that
ran it — at a flow's start, in a comment pass, on a schedule — would spend a board read on
every run for what the landing already wrote. So the verb may appear only in Markdown
documentation, and only in a paragraph stating it as on demand.
"""

from __future__ import annotations

import re
import subprocess

import pytest

from orchestrator.root import REPO_ROOT

#: Every file this scan reads is one git tracks, prose among them.
pytestmark = pytest.mark.reads_docs

#: The verb, however a command line spells the words between its parts, a continued line's
#: backslash among them.
VERB = re.compile(r"\bstatus(?:\s|\\)+reconcile\b")
#: What a documentation paragraph naming the verb says about when it runs.
ON_DEMAND = re.compile(r"\bon[ -]demand\b")
#: Where this scan's own statement of the rule, and every test quoting the verb, lives.
EXEMPT_PREFIX = "tests/"


def _tracked() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607 - git from the search path, as the gate runs it
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode()
    return [path for path in listed.split("\0") if path]


def _offending(path: str, text: str) -> list[str]:
    """Each place in ``text`` that names the verb other than as an on-demand verb."""
    if path.endswith(".md"):
        paragraphs = re.split(r"\n\s*\n", text)
        return [
            " ".join(paragraph.split())[:160]
            for paragraph in paragraphs
            if VERB.search(paragraph) and not ON_DEMAND.search(paragraph)
        ]
    return [
        f"line {text.count(chr(10), 0, found.start()) + 1}: {found[0]}"
        for found in VERB.finditer(text)
    ]


def test_no_tracked_file_runs_the_status_reconcile_verb() -> None:
    found: dict[str, list[str]] = {}
    for path in _tracked():
        if path.startswith(EXEMPT_PREFIX):
            continue
        try:
            text = (REPO_ROOT / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue
        offending = _offending(path, text)
        if offending:
            found[path] = offending

    assert found == {}, (
        "only documentation stating it as an on-demand verb may name `task status reconcile`; "
        f"these name it otherwise: {found}"
    )


@pytest.mark.parametrize(
    ("path", "text", "offends"),
    [
        ("justfile", "sync:\n    onetaskgraph task status reconcile --source followups\n", True),
        ("scripts/x.sh", 'exec "$store" task status \\\n  reconcile --source plans\n', True),
        ("docs/x.md", "Run `task status reconcile` after a closure.\n", True),
        ("docs/x.md", "Run `task status reconcile` on demand.\n\nElsewhere.\n", False),
    ],
)
def test_the_scan_tells_an_invocation_from_an_on_demand_statement(
    path: str, text: str, offends: bool
) -> None:
    assert bool(_offending(path, text)) is offends
