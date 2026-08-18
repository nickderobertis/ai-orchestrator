#!/usr/bin/env bash
# Ask the manager a blocking question over the planner channel, and answer with theirs.
#
# `ask-manager.sh <QUESTION-TEXT>` — the question as one argument (several words are
# joined). `ask-manager.sh --file <PATH>` reads it from a file, and `ask-manager.sh`
# with no arguments reads it from stdin, which is how a long question is piped in.
#
# A dispatched agent whose decision fork could change a key outcome has exactly one
# way to stop and ask rather than guess, and this is it. `onepipeline channel serve
# <RUN>` is the published server side: it reads ONE LINE of JSON on stdin, queues it
# as a planner surface, blocks until somebody replies, and writes that reply to
# stdout. Everything below is what stands between that verb and an agent that can
# trust its answer.
#
#   frame   ->  {"kind","message","blocking"?,"node"?}
#   answer  <-  the planner's reply envelope, verbatim
#
# **The frame is one compact line, and that is not a style choice.** A
# pretty-printed frame is refused with `the observer emitted a bad frame: EOF while
# parsing an object at line 1 column 1` — a parse error that names the symptom and
# not the cause. Removing that trap is one reason this wrapper exists.
#
# **`serve` answers its own timeouts, at exit 0, with a plausible ruling.** Measured:
# after the window elapses it prints
# `{"completion":false,"message":"no planner reply within the timeout; continue",
# "reason":"the channel timed out waiting for a verdict"}`. A caller that checked only
# the exit status would act on that as the manager's answer, and a fabricated verdict
# is worse than no verdict because it is actionable. So a reply whose `reason` is
# exactly that string is refused here, loudly, with nothing on stdout.
#
# **A reply is claimed by whichever reader arrives next, not by the call that asked.**
# Measured on a live run: a re-ask returned
# `{"version":1,"author":"monitor","commands":[{"op":"context",...}]}` — a live graph
# edit the monitor addressed to the engine, delivered here because this call happened
# to be the next reader. Two checks close that, in this order:
#
#   1. An answer is a ruling only when it carries a boolean `completion`. The live
#      edit above carries none, so it is refused rather than returned as prose.
#   2. Every question carries a minted correlation token, and only a ruling whose
#      `message` echoes it is this question's. A ruling addressed to somebody else is
#      re-asked, up to `MAX_ATTEMPTS`, rather than handed back.
#
# The token is the general remedy and needs no engine change: a reply routed to the
# wrong reader cannot echo a token it never saw. It also bounds the blast radius of
# the timeout check drifting — if a future release rewords that `reason`, the
# synthesized ruling stops matching the token and is re-asked and then refused,
# instead of being returned as an answer.
#
# **The reply window is set here rather than by the caller.** `serve`'s own default is
# ~30 seconds (29.8s measured), which is a supervisor's cadence and not a manager's:
# a question worth blocking on is worth waiting past the next time somebody looks at
# their terminal. `ONEPIPELINE_REPLY_TIMEOUT_SECONDS` governs that wait exactly
# (measured 5s -> 5.03s, 12s -> 11.89s) and appears in no `--help` output — it was
# found in the pinned binary — so it is a surface that can drift, and
# `tests/e2e/test_ask_manager_e2e.py` pins it. Relying on a caller to export it is
# what would silently return this to the 30-second default.
#
# Environment:
#   ONEPIPELINE_RUN_ID                        (required) the run to ask. What sets it
#       depends on the launch: `just plan` exports it, and so does an attached
#       `just orchestrate` — a detached or adopted one does not, and an observer member
#       carries the run's id as well as a dispatch does. Measured per shape by
#       `tests/e2e/test_launch_ask_seam_e2e.py`. So finding a value says which run this
#       process is under and never that it is a dispatch, and an unset one is refused
#       rather than guessed at.
#   ONEPIPELINE_BIN                           (optional) which onepipeline answers.
#   ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS  (optional) the reply window.
#   ORCHESTRATOR_ASK_MANAGER_NODE             (optional) the node the question is
#       about, so the engine can attach the surface to it. `serve` refuses a node the
#       run does not have, and that refusal is fatal here rather than retried.
set -euo pipefail

