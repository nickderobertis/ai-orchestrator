#!/usr/bin/env bash
set -euo pipefail
root="$(git rev-parse --show-toplevel)"; temp="$(mktemp -d)"; trap 'rm -rf "$temp"' EXIT
source_repo="$temp/source"; first="$temp/first"; second="$temp/second"; cache="$temp/cache"
mkdir -p "$source_repo"; cp -R "$root/tests/fixtures/nx-cache/." "$source_repo/"
mv "$source_repo/project.fixture.json" "$source_repo/project.json"; cp "$root/scripts/nx.sh" "$source_repo/nx.sh"
git -C "$source_repo" init -q; git -C "$source_repo" add .
git -C "$source_repo" -c user.name=test -c user.email=test.invalid commit -qm fixture
git -C "$source_repo" remote add origin https://example.invalid/nx-cache-proof.git
git -C "$source_repo" worktree add -q --detach "$first" HEAD; git -C "$source_repo" worktree add -q --detach "$second" HEAD
(cd "$first"; bun install >/dev/null; XDG_CACHE_HOME="$cache" AI_ORCHESTRATOR_NX_SHOW_OUTPUT=1 ./nx.sh run cache-proof:typecheck) >"$temp/first.log"
(cd "$second"; bun install >/dev/null; XDG_CACHE_HOME="$cache" AI_ORCHESTRATOR_NX_SHOW_OUTPUT=1 ./nx.sh run cache-proof:typecheck) >"$temp/second.log"
grep -Eq 'local cache|existing outputs match the cache' "$temp/second.log"
sed -i 's/"valid"/1/' "$second/src/index.ts"
if (cd "$second"; XDG_CACHE_HOME="$cache" ./nx.sh run cache-proof:typecheck) >"$temp/broken.log" 2>&1; then exit 1; fi
grep -Fq "not assignable to type 'string'" "$temp/broken.log"
printf 'nx cache check: cross-worktree hit and broken-input miss verified\n'
