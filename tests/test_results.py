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
                        "failure_attribution": {
                            "side": "judge",
                            "identity": "codex",
                            "cause": "quota_mid_conversation",
                            "reset_time": "Aug 8",
                        },
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
    assert "Provider: judge-side codex quota mid conversation, resets Aug 8" in output


def test_main_succeeds_for_failed_run_and_rejects_missing_run(tmp_path: Path, capsys) -> None:
    runs = tmp_path / "runs"
    _result(runs)
    assert main(["demo", "--runs-dir", str(runs)]) == 0
    assert "Run demo round-01 — failed" in capsys.readouterr().out

    assert main(["missing", "--runs-dir", str(runs)]) == 2
    assert "no completed round" in capsys.readouterr().err


def test_render_names_the_branch_a_parked_node_preserved(tmp_path: Path) -> None:
    """A park is idle, not lost, so the view has to say where the work is."""
    runs = tmp_path / "runs"
    round_dir = runs / "parked" / "round-01"
    round_dir.mkdir(parents=True)
    (round_dir / "result.json").write_text(
        json.dumps(
            {
                "schema_version": 5,
                "ok": False,
                "state": "waiting",
                "started_order": ["work"],
                "results": {
                    "work": {
                        "kind": "agent",
                        "status": "parked",
                        "task": "Sweep",
                        "outcome": "not-completed",
                        "branch": "feature/preserved",
                        "error": "cancelled cooperatively; parked by planner",
                    },
                    "never-started": {
                        "kind": "agent",
                        "status": "parked",
                        "task": "Later",
                        "error": "parked by planner",
                    },
                },
            }
        )
    )

    output = render("parked", runs)

    assert "work  parked  not-completed" in output
    assert "Preserved branch: feature/preserved" in output
    assert "Preserved branch: none (parked before it started)" in output


def test_render_carries_the_reason_and_marks_a_transcript_it_elides(tmp_path: Path) -> None:
    """An outcome names a category; only the reason says which one it was.

    Both halves belong on the same read, so the recorded sentence is rendered beside
    the status. A merge or rebase failure records its whole transcript there, which
    is what `Full logs` is for — so this shows the first line and *says* it stopped,
    because a silently truncated reason reads as a complete one.
    """
    runs = tmp_path / "runs"
    round_dir = runs / "reasoned" / "round-01"
    round_dir.mkdir(parents=True)
    (round_dir / "result.json").write_text(
        json.dumps(
            {
                "schema_version": 5,
                "ok": False,
                "state": "failed",
                "started_order": ["stale-pin", "conflicted", "blocked"],
                "results": {
                    "stale-pin": {
                        "kind": "agent",
                        "status": "failed",
                        "task": "Continue",
                        "outcome": "resume-failed",
                        "error": (
                            "resume-failed: cannot check whether the branch still carries "
                            "unattested incomplete provenance over origin/feature/prereq"
                        ),
                    },
                    "conflicted": {
                        "kind": "agent",
                        "status": "failed",
                        "task": "Publish",
                        "outcome": "sync-conflict",
                        "detail": "git rebase failed (exit 1): Rebasing (1/34)\nRebasing (2/34)\n",
                    },
                    "blocked": {
                        "kind": "agent",
                        "status": "blocked",
                        "task": "Wait",
                        "error": "dependency failed",
                    },
                },
            }
        )
    )

    output = render("reasoned", runs)

    assert "stale-pin  failed  resume-failed" in output
    assert (
        "  Reason: resume-failed: cannot check whether the branch still carries "
        "unattested incomplete provenance over origin/feature/prereq" in output
    )
    assert "  Reason: git rebase failed (exit 1): Rebasing (1/34) […]" in output
    # The reason a node has *instead of* an outcome is already its outcome column;
    # repeating it as a Reason line would be noise, not diagnosis.
    assert "blocked  blocked  dependency failed" in output
    assert "Reason: dependency failed" not in output
