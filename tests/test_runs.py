"""Unit tests for the repo-plan run ledger and guided continuation."""

from __future__ import annotations

import json
import os
import socket

import pytest

from orchestrator.config import ConfigError
from orchestrator.next_round import main, main_runs
from orchestrator.runs import (
    as_result_payload,
    latest_round,
    list_runs,
    load_completions,
    prepare_round,
    record_completions,
    resolve_run_dir,
    result_state,
    status_summary,
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


def test_list_runs_empty_and_invalid_result_payload(tmp_path) -> None:
    assert list_runs(tmp_path / "missing") == []
    with pytest.raises(ConfigError, match="invalid tracked-graph"):
        as_result_payload({"ok": True, "started_order": [], "results": {"a": {}}})


def test_result_state_derives_old_payload_states() -> None:
    assert result_state(_result("failed")) == "failed"
    assert (
        result_state(
            {
                "ok": False,
                "started_order": ["h"],
                "results": {"h": {"status": "waiting"}},
            }
        )
        == "waiting"
    )
    assert result_state(_result("done")) == "complete"


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


def test_write_result_rejects_duplicate_result(tmp_path) -> None:
    run_dir = tmp_path / "run"
    _, round_dir = write_next_plan(run_dir, PLAN)
    write_result(round_dir, _result("done"))
    with pytest.raises(ConfigError, match="already has a result"):
        write_result(round_dir, _result("done"))


def test_recovery_refuses_live_owner_and_claims_abandoned_round(tmp_path) -> None:
    run_dir = tmp_path / "run"
    _, round_dir = prepare_round(run_dir, PLAN)
    with pytest.raises(ConfigError, match="owner is still alive; recovery refused"):
        prepare_round(run_dir, PLAN, recover=True)

    status = round_dir / "status.json"
    status.write_text(
        json.dumps(
            {"status": "running", "pid": os.getpid() + 10_000_000, "host": socket.gethostname()}
        ),
        encoding="utf-8",
    )
    assert prepare_round(run_dir, PLAN, recover=True) == (1, round_dir)
    recovered = json.loads(status.read_text(encoding="utf-8"))
    assert recovered["pid"] == os.getpid()


@pytest.mark.parametrize(
    "owner",
    [
        {"status": "running", "pid": "not-a-pid", "host": "host"},
        {"status": "completed", "pid": 123, "host": "host"},
    ],
)
def test_recovery_refuses_invalid_owner_metadata(tmp_path, owner) -> None:
    run_dir = tmp_path / "run"
    _, round_dir = prepare_round(run_dir, PLAN)
    (round_dir / "status.json").write_text(json.dumps(owner), encoding="utf-8")

    with pytest.raises(ConfigError, match="invalid owner metadata; recovery refused"):
        prepare_round(run_dir, PLAN, recover=True)


def test_list_runs_uses_latest_completed_round(tmp_path) -> None:
    run = tmp_path / "demo"
    _, round_dir = write_next_plan(run, PLAN)
    write_result(round_dir, _result("done"))
    assert list_runs(tmp_path) == [("demo", 1, "1 done")]


def test_list_runs_surfaces_follow_ups(tmp_path) -> None:
    run = tmp_path / "demo"
    _, round_dir = write_next_plan(run, PLAN)
    result = _result("done")
    result["results"]["a"]["follow_ups"] = "- Add the adjacent regression test."
    write_result(round_dir, result)
    assert list_runs(tmp_path) == [
        (
            "demo",
            1,
            "1 done; follow-ups: a: - Add the adjacent regression test.",
        )
    ]


def test_status_summary_surfaces_waiting_human_action(tmp_path) -> None:
    run = tmp_path / "demo"
    _, round_dir = write_next_plan(run, PLAN)
    write_result(
        round_dir,
        {
            "ok": False,
            "state": "waiting",
            "started_order": ["h"],
            "results": {
                "h": {
                    "status": "waiting",
                    "human_actions": [
                        {
                            "ref": "h",
                            "task": "Review the result",
                            "unblocks": ["after"],
                            "unblocks_publication": False,
                        }
                    ],
                }
            },
        },
    )

    assert list_runs(tmp_path) == [
        ("demo", 1, "1 waiting; awaiting h: Review the result -> unblocks after")
    ]


def test_status_summary_waiting_downstream_variants() -> None:
    payload = {
        "ok": False,
        "state": "waiting",
        "started_order": ["a", "b"],
        "results": {
            "a": {
                "status": "waiting",
                "human_actions": [
                    {
                        "ref": "a",
                        "task": "Publish?",
                        "unblocks": [],
                        "unblocks_publication": True,
                    }
                ],
            },
            "b": {
                "status": "waiting",
                "human_actions": [
                    {
                        "ref": "b",
                        "task": "",
                        "unblocks": [],
                        "unblocks_publication": False,
                    }
                ],
            },
        },
    }

    summary = status_summary(payload)
    assert "unblocks workstream publication" in summary
    assert "unblocks nothing downstream" in summary


def test_human_completion_ledger_records_and_rejects_duplicates(tmp_path) -> None:
    run = tmp_path / "demo"
    run.mkdir()
    record_completions(run, ["h"], round_number=2)
    assert load_completions(run)[0]["ref"] == "h"
    with pytest.raises(ConfigError, match="already completed"):
        record_completions(run, ["h"], round_number=2)


def test_human_completion_ledger_rejects_bad_shape_and_duplicate_input(tmp_path) -> None:
    run = tmp_path / "demo"
    run.mkdir()
    with pytest.raises(ConfigError, match="unique"):
        record_completions(run, ["h", "h"], round_number=1)
    (run / "humans.json").write_text(json.dumps({"completions": [{"ref": "h"}]}), encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid human-completion"):
        load_completions(run)


def test_next_round_noop_does_not_create_round(tmp_path, capsys) -> None:
    run = tmp_path / "demo"
    _, round_dir = write_next_plan(run, PLAN)
    write_result(round_dir, _result("done"))
    rc = main(["demo", "--runs-dir", str(tmp_path)])
    assert rc == 0 and latest_round(run) == (1, round_dir)
    assert "nothing to iterate" in capsys.readouterr().out


def test_next_round_rejects_missing_run_and_pending_result(tmp_path, capsys) -> None:
    assert main(["missing", "--runs-dir", str(tmp_path)]) == 2
    assert "no recorded rounds" in capsys.readouterr().err

    run = tmp_path / "demo"
    write_next_plan(run, PLAN)
    assert main(["demo", "--runs-dir", str(tmp_path)]) == 2
    assert "latest round has no result" in capsys.readouterr().err


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


def test_runs_cli_no_recorded_runs(tmp_path, capsys) -> None:
    assert main_runs(["--runs-dir", str(tmp_path)]) == 0
    assert "No recorded runs" in capsys.readouterr().out


def test_next_round_complete_human_records_attestation_and_releases_dep(tmp_path, capsys) -> None:
    run = tmp_path / "demo"
    plan = {
        "tasks": [
            {"id": "h", "kind": "human", "task": "Review"},
            {"id": "after", "persona": "backend-engineer", "task": "After", "deps": ["h"]},
        ]
    }
    _, round_dir = write_next_plan(run, plan)
    write_result(
        round_dir,
        {
            "ok": False,
            "state": "waiting",
            "started_order": ["h"],
            "results": {
                "h": {
                    "kind": "human",
                    "status": "waiting",
                    "task": "Review",
                    "human_actions": [
                        {
                            "ref": "h",
                            "task": "Review",
                            "unblocks": ["after"],
                            "unblocks_publication": False,
                        }
                    ],
                },
                "after": {"status": "blocked", "blocked_by": ["h"]},
            },
        },
    )

    rc = main(["demo", "--runs-dir", str(tmp_path), "--complete-human", "h", "--plan-only"])

    assert rc == 0
    assert load_completions(run)[0]["ref"] == "h"
    next_plan = json.loads((run / "round-02" / "plan.json").read_text(encoding="utf-8"))
    assert next_plan["tasks"] == [
        {"id": "after", "persona": "backend-engineer", "task": "After", "deps": []}
    ]
    assert "just run-plan" in capsys.readouterr().out


def test_next_round_rejects_invalid_human_completion_refs(tmp_path, capsys) -> None:
    run = tmp_path / "demo"
    _, round_dir = write_next_plan(run, {"tasks": [{"id": "h", "kind": "human", "task": "Review"}]})
    write_result(
        round_dir,
        {
            "ok": False,
            "state": "waiting",
            "started_order": ["h"],
            "results": {
                "h": {
                    "status": "waiting",
                    "human_actions": [
                        {
                            "ref": "h",
                            "task": "Review",
                            "unblocks": [],
                            "unblocks_publication": False,
                        }
                    ],
                }
            },
        },
    )
    edits = tmp_path / "edits.json"
    edits.write_text(json.dumps({"complete_human": "h"}), encoding="utf-8")

    assert main(["demo", str(edits), "--runs-dir", str(tmp_path)]) == 2
    assert "complete_human" in capsys.readouterr().err

    assert main(["demo", "--runs-dir", str(tmp_path), "--complete-human", "agent"]) == 2
    assert "recorded waiting human" in capsys.readouterr().err

    assert (
        main(
            [
                "demo",
                "--runs-dir",
                str(tmp_path),
                "--complete-human",
                "h",
                "--complete-human",
                "h",
            ]
        )
        == 2
    )
    assert "unique" in capsys.readouterr().err


def test_next_round_rejects_already_completed_human_ref(tmp_path, capsys) -> None:
    run = tmp_path / "demo"
    _, round_dir = write_next_plan(run, {"tasks": [{"id": "h", "kind": "human", "task": "Review"}]})
    write_result(
        round_dir,
        {
            "ok": False,
            "state": "waiting",
            "started_order": ["h"],
            "results": {
                "h": {
                    "status": "waiting",
                    "human_actions": [
                        {
                            "ref": "h",
                            "task": "Review",
                            "unblocks": [],
                            "unblocks_publication": False,
                        }
                    ],
                }
            },
        },
    )
    record_completions(run, ["h"], round_number=1)

    assert main(["demo", "--runs-dir", str(tmp_path), "--complete-human", "h"]) == 2
    assert "already completed" in capsys.readouterr().err


def test_next_round_reports_completion_record_failure(tmp_path, monkeypatch, capsys) -> None:
    run = tmp_path / "demo"
    plan = {
        "tasks": [
            {"id": "h", "kind": "human", "task": "Review"},
            {"id": "after", "persona": "backend-engineer", "task": "After", "deps": ["h"]},
        ]
    }
    _, round_dir = write_next_plan(run, plan)
    write_result(
        round_dir,
        {
            "ok": False,
            "state": "waiting",
            "started_order": ["h"],
            "results": {
                "h": {
                    "status": "waiting",
                    "human_actions": [
                        {
                            "ref": "h",
                            "task": "Review",
                            "unblocks": ["after"],
                            "unblocks_publication": False,
                        }
                    ],
                },
                "after": {"status": "blocked", "blocked_by": ["h"]},
            },
        },
    )

    def fail_record(run_dir, refs, *, round_number):
        raise ConfigError("disk full")

    monkeypatch.setattr("orchestrator.next_round.record_completions", fail_record)
    assert main(["demo", "--runs-dir", str(tmp_path), "--complete-human", "h"]) == 2
    assert "disk full" in capsys.readouterr().err
