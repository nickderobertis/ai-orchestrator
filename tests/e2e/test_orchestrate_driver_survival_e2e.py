"""The tier above a round: the driver `just orchestrate` launches.

`tests/e2e/test_successor_survival_e2e.py` proves a *round owner* outlives both halves
of its launcher's ending — the turn teardown's signals, and the scratch sweep that
follows the launching dispatch. The driver that owns the whole run was left out of that
fix: it is spawned rather than re-`exec`ed, so it could not claim a dispatch attribution
of its own, and it went on carrying the stamp of whatever dispatch typed the command.
Once that dispatch settled, the sweep read the stamp as proof of a leaked tree and would
terminate the longest-lived process a run has — and every round beneath it.

So this journey runs the real sequence with nothing faked but the paid model: a real
`just orchestrate`, launched attached under a real dispatch's stamp exactly as a planner
turn launches one, a real teardown of that turn's process group, that dispatch's
ownership genuinely released, and then the real `just sweep-scratch` over the same
scratch root. The driver has to still be there afterwards, and still drive its run to a
settled round once it is released.

The companion half runs in that same sweep, because a fix that saved the driver by
weakening the reaper would pass every assertion above: a genuinely leaked process —
stamped for the finished dispatch, and re-attributed by nothing — must still be reaped
by the run that spares the driver.
"""

# llmlint: ignore-file[e2e_not_mocked] This drives the real `just orchestrate` and `just
# sweep-scratch` recipes, the real onejudge CLI, and a real process teardown; only the paid
# model is onejudge's own `command`-provider double, the explicit external-boundary
# exception AGENTS.md documents for this suite.

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import yaml
from process_tree import (
    await_reaped,
    is_running,
    spawn_reparented_leaving,
    write_reparented_leaving,
)
from waits import deadline as e2e_deadline
from waits import timeout as e2e_timeout

from orchestrator import BASE_CONFIG, REPO_ROOT
from orchestrator.coordination import process_start_identity
from orchestrator.scratch import (
    AGENT_STATUS_DIR_ENV,
    AGENT_STATUS_DIR_NAME,
    OWNER_LOCK_NAME,
    WATCHDOG_PATTERN,
)

FAKE_BACKEND = REPO_ROOT / "tests" / "e2e" / "fake_backend.py"
#: An owner record for a pid the kernel will not hand out again — what a settled
#: dispatch leaves behind once it has released its scratch directory.
FINISHED_OWNER = "999999999 1"


def _live_owner_record(pid: int) -> str:
    """The record a dispatch writes while it still holds its scratch directory."""
    identity = process_start_identity(pid)
    assert identity is not None, f"procfs did not identify pid {pid}"
    return f"{pid} {identity}"


