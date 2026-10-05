"""A plan's petsinc tasks land in Hello Patient's Linear, and the plan still runs as one.

The tracked `plans` source routes every `github.com/petsinc/*` task to the `hellopatient`
source, and the store decides where a plan's home goes from that alone: into
`hellopatient` when every task routes there, and otherwise onto `plans`, with the routed
tasks in a member project of `hellopatient`. These journeys drive both shapes through the
real `just finish-plan`, `just approve-design` and `just orchestrate`:

* **shape 1**, an all-petsinc plan, whose home, tasks and design document all land in
  `hellopatient`, is approved there, and reads `Queued` before its first worker turn and
  `Done` once the run settles;
* **shape 2**, a mixed plan, whose home, design document and non-petsinc tasks land on the
  loopback `plans` board and whose petsinc task lands in a `hellopatient` member project,
  with edges both ways — one launch of the home, one run, each settlement written where its
  task lives.

**`hellopatient` is a folder of Markdown here, and that is the one substitution.** No check
may reach the production Linear workspace, so the copied checkout's own configuration
declares `hellopatient` as a `local-md` source whose status mapping reads the eight state
names the tracked Linear mapping writes — read off the tracked file through the store's
resolution, so a mapping that moved moves this stand-in with it. It is made in a copy of this
checkout because the store's environment layer merges into the file rather than replacing a
source, so a `local-md` plugin would arrive beside the Linear keys and be refused for them;
`tests/test_plan_source_roots.py` holds the tracked `hellopatient` to the Linear plugin's own
schema. GitHub's Projects API is the loopback board `tests/github_board.py` serves, and the
paid model is the stand-in every journey here uses. The recipes, the engine, the store and
its routing, the publication of the petsinc task onto its scratch origin, and every record
read back are real.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import NamedTuple, NewType

import follow_up_variables
import plan_root_variable
import pytest
import short_state
from fake_backend import (
    PROMPT_LOG_ENV,
    RUN_ON_MARKER_ENV,
    TURN_GATE_ENV,
    TURN_GATE_REACHED,
    TURN_GATE_RELEASED,
)
from github_board import LINEAR_KEY_ENV, _serving_board
from linear_stand_in import provisioned, stand_in
from nx_workspace import answering_this_checkouts_origin, copy_working_tree
from project_fixtures import budgeted, helper, no_budgets
from published_tools import ONETASKGRAPH_BIN
from scratch_identity import pooling, seeded
from waits import timeout as e2e_timeout

from orchestrator import plan_store
from orchestrator.criteria_guard import APPENDIX
from orchestrator.plan_store import QualifiedProjectId
from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")
ENGINE_BIN = REPO_ROOT / ".venv" / "bin" / "onepipeline"

#: The scripted reviewer's one answer: a pass naming no finding.
PASSING_VERDICT = json.dumps({"passes": True, "findings": []})

#: The two sources under test, by the names the tracked configuration gives them.
LINEAR = "hellopatient"
BOARD = "plans"
AUTHORING = "authoring"

#: The petsinc repository every routed task names, registered as a scratch identity so its
#: lifecycle node has somewhere to publish.
PETSINC = "github.com/petsinc/hp-api"
EXECUTION_ALIAS = "hp-api-execution"

#: The line a design-doc dispatch's task carries, which says the turn is that one.
DESIGN_TASK_MARKER = "Write the design document a person reviews the plan"
#: The line a petsinc node's task carries, which says the turn commits on its branch.
COMMIT_MARKER = "Leave one file behind on your branch, and commit it."
COMMIT_COMMANDS = [
    [
        "sh",
        "-c",
        "[ -f ROUTED.md ] || { echo routed > ROUTED.md && git add ROUTED.md "
        "&& git -c user.email=test@example.com -c user.name=ai-orchestrator-test "
        "commit -qm 'feat: record the routed change'; }",
    ]
]

#: A launching session stated rather than inherited, and what an enclosing dispatch would
#: otherwise decide for these launches.
LAUNCHING_SESSION = "e2e-linear-routed-plans"
INHERITED_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: The Linear state names the two readings are taken by, as the tracked mapping writes them.
QUEUED = "Queued"
DONE = "Done"
#: The status category each of those states is, as the tracked mapping places it.
STATE_CATEGORIES = {QUEUED: "queued", DONE: "done"}

#: How often a journey reads the store while it waits for the plan's claim.
POLL_SECONDS = 0.2

#: What the board fixture hands a journey for the tracked Linear source, which the copy's
#: folder stand-in takes none of.
LINEAR_ENVIRONMENT = (LINEAR_KEY_ENV, f"ONETASKGRAPH_SOURCES__{LINEAR.upper()}__CONFIG__ENDPOINT")
#: The store's environment layer for the tracked Linear source. A host's `.env` may set a key
#: of it, such as the team, and the layer merges that into the copy's folder stand-in, whose
#: plugin refuses every Linear key, so none is inherited from the process running the suite.
LINEAR_LAYER = f"ONETASKGRAPH_SOURCES__{LINEAR.upper()}__"

#: What a journey building a copy of this checkout reads: everything git tracks.
COPIES_THE_TRACKED_TREE = pytest.mark.reads_docs


class Routed(NamedTuple):
    """A copy of this checkout whose `hellopatient` is a folder, and that folder."""

    checkout: Path
    linear: Path


@pytest.fixture(scope="module")
def routed(tmp_path_factory: pytest.TempPathFactory) -> Routed:
    """One copy of this checkout, shared by both shapes: its stand-in and its toolchain."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("linear-routed")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    copy_working_tree(checkout)
    answering_this_checkouts_origin(checkout)
    linear = tmp_path / LINEAR
    linear.mkdir()
    # llmlint: ignore[e2e_not_mocked] A `local-md` source stands in for `hellopatient`, because no check may reach the production Linear workspace; `tests/test_plan_source_roots.py` holds the tracked source to the Linear plugin's schema.  # noqa: E501 - a directive is one line, and its reason is longer than the limit
    stand_in(checkout, LINEAR, linear)
    provisioned(checkout)
    return Routed(checkout=checkout, linear=linear)


