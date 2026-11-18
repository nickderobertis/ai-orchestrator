#!/usr/bin/env bash
# The one review command of this host's planning road, and a thin wrapper over library
# verbs: it composes `onetaskgraph` reads and attestations with `oneagentgraph run
# graphs/planner.yaml --judge-only --member worker`, whose judge panel — the planner's
# `reviewer` and the `plan-checklist` llmlint judge (`config/plan-checklist.llmlint.yml`) —
# decides every review. Nothing here decides one.
#
# Every judged input — each task, the project, a live edit — is judged under the planner
# persona's plan-review bar (`personas/planner.yaml`), never a task's own worker persona's
# and never the base config's completion clause alone, with a `--task` saying it is a plan
# document under review: its criteria state what its nodes will deliver, and its delivery
# state is not judged. The keys a run writes (`config/run-written-keys.txt`) are left out of
# everything a judge reads and out of every attestation's digest, so a run settling, landing
# or amending a task neither reaches the judge as metadata nor voids a pass.
#
#   review-plan-item.sh <source>:<project>
#       Reviews what is not yet reviewed. The bar is the planner member's fingerprint
#       (`--bar-fingerprint`); every task whose `plan-review` attestation does not check
#       `valid` under it (`onetaskgraph project attestation check --tasks`) is judged and,
#       on a pass, attested with `onetaskgraph task attest`, excluding the whole run-written
#       list — except a task its run settled `done`, which is delivered work and is never
#       judged, whatever its text and whatever the store answers for its record, and a
#       withdrawn task, which is never judged, attested or shown to any judge, because the
#       launch omits it by the same status and `scripts/plan-attestation-check.sh` therefore
#       never holds it to a review. Then, when
#       no task was refused and the project's own attestation is not valid, the project's
#       description is judged, every task beside it for the judge to read — a task settled
#       `done` under `delivered/` rather than `tasks/`, as context the description is never
#       refused for — and attested on a pass.
#       Exit 0 when everything is reviewed, 1 when a judge refused something (nothing is
#       recorded for it), 2 when something could not be read or judged.
#
#   review-plan-item.sh < <hook document>
#       The engine's plan-edit hook (`plan_edit_hook` in config/onepipeline.launch.yaml):
#       the document on stdin is one live edit's composed task, judged the same way and
#       recorded nothing. Exit 0 passes, 1 refuses with the judge's findings on stderr,
#       anything else is "could not be judged".
#
# What a run writes is read from the copy the run writes: a task's board copy — the item
# its `onetaskgraph.copies` entry names under the destination the project's own
# `onetaskgraph.copies` names — when it has one, and the task itself otherwise, which is a
# plan launched from its own authoring copy. That copy's status says whether the task is
# withdrawn — the store's `cancelled`, which it answers for a board issue closed as not
# planned — its settlement whether it is settled `done`, and its amendment is rendered
# verbatim into its judged file as a section after its body, presented as the run's applied
# record: criteria the run added to the task by a live edit, part of its bar, and not a ruling the plan records, so no ruling reference is
# expected of it. The task's own text is never expected to restate it. The judged material
# is written under a scratch directory laid out as the checklist's globs read it —
# `tasks/<id>.md` for a task, `projects/<id>.md` for a description, and `delivered/<id>.md`,
# which no glob reads, for a task settled `done` — each a front matter of
# the record's title, `depends_on` and its metadata less the run-written keys, over its
# body, because the checklist's rules read the budget records and the unit there, and a
# judge reads a plan's ordering from `depends_on`: the ids of the tasks it depends on, `[]`
# for none. A project's edges come from the store's graph of it (`onetaskgraph project
# graph --format json --complete`), read whole or not judged at all; a hook document's from
# the dependencies its node names (`.node.deps`).
set -euo pipefail

if ! script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P) ||
    ! root=$(dirname -- "$script_dir") || ! cd -- "$root"; then
    echo "review-plan-item: could not enter the checkout this script lives in; run it by its path inside a readable checkout, then retry" >&2
    exit 2
