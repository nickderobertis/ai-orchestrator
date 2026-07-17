"""Unit tests for the canonical tracked graph executor."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from orchestrator.dispatch import DispatchError, Report
from orchestrator.gitops import GitError
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
from orchestrator.journal import JournalError, NodeSink, open_journal
from orchestrator.lifecycle import LifecycleResult, RepoPlanNode, Step, StepResult
from orchestrator.plan import PlanError, PlanNode
from orchestrator.runs import NodeId, RunId


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
    """The journal is written by the real scheduler, not by a stand-in for it.

    Only the transitions `run_graph` itself owns are asserted here. A lifecycle's
    own events (branch-discovered, step-settled, verification-*) are journaled from
    inside the lifecycle against the scope it is handed, so they are proven there —
    against the real lifecycle — rather than against a runner double that could
    only ever restate what this test told it to say.
    """
    journal = open_journal(tmp_path / "run-j", RunId("run-j"), 1)
    graph = parse_graph(
        {
            "tasks": [
                {"id": "direct", "persona": "planner", "task": "Plan"},
                {"id": "repo", "repo": "o/r", "persona": "backend-engineer", "task": "Patch"},
                {"id": "review", "kind": "human", "task": "Approve the change", "deps": ["repo"]},
            ]
        }
    )

    run_graph(
        graph,
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lambda node, **_: _lifecycle(outcome="merged", branch="feature"),
        journal=journal,
        run_id=RunId("run-j"),
        round_number=1,
    )

    events = journal.events()
    seen = {(e.kind, e.node, e.step) for e in events}
    assert ("node-started", "direct", None) in seen
    assert ("node-settled", "direct", None) in seen
    assert ("node-started", "repo", None) in seen
    assert ("node-settled", "repo", None) in seen
    assert ("human-waiting", "review", None) in seen
    # Sequence numbers are dense and monotonic even though nodes ran concurrently.
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    settled = next(e for e in events if e.kind == "node-settled" and e.node == "repo")
    assert settled.detail == {"status": "done", "outcome": "merged"}
    assert all(e.round == 1 and e.run_id == "run-j" for e in events)


def test_run_graph_journals_a_direct_node_whose_runner_raised(tmp_path: Path) -> None:
    """A runner that raises is a node failure, and the journal must say so.

    `schedule_dag` catches whatever a runner raises and settles the node as failed,
    so the ledger records it. If the journal records no matching transition, the
    round leaves a `node-started` with nothing to close it — which is exactly the
    reading a started-with-no-settled event is reserved for: work still in flight.
    """
    journal = open_journal(tmp_path / "run-x", RunId("run-x"), 1)
    graph = parse_graph({"tasks": [{"id": "api", "persona": "backend-engineer", "task": "A"}]})

    def boom(node: PlanNode, **_: object) -> Report:
        raise DispatchError("onejudge could not start")

    result = run_graph(
        graph,
        agent_runner=boom,
        lifecycle_runner=lambda node, **_: _lifecycle(),
        journal=journal,
        run_id=RunId("run-x"),
        round_number=1,
    )

    assert result.results["api"].status == "failed"
    events = journal.events()
    assert [(e.kind, e.node) for e in events] == [("node-started", "api"), ("node-failed", "api")]
    assert "onejudge could not start" in str(events[1].detail["detail"])


def test_run_graph_journals_a_lifecycle_node_whose_runner_raised(tmp_path: Path) -> None:
    """The same holds for a lifecycle node: a raised error still settles the journal."""
    journal = open_journal(tmp_path / "run-y", RunId("run-y"), 1)
    graph = parse_graph(
        {"tasks": [{"id": "api", "repo": "o/r", "persona": "backend-engineer", "task": "A"}]}
    )

    def boom(node: RepoPlanNode, *, journal: NodeSink | None = None) -> LifecycleResult:
        raise GitError("origin rejected the push")

    result = run_graph(
        graph,
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=boom,
        journal=journal,
        run_id=RunId("run-y"),
        round_number=1,
    )

    assert result.results["api"].status == "failed"
    assert [(e.kind, e.node) for e in journal.events()] == [
        ("node-started", "api"),
        ("node-failed", "api"),
    ]


def test_run_graph_labels_each_dispatch_with_its_place_in_the_graph(tmp_path: Path) -> None:
    """A direct node's dispatch is labelled with the round and node it belongs to.

    These labels are the only thing tying a recorded oneharness session back to the
    node that produced it, so they are asserted at the seam the real dispatch is
    called through rather than off the journal, which a subprocess never sees.
    """
    journal = open_journal(tmp_path / "run-l", RunId("run-l"), 7)
    graph = parse_graph(
        {
            "tasks": [
                {"id": "api", "persona": "backend-engineer", "task": "A"},
                {"id": "web", "persona": "frontend-engineer", "task": "B"},
            ]
        }
    )
    seen: dict[str, dict[str, str]] = {}

    def agent(node: PlanNode, *, labels: Mapping[str, str] | None = None) -> Report:
        seen[node.id] = dict(labels or {})
        return _report(node.persona)

    run_graph(
        graph,
        agent_runner=agent,
        lifecycle_runner=lambda node, **_: _lifecycle(),
        journal=journal,
        run_id=RunId("run-l"),
        round_number=7,
    )

    assert seen["api"] == {"run_id": "run-l", "round": "7", "node": "api"}
    assert seen["web"] == {"run_id": "run-l", "round": "7", "node": "web"}


def test_run_graph_scopes_each_lifecycle_to_its_own_node() -> None:
    """A lifecycle is handed a scope it cannot use to speak for a sibling node."""
    graph = parse_graph(
        {
            "tasks": [
                {"id": "api", "repo": "o/r", "persona": "backend-engineer", "task": "A"},
                {"id": "web", "repo": "o/r", "persona": "frontend-engineer", "task": "B"},
            ]
        }
    )
    scopes: dict[str, NodeSink] = {}

    def lifecycle(node: RepoPlanNode, *, journal: NodeSink | None = None) -> LifecycleResult:
        assert journal is not None
        scopes[node.id] = journal
        return _lifecycle()

    run_graph(
        graph,
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lifecycle,
    )

    assert scopes["api"].labels == {"node": "api"}
    with pytest.raises(JournalError, match="cannot append an event for node 'web'"):
        scopes["api"].append("branch-discovered", node=NodeId("web"))


def test_run_graph_without_a_journal_is_unchanged(tmp_path: Path) -> None:
    graph = parse_graph({"tasks": [{"id": "a", "persona": "p", "task": "t"}]})
    result = run_graph(
        graph,
        agent_runner=lambda node, **_: _report("p"),
        lifecycle_runner=lambda node, **_: _lifecycle(),
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
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lambda node, **_: _lifecycle(),
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

    def agent(node: PlanNode, **_: object) -> Report:
        return _report(node.persona, completed=node.id != "bad")

    result = run_graph(
        graph, agent_runner=agent, lifecycle_runner=lambda node, **_: _lifecycle()
    )

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
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lambda node, **_: _lifecycle("gate-failed", detail="gate failed"),
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

    def lifecycle(node, **_: object) -> LifecycleResult:
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
        agent_runner=lambda node, **_: _report(node.persona),
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
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lambda node, **_: _lifecycle(
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
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lambda node, **_: _lifecycle(),
    )

    json.dumps(graph_payload(result))


def test_run_graph_direct_success_and_lifecycle_payload() -> None:
    direct = parse_graph({"tasks": [{"id": "a", "persona": "backend-engineer", "task": "A"}]})
    direct_result = run_graph(
        direct,
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lambda node, **_: _lifecycle(),
    )
    assert direct_result.ok
    assert graph_payload(direct_result)["results"]["a"]["completed"] is True

    lifecycle = parse_graph(
        {"tasks": [{"id": "life", "repo": "o/r", "persona": "backend-engineer", "task": "L"}]}
    )
    lifecycle_result = run_graph(
        lifecycle,
        agent_runner=lambda node, **_: _report(node.persona),
        lifecycle_runner=lambda node, **_: _lifecycle("pr-open", detail="opened"),
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
