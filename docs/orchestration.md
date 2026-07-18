<!-- llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] Operator-facing wire examples are required here; orchestrator/channel.py remains authoritative and its exact contract is exercised by channel unit/e2e tests. -->

# Tracked graph orchestration

`just run-plan` turns one large task into one recorded hierarchical DAG. It is
the canonical executor for direct onejudge work, full repository lifecycles, and
explicit actions that only a person can complete. `just repo-plan` is a deprecated
alias retained so old lifecycle-only plan files keep working.

The current tracked-plan contract is schema version 3 (`"schema_version": 3`).
Plans that omit the version retain version-1 behavior for compatibility.

## The planner<->orchestrator channel

`just orchestrate <plan.json>` starts a detached orchestrator onejudge run and
prints its run id. The orchestrator is the graph executor; its supervisor is the
live planner rather than a simulated-user model. One orchestrator turn owns one
recorded graph round: it runs `just run-plan` (or `just next-round` after the first
round), watches `just monitor`, reads the settled result, and asks the planner for
a decision before writing the next round. The planner must not run either writer.
After launch it uses only the channel and the read-only `just monitor` / `just
runs` views; otherwise two processes can race the ledger lock.

At each round boundary the orchestrator emits JSON in its final assistant message:

```json
{"kind":"blocker","message":"Node X failed its gate; retry with a corrected fixture?","options":["retry X","drop X"]}
```

`orchestrator.channel.relay_supervisor` validates that emission and sends this
newline-delimited JSON frame to the planner:

```json
{"op":"supervisor","run_id":"RUN","round":1,"surface":{"kind":"blocker","message":"Node X failed its gate; retry with a corrected fixture?","options":["retry X","drop X"]},"messages":[{"role":"assistant","content":"..."}]}
```

The orchestrator persona defines the boundary-kind vocabulary. `surface.options`
is optional and, when present, is a list of strings. `messages` is the onejudge
conversation context. The planner replies with one of these shapes:

```json
{"completion":false,"message":"retry X with the fixture requirement","reason":"the graph is not complete"}
{"completion":true,"reason":"publication and follow-up triage verified"}
```

`completion: false` requires both `message` and `reason`; `completion: true`
requires `reason`. The orchestrator applies continuing guidance through
`next-round` edits. Completion is reserved for a verified `closeout` after the
whole graph is published and follow-ups are triaged.

The three planner-facing recipes are:

```sh
just orchestrate plan.json --runs-dir /host/path/runs
just channel-next RUN --runs-dir /host/path/runs
just channel-reply RUN reply.json --runs-dir /host/path/runs
```

`channel-next` waits for one surface. A bounded wait with no message returns
`{"status":"running","surface":null}`; a settled run returns
`{"status":"finished"}`. `channel-reply` accepts a reply file or reads JSON from
stdin when its file argument is omitted. Both sides may exit and reattach between
messages: transport state lives under `runs/<run-id>/channel/` as `up.fifo`,
`down.fifo`, `channel.json`, and the last `planner-verdict.json`.

Only the orchestrator crosses round boundaries. Worker onejudge processes remain
bounded to the graph round that dispatched them and never use the planner channel.
The orchestrator may author in an isolated execution checkout, but its
`--runs-dir` must resolve to the same host-visible path for the detached process
and planner. The round ledger and its sibling `channel/` directory cannot live
only inside a disposable worktree or container-private filesystem.

## Node shapes

Every top-level node needs a unique `id`; `deps` is an optional list of other
top-level ids. Omitted `kind` defaults to `agent` for compatibility.

| Shape | Required fields | Meaning |
| --- | --- | --- |
| Direct agent | `persona`, `task`; no `repo` | Dispatch one real onejudge process in the selected project directory. |
| Lifecycle agent | `repo`, plus `persona` + `task` or `steps` | Work on an isolated branch/worktree, verify, and publish through the repository's registered policy. |
| Human | `kind: human`, `task`; no persona or execution fields | Record action prose for a person. The harness never performs or infers it. |

An agent node or lifecycle agent step may instead set `expects_no_diff: true`
with `task` and no `persona` or `done_when`. This explicitly declares that the
task expects no repository change and no separate review evidence. It settles as
`done` with the existing `no-changes` outcome without dispatching onejudge. The
executor does not infer this from task prose. Combining the declaration with
`persona` or `done_when` is rejected while loading the plan, before any provider
time is spent. Omitting `expects_no_diff` preserves normal dispatch behavior.

A lifecycle `steps` list is its own DAG. Agent steps require `persona` and `task`.
Persona names may be top-level general roles (`engineer`) or slash-qualified
repo-specific roles (`crozier/crozier-corpus`).
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
project. It is also a highly capable coding agent, pair-programmed with and reviewed
by a simulated-user supervisor that pushes back until the task is actually done.
Bias toward fewer, larger coherent tasks that amortize setup. Split only for
genuine parallelism, a real dependency, or a genuinely different role or review
bar — not simply to give a capable agent a smaller slice. Put subtask-specific
requirements in detailed `task` prose and explicit per-node `done_when` acceptance
criteria, using `max_turns` when a task needs more room, rather than proliferating
personas. Every dispatch already has the built-in supervisor/reviewer; reserve a
dedicated `reviewer` step for complex DAGs where it reviews and integrates several
agents' independently produced work. Dependencies should name only real inputs so
unrelated branches remain parallel.

