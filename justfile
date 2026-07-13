# Command surface for ai-orchestrator. `just --list` is the index.
#
# `just bootstrap` must work from a clean clone; `just check` is the full quality
# gate (fails on any issue, no warnings-only mode). The gate is deterministic and
# offline: the e2e drives the real `onejudge` CLI with only the paid model faked
# (onejudge's `command` provider → tests/e2e/fake_backend.py).

set shell := ["bash", "-euo", "pipefail", "-c"]

coverage_min := "95"

# List available recipes.
default:
    @just --list

# Set up from a clean clone: install the toolchain, sync the Python env, and
# activate the committed git hooks (the pre-push llmlint gate).
bootstrap:
    ./scripts/session-setup.sh
    uv sync
    git config core.hooksPath .githooks

# Full quality gate: format check, lint, type check, persona validation, tests
# (unit + e2e, coverage enforced). Must pass before any commit.
check: format-check lint typecheck validate-personas test

# Whole suite (unit + e2e) with coverage enforced on the orchestrator package.
test:
    uv run pytest --cov=orchestrator --cov-report=term-missing --cov-fail-under={{coverage_min}}

# The e2e suite alone (real onejudge subprocess boundary) — quick inner loop.
test-e2e:
    uv run pytest tests/e2e

# Lint Python (ruff) and the shell script (shellcheck); fail on findings.
lint:
    uv run ruff check .
    @command -v shellcheck >/dev/null 2>&1 || { echo "shellcheck not installed (needed to lint the shell scripts) — https://github.com/koalaman/shellcheck#installing"; exit 1; }
    shellcheck scripts/*.sh .githooks/pre-push

# Static type check.
typecheck:
    uv run mypy

# Validate every persona against the delta contract (also part of `check`).
validate-personas:
    uv run orchestrator-validate-personas

# Format the codebase in place.
format:
    uv run ruff format .

# Fail if anything is unformatted (used by the gate).
format-check:
    uv run ruff format --check .

# Upgrade dependencies, then re-run the full gate; commit the refreshed lockfile.
upgrade:
    uv lock --upgrade
    uv sync
    @just check

# --- orchestrator verbs ---------------------------------------------------

# Dispatch one subtask: `just dispatch <persona> "<task>"`.
dispatch *args:
    uv run orchestrator-dispatch {{args}}

# Run a plan (task DAG): `just run-plan <plan.json>`.
run-plan *args:
    uv run orchestrator-run-plan {{args}}

# Scaffold a new persona: `just new-persona <name>`.
new-persona *args:
    uv run orchestrator-new-persona {{args}}

# Provision the session toolchain (installs onejudge; ensures oneharness; llmlint).
# Idempotent; runs automatically via the SessionStart hook. No-ops in CI.
session-setup:
    ./scripts/session-setup.sh

# --- llmlint (LLM-judge tier) --------------------------------------------
# Non-deterministic, harness-backed, and kept OUT of `just check`. It runs at
# pre-push (.githooks/pre-push) and on demand. Config is the composed llmlint.yml.

# Install/refresh the llmlint toolchain (llmlint + its oneharness). Idempotent.
setup-llmlint:
    ./scripts/setup-llmlint.sh

# LLM-judge lint over the whole tree (or pass paths to narrow). Run once with
# `just setup-llmlint` first in a plain terminal.
lint-llm *paths:
    @command -v llmlint >/dev/null 2>&1 || { echo "llmlint not installed — run 'just setup-llmlint'"; exit 1; }
    llmlint {{paths}}

# Deterministic, model-free llmlint gate: config structure, ignore directives name
# real rules, edited fragments bumped their version. The fast pre-flight.
lint-llm-validate *args:
    @command -v llmlint >/dev/null 2>&1 || { echo "llmlint not installed — run 'just setup-llmlint'"; exit 1; }
    llmlint validate {{args}}

# llmlint scoped to the merge-base diff with main — judges only what the branch
# changed. This is the blocking pre-push check.
lint-llm-diff base="origin/main":
    @command -v llmlint >/dev/null 2>&1 || { echo "llmlint not installed — run 'just setup-llmlint'"; exit 1; }
    llmlint --diff --diff-base "{{base}}"
