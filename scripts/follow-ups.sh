#!/usr/bin/env bash
# Dispatch the follow-up agent over one run's drafted follow-ups: `just follow-ups <run-id>
# [--feedback FILE] [--detach] [--to SOURCE]`.
#
# During a run every party drafts what it noticed outside its own scope as an unverified
# follow-up in the run's draft project (`orchestrator/follow_up_drafts.py`). This recipe
# launches the one agent that reads them afterwards: it verifies each draft against the
# trees it names, groups what stands by root cause, writes one verified ticket per root
# cause beside the drafts, and copies each onto the `followups` board — commenting on
# another run's open issue for the same root cause instead of duplicating it. The ticket's
# shape and who owns what on the board are `orchestrator/follow_up_tickets.py`'s, which
# renders both into the task this composes from `config/follow-up-task.md`.
#
# `scripts/plan.sh` is the precedent for everything about the launch, and each departure
# from it is named:
#
#   * **One direct node, `follow-ups`, in project `authoring:<run-id>-follow-ups`**, carrying
#     `PLAN_DIRECT_PLACEMENT_NOTE`: a direct node works in the launching checkout and
#     commits nothing, which is exactly what this agent's deliverable is — records under the
#     gitignored draft root and items on a board, never a branch.
#   * **It names the per-node agent graph `graphs/follow-up.yaml`**, a single-sided member
#     with no judge, so the task composed here is the only copy of its instructions it is
#     given. The node still names `../personas/follow-up.yaml`, because the engine refuses a
#     direct agent node that names no persona; the graph's member layers none.
#   * **Its run id is guaranteed free rather than refused when taken.** A planning flow's
#     name is the manager's to choose, so `scripts/plan.sh` refuses a taken one; this run's
#     name is derived from the run it follows up, and a second pass over the same run — a
#     re-dispatch with feedback, or a run the success hook reaches twice — must launch
#     rather than fail. So it takes the first of `<run-id>-follow-ups`,
#     `<run-id>-follow-ups-2`, `-3`, … that no run root holds, which is the same "a run root
#     exists" question `plan_run_is_free` asks. The project id stays `<run-id>-follow-ups`
#     whichever run id launched it, so a re-dispatch rewrites the one project.
#   * **It is exempt from design-document approval by a stamp bounded to its one node**, of
#     the `follow-ups` kind `orchestrator/design_approval.py` reads: there is no plan for a
#     person to read this project as, and a project that grows a second node, or whose stamp
#     names a node it does not hold, is gated like any other.
#
# Four refusals and endings come before anything is written, each told apart:
#
#   * a run something is still driving is refused (exit 2), because its drafts may still be
#     growing — `onepipeline watch --timeout 0` reads the run once and answers whether a
#     driver is live, which is the engine's own answer rather than a guess from its ledger;
#   * a run whose draft project holds no drafts and no tickets ends at one line naming it
#     and launches nothing (exit 0);
#   * `--to` names the board the tickets are copied onto, for a journey standing a local
#     store in for the live board, and is refused unless it names a configured source;
#   * `--feedback FILE` puts that file's text into the composed task verbatim, under a
#     heading of its own, for a re-dispatch over the same run's drafts, tickets and board.
#
# **Attached by default, and then the tickets are checked.** With no judge, what holds the
# agent's output to its shape is this check and the manager's reading, so once an attached
# run settles every ticket left under `tasks/<run-id>/tickets/` is validated and each one
# that fails is named. **`--detach` returns once the launch record exists**, printing
# exactly `follow-up run: <id>` and `watch it with: just watch <id>` on stdout, because the
# engine's success hook calls it that way under a deadline an attached run would outlive.
# The launch goes through `scripts/onepipeline.sh`, which keeps an exported launcher
# identity, so the follow-up run is owned by whichever session launched this. Nothing in
# this repository calls this recipe after another launch returns; that is the hook's.
set -euo pipefail

#: The project this launch writes says what it is: the one-node project a follow-ups launch
#: writes, bounded to the node below. `orchestrator/design_approval.py` is the one reader.
FOLLOW_UPS_METADATA='{"orchestrator.plan-kind": {"kind": "follow-ups", "nodes": ["follow-ups"]}}'

