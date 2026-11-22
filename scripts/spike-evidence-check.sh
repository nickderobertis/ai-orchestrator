#!/usr/bin/env bash
# The backstop for stale spike evidence: a check meeting the engine's registered-check
# contract as `scripts/plan-attestation-check.sh` does (the loaded plan on stdin, its project
# as the document's `project`, `{"refusals": [...]}` on stdout, exit 0 when it ran), and a
# thin wrapper over `onetaskgraph project attestation check --tasks` and `task answers`.
# `config/onepipeline.launch.yaml` registers it beside the launch gate, and both planning
# flows' `check` step register it over the plan they finish.
#
# It reads every task's `spike-evidence` record (`scripts/spike-records.sh` writes them) in
# one listing, and refuses each task the engine loaded whose record is void as
# `content-moved`: a task built on spikes that changed after their evidence was recorded,
# while the spikes it names did not. The refusal names the task, those spikes and both next
# steps. An absent or `valid` record passes; a record whose bar alone moved is the
# `spike-evidence` step's to re-record, never this check's to refuse.
#
# On a board copy the task and plan named are their authoring origins (`onetaskgraph.origin`),
# which the next steps edit and the next copy carries, and a task's spikes are read from that
# origin's stored answers when the copy holds none. Any read that fails exits non-zero, which
# the engine reports as a check that could not run.
set -euo pipefail
shopt -s inherit_errexit

if ! script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P) ||
    ! root=$(dirname -- "$script_dir") || ! cd -- "$root"; then
    echo "spike-evidence-check: could not enter the checkout this script lives in; run it by its path inside a readable checkout, then retry" >&2
    exit 2
fi
store="$root/.venv/bin/onetaskgraph"

fail() {
    echo "spike-evidence-check: $1" >&2
    exit 2
}

# What a failed store answer says, for a diagnostic: its failure message, or the answer itself.
said() {
    jq -r '.failure.message // empty' <<<"$1" 2>/dev/null | grep . || printf '%s' "${1:-nothing}"
}