def _base(tmp_path: Path) -> Path:
    """The real base config with only the paid provider swapped for the backend."""
    value = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    value["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    path = tmp_path / "base.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return path


def _plan(tmp_path: Path, release: Path) -> Path:
    """A plan whose driver parks before its first round until the test releases it.

    The pause is what keeps the driver demonstrably *unfinished* across the teardown
    and the sweep: a run that had already settled would survive both by having nothing
    left to do, which is not the property under test.
    """
    path = tmp_path / "driver-survival-plan.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": "driver-survives",
                "tasks": [
                    {
                        "id": "worker",
                        "persona": "engineer",
                        "task": f"pre-round-pause {release} complete-now no-assessment",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def scratch_root(tmp_path: Path) -> Path:
    """The one temporary root the launch and everything under it writes into.

    Pointed at through ``TMPDIR``, which is what `tempfile` — and so every scratch
    directory in this harness — resolves, so sweeping this root sweeps the real thing
    rather than a stand-in for it.
    """
    root = tmp_path / "scratch"
    root.mkdir()
    return root


class LaunchedRun:
    """One attached `just orchestrate`, started the way a planner turn starts it."""

    def __init__(self, process: subprocess.Popen[bytes], run_dir: Path, streams: Path) -> None:
        self.process = process
        self.group = process.pid
        self.run_dir = run_dir
        self.err = streams
        self.driver = self._await_driver()

    def _await_driver(self) -> int:
        status = self.run_dir / "orchestrator" / "status.json"
        guard = e2e_deadline(120)
        while time.monotonic() < guard:
            if status.is_file():
                return int(json.loads(status.read_text(encoding="utf-8"))["pid"])
            time.sleep(0.02)
        reported = self.err.read_text(encoding="utf-8", errors="replace")
        pytest.fail(f"the launch never recorded its driver at {status}\n{reported}")

    def teardown_launching_turn(self) -> None:
        """Kill the launcher's whole process group, as ending a harness turn does."""
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self.group, signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.process.wait(timeout=e2e_timeout(60))


@pytest.fixture
def launch(
    tmp_path: Path, scratch_root: Path, onejudge_bin: str
) -> Iterator[Callable[..., LaunchedRun]]:
    """Start attached launches under a dispatch's stamp, and reap whatever survives."""
    started: list[LaunchedRun] = []

    def _start(status_dir: Path | None, run_id: str, release: Path) -> LaunchedRun:
        errors = tmp_path / f"{run_id}.err"
        stamp = {} if status_dir is None else {AGENT_STATUS_DIR_ENV: os.fspath(status_dir)}
        arguments = [
            "just",
            "orchestrate",
            str(_plan(tmp_path, release)),
            "--run-id",
            run_id,
            "--runs-dir",
            str(tmp_path / "runs"),
            "--base",
            str(_base(tmp_path)),
            "--onejudge-bin",
            onejudge_bin,
            "--skill-command",
            sys.executable,
            str(FAKE_BACKEND),
        ]
        with (tmp_path / f"{run_id}.out").open("wb") as out, errors.open("wb") as err:
            process = subprocess.Popen(
                arguments,
                cwd=REPO_ROOT,
                stdout=out,
                stderr=err,
                # A harness turn puts what it starts in a group of its own and tears
                # that group down at the end; this is the group the teardown aims at.
                start_new_session=True,
                env={
                    key: value
                    for key, value in {
                        **os.environ,
                        "TMPDIR": str(scratch_root),
                        "XDG_STATE_HOME": str(tmp_path / "state"),
                        "ONEHARNESS_HISTORY_DIR": str(tmp_path / "history"),
                        **stamp,
                    }.items()
                    # A planner's shell carries no dispatch attribution, and this suite
                    # may itself be running under one.
                    if key != AGENT_STATUS_DIR_ENV or status_dir is not None
                },
            )
        run = LaunchedRun(process, tmp_path / "runs" / run_id, errors)
        started.append(run)
        return run

    yield _start
    for run in started:
        for pid in (run.driver, run.group):
            with contextlib.suppress(PermissionError, ProcessLookupError):
                os.killpg(os.getpgid(pid), signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            run.process.wait(timeout=e2e_timeout(30))


def _sweep(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", "sweep-scratch", "--root", str(root)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
        timeout=e2e_timeout(180),
    )


def _driver_directory(root: Path, driver: int) -> Path:
    """The watchdog directory the driver was re-attributed to.

    Found by the owner it records rather than by name, which is a temporary one nothing
    outside the launch knows — and which is the same evidence the sweeper reads. A
    successor directory carries no worker ``pid`` file, so a dispatch's own watchdog
    tree cannot be mistaken for one.
    """
    found = [
        candidate
        for candidate in sorted(root.glob(WATCHDOG_PATTERN))
        if (record := candidate / OWNER_LOCK_NAME).is_file()
        and record.read_text(encoding="utf-8").split()[:1] == [str(driver)]
        and not (candidate / "pid").exists()
    ]
    if len(found) != 1:
        pytest.fail(
            f"expected exactly one scratch directory claimed by the driver {driver} under "
            f"{root}, found {[str(path) for path in found]}"
        )
    return found[0]


def _await_result(path: Path) -> dict[str, object]:
    guard = e2e_deadline(240)
    while time.monotonic() < guard:
        if path.exists():
            parsed: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
            return parsed
        time.sleep(0.05)
    pytest.fail(f"the driver never recorded a round result at {path}")


@pytest.mark.load_sensitive
def test_an_orchestrate_driver_outlives_the_sweep_that_reaps_its_launchers_leavings(
    tmp_path: Path,
    scratch_root: Path,
    launch: Callable[..., LaunchedRun],
) -> None:
    """The driver survives its launcher's whole ending; a real leak still does not."""
    # llmlint: ignore-block[tests_mirror_real_usage] The *launching dispatch* is the
    # fixture here, not the subject, and no public command creates one and then settles
    # it on demand: `owned_scratch_directory` holds its record for the life of a real
    # dispatcher, so a live one could only be produced by dispatching a paid worker and
    # could only be settled by waiting for it. Writing the record is how
    # tests/e2e/test_successor_survival_e2e.py stages the identical boundary, and
    # everything under test — the launch, the teardown, the sweep, the driver — is real.
    launcher = scratch_root / "orchestrator-watchdog-launcher"
    status_dir = launcher / AGENT_STATUS_DIR_NAME
    status_dir.mkdir(parents=True)
    (launcher / OWNER_LOCK_NAME).write_text(_live_owner_record(os.getpid()), encoding="utf-8")
    release = tmp_path / "release-pre-round"

    run = launch(status_dir, "driver-survives", release)
    # Started while the launching dispatch is still live and stamped for it, exactly as
    # anything else that dispatch starts is. Nothing re-attributes it, so once the
    # dispatch is released below this is a genuinely leaked tree.
    leaked = spawn_reparented_leaving(
        write_reparented_leaving(tmp_path),
        tmp_path / "leaked.pid",
        status_dir,
        timeout=e2e_timeout(60),
    )

    run.teardown_launching_turn()

    assert is_running(run.driver), "the driver died with the turn that launched it"
    # The launching step settles: its dispatcher releases the scratch directory, which
    # is what turns every stamp naming it into evidence of something left behind.
    (launcher / OWNER_LOCK_NAME).write_text(FINISHED_OWNER, encoding="utf-8")
    # llmlint: ignore-end[tests_mirror_real_usage]

    swept = _sweep(scratch_root)

    assert is_running(run.driver), (
        f"the sweep reaped the run's driver as its launcher's leaving\n{swept.stdout}"
    )
    # It survived because it is attributed to a directory of its own, and that directory
    # outlived the sweep too. Resolved after the sweep for that reason.
    driver_directory = _driver_directory(scratch_root, run.driver)
    assert driver_directory.is_dir(), "the sweep reclaimed the directory the driver is under"
    assert await_reaped(leaked, timeout=e2e_timeout(60)), (
        f"a genuinely leaked tree survived the sweep that spared the driver\n{swept.stdout}"
    )
    assert str(leaked) in swept.stdout

    # Alive is not the claim; still driving is. The driver was parked before its first
    # round throughout the teardown and the sweep, and releasing it now has to produce a
    # real settled round — the work an operator loses when this tree is reaped.
    release.touch()
    settled = _await_result(run.run_dir / "round-01" / "result.json")
    assert settled["state"] == "complete", settled


@pytest.mark.load_sensitive
def test_a_launch_from_a_plain_shell_claims_no_attribution_and_drives_anyway(
    tmp_path: Path,
    scratch_root: Path,
    launch: Callable[..., LaunchedRun],
) -> None:
    """A planner's own shell is not a dispatch, and nothing is claimed for one there.

    The re-attribution exists to escape a *launching dispatch's* stamp. A launch that
    carries none has nothing to escape, and claiming a directory anyway would leave one
    behind per launch for a later sweep to reclaim. This asserts the negative directly,
    because it is the branch that runs on every planner's terminal.
    """
    release = tmp_path / "release-unstamped"

    run = launch(None, "driver-unstamped", release)
    release.touch()

    settled = _await_result(run.run_dir / "round-01" / "result.json")
    assert settled["state"] == "complete", settled
    # A dispatch's own watchdog tree records the worker `pid` it is watching; a claimed
    # successor directory is exactly the one without that file, so this is the whole
    # population the launch could have left behind.
    claimed = [path for path in scratch_root.glob(WATCHDOG_PATTERN) if not (path / "pid").exists()]
    assert claimed == [], claimed
