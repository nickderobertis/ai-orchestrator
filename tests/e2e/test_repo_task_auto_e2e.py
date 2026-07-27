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
    # The wrapper must find `uv` only through the tool bin it prepends, so the PATH
    # it inherits carries `just` and the interpreter a wrapper-script install of it
    # needs (a node-shim `just` is otherwise unrunnable here) — and nothing else.
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "just").symlink_to(just)
    node = shutil.which("node")
    if node is not None:
        (tools / "node").symlink_to(node)
    assert shutil.which("uv", path=f"{tools}:/usr/bin:/bin") is None

    proc = subprocess.run(
        [str(tools / "just"), "repo-task-auto", "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        env={**os.environ, "HOME": str(home), "PATH": f"{tools}:/usr/bin:/bin"},
    )

    assert proc.returncode == 0, proc.stderr
    assert "orchestrator-repo-task" in proc.stdout
