"""Unit tests for plan loading/validation and the DAG scheduler.

The scheduler is tested with an injected fake runner (no onejudge), so scheduling
behavior — ordering, parallelism, and skip-on-failure — is deterministic. The
real onejudge path is covered in tests/e2e/test_plan_e2e.py.
"""

from __future__ import annotations

import json
import threading

import pytest

from orchestrator.dispatch import DispatchError, Report
from orchestrator.plan import (
    NodeRun,
    Plan,
    PlanError,
    PlanNode,
    load_plan,
    parse_cross_dag_dependency,
    run_plan,
    schedule_dag,
)


def _report(persona: str, completed: bool) -> Report:
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


def _plan(tasks: list[PlanNode], concurrency: int = 4) -> Plan:
    return Plan(tasks=tasks, concurrency=concurrency)


# --- scheduling behavior ---------------------------------------------------


def test_diamond_respects_dependencies() -> None:
    plan = _plan(
        [
            PlanNode("a", "p", "t"),
            PlanNode("b", "p", "t", deps=["a"]),
            PlanNode("c", "p", "t", deps=["a"]),
            PlanNode("d", "p", "t", deps=["b", "c"]),
        ]
    )
    result = run_plan(plan, lambda n: _report(n.id, True))
    assert result.ok
    order = result.started_order
    assert order[0] == "a"
    assert order.index("a") < order.index("b") < order.index("d")
    assert order.index("a") < order.index("c") < order.index("d")


def test_independent_nodes_run_in_parallel() -> None:
    barrier = threading.Barrier(2, timeout=5)

    def runner(node: PlanNode) -> Report:
        barrier.wait()  # both must arrive concurrently or this times out
        return _report(node.id, True)

    plan = _plan([PlanNode("a", "p", "t"), PlanNode("b", "p", "t")], concurrency=2)
    result = run_plan(plan, runner)
    assert result.ok


def test_concurrency_one_serializes() -> None:
    barrier = threading.Barrier(2, timeout=1)

    def runner(node: PlanNode) -> Report:
        barrier.wait()
        return _report(node.id, True)

    plan = _plan([PlanNode("a", "p", "t"), PlanNode("b", "p", "t")], concurrency=1)
    result = run_plan(plan, runner)
    # Only one worker, so the barrier never completes: both nodes fail.
    assert not result.ok


def test_failed_node_skips_dependents() -> None:
    plan = _plan(
        [
            PlanNode("a", "p", "t"),
            PlanNode("b", "p", "t", deps=["a"]),
            PlanNode("c", "p", "t", deps=["b"]),
        ],
        concurrency=2,
    )
    result = run_plan(plan, lambda n: _report(n.id, n.id != "a"))
    assert result.results["a"].status == "failed"
    assert result.results["b"].status == "skipped"
    assert result.results["c"].status == "skipped"
    assert not result.ok


def test_runner_exception_is_a_node_failure() -> None:
    def runner(node: PlanNode) -> Report:
        if node.id == "a":
            raise DispatchError("boom")
        return _report(node.id, True)

    plan = _plan([PlanNode("a", "p", "t"), PlanNode("b", "p", "t", deps=["a"])], concurrency=2)
    result = run_plan(plan, runner)
    assert result.results["a"].status == "failed"
    assert "boom" in (result.results["a"].error or "")
    assert result.results["b"].status == "skipped"


def test_concurrency_override_wins() -> None:
    started: list[str] = []
    lock = threading.Lock()

    def runner(node: PlanNode) -> Report:
        with lock:
            started.append(node.id)
        return _report(node.id, True)

    plan = _plan([PlanNode("a", "p", "t"), PlanNode("b", "p", "t")], concurrency=1)
    result = run_plan(plan, runner, concurrency=2)
    assert result.ok
    assert set(started) == {"a", "b"}


def test_reconciler_resumes_running_and_retains_settled_actual_state() -> None:
    called: list[str] = []

    def run_one(node_id: str) -> NodeRun:
        called.append(node_id)
        return NodeRun("done")

    runs, order = schedule_dag(
        ["settled", "interrupted", "downstream"],
        {"settled": [], "interrupted": [], "downstream": ["settled", "interrupted"]},
        run_one,
        concurrency=2,
        actual={"settled": NodeRun("done"), "interrupted": NodeRun("running")},
        started_order=["settled", "interrupted"],
    )

    assert called == ["interrupted", "downstream"]
    assert order == ["settled", "interrupted", "downstream"]
    assert all(run.status == "done" for run in runs.values())


