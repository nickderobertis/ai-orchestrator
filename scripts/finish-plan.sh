#!/usr/bin/env bash
# Finish a plan a planner has authored: `just finish-plan <BRIEF.md> [--to SOURCE]
# [--name NAME] [--no-design-doc] [<onepipeline start flags>]`.
#
# This is the tail of the planning flow and its one implementation. `just plan` runs the
# planner and its review closeout and then delegates to this; an operator who edited a
# plan after it was authored runs this on its own. Both reach one copy of the sequence,
# so a change to what these steps do takes effect in both without being written twice.
#
# **The order is the whole point, and it is why the design document is written by a
# second launch rather than by a second node of the planner's own run.** A run cannot
# interject a review between its own nodes: a review record is written by this
# repository's own code and never by a dispatched agent — a worker runs in a worktree and
# nothing it writes below the plan root is tracked — and a `kind: human` node is reserved
# for an action an external person performs rather than for a manager's own validation.
# So the flow is two launches with the review between them:
#
#   1. **review** the plan, which is `just review-plan`'s judged turn over any task whose
#      authored content carries no record. After a `just plan` this is free and silent:
#      that launch's closeout already recorded what its own planner authored. After a
#      manager's edit it is not, and that is the case this ordering exists for — an edit
#      leaves the task unreviewed, and a document written before the review describes
#      content nobody read;
#   2. **check** the plan the way its launch will, so a plan that would be refused costs
#      no document dispatch;
#   3. **launch** the design-document node, as its own one-node planning project;
#   4. **copy** the plan and its documents into the destination a person reviews them in;
#   5. **report** where that destination holds the project and the document, read back
#      out of the store rather than composed from a name.
#
# **Every run id this flow will use is decided before anything is launched.** `just plan`
# resolves this flow's name the same way and refuses the design run's id as taken before
# it makes the planner's launch, so an hour of planning cannot end at a name collision.
# The id is printed beside the command that answers that run's questions, so a supervisor
# holding only this output can reach either run's channel without composing one.
#
# **The refusals are told apart by exit status**, because a builder that could not tell
# them apart would retry one as the other: 1 is the review refusing the plan's own
# criteria, 3 is the pre-launch check refusing the plan, 5 is the destination refusing
# the copy, 4 is the design-document launch not settling, and 2 is a flow that could not
# run at all. There is no repair loop here and there will not be one: the planner's own
# judge is the repair loop and it has already run, so a refusal hands every refused
# criterion back and stops.
#
# `--to` names the destination and defaults to the board this repository plans against;
# `--name` means what `scripts/plan.sh` gives it; `--no-design-doc` stops the flow; and
# the three retired placement flags are refused by name, because the design-document
# node is a **direct** node exactly as the planner's is and for the same reason —
# `scripts/plan.sh`'s header holds it. Every other flag reaches `onepipeline start`
# untouched.
#
# llmlint: ignore-file[changed_behavior_has_e2e] What this script *decides* — the order of
# the five steps, which of them each refusal stops at, the exit status each ends on, the
# one-node project the design launch runs, the file that launch's judge is pointed at (and
# the plan outside the authoring source whose judge is pointed at none), and the two
# locations it reports — is driven end
# to end in tests/plan_tooling/test_finish_plan_recipe_e2e.py against real stores and a real
# launch, and by tests/plan_tooling/test_plan_flow_e2e.py through `just plan`. One guard is
# uncovered for another reason and says why at its own site: the refusal of a plan id whose
# path component would leave the documents directory, which the store refuses first. Past
# it, what remains uncovered is one kind of thing and only that kind: guards over a broken checkout, which
# every step here carries because it runs from a worktree or a publication clone that may
# not be provisioned. A missing or unloadable helper, an unprovisioned plan-store CLI, an
# unwritable plan-authoring root, and a verb that cannot run at all are each driven one
# command earlier against the same helper — in tests/ask_seam/test_launch_ask_seam_e2e.py
# and tests/plan_tooling/test_plan_write_refusals_e2e.py, over `scripts/plan.sh`, which
# sources the same files and writes through the same root. Reaching one of them through
# *this* entry point means first getting a plan past a judged review and the plan check and
# only then breaking the filesystem or the toolchain under it, which is a journey about
# `mkdir` and `uv` rather than about this flow.
set -euo pipefail

#: What the project this launch writes says about itself: it is the plan a *planning*
#: run is producing, rather than a plan to be executed. That is the one exemption from
#: the design-document approval every launch is otherwise refused without — and here it
#: is exactly right, because this project's one node is what writes a design document at
#: all. `@NODES@` is substituted below with the one node id this launch writes, which is
#: what bounds the exemption to this launch: the reader requires the stamp and the
#: project's own tasks to agree, so a project that grew a second node is gated like any
#: other. `orchestrator/design_approval.py` is the one reader and states every name here.
PLANNING_PROJECT_METADATA='{"orchestrator.plan-kind": {"kind": "planning", "nodes": @NODES@}}'

