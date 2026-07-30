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

repo_identity="$(git config --get remote.origin.url || git rev-parse --show-toplevel)" || { echo "nx: cannot resolve repository identity; run from a Git checkout and retry" >&2; exit 1; }
repo_key="$(printf '%s' "$repo_identity" | sha256sum | cut -c1-16)" || { echo "nx: cannot derive the repository cache key; verify sha256sum is available and retry" >&2; exit 1; }
[[ "$repo_key" =~ ^[0-9a-f]{16}$ ]] || { echo "nx: derived an invalid repository cache key; verify sha256sum output and retry" >&2; exit 1; }
cache_root="${XDG_CACHE_HOME:-${HOME}/.cache}/ai-orchestrator/nx/${repo_key}"
mkdir -p "$cache_root" || { echo "nx: cannot create shared cache directory '$cache_root'; repair its parent permissions and retry" >&2; exit 1; }
export NX_CACHE_DIRECTORY="$cache_root"

# The log outlives this process on purpose: a failing run needs its full output
# after the fact, and a *running* one has to be inspectable (`tail -f`) without
# reading this process's file descriptors through /proc.
log="$(preserved_log_open "$(dirname -- "$script_dir")" nx)" || exit 1
if bunx nx "$@" 2>&1 | redact_secrets >"$log"; then
  # llmlint: ignore[tool_output_is_signal] Explicit debug output lets the cache-contract check inspect Nx's success evidence; default successful invocations still emit one line.
  if [[ "${AI_ORCHESTRATOR_NX_SHOW_OUTPUT:-}" == "1" ]]; then cat "$log"; fi
  printf 'nx: requested targets succeeded\n'
  exit 0
fi
cat "$log" >&2
printf "nx: targets failed; fix the reported project findings and rerun the same 'just' recipe (full output: %s)\n" "$log" >&2
exit 1
