"""A plan naming a private repository lives on the host's private GitHub board, all of it.

A host's onetaskgraph user layer — installed under this module's own configuration home —
declares a private GitHub Projects source shaped as the tracked example's `private-plans`,
and the example's route that sends a synthetic private owner's private work from `plans` to
it; the store's public boundary is on, as the tracked configuration switches it. Under that
boundary any item that classifies private moves the plan's home: the home, its design
document and every task go to the private board, and a task naming only a public repository
is seen on `plans` only as the store's neutral view under a `Public work` project. On a source
declared private the adopted store creates a new issue whose item names one repository that
does not read private in the repository of the project issue it is filed under, while the item
still names its public repository; it reads the board's `public` field and each repository's
visibility once per store copy, before that copy's first write — and `just copy-plan` makes two,
the project with its tasks and then its documents. The `onevcs` state root every command
reads registers the private owner's repository private and the public one public, so the
boundary classifies from rules this module wrote.

These journeys drive that through this checkout's real recipes — `just review-plan`, `just
copy-plan`, `just plans`, `just approve-design` — and the gate a launch runs:

* a mixed plan copies its home, document and both tasks onto the private board, every issue in
  a private repository, its public task's view alone onto `plans`, with no write sent there and
  no issue it holds naming anything private, and no request of any kind naming the private
  repository or source; a re-copy creates nothing; and its design document is approved on the
  private home, which the launch gate then reads as approved;
* with the route removed, a task naming the private repository would land on `plans`, and is
  refused before either board is written.

**Two boundaries are doubled**: GitHub's wire is the loopback stand-in `tests/github_board.py`
serves — `plans` and the private board, each with its own request log — because no check may
reach a live board; and the paid model, for the review turn, is the provider stand-in every
journey here uses. The recipes, the store and its boundary, `onevcs` and every record read back
are real. Every owner, repository and source is synthetic.
"""

from __future__ import annotations

import bisect
import copy
import json
import os
import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NamedTuple

import boundary_home
import onetaskgraph_user_layer
import pytest
from github_board import (
    BOARD,
    PRIVATE_BOARD,
    PRIVATE_LIBRARY_REPOSITORY,
    PRIVATE_REPOSITORIES,
    PRIVATE_STORE_REPOSITORY,
    PUBLIC_SAMPLE_REPOSITORY,
    RestRequest,
    _audit_follow_up_calls,
    _GitHubFixture,
    _GraphQLRequest,
    _hosted,
    _Operation,
    _plan_environment,
    _PrivateBoardFixture,
    _serving_board,
    _serving_private_board,
)
from project_fixtures import budgeted, designed, no_budgets, reviewed
from waits import timeout as e2e_timeout

from orchestrator import design_approval
from orchestrator.criteria_guard import APPENDIX
from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

#: The sources under test, by the names the tracked configuration and the example layer give.
AUTHORING = "authoring"
PLANS = "plans"
PRIVATE_PLANS = onetaskgraph_user_layer.PRIVATE_PLANS

#: The private owner's repository a private task names, and the public one a public task names,
#: as the normalized origins a task record holds.
PRIVATE_REPOSITORY = _hosted(PRIVATE_LIBRARY_REPOSITORY)
PUBLIC_REPOSITORY = _hosted(PUBLIC_SAMPLE_REPOSITORY)

#: The two tasks of the mixed plan, by id: neither depends on the other, so the public task's
#: view carries no edge to the private one.
PUBLIC_TASK = "open-client"
PRIVATE_TASK = "sealed-intake"
PUBLIC_TITLE = "feat: add the record client to the open library"
PRIVATE_TITLE = "feat: take records in through the sealed intake"

#: The neutral project the store files the public views on one public source under.
PUBLIC_WORK = "Public work"

#: Every operation that creates an issue, alone or in an aliased batch.
CREATES = frozenset({_Operation.CREATE_ISSUE, _Operation.CREATE_ISSUES})


