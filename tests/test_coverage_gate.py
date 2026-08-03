"""Enforcement contract for the repository's coverage floor.

The floor silently degraded to advisory once: the failure decision compares
``round(total, precision)``, so at coverage's default precision of 0 a 94.93%
total rounded up to 95, the command exited 0, and only the terminal summary said
"FAIL ... not reached". Every caller — the Nx targets, ``just check``, ``just
gate``, the pre-push hook — read that zero as a pass.

Since the code suite was split into tiers that run concurrently, the floor is no
longer evaluated inside a test invocation at all. ``orchestrator:test`` and
``orchestrator:test-serial`` each **measure** into their own data file and decide
nothing; ``orchestrator:coverage`` combines those files and compares the combined
total to ``[tool.coverage.report] fail_under``. That moved the enforcement point
but not the requirement: the floor still has exactly one source, and it still has
to be able to fail the build.

These tests drive that whole shape at the real boundary — two measuring
invocations, then a real ``coverage combine`` and ``coverage report`` — over a
generated package whose total is exact arithmetic and lands inside the band the
regression once forgave, configured with the floor and precision this repository
actually declares. They fail if either setting drifts back to a combination that
cannot fail the build, if a measuring tier starts deciding the floor, if the
enforced total stops counting a tier that measured, or if a measured data file
stops being portable enough to enforce in a checkout that did not write it.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from nx_inputs import CODE_SCOPED, COVERAGE_SCOPED, SERIAL_SCOPED

from orchestrator import REPO_ROOT

# Each generated function contributes exactly two statements (its ``def`` line,
# executed at import, and its ``return`` line, executed only when called) and no
# branches, so the fixture's coverage total is exact arithmetic.
FUNCTIONS = 200
STATEMENTS = FUNCTIONS * 2
#: The data file each measuring tier writes, and the one the combine produces,
#: named as the real targets name them.
SERIAL_DATA = ".coverage.serial"
PARALLEL_DATA = ".coverage.parallel"
COMBINED_DATA = ".coverage"
#: How many of the called functions the serial tier covers. Deliberately a small
#: share: neither tier reaches the floor on its own, so a tier that decided the
#: floor shows up as a failed command rather than as luck.
SERIAL_SHARE = 20


@pytest.fixture(scope="module")
def declared() -> dict[str, dict[str, object]]:
    """This repository's own ``[tool.coverage]`` tables."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return pyproject["tool"]["coverage"]


@pytest.fixture(scope="module")
def report_config(declared: dict[str, dict[str, object]]) -> dict[str, object]:
    """The declared ``[tool.coverage.report]`` table — the floor's single source."""
    return declared["report"]


@pytest.fixture(scope="module")
def relative_files(declared: dict[str, dict[str, object]]) -> bool:
    """Whether the declared ``[tool.coverage.run]`` records portable paths.

    Read rather than assumed, so the fixture project measures under the same setting
    the real tiers do and a run table that loses it fails a test instead of a build.
    """
    return bool(declared["run"].get("relative_files", False))


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


@pytest.fixture(scope="module")
def targets() -> dict[str, dict]:
    """The real Nx target declarations this module's fixture project imitates."""
    project = json.loads((REPO_ROOT / "orchestrator" / "project.json").read_text(encoding="utf-8"))
    return project["targets"]


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


def _write_project(
    root: Path, *, uncalled: int, fail_under: float, precision: int, relative_files: bool
) -> float:
    """Generate a package plus the two tiers' tests, with an exactly known total.

    The called functions are split between a serial module and a parallel one so
    the combined total is reachable only by combining both tiers' data — which is
    the property the split has to preserve.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "sample.py").write_text(
        "".join(f"def f{index}() -> int:\n    return {index}\n\n\n" for index in range(FUNCTIONS)),
        encoding="utf-8",
    )
    called = FUNCTIONS - uncalled
    assert called > SERIAL_SHARE, "the fixture must leave the parallel tier work of its own"
    for module, indexes in (
        ("test_serial", range(SERIAL_SHARE)),
        ("test_parallel", range(SERIAL_SHARE, called)),
    ):
        (root / f"{module}.py").write_text(
            f"import sample\n\n\ndef {module}() -> None:\n"
            + "".join(f"    assert sample.f{index}() == {index}\n" for index in indexes),
            encoding="utf-8",
        )
    (root / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n"
        'testpaths = ["."]\n\n'
        "[tool.coverage.run]\n"
        'source = ["sample"]\n'
        "branch = true\n"
        f"relative_files = {str(relative_files).lower()}\n\n"
        "[tool.coverage.report]\n"
        f"fail_under = {fail_under}\n"
        f"precision = {precision}\n",
        encoding="utf-8",
    )
    return (STATEMENTS - uncalled) / STATEMENTS * 100.0


def _environment(root: Path, data_file: str) -> dict[str, str]:
    """The environment one step of the fixture project runs under.

    ``COVERAGE_FILE`` is set on every step, never left to the caller's. The tier
    running this test names its own data file in that variable, so an inherited one
    pointed this fixture's combine at the suite's own coverage data — which both
    corrupted the run being measured and left the fixture reporting a total it
    never produced.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("COV_CORE")}
    env.pop("FORCE_COLOR", None)
    env["NO_COLOR"] = "1"
    env["PYTHONPATH"] = str(root)
    env["COVERAGE_FILE"] = data_file
    return env


