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

from orchestrator import REPO_ROOT, gitops
from orchestrator.registry import Registry


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
                        "persona": "engineer",
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


def test_legacy_direct_plan_and_recorded_ledger_still_run(
    tmp_path: Path, command_base, onejudge_bin: str
) -> None:
    """Pre-kind direct plans and pre-state ledgers continue through run-plan."""
    base = str(command_base())
    legacy_plan = {
        "concurrency": 1,
        "tasks": [
            {
                "id": "legacy-agent",
                "persona": "engineer",
                "task": "complete-now: run the old direct plan.",
            }
        ],
    }
    plan_path = tmp_path / "legacy-direct.json"
    plan_path.write_text(json.dumps(legacy_plan), encoding="utf-8")

    direct = _just(
        "run-plan",
        str(plan_path),
        "--no-record",
        "--base",
        base,
        "--onejudge-bin",
        onejudge_bin,
        "--format",
        "json",
    )
    assert direct.returncode == 0, direct.stderr
    direct_payload = json.loads(direct.stdout)
    assert direct_payload["state"] == "complete"
    assert direct_payload["results"]["legacy-agent"]["status"] == "done"

    runs = tmp_path / "runs"
    old_round = runs / "old-ledger" / "round-01"
    old_round.mkdir(parents=True)
    (old_round / "plan.json").write_text(json.dumps(legacy_plan), encoding="utf-8")
    (old_round / "result.json").write_text(
        json.dumps(
            {
                "ok": False,
                "started_order": ["legacy-agent"],
                "results": {
                    "legacy-agent": {
                        "status": "failed",
                        "completed": False,
                        "exit_code": 1,
                        "verdicts": [],
                        "usage": {},
                        "error": "did not complete",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    edits = tmp_path / "legacy-edits.json"
    edits.write_text(
        json.dumps(
            {"retry": {"legacy-agent": {"task": "complete-now: finish the old recorded run."}}}
        ),
        encoding="utf-8",
    )

    continued = _just(
        "next-round",
        "old-ledger",
        str(edits),
        "--runs-dir",
        str(runs),
        "--base",
        base,
        "--onejudge-bin",
        onejudge_bin,
        "--format",
        "json",
    )
    assert continued.returncode == 0, continued.stderr
    continued_payload = json.loads(continued.stdout)
    assert continued_payload["state"] == "complete"
    assert continued_payload["results"]["legacy-agent"]["status"] == "done"
    assert (runs / "old-ledger" / "round-02" / "result.json").is_file()


def test_legacy_repo_plan_runs_through_canonical_and_deprecated_alias(
    tmp_path: Path, bare_origin, command_base, onejudge_bin: str
) -> None:
    """Old lifecycle-only plan mappings work through both command names."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "legacy-repo-canonical")
    Registry().register(str(canonical), workflow="local")
    common = (
        "--no-record",
        "--workspace",
        str(tmp_path / "legacy-repo-worktrees"),
        "--base",
        str(command_base()),
        "--onejudge-bin",
        onejudge_bin,
        "--format",
        "json",
    )

    def write_plan(name: str, task: str) -> Path:
        path = tmp_path / f"{name}.json"
        path.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": name,
                            "repo": str(canonical),
                            "persona": "engineer",
                            "task": task,
                            "skip_verify": True,
                            "workflow": "local",
                            "repo_type": "single-owner",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return path

    canonical_run = _just(
        "run-plan",
        str(write_plan("legacy-repo", "complete-now write-change: old repo plan")),
        *common,
    )
    assert canonical_run.returncode == 0, canonical_run.stderr
    canonical_payload = json.loads(canonical_run.stdout)
    assert canonical_payload["state"] == "complete"
    assert canonical_payload["results"]["legacy-repo"]["outcome"] == "merged"

    alias_run = _just(
        "repo-plan",
        str(
            write_plan(
                "legacy-alias",
                "complete-now write-unique-change: deprecated repo plan alias",
            )
        ),
        *common,
    )
    assert alias_run.returncode == 0, alias_run.stderr
    assert "deprecated" in alias_run.stderr
    alias_payload = json.loads(alias_run.stdout)
    assert alias_payload["state"] == "complete"
    assert alias_payload["results"]["legacy-alias"]["outcome"] == "merged"
    assert (
        subprocess.run(
            ["git", "-C", str(origin), "cat-file", "-e", "main:CHANGE.txt"],
            capture_output=True,
        ).returncode
        == 0
    )
