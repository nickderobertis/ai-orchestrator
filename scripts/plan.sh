#!/usr/bin/env bash
# Launch a planner on a manager-written brief and finish the plan it writes: `just plan
# <BRIEF.md> [--name NAME] [--max-turns N] [--to SOURCE] [--no-design-doc]
# [<onepipeline start flags>]`.
#
# The manager's job is writing the brief and reviewing what comes back, not
# assembling a plan project by hand. So this recipe writes the project, and the one shape
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
#     taken rather than by predicting what will be minted. **Both** of this flow's run
#     ids are refused that way, here, before anything is launched: the tail below runs a
#     second launch, and an hour of planning must not end at a name collision.
#   * **The operational appendix is exported into the launch environment**, as text
#     rather than as a path. Every dispatched task must carry it verbatim and the
#     planner is what copies it in, but a planner may plan against a checkout other
#     than the one holding the tracked file — so a path names a file it cannot open,
#     and the refusal it then met named that same path.
#   * **`ORCHESTRATOR_ASK_MANAGER` is exported into the launch environment**, holding
#     the path of `scripts/ask-manager.sh`, which is how the dispatched planner stops
#     and asks rather than guessing at a decision fork. A launch that dropped it would
#     produce exactly the confidently-wrong plan the ask channel exists to prevent.
#     Established through `scripts/ask-manager-env.sh`, which every launch path shares,
#     and taken here before anything is written so a checkout that cannot ask is
#     refused rather than left holding a plan.
#   * **The plan-authoring root is resolved once and exported**, so every dispatch of
#     this launch reads the same directory rather than resolving a relative source root
#     against whatever working directory it happens to have. `onetaskgraph.yaml` roots
#     the `authoring` source at the relative `.plans`, and a dispatch resolves that
#     against its own working directory — which, while the planner worked in a worktree
#     of its own, was that worktree's own copy of the directory, read by nothing outside
#     it and reclaimed with it. Established through `scripts/plan-root-env.sh`, which
#     is the one place that variable's name and value are composed, and taken here
#     before anything is written so a checkout whose authoring source is not a writable
#     root is refused rather than left holding a plan nothing will find. **The project
#     this recipe generates is written under that same resolved root**, rather than
#     under a `.plans` relative to the directory the recipe was invoked from: those name
#     one directory for an ordinary launch and two the moment a caller points the root
#     elsewhere, and the launch gate then refuses the plan the launch has just written.
#   * **The watch command is printed**, so arming it is one copy-paste rather than
#     something composed under time pressure. `just channel-next` and not `just
#     monitor`: rendering a surface is not reading it, and only `channel-next`
#     consumes one — a planner's blocking question is answered there or not at all.
#
# **It is one direct node, so the planner works in the checkout the launch was made
# from.** That is the one thing about this recipe that is not merely convenience, and it
# has been decided both ways, each time the expensive way. A node with no `repo` is a
# *direct* node, and a direct node works in the launch directory — which for this recipe
# is the shared canonical checkout the self-dispatch rule in `AGENTS.md` forbids
# authoring in, because concurrent orchestrators use it and direct edits race them. A
# planner dispatched that way once cut a branch in the canonical checkout, committed to
# it, and left it checked out; a finished lifecycle publication then failed at its last
# step because the publication checkout was on that branch rather than on its base, and
# returning it to the base deleted the manager's own plan files, which the planner had
# force-added onto its branch from gitignored paths. So the node moved to a lifecycle
# shape — `repo` plus `execution_checkout`, a worktree cut from the registered safety
# clone, exactly as every other node this repository dispatches — and stayed there until
# the adopted engine made that shape one a planner cannot settle under.
#
# **Why it is back on the direct shape**: onepipeline settles a lifecycle dispatch whose
# branch is level with its base `failed` as `empty-branch` unless the node declared
# `expects_no_diff` (https://github.com/nickderobertis/onepipeline/pull/229) — and that
# declaration settles a node **without dispatching it**, refusing one that carries a
# persona at all, so no lifecycle declaration covers a dispatched node that commits
# nothing (https://github.com/nickderobertis/onepipeline/issues/238). A planner is
# exactly that node: its whole deliverable is a plan-store record under the exported
# authoring root, and it leaves its branch level with the base by design. Under the
# lifecycle shape every `just plan` on the adopted engine settled `failed`, and so did
# every design-document launch in `scripts/finish-plan.sh`, which composes its node the
# same way. The direct shape is the honest model of what these two dispatches produce —
# a record, never a branch — and the incident above is answered in the task rather than
# by the placement: `PLAN_DIRECT_PLACEMENT_NOTE` is appended to the brief of every
# launch, stating that the dispatch works in a checkout it does not own, **may write only
# to gitignored paths, may not commit, may not cut a branch, and may not leave the
# checkout on any branch but its base**, and which clause of the shared completion bar
# that exempts it from. That last half is not decoration: the shared completion clause in
# `config/onejudge.base.yaml` demands "every change this dispatch made committed" of
# every dispatch alike, and a planner that did correct, verified work settled
# `task-failed` against exactly that before the note said otherwise. The shared clause is
# right, is shared, and does not move; the exemption belongs in the task of the dispatch
# it is true of.
#
# What a caller gives up is the choice of placement: `--repo`, `--execution-checkout`
# and `--direct` are refused by name in `scripts/plan-brief.sh`, because the first two
# would compose the shape that fails and the third named the only shape there is. The
# journeys that drive this recipe need no scratch identity for the launch itself any
# more — a direct node opens no session — and the authoring root still comes from the
# variable `scripts/plan-root-env.sh` exports, so the plan lands where the manager reads
# it whichever directory the planner stood in.
#
# The journal, the ownership row, the planner surfaces, and the run's place in the DAG
# UI are `onepipeline start`'s own — the ledger it writes and the channel it serves —
# so this launch gets every one of them exactly as any other launch does, and an agent
# graph produces none of them. What a dag-scope graph adds is the two observer members
# and nothing else: the monitor that compares a run against its plan, and the
# `check-in` pacemaker.
#
# So this recipe launches on `--dag-graph off`, which is also `onepipeline start`'s
# own shipped default and is named here to state the intent rather than to inherit
# it. A planning run's **output is the plan**, so a monitor attached to one is
# comparing the run against a document that does not exist yet — the one run on this
# host where watching it that way can say least. A caller who wants an observer names
# one and keeps it, per flag, exactly as `just orchestrate` keeps a caller's own.
#
# **The plan this launch writes is one node, and the rest of the flow is a second
# launch.** A person cannot usefully review a plan node by node; what they can judge is
# one short document — what is being built and why, the architecture, the contracts, the
# acceptance criteria, and the planned work as a table of links. That document has to be
# written from **reviewed** content, and a run cannot interject a review between its own
# nodes: a review record is written by this repository's own code and never by a
# dispatched agent. So the document is not a second node of this run. When the planner
# has settled and this launch's closeout has recorded what it authored, this hands over
# to `scripts/finish-plan.sh`, which reviews the plan, checks it, launches the document,
# copies both into the destination, and reports where that destination holds them. That
# script is the one implementation of every step after the plan is authored, and an
# operator who edited a plan afterwards runs it on its own.
#
# **That tail is why a brief has to name the plan's qualified project id.** Nothing hands
# one launch's output to the next, and a plan written to an ignored path in the planner's
# own worktree does not outlive the run, so the tail has no other way to find the plan it
# is finishing. A brief therefore carries a `Plan project: <source>:<project>` line and is
# refused without one, exactly as it is refused for missing a required section — and it is
# refused **here**, before the planner is dispatched, rather than an hour later by the
# tail. `--no-design-doc` opts out of the requirement with the tail.
#
# **A detached launch keeps the planner and leaves the tail to the operator.** `--detach`
# hands back the moment the run is recorded, so the plan does not exist yet and every step
# of the tail would be about a project nothing has written. The launch says so and prints
# the command that finishes it once the planner has settled.
#
# `--name`, `--max-turns`, `--to` and `--no-design-doc` are consumed here, and the three
# retired placement flags are refused by name; every other flag is passed to
# `onepipeline start` untouched, `--dag-graph` included.
set -euo pipefail

