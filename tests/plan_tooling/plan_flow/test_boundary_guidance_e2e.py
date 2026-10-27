"""A plan spanning public and private repositories is guided; every other plan pays nothing.

The user wants private work to inform public libraries through generic examples, with no
private name, path, identifier or content reaching public output, and wants a plan that
stays inside one visibility to pay no prompt cost for it. So this module holds two
properties side by side, each through the interface a manager or a planner actually uses:

* **Single-visibility prompts are byte-identical to what they were before the boundary
  guidance existed.** The goldens under `boundary_golden/` were captured from the renders
  of the tree *before* the guidance landed — the planner task `just plan` writes for a
  brief with no `Repositories:` line, and for one whose `Repositories:` line names only
  public or only private repositories; and every node task the `plan-task` template
  renders, through the pinned store, for a generated 100-node all-public and all-private
  plan, together with the dispatch appendix alone. Each journey compares the render with
  its golden byte for byte, whitespace included, so one added byte — a blank line among
  them — fails it. **A golden is never regenerated to absorb a difference**: these
  journeys write no golden, and a mismatch leaves the observed render beside the test's
  own temporary directory for a person to read. By the user's ruling this is a
  correctness property rather than a budget, so nothing here is registered as one.
* **A brief whose `Repositories:` line spans visibilities is handed the engine's own
  guidance**, exactly as `onepipeline boundary guidance` prints it, after the placement
  note; and a plan task rendered with the engine's mixed `boundary` context carries the
  no-private-reference criterion on a public node and the boundary note in its appendix.

Visibility is the `onevcs` registry's answer, so every journey runs against a scratch
registry of its own whose rules declare each synthetic owner public or private. The
identities are synthetic and neutral; no real repository is named. Only the paid model is
stood in, at the provider boundary, exactly as `test_plan_recipe_e2e.py` stands it in.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] The edge is the module's
placement: every journey here drives `just plan`, or renders through the `templates/` and
the pinned store that planning launch reads, so `plan-tooling-plan-flow:test` — keyed on
those scripts, templates, `config/` and `orchestrator/` — is the edge each already sits
behind (`tests/plan_tooling/AGENTS.md`), and a project of its own would be keyed on the
same files.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import NamedTuple

import boundary_home
import pytest
import short_state
from fake_backend import PROMPT_LOG_ENV
from project_fixtures import helper, register_stand_in
from published_tools import ONETASKGRAPH_BIN
from scratch_identity import PLANNING_FLOW_ORIGIN, seeded
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: Where the goldens live: captured once, from the renders of the tree before the boundary
#: guidance existed, and never rewritten by anything in this suite.
GOLDEN = Path(__file__).resolve().parent / "boundary_golden"

#: The stand-ins for the paid model, and the guard that refuses every identity that seam
#: cannot reach.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: The pinned engine, whose own guidance a mixed brief is handed and whose `template
#: resolve plan-task` every node task here is rendered through.
ENGINE = REPO_ROOT / "node_modules" / ".bin" / "onepipeline"

#: The aliases `scripts/plan.sh` resolves this repository's identity through, seeded
#: against a scratch registry so no launch reaches this host's own.
PUBLICATION_ALIAS = "ai-orchestrator"
EXECUTION_ALIAS = "ai-orchestrator-isolated"

#: A launching session stated rather than inherited, and what an enclosing dispatch would
#: otherwise decide for these launches.
LAUNCHING_SESSION = "e2e-boundary-guidance"
INHERITED_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: Synthetic, neutral identities: two public repositories of one owner and two private
#: repositories of another. The registry's rules, not their names, make them what they are.
PUBLIC_OWNER = "example-public-org"
PRIVATE_OWNER = "example-private-org"
PUBLIC_REPOSITORIES = (
    f"github.com/{PUBLIC_OWNER}/open-widget",
    f"github.com/{PUBLIC_OWNER}/open-gadget",
)
PRIVATE_REPOSITORIES = (
    f"github.com/{PRIVATE_OWNER}/quiet-harbor",
    f"github.com/{PRIVATE_OWNER}/still-lantern",
)

#: The rules the scratch registry runs under: this repository's planning-flow identity as
#: the seed installs it, and one rule per synthetic owner declaring its visibility, so
#: `onevcs boundary inspect` answers from the rule and never from a host.
RULES = f"""version: 4
trailer_prefix: Orchestrator-
rules:
  - match: {{host: github.com, owner: nickderobertis}}
    publication: local-direct
    approvals: none
    visibility: private
  - match: {{host: github.com, owner: {PUBLIC_OWNER}}}
    publication: local-direct
    approvals: none
    visibility: public
  - match: {{host: github.com, owner: {PRIVATE_OWNER}}}
    publication: local-direct
    approvals: none
    visibility: private
