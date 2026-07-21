# Telemetry model

<!-- llmlint: ignore[no_redundant_instruction_pointers] The task explicitly requires the
spec to cross-link the operator guide so readers entering here do not mistake it for a how-to. -->
For operator commands, example output, and a diagnostic workflow, see
[`telemetry.md`](telemetry.md). This document remains the data contract.

This document is the target cross-layer contract for explaining where a
onejudge session's wall time goes. It is an implementation specification, not a
description of the current schema. Wall time is the primary signal. Tokens,
cache activity, and `cost_usd` are adjacent signals for capacity and spend; they
must never substitute for elapsed-time measurements.

## Questions the model must answer

For every graph node, and for the run as a whole, telemetry must answer:

- How much wall time, and what fraction of wall time, was agent-model latency?
- How much and what fraction was judge/supervisor-model latency?
- How much and what fraction was tool execution?
- How much and what fraction was idle/orchestration time?
- How many input, output, cache-read, and cache-write tokens were consumed, and
  what was their `cost_usd`, separately for the agent, judge, and llmlint?
- Which values are measured from the new contracts, which are derived from
  legacy data, and how much wall time remains unattributed?

The categories are mutually exclusive on a single node's wall-clock timeline:

`wall_ms = agent_model_ms + judge_model_ms + tool_ms + idle_orchestration_ms`

`idle_orchestration_ms` is the non-negative remainder, not a synonym for a
known wait state. It includes onejudge loop overhead, provider startup, process
handoffs, queueing, and gaps that upstream cannot yet classify. Consumers must
also expose `unattributed_ms`: it equals `idle_orchestration_ms` when any input
was inferred from a legacy field, and is zero only when interval-complete data
proves the classification. A category's fraction is its milliseconds divided by
`wall_ms`; for a zero-duration node all fractions are `0.0`.

A run can execute nodes concurrently. Run wall time is therefore the interval
from the first run event to terminal settlement (or observation time while
active), and must not be the sum of node wall times. For each category, the run
view unions that category's node intervals and clips them to the run interval.
If differently classified intervals overlap, precedence is `tool`,
`judge_model`, then `agent_model`; this deterministic presentation rule prevents
fractions above 100% and does not alter the per-node work totals. The remaining
run interval is `idle_orchestration_ms`. The view must additionally expose raw
`node_work_ms` sums so parallel work is visible rather than discarded.

## Layer contracts

All durations are integer monotonic elapsed milliseconds. A producer computes an
interval's duration from its own monotonic clock; monotonic values from different
processes are never compared. UTC timestamps are RFC 3339 strings used for
correlation and cross-process interval placement, never to recompute a producer's
duration. Usage counters are non-negative integers and `cost_usd` is a finite,
non-negative JSON number in US dollars. Zero means the producer measured none.

Every new field is optional to readers. Writers of the bumped schema emit required
measurements and omit unavailable optional measurements; they do not serialize an
unknown counter or cost as zero. `null` is reserved for a field whose presence has
meaning, such as an observed active interval with no finish. Empty optional
objects and arrays are omitted. Readers preserve this distinction when
round-tripping and ignore unknown fields.

### oneharness history

oneharness must bump its normalized history record `schema_version` and update
its checked-in schema golden in the same release. New writers provide these exact
additions on every record unless the field is described as optional:

- `started_at`: UTC timestamp at invocation start.
- `finished_at`: UTC timestamp at invocation finish, or `null` for an active or
  interrupted record whose end was not observed.
- `duration_ms`: total monotonic invocation duration, retained for compatibility.
- `model_ms`: elapsed time awaiting the model/provider, including stream setup
  through the final model byte, but excluding tool execution.
- `tool_ms`: elapsed time inside tool calls.
- `time_to_first_token_ms` (optional): time from provider request start to the
  first model content or reasoning token; omitted when the provider exposes no
  token boundary or no token arrived.
- `usage.input_tokens`, `usage.output_tokens`, `usage.cache_read_tokens`, and
  `usage.cache_write_tokens`: populated from the provider response when known.
- `usage.cost_usd` (optional): populated from provider-native cost when supplied,
  otherwise from oneharness's versioned model-price table; omitted when neither
  is known.

Each `events[]` item with `kind: "tool_call"` must additionally contain:

- `tool_call_id`: stable within the history session and shared with any matching
  tool result event.
