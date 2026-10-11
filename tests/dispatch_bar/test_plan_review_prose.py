"""The half of `tests/test_plan_review.py` that reads this repository's prose.

It sits in the `dispatch-bar` project.

These hold the plan review's prompt to the bar, the schema and the templates it is rendered
from. That project's key names each file they read, and `tests/conftest.py` holds them to
it; the tests of that module that read no prose stay there, in `orchestrator:test`.
"""

from __future__ import annotations

import json

import budget_telemetry
from test_plan_review import REALISTIC, _node_costs, _prompt_of, baseline_shaped, host_shaped

from orchestrator import plan_review
from orchestrator.root import REPO_ROOT


def test_the_plan_level_prompt_is_the_compact_view_of_a_realistic_plan() -> None:
    """Contract 2 over the baseline-shaped plan: every node alike, nothing past a summary."""
    realistic = baseline_shaped()
    prompt = _prompt_of(realistic)
    appendix = (REPO_ROOT / plan_review.APPENDIX).read_text(encoding="utf-8").strip()
    tasks = realistic.plan["tasks"]
    assert isinstance(tasks, list) and len(tasks) == 44
    assert sum(1 for node in tasks if node.get("kind") == "human") == 8
    assert sum(1 for node in tasks if node["repo"] == plan_review.host_repository()) == 1
    budgets = [budget for held in realistic.owned.values() for budget in held]
    assert len(budgets) == 52

    for node in tasks:
        node_id = node["id"]
        section = prompt.split(f"### Node `{node_id}`", 1)[1].split("\n\n### Node `", 1)[0]
        for field in plan_review.SHOWN_FIELDS:
            assert f'"{field}":' in section, (node_id, field)
        stored = realistic.view.records[node_id]
        assert isinstance(stored, dict) and stored["key"] in section, node_id
        assert f"task file: {realistic.view.tasks[node_id]}" in section, node_id
        summary = plan_review.summary(node["task"])
        assert f"summary: {summary}" in section, node_id
        assert len(summary.removesuffix(plan_review.SUMMARY_MARKER)) <= plan_review.SUMMARY_LIMIT
        for sentinel in ("LATER", "CRITERION", "NOTES"):
            assert f"SENTINEL-{sentinel}-{node_id}" not in prompt, (node_id, sentinel)
    # A lead past the bound is cut verbatim, and says so.
    assert "repository-03-baseline" in prompt
    assert plan_review.SUMMARY_MARKER in prompt
    # The operational notes are named once, never rendered.
    assert appendix.splitlines()[4] not in prompt, "a line of the notes' body was rendered"
    assert prompt.count(str(REPO_ROOT / plan_review.APPENDIX)) == 1
    # Each budget's long prose is a marked excerpt; every other answer is whole.
    for budget in budgets:
        for field in plan_review.EXCERPTED_BUDGET_FIELDS:
            value = budget[field]
            if len(value) > plan_review.EXCERPT_LIMIT:
                assert value not in prompt, (budget["id"], field)
                excerpt = value[: plan_review.EXCERPT_LIMIT] + plan_review.EXCERPT_MARKER
                assert json.dumps(excerpt, ensure_ascii=False) in prompt, (budget["id"], field)
            else:
                assert json.dumps(value, ensure_ascii=False) in prompt, (budget["id"], field)
        for field in ("id", "name", "basis", "repository", "file", "unit", "command"):
            assert json.dumps(budget[field], ensure_ascii=False) in prompt
    # And the plan's own: its three longest prose answers excerpted, every other whole.
    for answer in plan_review.EXCERPTED_PLAN_FIELDS:
        value = realistic.answers[answer]
        assert isinstance(value, str) and value not in prompt, answer
    for answer in ("sizing", "ten_x_summary", "checklist", "repo_wide_effects", "spike_findings"):
        assert (
            json.dumps(realistic.answers[answer], ensure_ascii=False, separators=(",", ":"))
            in prompt
        ), answer


def test_plan_level_prompt_size_is_recorded_for_both_realistic_plans() -> None:
    """Budget `plan-level-prompt-chars`: each realistic plan's prompt length is recorded.

    And the property that makes the budget hold for any plan this size: growing every
    task body of the host-shaped plan fourfold leaves its prompt exactly as long.
    """
    sizes = {name: len(_prompt_of(build())) for name, build in REALISTIC.items()}
    grown = len(_prompt_of(host_shaped(grown=4)))
    tasks, ordinary = host_shaped(grown=4).plan["tasks"], host_shaped().plan["tasks"]
    assert isinstance(tasks, list) and isinstance(ordinary, list)
    print(f"plan-level prompt characters: {sizes}; host-shaped grown fourfold: {grown}")
    unchanged = [
        node["id"]
        for node, before in zip(tasks, ordinary, strict=True)
        if len(node["task"]) <= len(before["task"])
    ]
    assert unchanged == [], f"these bodies did not grow: {unchanged}"
    assert grown == sizes["host-shaped"], f"a longer body changed the prompt: {grown} vs {sizes}"
    for name, size in sizes.items():
        budget_telemetry.record(
            "plan-level-prompt-chars", f"{name} fixture", size, unit="characters", combine="max"
        )


def test_plan_level_prompt_per_node_is_recorded_at_each_plans_costliest_node() -> None:
    """Budget `plan-level-prompt-chars-per-node`: each plan's costliest node is recorded.

    Including a node whose `## What` lead is past the summary's bound and a stepped node
    of three steps, which are the two shapes that cost the most.
    """
    costs = {name: _node_costs(_prompt_of(build())) for name, build in REALISTIC.items()}
    assert sum(map(len, costs.values())) == 88, costs
    for name, heavy in (("baseline-shaped", "repository-03-baseline"), ("host-shaped", "adopt-07")):
        assert costs[name][heavy] > plan_review.SUMMARY_LIMIT, heavy
    stepped = host_shaped().plan["tasks"]
    assert isinstance(stepped, list) and len(stepped[40]["steps"]) == 3
    assert '"id":"step-2"' in _prompt_of(host_shaped())
    for name, nodes in costs.items():
        costliest = max(nodes, key=nodes.__getitem__)
        print(f"{name}: costliest node {costliest} at {nodes[costliest]} characters")
        budget_telemetry.record(
            "plan-level-prompt-chars-per-node",
            f"{name} fixture, node `{costliest}`",
            nodes[costliest],
            unit="characters",
            combine="max",
        )


def test_the_reviewer_reads_a_task_file_only_up_to_the_notes_every_agent_task_shares() -> None:
    """The heading the prompt stops a task-file read at is the appendix's own."""
    appendix = (REPO_ROOT / plan_review.APPENDIX).read_text(encoding="utf-8")
    assert f"\n{plan_review.NOTES_HEADING}\n" in appendix
    composed = " ".join(plan_review._plan_prompt({"tasks": [{"id": "bare"}]}).split())
    assert f"read it only as far as its `{plan_review.NOTES_HEADING}` heading" in composed
    asked = " ".join(plan_review.PLAN_REVIEW_PROMPT.split())
    assert "a node's task file, up to the operational notes it ends with" in asked
