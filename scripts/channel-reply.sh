#!/usr/bin/env bash
# Send one reply envelope over the live channel: `just channel-reply <RUN> [FILE]`.
#
# AGENTS.md, under "Answering on the channel", is the contract. Local invariants: the
# envelope is staged so the bytes judged are the bytes sent; the token grammar is
# `scripts/ask-manager-contract.sh`'s and is never restated here; a refusal refuses the
# whole envelope; an unreadable queue or an unresolvable run reference forwards; and the
# verb's own answer is the whole of what a successful reply writes to stdout, with
# anything this recipe has to say for itself going to stderr.
set -euo pipefail

#: Where `onepipeline` keeps its ledger, and so where the pending surface is. The same
#: default and the same override every planner-facing verb reads, resolved against the
#: working directory exactly as they resolve it.
RUNS_ROOT_ENV="ONEPIPELINE_RUNS_DIR"
DEFAULT_RUNS_ROOT="runs"

#: Where the channel's own queue lives under one run's root, as `onepipeline` writes
#: it: `pending` is the surface a reply binds to, carrying its `id`, `kind`, `blocking`
#: flag and `message`, and `waiting` is every surface nobody has read yet.
QUEUE_PATH="channel/queue.json"

#: The run's own journal, where the engine records what it did with each edit it
#: committed. An `edit-committed` event carries the submitted `command` and the
#: compiled `operations`, and a `note`'s operation carries the `reached` this recipe
#: reads back.
JOURNAL_PATH="events.jsonl"

# Decides whether the envelope on stdin is a verdict for a question this run has not
# handed out, which is the one thing an envelope can say about itself that makes it
# provably undeliverable.
#
#   0  send it — the queue could not be read, the envelope is not a ruling, or it names no
#      blocking question that is still waiting
#   1  refuse it — a ruling echoing the correlation token of a blocking question this run
#      has NOT handed out; the token and what is pending instead are on stdout
#
# Keyed on the envelope's own bytes and never on queue state, because a non-blocking
# reader — the monitor's scoring `channel serve` — takes a verdict whatever the queue
# holds, so any queue-state rule refuses monitor scores. Only `scripts/ask-manager.sh`
# mints one of these tokens, so a refusal keyed on one names a blocking question by
# construction. AGENTS.md records why both broader rules were rejected, and a manager who
# left the token out is answered by `scripts/ask-manager.sh` rather than here.
#
# `ruling_refusal`, `pending_token` and `answer_echoes` are
# `scripts/ask-manager-contract.sh`'s, embedded above this by the caller.
GUARD_PROGRAM='
import json, sys

queue_path, prefix = sys.argv[1], sys.argv[2]
envelope = sys.stdin.read()
try:
    with open(queue_path, encoding="utf-8") as handle:
        queue = json.load(handle)
except (OSError, ValueError):
    sys.exit(0)
if ruling_refusal(envelope) is not None:
    # Not a ruling, so it is not trying to bind to any question at all — a live edit is
    # the ordinary case — and this guard has nothing to say about it.
    sys.exit(0)
pending = queue.get("pending") if isinstance(queue, dict) else None
queued = queue.get("waiting") if isinstance(queue, dict) else None
for surface in queued if isinstance(queued, list) else []:
    if not isinstance(surface, dict) or surface.get("blocking") is not True:
        continue
    unread = surface.get("message")
    if not isinstance(unread, str):
        continue
    unheard = pending_token(unread, prefix)
    if unheard is None or not answer_echoes(envelope, unheard):
        continue
    # The envelope names a blocking question this run has not handed out. Nothing else
    # on this channel mints one of these tokens, so this is the envelope saying which
    # question it answers, not a guess from what the queue holds.
    kind = pending.get("kind") if isinstance(pending, dict) else None
    sys.stdout.write(
        "the question carrying %s is still waiting to be read, and %s"
        % (
            unheard,
            "a %s surface is what is pending instead" % kind
            if isinstance(kind, str)
            else "nothing at all has been handed out",
        )
    )
    sys.exit(1)
sys.exit(0)
'

