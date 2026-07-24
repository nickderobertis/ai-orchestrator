#!/usr/bin/env bash
set -euo pipefail

workspace_root=$(git rev-parse --show-toplevel)
common_git_dir=$(git rev-parse --path-format=absolute --git-common-dir)
cache_root=${ORCHESTRATOR_CACHE_DIR:-"$common_git_dir/ai-orchestrator-nx"}

export NX_CACHE_DIRECTORY=${NX_CACHE_DIRECTORY:-"$cache_root"}
export NX_DAEMON=${NX_DAEMON:-false}

cd "$workspace_root"
exec bunx nx "$@"
