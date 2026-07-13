#!/usr/bin/env bash
# Idempotent dev-toolchain setup for a session. Wired into the Claude Code
# SessionStart hook (.claude/settings.json) and also runnable by hand
# (`just session-setup` / `just bootstrap`).
#
# What it ensures:
#   1. `onejudge` is installed — the offline gate drives it as a subprocess. Uses
#      the prebuilt install.sh where a release archive exists (x86_64 Linux,
#      macOS); otherwise builds from source (`cargo install`), which is the path
#      on Linux aarch64.
#   2. `oneharness` (0.3.20+, init-capable) is installed via the PyPI
#      `oneharness-cli` manylinux wheel — the LIVE dispatch path and `onejudge
#      init` need it (the offline gate does not). The wheel is used because it
#      runs on older glibc and carries `init`, unlike the prebuilt release binary.
#      See docs/onejudge-integration.md.
#   3. `codex` (the fallback PRIMARY harness) is installed via npm. Auth is a
#      one-time manual `codex login`. See docs/onejudge-integration.md.
#   4. Hands off to `setup-llmlint.sh` to install the llmlint LLM-judge tier.
#
# `set -e` is omitted on purpose: a flaky install must never abort session
# startup. The script owns its exit codes and always exits 0.
# llmlint: ignore-file[robust_shell, tool_output_is_signal] deliberate for a session-startup installer: `set -e` is omitted so a flaky install can't abort the hook (the script owns its exit codes and always exits 0), and progress is logged to stderr while failures log-and-continue rather than block startup.
set -uo pipefail

readonly BIN_DIR="$HOME/.local/bin"
readonly CARGO_BIN="$HOME/.cargo/bin"
readonly NODE_BIN="$HOME/.local/node/bin"   # npm global prefix (codex lands here)
export PATH="$BIN_DIR:$CARGO_BIN:$NODE_BIN:$PATH"

log() { printf 'session-setup: %s\n' "$*" >&2; }

# CI never needs this provisioning; keep it a no-op there.
if [ -n "${CI:-}" ]; then
  log "CI detected; skipping toolchain provisioning"
  exit 0
fi

install_onejudge() {
  if command -v onejudge >/dev/null 2>&1; then
    return 0
  fi
  log "installing onejudge"
  # Prefer the prebuilt archive; fall back to building from source (e.g. aarch64
  # Linux, which has no prebuilt onejudge archive).
  if curl -fsSL https://raw.githubusercontent.com/nickderobertis/onejudge/main/install.sh | bash >&2 \
    && command -v onejudge >/dev/null 2>&1; then
    return 0
  fi
  if command -v cargo >/dev/null 2>&1; then
    log "no prebuilt archive for this platform; building onejudge from source (this can take a few minutes)"
    cargo install onejudge --features cli --locked >&2 || log "onejudge build failed (continuing)"
  else
    log "cannot install onejudge: no prebuilt archive and no cargo — install a Rust toolchain, then rerun"
  fi
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
  if command -v oneharness >/dev/null 2>&1; then
    log "oneharness ready ($(oneharness --version 2>/dev/null || echo unknown))"
  fi
}

persist_session_env() {
  [ -n "${CLAUDE_ENV_FILE:-}" ] || return 0
  case ":${PATH}:" in
    *":${NODE_BIN}:"*) ;;
    *) printf 'export PATH=%q\n' "${PATH}" >>"$CLAUDE_ENV_FILE" ;;
  esac
}

install_onejudge
ensure_oneharness
ensure_codex
persist_session_env

# Install the llmlint LLM-judge tier (llmlint + its bundled oneharness).
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$script_dir/setup-llmlint.sh" || log "setup-llmlint failed (continuing)"

if command -v onejudge >/dev/null 2>&1; then
  log "ready (onejudge: $(onejudge --version 2>/dev/null || echo unknown))"
else
  log "onejudge is not installed — 'just check' will fail until it is (see above)"
fi
exit 0
