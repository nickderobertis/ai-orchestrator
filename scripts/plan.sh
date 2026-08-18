#!/usr/bin/env bash
# Launch a planner on a manager-written brief: `just plan <BRIEF.md> [--name NAME]
# [--max-turns N] [<onepipeline start flags>]`.
#
# The manager's job is writing the brief and reviewing what comes back, not
# assembling a plan file by hand. So this recipe writes the plan, and the one shape
# that must be right is machine-produced rather than remembered:
#
#   * **The persona is named as a path**, `../personas/planner.yaml`, relative to
#     `graphs/`. The bare name `planner` resolves to a role compiled into
#     `oneagentgraph` and this repository's persona file is never read — a planner
#     silently running the wrong role, with nothing in the launch to say so.
#   * **The run id is guaranteed to be this run's own**, and then exported as
#     `ONEPIPELINE_RUN_ID` so the dispatched planner asks on the channel this launch
#     printed. `onepipeline` mints a run id from the plan's `name` and, when a run
#     root of that name already exists, mints the first free `<name>-2` instead — so
#     a name is only the run id while nothing has taken it. This recipe writes the
#     plan and owns its `name`, so it guarantees that by refusing a name already
#     taken rather than by predicting what will be minted.
#   * **`ORCHESTRATOR_ASK_MANAGER` is exported into the launch environment**, holding
#     the path of `scripts/ask-manager.sh`, which is how the dispatched planner stops
#     and asks rather than guessing at a decision fork. A launch that dropped it would
#     produce exactly the confidently-wrong plan the ask channel exists to prevent.
#     Established through `scripts/ask-manager-env.sh`, which every launch path shares,
#     and taken here before anything is written so a checkout that cannot ask is
#     refused rather than left holding a plan.
#   * **The watch command is printed**, so arming it is one copy-paste rather than
#     something composed under time pressure. `just channel-next` and not `just
#     monitor`: rendering a surface is not reading it, and only `channel-next`
#     consumes one — a planner's blocking question is answered there or not at all.
#
# It is one node and no `repo`: planning authors no target-project content, so
# nothing is published and no lifecycle worktree is cut. Everything else is
# `just orchestrate`'s path exactly — `onepipeline start` with this host's dag-scope
# graph — so the run gets a journal, an ownership row, planner surfaces, and a place
# in the DAG UI.
#
# `--name` and `--max-turns` are consumed here; every other flag is passed to
# `onepipeline start` untouched. `--dag-graph` is refused rather than passed on,
# because this recipe always names one and the flag cannot be given twice.
set -euo pipefail

#: Where a generated plan is written, under the gitignored scratch root. Kept in the
#: repository rather than in a temporary directory because it is the document the
#: launch is judged against: a manager reading back what their brief became, or
#: relaunching it with `just orchestrate`, needs the file to still be there.
PLAN_DIRECTORY="scratch/plans"

#: The persona ref the one node carries. A path, deliberately — see the header.
PLANNER_PERSONA="../personas/planner.yaml"

#: The dag-scope graph every run on this host is watched by, exactly as
#: `just orchestrate` names it.
DAG_GRAPH="graphs/dag-scope.yaml"

#: The one node's id. It is what `just status` and the DAG UI label the dispatch.
NODE_ID="plan"

#: The headings a brief has to carry, because the brief IS the dispatched task and a
#: task is written in this template. `## Acceptance criteria` is the load-bearing one:
#: it is the node's whole review bar — there is no second place to state one, and
#: `done_when` is refused at load — so a brief without it dispatches a planner against
#: the shared clause alone and is judged on nothing this manager asked for.
REQUIRED_SECTIONS=("## What" "## Why" "## Acceptance criteria")

#: Where `onepipeline` keeps its ledger, and so where a run id is already taken. The
#: same default and the same override every planner-facing verb reads, resolved
#: against the working directory exactly as they resolve it.
RUNS_ROOT_ENV="ONEPIPELINE_RUNS_DIR"
DEFAULT_RUNS_ROOT="runs"

#: The run this launch tells its dispatch it is under. Exported rather than left to
#: the driver: an attached launch dispatches from the process `onepipeline start`
#: became, which carries it, but a detached one dispatches from the `drive-run` it
#: spawns — measured, a worker there is given no run id at all, so the wrapper refuses
#: its question with `ONEPIPELINE_RUN_ID is not set`. Sound only beside the guarantee
#: above: exporting a name the engine would have rewritten would send a blocking
#: question to somebody else's live run.
RUN_ID_ENV="ONEPIPELINE_RUN_ID"

