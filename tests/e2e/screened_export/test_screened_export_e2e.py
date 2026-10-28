"""A screened private-to-public export, planned, approved and run to completion on this host.

The user wants private work to inform public repositories while nothing public reveals a
private name, path, identifier or content, and wants that whole journey — planning it,
approving it, running it — to work on the installed host with the plan store's write screening
on. So this drives it end to end through the recipes an operator types, on the released
engine, plan store and `onevcs` this checkout's lock installs:

1. `just plan` over a brief whose `Repositories:` line spans a synthetic private and a
   synthetic public repository: the draft planner writes **one private coordinating plan** in
   the private `authoring` source — a private `publish: "preserve"` producer, an export edge
   and a public consumer, each task keeping its own visibility, nothing set on the plan — and
   the flow's tail reviews it, writes its design document and copies it to the board, whose
   route sends the canonical home to a private board and the public task's neutral view to
   the public `plans` board.
2. `just approve-design` on the home, and `just orchestrate` of it: the producer's
   generalized example is preserved on the private origin, the engine exports it as one
   neutral commit onto the public base, and the public consumer builds on that commit, opens
   its change request and lands; the canonical task's settled status is mirrored to its view.
3. Every public destination is then read for the synthetic private terms and for private
   provenance: every request the public board stand-in received and every record it holds,
   the public repository's history, trees, branches and commit metadata, the consumer's
   worktree branch, the change request's title and body, and the drafter's input.

Two negative journeys hold the screening before anything public is written: a plan whose
public task names a private term is refused by `just copy-plan`, and a copy of the private
design document onto the public source is refused by the store.

Only the paid model is doubled, at the provider boundary: each model turn is a deterministic
fixture (`actions.py`) running the real store, templates and git where the turn runs. The
GitHub wire is the loopback board (`tests/github_board.py`) and the `gh` stand-in
(`tests/e2e/fake_gh.py`); the git transport to the synthetic hosts is a fake `ssh` onto local
bare origins. Every identity is synthetic, and HOME, the configuration home, `ONEVCS_HOME`, the
authoring root, the registry and the runs directory are the journey's own.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] This *is* the edge: the
journey spends a real planning flow and a real run, and is its own Nx project,
`screened-export`, keyed on the planning, copy, approval, boundary and launch files it drives,
which no other project depends on.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

import fake_gh
import onetaskgraph_user_layer
import plan_root_variable
import pytest
import yaml
from export_plan import ExportEdge, PlanSpec, TaskSpec
from fake_backend import PROMPT_LOG_ENV, RUN_ON_MARKER_ENV
from github_board import (
    BOARD,
    CONFIGURED_PROJECT_NUMBER,
    REPOSITORY_NODE_IDS,
    STATUS_OPTIONS,
    _GitHubFixture,
    _Repository,
    _RepositoryNodeId,
    _served,
)
from linear_board import LinearBoard, linear_board
from nx_workspace import share_this_checkouts_install
from project_fixtures import helper, no_budgets
from waits import timeout as e2e_timeout

from orchestrator.plan_budgets import BudgetId, ModernBudget, Source, budget_anchor
from orchestrator.root import REPO_ROOT

pytestmark = [
    pytest.mark.xdist_group("screened-export"),
    pytest.mark.usefixtures("module_tmpdir"),
]

FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
FAKE_GH = helper("fake_gh.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")
ACTIONS = Path(__file__).parent / "actions.py"

#: The synthetic identities: this repository's planning-flow origin, which the planner nodes
#: run in, a public library, and the private service whose work is generalized into it.
PLANNING_ORIGIN = "github.com/nickderobertis/ai-orchestrator"
PUBLIC_ORIGIN = "github.com/example-public-org/open-widget"
PRIVATE_ORIGIN = "github.com/example-private-org/quiet-harbor"
PUBLIC_REPOSITORY = _Repository("example-public-org", "open-widget")
PRIVATE_REPOSITORY = _Repository("example-private-org", "quiet-harbor")

#: Every synthetic private term: the private repository's owner and name, and the private
#: plan's own code name, which its project and its producer's node id carry. None of them may
#: reach anything public, and nothing but the registry's rules makes the first two private.
CODENAME = "zephyrine"
PRIVATE_TERMS = ("quiet-harbor", "example-private-org", CODENAME)

#: The plan the draft planner writes, and the flow's own name.
FLOW = "screened-export"
PROJECT = f"{CODENAME}-cursor-export"
PRODUCER = f"{CODENAME}-generalize-cursor"
CONSUMER = "page-by-an-opaque-cursor"
#: The budget the public consumer owns, recorded in its own public repository, and the
#: in-page link its task writes to that budget's heading: both must reach its public view as
#: written.
CONSUMER_BUDGET = f"{CONSUMER}-page-seconds"
BUDGET_LINK = f"[the paging budget](#{budget_anchor(CONSUMER_BUDGET)})"
SOURCE_DIRECTORY = "examples/cursor"
TARGET_DIRECTORY = "examples/cursor"

#: What each scripted worker's prompt carries, which is how the stand-in tells them apart.
DRAFT_MARKER = "SCREENED-EXPORT-DRAFT"
DESIGN_MARKER = "Write the design document a person reviews the plan"
PRODUCE_MARKER = "Write the generic cursor example under examples/cursor."
CONSUME_MARKER = "Page the listing by the exported cursor example."

#: What the producer generalizes and the consumer builds, neutral as the export requires.
EXAMPLE = "A cursor is an opaque token naming the last record a page returned.\n"
BUILT = "The listing pages by the opaque cursor the example defines.\n"

#: The private tracker the store homes the plan in: a Linear source the journey's user layer
#: declares private, as a host declares an organization's, served by the Linear loopback.
#: Linear files no task in a repository, so a public task's canonical copy stays private there.
PRIVATE_TRACKER = "private-tracker"

#: The policy of each synthetic owner, and its visibility: the planning origin and the private
#: service publish locally, the public library through a change request the host merges.
RULES = """version: 4
trailer_prefix: Orchestrator-
rules:
  - match: {host: github.com, owner: nickderobertis}
    publication: local-direct
    approvals: none
    visibility: public
  - match: {host: github.com, owner: example-public-org}
    publication: change-auto
    approvals: none
    visibility: public
  - match: {host: github.com, owner: example-private-org}
    publication: local-direct
    approvals: none
    visibility: private
