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
#   3. The four published tools this repository is a configuration layer over —
#      oneagentgraph, onevcs, onepipeline, and onepipeline-ui — arrive from PyPI
#      through that same `uv sync` at the versions adopted in `config/`, and are
#      verified as both a distribution and a CLI (see PUBLISHED_TOOL_SPECS below).
#   4. `codex` (the fallback PRIMARY harness) is installed via npm. Auth is a
#      one-time manual `codex login`. See docs/onejudge-integration.md.
#   5. `bun` is installed via npm and verified so oneharness's `sdk-check` gate
#      can run in dispatched worktrees.
#   6. Hands off to `setup-llmlint.sh` to install the llmlint LLM-judge tier.
#   7. The standalone onetaskgraph CLI is installed from the checksum-verified release
#      archive at the version in `config/onetaskgraph.version` (its wheel is not the
#      release path this host relies on).
#
# Before any of that it runs `scripts/hold-run-lease.sh`, which holds this
# dispatch's run-root occupancy lease against a sibling `onevcs session open`
# reclaiming the directory the dispatch is working in. It is first because the
# exposure starts the moment the worktree exists, and provisioning is minutes long.
# See docs/run-root-reclamation.md.
#
# `set -e` is omitted so optional tool failures do not prevent the remaining
# setup steps. Missing or unusable onejudge, oneharness, published-tool, or bun
# binaries are different: the script finishes the other setup work, then exits
# non-zero because dispatch and the complete local gate require them.
# llmlint: ignore-file[robust_shell, tool_output_is_signal, boundary_inputs_validated] deliberate for a session-startup installer: `set -e` is omitted so optional tool failures don't abort later setup; progress is logged to stderr; the required onejudge, oneharness, and bun dependencies are verified explicitly and control the final exit status. CLAUDE_ENV_FILE is a path Claude Code itself provides for the session (a trusted platform input, not external/untrusted data); persist_session_env reads and appends to it exactly as the harness intends, so there is no untrusted boundary to validate.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly REPO_ROOT
readonly ONEJUDGE_VERSION_FILE="$REPO_ROOT/config/onejudge.version"
readonly ONEHARNESS_VERSION_FILE="$REPO_ROOT/config/oneharness.version"
readonly ONETASKGRAPH_VERSION_FILE="$REPO_ROOT/config/onetaskgraph.version"
# The published tools this repository is a configuration layer over, as
# `<CLI binary>|<PyPI distribution>|<config version file>`. Each publishes a
# binary wheel whose console script reports `<binary> <version>`, so one table
# drives reading, validating, and verifying all four. `onepipeline-ui` is the
# tool's name and `onepipeline-api-cli` the distribution PyPI carries it under;
# its npm counterpart is out of scope here because this repo provisions its
# Python-side tooling from PyPI.
readonly PUBLISHED_TOOL_SPECS=(
  "oneagentgraph|oneagentgraph-cli|oneagentgraph.version"
  "onevcs|onevcs-cli|onevcs.version"
  "onepipeline|onepipeline-cli|onepipeline.version"
  "onepipeline-api|onepipeline-api-cli|onepipeline-ui.version"
)
declare -A PUBLISHED_TOOL_VERSIONS=()
ADOPTED_ONEJUDGE_VERSION="$(tr -d '[:space:]' <"$ONEJUDGE_VERSION_FILE")"
readonly ADOPTED_ONEJUDGE_VERSION
ADOPTED_ONEHARNESS_VERSION="$(tr -d '[:space:]' <"$ONEHARNESS_VERSION_FILE")"
readonly ADOPTED_ONEHARNESS_VERSION
ADOPTED_ONETASKGRAPH_VERSION="$(tr -d '[:space:]' <"$ONETASKGRAPH_VERSION_FILE")"
readonly ADOPTED_ONETASKGRAPH_VERSION
readonly BIN_DIR="$HOME/.local/bin"
readonly CARGO_BIN="$HOME/.cargo/bin"
readonly NODE_BIN="$HOME/.local/node/bin"   # npm global prefix (codex lands here)
readonly PROJECT_VENV_BIN="$REPO_ROOT/.venv/bin"
export PATH="$PROJECT_VENV_BIN:$BIN_DIR:$CARGO_BIN:$NODE_BIN:$PATH"
# shellcheck source=scripts/claude-alt-config-dir.sh
source "$SCRIPT_DIR/claude-alt-config-dir.sh"
# Session setup resolves this fixed sibling path through SCRIPT_DIR at runtime.
# shellcheck source=scripts/claude-workspace-trust.sh
source "$SCRIPT_DIR/claude-workspace-trust.sh"
# The shared resolver is also used by fail-fast wrappers and enables `set -e`;
# session setup deliberately continues after optional setup failures.
set +e
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
if [[ ! $ADOPTED_ONETASKGRAPH_VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  log "invalid adopted onetaskgraph version in $ONETASKGRAPH_VERSION_FILE: '$ADOPTED_ONETASKGRAPH_VERSION'"
  if [[ ${BASH_SOURCE[0]} != "$0" ]]; then
    return 1
  fi
  exit 1
fi
for published_tool_spec in "${PUBLISHED_TOOL_SPECS[@]}"; do
  IFS='|' read -r published_tool_binary _ published_tool_version_file <<<"$published_tool_spec"
  published_tool_version_path="$REPO_ROOT/config/$published_tool_version_file"
  # An unreadable file answers empty, which the pattern below rejects by name
  # rather than as raw redirect noise.
  published_tool_version="$(tr -d '[:space:]' <"$published_tool_version_path" 2>/dev/null)"
  if [[ ! $published_tool_version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    log "invalid adopted $published_tool_binary version in $published_tool_version_path: '$published_tool_version'"
    if [[ ${BASH_SOURCE[0]} != "$0" ]]; then
      return 1
    fi
    exit 1
  fi
  PUBLISHED_TOOL_VERSIONS["$published_tool_binary"]="$published_tool_version"
done

# CI never needs this provisioning; keep it a no-op there.
if [ -n "${CI:-}" ]; then
  log "CI detected; skipping toolchain provisioning"
  exit 0
fi

install_project_dependencies() {
  if verify_onejudge >/dev/null 2>&1 && verify_oneharness >/dev/null 2>&1 \
    && verify_published_tools >/dev/null 2>&1; then
    return 0
  fi
  if ! command -v uv >/dev/null 2>&1; then
    log "cannot install required project dependencies: uv is not installed"
    return 1
  fi
  log "syncing pinned project dependencies into $REPO_ROOT/.venv"
  if uv sync --project "$REPO_ROOT" >&2; then
    hash -r
    if verify_onejudge && verify_oneharness && verify_published_tools; then
      return 0
    fi
  else
    log "project dependency sync failed"
  fi
  log "required pinned onejudge, oneharness, and published-tool dependencies are unavailable after uv sync"
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
  # codex is the worker fallback plus the judge and llmlint primary. It runs as
  # its own process, so its tools execute directly (nested claude-code can defer
  # them; see docs/onejudge-integration.md "Harnesses and the live path").
  # Authentication is a one-time manual step (`codex login`).
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

verify_published_tool() {
  # One published tool, checked the same two ways `oneharness` is: the
  # worktree-local CLI reports the adopted release, and the distribution the
  # wheel installed carries that same version.
  local binary="$1" distribution="$2" expected_version="$3" python_bin="$4"
  local path actual
  path="$PROJECT_VENV_BIN/$binary"
  if [ ! -x "$path" ]; then
    log "$binary verification failed: expected '$binary $expected_version' at $path"
    return 1
  fi
  if ! actual="$("$path" --version 2>&1)"; then
    log "$binary verification failed: $path could not report its version"
    return 1
  fi
  if [[ $actual != "$binary $expected_version" ]]; then
    log "$binary verification failed: expected '$binary $expected_version', got '$actual' from $path"
    return 1
  fi
  if ! actual="$("$python_bin" -c \
    "import importlib.metadata as metadata; print(metadata.version('$distribution'))" 2>&1)"; then
    log "$binary distribution verification failed: $distribution metadata is unavailable from $python_bin"
    return 1
  fi
  if [[ $actual != "$expected_version" ]]; then
    log "$binary distribution verification failed: expected '$expected_version', got '$actual'"
    return 1
  fi
  return 0
}

verify_published_tools() {
  # Every tool is reported on, not just the first failure: an operator fixing a
  # stale environment should see the whole list in one pass.
  local spec binary distribution python_bin status=0
  python_bin="$PROJECT_VENV_BIN/python"
  for spec in "${PUBLISHED_TOOL_SPECS[@]}"; do
    IFS='|' read -r binary distribution _ <<<"$spec"
    verify_published_tool \
      "$binary" "$distribution" "${PUBLISHED_TOOL_VERSIONS[$binary]}" "$python_bin" || status=1
  done
  return "$status"
}

onetaskgraph_target() {
  local machine
  machine="$(uname -m)"
  case "$(uname -s):$machine" in
    Linux:x86_64) printf '%s\n' x86_64-unknown-linux-gnu ;;
    Linux:aarch64 | Linux:arm64) printf '%s\n' aarch64-unknown-linux-gnu ;;
    Darwin:x86_64) printf '%s\n' x86_64-apple-darwin ;;
    Darwin:arm64 | Darwin:aarch64) printf '%s\n' aarch64-apple-darwin ;;
    *) log "onetaskgraph has no release archive for $(uname -s) $machine"; return 1 ;;
  esac
}

