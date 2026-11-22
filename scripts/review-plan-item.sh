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
# state is not judged. The keys a run writes (`onepipeline plan writeback-keys`) are left out of
# everything a judge reads and out of every attestation's digest, so a run settling, landing
# or amending a task neither reaches the judge as metadata nor voids a pass.
#
#   review-plan-item.sh <source>:<project>
#       Reviews what is not yet reviewed, as a query and a loop: the bar is the planner
#       member's fingerprint (`--bar-fingerprint`); the store's filtered check
#       (`onetaskgraph project attestation check --tasks --not-status "done" --not-status
#       cancelled --state void --state absent`) names the tasks to judge, and each is judged
#       and, on a pass, attested with `onetaskgraph task attest`, excluding the whole
#       run-written list. A task's own status decides whether it is judged — `just
#       refresh-plan` brings the board copy's statuses in first — so a `done` task is laid out
#       under `delivered/` as context and a `cancelled` (withdrawn) one is left out. Then, when
#       no task was refused and the project's own attestation is not valid, the project's
#       description is judged, its tasks beside it, and attested on a pass.
#       Exit 0 when everything is reviewed, 1 when a judge refused something (nothing is
#       recorded for it), 2 when something could not be read or judged.
#
#   review-plan-item.sh < <hook document>
#       The engine's plan-edit hook (`plan_edit_hook` in config/onepipeline.launch.yaml):
#       the document on stdin is one live edit's composed task, judged the same way and
#       recorded nothing. Exit 0 passes, 1 refuses, anything else is "could not be judged".
#       A refusal's first stdout line is one line naming the reviewed task and the
#       reviewer's first finding, and its stderr the reviewer's findings alone: the engine
#       reads the first non-empty stdout line as the refusal's summary and stderr as its full
#       reason. The judge run's event rendering, which carries its whole prompt, goes to a log
#       under the scratch directory instead, which nothing a refusal reads opens; a judge that
#       could not judge leaves its cause on stderr.
#
# The judged material is laid out under a scratch directory as the checklist's globs read it —
# `tasks/<id>.md` for a task, `projects/<id>.md` for a description, and `delivered/<id>.md`,
# which no glob reads, for a `done` task — each a front matter of the record's title,
# `depends_on` (from `onetaskgraph project graph --format json --complete`, or a hook
# document's `.node.deps`) and its metadata less the run-written keys, over its body. A judged
# task's `onepipeline.amendment` — its own, or its board copy's, read through its
# `onetaskgraph.copies` link, because the refresh copies status alone — is rendered after its
# body as the run's applied record: criteria the run added by a live edit, part of its bar,
# with no ruling reference expected and no restatement expected of the task's own text.
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

# The keys a run writes are the engine's own answer, the union of its task and project lists,
# so no copy of them is kept here to drift from the write-back that owns them. A task list
# that is not a non-empty list of non-empty names, or a project list that is not a list of
# them, is refused rather than read as nothing to leave out.
writeback_keys='select((.task | type == "array" and length > 0) and (.project | type == "array")
    and all(.task[], .project[]; type == "string" and . != "")) | .task + .project | unique | .[]'
