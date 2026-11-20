"""A plan this host copies to Linear keeps each item's metadata in the collapsed block.

Linear renders an HTML comment as a visible paragraph, so the plan store keeps an item's
metadata in one collapsed `+++ onetaskgraph metadata` section at the end of its long form, in
the bytes the adopted onetaskgraph release's `docs/metadata.md` states under *Linear's slot,
settled*. This journey is the check that goes red when the pinned store and this host's Linear
stand-in (`tests/linear_board.py`) part on that shape.

Everything is real but Linear's API: a `local-md` plan of a project, a design document and two
tasks, one depending on the other, authored through the locked plan-store CLI; this host's own
`just copy-plan` copying it to the example user layer's Linear source; and this host's own
`just plans` reading each copied record back.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple

import onetaskgraph_user_layer
import pytest
from github_board import AUTHORING_SOURCE, _plan_environment, _serving_board
from linear_board import LinearBoard, linear_board
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The plan the journey authors, and its design document's id.
PROJECT = "collapsed-metadata"
DOCUMENT = f"{PROJECT}-design"
#: The example user layer's Linear source an organization's plans land on.
LINEAR_DESTINATION = onetaskgraph_user_layer.ORGANIZATION_PLANS
#: The spelling earlier releases kept metadata in, which Linear showed as text.
LEGACY_OPENING = "<!-- onetaskgraph.metadata"

# llmlint: ignore[contracts_have_one_source_or_a_drift_gate] The collapsed block's bytes are the ones onetaskgraph's `docs/metadata.md` ("Linear's slot, settled") states at the release `config/onetaskgraph.version` pins; the installed store writing them is what this journey holds, so a release that moved them fails here.  # noqa: E501 - a directive is one line
#: A long form ending in the collapsed block: the content, one blank line, the summary line,
#: a blank line, the canonical JSON in one code span, a blank line, and `+++` closing it.
COLLAPSED_ENDING = re.compile(
    r"\n\n\+\+\+ onetaskgraph metadata\n\n`(?P<json>\{[^\n]*\})`\n\n\+\+\+\Z"
)


def _cli(environment: Mapping[str, str], *arguments: str) -> str:
    """Run the locked plan-store CLI with ``arguments``, answering its standard output."""
    result = subprocess.run(  # noqa: S603 - the locked plan-store CLI
        [str(ONETASKGRAPH_BIN), "--json", *arguments, "--no-interactive"],
        cwd=REPO_ROOT,
        env=dict(environment),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert result.returncode == 0, f"onetaskgraph {' '.join(arguments)}:\n{result.stderr}"
    return result.stdout


def _recipe(environment: Mapping[str, str], *arguments: str) -> str:
    """One of this checkout's recipes, as an operator runs it, answering its standard output."""
    result = subprocess.run(
        ["just", *arguments],  # noqa: S607 - the checkout's own recipe runner
        cwd=REPO_ROOT,
        env=dict(environment),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )
    assert result.returncode == 0, f"just {' '.join(arguments)}:\n{result.stdout}{result.stderr}"
    return result.stdout


def _authored(root: Path, environment: Mapping[str, str]) -> None:
    """Author the plan in `authoring`: its project, its design document and two tasks."""
    for name, text in (
        ("project.md", "Keep each item's metadata out of the text a person reads.\n"),
        (
            "design.md",
            "## What\n\nThe metadata block collapses.\n\n| Unit | Owner |\n| --- | --- |\n"
            "| store | onetaskgraph |\n",
        ),
        ("first.md", "Write the block.\n"),
        ("second.md", "Read the block back.\n"),
    ):
        (root / name).write_text(text, encoding="utf-8")
    _cli(
        environment,
        *("project", "create", AUTHORING_SOURCE, "--id", PROJECT),
        *("--title", "Collapsed metadata", "--body-file", str(root / "project.md")),
        *("--metadata", 'onepipeline.plan={"approved":true}'),
    )
    _cli(
        environment,
        *("document", "create", AUTHORING_SOURCE, "--project", PROJECT, "--id", DOCUMENT),
        *("--title", "Collapsed metadata design", "--body-file", str(root / "design.md")),
    )
    (first,) = json.loads(
        _cli(
            environment,
            *("task", "create", AUTHORING_SOURCE, "--project", PROJECT),
            *("--title", "feat: write the block", "--body-file", str(root / "first.md")),
            *("--metadata", "onepipeline.turn_budget=12"),
        )
    )["items"]
    _cli(
        environment,
        *("task", "create", AUTHORING_SOURCE, "--project", PROJECT),
        *("--title", "feat: read the block", "--body-file", str(root / "second.md")),
        *("--depends-on", str(first["id"]), "--metadata", 'onepipeline.persona="engineer"'),
    )


