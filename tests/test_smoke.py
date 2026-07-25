"""Deterministic mechanics around the explicitly paid real-harness smoke."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import orchestrator.smoke as smoke
from orchestrator.history import HistoryError, HistorySession, SessionId


def _record(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "type": "run",
                "schema_version": "1.0",
                "history_id": "turn-1",
                "harness": "codex",
                "prompt": smoke.TASK,
                "status": "ok",
                "exit_code": 0,
                "duration_ms": 10,
                "model_ms": 10,
                "tool_ms": 0,
                "started_at": "2026-07-25T00:00:00Z",
                "finished_at": "2026-07-25T00:00:00.010Z",
                "usage": {"cost_usd": None},
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_run_smoke_validates_prompt_and_complete_history(tmp_path, monkeypatch) -> None:
    history = tmp_path / "history.jsonl"
    _record(history)
    session = HistorySession(
        SessionId("smoke-session"),
        "smoke",
        tmp_path,
        "2026-07-25T00:00:00Z",
        history,
        {"role": "agent", "smoke": "smoke-id"},
    )

    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: "smoke-id")
    monkeypatch.setattr(smoke, "_run_wrapper", lambda *_args: None)
    monkeypatch.setattr(smoke, "all_sessions", lambda: [session])
    assert smoke.run_smoke() == smoke.SmokeResult("codex", None)


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
    ("records", "complete", "message"),
    [
        ([{"prompt": "", "harness": "codex"}], True, "did not receive"),
        ([{"prompt": smoke.TASK, "harness": "codex"}], False, "telemetry is incomplete"),
        ([{"prompt": smoke.TASK}], True, "does not identify"),
    ],
)
def test_run_smoke_rejects_broken_record_contracts(
    monkeypatch, records: list[dict[str, object]], complete: bool, message: str
) -> None:
    session = HistorySession(
        SessionId("smoke-session"),
        "smoke",
        Path("/tmp"),
        "2026-07-25T00:00:00Z",
        Path("/tmp/unused"),
        {"role": "agent", "smoke": "smoke-id"},
    )
    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: "smoke-id")
    monkeypatch.setattr(smoke, "_run_wrapper", lambda *_args: None)
    monkeypatch.setattr(smoke, "all_sessions", lambda: [session])
    monkeypatch.setattr(smoke, "session_records", lambda _session: records)
    monkeypatch.setattr(
        smoke, "history_session_is_successful_with_complete_telemetry", lambda _session: complete
    )
    with pytest.raises(HistoryError, match=message):
        smoke.run_smoke()


def test_main_reports_success_and_failure(monkeypatch, capsys) -> None:
    monkeypatch.setattr(smoke, "run_smoke", lambda: smoke.SmokeResult("codex", 0.0123456))
    assert smoke.main() == 0
    assert "$0.012346" in capsys.readouterr().out

    def fail() -> smoke.SmokeResult:
        raise HistoryError("broken")

    monkeypatch.setattr(smoke, "run_smoke", fail)
    assert smoke.main() == 1
    assert "smoke: broken" in capsys.readouterr().err
