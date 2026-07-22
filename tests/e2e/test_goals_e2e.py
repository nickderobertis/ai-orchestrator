"""E2E: the real CLI coordinates active goals through the shared disk index."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from orchestrator import REPO_ROOT


def _run(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", *args], cwd=REPO_ROOT, env=env, text=True, capture_output=True, check=False
    )


def test_overlapping_goals_require_and_record_acknowledgement(tmp_path: Path, command_base) -> None:
    state = tmp_path / "state"
    env = {**os.environ, "AI_ORCHESTRATOR_HOME": str(state)}
    target = tmp_path / "target"
    subprocess.run(["git", "init", "-b", "main", str(target)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(target), "config", "user.email", "e2e@example.test"], check=True
    )
    subprocess.run(["git", "-C", str(target), "config", "user.name", "E2E"], check=True)
    subprocess.run(["git", "-C", str(target), "remote", "add", "origin", str(target)], check=True)
    (target / "README.md").write_text("target\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(target), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(target), "commit", "-m", "init"], check=True, capture_output=True
    )
    registered = _run(
        "register-repo", str(target), "--repo-type", "single-owner", "--gate", "true", env=env
    )
    assert registered.returncode == 0, registered.stderr

    ready = tmp_path / "provider.ready"
    release = tmp_path / "provider.release"
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 4,
                "goal": {"text": "Protect the shared target"},
                "tasks": [
                    {
                        "id": "hold",
                        "persona": "engineer",
                        "task": (
                            f"complete-now provider-barrier-ready={ready} "
                            f"provider-barrier-release={release}"
                        ),
                    },
                    {
                        "id": "target",
                        "repo": str(target),
                        "task": "Record the target identity.",
                        "expects_no_diff": True,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    goal_less_plan = tmp_path / "goal-less-plan.json"
    goal_less_payload = json.loads(plan.read_text(encoding="utf-8"))
    del goal_less_payload["goal"]
    goal_less_plan.write_text(json.dumps(goal_less_payload), encoding="utf-8")
    base = command_base()
    command = [
        "just",
        "run-plan",
        str(plan),
        "--runs-dir",
        str(tmp_path / "runs"),
        "--base",
        str(base),
        "--provider",
        "command",
    ]
    goal_less_command = [*command]
    goal_less_command[2] = str(goal_less_plan)
    first = subprocess.Popen(
        [*command, "--run", "first"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    index = state / "runs-index.json"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if index.exists() and "first" in index.read_text(encoding="utf-8"):
            break
        time.sleep(0.02)
    else:
        first.kill()
        raise AssertionError("first run did not register in the goals index")

    refused = subprocess.run(
        [*goal_less_command, "--run", "second"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert refused.returncode == 2
    assert "first" in refused.stderr
    assert "Protect the shared target" in refused.stderr
    assert str(target) in refused.stderr

    acknowledged = subprocess.Popen(
        [*goal_less_command, "--run", "second", "--acknowledge-concurrent"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        indexed = json.loads(index.read_text(encoding="utf-8"))
        if "second" in indexed["runs"]:
            break
        time.sleep(0.02)
    else:
        release.write_text("release\n", encoding="utf-8")
        first.communicate(timeout=10)
        acknowledged.communicate(timeout=10)
        raise AssertionError("acknowledged run did not register in the goals index")
    goals = _run("goals", env=env)
    assert goals.returncode == 0, goals.stderr
    assert "Protect the shared target" in goals.stdout
    assert "Protect-the-shared-target" in goals.stdout
    assert "second  (no goal)" in goals.stdout
    assert str((tmp_path / "runs" / "first").resolve()) in goals.stdout
    assert str(target.resolve()) in goals.stdout

    release.write_text("release\n", encoding="utf-8")
    first_out, first_err = first.communicate(timeout=10)
    second_out, second_err = acknowledged.communicate(timeout=10)
    assert first.returncode == 0, first_out + first_err
    assert acknowledged.returncode == 0, second_out + second_err
    events = (tmp_path / "runs" / "second" / "events.jsonl").read_text(encoding="utf-8")
    assert "concurrent-acknowledged" in events
    indexed = json.loads(index.read_text(encoding="utf-8"))
    assert indexed["runs"] == {}
    goals = _run("goals", env=env)
    assert goals.returncode == 0, goals.stderr
    assert goals.stdout.strip() == "No active DAG goals."


@pytest.mark.parametrize(
    ("schema_version", "goal", "message"),
    [
        (3, {"text": "Too new"}, "requires schema_version 4"),
        (4, {"text": ""}, "goal.text' must be a non-empty string"),
        (4, [], "goal' must be a mapping"),
        (4, {"text": "Unknown", "extra": "field"}, "goal' has unknown field"),
        (4, {"id": "", "text": "Empty ID"}, "goal.id' must be a non-empty string"),
    ],
)
def test_run_plan_rejects_invalid_goal_contract(
    tmp_path: Path,
    command_base,
    schema_version: int,
    goal: object,
    message: str,
) -> None:
    plan = tmp_path / "invalid-goal.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": schema_version,
                "goal": goal,
                "tasks": [
                    {
                        "id": "unused",
                        "persona": "engineer",
                        "task": "This invalid plan must never dispatch.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = _run(
        "run-plan",
        str(plan),
        "--no-record",
        "--base",
        str(command_base()),
        "--provider",
        "command",
        env=os.environ.copy(),
    )

    assert result.returncode == 2
    assert message in result.stderr


def test_run_plan_accepts_explicit_goal_id(tmp_path: Path, command_base) -> None:
    plan = tmp_path / "explicit-goal.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 4,
                "goal": {"id": "operator-chosen", "text": "Use an explicit ID"},
                "tasks": [
                    {
                        "id": "done",
                        "persona": "engineer",
                        "task": "complete-now",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = _run(
        "run-plan",
        str(plan),
        "--no-record",
        "--base",
        str(command_base()),
        "--provider",
        "command",
        env=os.environ.copy(),
    )

    assert result.returncode == 0, result.stderr


def test_goals_rejects_empty_state_root_override() -> None:
    result = _run("goals", env={**os.environ, "AI_ORCHESTRATOR_HOME": " "})

    assert result.returncode != 0
    assert "AI_ORCHESTRATOR_HOME must not be empty" in result.stderr