class Bench(NamedTuple):
    """One shape's own registry, runs root, authoring root and environment."""

    environment: dict[str, str]
    authoring: Path
    tmp_path: Path
    #: What reaching the petsinc stand-in's hosted origin over git needs.
    identity: dict[str, str]


def _bench(tmp_path: Path, oneharness_bin: str, remote: Mapping[str, str]) -> Bench:
    """The environment a shape's flow runs in, with the petsinc stand-in registered."""
    # One slot and no overflow, so a session of the journey's own can hold the stand-in full.
    identity = seeded(
        tmp_path / "identity",
        execution=EXECUTION_ALIAS,
        origin=PETSINC,
        workspaces=pooling(1, overflow=0),
    )
    authoring = tmp_path / "authoring"
    authoring.mkdir()
    environment = dict(os.environ)
    # The copy provisions its own `.venv`, so this checkout's is not the active one there.
    for name in (*INHERITED_ENVIRONMENT, "VIRTUAL_ENV"):
        environment.pop(name, None)
    for name in [name for name in environment if name.startswith(LINEAR_LAYER)]:
        del environment[name]
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEVCS_HOME"] = str(identity.home)
    environment.update(identity.environment)
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment["FAKE_CODEX_ANSWERS"] = json.dumps([PASSING_VERDICT])
    environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp_path))
    environment[PROMPT_LOG_ENV] = str(tmp_path / "turns.jsonl")
    environment[plan_root_variable.name()] = str(authoring)
    environment["ONETASKGRAPH_SECRETS_FILE"] = str(tmp_path / "no-secrets.env")
    environment["ONETASKGRAPH_DEFAULT_SOURCES"] = f"{AUTHORING},{BOARD},{LINEAR}"
    root_name, plugin_name, command_name = follow_up_variables.all_names()
    environment.pop(command_name, None)
    environment[root_name] = str(tmp_path / "follow-ups")
    environment[plugin_name] = plan_store.WRITABLE_PLUGIN
    # The board fixture's Linear key and endpoint are for the tracked Linear source; the
    # copy's `hellopatient` is a folder, which takes neither.
    environment.update(
        {name: value for name, value in remote.items() if name not in LINEAR_ENVIRONMENT}
    )
    return Bench(
        environment=environment,
        authoring=authoring,
        tmp_path=tmp_path,
        identity=identity.environment,
    )


