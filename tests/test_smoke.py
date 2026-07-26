"""Deterministic mechanics around the explicitly paid real-harness smoke."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import orchestrator.smoke as smoke
from orchestrator.history import HistoryError, HistorySession, SessionId
from orchestrator.telemetry import history_session_launch_failure


def _record(path: Path, **overrides: object) -> None:
    """Write the record claude-code actually persists: a duration and nothing else.

    No ``started_at``, no ``model_ms`` / ``tool_ms`` phase split, and a null
    ``finished_at`` — the shape that broke the launch guard while fixtures that
    invented those fields stayed green.
    """
    path.write_text(
        json.dumps(
            {
                "type": "run",
                "schema_version": "1.1",
                "history_id": "turn-1",
                "harness": "claude-code",
                "harness_id": "claude-code:alternate",
                "model": "claude-opus-5",
                "prompt": smoke.TASK,
                "status": "ok",
                "exit_code": 0,
                "duration_ms": 3455,
                "finished_at": None,
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": 8,
                    "cache_read_tokens": 15268,
                    "cache_write_tokens": 5546,
                    "cost_usd": 0.063882,
                },
                **overrides,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _session(tmp_path: Path, history: Path) -> HistorySession:
    return HistorySession(
        SessionId("smoke-session"),
        "smoke",
        tmp_path,
        "2026-07-25T00:00:00Z",
        history,
        {"role": "agent", "smoke": "smoke-id"},
    )


def test_run_smoke_validates_prompt_and_launch_contract(tmp_path, monkeypatch) -> None:
    history = tmp_path / "history.jsonl"
    _record(history)

    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: "smoke-id")
    monkeypatch.setattr(smoke, "_run_wrapper", lambda *_args: None)
    monkeypatch.setattr(smoke, "all_sessions", lambda: [_session(tmp_path, history)])
    assert smoke.run_smoke() == smoke.SmokeResult("claude-code", 0.063882)


def test_run_smoke_accepts_the_codex_record_shape(tmp_path, monkeypatch) -> None:
    """The other real shape: a native phase split, no cache write, and no price."""
    history = tmp_path / "history.jsonl"
    _record(
        history,
        harness="codex",
        harness_id="codex",
        model="gpt-5.6-sol",
        duration_ms=2969,
        started_at="2026-07-25T00:00:00Z",
        finished_at="2026-07-25T00:00:02.969Z",
        model_ms=2234,
        tool_ms=0,
        usage={
            "input_tokens": 15472,
            "output_tokens": 7,
            "cache_read_tokens": 13056,
            "cache_write_tokens": None,
            "cost_usd": None,
        },
    )

    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: "smoke-id")
    monkeypatch.setattr(smoke, "_run_wrapper", lambda *_args: None)
    monkeypatch.setattr(smoke, "all_sessions", lambda: [_session(tmp_path, history)])
    assert smoke.run_smoke() == smoke.SmokeResult("codex", None)


def test_validation_mode_loads_isolated_store_through_public_cli(tmp_path: Path, capsys) -> None:
    project = tmp_path / "project"
    project.mkdir()
    history = project / "smoke.jsonl"
    _record(history)
    record = json.loads(history.read_text(encoding="utf-8"))
    record.update(
        session="smoke-session",
        name="smoke",
        project="/tmp/smoke-target",
        timestamp="2026-07-25T00:00:00Z",
        labels={"role": "agent", "smoke": "smoke-id", "ignored": 1},
    )
    history.write_text(json.dumps(record) + "\n", encoding="utf-8")
    (project / "malformed.jsonl").write_text("{bad json\n", encoding="utf-8")
    (project / "events-only.jsonl").write_text('{"type":"event"}\n', encoding="utf-8")

    sessions = smoke._stored_sessions(tmp_path)

    assert len(sessions) == 1
    assert sessions[0].labels == {"role": "agent", "smoke": "smoke-id"}
    assert smoke.main(["--validate-history", str(tmp_path), "--smoke-id", "smoke-id"]) == 0
    assert "smoke: passed via claude-code (recorded cost: $0.063882)" in capsys.readouterr().out


@pytest.mark.parametrize("reported_cost", ["malformed", math.inf, -math.inf, math.nan])
def test_run_smoke_renders_invalid_recorded_cost_as_unreported(
    tmp_path: Path, monkeypatch, capsys, reported_cost: object
) -> None:
    history = tmp_path / "history.jsonl"
    _record(history)
    record = json.loads(history.read_text(encoding="utf-8"))
    record["usage"]["cost_usd"] = reported_cost
    history.write_text(json.dumps(record) + "\n", encoding="utf-8")
    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: "smoke-id")
    monkeypatch.setattr(smoke, "_run_wrapper", lambda *_args: None)
    monkeypatch.setattr(smoke, "all_sessions", lambda: [_session(tmp_path, history)])

    assert smoke.main([]) == 0
    assert "recorded cost: unreported" in capsys.readouterr().out


def test_wrapper_surfaces_backgrounded_harness_failure(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / "oneharness-agent.sh"
    wrapper.write_text(
        "#!/bin/sh\n"
        "cat >/dev/null\n"
        'touch "$ORCHESTRATOR_AGENT_STATUS_DIR/agent.failed"\n'
        'echo "provider rejected task" >&2\n'
        "while :; do sleep 1; done\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    status = tmp_path / "status"
    status.mkdir()
    monkeypatch.setattr(smoke, "REPO_ROOT", root)

    with pytest.raises(HistoryError, match="provider rejected task"):
        smoke._run_wrapper(
            tmp_path, status, tmp_path / "history", "smoke-id", smoke.TIMEOUT_SECONDS
        )


@pytest.mark.parametrize(
    ("body", "timeout", "message"),
    [
        ("cat >/dev/null\nexit 3\n", smoke.TIMEOUT_SECONDS, "exit 3"),
        ("cat >/dev/null\nwhile :; do sleep 1; done\n", -10, "timed out"),
    ],
)
def test_wrapper_surfaces_exit_and_timeout(
    tmp_path, monkeypatch, body: str, timeout: int, message: str
) -> None:
    root = tmp_path / "repo"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / "oneharness-agent.sh"
    wrapper.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
    wrapper.chmod(0o755)
    status = tmp_path / "status"
    status.mkdir()
    monkeypatch.setattr(smoke, "REPO_ROOT", root)
    with pytest.raises(HistoryError, match=message):
        smoke._run_wrapper(tmp_path, status, tmp_path / "history", "smoke-id", timeout)


def test_run_smoke_rejects_missing_matching_history(monkeypatch) -> None:
    monkeypatch.setattr(smoke, "_run_wrapper", lambda *_args: None)
    monkeypatch.setattr(smoke, "all_sessions", lambda: [])
    with pytest.raises(HistoryError, match="found 0"):
        smoke.run_smoke()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"prompt": "a different task"}, "did not receive the dispatched task"),
        ({"harness": ""}, "does not identify the selected harness"),
        ({"status": "error"}, r"records status 'error' with exit code 0"),
        ({"exit_code": 1}, r"records status 'ok' with exit code 1"),
        ({"schema_version": 99}, "records unsupported history schema 99"),
        ({"duration_ms": None}, "records no measured duration"),
        ({"usage": None}, "reports no token accounting"),
        ({"usage": {"output_tokens": 8}}, "reports no input_tokens"),
        ({"usage": {"input_tokens": 2, "output_tokens": "eight"}}, "malformed output_tokens"),
    ],
)
def test_run_smoke_rejects_broken_record_contracts(
    tmp_path: Path, monkeypatch, overrides: dict[str, object], message: str
) -> None:
    history = tmp_path / "history.jsonl"
    _record(history, **overrides)
    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: "smoke-id")
    monkeypatch.setattr(smoke, "_run_wrapper", lambda *_args: None)
    monkeypatch.setattr(smoke, "all_sessions", lambda: [_session(tmp_path, history)])
    with pytest.raises(HistoryError, match=message):
        smoke.run_smoke()


def test_launch_contract_rejects_a_session_that_never_reached_a_harness(tmp_path: Path) -> None:
    history = tmp_path / "history.jsonl"
    history.write_text('{"type": "event", "run_id": "turn-1", "event": {}}\n', encoding="utf-8")

    assert history_session_launch_failure(_session(tmp_path, history)) == "recorded no harness run"


def test_main_reports_success_and_failure(monkeypatch, capsys) -> None:
    monkeypatch.setattr(smoke, "run_smoke", lambda: smoke.SmokeResult("codex", 0.0123456))
    assert smoke.main([]) == 0
    assert "$0.012346" in capsys.readouterr().out

    def fail() -> smoke.SmokeResult:
        raise HistoryError("broken")

    monkeypatch.setattr(smoke, "run_smoke", fail)
    assert smoke.main([]) == 1
    assert "smoke: broken" in capsys.readouterr().err


def test_timeout_override_is_bounded(monkeypatch) -> None:
    monkeypatch.delenv("ORCHESTRATOR_SMOKE_TIMEOUT_SECONDS", raising=False)
    assert smoke._timeout_seconds() == smoke.TIMEOUT_SECONDS
    monkeypatch.setenv("ORCHESTRATOR_SMOKE_TIMEOUT_SECONDS", "7")
    assert smoke._timeout_seconds() == 7
    for invalid in ("bad", "0", str(smoke.TIMEOUT_SECONDS + 1)):
        monkeypatch.setenv("ORCHESTRATOR_SMOKE_TIMEOUT_SECONDS", invalid)
        with pytest.raises(HistoryError, match="must be between"):
            smoke._timeout_seconds()
