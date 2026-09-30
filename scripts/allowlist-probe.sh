#!/usr/bin/env bash
# `just probe-allowlist` — establish the identities a real turn needs, then hand over to
# `scripts/allowlist-probe.py`, which drives the turns and says how nothing real is reached.
#
# The identities' config directories and the alternate Codex home come from
# `scripts/dispatch-env.sh` exactly as a launch establishes them, so the probe falls through
# the same chain `just smoke` does. Every argument reaches the probe.
set -euo pipefail

fail() {
    echo "probe-allowlist: $*" >&2
    exit 1
}

script_dir="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)" \
    || fail "cannot resolve this script's directory from '$0'; run it as \`just probe-allowlist\` from a readable checkout"
repo_root="$(dirname -- "$script_dir")" \
    || fail "cannot resolve the checkout above $script_dir; run it from a readable checkout"

# shellcheck source=scripts/dispatch-env.sh
. "$script_dir/dispatch-env.sh" \
    || fail "cannot load $script_dir/dispatch-env.sh; restore it from the repository, then retry"
export_dispatch_environment probe-allowlist \
    || fail "could not establish the identities' environment (its message is above); correct what it names, then retry"

cd -- "$repo_root" \
    || fail "cannot enter the checkout $repo_root; check it is readable, then retry"
# The checkout's own interpreter, as `just bootstrap` provisions it, rather than a `uv run`:
# a failing `uv` exits 1 or 2 as well, which would read as the probe's own verdict — 0 every
# row as the table says, 1 a row that is not or a probe that could not run, 2 arguments it
# refused. Any other status is the run itself ending some other way.
[ -x "$repo_root/.venv/bin/python" ] \
    || fail "this checkout has no interpreter at .venv/bin/python; run \`just bootstrap\` to provision it, then retry"
status=0
"$repo_root/.venv/bin/python" "$script_dir/allowlist-probe.py" "$@" || status=$?
case "$status" in
    0 | 1 | 2) exit "$status" ;;
esac
if [ "$status" -gt 128 ]; then
    fail "the probe was terminated by signal $((status - 128)); re-run it, then run \`just bootstrap\` if it repeats"
fi
fail "the probe ended with exit $status, a status it never gives; the output above says why; re-run it, then run \`just bootstrap\` if it repeats"
