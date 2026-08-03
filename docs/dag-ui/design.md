# DAG visualization system contract

<!-- llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] This is the explicitly
authoritative cross-language integration contract requested for implementations that do not yet
exist. It names Python record sources and the TypeScript package exports at each mirrored shape;
consumer implementation must import/generate from those sources, and golden drift gates become
possible only when the API and renderers land. -->
<!-- llmlint: ignore-file[changed_behavior_has_e2e] The document is a contract, while the only
executable addition is the framework-neutral layout package. Its public API tests exercise stable
geometry and invalid graph rejection; browser/CLI end-to-end tests belong with those future
consumers rather than a scaffold inventing them. -->

This document is the authoritative integration contract for the DAG web UI,
read-only API, CLI SVG renderer, and their shared layout package. It consumes the
repository telemetry model and fixes how those semantics are exposed and rendered. Implementations
must not infer state from display strings when a typed field exists.

## Boundaries and versioning

The Python server owns filesystem access, journal projection, telemetry
normalization, session discovery, validation, and authorization. TypeScript
clients consume its JSON; they never read `runs/` or oneharness history directly.
`@ai-orchestrator/dag-layout` owns renderer-neutral graph geometry. The browser
and CLI SVG adapter own presentation only.

All JSON uses UTF-8, RFC 3339 UTC timestamps, finite JSON numbers, and
`application/json`. IDs are opaque strings. New optional fields are additive and
omitted when unavailable; empty optional objects are omitted. Required fields
cannot change without a new API major version. Unknown fields must be ignored.
Invalid enums, negative durations/counters, non-finite numbers, and bad
references are rejected at the Python boundary.

The initial API base is `/api/v1`. Its telemetry payload embeds the existing
telemetry index at `telemetry_schema_version: 8`, mirroring that index's own
`schema_version`; this API version does not replace or renumber that contract.
`scripts/check-dag-state-contract.py` reconciles every copy of that number here
against `orchestrator.telemetry.TELEMETRY_SCHEMA_VERSION`.

## Read model

### Run list and detail

`GET /api/v1/runs?include_settled=false` returns:

```ts
interface RunList {
  api_version: 1;
  telemetry_schema_version: 8;
  observed_at: string;
  runs: RunSummary[];
}

interface RunSummary {
  run_id: string;
  state: string;
  phase: string;
  last_event: string | null; // null when the run has recorded no event yet
  last_progress_at?: number; // existing epoch-seconds value
  timing_quality: "complete" | "partial" | "legacy";
  linkage_quality: "native" | "labelled" | "inferred";
  timing: Timing;
  node_counts: Record<string, number>;
  launch?: RunLaunch; // omitted when the run recorded no launch_id
}

// The run-level join of the recorded launch_id to its provenance record. See
// "Launch and session provenance"; launcher_session_id appears only when the
// server's redaction policy is configured to expose it.
interface RunLaunch {
  launch_id: string;
  launcher: "claude-code" | "codex" | "unknown";
  launcher_session_id?: string;
}
```

Runs are ordered by most recent progress descending, then `run_id` ascending.
`include_settled` defaults to false, matching `just telemetry`; true matches
`just telemetry --all`.

`GET /api/v1/runs/{run_id}` returns a `RunDetail`:

```ts
interface RunDetail {
  api_version: 1;
  telemetry_schema_version: 8;
  observed_at: string;
  run: RunTelemetry;
  rounds: Round[];
  conversations: DagConversation[];
  details: DetailSnapshot; // persisted PR/commit/check detail from monitor/details.json
  logs?: Record<string, string>; // bounded, path-free tails of the run's own logs
  launch?: RunLaunch; // same run-level launch join as RunSummary
}

// The persisted PR/commit/check observations, exactly as
// orchestrator.monitor.DetailSnapshot.record() serializes them.
interface DetailSnapshot {
  version: number;
  commits: Record<string, unknown>;
  prs: Record<string, unknown>;
  check_rollup?: unknown;
}
```

`details` always appears (an empty snapshot serializes as `{version, commits:{},
prs:{}}`); `logs` and `launch` are omitted when the run wrote no logs or recorded no
`launch_id`.

