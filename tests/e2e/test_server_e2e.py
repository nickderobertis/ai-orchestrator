"""E2E: the read-only DAG API + SSE served by a real uvicorn socket.

The service is the layer under test, so it runs as a real loopback uvicorn server
and is driven over TCP with an ordinary HTTP client — no ASGI shortcut. The run
directory is a real ``runs/`` fixture written with the same journal writers the
executor uses, and conversations are read through a real ``oneharness history list``
subprocess served from a recorded store (`fake_oneharness`), exactly as telemetry is.
Only that history binary is a double; the projection, telemetry, journal, and HTTP
surface are all real.
"""

# llmlint: ignore-file[e2e_not_mocked] The FastAPI + SSE service under test runs as a
# real uvicorn server driven over a real socket; only oneharness' history reader — a
# separate trust boundary this service consumes — is served from a recorded store.
# llmlint: ignore-file[tests_mirror_real_usage] The server's real usage is reading a
# runs directory and a history store that some *other* process wrote, so producing
# those inputs here is fixture setup, not a shortcut past the interface under test.
# They are written through the same public writers the executor and monitor use
# (`open_journal(...).append`, `runs.write_result`, `monitor.save_snapshot`); driving
# a real orchestration to emit them instead would spend the paid harness, which this
# repository fakes by policy.

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn

from orchestrator import REPO_ROOT
from orchestrator.detail_snapshot import PrDetail
from orchestrator.journal import NodeId, RunId, open_journal
from orchestrator.launch import DEFAULT_MAX_AGE_SECONDS, write_provenance
from orchestrator.monitor import DetailSnapshot, save_snapshot
from orchestrator.runs import prepare_round, write_result
from orchestrator.server import create_app

FAKE_ONEHARNESS = REPO_ROOT / "tests" / "e2e" / "fake_oneharness.py"
LAUNCH_ID = "a" * 32
#: The console script `just telemetry-server` runs, as installed by this project.
SERVER_CLI = Path(sys.executable).parent / "orchestrator-telemetry-server"


