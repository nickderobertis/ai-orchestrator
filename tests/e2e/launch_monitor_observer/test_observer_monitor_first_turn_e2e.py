"""Every run's observer monitor survives its first turn, launched as a manager launches.

The monitor `graphs/dag-scope.yaml` declares runs its worker side under
`oneharness.orchestrator.toml`, which names no node-scratch indirection. Should that side
instead resolve the config discovered from the repository root — the worker's
`oneharness.toml`, whose parent repoints `XDG_RUNTIME_DIR` from
`ONEPIPELINE_NODE_SCRATCH_DIR` — `oneharness` refuses its first turn, because an observer
is handed no node scratch, and the run goes unwatched.

So this launches the way a manager does: the real `just orchestrate` from the repository's
root under the shipped observer graph, from an environment holding no
`ONEPIPELINE_NODE_SCRATCH_DIR`, every turn delegated by `tests/e2e/fake_backend.py` to the
real `oneharness` CLI under `--mock-harness` on the variant the chain names, so only the
paid model is doubled. It reads the monitor's first turn completing under the launching
driver and again under the driver `just orchestrate --adopt` attaches once the first has
exited, which relaunches the observer.
"""

# It sits beside `test_observer_graph_liveness_e2e.py` and
# `test_adopted_engine_reads_an_adopted_run_e2e.py`, whose launch helpers it reuses, in the
# code-keyed tier every launch of the installed engine here is in; a project of its own for
# one launch would split a suite whose fixtures and stand-ins it shares. It is a pytest
# journey over the real recipes, not a shell suite.
# llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] see above
# llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] see above
# llmlint: ignore-file[shell_test_tiers_stay_split] see above

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import pytest
from fake_backend import (
    OBSERVER_ANSWER_ENV,
    OBSERVER_MEMBER_ENV,
    TURN_GATE_ENV,
    TURN_GATE_REACHED,
)
from project_fixtures import project_from_plan
from served_recipe import attached, stop_launch
from test_adopted_engine_reads_an_adopted_run_e2e import _adopting, _just, _recorded, _until
from test_observer_graph_liveness_e2e import (
    OBSERVER_DEAD,
    OBSERVER_NOT_RESTARTED,
    Envelope,
    _graph_events,
    _of,
)
from test_orchestrate_launch_e2e import CandidatePlan, _node
from test_orchestrate_launch_e2e import _environment as _launched_environment

from orchestrator.root import REPO_ROOT

# What the recipes these journeys run leave in the temporary directory, by design or
# from a published tool they run, is this module's own and goes with it
# (`tests/short_state.py`).
pytestmark = [pytest.mark.usefixtures("module_tmpdir")]

#: The variable a dispatched side's runtime directory is repointed from, which a manager
#: session's environment never holds.
NODE_SCRATCH_ENV = "ONEPIPELINE_NODE_SCRATCH_DIR"

#: Where `oneagentgraph` keeps each graph's own record, which `_launched_environment`
#: names inside this journey's temporary root.
GRAPH_STATE_ENV = "ONEAGENTGRAPH_STATE_DIR"

LAUNCHING_SESSION = "e2e-observer-monitor-first-turn"
MONITOR_MEMBER = "monitor"
WORK_NODE = "work"

#: What `oneharness` says when it refuses a variant whose `env_from` source is unset,
#: naming the variable it could not read. A death carrying it is the regression.
INDIRECTION_REFUSAL = f"variant environment indirection `{NODE_SCRATCH_ENV}` is not set"

#: What the stand-in monitor says on its turn: an ordinary quiet turn, so what is read is
#: whether the turn ran at all and not anything the model's words caused.
SAID_ON_A_QUIET_TURN = "read the detailed stream since the cursor; nothing needed raising"

#: One agent node, held at the stand-in's turn gate for the whole journey, so the run is
#: live under both drivers while their observers take their first turns.
_HELD_NODE: CandidatePlan = {
    "schema_version": 2,
    "tasks": [
        _node(
            id=WORK_NODE,
            task=(
                "## What\nReport, once you are let go.\n\n## Why\nSo the run is live while "
                "its observer takes its first turn.\n\n## Acceptance criteria\n- Reported."
            ),
        )
    ],
}


