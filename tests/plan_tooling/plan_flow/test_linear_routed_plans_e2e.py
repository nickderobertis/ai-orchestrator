"""A plan naming Hidden Co's private repositories lives in Hidden Co's Linear, all of its road.

A host's onetaskgraph user layer — here the tracked example's, installed under this module's
own configuration home — routes every `github.com/hiddenco/*` task from `plans` to the
organization's `hiddenco-plans` Linear source; the store's public boundary is on, as the
tracked configuration switches it, and the layer declares every source it defines private.
Under that boundary the store places a plan by its voters, and any item that classifies
private moves the home: the home, its documents and every task go to the private source
its private voters route to, and a task naming only a public repository is seen on `plans`
only as the store's neutral view under a `Public work` project. A private item copied to a public
destination is refused before anything is written. The `onevcs` state root every command reads
registers the organization's repository private and the public ones public, so the boundary
classifies from rules this module wrote and never asks GitHub.

These journeys drive that through the real recipes:

* an all-hiddenco plan, and a plan whose only repository-naming task routes there, land whole
  in Linear;
* a mixed plan lands its home, design document and every task in Linear and its public
  task's view on `plans`, and a re-copy into Linear holds it one project; a mixed plan whose
  public task carries a budget copies it to its view and its private home alike;
* **the road**: a plan of the incident's shape — three tasks naming the organization's
  repositories and one `kind: human` node naming none — is finished, approved and launched
  over `hiddenco-plans:<project>`, its three workers commit and land, its human node is
  attested, and its settlements are written back to Linear, while the loopback `plans` board
  receives nothing;
* a person's edit to the approved Linear document refuses the next launch; a public task
  naming a private repository is refused before either board is written; and a Linear that
  rewrites what it stores in a way the store does not declare is refused by name.

The launch reaches the Linear source's credential the one way a host's launch does: through
the variable the user layer's `api_key_env` names, read out of the copy's gitignored `.env` by
the recipes' own credential loading (`scripts/credentials-env.sh`). No journey hands a
process that credential any other way.

**Three boundaries are doubled.** Linear is `tests/linear_board.py`'s loopback wire, serving
the source the example layer declares; GitHub's Projects API is the loopback board
`tests/github_board.py` serves; and the paid model is the provider stand-in every journey here
uses. The recipes, the engine, the store and its boundary, `onevcs`, and every record read back
are real. The copy's `onevcs` is a counting wrapper that records each `boundary` process it is
asked to run and then runs the pinned one unchanged, which is how one public copy's boundary
cost is measured.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import NamedTuple, NewType

import boundary_home
import fake_gh
import follow_up_variables
import onetaskgraph_user_layer
import plan_root_variable
import pytest
import short_state
from budget_telemetry import record
from fake_backend import (
    PROMPT_LOG_ENV,
    RUN_ON_MARKER_ENV,
    TURN_GATE_ENV,
    TURN_GATE_REACHED,
    TURN_GATE_RELEASED,
)
from github_board import BOARD as BOARD_FIXTURE
from github_board import SIBLING_REPOSITORY, _GitHubFixture, _serving_board
from linear_board import LINEAR_REWRITES, LinearBoard, Rewrite, linear_board, stored_form
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

from orchestrator import design_approval, plan_budgets, plan_check, plan_store
from orchestrator.criteria_guard import APPENDIX
from orchestrator.plan_budgets import BudgetId, ModernBudget, Source
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
FAKE_GH = helper("fake_gh.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")
ENGINE_BIN = REPO_ROOT / "node_modules" / ".bin" / "onepipeline"

#: The scripted reviewer's one answer: a pass naming no finding.
PASSING_VERDICT = json.dumps({"passes": True, "findings": []})

#: The two sources under test, by the names the tracked configuration gives them, and the
#: organization's follow-up tracker, which the boundary reads as a private source too.
LINEAR = onetaskgraph_user_layer.ORGANIZATION_PLANS
LINEAR_FOLLOWUPS = onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS
BOARD = "plans"
AUTHORING = "authoring"

#: The variable the example layer's Linear sources name their credential by.
LINEAR_KEY = str(onetaskgraph_user_layer.example()["sources"][LINEAR]["config"]["api_key_env"])
#: The state the tracked mapping writes a settled `done` task as.
LINEAR_DONE = str(
    onetaskgraph_user_layer.example()["sources"][LINEAR]["config"]["status_mapping"]["done"]["task"]
)

#: This repository's own public identity, and a second public one a public plan names: the
#: sibling the loopback board holds issues in beside this one.
THIS_REPOSITORY = boundary_home.THIS_REPOSITORY
SECOND_PUBLIC = f"github.com/{SIBLING_REPOSITORY.owner}/{SIBLING_REPOSITORY.name}"
#: The organization's private repositories. The first is registered as a scratch identity so
#: its lifecycle nodes have somewhere to publish; the second is only ever named in prose.
HIDDENCO = "github.com/hiddenco/quietharbor"
HIDDENCO_ELSEWHERE = "github.com/hiddenco/lanternapp"
EXECUTION_ALIAS = "quietharbor-execution"

#: The line a design-doc dispatch's task carries, which says the turn is that one.
DESIGN_TASK_MARKER = "Write the design document a person reviews the plan"


def _commits(name: str) -> list[list[str]]:
    """What a worker whose task names ``name`` does: commit one file of that name."""
    return [
        [
            "sh",
            "-c",
            f"[ -f {name}.md ] || {{ echo {name} > {name}.md && git add {name}.md "
            "&& git -c user.email=test@example.com -c user.name=ai-orchestrator-test "
            f"commit -qm 'feat: record {name}'; }}",
        ]
    ]


#: The line a hiddenco node's task carries, which says the turn commits on its branch.
COMMIT_MARKER = "Leave one file behind on your branch, and commit it."

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
    LINEAR_KEY,
)

#: The store's environment layer for the organization's sources, which the store spells with a
#: source name's hyphen as an underscore. A key of it inherited from the process running the
#: suite would point a source somewhere this module did not serve.
LINEAR_LAYER = "ONETASKGRAPH_SOURCES__HIDDENCO_"

#: What a journey building a copy of this checkout reads: everything git tracks.
COPIES_THE_TRACKED_TREE = pytest.mark.reads_docs

#: Where the copy's counting `onevcs` appends each `boundary` process it runs.
BOUNDARY_RECORD_ENV = "BOUNDARY_RECORD"

#: The copy's `onevcs`: each `boundary` invocation recorded with what it was handed, then the
#: pinned program run on the same arguments and input. Recording is the whole of what it adds.
COUNTING_ONEVCS = """#!@PYTHON@
import fcntl, json, os, subprocess, sys
real = "@REAL@"
record = os.environ.get("@RECORD@")
if not record or sys.argv[1:2] != ["boundary"]:
    os.execv(real, [real, *sys.argv[1:]])
handed = sys.stdin.buffer.read()
done = subprocess.run([real, *sys.argv[1:]], input=handed, capture_output=True)
with open(record, "a", encoding="utf-8") as stream:
    fcntl.flock(stream, fcntl.LOCK_EX)
    stream.write(json.dumps({"argv": sys.argv[1:], "input": handed.decode("utf-8", "replace"),
                             "status": done.returncode}) + "\\n")
sys.stdout.buffer.write(done.stdout)
sys.stderr.buffer.write(done.stderr)
sys.exit(done.returncode)
"""


class Routed(NamedTuple):
    """A copy of this checkout, its configuration home, and its environment."""

    checkout: Path
    config_home: Path
    #: What every command run in the copy carries to run on this checkout's install.
    environment: dict[str, str]


def _counting(checkout: Path) -> None:
    """Install the counting `onevcs` as the copy's own, in front of the pinned one."""
    installed = checkout / ".venv" / "bin" / "onevcs"
    real = boundary_home.ONEVCS.resolve()
    installed.unlink()
    # llmlint: ignore[e2e_not_mocked, tests_mirror_real_usage] The published CLI is the one boundary this suite doubles at, and this one runs the pinned program on every command unchanged: no real `onevcs` reports how many boundary processes a copy started, which is what the boundary budget counts.  # noqa: E501 - a directive is one line, and its reason is longer than the limit
    installed.write_text(
        COUNTING_ONEVCS.replace("@PYTHON@", str(checkout / ".venv" / "bin" / "python3"))
        .replace("@REAL@", str(real))
        .replace("@RECORD@", BOUNDARY_RECORD_ENV),
        encoding="utf-8",
    )
    installed.chmod(0o755)


