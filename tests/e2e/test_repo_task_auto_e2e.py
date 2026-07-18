"""Command-surface coverage for the automatic repo-task wrapper."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from orchestrator import REPO_ROOT


def test_repo_task_auto_prepends_uv_tool_bin_to_dispatch_path(tmp_path: Path) -> None:
    home = tmp_path / "home"
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    uv = shutil.which("uv")
    just = shutil.which("just")
    assert uv is not None
    assert just is not None
    (bin_dir / "uv").symlink_to(uv)

    proc = subprocess.run(
        [just, "repo-task-auto", "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        env={**os.environ, "HOME": str(home), "PATH": "/usr/bin:/bin"},
    )

    assert proc.returncode == 0, proc.stderr
    assert "orchestrator-repo-task" in proc.stdout