#: What this recipe will use as a plan name. Narrower than what
#: `scripts/ask-manager.sh` accepts as a run id, and deliberately so: `onepipeline`
#: mints the run id from the plan's `name` and NORMALIZES it on the way — measured,
#: `with.dots` becomes run `with-dots`, while case and `_` survive. Everything this
#: recipe prints and every verb it tells a manager to type names the run, so a name
#: that could be normalized would send them to a run id that does not exist. Refusing
#: the dot here is what keeps the name and the run id the same string.
SAFE_RUN_ID='^[A-Za-z0-9_][A-Za-z0-9_-]*$'

# Writes the one-node plan. The brief is read here and embedded verbatim: it IS the
# node's task, in the `## What` / `## Why` / `## Acceptance criteria` template every
# task this repository dispatches is written in, so anything that reformatted it
# would be editing the manager's words on the way to the planner.
PLAN_PROGRAM='
import json, pathlib, sys

name, brief, persona, node_id, turns = sys.argv[1:6]
task = pathlib.Path(brief).read_text(encoding="utf-8")
node = {"id": node_id, "persona": persona, "task": task}
if turns:
    node["max_turns"] = int(turns)
plan = {
    "schema_version": 2,
    "goal": {"text": f"Plan the work the manager briefed in {brief}"},
    "name": name,
    "tasks": [node],
}
sys.stdout.write(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
'

fail() {
    echo "plan: $1; $2" >&2
    exit 2
}

usage() {
    echo "usage: just plan <brief.md> [--name NAME] [--max-turns N] [<onepipeline start flags>]" >&2
}

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on.
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd) || fail "this recipe could not resolve the checkout it was run from" \
    "run it from a checkout, so the personas and graphs it names are that checkout's"

python="$script_dir/../.venv/bin/python3"
[ -x "$python" ] || python=python3

brief="${1:-}"
if [ -z "$brief" ]; then
    usage
    fail "no brief was named" \
        "write the planner's brief as a markdown file in the '## What' / '## Why' / '## Acceptance criteria' template, then name it here"
fi
case "$brief" in
    -*)
        usage
        fail "the first argument must be the brief, got the flag '$brief'" \
            "name the brief file first, then any flags after it"
        ;;
esac
if ! [ -f "$brief" ] || ! [ -r "$brief" ]; then
    fail "the brief '$brief' is not a readable file" \
        "check the path, or write the brief there first"
fi
[ -s "$brief" ] || fail "the brief '$brief' is empty, so the planner would be dispatched with no task" \
    "write what to plan, why it matters, and what the plan has to satisfy, then retry"
for section in "${REQUIRED_SECTIONS[@]}"; do
    grep -qF -- "$section" "$brief" || fail "the brief '$brief' states no '$section' section" \
        "a brief is the dispatched task, so write it in the '## What' / '## Why' / '## Acceptance criteria' template; the criteria are the planner's whole review bar"
done
shift

name=""
max_turns=""
forwarded=()
while [ $# -gt 0 ]; do
    case "$1" in
        --name)
            [ $# -ge 2 ] || fail "--name was given no value" "name the run, or omit --name to derive one from the brief's filename"
            name="$2"
            shift 2
            ;;
        --name=*)
            name="${1#--name=}"
            [ -n "$name" ] || fail "--name was given no value" "name the run, or omit --name to derive one from the brief's filename"
            shift
            ;;
        --max-turns)
            [ $# -ge 2 ] || fail "--max-turns was given no value" "give it a whole number of turns, or omit it for the persona's own budget"
            max_turns="$2"
            shift 2
            ;;
        --max-turns=*)
            max_turns="${1#--max-turns=}"
            [ -n "$max_turns" ] || fail "--max-turns was given no value" "give it a whole number of turns, or omit it for the persona's own budget"
            shift
            ;;
        --dag-graph | --dag-graph=*)
            fail "this recipe always launches on $DAG_GRAPH, and --dag-graph cannot be given twice" \
                "drop the flag, or write the plan yourself and launch it with 'just orchestrate'"
            ;;
        *)
            # Forwarded unvalidated, deliberately: every other flag is `onepipeline
            # start`'s, and it is the one thing that knows its own surface. A copy of
            # that flag list here would be exactly the drift
            # `tests/test_cli_surface_drift.py` exists to catch — and it would refuse a
            # flag a newer release added, turning a pass-through into a version pin.
            # llmlint: ignore[boundary_inputs_validated] `onepipeline start` validates its own surface; restating it here is the drift this repository gates against.
            forwarded+=("$1")
            shift
            ;;
    esac
done

