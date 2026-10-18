"""A plan's petsinc tasks land in Hello Patient's Linear, and the plan stays one plan.

The tracked `plans` source routes every `github.com/petsinc/*` task to the `hellopatient`
source, and the store decides where a plan's home goes from that alone: into
`hellopatient` when every task routes there, and otherwise onto `plans`, with the routed
tasks in a member project of `hellopatient`. These journeys drive both shapes through the
real `just finish-plan`:

* **shape 1**, an all-petsinc plan, whose home, tasks and design document all land in
  `hellopatient`;
* **shape 2**, a mixed plan, whose home, design document and non-petsinc tasks land on the
  loopback `plans` board and whose petsinc task lands in a `hellopatient` member project.

None launches the plan: the copy runs on this checkout's install, and a launch strips the
`PYTHONPATH` that points the copy's `orchestrator` at its own configuration, so a launch
from the copy would read this checkout's `hellopatient`, the real Linear workspace.

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
its routing, and every record read back are real.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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
)
from github_board import LINEAR_KEY_ENV, _serving_board
from linear_stand_in import stand_in
from nx_workspace import (
    answering_this_checkouts_origin,
    copy_working_tree,
    share_this_checkouts_install,
)
from project_fixtures import budgeted, helper, no_budgets
from published_tools import ONETASKGRAPH_BIN
from scratch_identity import Identity
from scratch_identity import seeded as seeded_identity
from waits import timeout as e2e_timeout

from orchestrator import plan_store
from orchestrator.criteria_guard import APPENDIX
from orchestrator.plan_store import QualifiedProjectId
from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

# What the recipes these journeys run leave in the temporary directory, by design or
# from a published tool they run, is this module's own and goes with it
# (`tests/short_state.py`).
pytestmark = [pytest.mark.usefixtures("module_tmpdir")]

#: The step a stand-in writer runs to copy the budget command's output into its answers.
MERGES_THE_BUDGETS = Path(__file__).resolve().parent / "merge_answers.py"

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
    #: What every command run in the copy carries to run on this checkout's install.
    environment: dict[str, str]


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
    shared = share_this_checkouts_install(checkout)
    return Routed(checkout=checkout, linear=linear, environment=shared)


def seeded(root: Path, *, execution: str, origin: str) -> Identity:
    """The stand-in's scratch identity, declared public: this journey routes public
    repository content into the team source."""
    return seeded_identity(root, execution=execution, origin=origin, visibility="public")


class Bench(NamedTuple):
    """One shape's own registry, runs root, authoring root and environment."""

    environment: dict[str, str]
    authoring: Path
    tmp_path: Path
    #: What reaching the petsinc stand-in's hosted origin over git needs.
    identity: dict[str, str]


def _bench(
    tmp_path: Path, oneharness_bin: str, remote: Mapping[str, str], shared: Mapping[str, str]
) -> Bench:
    """The environment a shape's flow runs in, with the petsinc stand-in registered."""
    identity = seeded(tmp_path / "identity", execution=EXECUTION_ALIAS, origin=PETSINC)
    authoring = tmp_path / "authoring"
    authoring.mkdir()
    environment = dict(os.environ)
    # The copy's `.venv` is its own overlay of this checkout's, so this checkout's is not
    # the active one there.
    for name in (*INHERITED_ENVIRONMENT, "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
        environment.pop(name, None)
    environment.update(shared)
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


class Node(NamedTuple):
    """One planned task: its node id, title, dependencies, and whether it is petsinc's."""

    id: NodeId
    title: str
    deps: tuple[NodeId, ...]
    petsinc: bool


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
                    "unit": "Petsinc change" if node.petsinc else "Library",
                }
                for node in nodes
            ],
        },
        native_id=name,
    )
    # Every plan states its plan-level budget answers; this one's work needs no budget.
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
                        'printed=$(uv run python -m orchestrator.plan_budgets "$3:$4") &&'
                        ' python3 "$9" "$7" "$printed" &&'
                        ' asset=$(python3 "${10}" "$7" "$2") &&'
                        ' "$1" template resolve design-doc ${8:+--repository "$8"} --json'
                        ' | "$2" document create "$3" --project "$4" --title "$5" --id "$6"'
                        ' --template-loader - --answers "$7" --no-interactive'
                        ' ${asset:+--asset "$asset"}',
                        "store-the-document",
                        str(ENGINE_BIN),
                        str(ONETASKGRAPH_BIN),
                        AUTHORING,
                        name,
                        f"Design: {name}",
                        f"{name}-design",
                        str(answers),
                        repository,
                        str(MERGES_THE_BUDGETS),
                        str(MERGES_THE_BUDGETS.with_name("copy_quoted_rows.py")),
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


