"""The one analysis every budget runs, driven as onebudgetspec runs it, and its wiring.

`python -m tests.budget_telemetry` is each budget's command: it is run here as a
subprocess with the variables onebudgetspec and the `budgets` target set, against parts
the real recorder wrote, and its result file is read back. A recording that is missing,
empty or unreadable fails the budget naming it, so nothing ever reports a default. And
each project's `budgets` target reads only directories that a test target it depends on
declares as its output and clears before it runs, so a budget never reads telemetry an
earlier tree left.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import budget_telemetry
import pytest
import yaml
from nx_inputs import project_declarations

from orchestrator.root import REPO_ROOT

BUDGET = "scratch-points"


def _analysed(
    tmp_path: Path, sources: list[Path], budget: str = BUDGET, **extra: str
) -> tuple[subprocess.CompletedProcess[str], dict | None]:
    """Run the analysis as a budget's command, and the result it wrote, if any."""
    result = tmp_path / "result.json"
    result.unlink(missing_ok=True)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("BUDGET_TELEMETRY_", "ONEBUDGETSPEC_"))
    }
    environment |= {
        budget_telemetry.BUDGET_ENV: budget,
        budget_telemetry.SOURCES_ENV: " ".join(map(str, sources)),
        "ONEBUDGETSPEC_RESULT": str(result),
        **extra,
    }
    done = subprocess.run(
        [sys.executable, "-m", "tests.budget_telemetry"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    written = json.loads(result.read_text(encoding="utf-8")) if result.exists() else None
    return done, written


def _recorded(directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(budget_telemetry.TELEMETRY_ENV, str(directory))


def test_a_sum_budget_reports_its_counted_parts_total_and_every_part_as_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "recorded"
    _recorded(source, monkeypatch)
    assert budget_telemetry.record(BUDGET, "phase reads", 3, unit="points", requests=4)
    assert budget_telemetry.record(BUDGET, "phase writes", 5, unit="points", requests=5)
    assert budget_telemetry.record(BUDGET, "step copy", 2, unit="points", counted=False)

    done, written = _analysed(tmp_path, [source])

    assert done.returncode == 0, done.stderr
    assert written == {
        "value": 8,
        "detail": "phase reads: 3 points, 4 requests\nphase writes: 5 points, 5 requests\n"
        "step copy: 2 points",
    }


def test_a_budget_reads_its_parts_from_whichever_recording_target_measured_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project's budgets read several targets' outputs, each budget's parts in one."""
    measuring, other = tmp_path / "measuring", tmp_path / "other"
    _recorded(other, monkeypatch)
    budget_telemetry.record("another-budget", "part", 1, unit="points")
    _recorded(measuring, monkeypatch)
    budget_telemetry.record(BUDGET, "phase", 4, unit="points")

    done, written = _analysed(tmp_path, [other, measuring])

    assert done.returncode == 0, done.stderr
    assert written == {"value": 4, "detail": "phase: 4 points"}


def test_a_max_budget_reports_its_largest_part_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "recorded"
    _recorded(source, monkeypatch)
    for label, size in (("case a", 90), ("case b", 97.5)):
        budget_telemetry.record(BUDGET, label, size, unit="characters", combine="max")

    done, written = _analysed(tmp_path, [source])

    assert done.returncode == 0, done.stderr
    assert written == {
        "value": 97.5,
        "detail": "case a: 90 characters\ncase b: 97.50 characters\nlargest: case b",
    }


def test_outside_a_recording_target_a_test_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(budget_telemetry.TELEMETRY_ENV, raising=False)
    assert budget_telemetry.record(BUDGET, "phase", 1, unit="points") is False


#: What the analysis says for each recording it cannot report from.
DIAGNOSTICS = {
    "missing": "nothing was recorded under",
    "empty": "nothing was recorded under",
    "unreadable": "is not a readable recorded part",
    "unknown-combination": "names no known combination: 'median'",
    "uncounted": "no recorded part counts toward its figure",
    "no-sources": "names no recorded directory",
    "unnamed": "is unset: run this as a budget's command",
}


@pytest.mark.parametrize(
    "fault",
    ["missing", "empty", "unreadable", "unknown-combination", "uncounted", "no-sources", "unnamed"],
)
def test_a_recording_that_cannot_be_read_fails_the_budget_naming_it(
    fault: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "recorded"
    sources = [source]
    budget = BUDGET
    match fault:
        case "empty":
            (source / BUDGET).mkdir(parents=True)
        case "unreadable":
            (source / BUDGET).mkdir(parents=True)
            (source / BUDGET / "part.json").write_text("{not json", encoding="utf-8")
        case "unknown-combination":
            (source / BUDGET).mkdir(parents=True)
            part = {"label": "a", "value": 1, "unit": "points", "combine": "median"}
            (source / BUDGET / "part.json").write_text(
                json.dumps({**part, "counted": True, "also": {}}), encoding="utf-8"
            )
        case "uncounted":
            _recorded(source, monkeypatch)
            budget_telemetry.record(BUDGET, "explains", 2, unit="points", counted=False)
        case "no-sources":
            sources = []
        case "unnamed":
            budget = ""

    done, written = _analysed(tmp_path, sources, budget=budget)

    assert done.returncode == 1, done.stderr
    assert written is None, "a budget whose recording was not read reported a value"
    assert done.stderr.startswith(f"budget {budget or '(unnamed)'}: "), done.stderr
    assert DIAGNOSTICS[fault] in done.stderr, done.stderr


def test_parts_of_one_figure_that_disagree_on_how_they_combine_fail_the_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "recorded"
    _recorded(source, monkeypatch)
    budget_telemetry.record(BUDGET, "a", 1, unit="points", combine="sum")
    budget_telemetry.record(BUDGET, "b", 2, unit="points", combine="max")

    done, written = _analysed(tmp_path, [source])

    assert (done.returncode, written) == (1, None), done.stderr
    assert "combine as ['max', 'sum']" in done.stderr, done.stderr


class Task(NamedTuple):
    """One Nx task: a project's target."""

    project: str
    target: str


class Recording(NamedTuple):
    """A test target recording telemetry: the directory it declares, and its command."""

    directory: str
    command: str


def _recordings() -> dict[Task, Recording]:
    """Each test target that records telemetry, by the output directory it declares."""
    recordings: dict[Task, Recording] = {}
    for project in project_declarations().values():
        for name, target in project.get("targets", {}).items():
            for output in target.get("outputs", []):
                if "/.telemetry/" in output:
                    directory = output.removeprefix("{workspaceRoot}/")
                    recordings[Task(project["name"], name)] = Recording(
                        directory, target["command"]
                    )
    return recordings


def _depended(project: str, target: dict) -> set[Task]:
    return {
        Task(dependency["projects"][0], dependency["target"])
        if isinstance(dependency, dict)
        else Task(project, dependency)
        for dependency in target["dependsOn"]
    }


def _analysed_by_target(root: str) -> set[str]:
    """Which budgets targets a project's file needs reading telemetry: by `host` label."""
    document = yaml.safe_load((REPO_ROOT / root / "budgets.yaml").read_text(encoding="utf-8"))
    return {
        "budgets-host" if "host" in budget.get("labels", []) else "budgets"
        for budget in document["budgets"]
        if "tests.budget_telemetry" in budget["command"]
    }


def test_each_budgets_target_reads_only_what_the_targets_it_depends_on_record() -> None:
    """Every source is a dependency's declared output, cleared and named by its command.

    Each project whose budgets run the analysis names, on the target checking them, the
    outputs of the targets it depends on. A cached `budgets` target is keyed on those
    outputs too, so a cached verdict is one over the same telemetry; an uncached
    `budgets-host` one reads what its uncached dependency measured in the same run.
    """
    recordings = _recordings()
    for recording in recordings.values():
        assert recording.command.startswith(f"rm -rf {recording.directory} && "), recording
        assert f"{budget_telemetry.TELEMETRY_ENV}={recording.directory} " in recording.command
    analysing = 0
    for root, project in project_declarations().items():
        if not root or not (REPO_ROOT / root / "budgets.yaml").is_file():
            continue
        for name in _analysed_by_target(root):
            analysing += 1
            target = project["targets"][name]
            sources = target.get("options", {}).get("env", {}).get(budget_telemetry.SOURCES_ENV)
            assert sources, f"{root}:{name} runs the analysis and names nothing it reads"
            depended = _depended(project["name"], target)
            read = sorted(recordings[task].directory for task in depended)
            assert sorted(sources.split()) == read, (root, name)
            if name == "budgets":
                assert {"dependentTasksOutputFiles": "**/*.json"} in target["inputs"], root
            else:
                assert all(
                    project["targets"][task.target].get("cache") is False for task in depended
                ), f"{root}'s host budgets read a figure a cache could replay"
    assert analysing, "no project's budgets run the telemetry analysis"
