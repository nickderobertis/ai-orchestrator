"""Mapping oneharness history sessions to the DAG-UI Conversation shape."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import orchestrator.conversations as conversations
from orchestrator.conversations import (
    attribution,
    conversation,
    dag_conversation,
    run_conversations,
)
from orchestrator.history import HistoryError, HistorySession, SessionId
from orchestrator.runs import RunId


def _session(name: str = "engineer-ship", **labels: str) -> HistorySession:
    return HistorySession(
        session_id=SessionId("sess-1"),
        name=name,
        project=Path("/project/app"),
        started="2026-07-19T00:00:00Z",
        path=Path("/store/sess-1.jsonl"),
        labels=labels,
    )


def _record(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session": "native-1",
        "name": "engineer-ship",
        "project": "/project/app",
        "timestamp": "2026-07-19T00:00:00Z",
        "harness": "codex",
        "model": "gpt",
        "prompt": "do the thing",
        "text": "done",
        "status": "ok",
        "usage": {"input_tokens": 10, "output_tokens": 2, "cost_usd": 0.01},
    }
    base.update(over)
    return base


def test_conversation_maps_records_to_the_ui_shape() -> None:
    records = [
        _record(harness="codex", reasoning="because"),
        _record(
            harness="claude",
            session_id="native-1",
            status="ok",
            # null preserved, bool rejected, absent omitted.
            usage={"input_tokens": None, "cache_read_tokens": True, "output_tokens": 3},
            events=[
                {
                    "kind": "tool_call",
                    "name": "command_execution",
                    "input": {"command": "ls"},
                    "output": "a\nb",
                },
                "not-a-dict",
                {"kind": "mystery"},
            ],
            custom_field={"kept": True},
        ),
    ]
    result = conversation(_session(), records)
    assert result["id"] == "native-1"
    assert result["harnesses"] == ["codex", "claude"]  # ordered, de-duplicated
    assert result["state"] == "completed"  # ok -> completed
    assert result["canContinue"] is True  # last has session_id and is not planned/skipped
    assert len(result["turns"]) == 2
    first, second = result["turns"]
    assert first["id"] == "native-1-0"
    assert first["reasoning"] == "because"
    assert first["usage"] == {"inputTokens": 10, "outputTokens": 2, "costUsd": 0.01}
    assert second["usage"] == {"inputTokens": None, "outputTokens": 3}  # bool dropped, null kept
    assert second["tools"][0] == {
        "index": 0,
        "kind": "tool_call",
        "input": {"command": "ls"},
        "name": "command_execution",
        "output": "a\nb",
    }
    # The non-dict event at index 1 is dropped; the unsupported kind at index 2 stays visible.
    assert second["tools"][1] == {"index": 2, "kind": "mystery"}
    assert len(second["tools"]) == 2
    assert second["unknown"] == {"custom_field": {"kept": True}}  # unconsumed keys preserved


def test_conversation_handles_empty_records_and_structured_reasoning() -> None:
    empty = conversation(_session(name="fallback-name"), [])
    assert empty["id"] == "sess-1"  # falls back to the session id
    assert empty["name"] == "fallback-name"
    assert empty["state"] == "unknown"  # no records -> status passes through unchanged
    assert empty["turns"] == []
    assert empty["canContinue"] is False

    structured = conversation(_session(), [_record(reasoning={"b": 1, "a": 2}, thinking="ignored")])
    assert structured["turns"][0]["reasoning"] == '{\n  "a": 2,\n  "b": 1\n}'


def test_conversation_non_dict_usage_and_duplicate_harness() -> None:
    result = conversation(_session(), [_record(usage=None), _record()])
    assert result["turns"][0]["usage"] == {}  # non-dict usage -> empty
    assert result["harnesses"] == ["codex"]  # duplicate harness collapsed


@pytest.mark.parametrize(
    "status,state",
    [
        ("ok", "completed"),
        ("nonzero", "failed"),
        ("spawn-error", "failed"),
        ("timeout", "stopped"),
        ("skipped", "stopped"),
        ("planned", "stopped"),
        ("running", "running"),
    ],
)
def test_status_state_mapping(status: str, state: str) -> None:
    result = conversation(_session(), [_record(status=status)])
    assert result["state"] == state
    assert result["turns"][0]["status"] == state


def test_can_continue_false_for_planned_last_record() -> None:
    result = conversation(_session(), [_record(session_id="x", status="planned")])
    assert result["canContinue"] is False


def test_attribution_passes_through_labelled_role() -> None:
    session = _session(
        run_id="demo",
        node="api",
        step="build",
        role="agent",
        agent_role="pr-author",
        persona="pr-author",
        launcher="codex",
        launch_id="L1",
        round="2",
    )
    result = attribution(session, [_record(finished_at="2026-07-19T00:00:01Z")])
    assert result["agentRole"] == "pr-author"
    assert "inferred" not in result  # labelled role is authoritative
    assert result["transportRole"] == "agent"
    assert result["runId"] == "demo"
    assert result["nodeId"] == "api"
    assert result["stepId"] == "build"
    assert result["round"] == 2
    assert result["launcher"] == "codex"
    assert result["launchId"] == "L1"
    assert result["finishedAt"] == "2026-07-19T00:00:01Z"


def test_attribution_marks_unfinished_and_unknown_launcher() -> None:
    result = attribution(_session(role="agent"), [_record(finished_at=None)])
    assert result["finishedAt"] is None  # observed but not finished
    assert result["launcher"] == "unknown"
    assert result["inferred"] is True  # no agent_role label


@pytest.mark.parametrize(
    "name,labels,expected",
    [
        ("you-are-a-strict-careful-evaluator-x", {"role": "judge"}, "judge"),
        ("pr-author-session", {}, "pr-author"),
        ("draft", {"persona": "pr-author"}, "pr-author"),
        ("orchestrator-demo", {}, "orchestrator"),
        ("x", {"persona": "orchestrator"}, "orchestrator"),
        ("check-in-run", {}, "check-in"),
        ("x", {"persona": "check-in"}, "check-in"),
        ("engineer-ship", {}, "worker"),
    ],
)
def test_agent_role_inference(name: str, labels: dict[str, str], expected: str) -> None:
    result = attribution(_session(name=name, **labels), [_record()])
    assert result["agentRole"] == expected
    assert result["inferred"] is True


def test_dag_conversation_wraps_both_parts() -> None:
    wrapped = dag_conversation(_session(run_id="demo", agent_role="worker"), [_record()])
    assert set(wrapped) == {"conversation", "attribution"}
    assert wrapped["attribution"]["agentRole"] == "worker"


def test_run_conversations_selects_sorts_and_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = HistorySession(
        SessionId("w"),
        "engineer",
        Path("/p"),
        "2026-07-19T00:00:02Z",
        Path("/w.jsonl"),
        {"run_id": "demo", "node": "api", "agent_role": "worker"},
    )
    orchestrator_session = HistorySession(
        SessionId("o"),
        "orchestrator-demo",
        Path("/p"),
        "2026-07-19T00:00:00Z",
        Path("/o.jsonl"),
        {},
    )
    other = HistorySession(
        SessionId("x"),
        "engineer",
        Path("/p"),
        "2026-07-19T00:00:03Z",
        Path("/x.jsonl"),
        {"run_id": "other"},
    )
    broken = HistorySession(
        SessionId("b"),
        "engineer",
        Path("/p"),
        "2026-07-19T00:00:01Z",
        Path("/b.jsonl"),
        {"run_id": "demo"},
    )

    def fake_records(session: HistorySession) -> list[dict[str, Any]]:
        if session.session_id == "b":
            raise HistoryError("cannot read history session")
        return [_record()]

    monkeypatch.setattr(
        conversations,
        "all_sessions",
        lambda *, oneharness_bin: [worker, orchestrator_session, other, broken],
    )
    monkeypatch.setattr(conversations, "session_records", fake_records)

    result = run_conversations(RunId("demo"), oneharness_bin="x")
    # orchestrator (t=00) then worker (t=02); "other" run excluded, broken skipped.
    ids = [c["conversation"]["id"] for c in result]
    assert ids == ["native-1", "native-1"]
    assert [c["attribution"].get("agentRole") for c in result] == ["orchestrator", "worker"]


def test_run_conversations_missing_store_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    def absent(*, oneharness_bin: str) -> list[HistorySession]:
        raise HistoryError("oneharness not found — run 'just bootstrap'")

    monkeypatch.setattr(conversations, "all_sessions", absent)
    assert run_conversations(RunId("demo"), oneharness_bin="x") == []


def test_run_conversations_reraises_other_history_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*, oneharness_bin: str) -> list[HistorySession]:
        raise HistoryError("oneharness history failed: broken")

    monkeypatch.setattr(conversations, "all_sessions", boom)
    with pytest.raises(HistoryError):
        run_conversations(RunId("demo"), oneharness_bin="x")
