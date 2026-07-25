"""Public-command integration coverage for persisted launch-smoke contracts."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

from orchestrator import REPO_ROOT
from orchestrator.smoke import TASK


def _record(
    history_dir: Path,
    smoke_id: str,
    *,
    prompt: object = TASK,
    harness: object = "codex",
    finished_at: object = "2026-07-25T00:00:00.010Z",
    status: object = "ok",
    exit_code: object = 0,
    cost: object = None,
    suffix: str = "one",
) -> None:
    project = history_dir / "tmp-smoke-target"
    project.mkdir(parents=True, exist_ok=True)
    history_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{smoke_id}-{suffix}"))
    record = {
        "type": "run",
        "schema_version": "1.0",
        "history_id": history_id,
        "session": f"smoke-{suffix}",
        "name": "reply-with-exactly-smoke-ok",
        "labels": {"role": "agent", "smoke": smoke_id},
        "project": "/tmp/smoke-target",
        "timestamp": "2026-07-25T00:00:00Z",
        "harness": harness,
        "model": "gpt-5.6-sol",
        "prompt": prompt,
        "permission_mode": "bypass",
        "status": status,
        "exit_code": exit_code,
        "duration_ms": 10,
        "started_at": "2026-07-25T00:00:00Z",
        "finished_at": finished_at,
        "model_ms": 10,
        "tool_ms": 0,
        "time_to_first_token_ms": 1,
        "text": "smoke-ok",
        "usage": {
            "input_tokens": 10,
            "output_tokens": 2,
            "cache_read_tokens": 0,
            "cache_write_tokens": None,
            "cost_usd": cost,
        },
    }
    path = project / f"smoke-{suffix}-20260725T000000Z-{history_id}.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")


def _validate(history_dir: Path, smoke_id: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "uv",
            "run",
            "orchestrator-smoke",
            "--validate-history",
            str(history_dir),
            "--smoke-id",
            smoke_id,
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEHARNESS_HISTORY_DIR": str(history_dir)},
        text=True,
        capture_output=True,
    )


def test_smoke_command_surfaces_real_wrapper_failure_without_a_paid_turn() -> None:
    result = subprocess.run(
        ["just", "smoke"],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "ONEHARNESS_BIN_CODEX": "/does/not/exist/codex",
            "ONEHARNESS_BIN_CLAUDE_CODE": "/does/not/exist/claude",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "real harness smoke failed" in result.stderr
    assert "rerun 'just smoke'" in result.stderr


@pytest.mark.parametrize("timeout", ["bad", "0", "121"])
def test_smoke_command_rejects_invalid_timeout_before_launch(timeout: str) -> None:
    result = subprocess.run(
        ["uv", "run", "orchestrator-smoke"],
        cwd=REPO_ROOT,
        env={**os.environ, "ORCHESTRATOR_SMOKE_TIMEOUT_SECONDS": timeout},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "ORCHESTRATOR_SMOKE_TIMEOUT_SECONDS must be between 1 and 120" in result.stderr


@pytest.mark.parametrize(
    "arguments",
    [
        ["--validate-history", "/tmp/history"],
        ["--smoke-id", "smoke-id"],
    ],
)
def test_validation_command_requires_history_and_smoke_id_together(arguments: list[str]) -> None:
    result = subprocess.run(
        ["uv", "run", "orchestrator-smoke", *arguments],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "--validate-history and --smoke-id must be supplied together" in result.stderr


@pytest.mark.parametrize(
    ("field", "value", "diagnostic"),
    [
        ("prompt", "wrong task", "did not receive the dispatched task"),
        ("finished_at", None, "history telemetry is incomplete"),
        ("harness", "", "does not identify the selected harness"),
        ("status", "nonzero", "history telemetry is incomplete"),
        ("exit_code", 2, "history telemetry is incomplete"),
    ],
)
def test_validation_command_rejects_broken_persisted_contract(
    tmp_path: Path, field: str, value: object, diagnostic: str
) -> None:
    smoke_id = f"broken-{field}"
    _record(tmp_path, smoke_id, **{field: value})

    result = _validate(tmp_path, smoke_id)

    assert result.returncode == 1
    assert diagnostic in result.stderr


def test_validation_command_rejects_multiple_matching_sessions(tmp_path: Path) -> None:
    smoke_id = "duplicate"
    _record(tmp_path, smoke_id, suffix="one")
    _record(tmp_path, smoke_id, suffix="two")

    result = _validate(tmp_path, smoke_id)

    assert result.returncode == 1
    assert "expected one smoke history session, found 2" in result.stderr


@pytest.mark.parametrize("cost", ["malformed", float("inf"), float("-inf"), float("nan")])
def test_validation_command_renders_invalid_recorded_cost_as_unreported(
    tmp_path: Path, cost: object
) -> None:
    smoke_id = "invalid-cost"
    _record(tmp_path, smoke_id, cost=cost)

    result = _validate(tmp_path, smoke_id)

    assert result.returncode == 0, result.stderr
    assert "recorded cost: unreported" in result.stdout
