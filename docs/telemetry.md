# Inspecting telemetry

Use the telemetry view to answer “where did the time go?” for active runs:

```sh
just telemetry                     # every run
just telemetry --breakdown         # every run, wall clock broken into buckets
just telemetry RUN_ID              # one named run, settled or not
just telemetry RUN_ID --breakdown  # that run, broken down
```

`onepipeline telemetry` takes exactly one option, `--breakdown`, and one optional
run argument; there is no scoping, filtering, or windowing flag to reach for. A
window is cut from the output rather than asked for.

`RUN_ID` is the identifier `launch.json` advertises, resolved exactly as `just
monitor` and `just status` resolve it — an exact run directory, or a plan name
that names one active launch. Naming a run is the request, so it is reported
whether or not it has settled; omitting it covers every run.

**The view is run-scoped, and it has no per-node rows.** Everything below was
re-measured against `onepipeline` v0.15.1 on this host's own runs root; the per-node
table, session timeline, turn histogram, and llmlint retry-rate cohort this document
used to describe belonged to the pre-extraction implementation and are not in the
adopted crate.

With no flag, each run is one JSON document on a line:

```text
{"schema_version":2,"run_id":"fix86-llmlint","wall_ms":3022255,
 "buckets":[{"name":"agent","ms":1718526},{"name":"judge"},{"name":"llmlint"},
            {"name":"gate","ms":1288078},{"name":"publication_wait","ms":1181},
            {"name":"lock_wait","ms":0},{"name":"setup","ms":14455},
            {"name":"scheduling","ms":15}],
 "usage":{"agent":{"input":68,"output":9692,"cache_read":1300969,
                   "cache_write":35173,"cost_usd":1.2448545},
          "judge":{"input":76046,"output":421,"cache_read":33024},
          "total":{"input":49783,"output":9937,"cache_read":1322985,
                   "cache_write":35173,"cost_usd":1.2448545}},
 "dispatches":1,"settled_done":0,"no_diff":0,
 "surfaces_queued":50,"surfaces_read":0}
```

`--breakdown` renders that same document as text:

```text
fix86-llmlint  WALL 50m22s
  agent                  28m38s   56%
  judge              not measured
  llmlint            not measured
  gate                   21m28s   42%
  publication_wait           1s    0%
  lock_wait                  0s    0%
  setup                     14s    0%
  scheduling                 0s    0%
  usage agent        in 68  out 9692  cache r 1300969 w 35173  $1.2449
  usage judge        in 76046  out 421  cache r 33024 w not measured  $not measured
  usage llmlint      not measured
  usage total        in 49783  out 9937  cache r 1322985 w 35173  $1.2449
  1 dispatch(es), 0 done, 0 no-diff; 50 surface(s) sent, 0 read
```

`WALL` is the run's elapsed time, not summed work. The **eight buckets are a closed
set and sum exactly to it** — re-taken on the adopted engine over this host's own
runs root, where all **137** recorded runs balance to the millisecond, which is worth
saying because it is an invariant the crate documented before it upheld it:
onepipeline 0.12.1 is the release that made emitted bucket totals match wall time in
both directions, and beneath it this sentence was a claim about the contract rather
than about a run. The set below is reconciled against
`onepipeline`'s own `BucketName` enum by
`tests/test_engine_contracts.py`, so a bucket the engine adds or renames
fails here rather than leaving this table quietly short of one: a bucket named by a free string could be added without
anything noticing the parts no longer add up to the whole. An absent `ms` is *not
measured*, which is a different fact from a measured `0`.

| Bucket | What it counts |
| --- | --- |
| `agent` | At least one agent dispatch in flight and nothing more specific happening |
| `judge` | A judge side of a dispatch running |
| `llmlint` | An LLM-lint pass running |
| `gate` | A repository's own verification gate running |
| `publication_wait` | The push, the change request, its checks, and the merge |
| `lock_wait` | Blocked on a repository identity's lock |
| `setup` | Preparing a workspace: the clone, the worktree, the fetch |
| `scheduling` | Everything else the run's clock covers — waiting on a decision or a person, and the gaps between dispatches |

