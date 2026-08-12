# Inspecting telemetry

Use the telemetry view to answer “where did the time go?” for active runs:

```sh
just telemetry --breakdown
just telemetry --breakdown --all   # include settled runs
just telemetry --all               # schema-versioned JSON for analysis
just telemetry RUN_ID              # one named run, settled or not
just telemetry --breakdown --all --since 2026-07-01T00:00:00Z --until 2026-08-01T00:00:00Z
```

`RUN_ID` is the identifier `launch.json` advertises, resolved exactly as `just
monitor` and `just status` resolve it — an exact run directory, or a plan name
that names one active launch. Naming a run is the request, so it is reported
whether or not it has settled; `--all` only governs the unscoped index.

A round still in flight has written no `result.json`, so its nodes are described
by the run journal: a node reads `running` only until the journal records it
settling, and a node recorded as `node-failed` reads `failed` here while the round
is still going.

The breakdown prints a run row and an indented row for every node. A typical
enriched row and timeline look like this:

```text
RUN/NODE              WALL   WORKER      JUDGE       LLMLINT     TOOL        GATE PUB LOCK SETUP SCHED IDLE UNATTR  TOKENS IN W/J/L OUT W/J/L  CACHE R/W  COST  TURNS LINT QUALITY
deploy                 950ms   500 52.6%   120 12.6%   100 10.5%    40  4.2%   20   10   30    20     0   110 11.6%      0  420/90/30 85/18/6 300/0 0.014 4 2 complete
  Timeline (UTC):
    turn 0 agent: 2026-07-19T12:00:00Z -> 2026-07-19T12:00:00.600Z [agent-7]
    turn 1 judge: 2026-07-19T12:00:00.600Z -> 2026-07-19T12:00:00.720Z [judge-2]
Turn histogram: 4=1
```

`WALL` is elapsed run or node time, not summed work; concurrent node time is
available as `node_work_ms` in JSON. `WORKER`, `JUDGE`, and `LLMLINT` are measured
provider latency for their respective roles. `TOOL` is measured execution inside tool
calls. `IDLE` is the non-negative remainder: orchestration, process handoffs,
unknown time after the explicit harness buckets. `LOCK` is time waiting for
process-shared locks, `SETUP` is fetch and worktree creation, and `SCHED` is time
from dependency readiness until the node worker starts. `UNATTR` is the part of the remainder that
legacy inputs cannot classify. Each timing category shows milliseconds and its
share of wall time.
`GATE` is repository verification and `PUB` is the wait from a green gate to
publication closeout. Since the repository's own merge path became the
authoritative verifier, the lifecycle runs no gate of its own, so `GATE` reads
zero for new runs and the gate's cost lands inside `PUB`, where `git push` runs
the `pre-push` hook. It stays populated for runs recorded before that change.
Both are clipped to their non-overlapping share of the
remaining wall budget, so the displayed model, tool, gate, publication, lock,
setup, scheduling, and idle buckets sum exactly to `WALL` even when raw journal
intervals overlap.

`TOKENS IN W/J/L` and `OUT W/J/L` are worker/judge/llmlint input and output tokens. `CACHE
R/W` is total cache-read/cache-write tokens, `COST` is total `cost_usd`, and
`TURNS` feeds the histogram at the bottom and counts only worker/judge conversation
turns; `LINT` counts llmlint invocations separately. A `?` means unknown; it never means
measured zero. JSON contains the same counters separately under `usage.agent`,
`usage.judge`, `usage.llmlint`, and `usage.total`, both per run and per node.

## Three session roles

Every session is classified as worker (`agent`), `judge`, or `llmlint`. Llmlint
sessions are nested quality checks and do not count against onejudge's `max_turns`.
Their wrong-file correction prompts are llmlint's retry loop, so they contribute to
`LINT`, not `TURNS`. Legacy sessions without a role label retain the documented
fallback, with the known llmlint prompt/name signatures checked first.

## Llmlint wrong-file retry rate

The bottom of the breakdown reports wrong-file correction sessions divided by
initial llmlint evaluation sessions, overall, by oneharness repository project,
and by node when the session has a `node` label. JSON exposes the same cohort as
`metrics.llmlint_wrong_file_retries`, including the numerator, denominator, rate,
observed `period_start`/`latest_session_start`, and the `by_repository` and
`by_node` splits.

