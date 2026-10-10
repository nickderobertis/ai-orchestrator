"""A live driver whose journal has gone quiet reads `PARKED`, and `PARKED` is driven.

Before https://github.com/nickderobertis/onepipeline/pull/701, and the published-hold
reading #714 added to it, the engine read a run `PARKED` once its journal had been quiet
past `ONEPIPELINE_PARKED_AFTER_SECONDS` and counted that as nothing driving it: `status`
advised `adopt`, an adoption ended the live driver's work, and a watch ended
`nothing-driving` on a run that was still being driven. `AGENTS.md` told a manager the
same thing — treat `PARKED` as stopped and intervene. This journey holds the adopted
engine to the reading `AGENTS.md` now states, through the recipes a manager reads a run
with: `just status` calls the run `PARKED` and driven, advises `stop` and never `adopt`,
`just orchestrate --adopt` refuses it, and `just watch` stays armed on it.

The run is one agent node whose stand-in worker holds its dispatch, launched detached so
its driver is alive and quiet. The threshold is shortened through the variable the engine
reads, in the readers' environment only, because what is under test is the verdict rather
than the default half hour. Everything is real — the recipes, the `onepipeline` beneath
them and the run's own records — and only the paid model is doubled, at the boundary
every launch journey here doubles it.
"""

# The same answers `tests/e2e/test_adopted_engine_reads_an_adopted_run_e2e.py` gives, for
# the same reason: this launches the installed engine through the recipes, so it sits in
# the code-keyed tier every such launch here is in and shares that tier's fixtures, and the
# xdist group is what holds it off the toolchain lock.
# llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] see above
# llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] see above
# llmlint: ignore-file[shell_test_tiers_stay_split] see above

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple

import pytest
from fake_backend import AGENT_DELAY_ENV
from nx_workspace import WORKSPACE_INSTALL_MARKS
from project_fixtures import project_from_plan
from test_adopted_engine_reads_an_adopted_run_e2e import _just, _recorded, _status, _until
from test_orchestrate_launch_e2e import CandidatePlan, _node
from test_orchestrate_launch_e2e import _environment as _launched_environment
from test_watch_recipe_e2e import heartbeats, returned, watch

from orchestrator.run_reading import RunId

#: A reader of the toolchain this checkout provisioned: every step here runs its tool
#: through a `just` recipe or `uv run`, and no writer touches that toolchain, so
#: `tests/e2e/nx_workspace.py` asks no xdist group of it.
pytestmark = list(WORKSPACE_INSTALL_MARKS)

#: A launching session this journey states rather than inherits, so the run is this
#: session's own to `just stop` when the journey is done with it.
LAUNCHING_SESSION = "e2e-parked-run-is-driven"

#: The variable the engine's views read the quiet threshold from, and the threshold this
#: journey sets: seconds of silence rather than half an hour. The held dispatch still
#: publishes a liveness beat about every fifteen seconds, and a beat is a journal write, so
#: under a threshold this short the run reads `ACTIVE` just after each beat and `PARKED`
#: for the rest of the cycle — which is the transition a watch must outlast.
PARKED_AFTER_ENV = "ONEPIPELINE_PARKED_AFTER_SECONDS"
PARKED_AFTER_SECONDS = 4

#: How long the stand-in worker holds its dispatch: far past every read below, so the
#: driver is alive for the whole journey.
WORKING_SECONDS = 600
#: How long a watch that is meant to run out is given — several beat cycles, so the run
#: goes from driving to parked more than once while it waits — and its heartbeat clock.
ELAPSING_SECONDS = 45
TICK_SECONDS = 1

#: The engine's own refusal of an adoption while a driver still holds the run.
REFUSED = 2

_ONE_NODE: CandidatePlan = {
    "schema_version": 2,
    "tasks": [
        _node(
            id="work",
            task=(
                "## What\nReport, taking your time.\n\n## Why\nSo the driver stays alive "
                "while its journal goes quiet.\n\n## Acceptance criteria\n- Reported."
            ),
        )
    ],
}


class HeldRun(NamedTuple):
    """A detached run whose live driver holds one dispatch open, quiet between its beats.

    `environment` is the readers': the launch's, with the shortened threshold.
    """

    environment: dict[str, str]
    run: RunId