# llmlint: ignore[suppressions_justified] A user layer is the store's open YAML document.
def _layer(*, routed: bool) -> dict[str, Any]:
    """The host's user layer: the example's `private-plans` source and, if ``routed``, the
    example's route sending the private owner's private work from `plans` to it.

    The example's other sources are left out, because a copy onto `plans` builds every source
    its routes name and the organization's Linear trackers are not this journey's subject.
    """
    example = onetaskgraph_user_layer.example()
    # llmlint: ignore[suppressions_justified] Each value is one source's open configuration.
    sources: dict[str, Any] = {PRIVATE_PLANS: copy.deepcopy(example["sources"][PRIVATE_PLANS])}
    if routed:
        sources[PLANS] = {
            "routes": [
                route
                for route in example["sources"][PLANS]["routes"]
                if route["to"] == PRIVATE_PLANS
            ]
        }
        assert sources[PLANS]["routes"], "the example layer routes nothing to private-plans"
    return {"sources": sources}


class Bench(NamedTuple):
    """One journey's environment, its authoring root, and where its store launches are kept."""

    environment: dict[str, str]
    authoring: Path
    #: Each store process this checkout's Python launched, with when (`tests/github_board.py`).
    trace: Path


def _variable(source: str, key: str) -> str:
    """The store's environment spelling of one of ``source``'s configuration keys."""
    return f"ONETASKGRAPH_SOURCES__{source.upper().replace('-', '_')}__CONFIG__{key}"


@contextmanager
def _bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, routed: bool) -> Iterator[Bench]:
    """Both boards served, the layer installed, the registry written, and the environment.

    The environment is also this process's for the journey, because the review fixture reads
    it from there.
    """
    home = boundary_home.registered_home(
        tmp_path / "registry", public=[PUBLIC_REPOSITORY], private=[PRIVATE_REPOSITORY]
    )
    config_home = onetaskgraph_user_layer.installed(tmp_path / "config-home", _layer(routed=routed))
    authoring = tmp_path / "authoring"
    authoring.mkdir()
    number = int(
        onetaskgraph_user_layer.example()["sources"][PRIVATE_PLANS]["config"]["project_number"]
    )
    with _serving_board() as remote, _serving_private_board(number) as private:
        environment = boundary_home.environment(_plan_environment(authoring), home)
        environment[onetaskgraph_user_layer.XDG_CONFIG_HOME] = str(config_home)
        environment.update(remote)
        environment[_variable(PRIVATE_PLANS, "ENDPOINT")] = private
        for source in (PLANS, PRIVATE_PLANS):
            environment[_variable(source, "PACING__MIN_MUTATION_INTERVAL_MS")] = "0"
        # A plan is drafted in the gitignored authoring root, which is this host's own: a host
        # switching the boundary on declares it private, as it declares every store of its own.
        environment["ONETASKGRAPH_SOURCES__AUTHORING__VISIBILITY"] = "private"
        # A read naming no source asks the default ones, the private board among them.
        environment["ONETASKGRAPH_DEFAULT_SOURCES"] = f"{AUTHORING},{PLANS},{PRIVATE_PLANS}"
        environment["UV_NO_SYNC"] = "1"
        trace = _audit_follow_up_calls(tmp_path, environment)
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        yield Bench(environment=environment, authoring=authoring, trace=trace)


def _task(what: str) -> str:
    """A task body that carries its bar, as a planner writes one."""
    return (
        f"## What\n\n{what}\n\n"
        "## Why\n\nThe work is planned where its owner tracks it.\n\n"
        "## Acceptance criteria\n\n"
        "- The change is in place and a test drives it end to end.\n"
        "- Every claim the dispatch makes about the finished work is true of the tree as it "
        "finally stands.\n\n"
        f"{(REPO_ROOT / APPENDIX).read_text(encoding='utf-8').strip()}\n"
    )


class PlannedTask(NamedTuple):
    """One task of a drafted plan: its node id, its title, and the one repository it names."""

    id: str
    title: str
    repository: str


