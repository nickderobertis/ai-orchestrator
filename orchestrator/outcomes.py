"""Authoritative recorded outcome domain for graph and lifecycle execution."""

from typing import Literal, get_args

INFRASTRUCTURE_FAILURE_OUTCOME: Literal["infrastructure-failure"] = "infrastructure-failure"
ALREADY_INTEGRATED_OUTCOME: Literal["already-integrated"] = "already-integrated"
WAITING_HUMAN_OUTCOME: Literal["waiting-human"] = "waiting-human"

NodeOutcome = Literal["no-changes", "infrastructure-failure"]
NODE_OUTCOMES: frozenset[NodeOutcome] = frozenset(get_args(NodeOutcome))

LifecycleOutcome = Literal[
    "merged",
    "already-integrated",
    "pr-open",
    "not-completed",
    "gate-failed",
    "no-changes",
    "checks-failed",
    "closed",
    "timeout",
    "error",
    "waiting-human",
    "resume-failed",
    "stack-conflict",
    "sync-conflict",
    "publication-retries-exhausted",
    "merge-conflict-retry",
]
LIFECYCLE_OUTCOMES: frozenset[LifecycleOutcome] = frozenset(get_args(LifecycleOutcome))
SUCCESSFUL_LIFECYCLE_OUTCOMES = frozenset[LifecycleOutcome](
    {"merged", ALREADY_INTEGRATED_OUTCOME, "pr-open"}
)

#: Node statuses that mean the work is gone rather than held. As a *dependency*
#: status the dependent can never run and settles `skipped`, not `blocked`; as a
#: *node* status anywhere in a round it settles the round `failed`. The name is
#: scoped to the statuses rather than to dependencies for that reason — it answers
#: both questions, exactly as `HELD_STATUSES` does for the other side of the split.
LOST_STATUSES = ("failed", "skipped")
#: Node statuses that mean work is held rather than lost. As a *dependency* status
#: this settles the dependent `blocked`; as a *node* status anywhere in a round it
#: settles the round `waiting`. Those are one rule — a round is still waiting for
#: exactly the reasons a dependent is still blocked — and two copies of it would let
#: an in-process round state and the same round's state recomputed from its recorded
#: payload drift apart. `parked` is one of them because a planner `cancel` idles a
#: node it may still `requeue`; treating it as unmet would settle every dependent
#: `skipped` and throw away the work the park exists to keep recoverable.
HELD_STATUSES = ("waiting", "blocked", "parked")
#: The order every view renders per-status counts in, most-settled first. One list,
#: because a reader comparing `just runs`, `just monitor`, and a round summary is
#: comparing the same run — and a status missing from one of them reads as a node
#: that is not there rather than as a view that never learned the word.
STATUS_DISPLAY_ORDER = ("done", "waiting", "blocked", "parked", "failed", "skipped")
