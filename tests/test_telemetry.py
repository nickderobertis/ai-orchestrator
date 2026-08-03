"""Contracts and derivations for the unified machine-readable run index."""

from __future__ import annotations

import json
import re
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import get_args

import pytest
from telemetry_contract import WATERFALL

import orchestrator.history as history_module
import orchestrator.telemetry as telemetry_module
from orchestrator.history import HistorySession, SessionId, SessionRole
from orchestrator.journal import NodeJournal, open_journal
from orchestrator.runs import NodeId, RunId, StepId, prepare_round, write_result
from orchestrator.telemetry import (
    SUPPORTED_HISTORY_SCHEMA_VERSIONS,
    TELEMETRY_SCHEMA_VERSION,
    Failure,
    FractionsRecord,
    LlmlintRetryMetrics,
    LlmlintRetryRate,
    NodeWorkRecord,
    Provider,
    RunTelemetry,
    SessionLink,
    TimingPresenceRecord,
    TimingRecord,
    UsageRecord,
    UsageValues,
    _command_class,
    _failure,
    _metrics,
    _summarize_session,
    _timing,
    _utc_datetime,
    collect_run,
    main,
)


def _recorded_run(tmp_path: Path, *, state: str = "failed") -> Path:
    run_dir = tmp_path / "runs" / "observed"
    _, round_dir = prepare_round(run_dir, {"tasks": [{"id": "api", "task": "ship"}]})
    journal = open_journal(run_dir, RunId("observed"), 1)
    node = NodeJournal(journal, NodeId("api"), RunId("observed"), 1)
    journal.append("round-started", detail={"nodes": 1})
    node.append("node-started", detail={"persona": "engineer"})
    node.append("lock-wait", detail={"identity": "git:/repo", "seconds": 0.001, "acquired": True})
    node.append("setup-finished", detail={"operation": "fetch", "seconds": 0.001})
    node.append(
        "verification-started",
        detail={
            "command": ["just", "gate"],
            "comparison_remote": "origin",
            "comparison_base": "main",
        },
    )
    node.append(
        "verification-finished",
        detail={
            "ok": True,
            "reused": True,
            "gate_attestation": {
                "commit": "a" * 40,
                "comparison_remote": "origin",
                "comparison_base": "main",
                "comparison_commit": "d" * 40,
                "command": ["just", "gate"],
                "environment_sha256": "e" * 64,
            },
        },
    )
    node.append("pr-created", detail={"pr": "https://example.test/pull/1"})
    node.append("node-settled", detail={"status": "done" if state == "complete" else "failed"})
    if state == "complete":
        node.append("publication-finished", detail={"pr": "https://example.test/pull/1"})
    journal.append("round-finished", detail={"state": state, "ok": state == "complete"})
    write_result(
        round_dir,
        {
            "ok": state == "complete",
            "state": state,
            "started_order": ["api"],
            "results": {
                "api": {
                    "status": "done" if state == "complete" else "failed",
                    "outcome": "merged" if state == "complete" else "checks-failed",
                    "detail": "required checks failed" if state != "complete" else "",
                    "branch": "orchestrator/api",
                    "pr_base": "main",
                    "resume": {
                        "branch": "orchestrator/api",
                        "base_branch": "main",
                        "pr_base": "main",
                        "checkpoint": "b" * 40,
                        "completed_steps": [],
                        "pr": None,
                    },
                    "retry_lineage": {
                        "supersedes_branch": "orchestrator/old",
                        "supersedes_checkpoint": "c" * 40,
                        "disposition": "recovered",
                    },
                }
            },
        },
    )
    return run_dir


def _stretch_recorded_span(run_dir: Path, step_seconds: float = 1.0) -> None:
    """Space a recorded run's events a second apart, as a real run's are.

    The fixture writes its whole journal in a few milliseconds. Attribution is
    capped by elapsed wall time, so synthetic sessions reporting tens of
    milliseconds of model and tool work inside a 3ms run get clamped — which
    makes such assertions depend on how fast the host wrote the file rather than
    on the aggregation under test.
    """
    path = run_dir / "events.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    start = events[0]["at"]
    path.write_text(
        "".join(
            json.dumps({**event, "at": start + index * step_seconds}) + "\n"
            for index, event in enumerate(events)
        ),
        encoding="utf-8",
    )


