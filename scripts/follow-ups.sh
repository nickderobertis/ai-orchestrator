#!/usr/bin/env bash
# Dispatch the follow-up agent over one run's drafted follow-ups: `just follow-ups <run-id>
# [--feedback FILE] [--comments] [--detach] [--to SOURCE]`.
#
# During a run every party drafts what it noticed outside its own scope as an unverified
# follow-up in the run's draft project (`orchestrator/follow_up_drafts.py`). This recipe
# launches the one agent that reads them afterwards: it verifies each draft against the
# trees it names, groups what stands by root cause, writes one verified ticket per root
# cause beside the drafts, and copies each onto the `followups` board — commenting on
# another run's open issue for the same root cause instead of duplicating it. The agent's
# task is this host's `follow-up-task` template, `templates/follow-up-task.md.j2`, rendered
# by the pinned plan store: the ticket's shape and who owns what on the board are
# `orchestrator/follow_up_tickets.py`'s, whose `answers` command states every value and
# every example the template's prose names, and the template's own `mode` is `initial`
# here, or `feedback` for a re-dispatch over the board's own comments, as `--comments`
# below says.
#
# `scripts/plan.sh` is the precedent for everything about the launch, and each departure
# from it is named:
#
#   * **One direct node, `follow-ups`, in project `authoring:<run-id>-follow-ups`**, whose
#     task the template renders ending in `PLAN_DIRECT_PLACEMENT_NOTE`: a direct node works
#     in the launching checkout and commits nothing, which is exactly what this agent's
#     deliverable is — records under the gitignored draft root and items on a board, never
#     a branch.
#   * **The node's task is created by the store from the template, never written here.**
#     `onepipeline template resolve follow-up-task --json` states the template through its
#     layers — a repository's `.onepipeline/templates/` in the working directory this was
#     run from, then this host's `templates/` — and `onetaskgraph task create
#     --template-loader -` renders it from the answers and records its
#     `onepipeline:follow-up-task` provenance, so the body is exactly the rendering. A
#     rendering is validated only once it is stored, so `onepipeline template check
#     follow-up-task --item` holds the created task to the criteria rule and to its
#     provenance, and a refusal launches nothing. A re-dispatch first retires the task the
#     project already holds, so the project keeps one node.
#   * **Every plan-store instruction in the task names this checkout's own CLI**,
#     resolved here and spelled in full, rather than the bare `onetaskgraph` a dispatch
#     would resolve from its own search path. Two real runs resolved another release that
#     way: one filed its tickets as issues of the wrong repository, and one had every
#     sound ticket refused by a validator reading an older record schema.
#   * **It names the per-node agent graph `graphs/follow-up.yaml`**, a single-sided member
#     with no judge, so the task created here is the only copy of its instructions it is
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
#   * a run that has not ended is refused (exit 2), because its drafts may still be growing
#     — `onepipeline status <run> --json` is read once, and only a run whose reading carries
#     an `ending`, of any kind, goes on: one still driven is retried once it ends, one paused
#     is refused naming each waiting human action and a blocking surface, and one nothing
#     drives with neither is to be adopted or stopped. That is the engine's own reading
#     rather than a guess from its ledger, and a run with no run root is one nothing drives;
#   * a run whose draft project holds no drafts and no tickets ends at one line naming it
#     and launches nothing (exit 0);
#   * `--to` names the board the tickets are copied onto, for a journey standing a local
#     store in for the live board, and is refused unless it names a configured source;
#   * `--feedback FILE` puts that file's text into the task verbatim, under a
#     heading of its own, for a re-dispatch over the same run's drafts, tickets and board;
#   * `--comments` says that file is a gathering of the board's own comments rather than a
#     manager's prose, and renders the **feedback** mode instead — the narrow
#     comment-answering task, with no inventory, no run-wide verification and no
#     accepted-fix comparison, and no status decision or copy but for a ticket a quoted
#     comment names. It is refused without `--feedback`, and
#     `scripts/follow-ups-answer-comments.sh` is its one caller.
#
# **Which mode is the caller's to say, never the file's.** Sniffing a feedback file for
# quoted comments would work for every file this repository writes and narrow silently the
# first time a manager's own feedback happened to quote one, so the mode is a flag: a bare
# `--feedback` stays the full re-dispatch AGENTS.md documents for a manager, and only the
# comment gathering asks for the narrow one.
#
# **Attached by default, and then the mode's own account is checked.** With no judge, what
# holds the agent's output to its shape is this check and the manager's reading, so once an
# attached run settles the validator that matches the mode is run and its refusal is what
# this recipe exits non-zero with. In initial mode that is every ticket left under
# `tasks/<run-id>/tickets/` validated and each one that fails named, then the disposition
# artifact checked for a disposition per input draft; in feedback mode it is the response
# artifact alone, because a run answering comments touches only the tickets its quoted
# comments name, and reading the rest would refuse it for somebody else's unfinished work. The task's own steps name
# the same validator, which is what binds the detached path the success hook launches.
#
# **`--detach` returns once the launch record exists**, printing
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

