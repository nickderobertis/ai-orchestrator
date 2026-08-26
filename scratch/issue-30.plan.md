# Plan: close the ten root causes on ai-orchestrator issue 30

Launch file: `scratch/issue-30.plan.json` (8 nodes, 9 dispatches, `concurrency: 3`).
Validated: `just check-plan scratch/issue-30.plan.json` → exit 0, *"9 dispatched node(s)
state the bar they are judged against"*.

This document is the decomposition's reasoning. The plan file is the deliverable.

## Coverage: every root cause, and the node that closes it

| # | Root cause | Owner | Node | What that node owes for it |
| --- | --- | --- | --- | --- |
| 1 | `bunx nx` runs an unpinned Nx and leaks its native binary per worktree | ai-orchestrator | `nx-pinned-and-shared-cache` / step `pinned-nx` | The wrapper runs the lockfile's Nx; a drift check fails on a mismatch, read from a real invocation |
| 2 | Codex-served agent turns emit no normalized tool events | oneharness | `codex-app-server-events` | Recognize the `codex app-server` transcript so a **controlled** codex turn produces events |
| 3 | Run liveness reported without regard to settlement | onepipeline | `settled-run-advice` | The advice line under a row agrees with the word on it; `requeue` before `adopt` for a parked run |
| 4 | The simulated supervisor has no stated boundary on its authority | ai-orchestrator | `supervisor-bounds-and-monitor-silence` | A bounded-authority clause in `config/onejudge.base.yaml`'s `user.persona`, honestly weighed |
| 5 | `adopt` lacks the detach ergonomics `start` has | onepipeline | `adopt-detach` | `adopt` takes the same attach/detach pair, with adoption's own lock ordering respected |
| 6 | A dispatch pinned to a shared branch inherits that branch's judged surface | **not notignored** — see below | `judged-base-for-a-follow-up` | Measure which base a follow-up dispatch actually gets, in three shapes, and record it |
| 7 | Process start tokens drift, so every liveness claim decays | onepipeline | `liveness-token-and-honest-stop` | A recorded identity that does not decay **and** a stop that does not report success when every claim was declined |
| 8 | A publication is verified by the lender's working tree | onevcs | `carry-the-branch-hooks` | A publication clone runs the hooks of the branch it is landing |
| 9 | A monitor that correctly reports nothing is killed for it | ai-orchestrator | `supervisor-bounds-and-monitor-silence` | Three-way split: sentinel lives, prose surfaces, genuine silence still fails |
| 10 | The Nx computation cache is workspace-local | ai-orchestrator | `nx-pinned-and-shared-cache` / step `cache-measured` | The measured answer, the empty-key decision, and a re-measured cost |

Two root causes share a node in each direction, and each has a stated share:

- **#1 and #10** are one node of two sequential steps on one branch, because both change
  `scripts/nx.sh` and two concurrent branches editing that file would conflict for nothing.
  `pinned-nx` owes the pinned binary and the drift check; `cache-measured` owes the
  measurement, the empty-key decision, and the corrected prose.
- **#4 and #9** are one node, because both are this repository's supervisory contract and
  both edit the same two regions (`config/onejudge.base.yaml` and the supervisory paragraphs
  of `AGENTS.md`). #9 dominates the work; #4 is one clause plus the honest caveat.
- **#7** is one node carrying two independent demands, and the criteria state both
  separately so neither can be quietly dropped: the non-decaying identity, and the stop that
  refuses to report success when every claim was declined. The manager confirmed the second
  may live here rather than in its own node.

The issue's **open, not-yet-root-caused** item — `turn-activity` journalling appearing to
stop at an interrupt while the turn kept running — gets **no node**, by decision (below). It
is a criterion on `supervisor-bounds-and-monitor-silence`.

## The three findings that moved, and how the nodes are written against them

### #3 is half fixed upstream and the node must not re-do that half

