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
RUN/NODE              WALL   AGENT       JUDGE       TOOL        GATE PUB LOCK SETUP SCHED IDLE UNATTR  TOKENS IN A/J OUT A/J  CACHE R/W  COST  TURNS QUALITY
deploy                 950ms   500 52.6%   120 12.6%   100 10.5%   40  20   10    30    20   110 11.6%      0  420/90 85/18 300/0 0.014 4 complete
  Timeline (UTC):
    turn 0 agent: 2026-07-19T12:00:00Z -> 2026-07-19T12:00:00.600Z [agent-7]
    turn 1 judge: 2026-07-19T12:00:00.600Z -> 2026-07-19T12:00:00.720Z [judge-2]
Turn histogram: 4=1
```

`WALL` is elapsed run or node time, not summed work; concurrent node time is
available as `node_work_ms` in JSON. `AGENT` and `JUDGE` are measured provider
latency for their respective roles. `TOOL` is measured execution inside tool
calls. `IDLE` is the non-negative remainder: orchestration, process handoffs,
unknown time after the explicit harness buckets. `LOCK` is time waiting for
process-shared locks, `SETUP` is fetch and worktree creation, and `SCHED` is time
from dependency readiness until the node worker starts. `UNATTR` is the part of the remainder that
legacy inputs cannot classify. Each timing category shows milliseconds and its
share of wall time.
`GATE` is repository verification and `PUB` is the wait from a green gate to
publication closeout. Both are clipped to their non-overlapping share of the
remaining wall budget, so the displayed model, tool, gate, publication, lock,
setup, scheduling, and idle buckets sum exactly to `WALL` even when raw journal
intervals overlap.

`TOKENS IN A/J` and `OUT A/J` are agent/judge input and output tokens. `CACHE
R/W` is total cache-read/cache-write tokens, `COST` is total `cost_usd`, and
`TURNS` feeds the histogram at the bottom. A `?` means unknown; it never means
measured zero. JSON contains the same counters separately under `usage.agent`,
`usage.judge`, and `usage.total`, both per run and per node.

## Full timing versus fallback

For records produced after the 0.4.4/0.3.4 upgrade, `AGENT`/`JUDGE` use
onejudge's typed party summaries, `TOOL` uses oneharness' normalized `tool_ms`
and per-tool-call `duration_ms`, and the timeline interleaves native
agent and judge sessions by `turn_index`. `QUALITY complete` means authoritative
role linkage and complete timing were available.

Pre-upgrade reports and history remain readable through a documented fallback.
Missing onejudge linkage falls back
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

## Diagnosing a slow or stalled run

1. Run `just telemetry --breakdown --all` and locate the largest run and node.
2. A large `LOCK` bucket means another process held a shared registry, journal,
   checkout, or worktree resource. Inspect `lock-wait` journal events for the
   recorded lock identity and whether acquisition timed out.
3. A large `SETUP` bucket distinguishes repository fetch cost from `git worktree
   add`; inspect `setup-finished.detail.operation` in `events.jsonl`.
4. A large `SCHED` bucket means the node was dependency-ready but waited for a
   worker slot. Compare it with the graph concurrency and adjacent node intervals.
5. For a failed gate, read the node result detail or the
   `verification-finished.detail.output_tail` journal field. Both contain the same
   bounded tail from the captured gate output, so reproducing the gate is not
   required to identify the failing tier.
