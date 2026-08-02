"""A recorded round outliving — or visibly failing to outlive — its launching turn.

The orchestrator agent starts a round from inside one harness turn and then surfaces an
update, and ending that turn tears down the turn's child process group. Unprotected,
the executor dies there and leaves `round-NN/status.json` saying `"status": "running"`
under a pid that no longer exists, so every progress surface goes on reporting a dead
run as healthy and waiting on the planner. Both `just run-plan` and `just next-round`
claim rounds that way, so both are driven here.

All the layers run through the real `just` recipes, against real processes and real
signals, with nothing about process liveness faked:

* tearing down the launching turn's whole process group — under every signal that
  teardown sends — leaves the round running, and it settles normally afterwards;
* an exception escaping the round ends it where it is, with its own exit status and its
  traceback, rather than climbing back out of the fork and running the rest of the
  launching process's program a second time in the child — and once a round has been
  claimed, that same exception leaves it recorded and reported as abandoned;
* a catchable signal delivered to the executor itself records the abandonment before
  the process dies, and the round refuses to be re-claimed without `--recover` — which
  in turn refuses a round whose recorded owner is still there to be found;
* an uncatchable SIGKILL — the one death nothing can record — still surfaces as
  abandoned in `just runs` and `just status`, because both derive it from the recorded
  owner's pid rather than from the status string it left behind; and it reaches those
  views in the two shapes that hide it — a run whose earlier round already settled, so
  the ledger has a healthy-looking summary row to correct, and a run whose queued
  planner surface outlived it, so the view would otherwise say a dead run is waiting on
  the planner.

The entry point detaches *every* `run-plan` invocation, not only the recorded ones, so
`--no-record` is driven here too. That path claims no round and writes no ledger, which
removes every surface the journeys above assert against: its owner is knowable only
from the pid the entry point announces, its result reaches the caller only as stdout,
and a death reaches the caller only as an exit status. Those three are what detaching
changed for it, so those three are what it is held to.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from rendezvous import Rendezvous
from run_rows import without_ownership
from waits import deadline as e2e_deadline
from waits import timeout as e2e_timeout

from orchestrator import REPO_ROOT
from orchestrator.channel import claim_heartbeat, create_channel, mark_heartbeat_due
from orchestrator.coordination import advisory_lock
from orchestrator.detach import CRASHED
from orchestrator.runs import TEARDOWN_SIGNALS
from orchestrator.watchdog import ProcessId, process_group_is_running


def _just(*args: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["just", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=e2e_timeout(180),
    )
    if args[:1] != ("runs",):
        return completed
    # Every `just runs` row names the session that launched it, and which session
    # that is depends on who runs the suite. The rows asserted below are about
    # abandonment, so the column is dropped here; tests/e2e/test_run_ownership_e2e.py
    # asserts what it says.
    return subprocess.CompletedProcess(
        completed.args,
        completed.returncode,
        without_ownership(completed.stdout),
        completed.stderr,
    )


def _read(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _status(runs: Path, history: Path) -> subprocess.CompletedProcess[str]:
    """`just status` against one ledger, with no dispatch history to report."""
    history.mkdir(exist_ok=True)
    return subprocess.run(
        ["just", "status", "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEHARNESS_HISTORY_DIR": str(history)},
        text=True,
        capture_output=True,
        check=False,
        timeout=e2e_timeout(180),
    )


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


ANNOUNCED_OWNER = re.compile(r"round owner pid (\d+) leads its own session")


def _await_announced_owner(err: Path) -> int:
    """The pid the detaching entry point announced as a round's own.

    A round that has claimed nothing yet — or that never will — has this announcement
    as the only place its pid is written down, which is why the entry point prints it
    and why an operator has nothing else to signal it by.
    """
    deadline = e2e_deadline(30)
    while time.monotonic() < deadline:
        announced = ANNOUNCED_OWNER.search(err.read_text(encoding="utf-8", errors="replace"))
        if announced is not None:
            return int(announced[1])
        time.sleep(0.01)
    pytest.fail(f"no round owner was announced in {err}")


class Launch:
    """One launched round: its launcher process, its owner, and its two streams."""

    def __init__(
        self,
        process: subprocess.Popen[str],
        streams: tuple[Path, Path],
        release: Path,
        common: list[str],
    ) -> None:
        self.process = process
        self.out, self.err = streams
        self.release = release
        self.common = common
        self.owner = self._owner()

    def _owner(self) -> int:
        return _await_announced_owner(self.err)

    def teardown_launching_turn(self, teardown: signal.Signals = signal.SIGTERM) -> int:
        """Kill the launcher's whole process group, as ending a turn does."""
        group = os.getpgid(self.process.pid)
        os.killpg(group, teardown)
        self.process.wait(timeout=e2e_timeout(15))
        return group


