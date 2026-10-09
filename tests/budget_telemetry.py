"""What the gate's tests record about what a behaviour costs, and the one analysis of it.

A cost figure in this repository's tests is a budget at the level the product owner
tracks, and the finer figures that explain it — a phase, a step, a document, a case — are
its breakdown. A test that measures one records each part it measured with :func:`record`
during its normal run, into the directory ``BUDGET_TELEMETRY_DIR`` names: the test target's
declared, uncommitted output, which that target clears before it runs, so a cached result
restores exactly the parts its run recorded. Outside a target that names the directory a
test records nothing and behaves the same.

Every budget's command is ``python -m tests.budget_telemetry``. It chooses the budget by
``ONEBUDGETSPEC_BUDGET_ID``, reads that budget's parts from the directories
``BUDGET_TELEMETRY_SOURCES`` names — the outputs of the test targets the project's budgets
target depends on — combines the counted ones into the figure, and reports it with every
part as its detail through ``onebudgetspec_sdk.report``. It never reads a ``budgets.yaml``
and never compares with a threshold: onebudgetspec is the only judge. A wall clock,
which a cache would replay from another moment, is recorded by an uncached test target
that the uncached `budgets-host` target depends on, so it is always this run's.

A missing, empty or unreadable recording fails the budget naming it, so no budget ever
reports a default in place of a measurement.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal, NamedTuple

from onebudgetspec_sdk import report

#: The directory a test records into, relative to the repository root unless absolute.
TELEMETRY_ENV = "BUDGET_TELEMETRY_DIR"
#: The recorded directories a budget's parts are read from, separated by whitespace.
SOURCES_ENV = "BUDGET_TELEMETRY_SOURCES"
#: The variable onebudgetspec sets to the id of the budget whose command is running.
BUDGET_ENV = "ONEBUDGETSPEC_BUDGET_ID"
REPO_ROOT = Path(__file__).resolve().parents[1]

#: How a budget's counted parts make its figure: their total, or the largest of them.
Combine = Literal["sum", "max"]


class Part(NamedTuple):
    """One measured part of a budget's figure, as a test recorded it."""

    label: str
    value: float
    unit: str
    combine: Combine
    counted: bool
    also: dict[str, float]

    def line(self) -> str:
        """The part as one line of the budget's breakdown."""
        figures = [f"{_figure(self.value)} {self.unit}"]
        figures += [f"{_figure(value)} {name}" for name, value in self.also.items()]
        return f"{self.label}: {', '.join(figures)}"


class TelemetryError(Exception):
    """A budget's recording that is missing, empty, unreadable or inconsistent."""


def _figure(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def _directory(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else REPO_ROOT / path


def record(  # noqa: PLR0913 - each is one fact about the part a test measured
    budget: str,
    label: str,
    value: float,
    *,
    unit: str,
    combine: Combine = "sum",
    counted: bool = True,
    **also: float,
) -> bool:
    """Record one part of ``budget``'s figure, when the running target names a directory.

    ``counted`` parts combine into the figure by ``combine``; a part that only explains it
    — the same cost measured by another journey, at another scale — is recorded with
    ``counted=False`` and appears in the breakdown alone. ``also`` carries the same part in
    other units, such as the requests beside its points. Returns whether it was recorded.
    """
    raw = os.environ.get(TELEMETRY_ENV)
    if not raw:
        return False
    directory = _directory(raw) / budget
    directory.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:60]
    digest = hashlib.sha256(label.encode()).hexdigest()[:12]
    target = directory / f"{slug}-{digest}.json"
    document = {
        "label": label,
        "value": value,
        "unit": unit,
        "combine": combine,
        "counted": counted,
        "also": also,
    }
    # Written whole and renamed into place, so a reader never meets half a part.
    staged = target.with_suffix(f".{os.getpid()}.tmp")
    staged.write_text(json.dumps(document), encoding="utf-8")
    staged.replace(target)
    return True


def _part(path: Path) -> Part:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        part = Part(
            label=str(document["label"]),
            value=float(document["value"]),
            unit=str(document["unit"]),
            combine=document["combine"],
            counted=bool(document["counted"]),
            also={str(name): float(value) for name, value in document["also"].items()},
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise TelemetryError(f"{path} is not a readable recorded part: {error}") from error
    if part.combine not in ("sum", "max"):
        raise TelemetryError(f"{path} names no known combination: {part.combine!r}")
    return part


def parts(budget: str, sources: Sequence[Path]) -> list[Part]:
    """Every part of ``budget`` the ``sources`` hold, at least one of which has to hold some.

    Each source is the output of a test target the budgets target depends on, so Nx has
    cleared or restored every one of them for this tree before the analysis runs.
    """
    if not sources:
        raise TelemetryError(f"{SOURCES_ENV} names no recorded directory")
    files = sorted(path for source in sources for path in (source / budget).glob("*.json"))
    if not files:
        searched = ", ".join(str(source / budget) for source in sources)
        raise TelemetryError(
            f"nothing was recorded under {searched}; run the test target whose output that "
            "is, which records this budget's parts"
        )
    return [_part(path) for path in files]


def analyse(recorded: Sequence[Part]) -> tuple[float, str]:
    """A budget's figure from its counted parts, and every part as its breakdown."""
    counted = [part for part in recorded if part.counted]
    if not counted:
        raise TelemetryError("no recorded part counts toward its figure")
    combinations = {part.combine for part in counted}
    if len(combinations) != 1:
        raise TelemetryError(f"its parts combine as {sorted(combinations)}")
    (combine,) = combinations
    explained = sorted((part for part in recorded if not part.counted), key=Part.line)
    lines = [part.line() for part in sorted(counted, key=Part.line)]
    if combine == "sum":
        value = sum(part.value for part in counted)
    else:
        largest = max(counted, key=lambda part: part.value)
        value = largest.value
        lines.append(f"largest: {largest.label}")
    lines += [part.line() for part in explained]
    return value, "\n".join(lines)


def main(environment: Mapping[str, str] | None = None) -> int:
    """Report the running budget's figure and breakdown; 1 with the reason when it cannot."""
    environment = os.environ if environment is None else environment
    budget = environment.get(BUDGET_ENV, "")
    try:
        if not budget:
            raise TelemetryError(f"{BUDGET_ENV} is unset: run this as a budget's command")
        sources = [_directory(raw) for raw in environment.get(SOURCES_ENV, "").split()]
        value, detail = analyse(parts(budget, sources))
    except TelemetryError as error:
        print(f"budget {budget or '(unnamed)'}: {error}", file=sys.stderr)
        return 1
    report(value, detail)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