`GET /api/v1/runs/{run_id}?include_conversations=false` serves `conversations` as
an empty array. Transcripts dominate this payload — a real run carries megabytes of
them across hundreds of sessions, refetched on every live update — so a client that
reads the run timeline instead asks for none of them. This is an opt-out, not a
schema change: `api_version` stays `1`, `conversations` stays required and present,
and the client simply asked for nothing in it. It defaults to `true`.

`RunTelemetry` is exactly `RunTelemetry.record()` from
`orchestrator/telemetry.py`: required `run_id`, `state`, `phase`, `last_event`,
`timing`, `nodes`, `usage`, `timing_quality`, `linkage_quality`, `sources`,
`node_work_ms`,
`turns`, and `lint`; optional `last_progress_at`, `providers`, `failure`, and
`check_rollup`. A `NodeTelemetry` is exactly `NodeTelemetry.record()`: required
`node`, `status`, `sessions`, `turns`, and `lint`; optional `outcome`, `branch`,
`comparison_remote`, `comparison_base`, `checkpoint`, `commit`,
`retry_lineage`, `gate_attestation`, `failure`, `timing`, `usage`, and
`tool_commands`.
`last_event` is required but nullable in both payloads: a run that has recorded no
journal event yet — a run that has only just launched — serves it as `null` rather
than as an empty string, so its absence is representable instead of degenerate.

The following aliases spell out the nested shapes without changing the Python
contract:

```ts
interface Timing {
  agent_seconds: number;
  judge_seconds: number;
  llmlint_seconds: number;
  gate_seconds: number;
  publication_wait_seconds: number;
  lock_wait_seconds: number;
  setup_seconds: number;
  scheduling_seconds: number;
  wall_seconds: number;
  agent_model_ms: number;
  judge_model_ms: number;
  llmlint_model_ms: number;
  tool_ms: number;
  idle_orchestration_ms: number;
  unattributed_ms: number;
  wall_ms: number;
  fractions: {
    agent_model: number;
    judge_model: number;
    llmlint_model: number;
    tool: number;
    idle_orchestration: number;
    lock_wait: number;
    setup: number;
    scheduling: number;
  };
}

type UsageValue = number | null;
interface UsageParty {
  input_tokens: UsageValue;
  output_tokens: UsageValue;
  cache_read_tokens: UsageValue;
  cache_write_tokens: UsageValue;
  cost_usd: UsageValue;
}
interface Usage {
  agent: UsageParty;
  judge: UsageParty;
  llmlint: UsageParty;
  total: UsageParty;
}
interface SessionLink {
  session_id: string;
  history_id?: string | null;
  role: "agent" | "judge" | "llmlint";
  turn_index?: number | null;
  started_at?: string;
  finished_at?: string | null;
}
```

Unknown usage is `null`, never zero. The API returns the collector's timing,
quality, source, legacy-alias, node-work, metric, and llmlint-cohort values
verbatim; it must not independently recalculate or reinterpret them.

### Round projection

Each `rounds[]` item is a JSON representation of
`orchestrator.projection.RoundProjection`:

```ts
interface Round {
  run_id: string;
  round: number;
  plan: {
    tasks: PlanTask[];
    schema_version?: number;
    concurrency?: number;
    name?: string;
  };
  node_states: Record<
    string,
    "running" | "done" | "failed" | "waiting" | "cancelled"
  >;
  node_status: Record<string, NodeStatus>;
  node_gated_by: Record<string, string[]>;
  node_results: Record<string, GraphResultItem>;
  attestations: string[];
  result: GraphPayload | null;
  last_seq: number;
}

type NodeStatus =
  | "pending"
  | "running"
  | "waiting"
  | "blocked"
  | "skipped"
  | "done"
  | "not-completed"
  | "failed"
  | "cancelled"
  | "unknown";
```

`PlanTask`, `GraphResultItem`, and `GraphPayload` retain their validated plan and
result JSON shapes rather than being flattened. The server obtains this object
only through `read_strict_events()` and `project_round()`. An inconsistent
authoritative stream fails the detail request with `409 projection_error`; it is
never rendered as a plausible graph.

