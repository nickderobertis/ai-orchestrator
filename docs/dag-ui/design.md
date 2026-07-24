# DAG visualization system contract

This document is the authoritative integration contract for every orchestration
DAG view. It fixes the server resources, provenance rules, and framework-neutral
layout model consumed by both the web graph and CLI SVG renderer. The underlying
timing semantics remain authoritative in
[`../telemetry-model.md`](../telemetry-model.md); this contract does not redefine
or aggregate them differently.

## Versioning and compatibility

The read API is version 1 and all JSON uses `snake_case`. Readers ignore unknown
fields. New optional fields are omitted when unavailable; they are never emitted
as `null` unless `null` has the explicit observed-but-unfinished meaning below.
Durations, usage unknowns, RFC 3339 timestamps, and concurrent interval
aggregation follow the telemetry model. Integers are JSON numbers and IDs are
opaque strings. A breaking field removal, semantic change, or enum narrowing
requires a new `/api/vN` prefix. SSE event payloads use the same versioned
resource shapes as ordinary responses.

## Read API types

The Python server serializes existing domain records rather than maintaining a
second telemetry model:

- `RunTelemetry.record()` is `RunTelemetryJson`.
- `NodeTelemetry.record()` is `NodeTelemetryJson`.
- `RoundProjection` is converted by the boundary adapter to
  `RoundProjectionJson`; its typed Python dictionaries are copied, not reshaped.

In TypeScript notation, the stable envelope and projection are:

```ts
type RunId = string;
type NodeState = "running" | "done" | "failed" | "waiting" | "cancelled";

interface ApiEnvelope<T> {
  schema_version: 1;
  generated_at: string;
  data: T;
}

interface RoundProjectionJson {
  run_id: RunId;
  round: number;
  plan: {
    tasks: Record<string, unknown>[];
    schema_version?: number;
    concurrency?: number;
    name?: string;
  };
  node_states: Record<string, NodeState>;
  node_results: Record<string, Record<string, unknown>>;
  attestations: string[];
  result?: Record<string, unknown>;
  last_seq: number;
}
```

`result` is omitted when the round has not finished. `node_states` is sparse:
planned nodes that have not started have no entry. The task definitions inside
`plan.tasks` are the source of node kind, dependencies, lifecycle steps, persona,
and task prose. Consumers must not infer dependencies from execution order.

`RunTelemetryJson` has required `run_id`, `state`, `phase`, `last_event`,
`timing`, `nodes`, `usage`, `telemetry_quality`, `sources`, `node_work_ms`,
`turns`, and `lint`. It optionally has `last_progress_at`, `providers`,
`failure`, and `check_rollup`. `NodeTelemetryJson` requires `node`, `status`,
`sessions`, `turns`, and `lint`; it optionally has `outcome`, `branch`,
`comparison_remote`, `comparison_base`, `checkpoint`, `commit`,
`retry_lineage`, `gate_attestation`, `timing`, `usage`, and `tool_commands`.
The exact timing, fractions, usage, quality, source, session-link, provider,
failure, and node-work members are schema-v6 fields defined in the telemetry
model and emitted by `orchestrator/telemetry.py`. The API must pass unknown usage
through as `null`, preserve a linked session's `finished_at: null`, and must not
turn missing measurements into zero.

The composite detail resource is:

```ts
interface RunDetail {
  telemetry: RunTelemetryJson;
  projection: RoundProjectionJson;
}
```

Telemetry describes observed execution and may degrade for legacy runs.
Projection is the strict authoritative journal fold and fails closed. A corrupt
projection therefore yields an error response; the server must not present a
tolerant monitor reconstruction as authoritative graph state.

## Read-only FastAPI and SSE surface

The server binds loopback by default. It accepts no write methods, file paths,
shell fragments, graph edits, attestations, or launch commands.

