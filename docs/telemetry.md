# Inspecting telemetry

Use the telemetry view to answer “where did the time go?” for active runs:

```sh
just telemetry --breakdown
just telemetry --breakdown --all   # include settled runs
just telemetry --all               # schema-versioned JSON for analysis
```

The breakdown prints a run row and an indented row for every node. A typical
enriched row and timeline look like this:

```text
RUN/NODE              WALL   AGENT       JUDGE       TOOL        IDLE        UNATTR  TOKENS IN A/J OUT A/J  CACHE R/W  COST  TURNS QUALITY
deploy                 950ms   500 52.6%   120 12.6%   250 26.3%    80  8.4%      0  420/90 85/18 300/0 0.014 4 complete
  Timeline (UTC):
    turn 0 agent: 2026-07-19T12:00:00Z -> 2026-07-19T12:00:00.600Z [agent-7]
    turn 1 judge: 2026-07-19T12:00:00.600Z -> 2026-07-19T12:00:00.720Z [judge-2]
Turn histogram: 4=1
```

`WALL` is elapsed run or node time, not summed work; concurrent node time is
available as `node_work_ms` in JSON. `AGENT` and `JUDGE` are measured provider
latency for their respective roles. `TOOL` is measured execution inside tool
calls. `IDLE` is the non-negative remainder: orchestration, process handoffs,
queueing, and any unknown time. `UNATTR` is the part of that remainder that
legacy inputs cannot classify. Each timing category shows milliseconds and its
share of wall time.

`TOKENS IN A/J` and `OUT A/J` are agent/judge input and output tokens. `CACHE
R/W` is total cache-read/cache-write tokens, `COST` is total `cost_usd`, and
`TURNS` feeds the histogram at the bottom. A `?` means unknown; it never means
measured zero. JSON contains the same counters separately under `usage.agent`,
`usage.judge`, and `usage.total`, both per run and per node.

## Full timing versus fallback

With current upstream data, `AGENT`/`JUDGE` use onejudge's party summaries,
`TOOL` uses timed oneharness tool calls, and the timeline interleaves native
agent and judge sessions by `turn_index`. `QUALITY complete` means authoritative
role linkage and complete timing were available.

Older reports and history remain readable. Missing onejudge linkage falls back
to `labels.role` (then recognized legacy judge names), and missing timed fields
fall back to journal wall time. Untimed `command_execution` events still identify
the dominant command class, but do not invent a duration: model and tool time
remain zero and the unknown share appears in `UNATTR`. The breakdown says
`Timeline: unavailable (legacy session linkage)`, and quality is `legacy` or
`partial`. Treat that as degraded evidence, not proof that judging or tools took
no time.

## Finding an optimization target

1. Run `just telemetry --breakdown --all` and start with the largest wall-time
   run and node. Check `node_work_ms` in JSON when nodes overlapped.
2. Compare model latency with tool time. High `AGENT` suggests prompt/context or
   turn-count work; high `TOOL` suggests inspecting `tool_commands` and the
   slowest timed tool calls in oneharness history.
3. Compare `JUDGE` with `AGENT`. Disproportionate judge time points to supervisor
   or evaluation overhead. Use the timeline to see repeated alternation or a
   long judge turn.
4. Large `IDLE` with low `UNATTR` points to orchestration or handoff overhead.
   Large `UNATTR`, a missing timeline, or `legacy` quality means collect richer
   upstream telemetry before optimizing from the apparent split.
5. Correlate long turns with token/cache/cost growth. A high turn-histogram bucket
   can expose stalled agents even when individual calls are not unusually slow.

The pinned onejudge and oneharness versions may not yet emit every field even
though the reader supports them. A separate version-bump follow-up is still
required; once adopted, it unlocks the full party linkage, timed tool breakdown,
and interleaved timeline for newly recorded runs without changing this command.
