"""Paid launch-path journey, selected explicitly rather than by the default gate."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from orchestrator import gitops

ROOT = Path(__file__).parents[2]

pytestmark = pytest.mark.skipif(
    os.environ.get("ORCHESTRATOR_REAL_HARNESS_SMOKE") != "1",
    reason="costs one real harness turn; the launch-path pre-push hook is its normal runner",
)


def test_launch_path_push_runs_real_smoke_then_gate(tmp_path: Path) -> None:
    """Drive the public hook with real just recipes, wrapper, harness, and history."""
    clone = gitops.clone(str(ROOT), tmp_path / "clone")
    before = gitops._git(["rev-parse", "HEAD"], cwd=clone).stdout.strip()
    probe = clone / "scripts" / "real-smoke-probe.sh"
    probe.write_text("# launch-path probe\n", encoding="utf-8")
    gitops._git(["add", str(probe)], cwd=clone)
    gitops._git(["commit", "-m", "test: real launch smoke"], cwd=clone)
    after = gitops._git(["rev-parse", "HEAD"], cwd=clone).stdout.strip()

    proc = subprocess.run(
        [str(clone / ".githooks/pre-push"), "origin", str(ROOT)],
        cwd=clone,
        input=f"refs/heads/main {after} refs/heads/main {before}\n",
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "launch path changed; running one real-harness smoke" in proc.stderr


def test_real_wrapper_surfaces_unavailable_configured_harnesses() -> None:
    proc = subprocess.run(
        ["just", "smoke"],
        cwd=ROOT,
        env={
            **os.environ,
            "ONEHARNESS_BIN_CODEX": "/does/not/exist/codex",
            "ONEHARNESS_BIN_CLAUDE_CODE": "/does/not/exist/claude",
        },
        text=True,
        capture_output=True,
    )
    assert proc.returncode != 0
    assert "real harness smoke failed" in proc.stderr
    assert "rerun 'just smoke'" in proc.stderr


def test_real_wrapper_surfaces_background_process_failure(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "oneharness"
    shim.write_text("#!/bin/sh\necho provider-crashed >&2\nexit 2\n", encoding="utf-8")
    shim.chmod(0o755)
    proc = subprocess.run(
        ["just", "smoke"],
        cwd=ROOT,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
    )
    assert proc.returncode != 0
    assert "provider-crashed" in proc.stderr


def test_ordinary_push_skips_real_smoke_and_runs_gate(tmp_path: Path) -> None:
    clone = gitops.clone(str(ROOT), tmp_path / "ordinary-clone")
    before = gitops._git(["rev-parse", "HEAD"], cwd=clone).stdout.strip()
    readme = clone / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    gitops._git(["add", str(readme)], cwd=clone)
    gitops._git(["commit", "-m", "docs: ordinary probe"], cwd=clone)
    after = gitops._git(["rev-parse", "HEAD"], cwd=clone).stdout.strip()

    proc = subprocess.run(
        [str(clone / ".githooks/pre-push"), "origin", str(ROOT)],
        cwd=clone,
        input=f"refs/heads/main {after} refs/heads/main {before}\n",
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "launch path changed" not in proc.stderr


def test_real_smoke_surfaces_timeout() -> None:
    proc = subprocess.run(
        ["just", "smoke"],
        cwd=ROOT,
        env={**os.environ, "ORCHESTRATOR_SMOKE_TIMEOUT_SECONDS": "1"},
        text=True,
        capture_output=True,
    )
    assert proc.returncode != 0
    assert "timed out" in proc.stderr or "real harness smoke failed" in proc.stderr


def test_real_smoke_rejects_missing_persisted_history() -> None:
    proc = subprocess.run(
        ["just", "smoke"],
        cwd=ROOT,
        env={**os.environ, "ONEHARNESS_HISTORY": "false"},
        text=True,
        capture_output=True,
    )
    assert proc.returncode != 0
    assert "expected one smoke history session, found 0" in proc.stderr


@pytest.mark.parametrize(
    ("corruption", "diagnostic"),
    [
        ("prompt", "did not receive the dispatched task"),
        ("telemetry", "history telemetry is incomplete"),
        ("harness", "does not identify the selected harness"),
    ],
)
def test_real_smoke_rejects_corrupt_persisted_contract(
    tmp_path: Path, corruption: str, diagnostic: str
) -> None:
    real_oneharness = shutil.which("oneharness")
    assert real_oneharness is not None
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "oneharness"
    shim.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
"$REAL_ONEHARNESS" "$@"
[[ ${1:-} == run ]] || exit 0
case "$SMOKE_CORRUPTION" in
  prompt)
    find "$ONEHARNESS_HISTORY_DIR" -type f \
      -exec sed -i 's/"prompt" *: *"[^"]*"/"prompt":"wrong"/g' {} +
    ;;
  telemetry)
    find "$ONEHARNESS_HISTORY_DIR" -type f \
      -exec sed -i 's/"finished_at" *: *"[^"]*"/"finished_at":null/g' {} +
    ;;
  harness)
    find "$ONEHARNESS_HISTORY_DIR" -type f \
      -exec sed -i 's/"harness" *: *"[^"]*"/"harness":""/g' {} +
    ;;
esac
""",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    proc = subprocess.run(
        ["just", "smoke"],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "REAL_ONEHARNESS": real_oneharness,
            "SMOKE_CORRUPTION": corruption,
        },
        text=True,
        capture_output=True,
    )
    assert proc.returncode != 0
    assert diagnostic in proc.stderr
