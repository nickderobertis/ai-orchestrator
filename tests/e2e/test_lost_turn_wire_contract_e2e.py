"""A turn the real producer really lost, put to the monitor's real judge side on a real run.

The monitor's judge side is `onemessagebus serve surfaces --codec onejudge`, as
`graphs/dag-scope.yaml` declares it, and the codec reads a harness's own machine transcript
to tell a turn its agent side lost from a turn that said something. Raising that transcript
verbatim is what cost this host its supervisory layer for a day — twenty-one thousand
characters a planner could neither read nor drop — and then a second day at a different
scale: 176.1 MB across 26 surfaces, a 3.6 GB journal, and a read-only `just runs` that needed
5.7 GB of RSS on a shared box.

The codec's reading of that shape is the bus's to prove, against a recorded fixture its own
suite keeps. What is this host's is that the producer it installs still writes a shape the
judge side it spawns reads the same way — so every journey here is that path with nothing
standing in: the real `codex` really loses a turn through a real `oneharness`
(`tests/lost_turn_producer.py`), and the argv read out of the graph is spawned against a run
directory the real engine made, whose channel is then read through `onemessagebus status`.

They differ in the one thing the codec branches on. Where the transcript records the
refusal that ended the turn, the loss is *provable*: one bounded surface names the cause
this turn's own frames recorded, and the member fails — whichever of the recorded proofs the
transcript keeps, and under this host's alternate Codex home as under the primary one, which
the surface then names. The last journey is the same real transcript with those frames
removed — the shape all 26 of this host's oversized surfaces had, `status: completed` and
`error: null` — where nothing is provable, and what has to arrive is **nothing at all**: an
unprovable transcript is content like any other, the member is answered with a
non-completion, and the channel is left as it was.

It reads the producer this host has installed, which lives outside the workspace and so
outside every `nx.json` key, so it runs in the uncached tier.
"""

# The finding these answer is about which Nx project owns this file, so it is the file
# that is suppressed and a project split that would resolve it.
# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] see above
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] see above

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import lost_turn_producer
import pytest
from lost_turn_producer import (
    PRODUCER,
    LostTurn,
    Proof,
    codex_home_named_by,
    keeping_only_the_proof,
)
from test_monitor_quiet_turn_e2e import (
    MEMBER_FAILED,
    NAMED_FAILURE_LIMIT,
    SURFACE_KIND_OF_A_LOST_TURN,
    _raised_since,
    _records,
)
from test_orchestrate_launch_e2e import (
    SETTLES_UNWATCHED,
    _environment,
    _judge_side,
    _just,
    _settling_project,
    _supervisor_frame,
)

pytestmark = [pytest.mark.reads_checkouts, pytest.mark.xdist_group("lost-turn-wire-contract")]

#: The run whose directory every journey here puts a frame to.
RUN = "wire-contract-e2e"

#: The variable the judge side reads this host's alternate codex home from, which
#: `scripts/codex-alt-home.sh` exports.
CODEX_ALT_HOME = "ORCHESTRATOR_CODEX_ALT_HOME"


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


@pytest.fixture(scope="session")
def lost_turn(
    tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str, codex_bin: str
) -> LostTurn:
    """A turn the real producer lost, captured through the real oneharness."""
    home = lost_turn_producer.refusing_home(tmp_path_factory.mktemp("codex-home"))
    return lost_turn_producer.capture(
        oneharness_bin, codex_bin, home, tmp_path_factory.mktemp("lost-turn")
    )


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
        yield environment
    finally:
        _just("stop", RUN, environment=environment, seconds=60)


def test_the_judge_side_raises_a_real_lost_turn_to_a_real_planner_channel(
    run_environment: dict[str, str], lost_turn: LostTurn
) -> None:
    """One lost turn reaches the planner as a line, under its own kind, non-blocking.

    The transcript stays out of the message, which is the whole fix: a planner may not
    filter the unread-surface line, so a surface that cannot be read and cannot be
    dropped is the worst of both. What must arrive instead is what the failure was and
    which harness it happened on, and the cause is asserted against what this turn's own
    frames recorded rather than against anything the judge side says about itself.
    """
    before = _records(run_environment, RUN)

    failed = _judge_side(_supervisor_frame(RUN, lost_turn.transcript), run_environment, RUN)

    assert failed.returncode == MEMBER_FAILED, failed.stdout + failed.stderr
    assert "completion" not in failed.stdout, failed.stdout
    raised = _raised_since(run_environment, RUN, before)
    assert len(raised) == 1, f"one lost turn raised {len(raised)} surface(s): {raised}"
    surface = raised[0]
    assert surface["kind"] == SURFACE_KIND_OF_A_LOST_TURN, surface
    assert surface["blocking"] is False, surface
    recorded = lost_turn_producer.classification_recorded_by(lost_turn.frames)
    assert len(recorded) == 1, f"this turn recorded {recorded or 'no'} classification(s)"
    assert surface["message"].startswith(f"monitor turn failed: {recorded.pop()} on {PRODUCER}."), (
        surface["message"]
    )
    assert len(surface["message"]) <= NAMED_FAILURE_LIMIT, surface["message"]
    assert lost_turn.transcript not in surface["message"], surface["message"]


