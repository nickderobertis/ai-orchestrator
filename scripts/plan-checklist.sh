#!/usr/bin/env bash
# Run the plan checklist — `config/plan-checklist.llmlint.yml` — over exactly one plan's
# documents: the project's description and each of its task documents, as the plan store
# locates them. It chooses which files make up a plan, which is planning-flow knowledge,
# and nothing else: the rules, the verdict and its report are llmlint's.
#
# Two invocation forms, told apart by the first argument:
#
#   plan-checklist.sh review <source:project> [<llmlint lint options>...]
#       What `just review-plan` runs (`orchestrator/plan_review.py`). Execs `llmlint lint
#       --cwd <authoring root> -c <the configuration> --no-ignore-check <options>
#       <documents>`: nothing else supplies the subcommand, the directory or the
#       configuration here.
#
#   plan-checklist.sh --version | lint <onejudge's llmlint argv>...
#       The `bin` of the planner's `plan-checklist` judge (`graphs/planner.yaml`). onejudge
#       probes it with `--version` when it builds the provider, which reaches llmlint as
#       exactly `llmlint --version`. Each decision then arrives as `lint --cwd <worktree>
#       ... -c <the side's config> ...`, which reaches llmlint as every argument given, in
#       order, with two changes: `--cwd`'s value becomes the authoring root, and
#       `--no-ignore-check` is added. The plan's documents follow. The plan is the one the
#       planning dispatch's brief names on its `Plan project:` line, read with
#       `scripts/plan-brief.sh`'s parser from the dispatch's own run records, which the
#       judge's process finds through the `ONEPIPELINE_RUN_ID` it inherits.
#
# llmlint runs from the authoring root, the directory holding `projects/` and `tasks/`,
# and is handed each document relative to it, because under `-c` it roots every glob at
# its working directory and drops a path outside it without a word: 0 files, every rule
# skipped, exit 0, which reads as a pass. A plan with no document yet — a planner that has
# written nothing — is run from an empty directory, where llmlint judges nothing and exits
# 0 on its one-line summary, rather than from any directory whose files it would judge.
#
# `--no-ignore-check`: every task body quotes the dispatch appendix, whose own `llmlint:
# ignore` directive names a rule this configuration does not hold, and a plan describing
# the departure convention quotes the directive with a placeholder. llmlint's structural
# pre-flight refuses both before any judge runs. Well-formed directives — a departure's
# `ignore-file` line — are still honoured, and one naming a misspelled rule suppresses
# nothing, so its rule still fires.
#
# The judge's oneharness routing is `scripts/llmlint-oneharness.sh`'s, through the
# runtime environment the repository's own judged tier uses.
set -euo pipefail

usage() {
    echo "plan-checklist: $1; run it as 'plan-checklist.sh review <source>:<project> [<llmlint lint options>...]' to review a plan, or as an llmlint judge's bin, which onejudge invokes as '--version' and as 'lint ...'" >&2
    exit 2
}

fail() {
    echo "plan-checklist: $1" >&2
    exit 2
}

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on.
root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd) || fail "could not resolve the checkout this script belongs to; run it by its path inside a readable checkout"
config="$root/config/plan-checklist.llmlint.yml"
store="$root/.venv/bin/onetaskgraph"
python="$root/.venv/bin/python3"

command -v llmlint >/dev/null 2>&1 ||
    fail "llmlint is not installed on PATH; run 'just setup-llmlint', then retry"

case "${1-}" in
    --version)
        [ "$#" -eq 1 ] || usage "'--version' takes no other argument, got: $*"
        exec llmlint --version
        ;;
    review)
        # llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally, so nothing would ever match.
        [ "$#" -ge 2 ] && [[ "$2" =~ ^[A-Za-z0-9_.-]+:[^[:space:]]+$ ]] || usage "'review' needs a qualified plan project, <source>:<project>, got: ${*:2}"
        form=review
        project=$2
        shift 2
        ;;
    lint) form=lint ;;
    *) usage "'${1-}' is neither of its two forms" ;;