#: Where a generated project's record sits *under* the plan-authoring root: the
#: `projects/` directory a local Markdown source keeps them in, beside the `tasks/` one.
#: The root itself is not named here — it is resolved at launch, below, because it is
#: the store's own answer rather than this script's.
PLAN_RECORDS="projects"
#: Where a project's task records sit under that same root, which the cleanup below
#: reaches when a write fails partway.
PLAN_TASKS="tasks"
PLAN_SOURCE="authoring"
#: Where a local Markdown source keeps its documents under that same root, beside the two
#: directories above: flat, one file per document id, every project's documents together.
PLAN_DOCUMENTS="documents"

#: The document id the design-doc dispatch stores its document under: the plan's own
#: native id with this appended, which is the name every design document on this host
#: already carried before anything asked for it. It is fixed in the task rather than left
#: to the dispatch because the judge below is pointed at one file, and a document stored
#: under any other name is one that judge is told does not exist.
DESIGN_DOC_ID_SUFFIX="-design"

#: How the design-doc dispatch's judge is told where its subject is. The document is
#: written under the gitignored plan-authoring root, so the only evidence a judge can ask
#: for about the tree — `git_status` and `git_diff` — can never show it: one dispatch of
#: this role asked `git_status` six turns running about a document it could not see, and
#: spent its evidence-tool retries doing so. onejudge's `user.artifacts` names paths the
#: judge-side prompts list to be read with the file-reading tools instead, and this is
#: that list's environment override.
#:
#: The override rather than a `user.artifacts` entry in `personas/design-doc.yaml`,
#: because only the override can name what a real launch writes. A persona is one static
#: file for every launch, so it could name the documents directory at most — every
#: project's documents, listed to a judge reviewing one of them — and a relative entry
#: there resolves against the judge's worktree, which is this checkout, so it would stop
#: naming the authoring root the moment that root is pointed anywhere else. This launch
#: knows the resolved root and the project, so it names the one file. The judge's
#: evidence-tool retry limit is deliberately left alone: this gives the judge a subject it
#: can see, not more attempts at one it cannot.
JUDGE_ARTIFACTS_ENV="ONEJUDGE_ARTIFACTS"

#: The node this launch writes, which is what `just status` and the DAG UI label the
#: dispatch. It is also what names that record on disk, because a local Markdown source
#: keeps one task per node id — which is what lets the cleanup below name the file it
#: has to take back rather than sweeping the directory.
DESIGN_DOC_NODE_ID="design-doc"

#: The persona ref that node carries. A path, deliberately: a bare `design-doc` resolves
#: against the roles compiled into `oneagentgraph`, which has no such role, so the node
#: would be refused rather than silently mis-run — but a path is what actually reaches
#: `personas/design-doc.yaml`, and paths are resolved against `graphs/`.
DESIGN_DOC_PERSONA="../personas/design-doc.yaml"

#: The node-scope agent graph that node is dispatched under, resolved against the
#: directory the run is launched from rather than against `graphs/`. It differs from the
#: shipped `graphs/node-scope.yaml` in the two harness configs and nothing else: Codex
#: leads the side that writes this document, the Claude subscriptions lead the side that
#: reviews whether it reads plainly to somebody outside the domain.
DESIGN_DOC_GRAPH="graphs/design-doc.yaml"

#: The one statement of what the document contains, who it is for, and what it is judged
#: on. Named in the dispatched task rather than left to the persona alone, because the
#: task is the only text a worker and its judge both read; the persona names the same
#: path, and neither restates the file.
DESIGN_DOC_TEMPLATE="config/design-doc-template.md"

#: The pair of markers in that file bounding the block this flow lends the dispatch.
#: One property is composed into the design-doc node's own acceptance criteria rather
#: than left behind the pointer above, because a document written from the pointer alone
#: kept missing it: a plan over four repositories named each piece by role and no
#: repository at all. The template stays the single source — what is composed are its own
#: bytes, read at launch — and a template this flow cannot find the pair in refuses the
#: launch rather than composing a task with the requirement silently missing.
DESIGN_DOC_LIFT_OPEN="<!-- composed-into-the-dispatch -->"
DESIGN_DOC_LIFT_CLOSE="<!-- end composed-into-the-dispatch -->"

#: Where those lifted bytes go in the task below. Named rather than spelled at the splice,
#: because the splice reads it three times — twice to cut the instructions around it and
#: once to count it — and three spellings of one placeholder is a typo that fills nothing.
DESIGN_DOC_LIFT_PLACEHOLDER="@ARCHITECTURE@"

