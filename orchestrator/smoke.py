"""One paid, real-harness launch-path smoke."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import REPO_ROOT
from .history import HistoryError, all_sessions, session_records
from .labels import format_labels
from .telemetry import history_session_has_complete_telemetry

TASK = "Reply with exactly: smoke-ok"
TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class SmokeResult:
    harness: str
    cost_usd: int | float | None


def _run_wrapper(target: Path, status_dir: Path, history_dir: Path, smoke_id: str) -> None:
    env = {
        **os.environ,
        "ONEHARNESS_HISTORY_DIR": str(history_dir),
        "ONEHARNESS_HISTORY_LABELS": format_labels({"role": "agent", "smoke": smoke_id}),
        "ORCHESTRATOR_AGENT_STATUS_DIR": str(status_dir),
    }
    process = subprocess.Popen(
        [
            str(REPO_ROOT / "scripts" / "oneharness-agent.sh"),
            "run",
            "--prompt-file",
            "-",
            "--cwd",
            str(target),
            "--mode",
            "bypass",
            "--timeout",
            str(TIMEOUT_SECONDS),
        ],
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    assert process.stdin is not None
    process.stdin.write(TASK)
    process.stdin.close()
    process.stdin = None
    deadline = time.monotonic() + TIMEOUT_SECONDS + 10
    while process.poll() is None and time.monotonic() < deadline:
        if (status_dir / "agent.failed").exists():
            os.killpg(process.pid, signal.SIGTERM)
            stdout, stderr = process.communicate()
            detail = stderr.strip() or stdout.strip() or "agent harness failed"
            raise HistoryError(f"real harness smoke failed: {detail}")
        time.sleep(0.25)
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        process.communicate()
        raise HistoryError("real harness smoke timed out") from None
    stdout, stderr = process.communicate()
    if process.returncode:
        detail = stderr.strip() or stdout.strip() or f"exit {process.returncode}"
        raise HistoryError(f"real harness smoke failed: {detail}")


def run_smoke() -> SmokeResult:
    """Run one real harness turn and validate its isolated history record."""
    smoke_id = str(uuid.uuid4())
    with tempfile.TemporaryDirectory(prefix="orchestrator-watchdog-smoke-") as root_name:
        root = Path(root_name)
        target = root / "target"
        status_dir = root / "agent"
        history_dir = root / "history"
        target.mkdir()
        status_dir.mkdir()
        _run_wrapper(target, status_dir, history_dir, smoke_id)

        previous = os.environ.get("ONEHARNESS_HISTORY_DIR")
        os.environ["ONEHARNESS_HISTORY_DIR"] = str(history_dir)
        try:
            matches = [
                session for session in all_sessions() if session.labels.get("smoke") == smoke_id
            ]
        finally:
            if previous is None:
                os.environ.pop("ONEHARNESS_HISTORY_DIR", None)
            else:
                os.environ["ONEHARNESS_HISTORY_DIR"] = previous
        if len(matches) != 1:
            raise HistoryError(f"expected one smoke history session, found {len(matches)}")
        session = matches[0]
        records = session_records(session)
        prompts = [record.get("prompt") for record in records]
        if not prompts or any(prompt != TASK or not prompt for prompt in prompts):
            raise HistoryError("real harness did not receive the dispatched task")
        if not history_session_has_complete_telemetry(session):
            raise HistoryError("real harness history telemetry is incomplete")
        harness = records[-1].get("harness")
        if not isinstance(harness, str) or not harness:
            raise HistoryError("real harness history does not identify the selected harness")
        usage = records[-1].get("usage", {})
        cost = usage.get("cost_usd") if isinstance(usage, dict) else None
        return SmokeResult(
            harness=harness,
            cost_usd=cost if isinstance(cost, (int, float)) else None,
        )


def main() -> int:
    try:
        result = run_smoke()
    except HistoryError as exc:
        print(f"smoke: {exc}", file=sys.stderr)
        return 1
    rendered_cost = f"${result.cost_usd:.6f}" if result.cost_usd is not None else "unreported"
    print(f"smoke: passed via {result.harness} (recorded cost: {rendered_cost})")
    return 0
