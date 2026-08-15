<!-- llmlint: ignore-file[determinism_vs_judgment] Repo discovery requires judgment across existing interfaces. -->
<!-- llmlint: ignore-file[no_redundant_instruction_pointers] The planner/orchestrator split requires a direct pointer to its live-channel operating contract. -->
<!-- llmlint: ignore-file[agents_md_durable_and_terse] Human-node eligibility is durable planner judgment and needs concrete modeling guidance here. -->
<!-- llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] Human-node misuse is intentionally rejected at planning, node-definition, and reviewer boundaries. -->

# AGENTS.md

Durable instructions for the **planner** and any agent working in this repo.
Write for a future maintainer, not as a session log. The deterministic steps are
the `just` recipes, each a thin wrapper over one of the published CLIs this
repository configures; this file holds the judgment.

> `CLAUDE.md` is a symlink to this file — edit `AGENTS.md` only.

## What this repo is

A local **orchestration harness**: you (the planner) take one large task, split it
into a dependency graph of smaller tasks, and review its execution. `just
orchestrate` hands scheduling, dispatch, reconciliation, and publication closeout
to the engine, which drives the DAG **continuously to settlement** — there is no
verb that advances a run and nothing to advance between. It also attaches
`graphs/dag-scope.yaml`: an **active monitor** using
[`personas/orchestrator.yaml`](personas/orchestrator.yaml), which drives nothing
and instead watches the detailed activity stream, compares it against the goal and
each node's task, and raises what it finds over the channel described in
[`docs/orchestration.md`](docs/orchestration.md#the-planner-channel). Worker
onejudge processes still run under simulated-user supervisors. The deliverable is
this setup itself — config, personas, scripts, docs — not a shipped binary.

Beyond dispatching at a directory, the harness manages a change's **full
life cycle** against any repo (GitHub or a local path): resolve its normalized
origin to one **repository identity**, choose a registered publication checkout,
do the work in an **isolated worktree cut from a per-run clone of an execution
checkout**, verify it with the repo's own gate, and merge it. That per-run clone
shares the execution checkout's object store and is what keeps concurrent
orchestrators from racing one worktree registry. Checkout aliases share one identity, and its
`workflow`, `repo_type` (`single-owner` or `team`), and verification `gate` come
from the **rules file** the identity matches rather than from anything stored per
identity. This host's copy of it is tracked as `config/onevcs.rules.yml` beside the
checkout list `config/onevcs.checkouts`, and `just repos-apply` installs both —
idempotently, so it is re-run after an edit rather than migrated. Editing that file
is how routing changes, and `onevcs rules check <repo>` is what shows the resolved
policy an identity ends up with. `just repos` only lists registered identities and
checkouts: the `workflow` and `repo_type` it also prints are `onevcs register`'s
derivation from the origin, unsettable and read by nothing on the publication path.
Dispatch uses the gate that file resolves and never auto-detects one.
Team repositories default to an ordinary ready-for-review
open PR; explicit `change-auto` or `change-direct` merges their remote PR.
Single-owner repositories preserve local direct or remote auto behavior, while
explicit `change-open` forces remote open-PR publication for that run without
changing stored local workflow. Team identities cannot use local workflow or
direct integration. Those four names — `local-direct`, `change-open`,
`change-auto`, `change-direct` — are the published `merge_policy` vocabulary; the
older `direct` / `none` / `auto` spellings are refused by name at launch.
Recover incomplete preserved branches with `just
repo-recover`, which verifies and publishes through the registered workflow; do
not bypass an incomplete provenance marker with a normal commit. The selected
publication checkout is **never worked in directly and only ever fast-forwarded**
after a merge lands; it must be clean with the selected root checked out before
dispatch. So `no-changes` from a node whose task was to change code means **look
for the work elsewhere** before it means there was none: check that checkout's
branches and its `main` against `origin/main`, and the repo's open PRs. A worker
dispatched without a worktree does the work in whatever checkout it can see, and
the empty session branch the node watched is then a truthful report about the
wrong directory — settle it only once you have looked. But a worker dispatched
**with** one starts there, and that is now measured rather than assumed: a
lifecycle dispatch against onepipeline 0.3.1 reported `pwd` as
`/home/nick.guest/.onevcs/workspaces/github.com-nickderobertis-ai-orchestrator-c2fddf4e28b4/runs/s-cec0174198d8/worktree`
as its first action, which is the directory `onevcs` recorded cutting for that
node's branch. So a task need not tell a lifecycle worker where to commit; that
paragraph is redundant and a planner may drop it from its templates without
re-deriving this. `tests/e2e/test_worker_start_directory_e2e.py` is what keeps it
answered — it launches a lifecycle node for real and reads the directory out of
the run's own journal. Preserved stacked branches
record their PR base so recovery targets the stack rather than the root. A plan is
the one tracked hierarchical graph: its
top-level DAG may mix direct agents, lifecycle agents, and explicit
human actions; a lifecycle node may itself run **several agent and human steps in
sequence on one branch**. Its reconciler accepts graph edits at any moment, because
there is no moment at which the graph stops being live. Review surfaced proposals
and use the [live-edit protocol](docs/orchestration.md#live-graph-edits) to change
the desired frontier.

## What "agent" means here

In this repo, an **agent** (or **subagent**) is a **dispatched onejudge process** —
a coding agent run under a simulated-user supervisor as a node of a plan launched
by `just orchestrate`. This is the default
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
   **`## Acceptance criteria` IS the node's review bar** — there is no second place
   to state one. A plan node carrying `done_when` is refused while the plan loads,
   at any declared `schema_version`, so a bar written there does not weaken the
   dispatch, it prevents it. What replaced it is one shared clause in
   `config/onejudge.base.yaml` under `user.done_when`, phrased against the task
   ("every acceptance criterion stated in the task is met") and handed to the judge
   verbatim beside a transcript whose first message is that task. So the criteria
   list is the thing a planner tunes, and tuning it is how a node gets a different
   bar: every criterion the planner would once have hidden in `done_when` goes in
   the list the worker also reads, where it can be acted on rather than only failed
   against. Only a measure true of *every* dispatch alike belongs in the shared
   clause, and never one naming a specific check tier — that is what refused
   complete work in repositories that run no such tier. Use `max_turns` when a task
   needs more room; at `schema_version: 2` it reaches the dispatch, which v1 never
   did. Start from
   `examples/tracked-graph.example.json`. Reserve `kind: human` for an action only
   an external person or outside system can perform: merge a PR, publish or
   release, trigger CI, register or change infrastructure, or provide external
   sign-off. It never represents the planner's own review, acceptance,
   validation, or integration decision. The planner reviews each settled node
   over the live channel and issues `add` / `retry` / `drop` / `reparent` edits.
   An accepted edit needs no carrying forward: [the graph of record is the live
   graph](docs/orchestration.md#the-graph-of-record-is-the-live-graph), projected
   from the run's own journal rather than re-read from the launch file, so a
   retry's replacement id, a branch pin, an amended `task` — which is how a node's
   review bar is amended, since the bar lives in its `## Acceptance criteria` — or
   `max_turns` are simply what is executing. What it learns about a node that keeps
   running belongs in a `context` edit: that note lasts exactly one dispatch, so
   state worth keeping is state attached again. See [Carried planner
   context](docs/orchestration.md#carried-planner-context). A
   human node the planner would attest itself is a modeling error: keep it only
   if the action is genuinely external; otherwise perform that coordination live
   with no node. See [Node shapes](docs/orchestration.md#node-shapes). Before a
   lifecycle run, use `just repos` to confirm its registered identity and available
   checkout aliases, and `onevcs rules check <repo>` for its resolved publication,
   approvals, and gate — `just repos`'s type, workflow, and gate columns are not the
   routing. Durable routing is the
   **rules file**'s: a rule matches a repository by pattern and names the
   publication policy, approvals, and gate that follow, so a routing change is an
   edit to `config/onevcs.rules.yml` plus `just repos-apply` rather than a command
   that writes a policy. Change it there rather
   than reaching for an accidental run-only override. Run
   `just repos --audit-gate-coverage` before relying on hooks or required PR checks
   as merge-path verification; keep missing and unknown coverage visible. Treat
   an unfamiliar project-sounding name as a lookup, not a question: search local
   paths such as `~/projects`, then `just repos`, then the current GitHub account
   with `gh search repos <name>` and `gh repo list <owner>`. A hit whose description
   matches the prompt's other clues resolves the reference; ask only when the
   search fails or leaves multiple strong candidates.
2. **Pick or create personas.** Match each subtask to a general role and review
   bar in `personas/`. Prefer precise task prose — a specific `## Acceptance
   criteria` list, which is the node's review bar — over encoding subtask details
   in a new persona. A node's `persona` is a *name* resolved against roles built
   into the tool, not against this repository's `personas/` directory, so a role
   whose built-in bar replaces `config/onejudge.base.yaml`'s (`planner`,
   `reviewer`, `researcher`) is reviewed without the shared acceptance-criteria
   clause, and a repo-specific slash-qualified name cannot be dispatched at all.
   See [Which of these files a dispatch actually reads](personas/README.md#which-of-these-files-a-dispatch-actually-reads).
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
   with `cancel`, which is a deliberate idle. A run whose *driver* is dead but whose
   ledger is intact is not lost and must not be relaunched under a new id: `just
   orchestrate --adopt <run-id>` attaches a fresh driver to it, keeping the run
   id, journal, ledger, and anchors, and refuses another session's run and one
   something is still driving — see [Adopting a run whose driver
   died](docs/orchestration.md#adopting-a-run-whose-driver-died). While the run
   works, the engine surfaces a **non-blocking** update for a dispatch that has
   recorded nothing past its stall threshold, and the monitor surfaces what it sees
   drifting from the plan; both are evidence to act on rather than verdicts, so
   decide between `cancel`, `retry`, `context`, and letting it run.
   `channel-reply` refuses an edit it
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
live edits](docs/orchestration.md#live-graph-edits). There is no verb that advances
a run, so there is nothing left for a planner to drive: the engine reconciles
continuously and two writers would race the ledger lock anyway.

`just channel-next` and `just monitor` read through the `planner`
[profile](docs/orchestration.md#read-profiles) — the pipeline's own decisions and
settlements, not every worker's turns. That narrowing is the point: the detail is
the monitor's to read, through `--filter monitor`, and the same flag is here when
you want it. `--all` bypasses profiles entirely.

`orchestrate` **stays attached by default**: it prints the launch record, streams
the run's merged events, and returns when the run **settles** — the
graph completed, a blocking planner surface is waiting on you, or nothing is
driving the run any more (exit 3, and the state to intervene in). Ctrl-C detaches
without stopping the run. Pass `--detach` when a run should go unattended — several
runs supervised at once, where you launch each one and come back to it — and
`just monitor <run-id>` re-attaches to any of them, streaming the same events
without the settle-and-return contract the foreground launch has. Do **not**
background a launch by hand to watch it; that is
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
that owner before overriding. `just orchestrate --adopt <run-id>` attaches a fresh driver
to its intact ledger. `complete` is a completion
verdict on the channel and deliberately does **not** stop scheduling; use `just
stop` when a run must actually end. A stopped run is left reclaimable exactly as a
run whose driver died is. `stop` is deliberately **not** in
`.claude/settings.json`'s allowlist: it ends live work, and `--force` overrides the
ownership check the incident above is about, so each one is approved on its own.

An agent-written, non-blocking per-workstream status also arrives when the durable
planner-update pacemaker becomes due (30 minutes by default). That pacemaker is the
`check-in` member of `graphs/dag-scope.yaml`, and it carries its own `task` that
opens with `{task}` — which is why that document declares schema 4. The task is what
keeps the member reporting rather than editing: `onepipeline` composes one task for
the graph and `oneagentgraph` gives it to every member that claims none, so a member
whose job is not the run-level task must state its own — and must interpolate the
composed one back in, because nothing in the environment names the run to an
observer member. Never let this one reach `onepipeline
reply`; live edits belong to the `monitor` member, which stays for the whole run,
and to you. Every planner-visible surface resets that clock. The interval is set
once, at launch, with `just orchestrate ... --heartbeat-interval SECONDS`, and
there is no way to change it afterwards: the reply envelope `just channel-reply`
sends is closed to unknown fields and accepts exactly `version`, `author`,
`completion`, `message`, `reason`, and `commands`, so a reply carrying
`"heartbeat_interval"` is refused whole and its verdict and graph edits go with it.
`--heartbeat-interval` is on `onepipeline start` alone — not on `adopt` either — so
choose the interval when launching, and relaunch rather than expecting to retune a
live run. The run continues without waiting for a reply to these surfaces.

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

The planner owns decomposition, persona choice, review decisions, human-action
attestation, and user liaison. The engine owns scheduling, ledger writes,
integration of finished work, and publication closeout. The monitor owns noticing —
and, where a fix is unambiguous and inside its
[allowlist](docs/orchestration.md#who-issued-an-edit-and-what-that-bounds), applying
it. None of these roles authors target-project content; dispatch implementation and
research to workers.

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
- **Monitor side** (watches a tracked graph) — `oneharness.orchestrator.toml`,
  named by `graphs/dag-scope.yaml`'s `monitor` member as its **agent** side. That
  member's judge side is not a harness config at all: it is the live planner, over
  `scripts/channel-serve.py`, so replying to a monitor surface steers its next turn.
  Deliberately the reverse of the worker order: both codex identities carry the
  role first, so this long-lived supervisory process does not queue ahead of the
  workers while Codex can still run it. It is also the **one** side here with no
  per-turn deadline (`timeout = 0`), because it watches for as long as the run
  lasts and a deadline would end the watching.
- **Pacemaker side** (the `check-in` planner update) — `oneharness.check-in.toml`,
  the monitor's routing verbatim with a finite deadline. It is a separate file
  for exactly one reason, and re-merging the two is a silent regression: see
  [Choosing a deadline per
  side](docs/onejudge-integration.md#choosing-a-deadline-per-side).
- **LLM lint side** — `oneharness.llmlint.toml`, forced by
  `scripts/llmlint-oneharness.sh`; the same supervisory order. It is **no longer
  codex-only**.

The judge and llmlint reaching the workers' subscriptions at all is the operator's
deliberate trade: those tiers can now contend for that Claude quota, and that is
accepted because a supervisory tier that can still run beats one isolated from the
quota that is left. Do not reorder these to restore the old isolation.

**Streaming and out-of-band turn control are independent, and a control failure is
never a reason to stop streaming.** `--stream` decides *when* a turn's transcript
arrives; `--control` opens a socket a separate process can interrupt the live turn
over. Both are supported together on a multi-identity fallback chain as of oneharness
0.8.0, whatever harness families it mixes: 0.7.2 stopped the control validator
counting *candidates* where it meant concurrent turns, and 0.8.0 stopped it demanding
one turn-control mechanism across the whole chain, binding the mechanism to the
candidate that serves the turn instead. That was the constraint that bit here — every
chain mixes claude-code with codex, which declare different mechanisms — so the
committed chains are now planned with each candidate on its own.
Turning a member's `stream` off to dodge a control refusal trades away the per-turn
visibility a planner supervises with and fixes nothing; that workaround was written
against this repository and rejected, and no member carries a `stream` key today. See
[Streaming and turn control are independent
concerns](docs/onejudge-integration.md#streaming-and-turn-control-are-independent-concerns).

Those files decide the defaults for every run on this host. Pairing the two sides
differently for **one** run is a property of that run's agent graph: pass
`--node-set members.worker.agent.oneharness_config=REF` and the corresponding
`members.worker.judge.oneharness_config=REF` override to `just orchestrate`, where
each referenced config declares the intended identity. Use `--set` instead of
`--node-set` to override a dag-scope member. Do not edit a config concurrent runs
also read.
The adopted graph invokes oneharness directly; `scripts/oneharness-agent.sh` is
still the smoke/manual boundary but its `ORCHESTRATOR_WORKER_HARNESSES` /
`ORCHESTRATOR_JUDGE_HARNESSES` compatibility variables are not on this launch
path. oneharness's `ONEHARNESS_HARNESSES` is process-wide and cannot express a
per-side choice. See
[Choosing a harness per
side](docs/onejudge-integration.md#choosing-a-harness-per-side).

Choosing the identity does not choose the **tier**: every one of those files pins a
`model` per harness, and the judge's three Claude identities are pinned to the
cheaper supervisor model deliberately. The model half of the per-side seam is
graph-native: override `members.worker.agent.model` and
`members.worker.judge.model`, and it applies to whichever candidate the chain
selects — `fallback` does not fall through a task failure, so a model a side's
selected identity rejects dies on that rejection rather than degrading. Note what
the seam does **not** buy — a config's per-harness `model` beats `ONEHARNESS_MODEL`,
so a model exported into the environment is inert against
`oneharness.llmlint.toml`'s own pins; overriding which *identity* that tier judges
on is what changes its model. See [Choosing a model per
side](docs/onejudge-integration.md#choosing-a-model-per-side).

Nor does it choose the **deadline**, and that half of the seam has no graph-native
field at all: a member takes `oneharness_config`, `model`, and `stream`, so the
config file is the only place a per-member `timeout` can live. Since oneharness
0.7.0, absent means no deadline; the worker, judge, and llmlint configs intentionally
take that default. `timeout = 0` also means no deadline and remains explicit in
`oneharness.orchestrator.toml`, because the monitor watches for the life of the run.
That is why the `check-in` pacemaker no longer shares that file: an unbounded
deadline reaching a scheduled member would leave a wedged turn alive forever,
which fails silently, so it reads
`oneharness.check-in.toml` instead. Never point two members at one config to save a
copy, and never set `ONEHARNESS_TIMEOUT` to fix a deadline — it is process-wide for
the whole graph run and beats every file, so it moves every member at once. See
[Choosing a deadline per
side](docs/onejudge-integration.md#choosing-a-deadline-per-side).

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
default — the no-approval mode. It is correct here because the **whole environment
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
There are no engine verbs left to invoke: `onepipeline start` drives the DAG to
settlement on its own, so `just orchestrate <plan.json>` is the whole launch and
the planner supervises the surfaces and proposals it raises over the [live
channel](docs/orchestration.md#the-planner-channel). There is no single-dispatch
command: one
subtask is a one-node plan (`examples/single-node-direct.plan.json`,
`examples/single-node-lifecycle.plan.json`), so no running work falls outside the
run ledger and the views built on it.
`just runs` lists recorded runs with the session that launched each one and the
surfaces each has queued unread; `just runs --mine` narrows that to this session's.
`just stop <run-id>` ends a run and its whole dispatch tree, subject to the
[ownership rule](#your-loop-as-planner) above. Every one of those verbs goes
through `scripts/onepipeline.sh`, which is the one place a planner's identity is
established: `onepipeline` decides ownership from `ONEPIPELINE_LAUNCHER` /
`ONEPIPELINE_LAUNCHER_SESSION`, the reader's as well as the launcher's, so a view
that did not identify itself matches no run and `--mine` lists nothing.

What a run's agents *are* is [`graphs/`](docs/orchestration.md#the-agent-graphs-a-run-launches),
and it is this repository's content rather than the engines': `onepipeline` ships
the paths `graphs/dag-scope.yaml` and `graphs/node-scope.yaml`, not the files,
because they name this operator's own onejudge base config, oneharness configs,
and personas. A checkout without them refuses every plan it has. Both paths
resolve against the directory a run is launched from, which is why `just
orchestrate` is run from the repository root.

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
They also report the tier *above* those dispatches: one driver line per unfinished
launch naming whether the orchestrator's recorded pid is still there, which part of
its loop the run's own state places it in, and how long since anything of it was last observed doing something.
A driver this host has proved is gone reads `DRIVER DEAD … nothing is driving this
run` — distinct from `PARKED`, which is a launch that still holds its pid. The same
tier is served as run-scope timeline spans, from a bounded local capture when the
harness refused to write its history; see [Seeing the supervisory
tier](docs/telemetry.md#seeing-the-supervisory-tier).
**`just recoverable`** is the other half of that: every preserved-but-unpublished
branch across the registered identities, where it lives, why its workstream stopped,
whether it carries an incomplete-step marker, and the exact command that lands it —
`just repo-recover` for incomplete provenance, `just integrate` for a complete
branch, with the fetch included when the publication checkout does not have the
branch. Reach for it instead of diffing clones by hand. Every one of these views is
read-only and safe beside live work.
Human completion is never inferred and enters the graph only as an explicit live
`attest` command, or the equivalent `onepipeline attest RUN REFERENCE`. Keep
operational
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
A node that died to the provider is diagnosed from `just status` alone: it and
`just runs` print a provider-health block for all five configured identities —
each one's binding window, utilization, and reset, with a failed probe listed as
`unknown` rather than dropped — above one rolled-up line per repeated cause naming
the refusing **side** and **identity**. Read the side first: the agent and judge
chains prefer different identities, so a fix aimed at the wrong one changes
nothing, and a whole night was once lost to a judge-chain quota that read as a
bare `harness failed (quota)`. `just results` and the read API carry the same
attribution per node with the harness's own bounded output. See [Diagnosing a
provider failure](docs/telemetry.md#diagnosing-a-provider-failure).
`just telemetry-server` serves the published read-only DAG API over a runs root and
`just dag-ui` serves the published browser bundle against it; both are read-only and
mutate no run. Neither is built here any more — the API is `onepipeline-api` and the
view is the `onepipeline-ui` bundle — so `just dag-ui` puts the two behind one origin
and `just dag-ui-screens` photographs that bundle at every viewport in the matrix,
printing the gitignored per-invocation gallery it wrote. Operational detail lives in
[`docs/dag-ui.md`](docs/dag-ui.md).
`just sweep-scratch` reclaims the scratch a dispatch leaves behind — the families
`oneagentgraph` itself produces, each judged on proven non-reference: a candidate no
live process names in its argv, environment, `cwd`/`root`/`exe`, open descriptors,
or memory mappings, past a short age that only covers the gap between creating a
directory and first naming it. `--dry-run` inspects without removing, and
`--min-age-hours` moves the conservative threshold for scratch that is only stale.
Session setup runs it automatically. Every sweep names the families it examined and
the families it could not, so a sweep that reclaimed nothing never hides an unswept
one.

The processes that are *meant* to outlive their launcher — the driver `just
orchestrate` starts, and the dispatches and publications it forks — are the
engines' own to keep alive and to reap; never work around a kill here with
`nohup`/`setsid` by hand.
Dead lifecycle runs form a separate bounded recovery history: retain the newest
**3** run roots with unpublished work. A retry or `repo-recover` adopts the exact
worktree only after claiming its free occupancy lease and rejecting a live
recorded owner; dirty adopted work becomes an incomplete-step commit and must
pass the ordinary merge-path gate before publication.

`just smoke` spends one real agent-harness turn in a throwaway directory and
verifies exact prompt delivery plus a successful, fully accounted oneharness
history record. Native per-phase timing is provider-optional, so its absence is a
telemetry-quality signal rather than a launch failure. The *launch* is relaunched a
bounded number of times — `oneagentgraph smoke` owns that policy now — and only the
launch: a host under
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
`oneharness.judge.toml`, `oneharness.orchestrator.toml`, or
`oneharness.check-in.toml`; ordinary pushes consume no harness quota.

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
different key per dispatch and the judge re-rolled on every run. Reading the caller
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
worker's own gate pays for it: `ONEVCS_COMPARISON_REMOTE` / `ONEVCS_COMPARISON_BASE`
is that identity's one source, and the lifecycle exports it to every dispatch and
every publishing push
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
they drive; `orchestrator:test` runs the rest under `codeWorkspace` — the
workspace minus `docs/**` and `**/*.md`.
`workspace:check-nx-cache` is narrowed the same way, onto the fixture and
scripts it builds its two worktrees from. A documentation edit stops charging for
the whole suite. No split may go stale silently: an undeclared test that opens
this checkout's own documentation fails in `tests/conftest.py` and is told to carry
`@pytest.mark.reads_docs`, and a `@pytest.mark.reads_recipes` test that opens
anything outside its narrower key fails the same way. See
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
into an isolated worktree cut from the registered `ai-orchestrator-isolated`
safety clone, with the canonical checkout retained as the node's `repo` publication
repository and only fast-forwarded after integration. Set `execution_checkout` on
each lifecycle node of the plan rather than passing a top-level flag.
This does not restrict the narrow direct git operations above on finished
dispatched work. Confirm `git config core.bare` is `false` before trusting a
self-dispatch result.

## Stack and composition

How this polyglot monorepo was built up from the create-repo reference pieces:

- **Product shape:** a configuration layer over four published CLIs, kept as an Nx
  workspace for its computation cache and its uniform target set. It remains
  closest to `shapes/skills-repo.md` (determinism-vs-judgment split,
  validate-in-gate, narrow allowlist), applied to onejudge configs + personas
  rather than skills. The engine, the lifecycle, and the browser view are no longer
  built here: `onepipeline`, `oneagentgraph`, `onevcs`, and `onepipeline-ui` own
  them, and the `just` recipes are thin wrappers over their verbs.
- **Language(s):** Bash for the recipes' wrappers, the harness routing, and
  provisioning; Python (uv, ruff, mypy, pytest) for the Nx `orchestrator` project —
  now the label contract, the redaction rule, and the suite that proves this
  layer; YAML, JSON, and TOML for configs.
- **Composed:** `base.md` (always) + `shapes/skills-repo.md` + `monorepo.md`. Nx
  provides the project graph, affected execution, and computation caching; the
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
- **Coverage is enforced at 100% line coverage** on the `orchestrator/` package
  (`just test`); the gate fails below it. `[tool.coverage.report]` in
  `pyproject.toml` is the floor's one source — `fail_under` sets it and
  `precision` decides it, because pytest-cov compares the total *after* rounding
  at that precision. `tests/test_coverage_gate.py` holds that combination to one
  that can actually fail the build. The floor is 100 because what is left of the
  package is small and is all trust boundary: the label contract handed to a
  subprocess, the redaction rule `scripts/preserved-log.sh` mirrors, and the paths
  both read. `orchestrator:test` measures and judges nothing; the uncached
  `orchestrator:coverage` reads what it wrote and compares that total to the
  declared floor. Nothing else may name a floor, and a measuring tier that did not
  write its data fails that read rather than lowering the total silently.
- **The suite runs across four xdist workers** (`-n 4 --dist loadgroup`), chosen
  from measurement rather than from `auto`: it is latency-bound, its floor is its
  longest single test, and the curve is flat past four while this host also runs
  live dispatches. A test whose subject is a process-wide or machine-wide
  resource declares that as a scheduling constraint — never as a loosened
  assertion, and never as a per-run solo re-proof by hand. The two markers that
  used to carry those constraints went with the dispatch journeys that needed
  them; a test of that shape reintroduces the marker, its tier, and its reason
  together rather than weakening an assertion to survive a worker.
- **Tests are realistic, not mocked.** What this repository still owns is its
  command surface, so the suite drives the *real* `just` recipes, the real wrapper
  scripts, the real `oneharness` CLI, and real Nx. The published CLIs a recipe
  delegates to are doubled at that boundary and nothing above it: each engine is
  proven in its own repository, and a real `onepipeline start` here would launch
  agents. Never double a recipe, a wrapper script, or the shell they run in.
- Validate external inputs at trust boundaries: a persona is validated before
  dispatch (`just validate-personas`), and the label contract handed to a
  subprocess is validated in `orchestrator/labels.py` before it can reach one.
- Do not commit secrets or credentials. Harness credentials (e.g.
  `CLAUDE_CODE_OAUTH_TOKEN`) live in the environment, referenced by name; the
  agent allowlist in `.claude/settings.json` stays narrow.

## Tests are context engineering

This repo runs on agents, so the suite is the only QA loop.

- **e2e** (`tests/e2e/`) proves the real journeys against the real boundaries: the
  whole delegation table driven through the real recipes and wrapper scripts, the
  llmlint tier's cached verdict and its two judging paths, the Nx cache keys
  against real Nx in real linked worktrees, the harness wrapper against the real
  `oneharness` CLI and its fallback chain, and session setup installing the
  adopted releases for real from PyPI. Only the paid model and the published CLIs
  a recipe delegates to are doubled.
- **unit** (`tests/`) covers what this layer decides on its own: the label
  contract, the redaction rule, the coverage floor's enforceability, and the drift
  gates over the pins and the prose.
- A recipe is not done until a journey drives it end to end in `tests/e2e/`.

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
already pushes to origin, and `just sync` fast-forwards a publication checkout to
what is on it. The pre-push
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
one. That `Orchestrator-` prefix is this host's, not the published default —
`config/onevcs.rules.yml`'s `trailer_prefix` is its one source, and a marker under a
prefix the rules file does not name is reported unrecognized and refused
publication rather than read or ignored. See [What the base branch carries for a
recovered incomplete
step](docs/repo-lifecycle.md#what-the-base-branch-carries-for-a-recovered-incomplete-step).
Provenance commits and marker-fragment subjects that already reached `main` stay
where they are: that history is never rewritten.

## After the main task

Act on two standing goals beyond the ask: (1) engineer the context for next time
(a real e2e for any journey a bug slipped through, a script for a step you did by
hand, a terse note here for what the code doesn't show); (2) keep the codebase and
environment clean and reproducible. Fold either in when it's the lowest-error path
to the ask; otherwise propose it as a follow-up. Skip busywork.