def _read_back(environment: Mapping[str, str], kind: str, native: str) -> dict[str, Any]:
    """The record ``native`` of the Linear source as this host's `just plans` reads it."""
    (entry,) = json.loads(
        _recipe(environment, "plans", "--json", kind, "show", f"{LINEAR_DESTINATION}:{native}")
    )["items"]
    item = entry["item"]
    assert isinstance(item, dict), entry
    return item


class Stored(NamedTuple):
    """One copied record the stand-in holds."""

    #: The plan-store kind `just plans` reads it as: `task`, `project` or `document`.
    kind: str
    #: Its long form as Linear stores it: an issue's description, or the content of the rest.
    text: str
    #: The stand-in's observation of it, its `metadata` read out of that long form.
    observed: dict[str, Any]


def _stored(linear: LinearBoard) -> list[Stored]:
    """Every copied record the stand-in holds."""
    return [
        *(Stored("task", str(one["description"]), one) for one in linear.issues()),
        *(Stored("project", str(one["content"]), one) for one in linear.projects()),
        *(Stored("document", str(one["content"]), one) for one in linear.documents()),
    ]


# llmlint: ignore-block[e2e_not_mocked] Linear's API is the one boundary doubled, because
# driving it writes to a live workspace; this host's copy and read recipes, the locked plan
# store they run, and its authoring folder are real, and the stand-in stores what they write.
def test_a_plan_copied_to_linear_keeps_its_metadata_in_the_collapsed_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every long form ends in the block, reads back its metadata, and a re-copy writes nothing."""
    root = tmp_path / "authoring"
    root.mkdir()
    # The example layer an organization's host declares, less the host's own private boards,
    # which this journey holds no credential for and must not reach.
    home = onetaskgraph_user_layer.installed(
        tmp_path / "config", onetaskgraph_user_layer.without_private_stores()
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    environment = _plan_environment(root)
    # Outside a planning flow `just copy-plan` copies and publishes no step output.
    environment.pop("ONEPIPELINE_STEP_OUTPUTS", None)
    _authored(tmp_path, environment)

    with _serving_board(linear=True) as remote, linear_board() as linear:
        # A copy builds every source its routes name, so the example's other Linear source is
        # pointed at the same loopback team rather than at a dead port.
        environment = {
            **environment,
            **remote,
            **linear.environment(LINEAR_DESTINATION),
            **linear.environment(onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS),
        }
        _recipe(environment, "copy-plan", f"{AUTHORING_SOURCE}:{PROJECT}", LINEAR_DESTINATION)

        stored = _stored(linear)
        assert sorted(one.kind for one in stored) == ["document", "project", "task", "task"]
        for kind, text, observed in stored:
            assert LEGACY_OPENING not in text, f"the Linear {kind} holds a comment slot:\n{text}"
            ending = COLLAPSED_ENDING.search(text)
            assert ending, f"the Linear {kind} does not end in the collapsed block:\n{text}"
            assert json.loads(ending["json"]) == observed["metadata"]
            assert observed["metadata"]["onetaskgraph.origin"].startswith(f"{AUTHORING_SOURCE}:")
            read_back = _read_back(environment, kind, str(observed["id"]))
            assert read_back["metadata"] == observed["metadata"], (
                f"`just plans {kind} show` reads the Linear {kind} with other metadata than it "
                f"stores:\n{read_back['metadata']}\n---\n{observed['metadata']}"
            )
            held = read_back.get("body", read_back.get("content"))
            assert isinstance(held, str), read_back
            assert "onetaskgraph metadata" not in held, f"the block reads back as content:\n{held}"

        before = len(linear.requests())
        _recipe(environment, "copy-plan", f"{AUTHORING_SOURCE}:{PROJECT}", LINEAR_DESTINATION)
        written = [one for one in linear.requests()[before:] if one["mutation"]]
        assert written == [], f"an unchanged re-copy wrote to Linear:\n{written}"
        assert _stored(linear) == stored


# llmlint: ignore-end[e2e_not_mocked]
