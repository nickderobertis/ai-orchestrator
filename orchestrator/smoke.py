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

from . import REPO_ROOT
from .history import HistoryError, HistorySession, SessionId, all_sessions
from .labels import SMOKE_LABEL, format_labels
from .scratch import AGENT_STATUS_DIR_ENV
from .telemetry import (
    NO_LAUNCH_RECORD_FAILURE,
    HistoryRecord,
    history_record_fallthrough_failure,
    history_record_fallthrough_reason,
    history_record_identity,
    history_session_launch_failure,
    is_turn_record,
    session_turn_records,
)

TASK = "Reply with exactly: smoke-ok"
TIMEOUT_SECONDS = 120
#: How many times the paid turn may be launched before the smoke gives up. The
#: launch is the half of this check a loaded host can break while the launch *path*
#: is fine; docs/onejudge-integration.md records why that matters here.
LAUNCH_ATTEMPTS = 3
#: Seconds before each retry, so a briefly saturated host has a chance to drain
#: rather than being asked the same question three times at once.
RETRY_BACKOFF_SECONDS = 2.0


@dataclass(frozen=True)
class FellThrough:
    """One candidate the fallback chain moved past on its way to the selected one."""

    #: The variant-qualified identity, not the harness: a chain's two Claude
    #: subscriptions are one harness and differ only here.
    identity: str
    reason: str


@dataclass(frozen=True)
class SmokeResult:
    #: The identity the chain selected, named the same way for the same reason.
    identity: str
    cost_usd: int | float | None
    #: How many real turns this smoke had to launch to record one.
    attempts: int = 1
    #: The candidates the chain refused before the one that ran, in the order it
    #: tried them. Reported rather than swallowed: which identity is out of quota
    #: is the operator's business even when the launch path is healthy.
    fell_through: tuple[FellThrough, ...] = ()


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
        "ONEHARNESS_HISTORY_LABELS": format_labels({"role": "agent", SMOKE_LABEL: smoke_id}),
        AGENT_STATUS_DIR_ENV: str(status_dir),
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
                value
                for line in path.read_text(encoding="utf-8").splitlines()
                if isinstance((value := json.loads(line)), dict) and is_turn_record(value)
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


def _selected(records: list[HistoryRecord]) -> tuple[tuple[FellThrough, ...], HistoryRecord]:
    """Split a chain's records into the candidates it refused and the one it ran.

    `run_mode = "fallback"` records every candidate it attempts, in priority order,
    and stops at the first that can actually run the task — so the launch path's
    outcome is the LAST record, and the ones before it are the chain doing its job.
    Judging them all alike is what failed this smoke for a healthy launch while one
    subscription's weekly quota was gone.

    A candidate that failed for any other reason is not one the chain moved past:
    it either ran and broke, or it broke in a way nobody classified, and both are
    the launch breakage this smoke exists to report.

    A candidate's own word for what happened to it is not taken on trust, because
    these records come out of a store nothing here wrote and each one reaches both
    the verdict and the operator's report. One that claims it stepped aside while
    naming no identity, or while carrying a turn somebody was billed for, is
    reported rather than believed.
    """
    *candidates, selected = records
    fell_through: list[FellThrough] = []
    for candidate in candidates:
        reason = history_record_fallthrough_reason(candidate)
        if reason is None:
            raise HistoryError(
                f"real harness candidate {history_record_identity(candidate)} failed "
                f"unclassified with status {candidate.get('status')!r} and exit code "
                f"{candidate.get('exit_code')!r}; the fallback chain only moves past a "
                "candidate it could not run at all, so this is a launch failure"
            )
        unsupported = history_record_fallthrough_failure(candidate)
        if unsupported is not None:
            raise HistoryError(
                f"real harness candidate {history_record_identity(candidate)} was recorded "
                f"as {reason} but {unsupported}; the fallback chain only moves past a "
                "candidate that ran nothing, so this is a launch failure"
            )
        fell_through.append(FellThrough(history_record_identity(candidate), reason))
    exhausted = history_record_fallthrough_reason(selected)
    if exhausted is not None:
        refused = ", ".join(
            f"{entry.identity} ({entry.reason})"
            for entry in [*fell_through, FellThrough(history_record_identity(selected), exhausted)]
        )
        raise HistoryError(
            f"real harness fallback chain had no candidate left to run the task: {refused}; "
            "restore one of these identities, then rerun"
        )
    return tuple(fell_through), selected


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
    records = session_turn_records(session)
    if not records:
        raise HistoryError(f"real harness history {NO_LAUNCH_RECORD_FAILURE}")
    fell_through, selected = _selected(records)
    if selected.get("prompt") != TASK:
        raise HistoryError("real harness did not receive the dispatched task")
    failure = history_session_launch_failure(session, [selected])
    if failure is not None:
        raise HistoryError(f"real harness history {failure}")
    # The launch contract above already proved the selected record names its harness.
    identity = history_record_identity(selected)
    usage = selected.get("usage", {})
    cost = usage.get("cost_usd") if isinstance(usage, dict) else None
    valid_cost = (
        cost
        if isinstance(cost, (int, float))
        and not isinstance(cost, bool)
        and math.isfinite(cost)
        and cost >= 0
        else None
    )
    return SmokeResult(identity=identity, cost_usd=valid_cost, fell_through=fell_through)


def run_smoke() -> SmokeResult:
    """Run one real harness turn and validate the record of the harness it selected.

    Only the *launch* is retried. What a recorded turn says is this repository's own
    contract, so a record that violates it is the regression this smoke exists to
    report — on the first turn, rather than paying for the same verdict three times.

    "The" record is the selected candidate's: under `run_mode = "fallback"` a chain
    that refused an exhausted subscription and ran the next identity has a healthy
    launch path, and its refusal record is evidence of that rather than against it.
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
    # Named on its own line, before the verdict: the chain selecting a working
    # identity is what this smoke passed on, and an operator reading a pass still
    # wants to know which subscription is gone and why.
    for entry in result.fell_through:
        print(
            f"smoke: fell through {entry.identity} ({entry.reason}); "
            "the fallback chain handed the turn to the next identity"
        )
    # Reported rather than smoothed into an ordinary pass: only the operator can
    # act on a host that killed a launch.
    retried = f" after {result.attempts} attempts" if result.attempts > 1 else ""
    print(f"smoke: passed via {result.identity} (recorded cost: {rendered_cost}){retried}")
    return 0