class FirstTurns(NamedTuple):
    """What one observer graph recorded by the time its monitor's first turn settled."""

    #: The monitor's agent-side turns that completed.
    completed: list[Envelope]
    #: Every member death the graph recorded.
    died: list[Envelope]


def _first_turns(events: list[Envelope], since: datetime) -> FirstTurns | None:
    """What the observers the run started from ``since`` on recorded.

    A driver relaunches an observer that died, so one driver's observers are every graph
    started while it drove; the stopped driver starts none, so every graph started once
    its stop is recorded is the adopting driver's. `None` until one has started.
    """
    started = [event.at for event in _of(events, "graph-started") if event.at >= since]
    if not started:
        return None
    own = [event for event in events if event.at >= min(started)]
    completed = [
        event
        for event in _of(own, "turn-completed", MONITOR_MEMBER)
        if event.payload.get("role") == "assistant"
    ]
    # A turn the driver's own stop cancels is the observer being ended, never a death.
    died = [death for death in _of(own, "member-died") if death.payload.get("cause") != "cancelled"]
    return FirstTurns(completed=completed, died=died)


def _settled_first_turn(since: datetime, state: Path) -> FirstTurns:
    """Wait for an observer started from ``since`` on to complete a monitor turn, or die."""
    seen: list[FirstTurns] = []

    def settled() -> bool:
        turns = _first_turns(_graph_events(state, live=True), since)
        if turns is None:
            return False
        seen[:] = [turns]
        return bool(turns.completed or turns.died)

    _until(f"an observer started from {since} to complete its monitor's first turn", settled)
    return seen[0]


def _refused_on_the_indirection(died: list[Envelope]) -> list[Envelope]:
    """Every death whose recorded cause names the unset indirection."""
    return [death for death in died if INDIRECTION_REFUSAL in json.dumps(death.payload)]


def _observer_verdicts(run: str, environment: dict[str, str]) -> list[str]:
    """The observer-death verdicts `just status` prints for the run, cut at the host report."""
    reported = _just("status", run, environment=environment, seconds=120)
    assert reported.returncode == 0, f"`just status` failed:\n{reported.stdout}{reported.stderr}"
    said = (reported.stdout + reported.stderr).split("\n  providers:", 1)[0]
    return [verdict for verdict in (OBSERVER_DEAD, OBSERVER_NOT_RESTARTED) if verdict in said]


class Observed(NamedTuple):
    """One run's two observers' first turns, and what `just status` said after each."""

    launched: FirstTurns
    launched_verdicts: list[str]
    adopted: FirstTurns
    adopted_verdicts: list[str]
    #: Whether the launch environment held the scratch variable, which it must not.
    inherited_scratch: bool


