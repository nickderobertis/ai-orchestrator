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
