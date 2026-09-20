"""A run stopped and then adopted reads as the live run it is, on the adopted engine.

`just stop` then `just orchestrate --adopt` is the documented way back from a driver that
is not working, and before https://github.com/nickderobertis/onepipeline/pull/390 the
views kept reading the stop after the adoption had answered it: `just status` called the
run settled with its driver dead while a fresh driver was dispatching its work, `just
watch` ended at once on `nothing-driving`, so the one command a manager arms to be told
about a run went quiet on a run that was doing something, and `just unwatched` — what the
`Stop` hook asks — excluded the run for good as a settled one. `tests/test_adopted_engine_
carries_this_plan.py` holds that the adopted release's history contains that landing; this
journey holds that the installed engine *does* what it landed, through the three recipes a
supervisor reads a run with.

The run is one agent node whose stand-in worker takes its time, launched detached so its
driver is a process the journey did not start and `just stop` is what ends it. Everything
is real — the recipes, the `onepipeline` beneath them, the stop, the adoption and the run's
own records — and the only thing doubled is the paid model, at the boundary every launch
journey here doubles it. Every state the fixture waits for is read the way an operator
reads it, off `just monitor`'s rendering of the run's merged stream.
"""

# The finding these answer is about which Nx project owns this file. It sits beside
# `tests/e2e/test_adopted_engine_settles_and_logs_e2e.py`, in the code-keyed tier every
# launch of the installed engine in this repository is in, for the reason that module
# gives: what it depends on is the whole of that key, and a project of its own for one
# launch would split a suite whose fixtures and stand-ins it shares. The third is the
# same question asked of a shell suite, which this is not: it is a pytest journey over
# the same recipes and scripts, and the xdist group is what holds it off the toolchain
# lock.
# llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] see above
# llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] see above
# llmlint: ignore-file[shell_test_tiers_stay_split] see above

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import IO, NamedTuple

import pytest
from fake_backend import AGENT_DELAY_ENV
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from project_fixtures import project_from_plan
from test_orchestrate_launch_e2e import CandidatePlan, _node
from test_orchestrate_launch_e2e import _environment as _launched_environment
from test_watch_selector_e2e import _statuses
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: `tests/e2e/nx_workspace.py`'s group: every step here is a `just` recipe blocking on
#: `uv run`, which waits on the lock a journey re-provisioning this checkout holds.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

#: A launching session this journey states rather than inherits, so the run is one
#: `just stop` and `just orchestrate --adopt` treat as this session's own.
LAUNCHING_SESSION = "e2e-reads-an-adopted-run"

WORK_NODE = "work"

#: How long the stand-in worker holds each dispatch. Long enough that the stop lands on a
#: live dispatch and the adopting driver is still driving its re-dispatch when the views
#: are read; the fixture ends the run long before it elapses.
WORKING_SECONDS = 300
#: How long a watch that is meant to run out is given, and the heartbeat clock it asks
#: for — several beats of silence, so what ends it is the clock and not the first pass.
ELAPSING_SECONDS = 6
TICK_SECONDS = 1
#: How long the run is given to reach each state the journey waits for.
PATIENCE_SECONDS = 240

#: The engine's own refusal of an adoption while a driver still holds the run.
REFUSED = 2

#: The verdict `just status` gives a driven run, and the two it gives a run nothing is
#: driving — `docs/telemetry.md` names the four. A stopped-then-adopted run read as one
#: of the latter two is the defect.
ACTIVE = "ACTIVE"
NOT_DRIVEN = ("DRIVER DEAD", "PARKED")
#: The status `just unwatched` answers when a run the session owns has nothing watching
#: it, which is what an adopted run nobody armed a watch on is.
RUNS_UNWATCHED = 6

#: The plan: one agent node, dispatched and held by the stand-in for the window above.
_ONE_NODE: CandidatePlan = {
    "schema_version": 2,
    "tasks": [
        _node(
            id=WORK_NODE,
            task=(
                "## What\nReport, taking your time.\n\n## Why\nSo a stop lands on a live "
                "dispatch and an adoption has work to drive.\n\n## Acceptance criteria\n"
                "- Reported."
            ),
        )
    ],
}


