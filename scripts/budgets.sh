#!/usr/bin/env bash
# A project's cached `budgets` target: check the budgets of budgets file $1 that are not
# labelled `host`, measuring each once, as two selections by onebudgetspec's own label flags.
#
# A budget also labelled `accepted-overrun` is over its bar by a cost the user accepted
# while an upstream fix lands, and its registration names that fix's follow-up and says
# why it is over: it is measured and reported on every check, but its over result warns
# rather than fails, until the follow-up lands and the label comes off. Every other budget
# is strict, and over one fails the target. An errored check in either selection fails it
# too, so a measurement that could not be taken is never read as within. Neither selection
# runs a budget labelled `host` — the uncached `budgets-host` target (`scripts/budgets-host.sh`)
# checks those — or `onepipeline` or `planning`, which are measured once a change lands or
# a planning flow ends; an empty selection exits 0 and prints nothing.
set -euo pipefail
# Session setup provisions this checkout, so no `uv run` here syncs it, however it is run.
export UV_NO_SYNC=1

budgets=${1:?usage: scripts/budgets.sh <budgets.yaml>}
strict_status=0
accepted_status=0
excluded=(--exclude-label host --exclude-label onepipeline --exclude-label planning)

uv run onebudgetspec check "$budgets" --exclude-label accepted-overrun \
    "${excluded[@]}" || strict_status="$?"
accepted_report="$(uv run onebudgetspec check "$budgets" --label accepted-overrun \
    "${excluded[@]}")" || accepted_status="$?"
if [[ -n $accepted_report ]]; then
    printf '%s\n' "$accepted_report"
fi

if (( strict_status == 1 )); then
    echo "budgets: a budget in $budgets is over, as its result above says, so this check fails: optimize what it measures until it is within" >&2
    exit 1
fi
if (( strict_status != 0 )); then
    echo "budgets: the check of $budgets errored (exit $strict_status), so its budgets are not known to be within; repair the cause reported above — a budgets file that is missing or invalid, or a measurement that could not be taken — then check again" >&2
    exit "$strict_status"
fi
if (( accepted_status > 1 )); then
    echo "budgets: the check of $budgets's accepted-overrun budgets errored (exit $accepted_status), so they are not known to be within; repair the cause reported above, then check again" >&2
    exit "$accepted_status"
fi
if (( accepted_status == 1 )); then
    over=$(sed -n 's/^budget \([^:]*\): .* — over;.*/\1/p' <<<"$accepted_report" | paste -sd ' ' || true)
    echo "budgets: warning: budget ${over:+${over// /, } }is over in $budgets and is still a defect to fix. It does not fail this check because it is labelled accepted-overrun: the user accepted this overrun while the upstream fix its registration names lands, after which the label comes off and the budget is strict again" >&2
fi
exit 0
