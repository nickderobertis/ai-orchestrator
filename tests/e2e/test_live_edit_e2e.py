"""Real-CLI journey for versioned live graph edits and atomic replay."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

from orchestrator import BASE_CONFIG, REPO_ROOT
from orchestrator.channel import create_channel
from orchestrator.projection import project_run
from orchestrator.runs import RunId

FAKE_BACKEND = REPO_ROOT / "tests" / "e2e" / "fake_backend.py"


def _wait_for(path: Path, predicate, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and predicate(path.read_text(encoding="utf-8")):
            return
        time.sleep(0.02)
    raise AssertionError(f"condition did not appear in {path}")


def _reply(run_id: str, runs: Path, commands: list[dict[str, object]]) -> None:
    subprocess.run(
        ["just", "channel-reply", run_id, "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        input=json.dumps({"version": 1, "commands": commands}),
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )


def test_real_cli_mutates_live_frontier_and_replays_atomic_edits(
    tmp_path: Path, onejudge_bin: str
) -> None:
    runs = tmp_path / "runs"
    run_id = "live-edit"
    run_dir = runs / run_id
    channel = create_channel(run_dir)
    base = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    base["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    base_path = tmp_path / "base.yaml"
    base_path.write_text(yaml.safe_dump(base), encoding="utf-8")
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "concurrency": 4,
                "tasks": [
                    {
                        "id": "slow_a",
                        "persona": "engineer",
                        "task": f"slow-branch {tmp_path / 'a.ticks'}",
                    },
                    {
                        "id": "slow_b",
                        "persona": "engineer",
                        "task": f"slow-branch {tmp_path / 'b.ticks'}",
                    },
                    {"id": "failed", "persona": "engineer", "task": "should-fail", "max_turns": 1},
                    {"id": "approve", "kind": "human", "task": "Approve"},
                    {
                        "id": "pending",
                        "task": "No diff",
                        "expects_no_diff": True,
                        "deps": ["slow_a"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "AI_ORCHESTRATOR_CHANNEL_DIR": str(channel),
        "AI_ORCHESTRATOR_CHANNEL_RUN_ID": run_id,
    }
    process = subprocess.Popen(
        [
            "just",
            "run-plan",
            str(plan),
            "--run",
            run_id,
            "--runs-dir",
            str(runs),
            "--base",
            str(base_path),
            "--onejudge-bin",
            onejudge_bin,
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    events = run_dir / "events.jsonl"
    _wait_for(
        events,
        lambda text: text.count('"kind": "node-started"') >= 3
        and '"kind": "human-waiting"' in text,
    )

    _reply(run_id, runs, [{"op": "reparent", "id": "pending", "deps": ["pending"]}])
    messages: list[str] = []
    for _ in range(4):
        rejected = subprocess.run(
            ["just", "channel-next", run_id, "--runs-dir", str(runs), "--timeout", "10"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        messages.append(json.loads(rejected.stdout)["surface"]["message"])
        if any("depends on itself" in message for message in messages):
            break
    assert any("depends on itself" in message for message in messages)
    before = events.read_text(encoding="utf-8").count('"kind": "edit-committed"')

    _reply(
        run_id,
        runs,
        [
            {
                "op": "add",
                "node": {
                    "id": "added",
                    "task": "No diff",
                    "expects_no_diff": True,
                    "deps": ["slow_a"],
                },
            },
            {"op": "reparent", "id": "pending", "deps": ["slow_b"]},
            {"op": "drop", "id": "slow_b", "dependents": "detach"},
            {"op": "attest", "ref": "approve"},
        ],
    )
    _wait_for(events, lambda text: text.count('"kind": "edit-committed"') >= before + 4)
    _wait_for(events, lambda text: '"kind": "node-failed"' in text and '"node": "failed"' in text)
    _reply(
        run_id,
        runs,
        [
            {
                "op": "retry",
                "id": "failed",
                "node": {"id": "retry", "task": "No diff", "expects_no_diff": True},
            }
        ],
    )
    _reply(run_id, runs, [{"op": "complete", "reason": "planner verified publication anchors"}])

    stdout, stderr = process.communicate(timeout=20)
    assert process.returncode == 1, stderr
    payload = json.loads(stdout)
    assert payload["results"]["added"]["status"] == "done"
    assert payload["results"]["pending"]["status"] == "done"
    assert payload["results"]["retry"]["status"] == "done"
    assert "slow_b" not in payload["results"]
    projection = project_run(events, RunId(run_id), 1)
    assert {node["id"] for node in projection.plan["tasks"]} == set(payload["results"])
    committed = [
        line for line in events.read_text().splitlines() if '"kind": "edit-committed"' in line
    ]
    reparent = next(json.loads(line) for line in committed if '"reparent"' in line)
    assert [operation["kind"] for operation in reparent["detail"]["operations"]] == [
        "edge-removed",
        "edge-added",
        "reparent",
    ]
