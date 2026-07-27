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

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn

from orchestrator import REPO_ROOT
from orchestrator.journal import NodeId, RunId, open_journal
from orchestrator.launch import write_provenance
from orchestrator.runs import prepare_round, write_result
from orchestrator.server import create_app

FAKE_ONEHARNESS = REPO_ROOT / "tests" / "e2e" / "fake_oneharness.py"
LAUNCH_ID = "a" * 32


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

        # A reconnect that resumes from a cursor gets no snapshot, only new changes.
        import shutil

        with client.stream("GET", "/api/v1/events", headers={"Last-Event-ID": "5"}) as response:
            lines = response.iter_lines()
            shutil.rmtree(run_dir)
            removed = _read_frames(lines, until="run.removed")
            assert removed[-1]["event"] == "run.removed"
            assert json.loads(removed[-1]["data"]) == {"run_id": "demo"}
            assert all(frame.get("event") != "snapshot" for frame in removed)

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