fi
store="$root/.venv/bin/onetaskgraph"
graph="$root/.venv/bin/oneagentgraph"
judge=("$graph" run graphs/planner.yaml --judge-only --member worker)
# Every task is judged under the planner persona's plan-review bar ("a principal engineer
# reviewing a plan before anyone builds from it"), never under the task's own worker persona:
# a worker persona's bar is a completion check, and a task description is never finished
# work, so judging under it refuses tasks as "no commits, no tests" regardless of content.
plan_reviewer=../personas/planner.yaml
# What every judge is told it reads: the things the plan-review bar needs said of a plan
# partway through its delivery, so a node not yet landed is never read as work missing and a
# task its run amended is never refused for not restating the amendment, and one task is
# never refused for a sibling's defect, which the sibling's own judgment refuses.
framing="This is a plan document — a project's description or one of its tasks, or a live edit to one — reviewed under the plan-review bar before or during its delivery. It is not finished work. Its acceptance criteria state what its nodes will deliver, not work this review requires to be done. Its delivery state — task statuses, settlements, landings, change requests, open issues — is not judged. A task's own text is not expected to restate its amendment, the criteria its run added by a live edit, or to be rewritten to match it; where the two differ, the amendment binds. When the material under review is one task, judge that task alone: the plan's other files are context, and a defect in one of them is not this task's."
# What the description's judge is told besides: which tasks beside it are delivered work.
delivered_framing="The tasks under delivered/ beside this description were settled done by their run: they are delivered work, shown as context only. Never refuse the description for the content of one of them."

keys_file="$root/config/run-written-keys.txt"
if ! listed_keys=$(sed -e 's/#.*//' -e 's/[[:space:]]//g' -e '/^$/d' "$keys_file") || [ -z "$listed_keys" ] ||
    ! run_written=$(jq -Rsc 'split("\n") | map(select(. != ""))' <<<"$listed_keys"); then
    echo "review-plan-item: could not read the run-written keys from $keys_file; restore it from the repository, then retry" >&2
    exit 2
fi
mapfile -t keys <<<"$listed_keys"
# Every attestation leaves the whole list out of its digest, so the run writing any of them
# after a pass leaves that pass valid.
excludes=()
for key in "${keys[@]}"; do
    excludes+=(--exclude "$key")
done

if ! scratch=$(mktemp -d "${TMPDIR:-/tmp}/review-plan-item.XXXXXX") ||
    ! mkdir -p "$scratch/tasks" "$scratch/delivered" "$scratch/projects"; then
    echo "review-plan-item: could not make a scratch directory under ${TMPDIR:-/tmp} to lay the judged records out in; free space there or point TMPDIR at a writable directory, then retry" >&2
    exit 2
fi
trap 'rm -rf -- "$scratch" || echo "review-plan-item: could not remove its scratch directory $scratch; remove it by hand" >&2' EXIT
# The judges' harness configs repoint their runtime directory under a dispatch's scratch
# (`oneharness.dispatch.toml`'s `env_from`); outside a dispatch — a manager's `just
# review-plan`, a planning run's command step — that is this run's own scratch.
export ONEPIPELINE_NODE_SCRATCH_DIR="${ONEPIPELINE_NODE_SCRATCH_DIR:-$scratch}"
# The reviewer runs `oneharness.judge.toml`, whose run-level grant names
# `$ORCHESTRATOR_DECLINE_RULING`; oneharness refuses a run with it unset, so this sets it as
# every launcher does. Outside a run the wrapper has no `ONEPIPELINE_RUN_ID` and refuses.
# shellcheck source=scripts/decline-ruling-env.sh
if ! source "$script_dir/decline-ruling-env.sh"; then
    echo "review-plan-item: could not load $script_dir/decline-ruling-env.sh, which sets the decline variable the reviewer's judge config names; restore it from the repository, then retry" >&2
    exit 2
