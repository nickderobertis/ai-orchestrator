"""The operator's two human-task boards, as the tracked document declares them, driven end to end.

`onetaskgraph.yaml` declares `human-public` and `human-private` — the owner, project number,
visibility, credential and the `Status` and `Priority` options of each — and a host's user layer
supplies the rest: `human-public`'s whole `routes` list, `human-private`'s private issue
repository, and each destination's `task_projection`
(`config/onetaskgraph.user-layer.example.yaml`). These journeys run the installed plan store
over exactly that: the committed document, the example layer installed beneath it, and only the
two boards' endpoints and the projection directories moved, so every other value is the
tracked one:

* **the fields**: `just plans sources fields <board> --apply`, the one verb a board's fields
  are set up through, adds the `Status` options each board lacks and creates its `Priority`
  field with the four options the tracked mapping names — Critical, High, Medium, Low — over a
  board that carried neither, without moving any item's status; the verb's dry run then
  reports nothing missing;
* **the selection**, through `onetaskgraph task project-human`, the store verb the engine's
  projection links: a task naming a public repository of the owner lands on `human-public`; one
  naming a private repository lands on `human-private`; one naming a public and a private
  repository at once is private, and lands there whole; an organization's private task lands in
  its own tracker by the layer's combined route, filed under the plan project it names there;
* **the boundary**: with no private route — a host whose layer declares none — a private task
  is refused and nothing is written on either board.

These are store journeys. Each request is hand-built, so none of them reaches the seam between
the engine and the store, where the engine names a plan-owned task's project and repositories.
They do not show that an engine launch puts a task on either board.

GitHub's Projects API is the loopback board `tests/github_board.py` serves — one server per
board, each under the project number the tracked document names — and the organization's
Linear tracker is the loopback Linear wire `tests/linear_board.py` serves, its source kept as
the example layer declares it, because no check may reach a live board or a Linear workspace.
Repository visibility is `onevcs`'s answer, from a registry `tests/boundary_home.py` writes
through it.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, NamedTuple

import boundary_home
import github_board
import onetaskgraph_user_layer
import pytest
from github_board import (
    HUMAN_PRIORITY_LABELS,
    HUMAN_TASKS_BOARD,
    PRIVATE_BOARD,
    STATUS_OPTIONS,
    _BoardItemId,
    _HumanTasksFixture,
    _Issue,
    _IssueNodeId,
    _plan_environment,
    _PrivateBoardFixture,
    _served,
)
from linear_board import LinearBoard, linear_board
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The two tracked boards, by source name, and the project number each is declared with.
PUBLIC_BOARD = "human-public"
PRIVATE_BOARD_SOURCE = "human-private"
NUMBERS = {PUBLIC_BOARD: 7, PRIVATE_BOARD_SOURCE: 8}

#: A public repository of the boards' owner: this repository.
OWN_PUBLIC = boundary_home.THIS_REPOSITORY
#: A synthetic private repository of the same owner, registered private.
OWN_PRIVATE = "github.com/nickderobertis/sample-private-notes"
#: An organization's repository, registered private and matched by the layer's combined route.
ORGANIZATION = "github.com/hiddenco/qh-api"

#: The host the example layer names as authoritative for every projection.
EXAMPLE_HOST = "workstation-1"
#: The project issue each board files a planless task under: the example layer names a
#: synthetic one, which the journey moves to the board's own.
PROJECT_ISSUE = "I_board_plan"
#: The plan project an organization's task is filed under in its tracker.
ORGANIZATION_PROJECT = "rollout"

#: The `Status` options a projected task reaches on either board: created at Todo, settled at
#: Done or Cancelled, and moved to In Progress by a person.
HUMAN_STATUSES = ("Todo", "In Progress", "Done", "Cancelled")

#: Each board as it stands before an operator sets it up: GitHub's new-board options, without
#: the `In Progress` and `Cancelled` the tracked mapping names, and no `Priority` field.
UNSET_STATUS_OPTIONS = tuple(
    option for option in STATUS_OPTIONS if option.name not in ("In Progress", "Cancelled")
)


class Boards(NamedTuple):
    """One journey's environment over both boards, and the organization's loopback tracker."""

    environment: dict[str, str]
    tracker: LinearBoard


def _layer(tmp_path: Path, *, routes: bool = True) -> dict[str, Any]:
    """The example layer with its intents under ``tmp_path``, each board's planless project its
    own project issue; ``routes`` false leaves `human-public` with no route at all.

    Typed `dict[str, Any]` because it is the parsed YAML document
    `onetaskgraph_user_layer.example()` hands back and `installed()` writes: its values are
    whatever shape the store's user-layer schema gives each source, which the store and not
    this journey validates, so a narrower type here would be a second schema to drift."""
    layer = onetaskgraph_user_layer.example()
    sources = dict(layer["sources"])
    for name in (PUBLIC_BOARD, PRIVATE_BOARD_SOURCE, onetaskgraph_user_layer.ORGANIZATION_PLANS):
        source = dict(sources[name])
        projection = dict(source["task_projection"])
        projection["directory"] = str(tmp_path / "intents" / name)
        if "project" in projection:
            projection["project"] = PROJECT_ISSUE
        source["task_projection"] = projection
        sources[name] = source
    if not routes:
        public = dict(sources[PUBLIC_BOARD])
        public.pop("routes")
        sources[PUBLIC_BOARD] = public
    layer["sources"] = sources
    return layer


def _issue_repository() -> str:
    """The private repository the example layer creates `human-private`'s issues in."""
    return str(
        onetaskgraph_user_layer.example()["sources"][PRIVATE_BOARD_SOURCE]["config"]["repository"]
    )


