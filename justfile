# Command surface for ai-orchestrator. `just --list` is the index.
#
# `just bootstrap` must work from a clean clone; `just check` is the deterministic
# quality tier (fails on any issue, no warnings-only mode). The e2e drives the real
# `onejudge` CLI with only the paid model faked (onejudge's `command` provider →
# tests/e2e/fake_backend.py).

set shell := ["bash", "-euo", "pipefail", "-c"]
set positional-arguments

# The coverage floor is not restated here: `[tool.coverage.report] fail_under` in
# pyproject.toml is its one source, enforced by the Nx `test` target this recipe runs.
repo_root := justfile_directory()

# List available recipes.
default:
    @just --list

# Set up from a clean clone: install the toolchain, sync the Python env, and
# activate the committed git hooks (the pre-push llmlint gate).
# llmlint: ignore[changed_behavior_has_e2e] This provisioning journey is run on every clean-clone bootstrap; recursively bootstrapping from its own e2e would replace the active test environment.
bootstrap:
    ./scripts/session-setup.sh
    @log=$(mktemp); trap 'rm -f "$log"' EXIT; bun install --frozen-lockfile >"$log" 2>&1 || { cat "$log" >&2; echo "bootstrap: repair package.json/bun.lock and retry" >&2; exit 1; }
    ./scripts/nx.sh run-many -t bootstrap
    git config core.hooksPath .githooks
    # Allow local-mode lifecycle pushes into this non-bare checkout.
    git config receive.denyCurrentBranch updateInstead

# Full quality gate: format check, lint, type check, persona validation, tests
# (unit + e2e, coverage enforced). Must pass before any commit.
#
# Every captured stage writes through `scripts/preserved-log.sh` to `.logs/<label>.log`
# (owner-only, credential values redacted, truncated per run). That log survives the
# process, so a failure is still readable afterwards and a *running* recipe can be
# followed with `tail -f .logs/check.log` instead of through /proc.
# llmlint: ignore[changed_behavior_has_e2e] The public recipe is the real deterministic gate invoked by this task and pre-push; its sequencing failures use subprocess doubles to avoid recursively invoking the same full suite.
check:
    @if [[ ! -x node_modules/.bin/nx ]]; then source ./scripts/preserved-log.sh; preserved_log_open "{{repo_root}}" check-install; log=$PRESERVED_LOG; bun install --frozen-lockfile 2>&1 | redact_secrets >"$log" || { cat "$log" >&2; echo "check: install locked workspace dependencies and retry (full output: $log)" >&2; exit 1; }; fi
    @source ./scripts/preserved-log.sh; preserved_log_open "{{repo_root}}" check; log=$PRESERVED_LOG; { ./scripts/nx.sh run-many -t format-check,lint,typecheck,test,test-docs && ./scripts/check-oneharness-ui-contract.sh && python3 ./scripts/check-dag-state-contract.py && ./scripts/check-nx-cache.sh; } 2>&1 | redact_secrets >"$log" || { cat "$log" >&2; echo "check: deterministic checks failed; fix the reported findings and retry (full output: $log)" >&2; exit 1; }; total=$(./scripts/coverage-total.sh "{{repo_root}}"); echo "check: all deterministic checks passed${total:+ (line coverage ${total}%)}"