| Method | Path | Result |
| --- | --- | --- |
| `GET` | `/api/v1/runs` | `ApiEnvelope<RunTelemetryJson[]>`, with the same settled-run inclusion semantics as `telemetry --all` |
| `GET` | `/api/v1/runs/{run_id}` | `ApiEnvelope<RunDetail>` for the latest projected round |
| `GET` | `/api/v1/runs/{run_id}/rounds/{round}` | `ApiEnvelope<RoundProjectionJson>` |
| `GET` | `/api/v1/runs/{run_id}/events` | SSE replay/live stream |
| `GET` | `/api/v1/runs/{run_id}/conversations` | `ApiEnvelope<Conversation[]>` |
| `GET` | `/healthz` | `{"status":"ok"}` after run/history roots are readable |

Unknown run/round IDs return `404`; invalid opaque ID syntax returns `422`;
strict projection failure returns `409` with
`{"error":{"code":"projection_invalid","message":"..."}}`; unreadable storage is
`503`. Other errors use the same `error.code`/`error.message` envelope and never
include host paths or tracebacks. Run IDs are validated before joining them to a
configured root, and the resolved path must remain beneath that root.

SSE responses use `Content-Type: text/event-stream`, `Cache-Control: no-cache`,
and `X-Accel-Buffering: no`. Each frame has the journal sequence as `id`, event
name `projection`, and an `ApiEnvelope<RoundProjectionJson>` JSON `data` line.
The first frame is a complete current snapshot. Later frames are complete,
strictly projected snapshots, so reconnecting clients need no patch state.
`Last-Event-ID` resumes after that journal sequence; if it is absent, stale, or
beyond the current sequence, the server sends the current snapshot. A
`keepalive` comment is sent every 15 seconds without advancing the ID. File
watching is bounded polling, disconnect-aware, and never holds a journal lock.
Terminal runs send one terminal snapshot and close.

## Launch-session provenance

Provenance answers which interactive Claude or Codex session launched a run; it
is separate from onejudge worker/judge sessions. The launcher captures only
explicit, trustworthy environment values:

```ts
interface LaunchProvenance {
  launcher: "claude" | "codex" | "unknown";
  session_id?: string;
  captured_at: string;
}
```

At `just orchestrate`/`run-plan` entry, the process checks
`CLAUDE_SESSION_ID` and `CODEX_SESSION_ID`. Exactly one non-empty value selects
that launcher and ID. Neither or both produce `launcher: "unknown"` with no ID;
names, process trees, terminals, and history recency are never guessed. The
record is written once into the run's initial durable metadata/journal event and
is immutable across orchestrator restarts, worker dispatches, retries, and
rounds. Child processes receive `ORCHESTRATOR_LAUNCHER` and
`ORCHESTRATOR_LAUNCH_SESSION_ID` only for correlation; they cannot overwrite the
durable record. API responses omit `session_id` when unknown. IDs are opaque,
never used as paths, and redacted from ordinary logs.

## `dag-layout` view model

`packages/dag-layout` will own pure TypeScript types and deterministic conversion
from `RunDetail`; it imports no React, DOM, canvas, terminal, filesystem, or
server modules. Both renderers consume this exact output:

```ts
type DagNodeStatus =
  | "planned" | "running" | "waiting" | "done" | "failed" | "cancelled";

interface DagLayout {
  schema_version: 1;
  run_id: string;
  round: number;
  width: number;
  height: number;
  nodes: DagLayoutNode[];
  edges: DagLayoutEdge[];
  groups: DagLayoutGroup[];
}

interface DagLayoutNode {
  id: string;
  label: string;
  kind: "agent" | "lifecycle" | "human";
  status: DagNodeStatus;
  x: number; y: number; width: number; height: number;
  layer: number; order: number;
  telemetry?: {
    wall_ms: number;
    telemetry_quality: "complete" | "partial" | "legacy";
  };
}

interface DagLayoutEdge {
  id: string;
  from: string;
  to: string;
  points: Array<{ x: number; y: number }>;
}

interface DagLayoutGroup {
  id: string;
  label: string;
  node_ids: string[];
  x: number; y: number; width: number; height: number;
}
```

