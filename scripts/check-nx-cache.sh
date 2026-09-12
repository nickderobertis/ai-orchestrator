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
if ! { mv "$source_repo/project.fixture.json" "$source_repo/project.json" && mkdir -p "$source_repo/scripts" && cp "$root/scripts/nx.sh" "$root/scripts/preserved-log.sh" "$root/scripts/install-lock.sh" "$root/scripts/workspace-install.sh" "$root/scripts/python-install.sh" "$root/scripts/onetaskgraph-install.sh" "$source_repo/scripts/"; }; then echo "nx cache check: repair fixture files and retry 'just check'" >&2; exit 1; fi
# Resolve this fixture's tree once per host, not once per `just check`. Without a
# lockfile every run re-asked the registry for ~120 package manifests, and those
# round-trips — not the install, which hardlinks out of an already warm package
# cache in well under a second — were the whole 140 seconds this check charged
# every commit. The answer never varied: the two versions are injected from the
# root manifest above, so it is a pure function of that manifest and the resolver.
# Key the shared lockfile on exactly those two and both worktrees below do a real
# `bun install` from a warm cache instead of a cold resolution.
bun_version="$(bun --version)" || { echo "nx cache check: install bun and retry 'just check'" >&2; exit 1; }
fixture_key="$( { cat "$source_repo/package.json" && printf '%s' "$bun_version"; } | sha256sum | cut -c1-16)" || { echo "nx cache check: cannot derive the fixture resolution key; verify sha256sum is available and retry 'just check'" >&2; exit 1; }
[[ "$fixture_key" =~ ^[0-9a-f]{16}$ ]] || { echo "nx cache check: derived an invalid fixture resolution key; verify sha256sum output and retry 'just check'" >&2; exit 1; }
# Where the shared cache lives comes from the environment, so it is an input like
# any other. An empty or relative value would not fail here — it would silently
# resolve against whatever directory this check happens to be running in, put the
# entries somewhere no later run looks, and quietly cost every commit a full
# resolution while appearing to work.
cache_home="${XDG_CACHE_HOME:-}"
if [[ -z "$cache_home" && -n "${HOME:-}" ]]; then cache_home="$HOME/.cache"; fi
[[ "$cache_home" == /?* ]] || { echo "nx cache check: XDG_CACHE_HOME (or HOME) must name an absolute directory, got '$cache_home'; correct it and retry 'just check'" >&2; exit 1; }
lock_cache="$cache_home/ai-orchestrator/nx-cache-fixture"
shared_lock="$lock_cache/$fixture_key.lock"
shared_manifest="$lock_cache/$fixture_key.manifest"
mkdir -p "$lock_cache" || { echo "nx cache check: cannot create shared resolution cache '$lock_cache'; repair its parent permissions and retry 'just check'" >&2; exit 1; }
# This cache lives outside the repository, so whatever comes back from it is an
# untrusted input however it got there — a truncated publish, a restored backup,
# an entry something else wrote under the same name. Seeding an unchecked file
# would not fail here; it would surface much later as an unrelated
# --frozen-lockfile error inside a worktree this check builds. So the entry is
# validated before it is trusted, on both counts a later install depends on. It has
# to parse whole and carry the three properties Bun requires, which is what makes
# truncation detectable — a file cut off mid-write keeps its opening bytes, so any
# check that reads only a prefix would pass exactly the entry most likely to be
# broken. And it has to come with the manifest it was actually resolved from,
# byte-identical to the one being resolved now, because the key alone cannot
# establish that: a key is a claim about provenance, not evidence of it. Anything
# that fails either is discarded and re-resolved rather than reported, because a
# repairable cache is not an error.
if ! { [[ -r "$shared_lock" && -r "$shared_manifest" ]] &&
  cmp -s "$shared_manifest" "$source_repo/package.json" &&
  bun -e 'const text = await Bun.file(process.argv[1]).text(); const lock = JSON.parse(text.replace(/,(\s*[}\]])/g, (_, tail) => tail)); if (typeof lock.lockfileVersion !== "number" || typeof lock.workspaces !== "object" || lock.workspaces === null || typeof lock.packages !== "object" || lock.packages === null) throw new Error("incomplete lockfile");' "$shared_lock" >/dev/null 2>&1; }; then
  rm -f "$shared_lock" "$shared_manifest" || { echo "nx cache check: cannot discard the unusable shared resolution in '$lock_cache'; repair its permissions and retry 'just check'" >&2; exit 1; }
  (cd "$source_repo" && bun install --lockfile-only) >"$temp/resolve.log" 2>&1 || { cat "$temp/resolve.log" >&2; echo "nx cache check: resolve the fixture dependency tree and retry 'just check'" >&2; exit 1; }
  # Published by rename because a concurrent check is computing the same answer:
  # a half-written lockfile would fail every later run's --frozen-lockfile install.
  # The manifest lands last, so a concurrent reader never accepts a lockfile whose
  # evidence of provenance has not arrived yet.
  if ! { cp "$source_repo/bun.lock" "$shared_lock.$$" && mv -f "$shared_lock.$$" "$shared_lock" && cp "$source_repo/package.json" "$shared_manifest.$$" && mv -f "$shared_manifest.$$" "$shared_manifest"; }; then echo "nx cache check: cannot publish the shared fixture lockfile to '$shared_lock'; repair its permissions and retry 'just check'" >&2; exit 1; fi
fi
cp "$shared_lock" "$source_repo/bun.lock" || { echo "nx cache check: cannot seed the fixture lockfile from '$shared_lock'; remove it and retry 'just check'" >&2; exit 1; }
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