#: The observer this launch attaches when the caller names none: nothing. A one-node run
#: that reads a finished plan and writes one document has no frontier for a monitor to
#: compare against a plan, and this launch is the *last* step of a flow rather than the
#: work it supervises. Named rather than left to `onepipeline start`'s own default of
#: `off`, so a release that moved that default cannot silently attach a monitor here.
DEFAULT_DAG_GRAPH="off"

#: How that default is recognized as already named, so it is added only when the caller
#: named neither spelling: `--dag-graph` refuses to be given twice, and appending one
#: over the caller's own would refuse the launch outright.
DAG_GRAPH_FLAG="--dag-graph"

#: What the design-doc node is told, appended after the brief as the rest of its task.
#: `@PLAN_PROJECT@`, `@TEMPLATE@` and `@ARCHITECTURE@` are substituted below —
#: placeholders rather than `printf` conversions, because two of them appear twice and a
#: format string reused per argument is how a multi-placeholder template comes out
#: interleaved.
#:
#: It opens by disowning the criteria above it, and that is the load-bearing sentence.
#: The brief is the planner's, so its `## Acceptance criteria` state what the PLAN has to
#: satisfy — and a judge reading a task holds the dispatch to every criterion it finds in
#: one. A design-doc dispatch judged against the plan's criteria is one that cannot pass,
#: because producing the plan was somebody else's node.
#:
#: What it does NOT do is restate the document: `config/design-doc-template.md` is the
#: one statement of the shape, the reader, and every property the document is judged on,
#: and a second copy here would be the copy a writer follows on the day the two drift.
#: `@ARCHITECTURE@` is filled from that file's own bytes rather than typed here, which is
#: why it is no exception to that; the lift itself is documented at the marker constants
#: above. It lands among the criteria rather than in the prose because a criterion is what
#: this dispatch's judge reads.
DESIGN_DOC_INSTRUCTIONS="

## What this dispatch owes

**Everything above is the brief a planner was given, and none of it is this dispatch's
acceptance criteria.** Writing the plan was another node's job and it is already done.
The brief is here because the document opens with what is being built and why, and the
manager's own words are where those two come from. Where anything above and anything
below disagree about what this dispatch owes, below wins.

What this dispatch owes is the one short document a person reviews that plan as, instead
of reading it node by node.

Read the whole plan out of the plan store — the project record and every one of its
tasks. The plan is
\`@PLAN_PROJECT@\`.
\`onetaskgraph\` is that store's command line, and \`--help\` documents what it can do.

The document itself is stated in the \`ai-orchestrator\` orchestration repository, at
\`@TEMPLATE@\`.
That file states its sections, their order, the reader it is written for, and every
property it is judged on, and it is the only statement of any of that — so read it before
writing anything and follow it exactly, and where anything else disagrees with it about
the document, that file wins.

## Acceptance criteria for this dispatch

One of these is quoted out of that template rather than written here, because that file
is the one statement of what the document is judged on. It is the property a plan across
repositories turns on, and it is in this task so that this dispatch is held to it here
rather than only behind the pointer above.

- One document exists, written to that template: its sections, in that file's order, and
  no others, satisfying every property it states of them.
@ARCHITECTURE@
- That document is stored as a document of that plan's own project, in the same plan
  store the plan itself is in, under the document id \`@DOCUMENT_ID@\`, so a reader finds
  it beside the plan rather than in a directory only this dispatch knows about.
- Every task of that plan has one row in the document's planned-tasks table, and each row
  points at its task using the location the store reports for that task — read back out
  of the store, never composed by hand.
- This dispatch reports where the stored document is, in the form the store reports it: a
  link where the store puts it on a website, a path where it puts it in a file on this
  machine.
- Every claim this dispatch makes about the finished work is true of the tree as it
  finally stands."

#: The exit statuses this flow answers with, which are its whole contract to a caller
#: that reads only the status. Every one of them is a *different next action*: correct
#: the criteria a reviewer named, correct the plan a check refused, read why a launch did
#: not settle, repair the destination, or repair this checkout.
REVIEW_REFUSED=1
UNRUNNABLE=2
PLAN_REFUSED=3
LAUNCH_FAILED=4
COPY_REFUSED=5

#: Writes the one-node project this launch runs. The brief is read here and embedded
#: verbatim: it IS the opening of the task, in the `## What` / `## Why` / `## Acceptance
#: criteria` template every task this repository dispatches is written in, so anything
#: that reformatted it would be editing the manager's words on the way to the dispatch.
#: Two things are appended after it, in this order and never woven in, so the manager's
#: own words are always the whole of what precedes them: the instructions that are the
#: rest of this node's task and the criteria it is judged against, and the note stating
#: where the dispatch works and which clause of the shared completion bar it is therefore
#: exempt from. The node carries no `repo`, no `execution_checkout` and no `title`: it is
#: a direct node, for the reason `scripts/plan.sh`'s header gives, and a direct node
#: publishes nothing a subject could name.
PLAN_PROGRAM='
import json, pathlib, sys