#: The node, its persona ref (resolved against `graphs/`), and the graph it runs under
#: (resolved against the launch directory, which is this checkout).
NODE_ID="follow-ups"
PERSONA="../personas/follow-up.yaml"
GRAPH="graphs/follow-up.yaml"

#: The task template, relative to this checkout.
TEMPLATE="config/follow-up-task.md"

#: The source the project is written into, and what its native id and run id append.
PLAN_SOURCE="authoring"
SUFFIX="-follow-ups"

#: What `onepipeline watch --timeout 0` exits with, read once, for a run nothing drives —
#: settled, or nothing driving it — and for one something still drives: a blocking surface
#: waiting, or the zero-second wait elapsing on a live run. `scripts/watch-run.sh` states
#: the whole vocabulary.
WATCH_SETTLED=0
WATCH_NOTHING_DRIVING=3
WATCH_SURFACE_WAITING=4
WATCH_ELAPSED=5

#: What one count the inventory answers with is: a non-negative whole number.
COUNT='^[0-9]+$'

#: Writes the one-node project. The composed task is embedded verbatim, and the placement
#: note is appended after it rather than woven in.
PROJECT_PROGRAM='
import json, pathlib, sys

from orchestrator.project_store import write_plan_project

(root, native, name, run, node_id, persona, graph, task_file, direct_note, metadata) = sys.argv[1:11]
task = pathlib.Path(task_file).read_text(encoding="utf-8").rstrip()
plan = {
    "schema_version": 3,
    "goal": {"text": f"Verify the follow-up drafts run {run} left, and put the verified tickets on the board"},
    "name": name,
    "tasks": [
        {
            "id": node_id,
            "persona": persona,
            "agent_graph": graph,
            "task": task + "\n\n" + direct_note.strip() + "\n",
        }
    ],
}
write_plan_project(pathlib.Path(root), plan, native_id=native, project_metadata=json.loads(metadata))
'

usage="just follow-ups <run-id> [--feedback FILE] [--detach] [--to SOURCE]"

fail() {
    echo "follow-ups: $1; $2" >&2
    exit 2
}

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on.
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd) || fail "this recipe could not resolve the checkout it was run from" \
    "run it from a checkout, so the graph and template it names are that checkout's"

python="$script_dir/../.venv/bin/python3"
[ -x "$python" ] || python=$(command -v python3) || fail "no Python interpreter was found" \
    "provision this checkout with 'just bootstrap', then retry"

load() {
    local helper="$script_dir/$1"
    if [ ! -f "$helper" ] || [ ! -r "$helper" ]; then
        fail "required helper is not a readable regular file: $helper" \
            "restore it from the repository or run 'just bootstrap', then retry"
    fi
    # shellcheck source=/dev/null
    if ! . "$helper"; then
        fail "the helper at $helper is readable but could not be loaded" \
            "restore it from the repository or run 'just bootstrap', then retry"
    fi
}

# shellcheck source=scripts/plan-brief.sh
load plan-brief.sh

run=${1:-}
case "$run" in
    "" | -*) fail "name the run whose drafts to verify as the first word" "usage: $usage" ;;
esac
# llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally, so nothing would ever match.
[[ "$run" =~ $PLAN_SAFE_RUN_ID ]] || fail "'$run' is not a run id: a run id is one word of letters, digits, '_' and '-'" \
    "name the run as 'just runs' lists it"
shift

feedback=""
detached=0
board=""
while [ $# -gt 0 ]; do
    case "$1" in
        --feedback | --to)
            if [ $# -lt 2 ] || [ -z "$2" ]; then
                fail "$1 was given no value" "usage: $usage"
            fi
            if [ "$1" = --feedback ]; then feedback=$2; else board=$2; fi
            shift 2
            ;;
        --feedback=* | --to=*)
            value=${1#*=}
            [ -n "$value" ] || fail "${1%%=*} was given no value" "usage: $usage"
            if [[ "$1" == --feedback=* ]]; then feedback=$value; else board=$value; fi
            shift
            ;;
        --detach)
            detached=1
            shift
            ;;
        *)
            fail "'$1' is not an option of this recipe" "usage: $usage"
            ;;
    esac