#: The registered template the node's task is rendered from, `templates/follow-up-task.md.j2`
#: at this host's layer, which `templates/templates.yaml` registers.
TEMPLATE_NAME="follow-up-task"

#: The source the project is written into, and what its native id and run id append.
PLAN_SOURCE="authoring"
SUFFIX="-follow-ups"

#: What one count the inventory answers with is: a non-negative whole number.
COUNT='^[0-9]+$'

#: Writes the project with no task yet and prints the id the store wrote it under, after
#: retiring every task the project already held: the store files a created task by its
#: title rather than under the project, so a re-dispatch would otherwise launch the
#: previous pass's node beside the one created next. The writer sweeps the layout it wrote
#: itself; the store's own records are named by the ids it answers, one per file.
PROJECT_PROGRAM='
import json, pathlib, sys

from orchestrator import plan_store
from orchestrator.project_store import TASKS_DIRECTORY, write_plan_project

(root, native, name, run, source, metadata, mode) = sys.argv[1:8]
goal = (
    f"Answer the quoted comments on follow-up tickets for run {run}"
    if mode == "feedback"
    else f"Verify the follow-up drafts run {run} left, and put the verified tickets on the board"
)
plan = {"schema_version": 3, "goal": {"text": goal}, "name": name, "tasks": []}
root = pathlib.Path(root)
project = write_plan_project(root, plan, native_id=native, project_metadata=json.loads(metadata))
for held in plan_store.read_tasks(f"{source}:{project}"):
    record = (root / TASKS_DIRECTORY / held.qualified_id.split(":", 1)[1]).with_suffix(".md")
    if record.resolve().is_relative_to((root / TASKS_DIRECTORY).resolve()):
        record.unlink(missing_ok=True)
print(project)
'

usage="just follow-ups <run-id> [--feedback FILE] [--comments] [--detach] [--to SOURCE]"

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
comments=0
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
        --comments)
            comments=1
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
if [ "$comments" -eq 1 ] && [ -z "$feedback" ]; then
    fail "--comments says which kind of feedback file this is, and none was named" \
        "gather the board's comments with 'just follow-ups-answer-comments', which names both"
fi

# The board credential from this checkout's `.env`, before the first board read in either
# mode; a name the environment already defines wins.
# llmlint: ignore-block[changed_behavior_has_e2e] Each outcome of these two lines is driven by `tests/plan_tooling/test_follow_ups_comment_answers_e2e.py`: in both modes, `.env` handed to the store and a defined name winning, a `.env` the helper refuses (malformed, or not a file), and the helper missing or unreadable; in `--comments` mode, the only one that reads the board before launching, no credential refused by the store. What they establish serves this recipe's own board reads before it launches; a dispatched worker's copy does not come from here but from the engine's dispatch environment hook, which `scripts/onepipeline.sh` names on every start and which re-runs `export_host_credentials` through `scripts/dispatch-env.sh` before each dispatch, unchanged by this recipe.
# shellcheck source=scripts/credentials-env.sh
load credentials-env.sh
export_host_credentials follow-ups || exit "$?"
# llmlint: ignore-end[changed_behavior_has_e2e]

