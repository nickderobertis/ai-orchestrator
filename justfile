# Command surface for ai-orchestrator. `just --list` is the index.
#
# `just bootstrap` must work from a clean clone; `just check` is the deterministic
# quality tier (fails on any issue, no warnings-only mode). The e2e drives the real
# `onejudge` CLI with only the paid model faked (onejudge's `command` provider →
# tests/e2e/fake_backend.py).

set shell := ["bash", "-euo", "pipefail", "-c"]
set positional-arguments

coverage_min := "95"

# List available recipes.
default:
    @just --list

# Set up from a clean clone: install the toolchain, sync the Python env, and
# activate the committed git hooks (the pre-push llmlint gate).
bootstrap:
    ./scripts/session-setup.sh
    bun install --frozen-lockfile
    uv sync
    ./scripts/nx.sh run-many -t build --all --skip-nx-cache
    git config core.hooksPath .githooks
    # Allow local-mode lifecycle pushes into this non-bare checkout.
    git config receive.denyCurrentBranch updateInstead

# Full affected quality gate. A cache-bypassed build runs first so a stale cache
# can never hide a broken clean build.
check:
    ./scripts/nx.sh run-many -t build --all --skip-nx-cache
    ./scripts/nx.sh affected -t format-check,lint,typecheck,test

# Complete pre-push gate: deterministic checks followed by llmlint on this branch.
gate remote=env_var_or_default("ORCHESTRATOR_COMPARISON_REMOTE", "origin") base=env_var_or_default("ORCHESTRATOR_COMPARISON_BASE", ""):
    @comparison=$(scripts/comparison-base.sh "$1" "$2")
    @log=$(mktemp); trap 'rm -f "$log"' EXIT; just check >"$log" 2>&1 || { cat "$log" >&2; echo "gate: deterministic checks failed; fix the reported findings and rerun 'just gate'" >&2; exit 1; }
    @comparison=$(scripts/comparison-base.sh "$1" "$2"); log=$(mktemp); trap 'rm -f "$log"' EXIT; just lint-llm-diff "$comparison" >"$log" 2>&1 || { cat "$log" >&2; echo "gate: llmlint failed; clear the reported findings and rerun 'just gate $1 $2'" >&2; exit 1; }

# Whole suite (unit + e2e) with coverage enforced on the orchestrator package.
test:
    ./scripts/nx.sh run-many -t test --all

# The e2e suite alone (real onejudge subprocess boundary) — quick inner loop.
test-e2e:
    uv run pytest tests/e2e

# Lint Python (ruff) and the shell script (shellcheck); fail on findings.
lint:
    ./scripts/nx.sh run-many -t lint --all

# Static type check.
typecheck:
    ./scripts/nx.sh run-many -t typecheck --all

# Validate every persona against the delta contract (also part of `check`).
validate-personas:
    ./scripts/nx.sh run workspace-tooling:lint

# Format the codebase in place.
format:
    ./scripts/nx.sh run-many -t format --all

# Fail if anything is unformatted (used by the gate).
format-check:
    ./scripts/nx.sh run-many -t format-check --all

# Upgrade dependencies, then re-run the full gate; commit the refreshed lockfile.
upgrade:
    bun update --latest
    uv lock --upgrade
    uv sync
    @just check

# Prove that a real cache-bypassed build succeeds, then that the same target is
# restored from one shared Nx cache in a second linked worktree.
prove-nx-cache:
    ./scripts/prove-nx-cache.sh

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

# List recorded tracked-graph runs and their latest status.
# llmlint: ignore[tool_output_is_signal] the requested multi-line run ledger is this viewing command's product.
runs *args:
    @uv run orchestrator-runs "$@"

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

telemetry *args:
    @uv run orchestrator-telemetry {{args}}

# Show running tasks joined with recent output, branch commits, and ledger rounds.
# Pass N or --all to include recently finished tasks.
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
    @ONEHARNESS_HISTORY_LABELS="$(uv run orchestrator-history-labels role=llmlint)" llmlint "$@"

# Deterministic, model-free llmlint gate: config structure, ignore directives name
# real rules, edited fragments bumped their version. The fast pre-flight.
lint-llm-validate *args:
    @command -v llmlint >/dev/null 2>&1 || { echo "llmlint not installed — run 'just setup-llmlint'"; exit 1; }
    @llmlint validate "$@"

# llmlint scoped to the merge-base diff with main — judges only what the branch
# changed. This is the blocking pre-push check.
lint-llm-diff base="origin/main":
    @command -v llmlint >/dev/null 2>&1 || { echo "llmlint not installed — run 'just setup-llmlint'"; exit 1; }
    ONEHARNESS_HISTORY_LABELS="$(uv run orchestrator-history-labels role=llmlint)" llmlint --diff --diff-base "{{base}}"
