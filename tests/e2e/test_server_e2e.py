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
from orchestrator.journal import JournalOperation, NodeId, RunId, StepId, open_journal
from orchestrator.launch import DEFAULT_MAX_AGE_SECONDS, provenance_path, write_provenance
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
        # A present run with no such transcript is distinct from a missing run, so a
        # viewer can tell "still being written" from "stop polling".
        missing = client.get("/api/v1/runs/demo/conversations/nope")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "conversation_not_found"

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

            # So does a monitor PR observation, which never touches the journal —
            # without this the UI would sit on a stale PR status forever.
            save_snapshot(
                run_dir,
                DetailSnapshot(prs={"api": PrDetail(number=11, state="MERGED").to_record()}),
            )
            from_snapshot = _read_frames(lines, until="run.changed")
            assert json.loads(from_snapshot[-1]["data"])["run_id"] == "demo"

            # And so does a log the run appends outside the event stream.
            (run_dir / "orchestrator").mkdir(parents=True, exist_ok=True)
            (run_dir / "orchestrator" / "gate.log").write_text("gate: passed\n", encoding="utf-8")
            from_log = _read_frames(lines, until="run.changed")
            assert json.loads(from_log[-1]["data"])["run_id"] == "demo"

            # And a rewritten launch record, which changes the launcher the detail
            # view joins to without touching the journal.
            (run_dir / "launch.json").write_text(
                json.dumps({"schema_version": 2, "run_id": "demo", "launch": {}}),
                encoding="utf-8",
            )
            from_launch = _read_frames(lines, until="run.changed")
            assert json.loads(from_launch[-1]["data"])["run_id"] == "demo"

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

        # A record replaced out from under us — the file lives outside the repo — is
        # refused on read too, rather than serving a smuggled session id.
        record = provenance_path(LAUNCH_ID)
        tampered = json.loads(record.read_text(encoding="utf-8"))
        tampered["launcher_session_id"] = "smuggled\nsecond-line"
        tampered["started_at"] = datetime.now(UTC).isoformat()
        record.write_text(json.dumps(tampered), encoding="utf-8")
        replaced = client.get("/api/v1/runs/demo").json()
        assert replaced["launch"] == {"launch_id": LAUNCH_ID, "launcher": "unknown"}

        # So is a far-future record, which no expiry check could ever retire. It is a
        # forged or corrupt stamp rather than clock skew, so the join degrades too.
        future = json.loads(record.read_text(encoding="utf-8"))
        future["launcher_session_id"] = "future-session"
        future["started_at"] = (datetime.now(UTC) + timedelta(days=365)).isoformat()
        record.write_text(json.dumps(future), encoding="utf-8")
        dated = client.get("/api/v1/runs/demo").json()
        assert dated["launch"] == {"launch_id": LAUNCH_ID, "launcher": "unknown"}
        assert "future-session" not in json.dumps(dated)

        # `after` is the query-parameter form of a resume cursor: like a valid
        # Last-Event-ID it continues the numbering, and still gets a snapshot.
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


