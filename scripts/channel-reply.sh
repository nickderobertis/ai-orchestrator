#!/usr/bin/env bash
# Send one reply envelope over the live channel: `just channel-reply <RUN> [FILE]`.
#
# The envelope reaches `onepipeline reply` exactly as it was given — this adds no
# field, rewrites nothing, and forwards the caller's own arguments. What it adds is one
# refusal, for the one case that is provably unusable.
#
# **`onepipeline reply` answers `{"reply":N,"state":"delivered"}` whatever the envelope
# carried**, because `delivered` is a transport receipt: the engine took the envelope
# and handed it to whoever was waiting. Whether the waiting reader can *use* it is a
# different question, and the reader that cannot is `scripts/ask-manager.sh`: its
# classifier acts only on a JSON object carrying a boolean `completion` and discards
# everything else with nothing on the channel to say so. Three replies omitting
# `completion` were each reported delivered and each discarded, the asking planner
# re-asked twice — minting a new correlation token each time and reporting that the
# call had returned no usable text — and it stayed blocked for about thirty-five
# minutes while its manager had every reason to believe it had been answered.
#
# So an envelope the pending question cannot use is refused **here**, at the point it
# is sent, where the person who wrote it is still there to write another. The pending
# surface is left pending, which is what makes the refusal recoverable: the question is
# still the one thing in front of the manager and a usable reply still answers it.
#
# Deliberately narrow, and each half of that narrowness is load-bearing:
#
#   * it looks only at `runs/<run>/channel/queue.json`'s `pending` surface, and only
#     when that surface is **blocking** and carries the correlation-token prefix
#     `scripts/ask-manager.sh` mints — which `scripts/ask-manager-contract.sh` declares,
#     so this file never spells it. Nothing else on this channel has a reader that
#     discards what it cannot parse;
#   * it judges the envelope against that script's own rule, sourced from
#     `scripts/ask-manager-contract.sh` rather than restated, because a check that
#     disagreed with the reader it protects would refuse usable replies while passing
#     unusable ones;
# The envelope reaches the verb on its stdin whichever shape it arrived in, which is
# the one thing here that is not pure pass-through and is deliberate: the bytes judged
# and the bytes sent must be the same bytes, and a caller's file is theirs to change
# between one read and the other.
#
#   * and it refuses nothing else. A reply with no blocking question pending goes
#     through untouched, and so does one carrying `commands`. That second exemption is
#     not a nicety: a live edit carries no `completion` by design, the adopted release
#     routes it to the command path — measured, `{"reply":0,"state":"applied"}` with a
#     question pending on the same run — and it never reaches the waiting reader at
#     all. Refusing it would take a manager's steering away for the whole time a
#     question is unanswered, which is precisely when steering is most needed. What is
#     left is the envelope that was *meant* as an answer and cannot be one.
#
# A queue that cannot be read is not evidence of anything, so it forwards: this stands
# between a manager and a channel, and a guard that refused whenever it could not see
# is a guard that takes the channel away. A run this could not safely resolve a queue
# path from is that same case — the value came from whoever typed the command, and the
# reference grammar both ends of this channel already share is what says whether it can
# be one path segment under the ledger. Forwarded rather than refused, because
# `onepipeline reply` is what owns which run ids it accepts.
set -euo pipefail

#: Where `onepipeline` keeps its ledger, and so where the pending surface is. The same
#: default and the same override every planner-facing verb reads, resolved against the
#: working directory exactly as they resolve it.
RUNS_ROOT_ENV="ONEPIPELINE_RUNS_DIR"
DEFAULT_RUNS_ROOT="runs"

#: Where the channel's own queue lives under one run's root, as `onepipeline` writes
#: it: `pending` is the surface a reply binds to, carrying its `id`, `kind`, `blocking`
#: flag and `message`.
QUEUE_PATH="channel/queue.json"

# Decides whether the envelope on stdin can answer the question this run is waiting on.
# One invocation reads both, because the two halves are one question: an envelope is
# only unusable *relative to* a pending surface, and asking them separately would give
# the second a failure path the first cannot reach.
#
#   0  send it — nothing blocking is pending, the pending surface is somebody else's
#      kind, the queue could not be read, the envelope carries `commands` and so is an
#      edit rather than an answer, or the envelope is a usable ruling
#   1  refuse it — the reason is on stdout
#
# `ruling_refusal` is `scripts/ask-manager-contract.sh`'s and is embedded above this by
# the caller; see that file for why it is not restated here.
GUARD_PROGRAM='
import json, sys

queue_path, prefix = sys.argv[1], sys.argv[2]
envelope = sys.stdin.read()
try:
    with open(queue_path, encoding="utf-8") as handle:
        queue = json.load(handle)
except (OSError, ValueError):
    sys.exit(0)
pending = queue.get("pending") if isinstance(queue, dict) else None
if not isinstance(pending, dict) or pending.get("blocking") is not True:
    sys.exit(0)
waiting = pending.get("message")
if not isinstance(waiting, str) or prefix not in waiting:
    sys.exit(0)
try:
    sent = json.loads(envelope)
except ValueError:
    sent = None
