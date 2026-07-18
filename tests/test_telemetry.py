"""Contracts and derivations for the unified machine-readable run index."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import orchestrator.telemetry as telemetry_module
from orchestrator.history import HistorySession, SessionId
from orchestrator.journal import NodeJournal, open_journal
from orchestrator.runs import NodeId, RunId, prepare_round, write_result
from orchestrator.telemetry import Failure, Provider, _failure, _metrics, collect_run, main


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
        + "\n",
        encoding="utf-8",
    )
    session = HistorySession(
        SessionId("session"),
        "ship",
        tmp_path,
        "2026-01-01T00:00:00Z",
        history_path,
        {"run_id": "observed"},
    )
    monkeypatch.setattr(telemetry_module, "worker_sessions", lambda **_kwargs: [session])

    telemetry = collect_run(run_dir, now=9999999999.0)
    assert telemetry is not None
    record = telemetry.record()
    assert record["providers"] == [{"provider": "oneharness", "harness": "codex", "model": "gpt-5"}]
    assert record["failure"] == {"class": "checks", "detail": "required checks failed"}
    assert record["timing"]["agent_seconds"] == 2.5
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


def test_index_cli_defaults_to_active_and_all_includes_complete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _recorded_run(tmp_path)
    completed = _recorded_run(tmp_path / "complete", state="complete")
    completed.rename(tmp_path / "runs" / "complete")
    assert main(["--runs-dir", str(tmp_path / "runs"), "--oneharness-bin", "absent"]) == 0
    active = json.loads(capsys.readouterr().out)
    assert active["schema_version"] == 1
    assert [run["run_id"] for run in active["runs"]] == ["observed"]
    assert active["metrics"]["recovered_branches"] == 1

    assert main(["--runs-dir", str(tmp_path / "runs"), "--all", "--oneharness-bin", "absent"]) == 0
    all_runs = json.loads(capsys.readouterr().out)
    assert {run["run_id"] for run in all_runs["runs"]} == {"observed", "complete"}
    assert _metrics([])["retry_attempts"] == 0
    assert main(["--runs-dir", str(tmp_path / "missing")]) == 0
    assert json.loads(capsys.readouterr().out)["runs"] == []