- `started_at` and `finished_at`: UTC interval bounds; `finished_at` may be
  `null` if interrupted.
- `duration_ms`: monotonic tool duration; `null` if no end was observed.
- `status`: one of `completed`, `failed`, `timeout`, or `interrupted`.

This applies to every tool, including `name: "command_execution"`; consumers
must not infer tool duration from command text. For a completed record,
`model_ms + tool_ms <= duration_ms`; the remainder is harness/provider overhead.
`tool_ms` is the union of its tool-call intervals so nested or overlapping calls
are not double-counted.

The schema bump is additive. Readers must accept both the prior and new schema
versions, ignore unknown fields, and treat every addition above as optional when
reading older history. A writer never labels a record with the new schema version
unless all required new-version fields validate.

### onejudge SDK and report

The typed `RunResult` and version 5 of its JSON report must add an optional
`telemetry` object with these exact fields:

- `wall_ms`: monotonic duration from task-loop entry through final result.
- `agent`: a party summary containing `model_ms`, `tool_ms`,
  `time_to_first_token_ms`, `usage`, and `session_ids`.
- `judge`: the same party summary for simulated-user prompts and all final
  evaluator calls.
- `orchestration_ms`: non-negative remainder after unioning linked agent model,
  judge model, and tool intervals within `wall_ms`.
- `sessions`: native linkage records, each containing `session_id`, `role`
  (`agent` or `judge`), `turn_index`, `started_at`, `finished_at`, and
  `history_id` (omitted only when history is disabled or unavailable).

Each party's `usage` has the exact fields `input_tokens`, `output_tokens`,
`cache_read_tokens`, `cache_write_tokens`, and `cost_usd`, with the same unknown
semantics as oneharness. `session_ids` is the ordered, de-duplicated list of the
party's native oneharness session IDs. `time_to_first_token_ms` is the first
known party invocation's value, not a sum. Party `model_ms` and `tool_ms` are
sums across that party's sequential invocations; onejudge must use interval
unions if it permits overlap.

Usage aggregation preserves unknowns independently per field. A party or total
counter is emitted only when that field is known for every contributing record;
an unknown contribution must not produce a deceptively exact partial sum. The
same rule applies to `cost_usd`. Empty parties omit `usage` and `session_ids`.

The linkage is authoritative: consumers identify agent and judge sessions by
`role`, never by task-name prefixes. onejudge must pass through the oneharness
history identity rather than reconstruct it from names. The SDK accepts report
version 4 with `telemetry = None` so an upgraded orchestrator can run against old
reports. When serialized again, absent telemetry is omitted rather than emitted
as `null`.

### Orchestrator index and command surface

The timing model landed in index version 2. Index version 3 added optional
onejudge-linked session timestamps used by the human timeline. Index version 4
added harness-overhead timing for lock waits, repository setup, and scheduling.
Index version 5 adds the third `llmlint` session role and its checked-in golden.
`RunTelemetry` includes:

### Three session roles

`agent` (shown as WORKER) and `judge` are the supervised conversation roles and
alone contribute to `turns`, the quantity bounded by `max_turns`. `llmlint` is a
nested lint role and never consumes that turn budget. Its wrong-file correction
prompt is the lint retry loop, so those invocations contribute to `lint` instead.

- `timing.agent_model_ms`, `timing.judge_model_ms`, `timing.tool_ms`,
  `timing.idle_orchestration_ms`, `timing.unattributed_ms`, and
  `timing.wall_ms`.
- `timing.lock_wait_seconds`, `timing.setup_seconds`, and
  `timing.scheduling_seconds`, derived from node-scoped journal records and graph
  transitions. Older journals render these as zero.
- `timing.fractions.agent_model`, `timing.fractions.judge_model`,
  `timing.fractions.tool`, `timing.fractions.idle_orchestration`,
  `timing.fractions.lock_wait`, `timing.fractions.setup`, and
  `timing.fractions.scheduling`.
- `usage.agent`, `usage.judge`, `usage.llmlint`, and `usage.total`, each with
  `input_tokens`, `output_tokens`, `cache_read_tokens`,
  `cache_write_tokens`, and `cost_usd`.
- `nodes[].timing` and `nodes[].usage` with the same shapes.
- Run and node `lint` counts, distinct from worker/judge `turns`, plus
  `timing.llmlint_model_ms`, `timing.llmlint_seconds`, and
  `timing.fractions.llmlint_model`.
