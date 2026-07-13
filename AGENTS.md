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

`onejudge init` scaffolds all three files (needs oneharness **0.3.20+**). The
committed copies are the hand-maintained equivalent — see
`docs/onejudge-integration.md` for why `init` is not run on this box and how to
regenerate them where a newer oneharness is available.

## Command surface

Use the `just` recipes (`just --list` is the index); do not hand-roll
equivalents. `just bootstrap` sets up from a clean clone (installs `onejudge`);
`just check` is the full gate and must pass before any commit. `just dispatch`,
`just run-plan`, `just new-persona`, `just validate-personas` are the orchestrator
verbs.

## Stack and composition

How this repo was built up from the create-repo reference pieces:

- **Product shape:** config / orchestration repo — closest to `shapes/skills-repo.md`
  (determinism-vs-judgment split, validate-in-gate, narrow allowlist), applied to
  onejudge configs + personas rather than skills.
- **Language(s):** Python (uv, ruff, mypy, pytest) for the orchestration package
  in `orchestrator/`; Bash for `scripts/session-setup.sh`; YAML/TOML for configs.
- **Composed:** `base.md` (always) + `shapes/skills-repo.md`.
- **Excluded, and why:** **CI** — deliberately, per the repo's charter: this is a
  local, private proof-of-concept ("local config/scripts/docs at this point"). The
  full gate still runs locally as `just check`; add `.github/workflows/` running
  `just bootstrap && just check` when this graduates past PoC. `releasing.md` —
  nothing versioned is published. `monorepo.md` — single deliverable. `llmlint`
  tier — heavyweight (needs a harness credential); not warranted for a local PoC.
  asdf / direnv / `src` layout — unneeded ceremony for a small Python package.

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

- **e2e** (`tests/e2e/`) proves the real journeys against the real onejudge
  boundary: a single dispatch that completes (exit 0), one that hits the turn cap
  without finishing (exit 1), a persona-merged dispatch, and a multi-node DAG that
  runs branches in parallel and skips a failed node's dependents.
- **unit** (`tests/`) covers the pure logic: base⊕persona merge, plan topological
  scheduling / concurrency / skip-on-failure, persona scaffolding, and validation
  rejecting malformed configs.
- A new orchestrator verb isn't done until its real journey lands in `tests/e2e/`.

## Commits and merging

Squash-merge via PR is the intended model; PRs follow
`.github/pull_request_template.md` (terse **What** / **Why**). Branch protection
and required checks are deferred with CI (see the exclusion above) — until then
`just check` is the gate, run locally before every commit. Keep the
`.claude/settings.json` allowlist current: add a new routine command there instead
of re-approving it each session.

## After the main task

Act on two standing goals beyond the ask: (1) engineer the context for next time
(a real e2e for any journey a bug slipped through, a script for a step you did by
hand, a terse note here for what the code doesn't show); (2) keep the codebase and
environment clean and reproducible. Fold either in when it's the lowest-error path
to the ask; otherwise propose it as a follow-up. Skip busywork.