#### One authoritative node status

`node_status` is the **only** node vocabulary a renderer may switch on. It holds
one entry for every task in `plan.tasks`, so a client never has to invent a status
for a node it cannot find, and it is owned by
`orchestrator.projection.NodeStatus` — `scripts/check-dag-state-contract.py`
reconciles the union above, the `dag-model` `nodeStatusSchema`, and
`@ai-orchestrator/dag-layout`'s `DAG_NODE_STATES` against that Literal.

The other two node vocabularies remain, and relate to it like this:

- `node_states` is the strict fold itself: what the journal recorded, and only for
  nodes it recorded something about. Its five members are a subset of `NodeStatus`
  and win wherever it has an entry. It stays served because it is the audit answer —
  what was *journalled*, with nothing derived on top.
- `RunTelemetry.nodes[]` is the timing and usage index, keyed by node. Its `status`
  is folded from the same journal and agrees with `node_status` for every node it
  holds; it simply holds no entry for a node the journal never recorded.
- `blocked`, `skipped` and `pending` exist only in `node_status`. The scheduler
  derives them and journals nothing, so the server re-derives them from the plan's
  own dependency edges using the scheduler's rule (`plan.UNMET_DEP_STATUSES` /
  `plan.GATED_DEP_STATUSES`) — otherwise every held node reads as `pending` for as
  long as the run is live. A node gated only by a cross-DAG prerequisite reads as
  `pending`: that status lives in another run's journal and this round cannot
  evidence it.
- `unknown` is the honest report of a recorded status outside the vocabulary, never
  a silent fallback onto a neighbouring meaning.

`node_gated_by` names, for each `blocked` or `skipped` node, the **plan node ids**
whose status gates it, in plan order; it omits every other node. It is not
`GraphResultItem.blocked_by`, which names *human action refs* (`node` or
`node/step`) on a settled result — a node view showing what holds a node reads both.

A run's own `state` also uses the word `blocked`, and means something else: the run
is waiting on a **planner** reply (`orchestrator.monitor.run_state`). The two never
share a field — a run's is `state`, a node's is `node_status` — and no renderer may
map one through the other's table.

`RunSummary.node_counts` counts this same derivation over the run's newest round, so
a list row and the graph it opens cannot describe different graphs. A run whose
authoritative stream will not fold degrades to the recorded telemetry statuses.

#### Typed failure and blocker facts

A failed or held node's reason is served typed, not left to be parsed out of prose:

- `NodeTelemetry.failure` (optional) is `{class: FailureClass, detail?: string}` —
  the same classification `RunTelemetry.failure` carries for the run, applied to that
  node's own recorded item, and omitted for a node that did not fail.
- `GraphResultItem` carries `error`, `detail`, `exit_code`, `blocked_by`,
  `waiting_steps`, and `human_actions` for the node it describes. They are optional
  because a node that neither failed nor waited records none of them.

```ts
type FailureClass =
  | "agent"
  | "gate"
  | "checks"
  | "publication"
  | "timeout"
  | "provider"
  | "configuration"
  | "unknown";
```

### Run timeline

`GET /api/v1/runs/{run_id}/timeline` returns one `RunTimeline` for the whole run;
a consumer filters it by `node_id` rather than issuing one request per node. The
server assembles it — clients never fold the journal, history, or the monitor
snapshot themselves.