# Complete pre-push gate: deterministic checks followed by llmlint on this branch.
#
# A passing run says everything it has to say on one line: the line coverage it
# measured, which base commit the llmlint verdict covers, and whether that verdict
# was judged now or replayed. The judge is non-deterministic and its verdict is
# memoized, so "green" is a claim about one judged diff against one base commit —
# and a worker whose gate replayed a recorded verdict needs to know that, and which
# base commit it covers, before it settles. The coverage total is measured either
# way; printing it here is what stops a reader opening `.coverage` by hand.
# llmlint: ignore[changed_behavior_has_e2e] Running the complete gate from a test would recursively run this same suite; the tier whose provenance is passed through here is proven end to end in tests/e2e/test_llmlint_cache_e2e.py.
gate remote=env_var_or_default("ORCHESTRATOR_COMPARISON_REMOTE", "origin") base=env_var_or_default("ORCHESTRATOR_COMPARISON_BASE", ""):
    @comparison=$(scripts/comparison-base.sh "$1" "$2")
    @source ./scripts/preserved-log.sh; preserved_log_open "{{repo_root}}" gate-check; log=$PRESERVED_LOG; just check 2>&1 | redact_secrets >"$log" || { cat "$log" >&2; echo "gate: deterministic checks failed; fix the reported findings and rerun 'just gate' (full output: $log)" >&2; exit 1; }
    @source ./scripts/preserved-log.sh; comparison=$(scripts/comparison-base.sh "$1" "$2"); preserved_log_open "{{repo_root}}" gate-llmlint; log=$PRESERVED_LOG; just lint-llm-diff "$comparison" 2>&1 | redact_secrets >"$log" || { cat "$log" >&2; echo "gate: llmlint failed; clear the reported findings against 'just lint-llm-diff $comparison' alone, then rerun 'just gate $1 $2' once to confirm (full output: $log)" >&2; exit 1; }; provenance=$(grep -m1 -E '^lint-llm-diff: (judged|replayed) ' "$log" || echo "lint-llm-diff: verdict provenance unavailable"); note=$(grep -q '^lint-llm-diff: ignoring ' "$log" && echo " [ignored an ambient global Nx cache skip]" || true); total=$(./scripts/coverage-total.sh "{{repo_root}}"); echo "gate: complete gate passed${total:+ (line coverage ${total}%)}; ${provenance#lint-llm-diff: }${note}"

# Spend one real harness turn proving prompt delivery and complete history telemetry.
# Kept out of `gate`; pre-push selects it only for launch-path changes.
smoke:
    @uv run orchestrator-smoke

# Whole suite (unit + e2e) with coverage enforced on the orchestrator package.
#
# Extra Nx flags pass straight through, which is how one tier is forced to re-run
# rather than replay: `just test --skip-nx-cache`. It is deliberately
# per-invocation — an exported global cache skip re-rolls every tier from every
# unrelated command, including the ones whose contract is cache replay.
#
# A green run says one line, like `check`: the suite's own output is the failure
# report, and it is streamed in full when there is one.
test *nx_args:
    @log=$(mktemp); trap 'rm -f "$log"' EXIT; ./scripts/nx.sh run-many -t test,test-docs {{nx_args}} >"$log" 2>&1 || { cat "$log" >&2; echo "test: suites failed; fix the reported findings and rerun 'just test'" >&2; exit 1; }; echo "test: all suites passed"

# The e2e suite alone (real onejudge subprocess boundary) — quick inner loop.
test-e2e:
    uv run pytest tests/e2e

# Lint Python (ruff) and the shell script (shellcheck); fail on findings.
lint:
    ./scripts/nx.sh affected -t lint

# Static type check.
typecheck:
    ./scripts/nx.sh affected -t typecheck

# Validate every persona against the delta contract (also part of `check`).
validate-personas:
    uv run orchestrator-validate-personas

# Format the codebase in place.
format:
    ./scripts/nx.sh affected -t format

# Fail if anything is unformatted (used by the gate).
format-check:
    ./scripts/nx.sh run-many -t format-check

# Upgrade dependencies, then re-run the full gate; commit the refreshed lockfile.
# llmlint: ignore[changed_behavior_has_e2e] The public recipe's real Bun success/failure paths run in an isolated fixture; uv and Nx are subprocess doubles because recursively running the full upgraded suite from pytest cannot terminate.
upgrade:
    @log=$(mktemp); trap 'rm -f "$log"' EXIT; { uv lock --upgrade && uv sync && bun update --latest nx @nx/eslint @nx/eslint-plugin @nx/js eslint typescript@6 typescript-eslint @biomejs/biome && ./scripts/nx.sh run-many -t build,lint,typecheck,test,test-docs; } >"$log" 2>&1 || { cat "$log" >&2; echo "upgrade: repair dependency constraints or target findings and retry" >&2; exit 1; }; echo "upgrade: dependencies refreshed and targets passed"

