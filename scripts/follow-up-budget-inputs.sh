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
#     A measurement that errored is one `--over-only` leaves out of its report, saying so
#     only by exiting 3, so that exit is relayed as one more `budget-unanswered` input naming
#     every change it may concern.
#
# Every record a verb answers with is checked for the fields read from it before any of it
# is relayed, and every failure names the verb, what it was asked, and how to repair it.
#
# `tests/e2e/follow_ups/test_follow_up_budget_inputs_e2e.py` drives it over a recorded run.
set -euo pipefail

fail() {
    echo "follow-up-budget-inputs: $1" >&2
    exit 2
}

checkout=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd) ||
    fail "the checkout this hook belongs to could not be resolved; name it by its path in a readable checkout, as onefollowup.yaml does, then retry"
export PATH="$checkout/node_modules/.bin:$checkout/.venv/bin:$PATH"

#: The grammar of a run id, the same in every script that holds one to it, as
#: `tests/family_scans/test_follow_up_ownership.py` holds them.
RUN_ID='^[A-Za-z0-9_-]+$'

envelope=$(cat) ||
    fail "standard input could not be read; run this as onefollowup.yaml's hooks.batch_preparing, which hands it the batch envelope, then retry"
run=$(jq -er --arg grammar "$RUN_ID" 'select(.schema_version == 1) | .batch | select(type == "string" and test($grammar))' <<<"$envelope") ||
    fail "standard input is not onefollowup's batch_preparing envelope, {\"schema_version\": 1, \"batch\": \"<run id>\"}; run this as onefollowup.yaml's hooks.batch_preparing, which hands it that"
runs=${ONEPIPELINE_RUNS_DIR:-$checkout/runs}

telemetry=$(onepipeline telemetry "$run" --changes --json) ||
    fail "the engine could not read the changes of run '$run'; check the run and its runs root with 'just status $run', then retry"
changes=$(jq -ce '[.changes[] | select(.landing != null)]
    | if all(.[]; (.node | type) == "string" and (.repository | type) == "string" and (.landing | type) == "string")
      then . else error("a landed change without a node, repository or landing") end' <<<"$telemetry") ||
    fail "the engine's change telemetry for run '$run' is not the shape this hook reads, {\"changes\": [{\"node\", \"repository\", \"landing\"}]}; adopt the engine config/onepipeline.version names with 'just bootstrap', then retry"

repositories=$(jq -r '.[].repository' <<<"$changes" | sort -u)
checkouts='{}'
while IFS= read -r repository; do
    [ -n "$repository" ] || continue
    directory=$(onevcs resolve "$repository" | jq -er '.publication_checkout | select(type == "string" and . != "")') ||
        fail "onevcs could not resolve the publication checkout of $repository, where run '$run' landed a change; register it with 'just repos-apply', then retry"
    [ -d "$directory" ] ||
        fail "the publication checkout onevcs names for $repository, $directory, is not a directory; re-provision it with 'just bootstrap', then retry"
    checkouts=$(jq -c --arg repository "$repository" --arg directory "$directory" \
        '. + {($repository): $directory}' <<<"$checkouts")
done <<<"$repositories"

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
    *) fail "onebudgetspec refused the budget check of run '$run' (status $status), most often over a budgets.yaml it cannot read; run 'onebudgetspec check' in each publication checkout the diagnostic above names, repair the file it refuses, then rerun 'just follow-ups $run'" ;;
esac
errored=false
[ "$status" -ne 3 ] || errored=true

jq -ce --arg run "$run" --argjson errored "$errored" '
    def over: if (.id | type) == "string" and (.actual | type) == "number"
        and (.threshold | type) == "number" and (.unit | type) == "string"
        then . else error("an over result without its budget id, actual, threshold or unit") end;
    def unanswered: if (.reason | type) == "string"
        then . else error("an unanswered subject without its reason") end;
    def change: if (.node | type) == "string" and (.landing | type) == "string"
        and (.repository | type) == "string" and ((.change | type) == "string" or .change == null)
        then . else error("a subject whose context is not the change this hook handed it") end;
    {
    schema_version: 1,
    inputs: (
        [.subjects[] | (.context | change) as $change | .report.results[] | over | {
            id: "budget-overrun:\($change.node):\(.id)",
            source: "budget-overrun",
            summary: "The change node \($change.node) landed as \($change.landing) went over the delivery budget \(.id): \(.actual) \(.unit) against \(.threshold)",
            detail: ({node: $change.node, landing: $change.landing, change: $change.change,
                repository: $change.repository, budget: .id, actual, threshold, unit,
                direction, detail, file} | tostring)
        }]
        + [.unanswered[] | unanswered | (.context | change) as $change | {
            id: "budget-unanswered:\($change.node)",
            source: "budget-unanswered",
            summary: "The change node \($change.node) landed as \($change.landing) could not be checked against its delivery budgets",
            detail: ({node: $change.node, landing: $change.landing, change: $change.change,
                repository: $change.repository, reason} | tostring)
        }]
        + (if $errored then [{
            id: "budget-unanswered:errored",
            source: "budget-unanswered",
            summary: "A delivery budget measurement errored for at least one landed change of run \($run), and the over-only report does not say which",
            detail: ({run: $run, nodes: [.subjects[].context.node],
                reason: "onebudgetspec check exited 3: a measurement errored, which --over-only leaves out of its report"} | tostring)
        }] else [] end)
    )
}' <<<"$report" ||
    fail "onebudgetspec's report for run '$run' is not the shape this hook reads; adopt the release config/onebudgetspec.version names with 'just bootstrap', then retry"
