"""E2E journeys for the canonical recorded tracked-graph CLI.

The direct-agent journeys invoke ``just run-plan`` / ``just next-round`` and
therefore spawn the adopted real onejudge binary.  Only onejudge's paid model
provider is replaced by the command-provider protocol double from ``conftest``.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

from orchestrator import REPO_ROOT


def _just(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_direct_human_pause_attestation_and_release_use_real_onejudge(
    tmp_path: Path, command_base, onejudge_bin: str
) -> None:
    """A recorded direct graph pauses, attests, and resumes without replay."""
    runs = tmp_path / "runs"
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "prepare",
                        "persona": "planner",
                        "task": "complete-now: prepare the release.",
                    },
                    {
                        "id": "approve",
                        "kind": "human",
                        "task": "Approve the prepared release.",
                        "deps": ["prepare"],
                    },
                    {
                        "id": "publish",
                        "persona": "backend-engineer",
                        "task": "complete-now: publish the approved release.",
                        "deps": ["approve"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    common = (
        "--runs-dir",
        str(runs),
        "--base",
        str(command_base()),
        "--onejudge-bin",
        onejudge_bin,
        "--format",
        "json",
    )

    paused = _just("run-plan", str(plan), "--run", "human-direct", *common)

    assert paused.returncode == 1, paused.stderr
    first = json.loads(paused.stdout)
    assert first["ok"] is False and first["state"] == "waiting"
    assert first["started_order"] == ["prepare", "approve"]
    assert first["results"]["prepare"]["status"] == "done"
    assert first["results"]["approve"] == {
        "kind": "human",
        "status": "waiting",
        "task": "Approve the prepared release.",
        "unblocks": ["publish"],
        "human_actions": [
            {
                "ref": "approve",
                "task": "Approve the prepared release.",
                "unblocks": ["publish"],
                "unblocks_publication": False,
            }
        ],
        "error": "awaiting human action",
    }
    assert first["results"]["publish"]["status"] == "blocked"
    assert first["results"]["publish"]["blocked_by"] == ["approve"]
    assert "Approve the prepared release." in paused.stderr
    assert "--complete-human approve" in paused.stderr

    round_one = runs / "human-direct" / "round-01"
    assert json.loads((round_one / "result.json").read_text(encoding="utf-8")) == first
    for invalid in ("missing", "publish", "prepare"):
        rejected = _just(
            "next-round",
            "human-direct",
            "--complete-human",
            invalid,
            *common,
        )
        assert rejected.returncode == 2
        assert "recorded waiting human task" in rejected.stderr
        assert not (runs / "human-direct" / "humans.json").exists()
        assert not (runs / "human-direct" / "round-02").exists()

    resumed = _just(
        "next-round",
        "human-direct",
        "--complete-human",
        "approve",
        *common,
    )

    assert resumed.returncode == 0, resumed.stderr
    second = json.loads(resumed.stdout)
    assert second["ok"] is True and second["state"] == "complete"
    assert second["started_order"] == ["publish"]
    assert list(second["results"]) == ["publish"]
    assert second["results"]["publish"]["status"] == "done"
    second_plan = json.loads(
        (runs / "human-direct" / "round-02" / "plan.json").read_text(encoding="utf-8")
    )
    assert [task["id"] for task in second_plan["tasks"]] == ["publish"]
    assert second_plan["tasks"][0]["deps"] == []

    completions = json.loads((runs / "human-direct" / "humans.json").read_text(encoding="utf-8"))[
        "completions"
    ]
    assert len(completions) == 1
    assert completions[0]["ref"] == "approve"
    assert completions[0]["round"] == 1
    completed_at = datetime.fromisoformat(completions[0]["completed_at"])
    assert completed_at.tzinfo is not None

    repeated = _just(
        "next-round",
        "human-direct",
        "--complete-human",
        "approve",
        *common,
    )
    assert repeated.returncode == 2
    assert "already completed" in repeated.stderr
    assert len(list((runs / "human-direct").glob("round-*"))) == 2
