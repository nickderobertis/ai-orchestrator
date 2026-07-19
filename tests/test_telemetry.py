"""Contracts and derivations for the unified machine-readable run index."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import orchestrator.telemetry as telemetry_module
from orchestrator.history import HistorySession, SessionId
from orchestrator.journal import NodeJournal, open_journal
from orchestrator.runs import NodeId, RunId, prepare_round, write_result
from orchestrator.telemetry import (
    SUPPORTED_HISTORY_SCHEMA_VERSIONS,
    TELEMETRY_SCHEMA_VERSION,
    Failure,
    FractionsRecord,
    Provider,
    SessionLink,
    TimingRecord,
    UsageValues,
    _command_class,
    _failure,
    _metrics,
    _summarize_session,
    _timing,
    collect_run,
    main,
)


def _recorded_run(tmp_path: Path, *, state: str = "failed") -> Path:
    run_dir = tmp_path / "runs" / "observed"
    _, round_dir = prepare_round(run_dir, {"tasks": [{"id": "api", "task": "ship"}]})
    journal = open_journal(run_dir, RunId("observed"), 1)
    node = NodeJournal(journal, NodeId("api"), RunId("observed"), 1)
    journal.append("round-started", detail={"nodes": 1})
    node.append("node-started", detail={"persona": "engineer"})
    node.append(
        "verification-started",
        detail={
            "command": ["just", "gate"],
            "comparison_remote": "origin",
            "comparison_base": "main",
        },
    )
    node.append(
        "verification-finished",
        detail={
            "ok": True,
            "reused": True,
            "gate_attestation": {
                "commit": "a" * 40,
                "comparison_remote": "origin",
                "comparison_base": "main",
                "comparison_commit": "d" * 40,
                "command": ["just", "gate"],
                "environment_sha256": "e" * 64,
            },
        },
    )
    node.append("pr-created", detail={"pr": "https://example.test/pull/1"})
    node.append("node-settled", detail={"status": "done" if state == "complete" else "failed"})
    if state == "complete":
        node.append("publication-finished", detail={"pr": "https://example.test/pull/1"})
    journal.append("round-finished", detail={"state": state, "ok": state == "complete"})
    write_result(
        round_dir,
        {
            "ok": state == "complete",
            "state": state,
            "started_order": ["api"],
            "results": {
                "api": {
                    "status": "done" if state == "complete" else "failed",
                    "outcome": "merged" if state == "complete" else "checks-failed",
                    "detail": "required checks failed" if state != "complete" else "",
                    "branch": "orchestrator/api",
                    "pr_base": "main",
                    "resume": {
                        "branch": "orchestrator/api",
                        "base_branch": "main",
                        "pr_base": "main",
                        "checkpoint": "b" * 40,
                        "completed_steps": [],
                        "pr": None,
                    },
                    "retry_lineage": {
                        "supersedes_branch": "orchestrator/old",
                        "supersedes_checkpoint": "c" * 40,
                        "disposition": "recovered",
                    },
                }
            },
        },
    )
    return run_dir


def test_collect_run_joins_ledger_journal_history_and_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = _recorded_run(tmp_path)
    history_path = tmp_path / "history.jsonl"
    history_path.write_text(
        json.dumps(
            {
                "provider": "oneharness",
                "harness": "codex",
                "model": "gpt-5",
                "duration_ms": 2500,
            }
        )
        + "\n"
        + json.dumps(
            {
                "provider": "oneharness",
                "harness": "codex",
                "model": "gpt-5",
                "duration_ms": -1000,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    session = HistorySession(
        SessionId("session"),
        "ship",
        tmp_path,
        "2026-01-01T00:00:00Z",
        history_path,
        {"run_id": "observed", "node": "api", "role": "agent"},
    )
    judge_history = tmp_path / "judge-history.jsonl"
    judge_history.write_text(
        '{"provider":"oneharness","harness":"codex","model":"gpt-5","duration_ms":1000}\n',
        encoding="utf-8",
    )
    judge_session = HistorySession(
        SessionId("judge-session"),
        "you-are-a-strict-careful-evaluator",
        tmp_path,
        "2026-01-01T00:00:01Z",
        judge_history,
        {"run_id": "observed", "role": "judge"},
    )
    monkeypatch.setattr(
        telemetry_module, "all_sessions", lambda **_kwargs: [session, judge_session]
    )

    telemetry = collect_run(run_dir, now=9999999999.0)
    assert telemetry is not None
    record = telemetry.record()
    assert record["providers"] == [{"provider": "oneharness", "harness": "codex", "model": "gpt-5"}]
    assert record["failure"] == {"class": "checks", "detail": "required checks failed"}
    assert record["timing"]["agent_seconds"] == 2.5
    assert record["timing"]["judge_seconds"] == 1.0
    assert record["timing"]["agent_model_ms"] == 0
    assert record["timing"]["unattributed_ms"] > 0
    assert record["telemetry_quality"] == "legacy"
    node = record["nodes"][0]
    assert node["checkpoint"] == "b" * 40
    assert node["comparison_remote"] == "origin"
    assert node["gate_attestation"]["commit"] == "a" * 40
    assert node["retry_lineage"]["disposition"] == "recovered"


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"status": "failed", "outcome": "timeout", "detail": "timed out"}, "timeout"),
        ({"status": "failed", "outcome": "gate-failed", "detail": "bad"}, "gate"),
        ({"status": "failed", "outcome": "closed", "detail": "closed"}, "publication"),
        ({"status": "failed", "detail": "provider error"}, "provider"),
        ({"status": "failed", "detail": "bad config"}, "configuration"),
        ({"status": "failed", "detail": "agent stopped"}, "agent"),
        ({"status": "done"}, None),
    ],
)
def test_failure_class_is_typed_and_deterministic(item, expected) -> None:
    found = _failure(item)
    assert (found.classification if found else None) == expected


def test_optional_record_fields_are_omitted() -> None:
    assert Failure("unknown").record() == {"class": "unknown"}
    assert Provider("oneharness").record() == {"provider": "oneharness"}


def test_schema_v2_field_golden_prevents_cross_layer_drift() -> None:
    golden = json.loads(
        (Path(__file__).parent / "golden" / "telemetry-v2-fields.json").read_text(encoding="utf-8")
    )
    assert golden == {
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "history_schema_versions": list(SUPPORTED_HISTORY_SCHEMA_VERSIONS),
        "roles": ["agent", "judge"],
        "qualities": ["complete", "legacy", "partial"],
        "sources": ["history_legacy", "journal_legacy", "oneharness", "onejudge"],
        "timing": sorted(TimingRecord.__required_keys__),
        "fractions": sorted(FractionsRecord.__required_keys__),
        "usage": sorted(UsageValues.__required_keys__),
        "session_link": sorted(SessionLink.__required_keys__),
    }
    contract = (Path(__file__).parents[1] / "docs" / "telemetry-model.md").read_text(
        encoding="utf-8"
    )
    assert "schema version 1 to 2" in contract
    for value in (*golden["roles"], *golden["qualities"], *golden["sources"]):
        assert f"`{value}`" in contract


def test_index_cli_defaults_to_active_and_all_includes_settled(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _recorded_run(tmp_path)
    completed = _recorded_run(tmp_path / "complete", state="complete")
    completed.rename(tmp_path / "runs" / "complete")
    assert main(["--runs-dir", str(tmp_path / "runs"), "--oneharness-bin", "absent"]) == 0
    active = json.loads(capsys.readouterr().out)
    assert active["schema_version"] == 2
    assert active["runs"] == []
    assert active["metrics"]["recovered_branches"] == 0

    assert main(["--runs-dir", str(tmp_path / "runs"), "--all", "--oneharness-bin", "absent"]) == 0
    all_runs = json.loads(capsys.readouterr().out)
    assert {run["run_id"] for run in all_runs["runs"]} == {"observed", "complete"}
    assert _metrics([])["retry_attempts"] == 0
    assert main(["--runs-dir", str(tmp_path / "missing")]) == 0
    assert json.loads(capsys.readouterr().out)["runs"] == []


def test_native_timing_usage_tools_and_breakdown_are_role_and_node_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = _recorded_run(tmp_path, state="complete")

    def session(role: str, duration: int, model: int, tool: int) -> HistorySession:
        path = tmp_path / f"{role}.jsonl"
        path.write_text(
            json.dumps(
                {
                    "duration_ms": duration,
                    "model_ms": model,
                    "tool_ms": tool,
                    "usage": {
                        "input_tokens": 10 if role == "agent" else 3,
                        "output_tokens": 5 if role == "agent" else 2,
                        "cache_read_tokens": 4,
                        "cache_write_tokens": 1,
                        "cost_usd": 0.02 if role == "agent" else 0.01,
                    },
                    "events": [
                        {
                            "kind": "tool_call",
                            "name": "command_execution",
                            "duration_ms": tool,
                            "input": {"command": "just gate"},
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return HistorySession(
            SessionId(f"{role}-session"),
            role,
            tmp_path,
            "2026-01-01T00:00:00Z",
            path,
            {"run_id": "observed", "node": "api", "role": role},
        )

    sessions = [session("agent", 20, 12, 5), session("judge", 10, 8, 0)]
    monkeypatch.setattr(telemetry_module, "all_sessions", lambda **_kwargs: sessions)
    telemetry = collect_run(run_dir)
    assert telemetry is not None
    record = telemetry.record()
    assert record["timing"]["agent_model_ms"] == 12
    assert record["timing"]["judge_model_ms"] == 8
    assert record["timing"]["tool_ms"] == 5
    assert record["usage"]["total"]["input_tokens"] == 13
    assert record["usage"]["total"]["cost_usd"] == pytest.approx(0.03)
    assert record["nodes"][0]["sessions"][1]["role"] == "judge"
    assert record["nodes"][0]["tool_commands"] == {"gate": 2}
    assert record["telemetry_quality"] == "legacy"
    assert main(["--runs-dir", str(tmp_path / "runs"), "--all", "--breakdown"]) == 0
    assert "Turn histogram:" in capsys.readouterr().out


def test_session_normalization_degrades_each_field_independently(tmp_path: Path) -> None:
    session = HistorySession(
        SessionId("legacy"),
        "ordinary",
        tmp_path,
        "2026-01-01T00:00:00Z",
        tmp_path / "x",
        {"role": "judge", "node": "api"},
    )
    summary = _summarize_session(
        session,
        [
            {
                "duration_ms": 9,
                "usage": {"input_tokens": 2, "output_tokens": True},
                "events": [
                    "bad",
                    {"kind": "message"},
                    {"kind": "tool_call", "name": "other", "duration_ms": 3},
                    {"kind": "tool_call", "name": "bash", "input": {"cmd": "git status"}},
                    {"kind": "tool_call", "name": "bash", "input": "bad"},
                ],
            },
            {"duration_ms": True, "events": "bad"},
        ],
    )
    assert summary.role == "judge"
    assert summary.duration_ms == 9
    assert summary.tool_ms == 3
    assert summary.commands == {"git": 1}
    assert summary.usage["input_tokens"] is None
    assert summary.usage["output_tokens"] is None
    assert not summary.interval_complete
    assert _command_class("just check") == "just"
    assert _command_class("") == "unknown"
    zero = _timing(0, [summary])
    assert set(zero["fractions"].values()) == {0.0}
    with pytest.raises(telemetry_module.HistoryError, match="unsupported.*schema"):
        _summarize_session(session, [{"schema_version": 99}])
