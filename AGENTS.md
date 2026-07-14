# AGENTS.md

Durable instructions for the **orchestrator** and any agent working in this repo.
Write for a future maintainer, not as a session log. Deterministic steps live in
`orchestrator/` (run via `just`); this file holds the judgment.

> `CLAUDE.md` is a symlink to this file — edit `AGENTS.md` only.

## What this repo is

A local **orchestration harness**: you (the orchestrator) take one large task,
split it into a dependency graph of smaller tasks, and drive each subtask to
completion by dispatching a **[onejudge](https://github.com/nickderobertis/onejudge)**
process with a fitting **persona**. onejudge runs a real coding agent under a
simulated-user supervisor that pushes back until the subtask is actually done.
Your job is the *decomposition, scheduling, and persona choice*; the mechanics of
running onejudge in parallel are scripts. The deliverable is this setup itself —
config, personas, scripts, docs — not a shipped binary.

Beyond dispatching at a directory, the orchestrator manages a change's **full
life cycle** against any repo (GitHub or a local path): clone it, do the work in
an isolated worktree, verify it with the repo's own gate, and merge it — for
GitHub via a PR that auto-merges once required checks are green, for a local repo
via a direct merge into the base branch after the checks pass. One larger task
becomes **multiple isolated PRs** coordinated by a DAG; a single PR can itself run
**several onejudge in sequence on one branch** (a node's `steps` sub-DAG). The DAG
is static within a run — you **adapt between rounds**, reading each round's results
and deriving the next plan (`just replan`). See `docs/repo-lifecycle.md`.

## What "agent" means here

In this repo, an **agent** (or **subagent**) is a **dispatched onejudge process** —
a coding agent run under a simulated-user supervisor via `just dispatch` /
`just repo-task` / `just run-plan`. This is the default sense of the word
everywhere below and in requests to you. When a task says "use an agent," "have an
agent do X," "dispatch an agent," or "spin up a subagent" — including for research
or investigation, not just code changes — dispatch onejudge. Do **not** reach for
the host harness's own subagent mechanism (e.g. Claude Code's `Agent`/`Task` tool,
a "fork") unless the request names it explicitly ("a Claude Code subagent," "the
Task tool"). When the wording is ambiguous, dispatch onejudge.

## Your loop as orchestrator

1. **Decompose.** Break the task into the smallest subtasks that are still worth
   a fresh agent — see the granularity rule below. Capture them as a plan (a DAG):
   each node has an `id`, a `persona`, the `task` prose, and `deps` (ids it needs
   finished first). Start from `examples/plan.example.json`.
2. **Pick or create personas.** Match each subtask to a persona in `personas/`.
   If none fits, create one: `just new-persona <name>` scaffolds
   `personas/<name>.yaml` from the template — fill in the agent role and the
   supervisor's `persona`/`done_when`, then `just check` validates it.
3. **Schedule for parallelism.** Run the plan with `just run-plan <plan.json>`.
   It topologically schedules the DAG, running every subtask whose deps are done
   concurrently (bounded by the plan's `concurrency`), so independent branches go
   in parallel and dependents wait only for what they actually need.
4. **Read the results, then decide.** Each subtask returns a onejudge report
   (completed? / verdicts / usage). Re-plan the next layer from what came back —
   a failed subtask skips its dependents; adjust granularity or persona and
   redispatch. One subtask at a time is `just dispatch <persona> "<task>"`.

The orchestrator does orchestration and planning only: decomposition, scheduling,
persona choice, and merge/integration coordination. Dispatch all target-project
work and research, including integration and closeout. A slight direct tweak to a
dispatched result is allowed only when planning has already made the fix clear and
it is quick to test; otherwise redispatch it.

Prefer each agent proving its own change with `just gate`, leaving integration as
a trivial merge. Before merging or pushing, independently confirm that the gate
exercised the change: relevant tests did not skip and their fixtures, specs, and
inputs were present. A green report or judge verdict without that evidence is not
green.

## The granularity rule (the core judgment)

Maximize parallelism, but **do not over-split**. Every onejudge is a fresh agent
that pays a fixed cost to prepare its context before it does useful work (reading
the repo, orienting). Split only where it buys real parallelism or a genuinely
different persona; keep a subtask whole when splitting it would cost more in
per-agent overhead than it saves in wall-clock or quality. Prefer a coherent
subtask that one agent can hold in its head over many micro-tasks that each
re-pay the setup tax. When unsure, err toward fewer, larger subtasks and split
further only if one proves too big. See `docs/orchestration.md`.

## Personas and the base config

A persona is a small onejudge **delta** file in `personas/` — the agent's role
(`agent.instructions`) plus the supervisor's `persona` / `done_when` / turn cap.
Common settings live once in `config/onejudge.base.yaml` (the base config);
`dispatch` merges base ⊕ persona ⊕ the CLI `--task` into one effective config and
runs `onejudge run` on it. Add a persona rather than overloading an existing one
when a subtask needs a distinct role or review bar. Catalog and authoring rules:
`personas/README.md`.

## The two sides of the conversation

onejudge drives a two-party conversation, and harness/model selection for each
side lives in oneharness config, not onejudge:

- **Agent side** (does the work) — `oneharness.toml`, discovered from the repo root.
- **Judge / simulated-user side** (supervises) — `oneharness.judge.toml`, passed
  as the base config's `provider.judge_config`.

`onejudge init` scaffolds both files plus a starter `onejudge.yaml` (needs
oneharness **0.3.20+**, installed as the `oneharness-cli` PyPI wheel by
`scripts/session-setup.sh`). The committed configs are that output with two
customizations — a cheaper judge model and the `IS_SANDBOX` env — and
`config/onejudge.base.yaml` supersedes init's starter `onejudge.yaml`. Regenerate
with `onejudge init --force`.

**Live dispatch** picks a harness via `oneharness.toml`'s fallback (codex primary).
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
is the complete pre-push bar: `check` plus the llmlint diff tier. `just dispatch` /
`just run-plan` dispatch onejudge at a
directory; `just repo-task <repo> <persona> "<task>"` and
`just repo-plan <repo-plan.json>` drive the full repo life cycle
(clone→gate→PR/merge, multi-PR DAGs, and `steps` workstreams on one PR);
`just replan <prev-plan> <result> [edits]` derives the next round from the last —
see `docs/repo-lifecycle.md`. `just new-persona` / `just validate-personas` round
out the orchestrator verbs. `just lint-llm` /
`lint-llm-diff` / `lint-llm-validate` are the **llmlint** LLM-judge tier — kept
out of `check` (non-deterministic, harness-backed) and enforced at pre-push.

A dispatched change is not done until `just gate` is green. Its agent clears its
own llmlint findings—by fixing them, adding a justified `ignore-file`, or disabling
an inapplicable rule in `llmlint.yml`—rather than leaving closeout to integration.

## Dispatching playbook

Watch a dispatched agent with `just history`, then `just history-show <id>`.
History needs `ONEHARNESS_HISTORY=1`; the dispatch wrappers set it.

Pass long or multi-line task and `--done-when` prose through a file or stdin, not
inline — the same channel the task prose already uses.

Prefer `just repo-task-auto` for a one-command dispatch — it sets up the harness
environment and, afterward, points at the branch holding the agent's commits so a
`not-completed` run's preserved work is never invisible. The judgment that matters:
keep `~/.local/node/bin` on `PATH` or the harness silently falls back off codex, and
read `not-completed` as a turn-cap timing signal rather than a failure — inspect the
branch before concluding work was lost. See `docs/onejudge-integration.md` for the
operational details and `docs/repo-lifecycle.md` for lifecycle mechanics.

## Dogfooding rule

Use the orchestrator harness for **all tasks of sufficient complexity**, in any
repo or project. Decompose the work and drive each substantial piece through
`just repo-task <repo> <persona> "<task>"` or `just repo-plan <plan.json>`; the
lifecycle clones the target, works in an isolated worktree, verifies with its
gate, and merges. Dispatch smaller project work with a single task rather than
doing it directly; only the slight-tweak exception above applies. This repo is one
local-mode case of the same rule.

## Stack and composition

How this repo was built up from the create-repo reference pieces:

- **Product shape:** config / orchestration repo — closest to `shapes/skills-repo.md`
  (determinism-vs-judgment split, validate-in-gate, narrow allowlist), applied to
  onejudge configs + personas rather than skills.
- **Language(s):** Python (uv, ruff, mypy, pytest) for the orchestration package
  in `orchestrator/` — including the repo-lifecycle layer (`workspace`, `gitops`,
  `verify`, `github`, `merge`, `lifecycle`, `replan`) that shells to real
  `git`/`gh`; Bash
  for `scripts/session-setup.sh`; YAML/TOML for configs.
- **Composed:** `base.md` (always) + `shapes/skills-repo.md`.
- **Excluded, and why:** **CI** — deliberately, per the repo's charter: this is a
  local, private proof-of-concept ("local config/scripts/docs at this point"). The
  full gate still runs locally as `just gate` and at pre-push; add
  `.github/workflows/` mirroring it when this graduates past PoC. `releasing.md` —
  nothing versioned is
  published. `monorepo.md` — single deliverable. asdf / direnv / `src` layout —
  unneeded ceremony for a small Python package.
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
  onejudge dispatch (completes / hits the turn cap / persona-merged / a parallel
  DAG that skips a failed node's dependents), and the **repo lifecycle** against a
  real bare git origin — local direct-merge and GitHub PR+auto-merge, plus
  gate-failure, not-completed (including recovery of partial work committed on
  its unmerged branch), no-changes, checks-failed, and a multi-PR DAG. Only
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
of re-approving it each session.

## After the main task

Act on two standing goals beyond the ask: (1) engineer the context for next time
(a real e2e for any journey a bug slipped through, a script for a step you did by
hand, a terse note here for what the code doesn't show); (2) keep the codebase and
environment clean and reproducible. Fold either in when it's the lowest-error path
to the ask; otherwise propose it as a follow-up. Skip busywork.