- `nodes[].sessions`, containing the linked `session_id`, `history_id`, `role`,
  and `turn_index` for drill-down.
- `telemetry_quality`: `complete`, `partial`, or `legacy`, plus `sources`, the
  ordered set of `onejudge`, `oneharness`, `history_legacy`, and `journal_legacy`
  actually used.
- `node_work_ms`, with `agent_model_ms`, `judge_model_ms`, `tool_ms`, and
  `wall_ms` summed across nodes without overlap removal.

The existing seconds fields remain readable aliases during one schema version:
`timing.agent_seconds` maps to combined model-plus-tool legacy agent time.
`timing.gate_seconds` and `timing.publication_wait_seconds` are accounted,
non-overlapping portions of those observed intervals, clipped to the remaining
wall budget in display order. Together with model, tool, lock, setup, scheduling,
and idle fields, their millisecond values sum exactly to `wall_ms`. They do not
participate in the model/tool/idle fractions.

The version-5 command keeps JSON as the default and provides `--breakdown` for a stable
human-readable view. The breakdown shows one run row followed by node rows with
wall duration, milliseconds and percentages for the four categories,
unattributed duration, agent/judge input and output tokens, cache tokens, total
`cost_usd`, and telemetry quality. `just telemetry --breakdown --all` includes
settled runs exactly as the JSON view does. Unknown usage renders `?`, not `0`.

## Graceful degradation

**Every consumer field consumes new upstream data when present and falls back to
today's fields when absent, so local value never blocks on upstream.** The
fallback inputs are `labels.role`, `labels.run_id`, per-record `duration_ms`,
`command_execution` events, and `usage`.

Session selection is common to every row below. Prefer onejudge's native
`telemetry.sessions` linkage. Otherwise select sessions whose `labels.run_id`
matches the run and classify each by `labels.role`. Only when `labels.role` is
absent, recognize llmlint's evaluation and wrong-file-correction prompt prefixes
(or their slugified session names) before applying the existing judge-name
fallback; all other legacy sessions are agent sessions. A node additionally requires the
matching `labels.node` (and `labels.step` when producing a step-scoped value).
Records that cannot be linked to the run are not consumed.

### Timing and fractions

| Consumer field | Preferred source | Exact legacy fallback and emitted value |
| --- | --- | --- |
| `timing.wall_ms` | onejudge `telemetry.wall_ms`; otherwise the union of linked oneharness `started_at`/`finished_at` intervals clipped to the journal run interval | Journal timing from first run event through terminal settlement, or observation time while active. Emit `0` only when the journal has no interval. |
| `timing.agent_model_ms` | onejudge `telemetry.agent.model_ms`; otherwise union linked agent oneharness model intervals or sum their `model_ms` when intervals are unavailable | No legacy field separates model waiting from tools. Emit `0` and move the linked records' non-negative `duration_ms` into `unattributed_ms`; do not call it agent-model time. |
| `timing.judge_model_ms` | onejudge `telemetry.judge.model_ms`; otherwise union linked judge oneharness model intervals or sum their `model_ms` | As above: legacy `duration_ms` is unseparated. Emit `0` here and account for it in `unattributed_ms`. If no judge can be linked, this is measured-unknown represented by `0` plus nonzero unattributed time, not proof that no judging occurred. |
| `timing.tool_ms` | union the native timed tool intervals linked through onejudge; otherwise union oneharness timed `tool_call` events | Legacy `command_execution` events prove tool presence but carry no duration. Emit `0` and leave their enclosing record `duration_ms` unattributed. No command event means “no legacy evidence,” not a measured zero. |
| `timing.idle_orchestration_ms` | onejudge `telemetry.orchestration_ms`, reconciled as the non-negative wall remainder after interval precedence | Journal `wall_ms` minus measured agent-model, judge-model, and tool intervals. This remainder includes both actual orchestration and unknown legacy time and is never negative. |
| `timing.unattributed_ms` | `0` when native linkage and interval-complete onejudge/oneharness timing covers the run | Union legacy linked record spans when placeable; otherwise the lesser of the summed non-negative per-record `duration_ms` and journal `wall_ms`, plus uncovered journal remainder. Clip and union so it never exceeds `wall_ms`. This value is contained within `idle_orchestration_ms`, not added beside it. |
| `timing.fractions.agent_model` | Derived from the emitted `agent_model_ms / wall_ms` | Use the emitted fallback milliseconds. Emit `0.0` when `wall_ms == 0`; otherwise the quotient, even when the numerator is zero because its duration is unattributed. |
| `timing.fractions.judge_model` | Derived from the emitted `judge_model_ms / wall_ms` | Same rule. |
| `timing.fractions.tool` | Derived from the emitted `tool_ms / wall_ms` | Same rule. A legacy `command_execution` event alone therefore does not create a positive fraction. |
| `timing.fractions.idle_orchestration` | Derived from the emitted `idle_orchestration_ms / wall_ms` | Same rule. It includes the unattributed share, which the adjacent `unattributed_ms` qualifies. |

