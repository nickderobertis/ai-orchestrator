#!/usr/bin/env bash
# `just follow-ups-answer-comments [--dry-run] [--run RUN-ID | --issue QUALIFIED-ID]
# [--to SOURCE] [--since RFC3339] [--detach]`, or `just follow-ups-answer-comments --from
# <snapshot> [--detach]`: a thin script over `orchestrator/follow_up_comments.py`, whose
# docstring states the snapshot every step reads, what is read, where each comment goes, how
# settle writes and when the watermark moves. This only runs its steps — gather into a
# snapshot, claim it and write its feedback, launch, settle — with the launches between them,
# each through `scripts/follow-ups.sh --comments`. A `--dry-run` stops after the gathering,
# printing the `--from` that launches exactly what it showed; a `--from` reads no board before
# its launches. The whole of it is one flow (`onepipeline flow run`) holding the launching
# session until it exits, whose first lines name the flow and the `just watch --flow <ID>`
# that watches it.
set -euo pipefail

fail() {
    echo "follow-ups-answer-comments: $1; $2" >&2
    exit 2
}

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own checkout stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on.
checkout=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd) || fail "this recipe could not resolve the checkout it was run from" \
    "run it from a checkout, so the board and drafts root it reads are that checkout's"

python="$checkout/.venv/bin/python3"
[ -x "$python" ] || fail "this checkout has no Python interpreter at $python" \
    "provision this checkout with 'just bootstrap', then retry"

# Each helper this sources is refused by name when it cannot be read or loaded, in the words
# `scripts/plan-store.sh` refuses the credentials helper in.
load() {
    local helper="$checkout/scripts/$1" what=$2
    if [ ! -f "$helper" ] || [ ! -r "$helper" ]; then
        fail "required helper is not a readable regular file: $helper" \
            "restore it from the repository or run 'just bootstrap', then retry"
    fi
    # The helper's path is built at run time from this function's argument, which ShellCheck
    # cannot follow; each one is linted as a script of its own.
    # shellcheck source=/dev/null
    if ! . "$helper"; then
        fail "the $what at $helper is readable but could not be loaded" \
            "restore it from the repository or run 'just bootstrap', then retry"
    fi
}

