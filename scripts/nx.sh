#!/usr/bin/env bash
# Give every checkout of one repository the same local Nx cache without putting
# generated state in a worktree. Nx's content hash still guards every replay.
set -euo pipefail

repo_identity="$(git config --get remote.origin.url || git rev-parse --show-toplevel)"
repo_key="$(printf '%s' "$repo_identity" | sha256sum | cut -c1-16)"
cache_root="${XDG_CACHE_HOME:-${HOME}/.cache}/ai-orchestrator/nx/${repo_key}"
mkdir -p "$cache_root"
export NX_CACHE_DIRECTORY="$cache_root"

exec bunx nx "$@"
