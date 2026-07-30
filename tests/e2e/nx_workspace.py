"""A throwaway copy of this repository that real Nx can hash, cache, and replay.

Every journey that asks "would this tree replay a recorded verdict?" needs the
real `nx.json`, the real `project.json` target declarations, and the real
`scripts/nx.sh`, driven against a checkout it is free to mutate. Copying exactly
what git would commit is what makes the copy hash the same way the original does:
Nx skips ignored state, so bringing `.venv`, `node_modules`, or `.nx` along would
add files the original never hashed.

`node_modules` is the one exception. It is ignored state that Nx itself needs, it
is far too large to copy, and it is a symlink out to this checkout's own install
rather than a copy for exactly that reason.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from orchestrator import REPO_ROOT

NODE_MODULES = REPO_ROOT / "node_modules"

#: Nx runs from `node_modules/.bin`, and a bare `pytest` run in a fresh worktree
#: has no reason to have installed it. Skipping with the remediation beats failing
#: on a missing toolchain: `just check` installs the workspace before it runs the
#: suite, so nothing is skipped in the gate that enforces these journeys.
requires_workspace_install = pytest.mark.skipif(
    not (NODE_MODULES / ".bin" / "nx").is_file(),
    reason="the workspace Nx install drives these journeys; "
    "run 'bun install --frozen-lockfile' (or 'just check') first",
)


def copy_checkout(destination: Path) -> None:
    """Copy exactly the files Nx would hash: everything git would commit from here."""
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    for relative in filter(None, listing.split("\0")):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, target, follow_symlinks=False)
    (destination / "node_modules").symlink_to(NODE_MODULES, target_is_directory=True)
