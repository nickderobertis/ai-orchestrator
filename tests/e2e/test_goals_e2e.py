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


def test_cross_dag_dependency_waits_then_reports_upstream_modification(
    tmp_path: Path, command_base, onejudge_bin: str
) -> None:
    state = tmp_path / "state"
    runs = tmp_path / "runs"
    env = {**os.environ, "AI_ORCHESTRATOR_HOME": str(state)}
    producer_ready, producer_release = tmp_path / "producer.ready", tmp_path / "producer.release"
    amend_ready, amend_release = tmp_path / "amend.ready", tmp_path / "amend.release"
    hold_ready, hold_release = tmp_path / "hold.ready", tmp_path / "hold.release"
    fail_ready, fail_release = tmp_path / "fail.ready", tmp_path / "fail.release"
    plan_a = tmp_path / "a.json"
    plan_a.write_text(
        json.dumps(
            {
                "schema_version": 5,
                "tasks": [
                    {
                        "id": "produce",
                        "persona": "engineer",
                        "task": (
                            f"complete-now provider-barrier-ready={producer_ready} "
                            f"provider-barrier-release={producer_release}"
                        ),
                    },
                    {
                        "id": "amend",
                        "persona": "engineer",
                        "task": (
                            f"complete-now provider-barrier-ready={amend_ready} "
                            f"provider-barrier-release={amend_release}"
                        ),
                        "deps": ["produce"],
                    },
                    {
                        "id": "hold",
                        "persona": "engineer",
                        "task": (
                            f"complete-now provider-barrier-ready={hold_ready} "
                            f"provider-barrier-release={hold_release}"
                        ),
                    },
                    {
                        "id": "fail",
                        "persona": "engineer",
                        "task": (
                            f"should-fail provider-barrier-ready={fail_ready} "
                            f"provider-barrier-release={fail_release}"
                        ),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    plan_b = tmp_path / "b.json"
    plan_b.write_text(
        json.dumps(
            {
                "schema_version": 5,
                "tasks": [
                    {
                        "id": "consume",
                        "task": "Observe the external dependency without dispatching.",
                        "expects_no_diff": True,
                        "deps": ["run:A#produce"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    common = [
        "--runs-dir",
        str(runs),
        "--base",
        str(command_base()),
        "--onejudge-bin",
        onejudge_bin,
        "--provider",
        "command",
        "--format",
        "json",
    ]
    upstream = subprocess.Popen(
        ["just", "run-plan", str(plan_a), "--run", "A", *common],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not (
        producer_ready.exists() and hold_ready.exists() and fail_ready.exists()
    ):
        time.sleep(0.02)
    assert producer_ready.exists() and hold_ready.exists() and fail_ready.exists()

    blocked = subprocess.run(
        ["just", "run-plan", str(plan_b), "--run", "B-blocked", *common],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert blocked.returncode == 1, blocked.stderr
    assert json.loads(blocked.stdout)["results"]["consume"]["status"] == "blocked"

    failed_plan = tmp_path / "failed.json"
    failed_mapping = json.loads(plan_b.read_text(encoding="utf-8"))
    failed_mapping["tasks"][0]["deps"] = ["run:A#fail"]
    failed_plan.write_text(json.dumps(failed_mapping), encoding="utf-8")
    fail_release.write_text("release\n", encoding="utf-8")
    deadline = time.monotonic() + 10
    upstream_events = runs / "A" / "events.jsonl"
    while time.monotonic() < deadline:
        if upstream_events.exists() and '"kind": "node-failed"' in upstream_events.read_text(
            encoding="utf-8"
        ):
            break
        time.sleep(0.02)
    failed = subprocess.run(
        ["just", "run-plan", str(failed_plan), "--run", "B-failed", *common],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert failed.returncode == 1, failed.stderr
    assert json.loads(failed.stdout)["results"]["consume"]["status"] == "blocked"

    producer_release.write_text("release\n", encoding="utf-8")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not amend_ready.exists():
        time.sleep(0.02)
    assert amend_ready.exists()
    restart_plan = tmp_path / "restart.json"
    restart_plan.write_text(
        json.dumps(
            {
                "schema_version": 5,
                "tasks": [
                    {
                        "id": "consume",
                        "task": "Record the satisfied external dependency.",
                        "expects_no_diff": True,
                        "deps": ["run:A#produce"],
                    },
                    {
                        "id": "pause",
                        "kind": "human",
                        "task": "Keep the downstream run resumable.",
                        "deps": ["consume"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    snapshotted = subprocess.run(
        ["just", "run-plan", str(restart_plan), "--run", "B", *common],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert snapshotted.returncode == 1, snapshotted.stderr
    assert json.loads(snapshotted.stdout)["results"]["consume"]["status"] == "done"

    amend_release.write_text("release\n", encoding="utf-8")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        upstream_records = [
            json.loads(line) for line in upstream_events.read_text(encoding="utf-8").splitlines()
        ]
        if any(
            record.get("node") == "amend" and record.get("kind") == "node-settled"
            for record in upstream_records
        ):
            break
        time.sleep(0.02)
    resumed = subprocess.run(
        ["just", "run-plan", str(restart_plan), "--run", "B", *common],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert resumed.returncode == 1, resumed.stderr
    events = runs / "B" / "events.jsonl"
    assert "upstream-modified" in events.read_text(encoding="utf-8")

    hold_release.write_text("release\n", encoding="utf-8")
    upstream_out, upstream_err = upstream.communicate(timeout=10)
    assert upstream.returncode == 1, upstream_out + upstream_err
    assert json.loads(upstream_out)["results"]["fail"]["status"] == "failed"


@pytest.mark.parametrize(
    "schema_version,dependency,message",
    [
        (4, "run:upstream#produce", "require schema_version 5"),
        (5, "run:upstream", "malformed cross-DAG dependency"),
    ],
)
def test_cross_dag_dependency_validation_reaches_cli_boundary(
    tmp_path: Path,
    schema_version: int,
    dependency: str,
    message: str,
) -> None:
    plan = tmp_path / "invalid-cross-dag.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": schema_version,
                "tasks": [
                    {
                        "id": "consume",
                        "task": "This must not dispatch.",
                        "expects_no_diff": True,
                        "deps": [dependency],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    rejected = subprocess.run(
        ["just", "run-plan", str(plan), "--no-record"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert rejected.returncode == 2
    assert message in rejected.stderr


def test_unknown_cross_dag_run_blocks_through_cli(tmp_path: Path) -> None:
    plan = tmp_path / "unknown-cross-dag.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 5,
                "tasks": [
                    {
                        "id": "consume",
                        "task": "This must remain blocked.",
                        "expects_no_diff": True,
                        "deps": ["run:not-active#publish"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    blocked = subprocess.run(
        ["just", "run-plan", str(plan), "--no-record", "--format", "json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert blocked.returncode == 1, blocked.stderr
    assert json.loads(blocked.stdout)["results"]["consume"]["status"] == "blocked"


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


def _init_target(path: Path) -> Path:
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "e2e@example.test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "E2E"], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", str(path)], check=True)
    (path / "README.md").write_text("target\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "init"], check=True, capture_output=True
    )
    return path


def _barrier_plan(path: Path, target: Path, ready: Path, release: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 4,
                "goal": {"text": f"Work {path.stem} on the shared target"},
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
    return path


def _wait_for_file(path: Path, seconds: float = 30) -> None:
    limit = time.monotonic() + seconds
    while time.monotonic() < limit:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError(f"did not appear: {path}")


def test_a_live_concurrent_run_is_named_apart_from_a_stale_registration(
    tmp_path: Path, command_base
) -> None:
    """Launching into company reports *which* company, and the views keep saying so.

    Two orchestrations once shared two isolated checkouts for hours, and the
    collision was found only by tracing a process tree by hand:
    `--acknowledge-concurrent`, needed to get past a dead run's stale registration,
    also silenced any notice of the live one. Both are reported here, differently,
    and the guard still refuses both without the flag.
    """
    state = tmp_path / "state"
    runs = tmp_path / "runs"
    env = {**os.environ, "AI_ORCHESTRATOR_HOME": str(state)}
    target = _init_target(tmp_path / "target")
    registered = _run(
        "register-repo", str(target), "--repo-type", "single-owner", "--gate", "true", env=env
    )
    assert registered.returncode == 0, registered.stderr
    identity = str(target.resolve())

    # A registration whose owner this host cannot rule on: another host's pid, no
    # final report. `_sweep` cannot retire it, so it outlives the run it belonged to
    # — the exact residue `--acknowledge-concurrent` exists to launch past.
    dead_dir = tmp_path / "dead-run"
    dead_dir.mkdir()
    state.mkdir(parents=True, exist_ok=True)
    (state / "runs-index.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runs": {
                    "dead": {
                        "run_id": "dead",
                        "run_dir": str(dead_dir),
                        "goal": {"id": "dead-goal", "text": "A run that already stopped"},
                        "identities": [identity],
                        "pid": 4242,
                        "host": "a-host-that-is-not-this-one",
                        "started": "2026-07-30T00:00:00+00:00",
                        "status": "active",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    base = command_base()
    live_ready, live_release = tmp_path / "live.ready", tmp_path / "live.release"
    second_ready, second_release = tmp_path / "second.ready", tmp_path / "second.release"

    def command(plan: Path, run: str, *extra: str) -> list[str]:
        return [
            "just",
            "run-plan",
            str(plan),
            "--runs-dir",
            str(runs),
            "--base",
            str(base),
            "--provider",
            "command",
            "--run",
            run,
            *extra,
        ]

    live_plan = _barrier_plan(tmp_path / "live.json", target, live_ready, live_release)
    second_plan = _barrier_plan(tmp_path / "second.json", target, second_ready, second_release)

    # Getting past the stale registration takes the flag, and says so without ever
    # claiming a live neighbour — there is none yet.
    live = subprocess.Popen(
        command(live_plan, "live", "--acknowledge-concurrent"),
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _wait_for_file(live_ready)
    index = json.loads((state / "runs-index.json").read_text(encoding="utf-8"))
    live_pid = index["runs"]["live"]["pid"]

    try:
        refused = subprocess.run(
            command(second_plan, "second"),
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert refused.returncode == 2
        # The two registrations no longer read alike: one names a working process and
        # its pid, the other says only that a registration is there.
        assert f"run 'live' is LIVE (owner pid {live_pid} on " in refused.stderr
        assert "run 'dead' is registered but not observable here" in refused.stderr
        assert "(recorded owner pid 4242 on a-host-that-is-not-this-one)" in refused.stderr
        assert identity in refused.stderr

        acknowledged = subprocess.Popen(
            command(second_plan, "second", "--acknowledge-concurrent"),
            cwd=REPO_ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            _wait_for_file(second_ready)
            viewed = _run("status", "--runs-dir", str(runs), env=env)
            assert viewed.returncode == 0, viewed.stderr
            # The planner's read-only view now carries the other live orchestration,
            # which appears nowhere in this run's own ledger. The stale registration
            # stays out of it: a view that listed that one too would report the very
            # thing `--acknowledge-concurrent` exists to launch past as live work.
            assert f"CONCURRENT: run 'live' is LIVE (owner pid {live_pid} on " in viewed.stdout
            assert "run 'dead'" not in viewed.stdout
            goals = _run("goals", env=env)
            assert goals.returncode == 0, goals.stderr
            assert f"owner: live (owner pid {live_pid} on " in goals.stdout
            assert "owner: unobservable (owner pid 4242 on a-host-that-is-not-this-one)" in (
                goals.stdout
            )
        finally:
            second_release.write_text("release\n", encoding="utf-8")
            second_out, second_err = acknowledged.communicate(timeout=120)
        assert acknowledged.returncode == 0, second_out + second_err
        # Acknowledging never hides the live neighbour again.
        assert "proceeding alongside a live concurrent run" in second_err
        assert f"run 'live' is LIVE (owner pid {live_pid} on " in second_err
    finally:
        live_release.write_text("release\n", encoding="utf-8")
        live_out, live_err = live.communicate(timeout=120)
    assert live.returncode == 0, live_out + live_err
    assert "proceeding alongside a live concurrent run" not in live_err