```ts
interface RunTimeline {
  api_version: 1;
  observed_at: string;
  run_id: string;
  spans: TimelineSpan[];
}

// One interval of recorded work. ended_at is null for work the recorded stream
// never closed, which is what an in-flight run looks like rather than an error.
// parent_id links spans into a tree; a span with no parent is run-level.
// count and total_duration_ms appear only on a "rollup" span.
interface TimelineSpan {
  id: string;
  kind: TimelineSpanKind;
  label: string;
  started_at: string;
  ended_at: string | null;
  events: TimelineEvent[];
  parent_id?: string;
  node_id?: string;
  step_id?: string;
  round?: number;
  status?: string;
  count?: number;
  total_duration_ms?: number;
  reference?: TimelineReference;
}

// One instant recorded inside a span. `kind` is the journal event kind that
// produced it, or "conversation-turn" for a turn, and is an open string.
interface TimelineEvent {
  id: string;
  kind: string;
  at: string;
  node_id?: string;
  step_id?: string;
  round?: number;
  status?: string;
  reference?: TimelineReference;
}

// Where an item's heavy content lives. The payload never inlines a transcript,
// a gate log, or a report body; a consumer fetches only the item it opens.
interface TimelineReference {
  kind: TimelineReferenceKind;
  value: string;
}

type TimelineSpanKind =
  | "round"
  | "node"
  | "step"
  | "dispatch"
  | "verification"
  | "publication"
  | "pr-drafting"
  | "conflict-resolution"
  | "human-wait"
  | "rollup";

type TimelineReferenceKind =
  | "conversation"
  | "gate_log"
  | "worker_report"
  | "oneharness_session"
  | "pr";
```

`orchestrator/timeline.py` owns the fold, from the run journal, the run's
conversations, and the persisted `DetailSnapshot`. Its rules:

- Journal `at` is epoch seconds on disk and is normalized to RFC 3339 UTC here,
  like every other timestamp in this API.
- A span brackets a recorded pair: `node-started`/`node-settled`\|`node-failed`,
  `step-started`/`step-settled`, `verification-started`/`verification-finished`,
  `pr-drafting-started`/`pr-drafting-finished`,
  `conflict-resolution-started`/`conflict-resolution-finished`, and
  `human-waiting`/`human-attested`. A round span opens at the first record
  carrying its round and closes at `round-finished`. A publication span has no
  recorded start, so it opens at the first of `pr-created`, `pr-ready`,
  `pr-checks-observed`, `pr-merged`, `publication-finished`, or
  `publication-failed` and closes on the last two — which remain events inside
  it, unlike the other boundary kinds.
- Every other record becomes an event inside the innermost span still open at its
  own locator, so `pr-drafting-fallback` reads as the reason drafting failed
  rather than as a sibling of the drafting it explains.
- One `dispatch` span per conversation, one `conversation-turn` event per turn. A
  conversation whose `attribution.transportRole` is `llmlint` is nested under the
  dispatch span it ran within rather than emitted beside it; a conversation whose
  attribution names no node attaches to its round, or to the run when it names
  neither.
- High-frequency kinds — those recorded per lock acquisition rather than per graph
  transition, currently `lock-wait` — collapse into one `rollup` span per node
  carrying `count` and `total_duration_ms`, never one item each.
- The `DetailSnapshot` carries no timestamps and so contributes no ordered item.
  It supplies the observed PR `state` as the `status` of a publication span the
  journal has not closed.
- A missing or unreadable history store or monitor snapshot degrades to an empty
  contribution; a corrupt authoritative journal fails the read with `409
  projection_error`, exactly as `RunDetail` does.

Errors use `{"error":{"code":string,"message":string}}`. A missing run is 404
`run_not_found`; a present run with no such transcript is 404
`conversation_not_found`, which a viewer can treat as "still being written" rather
than "stop polling". Invalid query/path input is 422 (`invalid_run_id`,
`invalid_conversation_id`, or `invalid_request`), corrupt persisted input is 409
`projection_error`, and an unexpected read failure is 500 — none of them carrying
filesystem paths or record contents. Codes are open strings: a client must handle an
unrecognized one by status.

## Read-only FastAPI and SSE surface

The server exposes only:

- `GET /healthz` → `{"status":"ok"}` without touching run storage.
- `GET /api/v1/runs`.
- `GET /api/v1/runs/{run_id}?include_conversations={optional}`.
- `GET /api/v1/runs/{run_id}/timeline` for the whole run's ordered spans and
  events.
- `GET /api/v1/runs/{run_id}/conversations/{conversation_id}` for one complete
  conversation when detail responses use summaries.
- `GET /api/v1/events?run_id={optional}&after={optional}` as SSE.