def _drafted(bench: Bench, name: str, tasks: Sequence[PlannedTask]) -> str:
    """Author ``tasks`` as one reviewed plan with a design document in `authoring`, answering
    its qualified id."""
    write_plan_project(
        bench.authoring,
        {
            "schema_version": 3,
            "goal": {"text": "Ship the intake and the client it consumes"},
            "name": name,
            "tasks": [
                {
                    "id": task.id,
                    "title": task.title,
                    "repo": task.repository,
                    "persona": "engineer",
                    "task": _task("Reply with done."),
                    "unit": "Library",
                }
                for task in tasks
            ],
        },
        native_id=name,
    )
    budgeted(
        AUTHORING, name, no_budgets(sorted({task.repository for task in tasks})), bench.environment
    )
    designed(
        AUTHORING,
        name,
        [
            {
                "task": task.title,
                "unit": "Library",
                "delivers": task.id,
                "depends_on": "none",
                "location": "the store's own location",
            }
            for task in tasks
        ],
        bench.environment,
    )
    return reviewed(f"{AUTHORING}:{name}")


def _run(bench: Bench, *command: str, seconds: float = 300) -> subprocess.CompletedProcess[str]:
    """One command of this checkout — a recipe or a script it runs — in ``bench``'s environment."""
    return subprocess.run(  # noqa: S603 - this checkout's recipes and the scripts they run
        list(command),
        cwd=REPO_ROOT,
        env=bench.environment,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=e2e_timeout(seconds),
        check=False,
    )


# llmlint: ignore[suppressions_justified] The store CLI owns this open JSON schema.
def _json(completed: subprocess.CompletedProcess[str]) -> Any:
    """What a successful store read printed, as the CLI's JSON document."""
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout)


def _gate(bench: Bench, home: str) -> subprocess.CompletedProcess[str]:
    """The design-approval gate `just orchestrate` runs before it dispatches anything, over
    ``home``, as `scripts/onepipeline.sh` runs it."""
    return _run(bench, "./scripts/plan-store.sh", "uv", "run", "orchestrator-launch-gate", home)


def _sent(requests: Sequence[_GraphQLRequest], rest: Sequence[RestRequest]) -> list[str]:
    """Every request body one board was sent, GraphQL and REST alike."""
    return [
        *(
            json.dumps({"query": request.query, "variables": request.variables})
            for request in requests
        ),
        *(json.dumps([request.method, request.path, request.body]) for request in rest),
    ]


def _mutations(requests: Sequence[_GraphQLRequest]) -> list[_GraphQLRequest]:
    return [request for request in requests if request.is_mutation]


def _clear_logs(bench: Bench) -> None:
    """Forget every request both boards took and every store launch, before a step."""
    for handler in (_GitHubFixture, _PrivateBoardFixture):
        handler.requests.clear()
        handler.rest_requests.clear()
    bench.trace.write_text("", encoding="utf-8")


class StoreCopy(NamedTuple):
    """One store copy a step launched, and what the boards took while it ran."""

    #: `project` or `document`: which of the store's copy verbs it was.
    verb: str
    #: The private board's reads of whether its Project is public.
    board_reads: list[_GraphQLRequest]
    #: The private board's REST reads of a repository's visibility.
    repository_reads: list[RestRequest]
    #: The writes either board took.
    writes: list[_GraphQLRequest]


