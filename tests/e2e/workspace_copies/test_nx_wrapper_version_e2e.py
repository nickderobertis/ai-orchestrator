"""End-to-end proof that the Nx wrapper runs the workspace's pinned Nx.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] It is not expensive: it
runs `nx --version` once in this checkout, installing and fetching nothing, 0.35 s measured
(`pytest --durations`). The task that retired the toolchain-isolation project placed it
here, beside the other `scripts/nx.sh` journeys this project owns, once it no longer needed
a copy of its own.

llmlint: ignore-file[shell_test_tiers_stay_split] Same placement, same reason: a project of
its own would add a project to keep under a second of one installed tool off an unrelated
edit.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

from orchestrator.root import REPO_ROOT

LOCAL_VERSION = re.compile(r"^- Local: v(?P<version>\S+)$", re.MULTILINE)


def test_nx_wrapper_runs_the_version_package_json_pins() -> None:
    """Read Nx's version through the real logging wrapper, in this checkout."""
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