#: The reply window this wrapper sets for itself, in seconds. Fifty minutes: long
#: enough that a manager who stepped away still gets to answer, short enough that a
#: wedged question is not immortal.
DEFAULT_TIMEOUT_SECONDS=3000

#: How many times one question is put to the channel. Every attempt past the first is
#: a ruling that was somebody else's answer, so this bounds the reply-binds-to-reader
#: defect rather than a slow manager: a timeout ends the loop on its first occurrence.
MAX_ATTEMPTS=4

#: The `reason` `onepipeline channel serve` synthesizes for its own timeout. Matched
#: exactly, because a substring would also swallow a manager who wrote about a timeout.
TIMEOUT_REASON="the channel timed out waiting for a verdict"

#: What a run id may be before it is passed to `channel serve` as an argv word and
#: resolved as the `runs/<run-id>/` directory, and what a node id may be before it is
#: put into the frame the engine resolves against that run's graph. The same superset
#: `scripts/channel-serve.py` checks, and for the same reason: it refuses only what
#: those uses cannot survive. Both come from the environment a dispatch was started
#: with, which is somebody else's to write, so both are checked here.
SAFE_REFERENCE='^[A-Za-z0-9_][A-Za-z0-9_.-]*$'

# Builds the one line `channel serve` reads. Compact separators are stated rather
# than left to the default so the requirement is visible at the place it is met; a
# message carrying newlines still leaves one physical line, because JSON escapes them.
FRAME_PROGRAM='
import json, os, sys

frame = {"kind": "planner-question", "message": sys.stdin.read(), "blocking": True}
node = os.environ.get("ORCHESTRATOR_ASK_MANAGER_NODE", "")
if node:
    frame["node"] = node
sys.stdout.write(json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n")
'

# Decides what the channel handed back, and is the only thing that may call it an
# answer. Exit codes rather than a printed verdict, so the shell branches on a status
# and stdout stays the manager message on the one path that has one.
#
#   0  this question's answer, on stdout
#   10 the channel answered its own timeout
#   11 not a ruling at all, excerpt on stdout
#   12 a ruling addressed to another reader, excerpt on stdout
CLASSIFY_PROGRAM='
import json, sys

timeout_reason, token = sys.argv[1], sys.argv[2]
raw = sys.stdin.read()
excerpt = " ".join(raw.split())[:200]
try:
    answer = json.loads(raw)
except json.JSONDecodeError:
    sys.stdout.write(excerpt)
    sys.exit(11)
if not isinstance(answer, dict) or not isinstance(answer.get("completion"), bool):
    sys.stdout.write(excerpt)
    sys.exit(11)
if answer.get("reason") == timeout_reason:
    sys.exit(10)
message = answer.get("message")
if not isinstance(message, str) or token not in message:
    sys.stdout.write(excerpt)
    sys.exit(12)
sys.stdout.write(message)
'

fail() {
    echo "ask-manager: $1; $2" >&2
    exit 2
}

usage() {
    echo "usage: ask-manager.sh <question-text> | --file <path> | (question on stdin)" >&2
}

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on.
if ! script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd) ||
    ! root=$(CDPATH='' cd -- "$script_dir/.." && pwd); then
    fail "this wrapper could not resolve the checkout it was run from" \
        "run it by its path inside a checkout, so the onepipeline it asks through is that checkout's"
fi

python="$root/.venv/bin/python3"
[ -x "$python" ] || python=python3

# Which `onepipeline` answers, resolved from this file's own location and never as a
# bare PATH lookup: a dispatch inherits the launching session's PATH, so a lookup
# would let the ambient environment decide which release the manager is reached
# through. This is `scripts/channel-serve.py`'s resolution with its one fallback
# dropped — that filter degrades to the bare name when the pinned binary is missing,
# and here a missing toolchain is said out loud instead.
pinned="$root/.venv/bin/onepipeline"
if [ -n "${ONEPIPELINE_BIN:-}" ]; then
    if ! onepipeline=$(command -v -- "$ONEPIPELINE_BIN" 2>/dev/null) || [ ! -x "$onepipeline" ]; then
        fail "ONEPIPELINE_BIN names '$ONEPIPELINE_BIN', which is not an executable this wrapper can run" \
            "point ONEPIPELINE_BIN at a usable onepipeline, or unset it to use the one this checkout pins"
    fi
