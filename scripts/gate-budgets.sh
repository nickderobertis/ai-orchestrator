# shellcheck shell=bash
# The pre-push hook's gate-time step: run the complete gate once, then hold the push to
# the root `budgets.yaml`.
#
# The gate is timed once, by `record_local_direct_gate` (`scripts/lock-timeout.sh`), which
# also records that duration against this push with the host conditions it sampled while
# the gate ran. The budgets are then checked once, with this checkout's locked
# onebudgetspec, excluding every budget labelled `onepipeline` — those are measured from
# onepipeline's telemetry and only this host's run-success hook selects them — so the
# `gate-time` budget's command reads back the one measurement already taken. Nothing here
# re-runs, re-samples or compares with an earlier push: over budget is a defect to
# optimize, on any host.
#
# Strict mode is established here rather than inherited, as the helper it sources does.
set -euo pipefail

# shellcheck source=scripts/lock-timeout.sh
# llmlint: ignore[robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at — the reason scripts/onepipeline.sh gives for its own.
. "${BASH_SOURCE[0]%/*}/lock-timeout.sh"

# Run the gate "${@:2}", check budgets file $1, and return the push's verdict.
#
# The result line is printed whatever the gate's verdict. A failing gate keeps its own
# refusal and status; a passing gate whose budget check reports a budget over refuses the
# push, saying to optimize it and pointing the manager at the host conditions printed with
# the result; a check that errored refuses the push naming the budgets file.
gate_within_budgets() {
    local budgets=$1 gate_status=0 budget_status=0 checker report over
    shift
    checker="${BASH_SOURCE[0]%/*}/../.venv/bin/onebudgetspec"
    # The push this gate is for, set without being exported: no process the gate starts —
    # a test that pushes, say — inherits a push to record for. Only the check is handed it.
    # One inherited from the environment is unset first, since a local shadowing it would
    # leave the inherited value exported to the gate.
    unset ORCHESTRATOR_GATE_PUSH
    local ORCHESTRATOR_GATE_PUSH="$EPOCHSECONDS-$$-$SRANDOM"

    record_local_direct_gate "$@" || gate_status=$?

    if [ ! -x "$checker" ]; then
        echo "pre-push: no onebudgetspec is installed at $checker, so $budgets cannot be checked; run 'just bootstrap' to install the pinned release, then push again" >&2
        return 1
    fi
    # The result lines are the library's own, printed as it wrote them once it has
    # finished, and read once more for the ids of any budget over.
    report=$(ORCHESTRATOR_GATE_PUSH=$ORCHESTRATOR_GATE_PUSH "$checker" check "$budgets" \
        --exclude-label onepipeline) || budget_status=$?
    if [ -n "$report" ]; then
        printf '%s\n' "$report"
    fi

    if [ "$gate_status" -ne 0 ]; then
        echo "pre-push: the gate failed (exit $gate_status), and its own report above is this push's refusal; fix what it reports, then push again" >&2
        return "$gate_status"
    fi
    case $budget_status in
        0) return 0 ;;
        1)
            # The ids are read from the library's own lines; where that read fails, the
            # refusal still stands and names the file, and the lines above name the budget.
            over=$(sed -n 's/^budget \([^:]*\): .* — over;.*/\1/p' <<<"$report" | paste -sd ' ') ||
                over=
            echo "pre-push: budget ${over:+${over// /, } }is over in $budgets, so this push is refused: optimize what it measures until it is within, rather than pushing again to re-measure it. The manager judges whether this host was in an extreme state from the host conditions printed with the result above: dispatches and load when it was checked, dispatches_max and load1_max at their peak while the gate ran" >&2
            return 1
            ;;
        *)
            echo "pre-push: the budget check of $budgets errored (exit $budget_status), so this push is refused as not known to be within its budgets; repair the cause reported above — a budgets file that is missing or invalid, or a measurement that could not be taken — then push again" >&2
            return 1
            ;;
    esac
}
