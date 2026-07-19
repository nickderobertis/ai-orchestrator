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


def _proposal_plan(tmp_path: Path, witness: Path) -> Path:
    path = tmp_path / "plan-mid-run-proposal.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "mid-run-proposal",
                "concurrency": 2,
                "tasks": [
                    {
                        "id": "discoverer",
                        "persona": "engineer",
                        "task": "complete-now discover-follow-up",
                    },
                    {
                        "id": "unrelated",
                        "persona": "engineer",
                        "task": f"slow-branch {witness}",
                    },
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


def _launch_cli(
    plan: Path, runs: Path, base: Path, onejudge_bin: str, *, run_id: str | None = None
) -> str:
    explicit_run = ["--run-id", run_id] if run_id is not None else []
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
            *explicit_run,
            "--skill-command",
            sys.executable,
            str(FAKE_BACKEND),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    record = json.loads(launched.stdout)
    return str(record["run_id"])


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


def _convenience_cli(recipe: str, run_id: str, runs: Path, text: str | None = None) -> None:
    command = ["just", recipe, run_id]
    if text is not None:
        command.append(text)
    subprocess.run(
        [*command, "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
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
    blocker = _next_cli("nested-surface-blocker", runs)
    assert blocker["surface"] == {
        "kind": "blocker",
        "message": "plan departure needs a decision",
        "blocking": True,
    }
    monitored = subprocess.run(
        [
            "just",
            "monitor",
            "nested-surface-blocker",
            "--once",
            "--runs-dir",
            str(runs),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "PLANNER REPLY REQUIRED (blocker)" in monitored.stdout
    active = subprocess.run(
        ["just", "runs", "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert f"{run_id}  round-01" in active.stdout
    assert "[ACTIVE]" in active.stdout
    _convenience_cli("channel-continue", "nested-surface-blocker", runs, "retry X")
    closeout = _next_cli(run_id, runs)
    assert closeout["surface"]["kind"] == "closeout"
    assert "retry X" in closeout["surface"]["message"]
    _convenience_cli("channel-approve", run_id, runs)

    report = _wait_report(run_dir / "orchestrator" / "report.json")
    assert report["stopped_early"] is False
    settled = subprocess.run(
        ["just", "runs", "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "[ACTIVE]" not in settled.stdout
    result = json.loads((run_dir / "round-01" / "result.json").read_text())
    assert result["results"]["worker"]["status"] == "done"
    assert (run_dir / "channel").exists()


def test_live_channel_surfaces_large_round_summary(tmp_path: Path, onejudge_bin: str) -> None:
    runs = tmp_path / "large-summary-runs"
    run_id = _launch_cli(
        _plan(tmp_path, "surface-large-summary"), runs, _base(tmp_path), onejudge_bin
    )
    summary = _next_cli(run_id, runs)
    message = summary["surface"]["message"]
    assert isinstance(message, str)
    assert message.startswith("tracked round completed ")
    assert len(message) > 100_000
    _reply_cli(run_id, runs, {"completion": True, "reason": "large summary verified"})
    report = _wait_report(runs / run_id / "orchestrator" / "report.json")
    assert report["stopped_early"] is False


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
    launch = json.loads((run_dir / "launch.json").read_text(encoding="utf-8"))
    assert isinstance(launch["pid"], int) and launch["host"]
    assert launch["channel_id"] == run_id
    assert launch["commands"] == {
        "channel_next": f"just channel-next {run_id} --runs-dir {runs.resolve()}",
        "monitor": f"just monitor {run_id} --runs-dir {runs.resolve()}",
    }
    planner = (run_dir / "planner.md").read_text(encoding="utf-8")
    assert launch["commands"]["channel_next"] in planner
    assert launch["commands"]["monitor"] in planner
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


def test_reattached_planner_replies_to_mid_run_proposal_without_stopping_graph(
    tmp_path: Path, onejudge_bin: str
) -> None:
    runs = tmp_path / "proposal-runs"
    witness = tmp_path / "unrelated.ticks"
    run_id = _launch_cli(_proposal_plan(tmp_path, witness), runs, _base(tmp_path), onejudge_bin)

    detached = _next_cli(run_id, runs, timeout="0.001")
    assert detached == {"status": "running", "surface": None}
    proposal = _next_cli(run_id, runs)
    assert proposal["surface"] == {
        "kind": "proposal",
        "message": "discoverer: - Add a regression test for the adjacent edge case.",
        "blocking": False,
    }
    monitored = subprocess.run(
        ["just", "monitor", run_id, "--once", "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "planner proposal awaiting optional reply (proposal)" in monitored.stdout
    ticks_before_reply = witness.read_text(encoding="utf-8").count("tick")
    _convenience_cli("channel-reject", run_id, runs, "out of scope")

    verdict_path = runs / run_id / "channel" / "planner-verdict.json"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not verdict_path.is_file():
        time.sleep(0.02)
    assert json.loads(verdict_path.read_text(encoding="utf-8")) == {
        "completion": False,
        "message": "stop and address planner rejection",
        "reason": "out of scope",
    }
    assert not (runs / run_id / "orchestrator" / "report.json").stat().st_size
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if witness.read_text(encoding="utf-8").count("tick") > ticks_before_reply:
            break
        time.sleep(0.02)
    assert witness.read_text(encoding="utf-8").count("tick") > ticks_before_reply

    boundary = _next_cli(run_id, runs)
    assert boundary["surface"]["kind"] in {"milestone", "closeout"}
    _reply_cli(run_id, runs, {"completion": True, "reason": "verified"})
    _wait_report(runs / run_id / "orchestrator" / "report.json")


def test_unanswered_mid_run_proposal_does_not_compete_with_boundary_verdict(
    tmp_path: Path, onejudge_bin: str
) -> None:
    runs = tmp_path / "unanswered-proposal-runs"
    witness = tmp_path / "unanswered-unrelated.ticks"
    run_id = _launch_cli(_proposal_plan(tmp_path, witness), runs, _base(tmp_path), onejudge_bin)

    proposal = _next_cli(run_id, runs)
    assert proposal["surface"]["kind"] == "proposal"
    # Deliberately leave the proposal unanswered. The unrelated node still settles,
    # then the pump must relinquish sole FIFO ownership before this boundary appears.
    boundary = _next_cli(run_id, runs)
    assert boundary["surface"]["kind"] in {"milestone", "closeout"}
    assert witness.read_text(encoding="utf-8").count("tick") >= 2
    _reply_cli(run_id, runs, {"completion": True, "reason": "boundary verified"})
    report = _wait_report(runs / run_id / "orchestrator" / "report.json")
    assert report["stopped_early"] is False


def test_continuation_round_proposal_uses_reconciled_round_number(
    tmp_path: Path, onejudge_bin: str
) -> None:
    runs = tmp_path / "continuation-runs"
    witness = tmp_path / "round-two-unrelated.ticks"
    plan = tmp_path / "continuation-plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "continuation-channel",
                "concurrency": 2,
                "tasks": [
                    {"id": "gate", "kind": "human", "task": "Approve continuation"},
                    {
                        "id": "discoverer",
                        "persona": "engineer",
                        "task": "complete-now discover-follow-up",
                        "deps": ["gate"],
                    },
                    {
                        "id": "unrelated",
                        "persona": "engineer",
                        "task": f"slow-branch {witness}",
                        "deps": ["gate"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    run_id = _launch_cli(plan, runs, _base(tmp_path), onejudge_bin)
    boundary = _next_cli(run_id, runs)
    assert boundary["surface"]["kind"] == "milestone"
    _reply_cli(
        run_id,
        runs,
        {"completion": False, "message": "continue round two", "reason": "gate approved"},
    )
    proposal = _next_cli(run_id, runs)
    assert proposal.get("round") == 2, proposal
    _reply_cli(
        run_id,
        runs,
        {"completion": False, "message": "defer", "reason": "next continuation"},
    )
    closeout = _next_cli(run_id, runs)
    assert closeout["surface"]["kind"] == "closeout"
    _reply_cli(run_id, runs, {"completion": True, "reason": "round two verified"})
    _wait_report(runs / run_id / "orchestrator" / "report.json")


def test_orchestrate_cli_reports_launch_boundary_failures(tmp_path: Path) -> None:
    missing = subprocess.run(
        ["orchestrator-orchestrate", str(tmp_path / "missing.json")],
        text=True,
        capture_output=True,
    )
    assert missing.returncode == 2
    assert "plan does not exist" in missing.stderr

    unknown_alias_plan = tmp_path / "unknown-alias.json"
    unknown_alias_plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "tasks": [
                    {
                        "id": "unknown",
                        "repo": "local/does-not-exist",
                        "persona": "engineer",
                        "task": "must not launch",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    unknown_runs = tmp_path / "unknown-runs"
    unknown_alias = subprocess.run(
        [
            "orchestrator-orchestrate",
            str(unknown_alias_plan),
            "--runs-dir",
            str(unknown_runs),
            "--onejudge-bin",
            "definitely-missing-onejudge",
        ],
        text=True,
        capture_output=True,
    )
    assert unknown_alias.returncode == 2
    assert "unknown local checkout alias 'local/does-not-exist'" in unknown_alias.stderr
    assert "just repos" in unknown_alias.stderr
    assert not unknown_runs.exists()

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


def test_supervision_cli_rejects_ambiguous_and_stale_plan_names(
    tmp_path: Path, onejudge_bin: str
) -> None:
    runs = tmp_path / "resolution-runs"
    plan = _plan(tmp_path, "surface-milestone")
    value = json.loads(plan.read_text(encoding="utf-8"))
    value["name"] = "shared plan"
    plan.write_text(json.dumps(value), encoding="utf-8")
    base = _base(tmp_path)
    for run_id in ("shared-a", "shared-b"):
        assert _launch_cli(plan, runs, base, onejudge_bin, run_id=run_id) == run_id
    ambiguous = subprocess.run(
        ["just", "channel-next", "shared plan", "--runs-dir", str(runs), "--timeout", "0"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert ambiguous.returncode == 2
    assert "valid active run ids: shared-a, shared-b" in ambiguous.stderr

    stale = subprocess.run(
        ["just", "monitor", "stale plan", "--once", "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert stale.returncode == 2
    assert "valid run ids: shared-a, shared-b" in stale.stderr
    for run_id in ("shared-a", "shared-b"):
        surface = _next_cli(run_id, runs)
        assert surface["surface"]["kind"] == "milestone"
        _reply_cli(run_id, runs, {"completion": True, "reason": "resolution verified"})
        _wait_report(runs / run_id / "orchestrator" / "report.json")
    assert _next_cli("shared-a", runs, timeout="0.1") == {"status": "finished"}
