"""The store's public boundary and a host layer's routes, driven over synthetic identities.

The tracked `onetaskgraph.yaml` lands the store's public boundary off, and a host switches it
on in its own user layer — a `write_policy` naming `onevcs boundary inspect` and `onevcs
boundary check`, and both boards declared `visibility: public` — beside the organization's own
trackers and the repository routes that send its work there
(`config/onetaskgraph.user-layer.example.yaml`). The layer installed here is that host's. These
journeys drive both halves through this checkout's real recipes, the installed plan-store CLI
and the pinned `onevcs`, answering from a registry this module writes through `onevcs` itself
(`tests/boundary_home.py`):

* **the write policy**: a neutral task is written to a public destination, and one naming a
  private repository — as a repository, or as a term in its text — is refused there and
  written where a source is declared private. The same write passing once the check is told
  to derive no terms is what says the refusal is the host-resolved policy's, read by the write,
  rather than a layer that loaded and was never asked;
* **the routes**, through `just plans`, the recipe over the store's own copy: an
  organization's follow-up ticket reaches its own tracker through the layer's `followups`
  route; an organization's plan is copied straight to its tracker, and
  a copy of it onto `plans` is refused whole rather than routed; a plan mixing it with public
  work is refused and never split; personal private work — alone or beside a public
  repository — is refused at the board; and a public-only plan copies onto `plans` exactly as
  before.

GitHub's Projects API is the loopback board `tests/github_board.py` serves, and the
organization's two Linear trackers are folders of Markdown declared private, as the real ones
are (`tests/onetaskgraph_user_layer.py`), because no check may reach a Linear workspace.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, NamedTuple

import boundary_home
import onetaskgraph_user_layer
import pytest
from github_board import BOARD, _plan_environment, _serving_board
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator.project_store import PlanNode, write_plan_project
from orchestrator.root import REPO_ROOT

#: A public repository every neutral fixture names: this repository's own identity.
PUBLIC = boundary_home.THIS_REPOSITORY
#: An organization's repository, registered private and matched by the layer's routes.
ORGANIZATION = "github.com/hiddenco/qh-api"
#: A personal private repository under a public owner, which no route matches.
PERSONAL = "github.com/sample-owner/pebblecraft"
#: The term `onevcs boundary check` derives from that repository's registered name.
PERSONAL_TERM = "pebblecraft"

#: Two folders the write-policy journey writes to, one declared public and one private.
PUBLIC_STAND_IN = "public-stand-in"
PRIVATE_STAND_IN = "private-stand-in"

#: The refusals the store answers with, in its own words.
NOT_PRIVATE = "is not declared private"
CARRIES_A_TERM = "carries a term of a private repository"
NOT_VERIFIED_PRIVATE = "which is not verified private"
PRIVATE_UNDER_PUBLIC = "a public project never holds a private member"
#: What the public folder holds once the neutral write landed, and nothing else ever does.
NEUTRAL_RECORDS = ["projects/work.md", "tasks/tidy-the-reader.md"]


class Layer(NamedTuple):
    """One journey's environment and the folders its user layer declares."""

    environment: dict[str, str]
    authoring: Path
    folders: Mapping[str, Path]


@pytest.fixture
def layer(tmp_path: Path) -> Iterator[Layer]:
    """The example layer switching the boundary on, its trackers stood in, over a registry and
    the loopback board."""
    home = boundary_home.registered_home(
        tmp_path / "registry", public=[PUBLIC], private=[ORGANIZATION, PERSONAL]
    )
    folders = {
        name: tmp_path / name
        for name in (
            onetaskgraph_user_layer.ORGANIZATION_PLANS,
            onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS,
            PUBLIC_STAND_IN,
            PRIVATE_STAND_IN,
        )
    }
    stood_in = {
        name: onetaskgraph_user_layer.stand_in(name, folders[name], tmp_path)
        for name in (
            onetaskgraph_user_layer.ORGANIZATION_PLANS,
            onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS,
        )
    }
    document: dict[str, Any] = onetaskgraph_user_layer.screened(
        onetaskgraph_user_layer.without_private_stores(
            onetaskgraph_user_layer.with_stand_ins(stood_in)
        )
    )
    for name, visibility in ((PUBLIC_STAND_IN, "public"), (PRIVATE_STAND_IN, "private")):
        folders[name].mkdir()
        document["sources"][name] = {
            "plugin": "local-md",
            "visibility": visibility,
            "config": {"root": str(folders[name])},
        }
    config_home = onetaskgraph_user_layer.installed(tmp_path / "config-home", document)
    authoring = tmp_path / "authoring"
    authoring.mkdir()
    environment = boundary_home.environment(_plan_environment(authoring), home)
    environment[onetaskgraph_user_layer.XDG_CONFIG_HOME] = str(config_home)
    with _serving_board() as remote:
        environment.update(remote)
        yield Layer(environment=environment, authoring=authoring, folders=folders)


