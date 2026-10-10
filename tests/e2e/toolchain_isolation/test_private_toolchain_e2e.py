"""The copies a toolchain writer works in share no toolchain directory with this checkout.

Driven with the installers themselves, because the property is what a real install does
in the copy: an in-sync `bun install` in a `copy_checkout` copy re-links that copy's
`.bin` — the write that, through the symlinked `node_modules` copies used to carry,
re-linked this checkout's under every other worker — and leaves this checkout's shim the
inode it was, and `isolated_python_root`'s `.venv` is an interpreter prefix of its own.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from nx_workspace import (
    NODE_MODULES,
    OFFLINE_INSTALLS,
    copy_checkout,
    isolated_python_root,
    rewrites_workspace_toolchain,
    shares_workspace_install,
)


@pytest.fixture(scope="module")
def copy(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A `copy_checkout` copy, made once: copying is a read of every tracked file."""
    destination = tmp_path_factory.mktemp("private-toolchain") / "copy"
    copy_checkout(destination)
    return destination


@pytest.fixture(scope="module")
def python_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An `isolated_python_root` copy, made once, for the same reason."""
    return isolated_python_root(tmp_path_factory.mktemp("private-python") / "checkout")


@shares_workspace_install
@rewrites_workspace_toolchain
def test_an_install_in_a_copy_rewrites_the_copys_node_modules_and_never_this_checkouts(
    copy: Path,
) -> None:
    shim = NODE_MODULES / ".bin" / "nx"
    before = shim.lstat()

    assert not (copy / "node_modules").is_symlink(), "copy_checkout symlinked node_modules"
    copied = copy / "node_modules" / ".bin" / "nx"
    assert (copy / "node_modules" / ".bin").stat().st_ino != (NODE_MODULES / ".bin").stat().st_ino
    installed = subprocess.run(
        ["bun", "install", "--frozen-lockfile"],
        cwd=copy,
        env={**os.environ, **OFFLINE_INSTALLS},
        text=True,
        capture_output=True,
        check=False,
    )

    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert copied.lstat().st_ino != before.st_ino, "the install re-linked nothing in the copy"
    after = shim.lstat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns), (
        "an install in the copy re-linked this checkout's own `node_modules/.bin/nx`"
    )


@shares_workspace_install
@rewrites_workspace_toolchain
def test_an_isolated_python_root_runs_an_interpreter_prefix_of_its_own(python_root: Path) -> None:
    root = python_root
    prefix = subprocess.run(
        [str(root / ".venv" / "bin" / "python"), "-c", "import sys; print(sys.prefix)"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()

    assert Path(prefix) == root / ".venv", prefix
    assert not (root / ".venv").is_symlink()
    assert not (root / "node_modules").is_symlink()
