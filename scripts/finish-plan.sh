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
#   3. **launch** the design-document node, as its own one-node planning project whose
#      task is rendered from the `plan-task` template and checked by the engine before
#      anything is dispatched;
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
# criteria, 3 is the pre-launch check refusing the plan, 6 is the engine refusing the
# design-document node's own rendered task, 5 is the destination refusing the copy, 4 is
# the design-document launch not settling, and 2 is a flow that could not run at all. There is no repair loop here and there will not be one: the planner's own
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
# command earlier against the same helper — in tests/ask_seam/launch/test_launch_ask_seam_e2e.py
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
PLAN_SOURCE="authoring"
#: Where a local Markdown source keeps its documents under that same root, beside the
#: `projects/` directory above: flat, one file per document id, every project's documents together.
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

#: The two registered templates this flow reaches, by the names `onepipeline template`
#: resolves. The writer's own task is a `plan-task` rendering, like every dispatched task on
#: this host, and what it writes is a `design-doc` rendering — this host's own template,
#: `templates/design-doc.md.j2`, which states the document's sections, its reader and every
#: property it is judged on. Neither is restated here: the task names the template and its
#: variables, and the template's descriptions are the one statement of the bar.
WRITER_TASK_TEMPLATE="plan-task"
DOCUMENT_TEMPLATE="design-doc"

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

#: The answers the writer's task is rendered from, composed from the brief, the plan's
#: qualified id, the document id, the resolve command `orchestrator/design_chain.py`'s
#: rule gives for the plan, and what `orchestrator/plan_budgets.py` states about the plan's
#: budgets: its budgets document's answers, which the document's budget sections restate
#: and which the task carries whole so the writer and its judge read the same entries, or
#: the reason the migration list gives for a plan that predates budgets. The brief's `## What` and `## Why` are quoted into the task's
#: own What and Why rather than the brief being embedded whole, because the brief's
#: acceptance criteria are the *plan's* and a judge holds a dispatch to every criterion it
#: finds in its task; the criteria below are this dispatch's. The resolve command is named
#: wherever the task reaches the template, so the writer and its judge read and render
#: through the chain the approval is keyed on. The note on where a direct node works is the
#: task's additional info.
# shellcheck disable=SC2016 # A Python program: its backticks are Markdown in the task it writes, never shell.
WRITER_ANSWERS_PROGRAM='
import json, pathlib, re, sys

(brief, plan, document, template, note, resolve, budgets) = sys.argv[1:8]
budgets = json.loads(budgets)
predates, budgets_document = budgets["predates"], budgets["document"]
quoted = json.dumps(budgets["answers"], ensure_ascii=False, indent=2)
# Line endings and trailing blanks are read the way `scripts/plan-brief.sh` reads a heading
# when it validates the brief, so a brief it accepted is one this composes from.
text = pathlib.Path(brief).read_text(encoding="utf-8").replace("\r\n", "\n")

def section(name):
    heading = rf"^## {name}[^\S\n]*\n(.*?)(?=^## |\Z)"
    found = re.findall(heading, text, re.MULTILINE | re.DOTALL)
    # Two sections of one name are two answers, and taking the first would compose the
    # task from whichever its author happened to write higher up.
    if len(found) > 1:
        sys.exit(f"{brief} states ## {name} {len(found)} times, so which one the task is composed from is ambiguous")
    return found[0].strip() if found else ""

brief_what = section("What")
brief_why = section("Why")
if not brief_what or not brief_why:
    sys.exit(f"{brief} states no ## What or no ## Why for the task to be composed from")
variables = f"{resolve} | onetaskgraph template variables --template-loader -"
budget_answers = "`workload`, `checklist`, `ten_x`, `budgets`, `repo_wide_effects`, `realistic_data` and `spike_findings`"
if predates:
    budget_task = (
        f"The plan predates budgets: `config/budgets-migration.yaml` names `{plan}`, with the "
        f"reason below, and it carries no budgets document. Answer `predates_budgets` with "
        f"that reason word for word, and leave {budget_answers} empty.\n\n"
        f"> {predates}"
    )
    budget_criterion = (
        f"The document\u2019s `predates_budgets` answer is the reason the migration list gives "
        f"for `{plan}`, word for word, and every budget answer is empty."
    )