@pytest.fixture(scope="module")
def routed(tmp_path_factory: pytest.TempPathFactory) -> Routed:
    """One copy of this checkout, shared by every journey: its layer and its toolchain."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("linear-routed")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    copy_working_tree(checkout)
    answering_this_checkouts_origin(checkout)
    layer = onetaskgraph_user_layer.without_private_stores()
    home = onetaskgraph_user_layer.installed(tmp_path / "config-home", layer)
    shared = share_this_checkouts_install(checkout)
    _counting(checkout)
    shared[onetaskgraph_user_layer.XDG_CONFIG_HOME] = str(home)
    # The counting `onevcs` is the one the store's write policy reaches by its bare name.
    shared["PATH"] = os.pathsep.join([str(checkout / ".venv" / "bin"), os.environ["PATH"]])
    return Routed(checkout=checkout, config_home=home, environment=shared)


def seeded(root: Path) -> Identity:
    """The organization's scratch identity, declared private as its rule says."""
    return seeded_identity(root, execution=EXECUTION_ALIAS, origin=HIDDENCO, visibility="private")


def _also_public(root: Path, identity: Identity, public: Sequence[str]) -> None:
    """Register a stub checkout of each ``public`` identity in ``identity``'s state root.

    Its rules become the seeded ones with each identity declared public, so the boundary
    classifies them from what this module wrote and asks GitHub nothing; no node of a plan
    naming them is ever launched, so a stub with no history beyond one commit is enough.
    """
    lines = ["version: 4", "rules:"]
    for identity_name in public:
        host, owner, name = identity_name.split("/")
        lines += [f"  - match: {{host: {host}, owner: {owner}, name: {name}}}"]
        lines += ["    visibility: public", "    publication: local-direct"]
    overlay = root / "public-visibility.yml"
    overlay.write_text("\n".join(lines) + "\n", encoding="utf-8")
    environment = boundary_home.environment(None, identity.home, transports=False)
    applied = subprocess.run(
        [str(boundary_home.ONEVCS), "rules", "apply"]
        + ["--base", str(root / "onevcs.rules.yml"), "--overlay", str(overlay)],
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert applied.returncode == 0, applied.stdout + applied.stderr
    for identity_name in public:
        host, owner, name = identity_name.split("/")
        stub = root / f"stub-{owner}-{name}"
        stub.mkdir()
        for argv in (
            ["git", "init", "-q", "-b", "main"],
            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid"]
            + ["commit", "-q", "--allow-empty", "-m", "init"],
            ["git", "remote", "add", "origin", f"https://{host}/{owner}/{name}.git"],
            [str(boundary_home.ONEVCS), "register", str(stub)],
        ):
            ran = subprocess.run(
                argv,
                cwd=stub,
                env=environment,
                text=True,
                capture_output=True,
                timeout=e2e_timeout(120),
                check=False,
            )
            assert ran.returncode == 0, ran.stdout + ran.stderr


class Bench(NamedTuple):
    """One journey's own registry, runs root, authoring root, environment and Linear."""

    environment: dict[str, str]
    authoring: Path
    tmp_path: Path
    linear: LinearBoard
    #: Where the copy's counting `onevcs` records each boundary process.
    boundaries: Path


def _bench(  # noqa: PLR0913 - each is one thing a journey's environment is built from
    tmp_path: Path,
    oneharness_bin: str,
    remote: Mapping[str, str],
    routed: Routed,
    linear: LinearBoard,
    served: Mapping[str, str],
    public: Sequence[str],
) -> Bench:
    """The environment a journey's recipes run in, with the hiddenco identity registered."""
    identity = seeded(tmp_path / "identity")
    if public:
        _also_public(tmp_path / "identity", identity, public)
    authoring = tmp_path / "authoring"
    authoring.mkdir()
    environment = dict(os.environ)
    # The copy's `.venv` is its own overlay of this checkout's, so this checkout's is not
    # the active one there.
    for name in (*INHERITED_ENVIRONMENT, "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
        environment.pop(name, None)
    environment.update(routed.environment)
    for name in [name for name in environment if name.startswith(LINEAR_LAYER)]:
        del environment[name]
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment = boundary_home.environment(environment, identity.home, transports=False)
    environment["PATH"] = os.pathsep.join(
        [str(routed.checkout / ".venv" / "bin"), environment["PATH"]]
    )
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
    # A plan is drafted in the gitignored authoring root, which is this host's own: a host
    # switching the boundary on declares it private, as it declares every store of its own.
    environment["ONETASKGRAPH_SOURCES__AUTHORING__VISIBILITY"] = "private"
    environment["ONETASKGRAPH_SECRETS_FILE"] = str(tmp_path / "no-secrets.env")
    environment["ONETASKGRAPH_DEFAULT_SOURCES"] = f"{AUTHORING},{BOARD},{LINEAR}"
    root_name, plugin_name, command_name = follow_up_variables.all_names()
    environment.pop(command_name, None)
    environment[root_name] = str(tmp_path / "follow-ups")
    environment[plugin_name] = plan_store.WRITABLE_PLUGIN
    environment.update(remote)
    # The loopback Linear's endpoint for each of the organization's sources; its credential
    # is the copy's `.env`'s, which the recipes load and nothing here exports.
    environment.update(served)
    boundaries = tmp_path / "boundaries.jsonl"
    environment[BOUNDARY_RECORD_ENV] = str(boundaries)
    # A lifecycle closeout asks the host whether its branch holds a change request; the
    # organization's repository is on no host, so the host's decisioning is answered at the
    # seam `onevcs` publishes for it, and nothing reaches GitHub.
    # llmlint: ignore[e2e_not_mocked] Only the remote host's decisioning, at onevcs's seam.
    environment["ONEVCS_GH"] = str(FAKE_GH)
    environment[fake_gh.STATE_ENV] = str(tmp_path / "gh-state")
    environment[fake_gh.CALLS_ENV] = str(tmp_path / "gh-calls.jsonl")
    assert LINEAR_KEY not in environment, "the credential is the `.env`'s to supply"
    return Bench(
        environment=environment,
        authoring=authoring,
        tmp_path=tmp_path,
        linear=linear,
        boundaries=boundaries,
    )


def _task(what: str) -> str:
    """A task body that carries its bar, as a planner writes one."""
    return (
        f"## What\n\n{what}\n\n"
        "## Why\n\nHidden Co's work is planned where that team tracks it.\n\n"
        "## Acceptance criteria\n\n"
        "- The change is in place and a test drives it end to end.\n"
        "- Every claim the dispatch makes about the finished work is true of the tree as it "
        "finally stands.\n\n"
        f"{(REPO_ROOT / APPENDIX).read_text(encoding='utf-8').strip()}\n"
    )


#: A plan node's id, which every task record carries as `onepipeline.id`.
NodeId = NewType("NodeId", str)


class Node(NamedTuple):
    """One planned task: its id, title, dependencies, the repository it names, and its role.

    ``repository`` is a normalized origin or empty for a task naming none. A hiddenco node
    either commits on its branch (``commits``) or declares `expects_no_diff`; a human node
    is the action prose alone; every other node is an agent node of no repository.
    """

    id: NodeId
    title: str
    deps: tuple[NodeId, ...] = ()
    repository: str = ""
    commits: bool = False
    human: bool = False
    #: Prose the task carries beyond its bar, such as a repository it mentions.
    mentions: str = ""


def _node_record(node: Node) -> dict[str, object]:
    """``node`` as the plan record a planner writes for it."""
    held: dict[str, object] = {"id": node.id, "title": node.title}
    if node.deps:
        held["deps"] = list(node.deps)
    if node.human:
        task = f"Sign off on {node.title}.{node.mentions}"
        return {**held, "kind": "human", "task": task, "unit": "Sign-off"}
    if node.repository:
        held["repo"] = node.repository
        if node.repository == HIDDENCO:
            held["execution_checkout"] = EXECUTION_ALIAS
    # A node settled with no dispatch takes no persona.
    if node.repository == HIDDENCO and not node.commits:
        held["expects_no_diff"] = True
    else:
        held["persona"] = "engineer"
    marker = f"{COMMIT_MARKER} Name it {node.id}." if node.commits else "Reply with done."
    held["task"] = _task(marker + node.mentions)
    held["unit"] = "Hiddenco change" if node.repository == HIDDENCO else "Library"
    return held


def _rendered(bench: Bench, name: str, node: Node) -> None:
    """Regenerate ``node``'s task as a planner renders it, from this host's `plan-task`.

    The pinned engine's `template resolve plan-task` piped into the store's own `task
    render`, which is the body the engine's rendered-only check holds a launch to.
    """
    answers = bench.tmp_path / f"{name}-{node.id}-task.json"
    marker = f"{COMMIT_MARKER} Name it {node.id}." if node.commits else "Reply with done."
    answers.write_text(
        json.dumps(
            {
                "what": marker + node.mentions,
                "why": "Hidden Co's work is planned where that team tracks it.",
                "acceptance_criteria": [
                    "The change is in place and a test drives it end to end.",
                    "Every claim the dispatch makes about the finished work is true of the "
                    "tree as it finally stands.",
                ],
                "budgets": [],
            }
        ),
        encoding="utf-8",
    )
    resolved = subprocess.run(
        [str(ENGINE_BIN), "template", "resolve", "plan-task", "--json"]
        + ["--template-root", str(REPO_ROOT / "templates")],
        cwd=REPO_ROOT,
        env=bench.environment,
        text=True,
        capture_output=True,
        check=True,
    )
    rendered = subprocess.run(
        [str(ONETASKGRAPH_BIN), "task", "render", f"{AUTHORING}:{name}/{node.id}"]
        + ["--template-loader", "-", "--answers", str(answers), "--no-interactive"],
        cwd=REPO_ROOT,
        env=bench.environment,
        input=resolved.stdout,
        text=True,
        capture_output=True,
        check=False,
    )
    assert rendered.returncode == 0, rendered.stdout + rendered.stderr


def _draft(
    bench: Bench,
    name: str,
    nodes: Sequence[Node],
    repositories: tuple[str, ...] = (),
    *,
    rendered: bool = False,
) -> str:
    """Author ``nodes`` as one plan in the authoring source, answering its qualified id.

    The project names ``repositories`` as its own, which vote on where that home goes. A
    ``rendered`` plan's agent tasks are regenerated from this host's `plan-task` template,
    as a planner's are, so a launch under the rendered-only check accepts them.
    """
    write_plan_project(
        bench.authoring,
        {
            "schema_version": 3,
            "goal": {"text": "Ship Hidden Co's change and the library it consumes"},
            "name": name,
            "tasks": [_node_record(node) for node in nodes],
        },
        native_id=name,
    )
    for node in nodes if rendered else ():
        if not node.human:
            _rendered(bench, name, node)
    if repositories:
        record_path = bench.authoring / "projects" / f"{name}.md"
        written = record_path.read_text(encoding="utf-8")
        # llmlint: ignore[tests_mirror_real_usage] A project's own `repositories` is a field of its record that no store verb sets, and `personas/planner.yaml` has a planner write `projects/<project>.md` by hand, so it is written into that record the way a planner writes it.  # noqa: E501
        record_path.write_text(
            written.replace(
                "---\n",
                "---\nrepositories:\n" + "".join(f'  - "{one}"\n' for one in repositories),
                1,
            ),
            encoding="utf-8",
        )
    # Every plan states its plan-level budget answers; this one's work needs no budget.
    budgeted(
        AUTHORING,
        name,
        no_budgets(sorted({node.repository for node in nodes if node.repository})),
        bench.environment,
    )
    return f"{AUTHORING}:{name}"


def _design_answers(nodes: Sequence[Node]) -> dict[str, object]:
    """What a design-doc dispatch's model would compose for the plan of ``nodes``.

    Its one unit names the repository of the plan's first repository-naming task, so a
    public plan's document names no private repository.
    """
    named = next((node.repository for node in nodes if node.repository), HIDDENCO)
    return {
        "what": "Hidden Co's change and the library it consumes.",
        "why": "The team plans in Linear.",
        "architecture": "One library, one consumer.",
        "units": [
            {
                "name": "Change",
                "repository": named.rpartition("/")[2],
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


def _scripted(bench: Bench, name: str, nodes: Sequence[Node]) -> None:
    """Script the design-doc dispatch's store command and each hiddenco worker's commit.

    The document is rendered by the pinned engine's resolve and stored by the store's own
    `document create`, run where the dispatch runs; the resolve names the plan's one
    repository when every task names it, as the writer's own task says to.
    """
    answers = bench.tmp_path / f"{name}-design.json"
    answers.write_text(json.dumps(_design_answers(nodes)), encoding="utf-8")
    repository = HIDDENCO if all(node.repository == HIDDENCO for node in nodes) else ""
    keyed = bench.tmp_path / "commands.json"
    commands: dict[str, list[list[str]]] = {
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
    }
    for node in nodes:
        if node.commits:
            commands[f"{COMMIT_MARKER} Name it {node.id}."] = _commits(node.id)
    keyed.write_text(json.dumps(commands), encoding="utf-8")
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
        "## What\nShip Hidden Co's change.\n\n"
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


def _finishing(
    routed: Routed,
    bench: Bench,
    name: str,
    nodes: Sequence[Node],
    repositories: tuple[str, ...] = (),
    *,
    rendered: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Draft and script the plan, then run `just finish-plan` over it, whatever it answers."""
    project = _draft(bench, name, nodes, repositories, rendered=rendered)
    _scripted(bench, name, nodes)
    try:
        return _just(routed, bench, "finish-plan", str(_brief(bench, project)), "--name", name)
    finally:
        _just(routed, bench, "stop", f"{name}-design", seconds=60)


def _finished(
    routed: Routed,
    bench: Bench,
    name: str,
    nodes: Sequence[Node],
    repositories: tuple[str, ...] = (),
    *,
    rendered: bool = False,
) -> Finished:
    """Draft, review, document and copy the plan through `just finish-plan`."""
    finished = _finishing(routed, bench, name, nodes, repositories, rendered=rendered)
    reported = finished.stdout + finished.stderr
    assert finished.returncode == 0, f"the tail failed:\n{reported}"
    homes = HOME_LINE.findall(reported)
    assert len(homes) == 1, f"the tail reported no single home for {name}:\n{reported}"
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


def _served(linear: LinearBoard, home: Path) -> dict[str, str]:
    """Point each of the organization's sources at ``linear``, the credential left out.

    The double reads the store's own resolution of the layer, so it is asked from this
    process with the copy's configuration home in force.
    """
    with pytest.MonkeyPatch.context() as patched:
        patched.setenv(onetaskgraph_user_layer.XDG_CONFIG_HOME, str(home))
        for name in [name for name in os.environ if name.startswith(LINEAR_LAYER)]:
            patched.delenv(name)
        served = {**linear.environment(LINEAR), **linear.environment(LINEAR_FOLLOWUPS)}
    return served


def _reading(routed: Routed, bench: Bench) -> dict[str, str]:
    """``bench``'s environment with the copy's `.env` credential, for a read this journey
    makes itself through a module rather than a recipe, which loads none."""
    (line,) = (routed.checkout / ".env").read_text(encoding="utf-8").splitlines()
    name, _, value = line.partition("=")
    return {**bench.environment, name: value}


def _credentialed(routed: Routed, served: dict[str, str]) -> dict[str, str]:
    """Write the double's credential into the copy's `.env`; what is left to export."""
    key = served.pop(LINEAR_KEY)
    (routed.checkout / ".env").write_text(f"{LINEAR_KEY}={key}\n", encoding="utf-8")
    return served


@contextmanager
def _gated(
    tmp_path: Path,
    oneharness_bin: str,
    routed: Routed,
    *,
    public: Sequence[str] = (),
    rewrites: Sequence[Rewrite] = LINEAR_REWRITES,
) -> Iterator[Bench]:
    """A served loopback `plans` board and Linear, and one journey's bench over both."""
    with _serving_board() as remote, linear_board(rewrites=rewrites) as linear:
        served = _credentialed(routed, _served(linear, routed.config_home))
        yield _bench(tmp_path, oneharness_bin, remote, routed, linear, served, public)


def _home_project(linear: LinearBoard, home: QualifiedProjectId) -> Mapping[str, object]:
    """The Linear project ``home`` names, as the double holds it."""
    native = home.partition(":")[2]
    (project,) = [held for held in linear.projects() if held["id"] == native]
    return project


def _board_mutations() -> list[str]:
    """Every request to the loopback `plans` board that would write content."""
    return [
        request.query.split("{", 1)[0].strip()
        for request in _GitHubFixture.requests
        if request.is_mutation
    ]


ALL_HIDDENCO = (
    Node(NodeId("migrate"), "feat: migrate the intake record", repository=HIDDENCO, commits=True),
    Node(
        NodeId("backfill"),
        "feat: backfill the migrated records",
        (NodeId("migrate"),),
        repository=HIDDENCO,
    ),
)


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]  # noqa: E501
# tests/plan_tooling/AGENTS.md states this project's split: `reads_docs` routes a journey that
# builds a copy of this checkout to `plan-tooling-plan-flow:test-docs`, keyed on the whole
# workspace because copying the tracked tree reads all of it. `test_plan_flow_e2e.py`'s
# default-board flow copies a checkout behind the same marker for the same reason.
# llmlint: ignore-block[e2e_not_mocked] Three boundaries are doubled and no more: the paid
# provider process, GitHub's Projects API (the loopback board, because the live board is shared
# by every run of this repository), and Linear (the loopback wire `tests/linear_board.py`
# serves, because no check may reach a Linear workspace). The recipes, engine, store, its
# boundary, `onevcs` and every record read back are real.
@COPIES_THE_TRACKED_TREE
@pytest.mark.xdist_group("linear-routed")
def test_an_all_hiddenco_plan_lands_whole_in_linear(
    routed: Routed, tmp_path: Path, oneharness_bin: str
) -> None:
    """Every task hiddenco's: the home, its tasks and its design document are Linear's.

    Copied by `just finish-plan` onto `plans`, as every plan is, and placed by the store's
    routing alone.
    """
    with _gated(tmp_path, oneharness_bin, routed) as bench:
        name = f"test-{os.getpid()}-all-hiddenco"
        home, reported = _finished(routed, bench, name, ALL_HIDDENCO)

        assert home.startswith(f"{LINEAR}:"), f"the home landed at {home}, not in {LINEAR}"
        for said in (f"`just approve-design {home}`", f"`just orchestrate {home}`"):
            assert said in reported, f"the tail never said {said!r}:\n{reported}"
        assert "its tasks routed to" not in reported, reported
        project = _home_project(bench.linear, home)
        assert project["name"], project
        documents = [held for held in bench.linear.documents() if held["project"]]
        assert [held["project"]["id"] for held in documents] == [project["id"]], documents
        stored = _items(routed, bench, home)
        assert set(stored) == {node.id for node in ALL_HIDDENCO}, stored
        assert {str(held["id"]).partition(":")[0] for held in stored.values()} == {LINEAR}
        assert BOARD_FIXTURE.created == [], [issue.title for issue in BOARD_FIXTURE.created]
    # llmlint: ignore-end[e2e_not_mocked]
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]  # noqa: E501


#: Where the store records, on a private home only, each public task's view, and the title
#: of the neutral project the views on one public source are filed under.
PUBLIC_PROJECTIONS = "onetaskgraph.public_projections"
PUBLIC_WORK = "Public work"

MIXED = (
    Node(
        NodeId("library"),
        "feat: add the record client to the library",
        repository=THIS_REPOSITORY,
    ),
    Node(
        NodeId("consumer"),
        "feat: consume the record client",
        (NodeId("library"),),
        repository=HIDDENCO,
        commits=True,
    ),
    Node(NodeId("rollout"), "feat: roll the client out behind its flag", (NodeId("consumer"),)),
)


def _private_named(text: str) -> list[str]:
    """Every private repository of the organization ``text`` names, in any spelling."""
    return sorted(
        {
            repository
            for repository in (HIDDENCO, HIDDENCO_ELSEWHERE)
            for spelled in (repository, repository.partition("/")[2], repository.rpartition("/")[2])
            if spelled in text
        }
    )


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]  # noqa: E501
# The same placement and reason as the journey above.
# llmlint: ignore-block[e2e_not_mocked] The same three doubled boundaries as the journey above.
@COPIES_THE_TRACKED_TREE
@pytest.mark.xdist_group("linear-routed")
@pytest.mark.xfail(
    strict=True,
    raises=KeyError,
    reason=(
        "onetaskgraph 0.12.5's Linear copy rewrites the private home without its "
        "public_projections record: https://github.com/nickderobertis/onetaskgraph/issues/3781; "
        "remove this marker once a release fixing it is adopted"
    ),
)
def test_a_mixed_plan_keeps_every_task_in_linear_and_shows_its_public_task_on_the_board(
    routed: Routed, tmp_path: Path, oneharness_bin: str
) -> None:
    """A private item moves the home: only the public task's neutral view reaches `plans`.

    The home, its design document and every task — the hiddenco one, the one naming no
    repository and the one naming this public repository — land in the Linear double; the
    public task is seen on `plans` only as the store's view of it, under a `Public work`
    project, recorded on the private home and nowhere public; and nothing on the board names
    a private repository. A copy of the same plan into Linear afterwards finds the project it
    put there and holds one.
    """
    with _gated(tmp_path, oneharness_bin, routed, public=[THIS_REPOSITORY]) as bench:
        name = f"test-{os.getpid()}-mixed"
        home, reported = _finished(routed, bench, name, MIXED)

        assert home.startswith(f"{LINEAR}:"), f"the home landed at {home}, not in {LINEAR}"
        stored = _items(routed, bench, home)
        placed = {node: str(held["id"]).partition(":")[0] for node, held in stored.items()}
        assert placed == {"library": LINEAR, "consumer": LINEAR, "rollout": LINEAR}, placed
        shown = _just(routed, bench, "plans", "project", "show", home, "--json", seconds=120)
        assert shown.returncode == 0, shown.stdout + shown.stderr
        metadata = json.loads(shown.stdout)["items"][0]["item"]["metadata"]
        assert plan_store.MEMBERS_KEY not in metadata, metadata
        views = [
            (str(view["task"]), str(view["projection"]).partition(":")[0])
            for view in metadata[PUBLIC_PROJECTIONS]["tasks"]
        ]
        assert views == [(str(stored["library"]["id"]), BOARD)], views
        assert f"`just orchestrate {home}`" in reported, reported
        project = _home_project(bench.linear, home)
        documents = [held for held in bench.linear.documents() if held["project"]]
        assert [held["project"]["id"] for held in documents] == [project["id"]], documents
        assert "orchestrator.plan-budgets" in project["metadata"], project["metadata"]
        # One neutral project and the public task's view on the board, none of whose text
        # names a private one.
        assert [issue.title for issue in BOARD_FIXTURE.created] == [
            PUBLIC_WORK,
            MIXED[0].title,
        ], [issue.title for issue in BOARD_FIXTURE.created]
        for issue in BOARD_FIXTURE.created:
            assert _private_named(f"{issue.title}\n{issue.body}") == [], issue

        recopied = _just(routed, bench, "copy-plan", f"{AUTHORING}:{name}", "--to", LINEAR)
        assert recopied.returncode == 0, recopied.stdout + recopied.stderr
        assert [held["id"] for held in bench.linear.projects()] == [project["id"]], (
            bench.linear.projects()
        )
    # llmlint: ignore-end[e2e_not_mocked]
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]  # noqa: E501


ONE_VOTER = (
    Node(NodeId("library"), "feat: add the record client to the library"),
    Node(
        NodeId("consumer"),
        "feat: consume the record client",
        (NodeId("library"),),
        repository=HIDDENCO,
        commits=True,
    ),
    Node(NodeId("rollout"), "feat: roll the client out behind its flag", (NodeId("consumer"),)),
)


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]  # noqa: E501
# The same placement and reason as the journeys above.
# llmlint: ignore-block[e2e_not_mocked] The same three doubled boundaries as the journeys above.
@COPIES_THE_TRACKED_TREE
@pytest.mark.xdist_group("linear-routed")
def test_a_plan_whose_only_repository_naming_task_routes_lands_whole_in_linear(
    routed: Routed, tmp_path: Path, oneharness_bin: str
) -> None:
    """A plan whose one repository-naming task is hiddenco's is all Linear's.

    Its hiddenco task is the only item naming a repository, so the store's own copy onto
    `plans` puts the home where that task routes, and the two tasks naming no repository go
    with the home: nothing lands on the board.
    """
    with _gated(tmp_path, oneharness_bin, routed) as bench:
        name = f"test-{os.getpid()}-one-voter"
        project = _draft(bench, name, ONE_VOTER)

        copied = _just(routed, bench, "plans", "project", "copy", project, "--to", BOARD, "--json")

        assert copied.returncode == 0, copied.stdout + copied.stderr
        landed = {
            str(entry["source"]): str(entry["destination"])
            for entry in json.loads(copied.stdout)["items"]
            if entry.get("destination")
        }
        assert landed[project].startswith(f"{LINEAR}:"), landed
        tasks = {source: to for source, to in landed.items() if source != project}
        assert sorted(tasks) == sorted(f"{project}/{node.id}" for node in ONE_VOTER), landed
        assert all(to.startswith(f"{LINEAR}:") for to in tasks.values()), landed
        assert len(bench.linear.issues()) == len(ONE_VOTER), bench.linear.issues()
        assert BOARD_FIXTURE.created == [], [issue.title for issue in BOARD_FIXTURE.created]
    # llmlint: ignore-end[e2e_not_mocked]
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]  # noqa: E501