# Every loaded node is a task of the store, so each must name its record by a non-empty id,
# and a member project's by a non-empty source; one that does not would go unchecked.
# llmlint: ignore[changed_behavior_has_e2e] A member project's task (its record names a `source`) is filed under that project, which this plan's `--tasks` answer does not cover, exactly as `scripts/plan-attestation-check.sh` leaves it out; no planning flow writes a spike-built task into a member project, so a journey would need a plan shape nothing here produces. A void record fed once as the plan's own task was refused and as a member's was passed over.
if ! input=$(cat) || ! loaded=$(jq -ce '
    if (.tasks | type) != "array" then error("no tasks")
    elif all(.tasks[]; type == "object" and (.task_record | type) == "object"
        and (.task_record.id | type) == "string" and .task_record.id != ""
        and (.task_record.source == null or ((.task_record.source | type) == "string" and .task_record.source != "")))
    then [.tasks[].task_record | select(.source == null) | .id]
    else error("malformed node") end' <<<"$input" 2>/dev/null); then
    fail "the engine handed this check no loaded plan whose every node carries a task record of a non-empty string id; run it as a check the engine registers ('onepipeline plan check --check' or a launch config's 'checks'), which hands it the plan it loaded, then retry"
fi
plan=$(jq -r '.project // ""' <<<"$input")
[[ $plan =~ ^[A-Za-z0-9._-]+:[^[:space:]]+$ ]] ||
    fail "the loaded plan names its project as '$plan', not a qualified plan id '<source>:<project>'; run this as a check the engine registers, which names the project it loaded, then retry"

answer=0
checks=$("$store" project attestation check "$plan" --kind spike-evidence --tasks --json --no-interactive) || answer=$?
[ "$answer" -eq 0 ] || [ "$answer" -eq 3 ] ||
    fail "the plan store could not check the spike-evidence records of $plan: $(said "$checks"); repair what it names, then retry"
if ! shown=$("$store" project show "$plan" --json --no-interactive) ||
    ! origin=$(jq -er --arg plan "$plan" 'select((.items | length) == 1 and .items[0].id == $plan) | .items[0].item.metadata["onetaskgraph.origin"] // $plan | strings | select(test("^[^:\\s]+:\\S+$"))' <<<"$shown"); then
    fail "the plan store did not show $plan as its one item (its diagnostic is above); repair what it names, then retry"
fi
# Every task the engine loaded is answered for, in a state the store defines, or a task the
# answer left out, or one in a state this does not know, would pass unchecked.
jq -e --arg source "${plan%%:*}" --argjson loaded "$loaded" '
    (.tasks | type) == "array"
    and all(.tasks[]; (.id | type) == "string" and (.id | startswith($source + ":"))
        and ((.state | IN("valid", "absent")) or (.state == "void" and (.reason | IN("content-moved", "bar-moved")))))
    and ([.tasks[].id | sub("^[^:]*:"; "")] as $answered | all($loaded[]; . as $id | $answered | index($id) != null))' \
    >/dev/null <<<"$checks" ||
    fail "the plan store's spike-evidence check of $plan does not answer, in a known state and under its own source, for every task the plan loaded; repair what the store names, then retry"
void=$(jq -c --arg source "${plan%%:*}" --argjson loaded "$loaded" \
    '.tasks[] | select(.state == "void" and .reason == "content-moved")
     | select((.id | sub("^[^:]*:"; "")) as $native | $loaded | index($native) != null) | .id' <<<"$checks") ||
    fail "jq could not read which of $plan's tasks hold void spike evidence; check that 'jq --version' answers, then retry"

# The spike node ids one task's `spikes` answer names, from its own stored answers or, on a
# copy that stores none, from its origin's.
spikes_named() {
    local id=$1 origin=$2 answers status=0
    answers=$("$store" task answers "$id" --json --no-interactive 2>/dev/null) || status=$?
    # Only a copy that stores no answers reads its origin's; any other failure is the store's.
    if [ "$status" -ne 0 ] && [ "$id" != "$origin" ] &&
        [ "$(jq -r '.failure.kind // empty' <<<"$answers" 2>/dev/null)" = no-stored-answers ]; then
        status=0
        answers=$("$store" task answers "$origin" --json --no-interactive 2>/dev/null) || status=$?
    fi
    [ "$status" -eq 0 ] ||
        fail "the plan store could not print the template answers of $id or its origin $origin: $(said "$answers"); repair what it names, then retry"
    jq -ce '(if type != "object" then error("malformed") elif .spikes == null then [] else .spikes end)
        | if type == "array" and all(.[]; type == "object" and (.spike | type) == "string" and .spike != "")
          then [.[].spike] | unique else error("malformed") end' <<<"$answers" 2>/dev/null ||
        fail "$origin's spikes answer is not a list of objects each naming a spike by a non-empty 'spike'; regenerate the task from its template with a well-formed spikes answer, then retry"
}

refusals=()
while IFS= read -r id; do
    [ -n "$id" ] || continue
    id=$(jq -r . <<<"$id")
    if ! shown=$("$store" task show "$id" --json --no-interactive 2>/dev/null) ||
        ! task=$(jq -er --arg id "$id" 'select((.items | length) == 1 and .items[0].id == $id) | .items[0].item.metadata["onetaskgraph.origin"] // $id | strings | select(test("^[^:\\s]+:\\S+$"))' <<<"$shown"); then
        fail "the plan store did not show $id as its one item: $(said "${shown:-}"); repair what it names, then retry"
    fi
    spikes=$(spikes_named "$id" "$task")
    refusal=$(jq -cn --arg task "$task" --arg plan "$origin" --argjson spikes "$spikes" '{node: null, field: null,
        reason: "\($task) changed after the evidence of the spikes it builds on, \($spikes | join(", ")), was recorded, and those spikes did not: its spike-evidence is void (content-moved). Either edit \($spikes | join(", ")) in \($plan)-spikes to match and run just finish-plan \($plan), which re-runs them; or, when the edit leaves their evidence true, run just spike-unaffected \($task) --reason \"<why>\""}')
    refusals+=("$refusal")
done <<<"$void"

printf '%s\n' ${refusals[@]+"${refusals[@]}"} | jq -sc '{refusals: .}'