Coordinates are integer CSS/SVG pixels with origin at top-left. Nodes use fixed
theme-independent geometry supplied in layout options. Dependency edges point
from prerequisite to dependent. Layers are longest-path ranks from roots;
within a layer, stable order is task-plan order then node ID. Edge IDs are
`from + "->" + to`; parallel duplicates are invalid. Status comes from
`node_states`, falling back to `planned`; terminal projection state wins over
telemetry lag. Telemetry is joined by exact node ID and omitted when no timing
exists. Groups represent lifecycle nodes with visible steps only; empty groups
are omitted. The converter rejects duplicate IDs, missing dependency targets,
cycles, non-finite geometry, and unsupported schema versions. Given identical
JSON and options it must return byte-equivalent JSON. Renderers choose colors,
fonts, focus behavior, and accessible descriptions but never recompute topology,
ordering, routes, or status.

## oneharness conversations

`GET .../conversations` resolves only `NodeTelemetryJson.sessions` linkage and
loads the matching normalized oneharness history record. Labels and name
heuristics are legacy telemetry fallbacks, not conversation identity. The
adapter targets the installed `@oneharness/ui` exported `Conversation` type
directly, with a compile-time `satisfies Conversation` check so upstream shape
changes fail `typecheck`.

Mapping rules are:

| Orchestrator/oneharness source | Conversation field |
| --- | --- |
| `history_id`, else `session_id` | conversation `id` |
| linked role `agent` / `judge` / `llmlint` | metadata `role`; display labels `WORKER` / `JUDGE` / `LLMLINT` |
| node ID | metadata `node_id` and title prefix |
| `turn_index` | metadata `turn_index`; missing stays omitted |
| history `started_at`, `finished_at` | conversation time bounds; observed unfinished remains `null` |
| normalized user/model content events | ordered user/assistant messages |
| normalized tool call/result events | the UI package's tool-call message/part shape, joined by `tool_call_id` |
| provider, harness, model, usage | conversation metadata when present |

Unknown history event kinds are ignored while relative order of recognized
events is preserved. Empty textual events are omitted. Tool results without a
matching call remain visible as orphan tool results rather than being attached
to the wrong call. Content is treated as untrusted display text; the adapter
does not emit raw HTML. A missing or unreadable linked history produces a
conversation entry with identity/role metadata and an explicit unavailable
state, not a fabricated transcript. Conversation ordering is node plan order,
then role order `agent`, `judge`, `llmlint`, then `turn_index`, then session ID.

## Toolchain decision and deferred work

The foundation pins Nx 23.1.0, Biome 2.5.5, typescript-eslint 8.65.0, and the
latest supported TypeScript 5.x, 5.9.3. TypeScript 7 is deliberately excluded
because the standard typescript-eslint parser required by Nx module-boundary
lint does not support it.

**Deferred:** adopt TypeScript 7 once typescript-eslint and Nx officially support
it, retaining the standard `@nx/enforce-module-boundaries` rule and upgrading
the lockfile, parser, lint plugin, compiler, and this decision together.

## Shared computation cache

`scripts/nx.sh` is the only Nx entry point. It derives a repository-identity key
from the normalized origin and exports `NX_CACHE_DIRECTORY` beneath
`$XDG_CACHE_HOME` (or a temp-cache fallback), so worktrees and linked clones of
one origin share artifacts without committing machine paths. Nx hashes declared
project inputs, lockfiles, commands, and tool versions before replay. Targets
must declare every output and must never read undeclared generated state.
Changing a source, configuration, lockfile, command, or tool version therefore
causes a real execution; `scripts/check-nx-cache.sh` proves replay in a linked
clone. Clean-build diagnosis uses `NX_SKIP_NX_CACHE=true`, never deletion of a
shared cache.
