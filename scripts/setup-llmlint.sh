#!/usr/bin/env bash
# Idempotent setup for the `llmlint` LLM-judge tier (llmlint + oneharness).
#
# Installs the `llmlint` binary from PyPI via `uv tool`. `llmlint-cli` wraps the
# prebuilt binary and depends on `oneharness-cli`, so one dependency resolution
# fetches both wheels (no Rust toolchain, no github.com reachability). llmlint
# finds `oneharness` beside its own binary in the tool venv, so this one install
# is a complete llmlint setup. Called by `scripts/session-setup.sh` and exposed as
# `just setup-llmlint`; safe to run by hand. Always exits 0 so a flaky install
# never aborts session startup.
#
# Harness selection: the committed `oneharness.toml` is in fallback mode (codex +
# gpt-5.5 primary, claude-code + opus-4.8 secondary), so llmlint runs the primary
# where Codex is authenticated and falls through to claude-code in a Claude Code
# session where codex is absent — no ONEHARNESS_* override needed.
# llmlint: ignore-file[robust_shell, tool_output_is_signal, boundary_inputs_validated] deliberate for a session-startup installer (see header): `set -e` is omitted so a flaky install can't abort the hook — the script owns its exit codes and always exits 0; success logs progress while failures log-and-continue rather than block startup; and the toolchain is installed from PyPI (`uv tool install llmlint-cli`) whose wheels ship with Trusted Publishing + PEP 740 attestations, so no unvalidated external input is executed.
set -uo pipefail

# Floor: llmlint >= 0.4.1, which keys the on-disk plugin cache by the version it
# fetched and revalidates a stale entry instead of keeping the first version a host
# ever saw. Below it a long-lived host silently pins every plugin at whatever it first
# resolved, so a rule a plugin has since added is reported as an unknown rule and a
# suppression naming it fails — while a fresh checkout's CI passes over the same tree.
# That cost two dispatches here. `llmlint plugins list` is the same release's way of
# seeing it: per entry, its pin, the version it resolved, and when the origin last
# confirmed it.
#
# It subsumes the 0.3.29 floor, whose judge prompt requires every distinct violation of
# a failing rule to be reported, and the 0.3.25 floor's role=llmlint labelling that keeps
# judge telemetry separate from agent telemetry. It also crosses 0.4.0, which is
# breaking: positional `FILES` are intersected with the configured file globs rather
# than replacing them. Nothing here passes positional files — `just lint-llm-diff` scopes
# by `--diff` — so this host is unaffected by that half.
# llmlint: ignore[changed_behavior_has_e2e] declarative dependency floor only; installer behavior is unchanged.
readonly LLMLINT_MIN="0.4.1"
readonly BIN_DIR="$HOME/.local/bin"

log() { printf 'setup-llmlint: %s\n' "$*" >&2; }

ensure_toolchain() {
  if ! command -v uv >/dev/null 2>&1; then
    log "uv not found; cannot install llmlint (install uv: https://docs.astral.sh/uv/)"
    return 0
  fi
  log "installing llmlint-cli >= $LLMLINT_MIN via uv tool"
  uv tool install --upgrade "llmlint-cli>=$LLMLINT_MIN" >&2 \
    || log "llmlint-cli install failed (continuing)"
}

export PATH="${BIN_DIR}:${PATH}"
ensure_toolchain
if command -v llmlint >/dev/null 2>&1; then
  log "ready (llmlint: $(llmlint --version 2>/dev/null || echo unknown))"
  llmlint doctor >&2 2>&1 || log "llmlint doctor reported an issue (see above)"
else
  log "llmlint not installed"
fi
exit 0
