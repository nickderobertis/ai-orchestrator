"""Unit tests for the canonical tracked graph executor."""

from __future__ import annotations

import json

import pytest

from orchestrator.dispatch import Report
from orchestrator.graph import graph_payload, parse_graph, run_graph
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


def test_human_node_validation_is_strict() -> None:
    with pytest.raises(PlanError, match="cannot set 'persona'"):
        parse_graph(
            {"tasks": [{"id": "review", "kind": "human", "task": "Review", "persona": "p"}]}
        )


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


def test_graph_payload_is_json_serializable() -> None:
    graph = parse_graph({"tasks": [{"id": "review", "kind": "human", "task": "Approve"}]})
    result = run_graph(
        graph,
        agent_runner=lambda node: _report(node.persona),
        lifecycle_runner=lambda node: _lifecycle(),
    )

    json.dumps(graph_payload(result))
