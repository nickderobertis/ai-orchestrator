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
  a public-only plan copies onto `plans` exactly as before;
* **the follow-up commands**, `board-status`, `board-items --ticket` and `copy` as the
  follow-up task has the agent run them: the mixed owner's private ticket is placed, searched
  and copied on `private-followups` alone by `followups`' combined route, its public one on
  `followups`, and the organization's on its own tracker; a ticket listing several
  repositories is held to the repository the store's `task locate` answers it creates the
  issue in, and created there; and a third party's is refused as outside the owner before
  anything is written.

GitHub's Projects API is the loopback board `tests/github_board.py` serves, and the
organization's two Linear trackers and the host's private plans board are folders of Markdown
declared private, as the real sources are (`tests/onetaskgraph_user_layer.py`), because no
check may reach a live private board or Linear workspace.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import NamedTuple

import boundary_home
import follow_up_variables
import github_board
import onetaskgraph_user_layer
import pytest
import yaml
from github_board import (
    BOARD,
    CONFIGURED_PROJECT_NUMBER,
    FOLLOWUPS_OPTIONS,
    _follow_up_ticket,
    _plan_environment,
    _Repository,
    _serving_board,
    _written_ticket,
)
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator import follow_up_tickets as tickets
from orchestrator.plan_store import WRITABLE_PLUGIN
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
#: A third party's public repository, outside every owner a board of the layer files under.
THIRD_PARTY = "github.com/contoso/work"
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
    drafts_root: Path


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
        public=[PUBLIC, OWNER_PUBLIC, THIRD_PARTY],
        private=[ORGANIZATION, PERSONAL, OWNER_PRIVATE],
    )
    folders = {
        name: tmp_path / name
        for name in (
            onetaskgraph_user_layer.ORGANIZATION_PLANS,
            onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS,
            onetaskgraph_user_layer.PRIVATE_PLANS,
            onetaskgraph_user_layer.PRIVATE_FOLLOWUPS,
            PUBLIC_STAND_IN,
            PRIVATE_STAND_IN,
        )
    }
    # A follow-up ticket is copied carrying `backlog`, which the tracker reads as its own word.
    stood_in = {
        name: onetaskgraph_user_layer.stand_in(
            name, folders[name], tmp_path, copied=(tickets.Status.PROPOSED.value,)
        )
        for name in (
            onetaskgraph_user_layer.ORGANIZATION_PLANS,
            onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS,
        )
    }
    document = onetaskgraph_user_layer.with_stand_ins(stood_in)
    for private in (
        onetaskgraph_user_layer.PRIVATE_PLANS,
        onetaskgraph_user_layer.PRIVATE_FOLLOWUPS,
    ):
        folders[private].mkdir()
        document["sources"][private] = {
            "plugin": "local-md",
            "visibility": "private",
            "config": {"root": str(folders[private])},
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
    drafts_root = tmp_path / "follow-ups"
    environment[follow_up_variables.root_name()] = str(drafts_root)
    environment[follow_up_variables.plugin_name()] = WRITABLE_PLUGIN
    # The follow-up options beside the plans board's own, so one loopback serves both.
    with _serving_board(options=FOLLOWUPS_OPTIONS) as remote:
        environment.update(remote)
        yield Layer(
            environment=environment, authoring=authoring, folders=folders, drafts_root=drafts_root
        )


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


#: The machine every follow-up fixture's ticket is verified on.
HOST = "verifier-01.build.example"


def _follow_ups(layer: Layer, *arguments: str) -> subprocess.CompletedProcess[str]:
    """One follow-up command, as the follow-up task has the agent run it from this checkout."""
    return _run(
        layer,
        [str(REPO_ROOT / ".venv" / "bin" / "python"), "-m", "orchestrator.follow_up_tickets"]
        + list(arguments),
    )


def _owned_followups(layer: Layer) -> None:
    """`followups` filed under the mixed owner, with its configured repository the owner's own.

    The tracked board files under this repository's owner; a host doing the mixed owner's work
    files under that owner, which is what makes its public repository a repository of the
    board's owner. The loopback serves `followups` under the number it answers.
    """
    layer.environment["ONETASKGRAPH_SOURCES__FOLLOWUPS__CONFIG__OWNER"] = "sample-owner"
    layer.environment["ONETASKGRAPH_SOURCES__FOLLOWUPS__CONFIG__REPOSITORY"] = (
        "sample-owner/public-library"
    )
    layer.environment["ONETASKGRAPH_SOURCES__FOLLOWUPS__CONFIG__PROJECT_NUMBER"] = str(
        CONFIGURED_PROJECT_NUMBER
    )
    layer.environment[
        "ONETASKGRAPH_SOURCES__FOLLOWUPS__CONFIG__PACING__MIN_MUTATION_INTERVAL_MS"
    ] = "0"
    # Its fields set up the way the operator sets up the live board, which the copy refuses
    # to write a priority without.
    fields = _run(layer, ["just", "plans", "sources", "fields", "followups", "--apply"])
    assert fields.returncode == 0, fields.stdout + fields.stderr


def _ticket(layer: Layer, repository: str, cause: str) -> Path:
    """A sound follow-up ticket about ``repository``, written under the layer's drafts root."""
    owner, name = repository.removeprefix("github.com/").split("/")
    return _written_ticket(
        layer.drafts_root,
        _follow_up_ticket(_Repository(owner, name), cause=cause, verified_on=HOST),
    )


def _filed(layer: Layer, ticket: Path) -> str:
    """`board-status`, the word it prints written, then `copy`, as the task sequences them;
    the destination the copy printed."""
    decided = _follow_ups(layer, "board-status", "--board", "followups", str(ticket))
    assert decided.returncode == 0, decided.stdout + decided.stderr
    assert decided.stdout.strip() == tickets.Status.PROPOSED.value, decided.stdout
    copied = _follow_ups(layer, "copy", "--board", "followups", str(ticket))
    assert copied.returncode == 0, copied.stdout + copied.stderr
    return str(json.loads(copied.stdout)["destination"])


def _searched(layer: Layer, ticket: Path) -> list[str]:
    """`board-items --ticket`, by the ticket's own origin: the ids it answered."""
    run, cause = tickets.located_path(ticket)
    found = _follow_ups(
        layer,
        "board-items",
        "--board",
        "followups",
        "--ticket",
        str(ticket),
        "--origin",
        tickets.qualified_id(run, cause),
    )
    assert found.returncode == 0, found.stdout + found.stderr
    return [str(one["id"]) for one in json.loads(found.stdout)["items"]]


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] The task that added these
# follow-up journeys names this module as where `board-status`, `board-items --ticket` and `copy`
# are driven over the example layer's combined `followups` route, which this module's fixture
# installs; the project's one input set is the one the block above states, and the follow-up
# commands they drive read no input outside it.
def test_the_mixed_owners_private_ticket_is_placed_searched_and_copied_on_its_private_board(
    layer: Layer,
) -> None:
    """`followups`' combined route sends the mixed owner's private ticket to private-followups.

    The store's `task locate`, which every follow-up command asks first, places it there: its
    search asks that board, its copy lands there and nowhere else, and its next search there
    finds the item it created. Nothing reaches the public board.
    """
    _owned_followups(layer)
    ticket = _ticket(layer, OWNER_PRIVATE, "hidden-library-drops-a-row")
    private = layer.folders[onetaskgraph_user_layer.PRIVATE_FOLLOWUPS]

    assert _searched(layer, ticket) == []
    destination = _filed(layer, ticket)

    assert destination.startswith(f"{onetaskgraph_user_layer.PRIVATE_FOLLOWUPS}:"), destination
    assert _held(private) == [f"tasks/{destination.partition(':')[2]}.md"]
    assert _searched(layer, ticket) == [destination]
    assert BOARD.created == [], [issue.title for issue in BOARD.created]
    assert "hidden-library" not in _board_text()
    assert tickets.BINDING_FIELD not in ticket.read_text(encoding="utf-8")