@contextmanager
def _serve(app: object) -> Iterator[str]:
    """Run ``app`` on an ephemeral loopback port; yield its base URL."""
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not server.started:  # pragma: no cover - startup failure
            raise RuntimeError("uvicorn did not start")
        sock: socket.socket = server.servers[0].sockets[0]
        host, port = sock.getsockname()[:2]
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _active_run(runs_dir: Path, run_id: str) -> Path:
    """A run whose only round has started but not settled (still watchable)."""
    run_dir = runs_dir / run_id
    prepare_round(run_dir, {"tasks": [{"id": "api", "task": "ship"}]})
    journal = open_journal(run_dir, RunId(run_id), 1)
    journal.append(
        "node-added", detail={"definition": {"id": "api", "persona": "engineer", "task": "ship"}}
    )
    journal.append("round-started", detail={"plan": {"schema_version": 3, "concurrency": 1}})
    journal.append("node-started", node=NodeId("api"), detail={"persona": "engineer"})
    (run_dir / "launch.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "run_id": run_id,
                "channel_id": run_id,
                "plan_name": run_id,
                "commands": {},
                "launch": {"launch_id": LAUNCH_ID},
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def _settle(run_dir: Path, run_id: str) -> None:
    journal = open_journal(run_dir, RunId(run_id), 1)
    journal.append(
        "node-settled",
        node=NodeId("api"),
        detail={"status": "done", "result": {"status": "done", "task": "ship"}},
    )
    result = {
        "ok": True,
        "state": "complete",
        "started_order": ["api"],
        "results": {"api": {"status": "done", "task": "ship"}},
    }
    journal.append("round-finished", detail={"result": result})
    write_result(run_dir / "round-01", result)


def _history_store(tmp_path: Path, run_id: str) -> Path:
    """A recorded oneharness store with a worker and a judge session for the run."""
    sessions = []
    for role, agent_role, name in (
        ("agent", "worker", "engineer-ship"),
        ("judge", "judge", "you-are-a-strict-careful-evaluator"),
    ):
        record = tmp_path / f"{role}.jsonl"
        record.write_text(
            json.dumps(
                {
                    "session": f"{role}-native",
                    "name": name,
                    "harness": "codex",
                    "model": "gpt",
                    "timestamp": "2026-07-19T00:00:00Z",
                    "prompt": f"{role} prompt",
                    "text": f"{role} said done",
                    "status": "ok",
                    "session_id": f"{role}-native",
                    "usage": {"input_tokens": 5, "output_tokens": 1},
                    "events": [
                        {
                            "kind": "tool_call",
                            "name": "command_execution",
                            "input": {"command": "just gate"},
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        sessions.append(
            {
                "id": f"{role}-native",
                "name": name,
                "project": str(tmp_path),
                "started": f"2026-07-19T00:00:0{len(sessions)}Z",
                "path": str(record),
                "labels": {
                    "run_id": run_id,
                    "node": "api",
                    "role": role,
                    "agent_role": agent_role,
                    "persona": "engineer",
                },
            }
        )
    store = tmp_path / "store.json"
    store.write_text(json.dumps({"sessions": sessions}), encoding="utf-8")
    return store


def _oneharness_bin(tmp_path: Path) -> Path:
    binary = tmp_path / "oneharness"
    binary.write_text(
        "#!/usr/bin/env python3\n" + FAKE_ONEHARNESS.read_text(encoding="utf-8"), encoding="utf-8"
    )
    binary.chmod(0o755)
    return binary


def _free_port() -> int:
    """A loopback port the OS just handed back, for a subprocess that cannot report one."""
    with closing(socket.socket()) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextmanager
def _serve_cli(*args: str) -> Iterator[str]:
    """Run the installed console script on a free port; yield its loopback base URL."""
    port = _free_port()
    process = subprocess.Popen(
        [str(SERVER_CLI), *args, "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:  # pragma: no cover - startup failure
                raise AssertionError(f"server exited early: {process.communicate()[0]}")
            try:
                if httpx.get(f"{base}/healthz", timeout=2).json() == {"status": "ok"}:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:  # pragma: no cover - startup timeout
            raise AssertionError("server never became reachable")
        yield base
    finally:
        process.terminate()
        process.wait(timeout=30)


def _tree(root: Path) -> dict[str, bytes]:
    """Every file beneath ``root`` keyed by relative path — a read must leave it identical."""
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _read_frames(lines: Iterator[str], *, until: str, limit: int = 40) -> list[dict[str, str]]:
    """Collect SSE frames from a single line iterator until ``event == until``."""
    frames: list[dict[str, str]] = []
    frame: dict[str, str] = {}
    seen = 0
    for line in lines:
        if line.startswith("id:"):
            frame["id"] = line[3:].strip()
        elif line.startswith("event:"):
            frame["event"] = line[6:].strip()
        elif line.startswith("data:"):
            frame["data"] = line[5:].strip()
        elif line.startswith(":"):
            frames.append({"comment": line})
            if until == "comment":
                return frames
        elif line == "" and frame:
            frames.append(frame)
            if frame.get("event") == until:
                return frames
            frame = {}
            seen += 1
            if seen > limit:  # pragma: no cover - safety valve
                return frames
    return frames  # pragma: no cover - stream closed early


def test_read_api_serves_projection_telemetry_and_role_tagged_conversations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = tmp_path / "runs"
    _active_run(runs, "demo")
    store = _history_store(tmp_path, "demo")
    monkeypatch.setenv("FAKE_ONEHARNESS_STORE", str(store))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    write_provenance(
        launch_id=LAUNCH_ID,
        launcher="claude-code",
        launcher_session_id="top-session",
        repository_identity="local/app",
    )
    # Default redaction: the join resolves the launcher but withholds the session id.
    app = create_app(runs, oneharness_bin=str(_oneharness_bin(tmp_path)))
    before = _tree(runs)

    with _serve(app) as base:
        client = httpx.Client(base_url=base, timeout=10)

        assert client.get("/healthz").json() == {"status": "ok"}

        runs_body = client.get("/api/v1/runs").json()
        assert [row["run_id"] for row in runs_body["runs"]] == ["demo"]
        assert runs_body["runs"][0]["launch"] == {
            "launch_id": LAUNCH_ID,
            "launcher": "claude-code",
        }

        detail = client.get("/api/v1/runs/demo").json()
        assert detail["rounds"][0]["node_states"] == {"api": "running"}
        assert detail["run"]["run_id"] == "demo"
        assert detail["launch"] == {"launch_id": LAUNCH_ID, "launcher": "claude-code"}
        assert "launcher_session_id" not in detail["launch"]  # redacted by default
        roles = {c["attribution"]["agentRole"] for c in detail["conversations"]}
        assert roles == {"worker", "judge"}
        worker = next(
            c for c in detail["conversations"] if c["attribution"]["agentRole"] == "worker"
        )
        assert worker["conversation"]["turns"][0]["user"] == "agent prompt"
        conversation_id = worker["conversation"]["id"]

        one = client.get(f"/api/v1/runs/demo/conversations/{conversation_id}").json()
        assert one["conversation"]["id"] == conversation_id
        assert one["attribution"]["transportRole"] == "agent"

        assert client.get("/api/v1/runs/bad!id").status_code == 422
        assert client.get("/api/v1/runs/absent").status_code == 404
        missing = client.get("/api/v1/runs/demo/conversations/nope")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "run_not_found"

        # A malformed query parameter uses the same error envelope, not FastAPI's.
        bad_query = client.get("/api/v1/runs", params={"include_settled": "maybe"})
        assert bad_query.status_code == 422
        assert bad_query.json()["error"]["code"] == "invalid_request"

    # The API is read-only: none of that journey — including the rejected requests —
    # touched a byte of the run directory it served.
    assert _tree(runs) == before

    # A deployment that opts into exposing the session id sees it surfaced.
    exposed_app = create_app(
        runs, oneharness_bin=str(_oneharness_bin(tmp_path)), expose_launcher_session_id=True
    )
    with _serve(exposed_app) as base:
        exposed = httpx.Client(base_url=base, timeout=10).get("/api/v1/runs/demo").json()
        assert exposed["launch"]["launcher_session_id"] == "top-session"


def test_events_stream_snapshots_then_invalidates_on_a_live_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = tmp_path / "runs"
    run_dir = _active_run(runs, "demo")
    monkeypatch.setenv("FAKE_ONEHARNESS_STORE", str(_history_store(tmp_path, "demo")))
    app = create_app(
        runs,
        oneharness_bin=str(_oneharness_bin(tmp_path)),
        poll_interval=0.05,
        heartbeat_interval=0.2,
    )

    with _serve(app) as base:
        client = httpx.Client(base_url=base, timeout=10)

        # Fresh connection opens with a snapshot of the current run list.
        with client.stream("GET", "/api/v1/events?run_id=demo") as response:
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-cache"
            lines = response.iter_lines()
            frames = _read_frames(lines, until="snapshot")
            snapshot = frames[-1]
            assert snapshot["event"] == "snapshot"
            snapshot_runs = json.loads(snapshot["data"])["runs"]
            assert snapshot_runs[0]["run_id"] == "demo"

            # An idle stream heartbeats at least once before anything changes.
            idle = _read_frames(lines, until="comment")
            assert idle[-1]["comment"].startswith(":")  # keep-alive comment

            # A live journal append then invalidates the run.
            _settle(run_dir, "demo")
            change = _read_frames(lines, until="run.changed")
            changed = change[-1]
            assert json.loads(changed["data"])["run_id"] == "demo"
            assert int(changed["id"]) > int(snapshot["id"])

        # A reconnect gets a snapshot too — nothing replays what it missed — but its
        # cursor continues from the one it supplied.
        with client.stream("GET", "/api/v1/events", headers={"Last-Event-ID": "5"}) as response:
            lines = response.iter_lines()
            resumed = _read_frames(lines, until="snapshot")
            assert resumed[-1]["event"] == "snapshot"
            assert resumed[-1]["id"] == "6"

            shutil.rmtree(run_dir)
            removed = _read_frames(lines, until="run.removed")
            assert removed[-1]["event"] == "run.removed"
            assert json.loads(removed[-1]["data"]) == {"run_id": "demo"}

        assert client.get("/api/v1/events?run_id=bad!id").status_code == 422


def test_events_stream_survives_a_malformed_last_event_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crafted resume header opens a fresh snapshot instead of failing the connection.

    ``--5`` is the shape that passed the original ``lstrip("-").isdigit()`` guard and
    then blew up in ``int()``; over a real socket that surfaced as a failed request
    rather than a stream, so the reconnect path is asserted here and not only against
    the parser.
    """
    runs = tmp_path / "runs"
    _active_run(runs, "demo")
    monkeypatch.setenv("FAKE_ONEHARNESS_STORE", str(_history_store(tmp_path, "demo")))
    app = create_app(
        runs,
        oneharness_bin=str(_oneharness_bin(tmp_path)),
        poll_interval=0.05,
        heartbeat_interval=0.2,
    )

    with _serve(app) as base:
        client = httpx.Client(base_url=base, timeout=10)

        for crafted in ("--5", "5-", "abc", ""):
            with client.stream(
                "GET", "/api/v1/events", headers={"Last-Event-ID": crafted}
            ) as response:
                assert response.status_code == 200, crafted
                frames = _read_frames(response.iter_lines(), until="snapshot")
                snapshot = frames[-1]
                assert snapshot["event"] == "snapshot", crafted
                # Cursor restarts at 1: the unusable header was discarded, not resumed.
                assert snapshot["id"] == "1", crafted
                assert json.loads(snapshot["data"])["runs"][0]["run_id"] == "demo"


def test_after_cursor_details_logs_and_projection_failure_over_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The remaining served fields and failure statuses, driven over the real socket.

    ``details`` and ``logs`` are the fields the contract added alongside the rounds,
    ``after`` is the documented resume alternative to ``Last-Event-ID``, and a corrupt
    journal must surface as 409 rather than a plausible graph.
    """
    runs = tmp_path / "runs"
    run_dir = _active_run(runs, "demo")
    _settle(run_dir, "demo")
    monkeypatch.setenv("FAKE_ONEHARNESS_STORE", str(_history_store(tmp_path, "demo")))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    # An expired provenance record must degrade the join, not resurface a stale session.
    write_provenance(
        launch_id=LAUNCH_ID,
        launcher="codex",
        launcher_session_id="stale-session",
        repository_identity="local/app",
        started_at=(
            datetime.now(UTC) - timedelta(seconds=DEFAULT_MAX_AGE_SECONDS + 60)
        ).isoformat(),
    )
    # Written through the monitor's own writer so the served snapshot is the real one.
    save_snapshot(
        run_dir,
        DetailSnapshot(
            prs={"api": PrDetail(number=7, state="OPEN", url="https://x/pull/7").to_record()}
        ),
    )
    (run_dir / "orchestrator").mkdir(parents=True, exist_ok=True)
    (run_dir / "orchestrator" / "gate.log").write_text("gate: passed\n", encoding="utf-8")
    # Larger than the served tail: a log is a scan aid, never an unbounded download.
    (run_dir / "orchestrator" / "stderr.log").write_text(
        "head-that-must-be-dropped\n" + "z" * 200_000, encoding="utf-8"
    )

    app = create_app(
        runs,
        oneharness_bin=str(_oneharness_bin(tmp_path)),
        expose_launcher_session_id=True,
        poll_interval=0.05,
        heartbeat_interval=0.2,
    )

    with _serve(app) as base:
        client = httpx.Client(base_url=base, timeout=10)

        detail = client.get("/api/v1/runs/demo").json()
        assert detail["details"]["prs"]["api"]["number"] == 7
        assert detail["logs"]["gate_log"] == "gate: passed\n"
        tail = detail["logs"]["orchestrator_stderr"]
        assert len(tail.encode()) == 64_000  # bounded to the tail, not the whole file
        assert "head-that-must-be-dropped" not in tail  # and it is the *end* of the log

        # This run has settled, so it appears only when the caller asks for settled runs.
        assert client.get("/api/v1/runs").json()["runs"] == []
        settled = client.get("/api/v1/runs", params={"include_settled": "true"}).json()
        assert [row["run_id"] for row in settled["runs"]] == ["demo"]
        # Expired: the launcher falls back and the session id is withheld even though
        # this deployment opted into exposing it.
        assert detail["launch"] == {"launch_id": LAUNCH_ID, "launcher": "unknown"}

        # `after` resumes without a snapshot, exactly like a valid Last-Event-ID.
        with client.stream("GET", "/api/v1/events?after=3") as response:
            assert response.status_code == 200
            frames = _read_frames(response.iter_lines(), until="snapshot")
            assert frames[-1]["id"] == "4"  # numbering continues from the supplied cursor

        # A negative cursor is not one this process could have issued.
        rejected = client.get("/api/v1/events?after=-1")
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "invalid_request"

        # An unusable conversation id is refused at the boundary, not scanned for.
        bad_conversation = client.get(f"/api/v1/runs/demo/conversations/{'x' * 300}")
        assert bad_conversation.status_code == 422
        assert bad_conversation.json()["error"]["code"] == "invalid_conversation_id"

        # A corrupt authoritative journal is a 409, never a rendered graph.
        journal = run_dir / "events.jsonl"
        journal.write_text(
            journal.read_text(encoding="utf-8") + '{"kind":"bogus"}\n', encoding="utf-8"
        )
        corrupt = client.get("/api/v1/runs/demo")
        assert corrupt.status_code == 409
        assert corrupt.json()["error"]["code"] == "projection_error"


def test_cli_refuses_a_nonloopback_bind_and_otherwise_serves(tmp_path: Path) -> None:
    """`just telemetry-server` runs this console script; drive it as a real process."""
    runs = tmp_path / "runs"
    _active_run(runs, "demo")
    assert SERVER_CLI.is_file(), f"install the project console scripts: {SERVER_CLI}"

    refused = subprocess.run(
        [str(SERVER_CLI), "--runs-dir", str(runs), "--host", "0.0.0.0"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert refused.returncode == 2, refused.stderr
    assert "--allow-nonloopback" in refused.stderr

    rejected = subprocess.run(
        [str(SERVER_CLI), "--runs-dir", str(runs), "--port", "70000"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert rejected.returncode != 0
    assert "port must be between 1 and 65535" in rejected.stderr

    # The loopback default serves.
    with _serve_cli("--runs-dir", str(runs)) as base:
        listed = httpx.get(f"{base}/api/v1/runs", timeout=30).json()
        assert [row["run_id"] for row in listed["runs"]] == ["demo"]

    # And the acknowledged non-loopback bind serves too, reachable over loopback
    # because 0.0.0.0 covers it.
    with _serve_cli("--runs-dir", str(runs), "--host", "0.0.0.0", "--allow-nonloopback") as base:
        assert httpx.get(f"{base}/healthz", timeout=30).json() == {"status": "ok"}


def test_events_stream_invalidates_conversations_for_a_watched_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new agent turn in history must reach a watching detail view.

    Conversations live outside the runs root, so nothing under it changes when an
    agent speaks; only the history poll can surface it.
    """
    runs = tmp_path / "runs"
    _active_run(runs, "demo")
    store = _history_store(tmp_path, "demo")
    monkeypatch.setenv("FAKE_ONEHARNESS_STORE", str(store))
    app = create_app(
        runs,
        oneharness_bin=str(_oneharness_bin(tmp_path)),
        poll_interval=0.05,
        heartbeat_interval=30.0,
        conversation_interval=0.05,
    )

    with _serve(app) as base:
        client = httpx.Client(base_url=base, timeout=15)
        with client.stream("GET", "/api/v1/events?run_id=demo") as response:
            lines = response.iter_lines()
            assert _read_frames(lines, until="snapshot")[-1]["event"] == "snapshot"

            # Append a second turn to the worker's recorded session.
            record = tmp_path / "agent.jsonl"
            record.write_text(
                record.read_text(encoding="utf-8")
                + json.dumps(
                    {
                        "session": "agent-native",
                        "name": "engineer-ship",
                        "harness": "codex",
                        "model": "gpt",
                        "timestamp": "2026-07-19T00:05:00Z",
                        "prompt": "keep going",
                        "text": "second turn",
                        "status": "ok",
                        "session_id": "agent-native",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            changed = _read_frames(lines, until="conversation.changed")
            assert changed[-1]["event"] == "conversation.changed"
            assert json.loads(changed[-1]["data"]) == {"run_id": "demo"}


def test_detail_degrades_to_no_conversations_when_history_is_absent(tmp_path: Path) -> None:
    """A missing history store must not fail the read the projection can still serve.

    Telemetry and the graph do not depend on oneharness history, so a viewer opened
    on a machine without it still gets the run — with an empty conversation list.
    """
    runs = tmp_path / "runs"
    _active_run(runs, "demo")
    app = create_app(runs, oneharness_bin=str(tmp_path / "definitely-not-installed"))

    with _serve(app) as base:
        client = httpx.Client(base_url=base, timeout=30)

        detail = client.get("/api/v1/runs/demo").json()
        assert detail["run"]["run_id"] == "demo"
        assert detail["rounds"][0]["node_states"] == {"api": "running"}
        assert detail["conversations"] == []

        # Addressing a conversation is then an ordinary 404, not a crash.
        missing = client.get("/api/v1/runs/demo/conversations/agent-native")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "run_not_found"
