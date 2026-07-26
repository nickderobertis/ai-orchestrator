"""Real command-surface journeys for host scratch protection."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Callable

import pytest

from orchestrator import REPO_ROOT
from orchestrator.lifecycle import run_repo_task
from orchestrator.scratch import MIN_FREE_BYTES_ENV
from orchestrator.workspace import Workspace


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
    assert not old_third_party.exists()
    assert live.exists()
    assert malformed.exists()
    assert recent.exists()
    assert "removed 2 directories" in result.stdout
    assert "reclaimed 46 bytes" in result.stdout


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
