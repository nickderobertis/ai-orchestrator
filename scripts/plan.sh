#!/usr/bin/env bash
# Launch a planner on a manager-written brief: `just plan <BRIEF.md> [--name NAME]
# [--max-turns N] [--repo ALIAS] [--execution-checkout ALIAS] [--direct]
# [--no-design-doc] [<onepipeline start flags>]`.
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
# **It is one lifecycle node, so the planner works in an isolated worktree.** That is
# the one thing about this recipe that is not merely convenience, and it was learned
# the expensive way. A node with no `repo` is a *direct* node, and a direct node works
# in the launch directory — which for this recipe is the shared canonical checkout the
# self-dispatch rule in `AGENTS.md` forbids authoring in, because concurrent
# orchestrators use it and direct edits race them. A planner dispatched that way cut a
# branch in the canonical checkout, committed to it, and left it checked out; a
# finished lifecycle publication then failed at its last step because the publication
# checkout was on that branch rather than on its base, and returning it to the base
# deleted the manager's own plan files, which the planner had force-added onto its
# branch from gitignored paths. So the node carries `repo` — the publication checkout
# this repository is published from — and `execution_checkout`, the registered safety
# clone its worktree is cut from, exactly as every other node this repository
# dispatches does.
#
# Two consequences a caller should know. The planner's working directory is that
# worktree and not this checkout, so a plan written to a gitignored path there does not
# outlive the run — a brief wanting the plan back names a path that is committed, or
# the manager reads it off the branch. And `--repo` / `--execution-checkout` are here
# for a caller planning against a differently registered host, and for the journeys
# that drive this recipe against a scratch identity rather than against this host's own
# checkouts.
#
# `--direct` is the old shape, kept for a planning dispatch that must not cut a
# worktree at all — it costs the clone, and a planner authoring nothing has no branch
# to leave. It is a working directory nobody owns exclusively, so a dispatch launched
# that way **may write only to gitignored paths, may not commit, and may not leave the
# checkout on any branch but its base.** That is now in the dispatched task rather than
# only here: `DIRECT_PLACEMENT_NOTE` is appended to the brief for a `--direct` launch,
# because the shared completion clause in `config/onejudge.base.yaml` demands "every
# change this dispatch made committed" of every dispatch alike and a `--direct` one may
# not commit at all. A planner that did correct, verified work settled `task-failed`
# against exactly that. The shared clause is right, is shared, and does not move; the
# exemption belongs in the task of the one dispatch it is true of.
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
# **The project has a second node, and by default every planning run writes one.** A
# person cannot usefully review a plan node by node; what they can judge is one short
# document — what is being built and why, the architecture, the contracts, the
# acceptance criteria, and the planned work as a table of links. So the launch writes a
# `design-doc` node depending on the planner node, dispatched under
# `graphs/design-doc.yaml` with `../personas/design-doc.yaml` — a PATH, for the reason
# the planner's persona is one — carrying the manager's brief unchanged followed by its
# own instructions. It takes the same publication repository and execution checkout the
# planner node takes, so it is a lifecycle node of this repository like every other.
# `--max-turns` is deliberately the planner node's alone: the design-doc node takes its
# persona's own budget, because a document is one read and one write rather than an
# open-ended search.
#
# **That second node is why a brief now has to name the plan's qualified project id.**
# Nothing hands one node's output to a later node, and a plan written to an ignored path
# in the planner's own worktree does not outlive the run, so the design-doc node has no
# other way to find the plan it is writing about. A brief therefore carries a
# `Plan project: <source>:<project>` line and is refused without one, exactly as it is
# refused for missing a required section — the durable instructions already tell a
# manager to name that id when writing a brief, so this makes an existing instruction
# enforceable rather than adding a rule. Nothing else in the brief is parsed.
#
# A line that is present and unusable is refused as a bad value rather than as an
# absence: two of them are ambiguous, and taking the earlier one dispatches the document
# writer at a plan its author may not have meant, while a value that names a project in
# no store reported as a missing line sends a manager looking for a line already in front
# of them.
#
# `--no-design-doc` is the opt-out, and it opts out of the requirement with the node:
# with no design-doc node there is nothing that needs a project id, so a brief that
# names none is accepted and the launch writes exactly the one-node project it wrote
# before this.
#
# `--name`, `--max-turns`, `--repo`, `--execution-checkout`, `--direct` and
# `--no-design-doc` are consumed here; every other flag is passed to `onepipeline start`
# untouched, `--dag-graph` included.
set -euo pipefail

