#!/usr/bin/env bash
# Print the line-coverage total the deterministic tier just measured, or nothing.
#
# `just check` and `just gate` measure coverage and then used to print only a
# success line, so reading the number meant opening the `.coverage` artifact by
# hand. This reads that same artifact — a declared Nx output of the `test`
# target, so it is restored with a cache hit too — at the floor's own precision.
#
# Silent and successful when there is nothing to report: a missing or unreadable
# artifact must never turn a green gate red.
set -uo pipefail

root=${1:-$(git rev-parse --show-toplevel 2>/dev/null)}
[[ -n $root && -f "$root/.coverage" ]] || exit 0
total=$(cd "$root" && uv run coverage report --format=total 2>/dev/null)
[[ $total =~ ^[0-9]+(\.[0-9]+)?$ ]] || exit 0
printf '%s\n' "$total"
