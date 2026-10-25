"""A dependency read asks only the sources a task's record names, on the adopted plan store.

The store this host adopted reads a task's edges from its own source and, per recorded edge,
from the source that edge names, and asks a private source what it records of the task only
for a copy that does not carry the mark a complete copy leaves (onetaskgraph's
`docs/metadata.md`, under "Which sources a dependency read asks"). The release before that
asked every source declared private on every dependency read, so a private tracker that could
not answer named itself on reads of work that could never depend on it.

This journey drives the locked plan-store CLI beside the interpreter under a configuration
home of its own, whose user layer declares a folder of Markdown not declared private and a
private Linear source pointed at a loopback port nothing listens on, every name synthetic. The
same read through the published release 0.9.0, which asks every private source, names that
source unanswered: the contrast is what says the adopted read never asked it. That release is
named here and is immutable on PyPI, so this file is the whole of what the journey reads beyond
the locked install, and its project keys on both.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import onetaskgraph_user_layer
from published_tools import ONETASKGRAPH_BIN

#: The published plan-store release whose dependency read asks every source declared private.
EVERY_PRIVATE_SOURCE_RELEASE = "0.9.0"
#: The folder of Markdown the task lives in, which declares no visibility.
WORKBENCH = "workbench"
#: The private Linear tracker the layer declares beside it.
TRACKER = "synthetic-tracker"
#: The credential the tracker names, given a value so a request to it is made and refused at
#: the closed port rather than skipped for want of a key.
TRACKER_KEY_ENV = "SYNTHETIC_LINEAR_API_KEY"
#: The task the journey reads, by its native id in the folder.
TASK = "synthetic-task"


def _layer(root: Path) -> dict[str, Any]:
    """The synthetic user layer: the public folder, and the private tracker nobody answers for."""
    return {
        "sources": {
            WORKBENCH: {"plugin": "local-md", "config": {"root": str(root)}},
            TRACKER: {
                "visibility": "private",
                "plugin": "linear",
                "config": {
                    "api_key_env": TRACKER_KEY_ENV,
                    "team": "SYN",
                    "endpoint": onetaskgraph_user_layer.UNSERVED_LINEAR,
                },
            },
        }
    }


def _task(root: Path) -> None:
    """One task of the folder, recording no edge."""
    tasks = root / "tasks"
    tasks.mkdir(parents=True)
    (tasks / f"{TASK}.md").write_text(
        "---\ntitle: Synthetic task\nstatus: todo\nproject: synthetic\n---\nA synthetic task.\n",
        encoding="utf-8",
    )


def _environment(home: Path) -> dict[str, str]:
    """This process's environment with ``home`` as its configuration home.

    Every `ONETASKGRAPH_` name is left out, so the store reads the layer and nothing a launch
    or this suite stated over it.
    """
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("ONETASKGRAPH_")
    }
    environment[onetaskgraph_user_layer.XDG_CONFIG_HOME] = str(home)
    environment[TRACKER_KEY_ENV] = "lin_api_synthetic"
    return environment


def _deps(command: list[str], cwd: Path, environment: dict[str, str]) -> tuple[dict[str, Any], str]:
    """The `--json` answer and standard error of ``command``'s dependency read of the task."""
    read = subprocess.run(  # noqa: S603 - the plan-store CLI under test
        [*command, "--json", "task", "deps", f"{WORKBENCH}:{TASK}"],
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=600,
    )
    assert read.returncode == 0, read.stdout + read.stderr
    return json.loads(read.stdout), read.stderr


def test_a_public_tasks_dependency_read_never_asks_the_private_tracker(tmp_path: Path) -> None:
    """The adopted store answers with no unanswered source; the release before named one."""
    root = tmp_path / WORKBENCH
    _task(root)
    home = onetaskgraph_user_layer.installed(tmp_path / "config-home", _layer(root))
    environment = _environment(home)
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    adopted, adopted_notices = _deps([str(ONETASKGRAPH_BIN)], cwd, environment)

    assert adopted["errors"] == [], adopted
    assert adopted["items"] == [], adopted
    assert adopted.get("unanswered", []) == [], adopted
    assert TRACKER not in adopted_notices, adopted_notices

    before, before_notices = _deps(
        ["uvx", "--from", f"onetaskgraph-cli=={EVERY_PRIVATE_SOURCE_RELEASE}", "onetaskgraph"],
        cwd,
        environment,
    )

    assert [entry["source"] for entry in before["unanswered"]] == [TRACKER], before
    assert before["unanswered"][0]["error"]["kind"] == "unavailable", before
    assert f"private source {TRACKER} was not asked" in before_notices, before_notices
