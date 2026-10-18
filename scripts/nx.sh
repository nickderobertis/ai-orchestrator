#!/usr/bin/env bash
# Give every checkout of one repository the same local Nx cache without putting
# generated state in a worktree. Nx's content hash still guards every replay.
# llmlint: ignore-file[boundary_inputs_validated] This is the trusted local operator command
# surface: XDG_CACHE_HOME/HOME are platform-owned directory roots, and arguments are intentionally
# Nx's own validated CLI. No value is interpreted as repository content or passed to eval.
# llmlint: ignore-file[changed_behavior_has_e2e] tests/e2e/workspace_copies/test_workspace_contract_e2e.py
# runs this real wrapper with Nx doubled, including a failing run's preserved owner-only log.
set -euo pipefail
# No `uv run` in a target or a test this launches syncs the environment on its way in.
export UV_NO_SYNC=1

script_dir="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck source=scripts/preserved-log.sh
. "$script_dir/preserved-log.sh"

# This wrapper installs nothing: session setup is the one place a checkout is
# provisioned (AGENTS.md, "The checks"). The helper refuses a checkout whose Python
# environment holds no interpreter, naming `just session-setup`.
# shellcheck source=scripts/require-provisioned.sh
. "$script_dir/require-provisioned.sh" || exit 1

repo_identity="$(git config --get remote.origin.url || git rev-parse --show-toplevel)" || { echo "nx: cannot resolve repository identity; run from a Git checkout and retry" >&2; exit 1; }
repo_key="$(printf '%s' "$repo_identity" | sha256sum | cut -c1-16)" || { echo "nx: cannot derive the repository cache key; verify sha256sum is available and retry" >&2; exit 1; }
[[ "$repo_key" =~ ^[0-9a-f]{16}$ ]] || { echo "nx: derived an invalid repository cache key; verify sha256sum output and retry" >&2; exit 1; }
cache_root="${XDG_CACHE_HOME:-${HOME}/.cache}/ai-orchestrator/nx/${repo_key}"
export NX_CACHE_DIRECTORY="$cache_root"

# The *other* Nx cache, keyed the same way so it grows per repository rather than per
# worktree: Nx copies its 22 MB native module into a directory named from the workspace
# root, and every dispatch here works in a new one. A sibling of the computation cache
# rather than that directory itself, because Nx creates `NX_CACHE_DIRECTORY` only when
# it stores a task result — which is what lets a metadata-only invocation leave nothing
# behind — while the native loader creates its own on every invocation. See AGENTS.md,
# "The computation cache those targets use".
export NX_NATIVE_FILE_CACHE_DIRECTORY="${XDG_CACHE_HOME:-${HOME}/.cache}/ai-orchestrator/nx-native/${repo_key}"

# Node's module compile cache, which the Nx CLI turns on and Node otherwise keeps under
# the temporary directory — where every Nx run left one beside the gate's test files. A
# persistent per-repository cache beside the two above instead, keyed the same way.
export NODE_COMPILE_CACHE="${XDG_CACHE_HOME:-${HOME}/.cache}/ai-orchestrator/node-compile/${repo_key}"

# Under the pre-push gate's recorder (`orchestrator/gate_impact.py`), whose temporary
# root is deeper than Nx's plugin-worker sockets leave room for in a Unix socket address,
# those sockets get a short directory of their own, registered with the recorder so it is
# counted with the gate's scratch, and removed when this run ends.
if [[ -n ${ORCHESTRATOR_GATE_SCRATCH:-} && -z ${NX_SOCKET_DIR:-} ]]; then
  nx_socket_dir=$(mktemp -d /tmp/aio-nx-XXXXXX) || { echo "nx: no socket directory could be made under /tmp; check that it is writable and has free space, then retry" >&2; exit 1; }
  trap 'rm -rf -- "$nx_socket_dir" || echo "nx: the socket directory $nx_socket_dir could not be removed; remove it by hand with rm -rf once no Nx run is using it" >&2' EXIT
  "$script_dir/../.venv/bin/python" -m orchestrator.gate_impact register "$nx_socket_dir" || { echo "nx: the socket directory $nx_socket_dir could not be registered with the gate's recorder; its line above says why" >&2; exit 1; }
  export NX_SOCKET_DIR="$nx_socket_dir"
fi

# Nx's daemon is off by default here, and any daemon that is turned back on uses
# the Nx session setup installed for its own housekeeping.
#
# A daemon primes an "are AI agents configured?" cache the instant it starts by
# installing `nx@latest` into a fresh system-temp directory — ~80 MB, logged as
# `[LATEST-NX]: Pulling latest Nx...` — and removes it only on a *graceful*
# shutdown. There is one daemon per workspace root and the e2e suite copies this
# checkout per journey, so one worktree accumulated dozens of resident daemons
# (8.9 GB RSS) and the host accumulated ~110 GB of abandoned installs in a day.
# The daemon buys about 0.1s per invocation on this workspace, which does not pay
# for that; `NX_DAEMON=true` still turns it back on for anyone who wants it.
# `NX_USE_LOCAL` is the independent guard that keeps such a daemon from pulling a
# private copy at all. It also makes `nx migrate` plan from the installed Nx
# rather than the latest release, which is the right trade for a wrapper whose job
# is running this workspace's own targets.
export NX_DAEMON="${NX_DAEMON-false}"
export NX_USE_LOCAL=true

# The Bun-written shim rather than a path inside the package: Nx has moved its
# bin entry between releases, and the shim is the one name that cannot. Session setup's
# locked install provides it; nothing here does.
NX_BIN="node_modules/.bin/nx"
[[ -x "$NX_BIN" ]] || NX_BIN="node_modules/.bin/nx.cmd"
if [[ ! -x "$NX_BIN" ]]; then
  echo "nx: this checkout has no executable node_modules/.bin/nx or node_modules/.bin/nx.cmd, so its workspace is not provisioned; run 'just session-setup' and retry" >&2
  exit 1
fi

# The log outlives this process on purpose: a failing run needs its full output
# after the fact, and a *running* one has to be inspectable (`tail -f`) without
# reading this process's file descriptors through /proc.
preserved_log_open "$(dirname -- "$script_dir")" nx || exit 1
log=$PRESERVED_LOG
if "$NX_BIN" "$@" 2>&1 | redact_secrets >"$log"; then
  # llmlint: ignore[tool_output_is_signal] Explicit debug output lets the cache-contract check inspect Nx's success evidence, and lets `just lint-llm-diff` show the judge report Nx replayed; default successful invocations still emit one line.
  if [[ "${AI_ORCHESTRATOR_NX_SHOW_OUTPUT:-}" == "1" ]]; then cat "$log"; fi
  printf 'nx: requested targets succeeded\n'
  exit 0
fi
cat "$log" >&2
printf "nx: targets failed; fix the reported project findings and rerun the same 'just' recipe (full output: %s)\n" "$log" >&2
exit 1
