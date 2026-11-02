"""A real launch files its plan's human item on the board the host routes it to.

`.onepipeline/config.yaml` names `human-public` as the human-task source, `onetaskgraph.yaml`
declares `human-public` and `human-private`, and a host's user layer supplies the rest — the
routes, `human-private`'s issue repository and each destination's `task_projection`
(`config/onetaskgraph.user-layer.example.yaml`). Each journey here launches, with `just
orchestrate`, a plan holding one `kind: human` step beside parked work naming the repositories
of one placement, and reads what the tracker received:

* a **public** plan's task lands on `human-public`;
* a **private** plan's task lands on `human-private`;
* a **mixed** plan, naming a public and a private repository, is filed privately on
  `human-private`, and `human-public` takes no issue at all;
* an **organization**'s plan follows the layer's combined route into that organization's own
  Linear tracker, and neither board takes an issue.

In every case the filed task closes with the link back to the plan the engine adds — the
plan's address where its source gives one, else its qualified id, which is what a local plan
has — and the engine's item names that tracker task. This is the seam the store journeys in
`tests/plan_tooling/host_layer_routing/test_human_task_boards_e2e.py` cannot reach: the engine
naming a plan-owned task's repositories and project, which before onepipeline 0.98.0 filed
it under the plan's own project with no repositories and was refused.

Everything is real but GitHub's Projects API (`tests/github_board.py`), the organization's
Linear API (`tests/linear_board.py`) and the paid model: the recipes, the installed engine and
plan store, and `onevcs`, answering each repository's visibility from a registry
`tests/boundary_home.py` writes through it.
"""

from __future__ import annotations

import json
import socket
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, NamedTuple

import boundary_home
import github_board
import onetaskgraph_user_layer
import pytest
from conftest import OWNERSHIP_ENV
from github_board import (
    HUMAN_PRIORITY_LABELS,
    HUMAN_TASKS_BOARD,
    PRIVATE_BOARD,
    STATUS_OPTIONS,
    _BoardItemId,
    _HumanTasksFixture,
    _Issue,
    _IssueNodeId,
    _prepare_plan_sources,
    _PrivateBoardFixture,
    _served,
)
from linear_board import LinearBoard, linear_board
from project_fixtures import local_project
from published_tools import ONETASKGRAPH_BIN
from test_board_copy_design_approval_e2e import _recipe
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The two tracked boards, by source name, and the project number each is declared with.
PUBLIC_SOURCE = "human-public"
PRIVATE_SOURCE = "human-private"
NUMBERS = {PUBLIC_SOURCE: 7, PRIVATE_SOURCE: 8}

#: A public repository of the boards' owner: this repository.
OWN_PUBLIC = boundary_home.THIS_REPOSITORY
#: A synthetic private repository of the same owner, registered private.
OWN_PRIVATE = "github.com/nickderobertis/sample-private-notes"
#: An organization's repository, registered private and matched by the layer's combined route.
ORGANIZATION = "github.com/hiddenco/qh-api"

#: The project issue each board files a task under, seeded on each board.
PUBLIC_PROJECT = "I_board_plan"
PRIVATE_PROJECT = "I_private_project"

#: The one Linear credential name `scripts/dispatch-env.sh` hands a launch's driver, which is
#: what projects a human task. The example layer's own name is on no table there, so the layer
#: here names this one instead, as the host whose layer the table was written for does.
DRIVER_LINEAR_KEY_ENV = "HELLOPATIENT_LINEAR_API_KEY"

#: The session the launches act as.
MANAGER = "human-board-projection-manager"

#: The human step every plan holds.
GATE = "approve-the-rollout"
STEP = {
    "kind": "task",
    "priority": "high",
    "title": "Approve the gradual rollout",
    "body": "Approve it once the dashboards look healthy.",
}


class Placement(NamedTuple):
    """One plan's repositories and the source its human task is filed in."""

    repositories: tuple[str, ...]
    source: str


PLACEMENTS = {
    "public": Placement((OWN_PUBLIC,), PUBLIC_SOURCE),
    "private": Placement((OWN_PRIVATE,), PRIVATE_SOURCE),
    "mixed": Placement((OWN_PUBLIC, OWN_PRIVATE), PRIVATE_SOURCE),
    "organization": Placement((ORGANIZATION,), onetaskgraph_user_layer.ORGANIZATION_PLANS),
}


class World(NamedTuple):
    """One journey's environment over both boards and the organization's tracker."""

    environment: dict[str, str]
    tracker: LinearBoard