`views.rs::liveness_word()` already returns `SETTLED` for a completed graph on `main`, so
the word on the row is right. What remains is the advice beneath it: `runs()` (~`views.rs:608`)
and `status()` (~`views.rs:646`) each gate on the raw `view.liveness().is_undriven()`. Read
directly on `main` while planning. The parked half is untouched:
`DriverLiveness::is_undriven()` folds `DriverDead | Parked` and both are prescribed `adopt`;
`requeue` appears nowhere in that path. The node's task says all of this, so the worker does
not spend a turn rediscovering that half the fix is already there.

### #7's suggested fix is partly pre-refuted, and my reading contradicts the brief's steer

The doc comment above `platform_process_start_token` already considers `/proc/<pid>/stat`
and rejects it as *"a platform fixed in only one of them"*, `ps` being the deliberate
portability compromise for macOS. So a node proposing `/proc` alone argues against a
decision already written down. Two things found while planning bear on it, and the manager
accepted both over the brief's original steer:

1. **The drift is a rate, not an offset** — about 0.8 s/min, so 33 s over 68 minutes. A
   tolerance small enough to keep pid reuse honest at a minute is useless against an
   hour-old record. A tolerance fix therefore has to scale with the record's age, which is a
   strictly larger design than the issue implies.
2. **The comment's premise may hold on only one platform.** `lstart` on macOS is understood
   to render a start time the kernel stores at process creation; Linux's `ps` re-derives it
   from the current clock less uptime, which is exactly the mechanism the issue measured and
   is consistent with `/proc/stat`'s `btime` being byte-identical across every read on the
   day `ps` wandered six seconds. If that holds, a kernel-fixed reading on Linux fixes the
   only broken platform and leaves the sound one alone — which **inverts** the portability
   objection rather than merely disagreeing with it.

The node is written to **state both arguments, verify the macOS premise itself rather than
inherit it, and be held only to the observable property** — a recorded identity that still
identifies its process hours later, a genuinely different process still refused, and the
argument made in the code it changes. That is the manager's answer (option (c) naming the
`/proc`-on-Linux route as the expected outcome). A reviewer meeting the change should expect
it to argue against the existing comment; that is intended, not an oversight.

`onepipeline`'s merge path requires `cross (macos-latest)`, so the platform half of this
change is verified by a required check rather than by assertion.

### #10's stated cause does not hold here, and the node says so first

Re-measured on this host on 2026-08-26, and the brief's numbers confirmed with one
correction:

- `scripts/nx.sh:36` exports `NX_CACHE_DIRECTORY` to
  `${XDG_CACHE_HOME:-$HOME/.cache}/ai-orchestrator/nx/<16 hex of sha256 of the origin url>`,
  overriding `nx.json` on every path through the wrapper.
- `.nx/cache` does not exist in this checkout; `.nx` holds `workspace-data` and llmlint-diff
  state only.
- The canonical checkout, `ai-orchestrator-isolated`, and this planner's own `onevcs` run
  clone **all** resolve to `756e79f93796751d` — 80 MB, 1,951 entries, a recorded
  `local-cache-hit` on 2026-08-25. Publication clones already share the cache by directory.
- The cache root holds **161 key directories, of which 157 are empty** (the brief said 159;
  four are non-empty today). The empties come from the fallback to
  `git rev-parse --show-toplevel` for a checkout with no `origin`, which is what the e2e
  journeys' copies are.

So "delete the `cacheDirectory` line" is not the fix, and the node's task says that in its
first sentence, with the warning that a dispatch following that instruction would change
nothing and report success. What the step owes instead is the empty-key decision, a
re-measured cost, and corrected prose.

## Decisions this plan makes, with their reasons

Six were put to the manager and answered; the rest I decided.

1. **#6 is in scope, as an ai-orchestrator measurement node.** The issue files it under
   `notignored`; that is where it was observed, not what decides it. What decides the judged
   base is `ONEVCS_COMPARISON_REMOTE` / `ONEVCS_COMPARISON_BASE`, exported by
   `crates/onevcs/src/merge_path.rs` and read here by `scripts/comparison-base.sh`, with
   `scripts/llmlint-fingerprint.sh` folding the resolved base into the cached tier's key. The
   node measures which base a follow-up gets in three shapes and records the answer; a onevcs
   change is a follow-up it **names** rather than performs, because it cannot be specified
   before the measurement. Manager confirmed.
