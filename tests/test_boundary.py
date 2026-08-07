"""The boundary-retry policy, and the record the next round folds into the journal.

The journeys are elsewhere: the wrapper's real retry runs as a subprocess in
tests/test_oneharness_orchestrator_wrapper.py, and both the check-in's retry and
the fold of a wrapper retry into the next round's journal run through the real
recipes in tests/e2e/test_boundary_retry_e2e.py. What lives here is the trust
boundary neither can produce on demand — an attempts log a subprocess wrote into
every shape this reader must refuse — and the policy's own arithmetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.boundary import (
    BACKOFF_FACTOR,
    DEFAULT_ATTEMPTS,
    DEFAULT_BACKOFF_SECONDS,
    MAX_ATTEMPTS,
    MAX_BACKOFF_SECONDS,
    MAX_LOG_LINE_BYTES,
    MAX_LOG_LINES_PER_FOLD,
    MAX_REASON_CHARS,
    BoundaryAttempt,
    RetryPolicy,
    attempts_log,
    drain_attempts,
    record_attempt,
    retry_boundary_request,
)


def test_policy_reads_the_environment_and_falls_back_to_the_default() -> None:
    assert RetryPolicy.from_environment({}) == RetryPolicy(
        DEFAULT_ATTEMPTS, DEFAULT_BACKOFF_SECONDS
    )
    configured = RetryPolicy.from_environment(
        {
            "ORCHESTRATOR_BOUNDARY_ATTEMPTS": "5",
            "ORCHESTRATOR_BOUNDARY_BACKOFF_SECONDS": "1.5",
        }
    )
    assert configured == RetryPolicy(5, 1.5)


@pytest.mark.parametrize(
    "environment",
    [
        {"ORCHESTRATOR_BOUNDARY_ATTEMPTS": "0"},
        {"ORCHESTRATOR_BOUNDARY_ATTEMPTS": "many"},
        {"ORCHESTRATOR_BOUNDARY_BACKOFF_SECONDS": "-1"},
        {"ORCHESTRATOR_BOUNDARY_BACKOFF_SECONDS": "nan"},
        {"ORCHESTRATOR_BOUNDARY_BACKOFF_SECONDS": "soon"},
    ],
)
def test_an_unusable_setting_never_stops_a_recovery(environment: dict[str, str]) -> None:
    """This decides how hard a recovery tries; a bad value must not disable it."""
    policy = RetryPolicy.from_environment(environment)
    assert policy.attempts >= 1
    assert policy.backoff > 0


def test_a_configured_count_past_the_ceiling_is_still_bounded() -> None:
    """ "Bounded retry" has to stay a bound however the environment is set."""
    policy = RetryPolicy.from_environment(
        {"ORCHESTRATOR_BOUNDARY_ATTEMPTS": str(MAX_ATTEMPTS * 100)}
    )

    assert policy.attempts == MAX_ATTEMPTS


def test_backoff_doubles_and_is_capped() -> None:
    policy = RetryPolicy(attempts=10, backoff=DEFAULT_BACKOFF_SECONDS)
    assert policy.delay(1) == DEFAULT_BACKOFF_SECONDS
    assert policy.delay(2) == DEFAULT_BACKOFF_SECONDS * BACKOFF_FACTOR
    assert policy.delay(9) == MAX_BACKOFF_SECONDS


def test_a_retryable_failure_is_asked_again_and_reported() -> None:
    calls: list[int] = []
    reported: list[BoundaryAttempt] = []
    waited: list[float] = []

    def operation() -> str:
        calls.append(len(calls) + 1)
        if len(calls) < 3:
            raise RuntimeError("provider refused")
        return "answered"

    answer = retry_boundary_request(
        operation,
        role="orchestrator",
        policy=RetryPolicy(attempts=3, backoff=2.0),
        retryable=lambda _exc: True,
        report=reported.append,
        sleep=waited.append,
        now=lambda: 1000.0,
    )

    assert answer == "answered"
    assert [item.attempt for item in reported] == [1, 2]
    assert {item.attempts for item in reported} == {3}
    assert waited == [2.0, 4.0]


def test_a_failure_the_caller_will_not_retry_is_raised_at_once() -> None:
    calls: list[int] = []

    def operation() -> str:
        calls.append(1)
        raise ValueError("the agent simply did its job badly")

    with pytest.raises(ValueError):
        retry_boundary_request(
            operation,
            role="check-in",
            policy=RetryPolicy(attempts=3, backoff=0.0),
            retryable=lambda _exc: False,
            report=lambda _attempt: None,
            sleep=lambda _seconds: None,
        )
    assert calls == [1]


def test_an_exhausted_budget_raises_the_last_failure() -> None:
    """A boundary that genuinely cannot be crossed still fails, and says so."""
    with pytest.raises(RuntimeError, match="still refusing"):
        retry_boundary_request(
            lambda: (_ for _ in ()).throw(RuntimeError("still refusing")),
            role="orchestrator",
            policy=RetryPolicy(attempts=2, backoff=0.0),
            retryable=lambda _exc: True,
            report=lambda _attempt: None,
            sleep=lambda _seconds: None,
        )


def _write(run_dir: Path, *lines: str) -> Path:
    path = attempts_log(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return path


def _record(**overrides: object) -> str:
    return json.dumps(
        {
            "at": 1.0,
            "role": "orchestrator",
            "attempt": 1,
            "attempts": 3,
            "reason": "the orchestrator turn exited 1 producing no output",
            **overrides,
        }
    )


def test_the_fold_advances_its_cursor_so_a_second_round_does_not_double_count(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _write(run_dir, _record(), _record(attempt=2))

    first = drain_attempts(run_dir)
    assert [item.attempt for item in first] == [1, 2]
    assert drain_attempts(run_dir) == []

    # A later round folds only what accumulated after it.
    with attempts_log(run_dir).open("a", encoding="utf-8") as stream:
        stream.write(_record(role="check-in") + "\n")
    assert [item.role for item in drain_attempts(run_dir)] == ["check-in"]


def test_a_log_that_outgrew_one_fold_is_continued_by_the_next(tmp_path: Path) -> None:
    """A bound on one fold must never become a record nothing can ever reach.

    A byte cap on the file did exactly that: the cursor counts lines, so a prefix
    that stopped growing was a fold that stopped advancing, and every retry recorded
    past the cap was invisible for good. Bounding the *lines examined per fold*
    keeps each round cheap while leaving the log drainable.
    """
    run_dir = tmp_path / "run"
    _write(run_dir, *[_record(attempt=1) for _ in range(MAX_LOG_LINES_PER_FOLD + 5)])

    first = drain_attempts(run_dir)
    assert len(first) == MAX_LOG_LINES_PER_FOLD
    assert len(drain_attempts(run_dir)) == 5
    assert drain_attempts(run_dir) == []


def test_a_line_too_long_to_be_a_record_is_skipped_without_stalling_the_fold(
    tmp_path: Path,
) -> None:
    """Skipping it must advance the cursor, or one bad line freezes every later one."""
    run_dir = tmp_path / "run"
    _write(run_dir, _record(reason="x" * (MAX_LOG_LINE_BYTES + 1)), _record(attempt=2))

    folded = drain_attempts(run_dir)

    assert [item.attempt for item in folded] == [2]
    assert drain_attempts(run_dir) == []


def test_the_fold_reads_nothing_when_no_boundary_request_was_ever_retried(
    tmp_path: Path,
) -> None:
    assert drain_attempts(tmp_path / "run") == []


@pytest.mark.parametrize(
    "line",
    [
        "not json at all",
        json.dumps(["not", "a", "mapping"]),
        _record(at="recently"),
        _record(at=float("inf")),
        _record(role=""),
        _record(role="x" * 41),
        _record(role="carriage\rreturn"),
        _record(attempt=0),
        _record(attempt=True),
        _record(attempts=1, attempt=2),
        _record(at=0),
        _record(at=-1.0),
        _record(attempt=1, attempts=MAX_ATTEMPTS + 1),
        _record(reason=None),
    ],
)
def test_a_record_this_reader_cannot_trust_is_dropped(tmp_path: Path, line: str) -> None:
    """A subprocess wrote this, and the reason reaches a journal and a terminal."""
    run_dir = tmp_path / "run"
    _write(run_dir, line)

    assert drain_attempts(run_dir) == []


def test_a_reason_is_collapsed_redacted_and_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This record outlives the terminal it is printed to, and reaches the journal."""
    monkeypatch.setenv("ORCHESTRATOR_TEST_TOKEN", "sk-live-boundary-value")
    run_dir = tmp_path / "run"
    _write(run_dir, _record(reason="refused\tsk-live-boundary-value\n" + "x" * 400))

    (folded,) = drain_attempts(run_dir)
    assert "\n" not in folded.reason and "\t" not in folded.reason
    assert "sk-live-boundary-value" not in folded.reason
    assert "<redacted:ORCHESTRATOR_TEST_TOKEN>" in folded.reason
    assert len(folded.reason) <= MAX_REASON_CHARS


