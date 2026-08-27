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
from harness_indirections import established_indirections, harness_routing
from no_paid_provider import REFUSAL, VERSION
from observer_environment import ENVIRONMENT_PATH_ENV
from project_fixtures import project_from_plan, read_project_plan
from published_surface import surface_of
from shared_dispatch_bar import (
    shared_agent_preamble,
    shared_completion_bar,
    shared_judge_persona,
)
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

#: The directory `_environment` puts ahead of everything on `PATH`, holding a `claude`
#: that refuses the turn. It covers the identity a journey never means to select, which
#: `ONEHARNESS_BIN_*` cannot: that seam keys on a harness id and reaches no variant.
PAID_PROVIDER_GUARD = Path(__file__).resolve().parent / "no-paid-provider"

#: The shipped example this journey launches. A plan written for this repository's
#: own operators, so a schema or field the published crate stopped accepting fails
#: here rather than the first time a planner types it.
SHIPPED_PROJECT = "examples:scheduler-research"

#: The run id `onepipeline` mints from that plan's `name`.
SHIPPED_RUN = "scheduler-research"

#: The plan schema written in the shipped example projects' onepipeline metadata.
PLAN_SCHEMA_VERSION = 3

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

#: The probe that stands exactly where `scripts/channel-serve.py` stands — as an
#: observer member's `judge.command` — and writes out the environment it was started
#: with, so the export below is measured rather than asserted from prose.
OBSERVER_PROBE = Path(__file__).resolve().parent / "observer_environment.py"

#: The variable `onepipeline` names an observer member's run with, taken from the
#: measurement below rather than from any document that restates it.
RUN_ID_ENV = "ONEPIPELINE_RUN_ID"

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
#: the member, the recipe, and the verb — and with the run spelled as the variable the
#: launch exports, because that is what binds the read to this run rather than to
#: whichever one the member inferred it was watching.
DETAILED_STREAM_COMMAND = 'onepipeline monitor "$ONEPIPELINE_RUN_ID" --filter monitor'

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


class Surface(TypedDict, total=False):
    """The fields of a handed-out surface this suite reads. `onepipeline` owns the rest.

    `kind` and `message` are on every surface; `source` names who raised it and
    `blocking` whether the run is held on it. `workstream` is present only when the
    surface was raised about a node, which is itself a claim these journeys make.
    """

    kind: Required[str]
    message: Required[str]
    source: str
    blocking: bool
    workstream: str


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
    message: str
    blocking: bool


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


#: Who the helpers attribute their diagnostics to when one of them refuses.
INDIRECTION_CALLER = "tests/e2e/test_orchestrate_launch_e2e.py"


def _environment(
    tmp_path: Path, oneharness_bin: str, *, session: str = LAUNCHING_SESSION
) -> dict[str, str]:
    """The environment one launched run and its planner views share.

    Three journeys here reach a **real** `oneharness`, and they are the three whose
    member is single-sided `kind: oneharness`: the `graphs/pr-author.yaml` drafter both
    `_drafted` journeys run, and the `oneharness.check-in.toml` probe
    `test_the_graphs_declared_version_is_one_that_expands_the_task_placeholder` builds.
    Since oneagentgraph 0.2.18 such a member runs its turn through the oneharness
    *library* on a thread of the graph process, so `ONEAGENTGRAPH_ONEHARNESS_BIN` never
    intercepts it and the real oneharness resolves each variant's indirection from this
    environment. Every other member here is two-party `kind: onejudge`, whose spawned
    CLI the fake backend replaces before any variant resolves — which is why the control
    case at `test_node_graph_uses_the_generic_base_when_no_persona_is_overridden` stayed
    green while those three failed on a missing indirection.

    So establishing the indirections makes those three pass by letting a real oneharness
    run, which is the opposite of doubling it. Do not read the fake backend as covering
    every path; the two seams below are what cover this one.
    """
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
    # The provider the library path still spawns, for the identity every config here
    # names first. Deliberately weaker than the `--mock-harness` the two-party path
    # uses — measured against oneharness 0.10.1, a mocked harness keeps its mock binary
    # and ignores this variable — so the two seams do not collide.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # And the identities that seam cannot reach; see `no_paid_provider`.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment.update(established_indirections(INDIRECTION_CALLER))
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
        SHIPPED_PROJECT,
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
        project_from_plan(plan),
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
    # Read from the file rather than quoted here: the default review contract is
    # `config/onejudge.base.yaml`'s to state, and a copy of it in this journey is a
    # second source to disagree with it. `tests/test_shared_dispatch_bar.py` holds
    # what that contract may say; this holds that it is what arrives.
    persona = shared_judge_persona()
    assert any(persona in " ".join(prompt.split()) for prompt in prompts), (
        "a graph invocation with no persona override was supervised against something "
        f"other than the generic review contract in config/onejudge.base.yaml:\n{persona}"
    )


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


@pytest.mark.reads_docs
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
    It is a real exclusion, not a tautology: the criterion a judge is handed is composed
    at dispatch, and a persona declaring `user.done_when_replaces_base` drops this clause
    out of that composition, leaving its own bar in place of it — which this reads as the
    shared bar never arriving. Pointing the node at a built-in role that declares a bar of
    its own — `planner`, `reviewer`, `researcher` — is *not* how that is reached and would
    still pass here: none of the five shipped roles declares that field, so the two bars
    are enforced together, as `Both of these must hold:` with this one stated first. See
    `personas/README.md`, "Which of these files a dispatch actually reads", for that
    measurement.
    """
    shared_bar = shared_completion_bar()
    task_record = REPO_ROOT / "examples/tasks/scheduler-research/research.md"
    task = task_record.read_text(encoding="utf-8").split("---", 2)[2]
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
    node that role is the one built into the tool, which is why `personas/` carries no
    file for it to be confused with.
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
        SHIPPED_PROJECT,
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
        project_from_plan(written),
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
        (project, node)
        for project in _plans_in_the_repository()
        # llmlint: ignore[suppressions_justified] The CLI fixture validates this list.
        for node in cast(list[dict[str, object]], read_project_plan(project)["tasks"])
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
#: `add` and `finding` are the two of the six that still apply to a settled graph, so
#: each is exercised on a run of its own rather than against this settled one.
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
    refusal read alike to a monitor that only checks the exit status. `add` and
    `finding` are exercised separately below, since they are the two that still apply.
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
    launch = _just("orchestrate", project_from_plan(plan), environment=environment)
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


class RefusedFinding(NamedTuple):
    """A `finding` the engine refuses on its own contents, and how it words the refusal.

    Named rather than positional because the two halves are different claims — what a
    monitor sent, and what the engine told it — and because a finding is refused for
    what it *carries* rather than for who asked or what the graph is, which is what
    separates these from `OPS_THE_MONITOR_MAY_NOT_ISSUE` and
    `MONITOR_OPS_ON_A_SETTLED_GRAPH`.
    """

    command: EditCommand
    refusal: str


#: A finding with nothing in it reports nothing, and one filed against a node the run
#: does not have files it nowhere.
FINDING_REFUSALS = (
    RefusedFinding(
        {"op": "finding", "message": ""},
        "a finding carries what was found: this one has an empty message",
    ),
    RefusedFinding(
        {"op": "finding", "message": "seen", "id": "nosuch"},
        "cannot raise a finding about node 'nosuch', which this run does not have",
    ),
)


def _reply(
    environment: dict, run: str, envelope: ReplyEnvelope
) -> subprocess.CompletedProcess[str]:
    """Send one envelope on a run's channel, through the recipe an author really uses."""
    return subprocess.run(
        ["just", "channel-reply", run],
        cwd=REPO_ROOT,
        env=environment,
        input=json.dumps(envelope),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )


