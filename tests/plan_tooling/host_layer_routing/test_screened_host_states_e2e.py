"""The plan-store recipes in each host state the tracked public boundary meets.

The tracked `onetaskgraph.yaml` switches the store's public boundary on and declares
`authoring` private, so every host runs these recipes screened. A host meets that in one of
three states (`onetaskgraph_user_layer.HostState`): no user layer at all; a user layer of
`config/onetaskgraph.user-layer.example.yaml`'s shape, over synthetic names, declaring its
organization's sources and its own stores private; and that layer in a process holding no
credential for the organization's sources. Each journey here drives a recipe a manager runs,
over content that names no private repository, in all three: `just review-plan`, `just
check-plan`, `just approve-design` and the launch gate it answers, and `just copy-plan` of a
plan with its design document onto the loopback `plans` board, approved there.

The second state is also where content naming a synthetic private repository is driven to
where it may go and away from where it may not, through `just plans`, the recipe over the
store, and `just copy-plan`: a task routed to the organization's tracker is written there;
that task, a design document, a spike report, a plan's budget answers and a follow-up ticket
naming the private repository are each written to the private `authoring` source and refused
at the public board and at `drafts`, a source declaring no visibility, with nothing written
there; and the same artifacts with neutral content reach the board.

One gap of the pinned store is pinned rather than hidden. Its copy authenticates every
private source, and builds the organization's tracker a board's route names, so in the
uncredentialed state a copy of a public plan onto the board is refused naming that source
and its missing credential (:data:`COPY_REFUSAL`). That journey accepts a copy that lands or
exactly that refusal, so a store that stops refusing keeps it green with no edit.

Only the paid provider behind `just review-plan` and GitHub's Projects API are stood in; the
recipes, the store, its layer and `onevcs` are the real ones.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import NamedTuple

import boundary_home
import github_board as served
import onetaskgraph_user_layer
import pytest
from github_board import AUTHORING_ROOT_ENV, AUTHORING_SOURCE, LINEAR_KEY_ENV, _serving_board
from onetaskgraph_user_layer import HostState, host_state
from project_fixtures import FIXTURE_UNIT, budgeted, designed, no_budgets, reviewed
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator import plan_budgets
from orchestrator.criteria_guard import APPENDIX
from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

#: The organization's two trackers the example layer declares private.
ORGANIZATION = (
    onetaskgraph_user_layer.ORGANIZATION_PLANS,
    onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS,
)

#: What the pinned store's copy says in the uncredentialed state: it builds the organization's
#: tracker the board's route names, and that ends on its missing credential.
COPY_REFUSAL = f"authentication for this source failed: set environment variable {LINEAR_KEY_ENV}"

#: A synthetic organization repository the example's routes send to its tracker, registered
#: private where content naming it is driven, as a host's rules overlay registers it.
PRIVATE_REPOSITORY = "github.com/hiddenco/qh-api"
#: The term `onevcs boundary check` derives from that repository's name.
PRIVATE_TERM = "qh-api"
#: A public repository the suite's state root registers public, and the loopback board's own.
PUBLIC_REPOSITORY = boundary_home.THIS_REPOSITORY

#: The refusals the boundary answers: a private item at a source not declared private, a write
#: carrying a private repository's term there, and a private member under a public project.
REFUSALS = (
    "is private",
    "carries a term of a private repository",
    "a public project never holds a private member",
)
#: The board a plan is copied onto, and `drafts`, the tracked source declaring no visibility.
BOARD = "plans"
UNDECLARED = "drafts"


class Host(NamedTuple):
    """The state a journey runs in, and the folder each stood-in private source writes."""

    state: HostState
    scratch: Path


@pytest.fixture(params=list(HostState), ids=[state.value for state in HostState])
def host(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Host:
    """This test's process, and every one it starts, on one host state.

    The `onevcs` state root answering the write policy registers every public repository the
    fixtures name, and the authoring root is the journey's own.
    """
    state = HostState(request.param)
    for name, value in host_state(state, tmp_path).items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    boundary_home.answering(monkeypatch)
    authoring = tmp_path / "authoring"
    authoring.mkdir()
    monkeypatch.setenv(AUTHORING_ROOT_ENV, str(authoring))
    return Host(state, tmp_path)


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


def _task(what: str) -> str:
    return (
        f"## What\n\n{what}\n\n"
        "## Why\n\nThe user cannot complete a purchase without it.\n\n"
        "## Acceptance criteria\n\n"
        "- The route accepts a valid request and rejects an invalid one.\n"
        "- A request-level test drives the route end to end and covers both paths.\n"
        "- Every claim the dispatch makes about the finished work is true of the tree as it "
        "finally stands.\n\n"
        f"{(REPO_ROOT / APPENDIX).read_text(encoding='utf-8').strip()}\n"
    )


def _reviewed_plan(tmp_path: Path, *, approve: bool = True) -> str:
    """A plan of one public node stated as a planner states it, reviewed by `just review-plan`.

    Its design document is rendered as a design-doc dispatch renders one; ``approve=False``
    leaves it unapproved, for the journeys that approve it.
    """
    plan = tmp_path / "screened.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Deliver the checkout route"},
                "name": "screened",
                "tasks": [
                    {
                        "id": "route",
                        "persona": "engineer",
                        "repo": f"https://{PUBLIC_REPOSITORY}",
                        "title": "feat: add the checkout route",
                        "task": _task("Add the route and the test that drives it."),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    # Draft in the configured private authoring source, as the real planning flow does.
    # Its source-side copy links let approval find the answers omitted from a public copy.
    content = json.loads(plan.read_text(encoding="utf-8"))
    content["tasks"][0]["unit"] = FIXTURE_UNIT
    native = f"screened-{tmp_path.name.replace('_', '-')}"
    root = Path(os.environ[AUTHORING_ROOT_ENV])
    write_plan_project(root, content, native_id=native)
    project = budgeted(AUTHORING_SOURCE, native, no_budgets([PUBLIC_REPOSITORY]))
    designed(
        AUTHORING_SOURCE,
        native,
        [
            {
                "task": "route",
                "delivers": "the route",
                "depends_on": "none",
                "location": str(root / "tasks" / native / "route.md"),
            }
        ],
    )
    if approve:
        approval = _run(["just", "approve-design", project])
        assert approval.returncode == 0, approval.stdout + approval.stderr
    return reviewed(project)


def _store(*arguments: str) -> object:
    shown = _run([str(ONETASKGRAPH_BIN), "--json", *arguments])
    assert shown.returncode == 0, shown.stderr
    return json.loads(shown.stdout)


def _copied(reported: str) -> dict[str, str]:
    """Each copied record's destination id by its source id, off `just copy-plan`'s report."""
    return {
        str(entry["source"]): str(entry["destination"])
        for entry in (json.loads(line) for line in reported.splitlines() if line.startswith("{"))
        if entry.get("destination")
    }


def _on_the_board() -> list[str]:
    """The titles of every issue the loopback board took and kept, read off the server."""
    return [issue.title for issue in served.BOARD.issues_created_and_kept()]


# llmlint: ignore-block[e2e_not_mocked] Two boundaries are stood in and no more: the paid
# provider behind `just review-plan` (`project_fixtures.reviewed`) and GitHub's Projects API
# (`tests/github_board.py`); the recipes, the store, its layer and `onevcs` are the real ones.
def test_each_state_reads_the_organization_s_sources_as_its_layer_declares_them(
    host: Host,
) -> None:
    """What every journey here runs under, which a journey passing under no layer would not show.

    The tracked policy and `authoring` private in every state; the organization's sources
    absent with no layer, private and answering with it, and private and unable to
    authenticate without their credential.
    """
    shown = _store("config", "show")
    assert isinstance(shown, dict)
    settings = {str(entry["key"]): entry["value"] for entry in shown["settings"]}
    listed = _store("sources", "list")
    assert isinstance(listed, list)
    states = {str(source["source"]): source for source in listed}

    assert settings.get("write_policy.check_command"), settings
    assert settings.get(f"sources.{AUTHORING_SOURCE}.visibility") == "private", settings
    for name in ORGANIZATION:
        if host.state is HostState.NO_USER_LAYER:
            assert name not in states, states
            continue
        assert settings.get(f"sources.{name}.visibility") == "private", settings
        if host.state is HostState.ORGANIZATION_PRIVATE:
            assert states[name]["state"] != "unavailable", states[name]
        else:
            assert states[name]["state"] == "unavailable", states[name]
            assert states[name]["error"]["kind"] == "auth", states[name]


def test_a_plan_is_reviewed_and_checked(host: Host) -> None:
    """`just review-plan` records its pass and `just check-plan` accepts the plan."""
    project = _reviewed_plan(host.scratch)

    checked = _run(["just", "check-plan", project])

    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "1 dispatched node(s)" in checked.stdout, checked.stdout


def test_a_design_is_approved_and_the_launch_gate_accepts_it(host: Host) -> None:
    """`just approve-design` records the approval, and the launch gate then accepts the plan."""
    project = _reviewed_plan(host.scratch, approve=False)

    unapproved = _run(["uv", "run", "--no-sync", "orchestrator-launch-gate", project])
    approved = _run(["just", "approve-design", project])
    gated = _run(["uv", "run", "--no-sync", "orchestrator-launch-gate", project])

    assert unapproved.returncode != 0, unapproved.stdout + unapproved.stderr
    assert approved.returncode == 0, approved.stdout + approved.stderr
    assert "recorded the approval of" in approved.stdout, approved.stdout
    assert gated.returncode == 0, gated.stdout + gated.stderr


def test_a_plan_copies_onto_the_board_with_its_document_and_is_approved_there(
    host: Host,
) -> None:
    """`just copy-plan` puts the plan and its design document on the board, approved there.

    In the uncredentialed state the pinned store may refuse the copy on the organization's
    missing credential and nothing else (:data:`COPY_REFUSAL`); the board is then left as it
    was.
    """
    project = _reviewed_plan(host.scratch, approve=False)

    with _serving_board() as remote:
        on_the_board = {**os.environ, **remote}
        copied = _run(["just", "copy-plan", project, "--to", BOARD], **on_the_board)
        if host.state is HostState.ORGANIZATION_UNCREDENTIALED and copied.returncode != 0:
            assert COPY_REFUSAL in copied.stderr, copied.stdout + copied.stderr
            assert sum(source in copied.stderr for source in ORGANIZATION) == 1, copied.stderr
            assert _on_the_board() == [], "a refused copy left something on the board"
            return
        assert copied.returncode == 0, copied.stdout + copied.stderr
        board_project = _copied(copied.stdout)[project]
        titles = _on_the_board()
        approved = _run(["just", "approve-design", board_project], **on_the_board)
        gated = _run(
            ["uv", "run", "--no-sync", "orchestrator-launch-gate", board_project], **on_the_board
        )

    assert "feat: add the checkout route" in titles, titles
    assert len(titles) > 1, f"the copy left the plan's design document behind: {titles}"
    assert approved.returncode == 0, approved.stdout + approved.stderr
    assert "recorded the approval of" in approved.stdout, approved.stdout
    assert gated.returncode == 0, gated.stdout + gated.stderr


# llmlint: ignore-end[e2e_not_mocked]


class Written(NamedTuple):
    """Where a write landed: the private source's folder, and whether each other refused it."""

    authoring: Path
    drafts: Path


