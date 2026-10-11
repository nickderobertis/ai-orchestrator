"""A copy of this checkout whose tracked Linear source is a folder of Markdown instead.

No check may reach the production Linear workspace, and the store's environment layer merges
into a source rather than replacing it, so a `local-md` plugin set over the tracked Linear
source would arrive beside its Linear keys and be refused for them. A journey that drives a
Linear source therefore runs in a copy of this checkout whose configuration declares that
source as a `local-md` folder, whose status mapping reads the state names the tracked Linear
mapping writes — read off the tracked file through the store's own resolution, so a mapping
that moved moves the stand-in with it. `tests/test_plan_source_roots.py` holds each tracked
Linear source to the Linear plugin's own schema.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Collection
from pathlib import Path

from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT


def tracked_states(source: str) -> dict[str, str]:
    """The workflow state each category of ``source`` is written as, out of the tracked file."""
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("ONETASKGRAPH_")
    }
    shown = subprocess.run(  # noqa: S603 - the installed plan-store CLI
        [str(ONETASKGRAPH_BIN), "--json", "config", "show"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert shown.returncode == 0, shown.stderr
    prefix = f"sources.{source}.config.status_mapping."
    states = {
        str(setting["key"])[len(prefix) :]: str(setting["value"])
        for setting in json.loads(shown.stdout)["settings"]
        if str(setting["key"]).startswith(prefix)
    }
    assert states, f"the tracked configuration maps no state for {source}"
    return states


def stand_in(checkout: Path, source: str, root: Path, copied: Collection[str] = ()) -> None:
    """Declare the copy's ``source`` as a folder at ``root`` reading the tracked state names.

    The block is located by the shape the tracked file has, so a configuration that moved
    fails here before anything could reach the real workspace.

    ``copied`` names each status a journey copies onto the stand-in carrying that category's
    own word as its name. Linear writes a copy's status by category, through its mapping; a
    folder takes the name it is handed and reads it back through a mapping of names, without
    case. So where a category's word is also the name of a tracked state of *another*
    category — `backlog`, the proposal a follow-up ticket is filed as, against Linear's own
    `Backlog`, the state a person defers to — that state is left out and the word maps to its
    own category, which is the reading Linear gives the copy.
    """
    configuration = checkout / "onetaskgraph.yaml"
    lines = configuration.read_text(encoding="utf-8").splitlines()
    opens = f"  {source}:"
    assert opens in lines, f"{configuration} declares no {source!r} source opening {opens!r}"
    start = lines.index(opens)
    end = start + 1
    while end < len(lines) and (lines[end].startswith("    ") or not lines[end].strip()):
        end += 1
    words = {word.casefold() for word in copied}
    mapping = [
        f"        {json.dumps(state)}: {category}"
        for key, state in tracked_states(source).items()
        for category in [key.split(".")[0]]
        if state.casefold() not in words
    ] + [f"        {json.dumps(word)}: {word}" for word in copied]
    # llmlint: ignore-block[e2e_not_mocked] A local `local-md` source stands in for a tracked
    # Linear source in every journey that drives one, because no check may reach the
    # production Linear workspace; the tracked Linear configuration is held to the plugin's own
    # schema by `tests/test_plan_source_roots.py` instead.
    lines[start:end] = [
        opens,
        "    plugin: local-md",
        "    config:",
        f"      root: {root}",
        "      status_mapping:",
        *mapping,
    ]
    # llmlint: ignore-end[e2e_not_mocked]
    configuration.write_text("\n".join(lines) + "\n", encoding="utf-8")


def provisioned(checkout: Path) -> None:
    """Give the copied checkout the locked toolchain a session start would give it."""
    environment = dict(os.environ)
    for named in ("UV_NO_SYNC", "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV"):
        environment.pop(named, None)
    synced = subprocess.run(
        ["uv", "sync", "--locked"],  # noqa: S607 - `uv` from the search path, as session setup runs it
        cwd=checkout,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )
    assert synced.returncode == 0, f"the copied checkout could not be provisioned:\n{synced.stderr}"
