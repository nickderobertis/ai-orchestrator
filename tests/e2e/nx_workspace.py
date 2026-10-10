"""A throwaway copy of this repository that real Nx can hash, cache, and replay.

Every journey that asks "would this tree replay a recorded verdict?" needs the
real `nx.json`, the real `project.json` target declarations, and the real
`scripts/nx.sh`, driven against a checkout it is free to mutate. Copying exactly
what git would commit is what makes the copy hash the same way the original does:
Nx skips ignored state, so bringing `.venv`, `node_modules`, or `.nx` along would
add files the original never hashed.

`node_modules` is the one exception. It is ignored state that Nx itself needs, and
`copy_checkout` gives every copy a **private** one hardlinked from this checkout's
install — never a symlink to it — which is why every journey that copies a checkout
has to have provisioned this one first.

This module is also the one statement of the suite's toolchain contract, which every
test module takes from here:

- A **writer** is a test that runs anything installing into or rewriting a `.venv`,
  `.venv/bin` or `node_modules`: `scripts/nx.sh` (its heal runs both installs), a `just`
  recipe reaching it, `scripts/workspace-install.sh`, `scripts/python-install.sh`,
  `scripts/session-setup.sh`, `uv sync` or `bun install` — the last even when the tree is
  in sync, because a no-change Bun install still re-links every package carrying a `bin`.
  A writer declares `rewrites_workspace_toolchain` (or spreads `TOOLCHAIN_WRITER_MARKS`)
  and runs those writes only inside a copy whose toolchain is its own: `copy_checkout`
  for `node_modules`, `isolated_python_root` when it rewrites `.venv` too. It may *read*
  this checkout's `.venv` through `UV_PROJECT_ENVIRONMENT` only beside `UV_NO_SYNC`, which
  is what keeps the copy's `scripts/python-install.sh` and `uv run` from syncing it.
- A **reader** only runs recipes and tools through a toolchain somebody else provisioned,
  and declares `shares_workspace_install` (or spreads `WORKSPACE_INSTALL_MARKS`).

Neither declaration is a scheduling constraint, and no xdist group protects a
toolchain. With no writer touching this checkout's toolchain, the only thing a reader
meets is `uv run`'s environment lock on `<root>/.venv`, which `uv` holds only while it
checks or syncs the environment and releases before it starts the child — `--no-sync`,
`--frozen` and `--locked` alike — and nothing syncs this checkout's. So readers keep plain
`uv run`. `tests/test_nx_cache_scope.py` holds the contract on every test module, and
`tests/e2e/toolchain_isolation/test_toolchain_isolation_e2e.py` drives a writer beside a
reader to prove it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from orchestrator.root import REPO_ROOT

NODE_MODULES = REPO_ROOT / "node_modules"

#: The marker a toolchain writer carries, registered in `pyproject.toml`. A name rather
#: than a group: it records what a test does so the drift gates can hold it to running
#: in a copy, and changes nothing about where the scheduler puts it.
TOOLCHAIN_WRITER_MARKER = "toolchain_writer"

#: The environment that keeps a copy's installers off every package index: uv answers
#: from its cache alone, and Bun's registry is an address nothing listens on, so an
#: install that would have to download fails rather than reaching the network. A copy's
#: lockfiles are this checkout's, whose own installs already filled both caches.
OFFLINE_INSTALLS = {"UV_OFFLINE": "1", "BUN_CONFIG_REGISTRY": "http://127.0.0.1:9/"}

#: A reader: this checkout's provisioned toolchain, and nothing about scheduling.
#: `workspace_install` in `tests/conftest.py` provisions what is absent, so a bare
#: `pytest` in a fresh worktree runs these journeys instead of withdrawing them, and it
#: runs no installer over a toolchain already provisioned.
WORKSPACE_INSTALL_MARKS = (pytest.mark.usefixtures("workspace_install"),)

#: A writer: the registered marker alone, never an xdist group.
TOOLCHAIN_WRITER_MARKS = (getattr(pytest.mark, TOOLCHAIN_WRITER_MARKER),)


# `Marked` is a test function or class, returned as it was given: applying a mark
# mutates the target and hands it back, so the decorator narrows nothing.
def shares_workspace_install[Marked](target: Marked) -> Marked:
    """Declare a reader of this checkout's provisioned toolchain.

    A module declaring it for every test spreads `WORKSPACE_INSTALL_MARKS` into its own
    `pytestmark` instead; either way the marks come from the one tuple.
    """
    for mark in reversed(WORKSPACE_INSTALL_MARKS):
        target = mark(target)
    return target


def rewrites_workspace_toolchain[Marked](target: Marked) -> Marked:
    """Declare a writer, which runs every install it makes inside a copy of its own.

    A writer whose copy is hardlinked from this checkout's install takes that install
    as well, by declaring `shares_workspace_install` beside this.
    """
    for mark in reversed(TOOLCHAIN_WRITER_MARKS):
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


def private_node_modules(destination: Path) -> None:
    """Give `destination` a `node_modules` of its own, hardlinked from this checkout's.

    `cp -al` shares every package file's inode and no data blocks, but every directory —
    `.bin` included — is the copy's own, so the in-sync Bun install `scripts/nx.sh` runs
    there re-links the copy's `.bin` and never this checkout's. A symlink instead is one
    tree under two names, and a copy's install then writes this checkout's.
    """
    subprocess.run(
        ["cp", "-al", str(NODE_MODULES), str(destination / "node_modules")],
        check=True,
        capture_output=True,
    )


def copy_checkout(destination: Path) -> None:
    """A copy of this checkout with a private, hardlinked copy of its workspace install."""
    copy_working_tree(destination)
    private_node_modules(destination)


def isolated_python_root(destination: Path) -> Path:
    """A copy of this checkout whose `node_modules` and `.venv` are both its own.

    For a writer that re-provisions the Python environment — `scripts/session-setup.sh`,
    or `scripts/nx.sh` without `UV_NO_SYNC` — as well as the Bun one. The copy is a
    repository (`git init`, no history), because those scripts resolve their checkout
    through git, and its `.venv` is synced from the copied `uv.lock` with `uv sync
    --locked`, offline, so nothing they install reaches this checkout's and building the
    copy reaches no index. Any environment the caller had pointed uv at is dropped from
    that sync, or the copy would be synced over it instead.
    """
    copy_checkout(destination)
    subprocess.run(["git", "init", "-q"], cwd=destination, check=True, capture_output=True)
    provided = ("UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV", "UV_NO_SYNC")
    synced = subprocess.run(
        ["uv", "sync", "--locked", "--project", str(destination)],
        cwd=destination,
        env={
            **{name: value for name, value in os.environ.items() if name not in provided},
            **OFFLINE_INSTALLS,
        },
        check=False,
        text=True,
        capture_output=True,
    )
    assert synced.returncode == 0, synced.stdout + synced.stderr
    return destination


def answering_this_checkouts_origin(destination: Path) -> None:
    """Give a copied tree the one thing of `.git` a review reads: this checkout's `origin`.

    `copy_working_tree` copies what git would commit and no `.git`, so a copy answers
    `git remote get-url origin` with nothing — and `orchestrator/plan_review.py` reads
    this host's own repository off that remote to decide which nodes of a plan are its
    own. A copy that could not answer would refuse every review and check run in it for
    want of the review configuration rather than for anything about the plan, and a
    copy answering a *different* origin would be a different bar. So the copy is made a
    repository with no history at all and exactly this checkout's remote: what it then
    answers is what this checkout answers, and nothing else of git is there to read.
    """
    url = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    subprocess.run(["git", "init", "-q"], cwd=destination, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", url], cwd=destination, check=True, capture_output=True
    )