#: What the project this launch writes says about itself: it is the plan a *planning*
#: run is producing, rather than a plan to be executed. That is the one exemption from
#: the design-document approval every launch is otherwise refused without — a planning
#: run's output is the plan, and the document it is reviewed as does not exist until the
#: run has written it. Stamped as a fact the project states rather than left to be
#: recognised from its shape, so a project somebody wrote by hand is not quietly exempt.
#: `orchestrator/design_approval.py` is the one reader and states every name here.
#:
#: **It names the node this launch writes, and that is what bounds the exemption to this
#: launch.** The exemption was scoped to the *project* until it named them, so a project
#: that had ever been a planning project stayed exempt for the rest of its life — and a
#: plan a planner writes into this very project is executable work sitting in it. The
#: reader compares this list against the tasks the project holds and requires the two to
#: *agree*, so anything beyond them and anything named here the project does not hold both
#: end the exemption.
#:
#: `@NODES@` is substituted below with the node id this launch actually wrote. Drift in
#: either direction is safe: a node written and not named here is a task the stamp does
#: not account for, and a node named here and not written is a claim the project does not
#: hold, so the next launch of this project is *refused* rather than quietly exempted
#: either way — which every real `just plan` journey catches, since each one launches for
#: real and the gate runs on it.
PLANNING_PROJECT_METADATA='{"orchestrator.plan-kind": {"kind": "planning", "nodes": @NODES@}}'