verify_onetaskgraph() {
  local binary="$BIN_DIR/onetaskgraph" actual expected
  expected="onetaskgraph $ADOPTED_ONETASKGRAPH_VERSION"
  [ -x "$binary" ] || return 1
  actual="$("$binary" --version 2>/dev/null)" || return 1
  [ "$actual" = "$expected" ]
}

install_onetaskgraph() {
  if verify_onetaskgraph; then
    mkdir -p "$REPO_ROOT/.plans/tasks" "$REPO_ROOT/.plans/projects"
    return $?
  fi
  local target archive base temporary checksum
  target="$(onetaskgraph_target)" || return 1
  archive="onetaskgraph-v${ADOPTED_ONETASKGRAPH_VERSION}-${target}.tar.gz"
  base="https://github.com/nickderobertis/onetaskgraph/releases/download/v${ADOPTED_ONETASKGRAPH_VERSION}"
  temporary="$(mktemp -d)" || return 1
  if ! curl --fail --location --silent --show-error "$base/$archive" -o "$temporary/$archive" \
    || ! curl --fail --location --silent --show-error "$base/$archive.sha256" -o "$temporary/$archive.sha256"; then
    log "onetaskgraph $ADOPTED_ONETASKGRAPH_VERSION release archive download failed"
    rm -rf "$temporary"
    return 1
  fi
  checksum="$(awk '{print $1}' "$temporary/$archive.sha256")"
  local actual_checksum
  if command -v sha256sum >/dev/null 2>&1; then
    actual_checksum="$(sha256sum "$temporary/$archive" | awk '{print $1}')"
  else
    actual_checksum="$(shasum -a 256 "$temporary/$archive" | awk '{print $1}')"
  fi
  if [[ ! $checksum =~ ^[[:xdigit:]]{64}$ ]] || [ "$actual_checksum" != "$checksum" ]; then
    log "onetaskgraph $ADOPTED_ONETASKGRAPH_VERSION release archive checksum verification failed"
    rm -rf "$temporary"
    return 1
  fi
  if ! tar -xzf "$temporary/$archive" -C "$temporary" \
    || [ ! -f "$temporary/onetaskgraph" ] \
    || ! mkdir -p "$BIN_DIR" \
    || ! install -m 0755 "$temporary/onetaskgraph" "$BIN_DIR/onetaskgraph"; then
    log "onetaskgraph $ADOPTED_ONETASKGRAPH_VERSION release archive installation failed"
    rm -rf "$temporary"
    return 1
  fi
  rm -rf "$temporary"
  hash -r
  verify_onetaskgraph && mkdir -p "$REPO_ROOT/.plans/tasks" "$REPO_ROOT/.plans/projects"
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

append_session_env() {
  # `2>/dev/null` precedes the append deliberately: bash performs redirections left
  # to right, so a failing `>>` on a later one still writes its raw "No such file or
  # directory" to the stderr in effect at that point. Suppressing first is what turns
  # a failed append into this function's own diagnostic instead of shell noise.
  if ! printf '%s\n' "$1" 2>/dev/null >>"$CLAUDE_ENV_FILE"; then
    log "cannot persist the session environment: $CLAUDE_ENV_FILE is not writable"
    return 1
  fi
}

persist_session_env() {
  [ -n "${CLAUDE_ENV_FILE:-}" ] || return 0
  local env_dir env_export has_path=0 has_llmlint_bin=0
  env_dir="$(dirname "$CLAUDE_ENV_FILE")"
  # On the FIRST session inside a freshly created config directory — a new machine,
  # or a newly authenticated subscription — the parent of the file Claude Code names
  # does not exist yet. Create it rather than skipping: it is the directory of the
  # harness's own path, and skipping would cost that session its toolchain PATH for
  # good. Best effort either way, and never the script's exit status; a lost export
  # costs only PATH, since scripts/llmlint-runtime-env.sh re-derives
  # LLMLINT_ONEHARNESS_BIN for both ends of the llmlint tier.
  if ! mkdir -p "$env_dir"; then
    log "cannot persist the session environment: $env_dir could not be created"
    return 0
  fi
  # Being the first writer is the normal case, and reading an absent or unreadable
  # file would leak the same raw redirect error the appends used to.
  if [ -f "$CLAUDE_ENV_FILE" ] && [ -r "$CLAUDE_ENV_FILE" ]; then
    while IFS= read -r env_export; do
      case "$env_export" in
        "export PATH="*"$NODE_BIN"*) has_path=1 ;;
        "export LLMLINT_ONEHARNESS_BIN="*) has_llmlint_bin=1 ;;
      esac
    done <"$CLAUDE_ENV_FILE"
  fi
  if [ "$has_path" -eq 0 ]; then
    printf -v env_export 'export PATH=%q' "$PATH"
    append_session_env "$env_export" || return 0
  fi
  if [ "$has_llmlint_bin" -eq 0 ]; then
    printf -v env_export 'export LLMLINT_ONEHARNESS_BIN=%q' "$LLMLINT_ONEHARNESS_BIN"
    append_session_env "$env_export" || return 0
  fi
}

