"""The pre-push hook's gate-time step, driven end to end with a stand-in gate.

`.githooks/pre-push` runs `gate_within_budgets` (`scripts/gate-budgets.sh`) over the
complete gate and the root `budgets.yaml`: the gate is timed once by
`record_local_direct_gate`, which records that duration against this push with the host
conditions it sampled while the gate ran, and the installed onebudgetspec then checks the
budgets as two selections — the `host-variable` budgets, whose over result warns, and
every other budget, whose over result refuses — its `gate-time` command reading the
recording back. These journeys drive
that function as the hook calls it — the real scripts, the real `just host` view the
dispatch count is read from, and the installed onebudgetspec — with a stand-in gate of
known duration in place of `just gate`, the way `tests/e2e/test_lock_timeout_bound_e2e.py`
drives `record_local_direct_gate`. The view is pointed at a runs root holding a known
number of live dispatches, built by `tests/e2e/probe_run_root.py`, and the load average
at a file the stand-in gate rewrites as it runs.

The budgets file each journey checks is the tracked root file, read and written to the
journey's own directory with its commands made absolute — commands run from the file's
directory — and, where a journey says so, one field changed or one budget added. The
threshold every within-budget journey is held to is the one the tracked file states.

llmlint: ignore-file[e2e_not_mocked] The one substitution is the gate itself: a second
test that ran `just gate` is what ai-orchestrator#1164 rejects, and the step sees a gate
only as the command the hook hands it, so a command of known duration is that interface
driven for real. The runs root and the load-average file are inputs the real sampler and
the real view read, written where each journey can say what they held.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] What these spend is a
few seconds of a stand-in gate apiece and the engine's `host` view over a runs root of a
handful of records — no launch and no paid turn — and what they read is
`scripts/gate-budgets.sh`, `scripts/lock-timeout.sh`, the two budget scripts and
`budgets.yaml`, which the code tier's key already covers.

llmlint: ignore-file[shell_test_tiers_stay_split] For the same cost: these journeys spend
seconds, against nothing outside this checkout, so a test project of their own would
narrow the key of a few seconds of work at the price of a catalog entry in each of the
places `tests/nx_inputs.py` and `tests/test_nx_cache_scope.py` hold every project to.

llmlint: ignore-file[tests_mirror_real_usage] Two departures, each forced. The journeys
call `gate_within_budgets`, the hook's step, rather than `.githooks/pre-push` whole,
because the hook's own line runs `just gate`, the complete gate a second test may not run
(ai-orchestrator#1164); the hook line naming the step is what every publication runs. And
the live dispatches are records `tests/e2e/probe_run_root.py` builds over processes the
journey starts and ends, for the reason that builder gives: a launch per journey would
spend a whole run to give the view one row, and `tests/test_engine_contracts.py`
reconciles every field it writes against the installed engine.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml
from probe_run_root import run_name, run_root
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

STEP = REPO_ROOT / "scripts" / "gate-budgets.sh"
ROOT_BUDGETS = REPO_ROOT / "budgets.yaml"
#: The label every budget measured from onepipeline's telemetry carries, which only this
#: host's run-success hook selects.
ONEPIPELINE_LABEL = "onepipeline"
#: The label of a budget whose measurement varies with this shared host's load, which the
#: hook warns about rather than refuses when over.
HOST_VARIABLE_LABEL = "host-variable"
#: How often the journeys sample the host: often enough that each value a stand-in gate
#: holds for a second or more is read.
SAMPLE_SECONDS = "0.2"


def _tracked_budgets() -> dict:
    return yaml.safe_load(ROOT_BUDGETS.read_text(encoding="utf-8"))


def _tracked_gate_time() -> dict:
    (budget,) = [entry for entry in _tracked_budgets()["budgets"] if entry["id"] == "gate-time"]
    return budget


def _absolute(command: list[str]) -> list[str]:
    """A command from the tracked file, made to run from any directory."""
    first, *rest = command
    return [str(REPO_ROOT / first) if "/" in first else first, *rest]


@dataclass(frozen=True)
class Host:
    """What one journey's host looks like to the sampler and to the check."""

    root: Path
    runs: Path
    loadavg: Path
    state: Path

    def environment(self) -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            # A push the enclosing process names, or a sampling cadence it set, is not
            # this journey's: the step mints its own push and the journey its cadence.
            if key not in {"ORCHESTRATOR_GATE_PUSH", "ORCHESTRATOR_GATE_SAMPLE_SECONDS"}
        }
        environment.update(
            {
                "XDG_STATE_HOME": str(self.state),
                "ONEPIPELINE_RUNS_DIR": str(self.runs),
                "ORCHESTRATOR_GATE_LOADAVG": str(self.loadavg),
                "ORCHESTRATOR_GATE_SAMPLE_SECONDS": SAMPLE_SECONDS,
            }
        )
        return environment

    @property
    def record(self) -> Path:
        return self.state / "ai-orchestrator" / "local-direct-gate-push"

    @property
    def gate_runs(self) -> Path:
        return self.root / "gate-runs"

    def budgets(self, *, threshold: float | None = None, extra: list[dict] | None = None) -> Path:
        """The tracked root file, written here with absolute commands."""
        document = _tracked_budgets()
        for condition in document.get("conditions", []):
            condition["command"] = _absolute(condition["command"])
        for budget in document["budgets"]:
            budget["command"] = _absolute(budget["command"])
            if threshold is not None and budget["id"] == "gate-time":
                budget["threshold"] = threshold
        document["budgets"].extend(extra or [])
        path = self.root / "budgets.yaml"
        path.write_text(yaml.safe_dump(document), encoding="utf-8")
        return path

    def gate(self, body: str, *, status: int = 0) -> Path:
        """A stand-in gate: it notes that it ran, runs ``body``, and exits ``status``."""
        script = self.root / "stand-in-gate"
        script.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            f"echo ran >>{shlex.quote(str(self.gate_runs))}\n"
            f"{body}\nexit {status}\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        return script

    def load(self, value: str) -> str:
        """A shell line making the load-average source answer ``value``."""
        return f"echo '{value} 1.00 1.00 1/100 1' >{shlex.quote(str(self.loadavg))}"