#: Where a generated project's record sits *under* the plan-authoring root: the
#: `projects/` directory a local Markdown source keeps them in, beside the `tasks/` one.
#: The root itself is not named here — it is resolved at launch, below, because it is
#: the store's own answer rather than this script's, and a second spelling of it here is
#: exactly what used to send the write and the read to two different directories.
PLAN_RECORDS="projects"
#: Where a project's task records sit under that same root, which the cleanup below
#: reaches when a write fails partway.
PLAN_TASKS="tasks"
PLAN_SOURCE="authoring"

#: The persona ref the planner node carries. A path, deliberately — see the header.
PLANNER_PERSONA="../personas/planner.yaml"

#: The observer this launch attaches when the caller names none: nothing — see the
#: header for why a planning run in particular is the wrong run to watch that way.
#: Named rather than left to `onepipeline start`'s own default of `off`, so the
#: recipe's intent is in the launch it composes and a release that moved that default
#: cannot silently attach a monitor to every plan.
DEFAULT_DAG_GRAPH="off"

#: How that default is recognized as already named, so it is added only when the
#: caller named neither spelling: `--dag-graph` refuses to be given twice, and
#: appending one over the caller's own would refuse the launch outright.
DAG_GRAPH_FLAG="--dag-graph"

#: The flag that hands a launch back before its planner has written anything, and so the
#: one flag that decides whether the tail below can run at all. Recognized rather than
#: forwarded-and-forgotten: every step of the tail is about the plan the planner writes,
#: and running them against a run that has just started asks the store for a project
#: nothing has authored.
DETACH_FLAG="--detach"

#: The planner node's id. It is what `just status` and the DAG UI label the dispatch,
#: and what names that node's own record on disk — which is what lets the cleanup below
#: name the file it has to take back rather than sweeping the directory.
NODE_ID="plan"

# Writes the plan. The brief is read here and embedded verbatim: it IS the task, in the
# `## What` / `## Why` / `## Acceptance criteria` template every task this repository
# dispatches is written in, so anything that reformatted it would be editing the
# manager's words on the way to the dispatch. One thing is appended after it, never
# woven in, so the manager's own words are always the whole of what precedes it:
# `PLAN_DIRECT_PLACEMENT_NOTE`, which states where the dispatch works and which clause
# of the shared completion bar it is therefore exempt from. The node carries no `repo`,
# no `execution_checkout` and no `title`: it is a direct node, for the reason the header
# gives, and a direct node publishes nothing a subject could name.
PLAN_PROGRAM='
import json, pathlib, sys

(name, brief, persona, node_id, turns, direct_note) = sys.argv[1:7]
task = pathlib.Path(brief).read_text(encoding="utf-8")

# A direct node works in the launch directory, and the shared completion bar demands
# every change committed. Saying so in the task is the only place the dispatch and its
# judge both read it.
node = {"id": node_id, "persona": persona}
node["task"] = task.rstrip() + "\n\n" + direct_note.strip() + "\n"
if turns:
    node["max_turns"] = int(turns)
plan = {
    "schema_version": 3,
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
    echo "usage: just plan <brief.md> [--name NAME] [--max-turns N] [--to SOURCE] [--no-design-doc] [<onepipeline start flags>]" >&2
}

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on.
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd) || fail "this recipe could not resolve the checkout it was run from" \
    "run it from a checkout, so the personas and graphs it names are that checkout's"

python="$script_dir/../.venv/bin/python3"
[ -x "$python" ] || python=python3

# Every helper is checked rather than left to `set -e`, which would exit on one that is
# readable but does not load — a truncated or half-written file — with whatever bash
# printed and no repair, and, for the write below, would leave a half-written project
# behind for the next launch to pick up.
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