def test_cli_refuses_a_nonloopback_bind_and_otherwise_serves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`just telemetry-server` runs this console script; drive it as a real process."""
    runs = tmp_path / "runs"
    _active_run(runs, "demo")
    monkeypatch.setenv("FAKE_ONEHARNESS_STORE", str(_history_store(tmp_path, "demo")))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
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

    # The loopback default serves, and --oneharness-bin / --expose-launcher-session-id
    # reach the app the process builds, not just create_app's keyword arguments.
    write_provenance(
        launch_id=LAUNCH_ID,
        launcher="codex",
        launcher_session_id="cli-session",
        repository_identity="local/app",
    )
    with _serve_cli(
        "--runs-dir",
        str(runs),
        "--oneharness-bin",
        str(_oneharness_bin(tmp_path)),
        "--expose-launcher-session-id",
    ) as base:
        listed = httpx.get(f"{base}/api/v1/runs", timeout=30).json()
        assert [row["run_id"] for row in listed["runs"]] == ["demo"]
        detail = httpx.get(f"{base}/api/v1/runs/demo", timeout=30).json()
        assert detail["launch"]["launcher_session_id"] == "cli-session"
        assert {c["attribution"]["agentRole"] for c in detail["conversations"]} == {
            "worker",
            "judge",
        }

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

            # Rewriting an existing turn in place must invalidate too: the turn a
            # live agent is still writing keeps its position while its text grows,
            # so a turn *count* would report nothing until the next turn began.
            record.write_text(
                record.read_text(encoding="utf-8").replace("second turn", "second turn, revised"),
                encoding="utf-8",
            )
            edited = _read_frames(lines, until="conversation.changed")
            assert edited[-1]["event"] == "conversation.changed"
            assert json.loads(edited[-1]["data"]) == {"run_id": "demo"}


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
        assert missing.json()["error"]["code"] == "conversation_not_found"


def test_detail_skips_an_unreadable_session_and_serves_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One corrupt transcript must not blind the view to every healthy one."""
    runs = tmp_path / "runs"
    _active_run(runs, "demo")
    store_path = _history_store(tmp_path, "demo")
    store = json.loads(store_path.read_text(encoding="utf-8"))
    store["sessions"].append(
        {
            "id": "broken-native",
            "name": "engineer-broken",
            "project": str(tmp_path),
            "started": "2026-07-19T00:00:09Z",
            "path": str(tmp_path / "never-written.jsonl"),
            "labels": {"run_id": "demo", "node": "api", "role": "agent"},
        }
    )
    store_path.write_text(json.dumps(store), encoding="utf-8")
    monkeypatch.setenv("FAKE_ONEHARNESS_STORE", str(store_path))
    app = create_app(runs, oneharness_bin=str(_oneharness_bin(tmp_path)))

    with _serve(app) as base:
        detail = httpx.Client(base_url=base, timeout=30).get("/api/v1/runs/demo").json()

    ids = {item["conversation"]["id"] for item in detail["conversations"]}
    assert ids == {"agent-native", "judge-native"}  # the unreadable session is skipped


def test_a_symlinked_run_id_cannot_read_outside_the_configured_root(tmp_path: Path) -> None:
    """A valid opaque id may still name a symlink; containment is what stops it."""
    runs = tmp_path / "runs"
    _active_run(runs, "demo")
    outside = tmp_path / "outside"
    _active_run(outside, "secret")
    (runs / "escape").symlink_to(outside / "secret", target_is_directory=True)

    app = create_app(runs, oneharness_bin=str(tmp_path / "definitely-not-installed"))

    with _serve(app) as base:
        client = httpx.Client(base_url=base, timeout=30)

        assert client.get("/api/v1/runs/escape").status_code == 404
        assert client.get("/api/v1/runs/escape/conversations/any").status_code == 404
        # And it is absent from the list the UI enumerates.
        listed = client.get("/api/v1/runs", params={"include_settled": "true"}).json()
        assert [row["run_id"] for row in listed["runs"]] == ["demo"]


def test_run_list_serves_healthy_runs_beside_a_corrupt_one(tmp_path: Path) -> None:
    """One unreadable run must not blind the UI to every other run in the root."""
    runs = tmp_path / "runs"
    _active_run(runs, "healthy")
    prepare_round(runs / "broken", {"tasks": [{"id": "api", "task": "x"}]})
    (runs / "broken" / "round-01" / "result.json").write_text("{ not json", encoding="utf-8")

    app = create_app(runs, oneharness_bin=str(tmp_path / "definitely-not-installed"))

    with _serve(app) as base:
        listed = (
            httpx.Client(base_url=base, timeout=30)
            .get("/api/v1/runs", params={"include_settled": "true"})
            .json()
        )

    assert [row["run_id"] for row in listed["runs"]] == ["healthy"]


