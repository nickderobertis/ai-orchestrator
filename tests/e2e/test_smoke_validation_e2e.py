"""Public-command integration coverage for persisted launch-smoke contracts."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import cast

import pytest

from orchestrator import REPO_ROOT
from orchestrator.smoke import TASK

#: The two record shapes oneharness actually persists on the pinned release, copied
#: from live turns. codex reports the native phase split but prices nothing;
#: claude-code prices its turn but records only a measured duration, leaving
#: ``started_at`` and the phase split absent and ``finished_at`` null. Fixtures that
#: invented the missing fields are what let the launch guard ship broken.
CODEX_RECORD: dict[str, object] = {
    "harness": "codex",
    "model": "gpt-5.6-sol",
    "duration_ms": 2969,
    "started_at": "2026-07-25T00:00:00Z",
    "finished_at": "2026-07-25T00:00:02.969Z",
    "model_ms": 2234,
    "tool_ms": 0,
    "time_to_first_token_ms": 2234,
    "text_source": "json:codex-agent-message",
    "usage": {
        "input_tokens": 15472,
        "output_tokens": 7,
        "cache_read_tokens": 13056,
        "cache_write_tokens": None,
        "cost_usd": None,
    },
}
CLAUDE_RECORD: dict[str, object] = {
    "harness": "claude-code",
    "variant": "alternate",
    "harness_id": "claude-code:alternate",
    "model": "claude-opus-5",
    "duration_ms": 3455,
    "finished_at": None,
    "text_source": "stream-json:result",
    "usage": {
        "input_tokens": 2,
        "output_tokens": 8,
        "cache_read_tokens": 15268,
        "cache_write_tokens": 5546,
        "cost_usd": 0.063882,
    },
}


def _record(
    history_dir: Path,
    smoke_id: str,
    *,
    shape: dict[str, object] | None = None,
    suffix: str = "one",
    **overrides: object,
) -> None:
    project = history_dir / "tmp-smoke-target"
    project.mkdir(parents=True, exist_ok=True)
    history_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{smoke_id}-{suffix}"))
    record: dict[str, object] = {
        "type": "run",
        "schema_version": "1.1",
        "history_id": history_id,
        "session": f"smoke-{suffix}",
        "name": "reply-with-exactly-smoke-ok",
        "labels": {"role": "agent", "smoke": smoke_id},
        "project": "/tmp/smoke-target",
        "timestamp": "2026-07-25T00:00:00Z",
        "prompt": TASK,
        "permission_mode": "bypass",
        "status": "ok",
        "exit_code": 0,
        "text": "smoke-ok",
        **(CODEX_RECORD if shape is None else shape),
        **overrides,
    }
    path = project / f"smoke-{suffix}-20260725T000000Z-{history_id}.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")


def _usage(**overrides: object) -> dict[str, object]:
    return {**cast(dict[str, object], CODEX_RECORD["usage"]), **overrides}


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
