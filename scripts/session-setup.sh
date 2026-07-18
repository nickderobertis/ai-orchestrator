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
#      installed via the PyPI `oneharness-cli` manylinux wheel and verified — the
#      live dispatch path, `onejudge init`, and timeout e2e gate need it. The wheel
#      is used because it runs on older glibc and carries `init`, unlike the
#      prebuilt release binary. See docs/onejudge-integration.md.
#   3. `codex` (the fallback PRIMARY harness) is installed via npm. Auth is a
#      one-time manual `codex login`. See docs/onejudge-integration.md.
#   4. Hands off to `setup-llmlint.sh` to install the llmlint LLM-judge tier.
#
# `set -e` is omitted so optional tool failures do not prevent the remaining
# setup steps. Missing or wrong onejudge/oneharness binaries are different: the
# script finishes the other setup work, then exits non-zero because dispatch and
# the gate require them.
# llmlint: ignore-file[robust_shell, tool_output_is_signal, boundary_inputs_validated] deliberate for a session-startup installer: `set -e` is omitted so optional tool failures don't abort later setup; progress is logged to stderr; the required onejudge and oneharness dependencies are verified explicitly and control the final exit status. CLAUDE_ENV_FILE is a path Claude Code itself provides for the session (a trusted platform input, not external/untrusted data); persist_session_env reads and appends to it exactly as the harness intends, so there is no untrusted boundary to validate.
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

install_onejudge() {
  local current="not installed"
  if command -v onejudge >/dev/null 2>&1; then
    current="$(onejudge --version 2>&1 || echo unusable)"
  fi
  if verify_onejudge >/dev/null 2>&1; then
    return 0
  fi
  if ! command -v uv >/dev/null 2>&1; then
    log "cannot install required onejudge $ADOPTED_ONEJUDGE_VERSION: uv is not installed"
    return 1
  fi
  log "installing onejudge SDK $ADOPTED_ONEJUDGE_VERSION from PyPI (current CLI: $current)"
  if uv sync --project "$REPO_ROOT" >&2; then
    hash -r
    if verify_onejudge; then
      return 0
    fi
  else
    log "onejudge $ADOPTED_ONEJUDGE_VERSION PyPI install failed"
  fi
  log "required onejudge SDK and CLI $ADOPTED_ONEJUDGE_VERSION are unavailable after pinned PyPI install"
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

ensure_codex() {
  # codex is the fallback PRIMARY harness in oneharness.toml — it runs as its own
  # process, so its tools execute directly (nested claude-code defers them; see
  # docs/onejudge-integration.md "Harnesses and the live path"). Install the CLI;
  # authentication is a one-time manual step (`codex login`).
  if command -v codex >/dev/null 2>&1; then
    return 0
  fi
  if command -v npm >/dev/null 2>&1; then
    log "installing @openai/codex via npm"
    npm install -g @openai/codex >&2 2>&1 || log "codex install failed (continuing)"
  else
    log "npm not found; cannot install codex (live path can still use claude-code)"
  fi
}

install_oneharness() {
  # The manylinux `oneharness-cli` wheel both runs on the host glibc and carries
  # `init` (the prebuilt release binary does not on both counts). uv is a
  # clean-clone prerequisite. Accept an existing binary only when it is the exact
  # adopted release; otherwise install the exact wheel and verify it before use.
  local current="not installed"
  if command -v oneharness >/dev/null 2>&1; then
    current="$(oneharness --version 2>&1 || echo unusable)"
  fi
  if verify_oneharness >/dev/null 2>&1; then
    return 0
  fi
  if ! command -v uv >/dev/null 2>&1; then
    log "cannot install required oneharness $ADOPTED_ONEHARNESS_VERSION: uv is not installed"
    return 1
  fi
  log "installing oneharness $ADOPTED_ONEHARNESS_VERSION via uv tool (current: $current)"
  if ! uv tool install --upgrade "oneharness-cli==$ADOPTED_ONEHARNESS_VERSION" >&2; then
    log "oneharness-cli $ADOPTED_ONEHARNESS_VERSION install failed"
    return 1
  fi
  hash -r
  # Remove any stale cargo-installed oneharness. The 0.2.x crates.io build lags the
  # init-capable wheel and its `run` lacks `--mode`, which onejudge's provider
  # needs; if it shadows the wheel on PATH, live dispatch dies with a confusing
  # "provider error ... Broken pipe". Keep only the wheel (on ~/.local/bin).
  if [ -x "$CARGO_BIN/oneharness" ] && [ -x "$BIN_DIR/oneharness" ]; then
    log "removing stale cargo oneharness ($("$CARGO_BIN/oneharness" --version 2>/dev/null || echo unknown)); the wheel supersedes it"
    rm -f "$CARGO_BIN/oneharness"
  fi
  if verify_oneharness; then
    log "oneharness ready ($(oneharness --version))"
    return 0
  fi
  log "required oneharness $ADOPTED_ONEHARNESS_VERSION is unavailable after pinned PyPI install"
  return 1
}

verify_oneharness() {
  local binary actual expected
  expected="oneharness $ADOPTED_ONEHARNESS_VERSION"
  if ! binary="$(command -v oneharness 2>/dev/null)"; then
    log "oneharness verification failed: expected '$expected', but no binary is on PATH"
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
  local path_export
  while IFS= read -r path_export; do
    case "$path_export" in
      "export PATH="*"$NODE_BIN"*) return 0 ;;
    esac
  done <"$CLAUDE_ENV_FILE" 2>/dev/null
  printf -v path_export 'export PATH=%q' "$PATH"
  printf '%s\n' "$path_export" >>"$CLAUDE_ENV_FILE"
}

if [[ ${BASH_SOURCE[0]} != "$0" ]]; then
  return 0
fi

toolchain_failed=0
install_onejudge || toolchain_failed=1
install_oneharness || toolchain_failed=1
ensure_codex
ensure_codex_gate
persist_session_env

# Install the llmlint LLM-judge tier (llmlint + its bundled oneharness).
bash "$SCRIPT_DIR/setup-llmlint.sh" || log "setup-llmlint failed (continuing)"

if verify_onejudge; then
  log "ready (onejudge: $(onejudge --version))"
else
  log "onejudge $ADOPTED_ONEJUDGE_VERSION is required — 'just check' will fail until setup succeeds"
  toolchain_failed=1
fi
if ! verify_oneharness; then
  log "oneharness $ADOPTED_ONEHARNESS_VERSION is required — 'just check' will fail until setup succeeds"
  toolchain_failed=1
fi
exit "$toolchain_failed"
