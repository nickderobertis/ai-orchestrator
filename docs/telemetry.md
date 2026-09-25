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
re-measured against `onepipeline` v0.45.0 on this host's own runs root; the per-node
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
runs root on 2026-08-27, where all **207** recorded runs balance to the millisecond,
which is worth saying because it is an invariant the crate documented before it upheld it:
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
after the 0.16.2/0.14.0 upgrade (`config/oneharness.version` and
`config/onejudge.version`), which is the boundary the older per-party accounting
sat behind. What a run recorded *before* that pair reports is **not established
here** — re-measure rather than assuming the shape carries backwards, and re-check
this paragraph whenever either pin moves, which
`tests/test_onejudge_version.py::test_telemetry_upgrade_boundary_matches_authoritative_versions`
forces by failing on the boundary sentence above until the pair it names is the adopted
one. That re-check has now been made thirteen times
without the shape moving. For the 0.10.3/0.5.1 upgrade, one turn spent on each binary
with everything else held differed by exactly two added keys — `results[].work` and
`fallback.stopped_without_work`, both of which say something about a failure nothing
could classify. For two of the five since, it was re-taken by spending a real
`oneagentgraph smoke` turn on the adopted pair, through this repository's own agent
wrapper and with `ONEHARNESS_HISTORY_DIR` pointed at a throwaway store, and reading the
record it wrote: at history
schema 1.1 the `usage` block is `input_tokens`, `output_tokens`, `cache_read_tokens`,
`cache_write_tokens`, `cost_usd` and nothing else, on the candidate that answered. Both
of those turns' chains selected `claude-code:alternate` first and so wrote no
fallen-through record to compare, which the earlier re-take of the same measurement did.
The oneharness half of the bump three before this one is a **classification** change — a completed
billed turn is no longer reported as a failure — so what it could have moved is
`status` and `failure_kind` on the record rather than the `usage` block, and neither
moved on a turn that succeeded. The oneharness half of the bump two before this one adds one
field beside the block rather than inside it: a record carrying `observed_model` — the
model the harness itself reported a controlled turn would run under — or classified
`model_mismatch` declares history schema **1.8**
(https://github.com/nickderobertis/oneharness/pull/1284), and the `usage` block is
untouched between the two tags. That one was re-taken without a paid turn, because the
turn that shows the field is the one `tests/e2e/test_controlled_turn_model_e2e.py`
drives offline: a controlled codex turn against the refusing loopback endpoint, whose
result carries `observed_model` beside the same five-key `usage` block. The oneharness
half of the bump before this one, 0.12.1 to 0.14.0, was re-taken without a paid turn, off
a record the pinned CLI wrote on this host for a candidate it skipped: at history schema
1.3 the `usage` block is the same five keys and nothing else, each null because nothing
ran. What that release adds sits on the CLI's stdout
(https://github.com/nickderobertis/oneharness/pull/1312), which no record is written
through. The oneharness half of this pin's own bump, 0.14.0 to 0.15.0, was re-taken the
same way and without a paid turn, off the two records one fallback chain wrote on this
host under the pinned CLI with `ONEHARNESS_HISTORY_DIR` pointed at a throwaway store — a
`skipped` candidate at history schema 1.3 and the `ok` one behind it, answered by a spy
binary, at 1.1 — and each carries the same five keys and nothing else. What that release
flips is the CLI's stdout default and the run-mode default
(https://github.com/nickderobertis/oneharness/pull/1316): the first is a rendering no
record is written through, and the second reaches no record here because every
`oneharness.*.toml` sets `run_mode` explicitly. The onejudge
half of the bump after that one, 0.8.1 to 0.10.0, moves no record writer at all: the
`oneharness-core` a record is written through is 0.13.1 in the engine wheel and
0.13.0 in the `onejudge-cli` wheel on both sides of it, read off each wheel's own
SBOM, and `tests/test_linked_libraries.py` holds both numbers — so it too was
re-taken without a turn. Nothing an accounting reader reads is
renamed, retyped, or re-meant, which is why the boundary sentence names the pair
these records are written under rather than the older one they were first taken on.
The onejudge half of that pair has since moved to 0.10.0 with the oneharness half and
the `oneharness-core` both engines link unchanged, so the record the `usage` block is
read from is written by the same release as before; what that onejudge release adds is a
judge side that can be a list, whose judges' usage it **sums** into the one `judge` party
(onejudge's own `judges.md`: *Usage is summed across judges*) — and this host stacks
none, so `judge` still counts one simulated user. It has since moved to 0.11.0 on the
same terms: `onejudge-cli` 0.11.0 is compiled against `oneharness-core` 0.13.0 and the
engine wheel still links 0.13.1, read off each wheel's own SBOM, and the report onejudge
writes stays at schema 12 on both tags; what 0.11.0 adds is `user.artifacts`, which names
files in a judge's prompt and writes nothing into a record's `usage`. It has since moved
to 0.12.0 on the same terms: `onejudge-cli` 0.12.0 is still compiled against
`oneharness-core` 0.13.0 and the engine wheel still links 0.13.1, read off each wheel's
own SBOM, and neither `crates/onejudge/src/report.rs` (schema 12) nor
`crates/onejudge/src/usage.rs` changes between the two tags; what 0.12.0 changes is where
its note contract and frame protocol come from — `onemessagebus` 0.4.0 — which writes
nothing into a record's `usage`. It has since moved to 0.13.1 on the same terms:
`crates/onejudge/src/report.rs` (schema 12) and `crates/onejudge/src/usage.rs` are
unchanged between the two tags; `onejudge-cli` 0.13.1 is compiled against
`oneharness-core` 0.14.0 and the engine wheel links 0.14.1, read off each wheel's own SBOM,
and the `Usage` those write is the same five fields as before — history schema 1.9 admits a
`server_overloaded` failure kind beside the block rather than inside it; what 0.13.1 adds
is the `turn` outcome on a `supervisor` frame, which writes nothing into a record's `usage`.
It has since moved to 0.13.2 on the same terms: neither `crates/onejudge/src/report.rs`
(schema 12) nor `crates/onejudge/src/usage.rs` changes between the two tags, `onejudge-cli`
0.13.2 is still compiled against `oneharness-core` 0.14.0 and the engine wheel still links
0.14.1, read off each wheel's own SBOM; what 0.13.2 changes is how it asks the spawned
`oneharness` for its report — explicitly as JSON — which writes nothing into a record's
`usage`. It has since moved to 0.13.3, and the oneharness half with it to 0.16.0, on the
same terms and this time together: neither `crates/onejudge/src/report.rs` (schema 12) nor
`crates/onejudge/src/usage.rs` changes between v0.13.2 and v0.13.3, which is the
`oneharness-core` relink and nothing else; `oneharness-core`'s own history
`SCHEMA_VERSION` is `1.9` at both v0.15.0 and v0.16.0, and what changes in
`crates/oneharness-core/src/domain/usage.rs` between them is rustdoc prose — intra-doc
links rewritten to name the private shapes they pointed at — with no field added, removed
or retyped. `onejudge-cli` 0.13.3 is compiled against `oneharness-core` 0.17.0 and the
engine wheel links 0.17.0 as well, read off each wheel's own SBOM. What 0.16.0 adds is the
**per-run pointer line** ([oneharness#1326](https://github.com/nickderobertis/oneharness/pull/1326)),
which is a line in a file of its own — the run's `oneharness-sessions.jsonl` — rather than
a field in a record, so the accounting block this section reads is untouched by it.
The oneharness half then moved to 0.16.1 on the same terms: nothing under
`crates/oneharness-core/src/domain/` changes between v0.16.0 and v0.16.1. What 0.16.1
changes ([oneharness#1344](https://github.com/nickderobertis/oneharness/pull/1344)) is the
process exit code for a one-candidate selection that cannot run, a base-id `--bin`
override reaching a variant, and which usage a refused `--format text --compact` prints —
none of it written into a record's `usage`. The onejudge half then moved to 0.13.4 on
the same terms: nothing under `crates/onejudge/src/` changes between v0.13.3 and v0.13.4,
which relinks the generic `onemessagebus` 0.8.0 and nothing else. `onejudge-cli` 0.13.4 is
still compiled against `oneharness-core` 0.17.0 while the engine wheel then linked 0.17.1,
read off each wheel's own SBOM, and nothing under `crates/oneharness-core/src/domain/`
changes between `oneharness-core-v0.17.0` and `oneharness-core-v0.17.1`.
The pair has since moved again, to 0.16.2/0.13.5, for the `extends` chain a config file
may now name — and the accounting block is untouched by it on the same terms. Nothing
under `crates/onejudge/src/` changes between v0.13.4 and v0.13.5
([onejudge#110](https://github.com/nickderobertis/onejudge/pull/110)), which relinks the
`oneharness-core` and nothing else. What that core adds
([oneharness#1349](https://github.com/nickderobertis/oneharness/pull/1349)) is confined
to `domain/config.rs`, `io/config.rs` and `io/init.rs`: an `ExtendsPath` and the layered
read that resolves it. `crates/oneharness-core/src/domain/usage.rs` is byte-identical
between `oneharness-core-v0.17.1` and `oneharness-core-v0.18.0`, and `onejudge-cli`
0.13.5, `oneagentgraph-cli` 0.4.10 and the engine wheel then all linked 0.18.0, read off
each wheel's own SBOM. The onejudge half then moved to 0.14.0 on the same terms: between
v0.13.5 and v0.14.0 only `crates/onejudge/src/note.rs` and `sdk_schema.rs` change
([onejudge#112](https://github.com/nickderobertis/onejudge/pull/112)), taking the note
contract off the retired bus agent crate, and `onejudge-cli` 0.14.0, `oneagentgraph-cli`
0.5.2 and the engine wheel all still link `oneharness-core` 0.18.0, read off each wheel's
own SBOM.
`dispatches`,
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
   (`DRIVER DEAD` and `PARKED`), and a run stopped and then adopted reads `ACTIVE`
   under its adopting driver rather than carrying the stop forward. The **observer** verdict is separate and prints
   beside it: `OBSERVER DEAD` when the launch named an observer graph whose run has
   ended, `OBSERVER NOT RESTARTED` when it named one, that graph run is over, and the
   driver has stopped starting another, `NO OBSERVER` when it named none,
   and nothing at all while it is
   watching. A run can read `ACTIVE  OBSERVER DEAD` — driving fine, unwatched — and
   that is a different fix from a dead driver. **The first two are not the same
   state.** `OBSERVER DEAD` is the window between an observer's graph run ending and
   the driver starting another, so it may clear on its own; `OBSERVER NOT RESTARTED`
   is the driver having given up, with the record saying why, and nothing is going to
   watch this run again — which is the one an operator acts on rather than waits out.
2. **The run timeline** (`GET /api/v2/runs/{run}/timeline?scope=run`, served by
   `just telemetry-server` — or by `just dag-ui`, which is the same published server
   with the browser view built into it answering on the same origin) is the structured
   view. Measured against real runs on
   **`onepipeline-api` 0.13.0**, the release `config/onepipeline-ui.version` pins —
   a measurement rather than a reading, because its CLI dumps no schema, so a bump is
   what re-opens this paragraph: `telemetry_schema_version` 20 on the envelope, unmoved
   across this bump, where
   0.11.0 served 19, 0.9.0 served 17, 0.7.3 served 16 and 0.7.2 served 15; `timeline_schema_version` 10,
   unmoved across this bump too, where 0.7.3 served
   8 (`tests/dag_ui/test_dag_ui_serving_e2e.py` holds both numbers to the reader's
   answer); spans of kind `run`, `dispatch`, `node`,
   `rollup`, `verification`, `publication`, and `human-wait`, each with `started_at`
   and an `ended_at` that is `null` while it is open. The `run` span carries `phase`,
   which read `starting`, `waiting`, `surfacing`, `settled`, and `finished` across the
   runs read here; no run read served the `dispatching` this paragraph used to name.
   On 0.12.1 this was re-read against the recorded runs under
   `tests/fixtures/timeline-runs/`, which serve both schema numbers, every phase above,
   and every span kind but `human-wait`, which none of those runs records.
   **A lane is a member the run's own graphs declared**, from 0.9.0: a session's
   `agent_role` is the member name the run recorded for it, served only where one of
   the run's recorded graph declarations — the `record.json` `oneagentgraph` keeps per
   graph run, read from `ONEAGENTGRAPH_STATE_DIR` — names that member, and absent
   otherwise; nothing is built into the reader, so a conversation's `agentRole` is
   optional. The recorded runs predate this host keeping those records beside them, so
   the journey serves them with the records their graph documents imply, written at test
   time from the members each document under `graphs/` declares.
   **The dispatch tier is two span kinds, and confusing them is the easy mistake.** A
   **`dispatch`** span is one supervisory conversation, parented on the *run*, with an
   `agent_role` of `monitor` or `check-in` (the pacemaker) — the monitor was served as
   `orchestrator` before 0.9.0 — a
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
   `tests/dag_ui/test_dag_ui_serving_e2e.py` serves six checked-in runs to assert: every
   span kind above except `human-wait`, each one's fields and parentage, the `waiting`,
   `surfacing`, `settled`, and `finished` phases, both the `open` and `merged`
   publication statuses, and both supervisory `agent_role`s. One of those runs is
   **derived rather than recorded**, and the only one here that is: `dag-ui-truth` is
   the sole run on this
   host whose *monitor* member ever completed a turn — which is what makes a `monitor`
   label exist at all — and it is 8.9MB of journal whose worker
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
   **The default listing is the live runs, and a settled one leaves it — but the
   filter is on the run's `state`, not on its run span's `phase`.** Reading those two
   as one is the mistake to avoid: over this host's own 454-run root `GET
   /api/v2/runs` carried no run whose `state` was `settled` and *did* carry runs whose
   `state` was `driver-dead` and whose run-span `phase` read `finished`, alongside
   `waiting`, `surfacing` and `deciding`. A settled run's timeline is still served in
   full by id, so a run an operator knows finished and cannot see in the list is this
   rather than a reader that lost it — and **`?include_settled=true` is how the rest
   are asked for**, which is what the browser bundle sends. What no parameter changes
   is the page size: `limit` is capped at 50, so the whole of a 385-run answer is
   eight cursor-paged requests.
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
3. **What 0.6.3 changed here, and it is one number; what 0.6.5 changed is on the
   other route.** Serving the same runs from
   0.6.2 and 0.6.3 side by side is what dates this section. The whole 0.6.3 delta is
   `timeline_schema_version` 6 becoming 7: on the six runs checked in under
   `tests/fixtures/timeline-runs/` and on `dag-ui-truth`, `issue-27`, and
   `pr-author-body` from this host's own runs root, every other byte of the timeline
   response was identical, and the conversation route was byte-identical too. That is
   what the release *adds* rather than what it changes — 0.6.3 renders which release
   carried each landed node, and no run on this host has a release event in it,
   because `ai-orchestrator` declares no release target and no plan launched from here
   has yet named `adoption` or `consumes` for a node in one of the six registered
   repositories that do declare one. So a reader who
   opens a node and finds no release row is looking at an undeclared target, not an
   unadopted release. `tests/dag_ui/test_dag_ui_serving_e2e.py` holds the rendering half —
   a browser opened on a real recorded run draws no release row from data that has
   none — and `tests/e2e/test_release_adoption_in_force_e2e.py` holds the half about
   this host, by asking every registered identity whether it declares a target.
   **0.6.5 — an earlier release, and one of the two whose whole delta is on the other
   route — leaves the timeline route alone and repairs the conversation route**, and the two halves of that were
   measured side by side by serving one runs root from a 0.6.4 and a 0.6.5
   `onepipeline-api` at once. The timeline response is
   byte-identical on all six checked-in fixtures and on this host's own
   `dag-ui-observability-2` and `report-shape`, at `telemetry_schema_version` 14 and
   `timeline_schema_version` 7 on both. The conversation route is byte-identical
   wherever a dispatch had one party — every conversation the six fixtures carry —
   and **halves** where a dispatch had two: `report-shape`'s
   `node-scope-1787485697614-425057.worker` is served as **8 turns by 0.6.4 and 4 by
   0.6.5**, because the supervisor's relayed side was being read as rows of its own.
   Each duplicate row carried the *previous* turn's reply as its `user`, an empty
   `assistant`, a null `model` and no usage at all, so a reader saw the agent's answer
   again on the user side and the transcript ran at twice its length. 0.6.5 serves the
   agent's side as the transcript, and gives each of the four turns its own `model`
   — `claude-opus-5`, where 0.6.4 served null on every turn of that conversation. The
   accounting is unmoved: the duplicate rows carried no usage, so the per-turn `costUsd`
   figures sum to the same total on both. That is the repair
   https://github.com/nickderobertis/onepipeline-ui/pull/44 describes, met on this
   host's own data.
   **What 0.7.2 changed for a reader is one number, and the rest of that release is
   the browser bundle's.** Serving one runs root from a 0.7.0 and a 0.7.2
   `onepipeline-api` at once — the three runs under `tests/fixtures/timeline-runs/`
   that carry a run-scope timeline — the whole delta is `timeline_schema_version` 7
   becoming 8. Every other byte of the run-scope timeline is identical, the
   conversation route is byte-identical, `/api/v2/runs` is identical but for its own
   `observed_at` clock, `telemetry_schema_version` stays 15, and both readers answer
   `/healthz` with `{"status":"ok","onepipeline_version":"0.19.0"}` — the same linked
   engine, so nothing about what these runs are read *through* moved. What 0.7.1 added
   is the reason for the number: a ready node's queue is served as spans, and none of
   these recorded runs has a node still waiting to be dispatched, so there is nothing
   here for it to serve. 0.7.2's own addition — one flat run list, a row refreshed by
   name, a live run that opens — is entirely the npm bundle's; the run list this reader
   serves it from did not move. `tests/dag_ui/test_dag_ui_serving_e2e.py` holds the
   number, so a reader that moved it again fails there.
   **Both those releases carry a `release-status: publish-failed` banner and both are
   nonetheless published**, which is worth recognising rather than re-diagnosing: the
   job that failed on each is `verify-npm`, a check *after* the publish, and a later
   `Published smoke` run succeeded. npm serves `onepipeline-ui` 0.7.1 and 0.7.2 and
   PyPI serves `onepipeline-api-cli` 0.7.1 and 0.7.2 — and this host installed the pair
   and drove them, which is the evidence the banner is about propagation rather than
   about the artifacts.
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
   history store is where it is, and reading it is a manual step — but no longer a
   search: the run's pointer file, below, is what says which sessions are its.

**Where a dispatch's transcripts live, and how a run's are found.** Nothing about the
first half changed at the adoption that added the second, and that is the point worth
stating plainly: every dispatch launched from here goes on recording its oneharness
sessions in **this host's own default store**, `$XDG_STATE_HOME/oneharness/history/`,
and `oneharness history list` still lists them there. The engine sets no
`ONEHARNESS_HISTORY_DIR` — an inherited one passes through untouched and a repository's
own `history_dir` is honoured — so an operator's store is where it always was, and any
claim that a dispatch's sessions move under the run root would be wrong. What the
adoption adds is a way to ask *which of them belong to one run*: each run additionally
holds `<run root>/oneharness-sessions.jsonl`, one typed line per harness run naming the
session it wrote, and every one of the engine's `onepipeline.*` labels is stamped there
beside this host's own `role`. So `just agents <run-id> [<node>]` answers a run's
sessions from that file, `oneharness history pointers <run root>/oneharness-sessions.jsonl`
reads the same file with the producing library's own verb, and
`oneharness history watch --label onepipeline.run_id=<run-id>` follows them live in the
default store.

**Two pins decide whether that file is whole, and moving one alone shows half the
agents.** `config/oneharness.version` is what makes this host's **two-party** turns
write a pointer line — the agent and judge sides of every worker spawn that CLI from
`PATH` — and `config/onepipeline.version` is what makes the observer's and the
drafter's **in-process** turns write one, since those run through the core the engine
links. A reader who moved the engine pin and left the CLI pin behind gets a pointer
file holding the graphs and none of the workers.

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