@pytest.fixture
def held(tmp_path: Path, oneharness_bin: str) -> Iterator[HeldRun]:
    """Launch detached and hand the run over as soon as the held dispatch is recorded."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    # The recipes and the engine are real; the paid provider is doubled where every launch
    # journey here doubles it.
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment = _launched_environment(tmp_path, oneharness_bin, session=LAUNCHING_SESSION)
    # llmlint: ignore[e2e_not_mocked] The same substituted provider, told to hold its turn.
    environment[AGENT_DELAY_ENV] = str(WORKING_SECONDS)
    plan = tmp_path / "parked-run.plan.json"
    plan.write_text(json.dumps(_ONE_NODE), encoding="utf-8")
    project = project_from_plan(plan)
    run = RunId(project.split(":", 1)[1])
    readers = {**environment, PARKED_AFTER_ENV: str(PARKED_AFTER_SECONDS)}

    try:
        launched = _just(
            "orchestrate", project, "--detach", "--dag-graph", "off", environment=environment
        )
        assert launched.returncode == 0, f"the launch failed:\n{launched.stdout}{launched.stderr}"
        _until("the dispatch", lambda: _recorded(run, environment, "node-dispatched") >= 1)
        yield HeldRun(readers, run)
    finally:
        _just("stop", run, environment=environment, seconds=120)


@pytest.fixture
def parked(held: HeldRun) -> HeldRun:
    """The held run, once its quiet journal has carried it past the threshold."""
    _until("the run to read PARKED", lambda: "PARKED" in _status(held.run, held.environment))
    return held


def test_a_live_quiet_run_reads_parked_driven_and_is_advised_stop_never_adopt(
    parked: HeldRun,
) -> None:
    """`just status` reads the run `PARKED`, driven, and offers `stop` alone as its lever.

    Under the engine before the landing the same run read `PARKED: nothing is driving this
    run; adopt it or stop it`, and the adoption that advice offered ended live work.
    """
    status = _status(parked.run, parked.environment)

    assert "PARKED" in status, status
    assert "is alive" in status, f"the advice does not name the live driver:\n{status}"
    assert "stop" in status, f"the advice does not offer `stop`:\n{status}"
    assert "adopt" not in status, f"a PARKED run is offered to an adoption:\n{status}"
    assert "DRIVER DEAD" not in status, status

    reading = _just(
        "status", parked.run, "--json", "--no-providers", environment=parked.environment
    )
    assert reading.returncode == 0, reading.stdout + reading.stderr
    document = json.loads(reading.stdout)
    assert document["word"] == "PARKED", document
    assert document["driven"] is True, document
    assert document["ending"] is None, document
    assert document["paused"] is None, document


def test_an_adoption_of_a_parked_run_is_refused(parked: HeldRun) -> None:
    """`just orchestrate --adopt` refuses a `PARKED` run as it refuses an `ACTIVE` one.

    And the live driver it refused to displace keeps the run: nothing was adopted.
    """
    adopting = _just("orchestrate", "--adopt", parked.run, environment=parked.environment)
    said = adopting.stdout + adopting.stderr

    assert adopting.returncode == REFUSED, f"exit {adopting.returncode}:\n{said}"
    assert "PARKED" in said, said
    assert _recorded(parked.run, parked.environment, "driver-adopted") == 0, said
    assert "PARKED" in _status(parked.run, parked.environment)


def test_a_watch_does_not_end_nothing_driving_when_its_run_turns_parked(held: HeldRun) -> None:
    """`just watch --until nothing-driving` outlasts its run going from `ACTIVE` to `PARKED`.

    Under the engine before the landing the run's views turning `PARKED` under the wait
    ended it `nothing-driving` — the silence `AGENTS.md`'s watch rule exists to end, on a
    run whose driver was still driving it. `just status` is read throughout the wait, so
    the journey proves the run did turn `PARKED` while the watch stayed armed.
    """
    seen: set[str] = set()
    with ThreadPoolExecutor(max_workers=1) as pool:
        started = time.monotonic()
        waiting = pool.submit(
            watch,
            held.run,
            "--until",
            "nothing-driving",
            "--timeout",
            str(ELAPSING_SECONDS),
            "--tick-interval",
            str(TICK_SECONDS),
            environment=held.environment,
        )
        while not waiting.done():
            status = _status(held.run, held.environment)
            seen.update(word for word in ("ACTIVE", "PARKED") if word in status)
            time.sleep(1.0)
        watched = waiting.result()
        waited = time.monotonic() - started

    ended = returned(watched)
    assert ended.condition == "elapsed", (
        f"the watch ended on {ended.condition!r} rather than elapsing:\n{watched.said}"
    )
    assert waited >= ELAPSING_SECONDS, f"the watch returned after {waited:.1f}s:\n{watched.said}"
    assert heartbeats(watched), f"the watch elapsed without a heartbeat:\n{watched.said}"
    assert seen == {"ACTIVE", "PARKED"}, (
        f"the run read only {sorted(seen)} under the wait, so it never went from driving "
        "to parked while the watch was armed"
    )
