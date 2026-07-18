"""E2E journeys for the canonical recorded tracked-graph CLI.

The direct-agent journeys invoke ``just run-plan`` / ``just next-round`` and
therefore spawn the adopted real onejudge binary.  Only onejudge's paid model
provider is replaced by the command-provider protocol double from ``conftest``.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest

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


@pytest.mark.parametrize(
    ("invalid_field", "invalid_value", "message"),
    [
        ("mode", "unknown", "resume 'mode' must be 'pause' or 'retry'"),
        ("source_round", 0, "resume 'source_round' must be a positive integer"),
    ],
)
def test_run_plan_rejects_invalid_retry_resume_contract(
    tmp_path: Path, invalid_field: str, invalid_value: object, message: str
) -> None:
    plan = tmp_path / "invalid-resume.json"
    resume = {
        "branch": "feature/preserved",
        "base_branch": "main",
        "pr_base": "main",
        "checkpoint": "a" * 40,
        "completed_steps": [],
        "pr": None,
        "mode": "retry",
        invalid_field: invalid_value,
    }
    plan.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "retry",
                        "repo": str(tmp_path / "target"),
                        "persona": "engineer",
                        "task": "Retry preserved work.",
                        "resume": resume,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = _just("run-plan", str(plan), "--no-record")

    assert result.returncode == 2
    assert message in result.stderr


def test_direct_human_pause_attestation_and_release_use_real_onejudge(
    tmp_path: Path, command_base, onejudge_bin: str
) -> None:
    """A recorded direct graph pauses, attests, and resumes without replay."""
    runs = tmp_path / "runs"
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
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
                ],
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
    assert first["schema_version"] == 2 and first["round"] == 1
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
    events = [
        json.loads(line)
        for line in (runs / "human-direct" / "events.jsonl").read_text().splitlines()
    ]
    assert [event["detail"]["definition"]["id"] for event in events[:3]] == [
        "prepare",
        "approve",
        "publish",
    ]
    assert [(event["detail"]["from"], event["detail"]["to"]) for event in events[3:5]] == [
        ("prepare", "approve"),
        ("approve", "publish"),
    ]
    started = next(event for event in events if event["kind"] == "round-started")
    assert started["detail"]["plan"] == {"schema_version": 2}
    finished = next(event for event in events if event["kind"] == "round-finished")
    assert finished["detail"]["result"] == first
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
    assert second_plan["schema_version"] == 2
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

    replay_plan = tmp_path / "replay-plan.json"
    replay_plan.write_text(json.dumps(second_plan))
    round_two = runs / "human-direct" / "round-02"
    for artifact in ("plan.json", "result.json", "status.json"):
        (round_two / artifact).unlink()
    replayed = _just("run-plan", str(replay_plan), "--run", "human-direct", "--recover", *common)
    assert replayed.returncode == 0, replayed.stderr
    assert json.loads((round_two / "plan.json").read_text()) == second_plan
    assert json.loads((round_two / "result.json").read_text()) == second
    round_three = runs / "human-direct" / "round-03"
    assert (round_three / "result.json").exists()


def test_recover_interrupted_real_cli_does_not_duplicate_node_start(
    tmp_path: Path, command_base, onejudge_bin: str
) -> None:
    runs = tmp_path / "runs"
    plan = tmp_path / "interrupt.json"
    plan.write_text(
        json.dumps({"tasks": [{"id": "work", "persona": "engineer", "task": "complete-now"}]})
    )
    command = [
        "just",
        "run-plan",
        str(plan),
        "--run",
        "interrupted",
        "--runs-dir",
        str(runs),
        "--base",
        str(command_base()),
        "--onejudge-bin",
        onejudge_bin,
        "--format",
        "json",
    ]
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    events_path = runs / "interrupted" / "events.jsonl"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if events_path.exists() and '"kind": "node-started"' in events_path.read_text():
            break
        time.sleep(0.01)
    else:
        process.kill()
        pytest.fail("run-plan did not durably start its node")
    os.killpg(process.pid, signal.SIGKILL)
    process.wait()
    round_dir = runs / "interrupted" / "round-01"
    (round_dir / "plan.json").unlink()

    recovered = subprocess.run(
        [*command, "--recover"], cwd=REPO_ROOT, text=True, capture_output=True, check=False
    )
    assert recovered.returncode == 0, recovered.stderr
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    assert sum(event["kind"] == "node-started" for event in events) == 1


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
    assert direct_payload["schema_version"] == 2 and "round" not in direct_payload
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


def test_expects_no_diff_skips_onejudge_while_sibling_uses_real_boundary(
    tmp_path: Path, bare_origin, command_base, onejudge_bin: str
) -> None:
    """The explicit zero-yield node settles without invoking the coding backend."""
    no_op_dir = tmp_path / "no-op-project"
    ordinary_dir = tmp_path / "ordinary-project"
    no_op_dir.mkdir()
    ordinary_dir.mkdir()
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "zero-yield-canonical")
    Registry().register(str(canonical), workflow="local")
    plan = tmp_path / "zero-yield.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "tasks": [
                    {
                        "id": "certify-unchanged",
                        "task": "write-change complete-now: certify the unchanged handoff.",
                        "project_dir": str(no_op_dir),
                        "expects_no_diff": True,
                    },
                    {
                        "id": "ordinary",
                        "persona": "engineer",
                        "task": "write-change complete-now: perform ordinary work.",
                        "project_dir": str(ordinary_dir),
                    },
                    {
                        "id": "lifecycle-no-op",
                        "repo": str(canonical),
                        "task": "write-change complete-now: no lifecycle should start.",
                        "expects_no_diff": True,
                    },
                    {
                        "id": "step-no-op",
                        "repo": str(canonical),
                        "skip_verify": True,
                        "steps": [
                            {
                                "id": "certify",
                                "task": "write-change complete-now: certify unchanged.",
                                "expects_no_diff": True,
                            }
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    completed = _just(
        "run-plan",
        str(plan),
        "--no-record",
        "--base",
        str(command_base()),
        "--onejudge-bin",
        onejudge_bin,
        "--workspace",
        str(tmp_path / "zero-yield-worktrees"),
        "--format",
        "json",
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["results"]["certify-unchanged"] == {
        "outcome": "no-changes",
        "completed": True,
        "kind": "agent",
        "status": "done",
        "task": "write-change complete-now: certify the unchanged handoff.",
        "error": None,
    }
    assert payload["results"]["ordinary"]["status"] == "done"
    assert payload["results"]["lifecycle-no-op"]["outcome"] == "no-changes"
    assert payload["results"]["step-no-op"]["outcome"] == "no-changes"
    assert not (no_op_dir / "CHANGE.txt").exists()
    assert (ordinary_dir / "CHANGE.txt").read_text(encoding="utf-8") == "change from fake agent\n"
    assert (
        subprocess.run(
            ["git", "-C", str(origin), "cat-file", "-e", "main:CHANGE.txt"],
            capture_output=True,
        ).returncode
        != 0
    )


def test_expects_no_diff_contract_is_rejected_at_cli_boundary(tmp_path: Path) -> None:
    invalid_plans = (
        (
            {"schema_version": 1, "tasks": [{"id": "x", "task": "x", "expects_no_diff": True}]},
            "requires schema_version 2",
        ),
        (
            {
                "schema_version": 2,
                "tasks": [
                    {
                        "id": "x",
                        "task": "x",
                        "expects_no_diff": True,
                        "persona": "reviewer",
                        "done_when": "provide review findings",
                    }
                ],
            },
            "cannot set 'persona', 'done_when'",
        ),
        (
            {
                "schema_version": 2,
                "tasks": [{"id": "x", "task": "x", "expects_no_diff": "yes"}],
            },
            "must be a boolean",
        ),
        (
            {"schema_version": 99, "tasks": [{"id": "x", "persona": "p", "task": "x"}]},
            "current version 3",
        ),
        (
            {
                "schema_version": 2,
                "tasks": [
                    {
                        "id": "x",
                        "repo": "owner/repo",
                        "task": "x",
                        "expects_no_diff": True,
                        "persona": "engineer",
                    }
                ],
            },
            "cannot set 'persona'",
        ),
        (
            {
                "schema_version": 2,
                "tasks": [
                    {
                        "id": "x",
                        "repo": "owner/repo",
                        "steps": [
                            {
                                "id": "ready",
                                "task": "x",
                                "expects_no_diff": True,
                                "done_when": "provide findings",
                            }
                        ],
                    }
                ],
            },
            "step 'ready' with expects_no_diff cannot set 'done_when'",
        ),
        (
            {
                "schema_version": 2,
                "tasks": [
                    {
                        "id": "x",
                        "repo": "owner/repo",
                        "task": "x",
                        "expects_no_diff": True,
                        "steps": [{"id": "ready", "task": "x", "expects_no_diff": True}],
                    }
                ],
            },
            "cannot also set 'steps'",
        ),
        (
            {
                "schema_version": 1,
                "tasks": [
                    {"id": "x", "persona": "engineer", "task": "x", "expects_no_diff": False}
                ],
            },
            "requires schema_version 2",
        ),
        ({"schema_version": 2, "tasks": "not-a-list"}, "non-empty 'tasks' list"),
        (
            {
                "schema_version": 2,
                "tasks": [
                    {
                        "id": "x",
                        "repo": "owner/repo",
                        "persona": "engineer",
                        "task": "x",
                        "verify_via_ci": True,
                    }
                ],
            },
            "verify_via_ci' requires schema_version 3",
        ),
    )
    for index, (mapping, message) in enumerate(invalid_plans):
        plan = tmp_path / f"invalid-{index}.json"
        plan.write_text(json.dumps(mapping), encoding="utf-8")
        rejected = _just("run-plan", str(plan), "--no-record")
        assert rejected.returncode == 2
        assert message in rejected.stderr


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
