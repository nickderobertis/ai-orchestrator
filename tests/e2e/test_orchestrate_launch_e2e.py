"""A shipped plan launches through `just orchestrate`, settles, and answers the planner.

This repository's whole function is dispatching work, and the only thing that
proves it can is a plan that actually runs. `tests/e2e/test_delegated_recipes_e2e.py`
holds each recipe to the command line it renders; that is a different promise, and
a delegation that renders perfectly can still reach a `onepipeline` that refuses —
which is exactly how a release shipped with no `graphs/dag-scope.yaml` at all and
every plan declaring a schema version the published crate rejects.

So this journey launches one of the shipped examples for real, through the real
recipe, and then asks the run the questions a planner asks it. Everything between
the recipe and the model is real: `onepipeline`'s driver, its engine verbs, its
channel, the `graphs/` agent-graph configs, `oneagentgraph`, and the onejudge
conversation those compose. `tests/e2e/fake_backend.py` stands in for the paid
model alone, at the `oneharness` seam where a dispatch reaches it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NamedTuple, TypedDict, cast

import pytest
from fake_backend import JUDGE_CONFIG_NAME, PROMPT_LOG_ENV, RUN_TASK
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The stand-in for the paid model, named to `oneagentgraph` as its harness.
FAKE_BACKEND = Path(__file__).resolve().parent / "fake_backend.py"

#: The shipped example this journey launches. A plan written for this repository's
#: own operators, so a schema or field the published crate stopped accepting fails
#: here rather than the first time a planner types it.
SHIPPED_PLAN = "examples/single-node-direct.plan.json"

#: The run id `onepipeline` mints from that plan's `name`.
SHIPPED_RUN = "scheduler-research"

#: The dag-scope agent graph every run launches, and how `just monitor` labels the
#: envelope stream it produces. `oneagentgraph` suffixes the run's own id, so this
#: is a prefix and the node-scope graph's members do not answer to it.
DAG_SCOPE_GRAPH = "graphs/dag-scope.yaml"
DAG_SCOPE_STREAM = "agent:dag-scope-"

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

#: The pacemaker member, whose turn must stay out of the run's rounds.
PACEMAKER_MEMBER = "check-in"

#: The round verbs that change a run's state. The pacemaker's own `task` forbids them
#: by name, and a round claimed from its turn is what killed every dispatched worker
#: before that task existed.
ROUND_VERBS = ("onepipeline round run", "onepipeline round next")

#: The orchestrator's role, as `personas/orchestrator.yaml` states it. Since
#: onepipeline 0.2.0 the composed task says only what the run is, so this is the only
#: thing that says it is to be driven.
DRIVE_ROLE = "Drive `onepipeline round run <run-id>`"

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
    # The attached launch streams what `just monitor` streams — on standard error,
    # keeping the settlement record on standard output the only thing a caller has
    # to parse. That it dispatched the node the plan declares is what the live
    # stream answers for.
    assert "node-dispatched" in launch.stderr
    # Whether the node then *settled* is read from the replayed stream instead. A
    # node settling and its round finishing are written in the same millisecond,
    # and an attached launch returns on the second — so the first has no obligation
    # to have reached standard error yet, and under suite load it has been observed
    # missing from a launch that settled `complete`. `just monitor` replays a
    # settled run's whole stream, so what it reports is the run rather than a race.
    # Without this the assertion is the one it is here to make: that "complete" is
    # not describing an empty run.
    stream = _just("monitor", SHIPPED_RUN, environment=launched.environment, seconds=60)
    assert stream.returncode == 0, stream.stderr
    assert "node-settled done" in stream.stdout, stream.stdout


@pytest.mark.xdist_group("orchestrate-launch")
def test_node_overrides_and_named_or_omitted_persona_paths_work(
    routed_persona_run: RoutedPersonaRun,
) -> None:
    """The launch forwards each side config and only the persona a node names."""
    launch = routed_persona_run.launch
    assert launch.returncode == 0, launch.stdout + launch.stderr
    stream = _just(
        "monitor", "routed-persona-e2e", environment=routed_persona_run.environment, seconds=60
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
    """
    stream = _just("monitor", SHIPPED_RUN, environment=launched.environment, seconds=60)
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
def test_the_pacemaker_is_given_its_own_task_and_never_the_orchestrators(
    launched: Launched,
) -> None:
    """The pacemaker comes due mid-run and is told to report, not to drive.

    This is the configuration whose absence made every dispatch die. `onepipeline`
    composes ONE task for this graph and `oneagentgraph` hands it to every member that
    does not claim one. When the composed task opened `Drive run <RUN> to settlement`,
    this member took it literally: single-sided, it began its turn before the two-party
    orchestrator began one, won the ownership lock, and the dispatched worker then ran
    *inside* its turn — where `oneharness.check-in.toml`'s finite deadline killed the
    worker with no record, because the process killed was the one that would have
    written it.

    The member's own `task` is the fix, and this is what holds it: the pacemaker's
    effective prompt is the scoped one this repository wrote, the run-level task is not
    in it, and neither is the orchestrator's role. Read from the prompts the run really
    issued rather than from the graph document, because a `task` present in the file and
    not reaching the model is exactly the failure being excluded.
    """
    turns = _recorded_turns(launched.prompt_log)
    pacemaker = _turns_of(turns, PACEMAKER_MEMBER)
    assert pacemaker, (
        f"the {PACEMAKER_MEMBER} member took no turn, so nothing here is proven; it is "
        f"launched with --heartbeat-interval {PACEMAKER_INTERVAL_SECONDS} so that it comes "
        f"due while the run is still going"
    )

    for turn in pacemaker:
        assert turn["prompt"].startswith("Report current progress on the onepipeline run"), (
            f"the pacemaker was not given its own task; it got:\n{turn['prompt']}"
        )
        # The composed task names the run in backticks and states its goal. Its absence
        # is the whole point: this member's prompt is its own, not the run's.
        assert RUN_TASK.search(turn["prompt"]) is None, (
            f"the run-level composed task reached the pacemaker:\n{turn['prompt']}"
        )
        assert DRIVE_ROLE not in turn["prompt"] + turn["system"], (
            f"the orchestrator's role reached the pacemaker:\n{turn['prompt']}"
        )
        # The round verbs reach this member only inside the sentence forbidding them,
        # so it is told what it must not do by the same name it would otherwise have
        # reached for. Compared on collapsed whitespace, because the task is prose in a
        # YAML block and wraps wherever it wraps.
        forbidden = "Never run " + " or ".join(f"`{verb}`" for verb in ROUND_VERBS)
        assert forbidden in " ".join(turn["prompt"].split()), (
            f"the pacemaker's task no longer forbids the round verbs by name:\n{turn['prompt']}"
        )


