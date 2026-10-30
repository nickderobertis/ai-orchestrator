"""The store's public boundary and a host layer's routes, driven over synthetic identities.

The tracked `onetaskgraph.yaml` switches the store's public boundary on — a `write_policy`
naming `onevcs boundary inspect` and `onevcs boundary check`, and both boards declared
`visibility: public` — and a host declares the organization's own trackers private in its user
layer, beside the repository routes that send its work there
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
  route; an organization's plan is copied straight to its tracker, and a copy of it onto
  `plans` homes it there whole; a plan mixing it with public work homes with its private work
  in that tracker, its public task alone reaching the board and nothing private with it;
  private work under an unmatched owner is refused at the board; the mixed owner's private
  work routes to private-plans while its public work stays public; and
  a public-only plan copies onto `plans` exactly as before.

GitHub's Projects API is the loopback board `tests/github_board.py` serves, and the
organization's two Linear trackers and the host's private plans board are folders of Markdown
declared private, as the real sources are (`tests/onetaskgraph_user_layer.py`), because no
check may reach a live private board or Linear workspace.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import NamedTuple

import boundary_home
import github_board
import onetaskgraph_user_layer
import pytest
import yaml
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
PERSONAL = "github.com/unrouted-owner/pebblecraft"
OWNER_PRIVATE = "github.com/sample-owner/hidden-library"
OWNER_PUBLIC = "github.com/sample-owner/public-library"
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
def layer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Layer]:
    """The example layer switching the boundary on, its trackers stood in, over a registry and
    the loopback board."""
    monkeypatch.setitem(
        github_board.REPOSITORY_NODE_IDS,
        github_board._Repository.parse("sample-owner/public-library", "owner fixture"),
        github_board._RepositoryNodeId("R_owner_public"),
    )
    home = boundary_home.registered_home(
        tmp_path / "registry",
        public=[PUBLIC, OWNER_PUBLIC],
        private=[ORGANIZATION, PERSONAL, OWNER_PRIVATE],
    )
    folders = {
        name: tmp_path / name
        for name in (
            onetaskgraph_user_layer.ORGANIZATION_PLANS,
            onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS,
            onetaskgraph_user_layer.PRIVATE_PLANS,
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
    document = onetaskgraph_user_layer.with_stand_ins(stood_in)
    document["sources"].pop(onetaskgraph_user_layer.PRIVATE_FOLLOWUPS)
    folders[onetaskgraph_user_layer.PRIVATE_PLANS].mkdir()
    document["sources"][onetaskgraph_user_layer.PRIVATE_PLANS] = {
        "plugin": "local-md",
        "visibility": "private",
        "config": {"root": str(folders[onetaskgraph_user_layer.PRIVATE_PLANS])},
    }
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


# llmlint: ignore-block[e2e_not_mocked] The public GitHub Projects wire is loopback
# (`tests/github_board.py`); the example's Linear trackers and private plans board are
# private folders, because no check may reach a live private board or Linear workspace.
# The store's routing, write policy, `onevcs` and recipes are real.
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
        ("personal", [PERSONAL]),
        ("personal-mixed", [PUBLIC, PERSONAL]),
    ],
)
def test_a_plan_touching_personal_private_work_is_refused_whole_at_the_board(
    layer: Layer, name: str, repositories: list[str]
) -> None:
    """A copy onto `plans` refuses a plan naming a private repository no route places.

    The plan is a private item, and with no private source its private voters place at it has
    nowhere private to go: the store refuses it before any route places a task, so nothing
    reaches the board and nothing reaches the organization's tracker.
    """
    plan = _plan(layer, name, repositories)

    copied = _run(layer, ["just", "plans", "project", "copy", plan, "--to", "plans"])

    assert copied.returncode != 0, copied.stdout
    assert NOT_PRIVATE in copied.stderr, copied.stderr
    assert BOARD.created == [], [issue.title for issue in BOARD.created]
    assert _held(layer.folders[onetaskgraph_user_layer.ORGANIZATION_PLANS]) == []


#: What names an organization's work, and so may never reach the public board: its
#: repository's owner and name, as `onevcs boundary check` would find them.
ORGANIZATION_TERMS = ("hiddenco", "qh-api")


def _board_text() -> str:
    """Everything the public board holds: each created issue's title, body and origin."""
    return "\n".join(
        f"{issue.title}\n{issue.body}\n{issue.origin or ''}" for issue in BOARD.created
    )


