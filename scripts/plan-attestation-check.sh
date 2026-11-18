#!/usr/bin/env bash
# The launch gate `config/onepipeline.launch.yaml` registers under `checks`, and a thin
# wrapper over `onetaskgraph ... attestation check`: it answers the engine's registered-check
# contract (the loaded plan on stdin, `{"refusals": [...]}` on stdout, exit 0 when it ran)
# with one refusal per record whose attestation does not check `valid`:
#
#   * every task the engine loaded and the project, for `plan-review`, under the planner
#     member's current bar — `oneagentgraph run graphs/planner.yaml --judge-only --member
#     worker --bar-fingerprint`, the bar `scripts/review-plan-item.sh` attests under. A
#     withdrawn task (closed as not planned) is skipped, because the launch omits it and it
#     will never run; a task is matched to its loaded node by the node's `task_record`, whose
#     `source` only a member project's task carries. A task its run settled `done` is never
#     refused, whatever its text and whatever the store answers for its record, exactly as
#     `scripts/review-plan-item.sh` never judges it; its settlement is read from its board
#     copy — the item its `onetaskgraph.copies` entry names under the destination the
#     project's own copies name — when it has one, and from the task itself otherwise, which
#     is the board copy when the launch runs over it;
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
if ! input=$(cat) || ! jq -e '.tasks | arrays' >/dev/null <<<"$input"; then
    echo "plan-attestation-check: the engine handed this check no loaded plan it could read on stdin; run it as a check the engine registers ('onepipeline plan check --check' or a launch config's 'checks'), then retry" >&2
    exit 2
fi
# Every loaded node is a task of the store, so each names its record by a non-empty id and,
# for a member project's task only, a non-empty source; one that does not could not be
# matched to its attestation and would pass unchecked, so it is refused here instead.
# Each node is judged by its position, whatever it holds, so a node with no usable id of its
# own is still named.
if ! malformed=$(jq -c '[.tasks | to_entries[]
    | select(if (.value | type) != "object" then true
        else (.value.task_record) as $record
        | if ($record | type) != "object" then true
          else (($record.id | type) != "string" or $record.id == ""
              or ($record.source != null
                  and (($record.source | type) != "string" or $record.source == "")))
          end
        end)
    | {position: .key, node: (if (.value | type) == "object" then .value.id else null end)}]' <<<"$input"); then
    echo "plan-attestation-check: the loaded plan on stdin could not be read as a list of nodes; run this as a check the engine registers ('onepipeline plan check --check' or a launch config's 'checks'), which hands it the plan it loaded, then retry" >&2
    exit 2
fi
[ "$malformed" = "[]" ] || {
    echo "plan-attestation-check: the loaded plan's nodes at $malformed carry no task record of a non-empty string id (and a source only as a non-empty string), so their plan-review cannot be checked; launch the plan from its store so every node carries its task_record, then retry" >&2
    exit 2
}

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