esac

for needed in "$store" "$python"; do
    [ -x "$needed" ] || fail "$needed is not installed; run 'just bootstrap' in $root, then retry"
done
# shellcheck source=scripts/llmlint-runtime-env.sh
. "$root/scripts/llmlint-runtime-env.sh" ||
    fail "$root/scripts/llmlint-runtime-env.sh could not be loaded; restore it from the repository or run 'just bootstrap', then retry"
llmlint_runtime_env "$root"
# Every oneharness record this run's judge calls write carries one label naming the run, so
# the plan-checklist budgets (`orchestrator/plan_checklist_budgets.py`) tell one decision's
# calls from the next: llmlint's own labels do not reach oneharness history.
# llmlint: ignore[changed_behavior_has_e2e] `date` failing on a host whose coreutils are installed is not reachable from a journey without removing the tool the whole suite runs on.
invocation=$(date -u +%Y%m%dT%H%M%S%N) || fail "could not read the clock to name this run's records; restore coreutils' date on PATH, then retry"
export ONEHARNESS_HISTORY_LABELS="${ONEHARNESS_HISTORY_LABELS:+$ONEHARNESS_HISTORY_LABELS,}orchestrator.plan-checklist=$invocation-$$"

# Prints each document of the plan project ``$2`` — its description first, then every task
# of every `task list` page — one absolute path per line. Exits 3 when the store holds no
# such project, saying why unless ``$3`` is `quiet`, and 2 when it holds one this cannot
# name a file of.
LIST_PROGRAM='
import json, os, subprocess, sys

store, project, quiet = sys.argv[1], sys.argv[2], sys.argv[3] == "quiet"
source, _, native = project.partition(":")

REPAIR = "; read the store with \"just plans project show " + project + "\", repair what it names, then retry"

def ask(*arguments):
    answered = subprocess.run([store, *arguments, "--json"], capture_output=True, text=True)
    if answered.returncode != 0:
        return None, answered.stderr.strip()
    try:
        document = json.loads(answered.stdout)
    except ValueError as exc:
        sys.exit(f"the plan store answered {project} with no JSON document ({exc})" + REPAIR)
    if not isinstance(document, dict) or not isinstance(document.get("items"), list):
        sys.exit(f"the plan store answered {project} with no list of items" + REPAIR)
    if document.get("errors"):
        sys.exit(f"the plan store answered {project} with errors: {document.get("errors")}" + REPAIR)
    following = document.get("next")
    if following is not None and not isinstance(following, str):
        sys.exit(f"the plan store answered {project} with a page cursor that is not a string" + REPAIR)
    return document, ""

def path(item, what):
    held = item.get("item") if isinstance(item, dict) else None
    located = held.get("location") if isinstance(held, dict) else None
    found = located.get("path") if isinstance(located, dict) else None
    if not isinstance(found, str) or not os.path.isabs(found) or not os.path.isfile(found):
        sys.exit(f"the plan store locates no file for {what} of {project} ({found!r}), so it is not a plan whose documents can be judged; move the plan into a local Markdown source such as \"authoring\", then retry")
    if not found.endswith(".md"):
        # The checklist selects "**/*.md", so any other file would be dropped unjudged.
        sys.exit(f"the plan store locates {what} of {project} at {found}, which is not a Markdown document the checklist selects; move the plan into a local Markdown source such as \"authoring\", then retry")
    return found

def every(*arguments):
    found, page = [], None
    while True:
        answered, why = ask(*arguments, *(["--page", page] if page else []))
        if answered is None:
            return None, why
        found += answered["items"]
        page = answered.get("next")
        if not page:
            return found, ""