@pytest.fixture
def gated(
    tmp_path: Path, oneharness_bin: str, routed: Routed
) -> Iterator[tuple[Bench, Mapping[str, str]]]:
    """A served loopback `plans` board and one shape's bench over it."""
    with _serving_board() as remote:
        yield _bench(tmp_path, oneharness_bin, remote, routed.environment), remote


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
# builds a copy of this checkout to `plan-tooling-plan-flow:test-docs`, keyed on the whole
# workspace because copying the tracked tree reads all of it. `test_plan_flow_e2e.py`'s
# default-board flow copies a checkout behind the same marker for the same reason.
# llmlint: ignore-block[e2e_not_mocked] Three boundaries are doubled and no more: the paid
# provider process, GitHub's Projects API (the loopback board, because the live board is shared
# by every run of this repository), and Linear (the folder the copy declares `hellopatient` as,
# because no check may reach the production workspace). The recipes, engine, store, routing
# and every record read back are real.
@COPIES_THE_TRACKED_TREE
@pytest.mark.xdist_group("linear-routed")
def test_an_all_petsinc_plan_lands_whole_in_linear(
    routed: Routed, gated: tuple[Bench, Mapping[str, str]]
) -> None:
    """Every task petsinc's: the home, its tasks and its design document are Linear's.

    Copied by `just finish-plan` onto `plans`, as every plan is, and placed by the store's
    routing alone.
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
def test_a_mixed_plan_lands_as_one_board_home_and_its_linear_member(
    routed: Routed, gated: tuple[Bench, Mapping[str, str]]
) -> None:
    """A plan spanning both orgs is one home and one member.

    The home lands on the `plans` board with the design document and the non-petsinc tasks;
    the petsinc task lands in the home's `hellopatient` member project.
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
    # The home holds the plan's one document, the one it is read as; its budget answers are
    # its own description, and its home project carries them.
    templates = sorted(
        (held["item"]["metadata"].get("onetaskgraph.template") or {}).get("template", "")
        for held in json.loads(documents.stdout)["items"]
    )
    assert templates == ["onepipeline:design-doc"], documents.stdout
    described = json.loads(shown.stdout)["items"][0]["item"]["metadata"]
    assert "orchestrator.plan-budgets" in described, described
    # llmlint: ignore-end[e2e_not_mocked]
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]  # noqa: E501


