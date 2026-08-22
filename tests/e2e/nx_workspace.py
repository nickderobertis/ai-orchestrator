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
from typing import TypeVar

import pytest

from orchestrator.root import REPO_ROOT

NODE_MODULES = REPO_ROOT / "node_modules"

#: A test function or class, returned as it was given: applying a mark mutates the
#: target and hands it back, so the decorator below narrows nothing.
Marked = TypeVar("Marked")

#: Taking this checkout's own install and joining the group that serialises it are
#: one decision, so they are one name. Sharing is the point of the symlink below —
#: `copy_checkout` points every copy's `node_modules` at this install rather than
#: duplicating it — and `scripts/nx.sh` heals through `bun install --frozen-lockfile`
#: before every Nx invocation, so two of these journeys at once are two installs
#: writing one tree. The lock that would serialise them is per copy, so each racer
#: takes a different one; under `-n 4` that fails as `bun install ... Failed to link
#: <pkg>: EEXIST`, or as an Nx cache miss where the recorded verdict was due.
#: `--dist loadgroup` is the fix, and applying both marks from one tuple is what stops
#: a journey taking the install without the group.
#:
#: Provision rather than skip: `workspace_install` in `tests/conftest.py` installs the
#: workspace this points at, so a bare `pytest` in a fresh worktree runs these journeys
#: instead of withdrawing them.
WORKSPACE_INSTALL_GROUP = "shared-workspace-install"
WORKSPACE_INSTALL_MARKS = (
    pytest.mark.usefixtures("workspace_install"),
    pytest.mark.xdist_group(WORKSPACE_INSTALL_GROUP),
)


def shares_workspace_install(target: Marked) -> Marked:
    """Take this checkout's install *and* join the group that serialises access to it.

    Named for both, because both happen: a caller reading only "requires" would not
    expect its test's scheduling to change, and that scheduling is the whole point.
    A module declaring them for every test spreads `WORKSPACE_INSTALL_MARKS` into its
    own `pytestmark` instead; either way the pair comes from the one tuple.
    """
    for mark in reversed(WORKSPACE_INSTALL_MARKS):
        target = mark(target)
    return target


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