def test_collect_run_joins_ledger_journal_history_and_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = _recorded_run(tmp_path)
    # A run whose whole journal lands inside one millisecond has no wall clock to
    # divide, so the categories below would report whatever the host had left rather
    # than what the run journalled.
    _stretch_recorded_span(run_dir)
    history_path = tmp_path / "history.jsonl"
    history_path.write_text(
        json.dumps(
            {
                "provider": "oneharness",
                "harness": "codex",
                "model": "gpt-5",
                "duration_ms": 2500,
            }
        )
        + "\n"
        + json.dumps(
            {
                "provider": "oneharness",
                "harness": "codex",
                "model": "gpt-5",
                "duration_ms": -1000,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    session = HistorySession(
        SessionId("session"),
        "ship",
        tmp_path,
        "2026-01-01T00:00:00Z",
        history_path,
        {"run_id": "observed", "node": "api", "role": "agent"},
    )
    judge_history = tmp_path / "judge-history.jsonl"
    judge_history.write_text(
        '{"provider":"oneharness","harness":"codex","model":"gpt-5","duration_ms":1000}\n',
        encoding="utf-8",
    )
    judge_session = HistorySession(
        SessionId("judge-session"),
        "you-are-a-strict-careful-evaluator",
        tmp_path,
        "2026-01-01T00:00:01Z",
        judge_history,
        {"run_id": "observed", "role": "judge"},
    )
    monkeypatch.setattr(history_module, "all_sessions", lambda **_kwargs: [session, judge_session])

    telemetry = collect_run(run_dir, now=9999999999.0)
    assert telemetry is not None
    record = telemetry.record()
    assert record["providers"] == [{"provider": "oneharness", "harness": "codex", "model": "gpt-5"}]
    assert record["failure"] == {"class": "checks", "detail": "required checks failed"}
    assert record["timing"]["agent_seconds"] == 2.5
    assert record["timing"]["judge_seconds"] == 1.0
    assert record["timing"]["agent_model_ms"] == 0
    # Every session here is a legacy one — no validated native fields — so none of the
    # idle share is attributable to any of them and all of it is unattributed. That is
    # the contract; "unattributed is positive" was a claim about how much wall clock the
    # host had left over, which a busy box can legitimately report as none.
    assert record["timing"]["unattributed_ms"] == record["timing"]["idle_orchestration_ms"]
    # The exact seconds the journal recorded, not merely a positive share: with the
    # run's own wall clock spread across its events there is room for every category,
    # so a dropped span reads as zero here instead of hiding behind a loaded box.
    assert record["timing"]["lock_wait_seconds"] == 0.001
    assert record["timing"]["setup_seconds"] == 0.001
    assert record["timing_quality"] == "legacy"
    assert record["linkage_quality"] == "labelled"
    node = record["nodes"][0]
    assert node["checkpoint"] == "b" * 40
    assert node["comparison_remote"] == "origin"
    assert node["gate_attestation"]["commit"] == "a" * 40
    assert node["retry_lineage"]["disposition"] == "recovered"


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"status": "failed", "outcome": "timeout", "detail": "timed out"}, "timeout"),
        ({"status": "failed", "outcome": "gate-failed", "detail": "bad"}, "gate"),
        ({"status": "failed", "outcome": "closed", "detail": "closed"}, "publication"),
        ({"status": "failed", "detail": "provider error"}, "provider"),
        ({"status": "failed", "detail": "bad config"}, "configuration"),
        ({"status": "failed", "detail": "agent stopped"}, "agent"),
        ({"status": "done"}, None),
    ],
)
def test_failure_class_is_typed_and_deterministic(item, expected) -> None:
    found = _failure(item)
    assert (found.classification if found else None) == expected


def test_optional_record_fields_are_omitted() -> None:
    assert Failure("unknown").record() == {"class": "unknown"}
    assert Provider("oneharness").record() == {"provider": "oneharness"}


def test_a_run_with_no_events_records_a_null_last_event(tmp_path: Path) -> None:
    """A prepared round that has journalled nothing has no last event, so it is null.

    This is the state every run passes through between `prepare_round` and its first
    append. `last_event` is required, so absence has to be representable in the value
    rather than encoded as an empty string a reader must recognise as "none".
    """
    run_dir = tmp_path / "runs" / "eventless"
    prepare_round(run_dir, {"tasks": [{"id": "api", "task": "ship"}]})

    telemetry = collect_run(run_dir, oneharness_bin="definitely-not-installed")

    assert telemetry is not None
    assert telemetry.last_event is None
    assert telemetry.record()["last_event"] is None


def test_harness_buckets_decompose_wall_time() -> None:
    timing = _timing(
        1000,
        [],
        gate=0.1,
        wait=0.1,
        lock_wait=0.2,
        setup=0.1,
        scheduling=0.2,
    )

    assert timing["lock_wait_seconds"] == 0.2
    assert timing["setup_seconds"] == 0.1
    assert timing["scheduling_seconds"] == 0.2
    assert (
        timing["agent_model_ms"]
        + timing["judge_model_ms"]
        + timing["tool_ms"]
        + round(timing["gate_seconds"] * 1000)
        + round(timing["lock_wait_seconds"] * 1000)
        + round(timing["setup_seconds"] * 1000)
        + round(timing["scheduling_seconds"] * 1000)
        + round(timing["publication_wait_seconds"] * 1000)
        + timing["idle_orchestration_ms"]
    ) == timing["wall_ms"]


