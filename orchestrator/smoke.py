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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from . import REPO_ROOT
from .history import HistoryError, HistorySession, SessionId, all_sessions, session_records
from .labels import format_labels
from .telemetry import HistoryRecord, history_session_launch_failure

TASK = "Reply with exactly: smoke-ok"
TIMEOUT_SECONDS = 120
#: How many times the paid turn may be launched before the smoke gives up.
#:
#: The launch is the half of this check the host can break without anything being
#: wrong with the launch *path*: under a concurrent e2e load — which is exactly
#: what a worker verifying its own change is running when the pre-push hook selects
#: this — the selected harness has started and died, and the identical command
#: passed standalone moments later. Reporting that one turn as a launch-path
#: regression cost a publication that had already passed its gate. A launch path
#: that is genuinely broken fails every attempt and still fails here, so the only
#: thing bounded retries buy back is the transient case; nothing is relaxed.
LAUNCH_ATTEMPTS = 3
#: Seconds to wait before each retry, so a host that is briefly saturated has a
#: chance to drain rather than being asked the same question three times at once.
RETRY_BACKOFF_SECONDS = 2.0


@dataclass(frozen=True)
class SmokeResult:
    harness: str
    cost_usd: int | float | None
    #: How many real turns this smoke had to launch to record one. Reported,
    #: because a smoke that needed two says something about the host that a smoke
    #: that needed one does not.
    attempts: int = 1


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
    failure = history_session_launch_failure(session)
    if failure is not None:
        raise HistoryError(f"real harness history {failure}")
    # The launch contract above already proved every record names its harness.
    harness = str(records[-1].get("harness"))
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
    """Run one real harness turn and validate its isolated history record.

    Only the *launch* is retried. What a recorded turn says is this repository's own
    contract, so a record that violates it is the regression this smoke exists to
    report — on the first turn, rather than paying for the same verdict three times.
    """
    smoke_id = str(uuid.uuid4())
    timeout_seconds = _timeout_seconds()
    with tempfile.TemporaryDirectory(prefix="orchestrator-watchdog-smoke-") as root_name:
        root = Path(root_name)
        for attempt in range(1, LAUNCH_ATTEMPTS + 1):
            # Each attempt gets its own tree. Reusing one would let the truncated
            # record a killed turn left behind be validated as though the turn that
            # finally succeeded had written it. The directory name keeps the
            # wrapper's own status-directory contract satisfied.
            attempt_root = root / f"orchestrator-watchdog-smoke-attempt-{attempt}"
            target = attempt_root / "target"
            status_dir = attempt_root / "agent"
            history_dir = attempt_root / "history"
            target.mkdir(parents=True)
            status_dir.mkdir()
            try:
                _run_wrapper(target, status_dir, history_dir, smoke_id, timeout_seconds)
            except HistoryError as exc:
                if attempt == LAUNCH_ATTEMPTS:
                    raise HistoryError(f"{exc} (after {LAUNCH_ATTEMPTS} attempts)") from exc
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            return replace(_validate_history(history_dir, smoke_id), attempts=attempt)
    raise AssertionError("unreachable: every attempt either returns or raises")


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
    # A smoke that needed a second launch says something about the host the first
    # one did not, and the operator reading this line is the only one who can act
    # on it — so it is reported rather than smoothed over into an ordinary pass.
    retried = f" after {result.attempts} attempts" if result.attempts > 1 else ""
    print(f"smoke: passed via {result.harness} (recorded cost: {rendered_cost}){retried}")
    return 0
