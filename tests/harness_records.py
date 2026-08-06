"""The real provider history records the launch smoke validates.

Single source for both smoke suites — `tests/test_smoke.py` and
`tests/e2e/test_smoke_validation_e2e.py`. Each shape below is a live oneharness
record captured from one real turn on the pinned release, minus the transcript
fields the smoke never reads. Neither harness reports every native field, and the
two disagree about *which* fields they omit: that asymmetry is exactly what the
launch contract must tolerate, so a fixture that invents the missing fields hides
the bug the smoke exists to catch.

The refusal shapes below were captured the same way, from real sessions where the
agent chain fell through an exhausted subscription and ran the turn on the next
identity. A chain records every candidate it attempts, so those records are what a
*healthy* launch path leaves behind on this host — which is precisely why they are
real captures rather than invented ones.

Restating a shape inside a suite is how one suite ends up passing against a record
oneharness no longer writes. When the real record contract changes, re-capture the
shape here and both suites move together.
"""

from __future__ import annotations

import json
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
    "failure_kind": None,
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
    "failure_kind": None,
}
#: The same claude-code shape recorded for the SECOND alternate subscription. That
#: is the identity this host's chain selects while the first one's weekly quota is
#: gone, so it is the record a healthy launch is judged by there.
CLAUDE_ALTERNATE2_RECORD: dict[str, Any] = {
    **CLAUDE_RECORD,
    "variant": "alternate2",
    "harness_id": "claude-code:alternate2",
}
#: What the accounting of a candidate that never ran the task looks like: the chain
#: classifies on this, and every counter being zero is why a fall-through can be
#: told from a turn that was billed for.
UNSPENT_USAGE: dict[str, Any] = {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "cost_usd": 0.0,
}
#: The subscription that was out of weekly quota, captured from a real chain that
#: then ran the turn on the next identity. `run_mode = "fallback"` records the
#: attempt, so this shape is what a healthy chain leaves behind ahead of the record
#: it selected — not a launch failure.
QUOTA_REFUSAL: dict[str, Any] = {
    "harness": "claude-code",
    "variant": "alternate",
    "harness_id": "claude-code:alternate",
    "model": "claude-opus-5",
    "status": "nonzero",
    "exit_code": 1,
    "duration_ms": 3949,
    "finished_at": None,
    "text": "You've hit your weekly limit · resets Aug 6, 7am (America/New_York)",
    "text_source": "stream-json:result",
    "usage": UNSPENT_USAGE,
    "failure_kind": "quota",
}
#: The same refusal from an identity this host never authenticated. It is the other
#: kind the chain moves past, and the reason an unconfigured candidate costs nothing.
AUTH_REFUSAL: dict[str, Any] = {
    **QUOTA_REFUSAL,
    "variant": "alternate2",
    "harness_id": "claude-code:alternate2",
    "text": "Failed to authenticate: OAuth session expired and could not be refreshed",
    "failure_kind": "auth",
}
#: A candidate the chain never started at all: no exit, no duration, no accounting.
SKIPPED_CANDIDATE: dict[str, Any] = {
    "harness": "claude-code",
    "variant": "alternate",
    "harness_id": "claude-code:alternate",
    "model": "claude-opus-5",
    "status": "skipped",
    "exit_code": None,
    "duration_ms": None,
    "finished_at": None,
    "text": None,
    "text_source": None,
    "usage": dict.fromkeys(UNSPENT_USAGE),
    "failure_kind": None,
}
#: The rejection the chain deliberately does NOT move past: it names a failure kind,
#: but the record carries billed work, so oneharness stops there and this smoke must
#: report it rather than reading it as a candidate that stepped aside.
RATE_LIMITED_RECORD: dict[str, Any] = {
    **QUOTA_REFUSAL,
    "text": "API rate limit exceeded",
    "usage": {**UNSPENT_USAGE, "input_tokens": 1200, "output_tokens": 340, "cost_usd": 0.21},
    "failure_kind": "rate_limit",
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


def smoke_history_chain(
    *shapes: Mapping[str, Any],
    smoke_id: str = "smoke-id",
    session: str = "smoke-session",
    **overrides: Any,
) -> str:
    """Render one session's records as the JSON lines oneharness appends for a chain.

    A fallback chain writes every candidate it attempts into ONE session, in the
    order it tried them, so the shapes here are given in that order and the last is
    the candidate that was selected. Each gets its own ``history_id`` because a
    reader that keyed on the record instead of the turn would otherwise see one.
    """
    return "".join(
        json.dumps(
            smoke_history_record(
                shape, smoke_id=smoke_id, session=session, history_id=f"turn-{index}", **overrides
            )
        )
        + "\n"
        for index, shape in enumerate(shapes, start=1)
    )


def reported_usage(shape: Mapping[str, Any], **overrides: Any) -> dict[str, Any]:
    """Return a shape's real usage block with individual counters overridden."""
    return {**shape["usage"], **overrides}