#: What the project this launch writes says about itself: it is the plan a *planning*
#: run is producing, rather than a plan to be executed. That is the one exemption from
#: the design-document approval every launch is otherwise refused without — a planning
#: run's output is the plan, and the document it is reviewed as does not exist until the
#: run has written it. Stamped as a fact the project states rather than left to be
#: recognised from its shape, so a two-node project somebody wrote by hand is not quietly
#: exempt and a planning launch that grows a third node does not quietly lose it.
#: `orchestrator/design_approval.py` is the one reader of this pair and states both names.
PLANNING_PROJECT_METADATA='{"orchestrator.plan-kind": "planning"}'

#: Where a generated project's record is written under the gitignored local-md root.
#: Kept in the repository because it is the project the launch is judged against and
#: its qualified id remains directly relaunchable with `just orchestrate`.
PLAN_DIRECTORY=".plans/projects"
PLAN_SOURCE="authoring"

#: The persona ref the planner node carries. A path, deliberately — see the header.
PLANNER_PERSONA="../personas/planner.yaml"

#: The persona ref the design-doc node carries, and for the same reason: a bare
#: `design-doc` resolves against the roles compiled into `oneagentgraph`, which has no
#: such role, so the node would be refused rather than silently mis-run — but a path is
#: what actually reaches `personas/design-doc.yaml`, and paths are resolved against
#: `graphs/`.
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

#: The second node's id, which is what `just status` and the DAG UI label the dispatch.
DESIGN_DOC_NODE_ID="design-doc"

#: Its change-request subject, composed on the same terms as the planner node's and with
#: the same releasable type: `.githooks/commit-msg` refuses a `docs:` subject, and a
#: publication that reached that hook would be refused from the far side of a gate run.
DESIGN_TITLE_PREFIX="feat(plan): design document for "

#: The line a brief carries to name the plan the design-doc node reads, and the two
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

#: The publication repository the one node carries, and the registered safety clone its
#: worktree is cut from. Aliases rather than paths, because `onevcs` resolves an alias
#: through its own registry and the two hosts this repository dispatches from lay these
#: checkouts out differently — `config/onevcs.checkouts` is where each one's path is
#: declared, and where a host that has neither is already accounted for.
DEFAULT_PUBLICATION_REPO="ai-orchestrator"
DEFAULT_EXECUTION_CHECKOUT="ai-orchestrator-isolated"

#: The subject a lifecycle node's change request opens under, which the plan schema
#: requires of one and which a squash-merged publication leaves on the base branch as
#: its only commit. It is composed rather than asked for, because the caller wrote a
#: brief rather than a commit: `feat` because this repository releases from a plan the
#: way it releases from any other tracked source, and the run's own name as the summary,
#: which is the one thing about this dispatch a reader of the base would want.
#:
#: A planner that authors nothing commits nothing and publishes nothing, so this is
#: usually a subject nobody ever reads — and that is the case it exists for: the node
#: that does leave a commit must not be the one discovering there is no subject for it.
TITLE_PREFIX="feat(plan): "

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

#: What a `--direct` dispatch is told, appended to the brief as part of its own task.
#: Stated as the placement plus the one clause it is exempt from, in that order: a
#: dispatch reading only "may not commit" beside a bar demanding every change committed
#: holds two instructions of equal authority and resolves it by guessing, which is the
#: failure this text exists to end.
DIRECT_PLACEMENT_NOTE="

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

#: The planner node's id. It is what `just status` and the DAG UI label the dispatch.
NODE_ID="plan"

#: What the design-doc node is told, appended after the brief as the rest of its task.
#: `@PLAN_PROJECT@` and `@TEMPLATE@` are substituted below — placeholders rather than
#: `printf` conversions, because each appears twice and a format string reused per
#: argument is how a two-placeholder template comes out interleaved.
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

