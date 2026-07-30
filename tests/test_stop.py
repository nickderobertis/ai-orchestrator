"""Unit tests for `just stop`: the ownership guard and the process teardown.

The real journeys — a launched run refused, forced, stopped, and reclaimed — run
through the CLI in tests/e2e/test_run_ownership_e2e.py. These call the same entry
point in-process, against real processes and real signals, so the refusals and the
rendering count toward the coverage gate, which a subprocess CLI invocation cannot.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from process_tree import is_running as observed_running

from orchestrator.launch import generate_launch_id, session_fingerprint, write_provenance
from orchestrator.stop import (
    ProcessId,
    StopReport,
    is_running,
    main,
    recorded_owners,
    report_outcome,
)

#: A process tree whose root is not this process's child: the launcher starts a
#: detached owner, records both pids, and exits, so nothing here can be mistaken
#: for a zombie the test itself is failing to reap.
_TREE = """
import subprocess, sys
marker = sys.argv[1]
owner = subprocess.Popen(
    [
        sys.executable,
        "-c",
        "import subprocess,sys,time;"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(600)']);"
        "open(sys.argv[1],'w').write(str(child.pid));"
        "time.sleep(600)",
        marker + ".child",
    ],
    start_new_session=True,
)
open(marker, "w").write(str(owner.pid))
"""

DEAD_PID = os.getpid() + 10_000_000


@pytest.fixture(autouse=True)
def _session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test as one named planner session, with its own state directory."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-mine")
    for name in ("CLAUDE_SESSION_ID", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_SANDBOX"):
        monkeypatch.delenv(name, raising=False)


def _record_run(
    runs: Path,
    run_id: str,
    *,
    session: str | None,
    orchestrator_pid: int | None = None,
    round_pid: int | None = None,
    host: str | None = None,
) -> Path:
    """Leave the records a launched run leaves: its join key and its owners."""
    run_dir = runs / run_id
    (run_dir / "orchestrator").mkdir(parents=True)
    launch: dict[str, object] = {"schema_version": 2, "run_id": run_id}
    if session is not None:
        launch_id = generate_launch_id()
        write_provenance(
            launch_id=launch_id,
            launcher="claude-code",
            launcher_session_id=session,
            repository_identity="local/app",
        )
        launch["launch"] = {"launch_id": launch_id}
    (run_dir / "launch.json").write_text(json.dumps(launch), encoding="utf-8")
    recorded_host = host or socket.gethostname()
    if orchestrator_pid is not None:
        (run_dir / "orchestrator" / "status.json").write_text(
            json.dumps({"status": "running", "pid": orchestrator_pid, "host": recorded_host}),
            encoding="utf-8",
        )
    if round_pid is not None:
        round_dir = run_dir / "round-01"
        round_dir.mkdir()
        (round_dir / "status.json").write_text(
            json.dumps({"status": "running", "pid": round_pid, "host": recorded_host}),
            encoding="utf-8",
        )
    return run_dir


def _spawn_tree(tmp_path: Path, name: str) -> tuple[ProcessId, ProcessId]:
    """Start a detached two-level tree and return its owner and worker pids."""
    marker = tmp_path / name
    launcher = subprocess.run([sys.executable, "-c", _TREE, str(marker)], check=True, timeout=30)
    assert launcher.returncode == 0
    child = marker.with_suffix(marker.suffix + ".child")
    wait = time.monotonic() + 30
    while time.monotonic() < wait:
        if marker.is_file() and child.is_file() and child.read_text().strip():
            return ProcessId(int(marker.read_text())), ProcessId(int(child.read_text()))
        time.sleep(0.02)
    raise AssertionError("the test process tree never reported its pids")


def _await_gone(*pids: ProcessId) -> None:
    """Assert nothing survived, asking the suite's own prober rather than the one under test."""
    wait = time.monotonic() + 30
    while time.monotonic() < wait:
        if not any(observed_running(pid) for pid in pids):
            return
        time.sleep(0.02)
    alive = [pid for pid in pids if observed_running(pid)]
    raise AssertionError(f"processes outlived the stop: {alive}")


def test_stop_refuses_a_run_another_planner_launched(tmp_path, capsys) -> None:
    runs = tmp_path / "runs"
    _record_run(runs, "theirs", session="session-theirs", orchestrator_pid=os.getpid())

    assert main(["theirs", "--runs-dir", str(runs)]) == 2

    error = capsys.readouterr().err
    # Named by its launching session, and never by the session id itself.
    assert f"claude-code:{session_fingerprint('session-theirs')}" in error
    assert "session-theirs" not in error
    assert "--force" in error


def test_stop_refuses_a_run_it_cannot_attribute(tmp_path, capsys) -> None:
    """Unknown is not the same as mine: it belongs to somebody until proven otherwise."""
    runs = tmp_path / "runs"
    _record_run(runs, "nameless", session=None, orchestrator_pid=os.getpid())

    assert main(["nameless", "--runs-dir", str(runs)]) == 2
    assert "no recorded launcher" in capsys.readouterr().err


def test_stop_refuses_when_the_caller_has_no_session_of_its_own(
    tmp_path, capsys, monkeypatch
) -> None:
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    runs = tmp_path / "runs"
    _record_run(runs, "owned", session="session-theirs", orchestrator_pid=os.getpid())

    assert main(["owned", "--runs-dir", str(runs)]) == 2
    assert "this session has no launcher provenance" in capsys.readouterr().err


