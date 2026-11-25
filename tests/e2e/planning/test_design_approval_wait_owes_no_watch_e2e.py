"""A planning flow waiting only on the user's design approval is owed no watch.

The user runs several manager sessions on one host, and a planning flow ends on a person
reviewing its design document: once `just plan` is waiting there, nothing is left for the
manager to do but wait for the user, and a watch re-armed every 35 minutes only spends
tokens to find the run still waiting. The installed engine decides that standing
(onepipeline#1841); this journey holds that the host reaches it as wired.

One sequence on one bench (`planning_bench`), each step through the real recipe, with only
the paid model doubled:

1. `just plan <brief> board` drafts, reviews, designs and copies a plan and waits at
   `approve-design` with its item open. For the launching session, the installed engine's
   `unwatched` owes nothing — neither the planning run nor its flow — naming the run as
   waiting on that item, and the `Stop` hook this repository's `.claude/settings.json`
   registers, run as the harness runs it, lets the turn end.
2. `just approve-design` attests the node, the run moves on and settles, and both again
   require the manager's attention: `unwatched` names the run owed, and the hook blocks the
   stop naming it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from typing import NamedTuple

import pytest
from drafting import brief, drafting
from planning_bench import BOARD, Bench, bench, just, run, runs_of, status
from waits import timeout as e2e_timeout

pytestmark = [pytest.mark.usefixtures("module_tmpdir"), pytest.mark.xdist_group("planning")]

#: The launching session every recipe below runs as, and the one asked about.
SESSION = "e2e-planning-human-wait"
#: `onepipeline unwatched`'s status when something the session owns is owed a watch.
RUNS_UNWATCHED = 6


class Reading(NamedTuple):
    """What the session's two readers answered at one moment of the sequence."""

    unwatched: subprocess.CompletedProcess[str]
    stop: subprocess.CompletedProcess[str]
    nodes: dict[str, str]


class Sequence(NamedTuple):
    planned: subprocess.CompletedProcess[str]
    planning_run: str
    waiting: Reading
    approved: subprocess.CompletedProcess[str]
    moved_on: Reading


def _unwatched(bench: Bench) -> subprocess.CompletedProcess[str]:
    """The installed engine's `unwatched` about the launching session, at the wake budget
    `.claude/settings.json` exports."""
    settings = json.loads((bench.checkout / ".claude" / "settings.json").read_text("utf-8"))
    return run(
        bench,
        "node_modules/.bin/onepipeline",
        *("unwatched", "--session", SESSION),
        *("--wake-budget", settings["env"]["ONEPIPELINE_WAKE_BUDGET"]),
        seconds=120,
    )


def _stop_hook(bench: Bench) -> subprocess.CompletedProcess[str]:
    """The `Stop` hook `.claude/settings.json` registers, run as Claude Code runs it: its
    command through a shell, `CLAUDE_PROJECT_DIR` naming the checkout, the payload on stdin."""
    settings = json.loads((bench.checkout / ".claude" / "settings.json").read_text("utf-8"))
    ((entry,),) = [one["hooks"] for one in settings["hooks"]["Stop"]]
    payload = {"session_id": SESSION, "hook_event_name": "Stop", "stop_hook_active": False}
    return subprocess.run(  # noqa: S603 - the tracked hook command, as the harness runs it
        ["bash", "-c", entry["command"]],  # noqa: S607 - the shell the harness runs it in
        input=json.dumps(payload),
        cwd=bench.checkout,
        env={**bench.environment, "CLAUDE_PROJECT_DIR": str(bench.checkout)},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(entry["timeout"]),
        check=False,
    )


def _reading(bench: Bench, run_id: str) -> Reading:
    nodes = status(bench, run_id)["nodes"]
    assert isinstance(nodes, list), nodes
    return Reading(
        _unwatched(bench),
        _stop_hook(bench),
        {str(node["id"]): str(node.get("status")) for node in nodes},
    )


@pytest.fixture(scope="module")
def sequence(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Sequence]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench_ = bench(tmp_path_factory.mktemp("planning-human-wait"), oneharness_bin, SESSION)
    native = f"plan-hw-{os.getpid()}"
    drafting(bench_, native)
    planned = just(bench_, "plan", brief(bench_, native), BOARD)
    (planning_run,) = [one for one in runs_of(bench_) if one.endswith("-planning")]
    waiting = _reading(bench_, planning_run)
    approved = just(bench_, "approve-design", f"{BOARD}:{native}", planning_run)
    moved_on = _reading(bench_, planning_run)
    yield Sequence(planned, planning_run, waiting, approved, moved_on)
    just(bench_, "stop", planning_run, seconds=120)


def test_the_plan_flow_reaches_its_design_approval_wait(sequence: Sequence) -> None:
    assert sequence.planned.returncode == 0, sequence.planned.stdout + sequence.planned.stderr
    nodes = sequence.waiting.nodes
    assert nodes["approve-design"] == "waiting", nodes
    assert all(state == "done" for node, state in nodes.items() if node != "approve-design")


def test_a_run_waiting_only_on_the_design_approval_is_owed_no_watch(sequence: Sequence) -> None:
    """Nothing the session owns is owed — the run and the flow that launched it alike — and
    the run is named as waiting on the open approval item."""
    answered = sequence.waiting.unwatched
    assert answered.returncode == 0, answered.stdout + answered.stderr
    said = answered.stdout + answered.stderr
    assert sequence.planning_run in said, said
    assert "waits on a person" in said and "'approve-design' is open" in said, said


def test_the_registered_stop_hook_lets_the_turn_end_at_the_design_approval_wait(
    sequence: Sequence,
) -> None:
    hooked = sequence.waiting.stop
    assert hooked.returncode == 0, hooked.stdout + hooked.stderr
    assert '"decision"' not in hooked.stdout, hooked.stdout


def test_once_the_design_is_approved_the_run_is_owed_again(sequence: Sequence) -> None:
    """The approval moves the run on, and from its present state it needs the manager again:
    `unwatched` names it owed and the hook refuses the stop naming it."""
    approved = sequence.approved
    assert approved.returncode == 0, approved.stdout + approved.stderr
    assert sequence.moved_on.nodes["approve-design"] == "done", sequence.moved_on.nodes
    answered = sequence.moved_on.unwatched
    assert answered.returncode == RUNS_UNWATCHED, answered.stdout + answered.stderr
    assert sequence.planning_run in answered.stdout, answered.stdout
    verdict = json.loads(sequence.moved_on.stop.stdout)
    assert verdict["decision"] == "block", verdict
    assert sequence.planning_run in verdict["reason"], verdict