brief="${1:-}"
if ! plan_brief_is_a_task plan "$brief"; then
    usage
    exit 2
fi
shift

plan_options_parse plan "$@" || exit 2
if [ -n "$PLAN_OPT_MAX_TURNS" ]; then
    [[ "$PLAN_OPT_MAX_TURNS" =~ ^[1-9][0-9]*$ ]] || fail "--max-turns is '$PLAN_OPT_MAX_TURNS', which is not a positive whole number of turns" \
        "give it a count like 40, or omit it for the persona's own budget"
fi

name=$(plan_run_name plan "$PLAN_OPT_NAME" "$brief") || exit 2

# The observer default, composed from what was forwarded rather than consumed out of
# it: a caller's `--dag-graph` — either spelling, and including their own `off` —
# reaches `onepipeline start` as they typed it, and this adds one only when they named
# neither. The same pass reads whether this launch was detached, which decides whether
# the tail can run at all.
observer=("$DAG_GRAPH_FLAG" "$DEFAULT_DAG_GRAPH")
detached=0
# llmlint: ignore[robust_shell] `${a[@]+"${a[@]}"}` is the idiom for expanding a possibly-empty array under `set -u`, and only its `+` alternate-value part is unquoted — the value it expands to is `"${a[@]}"`, so every element stays one argument. Measured: an array of `one two`, `*` and the empty string expands to exactly those three arguments, and an empty array expands to none. shellcheck, which this repository's `lint` target runs over every script, accepts it.
for argument in ${PLAN_OPT_FORWARDED[@]+"${PLAN_OPT_FORWARDED[@]}"}; do
    case "$argument" in
        "$DAG_GRAPH_FLAG" | "$DAG_GRAPH_FLAG"=*) observer=() ;;
        "$DETACH_FLAG") detached=1 ;;
    esac
done

# The whole of what this parses out of a brief, and it is parsed for the tail rather
# than for this launch: everything else in that file is the manager's prose and reaches
# the dispatch untouched. Read after the flags rather than beside the section checks
# above, because `--no-design-doc` decides whether it is required at all — with no tail
# there is nothing that needs one, so demanding it would refuse a launch that has no use
# for the answer.
tail_arguments=()
if [ "$PLAN_OPT_DESIGN_DOC" -eq 1 ]; then
    plan_brief_project plan "$brief" >/dev/null || exit 2
    # Both of this flow's run ids, refused as taken here rather than by the tail an hour
    # from now. The tail derives its own the same way, from the same helper, so what is
    # checked is the id it will actually use.
    plan_run_is_free plan "$(plan_design_run "$name")" || exit 2
    tail_arguments+=(--name "$name")
    [ -z "$PLAN_OPT_DESTINATION" ] || tail_arguments+=(--to "$PLAN_OPT_DESTINATION")
elif [ -n "$PLAN_OPT_DESTINATION" ]; then
    fail "--to names the destination the tail copies this plan into, and --no-design-doc stops the flow before there is anything to copy" \
        "drop one of the two: --to alone finishes the plan into that destination, and --no-design-doc alone launches the planner and stops"
fi

plan_run_is_free plan "$name" || exit 2
export "$PLAN_RUN_ID_ENV=$name"

# shellcheck source=scripts/credentials-env.sh
load credentials-env.sh
export_host_credentials plan || exit "$?"
# shellcheck source=scripts/ask-manager-env.sh
load ask-manager-env.sh
export_ask_manager plan || exit "$?"
# shellcheck source=scripts/plan-root-env.sh
load plan-root-env.sh
export_plan_authoring_root plan || exit "$?"
# shellcheck source=scripts/dispatch-appendix-env.sh
load dispatch-appendix-env.sh
export_dispatch_appendix plan || exit "$?"

# The root the helper above resolved, which is where this launch writes its project and
# where everything downstream of it then looks: `onepipeline start` below, the review
# snapshot beside it, and the closeout that reads what the run authored all resolve the
# `authoring` source through the store, and the store answers with this directory
# because this launch put it in the environment.
#
# It replaces a `.plans` relative to whatever directory the recipe was invoked from.
# Those are the same directory for an ordinary launch — the resolved root is this
# checkout's own `.plans` — and they parted exactly when a caller had pointed the root
# elsewhere: the launch wrote its project to the relative path and `onepipeline start`
# then looked for it under the configured one, so the launch gate refused a plan that
# had just been written and nothing was dispatched.
plan_root=${!PLAN_AUTHORING_ROOT_ENV}
plan_directory="$plan_root/$PLAN_RECORDS"

