#!/usr/bin/env bash
set -euo pipefail

workspace=$(git rev-parse --show-toplevel)
probe=$(mktemp -d)
trap 'rm -rf "$probe"' EXIT

git clone --quiet --shared "$workspace" "$probe/clone"
first=$("$workspace/scripts/nx.sh" nx run orchestrator:typecheck --skip-nx-cache=false 2>&1)
second=$(cd "$probe/clone" && ./scripts/nx.sh nx run orchestrator:typecheck --skip-nx-cache=false 2>&1)

if [[ "$second" != *"read from cache"* && "$second" != *"[local cache]"* ]]; then
    printf '%s\n' "$first" "$second" >&2
    echo "Nx did not replay orchestrator:typecheck from the shared cache" >&2
    exit 1
fi
echo "Nx shared-cache replay verified across linked clones"
