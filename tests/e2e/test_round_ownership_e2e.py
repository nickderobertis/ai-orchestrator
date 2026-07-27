"""A recorded round outliving — or visibly failing to outlive — its launching turn.

The failure these journeys pin down destroyed two real runs. The orchestrator agent
started `just run-plan` from inside one harness turn, surfaced an update, and ending
that turn tore down the turn's child process group: the executor took SIGTERM about a
minute after dispatching a worker onto a branch, and left `round-NN/status.json`
saying `"status": "running"` under a pid that no longer existed. Every progress
surface kept reporting the dead run as healthy and waiting on the planner, for hours.

All three layers are driven here through the real `just` recipes, against real
processes and real signals, with nothing about process liveness faked:

* tearing down the launching turn's whole process group leaves the round running, and
  it settles normally afterwards;
* a catchable signal delivered to the executor itself records the abandonment before
  the process dies, and the round refuses to be re-claimed without `--recover`;
* an uncatchable SIGKILL — the one death nothing can record — still surfaces as
  abandoned in `just runs` and `just status`, because both derive it from the recorded
  owner's pid rather than from the status string it left behind.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from waits import deadline as e2e_deadline
from waits import timeout as e2e_timeout

from orchestrator import REPO_ROOT


def _just(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=e2e_timeout(180),
    )


def _read(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _await(path: Path, what: str) -> None:
    deadline = e2e_deadline(30)
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.01)
    pytest.fail(f"{what} never appeared at {path}")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _await_exit(pid: int) -> None:
    deadline = e2e_deadline(20)
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _alive(pid), f"round owner {pid} never exited"


class Round:
    """One launched round: its launcher, its ledger, and its recorded owner."""

    def __init__(
        self,
        process: subprocess.Popen[str],
        runs: Path,
        run_id: str,
        release: Path,
        common: list[str],
    ):
        self.process = process
        self.runs = runs
        self.run_id = run_id
        self.release = release
        self.common = common
        self.owner = self._owner()

    @property
    def round_dir(self) -> Path:
        return self.runs / self.run_id / "round-01"

    def _owner(self) -> int:
        """The pid `run-plan` recorded when it claimed the round."""
        deadline = e2e_deadline(30)
        while time.monotonic() < deadline:
            with contextlib.suppress(OSError, ValueError, KeyError):
                recorded = _read(self.round_dir / "status.json")
                if recorded["status"] == "running":
                    return int(str(recorded["pid"]))
            time.sleep(0.01)
        pytest.fail(f"no running round owner was recorded under {self.round_dir}")


@pytest.fixture
def launch_round(
    tmp_path: Path, command_base: Callable[..., Path], onejudge_bin: str
) -> Iterator[Callable[[str], Round]]:
    """Launch a round parked in the real provider, and always reap what it left."""
    launched: list[Round] = []
    runs = tmp_path / "runs"
    base = command_base()

    def _start(run_id: str) -> Round:
        ready = tmp_path / f"{run_id}.ready"
        release = tmp_path / f"{run_id}.release"
        plan = tmp_path / f"{run_id}.json"
        plan.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "held",
                            "persona": "engineer",
                            "task": (
                                "complete-now "
                                f"provider-barrier-ready={ready} "
                                f"provider-barrier-release={release}"
                            ),
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        # `start_new_session` is the harness turn whose teardown killed the round, and
        # the output goes to a file for the same reason the real orchestrator redirects
        # it: once that turn is gone, nothing is left draining a pipe.
        log = (tmp_path / f"{run_id}.log").open("wb")
        common = [
            "--runs-dir",
            str(runs),
            "--base",
            str(base),
            "--onejudge-bin",
            onejudge_bin,
            "--format",
            "json",
        ]
        process = subprocess.Popen(
            ["just", "run-plan", str(plan), "--run", run_id, *common],
            cwd=REPO_ROOT,
            text=True,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        _await(ready, "the dispatched worker")
        round_under_test = Round(process, runs, run_id, release, common)
        launched.append(round_under_test)
        return round_under_test

    yield _start
    for launch in launched:
        launch.release.write_text("go\n", encoding="utf-8")
        for pid in (launch.owner, launch.process.pid):
            with contextlib.suppress(PermissionError, ProcessLookupError):
                os.killpg(os.getpgid(pid), signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            launch.process.wait(timeout=e2e_timeout(15))


def test_round_survives_the_teardown_of_its_launching_turn(launch_round) -> None:
    """Ending the launching turn must leave a dispatching round running."""
    launch = launch_round("survives-teardown")
    launcher_group = os.getpgid(launch.process.pid)

    os.killpg(launcher_group, signal.SIGTERM)
    launch.process.wait(timeout=e2e_timeout(15))
    # An explicit settle window, because what is under test is the *absence* of a
    # death: the teardown escalates, with `uv run` forwarding SIGTERM to its own
    # direct child and following it with SIGKILL about two seconds later.
    time.sleep(e2e_timeout(2.0))
    assert _alive(launch.owner), "the executor died with the turn that launched it"
    assert _read(launch.round_dir / "status.json")["status"] == "running"
    # Why it survived, asserted rather than assumed: neither the group teardown nor
    # `uv run`'s forwarding to its own direct child can reach a round that is neither.
    assert os.getsid(launch.owner) == launch.owner, "the executor did not lead its own session"
    assert os.getpgid(launch.owner) != launcher_group, "the executor stayed in the launching group"

    launch.release.write_text("go\n", encoding="utf-8")
    _await(launch.round_dir / "result.json", "the round result")
    _await_exit(launch.owner)
    assert _read(launch.round_dir / "result.json")["state"] == "complete"
    assert _read(launch.round_dir / "status.json")["status"] == "completed"

    listed = _just("runs", "--runs-dir", str(launch.runs))
    assert listed.returncode == 0, listed.stderr
    assert "survives-teardown  round-01  (1 done)" in listed.stdout
    assert "ABANDONED" not in listed.stdout


def test_signalled_executor_records_its_own_abandonment(launch_round) -> None:
    """A catchable signal must never leave `running` behind a dead owner."""
    launch = launch_round("signalled-owner")

    os.kill(launch.owner, signal.SIGTERM)
    _await_exit(launch.owner)

    status = _read(launch.round_dir / "status.json")
    assert status["status"] == "abandoned"
    assert status["pid"] == launch.owner
    assert "SIGTERM" in str(status["reason"])
    assert not (launch.round_dir / "result.json").exists()

    listed = _just("runs", "--runs-dir", str(launch.runs))
    assert listed.returncode == 0, listed.stderr
    assert (
        f"! signalled-owner  round-01 ABANDONED (owner pid {launch.owner} took SIGTERM)"
        in listed.stdout
    )
    assert "--recover" in listed.stdout

    refused = _just(
        "run-plan", str(launch.round_dir / "plan.json"), "--run", "signalled-owner", *launch.common
    )
    assert refused.returncode == 2, refused.stderr
    assert "was abandoned" in refused.stderr
    assert "reclaim it with --recover" in refused.stderr


def test_killed_executor_surfaces_as_abandoned_in_runs_and_status(
    tmp_path: Path, launch_round
) -> None:
    """SIGKILL records nothing, so both views must derive it from the owner's pid."""
    launch = launch_round("killed-owner")

    os.kill(launch.owner, signal.SIGKILL)
    _await_exit(launch.owner)
    assert _read(launch.round_dir / "status.json")["status"] == "running"

    listed = _just("runs", "--runs-dir", str(launch.runs))
    assert listed.returncode == 0, listed.stderr
    assert f"! killed-owner  round-01 ABANDONED (owner pid {launch.owner} is gone)" in listed.stdout
    assert f"--run killed-owner --runs-dir {launch.runs} --recover" in listed.stdout

    history = tmp_path / "empty-history"
    history.mkdir()
    reported = subprocess.run(
        ["just", "status", "--runs-dir", str(launch.runs)],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEHARNESS_HISTORY_DIR": str(history)},
        text=True,
        capture_output=True,
        check=False,
        timeout=e2e_timeout(180),
    )
    assert reported.returncode == 0, reported.stderr
    assert f"killed-owner: round-01 ABANDONED (owner pid {launch.owner} is gone)" in reported.stdout

    launch.release.write_text("go\n", encoding="utf-8")
    recovered = _just(
        "run-plan",
        str(launch.round_dir / "plan.json"),
        "--run",
        "killed-owner",
        *launch.common,
        "--recover",
    )
    assert recovered.returncode == 0, recovered.stderr
    assert _read(launch.round_dir / "result.json")["state"] == "complete"
    assert "ABANDONED" not in _just("runs", "--runs-dir", str(launch.runs)).stdout
