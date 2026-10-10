"""End-to-end proof that the Nx wrapper runs the workspace's pinned Nx."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from nx_workspace import (
    OFFLINE_INSTALLS,
    isolated_python_root,
    rewrites_workspace_toolchain,
    shares_workspace_install,
)

from orchestrator.root import REPO_ROOT

LOCAL_VERSION = re.compile(r"^- Local: v(?P<version>\S+)$", re.MULTILINE)


@pytest.fixture(scope="module")
def checkout(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A copy of this checkout whose install and `.venv` the wrapper's heal may rewrite.

    The wrapper heals both installs of the checkout it runs in before Nx starts, so it
    runs in a copy of its own rather than in this checkout, whose toolchain every other
    worker reads.
    """
    return isolated_python_root(tmp_path_factory.mktemp("nx-wrapper") / "checkout")


@shares_workspace_install
@rewrites_workspace_toolchain
def test_nx_wrapper_runs_the_version_package_json_pins(checkout: Path) -> None:
    """Read Nx's version through the real self-healing, logging wrapper."""
    pinned = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))[
        "devDependencies"
    ]["nx"]
    result = subprocess.run(
        ["./scripts/nx.sh", "--version"],
        cwd=checkout,
        env={**os.environ, **OFFLINE_INSTALLS, "AI_ORCHESTRATOR_NX_SHOW_OUTPUT": "1"},
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    match = LOCAL_VERSION.search(result.stdout)
    assert match is not None, f"Nx wrapper did not report its local version:\n{result.stdout}"
    actual = match.group("version")
    assert actual == pinned, f"Nx wrapper ran version {actual}, but package.json pins {pinned}"
