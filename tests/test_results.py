from __future__ import annotations

import json
from pathlib import Path

from orchestrator.results import main, render


def _result(runs: Path) -> None:
    round_dir = runs / "demo" / "round-01"
    round_dir.mkdir(parents=True)
    (round_dir / "result.json").write_text(
        json.dumps(
            {
                "schema_version": 4,
                "ok": False,
                "state": "failed",
                "started_order": ["bad", "blocked", "good"],
                "results": {
                    "bad": {
                        "kind": "agent",
                        "status": "failed",
                        "task": "fail",
                        "outcome": "gate-failed",
                        "ok": False,
                        "error": "gate failed",
                        "artifacts": {"gate_log": "/tmp/gate.log"},
                        "steps": [
                            {
                                "id": "work",
                                "kind": "agent",
                                "persona": "engineer",
                                "status": "done",
                                "artifacts": {"worker_report": "/tmp/report.json"},
                            }
                        ],
                    },
                    "blocked": {
                        "kind": "agent",
                        "status": "blocked",
                        "task": "wait",
                        "error": "dependency failed",
                    },
                    "good": {
                        "kind": "agent",
                        "status": "done",
                        "task": "pass",
                        "completed": True,
                        "error": None,
                    },
                },
            }
        )
    )


def test_render_lists_outcomes_detail_and_failure_artifacts(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _result(runs)
    (runs / "demo" / "round-02").mkdir()

    output = render("demo", runs)

    assert "bad  failed  gate-failed" in output
    assert "/tmp/gate.log" in output and "/tmp/report.json" in output
    assert "Full logs: unavailable (node recorded no artifacts)" in output
    assert "good  done  completed" in output
    assert "just history-show graph:demo/1/bad" in output


def test_main_succeeds_for_failed_run_and_rejects_missing_run(tmp_path: Path, capsys) -> None:
    runs = tmp_path / "runs"
    _result(runs)
    assert main(["demo", "--runs-dir", str(runs)]) == 0
    assert "Run demo round-01 — failed" in capsys.readouterr().out

    assert main(["missing", "--runs-dir", str(runs)]) == 2
    assert "no completed round" in capsys.readouterr().err