default:
  publication: local-direct
  approvals: none
"""

#: The brief every planner launch here is made from, with ``{repositories}`` standing for
#: the optional line — absent, or naming the repositories a case names.
BRIEF = """## What
Decide whether the paginated listing's cursor is an opaque token or a node id.

Plan project: authoring:cursor-shape
{repositories}
## Why
The browser view cannot deep-link to a page until that is settled.

## Acceptance criteria
- The cursor's shape and its type are stated.
"""


class Brief(NamedTuple):
    """One brief a planner is launched on, and the golden its planner task is held to."""

    name: str
    repositories: Sequence[str]
    golden: str | None
    #: Whether the launch runs beside a doubled `onevcs` whose `boundary inspect` answers
    #: off its own schema — the published CLI doubled below the seam under test.
    off_schema: bool = False


#: The briefs. The three single-visibility ones are held to their goldens; the mixed one
#: has none, because what it gains is the subject of its own journey.
NO_LINE = Brief("no-repositories", (), "planner-task.no-repositories.md")
ALL_PUBLIC = Brief(
    "public-repositories", PUBLIC_REPOSITORIES, "planner-task.public-repositories.md"
)
ALL_PRIVATE = Brief(
    "private-repositories", PRIVATE_REPOSITORIES, "planner-task.private-repositories.md"
)
MIXED = Brief("mixed-repositories", (PUBLIC_REPOSITORIES[0], PRIVATE_REPOSITORIES[0]), None)
#: A public repository beside one no registry here holds, which `onevcs` cannot answer for
#: and the engine counts private: the brief is mixed, and the launch says why.
UNREGISTERED = "github.com/example-unlisted-org/stray-notes"
UNANSWERED = Brief("unregistered-repository", (PUBLIC_REPOSITORIES[0], UNREGISTERED), None)
#: A registered repository no rule declares a visibility for, which `onevcs` answers
#: `unknown`: private to the engine, so beside a public one the brief is mixed, silently.
UNRULED = "github.com/example-unruled-org/plain-ledger"
UNKNOWN = Brief("unknown-repository", (PUBLIC_REPOSITORIES[0], UNRULED), None)
#: The all-public brief again, launched beside an `onevcs` whose answer is off its schema:
#: no answer, so never read as public, and the brief gains nothing.
OFF_SCHEMA = Brief("off-schema-answer", PUBLIC_REPOSITORIES, ALL_PUBLIC.golden, off_schema=True)
BRIEFS = (NO_LINE, ALL_PUBLIC, ALL_PRIVATE, MIXED, UNANSWERED, UNKNOWN, OFF_SCHEMA)

#: The doubled `onevcs`: `boundary inspect` answers a visibility its schema does not
#: declare, and every other verb is the real one's.
OFF_SCHEMA_ONEVCS = """#!/usr/bin/env bash
if [ "${{1-}} ${{2-}}" = "boundary inspect" ]; then
    cat >/dev/null
    printf '{{"visibility": "secret"}}\\n'
    exit 0
