#!/usr/bin/env bash
# Give every checkout of one repository the same local Nx cache without putting
# generated state in a worktree. Nx's content hash still guards every replay.
# llmlint: ignore-file[boundary_inputs_validated] This is the trusted local operator command
# surface: XDG_CACHE_HOME/HOME are platform-owned directory roots, and arguments are intentionally
# Nx's own validated CLI. No value is interpreted as repository content or passed to eval.
# llmlint: ignore-file[changed_behavior_has_e2e] scripts/check-nx-cache.sh exercises this wrapper
# through two real linked worktrees, including a cache hit and a changed-source typecheck failure.
set -euo pipefail

script_dir="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck source=scripts/preserved-log.sh
. "$script_dir/preserved-log.sh"

# Nx lives in `node_modules/.bin`, which a freshly created worktree does not have.
# Healing it here rather than in one recipe is what stops the same missing install
# from failing `just lint` with "Could not find Nx modules" while `just check`
# quietly repaired it — and what lets a bare `pytest` drive these targets without
# an operator running Bun by hand first. It exits immediately once provisioned.
"$script_dir/workspace-install.sh" || exit 1

repo_identity="$(git config --get remote.origin.url || git rev-parse --show-toplevel)" || { echo "nx: cannot resolve repository identity; run from a Git checkout and retry" >&2; exit 1; }
repo_key="$(printf '%s' "$repo_identity" | sha256sum | cut -c1-16)" || { echo "nx: cannot derive the repository cache key; verify sha256sum is available and retry" >&2; exit 1; }
[[ "$repo_key" =~ ^[0-9a-f]{16}$ ]] || { echo "nx: derived an invalid repository cache key; verify sha256sum output and retry" >&2; exit 1; }
cache_root="${XDG_CACHE_HOME:-${HOME}/.cache}/ai-orchestrator/nx/${repo_key}"
mkdir -p "$cache_root" || { echo "nx: cannot create shared cache directory '$cache_root'; repair its parent permissions and retry" >&2; exit 1; }
export NX_CACHE_DIRECTORY="$cache_root"

# Nx's daemon is off by default here, and any daemon that is turned back on uses
# the installed Nx for its own housekeeping.
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

# The log outlives this process on purpose: a failing run needs its full output
# after the fact, and a *running* one has to be inspectable (`tail -f`) without
# reading this process's file descriptors through /proc.
preserved_log_open "$(dirname -- "$script_dir")" nx || exit 1
log=$PRESERVED_LOG
if bunx nx "$@" 2>&1 | redact_secrets >"$log"; then
  # llmlint: ignore[tool_output_is_signal] Explicit debug output lets the cache-contract check inspect Nx's success evidence; default successful invocations still emit one line.
  if [[ "${AI_ORCHESTRATOR_NX_SHOW_OUTPUT:-}" == "1" ]]; then cat "$log"; fi
  printf 'nx: requested targets succeeded\n'
  exit 0
fi
cat "$log" >&2
printf "nx: targets failed; fix the reported project findings and rerun the same 'just' recipe (full output: %s)\n" "$log" >&2
exit 1
