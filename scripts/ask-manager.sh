#!/usr/bin/env bash
# The one executable `$ORCHESTRATOR_ASK_MANAGER` names: the engine's own `onepipeline ask`,
# from this checkout's `bun.lock` install where it has one, with every argument, stdin, stdout
# and exit status the verb's.
set -euo pipefail
# shellcheck source=scripts/npm-binary.sh
# llmlint: ignore[boundary_inputs_validated, robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at.
. "${BASH_SOURCE[0]%/*}/npm-binary.sh"
# The platform package's binary, never the npm launcher, for the reason that file gives.
engine=$(npm_binary onepipeline-cli onepipeline) || engine=onepipeline
# llmlint: ignore[boundary_inputs_validated] This line reads no input to validate: it falls back to the engine's own name, which `exec` then resolves against the dispatch's PATH, and that is how a dispatch with no `node_modules` reaches the engine at all — the ask-seam journey drives it. A checkout's locked install is an invariant rather than a boundary, and `scripts/onepipeline.sh` carries this directive for the same reason at the same seam.
[ -x "$engine" ] || engine=onepipeline
# llmlint: ignore[robust_shell, tool_output_is_signal, boundary_inputs_validated] Nothing is read here to validate: `"$@"` and stdin are handed to the verb untouched by an `exec`, so the engine validates them at the one boundary there is, and a check added here would be this repository validating an argument list it does not own. The adapter's contract is that it decides nothing: stdout, stderr and exit status are the verb's, or the shell's own when the verb cannot be launched, so handling or rewording an exec failure here would be the adapter translating a result it must pass through unchanged.
exec "$engine" ask "$@"