runs_root="${!PLAN_RUNS_ROOT_ENV:-$PLAN_DEFAULT_RUNS_ROOT}"
if [ -e "$runs_root" ] && { [ ! -d "$runs_root" ] || [ ! -r "$runs_root" ] || [ ! -x "$runs_root" ]; }; then
    fail "the run ledger at $runs_root cannot be searched, so whether run '$run' is still driven, and which follow-up run id is free, cannot be told" \
        "fix its permissions, or point $PLAN_RUNS_ROOT_ENV at a directory this launch can read, then retry"
fi

# A run with no run root is a run nothing drives. One that has one is asked, once, for the
# engine's reading of it, and only a run that ended is followed up.
if [ -e "$runs_root/$run" ]; then
    # The engine's own diagnostic reaches stderr as it writes it, so a read that fails is
    # explained in its words above the recipe's line.
    asked=0
    answered=$("$script_dir/onepipeline.sh" status "$run" --json) || asked=$?
    if [ "$asked" -ne 0 ]; then
        fail "whether run '$run' has ended could not be read: 'onepipeline status --json' exited $asked, with the engine's diagnostic above" \
            "read the run with 'just status $run', then retry"
    fi
    # `orchestrator/run_reading.py` checks the answer against the engine's published
    # document and prints the one decision this recipe acts on.
    decision=$(printf '%s' "$answered" | "$python" -m orchestrator.run_reading "$run") ||
        fail "the reading of run '$run' could not be checked" \
            "restore the pinned toolchain with 'just bootstrap', then retry"
    case "$decision" in
        ended) ;;
        "driven "*)
            fail "run '$run' is still driven (it reads ${decision#driven }), so its drafts may still be growing and no follow-up run was launched" \
                "retry once it ends; 'just watch $run' says when"
            ;;
        "paused "*)
            fail "run '$run' is paused, not ended, so no follow-up run was launched: ${decision#paused }" \
                "attest each waiting human action, or retire it with 'drop' (\"dependents\": \"detach\" and a reason), and answer a blocking surface — each through 'just channel-next $run' and 'just channel-reply $run' — or stop the run with 'just stop $run' to end it, then retry"
            ;;
        "crashed "*)
            fail "run '$run' has not ended and nothing is driving it (it reads ${decision#crashed })" \
                "adopt it with 'just orchestrate --adopt $run' to finish it, or stop it with 'just stop $run' to end it, then retry"
            ;;
        *)
            fail "whether run '$run' has ended could not be read: 'onepipeline status --json' answered something other than the engine's reading (${decision#unreadable })" \
                "read the run with 'just status $run', then retry"
            ;;
    esac
fi

# shellcheck source=scripts/follow-up-env.sh
load follow-up-env.sh
export_follow_up_drafts follow-ups || exit "$?"
drafts_root=${!FOLLOW_UP_DRAFTS_ROOT_ENV}

if [ "$comments" -ne 1 ]; then
    counts=$("$python" -m orchestrator.follow_up_tickets inventory --root "$drafts_root" "$run") ||
        fail "the drafts and tickets of run '$run' could not be counted under $drafts_root" \
            "restore the pinned toolchain with 'just bootstrap', then retry"
    read -r held_drafts held_tickets <<<"$counts"
    # llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally, so nothing would ever match.
    if ! [[ "$held_drafts" =~ $COUNT && "$held_tickets" =~ $COUNT ]]; then
        fail "counting the drafts and tickets of run '$run' answered '$counts' rather than two counts" \
            "restore the pinned toolchain with 'just bootstrap', then retry"
    fi
    # Every change the run landed, measured once against its own repository's
    # `onepipeline`-labelled budgets before anything is dispatched, in the budget account
    # beside the disposition account: the library's verdicts and the engine's telemetry,
    # which the dispatch only disposes of. A re-dispatch keeps the account it finds, since a
    # budget measures once. `docs/budgets.md` states what each budget's command is handed.
    budget_account=$("$python" -m orchestrator.follow_up_tickets open-budgets --root "$drafts_root" \
        --runs-root "$runs_root" --checkout "$script_dir/.." "$run") ||
        fail "the landed changes of run '$run' could not be measured against their budgets under $drafts_root" \
            "repair what the refusal above names, then retry 'just follow-ups $run'"
    overruns=$("$python" -m orchestrator.follow_up_tickets budget-overruns --root "$drafts_root" "$run") ||
        fail "the budget account at $budget_account could not be read" \
            "repair what the refusal above names, then retry 'just follow-ups $run'"
    # llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally, so nothing would ever match.
    [[ "$overruns" =~ $COUNT ]] ||
        fail "counting the overruns of run '$run' answered '$overruns' rather than a count" \
            "restore the pinned toolchain with 'just bootstrap', then retry"
