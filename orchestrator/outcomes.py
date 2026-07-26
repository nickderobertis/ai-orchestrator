"""Authoritative recorded outcome domain for graph and lifecycle execution."""

from typing import Literal

INFRASTRUCTURE_FAILURE_OUTCOME: Literal["infrastructure-failure"] = "infrastructure-failure"
ALREADY_INTEGRATED_OUTCOME: Literal["already-integrated"] = "already-integrated"
WAITING_HUMAN_OUTCOME: Literal["waiting-human"] = "waiting-human"

NodeOutcome = Literal["no-changes", "infrastructure-failure"]
NODE_OUTCOMES = frozenset[NodeOutcome]({"no-changes", INFRASTRUCTURE_FAILURE_OUTCOME})

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
LIFECYCLE_OUTCOMES = frozenset[LifecycleOutcome](
    {
        "merged",
        ALREADY_INTEGRATED_OUTCOME,
        "pr-open",
        "not-completed",
        "gate-failed",
        "no-changes",
        "checks-failed",
        "closed",
        "timeout",
        "error",
        WAITING_HUMAN_OUTCOME,
        "resume-failed",
        "stack-conflict",
        "sync-conflict",
        "publication-retries-exhausted",
        "merge-conflict-retry",
    }
)
SUCCESSFUL_LIFECYCLE_OUTCOMES = frozenset[LifecycleOutcome](
    {"merged", ALREADY_INTEGRATED_OUTCOME, "pr-open"}
)