The four repository buckets come from a phase machine over the node's `onevcs`
session stream:

| Bucket | The session event kinds that put a node in it |
| --- | --- |
| `setup` | `session-opened`, `fetch`, `commit-preserved`, `lock-acquired`, `recovery-attested` |
| `lock_wait` | `lock-wait` |
| `gate` | `gate-started` (reachable only from a store an older release wrote) |
| `publication_wait` | `gate-verdict`, `push`, `change-opened`, `change-check`, `merge-queued`, `change-merged`, `merge-completed`, `sync-conflict` |

A session's last record is what it is doing until the next one, and a kind this build
does not know — one a newer `onevcs` emits, or one that ends the session — leaves it
where it was rather than stopping its clock. Where two sessions disagree about a
millisecond the more specific state wins, blocked before working: `lock_wait`, then
`gate`, then `publication_wait`, then `setup`.

That table and that order are reconciled against `telemetry.rs`'s own `Phase::of`,
`Phase::bucket`, and `Phase::PRECEDENCE` on every `just check`, in both directions.

**`judge` and `llmlint` read `not measured` on this host, and that is expected.** The
bucket is filled from a `role` on a relayed turn, and nothing stamps one today —
`onepipeline`'s own `docs/contract-divergences.md` divergence 10 is the open proposal
that something should. Read a `judge` of `not measured` beside a non-zero `usage
judge` as exactly that: the supervisor ran and its tokens were counted, but no
interval was attributed to it. It is not evidence that judging took no time.

### What `gate` actually reads on this host

**`gate` is served absent by every run this stack now produces, and that is the
adoption rather than a fault.** The bucket is filled from `onevcs`'s `gate-started`
and `gate-verdict` events, and onevcs 0.11.0 emits neither — it removed the tier
that emitted them. onepipeline keeps the bucket because the contract fixes the
eight, and it is still filled when the run being read was recorded by an older
`onevcs`. The `fix86-llmlint` breakdown above is one of those: 21m28s, 42% of its
wall clock, recorded when this host still ran a gate of its own.

So on a run recorded today that cost has not vanished, it has moved. What verifies
a change is the repository's own merge path, and its wall time is the
publication's: the `pre-push` hook git runs at the publishing push lands in
`publication_wait`, and so does waiting on a host's required checks. **Read a
present `gate` as evidence the run predates the adoption**, and read `agent`
against `publication_wait` where the old advice said `agent` against `gate`.

The paragraph below is kept for reading those older runs. It described an
identity routed to `{kind: pre-push}`, whose cost landed in
`publication_wait` where `git push` runs the hook, or to `{kind: checks}`, whose
cost falls outside the run entirely because the host decides after the node settled.
That routing is the rules file's to change; this paragraph only says what follows
from it.

The document is `deny_unknown_fields` and its top-level fields are
`schema_version`, `run_id`, `wall_ms`, `buckets`, `usage`, `dispatches`,
`settled_done`, `no_diff`, `surfaces_queued`, and `surfaces_read` — a tenth would be
a schema version, not a field to discover.

`usage` is per party — `agent`, `judge`, `llmlint`, `total` — each carrying
`input`, `output`, `cache_read`, `cache_write`, and `cost_usd`, and each field
omitted rather than zeroed when it was never measured.
Everything said here about those fields was measured on records this host wrote
after the 0.11.0/0.5.3 upgrade (`config/oneharness.version` and
`config/onejudge.version`), which is the boundary the older per-party accounting
sat behind. What a run recorded *before* that pair reports is **not established
here** — re-measure rather than assuming the shape carries backwards, and re-check
this paragraph whenever either pin moves. That re-check has now been made twice
without the shape moving. For the 0.10.3/0.5.1 upgrade, one turn spent on each binary
with everything else held differed by exactly two added keys — `results[].work` and
`fallback.stopped_without_work`, both of which say something about a failure nothing
could classify. For this one it was re-taken on 2026-08-25 by spending a real
`just smoke` turn on the adopted pair and reading the record it wrote: at history
schema 1.1 the `usage` block is `input_tokens`, `output_tokens`, `cache_read_tokens`,
`cache_write_tokens`, `cost_usd` and nothing else, on the candidate that answered and
on the one that fell through on quota alike. Nothing an accounting reader reads is
renamed, retyped, or re-meant, which is why the boundary sentence names the pair
these records are written under rather than the older one they were first taken on. `dispatches`,
`settled_done`, `no_diff`, `surfaces_queued`, and `surfaces_read` are the run's own
counters; `surfaces_read` is what resets the planner-update pacemaker.