@pytest.mark.xdist_group("orchestrate-launch")
def test_the_orchestrator_still_drives_the_run_from_its_persona(launched: Launched) -> None:
    """Only the orchestrator is told to drive, and the telling comes from its persona.

    onepipeline 0.2.0 stopped composing the drive instruction into the run's task —
    the task now says what the run is, not who drives it — and the `orchestrator`
    member carries no `task` of its own. So whether anything still drives is a question
    about `personas/orchestrator.yaml` reaching that member's session, and it is
    answered here from the launch rather than from the persona file: the composed task
    it was given carries no drive instruction, its system prompt does, and the run
    reached a complete settlement, which nothing but the round verbs can produce.
    """
    turns = _recorded_turns(launched.prompt_log)
    # The orchestrator's agent side may take several turns of one session; every one of
    # them is that member and no other turn of the run may be.
    driving = [turn for turn in turns if DRIVE_ROLE in turn["system"]]
    assert driving, "nothing in this run was told to drive it, yet it settled"
    for turn in driving:
        # An agent side, by the one property that distinguishes the two: the judge side
        # is the turn pinned to the judge config. This used to read `config is None`,
        # which held only while onejudge left the agent side's config implicit; since
        # onepipeline 0.3.1 an agent side carries `.../oneharness.toml` and that spelling
        # would have failed a run doing exactly the right thing. What it excludes is
        # unchanged — a judge side being told to drive the run it is supervising.
        assert Path(turn["config"] or "").name != JUDGE_CONFIG_NAME, (
            "only an agent side may be told to drive; this turn was pinned to the judge "
            f"config at {turn['config']}"
        )
        named = RUN_TASK.search(turn["prompt"])
        assert named is not None, f"a driving turn named no run:\n{turn['prompt']}"
        assert named.group(1) == SHIPPED_RUN
        assert DRIVE_ROLE not in turn["prompt"], (
            "the composed run task carries a drive instruction again, so every member "
            f"taking one is told to drive:\n{turn['prompt']}"
        )