def _read_one(environment: dict, run: str) -> Surface | None:
    """The next surface the run hands out, read the way a planner reads one.

    `channel-next` is the only consumer, so this is what a manager working a queue down
    actually sees — and reading it that way is what lets a claim about *absence* be made
    about the planner's own view rather than about a file behind it.
    """
    read = _just("channel-next", run, environment=environment, seconds=60)
    assert read.returncode == 0, read.stderr
    return cast(SurfaceRead, json.loads(read.stdout))["surface"]


def _drain(environment: dict, run: str) -> Iterator[Surface]:
    """Each surface in turn, so a caller can read the run's state between two reads."""
    for _ in range(MOST_SURFACES_A_SETTLED_RUN_QUEUES):
        handed = _read_one(environment, run)
        if handed is None:
            return
        yield handed


#: What `just status` prefixes a consumed-but-unanswered blocking surface with.
AWAITING_A_DECISION = "waiting for planner decision"


def _awaiting_a_decision(environment: dict, run: str) -> list[str]:
    """The lines `just status` renders for surfaces it has handed out and is holding.

    A pending blocking surface's one planner-facing signal: `waiting for planner
    decision: <kind> — <message>`, which is what a manager sees while a question is
    outstanding. Measured on a real run rather than restated from the crate.
    """
    shown = _just("status", run, environment=environment, seconds=60)
    assert shown.returncode == 0, shown.stderr
    return [line.strip() for line in shown.stdout.splitlines() if AWAITING_A_DECISION in line]