# Local-first runs no CI, but origin is the shared source of truth: push every
# change that lands on main. The pre-push hook gates this like any push; if git
# refuses a non-fast-forward, fetch and rebase before retrying.
sync branch="" remote="origin":
    @comparison=$(scripts/comparison-base.sh "$2" "$1"); branch=${comparison#"$2/"}; git push --quiet -- "$2" "$branch" || { echo "sync: push failed; fetch '$2' and rebase '$branch', then retry" >&2; exit 1; }

# --- orchestrator verbs ---------------------------------------------------

# Dispatch one subtask: `just dispatch <persona> "<task>"`.
dispatch *args:
    @uv run orchestrator-dispatch "$@"

# Run the canonical tracked graph: direct agents, lifecycle agents, and humans.
# `just run-plan <plan.json>`.
run-plan *args:
    @uv run orchestrator-run-plan "$@"

# Launch the dedicated orchestrator with a host-visible live planner channel.
# llmlint: ignore[tool_output_is_signal] orchestrate reports validated launch failures and the caller can retry after correcting the named input.
orchestrate *args:
    @uv run orchestrator-orchestrate "$@"

# Bounded host-side reads and replies for the live planner channel.
# llmlint: ignore[tool_output_is_signal] channel-next returns a structured bounded status or a validated transport error for planner recovery.
channel-next *args:
    @uv run orchestrator-channel-next "$@"

# llmlint: ignore[tool_output_is_signal] channel-reply validates the reply and names transport/rendezvous failures so the planner can reattach and retry.
channel-reply *args:
    @uv run orchestrator-channel-reply "$@"

channel-surface *args:
    @uv run orchestrator-channel-surface "$@"

channel-approve *args:
    @uv run orchestrator-channel-approve "$@"

channel-reject *args:
    @uv run orchestrator-channel-reject "$@"

channel-continue *args:
    @uv run orchestrator-channel-continue "$@"

# Drive one subtask through a repo's full lifecycle (clone→gate→PR/merge):
# `just repo-task <repo> <persona> "<task>"`. `<repo>` is a GitHub name/slug/URL
# or a local path; direct base merge requires an explicit registered local workflow.
repo-task *args:
    @uv run orchestrator-repo-task "$@"

# Add preferred-harness PATH setup and preserved-commit recovery reporting to
# repo-task. Omit task (or pass `-`) to read a long task from stdin.
repo-task-auto *args:
    @./scripts/repo-task-auto.sh "$@"

# Remove dead watchdog scratch and conservatively stale known third-party scratch.
# Pass `--dry-run` to inspect candidates without removing them.
sweep-scratch *args:
    @uv run orchestrator-sweep-scratch "$@"

# Verify and publish a lifecycle-preserved branch through its registered workflow.
# `just repo-recover <branch> --repo <canonical-checkout>`.
repo-recover *args:
    @uv run orchestrator-repo-recover "$@"

# Deprecated alias for run-plan; old repo-plan inputs are still accepted unchanged.
repo-plan *args:
    @uv run orchestrator-repo-plan "$@"

# Derive the next round's plan from the last round's results + edits.
replan *args:
    @uv run orchestrator-replan "$@"

# Update, verify, fast-forward, and optionally push completed workstream branches:
# `just integrate claude/a claude/b --push`; omit branches to auto-discover them.
# llmlint: ignore[tool_output_is_signal] the requested readable per-branch train summary is this verb's product.
integrate *args:
    @uv run orchestrator-integrate "$@"

# Derive and run the next recorded tracked-graph round, optionally applying
# edits and/or `--complete-human NODE_ID[/STEP_ID]` attestations.
# llmlint: ignore[tool_output_is_signal] the tracked result and continuation guidance are this command's product.
next-round *args:
    @uv run orchestrator-next-round "$@"

# List recorded tracked-graph runs, who launched them, and their latest status.
# `just runs --mine` lists only the runs this session launched.
# llmlint: ignore[tool_output_is_signal] the requested multi-line run ledger is this viewing command's product.
runs *args:
    @uv run orchestrator-runs "$@"

# Stop a run this session launched, tree and all: `just stop <run-id>`. Refuses a run
# another planner launched, or one with no recorded launcher, unless given --force.
# A stopped run stays reclaimable through `just run-plan ... --recover`.
# llmlint: ignore[tool_output_is_signal] the ownership refusal and what was stopped are this command's product.
stop *args:
    @uv run orchestrator-stop "$@"

# llmlint: ignore[tool_output_is_signal] the requested cross-project goal inventory is this viewing command's product.
goals *args:
    @uv run orchestrator-goals "$@"

# Show every node outcome in one run and concrete full-log paths for failures.
# llmlint: ignore[tool_output_is_signal] this command is the requested results view.
results *args:
    @uv run orchestrator-results "$@"

# Register a repository checkout alias. Workflow belongs to its shared identity.
register-repo *args:
    @uv run orchestrator-register-repo "$@"

# Atomically migrate publication workflow for every checkout alias of one identity.
migrate-repo-workflow *args:
    @uv run orchestrator-migrate-repo-workflow "$@"

# Atomically migrate repository type for an identity; team also selects remote workflow.
migrate-repo-type *args:
    @uv run orchestrator-migrate-repo-type "$@"

migrate-repo-gate *args:
    @uv run orchestrator-migrate-repo-gate "$@"

# List repository identities and checkout aliases, optionally refreshing them.
# llmlint: ignore[tool_output_is_signal] the requested repo registry listing is this viewing command's product.
repos *args:
    @uv run orchestrator-repos "$@"

# List recent dispatched worker sessions across every target repo.
# llmlint: ignore[tool_output_is_signal] human-readable history is this viewing command's product.
history *args:
    uv run orchestrator-history {{args}}

# Show a readable progress digest for the newest matching worker session.
# llmlint: ignore[tool_output_is_signal] the requested multi-line digest is this viewing command's product.
history-show *args:
    uv run orchestrator-history-show {{args}}

# Follow one tracked-graph run as one concise event stream, aggregating the run
# journal, its labelled oneharness sessions, its lifecycle-branch commits, and its
# linked PR state. `just monitor [RUN_ID]`; defaults to the newest active run.
# Only successful graph completion exits 0 — waiting/failed/stopped heartbeat on.
# llmlint: ignore[tool_output_is_signal] the requested continuous event stream is this viewing command's product.
monitor *args:
    uv run orchestrator-monitor {{args}}

# Attach to one launched run and stay attached: `just watch RUN`. Prints planner
# surfaces as they arrive and node transitions as they happen, and returns when the
# run settles — exit 0 only when the graph completed. `orchestrate` launches detached
# so several runs can be supervised at once; this is the one command that attaches to
# one of them, instead of composing `channel-next` with the read-only views. Replies
# still go through `just channel-reply`.
# llmlint: ignore[tool_output_is_signal] the requested live surface-and-transition stream is this viewing command's product.
watch *args:
    uv run orchestrator-watch {{args}}

# Emit the schema-versioned run telemetry index, or `just telemetry <run-id>` for one
# named run — settled or not. `--breakdown` renders the operator timing view.
telemetry *args:
    @uv run orchestrator-telemetry {{args}}

# Serve the read-only DAG Observatory against a running telemetry server.
# llmlint: ignore[tool_output_is_signal] Vite startup and request logs are the foreground development server's operator-facing product.
dag-ui:
    ./scripts/nx.sh run dag-ui:serve

# Serve the read-only DAG telemetry API (FastAPI + SSE), loopback-bound by default.
# llmlint: ignore[tool_output_is_signal] the requested long-running read API is this command's product.
telemetry-server *args:
    uv run orchestrator-telemetry-server {{args}}

# Show running tasks joined with recent output, branch commits, and ledger rounds.
# Pass N or --all to include recently finished tasks, or `just status <run-id>` to
# scope the view to one run's own indicators and dispatched sessions.
# llmlint: ignore[tool_output_is_signal] the requested multi-task status report is this viewing command's product.
status *args:
    @uv run orchestrator-status {{args}}

# Scaffold a new persona: `just new-persona <name>`.
new-persona *args:
    @uv run orchestrator-new-persona "$@"

# Provision the session toolchain (installs onejudge, oneharness, bun, and llmlint).
# Idempotent; runs automatically via the SessionStart hook. No-ops in CI.
session-setup:
    # llmlint: ignore[tool_output_is_signal] installation progress and per-tool verification diagnostics are the session setup's operator-facing result.
    ./scripts/session-setup.sh

# --- llmlint (LLM-judge tier) --------------------------------------------
# Non-deterministic, harness-backed, and kept OUT of `just check`. It runs at
# pre-push (.githooks/pre-push) and on demand. Config is the composed llmlint.yml.

# Install/refresh the llmlint toolchain (llmlint + its oneharness). Idempotent.
setup-llmlint:
    ./scripts/setup-llmlint.sh

# LLM-judge lint over the whole tree (or pass paths to narrow). Run once with
# `just setup-llmlint` first in a plain terminal.
#
# `role=llmlint` stamps this tier's own harness sessions so they are separable from
# the agent/judge sessions of the work being linted (whose roles come from
# oneharness.toml / oneharness.judge.toml). It is layered over — not substituted
# for — the graph labels inherited when a dispatched agent runs its own gate, so a
# finding stays attributable to the run/round/node that provoked it.
lint-llm *paths:
    @command -v llmlint >/dev/null 2>&1 || { echo "llmlint not installed — run 'just setup-llmlint'"; exit 1; }
    @PATH="{{repo_root}}/.venv/bin:$PATH" LLMLINT_ONEHARNESS_BIN="{{repo_root}}/scripts/llmlint-oneharness.sh" ONEHARNESS_HISTORY_LABELS="$(uv run orchestrator-history-labels role=llmlint)" llmlint "$@"

# Deterministic, model-free llmlint gate: config structure, ignore directives name
# real rules, edited fragments bumped their version. The fast pre-flight.
lint-llm-validate *args:
    @command -v llmlint >/dev/null 2>&1 || { echo "llmlint not installed — run 'just setup-llmlint'"; exit 1; }
    @llmlint validate "$@"

# llmlint scoped to the merge-base diff with main — judges only what the branch
# changed. This is the blocking pre-push check.
#
# The judge is non-deterministic, so its verdict is memoized by the cached Nx
# `workspace:lint-llm-diff` target: an unchanged tree judged against an unchanged
# base replays the recorded verdict instead of rolling the dice again. The base
# ref is resolved to a commit here, before Nx hashes it, so a rebased or advanced
# base misses rather than replaying a verdict computed against a different base.
# The resolved base commit is reported with the verdict, because "green" means
# green *against that commit*: a gate run and the publication rebuild that judge
# different base commits are answering different questions.
#
# `just lint-llm-diff <base> --skip-nx-cache` is the one supported way to force a
# real re-judge, and it is deliberately per-invocation. An ambient global Nx cache
# skip (`NX_SKIP_NX_CACHE` / `NX_DISABLE_NX_CACHE`, exported to re-judge this tier
# and inherited by everything else) is reported and ignored here: it would re-roll
# a non-deterministic judge from every unrelated command, and it silently breaks
# the checks whose contract is cache replay — the llmlint verdict-replay journeys
# and `scripts/check-nx-cache.sh`. Every other Nx target still honours it.
#
# The target records the verdict and exits 0 so Nx will cache a failing one too;
# the last line is what enforces it, replaying the findings and the judged status.
# A failure therefore fails identically whether it was just judged or replayed.
# The recorded verdict and its judged marker are cleared first because Nx leaves
# pre-existing outputs alone rather than comparing them: without this, a stale or
# edited record would be replayed in place of the cached one. Nx's own success line
# is dropped so a run says exactly two things: one line of provenance — this verdict,
# from here, against that base commit — and the verdict itself. Its failures still
# reach stderr.
# llmlint: ignore[tool_output_is_signal] the judge's per-rule report and its one-line provenance are this tier's product; see scripts/llmlint-verdict.sh.
lint-llm-diff base="origin/main" *nx_args:
    @command -v llmlint >/dev/null 2>&1 || { echo "llmlint not installed — run 'just setup-llmlint'"; exit 1; }
    @base_sha=$(git rev-parse --verify --quiet "{{base}}^{commit}") || { echo "lint-llm-diff: '{{base}}' does not resolve to a commit; fetch it or pass an existing base" >&2; exit 1; }; if [[ -n "${NX_SKIP_NX_CACHE:-}${NX_DISABLE_NX_CACHE:-}" ]]; then echo "lint-llm-diff: ignoring the ambient global Nx cache skip; force a fresh judgement of this tier alone with 'just lint-llm-diff {{base}} --skip-nx-cache'" >&2; fi; unset NX_SKIP_NX_CACHE NX_DISABLE_NX_CACHE; rm -rf "{{repo_root}}/.nx/llmlint-diff" "{{repo_root}}/.nx/llmlint-diff.judged"; LLMLINT_DIFF_BASE_SHA="$base_sha" ./scripts/nx.sh run workspace:lint-llm-diff {{nx_args}} >/dev/null; LLMLINT_DIFF_BASE_SHA="$base_sha" ./scripts/llmlint-verdict.sh
