"""Public-command integration coverage for persisted launch-smoke contracts.

llmlint: ignore-file[e2e_not_mocked] The paid agent harness is the one boundary
this repository fakes. Where a journey below launches for real it does so through
`tests/e2e/fake_codex.py` at oneharness's own `ONEHARNESS_BIN_CODEX` seam, leaving
the recipe, the wrapper, oneharness, and the persisted history store real.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest
from fake_codex import provider_environment, uninstalled_provider_environment
from harness_records import (
    AUTH_REFUSAL,
    CLAUDE_ALTERNATE2_RECORD,
    CLAUDE_RECORD,
    CODEX_RECORD,
    QUOTA_REFUSAL,
    RATE_LIMITED_RECORD,
    reported_usage,
    smoke_history_chain,
    smoke_history_record,
)
from waits import timeout as e2e_timeout

from orchestrator import REPO_ROOT
from orchestrator.smoke import LAUNCH_ATTEMPTS


def _record(
    history_dir: Path,
    smoke_id: str,
    *,
    shape: dict[str, object] = CODEX_RECORD,
    suffix: str = "one",
    **overrides: object,
) -> None:
    """Persist one real provider record where `orchestrator-smoke` will read it."""
    project = history_dir / "tmp-smoke-target"
    project.mkdir(parents=True, exist_ok=True)
    history_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{smoke_id}-{suffix}"))
    record = smoke_history_record(
        shape,
        smoke_id=smoke_id,
        session=f"smoke-{suffix}",
        history_id=history_id,
        **overrides,
    )
    path = project / f"smoke-{suffix}-20260725T000000Z-{history_id}.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")


def _chain(
    history_dir: Path, smoke_id: str, *shapes: dict[str, object], suffix: str = "chain"
) -> None:
    """Persist one session recording every candidate a fallback chain attempted.

    One file, because that is how oneharness stores one turn's chain: the candidates
    it refused and the one it selected are records of the same session, in the order
    it tried them.
    """
    project = history_dir / "tmp-smoke-target"
    project.mkdir(parents=True, exist_ok=True)
    history_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{smoke_id}-{suffix}"))
    path = project / f"smoke-{suffix}-20260725T000000Z-{history_id}.jsonl"
    path.write_text(
        smoke_history_chain(*shapes, smoke_id=smoke_id, session=f"smoke-{suffix}"),
        encoding="utf-8",
    )


def _usage(**overrides: object) -> dict[str, object]:
    return reported_usage(CODEX_RECORD, **overrides)


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


def test_smoke_stops_after_one_launch_when_the_recorded_turn_breaks_its_contract(
    tmp_path: Path,
) -> None:
    """A broken record is the regression this smoke exists to report, not weather.

    The launch is retried because a loaded host can kill one while the launch path
    is fine. What the recorded turn *says* is this repository's own contract, so a
    turn that violates it must stop the smoke on the first launch rather than
    buying the same verdict twice more — and that is precisely the behaviour a
    monkeypatched `_run_wrapper` cannot prove, because the whole claim is about how
    much real quota gets spent.

    So this drives the real recipe, wrapper, `oneharness`, and history store, and
    fakes only the paid provider CLI: it returns a turn with no token accounting,
    which oneharness persists and the launch contract then rejects — a launch that
    itself succeeded, failing on what it recorded. The attempt log is the evidence:
    one line, not three.
    """
    launches = tmp_path / "paid-launches"

    result = subprocess.run(
        ["just", "smoke"],
        cwd=REPO_ROOT,
        env=provider_environment(attempt_log=launches, omit_usage=True),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
    )

    assert result.returncode == 1
    assert "real harness history reports no input_tokens" in result.stderr
    assert "rerun 'just smoke'" in result.stderr
    # Not "(after N attempts)": that suffix is the launch-retry path's, and taking
    # it here would mean the smoke had paid for two more turns to be told the same
    # thing.
    assert f"after {LAUNCH_ATTEMPTS} attempts" not in result.stderr
    assert launches.read_text(encoding="utf-8").splitlines() == ["launch"]


def test_smoke_command_surfaces_real_wrapper_failure_without_a_paid_turn() -> None:
    """A launch path that cannot start is reported, and finding that out costs nothing.

    Narrowing the selection is what makes it cost nothing, and it is the only thing that
    can: this used to also point `ONEHARNESS_BIN_CLAUDE_CODE` at nothing, as a second
    guard against the committed chain's first candidate spending a turn. That variable
    does not reach a *variant* — `claude-code:alternate` resolves the real `claude`
    whatever it is set to — so it guarded nothing, and dropping the narrowing spends a
    real turn and passes the smoke. The second half of this test's name is therefore
    asserted rather than assumed, in oneharness' own words for having run nothing.

    The selection is built rather than spelled inline for the same reason it is in
    the journeys above: a dispatch's `ORCHESTRATOR_WORKER_HARNESSES` beats the
    narrowing, and inheriting it here would launch a paid identity that starts.
    """
    result = subprocess.run(
        ["just", "smoke"],
        cwd=REPO_ROOT,
        env=uninstalled_provider_environment(),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
    )

    assert result.returncode == 1
    assert "real harness smoke failed" in result.stderr
    assert "rerun 'just smoke'" in result.stderr
    assert "all 1 fallback candidate(s) failed to start (codex [not-installed])" in result.stderr
    assert "nothing executed" in result.stderr


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
    ("case", "overrides", "diagnostic"),
    [
        ("unreached-task", {"prompt": "wrong task"}, "did not receive the dispatched task"),
        ("empty-task", {"prompt": ""}, "did not receive the dispatched task"),
        ("unidentified-harness", {"harness": ""}, "does not identify the selected harness"),
        ("nonstring-harness", {"harness": 7}, "does not identify the selected harness"),
        ("failed-status", {"status": "error"}, "records status 'error' with exit code 0"),
        ("nonzero-exit", {"exit_code": 2}, "records status 'ok' with exit code 2"),
        ("unmeasured-duration", {"duration_ms": None}, "records no measured duration"),
        ("unsupported-schema", {"schema_version": "9.9"}, "unsupported history schema '9.9'"),
        ("absent-usage", {"usage": None}, "reports no token accounting"),
        ("nondict-usage", {"usage": 12}, "reports no token accounting"),
        ("absent-input-tokens", {"usage": _usage(input_tokens=None)}, "reports no input_tokens"),
        ("absent-output-tokens", {"usage": _usage(output_tokens=None)}, "reports no output_tokens"),
        (
            "malformed-input-tokens",
            {"usage": _usage(input_tokens="many")},
            "reports malformed input_tokens 'many'",
        ),
        (
            "negative-output-tokens",
            {"usage": _usage(output_tokens=-1)},
            "reports malformed output_tokens -1",
        ),
        (
            "malformed-optional-counter",
            {"usage": _usage(cache_write_tokens=1.5)},
            "reports malformed cache_write_tokens 1.5",
        ),
    ],
)
def test_validation_command_rejects_broken_persisted_contract(
    tmp_path: Path, case: str, overrides: dict[str, object], diagnostic: str
) -> None:
    smoke_id = f"broken-{case}"
    _record(tmp_path, smoke_id, **overrides)

    result = _validate(tmp_path, smoke_id)

    assert result.returncode == 1
    assert diagnostic in result.stderr


def test_validation_command_rejects_a_session_that_never_reached_a_harness(tmp_path: Path) -> None:
    project = tmp_path / "tmp-smoke-target"
    project.mkdir(parents=True)
    (project / "smoke-empty-20260725T000000Z-empty.jsonl").write_text(
        json.dumps(
            {
                "type": "meta",
                "session": "smoke-empty",
                "name": "reply-with-exactly-smoke-ok",
                "labels": {"role": "agent", "smoke": "no-run"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = _validate(tmp_path, "no-run")

    assert result.returncode == 1
    assert "expected one smoke history session, found 0" in result.stderr


@pytest.mark.parametrize(
    ("harness", "shape", "rendered_cost"),
    [
        ("codex", CODEX_RECORD, "unreported"),
        ("claude-code:alternate", CLAUDE_RECORD, "$0.063882"),
    ],
)
def test_validation_command_accepts_each_real_harness_record_shape(
    tmp_path: Path, harness: str, shape: dict[str, object], rendered_cost: str
) -> None:
    """Neither shipped harness reports every native field; both are healthy launches."""
    smoke_id = f"real-{harness}"
    _record(tmp_path, smoke_id, shape=shape)

    result = _validate(tmp_path, smoke_id)

    assert result.returncode == 0, result.stderr
    assert f"smoke: passed via {harness} (recorded cost: {rendered_cost})" in result.stdout


def test_validation_command_passes_a_chain_that_fell_through_a_refused_candidate(
    tmp_path: Path,
) -> None:
    """The public command reads the launch path's outcome off the selected record.

    This is the shape this host writes today: `claude-code:alternate` is out of
    weekly quota, the chain hands the turn to `claude-code:alternate2`, and both
    records land in one session. Failing on the refusal blocked every push whose
    diff selects this smoke, for a launch path that was working.
    """
    smoke_id = "fell-through"
    _chain(tmp_path, smoke_id, QUOTA_REFUSAL, CLAUDE_ALTERNATE2_RECORD)

    result = _validate(tmp_path, smoke_id)

    assert result.returncode == 0, result.stderr
    assert "smoke: fell through claude-code:alternate (quota)" in result.stdout
    assert "smoke: passed via claude-code:alternate2 (recorded cost: $0.063882)" in result.stdout


@pytest.mark.parametrize(
    ("case", "shapes", "diagnostic"),
    [
        (
            "selected-failed",
            (QUOTA_REFUSAL, {**CODEX_RECORD, "status": "error"}),
            "records status 'error' with exit code 0",
        ),
        (
            "selected-unaccounted",
            (QUOTA_REFUSAL, {**CODEX_RECORD, "usage": None}),
            "reports no token accounting",
        ),
        (
            "unclassified-candidate",
            (RATE_LIMITED_RECORD, CODEX_RECORD),
            "real harness candidate claude-code:alternate failed unclassified",
        ),
        (
            "chain-exhausted",
            (QUOTA_REFUSAL, AUTH_REFUSAL),
            "no candidate left to run the task: claude-code:alternate (quota), "
            "claude-code:alternate2 (auth)",
        ),
    ],
)
def test_validation_command_still_fails_a_chain_that_did_not_run_the_task(
    tmp_path: Path, case: str, shapes: tuple[dict[str, object], ...], diagnostic: str
) -> None:
    """Falling through excuses the candidate, never the launch path's own outcome."""
    smoke_id = f"chain-{case}"
    _chain(tmp_path, smoke_id, *shapes, suffix=case)

    result = _validate(tmp_path, smoke_id)

    assert result.returncode == 1
    assert diagnostic in result.stderr
    assert "rerun 'just smoke'" in result.stderr