fi
if [ "$comments" -ne 1 ] && [ "$held_drafts" -eq 0 ] && [ "$held_tickets" -eq 0 ] && [ "$overruns" -eq 0 ]; then
    # A previous pass can consume every draft and still leave an incomplete account.
    # Read that account before calling the run empty; no artifact means no pass began.
    prior=$("$python" -m orchestrator.follow_up_tickets dispositions-path --root "$drafts_root" "$run") ||
        fail "the disposition account path for run '$run' could not be read" "repair the diagnostic above, then retry"
    if [ -f "$prior" ]; then
        "$python" -m orchestrator.follow_up_tickets check-dispositions --root "$drafts_root" "$run" >/dev/null ||
            fail "the account at $prior was refused above" "correct it before treating this run as complete"
    fi
    # Every landed change's result line, on standard error so the one line below stays the
    # whole of standard output, which the success hook relays alone.
    # llmlint: ignore[tool_output_is_signal] Each landed result is mandatory report output.
    "$python" -m orchestrator.follow_up_tickets budget-results --root "$drafts_root" "$run" >&2 ||
        fail "the budget account at $budget_account could not be read" "repair the refusal above, then retry"
    echo "follow-ups: run $run holds no follow-up drafts, no tickets and no budget overrun under $drafts_root, so there is nothing to verify and no follow-up run was launched"
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

# The repository layer `onepipeline template resolve` reads is the working directory this
# was run from, which `just` makes this checkout: taken before anything below changes one.
repository_layer=$PWD

scratch=$(mktemp) || fail "a scratch file for the task's answers could not be created" "free disk space and retry"
trap 'rm -f "$scratch" || echo "follow-ups: the scratch file $scratch could not be removed; delete it by hand" >&2' EXIT
# The budget account as measured, kept out of the dispatch's reach, so the check after
# settlement holds every value but the dispositions to what the library and the engine said.
measured=$(mktemp) || fail "a scratch file for the measured budget account could not be created" "free disk space and retry"
hook_snapshot=''
budget_hook=''
hook_handed_off=0
# shellcheck disable=SC2329,SC2317 # Invoked by the EXIT trap, not by a direct shell
# command, which also leaves shellcheck 0.9.0 reading every statement inside it as
# unreachable.
cleanup_launch() {
    local -a files=("$scratch" "$measured")
    if [ "$hook_handed_off" -eq 0 ]; then
        [ -z "$hook_snapshot" ] || files+=("$hook_snapshot")
        [ -z "$budget_hook" ] || files+=("$budget_hook")
    fi
    rm -f -- "${files[@]}" || echo "follow-ups: launch scratch could not be removed; delete the named files by hand" >&2
}
trap 'cleanup_launch' EXIT

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own checkout stops being enterable between its first line and this one; no journey can produce that without racing the filesystem the test itself runs on.
checkout=$(CDPATH='' cd -- "$script_dir/.." && pwd) || fail "this recipe's checkout could not be resolved" \
    "run it from a readable checkout, then retry"

# The plan-store CLI the task writes every store instruction with: this
# checkout's own, spelled from the resolved checkout so the task carries one canonical
# path. Nothing stands in for it. `config/onetaskgraph.version` is per checkout, so a copy
# the search path happens to offer answers about whichever checkout provisioned *it* —
# which is the resolution the header says cost two real runs, and letting it through here
# would put it back with the task's own authority behind it. A checkout nobody has
# bootstrapped is refused instead. It is also the store the task is created in.
store_cli="$checkout/.venv/bin/onetaskgraph"
[ -x "$store_cli" ] || fail "this checkout has no plan-store CLI at $store_cli, and a task's store instructions may name no other" \
    "provision this checkout with 'just bootstrap', then retry"
