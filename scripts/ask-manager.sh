#!/usr/bin/env bash
# Ask the manager a blocking question over the planner channel, and answer with theirs.
#
# `ask-manager.sh <QUESTION-TEXT>` — the question as one argument (several words are
# joined). `ask-manager.sh --file <PATH>` reads it from a file, and `ask-manager.sh`
# with no arguments reads it from stdin, which is how a long question is piped in.
#
# Those three forms are the invocation contract every task this host dispatches spells
# out, and turning them into `onemessagebus ask` is the whole of what this does. The
# question goes to the bus as one planner-question frame on stdin, and the bus owns
# everything after that: the correlation the answer is bound by, the reply window, the
# listener a later session of the same asker takes back, and the one line of JSON that
# answers — `{"answer":"reply",…}` at exit 0, or `timeout`, `abandoned` or `refused`
# at exit 1. That line and that status are this script's, untouched. See
# docs/orchestration.md, "Asking the manager", for what the bus replaced and why.
#
# Nothing is spawned but the bus and nothing under the run root is opened: the frame is
# encoded with bash builtins, and the run's channel directory is named, never read.
#
# Environment:
#   ONEPIPELINE_RUN_ID                        (required) the run to ask. What sets it
#       depends on the launch: `just plan` exports it, and so does an attached
#       `just orchestrate` — a detached or adopted one does not, and an observer member
#       carries the run's id as well as a dispatch does. Measured per shape by
#       `tests/ask_seam/test_launch_ask_seam_e2e.py`.
#   ONEPIPELINE_RUNS_DIR                      (optional) where the run's records live;
#       `runs` when unset, as the engine reads it.
#   ONEPIPELINE_CHANNEL_ASKER                 (optional) who asks. A later listener naming
#       the same asker takes back a question an earlier one left abandoned. A blank one is
#       refused.
#   ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS  (optional) the reply window, 3000 by default.
#   ORCHESTRATOR_ASK_MANAGER_NODE             (optional) the node the question is about:
#       at most 512 bytes with no control character and not blank, as the bus takes it.
set -euo pipefail

#: The reply window, in seconds, when the caller names none: config/onemessagebus.yaml's
#: `reply_window_seconds`, which `ask` does not read from the file itself.
DEFAULT_TIMEOUT_SECONDS=3000

#: The queue a question is raised on, and what the frame says it is.
QUEUE=surfaces
QUESTION_KIND=planner-question
QUESTION_SOURCE=proposal

#: What a run id may be before it is composed into the channel directory's path: one
#: word, which cannot start a flag or climb out of the runs root.
SAFE_RUN_ID='^[A-Za-z0-9_][A-Za-z0-9_.-]*$'

fail() {
    printf 'ask-manager: %s; %s\n' "$1" "$2" >&2
    exit 2
}

