<!-- llmlint: ignore-file[determinism_vs_judgment] Repo discovery requires judgment across existing interfaces. -->
<!-- llmlint: ignore-file[no_redundant_instruction_pointers] The planner/orchestrator split requires a direct pointer to its live-channel operating contract. -->
<!-- llmlint: ignore-file[agents_md_durable_and_terse] Human-node eligibility is durable planner judgment and needs concrete modeling guidance here. -->
<!-- llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] Human-node misuse is intentionally rejected at planning, node-definition, and reviewer boundaries. -->

# AGENTS.md

Durable instructions for the **planner** and any agent working in this repo.
Write for a future maintainer, not as a session log. Deterministic steps live in
`orchestrator/` (run via `just`); this file holds the judgment.

> `CLAUDE.md` is a symlink to this file — edit `AGENTS.md` only.

## What this repo is

A local **orchestration harness**: you (the planner) take one large task, split it
into a dependency graph of smaller tasks, and review its execution. `just
orchestrate` delegates scheduling, dispatch, round transitions, and publication
closeout to an orchestrator onejudge process using
[`personas/orchestrator.yaml`](personas/orchestrator.yaml). Its supervisor is the
live planner over the channel described in
[`docs/orchestration.md`](docs/orchestration.md#the-plannerorchestrator-channel).
Worker onejudge processes still run under simulated-user supervisors. The
deliverable is this setup itself — config, personas, scripts, docs — not a shipped
binary.

Beyond dispatching at a directory, the harness manages a change's **full
life cycle** against any repo (GitHub or a local path): resolve its normalized
origin to one **repository identity**, choose a registered publication checkout,
do the work in an **isolated worktree cut from a per-run clone of an execution
checkout**, verify it with the repo's own gate, and merge it. That per-run clone
shares the execution checkout's object store and is what keeps concurrent
orchestrators from racing one worktree registry. Checkout aliases share identity-level
`workflow`, `repo_type` (`single-owner` or `team`), and verification `gate`.
Schema-v4 identities infer
an omitted type from `gh api user --jq .login` versus the normalized GitHub origin
owner; legacy `local` workflow is affirmative single-owner evidence. Type and
workflow migrations are atomic (`just migrate-repo-type` /
`migrate-repo-workflow` / `migrate-repo-gate`). Gate candidates are ranked during
onboarding; dispatch uses the stored identity gate and never auto-detects one.
Team repositories default to an ordinary ready-for-review
open PR; explicit `auto` or `direct` merges their remote PR. Single-owner
repositories preserve local direct or remote auto behavior, while explicit `none`
forces remote open-PR publication for that run without changing stored local
workflow. Team identities cannot use local workflow or direct integration.
Recover incomplete preserved branches with `just
repo-recover`, which verifies and publishes through the registered workflow; do
not bypass an incomplete provenance marker with a normal commit. The selected
publication checkout is **never worked in directly and only ever fast-forwarded**
after a merge lands; it must be clean with the selected root checked out before
dispatch. Preserved stacked branches record their PR base so recovery targets the
stack rather than the root. `just run-plan` is the one tracked hierarchical graph
executor: its top-level DAG may mix direct agents, lifecycle agents, and explicit
human actions; a lifecycle node may itself run **several agent and human steps in
sequence on one branch**. Its reconciler accepts planner-issued graph edits while
a round is running; rounds are checkpoints, not adaptation barriers. Review
surfaced proposals and use the [live-edit
protocol](docs/orchestration.md#live-graph-edits) to change the desired frontier.

## What "agent" means here

In this repo, an **agent** (or **subagent**) is a **dispatched onejudge process** —
a coding agent run under a simulated-user supervisor as a node of a plan launched
by `just orchestrate` (or run directly by `just run-plan`). This is the default
sense of the word everywhere below and in requests to you. When a task says "use
an agent," "have an agent do X," "dispatch an agent," or "spin up a subagent" —
including for research or investigation, not just code changes — dispatch
onejudge. Do **not** reach for
the host harness's own built-in subagent mechanism (its own agent/task/fork tool)
unless the request names that mechanism explicitly. When the wording is ambiguous,
dispatch onejudge.

## Your loop as planner

1. **Decompose.** Apply the granularity rule below, then capture the work as a
   tracked DAG.
   When a user asks for a plan—through a harness plan mode or informally—and the
   work is orchestration-worthy, present the plan in this tracked-DAG structure
   and offer to capture it as `plan.json` and run it with `just orchestrate`.
   Continue to apply [the granularity rule](#the-granularity-rule-the-core-judgment)
   and the [direct-tweak exception](#your-loop-as-planner). When a split follows a
   contract seam, propose the concrete contract before dispatch — the route plus
   request and response fields and types, the exact signature, or the field name,
   type, and default — and get **explicit user approval on that contract**. Read far
   enough into **both** the producer and the consumer side to state it; a contract
   chosen without reading the consumer is the failure this exists to prevent, and
   that reading is direct planning work under the research boundary below. The
   approved contract is then fixed for the run: workers implement against it and
   never unilaterally change a shared interface, and a departure one of them wants
   arrives as a proposal for the planner to amend by live edit or defer. Nodes
   have unique IDs;
   agent nodes carry `persona` + concrete `task` prose (and optionally
   `repo`/`steps` for a lifecycle that runs several steps on one branch);
   `kind: human` nodes carry only the action prose; `deps` names real
   prerequisites. Write every agent node and step `task` with `## What`, `## Why`,
   and `## Acceptance criteria`, followed by `## Additional info` only when it is
   nonempty. `Why` records the user's motivation—the impact and decision driver
   that a diff cannot recover. If that why is unclear or absent from the request,
   ask the user before dispatch; never invent it or use the orchestration handoff
   as motivation. Acceptance criteria is the detailed source of truth visible to
   both worker and judge. Every criterion must be satisfiable by the worker
   inside its own dispatch, using only what that dispatch controls; never require
   evidence that turns on an external event the run does not control — an open
   PR, a deploy, a scheduled job, a third party. When the natural proof is
   ephemeral or external, require contract-level proof instead and name the
   artifact that defines the contract.
   The judge-only `done_when` must always require that all
   task acceptance criteria are met and may add broader quality measures such as
   a green gate, held coverage, or no regressions. Keep those specific criteria in
   the task, not only in `done_when`; use `max_turns` when needed. Start from
   `examples/tracked-graph.example.json`. Reserve `kind: human` for an action only
   an external person or outside system can perform: merge a PR, publish or
   release, trigger CI, register or change infrastructure, or provide external
   sign-off. It never represents the planner's own review, acceptance,
   validation, or integration decision. The planner reviews each settled node
   over the live channel and issues `add` / `retry` / `drop` / `split` edits.
   A live edit the reconciler accepts is carried forward: the plan of record for a
   transition is [the graph the round
   executed](docs/orchestration.md#the-plan-of-record-is-the-graph-the-round-executed),
   folded from the run's own journal rather than re-read from the launch file, so a
   retry's replacement id, a branch pin, an amended `task`, `done_when`, or
   `max_turns` all reach the next round. What it learns about a node that keeps
   running belongs in a `context` edit: that note is the one thing which carries
   exactly one round, so state worth keeping is state attached again. See [Carried
   planner context](docs/orchestration.md#carried-planner-context). A
   human node the planner would attest itself is a modeling error: keep it only
   if the action is genuinely external; otherwise perform that coordination live
   with no node. See [Node shapes](docs/orchestration.md#node-shapes). Before a
   lifecycle run, use `just repos` to confirm its repository identity, type,
   workflow, and available checkout aliases; make durable routing changes with
   the register/migration recipes rather than accidental run-only overrides. Run
   `just repos --audit-gate-coverage` before relying on hooks or required PR checks
   as merge-path verification; keep missing and unknown coverage visible. Treat
   an unfamiliar project-sounding name as a lookup, not a question: search local
   paths such as `~/projects`, then `just repos`, then the current GitHub account
   with `gh search repos <name>` and `gh repo list <owner>`. A hit whose description
   matches the prompt's other clues resolves the reference; ask only when the
   search fails or leaves multiple strong candidates.
2. **Pick or create personas.** Match each subtask to a general role and review
   bar in `personas/`. Prefer precise task prose plus per-node `done_when` over
   encoding subtask details in a new persona.
3. **Launch and supervise.** Start the graph with `just orchestrate <plan.json>`,
   which stays attached and hands the run back when it settles (see below),
   then review each structured boundary and mid-run proposal surfaced by the
   orchestrator. Issue valid live edits when the running frontier should change;
   workers propose but never edit. Triage follow-ups, keep the user informed at
   each milestone, and never let more than 30 minutes pass between updates. When
   a completed task published a PR, include the relevant PR link in its completion
   report. Require verified publication closeout before issuing `complete`. A run
   the progress views report as `PARKED` is alive and not working — no child process,
   no surface, no ledger write — so treat it as stopped and intervene rather than
   waiting on it; that liveness verdict is unrelated to a node the planner *parked*
   with `cancel`, which is a deliberate idle. `channel-reply` refuses an edit it
   cannot apply, with the reason, and every edit it accepts reaches the graph; a
   non-zero reply is a rejection to correct, never a command to resend.

   **One execution path per deliverable.** When a path fails, diagnose and fix that
   path or escalate to the operator with evidence; never launch a duplicate parallel
   path for the same deliverable — a planner-driven integrate or recovery beside a
   live node delivering it counts as a duplicate — without explicit operator
   approval. `cancel` is the tool for idling a redundant or misdirected node and
   `requeue` for resuming it; see [Parking a node, and picking it up
   again](docs/orchestration.md#parking-a-node-and-picking-it-up-again).

After `just orchestrate`, the planner uses **only** `just channel-next`, `just
channel-reply`, `just stop`, and the read-only `just monitor` / `just runs` /
`just status` views. `channel-reply` carries both legacy verdicts and [versioned
live edits](docs/orchestration.md#live-graph-edits). The planner never runs
`run-plan` or `next-round` itself: those commands belong to the orchestrator
process, and two writers would race the ledger lock.

`orchestrate` **stays attached by default**: it prints the launch record, streams
exactly what `just monitor` streams, and returns when the run **settles** — the
graph completed, a blocking planner surface is waiting on you, or nothing is
driving the run any more (exit 3, and the state to intervene in). Ctrl-C detaches
without stopping the run. Pass `--detach` when a run should go unattended — several
runs supervised at once, where you launch each one and come back to it — and
`just monitor <run-id>` re-attaches to any of them, with `--until-settled` for the
same return contract. Do **not** background a launch by hand to watch it; that is
what the foreground default replaced. Rebuilding run state
from `events.jsonl`, `ps`, or `git log` in a run clone instead is the omission
the read-only views now prevent: `just runs` and `just status` name every unread
surface, how stale it is, and the `just channel-next` that reads it, so an update
nobody read can no longer hide behind a row that says only `ACTIVE`. Rendering a
surface in `monitor` is not reading it — only `channel-next` consumes one.

**Runs are owned.** Several planners share this host, each supervising its own
workstreams, so a run belongs to the session that launched it. `just orchestrate`
records that session automatically and `just runs` shows it per row: `[mine]`, the
owning session (`[claude-code:3f9a1c2e]`), or `[unknown]`. `just runs --mine` lists
only yours. Act **only** on runs you launched. A run you cannot attribute belongs to
another planner until proven otherwise — `unknown` is never yours, and a run
launched before its session was recorded stays `unknown` forever. Never derive a
process list from `ps` and signal it: that pattern knows nothing about whose work it
matched, and it has already interrupted another planner mid-supervision here.
`just stop <run-id>` is the supported way to stop a run; it refuses another
planner's run and an unattributable one, naming the owner, and `--force` reports
that owner before overriding. A stopped run is left reclaimable exactly as an
interrupted round is (`just run-plan ... --recover`). `complete` is a completion
verdict on the channel and deliberately does **not** stop scheduling; use `just
stop` when a run must actually end. `stop` is deliberately **not** in
`.claude/settings.json`'s allowlist: it ends live work, and `--force` overrides the
ownership check the incident above is about, so each one is approved on its own.

The orchestrator also surfaces an agent-written, non-blocking per-workstream
status when its durable planner-update pacemaker becomes due (30 minutes by
default). Every planner-visible surface resets that clock. Set a launch interval
with `just orchestrate ... --heartbeat-interval SECONDS`; include
`"heartbeat_interval": SECONDS` in a normal `channel-reply` to adjust it live, or
`"heartbeat_interval": false` to disable it. The orchestrator continues without
waiting for a reply to these heartbeat surfaces.

Judge a dispatched branch against its own base (`merge-base` / `base..branch`),
never a moving `origin/main`; concurrent advancement can make a healthy branch
appear to delete files.

Treat unresolved same-identity dependencies as stack prerequisites, not merely
scheduling edges, and preserve them across replans until their content reaches
the root base. The deterministic mechanics live in `docs/repo-lifecycle.md`.

Accuracy and quality come first; saving time or tokens never relaxes their bar.
Subject to that, minimize both. Apply the fixed dispatch-cost judgment in [the
granularity rule](#the-granularity-rule-the-core-judgment) both when splitting
work and when deciding whether a dispatch adds value at all.

The planner owns decomposition, persona choice, review decisions, and user
liaison. The orchestrator owns scheduling, round-ledger writes, human-action
attestation, integration of finished work, and publication closeout. Neither role
authors target-project content; dispatch implementation and research to workers.

Reading a target repo to decompose work, select a persona, and write a precise task
is direct planning work. It stops once the task can be written; investigation
past that point is research and must be dispatched.

One narrow direct-tweak exception remains: **the complete gate can prove it**. If
planning has already determined the change, the planner may apply it directly
only when a check in the target repo's complete gate exercises the changed artifact
and demonstrates the fix; report that passing gate result. A mechanically checked
rename can qualify. If no gate check proves the payload, dispatch it as authoring;
commit-message payloads, PR titles and bodies, changelog prose, and release-note
prose are in this category. Line count and urgency are irrelevant. “It's just a
commit message,” “the diff is empty,” “it's only integration coordination,” and
“it's faster than dispatching” are not exceptions.

Require each worker to prove its own change with `just gate`. Review surfaced gate
evidence rather than a judge verdict alone; relevant checks must not have skipped.

## The granularity rule (the core judgment)

Maximize useful parallelism, but **do not over-split**. Every onejudge is a fresh
agent that pays a fixed cost to prepare its context before it does useful work
(reading the repo, orienting). These are highly capable coding agents,
pair-programmed with and reviewed by a simulated-user supervisor that pushes back
until the task is actually done. One can hold a large coherent task well, so fewer,
larger dispatches amortize setup cost better. Split only for genuine parallelism,
a real dependency, or a genuinely different persona or review bar — not merely to
hand a capable agent a smaller slice. Apply this at two scales: split only where a
fresh context buys enough to justify its overhead, and do not
dispatch at all when the planner already holds the context, planning has
determined the change, and the complete gate proves it. That dispatch is cost with
no benefit. Prefer a coherent subtask that one agent can hold in its head over
many micro-tasks that each re-pay the setup tax. When unsure, err toward fewer,
larger subtasks and split further only if one proves too big. See
`docs/orchestration.md`.

The rule above answers how big a node is; **where to cut is a contract**. The seam
that makes two nodes genuinely independent is an interface — an HTTP route, a method
signature, a CLI command or flag schema, a data schema or event payload. Cutting by
component, or merely into smaller slices, usually leaves both nodes rewriting the
same surface. Cut there and land the contract **first and non-breaking**: the first
node establishes the interface without changing existing behavior, so its callers
are unaffected and the gate stays green. It then unblocks the real implementation
and every consumer to proceed in parallel, each upgrading as the implementation
lands rather than needing a synchronized cutover. This licenses no over-splitting:
a contract seam earns a split only where it buys real parallelism, and when one
agent can hold producer and consumer together that is still the better dispatch.

An implementation dispatch owns the tests that prove its change. Keep
implementation and those tests in the same node or lifecycle step so the unit
settles fully proven; never split them into separate nodes or steps. A separate
test-focused dispatch is appropriate only to close a pre-existing coverage gap
or add a regression suite for code the planner is not otherwise changing. Use
`engineer` for that work; there is no test-only persona. A contract node is no
exception to any of this: it proves the new surface exists and that existing
behavior is unchanged. Operational guidance lives under
[Decomposition and scheduling](docs/orchestration.md#decomposition-and-scheduling).

## Personas and the base config

A persona is a small onejudge **delta** file in `personas/` — the agent's general
role (`agent.instructions`) plus the supervisor's review bar (`persona`). General
cross-repo roles are top-level files; repo-specific roles live under
`personas/<repo>/` and are dispatched with `<repo>/<name>`.
Common settings live once in `config/onejudge.base.yaml` (the base config);
`dispatch` merges base ⊕ persona ⊕ the CLI `--task` into one effective config and
runs `onejudge run` on it. Keep subtask-specificity in the node controls described
above; add a persona only for a genuinely distinct role or review bar. A dedicated
`reviewer` is reserved for complex DAGs where one agent reviews and integrates
several agents' independently produced work, since the simulated-user supervisor
already reviews every dispatch.

Draft a new role under gitignored `scratch/personas/`, dispatch against that
directory, and refine it from observed performance. Once proven, dispatch its
addition through this repo's isolated self-lifecycle; `just new-persona <name>`
scaffolds the tracked file and `just check` validates it. General personas stay
flat; repo-specific personas use slash-qualified names and subdirectories
(`crozier/crozier-corpus` maps to `personas/crozier/crozier-corpus.yaml`).

## The two sides of the conversation

This repository adopts the exact onejudge version declared in
`config/onejudge.version`, and session setup installs the pinned PyPI
`onejudge` distribution and verifies both its `onejudge_sdk` import and matching
CLI wheel. Dispatch calls the typed Python SDK, which drives and validates the
real CLI. onejudge drives a
two-party conversation, and harness/model selection for each
side lives in oneharness config, not onejudge:

**Every role names the same five identities** — `claude-code:alternate`,
`claude-code:alternate2`, `codex`, `codex:alternate`, `claude-code:primary` — and
they differ only in order. A role that omitted one would lose that quota entirely
once everything ahead of it was exhausted, which is the failure this arrangement
exists to prevent. The primary Claude identity is last everywhere:

- **Agent side** (does the work) — `oneharness.toml`, discovered from the repo root;
  it prefers both alternate Claude subscriptions, in order, because the personas
  are tuned against that model tier, and only then falls back to codex. Every agent
  turn also carries the normalized tool transcript, selected by
  `scripts/oneharness-agent.sh` because it is a `run` flag with no config key: the
  planner-visible views already build `just status`'s `Commands:` line and `just
  history-show`'s detail from each record's `events`, and claude-code's default
  output format carries no tool transcript at all, so those were empty for every
  claude-code dispatch. A dispatched turn takes **`--stream`**, which delivers those
  same events *as they occur* and implies `--events`' format selection; `--events`
  alone says only that the transcript is in the report at the end, which left a node
  invisible for the 600-2000 seconds a turn runs here. Streaming a
  `run_mode = "fallback"` chain has been allowed since oneharness 0.6.5, so nothing
  is traded for it. See [Streaming the agent
  side](docs/onejudge-integration.md#streaming-the-agent-side) for the filter that
  reconciles a stream with onejudge's single-report contract, the probe that decides,
  and why `--events` remains the degrade path.
- **Judge / simulated-user side** (supervises) — `oneharness.judge.toml`, passed
  as the base config's `provider.judge_config`; codex first, then the alternate
  Claude subscriptions. It keeps its cheaper-supervisor intent through `model`
  (`claude-sonnet-5` for all three of its Claude variants) rather than by staying
  off those subscriptions.
- **Orchestrator side** (drives a tracked graph) — `oneharness.orchestrator.toml`,
  forced by `scripts/oneharness-orchestrator.sh`, which `just orchestrate` pins as
  the launched process's oneharness binary. Deliberately the reverse of the worker
  order: both codex identities carry the role first, so this long-lived
  supervisory process does not queue ahead of the workers while Codex can still
  run it.
- **LLM lint side** — `oneharness.llmlint.toml`, forced by
  `scripts/llmlint-oneharness.sh`; the same supervisory order. It is **no longer
  codex-only**.

The judge and llmlint reaching the workers' subscriptions at all is the operator's
deliberate trade: those tiers can now contend for that Claude quota, and that is
accepted because a supervisory tier that can still run beats one isolated from the
quota that is left. Do not reorder these to restore the old isolation.

Those files decide every run on this host, so pair the two sides differently for
**one** dispatch with `--worker-harness` / `--judge-harness` rather than by editing
a config concurrent runs also read. `just run-plan` and `just orchestrate` both
take them; each side is validated against its own config and refused by name when
it is not one this repo configures, and with neither flag set nothing changes.
oneharness's own `ONEHARNESS_HARNESSES` cannot express this: it is process-wide
and beats config, so it moves both sides at once. See [Choosing a harness per
side](docs/onejudge-integration.md#choosing-a-harness-per-side).

An identity is not a tier, so the same two commands take `--worker-model` /
`--judge-model` beside them: every config here pins a model **per identity**, and the
judge's pins the cheaper supervisor one on all three of its Claude variants by design,
so moving that side onto a subscription does not move it onto a model. Either is
refused before dispatch unless that side's own harness override is set and names one
harness family, because a model belongs to the provider it was written for; the model
*value* is deliberately unchecked, since an unknown one fails loudly at the harness
just named while an unconfigured identity would route credentials this repository
alone configures. The wrapper exports `ONEHARNESS_MODEL` into that side's process, so
everything it then runs inherits the choice — a worker's own gate included — but an
inherited value loses to a config's per-harness `model` and so does not re-tier
`llmlint`; what moves that tier is the harness selection the same dispatch exports.
See [Choosing a model per side](docs/onejudge-integration.md#choosing-a-model-per-side).

`scripts/claude-alt-config-dir.sh` is the one source of **both** alternate config
directories (`ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR` → `$HOME/.claude-alt`,
`ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR` → `$HOME/.claude-alt2`), and all three
wrappers source it, so no role needs either exported by hand and no two can drift
apart. Authenticate the second plan with `CLAUDE_CONFIG_DIR="$HOME/.claude-alt2"
claude`; until then it costs nothing, because claude-code classifies an absent
config directory exactly as it classifies an empty one — `auth`, which falls
through — so unlike the codex helper this one creates nothing. See
[The second alternate Claude
subscription](docs/onejudge-integration.md#the-second-alternate-claude-subscription).

Every one of those chains also names `codex:alternate`, a **second codex
identity** that absorbs an exhausted quota without changing which subscription a
role competes for. `scripts/codex-alt-home.sh` is its one source — it derives
`ORCHESTRATOR_CODEX_ALT_HOME` as `$HOME/.codex-alt` and all three wrappers source
it, because oneharness refuses to start whenever that indirection is unset.
Authenticate it with `CODEX_HOME="$HOME/.codex-alt" codex login`; until then the
candidate costs nothing, since the helper guarantees the directory **exists** and
an empty codex home falls through as `auth` while an absent one hard-fails. That
asymmetry is the whole reason the helper creates it — see
[The second Codex identity](docs/onejudge-integration.md#the-second-codex-identity).

`onejudge init` scaffolds both files plus a starter `onejudge.yaml`. The adopted
exact oneharness release is declared in `config/oneharness.version`, installed as
the `oneharness-cli` PyPI wheel, and verified by `scripts/session-setup.sh`. Session
setup also installs and verifies Bun for oneharness's SDK gate. The
committed configs are that output with auth-variant routing, a cheaper Claude
judge fallback, and the `IS_SANDBOX` env, and
`config/onejudge.base.yaml` supersedes init's starter `onejudge.yaml`. Regenerate
with `onejudge init --force`.

**Live dispatch** picks a harness via `oneharness.toml`'s fallback (alternate
Claude subscription primary, codex secondary).
The lifecycle *and the orchestrator process itself* dispatch in **`bypass`** mode by
default — the no-approval mode; `just run-plan` and `just orchestrate` both take
the same `--oneharness-mode`. It is correct here because the **whole environment
is a sandbox** (a container):
codex's own `workspace-write` sandbox (`auto` mode) needs unprivileged user
namespaces this host disables, so `bypass` (no approvals, no inner sandbox) is the
working no-approval mode and the container is the boundary. The **allowlister**
`repo-write` hook stays wired (`scripts/session-setup.sh`) as belt-and-suspenders.
Full rationale, the merge strategies, and the claude-code caveat:
`docs/repo-lifecycle.md` and `docs/onejudge-integration.md`.

## Command surface

Use the `just` recipes (`just --list` is the index); do not hand-roll
equivalents. `just bootstrap` sets up from a clean clone (installs the toolchain,
activates the git hooks); `just check` is the deterministic tier, while `just gate`
is the complete pre-push bar: `check` plus the llmlint diff tier. Both report the
line-coverage total they measured, and every stage that captures its output keeps
it at `.logs/<label>.log` (`nx`, `workspace-install`, `check`, `upgrade`,
`gate-check`, `gate-llmlint`) — gitignored, owner-only, credential values redacted, truncated
per run. Each log fills as its own stage runs, so follow the innermost one:
`.logs/nx.log` while the Nx targets run (the long part), `.logs/check.log` for
the stages after them. Read a finished run from the same paths, and never read a
live command through `/proc`. Those paths are safe to tail because the truncation
is per *invocation*, not per path: a run records the log it is writing in the
exported `ORCHESTRATOR_PRESERVED_LOGS` claim list, and a nested run that finds its
path already claimed by a live enclosing one writes `.logs/<label>.<pid>.log`
instead and names that path in its own failure. This matters here because the
suite runs `just lint-llm-diff` against this checkout from inside `just check`.
`just run-plan`
is the recorded mixed-graph executor driven internally by the dedicated
orchestrator onejudge process. The planner launches multi-node work with `just
orchestrate <plan.json>` and supervises its surfaced boundaries and proposals
over the [live channel](docs/orchestration.md#the-plannerorchestrator-channel); it
does not invoke `run-plan` directly. There is no single-dispatch command: one
subtask is a one-node plan (`examples/single-node-direct.plan.json`,
`examples/single-node-lifecycle.plan.json`), so no running work falls outside the
run ledger and the views built on it.
`just runs` lists recorded runs with the session that launched each one and the
surfaces each has queued unread; `just runs --mine` narrows that to this session's.
`just stop <run-id>` ends a run and its whole dispatch tree, subject to the
[ownership rule](#your-loop-as-planner) above.

Those views also stop guessing at what is *running*. A node the ledger records as
started now reports which side of the conversation is serving it, on which harness
identity, and for how long — with an anomalous duration for that role flagged, and a
node nothing is driving flagged `UNDRIVEN` (deliberately not `parked`, which is the
node state a planner's own `cancel` produces). All of it is proven
from the dispatch ownership registry, never from `ps` output matched by pattern: the
`ORCHESTRATOR_AGENT_STATUS_DIR` stamp the kernel fixes into every process a dispatch
starts, plus the owner lock a live dispatcher holds. `just status` carries the host's
load averages with that same attribution, and **`just host`** is the whole-host
view — per live dispatch, its owning session, run/node, role, turn age, and load
contribution. Miscounting live dispatches from `ps`, and missing a judge turn wedged
for nearly two hours, are what these replace.
**`just recoverable`** is the other half of that: every preserved-but-unpublished
branch across the registered identities, where it lives, why its workstream stopped,
whether it carries an incomplete-step marker, and the exact command that lands it —
`just repo-recover` for incomplete provenance, `just integrate` for a complete
branch, with the fetch included when the publication checkout does not have the
branch. Reach for it instead of diffing clones by hand. Every one of these views is
read-only and safe beside live work.
Human completion is never inferred and enters the graph only as an explicit live
`attest` command (or compatibility `next-round` attestation). Keep operational
syntax and result contracts in
`docs/orchestration.md` and lifecycle policy in `docs/repo-lifecycle.md` rather
than duplicating command help here.
The root quality recipes delegate project selection and caching to Nx:
`check`/`test` use the full uniform target set through `run-many`, while
`lint`/`typecheck`/`format` use affected selection. The language-native tools
inside each project target remain authoritative, and `format-check` remains a
format-only verification. Session provisioning and the initial locked Bun
install precede Nx because they make Nx available; bootstrap then delegates
project setup through uniform Nx `bootstrap` targets.
`scripts/workspace-install.sh` is that locked Bun install's one source. A freshly
created worktree carries no `node_modules`, so every `scripts/nx.sh` runs it first
and heals itself; `just bootstrap` runs it with `--force`, which reapplies a
lockfile that moved. Nothing here asks an operator to run Bun by hand, and the e2e
journeys that drive real Nx provision through the same script rather than skipping
when a worktree is fresh — a bare `pytest` in one means what the gate means.
Use `docs/telemetry.md` to inspect session timing, usage, and the agent/judge
turn timeline with `just telemetry`.
`just telemetry-server` serves the read-only DAG API over a runs root and
`just dag-ui` serves the browser view against it; both are read-only and mutate
no run. `just dag-ui-screens` photographs every major surface of that view at every
viewport in its declared matrix, against the browser tier's own fixture server rather
than any real run, and prints the gitignored per-invocation gallery it wrote — which
is how a change to this UI is checked across resolutions without starting it by hand.
Operational detail lives in [`docs/dag-ui.md`](docs/dag-ui.md).
Use `just sweep-scratch --dry-run` to inspect definite dead watchdog scratch,
harness scratch no live process still references, and conservatively stale known
third-party scratch; omit `--dry-run` to reclaim it.
Session setup and every recorded round transition run this sweep automatically.
An active lifecycle makes third-party cleanup skip without waiting;
ownership-proven dead watchdog cleanup still proceeds.
The families an active dispatch itself produces — the private `nx` install every
`bunx nx` leaves behind, the native-binary cache Nx keys on each worktree's
workspace root, pytest run directories, onejudge scratch — are the
volume, so waiting for quiescence never reclaims them. They are swept **during**
dispatches instead, on proven non-reference: a candidate no live process names in
its argv, environment, `cwd`/`root`/`exe`, open descriptors, or memory mappings,
past a short age that only covers the gap
between creating a directory and first naming it. Mappings are not optional there:
a `dlopen`ed native binary leaves no descriptor, so for a running `nx` the mapping
is the only place its cache appears. Names too generic to sweep on are
identified by shape, and each family honors its producer's own retention. The sweep
also reaps *processes* a finished dispatch left running. Once a dispatcher dies its
tree is adopted by init, so every walk this harness terminates trees with starts
from a parent that no longer exists; what survives that is the environment the
kernel fixed at `exec`, and `ORCHESTRATOR_AGENT_STATUS_DIR` names the dispatch's own
watchdog scratch directory. A stamp for a dispatch that is over — its directory gone,
or its ownership lock free — is proof; a live dispatch's worker, an unstamped
process, one stamped for another root, and the sweeping process's own ancestry are
left running. Every
sweep names the families it examined and the families it could not, so `reclaimed
0 bytes` never hides an unswept one. See
[`orchestrator.scratch.UNREFERENCED_FAMILIES`](orchestrator/scratch.py).
Dead lifecycle runs form a separate bounded recovery history: retain the newest
**3** run roots with unpublished work. A retry or `repo-recover` adopts the exact
worktree only after claiming its free occupancy lease and rejecting a live
recorded owner; dirty adopted work becomes an incomplete-step commit and must
pass the ordinary merge-path gate before publication.

`just smoke` spends one real agent-harness turn in a throwaway directory and
verifies exact prompt delivery plus a successful, fully accounted oneharness
history record. Native per-phase timing is provider-optional, so its absence is a
telemetry-quality signal rather than a launch failure. The *launch* is relaunched
up to `orchestrator.smoke.LAUNCH_ATTEMPTS` times, and only the launch: a host under
concurrent e2e load has started the selected harness and had it die, which reads as
a launch-path outage and once cost a publication that had already passed its gate.
A genuinely broken launch path fails every attempt and still fails, and a recorded
turn that violates the contract fails on the first. A passing run says how many
launches it took. The record it judges is the **selected** candidate's: a
`fallback` chain records every candidate it attempts, so an identity that refused
the turn with a classified `quota` or `auth` failure — or was skipped outright — is
the chain working, and the smoke names it in the pass rather than failing on it.
Anything else is still a launch failure: the selected record breaking the contract,
a candidate failing for a reason the chain does not move past, a candidate whose
record does not back the reason it names — one that identifies no harness, or that
carries a turn somebody was billed for — or every candidate refusing. See [The
record a fallback chain is judged
by](docs/onejudge-integration.md#the-record-a-fallback-chain-is-judged-by).
It is deliberately outside `just gate`. The pre-push hook runs it
only when the pushed diff touches `scripts/`,
`config/oneharness.version`, `config/onejudge.base.yaml`, `oneharness.toml`,
`oneharness.judge.toml`, or `oneharness.orchestrator.toml`; ordinary pushes consume
no harness quota.

A dispatched change is not done until `just gate` is green, and its agent clears
its own llmlint findings rather than leaving closeout to integration: iterate on
them with `just lint-llm-diff <base>` alone, then run `just gate` once to confirm.
`llmlint.yml` is a legitimate deliverable when a task names it; otherwise a worker
fixes the code or adds a justified site-scoped `ignore` directive, and reports a
rule that looks wrong or misapplied instead of editing it. Deciding when a marginal
finding stops being worth another gate cycle—landing with a justified line-scoped
suppression plus a tracked follow-up—is the planner's call from that surfaced
report, never the worker's by suppressing.

The judge behind that tier is non-deterministic, so the run itself is cached: `just
lint-llm-diff` resolves the base ref to a commit and runs the cached Nx
`workspace:lint-llm-diff` target (the root `project.json` — the check spans the
whole tree, so it belongs to no single project), whose command is
`scripts/llmlint-judge.sh` — llmlint with `-v`, and nothing else. Re-running `just
gate` on an unchanged tree against an unchanged base replays that run's own
terminal output instead of rolling the judge again, which is what stops one branch
from being blocked by opposite verdicts on an identical diff. `-v` is why replay
loses nothing that matters: the report carries every rule and the `llmlint history
<id>` pointer, and Nx replays it verbatim. A green elides one thing — the
serialized judge calls `-v` also prints, ~214KB of a 226KB run — because Nx
replays a hit as one burst and a burst past one pipe buffer arrives truncated;
`llmlint history <id>` has them in full, and a failure is never cached and so
prints everything. The key covers the whole workspace, the
resolved base commit, and `scripts/llmlint-fingerprint.sh` — the installed llmlint
version plus the effective merged config, so a rule change in a plugin fetched from
outside this repository still invalidates. That fingerprint resolves both of those
through `scripts/llmlint-runtime-env.sh` — the one environment the target itself
judges with — rather than the caller's, so the key always describes the judge
configuration the run would actually use. `LLMLINT_ONEHARNESS_BIN` is why: `llmlint
config` renders it as `oneharness.bin`, and a dispatch inherits the orchestrator's
checkout path, the session's own, or nothing at all, so one judged diff hashed to a
different key per dispatch and the judge re-rolled every round. Reading the caller
fails a quieter way too — because Nx scores a runtime input that exits non-zero as
*no contribution* rather than as an error, a fingerprint the caller's environment
can break does not fail the tier, it drops the judge configuration out of the key
and replays a verdict that configuration has moved on from. Because Nx caches
successful tasks only, **only a green is cached**: findings (llmlint exit 1) and a
toolchain that never reached a verdict (exit >= 2) both fail the tier and re-judge
on the next run. That is deliberate — the record/replay protocol that used to
smuggle failures through Nx is gone, and with it the shared scratch directory a
concurrent invocation could clear out from under an in-flight judge. A branch
working through a red pays a fresh roll each time, and `llmlint history` (retained at
`history.max_runs` in `llmlint.yml`) is where every roll lands. A wrong *green*
still sticks: `just lint-llm-diff <base> --skip-nx-cache` re-judges but neither
reads nor writes the cache, so the next ordinary run replays the same entry until
the tree, the base commit, or the judge configuration moves. That per-invocation
flag is the only supported re-judge lever; an ambient global
`NX_SKIP_NX_CACHE` / `NX_DISABLE_NX_CACHE` is reported and ignored by this tier,
because it re-rolls the judge from every unrelated command and breaks the checks
whose contract is cache replay. When a
miss is unexplained, run `scripts/llmlint-fingerprint.sh` — a changed fingerprint
on an unchanged tree is a changed judge, not a changed diff. The cached green for
one content, base commit, and judge configuration is authoritative and the
worker's own gate pays for it: `verify.comparison_env` is that identity's one
source, and the lifecycle exports it to every dispatch and every publishing push
of a workstream so the `pre-push` hook replays what the worker cleared instead of
re-rolling against findings it never saw — a push that resolved its own base could
merge work whose own gate had failed. See
[One judged diff, one verdict](docs/repo-lifecycle.md#one-judged-diff-one-verdict).

Every cached Nx target replays a recorded answer, so one rule governs the test tier
too: a memo may stand in for a verdict on this tree only when its key covers
everything the check reads. The Python targets run from the workspace root over the
whole tree — pytest reads documentation, recipes, hooks, and app config — so they
are keyed on it through `nx.json`'s `wholeWorkspace` input. Narrowing one back to a
subset makes a green suite a claim about a tree that was never run; force a real
re-run of a single tier with `--skip-nx-cache` on that one invocation instead. The
narrowings that earn their keep answer at the scope their tests read:
`orchestrator:test-docs` runs the handful that assert on this repository's prose
and keeps the whole-workspace key; `orchestrator:test-recipes` runs the journeys
that drive `just` recipes and shell scripts under `recipeWorkspace`, exactly what
they drive; `orchestrator:test` and `orchestrator:test-serial` run the rest under
`codeWorkspace` — the workspace
minus `docs/**`, `**/*.md`, and the `apps/**` and `packages/**` no Python test
opens. Those two share one key because they read one tree; they are separate tasks
so each half reports its own failures and the one-second serial tier can be forced
to re-run without the three-minute bulk.
`workspace:check-nx-cache` is narrowed the same way, onto the fixture and
scripts it builds its two worktrees from, and `dag-ui:test` — vitest plus two
Playwright configs — onto `dagUiServerSurface`: the Python its fixture server
actually runs, which is the read API and everything that import reaches, rather
than all of `orchestrator/**/*`. A documentation edit stops charging eight
minutes, and an edit to a command-side module the served process never loads stops
charging a browser. No split may go stale silently: an undeclared test that opens
this checkout's own documentation fails in `tests/conftest.py` and is told to carry
`@pytest.mark.reads_docs`, a `@pytest.mark.reads_recipes` test that opens
anything outside its narrower key fails the same way, and a fixture that starts
importing Python outside the served surface fails in `tests/test_nx_cache_scope.py`
and is told to widen that named input. See
[When a cached verdict may stand
in](docs/repo-lifecycle.md#when-a-cached-verdict-may-stand-in-for-a-verdict-on-this-tree).

Every remote lifecycle PR without explicit title/body metadata gets one
post-verification `pr-author` dispatch. It drafts the template-shaped body from
the actual diff through a temporary out-of-worktree file; drafting failure falls
back to the deterministic body and must never block publication.

## Dogfooding rule

Use the orchestrator harness for **all tasks of sufficient complexity**, in any
repo or project. A **plan file launched by `just orchestrate`** is the only way to
dispatch: one subtask is a plan holding one node — one direct agent
(`examples/single-node-direct.plan.json`) or one lifecycle node
(`examples/single-node-lifecycle.plan.json`) — and a larger task is the same file
with more nodes. Nothing about plan schema, personas, or node semantics changes
with the node count, so a one-node run still gets a journal, an ownership row,
planner surfaces, and a place in the DAG UI. Lifecycle nodes clone the target,
work in an isolated worktree, verify with its gate, and publish. Dispatch smaller
project work with a single-node plan rather than doing it directly; only the
slight-tweak exception above applies. This repo is one
local-mode case of the same rule.

**Self-dispatch rule (this repo).** Never author working-tree changes in the shared
canonical checkout: concurrent orchestrators use it and direct edits race them.
Every change — including plans, personas, docs, and `AGENTS.md` — must be dispatched
into an isolated worktree cut from the registered `local/ai-orchestrator-isolated`
safety clone, with the canonical checkout retained as the node's `repo` publication
repository and only fast-forwarded after integration. Set `execution_checkout` on
each lifecycle node of the plan rather than passing a top-level flag.
This does not restrict the narrow direct git operations above on finished
dispatched work. Confirm `git config core.bare` is `false` before trusting a
self-dispatch result.

## Stack and composition

How this polyglot monorepo was built up from the create-repo reference pieces:

- **Product shape:** Nx monorepo containing a config/orchestration engine, shared
  libraries, and React applications. Its orchestration core remains closest to
  `shapes/skills-repo.md` (determinism-vs-judgment split, validate-in-gate,
  narrow allowlist), applied to onejudge configs + personas rather than skills.
- **Language(s):** Python (uv, ruff, mypy, pytest) for the Nx `orchestrator` project
  in `orchestrator/` — including the repo-lifecycle layer (`workspace`, `gitops`,
  `verify`, `github`, `merge`, `lifecycle`, `replan`) that shells to real
  `git`/`gh`; TypeScript and React (Bun, Nx, Biome, ESLint) for `apps/` and
  framework-independent `packages/`; Bash for provisioning and wrappers; YAML,
  JSON, and TOML for configs.
- **Composed:** `base.md` (always) + `shapes/skills-repo.md` +
  `monorepo.md` + the React and TypeScript references. Nx provides the project
  graph, affected execution, module boundaries, and computation caching; the
  underlying language tools remain the source of each check.
- **Excluded, and why:** **CI** — deliberately, per the repo's charter: this is a
  local, private proof-of-concept ("local config/scripts/docs at this point"). The
  full gate still runs locally as `just gate` and at pre-push; add
  `.github/workflows/` mirroring it when this graduates past PoC. `releasing.md` —
  nothing versioned is published. asdf / direnv — the committed Bun and uv
  lockfiles already make the workspace reproducible.
- **Composed additionally:** the `llmlint` LLM-judge tier (`ci.md`'s companion) —
  `llmlint.yml` + the `lint-llm*` recipes, enforced at **pre-push**
  (`.githooks/pre-push`) since there is no CI.

## Invariants (non-negotiable)

- The gate is strict: format check, lint, type check, and tests all fail on
  issues — no warnings-only mode.
- **Coverage is enforced at 95% line coverage** on the `orchestrator/` package
  (`just test`); the gate fails below it. `[tool.coverage.report]` in
  `pyproject.toml` is the floor's one source — `fail_under` sets it and
  `precision` decides it, because pytest-cov compares the total *after* rounding
  at that precision. `tests/test_coverage_gate.py` holds that combination to one
  that can actually fail the build. Two tiers measure and neither judges —
  `orchestrator:test-serial` under `coverage run` for the `single_threaded` tests
  and `orchestrator:test` across the workers for the rest, each writing its own
  data file — and the uncached `orchestrator:coverage` combines them and compares
  that one total to the declared floor. Nothing else may name a floor, and a
  measuring tier that did not write its data fails the combine rather than
  lowering the total silently.
- **The suite runs across four xdist workers** (`-n 4 --dist loadgroup`), chosen
  from measurement rather than from `auto`: it is latency-bound, its floor is its
  longest single test, and the curve is flat past four while this host also runs
  live dispatches. A test whose subject is a process-wide or machine-wide
  resource declares that as a scheduling constraint — never as a loosened
  assertion, and never as a per-run solo re-proof by hand. Two markers carry
  those constraints. `single_threaded` names a test whose subject is the process
  itself; it is selected out of the parallel tier into `orchestrator:test-serial`.
  `load_sensitive` names a journey that races several real processes and waits on
  a readiness handshake between them, where the constraint is *between* tests
  rather than inside one: the whole family declares one xdist group, so the
  distribution never has two of them in flight at once. Both are registered in
  `pyproject.toml` with their reason, and `tests/test_nx_cache_scope.py` fails a
  new journey of that shape that does not join the family. See [Four workers, and
  the tests that cannot have
  any](docs/repo-lifecycle.md#four-workers-and-the-tests-that-cannot-have-any).
- **Tests are realistic, not mocked.** The e2e suite drives the *real* `onejudge`
  CLI as a subprocess through the same `dispatch`/`run-plan` code the orchestrator
  uses. Only the paid model/harness is faked — via onejudge's own `command`
  provider pointed at `tests/e2e/fake_backend.py` (a deterministic backend
  speaking onejudge's JSON-lines protocol), which is the one genuinely external
  thing we can't run for free. Never mock the merge, the dispatch, or onejudge
  itself.
- Validate external inputs at trust boundaries: persona/plan files are validated
  before dispatch (`validate-personas`, and the plan loader), and onejudge report
  JSON is parsed defensively.
- Do not commit secrets or credentials. Harness credentials (e.g.
  `CLAUDE_CODE_OAUTH_TOKEN`) live in the environment, referenced by name; the
  agent allowlist in `.claude/settings.json` stays narrow.

## Tests are context engineering

This repo runs on agents, so the suite is the only QA loop.

- **e2e** (`tests/e2e/`) proves the real journeys against the real boundaries:
  onejudge dispatch and tracked direct graphs (including recorded human pause /
  attestation / release and compatibility inputs), and the **repo lifecycle**
  against a real bare git origin — local direct-merge, resumable local human
  workstreams, remote draft checkpoints, GitHub PR+auto-merge, gate-failure,
  not-completed (including recovery of partial work committed on its unmerged
  branch), no-changes, checks-failed, and a multi-PR DAG. Only
  the paid harness and GitHub's PR/CI decisioning are faked; git and the merge are
  real (`docs/repo-lifecycle.md`).
- **unit** (`tests/`) covers the pure logic: base⊕persona merge, plan topological
  scheduling / concurrency / skip-on-failure, persona scaffolding, and validation
  rejecting malformed configs.
- A new orchestrator verb isn't done until its real journey lands in `tests/e2e/`.

## Commits and merging

Squash-merge via PR is the intended model; PRs follow
`.github/pull_request_template.md` (terse **What** / **Why**). With no CI, the
**pre-push hook** (`.githooks/pre-push`, activated by `just bootstrap`) is the
enforcement point: it runs `just gate`, so nothing reaches the remote unproven.
Every dispatched agent must clear its own findings before committing. Branch
protection and a CI mirror of this gate are deferred with CI. Keep the
`.claude/settings.json` allowlist current: add a new routine command there instead
of re-approving it each session. Local-first is not local-only: keep the registered
base branch in sync with its origin, and push every change that reaches it immediately rather
than leaving verified work only in the local checkout. A dispatched `local` merge
already pushes to origin; publish a direct commit with `just sync`. The pre-push
gate guards every push. Never force-push or rewrite history on the registered base.

Squash-merge is what a recovered incomplete step publishes too, so **every** path
that advances the base — lifecycle publication, `repo-recover`, and the `integrate`
train — leaves **one** commit on it: the `(incomplete step)` marker and its
`chore: attest verified recovery of preserved work` are branch state, and merging or
fast-forwarding the branch's provenance commits onto the base contradicts this
model. The attestation is not dropped — the publication commit's message ends with one
`Orchestrator-Recovered-Incomplete: <marker sha>` trailer per marker it recovered,
so the base still records that a step was left incomplete and a green gate cleared
it. A published subject names the change only; a marker's text never appears in
one. See [What the base branch carries for a recovered incomplete
step](docs/repo-lifecycle.md#what-the-base-branch-carries-for-a-recovered-incomplete-step).
Provenance commits and marker-fragment subjects that already reached `main` stay
where they are: that history is never rewritten.

## After the main task

Act on two standing goals beyond the ask: (1) engineer the context for next time
(a real e2e for any journey a bug slipped through, a script for a step you did by
hand, a terse note here for what the code doesn't show); (2) keep the codebase and
environment clean and reproducible. Fold either in when it's the lowest-error path
to the ask; otherwise propose it as a follow-up. Skip busywork.