if [[ ${BASH_SOURCE[0]} != "$0" ]]; then
  return 0
fi

# First, and never fatal: an unheld lease costs a dispatch its working directory,
# and a failure to take one must not cost it its toolchain as well.
# llmlint: ignore[changed_behavior_has_e2e] The `||` guards a script that handles and
# reports every failure of its own and always exits 0, so reaching this branch means
# bash could not read `scripts/hold-run-lease.sh` at all — a worktree missing a
# tracked file, which is not a state a journey builds without also breaking the
# session setup it is driving. What the call itself does is driven end to end by
# `tests/e2e/test_run_root_lease_e2e.py`.
bash "$SCRIPT_DIR/hold-run-lease.sh" "$REPO_ROOT" || log "run-root lease unavailable; continuing session setup"

toolchain_failed=0
install_project_dependencies || toolchain_failed=1
install_onetaskgraph || toolchain_failed=1
if [ -f "$REPO_ROOT/justfile" ] && command -v just >/dev/null 2>&1; then
  just --justfile "$REPO_ROOT/justfile" --working-directory "$REPO_ROOT" sweep >&2 \
    || log "workspace sweep failed; continuing session setup"
else
  log "workspace sweep unavailable; continuing session setup"
fi
install_bun || toolchain_failed=1
ensure_codex
ensure_codex_gate
# All three claude-code identities are dispatch identities, so all three need this
# checkout marked trusted; `claude_trust_config_paths` names each one and reports
# whichever it could not, and `mark_claude_config_trust` tolerates a config that is
# not there yet, which is the state of one nobody has logged into.
claude_config_paths=()
mapfile -t claude_config_paths < <(claude_trust_config_paths session-setup)
managed_checkout_root="$(git -C "$REPO_ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
if [ -n "$managed_checkout_root" ]; then
  managed_checkout_root="$(dirname "$managed_checkout_root")"