tickets_module=("$python" -m orchestrator.follow_up_tickets)
if [ "$comments" -eq 1 ]; then
    mode=feedback
    # The file is embedded in the dispatched task verbatim and its acceptance criteria rest
    # on an account of the comments it quotes, so a file that is no gathering of this board
    # is one nothing should be launched over.
    # Quiet where it passes: this recipe's one status line is the launch it prints below,
    # and the command's refusals reach standard error whatever this does with its answer.
    "${tickets_module[@]}" check-gathering --board "$board" --feedback "$feedback" "$run" >/dev/null ||
        fail "'$feedback' is not a gathering of '$board' this run could answer, so no follow-up run was launched" \
            "gather the board's comments with 'just follow-ups-answer-comments --run $run', which writes one"
    # Asked of the module rather than restated here: where one gathering's account goes is
    # `responses_path`'s, and a second spelling of it would drift the day either moves.
    account=$("${tickets_module[@]}" responses-path --feedback "$feedback") ||
        fail "where the account answering '$feedback' goes could not be read" \
            "restore the pinned toolchain with 'just bootstrap', then retry"
    validator=("${tickets_module[@]}" check-responses --board "$board" --feedback "$feedback" "$run")
else
    mode=initial
    # Written before the launch, never by the dispatch: the agent deletes each draft a
    # ticket consumed, so an input set derived afterwards would be the drafts nothing
    # happened to. The artifact only grows, so a re-dispatch keeps the first pass's account.
    account=$("${tickets_module[@]}" open-dispositions --root "$drafts_root" "$run") ||
        fail "the drafts of run '$run' could not be recorded as this dispatch's input set under $drafts_root" \
            "repair the account or draft file the refusal above names, keeping every answer it records, then retry 'just follow-ups $run'"
    # The dispatch corrects its local account; this sole board check runs after settlement
    # so a final board edit or comment cannot escape validation.
    validator=("${tickets_module[@]}" check-dispositions --root "$drafts_root" --board "$board" "$run")
    cp -- "$budget_account" "$measured" ||
        fail "the budget account at $budget_account could not be kept as measured" "free disk space and retry"
    budget_validator=("${tickets_module[@]}" check-budgets --root "$drafts_root" --board "$board" \
        --measured "$measured" "$run")
fi
budget_hooks=()
if [ "$comments" -ne 1 ]; then
    hook_snapshot=$(mktemp) || fail "a per-launch budget snapshot could not be created" "free disk space and retry"
    budget_hook=$(mktemp) || fail "a per-launch budget closeout hook could not be created" "free disk space and retry"
    cp -- "$budget_account" "$hook_snapshot" ||
        fail "the budget account could not be captured for detached closeout" "free disk space and retry"
    "${tickets_module[@]}" write-budget-closeout --root "$drafts_root" --board "$board" \
        --measured "$hook_snapshot" --hook "$budget_hook" "$run" >/dev/null ||
        fail "the budget closeout hook could not be written" "repair the refusal above and retry"
    budget_hooks=(--success-hook "$budget_hook" --failure-hook "$budget_hook")
fi

# The engine that states the template and checks the task created from it: this checkout's
# installed one, run directly as a launch runs it, under this host's template root.
engine="$checkout/.venv/bin/onepipeline"
[ -x "$engine" ] || fail "this checkout has no engine at $engine to state the $TEMPLATE_NAME template with" \
    "provision this checkout with 'just bootstrap', then retry"
# shellcheck source=scripts/template-env.sh
load template-env.sh
export_template_root follow-ups || exit "$?"

answering=(answers --mode "$mode" --root "$drafts_root" --run "$run"
    --board "$board"
    --validate "\"$python\" -m orchestrator.follow_up_tickets validate"
    --board-status "\"$python\" -m orchestrator.follow_up_tickets board-status"
    --board-items "\"$python\" -m orchestrator.follow_up_tickets board-items"
    --copy "\"$python\" -m orchestrator.follow_up_tickets copy" --checkout "$checkout"
    --re-estimate "\"$python\" -m orchestrator.follow_up_tickets re-estimate"
    --plan-store "$store_cli")