fi
exec {real} "$@"
"""


def _brief_text(brief: Brief) -> str:
    line = f"Repositories: {', '.join(brief.repositories)}\n" if brief.repositories else ""
    return BRIEF.format(repositories=line)


def _run(
    *command: str, environment: dict[str, str], seconds: float = 120, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run one real command from this checkout."""
    return subprocess.run(
        list(command),
        cwd=REPO_ROOT,
        env=environment,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _registry(root: Path) -> dict[str, str]:
    """A scratch registry holding this flow's identity and the four synthetic ones."""
    identity = seeded(
        root, publication=PUBLICATION_ALIAS, execution=EXECUTION_ALIAS, origin=PLANNING_FLOW_ORIGIN
    )
    environment = {**os.environ, "ONEVCS_HOME": str(identity.home), **identity.environment}
    for origin in (*PUBLIC_REPOSITORIES, *PRIVATE_REPOSITORIES, UNRULED):
        register_stand_in(identity.home, origin, root / "stand-ins" / origin.replace("/", "-"))
    rules = root / "boundary.rules.yml"
    rules.write_text(RULES, encoding="utf-8")
    applied = _run("onevcs", "rules", "apply", "--base", str(rules), environment=environment)
    assert applied.returncode == 0, f"the scratch rules were not installed:\n{applied.stderr}"
    for origin, expected in (
        [(name, "public") for name in PUBLIC_REPOSITORIES]
        + [(name, "private") for name in PRIVATE_REPOSITORIES]
        + [(UNRULED, "unknown")]
    ):
        inspected = _run(
            "onevcs",
            "boundary",
            "inspect",
            "--input",
            "-",
            "--json",
            environment=environment,
            stdin=json.dumps({"repository": origin}),
        )
        assert json.loads(inspected.stdout) == {"visibility": expected}, inspected.stderr
    return environment


def _launch_environment(tmp_path: Path, oneharness_bin: str) -> dict[str, str]:
    environment = _registry(tmp_path)
    for name in INHERITED_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    environment[PROMPT_LOG_ENV] = str(tmp_path / "turns.jsonl")
    environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp_path))
    return environment


def _held_to_golden(observed: str, golden: str, scratch: Path) -> None:
    """Fail unless ``observed`` is the golden ``golden``'s bytes, leaving it for a reader."""
    kept = scratch / f"observed.{golden}"
    kept.write_text(observed, encoding="utf-8")
    path = GOLDEN / golden
    assert path.is_file(), f"no golden at {path}; the observed render is at {kept}"
    expected = path.read_text(encoding="utf-8")
    assert observed == expected, (
        f"the render differs from the golden captured before the boundary guidance "
        f"existed; compare {kept} with {path} (never regenerate the golden to absorb it)"
    )


class Planned(NamedTuple):
    """One `just plan` launch per brief: its planner task, and how the launch ended."""

    tasks: dict[str, str]
    launches: dict[str, subprocess.CompletedProcess[str]]
    scratch: Path
    guidance: str