def test_validation_command_rejects_multiple_matching_sessions(tmp_path: Path) -> None:
    smoke_id = "duplicate"
    _record(tmp_path, smoke_id, suffix="one")
    _record(tmp_path, smoke_id, suffix="two")

    result = _validate(tmp_path, smoke_id)

    assert result.returncode == 1
    assert "expected one smoke history session, found 2" in result.stderr


def test_validation_command_renders_numeric_recorded_cost_in_dollars(tmp_path: Path) -> None:
    smoke_id = "reported-cost"
    _record(tmp_path, smoke_id, usage=_usage(cost_usd=0.0123456))

    result = _validate(tmp_path, smoke_id)

    assert result.returncode == 0, result.stderr
    assert "smoke: passed via codex (recorded cost: $0.012346)" in result.stdout


@pytest.mark.parametrize("cost", ["malformed", float("inf"), float("-inf"), float("nan")])
def test_validation_command_renders_invalid_recorded_cost_as_unreported(
    tmp_path: Path, cost: object
) -> None:
    """An unpriceable turn is a reporting gap, not a launch failure."""
    smoke_id = "invalid-cost"
    _record(tmp_path, smoke_id, usage=_usage(cost_usd=cost))

    result = _validate(tmp_path, smoke_id)

    assert result.returncode == 0, result.stderr
    assert "recorded cost: unreported" in result.stdout