class RecordedLaunch(Launch):
    """A launched round that also claimed a round directory in a ledger."""

    def __init__(
        self,
        process: subprocess.Popen[str],
        streams: tuple[Path, Path],
        round_dir: Path,
        release: Path,
        common: list[str],
    ) -> None:
        self.round_dir = round_dir
        super().__init__(process, streams, release, common)

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
            "task": f"complete-now{self.hold(run_id).sentinels()}",
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

    def hold(self, run_id: str) -> Rendezvous:
        """The rendezvous this run's one agent node parks its first turn at."""
        return Rendezvous.at(self.tmp_path, run_id)

    def ready(self, run_id: str) -> Path:
        return self.hold(run_id).ready

    def release(self, run_id: str) -> Path:
        return self.hold(run_id).release

    def _start(
        self, run_id: str, number: int, args: tuple[str, ...]
    ) -> tuple[subprocess.Popen[str], tuple[Path, Path]]:
        """Start a round inside its own process group, the way a harness turn does.

        The output goes to files for the same reason the real orchestrator redirects
        it: once the launching turn is gone, nothing is left draining a pipe. They are
        kept apart because the unrecorded journeys read both — the round's result is
        the whole of stdout, and the owner announcement is on stderr.
        """
        streams = (
            self.tmp_path / f"{run_id}-round-{number:02d}.out",
            self.tmp_path / f"{run_id}-round-{number:02d}.err",
        )
        with streams[0].open("wb") as out, streams[1].open("wb") as err:
            process = subprocess.Popen(
                ["just", *args],
                cwd=REPO_ROOT,
                text=True,
                stdout=out,
                stderr=err,
                start_new_session=True,
            )
        _await(self.ready(run_id), "the dispatched worker")
        return process, streams

    def spawn(self, run_id: str, number: int, *args: str) -> RecordedLaunch:
        process, streams = self._start(run_id, number, args)
        launch = RecordedLaunch(
            process,
            streams,
            self.runs / run_id / f"round-{number:02d}",
            self.release(run_id),
            self.common,
        )
        self.launched.append(launch)
        return launch

    def run_plan(self, run_id: str, recipe: str = "run-plan") -> RecordedLaunch:
        plan = self.plan(run_id)
        return self.spawn(run_id, 1, recipe, str(plan), "--run", run_id, *self.common)

    def unrecorded(self, run_id: str) -> Launch:
        """Launch the `--no-record` path, which claims no round and writes no ledger."""
        args = ("run-plan", str(self.plan(run_id)), "--no-record", *self.common)
        process, streams = self._start(run_id, 1, args)
        launch = Launch(process, streams, self.release(run_id), self.common)
        self.launched.append(launch)
        return launch

    def waiting_round(self, run_id: str) -> subprocess.CompletedProcess[str]:
        """Settle a first round on a human action, so `next-round` has work to do."""
        return _just(
            "run-plan", str(self.plan(run_id, human_gate=True)), "--run", run_id, *self.common
        )

    def next_round(self, run_id: str) -> RecordedLaunch:
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


def _await_group_exit(group: int) -> bool:
    """Wait for a torn-down process group to finish emptying, and say whether it did.

    A group is empty only once every escalation aimed at it has run its course, so this
    is what "the teardown is over" actually consists of. Zombies do not count, which is
    what `process_group_is_running` is careful about: `just` is waited for by the test
    and the rest by init, so a group holding only uncollected statuses is finished.
    """
    deadline = e2e_deadline(60)
    while process_group_is_running(ProcessId(group)) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not process_group_is_running(ProcessId(group))


