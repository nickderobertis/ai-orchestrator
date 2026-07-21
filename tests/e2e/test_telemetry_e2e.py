"""E2E: telemetry CLI over run-plan and a recorded oneharness history store."""

# llmlint: ignore-file[e2e_not_mocked] oneharness' history reader is the subprocess
# boundary under test; fake_oneharness serves the same normalized store because the
# normalized history boundary under test. The report-producing backend supplies deterministic
# report-v5 values while the real onejudge and SDK exercise validation and persistence.

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from waits import timeout as e2e_timeout

from orchestrator import REPO_ROOT

FAKE_ONEHARNESS = REPO_ROOT / "tests" / "e2e" / "fake_oneharness.py"
FAKE_BACKEND = REPO_ROOT / "tests" / "e2e" / "fake_backend.py"


def test_breakdown_aggregates_real_multirole_history_records(
    tmp_path: Path, command_base, onejudge_bin: str
) -> None:
    runs_dir = tmp_path / "runs"
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "api",
                        "persona": "engineer",
                        "task": "complete-now: telemetry-native",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    report_proxy = tmp_path / "onejudge-report-proxy"
    report_proxy.write_text(
        f'#!/bin/sh\nexec {sys.executable} {FAKE_BACKEND} onejudge-report-proxy "$@"\n',
        encoding="utf-8",
    )
    report_proxy.chmod(0o755)
    producer_env = {
        **os.environ,
        "REAL_ONEJUDGE": onejudge_bin,
        "FAKE_REPORT_TELEMETRY": "native",
    }
    planned = subprocess.run(
        [
            "just",
            "run-plan",
            str(plan),
            "--run",
            "telemetry-e2e",
            "--runs-dir",
            str(runs_dir),
            "--base",
            str(command_base()),
            "--onejudge-bin",
            str(report_proxy),
        ],
        cwd=REPO_ROOT,
        env=producer_env,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    assert planned.returncode == 0, planned.stderr
    persisted = json.loads(
        (runs_dir / "telemetry-e2e" / "round-01" / "result.json").read_text(encoding="utf-8")
    )
    assert persisted["results"]["api"]["telemetry"]["wall_ms"] == 25

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
    for session_id, name, prompt in (
        (
            "legacy-lint-prompt",
            "unhelpful-legacy-name",
            "Evaluate each rule against the target files in this repository",
        ),
        (
            "legacy-lint-slug",
            "your-previous-verdict-reported-rule-violations-in-files-that-those-rules-do-not-cover",
            "legacy record without a direct prompt",
        ),
    ):
        history = tmp_path / f"{session_id}.jsonl"
        history.write_text(
            json.dumps(
                {
                    "prompt": prompt,
                    "duration_ms": 1,
                    "model_ms": 0,
                    "tool_ms": 0,
                    "usage": {
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": 0,
                        "cost_usd": 0.001,
                    },
                    "events": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        sessions.append(
            {
                "id": session_id,
                "name": name,
                "project": str(tmp_path),
                "started": "2026-07-19T00:00:00Z",
                "path": str(history),
                "labels": {"run_id": "telemetry-e2e", "node": "api"},
            }
        )
    store = tmp_path / "store.json"
    store.write_text(json.dumps({"sessions": sessions}), encoding="utf-8")
    oneharness = tmp_path / "oneharness"
    oneharness.write_text(
        "#!/usr/bin/env python3\n" + FAKE_ONEHARNESS.read_text(encoding="utf-8"), encoding="utf-8"
    )
    oneharness.chmod(0o755)
    environment = {
        **os.environ,
        "FAKE_ONEHARNESS_STORE": str(store),
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
    }
    command = subprocess.run(
        [
            "just",
            "telemetry",
            "--runs-dir",
            str(runs_dir),
            "--oneharness-bin",
            str(oneharness),
            "--all",
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    assert command.returncode == 0, command.stderr
    indexed = {run["run_id"]: run for run in json.loads(command.stdout)["runs"]}
    run = indexed["telemetry-e2e"]
    assert run["timing"]["agent_model_ms"] == 12
    assert run["timing"]["judge_model_ms"] == 8
    assert run["timing"]["tool_ms"] == 5
    assert run["usage"]["total"]["input_tokens"] == 15
    assert run["usage"]["total"]["cost_usd"] == 0.032
    assert [link["role"] for link in run["nodes"][0]["sessions"]] == [
        "agent",
        "judge",
        "llmlint",
        "llmlint",
    ]
    assert run["nodes"][0]["usage"]["total"]["input_tokens"] == 15
    assert run["nodes"][0]["timing"]["fractions"] == {
        "agent_model": 0.48,
        "judge_model": 0.32,
        "llmlint_model": 0.0,
        "tool": 0.2,
        "idle_orchestration": 0.0,
        "lock_wait": 0.0,
        "setup": 0.0,
        "scheduling": 0.0,
    }
    assert run["nodes"][0]["turns"] == 2
    assert run["nodes"][0]["lint"] == 2
    assert run["nodes"][0]["tool_commands"] == {"gate": 2}
    assert set(run["node_work_ms"]) == {
        "agent_model_ms",
        "judge_model_ms",
        "llmlint_model_ms",
        "tool_ms",
        "wall_ms",
    }
    assert run["telemetry_quality"] == "partial"
    assert run["sources"] == ["onejudge", "oneharness", "history_legacy", "journal_legacy"]

    breakdown = subprocess.run(
        [*command.args, "--breakdown"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    assert breakdown.returncode == 0, breakdown.stderr
    assert "telemetry-e2e" in breakdown.stdout
    assert "  api" in breakdown.stdout
    assert "turn 0 agent" in breakdown.stdout
    assert "turn 1 judge" in breakdown.stdout
    assert "2=1" in breakdown.stdout

    plan.write_text(
        plan.read_text(encoding="utf-8").replace("telemetry-native", "telemetry-native-invalid"),
        encoding="utf-8",
    )
    invalid_args = ["telemetry-invalid" if arg == "telemetry-e2e" else arg for arg in planned.args]
    producer_env["FAKE_REPORT_TELEMETRY"] = "invalid"
    invalid_produced = subprocess.run(
        invalid_args,
        cwd=REPO_ROOT,
        env=producer_env,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    assert invalid_produced.returncode == 1, invalid_produced.stderr
    for session in sessions:
        session["labels"]["run_id"] = "telemetry-invalid"
    store.write_text(json.dumps({"sessions": sessions}), encoding="utf-8")
    fallback = subprocess.run(
        command.args,
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    fallback_run = next(
        run for run in json.loads(fallback.stdout)["runs"] if run["run_id"] == "telemetry-invalid"
    )
    assert fallback_run["usage"]["total"]["cache_write_tokens"] == 0
    assert fallback_run["usage"]["total"]["cost_usd"] == 0.032
    assert fallback_run["telemetry_quality"] == "partial"

    judge_record = json.loads((tmp_path / "judge.jsonl").read_text(encoding="utf-8"))
    judge_record["usage"].pop("cache_write_tokens")
    judge_record["usage"]["cost_usd"] = True
    (tmp_path / "judge.jsonl").write_text(json.dumps(judge_record) + "\n", encoding="utf-8")
    unknown = subprocess.run(
        command.args,
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    unknown_run = next(
        run for run in json.loads(unknown.stdout)["runs"] if run["run_id"] == "telemetry-invalid"
    )
    assert unknown_run["usage"]["total"]["cache_write_tokens"] is None
    assert unknown_run["usage"]["total"]["cost_usd"] is None
    unknown_breakdown = subprocess.run(
        [*command.args, "--breakdown"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    assert "?" in unknown_breakdown.stdout

    judge_record["events"][0]["tool_call_id"] = ""
    (tmp_path / "judge.jsonl").write_text(json.dumps(judge_record) + "\n", encoding="utf-8")
    malformed_tool = subprocess.run(
        command.args,
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    assert malformed_tool.returncode == 2
    judge_record["events"][0]["tool_call_id"] = "judge-tool-1"
    (tmp_path / "judge.jsonl").write_text(json.dumps(judge_record) + "\n", encoding="utf-8")
    invalid_timing = dict(judge_record)
    invalid_timing["duration_ms"] = True
    (tmp_path / "judge.jsonl").write_text(json.dumps(invalid_timing) + "\n", encoding="utf-8")
    rejected_timing = subprocess.run(
        command.args,
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    assert rejected_timing.returncode == 2
    (tmp_path / "judge.jsonl").write_text(json.dumps(judge_record) + "\n", encoding="utf-8")
    sessions[1]["labels"]["role"] = "other"
    store.write_text(json.dumps({"sessions": sessions}), encoding="utf-8")
    rejected_role = subprocess.run(
        command.args,
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    assert rejected_role.returncode == 2
    sessions[1]["labels"]["role"] = "judge"
    store.write_text(json.dumps({"sessions": sessions}), encoding="utf-8")

    for history in (tmp_path / "agent.jsonl", tmp_path / "judge.jsonl"):
        record = json.loads(history.read_text(encoding="utf-8"))
        for field in ("schema_version", "model_ms", "tool_ms"):
            record.pop(field)
        for field in ("tool_call_id", "started_at", "finished_at", "duration_ms", "status"):
            record["events"][0].pop(field)
        history.write_text(json.dumps(record) + "\n", encoding="utf-8")
    plan.write_text(
        plan.read_text(encoding="utf-8").replace("telemetry-native-invalid", "telemetry-legacy"),
        encoding="utf-8",
    )
    legacy_args = ["telemetry-legacy" if arg == "telemetry-e2e" else arg for arg in planned.args]
    producer_env["FAKE_REPORT_TELEMETRY"] = "legacy"
    legacy_produced = subprocess.run(
        legacy_args,
        cwd=REPO_ROOT,
        env=producer_env,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    assert legacy_produced.returncode == 0, legacy_produced.stderr
    for session in sessions:
        session["labels"]["run_id"] = "telemetry-legacy"
    store.write_text(json.dumps({"sessions": sessions}), encoding="utf-8")
    legacy = subprocess.run(
        command.args,
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    legacy_run = next(
        run for run in json.loads(legacy.stdout)["runs"] if run["run_id"] == "telemetry-legacy"
    )
    assert legacy_run["telemetry_quality"] == "legacy"
    assert legacy_run["timing"]["unattributed_ms"] > 0
    legacy_breakdown = subprocess.run(
        [*command.args, "--breakdown"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    assert "Timeline: unavailable (legacy session linkage)" in legacy_breakdown.stdout

    invalid = json.loads((tmp_path / "agent.jsonl").read_text(encoding="utf-8"))
    invalid["schema_version"] = 99
    (tmp_path / "agent.jsonl").write_text(json.dumps(invalid) + "\n", encoding="utf-8")
    rejected = subprocess.run(
        command.args,
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    assert rejected.returncode == 2
    assert "unsupported oneharness history schema" in rejected.stderr

    (tmp_path / "agent.jsonl").write_text(
        '{"duration_ms":1200,"status":"running"}\n{"duration_ms":2500,"status":"completed"}\n',
        encoding="utf-8",
    )
    shown = subprocess.run(
        ["just", "history-show", "agent-history"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    assert shown.returncode == 0, shown.stderr
    assert "Latest: completed (3.7s)" in shown.stdout
