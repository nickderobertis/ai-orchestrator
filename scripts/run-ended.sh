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
#     is verifying what the run drafted. It runs `just follow-ups <run-id> --detach` in
#     this checkout and turns the recipe's two lines into the one a manager reads: the
#     follow-up run's id and its watch command. Detached, because the hook is awaited and a
#     follow-up run outlives its deadline. When the recipe answers that there is nothing to
#     verify, its one line is relayed alone, because it already says no follow-up run was
#     launched. It exits with the recipe's status.
#   * **failure** — the run ended any other way: a failed or skipped node, an unfinished
#     graph with nothing left to decide, or a clean stop. It launches nothing, and prints
#     one line naming the run, the reason the engine gave on stdin, and the command that
#     verifies the run's drafts by hand. It exits 0, because there is nothing it tried.
#
# What the engine hands it is validated before anything reads it: the run id against the
# grammar `scripts/plan-brief.sh` declares for a run id, the run root as an absolute path
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
# typing it is the only other way it runs. `just plan`, `just finish-plan` and `just
# follow-ups` name no hook, so a planning run and a follow-up run never launch one.
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

helper="$script_dir/plan-brief.sh"
if [ ! -f "$helper" ] || [ ! -r "$helper" ]; then
    fail "required helper is not a readable regular file: $helper" \
        "restore it from the repository, then let the run's owner verify its drafts with 'just follow-ups <run-id>'"
fi
# shellcheck source=scripts/plan-brief.sh
. "$helper"

run=${ONEPIPELINE_RUN_ID:-}
[ -n "$run" ] || fail "ONEPIPELINE_RUN_ID is not set, so there is no run to report on" "$usage"
# llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally, so nothing would ever match.
[[ "$run" =~ $PLAN_SAFE_RUN_ID ]] || fail "ONEPIPELINE_RUN_ID '$run' is not a run id: a run id is one word of letters, digits, '_' and '-'" "$usage"

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
        status=0
        # Standard error is left alone, so the recipe's own diagnostics and the engine's
        # launch record reach the hook's log; standard output is the recipe's answer.
        output=$(cd -- "$checkout" && just follow-ups "$run" --detach) || status=$?
        if [ "$status" -ne 0 ]; then
            [ -z "$output" ] || printf '%s\n' "$output"
            echo "run-ended: main work of run $run is complete, but 'just follow-ups $run --detach' exited $status, so no follow-up run was launched; the diagnostic above names why, and 'just follow-ups $run' verifies its drafts by hand"
            exit "$status"
        fi
        # Announced only when the recipe named exactly one follow-up run, spelled as a run id
        # is; anything else is relayed as the recipe said it.
        follow_up=$(sed -n 's/^follow-up run: //p' <<<"$output")
        # llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally, so nothing would ever match.
        if [[ "$follow_up" =~ $PLAN_SAFE_RUN_ID ]]; then
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