shown, why = ask("project", "show", project)
if shown is None:
    # Absent only when the source answers and lists no such project; a source that cannot
    # answer at all is a failure to read the plan, never a plan with nothing in it yet.
    listed, unread = every("project", "list", "--source", source)
    if listed is None:
        sys.exit(f"the plan store could not read the source of {project}: {unread}" + REPAIR)
    records = [item.get("item") if isinstance(item, dict) else None for item in listed]
    held = [record.get("id") if isinstance(record, dict) else None for record in records]
    if not all(isinstance(identity, str) and identity for identity in held):
        sys.exit(f"the plan store listed a project of {source} with no id, so it cannot say whether {project} is there" + REPAIR)
    if native in held:
        sys.exit(f"the plan store lists {project} but could not show it: {why}" + REPAIR)
    if not quiet:
        print(f"the plan store holds no project {project} ({why}); name the project the plan is in, which \"just plans project list --source {source}\" lists", file=sys.stderr)
    sys.exit(3)
described = len(shown["items"])
if described != 1:
    sys.exit(f"the plan store answered {described} records for the project {project}, where a plan has one description" + REPAIR)
paths = [path(shown["items"][0], "the description")]
tasks, why = every("task", "list", "--source", source, "--project", native)
if tasks is None:
    sys.exit(f"the plan store could not list the tasks of {project}: {why}" + REPAIR)
paths += [path(item, "a task") for item in tasks]
print("\n".join(paths))
'

# Prints the authoring root of the documents on stdin — the directory holding the
# description's `projects/` — then each document relative to it, or nothing for none.
RELATIVE_PROGRAM='
import os, sys

paths = [os.path.realpath(line) for line in sys.stdin.read().splitlines() if line]
if paths:
    root = os.path.dirname(os.path.dirname(paths[0]))
    outside = [path for path in paths if os.path.commonpath([root, path]) != root]
    if outside:
        sys.exit(f"these documents are not under the authoring root {root}: {outside}; keep every task of the plan in the source its description is in, then retry")
    print(root)
    for path in paths:
        print(os.path.relpath(path, root))
'

# Prints the one task the planning run ``$1`` dispatched under the runs root ``$2``, read
# from that run's own plan.
TASK_PROGRAM='
import json, pathlib, sys

run, runs = sys.argv[1:3]
recorded = pathlib.Path(runs) / run / "plan.json"
try:
    tasks = json.loads(recorded.read_text(encoding="utf-8")).get("tasks")
except (OSError, ValueError, AttributeError) as exc:
    sys.exit(f"run {run} has no readable plan at {recorded} ({exc}); read the run with \"just status {run}\"")
if not isinstance(tasks, list) or len(tasks) != 1:
    sys.exit(f"run {run} holds no one task at {recorded}, where a planning run holds one; read it with \"just status {run}\", and name this script as an llmlint judge only on a planning dispatch")
task = tasks[0].get("task") if isinstance(tasks[0], dict) else None
if not isinstance(task, str):
    sys.exit(f"the one task of run {run} at {recorded} carries no task text; read the run with \"just status {run}\"")
sys.stdout.write(task)
'

if [ "$form" = lint ]; then
    # The project this planning dispatch writes, from its own brief.
    run=${ONEPIPELINE_RUN_ID:-}
    [ -n "$run" ] || fail "no planning run is named in ONEPIPELINE_RUN_ID, so there is no brief to read the plan project from; this form runs only as the planner's llmlint judge"
    runs=${ONEPIPELINE_RUNS_DIR:-$root/runs}
    # shellcheck source=scripts/plan-brief.sh
    . "$root/scripts/plan-brief.sh" ||
        fail "$root/scripts/plan-brief.sh could not be loaded; restore it from the repository or run 'just bootstrap', then retry"
    # A run id names a directory under the runs root and nothing else, so one that is not
    # a name is refused before it becomes a path.
    # llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally, so nothing would ever match.
    [[ "$run" =~ $PLAN_SAFE_RUN_ID ]] || fail "ONEPIPELINE_RUN_ID is '$run', which is not a run id; this form runs only as the planner's llmlint judge, inside the dispatch that names its own run"
    # llmlint: ignore[changed_behavior_has_e2e] A host failure: `mktemp` refusing on a full or unwritable temporary filesystem, which a journey cannot produce without breaking the filesystem it runs on.
    brief=$(mktemp) || fail "could not open a temporary file to read run $run's brief into; free space in the temporary directory, then retry"
    trap 'rm -f "$brief" || echo "plan-checklist: the temporary brief $brief could not be removed; delete it by hand" >&2' EXIT
    "$python" -c "$TASK_PROGRAM" "$run" "$runs" >"$brief" || fail "the brief of run $run could not be read; the reason above names what to repair"
    project=$(plan_brief_project plan-checklist "$brief" optional) || exit 2
    # Removed here rather than left to the trap, because the run below replaces this shell.
    rm -f "$brief" || echo "plan-checklist: the temporary brief $brief could not be removed; delete it by hand" >&2
    trap - EXIT