@pytest.mark.parametrize(
    ("name", "repositories", "on_the_board"),
    [
        ("organization", [ORGANIZATION, ORGANIZATION], []),
        ("mixed", [PUBLIC, ORGANIZATION], ["Public work", "Task 0"]),
    ],
)
def test_a_plan_touching_an_organizations_work_homes_in_its_tracker_and_keeps_it_private(
    layer: Layer, name: str, repositories: list[str], on_the_board: list[str]
) -> None:
    """A copy onto `plans` homes a plan holding an organization's work in that tracker.

    The plan is private, so its home goes to the source declared private that its private
    tasks route to, with every task of the plan and the project's record. A public task of a
    mixed plan is seen where its own repository places it only as the store's neutral view,
    under a `Public work` project on the board, written through the screened boundary — and
    nothing the board holds names the private home, any canonical task, or the organization's
    repository.
    """
    plan = _plan(layer, name, repositories)
    folder = layer.folders[onetaskgraph_user_layer.ORGANIZATION_PLANS]

    copied = _run(layer, ["just", "plans", "project", "copy", plan, "--to", "plans"])

    assert copied.returncode == 0, copied.stdout + copied.stderr
    home = f"{onetaskgraph_user_layer.ORGANIZATION_PLANS}:{name}"
    assert home in copied.stdout, copied.stdout
    private = [f"tasks/{name}/task-{index}.md" for index in range(len(repositories))]
    assert _held(folder) == sorted([f"projects/{name}.md", *private]), _held(folder)
    assert [issue.title for issue in BOARD.created] == on_the_board
    held = _board_text()
    for private_term in (home, *private, *(path[len("tasks/") : -3] for path in private)):
        assert private_term not in held, (private_term, held)
    for term in ORGANIZATION_TERMS:
        assert term not in held.lower(), (term, held)


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


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] The task that added these
# journeys requires them in this module, which installs the example layer, and this project's
# one input set is what its sibling module `test_screened_host_states_e2e.py` really reads
# through `just check-plan` and `just copy-plan`, whose imports reach `plan_review.py`,
# `design_approval.py`, `spike_plan.py` and `publication_guard.py`; narrowing it would fail
# the undeclared-read guard in `tests/conftest.py`, and splitting the project is drafted as a
# follow-up rather than done in this change.
@pytest.mark.parametrize("repositories", [[OWNER_PRIVATE], [OWNER_PUBLIC, OWNER_PRIVATE]])
def test_owner_private_work_routes_and_locates_at_its_private_home(
    layer: Layer, repositories: list[str]
) -> None:
    """Locate predicts the private home; copying keeps private content off the board."""
    layer.environment["ONETASKGRAPH_SOURCES__PLANS__CONFIG__REPOSITORY"] = (
        "sample-owner/public-library"
    )
    plan = _plan(layer, "owner-work", repositories)
    private_index = repositories.index(OWNER_PRIVATE)
    located = _run(
        layer, ["just", "plans", "task", "locate", f"{plan}/task-{private_index}", "--to", "plans"]
    )
    assert located.returncode == 0, located.stdout + located.stderr
    assert "private-plans" in located.stdout, located.stdout
    copied = _run(layer, ["just", "plans", "project", "copy", plan, "--to", "plans"])
    assert copied.returncode == 0, copied.stdout + copied.stderr
    assert "private-plans:owner-work" in copied.stdout, copied.stdout
    assert _held(layer.folders[onetaskgraph_user_layer.PRIVATE_PLANS]) == sorted(
        [
            "projects/owner-work.md",
            *(f"tasks/owner-work/task-{index}.md" for index in range(len(repositories))),
        ]
    )
    assert [issue.title for issue in BOARD.created] == (
        ["Public work", "Task 0"] if OWNER_PUBLIC in repositories else []
    )
    for term in ("hidden-library", "private-plans", "owner-work", f"task-{private_index}"):
        assert term not in _board_text(), _board_text()


def test_owner_public_work_stays_on_the_public_board(layer: Layer) -> None:
    """The owner's public repository does not match its private combined route."""
    layer.environment["ONETASKGRAPH_SOURCES__PLANS__CONFIG__REPOSITORY"] = (
        "sample-owner/public-library"
    )
    plan = _plan(layer, "owner-public", [OWNER_PUBLIC])
    copied = _run(layer, ["just", "plans", "project", "copy", plan, "--to", "plans"])
    assert copied.returncode == 0, copied.stdout + copied.stderr
    assert [issue.title for issue in BOARD.created] == ["owner-public", "Task 0"]
    assert _held(layer.folders[onetaskgraph_user_layer.PRIVATE_PLANS]) == []


@pytest.mark.parametrize(
    ("kinds", "repository"),
    [
        (["repository", "classification", "combined", "combined"], OWNER_PRIVATE),
        (["repository", "classification", "classification"], OWNER_PRIVATE),
        (["repository", "repository"], OWNER_PUBLIC),
    ],
)
def test_overlapping_routes_follow_kind_precedence_and_written_order(
    layer: Layer, kinds: list[str], repository: str
) -> None:
    """Locate and copy choose the first entry of the highest-priority matching kind.

    Lower-priority entries deliberately precede the winning kind, and each winning kind
    has two matching entries with different destinations. This distinguishes both kind
    precedence and written order within all three kinds described by the example layer.
    """
    config = (
        Path(layer.environment[onetaskgraph_user_layer.XDG_CONFIG_HOME])
        / "onetaskgraph"
        / "config.yaml"
    )
    document = yaml.safe_load(config.read_text(encoding="utf-8"))
    winning_kind = kinds[-1]
    winner_index = kinds.index(winning_kind)
    routes = []
    for index, kind in enumerate(kinds):
        route = {
            "to": (
                PRIVATE_STAND_IN if index == winner_index else onetaskgraph_user_layer.PRIVATE_PLANS
            )
        }
        if kind in ("repository", "combined"):
            route["repositories"] = ["github.com/sample-owner/*"]
        if kind in ("classification", "combined"):
            route["classification"] = "private"
        routes.append(route)
    document["sources"]["plans"]["routes"] = routes
    config.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    plan = _plan(layer, "overlapping", [repository])

    located = _run(layer, ["just", "plans", "task", "locate", f"{plan}/task-0", "--to", "plans"])
    assert located.returncode == 0, located.stdout + located.stderr
    assert PRIVATE_STAND_IN in located.stdout, located.stdout
    copied = _run(layer, ["just", "plans", "task", "copy", f"{plan}/task-0", "--to", "plans"])
    assert copied.returncode == 0, copied.stdout + copied.stderr
    assert f"{PRIVATE_STAND_IN}:overlapping/task-0" in copied.stdout, copied.stdout
    assert _held(layer.folders[PRIVATE_STAND_IN]) == ["tasks/overlapping/task-0.md"]
    assert _held(layer.folders[onetaskgraph_user_layer.PRIVATE_PLANS]) == []
    assert BOARD.created == []


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


# llmlint: ignore-end[e2e_not_mocked]