class AdoptedRun(NamedTuple):
    """A run that was stopped and then adopted, with its adopting driver still attached."""

    environment: dict[str, str]
    run: str


def _just(
    *arguments: str, environment: dict[str, str], seconds: float = 300
) -> subprocess.CompletedProcess[str]:
    """Run one real recipe from this checkout."""
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _status(run: str, environment: dict[str, str]) -> str:
    """`just status` for the run, cut where `AGENTS.md`'s watch rule cuts it.

    The view is two documents in one stream — the run's own lines, then the host's health
    report — and the words a driver verdict is read in belong to the first.
    """
    reported = _just("status", run, environment=environment, seconds=120)
    return (reported.stdout + reported.stderr).split("\n  providers:", 1)[0]


def _recorded(run: str, environment: dict[str, str], kind: str) -> int:
    """How many records of `kind` the run's merged stream renders, read through `just monitor`.

    The read-only view an operator asks the same question of, over the whole stream
    rather than the planner profile, because the records waited on here — a dispatch, a
    stop, an adoption — are the pipeline's own and the profile narrows to decisions.
    """
    streamed = _just("monitor", run, "--all", environment=environment, seconds=120)
    return sum(
        1
        for line in (streamed.stdout + streamed.stderr).splitlines()
        if line.rstrip().endswith(f"  {kind}") or f"  {kind}  " in line
    )


def _until(what: str, ready: Callable[[], bool], *, seconds: float = PATIENCE_SECONDS) -> None:
    """Poll `ready` until it answers true, or fail naming what never arrived."""
    limit = deadline(seconds)
    while time.monotonic() < limit:
        if ready():
            return
        time.sleep(1.0)
    raise AssertionError(f"{what} never arrived within {seconds}s")


