"""Assembly and defensive-validation contracts for the DAG read model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.journal import NodeId, RunId, open_journal
from orchestrator.projection import read_strict_events
from orchestrator.read_model import (
    API_VERSION,
    InvalidRunId,
    ProjectionFailed,
    RunNotFound,
    list_runs,
    read_launcher,
    read_logs,
    round_record,
    run_conversation,
    run_detail,
    run_signature,
)
from orchestrator.runs import prepare_round, write_result

ABSENT = "definitely-not-a-real-oneharness-binary"


def _build_run(
    runs_dir: Path,
    run_id: str,
    *,
    settle: bool = True,
    launcher: dict[str, object] | None = None,
) -> Path:
    """Write a strict-valid single-node round, optionally settling it complete."""
    run_dir = runs_dir / run_id
    _, round_dir = prepare_round(run_dir, {"tasks": [{"id": "api", "task": "ship"}]})
    journal = open_journal(run_dir, RunId(run_id), 1)
    journal.append(
        "node-added", detail={"definition": {"id": "api", "persona": "engineer", "task": "ship"}}
    )
    journal.append("round-started", detail={"plan": {"schema_version": 3, "concurrency": 1}})
    journal.append("node-started", node=NodeId("api"), detail={"persona": "engineer"})
    if settle:
        journal.append(
            "node-settled",
            node=NodeId("api"),
            detail={
                "status": "done",
                "result": {"status": "done", "task": "ship", "pr": "https://x/pull/3"},
            },
        )
        result = {
            "ok": True,
            "state": "complete",
            "started_order": ["api"],
            "results": {"api": {"status": "done", "task": "ship", "pr": "https://x/pull/3"}},
        }
        journal.append("round-finished", detail={"result": result})
        write_result(round_dir, result)
    if launcher is not None:
        (run_dir / "launch.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "run_id": run_id,
                    "channel_id": run_id,
                    "plan_name": run_id,
                    "commands": {},
                    "launcher": launcher,
                }
            ),
            encoding="utf-8",
        )
    return run_dir


def test_list_runs_orders_by_progress_and_hides_settled(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _build_run(runs, "settled", settle=True)
    _build_run(runs, "active", settle=False)

    active_only = list_runs(runs, oneharness_bin=ABSENT)
    assert active_only["api_version"] == API_VERSION
    assert active_only["telemetry_schema_version"] == 6
    assert [row["run_id"] for row in active_only["runs"]] == ["active"]
    assert active_only["runs"][0]["node_counts"] == {"running": 1}

    everything = list_runs(runs, include_settled=True, oneharness_bin=ABSENT)
    assert {row["run_id"] for row in everything["runs"]} == {"active", "settled"}


def test_list_runs_skips_a_corrupt_run(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _build_run(runs, "healthy", settle=False)
    broken = runs / "broken"
    _, round_dir = prepare_round(broken, {"tasks": [{"id": "api", "task": "x"}]})
    (round_dir / "result.json").write_text("{ not json", encoding="utf-8")

    result = list_runs(runs, include_settled=True, oneharness_bin=ABSENT)
    assert [row["run_id"] for row in result["runs"]] == ["healthy"]


def test_list_runs_without_directory_is_empty(tmp_path: Path) -> None:
    assert list_runs(tmp_path / "missing", oneharness_bin=ABSENT)["runs"] == []


def test_list_runs_skips_a_directory_with_no_rounds(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _build_run(runs, "healthy", settle=False)
    (runs / "no-rounds").mkdir()  # a run directory that never recorded a round
    (runs / "stray.txt").write_text("not a run", encoding="utf-8")  # non-dir entry ignored
    result = list_runs(runs, include_settled=True, oneharness_bin=ABSENT)
    assert [row["run_id"] for row in result["runs"]] == ["healthy"]


def test_run_detail_exposes_projection_telemetry_and_launcher(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _build_run(runs, "demo", settle=True, launcher={"kind": "codex", "session_id": "sess-9"})

    detail = run_detail(runs, "demo", oneharness_bin=ABSENT)
    assert detail["run"]["run_id"] == "demo"
    assert detail["launcher"] == {"kind": "codex", "session_id": "sess-9"}
    assert len(detail["rounds"]) == 1
    round_zero = detail["rounds"][0]
    assert round_zero["node_states"] == {"api": "done"}
    assert round_zero["node_results"]["api"]["pr"] == "https://x/pull/3"
    assert detail["conversations"] == []
    assert "commits" in detail["details"] and "prs" in detail["details"]


def test_run_detail_rejects_invalid_and_missing_runs(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _build_run(runs, "demo", settle=True)
    with pytest.raises(InvalidRunId):
        run_detail(runs, "../etc", oneharness_bin=ABSENT)
    with pytest.raises(RunNotFound):
        run_detail(runs, "absent", oneharness_bin=ABSENT)
    empty = runs / "empty"
    empty.mkdir()
    with pytest.raises(RunNotFound):
        run_detail(runs, "empty", oneharness_bin=ABSENT)


def test_run_detail_reports_a_corrupt_journal_as_projection_error(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    run_dir = _build_run(runs, "demo", settle=True)
    journal = run_dir / "events.jsonl"
    journal.write_text(journal.read_text(encoding="utf-8") + '{"kind":"bogus"}\n', encoding="utf-8")
    with pytest.raises(ProjectionFailed):
        run_detail(runs, "demo", oneharness_bin=ABSENT)


def test_run_detail_reports_a_corrupt_result_as_projection_error(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    run_dir = _build_run(runs, "demo", settle=True)
    (run_dir / "round-01" / "result.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(ProjectionFailed):
        run_detail(runs, "demo", oneharness_bin=ABSENT)


def test_run_detail_omits_launcher_when_absent(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _build_run(runs, "demo", settle=True)  # no launch.json written
    detail = run_detail(runs, "demo", oneharness_bin=ABSENT)
    assert "launcher" not in detail
    assert "logs" not in detail  # no logs written for this run


def test_read_logs_returns_bounded_tails_and_omits_missing(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    run_dir = _build_run(runs, "demo", settle=True)
    assert read_logs(run_dir) == {}  # nothing written yet

    orchestrator_dir = run_dir / "orchestrator"
    orchestrator_dir.mkdir(exist_ok=True)
    (orchestrator_dir / "stderr.log").write_bytes(b"x" * 100_000 + b"TAIL")
    (orchestrator_dir / "gate.log").write_text("", encoding="utf-8")  # empty log omitted

    assert set(read_logs(run_dir)) == {"orchestrator_stderr"}
    (orchestrator_dir / "gate.log").write_text("gate ok\n", encoding="utf-8")

    logs = read_logs(run_dir)
    assert set(logs) == {"orchestrator_stderr", "gate_log"}
    assert logs["orchestrator_stderr"].endswith("TAIL")
    assert len(logs["orchestrator_stderr"].encode("utf-8")) <= 64_000  # bounded tail
    assert logs["gate_log"] == "gate ok\n"

    detail = run_detail(runs, "demo", oneharness_bin=ABSENT)
    assert detail["logs"]["gate_log"] == "gate ok\n"


def test_round_record_serializes_projection(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    run_dir = _build_run(runs, "demo", settle=True)
    events = read_strict_events(run_dir / "events.jsonl", RunId("demo"))
    record = round_record(events, RunId("demo"), 1)
    assert record["run_id"] == "demo"
    assert record["round"] == 1
    assert record["attestations"] == []
    assert record["result"]["state"] == "complete"
    assert json.loads(json.dumps(record))  # fully JSON-serializable


def test_run_conversation_missing_run_and_conversation(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _build_run(runs, "demo", settle=True)
    with pytest.raises(InvalidRunId):
        run_conversation(runs, "..", "c", oneharness_bin=ABSENT)
    with pytest.raises(RunNotFound):
        run_conversation(runs, "absent", "c", oneharness_bin=ABSENT)
    with pytest.raises(RunNotFound):
        run_conversation(runs, "demo", "no-such-conversation", oneharness_bin=ABSENT)


def test_read_launcher_degrades_on_missing_and_malformed(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    run_dir = _build_run(runs, "demo", settle=True)
    assert read_launcher(run_dir) is None  # no launch.json written

    (run_dir / "launch.json").write_text("{ not json", encoding="utf-8")
    assert read_launcher(run_dir) is None

    (run_dir / "launch.json").write_text(json.dumps({"launcher": {"kind": "nope"}}), "utf-8")
    assert read_launcher(run_dir) is None

    (run_dir / "launch.json").write_text(json.dumps({"launcher": "notdict"}), "utf-8")
    assert read_launcher(run_dir) is None

    (run_dir / "launch.json").write_text(json.dumps({"launcher": {"kind": "unknown"}}), "utf-8")
    assert read_launcher(run_dir) == {"kind": "unknown"}


def test_run_signature_advances_with_journal_growth(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    run_dir = _build_run(runs, "demo", settle=False)
    before = run_signature(run_dir)
    journal = open_journal(run_dir, RunId("demo"), 1)
    journal.append(
        "node-settled", node=NodeId("api"), detail={"status": "done", "result": {"status": "done"}}
    )
    after = run_signature(run_dir)
    assert after[1] > before[1]
    assert run_signature(tmp_path / "nope") == (0, 0)
