#!/usr/bin/env bash
# The one source of this workspace's locked Bun install.
#
# Nx lives in `node_modules/.bin`, so no Nx target can run in a freshly created
# worktree until this has. `scripts/nx.sh` heals through it before every
# invocation and `just bootstrap` forces it, so no caller has to decide.
#
# Idempotent and quiet: without `--force`, an already provisioned workspace exits
# having done and said nothing, and concurrent callers serialize on one lock so
# two Nx invocations in a fresh worktree cannot install over each other.
set -euo pipefail

script_dir="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
repo_root="$(dirname -- "$script_dir")"
# shellcheck source=scripts/preserved-log.sh
. "$script_dir/preserved-log.sh"

force=false
case $# in
    0) ;;
    1)
        if [[ $1 != --force ]]; then
            echo "workspace-install: unknown argument '$1'; pass --force to reinstall an already provisioned workspace" >&2
            exit 2
        fi
        force=true
        ;;
    *)
        echo "workspace-install: expected at most one argument; pass --force alone to reinstall an already provisioned workspace" >&2
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
lock_file="$lock_dir/workspace-install.lock"
if ! { mkdir -p "$lock_dir" && chmod 700 "$lock_dir"; }; then
    echo "workspace-install: cannot prepare '$lock_dir' for the install lock; repair its parent permissions and retry" >&2
    exit 1
fi
# Guarded, not assumed: bash reports a failed `exec` redirection rather than
# taking the shell down with it, so a lock file that turned unreadable between
# `mkdir` and here still gets a diagnostic that names the path instead of a
# silent death. Creation and the descriptor are separate steps because `9<`
# alone will not create the file.
if ! : >>"$lock_file" || ! exec 9<"$lock_file"; then
    echo "workspace-install: cannot open the install lock at '$lock_file'; repair its permissions and retry" >&2
    exit 1
fi
if ! flock 9; then
    echo "workspace-install: cannot serialize the locked install; retry once no other install is running" >&2
    exit 1
fi
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
