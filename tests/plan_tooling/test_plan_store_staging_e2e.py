"""A record replaced while the installed store walks its source leaves the walk answering.

A local Markdown source lists every entry of `projects/` and of each `tasks/<project>/`,
and refuses the whole walk for an entry that is gone by the time it opens it: `the source
returned data this interface cannot represent: … (os error 2)`. A staged replacement
renamed away mid-walk is exactly such an entry, so where `orchestrator/plan_store.py`
stages one decides whether a reader in another process is refused.

A listing cannot be interrupted between reading a directory and opening an entry, so the
race is made deterministic from the other side: an entry already gone — a dangling link —
stands where the rename would take the staged file away from.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

import pytest
from published_tools import ONETASKGRAPH_BIN

from orchestrator import project_store
from orchestrator.root import REPO_ROOT

#: A per-test root rather than the shared fixture root, because a vanished entry placed
#: in a walked directory refuses every other process walking the same tree.
SOURCE = "demo"

#: The replacement, run as its own process and held between staging and rename.
HELD_WRITER = Path(__file__).with_name("held_replacement.py")


class Listing(NamedTuple):
    """What the installed store answered to one listing verb over a source."""

    verb: str
    returncode: int
    ids: list[str]
    stderr: str


@contextmanager
def _vanished(entry: Path) -> Iterator[None]:
    """An entry a directory listing names and an open then cannot find."""
    entry.symlink_to(entry.with_name("renamed-away"))
    try:
        yield
    finally:
        entry.unlink()


def _listings(root: Path) -> list[Listing]:
    """The installed store's project and task listings of a local source at ``root``."""
    # The engine's `ONETASKGRAPH_BIN` pointer is an unknown setting to the store itself,
    # and any other `ONETASKGRAPH_` layer would configure sources this read does not name.
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("ONETASKGRAPH_")
    }
    listings: list[Listing] = []
    for verb in (["project", "list"], ["task", "list"]):
        result = subprocess.run(
            [
                str(ONETASKGRAPH_BIN),
                "--default-sources",
                SOURCE,
                "--set",
                f"sources.{SOURCE}.plugin=local-md",
                "--set",
                f"sources.{SOURCE}.config.root={root}",
                *verb,
                "--json",
            ],
            cwd=root,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        answer = json.loads(result.stdout) if result.returncode == 0 else {"items": []}
        listings.append(
            Listing(
                " ".join(verb),
                result.returncode,
                [item["id"] for item in answer["items"]],
                result.stderr,
            )
        )
    return listings


@pytest.mark.parametrize(
    ("project", "record"),
    [
        ("demo", "projects/demo.md"),
        ("demo", "tasks/demo/route.md"),
        # A project named like the other layout's directory, which a layout read in the
        # wrong order would stage inside `tasks/`.
        ("projects", "tasks/projects/route.md"),
    ],
)
def test_a_replacement_is_staged_where_no_walk_of_the_source_lists_it(
    project: str, record: str, tmp_path: Path
) -> None:
    """Read while held: past a vanished entry where it staged, refused for one beside it."""
    assert ONETASKGRAPH_BIN.is_file(), f"no installed onetaskgraph at {ONETASKGRAPH_BIN}"
    root = tmp_path / "store"
    project_store.write_plan_project(
        root,
        {"name": project, "tasks": [{"id": "route", "persona": "engineer", "task": "Add it."}]},
        native_id=project,
    )
    document = root / record
    environment = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    with subprocess.Popen(
        [sys.executable, str(HELD_WRITER), str(document)],
        cwd=REPO_ROOT,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as writer:
        assert writer.stdin is not None and writer.stdout is not None
        announced = writer.stdout.readline().strip()
        if not announced:
            pytest.fail(f"the writer ended before its rename: {writer.communicate()[1]}")
        staged = Path(announced)
        content = staged.read_text(encoding="utf-8")
        mode = staged.stat().st_mode
        with _vanished(staged.with_name(f"tmpvanished{staged.suffix}")):
            during = _listings(root)
        beside_entry = document.parent / f"tmpvanished{staged.suffix}"
        with _vanished(beside_entry):
            beside = _listings(root)
        _, errors = writer.communicate("\n", timeout=60)
    assert writer.returncode == 0, errors

    assert during == [
        Listing("project list", 0, [f"{SOURCE}:{project}"], ""),
        Listing("task list", 0, [f"{SOURCE}:{project}/route"], ""),
    ], f"a walk of the source listed where the replacement is staged, at {staged}"
    assert staged.parent == root, f"the replacement was staged at {staged}, not at {root}"
    assert any(
        listing.returncode != 0
        and str(beside_entry) in listing.stderr
        and "No such file or directory" in listing.stderr
        for listing in beside
    ), f"the store read past a vanished entry beside the record: {beside}"

    assert document.read_text(encoding="utf-8") == content
    assert '"orchestrator.plan-review": {"key": "abc"}' in content
    # `mkstemp` creates the staged file owner-only wherever it stages, and the rename
    # carries that mode onto the record, as it did when staging sat beside the record.
    assert stat.S_IMODE(document.stat().st_mode) == stat.S_IMODE(mode) == 0o600
    assert sorted(path.name for path in root.iterdir()) == [
        project_store.PROJECTS_DIRECTORY,
        project_store.TASKS_DIRECTORY,
    ], "the replacement left a staged file behind at the source root"