(name, brief, node_id, persona, graph, direct_note, design_task) = sys.argv[1:8]
task = pathlib.Path(brief).read_text(encoding="utf-8").rstrip()
task = task + "\n\n" + design_task.strip() + "\n"

# A per-node agent graph rather than the run-wide default: this role pairs its two sides
# the other way round from every other dispatch on this host, and that reversal is a
# property of the node rather than of the run.
node = {"id": node_id, "persona": persona, "agent_graph": graph}
# A direct node works in the launch directory, and the shared completion bar demands
# every change committed. Saying so in the task is the only place the dispatch and its
# judge both read it.
node["task"] = task.rstrip() + "\n\n" + direct_note.strip() + "\n"
plan = {
    "schema_version": 3,
    "goal": {"text": f"Write the design document for the plan the manager briefed in {brief}"},
    "name": name,
    "tasks": [node],
}
sys.stdout.write(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
'

fail() {
    echo "finish-plan: $1; $2" >&2
    exit "$UNRUNNABLE"
}

# Run one of this repository's own recipes, from wherever this script was invoked.
#
# Through `just` rather than through the command each recipe wraps, because the recipe is
# where that step is defined: `just copy-plan` heals this checkout's plan-store CLI before
# it copies, and a caller that reached past it to the copy alone would be a second
# definition of what copying a plan does — which agrees on the day it is written and
# stops agreeing the first time either moves.
#
# The justfile and the working directory are named rather than discovered, because `just`
# finds a justfile by walking up from the *caller's* directory and this script is run from
# wherever an operator happened to be.
recipe() {
    just --justfile "$script_dir/../justfile" --working-directory "$script_dir/.." "$@"
}

usage() {
    echo "usage: just finish-plan <brief.md> [--to SOURCE] [--name NAME] [--no-design-doc] [<onepipeline start flags>]" >&2
}

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on.
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd) || fail "this recipe could not resolve the checkout it was run from" \
    "run it from a checkout, so the personas and graphs it names are that checkout's"

python="$script_dir/../.venv/bin/python3"
[ -x "$python" ] || python=python3

# Every helper is checked rather than left to `set -e`, which would exit on one that is
# readable but does not load — a truncated or half-written file — with whatever bash
# printed and no repair.
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
if ! plan_brief_is_a_task finish-plan "$brief"; then
    usage
    exit "$UNRUNNABLE"
fi
shift

plan_options_parse finish-plan "$@" || exit "$UNRUNNABLE"
# The one option of the shared grammar this entry point does not own. Refused by name
# rather than forwarded to a verb that has never heard of it: the design-document node
# takes its persona's own budget, because a document is one read and one write rather
# than an open-ended search, and a caller who wanted to widen the *planner's* budget has
# named the wrong command.
[ -z "$PLAN_OPT_MAX_TURNS" ] || fail "--max-turns names the planner's budget, and this command launches no planner" \
    "the design-document dispatch takes its persona's own budget; drop the flag, or pass it to 'just plan'"

name=$(plan_run_name finish-plan "$PLAN_OPT_NAME" "$brief") || exit "$UNRUNNABLE"
design_run=$(plan_design_run "$name")

if [ "$PLAN_OPT_DESIGN_DOC" -eq 0 ]; then
    # The opt-out stops the flow here rather than copying a plan with no document: a plan
    # on the destination with nothing for a person to approve it as can never be
    # approved, and a plan that cannot be approved is one no launch will start. So there
    # is nothing to copy and nothing to report, and saying so is the whole of what this
    # ending owes a caller — including that it reviewed nothing, because a caller who read
    # this as "reviewed and stopped" would take a plan nothing has read to be cleared.
    echo "finish-plan: --no-design-doc, so this did nothing: no plan was reviewed or checked, no design document was launched, nothing was copied into a destination, and there is no location to report" >&2
    exit 0
fi

plan_project=$(plan_brief_project finish-plan "$brief") || exit "$UNRUNNABLE"
plan_run_is_free finish-plan "$design_run" || exit "$UNRUNNABLE"