elif [ -x "$pinned" ]; then
    onepipeline="$pinned"
else
    fail "this checkout has no onepipeline at $pinned" \
        "restore the pinned toolchain with 'just bootstrap', or point ONEPIPELINE_BIN at a usable onepipeline"
fi

case "${1:-}" in
    --file)
        [ $# -eq 2 ] || { usage; fail "--file takes exactly one path, got $(($# - 1))" "write the question to a file and name it once"; }
        [ -r "$2" ] || fail "the question file '$2' is not readable" "check the path, or pipe the question in on stdin instead"
        # llmlint: ignore[changed_behavior_has_e2e] Reachable only when a file this script has already tested as readable fails midway through being read; driving it would mean failing the filesystem the suite runs on.
        question=$(cat -- "$2") || fail "the question file '$2' could not be read to the end" \
            "check that it is a regular readable file, then ask again"
        ;;
    -*)
        usage
        fail "'$1' is not an option this wrapper takes" \
            "pass the question as text, name a file with --file, or pipe it in on stdin"
        ;;
    "")
        # llmlint: ignore[changed_behavior_has_e2e] Reachable only when this process's own stdin fails while being read; a journey can close stdin, which reads as an empty question and is covered, but cannot make the read itself fail.
        question=$(cat) || fail "the question could not be read from stdin" \
            "pipe the question in, name a file with --file, or pass it as text"
        ;;
    *)
        question="$*"
        ;;
esac

[ -n "${question//[[:space:]]/}" ] || fail "the question is empty" \
    "state the decision fork and what each branch would change, so the manager can answer it in one reply"

if [ -z "${ONEPIPELINE_RUN_ID+set}" ]; then
    fail "ONEPIPELINE_RUN_ID is not set, so there is no run whose channel to ask on" \
        "run this from inside a dispatch, which exports it, or export the run id from 'just runs' yourself"
fi
run="$ONEPIPELINE_RUN_ID"
[ -n "${run//[[:space:]]/}" ] || fail "ONEPIPELINE_RUN_ID is set but blank, so it names no run" \
    "export the run id 'just runs' lists for this workstream, or unset it if this is not running under one"
# llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally, so the check would accept nothing.
[[ "$run" =~ $SAFE_REFERENCE ]] || fail "ONEPIPELINE_RUN_ID is '$run', which this wrapper will not pass to 'channel serve' as a run" \
    "a run id is one word of letters, digits, '_', '.', and '-'; check it against the runs 'just runs' lists"

node="${ORCHESTRATOR_ASK_MANAGER_NODE:-}"
if [ -n "$node" ]; then
    # llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally, so the check would accept nothing.
    [[ "$node" =~ $SAFE_REFERENCE ]] || fail "ORCHESTRATOR_ASK_MANAGER_NODE is '$node', which this wrapper will not put in a frame as a node" \
        "a node id is one word of letters, digits, '_', '.', and '-'; check it against the nodes 'just status $run' lists, or unset it to ask about the run as a whole"
fi

window="${ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS:-$DEFAULT_TIMEOUT_SECONDS}"
[[ "$window" =~ ^[0-9]+$ ]] || fail "ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS is '$window', which is not a number of seconds" \
    "set it to a whole number of seconds, or unset it for the ${DEFAULT_TIMEOUT_SECONDS}s default"

# The correlation token, minted per question rather than per attempt: a re-ask is the
# same question, and a manager who answers the first surface late must still be heard.
# llmlint: ignore[changed_behavior_has_e2e] Reachable only when /dev/urandom or the tools that read it stop working on the host the suite itself runs on.
minted=$(od -An -N12 -tx1 /dev/urandom 2>/dev/null | tr -d ' \n') || minted=""
[ -n "$minted" ] || fail "no correlation token could be minted from /dev/urandom" \
    "check that /dev/urandom is readable in this environment, then retry"
token="ask-manager-token:$minted"

asked="$question

