"""E2E: run a real task DAG through onejudge via `run-plan`.

Drives the actual onejudge CLI once per node (model faked via the command
backend), proving the scheduler dispatches real processes down the dependency
tree in parallel and cascades a real failure.
"""

from __future__ import annotations

import json

from orchestrator import PERSONA_DIR
from orchestrator.plan import main


def _write_plan(tmp_path, obj: dict) -> str:
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return str(p)


def _run(plan_path: str, command_base, onejudge_bin, capsys) -> dict:
    rc = main(
        [
            plan_path,
            "--base",
            str(command_base()),
            "--persona-dir",
            str(PERSONA_DIR),
            "--onejudge-bin",
            onejudge_bin,
            "--format",
            "json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    payload["_rc"] = rc
    return payload


def test_diamond_dag_all_complete(tmp_path, command_base, onejudge_bin, capsys) -> None:
    plan = _write_plan(
        tmp_path,
        {
            "concurrency": 4,
            "tasks": [
                {"id": "design", "persona": "planner", "task": "Design the feature."},
                {
                    "id": "api",
                    "persona": "backend-engineer",
                    "task": "Build the API.",
                    "deps": ["design"],
                },
                {
                    "id": "ui",
                    "persona": "frontend-engineer",
                    "task": "Build the UI.",
                    "deps": ["design"],
                },
                {
                    "id": "review",
                    "persona": "reviewer",
                    "task": "Review it.",
                    "deps": ["api", "ui"],
                },
            ],
        },
    )
    result = _run(plan, command_base, onejudge_bin, capsys)
    assert result["_rc"] == 0
    assert result["ok"] is True
    assert all(r["status"] == "done" for r in result["results"].values())
    order = result["started_order"]
    assert order[0] == "design"
    assert order.index("design") < order.index("api") < order.index("review")
    assert order.index("design") < order.index("ui") < order.index("review")


def test_failure_cascades_to_dependents(tmp_path, command_base, onejudge_bin, capsys) -> None:
    plan = _write_plan(
        tmp_path,
        {
            "concurrency": 2,
            "tasks": [
                {"id": "design", "persona": "planner", "task": "should-fail: unsatisfiable."},
                {
                    "id": "api",
                    "persona": "backend-engineer",
                    "task": "Build on the design.",
                    "deps": ["design"],
                },
                {"id": "review", "persona": "reviewer", "task": "Review the API.", "deps": ["api"]},
            ],
        },
    )
    result = _run(plan, command_base, onejudge_bin, capsys)
    assert result["_rc"] == 1
    assert result["ok"] is False
    assert result["results"]["design"]["status"] == "failed"
    assert result["results"]["api"]["status"] == "skipped"
    assert result["results"]["review"]["status"] == "skipped"


def test_run_plan_cli_writes_output_file(tmp_path, command_base, onejudge_bin) -> None:
    plan = _write_plan(
        tmp_path, {"tasks": [{"id": "a", "persona": "planner", "task": "complete-now: trivial."}]}
    )
    out = tmp_path / "result.json"
    rc = main(
        [
            plan,
            "--base",
            str(command_base()),
            "--onejudge-bin",
            onejudge_bin,
            "--format",
            "json",
            "-o",
            str(out),
        ]
    )
    assert rc == 0
    assert '"ok"' in out.read_text(encoding="utf-8")
