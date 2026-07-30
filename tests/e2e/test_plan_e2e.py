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
                    "persona": "engineer",
                    "task": "Build the API.",
                    "deps": ["design"],
                },
                {
                    "id": "ui",
                    "persona": "engineer",
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
                    "persona": "engineer",
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


def test_a_node_that_stopped_short_of_its_cap_says_so(
    tmp_path, command_base, onejudge_bin, capsys
) -> None:
    """onejudge exits 1 for both, and the recorded error has to tell them apart.

    Every incomplete node used to be reported as "did not complete (hit the turn
    cap)", including ones that ended on turn 1 — which sends a reader to raise a
    cap that was never approached. Both journeys run here, through the real CLI
    and the real onejudge loop, so the two errors are compared side by side.
    """
    plan = _write_plan(
        tmp_path,
        {
            "concurrency": 2,
            "tasks": [
                {"id": "capped", "persona": "engineer", "task": "should-fail: unsatisfiable."},
                {
                    "id": "released",
                    "persona": "engineer",
                    # The supervisor releases this one on turn 1; the done_when
                    # judge then rejects it, so it settles incomplete at turn 1 of 4.
                    "task": "stop-short: released before its criteria were met.",
                },
            ],
        },
    )

    result = _run(plan, command_base, onejudge_bin, capsys)

    assert result["_rc"] == 1
    capped = result["results"]["capped"]
    released = result["results"]["released"]
    assert capped["status"] == released["status"] == "failed"
    assert capped["error"] == "hit the turn cap after 4 turns"
    assert released["error"].startswith("did not complete after 1 turn, short of its 4-turn cap")
    # And the reason the judge gave travels with it, so the node says why it
    # stopped rather than only how far it got.
    assert "released it before its criteria were met" in released["error"]