done
if [ -n "$feedback" ] && { [ ! -f "$feedback" ] || [ ! -r "$feedback" ]; }; then
    fail "the feedback file '$feedback' is not a readable file" "write the feedback there first, then retry"
fi

runs_root="${!PLAN_RUNS_ROOT_ENV:-$PLAN_DEFAULT_RUNS_ROOT}"
if [ -e "$runs_root" ] && { [ ! -d "$runs_root" ] || [ ! -r "$runs_root" ] || [ ! -x "$runs_root" ]; }; then
    fail "the run ledger at $runs_root cannot be searched, so whether run '$run' is still driven, and which follow-up run id is free, cannot be told" \
        "fix its permissions, or point $PLAN_RUNS_ROOT_ENV at a directory this launch can read, then retry"
fi

# A run with no run root is a run nothing drives. One that has one is asked, once.
if [ -e "$runs_root/$run" ]; then
    watched=0
    "$script_dir/onepipeline.sh" watch "$run" --timeout 0 --until nothing-driving >/dev/null 2>&1 || watched=$?
    case "$watched" in
        "$WATCH_SETTLED" | "$WATCH_NOTHING_DRIVING") ;;
        "$WATCH_SURFACE_WAITING" | "$WATCH_ELAPSED")
            fail "run '$run' is still being driven, so its drafts may still be growing and no follow-up run was launched" \
                "wait for it to settle with 'just watch $run', or stop it with 'just stop $run', then retry"
            ;;
        *)
            fail "whether run '$run' is still being driven could not be read: 'onepipeline watch' exited $watched" \
                "read the run with 'just status $run', then retry"
            ;;
    esac
fi

# shellcheck source=scripts/follow-up-env.sh
load follow-up-env.sh
export_follow_up_drafts follow-ups || exit "$?"
drafts_root=${!FOLLOW_UP_DRAFTS_ROOT_ENV}

counts=$("$python" -m orchestrator.follow_up_tickets inventory --root "$drafts_root" "$run") ||
    fail "the drafts and tickets of run '$run' could not be counted under $drafts_root" \
        "restore the pinned toolchain with 'just bootstrap', then retry"
read -r held_drafts held_tickets <<<"$counts"
# llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally, so nothing would ever match.
if ! [[ "$held_drafts" =~ $COUNT && "$held_tickets" =~ $COUNT ]]; then
    fail "counting the drafts and tickets of run '$run' answered '$counts' rather than two counts" \
        "restore the pinned toolchain with 'just bootstrap', then retry"
fi
if [ "$held_drafts" -eq 0 ] && [ "$held_tickets" -eq 0 ]; then
    echo "follow-ups: run $run holds no follow-up drafts and no tickets under $drafts_root, so there is nothing to verify and no follow-up run was launched"
    exit 0
fi

# The board the tickets are copied onto: the module's constant, or a configured source the
# caller named. Asked of the store's own configuration, environment layer included.
if [ -z "$board" ]; then
    board=$("$python" -c 'from orchestrator import follow_up_tickets; print(follow_up_tickets.BOARD)') ||
        fail "the board follow-up tickets are copied onto could not be read" \
            "restore the pinned toolchain with 'just bootstrap', then retry"
else
    configured=0
    "$python" -c 'import sys
from orchestrator import design_approval
sys.exit(0 if sys.argv[1] in design_approval.configured_sources() else 3)' "$board" || configured=$?
    case "$configured" in
        0) ;;
        3) fail "--to names '$board', which this checkout's plan store configures no source for" \
            "name a configured source, or omit --to for the followups board" ;;
        *) fail "the plan store's configuration could not be read to check --to '$board'" \
            "run 'just bootstrap', then retry" ;;
    esac
fi

project_native="$run$SUFFIX"
follow_up_run=$project_native
attempt=1
while [ -e "$runs_root/$follow_up_run" ]; do
    attempt=$((attempt + 1))
    follow_up_run="$project_native-$attempt"
done
export "$PLAN_RUN_ID_ENV=$follow_up_run"