`run-plan` starts every node whose dependencies are `done`, bounded by
`concurrency`. Lifecycle dependencies on the same repository identity also carry
publication/stack ancestry; cross-repository dependencies only schedule. A graph
is static within one round. Adapt after reading its recorded result.

## Status, state, and exit contract

Each node settles once per round:

- `done`: the agent completed or the lifecycle published successfully.
- `done` with outcome `no-changes`: an explicit `expects_no_diff` node settled
  deterministically without an agent dispatch.
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

## Monitoring a live run

`just runs` says where a round *ended* and `just history-show` says everything
about one thing in it. `just monitor` answers the question in between — "what is
happening right now, across the whole run?" It is the standard first view for
every recorded in-flight dispatch: both `run-plan` graphs and single
`repo-task`/`repo-task-auto` lifecycle nodes (the auto wrapper records a
monitorable run). It folds four stores that settle
at different times into one ordered stream:

| Source | Read from | Reported when |
| --- | --- | --- |
| Run journal | `runs/<run-id>/events.jsonl` | every node transition, as it is appended |
| oneharness history | sessions whose `run_id` label names this run | a session's status or turn count moves |
| Git | commits on each known lifecycle branch | once per commit, ever |
| GitHub | each lifecycle-linked PR | its state or any check changes |

PR check events identify the check, state, and whether it is required, and emit
each transition. Do not replace this aggregate view with an ad hoc `gh pr checks`
poller or bespoke lifecycle watch script. Start with `just monitor`; a targeted
`gh` query remains appropriate for a one-off detail absent from its stream.

```sh
just monitor                      # newest active run, follow until it completes
just monitor RUN_ID               # one named run
just monitor --once               # replay what is known, report state, exit 0
just monitor --format jsonl       # one JSON record per line, no header
just monitor --heartbeat 30 --poll-interval 5
```

Unchanged polls back off exponentially to a configurable bounded interval.
`--max-poll-interval` changes that bound. New observations reset the
initial interval. Silence heartbeats remain independent of polling frequency.

Without `RUN_ID` it picks the **newest active** run — anything that has not
completed successfully, including one merely waiting on a human, since that is the
most important state to be watching. If nothing is active it follows the newest
run, which replays and exits. `--runs-dir` moves the ledger as everywhere else.

**Exit contract.** Only a graph that *completed successfully* ends the stream
(exit 0). Waiting on a human, a failed node, and an executor that died all keep
heartbeating, because each is a state a person acts on and the run then continues
— through `next-round`, whose new round directory the next poll picks up. A
monitor that exited on them would report "finished" for a run that is merely
stuck. `--once` is the escape hatch and always exits 0 after one pass; only follow
mode encodes completion in its status. Exit 2 is an unresolvable run or bad input.

**Output shape.** The text stream's first line is exactly:

```text
Concise graph events; run just history-show <stream-id> for full detail.
```

That is the contract, not a banner. Every event line carries exactly one strict
typed id (`graph:`/`oh:`/`git:`/`pr:` — see `orchestrator/ids.py`), which is
precisely the argument `just history-show` resolves, so each summary can stay one
control-stripped line capped at 96 characters derived from recorded status/result
values. The monitor never tries to *be* the detail; it tells you the id to ask
for. Heartbeat lines carry no id — a heartbeat is the absence of an event, and
inventing one would put a value in the stream that `history-show` cannot resolve.
PR state and check observations are separate events under the same `pr:` id; every
check line says `required` or `optional` before its state and name.

Round transitions are not events for the same reason: a round has no node, so it
has no `graph:` id. They reach the reader as run state, in the heartbeat.

**Dedup and snapshots.** Every source is polled, so each pass re-reads what it has
already reported. An observation is keyed by a **durable source identity** the
source itself guarantees — a journal sequence, a commit sha, a PR state signature
or an individual check observation — never by its position in a pass, so a monitor
restarted mid-run replays and then continues rather than double-reporting. Git and
GitHub are remote state that
outlives the round but is not reproducible from the run directory (a branch is
deleted once its PR merges), so what they report is persisted to
`runs/<run-id>/monitor/details.json`, keyed by the same typed id. That is what
makes a replay of a finished run show the commits and PRs the live session saw,
without re-reaching the network.

The monitor only ever reads: it writes nothing to the ledger or journal, takes no
lock a writer needs, and treats every source as optional. A missing `gh`, an
unfetched branch, or an absent history store degrades that source to silence
instead of ending the stream.

For automation, `just telemetry [--all]` emits one schema-versioned JSON run
index. It joins phase, typed provider/failure identity, latest progress,
branch/commit/checkpoint, independent agent/gate/publication-wait timing plus
overlap-safe wall time, retry lineage, comparison base, reusable gate attestation,
and the persisted last-completed-check/current-blocker rollup. Top-level metrics
make retry reuse, recovered/abandoned branches, no-diff dispatches, and time from
green gate to publication directly consumable. The default is active runs;
`--all` includes settled runs.

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
- `orchestrator/journal.py`, `ids.py` — the append-only per-transition record and
  the strict typed ids its details point at.
- `orchestrator/monitor.py` — the four-source aggregation, dedup, and the
  `just monitor` stream/exit contract.
- `orchestrator/channel.py` — FIFO transport, surface/reply validation, and the
  live `relay_supervisor` command judge.
- `orchestrator/dispatch.py` — one worker subprocess, plus `launch_orchestrator`'s
  detached orchestrator path and split-provider wiring.
