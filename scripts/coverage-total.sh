#!/usr/bin/env bash
# Print the line-coverage total the deterministic tier just measured, or nothing.
#
# `just check` and `just gate` measure coverage and then used to print only a
# success line, so reading the number meant opening the `.coverage` artifact by
# hand. This reads that same artifact — a declared Nx output of the `test`
# target, so it is restored with a cache hit too — at the floor's own precision.
#
# Every step that can legitimately have nothing to report exits 0 explicitly: a
# missing artifact, an unavailable interpreter, or a total below the floor (which
# makes `coverage report` exit 2) must never turn a green gate red. The floor
# itself is enforced by the `test` target, not here.
set -euo pipefail

root=${1:-}
if [[ -z $root ]]; then
    root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
fi
[[ -f "$root/.coverage" ]] || exit 0
# `coverage report` exits 2 when the total is below the floor but still prints it.
# `|| true` keeps that number — the assignment has already happened, and only the
# status is being swallowed — where `|| total=""` would throw away the very output
# this is here to read. The shape check below decides whether it is usable.
total=$(cd "$root" && uv run coverage report --format=total 2>/dev/null) || true
[[ $total =~ ^[0-9]+(\.[0-9]+)?$ ]] || exit 0
printf '%s\n' "$total"