@pytest.fixture(scope="module")
def planned(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Planned]:
    """Launch a planner on each brief through the real recipe, and keep each planner task."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("boundary-guidance")
    environment = _launch_environment(tmp_path, oneharness_bin)
    guidance = _run(str(ENGINE), "boundary", "guidance", environment=environment)
    assert guidance.returncode == 0, guidance.stderr
    tasks: dict[str, str] = {}
    launches: dict[str, subprocess.CompletedProcess[str]] = {}
    runs: list[str] = []
    try:
        real = shutil.which("onevcs", path=environment["PATH"])
        assert real is not None, "onevcs is not on the launch's PATH"
        doubled = tmp_path / "off-schema-bin"
        doubled.mkdir()
        (doubled / "onevcs").write_text(OFF_SCHEMA_ONEVCS.format(real=real), encoding="utf-8")
        (doubled / "onevcs").chmod(0o755)
        for brief in BRIEFS:
            run = f"boundary-{brief.name}-{os.getpid()}"
            runs.append(run)
            written = tmp_path / f"{brief.name}.md"
            written.write_text(_brief_text(brief), encoding="utf-8")
            launch = _run(
                "just",
                "plan",
                str(written),
                "--name",
                run,
                "--no-design-doc",
                environment=(
                    # llmlint: ignore[e2e_not_mocked] The published `onevcs` CLI is doubled for its `boundary inspect` answer alone, below the recipe under test, which tests/AGENTS.md sanctions.  # noqa: E501
                    {**environment, "PATH": f"{doubled}{os.pathsep}{environment['PATH']}"}
                    if brief.off_schema
                    else environment
                ),
                seconds=300,
            )
            launches[brief.name] = launch
            plan = Path(environment["ONEPIPELINE_RUNS_DIR"]) / run / "plan.json"
            if launch.returncode == 0 and plan.is_file():
                (node,) = json.loads(plan.read_text(encoding="utf-8"))["tasks"]
                tasks[brief.name] = node["task"]
        yield Planned(tasks, launches, tmp_path, guidance.stdout)
    finally:
        for run in runs:
            _run("just", "stop", run, environment=environment, seconds=60)


@pytest.mark.parametrize("brief", [NO_LINE, ALL_PUBLIC, ALL_PRIVATE], ids=lambda brief: brief.name)
def test_a_single_visibility_planner_task_is_byte_identical_to_its_golden(
    planned: Planned, brief: Brief
) -> None:
    """Without a line, and with one naming one visibility, the planner gains no byte."""
    launch = planned.launches[brief.name]
    assert launch.returncode == 0, f"the launch failed:\n{launch.stdout}\n{launch.stderr}"
    assert brief.golden is not None
    _held_to_golden(planned.tasks[brief.name], brief.golden, planned.scratch)


def test_a_mixed_brief_hands_its_planner_the_engines_own_guidance(planned: Planned) -> None:
    """A brief spanning visibilities ends with the engine's guidance, after the placement note."""
    launch = planned.launches[MIXED.name]
    assert launch.returncode == 0, f"the launch failed:\n{launch.stdout}\n{launch.stderr}"
    task = planned.tasks[MIXED.name]
    without = planned.tasks[NO_LINE.name]
    guidance = planned.guidance.strip()
    assert guidance, "the pinned engine printed no boundary guidance"
    # Everything the single-visibility planner is told, the mixed one is told first: the
    # manager's brief (with its own line), the checklist and the placement note.
    placement_end = without.rstrip().rsplit("\n", 1)[-1]
    assert placement_end in task, "the mixed planner's task does not carry the placement note"
    head, _, tail = task.partition(placement_end)
    assert guidance in tail, "the engine's guidance is not after the placement note"
    assert guidance not in head
    # Its first paragraph is the one-line summary every engine refusal appends, so the
    # planner and a refused `just check-plan` are given the same advice.
    summary = json.loads(
        _run(str(ENGINE), "boundary", "guidance", "--json", environment=dict(os.environ)).stdout
    )["summary"]
    assert summary in task
    for name in (NO_LINE, ALL_PUBLIC, ALL_PRIVATE):
        assert summary not in planned.tasks[name.name]


def test_a_repository_onevcs_cannot_answer_for_is_counted_private_and_said(
    planned: Planned,
) -> None:
    """Beside a public repository, an unregistered one makes the brief mixed, and says so."""
    launch = planned.launches[UNANSWERED.name]
    assert launch.returncode == 0, f"the launch failed:\n{launch.stdout}\n{launch.stderr}"
    assert planned.guidance.strip() in planned.tasks[UNANSWERED.name]
    assert f"onevcs could not say whether '{UNREGISTERED}' is public" in launch.stderr
    assert "counted private" in launch.stderr


def test_an_unknown_visibility_is_counted_private_without_a_word(planned: Planned) -> None:
    """`unknown` is an answer, private to the engine: beside a public repository, mixed."""
    launch = planned.launches[UNKNOWN.name]
    assert launch.returncode == 0, f"the launch failed:\n{launch.stdout}\n{launch.stderr}"
    assert planned.guidance.strip() in planned.tasks[UNKNOWN.name]
    assert "could not say whether" not in launch.stderr


def test_an_answer_off_the_schema_is_never_read_as_public(planned: Planned) -> None:
    """Both repositories are public by rule, but the answers say nothing: no guidance."""
    launch = planned.launches[OFF_SCHEMA.name]
    assert launch.returncode == 0, f"the launch failed:\n{launch.stdout}\n{launch.stderr}"
    for repository in PUBLIC_REPOSITORIES:
        assert f"onevcs could not say whether '{repository}' is public" in launch.stderr
    assert OFF_SCHEMA.golden is not None
    _held_to_golden(planned.tasks[OFF_SCHEMA.name], OFF_SCHEMA.golden, planned.scratch)


#: The visibilities `scripts/plan.sh` reads an answer as, spelled where it reads them.
READ_VISIBILITIES = re.compile(r"visibility not in \(([^)]*)\)")