def _run(
    root: Path, *args: str, data_file: str = COMBINED_DATA
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", *args],
        cwd=root,
        env=_environment(root, data_file),
        text=True,
        capture_output=True,
    )


def _measure_serial(root: Path) -> subprocess.CompletedProcess[str]:
    """Run the serial tier the way ``orchestrator:test-serial`` runs: no pytest-cov."""
    return _run(
        root,
        "coverage",
        "run",
        "-m",
        "pytest",
        "test_serial.py",
        "--no-cov",
        "-q",
        data_file=SERIAL_DATA,
    )


def _measure_parallel(root: Path) -> subprocess.CompletedProcess[str]:
    """Run the parallel tier the way ``orchestrator:test`` runs: pytest-cov across workers."""
    return _run(
        root,
        "pytest",
        "test_parallel.py",
        "-n",
        "2",
        "--dist",
        "load",
        "--cov=sample",
        "--cov-report=",
        "--cov-fail-under=0",
        "-q",
        data_file=PARALLEL_DATA,
    )


def _enforce(root: Path) -> subprocess.CompletedProcess[str]:
    """Combine both tiers and compare the total to the floor, as ``coverage`` does."""
    (root / COMBINED_DATA).unlink(missing_ok=True)
    combined = _run(root, "coverage", "combine", SERIAL_DATA, PARALLEL_DATA)
    assert combined.returncode == 0, combined.stdout + combined.stderr
    return _run(root, "coverage", "report")


def _reported_total(output: str) -> float:
    """The total percentage one ``coverage report`` printed."""
    match = re.search(r"^TOTAL\s+.*?([0-9]+\.[0-9]+)%", output, flags=re.MULTILINE)
    assert match is not None, output
    return float(match.group(1))


def test_neither_measuring_tier_decides_the_floor(
    tmp_path: Path, floor: float, precision: int, relative_files: bool
) -> None:
    """Both tiers run below the floor and both succeed: measuring is not judging.

    The tiers run concurrently, so neither can see the combined total. A tier that
    still evaluated ``fail_under`` would fail every run on its own share — and a
    tier told to ignore the floor is the only way the declared one stays the only
    floor there is.
    """
    root = tmp_path / "measure"
    _write_project(
        root,
        uncalled=_uncalled_for_band(floor),
        fail_under=floor,
        precision=precision,
        relative_files=relative_files,
    )

    serial = _measure_serial(root)
    parallel = _measure_parallel(root)

    assert serial.returncode == 0, serial.stdout + serial.stderr
    assert parallel.returncode == 0, parallel.stdout + parallel.stderr
    assert "fail-under" not in serial.stdout + parallel.stdout, serial.stdout + parallel.stdout
    for data_file in (SERIAL_DATA, PARALLEL_DATA):
        assert (root / data_file).is_file(), f"{data_file} was not measured"
        alone = _reported_total(_run(root, "coverage", "report", data_file=data_file).stdout)
        assert alone < floor, (
            f"{data_file} reaches {alone}% alone, so this fixture would pass even if that "
            "tier enforced the floor; it has to prove the tier is not enforcing"
        )


def test_the_enforced_total_combines_every_tier_and_can_fail_the_build(
    tmp_path: Path, floor: float, precision: int, relative_files: bool
) -> None:
    """The floor is decided once, on data no tier is missing from, and it can fail."""
    root = tmp_path / "below"
    total = _write_project(
        root,
        uncalled=_uncalled_for_band(floor),
        fail_under=floor,
        precision=precision,
        relative_files=relative_files,
    )
    _measure_serial(root)
    _measure_parallel(root)
    # Read before enforcing: combining consumes the per-tier data files, exactly as
    # it does in the real target.
    alone = {
        data_file: _reported_total(_run(root, "coverage", "report", data_file=data_file).stdout)
        for data_file in (SERIAL_DATA, PARALLEL_DATA)
    }

    result = _enforce(root)

    combined = _reported_total(result.stdout)
    assert combined == pytest.approx(total, abs=0.01), result.stdout
    for data_file, measured in alone.items():
        assert combined > measured, (
            f"the enforced total {combined}% is not above {data_file}'s {measured}%, so the "
            "tier that wrote it is not counted towards the floor"
        )
    assert f"less than fail-under={floor:.{precision}f}" in result.stdout, result.stdout
    assert result.returncode != 0, (
        "the configured floor did not fail the command; coverage enforcement is advisory "
        f"again:\n{result.stdout}"
    )