[ -z "$feedback" ] || answering+=(--feedback "$feedback")
if [ "$comments" -eq 1 ]; then
    printf -v quoted_python '%q' "$python"
    printf -v quoted_board '%q' "$board"
    printf -v quoted_feedback '%q' "$feedback"
    printf -v quoted_run '%q' "$run"
    answering+=(--responses "$account"
        --check-responses "$quoted_python -m orchestrator.follow_up_tickets check-responses --board $quoted_board --feedback $quoted_feedback $quoted_run")
else
    printf -v quoted_python '%q' "$python"
    printf -v quoted_root '%q' "$drafts_root"
    printf -v quoted_board '%q' "$board"
    printf -v quoted_run '%q' "$run"
    answering+=(--dispositions "$account"
        --check-dispositions "$quoted_python -m orchestrator.follow_up_tickets check-dispositions --root $quoted_root $quoted_run"
        --budgets "$budget_account"
        --check-budgets "$quoted_python -m orchestrator.follow_up_tickets check-budgets --root $quoted_root $quoted_run")
fi
"${tickets_module[@]}" "${answering[@]}" >"$scratch" ||
    fail "the follow-up agent's task could not be answered for run '$run'" \
        "repair what the refusal above names, or restore the toolchain with 'just bootstrap', then retry"

# The store slugs the id it is handed, lower-casing it, so the project is launched by the
# id it answers with: a run id carrying a capital is otherwise one the store never holds.
written=$("$python" -c "$PROJECT_PROGRAM" "$plan_root" "$project_native" "$follow_up_run" "$run" \
    "$PLAN_SOURCE" "$FOLLOW_UPS_METADATA" "$mode") ||
    fail "the follow-ups project for run '$run' could not be written under $plan_root" \
        "restore the pinned toolchain with 'just bootstrap', then retry"

# The answer is `write_plan_project`'s own return, whose shape `project_store._slug` is the
# one definition of, so an empty answer is the one thing left for this launch to refuse.
# llmlint: ignore[boundary_inputs_validated] The value is this repository's own writer's return, taken in the process that wrote it; restating its slug shape here is the second source `contracts_have_one_source_or_a_drift_gate` forbids.
# llmlint: ignore[changed_behavior_has_e2e] Reachable only when the pinned toolchain's writer returns without naming the project it wrote; no journey can produce that without doubling the writer, which the suite never does.
[ -n "$written" ] ||
    fail "the plan store named no project for the follow-ups of run '$run'" \
        "restore the pinned toolchain with 'just bootstrap', then retry"
project="$PLAN_SOURCE:$written"

# The one node, created by the store from the template the engine states, in this
# checkout's store configuration. The placement note is answered here, beside the answers
# the module computes, because it is this host's direct-node note that `just plan` hands its
# own nodes too.
task=$(cd "$checkout" &&
    "$engine" template resolve "$TEMPLATE_NAME" --repo "$repository_layer" --json |
    "$store_cli" task create "$PLAN_SOURCE" --template-loader - --no-interactive \
        --project "$written" --title "$run follow-ups" --answers "$scratch" \
        --var "placement_note=$PLAN_DIRECT_PLACEMENT_NOTE" \
        --metadata "onepipeline.id=\"$NODE_ID\"" \
        --metadata "onepipeline.persona=\"$PERSONA\"" \
        --metadata "onepipeline.agent_graph=\"$GRAPH\"") ||
    fail "the follow-ups task for run '$run' could not be created in $project from the $TEMPLATE_NAME template" \
        "repair what the diagnostic above names, then retry"
# The store's answer is an item reference the check below and a person reading this
# recipe's refusals both act on, so it is held to one task of the source it was created in.
# llmlint: ignore[changed_behavior_has_e2e] Reachable only when the pinned store creates a task and names none, or one outside the source it was asked to create in; no journey can produce that without doubling the store, which the suite never does.
[[ "$task" == "$PLAN_SOURCE:"?* && "$task" != *[[:space:]]* ]] ||
    fail "the plan store named '$task' for the follow-ups of run '$run', which is not one task of '$PLAN_SOURCE'" \
        "restore the pinned toolchain with 'just bootstrap', then retry"