def test_a_cursor_that_cannot_be_read_folds_from_the_beginning(tmp_path: Path) -> None:
    """Repeating a record loses nothing; skipping one loses the whole point."""
    run_dir = tmp_path / "run"
    path = _write(run_dir, _record())
    path.with_name("boundary-attempts-cursor.json").write_text("{oh dear", encoding="utf-8")

    assert [item.attempt for item in drain_attempts(run_dir)] == [1]


def test_recording_an_attempt_round_trips_through_the_fold(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    record_attempt(
        attempts_log(run_dir),
        BoundaryAttempt(at=12.5, role="check-in", attempt=2, attempts=4, reason="quota"),
    )

    (folded,) = drain_attempts(run_dir)
    assert (folded.at, folded.role, folded.attempt, folded.attempts, folded.reason) == (
        12.5,
        "check-in",
        2,
        4,
        "quota",
    )


def test_a_log_that_cannot_be_written_is_not_what_stops_a_recovery(tmp_path: Path) -> None:
    """The record is lost and the recovery is not: the caller keeps retrying."""
    blocked = tmp_path / "file"
    blocked.write_text("not a directory\n", encoding="utf-8")
    unwritable = blocked / "run"

    record_attempt(
        attempts_log(unwritable),
        BoundaryAttempt(at=1.0, role="orchestrator", attempt=1, attempts=3, reason="quota"),
    )

    # Nothing landed, and nothing raised — the next round simply has nothing to fold.
    assert not attempts_log(unwritable).exists()
    assert drain_attempts(unwritable) == []