def test_a_tier_that_did_not_measure_fails_the_enforcement(
    tmp_path: Path, floor: float, precision: int, relative_files: bool
) -> None:
    """A missing tier must stop the run, not quietly shrink the total it is judged on.

    This is why the enforcing command names each tier's data file instead of
    letting `coverage report` discover whatever is lying about: a tier whose data
    never arrived would otherwise leave the floor evaluated against the tiers that
    did — a lower total judged as if it were the whole suite's, which is a failure
    that reads as a coverage regression rather than as a missing tier.
    """
    root = tmp_path / "missing"
    _write_project(
        root,
        uncalled=_uncalled_for_band(floor),
        fail_under=floor,
        precision=precision,
        relative_files=relative_files,
    )
    _measure_serial(root)
    _measure_parallel(root)
    (root / PARALLEL_DATA).unlink()

    result = _run(root, "coverage", "combine", SERIAL_DATA, PARALLEL_DATA)

    assert result.returncode != 0, result.stdout + result.stderr
    assert PARALLEL_DATA in result.stdout + result.stderr, result.stdout + result.stderr


def test_at_or_above_floor_run_exits_zero(
    tmp_path: Path, floor: float, precision: int, relative_files: bool
) -> None:
    """The same fixture passes once the floor sits at its total — the failure is the floor's."""
    root = tmp_path / "above"
    uncalled = _uncalled_for_band(floor)
    total = _write_project(
        root,
        uncalled=uncalled,
        fail_under=(STATEMENTS - uncalled) / STATEMENTS * 100.0,
        precision=precision,
        relative_files=relative_files,
    )
    _measure_serial(root)
    _measure_parallel(root)

    result = _enforce(root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _reported_total(result.stdout) == pytest.approx(total, abs=0.01), result.stdout
    assert "fail-under" not in result.stdout, result.stdout


def test_the_floor_is_enforced_on_data_measured_in_a_directory_that_is_gone(
    tmp_path: Path, floor: float, precision: int, relative_files: bool
) -> None:
    """A measured data file is a statement about the tree, not about one directory.

    Both measuring tiers declare their data file as an Nx output, so a cache hit
    hands the enforcing tier a file some *other* checkout wrote. On this host that
    other checkout is a per-dispatch worktree which is usually deleted by then, so
    coverage recording absolute paths turned the saving into a failure: combine
    succeeded, and the report then said "No source for code" about a file sitting
    under the current root the whole time — a green branch failing its own gate for
    where it had been measured rather than for what it covers.

    Measure, move the whole project to a path the data was never written under, and
    delete the original: that is the replay, at the real boundary.
    """
    measured = tmp_path / "measured"
    total = _write_project(
        measured,
        uncalled=_uncalled_for_band(floor),
        fail_under=(STATEMENTS - _uncalled_for_band(floor)) / STATEMENTS * 100.0,
        precision=precision,
        relative_files=relative_files,
    )
    _measure_serial(measured)
    _measure_parallel(measured)

    replayed = tmp_path / "replayed"
    shutil.copytree(measured, replayed)
    shutil.rmtree(measured)
    result = _enforce(replayed)

    assert "No source for code" not in result.stdout + result.stderr, (
        "the combined data names the directory it was measured in, so a replayed "
        f"cache cannot be enforced against this tree:\n{result.stdout}{result.stderr}"
    )
    assert _reported_total(result.stdout) == pytest.approx(total, abs=0.01), result.stdout
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_measuring_tier_writes_data_only_the_coverage_tier_judges(
    targets: dict[str, dict], floor: float
) -> None:
    """The real targets must keep measuring and judging in different commands.

    Without ``--cov``/``coverage run`` a tier contributes nothing and the combined
    total silently drops it. A ``--cov-fail-under`` naming a real floor anywhere
    would outrank ``pyproject.toml`` and give the repository two floors, which is
    the drift this whole module exists to catch.
    """
    assert "--cov=orchestrator" in targets[CODE_SCOPED]["command"], targets[CODE_SCOPED]["command"]
    assert "coverage run -m pytest" in targets[SERIAL_SCOPED]["command"], targets[SERIAL_SCOPED][
        "command"
    ]

    judging = targets[COVERAGE_SCOPED]["command"]
    assert "coverage combine" in judging and "coverage report" in judging, judging
    for name, target in targets.items():
        overrides = re.findall(r"--cov-fail-under[= ]([0-9.]+)", target["command"])
        assert all(float(value) == 0 for value in overrides), (
            f"the floor's single source is pyproject.toml (fail_under = {floor:g}); "
            f"orchestrator:{name} declares its own: {target['command']}"
        )
        if name != COVERAGE_SCOPED:
            assert "coverage report" not in target["command"], (
                f"orchestrator:{name} reports before every tier has measured, so it would "
                f"judge a partial total: {target['command']}"
            )