# Checked rather than left to `set -e`, which would exit with whatever the helper
# printed and no repair.
mkdir -p "$plan_directory" || fail "the plan directory $plan_directory could not be created" \
    "check that the plan-authoring root is a directory this launch may write into, then retry"
plan="$plan_directory/$name.md"
plan_task_records="$plan_root/$PLAN_TASKS/$name"
planning_metadata="${PLANNING_PROJECT_METADATA//@NODES@/[\"$NODE_ID\"]}"
"$python" -c "$PLAN_PROGRAM" "$name" "$brief" "$PLANNER_PERSONA" "$NODE_ID" "$PLAN_OPT_MAX_TURNS" \
    "$PLAN_DIRECT_PLACEMENT_NOTE" \
    | "$python" -m orchestrator.project_store "$plan_root" "$planning_metadata" >/dev/null || {
    # Reported rather than swallowed, and reported without ending the launch here: what
    # the operator has to act on is the write that failed, which the diagnostic below
    # names, and a removal that failed on top of it leaves records the next launch would
    # read — so it earns its own line naming them, and the refusal still comes last. The
    # one task record is named rather than matched by a pattern: this project has exactly
    # one node and its id is what that file is called, so there is nothing to sweep.
    rm -f "$plan" "$plan_task_records/$NODE_ID.md" ||
        echo "plan: part of the half-written plan could not be removed; delete $plan and $plan_task_records by hand, or the next launch of '$name' reads what this one left" >&2
    # This one is *expected* to fail whenever the directory is absent or still holds a
    # record the removal above could not take, and both are already reported by that
    # line, so its own failure is not a second thing to tell anybody about.
    rmdir "$plan_task_records" 2>/dev/null || :
    fail "the plan for '$brief' could not be written to $plan by $python" \
        "restore the pinned toolchain with 'just bootstrap', then retry"
}

# One line, and the placement is in it rather than beside it: where this planner works
# decides what a brief may ask it to leave behind, so a manager reading the receipt is
# the reader who needs it — and a second line on a successful launch is noise the next
# reader learns to skip.
placement="it is a direct node dispatched into this checkout, which concurrent orchestrators share, so it may write only to gitignored paths, may not commit, and may not leave the base branch"
# What a detached launch owes beside the receipt, folded into that one line rather than
# printed after the launch: `--detach` hands back before the plan exists, so the tail
# cannot run and the operator has to run it themselves once the planner has settled. It is
# known here, before anything is launched, and a second success line is noise the next
# reader learns to skip.
handover=""
if [ "$detached" -eq 1 ] && [ "${#tail_arguments[@]}" -ne 0 ]; then
    handover="; $DETACH_FLAG hands back before the planner has written anything, so once run $name has settled, finish the plan with: just finish-plan $brief ${tail_arguments[*]}"
