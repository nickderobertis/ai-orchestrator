<!-- llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] Operator-facing wire examples are required here; orchestrator/channel.py remains authoritative and its exact contract is exercised by channel unit/e2e tests. -->
<!-- llmlint: ignore-file[no_redundant_instruction_pointers] Live cancellation can preserve incomplete commits; the recovery command must link its safety contract at the point of use. -->

# Tracked graph orchestration

`just run-plan` turns one large task into one recorded hierarchical DAG. It is
the canonical executor for direct onejudge work, full repository lifecycles, and
explicit actions that only a person can complete. `just repo-plan` is a deprecated
alias retained so old lifecycle-only plan files keep working.

The current tracked-plan contract is schema version 5 (`"schema_version": 5`).
Plans that omit the version retain version-1 behavior for compatibility.

Version 4 adds an optional top-level `goal` mapping with required non-empty
`text` and optional `id`; when omitted, the id is derived from the text. Active
goals and their repository identities are visible across projects with `just goals`.
Before a recorded run starts, `run-plan` refuses to overlap any active run that
targets the same registered repository identity. An operator may deliberately
proceed with `--acknowledge-concurrent`; the shared identities and active run IDs
are then recorded as a `concurrent-acknowledged` journal event for audit. The
reservation remains visible across incomplete orchestrated rounds and is removed
after a standalone run completes or the final orchestrator report is durable.

The refusal says *which kind* of company each overlapping run is, because they
call for opposite decisions. A run reported `is LIVE (owner pid N on HOST)` has a
working owner: a second orchestration would share its checkouts. One reported
`holds pid N ... but shows no progress (PARKED)`, or `registered but not
observable here`, is a registration whose owner is not working — the residue
`--acknowledge-concurrent` exists to launch past. Acknowledging never hides a
live one: launching past it prints `proceeding alongside a live concurrent run`
on stderr, `just goals` states each registered owner's observed state, and the
planner's `just runs` and `just status` views carry a `CONCURRENT:` line naming
every live run that shares this one's identities — under the same
`--parked-after` threshold those views report parked with, so one view cannot
call a launch parked and a live neighbour in consecutive lines. Liveness is
observed at read time and never stored, because a recorded "this run was alive"
is false the moment its process exits.

## The planner<->orchestrator channel