The same mapping applies independently to every field under `nodes[].timing`,
using the node's onejudge summary or oneharness sessions selected by
`labels.run_id` plus `labels.node`, and the node's `node-started` through
settlement journal interval. A node with no journal interval emits zero timing
and fractions. Run timing uses interval unions and the precedence rule above;
node timing does not borrow duration from another node.

Legacy per-record `duration_ms` therefore has one unambiguous meaning: it is
evidence of elapsed party-session work for the compatibility
`timing.agent_seconds` calculation and for coverage accounting, but it is not
evidence of agent-model or judge-model latency. In the four-way model it remains
inside `idle_orchestration_ms` and is explicitly counted by `unattributed_ms`
until `model_ms` or timed tool events separate it.

### Usage

For every row, prefer the named onejudge party `usage` field, then aggregate the
same field from role-linked oneharness records, then aggregate today's per-record
`usage`. Native and legacy values may mix record by record. A counter or cost is
`null` when any contributing linked record lacks that field; it is `0` only when
all contributing records report measured zero. An empty party with authoritative
native linkage has measured zero usage; a party that cannot be linked has unknown
usage (`null`). An absent llmlint role is measured zero. Totals are `null` if any
contributing role is unknown and otherwise are the arithmetic sum; this prevents
a partial total from looking complete.

| Consumer field | Preferred source | Exact legacy fallback and emitted value |
| --- | --- | --- |
| `usage.agent.input_tokens` | onejudge `telemetry.agent.usage.input_tokens` | Sum agent-linked oneharness or legacy `usage.input_tokens`; otherwise `null` under the rule above. |
| `usage.agent.output_tokens` | onejudge `telemetry.agent.usage.output_tokens` | Sum agent-linked `usage.output_tokens`; otherwise `null`. |
| `usage.agent.cache_read_tokens` | onejudge `telemetry.agent.usage.cache_read_tokens` | Sum agent-linked `usage.cache_read_tokens`; otherwise `null`. |
| `usage.agent.cache_write_tokens` | onejudge `telemetry.agent.usage.cache_write_tokens` | Sum agent-linked `usage.cache_write_tokens`; otherwise `null`. |
| `usage.agent.cost_usd` | onejudge `telemetry.agent.usage.cost_usd` | Sum agent-linked `usage.cost_usd`; otherwise `null`. Never derive cost from tokens locally. |
| `usage.judge.input_tokens` | onejudge `telemetry.judge.usage.input_tokens` | Sum judge-linked `usage.input_tokens`; otherwise `null`. |
| `usage.judge.output_tokens` | onejudge `telemetry.judge.usage.output_tokens` | Sum judge-linked `usage.output_tokens`; otherwise `null`. |
| `usage.judge.cache_read_tokens` | onejudge `telemetry.judge.usage.cache_read_tokens` | Sum judge-linked `usage.cache_read_tokens`; otherwise `null`. |
| `usage.judge.cache_write_tokens` | onejudge `telemetry.judge.usage.cache_write_tokens` | Sum judge-linked `usage.cache_write_tokens`; otherwise `null`. |
| `usage.judge.cost_usd` | onejudge `telemetry.judge.usage.cost_usd` | Sum judge-linked `usage.cost_usd`; otherwise `null`. Never derive cost from tokens locally. |
| `usage.llmlint.*` | Linked llmlint oneharness records | Apply the same per-field aggregation and unknown rules as agent and judge; emit measured zero when no llmlint session is linked. |
| `usage.total.*` | Sum emitted agent, judge, and llmlint fields | Sum only when all contributing roles are known; otherwise `null`. |

`nodes[].usage` applies this exact table to sessions selected by the node labels.
It does not apportion a run-level usage value across nodes. Unknown fields render
as `?` in `--breakdown` and remain JSON `null` in the machine view.