if isinstance(sent, dict) and "commands" in sent:
    sys.exit(0)
refusal = ruling_refusal(envelope)
if refusal is None:
    sys.exit(0)
sys.stdout.write(refusal)
sys.exit(1)
'

fail() {
    echo "channel-reply: $1; $2" >&2
    exit 2
}

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on.
if ! script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd) ||
    ! root=$(CDPATH='' cd -- "$script_dir/.." && pwd); then
    fail "this recipe could not resolve the checkout it was run from" \
        "run it from inside a checkout, so the onepipeline it replies through is that checkout's"
fi

contract_helper="$script_dir/ask-manager-contract.sh"
if [ ! -f "$contract_helper" ] || [ ! -r "$contract_helper" ]; then
    fail "required helper is not a readable regular file: $contract_helper" \
        "restore it from the repository or run 'just bootstrap', then retry"
fi
# shellcheck source=scripts/ask-manager-contract.sh
. "$contract_helper"

python="$root/.venv/bin/python3"
[ -x "$python" ] || python=python3

delegate="$script_dir/onepipeline.sh"
if [ ! -x "$delegate" ]; then
    fail "the onepipeline wrapper is not executable at $delegate" \
        "restore it from the repository or run 'just bootstrap', then retry"
fi

# Every shape but the published one is forwarded unjudged, so this can never be the
# reason a reply the CLI would have accepted did not reach it: `onepipeline reply` owns
# its own surface, and a usage error is better reported by the verb that has one.
run="${1:-}"
if [ $# -lt 1 ] || [ $# -gt 2 ] || [ -z "$run" ]; then
    exec "$delegate" reply "$@"
fi
# An unreadable envelope file is the verb's to refuse, and it names the path it could
# not read; judging it here would replace that with a guess about what was in it.
# Checked before anything is staged, because a forward from below the trap would leave
# the staged copy behind: `exec` replaces the process, and a replaced process runs no
# EXIT trap.
if [ $# -eq 2 ] && [ ! -r "$2" ]; then
    exec "$delegate" reply "$@"
fi

# The envelope is staged once and the verb is given that copy, whichever shape it
# arrived in. Letting the verb re-read a caller's file instead would judge one set of
# bytes and send another: the file is the caller's to change between the two reads, and
# the whole of this guard is a claim about the bytes that reach the channel. Nothing is
# rewritten — the copy is byte for byte what was read.
staged=""
# `|| :` because a trap that fails takes the script's exit status with it: a cleanup
# that could not remove a temporary file must not turn a refusal into a different one.
trap 'rm -f "$staged" || :' EXIT
# llmlint: ignore[changed_behavior_has_e2e] Reachable only when TMPDIR stops being writable mid-run; a journey that made it unwritable would take the suite's own temporary files with it.
staged=$(mktemp) || fail "no temporary file could be made to hold the reply envelope" \
    "check that TMPDIR names a writable directory, then retry"

if [ $# -eq 2 ]; then
    # llmlint: ignore[changed_behavior_has_e2e] Reachable only when a file this script has already tested as readable fails midway through being read; driving it would mean failing the filesystem the suite runs on.
    cat -- "$2" >"$staged" || fail "the reply envelope at '$2' could not be read to the end" \
        "check that it is a regular readable file, then retry"
else
    # llmlint: ignore[changed_behavior_has_e2e] Reachable only when this process's own stdin fails while being read; a journey can close stdin, which reads as an empty envelope and is covered, but cannot make the read itself fail.
    cat >"$staged" || fail "the reply envelope could not be read from stdin" \
        "pipe the envelope in, or name a file after the run id"
fi

runs_root="${!RUNS_ROOT_ENV:-$DEFAULT_RUNS_ROOT}"
guard_status=0
# llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally, so the check would accept nothing.
if [[ "$run" =~ $ASK_MANAGER_SAFE_REFERENCE ]]; then
    refusal=$("$python" -c "$ASK_MANAGER_RULING_SOURCE$GUARD_PROGRAM" \
        "$runs_root/$run/$QUEUE_PATH" "$ASK_MANAGER_TOKEN_PREFIX" <"$staged") || guard_status=$?
fi
case "$guard_status" in
    0) ;;
    1)
        fail "run $run is waiting on an agent's blocking question and this envelope cannot answer it: $refusal" \
            "that question came from the ask-manager wrapper, which acts only on a JSON object carrying a boolean 'completion' and discards anything else without saying so — send one, echoing the question's token in its 'message'; the question is still pending"
        ;;
    *)
        # Reachable only when the judging helper itself could not run — a broken or
        # missing Python. Named as that rather than left to `set -e`, because the
        # alternative is a manager told nothing about a reply that was never sent.
        fail "this reply to run $run could not be judged against the question waiting on it: $python exited $guard_status" \
            "restore the pinned toolchain with 'just bootstrap', then retry"
        ;;
esac

# Run rather than `exec`: the staged copy is this process's to remove, and a replaced
# process runs no EXIT trap. The verb's own exit status is this recipe's.
status=0
"$delegate" reply "$run" <"$staged" || status=$?
exit "$status"
