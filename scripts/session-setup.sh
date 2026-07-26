#!/usr/bin/env bash
# Idempotent dev-toolchain setup for a session. Wired into the Claude Code
# SessionStart hook (.claude/settings.json) and also runnable by hand
# (`just session-setup` / `just bootstrap`).
#
# What it ensures:
#   1. The exact `onejudge` SDK version adopted in `config/onejudge.version` is
#      installed from PyPI. Its dependency supplies the matching `onejudge-cli`
#      wheel, and setup verifies both the Python import and resolved binary.
#   2. The exact `oneharness` version adopted in `config/oneharness.version` is
#      installed into this worktree's project venv by the same `uv sync` and
#      verified as both a distribution and CLI.
#   3. `codex` (the fallback PRIMARY harness) is installed via npm. Auth is a
#      one-time manual `codex login`. See docs/onejudge-integration.md.
#   4. `bun` is installed via npm and verified so oneharness's `sdk-check` gate
#      can run in dispatched worktrees.
#   5. Hands off to `setup-llmlint.sh` to install the llmlint LLM-judge tier.
#
# `set -e` is omitted so optional tool failures do not prevent the remaining
# setup steps. Missing or unusable onejudge, oneharness, or bun binaries are
# different: the script finishes the other setup work, then exits non-zero because
# dispatch and the complete local gate require them.
# llmlint: ignore-file[robust_shell, tool_output_is_signal, boundary_inputs_validated] deliberate for a session-startup installer: `set -e` is omitted so optional tool failures don't abort later setup; progress is logged to stderr; the required onejudge, oneharness, and bun dependencies are verified explicitly and control the final exit status. CLAUDE_ENV_FILE is a path Claude Code itself provides for the session (a trusted platform input, not external/untrusted data); persist_session_env reads and appends to it exactly as the harness intends, so there is no untrusted boundary to validate.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly REPO_ROOT
readonly ONEJUDGE_VERSION_FILE="$REPO_ROOT/config/onejudge.version"
readonly ONEHARNESS_VERSION_FILE="$REPO_ROOT/config/oneharness.version"
ADOPTED_ONEJUDGE_VERSION="$(tr -d '[:space:]' <"$ONEJUDGE_VERSION_FILE")"
readonly ADOPTED_ONEJUDGE_VERSION
ADOPTED_ONEHARNESS_VERSION="$(tr -d '[:space:]' <"$ONEHARNESS_VERSION_FILE")"
readonly ADOPTED_ONEHARNESS_VERSION
readonly BIN_DIR="$HOME/.local/bin"
readonly CARGO_BIN="$HOME/.cargo/bin"
readonly NODE_BIN="$HOME/.local/node/bin"   # npm global prefix (codex lands here)
readonly PROJECT_VENV_BIN="$REPO_ROOT/.venv/bin"
export PATH="$PROJECT_VENV_BIN:$BIN_DIR:$CARGO_BIN:$NODE_BIN:$PATH"
# llmlint forces its nested judge into oneharness read-only mode. The wrapper
# retains that filesystem boundary while granting network capability so codex
# does not ask bubblewrap to configure loopback in a forbidden namespace.
export LLMLINT_ONEHARNESS_BIN="$REPO_ROOT/scripts/llmlint-oneharness.sh"

log() { printf 'session-setup: %s\n' "$*" >&2; }

