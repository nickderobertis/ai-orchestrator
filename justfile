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

# Set up from a clean clone: install onejudge, then sync the Python env.
bootstrap:
    ./scripts/session-setup.sh
    uv sync

# Full quality gate: format check, lint, type check, persona validation, tests
# (unit + e2e, coverage enforced). Must pass before any commit.
check: format-check lint typecheck validate-personas test

# Whole suite (unit + e2e) with coverage enforced on the orchestrator package.
test:
    uv run pytest --cov=orchestrator --cov-report=term-missing --cov-fail-under={{coverage_min}}

# The e2e suite alone (real onejudge subprocess boundary) — quick inner loop.
test-e2e:
    uv run pytest tests/e2e

# Lint; fail on findings.
lint:
    uv run ruff check .

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

# Provision the session toolchain (installs onejudge; ensures oneharness).
# Idempotent; runs automatically via the SessionStart hook. No-ops in CI.
session-setup:
    ./scripts/session-setup.sh
