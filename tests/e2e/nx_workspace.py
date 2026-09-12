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
#: writing one tree. The lock that serialises them is the tree's rather than the
#: copy's — `scripts/workspace-install.sh` takes it beside the checkout `node_modules`
#: resolves into — because a lock kept per copy let each racer take a different one,
#: which failed as `bun install ... Failed to link <pkg>: EEXIST` even over a tree
#: already in agreement with the lockfile, and did so across two Nx targets' pytest
#: processes where no xdist group reaches. What the group still serialises is an Nx
#: cache miss where the recorded verdict was due, and the second resource below;
#: applying both marks from one tuple is what stops a journey taking the install
#: without the group.
#:
#: `node_modules` is one of two things this group serialises, and the second is why
#: the group is named for the toolchain rather than for the install. The other is this
#: checkout's project environment, `<root>/.venv`: `uv` takes an **exclusive** lock on
#: it, and every `just` recipe in this suite reaches its tool through `uv run`, which
#: waits on that lock for as long as a holder keeps it.
#:
#: **So the readers join it too, and one name is the whole mechanism.** `--dist
#: loadgroup` co-locates the tests that share a group *name* and says nothing about two
#: different names, which run on two workers at once — so a writer in one group and a
#: reader in another are exactly as concurrent as if neither declared anything. Both
#: halves therefore spell this one constant: the journeys that re-provision this
#: checkout, and the deadline-based channel journeys whose every step is a `just`
#: recipe waiting on the lock those journeys take. All of them run in the code-keyed
#: `orchestrator:test` tier, where `-n 4 --dist loadgroup` decides who runs beside whom.
#: AGENTS.md's four-xdist-workers invariant carries the measurements, including why host
#: CPU is not what this constraint is about.
#:
#: Provision rather than skip: `workspace_install` in `tests/conftest.py` installs the
#: workspace this points at, so a bare `pytest` in a fresh worktree runs these journeys
#: instead of withdrawing them.
SHARED_TOOLCHAIN_GROUP = "shared-checkout-toolchain"
WORKSPACE_INSTALL_MARKS = (
    pytest.mark.usefixtures("workspace_install"),
    pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP),
)


def shares_workspace_install(target: Marked) -> Marked:
    """Take this checkout's install *and* join the group that serialises access to it.

    Named for both, because both happen: a caller reading only "requires" would not
    expect its test's scheduling to change, and that scheduling is the whole point.
    A module declaring them for every test spreads `WORKSPACE_INSTALL_MARKS` into its
    own `pytestmark` instead; either way the pair comes from the one tuple.

    "Access" covers reading through this checkout's provisioned toolchain as well as
    reinstalling it, for the reason above: a journey that runs this checkout's own
    `scripts/session-setup.sh` holds the exclusive lock every other `uv run` here waits
    on, and rewrites the `.venv/bin` they resolve their tools from.
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