if [ -z "$name" ]; then
    # Derived from the brief's filename rather than from its prose: a manager
    # renaming the brief is deliberate, and a heading is not.
    # llmlint: ignore[changed_behavior_has_e2e] Reachable only when `basename` or `tr` itself fails on a path this script already opened and read; driving it would mean breaking the coreutils the suite runs on.
    if ! derived=$(basename -- "$brief") || ! name=$(printf '%s' "${derived%.md}" | tr -c 'A-Za-z0-9_-' '-'); then
        fail "no run name could be derived from the brief '$brief'" \
            "pass --name to state one, or rename the brief"
    fi
fi
# llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally, so the check would accept nothing.
[[ "$name" =~ $SAFE_RUN_ID ]] || fail "'$name' is not a run id this launch can use" \
    "a run id is one word of letters, digits, '_', and '-' starting with a letter, digit, or '_'; pass --name to state one"
if [ -n "$max_turns" ]; then
    [[ "$max_turns" =~ ^[1-9][0-9]*$ ]] || fail "--max-turns is '$max_turns', which is not a positive whole number of turns" \
        "give it a count like 40, or omit it for the persona's own budget"
fi

# A run root that already exists is what makes `onepipeline` mint `<name>-2` instead,
# so the name this recipe prints and exports would name a different — possibly live —
# run belonging to another workstream, and a blocking question asked there would queue
# on a channel its own manager is not watching. Refused rather than worked around: the
# name is the manager's to choose, and this is the only place that knows it is taken
# before a run exists under it.
# The ledger itself is checked first, because otherwise the test below cannot tell an
# unused name from one this process is not allowed to look up: both leave `-e` false, and
# the second would launch under a name that is already somebody else's run.
runs_root="${!RUNS_ROOT_ENV:-$DEFAULT_RUNS_ROOT}"
if [ -e "$runs_root" ] && { [ ! -d "$runs_root" ] || [ ! -r "$runs_root" ] || [ ! -x "$runs_root" ]; }; then
    fail "the run ledger at $runs_root cannot be searched, so this launch cannot tell whether '$name' is already a run" \
        "fix its permissions, or point $RUNS_ROOT_ENV at a directory this launch can read, then retry"
fi
[ ! -e "$runs_root/$name" ] || fail "run '$name' already exists under $runs_root, so this launch would be given a different run id than the one it printed" \
    "pass --name with a run id nothing has taken yet, or read the existing run with 'just channel-next $name'"
export "$RUN_ID_ENV=$name"

ask_manager_helper="$script_dir/ask-manager-env.sh"
if [ ! -f "$ask_manager_helper" ] || [ ! -r "$ask_manager_helper" ]; then
    fail "required helper is not a readable regular file: $ask_manager_helper" \
        "restore it from the repository or run 'just bootstrap', then retry"
fi
# shellcheck source=scripts/ask-manager-env.sh
. "$ask_manager_helper"
export_ask_manager plan || exit $?

# Both checked rather than left to `set -e`, which would exit with whatever the
# helper printed and no repair — and, for the write, would leave a half-written plan
# behind for the next launch to pick up.
mkdir -p "$PLAN_DIRECTORY" || fail "the plan directory $PLAN_DIRECTORY could not be created" \
    "check that this checkout is writable, then retry"
plan="$PLAN_DIRECTORY/$name.plan.json"
"$python" -c "$PLAN_PROGRAM" "$name" "$brief" "$PLANNER_PERSONA" "$NODE_ID" "$max_turns" >"$plan" || {
    # `|| :` so a removal that fails cannot replace the diagnostic below with its own
    # exit; the partial plan is then named by that diagnostic rather than silently kept.
    rm -f "$plan" || :
    fail "the plan for '$brief' could not be written to $plan by $python" \
        "restore the pinned toolchain with 'just bootstrap', then retry"
}

echo "plan: wrote $plan; answer this planner's questions with: just channel-next $name" >&2

# Through the shared wrapper rather than `uv run` directly, because that is where a
# planner's identity is established: a run launched without it records `unknown`,
# and `just runs --mine` and `just stop` then disown it.
#
# A caller's own flags reach `onepipeline start` as they were typed. That verb is the
# one thing that knows its own surface, and a copy of its flag list here would both be
# the drift `tests/test_cli_surface_drift.py` exists to catch and turn a pass-through
# into a version pin.
# llmlint: ignore[boundary_inputs_validated] `onepipeline start` validates its own surface; restating it here is the drift this repository gates against.
# llmlint: ignore[tool_output_is_signal] This is `just orchestrate`'s attached launch with a plan written first: streaming the run as it goes is what a manager stays attached for, and the one line this script owns — the plan it wrote and the command that answers the planner — is printed above.
exec "$script_dir/onepipeline.sh" start "$plan" --dag-graph "$DAG_GRAPH" ${forwarded[@]+"${forwarded[@]}"}
