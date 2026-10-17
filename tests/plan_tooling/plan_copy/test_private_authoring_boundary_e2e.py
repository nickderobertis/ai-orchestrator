"""An organization's plan is drafted in the private `authoring` source and never leaves it.

`authoring` is declared `visibility: private` here, and the store's public boundary is switched
on through a `write_policy` in a configuration home of this module's own, since the tracked
`onetaskgraph.yaml` lands it switched off (`boundary_home.screening`): the pinned `onevcs
boundary inspect` answers each repository's visibility and `onevcs boundary check` screens
every write to a source not declared private. These journeys drive that configuration as a
planner and a design-doc dispatch meet it:

* an organization's plan — its task naming a private repository — is written into
  `authoring`, its plan-level budget answers recorded on it and its design document
  rendered with those answers beside it, and each is read back as stored;
* that plan copied onto the `plans` board, declared public, is refused, and so is a public
  plan whose task merely names the private repository in its text — the boundary check's
  own refusal — while the same public plan without that name reaches the board.

The repositories' visibility is declared in a state root of this module's own, through the
real `onevcs rules apply` and `onevcs register`; the private one is a synthetic name. GitHub's
Projects API is the loopback board `tests/github_board.py` serves, because the live board is
shared by every run of this repository. The store, `onevcs`, the engine's template resolve and
the budget writer are the installed ones.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import boundary_home
import github_board as served
import pytest
from github_board import AUTHORING_SOURCE, _prepare_plan_sources, _serving_board
from project_fixtures import budgeted, designed, no_budgets
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator import plan_budgets
from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

#: A synthetic organization's repository, registered private.
PRIVATE_REPOSITORY = "github.com/hiddenco/qh-api"
#: This repository, registered public.
PUBLIC_REPOSITORY = boundary_home.THIS_REPOSITORY
#: The public board a plan is copied onto, declared `visibility: public` by the screening layer
#: (`boundary_home.WRITE_POLICY`) this journey switches the boundary on with.
BOARD = "plans"


class Bench(NamedTuple):
    """One journey's authoring root and the environment every command runs under."""

    authoring: Path
    environment: dict[str, str]


@pytest.fixture
def bench(tmp_path: Path) -> Bench:
    """An authoring root of the journey's own, read under a state root declaring visibility."""
    authoring = tmp_path / "authoring"
    authoring.mkdir()
    home = boundary_home.registered_home(
        tmp_path / "registry", public=(PUBLIC_REPOSITORY,), private=(PRIVATE_REPOSITORY,)
    )
    environment = boundary_home.environment(None, home)
    environment.update(_prepare_plan_sources(authoring))
    # The boundary switched on, in a configuration home of this journey's own.
    environment["XDG_CONFIG_HOME"] = str(boundary_home.screened_config_home(tmp_path))
    # Declared private at the store's environment layer: the tracked document declares no
    # source's visibility while the boundary is landed switched off, which waits for the store
    # to walk a public task's dependencies without authenticating every private source.
    environment[f"ONETASKGRAPH_SOURCES__{AUTHORING_SOURCE.upper()}__VISIBILITY"] = "private"
    return Bench(authoring, environment)


def _task(what: str) -> str:
    return (
        f"## What\n\n{what}\n\n"
        "## Why\n\nThe team needs it.\n\n"
        "## Acceptance criteria\n\n- A test drives the change end to end.\n"
    )


def _drafted(bench: Bench, name: str, repository: str, what: str) -> str:
    """Write a one-task plan into `authoring` as `just plan` stores one, and record its budgets."""
    write_plan_project(
        bench.authoring,
        {
            "schema_version": 3,
            "goal": {"text": "Ship the change"},
            "name": name,
            "tasks": [
                {
                    "id": "change",
                    "persona": "engineer",
                    "repo": f"https://{repository}",
                    "title": "feat: ship the change",
                    "task": _task(what),
                }
            ],
        },
    )
    if repository == PRIVATE_REPOSITORY:
        # A private task is never filed under a public project, so an organization's plan
        # project is classified private, as its planner creates it.
        classified = _store(
            bench,
            "project",
            "create",
            AUTHORING_SOURCE,
            "--id",
            name,
            "--title",
            name,
            "--classification",
            "private",
            "--no-interactive",
        )
        assert classified.returncode == 0, classified.stderr
    return budgeted(AUTHORING_SOURCE, name, no_budgets([repository]), bench.environment)


def _run(bench: Bench, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - the installed CLIs this journey drives
        list(arguments),
        cwd=REPO_ROOT,
        env=bench.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )


def _store(bench: Bench, *arguments: str) -> subprocess.CompletedProcess[str]:
    return _run(bench, str(ONETASKGRAPH_BIN), *arguments)


def _writer_answers(bench: Bench, project: str) -> dict[str, object]:
    """The budget answers the design-doc dispatch is handed, from the budget writer's CLI."""
    written = _run(bench, sys.executable, "-m", "orchestrator.plan_budgets", project)
    assert written.returncode == 0, written.stderr
    answers = json.loads(written.stdout)
    assert isinstance(answers, dict), answers
    return answers


