#!/usr/bin/env bash
set -euo pipefail

workspace_root=$(git rev-parse --show-toplevel)
proof_root=$(mktemp -d)
first_checkout="$proof_root/first"
second_checkout="$proof_root/second"
proof_cache="$proof_root/cache"

cleanup() {
    git -C "$workspace_root" worktree remove --force "$first_checkout" >/dev/null 2>&1 || true
    git -C "$workspace_root" worktree remove --force "$second_checkout" >/dev/null 2>&1 || true
    rm -rf "$proof_root"
}
trap cleanup EXIT

git -C "$workspace_root" worktree add --detach "$first_checkout" HEAD >/dev/null
git -C "$workspace_root" worktree add --detach "$second_checkout" HEAD >/dev/null
ln -s "$workspace_root/node_modules" "$first_checkout/node_modules"
ln -s "$workspace_root/node_modules" "$second_checkout/node_modules"

NX_CACHE_DIRECTORY="$proof_cache" bash "$first_checkout/scripts/nx.sh" run orchestrator:build \
    --skip-nx-cache >/dev/null
rm -rf "$first_checkout/dist"
NX_CACHE_DIRECTORY="$proof_cache" bash "$first_checkout/scripts/nx.sh" \
    run orchestrator:build >/dev/null
second_output=$(NX_CACHE_DIRECTORY="$proof_cache" \
    NX_TASKS_RUNNER_DYNAMIC_OUTPUT=false \
    bash "$second_checkout/scripts/nx.sh" run orchestrator:build)

if [[ "$second_output" != *"[local cache]"* && "$second_output" != *"existing outputs match the cache"* ]]; then
    echo "cross-worktree cache proof failed: second build was not restored from cache" >&2
    echo "$second_output" >&2
    exit 1
fi

echo "cross-worktree cache proof passed: clean build succeeded and linked worktree restored it"