--
This question was asked by an agent working on run $run, which is blocked until you
answer. Reply with 'just channel-reply $run' and include this token verbatim in your
reply's message, so your answer is matched to this question rather than to another
reader: $token

A reply on this channel is claimed by whichever reader reaches it next, so an answer
that does not carry the token is treated as somebody else's and the question is asked
again."

# Each helper is checked rather than left to `set -e`, which would exit with whatever
# the helper printed and no repair — the one shape of failure this wrapper exists to
# not have. `|| frame=""` keeps the status from ending the script before the cause
# beneath it can be said.
frame=$(printf '%s' "$asked" | "$python" -c "$FRAME_PROGRAM") || frame=""
[ -n "$frame" ] || fail "the question could not be encoded as a channel frame by $python" \
    "restore the pinned toolchain with 'just bootstrap', then ask again"

# The trap is armed before the second file is made, so a half-made pair is still
# cleaned up: `mktemp` that succeeds once and fails once would otherwise leak the
# first file on the way out.
served_out=""
served_err=""
# `|| :` because a trap that fails takes the script's exit status with it: a cleanup
# that could not remove a temporary file must not turn a refusal into a different one,
# or a successful answer into a failure on the way out.
trap 'rm -f "$served_out" "$served_err" || :' EXIT
# llmlint: ignore[changed_behavior_has_e2e] Reachable only when TMPDIR stops being writable mid-run; a journey that made it unwritable would take the suite's own temporary files with it.
if ! served_out=$(mktemp) || ! served_err=$(mktemp); then
    fail "no temporary file could be made to hold the channel's answer" \
        "check that TMPDIR names a writable directory, then ask again"
fi

attempt=1
while true; do
    serve_status=0
    printf '%s\n' "$frame" \
        | ONEPIPELINE_REPLY_TIMEOUT_SECONDS="$window" "$onepipeline" channel serve "$run" \
            >"$served_out" 2>"$served_err" || serve_status=$?
    if [ "$serve_status" -ne 0 ]; then
        # A refusal this could not read is still a refusal, so the fallback below
        # reports the exit status rather than letting the read end the script.
        refusal=$(tr -s '[:space:]' ' ' <"$served_err") || refusal=""
        # Fatal rather than retried, deliberately: `serve` refuses a frame it will
        # never accept — a node the run does not have, a run that does not exist — so
        # a retry loop would bury the cause under a wait and then a timeout.
        fail "the planner channel refused this question about run $run: ${refusal:-exit $serve_status}" \
            "check the run with 'just runs' and its nodes with 'just status $run', then ask again"
    fi
    [ -s "$served_out" ] || fail "the planner channel closed without answering the question about run $run" \
        "check with 'just status $run' whether the run settled while this was waiting, which leaves nobody to answer"

    classify_status=0
    classified=$("$python" -c "$CLASSIFY_PROGRAM" "$TIMEOUT_REASON" "$token" <"$served_out") || classify_status=$?
    case "$classify_status" in
        0)
            printf '%s\n' "$classified"
            exit 0
            ;;
        10)
            fail "no manager answered within ${window}s, and the channel synthesized its own ruling to say so" \
                "ask the manager to watch this run with 'just channel-next $run' before asking again, or raise ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS"
            ;;
        11)
            # A live graph edit the manager addressed to the engine reaches here
            # exactly this way. It is not prose and must never be reported as one.
            fail "the planner channel handed back something that is not a ruling: $classified" \
                "a ruling is a JSON object carrying a boolean 'completion'; this is what a live graph edit routed to this reader looks like, so ask again once the edit has landed"
            ;;
        12)
            if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
                fail "the last $MAX_ATTEMPTS rulings on run $run's channel were answers to other readers, the most recent being: $classified" \
                    "ask the manager to include the token this question carries verbatim in their reply's message, then ask again"
            fi
            attempt=$((attempt + 1))
            ;;
        *)
            # Reachable only when the judging helper itself could not run — a broken
            # or missing Python. Named as that rather than left to `set -e`, because
            # the alternative is an agent told nothing about an answer it never got.
            fail "the answer to this question about run $run could not be judged: $python exited $classify_status" \
                "restore the pinned toolchain with 'just bootstrap', then ask again"
            ;;
    esac
done
