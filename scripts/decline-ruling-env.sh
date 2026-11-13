# shellcheck shell=bash
# The ONE source of the decline seam every dispatch is given: `ORCHESTRATOR_DECLINE_RULING`,
# the path of `scripts/decline-ruling.sh`, which execs the engine's `onepipeline
# decline-ruling` against the dispatch's own run — how a worker, its judge or a planner that
# judges a manager's ruling wrong for the user's goal says so to the manager, with its reason,
# rather than following it silently or setting it aside unannounced.
# `config/onejudge.base.yaml`, `templates/dispatch-appendix.md` and `personas/planner.yaml`
# tell an agent to run the command that variable names, so a dispatch given nothing would
# leave that instruction expanding to the empty string.
#
# It is one of the resolvers `scripts/dispatch-env.sh` names, so it is established twice by
# one definition: by `scripts/onepipeline.sh` when a driver starts, and by
# `scripts/dispatch-env-hook.sh` before every node-scope dispatch — which is how a driver
# launched before this seam existed hands it to its next dispatch. A dispatch's judge side
# inherits the same environment its worker side does, so both receive it.
#
# Strict mode is established here rather than inherited, as scripts/ask-manager-env.sh does:
# the resolution below must abort rather than fall through to exporting a path that names
# nothing.
set -euo pipefail

# Derive, validate, and export ORCHESTRATOR_DECLINE_RULING, from this file's own location —
# never from PATH, which a dispatch inherits from whichever session launched it. $1 names
# the calling launcher so its diagnostics stay attributable.
export_decline_ruling() {
    local caller=${1:?export_decline_ruling: the name of the calling launcher is required, so its diagnostics stay attributable}
    local here wrapper
    # llmlint: ignore[changed_behavior_has_e2e] Reachable only when this helper's own directory stops being enterable between a caller sourcing it and calling this; no journey can produce that without racing the filesystem the test itself runs on.
    if ! here=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd); then
        echo "$caller: the decline-ruling wrapper could not be resolved from this checkout; run the launch from inside a checkout, so the wrapper a dispatch is given is that checkout's" >&2
        return 2
    fi
    # All three, as for the ask wrapper: what is exported is a command an agent runs.
    wrapper="$here/decline-ruling.sh"
    if [ ! -f "$wrapper" ] || [ ! -r "$wrapper" ] || [ ! -x "$wrapper" ]; then
        echo "$caller: the decline-ruling wrapper is not an executable file at $wrapper; restore it from the repository and 'chmod +x' it, so the agents this launches can decline a manager's ruling with their reason rather than follow it silently" >&2
        return 2
    fi
    export ORCHESTRATOR_DECLINE_RULING="$wrapper"
}