else:
    budget_task = (
        f"The plan\u2019s budgets are stated in its budgets document `{budgets_document}`, "
        f"which answers the following. The document\u2019s {budget_answers} answers restate "
        f"these, answer for answer, and `predates_budgets` is empty:\n\n```json\n"
        f"{quoted}\n```"
    )
    budget_criterion = (
        f"The document\u2019s {budget_answers} answers restate the answers of "
        f"`{budgets_document}` that this task quotes, answer for answer, and its "
        f"`predates_budgets` answer is empty."
    )
answers = {
    "what": (
        f"Write the design document a person reviews the plan `{plan}` as \u2014 terse prose, "
        f"complete interfaces \u2014 instead of "
        f"reading that plan node by node, and store it beside the plan under the document id "
        f"`{document}`.\n\n"
        f"Read the whole plan out of the plan store: the project record and every one of its "
        f"tasks. `onetaskgraph` is that store\u2019s command line, and `--help` documents what it "
        f"can do. The document is a rendering of the `{template}` template as `{resolve}` "
        f"resolves it for this plan, which is the resolve command this task names: the "
        f"guidance comments in that chain say how to write it, and `{variables}` lists the "
        f"answers it takes.\n\n"
        f"{budget_task}\n\n"
        f"What the planner was asked to plan, in the brief\u2019s words, for the document\u2019s What:"
        f"\n\n{brief_what}"
    ),
    "why": (
        "A person decides whether this plan launches by reading this document instead of the "
        "plan, so it has to let them accept or reject what the plan commits to. What the user "
        f"wants from the plan itself, in the brief\u2019s words:\n\n{brief_why}"
    ),
    "acceptance_criteria": [
        f"The document answers every variable `{variables}` lists, in the shape each "
        f"variable\u2019s description states, and the answers meet every rule the guidance "
        f"comments of the chain `{resolve}` resolves state, read against `{plan}` as the store "
        f"holds it.",
        f"The document is stored as a project document of `{plan}`, in the store that plan is "
        f"in, under the document id `{document}`, and is a rendering of the `{template}` "
        f"template: `{resolve}` piped into "
        f"`onetaskgraph document create --id {document} --template-loader - --no-interactive` "
        f"wrote it, which replaces a document the store already holds by that id whole, the "
        f"answers it was rendered from included, so it records `onepipeline:{template}` "
        f"provenance and the answers it was rendered from.",
        f"Every task of `{plan}` has one row in the planned-tasks answer, and each row\u2019s "
        "location is the location the plan store reports for that task, read back out of the "
        "store and never composed by hand.",
        budget_criterion,
        "This dispatch reports where the store put the document, in the form the store reports "
        "it: a link where it is on a website, a path where it is a file on this machine.",
        "Every claim this dispatch makes about the finished work is true of the tree as it "
        "finally stands.",
    ],
    "additional_info": re.sub(r"\A\s*## Additional info\s*", "", note).strip(),
}
sys.stdout.write(json.dumps(answers, ensure_ascii=False, indent=2) + "\n")
'

#: The exit statuses this flow answers with, which are its whole contract to a caller
#: that reads only the status. Every one of them is a *different next action*: correct
#: the criteria a reviewer named, correct the plan a check refused, read why a launch did
#: not settle, repair the destination, repair the task this checkout composes for the
#: design-document node, or repair this checkout.
REVIEW_REFUSED=1
UNRUNNABLE=2
PLAN_REFUSED=3
LAUNCH_FAILED=4
COPY_REFUSED=5
WRITER_REFUSED=6

#: Writes the project this launch runs, and nothing else: its one task is created below
#: through the `plan-task` template, into the project this writes. The project states the
#: goal and the name, and carries the planning stamp that bounds its exemption.
PLAN_PROGRAM='
import json, sys

