"""A shipped plan launches through `just orchestrate`, settles, and answers the planner.

This repository's whole function is dispatching work, and the only thing that
proves it can is a plan that actually runs. `tests/e2e/test_delegated_recipes_e2e.py`
holds each recipe to the command line it renders; that is a different promise, and
a delegation that renders perfectly can still reach a `onepipeline` that refuses —
which is exactly how a release shipped with no `graphs/dag-scope.yaml` at all and
every plan declaring a schema version the published crate rejects.

So this journey launches one of the shipped examples for real, through the real
recipe, and then asks the run the questions a planner asks it. Everything between
the recipe and the model is real: `onepipeline`'s driver, its continuous
reconciler, its channel and read profiles, the `graphs/` agent-graph configs,
`oneagentgraph`, and the onejudge conversation those compose.
`tests/e2e/fake_backend.py` stands in for the paid model alone, at the `oneharness`
seam where a dispatch reaches it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Literal, NamedTuple, NewType, Required, TypedDict, cast

import pytest
from fake_backend import AGENT_DELAY_ENV, JUDGE_CONFIG_NAME, PROMPT_LOG_ENV, RUN_TASK
from published_surface import surface_of
from shared_dispatch_bar import shared_agent_preamble, shared_completion_bar
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The stand-in for the paid model, named to `oneagentgraph` as its harness.
FAKE_BACKEND = Path(__file__).resolve().parent / "fake_backend.py"

#: The stand-in one layer lower: the paid provider binary itself, named to
#: `oneharness` as codex's. A single-sided `kind: oneharness` member runs oneharness
#: in process, so it spawns no oneharness binary for `FAKE_BACKEND` to be — this is
#: what covers those members instead; see `_environment`.
FAKE_CODEX = Path(__file__).resolve().parent / "fake_codex.py"

#: The shipped example this journey launches. A plan written for this repository's
#: own operators, so a schema or field the published crate stopped accepting fails
#: here rather than the first time a planner types it.
SHIPPED_PLAN = "examples/single-node-direct.plan.json"

#: The run id `onepipeline` mints from that plan's `name`.
SHIPPED_RUN = "scheduler-research"

#: The one shipped lifecycle plan, and the plan schema version this repository
#: writes — read from that file rather than restated, so the two cannot disagree and
#: a bump has one place to happen. The adopted crate still reads older versions
#: beneath this one; that is a courtesy to plan files an operator kept, not a
#: version to write.
SHIPPED_LIFECYCLE_PLAN = REPO_ROOT / "examples/single-node-lifecycle.plan.json"
PLAN_SCHEMA_VERSION: int = json.loads(SHIPPED_LIFECYCLE_PLAN.read_text("utf-8"))["schema_version"]

#: A Conventional Commit subject: `type(optional scope)optional !: summary`.
CONVENTIONAL_COMMIT_SUBJECT = re.compile(r"^[a-z]+(\([^()]+\))?!?: \S.*$")

#: The dag-scope agent graph every run launches, and how `just monitor` labels the
#: envelope stream it produces. `oneagentgraph` suffixes the run's own id, so this
#: is a prefix and the node-scope graph's members do not answer to it.
DAG_SCOPE_GRAPH = "graphs/dag-scope.yaml"
DAG_SCOPE_STREAM = "agent:dag-scope-"

#: The drafting agent graph every run launches, and the response contract its one
#: member answers under. Only reached on a remote lifecycle publication, which this
#: suite does not perform — so what is provable here is that the launch opted into it
#: and that the adopted reader accepts the document.
PR_AUTHOR_GRAPH = "graphs/pr-author.yaml"
PR_AUTHOR_BODY_SCHEMA = "config/pr-author-body.schema.json"

#: A composed drafting task, stated by this journey rather than copied from
#: `onepipeline`. What the member owes is version-independent — its own `task`
#: replaces whatever it is handed, so it must interpolate that back in — and any
#: distinctive text proves it. Quoting the crate's real prompt here would add a
#: second copy of somebody else's contract that nothing reconciles, and would fail
#: on a reword that changed nothing about this graph.
DRAFTING_TASK = "Draft the body for `probe-branch`, which added a health endpoint."

#: How often the launch tells the pacemaker to come due. Short enough that it comes
#: due while this run is still going, which is the only state in which what it does
#: with a turn can be observed at all.
PACEMAKER_INTERVAL_SECONDS = 1

#: The `graphs/dag-scope.yaml` member each recorded turn belongs to. `oneagentgraph`
#: gives every member a scratch directory named after it and pins that member's
#: harness configs inside it, so the `--config` the backend records is the attribution.
#: Since onepipeline 0.3.1 both sides of a member carry one, so this names the member
#: for either; which SIDE a turn is depends on the config's basename, not on having one.
MEMBER_OF_CONFIG = re.compile(r"/members/([^/]+)/")

#: The pacemaker member, whose turn reports and never edits.
PACEMAKER_MEMBER = "check-in"

#: The run's active monitor, and the member whose judgment this graph exists for.
MONITOR_MEMBER = "monitor"

#: The one mutation the pacemaker's own `task` forbids by name. Live edits belong to
#: the monitor, which stays for the run; this member takes one finitely deadlined turn
#: and exits, so an edit it issued would be answered after it stopped watching.
FORBIDDEN_OF_THE_PACEMAKER = "onepipeline reply"

#: The monitor's role, as `personas/orchestrator.yaml` states it. The composed task says
#: only what the run is, so this is the only thing that says what the member is for.
WATCH_ROLE = "Actively monitor one executing tracked graph"

#: The surface kind `scripts/channel-serve.py` raises a monitor's supervisor boundary
#: under. `channel serve` takes a free-form kind here, unlike `onepipeline surface
#: --kind`, so the surface says what it is rather than borrowing the pacemaker's word.
SURFACE_KIND_OF_A_MONITOR = "monitor"

#: The read the monitor's persona tells it to make: the `monitor` profile, which carries
#: each dispatched worker's turns as well as the pipeline's own node events. The whole
#: command rather than the profile name, because naming `monitor` alone would also match
#: the member, the recipe, and the verb.
DETAILED_STREAM_COMMAND = "onepipeline monitor <run-id> --filter monitor"

#: A launching session the journey states rather than inherits. The suite runs
#: inside a dispatch whose own harness session would otherwise decide these
#: assertions, and ownership is the thing under test.
LAUNCHING_SESSION = "e2e-planner-session"

#: Every name a launcher identity reaches `scripts/onepipeline.sh` through, so a
#: journey that states one is not also carrying the enclosing dispatch's.
LAUNCHER_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)


class Launched(NamedTuple):
    """One launched run: the environment it lives in, and how its launch ended.

    Named rather than positional because the two are different things a journey
    reaches for one at a time — `environment` to ask the run another question,
    `launch` to judge the launch itself — and a positional pair reads as neither.
    """

    #: What every later recipe has to be given to see the same run.
    environment: dict[str, str]
    #: The attached `just orchestrate` that settled it.
    launch: subprocess.CompletedProcess[str]
    #: Every effective prompt the run's turns were given, as the fake backend saw
    #: them. Which member each one is is decided by its `--config`, exactly as the
    #: backend decides it.
    prompt_log: Path


class RoutedPersonaRun(NamedTuple):
    """A run that exercises graph overrides and both persona paths."""

    environment: dict[str, str]
    launch: subprocess.CompletedProcess[str]
    worker_config: Path
    judge_config: Path
    prompt_log: Path


class GraphRef(TypedDict):
    origin: str


class GraphHistory(TypedDict):
    refs: list[GraphRef]


class PromptRecord(TypedDict):
    config: str | None
    prompt: str
    system: str


class Surface(TypedDict):
    """The fields of a handed-out surface this suite reads. `onepipeline` owns the rest."""

    kind: str
    message: str


class StreamedEvent(TypedDict):
    """One journal envelope as `onepipeline next` returns it, narrowed to its origin."""

    source: str


class SurfaceRead(TypedDict):
    """`onepipeline next`'s answer: the surface, if any, and the events its profile admits."""

    status: str
    surface: Surface | None
    events: list[StreamedEvent]


class EditCommand(TypedDict, total=False):
    """One live-edit command, as `onepipeline`'s reply envelope carries it.

    `op` is the only field every command has; the rest are the union of what the
    published operations take, which is why they are `total=False`. Stating them
    rather than passing a bare mapping is what makes a command aimed at a field no
    op accepts a type error here instead of a refusal at the engine.
    """

    op: Required[str]
    id: str
    note: str
    ref: str
    reason: str
    deps: list[str]
    dependents: str
    node: PlanNode


class ReplyEnvelope(TypedDict, total=False):
    """The reply envelope, which is closed to exactly these fields.

    `onepipeline` refuses an unknown one whole — verdict and edits with it — so the
    shape is worth stating rather than assembling ad hoc; `author` is what bounds
    which `commands` are allowed at all.
    """

    version: int
    author: str
    completion: bool
    message: str
    reason: str
    commands: list[EditCommand]


class RefusedOnTheGraph(NamedTuple):
    """An in-allowlist command, and the graph-state reason a settled run refuses it.

    Named rather than positional because the two are different claims — what was
    sent, and what makes the refusal the graph's rather than an authority verdict —
    and the distinction is the whole point of the parametrization.
    """

    command: EditCommand
    refusal: str


def _environment(
    tmp_path: Path, oneharness_bin: str, *, session: str = LAUNCHING_SESSION
) -> dict[str, str]:
    """The environment one launched run and its planner views share."""
    environment = dict(os.environ)
    for name in LAUNCHER_ENVIRONMENT:
        environment.pop(name, None)
    # The identity `scripts/onepipeline.sh` derives, stated as the ambient harness
    # variable a real planner session would carry rather than as the derived value:
    # the derivation is what this journey holds.
    environment["CLAUDE_CODE_SESSION_ID"] = session
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # The other half of the same seam, and not redundant with it. Since oneagentgraph
    # 0.2.18 a single-sided `kind: oneharness` member — the `check-in` pacemaker, and
    # `graphs/pr-author.yaml`'s drafter — runs its turn through the oneharness *library*
    # on a thread of the graph process, so no `oneharness` CLI is spawned for it and the
    # substitution above never sees it. A two-party `kind: onejudge` member still spawns
    # one, which is why the fake backend continues to serve every worker, judge, and
    # monitor turn. Left alone, those single-sided members would reach the real paid
    # provider on every launch this suite makes. What is still a process is the
    # provider, and `ONEHARNESS_BIN_CODEX` is oneharness's own per-harness binary
    # override: every one of this repository's configs names `codex` first, so pinning
    # that identity's binary is what keeps a suite run off a paid subscription. It is
    # deliberately weaker than `--mock-harness`, which the two-party path above still
    # uses — measured against oneharness 0.10.1, a mocked harness keeps its mock binary
    # and ignores this variable — so the two seams do not collide.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # Keeps this run's graph scratch, its history, and its sibling state out of the
    # host's, so a journey never reads or reclaims a live dispatch's.
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    return environment


