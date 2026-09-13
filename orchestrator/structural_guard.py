"""The one list of structural rules a node is held to, whichever tier asks.

A node is checked before launch by `just check-plan` and again whenever a reply envelope
results in it — an `add`, a `retry`'s replacement, a `requeue` with its overrides folded
in. The two tiers once applied different lists: the live-edit check held task prose to
the criteria bar and asked no structural question at all, so a `requeue` amending
`adoption: fast` onto a node publishing `local-direct` behind a releasing dependency was
accepted there and failed an hour of work at its last step, on a refusal
:mod:`orchestrator.adoption_guard` already carried the message for.

So the rules that read a node's **fields** — where it publishes, what it waits on, how it
adopts — are listed here once, and every tier reads this list rather than naming the
guards itself: :func:`orchestrator.criteria_guard.check_plan` and
:func:`orchestrator.plan_check.refusals` at the plan tier, and
:func:`orchestrator.live_edit_check.structural_refusal` at the live one. A rule added to
either guard reaches both tiers with nothing to follow, and a guard added here reaches
both too; `tests/test_structural_guard.py` fails when a module shaped like a guard is
read by one tier and not this list.

What is deliberately not here is :mod:`orchestrator.task_body`: it measures the issue body
the `plans` board would be handed, and a live edit never becomes an issue.
"""

from __future__ import annotations

from typing import Protocol

from orchestrator import adoption_guard, publication_guard
from orchestrator.publication_guard import Refusal


class Guard(Protocol):
    """What a structural rule module offers: every refusal, and the first one raised."""

    def refusals(self, plan: object) -> list[Refusal]:
        """Everything this guard refuses about ``plan``."""

    def check_plan(self, plan: object) -> None:
        """Raise this guard's own error for the first thing it refuses about ``plan``."""


#: The structural rules, in the order the plan tier has always asked them: where a node
#: lands, then whether the release it waits on could arrive there.
GUARDS: tuple[Guard, ...] = (publication_guard, adoption_guard)


def refusals(plan: object) -> list[Refusal]:
    """Every refusal every structural guard makes about ``plan``, guard by guard."""
    return [refused for guard in GUARDS for refused in guard.refusals(plan)]


def check_plan(plan: object) -> None:
    """Raise the first structural refusal about ``plan``, in each guard's own words."""
    for guard in GUARDS:
        guard.check_plan(plan)