def _copy_budget(node: Node, change: str) -> ModernBudget:
    """A modern budget record ``node``'s task owns, of the ``change`` a design document lists."""
    return ModernBudget(
        id=BudgetId(f"{node.id}-{change}-copy-seconds"),
        name=f"{node.id} {change} copy time",
        schema_version=2,
        source=Source.TELEMETRY,
        check_runtime_seconds=1,
        basis="design",
        # Each budget is the repository's whose task owns it: a public task's record
        # naming a private repository is refused at the public board.
        repository=node.repository,
        file="apps/web/budgets.yaml",
        file_change=change,
        measure="elapsed seconds of loopback copy",
        inner_measure_reason="",
        unit="seconds",
        direction="max",
        threshold=60,
        workload="One loopback fixture copy.",
        evidence="Synthetic routing fixture; whole record-read runtime estimated at 1 s.",
        command="Read the fixture copy timing record without launching another copy.",
    )


def _budgeted_answers(node: Node) -> dict[str, object]:
    """The answers ``node``'s task is regenerated from once it owns a budget."""
    return {
        "what": f"{COMMIT_MARKER} Name it {node.id}." if node.commits else "Reply with done.",
        "why": "Exercise routing with a modern budget record.",
        "acceptance_criteria": [
            "The route is exercised end to end.",
            "Every claim the dispatch makes about the finished work is true of the "
            "tree as it finally stands.",
        ],
    }


