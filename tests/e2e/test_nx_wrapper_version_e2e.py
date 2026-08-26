"""End-to-end proof that the Nx wrapper runs the workspace's pinned Nx."""

from __future__ import annotations

import json
import os
import re
import subprocess

from nx_workspace import shares_workspace_install

from orchestrator.root import REPO_ROOT

LOCAL_VERSION = re.compile(r"^- Local: v(?P<version>\S+)$", re.MULTILINE)


@shares_workspace_install
def test_nx_wrapper_runs_the_version_package_json_pins() -> None:
    """Read Nx's version through the real self-healing, logging wrapper."""
    pinned = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))[
        "devDependencies"
    ]["nx"]
    result = subprocess.run(
        ["./scripts/nx.sh", "--version"],
        cwd=REPO_ROOT,
        env={**os.environ, "AI_ORCHESTRATOR_NX_SHOW_OUTPUT": "1"},
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    match = LOCAL_VERSION.search(result.stdout)
    assert match is not None, f"Nx wrapper did not report its local version:\n{result.stdout}"
    actual = match.group("version")
    assert actual == pinned, f"Nx wrapper ran version {actual}, but package.json pins {pinned}"