def _task(what: str) -> str:
    """A task body that carries its bar, as a planner writes one."""
    return (
        f"## What\n\n{what}\n\n"
        "## Why\n\nHello Patient's work is planned where that team tracks it.\n\n"
        "## Acceptance criteria\n\n"
        "- The change is in place and a test drives it end to end.\n"
        "- Every claim the dispatch makes about the finished work is true of the tree as it "
        "finally stands.\n\n"
        f"{(REPO_ROOT / APPENDIX).read_text(encoding='utf-8').strip()}\n"
    )


#: A plan node's id, which every task record carries as `onepipeline.id`.
NodeId = NewType("NodeId", str)
#: The opaque token `onevcs` names an open session by.
SessionToken = NewType("SessionToken", str)


class Node(NamedTuple):
    """One planned task: its node id, title, dependencies, and whether it is petsinc's."""

    id: NodeId
    title: str
    deps: tuple[NodeId, ...]
    petsinc: bool


class Status(NamedTuple):
    """One task's status: its category, and the state name its source reads it at."""

    category: str
    name: str


def _draft(bench: Bench, name: str, nodes: tuple[Node, ...]) -> str:
    """Author ``nodes`` as one plan in the authoring source, answering its qualified id.

    A petsinc node is a lifecycle node in the registered stand-in: the first one commits
    on its branch, so a worker turn happens, and any other declares `expects_no_diff`. A
    node of no repository is a direct node, which stays on `plans`.
    """
    committing = next(node.id for node in nodes if node.petsinc)
    write_plan_project(
        bench.authoring,
        {
            "schema_version": 3,
            "goal": {"text": "Ship Hello Patient's change and the library it consumes"},
            "name": name,
            "tasks": [
                {
                    "id": node.id,
                    "title": node.title,
                    **({"deps": list(node.deps)} if node.deps else {}),
                    **(
                        {"repo": PETSINC, "execution_checkout": EXECUTION_ALIAS}
                        if node.petsinc
                        else {}
                    ),
                    # A node settled with no dispatch takes no persona.
                    **(
                        {"expects_no_diff": True}
                        if node.petsinc and node.id != committing
                        else {"persona": "engineer"}
                    ),
                    "task": _task(COMMIT_MARKER if node.id == committing else "Reply with done."),
                }
                for node in nodes
            ],
        },
        native_id=name,
    )
    # Every plan carries its budgets document; this one's work needs no budget.
    budgeted(
        AUTHORING,
        name,
        no_budgets([PETSINC] if any(node.petsinc for node in nodes) else []),
        bench.environment,
    )
    return f"{AUTHORING}:{name}"


def _design_answers(nodes: tuple[Node, ...]) -> dict[str, object]:
    """What a design-doc dispatch's model would compose for the plan of ``nodes``."""
    return {
        "what": "Hello Patient's change and the library it consumes.",
        "why": "The team plans in Linear.",
        "architecture": "One library, one consumer.",
        "units": [
            {
                "name": "Change",
                "repository": "hp-api",
                "part": "",
                "summary": "The change and what it consumes.",
                "reversible": [],
                "decisions": [],
            }
        ],
        "acceptance_criteria": ["The change ships."],
        "planned_tasks": [
            {
                "task": node.title,
                "unit": "Change",
                "delivers": node.id,
                "depends_on": ", ".join(node.deps) or "none",
                "location": "the store's own location",
            }
            for node in nodes
        ],
    }


