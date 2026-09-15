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
# **`serve` answers its own timeouts, at exit 0.** Through onepipeline 0.31.0 it printed
# a plausible ruling, `{"completion":false,"message":"no planner reply within the
# timeout; continue",…}`, which a caller checking only the exit status would act on as
# the manager's answer. The adopted 0.32.0 prints the wait itself instead —
# `{"answer":"timeout","correlation":"c-…"}`, carrying no `completion` — and that line is
# refused here as a timeout, loudly, with nothing on stdout, before it could be reported
# as merely not a ruling.
#
# **A reply is claimed by whichever reader arrives next, not by the call that asked.**
# That was measured on a live run under onepipeline 0.8.x: a re-ask returned
# `{"version":1,"author":"monitor","commands":[{"op":"context",...}]}` — a live graph
# edit the monitor addressed to the engine, delivered here because this call happened
# to be the next reader. That op no longer exists; the engine collapsed it into `note`
# and removed it from the envelope, and the envelope it would arrive in today carries
# `note` instead. The shape is quoted as it was measured, because what this paragraph
# records is the routing rather than the op. The adopted release routes a reply by the halves it carries,
# so that envelope now stays on the command path and never reaches this rendezvous;
# `tests/ask_seam/test_ask_manager_e2e.py` measures that from the reply verb's own answer.
# Both checks below are kept anyway, and are what an agent has left if a release
# regresses to arrival order. In this order:
#
#   1. An answer is a ruling only when it carries a boolean `completion`. The live
#      edit above carries none, so it is refused rather than returned as prose.
#   2. Every question carries a minted correlation token, and only a ruling whose
#      `message` echoes it is this question's. One that does not is never handed back:
#      it re-arms a listener or puts the question back — the split below decides which
#      — up to `MAX_ATTEMPTS`.
#
# The token is the general remedy and needs no engine change: a reply routed to the
# wrong reader cannot echo a token it never saw. The timeout check drifting is bounded
# too — if a future release renamed that `answer` word, the line would carry no
# `completion` and be refused as not a ruling, instead of being returned as an answer.
#
# **Whether the question goes back depends on which token the stray ruling echoes.** A
# ruling echoing a *foreign* token is another ask's answer outliving its asker, so this
# question's own surface was never touched and is still pending: the wrapper re-arms
# behind a **non-blocking** note and leaves the manager one question. A ruling echoing
# none is this question's surface answered without the echo, so it is spent and the
# question must go back as a blocking surface.
#
# Two measurements force that split rather than a simpler rule. `channel serve` has no
# listen-only mode — every frame it accepts queues a surface, including one with an
# empty `message` and one carrying an unknown `kind`, while a frame with no `message`
# is refused outright — so waiting again always queues something. And a run stops
# accepting replies once nothing *blocking* is pending, refusing with `run '<id>' has
# settled, so nothing will ever read a reply to it`, so a listener behind a non-blocking
# surface alone could never be answered.
#
# Both halves matter because a duplicate blocking question is self-sustaining. An ask
# killed while waiting leaves its manager's answer with nobody to claim it; the next ask
# draws that stale ruling and queues a second blocking copy; the manager answers both,
# and the copy nobody claimed is the stale ruling the ask after it draws.
#
# **A re-arm is a new listener, so the question survives one only while the sessions
# share a name.** The engine gives a session what an earlier one left outstanding only
# when both carry the same non-blank `ONEPIPELINE_CHANNEL_ASKER`; a session naming none
# is given nothing back. An inherited name therefore wins — the asker is the *dispatch*
# — and with nothing inherited this wrapper names itself from the token it minted,
# which is already constant across this invocation's re-arms.
#
# **The reply window is set here rather than by the caller.** `serve`'s own default is
# ~30 seconds (29.8s measured), which is a supervisor's cadence and not a manager's:
# a question worth blocking on is worth waiting past the next time somebody looks at
# their terminal. `ONEPIPELINE_REPLY_TIMEOUT_SECONDS` governs that wait exactly
# (measured 5s -> 5.03s, 12s -> 11.89s) and appears in no `--help` output — it was
# found in the pinned binary — so it is a surface that can drift, and
# `tests/ask_seam/test_ask_manager_e2e.py` pins it. Relying on a caller to export it is
# what would silently return this to the 30-second default.
#
# Environment:
#   ONEPIPELINE_RUN_ID                        (required) the run to ask. What sets it
#       depends on the launch: `just plan` exports it, and so does an attached
#       `just orchestrate` — a detached or adopted one does not, and an observer member
#       carries the run's id as well as a dispatch does. Measured per shape by
#       `tests/ask_seam/test_launch_ask_seam_e2e.py`. So finding a value says which run this
#       process is under and never that it is a dispatch, and an unset one is refused
#       rather than guessed at.
#   ONEPIPELINE_BIN                           (optional) which onepipeline answers.
#   ONEPIPELINE_RUNS_DIR                      (optional) where the run's records live.
#       Read to be checked and otherwise left alone: one that holds this run is passed
#       through untouched, and only one that does not is passed over.
#   ONEPIPELINE_NODE_SCRATCH_DIR              (optional) the dispatch's own scratch
#       directory, which sits under the run's directory and is how an ask made from a
#       lifecycle worktree finds the runs root. See the resolution below.
#   ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS  (optional) the reply window.
#   ORCHESTRATOR_ASK_MANAGER_NODE             (optional) the node the question is
#       about, so the engine can attach the surface to it. `serve` refuses a node the
#       run does not have, and that refusal is fatal here rather than retried.
set -euo pipefail

