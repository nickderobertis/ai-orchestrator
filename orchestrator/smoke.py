"""One paid, real-harness launch-path smoke."""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from . import REPO_ROOT
from .history import HistoryError, HistorySession, SessionId, all_sessions, session_records
from .labels import format_labels
from .telemetry import HistoryRecord, history_session_is_successful_with_complete_telemetry

TASK = "Reply with exactly: smoke-ok"
TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class SmokeResult:
    harness: str
    cost_usd: int | float | None


def _timeout_seconds() -> int:
    raw = os.environ.get("ORCHESTRATOR_SMOKE_TIMEOUT_SECONDS")
    if raw is None:
        return TIMEOUT_SECONDS
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if not 1 <= value <= TIMEOUT_SECONDS:
        raise HistoryError(
            f"ORCHESTRATOR_SMOKE_TIMEOUT_SECONDS must be between 1 and {TIMEOUT_SECONDS}"
        )
    return value


def _run_wrapper(
    target: Path, status_dir: Path, history_dir: Path, smoke_id: str, timeout_seconds: int
) -> None:
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
            str(timeout_seconds),
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
    deadline = time.monotonic() + timeout_seconds + 10
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


def _stored_sessions(history_dir: Path) -> list[HistorySession]:
    """Load validation candidates from an explicitly supplied isolated store."""
    sessions: list[HistorySession] = []
    for path in history_dir.rglob("*.jsonl"):
        try:
            records = [
                cast(HistoryRecord, value)
                for line in path.read_text(encoding="utf-8").splitlines()
                if isinstance((value := json.loads(line)), dict) and value.get("type") == "run"
            ]
        except (OSError, json.JSONDecodeError):
            continue
        if not records:
            continue
        record = records[0]
        labels = record.get("labels")
        sessions.append(
            HistorySession(
                SessionId(str(record.get("session", path.stem))),
                str(record.get("name", path.stem)),
                Path(str(record.get("project", history_dir))),
                str(record.get("timestamp", "")),
                path,
                {
                    str(key): str(value)
                    for key, value in labels.items()
                    if isinstance(key, str) and isinstance(value, str)
                }
                if isinstance(labels, dict)
                else {},
            )
        )
    return sessions


def _validate_history(
    history_dir: Path, smoke_id: str, *, sessions: list[HistorySession] | None = None
) -> SmokeResult:
    """Validate one isolated history store."""
    previous = os.environ.get("ONEHARNESS_HISTORY_DIR")
    os.environ["ONEHARNESS_HISTORY_DIR"] = str(history_dir)
    try:
        matches = [
            session
            for session in (all_sessions() if sessions is None else sessions)
            if session.labels.get("smoke") == smoke_id
        ]
    finally:
        if previous is None:
            os.environ.pop("ONEHARNESS_HISTORY_DIR", None)
        else:
            os.environ["ONEHARNESS_HISTORY_DIR"] = previous
    if len(matches) != 1:
        raise HistoryError(f"expected one smoke history session, found {len(matches)}")
    session = matches[0]
    records = cast(list[HistoryRecord], session_records(session))
    prompts = [record.get("prompt") for record in records]
    if not prompts or any(prompt != TASK or not prompt for prompt in prompts):
        raise HistoryError("real harness did not receive the dispatched task")
    if not history_session_is_successful_with_complete_telemetry(session):
        raise HistoryError("real harness history telemetry is incomplete")
    harness = records[-1].get("harness")
    if not isinstance(harness, str) or not harness:
        raise HistoryError("real harness history does not identify the selected harness")
    usage = records[-1].get("usage", {})
    cost = usage.get("cost_usd") if isinstance(usage, dict) else None
    valid_cost = (
        cost
        if isinstance(cost, (int, float))
        and not isinstance(cost, bool)
        and math.isfinite(cost)
        and cost >= 0
        else None
    )
    return SmokeResult(harness=harness, cost_usd=valid_cost)


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
        _run_wrapper(target, status_dir, history_dir, smoke_id, _timeout_seconds())
        return _validate_history(history_dir, smoke_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run or validate the real-harness smoke")
    parser.add_argument("--validate-history", type=Path)
    parser.add_argument("--smoke-id")
    args = parser.parse_args(argv)
    if (args.validate_history is None) != (args.smoke_id is None):
        parser.error("--validate-history and --smoke-id must be supplied together")
    try:
        result = (
            _validate_history(
                args.validate_history,
                args.smoke_id,
                sessions=_stored_sessions(args.validate_history),
            )
            if args.validate_history is not None and args.smoke_id is not None
            else run_smoke()
        )
    except HistoryError as exc:
        print(f"smoke: {exc}", file=sys.stderr)
        print(
            "smoke: fix the reported real-harness launch/history contract, then rerun 'just smoke'",
            file=sys.stderr,
        )
        return 1
    rendered_cost = f"${result.cost_usd:.6f}" if result.cost_usd is not None else "unreported"
    print(f"smoke: passed via {result.harness} (recorded cost: {rendered_cost})")
    return 0