def test_a_monitor_finding_raises_one_surface_and_mutates_no_graph(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The structured way a monitor reports, and the two properties that distinguish it.

    `personas/orchestrator.yaml` offers `finding` beside the five ops that change the
    graph, and tells the monitor both of the things asserted here — so a release that
    moved either would leave that persona teaching something the engine no longer does.
    It adds no node, and unlike every other op the monitor may issue it raises *no*
    `monitor-edit` surface beside itself: the finding is the report, and a second
    surface would double every observation in the one line a planner may not filter.
    Both are read through the planner's own views — the surfaces off `channel-next`,
    the graph off `results` — because those are where a monitor's report is either
    visible or not. A run of its own, since draining a queue consumes it.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    run = "monitor-finding-e2e"
    environment = _environment(tmp_path, oneharness_bin)
    plan = tmp_path / "monitor-finding.plan.json"
    plan.write_text(
        json.dumps({"schema_version": 2, "name": run, "tasks": [_node(id="only")]}),
        encoding="utf-8",
    )
    launch = _just("orchestrate", project_from_plan(plan), environment=environment)
    try:
        assert launch.returncode == 0, launch.stdout + launch.stderr

        for refused_finding in FINDING_REFUSALS:
            refused = _reply(
                environment,
                run,
                {"version": 1, "author": "monitor", "commands": [refused_finding.command]},
            )
            assert refused.returncode != 0, refused.stdout
            reported = refused.stderr + refused.stdout
            assert "is not an op the monitor may issue" not in reported, (
                f"a `finding` was refused for who asked rather than for what it "
                f"carried:\n{reported}"
            )
            assert refused_finding.refusal in reported, reported

        said = "issue: the finding op reached the engine"
        applied = _reply(
            environment,
            run,
            {
                "version": 1,
                "author": "monitor",
                "commands": [{"op": "finding", "message": said, "id": "only"}],
            },
        )
        assert applied.returncode == 0, applied.stderr + applied.stdout

        handed = list(_drain(environment, run))
        raised = [surface for surface in handed if surface["message"] == said]
        assert len(raised) == 1, (
            f"the `finding` op did not reach the planner as exactly one surface: {handed}"
        )
        assert raised[0]["kind"] == "finding", raised[0]
        assert raised[0]["source"] == "monitor", raised[0]
        assert raised[0]["workstream"] == "only", (
            f"a finding naming a node is no longer filed against that workstream: {raised[0]}"
        )
        assert raised[0]["blocking"] is False, (
            f"a finding is an observation, and must not stop the frontier: {raised[0]}"
        )

        # The half a one-surface assertion would miss. `monitor-edit` is what every
        # other in-allowlist op queues, so its absence from the whole drained queue is
        # the claim: the persona tells the model a finding arrives once, and this is
        # what makes that true.
        assert "monitor-edit" not in [surface["kind"] for surface in handed], (
            f"the engine raised a `monitor-edit` surface beside the finding, so every "
            f"observation now costs the planner two surfaces: {handed}"
        )

        # And that the report changed nothing it was reporting on. `results` is the
        # planner's per-node view, so a graph that gained a node shows up here.
        outcomes = _just("results", run, environment=environment, seconds=60)
        assert outcomes.returncode == 0, outcomes.stderr
        named = [line.split()[0] for line in outcomes.stdout.splitlines()[1:] if line.strip()]
        assert named == ["only"], (
            f"the `finding` op changed the graph, which is what makes it safe to reach "
            f"for on any observation:\n{outcomes.stdout}"
        )
    finally:
        _just("stop", run, environment=environment, seconds=60)


def test_a_blocking_surface_is_handed_out_first_and_reading_past_it_leaves_it_pending(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A worker's question can no longer sit behind a pile of observations.

    Both halves of the ordering `AGENTS.md` tells a manager to rely on. Every surface
    the monitor and the pacemaker raise is non-blocking by construction, so this run
    queues those and then one blocking finding *last*: the first read must hand out the
    blocking one anyway. And once it is handed out, reading the non-blocking surfaces
    behind it must leave it awaiting a decision — only a verdict answers it — because a
    manager draining a queue must not consume the question by accident. Read through
    `just status`, which is where a manager sees an outstanding question, rather than
    through the file behind it.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    run = "blocking-first-e2e"
    environment = _environment(tmp_path, oneharness_bin)
    plan = tmp_path / "blocking-first.plan.json"
    plan.write_text(
        json.dumps({"schema_version": 2, "name": run, "tasks": [_node(id="only")]}),
        encoding="utf-8",
    )
    launch = _just("orchestrate", project_from_plan(plan), environment=environment)
    try:
        assert launch.returncode == 0, launch.stdout + launch.stderr

        def _raise(message: str, *, blocking: bool) -> None:
            sent = _reply(
                environment,
                run,
                {
                    "version": 1,
                    "author": "monitor",
                    "commands": [{"op": "finding", "message": message, "blocking": blocking}],
                },
            )
            assert sent.returncode == 0, sent.stderr + sent.stdout

        _raise("observation queued first", blocking=False)
        _raise("observation queued second", blocking=False)
        asked = "question queued last, and read first"
        _raise(asked, blocking=True)

        first = _read_one(environment, run)
        assert first is not None, "the run handed out no surface at all"
        assert first["message"] == asked, (
            f"`channel-next` handed out a non-blocking surface while a blocking one was "
            f"queued behind two of them, so a worker's question can be buried again: "
            f"{first}"
        )

        read_past = 0
        for surface in _drain(environment, run):
            read_past += 1
            assert _awaiting_a_decision(environment, run) == [
                f"{AWAITING_A_DECISION}: finding — {asked}"
            ], (
                f"reading the non-blocking surface {surface['message']!r} cleared the "
                f"question the run is holding, so a manager draining the queue consumed "
                f"it"
            )
        assert read_past >= 2, (
            f"only {read_past} surface(s) were left to read past the question, so this "
            "run cannot show that reading past one leaves it standing"
        )
    finally:
        _just("stop", run, environment=environment, seconds=60)


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
        [
            "just",
            "orchestrate",
            project_from_plan(plan),
            "--heartbeat-interval",
            str(PACEMAKER_INTERVAL_SECONDS),
        ],
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


def _monitor_agent_turns(prompt_log: Path) -> list[PromptRecord]:
    """Every turn the monitor's AGENT side was given, newest last."""
    return [
        turn
        for turn in _turns_so_far(prompt_log)
        if "/members/monitor/" in (turn["config"] or "")
        and Path(turn["config"] or "").name != JUDGE_CONFIG_NAME
    ]


def _monitor_turns(prompt_log: Path) -> list[str]:
    """Every prompt the monitor's AGENT side was given, newest last."""
    return [turn["prompt"] for turn in _monitor_agent_turns(prompt_log)]


def _await(found: Callable[[], bool], seconds: float, failure: Callable[[], str]) -> None:
    """Wait for a live run to show something, or fail saying what it showed instead."""
    limit = deadline(seconds)
    while not found():
        if time.monotonic() >= limit:
            pytest.fail(failure())
        time.sleep(0.1)


#: What `personas/orchestrator.yaml` demands before a finding may be called a rule
#: violation, and where one it cannot ground goes instead. Read out of the SYSTEM prompt
#: the monitor was actually given rather than out of the persona file, which is the
#: point: a persona is an agent's role, so it arrives as the turn's system prompt after
#: travelling the base config, `oneagentgraph`, and `oneharness`, and none of those
#: reports what it passed on.
GROUNDING_DEMANDED = "Quote the file and the line it"
GROUNDING_FALLBACK = "observation"


@pytest.mark.xdist_group("planner-supervises-monitor")
def test_the_monitor_is_told_to_ground_a_violation_before_it_alleges_one(
    planner_supervised: Supervised,
) -> None:
    """The grounding rule reaches a real monitor turn, not just the persona file.

    A monitor that infers the rule an agent is judged against reports a protective act
    as a breach, which costs a planner more than silence would. So it must quote the
    file and line a rule comes from before calling anything a violation, and report
    what it cannot ground as an observation instead.

    That rule is prose handed to a model — nothing enforces it — so the one thing that
    would silently un-enforce it is the prose not arriving. `graphs/dag-scope.yaml`
    names the persona, but naming is not delivery: it travels the base config,
    `oneagentgraph`, and `oneharness` to reach a turn as that turn's system prompt, and
    none of them reports what it passed on. This reads the system prompt the monitor
    was given on a supervised launch.

    `tests/test_observer_grounding.py` holds the sides that state the rule and the
    section that explains it. This holds the one thing that file cannot: that a
    watching member is actually given it.
    """

    def told() -> list[str]:
        return [turn["system"] for turn in _monitor_agent_turns(planner_supervised.prompt_log)]

    _await(
        lambda: any(
            GROUNDING_DEMANDED in system and GROUNDING_FALLBACK in system for system in told()
        ),
        seconds=90,
        failure=lambda: (
            "the monitor was never told to ground a rule violation in a quoted file and "
            "line, so a rule it inferred reaches the planner as a breach; its turns were "
            f"given:\n{told()}"
        ),
    )


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

    `{task}` is the load-bearing part, and it is what this graph is written against
    rather than the only thing available: `onepipeline` does export
    `ONEPIPELINE_RUN_ID` to an observer member, which the journey below measures. The
    composed task is preferred because it is this graph's own contract rather than a
    per-release export, and because it carries the run's goal as well as its id — so
    a pacemaker reaching for the variable instead is a member written against
    something no document here promises.
    """
    # llmlint: ignore[tests_mirror_real_usage] A 30-minute schedule outlasts a journey.
    _, task = _dag_scope_document()

    assert "{task}" in task, (
        "the pacemaker no longer interpolates the composed task, so nothing tells it "
        f"which run to report on: {task}"
    )
    assert "$ONEPIPELINE_RUN_ID" not in task, (
        "the pacemaker reads its run from a per-release export rather than from the "
        f"composed task this graph is written against: {task}"
    )
    assert f"Never send `{FORBIDDEN_OF_THE_PACEMAKER}`" in task, (
        f"the pacemaker's task no longer forbids the edit verb by name: {task}"
    )


#: The run id `onepipeline` mints from the probing plan's `name`, and what the export
#: measured below has to be equal to.
OBSERVED_RUN = "observer-environment-e2e"


@pytest.fixture(scope="module")
def observed_environment(
    tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str
) -> dict[str, str]:
    """The environment a real launch starts its observer member's judge side in.

    An observer graph is attached by path and nothing else about it is this
    repository's to choose, so the graph here is `graphs/dag-scope.yaml`'s monitor
    member with one substitution: the probe takes `scripts/channel-serve.py`'s place
    as `judge.command`. Everything the environment could come from is left real — the
    `just` recipe, `onepipeline`'s driver, and the `oneagentgraph` run it starts the
    observer with.

    Written out rather than copied from the shipped document because every ref in
    that file is resolved against its own directory, so a copy anywhere else resolves
    none of them.

    Two nodes rather than one, so the run has work left while the monitor completes
    the exchange this measurement reads. A journey that raced settlement would report
    a missing export as a missing variable.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("observer-environment")
    environment = _environment(tmp_path, oneharness_bin)
    recorded = tmp_path / "observer-environment.json"
    environment[ENVIRONMENT_PATH_ENV] = str(recorded)
    graph = tmp_path / "observer.yaml"
    graph.write_text(
        "version: 4\n"
        "name: observer-environment\n"
        "members:\n"
        "  monitor:\n"
        "    kind: onejudge\n"
        f"    base_config: {REPO_ROOT / 'config/onejudge.base.yaml'}\n"
        f"    persona: {REPO_ROOT / 'personas/orchestrator.yaml'}\n"
        "    agent:\n"
        f"      oneharness_config: {REPO_ROOT / 'oneharness.orchestrator.toml'}\n"
        "    judge:\n"
        f'      command: ["{OBSERVER_PROBE}"]\n'
        "    mode: bypass\n",
        encoding="utf-8",
    )
    plan = tmp_path / "observer.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": PLAN_SCHEMA_VERSION,
                "goal": {"text": "measure what a run names to the graph watching it"},
                "name": OBSERVED_RUN,
                "concurrency": 1,
                "tasks": [
                    {
                        "id": "watched",
                        "task": "Report without changing files.",
                        "expects_no_diff": True,
                    },
                    {
                        "id": "watched-again",
                        "task": "Report without changing files.",
                        "expects_no_diff": True,
                        "deps": ["watched"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    launch = _just(
        "orchestrate",
        project_from_plan(plan),
        "--dag-graph",
        str(graph),
        environment=environment,
    )
    try:
        assert launch.returncode == 0, f"{launch.stdout}\n{launch.stderr}"
        assert recorded.is_file(), (
            "the observer member's judge side never ran, so this launch measured "
            f"nothing:\n{launch.stdout}\n{launch.stderr}"
        )
        # `onepipeline`'s own environment, as it handed it to the graph it attached.
        return cast(dict[str, str], json.loads(recorded.read_text(encoding="utf-8")))
    finally:
        _just("stop", OBSERVED_RUN, environment=environment, seconds=60)


@pytest.mark.xdist_group("observer-environment")
def test_a_launch_names_its_run_to_the_graph_watching_it(
    observed_environment: dict[str, str],
) -> None:
    """`onepipeline` exports `ONEPIPELINE_RUN_ID` to an observer member, set to the run.

    The gate on a per-release fact this repository states in four places. Nothing
    about the export is this repository's to decide, so a release that moved it would
    otherwise leave every one of those paragraphs reading true.

    A failure here is a re-measurement, not a repair. If the variable is gone or
    renamed, `graphs/dag-scope.yaml`, `docs/orchestration.md`, `AGENTS.md`, and
    `scripts/channel-serve.py` each say what was measured and against which release,
    and all of them move in the same change as this one.

    Nothing reads the variable: `scripts/channel-serve.py` and the `check-in`
    pacemaker take their run from the composed task, which is this graph's own
    contract. The export is documented, so it is measured.
    """
    assert observed_environment.get(RUN_ID_ENV) == OBSERVED_RUN, (
        f"the observer member was started with {RUN_ID_ENV}="
        f"{observed_environment.get(RUN_ID_ENV)!r}, not {OBSERVED_RUN!r}; re-measure "
        "the export and correct every document that states it, in this change"
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


#: Every Claude identity the pacemaker's chain names, read from that chain: these are
#: the candidates `ONEHARNESS_BIN_CLAUDE_CODE` would leave resolving to the real
#: `claude` — the seam keys on a harness id and reaches no variant — so an identity
#: added to the config has to be covered here rather than escaping the guard silently.
UNINTENDED_CLAUDE_IDENTITIES = tuple(
    identity
    for identity in harness_routing(REPO_ROOT / "oneharness.check-in.toml")["harnesses"]
    if identity.startswith("claude-code")
)


@pytest.mark.parametrize("identity", UNINTENDED_CLAUDE_IDENTITIES)
def test_a_turn_routed_to_a_paid_claude_identity_fails_naming_the_provider_it_reached(
    tmp_path: Path, oneharness_bin: str, identity: str
) -> None:
    """Reaching an unintended provider is a loud refusal, not a quiet charge.

    A real chain can select a real identity, and on a provisioned host a billed turn
    reads exactly like a free one from a journey's assertions. Each identity is driven
    on its own because `fallback` stops at the first candidate that runs, so one launch
    would prove only the first of the three.
    """
    environment = _environment(tmp_path, oneharness_bin)
    environment["ONEHARNESS_HARNESSES"] = identity

    assert shutil.which("claude", path=environment["PATH"]) == str(
        PAID_PROVIDER_GUARD / "claude"
    ), "a launch environment here resolves `claude` to something other than the guard"

    ran = subprocess.run(
        [
            oneharness_bin,
            "run",
            "--config",
            str(REPO_ROOT / "oneharness.check-in.toml"),
            "--prompt",
            "a turn no journey here means to spend",
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert ran.returncode != 0, f"{identity} answered the turn:\n{ran.stdout}"
    # oneharness owns this report's schema; only the three fields the claim rests on
    # are read out of it.
    attempted = json.loads(ran.stdout)["results"][-1]
    assert attempted["harness_id"] == identity, attempted
    assert attempted["available"] is True, (
        f"{identity} was skipped rather than routed to, so this proves nothing about "
        f"what a journey reaching it would spend: {attempted}"
    )
    assert REFUSAL in attempted["stderr"], (
        f"{identity} resolved to something other than the guard, so a journey that "
        f"reached it would have spent a paid subscription: {attempted}"
    )


def test_the_paid_provider_guard_answers_an_abbreviated_version_probe() -> None:
    """`-v` is answered as a probe, not refused as a turn.

    `oneharness` decides a candidate is installed by probing the binary it resolved,
    and a `claude` that fails that probe is classified as not installed and skipped —
    which leaves the journey above passing while proving nothing about routing. The
    guard accepts the abbreviated spelling for exactly that reason, and nothing else
    reaches that branch, so it is driven here through the `claude` that `PATH`
    resolves to: that symlink is the whole seam this stand-in reaches a run through.
    """
    resolved = shutil.which("claude", path=str(PAID_PROVIDER_GUARD))
    assert resolved == str(PAID_PROVIDER_GUARD / "claude"), (
        f"the guard directory resolves `claude` to {resolved}, so this would probe "
        f"something other than the stand-in"
    )

    probed = subprocess.run(
        [resolved, "-v"],
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
        check=False,
    )

    assert probed.returncode == 0, (
        f"the guard failed a `-v` probe, so a chain probing it that way reads the "
        f"identity as not installed and skips it:\n{probed.stdout}\n{probed.stderr}"
    )
    # Against the constant rather than a literal: what this holds is that the probe
    # branch answered, not which version the stand-in claims.
    assert probed.stdout == f"{VERSION}\n", (
        f"a `-v` probe answered with something other than the version the stand-in "
        f"claims: {probed.stdout!r}"
    )
    assert REFUSAL not in probed.stderr, (
        f"a `-v` probe was read as a turn and refused, which is the failure accepting "
        f"that spelling exists to prevent: {probed.stderr!r}"
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
        # The two ops the filter serves are `supervisor` and `judge`; an `assess` — the
        # op a top-level `assessment` produces — is refused by name, and
        # `tests/test_observer_judge_ops.py` is what keeps any channel-served persona
        # from declaring the key that would ask it.
        "an assess call rather than one of the two served ops": (
            json.dumps({**SUPERVISOR_FRAME, "op": "assess"}),
            "reach the planner channel, got 'assess'",
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


#: The kind `scripts/channel-serve.py` raises a turn its agent side lost under.
SURFACE_KIND_OF_A_LOST_TURN = "monitor-failed"

#: And the kind it raises a machine transcript no failure can be proven inside under —
#: the shape all 26 of this host's oversized surfaces were.
SURFACE_KIND_OF_A_TRANSCRIPT = "monitor-transcript"

#: The ceiling the surface's composition may not exceed, for any transcript, identity,
#: and run id these journeys drive through it. The raw transcript it replaces was 21,531
#: characters.
NAMED_FAILURE_LIMIT = 400

#: How the recorded transcript's harness classified the refusal it ended on.
LOST_TURN_CAUSE = "usageLimitExceeded"

#: Text that occurs only inside the transcript, asserted absent from the surface: a
#: frame name, a key, and the URL out of the refusal's own prose.
ONLY_IN_THE_TRANSCRIPT = ("turn/completed", "codexErrorInfo", "chatgpt.com")

#: The prompt the harness echoes back at itself, which is most of a lost turn's weight.
ECHOED_PROMPT = (
    "Actively monitor one executing tracked graph and report what drifts from it.\n" * 100
)


def _lost_turn_transcript(codex_home: str) -> str:
    """What a monitor turn its agent side lost leaves as the last thing it "said".

    The shape measured off this host's own `runs/rc-fixes-brief` channel queue: the
    harness's own JSON-RPC stream, one frame per line, opening with the home the
    identity it ran as is credentialed from, echoing the whole prompt back, and ending
    in an error frame and a `turn/completed` whose status is `failed`. Twenty of these
    queued unread on one run, each one 21,531 characters that had to be opened to find
    out it said nothing.

    Thread and session identifiers are this fixture's own and the echoed prompt is the
    monitor's role rather than the recorded run's; the frames, their order, and the two
    that carry the refusal are as recorded.
    """
    refusal = (
        "You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage "
        "to purchase more credits or try again at Aug 20th, 2026 3:30 AM."
    )
    thread = "01a01a5d-8df8-77b0-aace-730d932eefe4"
    turn = "01a01a5d-8f08-7642-947f-d6103de39e42"
    echoed = {
        "type": "userMessage",
        "id": "01a01a5d-9351-77a1-b947-d7aa89759a9c",
        "content": [{"type": "text", "text": ECHOED_PROMPT}],
    }
    error = {"message": refusal, "codexErrorInfo": LOST_TURN_CAUSE, "additionalDetails": None}
    frames: list[dict[str, object]] = [
        {
            "id": 1,
            "result": {
                "userAgent": "oneharness/0.145.0 (Ubuntu 24.4.0; x86_64)",
                "codexHome": codex_home,
                "platformFamily": "unix",
                "platformOs": "linux",
            },
        },
        {"method": "thread/started", "params": {"thread": {"id": thread}}},
        {
            "method": "turn/started",
            "params": {"threadId": thread, "turn": {"id": turn, "status": "inProgress"}},
        },
        {"method": "item/started", "params": {"item": echoed}},
        {"method": "item/completed", "params": {"item": echoed}},
        {
            "method": "account/rateLimits/updated",
            "params": {"rateLimits": {"credits": {"hasCredits": False, "balance": "0"}}},
        },
        {
            "method": "thread/status/changed",
            "params": {"threadId": thread, "status": {"type": "systemError"}},
        },
        {"method": "error", "params": {"error": error, "willRetry": False, "threadId": thread}},
        {
            "method": "turn/completed",
            "params": {
                "threadId": thread,
                "turn": {"id": turn, "items": [], "status": "failed", "error": error},
            },
        },
    ]
    return _transcript(*frames)


def _transcript(*frames: dict[str, object]) -> str:
    """One machine transcript, in the form a harness streams it: a JSON object per line."""
    return "\n".join(json.dumps(frame) for frame in frames)


def _capturing_channel(tmp_path: Path, environment: dict[str, str]) -> Path:
    """A stand-in `channel serve` that keeps the surface it was handed, and answers.

    What these journeys are about is the surface the filter *sends*, and the published
    `channel serve` neither hands one back nor answers one without a live run whose
    planner is reading it. Everything else is real: the filter, its argv, the frame on
    its stdin, and the response it validates off this stdout.
    """
    channel = tmp_path / "stand-in-channel"
    captured = tmp_path / "surface.json"
    # llmlint: ignore[e2e_not_mocked] A reader is what makes the sent surface readable.
    channel.write_text(
        f'#!/usr/bin/env bash\ncat > {captured}\necho \'{{"completion": false, '
        '"message": "keep watching", "reason": "mid-run"}\'\n',
        encoding="utf-8",
    )
    channel.chmod(0o755)
    # llmlint: ignore[e2e_not_mocked] A reader is what makes the sent surface readable.
    environment[ONEPIPELINE_BIN] = str(channel)
    return captured


def _frame_ending_in(said: str) -> str:
    """The recorded supervisor frame, with the monitor's conversation ending in `said`."""
    messages = [*cast(list[dict[str, str]], SUPERVISOR_FRAME["messages"])[:-1]]
    return json.dumps(
        {**SUPERVISOR_FRAME, "messages": [*messages, {"role": "assistant", "content": said}]}
    )


def test_the_channel_filter_names_a_lost_monitor_turn_instead_of_transcribing_it(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A turn the agent side lost reaches the planner as a named failure, not a dump.

    The planner may not filter the unread-surface line, because a blocking surface
    produces no other signal until it is read. So a monitor turn that failed rather than
    spoke used to arrive as twenty-one thousand characters of the harness's own JSON-RPC
    stream — unreadable and undroppable at once, and each one had to be opened to find
    out it said nothing. What has to arrive instead is the two things a planner acts on:
    what the failure was, and which identity's quota it happened on.
    """
    environment = _environment(tmp_path, oneharness_bin)
    captured = _capturing_channel(tmp_path, environment)
    transcript = _lost_turn_transcript("/home/nick/.codex")

    relayed = _serve(_frame_ending_in(transcript), environment)

    assert relayed.returncode == 0, relayed.stderr
    surface = json.loads(captured.read_text(encoding="utf-8"))
    assert surface["kind"] == SURFACE_KIND_OF_A_LOST_TURN, surface
    assert surface["blocking"] is False, surface
    named = surface["message"]
    assert f"monitor turn failed: {LOST_TURN_CAUSE} on codex." in named, named
    assert len(named) <= NAMED_FAILURE_LIMIT, f"{len(named)} characters is not a line: {named}"
    for buried in ONLY_IN_THE_TRANSCRIPT:
        assert buried not in named, f"the raw transcript reached the surface message: {named}"
    assert ECHOED_PROMPT.splitlines()[0] not in named, named


def test_the_channel_filter_names_which_codex_identity_lost_the_turn(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The identity is read out of the transcript, so the alternate is named as itself.

    The actionable half of the line is which quota to go and look at, and this host's
    two codex identities have separate ones. The transcript names the home it was
    credentialed from; `ORCHESTRATOR_CODEX_ALT_HOME` is what says which identity that
    is, and this journey establishes it exactly as a launch does. Reporting `codex` for
    a turn the alternate lost would send a planner to a quota that is fine.
    """
    environment = _environment(tmp_path, oneharness_bin)
    captured = _capturing_channel(tmp_path, environment)
    alternate = environment["ORCHESTRATOR_CODEX_ALT_HOME"]

    relayed = _serve(_frame_ending_in(_lost_turn_transcript(alternate)), environment)

    assert relayed.returncode == 0, relayed.stderr
    named = json.loads(captured.read_text(encoding="utf-8"))["message"]
    assert f"monitor turn failed: {LOST_TURN_CAUSE} on codex:alternate." in named, named
    assert len(named) <= NAMED_FAILURE_LIMIT, f"{len(named)} characters is not a line: {named}"


def test_the_channel_filter_names_a_lost_turn_however_its_harness_recorded_it(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """Every shape a lost turn is recorded in still reaches the planner as one line.

    The recorded transcript is one harness classifying one refusal, and the branches
    around it are what the next one will land on: an error frame with no terminal turn
    after it, a turn that failed recording nothing, an unclassified error whose only
    account of itself is prose, and a stream that never says which harness ran it. Each
    still has to name what it can and stay a line — including the two that would not on
    their own, a cause longer than the whole surface and a cause carrying newlines,
    which is text the harness controls and this filter renders.
    """
    environment = _environment(tmp_path, oneharness_bin)
    captured = _capturing_channel(tmp_path, environment)
    opening = {"result": {"codexHome": "/home/nick/.codex"}}
    recorded = {
        "an error frame with no terminal turn behind it": (
            _transcript(opening, {"method": "error", "params": {"error": {"code": "quotaLost"}}}),
            "monitor turn failed: quotaLost on codex.",
        ),
        "a turn that failed recording nothing about why": (
            _transcript(
                opening,
                {"method": "turn/completed", "params": {"turn": {"status": "failed"}}},
            ),
            "monitor turn failed: no cause recorded on codex.",
        ),
        "an error whose only account of itself is prose": (
            _transcript(
                opening,
                {"method": "error", "params": {"error": {"message": "the provider hung up"}}},
            ),
            "monitor turn failed: the provider hung up on codex.",
        ),
        "a stream that never says which harness ran it": (
            _transcript({"method": "error", "params": {"error": {"code": "quotaLost"}}}),
            "monitor turn failed: quotaLost on an unidentified harness.",
        ),
        "a cause carrying the newlines that would make the line two": (
            _transcript(
                opening,
                {"method": "error", "params": {"error": {"message": "hung up.\n\nRetry at 3AM."}}},
            ),
            "monitor turn failed: hung up. Retry at 3AM. on codex.",
        ),
    }
    for case, (said, expected) in recorded.items():
        relayed = _serve(_frame_ending_in(said), environment)

        assert relayed.returncode == 0, f"{case}: {relayed.stderr}"
        surface = json.loads(captured.read_text(encoding="utf-8"))
        assert surface["kind"] == SURFACE_KIND_OF_A_LOST_TURN, f"{case}: {surface}"
        assert expected in surface["message"], f"{case}: {surface['message']}"
        assert len(surface["message"]) <= NAMED_FAILURE_LIMIT, f"{case}: {surface['message']}"


def test_the_channel_filter_bounds_a_cause_its_harness_did_not_bound(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A refusal that is a paragraph still lands as a line a planner reads where it is.

    The cause is the one part of the line copied out of somebody else's transcript, and
    nothing on the far side of that boundary keeps it short: the recorded refusal is
    already two sentences and a URL. So the bound is asserted against a cause far longer
    than the whole surface may be, and the identity and the remedy — the two halves a
    reader acts on — have to survive it.
    """
    environment = _environment(tmp_path, oneharness_bin)
    captured = _capturing_channel(tmp_path, environment)
    unbounded = "the provider refused this turn and explained itself at length. " * 20
    said = _transcript(
        {"result": {"codexHome": "/home/nick/.codex"}},
        {"method": "error", "params": {"error": {"message": unbounded}}},
    )

    relayed = _serve(_frame_ending_in(said), environment)

    assert relayed.returncode == 0, relayed.stderr
    named = json.loads(captured.read_text(encoding="utf-8"))["message"]
    assert len(named) <= NAMED_FAILURE_LIMIT, f"{len(named)} characters is not a line: {named}"
    assert named.startswith("monitor turn failed: the provider refused this turn"), named
    assert "on codex." in named, named
    assert "`just monitor serve-e2e --filter monitor`" in named, named


def test_the_channel_filter_leaves_an_observation_written_as_prose_alone(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """What the monitor said in prose is raised verbatim, exactly as before.

    The complement of the journeys above, and the thing bounding a transcript must not
    cost: prose is the planner's question in the monitor's own words, so it reaches them
    whole. The two cases are the ones a careless classifier swallows — an observation
    that quotes the very frame the classifier keys on, and one whose closing line is a
    JSON value that is not an object. Both are prose by the only test that draws the
    line without guessing at a vocabulary: a prose line does not parse as a JSON object.
    """
    environment = _environment(tmp_path, oneharness_bin)
    # `onepipeline channel serve` is a published CLI this repository delegates to, doubled
    # at that boundary and nowhere above it: what is under test here is the surface the
    # real filter *sends*, and the published verb neither hands one back nor answers it
    # without a live planner. `tests/e2e/test_lost_turn_wire_contract_e2e.py` crosses the
    # real verb, on a real transcript.
    # llmlint: ignore[e2e_not_mocked] The published channel is doubled at its own boundary.
    captured = _capturing_channel(tmp_path, environment)
    left_alone = {
        "an observation that quotes a failed turn": (
            "node api's dispatch died to its provider. Its last frame was "
            '{"method": "turn/completed", "params": {"turn": {"status": "failed"}}} — retry it?'
        ),
        "a transcript one of whose lines is not an object": (
            _transcript({"method": "error", "params": {"error": {"code": "quotaLost"}}})
            + '\n"node api has drifted"'
        ),
    }
    for case, said in left_alone.items():
        relayed = _serve(_frame_ending_in(said), environment)

        assert relayed.returncode == 0, f"{case}: {relayed.stderr}"
        raised = captured.read_text(encoding="utf-8")
        assert json.loads(raised) == {
            "kind": SURFACE_KIND_OF_A_MONITOR,
            "message": said,
            "blocking": False,
        }, f"{case}: {raised}"


def test_the_channel_filter_bounds_a_transcript_it_cannot_prove_was_lost(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A machine transcript carrying no provable failure is bounded too, not republished.

    This is the shape that actually filled this channel. All 26 oversized surfaces
    measured on this host were `status: completed` with `error: null`, so nothing in any
    of them could be *proven* lost and all 26 were raised as the monitor's own words —
    176.1 MB of protocol carrying zero model-authored characters. The three branches
    below are the ones a real transcript lands on: a turn that finished, a stream with no
    terminal frame at all, and one whose failure-shaped frame carries a status the filter
    does not read as lost. Each has to arrive under its own kind as a line, naming the
    identity, the size, and the command that reads the full text.
    """
    environment = _environment(tmp_path, oneharness_bin)
    # `onepipeline channel serve` is a published CLI this repository delegates to, doubled
    # at that boundary and nowhere above it: what is under test here is the surface the
    # real filter *sends*, and the published verb neither hands one back nor answers it
    # without a live planner. `tests/e2e/test_lost_turn_wire_contract_e2e.py` crosses the
    # real verb, on a real transcript.
    # llmlint: ignore[e2e_not_mocked] The published channel is doubled at its own boundary.
    captured = _capturing_channel(tmp_path, environment)
    opening = {"result": {"codexHome": "/home/nick/.codex"}}
    unproven = {
        "a transcript of a turn that finished": _transcript(
            opening,
            {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
        ),
        "a stream that stops before any terminal frame": _transcript(
            opening,
            {"method": "thread/started", "params": {"thread": {"id": "01a01a5d"}}},
        ),
        "a turn whose status is not one the filter reads as lost": _transcript(
            opening,
            {"method": "turn/completed", "params": {"turn": {"status": "cancelled"}}},
        ),
    }
    for case, said in unproven.items():
        relayed = _serve(_frame_ending_in(said), environment)

        assert relayed.returncode == 0, f"{case}: {relayed.stderr}"
        surface = json.loads(captured.read_text(encoding="utf-8"))
        assert surface["kind"] == SURFACE_KIND_OF_A_TRANSCRIPT, f"{case}: {surface}"
        assert surface["blocking"] is False, f"{case}: {surface}"
        named = surface["message"]
        assert len(named) <= NAMED_FAILURE_LIMIT, f"{case}: {len(named)} is not a line: {named}"
        assert f"{len(said)} characters from codex" in named, f"{case}: {named}"
        assert "`just monitor serve-e2e --filter monitor`" in named, f"{case}: {named}"
        assert said not in named, f"the transcript reached the surface message: {named}"
        for buried in ("turn/completed", "codexHome"):
            assert buried not in named, f"{case}: the transcript leaked into {named}"


def test_the_channel_filter_names_which_identity_a_bounded_transcript_came_from(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The alternate codex identity is named as itself here too, not as `codex`.

    A bounded transcript withholds text a planner may want, so the line has to say whose
    output it is withholding: this host's two codex identities hold separate quotas and
    keep separate transcripts, and `ORCHESTRATOR_CODEX_ALT_HOME` is what tells them
    apart. A transcript naming no home at all says so rather than guessing at one.
    """
    environment = _environment(tmp_path, oneharness_bin)
    # `onepipeline channel serve` is a published CLI this repository delegates to, doubled
    # at that boundary and nowhere above it: what is under test here is the surface the
    # real filter *sends*, and the published verb neither hands one back nor answers it
    # without a live planner. `tests/e2e/test_lost_turn_wire_contract_e2e.py` crosses the
    # real verb, on a real transcript.
    # llmlint: ignore[e2e_not_mocked] The published channel is doubled at its own boundary.
    captured = _capturing_channel(tmp_path, environment)
    finished = {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}
    named_by = {
        "codex:alternate": _transcript(
            {"result": {"codexHome": environment["ORCHESTRATOR_CODEX_ALT_HOME"]}}, finished
        ),
        "an unidentified harness": _transcript(finished),
    }
    for identity, said in named_by.items():
        relayed = _serve(_frame_ending_in(said), environment)

        assert relayed.returncode == 0, f"{identity}: {relayed.stderr}"
        surface = json.loads(captured.read_text(encoding="utf-8"))
        assert surface["kind"] == SURFACE_KIND_OF_A_TRANSCRIPT, f"{identity}: {surface}"
        assert f"{len(said)} characters from {identity}," in surface["message"], surface["message"]


def test_a_bounded_transcript_still_never_answers_for_the_planner(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """Bounding the transcript changes nothing about who rules on the run.

    The same guarantee `test_a_named_failure_still_never_answers_for_the_planner` makes
    of the failure line, made of the surface that now stands where a republished
    transcript used to: onejudge reads this stdout as the planner's ruling, so a
    fabricated `{"completion": ...}` on a path whose channel cannot be reached would
    continue or settle a run nobody ruled on.
    """
    environment = _environment(tmp_path, oneharness_bin)
    frame = _frame_ending_in(
        _transcript(
            {"result": {"codexHome": "/home/nick/.codex"}},
            {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
        )
    )
    environment[ONEPIPELINE_BIN] = str(tmp_path / "no-such-onepipeline")

    refused = _serve(frame, environment)

    assert refused.returncode != 0, f"an unreachable channel was answered: {refused.stdout}"
    assert "could not run" in refused.stderr, refused.stderr
    assert "just bootstrap" in refused.stderr, refused.stderr
    assert "Traceback" not in refused.stderr, refused.stderr
    assert "completion" not in refused.stdout, refused.stdout


def test_a_named_failure_still_never_answers_for_the_planner(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """Naming the failure changes nothing about who rules on the run.

    The filter now composes a message of its own, which is exactly the point at which
    answering the planner's question for them would become easy — and onejudge reads
    this stdout as the planner's ruling, so a fabricated `{"completion": ...}` would
    continue or settle a run nobody ruled on. A lost turn whose channel cannot be
    reached at all still exits non-zero with the cause and the remedy.
    """
    environment = _environment(tmp_path, oneharness_bin)
    frame = _frame_ending_in(_lost_turn_transcript("/home/nick/.codex"))

    for case, override in (
        ("a channel that is not installed", str(tmp_path / "no-such-onepipeline")),
        ("a channel that cannot be executed", str(_unrunnable(tmp_path))),
    ):
        environment[ONEPIPELINE_BIN] = override

        refused = _serve(frame, environment)

        assert refused.returncode != 0, f"{case} was answered: {refused.stdout}"
        assert "could not run" in refused.stderr, f"{case}: {refused.stderr}"
        assert "just bootstrap" in refused.stderr, f"{case}: {refused.stderr}"
        assert "Traceback" not in refused.stderr, f"{case}: {refused.stderr}"
        assert "completion" not in refused.stdout, f"{case}: {refused.stdout}"


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
    launch = _just("orchestrate", project_from_plan(plan), environment=environment)
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


def _plans_in_the_repository() -> list[str]:
    """Every committed example project, as its qualified launch id."""
    return [
        f"examples:{record.stem}"
        for record in sorted((REPO_ROOT / "examples" / "projects").glob("*.md"))
    ]


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
    for project in plans:
        refused = _just(
            "orchestrate",
            project,
            "--detach",
            "--dag-graph",
            absent_graph,
            environment=environment,
            seconds=120,
        )
        reported = refused.stderr + refused.stdout
        # Three markers, one per boundary a loaded plan can next reach on this host:
        # the absent agent graph, a stale session holder, and the repository preflight
        # refusing to work an identity another session holds. That last one is not a
        # weaker signal than the others — it is preflight, which runs only on a plan the
        # launcher has already accepted — and it is the one a lifecycle plan naming this
        # repository reaches whenever a run of it is live, which is most of the time.
        reached_downstream_boundary = any(
            marker in reported
            for marker in ("dag-scope.yaml", "session holders", "concurrent project work refused")
        )
        assert reached_downstream_boundary, f"{project} was not accepted as a plan:\n{reported}"


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
    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "name": "merge-policy-probe",
        "tasks": [
            {
                "id": "service",
                "title": "feat: add service",
                "task": "## What\nAdd the service.\n\n## Why\nIt is required.\n\n"
                "## Acceptance criteria\n- The service works.\n",
                "repo": "ai-orchestrator-isolated",
            }
        ],
    }

    def launch(policy: str) -> str:
        plan["tasks"][0]["merge_policy"] = policy
        written = tmp_path / "candidate.plan.json"
        written.write_text(json.dumps(plan), encoding="utf-8")
        refused = _just(
            "orchestrate",
            project_from_plan(written),
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
        ["just", "orchestrate", project_from_plan(plan), "--dag-graph", "off"],
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
        [
            "just",
            "orchestrate",
            project_from_plan(plan),
            "--heartbeat-interval",
            str(PACEMAKER_INTERVAL_SECONDS),
        ],
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


#: How long the cancelled dispatch below has to stop itself before the engine reaps it.
#: The shipped grace is sized for a real turn to commit what it has, and so is far too
#: long to wait out here — this run says its own instead, which is the published way to
#: say it and the only one: `ONEPIPELINE_CANCEL_GRACE_SECONDS`. The default's value is
#: the engine's to state; naming it here would be a second copy nothing keeps in step.
CANCEL_GRACE_ENV = "ONEPIPELINE_CANCEL_GRACE_SECONDS"
CANCEL_GRACE_SECONDS = 5

#: The grace the *graceful* journey runs under, and the one of this pair that may be
#: scaled: there the deadline is only the premise, so it has to be long enough for a
#: real process teardown to finish under this tier's own load, which the unscaled five
#: seconds have not been. The escalation journey keeps them, because there the deadline
#: expiring *is* the behaviour under test.
STOPPING_GRACE_SECONDS = round(e2e_timeout(CANCEL_GRACE_SECONDS))

#: How long the worker below is held for. Comfortably past the grace above, because the
#: stand-in answers on a timer and takes no redirection: it is the dispatch that does
#: *not* stop when asked, which is exactly the arm the deadline exists for.
CANCELLED_WORKER_HELD_SECONDS = 90

#: How long the worker in the graceful journey is held for: inside the grace period, so
#: the dispatch is gone before the deadline the escalation journey waits out.
STOPPING_WORKER_HELD_SECONDS = 2

#: The two surfaces a cancellation raises, in the order it raises them.
INTERRUPTED = "dispatch-interrupted"
KILLED = "dispatch-killed"


def _cancellable(
    tmp_path: Path, oneharness_bin: str, run: RunId, held_seconds: int, grace_seconds: int
) -> Iterator[LiveRun]:
    """A run whose only node is held open, under a grace period short enough to wait out."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    environment = _environment(tmp_path, oneharness_bin)
    environment[AGENT_DELAY_ENV] = str(held_seconds)
    environment[CANCEL_GRACE_ENV] = str(grace_seconds)
    plan = tmp_path / f"{run}.plan.json"
    cancellable: CandidatePlan = {"schema_version": 2, "name": run, "tasks": [_node(id="held")]}
    plan.write_text(json.dumps(cancellable), encoding="utf-8")
    launch = subprocess.Popen(  # noqa: S603 - the real recipe, as an operator runs it
        ["just", "orchestrate", project_from_plan(plan), "--dag-graph", "off"],
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


@pytest.fixture
def cancellable_run(tmp_path: Path, oneharness_bin: str) -> Iterator[LiveRun]:
    """A run whose only node is held far past a short cancellation grace period."""
    yield from _cancellable(
        tmp_path,
        oneharness_bin,
        RunId("cancel-escalation-e2e"),
        CANCELLED_WORKER_HELD_SECONDS,
        CANCEL_GRACE_SECONDS,
    )


@pytest.fixture
def stopping_run(tmp_path: Path, oneharness_bin: str) -> Iterator[LiveRun]:
    """A run whose only node ends well inside a short cancellation grace period."""
    yield from _cancellable(
        tmp_path,
        oneharness_bin,
        RunId("cancel-graceful-e2e"),
        STOPPING_WORKER_HELD_SECONDS,
        STOPPING_GRACE_SECONDS,
    )


def _replied(live: LiveRun, command: EditCommand) -> subprocess.CompletedProcess[str]:
    """Send one live edit through the recipe an operator types."""
    envelope: ReplyEnvelope = {"version": 1, "commands": [command]}
    return subprocess.run(
        ["just", "channel-reply", live.run],
        cwd=REPO_ROOT,
        env=live.environment,
        input=json.dumps(envelope),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )


def _awaited(live: LiveRun, phrase: str, *, seconds: float) -> str:
    """Wait for one phrase to appear on the run's own stream, and hand back the stream."""
    limit = deadline(seconds)
    while True:
        stream = _just("monitor", live.run, "--all", environment=live.environment, seconds=60)
        if stream.returncode == 0 and phrase in stream.stdout:
            return stream.stdout
        if time.monotonic() >= limit:
            pytest.fail(f"{phrase!r} never reached the stream of {live.run}:\n{stream.stdout}")
        time.sleep(0.2)


# One run name per fixture, and each fixture stops its run by that name on the way out,
# so two of these on separate workers would stop each other's run. The constraint is the
# scheduling, never a longer deadline.
@pytest.mark.xdist_group("cancellation")
def test_a_cancel_reaches_the_dispatch_and_kills_what_outlives_the_grace_period(
    cancellable_run: LiveRun,
) -> None:
    """A planner `cancel` stops the dispatch, in two steps a supervisor can tell apart.

    This is the journey the prose is written from, and the incident behind it is the
    reason it exists: `cancel` used to raise a signal no agent process read, so a
    supervisor who cancelled a runaway watched it go on committing — 78 commits through
    two freezes — while every view said the node was parked. The claim is therefore not
    that the edit is accepted, which the live-edit journey above already holds, but that
    something happens to the process afterwards.

    Both steps, because the follow-up differs: a turn that took the redirection ended on
    its own terms and committed what it had, and one the deadline reaped stopped
    wherever it was. The stand-in worker answers on a timer and takes no redirection, so
    it is the dispatch that does not stop when asked — which is what makes the second
    step observable here at all.
    """
    _dispatched(cancellable_run)
    cancelled = _replied(cancellable_run, {"op": "cancel", "id": "held"})
    assert cancelled.returncode == 0, cancelled.stderr + cancelled.stdout

    _awaited(cancellable_run, INTERRUPTED, seconds=120)
    # And then the deadline, which is the half a supervisor has to know about: the
    # dispatch did not exit when it was asked, so it was torn down.
    _awaited(cancellable_run, KILLED, seconds=CANCEL_GRACE_SECONDS + 120)


@pytest.mark.xdist_group("cancellation")
def test_a_requeue_is_refused_while_the_cancelled_dispatch_is_still_in_flight(
    cancellable_run: LiveRun,
) -> None:
    """Parked is not stopped, so the requeue that follows a cancel too fast is refused.

    The failure this prevents is silent rather than loud: a `cancel` parks the node
    immediately while its dispatch runs on holding the workspace, so a requeue accepted
    there returns the node to a frontier where it waits on an occupancy lease its own
    predecessor holds, with nothing said about why. A supervisor spent forty minutes
    looking for a wedge that was not there. So the refusal is the behaviour, and it has
    to name what is being waited for rather than only saying no.
    """
    _dispatched(cancellable_run)
    cancelled = _replied(cancellable_run, {"op": "cancel", "id": "held"})
    assert cancelled.returncode == 0, cancelled.stderr + cancelled.stdout

    refused = _replied(cancellable_run, {"op": "requeue", "id": "held"})
    reported = refused.stderr + refused.stdout
    assert refused.returncode != 0, reported
    assert "still has a dispatch in flight" in reported, reported
    # Named, not merely refused: a supervisor told only "it is still running" has
    # nothing to look at while it waits.
    assert "running for" in reported, reported


@pytest.mark.xdist_group("cancellation")
def test_a_dispatch_that_ends_inside_the_grace_period_is_never_killed(
    stopping_run: LiveRun,
) -> None:
    """The other arm of a cancel, and the one a planner should usually see.

    The escalation journey holds a dispatch that will not stop; this holds the ordinary
    case, and the two together are what make the pair of surfaces worth telling apart at
    all. What is asserted is the *absence* of the kill: a supervisor reading
    `dispatch-killed` is being told that whatever the turn had not committed is gone, so
    an engine that raised it for every cancel would send them looking for lost work after
    a dispatch that lost none.

    The node is left to settle before the assertion rather than sampled the moment the
    interrupt lands, because "was not killed" is only true once there is no longer
    anything to kill.
    """
    _dispatched(stopping_run)
    cancelled = _replied(stopping_run, {"op": "cancel", "id": "held"})
    assert cancelled.returncode == 0, cancelled.stderr + cancelled.stdout

    _awaited(stopping_run, INTERRUPTED, seconds=120)
    # Past this run's own deadline, so a kill it was going to raise has had every chance
    # to arrive. It is `STOPPING_GRACE_SECONDS` the kill would be timed from, not the
    # unscaled constant, so a wait measured against that one could end before the engine
    # had reached the decision this asserts the outcome of.
    time.sleep(STOPPING_GRACE_SECONDS + CANCEL_GRACE_SECONDS * 2)
    stream = _just("monitor", stopping_run.run, "--all", environment=stopping_run.environment)
    assert stream.returncode == 0, stream.stderr
    assert KILLED not in stream.stdout, (
        f"a dispatch that ended inside the grace period was reported killed:\n{stream.stdout}"
    )