# Resolved before anything else, because the shared rule below is a file beside this
# one and two of the constants under it are built from what it declares. Reported
# through `echo` rather than `fail`, which is not defined yet: a wrapper that cannot
# find its own checkout has nothing to ask through either.
# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on.
if ! script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd) ||
    ! root=$(CDPATH='' cd -- "$script_dir/.." && pwd); then
    echo "ask-manager: this wrapper could not resolve the checkout it was run from; run it by its path inside a checkout, so the onepipeline it asks through is that checkout's" >&2
    exit 2
fi

# What a reply must carry to be a ruling this may act on, declared once and read by
# `just channel-reply` too — see scripts/ask-manager-contract.sh for why a second copy
# would be worse than no check at all.
contract_helper="$script_dir/ask-manager-contract.sh"
if [ ! -f "$contract_helper" ] || [ ! -r "$contract_helper" ]; then
    echo "ask-manager: required helper is not a readable regular file: $contract_helper; restore it from the repository or run 'just bootstrap', then ask again" >&2
    exit 2
fi
# shellcheck source=scripts/ask-manager-contract.sh
. "$contract_helper"

#: The reply window this wrapper sets for itself, in seconds. Fifty minutes: long
#: enough that a manager who stepped away still gets to answer, short enough that a
#: wedged question is not immortal.
DEFAULT_TIMEOUT_SECONDS=3000

#: How many rulings one question will look at before it gives up. The first attempt is
#: the question; each one after it either re-arms a listener over the still-pending
#: question or puts the question back, by the split below. So this bounds the
#: reply-binds-to-reader defect rather than a slow manager: a timeout ends the loop on
#: its first occurrence.
MAX_ATTEMPTS=4

#: What `onepipeline channel serve` answers in its `answer` field when its wait elapsed,
#: in a line carrying no `completion` — measured on onepipeline 0.32.0 as
#: `{"answer":"timeout","correlation":"c-…"}`. Matched exactly, only beside a string
#: `correlation` and only on a line with no `completion`, so a manager's ruling that
#: merely mentions a timeout is still a ruling.
TIMEOUT_ANSWER="timeout"