def test_the_mixed_owners_public_ticket_is_placed_and_copied_on_followups(layer: Layer) -> None:
    """The owner's public repository matches no private route: the ticket lands on `followups`,
    its issue created in that repository, and private-followups holds nothing."""
    _owned_followups(layer)
    ticket = _ticket(layer, OWNER_PUBLIC, "public-library-loses-a-tag")

    destination = _filed(layer, ticket)

    assert destination.startswith("followups:"), destination
    (issue,) = BOARD.created
    assert str(issue.repository) == OWNER_PUBLIC.removeprefix("github.com/")
    assert _searched(layer, ticket) == [destination]
    assert _held(layer.folders[onetaskgraph_user_layer.PRIVATE_FOLLOWUPS]) == []


def test_the_all_private_organizations_ticket_still_reaches_its_own_tracker(layer: Layer) -> None:
    """The organization's repository-only route still sends its ticket to its tracker."""
    _owned_followups(layer)
    ticket = _ticket(layer, ORGANIZATION, "intake-drops-a-field")
    tracker = layer.folders[onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS]

    destination = _filed(layer, ticket)

    assert destination.startswith(f"{onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS}:")
    assert _held(tracker) == [f"tasks/{destination.partition(':')[2]}.md"]
    assert _searched(layer, ticket) == [destination]
    assert BOARD.created == []
    assert _held(layer.folders[onetaskgraph_user_layer.PRIVATE_FOLLOWUPS]) == []