def test_the_visibilities_the_launch_reads_are_the_ones_onevcs_declares() -> None:
    """The answer's field and its values are `onevcs boundary schema`'s own, as installed."""
    schema = _run("onevcs", "boundary", "schema", "--json", environment=dict(os.environ))
    assert schema.returncode == 0, schema.stderr
    answer = json.loads(schema.stdout)["inspect"]["output"]
    assert answer["required"] == ["visibility"]
    declared = {choice["const"] for choice in answer["$defs"]["Visibility"]["oneOf"]}
    script = (REPO_ROOT / "scripts" / "plan.sh").read_text(encoding="utf-8")
    (spelled,) = READ_VISIBILITIES.findall(script)
    assert set(re.findall(r'"(\w+)"', spelled)) == declared


#: The scale of a generated plan: the user's ruling names 100 nodes, enough that a byte a
#: single-visibility plan gains per node would be paid a hundred times over.
NODES = 100


def _answers(index: int) -> dict[str, object]:
    """The answers node ``index`` of a generated plan is rendered with: varied, as a
    planner's are, so the goldens cover a node with and without its optional sections."""
    answers: dict[str, object] = {
        "what": f"Change the listing's page {index} so it reads its cursor from the query.",
        "why": f"An operator cannot reach page {index} of the node listing today.",
        "acceptance_criteria": [
            f"The listing's page {index} answers a valid cursor and refuses an invalid one.",
            "A request-level test drives both paths end to end.",
            "Every claim the dispatch makes about the finished work is true of the tree as "
            "it finally stands.",
        ],
    }
    if index % 3 == 0:
        answers["additional_info"] = f"The page size of page {index} is the view's to choose."
    if index % 5 == 0:
        answers["spikes"] = [
            {
                "spike": "spike-cursor",
                "report": "spike-cursor-report",
                "branch": "host/plan/spike-cursor",
            }
        ]
    return answers


class Rendered(NamedTuple):
    """A generated plan's node tasks, in node order, and the scratch they were kept in."""

    tasks: list[str]
    scratch: Path


def _template_environment(root: Path, *, private: bool = False) -> dict[str, str]:
    """The store a generated plan is rendered into, its repositories' visibility answered.

    The tracked document switches the store's public boundary on, so a task is classified by
    the repositories it names, through a state root registering each synthetic one with its
    visibility; a plan of private ones is written, as one must be, to a source declared
    private.
    """
    environment = boundary_home.environment(
        {key: value for key, value in os.environ.items() if not key.startswith("ONETASKGRAPH_")},
        boundary_home.registered_home(
            root.with_name(f"{root.name}-registry"),
            public=PUBLIC_REPOSITORIES,
            private=PRIVATE_REPOSITORIES,
        ),
    )
    environment["ONEPIPELINE_TEMPLATE_ROOT"] = str(REPO_ROOT / "templates")
    environment["ONETASKGRAPH_SOURCES__BOUNDARYGOLDEN__PLUGIN"] = "local-md"
    environment["ONETASKGRAPH_SOURCES__BOUNDARYGOLDEN__CONFIG__ROOT"] = str(root)
    if private:
        environment["ONETASKGRAPH_SOURCES__BOUNDARYGOLDEN__VISIBILITY"] = "private"
    return environment


