"""A real pytest session releases its shared Codex server even when a test fails."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import pytest
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

REPORT_ENV = "AIO_POOL_SESSION_REPORT"
FAIL_ENV = "AIO_POOL_SESSION_FAIL"
JOURNEY = "tests/e2e/pooled_server_session/test_pooled_server_session_e2e.py::test_session_probe"


@dataclass(frozen=True)
class SessionReport:
    """Evidence the child session leaves for its parent to verify after teardown."""

    state: str
    pids: list[int]


def test_session_probe(tmp_path: Path, short_state_base: Path, oneharness_bin: str) -> None:
    report = os.environ.get(REPORT_ENV)
    if report is None:
        pytest.skip("driven in a separate pytest session by the journey below")
    state = Path(os.environ["XDG_STATE_HOME"])
    assert state.is_relative_to(short_state_base)
    assert str(state) != os.environ["AIO_PARENT_STATE"]
    home = tmp_path / "codex"
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
            str(short_state_base / "sessions"),
        ],
        env={
            **os.environ,
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
    Path(report).write_text(json.dumps(asdict(SessionReport(state=str(state), pids=pids))))
    assert os.environ[FAIL_ENV] == "no", "deliberate failed test before session teardown"


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


@pytest.mark.parametrize("server", [True, False], ids=["server", "sweeper"])
def test_release_reports_a_survivor_without_signalling_it(tmp_path: Path, server: bool) -> None:
    """A process exceeding the lifecycle bound is named and left for its owner to end."""
    from pooled_server_state import release

    state = tmp_path / "survivor-state"
    state.mkdir()
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            "import sys; print('ready', flush=True); sys.stdin.read()",
            *(["app-server", "--listen"] if server else []),
        ],
        env={**os.environ, "XDG_STATE_HOME": str(state / "oneharness")},
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