`just orchestrate <plan.json>` starts a detached orchestrator onejudge run and
prints its run id. The orchestrator is the graph executor; its supervisor is the
live planner rather than a simulated-user model. During a round, the reconciler
converges the actual frontier toward a desired graph that the planner may edit
while nodes run. Recorded rounds are checkpoints and labels, not stop-the-world
adaptation barriers. The orchestrator alone writes the graph, journal, and round
ledger. After launch the planner uses only the channel, `just stop`, and the
read-only `just monitor` / `just runs` views; running `run-plan` or `next-round`
alongside it would race the single writer. Runs are owned by the session that
launched them — see [Who launched a run, and who may stop
it](#who-launched-a-run-and-who-may-stop-it).

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

Pass `--round-budget SECONDS` to `just orchestrate` to set the outer liveness
window for each graph round; it defaults to 14400 seconds (four hours). Exceeding
the window cooperatively cancels in-flight workers and emits a blocking
`round-budget` proposal, so the planner must intervene before work continues.
Raise the launch value when the graph contains lifecycle nodes whose legitimate
verify or authoring work may run longer than four hours.

`channel-next` waits for one surface. A bounded wait with no message returns
`{"status":"running","surface":null}`; a settled run returns
`{"status":"finished"}`. `channel-reply` accepts a reply file or reads JSON from
stdin when its file argument is omitted. Both sides may exit and reattach between
messages: transport state lives under `runs/<run-id>/channel/` as `up.fifo`,
`down.fifo`, `channel.json`, and the last `planner-verdict.json`.

### Planner-update pacemaker

`just orchestrate` seeds a durable 1800-second planner-update interval; override
it at launch with `--heartbeat-interval SECONDS`. A channel-side pacemaker keeps
checking that clock independently of graph reconciliation, including while a node
is inside a long-running agent step. When due, it claims and dispatches a dedicated
check-in agent. That read-only actor synthesizes a concise per-workstream update
from the run journal, status, monitor, telemetry, and labeled history, then sends
it exactly once with `just channel-surface`. The command queues the non-blocking
surface without waiting for a planner reply; the reconciler neither authors nor
relays its content. Only successful consumption through `channel-next` resets the
clock and appends `planner-surfaced` to `events.jsonl`; an update queued while no
planner is attached is neither reset nor audited as delivered.

The heartbeat record carries an atomic `in_flight` claim so concurrent pacemaker
ticks cannot dispatch duplicate check-ins. A failed attempt is recorded in
`channel/check-in.log`, clears its claim, and becomes eligible again at the next
configured interval without blocking the graph frontier. A successfully queued
surface retains the claim until delivery, preventing another actor from
duplicating the pending update.

Every planner-visible update—round boundary, proposal, or delivered heartbeat—
clears the due signal and restarts the clock. The pacemaker compares wall time
directly with the persisted `last_surface_at`, so a new round or restarted process
continues the same durable countdown rather than starting a fresh interval. To
adjust the cadence, add
`"heartbeat_interval": SECONDS` to an otherwise normal `channel-reply`; use
`"heartbeat_interval": false` to disable it. Values must be positive finite
seconds. This pacemaker is independent of the reader-side `just monitor
--heartbeat` silence display described below.

Recorded oneharness sessions preserve transport `role` and add semantic
`agent_role`: `worker`, `judge`, `orchestrator`, `check-in`, or `pr-author`.
This keeps check-in and PR-author infrastructure visible without counting either
as a node's implementation worker.

While a consumed surface is persisted awaiting an answer, `just runs` and `just
status` report `waiting for planner decision` for blocking surfaces and `waiting
for planner reply` for informational ones, followed by the surface kind and
message. A queued, unconsumed heartbeat remains non-blocking and is not reported
as a reply wait. This distinguishes completed work held at a planner boundary
from an orchestrator that is actively executing work.

That wait describes a *live* launch only. A queued surface outlives the work that
queued it, so a run reported abandoned or parked never wears it as its `just runs`
summary: the row keeps the round's own summary and the `ABANDONED (...)` or
`PARKED (...)` line beneath it says why the run stopped. `just status` is the
surface-reporting view and keeps both, in that order — the stopped line first, the
stale wait immediately after — so the reason the run is stuck stays visible without
the run reading as live supervision.

Every proposal includes `surface.blocking`: `true` means the worker or orchestrator
is awaiting the decision, while `false` is an informational follow-up that does
not stop the graph frontier. `monitor` renders `ACK REQUIRED` while any blocking
supervisor boundary, including closeout, awaits a reply.
The executor also emits a blocking `round-budget` proposal if a round exceeds
`--round-budget`; it cooperatively cancels in-flight nodes so a wedged dispatch
layer cannot leave the planner channel silent.

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
| `add` | `node`: full node mapping | Add a new node. Its `deps`, if any, must name graph nodes or valid cross-DAG references. |
| `drop` | `id`; `dependents`: `"drop"` or `"detach"` | Remove the node and recursively drop its dependents, or detach its direct dependents. |
| `reparent` | `id`; `deps`: list of dependency references | Replace an unstarted node's dependencies. |
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
unresolved same-identity dependent remains. Commands are reconciled in
order. Each accepted delta, including a multi-edge reparent or retry, is appended
by the reconciler's single writer as one `edit-committed` event carrying both the
submitted `command` and its compiled `operations`, so replay sees all of that
delta's compiled mutations or none of them, and reconstructs the graph from what
was actually submitted rather than inferring it. Channel frames are locked,
acknowledged JSON lines and are not limited to a FIFO's atomic-write size.

**Every edit is applied or rejected, and `channel-reply` reports which.** It
validates each edit against the graph projected from `events.jsonl` — through the
reconciler's own validator, so the answer is the one the reconciler would give —
and exits non-zero with the reason when it cannot be applied, before anything is
queued or sent. Edits also require a live round: replying with an edit when no
round is executing is refused with that reason, while a bare `complete` verdict
stays legal at a round boundary.

Accepted edits are appended to `runs/<run-id>/channel/commands.jsonl` and drained
from there by the reconciler, which advances `commands-cursor.json`. That durable
queue is what makes acceptance mean delivery: both `relay_supervisor` and the
reconciler's own receiver read the down FIFO, so a command riding only the frame
reached the graph or not depending on which reader won. The reconciler then
answers each claimed command in `command-outcomes.jsonl`, and `channel-reply`
waits for that verdict before it exits:

| Exit | Meaning |
| --- | --- |
| 0 | every edit in the envelope was applied by the reconciler |
| 1 | the edits were accepted and durable but not reconciled within `--timeout`; they remain queued — check `just monitor` rather than resubmitting |
| 2 | the reply was malformed, or an edit was refused at submission, or the reconciler rejected it (the reason is printed) |

An edit that passes submission can still lose a race to the frontier it was
validated against — the log a submitter reads lags the live frontier — and that
case is a synchronous rejection to the caller that issued it, not a proposal to
be noticed later. The same holds for a command left over from an earlier round or
one the round ended too soon to claim. Every rejection is also surfaced as a
`reconciler: rejected ...` proposal and recorded as an `edit-rejected` event
carrying the command and the reason. No accepted command is silently dropped.

Both edits that change only *eligibility* — `attest` and `reparent` — wake the
scheduler on the same reconciler pass. `blocked` and `skipped` are derived
statuses, so every committed edit discards them and re-derives them against the
new graph; a node the planner just made eligible is scheduled against a free
concurrency slot without waiting for an unrelated event.

**A retry may name only one branch, and it gets that branch every time.** A
lifecycle replacement node that carries both a `branch` pin and a `resume`
checkpoint is refused at submission when the two name different branches: the
lifecycle honours the checkpoint's branch and ignores the pin, so the planner
would not get the branch it named. When the pin and the resume agree but the
preserved work can no longer be resumed — it stopped being unattested-incomplete
because a recovery or an attestation landed on it — the node settles
`resume-failed` on the pinned branch with that reason, rather than moving to a
freshly generated branch. Which branch a retry produces is a function of the
envelope alone; resubmit without a `branch` pin to start the work fresh.

Dropping or retrying a running node sets its cooperative cancellation signal. A
direct dispatch stops; a lifecycle dispatch preserves commits already made on
its branch with incomplete provenance before it settles `cancelled` (publication
already in its commit phase may finish). Verify and publish preserved lifecycle
work with [`just repo-recover`](repo-lifecycle.md#integrating-completed-workstreams).

## Node shapes

Every top-level node needs a unique `id`; `deps` is an optional list of other
top-level ids or wait-only cross-DAG references of the form
`run:<run_id>#<node_id>`. Omitted `kind` defaults to `agent` for compatibility.
Cross-DAG edges resolve through the active-runs index and the upstream journal.
An unknown or inactive run, unfinished node, or failed node leaves the consumer
blocked. Once an upstream succeeds, the consumer records its journal sequence;
if that journal later advances, the consumer emits a non-crashing
`upstream-modified` event for planner review without rerunning work.

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

An unsatisfiable criterion does not fail fast. The simulated-user supervisor is
working correctly when it refuses completion, so the worker is parked and
re-asked until the turn cap — spending a full attempt, often several, to produce
a generic `not-completed` that names neither the criterion nor the cause. Before
dispatch, ask of each criterion: what would the worker run to satisfy it, and can
that command succeed right now? When a criterion's proof is necessarily
indirect, pair it with an `## Additional info` instruction to state the blocker in
the final assessment and stop rather than wait.

| Shape | Required fields | Meaning |
| --- | --- | --- |
| Direct agent | `persona`, `task`; no `repo` | Dispatch one real onejudge process in the selected project directory. |
| Lifecycle agent | `repo`, plus `persona` + `task` or `steps` | Work on an isolated branch/worktree and publish through the repository's merge-path gate and registered policy. Dispatch refuses identities without an executable pre-push hook or required PR checks. |
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

Do not split implementation from the tests that prove it into separate nodes or
steps: the implementing agent writes those tests in the same dispatch, and the
unit settles fully proven. The narrow test-focused exception and persona choice
are defined in [the granularity rule](../AGENTS.md#the-granularity-rule-the-core-judgment).

`run-plan` starts every node whose dependencies are `done`, bounded by
`concurrency`. Lifecycle dependencies on the same repository identity also carry
publication/stack ancestry; cross-repository dependencies only schedule. During
an orchestrated run, [live edits](#live-graph-edits) can change the desired graph
and the reconciler applies the new reachable frontier without waiting for the
round to settle.
Standalone `run-plan` accepts the same `--round-budget SECONDS` option described
for orchestrated launches above. The budget is a liveness backstop, not a
replacement for per-dispatch timeout, heartbeat, or stall detection.

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
- `failed` with outcome `infrastructure-failure`: a recognized provider or
  harness failure, ENOSPC, OOM kill, or failed scratch-capacity preflight
  prevented dispatch from running. This is terminal across
  rounds: the reconciler surfaces the underlying error as a blocking planner
  proposal on first occurrence, and replanning does not dispatch the node again.
  Unknown or ambiguous errors remain ordinary retryable task failures.
- `failed` with a `no-agent-progress` diagnosis: the budget was spent without the
  agent producing anything. onejudge counts every turn it attempts, and a provider
  that accepts a turn and answers with nothing still spends one, so a failing
  provider drains a whole cap in minutes. Reported apart from an ordinary turn cap
  because the two want opposite responses: retrying this one unchanged spends the
  next budget the same way.
- `skipped`: a failed dependency made execution unsafe. Failure takes precedence
  over a simultaneous waiting path, so such a descendant is skipped, not blocked.

The result's top-level `state` is `failed` if any node failed or skipped,
otherwise `waiting` if any node waits or is blocked, otherwise `complete`. `ok` is
true only for `complete`. Human and JSON output carry the same facts. Exit status
is 0 for `complete`, 1 for `waiting` or `failed`, and 2 for invalid plan, ledger,
configuration, or command input. Recorded result schema v5 adds the terminal
`infrastructure-failure` and successful `already-integrated` outcome values.

## Recorded rounds

Recording is on by default:

Before each recorded round is claimed, the executor runs the same conservative
scratch sweep exposed as `just sweep-scratch`. Dead `orchestrator-watchdog-*`
directories are identified by the ownership proof described under
[dispatch scratch ownership](onejudge-integration.md#dispatch-scratch-ownership),
and every directory that proof does not clear is reported as retained rather
than removed. Known third-party scratch is
eligible only after the conservative age threshold and only when no lifecycle
dispatch holds the host scratch shared lock. A destructive sweep takes the
exclusive lock without waiting; when a dispatch is active it skips third-party
scratch, reports that decision, and still removes definite dead watchdog
directories. Use
`just sweep-scratch --dry-run` to inspect candidates;
`orchestrator.scratch.THIRD_PARTY_PATTERNS` is the authoritative documented
pattern list and extension point.

Scratch the harness itself produces cannot wait for quiescence: a private `nx`
install per `bunx nx` invocation, a copy of Nx's ~22 MB native binary per workspace
root, a run directory per pytest session, and an
effective-config directory per onejudge dispatch appear *because* dispatches are
running, at gigabytes per hour. `orchestrator.scratch.UNREFERENCED_FAMILIES` is the
authoritative family list and extension point for these, and they are swept while
dispatches run, without the exclusive lock. What replaces quiescence is proven
non-reference: the sweep reads every live process's argv, environment, working
directory, root and executable links, open descriptors, and file-backed memory
mappings, and a candidate any of them names is retained and reported —
`retained N directories referenced by live processes`. Mappings are load-bearing
rather than belt-and-suspenders: Nx `dlopen`s its cached native binary and keeps no
descriptor, so a running `nx` names that cache nowhere else. That proof is retaken
against fresh procfs state immediately before removal. A procfs that cannot show
the sweeping process itself cannot answer the question at all, which is not the
same as answering "nothing is referenced": these families are then left alone
entirely and the run reports that it could not prove them unused. A short minimum age
(`UNREFERENCED_MIN_AGE_SECONDS`, 15 minutes) covers only the gap between creating a
directory and the first instant a process names it; the 24-hour default still
governs `THIRD_PARTY_PATTERNS`, which have no such proof behind them.
`--min-age-hours` can shorten that age but never lengthens it past the family
default. A name too generic to sweep on is not swept on: an Nx temp install is
recognized by its shape — one `nx` devDependency, an installed `node_modules`, and
nothing else — an Nx native cache by its exact `nx-native-file-cache-<7 hex>` name
holding nothing but `.node` copies, and each family honors its producer's own
retention, so pytest keeps
the newest three runs per root, a run whose `.lock` names a live session, and
whatever `pytest-current` points at.

Every sweep names the families it examined and, separately, the families it could
not — `swept families: watchdog, nx-install, …` and `skipped families: third-party
(lifecycle dispatch active)`. Each family appears in exactly one of the two lists,
so `reclaimed 0 bytes` always means "nothing was reclaimable", never "a family was
never looked at". A cleanup run that silently skips the family filling the disk
reads as a clean bill of health, which is worse than no cleanup at all.

```text
runs/<run-id>/round-01/plan.json
runs/<run-id>/round-01/status.json
runs/<run-id>/round-01/result.json
runs/<run-id>/round-01/<node>[/<step>]/worker-report.json
runs/<run-id>/round-01/<node>[/<step>]/oneharness-session.json
runs/<run-id>/humans.json
```

Without `--run`, the id is derived from the plan's `name` or filename and made
unique. `--runs-dir` moves the ledger, `--no-record` opts out, and `--recover`
claims a `running` or `abandoned` round only after its recorded owner is proven
gone. Plan and result writes are atomic; a live round cannot be claimed by
another process.
`just runs` summarizes the latest completed round, including waiting action prose
and what each action unblocks, then points to `just results <run>`. The results
view lists every node's status and outcome, links its typed-id detail view, and
prints the concrete full-artifact paths for failures. A run containing failed
nodes is still a successful results lookup; only an invalid invocation exits
non-zero. `just status` gives the same next step when a worker maps to a ledger
round.

Each recorded schema-v4 node result carries its own `artifacts` paths. Lifecycle
step payloads carry their step-specific raw onejudge report and stable
oneharness-session pointer. Local gate failures surface from `git push`; the
lifecycle records `gate-failed` when hook output identifies the gate and otherwise
records a self-describing `error` outcome with Git's diagnostic.
Remote-first failures remain named required-check outcomes. Each lifecycle node
also records one `merge-gate-coverage` event before it dispatches, naming the
`pre-push` hook and required checks that will verify it, so a late rejection can
be read against what was expected to run. Each gated push then records a
`verification-finished` event with its verdict and a bounded `output_tail`, and
appends the whole run to the node's `artifacts.gate_log` — for a green
publication as much as a rejected one, so a settled node can show what its gate
did rather than only that nothing objected.

That log holds every record the merge path produced, in order, including the
publications that never reached a gate at all: a rebuild whose base was advanced
under it, or that could not fetch, build its worktree, or write. Those settle
with a `publication-failed` event carrying the same bounded `output_tail` — for a
lost base race, one line per attempt naming the sha it verified against and the
sha it then observed — and the result's detail names the log. Before that, such a
failure recorded neither, which is how a run could settle seconds after a green
gate with no evidence of what went wrong. Because artifact
paths are in the terminal `GraphResultItem`, crash projection retains them
byte-for-byte.

A failed `just gate` names the tier that failed and the loop to close it. An
llmlint failure also prints the comparison base the gate resolved: clear those
findings against `just lint-llm-diff <base>` alone, then rerun the complete `just
gate` once to confirm. A full gate cycle per lint fix re-pays the deterministic
tier for a finding that tier cannot re-check.

Execution is a long-lived reconcile loop: it compares the round's live desired
graph with actual node state projected from `events.jsonl`, starts the reachable
frontier, and reacts to each completion until the graph is terminal. Journal
schema 2 terminal node events carry the complete serialized node result, so
`--recover` can replay a dead round's prefix, retain settled nodes byte-for-byte,
resume nodes that were running without another start transition, and converge the
remaining frontier. Schema 1 journals remain readable, but a schema 1 prefix with
settled nodes cannot be recovered because it predates durable node results.

The journal record contract is schema version 7, pinned by
`tests/golden/static-round-events-v7.json`; bump both together. Version 6 is
additive over 5: it adds the `edit-rejected`, `conflict-resolution-started`, and
`conflict-resolution-finished` kinds, and an optional `command` beside
`edit-committed`'s `operations`. A v5 journal therefore still replays — its
committed edits simply carry no command — while a record written at 6 or later
must carry the command that produced its mutations. Version 7 is additive over 6:
it adds `publication-failed`, for a publication that ended before any gate could
rule on it. Every supported version stays readable; a reader skips records from a
version it does not know rather than failing the round it is observing.

**A record's readability and its claim on a sequence number are different
questions.** A writer resuming a journal takes its next sequence above every line
that *claims* one for this run — including a line written by a newer schema, which
it cannot read. Skipping such a line when picking the number is how one run came to
hold two events at `seq` 104: the planner's `channel-next` ran from a newer
checkout than the orchestrator it was supervising. Strict replay stays strict in the
other direction — a line it cannot read might have been an authoritative graph
mutation, so a round refuses to record a result folded without it and says so — but
a *collision*, two records sharing a number with both present and in order, loses
nothing and is read through. That last part is not cosmetic: `channel-reply`
validates every live edit against this reader, so treating a collision as fatal
ends a healthy run's supervisability, which is what it did.

### A round outlives the turn that launched it

A round must not die because the orchestrator surfaced an update and ended its turn.
`just run-plan` and `just next-round` therefore fork before doing anything: the child
leads a session of its own and owns the round, while the parent exists only to relay
its exit status. The launching turn's teardown — and `uv run`, which forwards the
signal it receives to its own direct child and then escalates to SIGKILL — reaches
only that parent. `orchestrator/detach.py` holds the full reasoning; the practical
consequence is that Ctrl-C reaches the relaying parent rather than the round, so the
round announces the pid to signal when you do want it stopped.

The round's own exit statuses cross that fork unchanged — 0 complete, 1 unfinished, 2
rejected input, 128+N signalled. An exception escaping the round is the exception: it
ends the round there, prints its traceback, and exits **70**, so a crash is never read
as the unfinished round that also exits 1.

A round that stops without recording a result never stays `running`. Its owner writes
`{"status": "abandoned", "reason": ...}` on any catchable teardown signal and on any
other exit that recorded no result, and `--recover` reclaims an `abandoned` round the
same way it reclaims a dead `running` one. SIGKILL is the one death nothing can
record, so `just runs` and `just status` derive abandonment from the recorded owner's
pid: a dead owner is reported as `round-NN ABANDONED (...)` with the reclaiming
command, never as work in flight.

A launched orchestrator is derived the same way. `just orchestrate` records
`{"status": "running", "pid": ...}` once and never rewrites it, so a process that
crashed or was killed between rounds left a run reading exactly like ordinary
finished work. When this host can prove that pid gone, the launch never wrote a
report, and no round is still in flight, both views report the run as
`SETTLED (orchestrator pid N is gone ...)`. Every unknown withholds that verdict
instead — an owner that cannot be probed, an unreadable or unparseable record, or a
round still working — because sending a planner to tear down live work is the worse
error. Withholding it is not the same as saying nothing: a record that still claims a
`running` owner this host could not refute keeps its run listed as `ACTIVE`, and an
owner on **another host** is exactly that case, since a pid means nothing across
machines. A run another orchestrator is driving therefore reads as the live work it
is. Only a record this host cannot read at all drops out of both views, having
supported no claim either way.

A live pid is ownership, not progress. A launched orchestrator that keeps its pid
while doing nothing — no child process, no planner surface, and no ledger
write — is *parked*, and `just runs`, `just status`, and `just monitor` report it
as `PARKED (...)` rather than as running. All three signals must be absent past
the threshold, which defaults to 1800 seconds (the default planner-update
interval) and is overridable with `--parked-after SECONDS` on `just runs` and
`just status`. Every unreadable input resolves toward "still working", so a busy
orchestrator is never misreported as parked: one live descendant of the launch or
of its round owner, one fresh surface, or one journal, plan, status, or result
write is enough to keep it reported as running. A persisted `last_surface_at` that
is not a finite number is discarded rather than timed, since a non-finite stamp
would otherwise make the run look eternally fresh or eternally silent.

### Who launched a run, and who may stop it

Several planners share this host, so every view says whose run it is looking at.
`just orchestrate` records the launching session automatically: it mints a
`launch_id` into the run directory and writes the launcher and its session id to a
short-lived record under `$XDG_STATE_HOME/ai-orchestrator/launches/`, outside every
repository, because the session id may be sensitive. The launcher is detected from
the environment the harness exports — never from process ancestry — and
`--launcher` / `--launcher-session` (or `$ORCHESTRATOR_LAUNCHER` /
`$ORCHESTRATOR_LAUNCHER_SESSION`) still override it. A launch nothing identifies,
and every run recorded before this was populated, resolves to `unknown`: missing,
malformed, and expired records are all read the same way, and none of them is ever
attributed to the reader. `orchestrator/launch.py` is the single source for the
scheme.

```sh
just runs       # * demo   [mine]                     round-02  (2 done)
                #   other  [claude-code:3f9a1c2e]     round-01  (1 done)
                #   older  [unknown]                  round-01  (1 done)
just runs --mine             # only the runs this session launched
```

`[mine]` is this session; a named session is another planner's, labelled by a
stable digest rather than by the session id itself; `[unknown]` is a run nobody can
attribute. A provenance-less run never displays as the caller's.

### Stopping a run

```sh
just stop <run-id>                       # a run this session launched
just stop <run-id> --force               # after reporting whose run it is
just stop <run-id> --grace 30            # SIGTERM budget before SIGKILL (default 10s)
```

`just stop` refuses a run launched by another session, and refuses an `unknown` one
by the same rule, naming the owner or the unknown state; `--force` prints who owns
it and which recorded processes will be stopped before it proceeds. It resolves
those processes from the run's own `orchestrator/status.json` and `round-NN/status.json`
records and walks the live tree below them — never from `ps` output, and never as
one process group, because a dispatched worker leads a group of its own and a
`killpg` on the recorded pid would leave it running. Round owners take SIGTERM
first so each records its own abandonment; survivors are escalated to SIGKILL after
the grace period, and a process that outlives even that is reported by pid with a
non-zero status rather than hidden under a success.

Stopping records nothing about the run itself: the round is abandoned by its own
owner, exactly as an interrupted round is, so `just runs` reports
`round-NN ABANDONED (owner pid N took SIGTERM); reclaim with: just run-plan ... --recover`
and the work is reclaimable. `complete` on the channel is a completion verdict and
does not stop scheduling; `just stop` is what ends a run.

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
just monitor                      # newest active run
just monitor RUN_ID               # one named run
just monitor --once               # replay what is known, report state, exit 0
just monitor --follow             # follow even when output is captured
just monitor --format jsonl       # one JSON record per line, no header
just monitor --heartbeat 30 --poll-interval 5
```

Unchanged polls back off exponentially to a configurable bounded interval.
`--max-poll-interval` changes that bound. New observations reset the
initial interval. Silence heartbeats remain independent of polling frequency.

Without `RUN_ID` it picks the **newest active** run — anything that has not
completed successfully, including one merely waiting on a human, since that is the
most important state to be watching. If nothing is active it watches the newest
run. `--runs-dir` moves the ledger as everywhere else.

**When it follows, and when it returns.** Following is a terminal affordance: the
stream is flushed line by line and Ctrl-C is how a person ends it. A caller whose
stdout is a pipe or a file sees none of that — it gets the whole output when the
process exits — so on a terminal `just monitor` follows, and off one it makes a
single pass and exits 0, exactly as `--once`. `--follow` asks for the follow
anyway, for a reader that does consume the stream incrementally. This is not a
convenience: the planner is an automated supervisor whose invocations are always
captured, so the command `launch.json` advertises was, for it, one that produced
nothing and never returned, and every status check was done by reading
`events.jsonl` and `/proc` by hand instead.

**The bound that pass returns within.** `tests/e2e/test_monitor_e2e.py` holds the
real command to `orchestrator.monitor.RETURN_BOUND_SECONDS` on a runs root the size
of the planner's own. Nothing in the command computes that bound; what it enforces
is `SOURCE_TIMEOUT_SECONDS`, and the bound is derived from it so the two cannot
drift. The journal, the ledger, the snapshot, and git are local reads; the two
sources that are not — oneharness history, a subprocess over a store that only
grows, and `gh`, which is the network — each carry that deadline. `gh` carries it
twice: per call *and* as a budget for the whole source, so a run with many linked
PRs cannot spend one timeout per PR. An expired read is the same silence an absent
`gh` or history store already degrades to. `--source-timeout` moves that deadline;
a real root answers in about a second, so the bound is the guarantee, not the
expectation.

**Exit contract.** Only a graph that *completed successfully* ends the *follow*
(exit 0). Waiting on a human, a failed node, and an executor that died all keep
heartbeating, because each is a state a person acts on and the run then continues
— through `next-round`, whose new round directory the next poll picks up. A
monitor that exited on them would report "finished" for a run that is merely
stuck. `--once` — and every non-terminal invocation — always exits 0 after one
pass; only follow mode encodes completion in its status. A completed graph reads
the same either way (`graph complete`), because that detail is about the run and
not about how it was being watched. Exit 2 is an unresolvable run or bad input.

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

All three read-only views take the run id `launch.json` advertises: `just monitor
RUN_ID`, `just status RUN_ID`, and `just telemetry RUN_ID`. Each resolves it the
same way — an exact run directory, or a plan name that names exactly one active
launch. `status`'s positional keeps its original count meaning for a plain
integer (`just status 5` still lists five recent tasks), so a run whose id is all
digits is addressed through `just monitor` or `just results` instead. Scoped,
`status` reports only that run's indicators and the sessions its own scopes
labelled; `telemetry` reports that run whether or not it has settled, since naming
it is the request and the settled-run filter exists only to keep the *unscoped*
index about live work.

An unsettled round has written no `result.json`, so both `telemetry` and the DAG
read model describe its nodes from the journal itself: a node is `running` only
until the journal records it settling. A node recorded as `node-failed` reads as
failed in every read-only view, including while its round is still in flight.

`status` answers the same question from different evidence — a session is running
while its branch is still a checked-out worktree — and there the journal still
wins. A failed node can keep its checkout (a direct agent works in one it never had
to remove), so a session whose node the journal has settled is reported with that
recorded status, never as running. The rule across all of these is one rule: no
read-only view calls a node running once the ledger has recorded it settled,
whatever the filesystem still looks like.

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

`just telemetry-server` serves the same read model continuously instead of once:
a loopback-bound HTTP API plus an SSE invalidation stream over a runs directory,
for the DAG UI and any other live viewer. It is read-only in the same sense the
monitor is — no route mutates a run, executes a command, or accepts a path — so
it is safe to leave running beside an active orchestration. Its flags, response
shapes, and event vocabulary are fixed by
[`dag-ui/design.md`](dag-ui/design.md#running-it).

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

A failed lifecycle node whose preserved branch is carried forward is continued
**automatically at most `replan.MAX_AUTOMATIC_ROUND_RESUMES` times**. The count is
kept on the plan node's `resume.attempts` and settles the node out of the next
round once it is spent, exactly as a `drop` would: the failing result stands for
the planner, and the branch stays recoverable with `just repo-recover`. An explicit
`retry` edit clears the count, so the bound only ever stops the harness repeating
itself — never a decision the planner made after reading the result.

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