@dataclass(frozen=True)
class Pushed:
    """What one run of the step answered."""

    returncode: int
    stdout: str
    stderr: str

    def results(self, budget: str = "gate-time") -> list[str]:
        return [line for line in self.stdout.splitlines() if line.startswith(f"budget {budget}:")]

    def result(self, budget: str = "gate-time") -> str:
        (line,) = self.results(budget)
        return line

    def conditions(self, budget: str = "gate-time") -> dict[str, str]:
        """The host conditions the result line names, by name."""
        _, _, host = self.result(budget).partition("; host: ")
        pairs = (item.partition("=") for item in host.split())
        return {name: value for name, _, value in pairs}


@pytest.fixture
def host(tmp_path: Path) -> Iterator[Host]:
    """A host of this journey's own: a runs root, a load-average file and a state root."""
    built = Host(
        root=tmp_path,
        runs=tmp_path / "runs",
        loadavg=tmp_path / "loadavg",
        state=tmp_path / "state",
    )
    built.runs.mkdir()
    built.state.mkdir()
    built.loadavg.write_text("0.10 0.10 0.10 1/100 1\n", encoding="utf-8")
    yield built
    # A journey that made a directory unreadable or unwritable gives it back, so the
    # temporary tree can be removed.
    for directory in (built.runs, built.state / "ai-orchestrator"):
        if directory.is_dir():
            directory.chmod(0o755)


def _detached() -> int:
    """Start a process no journey process is the parent of, and return its pid.

    A dispatch outlives the driver that started it, and a process that ends is then reaped
    by whatever adopted it; a child of this test would instead linger as a zombie the view
    could still read as alive.
    """
    started = subprocess.run(
        ["bash", "-c", "sleep 600 >/dev/null 2>&1 & echo $!"],
        capture_output=True,
        text=True,
        check=True,
        timeout=e2e_timeout(30),
    )
    return int(started.stdout)


@pytest.fixture
def dispatches(host: Host) -> Iterator[list[int]]:
    """Three live dispatches on this journey's host, each in a run of its own.

    Each is a process this journey started, by pid, and ends, recorded in the dispatch
    registry the engine's `host` view reads; the pids are returned so a stand-in gate can
    end some of them.
    """
    pids = [_detached() for _ in range(3)]
    try:
        for pid in pids:
            run_root(host.runs, run_name(), dispatch_pid=pid)
        yield pids
    finally:
        for pid in pids:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


def _push(
    host: Host, budgets: Path, gate: Path, environment: dict[str, str] | None = None
) -> Pushed:
    """Run the hook's step as the hook runs it, with the stand-in gate.

    ``environment`` is added over the journey's host, for the journeys about what the
    step makes of a variable it is handed.
    """
    ran = subprocess.run(
        ["bash", "-c", f'. "{STEP}"\ngate_within_budgets "$1" "$2"', "step", budgets, gate],
        cwd=REPO_ROOT,
        env={**host.environment(), **(environment or {})},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )
    return Pushed(ran.returncode, ran.stdout, ran.stderr)