- One document exists, written to that template: its sections, in that file's order, and
  no others, satisfying every property it states of them.
- That document is stored as a document of that plan's own project, in the same plan
  store the plan itself is in, so a reader finds it beside the plan rather than in a
  directory only this dispatch knows about.
- Every task of that plan has one row in the document's planned-tasks table, and each row
  points at its task using the location the store reports for that task — read back out
  of the store, never composed by hand.
- This dispatch reports where the stored document is, in the form the store reports it: a
  link where the store puts it on a website, a path where it puts it in a file on this
  machine.
- Every claim this dispatch makes about the finished work is true of the tree as it
  finally stands."

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

# Writes the plan. The brief is read here and embedded verbatim in **both** nodes: it
# IS the task, in the `## What` / `## Why` / `## Acceptance criteria` template every
# task this repository dispatches is written in, so anything that reformatted it
# would be editing the manager's words on the way to the dispatch. Two things are
# appended after it, in this order and never woven in, so the manager's own words are
# always the whole of what precedes them: `DESIGN_DOC_INSTRUCTIONS` on the design-doc
# node, which is the rest of that node's task and the criteria it is judged against;
# and `DIRECT_PLACEMENT_NOTE` on every node of a `--direct` launch, which states where
# that dispatch works and which clause of the shared completion bar it is therefore
# exempt from.
PLAN_PROGRAM='
import json, pathlib, sys

(name, brief, persona, node_id, turns, repo, execution, title, direct_note,
 design_id, design_persona, design_graph, design_title, design_task) = sys.argv[1:15]
task = pathlib.Path(brief).read_text(encoding="utf-8")


def placed(node, body):
    """Give one node its placement and its task, on the terms that placement implies."""
    if repo:
        node["repo"] = repo
        node["execution_checkout"] = execution
        node["title"] = title if node["id"] == node_id else design_title
    else:
        # A direct node works in the launch directory, and the shared completion bar
        # demands every change committed. Saying so in the task is the only place the
        # dispatch and its judge both read it.
        body = body.rstrip() + "\n\n" + direct_note.strip() + "\n"
    node["task"] = body
    return node


planner = placed({"id": node_id, "persona": persona}, task)
if turns:
    planner["max_turns"] = int(turns)
tasks = [planner]
if design_task:
    # A per-node agent graph rather than the run-wide default: this role pairs its two
    # sides the other way round from every other dispatch on this host, and that
    # reversal is a property of the node rather than of the run.
    tasks.append(
        placed(
            {
                "id": design_id,
                "persona": design_persona,
                "deps": [node_id],
                "agent_graph": design_graph,
            },
            task.rstrip() + "\n\n" + design_task.strip() + "\n",
        )
    )
plan = {
    "schema_version": 3,
    "goal": {"text": f"Plan the work the manager briefed in {brief}"},
    "name": name,
    "tasks": tasks,
}
sys.stdout.write(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
'

fail() {
    echo "plan: $1; $2" >&2
    exit 2
}

usage() {
    echo "usage: just plan <brief.md> [--name NAME] [--max-turns N] [--repo ALIAS] [--execution-checkout ALIAS] [--direct] [--no-design-doc] [<onepipeline start flags>]" >&2
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
repo="$DEFAULT_PUBLICATION_REPO"
execution="$DEFAULT_EXECUTION_CHECKOUT"
design_doc=1
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
        --repo)
            if [ $# -lt 2 ] || [ -z "$2" ]; then
                fail "--repo was given no value" "name the checkout this plan's branch publishes from, or omit it for '$DEFAULT_PUBLICATION_REPO'"
            fi
            repo="$2"
            shift 2
            ;;
        --repo=*)
            repo="${1#--repo=}"
            [ -n "$repo" ] || fail "--repo was given no value" "name the checkout this plan's branch publishes from, or omit it for '$DEFAULT_PUBLICATION_REPO'"
            shift
            ;;
        --execution-checkout)
            if [ $# -lt 2 ] || [ -z "$2" ]; then
                fail "--execution-checkout was given no value" "name the safety clone the planner's worktree is cut from, or omit it for '$DEFAULT_EXECUTION_CHECKOUT'"
            fi
            execution="$2"
            shift 2
            ;;
        --execution-checkout=*)
            execution="${1#--execution-checkout=}"
            [ -n "$execution" ] || fail "--execution-checkout was given no value" "name the safety clone the planner's worktree is cut from, or omit it for '$DEFAULT_EXECUTION_CHECKOUT'"
            shift
            ;;
        --direct)
            # Both together, because a node carries the pair or neither: `execution_checkout`
            # without a `repo` names a clone nothing is cut from.
            repo=""
            execution=""
            shift
            ;;
        --no-design-doc)
            # Consumed rather than forwarded: `onepipeline start` has no such option, and
            # a launch that passed it on would be refused by the verb rather than by the
            # caller's own opt-out.
            design_doc=0
            shift
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

