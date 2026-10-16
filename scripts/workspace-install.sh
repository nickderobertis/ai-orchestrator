#!/usr/bin/env bash
# The one source of this workspace's locked Bun install.
#
# Nx lives in `node_modules/.bin`, so no Nx target can run in a freshly created
# worktree until this has. Session setup runs it, and `just bootstrap` forces it;
# nothing else installs (AGENTS.md, "The checks").
#
# Bun itself is the guard: `bun install --frozen-lockfile` reconciles the
# installed tree against the committed lockfile and installs only the difference,
# so a workspace already in agreement with it exits in milliseconds having
# installed nothing and said nothing. Testing for the Nx binary instead answered
# for *an* install rather than *the locked* one, which left a moved pin invisible
# to every checkout that already had a `node_modules`.
#
# Concurrent callers serialize on this checkout's one lock, so two session starts
# in a fresh worktree cannot install over each other: the loser waits, then asks
# Bun, which finds the winner's tree already in agreement and installs nothing.
# That is not harmless even when the tree is already in agreement: measured on
# Bun 1.3.14, concurrent `bun install --frozen-lockfile` runs over one in-sync
# tree fail with `Failed to link <pkg>: EEXIST`, because a no-change run still
# re-links every package carrying a `bin`, and the racers collide on those.
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

# The lock itself — preparing `.logs`, securing it, holding the descriptors — belongs
# to `scripts/install-lock.sh`. It lives beside this checkout's preserved logs, since
# both are per-worktree diagnostic state that is already ignored.
# shellcheck source=scripts/install-lock.sh
# llmlint: ignore[boundary_inputs_validated, robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at.
. "$script_dir/install-lock.sh"

# The lock is this checkout's, so the tree it serializes has to be this checkout's too: a
# `node_modules` that is a link would have Bun write whatever tree it points at, under a
# lock none of that tree's other callers take. A forced run discards the link below.
modules="$repo_root/node_modules"
if [[ "$force" != true && -L "$modules" ]]; then
    echo "workspace-install: '$modules' is a symbolic link, so installing would write the tree it points at under this checkout's lock rather than that tree's; remove the link, or run 'just bootstrap' to discard it and reinstall from the lockfile" >&2
    exit 1
fi

install_lock_take workspace-install "$repo_root" workspace-install.lock || exit 1
# What a forced run adds, now that the ordinary one already reconciles: distrust
# the installed tree itself. `just bootstrap` is its caller, and a clone it runs
# in can carry a `node_modules` no lockfile describes — one Bun would keep,
# because nothing in the lockfile contradicts it. `-L` beside `-e`, because `-e`
# follows a link: a dangling one is a tree to discard, and `-e` alone left it
# standing for Bun to fail on.
if [[ "$force" == true && (-e "$modules" || -L "$modules") ]] && ! rm -rf -- "$modules"; then
    echo "workspace-install: cannot remove '$modules' to reinstall it; repair its permissions and retry" >&2
    exit 1
fi

preserved_log_open "$repo_root" workspace-install || exit 1
log=$PRESERVED_LOG
if ! (cd "$repo_root" && bun install --frozen-lockfile) 2>&1 | redact_secrets >"$log"; then
    cat "$log" >&2
    echo "workspace-install: install locked workspace dependencies and retry (full output: $log)" >&2
    exit 1
fi