if ! answer=$("$script_dir/onepipeline.sh" plan writeback-keys --json) ||
    ! listed_keys=$(jq -er "$writeback_keys" <<<"$answer") ||
    ! run_written=$(jq -Rsc 'split("\n") | map(select(. != ""))' <<<"$listed_keys"); then
    echo "review-plan-item: the engine printed no run-written keys as lists of non-empty names; check that 'scripts/onepipeline.sh plan writeback-keys --json' answers at the adopted pin, then retry" >&2
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
    "${judge[@]}" ${flags[@]+"${flags[@]}"} --task "$task" --dir "$scratch" --input "$input" "$@" --output text >&3
}
# Where each judge run's event rendering goes: stderr for a review, the hook's own log for a
# live edit (below).
exec 3>&2

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
    # The event rendering carries the judge's whole prompt and the run's event log, which a
    # refusal's reader cannot show in a line, so it goes to a log no refusal reads; what the
    # judge printed on stderr is kept to be read back below.
    # llmlint: ignore-block[changed_behavior_has_e2e] Each arm here fails only when the scratch directory this script made, and wrote the judged task into a moment before, refuses a further write or read, or when jq fails on a title the document check above already proved is a string; no journey reaches either without racing the filesystem under the test or doubling coreutils or jq, which this repository's journeys never double. Each arm exits 2 with the same could-not-judge meaning the journeys drive for real through a judge that cannot judge.
    exec 3>"$scratch/judge-events.log" || scratch_failed "$scratch/judge-events.log"
    # Made before the judge runs, so a scratch that cannot hold it stops here rather than
    # leaving the redirection's failure to read as the judge's refusal.
    : >"$scratch/judge.err" || scratch_failed "$scratch/judge.err"
    title=$(jq -r 'if .task.title == "" then .node.id else .task.title end' <<<"$hook" |
        tr -s '[:space:]' ' ' | sed 's/^ //; s/ $//') || read_failed "read the hook document's task title"
    status=0
    judged "$file" "$plan_reviewer" "$framing" 2>"$scratch/judge.err" || status=$?
    judge_err=$(cat -- "$scratch/judge.err") || {
        echo "review-plan-item: the plan reviewer exited $status, but what it wrote on stderr could not be read back from $scratch/judge.err, so its verdict is unknown; free space under ${TMPDIR:-/tmp} or point TMPDIR at a writable directory, then send the edit again" >&2
        exit 2
    }
    # llmlint: ignore-end[changed_behavior_has_e2e]
    if [ "$status" -ne 1 ]; then
        [ -z "$judge_err" ] || printf '%s\n' "$judge_err" >&2
        [ "$status" -eq 0 ] ||
            echo "review-plan-item: the plan reviewer could not judge '$title' (exit $status; its diagnostic is above), so nothing was judged; repair what it names, then send the edit again" >&2
        exit "$status"
    fi
    # oneagentgraph states a fail as a `<member> failed its bar (<bar>):` line followed by one
    # `  - <finding>` line per finding, a finding's further lines indented beneath it; what
    # follows that line is the findings. The first finding is the summary's, whole and on one
    # line, because its first line alone can be only a judge's tally; the engine reads a
    # bounded stdout and cuts the summary on a character boundary, so it is not cut here.
    # llmlint: ignore-block[changed_behavior_has_e2e] Reached only when sed or tr fail on text already in memory, which no journey produces without doubling coreutils, which this repository's journeys never do; the arm exits 2, the could-not-judge exit the journeys drive for real.
    unread() {
        printf '%s\n' "$judge_err" >&2
        echo "review-plan-item: the plan reviewer refused '$title', but sed or tr could not $1 (their diagnostic is above, beside all the reviewer wrote), so no summary was made; check that those tools answer, then send the edit again" >&2
        exit 2
    }
    findings=$(sed -n '/^oneagentgraph: .* failed its bar (.*):$/,$p' <<<"$judge_err" | sed '1d') ||
        unread "read its findings from what it wrote on stderr"
    first="" seen=0
    while IFS= read -r line; do
        case $line in
            "  - "*)
                [ "$seen" -eq 0 ] || break
                seen=1 first=${line#"  - "}
                ;;
            *) [ "$seen" -eq 0 ] || first+=" $line" ;;
        esac
    done <<<"$findings"
    first=$(tr -s '[:space:]' ' ' <<<"$first" | sed 's/^ //; s/ $//') ||
        unread "put its first finding on one line"
    # llmlint: ignore-end[changed_behavior_has_e2e]
    # A refusal whose findings are not in that shape still refuses, with all it said beside it.
    # llmlint: ignore-block[tool_output_is_signal] The plan-edit hook contract (onepipeline docs/contract.md, "The plan-edit hook's two streams") makes stdout's first line the refusal's one-line summary and stderr its findings alone, so a resubmission instruction has no place on either; the engine's own refusal tells the manager that none of the reply's edits were applied.
    printf "the plan reviewer refused '%s': %s\n" "$title" "${first:-its findings are on stderr}"
    printf '%s\n' "${findings:-$judge_err}" >&2
    # llmlint: ignore-end[tool_output_is_signal]
    exit 1
fi

plan=$1
described=$(native_file "$plan" "$scratch/projects") || exit 2
bar=$("${judge[@]}" --bar-fingerprint) && [ -n "$bar" ] || {
    echo "review-plan-item: could not read the planner graph's review bar (its diagnostic is above); repair graphs/planner.yaml until 'oneagentgraph run graphs/planner.yaml --judge-only --member worker --bar-fingerprint' answers, then retry" >&2
    exit 2
}
refused=0

