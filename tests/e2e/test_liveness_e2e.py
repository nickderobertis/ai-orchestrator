"""Real-CLI journey: a launch that is alive but not working reports as parked.

The progress views read a run directory and the operating system, and those are
the boundaries driven here: real `just runs` / `just status` / `just monitor`
subprocesses against a run whose channel, heartbeat clock, journal, and queued
planner surface are written by the production writers — the surface by the real
relay the orchestrator's supervisor side runs — and whose recorded owner is a real
live process, one with a live child and one without.

# llmlint: ignore-file[tests_mirror_real_usage] The two records this writes by hand,
# `launch.json` and the owner pids, are the ones no command can produce on demand:
# the defect under test is an orchestrator alive with no work, and every real
# `just orchestrate` run either has a live descendant or writes its report and
# stops. Naming a process the test can hold in that exact state is the only way to
# reach it; the commands, the process liveness, the channel, and the ledger writes
# are all real.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator import REPO_ROOT
from orchestrator.channel import create_channel, record_surface
from orchestrator.journal import open_journal
from orchestrator.runs import RunId

#: A process that spawns one child, announces it, and then idles. Its recorded
#: owner is doing nothing either, so the *only* difference from the parked case is
#: the live child — which is what the parked decision must turn on.
_BUSY = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
    "open(sys.argv[1], 'w').write('ready\\n')\n"
    "time.sleep(600)\n"
)

#: The blocking surface's message, so a view reporting it instead is unmistakable.
SURFACE = "answer before round 2"


@pytest.fixture
def sleeper(tmp_path: Path) -> Iterator[list[subprocess.Popen[bytes]]]:
    started: list[subprocess.Popen[bytes]] = []
    yield started
    for process in started:
        process.kill()
        process.wait(timeout=e2e_timeout(10))


def _idle(started: list[subprocess.Popen[bytes]]) -> int:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    started.append(process)
    return process.pid


def _busy(started: list[subprocess.Popen[bytes]], ready: Path) -> int:
    process = subprocess.Popen([sys.executable, "-c", _BUSY, str(ready)])
    started.append(process)
    wait = deadline(30)
    while time.monotonic() < wait:
        if ready.is_file():
            return process.pid
        time.sleep(0.02)
    raise AssertionError("the busy launch never spawned its child")


@pytest.fixture
def relays() -> Iterator[list[subprocess.Popen[str]]]:
    started: list[subprocess.Popen[str]] = []
    yield started
    for relay in started:
        with contextlib.suppress(PermissionError, ProcessLookupError):
            os.killpg(os.getpgid(relay.pid), signal.SIGKILL)
        relay.wait(timeout=e2e_timeout(15))


def _queue_blocking_surface(run_dir: Path, started: list[subprocess.Popen[str]]) -> None:
    """Leave one real, unanswered blocking planner surface on the run's channel.

    Written by the production relay the orchestrator's supervisor side runs: it
    persists the surface and then blocks for a reply that never arrives. That is
    the exact state observed in the parked run — a surface still queued, so every
    view that reads it first renders a dead launch as live work waiting on a
    person. The relay never reaches its `record_surface`, so the run's silence
    clock is untouched, which is what makes this surface *competing* rather than
    progress.
    """
    relay = subprocess.Popen(
        [
            "uv",
            "run",
            "orchestrator-relay-supervisor",
            str(run_dir / "channel"),
            run_dir.name,
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
    started.append(relay)
    assert relay.stdin is not None
    relay.stdin.write(json.dumps({"op": "supervisor", "kind": "blocker", "message": SURFACE}))
    relay.stdin.close()
    wait = deadline(60)
    while time.monotonic() < wait:
        if (run_dir / "channel" / "planner-pending.json").is_file():
            return
        assert relay.poll() is None, f"the relay exited with {relay.returncode}"
        time.sleep(0.02)
    raise AssertionError("the relay never queued its planner surface")


PLAN = {"tasks": [{"id": "ship", "persona": "engineer", "task": "Ship"}]}


def _launch(run_dir: Path, pid: int, *, idle_for: float) -> None:
    """Record one launched run, silent since ``idle_for`` seconds ago, owned by ``pid``.

    The channel, its pacemaker clock, and the journal are written by the same
    functions production uses, through their own injectable clock where they have
    one. Only the two ownership records are written here, and only because they
    must name a process this test can hold in a known state.
    """
    stale = time.time() - idle_for
    create_channel(run_dir)
    record_surface(run_dir / "channel", now=stale)
    open_journal(run_dir, RunId(run_dir.name), 1).append(
        "round-started", detail={"nodes": 1, "concurrency": 1, "plan": {}}
    )
    round_dir = run_dir / "round-01"
    round_dir.mkdir()
    (run_dir / "orchestrator").mkdir()
    host = socket.gethostname()
    (run_dir / "launch.json").write_text(
        json.dumps({"schema_version": 2, "run_id": run_dir.name, "channel_id": run_dir.name}),
        encoding="utf-8",
    )
    (run_dir / "orchestrator" / "status.json").write_text(
        json.dumps({"status": "running", "pid": pid, "host": host}), encoding="utf-8"
    )
    (round_dir / "plan.json").write_text(json.dumps(PLAN), encoding="utf-8")
    (round_dir / "status.json").write_text(
        json.dumps({"status": "running", "pid": pid, "host": host}), encoding="utf-8"
    )
    for path in (
        run_dir / "events.jsonl",
        run_dir / "orchestrator" / "status.json",
        round_dir / "plan.json",
        round_dir / "status.json",
    ):
        os.utime(path, (stale, stale))


def _runs(runs_dir: Path, parked_after: float) -> str:
    listed = subprocess.run(
        [
            "just",
            "runs",
            "--runs-dir",
            str(runs_dir),
            "--parked-after",
            str(parked_after),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
        timeout=e2e_timeout(60),
    )
    return listed.stdout


def _line(output: str, run_id: str) -> str:
    return next(line for line in output.splitlines() if run_id in line)


def test_just_runs_reports_a_parked_launch_and_never_a_busy_one(
    tmp_path: Path, sleeper: list[subprocess.Popen[bytes]]
) -> None:
    runs = tmp_path / "runs"
    parked = runs / "parked-run"
    busy = runs / "busy-run"
    _launch(parked, _idle(sleeper), idle_for=600)
    _launch(busy, _busy(sleeper, tmp_path / "busy.ready"), idle_for=600)

    listed = _runs(runs, parked_after=60)
    assert "PARKED" in _line(listed, "parked-run")
    assert "no child process" in _line(listed, "parked-run")
    # Same silence, same threshold, one live child: never reported as parked.
    assert "PARKED" not in _line(listed, "busy-run")
    assert "ACTIVE" in _line(listed, "busy-run")

    # The threshold is the whole claim: under a longer one the same launch is
    # simply quiet, not parked.
    patient = _runs(runs, parked_after=100_000)
    assert "PARKED" not in patient
    assert "ACTIVE" in _line(patient, "parked-run")

    # A ledger write is progress, and it clears the report immediately.
    (parked / "events.jsonl").touch()
    assert "PARKED" not in _runs(runs, parked_after=60)


def test_just_status_reports_a_parked_launch(
    tmp_path: Path, sleeper: list[subprocess.Popen[bytes]]
) -> None:
    runs = tmp_path / "runs"
    _launch(runs / "parked-run", _idle(sleeper), idle_for=600)
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    reported = subprocess.run(
        [
            "just",
            "status",
            "--runs-dir",
            str(runs),
            "--parked-after",
            "60",
        ],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "HOME": str(tmp_path),
            "ONEHARNESS_HISTORY_DIR": str(history_dir),
        },
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
    )
    assert reported.returncode == 0, reported.stderr
    assert "parked-run: PARKED" in reported.stdout


def _monitor(runs: Path, run_id: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", "monitor", run_id, "--once", "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
    )


def test_just_monitor_reports_a_parked_launch_over_its_stale_blocking_surface(
    tmp_path: Path, sleeper: list[subprocess.Popen[bytes]], relays: list[subprocess.Popen[str]]
) -> None:
    """The default threshold, not an override: `just monitor` takes no flag.

    Both launches carry the same real unanswered blocking surface, so the surface
    is genuinely competing to be reported and the only difference between them is
    the live child. The parked one must be reported as parked *instead of* that
    surface — reporting the surface is precisely how the observed run rendered an
    idle orchestrator as work waiting on a person.
    """
    runs = tmp_path / "runs"
    parked = runs / "parked-run"
    busy = runs / "busy-run"
    _launch(parked, _idle(sleeper), idle_for=3600)
    _launch(busy, _busy(sleeper, tmp_path / "busy.ready"), idle_for=3600)
    _queue_blocking_surface(parked, relays)
    _queue_blocking_surface(busy, relays)

    watched = _monitor(runs, "parked-run")
    assert watched.returncode == 0, watched.stderr
    assert "round-01 parked: PARKED (alive with no child process" in watched.stdout
    # The surface outranked everything else this run recorded, and PARKED outranks it.
    assert SURFACE not in watched.stdout
    assert "ACK REQUIRED" not in watched.stdout

    # Same silence, same surface, one live child: the surface is what gets reported.
    working = _monitor(runs, "busy-run")
    assert working.returncode == 0, working.stderr
    assert "PARKED" not in working.stdout
    assert f"round-01 blocked: ACK REQUIRED: blocker: {SURFACE}" in working.stdout


def test_the_progress_views_reject_an_unusable_parked_threshold(tmp_path: Path) -> None:
    """The threshold is the claim's whole meaning, so an unusable one fails loudly."""
    runs = tmp_path / "runs"
    runs.mkdir()
    for command in (
        ["just", "runs", "--runs-dir", str(runs), "--parked-after", "0"],
        ["just", "runs", "--runs-dir", str(runs), "--parked-after", "inf"],
        ["just", "status", "--runs-dir", str(runs), "--parked-after", "-5"],
        ["just", "status", "--runs-dir", str(runs), "--parked-after", "nan"],
    ):
        refused = subprocess.run(
            command, cwd=REPO_ROOT, text=True, capture_output=True, timeout=e2e_timeout(60)
        )
        assert refused.returncode != 0
        assert "--parked-after must be a positive, finite number of seconds" in refused.stderr


def test_unreadable_liveness_records_do_not_hide_a_parked_launch(
    tmp_path: Path, sleeper: list[subprocess.Popen[bytes]]
) -> None:
    """A record a view cannot read is not evidence of work, and must not read as any."""
    runs = tmp_path / "runs"
    parked = runs / "parked-run"
    _launch(parked, _idle(sleeper), idle_for=600)
    (parked / "channel" / "heartbeat.json").write_text("{ truncated", encoding="utf-8")
    (parked / "round-01" / "status.json").write_text("{ truncated", encoding="utf-8")
    stale = time.time() - 600
    os.utime(parked / "channel" / "heartbeat.json", (stale, stale))
    os.utime(parked / "round-01" / "status.json", (stale, stale))

    listed = _runs(runs, parked_after=60)
    assert "PARKED" in _line(listed, "parked-run")
