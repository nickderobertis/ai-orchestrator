"""Every host of this repository reads the same human-item defaults from the tracked file alone.

`.onepipeline/config.yaml` turns human items on and names no `task_source` yet, and
`onetaskgraph.yaml` declares the boards that source will name, `human-public` and
`human-private`. So a host that has written no host-local `onepipeline/config.yaml` and set no
`ONEPIPELINE_CONFIG` has human items on. These journeys hold the three readers of that one
file to it, each from two independent configuration homes that carry no enablement:

* **the host CLI**, `just human-items config --json`: enabled, projecting into no tracker,
  read from the tracked file and nothing else, and the plan store resolving
  both tracked boards in either home; a host's own `enabled: false` turns it off for that host
  alone, which is what says the answer is the file's rather than the engine's default;
* **the embedded read API**, `just dag-ui`, which opens the engine's store through the SDK
  under the same loader: its action-items route answers rather than `503
  human_items_unavailable`, and a host's own `enabled: false` makes it refuse;
* **a launch**, `just orchestrate` of a plan holding one `kind: human` step. This proves the
  launch reads the shared enablement and records a durable item: the step's item is read back
  from the engine's store by a separate `just human-items list` after the launch, which a launch
  with human items off never leaves behind. It proves nothing about projection. No `task_source`
  is named, so the launch attempts none, and the journey asserts only that it did not.

Everything is real but GitHub's Projects API, the loopback double `tests/github_board.py`
serves, and the paid model `reviewed` scripts: the recipes, the installed engine, read API and
plan store, and the engine's item store under a runs root of each journey's own.
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, NamedTuple

import onetaskgraph_user_layer
import pytest
from conftest import OWNERSHIP_ENV
from github_board import _serving_board
from project_fixtures import local_project, reviewed
from served_recipe import serve, stop
from test_board_copy_design_approval_e2e import _copied, _on_the_board, _recipe
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The tracked file every host reads the defaults from.
TRACKED = REPO_ROOT / ".onepipeline" / "config.yaml"


class Board(NamedTuple):
    """A human-task board's identity as the store resolves it: its visibility and number."""

    visibility: Literal["public", "private"]
    project_number: int


#: The tracked human-task boards, declared and not yet named as the defaults' `task_source`.
BOARDS = {"human-public": Board("public", 7), "human-private": Board("private", 8)}

#: The session the launch journey acts as.
MANAGER = "shared-defaults-manager"


def _home(tmp_path: Path, name: str, *, disabled: bool = False) -> dict[str, str]:
    """An environment over a configuration home of its own, holding no human-item enablement.

    No `ONEPIPELINE_CONFIG`, and the home's `onepipeline/config.yaml` absent — unless
    ``disabled``, when it is the one host-local file a host writes to turn human items off.
    """
    home = onetaskgraph_user_layer.installed(tmp_path / name / "config", {"sources": {}})
    if disabled:
        (home / "onepipeline").mkdir()
        (home / "onepipeline" / "config.yaml").write_text(
            "human_items:\n  enabled: false\n", encoding="utf-8"
        )
    runs = tmp_path / name / "runs"
    runs.mkdir(parents=True)
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("ONEPIPELINE_CONFIG", *OWNERSHIP_ENV)
        and not key.startswith("ONETASKGRAPH_SOURCES__HUMAN_")
    }
    environment[onetaskgraph_user_layer.XDG_CONFIG_HOME] = str(home)
    environment["ONEPIPELINE_RUNS_DIR"] = str(runs)
    return environment


def _config(environment: Mapping[str, str]) -> dict[str, object]:
    shown = _recipe(environment, "human-items", "config", "--json")
    assert shown.returncode == 0, shown.stdout + shown.stderr
    answer: dict[str, object] = json.loads(shown.stdout)
    return answer


def _resolved_boards(environment: Mapping[str, str]) -> dict[str, Board]:
    """Each tracked human-task board's visibility and project number, as the store resolves."""
    shown = _recipe(environment, "plans", "--output", "json", "config", "show")
    assert shown.returncode == 0, shown.stdout + shown.stderr
    settings = {str(one["key"]): one["value"] for one in json.loads(shown.stdout)["settings"]}
    resolved: dict[str, Board] = {}
    for name in BOARDS:
        visibility = settings.get(f"sources.{name}.visibility")
        number = settings.get(f"sources.{name}.config.project_number")
        assert visibility in ("public", "private"), (name, visibility)
        assert isinstance(number, int) and not isinstance(number, bool), (name, number)
        resolved[name] = Board("public" if visibility == "public" else "private", number)
    return resolved


@pytest.mark.parametrize("name", ["first-host", "second-host"])
def test_the_host_cli_reads_the_tracked_defaults_in_every_configuration_home(
    tmp_path: Path, name: str
) -> None:
    """Enabled and projecting into no tracker, from the tracked file alone, in each home;
    off for one host only where that host's own file says so."""
    environment = _home(tmp_path, name)
    effective = _config(environment)
    human = effective["human_items"]
    assert isinstance(human, dict)
    assert human["enabled"] is True and human["task_source"] is None, human
    assert effective["sources"] == [str(TRACKED)], effective
    assert _resolved_boards(environment) == BOARDS

    off = _config(_home(tmp_path, f"{name}-off", disabled=True))
    off_items = off["human_items"]
    assert isinstance(off_items, dict) and off_items["enabled"] is False, off
    read = off["sources"]
    assert isinstance(read, list) and len(read) == 2 and read[0] == str(TRACKED), off
    # The other home is untouched by that host's choice.
    assert _config(environment)["human_items"] == human


