"""A landing that closes an issue moves its card to `Done`, on every board this host declares.

The engine hands the plan store the issues each landing closes, and the store finds them on
whichever source `onetaskgraph.yaml` declares that holds one — the `followups` board's cards
and a `plans` board task alike — so nothing here names a source, a flag or a setting for it,
and nothing reads a board to look for closures. These journeys hold that through this host's
own recipes: a landing a run makes, one each of `just publish-branch`, `just repo-recover` and
`just integrate` makes, and one a person makes after the run settled, which the run meets
when it is adopted.

Everything is real — the recipes, the engine, its linked `onevcs` and plan store, git, and
this repository's own `onetaskgraph.yaml` — with its `plans` and `followups` sources pointed
at the loopback board (`tests/github_board.py`) and the example user layer's Linear sources
at a loopback that answers nothing, and only the paid model doubled. The identity is a
scratch one filed under this repository's own origin, so a closing line names an issue the
loopback holds; a change request opened for it is answered by `tests/e2e/fake_gh.py`.
"""

# llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] `xdist_group` selects an
# xdist worker under this suite's `--dist loadgroup`, not a test tier.
# llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] The task that added these
# journeys places them with the landing journeys, in this directory-owned project keyed on the
# landing recipes and scripts they drive; a project of their own would re-key the whole
# launch-and-publication path for five journeys.
# llmlint: ignore-file[shell_test_tiers_stay_split] A pytest journey over the real recipes,
# beside every other landing journey in this directory, not a shell suite.

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar, NamedTuple