def test_a_ticket_listing_several_repositories_is_held_to_where_the_store_creates_its_issue(
    layer: Layer,
) -> None:
    """`validate` holds the record's `repository` to the store's `creates_in`, and the issue is
    created there: the board's configured repository, not either of those the ticket lists."""
    _owned_followups(layer)
    listed = (OWNER_PUBLIC, PUBLIC)
    configured = "github.com/sample-owner/public-library"
    spanning = dataclasses.replace(
        _follow_up_ticket(
            _Repository("sample-owner", "public-library"),
            cause="two-libraries-disagree-on-a-tag",
            verified_on=HOST,
        ),
        changes=tuple(tickets.Origin(one) for one in listed),
        repository=tickets.Origin(PUBLIC),
        title="ai-orchestrator: two libraries disagree on a tag",
        basis=tuple(
            sorted(tickets.Basis(tickets.Origin(one), tickets.Commit("0" * 40)) for one in listed)
        ),
    )
    ticket = _written_ticket(layer.drafts_root, spanning)

    refused = _follow_ups(layer, "validate", "--pending", str(ticket))
    assert refused.returncode == tickets.UNSOUND, refused.stdout + refused.stderr
    assert (
        f"in {configured!r}, as its `task locate` answers for a ticket listing "
        f"{OWNER_PUBLIC}, {PUBLIC}; its record's `repository` names {configured!r}, not "
        f"{PUBLIC!r}"
    ) in " ".join(refused.stderr.split()), refused.stderr

    held_to = dataclasses.replace(
        spanning,
        repository=tickets.Origin(configured),
        title="public-library: two libraries disagree on a tag",
    )
    _written_ticket(layer.drafts_root, held_to)
    validated = _follow_ups(layer, "validate", "--pending", str(ticket))
    assert validated.returncode == tickets.SOUND, validated.stdout + validated.stderr
    destination = _filed(layer, ticket)

    assert destination.startswith("followups:"), destination
    (issue,) = BOARD.created
    assert str(issue.repository) == configured.removeprefix("github.com/")


def test_a_third_partys_ticket_is_refused_as_outside_the_owner_before_anything_is_written(
    layer: Layer,
) -> None:
    """A third party's public repository is placed on `followups` and its issue would be
    created in that repository, outside the owner `followups` files under: `board-status` and
    `copy` each refuse it, and neither the ticket nor any board is written."""
    _owned_followups(layer)
    ticket = _ticket(layer, THIRD_PARTY, "work-drops-a-page")
    before = ticket.read_text(encoding="utf-8")

    refused = _follow_ups(layer, "board-status", "--board", "followups", str(ticket))
    assert refused.returncode == tickets.OUTSIDE_OWNER, refused.stderr
    assert (f"{THIRD_PARTY!r}, which is not a repository of the owner 'sample-owner'") in " ".join(
        refused.stderr.split()
    ), refused.stderr
    # Its copy refuses too: `board-status` refused it before writing its estimate.
    copied = _follow_ups(layer, "copy", "--board", "followups", str(ticket))
    assert copied.returncode != tickets.SOUND, copied.stdout

    assert ticket.read_text(encoding="utf-8") == before
    assert BOARD.created == []
    for name in (
        onetaskgraph_user_layer.PRIVATE_FOLLOWUPS,
        onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS,
    ):
        assert _held(layer.folders[name]) == [], name


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


# llmlint: ignore-end[e2e_not_mocked]