#: What a correlation token looks like on the wire. The prefix is
#: `scripts/ask-manager-contract.sh`'s, sourced above, because `just channel-reply` now
#: recognizes a pending question by it too; the rest is here, and stays here, because it
#: is what this wrapper *mints* and classifies by rather than anything the two ends agree
#: on — the reply recipe reads a pending surface's token as whatever follows the prefix,
#: and holding that to a shape would let a surface it did not recognize pass a reply
#: through unjudged. Three uses would otherwise drift apart: the token this question
#: mints, the pattern that decides a drawn ruling echoes *somebody else's*, and
#: `tests/e2e/planner_channel.py`'s `TOKEN`, which is what a manager is played by.
#: `tests/test_planner_seam_contracts.py` reconciles this with that one, because a
#: classifier reading a shape the minter stopped producing would call every foreign
#: answer this question's own.
TOKEN_BYTES=12
TOKEN_PATTERN="${ASK_MANAGER_TOKEN_PREFIX}[0-9a-f]{24}(?![0-9a-f])"

#: What the minted half must look like for `TOKEN_PATTERN` to match the token this
#: question puts on the wire. Computed from `TOKEN_BYTES` rather than restated, so it
#: cannot drift from the minting; `tests/test_planner_seam_contracts.py` holds
#: `TOKEN_PATTERN`'s own digit count to that same source.
TOKEN_SHAPE="^[0-9a-f]{$((TOKEN_BYTES * 2))}\$"

#: The variable a `channel serve` session names its asker in. Two sessions carrying the
#: same value are one asker; one carrying none adopts nothing and nothing adopts what it
#: raised. `onepipeline`'s `channel::ASKER_ENV` is the declaration.
ASKER_ENV="ONEPIPELINE_CHANNEL_ASKER"

#: What this wrapper calls itself when nothing else named its asker, joined to the token
#: below. Prefixed rather than bare so an asker minted here is legible as this wrapper's
#: in a queue that also holds the engine's own.
ASKER_PREFIX="ask-manager-"

# Builds both lines `channel serve` reads, in one call: the blocking question on
# stdin, then the non-blocking re-arm note from `argv[1]`. Compact separators are
# stated rather than left to the default so the requirement is visible at the place it
# is met; a message carrying newlines still leaves one physical line, because JSON
# escapes them.
#
# Both come from one invocation deliberately. Encoding them separately gave the second
# a failure path of its own that no journey could reach — a broken interpreter fails on
# the first frame and never gets to the second — so the two are one success or one
# refusal.
FRAME_PROGRAM='
import json, os, sys

