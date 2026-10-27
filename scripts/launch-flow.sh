# shellcheck shell=bash
# The ONE way a launcher that creates runs over time holds its manager's session for its
# whole life: sourced by scripts/plan.sh, scripts/finish-plan.sh, scripts/follow-ups.sh and
# scripts/follow-ups-answer-comments.sh.
#
# A launcher like those starts a run, works between runs — a review, a check, a copy —
# then starts more, and may ask its manager a blocking question at any point. A session
# that owes only the runs that happen to exist can end its turn in a step between two of
# them, and the flow then launches runs and asks questions nothing is woken for. So each
# launcher re-executes itself inside the engine's `onepipeline flow run`: a flow owned by
# the launching session, which `onepipeline unwatched` — and through it the `Stop` hook —
# owes a watch from its start to its exit, and which every run the launcher starts records
# as its `flow`. What a flow promises is the engine's to state (the Flows section of its
# stop-guard page); this only enters one.
#
# It goes through scripts/onepipeline.sh, so the launcher session that owns the flow is
# established exactly as it is for `start`, and the flow's own channel is kept under this
# host's bus configuration. A launcher already inside a live flow of the same session —
# scripts/plan.sh handing over to scripts/finish-plan.sh, or the success hook's
# `just follow-ups` under a planning flow — is run in that flow by the engine rather than
# nested, so a flow is one watch however many launchers it passes through.

set -euo pipefail

#: Set, in the environment of the program `flow run` starts, to `<pid>:<flow>`: the pid of
#: the launcher that asked for the flow, and the flow it was in before (empty for none).
#: It says this process is that program rather than a launcher about to enter one only
#: when that pid is this process's parent — the `flow run` the launcher became, which
#: starts it — or this process itself, for an engine that runs it in place; any other
#: value was inherited from somewhere else and is discarded. The flow says whether the one
#: this runs in is the engine's new registration or one it joined.
LAUNCH_FLOW_ENTERED_ENV=ORCHESTRATOR_FLOW_ENTERED

# Run the calling launcher inside a flow named ``$2``, re-executing ``$3`` with the rest of
# the arguments as its argv; ``$1`` names the launcher, so its diagnostics stay
# attributable. In the re-executed launcher it returns, having printed the receipt — the
# flow's id and the recipe that watches it — when the flow is a new one, so a manager's
# arming it is one copy-paste. It never returns in the launcher that asked.
run_in_launch_flow() {
    local caller=${1:?run_in_launch_flow: the name of the calling launcher is required, so its diagnostics stay attributable}
    local name=${2:?run_in_launch_flow: the name of the flow is required}
    local program=${3:?run_in_launch_flow: the launcher to re-execute is required}
    shift 3
    # A flow's id is minted from letters, digits, `.`, `_` and `-` alone, and a run name
    # or a run id this is given may carry anything else a caller typed.
    name=${name//[^A-Za-z0-9._-]/-}
    local entered=${!LAUNCH_FLOW_ENTERED_ENV-}
    unset "$LAUNCH_FLOW_ENTERED_ENV"
    local asked=${entered%%:*}
    if [ -n "$entered" ] && { [ "$asked" = "$PPID" ] || [ "$asked" = "$$" ]; }; then
        local before=${entered#*:}
        # No flow means `flow run` had no session to give it to and has said so itself;
        # the same flow as before is one this launcher joined, whose receipt was printed
        # by whatever registered it.
        # Only an id in the alphabet the engine mints one in is printed, so nothing the
        # environment carried reaches a terminal or the command a manager pastes.
        if [[ "${ONEPIPELINE_FLOW:-}" =~ ^[A-Za-z0-9._-]+$ ]] && [ "$ONEPIPELINE_FLOW" != "$before" ]; then
            echo "flow $ONEPIPELINE_FLOW: watch it with: just watch --flow $ONEPIPELINE_FLOW" >&2
        fi
        return 0
    fi
    local here
    # llmlint: ignore[changed_behavior_has_e2e] Reachable only when this helper's own directory stops being enterable between a caller sourcing it and calling this; no journey can produce that without racing the filesystem the test itself runs on.
    if ! here=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd); then
        echo "$caller: the engine wrapper could not be resolved from this checkout; run the launch from inside a checkout, so the flow it enters is that checkout's engine's" >&2
        return 2
    fi
    # A launch flow is what holds the manager's session for the launcher's whole life, and
    # a checkout with no installed engine can neither enter one nor launch the runs it
    # would hold, so the launcher is refused here, naming the engine, rather than going on
    # in no flow with nothing holding the session.
    # shellcheck source=scripts/npm-binary.sh
    if ! . "$here/npm-binary.sh"; then
        echo "$caller: the engine resolver $here/npm-binary.sh could not be loaded; restore it from the repository or run 'just bootstrap', then retry" >&2
        return 2
    fi
    local engine
    engine=$(npm_binary onepipeline-cli onepipeline) || return 2
    if [ ! -x "$engine" ]; then
        echo "$caller: this checkout has no engine at $engine, so no launch flow can hold a session for this launch; run 'just bootstrap' to install the pinned engine, then retry" >&2
        return 2
    fi
    # The wrapper is exec'd by the shell itself, never through `env`, with `execfail` set
    # and errexit off for that one line (bash exits on a failed exec under `set -e` even
    # with `execfail`), so a wrapper the kernel refuses comes back here to be explained
    # rather than leaving only the shell's bare error; the program is otherwise replaced
    # by `onepipeline flow run`, which says on standard error what it registered and exits
    # with the launcher's status.
    shopt -s execfail
    local status errexit=
    [[ $- == *e* ]] && errexit=1
    export "$LAUNCH_FLOW_ENTERED_ENV=$$:${ONEPIPELINE_FLOW:-}"
    set +e
    exec "$here/onepipeline.sh" flow run --name "$name" -- "$program" "$@"
    status=$?
    [ -n "$errexit" ] && set -e
    unset "$LAUNCH_FLOW_ENTERED_ENV"
    echo "$caller: the engine wrapper $here/onepipeline.sh could not be run (status $status), so no flow was entered; restore it from the repository or run 'just bootstrap', then retry" >&2
    return 2
}
