"""Unit tests for the repo-plan run ledger and guided continuation."""

from __future__ import annotations

import json

import pytest

from orchestrator.config import ConfigError
from orchestrator.next_round import main, main_runs
from orchestrator.runs import (
    latest_round,
    list_runs,
    prepare_round,
    resolve_run_dir,
    validate_run_id,
    write_next_plan,
    write_result,
)

PLAN = {"name": "Useful run", "concurrency": 1, "tasks": [{"id": "a"}]}


def _result(status: str) -> dict:
    return {
        "ok": status == "done",
        "started_order": ["a"],
        "results": {"a": {"status": status}},
    }


@pytest.mark.parametrize("run_id", ["../escape", "a/b", "a\\b", "..", "/absolute"])
def test_run_id_rejects_paths(run_id: str) -> None:
    with pytest.raises(ConfigError, match="run id"):
        validate_run_id(run_id)


def test_resolve_implicit_run_is_fresh(tmp_path) -> None:
    first = resolve_run_dir(tmp_path, PLAN, tmp_path / "fallback.json", None)
    assert first.name == "Useful-run"
    first.mkdir()
    second = resolve_run_dir(tmp_path, PLAN, tmp_path / "fallback.json", None)
    assert second.name.startswith("Useful-run-") and second != first


def test_round_numbering_latest_and_pending_reuse(tmp_path) -> None:
    run_dir = tmp_path / "run"
    number, first = write_next_plan(run_dir, PLAN)
    assert number == 1 and latest_round(run_dir) == (1, first)
    assert prepare_round(run_dir, PLAN) == (1, first)
    write_result(first, _result("failed"))
    number, second = prepare_round(run_dir, PLAN)
    assert number == 2 and second.name == "round-02"
    with pytest.raises(ConfigError, match="pending different"):
        prepare_round(run_dir, {"tasks": []})


def test_list_runs_uses_latest_completed_round(tmp_path) -> None:
    run = tmp_path / "demo"
    _, round_dir = write_next_plan(run, PLAN)
    write_result(round_dir, _result("done"))
    assert list_runs(tmp_path) == [("demo", 1, "1 done, 0 failed, 0 skipped")]


def test_next_round_noop_does_not_create_round(tmp_path, capsys) -> None:
    run = tmp_path / "demo"
    _, round_dir = write_next_plan(run, PLAN)
    write_result(round_dir, _result("done"))
    rc = main(["demo", "--runs-dir", str(tmp_path)])
    assert rc == 0 and latest_round(run) == (1, round_dir)
    assert "nothing to iterate" in capsys.readouterr().out


def test_plan_only_and_runs_cli(tmp_path, capsys) -> None:
    run = tmp_path / "demo"
    plan = {"tasks": [{"id": "a", "repo": "x", "persona": "p", "task": "t"}]}
    _, round_dir = write_next_plan(run, plan)
    write_result(round_dir, _result("failed"))
    edits = tmp_path / "edits.json"
    edits.write_text(json.dumps({"retry": {"a": {"max_turns": 8}}}), encoding="utf-8")
    assert main(["demo", str(edits), "--runs-dir", str(tmp_path), "--plan-only"]) == 0
    assert json.loads((run / "round-02" / "plan.json").read_text())["tasks"][0]["max_turns"] == 8
    assert main_runs(["--runs-dir", str(tmp_path)]) == 0
    assert "demo  round-01" in capsys.readouterr().out