# Merges what the engine recorded it did with each `note` this envelope carried into the
# verb's own answer, and writes that one line back out.
#
# **One line, not two.** The verb's answer is the whole of this recipe's stdout, so the
# outcome is carried inside it — `notes` beside `reply` and `state` — rather than printed
# beside it. Per note: `reached` is the engine's own word for which party of the node's
# conversation took it, `null` when nothing has decided it yet, and a `notes_unread`
# beside them says the journal could not be read at all, which is a different state from
# a note nothing has decided. An answer this cannot parse has nothing to merge into, so it
# is handed back whole and nothing is added beside it: stdout is one line or none. This
# program itself never fails; its caller says on stderr when it could not be run, because
# a receipt with no outcome in it and no reason beside it reads as a reply that carried no
# note.
#
# **`reached` is what the outcome is called, and it is the engine's word rather than this
# recipe's.** A note that reached a live turn and one carried to the node's next dispatch
# are the two ways an accepted note succeeds under the default, and they are materially
# different to whoever sent it: `worker`, `supervisor`, `judged-with` and `queued` say a
# conversation read it, and `carried` says the next dispatch will. A receipt that could
# not tell those apart is the incident this whole op was collapsed from.
#
# **Correlated rather than read by recency.** An `edit-committed` event is this reply's
# when it was appended after this reply was submitted *and* its command is one this
# envelope sent; the two are consumed one for one, in order, so an envelope carrying the
# same note twice reads back as two notes. Reading the newest outcome, or the last, or
# the only one, would answer with an earlier note's the moment this note's is not there
# yet — the case a manager most needs told apart.
#
# It looks once and never waits: exit 1 is the verb's published word for
# accepted-but-not-reconciled, which is the whole of the window in which an outcome is
# missing, and `just monitor` is where it can be read once it lands.
REPORT_PROGRAM='
import json, sys

journal_path, offset, answered = sys.argv[1], int(sys.argv[2]), sys.argv[3]


def handed_back(line):
    """The one line this recipe prints on success, or nothing when the verb said nothing."""
    if line:
        sys.stdout.write(line + "\n")


try:
    sent = json.loads(sys.stdin.read())
except ValueError:
    sent = None
commands = sent.get("commands") if isinstance(sent, dict) else None
notes = [
    command
    for command in (commands if isinstance(commands, list) else [])
    if isinstance(command, dict) and command.get("op") == "note"
]
if not notes:
    handed_back(answered)
    sys.exit(0)
unread = None
appended = ""
try:
    with open(journal_path, "rb") as handle:
        handle.seek(offset)
        appended = handle.read().decode("utf-8", "replace")
except OSError as unreadable:
    # A journal this could not open and a journal carrying no outcome yet are opposite
    # states — one is a broken read, the other a note nothing has decided the fate of —
    # so this is said in a field of its own rather than folded into an empty read.
    unread = "%s could not be read (%s)" % (journal_path, unreadable)
committed = []
for line in appended.splitlines():
    try:
        event = json.loads(line)
    except ValueError:
        continue
    if not isinstance(event, dict) or event.get("kind") != "edit-committed":
        continue
    payload = event.get("payload")
    if isinstance(payload, dict):
        committed.append(payload)
reported = []
for note in notes:
    node = note.get("id")
    recorded = None
    for at, payload in enumerate(committed):
        if payload.get("command") != note:
            continue
        operations = payload.get("operations")
        for operation in operations if isinstance(operations, list) else []:
            if isinstance(operation, dict) and operation.get("kind") == "note-delivered":
                recorded = operation.get("reached")
                break
        del committed[at]
        break
    reported.append(
        {
            "node": node if isinstance(node, str) else None,
            "reached": recorded if isinstance(recorded, str) else None,
        }
    )
carried = {"notes": reported}
if unread is not None:
    carried["notes_unread"] = unread
try:
    receipt = json.loads(answered)
except ValueError:
    receipt = None
if not isinstance(receipt, dict):
    # Nothing to merge into, so the verb keeps its answer whole and this adds nothing
    # beside it: success is one line or none. The pinned engine answers a JSON object on
    # both statuses this runs for, so what is given up here is an outcome on a path that
    # release does not take, and the run journal still carries it.
    handed_back(answered)
    sys.exit(0)
receipt.update(carried)
handed_back(json.dumps(receipt))
sys.exit(0)
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
# llmlint: ignore[changed_behavior_has_e2e] Reachable only when a file this script has just tested as a readable regular file fails to parse or evaluate; a journey producing that would have to corrupt the checkout it is reading itself from.
# shellcheck source=scripts/ask-manager-contract.sh
. "$contract_helper" || fail "the shared reply contract at $contract_helper could not be loaded, so what this recipe treats as a usable reply is undefined" \
    "restore it from the repository or run 'just bootstrap', then retry"

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

# llmlint: ignore[boundary_inputs_validated] This is `onepipeline`'s own configured ledger root, not an input this recipe owns a policy for: it reaches only two lenient reads — a queue whose unreadability forwards the envelope, and a journal whose size is checked where it is taken — and never a command line. Validating it here would be a second opinion on the verb's own configuration, and a stricter one would refuse replies the verb accepts.
runs_root="${!RUNS_ROOT_ENV:-$DEFAULT_RUNS_ROOT}"
# Empty when the run is not a reference this can safely resolve a path from, which is
# what keeps both reads — the guard's and the outcome's — off a path composed from a
# value nobody checked. Both are then skipped together, for the same reason.
run_root=""
# llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally, so the check would accept nothing.
if [[ "$run" =~ $ASK_MANAGER_SAFE_REFERENCE ]]; then
    run_root="$runs_root/$run"
fi

