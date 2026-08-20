#!/usr/bin/env bash
# The one source of this workspace's locked Python install, which `scripts/nx.sh`
# heals through before every invocation as it heals the Bun install through
# `scripts/workspace-install.sh`. Nothing here serializes it: uv takes an exclusive
# lock on the environment itself, which is what two concurrent syncs contend for.
set -euo pipefail

script_dir="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
repo_root="$(dirname -- "$script_dir")"
# shellcheck source=scripts/preserved-log.sh
. "$script_dir/preserved-log.sh"

if [[ $# -gt 0 ]]; then
    echo "python-install: expected no arguments, got '$*'; rerun it with none — the locked sync it performs is already idempotent, so there is no flag to reinstall or refresh with" >&2
    exit 2
fi

# uv's own "the environment is provided, do not touch it" signal, so a caller that
# already pointed uv at an environment is not synced over it. Read as uv reads it:
# a boolean, not a presence.
if [[ -n ${UV_NO_SYNC+set} ]]; then
    case ${UV_NO_SYNC,,} in
        1 | true | yes | on | y) exit 0 ;;
        0 | false | no | off | n) ;;
        *)
            echo "python-install: UV_NO_SYNC='$UV_NO_SYNC' is not a value uv reads; set it to 1/true/yes/on/y to keep the environment you provided, to 0/false/no/off/n to provision from the lockfile, or leave it unset" >&2
            exit 2
            ;;
    esac
fi

# A workspace with no committed Python lockfile has no Python environment to
# provision. `tests/fixtures/nx-cache` is one: the cache contract is proven by
# copying this wrapper chain into a TypeScript-only tree and running it for real.
if [[ ! -f $repo_root/uv.lock ]]; then
    exit 0
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "python-install: 'uv' is not installed, so the environment '$repo_root/uv.lock' declares cannot be provisioned; install uv and run 'just bootstrap'" >&2
    exit 1
fi

preserved_log_open "$repo_root" python-install || exit 1
log=$PRESERVED_LOG
# `--locked` rather than a plain sync: the committed lockfile decides what is
# installed, and a lockfile that would have to move is an error here instead of a
# silent update from under the change being verified. `uv run` does the opposite —
# it re-resolves and rewrites `uv.lock` on its way into a target — which on a branch
# whose subject *is* a pin means the gate verifies something the branch does not say.
if ! (cd "$repo_root" && uv sync --locked) 2>&1 | redact_secrets >"$log"; then
    cat "$log" >&2
    echo "python-install: provision the locked Python environment and retry; a lockfile that has to move is refreshed with 'uv lock' and committed as part of the change (full output: $log)" >&2
    exit 1
fi