def _generated(tmp_path: Path, repositories: Sequence[str]) -> Rendered:
    """Create a ``NODES``-node plan whose every task is rendered by the pinned store.

    The way a planner creates one: the pinned engine's `template resolve plan-task` piped
    into the store's own `task create`, each task naming one of ``repositories``. The
    bodies are read back through the store, in node order.
    """
    root = tmp_path / "store"
    root.mkdir()
    private = set(repositories) <= set(PRIVATE_REPOSITORIES)
    environment = _template_environment(root, private=private)
    loader = tmp_path / "loader.json"
    resolved = _run(
        str(ENGINE), "template", "resolve", "plan-task", "--json", environment=environment
    )
    assert resolved.returncode == 0, resolved.stderr
    loader.write_text(resolved.stdout, encoding="utf-8")
    created = _run(
        str(ONETASKGRAPH_BIN),
        "project",
        "create",
        "boundarygolden",
        "--id",
        "generated",
        "--title",
        "generated",
        *(["--classification", "private"] if private else []),
        environment=environment,
    )
    assert created.returncode == 0, created.stderr
    for index in range(1, NODES + 1):
        answers = tmp_path / f"answers-{index}.json"
        answers.write_text(json.dumps(_answers(index)), encoding="utf-8")
        task = _run(
            str(ONETASKGRAPH_BIN),
            "task",
            "create",
            "boundarygolden",
            "--template-loader",
            str(loader),
            "--answers",
            str(answers),
            "--no-interactive",
            "--project",
            "generated",
            "--title",
            f"feat: page {index:03d} of the listing",
            "--repository",
            repositories[index % len(repositories)],
            "--metadata",
            f'onepipeline.id="node-{index:03d}"',
            environment=environment,
        )
        assert task.returncode == 0, task.stderr
    listed = _run(
        str(ONETASKGRAPH_BIN),
        "task",
        "list",
        "--source",
        "boundarygolden",
        "--project",
        "generated",
        "--limit",
        "1000",
        "--json",
        environment=environment,
    )
    assert listed.returncode == 0, listed.stderr
    items = sorted(
        (record["item"] for record in json.loads(listed.stdout)["items"]),
        key=lambda item: str(item["metadata"]["onepipeline.id"]),
    )
    assert len(items) == NODES
    assert {repository for item in items for repository in item["repositories"]} == set(
        repositories
    )
    return Rendered([str(item["content"]) for item in items], tmp_path)


#: The separator the node goldens join their bodies with: a line no rendering carries, so a
#: body's own trailing whitespace is part of what is compared.
SEPARATOR = "\n\x1e-- node --\x1e\n"


@pytest.mark.parametrize(
    ("repositories", "golden"),
    [
        (PUBLIC_REPOSITORIES, "node-tasks.public.md.gz"),
        (PRIVATE_REPOSITORIES, "node-tasks.private.md.gz"),
    ],
    ids=["all-public", "all-private"],
)
def test_a_generated_single_visibility_plans_node_tasks_are_byte_identical_to_their_golden(
    tmp_path: Path, repositories: Sequence[str], golden: str
) -> None:
    """Every one of a 100-node single-visibility plan's tasks renders as it did before."""
    rendered = _generated(tmp_path, repositories)
    observed = SEPARATOR.join(rendered.tasks)
    kept = tmp_path / f"observed.{golden}"
    kept.write_bytes(gzip.compress(observed.encode("utf-8"), mtime=0))
    path = GOLDEN / golden
    assert path.is_file(), f"no golden at {path}; the observed renders are at {kept}"
    expected = gzip.decompress(path.read_bytes()).decode("utf-8").split(SEPARATOR)
    assert len(expected) == NODES
    for index, (body, held) in enumerate(zip(rendered.tasks, expected, strict=True), start=1):
        assert body == held, (
            f"node {index:03d}'s task differs from the golden captured before the boundary "
            f"guidance existed; the observed renders are at {kept} (never regenerate the "
            f"golden to absorb it)"
        )


def _appendix(environment: dict[str, str], *variables: str) -> subprocess.CompletedProcess[str]:
    return _run(
        str(ONETASKGRAPH_BIN),
        "template",
        "render",
        str(REPO_ROOT / "templates" / "dispatch-appendix.md"),
        "--search-path",
        str(REPO_ROOT / "templates"),
        "--no-interactive",
        *(part for variable in variables for part in ("--var", variable)),
        environment=environment,
    )


def test_the_dispatch_appendix_alone_is_byte_identical_to_its_golden(tmp_path: Path) -> None:
    """The appendix every dispatched task carries renders as it did, with no context given."""
    rendered = _appendix(_template_environment(tmp_path))
    assert rendered.returncode == 0, rendered.stderr
    _held_to_golden(rendered.stdout, "appendix.md", tmp_path)


#: The criterion a public node of a mixed plan owes, as the template states it.
NO_PRIVATE_REFERENCE = "- Nothing this task produces names anything private:"

#: The heading the appendix's boundary note opens with, and the sentence each side gets.
APPENDIX_NOTE = "### This plan spans public and private repositories"
PUBLIC_SIDE = "This node is public, so everything it produces is public"
PRIVATE_SIDE = "This node is private."