def _adopting(run: str, environment: dict[str, str], sink: IO[str]) -> subprocess.Popen[str]:
    """Attach a fresh driver once the stopped one has let go, and keep it attached.

    `--adopt` refuses a run something still drives, and a stop takes a moment to end its
    driver, so only that refusal is retried — told apart by its own status rather than by
    how quickly the process ended.
    """
    limit = deadline(PATIENCE_SECONDS)
    refusal = ""
    while time.monotonic() < limit:
        adopting = subprocess.Popen(  # noqa: S603 - the real recipe, as an operator runs it
            ["just", "orchestrate", "--adopt", run],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
        try:
            adopting.wait(timeout=e2e_timeout(20))
        except subprocess.TimeoutExpired:
            return adopting
        if adopting.returncode != REFUSED:
            return adopting
        refusal = f"exit {adopting.returncode}"
        time.sleep(2)
    raise AssertionError(f"run {run} could never be adopted: {refusal}")


@pytest.fixture
def adopted(tmp_path: Path, oneharness_bin: str) -> Iterator[AdoptedRun]:
    """Launch detached, stop the run on a live dispatch, adopt it, and hold the adoption.

    The stop is aimed at a dispatch that is really running — waited for through the
    dispatch record `just monitor` renders — because a stop over a run that never
    dispatched is not the state a manager recovers from. The adoption is held in a
    subprocess for the same reason an attached launch is elsewhere: it stays attached to
    the run it took over, and the views below are read while it is.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    environment = _launched_environment(tmp_path, oneharness_bin, session=LAUNCHING_SESSION)
    environment[AGENT_DELAY_ENV] = str(WORKING_SECONDS)
    plan = tmp_path / "adopted-run.plan.json"
    plan.write_text(json.dumps(_ONE_NODE), encoding="utf-8")
    project = project_from_plan(plan)
    run = project.split(":", 1)[1]

    def recorded(kind: str) -> int:
        return _recorded(run, environment, kind)

    try:
        launched = _just(
            "orchestrate", project, "--detach", "--dag-graph", "off", environment=environment
        )
        assert launched.returncode == 0, f"the launch failed:\n{launched.stdout}{launched.stderr}"
        _until("the first dispatch", lambda: recorded("node-dispatched") >= 1)

        stopped = _just("stop", run, environment=environment, seconds=120)
        assert stopped.returncode == 0, f"the stop failed:\n{stopped.stdout}{stopped.stderr}"
        _until("the stop to be recorded", lambda: recorded("run-stopped") >= 1)

        streamed = tmp_path / "adopting.log"
        with streamed.open("w", encoding="utf-8") as sink:
            adopting = _adopting(run, environment, sink)
            try:
                _until("the adoption to be recorded", lambda: recorded("driver-adopted") >= 1)
                # And the adopting driver's own dispatch, which is what makes it a driver
                # doing something rather than one about to hand the run back.
                _until("the adopting driver's dispatch", lambda: recorded("node-dispatched") >= 2)
                yield AdoptedRun(environment, run)
            finally:
                adopting.kill()
                adopting.wait(timeout=e2e_timeout(120))
    finally:
        _just("stop", run, environment=environment, seconds=120)


def test_a_stopped_then_adopted_run_reads_as_driven_by_its_adopting_driver(
    adopted: AdoptedRun,
) -> None:
    """`just status` judges the run by the driver driving it now, not by the stop it left.

    Under the engine before the landing the recorded stop outlived the adoption: the view
    reported the run settled and its driver dead while the adopting driver was dispatching
    its work, and a manager reading it re-adopted or relaunched a run that was fine.
    """
    status = _status(adopted.run, adopted.environment)

    assert ACTIVE in status, f"the adopted run does not read as driven:\n{status}"
    for verdict in NOT_DRIVEN:
        assert verdict not in status, f"the adopted run reads as {verdict}:\n{status}"
    assert "SETTLED" not in status, f"the adopted run reads as settled:\n{status}"
    # The stop is still on the stream — what changed is what it decides, not whether it
    # is recorded — so the view is read as judging the run afresh rather than as having
    # lost the record.
    assert _recorded(adopted.run, adopted.environment, "run-stopped") >= 1, (
        "the stop this journey recorded is gone from the run's stream"
    )


def test_a_stopped_then_adopted_run_nobody_watches_is_one_unwatched_names(
    adopted: AdoptedRun,
) -> None:
    """`just unwatched` reports the adopted run as a live one nothing watches.

    The verb behind the `Stop` hook, which is what refuses to end a manager's turn over a
    run it owns and nothing watches. Under the engine before the landing a stopped run
    stayed settled to this verb however many drivers adopted it, so a manager could put
    the recovered run down and never be told.
    """
    asked = _just("unwatched", "--session", LAUNCHING_SESSION, environment=adopted.environment)
    reported = asked.stdout + asked.stderr

    assert asked.returncode == RUNS_UNWATCHED, f"exit {asked.returncode}:\n{reported}"
    assert adopted.run in reported, reported
    assert ACTIVE in reported, reported
    assert "nothing has recorded a watch on it" in reported, reported


def test_a_watch_over_a_stopped_then_adopted_run_stays_armed(adopted: AdoptedRun) -> None:
    """`just watch` waits on the adopted run rather than ending on `nothing-driving`.

    The wait is bounded so that what ends it is its own clock: a watch that returned at
    once with the run's stop would end on `settled` or `nothing-driving` before a single
    heartbeat, which is the silence `AGENTS.md`'s watch rule exists to end. The heartbeats
    are asserted as well as the ending, because a watch that elapsed without ever writing
    one would have been armed on nothing.
    """
    started = time.monotonic()
    watched = _just(
        "watch",
        adopted.run,
        "--timeout",
        str(ELAPSING_SECONDS),
        "--tick-interval",
        str(TICK_SECONDS),
        environment=adopted.environment,
        seconds=240,
    )
    waited = time.monotonic() - started
    reported = watched.stdout + watched.stderr

    statuses = _statuses()
    assert watched.returncode == statuses["elapsed"], (
        f"the watch ended at exit {watched.returncode} rather than elapsing "
        f"({statuses['elapsed']}):\n{reported}"
    )
    assert "the wait elapsed with the run still live" in reported, reported
    assert waited >= ELAPSING_SECONDS, f"the watch returned after {waited:.1f}s:\n{reported}"
    assert "nothing is driving this run" not in reported, reported
    assert "the run settled" not in reported, reported
    heartbeats = [line for line in reported.splitlines() if line.startswith("heartbeat  ")]
    assert heartbeats, f"the watch elapsed without a heartbeat:\n{reported}"