# Lifted here and not lower down. Every refusal above is about the caller's own invocation,
# which is what an operator can correct; everything below costs a store CLI install, a
# judged review turn, a check and a launch. A checkout that cannot lift from this template
# cannot compose the dispatch at all, so discovering it after the review is a turn spent
# for nothing.
template="$script_dir/../$DESIGN_DOC_TEMPLATE"
# A regular file rather than anything readable: a directory at that path is readable, and
# `awk` answers one with a read error and an exit status the branch below would then
# report as a template lending no criterion — the wrong refusal, and the one whose repair
# is to edit a file that is not there.
{ [ -f "$template" ] && [ -r "$template" ]; } || fail "the design document's template is not readable as a file at '$template'" \
    "run this recipe from a checkout that has $DESIGN_DOC_TEMPLATE, or pass --no-design-doc to launch the planner alone"
# One open, one close, in that order, with a non-blank line between them. Counted rather
# than merely seen: a second open, a stray close and an unclosed block each name a
# different span from the one whoever edited that file meant, and each composes silently
# if the check asks only whether a marker went past. So each exits with a status of its
# own and is named on its own below — "no criterion between the markers" would send
# whoever reads it looking for missing prose when what is there is a second block. Blank
# lines inside the block are kept, so the criterion arrives as its author wrote it. The
# statuses start past `awk`'s own fatal exit so a read that failed inside `awk` cannot be
# reported as one of them.
#
# `|| lifted=$?` rather than reading `$?` on the next line: `set -e` ends the script on a
# failed assignment, so a status read afterwards is a line this never reaches.
lifted=0
architecture=$(awk -v opened="$DESIGN_DOC_LIFT_OPEN" -v closed="$DESIGN_DOC_LIFT_CLOSE" '
    $0 == opened { opens += 1; inside = 1; next }
    $0 == closed { closes += 1; if (!inside) { stray = 1 }; inside = 0; next }
    inside { print; if ($0 ~ /[^[:space:]]/) { lent = 1 } }
    END {
        if (opens == 0 && closes == 0) { exit 11 }
        if (stray) { exit 12 }
        if (inside) { exit 13 }
        if (opens > 1 || closes > 1) { exit 14 }
        if (!lent) { exit 15 }
        exit 0
    }
' "$template") || lifted=$?
case $lifted in
    0) ;;
    11) fail "$DESIGN_DOC_TEMPLATE carries neither '$DESIGN_DOC_LIFT_OPEN' nor '$DESIGN_DOC_LIFT_CLOSE', so it lends the design-doc dispatch no criterion" \
        "put both markers back around the property that dispatch is to be held to in its own task, or pass --no-design-doc to launch the planner alone" ;;
    12) fail "$DESIGN_DOC_TEMPLATE closes the lent block with '$DESIGN_DOC_LIFT_CLOSE' before any '$DESIGN_DOC_LIFT_OPEN' opens one" \
        "put the opening marker above the property that dispatch is to be held to, or pass --no-design-doc to launch the planner alone" ;;
    13) fail "$DESIGN_DOC_TEMPLATE opens the lent block with '$DESIGN_DOC_LIFT_OPEN' and never closes it, so the span it lends runs to the end of the file" \
        "put '$DESIGN_DOC_LIFT_CLOSE' back below that property, or pass --no-design-doc to launch the planner alone" ;;
    14) fail "$DESIGN_DOC_TEMPLATE carries more than one '$DESIGN_DOC_LIFT_OPEN' or '$DESIGN_DOC_LIFT_CLOSE', so more than one span claims to be the criterion" \
        "leave one pair of markers, around the property that dispatch is to be held to, or pass --no-design-doc to launch the planner alone" ;;
    15) fail "$DESIGN_DOC_TEMPLATE holds nothing but whitespace between '$DESIGN_DOC_LIFT_OPEN' and '$DESIGN_DOC_LIFT_CLOSE', so it lends the design-doc dispatch no criterion" \
        "write the property that dispatch is to be held to between those markers, or pass --no-design-doc to launch the planner alone" ;;
    # Every status above is one this flow assigns to a condition of the template, so it
    # names that condition and the edit that repairs it. This one is any other status
    # `awk` exited with, and the whole point of it is that this flow does not know what
    # happened — 127 is no `awk` on `PATH` at all, 126 is one that is not executable, and
    # a signal death is neither. Reporting those as a template that could not be read
    # sends whoever hits one to edit a file that is very likely fine, so it says what it
    # observed and names both places the fault can be instead of picking one.
    *) fail "the criterion between '$DESIGN_DOC_LIFT_OPEN' and '$DESIGN_DOC_LIFT_CLOSE' could not be lifted from $DESIGN_DOC_TEMPLATE: awk exited $lifted, which is not a status this recipe assigns" \
        "check that awk runs where this recipe is running and that $DESIGN_DOC_TEMPLATE is readable there — awk exits 127 when it is not on PATH and 126 when it is not executable — or pass --no-design-doc to launch the planner alone" ;;
