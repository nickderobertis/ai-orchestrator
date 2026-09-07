# shellcheck shell=bash
# The ONE source of what a manager's brief is, what a planning flow's options are, and
# what run ids that flow will use. Sourced by scripts/plan.sh and scripts/finish-plan.sh.
#
# The flow is two launches with a review between them — a planner authors the plan,
# `just review-plan` clears it, and only then is the design document written from
# reviewed content — so it is two scripts. What they share is everything *before* either
# of them acts: the brief is the same file, the options mean the same things, the
# placement defaults are the same pair, and the run ids one flow uses have to be decided
# together, because the second launch's name must be refused as taken before the first
# one is made rather than an hour later.
#
# A second copy of any of that is a launch path that can drift into refusing a brief the
# other accepts, or into printing a run id the engine then rewrites. So the grammar lives
# here once and each caller refuses the one option it does not own.
#
# Strict mode is established here rather than inherited, exactly as its three sibling
# helpers do: every function below decides whether a launch may proceed, and one that
# fell through would let it proceed on an unvalidated value.
set -euo pipefail

#: The headings a brief has to carry, because the brief IS the dispatched task and a
#: task is written in this template. `## Acceptance criteria` is the load-bearing one:
#: it is the node's whole review bar — there is no second place to state one, and
#: `done_when` is refused at load — so a brief without it dispatches a planner against
#: the shared clause alone and is judged on nothing this manager asked for.
PLAN_REQUIRED_SECTIONS=("## What" "## Why" "## Acceptance criteria")

#: The line a brief carries to name the plan the flow's later steps read, and the two
#: patterns that read it. A qualified id — `<source>:<project>` — because that is what
#: every plan store command is given, and an unqualified one names a project in no store.
#:
#: Declaring the line and validating its value are deliberately two steps. A brief is a
#: manager's prose and this is the one thing parsed out of it, so a line that announces a
#: plan project and then states an unusable one has to be refused as a bad value rather
#: than pass unnoticed as no declaration at all — the second reading is what would send a
#: manager looking for a line that is already there. The source half is a name, so it is
#: held to one; the project half is whatever that store calls a project, split at the
#: first colon exactly as `onetaskgraph` itself splits a qualified id.
PLAN_PROJECT_LINE="Plan project: <source>:<project>"
PLAN_PROJECT_DECLARATION='^Plan project:[[:space:]]*(.*[^[:space:]]|)[[:space:]]*$'
PLAN_PROJECT_QUALIFIED='^[A-Za-z0-9_.-]+:[^[:space:]]+$'

#: What a planning flow will use as a run name. Narrower than what
#: `scripts/ask-manager.sh` accepts as a run id, and deliberately so: `onepipeline`
#: mints the run id from the plan's `name` and NORMALIZES it on the way — measured,
#: `with.dots` becomes run `with-dots`, while case and `_` survive. Everything a flow
#: prints and every verb it tells a manager to type names the run, so a name that could
#: be normalized would send them to a run id that does not exist. Refusing the dot here
#: is what keeps the name and the run id the same string.
PLAN_SAFE_RUN_ID='^[A-Za-z0-9_][A-Za-z0-9_-]*$'

#: What the tail's own run is called, given the flow's name. A suffix rather than a
#: second `--name`, because the two runs are one flow: an operator who named the flow has
#: named both of its runs, and a tail run whose id could not be derived from the flow's
#: could not be refused as taken before the planner was launched.
PLAN_DESIGN_RUN_SUFFIX="-design"

#: Where `onepipeline` keeps its ledger, and so where a run id is already taken. The
#: same default and the same override every planner-facing verb reads, resolved
#: against the working directory exactly as they resolve it.
PLAN_RUNS_ROOT_ENV="ONEPIPELINE_RUNS_DIR"
PLAN_DEFAULT_RUNS_ROOT="runs"

#: The run a launch tells its dispatch it is under. Exported rather than left to the
#: driver: an attached launch dispatches from the process `onepipeline start` became,
#: which carries it, but a detached one dispatches from the `drive-run` it spawns —
#: measured, a worker there is given no run id at all, so the wrapper refuses its
#: question with `ONEPIPELINE_RUN_ID is not set`. Sound only beside the guarantee
#: :func:`plan_run_is_free` makes: exporting a name the engine would have rewritten would
#: send a blocking question to somebody else's live run.
# shellcheck disable=SC2034  # read by both launchers, which source this file.
PLAN_RUN_ID_ENV="ONEPIPELINE_RUN_ID"

#: The publication repository a flow's nodes carry, and the registered safety clone their
#: worktrees are cut from. Aliases rather than paths, because `onevcs` resolves an alias
#: through its own registry and the two hosts this repository dispatches from lay these
#: checkouts out differently — `config/onevcs.checkouts` is where each one's path is
#: declared, and where a host that has neither is already accounted for.
PLAN_DEFAULT_PUBLICATION_REPO="ai-orchestrator"
PLAN_DEFAULT_EXECUTION_CHECKOUT="ai-orchestrator-isolated"

