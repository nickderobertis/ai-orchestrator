"""Real-CLI journeys for run ownership: attribution, the view, and stopping.

Every boundary here is the production one. `just orchestrate` is launched from an
environment shaped like a planner's own session, so the provenance it records is
the one the ordinary path records — no flags, no fixture written by hand. `just
runs` and `just stop` are then driven as a second planner would drive them, from a
*different* session, which is the only way the refusal being tested can be real.

Only the paid harness is faked, at the seam onejudge exposes for it: both the
orchestrator agent and its worker run the deterministic `fake_backend`, and the
worker parks at a release barrier so a stop lands on a run with live work below it
rather than on an idle process. That worker is a real dispatch tree — round owner,
watchdog, onejudge, provider — which is what makes "no orphaned worker" mean
anything.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from process_tree import is_running
from rendezvous import Rendezvous
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator import BASE_CONFIG, REPO_ROOT
from orchestrator.launch import (
    LAUNCHER_ENVIRONMENT_VARIABLES,
    provenance_dir,
    read_launch_info,
    session_fingerprint,
)
from orchestrator.stop import RecordedOwner, recorded_owners, run_tree
from orchestrator.watchdog import ProcessId

FAKE_BACKEND = REPO_ROOT / "tests" / "e2e" / "fake_backend.py"

#: Everything a harness exports to say which session it is. Cleared before each
#: launch so a test's session is the one under test, not the one running the suite.
#: The detection inputs are taken from production rather than restated, so a marker
#: added there cannot go on leaking the developer's own session into these launches
#: while this list still claims to have isolated them.
_LAUNCHER_VARIABLES = (
    *sorted(LAUNCHER_ENVIRONMENT_VARIABLES),
    # Not detection inputs, so not derivable: `CODEX_HOME` is ambient configuration
    # production deliberately refuses to identify a session by, and the rest are the
    # explicit overrides and the label channel. A planner session exports all of them,
    # so they would still reach the launch under test.
    "CODEX_HOME",
    "ORCHESTRATOR_LAUNCHER",
    "ORCHESTRATOR_LAUNCHER_SESSION",
    "ONEHARNESS_HISTORY_LABELS",
)


def _session_env(
    tmp_path: Path, session_id: str | None, launcher: str = "claude-code"
) -> dict[str, str]:
    """The environment one planner session hands the commands it runs.

    ``None`` is a plain shell: no harness names it, so everything it launches is
    unattributable — the state every run on this host was in before provenance was
    populated, and the one a stop must still refuse.
    """
    environment = {**os.environ}
    for name in _LAUNCHER_VARIABLES:
        environment.pop(name, None)
    # The protected record belongs to this test, not to the developer's own state.
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    if session_id is not None and launcher == "claude-code":
        environment["CLAUDECODE"] = "1"
        environment["CLAUDE_CODE_SESSION_ID"] = session_id
    elif session_id is not None:
        environment["CODEX_THREAD_ID"] = session_id
    return environment


def _base(tmp_path: Path) -> Path:
    value = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    value["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
    path = tmp_path / "base.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return path


def _plan(tmp_path: Path, name: str) -> Path:
    """A one-worker plan whose worker parks until the test releases it."""
    path = tmp_path / f"{name}-plan.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "name": name,
                "tasks": [
                    {
                        "id": "worker",
                        "persona": "engineer",
                        "task": (
                            f"slow-branch {tmp_path / f'{name}.ticks'}"
                            f"{Rendezvous.at(tmp_path, name).sentinels(1)}"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@dataclass(frozen=True)
class Launch:
    """One launched run and the session that launched it."""

    run_id: str
    runs: Path
    env: dict[str, str]
    plan: Path

    @property
    def run_dir(self) -> Path:
        return self.runs / self.run_id


@pytest.fixture
def launches(tmp_path: Path) -> Iterator[list[Launch]]:
    """Force-stop every run a test launched, however the test ended."""
    started: list[Launch] = []
    yield started
    for launch in started:
        subprocess.run(
            ["just", "stop", launch.run_id, "--runs-dir", str(launch.runs), "--force"],
            cwd=REPO_ROOT,
            env=launch.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=e2e_timeout(120),
        )


def _orchestrate(
    tmp_path: Path,
    runs: Path,
    onejudge_bin: str,
    name: str,
    env: dict[str, str],
    started: list[Launch],
) -> Launch:
    plan = _plan(tmp_path, name)
    launched = subprocess.run(
        [
            "just",
            "orchestrate",
            "--detach",
            str(plan),
            "--runs-dir",
            str(runs),
            "--base",
            str(_base(tmp_path)),
            "--onejudge-bin",
            onejudge_bin,
            "--skill-command",
            sys.executable,
            str(FAKE_BACKEND),
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    launch = Launch(str(json.loads(launched.stdout)["run_id"]), runs, env, plan)
    started.append(launch)
    return launch


def _runs_view(runs: Path, env: dict[str, str], *extra: str) -> str:
    viewed = subprocess.run(
        ["just", "runs", "--runs-dir", str(runs), *extra],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=e2e_timeout(180),
    )
    assert viewed.returncode == 0, viewed.stderr
    return viewed.stdout


def _stop(launch: Launch, env: dict[str, str], *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", "stop", launch.run_id, "--runs-dir", str(launch.runs), *extra],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=e2e_timeout(180),
    )


def _await_parked_worker(launch: Launch, name: str, tmp_path: Path) -> tuple[RecordedOwner, ...]:
    """Wait until the run has a real dispatch tree sitting at the worker's barrier."""
    ready = Rendezvous.at(tmp_path, name).ready
    wait = deadline(180)
    while time.monotonic() < wait:
        owners = recorded_owners(launch.run_dir)
        if ready.is_file() and any(item.source.startswith("round-") for item in owners):
            return owners
        time.sleep(0.05)
    stderr = launch.run_dir / "orchestrator" / "stderr.log"
    detail = stderr.read_text(encoding="utf-8") if stderr.is_file() else "<no stderr>"
    raise AssertionError(f"the run never reached its worker barrier: {detail}")