### Linkage, quality, and work totals

| Consumer field | Preferred source | Exact legacy fallback and emitted value |
| --- | --- | --- |
| `nodes[].sessions` | onejudge `telemetry.sessions` records linked to the node | Build entries from oneharness sessions matching `labels.run_id` and `labels.node`; use `labels.role`, then the legacy name-prefix classification. Preserve the native `session_id`; set `history_id` to the history record identity when present, otherwise `null`; set `turn_index` to `null` because record order is not a native turn identity. Emit `[]` when no session can be linked. |
| `telemetry_quality` | Completeness of native onejudge linkage and interval-complete oneharness timing | Emit `complete` only when all linked sessions have native role linkage and complete timing; `legacy` when no new timing field contributes; `partial` for every mixture, invalid optional field, unknown usage field, or uncovered interval. |
| `sources` | Record each preferred source actually consumed | Emit the ordered de-duplicated subset of `onejudge`, `oneharness`, `history_legacy`, and `journal_legacy`. `labels.run_id`/`labels.role`, `duration_ms`, `command_execution`, and legacy `usage` imply `history_legacy`; journal wall or node intervals imply `journal_legacy`. Emit `[]` only when the run has neither linked history nor journal timing. |
| `node_work_ms.agent_model_ms` | Sum emitted `nodes[].timing.agent_model_ms` | Exact sum, including zero fallbacks; never infer from legacy `duration_ms`. |
| `node_work_ms.judge_model_ms` | Sum emitted `nodes[].timing.judge_model_ms` | Exact sum, including zero fallbacks. |
| `node_work_ms.tool_ms` | Sum emitted `nodes[].timing.tool_ms` | Exact sum; legacy `command_execution` events without timing contribute zero. |
| `node_work_ms.wall_ms` | Sum emitted `nodes[].timing.wall_ms` | Exact node sum without overlap removal. Nodes lacking an interval contribute zero. |

`node_work_ms` deliberately has no idle or unattributed field: those are
wall-clock coverage qualifications, while this object reports parallelizable
work totals. Consumers use each node's `unattributed_ms` and the run-level
quality fields to judge the totals.

Fallback is per field, not per record or run: for example, native timing can
coexist with legacy usage, and a provider-native token count can coexist with an
unknown cost. `telemetry_quality` is `complete` only when every session has
native role linkage, interval-complete timing, and populated timing categories;
it is `legacy` when no new timing field contributed, and `partial` otherwise.

## Trust boundaries and invalid data

oneharness history JSON, onejudge reports, journals, and persisted telemetry are
separate trust boundaries. Each reader validates the enclosing schema version,
field type, numeric range, timestamp syntax, enum membership, and interval order
before using a value. Booleans are not integers; non-finite JSON numbers,
negative counters or durations, reversed intervals, unknown roles, and malformed
session linkage are invalid rather than coercible.

An unsupported enclosing schema version is an actionable input error. Within a
supported or legacy version, an invalid optional telemetry field is ignored at
that field only, records `partial` quality, and allows the next fallback source;
it must not discard otherwise valid usage or timing from the same record. An
invalid required field on a record that claims the new schema invalidates that
record for new-schema aggregation. CLI output remains valid JSON on success;
input or schema errors are written to stderr and exit non-zero. Human breakdowns
render unknown values as `?` and never silently coerce them to zero.

## Local normalization landed ahead of the upstream schemas

The orchestrator can remove two current ambiguities without waiting for the
upstream schema additions:

- Telemetry reads all run-linked sessions and classifies native `labels.role`;
  name prefixes remain a fallback for legacy history and a worker-only
  human-history presentation concern. Until index version 2 lands, the legacy
  `timing.agent_seconds` field continues to count only sessions classified as
  agent rather than silently changing meaning.
- The canonical legacy session duration is the sum of all non-negative integer
  record durations. Telemetry and `orchestrator.history.digest()` use one shared
  aggregation helper. The latest record continues to supply latest status and
  text, never total duration.

The version-5 implementation requires realistic fixtures containing linked
agent, judge, and llmlint sessions, multiple records, timed tool events, partial new
fields, and pure legacy records. Its acceptance must exercise the real `just
telemetry` command and prove exact per-node/run arithmetic, overlap handling,
token/cost preservation, invalid-field degradation, omission on round-trip, and
every fallback tier.