def _assert_outlived_the_turn(launch: Launch, group: int) -> None:
    # Waited on rather than slept through, because what is under test is the *absence*
    # of a death: the teardown escalates, with `uv run` forwarding SIGTERM to its own
    # direct child and following it with SIGKILL about two seconds later. Emptying is
    # the fact that ends that escalation, and asserting it is strictly stronger than
    # sleeping past it — a window can expire with the escalation still pending, and it
    # charged all seven of these journeys for the slowest box imaginable every run.
    assert _await_group_exit(group), "the launching turn's own process group never finished dying"
    assert _alive(launch.owner), "the executor died with the turn that launched it"
    # Why it survived, asserted rather than assumed: neither the group teardown nor
    # `uv run`'s forwarding to its own direct child can reach a round that is neither.
    assert os.getsid(launch.owner) == launch.owner, "the executor did not lead its own session"
    assert os.getpgid(launch.owner) != group, "the executor stayed in the launching group"


def _assert_survived_teardown(launch: RecordedLaunch, group: int) -> None:
    _assert_outlived_the_turn(launch, group)
    assert _read(launch.round_dir / "status.json")["status"] == "running"


def _assert_settled(launch: RecordedLaunch) -> None:
    launch.release.write_text("go\n", encoding="utf-8")
    _await(launch.round_dir / "result.json", "the round result")
    _await_exit(launch.owner)
    assert _read(launch.round_dir / "result.json")["state"] == "complete"
    assert _read(launch.round_dir / "status.json")["status"] == "completed"


# Two independent axes of the same journey, deliberately not crossed. The recipe
# decides which entry point detaches — `repo-plan` is the deprecated alias for the same
# executor, so it gets the same protection and the same proof. The signal decides what
# ending a turn actually sends: a harness turn tears its group down with SIGTERM, a
# Ctrl-C sends SIGINT, and a lost terminal sends SIGHUP, all three documented in
# `TEARDOWN_SIGNALS` and none of which may reach a round that left the group.
@pytest.mark.parametrize(
    ("recipe", "teardown"),
    [("run-plan", teardown) for teardown in TEARDOWN_SIGNALS] + [("repo-plan", signal.SIGTERM)],
    ids=lambda value: value if isinstance(value, str) else value.name,
)
def test_a_round_survives_the_teardown_of_its_launching_turn(
    rounds: Rounds, recipe: str, teardown: signal.Signals
) -> None:
    """Ending the launching turn must leave a dispatching round running."""
    run_id = f"survives-{recipe}-{teardown.name}"
    launch = rounds.run_plan(run_id, recipe=recipe)

    _assert_survived_teardown(launch, launch.teardown_launching_turn(teardown))
    _assert_settled(launch)

    listed = _just("runs", "--runs-dir", str(rounds.runs))
    assert listed.returncode == 0, listed.stderr
    assert f"{run_id}  round-01  (1 done)" in listed.stdout
    assert "ABANDONED" not in listed.stdout


def _trivial_plan(tmp_path: Path) -> Path:
    """A plan that parses, for the journeys that never get as far as running it."""
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"tasks": [{"id": "held", "persona": "engineer", "task": "complete-now"}]}),
        encoding="utf-8",
    )
    return plan


def test_a_crashing_round_ends_in_the_fork_rather_than_climbing_back_out_of_it(
    tmp_path: Path,
) -> None:
    """An escaping exception must end the round, not resume the launching program.

    The forked round owns exactly one frame's worth of the stack; everything above it
    belongs to the launching process, which is already running that program in the
    parent. An exception unwinding past the fork runs the rest of it a second time in
    the child — and ends the round as a plain `1`, which is what an unfinished round
    returns, so the caller reads a crash as a round still waiting on a decision.

    Provoked the way an operator provokes it: `--runs-dir` naming something that is not
    a directory. Nothing validates that before `mkdir` reaches it, so the error comes
    straight back out through the round.
    """
    occupied = tmp_path / "runs"
    occupied.write_text("not a directory\n", encoding="utf-8")

    crashed = _just(
        "run-plan", str(_trivial_plan(tmp_path)), "--runs-dir", str(occupied), "--run", "crashing"
    )

    # Not 1: a crash and an unfinished round have to be tellable apart by exit status.
    assert crashed.returncode == CRASHED, crashed.stderr
    # Still diagnosable — the round died, but it said what killed it, exactly once.
    assert "FileExistsError" in crashed.stderr, crashed.stderr
    assert str(occupied) in crashed.stderr, crashed.stderr
    assert crashed.stderr.count("Traceback (most recent call last):") == 1, crashed.stderr
    # The proof that the launching program did not run a second time: the traceback
    # stops inside the round. A frame naming the entry point above the fork would be
    # the child reporting itself from the middle of the launcher's own program.
    assert "in _own_round" in crashed.stderr, crashed.stderr
    assert "in main_cli" not in crashed.stderr, crashed.stderr
    # And one round announced one owner; a second pass would have announced another.
    assert len(ANNOUNCED_OWNER.findall(crashed.stderr)) == 1, crashed.stderr


