#!/usr/bin/env bash
# The planning flows' spike records, and a thin wrapper over `onetaskgraph task attest`,
# `project attestation check --tasks`, `project show`, `task list`, `task show`, `task
# answers` and `scripts/onepipeline.sh plan writeback-keys/start/status/watch`. Two records,
# in the store's own terms, each attested excluding every key `onepipeline plan
# writeback-keys` prints for a task, so the engine's write-back never voids one:
#
#   * `spike-definition`, on each spike of `<plan>-spikes` (every task but the template's
#     `spike-brief` member), `--by spikes-step`: `launched` when its run starts, `ran` once
#     the run settled it `done` with its definition unmoved. A spike is current when that
#     record checks `valid` under `ran`; one whose store status is `done` and that holds no
#     record ran before these records were kept, is legacy and counts as current.
#   * `spike-evidence`, on each task of `<plan>` whose `spikes` answer names a spike,
#     `--by spike-evidence`, its bar the fingerprint of those spikes' `spike-definition`
#     records: a sha256 over the sorted `<spike id>=<record content digest>` lines.
#
# Verbs, each printing one line per item it acts on:
#
#   stale <plan>      the node id of each spike that is not current (read-only)
#   run <plan>        the flows' `spikes` step: backfill legacy spikes to `ran`, then run the
#                     stale ones on a fresh continuing run, `--redo` for each settled `done`
#   rework <plan>     each task whose `spike-evidence` is absent or under another fingerprint,
#                     with the spikes behind the difference (read-only)
#   record <plan>     attest `spike-evidence` on each task `rework` lists
#   unaffected <task> --reason TEXT
#                     attest one task's `spike-evidence` at the current fingerprint, noting why
#                     its edit leaves its spikes' evidence true
#
# `run` waits for its run to end with `status`/`watch` after the attached `start`.
# INTERIM WORKAROUND (manager ruling c6.0): the spikes run holds no human node, but the
# pinned engine's attached `start` also returns when a blocking surface waits on the
# manager — a spike worker's `ORCHESTRATOR_ASK_MANAGER` question — so after it this waits on
# `onepipeline status --json` and `onepipeline watch --until settled --timeout none` until the
# run ends. It is retired by https://github.com/nickderobertis/onepipeline/issues/1643, an
# attached `start` waiting across a blocking surface, after which the wait is that alone.
#
# Exit 0 when the verb did what it says, 1 when `run` ended with a spike not recorded `ran`
# or `unaffected` refused its arguments, 2 when a read or write it needs failed.
set -euo pipefail
# A failure inside a command substitution ends the script rather than the substitution alone.
shopt -s inherit_errexit

if ! script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P) ||
    ! root=$(dirname -- "$script_dir") || ! cd -- "$root"; then
    echo "spike-records: could not enter the checkout this script lives in; run it by its path inside a readable checkout, then retry" >&2
    exit 2
fi
store="$root/.venv/bin/onetaskgraph"
engine="$script_dir/onepipeline.sh"

fail() {
    echo "spike-records: $1" >&2
    exit "${2:-2}"
}

usage() {
    fail "usage: spike-records.sh stale|run|rework|record <source>:<plan> | unaffected <source>:<task> --reason TEXT" 2
}

# What a failed store answer says, for a diagnostic: its failure message, or the answer itself.
said() {
    jq -r '.failure.message // empty' <<<"$1" 2>/dev/null | grep . || printf '%s' "${1:-nothing}"
}

# The store's JSON answer to a check that exits 3 for a record that is not valid.
checked() {
    local status=0 answer
    answer=$("$store" "$@" --json --no-interactive) || status=$?
    [ "$status" -eq 0 ] || [ "$status" -eq 3 ] ||
        fail "the plan store refused '$*': $(said "$answer"); repair what it names, then retry"
    printf '%s\n' "$answer"
}

