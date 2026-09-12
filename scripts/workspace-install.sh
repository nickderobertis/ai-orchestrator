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
# which finds the winner's tree already in agreement and installs nothing. The
# lock is the tree's rather than the caller's, because the two are not always one
# directory: a copy of this checkout that symlinks `node_modules` at the install
# it shares — every journey `tests/e2e/nx_workspace.py` copies — is a second
# caller over one tree, and a lock kept beside each caller serialized nothing
# between them. That is not harmless even when the tree is already in agreement:
# measured on Bun 1.3.14, four `bun install --frozen-lockfile` at once over one
# in-sync shared tree fail with `Failed to link <pkg>: EEXIST`, because a
# no-change run still re-links every package carrying a `bin`, and the racers
# collide on those. Two Nx targets running two pytest processes is where the
# gate met it, which no xdist group can reach.
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

# The lock lives beside the preserved logs of the checkout that *owns* the tree
# — the one `node_modules` resolves into — because both are per-worktree
# diagnostic state that is already ignored; the repository root stays clean. For
# a checkout whose `node_modules` is its own, or does not exist yet, that is this
# checkout. The preserved log below stays this caller's, since it is this
# caller's run it records. Resolved by entering the tree and asking where it
# physically is, which every POSIX shell does, rather than by `readlink -f`.
#
# A tree that exists and cannot be entered is a third answer, not the second:
# read as absent, a dangling symlink or an unreadable directory would take this
# checkout's lock while whatever it points at is still the tree Bun writes, so
# the one property the lock exists for — every caller over one tree on one lock —
# would be lost exactly where the tree is already broken. `-e` follows a symlink
# and `-L` does not, so between them "absent" is neither a target nor a link.
modules="$repo_root/node_modules"
if [[ ! -e "$modules" && ! -L "$modules" ]]; then
    tree_root="$repo_root"
elif tree_root="$(CDPATH='' cd -P -- "$modules" 2>/dev/null && pwd -P)"; then
    tree_root="$(dirname -- "$tree_root")"
elif [[ "$force" == true ]]; then
    # A forced run discards that tree below before it installs, so the tree it
    # then writes is this checkout's own, and so is the lock.
    tree_root="$repo_root"
else
    echo "workspace-install: '$modules' exists but cannot be entered to find the tree it resolves into, so this install cannot be serialized with that tree's other callers; remove it if it is a stale symlink or an unreadable directory, or run 'just bootstrap' to discard it and reinstall from the lockfile" >&2
    exit 1
fi

# The lock itself — preparing `.logs`, securing it, holding the descriptors — is
# `scripts/install-lock.sh`'s, shared with `scripts/onetaskgraph-install.sh` so that
# neither installer can be hardened without the other. What stays here is *which*
# checkout's lock to take.
# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being readable between the resolution at the head of this file and here; no journey can produce that without racing the filesystem the test itself runs on.
lock_helper="$script_dir/install-lock.sh"
if [ ! -f "$lock_helper" ] || [ ! -r "$lock_helper" ]; then
    echo "workspace-install: required helper is not a readable regular file: $lock_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 1
fi
# shellcheck source=scripts/install-lock.sh
. "$lock_helper" || {
    echo "workspace-install: required helper $lock_helper could not be loaded; it is readable but did not load — restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 1
}
if ! command -v install_lock_take >/dev/null 2>&1; then
    echo "workspace-install: helper $lock_helper loaded but defines no install_lock_take; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 1
fi

install_lock_take workspace-install "$tree_root" workspace-install.lock || exit 1
# A forced run over a link into another checkout's tree changes which tree the
# path names mid-run: the link's target before it discards the link, a tree of
# this checkout's own after. One lock cannot cover that. The tree's lock, taken
# above, lets a caller already writing through the link finish before the link
# goes; this checkout's own, taken second, is what a caller arriving once the
# link is gone waits on. Always in that order, so two forced runs cannot deadlock.
# Compared physically, since `$repo_root` is the caller's spelling of a path the
# tree's root was resolved with `pwd -P`: one directory under two spellings is
# one lock file, and a second `flock` on it would wait on this process forever.
# The helper holds the second lock on a descriptor pair of its own, so taking it
# releases nothing the first is holding.
if [[ "$force" == true && "$tree_root" != "$(CDPATH='' cd -P -- "$repo_root" && pwd -P)" ]]; then
    install_lock_take workspace-install "$repo_root" workspace-install.lock || exit 1
fi
# What a forced run adds, now that the ordinary one already reconciles: distrust
# the installed tree itself. `just bootstrap` is its caller, and a clone it runs
# in can carry a `node_modules` no lockfile describes — one Bun would keep,
# because nothing in the lockfile contradicts it. `-L` beside `-e` for the same
# reason as above: a dangling link is a tree to discard, and `-e` alone left it
# standing for Bun to fail on. Removing a link removes the link and not the tree
# behind it, so a forced run in a copy leaves the checkout it shared with alone.
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
