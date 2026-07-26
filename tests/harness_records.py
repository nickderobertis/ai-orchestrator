"""The real provider history records the launch smoke validates.

Single source for both smoke suites — `tests/test_smoke.py` and
`tests/e2e/test_smoke_validation_e2e.py`. Each shape below is a live oneharness
record captured from one real turn on the pinned release, minus the transcript
fields the smoke never reads. Neither harness reports every native field, and the
two disagree about *which* fields they omit: that asymmetry is exactly what the
launch contract must tolerate, so a fixture that invents the missing fields hides
the bug the smoke exists to catch.

Restating a shape inside a suite is how one suite ends up passing against a record
oneharness no longer writes. When the real record contract changes, re-capture the
shape here and both suites move together.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from orchestrator.smoke import TASK

#: codex reports the native phase split, prices nothing, and counts no cache write.
CODEX_RECORD: dict[str, Any] = {
    "harness": "codex",
    "harness_id": "codex",
    "model": "gpt-5.6-sol",
    "duration_ms": 2969,
    "started_at": "2026-07-25T00:00:00Z",
    "finished_at": "2026-07-25T00:00:02.969Z",
    "model_ms": 2234,
    "tool_ms": 0,
    "time_to_first_token_ms": 2234,
    "text_source": "json:codex-agent-message",
    "usage": {
        "input_tokens": 15472,
        "output_tokens": 7,
        "cache_read_tokens": 13056,
        "cache_write_tokens": None,
        "cost_usd": None,
    },
}
#: claude-code prices its turn and counts its cache write, but records only a
#: measured duration: no ``started_at``, no phase split, and a null ``finished_at``.
CLAUDE_RECORD: dict[str, Any] = {
    "harness": "claude-code",
    "variant": "alternate",
    "harness_id": "claude-code:alternate",
    "model": "claude-opus-5",
    "duration_ms": 3455,
    "finished_at": None,
    "text_source": "stream-json:result",
    "usage": {
        "input_tokens": 2,
        "output_tokens": 8,
        "cache_read_tokens": 15268,
        "cache_write_tokens": 5546,
        "cost_usd": 0.063882,
    },
}


def smoke_history_record(
    shape: Mapping[str, Any],
    *,
    smoke_id: str = "smoke-id",
    session: str = "smoke-session",
    history_id: str = "turn-1",
    **overrides: Any,
) -> dict[str, Any]:
    """Wrap one real provider shape in the envelope oneharness persists around it.

    ``overrides`` break a single field to prove the launch contract rejects it.
    """
    return {
        "type": "run",
        "schema_version": "1.1",
        "history_id": history_id,
        "session": session,
        "name": "reply-with-exactly-smoke-ok",
        "labels": {"role": "agent", "smoke": smoke_id},
        "project": "/tmp/smoke-target",
        "timestamp": "2026-07-25T00:00:00Z",
        "prompt": TASK,
        "permission_mode": "bypass",
        "status": "ok",
        "exit_code": 0,
        "text": "smoke-ok",
        **shape,
        **overrides,
    }


def reported_usage(shape: Mapping[str, Any], **overrides: Any) -> dict[str, Any]:
    """Return a shape's real usage block with individual counters overridden."""
    return {**shape["usage"], **overrides}