# One listing per layout, each filtered by the store on the task's own status. A source that
# fails answers its errors in the listing and exits non-zero, so the answer is read either way.
listing() {
    local name=$1 answer
    shift
    answer=$("$store" task list --source "${plan%%:*}" --project "$plan" --all --json --no-interactive "$@") || :
    whole_listing "listing of $plan's tasks" "$answer"
    printf '%s\n' "$answer" >"$scratch/$name.json" || scratch_failed "$scratch/$name.json"
}
listing open --not-status "done" --not-status cancelled
listing delivered --status "done"
show_status=0
shown=$("$store" project show "$plan" --json --no-interactive) || show_status=$?
if [ "$show_status" -ne 0 ] || ! jq -e '(.errors // []) == [] and (.items | length) == 1' >/dev/null <<<"$shown" 2>/dev/null; then
    echo "review-plan-item: 'onetaskgraph project show $plan' exited $show_status and did not answer one project without errors, so nothing was judged: $(jq -c '.failure // {errors, items: (.items | length)}' <<<"$shown" 2>/dev/null || printf '%s' "${shown:-its diagnostic is above}"); repair what it names until 'onetaskgraph project show $plan' answers one project, then retry" >&2
    exit 2
fi
printf '%s\n' "$shown" >"$scratch/project.json" || scratch_failed "$scratch/project.json"
# Each task's dependency edges, read once from the store's own graph of the plan: a read the
# store cannot answer whole stops the review rather than judging a plan with edges missing.
graphed=0
"$store" project graph "$plan" --format json --complete --no-interactive >"$scratch/graph.json" 2>"$scratch/graph.err" ||
    graphed=$?
if [ "$graphed" -ne 0 ] ||
    # llmlint: ignore[changed_behavior_has_e2e] This document check guards an exit-0 answer that is not this plan's graph, which the pinned store never prints: reaching it would mean doubling the plan store in the planning journeys, a second fake of a published CLI to maintain for a branch that shares this refusal's one message and exit with the non-zero read `test_a_graph_the_store_cannot_answer_whole_stops_the_review_unjudged` drives for real.
    ! jq -e --arg plan "$plan" '.project == $plan and (.edges | type) == "array" and (.unanswered // []) == []
        and all(.edges[]; (.from | type == "string" and . != "") and (.to | type == "string" and . != ""))' "$scratch/graph.json" >/dev/null 2>&1; then
    reported=$({ cat "$scratch/graph.err" "$scratch/graph.json"; } 2>/dev/null | tr '\n' ' ') || reported=""
    echo "review-plan-item: the plan store's graph of $plan was not answered whole (exit $graphed), so nothing was judged: ${reported:-it printed nothing}; repair what it names, then retry" >&2
    exit 2
fi