#: What a `--direct` dispatch is told, appended to the brief as part of its own task.
#: Stated as the placement plus the one clause it is exempt from, in that order: a
#: dispatch reading only "may not commit" beside a bar demanding every change committed
#: holds two instructions of equal authority and resolves it by guessing, which is the
#: failure this text exists to end.
# shellcheck disable=SC2034  # read by both launchers, which source this file.
PLAN_DIRECT_PLACEMENT_NOTE="

## Additional info

**This dispatch works in a checkout it does not own, so it commits nothing.** It was
launched with \`--direct\`, which dispatches it into the shared canonical checkout that
several orchestrators use rather than into a worktree of its own. So it may write only to
gitignored paths, may not commit, and may not leave the checkout on any branch but its
base.

That exempts it from one clause of the shared completion bar every dispatch on this host
is judged against — \"with every change this dispatch made committed and nothing
half-applied left behind\". Here there is nothing to commit, and a clean \`git status\` is
the correct and complete outcome: what this dispatch writes to a gitignored path is the
deliverable, and committing it would be the failure rather than the proof."

# Refuse ``$2`` as a brief unless it is the dispatched task a planning flow can be made
# from. $1 names the calling launcher so its diagnostics stay attributable.
plan_brief_is_a_task() {
    local caller=${1:?plan_brief_is_a_task: the name of the calling launcher is required, so its diagnostics stay attributable; pass it as the first argument, then retry}
    local brief=${2-}
    local section
    if [ -z "$brief" ]; then
        echo "$caller: no brief was named; write the planner's brief as a markdown file in the '## What' / '## Why' / '## Acceptance criteria' template, then name it here" >&2
        return 2
    fi
    case "$brief" in
        -*)
            echo "$caller: the first argument must be the brief, got the flag '$brief'; name the brief file first, then any flags after it" >&2
            return 2
            ;;
    esac
    if ! [ -f "$brief" ] || ! [ -r "$brief" ]; then
        echo "$caller: the brief '$brief' is not a readable file; check the path, or write the brief there first" >&2
        return 2
    fi
    if [ ! -s "$brief" ]; then
        echo "$caller: the brief '$brief' is empty, so a dispatch would be given no task; write what to plan, why it matters, and what the plan has to satisfy, then retry" >&2
        return 2
    fi
    for section in "${PLAN_REQUIRED_SECTIONS[@]}"; do
        if ! grep -qF -- "$section" "$brief"; then
            echo "$caller: the brief '$brief' states no '$section' section; a brief is the dispatched task, so write it in the '## What' / '## Why' / '## Acceptance criteria' template; the criteria are the planner's whole review bar" >&2
            return 2
        fi
    done
}

# Echo the qualified plan project ``$2`` declares, or refuse it. Every line is read
# rather than stopping at the first, because two declarations are ambiguous and taking
# the earlier one silently points a later step at a plan its author may not have meant.
plan_brief_project() {
    local caller=${1:?plan_brief_project: the name of the calling launcher is required, so its diagnostics stay attributable; pass it as the first argument, then retry}
    local brief=${2-} line declared=0 project=""
    while IFS= read -r line || [ -n "$line" ]; do
        # llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally, so nothing would ever match.
        if [[ "$line" =~ $PLAN_PROJECT_DECLARATION ]]; then
            declared=$((declared + 1))
            project="${BASH_REMATCH[1]}"
        fi
    done < "$brief"
    if [ "$declared" -eq 0 ]; then
        echo "$caller: the brief '$brief' names no plan project, so nothing after the planner has a plan to read; add a line reading '$PLAN_PROJECT_LINE' naming the qualified project this plan is written to, or pass --no-design-doc to stop after the planner" >&2
        return 2
    fi
    if [ "$declared" -ne 1 ]; then
        echo "$caller: the brief '$brief' names $declared plan projects, so this flow cannot tell which one it is about; leave exactly one '$PLAN_PROJECT_LINE' line in it, naming the plan this brief is planning" >&2
        return 2
    fi
    # llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally, so nothing would ever match.
    if ! [[ "$project" =~ $PLAN_PROJECT_QUALIFIED ]]; then
        echo "$caller: the brief '$brief' names plan project '$project', which is not a qualified id and names a project in no store; write the line as '$PLAN_PROJECT_LINE' — a source name, a colon, and the project inside that source" >&2
        return 2
    fi
    printf '%s' "$project"
}

# Echo the run name a flow over ``$3`` runs under, given the caller's ``$2`` or nothing.
# Derived from the brief's filename rather than from its prose: a manager renaming the
# brief is deliberate, and a heading is not.
plan_run_name() {
    local caller=${1:?plan_run_name: the name of the calling launcher is required, so its diagnostics stay attributable; pass it as the first argument, then retry}
    local name=${2-} brief=${3-} derived
    if [ -z "$name" ]; then
        # llmlint: ignore[changed_behavior_has_e2e] Reachable only when `basename` or `tr` itself fails on a path this helper's caller already opened and read; driving it would mean breaking the coreutils the suite runs on.
        if ! derived=$(basename -- "$brief") || ! name=$(printf '%s' "${derived%.md}" | tr -c 'A-Za-z0-9_-' '-'); then
            echo "$caller: no run name could be derived from the brief '$brief'; pass --name to state one, or rename the brief" >&2
            return 2
        fi
    fi
    # llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally, so the check would accept nothing.
    if ! [[ "$name" =~ $PLAN_SAFE_RUN_ID ]]; then
        echo "$caller: '$name' is not a run id this launch can use; a run id is one word of letters, digits, '_', and '-' starting with a letter, digit, or '_'; pass --name to state one" >&2
        return 2
    fi
    printf '%s' "$name"
}

# Echo the run the design-document launch of a flow named ``$1`` runs under.
plan_design_run() {
    printf '%s%s' "${1-}" "$PLAN_DESIGN_RUN_SUFFIX"
}

# Refuse ``$2`` as a run id something has already taken.
#
# A run root that already exists is what makes `onepipeline` mint `<name>-2` instead, so
# the name a flow prints and exports would name a different — possibly live — run
# belonging to another workstream, and a blocking question asked there would queue on a
# channel its own manager is not watching. Refused rather than worked around: the name is
# the manager's to choose, and this is the only place that knows it is taken before a run
# exists under it.
plan_run_is_free() {
    local caller=${1:?plan_run_is_free: the name of the calling launcher is required, so its diagnostics stay attributable; pass it as the first argument, then retry}
    local name=${2-} runs_root
    runs_root="${!PLAN_RUNS_ROOT_ENV:-$PLAN_DEFAULT_RUNS_ROOT}"
    # The ledger itself is checked first, because otherwise the test below cannot tell an
    # unused name from one this process is not allowed to look up: both leave `-e` false,
    # and the second would launch under a name that is already somebody else's run.
    if [ -e "$runs_root" ] && { [ ! -d "$runs_root" ] || [ ! -r "$runs_root" ] || [ ! -x "$runs_root" ]; }; then
        echo "$caller: the run ledger at $runs_root cannot be searched, so this launch cannot tell whether '$name' is already a run; fix its permissions, or point $PLAN_RUNS_ROOT_ENV at a directory this launch can read, then retry" >&2
        return 2
    fi
    if [ -e "$runs_root/$name" ]; then
        echo "$caller: run '$name' already exists under $runs_root, so this launch would be given a different run id than the one it printed; pass --name with a run id nothing has taken yet, or read the existing run with 'just channel-next $name'" >&2
        return 2
    fi
}

# Read one planning flow's option grammar off "$@", leaving what it does not own alone.
#
# Every option below is shared by both entry points *as a grammar*, and each caller then
# refuses the one it does not own — `--max-turns` is the planner's alone and `--to` names
# a destination only the tail copies into. That is deliberate: one grammar read in one
# place cannot drift into accepting a spelling on one entry point and refusing it on the
# other, and a caller refusing its own non-option by name says more than a parser that
# forwarded it to a verb which has never heard of it.
#
# Answers through globals rather than through stdout because there are six of them and
# one is an array. $1 names the calling launcher so its diagnostics stay attributable.
plan_options_parse() {
    local caller=${1:?plan_options_parse: the name of the calling launcher is required, so its diagnostics stay attributable; pass it as the first argument, then retry}
    shift
    PLAN_OPT_NAME=""
    PLAN_OPT_MAX_TURNS=""
    PLAN_OPT_DESTINATION=""
    PLAN_OPT_REPO="$PLAN_DEFAULT_PUBLICATION_REPO"
    PLAN_OPT_EXECUTION="$PLAN_DEFAULT_EXECUTION_CHECKOUT"
    PLAN_OPT_DESIGN_DOC=1
    PLAN_OPT_FORWARDED=()
    while [ $# -gt 0 ]; do
        case "$1" in
            --name)
                if [ $# -lt 2 ] || [ -z "$2" ]; then
                    echo "$caller: --name was given no value; name the run, or omit --name to derive one from the brief's filename" >&2
                    return 2
                fi
                PLAN_OPT_NAME="$2"
                shift 2
                ;;
            --name=*)
                PLAN_OPT_NAME="${1#--name=}"
                if [ -z "$PLAN_OPT_NAME" ]; then
                    echo "$caller: --name was given no value; name the run, or omit --name to derive one from the brief's filename" >&2
                    return 2
                fi
                shift
                ;;
            --max-turns)
                if [ $# -lt 2 ] || [ -z "$2" ]; then
                    echo "$caller: --max-turns was given no value; give it a whole number of turns, or omit it for the persona's own budget" >&2
                    return 2
                fi
                PLAN_OPT_MAX_TURNS="$2"
                shift 2
                ;;
            --max-turns=*)
                PLAN_OPT_MAX_TURNS="${1#--max-turns=}"
                if [ -z "$PLAN_OPT_MAX_TURNS" ]; then
                    echo "$caller: --max-turns was given no value; give it a whole number of turns, or omit it for the persona's own budget" >&2
                    return 2
                fi
                shift
                ;;
            --to)
                if [ $# -lt 2 ] || [ -z "$2" ]; then
                    echo "$caller: --to was given no value; name the configured source this plan is copied into, or omit it for the board this repository plans against" >&2
                    return 2
                fi
                PLAN_OPT_DESTINATION="$2"
                shift 2
                ;;
            --to=*)
                PLAN_OPT_DESTINATION="${1#--to=}"
                if [ -z "$PLAN_OPT_DESTINATION" ]; then
                    echo "$caller: --to was given no value; name the configured source this plan is copied into, or omit it for the board this repository plans against" >&2
                    return 2
                fi
                shift
                ;;
            --repo)
                if [ $# -lt 2 ] || [ -z "$2" ]; then
                    echo "$caller: --repo was given no value; name the checkout this plan's branch publishes from, or omit it for '$PLAN_DEFAULT_PUBLICATION_REPO'" >&2
                    return 2
                fi
                PLAN_OPT_REPO="$2"
                shift 2
                ;;
            --repo=*)
                PLAN_OPT_REPO="${1#--repo=}"
                if [ -z "$PLAN_OPT_REPO" ]; then
                    echo "$caller: --repo was given no value; name the checkout this plan's branch publishes from, or omit it for '$PLAN_DEFAULT_PUBLICATION_REPO'" >&2
                    return 2
                fi
                shift
                ;;
            --execution-checkout)
                if [ $# -lt 2 ] || [ -z "$2" ]; then
                    echo "$caller: --execution-checkout was given no value; name the safety clone this flow's worktrees are cut from, or omit it for '$PLAN_DEFAULT_EXECUTION_CHECKOUT'" >&2
                    return 2
                fi
                PLAN_OPT_EXECUTION="$2"
                shift 2
                ;;
            --execution-checkout=*)
                PLAN_OPT_EXECUTION="${1#--execution-checkout=}"
                if [ -z "$PLAN_OPT_EXECUTION" ]; then
                    echo "$caller: --execution-checkout was given no value; name the safety clone this flow's worktrees are cut from, or omit it for '$PLAN_DEFAULT_EXECUTION_CHECKOUT'" >&2
                    return 2
                fi
                shift
                ;;
            --direct)
                # Both together, because a node carries the pair or neither:
                # `execution_checkout` without a `repo` names a clone nothing is cut from.
                PLAN_OPT_REPO=""
                PLAN_OPT_EXECUTION=""
                shift
                ;;
            --no-design-doc)
                # Consumed rather than forwarded: `onepipeline start` has no such option,
                # and a launch that passed it on would be refused by the verb rather than
                # by the caller's own opt-out.
                # shellcheck disable=SC2034  # read by both launchers, which source this file.
                PLAN_OPT_DESIGN_DOC=0
                shift
                ;;
            *)
                # Forwarded unvalidated, deliberately: every other flag is `onepipeline
                # start`'s, and it is the one thing that knows its own surface. A copy of
                # that flag list here would be exactly the drift
                # `tests/test_cli_surface_drift.py` exists to catch — and it would refuse a
                # flag a newer release added, turning a pass-through into a version pin.
                # llmlint: ignore[boundary_inputs_validated] `onepipeline start` validates its own surface; restating it here is the drift this repository gates against.
                PLAN_OPT_FORWARDED+=("$1")
                shift
                ;;
        esac
    done
    # The pair is checked after every option has been read rather than as each is read,
    # because they may arrive in any order and `--direct` clears both: a `--direct`
    # followed by one of them leaves half a placement, which names a clone nothing is cut
    # from or a checkout nothing publishes to.
    if { [ -n "$PLAN_OPT_REPO" ] && [ -z "$PLAN_OPT_EXECUTION" ]; } ||
        { [ -z "$PLAN_OPT_REPO" ] && [ -n "$PLAN_OPT_EXECUTION" ]; }; then
        echo "$caller: this launch would write a node placed at repo '${PLAN_OPT_REPO:-none}' and execution checkout '${PLAN_OPT_EXECUTION:-none}'; a node carries both or neither: name the missing one, or pass --direct alone for a dispatch that cuts no worktree" >&2
        return 2
    fi
}
