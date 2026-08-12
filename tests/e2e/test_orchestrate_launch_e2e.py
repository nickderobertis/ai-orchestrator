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
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
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
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # Keeps this run's graph scratch, its history, and its sibling state out of the
    # host's, so a journey never reads or reclaims a live dispatch's.
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    return environment


def _events(launched: Launched) -> list[dict]:
    """Every envelope the run recorded, from its own merged store."""
    store = Path(launched.environment["ONEPIPELINE_RUNS_DIR"]) / SHIPPED_RUN / "events.jsonl"
    return [json.loads(line) for line in store.read_text(encoding="utf-8").splitlines()]


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
    try:
        yield Launched(environment, launch)
    finally:
        # A journey that failed mid-run leaves a driver behind; the supported way to
        # end one is the recipe, and it is the owner here.
        _just("stop", SHIPPED_RUN, environment=environment, seconds=60)


@pytest.mark.xdist_group("orchestrate-launch")
def test_a_shipped_plan_launches_and_settles(launched: Launched) -> None:
    """`just orchestrate` drives a shipped example to a complete settlement."""
    launch = launched.launch
    assert launch.returncode == 0, f"the launch did not settle:\n{launch.stdout}\n{launch.stderr}"
    settlement = json.loads(launch.stdout.strip().splitlines()[-1])
    assert settlement == {"run_id": SHIPPED_RUN, "settlement": "complete"}
    # The attached launch streams what `just monitor` streams — on standard error,
    # keeping the settlement record on standard output the only thing a caller has
    # to parse. The node the plan declares has to appear in that stream having
    # actually been dispatched and settled, or "complete" describes an empty run.
    assert "node-dispatched" in launch.stderr
    assert "node-settled done" in launch.stderr


@pytest.mark.xdist_group("orchestrate-launch")
def test_both_dag_scope_members_start(launched: Launched) -> None:
    """The pacemaker member launches alongside the orchestrator, not only after its interval.

    `graphs/dag-scope.yaml` declares two members and the second one is the easy one
    to ship broken: its schedule is half an hour, so a persona ref, an oneharness
    config, or a schedule shape this graph got wrong would first be heard from
    thirty minutes into a real run. `oneagentgraph` starts a scheduled member with
    the graph rather than at its first tick, so the run's own event store answers
    for both of them within seconds.
    """
    events = _events(launched)
    started = {
        event["labels"].get("member") for event in events if event["kind"] == "member-started"
    }
    assert {"orchestrator", "check-in"} <= started, f"only {sorted(started)} started"


@pytest.mark.xdist_group("orchestrate-launch")
def test_no_turn_of_this_run_reached_a_paid_provider(launched: Launched) -> None:
    """Nothing in the launched run did real model work.

    Not a hypothetical: `--mock-harness ID` replaces the provider of that exact
    identity and no other, so an earlier revision of `tests/e2e/fake_backend.py`
    mocked `codex` and left the four other candidates of a fallback chain able to
    run. One suite run then spent twenty minutes of a paid Claude subscription
    exploring this checkout. A stand-in turn calls no tools, so a `turn-activity`
    anywhere in this run is a real agent working and the whole journey is void.
    """
    acted = [event for event in _events(launched) if event["kind"] == "turn-activity"]
    assert not acted, f"{len(acted)} real tool calls ran; the first was {acted[0]['payload']}"


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
def test_the_planner_channel_carries_a_surface_and_its_reply(
    launched: Launched,
) -> None:
    """A surface reaches the planner and the planner's answer reaches the run."""
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
    assert answered.returncode == 0, f"the reply was refused:\n{answered.stderr}"
    assert json.loads(answered.stdout)["state"] == "delivered"

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