def test_run_that_recorded_no_event_serves_a_null_last_event(tmp_path: Path) -> None:
    """A just-launched run has no last event; the API says null, never an empty string.

    A prepared round with no journal is exactly what a `repo-plan` run looks like the
    moment it launches. The empty string this used to serve failed the published
    contract's non-empty-string rule, and because the client validates the whole list
    in one parse, those runs took every healthy run in the response down with them.
    """
    runs = tmp_path / "runs"
    _active_run(runs, "with-events")
    prepare_round(runs / "eventless", {"tasks": [{"id": "api", "task": "ship"}]})
    assert not (runs / "eventless" / "events.jsonl").exists()

    app = create_app(runs, oneharness_bin=str(tmp_path / "definitely-not-installed"))

    with _serve(app) as base:
        client = httpx.Client(base_url=base, timeout=30)
        listed = client.get("/api/v1/runs", params={"include_settled": "true"}).json()
        detail = client.get("/api/v1/runs/eventless").json()

    rows = {row["run_id"]: row for row in listed["runs"]}
    # The mixed set is served whole: the eventless run is listed beside the one that
    # has events, and neither displaces the other.
    assert sorted(rows) == ["eventless", "with-events"]
    assert rows["eventless"]["last_event"] is None
    assert rows["with-events"]["last_event"] == "node-started"
    assert "last_progress_at" not in rows["eventless"]
    assert detail["run"]["last_event"] is None
    assert detail["rounds"] == []


