"""A throwaway copy of this repository that real Nx can hash, cache, and replay.

Every journey that asks "would this tree replay a recorded verdict?" needs the
real `nx.json`, the real `project.json` target declarations, and the real
`scripts/nx.sh`, driven against a checkout it is free to mutate. Copying exactly
what git would commit is what makes the copy hash the same way the original does:
Nx skips ignored state, so bringing `.venv`, `node_modules`, or `.nx` along would
add files the original never hashed.

`node_modules` is the one exception. It is ignored state that Nx itself needs, it
is far too large to copy, and it is a symlink out to this checkout's own install
rather than a copy for exactly that reason — which is why every journey that
copies a checkout has to have provisioned this one first.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from orchestrator import REPO_ROOT

NODE_MODULES = REPO_ROOT / "node_modules"

#: Provision rather than skip: `workspace_install` in `tests/conftest.py` installs
#: the workspace this points at, so a bare `pytest` in a fresh worktree runs these
#: journeys instead of withdrawing them.
requires_workspace_install = pytest.mark.usefixtures("workspace_install")


def copy_working_tree(destination: Path) -> None:
    """Copy exactly the files Nx would hash: everything git would commit from here.

    Separate from `copy_checkout` because a *fresh worktree* journey needs these
    files without the install: `git worktree add` carries committed content only,
    so the change under test reaches the new tree through this, and what the tree
    must not arrive with is the very `node_modules` it has to provision itself.
    The destination is expected to be empty of tracked content — a worktree added
    with `--no-checkout` — so that what lands there is this working tree exactly,
    with nothing left over from HEAD.
    """
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


def copy_checkout(destination: Path) -> None:
    """A copy of this checkout wired to this checkout's own workspace install."""
    copy_working_tree(destination)
    (destination / "node_modules").symlink_to(NODE_MODULES, target_is_directory=True)