# The board credential from this checkout's `.env`, as `scripts/plan-store.sh` loads it, then
# the drafts root every follow-ups launch reads, then the run-id grammar.
load credentials-env.sh "credentials helper"
export_host_credentials follow-ups-answer-comments || exit "$?"
load follow-up-env.sh "drafts-root helper"
export_follow_up_drafts follow-ups-answer-comments || exit "$?"
drafts_root=${!FOLLOW_UP_DRAFTS_ROOT_ENV}
# What this launcher used from scripts/plan-brief.sh and scripts/launch-flow.sh, which the
# planning cutover deleted, kept here verbatim until aio-followups-cutover replaces this
# script.
# llmlint: ignore[contracts_have_one_source_or_a_drift_gate] The run-id grammar this launcher took from scripts/plan-brief.sh, which the planning cutover deleted, copied as the smallest change the task ruled for each follow-ups file; aio-followups-cutover replaces both launchers, which is when the copies go.
PLAN_SAFE_RUN_ID='^[A-Za-z0-9_][A-Za-z0-9_-]*$'
LAUNCH_FLOW_ENTERED_ENV=ORCHESTRATOR_FLOW_ENTERED
# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] A verbatim copy of the one function this launcher used from scripts/launch-flow.sh, which the planning cutover deleted; the task ruled the smallest change in each follow-ups file, and aio-followups-cutover replaces both launchers, which is when the copy goes.
run_in_launch_flow() {
    local caller=${1:?run_in_launch_flow: the name of the calling launcher is required, so its diagnostics stay attributable}
    local name=${2:?run_in_launch_flow: the name of the flow is required}
    local program=${3:?run_in_launch_flow: the launcher to re-execute is required}
    shift 3
    name=${name//[^A-Za-z0-9._-]/-}
    local entered=${!LAUNCH_FLOW_ENTERED_ENV-}
    unset "$LAUNCH_FLOW_ENTERED_ENV"
    local asked=${entered%%:*}
    if [ -n "$entered" ] && { [ "$asked" = "$PPID" ] || [ "$asked" = "$$" ]; }; then
        local before=${entered#*:}
        if [[ "${ONEPIPELINE_FLOW:-}" =~ ^[A-Za-z0-9._-]+$ ]] && [ "$ONEPIPELINE_FLOW" != "$before" ]; then
            echo "flow $ONEPIPELINE_FLOW: watch it with: just watch --flow $ONEPIPELINE_FLOW" >&2
        fi
        return 0
    fi
    local here
    if ! here=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd); then
        echo "$caller: the engine wrapper could not be resolved from this checkout; run the launch from inside a checkout, so the flow it enters is that checkout's engine's" >&2
        return 2
    fi
    local engine="${here%/scripts}/node_modules/.bin/onepipeline"
    if [ ! -x "$engine" ]; then
        echo "$caller: this checkout has no engine at $engine, so no launch flow can hold a session for this launch; run 'just bootstrap' to install the pinned engine, then retry" >&2
        return 2
    fi
    shopt -s execfail
    local status errexit=
    [[ $- == *e* ]] && errexit=1
    export "$LAUNCH_FLOW_ENTERED_ENV=$$:${ONEPIPELINE_FLOW:-}"
    set +e
    exec "$here/onepipeline.sh" flow run --name "$name" -- "$program" "$@"
    status=$?
    [ -n "$errexit" ] && set -e
    unset "$LAUNCH_FLOW_ENTERED_ENV"
    echo "$caller: the engine wrapper $here/onepipeline.sh could not be run (status $status), so no flow was entered; restore it from the repository or run 'just bootstrap', then retry" >&2
    return 2
}
# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]

# The gathering, its launches, their watches and the settling check are one flow holding the
# session until it exits; each `scripts/follow-ups.sh` below runs in it rather than in one of
# its own.
run_in_launch_flow follow-ups-answer-comments follow-ups-answer-comments \
    "$checkout/scripts/follow-ups-answer-comments.sh" "$@"

scratch=$(mktemp -d) || fail "a scratch directory for the gathering could not be created" "free disk space and retry"
trap 'rm -rf "$scratch" || echo "follow-ups-answer-comments: the scratch directory $scratch could not be removed; delete it by hand" >&2' EXIT
intent="$scratch/intent.json"
plan="$scratch/plan.json"
module=("$python" -m orchestrator.follow_up_comments)

# A refusal here has launched nothing. `--from` reads nothing here: it only names the snapshot.
# llmlint: ignore[tool_output_is_signal] The report of every comment the snapshot holds, sent or left out with its reason, is this recipe's deliverable rather than noise around one: an operator reads it to know what was answered and why the rest was not.
(cd -- "$checkout" && "${module[@]}" gather --root "$drafts_root" --intent "$intent" "$@")
# The claim, the boundaries and the feedback files, from the snapshot alone; a dry run and a
# host nothing can be owed on leave nothing to claim.
# llmlint: ignore[tool_output_is_signal] Each line is one the report owes: the item the snapshot could not capture, which the task requires the launch to name, and the feedback file each run answers, which is what an operator opens to read what it was asked.
(cd -- "$checkout" && "${module[@]}" launch --root "$drafts_root" --intent "$intent" --plan "$plan")
launches=$(cd -- "$checkout" && "${module[@]}" launches --root "$drafts_root" --plan "$plan")

# Launched at once, each returning at its launch record, so no run waits on another's.
runs=()
logs=()
pids=()
attached=false
while IFS=$'\t' read -r run feedback mode to; do
    [ -n "$run" ] || continue
    log="$scratch/launch-${#runs[@]}.out"
    # From the checkout, as `just` runs it: its template layer is the working directory.
    (cd -- "$checkout" && exec scripts/follow-ups.sh "$run" --feedback "$feedback" --comments --detach ${to:+"$to"}) >"$log" &
    runs+=("$run")
    logs+=("$log")
    pids+=("$!")
    [ "$mode" = detach ] || attached=true
done <<<"$launches"

launched=()
follow_ups=()
for index in "${!runs[@]}"; do
    run=${runs[$index]}
    status=0
    wait "${pids[$index]}" || status=$?
    # An unreadable log names no run, which the check below reports with its next step.
    follow_up=$(sed -n 's/^follow-up run: //p' "${logs[$index]}" 2>/dev/null) || follow_up=""
    if [ "$status" -ne 0 ]; then
        cat -- "${logs[$index]}" >&2 || true
        echo "follow-ups-answer-comments: the launch for run $run exited $status, and its output above names why; read 'just runs' for a follow-up run of $run it may have started, then run 'just follow-ups-answer-comments --run $run' if none did" >&2
        continue
    fi
    # llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally, so nothing would ever match.
    # llmlint: ignore[changed_behavior_has_e2e] Reachable only when `scripts/follow-ups.sh --detach` exits 0 without its `follow-up run:` line, which its own journeys hold it never does; no journey can produce it without doubling that script.
    if ! [[ "$follow_up" =~ $PLAN_SAFE_RUN_ID ]]; then
        cat -- "${logs[$index]}" >&2
        echo "follow-ups-answer-comments: the launch for run $run exited 0 and named no follow-up run on the line 'follow-up run: <id>'; read what launched with 'just runs', then run 'just follow-ups-answer-comments --run $run' if nothing did" >&2
        continue
    fi
    launched+=(--launched "$run=$follow_up")
    follow_ups+=("$follow_up")
done

# Attached, every launched run is watched at once, and each settles in its own time. A
# watch that ends on anything but settlement or nothing driving is said, and `settle` still
# checks that run's account, which is what decides whether its comments were answered.
if [ "$attached" = true ]; then
    watches=()
    for follow_up in ${follow_ups[@]+"${follow_ups[@]}"}; do
        "$checkout/scripts/onepipeline.sh" watch "$follow_up" --until settled --timeout none >&2 &
        watches+=("$!")
    done
    for index in "${!watches[@]}"; do
        watched=0
        wait "${watches[$index]}" || watched=$?
        case "$watched" in
            0 | 3) ;;
            # llmlint: ignore[changed_behavior_has_e2e] Reachable only when the engine's own watch fails on a run it just launched — its store unreadable or the process killed mid-wait — which no journey can produce without doubling or signalling the engine; `settle` then checks that run's account either way, which the journeys drive.
            *) echo "follow-ups-answer-comments: the watch of run ${follow_ups[$index]} ended with status $watched before it settled; read it with 'just status ${follow_ups[$index]}'" >&2 ;;
        esac
    done
fi

# Run rather than exec'd, so the trap above still removes the scratch directory after it.
settled=0
# llmlint: ignore[tool_output_is_signal] Each run's check, every reply's URL, every board write and fallback read, every pending write needing attention, the watermark and the store read are what the task asks the report to end with, so a reader can open every answer and knows which release answered the query.
(cd -- "$checkout" && "${module[@]}" settle --root "$drafts_root" --plan "$plan" ${launched[@]+"${launched[@]}"}) || settled=$?
exit "$settled"