else
  managed_checkout_root="$REPO_ROOT"
fi
for claude_config_path in ${claude_config_paths[@]+"${claude_config_paths[@]}"}; do
  mark_claude_config_trust "$claude_config_path" "$managed_checkout_root" "$REPO_ROOT" \
    || log "Claude workspace trust setup failed for $claude_config_path; continuing"
done
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
if verify_published_tools; then
  for published_tool_spec in "${PUBLISHED_TOOL_SPECS[@]}"; do
    IFS='|' read -r published_tool_binary _ _ <<<"$published_tool_spec"
    log "ready ($published_tool_binary: ${PUBLISHED_TOOL_VERSIONS[$published_tool_binary]} at $PROJECT_VENV_BIN/$published_tool_binary)"
  done
else
  log "the adopted oneagentgraph, onevcs, onepipeline, and onepipeline-ui releases are required — 'just check' will fail until setup succeeds"
  toolchain_failed=1
fi
if verify_onetaskgraph; then
  log "ready (onetaskgraph: $ADOPTED_ONETASKGRAPH_VERSION at $BIN_DIR/onetaskgraph)"
else
  log "onetaskgraph $ADOPTED_ONETASKGRAPH_VERSION is required — 'just check' will fail until setup succeeds"
  toolchain_failed=1
fi
if ! verify_bun; then
  log "bun is required — the oneharness sdk-check gate will fail until setup succeeds"
  toolchain_failed=1
fi
exit "$toolchain_failed"
