"""Real command-surface journeys for host scratch protection."""

from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from orchestrator import REPO_ROOT
from orchestrator.lifecycle import run_repo_task
from orchestrator.scratch import (
    MIN_FREE_BYTES_ENV,
    OWNER_LOCK_NAME,
    WATCHDOG_PATTERN,
    sweep_scratch,
)
from orchestrator.workspace import Workspace

_BLOCKING_GATE = """
import os
import sys
import time
from pathlib import Path

candidate, ready, release = map(Path, sys.argv[1:])
candidate.mkdir(exist_ok=True)
(candidate / "payload").write_text("in-flight", encoding="utf-8")
old = time.time() - 48 * 60 * 60
os.utime(candidate, (old, old))
ready.write_text("ready", encoding="utf-8")
while not release.exists():
    time.sleep(0.02)
"""


def _run_lifecycle_with_blocking_gate(
    origin: str,
    canonical: str,
    workspace_root: str,
    base_path: str,
    persona_dir: str,
    scratch_root: str,
    candidate: str,
    ready: str,
    release: str,
    result_path: str,
) -> None:
    os.environ["TMPDIR"] = scratch_root
    tempfile.tempdir = None
    workspace = Workspace(
        workspace_root,
        resolver=lambda _spec: Path(canonical),
        workflow="local",
    )
    result = run_repo_task(
        origin,
        "complete-now write-unique-change: synchronized scratch sweep",
        "engineer",
        workspace=workspace,
        base_path=base_path,
        persona_dir=persona_dir,
        verify_cmd=[sys.executable, "-c", _BLOCKING_GATE, candidate, ready, release],
        repo_type="single-owner",
    )
    Path(result_path).write_text(
        json.dumps({"ok": result.ok, "outcome": result.outcome, "detail": result.detail}),
        encoding="utf-8",
    )


def _kernel_start_token(pid: int) -> str:
    """Read a process's start time (procfs field 22) without the code under test."""
    fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
    return fields[19]


def _worker_pid_is_gone(directory: Path) -> bool:
    """Report whether the pid this dispatch recorded for its worker has exited."""
    try:
        pid = int((directory / "pid").read_text(encoding="utf-8").split()[0])
    except (OSError, IndexError, ValueError):
        return False
    return not Path(f"/proc/{pid}").exists()