fi
export_decline_ruling review-plan-item || exit "$?"

# A node persona that is a path is written relative to the graphs directory, as every plan
# names one (`../personas/<name>.yaml`); a bare name is a built-in role.
persona_flags() {
    case "$1" in
        "") ;;
        */*) printf '%s\n' --persona "graphs/$1" ;;
        *) printf '%s\n' --persona "$1" ;;
    esac
}

judged() {
    local input=$1 persona=$2 task=$3
    shift 3
    local -a flags=()
    mapfile -t flags < <(persona_flags "$persona")
    "${judge[@]}" ${flags[@]+"${flags[@]}"} --task "$task" --dir "$scratch" --input "$input" "$@" --output text >&2
}

# A jq read of the store's own answers, saved under the scratch directory, that fails means
# jq is missing or broken, or the store answered something other than the documents it
# documents; either way nothing is judged from a partial read.
read_failed() {
    echo "review-plan-item: jq could not $1 (its diagnostic is above), so nothing was judged; check that 'jq --version' answers and that the plan store's answer is the JSON its --json documents, then retry" >&2
    exit 2
}

scratch_failed() {
    echo "review-plan-item: could not write $1 under its scratch directory; free space under ${TMPDIR:-/tmp} or point TMPDIR at a writable directory, then retry" >&2
    exit 2
}

# A store listing is used only whole: one reporting errors, or holding a further page, would
# have the review judge, and attest, part of a plan.
whole_listing() {
    jq -e '(.items | type) == "array" and (.errors // []) == [] and .next == null' >/dev/null <<<"$2" 2>/dev/null &&
        return 0
    echo "review-plan-item: the plan store's $1 failed or reported errors or a further page, so nothing was judged: $(jq -c '.failure // {errors, next}' <<<"$2" 2>/dev/null || printf '%s' "${2:-its diagnostic is above}"); repair what it names, then retry" >&2
    exit 2
}

# A record's native id names its judged file, kept to one path component and one file per
# id: `_` is encoded `_5f` and a store's `<project>/<task>` separator `_2f`, so no two ids
# share a name, and anything else that could leave the directory — a dot-only id, a
# character outside a record id's — is refused before anything is written.
native_file() {
    local native=${1#*:}
    native=${native//_/_5f}
    native=${native//\//_2f}
    if [[ ! "$native" =~ ^[A-Za-z0-9_@+.-]+$ || "$native" =~ ^\.+$ ]]; then
        echo "review-plan-item: the store answered the id '$1', which is not one path component, so it cannot be judged; report it to the plan store" >&2
        exit 2
    fi
    printf '%s/%s.md\n' "$2" "$native"
}

if [ "$#" -eq 0 ]; then
    hook=$(cat)
    jq -e '(.node.id | type) == "string" and .node.id != "" and (.task.title | type) == "string" and (.task.body | type) == "string" and (.node.deps | if . == null then [] else . end | type == "array" and all(type == "string" and . != ""))' >/dev/null <<<"$hook" 2>/dev/null || {
        echo "review-plan-item: the plan-edit hook's document names no node id, task title and task body, or names its node's dependencies as something other than a list of ids, so nothing was judged; report it to the engine" >&2
        exit 2
    }
    id=$(jq -r '.node.id' <<<"$hook")
    file=$(native_file "$id" "$scratch/tasks") || exit 2
    composed=$(jq -r '"---\n" + ({title: .task.title, depends_on: (.node.deps | if . == null then [] else . end)} | tojson) + "\n---\n" + .task.body' <<<"$hook") || exit 2
    printf '%s\n' "$composed" >"$file" || scratch_failed "$file"
    status=0
    judged "$file" "$plan_reviewer" "$framing" || status=$?
    exit "$status"
fi

plan=$1
described=$(native_file "$plan" "$scratch/projects") || exit 2
bar=$("${judge[@]}" --bar-fingerprint) && [ -n "$bar" ] || {
    echo "review-plan-item: could not read the planner graph's review bar (its diagnostic is above); repair graphs/planner.yaml until 'oneagentgraph run graphs/planner.yaml --judge-only --member worker --bar-fingerprint' answers, then retry" >&2
    exit 2
}
refused=0

# Every task, composed once: the judge reads each beside the project, and judges each
# stale one on its own.
# A source that fails answers its errors in the listing and exits non-zero, so the answer is
# read either way and refused for what it says.
listed=$("$store" task list --source "${plan%%:*}" --project "$plan" --all --json --no-interactive) || :
whole_listing "listing of $plan's tasks" "$listed"
printf '%s\n' "$listed" >"$scratch/tasks.json" || scratch_failed "$scratch/tasks.json"
# A failed show answers its structured failure on stdout (its prose on stderr, above), so the
# diagnostic carries the exit status and that failure rather than only an item count.
show_status=0
shown=$("$store" project show "$plan" --json --no-interactive) || show_status=$?
if [ "$show_status" -ne 0 ] || ! jq -e '(.errors // []) == [] and (.items | length) == 1' >/dev/null <<<"$shown" 2>/dev/null; then
    echo "review-plan-item: 'onetaskgraph project show $plan' exited $show_status and did not answer one project without errors, so nothing was judged: $(jq -c '.failure // {errors, items: (.items | length)}' <<<"$shown" 2>/dev/null || printf '%s' "${shown:-its diagnostic is above}"); repair what it names until 'onetaskgraph project show $plan' answers one project, then retry" >&2
    exit 2
fi
printf '%s\n' "$shown" >"$scratch/project.json" || scratch_failed "$scratch/project.json"

# The board copy is the one destination the project's own `onetaskgraph.copies` names, read
# in one listing; a plan with none is its run's own record, read from its own tasks.
if ! copies=$(jq -c '(.items[0].item.metadata | if has("onetaskgraph.copies") then .["onetaskgraph.copies"] else {} end) | if type == "object" and all(.[]; type == "string" and . != "") then to_entries else error("not an object of ids") end' "$scratch/project.json"); then
    echo "review-plan-item: $plan's onetaskgraph.copies is not an object naming each destination's copy by a non-empty id, so where its run writes cannot be read; repair it with the plan store, then retry" >&2
    exit 2
fi
destination=""
count=$(jq 'length' <<<"$copies") || read_failed "count the destinations $plan's onetaskgraph.copies names"
case $count in
    0) printf '%s\n' '{"items": []}' >"$scratch/board.json" || scratch_failed "$scratch/board.json" ;;
    1)
        if ! destination=$(jq -r '.[0].key' <<<"$copies") || ! board=$(jq -r '.[0].value' <<<"$copies"); then
            read_failed "read $plan's board copy from its onetaskgraph.copies"
        fi
        listed=$("$store" task list --source "$destination" --project "$board" --all --json --no-interactive) || :
        whole_listing "listing of $plan's board copy $board" "$listed"
        printf '%s\n' "$listed" >"$scratch/board.json" || scratch_failed "$scratch/board.json"
        ;;
    *)
        echo "review-plan-item: $plan is copied to $(jq -c 'map(.key)' <<<"$copies"), so no one board copy holds what its run wrote; review the plan from a copy naming one destination, then retry" >&2
        exit 2
        ;;
esac

# The copy of one listed task its run reads: its board copy when it has one, itself
# otherwise; what the run wrote there; whether that settles it `done`; whether that copy is
# withdrawn — the store's `cancelled`, which it answers for a board issue closed as not
# planned and which the engine's launch loader omits; and a record's authored metadata, less
# the run-written keys and the store's own attestation records, whose `excludes` name those
# keys and which are this review's bookkeeping rather than the plan.
# shellcheck disable=SC2016 # jq's own variables, expanded by jq rather than the shell.
run_state='def run_copy($board; $destination):
        ((.item.metadata | if has("onetaskgraph.copies") then .["onetaskgraph.copies"] else {} end) | if type == "object" then .[$destination] else null end) as $copy
        | if $destination != "" and $copy != null
          then ([$board.items[] | select(.id == $copy)][0] // {})
          else . end;
    def run_written($board; $destination): run_copy($board; $destination) | .item.metadata // {};
    def settled_done: .["onepipeline.settlement"].status == "done";
    def withdrawn($board; $destination): run_copy($board; $destination) | .item.status.category == "cancelled";
    def authored($keys): with_entries(select(.key | IN($keys[]) or . == "onetaskgraph.attestations" | not));'

# A task's copy link names its board copy by an id the board listing holds exactly once, and
# that copy answers its status category as text, and what the run wrote there holds its
# amendment as text and any settlement as an object with a text status, or the run's
# withdrawal, settlement or binding ruling would be read as absent.
problems=$(jq -r --slurpfile board "$scratch/board.json" --arg destination "$destination" "$run_state"'
    [.items[] | .id as $id | (.item.metadata | if has("onetaskgraph.copies") then .["onetaskgraph.copies"] else {} end) as $copies
     | if ($copies | type) != "object" then "\($id) holds an onetaskgraph.copies that is not an object"
       elif $destination != "" and $copies[$destination] != null
            and (($copies[$destination] | type) != "string"
                 or ([$board[0].items[] | select(.id == $copies[$destination])] | length) != 1)
       then "\($id) names its copy in \($destination) as \($copies[$destination] | tojson), which the board listing does not hold exactly once"
       # llmlint: ignore[changed_behavior_has_e2e] The pinned store answers every listed item a text status category, unknown for a status it cannot place, so no plan store a journey drives reaches this refusal: reaching it would mean doubling the plan store, a second fake of a published CLI to maintain for a guard sharing this check its one message and exit.
       elif (run_copy($board[0]; $destination).item.status.category | type) != "string"
       then "\($id) has a copy where its run reads it whose status category is not text"
       elif (run_written($board[0]; $destination)["onepipeline.amendment"] | . != null and type != "string")
       then "\($id) holds an amendment that is not text"
       elif (run_written($board[0]; $destination)["onepipeline.settlement"] | . != null and (type != "object" or (.status | type) != "string"))
       then "\($id) holds an onepipeline.settlement that is not an object with a text status"
       else empty end] | join("; ")' "$scratch/tasks.json") || read_failed "read where $plan's run wrote from its listings"
[ -z "$problems" ] || {
    echo "review-plan-item: what $plan's run wrote cannot be read, so nothing was judged: $problems; repair it with the plan store, then retry" >&2
    exit 2
}
# Each task's dependency edges, read once from the store's own graph of the plan: a read the
# store cannot answer whole — a failure, a private source unanswered, or a document that is
# not this plan's graph — stops the review rather than judging a plan with edges missing.
graphed=0
"$store" project graph "$plan" --format json --complete --no-interactive >"$scratch/graph.json" 2>"$scratch/graph.err" ||
    graphed=$?
if [ "$graphed" -ne 0 ] ||
    # llmlint: ignore[changed_behavior_has_e2e] This document check guards an exit-0 answer that is not this plan's graph, which the pinned store never prints: reaching it would mean doubling the plan store in the planning journeys, a second fake of a published CLI to maintain for a branch that shares this refusal's one message and exit with the non-zero read `test_a_graph_the_store_cannot_answer_whole_stops_the_review_unjudged` drives for real.
    ! jq -e --arg plan "$plan" '.project == $plan and (.nodes | type) == "array" and (.edges | type) == "array" and all(.edges[]; (.from | type) == "string" and (.to | type) == "string") and (.unanswered // []) == []' "$scratch/graph.json" >/dev/null 2>&1; then
    reported=$({ cat "$scratch/graph.err" "$scratch/graph.json"; } 2>/dev/null | tr '\n' ' ') || reported=""
    echo "review-plan-item: the plan store's graph of $plan was not answered whole (exit $graphed), so nothing was judged: ${reported:-it printed nothing}; repair what it names, then retry" >&2
    exit 2
fi
# Read whole before it is split, so a listing jq cannot read stops the review rather than
# leaving it judging fewer tasks than the plan holds.
listing=$(jq -c '.items[]' "$scratch/tasks.json") || read_failed "split the listing of $plan's tasks"
tasks=()
[ -z "$listing" ] || mapfile -t tasks <<<"$listing"
# A withdrawn task will never run, so it is laid out nowhere and never judged; a task settled
# `done` is delivered work, laid out under `delivered/` and never judged on its own; every
# other under `tasks/`.
delivered=()
withdrawn=()
for task in ${tasks[@]+"${tasks[@]}"}; do
    native=$(jq -r '.id' <<<"$task") || read_failed "read a listed task's id of $plan"
    run_status=$(jq -r --slurpfile board "$scratch/board.json" --arg destination "$destination" "$run_state"'
        if withdrawn($board[0]; $destination) then "withdrawn"
        elif run_written($board[0]; $destination) | settled_done then "done"
        else "open" end' <<<"$task") ||
        read_failed "read whether $native is withdrawn or settled done"
    if [ "$run_status" = "withdrawn" ]; then
        withdrawn+=("$native")
        continue
    elif [ "$run_status" = "done" ]; then
        delivered+=("$native")
        file=$(native_file "$native" "$scratch/delivered") || exit 2
    else
        file=$(native_file "$native" "$scratch/tasks") || exit 2
    fi
    composed=$(jq -r --slurpfile graph "$scratch/graph.json" --slurpfile board "$scratch/board.json" --arg destination "$destination" --argjson keys "$run_written" "$run_state"'
        .id as $id | (run_written($board[0]; $destination)["onepipeline.amendment"]) as $amendment
        | "---\n" + ({title: .item.title, depends_on: [$graph[0].edges[] | select(.to == $id) | .from], metadata: (.item.metadata | authored($keys))} | tojson) + "\n---\n" + (.item.content // "")
        + (if ($amendment | type) == "string" and $amendment != ""
           then "\n\n## Amendment: criteria the run added to this task by a live edit\n\nThis is the run'"'"'s applied record, not a ruling this plan records: the run added these criteria to the task above by a live edit, and they are part of its bar as written. No ruling reference is expected of them, and their having none is no ground to refuse the task. The task'"'"'s own text is not expected to restate them:\n\n" + $amendment + "\n"
           else "" end)' <<<"$task") ||
        read_failed "compose $native's judged file"
    printf '%s\n' "$composed" >"$file" || scratch_failed "$file"
done

checked=0
answer=$("$store" project attestation check "$plan" --kind plan-review --tasks --bar "$bar" --json --no-interactive) ||
    checked=$?
[ "$checked" -eq 0 ] || [ "$checked" -eq 3 ] || exit 2
# The check answers for exactly the tasks the listing holds, or what it left out would go
# unjudged; the two disagreeing is refused.
if ! jq -e --slurpfile listed "$scratch/tasks.json" '([.tasks[].id] | sort) == ([$listed[0].items[].id] | sort)' >/dev/null <<<"$answer" 2>/dev/null; then
    echo "review-plan-item: the plan store's attestation check for $plan does not answer for exactly the tasks its listing holds, so nothing was judged; re-run once the store answers both alike" >&2
    exit 2
fi
# A task withdrawn or settled `done` is never judged, whatever the store answers for its
# record; every other task is judged when its record is not valid.
unjudged=$(printf '%s\n' ${delivered[@]+"${delivered[@]}"} ${withdrawn[@]+"${withdrawn[@]}"} | jq -Rsc 'split("\n") | map(select(. != ""))') ||
    read_failed "list which of $plan's tasks are withdrawn or settled done"
withdrawn_ids=$(printf '%s\n' ${withdrawn[@]+"${withdrawn[@]}"} | jq -Rsc 'split("\n") | map(select(. != ""))') ||
    read_failed "list which of $plan's tasks are withdrawn"
listing=$(jq -r --argjson unjudged "$unjudged" '
    .tasks[] | select(.state != "valid" and (.id | IN($unjudged[]) | not)) | .id' <<<"$answer") ||
    read_failed "select which of $plan's tasks to judge from the attestation check"
stale=()
[ -z "$listing" ] || mapfile -t stale <<<"$listing"
for id in ${stale[@]+"${stale[@]}"}; do
    # A stale id is judged only as the one listed task it names: the store's two answers
    # disagreeing is refused rather than judged from the wrong file.
    if ! jq -e --arg id "$id" '[.items[] | select(.id == $id)] | length == 1' "$scratch/tasks.json" >/dev/null; then
        echo "review-plan-item: the plan store's attestation check names $id, which its listing of $plan does not hold exactly once, so nothing more was judged; re-run once the store answers both alike" >&2
        exit 2
    fi
    file=$(native_file "$id" "$scratch/tasks") || exit 2
    status=0
    judged "$file" "$plan_reviewer" "$framing" || status=$?
    case "$status" in
        0)
            "$store" task attest "$id" --kind plan-review --by review-plan --bar "$bar" "${excludes[@]}" --no-interactive >/dev/null || {
                echo "review-plan-item: $id passed its review, but the plan store did not record its plan-review attestation (its diagnostic is above), so nothing more was judged; repair what it names, then re-run 'just review-plan $plan', which judges $id again" >&2
                exit 2
            }
            ;;
        1) refused=1 ;;
        *) exit 2 ;;
    esac
done

# The project's review covers its tasks beside it, so it is neither judged nor attested while
# one of them is refused.
if [ "$refused" -ne 0 ]; then
    echo "review-plan-item: $plan's description was not judged, because a task of it was refused; repair what its judge named, then retry" >&2
    exit 1
fi
project_status=0
"$store" project attestation check "$plan" --kind plan-review --bar "$bar" --json --no-interactive >/dev/null || project_status=$?
case "$project_status" in
    0) ;;
    3)
        # A withdrawn task is left out of the description's `tasks`, as it is out of every file
        # beside it: the launch omits it.
        composed=$(jq -r --slurpfile tasks "$scratch/tasks.json" --slurpfile graph "$scratch/graph.json" --argjson keys "$run_written" --argjson withdrawn "$withdrawn_ids" "$run_state"'
            .items[0] | "---\n" + ({id: .id, title: .item.title, metadata: (.item.metadata | authored($keys)), tasks: [$tasks[0].items[] | select(.id | IN($withdrawn[]) | not) | .id as $id | {id, title: .item.title, depends_on: [$graph[0].edges[] | select(.to == $id) | .from], metadata: (.item.metadata | authored($keys))}]} | tojson) + "\n---\n" + (.item.content // "")' "$scratch/project.json") ||
            read_failed "compose $plan's judged description"
        printf '%s\n' "$composed" >"$described" || scratch_failed "$described"
        status=0
        judged "$described" "$plan_reviewer" "$framing $delivered_framing" --artifact "$scratch/tasks" --artifact "$scratch/delivered" || status=$?
        case "$status" in
            0)
                "$store" project attest "$plan" --kind plan-review --by review-plan --bar "$bar" "${excludes[@]}" --no-interactive >/dev/null || {
                    echo "review-plan-item: $plan's description passed its review, but the plan store did not record its plan-review attestation (its diagnostic is above); repair what it names, then re-run 'just review-plan $plan', which judges the description again" >&2
                    exit 2
                }
                ;;
            1) refused=1 ;;
            *) exit 2 ;;
        esac
        ;;
    *) exit 2 ;;
esac
exit "$refused"
