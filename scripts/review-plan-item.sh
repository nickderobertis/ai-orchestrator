#!/usr/bin/env bash
# The one review command of this host's planning road, and a thin wrapper over library
# verbs: it composes `onetaskgraph` reads and attestations with `oneagentgraph run
# graphs/planner.yaml --judge-only --member worker`, whose judge panel — the planner's
# `reviewer` and the `plan-checklist` llmlint judge (`config/plan-checklist.llmlint.yml`) —
# decides every review. Nothing here decides one.
#
#   review-plan-item.sh <source>:<project>
#       Reviews what is not yet reviewed. The bar is the planner member's fingerprint
#       (`--bar-fingerprint`); every task whose `plan-review` attestation does not check
#       `valid` under it (`onetaskgraph project attestation check --tasks`) is judged under
#       the planner persona's plan-review bar (`personas/planner.yaml`), never its own worker
#       persona's, and on a pass attested with `onetaskgraph task attest`. Then, unless the
#       project's own attestation is valid, the project's description is judged by the same
#       panel with no persona named, every task beside it for the judge to read, and
#       attested on a pass.
#       Exit 0 when everything is reviewed, 1 when a judge refused something (nothing is
#       recorded for it), 2 when something could not be read or judged.
#
#   review-plan-item.sh < <hook document>
#       The engine's plan-edit hook (`plan_edit_hook` in config/onepipeline.launch.yaml):
#       the document on stdin is one live edit's composed task, judged the same way under
#       the planner persona's plan-review bar and recorded nothing. Exit 0 passes, 1 refuses with the judge's
#       findings on stderr, anything else is "could not be judged".
#
# The judged material is written under a scratch directory laid out as the checklist's
# globs read it — `tasks/<id>.md` for a task, `projects/<id>.md` for a description — each a
# front matter of the record's title, `depends_on` and metadata (the JSON the store printed)
# over its body, because the checklist's rules read the budget records and the unit there,
# and a judge reads a plan's ordering from `depends_on`: the ids of the tasks it depends on,
# `[]` for none. A project's edges come from the store's graph of it (`onetaskgraph project
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

if ! scratch=$(mktemp -d "${TMPDIR:-/tmp}/review-plan-item.XXXXXX") ||
    ! mkdir -p "$scratch/tasks" "$scratch/projects"; then
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
    local input=$1 persona=$2
    shift 2
    local -a flags=()
    mapfile -t flags < <(persona_flags "$persona")
    "${judge[@]}" ${flags[@]+"${flags[@]}"} --dir "$scratch" --input "$input" "$@" --output text >&2
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
    echo "review-plan-item: the plan store's $1 reported errors or a further page, so nothing was judged: $(jq -c '{errors, next}' <<<"$2" 2>/dev/null || printf '%s' "$2"); repair what it names, then retry" >&2
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
    judged "$file" "$plan_reviewer" || status=$?
    exit "$status"
fi

plan=$1
# The keys a run's settlement write-back records on each record are the engine's, not
# authored content, so a review is not voided by the run it cleared settling.
settled=(--exclude onepipeline.projected_status --exclude onepipeline.settlement)
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
listing=$(jq -c '.items[]' "$scratch/tasks.json") || exit 2
tasks=()
[ -z "$listing" ] || mapfile -t tasks <<<"$listing"
for task in ${tasks[@]+"${tasks[@]}"}; do
    file=$(native_file "$(jq -r '.id' <<<"$task")" "$scratch/tasks") || exit 2
    composed=$(jq -r --slurpfile graph "$scratch/graph.json" '.id as $id | "---\n" + ({title: .item.title, depends_on: [$graph[0].edges[] | select(.to == $id) | .from], metadata: .item.metadata} | tojson) + "\n---\n" + (.item.content // "")' <<<"$task") ||
        exit 2
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
listing=$(jq -r '.tasks[] | select(.state != "valid") | .id' <<<"$answer") || exit 2
stale=()
[ -z "$listing" ] || mapfile -t stale <<<"$listing"
for id in ${stale[@]+"${stale[@]}"}; do
    # A stale id is judged only as the one listed task it names: the store's two answers
    # disagreeing is refused rather than judged under the wrong persona or file.
    if ! persona=$(jq -er --arg id "$id" '[.items[] | select(.id == $id)] | if length == 1 then .[0].item.metadata["onepipeline.persona"] // "" else error("not one") end' "$scratch/tasks.json"); then
        echo "review-plan-item: the plan store's attestation check names $id, which its listing of $plan does not hold exactly once, so nothing more was judged; re-run once the store answers both alike" >&2
        exit 2
    fi
    file=$(native_file "$id" "$scratch/tasks") || exit 2
    status=0
    judged "$file" "$plan_reviewer" || status=$?
    case "$status" in
        0) "$store" task attest "$id" --kind plan-review --by review-plan --bar "$bar" "${settled[@]}" --no-interactive >/dev/null ;;
        1) refused=1 ;;
        *) exit 2 ;;
    esac
done

project_status=0
"$store" project attestation check "$plan" --kind plan-review --bar "$bar" --json --no-interactive >/dev/null || project_status=$?
case "$project_status" in
    0) ;;
    3)
        shown=$("$store" project show "$plan" --json --no-interactive) || exit 2
        jq -e '(.errors // []) == [] and (.items | length) == 1' >/dev/null <<<"$shown" || {
            echo "review-plan-item: the plan store's answer for $plan reported errors or not one project, so it was not judged: $(jq -c '{errors, items: (.items | length)}' <<<"$shown" 2>/dev/null || printf '%s' "$shown"); repair what it names, then retry" >&2
            exit 2
        }
        composed=$(jq -r --slurpfile tasks "$scratch/tasks.json" --slurpfile graph "$scratch/graph.json" '.items[0] | "---\n" + ({id: .id, title: .item.title, metadata: .item.metadata, tasks: [$tasks[0].items[] | .id as $id | {id, title: .item.title, depends_on: [$graph[0].edges[] | select(.to == $id) | .from], metadata: .item.metadata}]} | tojson) + "\n---\n" + (.item.content // "")' <<<"$shown") ||
            exit 2
        printf '%s\n' "$composed" >"$described" || scratch_failed "$described"
        status=0
        judged "$described" "" --artifact "$scratch/tasks" || status=$?
        case "$status" in
            0) "$store" project attest "$plan" --kind plan-review --by review-plan --bar "$bar" "${settled[@]}" --no-interactive >/dev/null ;;
            1) refused=1 ;;
            *) exit 2 ;;
        esac
        ;;
    *) exit 2 ;;
esac
exit "$refused"
