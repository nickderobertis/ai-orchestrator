"""Unit tests for the canonical tracked graph executor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.dispatch import Report
from orchestrator.graph import (
    HumanAction,
    first_line,
    graph_payload,
    load_graph,
    main,
    main_repo_plan,
    parse_graph,
    print_continuation,
    render_actions,
    run_graph,
)
from orchestrator.lifecycle import LifecycleResult, Step, StepResult
from orchestrator.plan import PlanError, PlanNode


def _report(persona: str, completed: bool = True) -> Report:
    return Report(
        persona=persona,
        exit_code=0 if completed else 1,
        completed=completed,
        stopped_early=False,
        assistant_turns=1,
        verdicts=[],
        usage={},
        raw={},
        stderr="",
    )


def _lifecycle(outcome: str = "merged", **kw) -> LifecycleResult:
    base = {
        "repo": "o/r",
        "task": "change",
        "persona": "backend-engineer",
        "base_branch": "main",
        "branch": "feature",
        "outcome": outcome,
    }
    base.update(kw)
    return LifecycleResult(**base)


def test_run_graph_journals_what_the_round_actually_did(tmp_path: Path) -> None:
    """The journal is written by the real scheduler, not by a stand-in for it."""
    from orchestrator.journal import open_journal
    from orchestrator.verify import VerifyResult

    journal = open_journal(tmp_path / "run-j", "run-j", 1)
    graph = parse_graph(
        {
            "tasks": [
                {"id": "direct", "persona": "planner", "task": "Plan"},
                {"id": "repo", "repo": "o/r", "persona": "backend-engineer", "task": "Patch"},
                {"id": "review", "kind": "human", "task": "Approve the change", "deps": ["repo"]},
            ]
        }
    )
    merged = _lifecycle(
        outcome="merged",
        branch="feature",
        steps=[StepResult(id="main", persona="backend-engineer", status="done")],
        verify=VerifyResult(True, ["just", "gate"], ""),
    )

    run_graph(
        graph,
        agent_runner=lambda node: _report(node.persona),
        lifecycle_runner=lambda node: merged,
        journal=journal,
    )

    events = journal.events()
    seen = {(e.kind, e.node, e.step) for e in events}
    assert ("node-started", "direct", None) in seen
    assert ("node-settled", "direct", None) in seen
    assert ("branch-discovered", "repo", None) in seen
    assert ("step-settled", "repo", "main") in seen
    assert ("verification-finished", "repo", None) in seen
    assert ("human-waiting", "review", None) in seen
    # Sequence numbers are dense and monotonic even though nodes ran concurrently.
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    verification = next(e for e in events if e.kind == "verification-finished")
    assert verification.detail == {"ok": True, "command": ["just", "gate"]}


def test_run_graph_without_a_journal_is_unchanged(tmp_path: Path) -> None:
    graph = parse_graph({"tasks": [{"id": "a", "persona": "p", "task": "t"}]})
    result = run_graph(
        graph,
        agent_runner=lambda node: _report("p"),
        lifecycle_runner=lambda node: _lifecycle(),
    )
    assert result.state == "complete"


def test_parse_graph_accepts_old_direct_and_lifecycle_nodes() -> None:
    graph = parse_graph(
        {
            "tasks": [
                {"id": "direct", "persona": "planner", "task": "Plan"},
                {
                    "id": "repo",
                    "repo": "o/r",
                    "persona": "backend-engineer",
                    "task": "Patch",
                    "deps": ["direct"],
                },
            ]
        }
    )

    assert graph.tasks[0].direct is not None
    assert graph.tasks[1].lifecycle is not None
    assert graph.tasks[1].deps == ["direct"]


@pytest.mark.parametrize("field, value", [("persona", "p"), ("persona", None), ("repo", None)])
def test_human_node_validation_is_strict(field, value) -> None:
    with pytest.raises(PlanError, match=f"cannot set '{field}'"):
        parse_graph({"tasks": [{"id": "review", "kind": "human", "task": "Review", field: value}]})


@pytest.mark.parametrize(
    "plan, match",
    [
        ({"tasks": []}, "non-empty 'tasks'"),
        (
            {"concurrency": 0, "tasks": [{"id": "a", "persona": "p", "task": "t"}]},
            "concurrency",
        ),
        ({"tasks": ["x"]}, "must be a mapping"),
        ({"tasks": [{"persona": "p", "task": "t"}]}, "non-empty string 'id'"),
        (
            {
                "tasks": [
                    {"id": "a", "persona": "p", "task": "t"},
                    {"id": "a", "persona": "p", "task": "t"},
                ]
            },
            "duplicate",
        ),
        ({"tasks": [{"id": "a", "kind": "robot", "task": "t"}]}, "kind"),
        ({"tasks": [{"id": "a", "kind": "human", "task": ""}]}, "non-empty 'task'"),
        (
            {"tasks": [{"id": "release/approval", "kind": "human", "task": "Approve"}]},
            "reserved for NODE_ID/STEP_ID",
        ),
        (
            {"tasks": [{"id": "a", "kind": "human", "task": "t", "deps": "b"}]},
            "deps",
        ),
        (
            {"tasks": [{"id": "a", "kind": "human", "task": "t", "deps": ["missing"]}]},
            "unknown task",
        ),
        (
            {"tasks": [{"id": "a", "kind": "human", "task": "t", "deps": ["a"]}]},
            "depends on itself",
        ),
    ],
)
def test_parse_graph_rejects_bad_inputs(plan, match) -> None:
    with pytest.raises(PlanError, match=match):
        parse_graph(plan)


def test_top_level_human_waits_and_blocks_transitively() -> None:
    graph = parse_graph(
        {
            "tasks": [
                {"id": "review", "kind": "human", "task": "Approve the change"},
                {"id": "ship", "persona": "backend-engineer", "task": "Ship", "deps": ["review"]},
                {"id": "announce", "persona": "docs-writer", "task": "Announce", "deps": ["ship"]},
            ]
        }
    )

    result = run_graph(
        graph,
        agent_runner=lambda node: _report(node.persona),
        lifecycle_runner=lambda node: _lifecycle(),
    )
    payload = graph_payload(result)

    assert result.state == "waiting"
    assert result.results["review"].status == "waiting"
    assert result.results["ship"].status == "blocked"
    assert result.results["announce"].blocked_by == ["review"]
    assert payload["results"]["review"]["human_actions"][0]["unblocks"] == ["ship"]
    summary = result.summary()
    assert "Awaiting 1 human action" in summary
    assert "blocked by: review" in summary


def test_failure_precedence_over_waiting_dependency() -> None:
    graph = parse_graph(
        {
            "tasks": [
                {"id": "bad", "persona": "backend-engineer", "task": "Fail"},
                {"id": "review", "kind": "human", "task": "Review"},
                {
                    "id": "after",
                    "persona": "backend-engineer",
                    "task": "After",
                    "deps": ["bad", "review"],
                },
            ]
        }
    )

    def agent(node: PlanNode) -> Report:
        return _report(node.persona, completed=node.id != "bad")

    result = run_graph(graph, agent_runner=agent, lifecycle_runner=lambda node: _lifecycle())

    assert result.state == "failed"
    assert result.results["review"].status == "waiting"
    assert result.results["after"].status == "skipped"
    assert result.results["after"].blocked_by == []


def test_lifecycle_failure_sets_failed_state() -> None:
    graph = parse_graph(
        {"tasks": [{"id": "work", "repo": "o/r", "persona": "backend-engineer", "task": "Patch"}]}
    )

    result = run_graph(
        graph,
        agent_runner=lambda node: _report(node.persona),
        lifecycle_runner=lambda node: _lifecycle("gate-failed", detail="gate failed"),
    )

    assert result.state == "failed"
    assert result.results["work"].status == "failed"
    assert result.results["work"].error == "gate failed"


def test_lifecycle_human_step_waiting_action_uses_nested_ref() -> None:
    graph = parse_graph(
        {
            "tasks": [
                {
                    "id": "work",
                    "repo": "o/r",
                    "steps": [
                        {"id": "agent", "persona": "backend-engineer", "task": "Prepare"},
                        {"id": "approve", "kind": "human", "task": "Approve", "deps": ["agent"]},
                    ],
                },
                {"id": "after", "persona": "backend-engineer", "task": "After", "deps": ["work"]},
            ]
        }
    )

    def lifecycle(node) -> LifecycleResult:
        return _lifecycle(
            "waiting-human",
            detail="paused",
            steps=[
                StepResult(
                    "agent", "backend-engineer", "done", "agent", _report("backend-engineer")
                ),
                StepResult("approve", None, "waiting", "human", None),
            ],
            waiting_steps=["approve"],
        )

    result = run_graph(
        graph,
        agent_runner=lambda node: _report(node.persona),
        lifecycle_runner=lifecycle,
    )
    action = graph_payload(result)["results"]["work"]["human_actions"][0]

    assert action["ref"] == "work/approve"
    assert action["unblocks_publication"]
    assert result.results["after"].blocked_by == ["work/approve"]


def test_lifecycle_human_step_waiting_action_with_internal_downstream() -> None:
    graph = parse_graph(
        {
            "tasks": [
                {
                    "id": "work",
                    "repo": "o/r",
                    "steps": [
                        {"id": "approve", "kind": "human", "task": "Approve"},
                        {
                            "id": "finish",
                            "persona": "backend-engineer",
                            "task": "Finish",
                            "deps": ["approve"],
                        },
                    ],
                }
            ]
        }
    )

    result = run_graph(
        graph,
        agent_runner=lambda node: _report(node.persona),
        lifecycle_runner=lambda node: _lifecycle(
            "waiting-human",
            detail="paused",
            steps=[StepResult("approve", None, "waiting", "human", None)],
            waiting_steps=["approve"],
        ),
    )
    action = graph_payload(result)["results"]["work"]["human_actions"][0]
    assert action["unblocks"] == ["work/finish"]
    assert not action["unblocks_publication"]


def test_parse_graph_accepts_human_steps_and_rejects_agent_fields() -> None:
    graph = parse_graph(
        {
            "tasks": [
                {
                    "id": "work",
                    "repo": "o/r",
                    "steps": [{"id": "approve", "kind": "human", "task": "Approve"}],
                }
            ]
        }
    )

    assert graph.tasks[0].lifecycle is not None
    assert graph.tasks[0].lifecycle.steps == [Step("approve", None, "Approve", "human")]
    with pytest.raises(PlanError, match="reserved for NODE_ID/STEP_ID"):
        parse_graph(
            {
                "tasks": [
                    {
                        "id": "work",
                        "repo": "o/r",
                        "steps": [
                            {
                                "id": "security/approval",
                                "kind": "human",
                                "task": "Approve",
                            }
                        ],
                    }
                ]
            }
        )
    with pytest.raises(PlanError, match="human step 'approve' cannot set 'persona'"):
        parse_graph(
            {
                "tasks": [
                    {
                        "id": "work",
                        "repo": "o/r",
                        "steps": [
                            {
                                "id": "approve",
                                "kind": "human",
                                "task": "Approve",
                                "persona": "backend-engineer",
                            }
                        ],
                    }
                ]
            }
        )
    with pytest.raises(PlanError, match="human step 'approve' cannot set 'persona'"):
        parse_graph(
            {
                "tasks": [
                    {
                        "id": "work",
                        "repo": "o/r",
                        "steps": [
                            {
                                "id": "approve",
                                "kind": "human",
                                "task": "Approve",
                                "persona": None,
                            }
                        ],
                    }
                ]
            }
        )


def test_graph_payload_is_json_serializable() -> None:
    graph = parse_graph({"tasks": [{"id": "review", "kind": "human", "task": "Approve"}]})
    result = run_graph(
        graph,
        agent_runner=lambda node: _report(node.persona),
        lifecycle_runner=lambda node: _lifecycle(),
    )

    json.dumps(graph_payload(result))


def test_run_graph_direct_success_and_lifecycle_payload() -> None:
    direct = parse_graph({"tasks": [{"id": "a", "persona": "backend-engineer", "task": "A"}]})
    direct_result = run_graph(
        direct,
        agent_runner=lambda node: _report(node.persona),
        lifecycle_runner=lambda node: _lifecycle(),
    )
    assert direct_result.ok
    assert graph_payload(direct_result)["results"]["a"]["completed"] is True

    lifecycle = parse_graph(
        {"tasks": [{"id": "life", "repo": "o/r", "persona": "backend-engineer", "task": "L"}]}
    )
    lifecycle_result = run_graph(
        lifecycle,
        agent_runner=lambda node: _report(node.persona),
        lifecycle_runner=lambda node: _lifecycle("pr-open", detail="opened"),
    )
    payload = graph_payload(lifecycle_result)
    assert payload["results"]["life"]["outcome"] == "pr-open"


def test_render_action_helpers_cover_downstream_variants() -> None:
    assert HumanAction("a", "Task", ("b",)).downstream() == "b"
    assert HumanAction("a", "Task", (), True).downstream() == "workstream publication"
    assert HumanAction("a", "Task").downstream() == "nothing downstream"
    assert render_actions([]) == []
    long = "x" * 100
    assert first_line(long).endswith("...")


def test_run_plan_cli_records_by_default_for_human_plan(tmp_path, capsys) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"tasks": [{"id": "review", "kind": "human", "task": "Approve"}]}),
        encoding="utf-8",
    )

    rc = main(
        [
            str(plan),
            "--run",
            "demo",
            "--runs-dir",
            str(tmp_path / "runs"),
            "--format",
            "json",
        ]
    )

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "waiting"
    recorded = json.loads((tmp_path / "runs" / "demo" / "round-01" / "result.json").read_text())
    assert recorded["results"]["review"]["human_actions"][0]["ref"] == "review"


def test_load_graph_wraps_config_errors(tmp_path) -> None:
    with pytest.raises(PlanError):
        load_graph(tmp_path / "missing.json")


def test_load_graph_success(tmp_path) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"tasks": [{"id": "review", "kind": "human", "task": "Approve"}]}),
        encoding="utf-8",
    )
    assert load_graph(plan).tasks[0].id == "review"


def test_run_plan_cli_invalid_input_exits_2(tmp_path, capsys) -> None:
    plan = tmp_path / "bad.json"
    plan.write_text(json.dumps({"tasks": []}), encoding="utf-8")
    assert main([str(plan)]) == 2
    assert "run-plan:" in capsys.readouterr().err


def test_run_plan_cli_invalid_concurrency_override_exits_2(tmp_path, capsys) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"tasks": [{"id": "review", "kind": "human", "task": "Approve"}]}),
        encoding="utf-8",
    )

    assert main([str(plan), "--concurrency", "0", "--no-record"]) == 2
    assert "positive integer" in capsys.readouterr().err


def test_run_plan_cli_reports_pending_round_claim_failure(tmp_path, capsys) -> None:
    from orchestrator.runs import prepare_round

    plan = tmp_path / "plan.json"
    payload = {"tasks": [{"id": "review", "kind": "human", "task": "Approve"}]}
    plan.write_text(json.dumps(payload), encoding="utf-8")
    prepare_round(tmp_path / "runs" / "demo", payload)

    assert main([str(plan), "--run", "demo", "--runs-dir", str(tmp_path / "runs")]) == 2
    assert "could not claim run" in capsys.readouterr().err


def test_run_plan_cli_reports_recording_failure(monkeypatch, tmp_path, capsys) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"tasks": [{"id": "review", "kind": "human", "task": "Approve"}]}),
        encoding="utf-8",
    )

    def fail_write(round_dir, payload):
        raise ConfigError("ledger full")

    from orchestrator.config import ConfigError

    monkeypatch.setattr("orchestrator.graph.write_result", fail_write)
    assert main([str(plan), "--run", "demo", "--runs-dir", str(tmp_path / "runs")]) == 2
    assert "could not record run" in capsys.readouterr().err


def test_run_plan_cli_no_record_does_not_create_runs(tmp_path, capsys) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"tasks": [{"id": "review", "kind": "human", "task": "Approve"}]}),
        encoding="utf-8",
    )
    runs = tmp_path / "runs"

    assert main([str(plan), "--runs-dir", str(runs), "--no-record", "--format", "json"]) == 1
    assert not runs.exists()
    assert json.loads(capsys.readouterr().out)["state"] == "waiting"


def test_print_continuation_complete_and_failed_without_humans(tmp_path, capsys) -> None:
    complete = {"ok": True, "state": "complete", "started_order": [], "results": {}}
    print_continuation("demo", 1, tmp_path, complete, Path("runs"))
    assert "nothing to iterate" in capsys.readouterr().err

    failed = {
        "ok": False,
        "state": "failed",
        "started_order": ["a"],
        "results": {"a": {"status": "failed"}},
    }
    print_continuation("demo", 1, tmp_path, failed, tmp_path / "runs")
    assert "edits.json" in capsys.readouterr().err


def test_repo_plan_alias_warns(monkeypatch, capsys) -> None:
    monkeypatch.setattr("orchestrator.graph.main", lambda argv=None: 0)
    assert main_repo_plan(["plan.json"]) == 0
    assert "deprecated" in capsys.readouterr().err