def _just(*args: str, environment: dict, seconds: float = 300) -> subprocess.CompletedProcess[str]:
    """Run one real recipe from this checkout."""
    return subprocess.run(
        ["just", *args],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


@pytest.fixture(scope="module")
def launched(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Launched]:
    """Launch the shipped example once, and hand every question its settled run."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("orchestrate-launch")
    environment = _environment(tmp_path, oneharness_bin)
    prompt_log = tmp_path / "prompts.jsonl"
    environment[PROMPT_LOG_ENV] = str(prompt_log)
    # The pacemaker's shipped period is half an hour and this run settles in seconds,
    # so at the default it would never come due and every claim about what it does
    # with its turn would be vacuous. `--heartbeat-interval` is the published way to
    # say when it comes due, so the journey says it rather than editing the graph.
    launch = _just(
        "orchestrate",
        SHIPPED_PLAN,
        "--heartbeat-interval",
        str(PACEMAKER_INTERVAL_SECONDS),
        environment=environment,
    )
    # The newer graph/pipeline pair can have its already-running orchestrator begin
    # one final reconciliation round just after the attached launcher observes the
    # first complete boundary. Hand tests a quiescent run, as this fixture promises.
    settling = deadline(10)
    while True:
        status = _just("status", SHIPPED_RUN, environment=environment, seconds=60)
        if status.returncode == 0 and "SETTLED" in status.stdout:
            break
        if time.monotonic() >= settling:
            pytest.fail(f"the launched run did not quiesce:\n{status.stdout}\n{status.stderr}")
        time.sleep(0.05)
    try:
        yield Launched(environment, launch, prompt_log)
    finally:
        # A journey that failed mid-run leaves a driver behind; the supported way to
        # end one is the recipe, and it is the owner here.
        _just("stop", SHIPPED_RUN, environment=environment, seconds=60)


@pytest.fixture(scope="module")
def routed_persona_run(
    tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str
) -> Iterator[RoutedPersonaRun]:
    """Launch distinct side configs with one named and one omitted persona."""
    tmp_path = tmp_path_factory.mktemp("routed-persona")
    environment = _environment(tmp_path, oneharness_bin)
    worker_config = tmp_path / "worker.toml"
    judge_config = tmp_path / "judge.toml"
    worker_config.write_bytes((REPO_ROOT / "oneharness.toml").read_bytes())
    judge_config.write_bytes((REPO_ROOT / "oneharness.judge.toml").read_bytes())
    prompt_log = tmp_path / "prompts.jsonl"
    environment[PROMPT_LOG_ENV] = str(prompt_log)
    plan = tmp_path / "routed-persona.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "name": "routed-persona-e2e",
                "concurrency": 1,
                "tasks": [
                    {
                        "id": "named",
                        "persona": "docs-writer",
                        "task": "Report without changing files.",
                    },
                    {
                        "id": "default",
                        "task": "Report without changing files.",
                        "expects_no_diff": True,
                        "deps": ["named"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    launch = _just(
        "orchestrate",
        str(plan),
        "--node-set",
        f"members.worker.agent.oneharness_config={worker_config}",
        "--node-set",
        f"members.worker.judge.oneharness_config={judge_config}",
        environment=environment,
    )
    try:
        yield RoutedPersonaRun(environment, launch, worker_config, judge_config, prompt_log)
    finally:
        _just("stop", "routed-persona-e2e", environment=environment, seconds=60)


def _graph_history(run: str, environment: dict[str, str]) -> GraphHistory:
    shown = subprocess.run(
        ["oneagentgraph", "history", "show", run],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert shown.returncode == 0, shown.stderr
    # The published history command owns this schema; GraphHistory states the
    # two fields this test consumes after a successful command response.
    return cast(GraphHistory, json.loads(shown.stdout))


@pytest.fixture(scope="module")
def dag_scope_members() -> int:
    """How many members `graphs/dag-scope.yaml` declares, counted by its own reader.

    A declaration lookup rather than an observation: `oneagentgraph validate` is how
    this repository's graph documents are checked, and asking it keeps the expected
    count off a literal here and out of a hand-rolled YAML parser.
    """
    validated = subprocess.run(
        ["oneagentgraph", "validate", DAG_SCOPE_GRAPH],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert validated.returncode == 0, f"{DAG_SCOPE_GRAPH} did not validate:\n{validated.stderr}"
    declared = re.search(r"(\d+) member\(s\)", validated.stdout)
    assert declared is not None, f"no member count in:\n{validated.stdout}"
    return int(declared.group(1))


@pytest.mark.xdist_group("orchestrate-launch")
def test_a_shipped_plan_launches_and_settles(launched: Launched) -> None:
    """`just orchestrate` drives a shipped example to a complete settlement."""
    launch = launched.launch
    assert launch.returncode == 0, f"the launch did not settle:\n{launch.stdout}\n{launch.stderr}"
    settlement = json.loads(launch.stdout.strip().splitlines()[-1])
    assert settlement == {"run_id": SHIPPED_RUN, "settlement": "complete"}
    # The attached launch streams the run's merged events on standard error, keeping
    # the settlement record on standard output the only thing a caller has to parse.
    # What is asserted of that live stream is only that it *is* one: an attached
    # launch returns the moment the run settles, so any individual event has no
    # obligation to have reached standard error first, and `node-dispatched` has been
    # observed missing from a launch that settled `complete`.
    assert "run-started" in launch.stderr, launch.stderr

    # What the run actually did is read from the replayed stream, which is the whole
    # settled ledger rather than a race. Read through `--filter monitor` so the
    # dispatched worker's own graph is visible too: without this the assertion is the
    # one it is here to make, that "complete" is not describing an empty run.
    stream = _just(
        "monitor", SHIPPED_RUN, "--filter", "monitor", environment=launched.environment, seconds=60
    )
    assert stream.returncode == 0, stream.stderr
    assert "node-dispatched" in stream.stdout, stream.stdout
    assert "node-settled done" in stream.stdout, stream.stdout


@pytest.mark.xdist_group("orchestrate-launch")
def test_node_overrides_and_named_or_omitted_persona_paths_work(
    routed_persona_run: RoutedPersonaRun,
) -> None:
    """The launch forwards each side config and only the persona a node names.

    Read through `--filter monitor`: the per-node `oneagentgraph` launches this asks
    about are dispatched-agent events, which the default planner profile drops.
    """
    launch = routed_persona_run.launch
    assert launch.returncode == 0, launch.stdout + launch.stderr
    stream = _just(
        "monitor",
        "routed-persona-e2e",
        "--filter",
        "monitor",
        environment=routed_persona_run.environment,
        seconds=60,
    )
    assert stream.returncode == 0, stream.stderr
    node_runs = re.findall(r"agent:(node-scope-\S+) graph-started", stream.stdout)
    assert node_runs, stream.stdout

    # llmlint: ignore[tests_mirror_real_usage] Required proof reads refs omitted by planner views.
    histories = [_graph_history(run, routed_persona_run.environment) for run in node_runs]
    origins = [{ref["origin"] for ref in history["refs"]} for history in histories]
    expected_configs = {
        str(routed_persona_run.worker_config),
        str(routed_persona_run.judge_config),
    }
    assert expected_configs <= origins[0], origins
    # The fake backend writes this JSONL itself; PromptRecord states the one field
    # this test consumes from that test-owned schema.
    # llmlint: ignore[tests_mirror_real_usage] Effective prompts prove more than event labels.
    prompts = [
        cast(PromptRecord, json.loads(line))["prompt"]
        for line in routed_persona_run.prompt_log.read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        "You are an editor who values concision and accuracy" in prompt for prompt in prompts
    )
    assert "graph:default" in stream.stdout
    assert "node-settled done no-changes" in stream.stdout


def test_node_graph_uses_the_generic_base_when_no_persona_is_overridden(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A direct graph invocation needs no persona override."""
    environment = _environment(tmp_path, oneharness_bin)
    environment[PROMPT_LOG_ENV] = str(tmp_path / "prompts.jsonl")
    run = subprocess.run(
        [
            "oneagentgraph",
            "run",
            "graphs/node-scope.yaml",
            "--task",
            "Report without changing files.",
            "--dir",
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    # The fake backend writes this JSONL itself; PromptRecord states the one field
    # this test consumes from that test-owned schema.
    prompts = [
        cast(PromptRecord, json.loads(line))["prompt"]
        for line in (tmp_path / "prompts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any("Verify the requested task against its acceptance criteria" in p for p in prompts)


@pytest.mark.xdist_group("orchestrate-launch")
def test_every_dag_scope_member_starts_with_the_graph(
    launched: Launched, dag_scope_members: int
) -> None:
    """The pacemaker member launches alongside the orchestrator, not only after its interval.

    `graphs/dag-scope.yaml`'s second member is the easy one to ship broken: its
    schedule is half an hour, so a persona ref, an oneharness config, or a schedule
    shape this graph got wrong would first be heard from thirty minutes into a real
    run. `oneagentgraph` starts a scheduled member with the graph rather than at its
    first tick, so `just monitor` — the stream a planner watches a run through —
    reports every one of them within seconds of the launch.

    How many to expect is the graph's own declaration rather than a number written
    here, so a member added to `graphs/dag-scope.yaml` has to start too.

    Read through `--filter monitor`, because a member starting is an `oneagentgraph`
    event and the planner profile this recipe defaults to carries only the pipeline's
    own. The default view is held to that by
    `test_the_planner_profile_is_the_default_and_the_detailed_one_is_reachable`.
    """
    stream = _just(
        "monitor", SHIPPED_RUN, "--filter", "monitor", environment=launched.environment, seconds=60
    )
    assert stream.returncode == 0, stream.stderr
    started = [
        line
        for line in stream.stdout.splitlines()
        if DAG_SCOPE_STREAM in line and line.endswith("member-started")
    ]
    assert len(started) == dag_scope_members, (
        f"{dag_scope_members} dag-scope member(s) are declared but monitor reported "
        f"{len(started)} started:\n{stream.stdout}"
    )


def _recorded_turns(prompt_log: Path) -> list[PromptRecord]:
    """Every turn of a launched run, as the stand-in model was given it."""
    # The fake backend writes this JSONL itself, one object per turn with every field
    # PromptRecord names; it is test-owned on both ends, so the cast states that schema
    # rather than skipping a validation of somebody else's.
    # llmlint: ignore[tests_mirror_real_usage] Effective prompts prove more than event labels.
    return [
        cast(PromptRecord, json.loads(line))
        for line in prompt_log.read_text(encoding="utf-8").splitlines()
    ]


def _turns_of(turns: list[PromptRecord], member: str) -> list[PromptRecord]:
    """The turns `oneagentgraph` pinned to one named dag-scope member."""
    found = []
    for turn in turns:
        config = turn["config"]
        named = MEMBER_OF_CONFIG.search(config) if config else None
        if named is not None and named.group(1) == member:
            found.append(turn)
    return found


@pytest.mark.xdist_group("orchestrate-launch")
def test_the_monitor_watches_the_run_it_no_longer_drives(launched: Launched) -> None:
    """The dag-scope agent is told to watch, from its persona, and nothing is told to drive.

    Since onepipeline 0.4.0 the engine drives its own DAG continuously and the observer
    graph is optional (`--dag-graph`, shipped default `off`). So the question this
    answers is no longer "does anything still drive" but "did the watcher arrive, and did
    it arrive as a watcher": the composed task it was given says only what the run is,
    its system prompt carries the monitoring role from `personas/orchestrator.yaml`, that
    role names the detailed profile it is to read, and the run settled complete without
    any member being told to drive it.
    """
    turns = _recorded_turns(launched.prompt_log)
    # The monitor's agent side may take several turns of one session; every one of them
    # is that member and no other turn of the run may be.
    watching = [turn for turn in turns if WATCH_ROLE in turn["system"]]
    assert watching, "nothing in this run was told to watch it"
    # And the member carrying it is the one the graph declares, read the same way the
    # pacemaker's turns are: a renamed member with the persona still attached would
    # otherwise satisfy every assertion below while the docs named something absent.
    assert _turns_of(turns, MONITOR_MEMBER), (
        f"no turn of this run belonged to a `{MONITOR_MEMBER}` member of "
        f"{DAG_SCOPE_GRAPH}, so the watching role arrived under another name"
    )
    for turn in watching:
        # An agent side, by the one property that distinguishes the two: the judge side
        # is the turn pinned to the judge config. Since onepipeline 0.3.1 an agent side
        # carries `.../oneharness.toml`, so the config's basename is what separates them.
        assert Path(turn["config"] or "").name != JUDGE_CONFIG_NAME, (
            "only an agent side carries the watching role; this turn was pinned to the "
            f"judge config at {turn['config']}"
        )
        named = RUN_TASK.search(turn["prompt"])
        assert named is not None, f"a watching turn named no run:\n{turn['prompt']}"
        assert named.group(1) == SHIPPED_RUN
        assert WATCH_ROLE not in turn["prompt"], (
            "the composed run task carries the monitoring role again, so every member "
            f"taking one is told to monitor:\n{turn['prompt']}"
        )
        # The whole point of the role: it reads the unfiltered stream, not the planner's
        # summary. A monitor told to watch through the default profile would see none of
        # the worker activity it exists to judge.
        assert DETAILED_STREAM_COMMAND in " ".join(turn["system"].split()), (
            f"the monitor is no longer told to read the detailed activity stream:\n{turn['system']}"
        )


@pytest.mark.xdist_group("orchestrate-launch")
def test_the_shared_bar_reaches_a_dispatched_workers_judge_beside_its_task(
    launched: Launched,
) -> None:
    """The one review bar arrives verbatim, next to the criteria it is phrased against.

    The adopted `onepipeline` refuses a plan carrying a node-level `done_when` — proven
    by `test_a_plan_carrying_a_node_level_done_when_is_refused` — so a node's bar is now
    its own `## Acceptance criteria` plus one shared clause in
    `config/onejudge.base.yaml`. That clause does not restate any criterion; it says
    "every acceptance criterion stated in the task is met" and relies wholly on onejudge
    handing it over unchanged *and* showing the judge a transcript that opens with the
    task. Both halves, in one place, or the bar resolves to nothing: a criterion that
    points at a task the judge cannot see is a criterion no judge can apply, and it
    would fail open, accepting whatever the worker last said.

    Read from the prompt the judge was really given rather than from the config, because
    a bar present in the file and not reaching the judge is the failure being excluded.
    It is a real exclusion, not a tautology: point the shipped plan's node at a persona
    whose built-in role declares its own bar — `planner`, `reviewer`, `researcher` —
    and the criterion the judge is handed is that one instead, and this fails.
    """
    shared_bar = shared_completion_bar()
    # `cast` rather than a validating read: this is a plan file this repository ships and
    # `test_every_plan_this_repository_ships_is_one_the_published_crate_accepts` already
    # holds its shape against the launcher. A second schema check here would restate that
    # gate, and the `split` below fails loudly anyway if the task is not the prose it is.
    task = cast(
        str,
        json.loads((REPO_ROOT / SHIPPED_PLAN).read_text(encoding="utf-8"))["tasks"][0]["task"],
    )
    criteria = task.split("## Acceptance criteria", 1)[1].strip()
    assert criteria, "the shipped plan's node states no acceptance criteria to be judged by"

    # The criterion a judge was handed appears on no read-only view — `results`,
    # `status`, `transcript`, and `goals` all omit it — so the effective prompt is the
    # only place this is observable, and it is the thing under test. Everything
    # producing that prompt is the real launch above.
    # llmlint: ignore[tests_mirror_real_usage] No planner-facing view carries the criterion.
    supervising = [
        turn
        for turn in _turns_of(_recorded_turns(launched.prompt_log), "worker")
        if "Completion criterion:" in turn["prompt"]
    ]
    assert supervising, "no dispatched worker was supervised in this run, so nothing here is proven"
    for turn in supervising:
        assert shared_bar in " ".join(turn["prompt"].split()), (
            "the judge was given a completion criterion that is not the shared bar in "
            f"config/onejudge.base.yaml:\n{turn['prompt']}"
        )
        assert criteria in turn["prompt"], (
            "the judge was given the shared bar without the acceptance criteria it is "
            f"phrased against, so it resolves to nothing:\n{turn['prompt']}"
        )


@pytest.mark.xdist_group("orchestrate-launch")
def test_the_shared_preamble_reaches_a_dispatched_worker_itself(launched: Launched) -> None:
    """The standing bar every worker on this host reads arrives verbatim, on the agent side.

    Separate from the judge-side journey above, and deliberately not folded into it: the
    two clauses of `config/onejudge.base.yaml` reach the model by different paths — the
    preamble as the worker's system prompt, the completion criterion inside the
    supervisor's own turn — so one assertion would leave whichever path it did not take
    unproven. That is not hypothetical here: the judge side's `system` is empty in this
    run, and a preamble asserted against it would fail while the worker read it fine.

    Verbatim, because the preamble is the whole standing instruction — what verifying,
    committing, and scope mean for a dispatch, including one whose deliverable is a
    document rather than a diff. A paraphrase reaching the worker is a different
    instruction, and reading the effective prompt is the only place that is observable.

    The worker's role is appended after it rather than replacing it, which is why this
    is a containment check and not an equality one: for the shipped plan's `engineer`
    node that role is the one built into the tool, not `personas/engineer.yaml`.
    """
    preamble = shared_agent_preamble()
    # The system prompt a dispatch was given appears on no read-only view either, for
    # the same reason the completion criterion does not.
    # llmlint: ignore[tests_mirror_real_usage] No planner-facing view carries the system prompt.
    working = [
        turn
        for turn in _turns_of(_recorded_turns(launched.prompt_log), "worker")
        # The agent side, by the one property that separates the two: the judge side is
        # the turn pinned to the judge config.
        if Path(turn["config"] or "").name != JUDGE_CONFIG_NAME
    ]
    assert working, "no dispatched worker took an agent-side turn in this run"
    for turn in working:
        assert preamble in turn["system"], (
            "a dispatched worker was given a system prompt that does not carry the shared "
            f"preamble in config/onejudge.base.yaml verbatim:\n{turn['system']}"
        )


@pytest.mark.xdist_group("orchestrate-launch")
def test_no_turn_of_this_run_reached_a_paid_provider(launched: Launched) -> None:
    """Nothing in the launched run did real model work.

    A stand-in turn calls no tools, so a single tool call recorded anywhere in this
    run means the mock did not cover every candidate of a fallback chain and a real
    agent was working — which voids every other assertion here, because they would
    then be about a different run than the one this journey claims to prove.

    Read with `--all`, through no profile at all. A tool call is a dispatched agent's
    `turn-activity`, which the default planner profile does not carry — so reading this
    the planner's way would answer "no tool calls" for a run full of them, and this
    assertion would hold for the wrong reason exactly when it matters.
    """
    stream = _just("monitor", SHIPPED_RUN, "--all", environment=launched.environment, seconds=60)
    assert stream.returncode == 0, stream.stderr
    acted = [line for line in stream.stdout.splitlines() if line.endswith("turn-activity")]
    assert not acted, f"{len(acted)} real tool call(s) ran; the first was {acted[0]}"


def test_a_launch_reports_a_missing_dag_scope_graph(tmp_path: Path, oneharness_bin: str) -> None:
    """The launch path names the agent-graph config it could not read.

    The failure this whole journey exists for: `just orchestrate` attaches a dag-scope
    agent graph by path, the published crate ships only the *flag*, and a checkout
    without that file refuses every plan it has. A refusal that names the file is the
    difference between a maintainer writing it and a maintainer guessing.

    Named through the recipe's own `--dag-graph` pass-through rather than an
    environment variable, because that pass-through is now the only way to move the
    observer: onepipeline 0.4.0 retired `ONEPIPELINE_DAG_GRAPH` when the flag gained a
    shipped default of `off`.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    environment = _environment(tmp_path, oneharness_bin)

    refused = _just(
        "orchestrate",
        SHIPPED_PLAN,
        "--detach",
        "--dag-graph",
        str(tmp_path / "absent" / "dag-scope.yaml"),
        environment=environment,
        seconds=120,
    )
    assert refused.returncode != 0
    reported = refused.stderr + refused.stdout
    assert "dag-scope.yaml" in reported, reported


@pytest.mark.xdist_group("orchestrate-launch")
def test_the_recipe_opts_the_launch_into_this_hosts_drafting_graph(
    launched: Launched,
) -> None:
    """A real launch records `graphs/pr-author.yaml` as the graph that drafts its bodies.

    `--pr-author-graph` ships naming nothing, so a launch that does not name one opens
    its change requests with the body its plan states, or with none — which is how
    every pull request this harness opened came to carry `Published by onevcs.` as its
    Why. `just orchestrate` naming this file is the whole fix, and a flag that stopped
    reaching `onepipeline start` would fail nowhere: the run still settles, and only
    the next published PR is silently body-less.

    So this reads the run's own launch record rather than the command line. The record
    is what the driver kept, resolved to an absolute path by the crate that accepted
    the flag, which is proof the flag arrived and was understood — not proof that a
    recipe rendered it. `tests/e2e/test_delegated_recipes_e2e.py` holds the rendering.
    """
    launch_record = Path(launched.environment["ONEPIPELINE_RUNS_DIR"]) / SHIPPED_RUN / "launch.json"
    assert launch_record.is_file(), f"the launched run wrote no launch record at {launch_record}"
    recorded = json.loads(launch_record.read_text(encoding="utf-8"))

    assert recorded.get("pr_author_graph") == str(REPO_ROOT / PR_AUTHOR_GRAPH), (
        f"the launch recorded {recorded.get('pr_author_graph')!r} as its drafting graph; "
        f"`just orchestrate` must name {PR_AUTHOR_GRAPH} so this host's change requests "
        "are drafted rather than opened with no body"
    )


class DraftingResult(TypedDict):
    """The fields of one member's oneharness result this journey reads.

    `oneharness` owns the rest of the record; these four are the drafting contract —
    what the model answered, whether it satisfied the schema, and how many attempts
    that took.
    """

    structured: dict[str, str] | None
    schema_valid: bool | None
    schema_attempts: int | None
    schema_error: str | None


class DraftingReport(TypedDict):
    """The member report a drafting run writes, narrowed to what is read here."""

    prompt: str
    results: list[DraftingResult]


def _drafted(
    tmp_path: Path, oneharness_bin: str, answers: list[str]
) -> tuple[subprocess.CompletedProcess[str], DraftingReport]:
    """Run the real drafting graph on a scripted provider, and hand back its report.

    The task is this journey's own composed one; the member's `task` interpolates
    `{task}`, so a graph that stopped doing that shows up here as a drafter that was
    never told what branch it is on.
    """
    environment = _environment(tmp_path, oneharness_bin)
    environment["FAKE_CODEX_ANSWERS"] = json.dumps(answers)
    environment["FAKE_CODEX_ATTEMPT_LOG"] = str(tmp_path / "launches")
    ran = subprocess.run(
        [
            "oneagentgraph",
            "run",
            PR_AUTHOR_GRAPH,
            "--task",
            DRAFTING_TASK,
            "--dir",
            str(tmp_path),
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )
    settled = [
        json.loads(line)
        for line in ran.stdout.splitlines()
        if line.startswith("{") and json.loads(line)["kind"] == "member-settled"
    ]
    assert settled, f"the drafter never settled:\n{ran.stdout}\n{ran.stderr}"
    # oneharness owns this record's schema; DraftingReport states the fields read here
    # rather than re-validating somebody else's document.
    report = cast(
        DraftingReport,
        json.loads(Path(settled[0]["payload"]["report_path"]).read_text(encoding="utf-8")),
    )
    return ran, report


def test_the_drafting_graph_answers_with_the_body_contract(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A drafting turn produces the `{body}` object `onepipeline` publishes from.

    A remote lifecycle publication is what reaches this graph on a real run, and this
    suite performs none — that would open a pull request on a real repository. What it
    can drive is everything below that: the real `oneagentgraph`, the real graph
    document, the real `oneharness.pr-author.toml`, and the real schema, with only the
    paid provider scripted. That covers the part this repository actually owns, because
    the handoff to `onevcs` is one field — `results[].structured.body` — and a graph
    that produced anything else would publish a body-less change request in silence.
    """
    body = "## What\nAdded the endpoint.\n\n## Why\nOperators had nothing to poll.\n"
    ran, report = _drafted(tmp_path, oneharness_bin, [json.dumps({"body": body})])

    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert DRAFTING_TASK in report["prompt"], (
        "the member's own task replaced the composed drafting task without "
        f"interpolating it back in, so the drafter was shown no branch:\n{report['prompt']}"
    )
    answered = report["results"][-1]
    assert answered["schema_valid"] is True, answered
    assert answered["structured"] == {"body": body}, (
        f"the drafter answered {answered['structured']!r}; `onepipeline` publishes "
        "`results[].structured.body` and nothing else"
    )


def test_a_drafting_answer_that_does_not_validate_is_re_prompted(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The schema is the drafter's review, so a bad answer is refused and asked again.

    This is the whole reason the member has no judge side: `schema_max_retries` is what
    stands in for a supervisor. If a non-conforming answer were accepted instead, the
    first field name a model invented would reach `onevcs` as no body at all — the
    silent failure a single-sided member has nothing else to catch.
    """
    body = "## What\nSecond time.\n\n## Why\nThe first answer was not the contract.\n"
    ran, report = _drafted(
        tmp_path,
        oneharness_bin,
        # A plain prose answer, then one that is not the closed object, then the body.
        ["a body, but not as JSON", json.dumps({"summary": body}), json.dumps({"body": body})],
    )

    assert ran.returncode == 0, ran.stdout + ran.stderr
    answered = report["results"][-1]
    assert answered["schema_valid"] is True, answered
    assert answered["structured"] == {"body": body}
    assert answered["schema_attempts"] > 1, (
        f"the drafter settled in {answered['schema_attempts']} attempt(s), so the "
        "refused answers above were not re-prompted and the schema is not the review"
    )


def test_the_drafting_graph_is_one_the_adopted_reader_accepts() -> None:
    """`graphs/pr-author.yaml` validates, and the body schema is the closed contract.

    `oneagentgraph validate` is the same reader the launch uses, and it is what refuses
    the one combination this member cannot have: a `schema_file` member that also
    streams, because oneharness validates a structured answer against the complete
    response. The journeys above drive the member; this holds the document itself, so a
    graph edit that makes it unreadable fails before anything is launched.
    """
    validated = subprocess.run(
        ["oneagentgraph", "validate", PR_AUTHOR_GRAPH],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert validated.returncode == 0, (
        f"{PR_AUTHOR_GRAPH} did not validate:\n{validated.stdout}\n{validated.stderr}"
    )

    schema = json.loads((REPO_ROOT / PR_AUTHOR_BODY_SCHEMA).read_text(encoding="utf-8"))
    assert schema["required"] == ["body"], (
        f"{PR_AUTHOR_BODY_SCHEMA} must require `body`: that is the field `onepipeline` "
        "reads the drafted body out of, at `results[].structured.body`"
    )
    assert schema["properties"]["body"]["type"] == "string"
    assert schema["additionalProperties"] is False, (
        f"{PR_AUTHOR_BODY_SCHEMA} must close the object: an extra field a drafter "
        "answered with would be silently dropped downstream instead of re-prompted"
    )


#: The node kinds the published plan schema closes over. A node that names none is an
#: agent node, which is why every plan here writes `kind` only to ask for the other one.
NodeKind = Literal["human"]


class PlanNode(TypedDict, total=False):
    """One agent node of a candidate plan, in the published plan schema's own field names.

    `total=False` because a node states only what its journey needs. `done_when` is here
    although the adopted schema has no such field: writing one is what
    `test_a_plan_carrying_a_node_level_done_when_is_refused` does, so the retired field
    is part of this shape precisely so a test can offer it and be refused.
    """

    id: str
    kind: NodeKind
    persona: str
    task: str
    max_turns: int
    expects_no_diff: bool
    done_when: str


class Goal(TypedDict):
    """What a plan states it is for, in the one field the published schema gives it."""

    text: str


class CandidatePlan(TypedDict, total=False):
    """A plan offered to the real launcher, which is the only thing that judges it."""

    schema_version: int
    goal: Goal
    name: str
    tasks: list[PlanNode]


def _node(**fields: object) -> PlanNode:
    """One agent node, shaped the way every plan this repository ships shapes one."""
    node: PlanNode = {
        "id": "only",
        "persona": "engineer",
        "task": "## What\nReport.\n\n## Why\nBecause.\n\n## Acceptance criteria\n- Reported.",
    }
    # The overrides are the journey's own literals, one key at a time so the declared
    # shape above still describes what is written.
    node.update(cast(PlanNode, fields))
    return node


def _refused_plan(
    tmp_path: Path, oneharness_bin: str, plan: CandidatePlan
) -> subprocess.CompletedProcess[str]:
    """Offer one plan to the real launcher and hand back how it answered."""
    environment = _environment(tmp_path, oneharness_bin)
    written = tmp_path / "candidate.plan.json"
    written.write_text(json.dumps(plan), encoding="utf-8")
    # The plan is loaded and validated before the agent graph is, so a refusal here is
    # the plan's own and no paid work is reachable even if one were somehow accepted.
    return _just(
        "orchestrate",
        str(written),
        "--detach",
        "--dag-graph",
        str(tmp_path / "absent" / "dag-scope.yaml"),
        environment=environment,
        seconds=120,
    )


def test_a_plan_carrying_a_node_level_done_when_is_refused(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The bar cannot be written back into the plan, and saying so is the whole point.

    This repository moved every node's review bar out of `done_when` and into the task's
    `## Acceptance criteria` because the adopted `onepipeline` stopped accepting the
    field. If it were instead *ignored*, the migration would be cosmetic and the next
    planner to write one would get a node dispatched with a bar nobody applied. So what
    is held here is the refusal, and that it names where the bar goes instead.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    refused = _refused_plan(
        tmp_path,
        oneharness_bin,
        {"schema_version": 2, "tasks": [_node(done_when="all criteria are met")]},
    )

    assert refused.returncode != 0, refused.stdout
    reported = refused.stderr + refused.stdout
    assert "done_when" in reported and "Acceptance criteria" in reported, reported


def test_a_plan_declaring_an_unread_schema_version_is_refused_by_number(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A version this build does not read is refused naming the ones it does.

    A plan file is the one artifact an operator keeps a copy of, so a version number
    the launcher cannot read has to fail at the plan and say what to write instead,
    rather than somewhere downstream. The number here is one past the newest the
    adopted crate reads, because that is the direction a plan drifts: this repository
    writes the newest, and the crate keeps reading the older ones beneath it.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    unread = PLAN_SCHEMA_VERSION + 1
    refused = _refused_plan(
        tmp_path, oneharness_bin, {"schema_version": unread, "tasks": [_node()]}
    )

    assert refused.returncode != 0, refused.stdout
    reported = refused.stderr + refused.stdout
    assert "schema_version" in reported and str(unread) in reported, reported
    assert str(PLAN_SCHEMA_VERSION) in reported, (
        f"the refusal must name {PLAN_SCHEMA_VERSION} as a version it reads, so an "
        f"operator holding an unreadable plan learns what to write:\n{reported}"
    )


@pytest.mark.reads_docs
def test_every_lifecycle_node_this_repository_ships_states_a_title() -> None:
    """Schema 3 requires a lifecycle node's title, and it is the published subject.

    `test_every_plan_this_repository_ships_is_one_the_published_crate_accepts` already
    proves the launcher accepts each of these documents, which at schema 3 means each
    lifecycle node has *a* title. What that cannot see is the shape: the title is the
    change request's subject and a squash-merged publication leaves exactly one commit
    carrying it, so a title that is not a Conventional Commit subject lands on a base
    branch and stays there.
    """
    lifecycle = [
        (origin, node)
        for origin, document in _plans_in_the_repository()
        for node in json.loads(document)["tasks"]
        if "repo" in node
    ]
    assert lifecycle, "no lifecycle nodes were found to check"

    for origin, node in lifecycle:
        title = node.get("title")
        assert isinstance(title, str) and title.strip(), (
            f"{origin}: lifecycle node {node['id']!r} states no title; plan schema "
            f"{PLAN_SCHEMA_VERSION} requires one and it is the published subject"
        )
        assert CONVENTIONAL_COMMIT_SUBJECT.match(title), (
            f"{origin}: lifecycle node {node['id']!r} has title {title!r}, which is not "
            "a Conventional Commit subject (`type(scope)!: summary`)"
        )


def test_a_reply_carrying_a_heartbeat_interval_is_refused_whole(launched: Launched) -> None:
    """The pacemaker's interval is launch-only, and a reply that tries to retune it is lost.

    AGENTS.md told planners for months to put `"heartbeat_interval"` in a normal
    `channel-reply`. The envelope is closed to unknown fields, so such a reply is refused
    entirely — taking the verdict and any graph edits in it with it, which is why this
    costs a round boundary rather than being a harmless no-op. Held against the run this
    journey already launched, through the same recipe a planner answers a surface with.
    """
    reply = subprocess.run(
        ["just", "channel-reply", SHIPPED_RUN],
        cwd=REPO_ROOT,
        env=launched.environment,
        input=json.dumps(
            {"version": 1, "completion": False, "message": "go", "heartbeat_interval": 900}
        ),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert reply.returncode != 0, reply.stdout
    reported = reply.stderr + reply.stdout
    assert "heartbeat_interval" in reported, reported
    for accepted in ("version", "author", "completion", "message", "reason", "commands"):
        assert accepted in reported, (
            f"the refusal must name {accepted!r} as an accepted field so a planner can "
            f"see what the envelope does take:\n{reported}"
        )


#: How many reads it takes to drain a settled run's queue before giving up. A bound
#: rather than a loop, because `channel-next` on a settled run answers immediately and
#: an unbounded drain would spin on a queue that never empties.
MOST_SURFACES_A_SETTLED_RUN_QUEUES = 8

#: A stream line's label for an event the pipeline itself wrote, and for one that came
#: out of a dispatched `oneagentgraph` launch. Which of the two a read carries is the
#: whole difference between the profiles below.
PIPELINE_LINE = "graph:"
AGENT_LINE = "agent:"


@pytest.mark.xdist_group("orchestrate-launch")
def test_the_planner_profile_is_the_default_and_the_detailed_one_is_reachable(
    launched: Launched,
) -> None:
    """`just monitor` reads the planner profile, and `--filter`/`--all` reach past it.

    The planner's default view narrows to what the pipeline itself decided — node
    dispatch and settlement, surfaces, edits — and deliberately drops each dispatched
    worker's turns. That default is `onepipeline`'s own, which is why the recipe names
    no filter; what the recipe owes is that it does not get in the way of the two
    published ways to widen it, and that is what fails here if it starts pinning one.

    Read through `just monitor` rather than `just channel-next`, because rendering a
    surface is not consuming it: a journey that consumed this run's queued pacemaker
    update would take it from the assertions that follow it in this group.
    """
    default = _just("monitor", SHIPPED_RUN, environment=launched.environment, seconds=60)
    assert default.returncode == 0, default.stderr
    default_lines = default.stdout.splitlines()
    assert any(PIPELINE_LINE in line for line in default_lines), default.stdout
    assert not [line for line in default_lines if AGENT_LINE in line], (
        "the default view carried dispatched-agent events, so it is no longer the "
        f"planner profile:\n{default.stdout}"
    )

    detailed = _just(
        "monitor", SHIPPED_RUN, "--filter", "monitor", environment=launched.environment, seconds=60
    )
    assert detailed.returncode == 0, detailed.stderr
    assert [line for line in detailed.stdout.splitlines() if AGENT_LINE in line], (
        f"`--filter monitor` did not widen the view past the planner profile:\n{detailed.stdout}"
    )

    unfiltered = _just(
        "monitor", SHIPPED_RUN, "--all", environment=launched.environment, seconds=60
    )
    assert unfiltered.returncode == 0, unfiltered.stderr
    assert [line for line in unfiltered.stdout.splitlines() if AGENT_LINE in line], (
        f"`--all` did not read past the planner profile:\n{unfiltered.stdout}"
    )

    # The recovery path a planner meets by asking for both at once. The two are
    # mutually exclusive at the CLI, and the recipe passing them through untouched is
    # what lets the CLI say so instead of the wrapper guessing which one was meant.
    both = _just(
        "monitor",
        SHIPPED_RUN,
        "--filter",
        "monitor",
        "--all",
        environment=launched.environment,
        seconds=60,
    )
    assert both.returncode != 0, both.stdout
    assert "--filter" in both.stderr and "--all" in both.stderr, both.stderr


#: Every op the monitor may issue, and what an already-settled graph answers each with.
#: The refusal is the graph's, not an authority verdict, which is the distinction under
#: test: these four are refused for what the node is, and the four below for who asked.
#: `add` is the fifth allowed op and applies even here, so it is exercised on a run of
#: its own rather than against this settled one.
MONITOR_OPS_ON_A_SETTLED_GRAPH = (
    RefusedOnTheGraph(
        {"op": "context", "id": "research", "note": "n"}, "nothing will read the note"
    ),
    RefusedOnTheGraph({"op": "cancel", "id": "research"}, "not pending or running"),
    RefusedOnTheGraph(
        {
            "op": "retry",
            "id": "research",
            "node": {"id": "redo", "task": "x", "expects_no_diff": True},
        },
        "not running, failed, or cancelled",
    ),
    RefusedOnTheGraph({"op": "requeue", "id": "research"}, "not parked"),
)

#: Every op the monitor may not issue, whatever the graph looks like.
OPS_THE_MONITOR_MAY_NOT_ISSUE: tuple[EditCommand, ...] = (
    {"op": "drop", "id": "research", "dependents": "drop"},
    {"op": "reparent", "id": "research", "deps": []},
    {"op": "attest", "ref": "research"},
    {"op": "complete", "reason": "verified"},
)


def _monitor_reply(launched: Launched, envelope: ReplyEnvelope) -> subprocess.CompletedProcess[str]:
    """Send one envelope through the recipe a monitor's edit actually goes out on."""
    return subprocess.run(
        ["just", "channel-reply", SHIPPED_RUN],
        cwd=REPO_ROOT,
        env=launched.environment,
        input=json.dumps(envelope),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )


@pytest.mark.xdist_group("orchestrate-launch")
@pytest.mark.parametrize("command", OPS_THE_MONITOR_MAY_NOT_ISSUE, ids=lambda row: str(row["op"]))
def test_an_op_outside_the_monitor_allowlist_is_refused_by_the_engine(
    launched: Launched, command: EditCommand
) -> None:
    """`personas/orchestrator.yaml` states the allowlist; the engine is what enforces it.

    A persona is a prompt, so a bound stated only there is a bound a model may cross.
    These four are the ones whose crossing costs the planner a decision it never made —
    removing work, rewiring dependencies, attesting a human action nobody took, and
    declaring the run finished — so what is held here is that the published engine
    refuses them for *who asked*, ahead of any question about the graph's state. Sent
    through the real recipe, against the run this journey already launched.
    """
    refused = _monitor_reply(launched, {"version": 1, "author": "monitor", "commands": [command]})

    assert refused.returncode != 0, refused.stdout
    reported = refused.stderr + refused.stdout
    assert f"'{command['op']}' is not an op the monitor may issue" in reported, reported
    # Every refusal names the available action, which is the whole design: an op the
    # monitor may not apply is one it is meant to escalate.
    assert "Surface it to the planner" in reported, reported


@pytest.mark.xdist_group("orchestrate-launch")
@pytest.mark.parametrize(
    "refused_on_the_graph", MONITOR_OPS_ON_A_SETTLED_GRAPH, ids=lambda row: str(row.command["op"])
)
def test_an_op_inside_the_monitor_allowlist_is_judged_on_the_graph_not_the_author(
    launched: Launched, refused_on_the_graph: RefusedOnTheGraph
) -> None:
    """The five allowed ops reach the graph, and are answered by what the graph is.

    The other half of the allowlist, and the half a refusal-only test would leave
    unproven: an in-allowlist op must not be refused for *who asked*. This run has
    settled, so each of these four is refused for what the node now is — and the
    refusal wording is the distinction, because an authority refusal and a state
    refusal read alike to a monitor that only checks the exit status. `add` is the
    fifth and is exercised separately below, since it is the one that still applies.
    """
    refused = _monitor_reply(
        launched,
        {"version": 1, "author": "monitor", "commands": [refused_on_the_graph.command]},
    )

    assert refused.returncode != 0, refused.stdout
    reported = refused.stderr + refused.stdout
    assert "is not an op the monitor may issue" not in reported, (
        f"an in-allowlist op was refused for who asked rather than for the graph:\n{reported}"
    )
    assert refused_on_the_graph.refusal in reported, reported


def test_a_monitor_edit_is_applied_and_attributed_to_the_monitor(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """An in-allowlist edit lands on the graph, carrying the author that makes it visible.

    This is the behaviour the persona's "apply the fix yourself" instruction rests on,
    and the reason `"author":"monitor"` is required rather than decorative: the engine
    records the author on the committed edit and queues a non-blocking planner surface
    naming it, so a fix the monitor applied is reported as the monitor's without the
    monitor also having to report it. A run of its own, because this one mutates the
    graph it is sent to.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    environment = _environment(tmp_path, oneharness_bin)
    plan = tmp_path / "monitor-edit.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "name": "monitor-edit-e2e",
                "tasks": [_node(id="only")],
            }
        ),
        encoding="utf-8",
    )
    launch = _just("orchestrate", str(plan), environment=environment)
    try:
        assert launch.returncode == 0, launch.stdout + launch.stderr
        applied = subprocess.run(
            ["just", "channel-reply", "monitor-edit-e2e"],
            cwd=REPO_ROOT,
            env=environment,
            input=json.dumps(
                {
                    "version": 1,
                    "author": "monitor",
                    "commands": [
                        {
                            "op": "add",
                            "node": {
                                "id": "monitor-added",
                                "task": "Report.",
                                "expects_no_diff": True,
                            },
                        }
                    ],
                }
            ),
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )
        assert applied.returncode == 0, applied.stderr + applied.stdout

        # The attribution is only worth what a planner can read, so it is read back the
        # way a planner reads one: from the run's own stream, through the recipe.
        stream = _just("monitor", "monitor-edit-e2e", "--all", environment=environment, seconds=60)
        assert stream.returncode == 0, stream.stderr
        assert "edit-committed" in stream.stdout, stream.stdout
        assert "monitor-edit" in stream.stdout, (
            "the engine queued no planner surface naming the monitor's edit, so a fix "
            f"the monitor applied is invisible to the planner:\n{stream.stdout}"
        )

        # And consumed the way a planner consumes one. `just channel-next` hands out
        # each queued surface once, with the events its profile admits, so this is also
        # where the recipe's `planner` default is held live: consuming a surface mutates
        # the queue, so it is done on this journey's own run rather than the shared one.
        #
        # Drained rather than read once, because this run's monitor is raising its own
        # supervisor surfaces alongside the engine's attribution and the order of the
        # two is not this journey's claim.
        seen: list[str] = []
        sources: set[str] = set()
        for _ in range(MOST_SURFACES_A_SETTLED_RUN_QUEUES):
            read = _just("channel-next", "monitor-edit-e2e", environment=environment, seconds=60)
            assert read.returncode == 0, read.stderr
            handed = cast(SurfaceRead, json.loads(read.stdout))
            sources |= {event["source"] for event in handed["events"]}
            if handed["surface"] is None:
                break
            seen.append(handed["surface"]["kind"])
            if handed["surface"]["kind"] == "monitor-edit":
                break
        assert "monitor-edit" in seen, (
            f"no surface naming the monitor's edit was handed out; got {seen}"
        )
        assert sources and sources == {"pipeline"}, (
            "`just channel-next` no longer reads through the planner profile; it "
            f"carried events from {sorted(sources)}"
        )
    finally:
        _just("stop", "monitor-edit-e2e", environment=environment, seconds=60)


#: The judge side `graphs/dag-scope.yaml` gives the monitor: the filter that makes the
#: planner channel serviceable as a onejudge command provider.
CHANNEL_SERVE = REPO_ROOT / "scripts" / "channel-serve.py"

#: The supervisor frame onejudge writes to a judge-side command provider's stdin, as
#: recorded off a real `oneagentgraph run` whose judge side was a frame-dumping
#: command. `onepipeline channel serve` reads `{kind, message, blocking?, node?}` and
#: refuses an unknown field, so this shape is exactly what the filter exists to
#: translate — and the fixture below is the real thing rather than a paraphrase.
# A typed model here would be a second copy of an external wire format whose one model
# is `SupervisorFrame` in `scripts/channel-serve.py`, and the filter — not this fixture
# — is what that model exists to type; a copy could drift from the recorded frame the
# journey feeds it and still typecheck.
# llmlint: ignore[modern_domain_modeling] `scripts/channel-serve.py` owns this model.
SUPERVISOR_FRAME: dict[str, object] = {
    "op": "supervisor",
    "task": "onepipeline run `serve-e2e`.\n\nGoal: prove the channel is the judge side",
    "persona": "Act as the live planner reviewing the run's monitor.",
    "done_when": "the run has settled",
    "worktree": str(REPO_ROOT),
    "history_name": "dag-scope-1-monitor-skill",
    "messages": [
        {"role": "user", "content": "onepipeline run `serve-e2e`."},
        {"role": "assistant", "content": "node api has drifted from its acceptance criteria"},
    ],
    "session": "dag-scope-1-monitor-user",
}


def _serve(frame: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Drive the real filter at the interface `oneagentgraph` spawns it through."""
    return subprocess.run(
        [str(CHANNEL_SERVE)],
        cwd=REPO_ROOT,
        env=environment,
        input=frame,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )


class Supervised(NamedTuple):
    """One launch whose monitor is actually being supervised, and its evidence.

    The supervision is the fixture's whole job: a monitor whose judge side is the
    planner takes one turn and then waits, so a run nobody answers stops producing
    the very turns these journeys read. Answering it is also what a planner does.
    """

    #: What every later recipe has to be given to see the same run.
    environment: dict[str, str]
    #: Every effective prompt the run's turns were given, as the fake backend saw them.
    prompt_log: Path
    #: The instruction the supervising planner keeps sending, asserted verbatim below.
    steer: str


#: The run id `onepipeline` mints from the supervised plan's `name`.
SUPERVISED_RUN = "planner-supervises-monitor"

#: What the planner tells the monitor. Distinctive prose, because one journey asserts
#: this exact text becomes the monitor's next instruction.
PLANNER_STEER = "keep watching the gate and report what it is blocking"


@pytest.fixture(scope="module")
def planner_supervised(
    tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str
) -> Iterator[Supervised]:
    """Launch a run and supervise its monitor for as long as the journeys need it.

    A plan holding one human gate, so the graph has a decision to sit on and the run
    stays unsettled while the monitor works. A background answerer plays the planner:
    it reads each surface with the real `just channel-next` and answers it with the
    real `just channel-reply`, which is what keeps the conversation — and therefore
    the observer graph, and therefore its scheduled pacemaker — alive.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("planner-supervises-monitor")
    environment = _environment(tmp_path, oneharness_bin)
    prompt_log = tmp_path / "prompts.jsonl"
    environment[PROMPT_LOG_ENV] = str(prompt_log)
    plan = tmp_path / "serve.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "goal": {"text": "prove the channel is the monitor's judge side"},
                "name": SUPERVISED_RUN,
                "tasks": [
                    {
                        "id": "gate",
                        "kind": "human",
                        "task": "## What\nApprove.\n\n## Why\nProbe.\n\n"
                        "## Acceptance criteria\n- Approved.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    launched = subprocess.Popen(  # noqa: S603 - the real recipe, as an operator runs it
        ["just", "orchestrate", str(plan), "--heartbeat-interval", str(PACEMAKER_INTERVAL_SECONDS)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    supervising = threading.Event()

    def supervise() -> None:
        while not supervising.is_set():
            read = _just("channel-next", SUPERVISED_RUN, environment=environment, seconds=60)
            if read.returncode != 0 or not read.stdout.strip():
                time.sleep(0.1)
                continue
            handed = cast(SurfaceRead, json.loads(read.stdout))
            if handed["surface"] is None:
                time.sleep(0.1)
                continue
            subprocess.run(
                ["just", "channel-reply", SUPERVISED_RUN],
                cwd=REPO_ROOT,
                env=environment,
                input=json.dumps(
                    {
                        "completion": False,
                        "message": PLANNER_STEER,
                        "reason": "the run is not finished",
                    }
                ),
                text=True,
                capture_output=True,
                timeout=e2e_timeout(60),
                check=False,
            )

    planner = threading.Thread(target=supervise, daemon=True)
    planner.start()
    try:
        yield Supervised(environment, prompt_log, PLANNER_STEER)
    finally:
        supervising.set()
        planner.join(timeout=e2e_timeout(60))
        launched.kill()
        launched.wait(timeout=e2e_timeout(60))
        _just("stop", SUPERVISED_RUN, environment=environment, seconds=60)


def _turns_so_far(prompt_log: Path) -> list[PromptRecord]:
    """Every turn recorded up to now, on a run that is still going.

    Tolerates the log not existing yet, which is the ordinary state for the first
    fraction of a second of a live launch and not a failure to report.
    """
    return _recorded_turns(prompt_log) if prompt_log.exists() else []


def _monitor_turns(prompt_log: Path) -> list[str]:
    """Every prompt the monitor's AGENT side was given, newest last."""
    return [
        turn["prompt"]
        for turn in _turns_so_far(prompt_log)
        if "/members/monitor/" in (turn["config"] or "")
        and Path(turn["config"] or "").name != JUDGE_CONFIG_NAME
    ]


def _await(found: Callable[[], bool], seconds: float, failure: Callable[[], str]) -> None:
    """Wait for a live run to show something, or fail saying what it showed instead."""
    limit = deadline(seconds)
    while not found():
        if time.monotonic() >= limit:
            pytest.fail(failure())
        time.sleep(0.1)


@pytest.mark.xdist_group("planner-supervises-monitor")
def test_a_planner_reply_reaches_the_monitor_in_its_own_conversation(
    planner_supervised: Supervised,
) -> None:
    """The planner channel is the monitor's judge side, so a reply steers its next turn.

    This is the whole point of wiring `onepipeline channel serve` behind that member:
    a planner who answers a monitor surface is not only editing the graph, they are
    answering the monitor, and what they say has to become the monitor's next
    instruction. Nothing short of a real launch shows it — the reply travels onejudge's
    supervisor boundary, out through `scripts/channel-serve.py` to the channel, back
    through it, and into the next turn's prompt — so this reads the prompt the monitor
    was actually given, from a run being supervised through the real recipes.

    Pointed at `graphs/dag-scope.yaml` itself. Wire that member's judge back to a
    simulated-user harness and this fails at the surface that never arrives.
    """
    _await(
        lambda: any(
            planner_supervised.steer in prompt
            for prompt in _monitor_turns(planner_supervised.prompt_log)
        ),
        seconds=90,
        failure=lambda: (
            "the planner's reply never reached the monitor's conversation; its turns "
            f"were given:\n{_monitor_turns(planner_supervised.prompt_log)}"
        ),
    )


def _dag_scope_document() -> tuple[int, str]:
    """The shipped graph's declared version and its `check-in` task, from the file.

    Read with a reader written for these two fields rather than with a YAML library:
    the workspace installs none, and `oneagentgraph validate` — which the deterministic
    gate runs over this file — is what holds the document well-formed.
    """
    lines = (REPO_ROOT / DAG_SCOPE_GRAPH).read_text(encoding="utf-8").splitlines()
    declared = next(line for line in lines if line.startswith("version:"))
    opened = next(index for index, line in enumerate(lines) if line.strip() == "task: |")
    indent = len(lines[opened]) - len(lines[opened].lstrip())
    body = []
    for line in lines[opened + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line.strip())
    return int(declared.split(":", 1)[1]), " ".join(body)


@pytest.mark.reads_docs
def test_the_pacemaker_is_told_which_run_to_report_on_and_not_to_edit() -> None:
    """The pacemaker's own task names its run through `{task}`, and forbids editing.

    Its live turn is no longer observable in a short journey, and that is the design
    working rather than a gap: the schedule is resettable, so every planner-visible
    surface restarts its clock — and a monitor whose judge side is the planner raises
    one on every turn. A pacemaker exists to speak when nobody else has, so a
    supervised run is exactly the run where it should stay quiet. What still has to
    hold is the content of the task it would be given, which is this file's to state.

    `{task}` is the load-bearing part. `onepipeline` exports NO variable naming the
    run to an observer member — measured by dumping a judge command's whole
    environment on a real launch — so a task written against `$ONEPIPELINE_RUN_ID`
    reads empty on an operator's launch, and inside a dispatch that exports one for
    its own run it silently reports on the enclosing run instead of this one.
    """
    # llmlint: ignore[tests_mirror_real_usage] A 30-minute schedule outlasts a journey.
    _, task = _dag_scope_document()

    assert "{task}" in task, (
        "the pacemaker no longer interpolates the composed task, so nothing tells it "
        f"which run to report on: {task}"
    )
    assert "$ONEPIPELINE_RUN_ID" not in task, (
        "the pacemaker names a run id variable `onepipeline` does not export to an "
        f"observer member: {task}"
    )
    assert f"Never send `{FORBIDDEN_OF_THE_PACEMAKER}`" in task, (
        f"the pacemaker's task no longer forbids the edit verb by name: {task}"
    )


@pytest.mark.reads_docs
def test_the_graphs_declared_version_is_one_that_expands_the_task_placeholder(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """`{task}` is a per-version behaviour, so the version the graph declares is checked.

    `oneagentgraph` passes the token through literally below schema 4 — a member would
    be handed the six characters instead of its run. Measured here against the real CLI
    at the version `graphs/dag-scope.yaml` actually declares, so lowering that version
    fails here rather than in a pacemaker update naming no run.

    The probe is the pacemaker's own shape — single-sided, on the pacemaker's config —
    and since oneagentgraph 0.2.18 that means oneharness runs **in process**: no
    oneharness binary is spawned, so `ONEAGENTGRAPH_ONEHARNESS_BIN` never sees this
    turn and there is no fake-backend prompt log to read. What is read instead is the
    member's own report, which is where a library run records the prompt it composed —
    a better witness than a log, since it is the producing library's own record, and
    the same one the drafting journeys above read. `_environment` keeps the turn off
    the paid provider at the layer that still covers it.
    """
    declared, _ = _dag_scope_document()
    environment = _environment(tmp_path, oneharness_bin)
    graph = tmp_path / "placeholder.yaml"
    graph.write_text(
        f"version: {declared}\n"
        "name: placeholder\n"
        "members:\n"
        "  probe:\n"
        "    kind: oneharness\n"
        f"    oneharness_config: {REPO_ROOT / 'oneharness.check-in.toml'}\n"
        "    task: |\n"
        "      opened with {task}\n",
        encoding="utf-8",
    )
    composed = "onepipeline run `placeholder-run`."

    ran = subprocess.run(
        ["oneagentgraph", "run", str(graph), "--task", composed, "--dir", str(tmp_path)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )

    assert ran.returncode == 0, ran.stdout + ran.stderr
    settled = [
        json.loads(line)
        for line in ran.stdout.splitlines()
        if line.startswith("{") and json.loads(line)["kind"] == "member-settled"
    ]
    assert settled, f"the probe member never settled:\n{ran.stdout}\n{ran.stderr}"
    report = json.loads(Path(settled[0]["payload"]["report_path"]).read_text(encoding="utf-8"))

    assert composed in report["prompt"], (
        f"schema {declared} did not expand `{{task}}`, so a member that claims its own "
        f"task cannot learn its run; it was given:\n{report['prompt']}"
    )


def test_the_channel_filter_refuses_input_the_planner_channel_would_not_answer(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """Malformed input is refused by name, never answered on the planner's behalf.

    The filter sits between a model's conversation and a live planner, so the one
    thing it must never do is invent a verdict when it cannot reach one. Each case
    below is driven through the real script at the interface `oneagentgraph` spawns
    it through, and each must fail with a diagnostic rather than print a supervisor
    response: onejudge reads this stdout as the planner's answer, so a fabricated
    `{"completion": ...}` here would settle or continue a run nobody ruled on.
    """
    environment = _environment(tmp_path, oneharness_bin)

    refusals = {
        "nothing on stdin at all": ("", "no supervisor frame on stdin"),
        "not JSON at all": ("this is not a frame", "not JSON"),
        "not an object": ("[1, 2]", "must be a JSON object"),
        "an eval call rather than a supervisor one": (
            json.dumps({**SUPERVISOR_FRAME, "op": "eval"}),
            "only the `supervisor` op",
        ),
        "a task that names no run": (
            json.dumps({**SUPERVISOR_FRAME, "task": "no run named here"}),
            "does not name its run",
        ),
        # The run id is read out of somebody else's prose and then spent as an argv word
        # and a `runs/<run-id>/` directory, so a task naming something those two uses do
        # not survive is refused here rather than handed to a subprocess to interpret.
        "a task naming a run argv would read as a flag": (
            json.dumps({**SUPERVISOR_FRAME, "task": "onepipeline run `--all`."}),
            "will not pass to",
        ),
        "a task naming a run that reaches outside the runs directory": (
            json.dumps({**SUPERVISOR_FRAME, "task": "onepipeline run `../../etc/passwd`."}),
            "will not pass to",
        ),
        "a frame carrying no conversation": (
            json.dumps({**SUPERVISOR_FRAME, "messages": "node api has drifted"}),
            "carries no `messages` conversation",
        ),
        "a conversation the monitor has not spoken in": (
            json.dumps({**SUPERVISOR_FRAME, "messages": [{"role": "user", "content": "hi"}]}),
            "said nothing",
        ),
    }
    for case, (frame, expected) in refusals.items():
        refused = _serve(frame, environment)

        assert refused.returncode != 0, f"{case} was accepted: {refused.stdout}"
        assert expected in refused.stderr, f"{case}: {refused.stderr}"
        assert "completion" not in refused.stdout, (
            f"{case} produced a supervisor verdict onejudge would act on: {refused.stdout}"
        )


#: The seam `scripts/channel-serve.py` resolves its `onepipeline` through, so a journey
#: can drive what happens when that binary cannot run or answers nothing.
ONEPIPELINE_BIN = "ONEPIPELINE_BIN"


def test_the_channel_filter_reports_a_channel_it_cannot_run(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A toolchain that cannot answer the planner is reported, not answered around.

    The filter resolves the pinned `onepipeline` rather than looking one up, so the
    failure a broken checkout produces is a spawn failure — and the monitor's whole
    conversation depends on it. Held here because the alternative to reporting it is
    the one thing this filter must never do: rule on the run itself.

    Every unusable override is reported the same way, including the ones that never
    reach a spawn at all: the value is the graph's environment rather than this
    filter's, so an empty or unencodable one has to be named as the cause here instead
    of surfacing as a traceback from the subprocess boundary.
    """
    environment = _environment(tmp_path, oneharness_bin)

    for case, override in (
        ("a path where nothing is installed", str(tmp_path / "no-such-onepipeline")),
        ("a file that is not executable", str(_unrunnable(tmp_path))),
        ("an override set to nothing at all", ""),
    ):
        environment[ONEPIPELINE_BIN] = override

        refused = _serve(json.dumps(SUPERVISOR_FRAME), environment)

        assert refused.returncode != 0, f"{case} was run: {refused.stdout}"
        assert "could not run" in refused.stderr, f"{case}: {refused.stderr}"
        assert "just bootstrap" in refused.stderr, f"{case}: {refused.stderr}"
        assert "Traceback" not in refused.stderr, f"{case}: {refused.stderr}"
        assert "completion" not in refused.stdout, f"{case}: {refused.stdout}"


def _unrunnable(tmp_path: Path) -> Path:
    """A real `onepipeline`-shaped file that the filter still cannot run."""
    present = tmp_path / "onepipeline-without-the-bit"
    present.write_text("#!/usr/bin/env bash\necho '{\"completion\": true}'\n", encoding="utf-8")
    present.chmod(0o644)
    return present


def test_the_channel_filter_refuses_an_answer_it_cannot_recognise(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """Whatever the channel says is checked to be a ruling before onejudge acts on it.

    onejudge reads this stdout as the planner's verdict, so relaying an unchecked
    response would let a changed release — or an empty one — settle or continue a run
    nobody ruled on. The ruling's `completion` and the prose onejudge hands back to the
    monitor are both checked, and every shape is driven against the real script through
    a stand-in channel, which is the only way to produce an answer the published one
    never gives.
    """
    environment = _environment(tmp_path, oneharness_bin)
    channel = tmp_path / "stand-in-channel"
    answers = tmp_path / "answer.txt"
    # The subject of this journey is what the filter does with an answer the published
    # `channel serve` never gives, so the published one cannot produce the input under
    # test; every other boundary here is real. The stand-in reads the surface and
    # replies with whatever the case put in the file, so the answer travels as bytes
    # rather than through a shell quoting it.
    # llmlint: ignore[e2e_not_mocked] The published channel cannot make this input.
    channel.write_text(
        f"#!/usr/bin/env bash\ncat > /dev/null\ncat {answers}\n",
        encoding="utf-8",
    )
    channel.chmod(0o755)
    environment[ONEPIPELINE_BIN] = str(channel)

    for case, answer, expected in (
        ("an empty answer", "", "closed without answering"),
        ("an answer that is not JSON", "ok\n", "is not JSON"),
        ("an answer that is not a ruling", '{"message": "sure"}\n', "not a supervisor ruling"),
        (
            "a ruling whose message is not prose",
            '{"completion": true, "message": 3}\n',
            "`message` for run",
        ),
        (
            "a ruling whose reason is not prose",
            '{"completion": false, "reason": ["drift"]}\n',
            "`reason` for run",
        ),
    ):
        answers.write_text(answer, encoding="utf-8")

        refused = _serve(json.dumps(SUPERVISOR_FRAME), environment)

        assert refused.returncode != 0, f"{case} was relayed: {refused.stdout}"
        assert expected in refused.stderr, f"{case}: {refused.stderr}"
        assert "completion" not in refused.stdout, f"{case}: {refused.stdout}"


def test_the_channel_filter_relays_a_ruling_whole_including_fields_it_does_not_know(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """An additive field in the planner's ruling reaches onejudge rather than dying here.

    The complement of the refusals above, and the reason the relay is deliberate:
    onejudge — not this filter — decides what a ruling may carry, so a onepipeline
    release that adds a field to its answer must not kill the monitor on its first turn,
    and must not have that field quietly withheld from the onejudge that would act on
    it. Both halves are asserted: the additive field arrives, and the three fields this
    filter does check arrive unaltered beside it.

    The request translation is held here too, because the same run proves it: what the
    stand-in receives on stdin is the observer frame `channel serve` reads, carrying the
    monitor's own last words, non-blocking so a watcher's question never stops the
    frontier.
    """
    environment = _environment(tmp_path, oneharness_bin)
    channel = tmp_path / "stand-in-channel"
    captured = tmp_path / "surface.json"
    # The input under test is an answer carrying a field no published `onepipeline`
    # release emits yet, so the published `channel serve` cannot produce it; the
    # filter, its argv, the frame, and the pipes are all real.
    # llmlint: ignore[e2e_not_mocked] No published onepipeline emits the field tested.
    channel.write_text(
        f"#!/usr/bin/env bash\ncat > {captured}\n"
        'echo \'{"completion": false, "message": "keep watching", "reason": "mid-run",'
        ' "surface_id": 7, "planner": {"session": "planner-1"}}\'\n',
        encoding="utf-8",
    )
    channel.chmod(0o755)
    environment[ONEPIPELINE_BIN] = str(channel)

    relayed = _serve(json.dumps(SUPERVISOR_FRAME), environment)

    assert relayed.returncode == 0, relayed.stderr
    ruling = json.loads(relayed.stdout)
    assert ruling["surface_id"] == 7, f"an additive field was dropped: {ruling}"
    assert ruling["planner"] == {"session": "planner-1"}, f"an additive field was dropped: {ruling}"
    assert ruling["completion"] is False, ruling
    assert ruling["message"] == "keep watching", ruling
    assert ruling["reason"] == "mid-run", ruling

    surface = json.loads(captured.read_text(encoding="utf-8"))
    assert surface == {
        "kind": "monitor",
        "message": "node api has drifted from its acceptance criteria",
        "blocking": False,
    }, surface


def test_the_channel_filter_reports_a_channel_that_cannot_be_executed(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A channel that passes for runnable and then fails to exec is still a diagnostic.

    The check that resolves `ONEPIPELINE_BIN` to an executable cannot answer whether the
    kernel will accept it, so this is the failure that survives it: a file with the bit
    set whose interpreter is not there. `subprocess.run` raises at the spawn itself, and
    the whole point of catching it is that the monitor's judge side reports a cause and a
    remedy — a traceback on this path would reach onejudge as a provider that produced no
    output, and say nothing about a broken checkout.
    """
    environment = _environment(tmp_path, oneharness_bin)
    channel = tmp_path / "channel-with-no-interpreter"
    channel.write_text("#!/nonexistent/interpreter\necho unreachable\n", encoding="utf-8")
    channel.chmod(0o755)
    environment[ONEPIPELINE_BIN] = str(channel)

    refused = _serve(json.dumps(SUPERVISOR_FRAME), environment)

    assert refused.returncode != 0, refused.stdout
    assert "could not run" in refused.stderr, refused.stderr
    assert "channel serve serve-e2e" in refused.stderr, refused.stderr
    assert "just bootstrap" in refused.stderr, refused.stderr
    assert "Traceback" not in refused.stderr, refused.stderr
    assert "completion" not in refused.stdout, refused.stdout


def test_the_channel_filter_reports_a_refusal_from_the_channel_itself(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A well-formed frame the *channel* rejects is reported, not smoothed over.

    The complement of the case above: here the filter's own validation passes and
    `onepipeline channel serve` is the one that refuses — an unknown run. The filter
    has a real answer to relay and none arrives, so it must say which end refused and
    exit non-zero rather than answer for the planner. Driven against a runs directory
    that genuinely holds no such run, so the refusal is the published one.
    """
    environment = _environment(tmp_path, oneharness_bin)

    refused = _serve(json.dumps(SUPERVISOR_FRAME), environment)

    assert refused.returncode != 0, refused.stdout
    assert "the planner channel refused the surface" in refused.stderr, refused.stderr
    assert "no such run" in refused.stderr, refused.stderr
    assert "completion" not in refused.stdout, refused.stdout


def test_a_nodes_turn_budget_reaches_the_dispatch_it_was_written_for(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """`max_turns` is forwarded now, where the retired schema version dropped it.

    Under schema 1 the field was accepted and then ignored, so a planner who gave a hard
    node more room silently got the base config's cap and a worker cut off mid-task. It
    is not observable from the conversation — with a stand-in supervisor that accepts on
    the first turn the cap is never reached — so this reads the effective onejudge config
    the dispatch was actually launched with, which is where the budget either arrived or
    did not.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    budget = 5
    environment = _environment(tmp_path, oneharness_bin)
    prompt_log = tmp_path / "prompts.jsonl"
    environment[PROMPT_LOG_ENV] = str(prompt_log)
    # `oneagentgraph` writes each member's effective config into its own scratch, and
    # names that directory here rather than under the host's state, so this reads the
    # run's own and never a concurrent dispatch's.
    scratch = tmp_path / "graph-state"
    environment["ONEAGENTGRAPH_STATE_DIR"] = str(scratch)
    plan = tmp_path / "budget.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "name": "turn-budget-e2e",
                "tasks": [_node(max_turns=budget)],
            }
        ),
        encoding="utf-8",
    )
    launch = _just("orchestrate", str(plan), environment=environment)
    try:
        assert launch.returncode == 0, launch.stdout + launch.stderr
        # No planner-facing view reports a node's turn budget — `results`, `status`,
        # `transcript`, and `goals` were each checked — and the conversation cannot show
        # it either, since a cap that is never reached leaves no trace. The config the
        # dispatch was launched with is where the budget arrived or did not, and the
        # launch that wrote it is the real recipe.
        # llmlint: ignore[tests_mirror_real_usage] No planner-facing view carries the budget.
        dispatched = sorted(scratch.glob("node-scope-*/members/worker/onejudge.yaml"))
        assert dispatched, (
            f"no dispatched worker config was written under {scratch}, so the budget "
            f"cannot be read from the dispatch it was written for:\n{launch.stdout}"
        )
        for config in dispatched:
            assert f"max_turns: {budget}" in config.read_text(encoding="utf-8"), (
                f"{config} did not receive the node's turn budget of {budget}"
            )
    finally:
        _just("stop", "turn-budget-e2e", environment=environment, seconds=60)


def _plans_in_the_repository() -> list[tuple[str, str]]:
    """Every plan document this repository ships, by where it is written.

    The shipped example files, plus the plan snippets `docs/` teaches from — a
    fenced JSON block that declares a `schema_version` is a plan an operator will
    copy, and it drifts from the published schema exactly as silently as a file
    does.
    """
    found = [
        (str(plan.relative_to(REPO_ROOT)), plan.read_text("utf-8"))
        for plan in sorted((REPO_ROOT / "examples").glob("*.json"))
    ]
    for document in sorted((REPO_ROOT / "docs").glob("*.md")):
        blocks = re.findall(r"```json\n(.*?)```", document.read_text("utf-8"), re.DOTALL)
        found.extend(
            (f"{document.relative_to(REPO_ROOT)} snippet {index}", block)
            for index, block in enumerate(blocks)
            if '"schema_version"' in block
        )
    return found


@pytest.mark.reads_docs
def test_every_plan_this_repository_ships_is_one_the_published_crate_accepts(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """No plan here declares a schema version or a field value the launcher refuses.

    One example is launched for real above; launching all of them would spend the
    wall clock of a full dispatch each to re-prove the same launch path. What is
    unproven without this is narrower and is what actually broke: the *document*.
    `onepipeline start` loads and validates the plan before repository preflight and
    agent-graph loading. Reaching either downstream boundary therefore proves the
    plan was accepted without launching paid work.

    One test rather than one per plan, because the documents are read here rather
    than at collection: `docs/` is outside the code-only tier's cache key, and a
    parametrization computed from it would be built in a tier that does not hash it.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    plans = _plans_in_the_repository()
    assert plans, "no plan documents were found to check"
    environment = _environment(tmp_path, oneharness_bin)
    absent_graph = str(tmp_path / "absent" / "dag-scope.yaml")
    plan = tmp_path / "candidate.plan.json"

    for origin, document in plans:
        plan.write_text(document, encoding="utf-8")
        refused = _just(
            "orchestrate",
            str(plan),
            "--detach",
            "--dag-graph",
            absent_graph,
            environment=environment,
            seconds=120,
        )
        reported = refused.stderr + refused.stdout
        reached_downstream_boundary = "dag-scope.yaml" in reported or "session holders" in reported
        assert reached_downstream_boundary, f"{origin} was not accepted as a plan:\n{reported}"


#: Every file that restates the `merge_policy` vocabulary in prose. Three, because
#: the routing policy, the plan schema, and the planner doctrine each need it in
#: front of the reader; none of them is its source.
MERGE_POLICY_PROSE = ("AGENTS.md", "docs/orchestration.md", "docs/repo-lifecycle.md")

#: A policy name as those files write it. Every published name is `local-` or
#: `change-` prefixed, which is what lets a whole document be swept for the
#: vocabulary rather than one sentence parsed out of it — so a name dropped from a
#: list and a name invented in a paragraph both fail.
POLICY_IN_PROSE = re.compile(r"`((?:local|change)-[a-z]+)`")

#: The retired spellings, anchored on the "old"/"older" each file introduces them
#: with so the pattern cannot wander onto another slash-separated triple. Prose is
#: hard-wrapped, so every gap here is any whitespace rather than a space.
RETIRED_IN_PROSE = re.compile(r"\bold(?:er)?\s+`([a-z-]+)`\s+/\s+`([a-z-]+)`\s+/\s+`([a-z-]+)`")

#: A `merge_policy` no vocabulary will contain, used to make the launcher enumerate
#: the one it does accept.
UNKNOWN_POLICY = "no-such-merge-policy"

#: How `onepipeline` refuses a policy: by naming the value, then listing what it
#: would have taken. That list is the vocabulary's one source.
REFUSED_POLICY = re.compile(r"unknown variant `([^`]+)`, expected one of ((?:`[^`]+`(?:, )?)+)")

#: The shipped lifecycle example, which is the plan shape `merge_policy` belongs to.
LIFECYCLE_PLAN = "examples/single-node-lifecycle.plan.json"


@pytest.fixture(scope="module")
def merge_policy_launch(
    tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str
) -> Callable[[str], str]:
    """Hand the real launcher a lifecycle plan carrying one policy, and report what it said.

    Pointed at an agent graph that is not there, exactly as the plan-acceptance
    journey above is: `onepipeline start` validates the plan before it reads
    anything else, so a policy it accepted is one whose only complaint is the graph
    and nothing runs either way.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("merge-policy")
    environment = _environment(tmp_path, oneharness_bin)
    absent_graph = str(tmp_path / "absent" / "dag-scope.yaml")
    plan = json.loads((REPO_ROOT / LIFECYCLE_PLAN).read_text(encoding="utf-8"))

    def launch(policy: str) -> str:
        plan["tasks"][0]["merge_policy"] = policy
        written = tmp_path / "candidate.plan.json"
        written.write_text(json.dumps(plan), encoding="utf-8")
        refused = _just(
            "orchestrate",
            str(written),
            "--detach",
            "--dag-graph",
            absent_graph,
            environment=environment,
            seconds=120,
        )
        assert refused.returncode != 0, f"merge_policy {policy!r} reached a real launch"
        return refused.stderr + refused.stdout

    return launch


@pytest.fixture(scope="module")
def published_merge_policies(merge_policy_launch: Callable[[str], str]) -> frozenset[str]:
    """The `merge_policy` vocabulary, from the only thing that decides it.

    `onepipeline` enumerates what it accepts in the refusal it writes for what it
    does not, so one deliberately impossible policy is the whole published list.
    """
    reported = merge_policy_launch(UNKNOWN_POLICY)
    refusal = REFUSED_POLICY.search(reported)
    assert refusal is not None, f"the launcher enumerated no vocabulary:\n{reported}"
    assert refusal.group(1) == UNKNOWN_POLICY, reported
    return frozenset(re.findall(r"`([^`]+)`", refusal.group(2)))


@pytest.mark.reads_docs
@pytest.mark.parametrize("relative_path", MERGE_POLICY_PROSE)
def test_the_merge_policy_vocabulary_in_prose_is_the_published_one(
    relative_path: str, published_merge_policies: frozenset[str]
) -> None:
    """No document here names a `merge_policy` the launcher would refuse, or omits one it takes.

    The vocabulary is the published crate's, and prose that restates it is a copy —
    which is the shape that goes stale in silence. A planner following a stale copy
    writes a plan that dies at launch, which is what the adoption of these names
    already did once.
    """
    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    named = set(POLICY_IN_PROSE.findall(text))
    assert named == set(published_merge_policies), (
        f"{relative_path} names merge policies {sorted(named)}; the launcher accepts "
        f"{sorted(published_merge_policies)}"
    )


@pytest.mark.reads_docs
def test_the_retired_merge_policy_spellings_are_refused_by_name(
    merge_policy_launch: Callable[[str], str], published_merge_policies: frozenset[str]
) -> None:
    """Every file promising the old spellings are refused by name names spellings that are.

    The other half of the same contract: a reader is told what their pre-adoption
    plan will do, and the promise is only worth the launch that keeps it. One set of
    launches for all three files, because a file that spelled the triple differently
    is itself the drift this rejects.
    """
    quoted = {
        relative_path: RETIRED_IN_PROSE.search(
            (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        )
        for relative_path in MERGE_POLICY_PROSE
    }
    missing = sorted(path for path, found in quoted.items() if found is None)
    assert not missing, f"{missing} no longer name the retired merge-policy spellings"
    spellings = {path: found.groups() for path, found in quoted.items() if found is not None}
    assert len(set(spellings.values())) == 1, (
        f"the files disagree on the retired spellings: {spellings}"
    )

    for spelling in next(iter(spellings.values())):
        assert spelling not in published_merge_policies, f"`{spelling}` is a published policy"
        assert f"unknown variant `{spelling}`" in merge_policy_launch(spelling), (
            f"the launcher does not refuse `{spelling}` by name"
        )


#: How long the stand-in holds a worker turn open so a journey can act while the run
#: is unmistakably live. Long enough to dispatch an edit and read the run back inside
#: it, short enough that the journey is not the slowest thing in the suite.
WORKER_HELD_SECONDS = 20

#: The verb the engine used to be advanced by. It is gone, and its absence is half of
#: what "settles on its own" means: the other half is a run that settled anyway.
RETIRED_ADVANCING_VERB = "round"

#: The budget that used to bound how many times a run could be advanced.
RETIRED_ADVANCING_FLAG = "--round-budget"


@pytest.mark.xdist_group("orchestrate-launch")
def test_a_launch_settles_with_no_verb_left_that_could_have_advanced_it(
    launched: Launched,
) -> None:
    """The run reached a complete settlement, and nothing exists that could have driven it.

    Two halves, and neither is worth much alone. That a launch returns `complete` was
    already true when a round verb drove the graph forward between planner boundaries,
    so on its own it does not say the engine is roundless. That the published surface
    has no advancing verb does not say a run still settles. Together they are the
    claim: `just orchestrate` was the only command run against this run, the graph
    completed, and there is no verb that could have been the thing that advanced it.

    The surface is read from the pinned binary rather than asserted from memory, so a
    release that reintroduced an advancing verb fails here instead of quietly making
    this journey's premise false.
    """
    surface = surface_of("onepipeline")
    assert (RETIRED_ADVANCING_VERB,) not in surface.paths, (
        f"`onepipeline {RETIRED_ADVANCING_VERB}` is back, so a run is no longer driven "
        "to settlement by the engine alone"
    )
    assert RETIRED_ADVANCING_FLAG not in surface.flags[("start",)], (
        f"`onepipeline start` accepts {RETIRED_ADVANCING_FLAG} again, so a run is bounded "
        "by rounds rather than driven to settlement"
    )

    settlement = json.loads(launched.launch.stdout.strip().splitlines()[-1])
    assert settlement == {"run_id": SHIPPED_RUN, "settlement": "complete"}

    # And the graph really finished, rather than the launch merely returning: every
    # node this run has reports an outcome, read the way a planner reads one.
    outcomes = _just("results", SHIPPED_RUN, environment=launched.environment, seconds=60)
    assert outcomes.returncode == 0, outcomes.stderr
    assert "research" in outcomes.stdout, outcomes.stdout


#: A run's own name on the ledger. Every planner-facing verb takes one, and a plan's
#: `name` is where it comes from, so the two are the same string for a reason.
RunId = NewType("RunId", str)


class LiveRun(NamedTuple):
    """A run with a dispatched worker still in flight, and the launch driving it."""

    environment: dict[str, str]
    run: RunId
    #: The attached launch. Held so the driver stays alive; killed on teardown.
    launch: subprocess.Popen[str]


@pytest.fixture
def live_run(tmp_path: Path, oneharness_bin: str) -> Iterator[LiveRun]:
    """Launch a run whose only node is held open, and hand it over while it runs.

    `--dag-graph off` deliberately: this run has no monitor and no pacemaker, so
    nothing raises a planner surface and there is no boundary of any kind for an edit
    to be waiting on. That is the point — an edit accepted here was accepted mid-run
    or not at all.

    Attached rather than detached, because a detached launch's driver exits as soon as
    the graph has nothing to schedule, and a run with no driver executes nothing an
    edit adds.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    run = RunId("live-edit-e2e")
    environment = _environment(tmp_path, oneharness_bin)
    environment[AGENT_DELAY_ENV] = str(WORKER_HELD_SECONDS)
    plan = tmp_path / "live.plan.json"
    live: CandidatePlan = {"schema_version": 2, "name": run, "tasks": [_node(id="held")]}
    plan.write_text(json.dumps(live), encoding="utf-8")
    launch = subprocess.Popen(  # noqa: S603 - the real recipe, as an operator runs it
        ["just", "orchestrate", str(plan), "--dag-graph", "off"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        yield LiveRun(environment, run, launch)
    finally:
        launch.kill()
        launch.wait(timeout=e2e_timeout(60))
        _just("stop", run, environment=environment, seconds=60)


def _dispatched(live: LiveRun) -> str:
    """Wait until the run has a worker in flight, and report the stream that proves it."""
    limit = deadline(120)
    while True:
        stream = _just("monitor", live.run, "--all", environment=live.environment, seconds=60)
        if stream.returncode == 0 and "node-dispatched" in stream.stdout:
            return stream.stdout
        if time.monotonic() >= limit:
            pytest.fail(f"no worker was ever dispatched:\n{stream.stdout}\n{stream.stderr}")
        time.sleep(0.2)


def test_a_graph_edit_is_accepted_while_a_node_is_still_running(live_run: LiveRun) -> None:
    """The reconciler takes an edit at any moment, not at a boundary it hands out.

    Under the round model a change to the plan waited for a boundary: the run reached
    the end of a round, surfaced to the planner, and the planner's answer was where an
    edit could be applied. The engine reconciles a live desired graph continuously now,
    and this is what that means concretely — the edit below is sent while a dispatched
    worker is mid-turn, to a run with no observer graph attached, so no surface has
    been raised and none has been consumed. There is no boundary here to have waited
    for.

    What is asserted is not only that the reply was accepted but that the graph it was
    applied to went on to *execute* the added node. An edit recorded and never
    scheduled would satisfy an exit status and nothing else.
    """
    _dispatched(live_run)
    # Nothing has been handed out to be answered: this run has no observer, so the
    # edit cannot be riding on a surface even accidentally.
    pending = _just("channel-next", live_run.run, environment=live_run.environment, seconds=60)
    assert pending.returncode == 0, pending.stderr
    # `cast` rather than a validating read: `SurfaceRead` states the three fields this
    # suite consumes from `onepipeline next`'s own schema, and the subscript below
    # fails loudly if the answer is not that shape.
    assert cast(SurfaceRead, json.loads(pending.stdout))["surface"] is None, (
        f"this run raised a planner surface, so the edit below could be answering one "
        f"rather than reaching the reconciler mid-run:\n{pending.stdout}"
    )

    # No persona: `expects_no_diff` settles without a dispatch, and the launcher
    # refuses a node that declares both — so this is written out rather than built
    # from `_node`, whose default is an agent node.
    settles_without_dispatch: PlanNode = {
        "id": "added-mid-run",
        "task": "Report.",
        "expects_no_diff": True,
    }
    add: EditCommand = {"op": "add", "node": settles_without_dispatch}
    added: ReplyEnvelope = {"version": 1, "commands": [add]}
    applied = subprocess.run(
        ["just", "channel-reply", live_run.run],
        cwd=REPO_ROOT,
        env=live_run.environment,
        input=json.dumps(added),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert applied.returncode == 0, applied.stderr + applied.stdout

    # The run was still going when that landed, which is the whole claim: the held
    # worker had not answered yet, so the graph had not reached anything a boundary
    # could have been.
    running = _just("status", live_run.run, environment=live_run.environment, seconds=60)
    assert running.returncode == 0, running.stderr
    assert "SETTLED" not in running.stdout.splitlines()[0], (
        f"the run had already settled, so this proves nothing about a live edit:\n{running.stdout}"
    )

    settling = deadline(180)
    while True:
        stream = _just(
            "monitor", live_run.run, "--all", environment=live_run.environment, seconds=60
        )
        if stream.returncode == 0 and "added-mid-run" in stream.stdout:
            break
        if time.monotonic() >= settling:
            pytest.fail(
                "the node added mid-run never reached the executing graph:\n"
                f"{stream.stdout}\n{stream.stderr}"
            )
        time.sleep(0.5)
    assert "edit-committed" in stream.stdout, stream.stdout


class VerdictRecipe(NamedTuple):
    """One verdict recipe and the arguments an operator types after it."""

    recipe: str
    arguments: tuple[str, ...]


#: Every verdict recipe, and the envelope each renders. The three are the planner's
#: whole legacy vocabulary on the channel, and each is sent here through the recipe an
#: operator types rather than as JSON a test wrote.
VERDICT_RECIPES = (
    VerdictRecipe("channel-continue", ("keep going",)),
    VerdictRecipe("channel-reject", ("the gate never ran",)),
    VerdictRecipe("channel-approve", ()),
)


class SupervisedGate(NamedTuple):
    """A run whose channel has a reader, and the environment that reaches it."""

    environment: dict[str, str]
    run: RunId


@pytest.fixture
def supervised_gate(tmp_path: Path, oneharness_bin: str) -> Iterator[SupervisedGate]:
    """A monitored run whose planner channel has somebody waiting on the other end."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    run = RunId("verdict-recipes-e2e")
    environment = _environment(tmp_path, oneharness_bin)
    plan = tmp_path / "verdict.plan.json"
    # A human action is nobody's to dispatch, so it names no persona.
    gate: PlanNode = {
        "id": "gate",
        "kind": "human",
        "task": "## What\nApprove.\n\n## Why\nProbe.\n\n## Acceptance criteria\n- Approved.",
    }
    supervised: CandidatePlan = {
        "schema_version": 2,
        "goal": {"text": "prove the verdict recipes reach the live channel"},
        "name": run,
        "tasks": [gate],
    }
    plan.write_text(json.dumps(supervised), encoding="utf-8")
    launch = subprocess.Popen(  # noqa: S603 - the real recipe, as an operator runs it
        ["just", "orchestrate", str(plan), "--heartbeat-interval", str(PACEMAKER_INTERVAL_SECONDS)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        yield SupervisedGate(environment, run)
    finally:
        launch.kill()
        launch.wait(timeout=e2e_timeout(60))
        _just("stop", run, environment=environment, seconds=60)


# One run name serves all three parametrizations, and the fixture stops it by that name
# on the way out, so two of them on separate workers would stop each other's run. The
# constraint is the scheduling, never a longer deadline: the loop below is only waiting
# for a rendezvous its own run holds open.
@pytest.mark.xdist_group("verdict-recipes")
@pytest.mark.parametrize(("recipe", "arguments"), VERDICT_RECIPES, ids=lambda row: str(row))
def test_a_verdict_recipe_is_accepted_by_the_live_planner_channel(
    supervised_gate: SupervisedGate, recipe: str, arguments: tuple[str, ...]
) -> None:
    """Each verdict recipe renders an envelope the real channel takes and delivers.

    `tests/e2e/test_delegated_recipes_e2e.py` proves what these three write, against a
    traced `uv`; it cannot prove that `onepipeline reply` accepts it, and an envelope
    the CLI refuses would pass there and fail an operator. So each one is sent here to
    a real run whose channel has a reader waiting, through the recipe as typed.

    Retried against successive surfaces rather than sent once. A verdict is refused
    outright when nothing is waiting to read it — the engine says so by name — so the
    thing under test is only observable while a rendezvous is open, and which surface
    that is, is not this journey's claim.
    """
    environment, run = supervised_gate
    limit = deadline(180)
    delivered = None
    while delivered is None:
        if time.monotonic() >= limit:
            pytest.fail(f"`just {recipe}` was never accepted by the channel of run {run}")
        handed = _just("channel-next", run, environment=environment, seconds=60)
        if handed.returncode != 0 or not handed.stdout.strip():
            time.sleep(0.1)
            continue
        # `cast` for the same reason as above: the published verb owns this schema,
        # and `SurfaceRead` states only the part this suite reads back from it.
        if cast(SurfaceRead, json.loads(handed.stdout))["surface"] is None:
            time.sleep(0.1)
            continue
        answered = _just(recipe, run, *arguments, environment=environment, seconds=60)
        if answered.returncode == 0:
            delivered = answered.stdout
    assert '"state":"delivered"' in "".join(delivered.split()), delivered
