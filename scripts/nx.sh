#!/usr/bin/env bash
set -euo pipefail

repo_url=$(git config --get remote.origin.url 2>/dev/null || git rev-parse --show-toplevel)
local_origin=${repo_url#file://}
if [[ -d "$local_origin" ]]; then
    repo_url=$(git -C "$local_origin" config --get remote.origin.url 2>/dev/null || printf '%s' "$repo_url")
fi
case "$repo_url" in
    git@github.com:*) repo_url="https://github.com/${repo_url#git@github.com:}" ;;
    ssh://git@github.com/*) repo_url="https://github.com/${repo_url#ssh://git@github.com/}" ;;
esac
repo_url=${repo_url%.git}
repo_key=$(printf '%s' "$repo_url" | sha256sum | cut -c1-16)
account_home=$(getent passwd "$(id -u)" | cut -d: -f6)
cache_root=${XDG_CACHE_HOME:-"$account_home/.cache"}
export NX_CACHE_DIRECTORY="${NX_CACHE_DIRECTORY:-$cache_root/ai-orchestrator/nx/$repo_key}"

mkdir -p "$NX_CACHE_DIRECTORY"
exec bun "$@"