@pytest.fixture
def organization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Written]:
    """The organization-private host, with `drafts` rooted at a folder of the journey's own."""
    for name, value in host_state(HostState.ORGANIZATION_PRIVATE, tmp_path).items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    # Every public repository the fixtures name registered public; whether the organization's
    # is registered too is each journey's to say.
    boundary_home.answering(monkeypatch)
    authoring, drafts = tmp_path / "authoring", tmp_path / "drafts"
    authoring.mkdir()
    drafts.mkdir()
    monkeypatch.setenv(AUTHORING_ROOT_ENV, str(authoring))
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{UNDECLARED.upper()}__CONFIG__ROOT", str(drafts))
    with _serving_board() as remote:
        for name, value in remote.items():
            monkeypatch.setenv(name, value)
        yield Written(authoring, drafts)


def _register_private(monkeypatch: pytest.MonkeyPatch) -> None:
    """The organization's repository registered private, as the host's rules overlay declares
    it, so `onevcs boundary check` knows its terms wherever the content names it."""
    boundary_home.answering(
        monkeypatch, public=boundary_home.FIXTURE_REPOSITORIES, private=(PRIVATE_REPOSITORY,)
    )


#: What `just plans` runs: the plan store this checkout locks, behind the wrapper that
#: establishes its board credential. Run directly rather than through the recipe, whose
#: `{{args}}` splits an argument holding a space, such as a title, into several.
PLAN_STORE = [str(REPO_ROOT / "scripts" / "plan-store.sh"), str(ONETASKGRAPH_BIN)]


