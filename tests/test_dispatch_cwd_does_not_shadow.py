"""A worktree of this repository is a dispatch cwd, and may not capture its imports.

An orchestrator dispatches a subtask by running the agent with its cwd set to the
worktree it prepared, and it launches its own helper modules as ``python -m
<module>`` in that process. ``-m`` puts the cwd at the front of ``sys.path``, so a
regular package at the root of the worktree answers every ``<name>.*`` import the
dispatcher makes for itself.

That is not hypothetical here. This repository dispatches into itself, and its own
package is named ``orchestrator`` — the same name the orchestrator that dispatches
it uses for its modules. While the two trees held the same code the capture was
invisible; once this repository became a configuration layer over the published
CLIs and kept only two modules under that name, the captured import stopped
resolving and every dispatch into a worktree of this tree died before its first
turn, with an empty report the SDK could only report as invalid JSON.

So these drive the resolution itself, with a real interpreter, from this checkout's
own root: an ``orchestrator`` package installed elsewhere on the path must still be
the one ``python -m`` finds.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator.root import REPO_ROOT

#: What the installed-elsewhere package prints. A dispatcher's helper module is not
#: importable from this tree at all, so the probe stands in for one: if this tree
#: captures the name, the module is missing and the interpreter says so instead.
MARKER = "resolved-outside-the-worktree"


def _install_package(directory: Path) -> Path:
    """Write the package a dispatcher would have installed in its own environment."""
    package = directory / "orchestrator"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "watchdog_probe.py").write_text(f"print({MARKER!r})\n", encoding="utf-8")
    return directory


def _resolve_from(cwd: Path, path_entry: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "orchestrator.watchdog_probe"],
        cwd=str(cwd),
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(path_entry), "HOME": str(cwd)},
        text=True,
        capture_output=True,
        timeout=60,
    )


def test_a_dispatch_run_from_this_checkout_imports_the_dispatchers_package(
    tmp_path: Path,
) -> None:
    """The real repository root, driven the way a dispatch's own launcher drives it."""
    installed = _install_package(tmp_path / "installed")

    result = _resolve_from(REPO_ROOT, installed)

    assert result.returncode == 0, (
        "python -m from this checkout did not reach the orchestrator package installed "
        f"elsewhere on the path — a dispatch into a worktree of this tree cannot start: "
        f"{result.stderr}"
    )
    assert result.stdout.strip() == MARKER


def test_a_root_package_with_an_init_captures_that_import(tmp_path: Path) -> None:
    """The negative control: what this repository must not go back to being."""
    installed = _install_package(tmp_path / "installed")
    shadowing = tmp_path / "shadowing"
    (shadowing / "orchestrator").mkdir(parents=True)
    (shadowing / "orchestrator" / "__init__.py").write_text("", encoding="utf-8")

    result = _resolve_from(shadowing, installed)

    assert result.returncode != 0
    assert "No module named orchestrator.watchdog_probe" in result.stderr


@pytest.mark.parametrize("name", ["__init__.py", "__init__.pyi"])
def test_this_package_declares_no_init(name: str) -> None:
    """Said directly as well, so the reason a reviewer sees is the reason it is gone."""
    assert not (REPO_ROOT / "orchestrator" / name).exists(), (
        f"orchestrator/{name} makes this tree a regular package again, which captures "
        "the orchestrator imports a dispatch running here makes for itself"
    )
