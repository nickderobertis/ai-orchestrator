"""`--dist loadgroup` really does serialise one xdist group, driven rather than assumed.

Every journey carrying `WORKSPACE_INSTALL_MARKS` drives a real `bun install
--frozen-lockfile` into one shared `node_modules`, and the group half of that tuple is
the only thing keeping two of them from doing it at once. The deadline-based channel
journeys name that same group, because their every step is a `just` recipe waiting on
the `<root>/.venv` lock those installs hold. That makes the scheduler's behaviour
load-bearing here rather than incidental, so it is measured: real pytest-xdist over real
tests that report which worker took them and when.

Non-overlap is asserted beside co-location because it is the property the shared install
needs — a group pinned to one worker would still race if xdist ever ran a worker's tests
concurrently, and it is that, not co-location, that the `EEXIST` failures came from.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from nx_workspace import SHARED_TOOLCHAIN_GROUP
from waits import timeout as e2e_timeout

#: Enough tests that a load-balancing scheduler would split them across two workers,
#: and few enough that the run stays about a second.
GROUPED = 6

#: Tests that declare the group and record when and where each of them ran. `worker_id`
#: is pytest-xdist's own fixture, so what lands in the log is the scheduler's answer
#: rather than this file's guess at it.
GENERATED = """
import os
import time

import pytest

pytestmark = pytest.mark.xdist_group({group!r})


@pytest.mark.parametrize("index", range({count}))
def test_records_where_and_when_it_ran(index, worker_id):
    started = time.monotonic()
    # Long enough that two workers running this concurrently would overlap.
    time.sleep(0.2)
    ended = time.monotonic()
    assert worker_id.startswith("gw"), f"not an xdist worker: {{worker_id}}"
    assert ended - started >= 0.2, "the interval this reports is not the one it took"
    with open(os.environ["SHARED_INSTALL_LOG"], "a", encoding="utf-8") as log:
        log.write(f"{{worker_id}} {{started}} {{ended}}\\n")
"""


class Ran(NamedTuple):
    """One recorded run of a grouped test: which worker took it, and when."""

    worker: str
    started: float
    ended: float


def _schedule(tmp_path: Path) -> list[Ran]:
    """Run the generated tests under real pytest-xdist and read back what happened."""
    generated = tmp_path / "test_generated.py"
    generated.write_text(
        GENERATED.format(group=SHARED_TOOLCHAIN_GROUP, count=GROUPED), encoding="utf-8"
    )
    log = tmp_path / "schedule.log"
    log.write_text("", encoding="utf-8")

    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(generated),
            "-p",
            "no:cacheprovider",
            "-n",
            "2",
            "--dist",
            "loadgroup",
            "-q",
        ],
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "SHARED_INSTALL_LOG": str(log),
        },
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr

    recorded = log.read_text(encoding="utf-8").splitlines()
    return [
        Ran(worker, float(started), float(ended))
        for worker, started, ended in (line.split() for line in recorded)
    ]


def test_one_group_runs_on_one_worker_and_never_two_at_once(tmp_path: Path) -> None:
    """The guarantee the shared install rests on, read off the run rather than restated."""
    recorded = _schedule(tmp_path)

    assert len(recorded) == GROUPED, f"only {len(recorded)} of {GROUPED} tests reported"
    workers = {ran.worker for ran in recorded}
    assert len(workers) == 1, (
        f"the {SHARED_TOOLCHAIN_GROUP!r} group ran across {sorted(workers)}; "
        "`--dist loadgroup` is what keeps every journey sharing one `node_modules` off "
        "each other, and it is not in force"
    )

    ordered = sorted(recorded, key=lambda ran: ran.started)
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        assert earlier.ended <= later.started, (
            f"{earlier} and {later} overlapped, so two journeys sharing one install "
            "could run their `bun install` at the same time"
        )
