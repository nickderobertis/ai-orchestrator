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
at a loopback answering only the screening read, as a tracker holding nothing, and only the
paid model doubled. The identity is a
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
    CANCELLED,
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
# records every request, answers the store's screening read with an empty tracker and refuses
# everything else, so the journeys can say the closure write asked nothing else of the
# organization's tracker.
class _LinearFixture(BaseHTTPRequestHandler):
    """Every request a Linear source sends, recorded and refused."""

    requests: ClassVar[list[str]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
        length = int(self.headers.get("Content-Length") or 0)
        request = self.rfile.read(length).decode("utf-8", "replace")
        _LinearFixture.requests.append(request)
        if _screening_search(request):
            # The organization's tracker holds no copy of any board item.
            empty = {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}
            body = json.dumps({"data": {"issues": empty}})
            self.send_response(200)
        else:
            body = json.dumps({"errors": [{"message": "the Linear loopback answers nothing"}]})
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, format: str, *args: object) -> None:
        return


#: What a screened write to a public board asks each private source: the items whose copy
#: link names the board item written, by a description search for its qualified id.
SCREENED_ITEM = re.compile(r'"plans:[A-Za-z0-9_]+"')


def _screening_search(request: str) -> bool:
    """Whether ``request`` is the store's screening read, and nothing else.

    With the public write boundary on, every write to a board item first asks each source
    declared private whether it holds a copy of that item. That read is the only one this
    loopback answers, and it asks nothing about a card: its one condition beyond the team
    and project scope is the description naming the board item.
    """
    sent = json.loads(request)
    query = sent.get("query", "")
    if not query.lstrip().startswith("query") or "mutation" in query:
        return False
    conditions = sent.get("variables", {}).get("filter", {}).get("and", [])
    searched = [
        condition["description"]["contains"]
        for condition in conditions
        if isinstance(condition, dict) and "description" in condition
    ]
    scoped = all(
        set(condition) <= {"team", "project", "description"}
        for condition in conditions
        if isinstance(condition, dict)
    )
    return scoped and len(searched) == 1 and SCREENED_ITEM.fullmatch(searched[0]) is not None


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
        # The store's screening of each board write is the one read it may send there.
        unscreened = [one for one in _LinearFixture.requests if not _screening_search(one)]
        assert unscreened == [], f"the closure write reached the Linear loopback: {unscreened}"


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


#: The `plans` board's issues the reconcile journey plants: two plan homes and a task, each
#: numbered apart from the bench's own and from each other.
NOT_PLANNED_HOME = 301
COMPLETED_HOME = 302
COMPLETED_TASK = 303


def _home(board: _Board, number: int, status: str, state_reason: str) -> _Issue:
    """A plan's home on ``board`` at ``status``, its issue closed for ``state_reason``.

    A board issue is a project when it has sub-issues, so the home carries one, open, which
    the reconcile has to leave as it is.
    """
    home = _planted(board, number, status, f"A plan homed at #{number}")
    home.sub_issues = 1
    home.state, home.state_reason = "CLOSED", state_reason
    member = _planted(board, number + 100, "Todo", f"A task of the plan at #{number}")
    member.parent_id = home.content_id
    return home