if [[ ! $ADOPTED_ONEJUDGE_VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  log "invalid adopted onejudge version in $ONEJUDGE_VERSION_FILE: '$ADOPTED_ONEJUDGE_VERSION'"
  if [[ ${BASH_SOURCE[0]} != "$0" ]]; then
    return 1
  fi
  exit 1
fi
if [[ ! $ADOPTED_ONEHARNESS_VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  log "invalid adopted oneharness version in $ONEHARNESS_VERSION_FILE: '$ADOPTED_ONEHARNESS_VERSION'"
  if [[ ${BASH_SOURCE[0]} != "$0" ]]; then
    return 1
  fi
  exit 1
fi

# CI never needs this provisioning; keep it a no-op there.
if [ -n "${CI:-}" ]; then
  log "CI detected; skipping toolchain provisioning"
  exit 0
fi

install_project_dependencies() {
  if verify_onejudge >/dev/null 2>&1 && verify_oneharness >/dev/null 2>&1; then
    return 0
  fi
  if ! command -v uv >/dev/null 2>&1; then
    log "cannot install required project dependencies: uv is not installed"
    return 1
  fi
  log "syncing pinned project dependencies into $REPO_ROOT/.venv"
  if uv sync --project "$REPO_ROOT" >&2; then
    hash -r
    if verify_onejudge && verify_oneharness; then
      return 0
    fi
  else
    log "project dependency sync failed"
  fi
  log "required pinned onejudge and oneharness dependencies are unavailable after uv sync"
  return 1
}

verify_onejudge() {
  local binary actual expected sdk_actual python_bin
  expected="onejudge $ADOPTED_ONEJUDGE_VERSION"
  if ! binary="$(command -v onejudge 2>/dev/null)"; then
    log "onejudge verification failed: expected '$expected', but no binary is on PATH"
    return 1
  fi
  if ! actual="$("$binary" --version 2>&1)"; then
    log "onejudge verification failed: $binary could not report its version"
    return 1
  fi
  if [[ $actual != "$expected" ]]; then
    log "onejudge verification failed: expected '$expected', got '$actual' from $binary"
    return 1
  fi
  python_bin="$PROJECT_VENV_BIN/python"
  if [ ! -x "$python_bin" ]; then
    log "onejudge SDK verification failed: $python_bin is unavailable"
    return 1
  fi
  if ! sdk_actual="$("$python_bin" -c 'import onejudge_sdk; print(onejudge_sdk.__version__)' 2>&1)"; then
    log "onejudge SDK verification failed: onejudge_sdk could not be imported by $python_bin"
    return 1
  fi
  if [[ $sdk_actual != "$ADOPTED_ONEJUDGE_VERSION" ]]; then
    log "onejudge SDK verification failed: expected '$ADOPTED_ONEJUDGE_VERSION', got '$sdk_actual'"
    return 1
  fi
  return 0
}

expose_codex() {
  local codex_binary="$1"
  [ "$codex_binary" != "$BIN_DIR/codex" ] || return 0
  if ! mkdir -p "$BIN_DIR" || ! ln -sfn "$codex_binary" "$BIN_DIR/codex"; then
    log "could not expose $codex_binary at $BIN_DIR/codex (continuing)"
  fi
  hash -r
}

ensure_codex() {
  # codex is the fallback PRIMARY harness in oneharness.toml — it runs as its own
  # process, so its tools execute directly (nested claude-code defers them; see
  # docs/onejudge-integration.md "Harnesses and the live path"). Install the CLI;
  # authentication is a one-time manual step (`codex login`).
  local codex_binary
  if codex_binary="$(command -v codex 2>/dev/null)"; then
    # npm under asdf installs codex inside the selected Node version, while
    # nested workers reliably inherit ~/.local/bin. Refresh a stable entry there
    # on every session so llmlint's oneharness subprocess can resolve the already
    # authenticated subscription CLI without an OPENAI_API_KEY.
    expose_codex "$codex_binary"
    return 0
  fi
  if command -v npm >/dev/null 2>&1; then
    log "installing @openai/codex via npm"
    npm install -g @openai/codex >&2 2>&1 || log "codex install failed (continuing)"
    hash -r
    if codex_binary="$(command -v codex 2>/dev/null)"; then
      expose_codex "$codex_binary"
    fi
  else
    log "npm not found; cannot install codex (live path can still use claude-code)"
  fi
}

verify_oneharness() {
  local binary actual distribution_actual expected python_bin
  expected="oneharness $ADOPTED_ONEHARNESS_VERSION"
  binary="$PROJECT_VENV_BIN/oneharness"
  python_bin="$PROJECT_VENV_BIN/python"
  if [ ! -x "$binary" ]; then
    log "oneharness verification failed: expected '$expected' at $binary"
    return 1
  fi
  if ! actual="$("$binary" --version 2>&1)"; then
    log "oneharness verification failed: $binary could not report its version"
    return 1
  fi
  if [[ $actual != "$expected" ]]; then
    log "oneharness verification failed: expected '$expected', got '$actual' from $binary"
    return 1
  fi
  if ! distribution_actual="$("$python_bin" -c 'import importlib.metadata as metadata; print(metadata.version("oneharness-cli"))' 2>&1)"; then
    log "oneharness distribution verification failed: oneharness-cli metadata is unavailable from $python_bin"
    return 1
  fi
  if [[ $distribution_actual != "$ADOPTED_ONEHARNESS_VERSION" ]]; then
    log "oneharness distribution verification failed: expected '$ADOPTED_ONEHARNESS_VERSION', got '$distribution_actual'"
    return 1
  fi
  return 0
}

install_bun() {
  if verify_bun >/dev/null 2>&1; then
    return 0
  fi
  if ! command -v npm >/dev/null 2>&1; then
    log "cannot install required bun: npm is not installed"
    return 1
  fi
  log "installing bun via npm"
  if ! npm install -g bun >&2; then
    log "bun npm install failed"
    return 1
  fi
  hash -r
  if verify_bun; then
    log "bun ready ($(bun --version))"
    return 0
  fi
  log "required bun is unavailable after npm install"
  return 1
}

verify_bun() {
  local binary actual
  if ! binary="$(command -v bun 2>/dev/null)"; then
    log "bun verification failed: no binary is on PATH"
    return 1
  fi
  if ! actual="$("$binary" --version 2>&1)" || [ -z "$actual" ]; then
    log "bun verification failed: $binary could not report its version"
    return 1
  fi
  return 0
}

ensure_codex_gate() {
  # allowlister gates codex's tool calls via its PreToolUse hook, so codex can run
  # in `--oneharness-mode bypass` (needed where its OS sandbox can't initialize —
  # e.g. a host that disallows unprivileged user namespaces) without losing a
  # guardrail. Wire the repo-write profile when both codex and allowlister exist.
  command -v codex >/dev/null 2>&1 || return 0
  if command -v allowlister >/dev/null 2>&1; then
    allowlister init --harness codex --profile repo-write -y --no-history >&2 2>&1 \
      || log "allowlister codex-hook wiring failed (continuing)"
  else
    log "allowlister not found — codex bypass mode would be ungated (install: https://github.com/nickderobertis/allowlister)"
  fi
}

persist_session_env() {
  [ -n "${CLAUDE_ENV_FILE:-}" ] || return 0
  local env_export has_path=0 has_llmlint_bin=0
  while IFS= read -r env_export; do
    case "$env_export" in
      "export PATH="*"$NODE_BIN"*) has_path=1 ;;
      "export LLMLINT_ONEHARNESS_BIN="*) has_llmlint_bin=1 ;;
    esac
  done <"$CLAUDE_ENV_FILE" 2>/dev/null
  if [ "$has_path" -eq 0 ]; then
    printf -v env_export 'export PATH=%q' "$PATH"
    printf '%s\n' "$env_export" >>"$CLAUDE_ENV_FILE"
  fi
  if [ "$has_llmlint_bin" -eq 0 ]; then
    printf -v env_export 'export LLMLINT_ONEHARNESS_BIN=%q' "$LLMLINT_ONEHARNESS_BIN"
    printf '%s\n' "$env_export" >>"$CLAUDE_ENV_FILE"
  fi
}

if [[ ${BASH_SOURCE[0]} != "$0" ]]; then
  return 0
fi

toolchain_failed=0
install_project_dependencies || toolchain_failed=1
if [ -f "$REPO_ROOT/justfile" ] && command -v just >/dev/null 2>&1; then
  just --justfile "$REPO_ROOT/justfile" --working-directory "$REPO_ROOT" sweep-scratch >&2 \
    || log "scratch sweep failed; continuing session setup"
else
  log "scratch sweep unavailable; continuing session setup"
fi
install_bun || toolchain_failed=1
ensure_codex
ensure_codex_gate
persist_session_env

# Install the llmlint LLM-judge tier (llmlint + its bundled oneharness).
bash "$SCRIPT_DIR/setup-llmlint.sh" || log "setup-llmlint failed (continuing)"

if verify_onejudge; then
  onejudge_binary="$(command -v onejudge)"
  log "ready (onejudge: $("$onejudge_binary" --version) at $onejudge_binary)"
else
  log "onejudge $ADOPTED_ONEJUDGE_VERSION is required — 'just check' will fail until setup succeeds"
  toolchain_failed=1
fi
if ! verify_oneharness; then
  log "oneharness $ADOPTED_ONEHARNESS_VERSION is required — 'just check' will fail until setup succeeds"
  toolchain_failed=1
fi
if ! verify_bun; then
  log "bun is required — the oneharness sdk-check gate will fail until setup succeeds"
  toolchain_failed=1
fi
exit "$toolchain_failed"
