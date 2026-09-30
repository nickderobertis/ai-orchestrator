"""A cancelled dispatch that ends inside its grace period is never reported killed.

A planner `cancel` interrupts the dispatch's running turn and, if the dispatch has not
exited when the engine's grace deadline passes, kills it and raises `dispatch-killed`.
`tests/e2e/test_orchestrate_launch_e2e.py` holds that arm under a five-second grace. This
module holds the other: a dispatch that ends before the deadline, which a supervisor must
not be told lost its uncommitted work.

Everything between the recipe and the model is real: `just orchestrate`, `just
channel-reply`, `just monitor`, the installed `onepipeline` driver and the
`oneagentgraph` it links. **The paid model alone is doubled**, by
`tests/e2e/fake_backend.py` at the `oneagentgraph` seam. That stand-in cannot take an
interrupt — oneharness's own mock provider has no turn its control socket can reach — so
the dispatch ends when its held turn has answered, and the grace is set far past that.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import NamedTuple, TypedDict

import follow_up_variables
import plan_root_variable
import pytest
import short_state
from fake_backend import AGENT_DELAY_ENV
from harness_indirections import established_indirections
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from project_fixtures import helper, project_from_plan
from waits import timeout as e2e_timeout
from waits import until

from orchestrator.plan_store import WRITABLE_PLUGIN
from orchestrator.root import REPO_ROOT

#: A real launch holds this checkout's toolchain for as long as it runs, so this module is
#: scheduled with every other journey that does.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

#: The paid model's stand-ins, and the guard covering the identities neither seam reaches.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: The launching session this module states, and what an enclosing dispatch would
#: otherwise decide for the launch: ownership and the stores it resolves.
LAUNCHING_SESSION = "e2e-graceful-cancel"
INHERITED = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "ONEPIPELINE_RUN_ROOT",
    "ONEPIPELINE_NODE_SCRATCH_DIR",
    "ONEVCS_SESSION",
    "ORCHESTRATOR_ASK_MANAGER",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    # Where an enclosing dispatch records its oneharness history. Inherited, every stand-in
    # turn here queued on that host-wide history index lock, tens of seconds a turn.
    "ONEHARNESS_HISTORY",
    "ONEHARNESS_HISTORY_DIR",
    "ONEHARNESS_HISTORY_LABELS",
    "ONEHARNESS_HISTORY_POINTER_FILE",
    *follow_up_variables.all_names(),
    plan_root_variable.name(),
)

#: Who the indirection helpers attribute their diagnostics to.
INDIRECTION_CALLER = "tests/graceful_cancel/test_graceful_cancel_e2e.py"

RUN = "cancel-graceful-e2e"
NODE = "held"

#: How long the worker's turn is held: past the judged `channel-reply` that carries the
#: cancel, so the cancel reaches a dispatch still in flight.
HELD_SECONDS = 20

#: The engine's cancellation grace, set through its published variable, before load
#: scaling. Here the deadline is the premise rather than the subject, so it is set far
#: past the whole cancelled dispatch; the journey waits for settlement, not for the grace,
#: so its length costs nothing when the dispatch ends early.
CANCEL_GRACE_ENV = "ONEPIPELINE_CANCEL_GRACE_SECONDS"
GRACE_UNSCALED_SECONDS = 150
GRACE_SECONDS = round(e2e_timeout(GRACE_UNSCALED_SECONDS))

#: The two surfaces a cancellation can raise, and the settlement it ends in.
DISPATCHED = "node-dispatched"
INTERRUPTED = "dispatch-interrupted"
KILLED = "dispatch-killed"
SETTLED = "node-settled"
SETTLED_CANCELLED = "node-settled cancelled"


class PlanNode(TypedDict):
    """The one node the launched plan holds, in the engine's plan schema."""

    id: str
    persona: str
    task: str


class Plan(TypedDict):
    """The plan `just orchestrate` launches; the engine's loader is what validates it."""

    schema_version: int
    name: str
    tasks: list[PlanNode]


class CancelCommand(TypedDict):
    """One `cancel` in a reply envelope, which the bus validates before it is appended."""

    op: str
    id: str


class ReplyEnvelope(TypedDict):
    """The reply `just channel-reply` reads on standard input."""

    version: int
    commands: list[CancelCommand]


class LaunchedRun(NamedTuple):
    """A launched run whose one worker is in flight, and the environment it lives in."""

    environment: dict[str, str]


