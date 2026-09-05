"""A monitor turn's machine transcript, raised to a real planner channel as one line.

`scripts/channel-serve.py` is the judge side of `graphs/dag-scope.yaml`'s monitor: it
takes onejudge's supervisor frame on stdin and raises what the monitor said as a surface
through `onepipeline channel serve`. When what the monitor "said" is the harness's own
JSON-RPC transcript rather than prose, raising it verbatim is what cost this host its
supervisory layer for a day — twenty-one thousand characters a planner could neither read
nor drop, twelve hundred of them discarded as noise — and then cost it a second day at a
different scale: 176.1 MB across 26 surfaces, a 3.6 GB journal, and a read-only `just
runs` that needed 5.7 GB of RSS on a shared box.

Both journeys here are that path driven with nothing standing in: the real `codex`
really loses a turn, the real filter reads the transcript it left, and the real published
`channel serve` queues what the filter raised. The surface is then read where a manager
reads one — out of `runs/<run-id>/channel/queue.json`. All this journey provides is an
empty directory named after a run; the published verb creates the channel inside it.

They differ in the one thing the filter branches on. The first transcript records the
refusal that ended the turn, so the failure is *provable* and the surface names its cause
and identity. The second is the same real transcript with those frames removed — the
shape all 26 of this host's oversized surfaces had, `status: completed` and `error: null`
— where nothing is provable, and what has to arrive is **nothing at all**: the monitor's
prose raises no surface now, so an unprovable transcript is content like any other, the
member is answered and lives, and the channel is never opened.

Whether the *shape* the filter reads is still the shape the producer writes is a
different question, asked of a declaration rather than of a journey, and
`tests/test_lost_turn_wire_contract.py` is where it is asked. This is the half that says
the parts still add up to the line a planner acts on.

It reads the producer this host has installed, which lives outside the workspace and so
outside every `nx.json` key, so it runs in the uncached tier.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, TypedDict, cast

import lost_turn_producer
import pytest
from lost_turn_producer import PRODUCER, LostTurn
from test_orchestrate_launch_e2e import SURFACE_KIND_OF_A_LOST_TURN
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_checkouts

#: The filter this journey drives, at the interface `oneagentgraph` spawns it through.
CHANNEL_SERVE = REPO_ROOT / "scripts" / "channel-serve.py"

#: The seam that would let something other than the pinned `onepipeline` answer the
#: planner. Cleared rather than set: the published channel is the point of the journey.
ONEPIPELINE_BIN = "ONEPIPELINE_BIN"

#: The run each journey works against, which the filter reads out of the composed task
#: and renders back into the command it points a planner at. One per journey, because the
#: published verb creates a durable channel under the run and two journeys queueing into
#: one would each read the other's surface.
RUN = "wire-contract-e2e"
RUN_OF_AN_UNPROVEN_TRANSCRIPT = "wire-contract-unproven-e2e"

#: How long the filter may take to answer an unprovable transcript on its own. It opens
#: no channel at all for one, so what this really bounds is the regression: a filter that
#: raised a surface instead would sit inside `channel serve` waiting out its whole reply
#: window, and that wait is what the failure would look like.
UNRAISED_SECONDS = 30


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
    home = lost_turn_producer.unreachable_home(tmp_path_factory.mktemp("codex-home"))
    return lost_turn_producer.capture(
        oneharness_bin, codex_bin, home, tmp_path_factory.mktemp("lost-turn")
    )


class QueuedSurface(TypedDict):
    """One planner surface as the run's own channel queue holds it.

    The three fields `scripts/channel-serve.py` composes and a manager reads, named
    rather than left as a bare mapping: what a queued surface *is* is the contract these
    journeys are about. `channel serve` adds bookkeeping of its own — an id, a source —
    which is the published verb's to declare and no part of what is asserted here.
    """

    kind: str
    message: str
    blocking: bool


def _queued(queue: Path, serving: subprocess.Popen[str]) -> list[dict[str, Any]]:
    """The surfaces the run's own channel is holding, once the filter has raised one.

    Polled rather than waited on, because `channel serve` queues the surface and then
    keeps running to wait for a planner. A half-written queue is read as no queue: the
    published verb is writing this file, so a partial read is a race and not an answer.
    """
    limit = deadline(60)
    while True:
        assert serving.poll() is None, (
            f"the filter exited before raising a surface: {serving.communicate()}"
        )
        assert time.monotonic() < limit, f"no surface reached {queue} within the wait"
        try:
            waiting = json.loads(queue.read_text(encoding="utf-8"))["waiting"]
        except (OSError, json.JSONDecodeError, KeyError):
            waiting = []
        if waiting:
            return list(waiting)
        time.sleep(0.05)


def _raised_to_a_real_channel(tmp_path: Path, run: str, said: str) -> QueuedSurface:
    """The one surface the real filter queued on run `run`'s own real channel.

    The whole round trip, shared by both journeys because the only thing that differs
    between them is what the monitor's last message is: the real filter runs at the
    interface `oneagentgraph` spawns it through, the pinned `onepipeline` serves the
    channel, and the surface is read back out of `queue.json` where a manager reads one.
    """
    runs = tmp_path / "runs"
    (runs / run).mkdir(parents=True)
    environment = dict(os.environ)
    environment["ONEPIPELINE_RUNS_DIR"] = str(runs)
    environment.pop(ONEPIPELINE_BIN, None)
    frame = {
        "op": "supervisor",
        "task": f"onepipeline run `{run}`.\n\nGoal: prove the transcript surface",
        "messages": [{"role": "assistant", "content": said}],
    }

    serving = subprocess.Popen(
        [str(CHANNEL_SERVE)],
        cwd=REPO_ROOT,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert serving.stdin is not None
        serving.stdin.write(json.dumps(frame))
        serving.stdin.close()
        queued = _queued(runs / run / "channel" / "queue.json", serving)
    finally:
        # The surface is raised non-blocking and nobody here is the planner, so `channel
        # serve` is now waiting out its own reply window. What it does at the end of one
        # is another journey's subject; these have what they came to read.
        serving.terminate()
        serving.wait(timeout=e2e_timeout(30))

    assert len(queued) == 1, queued
    # The queue is JSON off disk, so the three fields are checked by the assertions each
    # journey makes rather than by the decoder; the cast names what the published verb
    # queued so the journeys read it as a surface instead of as a mapping.
    return cast(QueuedSurface, queued[0])


def _answered_without_the_channel(
    runs: Path, run: str, said: str
) -> subprocess.CompletedProcess[str]:
    """Put one frame to the real filter and wait for it to answer without a channel.

    For the case that raises nothing: the filter is expected to decide and exit without
    ever opening `channel serve`. A `TimeoutExpired` here is therefore a finding rather
    than a flake — it says the filter went to the channel and is waiting out a reply
    window nobody will answer — so it is re-raised as that.
    """
    environment = dict(os.environ)
    environment["ONEPIPELINE_RUNS_DIR"] = str(runs)
    environment.pop(ONEPIPELINE_BIN, None)
    frame = {
        "op": "supervisor",
        "task": f"onepipeline run `{run}`.\n\nGoal: prove an unprovable transcript raises none",
        "messages": [{"role": "assistant", "content": said}],
    }
    try:
        return subprocess.run(
            [str(CHANNEL_SERVE)],
            cwd=REPO_ROOT,
            env=environment,
            input=json.dumps(frame),
            text=True,
            capture_output=True,
            timeout=e2e_timeout(UNRAISED_SECONDS),
            check=False,
        )
    except subprocess.TimeoutExpired as waited:
        raise AssertionError(
            f"the filter did not answer run {run} on its own within the wait, which is "
            "what raising a surface looks like from here: `channel serve` queues it and "
            "then blocks for a planner who is not coming"
        ) from waited


def test_the_channel_filter_raises_a_real_lost_turn_to_a_real_planner_channel(
    tmp_path: Path, lost_turn: LostTurn
) -> None:
    """One lost turn reaches the planner as a line, under its own kind, non-blocking.

    The transcript stays out of the message, which is the whole fix: a planner may not
    filter the unread-surface line, so a surface that cannot be read and cannot be
    dropped is the worst of both. What must arrive instead is the two things a planner
    acts on — what the failure was, and which identity's quota it happened on — and both
    are asserted against what this turn's own frames recorded rather than against
    anything the filter says about itself.
    """
    raised = _raised_to_a_real_channel(tmp_path, RUN, lost_turn.transcript)

    assert raised["kind"] == SURFACE_KIND_OF_A_LOST_TURN, raised
    assert raised["blocking"] is False, raised
    recorded = lost_turn_producer.classification_recorded_by(lost_turn.frames)
    assert len(recorded) == 1, f"this turn recorded {recorded or 'no'} classification(s)"

    assert raised["message"] == (
        f"monitor turn failed: {recorded.pop()} on {PRODUCER}. It said nothing, so there "
        f"is nothing to answer; its {len(lost_turn.transcript)}-character transcript is "
        f"not repeated here. Read it with `just monitor {RUN} --filter monitor`."
    ), raised["message"]


def test_the_channel_filter_raises_nothing_for_a_real_transcript_that_proves_no_failure(
    tmp_path: Path, lost_turn: LostTurn
) -> None:
    """A real transcript nothing can be proven inside raises no surface at all.

    This is the shape that actually filled this channel: all 26 of the oversized surfaces
    measured on this host were `status: completed` with `error: null`, so `lost_turn_error`
    proved nothing about any of them and every one was republished as the monitor's own
    words — 176.1 MB of protocol carrying zero model-authored characters. The bounded
    `monitor-transcript` line that used to stand in its place existed only to keep that
    republication readable, and it went with the republication: prose raises no surface,
    so an unprovable transcript is content the member produced. What must happen is that
    the channel is never opened, the member is answered with a ruling onejudge can act
    on, and nothing at all is queued for a planner to read.

    The transcript is the real producer's own bytes with the two frames that record the
    refusal removed, which is the whole of the difference between the two shapes — a turn
    that completes needs a reachable provider, which this cannot have offline. So the
    journey above and this one differ in exactly what the filter branches on.
    """
    unproven = lost_turn_producer.without_the_frames_that_prove_the_loss(lost_turn.transcript)
    assert unproven != lost_turn.transcript, "the real transcript proved no failure to remove"
    runs = tmp_path / "runs"
    queue = runs / RUN_OF_AN_UNPROVEN_TRANSCRIPT / "channel" / "queue.json"
    (runs / RUN_OF_AN_UNPROVEN_TRANSCRIPT).mkdir(parents=True)

    answered = _answered_without_the_channel(runs, RUN_OF_AN_UNPROVEN_TRANSCRIPT, unproven)

    assert answered.returncode == 0, answered.stderr
    assert not queue.exists(), (
        "an unprovable machine transcript still reached the planner's queue: "
        f"{queue.read_text(encoding='utf-8')}"
    )
    ruling = json.loads(answered.stdout)
    assert ruling["completion"] is False, (
        f"an unprovable transcript answered onejudge with a completion, which settles a "
        f"watch nobody ruled on: {ruling}"
    )
    assert unproven not in ruling["message"], (
        f"the transcript came back to the monitor inside its own ruling: {ruling}"
    )
