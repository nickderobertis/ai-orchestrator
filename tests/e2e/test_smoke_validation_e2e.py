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
from fake_codex import provider_environment
from harness_records import (
    CLAUDE_RECORD,
    CODEX_RECORD,
    reported_usage,
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
    result = subprocess.run(
        ["just", "smoke"],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "ONEHARNESS_HARNESSES": "codex",
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
        ("claude-code", CLAUDE_RECORD, "$0.063882"),
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
