# DAG visualization contract

This document is the authoritative cross-language contract for the DAG
visualization system. Python owns persisted run truth and the read API.
TypeScript validates that API at its boundary. Both the interactive graph and
the CLI SVG renderer consume the same framework-independent layout result.

The contract is additive over the current run ledger, strict projection, and
telemetry index. It does not authorize the UI server to mutate a graph.

## Sources of truth

The server joins, but does not reinterpret, these existing Python models:

- `RoundProjection` in `orchestrator/projection.py` owns the graph definition,
  current `NodeState`, settled `GraphResultItem`, human attestations, projected
  result, and last durable event sequence for one round.
- `RunTelemetry.record()` in `orchestrator/telemetry.py` owns the run and node
  timing, usage, session linkage, phase, provider, failure, check, branch, and
  publication fields. Its top-level wire schema is telemetry index version 6,
  as specified in `docs/telemetry-model.md`.
- `launch.json`, round `plan.json` files, the authoritative `events.jsonl`, and
  round result artifacts remain the persisted records. API responses are
  disposable projections and are never written back.

If two sources overlap, graph topology and task definitions come from the strict
round projection; operational measurements and session links come from
telemetry. A consumer must retain unknown optional fields and must distinguish
an omitted unknown value from measured zero. Empty optional arrays and objects
are omitted, except existing Python records that deliberately serialize them
(notably `nodes` and `sessions`).

## Read API

The implementation is a FastAPI application mounted under `/api/v1`. All JSON
uses UTF-8, RFC 3339 UTC timestamps, integer milliseconds, and the exact
snake-case field names emitted by Python. Run and node identifiers are opaque
strings. Requests containing a run id or round that cannot be resolved return
`404`; malformed identifiers return `422`; a corrupt durable projection returns
`409` with `{"code":"projection_invalid","detail":"..."}` rather than a partial
graph.

The surface is read-only:

| Method and path | Response |
| --- | --- |
| `GET /api/v1/runs` | `RunSummary[]`, newest first; query parameters `state`, `launcher_session_id`, `cursor`, and `limit` are optional. |
| `GET /api/v1/runs/{run_id}` | One `RunDetail` including launch provenance, round summaries, current strict graph, and `RunTelemetry.record()`. |
| `GET /api/v1/runs/{run_id}/rounds/{round}` | One `RoundDetail` from `RoundProjection`, including definitions, states, results, attestations, and `last_seq`. |
| `GET /api/v1/runs/{run_id}/nodes/{node_id}` | One `NodeDetail` joining the current node definition, projected state/result, node telemetry, artifacts, and transcript locators. |
| `GET /api/v1/runs/{run_id}/events` | Server-Sent Events for this run. `Last-Event-ID` resumes after a durable sequence. |
| `GET /healthz` | `{"status":"ok"}` after the runs root can be read. |

`RunSummary` has required `run_id`, `state`, `phase`, `last_event`, `round`, and
`launch`; it includes `last_progress_at`, `failure`, and `check_rollup` when
known. `RunDetail` adds:

```json
{
  "run_id": "dag-visualization-ui",
  "launch": {
    "session_id": "opaque-launcher-id",
    "client": "codex",
    "started_at": "2026-07-24T12:00:00Z"
  },
  "rounds": [{"round": 2, "state": "running"}],
  "graph": {
    "round": 2,
    "plan": {"schema_version": 4, "name": "DAG UI", "tasks": []},
    "node_states": {"foundation": "running"},
    "node_results": {},
    "attestations": [],
    "last_seq": 41
  },
  "telemetry": {}
}
```

The `telemetry` member is exactly `RunTelemetry.record()`, without renaming or
unit conversion. Therefore it carries required `run_id`, `state`, `phase`,
`last_event`, `timing`, `nodes`, `usage`, `telemetry_quality`, `sources`,
`node_work_ms`, `turns`, and `lint`; optional fields follow the Python
serializer. Each node telemetry entry requires `node`, `status`, `sessions`,
`turns`, and `lint`, with the optional branch, commit, gate, timing, usage,
retry, and outcome fields documented by the model.