@pytest.mark.parametrize("proof", list(Proof), ids=str)
def test_either_proof_a_real_lost_turn_records_is_enough_for_the_judge_side(
    run_environment: dict[str, str], lost_turn: LostTurn, proof: Proof
) -> None:
    """Each of the two frames that prove a loss is read as one on its own.

    A real lost turn records both an error notification and a failed turn, so the journey
    above cannot tell whether the judge side still reads each. Another release of the
    producer may send only one, and a judge side that had stopped reading it would take a
    lost turn for content — a failing monitor that looks healthy. So the producer's own
    transcript is cut down to one proof at a time and put to the graph's judge command.
    """
    transcript = keeping_only_the_proof(lost_turn.transcript, proof)
    before = _records(run_environment, RUN)

    failed = _judge_side(_supervisor_frame(RUN, transcript), run_environment, RUN)

    assert failed.returncode == MEMBER_FAILED, failed.stdout + failed.stderr
    raised = _raised_since(run_environment, RUN, before)
    assert len(raised) == 1, f"a lost turn proven by {proof} raised {len(raised)}: {raised}"
    surface = raised[0]
    assert surface["kind"] == SURFACE_KIND_OF_A_LOST_TURN, surface
    assert surface["blocking"] is False, surface
    assert surface["message"].startswith("monitor turn failed: "), surface["message"]
    assert f" on {PRODUCER}." in surface["message"], surface["message"]
    assert len(surface["message"]) <= NAMED_FAILURE_LIMIT, surface["message"]
    assert transcript not in surface["message"], surface["message"]


def test_a_lost_turn_under_this_hosts_alternate_codex_home_names_that_identity(
    run_environment: dict[str, str], lost_turn: LostTurn
) -> None:
    """The surface says which of this host's two codex identities lost the turn.

    `scripts/codex-alt-home.sh` exports `ORCHESTRATOR_CODEX_ALT_HOME` to every side, and
    the judge side compares it with the home the producer's own initialize response names.
    The journey above runs under a home that is not the alternate and is named `codex`;
    here the same real transcript is served with the alternate set to that very home, and
    the quota a planner is sent to look at has to be the alternate's.
    """
    environment = {**run_environment, CODEX_ALT_HOME: codex_home_named_by(lost_turn.frames)}
    recorded = lost_turn_producer.classification_recorded_by(lost_turn.frames)
    assert len(recorded) == 1, f"this turn recorded {recorded or 'no'} classification(s)"
    before = _records(environment, RUN)

    failed = _judge_side(_supervisor_frame(RUN, lost_turn.transcript), environment, RUN)

    assert failed.returncode == MEMBER_FAILED, failed.stdout + failed.stderr
    raised = _raised_since(environment, RUN, before)
    assert len(raised) == 1, f"one lost turn raised {len(raised)} surface(s): {raised}"
    assert raised[0]["message"].startswith(
        f"monitor turn failed: {recorded.pop()} on {PRODUCER}:alternate."
    ), raised[0]["message"]


def test_the_judge_side_raises_nothing_for_a_real_transcript_that_proves_no_failure(
    run_environment: dict[str, str], lost_turn: LostTurn
) -> None:
    """A real transcript nothing can be proven inside raises no surface at all.

    This is the shape that actually filled this channel: all 26 of the oversized surfaces
    measured on this host were `status: completed` with `error: null`, so nothing proved a
    failure about any of them and every one was republished as the monitor's own words —
    176.1 MB of protocol carrying zero model-authored characters. What must happen now is
    that the member is answered with a ruling onejudge can act on, and nothing at all is
    queued for a planner to read.

    The transcript is the real producer's own bytes with the two frames that record the
    refusal removed, which is the whole of the difference between the two shapes — a turn
    that completes needs a reachable provider, which this cannot have offline.
    """
    unproven = lost_turn_producer.without_the_frames_that_prove_the_loss(lost_turn.transcript)
    assert unproven != lost_turn.transcript, "the real transcript proved no failure to remove"
    before = _records(run_environment, RUN)

    answered = _judge_side(_supervisor_frame(RUN, unproven), run_environment, RUN)

    assert answered.returncode == 0, answered.stderr
    raised = _raised_since(run_environment, RUN, before)
    assert raised == [], f"an unprovable machine transcript reached the planner's queue: {raised}"
    ruling = json.loads(answered.stdout)
    assert ruling["completion"] is False, (
        f"an unprovable transcript answered onejudge with a completion, which settles a "
        f"watch nobody ruled on: {ruling}"
    )
    assert unproven not in ruling["message"], (
        f"the transcript came back to the monitor inside its own ruling: {ruling}"
    )


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
