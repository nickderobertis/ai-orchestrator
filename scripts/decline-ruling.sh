#!/usr/bin/env bash
# The one executable `$ORCHESTRATOR_DECLINE_RULING` names: the engine's own `onepipeline
# decline-ruling`, from this checkout's `bun.lock` install where it has one, against the run
# this dispatch belongs to — `ONEPIPELINE_RUN_ID`, which the engine puts in every
# dispatch's environment, on its worker's side and its judge's alike — with every other
# argument, stdin, stdout and exit status the verb's. It mirrors `scripts/ask-manager.sh`;
# the verb raises a non-blocking `declined-ruling` surface on that run's channel, carrying
# the reason, and refuses a ruling that is the user's.
set -euo pipefail
engine="${BASH_SOURCE[0]%/*}/../node_modules/.bin/onepipeline"
# llmlint: ignore[boundary_inputs_validated] This line reads no input to validate: it falls back to the engine's own name, which `exec` then resolves against the dispatch's PATH, exactly as `scripts/ask-manager.sh` does at the same seam and for the same reason — a dispatch with no `node_modules` reaches the engine that way.
[ -x "$engine" ] || engine=onepipeline
# The verb takes the run as its one positional argument and this adapter names it, so the
# run is the one thing checked here: without it the verb would read the reason's file as
# the run id. Exit 2 is the verb's own status for a refusal, so a caller reads one status.
if [ -z "${ONEPIPELINE_RUN_ID-}" ]; then
    echo "decline-ruling: ONEPIPELINE_RUN_ID is not set, so there is no run whose manager to tell; run this from inside a dispatched turn, whose environment the engine names its run in" >&2
    exit 2
fi
# llmlint: ignore[robust_shell, tool_output_is_signal, boundary_inputs_validated] Nothing more is read here to validate: `"$@"` and stdin are handed to the verb untouched by an `exec`, so the engine validates the ruling reference and the reason at the one boundary there is. The adapter decides nothing else: stdout, stderr and exit status are the verb's, or the shell's own when the verb cannot be launched, exactly as `scripts/ask-manager.sh` passes them through.
exec "$engine" decline-ruling "$ONEPIPELINE_RUN_ID" "$@"