`RoundDetail.graph.plan.tasks` preserves validated plan node definitions. The
client derives edges from each task's `deps`; it does not infer dependencies
from scheduling order. `node_states` accepts only the current projection values
`running`, `done`, `failed`, `waiting`, and `cancelled`. A task absent from
`node_states` is pending, not unknown. The optional projected `result` is
included only after the round has a result.

`NodeDetail` includes required `run_id`, `round`, `definition`, and `state`.
Optional `result`, `telemetry`, `artifacts`, and `transcripts` are omitted when
unavailable. Artifact values are server-generated opaque download or log
locators; filesystem paths are never accepted from the browser as input.

### SSE

The server tails the authoritative journal and emits only committed information.
Each event has MIME type `text/event-stream`, `id` equal to the decimal durable
journal sequence, `event: projection`, and JSON data:

```json
{
  "run_id": "dag-visualization-ui",
  "seq": 42,
  "round": 2,
  "changed": ["graph", "telemetry"],
  "observed_at": "2026-07-24T12:01:02Z"
}
```

The payload is an invalidation notice, not a second model. On receipt, clients
refetch the named resources. This keeps replay and live reads identical.
Sequence ids are strictly increasing within a run. The server sends an SSE
comment heartbeat at least every 15 seconds, honors `Last-Event-ID`, and closes
with `event: reset` when the requested id predates retained history. Clients
then refetch the run and reconnect without a last id. Slow consumers may be
coalesced to the newest invalidation because the GET resources are authoritative.
No endpoint supports POST, PUT, PATCH, DELETE, attestation, planner verdicts, or
live graph edits.

## Launching-session provenance

Worker and judge labels identify sessions *inside* a run; they do not identify
the Claude or Codex session that launched `just orchestrate`. The launcher must
provide these environment values at orchestration launch:

- `AI_ORCHESTRATOR_LAUNCH_SESSION_ID`: opaque native session id.
- `AI_ORCHESTRATOR_LAUNCH_CLIENT`: `claude` or `codex`.
- `AI_ORCHESTRATOR_LAUNCH_STARTED_AT`: optional RFC 3339 UTC timestamp.

The orchestrator validates and copies them once into `launch.json` under an
optional `launcher` object with `session_id`, `client`, and optional
`started_at`. It never reads mutable process environment again for that run.
Older runs omit `launcher` and serialize API `launch` as
`{"session_id":"unattributed:<run_id>","client":"unknown"}`. The synthetic id is
stable and must not be merged with a real session later. Navigation groups runs
by the pair `(client, session_id)`, newest launcher group first, then runs newest
first. Session ids are displayed only as short opaque labels and are never used
as authorization tokens.

## Shared layout contract

`packages/dag-layout` exposes a pure, framework-independent function:

```ts
layoutDag(input: DagLayoutInput, options?: DagLayoutOptions): DagViewModel
```

`DagLayoutInput` contains `nodes` and `edges`. A node has `id`, ordered `label`
lines, `state`, optional `kind`, and optional semantic `badges`. An edge has a
stable `id`, `source`, `target`, and optional `state`. Inputs are sorted by id
before layout. Options have explicit defaults: top-to-bottom direction, 32 px
rank spacing, 24 px node spacing, 16 px padding, and integer coordinates.

`DagViewModel` contains:

- `width`, `height`, and `viewBox`;
- `nodes[]` with `id`, integer `x`, `y`, `width`, `height`, anchor points,
  original semantic fields, and presentation tokens;
- `edges[]` with `id`, `source`, `target`, an ordered integer point list,
  optional label position, and presentation tokens;
