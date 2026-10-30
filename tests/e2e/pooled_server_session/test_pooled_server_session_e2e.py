"""A real pytest session releases its shared Codex server even when a test fails."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import pytest
import short_state
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

REPORT_ENV = "AIO_POOL_SESSION_REPORT"
FAIL_ENV = "AIO_POOL_SESSION_FAIL"
MODULE = "tests/e2e/pooled_server_session/test_pooled_server_session_e2e.py"
JOURNEY = f"{MODULE}::test_session_probe"
OWNER_REPORT_ENV = "AIO_POOL_OWNER_REPORT"
SURVIVORS_ENV = "AIO_POOL_SURVIVORS"


@dataclass(frozen=True)
class SessionReport:
    """Evidence the child session leaves for its parent to verify after teardown."""

    state: str
    pids: list[int]


def _start_server(oneharness_bin: str, state: Path, sessions: Path, home: Path) -> list[int]:
    """Run one controlled turn under `state`, and return the shared server it left serving."""
    home.mkdir()
    (home / "config.toml").write_text("")
    result = subprocess.run(
        [
            oneharness_bin,
            "run",
            "--config",
            str(REPO_ROOT / "oneharness.toml"),
            "--harness",
            "codex",
            # llmlint: ignore[e2e_not_mocked] Only the paid Codex process is replaced.
            "--mock-harness",
            "codex",
            "--prompt",
            "session cleanup",
            "--control",
            "--session",
            "pooled-probe",
            "--session-dir",
            str(sessions),
        ],
        env={
            **os.environ,
            "XDG_STATE_HOME": str(state),
            "CODEX_HOME": str(home),
            "MOCK_STDOUT": "\n".join(
                json.dumps(event)
                for event in [
                    {"type": "thread.started", "thread_id": "pool-probe"},
                    {
                        "type": "item.completed",
                        "item": {
                            "id": "answer",
                            "type": "agent_message",
                            "text": "cleanup-ok",
                        },
                    },
                    {"type": "turn.completed", "usage": {"input_tokens": 4, "output_tokens": 1}},
                ]
            )
            + "\n",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=e2e_timeout(30),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    sockets = list((state / "oneharness" / "servers").glob("*/sock/app-server.sock"))
    assert sockets, result.stdout + result.stderr
    pids = []
    for process in Path("/proc").iterdir():
        if not process.name.isdecimal():
            continue
        try:
            command = (process / "cmdline").read_bytes()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        if str(sockets[0]).encode() in command:
            pids.append(int(process.name))
    assert pids, sockets
    return pids


def test_session_probe(tmp_path: Path, short_state_base: Path, oneharness_bin: str) -> None:
    report = os.environ.get(REPORT_ENV)
    if report is None:
        pytest.skip("driven in a separate pytest session by the journey below")
    state = Path(os.environ["XDG_STATE_HOME"])
    assert state.is_relative_to(short_state_base)
    assert str(state) != os.environ["AIO_PARENT_STATE"]
    pids = _start_server(oneharness_bin, state, short_state_base / "sessions", tmp_path / "codex")
    Path(report).write_text(json.dumps(asdict(SessionReport(state=str(state), pids=pids))))
    assert os.environ[FAIL_ENV] == "no", "deliberate failed test before session teardown"


def test_server_owner_probe(tmp_path: Path, oneharness_bin: str) -> None:
    """Start a shared server in this test, record it, and fail when told to."""
    report = os.environ.get(OWNER_REPORT_ENV)
    if report is None:
        pytest.skip("driven in a separate pytest session by the journey below")
    state = short_state.state_home(tmp_path)
    pids = _start_server(oneharness_bin, state, state / "sessions", tmp_path / "codex")
    Path(report).write_text(json.dumps(asdict(SessionReport(state=str(state), pids=pids))))
    assert os.environ[FAIL_ENV] == "no", "deliberate failed test before its own teardown"


def test_survivor_probe() -> None:
    """Run after the owner probe's teardown and before the session's: read its server."""
    report = os.environ.get(OWNER_REPORT_ENV)
    if report is None:
        pytest.skip("driven in a separate pytest session by the journey below")
    record = SessionReport(**json.loads(Path(report).read_text()))
    survivors = [pid for pid in record.pids if Path(f"/proc/{pid}").exists()]
    Path(os.environ[SURVIVORS_ENV]).write_text(json.dumps(survivors))
    assert not survivors, f"the server the previous test started outlived it: {survivors}"


@pytest.mark.parametrize("failed", [False, True])
def test_session_waits_for_its_server_before_removing_state(tmp_path: Path, failed: bool) -> None:
    report = tmp_path / "report.json"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", JOURNEY, "--no-cov", "-p", "no:cacheprovider"],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            REPORT_ENV: str(report),
            FAIL_ENV: "yes" if failed else "no",
            "AIO_PARENT_STATE": os.environ.get("XDG_STATE_HOME", ""),
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=e2e_timeout(120),
    )
    assert result.returncode == int(failed), result.stdout + result.stderr
    assert report.exists(), result.stdout + result.stderr
    record = SessionReport(**json.loads(report.read_text()))
    state = Path(record.state)
    assert not [pid for pid in record.pids if Path(f"/proc/{pid}").exists()]
    assert not state.exists(), f"session left its server state at {state}"


@pytest.mark.parametrize("failed", [False, True])
def test_a_test_waits_for_its_server_before_the_next_test_runs(
    tmp_path: Path, failed: bool
) -> None:
    """No shared server outlives the test that started it, passed or failed.

    The child session runs the owner probe and then the survivor probe, so the survivor
    probe reads the owner's server after the owner's teardown and before the session's.
    """
    report = tmp_path / "owner.json"
    survivors = tmp_path / "survivors.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            f"{MODULE}::test_server_owner_probe",
            f"{MODULE}::test_survivor_probe",
            "--no-cov",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            OWNER_REPORT_ENV: str(report),
            SURVIVORS_ENV: str(survivors),
            FAIL_ENV: "yes" if failed else "no",
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=e2e_timeout(240),
    )
    assert survivors.exists(), result.stdout + result.stderr
    assert json.loads(survivors.read_text()) == [], result.stdout + result.stderr
    assert result.returncode == int(failed), result.stdout + result.stderr
    record = SessionReport(**json.loads(report.read_text()))
    assert record.pids


@pytest.mark.parametrize(
    ("server", "scrubbed"),
    [(True, False), (False, False), (True, True)],
    ids=["server", "sweeper", "scrubbed-server"],
)
def test_release_reports_a_survivor_without_signalling_it(
    tmp_path: Path, server: bool, scrubbed: bool
) -> None:
    """A process exceeding the lifecycle bound is named and left for its owner to end.

    `scrubbed` is a server started as oneharness 0.26 starts one, with no
    `XDG_STATE_HOME` in its environment: only its socket address says whose it is.
    """
    from pooled_server_state import release

    state = tmp_path / "survivor-state"
    state.mkdir()
    socket = f"unix://{state}/oneharness/servers/survivor/sock/app-server.sock"
    environment = {key: value for key, value in os.environ.items() if key != "XDG_STATE_HOME"}
    if not scrubbed:
        environment["XDG_STATE_HOME"] = str(state / "oneharness")
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            "import sys; print('ready', flush=True); sys.stdin.read()",
            *(["app-server", "--listen", socket] if server else []),
        ],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"
        with pytest.raises(RuntimeError) as refused:
            release(state)
        message = str(refused.value)
        assert f"processes still using test state {state}" in message
        assert f"{process.pid}: executable=" in message
        assert "parent chain=" in message and "stdin=" in message
        assert process.poll() is None
        # Servers block removal; sweepers are waited for after their entry is removed.
        assert state.exists() is server
    finally:
        process.communicate(timeout=e2e_timeout(10))
    assert process.returncode == 0


@pytest.mark.parametrize("related", [False, True], ids=["server", "server-and-client"])
def test_a_test_teardown_reports_a_survivor_without_signalling_it(
    tmp_path: Path, related: bool
) -> None:
    """A server a test started that outlives the bound is named and left running.

    The server carries no `XDG_STATE_HOME`, as oneharness 0.26 starts one; with
    `related`, a client of its state home stays alive too and is named beside it.
    """
    from pooled_server_state import await_started, snapshot

    base = tmp_path / "base"
    socket = base / "0" / "oneharness" / "servers" / "survivor" / "sock" / "app-server.sock"
    socket.parent.mkdir(parents=True)
    socket.touch()
    before = snapshot(base)
    environment = {key: value for key, value in os.environ.items() if key != "XDG_STATE_HOME"}
    hold = "import sys; print('ready', flush=True); sys.stdin.read()"
    processes = [
        subprocess.Popen(
            [sys.executable, "-u", "-c", hold, "app-server", "--listen", f"unix://{socket}"],
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
    ]
    if related:
        processes.append(
            subprocess.Popen(
                [sys.executable, "-u", "-c", hold],
                env={**environment, "XDG_STATE_HOME": str(base / "0")},
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
            )
        )
    try:
        for process in processes:
            assert process.stdout is not None
            assert process.stdout.readline().strip() == "ready"
        with pytest.raises(RuntimeError) as refused:
            await_started(base, before, seconds=1)
        message = str(refused.value)
        assert f"processes a test started under ['{base / '0'}']" in message
        for process in processes:
            assert f"{process.pid}: executable=" in message
            assert process.poll() is None
    finally:
        for process in processes:
            process.communicate(timeout=e2e_timeout(10))
    assert [process.returncode for process in processes] == [0] * len(processes)
    await_started(base, before, seconds=1)