def _seed_private_project() -> None:
    """File the project issue a planless private task is filed under on the private board, in
    its private issue repository: a private board otherwise holds nothing."""
    project = _Issue(
        item_id=_BoardItemId("PVTI_private_project"),
        content_id=_IssueNodeId(PROJECT_ISSUE),
        number=1,
        title="Private human tasks",
        body="The board's own project issue.",
        repository=github_board._Repository.parse(_issue_repository(), "private board"),
    )
    project.board = PRIVATE_BOARD
    PRIVATE_BOARD.issues.append(project)


@pytest.fixture
def boards(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Boards]:
    """Both tracked boards served on loopback under the example layer, a registry answering
    each repository's visibility, and every board without a `Priority` field or two of its
    tracked `Status` options yet."""
    routes = getattr(request, "param", True)
    # GitHub knows the owner's private repository and the private board's issue repository the
    # example layer names, and answers both private, as it would.
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
    config_home = onetaskgraph_user_layer.installed(
        tmp_path / "config-home", _layer(tmp_path, routes=routes)
    )
    authoring = tmp_path / "authoring"
    authoring.mkdir()
    environment = boundary_home.environment(_plan_environment(authoring), home)
    environment = {
        name: value
        for name, value in environment.items()
        if not name.startswith(("ONETASKGRAPH_SOURCES__HUMAN_", "ONETASKGRAPH_HOST"))
    }
    environment[onetaskgraph_user_layer.XDG_CONFIG_HOME] = str(config_home)
    # The tracker's environment is read off this process's resolution of the same layer.
    monkeypatch.setenv(onetaskgraph_user_layer.XDG_CONFIG_HOME, str(config_home))
    environment["ONETASKGRAPH_HOST"] = EXAMPLE_HOST
    environment["GH_PROJECTS_TOKEN"] = "fixture-token"
    with (
        linear_board() as tracker,
        _served(
            _HumanTasksFixture, HUMAN_TASKS_BOARD, UNSET_STATUS_OPTIONS, NUMBERS[PUBLIC_BOARD]
        ) as public,
        _served(
            _PrivateBoardFixture,
            PRIVATE_BOARD,
            UNSET_STATUS_OPTIONS,
            NUMBERS[PRIVATE_BOARD_SOURCE],
        ) as private,
    ):
        _seed_private_project()
        for name, endpoint in ((PUBLIC_BOARD, public), (PRIVATE_BOARD_SOURCE, private)):
            prefix = f"ONETASKGRAPH_SOURCES__{name.upper().replace('-', '_')}__CONFIG__"
            environment[f"{prefix}ENDPOINT"] = endpoint
            environment[f"{prefix}PACING__MIN_MUTATION_INTERVAL_MS"] = "0"
        environment.update(tracker.environment(onetaskgraph_user_layer.ORGANIZATION_PLANS))
        yield Boards(environment, tracker)


def _run(
    environment: Mapping[str, str], command: list[str], stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this checkout's recipes and the pinned CLIs
        command,
        cwd=REPO_ROOT,
        env=dict(environment),
        input=stdin,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )


def _fields(environment: Mapping[str, str], source: str) -> dict[str, list[str]]:
    """Each single-select field the store reads on ``source``'s board, by its options' names."""
    planned = _run(environment, [str(ONETASKGRAPH_BIN), "sources", "fields", source, "--json"])
    assert planned.returncode == 0, planned.stdout + planned.stderr
    return {
        str(field["field"]): [str(option["name"]) for option in field["existing"]]
        for field in json.loads(planned.stdout)["fields"]
    }


def _missing(environment: Mapping[str, str], source: str) -> list[str]:
    """Every option the verb's dry run plans to add on ``source``'s board, field by field."""
    planned = _run(environment, [str(ONETASKGRAPH_BIN), "sources", "fields", source, "--json"])
    assert planned.returncode == 0, planned.stdout + planned.stderr
    return [
        f"{field['field']}: {name}"
        for field in json.loads(planned.stdout)["fields"]
        for name in field["missing"]
    ]


def _projected(
    environment: Mapping[str, str],
    item: str,
    repositories: list[str],
    *,
    project: str | None = None,
    priority: str = "high",
) -> subprocess.CompletedProcess[str]:
    """Project one open human task, as the engine's projection hands it to the store."""
    request = {
        "schema_version": 1,
        "item_id": item,
        "owner": {"session": "human-boards-manager", "run": None, "plan": None},
        "title": f"Decide {item}",
        "body": "Answer what this step waits on.",
        "priority": priority,
        "repositories": repositories,
        "project": project,
        "source": PUBLIC_BOARD,
        "state": "open",
    }
    return _run(
        environment,
        [str(ONETASKGRAPH_BIN), "task", "project-human", "--input", "-", "--output", "json"],
        stdin=json.dumps(request),
    )


def _fields_applied(environment: Mapping[str, str]) -> None:
    """Set both boards' fields up, as an operator does before human items project into them."""
    for source in (PUBLIC_BOARD, PRIVATE_BOARD_SOURCE):
        applied = _run(environment, ["just", "plans", "sources", "fields", source, "--apply"])
        assert applied.returncode == 0, applied.stdout + applied.stderr


def _titles(board: github_board._Board) -> list[str]:
    return [issue.title for issue in board.created]


# llmlint: ignore-block[e2e_not_mocked] GitHub's Projects API is loopback
# (`tests/github_board.py`) and so is the organization's Linear tracker
# (`tests/linear_board.py`), because no check may reach a live board or Linear workspace; the
# store, `onevcs`, the recipe and the committed configuration are real.
def test_sources_fields_sets_up_each_board_s_status_options_and_priority_field(
    boards: Boards,
) -> None:
    """Each board starts without `In Progress`, `Cancelled` or a `Priority` field and ends with
    every tracked `Status` option and Critical, High, Medium, Low, every item's status where it
    was; the verb's dry run then reports nothing missing.

    The double refuses an `updateProjectV2Field` input GitHub's schema refuses — the
    `projectId` the store sent before 0.13.1 among them — and one that would move an item's
    status, so a green apply is one the live boards would take.
    """
    for board in (HUMAN_TASKS_BOARD, PRIVATE_BOARD):
        board.issues[0].status = "Done"
    held = {
        source: {issue.title: issue.status for issue in board.issues}
        for source, board in (
            (PUBLIC_BOARD, HUMAN_TASKS_BOARD),
            (PRIVATE_BOARD_SOURCE, PRIVATE_BOARD),
        )
    }
    for source in (PUBLIC_BOARD, PRIVATE_BOARD_SOURCE):
        before = _fields(boards.environment, source)
        assert before.get("Priority", []) == [], (source, before)
        assert {"In Progress", "Cancelled"}.isdisjoint(before["Status"]), (source, before)
        assert _missing(boards.environment, source), source
        applied = _run(
            boards.environment, ["just", "plans", "sources", "fields", source, "--apply"]
        )
        assert applied.returncode == 0, applied.stdout + applied.stderr
        after = _fields(boards.environment, source)
        assert after["Priority"] == list(HUMAN_PRIORITY_LABELS.values()), (source, after)
        assert after["Priority"] == ["Critical", "High", "Medium", "Low"], after
        assert set(HUMAN_STATUSES) <= set(after["Status"]), (source, after)
        # The options the board already carried keep their places; the added ones follow.
        assert after["Status"][: len(before["Status"])] == before["Status"], (source, after)
        dry_run = _run(boards.environment, ["just", "plans", "sources", "fields", source])
        assert dry_run.returncode == 0, dry_run.stdout + dry_run.stderr
        assert _missing(boards.environment, source) == [], source
    for source, board in ((PUBLIC_BOARD, HUMAN_TASKS_BOARD), (PRIVATE_BOARD_SOURCE, PRIVATE_BOARD)):
        assert [option.name for option in board.priorities] == ["Critical", "High", "Medium", "Low"]
        assert {issue.title: issue.status for issue in board.issues} == held[source], source