- `tokens`, a finite semantic palette keyed by
  `pending`, `running`, `waiting`, `done`, `failed`, and `cancelled`.

The layout package emits semantic tokens such as `node.failed.border`, never CSS
classes, React elements, SVG markup, or terminal color codes. Status precedence
for a node is `failed`, `cancelled`, `waiting`, `running`, `done`, then
`pending`. Sizing uses a package-owned deterministic text metric rather than DOM
measurement. The same input, options, package version, and layout-engine version
must produce byte-equivalent canonical JSON on every platform.

React Flow adapts node rectangles and routed points from this view model without
running its own layout. The CLI SVG renderer consumes the same values and tokens
without importing React or a browser DOM. Both renderers must test the same
checked-in canonical view-model fixture; renderer-specific snapshots alone do
not establish parity.

## oneharness transcript mapping

The transcript join starts from `NodeTelemetry.sessions`, never a session-name
guess when native linkage exists. Each link carries `session_id`, `history_id`,
`role` (`agent`, `judge`, or `llmlint`), and `turn_index`. Resolve
`history_id` through the oneharness history reader and preserve its event order.
For legacy runs only, use the existing fallback in `orchestrator/history.py`:
match `labels.run_id`, `labels.node`, and optional `labels.step`; classify
`labels.role`, then known llmlint prompt/name signatures, then judge prefixes,
with the remainder treated as agent.

The adapter to the published `@oneharness/ui` presentational `Conversation`
shape follows this mapping:

| Orchestrator/oneharness value | Conversation value |
| --- | --- |
| run id + node id | stable conversation id |
| node task title or id | conversation title |
| session `started` / record timestamps | conversation/turn timestamps |
| `role: agent` | participant `worker`, shown as WORKER |
| `role: judge` | participant `judge`, shown as JUDGE |
| `role: llmlint` | participant `llmlint`, shown as LLMLINT |
| `turn_index` | primary turn ordering key |
| normalized prompt/user content | request message |
| normalized assistant text/reasoning | response message |
| tool-call and tool-result events | ordered tool parts on that response |
| record status/error | turn status and error presentation |
| usage and timing | optional turn metadata |

A role is presentation metadata, not a chat-authority claim. Sort by
`turn_index`, then session start, then session id for deterministic legacy ties.
Unknown event kinds become an `unsupported` part carrying their JSON value; they
must not be discarded. Markdown is rendered as untrusted content: raw HTML is
disabled, links use safe protocols, and tool JSON is escaped. Missing history
produces an unavailable transcript locator with a reason, not an empty
conversation that falsely implies no turns.

## Toolchain and project rules

The workspace uses Bun 1.3.14, Nx 23.1.0, Biome 2.5.5, ESLint 10.7.0,
typescript-eslint 8.65.0, and TypeScript 6.0.3. TypeScript 6.0.3 is the newest
stable compiler inside typescript-eslint's supported `>=4.8.4 <6.1.0` range.
TypeScript 7 is intentionally deferred because the parser used by Nx
`@nx/enforce-module-boundaries` excludes it.

Every Python or TypeScript project declares local `build`, `lint`, `test`, and
`typecheck` targets where applicable. TypeScript projects carry one `scope:*`
tag and one `type:*` tag so `@nx/enforce-module-boundaries` can enforce layers.
Root `just` recipes are stable aliases over Nx affected or run-many execution;
project-specific loops do not belong in the command surface.

Nx cacheable targets declare inputs and outputs. `scripts/nx.sh` points linked
worktrees at the Git common directory (or the lifecycle-provided
`ORCHESTRATOR_CACHE_DIR`) so isolated execution checkouts reuse artifacts.
`just check` first runs every build with `--skip-nx-cache`, then runs the
affected quality targets. `just prove-nx-cache` creates two linked worktrees,
proves the cache-bypassed build, and requires the second worktree to restore the
same build from their shared cache. Thus cache reuse cannot turn a broken clean
build green.
