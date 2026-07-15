#!/usr/bin/env bash
# Idempotent dev-toolchain setup for a session. Wired into the Claude Code
# SessionStart hook (.claude/settings.json) and also runnable by hand
# (`just session-setup` / `just bootstrap`).
#
# What it ensures:
#   1. The exact `onejudge` version adopted in `config/onejudge.version` is
#      installed — the offline gate drives it as a subprocess. Uses that release's
#      prebuilt install.sh where an archive exists (x86_64 Linux, macOS); otherwise
#      builds the same version from crates.io (`cargo install`), which is the path
#      on Linux aarch64. Every path verifies the resolved binary before continuing.
#   2. `oneharness` (0.3.20+, init-capable) is installed via the PyPI
#      `oneharness-cli` manylinux wheel — the LIVE dispatch path and `onejudge
#      init` need it (the offline gate does not). The wheel is used because it
#      runs on older glibc and carries `init`, unlike the prebuilt release binary.
#      See docs/onejudge-integration.md.
#   3. `codex` (the fallback PRIMARY harness) is installed via npm. Auth is a
#      one-time manual `codex login`. See docs/onejudge-integration.md.
#   4. Hands off to `setup-llmlint.sh` to install the llmlint LLM-judge tier.
#
# `set -e` is omitted so optional tool failures do not prevent the remaining
# setup steps. A missing or wrong onejudge is different: the script finishes the
# other setup work, then exits non-zero because dispatch and the gate require it.
# llmlint: ignore-file[robust_shell, tool_output_is_signal, boundary_inputs_validated] deliberate for a session-startup installer: `set -e` is omitted so optional tool failures don't abort later setup; progress is logged to stderr; the required onejudge dependency is verified explicitly and controls the final exit status. CLAUDE_ENV_FILE is a path Claude Code itself provides for the session (a trusted platform input, not external/untrusted data); persist_session_env reads and appends to it exactly as the harness intends, so there is no untrusted boundary to validate.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly REPO_ROOT
readonly ONEJUDGE_VERSION_FILE="$REPO_ROOT/config/onejudge.version"
ADOPTED_ONEJUDGE_VERSION="$(tr -d '[:space:]' <"$ONEJUDGE_VERSION_FILE")"
readonly ADOPTED_ONEJUDGE_VERSION
readonly ONEJUDGE_RELEASE="v$ADOPTED_ONEJUDGE_VERSION"
readonly ONEJUDGE_INSTALL_SCRIPT="https://raw.githubusercontent.com/nickderobertis/onejudge/$ONEJUDGE_RELEASE/install.sh"
readonly LOCAL_ROOT="$HOME/.local"
readonly BIN_DIR="$HOME/.local/bin"
readonly CARGO_BIN="$HOME/.cargo/bin"
readonly NODE_BIN="$HOME/.local/node/bin"   # npm global prefix (codex lands here)
export PATH="$BIN_DIR:$CARGO_BIN:$NODE_BIN:$PATH"

log() { printf 'session-setup: %s\n' "$*" >&2; }

if [[ ! $ADOPTED_ONEJUDGE_VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  log "invalid adopted onejudge version in $ONEJUDGE_VERSION_FILE: '$ADOPTED_ONEJUDGE_VERSION'"
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
  log "installing onejudge $ADOPTED_ONEJUDGE_VERSION (current: $current)"
  # Prefer the prebuilt archive; fall back to building from source (e.g. aarch64
  # Linux, which has no prebuilt onejudge archive).
  if curl -fsSL "$ONEJUDGE_INSTALL_SCRIPT" \
    | ONEJUDGE_INSTALL_DIR="$BIN_DIR" ONEJUDGE_VERSION="$ONEJUDGE_RELEASE" bash >&2; then
    hash -r
    if verify_onejudge; then
      return 0
    fi
    log "the pinned release installer did not produce the required binary; trying crates.io"
  else
    log "the pinned release archive is unavailable for this platform; trying crates.io"
  fi
  if command -v cargo >/dev/null 2>&1; then
    log "building onejudge $ADOPTED_ONEJUDGE_VERSION from crates.io (this can take a few minutes)"
    if cargo install onejudge --version "$ADOPTED_ONEJUDGE_VERSION" --features cli --locked \
      --force --root "$LOCAL_ROOT" >&2; then
      hash -r
      if verify_onejudge; then
        return 0
      fi
    else
      log "onejudge $ADOPTED_ONEJUDGE_VERSION source build failed"
    fi
  else
    log "cannot build onejudge $ADOPTED_ONEJUDGE_VERSION: cargo is not installed"
  fi
  log "required onejudge $ADOPTED_ONEJUDGE_VERSION is unavailable after pinned release and crates.io install attempts"
  return 1
}

verify_onejudge() {
  local binary actual expected
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

ensure_oneharness() {
  # The manylinux `oneharness-cli` wheel both runs on the host glibc and carries
  # `init` (the prebuilt release binary does not on both counts). uv is a
  # clean-clone prerequisite; if absent, leave any existing oneharness in place.
  if ! command -v uv >/dev/null 2>&1; then
    log "uv not found; cannot install oneharness (offline gate unaffected; live path needs it)"
    return 0
  fi
  log "ensuring oneharness (init-capable) via uv tool"
  uv tool install --upgrade 'oneharness-cli>=0.3.20' >&2 \
    || log "oneharness-cli install failed (offline gate unaffected; live path needs it)"
  # Remove any stale cargo-installed oneharness. The 0.2.x crates.io build lags the
  # init-capable wheel and its `run` lacks `--mode`, which onejudge's provider
  # needs; if it shadows the wheel on PATH, live dispatch dies with a confusing
  # "provider error ... Broken pipe". Keep only the wheel (on ~/.local/bin).
  if [ -x "$CARGO_BIN/oneharness" ] && [ -x "$BIN_DIR/oneharness" ]; then
    log "removing stale cargo oneharness ($("$CARGO_BIN/oneharness" --version 2>/dev/null || echo unknown)); the wheel supersedes it"
    rm -f "$CARGO_BIN/oneharness"
  fi
  if command -v oneharness >/dev/null 2>&1; then
    log "oneharness ready ($(oneharness --version 2>/dev/null || echo unknown))"
  fi
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

onejudge_failed=0
install_onejudge || onejudge_failed=1
ensure_oneharness
ensure_codex
ensure_codex_gate
persist_session_env

# Install the llmlint LLM-judge tier (llmlint + its bundled oneharness).
bash "$SCRIPT_DIR/setup-llmlint.sh" || log "setup-llmlint failed (continuing)"

if verify_onejudge; then
  log "ready (onejudge: $(onejudge --version))"
else
  log "onejudge $ADOPTED_ONEJUDGE_VERSION is required — 'just check' will fail until setup succeeds"
  onejudge_failed=1
fi
exit "$onejudge_failed"