answer=$(relay "$store" project attestation check "$plan" --kind plan-review --tasks --bar "$bar")
# The tasks held to their review are the plan's own tasks the engine loaded, qualified as the
# store names them. A member project's task (its record carries a `source`) is filed under
# that member project, which this `--tasks` answer does not cover, so it is outside this
# check. Every task of the plan is answered for, or one left out would pass
# unchecked: the answer is held to the plan's own whole listing, and every loaded own task
# must be in it, so a loaded task whose record names no task of the plan fails closed.
loaded=$(jq -c --arg source "${plan%%:*}" '[.tasks[].task_record | select(.source == null) | "\($source):\(.id)"]' <<<"$input") || {
    echo "plan-attestation-check: the loaded plan's task records could not be qualified under ${plan%%:*}; run this as a check the engine registers, which hands it the plan it loaded, then retry" >&2
    exit 2
}
tasks=$("$store" task list --source "${plan%%:*}" --project "$plan" --all --json --no-interactive) || {
    echo "plan-attestation-check: the plan store could not list the tasks of $plan (its diagnostic is above); repair what it names until 'onetaskgraph task list --project $plan --all' answers, then retry" >&2
    exit 2
}
# Through a file descriptor rather than an argument, which a large plan's listing outgrows.
jq -e --slurpfile listed <(printf '%s\n' "$tasks") --slurpfile loaded <(printf '%s\n' "$loaded") '$listed[0] as $tasks | (($tasks.errors // []) == []) and $tasks.next == null and (([.tasks[].id] | sort) == ([$tasks.items[].id] | sort)) and ($loaded[0] - [.tasks[].id] == [])' >/dev/null <<<"$answer" || {
    echo "plan-attestation-check: the plan store's attestation check for $plan does not answer for exactly the tasks the plan holds, the tasks the engine loaded among them; repair what the store names, then retry" >&2
    exit 2
}
# Where the run writes: the one destination the project's own `onetaskgraph.copies` names,
# read in one listing, or — for a plan with none — its own tasks.
if ! shown=$("$store" project show "$plan" --json --no-interactive) ||
    ! copies=$(jq -ce 'select((.errors // []) == [] and (.items | length) == 1)
        | (.items[0].item.metadata | if has("onetaskgraph.copies") then .["onetaskgraph.copies"] else {} end)
        | select(type == "object" and all(.[]; type == "string" and . != "")) | to_entries' <<<"$shown"); then
    echo "plan-attestation-check: the plan store could not show $plan as one project whose onetaskgraph.copies names each destination's copy by a non-empty id: $(jq -c '.failure // {errors, items: (.items | length), copies: .items[0].item.metadata["onetaskgraph.copies"]}' <<<"${shown:-}" 2>/dev/null || printf '%s' "${shown:-its diagnostic is above}"); repair what it names until 'onetaskgraph project show $plan' answers, then retry" >&2
    exit 2
fi
destination="" board='{"items": []}'
count=$(jq 'length' <<<"$copies") || read_failed "count the destinations $plan's onetaskgraph.copies names"
case $count in
    0) ;;
    1)
        if ! destination=$(jq -r '.[0].key' <<<"$copies") || ! board_project=$(jq -r '.[0].value' <<<"$copies"); then
            read_failed "read $plan's board copy from its onetaskgraph.copies"
        fi
        if ! board=$("$store" task list --source "$destination" --project "$board_project" --all --json --no-interactive) ||
            ! jq -e '(.items | type) == "array" and (.errors // []) == [] and .next == null' >/dev/null <<<"$board"; then
            echo "plan-attestation-check: the plan store could not list the board copy of $plan in $destination whole: $(jq -c '.failure // {errors, next}' <<<"${board:-}" 2>/dev/null || printf '%s' "${board:-its diagnostic is above}"); repair what it names, then retry" >&2
            exit 2
        fi
        ;;
    *)
        echo "plan-attestation-check: $plan is copied to $(jq -c 'map(.key)' <<<"$copies"), so no one board copy holds what its run wrote; launch the plan from a copy naming one destination, then retry" >&2
        exit 2
        ;;
esac
# A task's copy link names its board copy by an id the board listing holds exactly once, and
# what the run wrote there holds any settlement as an object with a text status, or its
# settlement would be read as absent rather than refused as unreadable.
problems=$(jq -r --slurpfile board <(printf '%s\n' "$board") --arg destination "$destination" '
    [.items[] | .id as $id | (.item.metadata | if has("onetaskgraph.copies") then .["onetaskgraph.copies"] else {} end) as $copies
     | if ($copies | type) != "object" then "\($id) holds an onetaskgraph.copies that is not an object"
       elif $destination != "" and $copies[$destination] != null
            and (($copies[$destination] | type) != "string"
                 or ([$board[0].items[] | select(.id == $copies[$destination])] | length) != 1)
       then "\($id) names its copy in \($destination) as \($copies[$destination] | tojson), which the board listing does not hold exactly once"
       elif ((if $destination != "" and $copies[$destination] != null
              then ([$board[0].items[] | select(.id == $copies[$destination]) | .item.metadata][0] // {})
              else .item.metadata end)["onepipeline.settlement"]
             | . != null and (type != "object" or (.status | type) != "string"))
       then "\($id) holds an onepipeline.settlement that is not an object with a text status"
       else empty end] | join("; ")' <<<"$tasks") || read_failed "read where $plan's run wrote from its listings"
[ -z "$problems" ] || {
    echo "plan-attestation-check: where $plan's run wrote cannot be read: $problems; repair it with the plan store, then retry" >&2
    exit 2
}
# A task settled `done` is delivered work, never refused whatever the store answers for its
# record, as the review never judges it; every other loaded task is relayed as the store
# answers it.
# shellcheck disable=SC2016 # jq's own variables, expanded by jq rather than the shell.
lines=$(jq -c --slurpfile loaded <(printf '%s\n' "$loaded") --slurpfile listed <(printf '%s\n' "$tasks") \
    --slurpfile board <(printf '%s\n' "$board") --arg destination "$destination" '
    ($listed[0].items | map({key: .id, value: (
        ((.item.metadata | if has("onetaskgraph.copies") then .["onetaskgraph.copies"] else {} end) | if type == "object" then .[$destination] else null end) as $copy
        | (if $destination != "" and $copy != null
           then ([$board[0].items[] | select(.id == $copy) | .item.metadata][0] // {})
           else .item.metadata end)
        | .["onepipeline.settlement"].status == "done")}) | from_entries) as $done
    | .tasks[] | select((.id | IN($loaded[0][])) and $done[.id] != true)
    | {item: .id, kind, state, reason}' <<<"$answer") ||
    read_failed "read which of $plan's tasks hold a valid review from the attestation check"
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