# llmlint: ignore[expensive_tests_stay_behind_their_own_edge] This routed-copy journey uses the existing loopback board and copied-checkout harness in plan-tooling-plan-flow; that harness reads the tracked tree, so wholeWorkspace is its actual input rather than avoidable unrelated selection.  # noqa: E501
# llmlint: ignore[e2e_not_mocked] This uses the established routing harness's paid-provider double, loopback GitHub Projects API and local-md Linear stand-in; production boards are shared and tests may not write them. Recipes, store, routing and destination records are real.  # noqa: E501
@COPIES_THE_TRACKED_TREE
@pytest.mark.xdist_group("linear-routed")
def test_modern_budget_links_are_recomputed_after_a_routed_copy(
    routed: Routed, gated: tuple[Bench, Mapping[str, str]]
) -> None:
    """Real routing creates current GitHub and local Linear destinations from authoring."""
    from project_fixtures import owning

    bench, _ = gated
    name = f"test-{os.getpid()}-modern-budget-links"
    project = _draft(bench, name, MIXED)
    # One budget added and one changed, the two `file_change`s a design document lists and
    # links; an unchanged one is presentation-filtered out of the section these links are in.
    for node, change in zip(MIXED[:2], ("add", "change"), strict=True):
        budget = {
            "id": f"{node.id}-copy-seconds",
            "name": f"{node.id} copy time",
            "schema_version": 2,
            "source": "telemetry",
            "check_runtime_seconds": 1,
            "basis": "design",
            "repository": PETSINC,
            "file": "apps/web/budgets.yaml",
            "file_change": change,
            "measure": "elapsed seconds of loopback copy",
            "inner_measure_reason": "",
            "unit": "seconds",
            "direction": "max",
            "threshold": 60,
            "workload": "One loopback fixture copy.",
            "evidence": "Synthetic routing fixture; whole record-read runtime estimated at 1 s.",
            "command": "Read the fixture copy timing record without launching another copy.",
        }
        owning(
            AUTHORING,
            name,
            node.id,
            [budget],
            bench.environment,
            answers={
                "what": COMMIT_MARKER if node.petsinc else "Reply with done.",
                "why": "Exercise routing with a modern budget record.",
                "acceptance_criteria": [
                    "The route is exercised end to end.",
                    "Every claim the dispatch makes about the finished work is true of the tree "
                    "as it finally stands.",
                ],
            },
        )
    answers = no_budgets([PETSINC])
    answers.update(schema_version=2, checklist=[], repo_wide_effects=[])
    budgeted(AUTHORING, name, answers, bench.environment)
    _scripted(bench, name, MIXED)
    try:
        finished = _just(routed, bench, "finish-plan", str(_brief(bench, project)), "--name", name)
        assert finished.returncode == 0, finished.stdout + finished.stderr
        (home,) = HOME_LINE.findall(finished.stdout + finished.stderr)
        printed = subprocess.run(
            [str(routed.checkout / ".venv/bin/python"), "-m", "orchestrator.plan_budgets", home],
            cwd=routed.checkout,
            env=bench.environment,
            text=True,
            capture_output=True,
            check=True,
        )
        writer = json.loads(printed.stdout)
        locations = {
            node: held["item"]["location"]
            for node, held in _items(routed, bench, QualifiedProjectId(home)).items()
        }
        for budget in writer["budgets"]:
            destination = locations[budget["node"]]
            assert budget["location"] == destination.get("url", destination.get("path"))
            assert "section_url" not in budget
            assert str(bench.authoring) not in budget["location"]
        assert writer["budgets"][0]["schema_version"] == 2
        # Re-render using current destination answers; no source-host fragment is persisted.
        base = _design_answers(MIXED)
        supplied = bench.tmp_path / "destination-answers.json"
        supplied.write_text(json.dumps({**base, **writer}), encoding="utf-8")
        resolved = subprocess.run(
            [
                str(routed.checkout / ".venv/bin/onepipeline"),
                "template",
                "resolve",
                "design-doc",
                "--json",
                "--template-root",
                str(routed.checkout / "templates"),
            ],
            cwd=routed.checkout,
            env=bench.environment,
            text=True,
            capture_output=True,
            check=True,
        )
        rendered = subprocess.run(
            [
                str(ONETASKGRAPH_BIN),
                "template",
                "render",
                "--template-loader",
                "-",
                "--answers",
                str(supplied),
                "--no-interactive",
            ],
            input=resolved.stdout,
            cwd=routed.checkout,
            env=bench.environment,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        assert "## Budgets\n" in rendered, rendered
        for index, budget in enumerate(writer["budgets"], 1):
            fragment = (
                f"#user-content-{budget['id']}"
                if budget["location"].startswith("https://github.com/")
                else ""
            )
            assert f"[{index}]: {budget['location']}{fragment}\n" in rendered
    finally:
        _just(routed, bench, "stop", f"{name}-design", seconds=60)