fi
project="$PLAN_SOURCE:$name"
# Named relative to the directory this launch was made from when the plan is under it,
# which for an ordinary `just plan` is this checkout and the line a manager already
# reads, and absolute otherwise. Both halves are the same claim — where the plan is —
# and a path spelled relative to a directory it is not under names nothing, which is
# what a caller who has pointed the plan-authoring root elsewhere would be handed.
case "$plan" in
    "$PWD"/*) written=${plan#"$PWD"/} ;;
    *) written=$plan ;;
esac
echo "plan: wrote $project at $written; $placement; answer this planner's questions with: just channel-next $name$handover" >&2

# The snapshot `record_projects_new_since` is taken against; its docstring says what
# the window does and does not cover. Placed after the brief project is written, and
# deliberately: a manager wrote that one, no planner reviewed it, and `just check-plan`
# is right to go on refusing it.
# llmlint: ignore[changed_behavior_has_e2e] A host failure: `mktemp` refusing on a full or unwritable temporary filesystem. Driving it means breaking the filesystem the journey itself runs on.
snapshot=$(mktemp) || fail "the review snapshot could not be opened" \
    "free disk space and retry"
# The snapshot holds no secret and nothing reads it after this launch, so a removal
# that fails costs one stale file in the temporary directory rather than anything a
# caller has to act on now — but it is said rather than swallowed, because the next
# reader of a full temporary filesystem should know what put a file there.
trap 'rm -f "$snapshot" || echo "plan: the review snapshot at $snapshot could not be removed; delete it by hand once this launch has finished" >&2' EXIT
# llmlint: ignore[changed_behavior_has_e2e] The snapshot lists each plan source's own root, so what can fail it is the store's configuration being unreadable or the pinned toolchain being absent from a checkout this script has already resolved — neither reachable from a journey that is not breaking the host it runs on.
"$python" -m orchestrator.plan_review snapshot "$snapshot" || fail \
    "the plan review snapshot could not be taken by $python" \
    "restore the pinned toolchain with 'just bootstrap', then retry"

# Through the shared wrapper rather than `uv run` directly, because that is where a
# planner's identity is established: a run launched without it records `unknown`,
# and `just runs --mine` and `just stop` then disown it.
#
# A caller's own flags reach `onepipeline start` as they were typed, with this
# recipe's observer default after them and only when they named none. That verb is the
# one thing that knows its own surface, and a copy of its flag list here would both be
# the drift `tests/test_cli_surface_drift.py` exists to catch and turn a pass-through
# into a version pin.
#
# Run rather than `exec`ed, which it was until this launch grew a closeout: a planning
# run that settles successfully has produced a plan its own judge has already read, so
# the tasks it authored are recorded here rather than left for `just review-plan` to
# spend a second judged turn on. A run that did not settle successfully records nothing,
# and a planner working in its own worktree — which is the default placement — writes
# its plan somewhere this snapshot never saw, so there is simply nothing to record.
#
# A closeout that fails carries its own status out, rather than being swallowed under a
# green launch: what it failed to do is record what this run authored, and a plan
# silently left unrecorded is one whose next `just check-plan` refuses it with nothing
# to say why. What does *not* fail it is a plan it cannot record — that one is named and
# left alone, because a closeout cannot tell its own run's output from a neighbour's and
# an unrelated plan may not kill this launch. Both endings that a journey can reach
# without breaking the host are driven — a settled run recording what it authored, and
# an unsettled one recording nothing — in tests/e2e/test_plan_review_e2e.py.
status=0
# One directive rather than two stacked ones: a directive's scope is the line under it, so the upper of a stacked pair covers the lower and never the command, and the judge reported whichever of the two it had stranded.
# llmlint: ignore[boundary_inputs_validated, tool_output_is_signal, robust_shell] `onepipeline start` validates its own surface and restating it here is the drift this repository gates against; this is `just orchestrate`'s attached launch with a plan written first, so streaming the run as it goes is what a manager stays attached for — the one line this script owns, the plan it wrote and the command that answers the planner, is printed above; and the two array expansions are the `set -u` idiom whose `+` part alone is unquoted, measured to keep `one two`, `*` and the empty string each one argument.
"$script_dir/onepipeline.sh" start "$project" ${PLAN_OPT_FORWARDED[@]+"${PLAN_OPT_FORWARDED[@]}"} ${observer[@]+"${observer[@]}"} || status=$?
if [ "$status" -eq 0 ]; then
    # llmlint: ignore[changed_behavior_has_e2e] The one ending left is a settled run whose closeout then fails outright, which now takes an unreadable snapshot or an unreadable review bar rather than any plan on disk; a project it cannot record is passed over instead, which `tests/test_plan_review.py` drives.
    "$python" -m orchestrator.plan_review closeout "$snapshot" || status=$?
fi
if [ "$status" -ne 0 ] || [ "${#tail_arguments[@]}" -eq 0 ]; then
    exit "$status"
fi

# The rest of the flow, in the one place it is implemented. It is reached only on a
# launch that settled and recorded what it authored, because every step of it is about
# the plan the planner wrote: reviewing a project nothing has written, or writing a
# document about one, is not a cheaper version of this — it is a refusal an hour after
# the manager stopped watching.
# A detached launch has already been told how to finish the plan, on the one receipt line
# above: the tail is about the plan the planner writes, and this hands back before there
# is one.
[ "$detached" -eq 0 ] || exit 0
# The tail's own lines are its product rather than a second success report of this one:
# it is a second launch, and the run id it prints is the only place that run's channel is
# named — a supervisor holding this output has to be able to reach both. The two locations
# it ends with are what a person opens to review the plan.
# llmlint: ignore[tool_output_is_signal] see the note above this line
exec "$script_dir/finish-plan.sh" "$brief" "${tail_arguments[@]}"
