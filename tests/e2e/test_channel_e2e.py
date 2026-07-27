"""Real onejudge split-provider journey for the live planner channel."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator import BASE_CONFIG, REPO_ROOT, gitops
from orchestrator.dispatch import launch_orchestrator
from orchestrator.labels import parse_labels
from orchestrator.launch import LAUNCH_RECORD_NAME, read_launch_info, read_provenance
from orchestrator.read_model import resolve_launch
from orchestrator.registry import Registry
from orchestrator.watchdog import ProcessId, process_activity

FAKE_BACKEND = REPO_ROOT / "tests" / "e2e" / "fake_backend.py"
MOCK_ONEHARNESS = REPO_ROOT / "tests" / "e2e" / "mock_oneharness.py"


def _base(tmp_path: Path) -> Path:
    value = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    value["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    path = tmp_path / "base.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return path


def _oneharness_worker_base(tmp_path: Path) -> Path:
    value = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    value["provider"] = {"kind": "oneharness", "bin": "oneharness"}
    path = tmp_path / "oneharness-worker-base.yaml"
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
    env: dict[str, str] | None = None,
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
        env={**os.environ, **(env or {})},
        text=True,
        capture_output=True,
        check=True,
    )
    return str(json.loads(launched.stdout)["run_id"])


def test_due_heartbeat_surfaces_during_active_step_and_disabled_run_stays_silent(
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
                        "task": (
                            f"slow-branch {witness} pacemaker-slow complete-now heartbeat-channel"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    failed_check_in = tmp_path / "failed-check-in"
    run_id = _launch_cli(
        plan,
        runs,
        _base(tmp_path),
        onejudge_bin,
        heartbeat_interval=0.5,
        env={"FAKE_CHECK_IN_FAIL_ONCE": str(failed_check_in)},
    )
    # llmlint: ignore[tests_mirror_real_usage] Required durable clock audit has no CLI view.
    heartbeat_path = runs / run_id / "channel" / "heartbeat.json"
    # llmlint: ignore[tests_mirror_real_usage] Required queue audit has no CLI view.
    initial_state = json.loads(heartbeat_path.read_text())
    cleared_state: dict[str, object] | None = None
    failure_deadline = deadline(120)
    while time.monotonic() < failure_deadline:
        current = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        if failed_check_in.is_file() and current["in_flight"] is False:
            cleared_state = current
            break
        time.sleep(0.01)
    assert cleared_state is not None
    assert cleared_state["due"] is False
    retry_not_before = cleared_state["retry_not_before"]
    assert isinstance(retry_not_before, int | float)
    assert retry_not_before > time.time()
    assert witness.is_file()
    queued_path = runs / run_id / "channel" / "heartbeat-surface.json"
    queue_deadline = deadline(120)
    while not queued_path.is_file() and time.monotonic() < queue_deadline:
        time.sleep(0.01)
    assert queued_path.is_file()
    assert failed_check_in.read_text(encoding="utf-8") == "failed\n"
    assert not (runs / run_id / "channel" / "check-in-message.txt").exists()
    # llmlint: ignore[tests_mirror_real_usage] The acceptance contract requires the
    # durable failed-attempt audit and cleared claim; neither has an operator CLI.
    check_in_log = runs / run_id / "channel" / "check-in.log"
    failure_records = [
        json.loads(line) for line in check_in_log.read_text(encoding="utf-8").splitlines()
    ]
    assert len(failure_records) == 1
    assert failure_records[0]["succeeded"] is False
    # llmlint: ignore[tests_mirror_real_usage] Exact dispatch dedup is observable only
    # at the paid-provider seam; reconcile, onejudge, and the FIFO remain real here.
    attempts = (
        (runs / run_id / "channel" / "check-in-dispatches.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert attempts == ["failed", "success"]
    # llmlint: ignore[tests_mirror_real_usage] Required pre-consumption audit has no CLI view.
    queued_state = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    # llmlint: ignore[tests_mirror_real_usage] Required journal audit has no CLI view.
    queued_events = (runs / run_id / "events.jsonl").read_text(encoding="utf-8")
    assert queued_state["last_surface_at"] == initial_state["last_surface_at"]
    assert queued_state["due"] is True
    # llmlint: ignore[tests_mirror_real_usage] Acceptance requires proving the
    # failed attempt's durable in-flight claim clears before its retry succeeds.
    assert queued_state["in_flight"] is True
    # llmlint: ignore[tests_mirror_real_usage] Acceptance requires proving retry
    # waits for the next durable heartbeat interval rather than the next tick.
    assert queued_path.stat().st_mtime >= queued_state["retry_not_before"]
    assert '"kind":"planner-surfaced"' not in queued_events
    # llmlint: ignore[tests_mirror_real_usage] The deterministic command provider
    # replaces only the paid model and records labels from the real subprocess env.
    recorded_labels = parse_labels(
        (runs / run_id / "channel" / "check-in-labels.txt").read_text(encoding="utf-8")
    )
    assert recorded_labels["agent_role"] == "check-in"
    assert recorded_labels["persona"] == "check-in"
    expected_message = (
        "active-worker: executing the slow agent step; "
        "evidence: node-started is recorded and node-settled is absent; follow-ups: none"
    )
    heartbeats: list[dict[str, object]] = []
    while len(heartbeats) < 3:
        heartbeat = _wait_surface(run_id, runs, wait_seconds=120)
        if heartbeat["surface"] == {
            "kind": "heartbeat",
            "message": expected_message,
            "blocking": False,
        }:
            heartbeats.append(heartbeat)
    assert len(heartbeats) == 3
    state = json.loads(heartbeat_path.read_text())
    assert state["last_surface_at"] > initial_state["last_surface_at"]

    while True:
        boundary = _wait_surface(run_id, runs, wait_seconds=120)
        boundary_surface = boundary["surface"]
        assert isinstance(boundary_surface, dict)
        if boundary_surface["kind"] != "heartbeat":
            break
    assert boundary_surface["kind"] == "milestone"
    wait_deadline = deadline(5)
    while True:
        reset = json.loads(heartbeat_path.read_text())
        if reset["due"] is False or time.monotonic() >= wait_deadline:
            break
        time.sleep(0.01)
    assert reset["due"] is False
    assert reset["last_surface_at"] >= state["last_surface_at"]
    assert _next_cli(run_id, runs, timeout="0.02").get("surface") is None
    _reply_cli(run_id, runs, {"completion": True, "reason": "verified heartbeat"})
    _wait_report(runs / run_id / "orchestrator" / "report.json")
    events = [
        json.loads(line)
        for line in (runs / run_id / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    surfaced = [event for event in events if event["kind"] == "planner-surfaced"]
    assert len([event for event in surfaced if event["detail"]["kind"] == "heartbeat"]) >= 3

    disabled_plan = json.loads(plan.read_text(encoding="utf-8"))
    disabled_plan["tasks"][0]["task"] = f"slow-branch {witness} complete-now heartbeat-channel"
    plan.write_text(json.dumps(disabled_plan), encoding="utf-8")
    disabled_id = _launch_cli(
        plan,
        runs,
        _base(tmp_path),
        onejudge_bin,
        heartbeat_interval=10,
        requested_run_id="heartbeat-disabled",
    )
    _reply_cli(
        disabled_id,
        runs,
        {
            "completion": False,
            "reason": "disable the pacemaker",
            "message": "continue",
            "heartbeat_interval": False,
        },
    )
    disabled_boundary = _wait_surface(disabled_id, runs, wait_seconds=120)
    assert disabled_boundary["surface"]["kind"] == "milestone"
    assert _next_cli(disabled_id, runs, timeout="0.15").get("surface") is None
    _reply_cli(disabled_id, runs, {"completion": True, "reason": "verified disabled"})
    _wait_report(runs / disabled_id / "orchestrator" / "report.json")
    disabled_events = (runs / disabled_id / "events.jsonl").read_text(encoding="utf-8")
    assert '"kind":"planner-surfaced"' not in disabled_events

    for target, message, timeout, diagnostic in (
        (run_id, "", "1", "non-empty"),
        (run_id, "status", "-1", "positive, finite"),
        ("missing-run", "status", "1", "valid run ids"),
    ):
        rejected = subprocess.run(
            [
                "just",
                "channel-surface",
                target,
                message,
                "--runs-dir",
                str(runs),
                "--timeout",
                timeout,
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert rejected.returncode == 2
        assert diagnostic in rejected.stderr

    # Direct corruption exercises the defensive parser used after disk damage or a
    # torn legacy write while the surrounding poll views stay public.
    # llmlint: ignore[tests_mirror_real_usage] No public command can corrupt this state.
    heartbeat_path.write_text(
        json.dumps({"last_surface_at": "bad", "interval_s": 1, "due": False, "enabled": True}),
        encoding="utf-8",
    )
    corrupt_monitor = subprocess.run(
        ["just", "monitor", run_id, "--once", "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert corrupt_monitor.returncode == 0
    assert "Traceback" not in corrupt_monitor.stderr
    corrupt_status = subprocess.run(
        ["just", "status", "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "planner update due" not in corrupt_status.stdout


def test_completed_check_in_without_surface_is_logged_and_retried(
    tmp_path: Path, onejudge_bin: str
) -> None:
    runs = tmp_path / "runs"
    witness = tmp_path / "slow-witness"
    plan = tmp_path / "missing-check-in-surface.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "missing-check-in-surface",
                "tasks": [
                    {
                        "id": "active-worker",
                        "persona": "engineer",
                        "task": f"slow-branch {witness} pacemaker-slow complete-now",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    skipped = tmp_path / "skipped-check-in-surface"
    run_id = _launch_cli(
        plan,
        runs,
        _base(tmp_path),
        onejudge_bin,
        heartbeat_interval=0.5,
        env={"FAKE_CHECK_IN_SKIP_SURFACE_ONCE": str(skipped)},
    )
    channel = runs / run_id / "channel"
    # llmlint: ignore[tests_mirror_real_usage] The acceptance contract requires
    # the durable missing-surface failure diagnostic, which has no operator CLI.
    failure_log = channel / "check-in.log"
    failure_deadline = deadline(120)
    failure: dict[str, object] | None = None
    while time.monotonic() < failure_deadline:
        if failure_log.is_file():
            records = [
                json.loads(line) for line in failure_log.read_text(encoding="utf-8").splitlines()
            ]
            if records:
                failure = records[0]
                break
        time.sleep(0.01)
    assert skipped.read_text(encoding="utf-8") == "skipped\n"
    assert failure is not None
    assert failure["succeeded"] is False
    assert failure["detail"] == (
        "RuntimeError: check-in agent did not surface a completed status update"
    )

    queued = channel / "heartbeat-surface.json"
    queue_deadline = deadline(120)
    while not queued.is_file() and time.monotonic() < queue_deadline:
        time.sleep(0.01)
    assert queued.is_file()
    # llmlint: ignore[tests_mirror_real_usage] Exact dispatch/retry dedup is
    # observable only at the paid-provider seam; onejudge and orchestration stay real.
    assert (channel / "check-in-dispatches.txt").read_text().splitlines() == [
        "missing-surface",
        "success",
    ]
    heartbeat = _wait_surface(run_id, runs, wait_seconds=120)
    assert heartbeat["surface"] == {
        "kind": "heartbeat",
        "message": (
            "active-worker: executing the slow agent step; "
            "evidence: node-started is recorded and node-settled is absent; "
            "follow-ups: none"
        ),
        "blocking": False,
    }
    assert witness.is_file()

    while True:
        boundary = _wait_surface(run_id, runs, wait_seconds=120)
        if boundary["surface"]["kind"] != "heartbeat":
            break
    assert boundary["surface"]["kind"] == "milestone"
    _reply_cli(run_id, runs, {"completion": True, "reason": "verified missing surface"})
    _wait_report(runs / run_id / "orchestrator" / "report.json")


@pytest.mark.parametrize(
    ("sentinel", "underlying_error"),
    [
        ("provider-errors", "provider error"),
        ("infrastructure-sigkill", "oneharness exited with signal: 9 (SIGKILL)"),
        ("infrastructure-auth", "harness failed (auth): login required"),
        (
            "infrastructure-v03-write",
            "harness claude-code cannot write v0.3 history telemetry",
        ),
        (
            "infrastructure-v03-incomplete",
            "new history record lacks complete v0.3 telemetry",
        ),
        (
            "infrastructure-v10-write",
            "harness codex cannot write v1.0 history telemetry",
        ),
        (
            "infrastructure-v10-incomplete",
            "new history run lacks complete v1.0 telemetry",
        ),
        ("infrastructure-enospc", "[Errno 28] No space left on device"),
        ("infrastructure-oom", "worker was OOMKilled"),
        ("infrastructure-preflight", "scratch filesystem at /tmp has 1 bytes free"),
    ],
)
def test_infrastructure_failure_is_terminal_blocker_without_second_round(
    tmp_path: Path, onejudge_bin: str, sentinel: str, underlying_error: str
) -> None:
    """A real failed provider dispatch stops iteration and names its cause."""
    runs = tmp_path / f"{sentinel}-runs"
    run_id = _launch_cli(_plan(tmp_path, sentinel), runs, _base(tmp_path), onejudge_bin)

    blocker = _wait_surface(run_id, runs, wait_seconds=120)

    surface = blocker["surface"]
    assert isinstance(surface, dict)
    assert surface["kind"] == "proposal" and surface["blocking"] is True
    assert "terminal infrastructure failure; dispatch cannot run" in str(surface["message"])
    assert underlying_error in str(surface["message"])
    first_result = _wait_report(runs / run_id / "round-01" / "result.json")
    failed = first_result["results"]["worker"]
    assert failed["status"] == "failed"
    assert failed["outcome"] == "infrastructure-failure"
    assert underlying_error in failed["error"]
    _reply_cli(
        run_id,
        runs,
        {
            "completion": False,
            "message": "infrastructure failure acknowledged",
            "reason": "the provider must be repaired outside this run",
        },
    )
    boundary = _wait_surface(run_id, runs, wait_seconds=120)
    assert boundary["surface"]["kind"] in {"milestone", "closeout"}
    assert not (runs / run_id / "round-02").exists()
    _reply_cli(
        run_id,
        runs,
        {"completion": True, "reason": "terminal infrastructure blocker verified"},
    )
    _wait_report(runs / run_id / "orchestrator" / "report.json")


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


def _kill_new_agent_worker(
    orchestrator_pid: int,
    known_status_files: set[Path],
    barrier: Path,
) -> Path:
    wait_deadline = deadline(10)
    while time.monotonic() < wait_deadline:
        descendants = process_activity(ProcessId(orchestrator_pid)).pids
        candidates = (
            set(Path("/tmp").glob("orchestrator-watchdog-*/agent/agent.child.pid"))
            - known_status_files
        )
        if barrier.exists():
            for status_file in candidates:
                try:
                    worker_pid = ProcessId(int(status_file.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    continue
                if worker_pid in descendants:
                    os.kill(worker_pid, signal.SIGTERM)
                    return status_file
        time.sleep(0.02)
    raise AssertionError("lifecycle agent did not reach the provider barrier")


def test_orchestrator_retries_dead_lifecycle_worker_then_surfaces_blocker(
    tmp_path: Path,
    bare_origin,
    onejudge_bin: str,
    oneharness_bin: str,
    monkeypatch,
) -> None:
    """Kill both real lifecycle attempts and drive retry/exhaustion over the live channel."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical")
    Registry().register(
        str(canonical),
        workflow="local",
        repo_type="single-owner",
        gate="true",
    )
    runs = tmp_path / "runs"
    barrier = tmp_path / "worker-provider-ready"
    pre_round_release = tmp_path / "start-first-round"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # llmlint: ignore[e2e_not_mocked] This replaces only the paid agent harness. The real
    # onejudge, lifecycle, wrapper, tracked PID, process kill, git, retry, and channel run.
    (bin_dir / "oneharness").symlink_to(MOCK_ONEHARNESS)
    plan = tmp_path / "dead-lifecycle.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "lifecycle-worker-death-retry",
                "tasks": [
                    {
                        "id": "change",
                        "repo": str(canonical),
                        "persona": "engineer",
                        "task": (
                            "lifecycle-worker-death-retry "
                            f"pre-round-pause {pre_round_release} start-worker"
                        ),
                        "verify_cmd": ["true"],
                        "workflow": "local",
                        "repo_type": "single-owner",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ORCHESTRATOR_WORKER_HEARTBEAT_TIMEOUT", "1")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("REAL_ONEHARNESS_BIN", oneharness_bin)
    monkeypatch.setenv("MOCK_AGENT_BARRIER", str(barrier))
    status_files = set(Path("/tmp").glob("orchestrator-watchdog-*/agent/agent.child.pid"))
    run_id = _launch_cli(plan, runs, _oneharness_worker_base(tmp_path), onejudge_bin)
    run_dir = runs / run_id
    pre_round_release.touch()
    orchestrator_status = json.loads(
        (run_dir / "orchestrator" / "status.json").read_text(encoding="utf-8")
    )
    first_status = _kill_new_agent_worker(orchestrator_status["pid"], status_files, barrier)

    first_surface = _wait_surface(run_id, runs)
    assert first_surface["surface"]["kind"] == "milestone"
    first_result = json.loads((run_dir / "round-01" / "result.json").read_text(encoding="utf-8"))
    assert first_result["results"]["change"]["status"] == "failed"
    assert first_result["results"]["change"]["outcome"] == "not-completed"
    assert first_result["results"]["change"]["error"].endswith("worker-died")

    barrier.unlink()
    _convenience_cli("channel-continue", run_id, runs, "retry change")
    _kill_new_agent_worker(orchestrator_status["pid"], status_files | {first_status}, barrier)

    exhausted = _wait_surface(run_id, runs)
    assert exhausted["surface"] == {
        "kind": "blocker",
        "message": (
            "lifecycle worker died after its bounded retry; planner intervention is required"
        ),
    }
    second_result = json.loads((run_dir / "round-02" / "result.json").read_text(encoding="utf-8"))
    assert second_result["results"]["change"]["status"] == "failed"
    assert second_result["results"]["change"]["outcome"] == "not-completed"
    assert second_result["results"]["change"]["error"].endswith("worker-died")
    retry_plan = json.loads((run_dir / "round-02" / "plan.json").read_text(encoding="utf-8"))
    assert retry_plan["tasks"][0]["id"] == "change"
    _convenience_cli("channel-approve", run_id, runs)
    _wait_report(run_dir / "orchestrator" / "report.json")


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
    expected_wait = "waiting for planner decision: blocker: plan departure needs a decision"
    assert f"* {run_id}" in listed.stdout
    assert expected_wait in listed.stdout
    status = subprocess.run(
        ["just", "status", "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert f"{run_id}: {expected_wait}" in status.stdout
    pending_path = run_dir / "channel" / "planner-pending.json"
    pending = pending_path.read_text(encoding="utf-8")
    # llmlint: ignore-block[tests_mirror_real_usage] No public producer can corrupt this
    # durable file; a directory at the file path deterministically exercises the
    # unreadable-state boundary even when the e2e process runs as root.
    for damage in ("malformed", "unreadable"):
        if damage == "malformed":
            pending_path.write_text("{broken", encoding="utf-8")
        else:
            pending_path.unlink()
            pending_path.mkdir()
        for command in ("runs", "status"):
            damaged = subprocess.run(
                ["just", command, "--runs-dir", str(runs)],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            assert damaged.returncode == 0
            assert "Traceback" not in damaged.stderr
            assert expected_wait not in damaged.stdout
            if command == "runs":
                assert f"* {run_id}" in damaged.stdout
                assert "(1 done)" in damaged.stdout
        if pending_path.is_dir():
            pending_path.rmdir()
    # llmlint: ignore-end[tests_mirror_real_usage]
    pending_path.write_text(pending, encoding="utf-8")
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
    tmp_path: Path, onejudge_bin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cover the Python launch boundary while still driving the real onejudge process."""
    runs = tmp_path / "api-runs"
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    run_id = launch_orchestrator(
        _plan(tmp_path, "surface-milestone"),
        runs_dir=runs,
        base_path=_base(tmp_path),
        onejudge_bin=onejudge_bin,
        skill_provider={"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]},
        turn_timeout=int(e2e_timeout(10)),
        launcher="claude-code",
        launcher_session_id="planner-session",
    )
    run_dir = runs / run_id
    status = json.loads((run_dir / "orchestrator" / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "running"
    assert isinstance(status["pid"], int) and status["host"]

    # A real launch splits its provenance: the run directory gets only the join key,
    # and the sensitive session id lands in the out-of-repo record the read API
    # resolves. This is the production write path, not a manufactured fixture.
    launch_id = read_launch_info(run_dir)
    assert launch_id is not None
    assert "planner-session" not in (run_dir / LAUNCH_RECORD_NAME).read_text(encoding="utf-8")
    provenance = read_provenance(launch_id)
    assert provenance is not None
    assert provenance["launcher"] == "claude-code"
    assert provenance["launcher_session_id"] == "planner-session"
    # And the read API's own join reports that launcher back.
    assert resolve_launch(run_dir) == {"launch_id": launch_id, "launcher": "claude-code"}
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
    expected_wait = (
        "waiting for planner reply: proposal: "
        "discoverer: - Add a regression test for the adjacent edge case."
    )
    listed = subprocess.run(
        ["just", "runs", "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert expected_wait in listed.stdout
    status = subprocess.run(
        ["just", "status", "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert f"{run_id}: {expected_wait}" in status.stdout
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

    while True:
        boundary = _next_cli(run_id, runs)
        if boundary.get("surface", {}).get("kind") != "heartbeat":
            break
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


def test_orchestrate_cli_refuses_unusable_launch_provenance(tmp_path: Path) -> None:
    """Provenance is validated at the launch boundary, before anything is spawned.

    A record that cannot be written correctly must stop the launch rather than
    produce a run whose session join silently never resolves.
    """
    plan = _plan(tmp_path, "surface-milestone")
    base = _base(tmp_path)

    def orchestrate(
        *extra: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "orchestrator-orchestrate",
                str(plan),
                "--runs-dir",
                str(tmp_path / f"runs-{len(extra)}-{bool(env)}"),
                "--base",
                str(base),
                *extra,
            ],
            text=True,
            capture_output=True,
            env=env,
        )

    # A multiline session id would smuggle a second line into a history label.
    multiline = orchestrate("--launcher", "codex", "--launcher-session", "one\ntwo")
    assert multiline.returncode == 2
    assert "session id" in multiline.stderr

    # A launcher outside the closed vocabulary is refused by the CLI itself.
    unknown = orchestrate("--launcher", "not-a-harness")
    assert unknown.returncode == 2
    assert "--launcher" in unknown.stderr

    # No absolute state directory: the protected record has nowhere correct to go.
    broken_state = dict(os.environ, HOME="relative/home")
    broken_state.pop("XDG_STATE_HOME", None)
    relative_home = orchestrate(
        "--launcher", "codex", "--launcher-session", "planner", env=broken_state
    )
    assert relative_home.returncode == 2
    assert "absolute state directory" in relative_home.stderr