# shellcheck source=scripts/plan-root-env.sh
load plan-root-env.sh
export_plan_authoring_root follow-ups || exit "$?"
plan_root=${!PLAN_AUTHORING_ROOT_ENV}

scratch=$(mktemp) || fail "a scratch file for the composed task could not be created" "free disk space and retry"
trap 'rm -f "$scratch" || echo "follow-ups: the scratch file $scratch could not be removed; delete it by hand" >&2' EXIT

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own checkout stops being enterable between its first line and this one; no journey can produce that without racing the filesystem the test itself runs on.
checkout=$(CDPATH='' cd -- "$script_dir/.." && pwd) || fail "this recipe's checkout could not be resolved" \
    "run it from a readable checkout, then retry"
compose=(compose --template "$checkout/$TEMPLATE" --root "$drafts_root" --run "$run" --board "$board"
    --validate "\"$python\" -m orchestrator.follow_up_tickets validate"
    --board-status "\"$python\" -m orchestrator.follow_up_tickets board-status" --checkout "$checkout")
[ -z "$feedback" ] || compose+=(--feedback "$feedback")
"$python" -m orchestrator.follow_up_tickets "${compose[@]}" >"$scratch" ||
    fail "the follow-up agent's task could not be composed from $TEMPLATE" \
        "the diagnostic above names what to repair"

"$python" -c "$PROJECT_PROGRAM" "$plan_root" "$project_native" "$follow_up_run" "$run" "$NODE_ID" \
    "$PERSONA" "$GRAPH" "$scratch" "$PLAN_DIRECT_PLACEMENT_NOTE" "$FOLLOW_UPS_METADATA" ||
    fail "the follow-ups project for run '$run' could not be written under $plan_root" \
        "restore the pinned toolchain with 'just bootstrap', then retry"

project="$PLAN_SOURCE:$project_native"

if [ "$detached" -eq 1 ]; then
    launched=0
    # The engine's own launch record goes to standard error, so standard output is the two
    # lines below and nothing else.
    # llmlint: ignore[tool_output_is_signal] The engine's launch record is kept for an operator reading standard error, and standard output carries only the two lines the success hook relays.
    "$script_dir/onepipeline.sh" start "$project" --dag-graph off --detach >&2 || launched=$?
    if [ "$launched" -ne 0 ]; then
        echo "follow-ups: the detached launch of run $follow_up_run over run $run failed; the diagnostic above names why" >&2
        exit "$launched"
    fi
    [ -f "$runs_root/$follow_up_run/launch.json" ] ||
        fail "the detached launch returned, but run $follow_up_run has no launch record under $runs_root" \
            "read the host's runs with 'just runs', then retry"
    # llmlint: ignore[tool_output_is_signal] Contract C7 fixes exactly these two lines: the engine's success hook relays them to the manager, and the second is the watch command a detached run is owed, so folding them into one line breaks what that relay reads.
    printf 'follow-up run: %s\nwatch it with: just watch %s\n' "$follow_up_run" "$follow_up_run"
    exit 0
fi

echo "follow-ups: launching run $follow_up_run to verify run $run's drafts onto '$board'; watch it with: just watch $follow_up_run" >&2
status=0
# llmlint: ignore[tool_output_is_signal] This is an attached launch, so streaming the run as it goes is what the operator stays attached for.
"$script_dir/onepipeline.sh" start "$project" --dag-graph off || status=$?

# With no judge, this is what holds the output to its shape: every ticket the run left is
# validated through the store, and each one that fails is named with what is wrong.
checked=0
"$python" -m orchestrator.follow_up_tickets check-run --root "$drafts_root" "$run" || checked=$?
if [ "$checked" -ne 0 ]; then
    echo "follow-ups: the ticket(s) named above under $drafts_root/tasks/$run/tickets fail the ticket shape; correct them, or re-dispatch with 'just follow-ups $run --feedback FILE'" >&2
fi
if [ "$status" -ne 0 ]; then
    echo "follow-ups: run $follow_up_run did not settle successfully; read it with 'just results $follow_up_run'" >&2
    exit "$status"
fi
exit "$checked"