def _scripted(bench: Bench, name: str, nodes: tuple[Node, ...]) -> None:
    """Script the design-doc dispatch's store command and the petsinc worker's commit.

    The document is rendered by the pinned engine's resolve and stored by the store's own
    `document create`, run where the dispatch runs; the resolve names the plan's one
    repository when every task names it, as the writer's own task says to.
    """
    answers = bench.tmp_path / f"{name}-design.json"
    answers.write_text(json.dumps(_design_answers(nodes)), encoding="utf-8")
    repository = PETSINC if all(node.petsinc for node in nodes) else ""
    keyed = bench.tmp_path / "commands.json"
    keyed.write_text(
        json.dumps(
            {
                DESIGN_TASK_MARKER: [
                    [
                        "bash",
                        "-c",
                        '"$1" template resolve design-doc ${8:+--repository "$8"} --json'
                        ' | "$2" document create "$3" --project "$4" --title "$5" --id "$6"'
                        ' --template-loader - --answers "$7" --no-interactive',
                        "store-the-document",
                        str(ENGINE_BIN),
                        str(ONETASKGRAPH_BIN),
                        AUTHORING,
                        name,
                        f"Design: {name}",
                        f"{name}-design",
                        str(answers),
                        repository,
                    ]
                ],
                COMMIT_MARKER: COMMIT_COMMANDS,
            }
        ),
        encoding="utf-8",
    )
    bench.environment[RUN_ON_MARKER_ENV] = str(keyed)


