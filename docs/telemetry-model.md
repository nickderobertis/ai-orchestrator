# Telemetry model

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
  what was their `cost_usd`, separately for the agent and judge?
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

The telemetry index moves from schema version 1 to 2 when these fields land, and
its checked-in golden must move in the same change. `RunTelemetry` adds:

- `timing.agent_model_ms`, `timing.judge_model_ms`, `timing.tool_ms`,
  `timing.idle_orchestration_ms`, `timing.unattributed_ms`, and
  `timing.wall_ms`.
- `timing.fractions.agent_model`, `timing.fractions.judge_model`,
  `timing.fractions.tool`, and `timing.fractions.idle_orchestration`.
- `usage.agent`, `usage.judge`, and `usage.total`, each with
  `input_tokens`, `output_tokens`, `cache_read_tokens`,
  `cache_write_tokens`, and `cost_usd`.
- `nodes[].timing` and `nodes[].usage` with the same shapes.
- `nodes[].sessions`, containing the linked `session_id`, `history_id`, `role`,
  and `turn_index` for drill-down.
- `telemetry_quality`: `complete`, `partial`, or `legacy`, plus `sources`, the
  ordered set of `onejudge`, `oneharness`, `history_legacy`, and `journal_legacy`
  actually used.
- `node_work_ms`, with `agent_model_ms`, `judge_model_ms`, `tool_ms`, and
  `wall_ms` summed across nodes without overlap removal.

The existing seconds fields remain readable aliases during one schema version:
`timing.agent_seconds` maps to combined model-plus-tool legacy agent time,
`timing.gate_seconds` remains gate process time, and
`timing.publication_wait_seconds` remains publication wait. They do not
participate in the new four-way fractions.

The version-2 command keeps JSON as the default and adds `--breakdown` for a stable
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

Apply the fallback in this order:

1. Use onejudge's native `telemetry.sessions` linkage and party summaries.
2. Otherwise select all sessions with matching `labels.run_id`, classify them by
   `labels.role`, and aggregate their new oneharness fields.
3. Otherwise classify the recognized legacy judge name prefixes as judge and
   all other sessions as agent. Sum each record's non-negative `duration_ms` as
   legacy party elapsed time, identify tool presence from `command_execution`
   events without inventing tool duration, and aggregate whatever legacy
   `usage` fields exist.
4. Derive wall time from the run journal as today. Put every duration that cannot
   be separated safely in `idle_orchestration_ms` and `unattributed_ms`; never
   guess an agent/judge/tool split from token counts or event counts.

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

The version-2 implementation requires realistic fixtures containing linked
agent and judge sessions, multiple records, timed tool events, partial new
fields, and pure legacy records. Its acceptance must exercise the real `just
telemetry` command and prove exact per-node/run arithmetic, overlap handling,
token/cost preservation, invalid-field degradation, omission on round-trip, and
every fallback tier.
