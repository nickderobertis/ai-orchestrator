"""Real onejudge split-provider journey for the live planner channel."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

from orchestrator import BASE_CONFIG, REPO_ROOT
from orchestrator.dispatch import launch_orchestrator

FAKE_BACKEND = REPO_ROOT / "tests" / "e2e" / "fake_backend.py"


def _base(tmp_path: Path) -> Path:
    value = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    value["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    path = tmp_path / "base.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return path


def _plan(tmp_path: Path, sentinel: str) -> Path:
    path = tmp_path / f"plan-{sentinel}.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": f"nested-{sentinel}",
                "tasks": [
                    {
                        "id": "worker",
                        "persona": "engineer",
                        "task": f"{sentinel} complete-now",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _wait_report(path: Path) -> dict[str, object]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size:
            return json.loads(path.read_text(encoding="utf-8"))
        time.sleep(0.02)
    raise AssertionError(f"report did not land: {path}")


def _launch_cli(plan: Path, runs: Path, base: Path, onejudge_bin: str) -> str:
    launched = subprocess.run(
        [
            "just",
            "orchestrate",
            str(plan),
            "--runs-dir",
            str(runs),
            "--base",
            str(base),
            "--onejudge-bin",
            onejudge_bin,
            "--skill-command",
            sys.executable,
            str(FAKE_BACKEND),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return launched.stdout.strip()


def _next_cli(run_id: str, runs: Path, timeout: str = "10") -> dict[str, object]:
    result = subprocess.run(
        ["just", "channel-next", run_id, "--runs-dir", str(runs), "--timeout", timeout],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def _reply_cli(run_id: str, runs: Path, value: dict[str, object]) -> None:
    subprocess.run(
        ["just", "channel-reply", run_id, "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        input=json.dumps(value),
        text=True,
        capture_output=True,
        check=True,
    )


def test_live_channel_runs_real_nested_graph_and_round_trips_guidance(
    tmp_path: Path, onejudge_bin: str
) -> None:
    runs = tmp_path / "host-runs"
    run_id = _launch_cli(_plan(tmp_path, "surface-blocker"), runs, _base(tmp_path), onejudge_bin)
    run_dir = runs / run_id
    blocker = _next_cli(run_id, runs)
    assert blocker["surface"] == {
        "kind": "blocker",
        "message": "plan departure needs a decision",
    }
    _reply_cli(
        run_id,
        runs,
        {"completion": False, "message": "retry X", "reason": "planner chose retry"},
    )
    closeout = _next_cli(run_id, runs)
    assert closeout["surface"]["kind"] == "closeout"
    assert "retry X" in closeout["surface"]["message"]
    _reply_cli(run_id, runs, {"completion": True, "reason": "planner verified closeout"})

    report = _wait_report(run_dir / "orchestrator" / "report.json")
    assert report["stopped_early"] is False
    nested = [path for path in runs.iterdir() if path != run_dir]
    assert len(nested) == 1
    nested_result = json.loads((nested[0] / "round-01" / "result.json").read_text())
    assert nested_result["results"]["worker"]["status"] == "done"
    assert not (nested[0] / "channel").exists()


def test_launch_api_records_detached_owner_and_real_report(
    tmp_path: Path, onejudge_bin: str
) -> None:
    """Cover the Python launch boundary while still driving the real onejudge process."""
    runs = tmp_path / "api-runs"
    run_id = launch_orchestrator(
        _plan(tmp_path, "surface-milestone"),
        runs_dir=runs,
        base_path=_base(tmp_path),
        onejudge_bin=onejudge_bin,
        skill_provider={"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]},
        turn_timeout=10,
    )
    run_dir = runs / run_id
    status = json.loads((run_dir / "round-01" / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "running"
    assert isinstance(status["pid"], int) and status["host"]
    surface = _next_cli(run_id, runs)
    assert surface["surface"]["kind"] == "milestone"
    _reply_cli(run_id, runs, {"completion": True, "reason": "verified"})
    report = _wait_report(run_dir / "orchestrator" / "report.json")
    assert report["stopped_early"] is False


def test_bridge_timeout_reattach_finished_and_monitorable(
    tmp_path: Path, onejudge_bin: str
) -> None:
    runs = tmp_path / "host-runs"
    plan = _plan(tmp_path, "surface-milestone")
    run_id = _launch_cli(plan, runs, _base(tmp_path), onejudge_bin)
    env = {**os.environ, "AI_ORCHESTRATOR_HOME": str(tmp_path / "state")}
    invalid_reply = subprocess.run(
        ["orchestrator-channel-reply", run_id, "--runs-dir", str(runs), "--timeout", "0.001"],
        input="not-json",
        text=True,
        capture_output=True,
        env=env,
    )
    assert invalid_reply.returncode == 2
    assert "channel-reply" in invalid_reply.stderr
    timed = subprocess.run(
        [
            "orchestrator-channel-next",
            run_id,
            "--runs-dir",
            str(runs),
            "--timeout",
            "0.001",
        ],
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    assert json.loads(timed.stdout) == {"status": "running", "surface": None}

    # A fresh process reattaches to the persisted FIFO and receives the pending surface.
    surface = subprocess.run(
        ["orchestrator-channel-next", run_id, "--runs-dir", str(runs), "--timeout", "10"],
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    assert json.loads(surface.stdout)["surface"]["kind"] == "milestone"
    reply = subprocess.run(
        ["orchestrator-channel-reply", run_id, "--runs-dir", str(runs)],
        input=json.dumps({"completion": True, "reason": "done"}),
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    assert reply.stderr == ""
    _wait_report(runs / run_id / "orchestrator" / "report.json")
    finished = subprocess.run(
        ["orchestrator-channel-next", run_id, "--runs-dir", str(runs), "--timeout", "0.1"],
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    assert json.loads(finished.stdout) == {"status": "finished"}
    status = json.loads((runs / run_id / "round-01" / "status.json").read_text())
    assert isinstance(status["pid"], int) and status["host"]


def test_orchestrate_cli_reports_launch_boundary_failures(tmp_path: Path) -> None:
    missing = subprocess.run(
        ["orchestrator-orchestrate", str(tmp_path / "missing.json")],
        text=True,
        capture_output=True,
    )
    assert missing.returncode == 2
    assert "plan does not exist" in missing.stderr

    plan = _plan(tmp_path, "surface-milestone")
    split_base = _base(tmp_path)
    value = yaml.safe_load(split_base.read_text(encoding="utf-8"))
    value["provider"] = {"kind": "split"}
    split_base.write_text(yaml.safe_dump(value), encoding="utf-8")
    invalid_provider = subprocess.run(
        ["orchestrator-orchestrate", str(plan), "--base", str(split_base)],
        text=True,
        capture_output=True,
    )
    assert invalid_provider.returncode == 2
    assert "provider kind" in invalid_provider.stderr

    missing_binary = subprocess.run(
        [
            "orchestrator-orchestrate",
            str(plan),
            "--runs-dir",
            str(tmp_path / "other-runs"),
            "--base",
            str(_base(tmp_path)),
            "--onejudge-bin",
            "definitely-missing-onejudge",
        ],
        text=True,
        capture_output=True,
    )
    assert missing_binary.returncode == 2
    assert "binary not found" in missing_binary.stderr
