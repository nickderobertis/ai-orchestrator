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
    # `just` may itself be a launcher that needs its own interpreter on PATH, so
    # keep its directory reachable. It carries no uv, so the recipe still has to
    # derive the tool bin from HOME — the premise under test.
    launcher_dir = str(Path(just).parent)
    assert shutil.which("uv", path=launcher_dir) is None

    proc = subprocess.run(
        [just, "repo-task-auto", "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        env={**os.environ, "HOME": str(home), "PATH": f"{launcher_dir}:/usr/bin:/bin"},
    )

    assert proc.returncode == 0, proc.stderr
    assert "orchestrator-repo-task" in proc.stdout
