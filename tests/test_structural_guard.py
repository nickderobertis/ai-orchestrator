"""Both tiers that guard a node read one list of structural rules, and cannot drift apart.

`just check-plan` holds a node to `orchestrator/publication_guard.py` and
`orchestrator/adoption_guard.py` before launch; the live-edit check holds a node a reply
results in to the same rules. They once applied different lists, and a `requeue` the live
tier accepted failed an hour of work on a refusal the plan tier already had the message
for. So both read `orchestrator/structural_guard.py`'s `GUARDS`, and this module fails the
day either stops: a rule nobody has written yet is put on that list, and every tier must
refuse with it — and a module shaped like a guard that is not on the list must be named
here as belonging to the plan tier alone, with the reason.
"""

from __future__ import annotations

import ast

import pytest

from orchestrator import criteria_guard, envelope_review, plan_check, structural_guard, task_body
from orchestrator.plan_store import NodeId
from orchestrator.publication_guard import Refusal
from orchestrator.root import REPO_ROOT

#: What the invented rule says, so each tier's answer can be told to be its answer.
INVENTED_REASON = "an invented structural rule refuses every node it is shown"

#: The one guard-shaped module the plan tier reads that the live tier does not, and why:
#: it measures the issue body the `plans` board is handed, and a live edit never becomes
#: an issue.
PLAN_ONLY = {task_body.__name__.rpartition(".")[2]}


class InventedError(ValueError):
    """What the invented rule raises on the path that reports one refusal at a time."""


class Invented:
    """A structural rule added to the list, refusing every node a plan names."""

    def refusals(self, plan: object) -> list[Refusal]:
        tasks = plan.get("tasks") if isinstance(plan, dict) else None
        return [
            Refusal(node=NodeId(task["id"]), field="adoption", reason=INVENTED_REASON)
            for task in (tasks if isinstance(tasks, list) else [])
            if isinstance(task, dict) and isinstance(task.get("id"), str)
        ]

    def check_plan(self, plan: object) -> None:
        for refused in self.refusals(plan):
            raise InventedError(f"{refused.node}: {refused.reason}")


def test_a_rule_put_on_the_list_is_applied_by_every_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """The plan tier's two paths and the envelope validator all refuse with the new rule.

    A tier that named its guards itself rather than reading the list would pass this plan
    and this envelope, because neither real guard has anything to say about a human node
    naming no repository.
    """
    monkeypatch.setattr(structural_guard, "GUARDS", (Invented(),))
    gate = {"id": "gate", "kind": "human", "task": "Approve."}
    document = {"schema_version": 3, "name": "probe", "tasks": [gate]}

    through_the_verb = plan_check.refusals(document, plan_check.UNNAMED_PROJECT)
    assert {"node": "gate", "field": "adoption", "reason": INVENTED_REASON} in through_the_verb

    with pytest.raises(InventedError, match=INVENTED_REASON):
        criteria_guard.check_plan(document)

    envelope = {"version": 2, "commands": [{"op": "add", "node": {**gate, "id": "added"}}]}
    refused = envelope_review.structural_refusal(envelope)
    assert refused == f"node 'added', as this reply states it: adoption: {INVENTED_REASON}"


def test_the_list_is_exactly_the_guard_shaped_modules_the_plan_tier_does_not_keep_to_itself() -> (
    None
):
    """A new guard module is on the list, or named in :data:`PLAN_ONLY` with its reason.

    A guard is a module of `orchestrator/` offering `refusals(plan)` — the shape both real
    guards and the board's body check have. Adding one and wiring it into the plan tier
    alone is the regression this exists against, and it fails here until the author has
    decided which tier it belongs to.
    """
    shaped = set()
    for source in sorted((REPO_ROOT / "orchestrator").glob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for statement in tree.body:
            if (
                isinstance(statement, ast.FunctionDef)
                and statement.name == "refusals"
                and [argument.arg for argument in statement.args.args] == ["plan"]
            ):
                shaped.add(source.stem)
    listed = {
        getattr(guard, "__name__", "").rpartition(".")[2] for guard in structural_guard.GUARDS
    }
    aggregator = structural_guard.__name__.rpartition(".")[2]

    assert shaped - {aggregator} == listed | PLAN_ONLY
    assert listed.isdisjoint(PLAN_ONLY)
