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
import itertools
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
from run_rows import without_ownership
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator import REPO_ROOT
from orchestrator.channel import create_channel, record_surface
from orchestrator.goals import register_run
from orchestrator.journal import open_journal
from orchestrator.liveness import PARKED_AFTER_SECONDS
from orchestrator.runs import RunId

#: A process that spawns one child, announces it, and then idles. Its recorded
#: owner is doing nothing either, so the *only* difference from the parked case is
#: the live child — which is what the parked decision must turn on.
_BUSY = (
    # The CHILD writes the ready file from inside its own payload, so readiness
    # means the child is past exec and visible to liveness scans - a parent-side
    # write can land in the fork-to-exec window where the scan sees no stamp.
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c',\n"
    "    'import sys, time; open(sys.argv[1], \\'w\\').write(\\'ready\\\\n\\'); time.sleep(600)',\n"
    "    sys.argv[1]])\n"
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

#: A graph the real executor settles without dispatching anything: it records the
#: round, writes its result, and stops on the human action. That is the observed
#: run's exact shape — round 1 finished waiting on a person, and the orchestrator
#: then went quiet instead of opening round 2.
HUMAN_PLAN = {"tasks": [{"id": "gate", "kind": "human", "task": "Attest the release."}]}


def _settle_round(runs_dir: Path, run_id: str, plan_path: Path) -> None:
    """Settle one real recorded round through `just run-plan`.

    The round directory, its result, and the summary the listing later renders are
    the real executor's, so the row this test asserts against is one production
    actually produces. A human-only graph reaches that state spending no harness
    turn, and `run-plan` exits non-zero because the graph is not complete.
    """
    plan_path.write_text(json.dumps(HUMAN_PLAN), encoding="utf-8")
    settled = subprocess.run(
        [
            "just",
            "run-plan",
            str(plan_path),
            "--run",
            run_id,
            "--runs-dir",
            str(runs_dir),
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
    )
    assert (runs_dir / run_id / "round-01" / "result.json").is_file(), settled.stderr


def _launch(run_dir: Path, pid: int, *, idle_for: float, settled: bool = False) -> None:
    """Record one launched run, silent since ``idle_for`` seconds ago, owned by ``pid``.

    The channel, its pacemaker clock, and the journal are written by the same
    functions production uses, through their own injectable clock where they have
    one. Only the two ownership records are written here, and only because they
    must name a process this test can hold in a known state. With ``settled``, the
    round is the one `_settle_round` already recorded and nothing about it is
    written here at all.
    """
    stale = time.time() - idle_for
    round_dir = run_dir / "round-01"
    host = socket.gethostname()
    if not settled:
        open_journal(run_dir, RunId(run_dir.name), 1).append(
            "round-started", detail={"nodes": 1, "concurrency": 1, "plan": {}}
        )
        round_dir.mkdir()
        (round_dir / "plan.json").write_text(json.dumps(PLAN), encoding="utf-8")
        (round_dir / "status.json").write_text(
            json.dumps({"status": "running", "pid": pid, "host": host}), encoding="utf-8"
        )
    create_channel(run_dir)
    record_surface(run_dir / "channel", now=stale)
    (run_dir / "orchestrator").mkdir()
    (run_dir / "launch.json").write_text(
        json.dumps({"schema_version": 2, "run_id": run_dir.name, "channel_id": run_dir.name}),
        encoding="utf-8",
    )
    (run_dir / "orchestrator" / "status.json").write_text(
        json.dumps({"status": "running", "pid": pid, "host": host}), encoding="utf-8"
    )
    for path in (
        run_dir / "events.jsonl",
        run_dir / "orchestrator" / "status.json",
        *sorted(round_dir.iterdir()),
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
    # The ownership column is dropped here: it names the launching session, which
    # differs per developer. tests/e2e/test_run_ownership_e2e.py asserts it directly.
    return without_ownership(listed.stdout)


def _line(output: str, run_id: str) -> str:
    return next(line for line in output.splitlines() if run_id in line)


def _beneath(output: str, row: str) -> list[str]:
    """The indented indicator lines one row owns, in the order the view printed them.

    A row carries several independent indicators and the view is free to gain
    another, so what a caller asserts on is which line appears under *this* run —
    never which offset it landed at, which is a claim about the neighbours.
    """
    following = output.split(row, 1)[1].splitlines()[1:]
    return [
        line.strip() for line in itertools.takewhile(lambda line: line.startswith(" "), following)
    ]


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


# Asserts over every visible run's liveness; concurrent tests' runs pollute it.
@pytest.mark.single_threaded
def test_the_views_name_a_live_concurrent_run_and_never_a_parked_one(
    tmp_path: Path, sleeper: list[subprocess.Popen[bytes]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Company a planner must act on is the working kind, and only that kind.

    Two orchestrations once worked one repository identity for hours, found only by
    tracing a process tree by hand. Nothing in either run's own ledger mentions the
    other, so these lines are the only place a planner learns of it — and a run that
    merely holds a pid is the residue `--acknowledge-concurrent` exists to launch
    past, so reporting that one as company would put the confusion straight back.
    """
    state = tmp_path / "state"
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(state))
    runs = tmp_path / "runs"
    working = {"busy-one": tmp_path / "one.ready", "busy-two": tmp_path / "two.ready"}
    owners = {name: _busy(sleeper, ready) for name, ready in working.items()}
    owners["parked-run"] = _idle(sleeper)
    for run_id, pid in owners.items():
        # Silent for longer than the pacemaker-derived default too: `just goals` is a
        # standalone inventory with no threshold flag, so it must reach the same
        # verdict as the view that was given one.
        _launch(runs / run_id, pid, idle_for=PARKED_AFTER_SECONDS + 600)
        # Registered by the production writer every launch registers through, so
        # what the views read is the index a real launch leaves behind.
        register_run(
            run_id=run_id,
            run_dir=runs / run_id,
            goal={"id": run_id, "text": f"Goal for {run_id}"},
            identities=["local/shared"],
            pid=pid,
            acknowledge_concurrent=True,
        )

    listed = _runs(runs, parked_after=60)
    inventory = subprocess.run(
        ["just", "goals"],
        cwd=REPO_ROOT,
        env={**os.environ, "AI_ORCHESTRATOR_HOME": str(state)},
        text=True,
        capture_output=True,
        check=True,
        timeout=e2e_timeout(60),
    ).stdout

    # Each working run is told about the other, by pid, with the identity they share.
    for run_id, other in (("busy-one", "busy-two"), ("busy-two", "busy-one")):
        beneath = _beneath(listed, f"* {run_id}  ACTIVE")
        assert (
            beneath.count(
                f"CONCURRENT: run '{other}' is LIVE (owner pid {owners[other]} on "
                f"{socket.gethostname()}) goal 'Goal for {other}'; shared identities: local/shared"
            )
            == 1
        ), beneath
        assert not [line for line in beneath if "parked-run" in line], beneath
    # The parked launch is reported as stopped, and never as a second run at work.
    assert "PARKED" in _line(listed, "parked-run")
    assert "CONCURRENT" not in _line(listed, "parked-run")

    # `just goals` states each registered owner for the same reason: a registration
    # is not a running process, and the two call for opposite decisions.
    assert f"owner: parked (owner pid {owners['parked-run']} on " in inventory
    assert f"owner: live (owner pid {owners['busy-one']} on " in inventory


def test_just_runs_reports_a_settled_rounds_own_summary_under_a_parked_launch(
    tmp_path: Path, sleeper: list[subprocess.Popen[bytes]], relays: list[subprocess.Popen[str]]
) -> None:
    """The observed shape: round 1 settled, a surface still queued, nothing working.

    A recorded row renders what the run is *waiting on* in place of the round
    summary, which is right for a live launch and a half-truth for a stopped one:
    the queued surface outlives the work that queued it, so a parked run would wear
    it as its summary and read as a launch actively waiting on the planner. The
    round's own summary is what that row is; the PARKED line beneath says why it
    stopped.
    """
    runs = tmp_path / "runs"
    parked = runs / "parked-run"
    busy = runs / "busy-run"
    for run_id in ("parked-run", "busy-run"):
        _settle_round(runs, run_id, tmp_path / f"{run_id}.json")
    _launch(parked, _idle(sleeper), idle_for=600, settled=True)
    _launch(busy, _busy(sleeper, tmp_path / "busy.ready"), idle_for=600, settled=True)
    _queue_blocking_surface(parked, relays)
    _queue_blocking_surface(busy, relays)

    listed = _runs(runs, parked_after=60)
    stopped = _line(listed, "parked-run")
    assert stopped.startswith("! parked-run  round-01  (1 waiting;")
    assert SURFACE not in stopped
    assert "PARKED (alive with no child process" in listed

    # The same surface on a launch that is working is exactly what the row should
    # say, so the summary it replaces above is genuinely competing.
    assert _line(listed, "busy-run") == (
        f"* busy-run  round-01  (waiting for planner decision: blocker: {SURFACE})"
    )


def test_just_status_reports_a_parked_launch_beside_its_stale_surface(
    tmp_path: Path, sleeper: list[subprocess.Popen[bytes]], relays: list[subprocess.Popen[str]]
) -> None:
    """Beside, not instead of, and the parked line first.

    `just status` is the surface-reporting view, so hiding the queued surface would
    lose the reason the run is stuck. Ordering carries the whole meaning: read the
    other way round, the run is waiting on a person who is being waited on by
    nothing. This is the same contract an abandoned round already keeps.
    """
    runs = tmp_path / "runs"
    parked = runs / "parked-run"
    _launch(parked, _idle(sleeper), idle_for=600)
    _queue_blocking_surface(parked, relays)
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
    lines = reported.stdout.splitlines()
    stalled = next(line for line in lines if "PARKED" in line)
    stale = f"parked-run: waiting for planner decision: blocker: {SURFACE}"
    assert stalled.startswith("parked-run: PARKED (alive with no child process")
    assert stale in lines, reported.stdout
    assert lines.index(stalled) + 1 == lines.index(stale), reported.stdout


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
    """The default threshold, not an override: this invocation passes no flag.

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


def test_a_settle_terminating_attach_returns_on_a_parked_launch(
    tmp_path: Path, sleeper: list[subprocess.Popen[bytes]], relays: list[subprocess.Popen[str]]
) -> None:
    """Parked is a settlement: `--until-settled` hands a parked run back, non-zero.

    This is the state the whole attach mode exists for. A parked launch keeps its
    pid, so a follow would wait on it forever and read exactly like a run that is
    merely quiet — which is how one went unattended for hours. Returning 3 is what
    makes the difference reach a caller that is not watching the stream.

    The busy launch is the control, and it settles too — but at the surface it is
    genuinely blocked on, with the status that says a planner reply is all it
    wants. Same silence, same surface, one live child, two different answers.
    """
    runs = tmp_path / "runs"
    parked = runs / "parked-run"
    busy = runs / "busy-run"
    _launch(parked, _idle(sleeper), idle_for=3600)
    _launch(busy, _busy(sleeper, tmp_path / "busy.ready"), idle_for=3600)
    _queue_blocking_surface(parked, relays)
    _queue_blocking_surface(busy, relays)

    stalled = subprocess.run(
        ["just", "monitor", "parked-run", "--until-settled", "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
    )
    assert stalled.returncode == 3, stalled.stdout + stalled.stderr
    assert "settled, nothing is driving this run" in stalled.stdout.splitlines()[-1]
    assert "PARKED (alive with no child process" in stalled.stdout

    waiting = subprocess.run(
        ["just", "monitor", "busy-run", "--until-settled", "--runs-dir", str(runs)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
    )
    assert waiting.returncode == 0, waiting.stdout + waiting.stderr
    assert "settled, awaiting the planner" in waiting.stdout.splitlines()[-1]
    assert SURFACE in waiting.stdout

    # The threshold is what made the first one settle, and it is honoured here as
    # everywhere else: under a longer one that same launch is simply quiet, so the
    # attach keeps following it and the surface it is holding is what settles it.
    patient = subprocess.run(
        [
            "just",
            "monitor",
            "parked-run",
            "--until-settled",
            "--runs-dir",
            str(runs),
            "--parked-after",
            "100000",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
    )
    assert patient.returncode == 0, patient.stdout + patient.stderr
    assert "settled, awaiting the planner" in patient.stdout.splitlines()[-1]


def test_the_progress_views_reject_an_unusable_parked_threshold(tmp_path: Path) -> None:
    """The threshold is the claim's whole meaning, so an unusable one fails loudly."""
    runs = tmp_path / "runs"
    runs.mkdir()
    for command in (
        ["just", "runs", "--runs-dir", str(runs), "--parked-after", "0"],
        ["just", "runs", "--runs-dir", str(runs), "--parked-after", "inf"],
        ["just", "status", "--runs-dir", str(runs), "--parked-after", "-5"],
        ["just", "status", "--runs-dir", str(runs), "--parked-after", "nan"],
        # The two attaching commands take the same flag, so they owe the same
        # refusal: a threshold this view cannot use is one no view may guess at.
        ["just", "monitor", "--runs-dir", str(runs), "--parked-after", "inf"],
        ["just", "monitor", "--runs-dir", str(runs), "--parked-after", "0"],
        ["just", "orchestrate", "plan.json", "--parked-after", "nan"],
        ["just", "orchestrate", "plan.json", "--parked-after", "-5"],
    ):
        refused = subprocess.run(
            command, cwd=REPO_ROOT, text=True, capture_output=True, timeout=e2e_timeout(60)
        )
        assert refused.returncode != 0
        assert "--parked-after must be a positive, finite number of seconds" in refused.stderr


@pytest.mark.parametrize(
    "heartbeat",
    [
        pytest.param("{ truncated", id="truncated"),
        # Numeric and unusable. `.inf` and `.nan` are the tokens the ledger's own YAML
        # reader turns into real floats, so any process that writes this file can put
        # them there. Untimed, `.inf` reads as the newest moment there is and a parked
        # launch looks as if it had just surfaced; `.nan` loses every comparison it
        # takes part in, which reaches the same wrong answer by the other route.
        pytest.param('{"schema_version": 1, "last_surface_at": .inf}', id="infinite"),
        pytest.param('{"schema_version": 1, "last_surface_at": .nan}', id="not-a-number"),
    ],
)
def test_unusable_liveness_records_do_not_hide_a_parked_launch(
    tmp_path: Path, sleeper: list[subprocess.Popen[bytes]], heartbeat: str
) -> None:
    """A record a view cannot use is not evidence of work, and must not read as any."""
    runs = tmp_path / "runs"
    parked = runs / "parked-run"
    _launch(parked, _idle(sleeper), idle_for=600)
    (parked / "channel" / "heartbeat.json").write_text(heartbeat, encoding="utf-8")
    (parked / "round-01" / "status.json").write_text("{ truncated", encoding="utf-8")
    stale = time.time() - 600
    os.utime(parked / "channel" / "heartbeat.json", (stale, stale))
    os.utime(parked / "round-01" / "status.json", (stale, stale))

    listed = _runs(runs, parked_after=60)
    assert "PARKED" in _line(listed, "parked-run")
