#!/usr/bin/env bash
# Send one of the three legacy planner verdicts over the live channel.
#
# `onepipeline reply` takes one envelope, on stdin or in a file, and accepts the
# legacy verdicts beside the versioned live-edit commands. The three convenience
# recipes are the planner's spelling of those verdicts, so the rendering lives here
# — one place that knows the envelope — rather than three times in the justfile.
#
# A verdict carries prose the planner typed, so it is rendered with a JSON encoder
# rather than interpolated into a template: an apostrophe or a newline in a
# rejection reason would otherwise produce an envelope the CLI refuses, or worse,
# one it accepts with the reason truncated at the first quote.
set -euo pipefail

usage() {
    echo "usage: planner-verdict.sh <approve|reject|continue> <run-id> [text]" >&2
    exit 2
}

verdict="${1:-}"
run="${2:-}"
[ -n "$verdict" ] && [ -n "$run" ] || usage
shift 2
text="$*"

case "$verdict" in
    approve)
        [ -z "$text" ] || { echo "channel-approve: approve does not accept a message" >&2; exit 2; }
        ;;
    reject | continue)
        [ -n "$text" ] || { echo "channel-$verdict: $verdict requires a message" >&2; exit 2; }
        ;;
    *) usage ;;
esac

envelope=$(python3 -c 'import json,sys
verdict, text = sys.argv[1], sys.argv[2]
payload = ({"completion": True, "reason": "approved"} if verdict == "approve"
           else {"completion": False, "reason": text, "message": text})
print(json.dumps(payload))' "$verdict" "$text") || {
    echo "channel-$verdict: could not render the reply envelope; check the message text and retry" >&2
    exit 2
}

printf '%s\n' "$envelope" | uv run onepipeline reply "$run"