(name, brief) = sys.argv[1:3]
plan = {
    "schema_version": 3,
    "goal": {"text": f"Write the design document for the plan the manager briefed in {brief}"},
    "name": name,
    "tasks": [],
}
sys.stdout.write(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
'

#: The metadata the writer's task record carries beside its rendered body: the node id the
#: planning stamp names, the persona path and the per-node agent graph. A per-node graph
#: rather than the run-wide default, because this role pairs its two sides the other way
#: round from every other dispatch on this host, and that reversal is a property of the node.
#: The node carries no `repo` and no `execution_checkout`: it is a direct node, for the
#: reason `scripts/plan.sh`'s header gives.
WRITER_METADATA_PROGRAM='
import json, sys

(node_id, persona, graph) = sys.argv[1:4]
for key, value in (("id", node_id), ("persona", persona), ("agent_graph", graph)):
    print(f"onepipeline.{key}={json.dumps(value)}")
'

#: Holds the engine's `template resolve --json` answer to being the loader document of the
#: one template asked for: an object naming it as its reference, with an entry to render.
LOADER_PROGRAM='
import json, sys

(name,) = sys.argv[1:2]
stated = json.load(sys.stdin)
if not isinstance(stated, dict) or stated.get("reference") != f"onepipeline:{name}":
    sys.exit(f"the engine resolved {name} to no loader naming onepipeline:{name}")
entry, templates = stated.get("entry"), stated.get("templates")
if not isinstance(entry, str) or not entry or not isinstance(templates, list) or not templates:
    sys.exit(f"the engine resolved {name} to a loader with no entry or templates to render")
# Which of these the entry names is left to the store, which resolves it against them and
# the loader search path and refuses an entry naming nothing (`template "<entry>" was not
# found`) before it creates anything: repeating that resolution here would be a second one.
for template in templates:
    if not isinstance(template, dict) or not isinstance(template.get("name"), str) \
            or not template["name"] or not isinstance(template.get("source"), str) \
            or not template["source"].strip():
        sys.exit(f"the engine resolved {name} to a loader holding a template with no name or no source: {template!r}")
'

#: Reads `onetaskgraph task create --json`'s answer: the created task's qualified id, then
#: the path the store reports it at, one per line — held to being this project's one node
#: and a record under the authoring root's `tasks/`, since the authoring store is a local one
#: and always reports where it wrote. The path is
#: what a refused task is taken back by, so it is the store's answer rather than a layout —
#: and it is held to lying under the plan-authoring root this launch wrote into, the one
#: directory a removal here may reach, before anything deletes it.
CREATED_PROGRAM='
import json, pathlib, sys

(root, source, project, node) = sys.argv[1:5]
answered = json.load(sys.stdin)
items = answered.get("items") if isinstance(answered, dict) else None
if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
    sys.exit(f"the store answered the create with no one created task: {answered!r}")
(item,) = items
created, record = item.get("id"), item.get("item")
if not isinstance(created, str) or not created.startswith(f"{source}:") or "\n" in created:
    sys.exit(f"the store reports the created task as {created!r}, not an id in {source}")
if not isinstance(record, dict) or record.get("project") != project:
    sys.exit(f"the store reports {created} outside the project {project} it was created in")
metadata = record.get("metadata")
if not isinstance(metadata, dict) or metadata.get("onepipeline.id") != node:
    sys.exit(f"the store reports {created} as a node other than {node}, the one created")
located = record.get("location")
held = located.get("path") if isinstance(located, dict) else None
tasks = pathlib.Path(root).resolve() / "tasks"
if not isinstance(held, str) or "\n" in held or pathlib.Path(held).suffix != ".md":
    sys.exit(f"the store reports no task record path for {created}: {located!r}")
if not pathlib.Path(held).resolve().is_relative_to(tasks):
    sys.exit(f"the store reports the task at {held}, not a task record under {tasks}")
if pathlib.Path(held).resolve().relative_to(tasks).with_suffix("").as_posix() != created.partition(":")[2]:
    sys.exit(f"the store reports {created} at {held}, a record named for another task")
print(created)
print(held)
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

# The resolve command the document is rendered through, by the one rule the approval reads
# too (`orchestrator/design_chain.py`): a plan whose tasks all name one repository resolves
# through that repository's layer, and every other plan through this directory's.
document_resolve=$("$python" -m orchestrator.design_chain "$plan_project") ||
    fail "which repository's layer the design document of $plan_project resolves through could not be read out of the plan store; the diagnostic above names why" \
        "repair what it names, then run this command again"

# What the document's budget sections restate: the plan's budgets document's answers, or the
# reason the migration list gives for a plan that predates budgets. The check above has
# already refused a plan with neither, so a failure here is a store that stopped answering.
budgets_context=$("$python" -m orchestrator.plan_budgets "$plan_project") ||
    fail "what the design document of $plan_project restates about its budgets could not be read; the diagnostic above names why" \
        "repair what it names, then run this command again"

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

planning_metadata="${PLANNING_PROJECT_METADATA//@NODES@/[\"$DESIGN_DOC_NODE_ID\"]}"

design_plan="$plan_directory/$design_run.md"
# Where the writer's task record sits once the store has created it, which is the store's
# own answer below; empty until then, so a refusal before it takes back the project alone.
writer_record=""
# Take back what this launch wrote when a later step refuses it, so the next run of the
# same name starts from nothing: a project left holding a refused task would be read by
# that run as a project with two nodes, which its planning stamp does not describe.
# Reported rather than swallowed when a removal fails, because a record left behind is one
# the next run reads.
take_back() {
    local record
    for record in "$design_plan" ${writer_record:+"$writer_record"}; do
        rm -f "$record" ||
            echo "finish-plan: $record could not be removed; delete it by hand, or the next run of '$design_run' reads what this one left" >&2
    done
    # A task the store created but reported no path for is one this cannot reach, so it is
    # named for the operator to remove rather than left behind in silence.
    if [ -n "${writer_task:-}" ] && [ -z "$writer_record" ]; then
        echo "finish-plan: the store reported no path for $writer_task, so it was not removed; remove it from $PLAN_SOURCE:$design_run by hand before the next run of '$design_run'" >&2
    fi
}
"$python" -c "$PLAN_PROGRAM" "$design_run" "$brief" \
    | "$python" -m orchestrator.project_store "$plan_root" "$planning_metadata" >/dev/null || {
    take_back
    fail "the design-document project for '$brief' could not be written to $design_plan by $python" \
        "restore the pinned toolchain with 'just bootstrap', then retry"
}

# The writer's task, created the way every task on this host is: the `plan-task` template
# as the engine resolves it now, piped into the store's own `task create`, answering its
# variables. The answers travel in a file rather than as `--var` words because two of them
# run to many lines.
answers_file=""
# Remove the scratch answers file, reporting rather than ending on a removal that fails:
# what is left is a scratch file under the system's temporary directory, and the refusal
# or the launch after it is what the operator acts on.
discard_answers() {
    [ -z "$answers_file" ] || rm -f "$answers_file" ||
        echo "finish-plan: the scratch answers file $answers_file could not be removed; delete it by hand" >&2
}
answers_file=$(mktemp "${TMPDIR:-/tmp}/finish-plan-answers.XXXXXX") || {
    answers_file=""
    take_back
    fail "a scratch file for the design-document task's answers could not be created" \
        "check that ${TMPDIR:-/tmp} is a directory this launch may write into, then retry"
}
"$python" -c "$WRITER_ANSWERS_PROGRAM" "$brief" "$plan_project" "$design_document_id" \
    "$DOCUMENT_TEMPLATE" "$PLAN_DIRECT_PLACEMENT_NOTE" "$document_resolve" "$budgets_context" >"$answers_file" || {
    discard_answers
    take_back
    fail "the design-document task's answers could not be composed from '$brief' by $python; the diagnostic above names why" \
        "give the brief a '## What' and a '## Why', or restore the pinned toolchain with 'just bootstrap', then retry"
}
metadata=$("$python" -c "$WRITER_METADATA_PROGRAM" "$DESIGN_DOC_NODE_ID" "$DESIGN_DOC_PERSONA" "$DESIGN_DOC_GRAPH") || {
    discard_answers
    take_back
    fail "the design-document task's node metadata could not be composed by $python" \
        "restore the pinned toolchain with 'just bootstrap', then retry"
}
metadata_flags=()
while IFS= read -r setting; do
    metadata_flags+=(--metadata "$setting")
done <<<"$metadata"
# The loader is resolved before the store is asked, rather than streamed into it, so a
# resolve that fails is reported as that rather than as a store handed nothing to render.
loader=$("$script_dir/onepipeline.sh" template resolve "$WRITER_TASK_TEMPLATE" --json) || {
    discard_answers
    take_back
    fail "the $WRITER_TASK_TEMPLATE template could not be resolved through this checkout's templates/; the diagnostic above names why" \
        "repair what it names, then run this command again"
}
# The loader is the engine's answer and the one thing the store renders from, so it is held
# to naming the template this asked for before anything is created from it.
"$python" -c "$LOADER_PROGRAM" "$WRITER_TASK_TEMPLATE" <<<"$loader" || {
    discard_answers
    take_back
    fail "the engine's answer to resolving the $WRITER_TASK_TEMPLATE template is not its loader document; the diagnostic above names why" \
        "provision this checkout with 'just bootstrap', then run this command again"
}
created_status=0
# llmlint: ignore[tool_output_is_signal] The store's answer is read, not shown: it is the created task's id and the path a refusal takes it back by.
created=$(printf '%s' "$loader" \
    | uv run onetaskgraph task create "$PLAN_SOURCE" --template-loader - --no-interactive \
        --project "$design_run" --title "Write the design document for $plan_project" \
        --answers "$answers_file" "${metadata_flags[@]}" --json) || created_status=$?
discard_answers
if [ "$created_status" -ne 0 ]; then
    take_back
    fail "the design-document task could not be rendered from the $WRITER_TASK_TEMPLATE template into $PLAN_SOURCE:$design_run (status $created_status); the diagnostic above names why" \
        "repair what it names, then run this command again"
fi
case $plan_root in
    /*) authoring_root=$plan_root ;;
    *) authoring_root="$PWD/$plan_root" ;;
esac
read_status=0
answered=$(printf '%s' "$created" | "$python" -c "$CREATED_PROGRAM" "$authoring_root" "$PLAN_SOURCE" \
    "$design_run" "$DESIGN_DOC_NODE_ID") || read_status=$?
if [ "$read_status" -ne 0 ]; then
    take_back
    fail "the store's answer to creating the design-document task could not be read (status $read_status); the diagnostic above names why, and the task it created is left in $PLAN_SOURCE:$design_run" \
        "find it with 'just plans task list --source $PLAN_SOURCE --project $design_run', which lists it without the project record this took back, remove the record it names, then retry"
fi
{ read -r writer_task; read -r writer_record; } <<<"$answered" || :
if [ -z "${writer_task:-}" ] || [ -z "${writer_record:-}" ]; then
    take_back
    fail "the store created the design-document task but reported no id or no record path for it" \
        "find it with 'just plans task list --source $PLAN_SOURCE --project $design_run', which lists it without the project record this took back, remove the record it names, then retry"
fi

# The rendering, validated now that it is written, before anything is launched: the
# engine's own check of a stored item against the criteria rule and against being the
# rendering its provenance records. A task that lists no acceptance criteria is refused
# here, by the engine that would otherwise refuse it at launch, and nothing is launched.
# Not `just check-plan`, which holds a planner's plan to its review records and so refuses
# every freshly rendered one-node project of this kind for want of one.
checked=0
verdict=$("$script_dir/onepipeline.sh" template check "$WRITER_TASK_TEMPLATE" --item "$writer_task" 2>&1) ||
    checked=$?
if [ "$checked" -ne 0 ]; then
    take_back
    [ -z "$verdict" ] || printf '%s\n' "$verdict" >&2
    # The engine words a refusal as a line of its own opening `onepipeline: refused:` and
    # naming the item or the template it checked, and answers a check that could not run at
    # all with the same status, so that line is what tells them apart; a `refused:` anywhere
    # else in its output is not one, and its repair is this checkout rather than the answers.
    refusal=""
    while IFS= read -r said; do
        case $said in
            "onepipeline: refused: item $writer_task: "* | "onepipeline: refused: template $WRITER_TASK_TEMPLATE: "*)
                refusal=$said
                break
                ;;
        esac
    done <<<"$verdict"
    [ -n "$refusal" ] ||
        fail "the engine's check of $writer_task could not run (status $checked), so no design document was launched and nothing was copied" \
            "provision this checkout with 'just bootstrap', then run this command again"
    # A refusal of the item is repaired in the answers this recipe composes for it; one of
    # the template is not, since every answer renders through that template unchanged.
    repair="correct the answers this recipe composes for it, then run this command again"
    case $refusal in
        *": no criteria listed") why="it lists no acceptance criteria, so there is nothing its dispatch could be judged against" ;;
        "onepipeline: refused: template "*)
            why="the engine refused the $WRITER_TASK_TEMPLATE template it was rendered from (status $checked), for the reason above"
            repair="repair the $WRITER_TASK_TEMPLATE template 'onepipeline template resolve $WRITER_TASK_TEMPLATE --json' names until 'onepipeline template check $WRITER_TASK_TEMPLATE' passes, then run this command again"
            ;;
        *) why="the engine refused it as a rendering of $WRITER_TASK_TEMPLATE (status $checked), for the reason above" ;;
    esac
    echo "finish-plan: the design-document task $writer_task was refused before launching: $why; no design document was launched and nothing was copied. To go on, $repair" >&2
    exit "$WRITER_REFUSED"
fi

# llmlint: ignore[tool_output_is_signal] The one line naming the run this launches and the command that answers its questions, which a detached or interrupted supervisor has no other way to reach; tests/plan_tooling/test_finish_plan_recipe_e2e.py's test_the_tail_names_the_channel_of_the_run_it_launches requires it.
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