def test_a_crash_past_the_claim_abandons_the_round_rather_than_leaving_it_running(
    tmp_path: Path,
) -> None:
    """An exception after the claim must leave the round self-evidently dead.

    The guard covers this exit the way it covers a signal, and it is the exit with the
    least warning: no handler runs, no result is written, and the owner is gone before
    anything asks it about the round it took. Unguarded, `round-01/status.json` keeps
    saying `running` under a pid that no longer exists, and `just runs` goes on
    reporting the run as work in flight.

    Provoked from outside the process, like the pre-claim crash above: the run's journal
    path occupied by a directory, which nothing opens until the round is already claimed.
    """
    runs = tmp_path / "runs"
    run_id = "crashing-past-the-claim"
    (runs / run_id / "events.jsonl").mkdir(parents=True)

    crashed = _just(
        "run-plan", str(_trivial_plan(tmp_path)), "--runs-dir", str(runs), "--run", run_id
    )

    assert crashed.returncode == CRASHED, crashed.stderr
    assert "IsADirectoryError" in crashed.stderr, crashed.stderr

    round_dir = runs / run_id / "round-01"
    recorded = _read(round_dir / "status.json")
    assert recorded["status"] == "abandoned"
    assert not (round_dir / "result.json").exists(), "a crashed round recorded a result"
    # Recorded by the process that owned the round, not by the relaying parent that
    # outlived it: the reason names the same pid the round announced as its own.
    assert ANNOUNCED_OWNER.findall(crashed.stderr) == [str(recorded["pid"])], crashed.stderr
    assert recorded["reason"] == f"owner pid {recorded['pid']} stopped without recording a result"

    listed = _just("runs", "--runs-dir", str(runs))
    assert listed.returncode == 0, listed.stderr
    assert (
        f"{run_id}  round-01 ABANDONED (owner pid {recorded['pid']} stopped without "
        f"recording a result); reclaim with: just run-plan {round_dir / 'plan.json'} "
        f"--run {run_id} --runs-dir {runs} --recover"
    ) in listed.stdout, listed.stdout


def test_a_rejected_command_line_reaches_the_caller_as_the_status_it_chose(
    tmp_path: Path,
) -> None:
    """A status the round chose must cross the fork unchanged, not become a crash.

    `argparse` ends a rejected command line by raising, inside the forked round, so it
    arrives where a crash does. Reading the two as one would report every mistyped flag
    as an internal failure — and the usage message that says how to fix it would be the
    thing a reader stopped trusting.
    """
    rejected = _just("run-plan", str(_trivial_plan(tmp_path)), "--bogus-flag")

    assert rejected.returncode == 2, rejected.stderr
    assert "unrecognized arguments: --bogus-flag" in rejected.stderr, rejected.stderr
    assert "Traceback" not in rejected.stderr, rejected.stderr


