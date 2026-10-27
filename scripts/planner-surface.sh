#!/usr/bin/env bash
# Raise a non-blocking planner status update: `just channel-surface RUN [TEXT]`.
#
# The published verb names the surface's kind and reads its text from a file, or
# from stdin when none is named (`onepipeline surface --kind check-in RUN [FILE]`);
# the recipe keeps the positional shape the planner and the docs use and hands the
# text over on stdin, so no prose it was given is ever a command-line word. `-`, or
# no text at all, reads the message from this recipe's own stdin, which is how an
# agent pipes a long update in.
set -euo pipefail

run="${1:-}"
[ -n "$run" ] || {
    echo "channel-surface: no run id was given, and a surface names the channel it goes to" >&2
    echo "usage: planner-surface.sh <run-id> [text]; 'just runs' lists the run ids" >&2
    exit 2
}
shift
message="$*"

if [ -z "$message" ] || [ "$message" = "-" ]; then
    message=$(cat)
fi
[ -n "${message//[[:space:]]/}" ] || {
    echo "channel-surface: status update must be a non-empty string" >&2
    echo "  give it as arguments after the run id, or pipe it in on stdin" >&2
    exit 2
}

# The engine `bun.lock` installs into this checkout, by absolute path: the platform
# package's binary, never the npm launcher, for the reason `scripts/npm-binary.sh` gives.
if ! script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd); then
    echo "channel-surface: the directory holding $0 could not be entered; run it from a readable checkout" >&2
    exit 2
fi
# shellcheck source=scripts/npm-binary.sh
# llmlint: ignore[boundary_inputs_validated, robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at.
. "$script_dir/npm-binary.sh"
engine=$(npm_binary onepipeline-cli onepipeline) || exit 2
if [ ! -x "$engine" ]; then
    echo "channel-surface: this checkout has no executable engine at $engine; run 'just bootstrap' to install the pinned releases from bun.lock, then retry" >&2
    exit 2
fi
# llmlint: ignore[tool_output_is_signal] onepipeline's own refusal names the run it could not find and the surface it did not queue, so a second diagnosis here would be a less informed one in front of a caller who already has the engine's.
printf '%s\n' "$message" | "$engine" surface --kind check-in "$run"