def test_over_budget_buckets_are_clipped_to_exactly_wall_time() -> None:
    timing = _timing(
        100,
        [],
        gate=0.08,
        wait=0.08,
        lock_wait=0.08,
        setup=0.08,
        scheduling=0.08,
    )

    displayed_ms = sum(
        round(timing[field] * 1000)
        for field in (
            "gate_seconds",
            "publication_wait_seconds",
            "lock_wait_seconds",
            "setup_seconds",
            "scheduling_seconds",
        )
    )
    assert displayed_ms + timing["idle_orchestration_ms"] == timing["wall_ms"]
    assert timing["gate_seconds"] == 0.08
    assert timing["lock_wait_seconds"] == 0.02
    assert timing["setup_seconds"] == timing["scheduling_seconds"] == 0
    assert timing["publication_wait_seconds"] == 0


def test_the_clipping_order_the_contract_helper_states_is_the_one_timing_uses() -> None:
    """`_timing` spells its allocation order as straight-line code, not as data.

    `tests/telemetry_contract.WATERFALL` restates that order so both suites can say
    what a category is owed on a run that ran out of wall clock. This is the gate that
    stops the restatement drifting: each category is starved in turn by giving the run
    exactly enough milliseconds to pay for everything ahead of it and no more.
    """
    journalled = {"gate": 0.001, "lock_wait": 0.001, "setup": 0.001, "scheduling": 0.001}
    # `wait` is the argument behind `publication_wait_seconds`; the names differ because
    # the record states the wait it reports and the argument states what was waited on.
    assert tuple(journalled) + ("wait",) == tuple(
        category.removesuffix("_seconds").replace("publication_wait", "wait")
        for category in WATERFALL
    )

    for affordable, category in enumerate(WATERFALL, start=1):
        timing = _timing(affordable, [], wait=0.001, **journalled)

        paid = [round(timing[name] * 1000) for name in WATERFALL]
        assert paid == [1] * affordable + [0] * (len(WATERFALL) - affordable), category


@pytest.mark.reads_docs
def test_schema_v9_field_golden_prevents_cross_layer_drift() -> None:
    golden = json.loads(
        (Path(__file__).parent / "golden" / "telemetry-v9-fields.json").read_text(encoding="utf-8")
    )
    assert golden == {
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "history_schema_versions": list(SUPPORTED_HISTORY_SCHEMA_VERSIONS),
        "roles": list(get_args(SessionRole)),
        "timing_qualities": ["complete", "legacy", "partial"],
        "linkage_qualities": ["inferred", "labelled", "native"],
        "sources": ["history_legacy", "journal_legacy", "oneharness", "onejudge"],
        "timing_presence": sorted(TimingPresenceRecord.__required_keys__),
        "timing": sorted(TimingRecord.__required_keys__),
        "fractions": sorted(FractionsRecord.__required_keys__),
        "usage": sorted(UsageValues.__required_keys__),
        "session_link": sorted(SessionLink.__required_keys__ | SessionLink.__optional_keys__),
        "llmlint_retry_rate": sorted(LlmlintRetryRate.__required_keys__),
        "llmlint_retry_metrics": sorted(LlmlintRetryMetrics.__required_keys__),
    }
    contract = (Path(__file__).parents[1] / "docs" / "telemetry-model.md").read_text(
        encoding="utf-8"
    )
    assert "Index version 9" in contract
    typescript_contract = (
        Path(__file__).parents[1] / "packages" / "dag-model" / "src" / "index.ts"
    ).read_text(encoding="utf-8")
    typescript_schema_versions = [
        int(version)
        for version in re.findall(
            r"telemetry_schema_version:\s*z\.literal\((\d+)\)",
            typescript_contract,
        )
    ]
    assert typescript_schema_versions == [TELEMETRY_SCHEMA_VERSION] * 2
    for value in (
        *golden["roles"],
        *golden["timing_qualities"],
        *golden["linkage_qualities"],
        *golden["sources"],
    ):
        assert f"`{value}`" in contract
    documented_fields = (
        *(f"timing.{field}" for field in golden["timing"] if field.endswith("_ms")),
        "timing.lock_wait_seconds",
        "timing.setup_seconds",
        "timing.scheduling_seconds",
        *(f"timing.fractions.{field}" for field in golden["fractions"]),
        *(f"usage.{party}" for party in UsageRecord.__required_keys__),
        *(f"`{field}`" for field in golden["usage"]),
        "nodes[].timing",
        "nodes[].usage",
        "nodes[].sessions",
        *(f"`{field}`" for field in golden["session_link"]),
        *(f"`{field}`" for field in NodeWorkRecord.__required_keys__),
        *(f"`{field}`" for field in golden["llmlint_retry_metrics"]),
        *(
            field.name
            for field in fields(RunTelemetry)
            if field.name in {"node_work_ms", "turns", "lint"}
        ),
    )
    for field in documented_fields:
        marker = field if field.startswith("`") else f"`{field}`"
        assert marker in contract


