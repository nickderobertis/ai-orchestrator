#!/usr/bin/env bash
# The launch gate `config/onepipeline.launch.yaml` registers under `checks`, and a thin
# wrapper over `onetaskgraph ... attestation check`: it answers the engine's registered-check
# contract (the loaded plan on stdin, `{"refusals": [...]}` on stdout, exit 0 when it ran)
# with one refusal per record whose attestation does not check `valid`:
#
#   * every task and the project, for `plan-review`, under the planner member's current bar —
#     `oneagentgraph run graphs/planner.yaml --judge-only --member worker --bar-fingerprint`,
#     the bar `scripts/review-plan-item.sh` attests under;
#   * the plan's design document — the document of the project rendered from
#     `onepipeline:design-doc` — for `design-approval`, under the design-doc template's digest
#     as `onepipeline template resolve design-doc --json` states it, the bar `just
#     approve-design` attests under.
#
# The store decides each state (`valid`, `void` with its reason, `absent`); this script only
# relays it. The engine's check document names no project, so the plan is read from
# `ORCHESTRATOR_PLAN`, which `scripts/orchestrate.sh` exports from the project it launches.
# Any read that fails exits non-zero, which the engine reports as a check that could not run.
set -euo pipefail

if ! script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P) ||
    ! root=$(dirname -- "$script_dir") || ! cd -- "$root"; then
    echo "plan-attestation-check: could not enter the checkout this script lives in; run it by its path inside a readable checkout, then retry" >&2
    exit 2
fi
store="$root/.venv/bin/onetaskgraph"
cat >/dev/null

plan=${ORCHESTRATOR_PLAN:?ORCHESTRATOR_PLAN names no plan: launch it with just orchestrate <source>:<project>}
bar=$("$root/.venv/bin/oneagentgraph" run graphs/planner.yaml --judge-only --member worker --bar-fingerprint) &&
    [ -n "$bar" ] || {
    echo "plan-attestation-check: could not read the planner graph's review bar (its diagnostic is above); repair graphs/planner.yaml until 'oneagentgraph run graphs/planner.yaml --judge-only --member worker --bar-fingerprint' answers, then retry" >&2
    exit 2
}
template=$("$script_dir/onepipeline.sh" template resolve design-doc --json |
    jq -er '.digest | select(type == "string" and . != "")') || {
    echo "plan-attestation-check: the engine resolved no design-doc template digest to check approvals under; repair the template until 'scripts/onepipeline.sh template check design-doc' passes, then retry" >&2
    exit 2
}

if ! checks=$(mktemp "${TMPDIR:-/tmp}/plan-attestation-check.XXXXXX"); then
    echo "plan-attestation-check: could not make a scratch file under ${TMPDIR:-/tmp} to gather the attestation checks in; free space there or point TMPDIR at a writable directory, then retry" >&2
    exit 2
fi
trap 'rm -f -- "$checks" || echo "plan-attestation-check: could not remove its scratch file $checks; remove it by hand" >&2' EXIT

relay() {
    local status=0 answer
    answer=$("$@" --json --no-interactive) || status=$?
    [ "$status" -eq 0 ] || [ "$status" -eq 3 ] || return "$status"
    printf '%s\n' "$answer"
}

record() {
    printf '%s\n' "$1" >>"$checks" && return 0
    echo "plan-attestation-check: could not write to its scratch file $checks; free space under ${TMPDIR:-/tmp} or point TMPDIR at a writable directory, then retry" >&2
    exit 2
}

answer=$(relay "$store" project attestation check "$plan" --kind plan-review --tasks --bar "$bar")
# Every task of the plan is answered for, or one left out would pass unchecked: the check's
# tasks are held to the plan's own whole listing.
tasks=$("$store" task list --source "${plan%%:*}" --project "$plan" --all --json --no-interactive)
# Through a file descriptor rather than an argument, which a large plan's listing outgrows.
jq -e --slurpfile listed <(printf '%s\n' "$tasks") '$listed[0] as $tasks | (($tasks.errors // []) == []) and $tasks.next == null and (([.tasks[].id] | sort) == ([$tasks.items[].id] | sort))' >/dev/null <<<"$answer" || {
    echo "plan-attestation-check: the plan store's attestation check for $plan does not answer for exactly the tasks the plan holds; repair what the store names, then retry" >&2
    exit 2
}
lines=$(jq -c '.tasks[] | {item: .id, kind, state, reason}' <<<"$answer")
record "$lines"
answer=$(relay "$store" project attestation check "$plan" --kind plan-review --bar "$bar")
lines=$(jq -c --arg plan "$plan" '{item: $plan, kind, state, reason}' <<<"$answer")
record "$lines"

# Used only whole: a listing reporting errors, or holding a further page, could leave out the
# document whose approval is what this checks.
listed=$("$store" document list --source "${plan%%:*}" --project "$plan" --json --no-interactive)
jq -e '(.errors // []) == [] and .next == null' >/dev/null <<<"$listed" || {
    echo "plan-attestation-check: the plan store's listing of $plan's documents reported errors or a further page: $(jq -c '{errors, next}' <<<"$listed"); repair what it names, then retry" >&2
    exit 2
}
found=$(jq -r '.items[] | select(.item.metadata["onetaskgraph.template"].template == "onepipeline:design-doc") | .id' <<<"$listed")
documents=()
[ -z "$found" ] || mapfile -t documents <<<"$found"
if [ "${#documents[@]}" -eq 0 ]; then
    lines=$(jq -cn --arg plan "$plan" '{item: $plan, kind: "design-approval", state: "absent", reason: "the plan holds no design document rendered from onepipeline:design-doc"}')
    record "$lines"
fi
for document in ${documents[@]+"${documents[@]}"}; do
    answer=$(relay "$store" document attestation check "$document" --kind design-approval --bar "$template")
    lines=$(jq -c --arg document "$document" '{item: $document, kind, state, reason}' <<<"$answer")
    record "$lines"
done

jq -s '{refusals: [.[] | select(.state != "valid") | {node: null, field: null,
    reason: "\(.item): \(.kind) is \(.state)\(if .reason then " (\(.reason))" else "" end); \(if .kind == "plan-review" then "re-review it with just review-plan" else "re-approve it with just approve-design" end)"}]}' "$checks"