def _budgets_of(body: str) -> str:
    """The `## Budgets` section of ``body``, up to the next section's heading."""
    assert "## Budgets\n" in body, body
    return "## Budgets\n" + body.split("## Budgets\n", 1)[1].split("\n## ", 1)[0]


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]  # noqa: E501
# The same placement and reason as the journeys above.
# llmlint: ignore-block[e2e_not_mocked] The same three doubled boundaries as the journeys above.
@COPIES_THE_TRACKED_TREE
@pytest.mark.xdist_group("linear-routed")
def test_a_mixed_plan_whose_public_task_carries_a_budget_copies_it_to_both_destinations(
    routed: Routed, tmp_path: Path, oneharness_bin: str
) -> None:
    """A public task's budget travels on its view, under a plain heading and no raw HTML.

    The task's budget section renders each schema-2 budget under `### Budget <id>`, plain
    Markdown a public view can carry, so a plan naming public and private repositories whose
    public task owns a budget copies whole: its view on `plans` carries the heading, and its
    canonical task in the private home carries the whole section.
    """
    from project_fixtures import owning

    with _gated(tmp_path, oneharness_bin, routed, public=[THIS_REPOSITORY]) as bench:
        name = f"test-{os.getpid()}-public-budget"
        project = _draft(bench, name, MIXED)
        library = MIXED[0]
        budget = asdict(_copy_budget(library, "add"))
        owning(
            AUTHORING,
            name,
            library.id,
            [budget],
            bench.environment,
            answers=_budgeted_answers(library),
        )

        copied = _just(routed, bench, "plans", "project", "copy", project, "--to", BOARD)

        said = copied.stdout + copied.stderr
        assert copied.returncode == 0, f"the public task's budget was not copied:\n{said}"
        heading = f"### Budget {budget['id']}\n\n- **Name:** {budget['name']}\n"
        (view,) = [issue for issue in BOARD_FIXTURE.created if issue.title == library.title]
        shown = _budgets_of(view.body)
        assert heading in shown, shown
        assert re.search(r"<[A-Za-z/!?]", shown) is None, shown
        for issue in BOARD_FIXTURE.created:
            assert _private_named(f"{issue.title}\n{issue.body}") == [], issue
        (held,) = bench.linear.projects()
        home = QualifiedProjectId(f"{LINEAR}:{held['id']}")
        canonical = _items(routed, bench, home)[library.id]
        assert str(canonical["id"]).startswith(f"{LINEAR}:"), canonical["id"]
        # As Linear stores Markdown, which the double rewrites the way Linear does.
        section = stored_form(plan_budgets.render_section([budget]).strip("\n"), LINEAR_REWRITES)
        content = str(canonical["item"]["content"])
        assert section in content, content
    # llmlint: ignore-end[e2e_not_mocked]
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]  # noqa: E501


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]  # noqa: E501
# The same placement and reason as the journeys above.
# llmlint: ignore-block[e2e_not_mocked] The same three doubled boundaries as the journeys above.
@COPIES_THE_TRACKED_TREE
@pytest.mark.xdist_group("linear-routed")
def test_modern_budget_links_are_recomputed_after_a_routed_copy(
    routed: Routed, tmp_path: Path, oneharness_bin: str
) -> None:
    """Real routing creates current Linear destinations from authoring for a mixed plan.

    Every task of a plan holding a private one is canonical in the private home, so the
    budgets each task owns — the public task's as well as the private one's — link to that
    task's Linear issue, with no fragment, whatever anchor the writer prints for them.
    """
    from project_fixtures import owning

    with _gated(tmp_path, oneharness_bin, routed, public=[THIS_REPOSITORY]) as bench:
        name = f"test-{os.getpid()}-modern-budget-links"
        project = _draft(bench, name, MIXED)
        # One budget added and one changed, the two `file_change`s a design document lists
        # and links; an unchanged one is presentation-filtered out of the section these links
        # are in.
        library, consumer = MIXED[0], MIXED[1]
        owning(
            AUTHORING,
            name,
            library.id,
            [asdict(_copy_budget(library, "add"))],
            bench.environment,
            answers=_budgeted_answers(library),
        )
        owning(
            AUTHORING,
            name,
            consumer.id,
            [asdict(_copy_budget(consumer, "change"))],
            bench.environment,
            answers=_budgeted_answers(consumer),
        )
        answers = no_budgets([HIDDENCO, THIS_REPOSITORY])
        answers.update(schema_version=2, checklist=[], repo_wide_effects=[])
        budgeted(AUTHORING, name, answers, bench.environment)
        _scripted(bench, name, MIXED)
        try:
            finished = _just(
                routed, bench, "finish-plan", str(_brief(bench, project)), "--name", name
            )
            assert finished.returncode == 0, finished.stdout + finished.stderr
            (home,) = HOME_LINE.findall(finished.stdout + finished.stderr)
            printed = subprocess.run(
                [str(routed.checkout / ".venv/bin/python"), "-m", "orchestrator.plan_budgets"]
                + [home],
                cwd=routed.checkout,
                env=_reading(routed, bench),
                text=True,
                capture_output=True,
                check=False,
            )
            assert printed.returncode == 0, printed.stderr
            writer = json.loads(printed.stdout)
            locations = {
                node: held["item"]["location"]
                for node, held in _items(routed, bench, QualifiedProjectId(home)).items()
            }
            hosts = set()
            for budget in writer["budgets"]:
                destination = locations[budget["node"]]
                assert budget["location"] == destination.get("url", destination.get("path"))
                assert "section_url" not in budget
                assert str(bench.authoring) not in budget["location"]
                hosts.add(budget["location"].split("/")[2])
            # One budget is the public task's and one the hiddenco task's, both canonical in
            # Linear, and each prints its heading's anchor.
            assert hosts == {"linear.app"}, writer["budgets"]
            assert sorted(budget["node"] for budget in writer["budgets"]) == [
                consumer.id,
                library.id,
            ], writer["budgets"]
            for budget in writer["budgets"]:
                assert budget["schema_version"] == 2
                assert budget["anchor"] == plan_budgets.budget_anchor(budget["id"]), budget
            # Re-render using current destination answers; no source-host fragment persists.
            base = _design_answers(MIXED)
            supplied = bench.tmp_path / "destination-answers.json"
            supplied.write_text(json.dumps({**base, **writer}), encoding="utf-8")
            resolved = subprocess.run(
                [str(routed.checkout / "node_modules/.bin/onepipeline"), "template", "resolve"]
                + ["design-doc", "--json", "--template-root", str(routed.checkout / "templates")],
                cwd=routed.checkout,
                env=bench.environment,
                text=True,
                capture_output=True,
                check=True,
            )
            rendered = subprocess.run(
                [str(ONETASKGRAPH_BIN), "template", "render", "--template-loader", "-"]
                + ["--answers", str(supplied), "--no-interactive"],
                input=resolved.stdout,
                cwd=routed.checkout,
                env=bench.environment,
                text=True,
                capture_output=True,
                check=True,
            ).stdout
            assert "## Budgets\n" in rendered, rendered
            for index, budget in enumerate(writer["budgets"], 1):
                assert f"[{index}]: {budget['location']}\n" in rendered
            assert "#user-content-" not in rendered, rendered
        finally:
            _just(routed, bench, "stop", f"{name}-design", seconds=60)
    # llmlint: ignore-end[e2e_not_mocked]
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]  # noqa: E501


