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
re-measured against `onepipeline` v0.10.1 on this host's own runs root; the per-node
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
set and sum exactly to it** — and the set below is reconciled against
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
| `gate` | `gate-started` |
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

`gate` is the span from `onevcs`'s `gate-started` to its `gate-verdict`. Those
events are emitted **only where the resolved policy names a `{command: [...]}`
gate** — `gate::own_command` returns nothing for `{kind: pre-push}` and
`{kind: checks}`, so `onevcs`'s verify step is a no-op and no span opens.

**Every rule in `config/onevcs.rules.yml` on this host currently names a `command:`
gate**, so `gate` is populated for new runs and is often the largest bucket after
`agent`: the `fix86-llmlint` run above spent 21m28s — 42% of its wall clock — there.
It reads `0` for a run whose nodes published nothing, and it would read `0` for an
identity routed to `{kind: pre-push}`, whose cost would land in
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
after the 0.10.2/0.4.0 upgrade (`config/oneharness.version` and
`config/onejudge.version`), which is the boundary the older per-party accounting
sat behind. What a run recorded *before* that pair reports is **not established
here** — re-measure rather than assuming the shape carries backwards, and re-check
this paragraph whenever either pin moves. `dispatches`,
`settled_done`, `no_diff`, `surfaces_queued`, and `surfaces_read` are the run's own
counters; `surfaces_read` is what resets the planner-update pacemaker.

## Finding an optimization target

1. Run `just telemetry --breakdown` and start with the largest `WALL`.
2. Compare `agent` with `gate`. A run that is mostly `agent` is prompt, context, or
   turn-count work; one that is mostly `gate` is paying for the repository's own
   bar, and `just lint-llm-diff`'s cached verdict is the lever there rather than
   anything in this view.
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
5. **For a failed gate, read the node's settlement detail and the session's own
   `gate-verdict`.** The detail is `onevcs: <command> rejected "<branch>"`, and the
   verdict event carries the command, the verdict, the output, a `preserved_log`
   path that outlives the run's worktree, and the whole run again as an artifact
   (`onevcs artifact`). The read API serves the same run as a `verification` span
   whose `detail.output_tail` is the tail of it and whose `detail.artifact_id`
   opens the rest — see [Seeing the supervisory
   tier](#seeing-the-supervisory-tier). There is no `merge-gate-coverage` or
   `verification-finished` *event*, and no `artifacts.gate_log` on the node
   result.
6. **For a failure that never reached a gate** — a base advanced under the
   publication, a fetch or a worktree that could not be built — there is no gate
   span and no preserved log, because none was produced. The whole account is the
   same settlement detail, carrying `onevcs`'s own reason. Those two no longer settle
   alike: a rejected gate is terminal and settles `publication-failed`, while a base
   that moved under the publication is `sync-conflict` — a word of its own, reached
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
   **`onepipeline-api` 0.6.1**, the release `config/onepipeline-ui.version` pins —
   a measurement rather than a reading, because that crate has no registered checkout
   on this host and its CLI dumps no schema, so a bump is what re-opens this
   paragraph: `timeline_schema_version` 6, spans of kind `run`, `dispatch`, `node`,
   `rollup`, `verification`, `publication`, and `human-wait`, each with `started_at`
   and an `ended_at` that is `null` while it is open. The `run` span carries `phase`,
   which read `waiting`, `deciding`, `surfacing`, `settled`, and `finished` across the
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
   `tests/e2e/test_dag_ui_serving_e2e.py` serves five checked-in runs to assert: every
   span kind above except `human-wait`, each one's fields and parentage, the `waiting`,
   `settled`, and `finished` phases, both the `open` and `merged` publication statuses,
   and both supervisory `agent_role`s. The fifth of those runs is **derived rather than
   recorded**, and the only one here that is: `dag-ui-truth` is the sole run on this
   host whose *monitor* member ever completed a turn — which is what makes an
   `orchestrator` label exist at all — and it is 8.9MB of journal whose worker
   transcripts quote an `llmlint: ignore` directive the linter then reads as a real
   one, so the whole run cannot be checked in. `dag-ui-truth-monitor-slice` is the six
   events of its dag-scope graph, kept verbatim; the fixture's own docstring records
   what was dropped and the one field rewritten. Four values are still observed and
   unheld: `deciding`, `surfacing` and `conflict` off `dag-ui-truth` and `issue-27`,
   and `human-wait` off `pr-author-body`. A bump re-measures those four by hand against
   this host's runs root; everything else fails the gate on its own.
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
3. **What 0.6.1 changed, and what is still missing.** Serving the same runs from
   0.5.0 and 0.6.1 side by side is what dates this section, and the delta is the
   reason the pin moved. On `dag-ui-truth` the older release served **no `worker`
   rollup at all** and left the monitor's `dispatch` span with a null `agent_role`,
   so the tier this section is about was invisible in the one view built to show it;
   0.6.1 serves 26 worker rollups for that run and labels that dispatch
   `orchestrator`, and `issue-27` reproduces the same thing at 0 against 42. **Span
   bounds moved with them, so a duration read off the older release is not
   comparable.** 0.5.0 opened a `publication` span per session and never closed the
   superseded ones — 26 of them on `dag-ui-truth` against 0.6.1's 8, the 18 dropped
   all open-ended and status-less — and bounded the survivors by the node that
   started the work rather than by the publication: `publication.workspace-staleness`
   ran 20:24:50Z to never at 0.5.0 and runs 11:54:16Z to 12:07:15Z at 0.6.1. The
   `pr-author` rollups moved the same way, from the node's dispatch window onto the
   drafting turn itself.
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
   `used_percent`, `resets_at`, and which one `is_binding`.
   <!-- llmlint: ignore[contracts_have_one_source_or_a_drift_gate] These field names
   are `oneharness_core::io::usage::report`'s, and this host cannot resolve one
   authoritative declaration for them: the adopted engine wheel's SBOM declares
   `oneharness-core` at two versions at once (0.10.1 and 0.8.0), and the registered
   `oneharness` checkout's tags stop at v0.9.0, so neither is fetchable to read. Every
   other engine contract in these documents is reconciled against source; this one is
   a restatement of a forwarded JSON shape, and gating it would mean pinning it to a
   ref nothing here can name. Tracked as follow-up. --> Every probe there is
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