def _store_copies(trace: Path) -> list[StoreCopy]:
    """Each store copy the step launched, in order, with the requests sent while it ran.

    The store runs one process at a time, so a request belongs to the last store launch
    before the stand-in received it.
    """
    launches = [
        entry
        for entry in map(json.loads, trace.read_text(encoding="utf-8").splitlines())
        if entry["kind"] == "store"
    ]
    starts = [float(entry["at"]) for entry in launches]
    windows: dict[int, StoreCopy] = {}
    for index, entry in enumerate(launches):
        argv = [str(word) for word in entry["argv"]]
        if argv[1:2] in (["project"], ["document"]) and argv[2:3] == ["copy"]:
            windows[index] = StoreCopy(argv[1], [], [], [])

    def held(at: float) -> StoreCopy | None:
        return windows.get(bisect.bisect_right(starts, at) - 1)

    for request in _PrivateBoardFixture.requests:
        copied = held(request.received)
        if copied is not None and request.operation is _Operation.PROJECT_VISIBILITY:
            copied.board_reads.append(request)
    for read in _PrivateBoardFixture.rest_requests:
        copied = held(read.received)
        if copied is not None and read.method == "GET":
            copied.repository_reads.append(read)
    for request in _mutations([*_GitHubFixture.requests, *_PrivateBoardFixture.requests]):
        copied = held(request.received)
        if copied is not None:
            copied.writes.append(request)
    return [windows[index] for index in sorted(windows)]


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] The task that added these
# journeys requires them in the project holding the Linear routed-plans journey, so their graph
# edges stay the plan-copy recipes and the stand-ins that project already depends on; they run
# from this checkout rather than a copy of it, so they stay in its keyed `test` target.
# llmlint: ignore-block[e2e_not_mocked] Two boundaries are doubled and no more: GitHub's wire,
# as the loopback stand-in serving `plans` and the private board, because no check may reach a
# live board; and the paid provider process for the review turn. The recipes, the store and its
# boundary, `onevcs` and every record read back are real.
def test_a_mixed_plan_lives_on_the_private_github_board_and_shows_its_public_task_on_plans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The private board holds the plan whole; `plans` holds one neutral view; nothing private
    reaches `plans`; a re-copy creates nothing; and the design is approved on the private home.
    """
    with _bench(tmp_path, monkeypatch, routed=True) as bench:
        name = f"private-home-{os.getpid()}"
        project = _drafted(
            bench,
            name,
            [
                PlannedTask(PUBLIC_TASK, PUBLIC_TITLE, PUBLIC_REPOSITORY),
                PlannedTask(PRIVATE_TASK, PRIVATE_TITLE, PRIVATE_REPOSITORY),
            ],
        )
        _clear_logs(bench)

        copied = _run(bench, "just", "copy-plan", project)

        said = copied.stdout + copied.stderr
        assert copied.returncode == 0, said
        # Where the copy put the home, as the store reports each record it copied.
        reported = {
            str(entry["source"]): str(entry["destination"])
            for entry in map(json.loads, copied.stdout.splitlines())
            if entry.get("destination")
        }
        home = reported[project]
        assert home.startswith(f"{PRIVATE_PLANS}:"), said
        listed = _json(
            _run(bench, "just", "plans", "task", "list", "--project", home, "--members", "--json")
        )
        stored = {str(held["item"]["metadata"]["onepipeline.id"]): held for held in listed["items"]}
        assert sorted(stored) == [PUBLIC_TASK, PRIVATE_TASK], listed
        assert {str(held["id"]).partition(":")[0] for held in stored.values()} == {PRIVATE_PLANS}
        documents = _json(
            _run(bench, "just", "plans", "document", "list", "--project", home, "--json")
        )
        assert [str(held["id"]).partition(":")[0] for held in documents["items"]] == [
            PRIVATE_PLANS
        ], documents
        # Every issue made for the private board is in a repository GitHub answers private: the
        # home, its document and the public task's canonical item in the board's own, the
        # private task in the private repository it names.
        created = PRIVATE_BOARD.created
        assert len(created) == 4, [issue.title for issue in created]
        assert all(issue.repository in PRIVATE_REPOSITORIES for issue in created), [
            (issue.title, str(issue.repository)) for issue in created
        ]
        public_item = _json(
            _run(bench, "just", "plans", "task", "show", str(stored[PUBLIC_TASK]["id"]), "--json")
        )
        (shown_public,) = public_item["items"]
        assert shown_public["item"]["repositories"] == [PUBLIC_REPOSITORY], public_item
        # On `plans`, one neutral project and the public task's view, both filed in the public
        # repository the view names.
        assert [(issue.title, issue.repository) for issue in BOARD.created] == [
            (PUBLIC_WORK, PUBLIC_SAMPLE_REPOSITORY),
            (PUBLIC_TITLE, PUBLIC_SAMPLE_REPOSITORY),
        ], [(issue.title, str(issue.repository)) for issue in BOARD.created]
        # No request of any kind sent to `plans` names the private repository or the private
        # source; and no write sent there, nor any issue it holds, names those, the plan's id or
        # the private task's id. (The store's origin lookup on `plans`, a read, searches by each
        # record's authoring origin, which carries the last two: a store matter, not this host's.)
        sent = _sent(_GitHubFixture.requests, _GitHubFixture.rest_requests)
        written = [
            *_sent(_mutations(_GitHubFixture.requests), []),
            *(f"{issue.title}\n{issue.body}\n{issue.origin}" for issue in BOARD.issues),
        ]
        for private, bodies in (
            (PRIVATE_LIBRARY_REPOSITORY.name, sent),
            (str(PRIVATE_LIBRARY_REPOSITORY), sent),
            (PRIVATE_PLANS, sent),
            (name, written),
            (PRIVATE_TASK, written),
        ):
            leaked = [body for body in bodies if private in body]
            assert leaked == [], (private, leaked)
        # The recipe copies through two store invocations — the project with its tasks, then its
        # documents. Each reads the private board's visibility and each repository's at most
        # once, every one of those reads answered before that invocation's first write to either
        # board. The released store reads per invocation rather than per recipe copy, so the
        # document copy reads again after the project copy's writes (a store matter).
        copies = _store_copies(bench.trace)
        assert [store_copy.verb for store_copy in copies] == ["project", "document"], copies
        assert len(copies[0].board_reads) == 1, copies[0]
        for store_copy in copies:
            assert len(store_copy.board_reads) <= 1, (store_copy.verb, store_copy.board_reads)
            paths = [read.path for read in store_copy.repository_reads]
            assert sorted(paths) == sorted(set(paths)), (store_copy.verb, paths)
            assert store_copy.writes, store_copy.verb
            first_write = min(request.received for request in store_copy.writes)
            answered = [
                *(request.answered.at for request in store_copy.board_reads),
                *(read.answered_at for read in store_copy.repository_reads),
            ]
            assert all(at is not None and at < first_write for at in answered), (
                store_copy,
                answered,
            )
        assert {
            f"/repos/{repository}"
            for repository in (
                PRIVATE_LIBRARY_REPOSITORY,
                PUBLIC_SAMPLE_REPOSITORY,
                PRIVATE_STORE_REPOSITORY,
            )
        } == {read.path for read in copies[0].repository_reads}, copies[0]

        _clear_logs(bench)
        recopied = _run(bench, "just", "copy-plan", project)

        assert recopied.returncode == 0, recopied.stdout + recopied.stderr
        for handler in (_GitHubFixture, _PrivateBoardFixture):
            creates = [request for request in handler.requests if request.operation in CREATES]
            assert creates == [], (handler.__name__, creates)
        assert len(PRIVATE_BOARD.created) == 4 and len(BOARD.created) == 2

        unapproved = _gate(bench, home)
        assert unapproved.returncode == 1, unapproved.stdout + unapproved.stderr
        approved = _run(bench, "just", "approve-design", home)

        assert approved.returncode == 0, approved.stdout + approved.stderr
        (document,) = _json(
            _run(bench, "just", "plans", "document", "list", "--project", home, "--json")
        )["items"]
        shown = _json(
            _run(bench, "just", "plans", "document", "show", str(document["id"]), "--json")
        )
        assert design_approval.RECORD_KEY in json.dumps(shown), shown
        gated = _gate(bench, home)
        assert gated.returncode == 0, gated.stdout + gated.stderr
        assert "carries no approval" not in gated.stderr, gated.stderr


def test_a_private_task_with_no_route_is_refused_before_either_board_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the route removed, a task naming the private repository would land on `plans`, and
    the store's boundary refuses it there before either board takes a write."""
    with _bench(tmp_path, monkeypatch, routed=False) as bench:
        name = f"unrouted-{os.getpid()}"
        project = _drafted(
            bench, name, [PlannedTask(PRIVATE_TASK, PRIVATE_TITLE, PRIVATE_REPOSITORY)]
        )
        _clear_logs(bench)

        copied = _run(bench, "just", "copy-plan", project)

        said = copied.stdout + copied.stderr
        assert copied.returncode != 0, said
        # The task makes the plan private, and the store refuses it at the public board.
        assert f"{project} is private, and source {PLANS} is not declared private" in said, said
        assert _mutations(_GitHubFixture.requests) == []
        assert _mutations(_PrivateBoardFixture.requests) == []
        assert BOARD.created == [] and PRIVATE_BOARD.created == []
    # llmlint: ignore-end[e2e_not_mocked]
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