esac

# The board this repository plans against when the caller names none, read from the one
# place that states it rather than spelled a second time here: `just copy-plan` copies
# into that same source by default, and two spellings of it is one flow copying into a
# destination it did not report the location of.
destination="$PLAN_OPT_DESTINATION"
if [ -z "$destination" ]; then
    destination=$("$python" -c 'from orchestrator import plan_copy; print(plan_copy.BOARD)') ||
        fail "the destination this flow copies into by default could not be read" \
            "restore the pinned toolchain with 'just bootstrap', then retry, or name one with --to"
fi

# The store CLI this checkout pins, healed into its own `.venv/bin` before anything asks
# the store a question: session setup runs on a `SessionStart` hook that a fresh worktree
# and a publication clone never fire, so a run there resolves whatever copy another
# checkout left on `PATH`, or nothing at all. It is here for the steps that read the store
# and heal nothing themselves — the review and the check; `just copy-plan` performs its
# own, which is why this is not that one repeated.
"$script_dir/onetaskgraph-install.sh" || fail "this checkout's plan store CLI could not be provisioned" \
    "the diagnostic above names the failing step, and 'just session-setup' performs the same install"

# 1. The review, which is what makes every step below it a step about reviewed content.
# Its own exit statuses are carried through unchanged in meaning: 1 is a refusal naming
# every refused criterion, which this flow ends on rather than repairing — the planner's
# own judge is the repair loop and it has already run.
review_status=0
recipe review-plan "$plan_project" || review_status=$?
if [ "$review_status" -ne 0 ]; then
    if [ "$review_status" -eq 1 ]; then
        echo "finish-plan: the review above refused $plan_project, so no design document was launched and nothing was copied; correct every criterion it named in the plan's own task record, then run 'just finish-plan $brief' again" >&2
        exit "$REVIEW_REFUSED"
    fi
    echo "finish-plan: $plan_project could not be reviewed, so nothing after it was attempted; the diagnostic above names what to repair" >&2
    exit "$UNRUNNABLE"
fi

# 2. The plan its own launch will read, refused here rather than after a document
# dispatch has been paid for.
check_status=0
recipe check-plan "$plan_project" || check_status=$?
if [ "$check_status" -ne 0 ]; then
    if [ "$check_status" -eq 1 ]; then
        echo "finish-plan: the check above refused $plan_project, so no design document was launched and nothing was copied; a plan that would not launch is not one to write a document about — correct each node the check named in the plan's own task record, read it back with 'just check-plan $plan_project', and run this command again" >&2
        exit "$PLAN_REFUSED"
    fi
    echo "finish-plan: $plan_project could not be checked, so nothing after it was attempted; the diagnostic above names what to repair" >&2
    exit "$UNRUNNABLE"
fi

# The observer default, composed from what was forwarded rather than consumed out of it:
# a caller's `--dag-graph` — either spelling, and including their own `off` — reaches
# `onepipeline start` as they typed it, and this adds one only when they named neither.
observer=("$DAG_GRAPH_FLAG" "$DEFAULT_DAG_GRAPH")
# llmlint: ignore[robust_shell] `${a[@]+"${a[@]}"}` is the idiom for expanding a possibly-empty array under `set -u`, and only its `+` alternate-value part is unquoted — the value it expands to is `"${a[@]}"`, so every element stays one argument. Measured: an array of `one two`, `*` and the empty string expands to exactly those three arguments, and an empty array expands to none. shellcheck, which this repository's `lint` target runs over every script, accepts it.
for argument in ${PLAN_OPT_FORWARDED[@]+"${PLAN_OPT_FORWARDED[@]}"}; do
    case "$argument" in
        "$DAG_GRAPH_FLAG" | "$DAG_GRAPH_FLAG"=*)
            observer=()
            break
            ;;
    esac
done

export "$PLAN_RUN_ID_ENV=$design_run"

# shellcheck source=scripts/credentials-env.sh
load credentials-env.sh
export_host_credentials finish-plan || exit "$?"
# shellcheck source=scripts/ask-manager-env.sh
load ask-manager-env.sh
export_ask_manager finish-plan || exit "$?"
# shellcheck source=scripts/plan-root-env.sh
load plan-root-env.sh
export_plan_authoring_root finish-plan || exit "$?"