@pytest.mark.xdist_group("landing-closes-board-cards")
def test_the_on_demand_reconcile_moves_home_cards_with_their_issues_closure(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """`just plans task status reconcile --source plans` reaches a plan's home card too.

    A home whose issue a person closed is the lag no landing writes, and the reconcile a
    person runs on demand is what catches it: a home closed as not planned reads
    `Cancelled`, a home and a task closed as completed read `Done`, and only the cards move,
    never an issue's state or reason. Nothing else on either board moves.
    """
    with _bench(tmp_path, oneharness_bin) as bench:
        not_planned = _home(BOARD, NOT_PLANNED_HOME, "In Progress", "NOT_PLANNED")
        completed = _home(BOARD, COMPLETED_HOME, "Todo", "COMPLETED")
        task = _planted(BOARD, COMPLETED_TASK, "Todo", "A plan task closed by hand")
        task.state, task.state_reason = "CLOSED", "COMPLETED"
        before = _cards()

        reconciled = _just(bench, "plans", "task", "status", "reconcile", "--source", "plans")

        assert reconciled.returncode == 0, reconciled.stdout + reconciled.stderr
        after = _cards()
        expected = {
            (BOARD.number, NOT_PLANNED_HOME): Card(CANCELLED.name, "CLOSED", "NOT_PLANNED"),
            (BOARD.number, COMPLETED_HOME): Card(DONE.name, "CLOSED", "COMPLETED"),
            (BOARD.number, COMPLETED_TASK): Card(DONE.name, "CLOSED", "COMPLETED"),
        }
        assert {key: after[key] for key in expected} == expected, reconciled.stdout
        for issue in (not_planned, completed, task):
            assert len(_status_writes(issue)) == 1, (
                f"#{issue.number} took {len(_status_writes(issue))} Status writes, not one"
            )
        moved = {
            key: (before[key], card)
            for key, card in after.items()
            if card != before[key] and key not in expected
        }
        assert moved == {}, f"items the reconcile had no closure for moved: {moved}"
        walked = [
            (request.operation, request.variables.get("filter"))
            for request in _board_requests()
            if ITEMS_READ.search(request.query)
        ]
        assert walked == [(_Operation.CLOSED_ITEMS, "is:closed")], (
            f"the reconcile read a board's items other than through its closed filter: {walked}"
        )


class Homed(NamedTuple):
    """A plan copied onto the `plans` board and approved there, its home moved to `In Progress`."""

    project: str
    run: str
    home: _Issue
    task: _Issue
    #: How many board requests setting the plan up spent, so the run's own are those after.
    asked: int


def _homed(bench: Bench, name: str) -> Homed:
    """Copy the one-node plan and its design document onto the `plans` board, as `just
    finish-plan` does, approve it there, and move its home to `In Progress`.

    The home is the project issue the copy created on the board, and the task its one
    sub-issue. `In Progress` is where a person leaves a plan whose run is under way, and it
    is set through the store's own project status verb, so a `Done` afterwards is the
    engine's write and not the copy's.
    """
    local = _project(bench, name)
    copied = _just(bench, "plans", "project", "copy", local, "--to", "plans", "--json")
    assert copied.returncode == 0, copied.stdout + copied.stderr
    # The fixture source is private, so its copies carry no origin: the copy's own report
    # names where each of its items landed.
    landed = {
        str(entry["source"]): str(entry["destination"])
        for entry in json.loads(copied.stdout)["items"]
    }
    project = landed[local]
    document = _just(
        bench, "plans", "document", "copy", f"{local}-design", "--to", "plans", "--json"
    )
    assert document.returncode == 0, document.stdout + document.stderr
    approved = _just(bench, "approve-design", project, seconds=300)
    assert approved.returncode == 0, approved.stdout + approved.stderr
    (home,) = [issue for issue in BOARD.issues_created_and_kept() if _is(issue, project)]
    (task,) = [
        issue
        for issue in BOARD.issues_created_and_kept()
        if _is(issue, landed[f"{local}/{NODE_ID}"])
    ]
    assert task.parent_id == home.content_id, (home, task)
    moved = _just(bench, "plans", "project", "status", "set", project, "in-progress")
    assert moved.returncode == 0, moved.stdout + moved.stderr
    assert home.status == "In Progress", home
    return Homed(project, name, home, task, len(_board_requests()))


def _is(issue: _Issue, qualified: str) -> bool:
    """Whether ``issue`` is the board item the store names ``qualified``."""
    return f"plans:{issue.content_id}" == qualified


def _home_records(bench: Bench, run: str) -> list[dict[str, object]]:
    """The payloads of every `home-status` record the run journalled."""
    return [
        record["payload"] for record in _journal(bench, run) if record.get("kind") == "home-status"
    ]


@pytest.mark.xdist_group("landing-closes-board-cards")
def test_a_run_settling_with_every_node_done_moves_its_plan_home_to_done(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A plan's own card on the `plans` board reads `Done` once its run finishes.

    The plan is launched from its board copy, so the run's home is that project's issue,
    which a person had moved to `In Progress`. Its one node lands on a `local-direct`
    identity with a commit closing nothing, so the only closure is the engine's own write to
    the home, through the store, once every node is done: the home's card reads `Done` and
    its issue is closed as completed. Nothing on this host names that write — no flag,
    setting or variable reaches the launch — and nothing reads a board's items for it.
    """
    with _bench(tmp_path, oneharness_bin) as bench:
        _commits(bench, "feat: land the plan's one change")
        homed = _homed(bench, "home-closes")
        before = _cards()

        launched = _just(bench, "orchestrate", homed.project)

        assert launched.returncode == 0, (
            f"the run did not settle:\n{launched.stdout}{launched.stderr}"
        )
        home_key = (BOARD.number, homed.home.number)
        _until(
            "the plan's home at Done",
            lambda: _cards()[home_key].status == DONE.name,
            _explained(bench, homed.run),
        )
        after = _cards()
        assert after[home_key] == Card(DONE.name, "CLOSED", "COMPLETED"), after[home_key]
        task_key = (BOARD.number, homed.task.number)
        assert after[task_key].status == DONE.name, (
            f"the plan's task reads {after[task_key]}, not as its settlement wrote it"
        )
        moved = {
            key: (before[key], card)
            for key, card in after.items()
            if card != before[key] and key not in {home_key, task_key}
        }
        assert moved == {}, f"items outside the plan moved: {moved}"
        walked = [
            request.operation
            for request in _board_requests()[homed.asked :]
            if ITEMS_READ.search(request.query)
        ]
        assert walked == [], f"the run read a board's items: {walked}"
        (recorded,) = _home_records(bench, homed.run)
        assert (recorded["project"], recorded["outcome"]) == (homed.project, "written"), recorded
        keys = sorted(_keys(json.loads(_launch_record(bench, homed.run))))
        assert not [key for key in keys if re.search(r"clos|home", key, re.IGNORECASE)], (
            f"the run's launch record carries a closure or home setting: {keys}"
        )


@pytest.mark.xdist_group("landing-closes-board-cards")
def test_a_run_whose_node_fails_leaves_its_plan_home_as_it_was(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The same plan whose node fails ends with its home's card and issue untouched.

    The stand-in worker commits nothing, so the lifecycle node, asked for a change, settles
    failed with its branch level with its base, and the run ends without reaching the
    success ending: the engine owes the home nothing, and the card stays at `In Progress`
    with its issue open.
    """
    with _bench(tmp_path, oneharness_bin) as bench:
        homed = _homed(bench, "home-stays")
        before = _cards()

        launched = _just(bench, "orchestrate", homed.project)

        settled = [
            record["payload"]
            for record in _journal(bench, homed.run)
            if record.get("kind") == "node-settled"
        ]
        assert settled and all(payload.get("status") == "failed" for payload in settled), (
            f"the node did not fail:\n{settled}\n{launched.stdout}{launched.stderr}"
        )
        home_key = (BOARD.number, homed.home.number)
        assert _cards()[home_key] == before[home_key] == Card("In Progress", "OPEN", None)
        assert _home_records(bench, homed.run) == [], "a failed run wrote its home"
