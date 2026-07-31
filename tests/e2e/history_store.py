"""One writer for the oneharness history store the planner's views read.

`just status` reads dispatched work through oneharness' normalized history, and
the e2e journeys that exercise it drive onejudge's `command` provider — so no
oneharness process runs and nothing populates that store. This module writes it in
oneharness' own 1.0 line format, which keeps the store one shape across every suite
that reads it instead of each restating a record oneharness may no longer write.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from pathlib import Path

_UUID_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def write_worker_session(
    path: Path,
    *,
    project: Path,
    status: str = "ok",
    name: str = "implement-status-view",
    prompt: str = "Implement the unified status view.",
    labels: Mapping[str, str] | None = None,
) -> None:
    """Write one worker session in oneharness' 1.0 event-sourced line format.

    A ``type: "event"`` tool-call line plus a ``type: "run"`` line linked by the
    run's ``history_id``; every 1.0 run status is terminal, so ``status`` picks one
    of ``ok``/``nonzero`` rather than a live state (the running/recent split is
    driven by whether the branch is still a checked-out worktree). ``labels`` is
    what a dispatch stamps through `orchestrator.labels`, so a session can be
    attributed to the tracked run that dispatched it.
    """
    run_id = str(uuid.uuid5(_UUID_NS, f"{path.stem}-status"))
    event = {
        "type": "event",
        "schema_version": "1.0",
        "run_id": run_id,
        "harness": "codex",
        "event": {
            "kind": "tool_call",
            "name": "command_execution",
            "input": {"command": "just check"},
            "output": "",
            "index": 0,
            "tool_call_id": f"{run_id}-c0",
            "started_at": "2026-07-14T12:00:00.100Z",
            "finished_at": "2026-07-14T12:00:00.200Z",
            "duration_ms": 100,
            "status": "completed",
        },
    }
    run = {
        "type": "run",
        "schema_version": "1.0",
        "history_id": run_id,
        "session": path.stem,
        "name": name,
        "labels": dict(labels or {}),
        "project": str(project),
        "timestamp": "2026-07-14T12:00:00Z",
        "harness": "codex",
        "model": "gpt-5",
        "prompt": prompt,
        "permission_mode": "bypass",
        "status": status,
        "exit_code": 0,
        "duration_ms": 2300,
        "started_at": "2026-07-14T12:00:00.000Z",
        "finished_at": "2026-07-14T12:00:02.300Z",
        "model_ms": 2000,
        "tool_ms": 100,
        "time_to_first_token_ms": 40,
        "text": "Implemented the unified view.",
        "text_source": "json:codex-agent-message",
        "usage": {
            "input_tokens": 120,
            "output_tokens": 40,
            "cache_read_tokens": 0,
            "cache_write_tokens": None,
            "cost_usd": None,
        },
        "session_id": "codex-status-thread",
        "failure_kind": None,
    }
    path.write_text(json.dumps(event) + "\n" + json.dumps(run) + "\n", encoding="utf-8")