def _layer(tmp_path: Path) -> dict[str, Any]:
    """The example layer as a host declares it, with this machine as the projection authority,
    intents under ``tmp_path`` and each board's project the issue seeded on it.

    Typed `dict[str, Any]` because it is the parsed YAML document the store, not this journey,
    validates."""
    layer = onetaskgraph_user_layer.example()
    sources = dict(layer["sources"])
    projects = {PUBLIC_SOURCE: PUBLIC_PROJECT, PRIVATE_SOURCE: PRIVATE_PROJECT}
    for name in (PUBLIC_SOURCE, PRIVATE_SOURCE, onetaskgraph_user_layer.ORGANIZATION_PLANS):
        source = dict(sources[name])
        projection = dict(source["task_projection"])
        projection["host"] = socket.gethostname()
        projection["directory"] = str(tmp_path / "intents" / name)
        if name in projects:
            projection["project"] = projects[name]
        else:
            source["config"] = {**source["config"], "api_key_env": DRIVER_LINEAR_KEY_ENV}
        source["task_projection"] = projection
        sources[name] = source
    layer["sources"] = sources
    return layer


def _issue_repository() -> str:
    """The private repository the example layer creates `human-private`'s issues in."""
    return str(onetaskgraph_user_layer.example()["sources"][PRIVATE_SOURCE]["config"]["repository"])


def _seed_private_project() -> None:
    """File the project issue a private task is filed under on the private board."""
    project = _Issue(
        item_id=_BoardItemId("PVTI_private_project"),
        content_id=_IssueNodeId(PRIVATE_PROJECT),
        number=1,
        title="Private human tasks",
        body="The board's own project issue.",
        repository=github_board._Repository.parse(_issue_repository(), "private board"),
    )
    project.board = PRIVATE_BOARD
    PRIVATE_BOARD.issues.append(project)