@dataclass(frozen=True)
class Drained:
    """What a pipe held when it was read without waiting, and whether that was all."""

    text: str
    #: No process held the pipe's write end any longer when it was read.
    at_its_end: bool


@dataclass(frozen=True)
class PushedToItsEnd:
    """One run of the step, and whether its output had ended by the time it exited."""

    pushed: Pushed
    output_ended: bool


def _drained(descriptor: int) -> Drained:
    """Everything already written to a pipe, and whether it is at its end.

    Read without blocking, so what is answered is the pipe's state now: at its end means
    no process holds its write end any longer, and a read that would block means one
    still does.
    """
    os.set_blocking(descriptor, False)
    chunks: list[bytes] = []
    while True:
        try:
            chunk = os.read(descriptor, 65536)
        except BlockingIOError:
            return Drained(b"".join(chunks).decode(), at_its_end=False)
        if not chunk:
            return Drained(b"".join(chunks).decode(), at_its_end=True)
        chunks.append(chunk)


def _push_to_its_end(
    host: Host, budgets: Path, gate: Path, environment: dict[str, str] | None = None
) -> PushedToItsEnd:
    """Run the step as `_push` does, and read whether its output ended when it did.

    The step's own exit is waited on, never its output, and the output is then read at
    once: a sampler left holding it — a reading still waiting on its source, a nap still
    running — is a process still holding the write end at that moment, and nothing about
    how long the step took enters into it. Its output fits a pipe's buffer, so the step
    never waits on this reader to finish writing it.
    """
    process = subprocess.Popen(
        ["bash", "-c", f'. "{STEP}"\ngate_within_budgets "$1" "$2"', "step", budgets, gate],
        cwd=REPO_ROOT,
        env={**host.environment(), **(environment or {})},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None and process.stderr is not None
    try:
        try:
            process.wait(timeout=e2e_timeout(180))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
        stdout = _drained(process.stdout.fileno())
        stderr = _drained(process.stderr.fileno())
    finally:
        process.stdout.close()
        process.stderr.close()
    return PushedToItsEnd(
        Pushed(process.returncode, stdout.text, stderr.text),
        output_ended=stdout.at_its_end and stderr.at_its_end,
    )


def _ran_once(host: Host, pushed: Pushed) -> None:
    """The gate ran once and `gate-time` was measured once."""
    assert host.gate_runs.read_text(encoding="utf-8").splitlines() == ["ran"], pushed.stderr
    assert len(pushed.results()) == 1, pushed.stdout + pushed.stderr


def _format(number: float) -> str:
    """A number as onebudgetspec's result line prints it."""
    return f"{number:g}"


def test_a_gate_within_budget_prints_its_result_and_is_admitted(
    host: Host, dispatches: list[int]
) -> None:
    """Actual, budget, headroom, `within`, and the host conditions, the gate's own included.

    The budget is the tracked file's threshold, read from it rather than restated, and
    an `onepipeline`-labelled budget the journey places in the file is never run.
    """
    threshold = float(_tracked_gate_time()["threshold"])
    telemetry_ran = host.root / "telemetry-ran"
    budgets = host.budgets(
        extra=[
            {
                "id": "cycle-time-probe",
                "labels": [ONEPIPELINE_LABEL],
                "measure": "elapsed",
                "command": ["touch", str(telemetry_ran)],
                "unit": "seconds",
                "direction": "max",
                "threshold": 1,
            }
        ]
    )

    pushed = _push(host, budgets, host.gate("sleep 3"))

    assert pushed.returncode == 0, pushed.stdout + pushed.stderr
    _ran_once(host, pushed)
    line = pushed.result()
    seconds = int(host.record.read_text(encoding="utf-8").split("seconds=")[1].split()[0])
    assert seconds >= 3, host.record.read_text(encoding="utf-8")
    assert f"actual {seconds} seconds" in line, line
    assert f"budget {_format(threshold)} seconds" in line, line
    assert f"headroom {_format(threshold - seconds)} seconds" in line, line
    assert "— within" in line, line
    conditions = pushed.conditions()
    assert conditions["dispatches"] == "3", line
    assert conditions["dispatches_max"] == "3", line
    assert conditions["load1_max"] == "0.10", line
    assert pushed.results("cycle-time-probe") == [], pushed.stdout
    assert not telemetry_ran.exists(), "an onepipeline-labelled budget was run"


def test_the_dispatch_count_during_the_gate_is_reported_beside_the_count_after_it(
    host: Host, dispatches: list[int]
) -> None:
    """Three dispatches while the gate ran, one once it had finished.

    Two of the dispatches end as the gate does. The file's own `dispatches` condition is
    sampled when the check runs, after the gate, so it reads one; `dispatches_max` is the
    peak of what the hook sampled while the gate ran, three. A command that sampled only
    after the gate would report one for both.
    """
    ended = " ".join(str(pid) for pid in dispatches[1:])
    gate = host.gate(f"sleep 4\nkill {ended}\nsleep 0.5")

    pushed = _push(host, host.budgets(), gate)

    assert pushed.returncode == 0, pushed.stdout + pushed.stderr
    _ran_once(host, pushed)
    conditions = pushed.conditions()
    assert conditions["dispatches_max"] == "3", pushed.result()
    assert conditions["dispatches"] == "1", pushed.result()


def test_the_load_peak_is_the_largest_value_read_while_the_gate_ran(host: Host) -> None:
    """Several readable values during the gate, then a lower one after it.

    The peak is the largest the gate held, so neither a constant nor a reading taken
    after the gate could produce it. One stretch of the gate holds no load average at all,
    and those readings are reported as failed beside the peak the others give.
    """
    host.loadavg.write_text("2.50 1.00 1.00 1/100 1\n", encoding="utf-8")
    gate = host.gate(
        "\n".join(
            (
                "sleep 1.5",
                host.load("7.25"),
                "sleep 1.5",
                host.load("unreadable"),
                "sleep 1",
                host.load("4.00"),
                "sleep 1.5",
                host.load("0.50"),
            )
        )
    )

    pushed = _push(host, host.budgets(), gate)

    assert pushed.returncode == 0, pushed.stdout + pushed.stderr
    _ran_once(host, pushed)
    assert pushed.conditions()["load1_max"] == "7.25", pushed.result()
    assert "load1_max sample(s) could not be read while the gate ran" in pushed.stderr
    assert "its peak 7.25 covers only the readings that succeeded" in pushed.stderr
    assert "begins with 'unreadable', which is no load average" in pushed.stderr


@pytest.mark.parametrize("load_source", ["missing", "malformed"])
def test_conditions_no_sample_could_read_are_unknown(host: Host, load_source: str) -> None:
    """A load source that is not there, or holds no load average, and a view that cannot
    read its runs root."""
    if load_source == "missing":
        host.loadavg.unlink()
    else:
        host.loadavg.write_text("not-a-load 1.00 1.00 1/100 1\n", encoding="utf-8")
    host.runs.chmod(0)

    pushed = _push(host, host.budgets(), host.gate("sleep 2"))

    assert pushed.returncode == 0, pushed.stdout + pushed.stderr
    _ran_once(host, pushed)
    conditions = pushed.conditions()
    assert conditions["load1_max"] == "unknown", pushed.result()
    assert conditions["dispatches_max"] == "unknown", pushed.result()
    assert conditions["dispatches"] == "unknown", pushed.result()
    # Each unknown peak is reported with what its last reading said and what repairs it.
    assert "no load1_max sample could be read while the gate ran" in pushed.stderr
    assert f"the load average source {host.loadavg}" in pushed.stderr, pushed.stderr
    assert "no dispatches_max sample could be read while the gate ran" in pushed.stderr
    assert "it could not read the runs root" in pushed.stderr, pushed.stderr


def _strict(identifier: str, command: list[str]) -> dict:
    """A root budget carrying no `host-variable` label, measured as ``command``'s duration."""
    return {
        "id": identifier,
        "measure": "elapsed",
        "command": command,
        "unit": "seconds",
        "direction": "max",
        "threshold": 1,
    }


def test_a_passing_gate_over_its_host_variable_budget_is_admitted_with_a_warning(
    host: Host,
) -> None:
    """Admitted, printing the over line, warning to optimize and saying why it does not block."""
    assert HOST_VARIABLE_LABEL in _tracked_gate_time()["labels"]

    pushed = _push(host, host.budgets(threshold=1), host.gate("sleep 2"))

    assert pushed.returncode == 0, pushed.stdout + pushed.stderr
    _ran_once(host, pushed)
    assert "— over" in pushed.result(), pushed.result()
    assert "dispatches_max=" in pushed.result() and "load1_max=" in pushed.result()
    (warning,) = [line for line in pushed.stderr.splitlines() if "warning:" in line]
    assert "budget gate-time is over" in warning, warning
    assert "should still be optimized" in warning, warning
    assert "host conditions" in warning, warning
    assert "does not block this push" in warning, warning
    assert "shared, variably loaded host is not a consistent measurement" in warning, warning
    assert "consistent system such as a CI runner" in warning, warning
    assert "refused" not in pushed.stderr, pushed.stderr


def test_a_gate_over_a_strict_budget_is_refused_and_told_to_optimize(host: Host) -> None:
    """Refused, naming the budget, saying to optimize, and pointing at the conditions."""
    budgets = host.budgets(extra=[_strict("strict-probe", ["sleep", "1.5"])])

    pushed = _push(host, budgets, host.gate("true"))

    assert pushed.returncode == 1, pushed.stdout + pushed.stderr
    _ran_once(host, pushed)
    assert "— within" in pushed.result(), pushed.result()
    assert "— over" in pushed.result("strict-probe"), pushed.stdout
    assert "budget strict-probe is over" in pushed.stderr, pushed.stderr
    assert "this push is refused" in pushed.stderr and "optimize" in pushed.stderr
    assert "host conditions" in pushed.stderr and "manager" in pushed.stderr, pushed.stderr
    assert "warning:" not in pushed.stderr, pushed.stderr


def test_a_host_variable_over_beside_a_strict_over_is_still_refused(host: Host) -> None:
    """The warning about `gate-time` does not turn the strict budget's refusal into an admission."""
    budgets = host.budgets(threshold=1, extra=[_strict("strict-probe", ["sleep", "1.5"])])

    pushed = _push(host, budgets, host.gate("sleep 2"))

    assert pushed.returncode == 1, pushed.stdout + pushed.stderr
    _ran_once(host, pushed)
    assert "— over" in pushed.result(), pushed.result()
    assert "— over" in pushed.result("strict-probe"), pushed.stdout
    (refusal,) = [line for line in pushed.stderr.splitlines() if "this push is refused" in line]
    assert "budget strict-probe is over" in refusal and "optimize" in refusal, refusal
    assert "gate-time" not in refusal, refusal


def test_a_host_variable_over_beside_a_strict_error_is_refused_naming_the_file(
    host: Host,
) -> None:
    """A strict budget whose command fails is an errored check, whatever `gate-time` says."""
    budgets = host.budgets(threshold=1, extra=[_strict("strict-probe", ["false"])])

    pushed = _push(host, budgets, host.gate("sleep 2"))

    assert pushed.returncode == 1, pushed.stdout + pushed.stderr
    _ran_once(host, pushed)
    assert "— over" in pushed.result(), pushed.result()
    assert pushed.result("strict-probe").startswith("budget strict-probe: error —"), pushed.stdout
    assert f"the budget check of {budgets} errored" in pushed.stderr, pushed.stderr
    assert "warning:" not in pushed.stderr, pushed.stderr


def test_a_failing_gate_still_reports_its_result_and_keeps_its_own_refusal(host: Host) -> None:
    pushed = _push(host, host.budgets(), host.gate("sleep 1", status=7))

    assert pushed.returncode == 7, pushed.stdout + pushed.stderr
    _ran_once(host, pushed)
    assert "— within" in pushed.result(), pushed.result()
    assert "the gate failed (exit 7)" in pushed.stderr, pushed.stderr


def test_a_missing_budgets_file_is_refused_naming_it(host: Host) -> None:
    missing = host.root / "budgets.yaml"

    pushed = _push(host, missing, host.gate("true"))

    assert pushed.returncode != 0, pushed.stdout + pushed.stderr
    assert host.gate_runs.read_text(encoding="utf-8").splitlines() == ["ran"]
    assert pushed.results() == [], pushed.stdout
    assert f"the budget check of {missing} errored" in pushed.stderr, pushed.stderr
    assert "no such file or directory" in pushed.stderr, pushed.stderr


def test_a_duration_recorded_for_an_earlier_push_is_refused_naming_it(host: Host) -> None:
    """A recording that failed leaves the earlier push's record, which is never reported.

    An earlier push records its gate and is admitted. The state directory is then made
    read-only, so the next push's gate cannot be recorded — the case the refusal exists
    for — and what that push finds is the earlier one's record.
    """
    budgets = host.budgets()
    earlier = _push(host, budgets, host.gate("true"))
    assert earlier.returncode == 0, earlier.stdout + earlier.stderr
    earlier_push = host.record.read_text(encoding="utf-8").splitlines()[0].removeprefix("push=")
    host.gate_runs.unlink()
    host.record.parent.chmod(0o555)

    pushed = _push(host, budgets, host.gate("sleep 1"))

    assert pushed.returncode == 1, pushed.stdout + pushed.stderr
    _ran_once(host, pushed)
    assert "could not be recorded for this push" in pushed.stderr, pushed.stderr
    assert pushed.result().startswith("budget gate-time: error —"), pushed.result()
    assert f"for an earlier push ({earlier_push})" in pushed.stderr, pushed.stderr
    assert f"the budget check of {budgets} errored" in pushed.stderr, pushed.stderr


def test_samples_that_cannot_be_written_or_read_are_said_so_and_left_out(host: Host) -> None:
    """The sample directory locked part-way through the gate.

    A sample that can no longer be written ends its loop, saying so, and the peak is taken
    over what was written before: the higher load the gate goes on to hold is not in it. A
    sample file that can no longer be read is a peak of `unknown`. And a sample directory
    that cannot be removed afterwards is named, for removal by hand.
    """
    temporary = host.root / "tmp"
    temporary.mkdir()
    gate = host.gate(
        "\n".join(
            (
                "sleep 1.5",
                'samples=$(echo "$TMPDIR"/tmp.*)',
                'chmod 000 "$samples/dispatches"',
                'chmod 400 "$samples/load1"',
                'chmod 500 "$samples"',
                host.load("9.99"),
                "sleep 1.5",
            )
        )
    )

    try:
        pushed = _push(host, host.budgets(), gate, {"TMPDIR": str(temporary)})
    finally:
        for left in temporary.glob("tmp.*"):
            left.chmod(0o700)
            for sample in left.iterdir():
                sample.chmod(0o600)

    assert pushed.returncode == 0, pushed.stdout + pushed.stderr
    _ran_once(host, pushed)
    assert "a sample could not be written to" in pushed.stderr, pushed.stderr
    conditions = pushed.conditions()
    assert conditions["load1_max"] == "0.10", pushed.result()
    assert conditions["dispatches_max"] == "unknown", pushed.result()
    assert "could not be read back, so their peak is recorded as unknown" in pushed.stderr
    assert "could not be removed; remove that directory by hand" in pushed.stderr, pushed.stderr


def _readers_of(fifo: Path) -> bool:
    """Whether any process holds ``fifo`` open for reading, or waits to.

    Opening a FIFO's write end without blocking succeeds only while it has a reader, and
    that open is what releases a reader still waiting in its own open, so the end is held
    until the reader has finished its line rather than closed under it.
    """
    try:
        descriptor = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
    except OSError:
        return False
    with contextlib.suppress(OSError):
        os.write(descriptor, b"0.00 0.00 0.00 1/100 1\n")
    os.close(descriptor)
    return True


def test_a_sampler_stopped_mid_read_leaves_nothing_reading_or_holding_the_output(
    host: Host,
) -> None:
    """A load-average reading still waiting on its source when the gate ends.

    The source is a FIFO nothing writes, so every reading blocks for as long as the gate
    runs. Stopping the sampler has to end that reading too: one left waiting would hold
    the step's output open, keeping whoever reads it to its end — a publication — waiting
    on a source that may never answer, and would outlive the push it sampled.
    """
    host.loadavg.unlink()
    os.mkfifo(host.loadavg)

    try:
        ran = _push_to_its_end(host, host.budgets(), host.gate("sleep 1.5"))
        left_reading = _readers_of(host.loadavg)
    finally:
        # Each look releases a reader left waiting; the deadline keeps a sampler that
        # goes on reopening the source from holding the journey, and removing the FIFO
        # then turns any later reading into a failed one rather than another wait.
        deadline = time.monotonic() + 10
        while _readers_of(host.loadavg) and time.monotonic() < deadline:
            time.sleep(0.1)
        host.loadavg.unlink()

    pushed = ran.pushed
    assert ran.output_ended, "the step's output was held open after the step exited"
    assert not left_reading, "a load-average reading was left waiting after the gate ended"
    assert pushed.returncode == 0, pushed.stdout + pushed.stderr
    _ran_once(host, pushed)
    assert pushed.conditions()["load1_max"] == "unknown", pushed.result()


def test_a_gate_with_nowhere_to_record_still_removes_its_samples(host: Host) -> None:
    """Neither state root is absolute, so the gate's duration has nowhere to go.

    The push is refused, since its `gate-time` has nothing to read back, and the host
    samples taken while the gate ran are removed rather than left in the temporary
    directory for every such push to add to.
    """
    temporary = host.root / "tmp"
    temporary.mkdir()

    pushed = _push(
        host,
        host.budgets(),
        host.gate("sleep 1"),
        {"TMPDIR": str(temporary), "XDG_STATE_HOME": "", "HOME": "relative-home"},
    )

    assert pushed.returncode == 1, pushed.stdout + pushed.stderr
    _ran_once(host, pushed)
    assert "neither XDG_STATE_HOME nor HOME is an absolute path" in pushed.stderr, pushed.stderr
    assert pushed.result().startswith("budget gate-time: error —"), pushed.result()
    assert list(temporary.glob("tmp.*")) == [], "the gate's host samples were left behind"


def test_an_inherited_push_is_replaced_and_kept_from_the_gate(host: Host) -> None:
    """The step names its own push, hands it to the check alone, and never to the gate.

    A process the gate starts — a test that pushes, say — would otherwise record over this
    push's measurement.
    """
    seen = host.root / "gate-saw"
    gate = host.gate(f'echo "${{ORCHESTRATOR_GATE_PUSH-unset}}" >{shlex.quote(str(seen))}')

    pushed = _push(host, host.budgets(), gate, {"ORCHESTRATOR_GATE_PUSH": "inherited"})

    assert pushed.returncode == 0, pushed.stdout + pushed.stderr
    _ran_once(host, pushed)
    assert seen.read_text(encoding="utf-8").strip() == "unset", "the gate inherited a push"
    recorded = host.record.read_text(encoding="utf-8").splitlines()[0]
    assert recorded.startswith("push=") and recorded != "push=inherited", recorded
    assert "— within" in pushed.result(), pushed.result()


def test_a_sampling_cadence_that_is_no_number_of_seconds_falls_back_and_still_samples(
    host: Host,
) -> None:
    """And a sampler stopped mid-nap leaves nothing holding the step's output open.

    The fallback naps a minute between samples; a nap left holding the hook's stdout or
    stderr would keep whoever reads them to their end waiting it out, so the output being
    at its end the moment the step exits — the gate having ended mid-nap — is what shows
    the stop released them.
    """
    host.loadavg.write_text("3.75 1.00 1.00 1/100 1\n", encoding="utf-8")

    ran = _push_to_its_end(
        host, host.budgets(), host.gate("sleep 2"), {"ORCHESTRATOR_GATE_SAMPLE_SECONDS": "0"}
    )
    pushed = ran.pushed

    assert ran.output_ended, "the step's output was held open after the step exited"
    assert pushed.returncode == 0, pushed.stdout + pushed.stderr
    _ran_once(host, pushed)
    assert "ORCHESTRATOR_GATE_SAMPLE_SECONDS=0 is not a positive number" in pushed.stderr
    assert pushed.conditions()["load1_max"] == "3.75", pushed.result()


def test_the_root_files_dispatches_condition_reads_the_host_view(
    host: Host, dispatches: list[int]
) -> None:
    """The tracked condition, through the installed check, answers the view's count.

    A run root the view skips and a registry entry whose dispatch has ended — two lines the
    view prints beside its rows — leave the count as it was. With the view unable to read
    its runs root, listing a dispatch it cannot prove alive or gone, or not there to run,
    the same check records `unknown`.
    """
    document = {
        "schema_version": 1,
        "conditions": [
            {**condition, "command": _absolute(condition["command"])}
            for condition in _tracked_budgets()["conditions"]
            if condition["name"] == "dispatches"
        ],
        "budgets": [
            {
                "id": "probe",
                "measure": "elapsed",
                "command": ["true"],
                "unit": "seconds",
                "direction": "max",
                "threshold": 60,
            }
        ],
    }
    budgets = host.root / "budgets.yaml"
    budgets.write_text(yaml.safe_dump(document), encoding="utf-8")
    checker = Path(sys.executable).parent / "onebudgetspec"

    def check(environment: dict[str, str] | None = None) -> str:
        ran = subprocess.run(
            [str(checker), "check", str(budgets), "--json"],
            cwd=host.root,
            env={**host.environment(), **(environment or {})},
            text=True,
            capture_output=True,
            timeout=e2e_timeout(180),
            check=False,
        )
        assert ran.returncode == 0, ran.stdout + ran.stderr
        report = json.loads(ran.stdout)
        (result,) = report["results"]
        return result["host"]["conditions"]["dispatches"]

    assert check() == str(len(dispatches))

    (host.runs / "not-a-run").mkdir()
    ended = _detached()
    run_root(host.runs, run_name(), dispatch_pid=ended)
    os.kill(ended, signal.SIGKILL)
    view = subprocess.run(
        ["just", "host"],
        cwd=REPO_ROOT,
        env=host.environment(),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=True,
    ).stdout
    assert "run root(s) skipped" in view and "stale registry entr" in view, view
    assert check() == str(len(dispatches))

    assert check({"PATH": "/usr/bin:/bin"}) == "unknown"
    host.runs.chmod(0)
    assert check() == "unknown"
    host.runs.chmod(0o755)
    run_root(host.runs, run_name())
    assert check() == "unknown"


#: The `gate-time` command the tracked file names, run from any directory.
MEASUREMENT = REPO_ROOT / "scripts" / "budget-gate-time.sh"
#: A push name as the hook mints one.
A_PUSH = "1790000000-4242-17"


def _record(host: Host, **fields: str) -> None:
    """A recording at this host's push record, as `record_gate_push` writes one.

    Written by hand only for the records no gate run writes — a corrupted one — and for
    the leading zero a writer never produces but a reader still has to make a number of.
    """
    values = {
        "push": A_PUSH,
        "seconds": "42",
        "dispatches_max": "2",
        "load1_max": "1.50",
        **fields,
    }
    host.record.parent.mkdir(parents=True, exist_ok=True)
    host.record.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8"
    )