def test_plan_result_summary_and_render() -> None:
    plan = _plan([PlanNode("a", "p", "t")])
    result = run_plan(plan, lambda n: _report(n.id, True))
    assert "completed" in result.summary()


# --- plan loading / validation --------------------------------------------


def _write(tmp_path, obj: dict) -> str:
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return str(p)


def test_load_valid_plan(tmp_path) -> None:
    plan = load_plan(
        _write(
            tmp_path,
            {
                "concurrency": 3,
                "tasks": [
                    {"id": "a", "persona": "planner", "task": "plan it"},
                    {"id": "b", "persona": "reviewer", "task": "review it", "deps": ["a"]},
                ],
            },
        )
    )
    assert plan.concurrency == 3
    assert [t.id for t in plan.tasks] == ["a", "b"]


def test_plan_needs_tasks(tmp_path) -> None:
    with pytest.raises(PlanError, match="non-empty 'tasks'"):
        load_plan(_write(tmp_path, {"tasks": []}))


def test_plan_rejects_bad_concurrency(tmp_path) -> None:
    with pytest.raises(PlanError, match="concurrency"):
        load_plan(
            _write(
                tmp_path, {"concurrency": 0, "tasks": [{"id": "a", "persona": "p", "task": "t"}]}
            )
        )


def test_plan_rejects_duplicate_id(tmp_path) -> None:
    with pytest.raises(PlanError, match="duplicate"):
        load_plan(
            _write(
                tmp_path,
                {
                    "tasks": [
                        {"id": "a", "persona": "p", "task": "t"},
                        {"id": "a", "persona": "p", "task": "t"},
                    ]
                },
            )
        )


def test_plan_rejects_unknown_dep(tmp_path) -> None:
    with pytest.raises(PlanError, match="unknown task"):
        load_plan(
            _write(tmp_path, {"tasks": [{"id": "a", "persona": "p", "task": "t", "deps": ["z"]}]})
        )


def test_plan_rejects_self_dep(tmp_path) -> None:
    with pytest.raises(PlanError, match="depends on itself"):
        load_plan(
            _write(tmp_path, {"tasks": [{"id": "a", "persona": "p", "task": "t", "deps": ["a"]}]})
        )


def test_plan_rejects_cycle(tmp_path) -> None:
    with pytest.raises(PlanError, match="cycle"):
        load_plan(
            _write(
                tmp_path,
                {
                    "tasks": [
                        {"id": "a", "persona": "p", "task": "t", "deps": ["b"]},
                        {"id": "b", "persona": "p", "task": "t", "deps": ["a"]},
                    ]
                },
            )
        )


def test_plan_rejects_missing_persona(tmp_path) -> None:
    with pytest.raises(PlanError, match="needs a 'persona'"):
        load_plan(_write(tmp_path, {"tasks": [{"id": "a", "task": "t"}]}))


def test_plan_rejects_non_mapping_task(tmp_path) -> None:
    with pytest.raises(PlanError, match="must be a mapping"):
        load_plan(_write(tmp_path, {"tasks": ["not-a-dict"]}))


def test_plan_rejects_bad_id(tmp_path) -> None:
    with pytest.raises(PlanError, match="non-empty string 'id'"):
        load_plan(_write(tmp_path, {"tasks": [{"persona": "p", "task": "t"}]}))


def test_plan_rejects_empty_task(tmp_path) -> None:
    with pytest.raises(PlanError, match="non-empty 'task'"):
        load_plan(_write(tmp_path, {"tasks": [{"id": "a", "persona": "p", "task": "  "}]}))


def test_plan_rejects_bad_deps(tmp_path) -> None:
    with pytest.raises(PlanError, match="list of ids"):
        load_plan(
            _write(tmp_path, {"tasks": [{"id": "a", "persona": "p", "task": "t", "deps": "x"}]})
        )


def test_cross_dag_dependency_parser_distinguishes_local_and_validates_shape() -> None:
    assert parse_cross_dag_dependency("local") is None
    parsed = parse_cross_dag_dependency("run:release-12#publish")
    assert parsed is not None
    assert (parsed.run_id, parsed.node_id) == ("release-12", "publish")
    for malformed in ("run:", "run:other", "run:#node", "run:other#", "run:bad/id#node"):
        with pytest.raises(PlanError, match="malformed cross-DAG dependency"):
            parse_cross_dag_dependency(malformed)
