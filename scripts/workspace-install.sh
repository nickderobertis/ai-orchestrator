#!/usr/bin/env bash
# The one source of this workspace's locked Bun install.
#
# Nx lives in `node_modules/.bin`, so no Nx target can run in a freshly created
# worktree until this has. `scripts/nx.sh` heals through it before every
# invocation and `just bootstrap` forces it, so no caller has to decide.
#
# Bun itself is the guard: `bun install --frozen-lockfile` reconciles the
# installed tree against the committed lockfile and installs only the difference,
# so a workspace already in agreement with it exits in milliseconds having
# installed nothing and said nothing. Testing for the Nx binary instead answered
# for *an* install rather than *the locked* one, which left a moved pin invisible
# to every checkout that already had a `node_modules`.
#
# Concurrent callers still serialize on one lock, so two Nx invocations in a
# fresh worktree cannot install over each other: the loser waits, then asks Bun,
# which finds the winner's tree already in agreement and installs nothing.
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
            echo "workspace-install: unknown argument '$1'; pass --force to discard the installed tree and reinstall it from the lockfile" >&2
            exit 2
        fi
        force=true
        ;;
    *)
        echo "workspace-install: expected at most one argument; pass --force alone to discard the installed tree and reinstall it from the lockfile" >&2
        exit 2
        ;;
esac

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
# What a forced run adds, now that the ordinary one already reconciles: distrust
# the installed tree itself. `just bootstrap` is its caller, and a clone it runs
# in can carry a `node_modules` no lockfile describes — one Bun would keep,
# because nothing in the lockfile contradicts it.
if [[ $force == true && -e "$repo_root/node_modules" ]] && ! rm -rf -- "$repo_root/node_modules"; then
    echo "workspace-install: cannot remove '$repo_root/node_modules' to reinstall it; repair its permissions and retry" >&2
    exit 1
fi

preserved_log_open "$repo_root" workspace-install || exit 1
log=$PRESERVED_LOG
if ! (cd "$repo_root" && bun install --frozen-lockfile) 2>&1 | redact_secrets >"$log"; then
    cat "$log" >&2
    echo "workspace-install: install locked workspace dependencies and retry (full output: $log)" >&2
    exit 1
fi