# llmlint: ignore-block[e2e_not_mocked] GitHub's Projects API is the one boundary doubled,
# for the reason `tests/github_board.py` gives — driving it writes to the live board this
# repository plans on; the store, `onevcs` and the records they write are all real.
@pytest.fixture
def board(bench: Bench) -> Iterator[Bench]:
    """The loopback board served as `plans`, the bench's environment pointed at it."""
    with _serving_board() as remote:
        yield Bench(bench.authoring, {**bench.environment, **remote})


# llmlint: ignore-end[e2e_not_mocked]


def _created_on_the_board() -> list[str]:
    """The titles of every issue the loopback board took and kept, read off the server."""
    return [issue.title for issue in served.BOARD.issues_created_and_kept()]


def test_an_organizations_plan_its_budgets_and_its_design_document_are_stored_in_authoring(
    bench: Bench,
) -> None:
    """Every write of an organization's plan reaches the private authoring source."""
    project = _drafted(bench, "org-plan", PRIVATE_REPOSITORY, "Change the qh-api intake form.")

    shown = _store(bench, "project", "show", project, "--json")
    assert shown.returncode == 0, shown.stderr
    (held,) = json.loads(shown.stdout)["items"]
    assert held["item"]["classification"] == "private", held["item"]
    recorded = held["item"]["metadata"][plan_budgets.PLAN_RECORD]
    assert recorded == no_budgets([PRIVATE_REPOSITORY]), recorded

    answers = _writer_answers(bench, project)
    designed(AUTHORING_SOURCE, "org-plan", [], bench.environment, budget_answers=answers)
    document = _store(bench, "document", "show", f"{AUTHORING_SOURCE}:org-plan-design", "--json")
    assert document.returncode == 0, document.stderr
    assert "org-plan" in json.loads(document.stdout)["items"][0]["item"]["content"]
    assert (bench.authoring / "documents").is_dir(), "the design document is not on disk"

    checked = _run(
        bench, sys.executable, "-m", "orchestrator.plan_budgets", project, "--check-design-document"
    )
    assert checked.returncode == 0, checked.stderr


def test_a_write_naming_a_private_repository_to_the_public_board_is_refused(board: Bench) -> None:
    """The organization's plan, and a public plan naming the private repository, stay off it.

    The control — the same public plan without the private name — reaches the board, so each
    refusal is the boundary's and not the board's.
    """
    private = _drafted(board, "org-plan", PRIVATE_REPOSITORY, "Change the intake form.")
    copied = _store(board, "project", "copy", private, "--to", BOARD)
    assert copied.returncode != 0
    assert "is private" in copied.stderr, copied.stderr

    naming = _drafted(board, "naming-plan", PUBLIC_REPOSITORY, "Mirror what qh-api does.")
    copied = _store(board, "project", "copy", naming, "--to", BOARD)
    assert copied.returncode != 0
    assert "carries a term of a private repository" in copied.stderr, copied.stderr
    assert _created_on_the_board() == [], "a refused copy left something on the board"

    public = _drafted(board, "public-plan", PUBLIC_REPOSITORY, "Mirror what the API does.")
    copied = _store(board, "project", "copy", public, "--to", BOARD)
    assert copied.returncode == 0, copied.stderr
    assert "feat: ship the change" in _created_on_the_board()


def test_an_organizations_plan_routes_to_its_private_plan_source(board: Bench) -> None:
    """Copy through the public board's routing and read the private destination back."""
    destination = "teamplans"
    root = board.authoring.parent / destination
    root.mkdir()
    environment = {
        **board.environment,
        "ONETASKGRAPH_SOURCES__PLANS__ROUTES__0__CLASSIFICATION": "private",
        "ONETASKGRAPH_SOURCES__PLANS__ROUTES__0__TO": destination,
        "ONETASKGRAPH_SOURCES__TEAMPLANS__PLUGIN": "local-md",
        "ONETASKGRAPH_SOURCES__TEAMPLANS__VISIBILITY": "private",
        "ONETASKGRAPH_SOURCES__TEAMPLANS__CONFIG__ROOT": str(root),
    }
    routed = Bench(board.authoring, environment)
    project = _drafted(routed, "team-plan", PRIVATE_REPOSITORY, "Change the intake form.")
    copied = _store(routed, "project", "copy", project, "--to", BOARD, "--json")
    assert copied.returncode == 0, copied.stderr
    listed = _store(routed, "project", "list", "--source", destination, "--json")
    assert listed.returncode == 0, listed.stderr
    (held,) = json.loads(listed.stdout)["items"]
    tasks = _store(
        routed, "task", "list", "--source", destination, "--project", held["id"], "--json"
    )
    assert tasks.returncode == 0, tasks.stderr
    (task,) = json.loads(tasks.stdout)["items"]
    assert task["item"]["repositories"] == [PRIVATE_REPOSITORY]
