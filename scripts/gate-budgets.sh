# shellcheck shell=bash
# The pre-push hook's gate-time step: run the complete gate once, then hold the push to
# the root `budgets.yaml`.
#
# The gate is timed once, by `record_local_direct_gate` (`scripts/lock-timeout.sh`), which
# also records that duration against this push with the host conditions it sampled while
# the gate ran. The budgets are then checked with this checkout's locked onebudgetspec,
# excluding every budget labelled `onepipeline` — those are measured from onepipeline's
# telemetry and only this host's run-success hook selects them — so the `gate-time`
# budget's command reads back the one measurement already taken. Nothing here re-runs,
# re-samples or compares with an earlier push.
#
# The rest of the file is checked as two selections, by the library's own label flags, so
# every budget is measured once and the verdict follows the selection rather than a read
# of which budget a line names. A budget labelled `host-variable` measures something that
# varies with the load on this shared host — the gate's wall clock among them — and over
# one is warned about rather than refused: it is still a defect to optimize, but on a
# shared, variably loaded host it is not a consistent measurement, so a strict absolute
# threshold on it can only be a gate on a consistent system such as a CI runner. Every
# other budget is strict, and over one refuses the push. A failed gate, and a check that
# errored in either selection — a `gate-time` that could not be measured included — still
# refuse. Neither selection is timed in `gate-time`, since both run after the gate. An
# empty selection exits 0, prints nothing and samples no host conditions, which is the
# strict one today; a strict root budget added later makes every push sample the
# conditions a second time, so add one deliberately.
#
# Strict mode is established here rather than inherited, as the helper it sources does.
set -euo pipefail

# shellcheck source=scripts/lock-timeout.sh
# llmlint: ignore[robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at — the reason scripts/onepipeline.sh gives for its own.
. "${BASH_SOURCE[0]%/*}/lock-timeout.sh"

# Run the gate "${@:2}", check budgets file $1, and return the push's verdict.
#
# Both selections' result lines are printed whatever the gate's verdict, with their host
# conditions, then the verdict. A failing gate keeps its own refusal and status; a check
# that errored in either selection refuses the push naming the budgets file; a strict
# budget over refuses the push, saying to optimize it and pointing the manager at the host
# conditions; a `host-variable` budget over with nothing else wrong admits the push with a
# warning saying the same and why it does not block.
gate_within_budgets() {
    local budgets="$1" gate_status=0 variable_status=0 strict_status=0 checker
    local variable_report strict_report over
    shift
    checker="${BASH_SOURCE[0]%/*}/../.venv/bin/onebudgetspec"
    # The push this gate is for, set without being exported: no process the gate starts —
    # a test that pushes, say — inherits a push to record for. Only the check is handed it.
    # One inherited from the environment is unset first, since a local shadowing it would
    # leave the inherited value exported to the gate.
    unset ORCHESTRATOR_GATE_PUSH
    local ORCHESTRATOR_GATE_PUSH="$EPOCHSECONDS-$$-$SRANDOM"

    record_local_direct_gate "$@" || gate_status="$?"

    if [ ! -x "$checker" ]; then
        echo "pre-push: no onebudgetspec is installed at $checker, so $budgets cannot be checked; run 'just bootstrap' to install the pinned release, then push again" >&2
        return 1
    fi
    # The result lines are the library's own, printed as it wrote them once it has
    # finished, and read once more for the ids of any budget over.
    variable_report="$(ORCHESTRATOR_GATE_PUSH="$ORCHESTRATOR_GATE_PUSH" "$checker" check "$budgets" \
        --label host-variable --exclude-label onepipeline)" || variable_status="$?"
    strict_report="$(ORCHESTRATOR_GATE_PUSH="$ORCHESTRATOR_GATE_PUSH" "$checker" check "$budgets" \
        --exclude-label onepipeline --exclude-label host-variable)" || strict_status="$?"
    if [ -n "$variable_report" ]; then
        printf '%s\n' "$variable_report"
    fi
    if [ -n "$strict_report" ]; then
        printf '%s\n' "$strict_report"
    fi

    if [ "$gate_status" -ne 0 ]; then
        echo "pre-push: the gate failed (exit $gate_status), and its own report above is this push's refusal; fix what it reports, then push again" >&2
        return "$gate_status"
    fi
    if [ "$variable_status" -gt 1 ] || [ "$strict_status" -gt 1 ]; then
        echo "pre-push: the budget check of $budgets errored (exit $variable_status for its host-variable budgets, $strict_status for the rest), so this push is refused as not known to be within its budgets; repair the cause reported above — a budgets file that is missing or invalid, or a measurement that could not be taken — then push again" >&2
        return 1
    fi
    if [ "$strict_status" -eq 1 ]; then
        over="$(_budgets_over "$strict_report")"
        echo "pre-push: budget ${over:+${over// /, } }is over in $budgets, so this push is refused: optimize what it measures until it is within, rather than pushing again to re-measure it. The manager judges whether this host was in an extreme state from the host conditions printed with the result above: dispatches and load when it was checked, dispatches_max and load1_max at their peak while the gate ran" >&2
        return 1
    fi
    if [ "$variable_status" -eq 1 ]; then
        over="$(_budgets_over "$variable_report")"
        echo "pre-push: warning: budget ${over:+${over// /, } }is over in $budgets and should still be optimized; the host conditions printed with the result above say how loaded this host was while it was measured. It does not block this push because it is labelled host-variable: gate wall clock on a shared, variably loaded host is not a consistent measurement, so a strict absolute threshold on it can only be a gate on a consistent system such as a CI runner" >&2
    fi
    return 0
}

# The ids of the budgets the library's result lines $1 report over, space-separated. Where
# that read fails the verdict still stands and names the file, and the lines name the budget.
_budgets_over() {
    sed -n 's/^budget \([^:]*\): .* — over;.*/\1/p' <<<"$1" | paste -sd ' ' || true
}
