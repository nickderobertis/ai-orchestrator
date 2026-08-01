"""Unit tests for across-round replanning (`orchestrator.replan`)."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from orchestrator.plan import PlanError
from orchestrator.replan import (
    MAX_AUTOMATIC_ROUND_RESUMES,
    _apply_lifecycle_resume,
    main,
    next_round,
)


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
        {"retry": {"b": {"max_turns": 12, "persona": "engineer"}}},
    )
    b = plan["tasks"][0]
    assert b["max_turns"] == 12 and b["persona"] == "engineer"


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


def test_pinned_branch_keeps_a_waiting_workstream_resuming_its_human_steps() -> None:
    work = {
        "id": "work",
        "repo": "o/r",
        "branch": "feature/work",
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

    assert plan["tasks"][0]["branch"] == "feature/work"
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


def test_failed_lifecycle_carries_preserved_resume_without_retry_edit() -> None:
    work = {
        "id": "work",
        "repo": "o/r",
        "persona": "engineer",
        "task": "Continue",
    }
    resume = {
        "branch": "feature/preserved",
        "base_branch": "main",
        "pr_base": "main",
        "checkpoint": "abcdef1",
        "completed_steps": [],
        "mode": "retry",
    }
    result = {
        "round": 3,
        "results": {
            "work": {
                "status": "failed",
                "outcome": "not-completed",
                "resume": resume,
            }
        },
    }

    carried = next_round(_plan(work), result)

    # The continuation is the harness's own, so it is counted against the budget
    # that stops a node being redispatched at the same branch forever.
    assert carried["tasks"][0]["resume"] == {**resume, "source_round": 3, "attempts": 1}


def _preserved_failure(resume: dict[str, object], *, round_number: int = 3) -> dict[str, object]:
    return {
        "round": round_number,
        "results": {"work": {"status": "failed", "outcome": "not-completed", "resume": resume}},
    }


#: The continuation a failing lifecycle node preserves. The lifecycle rebuilds one
#: of these every round from whatever it just preserved, so it never carries a tally
#: of how many rounds preceded it — which is why the budget is counted on the plan.
_PRESERVED_RESUME: dict[str, object] = {
    "branch": "feature/preserved",
    "base_branch": "main",
    "pr_base": "main",
    "checkpoint": "abcdef1",
    "completed_steps": [],
    "mode": "retry",
}


def test_automatic_continuation_settles_a_node_once_its_budget_is_spent() -> None:
    """A preserved branch is continued a bounded number of times, then left alone.

    Unbounded, this is the loop that redispatched one node every round on the same
    branch, handing it another `(incomplete step)` marker commit each time. Rounds
    are chained the way the real ledger chains them — each plan derived from the last
    — because the tally lives on the plan and a result-only chain would never bound.
    """
    work = {"id": "work", "repo": "o/r", "persona": "engineer", "task": "Continue"}
    plan = _plan(work)

    for attempt in range(1, MAX_AUTOMATIC_ROUND_RESUMES + 1):
        plan = next_round(plan, _preserved_failure(_PRESERVED_RESUME))
        assert plan["tasks"][0]["resume"]["attempts"] == attempt

    assert next_round(plan, _preserved_failure(_PRESERVED_RESUME))["tasks"] == []


@pytest.mark.parametrize("tally", [-1, 1.5, "2", True, None])
def test_a_malformed_carried_continuation_tally_is_refused(tally: object) -> None:
    """A tally that is not a whole count is bad input, never a budget to start over.

    Coercing it to zero is the failure mode with teeth: a plan carried across rounds
    or hand-edited would silently regain a full continuation budget every round, which
    is the unbounded redispatch of one preserved branch the budget exists to stop.
    """
    work = {"id": "work", "repo": "o/r", "persona": "engineer", "task": "Continue"}
    carried = _plan({**work, "resume": {**_PRESERVED_RESUME, "attempts": tally}})

    with pytest.raises(PlanError, match="resume 'attempts' must be a non-negative integer"):
        next_round(carried, _preserved_failure(_PRESERVED_RESUME))


def test_an_explicit_retry_restores_the_full_continuation_budget() -> None:
    """The bound stops the harness repeating itself, never a planner decision."""
    work = {"id": "work", "repo": "o/r", "persona": "engineer", "task": "Continue"}
    exhausted = _plan({**work, "resume": {**_PRESERVED_RESUME, "attempts": 2}})

    assert next_round(exhausted, _preserved_failure(_PRESERVED_RESUME))["tasks"] == []

    retried = next_round(
        exhausted, _preserved_failure(_PRESERVED_RESUME), {"retry": {"work": {"max_turns": 40}}}
    )

    assert [task["id"] for task in retried["tasks"]] == ["work"]
    assert "attempts" not in retried["tasks"][0]["resume"]
    assert retried["tasks"][0]["max_turns"] == 40


def test_a_waiting_human_workstream_is_never_bounded_by_the_retry_budget() -> None:
    """A human gate is not a failed attempt; waiting rounds must not spend budget."""
    work = {
        "id": "work",
        "repo": "o/r",
        "persona": "engineer",
        "task": "Continue",
        "steps": [{"id": "gate", "kind": "human", "task": "approve"}],
    }
    resume = {
        "branch": "feature/paused",
        "base_branch": "main",
        "pr_base": "main",
        "checkpoint": "abcdef1",
        "completed_steps": [],
    }
    waiting = {
        "round": 2,
        "results": {"work": {"status": "waiting", "resume": resume, "waiting_steps": ["gate"]}},
    }
    plan = _plan(work)

    for _ in range(MAX_AUTOMATIC_ROUND_RESUMES + 2):
        plan = next_round(plan, waiting)
        assert [task["id"] for task in plan["tasks"]] == ["work"]
        assert "attempts" not in plan["tasks"][0]["resume"]


def test_explicit_branch_overrides_inferred_preserved_resume() -> None:
    work = {
        "id": "work",
        "repo": "o/r",
        "persona": "engineer",
        "task": "Restart",
        "branch": "feature/fresh-start",
    }
    result = {
        "round": 3,
        "results": {
            "work": {
                "status": "failed",
                "outcome": "not-completed",
                "resume": {
                    "branch": "feature/preserved",
                    "base_branch": "main",
                    "pr_base": "main",
                    "checkpoint": "abcdef1",
                    "completed_steps": [],
                    "mode": "retry",
                },
            }
        },
    }

    carried = next_round(_plan(work), result)

    assert carried["tasks"][0]["branch"] == "feature/fresh-start"
    assert "resume" not in carried["tasks"][0]


def test_lifecycle_resume_ignores_unaddressable_result_entries() -> None:
    missing_id: dict = {}
    missing_result = {"id": "work"}

    _apply_lifecycle_resume(missing_id, {}, set())
    _apply_lifecycle_resume(missing_result, {"work": "invalid"}, set())

    assert missing_id == {}
    assert missing_result == {"id": "work"}


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


def test_infrastructure_failure_is_terminal_across_rounds() -> None:
    result = _result(a="failed")
    result["results"]["a"]["outcome"] = "infrastructure-failure"

    assert next_round(_plan(A), result)["tasks"] == []


@pytest.mark.parametrize("value", [True, False, None])
def test_verify_via_ci_optional_field_round_trips_only_when_present(value) -> None:
    task = deepcopy(A)
    if value is not None:
        task["verify_via_ci"] = value
    plan = {"schema_version": 3, "tasks": [task]}

    carried = next_round(plan, _result(a="failed"))

    assert carried["schema_version"] == 3
    if value is None:
        assert "verify_via_ci" not in carried["tasks"][0]
    else:
        assert carried["tasks"][0]["verify_via_ci"] is value


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


def test_planner_context_crosses_the_transition_beside_the_branch_pin() -> None:
    """The knowledge the round produced survives with the state that already did."""
    work = {
        "id": "work",
        "repo": "o/r",
        "persona": "engineer",
        "task": "## What\nFinish the sweep",
        "branch": "feature/preserved",
    }
    attached = {"work": ["41 commits are on the branch", "one llmlint finding is open"]}

    carried = next_round(
        _plan(work),
        _preserved_failure(_PRESERVED_RESUME),
        carried_context=attached,
    )

    node = carried["tasks"][0]
    assert node["context"] == attached["work"]
    # The pin still routes the continuation, and the task prose is untouched: the
    # notes are data, rendered into the dispatched task where the node is parsed.
    assert node["branch"] == "feature/preserved"
    assert node["task"] == work["task"]


def test_carried_context_is_replaced_each_round_rather_than_accumulated() -> None:
    """A note travels exactly one transition, so stale state cannot pile up."""
    work = {"id": "work", "persona": "engineer", "task": "Work"}

    first = next_round(_plan(work), _result(work="failed"), carried_context={"work": ["round one"]})
    assert first["tasks"][0]["context"] == ["round one"]

    second = next_round(first, _result(work="failed"), carried_context={"work": ["round two"]})
    assert second["tasks"][0]["context"] == ["round two"]

    quiet = next_round(second, _result(work="failed"))
    assert "context" not in quiet["tasks"][0]


def test_context_for_a_node_that_leaves_the_round_reaches_nothing() -> None:
    """Notes follow a node id; a dropped or replaced id takes its notes with it."""
    dropped = next_round(
        _plan(A, B),
        _result(a="failed", b="failed"),
        {"drop": ["a"]},
        carried_context={"a": ["only about a"], "b": ["about b"]},
    )

    assert [task["id"] for task in dropped["tasks"]] == ["b"]
    assert dropped["tasks"][0]["context"] == ["about b"]

    split = next_round(
        _plan(A),
        _result(a="failed"),
        {"split": {"a": [{"id": "a1", "repo": "o/r", "persona": "engineer", "task": "A1"}]}},
        carried_context={"a": ["only about a"]},
    )
    assert [task["id"] for task in split["tasks"]] == ["a1"]
    assert "context" not in split["tasks"][0]


def test_a_retry_that_states_context_overrules_what_the_round_attached() -> None:
    """A decision made after reading the result beats collection, empty included."""
    work = {"id": "work", "persona": "engineer", "task": "Work", "context": ["stale"]}

    stated = next_round(
        _plan(work),
        _result(work="failed"),
        {"retry": {"work": {"context": ["the planner's own brief"]}}},
        carried_context={"work": ["collected"]},
    )
    assert stated["tasks"][0]["context"] == ["the planner's own brief"]

    cleared = next_round(
        _plan(work),
        _result(work="failed"),
        {"retry": {"work": {"context": []}}},
        carried_context={"work": ["collected"]},
    )
    assert cleared["tasks"][0]["context"] == []


def test_round_context_collects_only_the_notes_that_round_committed(tmp_path) -> None:
    """Read from the round's committed edits, which is what bounds accumulation."""
    from orchestrator.journal import NodeId, RunId, open_journal
    from orchestrator.replan import round_context

    run_dir = tmp_path / "run"
    first = open_journal(run_dir, RunId("run"), 1)
    first.append(
        "edit-committed",
        detail={
            "command": {"op": "context", "id": "work", "note": "from round one"},
            "operations": [
                {"kind": "context-added", "node": "work", "detail": {"note": "from round one"}}
            ],
        },
    )
    second = open_journal(run_dir, RunId("run"), 2)
    second.append(
        "edit-committed",
        detail={
            "operations": [
                {"kind": "context-added", "node": "work", "detail": {"note": "from round two"}},
                {"kind": "context-added", "node": "other", "detail": {"note": "for other"}},
            ]
        },
    )
    # Neither a differently shaped edit nor an unrelated event contributes a note.
    second.append(
        "edit-committed",
        detail={"operations": [{"kind": "completion-requested", "detail": {"reason": "done"}}]},
    )
    second.append("node-started", node=NodeId("work"), detail={"node_kind": "direct"})

    assert round_context(run_dir, 1) == {"work": ["from round one"]}
    assert round_context(run_dir, 2) == {"work": ["from round two"], "other": ["for other"]}
    assert round_context(run_dir, 3) == {}
    assert round_context(tmp_path / "absent", 1) == {}
