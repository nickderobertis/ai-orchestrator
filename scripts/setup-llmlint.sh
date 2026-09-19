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
#
# Ceiling: the highest `llmlint-cli` at or above the floor whose declared
# `oneharness-cli` requirement admits the release `config/oneharness.version` names.
# That pin is the `oneharness` every judged run reaches (`scripts/llmlint-runtime-env.sh`
# puts `.venv/bin` first), and a llmlint requiring a newer one refuses every run with
# `oneharness <pin> is too old` — which an uncapped install did here on the next session
# start. So the pin is handed to uv's resolver as a constraint and no ceiling version is
# written; `tests/test_llmlint_release_pin.py` holds the installed release to the pin.
# When nothing satisfies the rule, or the pin cannot be read, the installed llmlint is
# left as it is.
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
# llmlint: ignore[changed_behavior_has_e2e] declarative dependency floor only; the installer's selection is driven by tests/e2e/test_setup_llmlint_e2e.py.
readonly LLMLINT_MIN="0.4.1"
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(dirname -- "$script_dir")
readonly repo_root
# The one source of the ceiling: the `oneharness` release this checkout adopts.
readonly ONEHARNESS_PIN_FILE="$repo_root/config/oneharness.version"
# Where `uv tool` links the executables it installs; its own override is honoured so
# a caller pointing the install at a throwaway tool dir finds what it installed.
readonly BIN_DIR="${UV_TOOL_BIN_DIR:-$HOME/.local/bin}"

log() { printf 'setup-llmlint: %s\n' "$*" >&2; }

# The pinned `oneharness-cli` release, or a logged failure naming the file.
read_oneharness_pin() {
  local pin
  if [ ! -r "$ONEHARNESS_PIN_FILE" ]; then
    log "cannot read the oneharness pin at $ONEHARNESS_PIN_FILE; leaving the installed llmlint as it is"
    return 1
  fi
  pin=$(tr -d '[:space:]' < "$ONEHARNESS_PIN_FILE")
  case "$pin" in
    '' | *[!0-9.]*)
      log "the oneharness pin at $ONEHARNESS_PIN_FILE reads '$pin', which is not a release; leaving the installed llmlint as it is"
      return 1
      ;;
  esac
  printf '%s\n' "$pin"
}

ensure_toolchain() {
  local pin
  if ! command -v uv >/dev/null 2>&1; then
    log "uv not found; cannot install llmlint (install uv: https://docs.astral.sh/uv/)"
    return 0
  fi
  pin=$(read_oneharness_pin) || return 0
  log "installing the highest llmlint-cli >= $LLMLINT_MIN that runs under oneharness-cli $pin (the release config/oneharness.version pins) via uv tool"
  uv tool install --upgrade "llmlint-cli>=$LLMLINT_MIN" --with "oneharness-cli==$pin" >&2 \
    || log "no llmlint-cli >= $LLMLINT_MIN resolved under oneharness-cli $pin, or the install failed (uv's report is above); leaving the installed llmlint as it is"
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
