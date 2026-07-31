#!/usr/bin/env bash
# The one source of this workspace's locked Bun install.
#
# Nx lives in `node_modules/.bin`, so every Nx target depends on this step, and a
# freshly created worktree carries none of it. `just check` used to heal that
# inline while a bare `./scripts/nx.sh` did not, so one missing install produced
# two different stories: `check` named the provisioning and repaired it, while
# every other Nx recipe failed with "Could not find Nx modules" under advice to
# fix project findings it had never reached. Owning the step here is what makes
# the two agree — `scripts/nx.sh` heals before it runs anything, `just bootstrap`
# forces the same install from a clean clone, and the e2e journeys that drive real
# Nx provision through this rather than asking an operator to run Bun by hand.
#
# Idempotent and safe to call from anywhere: without `--force` an already
# provisioned workspace exits immediately, and concurrent callers serialize on one
# lock so two Nx invocations in a fresh worktree cannot install over each other.
set -euo pipefail

script_dir="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
repo_root="$(dirname -- "$script_dir")"
# shellcheck source=scripts/preserved-log.sh
. "$script_dir/preserved-log.sh"

force=false
case "${1-}" in
    --force) force=true ;;
    "") ;;
    *)
        echo "workspace-install: unknown argument '$1'; pass --force to reinstall an already provisioned workspace" >&2
        exit 2
        ;;
esac

nx_bin="$repo_root/node_modules/.bin/nx"
if [[ $force == false && -x $nx_bin ]]; then
    exit 0
fi

# The lock lives beside the preserved logs because both are per-worktree
# diagnostic state that is already ignored; the repository root stays clean.
lock_dir="$repo_root/.logs"
if ! { mkdir -p "$lock_dir" && chmod 700 "$lock_dir"; }; then
    echo "workspace-install: cannot prepare '$lock_dir'; repair its parent permissions and retry" >&2
    exit 1
fi
exec 9>"$lock_dir/workspace-install.lock"
flock 9 || {
    echo "workspace-install: cannot serialize the locked install; retry once no other install is running" >&2
    exit 1
}
# Re-read under the lock: whoever held it may have just installed what this call
# came for, and reinstalling on top of that is pure cost.
if [[ $force == false && -x $nx_bin ]]; then
    exit 0
fi

preserved_log_open "$repo_root" workspace-install || exit 1
log=$PRESERVED_LOG
if ! (cd "$repo_root" && bun install --frozen-lockfile) 2>&1 | redact_secrets >"$log"; then
    cat "$log" >&2
    echo "workspace-install: install locked workspace dependencies and retry (full output: $log)" >&2
    exit 1
fi
printf 'workspace-install: locked workspace dependencies installed\n'