def test_detail_maps_transcript_edge_cases_to_the_ui_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mapping a client actually receives, over HTTP, for awkward history records."""
    runs = tmp_path / "runs"
    _active_run(runs, "demo")
    record = tmp_path / "edge.jsonl"
    record.write_text(
        json.dumps(
            {
                "session": "edge-native",
                "name": "engineer-edge",
                "harness": "codex",
                "timestamp": "2026-07-19T00:00:00Z",
                "prompt": "go",
                "text": "stopped early",
                "status": "timeout",
                "thinking": {"b": 1, "a": 2},
                # Bools and non-finite numbers are not counters; nulls are meaningful.
                "usage": {"input_tokens": True, "output_tokens": None, "cost_usd": 0.5},
                "events": ["not-a-dict", {"kind": "tool_call", "name": "just", "output": 7}],
                "unrecognized_field": {"kept": True},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    store = tmp_path / "store.json"
    store.write_text(
        json.dumps(
            {
                "sessions": [
                    {
                        "id": "edge-native",
                        "name": "engineer-edge",
                        "project": str(tmp_path),
                        "started": "2026-07-19T00:00:00Z",
                        "path": str(record),
                        "labels": {"run_id": "demo", "node": "api", "role": "agent"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAKE_ONEHARNESS_STORE", str(store))
    app = create_app(runs, oneharness_bin=str(_oneharness_bin(tmp_path)))

    with _serve(app) as base:
        detail = httpx.Client(base_url=base, timeout=30).get("/api/v1/runs/demo").json()

    served = detail["conversations"][0]
    turn = served["conversation"]["turns"][0]
    assert served["conversation"]["state"] == "stopped"  # timeout -> stopped
    assert served["conversation"]["canContinue"] is False  # no session_id to resume
    assert turn["reasoning"] == '{\n  "a": 2,\n  "b": 1\n}'  # structured thinking, JSON-encoded
    assert turn["usage"] == {"outputTokens": None, "costUsd": 0.5}  # bool dropped, null kept
    assert turn["tools"] == [{"index": 1, "kind": "tool_call", "name": "just", "output": None}]
    assert turn["unknown"] == {"unrecognized_field": {"kept": True}}  # nothing silently dropped
    assert turn["model"] is None
    # The role was not labelled, so it is inferred and marked as such.
    assert served["attribution"] == {
        "transportRole": "agent",
        "agentRole": "worker",
        "launcher": "unknown",
        "runId": "demo",
        "nodeId": "api",
        "inferred": True,
    }


def test_a_symlinked_log_cannot_stream_a_file_outside_the_run(tmp_path: Path) -> None:
    """A log path is fixed, but the file at it can point anywhere."""
    runs = tmp_path / "runs"
    run_dir = _active_run(runs, "demo")
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("credentials that are not this run's log\n", encoding="utf-8")
    (run_dir / "orchestrator").mkdir(parents=True, exist_ok=True)
    (run_dir / "orchestrator" / "gate.log").symlink_to(secret)
    # A real log beside it still serves, so this is containment, not a blanket refusal.
    (run_dir / "orchestrator" / "stderr.log").write_text("real tail\n", encoding="utf-8")

    app = create_app(runs, oneharness_bin=str(tmp_path / "definitely-not-installed"))

    with _serve(app) as base:
        detail = httpx.Client(base_url=base, timeout=30).get("/api/v1/runs/demo").json()

    assert detail["logs"] == {"orchestrator_stderr": "real tail\n"}
    assert "credentials" not in json.dumps(detail)


def test_an_unexpected_read_failure_keeps_the_error_envelope(tmp_path: Path) -> None:
    """An unanticipated failure must not break the envelope or leak the path."""
    runs = tmp_path / "runs"
    run_dir = _active_run(runs, "demo")
    # An unreadable authoritative journal is a real filesystem condition the read
    # model does not anticipate: it handles a *corrupt* journal, not an unopenable one.
    journal = run_dir / "events.jsonl"
    journal.chmod(0o000)
    try:
        journal.read_bytes()
    except OSError:
        pass
    else:  # pragma: no cover - running as root
        journal.chmod(0o644)
        pytest.skip("this process bypasses file permissions")

    app = create_app(runs, oneharness_bin=str(tmp_path / "definitely-not-installed"))
    try:
        with _serve(app) as base:
            response = httpx.Client(base_url=base, timeout=30).get("/api/v1/runs/demo")
    finally:
        journal.chmod(0o644)

    assert response.status_code == 500
    body = response.json()
    assert body == {"error": {"code": "read_error", "message": "unexpected read failure"}}
    assert str(tmp_path) not in json.dumps(body)  # no filesystem path leaked


def test_conversation_polling_survives_a_failing_history_subprocess(tmp_path: Path) -> None:
    """A history binary that errors on every call must not end a watching stream.

    The runs root is a separate source; a viewer should keep receiving run
    invalidations even when transcripts are temporarily unreadable.
    """
    runs = tmp_path / "runs"
    run_dir = _active_run(runs, "demo")
    broken = tmp_path / "oneharness"
    broken.write_text("#!/bin/sh\necho 'history backend exploded' >&2\nexit 3\n", encoding="utf-8")
    broken.chmod(0o755)

    app = create_app(
        runs,
        oneharness_bin=str(broken),
        poll_interval=0.05,
        heartbeat_interval=30.0,
        conversation_interval=0.05,
    )

    with _serve(app) as base:
        client = httpx.Client(base_url=base, timeout=15)
        with client.stream("GET", "/api/v1/events?run_id=demo") as response:
            assert response.status_code == 200
            lines = response.iter_lines()
            assert _read_frames(lines, until="snapshot")[-1]["event"] == "snapshot"

            # Conversation polls are failing throughout; run invalidation still works.
            _settle(run_dir, "demo")
            changed = _read_frames(lines, until="run.changed")
            assert json.loads(changed[-1]["data"])["run_id"] == "demo"
            assert all(frame.get("event") != "conversation.changed" for frame in changed)


def test_a_directory_with_no_recorded_round_is_not_a_run(tmp_path: Path) -> None:
    """An empty or unrelated directory under the root reads as absent, not as a run."""
    runs = tmp_path / "runs"
    _active_run(runs, "demo")
    (runs / "scratch").mkdir()  # a bare directory that never recorded a round

    app = create_app(runs, oneharness_bin=str(tmp_path / "definitely-not-installed"))

    with _serve(app) as base:
        client = httpx.Client(base_url=base, timeout=30)

        for path in ("/api/v1/runs/scratch", "/api/v1/runs/scratch/conversations/any"):
            response = client.get(path)
            assert response.status_code == 404, path
            # Not conversation_not_found: there is no run to have conversations in.
            assert response.json()["error"]["code"] == "run_not_found", path

        listed = client.get("/api/v1/runs", params={"include_settled": "true"}).json()
        assert [row["run_id"] for row in listed["runs"]] == ["demo"]


LOCK_WAITS = 1200


def _lifecycle_run(runs_dir: Path, run_id: str) -> Path:
    """A lifecycle run recorded the way the executor records one, still in flight.

    It carries the whole span vocabulary a reader cares about — a step, a merge-path
    verification, a PR that has not merged — plus the contention a real run drowns
    in, so the served timeline is exercised at the shape and the scale it must hold.
    """
    run_dir = runs_dir / run_id
    prepare_round(
        run_dir,
        {
            "tasks": [
                {"id": "api", "repo": "acme/app", "task": "ship"},
                {"id": "docs", "repo": "acme/app", "task": "document"},
                {"id": "signoff", "kind": "human", "task": "Approve the release"},
            ]
        },
    )
    journal = open_journal(run_dir, RunId(run_id), 1)
    node = NodeId("api")
    step = StepId("implement")
    journal.append(
        "node-added",
        detail={"definition": {"id": "api", "persona": "engineer", "task": "ship"}},
    )
    journal.append(
        "node-added",
        detail={"definition": {"id": "docs", "persona": "engineer", "task": "document"}},
    )
    journal.append(
        "node-added",
        detail={"definition": {"id": "signoff", "kind": "human", "task": "Approve the release"}},
    )
    journal.append("round-started", detail={"plan": {"schema_version": 3, "concurrency": 1}})
    journal.append("setup-finished", node=node, detail={"seconds": 3.5, "checkout": "acme/app"})
    journal.append("node-started", node=node, detail={"node_kind": "lifecycle"})
    journal.append(
        "merge-gate-coverage",
        node=node,
        detail={"identity": "acme/app", "pre_push_hook": ".githooks/pre-push"},
    )
    journal.append(
        "branch-discovered",
        node=node,
        detail={"repo": "acme/app", "branch": "feature/api", "base_branch": "main"},
    )
    journal.append(
        "step-started", node=node, step=step, detail={"step_kind": "agent", "persona": "engineer"}
    )
    journal.append("step-settled", node=node, step=step, detail={"status": "done", "turns": 6})
    journal.append("verification-started", node=node, detail={"label": "branch push feature/api"})
    journal.append(
        "verification-finished",
        node=node,
        detail={
            "label": "branch push feature/api",
            "ok": True,
            "command": ["just", "gate"],
            "output_tail": "gate: passed with a secret token in the tail",
            "log_path": "runs/demo/round-01/api/gate.log",
        },
    )
    journal.append(
        "pr-created",
        node=node,
        detail={"repo": "acme/app", "pr": "https://x/pull/7", "number": 7, "base": "main"},
    )
    journal.append(
        "pr-checks-observed",
        node=node,
        detail={"repo": "acme/app", "pr": "https://x/pull/7", "state": "OPEN", "merged": False},
    )
    # The contention a real run records: thousands of these against a handful of
    # everything else. They must reach the client as one span, not as one item each.
    journal.append_batch(
        [
            JournalOperation(
                kind="lock-wait", detail={"identity": "merge:acme/app", "seconds": 0.5}
            )
            for _ in range(LOCK_WAITS)
        ],
        node=node,
    )
    # A second node that reached the rest of the recorded vocabulary: it drafted a PR
    # (badly), resolved a conflict, waited on a human, and published.
    docs = NodeId("docs")
    journal.append("node-started", node=docs, detail={"node_kind": "lifecycle"})
    journal.append("conflict-resolution-started", node=docs, detail={"base": "main"})
    journal.append("conflict-resolution-finished", node=docs, detail={"ok": True})
    journal.append("pr-drafting-started", node=docs, detail={"base": "main"})
    journal.append("pr-drafting-fallback", node=docs, detail={"reason": "drafting worker died"})
    journal.append(
        "pr-drafting-finished",
        node=docs,
        detail={"completed": False, "reason": "drafting worker died"},
    )
    # The one action only a person can take: a top-level human node, waited on and
    # then attested by the planner.
    signoff = NodeId("signoff")
    journal.append(
        "human-waiting",
        node=signoff,
        detail={
            "step_kind": "human",
            "result": {
                "kind": "human",
                "status": "waiting",
                "task": "Approve the release",
                "human_actions": [
                    {
                        "ref": "signoff",
                        "task": "Approve the release",
                        "unblocks": [],
                        "unblocks_publication": False,
                    }
                ],
            },
        },
    )
    journal.append("human-attested", node=signoff, detail={"ref": "signoff"})
    journal.append(
        "publication-finished",
        node=docs,
        detail={"repo": "acme/app", "pr": "https://x/pull/8", "branch": "feature/docs"},
    )
    journal.append(
        "node-settled",
        node=docs,
        detail={
            "status": "done",
            "result": {
                "status": "done",
                "artifacts": {"worker_report": "runs/demo/round-01/docs/report.json"},
            },
        },
    )
    save_snapshot(
        run_dir,
        DetailSnapshot(
            prs={
                "pr:acme/app#7": PrDetail(
                    number=7, url="https://x/pull/7", state="BLOCKED", draft=False
                ).to_record()
            }
        ),
    )
    return run_dir


def _lifecycle_history(tmp_path: Path, run_id: str, base: datetime) -> Path:
    """A store with a worker, the lint run nested under it, and a run-level check-in.

    Session times are relative to ``base`` — the moment the run fixture finished
    journaling — because history and the journal are written by different processes
    against the same wall clock, and the nesting the timeline resolves is exactly
    that overlap. Pinned literals would place every session before the whole run.
    """

    def at(seconds: int) -> str:
        return (base + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")

    sessions = []
    for session_id, name, role, agent_role, turns, labels in (
        (
            "worker-native",
            "engineer-ship",
            "agent",
            "worker",
            (1, 40),
            {"node": "api", "step": "implement", "round": "1", "persona": "engineer"},
        ),
        # Nested inside the worker's turns: this is the lint run that dispatch drove.
        ("lint-native", "llmlint-diff", "llmlint", "worker", (20,), {"node": "api", "round": "1"}),
        (
            "check-in-native",
            "check-in-round-1",
            "agent",
            "check-in",
            (60,),
            {"round": "1", "persona": "check-in"},
        ),
    ):
        record = tmp_path / f"{session_id}.jsonl"
        record.write_text(
            "".join(
                json.dumps(
                    {
                        "session": session_id,
                        "name": name,
                        "project": str(tmp_path),
                        "harness": "codex",
                        "model": "gpt",
                        "timestamp": at(offset),
                        "prompt": "go",
                        "text": "a transcript body that must never reach the timeline payload",
                        "status": "ok",
                        "session_id": session_id,
                    }
                )
                + "\n"
                for offset in turns
            ),
            encoding="utf-8",
        )
        sessions.append(
            {
                "id": session_id,
                "name": name,
                "project": str(tmp_path),
                "started": at(turns[0]),
                "path": str(record),
                "labels": {"run_id": run_id, "role": role, "agent_role": agent_role, **labels},
            }
        )
    # A session history recorded with a timestamp nothing can place in time. It is
    # well-formed enough to reach the mapper, so only the fold can drop it.
    undatable = tmp_path / "undatable-native.jsonl"
    undatable.write_text(
        json.dumps(
            {
                "session": "undatable-native",
                "name": "engineer-undatable",
                "project": str(tmp_path),
                "harness": "codex",
                "timestamp": "whenever",
                "prompt": "go",
                "text": "a transcript body that must never reach the timeline payload",
                "status": "ok",
                "session_id": "undatable-native",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    sessions.append(
        {
            "id": "undatable-native",
            "name": "engineer-undatable",
            "project": str(tmp_path),
            "started": "whenever",
            "path": str(undatable),
            "labels": {
                "run_id": run_id,
                "role": "agent",
                "agent_role": "worker",
                "node": "api",
                "round": "1",
            },
        }
    )
    store = tmp_path / "timeline-store.json"
    store.write_text(json.dumps({"sessions": sessions}), encoding="utf-8")
    return store


def test_timeline_endpoint_serves_one_ordered_run_history_over_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole journey a viewer takes: one timeline fetch, and detail without transcripts."""
    runs = tmp_path / "runs"
    run_dir = _lifecycle_run(runs, "demo")
    store = _lifecycle_history(tmp_path, "demo", datetime.now(UTC))
    monkeypatch.setenv("FAKE_ONEHARNESS_STORE", str(store))
    app = create_app(runs, oneharness_bin=str(_oneharness_bin(tmp_path)))
    before = _tree(runs)

    with _serve(app) as base:
        client = httpx.Client(base_url=base, timeout=30)

        body = client.get("/api/v1/runs/demo/timeline")
        assert body.status_code == 200
        timeline = body.json()
        assert timeline["api_version"] == 1
        assert timeline["run_id"] == "demo"
        spans = timeline["spans"]
        by_id = {span["id"]: span for span in spans}
        by_kind: dict[str, list[dict[str, object]]] = {}
        for span in spans:
            by_kind.setdefault(span["kind"], []).append(span)

        # One span per recorded activity, and 1200 lock waits as exactly one rollup.
        assert sorted(by_kind) == [
            "conflict-resolution",
            "dispatch",
            "human-wait",
            "node",
            "pr-drafting",
            "publication",
            "rollup",
            "round",
            "step",
            "verification",
        ]
        assert len(spans) < 25, "the timeline must stay bounded by the graph, not by contention"
        rollup = by_kind["rollup"][0]
        assert rollup["label"] == "lock-wait"
        assert rollup["count"] == LOCK_WAITS
        assert rollup["total_duration_ms"] == LOCK_WAITS * 500

        nodes = {span["node_id"]: span for span in by_kind["node"]}
        # The node is still running, so its span is open — that is what a live run is.
        node_span = nodes["api"]
        assert node_span["ended_at"] is None
        assert by_kind["step"][0]["parent_id"] == node_span["id"]
        assert by_kind["step"][0]["ended_at"] is not None

        # The second node reached the rest of the vocabulary, all of it closed and
        # nested under the node it ran in.
        settled = nodes["docs"]
        assert settled["ended_at"] is not None
        assert settled["status"] == "done"
        assert settled["reference"] == {
            "kind": "worker_report",
            "value": "runs/demo/round-01/docs/report.json",
        }
        drafting = by_kind["pr-drafting"][0]
        assert drafting["parent_id"] == settled["id"]
        # Drafting failure must never block publication, so it settles not-completed —
        # and the fallback that says why reads as work inside it, not beside it.
        assert drafting["status"] == "not-completed"
        assert [event["kind"] for event in drafting["events"]] == ["pr-drafting-fallback"]
        conflict = by_kind["conflict-resolution"][0]
        assert (conflict["parent_id"], conflict["status"]) == (settled["id"], "ok")
        assert conflict["ended_at"] is not None
        waited = by_kind["human-wait"][0]
        # A human node never "starts": its wait span is the node's own recorded work,
        # so it hangs off the round rather than off a node span that does not exist.
        assert waited["parent_id"] == by_kind["round"][0]["id"]
        assert (waited["node_id"], waited["status"]) == ("signoff", "attested")
        assert waited["ended_at"] is not None
        closed = next(span for span in by_kind["publication"] if span["node_id"] == "docs")
        assert closed["status"] == "finished"
        assert closed["reference"] == {"kind": "pr", "value": "https://x/pull/8"}
        assert [event["kind"] for event in closed["events"]] == ["publication-finished"]

        # The lint run reads as work inside the worker dispatch, not beside it.
        dispatches = {span["reference"]["value"]: span for span in by_kind["dispatch"]}
        assert dispatches["lint-native"]["parent_id"] == dispatches["worker-native"]["id"]
        # And the worker itself hangs off the step it was labelled with.
        assert dispatches["worker-native"]["parent_id"] == by_kind["step"][0]["id"]
        # A check-in names a round and no node, so it lands on the round.
        assert dispatches["check-in-native"]["parent_id"] == by_kind["round"][0]["id"]
        turns = dispatches["worker-native"]["events"]
        assert [event["kind"] for event in turns] == ["conversation-turn"] * 2
        # A session whose recorded start cannot be placed in time is omitted rather
        # than given an invented one — every other transcript still reaches the view.
        assert set(dispatches) == {"worker-native", "lint-native", "check-in-native"}

        # Heavy content is addressed, never inlined — and each address resolves.
        verification = by_kind["verification"][0]
        assert verification["reference"] == {
            "kind": "gate_log",
            "value": "runs/demo/round-01/api/gate.log",
        }
        publication = next(span for span in by_kind["publication"] if span["node_id"] == "api")
        assert publication["reference"] == {"kind": "pr", "value": "https://x/pull/7"}
        # The publication has not closed, so it shows the state the monitor observed.
        assert publication["status"] == "BLOCKED"
        assert [event["kind"] for event in publication["events"]] == [
            "pr-created",
            "pr-checks-observed",
        ]
        rendered = json.dumps(timeline)
        assert "a transcript body that must never reach the timeline payload" not in rendered
        assert "a secret token in the tail" not in rendered

        # Ordering and normalization hold across the whole payload.
        assert [span["started_at"] for span in spans] == sorted(
            span["started_at"] for span in spans
        )
        assert all(span["started_at"].endswith("+00:00") for span in spans)
        for span in spans:
            assert span.get("parent_id") in {None, *by_id}

        # Detail without transcripts: same api_version, same required fields, no bodies.
        lean = client.get("/api/v1/runs/demo", params={"include_conversations": "false"}).json()
        assert lean["conversations"] == []
        assert lean["api_version"] == 1
        assert lean["run"]["run_id"] == "demo"
        assert lean["rounds"][0]["node_states"] == {
            "api": "running",
            "docs": "done",
            "signoff": "waiting",
        }
        assert "details" in lean
        # The default is unchanged: a client that asks for nothing still gets them.
        full = client.get("/api/v1/runs/demo").json()
        # The detail view still serves the undatable transcript: only its *position in
        # time* is unknown, and this payload does not order by it.
        assert {item["conversation"]["id"] for item in full["conversations"]} == {
            "worker-native",
            "lint-native",
            "check-in-native",
            "undatable-native",
        }

        # A malformed opt-out is refused in the same envelope as any other bad query,
        # rather than being read as "false" and quietly serving a smaller payload.
        malformed = client.get("/api/v1/runs/demo", params={"include_conversations": "sometimes"})
        assert malformed.status_code == 422
        assert malformed.json()["error"]["code"] == "invalid_request"

        # Every trust boundary behaves like the rest of this read model.
        assert client.get("/api/v1/runs/bad!id/timeline").status_code == 422
        absent = client.get("/api/v1/runs/absent/timeline")
        assert absent.status_code == 404
        assert absent.json()["error"]["code"] == "run_not_found"

        journal = run_dir / "events.jsonl"
        journal.write_text(
            journal.read_text(encoding="utf-8") + '{"kind":"bogus"}\n', encoding="utf-8"
        )
        corrupt = client.get("/api/v1/runs/demo/timeline")
        assert corrupt.status_code == 409
        assert corrupt.json()["error"]["code"] == "projection_error"
        journal.write_text(
            journal.read_text(encoding="utf-8").replace('{"kind":"bogus"}\n', ""), encoding="utf-8"
        )

    # Serving the timeline is a read: the run directory it folded is byte-identical.
    assert _tree(runs) == before