#: The incident's shape: three tasks naming the organization's repository, each a worker's,
#: and one `kind: human` node naming none, which a person attests while the work runs. The
#: ledger posts what the intake records, which is the edge the design document's graph shows.
ROAD = (
    Node(NodeId("intake"), "feat: record the intake form", repository=HIDDENCO, commits=True),
    Node(
        NodeId("ledger"),
        "feat: post the intake to the ledger",
        (NodeId("intake"),),
        repository=HIDDENCO,
        commits=True,
    ),
    Node(
        NodeId("notify"), "feat: notify the clinic of the intake", repository=HIDDENCO, commits=True
    ),
    Node(NodeId("signoff"), "the clinic's consent to the intake rollout", human=True),
)

#: The status a run reads as once every node is `done`.
SETTLED = "SETTLED"


def _reading_of(routed: Routed, bench: Bench, run: str) -> dict[str, object]:
    """The engine's own reading of ``run``, as `just status --json` prints it."""
    read = _just(routed, bench, "status", run, "--json", "--no-providers", seconds=120)
    assert read.returncode == 0, read.stdout + read.stderr
    reading = json.loads(read.stdout)
    assert isinstance(reading, dict), read.stdout
    return reading


class Road(NamedTuple):
    """Everything one plan's road did, read back once it ended."""

    bench: Bench
    name: str
    home: QualifiedProjectId
    approved: subprocess.CompletedProcess[str]
    launched: subprocess.CompletedProcess[str]
    run: str
    attested: subprocess.CompletedProcess[str]
    status: subprocess.CompletedProcess[str]
    results: subprocess.CompletedProcess[str]
    #: How many Linear requests had been logged at the end of each phase.
    marks: dict[str, int]
    requests: list[dict[str, object]]
    board_mutations: list[str]
    #: The organization's bare origin the three workers' changes landed on.
    origin: Path
    #: The approved document's native id, a person's edit to it, and the launch after.
    document: str
    relaunched: subprocess.CompletedProcess[str]


