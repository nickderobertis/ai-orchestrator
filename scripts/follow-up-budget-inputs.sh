#!/usr/bin/env bash
# The `batch_preparing` hook `onefollowup.yaml` names: which of a finished run's landed
# changes went over their delivery budgets, as onefollowup inputs the verifying agent
# disposes like any draft. onefollowup runs it from the launch directory with
# `{"schema_version": 1, "batch": "<id>"}` on stdin, the batch id being the finished run's;
# it prints `{"schema_version": 1, "inputs": [{"id", "source", "summary", "detail"}]}`.
#
# A composition of four verbs, deciding nothing itself:
#
#   * `onepipeline telemetry <run> --changes --json` lists the run's changes, and each one
#     carrying a landing is a subject;
#   * `onevcs resolve <repository>` names each change's publication checkout, the subject's
#     directory, whose own `budgets.yaml` states the budgets that change is held to;
#   * `onebudgetspec check --subjects - --label onepipeline --over-only --json` measures each
#     subject's delivery budgets with the run, node and runs directory a delivery budget's
#     command reads in its environment, and says which went over — that is onebudgetspec's
#     to decide;
#   * `jq` turns each over result into one `budget-overrun` input and each subject
#     onebudgetspec could not check into one `budget-unanswered` input, so none is dropped.
#
# `tests/e2e/follow_ups/test_follow_up_budget_inputs_e2e.py` drives it over a recorded run.
set -euo pipefail

checkout=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
export PATH="$checkout/node_modules/.bin:$checkout/.venv/bin:$PATH"

run=$(jq -er '.batch | strings')
runs=${ONEPIPELINE_RUNS_DIR:-$checkout/runs}

changes=$(onepipeline telemetry "$run" --changes --json | jq -c '[.changes[] | select(.landing != null)]')

checkouts='{}'
while IFS= read -r repository; do
    [ -n "$repository" ] || continue
    directory=$(onevcs resolve "$repository" | jq -er '.publication_checkout')
    checkouts=$(jq -c --arg repository "$repository" --arg directory "$directory" \
        '. + {($repository): $directory}' <<<"$checkouts")
done < <(jq -r '.[].repository' <<<"$changes" | sort -u)

subjects=$(jq -c --arg run "$run" --arg runs "$runs" --argjson checkouts "$checkouts" '{
    schema_version: 1,
    subjects: [.[] | {
        id: .node,
        directory: $checkouts[.repository],
        env: {ONEPIPELINE_RUN_ID: $run, ONEPIPELINE_NODE_ID: .node, ONEPIPELINE_RUNS_DIR: $runs},
        context: {node: .node, landing: .landing, change: .change_url, repository: .repository}
    }]
}' <<<"$changes")

# 0 is every budget within and 1 some over; 3 is a measurement that errored, which
# `--over-only` leaves out of the report it still prints. 2 is a refused invocation.
status=0
report=$(onebudgetspec check --subjects - --label onepipeline --over-only --json <<<"$subjects") || status=$?
case "$status" in
    0 | 1 | 3) ;;
    *) exit "$status" ;;
esac

jq -c '{
    schema_version: 1,
    inputs: (
        [.subjects[] | .context as $change | .report.results[] | {
            id: "budget-overrun:\($change.node):\(.id)",
            source: "budget-overrun",
            summary: "The change node \($change.node) landed as \($change.landing) went over the delivery budget \(.id): \(.actual) \(.unit) against \(.threshold)",
            detail: ({node: $change.node, landing: $change.landing, change: $change.change,
                repository: $change.repository, budget: .id, actual, threshold, unit,
                direction, detail, file} | tostring)
        }]
        + [.unanswered[] | .context as $change | {
            id: "budget-unanswered:\($change.node)",
            source: "budget-unanswered",
            summary: "The change node \($change.node) landed as \($change.landing) could not be checked against its delivery budgets",
            detail: ({node: $change.node, landing: $change.landing, change: $change.change,
                repository: $change.repository, reason} | tostring)
        }]
    )
}' <<<"$report"