def _run(layer: Layer, command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this checkout's recipes and the pinned CLIs
        command,
        cwd=REPO_ROOT,
        env=layer.environment,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=e2e_timeout(180),
        check=False,
    )


def _store(layer: Layer, *arguments: str) -> subprocess.CompletedProcess[str]:
    return _run(layer, [str(ONETASKGRAPH_BIN), *arguments, "--no-interactive"])


def _held(folder: Path) -> list[str]:
    """Every record a folder source holds, relative to its root."""
    return sorted(str(path.relative_to(folder)) for path in folder.rglob("*.md"))


def _plan(layer: Layer, name: str, repositories: list[str]) -> str:
    """Author one plan in `authoring` whose tasks name ``repositories``, answering its id."""
    write_plan_project(
        layer.authoring,
        {
            "name": name,
            "tasks": [
                PlanNode(id=f"task-{index}", title=f"Task {index}", task="Do it.", repo=repository)
                for index, repository in enumerate(repositories)
            ],
        },
    )
    return f"authoring:{name}"


# llmlint: ignore-block[e2e_not_mocked] GitHub's Projects API is the one boundary doubled
# (`tests/github_board.py`), and the example layer's Linear trackers are folders, because no
# check may reach a Linear workspace; the store, its write policy, `onevcs` and the recipes
# are the real ones.
def test_the_host_resolved_write_policy_permits_neutral_writes_and_refuses_private_ones(
    layer: Layer, tmp_path: Path
) -> None:
    """A neutral write lands at a public destination; a private repository's never does.

    Named as a repository, a private one makes the item private, which a public project
    refuses; named in the item's text, its registered name is a term `onevcs boundary check`
    finds, which a destination not declared private refuses. Both land where a source is
    declared private, and the term write lands at the public destination when the check is
    told to derive no terms — so the refusal is the host-resolved policy's check, asked by the
    write, and not a configuration that loaded and was ignored.
    """
    body = tmp_path / "body.md"
    body.write_text("## What\nA neutral change.\n", encoding="utf-8")
    mentions = tmp_path / "mentions.md"
    mentions.write_text(
        f"## What\nPort the importer {PERSONAL.removeprefix('github.com/')} carries.\n",
        encoding="utf-8",
    )
    for source in (PUBLIC_STAND_IN, PRIVATE_STAND_IN):
        made = _store(layer, "project", "create", source, "--id", "work", "--title", "Work")
        assert made.returncode == 0, made.stderr

    def create(
        source: str, title: str, repository: str, text: Path, *extra: str
    ) -> subprocess.CompletedProcess[str]:
        return _store(
            layer,
            "task",
            "create",
            source,
            "--project",
            "work",
            "--title",
            title,
            "--repository",
            repository,
            "--body-file",
            str(text),
            *extra,
        )

    neutral = create(PUBLIC_STAND_IN, "Tidy the reader", PUBLIC, body)
    assert neutral.returncode == 0, neutral.stderr
    assert _held(layer.folders[PUBLIC_STAND_IN]) == NEUTRAL_RECORDS

    repository = create(PUBLIC_STAND_IN, "Port the importer", PERSONAL, body)
    assert repository.returncode != 0, repository.stdout
    assert PRIVATE_UNDER_PUBLIC in repository.stderr, repository.stderr

    titled = create(PUBLIC_STAND_IN, f"Fix the {PERSONAL_TERM} importer", PUBLIC, body)
    assert titled.returncode != 0, titled.stdout
    assert CARRIES_A_TERM in titled.stderr and NOT_VERIFIED_PRIVATE in titled.stderr, titled.stderr
    bodied = create(PUBLIC_STAND_IN, "Port the importer", PUBLIC, mentions)
    assert bodied.returncode != 0, bodied.stdout
    assert CARRIES_A_TERM in bodied.stderr, bodied.stderr
    assert _held(layer.folders[PUBLIC_STAND_IN]) == NEUTRAL_RECORDS

    unscreened = create(
        PUBLIC_STAND_IN, f"Fix the {PERSONAL_TERM} importer", PUBLIC, body, "--term-scope-empty"
    )
    assert unscreened.returncode == 0, unscreened.stderr

    private = _store(
        layer,
        "project",
        "create",
        PRIVATE_STAND_IN,
        "--id",
        "personal",
        "--title",
        "Personal",
        "--classification",
        "private",
    )
    assert private.returncode == 0, private.stderr
    for title, repository_, text in (
        ("Port the importer", PERSONAL, body),
        (f"Fix the {PERSONAL_TERM} importer", PUBLIC, mentions),
    ):
        written = _store(
            layer,
            "task",
            "create",
            PRIVATE_STAND_IN,
            "--project",
            "personal",
            "--title",
            title,
            "--repository",
            repository_,
            "--body-file",
            str(text),
        )
        assert written.returncode == 0, written.stderr


