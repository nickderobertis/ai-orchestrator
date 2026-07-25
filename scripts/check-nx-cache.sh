#!/usr/bin/env bash
set -euo pipefail
root="$(git rev-parse --show-toplevel)"; temp="$(mktemp -d)"; trap 'rm -rf "$temp"' EXIT
source_repo="$temp/source"; first="$temp/first"; second="$temp/second"; cache="$temp/cache"
mkdir -p "$source_repo"; cp -R "$root/tests/fixtures/nx-cache/." "$source_repo/"
bun -e 'const root = await Bun.file(process.argv[1]).json(); const fixture = await Bun.file(process.argv[2]).json(); fixture.devDependencies = {nx: root.devDependencies.nx, typescript: root.devDependencies.typescript}; await Bun.write(process.argv[2], `${JSON.stringify(fixture, null, 2)}\n`);' "$root/package.json" "$source_repo/package.json"
mv "$source_repo/project.fixture.json" "$source_repo/project.json"; cp "$root/scripts/nx.sh" "$source_repo/nx.sh"
git -C "$source_repo" init -q; git -C "$source_repo" add .
git -C "$source_repo" -c user.name=test -c user.email=test.invalid commit -qm fixture
git -C "$source_repo" remote add origin https://example.invalid/nx-cache-proof.git
git -C "$source_repo" worktree add -q --detach "$first" HEAD; git -C "$source_repo" worktree add -q --detach "$second" HEAD
(cd "$first"; just bootstrap; XDG_CACHE_HOME="$cache" AI_ORCHESTRATOR_NX_SHOW_OUTPUT=1 ./nx.sh run cache-proof:typecheck) >"$temp/first.log" 2>&1 || { cat "$temp/first.log" >&2; echo "nx cache check: first fixture failed; repair the fixture bootstrap or target and retry 'just check'" >&2; exit 1; }
(cd "$second"; just bootstrap; XDG_CACHE_HOME="$cache" AI_ORCHESTRATOR_NX_SHOW_OUTPUT=1 ./nx.sh run cache-proof:typecheck) >"$temp/second.log" 2>&1 || { cat "$temp/second.log" >&2; echo "nx cache check: second fixture failed; repair the fixture bootstrap or target and retry 'just check'" >&2; exit 1; }
grep -Eq 'local cache|existing outputs match the cache' "$temp/second.log" || { cat "$temp/second.log" >&2; echo "nx cache check: second worktree missed; verify the shared cache path and target inputs, then retry 'just check'" >&2; exit 1; }
sed -i 's/"valid"/1/' "$second/src/index.ts"
if (cd "$second"; XDG_CACHE_HOME="$cache" ./nx.sh run cache-proof:typecheck) >"$temp/broken.log" 2>&1; then echo "nx cache check: broken input replayed success; inspect Nx input declarations before retrying 'just check'" >&2; exit 1; fi
grep -Fq "not assignable to type 'string'" "$temp/broken.log" || { cat "$temp/broken.log" >&2; echo "nx cache check: unexpected failure; repair the fixture typecheck journey and retry 'just check'" >&2; exit 1; }
printf 'nx cache check: cross-worktree hit and broken-input miss verified\n'
