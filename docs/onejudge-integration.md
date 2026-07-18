# onejudge integration

How this repo calls [onejudge](https://github.com/nickderobertis/onejudge) and how
the two conversation sides are wired.

This repository adopts exactly **onejudge 0.3.3**, declared once in
`config/onejudge.version` and pinned as the PyPI distribution `onejudge`. That
distribution exposes the Python SDK as `onejudge_sdk` and installs the matching
`onejudge-cli==0.3.3` wheel. `just bootstrap` verifies both the SDK import and the
resolved CLI's exact `onejudge --version` output; setup exits non-zero unless both
report onejudge 0.3.3.

## The layering

```
oneharness  →  one harness invocation, one JSON report   (WHICH harness + model)
onejudge    →  simulated-user loop that drives a task to completion  (this repo calls it)
```

`onejudge run` points a harness at a task and lets an LLM-driven **simulated user
supervise** it — pushing back, asking for verification, re-prompting — until a
`done_when` condition holds or `max_turns` is hit. That supervision is what lets a
subtask reach "actually done" instead of "the agent said done."

## The two sides of the conversation

A onejudge run is a two-party conversation. Harness/model selection for each side
lives in an oneharness config, not in onejudge:

| Side | Role | Config file |
| --- | --- | --- |
| **Agent** | does the work | `oneharness.toml` (discovered from the repo root) |
| **Judge / simulated user** | supervises + scores | `oneharness.judge.toml` (via the base config's `provider.judge_config`) |

Edit those two files (or use oneharness's `ONEHARNESS_*` env overrides) to change
the harness or model on either side. `config/onejudge.base.yaml` carries only the
loop's own concerns (persona defaults, session), never harness/model selection.

## Provider wiring

This repository uses three onejudge provider arrangements:

- `oneharness` is the live worker path. Its agent and simulated-user sides use
  the two harness configs above.
- `command` is the deterministic test path. A local JSON-lines process stands in
  for the paid harness boundary.
- The live orchestrator uses a `split` provider: its `skill` side is either the
  configured oneharness provider or a command provider, while its `judge` side is
  a command invoking `orchestrator.channel.relay_supervisor`. The relay forwards
  supervisor requests over the run's FIFOs to the live planner and returns the
  planner's completion or continuation reply to onejudge. Final boolean/score
  judge calls mirror the persisted planner verdict; the launch removes standalone
  model evals and assessment because the live planner is completion authority.

`orchestrator.dispatch.launch_orchestrator` creates this split config and launches
`onejudge run` with a detached `subprocess.Popen`. This wiring is specific to the
orchestrator persona; worker dispatch retains its ordinary oneharness or command
provider and simulated-user loop.

## How a persona becomes a run

```
config/onejudge.base.yaml   (shared: provider, agent preamble, defaults)
        ⊕
personas/<name>.yaml or personas/<repo>/<name>.yaml
                            (delta: agent role + supervisor persona)
        ⊕
task "<the subtask>"         (passed to the SDK, never merged into a file)
        ↓  orchestrator.config.build_effective_config
effective config object     →  onejudge_sdk.OneJudge.run   →  validated RunResult
```

Repo-specific personas are addressed by their slash-qualified catalog name, such
as `crozier/crozier-corpus`; general cross-repo roles retain top-level names.

`dispatch` passes the effective config object and task to
`onejudge_sdk.OneJudge.run`. The SDK owns the temporary config, stdin task
transport, CLI invocation, and report-contract validation; the orchestrator maps
its typed `RunResult` into the existing `Report`. Exit codes mirror onejudge: `0`
completed, `1` incomplete, and `2` bad config or provider/runtime failure (raised
as a `DispatchError` with onejudge's stderr). onejudge v0.3.3
emits report schema v4, including the unified supervisor's completion reason and
the optional final `assessment` this repository uses to surface follow-up work.

## The init process

`onejudge init` scaffolds all three files — `oneharness.toml`,
`oneharness.judge.toml`, and a starter `onejudge.yaml` — by shelling out to
`oneharness init`. The adopted exact release lives in
`config/oneharness.version`; `just bootstrap` installs and verifies that version:

```sh
onejudge init --force    # writes the two oneharness configs + a starter onejudge.yaml
onejudge schema          # the annotated, authoritative config reference
```

The committed `oneharness.toml` / `oneharness.judge.toml` are **that init output**
with two deliberate edits: the judge side runs a cheaper model than the agent, and
both add an `IS_SANDBOX` env so claude-code runs under root. init's starter
`onejudge.yaml` is not kept — `config/onejudge.base.yaml` supersedes it as the base
this repo merges personas onto.

The committed base and adapter follow the onejudge v0.3.3 schema: persona-authored
`agent.instructions` is internal ai-orchestrator vocabulary and is translated to
onejudge's `system_prompt`; no obsolete onejudge `agent` block reaches the CLI.
The real-CLI e2e suite checks these schema and CLI surfaces before it drives the
same SDK-to-CLI path used in production dispatch.

**Getting the adopted oneharness on this box.** The prebuilt oneharness
release binary needs a newer glibc than the host provides, and the crates.io build
lags behind the 0.3.x releases that added `init`. The **PyPI `oneharness-cli`
wheel** (a manylinux build) is the one that both runs on the host's glibc and
carries `init`, so `scripts/session-setup.sh` installs the exact
`config/oneharness.version` release and rejects a stale binary. Version 0.4.0 is
the adopted release; it succeeds 0.3.24, the first release to carry the
process-tree timeout and partial telemetry fix from
[oneharness PR #1147](https://github.com/nickderobertis/oneharness/pull/1147),
so that fix stays in effect.

**Watch for a stale cargo oneharness.** An earlier `cargo install oneharness`
leaves a 0.2.x binary in `~/.cargo/bin`; its `run` lacks `--mode`, which onejudge's
oneharness provider needs. If it precedes the wheel on `PATH`, live dispatch dies
with a confusing `provider error ... Broken pipe` (the harness process rejects the
flags and exits before the prompt is written). `session-setup.sh` removes it once
the wheel is installed; keep `~/.local/bin` ahead of `~/.cargo/bin` regardless.

## Harnesses and the live path

Live dispatch drives a real harness, chosen by `oneharness.toml`'s fallback chain
(`codex` primary, `claude-code` secondary). The offline gate needs neither; the
timeout e2e gate drives the adopted oneharness with a local fixture, and each live
harness has an **environment requirement** for its tools to actually execute:

- **codex** runs as its own process and executes tools directly, so it is the
  preferred nested harness (the fallback primary). It sandboxes via **bubblewrap**,
  which needs **unprivileged user namespaces**; where the host disallows them (e.g.
  Ubuntu's AppArmor `restrict_unprivileged_userns`), codex can't create its sandbox
  and falls back to read-only. **The fix is `--oneharness-mode bypass`** (codex's
  `--dangerously-bypass-approvals-and-sandbox`): no OS sandbox, so writes work
  regardless of the kernel. To keep a guardrail in place of the sandbox, wire
  **allowlister** as codex's PreToolUse hook with the `repo-write` profile
  (`scripts/session-setup.sh` does this): it auto-allows repo edits and the
  project's build/test, and holds dangerous commands (`rm -rf`, force-push,
  publish) for approval. Verified live end-to-end: a dispatched agent created and
  verified a file in its `--project-dir`, gated by allowlister.
- **claude-code** works standalone, but **inside a bridged/managed Claude Code
  session its nested tool calls are deferred to the outer controller**
  (`stop_reason: tool_deferred`) and never execute — so an orchestrator running
  *inside* such a session cannot dispatch tool-using claude-code agents. Run the
  orchestrator from a standalone shell (or CI), or use codex per above.

Net: the orchestration setup is harness-agnostic and correct. On a
no-unprivileged-userns host, dispatch codex with
`--oneharness-mode bypass` and the allowlister gate; run-plan takes the same flag.

## Dispatching playbook

- **Prepare the harness environment.** codex is oneharness's preferred agent
  harness, but its executable installs in `~/.local/node/bin`. Keep that
  directory on `PATH` or oneharness silently falls back to claude-code;
  `scripts/session-setup.sh` persists the path. The dispatch code also sets
  `ONEHARNESS_TIMEOUT` to 10,800 seconds (three hours), a temporary hard per-turn
  ceiling so legitimate build-heavy agents can finish. Set the variable
  explicitly to override it; onejudge's `max_turns` and the lifecycle `--timeout`
  still bound the whole run independently. Finer inactivity and phase budgets
  remain tracked in issue #6. Project dispatch also pins the agent-side
  oneharness `--config` to this repo's config, which forces codex to
  `gpt-5.6-sol` while retaining claude-code's Claude fallback model. A global
  `ONEHARNESS_MODELS` chain cannot be used here: onejudge supplies `--session`,
  and oneharness rejects multi-model runs combined with a named session.
- **Use the tracked graph for coordinated work.** `just run-plan` accepts direct
  agents, repository lifecycle agents, and explicit human nodes in one recorded
  DAG. A lifecycle `steps` list may mix agent steps with `kind: human` steps on a
  resumable branch. Human nodes never call onejudge; after a person performs the
  reported action, `just next-round RUN --complete-human NODE[/STEP]` records an
  attestation and releases only its dependents. Completed direct agents and
  lifecycle steps are not dispatched again.
- **Prefer the one-command wrapper.** Use
  `just repo-task-auto <repo> <persona> "<task>"`. It sets the dispatch
  environment and reports the branch's commit delta after the run, making
  stranded work visible. Use `just repo-task` or `orchestrator-repo-task` when
  the wrapper is unavailable.
- **Inspect a `not-completed` branch.** This status commonly means the agent hit
  the turn cap at the moment it finished, not that its work failed or vanished.
  Agents commit incrementally, and the lifecycle preserves those commits on the
  branch. Check its commit delta before deciding whether to recover or redispatch.
  The wrapper prints `just repo-recover <branch> --repo <checkout>`; that command
  verifies and publishes the preserved branch through its registered workflow.
- **Choose publication from identity type and workflow.** Omitted type is inferred
  from authenticated GitHub login versus normalized origin owner; pass
  `--repo-type` when that cannot resolve. Team defaults to a ready-for-review open
  PR; single-owner preserves local direct or remote auto publication. Multiple
  aliases share one identity. Migrate every alias atomically with `just
  migrate-repo-type <alias> --repo-type <single-owner|team>` or `just
  migrate-repo-workflow <alias> --workflow <local|remote>`. Configure a local
  single-owner repository's
  working repository with
  `git config receive.denyCurrentBranch updateInstead` so that push can update
  the checked-out base branch.
- **Resolve llmlint findings on touched files.** llmlint evaluates the diff, so
  it can expose a pre-existing pattern in any file the change touches. Fix the
  finding or add a narrow, justified ignore-file suppression (the rule name plus
  why; see `scripts/session-setup.sh` for the directive syntax). If a rule is
  architecturally inapplicable,
  disable it once in `llmlint.yml` with `override: true` and `relevance: false`;
  `async_typed_clients_at_boundaries` is disabled this way because this harness
  is a synchronous CLI.

See [the repository lifecycle](repo-lifecycle.md) for clone, gate, recovery, and
merge mechanics.

## Testing against onejudge without a paid model

onejudge's `command` provider speaks a small JSON-lines protocol
([onejudge v0.3.3 docs/protocol.md](https://github.com/nickderobertis/onejudge/blob/v0.3.3/docs/protocol.md)),
so any command can stand in for the harness. The e2e suite points it at
`tests/e2e/fake_backend.py` — a deterministic backend — so the gate drives the
**real** onejudge CLI and loop across a real subprocess boundary, faking only the
paid model/harness. This is the one sanctioned mock (a genuinely external service),
and it is confined to the provider seam; the merge, SDK dispatch, CLI, and report
validation all run for real. That backend implements onejudge v0.3.3's protocol v4 unified
`supervisor` operation; the e2e fixture rejects any real CLI whose version is not
the adopted `config/onejudge.version` value.