fi

documents=""
if [ -n "${project-}" ]; then
    status=0
    # A dispatch whose planner has not written yet asks quietly: that is the expected state.
    quiet=$([ "$form" = lint ] && echo quiet || echo loud)
    documents=$(cd "$root" && "$python" -c "$LIST_PROGRAM" "$store" "$project" "$quiet") || status=$?
    # A project the store does not hold yet is a planner that has written nothing, which
    # is judged as nothing; a review names a plan that has to exist.
    if [ "$status" -eq 3 ] && [ "$form" = lint ]; then
        documents=""
    elif [ "$status" -ne 0 ]; then
        fail "the documents of $project could not be listed; the reason above names what to repair"
    fi
fi

located=()
if [ -n "$documents" ]; then
    relative=$(printf '%s' "$documents" | "$python" -c "$RELATIVE_PROGRAM") ||
        fail "the documents of $project could not be named relative to their authoring root; the reason above names what to repair"
    mapfile -t located <<<"$relative"
fi

empty=""
if [ "${#located[@]}" -gt 0 ]; then
    cwd=${located[0]}
    files=("${located[@]:1}")
else
    # llmlint: ignore[changed_behavior_has_e2e] A host failure: `mktemp -d` refusing on a full or unwritable temporary filesystem, which a journey cannot produce without breaking the filesystem it runs on.
    empty=$(mktemp -d) || fail "could not make the empty directory a plan with no document is judged from; free space in the temporary directory, then retry"
    cwd=$empty
    files=()
fi

# `just lint-llm` judges this repository's own tree under `llmlint.yml`; the checklist judges
# a plan's documents under its own configuration from the plan-authoring root, and as a
# judge's `bin` it must answer onejudge's own `llmlint` argv, so no recipe stands in for the
# invocation composed below.
# llmlint: ignore-block[work_goes_through_command_surface] see above
if [ "$form" = review ]; then
    command=(llmlint lint --cwd "$cwd" -c "$config" --no-ignore-check "$@")
else
    command=()
    rewritten=0
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --cwd)
                [ "$#" -ge 2 ] || usage "'--cwd' names no directory"
                command+=(--cwd "$cwd")
                rewritten=$((rewritten + 1))
                shift 2
                continue
                ;;
            --cwd=*)
                command+=("--cwd=$cwd")
                rewritten=$((rewritten + 1))
                ;;
            *) command+=("$1") ;;
        esac
        shift
    done
    [ "$rewritten" -eq 1 ] || usage "onejudge's 'lint' names --cwd $rewritten time(s), where it names the worktree once"
    command=(llmlint "${command[@]}" --no-ignore-check)
fi
command+=(${files[@]+"${files[@]}"})
# llmlint: ignore-end[work_goes_through_command_surface]

if [ -z "$empty" ]; then
    exec "${command[@]}"
fi
status=0
"${command[@]}" || status=$?
rmdir "$empty" || echo "plan-checklist: the empty directory $empty could not be removed; delete it by hand" >&2
exit "$status"