There are no mutation routes, command execution, file paths, arbitrary history
queries, or user-supplied globbing. Run, conversation, and cursor IDs are
validated opaque identifiers and resolved beneath configured roots.

Every read but `/healthz` is blocking work — a walk of the runs root plus an
`oneharness history list` subprocess that reads the whole store — so none of it
runs on the event loop, and one slow read occupies its own request rather than
freezing every other connection and live stream. `RunList` costs **one** history
subprocess however many runs the root holds: that command answers "every session
there is", so a per-run read bought nothing and made the list degrade by about a
second per run ever recorded. The read is shared within one scan and never across
scans — the store is live, so a later request must not be answered from an earlier
one's sessions.

SSE uses `text/event-stream`, `Cache-Control: no-cache`, and heartbeat comments
at least every 15 seconds. Each event has journal sequence or server cursor in
`id`, one of `snapshot`, `run.changed`, `conversation.changed`, or `run.removed`
in `event`, and one compact JSON object in `data`.

Every connection opens with `snapshot` carrying the current `RunList`, including a
reconnect that supplies `Last-Event-ID` or `after`. The server retains no event
history, so it cannot replay what a disconnected client missed; a snapshot is the
only way that client cannot silently keep serving stale state. A supplied cursor
therefore only continues the id sequence — ids stay monotonic across a reconnect
within one process — and an unparseable or negative one is discarded rather than
refused. Cursors are ordered only within one server process.

`run.changed` and `run.removed` are polled from the runs root. `conversation.changed`
is polled from oneharness history on its own slower interval and only when the
request names a single `run_id`, because each poll spawns a real history subprocess.
Backpressure coalesces repeated changes to the same run, never unboundedly queues
them. Clients refetch run detail after a change event; SSE is invalidation, not a
second state model.

The default bind is loopback. Non-loopback binding requires explicit operator
configuration and an authentication middleware supplied by the deployment.
CORS is off unless explicit origins are configured.

### Running it

`just telemetry-server` serves this API over uvicorn against a real runs
directory and stays in the foreground until interrupted:

```sh
just telemetry-server                              # runs/ on http://127.0.0.1:8787
just telemetry-server --runs-dir runs --port 8791
```

`--runs-dir` (default `runs`) is the one configured root every run and
conversation id resolves beneath; the server never writes to it. `--host` and
`--port` default to `127.0.0.1:8787`, and a non-loopback `--host` exits 2 unless
`--allow-nonloopback` is passed to acknowledge that the deployment supplies its
own authentication. `--oneharness-bin` names the history binary the conversation
reads shell out to. `--expose-launcher-session-id` lifts the default redaction
described under "Launch and session provenance".

## Launch and session provenance

The UI may launch from either a Claude Code or Codex session, but it does not
trust process ancestry or guess the launcher from model names. It reads what the
harness itself exports into the session (`launch.detect_launch`), so the ordinary
`just orchestrate` records provenance with no flags and `--launcher` /
`--launcher-session` remain an override. The launcher creates a random 128-bit
`launch_id`, writes a short-lived provenance record outside the repository, and
passes these validated labels to every top-level oneharness invocation:

```ts
interface LaunchProvenance {
  schema_version: 1;
  launch_id: string;
  launcher: "claude-code" | "codex";
  launcher_session_id: string;
  started_at: string;
  repository_identity: string;
}
```

History labels carry `launch_id`, `launcher`, and the existing graph locator
labels `run_id`, `round`, `node`, and optional `step`. `launcher_session_id` is
kept in the protected provenance record rather than history labels because it
may be sensitive. The server joins on `launch_id`, exposes launcher and
`launch_id`, and exposes the launcher session ID only when its configured
redaction policy permits it. Missing/expired provenance yields
`launcher: "unknown"` without changing graph attribution. Nested processes
inherit labels through `orchestrator.labels.merge_labels`; the more-specific
dispatch owns graph locator and semantic-role values.