def _mixed_render(tmp_path: Path, clearance: str) -> str:
    """One task rendered by the pinned store with the engine's mixed context answered."""
    environment = _template_environment(tmp_path)
    resolved = _run(
        str(ENGINE), "template", "resolve", "plan-task", "--json", environment=environment
    )
    assert resolved.returncode == 0, resolved.stderr
    answers = tmp_path / f"answers-{clearance}.json"
    answers.write_text(
        json.dumps({**_answers(1), "boundary": {"mixed": True, "clearance": clearance}}),
        encoding="utf-8",
    )
    rendered = _run(
        str(ONETASKGRAPH_BIN),
        "template",
        "render",
        "--template-loader",
        "-",
        "--answers",
        str(answers),
        "--no-interactive",
        "--json",
        environment=environment,
        stdin=resolved.stdout,
    )
    assert rendered.returncode == 0, rendered.stderr
    return str(json.loads(rendered.stdout)["body"])


def test_a_mixed_plans_public_task_owes_the_no_private_reference_criterion(tmp_path: Path) -> None:
    """A public node of a mixed plan carries the criterion, and its appendix the public note."""
    body = _mixed_render(tmp_path, "public")
    criteria = body.split("## Acceptance criteria", 1)[1].split("\n## ", 1)[0]
    assert NO_PRIVATE_REFERENCE in criteria, "the criterion is not among the task's criteria"
    for output in ("files, paths, names or identifiers", "commit messages or branch"):
        assert output in criteria
    assert body.count(APPENDIX_NOTE) == 1
    note = body.split(APPENDIX_NOTE, 1)[1]
    assert PUBLIC_SIDE in note
    assert PRIVATE_SIDE not in note


def test_a_mixed_plans_private_task_gains_the_private_note_and_no_criterion(
    tmp_path: Path,
) -> None:
    """A private node of a mixed plan is told how its work crosses, and owes no such criterion."""
    body = _mixed_render(tmp_path, "private")
    assert NO_PRIVATE_REFERENCE not in body
    note = body.split(APPENDIX_NOTE, 1)[1]
    assert PRIVATE_SIDE in note
    assert PUBLIC_SIDE not in note


def test_a_mixed_task_carries_the_unchanged_appendix_then_the_boundary_note(
    tmp_path: Path,
) -> None:
    """The note is added after the appendix every task carries, which is otherwise unchanged."""
    golden = (GOLDEN / "appendix.md").read_text(encoding="utf-8")
    for clearance in ("public", "private"):
        body = _mixed_render(tmp_path, clearance)
        before, _, after = body.partition(golden)
        assert after, f"the {clearance} task does not carry the appendix byte for byte"
        assert after.lstrip("\n").startswith(APPENDIX_NOTE)


@pytest.mark.parametrize(
    ("line", "said"),
    [
        ("Repositories: example-public-org/open-widget\n", "not a normalized origin"),
        ("Repositories:\n", "naming nothing"),
        (
            f"Repositories: {PUBLIC_REPOSITORIES[0]}\nRepositories: {PRIVATE_REPOSITORIES[0]}\n",
            "declares 2 'Repositories:' lines",
        ),
        (f"Repositories: {PUBLIC_REPOSITORIES[0]},\n", "names repository '' on"),
    ],
    ids=["not-an-origin", "empty", "declared-twice", "trailing-comma"],
)
def test_a_repositories_line_it_cannot_read_is_refused_before_anything_launches(
    tmp_path: Path, line: str, said: str
) -> None:
    """A malformed line is the brief's to fix, so nothing is written and nothing launched."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    environment = dict(os.environ)
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    brief = tmp_path / "malformed.md"
    brief.write_text(BRIEF.format(repositories=line), encoding="utf-8")
    run = f"boundary-malformed-{os.getpid()}"
    refused = _run(
        "just", "plan", str(brief), "--name", run, "--no-design-doc", environment=environment
    )
    assert refused.returncode == 2, f"{refused.stdout}\n{refused.stderr}"
    assert said in refused.stderr, refused.stderr
    assert not (tmp_path / "runs" / run).exists(), "a run was launched from a brief it refused"
