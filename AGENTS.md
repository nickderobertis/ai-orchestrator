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
do the work in an **isolated worktree cut from an execution checkout**, verify it
with the repo's own gate, and merge it. Checkout aliases share identity-level
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
a coding agent run under a simulated-user supervisor via `just dispatch` /
`just repo-task`, or as a worker launched by `just orchestrate`. This is the
default sense of the word
everywhere below and in requests to you. When a task says "use an agent," "have an
agent do X," "dispatch an agent," or "spin up a subagent" — including for research
or investigation, not just code changes — dispatch onejudge. Do **not** reach for
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
   and the [direct-tweak exception](#your-loop-as-planner). Nodes have unique IDs;
   agent nodes carry `persona` + concrete `task` prose (and optionally
   `repo`/`steps` for a lifecycle that runs several steps on one branch);
   `kind: human` nodes carry only the action prose; `deps` names real
   prerequisites. Write every agent node and step `task` with `## What`, `## Why`,
   and `## Acceptance criteria`, followed by `## Additional info` only when it is
   nonempty. `Why` records the user's motivation—the impact and decision driver
   that a diff cannot recover. If that why is unclear or absent from the request,
   ask the user before dispatch; never invent it or use the orchestration handoff
   as motivation. Acceptance criteria is the detailed source of truth visible to
   both worker and judge. The judge-only `done_when` must always require that all
   task acceptance criteria are met and may add broader quality measures such as
   a green gate, held coverage, or no regressions. Keep those specific criteria in
   the task, not only in `done_when`; use `max_turns` when needed. Start from
   `examples/tracked-graph.example.json`. Reserve `kind: human` for an action only
   an external person or outside system can perform: merge a PR, publish or
   release, trigger CI, register or change infrastructure, or provide external
   sign-off. It never represents the planner's own review, acceptance,
   validation, or integration decision. The planner reviews each settled node
   over the live channel and issues `add` / `retry` / `drop` / `split` edits. A
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
   then review each structured boundary and mid-run proposal surfaced by the
   orchestrator. Issue valid live edits when the running frontier should change;
   workers propose but never edit. Triage follow-ups, keep the user informed at
   each milestone, and never let more than 30 minutes pass between updates. When
   a completed task published a PR, include the relevant PR link in its completion
   report. Require verified publication closeout before issuing `complete`.

After `just orchestrate`, the planner uses **only** `just channel-next`, `just
channel-reply`, and the read-only `just monitor` / `just runs` / `just status`
views. `channel-reply` carries both legacy verdicts and [versioned live
edits](docs/orchestration.md#live-graph-edits). The planner never runs `run-plan`
or `next-round` itself: those commands belong to the orchestrator process, and
two writers would race the ledger lock.

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

An implementation dispatch owns the tests that prove its change. Keep
implementation and those tests in the same node or lifecycle step so the unit
settles fully proven; never split them into separate nodes or steps. A separate
test-focused dispatch is appropriate only to close a pre-existing coverage gap
or add a regression suite for code the planner is not otherwise changing. Use
`engineer` for that work; there is no test-only persona. Operational guidance
lives under [Decomposition and scheduling](docs/orchestration.md#decomposition-and-scheduling).

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

- **Agent side** (does the work) — `oneharness.toml`, discovered from the repo root;
  it prefers `claude-code:alternate` on the alternate subscription and falls back
  to codex.
- **Judge / simulated-user side** (supervises) — `oneharness.judge.toml`, passed
  as the base config's `provider.judge_config`; codex is primary and
  `claude-code:primary` uses only the primary subscription.
- **LLM lint side** — `oneharness.llmlint.toml`, forced by
  `scripts/llmlint-oneharness.sh`; it is codex-only.

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
The lifecycle dispatches in **`bypass`** mode by default — the no-approval mode —
which is correct here because the **whole environment is a sandbox** (a container):
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
is the complete pre-push bar: `check` plus the llmlint diff tier. `just run-plan`
is the recorded mixed-graph executor driven internally by the dedicated
orchestrator onejudge process. The planner launches multi-node work with `just
orchestrate <plan.json>` and supervises its surfaced boundaries and proposals
over the [live channel](docs/orchestration.md#the-plannerorchestrator-channel); it
does not invoke `run-plan` directly. `repo-plan` exists only for compatibility.
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
Use `docs/telemetry.md` to inspect session timing, usage, and the agent/judge
turn timeline with `just telemetry`.
Use `just sweep-scratch --dry-run` to inspect definite dead watchdog scratch and
conservatively stale known third-party scratch; omit `--dry-run` to reclaim it.
Session setup and every recorded round transition run this sweep automatically.
An active lifecycle makes third-party cleanup skip without waiting; PID-proven
dead watchdog cleanup still proceeds.

`just smoke` spends exactly one real agent-harness turn in a throwaway directory
and verifies exact prompt delivery plus a complete native oneharness history
record. It is deliberately outside `just gate`. The pre-push hook runs it only
when the pushed diff touches `scripts/`, `config/oneharness.version`,
`config/onejudge.base.yaml`, `oneharness.toml`, or `oneharness.judge.toml`;
ordinary pushes consume no harness quota.

A dispatched change is not done until `just gate` is green. Its agent clears its
own llmlint findings—by fixing them, adding a justified `ignore-file`, or disabling
an inapplicable rule in `llmlint.yml`—rather than leaving closeout to integration.

Every remote lifecycle PR without explicit title/body metadata gets one
post-verification `pr-author` dispatch. It drafts the template-shaped body from
the actual diff through a temporary out-of-worktree file; drafting failure falls
back to the deterministic body and must never block publication.

## Dogfooding rule

Use the orchestrator harness for **all tasks of sufficient complexity**, in any
repo or project. Use `just dispatch` for one direct-agent node, `just repo-task`
for one lifecycle node, and `just orchestrate <plan.json>` by default for every
multi-node tracked graph. Lifecycle
nodes clone the target, work in an isolated worktree, verify with its gate, and
publish. Dispatch smaller project work with a single task rather than doing it
directly; only the slight-tweak exception above applies. This repo is one
local-mode case of the same rule.

**Self-dispatch rule (this repo).** Never author working-tree changes in the shared
canonical checkout: concurrent orchestrators use it and direct edits race them.
Every change — including plans, personas, docs, and `AGENTS.md` — must be dispatched
into an isolated worktree cut from the registered `local/ai-orchestrator-isolated`
safety clone, with the canonical checkout retained as the positional publication
repository and only fast-forwarded after integration. Pass `--execution-checkout`
for `just repo-task`; in a plan launched by `just orchestrate`, set
`execution_checkout` on each lifecycle node instead of passing a top-level flag.
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
  (`just test`); the gate fails below it.
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

## After the main task

Act on two standing goals beyond the ask: (1) engineer the context for next time
(a real e2e for any journey a bug slipped through, a script for a step you did by
hand, a terse note here for what the code doesn't show); (2) keep the codebase and
environment clean and reproducible. Fold either in when it's the lowest-error path
to the ask; otherwise propose it as a follow-up. Skip busywork.
