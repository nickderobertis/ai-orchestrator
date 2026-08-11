#!/usr/bin/env bash
# Raise a non-blocking planner status update: `just channel-surface RUN [TEXT]`.
#
# The published verb names the surface's kind and takes its text as an option
# (`onepipeline surface --kind check-in --message TEXT RUN`); the recipe keeps the
# positional shape the planner and the docs use. `-`, or no text at all, reads the
# message from stdin, which is how an agent pipes a long update in.
set -euo pipefail

run="${1:-}"
[ -n "$run" ] || { echo "usage: planner-surface.sh <run-id> [text]" >&2; exit 2; }
shift
message="$*"

if [ -z "$message" ] || [ "$message" = "-" ]; then
    message=$(cat)
fi
[ -n "${message//[[:space:]]/}" ] || {
    echo "channel-surface: status update must be a non-empty string" >&2
    exit 2
}

uv run onepipeline surface --kind check-in --message "$message" "$run"