def _minted(runs: Path, launch: subprocess.Popen[str]) -> str:
    """The one run a launch minted under its own runs root, waited for rather than guessed."""
    deadline = time.monotonic() + e2e_timeout(180)
    while time.monotonic() < deadline:
        minted = sorted(held.name for held in runs.iterdir()) if runs.is_dir() else []
        if minted:
            assert len(minted) == 1, f"this launch's own runs root holds several runs: {minted}"
            return minted[0]
        if launch.poll() is not None:
            stdout, stderr = launch.communicate()
            raise AssertionError(f"the launch minted no run:\n{stdout}{stderr}")
        time.sleep(0.2)
    raise AssertionError(f"no run was minted under {runs}")


def _at_the_turn_gate(launch: subprocess.Popen[str], gate: Path) -> None:
    """Wait until the launch's first worker turn is held at the paid-model boundary."""
    deadline = time.monotonic() + e2e_timeout(300)
    while not (gate / TURN_GATE_REACHED).exists():
        if launch.poll() is not None:
            stdout, stderr = launch.communicate()
            raise AssertionError(f"the launch ended before any worker turn: {stdout}{stderr}")
        assert time.monotonic() < deadline, f"no worker turn reached the gate at {gate}"
        time.sleep(0.2)


def _settled(routed: Routed, bench: Bench, run: str) -> dict[str, object]:
    """The run's reading once it reads `SETTLED`, or as it stands at the deadline."""
    deadline = time.monotonic() + e2e_timeout(600)
    while True:
        reading = _reading_of(routed, bench, run)
        if reading.get("word") == SETTLED or time.monotonic() > deadline:
            return reading
        time.sleep(1)


@pytest.fixture(scope="module")
def road(
    routed: Routed, tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str
) -> Iterator[Road]:
    """Drive one plan of the incident's shape from its first copy to its last write-back.

    `just finish-plan` reviews, documents and copies it; `just copy-plan` re-copies it
    unchanged; `just approve-design` and `just orchestrate` run over the Linear home it
    reported, once. The three workers commit and land on the organization's repository, the
    sign-off is attested over the run's channel while the driver drives the run, as the
    person who took it attests it, and the run settles. Linear's request log is marked at
    the end of each phase. Then a person edits the approved document in Linear, and the
    project is launched again.
    """
    tmp_path = tmp_path_factory.mktemp("road")
    with _gated(tmp_path, oneharness_bin, routed) as bench:
        name = f"road-{os.getpid()}"
        marks: dict[str, int] = {}
        home, _ = _finished(routed, bench, name, ROAD, rendered=True)
        marks["first copy"] = len(bench.linear.requests())
        recopied = _just(routed, bench, "copy-plan", f"{AUTHORING}:{name}")
        assert recopied.returncode == 0, recopied.stdout + recopied.stderr
        marks["unchanged re-copy"] = len(bench.linear.requests())
        approved = _just(routed, bench, "approve-design", home, seconds=300)
        marks["approval"] = len(bench.linear.requests())
        runs = bench.tmp_path / "launched"
        launching = {**bench.environment, "ONEPIPELINE_RUNS_DIR": str(runs)}
        # Launched as a host launches, under the engine's own rendered-only check.
        launching.pop(plan_check.REQUIRE_RENDERED_ENV, None)
        # Every worker turn waits at the paid-model boundary until the sign-off is attested,
        # so the person acts while the driver is still driving the run.
        gate = bench.tmp_path / "turn-gate"
        gate.mkdir()
        launching[TURN_GATE_ENV] = str(gate)
        launch = subprocess.Popen(  # noqa: S603 - this repository's own recipe, in the copy
            ["just", "orchestrate", home, "--dag-graph", "off"],  # noqa: S607 - as an operator types it
            cwd=routed.checkout,
            env=launching,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        reading_bench = bench._replace(environment=launching)
        try:
            run = _minted(runs, launch)
            _at_the_turn_gate(launch, gate)
            envelope = json.dumps({"version": 3, "commands": [{"op": "attest", "ref": "signoff"}]})
            attested = subprocess.run(
                ["just", "channel-reply", run],
                cwd=routed.checkout,
                env=launching,
                input=envelope,
                text=True,
                capture_output=True,
                timeout=e2e_timeout(120),
                check=False,
            )
            (gate / TURN_GATE_RELEASED).touch()
            _settled(routed, reading_bench, run)
            stdout, stderr = launch.communicate(timeout=e2e_timeout(600))
        finally:
            if launch.poll() is None:
                launch.kill()
                launch.communicate()
        launched = subprocess.CompletedProcess(launch.args, launch.returncode, stdout, stderr)
        requests = bench.linear.requests()
        board_mutations = _board_mutations()
        status = _just(routed, reading_bench, "status", run, "--no-providers", seconds=120)
        results = _just(routed, reading_bench, "results", run, seconds=120)
        (document,) = [
            held
            for held in bench.linear.documents()
            if held["project"] == {"id": home.partition(":")[2]}
        ]
        # llmlint: ignore-block[tests_mirror_real_usage] A person edits the document in Linear's
        # own app, which no interface of this host reaches; the double's `edit()` is that act,
        # which the node's criterion names, and the launch gate that must refuse it is the real one.
        bench.linear.edit(
            str(document["id"]), str(document["content"]) + "\nA person's late addition.\n"
        )
        # llmlint: ignore-end[tests_mirror_real_usage]
        relaunched = _just(routed, reading_bench, "orchestrate", home, "--dag-graph", "off")
        yield Road(
            bench=reading_bench,
            name=name,
            home=home,
            approved=approved,
            launched=launched,
            run=run,
            attested=attested,
            status=status,
            results=results,
            marks=marks,
            requests=requests,
            board_mutations=board_mutations,
            origin=tmp_path / "identity" / "origin.git",
            document=str(document["id"]),
            relaunched=relaunched,
        )


def _node_of(linear: LinearBoard) -> dict[str, str]:
    """Each Linear issue's native id, by the plan node it carries."""
    return {str(held["id"]): str(held["metadata"]["onepipeline.id"]) for held in linear.issues()}


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]  # noqa: E501
# The same placement and reason as the journeys above: every journey below reads the one
# module-scoped road, which is driven once.
# llmlint: ignore-block[e2e_not_mocked] The same three doubled boundaries as the journeys above,
# and the remote host's decisioning at the seam `onevcs` publishes for it, because the
# organization's repository is on no host.
@COPIES_THE_TRACKED_TREE
@pytest.mark.xdist_group("linear-routed")
def test_the_roads_home_tasks_and_document_are_stored_in_linear_and_nothing_on_the_board(
    road: Road,
) -> None:
    """(1) The home, the human node, the three tasks and the document; no `plans` item."""
    assert road.home.startswith(f"{LINEAR}:"), road.home
    native = road.home.partition(":")[2]
    assert [held["id"] for held in road.bench.linear.projects()] == [native]
    issues = road.bench.linear.issues()
    assert sorted(_node_of(road.bench.linear).values()) == sorted(node.id for node in ROAD)
    assert all(held["project"] == {"id": native} for held in issues), issues
    (signoff,) = [held for held in issues if held["metadata"]["onepipeline.id"] == "signoff"]
    assert signoff["metadata"]["onepipeline.kind"] == "human", signoff["metadata"]
    assert "onetaskgraph.repositories" not in signoff["metadata"], signoff["metadata"]
    assert road.document in {str(held["id"]) for held in road.bench.linear.documents()}
    assert BOARD_FIXTURE.created == [], [issue.title for issue in BOARD_FIXTURE.created]
    assert road.board_mutations == [], road.board_mutations


