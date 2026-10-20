"""A host layer refining the two tracked boards with routes alone, read outside this checkout.

A host's user layer (`config/onetaskgraph.user-layer.example.yaml`, installed here as it is)
gives `plans` and `followups` nothing but their `routes`: everything else about those two
sources is this repository's `onetaskgraph.yaml`. Every plan-store call reads that layer, so
a call made from a directory no project document covers reads the two entries with no
plugin. The store before onetaskgraph 0.7.3 refused every such call (`sources.followups:
missing field plugin`); the adopted one leaves a source no layer defines unconfigured. These
journeys drive the installed CLI as a host's shell would: outside the checkout the call
succeeds and only naming one of those sources is refused, and inside it the layer changes
no setting this checkout resolves without it.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import onetaskgraph_user_layer
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

#: The two sources the tracked document defines and the layer only refines.
BOARDS = ("plans", "followups")


def _environment(home: Path) -> dict[str, str]:
    """This process's environment with ``home`` as its configuration home and no
    `ONETASKGRAPH_` setting, so the documents alone decide what is read."""
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("ONETASKGRAPH_")
    }
    environment[onetaskgraph_user_layer.XDG_CONFIG_HOME] = str(home)
    environment.pop("HIDDENCO_LINEAR_API_KEY", None)
    return environment


def _store(home: Path, cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - the installed plan-store CLI
        [str(ONETASKGRAPH_BIN), *arguments],
        cwd=cwd,
        env=_environment(home),
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=e2e_timeout(60),
        check=False,
    )


def test_the_example_layer_refines_each_tracked_board_with_its_routes_alone() -> None:
    """The shape the other journeys rely on: no other key under either board, synthetic names."""
    sources = onetaskgraph_user_layer.example()["sources"]

    for board in BOARDS:
        assert set(sources[board]) == {"routes"}, sources[board]
        for route in sources[board]["routes"]:
            assert route["to"].startswith("hiddenco-"), route
            assert all(name.startswith("github.com/hiddenco/") for name in route["repositories"])


def test_a_call_outside_this_checkout_succeeds_and_names_the_unconfigured_board(
    tmp_path: Path,
) -> None:
    """Outside the checkout the layer is read alone: the call succeeds, the refined boards are
    listed nowhere, and only a call naming one of them is refused, by name."""
    home = onetaskgraph_user_layer.installed(tmp_path / "config-home")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    shown = _store(home, elsewhere, "--json", "config", "show")
    assert shown.returncode == 0, shown.stderr
    keys = {str(entry["key"]) for entry in json.loads(shown.stdout)["settings"]}
    for board in BOARDS:
        assert {key for key in keys if key.startswith(f"sources.{board}.")} == {
            f"sources.{board}.routes"
        }, sorted(keys)

    listed = _store(home, elsewhere, "--json", "sources", "list")
    assert listed.returncode == 0, listed.stderr
    available = {str(source["source"]) for source in json.loads(listed.stdout)}
    assert onetaskgraph_user_layer.ORGANIZATION_PLANS in available, available
    assert available.isdisjoint(BOARDS), available

    named = _store(home, elsewhere, "task", "list", "--source", "plans", "--no-interactive")
    assert named.returncode != 0, named.stdout
    assert 'source "plans" is not configured here' in named.stderr, named.stderr


def test_inside_this_checkout_the_layer_changes_no_setting_resolved_without_it(
    tmp_path: Path,
) -> None:
    """Every setting this checkout resolves with no layer resolves the same beneath it; what the
    layer adds is its own sources and the two boards' routes, and nothing else."""
    layer = onetaskgraph_user_layer.example()
    beneath = onetaskgraph_user_layer.resolved(
        onetaskgraph_user_layer.installed(tmp_path / "config-home", layer)
    )
    alone = onetaskgraph_user_layer.resolved(
        onetaskgraph_user_layer.installed(tmp_path / "empty-config-home", {})
    )

    assert {key: beneath.get(key) for key in alone} == alone
    added = set(beneath) - set(alone)
    owned = tuple(f"sources.{name}." for name in layer["sources"] if name not in BOARDS) + tuple(
        f"sources.{board}.routes" for board in BOARDS
    )
    assert added, "the layer added nothing, so it was never read"
    assert {key for key in added if not key.startswith(owned)} == set(), sorted(added)
    assert {f"sources.{board}.routes" for board in BOARDS} <= added, sorted(added)