def test_an_organization_s_ticket_reaches_its_own_tracker_by_the_layer_s_route(
    layer: Layer,
) -> None:
    """A ticket of the organization copied to `followups` lands in its tracker, not the board.

    A personal private ticket copied the same way is refused at the board: no route
    matches it, and nothing places a private item on a public source.
    """
    _plan(layer, "tickets", [ORGANIZATION])
    _plan(layer, "personal-tickets", [PERSONAL])
    folder = layer.folders[onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS]

    routed = _run(
        layer, ["just", "plans", "task", "copy", "authoring:tickets/task-0", "--to", "followups"]
    )
    assert routed.returncode == 0, routed.stdout + routed.stderr
    assert f"{onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS}:tickets/task-0" in routed.stdout
    assert "tasks/tickets/task-0.md" in _held(folder)

    refused = _run(
        layer,
        ["just", "plans", "task", "copy", "authoring:personal-tickets/task-0", "--to", "followups"],
    )
    assert refused.returncode != 0, refused.stdout
    assert NOT_PRIVATE in refused.stderr, refused.stderr
    assert BOARD.created == [], [issue.title for issue in BOARD.created]


@pytest.mark.parametrize(
    ("name", "repositories"),
    [
        ("organization", [ORGANIZATION, ORGANIZATION]),
        ("mixed", [PUBLIC, ORGANIZATION]),
        ("personal", [PERSONAL]),
        ("personal-mixed", [PUBLIC, PERSONAL]),
    ],
)
def test_a_plan_touching_private_work_is_refused_whole_at_the_board(
    layer: Layer, name: str, repositories: list[str]
) -> None:
    """A copy onto `plans` refuses a plan naming a private repository, and splits none.

    The plan is a private item however its tasks would route, and the store refuses a
    private item at a source not declared private before any route places a task: nothing
    reaches the board, and nothing reaches the organization's tracker.
    """
    plan = _plan(layer, name, repositories)

    copied = _run(layer, ["just", "plans", "project", "copy", plan, "--to", "plans"])

    assert copied.returncode != 0, copied.stdout
    assert NOT_PRIVATE in copied.stderr, copied.stderr
    assert BOARD.created == [], [issue.title for issue in BOARD.created]
    assert _held(layer.folders[onetaskgraph_user_layer.ORGANIZATION_PLANS]) == []


def test_an_organization_s_plan_is_copied_straight_to_its_own_tracker(layer: Layer) -> None:
    """A copy straight to the organization's tracker lands the plan there whole.

    The store's own copy, through `just plans`, which `just copy-plan --to` runs once a plan's
    review records stand; `tests/plan_tooling/plan_flow/test_linear_routed_plans_e2e.py`
    drives that recipe, through `just finish-plan`, to the same tracker.
    """
    plan = _plan(layer, "organization", [ORGANIZATION, ORGANIZATION])

    copied = _run(
        layer,
        [
            "just",
            "plans",
            "project",
            "copy",
            plan,
            "--to",
            onetaskgraph_user_layer.ORGANIZATION_PLANS,
        ],
    )

    assert copied.returncode == 0, copied.stdout + copied.stderr
    assert _held(layer.folders[onetaskgraph_user_layer.ORGANIZATION_PLANS]) == [
        "projects/organization.md",
        "tasks/organization/task-0.md",
        "tasks/organization/task-1.md",
    ]
    assert BOARD.created == [], [issue.title for issue in BOARD.created]


def test_a_public_only_plan_copies_onto_the_board_as_before(layer: Layer) -> None:
    """A plan naming public repositories alone lands on `plans`, its tasks under its project."""
    plan = _plan(layer, "public", [PUBLIC, PUBLIC])

    copied = _run(layer, ["just", "plans", "project", "copy", plan, "--to", "plans"])

    assert copied.returncode == 0, copied.stdout + copied.stderr
    assert [issue.title for issue in BOARD.created] == ["public", "Task 0", "Task 1"]
    assert _held(layer.folders[onetaskgraph_user_layer.ORGANIZATION_PLANS]) == []


# llmlint: ignore-end[e2e_not_mocked]
