"""E2E: telemetry CLI over real journal/history files and its subprocess boundary."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from orchestrator import REPO_ROOT
from orchestrator.journal import open_journal
from orchestrator.runs import NodeId, RunId, prepare_round, write_result

FAKE_ONEHARNESS = REPO_ROOT / "tests" / "e2e" / "fake_oneharness.py"


def test_breakdown_aggregates_real_multirole_history_records(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "telemetry-e2e"
    _, round_dir = prepare_round(run_dir, {"tasks": [{"id": "api", "task": "ship"}]})
    journal = open_journal(run_dir, RunId("telemetry-e2e"), 1)
    journal.append("round-started", detail={"nodes": 1})
    journal.append("node-started", node=NodeId("api"))
    time.sleep(0.04)
    journal.append("node-settled", node=NodeId("api"), detail={"status": "done"})
    journal.append("round-finished", detail={"state": "complete", "ok": True})
    write_result(
        round_dir,
        {
            "ok": True,
            "state": "complete",
            "started_order": ["api"],
            "results": {"api": {"status": "done", "kind": "agent"}},
        },
    )
    active_dir = runs_dir / "active-node"
    prepare_round(active_dir, {"tasks": [{"id": "waiting", "task": "wait"}]})
    active_journal = open_journal(active_dir, RunId("active-node"), 1)
    active_journal.append("round-started", detail={"nodes": 1})
    active_journal.append("node-started", node=NodeId("waiting"))
    time.sleep(0.02)

    sessions = []
    for role, model_ms, tool_ms, tokens, cost in (
        ("agent", 12, 5, 10, 0.02),
        ("judge", 8, 0, 3, 0.01),
    ):
        history = tmp_path / f"{role}.jsonl"
        history.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "duration_ms": model_ms + tool_ms,
                    "model_ms": model_ms,
                    "tool_ms": tool_ms,
                    "usage": {
                        "input_tokens": tokens,
                        "output_tokens": 2,
                        "cache_read_tokens": 1,
                        "cache_write_tokens": 0,
                        "cost_usd": cost,
                    },
                    "events": [
                        {
                            "kind": "tool_call",
                            "name": "command_execution",
                            "duration_ms": tool_ms,
                            "input": {"command": "just gate"},
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        sessions.append(
            {
                "id": f"{role}-history",
                "name": role,
                "project": str(tmp_path),
                "started": "2026-07-19T00:00:00Z",
                "path": str(history),
                "labels": {"run_id": "telemetry-e2e", "node": "api", "role": role},
            }
        )
    store = tmp_path / "store.json"
    store.write_text(json.dumps({"sessions": sessions}), encoding="utf-8")
    oneharness = tmp_path / "oneharness"
    oneharness.write_text(
        "#!/usr/bin/env python3\n" + FAKE_ONEHARNESS.read_text(encoding="utf-8"), encoding="utf-8"
    )
    oneharness.chmod(0o755)
    environment = {**os.environ, "FAKE_ONEHARNESS_STORE": str(store)}
    command = subprocess.run(
        [
            "just",
            "telemetry",
            "--runs-dir",
            str(runs_dir),
            "--all",
            "--oneharness-bin",
            str(oneharness),
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert command.returncode == 0, command.stderr
    indexed = {run["run_id"]: run for run in json.loads(command.stdout)["runs"]}
    run = indexed["telemetry-e2e"]
    assert run["timing"]["agent_model_ms"] == 12
    assert run["timing"]["judge_model_ms"] == 8
    assert run["timing"]["tool_ms"] == 5
    assert run["usage"]["total"]["input_tokens"] == 13
    assert run["usage"]["total"]["cost_usd"] == 0.03
    assert [link["role"] for link in run["nodes"][0]["sessions"]] == ["agent", "judge"]
    assert run["nodes"][0]["tool_commands"] == {"gate": 2}
    assert run["node_work_ms"]["wall_ms"] > 0
    assert run["telemetry_quality"] == "complete"
    assert run["sources"] == ["oneharness", "history_legacy", "journal_legacy"]
    assert indexed["active-node"]["nodes"][0]["timing"]["wall_ms"] > 0

    breakdown = subprocess.run(
        [*command.args, "--breakdown"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert breakdown.returncode == 0, breakdown.stderr
    assert "telemetry-e2e" in breakdown.stdout
    assert "2=1" in breakdown.stdout