def _run(environment: Mapping[str, str], command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this checkout's recipes and the pinned CLIs
        command,
        cwd=REPO_ROOT,
        env=dict(environment),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    """Both tracked boards on loopback with their fields set up, the organization's tracker
    with one project its human tasks are filed under, the example layer installed as a host
    declares it, and a runs root of the journey's own."""
    private_repositories = set(github_board.PRIVATE_REPOSITORIES)
    for at, named in enumerate((OWN_PRIVATE.removeprefix("github.com/"), _issue_repository())):
        repository = github_board._Repository.parse(named, "private fixture")
        node = github_board._RepositoryNodeId(f"R_own_private_{at}")
        monkeypatch.setitem(github_board.REPOSITORY_NODE_IDS, repository, node)
        private_repositories.add(repository)
    monkeypatch.setattr(github_board, "PRIVATE_REPOSITORIES", frozenset(private_repositories))
    home = boundary_home.registered_home(
        tmp_path / "registry", public=[OWN_PUBLIC], private=[OWN_PRIVATE, ORGANIZATION]
    )
    config_home = onetaskgraph_user_layer.installed(tmp_path / "config-home", _layer(tmp_path))
    authoring = tmp_path / "authoring"
    authoring.mkdir()
    runs = tmp_path / "runs"
    runs.mkdir()
    environment = boundary_home.environment(None, home)
    environment = {
        name: value
        for name, value in environment.items()
        if name not in ("ONEPIPELINE_CONFIG", "ONETASKGRAPH_HOST", *OWNERSHIP_ENV)
        and not name.startswith("ONETASKGRAPH_SOURCES__HUMAN_")
    }
    environment.update(_prepare_plan_sources(authoring))
    environment[onetaskgraph_user_layer.XDG_CONFIG_HOME] = str(config_home)
    monkeypatch.setenv(onetaskgraph_user_layer.XDG_CONFIG_HOME, str(config_home))
    environment["GH_PROJECTS_TOKEN"] = "fixture-token"
    environment["ONEPIPELINE_RUNS_DIR"] = str(runs)
    environment["ONEPIPELINE_LAUNCHER_SESSION"] = MANAGER
    with (
        linear_board() as tracker,
        _served(
            _HumanTasksFixture, HUMAN_TASKS_BOARD, STATUS_OPTIONS, NUMBERS[PUBLIC_SOURCE]
        ) as public,
        _served(
            _PrivateBoardFixture, PRIVATE_BOARD, STATUS_OPTIONS, NUMBERS[PRIVATE_SOURCE]
        ) as private,
    ):
        _seed_private_project()
        for name, endpoint in ((PUBLIC_SOURCE, public), (PRIVATE_SOURCE, private)):
            prefix = f"ONETASKGRAPH_SOURCES__{name.upper().replace('-', '_')}__CONFIG__"
            environment[f"{prefix}ENDPOINT"] = endpoint
            environment[f"{prefix}PACING__MIN_MUTATION_INTERVAL_MS"] = "0"
        environment.update(tracker.environment(onetaskgraph_user_layer.ORGANIZATION_PLANS))
        for name in (PUBLIC_SOURCE, PRIVATE_SOURCE):
            applied = _run(environment, ["just", "plans", "sources", "fields", name, "--apply"])
            assert applied.returncode == 0, applied.stdout + applied.stderr
        made = _run(
            environment,
            [str(ONETASKGRAPH_BIN), "project", "create", onetaskgraph_user_layer.ORGANIZATION_PLANS]
            + ["--id", "human-tasks", "--title", "Human tasks", "--classification", "private"]
            + ["--output", "json", "--no-interactive"],
        )
        assert made.returncode == 0, made.stdout + made.stderr
        (created,) = json.loads(made.stdout)["items"]
        organization = onetaskgraph_user_layer.ORGANIZATION_PLANS.upper().replace("-", "_")
        environment[f"ONETASKGRAPH_SOURCES__{organization}__TASK_PROJECTION__PROJECT"] = str(
            created["id"]
        ).partition(":")[2]
        yield World(environment, tracker)


def _plan(repositories: tuple[str, ...]) -> dict[str, object]:
    """The human step, and one parked work task a repository, so nothing dispatches."""
    work = [
        {
            "id": f"work-{at}",
            "persona": "engineer",
            "repo": repository,
            "parked": True,
            "task": (
                f"## What\nDo work {at}.\n\n## Why\nSo the rollout ships.\n\n"
                f"## Acceptance criteria\n- work {at} is done."
            ),
        }
        for at, repository in enumerate(repositories)
    ]
    return {
        "schema_version": 3,
        "goal": {"text": "roll the change out gradually"},
        "tasks": [
            {"id": GATE, "kind": "human", "task": STEP["body"], "human_item": STEP},
            *work,
        ],
    }


def _created(board: github_board._Board) -> list[_Issue]:
    return [issue for issue in board.created if issue in board.issues]


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `board-copy-approval` is
# this directory's own leaf project, keyed on `boardCopyApprovalWorkspace`, which names the
# recipes, scripts, configuration and example layer this journey drives.
# llmlint: ignore-block[e2e_not_mocked] GitHub's Projects API and Linear's API are the loopback
# doubles beside this suite, because no check may reach a live board or a Linear workspace, and
# the paid model is never reached because nothing dispatches; the recipes, the pinned engine and
# plan store, `onevcs` and the committed configuration are all real.
@pytest.mark.parametrize("placement", list(PLACEMENTS), ids=list(PLACEMENTS))
def test_a_launch_files_its_plan_s_human_task_where_the_host_routes_it(
    world: World, placement: str
) -> None:
    """The launched plan's human item is projected into the routed source, its body closing
    with the link back to the plan, and no other tracker takes an issue."""
    expected = PLACEMENTS[placement]
    project = local_project(json.dumps(_plan(expected.repositories)), f"human-board-{placement}")
    launched = _recipe(
        world.environment,
        *("orchestrate", project, "--dag-graph", "off"),
        *("--success-hook=", "--failure-hook="),
    )
    listed = _recipe(world.environment, "human-items", "list", "--json")
    assert listed.returncode == 0, listed.stdout + listed.stderr
    items = [view["item"] for view in json.loads(listed.stdout)]
    stopped = [_recipe(world.environment, "stop", str(item["owner"]["run"])) for item in items]

    assert launched.returncode in (0, 3), launched.stdout + launched.stderr
    assert all(one.returncode == 0 for one in stopped), [one.stderr for one in stopped]
    (item,) = items
    assert item["owner"]["plan"] == project and item["block"]["node"] == GATE, item
    tracker = item["tracker"]
    assert isinstance(tracker, dict), (item, launched.stderr)
    link = f"Plan: `{project}`"

    public, private = _created(HUMAN_TASKS_BOARD), _created(PRIVATE_BOARD)
    organization = world.tracker.issues()
    filed: dict[str, list[tuple[str, str]]] = {
        PUBLIC_SOURCE: [(issue.title, issue.body) for issue in public],
        PRIVATE_SOURCE: [(issue.title, issue.body) for issue in private],
        onetaskgraph_user_layer.ORGANIZATION_PLANS: [
            (str(issue["title"]), str(issue["description"])) for issue in organization
        ],
    }
    for source, issues in filed.items():
        if source != expected.source:
            assert issues == [], (placement, source, issues)
    ((title, body),) = filed[expected.source]
    assert title == STEP["title"], (placement, title)
    # The step's own body, then the plan's link, ahead of the store's metadata block.
    shown = body.partition("\n\n<!-- ")[0].rstrip()
    assert shown == f"{STEP['body']}\n\n{link}", (placement, body)
    if expected.source == onetaskgraph_user_layer.ORGANIZATION_PLANS:
        (issue,) = organization
        assert tracker["url"] == issue["url"], (tracker, issue)
    else:
        (issue,) = public or private
        board = HUMAN_TASKS_BOARD if expected.source == PUBLIC_SOURCE else PRIVATE_BOARD
        assert issue.priority == HUMAN_PRIORITY_LABELS[str(STEP["priority"])], issue
        assert issue.repository is not None
        assert str(issue.repository) in tracker["url"], (tracker, issue.repository)
        assert board is (HUMAN_TASKS_BOARD if public else PRIVATE_BOARD)
    # llmlint: ignore-end[e2e_not_mocked]
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