2. **#2 is scoped as a bounded fix, not a bug report, and its cause is not the issue's.**
   The predicate is not "codex-served" but **controlled**: `ControlShape::CodexAppServer`
   drives a controlled codex turn over the `codex app-server` JSON-RPC protocol instead of
   `codex exec --json`, and `oneharness`'s own note says it drives the turn itself for every
   mechanism *except* Claude Code — which is exactly why claude-code turns kept their 348
   events under the same control setting. `domain/events.rs`'s codex recognizer keys on
   `exec --json`'s `item.started` / `item.completed`; the app-server's frames are
   method-keyed (`tests/fixtures/codex-app-server-usage-limit.jsonl` is a real capture from a
   `--control` turn). Since turn control is live on every dispatch on this host, this is the
   common case rather than a corner. The node adds an app-server recognizer sourced from a
   real capture through `scripts/explore-events.sh`, the repository's own pattern. The task
   keeps an honest escape: if a real capture shows the protocol exposes no tool activity at
   all, the deliverable becomes that finding with its evidence — but reporting a fix that
   leaves the events absent is refused by a criterion. The manager is correcting the issue
   record; no node does that.
3. **Three separate onepipeline nodes, not one node of three steps.** They touch different
   functions, they release under different conventional types (`fix`, `feat`, `fix`), and one
   squashed publication would collapse three release-note lines into one for a published
   library. The cost is three branches against a base already moving under `adopt-onevcs-0152`
   and `blank-drafting-windows`; concurrent lifecycle dispatches on one identity are supported
   on this stack, and `change-auto` merges each as its checks go green. Manager confirmed.
   The three are kept disjoint in the files they touch — `views.rs` for #3, `cli.rs` plus
   adoption in `driver.rs` for #5, `sys.rs` plus the claim and teardown paths in `driver.rs`
   for #7.
4. **No node for the open item.** onepipeline's next release is already queued as open change
   request #124 (*"chore: release v0.15.0"*), which carries #127 with #121, #125 and #126, so
   adopting it is the ordinary release-adoption workstream and a node here would duplicate
   `adopt-onevcs-0152`. Instead `supervisor-bounds-and-monitor-silence` owes a criterion
   recording #127 as the **candidate** explanation — the mechanism is not isolated — with the
   interim guidance already in use: read a dispatch's worktree before believing its event
   stream. Manager confirmed.
5. **No engine-repo cache nodes for #10.** Measured: `onepipeline`, `onevcs`, `oneagentgraph`
   and `onepipeline-ui` **already** invoke `node_modules/.bin/nx` (so #1 does not apply to
   them) but do use workspace-local `.nx/cache` with no override, so #10's stated cause does
   hold there — while their caches are 0.3–1.1 MB against 80 MB for the ai-orchestrator key.
   Not a cost worth four more change requests. `oneharness` has no Nx at all. What that
   finding *is* worth is in `pinned-nx`'s task: four sibling repositories are a working
   reference implementation of #1's fix, named by path, so the node adapts one rather than
   designing from scratch. Manager confirmed.
6. **Every node is `persona: engineer`.** All eight change tracked files, and `just
   check-plan` refuses `researcher` under criteria that require a file to change — the
   pairing that cost a run and a relaunch. There is no test-only persona and none is wanted:
   each node owns the tests that prove its own change.
7. **The three ai-orchestrator nodes each name the `AGENTS.md` region they may edit**, in
   their task prose, because three concurrent branches editing one file is the one predictable
   conflict in this plan. `nx-pinned-and-shared-cache` owns the Nx wrapper and cache
   paragraphs; `supervisor-bounds-and-monitor-silence` owns the supervisory and personas
   material; `judged-base-for-a-follow-up` owns the judged-tier and comparison-base material
   and lands its answer in `docs/repo-lifecycle.md`.
8. **Execution checkouts are assigned rather than left to default**, per this repository's
   self-dispatch rule: `ai-orchestrator-isolated` for the Nx and judged-base nodes,
   `ai-orchestrator-isolated-2` for the supervisory node. Both are registered and were clean
   on `main` when the plan was written. `repo` is the canonical `ai-orchestrator` publication
   checkout in every case.