# A record's front matter — title, `depends_on`, metadata less the run-written keys and the
# store's attestation records — over its body, then any amendment the caller passes.
# shellcheck disable=SC2016 # jq's own variables, expanded by jq rather than the shell.
compose='.id as $id | "---\n" + ({title: .item.title, depends_on: [$graph[0].edges[] | select(.to == $id) | .from],
        metadata: (.item.metadata | with_entries(select(.key | IN($keys[]) or . == "onetaskgraph.attestations" | not)))} | tojson)
    + "\n---\n" + (.item.content // "")
    + (if $amendment != ""
       then "\n\n## Amendment: criteria the run added to this task by a live edit\n\nThis is the run'"'"'s applied record, not a ruling this plan records: the run added these criteria to the task above by a live edit, and they are part of its bar as written. No ruling reference is expected of them, and their having none is no ground to refuse the task. The task'"'"'s own text is not expected to restate them:\n\n" + $amendment + "\n"
       else "" end)'
write_record() {
    local task=$1 file=$2 amendment=${3:-} composed
    composed=$(jq -r --slurpfile graph "$scratch/graph.json" --argjson keys "$run_written" \
        --arg amendment "$amendment" "$compose" <<<"$task") || read_failed "compose $file"
    printf '%s\n' "$composed" >"$file" || scratch_failed "$file"
}
lay_out() {
    local items task native file
    items=$(jq -c '.items[]' "$scratch/$1.json") || read_failed "split the listing of $plan's tasks"
    while IFS= read -r task; do
        [ -n "$task" ] || continue
        native=$(jq -r '.id' <<<"$task") || read_failed "read a listed task's id of $plan"
        file=$(native_file "$native" "$scratch/$2") || exit 2
        write_record "$task" "$file"
    done <<<"$items"
}
lay_out open tasks
lay_out delivered delivered

# A record with no amendment has none; one holding anything but text, `false` and `null`
# among them, is refused.
amendment_of='.item.metadata | if has("onepipeline.amendment") then .["onepipeline.amendment"] | if type == "string" then . else error("not text") end else "" end'
not_text() {
    echo "review-plan-item: $1 holds an amendment that is not text, so nothing more was judged; set its onepipeline.amendment to the criteria's text, then retry" >&2
    exit 2
}
# A task's copy link is an object naming its one destination's copy by a qualified id, where
# its run writes; a link of any other shape, or naming several, says no one copy holds it.
copy_of='.item.metadata | if has("onetaskgraph.copies") then .["onetaskgraph.copies"] else {} end | if type == "object" and length <= 1 and all(.[]; type == "string" and test("^[^:\\s]+:\\S+$")) then (to_entries[0].value // "") else error("malformed") end'


# The tasks to judge are the store's answer: open, and without a valid record under the bar.
checked=0
answer=$("$store" project attestation check "$plan" --kind plan-review --tasks --bar "$bar" \
    --not-status "done" --not-status cancelled --state void --state absent --json --no-interactive) || checked=$?
[ "$checked" -eq 0 ] || [ "$checked" -eq 3 ] || exit 2
ids=$(jq -r '.tasks | if type == "array" and all(.[]; .id | type == "string" and . != "") then .[].id else error("not a list of tasks") end' <<<"$answer") ||
    read_failed "read which of $plan's tasks to judge from the attestation check"
stale=()
[ -z "$ids" ] || mapfile -t stale <<<"$ids"
for id in ${stale[@]+"${stale[@]}"}; do
    task=$(jq -ce --arg id "$id" '.items[] | select(.id == $id)' "$scratch/open.json") || {
        echo "review-plan-item: the plan store's attestation check names $id, which its listing of $plan does not hold, so nothing more was judged; re-run once the store answers both alike" >&2
        exit 2
    }
    # The amendment is the task's own, or else its board copy's, read through its copy link.
    # Only text is rendered: anything else is no amendment a judge could read as criteria.
    amendment=$(jq -r "$amendment_of" <<<"$task") || not_text "$id"
    copy=$(jq -r "$copy_of" <<<"$task") || {
        echo "review-plan-item: $id holds an onetaskgraph.copies that is not an object naming one destination's copy by a qualified id, so no one board copy holds its amendment and nothing more was judged; repair its copy link with the plan store, then retry" >&2
        exit 2
    }
    if [ -z "$amendment" ] && [ -n "$copy" ]; then
        if ! copied=$("$store" task show "$copy" --json --no-interactive) ||
            ! jq -e --arg copy "$copy" '(.errors // []) == [] and (.items | length) == 1 and .items[0].id == $copy' >/dev/null <<<"$copied" 2>/dev/null; then
            echo "review-plan-item: the plan store could not show $id's board copy $copy as one record to read its amendment (its diagnostic is above), so nothing more was judged; repair what it names, then retry" >&2
            exit 2
        fi
        amendment=$(jq -r ".items[0] | $amendment_of" <<<"$copied") || not_text "$copy"
    fi
    file=$(native_file "$id" "$scratch/tasks") || exit 2
    [ -z "$amendment" ] || write_record "$task" "$file" "$amendment"
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
        # The description's `tasks` are every task but a withdrawn one, which the launch omits.
        composed=$(jq -r --slurpfile open "$scratch/open.json" --slurpfile delivered "$scratch/delivered.json" --slurpfile graph "$scratch/graph.json" --argjson keys "$run_written" '
            def authored: with_entries(select(.key | IN($keys[]) or . == "onetaskgraph.attestations" | not));
            .items[0] | "---\n" + ({id: .id, title: .item.title, metadata: (.item.metadata | authored), tasks: [($open[0].items + $delivered[0].items)[] | .id as $id | {id, title: .item.title, depends_on: [$graph[0].edges[] | select(.to == $id) | .from], metadata: (.item.metadata | authored)}]} | tojson) + "\n---\n" + (.item.content // "")' "$scratch/project.json") ||
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