@COPIES_THE_TRACKED_TREE
@pytest.mark.xdist_group("linear-routed")
def test_the_roads_approval_is_recorded_on_the_linear_document(road: Road) -> None:
    """(2) `just approve-design` over the Linear home exits 0 and records on that document."""
    assert road.approved.returncode == 0, road.approved.stdout + road.approved.stderr
    (document,) = [
        held for held in road.bench.linear.documents() if str(held["id"]) == road.document
    ]
    assert design_approval.RECORD_KEY in document["metadata"], document["metadata"]


@COPIES_THE_TRACKED_TREE
@pytest.mark.xdist_group("linear-routed")
def test_the_roads_one_launch_dispatches_lands_and_settles_every_node_done(road: Road) -> None:
    """(3) Past the gate and the rendered-only check, every worker landed and the person attested.

    Each worker's turn is the paid-model double committing its node's file, which the
    organization's origin then carries on its base.
    """
    said = road.launched.stdout + road.launched.stderr
    assert road.launched.returncode == 0, said
    assert json.loads(road.launched.stdout.strip().splitlines()[-1])["settlement"] == "complete"
    assert '"state":"applied"' in road.attested.stdout.replace(" ", ""), road.attested
    for node in ROAD:
        if node.human:
            continue
        assert f"graph:{node.id}" in said and "node-dispatched" in said, said
        shown = subprocess.run(
            ["git", "--git-dir", str(road.origin), "show", f"main:{node.id}.md"],
            text=True,
            capture_output=True,
            check=False,
        )
        assert shown.stdout == f"{node.id}\n", shown.stderr
    for node in ROAD:
        if node.human:
            # A human node's stream says only `waiting`: the attestation that settles it is
            # the run's record, which its results view reads.
            assert re.search(rf"graph:{node.id}\s+node-settled waiting", said), said
            assert re.search(rf"^\s+{node.id}\s+done\b", road.results.stdout, re.M), (
                road.results.stdout
            )
            continue
        settled = re.findall(rf"graph:{node.id}\s+node-settled (\w+)", said)
        assert settled and settled[-1] == "done", f"{node.id} settled {settled}"


@COPIES_THE_TRACKED_TREE
@pytest.mark.xdist_group("linear-routed")
def test_every_linear_task_of_the_road_reads_the_state_the_mapping_names_for_done(
    road: Road,
) -> None:
    """(4) Each task's stored state is `Done`, the state the tracked mapping writes `done` as."""
    states = {
        str(held["metadata"]["onepipeline.id"]): held["state"]["name"]
        for held in road.bench.linear.issues()
    }
    assert states == {node.id: LINEAR_DONE for node in ROAD}, states


@COPIES_THE_TRACKED_TREE
@pytest.mark.xdist_group("linear-routed")
def test_the_status_and_results_views_report_the_road_settled_with_every_node_done(
    road: Road,
) -> None:
    """(5) Both views read the run `SETTLED`, each node `done`."""
    assert road.status.returncode == 0, road.status.stderr
    assert re.search(rf"^{road.run}\s+{SETTLED}\s+4/4 done", road.status.stdout, re.M), (
        road.status.stdout
    )
    assert road.results.returncode == 0, road.results.stderr
    for node in ROAD:
        assert re.search(rf"^\s+{node.id}\s+done\b", road.results.stdout, re.M), road.results.stdout


@COPIES_THE_TRACKED_TREE
@pytest.mark.xdist_group("linear-routed")
def test_a_persons_edit_to_the_approved_linear_document_refuses_the_next_launch(
    road: Road,
) -> None:
    """(c) The launch gate names the document a person changed since it was approved."""
    said = road.relaunched.stdout + road.relaunched.stderr
    print(said)
    assert road.relaunched.returncode != 0, said
    assert road.document in said, said
    assert "node-dispatched" not in said, said


