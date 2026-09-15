"""Two things the adopted engine does that the one before it did not, observed on a real run.

Both are engine-side landings of the root-causes plan, and both are the kind of repair
that reads as absent from every pin: an engine without them settles a run and answers
a read exactly as loudly as one with them. `tests/test_adopted_engine_carries_this_plan.py`
holds that the adopted release's history *contains* each landing; what these journeys
hold is that the installed engine *does* what the landing did, on a run launched through
the real recipe.

* **A settled node is no longer parked**
  (https://github.com/nickderobertis/onepipeline/pull/229). A `cancel` parks a node;
  before that landing a `settle` recorded the node's outcome and left the park standing,
  so a run every node of which had reached a settled outcome reported itself unfinished,
  with something idle that nobody had decided on — and managers retried settled work to
  get the run to end.
* **A read of the channel is accounted for in the surface's own log**
  (https://github.com/nickderobertis/onepipeline/pull/227). The queue is a projection of
  `channel/surfaces.jsonl`; before that landing the log recorded a surface being queued,
  abandoned and attended, and never a manager *claiming* one, so a read of the channel
  was the one transition the record could not explain — which is the lost-update hazard
  a read of the channel landing over a worker's write used to be.

The run is a human gate with one agent node behind it, launched with `--dag-graph off` so
nothing but these journeys raises a surface on its channel, and nothing is ever
dispatched — the gate is never attested through, and the node behind it is settled by
hand — so it costs a launch and no provider turn. Everything is real: the recipes, the
`onepipeline` beneath them, and the run's own records.
"""

# The finding these answer is about which Nx project owns this file. It sits beside
# `tests/e2e/test_orchestrate_launch_e2e.py`, in the code-keyed tier every launch of the
# installed engine in this repository is in; what it depends on — the recipes, the
# engine, the run's own records — is the whole of that key, so no narrower edge exists
# to put it behind, and a project of its own for two launches would split a suite whose
# fixtures and stand-ins it shares.
# llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] see above
# llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] see above

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NamedTuple, TypeVar, cast

import pytest
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from project_fixtures import project_from_plan
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: A launching session these journeys state rather than inherit: this suite runs inside
#: a dispatch whose own harness session would otherwise own the runs.
LAUNCHING_SESSION = "e2e-settles-and-logs"

#: Every name a launcher identity reaches `scripts/onepipeline.sh` through, so a
#: journey that states one is not also carrying the enclosing dispatch's.
INHERITED_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    "ONEPIPELINE_RUN_ID",
)

GATE_NODE = "gate"
WORK_NODE = "work"

#: How long a journey waits for the run to record something.
PATIENCE_SECONDS = 60

#: `tests/e2e/nx_workspace.py`'s group: every step here is a `just` recipe blocking on
#: `uv run`, which waits on the lock a journey re-provisioning this checkout holds.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

Found = TypeVar("Found")


class HeldRun(NamedTuple):
    """A live run whose frontier is a human gate, and what every verb needs to see it."""

    environment: dict[str, str]
    run: str
    root: Path