def _shared_completion_bar() -> str:
    """`user.done_when` from `config/onejudge.base.yaml`, folded as YAML folds it.

    Read with a reader written for this one field rather than with a YAML library: the
    workspace installs none, and adding a parser as a dependency to read four lines of
    a file this repository writes is a worse trade than fifteen lines that state the
    shape they accept. The field is a `>-` folded scalar, so its value is the
    more-indented block that follows, with newlines folded to single spaces — which is
    also how it reaches the judge, and what makes comparing against a prompt valid.
    """
    lines = (REPO_ROOT / "config" / "onejudge.base.yaml").read_text(encoding="utf-8").splitlines()
    opened = next(
        (index for index, line in enumerate(lines) if line.strip() == "done_when: >-"), None
    )
    assert opened is not None, (
        "config/onejudge.base.yaml no longer opens `done_when` as a `>-` folded scalar, "
        "so this reader cannot state what the shared bar is"
    )
    indent = len(lines[opened]) - len(lines[opened].lstrip())
    folded = []
    for line in lines[opened + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        folded.append(line.strip())
    bar = " ".join(" ".join(folded).split())
    assert bar, "config/onejudge.base.yaml states an empty shared completion bar"
    return bar


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
    shared_bar = _shared_completion_bar()
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
def test_no_turn_of_this_run_reached_a_paid_provider(launched: Launched) -> None:
    """Nothing in the launched run did real model work.

    A stand-in turn calls no tools, so a single tool call recorded anywhere in this
    run means the mock did not cover every candidate of a fallback chain and a real
    agent was working — which voids every other assertion here, because they would
    then be about a different run than the one this journey claims to prove.
    """
    stream = _just("monitor", SHIPPED_RUN, environment=launched.environment, seconds=60)
    assert stream.returncode == 0, stream.stderr
    acted = [line for line in stream.stdout.splitlines() if line.endswith("turn-activity")]
    assert not acted, f"{len(acted)} real tool call(s) ran; the first was {acted[0]}"


def test_a_launch_reports_a_missing_dag_scope_graph(tmp_path: Path, oneharness_bin: str) -> None:
    """The launch path names the agent-graph config it could not read.

    The failure this whole journey exists for: `onepipeline start` launches a
    dag-scope agent graph by path, ships only the *path*, and a checkout without
    that file refuses every plan it has. A refusal that names the file is the
    difference between a maintainer writing it and a maintainer guessing.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    environment = _environment(tmp_path, oneharness_bin)
    environment["ONEPIPELINE_DAG_GRAPH"] = str(tmp_path / "absent" / "dag-scope.yaml")

    refused = _just("orchestrate", SHIPPED_PLAN, "--detach", environment=environment, seconds=120)
    assert refused.returncode != 0
    reported = refused.stderr + refused.stdout
    assert "dag-scope.yaml" in reported, reported


class PlanNode(TypedDict, total=False):
    """One agent node of a candidate plan, in the published plan schema's own field names.

    `total=False` because a node states only what its journey needs. `done_when` is here
    although the adopted schema has no such field: writing one is what
    `test_a_plan_carrying_a_node_level_done_when_is_refused` does, so the retired field
    is part of this shape precisely so a test can offer it and be refused.
    """

    id: str
    persona: str
    task: str
    max_turns: int
    done_when: str


class CandidatePlan(TypedDict, total=False):
    """A plan offered to the real launcher, which is the only thing that judges it."""

    schema_version: int
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
    # The plan is loaded and validated before the agent graph is, so a refusal here is
    # the plan's own and no paid work is reachable even if one were somehow accepted.
    environment["ONEPIPELINE_DAG_GRAPH"] = str(tmp_path / "absent" / "dag-scope.yaml")
    written = tmp_path / "candidate.plan.json"
    written.write_text(json.dumps(plan), encoding="utf-8")
    return _just("orchestrate", str(written), "--detach", environment=environment, seconds=120)


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


def test_a_plan_declaring_the_retired_schema_version_is_refused(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """Schema 1 is refused by number, so a plan written before this adoption fails loudly.

    Every plan this repository shipped declared version 1, and the crate that reads them
    now takes only 2. A plan file is the one artifact an operator keeps a copy of, so the
    refusal has to name the version it wants rather than failing somewhere downstream.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    refused = _refused_plan(tmp_path, oneharness_bin, {"schema_version": 1, "tasks": [_node()]})

    assert refused.returncode != 0, refused.stdout
    reported = refused.stderr + refused.stdout
    assert "schema_version" in reported and "2" in reported, reported


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
    for accepted in ("version", "completion", "message", "reason", "commands"):
        assert accepted in reported, (
            f"the refusal must name {accepted!r} as an accepted field so a planner can "
            f"see what the envelope does take:\n{reported}"
        )


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
    environment["ONEPIPELINE_DAG_GRAPH"] = str(tmp_path / "absent" / "dag-scope.yaml")
    plan = tmp_path / "candidate.plan.json"

    for origin, document in plans:
        plan.write_text(document, encoding="utf-8")
        refused = _just("orchestrate", str(plan), "--detach", environment=environment, seconds=120)
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
    environment["ONEPIPELINE_DAG_GRAPH"] = str(tmp_path / "absent" / "dag-scope.yaml")
    plan = json.loads((REPO_ROOT / LIFECYCLE_PLAN).read_text(encoding="utf-8"))

    def launch(policy: str) -> str:
        plan["tasks"][0]["merge_policy"] = policy
        written = tmp_path / "candidate.plan.json"
        written.write_text(json.dumps(plan), encoding="utf-8")
        refused = _just(
            "orchestrate", str(written), "--detach", environment=environment, seconds=120
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
