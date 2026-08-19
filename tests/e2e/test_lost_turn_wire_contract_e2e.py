"""A monitor turn the real producer lost, raised to a real planner channel as one line.

`scripts/channel-serve.py` is the judge side of `graphs/dag-scope.yaml`'s monitor: it
takes onejudge's supervisor frame on stdin and raises what the monitor said as a surface
through `onepipeline channel serve`. When the monitor's agent side *loses* the turn, what
it "said" is the harness's own JSON-RPC transcript, and raising that verbatim is what
cost this host its supervisory layer for a day — twenty-one thousand characters a planner
could neither read nor drop, twelve hundred of them discarded as noise.

So this drives the whole path with nothing standing in: the real `codex` really loses a
turn, the real filter reads the transcript it left, and the real published `channel
serve` queues what the filter raised. The surface is then read where a manager reads one
— out of `runs/<run-id>/channel/queue.json`. All this journey provides is an empty
directory named after a run; the published verb creates the channel inside it.

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
from typing import Any

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

#: The run the surface is raised on, which the filter reads out of the composed task and
#: renders back into the command it points a planner at.
RUN = "wire-contract-e2e"


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
    runs = tmp_path / "runs"
    (runs / RUN).mkdir(parents=True)
    environment = dict(os.environ)
    environment["ONEPIPELINE_RUNS_DIR"] = str(runs)
    environment.pop(ONEPIPELINE_BIN, None)
    frame = {
        "op": "supervisor",
        "task": f"onepipeline run `{RUN}`.\n\nGoal: prove the lost-turn surface",
        "messages": [{"role": "assistant", "content": lost_turn.transcript}],
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
        queued = _queued(runs / RUN / "channel" / "queue.json", serving)
    finally:
        # The surface is raised non-blocking and nobody here is the planner, so `channel
        # serve` is now waiting out its own reply window. What it does at the end of one
        # is another journey's subject; this one has what it came to read.
        serving.terminate()
        serving.wait(timeout=e2e_timeout(30))

    assert len(queued) == 1, queued
    assert queued[0]["kind"] == SURFACE_KIND_OF_A_LOST_TURN, queued[0]
    assert queued[0]["blocking"] is False, queued[0]
    recorded = lost_turn_producer.classification_recorded_by(lost_turn.frames)
    assert len(recorded) == 1, f"this turn recorded {recorded or 'no'} classification(s)"

    assert queued[0]["message"] == (
        f"monitor turn failed: {recorded.pop()} on {PRODUCER}. It said nothing, so there "
        f"is nothing to answer; its {len(lost_turn.transcript)}-character transcript is "
        f"not repeated here. Read it with `just monitor {RUN} --filter monitor`."
    ), queued[0]["message"]