def _environment(tmp: Path, oneharness_bin: str) -> dict[str, str]:
    environment = dict(os.environ)
    for name in INHERITED:
        environment.pop(name, None)
    root_name, plugin_name, _ = follow_up_variables.all_names()
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp / "runs")
    environment[root_name] = str(tmp / "follow-ups")
    environment[plugin_name] = WRITABLE_PLUGIN
    environment[plan_root_variable.name()] = str(tmp / "plans")
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment.update(established_indirections(INDIRECTION_CALLER))
    environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp))
    environment[AGENT_DELAY_ENV] = str(HELD_SECONDS)
    environment[CANCEL_GRACE_ENV] = str(GRACE_SECONDS)
    return environment


def _just(environment: dict[str, str], *arguments: str, stdin: str | None = None) -> str:
    """Run one of this checkout's recipes and hand back what it printed."""
    ran = subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert ran.returncode == 0, f"just {' '.join(arguments)}:\n{ran.stdout}{ran.stderr}"
    return ran.stdout


def _stream(environment: dict[str, str]) -> str:
    return _just(environment, "monitor", RUN, "--all")


def _stream_so_far(environment: dict[str, str]) -> str:
    """The run's stream while it may not exist yet: the launch records it asynchronously."""
    ran = subprocess.run(
        ["just", "monitor", RUN, "--all"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    return ran.stdout + ran.stderr


def _stamped(stream: str, phrase: str) -> datetime:
    """When the run's journal first recorded `phrase`, read off the line's own stamp."""
    line = next(line for line in stream.splitlines() if phrase in line)
    return datetime.fromisoformat(line.split()[0].replace("Z", "+00:00"))


@pytest.fixture
def launched_run(tmp_path: Path, oneharness_bin: str) -> Iterator[LaunchedRun]:
    """Launch a one-node run and hand it over once its worker is dispatched."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    environment = _environment(tmp_path, oneharness_bin)
    task = "## What\nReport.\n\n## Why\nBecause.\n\n## Acceptance criteria\n- Reported."
    plan: Plan = {
        "schema_version": 2,
        "name": RUN,
        "tasks": [{"id": NODE, "persona": "engineer", "task": task}],
    }
    written = tmp_path / f"{RUN}.plan.json"
    written.write_text(json.dumps(plan), encoding="utf-8")
    launch = subprocess.Popen(
        ["just", "orchestrate", project_from_plan(written), "--dag-graph", "off"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        until(
            "the worker to be dispatched",
            lambda: DISPATCHED in _stream_so_far(environment),
            seconds=120,
            state=lambda: _stream_so_far(environment),
        )
        yield LaunchedRun(environment)
    finally:
        launch.kill()
        launch.wait(timeout=e2e_timeout(60))
        subprocess.run(
            ["just", "stop", RUN],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )


def test_a_dispatch_that_ends_inside_the_grace_period_is_never_killed(
    launched_run: LaunchedRun,
) -> None:
    """The ordinary arm of a cancel: the dispatch ends before its deadline, and nothing is killed.

    A supervisor reading `dispatch-killed` is told that whatever the turn had not
    committed is gone, so an engine that raised it for a dispatch that ended inside its
    grace would send them looking for lost work after a dispatch that lost none.

    What is asserted is read off the run's own journal once the node has settled, because
    "was not killed" is only true once there is nothing left to kill: the engine
    interrupted the dispatch, the node then settled `cancelled` before the deadline, and no
    kill was raised. The wait ends on that settlement, not on the grace, so the run takes
    as long as the dispatch does; and it is bounded past the grace, so a dispatch the
    engine does kill still settles inside it and fails on the kill rather than on a timeout.
    """
    environment = launched_run.environment
    envelope: ReplyEnvelope = {"version": 2, "commands": [{"op": "cancel", "id": NODE}]}
    _just(environment, "channel-reply", RUN, stdin=json.dumps(envelope))

    until(
        "the cancelled node to settle",
        lambda: SETTLED in _stream(environment),
        seconds=GRACE_UNSCALED_SECONDS + 60,
        state=lambda: _stream(environment),
    )
    stream = _stream(environment)

    interrupted, settled = _stamped(stream, INTERRUPTED), _stamped(stream, SETTLED)
    assert interrupted <= settled, stream
    assert (settled - interrupted).total_seconds() < GRACE_SECONDS, stream
    assert SETTLED_CANCELLED in stream, stream
    assert KILLED not in stream, (
        f"a dispatch that ended inside the grace period was reported killed:\n{stream}"
    )
