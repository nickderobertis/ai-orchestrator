"""Unit tests for across-round replanning (`orchestrator.replan`)."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from orchestrator.plan import PlanError
from orchestrator.replan import main, next_round


def _plan(*tasks: dict) -> dict:
    return {"concurrency": 2, "tasks": deepcopy(tasks)}


def _result(**statuses: str) -> dict:
    return {"results": {nid: {"status": s, "outcome": s} for nid, s in statuses.items()}}


A = {"id": "a", "repo": "o/r", "persona": "engineer", "task": "A"}
B = {"id": "b", "repo": "o/r", "persona": "reviewer", "task": "B", "deps": ["a"]}
H = {"id": "h", "kind": "human", "task": "Review the change"}
DIRECT_AFTER_H = {"id": "after", "persona": "engineer", "task": "After", "deps": ["h"]}


def test_done_node_is_carried_out_and_dep_satisfied() -> None:
    # a merged, b failed: next round drops a (done) and b keeps running with its
    # dep on a dropped (satisfied — a is on the base branch now).
    plan = next_round(_plan(A, B), _result(a="done", b="failed"))
    ids = [t["id"] for t in plan["tasks"]]
    assert ids == ["b"]
    assert plan["tasks"][0]["deps"] == []  # dep on merged 'a' satisfied


def test_done_open_dependency_becomes_stack_anchor_across_rounds() -> None:
    result = {
        "results": {
            "a": {
                "status": "done",
                "outcome": "pr-open",
                "repo": "o/r",
                "publication_identity": "https://github.com/o/r",
                "branch": "feature/a",
                "base_branch": "main",
                "pr_base": "main",
                "pr": "https://github.com/o/r/pull/1",
            },
            "b": {"status": "failed", "outcome": "gate-failed"},
        }
    }

    plan = next_round(_plan(A, B), result)

    b = plan["tasks"][0]
    assert b["deps"] == []
    assert b["stack_bases"] == [
        {
            "branch": "feature/a",
            "repo": "o/r",
            "identity": "https://github.com/o/r",
            "base_branch": "main",
            "pr": "https://github.com/o/r/pull/1",
            "pr_base": "main",
        }
    ]


def test_open_dependency_stack_anchor_passes_through_completed_human_gate() -> None:
    parent = {**A, "branch": "feature/a"}
    approval = {**H, "deps": ["a"]}
    child = {**B, "deps": ["h"]}
    result = {
        "results": {
            "a": {
                "status": "done",
                "outcome": "pr-open",
                "repo": "o/r",
                "publication_identity": "https://github.com/o/r",
                "branch": "feature/a",
                "base_branch": "main",
                "pr_base": "main",
                "pr": "https://github.com/o/r/pull/1",
            },
            "h": {
                "kind": "human",
                "status": "waiting",
                "human_actions": [
                    {
                        "ref": "h",
                        "task": "Review the change",
                        "unblocks": ["b"],
                        "unblocks_publication": False,
                    }
                ],
            },
            "b": {"status": "blocked", "blocked_by": ["h"]},
        }
    }

    plan = next_round(_plan(parent, approval, child), result, {"complete_human": ["h"]})

    assert plan["tasks"] == [
        {
            **child,
            "deps": [],
            "stack_bases": [
                {
                    "branch": "feature/a",
                    "repo": "o/r",
                    "identity": "https://github.com/o/r",
                    "base_branch": "main",
                    "pr": "https://github.com/o/r/pull/1",
                    "pr_base": "main",
                }
            ],
        }
    ]


def test_merged_dependency_landed_on_synthetic_base_carries_that_base() -> None:
    result = {
        "results": {
            "a": {
                "status": "done",
                "outcome": "merged",
                "repo": "o/r",
                "publication_identity": "identity",
                "branch": "feature/a",
                "base_branch": "main",
                "pr_base": "ai-orchestrator/stack-base/parents",
                "pr": "https://github.com/o/r/pull/1",
            },
            "b": {"status": "failed"},
        }
    }

    plan = next_round(_plan(A, B), result)

    assert plan["tasks"][0]["stack_bases"][0]["branch"] == ("ai-orchestrator/stack-base/parents")
    assert plan["tasks"][0]["stack_bases"][0]["pr_base"] == ("ai-orchestrator/stack-base/parents")


def test_cross_round_anchor_replaces_duplicate_branch_with_current_metadata() -> None:
    prior = _plan(A, B)
    prior["tasks"][1]["stack_bases"] = [
        {
            "branch": "feature/a",
            "repo": "o/r",
            "identity": "https://github.com/o/r",
            "base_branch": "main",
            "pr": "https://github.com/o/r/pull/old",
        }
    ]
    result = {
        "results": {
            "a": {
                "status": "done",
                "outcome": "pr-open",
                "repo": "o/r",
                "publication_identity": "https://github.com/o/r",
                "branch": "feature/a",
                "base_branch": "main",
                "pr_base": "main",
                "pr": "https://github.com/o/r/pull/2",
            },
            "b": {"status": "failed"},
        }
    }

    plan = next_round(prior, result)

    assert plan["tasks"][0]["stack_bases"] == [
        {
            "branch": "feature/a",
            "repo": "o/r",
            "identity": "https://github.com/o/r",
            "base_branch": "main",
            "pr": "https://github.com/o/r/pull/2",
            "pr_base": "main",
        }
    ]


def test_retry_overrides_fields() -> None:
    plan = next_round(
        _plan(A, B),
        _result(a="done", b="failed"),
        {"retry": {"b": {"max_turns": 12, "persona": "test-engineer"}}},
    )
    b = plan["tasks"][0]
    assert b["max_turns"] == 12 and b["persona"] == "test-engineer"


def test_split_replaces_a_node() -> None:
    plan = next_round(
        _plan(A, B),
        _result(a="done", b="failed"),
        {
            "split": {
                "b": [
                    {"id": "b1", "repo": "o/r", "persona": "reviewer", "task": "B1"},
                    {
                        "id": "b2",
                        "repo": "o/r",
                        "persona": "reviewer",
                        "task": "B2",
                        "deps": ["b1"],
                    },
                ]
            }
        },
    )
    ids = [t["id"] for t in plan["tasks"]]
    assert ids == ["b1", "b2"] and "b" not in ids


def test_add_new_node() -> None:
    plan = next_round(
        _plan(A, B),
        _result(a="done", b="done"),  # both merged → only the added node remains
        {"add": [{"id": "c", "repo": "o/r", "persona": "docs-writer", "task": "C", "deps": ["a"]}]},
    )
    ids = [t["id"] for t in plan["tasks"]]
    assert ids == ["c"] and plan["tasks"][0]["deps"] == []  # dep on merged 'a' satisfied


def test_complete_top_level_human_releases_dependent() -> None:
    result = {
        "results": {
            "h": {
                "kind": "human",
                "status": "waiting",
                "human_actions": [
                    {
                        "ref": "h",
                        "task": "Review the change",
                        "unblocks": ["after"],
                        "unblocks_publication": False,
                    }
                ],
            },
            "after": {"status": "blocked", "blocked_by": ["h"]},
        }
    }

    plan = next_round(_plan(H, DIRECT_AFTER_H), result, {"complete_human": ["h"]})

    assert [task["id"] for task in plan["tasks"]] == ["after"]
    assert plan["tasks"][0]["deps"] == []


def test_complete_nested_human_step_updates_resume() -> None:
    work = {
        "id": "work",
        "repo": "o/r",
        "steps": [
            {"id": "prepare", "persona": "engineer", "task": "Prepare"},
            {"id": "approve", "kind": "human", "task": "Approve", "deps": ["prepare"]},
            {"id": "finish", "persona": "engineer", "task": "Finish", "deps": ["approve"]},
        ],
    }
    result = {
        "results": {
            "work": {
                "status": "waiting",
                "outcome": "waiting-human",
                "waiting_steps": ["approve"],
                "human_actions": [
                    {
                        "ref": "work/approve",
                        "task": "Approve",
                        "unblocks": ["work/finish"],
                        "unblocks_publication": False,
                    }
                ],
                "resume": {
                    "branch": "feature/work",
                    "base_branch": "main",
                    "pr_base": "main",
                    "checkpoint": "abcdef1",
                    "completed_steps": ["prepare"],
                    "pr": None,
                },
            }
        }
    }

    plan = next_round(_plan(work), result, {"complete_human": ["work/approve"]})

    assert plan["tasks"][0]["resume"]["completed_steps"] == ["prepare", "approve"]


def test_nested_completion_handles_legacy_lifecycle_node_id_with_slash() -> None:
    work = {
        "id": "release/work",
        "repo": "o/r",
        "steps": [{"id": "approve", "kind": "human", "task": "Approve"}],
    }
    result = {
        "results": {
            "release/work": {
                "status": "waiting",
                "outcome": "waiting-human",
                "waiting_steps": ["approve"],
                "human_actions": [
                    {
                        "ref": "release/work/approve",
                        "task": "Approve",
                        "unblocks": [],
                        "unblocks_publication": True,
                    }
                ],
                "resume": {
                    "branch": "feature/work",
                    "base_branch": "main",
                    "pr_base": "main",
                    "checkpoint": "abcdef1",
                    "completed_steps": [],
                    "pr": None,
                },
            }
        }
    }

    plan = next_round(_plan(work), result, {"complete_human": ["release/work/approve"]})

    assert plan["tasks"][0]["resume"]["completed_steps"] == ["approve"]


def test_invalid_complete_human_ref_fails_validation() -> None:
    result = {
        "results": {
            "h": {
                "kind": "human",
                "status": "waiting",
                "human_actions": [
                    {"ref": "h", "task": "Review", "unblocks": [], "unblocks_publication": False}
                ],
            }
        }
    }

    with pytest.raises(PlanError, match="recorded waiting human"):
        next_round(_plan(H), result, {"complete_human": ["not-h"]})


def test_complete_human_rejects_fabricated_agent_action() -> None:
    result = {
        "results": {
            "a": {
                "kind": "agent",
                "status": "waiting",
                "human_actions": [
                    {
                        "ref": "a",
                        "task": "Pretend this agent is human",
                        "unblocks": [],
                        "unblocks_publication": False,
                    }
                ],
            }
        }
    }

    with pytest.raises(PlanError, match="recorded waiting human"):
        next_round(_plan(A), result, {"complete_human": ["a"]})


def test_complete_human_edits_must_be_a_unique_list() -> None:
    with pytest.raises(PlanError, match="must be a list"):
        next_round(_plan(H), {"results": {}}, {"complete_human": "h"})
    result = {
        "results": {
            "h": {
                "status": "waiting",
                "human_actions": [
                    {"ref": "h", "task": "Review", "unblocks": [], "unblocks_publication": False}
                ],
            }
        }
    }
    with pytest.raises(PlanError, match="unique"):
        next_round(_plan(H), result, {"complete_human": ["h", "h"]})


def test_waiting_lifecycle_node_requires_resume_metadata() -> None:
    work = {
        "id": "work",
        "repo": "o/r",
        "steps": [{"id": "approve", "kind": "human", "task": "Approve"}],
    }
    result = {
        "results": {
            "work": {
                "status": "waiting",
                "waiting_steps": ["approve"],
                "human_actions": [
                    {
                        "ref": "work/approve",
                        "task": "Approve",
                        "unblocks": [],
                        "unblocks_publication": True,
                    }
                ],
            }
        }
    }

    with pytest.raises(PlanError, match="resume"):
        next_round(_plan(work), result, {"complete_human": ["work/approve"]})


def test_waiting_lifecycle_resume_completed_steps_must_be_list() -> None:
    work = {
        "id": "work",
        "repo": "o/r",
        "steps": [{"id": "approve", "kind": "human", "task": "Approve"}],
    }
    result = {
        "results": {
            "work": {
                "status": "waiting",
                "waiting_steps": ["approve"],
                "human_actions": [
                    {
                        "ref": "work/approve",
                        "task": "Approve",
                        "unblocks": [],
                        "unblocks_publication": True,
                    }
                ],
                "resume": {
                    "branch": "feature/work",
                    "base_branch": "main",
                    "pr_base": "main",
                    "checkpoint": "abcdef1",
                    "completed_steps": "approve",
                    "pr": None,
                },
            }
        }
    }

    with pytest.raises(PlanError, match="completed_steps"):
        next_round(_plan(work), result, {"complete_human": ["work/approve"]})


def test_failed_human_pause_can_be_retried_without_resume_metadata() -> None:
    work = {
        "id": "work",
        "repo": "o/r",
        "steps": [
            {"id": "prepare", "persona": "engineer", "task": "Prepare"},
            {"id": "approve", "kind": "human", "task": "Approve", "deps": ["prepare"]},
        ],
    }
    result = {
        "results": {
            "work": {
                "status": "failed",
                "outcome": "gate-failed",
                "waiting_steps": ["approve"],
                "resume": None,
            }
        }
    }

    assert next_round(_plan(work), result)["tasks"] == [work]


def test_drop_removes_a_node() -> None:
    plan = next_round(_plan(A, B), _result(a="failed", b="failed"), {"drop": ["b"]})
    assert [t["id"] for t in plan["tasks"]] == ["a"]


def test_bad_edit_fails_validation() -> None:
    with pytest.raises(PlanError, match="duplicate"):
        next_round(
            _plan(A, B),
            _result(a="failed", b="failed"),
            {"add": [{"id": "a", "repo": "o/r", "persona": "p", "task": "dup"}]},
        )


@pytest.mark.parametrize(
    "edits, match",
    [
        ({"retry": []}, "'retry' must be a mapping"),
        ({"retry": {"a": []}}, "override mappings"),
        ({"split": []}, "'split' must be a mapping"),
        ({"split": {"a": {}}}, "replacement nodes"),
        ({"add": {}}, "list of task mappings"),
        ({"drop": "a"}, "unique list"),
        ({"drop": ["a", "a"]}, "unique list"),
    ],
)
def test_malformed_edits_fail_as_invalid_input(edits, match) -> None:
    with pytest.raises(PlanError, match=match):
        next_round(_plan(A), _result(a="failed"), edits)


def test_empty_next_round_signals_nothing_to_iterate() -> None:
    assert next_round(_plan(A, B), _result(a="done", b="done"))["tasks"] == []


# --- CLI -------------------------------------------------------------------


def _write(tmp_path, name: str, obj: dict) -> str:
    p = tmp_path / name
    p.write_text(json.dumps(obj), encoding="utf-8")
    return str(p)


def test_main_writes_next_plan(tmp_path, capsys) -> None:
    prev = _write(tmp_path, "plan.json", _plan(A, B))
    res = _write(tmp_path, "res.json", _result(a="done", b="failed"))
    edits = _write(tmp_path, "edits.json", {"retry": {"b": {"max_turns": 9}}})
    rc = main([prev, res, edits])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert [t["id"] for t in out["tasks"]] == ["b"] and out["tasks"][0]["max_turns"] == 9


def test_main_no_edits_and_output_file(tmp_path) -> None:
    prev = _write(tmp_path, "plan.json", _plan(A, B))
    res = _write(tmp_path, "res.json", _result(a="failed", b="failed"))
    out = tmp_path / "next.json"
    rc = main([prev, res, "-o", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert {t["id"] for t in payload["tasks"]} == {"a", "b"}


def test_main_bad_edit_exit_2(tmp_path, capsys) -> None:
    prev = _write(tmp_path, "plan.json", _plan(A, B))
    res = _write(tmp_path, "res.json", _result(a="failed", b="failed"))
    edits = _write(tmp_path, "edits.json", {"add": [A]})
    rc = main([prev, res, edits])
    assert rc == 2 and "replan:" in capsys.readouterr().err
