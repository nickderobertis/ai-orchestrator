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

#: A launching session the journey states rather than inherits. The suite runs
#: inside a dispatch whose own harness session would otherwise decide these
#: assertions, and ownership is the thing under test.
LAUNCHING_SESSION = "e2e-planner-session"
OTHER_SESSION = "another-planner-session"

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
    prompt: str


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
    launch = _just("orchestrate", SHIPPED_PLAN, environment=environment)
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
        yield Launched(environment, launch)
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
    environment["FAKE_BACKEND_PROMPT_LOG"] = str(prompt_log)
    plan = tmp_path / "routed-persona.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "routed-persona-e2e",
                "concurrency": 1,
                "tasks": [
                    {
                        "id": "named",
                        "persona": "docs-writer",
                        "task": "Report without changing files.",
                        "done_when": "the stand-in report is accepted",
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


@pytest.mark.xdist_group("orchestrate-launch")
def test_the_read_only_planner_views_answer_for_the_settled_run(
    launched: Launched,
) -> None:
    """Every view a planner reads a run through reports it, and reports it as this session's."""
    environment = launched.environment
    views = {
        ("runs",): f"{SHIPPED_RUN}",
        ("runs", "--mine"): "[mine]",
        ("status", SHIPPED_RUN): "SETTLED",
        ("results", SHIPPED_RUN): "research",
        ("monitor", SHIPPED_RUN): "round-finished complete",
        ("goals",): "scheduler",
        ("host",): "host ",
    }
    for invocation, expected in views.items():
        view = _just(*invocation, environment=environment, seconds=60)
        assert view.returncode == 0, f"just {' '.join(invocation)} failed:\n{view.stderr}"
        assert expected in view.stdout, f"just {' '.join(invocation)} said:\n{view.stdout}"
    # Telemetry is the one view whose product is a document rather than a table, so
    # it is read as one: a bucket nothing measured must be absent, not a zero.
    telemetry = _just("telemetry", SHIPPED_RUN, environment=environment, seconds=60)
    assert telemetry.returncode == 0, telemetry.stderr
    measured = json.loads(telemetry.stdout)
    assert measured["run_id"] == SHIPPED_RUN
    assert measured["dispatches"] >= 1
    assert measured["settled_done"] >= 1


@pytest.mark.xdist_group("orchestrate-launch")
def test_a_settled_run_carries_a_surface_but_refuses_an_unreadable_reply(
    launched: Launched,
) -> None:
    """A surface remains readable, while quiescence makes a reply impossible."""
    environment = launched.environment
    raised = _just(
        "channel-surface",
        SHIPPED_RUN,
        "a stand-in status update",
        environment=environment,
        seconds=60,
    )
    assert raised.returncode == 0, raised.stderr
    assert json.loads(raised.stdout)["state"] == "queued"

    # `channel-reply` reads the envelope from stdin when no file names it, which is
    # the shape the planner doctrine uses; give it one.
    answered = subprocess.run(
        ["just", "channel-reply", SHIPPED_RUN],
        cwd=REPO_ROOT,
        env=environment,
        input=json.dumps(
            {"completion": False, "message": "keep going", "reason": "the e2e replied"}
        ),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert answered.returncode == 2, answered.stderr
    assert "has settled, so nothing will ever read a reply" in answered.stderr

    read = _just("channel-next", SHIPPED_RUN, environment=environment, seconds=60)
    assert read.returncode == 0, read.stderr
    surface = json.loads(read.stdout)["surface"]
    assert surface is not None, "the queued surface never reached the planner"
    assert surface["message"] == "a stand-in status update"


@pytest.mark.xdist_group("orchestrate-launch")
def test_stop_refuses_a_run_another_planner_launched(
    launched: Launched,
) -> None:
    """Ownership is recorded at launch and enforced at `just stop`."""
    environment = launched.environment
    foreign = dict(environment)
    foreign["CLAUDE_CODE_SESSION_ID"] = OTHER_SESSION
    foreign.pop("ONEPIPELINE_LAUNCHER", None)
    foreign.pop("ONEPIPELINE_LAUNCHER_SESSION", None)

    refused = _just("stop", SHIPPED_RUN, environment=foreign, seconds=60)
    assert refused.returncode != 0, f"another planner's run was stopped:\n{refused.stdout}"
    # The refusal names the owner rather than merely declining, and names it as the
    # launcher this repository derived rather than as `unknown` — which is what a
    # run launched with no identity established would have recorded.
    assert "not to this session" in refused.stderr, refused.stderr
    assert "claude-code:" in refused.stderr, refused.stderr

    listed = _just("runs", "--mine", environment=foreign, seconds=60)
    assert listed.returncode == 0, listed.stderr
    assert SHIPPED_RUN not in listed.stdout, "another planner's run was listed as mine"


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
    `onepipeline start` loads and validates the plan before it reads anything else,
    so pointing it at an agent graph that is not there separates the two — the plan
    was accepted if and only if the refusal is about the graph.

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
        assert "dag-scope.yaml" in reported, f"{origin} was not accepted as a plan:\n{reported}"


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
