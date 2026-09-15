# shellcheck shell=bash
# The ONE source of the ask-manager seam every launch puts in its dispatches'
# environment, sourced by scripts/onepipeline.sh and scripts/plan.sh.
#
# `ORCHESTRATOR_ASK_MANAGER` holds the path of `scripts/ask-manager.sh`, the shim that
# hands one blocking question to `onemessagebus ask` on the run's channel, which is how
# a dispatched agent asks its manager instead of guessing at a decision fork.
# `personas/planner.yaml` tells an agent to run the command that
# variable names, so a launch that exports nothing leaves that instruction expanding to
# the empty string: a worker with no recourse and nothing to report it to.
#
# It is a helper rather than a line in each launcher because both callers need the
# same two things — the path, and the refusal when it is not runnable — and a second
# copy of either is a launch path that can drift into providing neither.

# This helper establishes strict mode itself rather than inheriting whatever the
# sourcing caller happened to set, exactly as scripts/codex-alt-home.sh does: the
# resolution below must abort rather than fall through to exporting a path that
# names nothing.
set -euo pipefail

# Derive, validate, and export ORCHESTRATOR_ASK_MANAGER. Resolved from this file's own
# location, because the wrapper is its neighbour in the checkout being launched from —
# never from PATH, which a dispatch inherits from whichever session launched it. $1
# names the calling launcher so its diagnostics stay attributable.
export_ask_manager() {
    # Named the way scripts/codex-alt-home.sh names its own required input: a caller
    # that forgot the label would otherwise abort on `1: unbound variable`, which says
    # nothing about which helper was called wrong.
    local caller=${1:?export_ask_manager: the name of the calling launcher is required, so its diagnostics stay attributable}
    local here wrapper
    # llmlint: ignore[changed_behavior_has_e2e] Reachable only when this helper's own directory stops being enterable between a caller sourcing it and calling this; no journey can produce that without racing the filesystem the test itself runs on.
    if ! here=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd); then
        echo "$caller: the ask-manager wrapper could not be resolved from this checkout; run the launch from inside a checkout, so the wrapper a dispatch is given is that checkout's" >&2
        return 2
    fi
    # All three, because what is exported is a command an agent runs: `-x` alone accepts
    # a directory of that name, and a shell script that cannot be read cannot be run
    # whatever its mode says.
    wrapper="$here/ask-manager.sh"
    if [ ! -f "$wrapper" ] || [ ! -r "$wrapper" ] || [ ! -x "$wrapper" ]; then
        echo "$caller: the ask-manager wrapper is not an executable file at $wrapper; restore it from the repository and 'chmod +x' it, so the agents this launches can stop and ask rather than guess" >&2
        return 2
    fi
    export ORCHESTRATOR_ASK_MANAGER="$wrapper"
}
