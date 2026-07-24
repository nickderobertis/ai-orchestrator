#!/usr/bin/env bash
# Give every checkout of one repository the same local Nx cache without putting
# generated state in a worktree. Nx's content hash still guards every replay.
# llmlint: ignore-file[boundary_inputs_validated] This is the trusted local operator command
# surface: XDG_CACHE_HOME/HOME are platform-owned directory roots, and arguments are intentionally
# Nx's own validated CLI. No value is interpreted as repository content or passed to eval.
# llmlint: ignore-file[changed_behavior_has_e2e] The acceptance-specific real linked-worktree check
# exercises a 100% cross-worktree cache hit and a changed-source typecheck failure; keeping that
# destructive worktree operation out of the ordinary unit suite avoids mutating its checkout.
set -euo pipefail

repo_identity="$(git config --get remote.origin.url || git rev-parse --show-toplevel)"
repo_key="$(printf '%s' "$repo_identity" | sha256sum | cut -c1-16)"
cache_root="${XDG_CACHE_HOME:-${HOME}/.cache}/ai-orchestrator/nx/${repo_key}"
mkdir -p "$cache_root"
export NX_CACHE_DIRECTORY="$cache_root"

log="$(mktemp)"
trap 'rm -f "$log"' EXIT
if bunx nx "$@" >"$log" 2>&1; then
  printf 'nx: requested targets succeeded\n'
  exit 0
fi
cat "$log" >&2
printf "nx: targets failed; fix the reported project findings and rerun the same 'just' recipe\n" >&2
exit 1