def test_public_and_private_own_account_tasks_land_on_their_own_boards(boards: Boards) -> None:
    """Public work lands on `human-public`; private and mixed work lands on `human-private`."""
    _fields_applied(boards.environment)

    public = _projected(boards.environment, "public-step", [OWN_PUBLIC], priority="urgent")
    assert public.returncode == 0, public.stdout + public.stderr
    assert json.loads(public.stdout)["source"] == PUBLIC_BOARD, public.stdout
    private = _projected(boards.environment, "private-step", [OWN_PRIVATE])
    assert private.returncode == 0, private.stdout + private.stderr
    assert json.loads(private.stdout)["source"] == PRIVATE_BOARD_SOURCE, private.stdout
    mixed = _projected(boards.environment, "mixed-step", [OWN_PUBLIC, OWN_PRIVATE])
    assert mixed.returncode == 0, mixed.stdout + mixed.stderr
    assert json.loads(mixed.stdout)["source"] == PRIVATE_BOARD_SOURCE, mixed.stdout

    assert _titles(HUMAN_TASKS_BOARD) == ["Decide public-step"]
    assert _titles(PRIVATE_BOARD) == ["Decide private-step", "Decide mixed-step"]
    # The wire's `urgent` is the operator's Critical on the board.
    (created,) = HUMAN_TASKS_BOARD.created
    assert created.priority == "Critical", created


def test_an_organization_s_task_lands_in_its_tracker_under_the_plan_project(
    boards: Boards,
) -> None:
    """The layer's combined route sends an organization's private task to its own tracker,
    filed under the plan project the request names there, and neither board sees it."""
    _fields_applied(boards.environment)
    made = _run(
        boards.environment,
        [str(ONETASKGRAPH_BIN), "project", "create", onetaskgraph_user_layer.ORGANIZATION_PLANS]
        + ["--id", ORGANIZATION_PROJECT, "--title", "Rollout", "--classification", "private"]
        + ["--output", "json"]
        + ["--no-interactive"],
    )
    assert made.returncode == 0, made.stdout + made.stderr
    (created,) = json.loads(made.stdout)["items"]
    project = str(created["id"])
    routed = _projected(boards.environment, "organization-step", [ORGANIZATION], project=project)
    assert routed.returncode == 0, routed.stdout + routed.stderr
    answer = json.loads(routed.stdout)
    assert answer["source"] == onetaskgraph_user_layer.ORGANIZATION_PLANS, answer
    native = project.partition(":")[2]
    assert answer["project"] == native, answer
    (filed,) = boards.tracker.issues()
    assert filed["title"] == "Decide organization-step", filed
    assert filed["project"] == {"id": native}, filed
    assert _titles(HUMAN_TASKS_BOARD) == [] and _titles(PRIVATE_BOARD) == []


@pytest.mark.parametrize("boards", [False], indirect=True, ids=["no-private-route"])
def test_with_no_private_route_a_private_task_is_refused_and_nothing_is_written(
    boards: Boards,
) -> None:
    """A layer declaring no route leaves a private task nowhere it may land: refused, and
    neither board takes a write, while a public task still lands on `human-public`."""
    _fields_applied(boards.environment)
    refused = _projected(boards.environment, "private-step", [OWN_PRIVATE])
    assert refused.returncode != 0, refused.stdout
    failure = json.loads(refused.stdout)["failure"]
    assert failure["kind"] == "not-private-destination", failure
    assert failure["source"] == PUBLIC_BOARD, failure
    assert _titles(HUMAN_TASKS_BOARD) == [] and _titles(PRIVATE_BOARD) == []
    landed = _projected(boards.environment, "public-step", [OWN_PUBLIC])
    assert landed.returncode == 0, landed.stdout + landed.stderr
    assert _titles(HUMAN_TASKS_BOARD) == ["Decide public-step"]


# llmlint: ignore-end[e2e_not_mocked]
