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
telemetry index `schema_version: 6`; this API version does not replace or
renumber that contract.

## Read model

### Run list and detail

`GET /api/v1/runs?include_settled=false` returns:

```ts
interface RunList {
  api_version: 1;
  telemetry_schema_version: 6;
  observed_at: string;
  runs: RunSummary[];
}

interface RunSummary {
  run_id: string;
  state: string;
  phase: string;
  last_event: string;
  last_progress_at?: number; // existing epoch-seconds value
  telemetry_quality: "complete" | "partial" | "legacy";
  timing: Timing;
  node_counts: Record<string, number>;
}
```

Runs are ordered by most recent progress descending, then `run_id` ascending.
`include_settled` defaults to false, matching `just telemetry`; true matches
`just telemetry --all`.

`GET /api/v1/runs/{run_id}` returns a `RunDetail`:

```ts
interface RunDetail {
  api_version: 1;
  telemetry_schema_version: 6;
  observed_at: string;
  run: RunTelemetry;
  rounds: Round[];
  conversations: NodeConversations[];
}
```

`RunTelemetry` is exactly `RunTelemetry.record()` from
`orchestrator/telemetry.py`: required `run_id`, `state`, `phase`, `last_event`,
`timing`, `nodes`, `usage`, `telemetry_quality`, `sources`, `node_work_ms`,
`turns`, and `lint`; optional `last_progress_at`, `providers`, `failure`, and
`check_rollup`. A `NodeTelemetry` is exactly `NodeTelemetry.record()`: required
`node`, `status`, `sessions`, `turns`, and `lint`; optional `outcome`, `branch`,
`comparison_remote`, `comparison_base`, `checkpoint`, `commit`,
`retry_lineage`, `gate_attestation`, `timing`, `usage`, and `tool_commands`.

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

Unknown usage is `null`, never zero. Timing categories and precedence, quality,
sources, legacy aliases, node-work sums, metrics, and llmlint retry cohorts have
exactly the meanings in `telemetry-model.md`. The API calls the same collector;
it must not independently recalculate them.

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
  node_results: Record<string, GraphResultItem>;
  attestations: string[];
  result: GraphPayload | null;
  last_seq: number;
}
```

`PlanTask`, `GraphResultItem`, and `GraphPayload` retain their validated plan and
result JSON shapes rather than being flattened. The server obtains this object
only through `read_strict_events()` and `project_round()`. An inconsistent
authoritative stream fails the detail request with `409 projection_error`; it is
never rendered as a plausible graph. Pending nodes are plan tasks absent from
`node_states`.

Errors use `{"error":{"code":string,"message":string}}`. A missing run is 404,
invalid query/path input is 422, corrupt persisted input is 409, and an
unexpected read failure is 500 without filesystem paths or record contents.

## Read-only FastAPI and SSE surface

The server exposes only:

- `GET /healthz` → `{"status":"ok"}` without touching run storage.
- `GET /api/v1/runs`.
- `GET /api/v1/runs/{run_id}`.
- `GET /api/v1/runs/{run_id}/conversations/{conversation_id}` for one complete
  conversation when detail responses use summaries.
- `GET /api/v1/events?run_id={optional}&after={optional}` as SSE.

There are no mutation routes, command execution, file paths, arbitrary history
queries, or user-supplied globbing. Run, conversation, and cursor IDs are
validated opaque identifiers and resolved beneath configured roots.

SSE uses `text/event-stream`, `Cache-Control: no-cache`, and heartbeat comments
at least every 15 seconds. Each event has journal sequence or server cursor in
`id`, one of `snapshot`, `run.changed`, `conversation.changed`, or `run.removed`
in `event`, and one compact JSON object in `data`. On connection or an expired
`Last-Event-ID`, the server sends `snapshot` with the current `RunList`. A valid
`Last-Event-ID` resumes strictly after that cursor. Cursors are ordered only
within one server process; clients must accept a snapshot after restart.
Backpressure coalesces repeated changes to the same run, never unboundedly
queues them. Clients refetch run detail after a change event; SSE is
invalidation, not a second state model.

The default bind is loopback. Non-loopback binding requires explicit operator
configuration and an authentication middleware supplied by the deployment.
CORS is off unless explicit origins are configured.

## Launch and session provenance

The UI may launch from either a Claude Code or Codex session, but it does not
trust process ancestry or guess the launcher from model names. The launcher
creates a random 128-bit `launch_id`, writes a short-lived provenance record
outside the repository, and passes these validated labels to every top-level
oneharness invocation:

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