def _reclaiming(
    launch: Launch, base: Path, env: dict[str, str], tmp_path: Path
) -> subprocess.Popen[str]:
    """Start the reclaim `just runs` printed, and leave it running.

    Started rather than awaited because the round it reclaims has to be *watched*:
    its node parks at a barrier this test has not opened yet, so a call that waited
    for the command to return would wait for a release it is holding. Output goes to
    files for the reason the recorded launches use them — nothing drains a pipe while
    the test is doing something else.
    """
    streams = _reclaim_streams(tmp_path)
    with streams[0].open("wb") as out, streams[1].open("wb") as err:
        return subprocess.Popen(
            [
                "just",
                "run-plan",
                str(launch.run_dir / "round-01" / "plan.json"),
                "--run",
                launch.run_id,
                "--runs-dir",
                str(launch.runs),
                "--base",
                str(base),
                "--provider",
                "command",
                "--recover",
            ],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            stdout=out,
            stderr=err,
            start_new_session=True,
        )


def _reclaim_streams(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "reclaim.out", tmp_path / "reclaim.err"


def _await_the_interrupted_node_running_again(
    held: Rendezvous, reclaiming: subprocess.Popen[str], tmp_path: Path
) -> None:
    """Block until the reclaimed round has the interrupted node parked at `held`.

    There is no second outcome to be timed into: either the node reaches the barrier,
    or the reclaim ends without it and says why. A reclaim that refused the abandoned
    round, or replayed its recorded result, takes the second branch — it exits without
    ever dispatching the node — so neither can be mistaken for a re-execution.
    """
    wait = deadline(240)
    while not held.arrived():
        if reclaiming.poll() is not None:
            out, err = (path.read_text(encoding="utf-8") for path in _reclaim_streams(tmp_path))
            raise AssertionError(f"the reclaim never re-ran the interrupted node: {out}{err}")
        assert time.monotonic() < wait, "the reclaimed round never reached the node's barrier"
        time.sleep(0.02)


def _claimed_owner(launch: Launch) -> ProcessId:
    """The pid recorded for the round that is executing now."""
    status = json.loads((launch.run_dir / "round-01" / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "running", status
    return ProcessId(int(status["pid"]))


def _await_gone(pids: set[ProcessId]) -> set[ProcessId]:
    wait = deadline(60)
    while time.monotonic() < wait:
        alive = {pid for pid in pids if is_running(pid)}
        if not alive:
            return set()
        time.sleep(0.05)
    return {pid for pid in pids if is_running(pid)}


#: A detached process that refuses SIGTERM. Nothing a real dispatch runs behaves this
#: way on demand, so the escalation path needs one planted; it is a real process taking
#: real signals, which is the part that matters.
_STUBBORN = """
import signal, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1]).write_text(str(__import__("os").getpid()), encoding="utf-8")
while True:
    time.sleep(0.05)
"""

#: A detached process that takes signals normally, used as the pid a stale record names.
_SLEEPER = """
import sys, time
from pathlib import Path
Path(sys.argv[1]).write_text(str(__import__("os").getpid()), encoding="utf-8")
while True:
    time.sleep(0.05)
"""


def _spawn(source: str, marker: Path) -> ProcessId:
    """Start a detached helper process and return the pid it reports."""
    subprocess.Popen([sys.executable, "-c", source, str(marker)], start_new_session=True)
    wait = deadline(60)
    while time.monotonic() < wait:
        if marker.is_file() and marker.read_text(encoding="utf-8").strip():
            return ProcessId(int(marker.read_text(encoding="utf-8")))
        time.sleep(0.02)
    raise AssertionError(f"helper process never reported its pid to {marker}")


# The one thing in this file the public surface cannot produce, and it is deliberate.
# `just stop` decides from a round's recorded pid and start stamp, so reaching the
# escalation and pid-recycling paths needs an owner process that ignores SIGTERM and
# one whose record predates it. No plan onejudge can run produces either on demand,
# and a pid cannot be recycled to schedule. The run, its provenance, its own owners,
# and the stop itself all still come from the real recipes; only these two extra
# owners are planted, and they are real processes taking real signals.
# llmlint: ignore-block[tests_mirror_real_usage] see the note above this directive.
def _record_owner(run_dir: Path, round_name: str, pid: ProcessId, started: datetime) -> None:
    """Record ``pid`` as one of this run's own round owners, as a round would."""
    round_dir = run_dir / round_name
    round_dir.mkdir(parents=True, exist_ok=True)
    (round_dir / "status.json").write_text(
        json.dumps(
            {
                "status": "running",
                "pid": int(pid),
                "host": socket.gethostname(),
                "started": started.isoformat(),
            }
        ),
        encoding="utf-8",
    )


# llmlint: ignore-end[tests_mirror_real_usage]


def test_stop_escalates_a_process_that_refuses_sigterm_and_spares_a_recycled_pid(
    tmp_path: Path, onejudge_bin: str, launches: list[Launch]
) -> None:
    """The two teardown paths a real dispatch cannot be asked to produce on demand.

    The run, its provenance, and its own owners are real: `just orchestrate` launched
    it and `just stop` ends it through the recipe. What is planted is two extra owner
    processes, because neither state can be ordered up from a real dispatch — nothing
    onejudge runs ignores SIGTERM, and a pid cannot be recycled to schedule. Both are
    real processes taking real signals from the real CLI, which is what these paths
    are about.
    """
    runs = tmp_path / "runs"
    planner = _session_env(tmp_path, "session-alpha")
    mine = _orchestrate(tmp_path, runs, onejudge_bin, "escalated", planner, launches)
    owners = _await_parked_worker(mine, "escalated", tmp_path)
    tree = run_tree(owners)

    stubborn = _spawn(_STUBBORN, tmp_path / "stubborn.pid")
    # Recorded as a live round owner, exactly as a working round records itself.
    _record_owner(mine.run_dir, "round-90", stubborn, datetime.now(UTC))

    # A pid whose process demonstrably began *after* the record naming it: the number
    # came around again, and the process wearing it now is somebody else's.
    recycled = _spawn(_SLEEPER, tmp_path / "recycled.pid")
    _record_owner(mine.run_dir, "round-91", recycled, datetime.now(UTC) - timedelta(hours=2))

    # Both plants are live and equally reachable; only the recycling guard tells them
    # apart, so this is what makes the survival assertion below mean anything. Without
    # it the stale record would name a signalling target like any other owner.
    sources = {item.source for item in recorded_owners(mine.run_dir)}
    assert "round-90" in sources
    assert "round-91" not in sources, "a pid its record predates was adopted as an owner"

    try:
        stopped = _stop(mine, planner, "--grace", "1")
        assert stopped.returncode == 0, stopped.stderr
        # The stubborn owner outlived SIGTERM and was escalated, and the report counts it.
        counted = re.search(r"\((\d+) needed SIGKILL\)", stopped.stdout)
        assert counted is not None, stopped.stdout
        assert int(counted.group(1)) >= 1, stopped.stdout
        assert _await_gone(tree | {stubborn}) == set()

        # The recycled pid was never signalled: this stop left the stranger alone.
        assert is_running(recycled), "a pid the record predates was signalled"
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(recycled, signal.SIGKILL)


def test_orchestrate_records_this_session_and_runs_shows_who_owns_each_run(
    tmp_path: Path, onejudge_bin: str, launches: list[Launch]
) -> None:
    """The ordinary launch is attributable, and the view every planner reads says so."""
    runs = tmp_path / "runs"
    planner = _session_env(tmp_path, "session-alpha")
    mine = _orchestrate(tmp_path, runs, onejudge_bin, "owned", planner, launches)

    # No flags were passed: the run recorded the session it was launched from.
    launch_id = read_launch_info(mine.run_dir)
    assert launch_id is not None
    record = json.loads(
        (
            Path(planner["XDG_STATE_HOME"]) / "ai-orchestrator" / "launches" / f"{launch_id}.json"
        ).read_text(encoding="utf-8")
    )
    assert record["launcher"] == "claude-code"
    assert record["launcher_session_id"] == "session-alpha"

    # And the sensitive half stayed out of the repository: the run directory holds
    # only the join key, exactly as the scheme requires.
    assert provenance_dir() != mine.run_dir
    leaked = [
        str(path)
        for path in mine.run_dir.rglob("*")
        if path.is_file() and b"session-alpha" in path.read_bytes()
    ]
    assert leaked == []

    # A second, unattributable launch: the state every run on this host was already in.
    stranger = _session_env(tmp_path, None)
    theirs = _orchestrate(tmp_path, runs, onejudge_bin, "unowned", stranger, launches)
    assert read_launch_info(theirs.run_dir) is not None

    owner_view = _runs_view(runs, planner)
    assert f"{mine.run_id}  [mine]" in owner_view
    # A run with no provenance reads as explicitly unknown, never as the caller's.
    assert f"{theirs.run_id}  [unknown]" in owner_view

    # The same runs, read by another planner: neither is theirs, and the owned one
    # is named by its launching session rather than by a session id nobody may print.
    other = _session_env(tmp_path, "session-beta")
    other_view = _runs_view(runs, other)
    assert f"{mine.run_id}  [claude-code:{session_fingerprint('session-alpha')}]" in other_view
    assert "session-alpha" not in other_view
    assert f"{theirs.run_id}  [unknown]" in other_view

    assert mine.run_id in _runs_view(runs, planner, "--mine")
    assert theirs.run_id not in _runs_view(runs, planner, "--mine")
    assert _runs_view(runs, other, "--mine").strip() == "No runs launched by this session."


def test_stop_refuses_another_planners_run_and_an_unattributable_one(
    tmp_path: Path, onejudge_bin: str, launches: list[Launch]
) -> None:
    """The guard that would have prevented the incident, and the override that reports."""
    runs = tmp_path / "runs"
    planner = _session_env(tmp_path, "session-alpha")
    other = _session_env(tmp_path, "session-beta")
    theirs = _orchestrate(tmp_path, runs, onejudge_bin, "guarded", planner, launches)
    owners = _await_parked_worker(theirs, "guarded", tmp_path)
    tree = run_tree(owners)

    refused = _stop(theirs, other)
    assert refused.returncode == 2, refused.stdout
    assert f"claude-code:{session_fingerprint('session-alpha')}" in refused.stderr
    assert "--force" in refused.stderr
    assert "session-alpha" not in refused.stderr
    # A refusal is a refusal: the run it declined to stop is still working.
    assert {pid for pid in tree if is_running(pid)}

    unattributed = _orchestrate(
        tmp_path, runs, onejudge_bin, "nameless", _session_env(tmp_path, None), launches
    )
    anonymous = _stop(unattributed, other)
    assert anonymous.returncode == 2, anonymous.stdout
    assert "no recorded launcher" in anonymous.stderr

    forced = _stop(theirs, other, "--force")
    assert forced.returncode == 0, forced.stderr
    # --force proceeds only after naming whose work it is ending.
    assert f"claude-code:{session_fingerprint('session-alpha')}" in forced.stdout
    assert _await_gone(tree) == set()


def test_stopping_a_run_leaves_no_worker_behind_and_the_round_reclaimable(
    tmp_path: Path, onejudge_bin: str, launches: list[Launch]
) -> None:
    """A stop ends the whole dispatch tree and gives the round back, unfinished."""
    runs = tmp_path / "runs"
    planner = _session_env(tmp_path, "session-alpha")
    mine = _orchestrate(tmp_path, runs, onejudge_bin, "reclaimed", planner, launches)
    owners = _await_parked_worker(mine, "reclaimed", tmp_path)
    tree = run_tree(owners)
    # The tree is a real dispatch: the launched orchestrator, the round owner it
    # started, and the worker process below them — not one idle process.
    assert len(tree) > len(owners)

    stopped = _stop(mine, planner)
    assert stopped.returncode == 0, stopped.stderr
    assert _await_gone(tree) == set()

    # The stopped round is reported exactly as an interrupted one, and names its own
    # reclaiming command rather than leaving the planner to reconstruct it.
    view = _runs_view(runs, planner)
    assert "ABANDONED" in view
    assert "--recover" in view

    # And the reclaim is real. What a stop decides is that the round it abandoned is
    # one `--recover` re-executes — not one it refuses, and not one whose recorded
    # result it replays — so that is what this proves, and it proves it where the
    # round says so itself rather than through a second dispatch's exit status. The
    # reclaim is started with the barrier still closed, so the interrupted node has
    # to be dispatched a second time to reach it, and it blocks there until this test
    # lets it go. Both halves are the round's own signals: the claim it writes before
    # any node runs, and the arrival, which a replayed result could not produce.
    held = Rendezvous.at(tmp_path, "reclaimed")
    held.ready.unlink()
    reclaiming = _reclaiming(mine, _base(tmp_path), planner, tmp_path)
    try:
        _await_the_interrupted_node_running_again(held, reclaiming, tmp_path)
        assert _claimed_owner(mine) not in {owner.pid for owner in owners}
    finally:
        held.let_go()
        # Reaped, not asserted on: what a released dispatch then does is dispatch's
        # own contract, and tests/e2e/test_round_ownership_e2e.py carries a reclaimed
        # round through to `complete` without a killed tree's teardown beside it.
        reclaiming.wait(timeout=e2e_timeout(600))


def test_stop_answers_for_a_run_it_cannot_address_or_has_nothing_left_to_stop(
    tmp_path: Path, onejudge_bin: str, launches: list[Launch]
) -> None:
    """The refusals a planner meets by mistyping, and the second stop that is a no-op.

    Every record read here was written by a real launch and a real stop; nothing is
    fabricated. Stopping an already-stopped run is the ordinary way a planner reaches
    the no-live-process path — after an interrupted round, or after a stop it is not
    sure landed — so it must be a plain success rather than an error.
    """
    runs = tmp_path / "runs"
    planner = _session_env(tmp_path, "session-alpha")
    mine = _orchestrate(tmp_path, runs, onejudge_bin, "addressed", planner, launches)
    _await_parked_worker(mine, "addressed", tmp_path)

    def stop(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["just", "stop", *args, "--runs-dir", str(runs)],
            cwd=REPO_ROOT,
            env=planner,
            text=True,
            capture_output=True,
            check=False,
            timeout=e2e_timeout(180),
        )

    # A run id that could name a path, and one that names nothing, are both refused
    # before anything is signalled — the live run above is still running afterwards.
    escaped = stop("../escape")
    assert escaped.returncode == 2, escaped.stdout
    assert "run id" in escaped.stderr
    absent = stop("no-such-run")
    assert absent.returncode == 2, absent.stdout
    assert "no recorded run" in absent.stderr
    # A grace period that is not a finite number would never expire into escalation.
    unbounded = stop(mine.run_id, "--grace", "nan")
    assert unbounded.returncode == 2, unbounded.stdout
    assert "finite" in unbounded.stderr
    assert recorded_owners(mine.run_dir), "the refusals must not have stopped the run"

    first = stop(mine.run_id)
    assert first.returncode == 0, first.stderr
    assert "signalled" in first.stdout

    # Stopping it again is a success that says there was nothing left, and still
    # points at the run's own review command.
    again = stop(mine.run_id)
    assert again.returncode == 0, again.stderr
    assert "nothing to stop" in again.stdout
    assert f"just results {mine.run_id}" in again.stdout


def _recorded_provenance(launch: Launch) -> dict[str, object]:
    """The out-of-repo record this launch joined to, read where the scheme puts it."""
    launch_id = read_launch_info(launch.run_dir)
    assert launch_id is not None
    record = (
        Path(launch.env["XDG_STATE_HOME"]) / "ai-orchestrator" / "launches" / f"{launch_id}.json"
    )
    return dict(json.loads(record.read_text(encoding="utf-8")))


def test_orchestrate_records_a_codex_session_and_honours_explicit_overrides(
    tmp_path: Path, onejudge_bin: str, launches: list[Launch]
) -> None:
    """Detection is not hardcoded to one harness, and a typed flag still wins."""
    runs = tmp_path / "runs"
    codex = _session_env(tmp_path, "thread-77", launcher="codex")
    detected = _orchestrate(tmp_path, runs, onejudge_bin, "codex-detected", codex, launches)
    record = _recorded_provenance(detected)
    assert record["launcher"] == "codex"
    assert record["launcher_session_id"] == "thread-77"

    # An explicit pair overrides the ambient session it was launched from, which is
    # what lets a wrapper attribute a run it starts on someone else's behalf.
    plan = _plan(tmp_path, "explicit")
    launched = subprocess.run(
        [
            "just",
            "orchestrate",
            "--detach",
            str(plan),
            "--runs-dir",
            str(runs),
            "--base",
            str(_base(tmp_path)),
            "--onejudge-bin",
            onejudge_bin,
            "--launcher",
            "claude-code",
            "--launcher-session",
            "declared-session",
            "--skill-command",
            sys.executable,
            str(FAKE_BACKEND),
        ],
        cwd=REPO_ROOT,
        env=codex,
        text=True,
        capture_output=True,
        check=True,
    )
    override = Launch(str(json.loads(launched.stdout)["run_id"]), runs, codex, plan)
    launches.append(override)
    overridden = _recorded_provenance(override)
    assert overridden["launcher"] == "claude-code"
    assert overridden["launcher_session_id"] == "declared-session"

    # Each run reads as its own session's, and neither as the other's.
    codex_view = _runs_view(runs, codex)
    assert f"{detected.run_id}  [mine]" in codex_view
    assert (
        f"{override.run_id}  [claude-code:{session_fingerprint('declared-session')}]" in codex_view
    )
    assert _runs_view(runs, codex, "--mine").count(override.run_id) == 0

    # A launcher named without a session cannot join anything, and neither can a
    # session id the scheme cannot carry: both degrade to `unknown` rather than
    # attaching the run to a session that did not launch it.
    for name, extra, environment in (
        ("half-override", ["--launcher", "claude-code"], codex),
        ("unusable-session", [], _session_env(tmp_path, "two\nlines")),
    ):
        partial = _plan(tmp_path, name)
        started = subprocess.run(
            [
                "just",
                "orchestrate",
                "--detach",
                str(partial),
                "--runs-dir",
                str(runs),
                "--base",
                str(_base(tmp_path)),
                "--onejudge-bin",
                onejudge_bin,
                *extra,
                "--skill-command",
                sys.executable,
                str(FAKE_BACKEND),
            ],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=True,
        )
        degraded = Launch(str(json.loads(started.stdout)["run_id"]), runs, environment, partial)
        launches.append(degraded)
        launch_id = read_launch_info(degraded.run_dir)
        assert launch_id is not None, "the run still records its own join key"
        assert not (
            Path(environment["XDG_STATE_HOME"])
            / "ai-orchestrator"
            / "launches"
            / f"{launch_id}.json"
        ).exists()
        assert f"{degraded.run_id}  [unknown]" in _runs_view(runs, environment)