def test_timeline_degrades_when_history_and_the_snapshot_are_unusable(tmp_path: Path) -> None:
    """A viewer still gets the recorded graph when the optional sources cannot be read.

    Neither source is evidence a decision is made on: history lives outside the runs
    root and the snapshot is an optimization over observation, so a machine without
    oneharness and a snapshot this build cannot parse must both degrade to an empty
    contribution rather than failing a read the journal can serve.
    """
    runs = tmp_path / "runs"
    run_dir = _lifecycle_run(runs, "demo")
    snapshot = run_dir / "monitor" / "details.json"
    snapshot.write_text(json.dumps({"version": 999, "prs": "not a mapping"}), encoding="utf-8")
    app = create_app(runs, oneharness_bin=str(tmp_path / "definitely-not-installed"))

    with _serve(app) as base:
        timeline = httpx.Client(base_url=base, timeout=30).get("/api/v1/runs/demo/timeline").json()

    open_publication = next(
        span
        for span in timeline["spans"]
        if span["kind"] == "publication" and span["node_id"] == "api"
    )
    # Nothing observed: the open publication carries no state it could not have read.
    assert "status" not in open_publication
    assert open_publication["reference"] == {"kind": "pr", "value": "https://x/pull/7"}
    # And the verdict the journal itself recorded is unaffected.
    assert (
        next(
            span["status"]
            for span in timeline["spans"]
            if span["kind"] == "publication" and span["node_id"] == "docs"
        )
        == "finished"
    )

    kinds = {span["kind"] for span in timeline["spans"]}
    assert "dispatch" not in kinds  # no transcripts to place
    assert {
        "round",
        "node",
        "step",
        "verification",
        "publication",
        "pr-drafting",
        "conflict-resolution",
        "human-wait",
        "rollup",
    } <= kinds
