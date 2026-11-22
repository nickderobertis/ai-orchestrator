#!/usr/bin/env bash
# The launch gate `config/onepipeline.launch.yaml` registers under `checks`, and a thin
# wrapper over `onetaskgraph ... attestation check`: it answers the engine's registered-check
# contract (the loaded plan on stdin, its project as the document's `project`, `{"refusals":
# [...]}` on stdout, exit 0 when it ran) with one refusal per record whose attestation does not
# check `valid`:
#
#   * each of the plan's tasks the store's filtered check names, for `plan-review`, under the
#     planner member's current bar (`oneagentgraph run graphs/planner.yaml --judge-only
#     --member worker --bar-fingerprint`, the bar `scripts/review-plan-item.sh` attests
#     under) — `--not-status done --not-status cancelled --state void --state absent` — so a
#     task is skipped by its own status exactly as the review skips it, the withdrawn tasks
#     the launch omits among them, and a member project's task is outside this plan's
#     `--tasks`;
#   * the project, for `plan-review`, under the same bar;
#   * the plan's design document — the document of the project rendered from
#     `onepipeline:design-doc` — for `design-approval`, under the design-doc template's digest
#     as `onepipeline template resolve design-doc --json` states it, the bar `just
#     approve-design` attests under.
#
# The store decides each state (`valid`, `void` with its reason, `absent`); this script only
# relays it. Any read that fails exits non-zero, which the engine reports as a check that
# could not run.
set -euo pipefail

if ! script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P) ||
    ! root=$(dirname -- "$script_dir") || ! cd -- "$root"; then
    echo "plan-attestation-check: could not enter the checkout this script lives in; run it by its path inside a readable checkout, then retry" >&2
    exit 2
fi
store="$root/.venv/bin/onetaskgraph"
if ! input=$(cat) || ! plan=$(jq -er '.project | strings | select(test("^[^:\\s]+:\\S+$"))' <<<"$input"); then
    echo "plan-attestation-check: the engine handed this check no loaded plan naming its project on stdin; run it as a check the engine registers ('onepipeline plan check --check' or a launch config's 'checks'), then retry" >&2
    exit 2
fi
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

# A jq read of the store's answers that fails means jq is missing or broken, or the store
# answered something other than the documents its --json documents; the launch is refused
# rather than checked from a partial read.
read_failed() {
    echo "plan-attestation-check: jq could not $1 (its diagnostic is above), so the plan was not checked; check that 'jq --version' answers and that the plan store's answer is the JSON its --json documents, then retry" >&2
    exit 2
}

record() {
    printf '%s\n' "$1" >>"$checks" && return 0
    echo "plan-attestation-check: could not write to its scratch file $checks; free space under ${TMPDIR:-/tmp} or point TMPDIR at a writable directory, then retry" >&2
    exit 2
}

answer=$(relay "$store" project attestation check "$plan" --kind plan-review --tasks --bar "$bar" \
    --not-status "done" --not-status cancelled --state void --state absent)
lines=$(jq -c '.tasks | if type == "array" and all(.[]; .id | type == "string" and . != "") then .[] | {item: .id, kind, state, reason} else error("not a list of tasks") end' <<<"$answer") ||
    read_failed "read which of $plan's tasks hold no valid review from the attestation check"
record "$lines"
answer=$(relay "$store" project attestation check "$plan" --kind plan-review --bar "$bar")
lines=$(jq -c --arg plan "$plan" '{item: $plan, kind, state, reason}' <<<"$answer") ||
    read_failed "read $plan's own review from its attestation check"
record "$lines"

# Used only whole: a listing reporting errors, or holding a further page, could leave out the
# document whose approval is what this checks.
listed=$("$store" document list --source "${plan%%:*}" --project "$plan" --json --no-interactive)
jq -e '(.errors // []) == [] and .next == null' >/dev/null <<<"$listed" || {
    echo "plan-attestation-check: the plan store's listing of $plan's documents reported errors or a further page: $(jq -c '{errors, next}' <<<"$listed"); repair what it names, then retry" >&2
    exit 2
}
found=$(jq -r '.items[] | select(.item.metadata["onetaskgraph.template"].template == "onepipeline:design-doc") | .id' <<<"$listed") ||
    read_failed "find $plan's design document in its document listing"
documents=()
[ -z "$found" ] || mapfile -t documents <<<"$found"
if [ "${#documents[@]}" -eq 0 ]; then
    lines=$(jq -cn --arg plan "$plan" '{item: $plan, kind: "design-approval", state: "absent", reason: "the plan holds no design document rendered from onepipeline:design-doc"}')
    record "$lines"
fi
for document in ${documents[@]+"${documents[@]}"}; do
    answer=$(relay "$store" document attestation check "$document" --kind design-approval --bar "$template")
    lines=$(jq -c --arg document "$document" '{item: $document, kind, state, reason}' <<<"$answer") ||
        read_failed "read $document's approval from its attestation check"
    record "$lines"
done

jq -s '{refusals: [.[] | select(.state != "valid") | {node: null, field: null,
    reason: "\(.item): \(.kind) is \(.state)\(if .reason then " (\(.reason))" else "" end); \(if .kind == "plan-review" then "re-review it with just review-plan" else "re-approve it with just approve-design" end)"}]}' "$checks"