def test_an_interrupt_before_the_round_claims_anything_reaches_the_caller_as_130(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SIGINT ahead of the round's own handler still owes its caller 128+SIGINT.

    The handler that records an abandonment is installed only once the round has
    claimed the ledger, so everything up to that claim runs under the interpreter's
    default SIGINT handler and arrives at the fork as a `KeyboardInterrupt`. Reporting
    it as a crash would tell an operator that their own Ctrl-C was an internal failure,
    and letting it unwind past the fork would run the launching program a second time
    in the child.

    Held in that window by the contention that produces it: another process owning the
    run's ledger lock parks this round short of the claim, which is where an operator
    watching a stuck launch gives up and interrupts it.
    """
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(tmp_path / "state"))
    run_dir = tmp_path / "runs" / "interrupted"
    run_dir.mkdir(parents=True)
    err = tmp_path / "interrupted.err"
    owner: int | None = None

    with advisory_lock(f"ledger:{run_dir.resolve()}"), err.open("wb") as stream:
        launcher = subprocess.Popen(
            [
                "just",
                "run-plan",
                str(_trivial_plan(tmp_path)),
                "--run",
                run_dir.name,
                "--runs-dir",
                str(run_dir.parent),
            ],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=stream,
            start_new_session=True,
        )
        try:
            owner = _await_announced_owner(err)
            os.kill(owner, signal.SIGINT)
            interrupted = launcher.wait(timeout=e2e_timeout(30))
        finally:
            for pid in (owner, launcher.pid):
                if pid is not None:
                    with contextlib.suppress(PermissionError, ProcessLookupError):
                        os.kill(pid, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                launcher.wait(timeout=e2e_timeout(15))

    assert interrupted == 128 + int(signal.SIGINT)
    reported = err.read_text(encoding="utf-8", errors="replace")
    assert "Traceback" not in reported, reported
    # One announcement: a second would be the launching program running again in the child.
    assert len(ANNOUNCED_OWNER.findall(reported)) == 1, reported
    # That the interrupt really landed in the pre-handler window: the round never got
    # past the lock to claim anything, so there is no status for a guard to abandon.
    assert not (run_dir / "round-01").exists()


def test_next_round_continuation_survives_the_teardown_of_its_launching_turn(
    rounds: Rounds,
) -> None:
    """A continuation claims a round through a second entry point, so prove that too."""
    waiting = rounds.waiting_round("survives-continuation")
    assert waiting.returncode == 1, waiting.stderr
    assert json.loads(waiting.stdout)["state"] == "waiting"

    launch = rounds.next_round("survives-continuation")
    _assert_survived_teardown(launch, launch.teardown_launching_turn())
    _assert_settled(launch)

    listed = _just("runs", "--runs-dir", str(rounds.runs))
    assert listed.returncode == 0, listed.stderr
    assert "survives-continuation  round-02  (1 done)" in listed.stdout


# Driven from the production list rather than a second copy of it: every signal that
# means "the group you were launched in is going away" records which one it was, so a
# signal added there is a journey here.
@pytest.mark.parametrize("teardown", TEARDOWN_SIGNALS)
def test_signalled_executor_records_its_own_abandonment(
    rounds: Rounds, teardown: signal.Signals
) -> None:
    """A catchable signal must never leave `running` behind a dead owner."""
    name = teardown.name
    run_id = f"signalled-{name}"
    launch = rounds.run_plan(run_id)

    os.kill(launch.owner, teardown)
    _await_exit(launch.owner)
    # The launcher waits on the round only to report how it ended, so a signalled
    # round still reaches the caller as the familiar 128+N rather than as a success.
    assert launch.process.wait(timeout=e2e_timeout(15)) == 128 + int(teardown)

    status = _read(launch.round_dir / "status.json")
    assert status["status"] == "abandoned"
    assert status["pid"] == launch.owner
    assert status["reason"] == f"owner pid {launch.owner} took {name}"
    assert not (launch.round_dir / "result.json").exists()

    listed = _just("runs", "--runs-dir", str(rounds.runs))
    assert listed.returncode == 0, listed.stderr
    assert f"! {run_id}  round-01 ABANDONED (owner pid {launch.owner} took {name})" in listed.stdout
    assert "--recover" in listed.stdout

    refused = _just(
        "run-plan", str(launch.round_dir / "plan.json"), "--run", run_id, *launch.common
    )
    assert refused.returncode == 2, refused.stderr
    assert "was abandoned" in refused.stderr
    assert "reclaim it with --recover" in refused.stderr

    # And then the reclaim that refusal points at. A round whose owner *recorded* its
    # own abandonment is the other half of `--recover`: the SIGKILL journey reclaims a
    # round still saying `running` behind a dead pid, and this one reclaims the
    # `abandoned` status the handler wrote. An operator following the printed advice
    # has to land on a finished round, not a second refusal.
    launch.release.write_text("go\n", encoding="utf-8")
    recovered = _just(
        "run-plan",
        str(launch.round_dir / "plan.json"),
        "--run",
        run_id,
        *launch.common,
        "--recover",
    )
    assert recovered.returncode == 0, recovered.stderr
    assert _read(launch.round_dir / "result.json")["state"] == "complete"
    assert _read(launch.round_dir / "status.json")["status"] == "completed"
    assert "ABANDONED" not in _just("runs", "--runs-dir", str(rounds.runs)).stdout


def test_killed_executor_surfaces_as_abandoned_in_runs_and_status(
    tmp_path: Path, rounds: Rounds
) -> None:
    """SIGKILL records nothing, so both views must derive it from the owner's pid.

    The status file the dead owner left is also the untrusted input both views read, so
    it is rewritten here the way anything with write access to the ledger could rewrite
    it: a reason no round would ever record, carrying the escape sequences a terminal
    acts on. Neither view may pass that through to the operator reading it.
    """
    launch = rounds.run_plan("killed-owner")

    os.kill(launch.owner, signal.SIGKILL)
    _await_exit(launch.owner)
    left = _read(launch.round_dir / "status.json")
    assert left["status"] == "running"
    (launch.round_dir / "status.json").write_text(
        json.dumps({**left, "reason": "\x1b[2J\x1b]0;owned\x07"}), encoding="utf-8"
    )

    listed = _just("runs", "--runs-dir", str(rounds.runs))
    assert listed.returncode == 0, listed.stderr
    assert f"! killed-owner  round-01 ABANDONED (owner pid {launch.owner} is gone)" in listed.stdout
    assert f"--run killed-owner --runs-dir {rounds.runs} --recover" in listed.stdout
    assert "\x1b" not in listed.stdout

    reported = _status(rounds.runs, tmp_path / "empty-history")
    assert reported.returncode == 0, reported.stderr
    assert f"killed-owner: round-01 ABANDONED (owner pid {launch.owner} is gone)" in reported.stdout
    assert "\x1b" not in reported.stdout

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


def test_runs_reports_a_dead_round_under_the_summary_of_the_last_settled_one(
    rounds: Rounds,
) -> None:
    """A run with settled history keeps its ledger row, so the death must join it.

    A run that completed a round before the one that died gives `just runs` a summary
    to print, so the abandonment has to reach that existing row rather than only the
    no-history line the earlier journeys take. Getting this wrong is the whole symptom:
    the run reads as `round-01 (1 done)`, exactly like a healthy one, while nothing is
    working on round-02.
    """
    run_id = "abandoned-continuation"
    waiting = rounds.waiting_round(run_id)
    assert waiting.returncode == 1, waiting.stderr
    assert json.loads(waiting.stdout)["state"] == "waiting"

    launch = rounds.next_round(run_id)
    os.kill(launch.owner, signal.SIGKILL)
    _await_exit(launch.owner)

    listed = _just("runs", "--runs-dir", str(rounds.runs))
    assert listed.returncode == 0, listed.stderr
    lines = listed.stdout.splitlines()
    # The summary still names round-01, the newest round that actually settled; the
    # dead round-02 is reported as the line under it, against that row's `!` marker.
    row = next(index for index, line in enumerate(lines) if line.startswith(f"! {run_id}  "))
    assert lines[row].startswith(f"! {run_id}  round-01  (")
    assert lines[row + 1] == (
        f"    round-02 ABANDONED (owner pid {launch.owner} is gone); reclaim with: "
        f"just run-plan {launch.round_dir / 'plan.json'} --run {run_id} "
        f"--runs-dir {rounds.runs} --recover"
    )
    # Once, and attached to the run's own row: a second standalone line would be the
    # same dead round reported twice, which is how a reader loses track of which is
    # the live one.
    assert listed.stdout.count("ABANDONED") == 1


def test_status_reports_a_dead_round_beside_the_surface_it_left_pending(
    tmp_path: Path, rounds: Rounds
) -> None:
    """The stale surface is the misreading, so both lines must appear together.

    The surface a run last queued outlives the round that queued it, so a view that
    reports only the surface says "waiting for planner decision" long after the
    executor is gone and the run reads as waiting on a person rather than dead. Driven
    through the real relay the orchestrator's supervisor side runs — it queues the
    surface and then blocks for a reply that never comes, which is the production
    state exactly.
    """
    run_id = "abandoned-with-surface"
    launch = rounds.run_plan(run_id)
    # llmlint: ignore[tests_mirror_real_usage] What is untested here is only how the
    # channel directory comes into being: `just orchestrate` makes it with this exact
    # call and then launches an orchestrator agent, which would spend real harness
    # turns on every run of this suite. Everything the journey actually asserts —
    # queuing a surface, the round dying under it, and what `just status` then prints —
    # runs through the real `orchestrator-relay-supervisor` and `just status` below.
    channel_dir = create_channel(launch.runs / run_id, heartbeat_interval=10)
    # A check-in this run queued and nobody read, still on disk when it dies. Its own
    # reporting line must not survive the run: both views would otherwise invite a
    # planner to `channel-next` a surface nothing will ever follow up on. Queued
    # through `just channel-surface`, the same command the check-in agent invokes,
    # against a claim taken the way the pacemaker takes one — only the paid agent that
    # would author the message is left out. It goes first because the production guard
    # is real: a check-in is refused while a planner surface awaits a reply, and the
    # relay below raises one that never gets answered.
    due_at = float(json.loads((channel_dir / "heartbeat.json").read_text())["last_surface_at"])
    mark_heartbeat_due(channel_dir, now=due_at + 11)
    assert claim_heartbeat(channel_dir), "the pacemaker had no check-in to claim"
    queued = subprocess.run(
        [
            "just",
            "channel-surface",
            run_id,
            "worker: still verifying",
            "--runs-dir",
            str(rounds.runs),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=e2e_timeout(180),
    )
    assert queued.returncode == 0, queued.stderr
    assert (channel_dir / "heartbeat-surface.json").is_file()
    relay = subprocess.Popen(
        [
            "uv",
            "run",
            "orchestrator-relay-supervisor",
            str(channel_dir),
            run_id,
            "1",
            "--timeout",
            str(e2e_timeout(600)),
        ],
        cwd=REPO_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )
    try:
        assert relay.stdin is not None
        relay.stdin.write(json.dumps({"op": "supervisor", "kind": "blocker", "message": "held"}))
        relay.stdin.close()
        _await(channel_dir / "planner-pending.json", "the queued planner surface")

        os.kill(launch.owner, signal.SIGKILL)
        _await_exit(launch.owner)

        reported = _status(rounds.runs, tmp_path / "empty-history")
        listed = subprocess.run(
            ["just", "runs", "--runs-dir", str(rounds.runs)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=e2e_timeout(180),
        )
    finally:
        with contextlib.suppress(PermissionError, ProcessLookupError):
            os.killpg(os.getpgid(relay.pid), signal.SIGKILL)
        relay.wait(timeout=e2e_timeout(15))
    assert reported.returncode == 0, reported.stderr
    lines = reported.stdout.splitlines()
    dead = (
        f"{run_id}: round-01 ABANDONED (owner pid {launch.owner} is gone); reclaim with: "
        f"just run-plan {launch.round_dir / 'plan.json'} --run {run_id} "
        f"--runs-dir {rounds.runs} --recover"
    )
    stale = f"{run_id}: waiting for planner decision: blocker: held"
    # Both, and the death first: the surface is still genuinely queued, so hiding it
    # would lose the reason this run is stuck, but reading it before the abandonment
    # is what made a dead run look like live work waiting on the planner.
    assert dead in lines, reported.stdout
    assert stale in lines, reported.stdout
    assert lines.index(dead) + 1 == lines.index(stale), reported.stdout

    # The queued check-in is still on disk, and neither view offers to read it: a run
    # that stopped keeps the line saying why, not an invitation to supervise it. The
    # two views are read interchangeably, so they must not disagree about this.
    assert (channel_dir / "heartbeat-surface.json").is_file()
    assert listed.returncode == 0, listed.stderr
    assert f"! {run_id}" in listed.stdout, listed.stdout
    # Asserted as this run's own indicator, never as a substring of the whole view:
    # both commands render text they were not pointed at — a sibling row, the host's
    # dispatch history — and one of them quoting the phrase must not answer for this
    # run. `just runs` names it on an indented continuation of the run's row; `just
    # status` prefixes it with the run id.
    assert not [
        line for line in listed.stdout.splitlines() if line.strip().startswith("1 planner update")
    ], listed.stdout
    assert not [
        line for line in reported.stdout.splitlines() if line.startswith(f"{run_id}: 1 planner ")
    ], reported.stdout


def test_a_recovery_that_refuses_the_journal_abandons_the_round_it_claimed(
    rounds: Rounds,
) -> None:
    """An early exit after claiming a round must not leave it reported as running.

    Not every death is a signal. A recovery attempt that claims the round and then
    refuses its corrupted journal returns before dispatching anything, and that exit
    has to leave the same self-evidently-dead record a signalled one does.
    """
    launch = rounds.run_plan("refused-recovery")
    os.kill(launch.owner, signal.SIGKILL)
    _await_exit(launch.owner)

    events = launch.runs / "refused-recovery" / "events.jsonl"
    with events.open("ab") as stream:
        stream.write(b"{broken}\n")

    refused = _just(
        "run-plan",
        str(launch.round_dir / "plan.json"),
        "--run",
        "refused-recovery",
        *launch.common,
        "--recover",
    )
    assert refused.returncode == 2
    assert "malformed authoritative event" in refused.stderr

    status = _read(launch.round_dir / "status.json")
    assert status["status"] == "abandoned"
    assert status["reason"] == f"owner pid {status['pid']} stopped without recording a result"
    assert status["pid"] != launch.owner, "the refusing recovery did not claim the round"
    assert (
        "refused-recovery  round-01 ABANDONED"
        in _just("runs", "--runs-dir", str(rounds.runs)).stdout
    )


def test_a_recovery_refuses_an_abandoned_label_over_a_still_live_owner(rounds: Rounds) -> None:
    """An `abandoned` label must not hand a working round to a second executor.

    That label is the owner's own report that it stopped, and it is what `--recover`
    acts on — but it is a string in an ordinary file, written a moment before the owner
    dies rather than after. Believed on its own, a reclaim landing in that moment, or
    anything else that can rewrite the file, puts a second executor on a round the
    first is still running: two processes dispatching the same nodes into the same
    ledger. The recorded pid is the part of the record a relabelling cannot fake.
    """
    run_id = "live-owner-relabelled"
    launch = rounds.run_plan(run_id)
    status = launch.round_dir / "status.json"
    claimed = _read(status)
    assert claimed["status"] == "running" and claimed["pid"] == launch.owner
    status.write_text(
        json.dumps({**claimed, "status": "abandoned", "reason": "relabelled"}), encoding="utf-8"
    )

    refused = _just(
        "run-plan",
        str(launch.round_dir / "plan.json"),
        "--run",
        run_id,
        *launch.common,
        "--recover",
    )

    assert refused.returncode == 2, refused.stderr
    assert "the recorded owner may still be alive; recovery refused" in refused.stderr
    assert _alive(launch.owner), "the refused recovery took the round out from under its owner"
    # And it left the claim alone rather than stamping itself in as the new owner.
    assert _read(status)["pid"] == launch.owner
    assert not (launch.round_dir / "result.json").exists()


def test_an_unrecorded_round_outlives_its_launching_turn_and_still_reports(
    rounds: Rounds,
) -> None:
    """`--no-record` detaches too, so its result must still find the caller.

    Detaching moved this round two processes away from the caller: it is no longer the
    launcher, and it no longer even shares the launcher's session. With no ledger to
    fall back on, everything the caller learns about it now has to survive that trip —
    here the pid to signal it by and the result it prints, and in the next journey the
    status it exits with.
    """
    launch = rounds.unrecorded("unrecorded-survives")

    _assert_outlived_the_turn(launch, launch.teardown_launching_turn())
    # The launcher is already gone; the round finishes with nothing left waiting on it,
    # which is the whole point of detaching it.
    launch.release.write_text("go\n", encoding="utf-8")
    _await_exit(launch.owner)

    # `--format json` makes stdout the round's entire result, so it parses only if it
    # arrived exactly once: forking a process with a buffered stream is how a payload
    # gets written twice, and two concatenated objects are not JSON.
    payload = json.loads(launch.out.read_text(encoding="utf-8"))
    assert payload["state"] == "complete"
    assert payload["results"]["held"]["status"] == "done"
    # Announced by the round itself and only there: the relaying parent must not print
    # a second, misleading pid for an operator to signal.
    assert len(ANNOUNCED_OWNER.findall(launch.err.read_text(encoding="utf-8"))) == 1
    assert launch.owner != launch.process.pid
    # Unrecorded means unrecorded, detached or not: no ledger appeared to be abandoned.
    assert not rounds.runs.exists(), "the unrecorded round wrote a ledger"


def test_a_signalled_unrecorded_round_reaches_its_caller_as_the_signal_it_took(
    rounds: Rounds,
) -> None:
    """With no round to abandon, the relayed exit status is the only surface left.

    The recorded path answers a killed executor with an ABANDONED ledger row. This one
    has nowhere to write that, so if the relay swallowed the death — reporting the
    launcher's own clean exit instead — the caller would read a signalled round as a
    successful one and never learn otherwise.
    """
    launch = rounds.unrecorded("unrecorded-signalled")

    os.kill(launch.owner, signal.SIGTERM)
    _await_exit(launch.owner)

    assert launch.process.wait(timeout=e2e_timeout(15)) == 128 + int(signal.SIGTERM)
    assert launch.out.read_text(encoding="utf-8") == "", "a killed round reported a result"
    assert not rounds.runs.exists(), "the unrecorded round wrote a ledger"