## Finding an optimization target

1. Run `just telemetry --breakdown` and start with the largest `WALL`.
2. Compare `agent` with `publication_wait`. A run that is mostly `agent` is prompt,
   context, or turn-count work; one that is mostly `publication_wait` is paying for
   the repository's own bar at the merge path, and `just lint-llm-diff`'s cached
   verdict is the lever there rather than anything in this view. On a run recorded
   before onevcs 0.11.0 that second bucket is `gate` instead; see [What `gate`
   actually reads on this host](#what-gate-actually-reads-on-this-host).
3. A large `scheduling` bucket on a wide graph is the frontier waiting — on a
   decision point, on a person, or on concurrency. Compare it with the graph's
   `concurrency` and with `just status`, which names what each node is waiting for.
4. `dispatches` against `settled_done` is the re-ask rate. A dispatch that produced
   nothing is asked again up to `ONEPIPELINE_BOUNDARY_ATTEMPTS` times, and each
   attempt counts here, so `dispatches` well above the node count is a provider
   problem rather than a scheduling one — read
   [Diagnosing a provider failure](#diagnosing-a-provider-failure).
5. `usage total` against `WALL` is the spend rate. `cost_usd` is absent, not zero,
   wherever a provider reported none.

## Diagnosing a slow or stalled run

1. Run `just telemetry --breakdown` and locate the largest run.
2. A large `lock_wait` bucket means another process held the identity's merge queue
   or an advisory lock. The `lock-wait` events in `events.jsonl` carry the identity,
   the elapsed seconds, and the one-based queue position.
3. A large `setup` bucket is the clone, the worktree, and the fetch. On this host
   that is normally 14-22s per lifecycle node; well above it means a cold or a
   contended execution checkout.
4. A large `scheduling` bucket means the graph was not working. `just status` is
   where that is diagnosed, not here — it names the in-flight dispatches, the
   surfaces waiting unread, and whether anything is driving the run at all.
5. **For a refused publication, read the node's settlement detail and the session's
   own `push` event.** The detail carries `onevcs`'s own reason, and that event
   carries the merge path's output, a `preserved_log` path that outlives the run's
   worktree, and the whole run again as an artifact (`onevcs artifact`). On a run
   recorded before onevcs 0.11.0 the same evidence hangs off `gate-verdict` and the
   detail reads `onevcs: <command> rejected "<branch>"`. The read API serves the same run as a `verification` span
   whose `detail.output_tail` is the tail of it and whose `detail.artifact_id`
   opens the rest — see [Seeing the supervisory
   tier](#seeing-the-supervisory-tier). There is no `merge-gate-coverage` or
   `verification-finished` *event*, and no `artifacts.gate_log` on the node
   result.
6. **For a failure that never reached the merge path** — a base advanced under the
   publication, a fetch or a worktree that could not be built — there is no
   preserved log, because none was produced. The whole account is the same
   settlement detail, carrying `onevcs`'s own reason. Those two no longer settle
   alike: a refusal by something that judged the publication is terminal and settles
   `publication-failed`, while a base that moved under the publication is
   `sync-conflict` — a word of its own, reached
   only after the node was dispatched again on that branch. The detail is still where
   each of them says what happened.

## Seeing the supervisory tier

The `monitor` member and the scheduled `check-in` dispatches beside it are agents
like any other, and they are visible the same way — with one addition and one gap
this document is explicit about.

They were invisible for longer than the workers, and the reason is worth keeping:
oneharness *was* recording them. This host's history store holds 157 supervisory
and 728 `check-in` run records, correctly role-labelled, out of 12,387 runs. Nothing
served them.

1. **`just status <run-id>` and `just runs`** report two independent liveness
   verdicts, and confusing them is the common mistake. The **driver** verdict is
   one of `ACTIVE`, `DRIVER DEAD`, `PARKED`, or `UNDRIVEN`; `just orchestrate
   --adopt` is the way back from the two that mean nothing is driving the run
   (`DRIVER DEAD` and `PARKED`). The **observer** verdict is separate and prints
   beside it: `OBSERVER DEAD` when the launch named an observer graph whose run has
   ended, `NO OBSERVER` when it named none, and nothing at all while it is
   watching. A run can read `ACTIVE  OBSERVER DEAD` — driving fine, unwatched — and
   that is a different fix from a dead driver.
2. **The run timeline** (`GET /api/v2/runs/{run}/timeline?scope=run`, served by
   `just telemetry-server`) is the structured view. Measured against real runs on
   **`onepipeline-api` 0.6.3**, the release `config/onepipeline-ui.version` pins —
   a measurement rather than a reading, because that crate has no registered checkout
   on this host and its CLI dumps no schema, so a bump is what re-opens this
   paragraph: `timeline_schema_version` 7, spans of kind `run`, `dispatch`, `node`,
   `rollup`, `verification`, `publication`, and `human-wait`, each with `started_at`
   and an `ended_at` that is `null` while it is open. The `run` span carries `phase`,
   which read `starting`, `waiting`, `surfacing`, `settled`, and `finished` across the
   runs read here; no run read served the `dispatching` this paragraph used to name.
   **The dispatch tier is two span kinds, and confusing them is the easy mistake.** A
   **`dispatch`** span is one supervisory conversation, parented on the *run*, with an
   `agent_role` of `orchestrator` (the monitor) or `check-in` (the pacemaker), a
   `transport_role`, a `status`, and a `reference` of `{kind: conversation}`. A
   **`rollup`** span is the per-node tier, parented on the *node*, and comes in two
   shapes: labelled `dispatch` it carries `agent_role` — `worker` or `pr-author` — a
   `transport_role`, and a `count`; labelled `lock-wait` it carries
   `total_duration_ms` and no roles at all. A **`verification`** span is a gate run,
   carrying `status` and a `detail` of `{ok, output_tail, artifact_id}`; the tail is
   where a failed gate's own words are, and `artifact_id` opens the whole log. A
   **`publication`** span carries `status` (`open`, `merged`, `conflict` were served
   here) and, once it has one, a `reference` of `{kind: pr}`.
   **None of the lists above is a closed contract, and reading one as though it were
   is the mistake this paragraph most invites.** The reader declares none of it, so
   each list is the set of values *observed*, and a value missing from one is
   unmeasured rather than impossible. What is held rather than observed is what
   `tests/e2e/test_dag_ui_serving_e2e.py` serves six checked-in runs to assert: every
   span kind above except `human-wait`, each one's fields and parentage, the `waiting`,
   `surfacing`, `settled`, and `finished` phases, both the `open` and `merged`
   publication statuses, and both supervisory `agent_role`s. One of those runs is
   **derived rather than recorded**, and the only one here that is: `dag-ui-truth` is
   the sole run on this
   host whose *monitor* member ever completed a turn — which is what makes an
   `orchestrator` label exist at all — and it is 8.9MB of journal whose worker
   transcripts quote an `llmlint: ignore` directive the linter then reads as a real
   one, so the whole run cannot be checked in. `dag-ui-truth-monitor-slice` is the six
   events of its dag-scope graph, kept verbatim; the fixture's own docstring records
   what was dropped and the one field rewritten. Two values are still observed and
   unheld, and both re-measured on 0.6.3: `conflict` off `issue-27` and
   `pr-author-body`, and `human-wait` off `pr-author-body`. **The third one is gone,
   and the way it went is the warning.** `deciding` was read off `dag-ui-truth` and
   `issue-27` and is served by neither now — they answer `surfacing` and `waiting`, on
   0.6.2 and 0.6.3 alike, so the release did not take it. A `run` span's `phase` is
   the run's *current* state rather than a record of the states it passed through, so
   a phase only a live run exhibits cannot be re-measured off a finished one at all.
   Re-measure the two by hand against those named runs on a bump; do not go looking
   for a phase a stopped run can no longer be in.
   **The listing is the live runs, and a settled one leaves it.** `GET /api/v2/runs`
   carried every run read here whose `phase` was `waiting` or `surfacing` and none
   whose phase was `settled` or `finished` — but a settled run's timeline is still
   served in full by id. So a run an operator knows finished, missing from the view's
   list, is this rather than a reader that lost it.
   <!-- llmlint: ignore[contracts_have_one_source_or_a_drift_gate] These field names
   have no authoritative declaration this host can read: `onepipeline-api` is in no
   registered checkout, its source is in neither the `onepipeline` repository nor the
   installed wheel, and `onepipeline-api serve` is its only verb — there is no schema
   to dump. Every other engine contract in these documents is reconciled against
   source; this one is a *measurement* off a live response, so what
   `tests/test_onejudge_version.py::test_claims_about_the_adopted_read_api_name_the_adopted_release`
   holds is the half that can be held — that the paragraph names the release it was
   measured on, so a pin bump fails the gate and re-opens it. Reconciling the fields
   themselves needs a registered checkout of that crate or a schema verb on its CLI,
   and is tracked as follow-up. -->
3. **What 0.6.3 changed here, and it is one number.** Serving the same runs from
   0.6.2 and 0.6.3 side by side is what dates this section. The whole delta is
   `timeline_schema_version` 6 becoming 7: on the six runs checked in under
   `tests/fixtures/timeline-runs/` and on `dag-ui-truth`, `issue-27`, and
   `pr-author-body` from this host's own runs root, every other byte of the timeline
   response is identical, and the conversation route is byte-identical too. That is
   what the release *adds* rather than what it changes — 0.6.3 renders which release
   carried each landed node, and no run on this host has a release event in it,
   because no repository registered here declares a release target. So a reader who
   opens a node and finds no release row is looking at an undeclared target, not an
   unadopted release. `tests/e2e/test_dag_ui_serving_e2e.py` holds the rendering half —
   a browser opened on a real recorded run draws no release row from data that has
   none — and `tests/e2e/test_release_adoption_in_force_e2e.py` holds the half about
   this host, by asking every registered identity whether it declares a target.
4. **What 0.6.2 changed, and what is still missing.** Kept because it is the release
   that made the transcript a view worth opening, and the accounting defect it fixed
   is the kind a reader cannot eyeball. What moved is the **conversation route**,
   `GET /api/v2/runs/{run}/conversations/{conversation}`, which is where the view
   reads a dispatch's transcript: on 0.6.1 an operator opening a settled dispatch was
   shown its tool calls and nothing else.
   Comparing every conversation those runs carry, 20 differ between the two releases.
   On 0.6.1 each of them was served **one turn with no reply text at all** — a turn
   with a null `assistant`, a null `model`, and, in place of that turn's own usage, a
   copy of the whole dispatch's totals; 0.6.2 serves none, and gives every turn its
   own five figures and its own model — as does 0.6.3, whose answers on this route
   are byte-identical to 0.6.2's on every run compared above. **The accounting is the
   half worth keeping, because it is the half a reader cannot eyeball.** That extra
   turn was added on top of the turns already accounting for the dispatch, so per-turn
   figures a view adds up overstated it on all 20: on 17 of them 0.6.2's now sum *exactly* to what the
   dispatch's report records, and on 14 of those 17 the 0.6.1 sum was exactly double
   it — `dag-ui-conversation` read $38.85 against a report of $19.43. The other three
   overstate by less than double only because that release was **also dropping turns
   whole**, which is the same defect seen from the other side: `triage-by-root-cause-5`
   serves 20 turns carrying reply text against 0.6.1's 3, and
   `orchestrator-adopt-and-sweep` 16 against 5.
   That route needs something no other section here does, and it is why one fixture
   under `tests/fixtures/timeline-runs/` is checked in **with its `reports/`
   directory**: a run's events record that a turn happened, while what the agent said,
   what its tools observed, and what the turn cost live in the dispatch's report. A
   fixture without one serves empty turns at every release, so it can neither show
   this defect nor show it fixed.
   What is still absent is not the read API's to supply: there is no
   `runs/<run-id>/supervisory/` capture, no `conversation-turn` event, and no
   `history-write-failed` event on the adopted stack; the bounded local capture this
   document used to describe belonged to the pre-extraction dispatch layer and went
   with it. When a supervisory turn is missing from the timeline, the oneharness
   history store is where it is, and reading it is a manual step.

The upstream defect that motivated the capture is oneharness refusing a history
write with `new history run lacks complete v1.0 telemetry` (and the `cannot write vN
history telemetry` variants), raised in
`crates/oneharness-core/src/io/history.rs`. A dispatch that dies on one reaches the
run as a failure like any other; whether `onepipeline` classifies it specifically is
**not established here** — do not assume it does.

That refusal is not codex-specific, though it was assumed to be, and the assumption
sent a night's debugging at the wrong harness. A refused write leaves nothing behind,
so the store cannot exhibit one directly; what it does
show is that every one of codex's 7,642 run records is `ok` with complete native
telemetry, while claude-code supplies no native per-phase timing at all (`started_at`,
`finished_at`, `tool_ms`, `time_to_first_token_ms`, `model_ms` are absent from all
4,745 of its records) and carries every recorded failure. Those counts come from
reading `type: "run"` lines out of the history store; re-measure there rather than
inferring the harness from chain order.

## Diagnosing a provider failure

A node that dies to the provider says which side of the conversation refused,
which identity refused it, and why. `just status` and `just runs` carry that
without any further command, because the incident this exists for cost a night:
every death printed `provider error (respond): harness failed (quota)`, which
names neither.

1. Read the **provider health block** both views print. It is `oneagentgraph
   health`'s own JSON report, forwarded verbatim — that call is
   `oneharness_core::io::usage::report`, the `oneharness usage` verb as a library
   call, so the identities and their windows are oneharness's to define and nothing
   is re-assembled on the way through. Each identity carries its `harness`, the
   `selector` naming the indirection dispatch uses
   (`scripts/claude-alt-config-dir.sh`, `scripts/codex-alt-home.sh`), its
   `auth_mode` and `plan`, and an `availability` listing every window with
   `used_percent`, `resets_at`, and which one `is_binding`. Every one of those names is
   reconciled against `oneharness-core`'s own declaration on each `just check`, by
   `tests/test_engine_contracts.py::test_the_health_block_fields_are_the_turn_engines_own`
   — so a renamed field fails there rather than reaching an operator as an identity
   that looks unprobed. Every probe there is
   free: no harness takes a model turn. A health probe that cannot run at all is
   silence rather than a failure — the block is simply absent and the rest of the
   view still reports.
2. Read the failure line beneath each run. It names the **side** and the
   **identity**: `dispatch-appendix: failed — the agent side: identity
   'claude-code:alternate' refused (quota)`. Read the side first — the agent and
   judge chains prefer different identities, so a fix aimed at the wrong one
   changes nothing.
3. `just results <run-id>` carries the same attribution per node with the harness's
   own bounded output.

**Two things this document used to claim and cannot.** There is no
`ORCHESTRATOR_PROVIDER_HEALTH_PROBE` switch — it appears in no engine and in no
script here, so a host that must not probe has no documented way to say so. And
there are no `raw_tail`, `quota_at_launch`, `quota_mid_conversation`, or
`judge_unrecorded` fields: whether a refusal fell through to the next identity or
was bound to the refusing one has to be read from the chain order in the relevant
`oneharness.*.toml` against the health block above. Both are gaps to close
upstream, not settings to reach for.