# The observer default, composed from what was forwarded rather than consumed out of
# it: a caller's `--dag-graph` — either spelling, and including their own `off` —
# reaches `onepipeline start` as they typed it, and this adds one only when they named
# neither.
observer=("$DAG_GRAPH_FLAG" "$DEFAULT_DAG_GRAPH")
for argument in ${forwarded[@]+"${forwarded[@]}"}; do
    case "$argument" in
        "$DAG_GRAPH_FLAG" | "$DAG_GRAPH_FLAG"=*)
            observer=()
            break
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
# The pair is checked after every flag has been read rather than as each is read,
# because the flags may arrive in any order and `--direct` clears both: a `--direct`
# followed by one of them leaves half a placement, which names a clone nothing is cut
# from or a checkout nothing publishes to.
if { [ -n "$repo" ] && [ -z "$execution" ]; } || { [ -z "$repo" ] && [ -n "$execution" ]; }; then
    fail "this launch would write a node placed at repo '${repo:-none}' and execution checkout '${execution:-none}'" \
        "a node carries both or neither: name the missing one, or pass --direct alone for a dispatch that cuts no worktree"
fi

# The plan the design-doc node reads, taken from the brief and nothing else. Read after
# the flags rather than beside the section checks above, because `--no-design-doc`
# decides whether it is required at all — with no design-doc node there is nothing that
# needs one, so demanding it would refuse a launch that has no use for the answer.
#
# The whole of what this parses out of a brief. Everything else in that file is the
# manager's prose and reaches the dispatch untouched.
design_instructions=""
if [ "$design_doc" -eq 1 ]; then
    plan_project=""
    declared=0
    # Every line is read rather than stopping at the first, because two declarations are
    # ambiguous and taking the earlier one silently dispatches the design-doc node at a
    # plan its author may not have meant.
    while IFS= read -r line || [ -n "$line" ]; do
        # llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally, so nothing would ever match.
        if [[ "$line" =~ $PLAN_PROJECT_DECLARATION ]]; then
            declared=$((declared + 1))
            plan_project="${BASH_REMATCH[1]}"
        fi
    done < "$brief"
    [ "$declared" -ne 0 ] || fail "the brief '$brief' names no plan project, so the design-doc node would have no plan to read" \
        "add a line reading '$PLAN_PROJECT_LINE' naming the qualified project this plan is written to, or pass --no-design-doc to launch the planner alone"
    [ "$declared" -eq 1 ] || fail "the brief '$brief' names $declared plan projects, so this launch cannot tell which one the design-doc node is to read" \
        "leave exactly one '$PLAN_PROJECT_LINE' line in it, naming the plan this brief is planning"
    # llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally, so nothing would ever match.
    [[ "$plan_project" =~ $PLAN_PROJECT_QUALIFIED ]] || fail "the brief '$brief' names plan project '$plan_project', which is not a qualified id and names a project in no store" \
        "write the line as '$PLAN_PROJECT_LINE' — a source name, a colon, and the project inside that source"
    design_instructions="${DESIGN_DOC_INSTRUCTIONS//@PLAN_PROJECT@/$plan_project}"
    design_instructions="${design_instructions//@TEMPLATE@/$DESIGN_DOC_TEMPLATE}"
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

