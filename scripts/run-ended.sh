#!/usr/bin/env bash
# The one command `just orchestrate` names as both of a launch's run-end hooks, told which
# by `ONEPIPELINE_HOOK`. The engine spawns it once when a run ends — with no shell and no
# arguments, in the launch directory, with `ONEPIPELINE_RUN_ID` and `ONEPIPELINE_RUN_ROOT`
# naming the run and the run owner's launcher identity exported — awaits it under
# `--hook-timeout`, keeps what it prints in `hooks/<hook>.log` under the run, and relays
# that to an attached launch's stderr. Nothing it does can change how the run settled.
# onepipeline's `docs/contract.md` (**Run-end hooks**) is the contract; this is what this
# host does with it.
#
#   * **success** — every node ended `done`, so the main work is complete and what is left
#     is discarding the plan's spike branches and verifying what the run drafted. The
#     plan's spikes ran as the run of its `<plan>-spikes` project, so every branch
#     `onepipeline unpublished --host --format json` names as preserved by a run of that
#     project — a run whose id ends `-spikes`, or `-spikes-<n>`, as one minted from the
#     project's `<plan title> spikes` title does, and whose `onepipeline status --json`
#     `group` is that project — is discarded with `just
#     reclaim-branch <branch> --repo <identity> --discard`, the plan being the project the
#     run was launched from (`onepipeline status --json`'s `group`) or the one its
#     `onetaskgraph.origin` names when it is a copy; one line reports each outcome, and a
#     branch it cannot discard stops neither the others nor the follow-up run. Then it runs
#     `just follow-ups <run-id>` in this checkout, which creates the `follow-ups` plan from
#     its template and launches it detached — the hook is awaited and a follow-up run
#     outlives its deadline — and turns the recipe's line into the one a manager reads:
#     the follow-up run's id and its watch command. For a batch onefollowup reports empty
#     the recipe launches nothing and its one line is relayed as it said it. It exits with
#     the recipe's status.
#   * **failure** — the run ended any other way: a failed or skipped node, an unfinished
#     graph with nothing left to decide, or a clean stop. It discards no spike branch,
#     because a failed run's retries and recovery may still need them. It launches nothing,
#     and prints
#     one line naming the run, the reason the engine gave on stdin, and the command that
#     verifies the run's drafts by hand. It exits 0, because there is nothing it tried.
#
# What the engine hands it is validated before anything reads it: the run id against the
# grammar of a run id, the run root as an absolute path
# naming that run, and the failure document's version, hook, run, reason kind and nodes.
# A document that fails still ends in the failure line, naming what was wrong, because
# what a manager needs from that line does not depend on the reason.
#
# The follow-up run belongs to the main run's owner: the engine exports that owner's
# launcher identity to the hook, and `scripts/onepipeline.sh` keeps an exported identity
# rather than deriving one from whatever process fired the hook. Its runs root is the main
# run's own, taken from `ONEPIPELINE_RUN_ROOT`, so the recipe finds the run it follows up
# wherever the launch put it.
#
# This is the only thing in this repository that launches `just follow-ups`; a manager
# typing it is the only other way it runs. `just plan`, `just finish-plan` and the follow-up
# recipes launch under `config/onepipeline.workflows.yaml`, which registers no run-end hook,
# so a planning run and a follow-up run never launch one.
# `tests/run_end_hooks/test_run_end_hooks_e2e.py` drives both modes through a real launch.
set -euo pipefail

usage="run as a run-end hook: onepipeline start --success-hook $0 --failure-hook $0"

fail() {
    echo "run-ended: $1; $2" >&2
    exit 2
}

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on.
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P) || fail "this hook could not resolve the checkout it belongs to" \
    "name it by an absolute path in a readable checkout"
checkout=$(dirname -- "$script_dir")

# What a run id is, is the engine's: this hook holds the one it is handed to nothing but being
# a printable word, because each line it writes names the run and a manager reads them.
run=${ONEPIPELINE_RUN_ID:-}
[ -n "$run" ] || fail "ONEPIPELINE_RUN_ID is not set, so there is no run to report on" "$usage"
[[ "$run" != *[[:space:][:cntrl:]]* ]] || fail "ONEPIPELINE_RUN_ID '$run' is not a run id: it carries a space or a control character, which no run id the engine mints does" "$usage"