def _measured(host: Host, environment: dict[str, str]) -> tuple[dict, str]:
    """The tracked `gate-time` budget measured by the installed check, as the hook asks."""
    document = _tracked_budgets()
    document.pop("conditions", None)
    document["budgets"] = [_tracked_gate_time()]
    for budget in document["budgets"]:
        budget["command"] = _absolute(budget["command"])
    budgets = host.root / "gate-time-only.yaml"
    budgets.write_text(yaml.safe_dump(document), encoding="utf-8")
    ran = subprocess.run(
        [
            str(Path(sys.executable).parent / "onebudgetspec"),
            "check",
            str(budgets),
            "--exclude-label",
            "onepipeline",
            "--json",
        ],
        cwd=host.root,
        env={**host.environment(), **environment},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    (result,) = json.loads(ran.stdout)["results"]
    return result, ran.stderr


def test_a_recording_with_a_leading_zero_is_reported_as_a_number(host: Host) -> None:
    _record(host, seconds="0042")

    result, stderr = _measured(host, {"ORCHESTRATOR_GATE_PUSH": A_PUSH})

    assert result["verdict"] == "within", stderr
    assert result["actual"] == 42
    assert result["host"]["conditions"]["dispatches_max"] == "2"
    assert result["host"]["conditions"]["load1_max"] == "1.50"


@pytest.mark.parametrize(
    ("push", "record", "environment", "said"),
    [
        (None, {}, {}, "no push is named in ORCHESTRATOR_GATE_PUSH"),
        ("a push", {}, {}, "ORCHESTRATOR_GATE_PUSH=a push is not a push the hook names"),
        (
            A_PUSH,
            {},
            {"XDG_STATE_HOME": "relative", "HOME": "also-relative"},
            "neither XDG_STATE_HOME nor HOME is an absolute path",
        ),
        (A_PUSH, None, {}, "no gate could be read at"),
        (A_PUSH, {"seconds": "a while"}, {}, "names no whole number of seconds (a while)"),
        (A_PUSH, {"dispatches_max": "-1"}, {}, "names dispatches_max=-1"),
        (A_PUSH, {"load1_max": "high"}, {}, "names load1_max=high"),
    ],
    ids=[
        "no-push",
        "malformed-push",
        "no-state-root",
        "no-record",
        "malformed-seconds",
        "malformed-dispatches-peak",
        "malformed-load-peak",
    ],
)
def test_a_measurement_that_cannot_be_trusted_is_an_error_saying_why(
    host: Host,
    push: str | None,
    record: dict[str, str] | None,
    environment: dict[str, str],
    said: str,
) -> None:
    """Every refusal the measurement makes, as the check records it: an error, never a value."""
    if record is not None:
        _record(host, **record)
    named = {} if push is None else {"ORCHESTRATOR_GATE_PUSH": push}

    result, stderr = _measured(host, {**named, **environment})

    assert result["verdict"] == "error", result
    assert result["actual"] is None, result
    assert said in stderr, stderr


def test_the_measurement_run_outside_a_check_names_what_it_needs(host: Host) -> None:
    """Run by hand it has no result file to write; given one it cannot write, it says so."""
    _record(host)
    environment = {**host.environment(), "ORCHESTRATOR_GATE_PUSH": A_PUSH}
    environment.pop("ONEBUDGETSPEC_RESULT", None)

    unnamed = subprocess.run(
        [str(MEASUREMENT)],
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    unwritable = subprocess.run(
        [str(MEASUREMENT)],
        env={**environment, "ONEBUDGETSPEC_RESULT": str(host.root / "missing" / "result.json")},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert unnamed.returncode == 1, unnamed.stderr
    assert "ONEBUDGETSPEC_RESULT names no result file" in unnamed.stderr, unnamed.stderr
    assert unwritable.returncode == 1, unwritable.stderr
    assert "the result could not be written to" in unwritable.stderr, unwritable.stderr