def _plans(*arguments: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """One plan-store command as `just plans` runs it, as a planner or a manager does."""
    return subprocess.run(
        [*PLAN_STORE, *arguments, "--no-interactive"],
        cwd=REPO_ROOT,
        env=dict(os.environ),
        input=stdin,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )


def _project(source: str, native: str, *, private: bool = False) -> str:
    """``native`` created in ``source``, classified private as a planner creates an
    organization's plan, where ``private``; the native id the source gave it, which a board
    mints for itself."""
    classified = ["--classification", "private"] if private else []
    made = _plans(
        "--json", "project", "create", source, "--id", native, "--title", native, *classified
    )
    assert made.returncode == 0, made.stdout + made.stderr
    created = json.loads(made.stdout)
    qualified = str(created.get("id") or created["items"][0]["id"])
    return qualified.partition(":")[2]


def _files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.md") if path.is_file())


#: The destinations every artifact is written to: the private source, the public board and
#: the source declaring no visibility.
DESTINATIONS = (AUTHORING_SOURCE, BOARD, UNDECLARED)


def _rendered_document(source: str, project: str, template: str, answers: Path) -> list[str]:
    """`onepipeline template resolve <template>` piped into `just plans document create`."""
    return [
        "bash",
        "-c",
        'set -euo pipefail; onepipeline template resolve "$1" --json | '
        '"$6" "$7" document create "$2" --project "$3" --title "$4" '
        '--template-loader - --answers "$5" --no-interactive',
        "rendered",
        template,
        source,
        project,
        f"{template} for {project}",
        str(answers),
        *PLAN_STORE,
    ]


def _spike_answers(path: Path, method: str) -> Path:
    path.write_text(
        json.dumps(
            {
                "spike": "spike-intake",
                "branch": "nick/intake/spike-intake",
                "harness": "`harness.sh` at the branch's root; run `sh harness.sh`.",
                "method": method,
                "candidates": [],
                "findings": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def _fixture_write(
    write: Callable[..., object], *arguments: object, **named: object
) -> subprocess.CompletedProcess[str]:
    """One of `project_fixtures`' writers, which raise on a refused write, as an outcome."""
    try:
        write(*arguments, **named)
    except AssertionError as refused:
        return subprocess.CompletedProcess([], 1, "", str(refused))
    return subprocess.CompletedProcess([], 0, "", "")


def _ticket(path: Path, repository: str, what: str) -> Path:
    path.write_text(
        f"## What happened\n{what}\n\n## Where\n`intake/form.py`.\n\n"
        f"## Why it is out of scope\nThis node owns the reader.\n\n"
        f"## Evidence\n```\nfailed against {repository}\n```\n",
        encoding="utf-8",
    )
    return path


# llmlint: ignore-block[e2e_not_mocked] GitHub's Projects API is the one boundary doubled, for
# the reason `tests/github_board.py` gives, and the organization's tracker is a folder declared
# private, because no check may reach a Linear workspace; the recipes, the store, its layer and
# `onevcs` are the real ones.
def test_a_task_naming_the_organization_s_repository_is_written_to_its_tracker(
    organization: Written, tmp_path: Path
) -> None:
    """A plan of the organization's repository, drafted in `authoring`, copies to its tracker."""
    tracker = tmp_path / "stand-ins" / onetaskgraph_user_layer.ORGANIZATION_PLANS
    _project(AUTHORING_SOURCE, "org-plan", private=True)
    body = tmp_path / "task.md"
    body.write_text(_task(f"Change the {PRIVATE_TERM} intake form."), encoding="utf-8")
    created = _plans(
        "task",
        "create",
        AUTHORING_SOURCE,
        "--project",
        "org-plan",
        "--title",
        "feat: change the intake form",
        "--repository",
        PRIVATE_REPOSITORY,
        "--body-file",
        str(body),
    )
    assert created.returncode == 0, created.stdout + created.stderr

    copied = _plans(
        "project",
        "copy",
        f"{AUTHORING_SOURCE}:org-plan",
        "--to",
        onetaskgraph_user_layer.ORGANIZATION_PLANS,
    )

    assert copied.returncode == 0, copied.stdout + copied.stderr
    held = [path.read_text(encoding="utf-8") for path in _files(tracker / "tasks")]
    assert any(PRIVATE_REPOSITORY in text for text in held), held
    assert _on_the_board() == [], "the organization's task reached the public board"


#: The artifacts that name a repository in a field the boundary classifies by identity — a
#: task's recorded repositories — and those that name it only in their content: a bare name in
#: prose, and the full origin in a plan's budget answers.
STRUCTURED = frozenset({"task", "follow-up ticket"})
UNSTRUCTURED = frozenset({"design document", "spike report", "budget answers"})


@pytest.mark.parametrize(
    ("naming", "registered"),
    [(True, True), (True, False), (False, False)],
    ids=[
        "naming-a-registered-private-repository",
        "naming-an-unregistered-private-repository",
        "neutral",
    ],
)
def test_each_artifact_is_written_where_it_may_go_and_refused_where_it_may_not(
    organization: Written,
    tmp_path: Path,
    naming: bool,
    registered: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task, a design document, a spike report, budget answers and a follow-up ticket.

    Naming a private repository the host registers, each lands in `authoring` and is refused
    at the board and at `drafts` with nothing written there; neutral, each lands at all three.
    Naming one the host never registered, an artifact recording it as a repository is refused
    all the same, because an identity `onevcs boundary inspect` cannot answer reads `unknown`,
    which is private. One naming it only in its content is not: `onevcs boundary check` derives
    the terms it screens for from registered identities alone, so a bare name in prose has no
    term to match — a limit of term screening — and neither, under the pinned `onevcs`, does
    the identity's full origin in a plan's budget answers, which a follow-up asks it to
    classify.
    """
    if registered:
        _register_private(monkeypatch)
    repository = PRIVATE_REPOSITORY if naming else PUBLIC_REPOSITORY
    mention = PRIVATE_TERM if naming else "listing"
    outcomes: dict[tuple[str, str], subprocess.CompletedProcess[str]] = {}
    for destination in DESTINATIONS:
        project = _project(
            destination,
            f"artifacts-{destination}",
            private=naming and destination == AUTHORING_SOURCE,
        )
        body = tmp_path / f"task-{destination}.md"
        body.write_text(_task(f"Change the {mention} intake form."), encoding="utf-8")
        outcomes[("task", destination)] = _plans(
            "task",
            "create",
            destination,
            "--project",
            project,
            "--title",
            "feat: change the intake form",
            "--repository",
            repository,
            "--body-file",
            str(body),
        )
        outcomes[("design document", destination)] = _fixture_write(
            designed,
            destination,
            project,
            [],
            dict(os.environ),
            answers={"what": f"Port {mention}."},
        )
        spike = _spike_answers(tmp_path / f"spike-{destination}.json", f"Measured {mention}.")
        outcomes[("spike report", destination)] = _run(
            _rendered_document(destination, project, "spike-report", spike)
        )
        ticket = _ticket(
            tmp_path / f"ticket-{destination}.md", repository, f"The {mention} reader skips a page."
        )
        outcomes[("follow-up ticket", destination)] = _plans(
            "task",
            "create",
            destination,
            "--project",
            project,
            "--title",
            f"The {mention} reader skips a page",
            "--repository",
            repository,
            "--body-file",
            str(ticket),
        )
        outcomes[("budget answers", destination)] = _fixture_write(
            budgeted, destination, project, no_budgets([repository]), dict(os.environ)
        )

    assert {artifact for artifact, _ in outcomes} == STRUCTURED | UNSTRUCTURED, outcomes
    for (artifact, destination), outcome in outcomes.items():
        said = f"{artifact} at {destination}: {outcome.stdout}{outcome.stderr}"
        screened = registered or artifact in STRUCTURED
        if naming and destination != AUTHORING_SOURCE and screened:
            assert outcome.returncode != 0, said
            assert any(refusal in said for refusal in REFUSALS), said
        else:
            assert outcome.returncode == 0, said
    if naming and not registered:
        titles = _on_the_board()
        assert "feat: change the intake form" not in titles, titles
        assert f"The {PRIVATE_TERM} reader skips a page" not in titles, titles
    if naming and registered:
        written = [path.read_text(encoding="utf-8") for path in _files(organization.drafts)]
        assert not any(PRIVATE_TERM in text for text in written), written
        assert not any(PRIVATE_TERM in title for title in _on_the_board()), _on_the_board()
        assert any(
            PRIVATE_TERM in path.read_text(encoding="utf-8")
            for path in _files(organization.authoring)
        )
        recorded = _store("project", "show", f"{AUTHORING_SOURCE}:artifacts-{AUTHORING_SOURCE}")
        assert isinstance(recorded, Mapping)
        (held,) = recorded["items"]
        assert held["item"]["metadata"][plan_budgets.PLAN_RECORD] == no_budgets([repository])


# llmlint: ignore-end[e2e_not_mocked]