@pytest.fixture(scope="module")
def observed(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Observed]:
    """Launch from the root, read the first observer, stop the driver, adopt, read the second."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("observer-first-turn")
    environment = _launched_environment(tmp_path, oneharness_bin, session=LAUNCHING_SESSION)
    # llmlint: ignore[e2e_not_mocked] Only the paid model's words are scripted.
    environment[OBSERVER_ANSWER_ENV] = SAID_ON_A_QUIET_TURN
    environment[OBSERVER_MEMBER_ENV] = MONITOR_MEMBER
    gate = tmp_path / "turn-gate"
    gate.mkdir()
    environment[TURN_GATE_ENV] = str(gate)
    state = Path(environment[GRAPH_STATE_ENV])
    plan = tmp_path / "observer-first-turn.plan.json"
    plan.write_text(json.dumps(_HELD_NODE), encoding="utf-8")
    project = project_from_plan(plan)
    run = project.split(":", 1)[1]

    adopting = None
    try:
        launched = attached(["just", "orchestrate", project, "--detach"], environment)
        assert launched.returncode == 0, f"the launch failed:\n{launched.stdout}{launched.stderr}"
        _until("the worker to be in its turn", (gate / TURN_GATE_REACHED).exists)
        first = _settled_first_turn(datetime.min.replace(tzinfo=UTC), state)
        first_verdicts = _observer_verdicts(run, environment)

        stopped = _just("stop", run, environment=environment, seconds=120)
        assert stopped.returncode == 0, f"the stop failed:\n{stopped.stdout}{stopped.stderr}"
        _until("the stop to be recorded", lambda: _recorded(run, environment, "run-stopped") >= 1)
        stopped_at = datetime.now(UTC)

        with (tmp_path / "adopting.log").open("w", encoding="utf-8") as sink:
            adopting = _adopting(run, environment, sink)
            second = _settled_first_turn(stopped_at, state)
            second_verdicts = _observer_verdicts(run, environment)
            yield Observed(
                launched=first,
                launched_verdicts=first_verdicts,
                adopted=second,
                adopted_verdicts=second_verdicts,
                inherited_scratch=NODE_SCRATCH_ENV in environment,
            )
    finally:
        stop_launch(run, environment, *([] if adopting is None else [adopting]))


def test_the_launch_is_made_as_a_manager_makes_it(observed: Observed) -> None:
    """From the root, so the root `oneharness.toml` is what discovery would find, and with
    no node scratch in the environment, as a manager session's has none."""
    assert (REPO_ROOT / "oneharness.toml").is_file()
    assert not observed.inherited_scratch, (
        f"the launch environment carries {NODE_SCRATCH_ENV}, which hides a worker side that "
        "resolves the dispatched role's indirections from a manager's launch"
    )


def test_the_launched_observers_monitor_completes_its_first_turn(observed: Observed) -> None:
    """The observer the launching driver attaches watches: its monitor's first turn runs."""
    refused = _refused_on_the_indirection(observed.launched.died)
    assert not refused, (
        f"the monitor's first turn was refused on the unset {NODE_SCRATCH_ENV} indirection, "
        f"so its worker side resolved the dispatched worker's config: {refused}"
    )
    assert observed.launched.completed, (
        f"the monitor completed no turn; the graph recorded deaths {observed.launched.died}"
    )
    assert not observed.launched_verdicts, (
        f"`just status` reports the launched run's observer as {observed.launched_verdicts}"
    )


def test_the_adopted_observers_monitor_completes_its_first_turn(observed: Observed) -> None:
    """The observer `just orchestrate --adopt` relaunches watches the same way."""
    refused = _refused_on_the_indirection(observed.adopted.died)
    assert not refused, (
        f"the adopted observer's monitor was refused on the unset {NODE_SCRATCH_ENV} "
        f"indirection: {refused}"
    )
    assert observed.adopted.completed, (
        f"the adopted observer's monitor completed no turn; deaths {observed.adopted.died}"
    )
    assert not observed.adopted_verdicts, (
        f"`just status` reports the adopted run's observer as {observed.adopted_verdicts}"
    )


def test_the_refusal_these_journeys_read_as_absent_is_what_oneharness_says(
    oneharness_bin: str, tmp_path: Path
) -> None:
    """The refusal asserted absent above is the one the adopted `oneharness` prints, so a
    rewording fails here rather than leaving those assertions vacuous.

    It is taken from the real CLI, refusing a mocked turn under the root `oneharness.toml`
    from an environment holding no node scratch.
    """
    environment = {key: value for key, value in os.environ.items() if key != NODE_SCRATCH_ENV}
    # llmlint: ignore[e2e_not_mocked] Only the paid model is doubled, by the CLI's own mock.
    environment["MOCK_STDOUT"] = json.dumps({"result": SAID_ON_A_QUIET_TURN})
    refused = subprocess.run(
        [
            oneharness_bin,
            "run",
            "--config",
            str(REPO_ROOT / "oneharness.toml"),
            "--harness",
            "codex:primary",
            "--mock-harness",
            "codex",
            "--prompt",
            "report",
        ],
        cwd=tmp_path,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert refused.returncode != 0 and INDIRECTION_REFUSAL in refused.stderr, (
        f"`oneharness` no longer refuses an unset indirection in the words "
        f"{INDIRECTION_REFUSAL!r}: exit {refused.returncode}\n{refused.stdout}{refused.stderr}"
    )
