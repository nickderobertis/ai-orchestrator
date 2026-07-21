<!-- llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] Operator-facing wire examples are required here; orchestrator/channel.py remains authoritative and its exact contract is exercised by channel unit/e2e tests. -->
<!-- llmlint: ignore-file[no_redundant_instruction_pointers] Live cancellation can preserve incomplete commits; the recovery command must link its safety contract at the point of use. -->

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
live planner rather than a simulated-user model. During a round, the reconciler
converges the actual frontier toward a desired graph that the planner may edit
while nodes run. Recorded rounds are checkpoints and labels, not stop-the-world
adaptation barriers. The orchestrator alone writes the graph, journal, and round
ledger. After launch the planner uses only the channel and the read-only `just
monitor` / `just runs` views; running `run-plan` or `next-round` alongside it
would race the single writer.

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
conversation context. Settled workers may also surface `kind: "proposal"` while
the round continues. Proposals are advice only: workers never receive the down
FIFO, and only the planner can issue edits. The planner replies with one of these
legacy verdict shapes:

```json
{"completion":false,"message":"retry X with the fixture requirement","reason":"the graph is not complete"}
{"completion":true,"reason":"publication and follow-up triage verified"}
```

`completion: false` requires both `message` and `reason`; `completion: true`
requires `reason`. Completion is reserved for a verified `closeout` after the
whole graph is published and follow-ups are triaged. See [Live graph edits](#live-graph-edits)
to change the graph without waiting for a round boundary.

The three planner-facing recipes are:

```sh
just orchestrate plan.json --runs-dir /host/path/runs
just channel-next RUN --runs-dir /host/path/runs
just channel-reply RUN reply.json --runs-dir /host/path/runs
just channel-approve RUN --runs-dir /host/path/runs
just channel-reject RUN "verification failed" --runs-dir /host/path/runs
just channel-continue RUN "apply the edit and continue" --runs-dir /host/path/runs
```

`orchestrate` prints a JSON launch record containing `run_id`, `channel_id`, and
literal `commands.channel_next` / `commands.monitor` values, and persists the same
handoff at `runs/<run-id>/planner.md`. Use that `run_id` for every supervision
recipe. A plan name is also accepted when it identifies exactly one active launch;
an ambiguous or stale name fails and lists valid run ids.

`channel-next` waits for one surface. A bounded wait with no message returns
`{"status":"running","surface":null}`; a settled run returns
`{"status":"finished"}`. `channel-reply` accepts a reply file or reads JSON from
stdin when its file argument is omitted. Both sides may exit and reattach between
messages: transport state lives under `runs/<run-id>/channel/` as `up.fifo`,
`down.fifo`, `channel.json`, and the last `planner-verdict.json`.

Every proposal includes `surface.blocking`: `true` means the worker or orchestrator
is awaiting the decision, while `false` is an informational follow-up that does
not stop the graph frontier. `monitor` renders `ACK REQUIRED` while any blocking
supervisor boundary, including closeout, awaits a reply.

The raw reply schema remains available for edits and automation. A continuing
reply is `{"completion":false,"message":"what to do next","reason":"why"}`;
an approval is `{"completion":true,"reason":"what was verified"}`. Either may
also contain `"version":1` plus a `"commands"` array using the operations below.
The convenience recipes construct the common forms: `channel-approve` sends a
completed verdict, `channel-reject` sends a continuing verdict whose reason and
message are the supplied text, and `channel-continue` sends the same continuing
shape for a non-rejection instruction.

Only the orchestrator crosses round boundaries. Worker onejudge processes remain
bounded to the graph round that dispatched them and never use the planner channel.
The orchestrator may author in an isolated execution checkout, but its
`--runs-dir` must resolve to the same host-visible path for the detached process
and planner. The round ledger and its sibling `channel/` directory cannot live
only inside a disposable worktree or container-private filesystem.

### Live graph edits

Send a version-1 edit envelope to `channel-reply`; this is the shape exercised by
`tests/e2e/test_live_edit_e2e.py`:

```sh
just channel-reply RUN --runs-dir /host/path/runs <<'JSON'
{"version":1,"commands":[{"op":"reparent","id":"pending","deps":["slow_b"]},{"op":"drop","id":"slow_b","dependents":"detach"},{"op":"attest","ref":"approve"}]}
JSON
```

The accepted commands are:

| `op` | Required fields | Effect |
| --- | --- | --- |
| `add` | `node`: full node mapping | Add a new node. Its `deps`, if any, must name graph nodes. |
| `drop` | `id`; `dependents`: `"drop"` or `"detach"` | Remove the node and recursively drop its dependents, or detach its direct dependents. |
| `reparent` | `id`; `deps`: list of node ids | Replace an unstarted node's dependencies. |
| `retry` | `id`; `node`: full replacement node mapping with a new id | Supersede a running, failed, or cancelled node with a fresh lineage and redirect its direct dependents. |
| `attest` | `ref` | Complete a currently ready, waiting human action. |
| `complete` | `reason` | Journal the planner's completion request independently of graph mutation. |

A command-only envelope gets a synthesized continuing verdict. Commands can
instead accompany either legacy verdict, for example:

```json
{"completion":false,"message":"apply the replacement and continue","reason":"the failed node is retryable","version":1,"commands":[{"op":"retry","id":"failed","node":{"id":"retry","task":"No diff","expects_no_diff":true}}]}
```

`complete` is the versioned equivalent of a completion verdict and may share an
envelope with graph edits:

```json
{"version":1,"commands":[{"op":"complete","reason":"publication and follow-up triage verified"}]}
```

Completion is decoupled from scheduling: the reconciler journals it for audit
and replay, while the graph continues to settle its frontier and the supervisor
verdict closes the orchestrator run.

Every delta is validated against the live frontier before commit. The resulting
graph must still satisfy the normal plan schema: ids and referenced dependencies
must exist, and dependencies cannot form a cycle or self-edge. `reparent` cannot
change a started node; `retry` requires a running, failed, or cancelled target and
a new replacement id; `attest` requires a ready waiting human action. `drop` must
state the dependents' fate and cannot remove the last publication anchor while an
unresolved same-identity dependent remains. A rejected delta changes no state and
returns as a `reconciler: rejected ...` proposal. Commands are reconciled in
order. Each accepted delta, including a multi-edge reparent or retry, is appended
by the reconciler's single writer as one `edit-committed` event, so replay sees
all of that delta's compiled mutations or none of them. Channel frames are
locked, acknowledged JSON lines and are not limited to a FIFO's atomic-write
size.

Dropping or retrying a running node sets its cooperative cancellation signal. A
direct dispatch stops; a lifecycle dispatch preserves commits already made on
its branch with incomplete provenance before it settles `cancelled` (publication
already in its commit phase may finish). Verify and publish preserved lifecycle
work with [`just repo-recover`](repo-lifecycle.md#integrating-completed-workstreams).

## Node shapes

Every top-level node needs a unique `id`; `deps` is an optional list of other
top-level ids. Omitted `kind` defaults to `agent` for compatibility.

The planner writes every agent node and step `task` with this prose template:

```markdown
## What
<the change>

## Why
<the user's motivation: impact and decision driver>

## Acceptance criteria
<detailed, specific source of truth for done>

## Additional info
<optional; omit the section when empty>
```

The task is visible to both the worker and judge, so its Acceptance criteria hold
all detailed, change-specific requirements. `done_when` is judge-only: it must
always require that all task acceptance criteria are met, and may add broader
quality measures such as a green gate, held coverage, or no regressions. Do not
hide specific acceptance criteria only in `done_when`. `Why` is the user-facing
impact and what drove the decision, not an orchestration handoff. If the request
does not make that why clear, the planner must ask the user before dispatch rather
than inventing it.

| Shape | Required fields | Meaning |
| --- | --- | --- |
| Direct agent | `persona`, `task`; no `repo` | Dispatch one real onejudge process in the selected project directory. |
| Lifecycle agent | `repo`, plus `persona` + `task` or `steps` | Work on an isolated branch/worktree, verify, and publish through the repository's registered policy. |
| Human | `kind: human`, `task`; no persona or execution fields | Record an action only an external person or outside system can perform. Planner review, acceptance, validation, and integration happen through live channel edits, not a human node. |

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
requirements in the structured `task` prose and use a terse per-node `done_when`
that references all task acceptance criteria plus any broader bar. Use `max_turns`
when a task needs more room, rather than proliferating
personas. Every dispatch already has the built-in supervisor/reviewer; reserve a
dedicated `reviewer` step for complex DAGs where it reviews and integrates several
agents' independently produced work. Dependencies should name only real inputs so
unrelated branches remain parallel.

`run-plan` starts every node whose dependencies are `done`, bounded by
`concurrency`. Lifecycle dependencies on the same repository identity also carry
publication/stack ancestry; cross-repository dependencies only schedule. During
an orchestrated run, [live edits](#live-graph-edits) can change the desired graph
and the reconciler applies the new reachable frontier without waiting for the
round to settle.

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
runs/<run-id>/round-01/<node>/gate.log
runs/<run-id>/round-01/<node>[/<step>]/worker-report.json
runs/<run-id>/round-01/<node>[/<step>]/oneharness-session.json
runs/<run-id>/humans.json
```

Without `--run`, the id is derived from the plan's `name` or filename and made
unique. `--runs-dir` moves the ledger, `--no-record` opts out, and `--recover`
claims a `running` round only after its recorded owner is proven gone. Plan and
result writes are atomic; a live round cannot be claimed by another process.
`just runs` summarizes the latest completed round, including waiting action prose
and what each action unblocks, then points to `just results <run>`. The results
view lists every node's status and outcome, links its typed-id detail view, and
prints the concrete full-artifact paths for failures. A run containing failed
nodes is still a successful results lookup; only an invalid invocation exits
non-zero. `just status` gives the same next step when a worker maps to a ledger
round.

Each recorded schema-v4 node result carries its own `artifacts` paths. Lifecycle
step payloads carry their step-specific raw onejudge report and stable
oneharness-session pointer; complete gate stdout and stderr are atomically stored
as `gate.log` before the terminal result is journaled. Because those paths are in
the terminal `GraphResultItem`, crash projection retains them byte-for-byte.

Execution is a long-lived reconcile loop: it compares the round's live desired
graph with actual node state projected from `events.jsonl`, starts the reachable
frontier, and reacts to each completion until the graph is terminal. Journal
schema 2 terminal node events carry the complete serialized node result, so
`--recover` can replay a dead round's prefix, retain settled nodes byte-for-byte,
resume nodes that were running without another start transition, and converge the
remaining frontier. Schema 1 journals remain readable, but a schema 1 prefix with
settled nodes cannot be recovered because it predates durable node results.

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

Use `just telemetry --breakdown [--all]` for the operator view. The practical
field guide and diagnostic workflow are in [`telemetry.md`](telemetry.md); the
versioned cross-layer contract is in [`telemetry-model.md`](telemetry-model.md).

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
- `orchestrator/plan.py` — direct-agent parsing and the shared DAG reconciler.
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