def _just(
    *arguments: str, environment: dict[str, str], seconds: float = 180, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run one real recipe from this checkout."""
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _task(what: str) -> str:
    return f"## What\n{what}\n\n## Why\nHold the run.\n\n## Acceptance criteria\n- Done."


@pytest.fixture
def held(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[HeldRun]:
    """Launch the gate-and-work plan, and stop the run however the journey ends."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    named = re.sub(r"[^A-Za-z0-9]+", "-", request.node.name)[-40:].strip("-")
    run = f"settles-and-logs-{os.getpid()}-{named}"
    environment = dict(os.environ)
    for name in INHERITED_ENVIRONMENT:
        environment.pop(name, None)
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "goal": {"text": "hold a run behind a gate, then settle behind it"},
                "name": run,
                "tasks": [
                    {"id": GATE_NODE, "kind": "human", "task": _task("Approve.")},
                    {
                        "id": WORK_NODE,
                        "persona": "engineer",
                        "deps": [GATE_NODE],
                        "task": _task("Report."),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    launch = _just(
        "orchestrate",
        project_from_plan(plan),
        "--dag-graph",
        "off",
        environment=environment,
        seconds=600,
    )
    assert launch.returncode == 0, f"the launch failed:\n{launch.stdout}\n{launch.stderr}"
    try:
        yield HeldRun(environment, run, tmp_path / "runs" / run)
    finally:
        _just("stop", run, environment=environment, seconds=60)


def _waited_for(what: str, look: Callable[[], Found | None]) -> Found:
    """Poll `look` until it answers something, or fail naming what never arrived."""
    limit = deadline(PATIENCE_SECONDS)
    while True:
        found = look()
        if found is not None:
            return found
        assert time.monotonic() < limit, f"{what} never arrived within the wait"
        time.sleep(0.2)


def _reply(held: HeldRun, *commands: dict[str, Any]) -> int:
    """Send one edit envelope over the live channel, as a manager types it, and read its receipt.

    The receipt is the bus's transport answer — `{queue, position, id}` — and says where
    the envelope went, not that the graph took it: a run handed back at its gate has
    nothing driving it, so the edit waits on the run's `commands` queue until a driver
    attached with `just orchestrate --adopt` drains it. The id is what the engine's answer
    on `command-outcomes` is keyed on.
    """
    sent = _just(
        "channel-reply",
        held.run,
        environment=held.environment,
        seconds=120,
        stdin=json.dumps({"version": 2, "commands": list(commands)}),
    )
    assert sent.returncode == 0, sent.stderr + sent.stdout
    printed = sent.stdout.splitlines()
    assert len(printed) == 1, f"the bus's answer is not the whole of stdout:\n{sent.stdout}"
    # `cast` rather than a validating read: the bus owns this shape, and the members read
    # here are the ones asserted.
    receipt = cast(dict[str, Any], json.loads(printed[0]))
    assert receipt.get("queue") == "commands", (
        f"the edit was not sent to the run's command queue: {sent.stdout}\n{sent.stderr}"
    )
    assert "state" not in receipt, f"a transport receipt claimed to have applied: {receipt}"
    return int(receipt["id"])


def _adopted(held: HeldRun, *, seconds: float = 300) -> None:
    """Attach a driver to the handed-back run, once the launch's own has let go, and let it drain.

    `just orchestrate --adopt` is for a run nothing is driving and refuses one that still
    is, so only that refusal is retried; the adopted driver reconciles the queued edits in
    the order they were sent and returns once the run has settled.
    """
    limit = deadline(seconds)
    refused = ""
    while time.monotonic() < limit:
        adopted = _just("orchestrate", "--adopt", held.run, environment=held.environment)
        if adopted.returncode == 0:
            return
        refused = adopted.stdout + adopted.stderr
        assert "is still being driven" in refused, f"the adoption failed:\n{refused}"
        time.sleep(1.0)
    raise AssertionError(f"run {held.run} was still being driven after {seconds}s:\n{refused}")


def _outcome(held: HeldRun, envelope_id: int) -> dict[str, Any]:
    """The engine's answer to the command envelope `envelope_id`, read through the bus."""
    streamed = subprocess.run(
        [
            str(REPO_ROOT / ".venv" / "bin" / "onemessagebus"),
            "subscribe",
            "command-outcomes",
            "--until",
            json.dumps({"field": "id", "equals": envelope_id}),
            "--timeout",
            "120",
            "--config",
            str(REPO_ROOT / "config" / "onemessagebus.yaml"),
            "--transport-dir",
            str(held.root / "channel"),
        ],
        cwd=REPO_ROOT,
        env=held.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )
    assert streamed.returncode == 0, f"no outcome for envelope {envelope_id}:\n{streamed.stderr}"
    # The last line is the one `--until` admitted. `cast` rather than a validating read:
    # the record is the engine's own command outcome, and the journey asserts `applied`.
    return cast(dict[str, Any], json.loads(streamed.stdout.splitlines()[-1])["record"])


def _results(held: HeldRun) -> str | None:
    """`just results` for the run, once it reports the run complete.

    The views rather than `result.json`: that document is what the driver wrote when it
    handed the run back at its human gate, and a run whose edits a driver reconciled
    after that is folded fresh by every view.
    """
    results = _just("results", held.run, environment=held.environment, seconds=60)
    if results.returncode != 0 or f"{held.run}  complete" not in results.stdout:
        return None
    return results.stdout


def test_a_node_settled_after_a_park_ends_the_park_and_the_run_reports_itself_complete(
    held: HeldRun,
) -> None:
    """Park the work, open its gate, settle it from evidence: the run ends complete.

    Under the engine before the landing the park outlived the settlement — the node read
    `done` and stayed parked — so the run never settled, and `just status` went on
    reporting a node idle for a decision nobody had made.

    The launch hands the run back at its gate, so the three edits are sent to a run
    nothing is driving: each waits on the command queue, and the driver `--adopt` attaches
    drains them in order — the park first, so opening the gate dispatches nothing.
    """
    sent = [
        _reply(
            held,
            {
                "op": "cancel",
                "id": WORK_NODE,
                "reason": "the report this node would write is already on the issue",
            },
        ),
        # The gate opens, so the parked node is the only thing between the run and its end.
        _reply(held, {"op": "attest", "ref": GATE_NODE}),
        _reply(
            held,
            {
                "op": "settle",
                "id": WORK_NODE,
                "outcome": "done",
                "evidence": "the report is on the issue; nothing is left for a dispatch to do",
            },
        ),
    ]
    assert len(set(sent)) == len(sent), f"the bus answered one id for several envelopes: {sent}"

    _adopted(held)

    for envelope_id in sent:
        took = _outcome(held, envelope_id)
        assert took["applied"] is True, f"the adopted driver did not apply the edit: {took}"

    results = _waited_for("the run to report itself complete", lambda: _results(held))
    assert f"{WORK_NODE}" in results and "done (settled-from-evidence)" in results, results
    assert "parked" not in results.lower(), results
    # Settled from evidence, never dispatched for it: the park ended without sending the
    # node back to the frontier for a redispatch of work already done. `just transcript`
    # is how an operator asks what a dispatch left behind, and it refuses a node the run
    # holds no dispatch record for by name — naming which nodes it does hold, which for
    # a run that dispatched nothing is none.
    transcript = _just("transcript", held.run, WORK_NODE, environment=held.environment)
    assert transcript.returncode != 0, (
        f"the run has a transcript for {WORK_NODE!r}, so it was dispatched:\n{transcript.stdout}"
    )
    assert f"recorded nothing for node '{WORK_NODE}'" in transcript.stderr, transcript.stderr
    assert "it has records for: nothing yet" in transcript.stderr, transcript.stderr
    status = _just("status", held.run, environment=held.environment)
    assert status.returncode == 0, status.stderr
    assert "SETTLED  2/2 done" in status.stdout, status.stdout
    assert "parked" not in status.stdout.lower(), status.stdout


# llmlint: ignore-block[tests_mirror_real_usage] The log is the record, and the record is
# the subject: the journey below reads the channel the one way a manager does, `just
# channel-next`, and what it holds is that the read is accounted for in the run's own
# surface log — which the queue every view answers from is a projection of, and which no
# view renders. There is nothing between the read and the record to observe it through.
def _surface_log(held: HeldRun) -> list[dict[str, Any]]:
    """Every line of the run's surface log, which the queue is a projection of."""
    log = held.root / "channel" / "surfaces.jsonl"
    if not log.is_file():
        return []
    return [
        cast(dict[str, Any], json.loads(line))
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# llmlint: ignore-end[tests_mirror_real_usage]


def test_reading_a_surface_leaves_the_surface_log_accounting_for_the_read(
    held: HeldRun,
) -> None:
    """A finding is raised, a manager reads it, and the log says it was claimed.

    The read is `just channel-next`, which is the one way a manager consumes a surface.
    Under the engine before the landing the log held the surface being queued and
    nothing about the read: the queue's projection moved and the record did not.
    """
    raised = subprocess.run(
        [
            str(REPO_ROOT / ".venv" / "bin" / "onepipeline"),
            "surface",
            held.run,
            "--kind",
            "finding",
        ],
        cwd=REPO_ROOT,
        env=held.environment,
        input="issue: the gate has been waiting on a person for an hour",
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert raised.returncode == 0, raised.stderr + raised.stdout
    queued = _waited_for(
        "the finding to reach the log",
        lambda: next((line for line in _surface_log(held) if line.get("event") == "queued"), None),
    )
    surface_id = queued["id"]

    read = _just("channel-next", held.run, environment=held.environment, seconds=60)
    assert read.returncode == 0, read.stderr
    handed = cast(dict[str, Any], json.loads(read.stdout)).get("surface")
    assert isinstance(handed, dict) and handed.get("id") == surface_id, read.stdout

    claimed = _waited_for(
        "the read to reach the log",
        lambda: next(
            (
                line
                for line in _surface_log(held)
                if line.get("event") == "claimed" and line.get("id") == surface_id
            ),
            None,
        ),
    )
    assert claimed["kind"] == "finding", claimed
    # And the record explains the whole life of the surface on its own, in order: it
    # was queued, and then a reader claimed it — nothing the queue shows is unaccounted.
    events = [line.get("event") for line in _surface_log(held) if line.get("id") == surface_id]
    assert events[:2] == ["queued", "claimed"], events
