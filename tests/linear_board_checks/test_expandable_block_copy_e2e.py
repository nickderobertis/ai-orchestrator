"""A plan copied from this host writes an expandable block in each tracker's own form.

A plan's approver reads its design document on whichever tracker the plan lands on, and an
organization's plans land on Linear, which renders a `<details>` block as literal text. The
adopted plan store writes a hand-authored expandable block onto a GitHub Projects board as the
authored block and onto Linear as Linear's own collapsible section, and reads either back in
the authored form, as onetaskgraph's README states under *Expandable blocks*.

This journey drives the locked `.venv/bin/onetaskgraph` itself: a `local-md` project whose
task body, project description and document each hold one authored block, its summary
carrying `<code>`, copied with the CLI's own `project copy` and `document copy` to the
loopback GitHub board (`tests/github_board.py`) and the loopback Linear wire
(`tests/linear_board.py`), and read back through the CLI's own `show`. The block is written
by hand rather than rendered from the design-doc template, whose drift against the store's
form is a journey of its own.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path

import onetaskgraph_user_layer
import pytest
from github_board import AUTHORING_SOURCE, BOARD, _plan_environment, _serving_board
from linear_board import linear_board
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The project the journey authors, and the document filed under it.
PROJECT = "expandable-blocks"
DOCUMENT = f"{PROJECT}-design"
#: The titles each record is filed under, which is how each is found on either tracker.
TASK_TITLE = "feat: collapse the reasoning behind a block"
PROJECT_TITLE = "Expandable blocks"
DOCUMENT_TITLE = "Expandable blocks design"
#: The GitHub board every plan of this repository is copied onto.
GITHUB_DESTINATION = "plans"
#: The example user layer's Linear source an organization's plans land on.
LINEAR_DESTINATION = onetaskgraph_user_layer.ORGANIZATION_PLANS

# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] The authored block and its Linear form are the two shapes onetaskgraph's README states under "Expandable blocks" (a `<summary>` line whose `<code>TEXT</code>` Linear stores as a backtick span inside `+++ LSUMMARY` … `+++`); the installed CLI is what this journey holds to them, and a store release that moved either form fails here.  # noqa: E501 - a directive is one line
#: One expandable block in the authored form, written by hand: `<details>` and `</details>`
#: alone on their lines, a one-line summary carrying inline `<code>`, and a body of a
#: paragraph and a fenced block set off by one blank line on each side.
AUTHORED_BLOCK = (
    "<details>\n"
    "<summary>Why <code>project copy</code> collapses this</summary>\n"
    "\n"
    "The reasoning a reader opens only when they need it.\n"
    "\n"
    "```sh\n"
    "just copy-plan authoring:expandable-blocks\n"
    "```\n"
    "\n"
    "</details>"
)
#: The same block in Linear's collapsible form: the summary's `<code>` written as a
#: backtick span after `+++ `, the body unchanged, and `+++` alone closing it.
LINEAR_BLOCK = (
    "+++ Why `project copy` collapses this\n"
    "\n"
    "The reasoning a reader opens only when they need it.\n"
    "\n"
    "```sh\n"
    "just copy-plan authoring:expandable-blocks\n"
    "```\n"
    "\n"
    "+++"
)
# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


def _body(subject: str) -> str:
    """A record's content: prose either side of the one block, as a design document holds it."""
    return f"The {subject} states its decision first.\n\n{AUTHORED_BLOCK}\n\nThe rest follows.\n"


