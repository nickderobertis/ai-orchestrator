"""A cancelled dispatch that stops when it is asked is never reported killed.

A planner `cancel` interrupts the dispatch's running turn and, if the dispatch has not
exited when the engine's grace deadline passes, kills it and raises `dispatch-killed`.
`tests/e2e/test_orchestrate_launch_e2e.py` holds the second arm, with a stand-in that
refuses the interrupt so only the deadline can end it. This module holds the first: a
dispatch whose turn takes the interrupt and ends, which a supervisor must not be told
lost its uncommitted work.

Everything between the recipe and the model is real: `just orchestrate`, `just
channel-reply`, `just monitor`, the installed `onepipeline` driver and the
`oneagentgraph` it links. **The paid model alone is doubled**, by
`tests/e2e/fake_backend.py` at the `oneagentgraph` seam, run here with
`COOPERATIVE_INTERRUPT_ENV` set so it serves `oneharness interrupt` as a controllable
harness does. Its log of each held turn is what shows the turn ended because it was
interrupted rather than because its hold ran out.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import follow_up_variables
import plan_root_variable
import pytest
import short_state
from fake_backend import AGENT_DELAY_ENV, COOPERATIVE_INTERRUPT_ENV, INTERRUPT_LOG, HeldTurn
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
    *follow_up_variables.all_names(),
    plan_root_variable.name(),
)

#: Who the indirection helpers attribute their diagnostics to.
INDIRECTION_CALLER = "tests/graceful_cancel/test_graceful_cancel_e2e.py"

RUN = "cancel-graceful-e2e"
NODE = "held"

#: The engine's cancellation grace, set through its published variable. Load-scaled,
#: because here the deadline is the premise rather than the subject: it bounds only the
#: interrupted turn's answer and the dispatch's teardown.
CANCEL_GRACE_ENV = "ONEPIPELINE_CANCEL_GRACE_SECONDS"
GRACE_UNSCALED_SECONDS = 15
GRACE_SECONDS = round(e2e_timeout(GRACE_UNSCALED_SECONDS))

#: The worker's hold, twice the grace: a hold that ran out would end the dispatch after
#: the deadline, so a dispatch that settles unkilled did so because the interrupt ended it.
HELD_SECONDS = GRACE_SECONDS * 2

#: The two surfaces a cancellation can raise, and the settlement it ends in.
INTERRUPTED = "dispatch-interrupted"
KILLED = "dispatch-killed"
SETTLED = "node-settled"
SETTLED_CANCELLED = "node-settled cancelled"


class CancellableRun(NamedTuple):
    """A launched run whose one worker turn is held, and where its backend logs turns."""

    environment: dict[str, str]
    interrupts: Path


def _environment(tmp: Path, oneharness_bin: str, interrupts: Path) -> dict[str, str]:
    environment = dict(os.environ)
    for name in INHERITED:
        environment.pop(name, None)
    root_name, plugin_name, _ = follow_up_variables.all_names()
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp / "runs")
    environment[root_name] = str(tmp / "follow-ups")
    environment[plugin_name] = WRITABLE_PLUGIN
    environment[plan_root_variable.name()] = str(tmp / "plans")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment.update(established_indirections(INDIRECTION_CALLER))
    environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp))
    environment[AGENT_DELAY_ENV] = str(HELD_SECONDS)
    environment[CANCEL_GRACE_ENV] = str(GRACE_SECONDS)
    environment[COOPERATIVE_INTERRUPT_ENV] = str(interrupts)
    return environment


def _just(environment: dict[str, str], *arguments: str, stdin: str | None = None) -> str:
    """Run one of this checkout's recipes and hand back what it printed."""
    ran = subprocess.run(  # noqa: S603 - this checkout's own recipes
        ["just", *arguments],  # noqa: S607
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


def _stamped(stream: str, phrase: str) -> datetime:
    """When the run's journal first recorded `phrase`, read off the line's own stamp."""
    line = next(line for line in stream.splitlines() if phrase in line)
    return datetime.fromisoformat(line.split()[0].replace("Z", "+00:00"))


@pytest.fixture
def cancellable_run(tmp_path: Path, oneharness_bin: str) -> Iterator[CancellableRun]:
    """Launch a one-node run and hand it over once its worker's turn is held open."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    interrupts = tmp_path / "interrupts"
    interrupts.mkdir()
    environment = _environment(tmp_path, oneharness_bin, interrupts)
    plan = tmp_path / f"{RUN}.plan.json"
    task = "## What\nReport.\n\n## Why\nBecause.\n\n## Acceptance criteria\n- Reported."
    node = {"id": NODE, "persona": "engineer", "task": task}
    plan.write_text(json.dumps({"schema_version": 2, "name": RUN, "tasks": [node]}))
    launch = subprocess.Popen(  # noqa: S603 - the real recipe, as an operator runs it
        ["just", "orchestrate", project_from_plan(plan), "--dag-graph", "off"],  # noqa: S607
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # A fact rather than a dispatch event: an interrupt reaches a turn only while one
        # is held, so the cancel below is sent once the backend says the turn is holding.
        until(
            "the dispatched worker's turn to be held",
            lambda: any(interrupts.glob("*.holding")),
            seconds=120,
            state=lambda: _stream(environment),
        )
        yield CancellableRun(environment, interrupts)
    finally:
        launch.kill()
        launch.wait(timeout=e2e_timeout(60))
        subprocess.run(  # noqa: S603 - the real recipe, as an operator runs it
            ["just", "stop", RUN],  # noqa: S607
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )


def test_a_dispatch_that_ends_inside_the_grace_period_is_never_killed(
    cancellable_run: CancellableRun,
) -> None:
    """The ordinary arm of a cancel: the turn takes the interrupt, and nothing is killed.

    A supervisor reading `dispatch-killed` is told that whatever the turn had not
    committed is gone, so an engine that raised it for a dispatch that stopped when asked
    would send them looking for lost work after a dispatch that lost none.

    The order is what is asserted, each step from its own record: the engine interrupted
    the dispatch, the held turn ended because of that interrupt and well before its hold
    would have run out, and the node then settled `cancelled` inside the grace, with no
    kill raised. Nothing here depends on how long a turn takes: the held turn ends on the
    interrupt, and its hold is twice the grace, so a dispatch that settled unkilled inside
    the grace can only have ended on the interrupt. The wait for settlement outlasts the
    grace, so a dispatch the engine does kill still settles inside it and fails on the
    kill rather than on a timeout.
    """
    environment = cancellable_run.environment
    envelope = {"version": 2, "commands": [{"op": "cancel", "id": NODE}]}
    _just(environment, "channel-reply", RUN, stdin=json.dumps(envelope))

    until(
        "the cancelled node to settle",
        lambda: SETTLED in _stream(environment),
        seconds=GRACE_UNSCALED_SECONDS * 3,
        state=lambda: _stream(environment),
    )
    stream = _stream(environment)

    log = cancellable_run.interrupts / INTERRUPT_LOG
    turns: list[HeldTurn] = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(turns) == 1, turns
    turn = turns[0]
    assert turn["ended_by"] == "interrupt", turn
    assert turn["interrupted_at"] is not None, turn
    assert turn["held_from"] <= turn["interrupted_at"] <= turn["ended_at"], turn
    assert turn["ended_at"] - turn["held_from"] < HELD_SECONDS, turn

    interrupted, settled = _stamped(stream, INTERRUPTED), _stamped(stream, SETTLED)
    assert interrupted <= settled, stream
    assert turn["ended_at"] <= settled.timestamp(), (turn, stream)
    assert (settled - interrupted).total_seconds() < GRACE_SECONDS, stream
    assert SETTLED_CANCELLED in stream, stream
    assert KILLED not in stream, (
        f"a dispatch that ended inside the grace period was reported killed:\n{stream}"
    )