default:
  publication: local-direct
  approvals: none
"""

#: The fake `ssh` every synthetic host is reached through: it serves each `owner/name.git`
#: from the bare origin the journey seeded for it, and refuses any other.
FAKE_SSH = """#!/usr/bin/env bash
set -euo pipefail
shift
read -r verb requested <<<"$1"
requested=${{requested#\\'}}
requested=${{requested%\\'}}
case "$requested" in
{cases}
  *) echo "fake-ssh: no origin is served as $requested" >&2; exit 1 ;;
esac
"""

GIT_IDENTITY = ("-c", "user.email=seed@example.invalid", "-c", "user.name=Example Seed")

#: A launching session the journey states rather than inherits.
LAUNCHING_SESSION = "e2e-screened-export"
INHERITED = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "ONEPIPELINE_CHANNEL_ASKER",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    "VIRTUAL_ENV",
    "UV_PROJECT_ENVIRONMENT",
)

#: The answer every review turn of the planning flow gets, and the drafter's body.
PASSING_VERDICT = {"passes": True, "findings": []}
DRAFTED_BODY = {"body": "## What\n\nPage the listing by an opaque cursor.\n"}


def _git(*args: str, cwd: Path | None = None, environment: dict[str, str] | None = None) -> str:
    done = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert done.returncode == 0, f"git {' '.join(args)} failed:\n{done.stderr}"
    return done.stdout


def _checkout(destination: Path) -> Path:
    """A checkout of this journey's declared inputs, running on this checkout's install."""
    declaration = json.loads((Path(__file__).parent / "project.json").read_text())
    for entry in declaration["namedInputs"]["screenedExportWorkspace"]:
        if not entry.startswith("{workspaceRoot}/"):
            continue
        relative = entry.removeprefix("{workspaceRoot}/")
        if relative.endswith("/**/*"):
            directory = relative.removesuffix("/**/*")
            shutil.copytree(
                REPO_ROOT / directory,
                destination / directory,
                ignore=shutil.ignore_patterns("__pycache__"),
                dirs_exist_ok=True,
            )
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, target)
    share_this_checkouts_install(destination)
    installed_lint = shutil.which("llmlint")
    assert installed_lint is not None, "provision the published llmlint before this journey"
    if not (destination / ".venv/bin/llmlint").exists():
        (destination / ".venv/bin/llmlint").symlink_to(installed_lint)
    (destination / ".env").write_text("GH_PROJECTS_TOKEN=fixture-token\n")
    for args in (
        ("init", "-q", "-b", "main"),
        ("remote", "add", "origin", f"git@github.com:{PLANNING_ORIGIN.split('/', 1)[1]}.git"),
        ("add", "."),
        (*GIT_IDENTITY, "commit", "-q", "-m", "test: seed the declared planning interfaces"),
    ):
        _git(*args, cwd=destination)
    return destination


class Origin(NamedTuple):
    """One synthetic hosted repository: its bare origin and its two registered clones."""

    bare: Path
    publication: Path
    execution: Path


def _seed(root: Path, origin: str, files: dict[str, str]) -> Origin:
    owner, name = origin.split("/")[1:]
    bare = root / "origins" / owner / f"{name}.git"
    seed = root / "seeds" / name
    _git("init", "-q", "--bare", "-b", "main", str(bare))
    _git("init", "-q", "-b", "main", str(seed))
    for relative, content in {"README.md": "seed\n", **files}.items():
        (seed / relative).parent.mkdir(parents=True, exist_ok=True)
        (seed / relative).write_text(content, encoding="utf-8")
    _git("add", "-A", cwd=seed)
    _git(*GIT_IDENTITY, "commit", "-qm", "chore: seed", cwd=seed)
    _git("remote", "add", "origin", str(bare), cwd=seed)
    _git("push", "-q", "origin", "main", cwd=seed)
    return Origin(bare, root / "checkouts" / name, root / "checkouts" / f"{name}-execution")


class Bench(NamedTuple):
    """The journey's isolated host: its environment, checkout, origins and scratch."""

    root: Path
    checkout: Path
    environment: dict[str, str]
    origins: dict[str, Origin]
    gh_state: Path
    prompts: Path
    drafter_prompts: Path
    plan_spec: Path
    linear: LinearBoard


def _registry(root: Path, environment: dict[str, str]) -> dict[str, Origin]:
    """Seed the three synthetic origins and register their clones in the journey's registry."""
    origins = {
        PLANNING_ORIGIN: _seed(root, PLANNING_ORIGIN, {}),
        PUBLIC_ORIGIN: _seed(root, PUBLIC_ORIGIN, {"src/listing.md": "A plain listing.\n"}),
        PRIVATE_ORIGIN: _seed(
            root, PRIVATE_ORIGIN, {f"internal/{CODENAME}.md": f"The {CODENAME} cursor.\n"}
        ),
    }
    cases = "".join(
        f'  {shlex.quote(origin.split("/", 1)[1] + ".git")}) exec "$verb" '
        f"{shlex.quote(str(seeded.bare))} ;;\n"
        for origin, seeded in origins.items()
    )
    ssh = root / "fake-ssh"
    ssh.write_text(FAKE_SSH.format(cases=cases), encoding="utf-8")
    ssh.chmod(0o755)
    # llmlint: ignore[e2e_not_mocked] Only the transport to GitHub is substituted.
    environment["GIT_SSH_COMMAND"] = str(ssh)
    for origin, seeded in origins.items():
        for clone in (seeded.publication, seeded.execution):
            _git(
                "clone",
                "-q",
                f"git@github.com:{origin.split('/', 1)[1]}.git",
                str(clone),
                environment=environment,
            )
    manifest = root / "onevcs.checkouts"
    manifest.write_text(
        "".join(f"{seeded.publication}\n{seeded.execution}\n" for seeded in origins.values()),
        encoding="utf-8",
    )
    rules = root / "onevcs.rules.yml"
    rules.write_text(RULES, encoding="utf-8")
    applied = subprocess.run(
        ["just", "repos-apply", "--checkouts", str(manifest), "--rules", str(rules)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )
    assert applied.returncode == 0, f"repos-apply failed:\n{applied.stdout}\n{applied.stderr}"
    for origin, expected in ((PUBLIC_ORIGIN, "public"), (PRIVATE_ORIGIN, "private")):
        inspected = subprocess.run(
            ["onevcs", "boundary", "inspect", "--input", "-", "--json"],
            input=json.dumps({"repository": origin}),
            env=environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )
        assert json.loads(inspected.stdout)["visibility"] == expected, inspected.stdout
    return origins


def _user_layer() -> dict[str, object]:
    """Route the private repository's work to the private tracker, declared private there.

    The tracker is the example layer's organization tracker, as a host declares one, under a
    name of the journey's own and routed from the journey's synthetic private owner.
    """
    tracker = onetaskgraph_user_layer.example()["sources"][
        onetaskgraph_user_layer.ORGANIZATION_PLANS
    ]
    assert tracker["visibility"] == "private", tracker
    return {
        "sources": {
            "plans": {
                "routes": [
                    {"repositories": ["github.com/example-private-org/*"], "to": PRIVATE_TRACKER}
                ]
            },
            PRIVATE_TRACKER: tracker,
        }
    }


def _tracker_key() -> str:
    """The variable the private tracker's credential is read from, as its layer names it."""
    tracker = _user_layer()["sources"]
    assert isinstance(tracker, dict)
    return str(tracker[PRIVATE_TRACKER]["config"]["api_key_env"])


def _tracker_environment(linear: LinearBoard, config_home: Path) -> dict[str, str]:
    """Point the private tracker at ``linear``, as the store resolves the journey's layer."""
    with pytest.MonkeyPatch.context() as patched:
        patched.setenv(onetaskgraph_user_layer.XDG_CONFIG_HOME, str(config_home))
        for name in [name for name in os.environ if name.startswith("ONETASKGRAPH_SOURCES__")]:
            patched.delenv(name)
        return linear.environment(PRIVATE_TRACKER)


def _plan_spec(root: Path, checkout: Path, temporary: Path) -> Path:
    """The plan the scripted draft planner writes: one private coordinating project."""
    spec = PlanSpec(
        project=PROJECT,
        title=f"{CODENAME} cursor export",
        goal="Page the public listing by a cursor generalized from private work",
        budgets={
            **no_budgets([PUBLIC_ORIGIN, PRIVATE_ORIGIN]),
            "schema_version": 2,
            "checklist": [],
            "repo_wide_effects": [],
        },
        export_edges=[
            ExportEdge(
                source=PRODUCER,
                to=CONSUMER,
                source_directory=SOURCE_DIRECTORY,
                target_directory=TARGET_DIRECTORY,
            )
        ],
        tasks=[
            TaskSpec(
                id=PRODUCER,
                title="feat: write a generic cursor example",
                repository=PRIVATE_ORIGIN,
                execution_checkout="quiet-harbor-execution",
                publish="preserve",
                clearance="private",
                what=f"{PRODUCE_MARKER} Generalize the {CODENAME} cursor in quiet-harbor's "
                f"internal/{CODENAME}.md into a neutral example.",
                why="A public listing needs the cursor without the private service.",
                criteria=["The example under examples/cursor names nothing private."],
            ),
            TaskSpec(
                id=CONSUMER,
                title="feat: page the listing by an opaque cursor",
                repository=PUBLIC_ORIGIN,
                execution_checkout="open-widget-execution",
                deps=[PRODUCER],
                clearance="public",
                what=f"{CONSUME_MARKER} Build the listing's paging on the cursor example, "
                f"held to {BUDGET_LINK}.",
                why="Callers page long listings without skipping a record.",
                criteria=["A caller pages the whole listing by its cursor."],
                budgets=[
                    ModernBudget(
                        id=BudgetId(CONSUMER_BUDGET),
                        name="listing page time",
                        schema_version=2,
                        source=Source.TELEMETRY,
                        check_runtime_seconds=1,
                        basis="design",
                        repository=PUBLIC_ORIGIN,
                        file="budgets.yaml",
                        file_change="add",
                        measure="elapsed seconds to page the whole listing",
                        inner_measure_reason="",
                        unit="seconds",
                        direction="max",
                        threshold=5,
                        workload="One listing of a thousand records.",
                        evidence="Synthetic journey fixture; the read is estimated at 1 s.",
                        command="Read the recorded paging time without paging again.",
                    )
                ],
            ),
        ],
    )
    written = root / "plan-spec.json"
    written.write_text(spec.model_dump_json(by_alias=True), encoding="utf-8")
    python = str(checkout / ".venv/bin/python")
    base = [python, str(ACTIONS)]
    scratch = root / "actions"
    commands = {
        DRAFT_MARKER: [base + ["draft", str(written), str(scratch)]],
        # The flow renders the plan's graph under the temporary root its recipes run under,
        # which is this module's own (`tests/short_state.py`).
        DESIGN_MARKER: [base + ["design", PROJECT, str(scratch), "--graph-root", str(temporary)]],
        PRODUCE_MARKER: [
            base
            + [
                "commit",
                f"{SOURCE_DIRECTORY}/README.md",
                str(scratch),
                "--content",
                EXAMPLE,
                "--message",
                "feat: add a generic cursor example",
            ]
        ],
        CONSUME_MARKER: [
            base
            + [
                "commit",
                "src/paging.md",
                str(scratch),
                "--content",
                BUILT,
                "--message",
                "feat: page the listing by an opaque cursor",
            ]
        ],
    }
    (root / "commands.json").write_text(json.dumps(commands), encoding="utf-8")
    return written


@pytest.fixture(scope="module")
def bench(tmp_path_factory: pytest.TempPathFactory, module_tmpdir: Path) -> Iterator[Bench]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    root = tmp_path_factory.mktemp("screened-export")
    monkeypatch = pytest.MonkeyPatch()
    for repository, node in (
        (PUBLIC_REPOSITORY, "R_open_widget"),
        (PRIVATE_REPOSITORY, "R_quiet_harbor"),
    ):
        monkeypatch.setitem(REPOSITORY_NODE_IDS, repository, _RepositoryNodeId(node))
    environment = {key: value for key, value in os.environ.items() if key not in INHERITED}
    for name in list(environment):
        if name.startswith(("ONETASKGRAPH_SOURCES__", "ONEPIPELINE_REQUIRE")):
            del environment[name]
    home = root / "home"
    home.mkdir()
    # The tools' own caches stay where the lock was installed from; everything a run or a
    # store writes lands under the journey's HOME.
    environment.setdefault("UV_CACHE_DIR", str(Path.home() / ".cache" / "uv"))
    environment["HOME"] = str(home)
    environment["XDG_CACHE_HOME"] = str(root / "cache")
    environment["XDG_STATE_HOME"] = str(root / "state")
    environment["ONEVCS_HOME"] = str(root / "onevcs")
    environment["ONEPIPELINE_RUNS_DIR"] = str(root / "runs")
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_LAUNCHER_SESSION"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_LAUNCHER"] = "claude-code"
    # Every commit the run's own machinery makes carries a neutral synthetic identity.
    for role in ("AUTHOR", "COMMITTER"):
        environment[f"GIT_{role}_NAME"] = "Example Host"
        environment[f"GIT_{role}_EMAIL"] = "host@example.invalid"
    origins = _registry(root, environment)
    checkout = _checkout(root / "checkout")
    environment[onetaskgraph_user_layer.XDG_CONFIG_HOME] = str(
        onetaskgraph_user_layer.installed(root / "xdg-config", _user_layer())
    )
    authoring = root / "authoring"
    authoring.mkdir()
    environment[plan_root_variable.name()] = str(authoring)
    environment[plan_root_variable.plugin_name()] = "local-md"
    for source in ("FOLLOW_UP_RUNS", "TEST_FIXTURES", "DRAFTS", "EXAMPLES"):
        local = root / "sources" / source.lower()
        local.mkdir(parents=True)
        environment[f"ONETASKGRAPH_SOURCES__{source}__CONFIG__ROOT"] = str(local)
    environment["ONETASKGRAPH_SECRETS_FILE"] = str(root / "no-secrets.env")
    # The run reads the plan's tasks from its default sources, which the tracked document
    # states and a layer beneath it cannot widen, so the tracker joins them in the environment,
    # as `tests/plan_tooling/plan_flow/test_linear_routed_plans_e2e.py` has it join.
    tracked = yaml.safe_load((REPO_ROOT / "onetaskgraph.yaml").read_text(encoding="utf-8"))
    environment["ONETASKGRAPH_DEFAULT_SOURCES"] = ",".join(
        [*tracked["default_sources"], PRIVATE_TRACKER]
    )
    gh_state = root / "gh-state"
    # The public base is unprotected, so the host merges an armed change as soon as it may.
    gh_state.mkdir()
    (gh_state / fake_gh.MERGE_PERMIT).touch()
    prompts = root / "turns.jsonl"
    drafter_prompts = root / "codex-prompts.jsonl"
    plan_spec = _plan_spec(root, checkout, module_tmpdir)
    # llmlint: ignore-block[e2e_not_mocked] Only the paid provider and the remote host's
    # decisioning are substituted, each at its own seam.
    environment.update(
        {
            "ONEAGENTGRAPH_ONEHARNESS_BIN": str(FAKE_BACKEND),
            "REAL_ONEHARNESS_BIN": str(checkout / ".venv/bin/oneharness"),
            "ONEHARNESS_BIN_CODEX": str(FAKE_CODEX),
            "FAKE_CODEX_ANSWERS": json.dumps([json.dumps(PASSING_VERDICT)]),
            "FAKE_CODEX_PROMPT_LOG": str(drafter_prompts),
            RUN_ON_MARKER_ENV: str(root / "commands.json"),
            PROMPT_LOG_ENV: str(prompts),
            "PATH": f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}",
            "ONEVCS_GH": str(FAKE_GH),
            fake_gh.STATE_ENV: str(gh_state),
            fake_gh.CALLS_ENV: str(root / "gh-calls.jsonl"),
            fake_gh.ORIGINS_ENV: json.dumps(
                {str(PUBLIC_REPOSITORY): str(origins[PUBLIC_ORIGIN].bare)}
            ),
            "PYTHONPATH": os.pathsep.join([str(checkout), str(REPO_ROOT / "tests/e2e")]),
            "UV_NO_SYNC": "1",
        }
    )
    # llmlint: ignore-end[e2e_not_mocked]
    with (
        _served(_GitHubFixture, BOARD, STATUS_OPTIONS, CONFIGURED_PROJECT_NUMBER) as public,
        linear_board() as linear,
    ):
        served = _tracker_environment(
            linear, Path(environment[onetaskgraph_user_layer.XDG_CONFIG_HOME])
        )
        # The tracker's credential reaches every recipe and dispatch the one way a host's does:
        # out of the checkout's gitignored `.env`, by the recipes' own credential loading.
        key = _tracker_key()
        with (checkout / ".env").open("a") as credentials:
            credentials.write(f"{key}={served.pop(key)}\n")
        environment.pop(key, None)
        environment.update(
            {
                **served,
                "GH_PROJECTS_TOKEN": "fixture-token",
                "ONETASKGRAPH_SOURCES__PLANS__CONFIG__ENDPOINT": public,
                "ONETASKGRAPH_SOURCES__PLANS__CONFIG__PACING__MIN_MUTATION_INTERVAL_MS": "0",
            }
        )
        try:
            yield Bench(
                root,
                checkout,
                environment,
                origins,
                gh_state,
                prompts,
                drafter_prompts,
                plan_spec,
                linear,
            )
        finally:
            monkeypatch.undo()


def _just(bench: Bench, *arguments: str, seconds: float = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", *arguments],
        cwd=bench.checkout,
        env=bench.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _brief(bench: Bench) -> Path:
    brief = bench.root / "brief.md"
    brief.write_text(
        f"## What\n\n{DRAFT_MARKER}: page the public listing by a cursor generalized from "
        "private work.\n\n"
        f"Plan project: authoring:{PROJECT}\n\n"
        f"Repositories: {PRIVATE_ORIGIN}, {PUBLIC_ORIGIN}\n\n"
        "## Why\n\nCallers page long listings without skipping a record.\n\n"
        "## Acceptance criteria\n\n- The public listing pages by an opaque cursor.\n",
        encoding="utf-8",
    )
    return brief


def _explained(bench: Bench, done: subprocess.CompletedProcess[str]) -> str:
    """What a recipe said, and what any scripted turn failed on."""
    failures = bench.root / "actions" / "failures.log"
    failed = failures.read_text() if failures.exists() else ""
    return f"{done.stdout[-6000:]}\n{done.stderr[-6000:]}\nscripted turns:\n{failed}"


#: A journal record or its payload, as the engine writes it: an open JSON object whose fields
#: vary by `kind`, of which this journey reads a handful by name and asserts on their values.
JournalRecord = dict[str, Any]


def _journal(bench: Bench, run: str) -> list[JournalRecord]:
    # llmlint: ignore[tests_mirror_real_usage] No view prints a session's branch and worktree.
    path = bench.environment["ONEPIPELINE_RUNS_DIR"] + f"/{run}/events.jsonl"
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


def _settled(bench: Bench, run: str) -> dict[str, JournalRecord]:
    """How each node of ``run`` last settled, by its node id."""
    return {
        str(record["labels"]["node"]): record["payload"]
        for record in _journal(bench, run)
        if record.get("kind") == "node-settled" and record.get("labels", {}).get("node")
    }


def _bare(bench: Bench, origin: str, *args: str) -> str:
    return _git("--git-dir", str(bench.origins[origin].bare), *args)


def _public_history(bench: Bench) -> str:
    """Everything the public origin holds: every ref, every commit's metadata and message,
    and every path and blob any commit reaches."""
    refs = _bare(bench, PUBLIC_ORIGIN, "for-each-ref", "--format=%(refname)")
    log = _bare(bench, PUBLIC_ORIGIN, "log", "--all", "--format=%H %an <%ae> %cn <%ce>%n%B")
    objects = _bare(bench, PUBLIC_ORIGIN, "rev-list", "--all", "--objects")
    blobs = []
    for line in objects.splitlines():
        sha = line.split(" ", 1)[0]
        if _bare(bench, PUBLIC_ORIGIN, "cat-file", "-t", sha).strip() == "blob":
            blobs.append(_bare(bench, PUBLIC_ORIGIN, "cat-file", "-p", sha))
    return "\n".join([refs, log, objects, *blobs])


class Journey(NamedTuple):
    """What one planned, approved and settled screened export left behind."""

    home: str
    run: str
    #: Every private name of this plan that must not reach a public destination: the synthetic
    #: terms, and the private side's own provenance — its project, tasks, tracker, branch,
    #: head and paths.
    private: tuple[str, ...]
    #: Each public destination's whole content, by what it is.
    public: dict[str, str]


@pytest.fixture(scope="module")
def journey(bench: Bench) -> Journey:
    """Plan, approve and run the screened export through the operator's recipes."""
    planned = _just(bench, "plan", str(_brief(bench)), "--name", FLOW, seconds=1800)
    assert planned.returncode == 0, _explained(bench, planned)
    home = re.search(r"its home is (\S+);", planned.stdout + planned.stderr)
    assert home is not None, _explained(bench, planned)
    assert home[1].startswith(f"{PRIVATE_TRACKER}:"), home[1]
    approved = _just(bench, "approve-design", home[1])
    assert approved.returncode == 0, _explained(bench, approved)
    # What the run's drafter is handed is read apart from the planning flow's own reviews,
    # which read the private plan in private.
    planning_prompts = bench.drafter_prompts.with_suffix(".planning.jsonl")
    if bench.drafter_prompts.exists():
        bench.drafter_prompts.rename(planning_prompts)
    bench.environment["FAKE_CODEX_ANSWERS"] = json.dumps([json.dumps(DRAFTED_BODY)])
    launched = _just(
        bench,
        "orchestrate",
        home[1],
        "--dag-graph",
        "off",
        "--success-hook=",
        "--failure-hook=",
        seconds=1800,
    )
    assert launched.returncode == 0, _explained(bench, launched)
    run = PROJECT
    status = _just(bench, "status", run, "--json")
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["word"] == "SETTLED", status.stdout
    journal = _journal(bench, run)
    settled = _settled(bench, run)
    opened = [
        record["payload"]
        for record in journal
        if record.get("kind") == "session-opened" and "worktree" in record["payload"]
    ]
    (project,) = [held for held in bench.linear.projects() if held["id"] == home[1].split(":")[1]]
    private = (
        *PRIVATE_TERMS,
        PROJECT,
        PRODUCER,
        project["title"],
        project["id"],
        PRIVATE_TRACKER,
        *(issue["id"] for issue in bench.linear.issues()),
        *(issue["identifier"] for issue in bench.linear.issues()),
        *(document["id"] for document in bench.linear.documents()),
        "linear.app",
        "authoring:",
        str(settled[PRODUCER]["branch"]),
        str(settled[PRODUCER]["head"]),
        str(bench.origins[PRIVATE_ORIGIN].bare),
        str(bench.root / "authoring"),
        f"internal/{CODENAME}.md",
    )
    drafted = (
        [json.loads(line) for line in bench.drafter_prompts.read_text().splitlines()]
        if bench.drafter_prompts.exists()
        else []
    )
    public = {
        # A lookup the source sends the board stores nothing and is not a public destination;
        # the writes are what the board keeps and shows.
        "public board writes": "\n".join(
            [
                *(
                    json.dumps([request.query, request.variables])
                    for request in _GitHubFixture.requests
                    if request.is_mutation
                ),
                *(
                    json.dumps([request.method, request.path, request.body])
                    for request in _GitHubFixture.rest_requests
                    if request.method != "GET"
                ),
            ]
        ),
        "public board records": "\n".join(
            json.dumps([issue.title, issue.body, issue.status, str(issue.repository), issue.origin])
            for issue in BOARD.issues
        ),
        "public origin": _public_history(bench),
        "public worktree branches": "\n".join(
            str(session["branch"]) for session in opened if session.get("identity") == PUBLIC_ORIGIN
        ),
        "publication payload": (bench.root / "gh-calls.jsonl").read_text(encoding="utf-8"),
        "change request": "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(fake_gh.records(bench.gh_state, str(PUBLIC_REPOSITORY)).iterdir())
        ),
        "drafter input": "\n".join(json.dumps(record) for record in drafted),
    }
    return Journey(home[1], run, private, public)


def test_the_private_producer_preserves_its_generalized_example(
    bench: Bench, journey: Journey
) -> None:
    """The producer's branch is kept on the private origin, carrying only the example, and the
    private base is untouched."""
    settled = _settled(bench, journey.run)
    producer = settled[PRODUCER]
    assert (producer["status"], producer["outcome"]) == ("done", "preserved"), producer
    branch, head = str(producer["branch"]), str(producer["head"])
    assert _bare(bench, PRIVATE_ORIGIN, "rev-parse", f"refs/heads/{branch}").strip() == head
    assert _bare(bench, PRIVATE_ORIGIN, "show", f"{head}:{SOURCE_DIRECTORY}/README.md") == EXAMPLE
    changed = _bare(bench, PRIVATE_ORIGIN, "diff", "--name-only", "main", head).split()
    assert changed == [f"{SOURCE_DIRECTORY}/README.md"], changed
    assert (
        _bare(bench, PRIVATE_ORIGIN, "log", "--format=%s", "main").split("\n")[0] == "chore: seed"
    )


def test_one_neutral_export_commit_reaches_the_public_base_and_the_consumer_builds_on_it(
    bench: Bench, journey: Journey
) -> None:
    """The export is one neutral commit on the public base; the consumer's change starts from
    it, lands, and the public base holds the example and what was built on it."""
    exports = json.loads(
        (Path(bench.environment["ONEPIPELINE_RUNS_DIR"]) / journey.run / "exports.json").read_text()
    )
    (export,) = exports
    exported = str(export["exported_head"])
    shown = _bare(bench, PUBLIC_ORIGIN, "show", "--no-patch", "--format=%P|%an <%ae>|%s", exported)
    parent, author, subject = shown.strip().split("|")
    seed = _bare(bench, PUBLIC_ORIGIN, "rev-list", "--max-parents=0", "main").strip()
    assert parent == seed, shown
    for field in (author, subject):
        assert not any(term in field for term in journey.private), field
    exported_tree = _bare(bench, PUBLIC_ORIGIN, "ls-tree", "-r", "--name-only", exported).split()
    assert sorted(exported_tree) == ["README.md", f"{TARGET_DIRECTORY}/README.md", "src/listing.md"]
    (change,) = [
        json.loads(path.read_text())
        for path in fake_gh.records(bench.gh_state, str(PUBLIC_REPOSITORY)).glob("pr-*.json")
    ]
    assert change["state"] == "MERGED", change
    head = _bare(bench, PUBLIC_ORIGIN, "rev-parse", f"refs/heads/{change['head']}").strip()
    assert _bare(bench, PUBLIC_ORIGIN, "merge-base", "--is-ancestor", exported, head) == ""
    assert _bare(bench, PUBLIC_ORIGIN, "show", f"main:{TARGET_DIRECTORY}/README.md") == EXAMPLE
    assert _bare(bench, PUBLIC_ORIGIN, "show", "main:src/paging.md") == BUILT
    settled = _settled(bench, journey.run)
    assert settled[CONSUMER]["status"] == "done", settled[CONSUMER]


def test_the_public_view_follows_the_canonical_status(bench: Bench, journey: Journey) -> None:
    """The consumer's canonical task in the private home is done, and its one public view on
    `plans` reads done too, under a neutral `Public work` project naming nothing else."""
    canonical = {
        str(issue["metadata"].get("onepipeline.id")): issue for issue in bench.linear.issues()
    }
    assert canonical[CONSUMER]["state"]["name"] == "Done", canonical[CONSUMER]
    assert canonical[PRODUCER]["state"]["name"] == "Done", canonical[PRODUCER]
    created = [issue for issue in BOARD.issues if issue.number > 2]
    views = [issue for issue in created if issue.sub_issues == 0]
    (view,) = views
    assert view.title == "feat: page the listing by an opaque cursor", view
    assert view.status == "Done" and view.state == "CLOSED", view
    assert view.repository == PUBLIC_REPOSITORY, view
    assert view.blocked_by == [], view
    (work,) = [issue for issue in created if issue is not view]
    assert work.title == "Public work", work


def test_the_public_view_carries_its_task_s_budget_anchor_and_link(journey: Journey) -> None:
    """The budget the public task owns travels on its one view: the budget's heading, whose
    slug is the anchor, and the task's in-page link to it, each as the task wrote them."""
    assert journey.home
    (view,) = [issue for issue in BOARD.issues if issue.number > 2 and issue.sub_issues == 0]
    assert f"### Budget {CONSUMER_BUDGET}\n" in view.body, view.body
    assert BUDGET_LINK in view.body, view.body


def test_no_public_destination_carries_a_private_term_or_private_provenance(
    journey: Journey,
) -> None:
    """Every write and record of the public board, the public origin's refs, commits, paths
    and blobs, the consumer's branch, the publication's calls, its change request and the
    drafter's input name nothing of the private side."""
    for destination, held in journey.public.items():
        assert held.strip(), f"{destination} was never observed"
        for name in journey.private:
            assert name not in held, f"{destination} carries the private {name!r}"
    for key in ("depends_on", "member_of", "onetaskgraph.members", "public_projections"):
        assert key not in journey.public["public board records"], key
    # The drafter named the run only by its variable, never by the run's own id.
    assert "$ONEPIPELINE_RUN_ID" in journey.public["drafter input"]


def test_a_public_task_carrying_a_private_term_is_refused_before_the_public_board_is_written(
    bench: Bench, journey: Journey
) -> None:
    """A second plan whose public task's title names the private repository is refused by
    `just copy-plan`, and the public board takes no write."""
    planned = PlanSpec.model_validate_json(bench.plan_spec.read_text())
    producer, consumer = planned.tasks
    named_consumer = consumer.model_copy(
        update={"title": "feat: page the listing as quiet-harbor does"}
    )
    spec = planned.model_copy(
        update={"project": f"{CODENAME}-named-export", "tasks": [producer, named_consumer]}
    )
    named = bench.root / "named-spec.json"
    named.write_text(spec.model_dump_json(by_alias=True), encoding="utf-8")
    scratch = bench.root / "named-actions"
    drafted = subprocess.run(
        [str(bench.checkout / ".venv/bin/python"), str(ACTIONS), "draft", str(named), str(scratch)],
        cwd=bench.checkout,
        env=bench.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )
    assert drafted.returncode == 0, drafted.stderr
    bench.environment["FAKE_CODEX_ANSWERS"] = json.dumps([json.dumps(PASSING_VERDICT)])
    reviewed = _just(bench, "review-plan", f"authoring:{spec.project}")
    assert reviewed.returncode == 0, _explained(bench, reviewed)
    writes = [request for request in _GitHubFixture.requests if request.is_mutation]
    records = [(issue.title, issue.body, issue.status) for issue in BOARD.issues]

    copied = _just(bench, "copy-plan", f"authoring:{spec.project}")

    said = copied.stdout + copied.stderr
    assert copied.returncode != 0, f"a public task naming a private term was copied:\n{said}"
    assert [request for request in _GitHubFixture.requests if request.is_mutation] == writes
    assert [(issue.title, issue.body, issue.status) for issue in BOARD.issues] == records


def test_the_private_design_document_is_refused_by_the_public_source(
    bench: Bench, journey: Journey
) -> None:
    """A copy of the plan's private design document onto `plans` is refused by the store, and
    the public board takes no write."""
    writes = [request for request in _GitHubFixture.requests if request.is_mutation]

    copied = subprocess.run(
        [
            str(bench.checkout / ".venv/bin/onetaskgraph"),
            "document",
            "copy",
            f"authoring:{PROJECT}-design",
            "--to",
            "plans",
        ],
        cwd=bench.checkout,
        env=bench.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )

    assert copied.returncode != 0, f"the private design document was copied:\n{copied.stdout}"
    assert "private" in copied.stderr, copied.stderr
    assert [request for request in _GitHubFixture.requests if request.is_mutation] == writes