def _state_names(linear: LinearBoard) -> dict[str, str]:
    """Each workflow state and project status the double serves, by id, read off its wire."""
    asked = urllib.request.Request(
        linear.endpoint,
        data=json.dumps({"query": "query States { organization { id } }"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(asked, timeout=30) as answered:  # noqa: S310 - loopback
        data = json.loads(answered.read())["data"]
    (team,) = data["teams"]["nodes"]
    states = [*team["states"]["nodes"], *data["projectStatuses"]["nodes"]]
    return {str(state["id"]): str(state["name"]) for state in states}


def _writes_back(request: Mapping[str, object], names: Mapping[str, str]) -> str | None:
    """The status a Linear write moves its item to, `metadata` for none, or None for a read.

    Named by the state the write sets, or by the status its projected metadata records when it
    sets none.
    """
    operation = str(request["operation"])
    if not operation.endswith(("Update", "Create", "Delete")):
        return None
    variables = request["variables"]
    assert isinstance(variables, dict)
    written = variables.get("input") or {}
    state = written.get("stateId") or written.get("statusId")
    if state:
        return names.get(str(state), str(state))
    description = str(written.get("description") or written.get("content") or "")
    slot = re.search(r"<!-- onetaskgraph.metadata `(.+?)` -->", description, re.S)
    projected = json.loads(slot[1]).get("onepipeline.projected_status") if slot else None
    return str(projected) if projected else "metadata"


@COPIES_THE_TRACKED_TREE
@pytest.mark.xdist_group("linear-routed")
def test_the_roads_linear_requests_are_recorded_by_phase_node_and_status(road: Road) -> None:
    """Every request Linear logged from the first copy to the last settlement write-back.

    Recorded as the `linear-requests-per-private-plan-road` budget's parts, never asserted
    against a threshold: the copies and the approval by phase, and each request of the run by
    the node whose issue it reads or writes (the plan's home for its project) and by the
    status the write it belongs to carries.
    """
    budget = "linear-requests-per-private-plan-road"
    names = _state_names(road.bench.linear)
    writes = [index for index, request in enumerate(road.requests) if _writes_back(request, names)]
    last = writes[-1] + 1
    marks = road.marks
    phases = {
        "first copy": (0, marks["first copy"]),
        "unchanged re-copy": (marks["first copy"], marks["unchanged re-copy"]),
        "approval": (marks["unchanged re-copy"], marks["approval"]),
    }
    for phase, (start, end) in phases.items():
        record(budget, phase, end - start, unit="requests")
    nodes = _node_of(road.bench.linear)
    native = road.home.partition(":")[2]
    by_node: Counter[str] = Counter()
    by_status: Counter[str] = Counter()
    launch = 0
    pending: list[str] = []
    for request in road.requests[marks["approval"] : last]:
        variables = request["variables"]
        assert isinstance(variables, dict)
        item = str(variables.get("id", ""))
        owner = nodes.get(item) or ("plan home" if item == native else "")
        written = _writes_back(request, names)
        if not owner:
            launch += 1
            continue
        by_node[owner] += 1
        pending.append(owner)
        if written is not None:
            by_status[f"{owner} to {written}"] += len(pending)
            pending = []
    by_status["unpaired reads"] += len(pending)
    record(budget, "launch and run reads", launch, unit="requests")
    for owner, count in sorted(by_node.items()):
        record(budget, f"write-backs of {owner}", count, unit="requests")
    for change, count in sorted(by_status.items()):
        record(budget, f"status change {change}", count, unit="requests", counted=False)
    assert sum(by_node.values()) + launch == last - marks["approval"]
    print(f"{budget}: {last} requests; marks {marks}; nodes {dict(by_node)}; launch {launch}")
    print(f"{budget}: by status {dict(by_status)}")


#: A mixed plan whose public task's own prose names the organization's private repository.
LEAKING = (
    Node(
        NodeId("library"),
        "feat: add the record client to the library",
        repository=THIS_REPOSITORY,
        mentions=f" Mirror the intake client {HIDDENCO} already ships.",
    ),
    Node(
        NodeId("consumer"),
        "feat: consume the record client",
        (NodeId("library"),),
        repository=HIDDENCO,
        commits=True,
    ),
)


@COPIES_THE_TRACKED_TREE
@pytest.mark.xdist_group("linear-routed")
def test_a_public_task_naming_a_private_repository_is_refused_before_either_board_is_written(
    routed: Routed, tmp_path: Path, oneharness_bin: str
) -> None:
    """(d) The store's boundary refusal names the task; neither board takes a write for it."""
    with _gated(tmp_path, oneharness_bin, routed, public=[THIS_REPOSITORY]) as bench:
        name = f"test-{os.getpid()}-leaking"
        project = _draft(bench, name, LEAKING)
        reviewed = _just(routed, bench, "review-plan", project)
        assert reviewed.returncode == 0, reviewed.stdout + reviewed.stderr
        before = len(bench.linear.requests())
        _GitHubFixture.requests.clear()

        copied = _just(routed, bench, "copy-plan", project)

        said = copied.stdout + copied.stderr
        assert copied.returncode != 0, said
        assert f"{project}/library was not written to source {BOARD}" in said, said
        assert "a term of a private repository" in said, said
        written = [
            str(request["operation"])
            for request in bench.linear.requests()[before:]
            if _writes_back(request, {}) is not None
        ]
        assert written == [], written
        assert _board_mutations() == [], _board_mutations()
        assert bench.linear.projects() == [] and bench.linear.issues() == [], (
            bench.linear.projects(),
            bench.linear.issues(),
        )


#: A Linear normalization the store does not declare: it reaches only the design document.
UNDECLARED = Rewrite("undeclared", lambda text: text.replace("one consumer.", "one consumer!"))


@COPIES_THE_TRACKED_TREE
@pytest.mark.xdist_group("linear-routed")
def test_a_copy_linear_stores_differently_lands_and_can_be_approved(
    routed: Routed, tmp_path: Path, oneharness_bin: str
) -> None:
    """(e) One undeclared rewrite: the copy records what Linear stored, and is approved.

    From onetaskgraph 0.12.4 the store takes the content Linear stored from each write's
    own answer rather than predicting it, so a normalization nothing declares no longer
    refuses the copy: the design document lands as Linear stored it, and the approval recipe
    over the Linear home records an approval of that document.
    """
    with _gated(tmp_path, oneharness_bin, routed, rewrites=(*LINEAR_REWRITES, UNDECLARED)) as bench:
        name = f"test-{os.getpid()}-diverged"
        finished = _finishing(routed, bench, name, ALL_HIDDENCO)

        said = finished.stdout + finished.stderr
        assert finished.returncode == 0, said
        assert "stored-content-diverged" not in said, said
        (document,) = bench.linear.documents()
        assert "one consumer!" in str(document["content"]), document
        (project,) = bench.linear.projects()
        approved = _just(routed, bench, "approve-design", f"{LINEAR}:{project['id']}")
        assert approved.returncode == 0, approved.stdout + approved.stderr
        (document,) = bench.linear.documents()
        assert design_approval.RECORD_KEY in document["metadata"], document["metadata"]


#: A Linear normalization the store does not declare that reaches only the tasks' bodies,
#: whose acceptance criteria the design document does not restate.
UNDECLARED_IN_TASKS = Rewrite(
    "undeclared-in-tasks",
    lambda text: text.replace("a test drives it end to end.", "a test drives it end to end!"),
)


@COPIES_THE_TRACKED_TREE
@pytest.mark.xdist_group("linear-routed")
def test_a_task_linear_stores_differently_is_copied_as_linear_stored(
    routed: Routed, tmp_path: Path, oneharness_bin: str
) -> None:
    """A task Linear rewrites is recorded as Linear stored it, and the document follows.

    `just copy-plan` reads the store's report of each task; with the stored form taken from
    Linear's own answer no task entry is `failed`, so the flow goes on to copy the design
    document beside the tasks.
    """
    with _gated(
        tmp_path, oneharness_bin, routed, rewrites=(*LINEAR_REWRITES, UNDECLARED_IN_TASKS)
    ) as bench:
        name = f"test-{os.getpid()}-task-diverged"
        finished = _finishing(routed, bench, name, ALL_HIDDENCO)

        said = finished.stdout + finished.stderr
        assert finished.returncode == 0, said
        assert "stored-content-diverged" not in said, said
        rewritten = [
            issue
            for issue in bench.linear.issues()
            if "a test drives it end to end!" in str(issue["description"])
        ]
        assert rewritten, bench.linear.issues()
        assert len(bench.linear.documents()) == 1, bench.linear.documents()


#: A public plan: six tasks naming two public repositories, with one edge for its graph.
PUBLIC = (
    Node(NodeId("schema"), "feat: add the record schema", repository=THIS_REPOSITORY),
    Node(NodeId("client"), "feat: add the record client", (NodeId("schema"),), THIS_REPOSITORY),
    Node(NodeId("docs"), "feat: document the record client", repository=THIS_REPOSITORY),
    Node(NodeId("service"), "feat: serve the records", repository=SECOND_PUBLIC),
    Node(NodeId("cache"), "feat: cache the served records", repository=SECOND_PUBLIC),
    Node(NodeId("metrics"), "feat: count the served records", repository=SECOND_PUBLIC),
)
#: The spikes whose reports the public plan holds beside its design document.
PUBLIC_SPIKES = ("spike-listing", "spike-cache")


def _spike_report(bench: Bench, name: str, spike: str) -> None:
    """Store ``spike``'s report in the plan's project, as a spike writes it."""
    answers = bench.tmp_path / f"{spike}-report.json"
    answers.write_text(
        json.dumps(
            {
                "spike": spike,
                "branch": f"{spike}-branch",
                "harness": f"`{spike}.sh` at the branch's root; run `sh {spike}.sh`.",
                "method": "Twenty pages of the real listing at 2,000 nodes.",
                "candidates": [],
                "findings": [],
            }
        ),
        encoding="utf-8",
    )
    resolved = subprocess.run(
        [str(ENGINE_BIN), "template", "resolve", "spike-report", "--json"]
        + ["--template-root", str(REPO_ROOT / "templates")],
        cwd=REPO_ROOT,
        env=bench.environment,
        text=True,
        capture_output=True,
        check=True,
    )
    report = f"{name}-{spike}-report"
    stored = subprocess.run(
        [str(ONETASKGRAPH_BIN), "document", "create", AUTHORING, "--project", name]
        + ["--title", report, "--id", report, "--template-loader", "-"]
        + ["--answers", str(answers), "--no-interactive"],
        cwd=REPO_ROOT,
        env=bench.environment,
        input=resolved.stdout,
        text=True,
        capture_output=True,
        check=False,
    )
    assert stored.returncode == 0, stored.stdout + stored.stderr


@COPIES_THE_TRACKED_TREE
@pytest.mark.xdist_group("linear-routed")
def test_one_public_copys_boundary_processes_are_recorded(
    routed: Routed, tmp_path: Path, oneharness_bin: str
) -> None:
    """Every `onevcs boundary` process one `just finish-plan` of a public plan starts.

    Recorded as the `boundary-invocations-per-public-copy` budget's parts, inspect apart from
    check and each check with the items it screened, never asserted against a threshold.
    """
    budget = "boundary-invocations-per-public-copy"
    with _gated(tmp_path, oneharness_bin, routed, public=[THIS_REPOSITORY, SECOND_PUBLIC]) as bench:
        name = f"test-{os.getpid()}-public"
        project = _draft(bench, name, PUBLIC)
        for spike in PUBLIC_SPIKES:
            _spike_report(bench, name, spike)
        _scripted(bench, name, PUBLIC)
        # Only what the finish-plan starts is counted, not what drafting the plan read.
        bench.boundaries.unlink(missing_ok=True)
        try:
            finished = _just(
                routed, bench, "finish-plan", str(_brief(bench, project)), "--name", name
            )
        finally:
            _just(routed, bench, "stop", f"{name}-design", seconds=60)
        said = finished.stdout + finished.stderr
        assert finished.returncode == 0, said
        (home,) = HOME_LINE.findall(said)
        assert home.startswith(f"{BOARD}:"), home
        documents = _just(
            routed, bench, "plans", "document", "list", "--project", home, "--json", seconds=120
        )
        assert documents.returncode == 0, documents.stdout + documents.stderr
        assert len(json.loads(documents.stdout)["items"]) == 1 + len(PUBLIC_SPIKES)
        recorded = [
            json.loads(line)
            for line in bench.boundaries.read_text(encoding="utf-8").splitlines()
            if line
        ]
    verbs = Counter(str(entry["argv"][1]) for entry in recorded)
    assert set(verbs) <= {"inspect", "check"}, verbs
    screened = [_screened(str(entry["input"])) for entry in recorded if entry["argv"][1] == "check"]
    record(budget, "inspect processes", verbs["inspect"], unit="processes")
    record(budget, "check processes", verbs["check"], unit="processes", items=sum(screened))
    repositories = Counter(
        str(json.loads(str(entry["input"])).get("repository"))
        for entry in recorded
        if entry["argv"][1] == "inspect"
    )
    for repository, count in sorted(repositories.items()):
        record(budget, f"inspect of {repository}", count, unit="processes", counted=False)
    for index, items in enumerate(screened, 1):
        record(budget, f"check {index}", 1, unit="processes", counted=False, items=items)
    print(f"{budget}: {dict(verbs)}; inspects {dict(repositories)}; items per check {screened}")


def _screened(handed: str) -> int:
    """How many items one `onevcs boundary check` was handed to screen.

    The store hands each check one item's terms — its title, metadata and repositories — as
    one document, or several items' as a list of them.
    """
    document = json.loads(handed)
    return len(document) if isinstance(document, list) else 1


# llmlint: ignore-end[e2e_not_mocked]
# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split, test_tiers_split_by_project_not_by_marker]  # noqa: E501