import fake_gh
import onetaskgraph_user_layer
import pytest
import short_state
from fake_backend import PROMPT_LOG_ENV, RUN_ON_MARKER_ENV
from github_board import (
    BOARD,
    CONFIGURED_REPOSITORY,
    DONE,
    FOLLOWUPS_BOARD,
    LINEAR_FOLLOWUPS_ENDPOINT_ENV,
    LINEAR_KEY_ENV,
    LINEAR_PLANS_ENDPOINT_ENV,
    _Board,
    _BoardField,
    _BoardItemId,
    _FollowUpsFixture,
    _GitHubFixture,
    _GraphQLRequest,
    _Issue,
    _IssueNodeId,
    _Operation,
    _serving_both_boards,
)
from project_fixtures import helper, project_from_plan
from scratch_identity import Identity, seeded
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The stand-ins for the paid model, and the guard refusing every other paid identity.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
FAKE_GH = helper("fake_gh.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: The identity every journey lands on: this repository's own origin, so a bare `#N` in a
#: closing line names an issue of the repository both boards file their issues in.
ORIGIN = f"github.com/{CONFIGURED_REPOSITORY}"
EXECUTION_ALIAS = "execution"
NODE_ID = "close"

#: The follow-up ticket a landing closes, accepted at `Todo`, and the `plans` board task it
#: closes beside it, at `In Progress`. Numbered apart from each board's own seeded issues and
#: from each other, as two issues of one repository are.
TICKET = 101
TASK = 201
#: A second accepted ticket nothing closes, which has to read exactly as it did.
UNTOUCHED_TICKET = 102

#: The phrase that tells the stand-in worker this is the turn that commits.
COMMIT_MARKER = "Leave one file behind on your branch, and commit it."

#: A launching session the journeys state rather than inherit, and every name a launcher
#: identity reaches `scripts/onepipeline.sh` through.
LAUNCHING_SESSION = "e2e-landing-closes-board-cards"
LAUNCHER_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: The `plans` and `followups` settings an operator's shell could carry, removed so the
#: committed document is what the store applies — only the endpoints are pointed elsewhere.
SOURCE_OVERRIDES = ("ONETASKGRAPH_SOURCES__PLANS__", "ONETASKGRAPH_SOURCES__FOLLOWUPS__")

#: The local sources the committed document roots inside this checkout, each the suite does
#: not already point elsewhere pointed at a directory of the journey's own, so the store can
#: build every source it declares.
LOCAL_ROOTS = ("AUTHORING", "FOLLOW_UP_RUNS", "TEST_FIXTURES", "DRAFTS", "EXAMPLES")

#: How long a closure is given to reach the board once its landing is recorded.
PATIENCE_SECONDS = 240


# llmlint: ignore-block[e2e_not_mocked] Linear's API is a boundary no test may reach: this
# answers every request with a refusal and records it, so the journeys can say the closure
# write asked nothing of the organization's tracker.
class _LinearFixture(BaseHTTPRequestHandler):
    """Every request a Linear source sends, recorded and refused."""

    requests: ClassVar[list[str]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
        length = int(self.headers.get("Content-Length") or 0)
        _LinearFixture.requests.append(self.rfile.read(length).decode("utf-8", "replace"))
        body = json.dumps({"errors": [{"message": "the Linear loopback answers nothing"}]})
        self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def _serving_linear() -> Iterator[str]:
    """Serve the Linear loopback, yielding its endpoint."""
    _LinearFixture.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LinearFixture)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/graphql"
    finally:
        server.shutdown()
        thread.join(timeout=e2e_timeout(60))
        server.server_close()


# llmlint: ignore-end[e2e_not_mocked]


#: The one Linear credential name `scripts/dispatch-env.sh` hands a launch's driver. The
#: example layer's own name is on no table there, so a driver would build its Linear
#: sources without a key and record every closure `failed`; the journeys' layer names this
#: one instead, as the host whose layer the table was written for does.
DRIVER_LINEAR_KEY_ENV = "HELLOPATIENT_LINEAR_API_KEY"


def _host_layer() -> dict[str, object]:
    """The example user layer, its private stores left out and its Linear key the driver's."""
    layer = onetaskgraph_user_layer.without_private_stores()
    sources = layer["sources"]
    assert isinstance(sources, dict)
    for source in sources.values():
        config = source.get("config") if isinstance(source, dict) else None
        if isinstance(config, dict) and config.get("api_key_env") == LINEAR_KEY_ENV:
            config["api_key_env"] = DRIVER_LINEAR_KEY_ENV
    return layer


class Card(NamedTuple):
    """What a person reads off one board item: its option, and its issue's state."""

    status: str
    state: str
    reason: str | None


def _planted(board: _Board, number: int, status: str, title: str) -> _Issue:
    """An accepted item of ``board`` at ``status``, whose issue is ``number`` and open."""
    issue = _Issue(
        item_id=_BoardItemId(f"PVTI_{number}"),
        content_id=_IssueNodeId(f"I_{number}"),
        number=number,
        title=title,
        body="An item a landing may close.",
        status=status,
    )
    issue.board = board
    board.issues.append(issue)
    return issue


def _cards() -> dict[tuple[int, int], Card]:
    """Every item of both boards, by its board's number and its issue's number."""
    return {
        (board.number, issue.number): Card(issue.status, issue.state, issue.state_reason)
        for board in (BOARD, FOLLOWUPS_BOARD)
        for issue in board.issues
    }


def _board_requests() -> list[_GraphQLRequest]:
    return [*_GitHubFixture.requests, *_FollowUpsFixture.requests]


#: A read of a board's own `items` connection: the board walk no closure may make.
ITEMS_READ = re.compile(r"(?<![A-Za-z])items\(")


class Bench(NamedTuple):
    """One journey's identity, environment, and the items it plants on both boards."""

    root: Path
    identity: Identity
    environment: dict[str, str]
    ticket: _Issue
    task: _Issue
    untouched: _Issue
    before: dict[tuple[int, int], Card]


@contextmanager
def _bench(root: Path, oneharness_bin: str, *, reviewed: bool = False) -> Iterator[Bench]:
    """Serve both boards and the Linear loopback, plant the items, and seed the identity."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    # Public, as this repository is: the store writes an item of a private repository only
    # to a source declared private, which neither board is.
    identity = seeded(
        root, execution=EXECUTION_ALIAS, origin=ORIGIN, visibility="public", reviewed=reviewed
    )
    with _serving_both_boards() as remote, _serving_linear() as linear:
        ticket = _planted(FOLLOWUPS_BOARD, TICKET, "Todo", "An accepted ticket a landing closes")
        untouched = _planted(FOLLOWUPS_BOARD, UNTOUCHED_TICKET, "Todo", "An accepted ticket")
        task = _planted(BOARD, TASK, "In Progress", "A plan task a landing closes")
        environment = dict(os.environ)
        for name in list(environment):
            if name in LAUNCHER_ENVIRONMENT or name.startswith(SOURCE_OVERRIDES):
                del environment[name]
        environment.update(remote)
        environment.update(identity.environment)
        environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
        environment["ONEVCS_HOME"] = str(identity.home)
        environment["ONEPIPELINE_RUNS_DIR"] = str(root / "runs")
        environment["XDG_STATE_HOME"] = str(short_state.state_home(root))
        for source in LOCAL_ROOTS:
            # A root the suite already points somewhere — the one a fixture's plan is stored
            # in among them — is left where it is.
            named = f"ONETASKGRAPH_SOURCES__{source}__CONFIG__ROOT"
            if named not in environment:
                local = root / "sources" / source.lower()
                local.mkdir(parents=True)
                environment[named] = str(local)
        # The host's own layer as the example states it, its Linear sources at the loopback
        # above and its private stores left out, since those name a board no test may reach.
        environment[onetaskgraph_user_layer.XDG_CONFIG_HOME] = str(
            onetaskgraph_user_layer.installed(root / "xdg-config", _host_layer())
        )
        environment[DRIVER_LINEAR_KEY_ENV] = "fixture-linear-key"
        environment[LINEAR_PLANS_ENDPOINT_ENV] = linear
        environment[LINEAR_FOLLOWUPS_ENDPOINT_ENV] = linear
        # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
        environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
        environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
        environment[PROMPT_LOG_ENV] = str(root / "turns.jsonl")
        # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
        environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
        environment["FAKE_CODEX_ANSWERS"] = json.dumps(
            [json.dumps({"body": "A change whose landing closes a ticket."})]
        )
        # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
        environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
        yield Bench(root, identity, environment, ticket, task, untouched, _cards())
        assert _LinearFixture.requests == [], (
            f"the closure write reached the Linear loopback: {_LinearFixture.requests}"
        )


def _just(bench: Bench, *arguments: str, seconds: float = 600) -> subprocess.CompletedProcess[str]:
    """Run one real recipe from this checkout, under the bench's environment."""
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=bench.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _until(
    what: str,
    ready: Callable[[], bool],
    explained: Callable[[], str],
    *,
    seconds: float = PATIENCE_SECONDS,
) -> None:
    """Poll ``ready`` until it answers true, or fail naming what never arrived, and why."""
    limit = deadline(seconds)
    while time.monotonic() < limit:
        if ready():
            return
        time.sleep(1.0)
    raise AssertionError(f"{what} never arrived within {seconds}s:\n{explained()}")


#: The records a landing and its closure leave on a run's journal.
LANDING_KINDS = frozenset(
    {"merge-completed", "change-merged", "node-landed", "node-settled", "landing-closures"}
)


def _explained(bench: Bench, run: str) -> Callable[[], str]:
    """What the run journalled about its landings, and what both boards were asked."""

    def explain() -> str:
        landed = [
            f"{record.get('kind')}: {json.dumps(record.get('payload'))}"
            for record in _journal(bench, run)
            if record.get("kind") in LANDING_KINDS
        ]
        asked = [request.operation.value for request in _board_requests()]
        return "\n".join([*landed, f"board requests: {asked}", f"cards: {_cards()}"])

    return explain


def _commits(bench: Bench, message: str) -> None:
    """Have the stand-in worker commit one file, under ``message``, on its own branch."""
    keyed = bench.root / "commands.json"
    written = bench.root / "commit-message.txt"
    written.write_text(message, encoding="utf-8")
    command = (
        "[ -f CLOSED.md ] || { echo closed > CLOSED.md && git add CLOSED.md "
        "&& git -c user.email=test@example.com -c user.name=ai-orchestrator-test "
        f"commit -q -F {shlex.quote(str(written))}; }}"
    )
    keyed.write_text(json.dumps({COMMIT_MARKER: [["sh", "-c", command]]}), encoding="utf-8")
    bench.environment[RUN_ON_MARKER_ENV] = str(keyed)


def _project(bench: Bench, name: str) -> str:
    """A one-node lifecycle plan against the scratch identity, stored as a local project.

    ``name`` is the plan's, which is the id the run it launches is minted under.
    """
    plan = bench.root / f"{name}.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "goal": {"text": "Land a change that closes the issues it names"},
                "name": name,
                "tasks": [
                    {
                        "id": NODE_ID,
                        # The identity's normalized origin, which the record's own
                        # `repositories` holds, as every plan this host writes names it.
                        "repo": ORIGIN,
                        "execution_checkout": EXECUTION_ALIAS,
                        "persona": "engineer",
                        "task": (
                            f"## What\n{COMMIT_MARKER}\n\n"
                            "## Why\nIts landing closes a ticket and a plan task.\n\n"
                            "## Acceptance criteria\n- The dispatch starts and reports.\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return project_from_plan(plan)


def _journal(bench: Bench, run: str) -> list[dict[str, object]]:
    # llmlint: ignore[tests_mirror_real_usage] No view prints a closure record's fields.
    path = bench.root / "runs" / run / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _closures(bench: Bench, run: str) -> list[dict[str, object]]:
    """The payloads of every `landing-closures` record the run journalled."""
    return [
        record["payload"]
        for record in _journal(bench, run)
        if record.get("kind") == "landing-closures"
    ]


def _launch_record(bench: Bench, run: str) -> str:
    # llmlint: ignore[tests_mirror_real_usage] What a launch recorded is read nowhere else.
    return (bench.root / "runs" / run / "launch.json").read_text(encoding="utf-8")


def _keys(document: object) -> Iterator[str]:
    """Every key of a JSON document, at any depth."""
    match document:
        case dict():
            for key, value in document.items():
                yield str(key)
                yield from _keys(value)
        case list():
            for value in document:
                yield from _keys(value)
        case _:
            return


def _status_writes(item: _Issue) -> list[_GraphQLRequest]:
    """Every Status write either board took for ``item``'s row."""
    return [
        request
        for request in _board_requests()
        if request.operation in (_Operation.UPDATE_FIELD, _Operation.UPDATE_FIELD_WHOLE)
        and request.input_value("itemId") == item.item_id
        and request.input_value("fieldId") == _BoardField.STATUS
    ]


def _assert_closed(bench: Bench, closed: list[_Issue], run: str | None) -> None:
    """Each of ``closed`` is done on its board and its issue closed; nothing else moved."""
    after = _cards()
    for issue in closed:
        key = (issue.held_by.number, issue.number)
        assert after[key] == Card(DONE.name, "CLOSED", "COMPLETED"), (
            f"#{issue.number} reads {after[key]} on board {issue.held_by.number}, not done"
        )
        assert len(_status_writes(issue)) == 1, (
            f"#{issue.number} took {len(_status_writes(issue))} Status writes, not one"
        )
    moved = {
        key: (bench.before[key], card)
        for key, card in after.items()
        if card != bench.before[key]
        and key not in {(issue.held_by.number, issue.number) for issue in closed}
    }
    assert moved == {}, f"items no landing closed moved: {moved}"
    walked = [
        request.operation for request in _board_requests() if ITEMS_READ.search(request.query)
    ]
    assert walked == [], f"a board's items were read: {walked}"
    if run is not None:
        keys = sorted(_keys(json.loads(_launch_record(bench, run))))
        assert not [key for key in keys if "clos" in key.lower()], (
            f"the run's launch record carries a closure setting: {keys}"
        )


@pytest.mark.xdist_group("landing-closes-board-cards")
def test_a_runs_local_direct_landing_moves_its_ticket_and_plan_task_to_done(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A run's landing closing a ticket and a plan task moves both cards to `Done`.

    The ticket is accepted at `Todo`, which only a person moves, and the task is `In
    Progress`: the engine's closure write moves both anyway, because the work their issues
    describe landed, and closes both issues as completed.

    The closing lines name their repository: on the adopted onevcs a run's landing reports
    its identity as the lender checkout's path, so a bare `#N` there resolves to no
    repository and closes nothing (a follow-up against onevcs), where `just publish-branch`
    below resolves the same identity's bare `#N`.
    """
    with _bench(tmp_path, oneharness_bin) as bench:
        _commits(
            bench,
            f"feat: close the ticket\n\nCloses {CONFIGURED_REPOSITORY}#{TICKET}\n"
            f"Closes {CONFIGURED_REPOSITORY}#{TASK}\n",
        )
        run = "landing-closes"
        project = _project(bench, run)

        launched = _just(bench, "orchestrate", project)

        assert launched.returncode == 0, (
            f"the run did not settle:\n{launched.stdout}{launched.stderr}"
        )
        _until(
            "both cards at Done",
            lambda: (
                _cards()[(FOLLOWUPS_BOARD.number, TICKET)].status == DONE.name
                and _cards()[(BOARD.number, TASK)].status == DONE.name
            ),
            _explained(bench, run),
        )
        _assert_closed(bench, [bench.ticket, bench.task], run)
        (recorded,) = _closures(bench, run)
        assert recorded["outcome"] == "projected", recorded
        assert sorted(str(issue) for issue in recorded["closes"]) == sorted(
            f"{CONFIGURED_REPOSITORY}#{number}" for number in (TICKET, TASK)
        ), recorded


@pytest.mark.xdist_group("landing-closes-board-cards")
def test_publish_branch_landing_moves_its_ticket_to_done(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """`just publish-branch` closes what its landing closes, with nothing named for it."""
    with _bench(tmp_path, oneharness_bin) as bench:
        checkout = bench.identity.publication
        branch = "finished/closes-a-ticket"
        git = ["git", "-c", "user.email=test@example.com", "-c", "user.name=ai-orchestrator-test"]
        steps = (
            ["checkout", "-q", "-b", branch],
            ["add", "SHIPPED.md"],
            ["commit", "-q", "-m", f"feat: close the ticket\n\nCloses #{TICKET}"],
            ["checkout", "-q", "main"],
        )
        (checkout / "SHIPPED.md").write_text("shipped\n", encoding="utf-8")
        for step in steps:
            done = subprocess.run(
                [*git, *step], cwd=checkout, capture_output=True, text=True, check=False
            )
            assert done.returncode == 0, done.stderr

        published = _just(bench, "publish-branch", branch, "--repo", str(checkout))

        assert published.returncode == 0, published.stdout + published.stderr
        _assert_closed(bench, [bench.ticket], None)
        assert bench.task.status == "In Progress", "the plan task no landing closed moved"


GIT_AS_TEST = ["git", "-c", "user.email=test@example.com", "-c", "user.name=ai-orchestrator-test"]


def _git(bench: Bench, checkout: Path, *step: str) -> None:
    """One git step in ``checkout``, reaching the identity's origin as the bench does."""
    done = subprocess.run(
        [*GIT_AS_TEST, *step],
        cwd=checkout,
        env=bench.environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def _branch(bench: Bench, branch: str, commits: list[tuple[str, str]]) -> Path:
    """Cut ``branch`` in the publication checkout, one commit per (file, message); back to main."""
    checkout = bench.identity.publication
    _git(bench, checkout, "checkout", "-q", "-b", branch)
    for name, message in commits:
        (checkout / name).write_text(f"{name}\n", encoding="utf-8")
        _git(bench, checkout, "add", name)
        _git(bench, checkout, "commit", "-q", "-m", message)
    _git(bench, checkout, "checkout", "-q", "main")
    return checkout


@pytest.mark.xdist_group("landing-closes-board-cards")
def test_repo_recover_landing_moves_its_ticket_to_done(tmp_path: Path, oneharness_bin: str) -> None:
    """`just repo-recover` over a preserved branch closes what its landing closes.

    The branch carries an unattested incomplete-step marker, the state only the recovery
    verb lands, and the identity a passing tracked `pre-push` hook, because `recover`
    refuses a merge path that verifies nothing.
    """
    with _bench(tmp_path, oneharness_bin) as bench:
        checkout = bench.identity.publication
        hooks = checkout / ".githooks"
        hooks.mkdir()
        (hooks / "pre-push").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        (hooks / "pre-push").chmod(0o755)
        _git(bench, checkout, "add", ".githooks")
        _git(bench, checkout, "commit", "-q", "-m", "chore: verify a push")
        _git(bench, checkout, "push", "-q", "origin", "main")
        _git(bench, checkout, "config", "core.hooksPath", ".githooks")
        branch = "claude/interrupted-closing-work"
        _branch(
            bench,
            branch,
            [
                ("SHIPPED.md", f"feat: close the ticket\n\nCloses #{TICKET}"),
                (
                    "HALF.md",
                    "feat: land half the work (incomplete step)\n\n"
                    "Orchestrator-Status: incomplete\n",
                ),
            ],
        )

        recovered = _just(bench, "repo-recover", branch, "--repo", str(checkout))

        assert recovered.returncode == 0, recovered.stdout + recovered.stderr
        _assert_closed(bench, [bench.ticket], None)
        assert bench.task.status == "In Progress", "the plan task no landing closed moved"


@pytest.mark.xdist_group("landing-closes-board-cards")
def test_integrate_landing_moves_its_ticket_to_done(tmp_path: Path, oneharness_bin: str) -> None:
    """`just integrate` merging a finished branch closes what its landing closes.

    `onevcs integrate` reads the checkout it is run inside, so the recipe runs inside the
    identity's publication checkout, naming this repository's justfile, with `uv` pointed
    back here. The identity stands in for this repository, the one the integrate recipe here
    lands, whose own tree carries this host's `onetaskgraph.yaml`; so the throwaway base
    carries that file unchanged. It has to: the engine's integrate hands the store no
    sources of the launching host's, as `publish-branch` does, so the store reads them from
    the integrated checkout, and a tree carrying no store configuration closes nothing —
    the accepted lag, until the follow-up "just integrate in another repository's checkout
    closes no follow-up card" is fixed upstream.
    """
    with _bench(tmp_path, oneharness_bin) as bench:
        checkout = bench.identity.publication
        shutil.copyfile(REPO_ROOT / "onetaskgraph.yaml", checkout / "onetaskgraph.yaml")
        _git(bench, checkout, "add", "onetaskgraph.yaml")
        _git(bench, checkout, "commit", "-q", "-m", "chore: carry the plan store's sources")
        _git(bench, checkout, "push", "-q", "origin", "main")
        branch = "finished/integrated-closing-work"
        checkout = _branch(
            bench, branch, [("SHIPPED.md", f"feat: close the ticket\n\nCloses #{TICKET}")]
        )

        integrated = subprocess.run(
            [
                "just",
                "--justfile",
                str(REPO_ROOT / "justfile"),
                "--working-directory",
                str(checkout),
                "integrate",
                branch,
                "--push",
            ],
            cwd=checkout,
            env={**bench.environment, "UV_PROJECT": str(REPO_ROOT)},
            text=True,
            capture_output=True,
            timeout=e2e_timeout(600),
            check=False,
            stdin=subprocess.DEVNULL,
        )

        assert integrated.returncode == 0, integrated.stdout + integrated.stderr
        assert f"{branch}: merged" in integrated.stdout, integrated.stdout
        _assert_closed(bench, [bench.ticket], None)
        assert bench.task.status == "In Progress", "the plan task no landing closed moved"


@pytest.mark.xdist_group("landing-closes-board-cards")
def test_an_adoption_moves_the_card_a_late_merge_closed(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A change request a person merged after its run settled closes its card on adoption.

    The identity keeps its change request as a draft for review, so the run settles with
    nothing landed and nothing closed. Once it is stopped, a person merges the change
    request closing the ticket's issue; adopting the run meets that merge and moves the card
    exactly once.
    """
    with _bench(tmp_path, oneharness_bin, reviewed=True) as bench:
        repository = str(CONFIGURED_REPOSITORY)
        state = tmp_path / "gh-state"
        # The branch is still pushed with real git into the bare origin.
        # llmlint: ignore[e2e_not_mocked] Only the remote host's decisioning, at onevcs's seam.
        bench.environment["ONEVCS_GH"] = str(FAKE_GH)
        bench.environment[fake_gh.STATE_ENV] = str(state)
        bench.environment[fake_gh.CALLS_ENV] = str(tmp_path / "gh-calls.jsonl")
        origin = tmp_path / "origin.git"
        bench.environment[fake_gh.ORIGINS_ENV] = json.dumps({repository: str(origin)})
        _commits(bench, "feat: close the ticket once a person merges it")
        run = "late-merge-closes"
        project = _project(bench, run)

        launched = _just(bench, "orchestrate", project)

        assert launched.returncode == 0, (
            f"the run did not settle:\n{launched.stdout}{launched.stderr}"
        )
        (opened,) = fake_gh.records(state, repository).glob("pr-*.json")
        change = json.loads(opened.read_text(encoding="utf-8"))
        assert change["isDraft"] is True and change["state"] == "OPEN", change
        assert _closures(bench, run) == [], "a draft kept for review closed something"
        assert bench.ticket.status == "Todo", "the ticket moved before anything landed"

        stopped = _just(bench, "stop", run, seconds=120)
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        fake_gh.merged_by_person(
            state,
            repository,
            origin,
            number=change["number"],
            closes=[f"{repository}#{TICKET}"],
        )
        adopted = _just(bench, "orchestrate", "--adopt", run)

        assert adopted.returncode == 0, adopted.stdout + adopted.stderr
        _until(
            "the ticket at Done",
            lambda: _cards()[(FOLLOWUPS_BOARD.number, TICKET)].status == DONE.name,
            _explained(bench, run),
        )
        _assert_closed(bench, [bench.ticket], run)
        assert bench.task.status == "In Progress", "the plan task no landing closed moved"
