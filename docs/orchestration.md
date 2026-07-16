# Tracked graph orchestration

`just run-plan` turns one large task into one recorded hierarchical DAG. It is
the canonical executor for direct onejudge work, full repository lifecycles, and
explicit actions that only a person can complete. `just repo-plan` is a deprecated
alias retained so old lifecycle-only plan files keep working.

## Node shapes

Every top-level node needs a unique `id`; `deps` is an optional list of other
top-level ids. Omitted `kind` defaults to `agent` for compatibility.

| Shape | Required fields | Meaning |
| --- | --- | --- |
| Direct agent | `persona`, `task`; no `repo` | Dispatch one real onejudge process in the selected project directory. |
| Lifecycle agent | `repo`, plus `persona` + `task` or `steps` | Work on an isolated branch/worktree, verify, and publish through the repository's registered policy. |
| Human | `kind: human`, `task`; no persona or execution fields | Record action prose for a person. The harness never performs or infers it. |

A lifecycle `steps` list is its own DAG. Agent steps require `persona` and `task`.
Human steps require `kind: human` and `task`, and are referenced outside the node
as `NODE_ID/STEP_ID`. Steps share one branch and run serially in topological order
because concurrent writers cannot safely share a worktree. See
[`tracked-graph.example.json`](../examples/tracked-graph.example.json) for direct,
lifecycle, top-level human, and nested human nodes in one graph.
The `/` separator is reserved: top-level human ids and human step ids cannot
contain it. Existing lifecycle node ids may contain `/`; nested completion strips
that node's exact prefix rather than assuming the first slash separates the step.
Resume metadata is accepted only on a workstream containing a human step. Its
explicit branch/base must agree with the node, completed steps must be unique and
dependency-closed, and a GitHub PR URL must name the lifecycle repository.

## Decomposition and scheduling

A fresh onejudge process pays a fixed setup cost to read and understand its
project. Split work when independent pieces can run concurrently, when different
personas materially improve it, or when one risky piece deserves a focused review
bar. Keep tightly coupled or tiny work together. Dependencies should name only
real inputs so unrelated branches remain parallel.

`run-plan` starts every node whose dependencies are `done`, bounded by
`concurrency`. Lifecycle dependencies on the same repository identity also carry
publication/stack ancestry; cross-repository dependencies only schedule. A graph
is static within one round. Adapt after reading its recorded result.

## Status, state, and exit contract

Each node settles once per round:

- `done`: the agent completed or the lifecycle published successfully.
- `waiting`: a ready human node or lifecycle human step needs action. Its
  `human_actions` entry includes the exact `task`, direct `unblocks`, and whether
  it unblocks workstream publication.
- `blocked`: execution is transitively gated by a waiting human. `blocked_by`
  contains the ready top-level or `NODE_ID/STEP_ID` human references.
- `failed`: an executed agent or lifecycle failed.
- `skipped`: a failed dependency made execution unsafe. Failure takes precedence
  over a simultaneous waiting path, so such a descendant is skipped, not blocked.

The result's top-level `state` is `failed` if any node failed or skipped,
otherwise `waiting` if any node waits or is blocked, otherwise `complete`. `ok` is
true only for `complete`. Human and JSON output carry the same facts. Exit status
is 0 for `complete`, 1 for `waiting` or `failed`, and 2 for invalid plan, ledger,
or command input.

## Recorded rounds

Recording is on by default:

```text
runs/<run-id>/round-01/plan.json
runs/<run-id>/round-01/status.json
runs/<run-id>/round-01/result.json
runs/<run-id>/humans.json
```

Without `--run`, the id is derived from the plan's `name` or filename and made
unique. `--runs-dir` moves the ledger, `--no-record` opts out, and `--recover`
claims a `running` round only after its recorded owner is proven gone. Plan and
result writes are atomic; a live round cannot be claimed by another process.
`just runs` summarizes the latest completed round, including waiting action prose
and what each action unblocks.

## Human completion attestations

After doing a reported action, attest it explicitly:

```sh
just next-round RUN --complete-human HUMAN_ID
just next-round RUN --complete-human NODE_ID/STEP_ID
```

The same operation can be supplied in an edits file as `"complete_human":
["HUMAN_ID"]`. Multiple CLI flags and file entries combine, but references must
be unique. Only a human action recorded as `waiting` in the latest completed
round can be attested. Unknown ids, blocked nodes/steps, agent ids, and previously
completed humans are invalid and exit 2.

The harness never guesses that a meeting, approval, deployment, or other human
action happened. Each accepted attestation is durably appended to `humans.json`
with its reference, the waiting round number, and a UTC timestamp. Replanning
removes a completed top-level human or adds a nested human to the lifecycle
resume's `completed_steps`; already-done agents are removed and never replayed.
If a top-level human is the final node, the attestation records a completed
continuation round so `just runs` no longer reports the finished run as waiting.

## Replanning

`just next-round RUN [edits.json]` reads the latest plan and result, writes the
next numbered plan, runs it, and records the result. `--plan-only` stops after
derivation. Edits may `retry` with overrides, `split`, `add`, `drop`, or
`complete_human`. Completed nodes fall out of the next plan, satisfied dependency
ids are removed, and unresolved lifecycle stack anchors/resume checkpoints are
preserved. An unresolved same-repository publication anchor passes through removed
human gates (and other non-publication nodes), so attestation cannot silently cut a
downstream lifecycle branch from the root. The derived graph is validated before
an attestation is recorded.

`just replan PREV_PLAN PREV_RESULT [edits.json]` exposes the lower-level pure
derivation command. Old direct plans, old lifecycle-only repo plans, and recorded
results without `state` remain readable.

## Where this lives

- `orchestrator/graph.py` — canonical mixed-node validation, scheduling, result,
  output, and exit semantics.
- `orchestrator/plan.py` — direct-agent parsing and the shared DAG scheduler.
- `orchestrator/lifecycle.py` — repository nodes and resumable step workstreams.
- `orchestrator/runs.py`, `next_round.py`, `replan.py` — durable rounds,
  attestations, and continuation.
- `orchestrator/dispatch.py` — one direct agent or lifecycle agent step → one
  onejudge subprocess.
