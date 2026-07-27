"""A recorded round outliving — or visibly failing to outlive — its launching turn.

The failure these journeys pin down destroyed two real runs. The orchestrator agent
started a round from inside one harness turn, surfaced an update, and ending that turn
tore down the turn's child process group: the executor took SIGTERM about a minute
after dispatching a worker onto a branch, and left `round-NN/status.json` saying
`"status": "running"` under a pid that no longer existed. Every progress surface kept
reporting the dead run as healthy and waiting on the planner, for hours. It happened
once under `just run-plan` and once under `just next-round`, so both are driven here.

All the layers run through the real `just` recipes, against real processes and real
signals, with nothing about process liveness faked:

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


class Launch:
    """One launched round: its launcher process, its ledger, and its owner."""

    def __init__(
        self,
        process: subprocess.Popen[str],
        round_dir: Path,
        release: Path,
        common: list[str],
    ) -> None:
        self.process = process
        self.round_dir = round_dir
        self.release = release
        self.common = common
        self.owner = self._owner()

    @property
    def runs(self) -> Path:
        return self.round_dir.parent.parent

    def _owner(self) -> int:
        """The pid the executor recorded when it claimed the round."""
        deadline = e2e_deadline(30)
        while time.monotonic() < deadline:
            with contextlib.suppress(OSError, ValueError, KeyError):
                recorded = _read(self.round_dir / "status.json")
                if recorded["status"] == "running":
                    return int(str(recorded["pid"]))
            time.sleep(0.01)
        pytest.fail(f"no running round owner was recorded under {self.round_dir}")

    def teardown_launching_turn(self) -> int:
        """Kill the launcher's whole process group, as ending a turn does."""
        group = os.getpgid(self.process.pid)
        os.killpg(group, signal.SIGTERM)
        self.process.wait(timeout=e2e_timeout(15))
        return group


