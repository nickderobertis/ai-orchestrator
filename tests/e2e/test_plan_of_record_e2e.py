"""E2E: every transition into a new round folds the graph the last round executed.

`next-round` folded the run's own journal; `just run-plan <plan> --run <id>` did not,
and that is the command an orchestrator reaches for to reclaim a run it is already
driving. Rounds 2 and 3 of `dag-observatory-ux-2` were launched that way from the
untouched round-1 launch file, so nodes whose work had already merged were dispatched
again and every accepted live edit — a `retry`'s replacement id above all — was gone.

Only onejudge's paid model provider is replaced, by the command-provider double in
`fake_backend.py`; the CLI, the ledger, the journal, the channel and the fold are real.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from rendezvous import Rendezvous
from waits import deadline as e2e_deadline
from waits import timeout as e2e_timeout

from orchestrator import REPO_ROOT
from orchestrator.channel import CHANNEL_DIR_ENV, CHANNEL_RUN_ID_ENV, create_channel
from orchestrator.plan import PLAN_SCHEMA_VERSION

#: A dispatch's whole life, from launch to a settled recorded round.
ROUND_TIMEOUT = 180

#: A cross-DAG watch on a run that is not active, so it never resolves and the node
#: holding it is blocked in every round — which is what makes it observable that the
#: transition carried the reference rather than removing it.
EXTERNAL_DEPENDENCY = "run:no-such-upstream#publish"


def _wait_for_node_event(events: Path, kind: str, node: str, seconds: float = 120) -> None:
    """Block until one journaled record carries both ``kind`` and ``node``.

    Matched within one record rather than as two substrings of the whole log: a
    node's own `node-started` line already carries its id, so pairing that with a
    bare kind search is satisfied by any other node's event of that kind. Only an
    unterminated trailing fragment is skipped — the journal appends whole lines, so
    that is the one thing a poll of a live log may drop and every other record still
    has to parse.
    """
    guard = e2e_deadline(seconds)
    while time.monotonic() < guard:
        text = events.read_text(encoding="utf-8") if events.is_file() else ""
        lines = text.splitlines()
        if lines and not text.endswith("\n"):
            lines.pop()
        if any(
            record.get("kind") == kind and record.get("node") == node
            for record in (json.loads(line) for line in lines if line.strip())
        ):
            return
        time.sleep(0.02)
    raise AssertionError(f"no {kind!r} event for {node!r} appeared in {events}")


def _round(run_dir: Path, number: int, name: str) -> dict:
    return json.loads((run_dir / f"round-{number:02d}" / name).read_text(encoding="utf-8"))


def test_rerunning_the_launch_file_folds_the_plan_of_record_into_each_new_round(
    tmp_path: Path, command_base, onejudge_bin: str
) -> None:
    """Two transitions through `just run-plan`, the second out of a cancelled round.

    Round 1 lands one node whose work is finished and retries another live, giving the
    replacement an id that exists only in the executed graph. Every later round is
    started by handing `run-plan` the same launch file again — exactly what re-dispatched
    merged work on the real run — and each one must run the folded graph instead: the
    finished nodes carried out, the superseded original gone, the replacement scheduled
    under its own id. Round 2 ends by budget cancellation, so round 3 proves the fold
    survives the transition the defect report named.
    """
    runs = tmp_path / "runs"
    run_id = "plan-of-record"
    run_dir = runs / run_id
    channel = create_channel(run_dir)
    events = run_dir / "events.jsonl"
    # Holds round 1 open while the planner's edits are submitted against a live
    # frontier, then completes: a real dispatched node whose work is done and which
    # no later round may run again.
    holder = Rendezvous.at(tmp_path, "holder")
    # The replacement's own wedge. Released for round 1 so the retry settles and the
    # round can end; removed for round 2 so the same carried-forward node is provably
    # still in flight when the budget cancels it, rather than betting that a dispatch
    # outlasts a stopwatch.
    replacement = Rendezvous.at(tmp_path, "replacement")
    replacement.let_go()
    plan = tmp_path / "launch.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": PLAN_SCHEMA_VERSION,
                "name": "plan-of-record",
                "tasks": [
                    {
                        "id": "landed",
                        "kind": "human",
                        "task": "Merge the prepared pull request.",
                    },
                    {
                        "id": "holder",
                        "persona": "engineer",
                        "task": f"complete-now: hold the round open.{holder.sentinels()}",
                    },
                    {
                        "id": "flaky",
                        "persona": "engineer",
                        "task": "should-fail",
                        "max_turns": 1,
                    },
                    {
                        # A watch on a run that does not exist, so it is blocked in
                        # every round and carried forward holding the reference
                        # itself. What it proves is that the transition keeps that
                        # reference: strip it as a satisfied dependency id and this
                        # node stops waiting on an upstream it never saw resolve.
                        "id": "watcher",
                        "task": "Wait for the external producer.",
                        "expects_no_diff": True,
                        "deps": [EXTERNAL_DEPENDENCY],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    common = [
        "--runs-dir",
        str(runs),
        "--base",
        str(command_base()),
        "--onejudge-bin",
        onejudge_bin,
    ]

    first = subprocess.Popen(
        ["just", "run-plan", str(plan), "--run", run_id, *common],
        cwd=REPO_ROOT,
        env={**os.environ, CHANNEL_DIR_ENV: str(channel), CHANNEL_RUN_ID_ENV: run_id},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    holder.wait(e2e_timeout(120))
    _wait_for_node_event(events, "node-failed", "flaky")
    reply = subprocess.run(
        ["just", "channel-reply", run_id, "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        input=json.dumps(
            {
                "version": 1,
                "commands": [
                    {"op": "attest", "ref": "landed"},
                    {
                        "op": "retry",
                        "id": "flaky",
                        "node": {
                            "id": "flaky-again",
                            "persona": "engineer",
                            "max_turns": 1,
                            "task": f"should-fail{replacement.sentinels()}",
                        },
                    },
                ],
            }
        ),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
    )
    assert reply.returncode == 0, reply.stderr
    _wait_for_node_event(events, "node-failed", "flaky-again")
    holder.let_go()
    launched_stdout, launched_stderr = first.communicate(timeout=e2e_timeout(ROUND_TIMEOUT))
    assert first.returncode == 1, (launched_stdout, launched_stderr)

    executed = _round(run_dir, 1, "result.json")["results"]
    assert executed["landed"]["status"] == "done"
    assert executed["holder"]["status"] == "done"
    assert executed["flaky"]["status"] == "failed"
    assert executed["flaky-again"]["status"] == "failed"
    assert executed["watcher"]["status"] == "blocked"

    # The transition the defect report is about: the same launch file, handed back to
    # `run-plan` against the run it already recorded a round for.
    replacement.release.unlink()
    second = subprocess.run(
        ["just", "run-plan", str(plan), "--run", run_id, *common, "--round-budget", "0.4"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(ROUND_TIMEOUT),
    )
    assert second.returncode == 1, (second.stdout, second.stderr)
    # Folded, not re-read: the two finished nodes are carried out rather than
    # dispatched again, and the frontier is the replacement's id — which exists
    # nowhere in the file this round was launched with.
    carried = _round(run_dir, 2, "plan.json")["tasks"]
    assert [task["id"] for task in carried] == ["watcher", "flaky-again"]
    # The watch is not a satisfied dependency id: it names no node of this graph, so
    # the transition keeps it rather than stripping it off the node that holds it.
    assert next(task for task in carried if task["id"] == "watcher")["deps"] == [
        EXTERNAL_DEPENDENCY
    ]
    assert "deriving the next one from the graph that round executed" in second.stderr
    cancelled = _round(run_dir, 2, "result.json")["results"]
    assert set(cancelled) == {"flaky-again", "watcher"}
    assert cancelled["flaky-again"]["status"] == "cancelled"
    assert cancelled["watcher"]["status"] == "blocked"

    # And again out of the round the budget cancelled, which is the transition that
    # bypassed the fold on the real run.
    replacement.let_go()
    third = subprocess.run(
        ["just", "run-plan", str(plan), "--run", run_id, *common],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(ROUND_TIMEOUT),
    )
    assert third.returncode == 1, (third.stdout, third.stderr)
    resumed_plan = _round(run_dir, 3, "plan.json")["tasks"]
    assert [task["id"] for task in resumed_plan] == ["watcher", "flaky-again"]
    # Still held after a second transition, and after one out of a cancelled round.
    assert next(task for task in resumed_plan if task["id"] == "watcher")["deps"] == [
        EXTERNAL_DEPENDENCY
    ]
    resumed = _round(run_dir, 3, "result.json")["results"]
    assert set(resumed) == {"flaky-again", "watcher"}
    assert resumed["flaky-again"]["status"] == "failed"