def test_stop_rejects_a_run_it_cannot_address(tmp_path, capsys) -> None:
    assert main(["../escape", "--runs-dir", str(tmp_path)]) == 2
    assert "run id" in capsys.readouterr().err
    assert main(["absent", "--runs-dir", str(tmp_path)]) == 2
    assert "no recorded run" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exit_status:
        main(["absent", "--runs-dir", str(tmp_path), "--grace", "-1"])
    assert exit_status.value.code == 2


def test_stop_reports_a_run_with_nothing_left_running(tmp_path, capsys) -> None:
    runs = tmp_path / "runs"
    _record_run(runs, "settled", session="session-mine", orchestrator_pid=DEAD_PID)

    assert main(["settled", "--runs-dir", str(runs)]) == 0

    out = capsys.readouterr().out
    assert "nothing to stop" in out
    assert "just results settled" in out


def test_stop_leaves_another_hosts_processes_alone(tmp_path, capsys) -> None:
    """This host cannot signal another's, and must say so rather than claim success."""
    runs = tmp_path / "runs"
    _record_run(runs, "elsewhere", session="session-mine", orchestrator_pid=1, host="other-host")

    assert main(["elsewhere", "--runs-dir", str(runs)]) == 0

    captured = capsys.readouterr()
    assert "cannot signal another host's processes" in captured.err
    assert "nothing to stop" in captured.out


def test_stop_refuses_to_tear_down_the_tree_it_is_running_in(tmp_path, capsys) -> None:
    runs = tmp_path / "runs"
    _record_run(runs, "self", session="session-mine", orchestrator_pid=os.getpid())

    assert main(["self", "--runs-dir", str(runs)]) == 2
    assert "part of self's process tree" in capsys.readouterr().err


def test_stop_ends_the_whole_tree_of_a_run_this_session_launched(tmp_path, capsys) -> None:
    owner, worker = _spawn_tree(tmp_path, "owned-tree")
    runs = tmp_path / "runs"
    run_dir = _record_run(
        runs, "mine", session="session-mine", orchestrator_pid=owner, round_pid=owner
    )
    assert {item.pid for item in recorded_owners(run_dir)} == {owner}

    assert main(["mine", "--runs-dir", str(runs)]) == 0

    # The worker below the recorded owner is stopped too: it is in no process group
    # the recorded pid names, so only walking the tree reaches it.
    _await_gone(owner, worker)
    assert "signalled 2 process(es)" in capsys.readouterr().out


def test_forcing_another_planners_run_reports_the_owner_before_stopping_it(
    tmp_path, capsys
) -> None:
    owner, worker = _spawn_tree(tmp_path, "forced-tree")
    runs = tmp_path / "runs"
    _record_run(runs, "forced", session="session-theirs", orchestrator_pid=owner)

    assert main(["forced", "--runs-dir", str(runs), "--force"]) == 0

    out = capsys.readouterr().out
    assert f"claude-code:{session_fingerprint('session-theirs')}" in out
    assert f"orchestrator owner pid {owner}" in out
    _await_gone(owner, worker)


def test_stop_kills_a_process_that_ignores_the_signal_it_was_given(tmp_path, capsys) -> None:
    """A grace period is a courtesy, not a way out: SIGTERM is escalated."""
    marker = tmp_path / "stubborn"
    source = (
        "import signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "open(sys.argv[1], 'w').write('ready')\n"
        "time.sleep(600)\n"
    )
    stubborn = subprocess.Popen(  # noqa: S603 - a fixture process, not external input
        [sys.executable, "-c", source, str(marker)], start_new_session=True
    )
    wait = time.monotonic() + 30
    while time.monotonic() < wait and not marker.is_file():
        time.sleep(0.02)
    assert marker.is_file()
    runs = tmp_path / "runs"
    _record_run(runs, "stubborn", session="session-mine", orchestrator_pid=stubborn.pid)

    assert main(["stubborn", "--runs-dir", str(runs), "--grace", "0.2"]) == 0

    assert "1 needed SIGKILL" in capsys.readouterr().out
    stubborn.wait(timeout=30)


def test_a_status_record_that_names_no_working_process_contributes_no_pid(tmp_path) -> None:
    """Only a record that clearly names a running owner may decide what is signalled."""
    runs = tmp_path / "runs"
    run_dir = _record_run(runs, "malformed", session="session-mine")
    status = run_dir / "orchestrator" / "status.json"
    status.write_text("{ not json", encoding="utf-8")
    assert recorded_owners(run_dir) == ()
    for record in (
        {"status": "completed", "pid": 1, "host": socket.gethostname()},
        {"status": "running", "pid": True, "host": socket.gethostname()},
        {"status": "running", "pid": 0, "host": socket.gethostname()},
        {"status": "running", "pid": 1, "host": ""},
        {"status": "running", "pid": 1},
    ):
        status.write_text(json.dumps(record), encoding="utf-8")
        assert recorded_owners(run_dir) == ()


def test_a_process_this_user_may_not_signal_is_reported_running() -> None:
    """Init is the honest example: unprobeable must never read as gone."""
    assert is_running(ProcessId(1))
    assert not is_running(ProcessId(DEAD_PID))


def test_a_stop_that_left_something_running_is_a_failure_with_a_status(capsys) -> None:
    survivor = ProcessId(DEAD_PID)
    report = StopReport(signalled=(survivor,), remaining=(survivor,), escalated=(survivor,))

    assert report_outcome("leaky", report) == 1

    captured = capsys.readouterr()
    assert "signalled 1 process(es) (1 needed SIGKILL)" in captured.out
    assert f"left 1 process(es) running: {survivor}" in captured.err