credentials_helper="$script_dir/credentials-env.sh"
if [ ! -f "$credentials_helper" ] || [ ! -r "$credentials_helper" ]; then
    fail "required helper is not a readable regular file: $credentials_helper" \
        "restore it from the repository or run 'just bootstrap', then retry"
fi
# shellcheck source=scripts/credentials-env.sh
if ! . "$credentials_helper"; then
    fail "the credentials helper at $credentials_helper is readable but could not be loaded" \
        "restore it from the repository or run 'just bootstrap', then retry"
fi
export_host_credentials plan || exit $?

ask_manager_helper="$script_dir/ask-manager-env.sh"
if [ ! -f "$ask_manager_helper" ] || [ ! -r "$ask_manager_helper" ]; then
    fail "required helper is not a readable regular file: $ask_manager_helper" \
        "restore it from the repository or run 'just bootstrap', then retry"
fi
# shellcheck source=scripts/ask-manager-env.sh
if ! . "$ask_manager_helper"; then
    fail "the ask-manager helper at $ask_manager_helper is readable but could not be loaded" \
        "restore it from the repository or run 'just bootstrap', then retry"
fi
export_ask_manager plan || exit $?

# Both checked rather than left to `set -e`, which would exit with whatever the
# helper printed and no repair — and, for the write, would leave a half-written project
# behind for the next launch to pick up.
mkdir -p "$PLAN_DIRECTORY" || fail "the plan directory $PLAN_DIRECTORY could not be created" \
    "check that this checkout is writable, then retry"
plan="$PLAN_DIRECTORY/$name.md"
"$python" -c "$PLAN_PROGRAM" "$name" "$brief" "$PLANNER_PERSONA" "$NODE_ID" "$max_turns" \
    "$repo" "$execution" "$TITLE_PREFIX$name" "$DIRECT_PLACEMENT_NOTE" \
    "$DESIGN_DOC_NODE_ID" "$DESIGN_DOC_PERSONA" "$DESIGN_DOC_GRAPH" "$DESIGN_TITLE_PREFIX$name" \
    "$design_instructions" \
    | "$python" -m orchestrator.project_store .plans "$PLANNING_PROJECT_METADATA" >/dev/null || {
    # `|| :` so a removal that fails cannot replace the diagnostic below with its own
    # exit; the partial plan is then named by that diagnostic rather than silently kept.
    rm -f "$plan" ".plans/tasks/$name"/*.md || :
    rmdir ".plans/tasks/$name" 2>/dev/null || :
    fail "the plan for '$brief' could not be written to $plan by $python" \
        "restore the pinned toolchain with 'just bootstrap', then retry"
}

# One line, and the placement is in it rather than beside it: where this planner works
# decides what a brief may ask it to leave behind, so a manager reading the receipt is
# the reader who needs it — and a second line on a successful launch is noise the next
# reader learns to skip.
if [ -n "$repo" ]; then
    placement="it works in a worktree cut from '$execution', so a plan written to a gitignored path there does not outlive the run"
else
    placement="--direct dispatches it into this checkout, which concurrent orchestrators share, so it may write only to gitignored paths, may not commit, and may not leave the base branch"
fi
project="$PLAN_SOURCE:$name"
echo "plan: wrote $project at $plan; $placement; answer this planner's questions with: just channel-next $name" >&2

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
# llmlint: ignore[boundary_inputs_validated, tool_output_is_signal] `onepipeline start` validates its own surface and restating it here is the drift this repository gates against; and this is `just orchestrate`'s attached launch with a plan written first, so streaming the run as it goes is what a manager stays attached for — the one line this script owns, the plan it wrote and the command that answers the planner, is printed above.
"$script_dir/onepipeline.sh" start "$project" ${forwarded[@]+"${forwarded[@]}"} ${observer[@]+"${observer[@]}"} || status=$?
if [ "$status" -eq 0 ]; then
    # llmlint: ignore[changed_behavior_has_e2e] The one ending left is a settled run whose closeout then fails outright, which now takes an unreadable snapshot or an unreadable review bar rather than any plan on disk; a project it cannot record is passed over instead, which `tests/test_plan_review.py` drives.
    "$python" -m orchestrator.plan_review closeout "$snapshot" || status=$?
fi
exit "$status"