def _human_items_route(environment: dict[str, str], tmp_path: Path) -> tuple[int, object]:
    """GET the read API's action-items route from `just dag-ui`, as the browser does."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    serving = dict(environment)
    serving["CLAUDE_CODE_SESSION_ID"] = MANAGER
    serving["ONEAGENTGRAPH_STATE_DIR"] = str(tmp_path / "graph-records")
    recipe = serve(["just", "dag-ui", "--bind", f"127.0.0.1:{port}"], serving)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + e2e_timeout(60)
        while True:
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=2):
                    break
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                assert time.monotonic() < deadline and recipe.poll() is None, "never answered"
                time.sleep(0.1)
        try:
            with urllib.request.urlopen(f"{base}/api/human-items", timeout=e2e_timeout(60)) as got:
                return got.status, json.loads(got.read())
        except urllib.error.HTTPError as refused:
            return refused.code, json.loads(refused.read() or b"{}")
    finally:
        stop(recipe)


@pytest.mark.parametrize("name", ["first-host", "second-host"])
def test_the_embedded_read_api_serves_action_items_from_the_tracked_defaults(
    tmp_path: Path, name: str
) -> None:
    """`just dag-ui` answers its action-items route in a home with no enablement, and refuses it
    as unavailable only in a home whose own file turns human items off."""
    status, answer = _human_items_route(_home(tmp_path, name), tmp_path / name)
    assert status == 200, answer
    assert answer == [] or isinstance(answer, (list, dict)), answer

    off_status, off_answer = _human_items_route(
        _home(tmp_path, f"{name}-off", disabled=True), tmp_path / f"{name}-off"
    )
    assert off_status == 503, off_answer
    assert "human_items_unavailable" in json.dumps(off_answer), off_answer


#: The human step the launch journey's one node is.
GATE = "approve-the-rollout"
STEP = {
    "kind": "task",
    "priority": "medium",
    "title": "Approve the gradual rollout",
    "body": "Approve it once the dashboards look healthy.",
}


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `board-copy-approval` is
# this directory's own leaf project, keyed on `boardCopyApprovalWorkspace`, the edge this rule
# asks for; that key names the recipes, scripts, configuration and package this journey drives.
# llmlint: ignore-block[e2e_not_mocked] GitHub's Projects API is the one boundary doubled,
# because driving it writes to the live board this repository plans on, and the paid model
# `reviewed` scripts is the other; the recipes, the pinned engine and plan store, and the
# engine's item store are all real, and the plan's one node is a human step nothing dispatches.
def test_a_launch_raises_its_human_step_with_no_host_local_enablement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A launch in a home that enables nothing reads the shared enablement and records a durable
    item for its human step. It proves no projection: none is attempted, and no board task
    exists to read."""
    plan = {
        "schema_version": 3,
        "goal": {"text": "roll the change out gradually"},
        "tasks": [{"id": GATE, "kind": "human", "task": STEP["body"], "human_item": STEP}],
    }
    project = reviewed(local_project(json.dumps(plan), "shared-defaults-human"))
    environment = _home(tmp_path, "launching-host")
    environment["ONEPIPELINE_LAUNCHER_SESSION"] = MANAGER
    assert _config(environment)["sources"] == [str(TRACKED)]
    with _serving_board() as remote:
        on_the_board = {**environment, **remote}
        copied = _recipe(on_the_board, "copy-plan", project, "--to", "plans")
        assert copied.returncode == 0, copied.stdout + copied.stderr
        home = _copied(copied.stdout)[project]
        _on_the_board(monkeypatch, remote)
        approved = _recipe(on_the_board, "approve-design", home)
        assert approved.returncode == 0, approved.stdout + approved.stderr
        launched = _recipe(
            on_the_board,
            *("orchestrate", home, "--dag-graph", "off"),
            *("--success-hook=", "--failure-hook="),
        )
        listed = _recipe(on_the_board, "human-items", "list", "--json")
        assert listed.returncode == 0, listed.stdout + listed.stderr
        items = [view["item"] for view in json.loads(listed.stdout)]
        stopped = [_recipe(on_the_board, "stop", str(item["owner"]["run"])) for item in items]

    assert launched.returncode in (0, 3), launched.stdout + launched.stderr
    (item,) = items
    assert {key: item[key] for key in STEP} == STEP, item
    assert item["owner"]["session"] == MANAGER and item["owner"]["plan"] == home, item
    assert item["block"]["scope"] == "work" and item["block"]["node"] == GATE, item
    assert all(one.returncode == 0 for one in stopped), [one.stderr for one in stopped]
    # No `task_source` is named, so the launch attempts no projection into any tracker.
    assert "projection into" not in launched.stderr, launched.stderr
    assert item["tracker"] is None, item
    # llmlint: ignore-end[e2e_not_mocked]
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
