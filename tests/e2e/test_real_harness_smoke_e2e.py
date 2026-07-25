"""Paid launch-path journey, selected explicitly rather than by the default gate."""

from __future__ import annotations

import os
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
