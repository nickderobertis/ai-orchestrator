from __future__ import annotations

import json
from pathlib import Path

from orchestrator.dispatch import classify_provider_failure
from orchestrator.journal import NodeId, open_journal
from orchestrator.provider_health import IDENTITIES, failure_rollups, probe, render
from orchestrator.runs import RunId


def test_provider_failure_vocabulary_and_bounded_payload() -> None:
    quota = classify_provider_failure(
        "judge-side codex:alternate quota exhausted; resets Aug 8 " + "x" * 3000
    )
    assert quota is not None
    assert quota["side"] == "judge"
    assert quota["identity"] == "codex:alternate"
    assert quota["cause"] == "quota_mid_conversation"
    assert len(quota["raw_tail"]) == 2000

    stale = classify_provider_failure(
        "claude-code:alternate2 harness: No conversation found with session ID dead-beef"
    )
    assert stale is not None
    assert stale["cause"] == "stale_session_resume"
    assert stale["missing_session_id"] == "dead-beef"

    rate = classify_provider_failure(
        "agent claude-code:primary rate limit watchdog killed retry loop after 12 minutes"
    )
    assert rate is not None
    assert rate["cause"] == "rate_limit"
    assert rate["wait_seconds"] == 720


def test_probe_keeps_all_configured_identities_and_renders_unknown(monkeypatch) -> None:
    payload = {
        "schema_version": "0.1",
        "identities": [
            {
                "harness": "codex",
                "availability": {
                    "state": "available",
                    "windows": [
                        {
                            "id": "weekly",
                            "usage": {"kind": "metered", "used_percent": 1.0},
                            "resets_at": "2026-08-08T00:00:00Z",
                        }
                    ],
                },
            }
        ],
    }

    class Result:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""

    monkeypatch.setattr("orchestrator.provider_health.subprocess.run", lambda *a, **k: Result())
    snapshot = probe(cwd=Path("."))
    assert [item["identity"] for item in snapshot["identities"]] == list(IDENTITIES)
    shown = render(snapshot)
    assert "codex: weekly binding, 100% used, resets 2026-08-08" in shown
    assert "claude-code:alternate: unknown" in shown


def test_failure_rollup_collapses_same_cause(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    journal = open_journal(run_dir, RunId("run"), 1)
    fact = {
        "side": "judge",
        "identity": "codex:primary",
        "cause": "quota_mid_conversation",
        "reset_time": "Aug 8",
    }
    for node in ("a", "b", "c"):
        journal.append("node-failed", node=NodeId(node), detail={"failure_attribution": fact})
    assert failure_rollups(run_dir) == [
        "3 nodes failed on judge-side codex:primary quota mid conversation, resets Aug 8"
    ]