# One check entry the store can answer: `absent` with no record, or `valid`/`void` (for a reason
# the store defines) over a record carrying the bar, content digest and time this script reads.
# The time must be the store's second-precision UTC form, because `rework` orders records by
# comparing it as text, and the digest a complete lowercase sha256, because fingerprints hash it.
record_shape='def well_formed:
    (.id | type) == "string"
    and (if .state == "absent" then .record == null
         elif .state == "valid" or (.state == "void" and (.reason | IN("content-moved", "bar-moved")))
         then (.record | type) == "object" and (.record.bar | type) == "string"
             and (.record.at | type) == "string"
             and (.record.at | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"))
             and (.record.content_digest | type) == "string"
             and (.record.content_digest | test("^sha256:[0-9a-f]{64}$"))
         else false end);'

# Whether a `--tasks` check answer holds exactly one entry, in a state the store defines, for
# each task of a listing; a missing entry would otherwise read as no record at all.
covers() {
    jq -e --slurpfile listed <(printf '%s\n' "$2") "$record_shape"'
        (.tasks | type) == "array" and all(.tasks[]; well_formed)
        and ([.tasks[].id] | sort) == ([$listed[0].items[].id] | sort)' >/dev/null <<<"$1"
}

# One `--exclude` per key the engine's write-back owns on a task, read from the engine.
excludes=()
load_excludes() {
    local answer keys key
    if ! answer=$("$engine" plan writeback-keys --json) ||
        ! keys=$(jq -er '.task | select(type == "array" and length > 0 and all(.[]; type == "string" and . != "")) | .[]' <<<"$answer"); then
        fail "the engine printed no task write-back keys as a list of non-empty names; check that 'scripts/onepipeline.sh plan writeback-keys --json' answers at the adopted pin, then retry"
    fi
    excludes=()
    while IFS= read -r key; do excludes+=(--exclude "$key"); done <<<"$keys"
}

# Whether `<plan>-spikes` exists: 0 yes, 1 no, exits 2 when the store cannot say.
has_spikes() {
    local answer status=0
    answer=$("$store" project show "$1-spikes" --json --no-interactive 2>/dev/null) || status=$?
    [ "$status" -eq 0 ] && return 0
    [ "$(jq -r '.failure.kind // empty' <<<"$answer" 2>/dev/null)" = no-such-item ] && return 1
    fail "the plan store could not show $1-spikes: $(said "$answer"); repair what it names, then retry"
}

# The spikes of `<plan>-spikes` as JSON lines `{id, node, status, state, reason, bar, digest, at}`,
# their `spike-definition` records read in one listing.
spikes_of() {
    local plan=$1 listed checks lines
    if ! listed=$("$store" task list --source "${plan%%:*}" --project "$plan-spikes" --all --json --no-interactive) ||
        ! jq -e '(.errors // []) == [] and .next == null and (.items | type) == "array"' >/dev/null <<<"$listed"; then
        fail "the plan store could not list $plan-spikes whole: $(jq -c '.failure // {errors, next}' <<<"${listed:-}" 2>/dev/null || printf '%s' "${listed:-nothing}"); repair what it names, then retry"
    fi
    jq -e 'all(.items[]; .item.metadata["onepipeline.id"] | type == "string" and . != "")' >/dev/null <<<"$listed" ||
        fail "a task of $plan-spikes carries no onepipeline.id node id; give every spike its 'spike-<topic>' id with the plan store, then retry"
    jq -e 'all(.items[]; .item.status.category | type == "string" and . != "")' >/dev/null <<<"$listed" ||
        fail "a task of $plan-spikes carries no status category, so whether it ran cannot be told; repair its status with the plan store, then retry"
    checks=$(checked project attestation check "$plan-spikes" --kind spike-definition --tasks)
    covers "$checks" "$listed" ||
        fail "the plan store's spike-definition check of $plan-spikes does not answer, in a known state, for exactly the tasks it lists; repair what the store names, then retry"
    lines=$(jq -c --slurpfile checks <(printf '%s\n' "$checks") '
        ($checks[0].tasks | map({key: .id, value: .}) | from_entries) as $records
        | .items[] | select(.item.metadata["onepipeline.id"] != "spike-brief")
        | ($records[.id] // {state: "absent"}) as $record
        | {id, node: .item.metadata["onepipeline.id"], status: .item.status.category,
           state: $record.state, reason: $record.reason, bar: $record.record.bar,
           digest: $record.record.content_digest, at: $record.record.at}' <<<"$listed") ||
        fail "jq could not read the spikes of $plan-spikes from the store's answers; check that 'jq --version' answers, then retry"
    printf '%s\n' "$lines"
}

# Whether one spike line is current: its record valid under `ran`, or legacy.
current() {
    jq -e '(.state == "valid" and .bar == "ran") or (.state == "absent" and .status == "done")' >/dev/null <<<"$1"
}

attest_definition() {
    "$store" task attest "$1" --kind spike-definition --by spikes-step --bar "$2" "${excludes[@]}" \
        "${@:3}" --no-interactive >/dev/null ||
        fail "the plan store could not attest $1's spike-definition at '$2' (its diagnostic is above); repair what it names, then rerun the flow's spikes step"
}

verb_stale() {
    local plan=$1 spikes line
    has_spikes "$plan" || return 0
    spikes=$(spikes_of "$plan")
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        # llmlint: ignore[tool_output_is_signal] This read-only verb's output is its answer, not progress: the agreed contract prints each stale spike's node id on its own line, which `just finish-plan` reads to decide whether to re-run spikes and refinalize, so a single summary line would lose the ids it acts on.
        current "$line" || jq -r '.node' <<<"$line"
    done <<<"$spikes"
}

# The ending of run ``$1``, as `status --json` states it, or nothing while it is under way.
ending_of() {
    local answer
    answer=$("$engine" status "$1" --json) ||
        fail "could not read the state of spikes run $1 (the engine's diagnostic is above); read it with 'scripts/onepipeline.sh status $1', then rerun the flow's spikes step"
    jq -ce '.ending // {} | if . == {} then . elif (.kind | type) == "string" then . else error("no kind") end' <<<"$answer" ||
        fail "spikes run $1's status names an ending with no kind; read it with 'scripts/onepipeline.sh status $1 --json', then rerun the flow's spikes step"
}

verb_run() {
    local plan=$1 line spikes run ending spike_id started settled answer
    has_spikes "$plan" || { echo "no spikes project: $plan-spikes does not exist, so nothing runs"; return 0; }
    load_excludes
    spikes=$(spikes_of "$plan")
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        jq -e '.state == "absent" and .status == "done"' >/dev/null <<<"$line" || continue
        attest_definition "$(jq -r '.id' <<<"$line")" ran --note "ran before spike records were kept"
        # llmlint: ignore[tool_output_is_signal] The verbs' agreed contract is one line per item acted on, kept as the step's log a manager reads; one summary would drop which spike was backfilled.
        echo "backfilled $(jq -r '.node' <<<"$line"): it ran before spike records were kept, so it is recorded as ran"
    done <<<"$spikes"
    local stale=() redo=() ids=()
    spikes=$(spikes_of "$plan")
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        current "$line" && continue
        spike_id=$(jq -r '.node' <<<"$line")
        stale+=("$spike_id")
        ids+=("$(jq -r '.id' <<<"$line")")
        [ "$(jq -r '.status' <<<"$line")" != "done" ] || redo+=(--redo "$spike_id")
    done <<<"$spikes"
    [ "${#stale[@]}" -gt 0 ] || { echo "no stale spike: every spike of $plan-spikes ran its current definition"; return 0; }
    local id
    for id in "${ids[@]}"; do attest_definition "$id" launched; done
    # llmlint: ignore[tool_output_is_signal] The verbs' agreed contract is one line per item acted on, kept as the step's log a manager reads; one summary would drop which spikes were launched.
    for spike_id in "${stale[@]}"; do echo "launching $spike_id"; done
    # An attached `start` exits non-zero when its run ends unsuccessfully; that run is still
    # read below, so only a launch record naming no run means it did not start.
    started=$("$engine" start "$plan-spikes" --launch-config config/onepipeline.workflows.yaml \
        --dag-graph off --label kind=spikes --branch-template "${plan#*:}/{{ node.id }}" \
        ${redo[@]+"${redo[@]}"}) || true
    run=$(tail -n 1 <<<"$started" | jq -er '.run_id | strings | select(test("^[A-Za-z0-9._-]+$"))' 2>/dev/null) ||
        fail "the spikes run of $plan-spikes did not start (the engine's diagnostic is above); repair what it names, then rerun the flow's spikes step"
    ending=$(ending_of "$run")
    while [ "$ending" = "{}" ]; do
        # A watch also ends on a surface, an elapsed bound, a run nobody drives, a settled
        # node or a changed run (exits 3-7); the status read after it decides whether the run
        # has ended. Any other exit is the watch itself failing, which no re-read repairs.
        local watched=0
        "$engine" watch "$run" --until settled --timeout none || watched=$?
        case $watched in
            0 | 3 | 4 | 5 | 6 | 7) ;;
            *) fail "watching spikes run $run failed with exit $watched (the engine's diagnostic is above); repair what it names, read the run with 'scripts/onepipeline.sh status $run', then rerun the flow's spikes step" ;;
        esac
        ending=$(ending_of "$run")
    done
    if ! answer=$("$engine" status "$run" --json) ||
        ! settled=$(jq -ce '.nodes
            | if type == "array" and all(.[]; type == "object" and (.id | type) == "string" and .id != "" and (.status | type) == "string")
              then [.[] | select(.status == "done") | .id] else error("malformed nodes") end' <<<"$answer"); then
        fail "could not read which spikes run $run settled done; read it with 'scripts/onepipeline.sh status $run --json', then rerun the flow's spikes step"
    fi
    local missed=()
    spikes=$(spikes_of "$plan")
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        spike_id=$(jq -r '.node' <<<"$line")
        printf '%s\n' "${stale[@]}" | grep -qxF -- "$spike_id" || continue
        if jq -e --arg node "$spike_id" 'index($node) != null' >/dev/null <<<"$settled" &&
            jq -e '.state == "valid" and .bar == "launched"' >/dev/null <<<"$line"; then
            attest_definition "$(jq -r '.id' <<<"$line")" ran --note "spikes run $run"
            # llmlint: ignore[tool_output_is_signal] The verbs' agreed contract is one line per item acted on, kept as the step's log a manager reads; one summary would drop which spikes reached ran.
            echo "ran $spike_id in spikes run $run"
        elif jq -e '.state == "void" and .reason == "content-moved"' >/dev/null <<<"$line"; then
            echo "edited while it ran: $spike_id's task changed during spikes run $run, so its record stays at launched; rerun the flow's spikes step to run its current definition" >&2
            missed+=("$spike_id")
        else
            echo "not run: spikes run $run did not settle $spike_id done" >&2
            missed+=("$spike_id")
        fi
    done <<<"$spikes"
    [ "$(jq -r '.kind' <<<"$ending")" = complete ] && [ "${#missed[@]}" -eq 0 ] ||
        fail "spikes run $run ended $(jq -r '.kind' <<<"$ending") with ${missed[*]:-every spike} not recorded as ran; read why with 'scripts/onepipeline.sh results $run', repair or edit the spike in $plan-spikes, then run 'just finish-plan $plan', which re-runs what is still stale" 1
}

# The spike node ids one task's `spikes` answer names, as a JSON array; `[]` for a task
# rendered with no answers stored. Exits 2 when the answers are unreadable or malformed.
spikes_named() {
    local answer status=0
    answer=$("$store" task answers "$1" --json --no-interactive 2>/dev/null) || status=$?
    if [ "$status" -ne 0 ]; then
        [ "$(jq -r '.failure.kind // empty' <<<"$answer" 2>/dev/null)" = no-stored-answers ] ||
            fail "the plan store could not print $1's template answers: $(said "$answer"); repair what it names, then retry"
        # llmlint: ignore[changed_behavior_has_e2e] A task with no stored answers was created from a body rather than a template, so it has no `spikes` answer and builds on no spike. A journey would need such a task in a plan whose flow then passes `plan check`, whose rendered-only rule refuses it; the store's `no-stored-answers` refusal for such a task was read once against a scratch store instead.
        echo "[]"
        return 0
    fi
    jq -ce '(if type != "object" then error("malformed") elif .spikes == null then [] else .spikes end)
        | if type == "array" and all(.[]; type == "object" and (.spike | type) == "string" and .spike != "")
          then [.[].spike] | unique else error("malformed") end' <<<"$answer" 2>/dev/null ||
        fail "$1's spikes answer is not a list of objects each naming a spike by a non-empty 'spike'; regenerate the task from its template with a well-formed spikes answer, then retry"
}

# The fingerprint each task naming spikes is held to, as JSON lines
# `{id, node, spikes, fingerprint, state, reason, bar, at, changed}`, from the authoring spikes'
# `spike-definition` records and each task's own `spike-evidence` record. `changed` names the
# spikes recorded since that evidence was, which are behind a difference in its fingerprint.
evidence_of() {
    local plan=$1 records="{}" listed checks line id ids spikes pairs digest
    if has_spikes "$plan"; then
        records=$(spikes_of "$plan")
        records=$(jq -sc 'map({key: .node, value: {digest: (.digest // "absent"), at}}) | from_entries' <<<"$records")
    fi
    if ! listed=$("$store" task list --source "${plan%%:*}" --project "$plan" --all --json --no-interactive) ||
        ! jq -e '(.errors // []) == [] and .next == null and (.items | type) == "array"' >/dev/null <<<"$listed"; then
        fail "the plan store could not list $plan whole: $(jq -c '.failure // {errors, next}' <<<"${listed:-}" 2>/dev/null || printf '%s' "${listed:-nothing}"); repair what it names, then retry"
    fi
    checks=$(checked project attestation check "$plan" --kind spike-evidence --tasks)
    covers "$checks" "$listed" ||
        fail "the plan store's spike-evidence check of $plan does not answer, in a known state, for exactly the tasks it lists; repair what the store names, then retry"
    ids=$(jq -r '.items[].id' <<<"$listed")
    while IFS= read -r id; do
        [ -n "$id" ] || continue
        spikes=$(spikes_named "$id")
        [ "$spikes" != "[]" ] || continue
        jq -e --argjson records "$records" 'all(.[]; . as $spike | $records | has($spike))' >/dev/null <<<"$spikes" ||
            fail "$id names a spike that is not a task of $plan-spikes ($(jq -r --argjson records "$records" '[.[] | select(. as $spike | $records | has($spike) | not)] | join(", ")' <<<"$spikes")); regenerate it from its template naming only spikes of $plan-spikes, then retry"
        pairs=$(jq -r --argjson records "$records" '.[] | "\(.)=\($records[.].digest // "absent")"' <<<"$spikes" | LC_ALL=C sort) ||
            fail "could not sort the spike records $id names (the diagnostic is above); check that 'jq --version' and 'sort --version' answer and that TMPDIR is writable, then retry"
        digest=$(printf '%s\n' "$pairs" | sha256sum) ||
            fail "sha256sum could not hash the spike records $id names (its diagnostic is above); check that 'sha256sum --version' answers, then retry"
        digest=${digest%% *}
        [[ $digest =~ ^[0-9a-f]{64}$ ]] || fail "sha256sum gave no digest for $id's spikes; check that 'sha256sum --version' answers, then retry"
        line=$(jq -c --arg id "$id" --argjson spikes "$spikes" --argjson records "$records" --arg fingerprint "sha256:$digest" \
            --slurpfile listed <(printf '%s\n' "$listed") '
            (.tasks[] | select(.id == $id)) // {state: "absent"}
            | {id: $id, node: ([$listed[0].items[] | select(.id == $id) | .item.metadata["onepipeline.id"]][0]),
               spikes: $spikes, fingerprint: $fingerprint, state, reason,
               bar: .record.bar, at: .record.at}
            # llmlint: ignore[changed_behavior_has_e2e] The fallback to every named spike answers only an evidence record newer than every spike record it differs from, which the store produces from no verb this flow runs: forging one would hand-write a record time. The selection itself is driven by the re-finish journey, whose docs task names both spikes.
            | .changed = (.at as $at | [$spikes[] | select($at == null or ($records[.].at // "") >= $at)]
                | if . == [] then $spikes else . end)' <<<"$checks")
        printf '%s\n' "$line"
    done <<<"$ids"
}

# Whether one evidence line is what `rework` lists: no record, or one under another fingerprint.
reworked() {
    jq -e '.state == "absent" or .bar != .fingerprint' >/dev/null <<<"$1"
}

verb_rework() {
    local evidence line
    evidence=$(evidence_of "$1")
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        reworked "$line" || continue
        # llmlint: ignore[tool_output_is_signal] This read-only verb's output is its answer, not progress: the agreed contract lists each task to rework with the spikes behind it, one per line, which finalize.md.j2 hands the planner as its rework list, so a single summary line would lose the tasks it names.
        jq -r '"\(.id) (\(.node)): \(if .state == "absent" then "no evidence is recorded from its spikes \(.spikes | join(", "))" else "its spikes \(.changed | join(", ")) changed since its evidence was recorded" end)"' <<<"$line"
    done <<<"$evidence"
}

attest_evidence() {
    "$store" task attest "$1" --kind spike-evidence --by spike-evidence --bar "$2" "${excludes[@]}" \
        "${@:3}" --no-interactive >/dev/null ||
        fail "the plan store could not attest $1's spike-evidence (its diagnostic is above); repair what it names, then retry"
}

verb_record() {
    local evidence line
    load_excludes
    evidence=$(evidence_of "$1")
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        reworked "$line" || continue
        attest_evidence "$(jq -r '.id' <<<"$line")" "$(jq -r '.fingerprint' <<<"$line")"
        # llmlint: ignore[tool_output_is_signal] The verbs' agreed contract is one line per item acted on, kept as the step's log a manager reads; one summary would drop which tasks were recorded.
        jq -r '"recorded \(.id) (\(.node)) against \(.spikes | join(", "))"' <<<"$line"
    done <<<"$evidence"
}

verb_unaffected() {
    local task=$1 reason=$2 shown project line status=0
    [ -n "${reason//[[:space:]]/}" ] ||
        fail "give the reason $task's edit leaves its spikes' evidence true: just spike-unaffected $task --reason \"<why>\"" 1
    shown=$("$store" task show "$task" --json --no-interactive 2>/dev/null) || status=$?
    if [ "$status" -ne 0 ]; then
        [ "$(jq -r '.failure.kind // empty' <<<"$shown" 2>/dev/null)" = no-such-item ] ||
            fail "the plan store could not show $task: $(said "$shown"); repair what it names, then retry"
        fail "the plan store holds no task $task; name an authoring task of a plan, such as authoring:<task>, then retry" 1
    fi
    jq -e --arg id "$task" '(.items | length) == 1 and .items[0].id == $id' >/dev/null 2>&1 <<<"$shown" ||
        fail "the plan store did not show $task as its one item: $(said "$shown"); repair what it names, then retry"
    project=$(jq -er '.items[0].item.project | strings | select(. != "")' <<<"$shown") ||
        fail "$task belongs to no project, so it has no plan whose spikes it could be built on; name a task of a plan, then retry" 1
    line=$(evidence_of "${task%%:*}:$project")
    line=$(jq -c --arg id "$task" 'select(.id == $id)' <<<"$line")
    [ -n "$line" ] ||
        fail "$task names no spike in its spikes answer, so it holds no spike evidence to record and the spike-evidence check never refuses it; nothing is needed" 1
    load_excludes
    attest_evidence "$task" "$(jq -r '.fingerprint' <<<"$line")" --note "$reason"
    jq -r --arg reason "$reason" '"recorded \(.id) (\(.node)) as unaffected by its edit: \($reason)"' <<<"$line"
}

[ $# -ge 2 ] || usage
verb=$1 subject=$2
shift 2
[[ $subject =~ ^[A-Za-z0-9._-]+:[^[:space:]]+$ ]] ||
    fail "'$subject' is not a qualified id '<source>:<id>'; name the plan or task as the plan store qualifies it, such as authoring:<id>, then retry" 2
case $verb in
    stale) [ $# -eq 0 ] || usage; verb_stale "$subject" ;;
    run) [ $# -eq 0 ] || usage; verb_run "$subject" ;;
    rework) [ $# -eq 0 ] || usage; verb_rework "$subject" ;;
    record) [ $# -eq 0 ] || usage; verb_record "$subject" ;;
    unaffected)
        [ $# -eq 2 ] && [ "$1" = --reason ] || usage
        verb_unaffected "$subject" "$2"
        ;;
    *) usage ;;
esac
