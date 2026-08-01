#!/usr/bin/env bash
# Prove Nx replays a cached task across worktrees and misses on a changed input.
#
# Cache replay is this check's whole claim, so an ambient global cache skip — the
# lever an operator reaches for to force a re-judge of the llmlint tier — is
# dropped here rather than allowed to fail a check it says nothing about. Use
# `just lint-llm-diff <base> --skip-nx-cache` for that instead; the fixture below
# owns its own cache directory and asserts both a hit and a miss on their merits.
set -euo pipefail
unset NX_SKIP_NX_CACHE NX_DISABLE_NX_CACHE
root="$(git rev-parse --show-toplevel)" || { echo "nx cache check: resolve the repository root and retry 'just check'" >&2; exit 1; }
[[ "$root" = /* && -r "$root/package.json" && -d "$root/tests/fixtures/nx-cache" ]] || { echo "nx cache check: run from a complete repository checkout and retry 'just check'" >&2; exit 1; }
temp="$(mktemp -d)" || { echo "nx cache check: make temporary storage available and retry 'just check'" >&2; exit 1; }
trap 'rm -rf "$temp"' EXIT
source_repo="$temp/source"; first="$temp/first"; second="$temp/second"; cache="$temp/cache"
if ! { mkdir -p "$source_repo" && cp -R "$root/tests/fixtures/nx-cache/." "$source_repo/"; }; then echo "nx cache check: copy the fixture into temporary storage and retry 'just check'" >&2; exit 1; fi
bun -e 'const root = await Bun.file(process.argv[1]).json(); const fixture = await Bun.file(process.argv[2]).json(); if (typeof root.devDependencies?.nx !== "string" || typeof root.devDependencies?.typescript !== "string" || typeof fixture !== "object" || fixture === null || Array.isArray(fixture) || typeof fixture.name !== "string" || fixture.private !== true) throw new Error("expected root Nx/TypeScript versions and a private fixture package manifest"); fixture.devDependencies = {nx: root.devDependencies.nx, typescript: root.devDependencies.typescript}; await Bun.write(process.argv[2], JSON.stringify(fixture, null, 2) + "\n");' "$root/package.json" "$source_repo/package.json" || { echo "nx cache check: repair root and fixture package.json contracts, then retry 'just check'" >&2; exit 1; }
if ! { mv "$source_repo/project.fixture.json" "$source_repo/project.json" && mkdir -p "$source_repo/scripts" && cp "$root/scripts/nx.sh" "$root/scripts/preserved-log.sh" "$root/scripts/workspace-install.sh" "$source_repo/scripts/"; }; then echo "nx cache check: repair fixture files and retry 'just check'" >&2; exit 1; fi
if ! {
  git -C "$source_repo" init -q &&
    git -C "$source_repo" add . &&
    git -C "$source_repo" -c user.name=test -c user.email=test.invalid commit -qm fixture &&
    git -C "$source_repo" remote add origin https://example.invalid/nx-cache-proof.git &&
    git -C "$source_repo" worktree add -q --detach "$first" HEAD &&
    git -C "$source_repo" worktree add -q --detach "$second" HEAD
}; then echo "nx cache check: initialize both fixture worktrees successfully, then retry 'just check'" >&2; exit 1; fi
(cd "$first"; just bootstrap; XDG_CACHE_HOME="$cache" AI_ORCHESTRATOR_NX_SHOW_OUTPUT=1 ./scripts/nx.sh run cache-proof:typecheck) >"$temp/first.log" 2>&1 || { cat "$temp/first.log" >&2; echo "nx cache check: first fixture failed; repair the fixture bootstrap or target and retry 'just check'" >&2; exit 1; }
(cd "$second"; just bootstrap; XDG_CACHE_HOME="$cache" AI_ORCHESTRATOR_NX_SHOW_OUTPUT=1 ./scripts/nx.sh run cache-proof:typecheck) >"$temp/second.log" 2>&1 || { cat "$temp/second.log" >&2; echo "nx cache check: second fixture failed; repair the fixture bootstrap or target and retry 'just check'" >&2; exit 1; }
grep -Eq 'local cache|existing outputs match the cache' "$temp/second.log" || { cat "$temp/second.log" >&2; echo "nx cache check: second worktree missed; verify the shared cache path and target inputs, then retry 'just check'" >&2; exit 1; }
sed -i 's/"valid"/1/' "$second/src/index.ts" || { echo "nx cache check: make the fixture source writable and retry 'just check'" >&2; exit 1; }
if (cd "$second"; XDG_CACHE_HOME="$cache" ./scripts/nx.sh run cache-proof:typecheck) >"$temp/broken.log" 2>&1; then echo "nx cache check: broken input replayed success; inspect Nx input declarations before retrying 'just check'" >&2; exit 1; fi
grep -Fq "not assignable to type 'string'" "$temp/broken.log" || { cat "$temp/broken.log" >&2; echo "nx cache check: unexpected failure; repair the fixture typecheck journey and retry 'just check'" >&2; exit 1; }
# That failure's own log has to outlive the process that produced it. `nx.sh` used
# to write to a `mktemp` file an EXIT trap removed, so a failing run was readable
# once, on stderr, and a running one only through /proc. This asserts the surviving
# copy on the real script, right where a real failure has just happened.
preserved="$second/.logs/nx.log"
[[ -f "$preserved" ]] || { echo "nx cache check: the failing nx.sh run left no log at $preserved; restore scripts/preserved-log.sh and retry 'just check'" >&2; exit 1; }
grep -Fq "not assignable to type 'string'" "$preserved" || { cat "$preserved" >&2; echo "nx cache check: the preserved nx.sh log does not carry the failure it reported; repair scripts/nx.sh and retry 'just check'" >&2; exit 1; }
# `stat -c` is GNU and `stat -f` is BSD/macOS; ask each in turn rather than
# assuming the platform, so this check means the same thing wherever it runs.
mode=$(stat -c '%a' "$preserved" 2>/dev/null || stat -f '%Lp' "$preserved" 2>/dev/null) || { echo "nx cache check: cannot read the preserved log's mode; retry 'just check'" >&2; exit 1; }
[[ $mode == 600 ]] || { echo "nx cache check: preserved log $preserved is mode $mode, not 600; preserved evidence stays owner-only" >&2; exit 1; }
printf 'nx cache check: cross-worktree hit, broken-input miss, and preserved failure log verified\n'