`oneharness_retry_sessions` is the broader guardrail: it counts all llmlint
oneharness sessions in the cohort that are not initial evaluations. Compare it
alongside `wrong_file_corrections` so a prompt or template change that moves
rework away from the known correction signature remains visible. Use `--since`
(inclusive) and `--until` (exclusive) with UTC ISO-8601 timestamps to create
repeatable before/after cohorts.

## Full timing versus fallback

For records produced after the 0.6.15/0.3.8 upgrade, `AGENT`/`JUDGE` use <!-- llmlint: ignore[contracts_have_one_source_or_a_drift_gate] tests/test_onejudge_version.py::test_telemetry_upgrade_boundary_matches_authoritative_versions enforces this version boundary against the authoritative pins. -->
onejudge's typed party summaries, `TOOL` uses oneharness' normalized `tool_ms`
and per-tool-call `duration_ms`, and the timeline interleaves native
agent and judge sessions by `turn_index`. `QUALITY` renders
`<timing_quality>/<linkage_quality>`: timing is `complete`, `partial`, or
`legacy` according to measured-field completeness, independently of linkage,
which is `native`, `labelled`, or `inferred`.
`native` requires onejudge session linkage to cover every history summary
contributing to the row; mixed native and label-linked summaries are `labelled`
when every role label is valid.

Pre-upgrade reports and history remain readable through a documented fallback.
Missing onejudge linkage falls back
to `labels.role` (then recognized legacy judge names), and missing timed fields
fall back to journal wall time. Untimed `command_execution` events still identify
the dominant command class, but do not invent a duration: model and tool time
render as `?` and the unknown share appears in `UNATTR`. History schemas `1.1`
and `1.2` are recognized. Schema `1.2` `observed_tool_ms` and tool events marked
`timing_source: stdout_observed` remain visible but make timing quality `partial`;
only provider-measured timing can contribute to `complete`.
Records from a newer, unrecognized history schema are also read best-effort and
marked degraded.
Missing or null run timing and tool-event timing/status degrade only the affected
session. In contrast, malformed present values and contradictory timing remain
errors: finish cannot precede start, and model plus tool time cannot exceed the
record duration. Measured WORKER/JUDGE/LLMLINT/TOOL values remain visible for
`complete` and `partial` timing; `?` means no measurement exists. `UNATTR` and
the two quality dimensions qualify those values. JSON consumers use
`timing_presence` to distinguish a measured zero from an unavailable category.
The breakdown says
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
   Large `UNATTR`, a missing timeline, or `legacy/inferred` quality means collect richer
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
5. For a failed gate, read the node result detail: it names the rejecting
   `pre-push` hook and carries the Git diagnostic, so reproducing the gate is not
   required to identify the failing tier. Read it against the node's
   `merge-gate-coverage` event, which records the hook and required checks
   dispatch expected to run, and against `verification-finished`, which brackets
   each gated push with its verdict and a bounded `detail.output_tail`. The whole
   run is preserved at the node's `artifacts.gate_log`, which accumulates one
   record per gated push (branch, then publication) — so a green publication can
   also show what its gate did, not only that nothing objected.
6. For a failure that never reached a gate — a base advanced under the rebuild,
   a fetch or worktree that could not be built — read `publication-failed`. It
   carries the same bounded `output_tail` and points at the same log, so "the
   gate rejected it", "a sibling run moved the base", and "the host failed" are
   three different readings rather than one silent settle.

## Seeing the supervisory tier

The launched orchestrator and its per-round check-in dispatches are agents like any
other, and they are visible the same way — with one addition and one fallback.

They were invisible for longer than the workers, and the reason is worth keeping:
oneharness *was* recording them. This host's history store holds 157 `orchestrator`
and 728 `check-in` run records, correctly role-labelled, out of 12,387 runs. Nothing
served them. So the run-scope span below is the fix; the capture is a fallback for
the narrower case where the write itself was refused.