# The root the helper above resolved, which is where this launch writes its project and
# where `onepipeline start` then looks for it. It replaces the `.plans` the store would
# resolve from whichever `onetaskgraph.yaml` the reading process found: that is this
# checkout's for an ordinary launch and another one the moment a caller points the root
# elsewhere, and the launch gate then refuses the plan the launch has just written.
plan_root=${!PLAN_AUTHORING_ROOT_ENV}
plan_directory="$plan_root/$PLAN_RECORDS"
mkdir -p "$plan_directory" || fail "the plan directory $plan_directory could not be created" \
    "check that the plan-authoring root is a directory this launch may write into, then retry"

plan_source=${plan_project%%:*}
design_document_id="${plan_project#*:}$DESIGN_DOC_ID_SUFFIX"

# The design document's file, for the judge — named only for a plan in the authoring
# source, because that is the one source whose root this launch resolved and exported
# above, and the document is stored in the plan's own source: a plan held anywhere else
# would have its judge pointed at a file under a root that plan is not in. Made absolute
# against this directory, which is what every write of this script resolves a relative
# root against, rather than left to resolve against the judge's worktree.
#
# The project half of the brief's id is whatever the store calls a project, `/` included —
# a local store resolves `sub/x` to a nested record and keeps its documents nested the
# same way, so that path is right — but a component that is empty, `.` or `..` would carry
# the path out of the documents directory, and is refused before anything is launched.
judge_artifacts=()
if [ "$plan_source" = "$PLAN_AUTHORING_SOURCE" ]; then
    case "/${plan_project#*:}/" in
        # llmlint: ignore[changed_behavior_has_e2e] Unreachable through this flow as the store stands: the review above reads the project out of the store first, and a local store answers `no project with that id` for every id with an empty, `.` or `..` component (measured against a store holding `projects/sub/x.md`: `st:sub/x` resolves, `st:../store/projects/sub/x` does not), which that step reports as a plan that could not be reviewed. It guards the path against a store that ever resolves one.
        *//* | */./* | */../*)
            fail "the plan project '$plan_project' has an empty, '.' or '..' path component, so its design document's path would leave $PLAN_DOCUMENTS/" \
                "name the plan by the id the store lists it under ('just plans project list --source $PLAN_AUTHORING_SOURCE'), then retry" ;;
    esac
    case $plan_root in
        /*) documents_root=$plan_root ;;
        *) documents_root="$PWD/$plan_root" ;;
    esac
    judge_artifacts=("$JUDGE_ARTIFACTS_ENV=$documents_root/$PLAN_DOCUMENTS/$design_document_id.md")
fi

design_instructions="${DESIGN_DOC_INSTRUCTIONS//@PLAN_PROJECT@/$plan_project}"
design_instructions="${design_instructions//@TEMPLATE@/$DESIGN_DOC_TEMPLATE}"
design_instructions="${design_instructions//@DOCUMENT_ID@/$design_document_id}"
# `@ARCHITECTURE@` is spliced rather than substituted, because what fills it is somebody
# else's bytes and has to arrive verbatim. A `//` replacement would not leave it so: bash
# 5.2 enables `patsub_replacement`, which reads `&` in a *replacement* as the matched
# pattern and `\` as an escape, so a block saying `read & write` would reach the dispatch
# saying `read @ARCHITECTURE@ write` — the criterion this path exists to place in front of
# the dispatch, corrupted silently, with the flow still composing and still launching.
# Escaping the two is the wrong repair: correct only while `patsub_replacement` is on, and
# double-escaping where it is off. Prefix and suffix removal interpret nothing, so the
# splice is verbatim under either shell.
#
# The guard is what makes it total, since `%%` and `#` remove only the outermost match and
# a second placeholder would be left unfilled. It counts by length rather than with a
# tool so that the failure of the thing counting cannot arrive as the count: `grep -c`
# counts lines rather than occurrences, and a `grep`ped count that finds none exits 1,
# which `pipefail` and `set -e` turn into the end of the script.
placeholder_free=${DESIGN_DOC_INSTRUCTIONS//"$DESIGN_DOC_LIFT_PLACEHOLDER"/}
placeholder_count=$(( (${#DESIGN_DOC_INSTRUCTIONS} - ${#placeholder_free}) / ${#DESIGN_DOC_LIFT_PLACEHOLDER} ))
[ "$placeholder_count" = "1" ] || fail \
    "this recipe's design-doc instructions carry $placeholder_count '$DESIGN_DOC_LIFT_PLACEHOLDER' placeholders rather than exactly one, so the criterion lifted from $DESIGN_DOC_TEMPLATE would not reach the dispatched task whole" \
    "leave exactly one '$DESIGN_DOC_LIFT_PLACEHOLDER' in DESIGN_DOC_INSTRUCTIONS in $(basename "$0"), where that criterion belongs"
design_instructions="${design_instructions%%"$DESIGN_DOC_LIFT_PLACEHOLDER"*}$architecture${design_instructions#*"$DESIGN_DOC_LIFT_PLACEHOLDER"}"
planning_metadata="${PLANNING_PROJECT_METADATA//@NODES@/[\"$DESIGN_DOC_NODE_ID\"]}"

design_plan="$plan_directory/$design_run.md"
design_tasks="$plan_root/$PLAN_TASKS/$design_run"
"$python" -c "$PLAN_PROGRAM" "$design_run" "$brief" "$DESIGN_DOC_NODE_ID" "$DESIGN_DOC_PERSONA" \
    "$DESIGN_DOC_GRAPH" "$PLAN_DIRECT_PLACEMENT_NOTE" "$design_instructions" \
    | "$python" -m orchestrator.project_store "$plan_root" "$planning_metadata" >/dev/null || {
    # Reported rather than swallowed, and reported without ending the launch here: what
    # the operator has to act on is the write that failed, which the diagnostic below
    # names, and a removal that failed on top of it leaves records the next run would
    # read — so it earns its own line naming them, and the refusal still comes last. The
    # one task record is named rather than matched by a pattern: this project has exactly
    # one node and its id is what that file is called, so there is nothing to sweep.
    rm -f "$design_plan" "$design_tasks/$DESIGN_DOC_NODE_ID.md" ||
        echo "finish-plan: part of the half-written project could not be removed; delete $design_plan and $design_tasks by hand, or the next run of '$design_run' reads what this one left" >&2
    # This one is *expected* to fail whenever the directory is absent or still holds the
    # record the removal above could not take, and both are already reported by that
    # line, so its own failure is not a second thing to tell anybody about.
    rmdir "$design_tasks" 2>/dev/null || :
    fail "the design-document project for '$brief' could not be written to $design_plan by $python" \
        "restore the pinned toolchain with 'just bootstrap', then retry"
}

echo "finish-plan: launching run $design_run to write the design document for $plan_project; answer this dispatch's questions with: just channel-next $design_run" >&2

# 3. The design-document launch, through the shared wrapper rather than `uv run`
# directly, because that is where this launch's identity is established: a run launched
# without it records `unknown`, and `just runs --mine` and `just stop` then disown it.
launch_status=0
# The judge's artifacts reach the launch through `env` rather than `export`, so they are
# this launch's alone: the copy and the report after it run no judge, and a list left in
# this shell would be inherited by anything a later step spawns.
# llmlint: ignore[boundary_inputs_validated, tool_output_is_signal, robust_shell] `onepipeline start` validates its own surface and restating it here is the drift this repository gates against; this is an attached launch, so streaming the run as it goes is what a manager stays attached for — the lines this script owns are its own; and the three array expansions are the `set -u` idiom whose `+` part alone is unquoted, measured to keep `one two`, `*` and the empty string each one argument.
env ${judge_artifacts[@]+"${judge_artifacts[@]}"} "$script_dir/onepipeline.sh" start "$PLAN_SOURCE:$design_run" \
    ${PLAN_OPT_FORWARDED[@]+"${PLAN_OPT_FORWARDED[@]}"} ${observer[@]+"${observer[@]}"} || launch_status=$?
if [ "$launch_status" -ne 0 ]; then
    echo "finish-plan: run $design_run did not settle, so nothing was copied into '$destination'; read it with 'just channel-next $design_run', and run this command again once the document is written" >&2
    exit "$LAUNCH_FAILED"
fi

# 4. The copy, which is the step the whole ordering above exists to make safe: what lands
# on the destination is a plan something reviewed and a document written from it.
copy_status=0
recipe copy-plan "$plan_project" --to "$destination" || copy_status=$?
if [ "$copy_status" -ne 0 ]; then
    if [ "$copy_status" -eq 3 ]; then
        echo "finish-plan: '$destination' refused the copy of $plan_project, and reported why above; the plan and its document are unchanged where they were drafted, and running this command again resumes a copy that partly landed" >&2
        exit "$COPY_REFUSED"
    fi
    echo "finish-plan: $plan_project could not be copied into '$destination'; the diagnostic above names what to repair" >&2
    exit "$UNRUNNABLE"
fi

# 5. Where the destination holds what just landed, read back out of it. Not composed from
# a project name: a destination decides its own native ids and where its records live, so
# a board mints a number where a directory keeps the name.
# llmlint: ignore[tool_output_is_signal] The two locations are what a person opens to review this plan, so they are this command's product and reach the operator's own stream.
uv run orchestrator-plan-locations "$plan_project" --in "$destination" ||
    fail "$plan_project landed in '$destination', but where it holds the plan and its design document could not be read" \
        "read the copy's own per-record report above, then ask the store directly with 'just plans project list --source $destination'"