def _cli(environment: Mapping[str, str], *arguments: str) -> str:
    """Run the locked plan-store CLI with ``arguments``, answering its standard output."""
    result = subprocess.run(  # noqa: S603 - the locked plan-store CLI
        [str(ONETASKGRAPH_BIN), "--json", *arguments],
        cwd=REPO_ROOT,
        env=dict(environment),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert result.returncode == 0, f"onetaskgraph {' '.join(arguments)}:\n{result.stderr}"
    return result.stdout


def _authored(root: Path, environment: Mapping[str, str]) -> None:
    """Author the project, one task and one document in the `authoring` folder of Markdown."""
    for subject, name in (("project", "project.md"), ("task", "task.md"), ("document", "doc.md")):
        (root / name).write_text(_body(subject), encoding="utf-8")
    _cli(
        environment,
        "project",
        "create",
        AUTHORING_SOURCE,
        "--id",
        PROJECT,
        "--title",
        PROJECT_TITLE,
        "--body-file",
        str(root / "project.md"),
    )
    _cli(
        environment,
        "task",
        "create",
        AUTHORING_SOURCE,
        "--project",
        PROJECT,
        "--title",
        TASK_TITLE,
        "--body-file",
        str(root / "task.md"),
    )
    _cli(
        environment,
        "document",
        "create",
        AUTHORING_SOURCE,
        "--project",
        PROJECT,
        "--id",
        DOCUMENT,
        "--title",
        DOCUMENT_TITLE,
        "--body-file",
        str(root / "doc.md"),
    )


def _copied(environment: Mapping[str, str], destination: str) -> None:
    """Copy the project, its task with it, and then its document, onto ``destination``."""
    _cli(environment, "project", "copy", f"{AUTHORING_SOURCE}:{PROJECT}", "--to", destination)
    _cli(environment, "document", "copy", f"{AUTHORING_SOURCE}:{DOCUMENT}", "--to", destination)


def _shown(environment: Mapping[str, str], kind: str, qualified: str) -> dict[str, object]:
    """The record ``qualified`` as the CLI's own `show` reads it back."""
    (entry,) = json.loads(_cli(environment, kind, "show", qualified))["items"]
    item = entry["item"]
    assert isinstance(item, dict)
    return item


def _has_no_details_line(stored: str) -> bool:
    return not any(line.lstrip().startswith("<details") for line in stored.splitlines())


# llmlint: ignore-block[e2e_not_mocked] GitHub's Projects API and Linear's API are the two
# boundaries doubled, because driving either writes to a live tracker; the locked plan-store
# CLI, its copy and its reads are real, and each double stores what the CLI writes.
def test_a_hand_written_block_reaches_github_as_authored_and_linear_as_its_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GitHub keeps the authored block, Linear stores `+++`, and Linear reads back authored."""
    root = tmp_path / "authoring"
    root.mkdir()
    # The example layer an organization's host declares, less the host's own private boards,
    # which this journey holds no credential for and must not reach.
    home = onetaskgraph_user_layer.installed(
        tmp_path / "config", onetaskgraph_user_layer.without_private_stores()
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    environment = _plan_environment(root)
    _authored(tmp_path, environment)

    with _serving_board(linear=True) as remote, linear_board() as linear:
        # A copy onto `plans` builds every source its routes name, so the example's other
        # Linear source is pointed at the same loopback team rather than at a dead port.
        environment = {
            **environment,
            **remote,
            **linear.environment(LINEAR_DESTINATION),
            **linear.environment(onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS),
        }
        _copied(environment, GITHUB_DESTINATION)
        _copied(environment, LINEAR_DESTINATION)

        for title in (TASK_TITLE, PROJECT_TITLE, DOCUMENT_TITLE):
            # A document's issue is titled with the store's own kind prefix before its title.
            (body,) = [issue.body for issue in BOARD.issues if issue.title.endswith(title)]
            assert AUTHORED_BLOCK in body, (
                f"the GitHub board stores {title!r} without the authored block byte for byte:\n"
                f"{body}"
            )

        stored = [
            ("task", "description", linear.issues()),
            ("project", "content", linear.projects()),
            ("document", "content", linear.documents()),
        ]
        for kind, field, records in stored:
            (record,) = records
            text = str(record[field])
            assert LINEAR_BLOCK in text, (
                f"the Linear {kind} stores no `+++` section for the block:\n{text}"
            )
            assert _has_no_details_line(text), f"the Linear {kind} kept a `<details>` line:\n{text}"
            read_back = _shown(environment, kind, f"{LINEAR_DESTINATION}:{record['id']}")
            held = read_back.get("body", read_back.get("content"))
            assert isinstance(held, str), read_back
            assert AUTHORED_BLOCK in held, (
                f"the Linear {kind} reads back through `show` without the authored block:\n{held}"
            )
    # llmlint: ignore-end[e2e_not_mocked]