node = os.environ.get("ORCHESTRATOR_ASK_MANAGER_NODE", "")
for message, blocking in ((sys.stdin.read(), True), (sys.argv[1], False)):
    frame = {"kind": "planner-question", "message": message, "blocking": blocking}
    if node:
        frame["node"] = node
    sys.stdout.write(json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n")
'

# Decides what the channel handed back, and is the only thing that may call it an
# answer. Exit codes rather than a printed verdict, so the shell branches on a status
# and stdout stays the manager message on the one path that has one.
#
# Neither half of what makes an answer this question's is decided here. Whether an
# envelope is a ruling at all, and whether it echoes the token this question minted, are
# both `scripts/ask-manager-contract.sh`'s to say and are embedded above rather than
# restated: `just channel-reply` refuses an envelope this would discard, and a second
# copy of either condition is what would let the two ends disagree about which replies
# are usable. What stays here is the reading of a token that is somebody *else's*, which
# is this wrapper's own question about which repair it needs rather than a rule the two
# ends share.
#
#   0  this question's answer, on stdout
#   10 the channel answered its own timeout
#   11 not a ruling at all, excerpt on stdout
#   12 another ask's answer, left on the channel; mine is still pending
#   13 a ruling with no token at all, so this question's own surface was consumed
#
# 12 and 13 are both "not mine", and they are split because the repair differs. A
# ruling echoing a *different* `ask-manager-token` is another invocation's answer that
# outlived its asker, so this question is still pending and only needs listening to
# again. A ruling echoing none was the manager answering this very surface without the
# echo, which consumed it — and a run with no blocking surface left pending stops
# accepting replies at all, so that case has to put the question back.
CLASSIFY_PROGRAM="$ASK_MANAGER_RULING_SOURCE$ASK_MANAGER_TOKEN_SOURCE"'
import json, re, sys

timeout_answer, token, foreign = sys.argv[1], sys.argv[2], sys.argv[3]
raw = sys.stdin.read()
excerpt = " ".join(raw.split())[:200]
try:
    said = json.loads(raw)
except json.JSONDecodeError:
    said = None
if (
    isinstance(said, dict)
    and said.get("answer") == timeout_answer
    and isinstance(said.get("correlation"), str)
    and "completion" not in said
):
    sys.exit(10)
if ruling_refusal(raw) is not None:
    sys.stdout.write(excerpt)
    sys.exit(11)
answer = json.loads(raw)
if not answer_echoes(raw, token):
    sys.stdout.write(excerpt)
    message = answer.get("message")
    somebody_elses = isinstance(message, str) and re.search(foreign, message)
    sys.exit(12 if somebody_elses else 13)
sys.stdout.write(answer["message"])
'

fail() {
    echo "ask-manager: $1; $2" >&2
    exit 2
}

usage() {
    echo "usage: ask-manager.sh <question-text> | --file <path> | (question on stdin)" >&2
}

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
[[ "$run" =~ $ASK_MANAGER_SAFE_REFERENCE ]] || fail "ONEPIPELINE_RUN_ID is '$run', which this wrapper will not pass to 'channel serve' as a run" \
    "a run id is one word of letters, digits, '_', '.', and '-'; check it against the runs 'just runs' lists"

node="${ORCHESTRATOR_ASK_MANAGER_NODE:-}"
if [ -n "$node" ]; then
    # llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally, so the check would accept nothing.
    [[ "$node" =~ $ASK_MANAGER_SAFE_REFERENCE ]] || fail "ORCHESTRATOR_ASK_MANAGER_NODE is '$node', which this wrapper will not put in a frame as a node" \
        "a node id is one word of letters, digits, '_', '.', and '-'; check it against the nodes 'just status $run' lists, or unset it to ask about the run as a whole"
fi

# Where this run's records live, which is not where the ask is being made from.
# `onepipeline` looks under `ONEPIPELINE_RUNS_DIR`, and under a *relative* `runs` when
# nothing names one — so an ask from a lifecycle worktree was refused `no such run
# '<run>' under runs`, raising no surface for anybody to notice. See
# docs/orchestration.md for the resolution below and why each rung is where it is.
#
# Three constraints the code cannot state for itself:
#   * The runs root is NAMED rather than reached by `cd`. A `--file` path and a piped
#     question are the caller's, and changing directory would re-root them silently.
#   * `$root`, this wrapper's own checkout, is deliberately not a rung. It is usually
#     also where the launch ran, but nothing ties the two, and asking confidently on
#     the wrong store is worse than being refused.
#   * The scratch path must be absolute: `%/*` is a fixpoint on a component holding no
#     `/`, so a relative one walks to its first component and loops there forever.
if [ ! -f "${ONEPIPELINE_RUNS_DIR:-runs}/$run/launch.json" ]; then
    case "${ONEPIPELINE_NODE_SCRATCH_DIR:-}" in
        /*) candidate="$ONEPIPELINE_NODE_SCRATCH_DIR" ;;
        *) candidate="" ;;
    esac
    # Stopping at the ancestor named for THIS run that carries a launch record, rather
    # than at a counted depth: how many components separate the two is the engine's
    # layout to change, and `tests/ask_seam/test_launch_ask_seam_e2e.py` is what holds
    # it to putting a dispatch's scratch under the run at all.
    while [ -n "$candidate" ]; do
        if [ "${candidate##*/}" = "$run" ] && [ -n "${candidate%/*}" ] &&
            [ -f "$candidate/launch.json" ]; then
            export ONEPIPELINE_RUNS_DIR="${candidate%/*}"
            break
        fi
        candidate="${candidate%/*}"
    done
    # Nothing found leaves the ask where it was, for `serve` to refuse naming what it
    # looked under: a guess would send the question where no manager reads.
fi

window="${ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS:-$DEFAULT_TIMEOUT_SECONDS}"
[[ "$window" =~ ^[0-9]+$ ]] || fail "ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS is '$window', which is not a number of seconds" \
    "set it to a whole number of seconds, or unset it for the ${DEFAULT_TIMEOUT_SECONDS}s default"

# The correlation token, minted per question rather than per attempt: a re-ask is the
# same question, and a manager who answers the first surface late must still be heard.
# llmlint: ignore[changed_behavior_has_e2e] Reachable only when /dev/urandom or the tools that read it stop working on the host the suite itself runs on.
minted=$(od -An -N"$TOKEN_BYTES" -tx1 /dev/urandom 2>/dev/null | tr -d ' \n') || minted=""
[ -n "$minted" ] || fail "no correlation token could be minted from /dev/urandom" \
    "check that /dev/urandom is readable in this environment, then retry"
# Checked against the shape the classifier matches, not merely for being non-empty. A
# token `TOKEN_PATTERN` does not match makes every ruling read as carrying no token,
# which puts this question back as a second blocking surface — the duplication the
# whole wrapper exists to prevent, arriving silently and on every ask.
# llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally, so the check would accept nothing.
[[ "$minted" =~ $TOKEN_SHAPE ]] || fail \
    "the correlation token minted here is '$minted', which is not the $((TOKEN_BYTES * 2)) lowercase hex digits a reply is matched against" \
    "check that the 'od' and 'tr' first on this PATH behave as coreutils' do, then ask again"
token="$ASK_MANAGER_TOKEN_PREFIX$minted"

# Exported rather than set on the `serve` command below: an assignment prefix takes a
# literal name, so writing it out there would be a second source for one variable.
asker="${!ASKER_ENV-}"
if [ -z "${asker//[[:space:]]/}" ]; then
    asker="$ASKER_PREFIX$minted"
fi
export "$ASKER_ENV=$asker"

# The protocol leads and the question follows, which is deliberate and is the half of
# this surface a truncating reader must keep. Appended after the body it was the first
# thing a reader that cut the message lost — and losing it loses the one thing that
# decides whether the answer can be matched at all, leaving a manager to write a reply
# nothing can claim. Leading with it also settles which token a reader extracts: the
# rule in scripts/ask-manager-contract.sh takes the first line carrying the prefix, and
# at the head that line is this wrapper's rather than anything the question quotes.
asked="An agent working on run $run is blocked on the question below until you answer it.
Reply with 'just channel-reply $run', sending a JSON object that carries a boolean
'completion' and states your decision in its 'message' — and echo this token verbatim
inside that 'message', so your answer is matched to this question rather than to another
reader. A reply on this channel is claimed by whichever reader reaches it next, so an
answer that does not carry the token is treated as somebody else's and a listener
re-arms. The token to echo:
$token

--
$question"

# What a re-arm says, and why it is deliberately not the question a second time. The
# question is already pending as this run's one blocking surface and stays the thing to
# answer; this only reports that somebody is listening for that answer again. The token
# still rides along, because a manager who answers here anyway has to be matched to the
# question rather than to another reader — but nothing here asks them to.
rearmed="A listener re-armed on run $run. This note needs no answer of its own.
If you answer here regardless, echo this token verbatim inside your reply's 'message',
so your answer is matched to the pending question rather than to another reader:
$token

--
An agent's blocking question is already pending on this channel and is still the surface
to answer. The listener waiting for that answer was handed a ruling addressed to another
reader instead — a reply here is claimed by whichever reader reaches one next — so it is
waiting again, and it is what will receive the answer to the pending question.

Answer the question, not this note."

# Each helper is checked rather than left to `set -e`, which would exit with whatever
# the helper printed and no repair — the one shape of failure this wrapper exists to
# not have. `|| frames=""` keeps the status from ending the script before the cause
# beneath it can be said.
frames=$(printf '%s' "$asked" | "$python" -c "$FRAME_PROGRAM" "$rearmed") || frames=""
# Split in the shell rather than by two more helpers: a helper here would need a check
# of its own for the same reason, and there is nothing to check when nothing is run.
# The `case` is what makes a single line a refusal — without it a one-line answer would
# leave both halves set to that line, and the emptiness check below would pass it.
newline=$'\n'
frame=""
rearm=""
case "$frames" in
    *"$newline"*)
        frame="${frames%%"$newline"*}"
        rearm="${frames#*"$newline"}"
        ;;
esac
if [ -z "$frame" ] || [ -z "$rearm" ]; then
    fail "the question could not be encoded as a channel frame by $python" \
        "restore the pinned toolchain with 'just bootstrap', then ask again"
fi

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
serving="$frame"
while true; do
    serve_status=0
    printf '%s\n' "$serving" \
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
    classified=$("$python" -c "$CLASSIFY_PROGRAM" "$TIMEOUT_ANSWER" "$token" "$TOKEN_PATTERN" <"$served_out") || classify_status=$?
    case "$classify_status" in
        0)
            # llmlint: ignore[tool_output_is_signal] The manager's answer IS this wrapper's output, and a decision at a fork is prose that arrives as many lines. Abridging it here would hand a dispatched agent a truncated ruling to act on, which is the one failure this whole wrapper exists to prevent; test_the_wrapper_answers_with_the_managers_message_and_nothing_else pins the message whole and nothing else on stdout.
            printf '%s\n' "$classified"
            exit 0
            ;;
        10)
            fail "no manager answered within ${window}s, and the channel answered its own timeout to say so" \
                "ask the manager to watch this run with 'just channel-next $run' before asking again, or raise ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS"
            ;;
        11)
            # A live graph edit the manager addressed to the engine reaches here
            # exactly this way. It is not prose and must never be reported as one.
            fail "the planner channel handed back something that is not a ruling: $classified" \
                "a ruling is a JSON object carrying a boolean 'completion'; a live graph edit routed here looks like this, so ask again once the edit has landed"
            ;;
        12 | 13)
            # llmlint: ignore[changed_behavior_has_e2e] This bound is driven to exhaustion end to end by test_a_run_whose_channel_keeps_answering_other_readers_is_given_up_on, which reaches it through 13. Reaching the same bound through 12 alone is not drivable against a real channel: it needs several orphaned answers waiting at once, and a run stops accepting replies the moment nothing blocking is pending — measured, the second `onepipeline reply` is refused with `run '<id>' has settled` — so a channel holds at most one.
            if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
                fail "the last $MAX_ATTEMPTS rulings on run $run's channel were answers to other readers, the most recent being: $classified" \
                    "ask the manager to include the token this question carries verbatim in their reply's message, then ask again"
            fi
            attempt=$((attempt + 1))
            if [ "$classify_status" -eq 12 ]; then
                # Another ask's answer, outliving the asker it was meant for. This
                # question's own blocking surface was never touched and is still the one
                # thing in front of the manager, so re-arm as a listener and leave it
                # the only question they see.
                serving="$rearm"
            else
                # This question's surface was answered without the echo, so it is spent.
                # Nothing blocking is left pending, and a run in that state refuses every
                # further reply — so the question goes back, or nobody can answer at all.
                serving="$frame"
            fi
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
