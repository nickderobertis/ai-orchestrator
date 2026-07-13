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
#   2. `oneharness` presence is reported — the LIVE dispatch path needs it (the
#      offline gate does not). `onejudge init` additionally needs oneharness
#      0.3.20+; see docs/onejudge-integration.md.
#
# `set -e` is omitted on purpose: a flaky install must never abort session
# startup. The script owns its exit codes and always exits 0. Quiet on success.
set -uo pipefail

readonly BIN_DIR="$HOME/.local/bin"
readonly CARGO_BIN="$HOME/.cargo/bin"
export PATH="$BIN_DIR:$CARGO_BIN:$PATH"

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

report_oneharness() {
  if command -v oneharness >/dev/null 2>&1; then
    log "oneharness present ($(oneharness --version 2>/dev/null || echo unknown)) — live dispatch available"
  else
    log "oneharness not found — the offline gate is unaffected; the LIVE dispatch path needs it"
  fi
}

persist_session_env() {
  [ -n "${CLAUDE_ENV_FILE:-}" ] || return 0
  case ":${PATH}:" in
    *":${CARGO_BIN}:"*) ;;
    *) printf 'export PATH=%q\n' "${CARGO_BIN}:${PATH}" >>"$CLAUDE_ENV_FILE" ;;
  esac
}

install_onejudge
report_oneharness
persist_session_env

if command -v onejudge >/dev/null 2>&1; then
  log "ready (onejudge: $(onejudge --version 2>/dev/null || echo unknown))"
else
  log "onejudge is not installed — 'just check' will fail until it is (see above)"
fi
exit 0