# Sets `encoded` to the question as a JSON string, escaped with builtins alone. Every byte
# JSON reserves is escaped: the backslash and the quote, the three whitespace controls by
# name, and every other control character as `\u00XX`. Byte-wise under the C locale, so
# text in any encoding passes through as it was written.
store_json_string_in_encoded() {
    local LC_ALL=C text=$1 code hex control
    text=${text//\\/\\\\}
    text=${text//\"/\\\"}
    text=${text//$'\n'/\\n}
    text=${text//$'\r'/\\r}
    text=${text//$'\t'/\\t}
    for ((code = 1; code < 32; code++)); do
        printf -v hex '%02x' "$code"
        printf -v control '%b' "\\x$hex"
        text=${text//"$control"/\\u00$hex}
    done
    printf -v encoded '"%s"' "$text"
}

# Whether the question is text the frame can carry: well-formed UTF-8, which is what JSON
# is. Matched byte-wise with a builtin regex — one alternative per well-formed sequence,
# so an overlong form, a surrogate and a truncated sequence all fail — because the bus
# would otherwise be handed a malformed frame.
well_formed_utf8() {
    local LC_ALL=C tail pattern
    printf -v tail '[%b-%b]' '\x80' '\xbf'
    printf -v pattern '^([%b-%b]|[%b-%b]%s|%b[%b-%b]%s|[%b-%b%b%b]%s%s|%b[%b-%b]%s|%b[%b-%b]%s%s|[%b-%b]%s%s%s|%b[%b-%b]%s%s)*$' \
        '\x01' '\x7f' \
        '\xc2' '\xdf' "$tail" \
        '\xe0' '\xa0' '\xbf' "$tail" \
        '\xe1' '\xec' '\xee' '\xef' "$tail" "$tail" \
        '\xed' '\x80' '\x9f' "$tail" \
        '\xf0' '\x90' '\xbf' "$tail" "$tail" \
        '\xf1' '\xf3' "$tail" "$tail" "$tail" \
        '\xf4' '\x80' '\x8f' "$tail" "$tail"
    # llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally.
    [[ $1 =~ $pattern ]]
}

# Sets `question` to the whole of standard input; `$1` says where that input came from.
# `read -d ''` stops at a NUL or at the end of input: a NUL (0) is a byte no frame can carry
# and would cut the question short, and the end of input (1) is how every question ends. A
# read that failed — input that is a directory, say — exits 1 as well, and bash tells the two
# apart only by leaving the variable unset, measured, so that is what is asked of it.
read_question() {
    local status=0
    unset question
    IFS= read -r -d '' question || status=$?
    [ "$status" -ne 0 ] || fail "the question $1 carries a NUL byte, which no frame can carry" \
        "remove it and ask again; nothing was asked"
    if [ "$status" -ne 1 ] || [ -z "${question+set}" ]; then
        fail "the question $1 could not be read (read exited $status)" \
            "check that input, then ask again; nothing was asked"
    fi
}

# Whether `$1` is what the bus takes as what a question is about: at most 512 bytes, with no
# control character (a line break is one), and not blank — counted byte-wise, as the bus
# counts. A value it would refuse is refused here instead, naming the variable it came from;
# tests/e2e/test_ask_manager_shim_e2e.py holds this to the installed bus at each edge.
acceptable_about_value() {
    local LC_ALL=C
    [ "${#1}" -le 512 ] && [[ $1 != *[[:cntrl:]]* ]] && [ -n "${1//[[:space:]]/}" ]
}

here=${BASH_SOURCE[0]%/*}
[ "$here" != "${BASH_SOURCE[0]}" ] || here=.
case "$here" in
    /*) ;;
    *) here="$PWD/$here" ;;
esac
config="${here%/scripts}/config/onemessagebus.yaml"
[ "${here%/scripts}" != "$here" ] || config="$here/../config/onemessagebus.yaml"

case "${1:-}" in
    --file)
        [ $# -eq 2 ] || fail "--file takes exactly one path, got $(($# - 1))" "write the question to a file and name it once"
        [ -r "$2" ] || fail "the question file '$2' is not readable" "check the path, or pipe the question in on stdin instead"
        { read_question "in '$2'"; } <"$2" || fail "the question file '$2' could not be opened" \
            "check the path, or pipe the question in on stdin instead"
        ;;
    -*)
        fail "'$1' is not an option this script takes" \
            "pass the question as text, name a file with --file, or pipe it in on stdin"
        ;;
    "")
        read_question "on stdin"
        ;;
    *)
        question="$*"
        ;;
esac

[ -n "${question//[[:space:]]/}" ] || fail "the question is empty" \
    "state the decision fork and what each branch would change, so the manager can answer it in one reply"
well_formed_utf8 "$question" || fail "the question is not well-formed UTF-8 text, so no frame can carry it" \
    "re-encode the question as UTF-8 and ask again; nothing was asked"

run="${ONEPIPELINE_RUN_ID-}"
[ -n "$run" ] || fail "ONEPIPELINE_RUN_ID is not set, so there is no run whose channel to ask on" \
    "run this from inside a dispatch, which exports it, or export the run id from 'just runs' yourself"
# Byte-wise: under a UTF-8 locale `[A-Za-z]` also matches accented letters, which name no
# run the engine mints.
safe_run_id() {
    local LC_ALL=C
    # llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally, so the check would accept nothing.
    [[ $1 =~ $SAFE_RUN_ID ]]
}
safe_run_id "$run" || fail "ONEPIPELINE_RUN_ID is '$run', which names no run directory" \
    "a run id is one word of letters, digits, '_', '.', and '-'; check it against the runs 'just runs' lists"

window="${ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS:-$DEFAULT_TIMEOUT_SECONDS}"
[[ "$window" =~ ^[0-9]+$ ]] || fail "ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS is '$window', which is not a number of seconds" \
    "set it to a whole number of seconds, or unset it for the ${DEFAULT_TIMEOUT_SECONDS}s default"

asker="${ONEPIPELINE_CHANNEL_ASKER:-}"
[ -z "$asker" ] || [ -n "${asker//[[:space:]]/}" ] || fail "ONEPIPELINE_CHANNEL_ASKER is blank, so it names no asker" \
    "unset it, or set it to the word a later session of this dispatch asks as"
node="${ORCHESTRATOR_ASK_MANAGER_NODE:-}"
[ -z "$node" ] || acceptable_about_value "$node" || fail "ORCHESTRATOR_ASK_MANAGER_NODE is not at most 512 bytes of non-blank text free of control characters, so no question can be about it" \
    "set it to the id of the node the question is about, or unset it"

encoded=""
store_json_string_in_encoded "$question"
frame="{\"kind\":\"$QUESTION_KIND\",\"message\":$encoded,\"source\":\"$QUESTION_SOURCE\"}"

# llmlint: ignore[boundary_inputs_validated] `ONEPIPELINE_RUNS_DIR` is the engine's own export naming the runs root, and it reaches nothing but the bus's `--transport-dir`, which refuses an unusable directory naming it; the run id composed beneath it is validated above.
arguments=(ask "$QUEUE" --blocking --config "$config"
    --transport-dir "${ONEPIPELINE_RUNS_DIR:-runs}/$run/channel")
[ -z "$asker" ] || arguments+=(--asker "$asker")
arguments+=(--timeout "$window")
[ -z "$node" ] || arguments+=(--about "$node")

command -v onemessagebus >/dev/null || fail "onemessagebus is not on PATH, so there is no bus to ask on; nothing was asked" \
    "run this from inside a dispatch, whose environment carries it, or run 'just bootstrap' in this checkout"
# llmlint: ignore[tool_output_is_signal] The bus's one line and exit status are this script's whole answer by contract, untouched, so an asker reads `timeout`, `abandoned` or `refused` as the bus names it; the bus's own stderr says what to do next, and anything added here would be a second answer beside it.
exec onemessagebus "${arguments[@]}" <<<"$frame"