def test_index_cli_defaults_to_active_and_all_includes_settled(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _recorded_run(tmp_path)
    completed = _recorded_run(tmp_path / "complete", state="complete")
    completed.rename(tmp_path / "runs" / "complete")
    assert main(["--runs-dir", str(tmp_path / "runs"), "--oneharness-bin", "absent"]) == 0
    active = json.loads(capsys.readouterr().out)
    assert active["schema_version"] == 9
    assert active["runs"] == []
    assert active["metrics"]["recovered_branches"] == 0

    assert main(["--runs-dir", str(tmp_path / "runs"), "--all", "--oneharness-bin", "absent"]) == 0
    all_runs = json.loads(capsys.readouterr().out)
    assert {run["run_id"] for run in all_runs["runs"]} == {"observed", "complete"}
    assert _metrics([])["retry_attempts"] == 0
    assert main(["--runs-dir", str(tmp_path / "missing")]) == 0
    assert json.loads(capsys.readouterr().out)["runs"] == []


def test_native_timing_usage_tools_and_breakdown_are_role_and_node_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = _recorded_run(tmp_path, state="complete")
    # The attribution asserted below is per-role, not wall-clamped; give the run the
    # kind of span a real one has.
    _stretch_recorded_span(run_dir)

    def session(role: str, duration: int, model: int, tool: int) -> HistorySession:
        path = tmp_path / f"{role}.jsonl"
        path.write_text(
            json.dumps(
                {
                    "duration_ms": duration,
                    "model_ms": model,
                    "tool_ms": tool,
                    "usage": {
                        "input_tokens": 10 if role == "agent" else 3,
                        "output_tokens": 5 if role == "agent" else 2,
                        "cache_read_tokens": 4,
                        "cache_write_tokens": 1,
                        "cost_usd": 0.02 if role == "agent" else 0.01,
                    },
                    "events": [
                        {
                            "kind": "tool_call",
                            "name": "command_execution",
                            "duration_ms": tool,
                            "input": {"command": "just gate"},
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return HistorySession(
            SessionId(f"{role}-session"),
            role,
            tmp_path,
            "2026-01-01T00:00:00Z",
            path,
            {"run_id": "observed", "node": "api", "role": role},
        )

    labelled_lint = session("llmlint", 7, 6, 0)
    legacy_lint = session("legacy-lint", 4, 3, 0)
    legacy_lint = replace(
        legacy_lint,
        name="evaluate-each-rule-against-the-target",
        labels={"run_id": "observed", "node": "api"},
    )
    sessions = [
        session("agent", 20, 12, 5),
        session("judge", 10, 8, 0),
        labelled_lint,
        legacy_lint,
    ]
    monkeypatch.setattr(history_module, "all_sessions", lambda **_kwargs: sessions)
    telemetry = collect_run(run_dir)
    assert telemetry is not None
    record = telemetry.record()
    assert record["timing"]["agent_model_ms"] == record["nodes"][0]["timing"]["agent_model_ms"]
    assert record["timing"]["judge_model_ms"] == 8
    assert record["timing"]["tool_ms"] == 5
    assert record["timing"]["llmlint_model_ms"] == 9
    assert record["usage"]["llmlint"]["input_tokens"] == 6
    assert record["usage"]["total"]["input_tokens"] == 19
    assert record["usage"]["total"]["cost_usd"] == pytest.approx(0.05)
    assert record["nodes"][0]["sessions"][1]["role"] == "judge"
    assert record["nodes"][0]["tool_commands"] == {"gate": 4}
    assert record["turns"] == record["nodes"][0]["turns"] == 2
    assert record["lint"] == record["nodes"][0]["lint"] == 2
    assert record["timing_quality"] == "partial"
    assert record["linkage_quality"] == "inferred"
    assert record["timing_presence"] == {
        "agent_model_ms": True,
        "judge_model_ms": True,
        "llmlint_model_ms": True,
        "tool_ms": True,
    }
    assert main(["--runs-dir", str(tmp_path / "runs"), "--all", "--breakdown"]) == 0
    breakdown = capsys.readouterr().out
    assert "WORKER" in breakdown and "LLMLINT" in breakdown and "TURNS LINT" in breakdown
    run_row = next(line for line in breakdown.splitlines() if line.startswith("observed"))
    for field in ("agent_model_ms", "judge_model_ms", "llmlint_model_ms", "tool_ms"):
        assert f"{record['timing'][field]:5}" in run_row
    assert "?" not in run_row
    assert "Turn histogram: 2=1" in breakdown


def test_breakdown_timeline_orders_turns_and_marks_unfinished_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = tmp_path / "runs" / "observed"
    _, round_dir = prepare_round(run_dir, {"tasks": [{"id": "api", "task": "ship"}]})
    journal = open_journal(run_dir, RunId("observed"), 1)
    node = NodeJournal(journal, NodeId("api"), RunId("observed"), 1)
    journal.append("round-started", detail={"nodes": 1})
    node.append("node-started", detail={"persona": "engineer"})
    node.append("node-settled", detail={"status": "done"})
    journal.append("round-finished", detail={"state": "complete", "ok": True})
    write_result(
        round_dir,
        {
            "ok": True,
            "state": "complete",
            "started_order": ["api"],
            "results": {
                "api": {
                    "status": "done",
                    "telemetry": {
                        "wall_ms": 30,
                        "orchestration_ms": 1,
                        "agent": {"model_ms": 8, "tool_ms": 2},
                        "judge": {"model_ms": 4, "tool_ms": 0},
                        # Deliberately out of turn order: the timeline sorts them.
                        "sessions": [
                            {
                                "session_id": "judge-turn",
                                "role": "judge",
                                "turn_index": 1,
                                "started_at": "2026-07-19T00:00:02Z",
                                "finished_at": "2026-07-19T00:00:03Z",
                            },
                            {
                                "session_id": "agent-turn",
                                "role": "agent",
                                "turn_index": 0,
                                "started_at": "2026-07-19T00:00:00Z",
                                # No finished_at: the run was interrupted mid-turn.
                            },
                        ],
                    },
                }
            },
        },
    )
    monkeypatch.setattr(history_module, "all_sessions", lambda **_kwargs: [])

    assert main(["--runs-dir", str(tmp_path / "runs"), "--all", "--breakdown"]) == 0
    breakdown = capsys.readouterr().out
    timeline = [line for line in breakdown.splitlines() if line.startswith("    turn ")]
    assert timeline == [
        "    turn 0 agent: 2026-07-19T00:00:00Z -> active/interrupted [agent-turn]",
        "    turn 1 judge: 2026-07-19T00:00:02Z -> 2026-07-19T00:00:03Z [judge-turn]",
    ]


def test_index_cli_rejects_malformed_and_inverted_history_boundaries(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = str(tmp_path / "runs")
    assert main(["--runs-dir", runs, "--since", "2026-07-19"]) == 2
    assert "--since must be an ISO-8601 UTC timestamp" in capsys.readouterr().err

    assert main(["--runs-dir", runs, "--until", "not-a-timestamp"]) == 2
    assert "--until must be an ISO-8601 UTC timestamp" in capsys.readouterr().err

    with pytest.raises(SystemExit) as inverted:
        main(
            [
                "--runs-dir",
                runs,
                "--since",
                "2026-07-19T00:00:01Z",
                "--until",
                "2026-07-19T00:00:00Z",
            ]
        )
    assert inverted.value.code == 2
    assert "--since must be earlier than --until" in capsys.readouterr().err

    # Equal boundaries select nothing, so they are rejected rather than silently empty.
    with pytest.raises(SystemExit) as degenerate:
        main(
            [
                "--runs-dir",
                runs,
                "--since",
                "2026-07-19T00:00:00Z",
                "--until",
                "2026-07-19T00:00:00Z",
            ]
        )
    assert degenerate.value.code == 2
    assert "--since must be earlier than --until" in capsys.readouterr().err


def test_session_normalization_degrades_each_field_independently(tmp_path: Path) -> None:
    session = HistorySession(
        SessionId("legacy"),
        "ordinary",
        tmp_path,
        "2026-01-01T00:00:00Z",
        tmp_path / "x",
        {"role": "judge", "node": "api"},
    )
    summary = _summarize_session(
        session,
        [
            {
                "duration_ms": 9,
                "usage": {"input_tokens": 2, "output_tokens": True},
                "events": [
                    "bad",
                    {"kind": "message"},
                    {"kind": "tool_call", "name": "other", "duration_ms": 3},
                    {"kind": "tool_call", "name": "bash", "input": {"cmd": "git status"}},
                    {"kind": "tool_call", "name": "bash", "input": "bad"},
                ],
            },
            {"duration_ms": True, "events": "bad"},
        ],
    )
    assert summary.role == "judge"
    assert summary.duration_ms == 9
    assert summary.tool_ms == 3
    assert summary.commands == {"git": 1}
    assert summary.usage["input_tokens"] is None
    assert summary.usage["output_tokens"] is None
    assert not summary.validated_native_fields
    assert _command_class("just check") == "just"
    assert _command_class("") == "unknown"
    zero = _timing(0, [summary])
    assert set(zero["fractions"].values()) == {0.0}
    current = _summarize_session(session, [{"schema_version": "1.1", "duration_ms": 1}])
    assert not current.validated_native_fields


def test_new_history_schema_rejects_invalid_intervals_roles_and_tool_events(tmp_path: Path) -> None:
    session = HistorySession(
        SessionId("new"), "agent", tmp_path, "now", tmp_path / "history", {"role": "agent"}
    )
    base = {
        "schema_version": "1.1",
        "duration_ms": 5,
        "model_ms": 3,
        "tool_ms": 2,
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:00:00.005Z",
        "usage": {},
        "events": [],
    }
    assert _utc_datetime("2026-01-01") is None
    assert _utc_datetime("not-a-dateZ") is None
    assert _utc_datetime(1) is None
    unavailable = _summarize_session(
        session,
        [
            {
                "schema_version": "1.1",
                "status": "spawn-error",
                "duration_ms": 0,
                "usage": {},
            }
        ],
    )
    assert unavailable.duration_ms == 0
    assert not unavailable.validated_native_fields
    for changed in (
        {"duration_ms": 4},
        {"started_at": "2026-01-01"},
        {"finished_at": "2025-01-01T00:00:00Z"},
        {
            "events": [
                {
                    "kind": "tool_call",
                    "tool_call_id": "",
                    "started_at": base["started_at"],
                    "finished_at": None,
                    "duration_ms": None,
                    "status": "completed",
                }
            ]
        },
        {
            "events": [
                {
                    "kind": "tool_call",
                    "tool_call_id": "x",
                    "started_at": base["started_at"],
                    "finished_at": "bad",
                    "duration_ms": -1,
                    "status": "unknown",
                }
            ]
        },
    ):
        with pytest.raises(telemetry_module.HistoryError, match="invalid"):
            _summarize_session(session, [{**base, **changed}])
    bad_role = HistorySession(
        SessionId("bad"), "agent", tmp_path, "now", tmp_path / "history", {"role": "other"}
    )
    with pytest.raises(telemetry_module.HistoryError, match="role"):
        _summarize_session(bad_role, [])


def test_new_history_schema_degrades_absent_timing_and_null_tool_event(tmp_path: Path) -> None:
    session = HistorySession(
        SessionId("claude"), "agent", tmp_path, "now", tmp_path / "history", {"role": "agent"}
    )
    summary = _summarize_session(
        session,
        [
            {
                "schema_version": "1.0",
                "duration_ms": None,
                "finished_at": None,
                "events": [
                    {
                        "kind": "tool_call",
                        "name": "command_execution",
                        "tool_call_id": "call-1",
                        "started_at": None,
                        "finished_at": None,
                        "duration_ms": None,
                        "status": None,
                        "input": {"command": "just check"},
                    }
                ],
            }
        ],
    )
    assert not summary.validated_native_fields
    assert summary.model_ms == 0
    assert summary.tool_ms == 0
    assert summary.commands == {"just": 1}


def test_history_schema_1_2_distinguishes_observed_tool_timing(tmp_path: Path) -> None:
    session = HistorySession(
        SessionId("observed"), "agent", tmp_path, "now", tmp_path / "history", {"role": "agent"}
    )
    base = {
        "schema_version": "1.2",
        "duration_ms": 5,
        "model_ms": 3,
        "tool_ms": 2,
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:00:00.005Z",
        "usage": {},
        "events": [],
    }
    provider = _summarize_session(session, [base])
    observed = _summarize_session(
        session,
        [
            {
                **base,
                "model_ms": None,
                "tool_ms": None,
                "observed_tool_ms": 2,
                "started_at": None,
                "finished_at": None,
                "events": [
                    {
                        "kind": "tool_call",
                        "tool_call_id": "call-1",
                        "started_at": "2026-01-01T00:00:00Z",
                        "finished_at": "2026-01-01T00:00:00.002Z",
                        "duration_ms": 2,
                        "status": "completed",
                        "timing_source": "stdout_observed",
                    }
                ],
            }
        ],
    )

    assert provider.validated_native_fields
    assert observed.tool_ms == 2
    assert observed.has_tool_measurement
    assert not observed.validated_native_fields

    with pytest.raises(telemetry_module.HistoryError, match="observed timing"):
        _summarize_session(session, [{**base, "observed_tool_ms": 2}])


def test_report_telemetry_validates_linkage_usage_and_step_aggregation(tmp_path: Path) -> None:
    usage = {
        "input_tokens": 2,
        "output_tokens": 1,
        "cache_read_tokens": 3,
        "cache_write_tokens": 0,
        "cost_usd": 0.01,
    }
    value = {
        "wall_ms": 12,
        "orchestration_ms": 1,
        "agent": {"model_ms": 5, "tool_ms": 2, "usage": usage},
        "judge": {"model_ms": 4, "tool_ms": 0, "usage": usage},
        "sessions": [
            {
                "session_id": "native",
                "history_id": "record",
                "role": "judge",
                "turn_index": 1,
                "started_at": "2026-07-19T00:00:00Z",
                "finished_at": "2026-07-19T00:00:00.004Z",
            }
        ],
    }
    native = telemetry_module._native_telemetry(value)
    assert native is not None
    assert native.tool_ms == 2
    assert native.usage is not None
    assert native.usage["total"]["input_tokens"] == 4
    assert native.sessions[0]["history_id"] == "record"
    assert not native.invalid

    session = HistorySession(
        SessionId("native"), "agent", tmp_path, "now", tmp_path / "history", {"role": "agent"}
    )
    summary = _summarize_session(session, [{"duration_ms": 4, "usage": usage}])
    linked = telemetry_module._link_native_roles([summary], native)
    assert linked[0].role == "judge"
    assert linked[0].link["turn_index"] == 1

    steps = telemetry_module._item_native(
        {"steps": [{"telemetry": value}, {"telemetry": {**value, "wall_ms": 8}}]}
    )
    assert steps is not None
    assert steps.wall_ms == 20
    assert steps.agent_model_ms == 10
    assert len(steps.sessions) == 2
    assert telemetry_module._item_native({"steps": []}) is None
    assert telemetry_module._item_native({}) is None

    combined = telemetry_module._aggregate_usage([native.usage, native.usage])
    assert combined is not None
    assert combined["total"]["input_tokens"] == 8
    assert telemetry_module._aggregate_usage([]) is None

    native.usage["judge"]["cache_write_tokens"] = None
    native.usage["judge"]["cost_usd"] = None
    fallback = telemetry_module._usage(linked)
    merged = telemetry_module._merge_usage(native.usage, fallback)
    assert merged["judge"]["cache_write_tokens"] == 0
    assert merged["judge"]["cost_usd"] == 0.01


@pytest.mark.parametrize(
    "value",
    [
        "bad",
        {"wall_ms": True, "orchestration_ms": -1, "agent": "bad", "judge": []},
        {"agent": {"model_ms": -1, "tool_ms": True, "usage": "bad"}},
        {"judge": {"usage": {"input_tokens": True, "cost_usd": float("inf")}}},
        {"sessions": "bad"},
        {"sessions": ["bad"]},
        {
            "sessions": [
                {
                    "session_id": "",
                    "history_id": 1,
                    "role": "other",
                    "turn_index": -1,
                    "started_at": "bad",
                    "finished_at": "earlier",
                }
            ]
        },
        {
            "sessions": [
                {
                    "session_id": "x",
                    "role": "agent",
                    "turn_index": 0,
                    "started_at": "2026-07-19T00:00:01Z",
                    "finished_at": "2026-07-19T00:00:00Z",
                }
            ]
        },
    ],
)
def test_invalid_report_telemetry_degrades_without_rejecting_run(value: object) -> None:
    native = telemetry_module._native_telemetry(value)
    assert native is not None
    assert native.invalid


def test_run_timing_retains_native_values_and_old_history_is_unattributed(tmp_path: Path) -> None:
    session = HistorySession(
        SessionId("legacy"),
        "legacy",
        tmp_path,
        "now",
        tmp_path / "legacy",
        {"node": "legacy", "role": "agent"},
    )
    legacy = _summarize_session(
        session,
        [{"duration_ms": 30, "usage": {}}],
    )
    native = telemetry_module._NativeTelemetry(20, None, 12, 0, 3, None, [])
    native_node = telemetry_module.NodeTelemetry(
        "native", "done", timing=_timing(20, [], native=native)
    )
    legacy_node = telemetry_module.NodeTelemetry("legacy", "done", timing=_timing(30, [legacy]))
    timing = telemetry_module._run_timing(
        0,
        100,
        [legacy],
        [native_node, legacy_node],
        {"native": native, "legacy": None},
        0,
        0,
    )
    assert native_node.timing["agent_model_ms"] == 12
    assert legacy_node.timing["agent_model_ms"] == 0
    assert legacy_node.timing["tool_ms"] == 0
    assert legacy_node.timing["unattributed_ms"] == 30
    assert timing["agent_model_ms"] == 12
    assert timing["tool_ms"] == 3
    assert timing["unattributed_ms"] >= 30


def test_collect_run_prefers_native_scalars_and_unions_fallback_tool_intervals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "runs" / "overlap"
    _, round_dir = prepare_round(
        run_dir, {"tasks": [{"id": node, "task": node} for node in ("native", "b", "c")]}
    )
    journal = open_journal(run_dir, RunId("overlap"), 1)
    journal.append("round-started", detail={"nodes": 3})
    for name in ("native", "b", "c"):
        node = NodeJournal(journal, NodeId(name), RunId("overlap"), 1)
        node.append("node-started", detail={"persona": "engineer"})
        node.append("node-settled", detail={"status": "done"})
    journal.append("round-finished", detail={"state": "complete", "ok": True})
    base = datetime(2026, 7, 19, tzinfo=UTC)
    event_path = run_dir / "events.jsonl"
    events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
    for event in events:
        event["at"] = (
            base
            + timedelta(
                milliseconds=140
                if event["kind"].endswith("finished") or event["kind"] == "node-settled"
                else 0
            )
        ).timestamp()
    event_path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    write_result(
        round_dir,
        {
            "ok": True,
            "state": "complete",
            "started_order": ["native", "b", "c"],
            "results": {
                "native": {
                    "status": "done",
                    "telemetry": {
                        "wall_ms": 140,
                        "agent": {"model_ms": 12, "tool_ms": 7},
                    },
                },
                "b": {"status": "done"},
                "c": {"status": "done"},
            },
        },
    )

    def history(node: str, model: int, start: int, finish: int) -> HistorySession:
        path = tmp_path / f"{node}.jsonl"
        record_start = base.isoformat().replace("+00:00", "Z")
        record_finish = (base + timedelta(milliseconds=140)).isoformat().replace("+00:00", "Z")
        tool_start = (base + timedelta(milliseconds=start)).isoformat().replace("+00:00", "Z")
        tool_finish = (base + timedelta(milliseconds=finish)).isoformat().replace("+00:00", "Z")
        path.write_text(
            json.dumps(
                {
                    "schema_version": "0.3",
                    "duration_ms": 140,
                    "model_ms": model,
                    "tool_ms": finish - start,
                    "started_at": record_start,
                    "finished_at": record_finish,
                    "usage": {},
                    "events": [
                        {
                            "kind": "tool_call",
                            "name": "bash",
                            "tool_call_id": f"{node}-tool",
                            "started_at": tool_start,
                            "finished_at": tool_finish,
                            "duration_ms": finish - start,
                            "status": "completed",
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return HistorySession(
            SessionId(node),
            node,
            tmp_path,
            record_start,
            path,
            {"run_id": "overlap", "node": node, "role": "agent"},
        )

    sessions = [history("native", 80, 40, 60), history("b", 20, 50, 90), history("c", 15, 70, 110)]
    monkeypatch.setattr(history_module, "all_sessions", lambda **_kwargs: sessions)

    telemetry = collect_run(run_dir)
    assert telemetry is not None
    timing = telemetry.record()["timing"]
    assert timing["wall_ms"] == 140
    assert timing["agent_model_ms"] == 47
    assert timing["tool_ms"] == 67
    assert timing["idle_orchestration_ms"] == 26


def test_unsettled_round_reads_node_settlement_out_of_its_own_journal(tmp_path: Path) -> None:
    """A round with no `result.json` is described by the journal, not by optimism.

    Every node that had ever appeared in the journal was reported `running`, so a
    node the ledger recorded as `node-failed` still rendered as running — the stale
    picture a supervisor then trusts. The real journey is
    `tests/e2e/test_run_views_by_id_e2e.py`, which reads a live run's failed node
    back through `just telemetry`; this covers what that journey cannot reach
    in-process: the earlier-round exclusion, a step-scoped wait that settles nothing,
    and a terminal event whose serialized result is unusable.
    """
    run_dir = tmp_path / "runs" / "unsettled"
    run_id = RunId("unsettled")
    prepare_round(run_dir, {"tasks": [{"id": "gone", "task": "old"}]})
    settled = open_journal(run_dir, run_id, 1)
    settled.append("round-started", detail={"nodes": 1})
    NodeJournal(settled, NodeId("gone"), run_id, 1).append("node-started", detail={})
    write_result(
        run_dir / "round-01",
        {"ok": True, "state": "complete", "started_order": ["gone"], "results": {}},
    )

    prepare_round(run_dir, {"tasks": [{"id": "boom", "task": "fail"}]})
    journal = open_journal(run_dir, run_id, 2)
    journal.append("round-started", detail={"nodes": 3})
    boom = NodeJournal(journal, NodeId("boom"), run_id, 2)
    boom.append("node-started", detail={})
    boom.append(
        "node-failed",
        detail={"outcome": "not-completed", "result": {"status": "failed", "kind": "agent"}},
    )
    bare = NodeJournal(journal, NodeId("bare"), run_id, 2)
    bare.append("node-started", detail={})
    # A terminal event whose serialized result cannot be validated still proves what
    # its own kind means, so the node settles rather than reverting to running.
    bare.append("node-failed", detail={"outcome": "gate-failed", "result": {"status": 7}})
    held = NodeJournal(journal, NodeId("held"), run_id, 2)
    held.append("node-started", detail={})
    held.for_step(StepId("verify")).append("human-waiting", detail={"ref": "held/verify"})

    telemetry = collect_run(run_dir, oneharness_bin="definitely-not-installed")
    assert telemetry is not None
    statuses = {node.node: node.status for node in telemetry.nodes}
    # `gone` belongs to round 1, whose own recorded result already describes it.
    assert statuses == {"boom": "failed", "bare": "failed", "held": "running"}
    assert {node.node: node.outcome for node in telemetry.nodes}["bare"] == "gate-failed"


def test_naming_a_run_reports_it_settled_or_not_and_refuses_an_unknown_one(
    tmp_path: Path, capsys
) -> None:
    runs_dir = tmp_path / "runs"
    for name, ok in (("done", True), ("other", True)):
        _, round_dir = prepare_round(runs_dir / name, {"tasks": [{"id": "api", "task": "ship"}]})
        journal = open_journal(runs_dir / name, RunId(name), 1)
        journal.append("round-started", detail={"nodes": 1})
        NodeJournal(journal, NodeId("api"), RunId(name), 1).append("node-started", detail={})
        write_result(
            round_dir,
            {"ok": ok, "state": "complete", "started_order": ["api"], "results": {}},
        )

    assert main(["nowhere", "--runs-dir", str(runs_dir)]) == 2
    assert "no recorded run 'nowhere'" in capsys.readouterr().err
    # Both runs settled, so the unscoped index is empty without `--all` — naming one
    # is the request and must not be filtered out by that default.
    assert main(["--runs-dir", str(runs_dir)]) == 0
    assert json.loads(capsys.readouterr().out)["runs"] == []
    assert main(["done", "--runs-dir", str(runs_dir)]) == 0
    assert [run["run_id"] for run in json.loads(capsys.readouterr().out)["runs"]] == ["done"]
