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

#: The outcomes that settle a workstream leaving *nothing* on a branch for a later
#: round to continue, whatever the agent did. Every other outcome is eligible to leave
#: continuable work, and the round fold carries a preserved branch only when the result
#: recorded `Resume` metadata for it — so an outcome missing from that recording
#: silently discards a finished branch and the next round re-derives it. Stated as the
#: exceptions rather than as the eligible set, so an outcome added to `LifecycleOutcome`
#: is eligible until somebody decides otherwise: the failure that costs work is the one
#: nobody classified.
PRESERVATION_INELIGIBLE_OUTCOMES: frozenset[LifecycleOutcome] = SUCCESSFUL_LIFECYCLE_OUTCOMES | {
    # The agent produced no commit, so there is no branch content to hand on.
    "no-changes",
    # Held rather than lost, and the pause records a continuation of its own.
    WAITING_HUMAN_OUTCOME,
    # The recorded pin is the thing that failed; re-pinning the branch it named would
    # repeat the identical refusal every round instead of reaching a planner.
    "resume-failed",
    # An internal signal inside the publication loop, never a settled workstream.
    "merge-conflict-retry",
}
#: Outcomes whose settlement *may* leave committed work on a branch — eligibility, not
#: a promise. `error`, `timeout`, and `not-completed` each settle before the agent
#: commits anything often enough that membership here says only "ask the branch":
#: `lifecycle` records a `Resume` for the members that turn out to have commits ahead
#: of their base, and for no others. The complement of the ineligible set above, so the
#: two partition `LifecycleOutcome`.
PRESERVATION_ELIGIBLE_OUTCOMES: frozenset[LifecycleOutcome] = (
    LIFECYCLE_OUTCOMES - PRESERVATION_INELIGIBLE_OUTCOMES
)
#: The preservation-eligible outcomes whose branch *content* the merge path refused. A
#: continuation of one has to re-dispatch its steps: skipping them as completed would
#: republish the identical tree the gate — or the required checks — just rejected.
REJECTED_CONTENT_OUTCOMES: frozenset[LifecycleOutcome] = frozenset({"gate-failed", "checks-failed"})

#: Node statuses that mean the work is gone rather than held. As a *dependency*
#: status the dependent can never run and settles `skipped`, not `blocked`; as a
#: *node* status anywhere in a round it settles the round `failed`. The name is
#: scoped to the statuses rather than to dependencies for that reason — it answers
#: both questions, exactly as `HELD_STATUSES` does for the other side of the split.
LOST_STATUSES = ("failed", "skipped")
#: Node statuses that fail the round holding them. `cancelled` joins the lost ones
#: here and only here: a cancelled node has left the graph, so it is never a
#: *dependency* status, but the round that cancelled it did not complete. One
#: constant for the same reason `HELD_STATUSES` is one — the in-process
#: `GraphResult.state` and the state `runs.result_state` derives from a recorded
#: payload have to answer identically, and a payload written before `state` was
#: recorded is read by the second alone.
FAILED_ROUND_STATUSES = (*LOST_STATUSES, "cancelled")
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
