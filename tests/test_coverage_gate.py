"""Enforcement contract for the repository's coverage floor.

The floor silently degraded to advisory once: pytest-cov decides failure on
``round(total, precision)``, so at coverage's default precision of 0 a 94.93%
total rounded up to 95, pytest exited 0, and only the terminal summary said
"FAIL ... not reached". Every caller — the Nx ``test`` target, ``just check``,
``just gate``, the pre-push hook — read that zero as a pass.

These tests drive the real pytest/pytest-cov boundary over a generated package
whose total lands inside that once-forgiven band, configured with the floor and
precision this repository actually declares. They fail if either setting drifts
back to a combination that cannot fail the build.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from orchestrator import REPO_ROOT

# Each generated function contributes exactly two statements (its ``def`` line,
# executed at import, and its ``return`` line, executed only when called) and no
# branches, so the fixture's coverage total is exact arithmetic.
FUNCTIONS = 200
STATEMENTS = FUNCTIONS * 2


@pytest.fixture(scope="module")
def report_config() -> dict[str, object]:
    """The declared ``[tool.coverage.report]`` table — the floor's single source."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return pyproject["tool"]["coverage"]["report"]


@pytest.fixture(scope="module")
def floor(report_config: dict[str, object]) -> float:
    declared = report_config["fail_under"]
    assert isinstance(declared, int | float), f"fail_under must be numeric, got {declared!r}"
    return float(declared)


@pytest.fixture(scope="module")
def precision(report_config: dict[str, object]) -> int:
    declared = report_config["precision"]
    assert isinstance(declared, int), f"precision must be an integer, got {declared!r}"
    return declared


def _uncalled_for_band(floor: float) -> int:
    """Uncalled functions putting the total just under ``floor`` but rounding up to it.

    This is the band the regression forgave: strictly below the floor, yet
    indistinguishable from it once rounded at precision 0.
    """
    uncalled = math.floor(STATEMENTS * (100.0 - floor) / 100.0) + 1
    total = (STATEMENTS - uncalled) / STATEMENTS * 100.0
    assert total < floor, f"fixture total {total} is not below the floor {floor}"
    assert round(total, 0) >= floor, (
        f"fixture total {total} does not sit in the band precision 0 rounds up to {floor}"
    )
    return uncalled


def _write_project(root: Path, *, uncalled: int, fail_under: float, precision: int) -> float:
    """Generate a package plus its test whose coverage total is exactly known."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "sample.py").write_text(
        "".join(f"def f{index}() -> int:\n    return {index}\n\n\n" for index in range(FUNCTIONS)),
        encoding="utf-8",
    )
    called = FUNCTIONS - uncalled
    (root / "test_sample.py").write_text(
        "import sample\n\n\ndef test_sample() -> None:\n"
        + "".join(f"    assert sample.f{index}() == {index}\n" for index in range(called)),
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n"
        'testpaths = ["."]\n\n'
        "[tool.coverage.run]\n"
        'source = ["sample"]\n'
        "branch = true\n\n"
        "[tool.coverage.report]\n"
        f"fail_under = {fail_under}\n"
        f"precision = {precision}\n",
        encoding="utf-8",
    )
    return (STATEMENTS - uncalled) / STATEMENTS * 100.0


def _run_pytest(root: Path) -> subprocess.CompletedProcess[str]:
    """Run pytest on the generated project the way the Nx ``test`` target runs it."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("COV_CORE")}
    env.pop("FORCE_COLOR", None)
    env["NO_COLOR"] = "1"
    env["PYTHONPATH"] = str(root)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--cov=sample", "--cov-report=term", "-q"],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
    )


def test_below_floor_run_exits_nonzero_inside_the_rounding_band(
    tmp_path: Path, floor: float, precision: int
) -> None:
    """A total just under the floor must fail the command, not merely print FAIL."""
    total = _write_project(
        tmp_path / "below",
        uncalled=_uncalled_for_band(floor),
        fail_under=floor,
        precision=precision,
    )

    result = _run_pytest(tmp_path / "below")

    assert f"Total coverage: {total:.2f}%" in result.stdout, result.stdout
    assert "FAIL Required test coverage" in result.stdout, result.stdout
    assert result.returncode != 0, (
        "the configured floor did not fail the run; coverage enforcement is advisory again:\n"
        f"{result.stdout}"
    )


def test_at_or_above_floor_run_exits_zero(tmp_path: Path, floor: float, precision: int) -> None:
    """The same fixture passes once the floor sits at its total — the failure is the floor's."""
    uncalled = _uncalled_for_band(floor)
    total = _write_project(
        tmp_path / "above",
        uncalled=uncalled,
        fail_under=(STATEMENTS - uncalled) / STATEMENTS * 100.0,
        precision=precision,
    )

    result = _run_pytest(tmp_path / "above")

    assert result.returncode == 0, result.stdout
    assert f"Total coverage: {total:.2f}%" in result.stdout, result.stdout
    assert "FAIL" not in result.stdout, result.stdout


def test_nx_test_target_measures_the_orchestrator_package(floor: float) -> None:
    """The enforced command must measure coverage and must not shadow the declared floor.

    Without ``--cov`` pytest-cov never registers and ``fail_under`` is inert; a
    ``--cov-fail-under`` on the command line would silently outrank pyproject.toml.
    """
    project = json.loads((REPO_ROOT / "orchestrator" / "project.json").read_text(encoding="utf-8"))
    command = project["targets"]["test"]["command"]

    assert "--cov=orchestrator" in command, command
    assert "--cov-fail-under" not in command, (
        f"the floor's single source is pyproject.toml (fail_under = {floor:g}); "
        f"remove the override from the Nx test target: {command}"
    )
