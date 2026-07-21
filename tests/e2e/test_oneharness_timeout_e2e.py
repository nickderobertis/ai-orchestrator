"""Real-CLI regression coverage for oneharness process-tree timeouts."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from orchestrator import REPO_ROOT

TIMEOUT_HARNESS = REPO_ROOT / "tests" / "e2e" / "timeout_harness.py"


def _assert_descendant_stopped(tick_file: Path) -> None:
    """Prove the fixture existed and cannot keep working after CLI return."""
    witness_deadline = time.monotonic() + 2
    while time.monotonic() < witness_deadline:
        witnessed = tick_file.stat().st_size if tick_file.exists() else 0
        if witnessed:
            break
        time.sleep(0.02)
    else:
        raise AssertionError("TERM-ignoring descendant never wrote its durable tick witness")

    time.sleep(0.3)
    assert tick_file.stat().st_size == witnessed, (
        "TERM-ignoring descendant kept ticking after oneharness returned"
    )


def test_timeout_kills_process_tree_and_preserves_real_partial_telemetry(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """Drive the adopted binary across the subprocess, parser, and history boundaries."""
    tick_file = tmp_path / "descendant.ticks"
    history_dir = tmp_path / "history"
    env = {key: value for key, value in os.environ.items() if not key.startswith("ONEHARNESS_")}
    env["TIMEOUT_HARNESS_TICK_FILE"] = str(tick_file)

    started = time.monotonic()
    proc = subprocess.run(
        [
            oneharness_bin,
            "run",
            "--harness",
            "opencode",
            "--prompt",
            "capture timeout evidence",
            "--timeout",
            "1",
            "--bin",
            f"opencode={TIMEOUT_HARNESS}",
            "--history",
            "--history-dir",
            str(history_dir),
            "--no-config",
            "--compact",
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=7,
    )
    elapsed = time.monotonic() - started

    assert proc.returncode == 1, proc.stderr
    assert elapsed >= 0.9, f"timeout returned before its configured deadline: {elapsed:.3f}s"
    assert elapsed < 3, f"timeout did not return near its deadline: {elapsed:.3f}s"
    report: dict[str, Any] = json.loads(proc.stdout)
    result = report["results"][0]
    assert result["status"] == "timeout"
    assert result["exit_code"] is None
    assert result["text"] == "partial answer"
    assert result["text_source"] == "json:opencode-parts"
    assert result["usage"]["input_tokens"] == 12
    assert result["usage"]["output_tokens"] == 3
    assert result["usage"]["cache_read_tokens"] == 9
    assert result["usage"]["cache_write_tokens"] == 4
    assert result["usage"]["cost_usd"] == 0.01
    assert result["session_id"] == "ses-timeout"
    assert result["events_source"] == "json:opencode-parts"
    assert result["events"] == [
        {
            "kind": "tool_call",
            "name": "bash",
            "input": {"command": "echo hi"},
            "output": "hi",
            "index": 0,
            "tool_call_id": None,
            "started_at": None,
            "finished_at": None,
            "duration_ms": None,
            "status": None,
        }
    ]
    assert "native child stderr" in result["stderr"]
    assert result["stdout"].endswith('{"type":"incomplete"')

    # The timed-out transcript has no complete provider timing trace. The released
    # CLI preserves the partial result but gracefully omits its history record.
    history_file = Path(report["history_file"])
    assert not history_file.exists()
    assert "could not write history record" in proc.stderr

    _assert_descendant_stopped(tick_file)
