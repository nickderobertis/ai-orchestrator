"""A turn the real producer really lost, reported by the real onejudge to the real judge side.

The monitor's judge side is `onemessagebus serve surfaces --codec monitor`, as
`graphs/dag-scope.yaml` declares it, and the binding this host declares reads a lost turn
off one field: the `turn` object onejudge puts on every `supervisor` frame, whose `outcome`
is `lost` and whose `cause` and `harness` are what onejudge read off the harness's own
report. So nothing on this host reads a transcript any more — raising one verbatim is what
cost this host its supervisory layer for a day, twenty-one thousand characters a planner
could neither read nor drop — and what has to hold is that the producer this host installs,
losing a turn for real, reaches the judge side it spawns as one bounded line.

Nothing here is stood in for but the model endpoint. The real `codex` runs through the
real `oneharness` the agent side of a real `onejudge run` calls, against a loopback endpoint
that refuses every turn (`tests/lost_turn_producer.py`); onejudge reports the loss to the
argv read out of the graph, spawned over a run directory the real engine made; and the
channel is read back through `onemessagebus status`.

It reads the producer this host has installed, which lives outside the workspace and so
outside every `nx.json` key, so it runs in the uncached tier.
"""

# The finding these answer is about which Nx project owns this file, so it is the file
# that is suppressed and a project split that would resolve it.
# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] see above
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] see above

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import lost_turn_producer
import monitor_conversation
import pytest
from conftest import git
from lost_turn_producer import PRODUCER
from monitor_conversation import Conversation, Lost
from test_monitor_quiet_turn_e2e import (
    ASKER,
    ASKER_ENV,
    MEMBER_FAILED,
    NAMED_FAILURE_LIMIT,
    SURFACE_KIND_OF_A_LOST_TURN,
    _raised_since,
    _records,
)
from test_orchestrate_launch_e2e import (
    RUN_ID_ENV,
    SETTLES_UNWATCHED,
    _environment,
    _judge_command,
    _just,
    _settling_project,
)

pytestmark = [pytest.mark.reads_checkouts, pytest.mark.xdist_group("lost-turn-wire-contract")]

#: The run whose directory the judge side raises on.
RUN = "wire-contract-e2e"

#: How oneharness is told which binary a harness id runs: the real producer, named
#: directly, so the launch environment's provider stand-in never answers this turn.
PRODUCER_BIN_ENV = f"ONEHARNESS_BIN_{PRODUCER.upper()}"

#: How oneharness classifies the loopback endpoint's refusal (a `401`).
REFUSED_BY_THE_ENDPOINT = "auth"


@pytest.fixture(scope="session")
def codex_bin() -> str:
    """The real producer binary, without which there is no lost turn to raise.

    Deliberately a failure and not a skip: a journey that quietly does not run reports
    the same green as one that ran. `scripts/session-setup.sh` installs it.
    """
    found = lost_turn_producer.installed_producer()
    if found is None:
        pytest.fail(
            f"the real {PRODUCER} producer is not installed, so no turn can be lost the "
            "way a monitor loses one — run `scripts/session-setup.sh`"
        )
    return found


@pytest.fixture(scope="module")
def run_environment(
    tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str
) -> Iterator[dict[str, str]]:
    """The environment of a run root the real engine made, launched and settled unwatched."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("wire-contract-run")
    environment = _environment(tmp_path, oneharness_bin)
    launch = _just(
        "orchestrate", _settling_project(tmp_path, RUN), *SETTLES_UNWATCHED, environment=environment
    )
    try:
        assert launch.returncode == 0, launch.stdout + launch.stderr
        assert (Path(environment["ONEPIPELINE_RUNS_DIR"]) / RUN / "launch.json").is_file()
        yield {**environment, RUN_ID_ENV: RUN, ASKER_ENV: ASKER}
    finally:
        _just("stop", RUN, environment=environment, seconds=60)


def test_a_turn_the_real_producer_lost_raises_one_named_surface_and_ends_the_member(
    run_environment: dict[str, str], codex_bin: str, tmp_path: Path
) -> None:
    """One lost turn reaches the planner as a line naming its cause and harness, once.

    The cause and the harness are asserted against what onejudge's own report attributes
    the failure to — the candidate that ran and how oneharness classified it — rather than
    against anything the judge side says about itself. And the member ends: the judge
    side's exit 1 is recorded by onejudge as that side failing, and the run fails with the
    turn's own classified failure, which is what the graph then records as the death.
    """
    (tmp_path / "codex-home").mkdir()
    home = lost_turn_producer.refusing_home(tmp_path / "codex-home")
    # A repository, because the producer refuses to run in a directory it does not trust
    # before it asks the endpoint anything — a loss, but not the one this is about.
    conversation = tmp_path / "conversation"
    git("init", "-q", str(conversation))
    before = _records(run_environment, RUN)

    held = monitor_conversation.hold(
        Conversation(Lost(home), _judge_command()),
        {**run_environment, PRODUCER_BIN_ENV: codex_bin},
        conversation,
    )

    report = held.report or {}
    (candidate,) = report["telemetry"]["attribution"][0]["candidates"]
    assert candidate["ran"] is False and candidate["harness_id"] == PRODUCER, candidate
    cause = candidate.get("failure_kind") or candidate["status"]
    assert cause == REFUSED_BY_THE_ENDPOINT, (
        f"the turn was lost before it reached the refusing endpoint: {candidate}"
    )
    assert f"{PRODUCER} [{cause}]" in held.error(), (
        f"the run did not end on the turn's own classified failure: {held.error()}"
    )
    assert any(f"exit status: {MEMBER_FAILED}" in reason for reason in held.judge_errors()), (
        f"onejudge did not record the judge side failing the lost turn: {held.judge_errors()}"
    )
    raised = _raised_since(run_environment, RUN, before)
    assert len(raised) == 1, f"one lost turn raised {len(raised)} surface(s): {raised}"
    surface = raised[0]
    assert surface["kind"] == SURFACE_KIND_OF_A_LOST_TURN, surface
    assert surface["blocking"] is False, surface
    assert surface.get("source") == "proposal", surface
    assert surface["message"].startswith(f"monitor turn failed: {cause} on {PRODUCER}."), surface[
        "message"
    ]
    assert len(surface["message"]) <= NAMED_FAILURE_LIMIT, surface["message"]


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
