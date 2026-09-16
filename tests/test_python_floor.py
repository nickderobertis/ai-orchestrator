"""Every tool keyed on this repository's Python floor reads the floor it declares.

`[project] requires-python` is the floor's one source. Ruff's `target-version`, mypy's
`python_version` and the lockfile's own `requires-python` each restate it in a form
their tool reads, and none of them fails when it drifts: a lower lint target lets
syntax the floor would allow go unflagged, a lower type-check target checks against an
interpreter no install can run, and a lockfile resolved for a different floor carries
wheels for interpreters the project refuses.
"""

from __future__ import annotations

import re
import tomllib
from typing import NamedTuple

import pytest

from orchestrator.root import REPO_ROOT

PYPROJECT = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
LOCKFILE = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))


class PythonVersion(NamedTuple):
    """A `major.minor` interpreter version."""

    major: int
    minor: int


@pytest.fixture(scope="module")
def floor() -> PythonVersion:
    """The `major.minor` lower bound `requires-python` declares, and nothing else."""
    declared = PYPROJECT["project"]["requires-python"]
    bound = re.fullmatch(r">=(\d+)\.(\d+)", declared)
    assert bound, f"requires-python must be a bare `>=major.minor` bound, got {declared!r}"
    return PythonVersion(int(bound.group(1)), int(bound.group(2)))


def test_ruff_targets_the_declared_floor(floor: PythonVersion) -> None:
    target = PYPROJECT["tool"]["ruff"]["target-version"]
    assert target == f"py{floor.major}{floor.minor}"


def test_mypy_targets_the_declared_floor(floor: PythonVersion) -> None:
    target = PYPROJECT["tool"]["mypy"]["python_version"]
    assert target == f"{floor.major}.{floor.minor}"


def test_the_lockfile_was_resolved_for_the_declared_floor() -> None:
    declared = PYPROJECT["project"]["requires-python"]
    assert LOCKFILE["requires-python"] == declared
