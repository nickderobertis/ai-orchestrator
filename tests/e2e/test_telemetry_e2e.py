"""E2E: telemetry CLI over run-plan and a recorded oneharness history store."""

# llmlint: ignore-file[e2e_not_mocked] oneharness' history reader is the subprocess
# boundary under test; fake_oneharness serves the same normalized store because the
# not-yet-released v2 upstream fields cannot be produced by today's real binary.

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from orchestrator import REPO_ROOT

FAKE_ONEHARNESS = REPO_ROOT / "tests" / "e2e" / "fake_oneharness.py"


def test_breakdown_aggregates_real_multirole_history_records(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"tasks": [{"id": "api", "kind": "human", "task": "Approve telemetry."}]}),
        encoding="utf-8",
    )
    planned = subprocess.run(
        ["just", "run-plan", str(plan), "--run", "telemetry-e2e", "--runs-dir", str(runs_dir)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert planned.returncode == 1

    sessions = []
    for role, model_ms, tool_ms, tokens, cost in (
        ("agent", 12, 5, 10, 0.02),
        ("judge", 8, 0, 3, 0.01),
    ):
        history = tmp_path / f"{role}.jsonl"
        history.write_text(
            json.dumps(
                {
                    "schema_version": "0.3",
                    "started_at": "2026-07-19T00:00:00+00:00",
                    "finished_at": "2026-07-19T00:00:00.020000+00:00",
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
                            "tool_call_id": f"{role}-tool-1",
                            "started_at": "2026-07-19T00:00:00+00:00",
                            "finished_at": "2026-07-19T00:00:00.005000+00:00",
                            "duration_ms": tool_ms,
                            "status": "completed",
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
    assert run["nodes"][0]["usage"]["total"]["input_tokens"] == 13
    assert set(run["nodes"][0]["timing"]["fractions"].values()) == {0.0}
    assert run["nodes"][0]["turns"] == 2
    assert run["nodes"][0]["tool_commands"] == {"gate": 2}
    assert set(run["node_work_ms"]) == {"agent_model_ms", "judge_model_ms", "tool_ms", "wall_ms"}
    assert run["telemetry_quality"] == "partial"
    assert run["sources"] == ["oneharness", "history_legacy", "journal_legacy"]

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
    assert "  api" in breakdown.stdout
    assert "2=1" in breakdown.stdout

    judge_record = json.loads((tmp_path / "judge.jsonl").read_text(encoding="utf-8"))
    judge_record["usage"].pop("cache_write_tokens")
    judge_record["usage"]["cost_usd"] = True
    (tmp_path / "judge.jsonl").write_text(json.dumps(judge_record) + "\n", encoding="utf-8")
    unknown = subprocess.run(
        command.args, cwd=REPO_ROOT, env=environment, text=True, capture_output=True, timeout=30
    )
    assert json.loads(unknown.stdout)["runs"][0]["usage"]["total"]["cache_write_tokens"] is None
    assert json.loads(unknown.stdout)["runs"][0]["usage"]["total"]["cost_usd"] is None
    unknown_breakdown = subprocess.run(
        [*command.args, "--breakdown"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert "?" in unknown_breakdown.stdout

    judge_record["events"][0]["tool_call_id"] = ""
    (tmp_path / "judge.jsonl").write_text(json.dumps(judge_record) + "\n", encoding="utf-8")
    malformed_tool = subprocess.run(
        command.args, cwd=REPO_ROOT, env=environment, text=True, capture_output=True, timeout=30
    )
    assert malformed_tool.returncode == 2
    judge_record["events"][0]["tool_call_id"] = "judge-tool-1"
    (tmp_path / "judge.jsonl").write_text(json.dumps(judge_record) + "\n", encoding="utf-8")

    for history in (tmp_path / "agent.jsonl", tmp_path / "judge.jsonl"):
        record = json.loads(history.read_text(encoding="utf-8"))
        record.update({"duration_ms": 20_000, "model_ms": 10_000, "tool_ms": 10_000})
        record["finished_at"] = "2026-07-19T00:00:20+00:00"
        record["events"][0]["duration_ms"] = 10_000
        record["events"][0]["finished_at"] = "2026-07-19T00:00:10+00:00"
        history.write_text(json.dumps(record) + "\n", encoding="utf-8")
    clipped = subprocess.run(
        command.args, cwd=REPO_ROOT, env=environment, text=True, capture_output=True, timeout=30
    )
    clipped_timing = json.loads(clipped.stdout)["runs"][0]["timing"]
    assert clipped_timing["tool_ms"] == clipped_timing["wall_ms"]
    assert clipped_timing["agent_model_ms"] == clipped_timing["judge_model_ms"] == 0

    for history in (tmp_path / "agent.jsonl", tmp_path / "judge.jsonl"):
        record = json.loads(history.read_text(encoding="utf-8"))
        for field in ("schema_version", "model_ms", "tool_ms"):
            record.pop(field)
        for field in ("tool_call_id", "started_at", "finished_at", "duration_ms", "status"):
            record["events"][0].pop(field)
        history.write_text(json.dumps(record) + "\n", encoding="utf-8")
    legacy = subprocess.run(
        command.args, cwd=REPO_ROOT, env=environment, text=True, capture_output=True, timeout=30
    )
    legacy_run = json.loads(legacy.stdout)["runs"][0]
    assert legacy_run["telemetry_quality"] == "legacy"
    assert legacy_run["timing"]["unattributed_ms"] > 0

    invalid = json.loads((tmp_path / "agent.jsonl").read_text(encoding="utf-8"))
    invalid["schema_version"] = 99
    (tmp_path / "agent.jsonl").write_text(json.dumps(invalid) + "\n", encoding="utf-8")
    rejected = subprocess.run(
        command.args, cwd=REPO_ROOT, env=environment, text=True, capture_output=True, timeout=30
    )
    assert rejected.returncode == 2
    assert "unsupported oneharness history schema" in rejected.stderr
