"""The plan-store recipes on a host whose organization's sources are private and cannot answer.

A host doing an organization's work declares that organization's trackers private in its own
user layer (`config/onetaskgraph.user-layer.example.yaml`), and a host that does not hold the
organization's credential — every other host, and this one between key rotations — reads
those sources as unavailable. Declaring any visibility switches the store's public boundary
on, so the layer installed here is the example as `onetaskgraph_user_layer.screened()` declares
it: the write policy, both boards public, the organization's two Linear sources private, and
their credential absent from the environment. The host's own private boards are left out, as
every boundary journey here leaves them out, because their GitHub endpoint is not this suite's
loopback board. Each journey installs the layer under a configuration home of its own, so no
layer of the host's reaches it.

Under the store before onetaskgraph 0.7.3 every dependency read failed on the organization's
sources (`task deps` exited 4, naming them), and `just review-plan` with it. These journeys
drive the recipes a manager runs — `just review-plan`, `just check-plan`, `just
approve-design` and the launch gate it answers, `just copy-plan`, `just follow-up` and `just
follow-ups` — and the ticket copy the follow-up run makes, through the installed CLIs; only
the paid provider and GitHub's Projects API are stood in.

Two gaps remain in the adopted releases, and each is pinned rather than hidden:

* **The copy.** The store's copy authenticates against every private source, so a copy of a
  public plan or ticket between sources it can reach is refused naming the organization's
  source and its missing credential (:data:`COPY_REFUSED_SOURCE`, :data:`COPY_REFUSAL`), on
  onetaskgraph 0.7.3 and 0.9.0 alike. The copying journeys accept a copy that lands or that
  refusal and nothing else, so a store that stops refusing keeps them green with no edit.
* **The launch.** The engine holds a launched plan's nodes, with no timeout, on a private
  source it could not ask about prerequisites (onepipeline's `docs/contract-divergences.md`,
  entry 131), so no journey here launches. `just approve-design` launches nothing — the
  engine's part in it is `template resolve` — so its journey records the approval and has
  the launch gate accept it, which is as far as a launch goes before the engine holds it.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import boundary_home
import follow_up_variables
import onetaskgraph_user_layer
import pytest
from github_board import (
    CONFIGURED_REPOSITORY,
    LINEAR_KEY_ENV,
    UNSERVED_LINEAR,
    _decided_status,
    _follow_up_step,
    _follow_up_ticket,
    _followups_environment,
    _serving_followups,
    _written_ticket,
)
from project_fixtures import project_from_plan, reviewed
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator import follow_up_tickets
from orchestrator.criteria_guard import APPENDIX
from orchestrator.root import REPO_ROOT

#: The organization's sources the layer declares private.
ORGANIZATION = (
    onetaskgraph_user_layer.ORGANIZATION_PLANS,
    onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS,
)

#: The refusal the adopted store's copy answers with, on the organization's source: the copy
#: either asks it, or builds it as the destination one of `followups`'s routes names, and each
#: ends on its missing credential.
COPY_REFUSED_SOURCE = f"source {onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS} could not"
COPY_REFUSAL = f"authentication for this source failed: set environment variable {LINEAR_KEY_ENV}"

#: A public source the copy journey copies a reviewed plan onto.
DESTINATION = "destination"


def _layer(destination: Path) -> dict[str, object]:
    """The example, screened, without the host's own boards, its Linear sources unserved.

    Each Linear source's endpoint is the loopback port nothing listens on, so even a request
    made past the missing credential stays on this machine; ``destination`` is a public
    folder a plan may be copied onto.
    """
    document = onetaskgraph_user_layer.screened(onetaskgraph_user_layer.without_private_stores())
    sources = document["sources"]
    for name in ORGANIZATION:
        sources[name]["config"]["endpoint"] = UNSERVED_LINEAR
    destination.mkdir()
    sources[DESTINATION] = {
        "plugin": "local-md",
        "visibility": "public",
        "config": {"root": str(destination)},
    }
    return document


@pytest.fixture
def uncredentialed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """This test's process, and every one it starts, under the layer with no Linear key.

    Answers the public folder the layer declares. The `onevcs` state root answering the write
    policy registers every public repository the fixtures name.
    """
    destination = tmp_path / DESTINATION
    home = onetaskgraph_user_layer.installed(tmp_path / "config-home", _layer(destination))
    boundary_home.answering(monkeypatch)
    monkeypatch.setenv(onetaskgraph_user_layer.XDG_CONFIG_HOME, str(home))
    monkeypatch.delenv(LINEAR_KEY_ENV, raising=False)
    yield destination


def _run(command: list[str], **stated: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this checkout's recipes and the installed CLIs
        command,
        cwd=REPO_ROOT,
        env={**os.environ, **stated},
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=e2e_timeout(300),
        check=False,
    )


def _reviewed_plan(tmp_path: Path, *, approve: bool = True) -> str:
    """A plan of one public node stated as a planner states it, reviewed by `just review-plan`.

    ``approve=False`` leaves its design document unapproved, for the journey that approves it.
    """
    task = (
        "## What\n\nAdd the route and the test that drives it.\n\n"
        "## Why\n\nThe user cannot complete a purchase without it.\n\n"
        "## Acceptance criteria\n\n"
        "- The route accepts a valid request and rejects an invalid one.\n"
        "- A request-level test drives the route end to end and covers both paths.\n"
        "- Every claim the dispatch makes about the finished work is true of the tree as it "
        "finally stands.\n\n"
        f"{(REPO_ROOT / APPENDIX).read_text(encoding='utf-8').strip()}\n"
    )
    plan = tmp_path / "uncredentialed.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Deliver the checkout route"},
                "name": "uncredentialed",
                "tasks": [
                    {
                        "id": "route",
                        "persona": "engineer",
                        "repo": "https://github.com/nickderobertis/some-service",
                        "title": "feat: add the checkout route",
                        "task": task,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return reviewed(project_from_plan(plan, f"uncredentialed-{tmp_path.name}", approve=approve))


def _store(*arguments: str) -> object:
    shown = _run([str(ONETASKGRAPH_BIN), "--json", *arguments])
    assert shown.returncode == 0, shown.stderr
    return json.loads(shown.stdout)


def _copied_or_refused_on_the_gap(copied: subprocess.CompletedProcess[str]) -> None:
    """A copy lands, or is refused on the organization's source's credential and nothing else."""
    said = copied.stdout + copied.stderr
    refused = COPY_REFUSED_SOURCE in copied.stderr and COPY_REFUSAL in copied.stderr
    assert copied.returncode == 0 or refused, said


# llmlint: ignore-block[e2e_not_mocked] Two boundaries are stood in and no more: the paid
# provider behind `just review-plan` (`project_fixtures.reviewed`) and GitHub's Projects API
# (`tests/github_board.py`); the recipes, the store, its layer and `onevcs` are the real ones.
def test_the_store_reads_the_organization_s_sources_private_and_uncredentialed(
    uncredentialed: Path,
) -> None:
    """What every journey here runs under: both sources declared private, neither able to
    authenticate, which a journey passing under no layer at all would not show."""
    shown = _store("config", "show")
    assert isinstance(shown, dict)
    settings = {str(entry["key"]): entry["value"] for entry in shown["settings"]}
    listed = _store("sources", "list")
    assert isinstance(listed, list)
    states = {str(source["source"]): source for source in listed}

    for name in ORGANIZATION:
        assert settings.get(f"sources.{name}.visibility") == "private", settings
        assert states[name]["state"] == "unavailable", states[name]
        assert states[name]["error"]["kind"] == "auth", states[name]


def test_a_plan_is_reviewed_and_checked_while_the_organization_s_sources_cannot_answer(
    uncredentialed: Path, tmp_path: Path
) -> None:
    """`just review-plan` records its pass and `just check-plan` accepts the plan."""
    project = _reviewed_plan(tmp_path)

    checked = _run(["just", "check-plan", project])

    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "1 dispatched node(s)" in checked.stdout, checked.stdout


def test_a_design_is_approved_and_gated_while_the_organization_s_sources_cannot_answer(
    uncredentialed: Path, tmp_path: Path
) -> None:
    """`just approve-design` records the approval, and the launch gate then accepts the plan."""
    project = _reviewed_plan(tmp_path, approve=False)

    unapproved = _run(["uv", "run", "--no-sync", "orchestrator-launch-gate", project])
    approved = _run(["just", "approve-design", project])
    gated = _run(["uv", "run", "--no-sync", "orchestrator-launch-gate", project])

    assert unapproved.returncode != 0, unapproved.stdout + unapproved.stderr
    assert approved.returncode == 0, approved.stdout + approved.stderr
    assert "recorded the approval of" in approved.stdout, approved.stdout
    assert gated.returncode == 0, gated.stdout + gated.stderr


def test_a_reviewed_plan_copies_or_is_refused_only_on_the_store_s_copy_gap(
    uncredentialed: Path, tmp_path: Path
) -> None:
    """`just copy-plan` onto a public folder lands the plan, or names the copy refusal alone."""
    project = _reviewed_plan(tmp_path)

    copied = _run(["just", "copy-plan", project, "--to", DESTINATION])

    _copied_or_refused_on_the_gap(copied)
    if copied.returncode == 0:
        assert list(uncredentialed.rglob("*.md")), "the copy reported success and wrote nothing"


def test_a_manager_drafts_a_follow_up_while_the_organization_s_sources_cannot_answer(
    uncredentialed: Path, tmp_path: Path
) -> None:
    """`just follow-up` writes the draft, and the store reads it back."""
    drafts = tmp_path / "drafts"
    body = (
        "## What happened\nThe sweep reports a family it never examined.\n\n"
        "## Where\n`scripts/sweep.sh`, its trailer.\n\n"
        "## Why it is out of scope\nThis node owns the layer.\n\n"
        "## Evidence\n```\n0 B reclaimed\n```\n"
    )
    drafted = subprocess.run(
        [
            "just",
            "follow-up",
            "layer-run",
            "--title",
            "The sweep trailer names an unexamined family",
            "--repository",
            boundary_home.THIS_REPOSITORY,
        ],
        cwd=REPO_ROOT,
        env={**os.environ, follow_up_variables.root_name(): str(drafts)},
        input=body,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )

    assert drafted.returncode == 0, drafted.stdout + drafted.stderr
    (written,) = (drafts / "tasks").rglob("*.md")
    assert written.parent == drafts / "tasks" / "layer-run" / "drafts", written
    qualified = drafted.stdout.split()[1]
    shown = _run(
        [str(ONETASKGRAPH_BIN), "task", "show", qualified, "--json"],
        **{follow_up_variables.root_name(): str(drafts)},
    )
    assert shown.returncode == 0, shown.stderr
    assert [held["item"]["title"] for held in json.loads(shown.stdout)["items"]] == [
        "The sweep trailer names an unexamined family"
    ]


def test_the_follow_ups_recipe_runs_and_its_ticket_copy_meets_only_the_store_s_copy_gap(
    uncredentialed: Path, tmp_path: Path
) -> None:
    """`just follow-ups` answers a run with nothing to verify, and the ticket copy its agent
    makes onto `followups` lands or names the copy refusal alone."""
    environment, drafts = _followups_environment(tmp_path)
    environment[onetaskgraph_user_layer.XDG_CONFIG_HOME] = os.environ[
        onetaskgraph_user_layer.XDG_CONFIG_HOME
    ]
    environment.pop(LINEAR_KEY_ENV, None)
    ticket = _written_ticket(drafts, _follow_up_ticket(CONFIGURED_REPOSITORY))

    with _serving_followups(environment):
        empty = subprocess.run(
            ["just", "follow-ups", "layer-empty-run"],
            cwd=REPO_ROOT,
            env=environment | {"ONEPIPELINE_RUNS_DIR": str(tmp_path / "runs")},
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=e2e_timeout(300),
            check=False,
        )
        _decided_status(environment, ticket)
        copied = _follow_up_step(
            environment, "copy", "--board", follow_up_tickets.BOARD, str(ticket)
        )

    assert empty.returncode == 0, empty.stdout + empty.stderr
    assert "nothing to verify and no follow-up run was launched" in empty.stdout, empty.stdout
    _copied_or_refused_on_the_gap(copied)


# llmlint: ignore-end[e2e_not_mocked]