As landed here, `just orchestrate` (`orchestrator.launch` +
`dispatch.launch_orchestrator`) mints the `launch_id`, writes the
`LaunchProvenance` record to `$XDG_STATE_HOME/ai-orchestrator/launches/<launch_id>.json`
(only for a known launcher with a session id), and stamps `launch_id` + `launcher`
(plus `run_id`) onto the orchestrator's `ONEHARNESS_HISTORY_LABELS`. Every nested
`run_onejudge` dispatch merges those inherited labels under its own graph locators,
so each worker/judge/orchestrator conversation carries the join labels. The run
directory records only the non-sensitive `launch_id` (in `launch.json` under a
`launch: {launch_id}` object); the sensitive session id never enters the repository.
The server resolves a run's `RunLaunch` by reading that `launch_id` and joining it to
the provenance record — reporting `launcher: "unknown"` when the record is missing,
malformed, or older than its short-lived max age — and includes
`launcher_session_id` only when started with `--expose-launcher-session-id`
(`create_app(expose_launcher_session_id=True)`), which is off by default.

`role` in current oneharness history is a transport-party role:
`agent`, `judge`, or `llmlint`. It remains untouched for telemetry
compatibility. A new `agent_role` label carries the semantic taxonomy below,
and `persona` carries the dispatched persona where applicable. This separation
prevents a pr-author worker from disappearing from worker timing.

## Agent and subagent conversations

Every labeled oneharness session maps to one
`@oneharness/ui` `Conversation`. The authoritative version is `0.1.0` at immutable
commit `5a2b48908ef84900a47eaccd7f616327b2997b6c`; the exact exported transcript
types are checked in at `oneharness-ui-contract.d.ts` and their pin is validated
by `scripts/check-oneharness-ui-contract.sh`. Consumers import the package type.
Because that type has no metadata or parent field, the API uses this envelope:

```ts
interface DagConversation {
  conversation: import("@oneharness/ui").Conversation;
  attribution: {
    runId?: string;
    round?: number;
    nodeId?: string;
    stepId?: string;
    launchId?: string;
    launcher?: "claude-code" | "codex" | "unknown";
    transportRole: "agent" | "judge" | "llmlint";
    agentRole: AgentRole;
    parentConversationId?: string;
    persona?: string;
    finishedAt?: string | null;
    inferred?: true;
    timing?: Timing;
  };
}
type AgentRole =
  | "orchestrator"
  | "worker"
  | "judge"
  | "check-in"
  | "pr-author";
```

The package has turns, not a message union. Records map in source order:

| Target | Exact transformation |
| --- | --- |
| `id`, `name`, `project`, `startedAt` | First record `session`, `name`, `project`, `timestamp`. |
| `harnesses` | Ordered de-duplicated record harnesses. |
| `state` | Last status: `ok→completed`; `nonzero`/`spawn-error→failed`; `timeout`/`skipped`/`planned→stopped`; otherwise unchanged. |
| `canContinue` | Last record has native `session_id` and is not planned, skipped, or spawn-error. |
| `turn.id` | ``${record.session}-${zeroBasedIndex}``. |
| `turn.user`, `assistant` | `prompt`; `text ?? null`. |
| `turn.reasoning` | First non-empty `reasoning` or `thinking`; strings unchanged, structured values two-space JSON, else null. |
| `turn.harness`, `model`, `timestamp` | `harness`, `model ?? null`, `timestamp`. |
| `turn.status`, `failureKind` | Status mapping above; `failure_kind ?? null`. |
| `turn.usage` | Rename snake-case keys to `inputTokens`, `outputTokens`, `cacheReadTokens`, `cacheWriteTokens`, `costUsd`; preserve number/null and omit absent properties. |
| `turn.tools` | Every event becomes required `index`/`kind` plus `input`, `name`, `output` only when present. Unsupported kinds remain visible generic tool events. |
| `turn.unknown` | Every unconsumed record key and JSON value; system content without a public field stays here. |

Timing, graph labels, launch provenance, semantic role, persona, finish time, and
parent linkage live only in `attribution`; no invented Conversation fields are
allowed. Native onejudge linkage supplies `parentConversationId`; fallback
requires exact run/node/step labels and interval containment. Missing optional
values are omitted, except an observed active finish is `null`. Secrets, paths,
environment, and unrecorded reasoning are never serialized.

Role assignment is deterministic:

| Process purpose | Required labels | Conversation mapping |
| --- | --- | --- |
| Planner-facing orchestrator onejudge | `role=agent`, `agent_role=orchestrator` | One node-external orchestrator Conversation for each worker-side session. Its judge-side sessions are separate `agentRole=judge` children. |
| Ordinary direct/lifecycle/step dispatch | `role=agent`, `agent_role=worker`, `persona=…` | Worker Conversation attached to `node` and optional `step`; title is the persona plus node label. |
| Simulated-user supervisor/evaluator | `role=judge`, `agent_role=judge` | Judge Conversation attached to the worker/orchestrator session via native onejudge linkage (`parentConversationId`); never merged into worker messages. |
| Periodic planner update dispatch | `role=agent`, `agent_role=check-in` | Check-in subagent Conversation attached to its node/workstream and parent orchestrator; it does not count as the node's implementation worker. |
| Diff-derived PR description dispatch | `role=agent`, `agent_role=pr-author`, `persona=pr-author` | PR-author subagent Conversation attached to the lifecycle node and drafting journal interval. |

Nested llmlint remains `transportRole=llmlint`; it is verification activity,
not one of the five agent roles. It appears as a child conversation with
`agentRole=worker` only for grouping, a visible `LINT` subtype, and never counts
as a worker turn. For legacy data lacking `agent_role`, the adapter maps
`role=judge` to judge, session/persona `pr-author` to pr-author, known check-in
session labels to check-in, the dedicated orchestrator node/session to
orchestrator, and remaining `role=agent`/`llmlint` to worker. It marks this
classification `inferred: true`; new writers must emit labels and cannot rely on
names.

Native onejudge session linkage is the parent/turn authority. Fallback linking
uses exact `run_id` + `node` + optional `step` labels and time containment,
never substring matching. Unlinked sessions are shown in a run-level
“Unassigned” group rather than discarded.

## Shared DAG layout view model

`packages/dag-layout` is the only graph geometry contract. It accepts
`DagLayoutInput {nodes, edges}` and returns `DagLayout {width,height,nodes,edges}`
as exported from `@ai-orchestrator/dag-layout`. IDs are stable plan IDs; an edge
ID is stable for its `(source,target,dependency-kind)` tuple. Node input carries
only renderer-neutral label, kind, and state. UI-only selection, hover, URLs,
icons, colors, and Conversation objects stay outside the layout package.

Coordinates and dimensions are non-negative integer CSS/SVG pixels. The origin
is top-left. Each positioned node is an axis-aligned rectangle; an edge is an
ordered polyline from source boundary to target boundary. Layout is a pure,
deterministic function: inputs are sorted by ID before tie-breaking, dependency
rank flows left-to-right, disconnected components use stable ID order, and the
same input produces byte-equivalent JSON on browser and CLI runtimes. Duplicate
IDs, dangling edges, self-edges, cycles, non-finite dimensions, and unsupported
states are input errors.

The web graph renders the returned model with accessible node buttons and an
adjacent keyboard-navigable list. The CLI renderer converts the same model to
SVG with escaped text and stable element order. Neither renderer may run its own
layout algorithm. Golden fixtures must feed one input through the package and
assert both renderers reference identical coordinates and routes.

## Nx workspace contract

Nx projects expose uniform `build`, `lint`, `test`, `typecheck`, and `format`
targets. Python targets call uv/ruff/mypy/pytest; TypeScript targets call
TypeScript, ESLint with `@nx/enforce-module-boundaries`, Biome, and Bun.
`scope:shared` libraries may depend only on `scope:shared` libraries. Apps may
consume packages but packages never consume apps.

All root quality recipes enter Nx through `scripts/nx.sh`. It derives a stable
cache key from repository identity and places `NX_CACHE_DIRECTORY` under the
user cache directory, so linked clones and lifecycle worktrees share artifacts
without writing `.nx/` into either checkout. Nx content hashes include project
inputs and shared lock/config inputs; a clean source or config change therefore
cannot replay a stale success. `.nx/`, `node_modules/`, and build output are
ignored as a second line of defense.