# Discard every branch the run of this run's plan's `<plan>-spikes` project preserved,
# reporting each outcome. Nothing it meets ends the hook: a plan whose spikes outlive their
# use costs disk and a listing line, while a success hook that stopped here would launch no
# follow-up run.
discard_spike_branches() {
    local plan origin spikes listed candidates candidate group rows reclaimed discarded branch identity
    local -a runs=()
    local -a gone=()
    # Each lookup that fails stops the discard with one line saying so, and the follow-up
    # run still launches: a spike branch kept by mistake costs disk, a wrong one discarded
    # costs work.
    if ! plan=$(cd -- "$checkout" && scripts/onepipeline.sh status "$run" --json | jq -er '.group'); then
        echo "run-ended: the plan of run $run could not be read from 'onepipeline status', so no spike branch was discarded; each is listed by 'just unpublished' and discarded with 'just reclaim-branch <branch> --repo <identity> --discard'"
        return 0
    fi
    if ! origin=$(cd -- "$checkout" && .venv/bin/onetaskgraph project show "$plan" --json --no-interactive |
        jq -er '(.items // []) as $items | if (.errors // []) != [] or ($items | length) != 1 then error("not one project") else ($items[0].item.metadata["onetaskgraph.origin"] // "") end | if type == "string" and (. == "" or test("^[^:]+:.+$")) then . else error("an origin that is no qualified id") end'); then
        echo "run-ended: project $plan could not be read from the plan store, so no spike branch was discarded; each is listed by 'just unpublished' and discarded with 'just reclaim-branch <branch> --repo <identity> --discard'"
        return 0
    fi
    spikes="${origin:-$plan}-spikes"
    # Its exit status says whether branches are owed, not whether it answered, so what it
    # printed is what is read: an answer without its `rows`, or one whose verdict is
    # `unanswered` because the registry could not be read, is a listing that failed.
    listed=$(cd -- "$checkout" && scripts/onepipeline.sh unpublished --host --format json) || :
    # The `-<n>` form is the engine's id for a repeat launch of the spikes project. No journey
    # relaunches one: that would redispatch every spike and preserve its branches a second
    # time, minutes of the planning journey for one alternation in this pattern, and the
    # `group` check below decides membership for that id exactly as for the first.
    if ! candidates=$(jq -r 'if (.rows | type) != "array" or .verdict == "unanswered" then error("no answer") else [.rows[] | .run // "" | select(test("-spikes(-[0-9]+)?$"))] | unique | .[] end' <<<"$listed" 2>/dev/null); then
        echo "run-ended: the preserved branches could not be listed by 'onepipeline unpublished', so no spike branch of $spikes was discarded; each is listed by 'just unpublished' and discarded with 'just reclaim-branch <branch> --repo <identity> --discard'"
        return 0
    fi
    # A candidate whose project cannot be read may be this plan's spikes run or another's, so
    # its branches are kept and the line says how to look and discard by hand.
    while IFS= read -r candidate; do
        [ -n "$candidate" ] || continue
        if ! group=$(cd -- "$checkout" && scripts/onepipeline.sh status "$candidate" --json | jq -er '.group'); then
            echo "run-ended: the project of run $candidate could not be read from 'onepipeline status' (its diagnostic is above), so its preserved branches were kept; 'just status $candidate' names its project, and if that is $spikes, 'just reclaim-branch <branch> --repo <identity> --discard' discards each"
        elif [ "$group" = "$spikes" ]; then
            runs+=("$candidate")
        fi
    done <<<"$candidates"
    if ! rows=$(jq -r --args 'if (.rows | type) != "array" then error("no answer") else .rows[] | select((.run // "") as $r | $ARGS.positional | index($r)) | if (.branch | type) == "string" and .branch != "" and (.identity | type) == "string" and .identity != "" then [.branch, .identity] | @tsv else error("a row names no branch or identity") end end' "${runs[@]}" <<<"$listed" 2>/dev/null); then
        echo "run-ended: the preserved branches could not be listed by 'onepipeline unpublished', so no spike branch of $spikes was discarded; each is listed by 'just unpublished' and discarded with 'just reclaim-branch <branch> --repo <identity> --discard'"
        return 0
    fi
    while IFS=$'\t' read -r branch identity; do
        [ -n "$branch" ] || continue
        reclaimed=0
        discarded=$(cd -- "$checkout" && just reclaim-branch "$branch" --repo "$identity" --discard 2>&1) || reclaimed=$?
        if [ "$reclaimed" -eq 0 ]; then
            gone+=("$branch of $identity")
        else
            printf '%s\n' "$discarded"
            echo "run-ended: spike branch $branch of $identity could not be discarded ('just reclaim-branch' exited $reclaimed, for the reason above); it is kept, and 'just reclaim-branch $branch --repo $identity --discard' discards it"
        fi
    done <<<"$rows"
    if [ "${#gone[@]}" -ne 0 ]; then
        local IFS=","
        # llmlint: ignore[tool_output_is_signal] The task that added this discard requires the hook to report each outcome on its output, and a manager reading this log decides from it which spike branches are gone; it is one line for all of them, and the follow-up receipt stays its own line because its exact shape is what a supervisor reads the follow-up run's id from.
        echo "run-ended: discarded the spike branches ${gone[*]}, because run $run, its plan's main run, succeeded"
    fi
}