def _just(
    routed: Routed, bench: Bench, *args: str, seconds: float = 600
) -> subprocess.CompletedProcess[str]:
    """One real recipe, run in the copied checkout."""
    return subprocess.run(
        ["just", *args],
        cwd=routed.checkout,
        env=bench.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _brief(bench: Bench, project: str) -> Path:
    """A manager's brief naming the plan the flow finishes."""
    brief = bench.tmp_path / "brief.md"
    brief.write_text(
        "## What\nShip Hello Patient's change.\n\n"
        f"Plan project: {project}\n\n"
        "## Why\nThe team plans in Linear.\n\n"
        "## Acceptance criteria\n- The change ships.\n",
        encoding="utf-8",
    )
    return brief


HOME_LINE = re.compile(r"its home is (\S+);")


class Finished(NamedTuple):
    """The home `just finish-plan` reported, and everything it said."""

    home: QualifiedProjectId
    reported: str


def _finished(routed: Routed, bench: Bench, name: str, nodes: tuple[Node, ...]) -> Finished:
    """Draft, review, document and copy the plan through `just finish-plan`."""
    project = _draft(bench, name, nodes)
    _scripted(bench, name, nodes)
    finished = _just(routed, bench, "finish-plan", str(_brief(bench, project)), "--name", name)
    _just(routed, bench, "stop", f"{name}-design", seconds=60)
    reported = finished.stdout + finished.stderr
    assert finished.returncode == 0, f"the tail failed:\n{reported}"
    homes = HOME_LINE.findall(reported)
    assert len(homes) == 1, f"the tail reported no single home for {project}:\n{reported}"
    return Finished(home=QualifiedProjectId(homes[0]), reported=reported)


def _items(
    routed: Routed, bench: Bench, home: QualifiedProjectId
) -> dict[str, Mapping[str, object]]:
    """Every task of ``home`` and its members, by node id, as `just plans` reads them."""
    listed = _just(
        routed,
        bench,
        "plans",
        "task",
        "list",
        "--project",
        home,
        "--members",
        "--json",
        seconds=120,
    )
    assert listed.returncode == 0, listed.stdout + listed.stderr
    return {
        str(held["item"]["metadata"]["onepipeline.id"]): held
        for held in json.loads(listed.stdout)["items"]
    }


def _status(held: Mapping[str, object]) -> Status:
    """One task's status category and the state name its source reads it at."""
    item = held["item"]
    assert isinstance(item, dict), held
    status = item["status"]
    assert isinstance(status, dict), held
    return Status(category=str(status["category"]), name=str(status["name"]))


def _reads_state(status: Status, state: str) -> bool:
    """Whether a task's ``status`` is the Linear state ``state``, as its source names it.

    The name is compared without case because the stand-in folder writes a state name as its
    lower-case key; Linear itself answers the name as the mapping spells it.
    """
    return (status.category, status.name.casefold()) == (
        STATE_CATEGORIES[state],
        state.casefold(),
    )


def _reads(held: Mapping[str, object], state: str) -> bool:
    """Whether ``held`` reads at the Linear state ``state``."""
    return _reads_state(_status(held), state)


def _onevcs(bench: Bench, *arguments: str) -> subprocess.CompletedProcess[str]:
    """The pinned `onevcs`, against the shape's scratch registry."""
    return subprocess.run(
        ["uv", "run", "onevcs", *arguments],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEVCS_HOME": bench.environment["ONEVCS_HOME"], **bench.identity},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )


def _held_queued(
    routed: Routed,
    bench: Bench,
    home: QualifiedProjectId,
    launch: subprocess.Popen[str],
    gate: Path,
) -> dict[str, Status]:
    """Every task's state once all read `Queued`, or as they stand at the deadline.

    Read while the petsinc stand-in's one pool slot is held by a session of this journey's
    own, so the engine cannot place the node it would dispatch first and holds it under
    `workspace-wait`: the run has claimed its plan and no node has been dispatched, which is
    the moment before any worker turn. Holding the slot is what makes that moment last long
    enough for a store read rather than a race one would lose.
    """
    deadline = time.monotonic() + e2e_timeout(180)
    while True:
        read = {node: _status(held) for node, held in _items(routed, bench, home).items()}
        queued = all(_reads_state(status, QUEUED) for status in read.values())
        if queued or launch.poll() is not None or time.monotonic() > deadline:
            assert not (gate / TURN_GATE_REACHED).exists(), (
                f"a worker turn started while the only slot was held: {read}"
            )
            return read
        time.sleep(POLL_SECONDS)


class Launched(NamedTuple):
    """One attached launch of a plan's home, and what was read around it."""

    #: The finished `just orchestrate`.
    launch: subprocess.CompletedProcess[str]
    #: Each task's status, by node id, read while no node could be dispatched.
    before: dict[str, Status]
    #: The run's own journal.
    journal: Path


def _approved_and_settled(
    routed: Routed, bench: Bench, home: QualifiedProjectId, watching: bool
) -> Launched:
    """Approve ``home`` through the recipe, launch it, and answer what its tasks read.

    With ``watching``, the launch is made while the petsinc stand-in's one slot is held, and
    every task is read through the store before that slot is given back.
    """
    approved = _just(routed, bench, "approve-design", home)
    assert approved.returncode == 0, approved.stdout + approved.stderr
    assert "recorded the approval of" in approved.stdout, approved.stdout
    runs = bench.tmp_path / "launched"
    bench.environment["ONEPIPELINE_RUNS_DIR"] = str(runs)
    gate = bench.tmp_path / "turn-gate"
    gate.mkdir()
    bench.environment[TURN_GATE_ENV] = str(gate)
    holder: SessionToken | None = None
    if watching:
        opened = _onevcs(bench, "session", "open", EXECUTION_ALIAS)
        assert opened.returncode == 0, opened.stdout + opened.stderr
        holder = SessionToken(str(json.loads(opened.stdout.strip())["token"]))
    launch = subprocess.Popen(  # noqa: S603 - this repository's own recipe
        ["just", "orchestrate", home, "--dag-graph", "off"],
        cwd=routed.checkout,
        env=bench.environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    before: dict[str, Status] = {}
    try:
        if holder is not None:
            before = _held_queued(routed, bench, home, launch, gate)
    finally:
        if holder is not None:
            closed = _onevcs(bench, "session", "close", holder)
            assert closed.returncode == 0, closed.stdout + closed.stderr
        (gate / TURN_GATE_RELEASED).touch()
        stdout, stderr = launch.communicate(timeout=e2e_timeout(900))
    finished = subprocess.CompletedProcess(launch.args, launch.returncode, stdout, stderr)
    (run,) = [held for held in runs.iterdir() if held.is_dir()]
    return Launched(launch=finished, before=before, journal=run / "events.jsonl")


def _settlement(launch: subprocess.CompletedProcess[str]) -> object:
    """The settlement the attached launch's last line reports."""
    assert launch.returncode == 0, launch.stdout + launch.stderr
    return json.loads(launch.stdout.strip().splitlines()[-1])["settlement"]


def _journal(path: Path) -> list[Mapping[str, object]]:
    """The run's own journal, read in order."""
    # No view reports the moment each node was dispatched and settled, which is what says
    # the cross-source edges were honoured, so the run's own journal is read.
    # llmlint: ignore[tests_mirror_real_usage] no view reports dispatch order; see above
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _moments(journal: list[Mapping[str, object]], kind: str) -> dict[str, int]:
    """Each node's first record of ``kind``, by its position in the journal."""
    seen: dict[str, int] = {}
    for index, record in enumerate(journal):
        labels = record.get("labels")
        node = labels.get("node") if isinstance(labels, dict) else None
        if record.get("kind") == kind and isinstance(node, str):
            seen.setdefault(node, index)
    return seen


@pytest.fixture
def gated(tmp_path: Path, oneharness_bin: str) -> Iterator[tuple[Bench, Mapping[str, str]]]:
    """A served loopback `plans` board and one shape's bench over it."""
    with _serving_board() as remote:
        yield _bench(tmp_path, oneharness_bin, remote), remote


ALL_PETSINC = (
    Node(NodeId("migrate"), "feat: migrate the patient record", (), petsinc=True),
    Node(
        NodeId("backfill"),
        "feat: backfill the migrated records",
        (NodeId("migrate"),),
        petsinc=True,
    ),
)


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]  # noqa: E501
# tests/plan_tooling/AGENTS.md states this project's split: `reads_docs` routes a journey that
# builds a copy of this checkout to `plan-tooling:test-docs`, keyed on the whole workspace because
# copying the tracked tree reads all of it. `test_plan_flow_e2e.py`'s default-board flow copies
# and provisions a checkout behind the same marker for the same reason.
# llmlint: ignore-block[e2e_not_mocked] Three boundaries are doubled and no more: the paid
# provider process, GitHub's Projects API (the loopback board, because the live board is shared
# by every run of this repository), and Linear (the folder the copy declares `hellopatient` as,
# because no check may reach the production workspace). The recipes, engine, store, routing,
# publication and every record read back are real.
@COPIES_THE_TRACKED_TREE
@pytest.mark.xdist_group("linear-routed")
def test_an_all_petsinc_plan_lands_whole_in_linear_and_runs_from_there(
    routed: Routed, gated: tuple[Bench, Mapping[str, str]]
) -> None:
    """Every task petsinc's: the home, its tasks and its design document are Linear's.

    Copied by `just finish-plan` onto `plans`, as every plan is, and placed by the store's
    routing alone. Approved there by `just approve-design`, and launched by `just orchestrate`
    of the home id: before any worker takes a turn every task reads `Queued`, and once the run
    settles every task reads `Done`.
    """
    bench, _ = gated
    name = f"test-{os.getpid()}-all-petsinc"
    home, reported = _finished(routed, bench, name, ALL_PETSINC)

    assert home.startswith(f"{LINEAR}:"), f"the home landed at {home}, not in {LINEAR}"
    for said in (
        f"plan-locations: {LINEAR} holds the plan at {routed.linear}",
        f"plan-locations: {LINEAR} holds its design document at {routed.linear}",
        f"`just approve-design {home}`",
        f"`just orchestrate {home}`",
    ):
        assert said in reported, f"the tail never said {said!r}:\n{reported}"
    assert "its tasks routed to" not in reported, reported
    landed = {str(path.relative_to(routed.linear)) for path in routed.linear.rglob("*.md")}
    native = home.partition(":")[2]
    assert f"projects/{native}.md" in landed, landed
    assert any(path.startswith("documents/") for path in landed), (
        f"the design document did not land in {LINEAR}: {landed}"
    )
    stored = _items(routed, bench, home)
    assert set(stored) == {node.id for node in ALL_PETSINC}, stored
    assert {str(held["id"]).partition(":")[0] for held in stored.values()} == {LINEAR}, stored

    launch, before, journal = _approved_and_settled(routed, bench, home, watching=True)

    assert _settlement(launch) == "complete", launch.stdout + launch.stderr
    assert {node: _reads_state(status, QUEUED) for node, status in before.items()} == {
        node.id: True for node in ALL_PETSINC
    }, f"before the first worker turn the tasks read {before}"
    assert set(_moments(_journal(journal), "node-settled")) == {node.id for node in ALL_PETSINC}
    settled = _items(routed, bench, home)
    assert {node: _reads(held, DONE) for node, held in settled.items()} == {
        node.id: True for node in ALL_PETSINC
    }, {node: _status(held) for node, held in settled.items()}
    # llmlint: ignore-end[e2e_not_mocked]
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]  # noqa: E501


MIXED = (
    Node(NodeId("library"), "feat: add the record client to the library", (), petsinc=False),
    Node(
        NodeId("consumer"),
        "feat: consume the record client",
        (NodeId("library"),),
        petsinc=True,
    ),
    Node(
        NodeId("rollout"),
        "feat: roll the client out behind its flag",
        (NodeId("consumer"),),
        petsinc=False,
    ),
)


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]  # noqa: E501
# The same placement and reason as the journey above.
# llmlint: ignore-block[e2e_not_mocked] The same three doubled boundaries as the journey above.
@COPIES_THE_TRACKED_TREE
@pytest.mark.xdist_group("linear-routed")
def test_a_mixed_plan_runs_as_one_from_its_board_home_and_its_linear_member(
    routed: Routed, gated: tuple[Bench, Mapping[str, str]]
) -> None:
    """A plan spanning both orgs is one home, one member, one run, and edges both ways.

    The home lands on the `plans` board with the design document and the non-petsinc tasks;
    the petsinc task lands in the home's `hellopatient` member project. One launch of the home
    runs every node under one run id, each after the nodes it depends on across the two
    stores, and each settlement is written to the task's own item.
    """
    bench, _ = gated
    name = f"test-{os.getpid()}-mixed"
    home, reported = _finished(routed, bench, name, MIXED)

    assert home.startswith(f"{BOARD}:"), f"the home landed at {home}, not on {BOARD}"
    stored = _items(routed, bench, home)
    placed = {node: str(held["id"]).partition(":")[0] for node, held in stored.items()}
    assert placed == {"library": BOARD, "consumer": LINEAR, "rollout": BOARD}, placed
    shown = _just(routed, bench, "plans", "project", "show", home, "--json", seconds=120)
    assert shown.returncode == 0, shown.stdout + shown.stderr
    members = json.loads(shown.stdout)["items"][0]["item"]["metadata"][plan_store.MEMBERS_KEY]
    assert [str(member).partition(":")[0] for member in members] == [LINEAR], members
    for said in (
        f"plan-locations: {BOARD} holds the plan at https://github.com/",
        f"plan-locations: {BOARD} holds its design document at https://github.com/",
        f"plan-locations: its tasks routed to {LINEAR} are in {members[0]}",
        f"`just orchestrate {home}`",
    ):
        assert said in reported, f"the tail never said {said!r}:\n{reported}"
    documents = _just(
        routed, bench, "plans", "document", "list", "--project", home, "--json", seconds=120
    )
    assert documents.returncode == 0, documents.stdout + documents.stderr
    # The home holds the plan's two documents: the one it is read as, and its budgets.
    templates = sorted(
        (held["item"]["metadata"].get("onetaskgraph.template") or {}).get("template", "")
        for held in json.loads(documents.stdout)["items"]
    )
    assert templates == ["onepipeline:design-doc", "onepipeline:plan-budgets"], documents.stdout

    launch, _, journal = _approved_and_settled(routed, bench, home, watching=False)

    assert _settlement(launch) == "complete", launch.stdout + launch.stderr
    records = _journal(journal)
    dispatched, settled = _moments(records, "node-dispatched"), _moments(records, "node-settled")
    for node in MIXED:
        for dependency in node.deps:
            assert settled[dependency] < dispatched[node.id], (
                f"{node.id} was dispatched before {dependency}, which it depends on across "
                f"the two stores, had settled: {dispatched} {settled}"
            )
    finished = _items(routed, bench, home)
    assert {node: _reads(held, DONE) for node, held in finished.items()} == {
        node.id: True for node in MIXED
    }, {node: _status(held) for node, held in finished.items()}
    assert {node: str(held["id"]).partition(":")[0] for node, held in finished.items()} == placed
    # llmlint: ignore-end[e2e_not_mocked]
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]  # noqa: E501
