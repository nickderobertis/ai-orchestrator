"""Real onejudge split-provider journey for the live planner channel."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml
from waits import deadline
from waits import timeout as e2e_timeout

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


def _proposal_plan(
    tmp_path: Path,
    witness: Path,
    provider_barrier: tuple[Path, Path] | None = None,
) -> Path:
    barrier = (
        ""
        if provider_barrier is None
        else f" provider-barrier-ready={provider_barrier[0]} "
        f"provider-barrier-release={provider_barrier[1]}"
    )
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
                        "task": f"slow-branch {witness}{barrier}",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _wait_report(path: Path) -> dict[str, object]:
    wait_deadline = deadline(15)
    while time.monotonic() < wait_deadline:
        if path.is_file() and path.stat().st_size:
            return json.loads(path.read_text(encoding="utf-8"))
        time.sleep(0.02)
    raise AssertionError(f"report did not land: {path}")


def _launch_cli(
    plan: Path,
    runs: Path,
    base: Path,
    onejudge_bin: str,
    requested_run_id: str | None = None,
    heartbeat_interval: float | None = None,
) -> str:
    run_id_args = ["--run-id", requested_run_id] if requested_run_id else []
    heartbeat_args = (
        ["--heartbeat-interval", str(heartbeat_interval)] if heartbeat_interval is not None else []
    )
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
            *run_id_args,
            *heartbeat_args,
            "--skill-command",
            sys.executable,
            str(FAKE_BACKEND),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return str(json.loads(launched.stdout)["run_id"])


def test_due_heartbeat_is_agent_synthesized_and_normal_surface_resets_clock(
    tmp_path: Path, onejudge_bin: str
) -> None:
    runs = tmp_path / "runs"
    witness = tmp_path / "slow-witness"
    plan = tmp_path / "heartbeat-channel.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "heartbeat-channel",
                "tasks": [
                    {
                        "id": "active-worker",
                        "persona": "engineer",
                        "task": f"slow-branch {witness} complete-now heartbeat-channel",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    run_id = _launch_cli(plan, runs, _base(tmp_path), onejudge_bin, heartbeat_interval=0.1)
    heartbeat = _wait_surface(run_id, runs, wait_seconds=120)
    assert heartbeat["surface"] == {
        "kind": "heartbeat",
        "message": "active worker: round complete; follow-ups: none",
        "blocking": False,
    }
    heartbeat_path = runs / run_id / "channel" / "heartbeat.json"
    wait_deadline = deadline(5)
    while True:
        state = json.loads(heartbeat_path.read_text())
        if state["due"] is False or time.monotonic() >= wait_deadline:
            break
        time.sleep(0.01)
    assert state["due"] is False

    boundary = _wait_surface(run_id, runs, wait_seconds=120)
    boundary_surface = boundary["surface"]
    assert isinstance(boundary_surface, dict)
    assert boundary_surface["kind"] == "milestone"
    reset = json.loads(heartbeat_path.read_text())
    assert reset["due"] is False
    assert reset["last_surface_at"] >= state["last_surface_at"]
    assert _next_cli(run_id, runs, timeout="0.02").get("surface") is None
    _reply_cli(run_id, runs, {"completion": True, "reason": "verified heartbeat"})


def _next_cli(run_id: str, runs: Path, timeout: str | None = None) -> dict[str, object]:
    wait_timeout = str(e2e_timeout(10)) if timeout is None else timeout
    result = subprocess.run(
        ["just", "channel-next", run_id, "--runs-dir", str(runs), "--timeout", wait_timeout],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def _wait_surface(run_id: str, runs: Path, *, wait_seconds: float = 15) -> dict[str, object]:
    wait_deadline = deadline(wait_seconds)
    while time.monotonic() < wait_deadline:
        value = _next_cli(run_id, runs, timeout=str(e2e_timeout(2)))
        if value.get("surface") is not None:
            return value
    raise AssertionError(f"surface did not arrive for {run_id}")


def _reply_cli(run_id: str, runs: Path, value: dict[str, object]) -> None:
    subprocess.run(
        ["just", "channel-reply", run_id, "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        input=json.dumps(value),
        text=True,
        capture_output=True,
        check=True,
    )


def _convenience_cli(recipe: str, run_id: str, runs: Path, message: str | None = None) -> None:
    command = ["just", recipe, run_id]
    if message is not None:
        command.append(message)
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
    run_id = _launch_cli(
        _plan(tmp_path, "surface-blocker"),
        runs,
        _base(tmp_path),
        onejudge_bin,
        requested_run_id="actual-run-id",
    )
    run_dir = runs / run_id
    launch = json.loads((run_dir / "launch.json").read_text(encoding="utf-8"))
    assert launch["commands"] == {
        "channel_next": f"just channel-next {run_id}",
        "monitor": f"just monitor {run_id}",
    }
    assert f"just channel-next {run_id}" in (run_dir / "planner.md").read_text(encoding="utf-8")
    unknown = subprocess.run(
        ["just", "channel-next", "mistyped", "--runs-dir", str(runs), "--timeout", "0"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert unknown.returncode == 2
    assert f"valid run ids: {run_id}" in unknown.stderr
    release = tmp_path / "release-pre-round"
    pre_round_plan = tmp_path / "pre-round.json"
    pre_round_plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "pre-round-plan",
                "tasks": [
                    {
                        "id": "worker",
                        "persona": "engineer",
                        "task": f"pre-round-pause {release} complete-now no-assessment",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    pre_round_id = _launch_cli(pre_round_plan, runs, _base(tmp_path), onejudge_bin)
    pre_round_listing = subprocess.run(
        ["just", "runs", "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert f"* {pre_round_id}  ACTIVE  (orchestrator running)" in pre_round_listing.stdout
    release.touch()
    assert _wait_surface(pre_round_id, runs)["surface"]["kind"] == "milestone"
    _convenience_cli("channel-approve", pre_round_id, runs)
    _wait_report(runs / pre_round_id / "orchestrator" / "report.json")
    blocker = _next_cli("nested-surface-blocker", runs)
    assert blocker["surface"] == {
        "kind": "blocker",
        "message": "plan departure needs a decision",
    }
    monitored = subprocess.run(
        ["just", "monitor", "nested-surface-blocker", "--once", "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert "ACK REQUIRED" in monitored.stdout
    listed = subprocess.run(
        ["just", "runs", "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert f"* {run_id}" in listed.stdout
    _convenience_cli("channel-continue", run_id, runs, "retry X")
    closeout = _next_cli(run_id, runs)
    assert closeout["surface"]["kind"] == "closeout"
    assert "retry X" in closeout["surface"]["message"]
    _convenience_cli("channel-approve", run_id, runs)

    report = _wait_report(run_dir / "orchestrator" / "report.json")
    assert report["stopped_early"] is False
    assert set(runs.iterdir()) == {runs / pre_round_id, run_dir}
    nested_result = json.loads((run_dir / "round-01" / "result.json").read_text())
    assert nested_result["results"]["worker"]["status"] == "done"
    assert (run_dir / "channel").exists()

    duplicate_plan = _plan(tmp_path, "surface-blocker")
    duplicate_ids = [
        _launch_cli(
            duplicate_plan,
            runs,
            _base(tmp_path),
            onejudge_bin,
            requested_run_id=f"duplicate-{index}",
        )
        for index in (1, 2)
    ]
    ambiguous = subprocess.run(
        ["just", "channel-next", "nested-surface-blocker", "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert ambiguous.returncode == 2
    assert "duplicate-1, duplicate-2" in ambiguous.stderr
    ambiguous_monitor = subprocess.run(
        ["just", "monitor", "nested-surface-blocker", "--once", "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert ambiguous_monitor.returncode == 2
    assert "duplicate-1, duplicate-2" in ambiguous_monitor.stderr
    for duplicate_id in duplicate_ids:
        assert _next_cli(duplicate_id, runs)["surface"]["kind"] == "blocker"
        _convenience_cli("channel-continue", duplicate_id, runs, "continue")
        assert _next_cli(duplicate_id, runs)["surface"]["kind"] == "closeout"
        _convenience_cli("channel-approve", duplicate_id, runs)
        _wait_report(runs / duplicate_id / "orchestrator" / "report.json")
    stale = subprocess.run(
        ["just", "channel-next", "nested-surface-blocker", "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert stale.returncode == 2
    assert "valid run ids" in stale.stderr
    stale_monitor = subprocess.run(
        ["just", "monitor", "nested-surface-blocker", "--once", "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert stale_monitor.returncode == 2
    assert "valid run ids" in stale_monitor.stderr


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
        turn_timeout=int(e2e_timeout(10)),
    )
    run_dir = runs / run_id
    status = json.loads((run_dir / "orchestrator" / "status.json").read_text(encoding="utf-8"))
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
        [
            "orchestrator-channel-next",
            run_id,
            "--runs-dir",
            str(runs),
            "--timeout",
            str(e2e_timeout(10)),
        ],
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
    provider_ready = tmp_path / "unrelated.ready"
    provider_release = tmp_path / "unrelated.release"
    run_id = _launch_cli(
        _proposal_plan(tmp_path, witness, (provider_ready, provider_release)),
        runs,
        _base(tmp_path),
        onejudge_bin,
    )

    detached = _next_cli(run_id, runs, timeout="0.001")
    assert detached == {"status": "running", "surface": None}
    heartbeat_path = runs / run_id / "channel" / "heartbeat.json"
    before_proposal = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    proposal = _next_cli(run_id, runs)
    assert proposal["surface"] == {
        "kind": "proposal",
        "message": "discoverer: - Add a regression test for the adjacent edge case.",
        "blocking": False,
    }
    wait_deadline = deadline(5)
    while True:
        after_proposal = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        if (
            after_proposal["last_surface_at"] > before_proposal["last_surface_at"]
            or time.monotonic() >= wait_deadline
        ):
            break
        time.sleep(0.01)
    assert after_proposal["last_surface_at"] > before_proposal["last_surface_at"]
    assert after_proposal["due"] is False
    monitored = subprocess.run(
        ["just", "monitor", run_id, "--once", "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert "REPLY REQUESTED" in monitored.stdout
    assert provider_ready.read_text(encoding="utf-8") == "ready\n"
    _reply_cli(
        run_id,
        runs,
        {
            "completion": False,
            "message": "defer to next round",
            "reason": "defer to next round",
            "heartbeat_interval": 2,
        },
    )

    verdict_path = runs / run_id / "channel" / "planner-verdict.json"
    wait_deadline = deadline(5)
    while time.monotonic() < wait_deadline:
        heartbeat_state = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        if verdict_path.is_file() and heartbeat_state["interval_s"] == 2:
            break
        time.sleep(0.02)
    assert heartbeat_state["enabled"] is True
    assert heartbeat_state["interval_s"] == 2
    assert json.loads(verdict_path.read_text(encoding="utf-8")) == {
        "completion": False,
        "message": "defer to next round",
        "reason": "defer to next round",
        "heartbeat_interval": 2,
    }
    assert not (runs / run_id / "orchestrator" / "report.json").stat().st_size
    provider_release.write_text("release\n", encoding="utf-8")
    wait_deadline = deadline(5)
    while time.monotonic() < wait_deadline:
        if witness.is_file() and witness.read_text(encoding="utf-8").count("tick") == 2:
            break
        time.sleep(0.02)
    assert witness.read_text(encoding="utf-8").count("tick") == 2

    boundary = _next_cli(run_id, runs)
    assert boundary["surface"]["kind"] in {"milestone", "closeout"}
    _reply_cli(
        run_id,
        runs,
        {"completion": True, "reason": "verified", "heartbeat_interval": False},
    )
    _wait_report(runs / run_id / "orchestrator" / "report.json")
    assert json.loads(heartbeat_path.read_text(encoding="utf-8"))["enabled"] is False


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
    assert proposal["round"] == 2
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