case "${ONEPIPELINE_HOOK:-}" in
    success)
        run_root=${ONEPIPELINE_RUN_ROOT:-}
        [ -n "$run_root" ] || fail "ONEPIPELINE_RUN_ROOT is not set, so the runs root run $run lives in is unknown" "$usage"
        # Canonical as well as absolute: a `.`, `..` or empty segment would make its parent a
        # directory other than the runs root the run lives in.
        [[ "$run_root" == /* && "${run_root##*/}" == "$run" && "$run_root/" != *//* && "$run_root/" != */./* && "$run_root/" != */../* ]] ||
            fail "ONEPIPELINE_RUN_ROOT '$run_root' is not the canonical absolute directory of run $run" "$usage"
        export ONEPIPELINE_RUNS_DIR
        ONEPIPELINE_RUNS_DIR=$(dirname -- "$run_root")
        discard_spike_branches "$run_root"
        status=0
        # Standard error is left alone, so the recipe's own diagnostics and the engine's
        # launch record reach the hook's log; standard output is the recipe's answer.
        output=$(cd -- "$checkout" && just follow-ups "$run") || status=$?
        if [ "$status" -ne 0 ]; then
            [ -z "$output" ] || printf '%s\n' "$output"
            echo "run-ended: main work of run $run is complete, but 'just follow-ups $run' exited $status, so no follow-up run was launched; the diagnostic above names why, and 'just follow-ups $run' verifies its drafts by hand"
            exit "$status"
        fi
        # Announced only when the recipe named exactly one follow-up run, as one printable
        # word; anything else is relayed as the recipe said it.
        follow_up=$(sed -n 's/^follow-up run: \([^;]*\); watch it with: just watch .*$/\1/p' <<<"$output")
        if [ -n "$follow_up" ] && [[ "$follow_up" != *[[:space:][:cntrl:]]* ]]; then
            echo "main work of run $run is complete; follow-ups are being verified in run $follow_up; watch it with: just watch $follow_up"
        else
            printf '%s\n' "$output"
        fi
        exit 0
        ;;
    failure)
        python="$checkout/.venv/bin/python3"
        [ -x "$python" ] || python=python3
        # The reason is the engine's stdin document. One that cannot be read still ends in
        # the line below, because what the manager needs from it — that nothing was
        # launched and how to verify by hand — does not depend on the reason.
        reason=$("$python" -c '
import json, sys

KINDS = ("nodes", "unfinished", "stopped")

def reason(document, run):
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("it is not a version 1 hook document")
    if document.get("hook") != "failure" or document.get("run_id") != run:
        raise ValueError(f"it is not the failure hook of run {run}")
    given = document.get("reason")
    if not isinstance(given, dict) or given.get("kind") not in KINDS:
        raise ValueError("its reason kind is not one of " + ", ".join(KINDS))
    nodes = given.get("nodes")
    if not isinstance(nodes, list) or not all(
        isinstance(node, dict) and all(
            isinstance(node.get(field), str) and node[field] and node[field].isprintable()
            for field in ("id", "status")
        )
        for node in nodes
    ):
        raise ValueError("its nodes are not a list of nodes with a printable id and status")
    listed = ", ".join(f"{node['"'"'id'"'"']} {node['"'"'status'"'"']}" for node in nodes)
    if given["kind"] == "stopped":
        return "reason stopped: the run was stopped" + (f" with {listed}" if listed else "")
    return f"reason {given['"'"'kind'"'"']}: {listed or '"'"'no node listed'"'"'}"

try:
    print(reason(json.load(sys.stdin), sys.argv[1]))
except ValueError as error:
    print(f"the reason could not be read from the hook input: {error}")
' "$run" 2>&1) || reason="the reason could not be read, because no Python interpreter could run"
        echo "run-ended: run $run ended without every node done ($reason); no follow-up run was launched; verify its drafts by hand with: just follow-ups $run"
        exit 0
        ;;
    *)
        fail "ONEPIPELINE_HOOK is '${ONEPIPELINE_HOOK:-}', not 'success' or 'failure'" "$usage"
        ;;
esac