1. **`just status <run-id>` and `just runs`** carry one driver line per unfinished
   launch: whether the recorded pid is still there, which part of its loop the run's
   own state places it in (`starting`, `driving-round`, `executing-run-plan`,
   `reviewing-results`, `surfacing`), and how long since anything of it was last observed doing something. A
   driver this host has *proved* is gone reads `DRIVER DEAD (pid N is gone) — …;
   nothing is driving this run`. That is deliberately distinct from `PARKED`, which
   is a launch that still holds its pid while nothing progresses, and from a node
   the planner idled with `cancel`.
2. **The run timeline** (`GET /api/v2/runs/{run_id}/timeline?scope=run`) serves one
   dispatch span per supervisory session, role-labelled through `agent_role`, open
   (`ended_at: null`) while the session is still speaking. The driver's span also
   carries `phase`, the same vocabulary the CLI prints.
3. **When the harness refused the history write**, the span comes from the run's own
   bounded local capture under `runs/<run-id>/supervisory/` instead of from a
   transcript: same role, timing and liveness, the captured turns as
   `conversation-turn` events with status `captured`, the bounded text under
   `detail.output_tail`, and the refusal itself as a `history-write-failed` event
   carrying the reason. A capture-backed span has no `conversation` reference,
   because there is no transcript to open. A capture stands down as soon as history
   *did* record that session, so a session is never drawn twice.

The capture is written by the dispatch layer at the seams it already owns:
`just orchestrate` opens the driver's, the channel relay appends one bounded turn
per orchestrator turn, and the check-in dispatcher opens and closes its own. It is a
summary and never a replacement — bounded to the newest few turns, each cut to a
readable head, so it stays sized by the tier rather than by the run.

The upstream defect it exists for is oneharness refusing a history write with
`new history run lacks complete v1.0 telemetry` (and the `cannot write vN history
telemetry` variants), raised in `crates/oneharness-core/src/io/history.rs`.
`onepipeline` owns those patterns now: it classifies a dispatch that died on one as
an infrastructure failure, and its capture records the same string as the reason a
session is missing.

That refusal is not codex-specific, though it was assumed to be, and the assumption
sent a night's debugging at the wrong harness. A refused write leaves nothing behind,
so the store cannot exhibit one directly; what it does
show is that every one of codex's 7,642 run records is `ok` with complete native
telemetry, while claude-code supplies no native per-phase timing at all (`started_at`,
`finished_at`, `tool_ms`, `time_to_first_token_ms`, `model_ms` are absent from all
4,745 of its records) and carries every recorded failure. Those counts come from
reading `type: "run"` lines out of the history store (`just telemetry` reaches the
same records); re-measure there rather than inferring the harness from chain order.

## Diagnosing a provider failure

A node that dies to the provider says which side of the conversation refused,
which identity refused it, and why. `just status` and `just runs` carry that
without any further command, because the incident this exists for cost a night:
every death printed `provider error (respond): harness failed (quota)`, which
names neither.

1. Read the **provider health** block both views print. It lists all five
   configured identities under the same indirections dispatch uses
   (`scripts/claude-alt-config-dir.sh`, `scripts/codex-alt-home.sh`), with each
   one's binding window, utilization, and reset time. An identity whose probe
   failed is listed as `unknown` rather than dropped, so a chain is never
   silently short one member.
2. Read the rolled-up failure lines beneath each run. Repeated deaths on one
   cause collapse to one line — `3 nodes failed on judge-side codex
   quota mid conversation, resets Aug 8` — so a whole round's worth of the same
   refusal reads as the single fact it is.
3. `just results <run-id>` and the read API's `failure` record carry the same
   attribution per node, plus a bounded `raw_tail` of what the harness actually
   printed and, where the harness stated one, its structured error payload.
   `quota_at_launch` fell through to the next identity; `quota_mid_conversation`
   could not, because the conversation was already bound to the refusing one.
4. A dispatch whose agent side was recorded but whose judge side was not is
   marked `judge_unrecorded`. The supervisor did not vanish — its harness failed
   to write history — so read the raw session rather than concluding the run was
   unsupervised.

The probe is read-only and cheap, but it does reach the configured providers.
Set `ORCHESTRATOR_PROVIDER_HEALTH_PROBE=0` on an offline or metered host to
answer every view with unknown identities instead; this repository's own test
suite sets it so a suite run never touches a paid identity.