# Initialized beside the status rather than only inside the branch that sets them: the
# check below reads both, and a run this could not resolve a queue path from never enters
# that branch at all.
guard_status=0
guard_said=""
if [ -n "$run_root" ]; then
    guard_said=$("$python" -c "$ASK_MANAGER_RULING_SOURCE$ASK_MANAGER_TOKEN_SOURCE$GUARD_PROGRAM" \
        "$run_root/$QUEUE_PATH" "$ASK_MANAGER_TOKEN_PREFIX" <"$staged") || guard_status=$?
fi
# Every refusal below names itself on stdout, so a non-zero status with nothing there is
# the judging helper having failed rather than a verdict about the envelope. Checked
# rather than assumed, because a broken interpreter exits with a status of its own and
# that status can collide with one of these: read as a refusal it would refuse a reply
# nothing had judged, with an empty reason where the explanation should be — which is the
# exact failure this recipe exists to prevent, wearing this recipe's own voice.
if [ "$guard_status" -ne 0 ] && [ -z "$guard_said" ]; then
    fail "this reply to run $run could not be judged against the question waiting on it: $python exited $guard_status with nothing to say for it" \
        "restore the pinned toolchain with 'just bootstrap', then retry"
fi
case "$guard_status" in
    0) ;;
    1)
        fail "run $run is waiting on an agent's blocking question that nobody has read yet, so its rendezvous is not open and this envelope's 'completion' would be reported delivered to you having reached nobody — $guard_said" \
            "read the queue first: 'just channel-next $run' hands a blocking surface out ahead of every other kind, and once that question is the pending one this same envelope answers it. Nothing was sent, so an envelope carrying 'commands' beside the verdict applied no edit either, and re-sending those commands on their own still applies them"
        ;;
    *)
        # Reachable only when the judging helper itself could not run — a broken or
        # missing Python. Named as that rather than left to `set -e`, because the
        # alternative is a manager told nothing about a reply that was never sent.
        fail "this reply to run $run could not be judged against the question waiting on it: $python exited $guard_status" \
            "restore the pinned toolchain with 'just bootstrap', then retry"
        ;;
esac

# Where the journal already ends. Read before the send, because that is the whole of
# what makes an outcome below provably this reply's rather than an earlier note's. The
# count is checked rather than trusted: it is another program's output on its way to
# being an offset, and one that is not a plain count would silently seek somewhere else.
journal=""
journal_end=0
if [ -n "$run_root" ]; then
    journal="$run_root/$JOURNAL_PATH"
    if [ -f "$journal" ]; then
        # llmlint: ignore[changed_behavior_has_e2e] Reachable only when a file this line has just tested as a regular file cannot be measured, or when `wc` answers something that is not a count; driving either means breaking the filesystem or the toolchain the suite itself runs on.
        if ! measured=$(wc -c <"$journal") ||
            ! [[ "$measured" =~ ^[[:space:]]*([0-9]+)[[:space:]]*$ ]]; then
            fail "run $run's own journal at $journal could not be measured, so what this reply does to a note could not be told apart from what an earlier one did" \
                "check that it is a readable regular file, then retry; nothing has been sent"
        fi
        journal_end=${BASH_REMATCH[1]}
    fi
fi

# Run rather than `exec`: the staged copy is this process's to remove, and a replaced
# process runs no EXIT trap. The verb's own exit status is this recipe's.
#
# Its stdout is captured rather than left to flow through, because the note outcomes are
# merged into it: what this prints on success is the verb's answer and nothing beside it.
# Its stderr is not captured and reaches the caller as the verb wrote it.
status=0
delegated=$("$delegate" reply "$run" <"$staged") || status=$?

# Merged for an envelope the verb accepted — exit 0, applied, and exit 1, accepted and
# durable but not reconciled in time — and for neither of those is the receipt alone an
# answer about the note. A refusal sent nothing, so there is nothing to merge and nothing
# extra is printed.
if [ -n "$journal" ] && { [ "$status" -eq 0 ] || [ "$status" -eq 1 ]; }; then
    report_status=0
    reported=$("$python" -c "$REPORT_PROGRAM" "$journal" "$journal_end" "$delegated" \
        <"$staged") || report_status=$?
    if [ "$report_status" -eq 0 ]; then
        delegated="$reported"
    else
        # The envelope is already sent, so this is not a refusal: the verb's own answer
        # and its exit status stay the caller's, and stdout is still that answer alone.
        # What must not happen is silence. An answer carrying no note outcome reads
        # exactly like an answer to an envelope that carried no note, so a manager whose
        # read failed would be told nothing and conclude their note had no fate to
        # report — this recipe's own defect, worn one layer in. Named on stderr, with
        # where the outcome can still be read.
        printf 'channel-reply: %s\n' \
            "what this reply did with its note(s) could not be read back: $python exited $report_status; the reply itself was sent, and the engine still recorded each note's fate on run $run's own journal — read it with 'just monitor $run'" >&2
    fi
fi
[ -z "$delegated" ] || printf '%s\n' "$delegated"
exit "$status"