def _wait_for_path(path: Path, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            pytest.fail(f"timed out waiting for {path}")
        time.sleep(0.02)


def test_sweep_recipe_reclaims_orphans_and_preserves_live_scratch(tmp_path: Path) -> None:
    dead = tmp_path / "orchestrator-watchdog-dead"
    dead.mkdir()
    (dead / "pid").write_text("999999999\n", encoding="utf-8")
    (dead / "payload").write_bytes(b"x" * 17)
    live = tmp_path / "orchestrator-watchdog-live"
    live.mkdir()
    (live / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    malformed = tmp_path / "orchestrator-watchdog-malformed"
    malformed.mkdir()
    (malformed / "pid").write_text("bad\n", encoding="utf-8")
    crashed = tmp_path / "orchestrator-watchdog-crashed"
    crashed.mkdir()
    (crashed / OWNER_LOCK_NAME).write_text("999999999 1", encoding="utf-8")
    recycled = tmp_path / "orchestrator-watchdog-recycled"
    recycled.mkdir()
    recycled_record = f"{os.getpid()} 1"
    (recycled / OWNER_LOCK_NAME).write_text(recycled_record, encoding="utf-8")
    owned = tmp_path / "orchestrator-watchdog-owned"
    owned.mkdir()
    (owned / OWNER_LOCK_NAME).write_text(
        f"{os.getpid()} {_kernel_start_token(os.getpid())}", encoding="utf-8"
    )
    # pid 1 belongs to another user, so the sweep cannot signal it and must decide
    # on its procfs identity alone — the case that once pinned any recycled pid.
    foreign = tmp_path / "orchestrator-watchdog-foreign"
    foreign.mkdir()
    (foreign / OWNER_LOCK_NAME).write_text(f"1 {_kernel_start_token(1)}", encoding="utf-8")
    foreign_recycled = tmp_path / "orchestrator-watchdog-foreign-recycled"
    foreign_recycled.mkdir()
    foreign_record = "1 1"
    (foreign_recycled / OWNER_LOCK_NAME).write_text(foreign_record, encoding="utf-8")
    garbled = tmp_path / "orchestrator-watchdog-garbled"
    garbled.mkdir()
    (garbled / OWNER_LOCK_NAME).write_text("not-an-owner-record", encoding="utf-8")
    symlinked = tmp_path / "orchestrator-watchdog-symlinked"
    symlinked.mkdir()
    (symlinked / OWNER_LOCK_NAME).symlink_to(owned / OWNER_LOCK_NAME)
    old_third_party = tmp_path / "oneharness-sdk-old"
    old_third_party.mkdir()
    (old_third_party / "payload").write_bytes(b"y" * 19)
    old = time.time() - 48 * 60 * 60
    os.utime(old_third_party, (old, old))
    recent = tmp_path / "playwright-active"
    recent.mkdir()

    result = subprocess.run(
        ["just", "sweep-scratch", "--root", str(tmp_path)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert not dead.exists()
    assert not crashed.exists()
    assert not recycled.exists()
    assert not garbled.exists()
    assert not foreign_recycled.exists()
    assert not old_third_party.exists()
    assert live.exists()
    assert malformed.exists()
    assert owned.exists()
    assert foreign.exists()
    # A lock reachable only through a symlink is never trusted to name its owner.
    assert symlinked.exists()
    assert recent.exists()
    assert "removed 6 directories" in result.stdout
    reclaimed = (
        46
        + len("999999999 1")
        + len(recycled_record)
        + len(foreign_record)
        + len("not-an-owner-record")
    )
    assert f"reclaimed {reclaimed} bytes" in result.stdout
    assert "retained 5 watchdog directories not proven reclaimable" in result.stdout


def test_third_party_sweep_skips_inflight_lifecycle_then_reclaims(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    command_base: Callable[..., Path],
    personas_dir: Path,
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    candidate = scratch / "visual-inflight"
    dead_watchdog = scratch / "orchestrator-watchdog-dead"
    dead_watchdog.mkdir()
    (dead_watchdog / "pid").write_text("999999999\n", encoding="utf-8")
    ready = tmp_path / "gate-ready"
    release = tmp_path / "gate-release"
    result_path = tmp_path / "lifecycle-result.json"
    origin = bare_origin()
    canonical = tmp_path / "canonical"
    subprocess.run(
        ["git", "clone", str(origin), str(canonical)],
        text=True,
        capture_output=True,
        check=True,
    )
    process = multiprocessing.Process(
        target=_run_lifecycle_with_blocking_gate,
        args=(
            str(origin),
            str(canonical),
            str(tmp_path / "worktrees"),
            str(command_base()),
            str(personas_dir),
            str(scratch),
            str(candidate),
            str(ready),
            str(release),
            str(result_path),
        ),
    )
    process.start()
    try:
        _wait_for_path(ready)
        during = subprocess.run(
            ["just", "sweep-scratch", "--root", str(scratch)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        assert candidate.exists()
        assert not dead_watchdog.exists()
        assert "third-party sweep skipped: lifecycle dispatch active" in during.stdout
    finally:
        release.write_text("release", encoding="utf-8")
        process.join(60)
        if process.is_alive():
            process.terminate()
            process.join(10)

    assert process.exitcode == 0
    lifecycle_result = json.loads(result_path.read_text(encoding="utf-8"))
    assert lifecycle_result["ok"] is True, lifecycle_result
    assert lifecycle_result["outcome"] == "merged"
    after = subprocess.run(
        ["just", "sweep-scratch", "--root", str(scratch)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert not candidate.exists()
    assert "removed 1 directories" in after.stdout


@pytest.mark.parametrize("identifiable", [True, False], ids=["identified", "unidentifiable"])
def test_concurrent_sweep_preserves_a_dispatch_past_its_worker_exit(
    tmp_path: Path, command_base: Callable[..., Path], onejudge_bin: str, identifiable: bool
) -> None:
    """A real dispatch keeps its scratch while a real sweep runs beside it.

    The recorded pid is the onejudge worker, which dies while the dispatcher is
    still reaping its process tree and parsing the report out of the directory.
    Sweeping continuously across the whole dispatch guarantees the sweeper meets
    that window rather than waiting for a lucky interleaving. A dispatcher that
    cannot read its own procfs identity records no token at all, so the lock has
    to hold the tree on its own.
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    dispatch_env = {**os.environ, "TMPDIR": str(scratch)}
    if not identifiable:
        blind = tmp_path / "empty-proc"
        blind.mkdir()
        dispatch_env["AI_ORCHESTRATOR_PROC_ROOT"] = str(blind)
    process = subprocess.Popen(
        [
            "just",
            "dispatch",
            "engineer",
            "complete-now: survive a concurrent scratch sweep",
            "--base",
            str(command_base()),
            "--project-dir",
            str(project_dir),
            "--onejudge-bin",
            onejudge_bin,
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        env=dispatch_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    swept_past_worker_exit: list[Path] = []
    try:
        while process.poll() is None:
            past_exit = [
                directory
                for directory in scratch.glob(WATCHDOG_PATTERN)
                if _worker_pid_is_gone(directory)
            ]
            # The unattended sweep this test races is the in-process
            # `sweep_scratch()` call every recorded round transition makes
            # (orchestrator/graph.py). A `just sweep-scratch` subprocess takes
            # longer to start than the post-exit window it must land inside, and
            # the recipe surface is covered by the recipe tests above.
            # llmlint: ignore[tests_mirror_real_usage] this is the round-transition caller
            result = sweep_scratch(scratch)
            assert result.removed == (), result.removed
            swept_past_worker_exit.extend(
                directory for directory in past_exit if directory in result.watchdog_retained
            )
            time.sleep(0.001)
    finally:
        stdout, stderr = process.communicate(timeout=120)

    assert process.returncode == 0, stderr
    assert swept_past_worker_exit, "the sweep never observed the post-worker-exit window"
    # The report is parsed out of the swept-past directory, so its survival is the
    # dispatch's own evidence that nothing removed the tree underneath it.
    report = json.loads(stdout)
    assert report["schema_version"] == 5
    assert report["stopped_early"] is False
    assert report["transcript"]["messages"]
    assert list(scratch.glob(WATCHDOG_PATTERN)) == []


def test_dry_run_recipe_and_recorded_round_transition_sweep(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    candidate = scratch / "oneharness-counter-old"
    candidate.mkdir()
    old = time.time() - 48 * 60 * 60
    os.utime(candidate, (old, old))
    inspected = subprocess.run(
        ["just", "sweep-scratch", "--root", str(scratch), "--dry-run"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert str(candidate) in inspected.stdout and candidate.exists()
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "scratch-round",
                "tasks": [
                    {
                        "id": "no-diff",
                        "task": "No dispatch is expected.",
                        "expects_no_diff": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "just",
            "run-plan",
            str(plan),
            "--run",
            "scratch-round",
            "--runs-dir",
            str(tmp_path / "runs"),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "TMPDIR": str(scratch)},
        text=True,
        capture_output=True,
        check=True,
    )
    assert not candidate.exists()


def test_lifecycle_cli_refuses_dispatch_when_scratch_capacity_is_insufficient(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "target"
    repo.mkdir()
    result = subprocess.run(
        [
            "just",
            "repo-task",
            str(repo),
            "engineer",
            "must not dispatch",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, MIN_FREE_BYTES_ENV: str(2**63 - 1)},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert str(Path(os.environ.get("TMPDIR", "/tmp")).resolve()) in result.stderr
    assert "bytes free" in result.stderr
    assert "just sweep-scratch" in result.stderr
    assert MIN_FREE_BYTES_ENV in result.stderr


def test_lifecycle_dispatch_honors_valid_scratch_capacity_override(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    command_base: Callable[..., Path],
    personas_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin = bare_origin()
    canonical = tmp_path / "canonical"
    subprocess.run(
        ["git", "clone", str(origin), str(canonical)],
        text=True,
        capture_output=True,
        check=True,
    )
    monkeypatch.setenv(MIN_FREE_BYTES_ENV, "0")
    workspace = Workspace(
        tmp_path / "worktrees",
        resolver=lambda _spec: canonical,
        workflow="local",
    )
    result = run_repo_task(
        str(origin),
        "complete-now write-unique-change: capacity override",
        "engineer",
        workspace=workspace,
        base_path=command_base(),
        persona_dir=personas_dir,
        verify_cmd=["true"],
        repo_type="single-owner",
    )

    assert result.ok is True
    assert result.outcome == "merged"


@pytest.mark.parametrize(
    ("value", "message"),
    [("invalid", "integer byte count"), ("-1", "at least zero")],
)
def test_lifecycle_recipe_rejects_invalid_capacity_configuration(
    tmp_path: Path, value: str, message: str
) -> None:
    repo = tmp_path / "target"
    repo.mkdir()
    result = subprocess.run(
        ["just", "repo-task", str(repo), "engineer", "must not dispatch"],
        cwd=REPO_ROOT,
        env={**os.environ, MIN_FREE_BYTES_ENV: value},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    assert MIN_FREE_BYTES_ENV in result.stderr and message in result.stderr


def test_sweep_recipe_rejects_nonfinite_or_negative_age(tmp_path: Path) -> None:
    for value in ("nan", "inf", "-1"):
        result = subprocess.run(
            ["just", "sweep-scratch", "--root", str(tmp_path), "--min-age-hours", value],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
        )
        assert result.returncode != 0
        assert "finite number at least zero" in result.stderr
    missing = subprocess.run(
        ["just", "sweep-scratch", "--root", str(tmp_path / "missing")],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert missing.returncode != 0
    assert "--root must be an existing directory" in missing.stderr


def test_sweep_recipe_custom_age_and_bounded_inspection(tmp_path: Path) -> None:
    eligible = tmp_path / "visual-custom-age"
    eligible.mkdir()
    two_hours_old = time.time() - 2 * 60 * 60
    os.utime(eligible, (two_hours_old, two_hours_old))
    subprocess.run(
        [
            "just",
            "sweep-scratch",
            "--root",
            str(tmp_path),
            "--min-age-hours",
            "1",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert not eligible.exists()

    for index in range(22):
        path = tmp_path / f"playwright-old-{index:02d}"
        path.mkdir()
        os.utime(path, (two_hours_old, two_hours_old))
    inspected = subprocess.run(
        [
            "just",
            "sweep-scratch",
            "--root",
            str(tmp_path),
            "--min-age-hours",
            "1",
            "--dry-run",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert len(inspected.stdout.splitlines()) == 1
    assert "2 more omitted" in inspected.stdout


def test_sweep_recipe_reports_actionable_deletion_failure(tmp_path: Path) -> None:
    candidate = tmp_path / "visual-permission-failure"
    candidate.mkdir()
    (candidate / "payload").write_text("data", encoding="utf-8")
    old = time.time() - 48 * 60 * 60
    os.utime(candidate, (old, old))
    candidate.chmod(0o555)
    try:
        result = subprocess.run(
            ["just", "sweep-scratch", "--root", str(tmp_path)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
        )
    finally:
        candidate.chmod(0o755)
    assert result.returncode != 0
    assert "check path permissions" in result.stderr
    assert "just sweep-scratch --dry-run" in result.stderr


def test_recorded_round_reports_actionable_sweep_failure(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    candidate = scratch / "screencomp-permission-failure"
    candidate.mkdir()
    (candidate / "payload").write_text("data", encoding="utf-8")
    old = time.time() - 48 * 60 * 60
    os.utime(candidate, (old, old))
    candidate.chmod(0o555)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "scratch-failure",
                "tasks": [
                    {
                        "id": "no-diff",
                        "task": "No dispatch is expected.",
                        "expects_no_diff": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            [
                "just",
                "run-plan",
                str(plan),
                "--run",
                "scratch-failure",
                "--runs-dir",
                str(tmp_path / "runs"),
            ],
            cwd=REPO_ROOT,
            env={**os.environ, "TMPDIR": str(scratch)},
            text=True,
            capture_output=True,
        )
    finally:
        candidate.chmod(0o755)
    assert result.returncode == 2
    assert "scratch sweep failed before claiming the round" in result.stderr
    assert "just sweep-scratch --dry-run" in result.stderr
    assert "Traceback" not in result.stderr
