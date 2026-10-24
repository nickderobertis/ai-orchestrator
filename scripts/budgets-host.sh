#!/usr/bin/env bash
# A project's `budgets-host` target: check the `host` budgets of budgets file $1, measuring
# each once, as two selections by onebudgetspec's own label flags.
#
# A budget also labelled `host-variable` measures something that varies with the load on
# this shared host — a recipe's wall clock among them — and this repository has no CI, so
# on this host it is measured and reported on every check but its over result warns rather
# than fails: a strict absolute threshold on a wall clock can only be a gate on a
# consistent system such as a CI runner. Every other `host` budget is strict, and over one
# fails the target. An errored check in either selection fails it too, so a measurement
# that could not be taken is never read as within. `scripts/gate-budgets.sh` applies the
# same label to the root file on the pre-push hook. Neither selection runs a budget
# labelled `onepipeline` or `planning`, which are measured once a change lands or a
# planning flow ends; an empty selection exits 0 and prints nothing.
set -euo pipefail
# Session setup provisions this checkout, so no `uv run` here syncs it, however it is run.
export UV_NO_SYNC=1

budgets=${1:?usage: scripts/budgets-host.sh <budgets.yaml>}
strict_status=0
variable_status=0
excluded=(--exclude-label onepipeline --exclude-label planning)

uv run onebudgetspec check "$budgets" --label host --exclude-label host-variable \
    "${excluded[@]}" || strict_status="$?"
# Only the budgets carrying both labels: `--label` keeps a budget carrying any one of
# those named, so the `host` ones are dropped here unless they are also `host-variable`,
# by selecting `host-variable` alone — a `host-variable` budget is always labelled `host`
# too, which the cached `budgets` target's `--exclude-label host` relies on.
variable_report="$(uv run onebudgetspec check "$budgets" --label host-variable \
    "${excluded[@]}")" || variable_status="$?"
if [[ -n $variable_report ]]; then
    printf '%s\n' "$variable_report"
fi

if (( strict_status == 1 )); then
    echo "budgets-host: a host budget in $budgets is over, as its result above says, so this check fails: optimize what it measures until it is within, rather than checking again to re-measure it; the host conditions printed with the result say how loaded this host was" >&2
    exit 1
fi
if (( strict_status != 0 )); then
    echo "budgets-host: the check of $budgets's host budgets errored (exit $strict_status), so they are not known to be within; repair the cause reported above — a budgets file that is missing or invalid, or a measurement that could not be taken — then check again" >&2
    exit "$strict_status"
fi
if (( variable_status > 1 )); then
    echo "budgets-host: the check of $budgets's host-variable budgets errored (exit $variable_status), so they are not known to be within; repair the cause reported above, then check again" >&2
    exit "$variable_status"
fi
if (( variable_status == 1 )); then
    over=$(sed -n 's/^budget \([^:]*\): .* — over;.*/\1/p' <<<"$variable_report" | paste -sd ' ' || true)
    echo "budgets-host: warning: budget ${over:+${over// /, } }is over in $budgets and should still be optimized; the host conditions printed with the result above say how loaded this host was while it was measured. It does not fail this check because it is labelled host-variable: a wall clock on a shared, variably loaded host is not a consistent measurement, so a strict absolute threshold on it can only be a gate on a consistent system such as a CI runner" >&2
fi
exit 0