# A rendering is validated only once it is stored: the engine holds the created task to
# the criteria rule and to being the rendering its provenance records, and a task it
# refuses is one nothing launches. Its own line on success is dropped, so standard output
# stays what `--detach` promises; a refusal reaches standard error as the engine wrote it.
(cd "$checkout" && "$engine" template check "$TEMPLATE_NAME" --repo "$repository_layer" --item "$task" >/dev/null) ||
    fail "the engine refused the follow-ups task $task above, so no follow-up run was launched" \
        "correct the $TEMPLATE_NAME template the refusal names, then retry"

if [ "$detached" -eq 1 ]; then
    launched=0
    # The engine's own launch record goes to standard error, so standard output is the two
    # lines below and nothing else.
    # llmlint: ignore[tool_output_is_signal] The engine's launch record is kept for an operator reading standard error, and standard output carries only the two lines the success hook relays.
    "$script_dir/onepipeline.sh" start "$project" --dag-graph off --detach "${budget_hooks[@]}" >&2 || launched=$?
    [ ! -f "$runs_root/$follow_up_run/launch.json" ] || hook_handed_off=1
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

if [ "$comments" -eq 1 ]; then
    echo "follow-ups: launching run $follow_up_run to answer run $run's quoted comments on '$board'; watch it with: just watch $follow_up_run" >&2
else
    echo "follow-ups: launching run $follow_up_run to verify run $run's drafts onto '$board'; watch it with: just watch $follow_up_run" >&2
fi
status=0
# llmlint: ignore[tool_output_is_signal] This is an attached launch, so streaming the run as it goes is what the operator stays attached for.
"$script_dir/onepipeline.sh" start "$project" --dag-graph off "${budget_hooks[@]}" || status=$?
[ ! -f "$runs_root/$follow_up_run/launch.json" ] || hook_handed_off=1

# With no judge, this is what holds the output to its shape. Initial mode validates every
# ticket the run left through the store, then reads the disposition account; feedback mode
# reads the response account and nothing else, because that dispatch is not the one that
# writes tickets and a ticket another dispatch left unfinished is not its refusal to carry.
again="just follow-ups $run --feedback FILE"
checked=0
if [ "$comments" -ne 1 ]; then
    "${tickets_module[@]}" check-run --root "$drafts_root" "$run" || checked=$?
    if [ "$checked" -ne 0 ]; then
        echo "follow-ups: the ticket(s) named above under $drafts_root/tasks/$run/tickets fail the ticket shape; correct them, or re-dispatch with '$again'" >&2
    fi
fi
accounted=0
"${validator[@]}" >/dev/null || accounted=$?
if [ "$accounted" -ne 0 ]; then
    if [ "$comments" -eq 1 ]; then
        printf -v check_command '%q ' "${validator[@]}"
        echo "follow-ups: the account at $account was refused above; correct it and run '${check_command% }', reusing any reply already posted under this run's marker" >&2
    else
        echo "follow-ups: the account at $account was refused above; correct it, or re-dispatch with '$again'" >&2
    fi
    [ "$checked" -ne 0 ] || checked=$accounted
fi
if [ "$comments" -ne 1 ]; then
    reported=0
    # llmlint: ignore[tool_output_is_signal] Each landed result is mandatory report output.
    "${tickets_module[@]}" budget-results --root "$drafts_root" --measured "$measured" "$run" || reported=$?
    if [ "$reported" -ne 0 ]; then
        echo "follow-ups: results at $budget_account could not be reported; repair the refusal above and retry with '$again'" >&2
        [ "$checked" -ne 0 ] || checked=$reported
    fi
    budgeted=0
    "${budget_validator[@]}" >/dev/null || budgeted=$?
    if [ "$budgeted" -ne 0 ]; then
        echo "follow-ups: the budget account at $budget_account was refused above; correct its dispositions, or re-dispatch with '$again'" >&2
        [ "$checked" -ne 0 ] || checked=$budgeted
    fi
fi
if [ "$status" -ne 0 ]; then
    echo "follow-ups: run $follow_up_run did not settle successfully; read it with 'just results $follow_up_run'" >&2
    exit "$status"
fi
exit "$checked"