class Rounds:
    """Launch rounds against one ledger and reap whatever they leave behind."""

    def __init__(self, tmp_path: Path, base: Path, onejudge_bin: str) -> None:
        self.tmp_path = tmp_path
        self.runs = tmp_path / "runs"
        self.common = [
            "--runs-dir",
            str(self.runs),
            "--base",
            str(base),
            "--onejudge-bin",
            onejudge_bin,
            "--format",
            "json",
        ]
        self.launched: list[Launch] = []

    def plan(self, run_id: str, *, human_gate: bool = False) -> Path:
        """A plan whose one agent node parks in the real provider until released."""
        held: dict[str, object] = {
            "id": "held",
            "persona": "engineer",
            "task": (
                "complete-now "
                f"provider-barrier-ready={self.ready(run_id)} "
                f"provider-barrier-release={self.release(run_id)}"
            ),
        }
        tasks: list[dict[str, object]] = [held]
        if human_gate:
            held["deps"] = ["gate"]
            tasks.insert(
                0,
                {
                    "id": "gate",
                    "kind": "human",
                    "task": "Attest the release before the held node runs.",
                },
            )
        path = self.tmp_path / f"{run_id}.json"
        path.write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
        return path

    def ready(self, run_id: str) -> Path:
        return self.tmp_path / f"{run_id}.ready"

    def release(self, run_id: str) -> Path:
        return self.tmp_path / f"{run_id}.release"

    def spawn(self, run_id: str, number: int, *args: str) -> Launch:
        """Start a round inside its own process group, the way a harness turn does.

        The output goes to a file for the same reason the real orchestrator redirects
        it: once the launching turn is gone, nothing is left draining a pipe.
        """
        log = (self.tmp_path / f"{run_id}-round-{number:02d}.log").open("wb")
        process = subprocess.Popen(
            ["just", *args],
            cwd=REPO_ROOT,
            text=True,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        _await(self.ready(run_id), "the dispatched worker")
        launch = Launch(
            process,
            self.runs / run_id / f"round-{number:02d}",
            self.release(run_id),
            self.common,
        )
        self.launched.append(launch)
        return launch

    def run_plan(self, run_id: str) -> Launch:
        plan = self.plan(run_id)
        return self.spawn(run_id, 1, "run-plan", str(plan), "--run", run_id, *self.common)

    def waiting_round(self, run_id: str) -> subprocess.CompletedProcess[str]:
        """Settle a first round on a human action, so `next-round` has work to do."""
        return _just(
            "run-plan", str(self.plan(run_id, human_gate=True)), "--run", run_id, *self.common
        )

    def next_round(self, run_id: str) -> Launch:
        return self.spawn(run_id, 2, "next-round", run_id, "--complete-human", "gate", *self.common)

    def reap(self) -> None:
        for launch in self.launched:
            launch.release.write_text("go\n", encoding="utf-8")
            for pid in (launch.owner, launch.process.pid):
                with contextlib.suppress(PermissionError, ProcessLookupError):
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                launch.process.wait(timeout=e2e_timeout(15))


@pytest.fixture
def rounds(
    tmp_path: Path, command_base: Callable[..., Path], onejudge_bin: str
) -> Iterator[Rounds]:
    launcher = Rounds(tmp_path, command_base(), onejudge_bin)
    yield launcher
    launcher.reap()


def _assert_survived_teardown(launch: Launch, group: int) -> None:
    # An explicit settle window, because what is under test is the *absence* of a
    # death: the teardown escalates, with `uv run` forwarding SIGTERM to its own direct
    # child and following it with SIGKILL about two seconds later.
    time.sleep(e2e_timeout(2.0))
    assert _alive(launch.owner), "the executor died with the turn that launched it"
    assert _read(launch.round_dir / "status.json")["status"] == "running"
    # Why it survived, asserted rather than assumed: neither the group teardown nor
    # `uv run`'s forwarding to its own direct child can reach a round that is neither.
    assert os.getsid(launch.owner) == launch.owner, "the executor did not lead its own session"
    assert os.getpgid(launch.owner) != group, "the executor stayed in the launching group"


def _assert_settled(launch: Launch) -> None:
    launch.release.write_text("go\n", encoding="utf-8")
    _await(launch.round_dir / "result.json", "the round result")
    _await_exit(launch.owner)
    assert _read(launch.round_dir / "result.json")["state"] == "complete"
    assert _read(launch.round_dir / "status.json")["status"] == "completed"


def test_run_plan_round_survives_the_teardown_of_its_launching_turn(rounds: Rounds) -> None:
    """Ending the launching turn must leave a dispatching round running."""
    launch = rounds.run_plan("survives-teardown")

    _assert_survived_teardown(launch, launch.teardown_launching_turn())
    _assert_settled(launch)

    listed = _just("runs", "--runs-dir", str(rounds.runs))
    assert listed.returncode == 0, listed.stderr
    assert "survives-teardown  round-01  (1 done)" in listed.stdout
    assert "ABANDONED" not in listed.stdout


def test_next_round_continuation_survives_the_teardown_of_its_launching_turn(
    rounds: Rounds,
) -> None:
    """The second observed failure was a `next-round` continuation, so prove that too."""
    waiting = rounds.waiting_round("survives-continuation")
    assert waiting.returncode == 1, waiting.stderr
    assert json.loads(waiting.stdout)["state"] == "waiting"

    launch = rounds.next_round("survives-continuation")
    _assert_survived_teardown(launch, launch.teardown_launching_turn())
    _assert_settled(launch)

    listed = _just("runs", "--runs-dir", str(rounds.runs))
    assert listed.returncode == 0, listed.stderr
    assert "survives-continuation  round-02  (1 done)" in listed.stdout


def test_signalled_executor_records_its_own_abandonment(rounds: Rounds) -> None:
    """A catchable signal must never leave `running` behind a dead owner."""
    launch = rounds.run_plan("signalled-owner")

    os.kill(launch.owner, signal.SIGTERM)
    _await_exit(launch.owner)

    status = _read(launch.round_dir / "status.json")
    assert status["status"] == "abandoned"
    assert status["pid"] == launch.owner
    assert "SIGTERM" in str(status["reason"])
    assert not (launch.round_dir / "result.json").exists()

    listed = _just("runs", "--runs-dir", str(rounds.runs))
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
    tmp_path: Path, rounds: Rounds
) -> None:
    """SIGKILL records nothing, so both views must derive it from the owner's pid."""
    launch = rounds.run_plan("killed-owner")

    os.kill(launch.owner, signal.SIGKILL)
    _await_exit(launch.owner)
    assert _read(launch.round_dir / "status.json")["status"] == "running"

    listed = _just("runs", "--runs-dir", str(rounds.runs))
    assert listed.returncode == 0, listed.stderr
    assert f"! killed-owner  round-01 ABANDONED (owner pid {launch.owner} is gone)" in listed.stdout
    assert f"--run killed-owner --runs-dir {rounds.runs} --recover" in listed.stdout

    history = tmp_path / "empty-history"
    history.mkdir()
    reported = subprocess.run(
        ["just", "status", "--runs-dir", str(rounds.runs)],
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
    assert "ABANDONED" not in _just("runs", "--runs-dir", str(rounds.runs)).stdout
