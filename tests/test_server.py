"""Deterministic unit coverage for the SSE generator and its helpers.

The socket-level journeys live in ``tests/e2e/test_server_e2e.py``; this drives the
``_event_stream`` async generator directly so every branch — snapshot, change,
removal, heartbeat, resume, and client disconnect — is exercised without racing a
real poll clock.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import anyio
import pytest

import orchestrator.server as server
from orchestrator.journal import NodeId, RunId, open_journal
from orchestrator.read_model import InvalidRunId, ProjectionFailed, RunNotFound
from orchestrator.runs import prepare_round

ABSENT = "definitely-not-a-real-oneharness-binary"


class _Request:
    """A minimal ASGI request whose disconnect flips after N poll checks."""

    def __init__(self, disconnect_after: int) -> None:
        self._limit = disconnect_after
        self._calls = 0
        self.headers: dict[str, str] = {}

    async def is_disconnected(self) -> bool:
        self._calls += 1
        return self._calls > self._limit


def _active_run(runs_dir: Path, run_id: str) -> Path:
    run_dir = runs_dir / run_id
    prepare_round(run_dir, {"tasks": [{"id": "api", "task": "ship"}]})
    journal = open_journal(run_dir, RunId(run_id), 1)
    journal.append(
        "node-added", detail={"definition": {"id": "api", "persona": "engineer", "task": "ship"}}
    )
    journal.append("round-started", detail={"plan": {"schema_version": 3, "concurrency": 1}})
    journal.append("node-started", node=NodeId("api"), detail={"persona": "engineer"})
    return run_dir


def test_sse_and_error_helpers() -> None:
    framed = server._sse(7, "run.changed", {"run_id": "demo"})
    assert framed == 'id: 7\nevent: run.changed\ndata: {"run_id":"demo"}\n\n'

    response = server._error(404, "run_not_found", "gone")
    assert response.status_code == 404
    assert json.loads(bytes(response.body)) == {
        "error": {"code": "run_not_found", "message": "gone"}
    }


@pytest.mark.parametrize(
    "exc,status,code",
    [
        (InvalidRunId("x"), 422, "invalid_run_id"),
        (RunNotFound("x"), 404, "run_not_found"),
        (ProjectionFailed("x"), 409, "projection_error"),
    ],
)
def test_status_for(exc: Exception, status: int, code: str) -> None:
    assert server._status_for(exc) == (status, code)  # type: ignore[arg-type]


def test_signatures_missing_dir_and_watch_filter(tmp_path: Path) -> None:
    assert server._signatures(tmp_path / "missing", None) == {}
    runs = tmp_path / "runs"
    _active_run(runs, "demo")
    _active_run(runs, "other")
    only = server._signatures(runs, "demo")
    assert set(only) == {"demo"}


def test_event_stream_snapshot_change_removal_then_disconnect(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    run_dir = _active_run(runs, "demo")

    async def body() -> None:
        gen = server._event_stream(_Request(3), runs, "demo", None, ABSENT, 0.0, 100.0)
        snapshot = await gen.__anext__()
        assert "event: snapshot" in snapshot
        assert json.loads(snapshot.split("data: ", 1)[1])["runs"][0]["run_id"] == "demo"

        # Grow the journal -> the next poll reports the run changed.
        open_journal(run_dir, RunId("demo"), 1).append(
            "human-attested", node=NodeId("api"), detail={"ref": "api"}
        )
        change = await gen.__anext__()
        assert "event: run.changed" in change
        assert json.loads(change.split("data: ", 1)[1]) == {"run_id": "demo", "round": 1}

        # Remove the run -> the next poll reports it gone.
        shutil.rmtree(run_dir)
        removed = await gen.__anext__()
        assert "event: run.removed" in removed
        assert json.loads(removed.split("data: ", 1)[1]) == {"run_id": "demo"}

        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()  # is_disconnected now returns True

    anyio.run(body)


def test_event_stream_resume_skips_snapshot_and_heartbeats(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _active_run(runs, "demo")

    async def body() -> None:
        gen = server._event_stream(_Request(5), runs, None, 5, ABSENT, 0.0, 0.0)
        first = await gen.__anext__()
        assert first == ": keep-alive\n\n"  # resume mode: no snapshot, idle heartbeat

    anyio.run(body)