9. **`repo_type`, `workflow` and `merge_policy` are omitted from every node.** The routing is
   the rules file's, and `onevcs rules check` is the authority. Stating them in the plan would
   be a second answer to a question already answered.
10. **`max_turns` is stated on every dispatch** (18–28), against the base config's shared
    default of 12 supervisor rounds. Twelve rounds is thin for a foreign codebase with a
    ~28-minute complete gate at the end; the heavier nodes (#7's two halves, #2's live
    capture) carry 28. These are ceilings, not budgets to spend.
11. **`concurrency: 3`.** Eight independent nodes could all run at once; three keeps the host
    kind to the two other managers' live runs and to the provider quota, and still finishes
    the graph in three waves.
12. **Every node and every step names its dependencies explicitly, and all but one name an
    empty list.** Nothing in this plan is a real prerequisite of anything else, so seven
    top-level nodes and one step carry `"deps": []` rather than omitting the key — an omitted
    key says nothing about whether the question was asked. The one real edge is inside
    `nx-pinned-and-shared-cache`, where `cache-measured` carries `"deps": ["pinned-nx"]`
    because it genuinely follows that step on one branch. No top-level node depends on
    another: these are eight independent changes across four repositories, and a dependency
    edge added only to serialize them would be a scheduling edge wearing a prerequisite's
    clothes.

## Contracts

There is no cross-repository interface seam in this plan: every node changes one repository's
internals and no node consumes another node's output. So no node declares `adoption` or
`consumes`, and none needs to — no repository registered on this host declares a release
target, so both fields resolve to `fast` with nothing to await. The one place a shape is
*shared* is #9's sentinel, which is a contract between `personas/orchestrator.yaml` and
`scripts/channel-serve.py` — both inside one node, which is why it is one node.

## Corrections to the brief, carried into the plan

- **`onevcs rules check` resolves `change-auto` for all three engine identities**, not the
  ready-for-review open pull request the brief described. Each node's change request merges
  itself once its required checks pass. Manager accepted; the plan is written against
  `change-auto`.
- **`onepipeline` `main` is four commits past `v0.14.2`, not two** — #121, #127, #126 and
  #125 — and a release change request (#124) for v0.15.0 is already open. The three
  onepipeline nodes branch from a `main` ahead of the release this host runs, and should
  expect it to move again under them.
- **The cache root holds 157 empty key directories, not 159.** Four are non-empty today; the
  count moves as journeys run.
- **`onevcs` `main` is at `v0.15.2`**, and `carry_hooks` is byte-identical to what the issue
  quotes.

## Risks the manager should hold

- **`onepipeline` base churn.** Three of this plan's nodes branch from a repository two other
  runs are already changing. None of them should touch `Cargo.toml`/`Cargo.lock` version pins
  or `tests/e2e/node_validator.rs`; if one turns out to need to, that is a proposal to the
  manager rather than a change to make — say so and keep building against the agreed surface.
- **`AGENTS.md` three ways.** Mitigated by naming each node's region, not eliminated. A
  `sync-conflict` on one of the three ai-orchestrator nodes is the expected shape and is
  retried by the engine onto the same branch.
- **`carry-the-branch-hooks` changes what verifies a change.** That is squarely in scope —
  it is #8's whole subject — but it is the one node in this plan whose change alters the
  merge path itself, and it lands in the crate every publication on this host runs. Worth
  reading its change request rather than letting `change-auto` be the only reader.
- **#2 may cost a paid turn** to take a real capture. The node's criteria permit driving the
  proof against a recorded capture through the live path where a paid turn cannot be spent
  inside the checks, provided the live capture is recorded.

## Follow-ups this plan names but does not perform

- A `onevcs` change to the base exported to a follow-up dispatch, **if**
  `judged-base-for-a-follow-up`'s measurement shows that is where the fix belongs. It cannot
  be specified before that measurement, which is why it is not a node.
- Whatever `cache-measured`'s re-measured gate cost justifies, if it turns out the cost is
  real and the cause is something neither the issue nor this plan has named.
