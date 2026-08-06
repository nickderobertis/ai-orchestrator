#!/usr/bin/env bash
# llmlint: ignore-file[changed_behavior_has_e2e] subprocess tests drive every wrapper branch; only the paid oneharness child is replaced at the repository's designated external seam.
# Force the orchestrator's agent config; target-project discovery must not override it.
#
# onejudge routes BOTH conversation sides through this one provider.bin: the agent
# turn (whose args carry no --config) and the judge / simulated-user turn (whose
# args already carry `--config <judge_config>` from onejudge). Injecting the agent
# config unconditionally would hand `oneharness run` two --config flags, which it
# rejects ("cannot be used multiple times"). So force the agent config only when the
# caller has not already chosen one — that is exactly the agent side; the judge side
# passes through untouched, keeping its own config.
#
# That same branch is where each side's harness SELECTION is resolved. oneharness's
# own ONEHARNESS_HARNESSES is process-wide and beats config, so one value set by the
# parent would move both sides at once. ORCHESTRATOR_WORKER_HARNESSES and
# ORCHESTRATOR_JUDGE_HARNESSES are per-side instead: each is applied to only its own
# branch's `exec`, so a side carrying an explicit value never inherits the other
# side's — nor an ambient process-wide one. See orchestrator/harnesses.py, which
# validates both against the configs before a dispatch ever starts.
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(dirname -- "$script_dir")
# The worker config maps these portable, non-secret parent values into
# CLAUDE_CONFIG_DIR for its two alternate-subscription children; the derivation is
# shared with the other wrappers so the roles cannot drift apart.
alt_config_helper="$script_dir/claude-alt-config-dir.sh"
if [ ! -f "$alt_config_helper" ] || [ ! -r "$alt_config_helper" ]; then
    echo "oneharness-agent: required helper is not a readable regular file: $alt_config_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
# shellcheck source=scripts/claude-alt-config-dir.sh
. "$alt_config_helper"
resolve_claude_alt_config_dir oneharness-agent || exit $?
alternate_config_dir=$ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR
alternate2_config_dir=$ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR
# The worker chain's last candidate is a second Codex identity, whose variant maps
# this portable value into CODEX_HOME. oneharness refuses to run when the
# indirection is unset, so it must be exported even on a host that never
# authenticated one; see the fallthrough note in the helper.
codex_alt_helper="$script_dir/codex-alt-home.sh"
if [ ! -f "$codex_alt_helper" ] || [ ! -r "$codex_alt_helper" ]; then
    echo "oneharness-agent: required helper is not a readable regular file: $codex_alt_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
# shellcheck source=scripts/codex-alt-home.sh
. "$codex_alt_helper"
ensure_codex_alt_home oneharness-agent || exit $?
alternate_harness=claude-code:alternate
agent_config="$repo_root/oneharness.toml"
# Both are `run` flags with no config key, so this wrapper is the only place the
# agent side can adopt either, and neither may be repeated -- hence two arrays
# rather than one string, each emptied where the caller already asked for it. The
# selection between them is made below; why it exists is in
# docs/onejudge-integration.md, "Streaming the agent side".
agent_events=(--events)
agent_stream=()
# The filter that reconciles a streamed turn with onejudge, which parses this
# process's stdout as exactly one JSON document. See the script for what it does.
stream_filter="$script_dir/oneharness-stream.py"

# Emit the identities a config's `harnesses` chain names, in order, one per line;
# emit nothing when it declares none, and fail non-zero when the file cannot be
# read, cannot be parsed, or declares a chain that is not a list of identities. A
# malformed chain is refused rather than filtered down to its usable members: the
# selection it would authorize is not the one the file was trying to declare.
#
# Read with tomllib rather than by scanning the file here, because this is the
# second reader of one contract: orchestrator/harnesses.py validates a selection
# against the same key before a dispatch starts, and this wrapper checks it again
# at the variable's own boundary. A hand-rolled scanner would be a second answer to
# the same question — one that could take an identity quoted inside a comment for a
# configured one, or miss one a valid file wrote differently.

# The one interpreter every python helper here runs: this repository's own where it
# exists, so both readers of that contract run the same tomllib and the stream filter
# runs the same build the rest of the harness does. A worktree with no virtualenv yet
# still resolves the system `python3`.
repo_interpreter() {
    if [ -x "$repo_root/.venv/bin/python3" ]; then
        printf '%s\n' "$repo_root/.venv/bin/python3"
    else
        printf '%s\n' python3
    fi
}

config_harness_chain() {
    local interpreter
    interpreter=$(repo_interpreter)
    "$interpreter" -c '
import sys, tomllib

try:
    with open(sys.argv[1], "rb") as config:
        chain = tomllib.load(config).get("harnesses")
except (OSError, tomllib.TOMLDecodeError) as error:
    sys.exit(f"oneharness-agent: cannot read {sys.argv[1]}: {error}")
if chain is None:
    sys.exit(0)
if not isinstance(chain, list) or not all(
    isinstance(identity, str) and identity for identity in chain
):
    sys.exit(f"oneharness-agent: {sys.argv[1]} declares a malformed harnesses chain: {chain!r}")
for identity in chain:
    print(identity)
' "$1"
}

# Apply one side's selection to this process only, after checking it against the
# config that side is about to run from. The dispatch layer validates the same way
# before anything starts (orchestrator/harnesses.py), but this is the boundary a
# hand-set variable arrives at, and a selection nobody can honor must stop the turn
# rather than reach oneharness as a chain it will run something else for.
# $1 names the variable for diagnostics, $2 is its value, $3 the config to check.
apply_side_selection() {
    local variable=$1 value=$2 config=$3 selectable candidate
    local -a requested
    if ! selectable=$(config_harness_chain "$config" | tr '\n' ' '); then
        echo "oneharness-agent: cannot read the harness chain $variable is selected from; correct $config, or unset $variable to use its chain in order, then retry" >&2
        return 2
    fi
    if [ -z "${selectable// /}" ]; then
        echo "oneharness-agent: $config declares no 'harnesses' chain to select from; restore it from the repository, then retry" >&2
        return 2
    fi
    # Split on commas alone: an unquoted expansion would also glob, so a value
    # containing `*` could silently become whatever the cwd happens to hold.
    IFS=',' read -r -a requested <<<"$value"
    # Space-delimited on both sides so one identity cannot match another by prefix.
    for candidate in "${requested[@]}"; do
        case " $selectable" in
            *" $candidate "*) ;;
            *)
                echo "oneharness-agent: $variable '$value': '$candidate' is not a harness $config configures; select from ${selectable% }" >&2
                return 2
                ;;
        esac
    done
    export ONEHARNESS_HARNESSES="$value"
}

# Whether one `key=value` pair satisfies oneharness's history-label contract: a key
# of 1-64 ASCII letters/digits/dot/underscore/hyphen starting alphanumeric, and a
# non-empty value of at most 256 characters carrying no control character. The comma
# the contract also forbids cannot survive the split below. This mirrors
# orchestrator/labels.py, which is the validating *writer* of the same contract; this
# is the boundary a value hand-set in the environment arrives at instead.
valid_history_label() {
    local pair=$1 key value
    case "$pair" in
        *=*) ;;
        *) return 1 ;;
    esac
    key=${pair%%=*}
    value=${pair#*=}
    [[ $key =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || return 1
    [ -n "$value" ] && [ "${#value}" -le 256 ] || return 1
    [[ $value =~ [[:cntrl:]] ]] && return 1
    return 0
}

# Drop one key from this process's ONEHARNESS_HISTORY_LABELS, keeping every other
# valid pair in the order it arrived. A pair that violates the contract is dropped
# rather than rewritten or passed on, matching how `orchestrator.labels.parse_labels`
# treats an inherited value: what this rewrites is not this process's to correct, and
# a rewrite must not hand oneharness a list it would refuse. The variable is unset
# rather than left empty when nothing survives, because an empty list is not a value
# oneharness accepts.
drop_history_label() {
    local key=$1 pair
    local kept=''
    local -a pairs
    [ -n "${ONEHARNESS_HISTORY_LABELS-}" ] || return 0
    # Split on commas alone, the wire format's one separator; an unquoted expansion
    # would also glob, so a value containing `*` could become whatever the cwd holds.
    IFS=',' read -r -a pairs <<<"$ONEHARNESS_HISTORY_LABELS"
    for pair in "${pairs[@]}"; do
        case "$pair" in
            "$key="*) continue ;;
        esac
        valid_history_label "$pair" || continue
        kept="${kept:+$kept,}$pair"
    done
    if [ -n "$kept" ]; then
        export ONEHARNESS_HISTORY_LABELS="$kept"
    else
        unset ONEHARNESS_HISTORY_LABELS
    fi
}

if [ "${1-}" != "run" ]; then
    echo "oneharness-agent: expected the 'run' subcommand; invoke through onejudge dispatch or retry as 'scripts/oneharness-agent.sh run ...'" >&2
    exit 2
fi
shift

caller_config=false
caller_config_path=
caller_stream=false
expect_config_value=false
# llmlint: ignore[boundary_inputs_validated] this repository's dispatch layer is the only caller and passes exactly one --config; oneharness honors the last value, which this wrapper validates.
for arg in "$@"; do
    if [[ $expect_config_value == true ]]; then
        if [[ -z $arg ]]; then
            echo "oneharness-agent: --config requires a non-empty path; retry with '--config /absolute/path/to/config.toml'" >&2
            exit 2
        fi
        caller_config_path=$arg
        expect_config_value=false
        continue
    fi
    case "$arg" in
        --config)
            if [[ $caller_config == true ]]; then
                echo "oneharness-agent: --config may be provided only once; remove duplicate config arguments and retry" >&2
                exit 2
            fi
            caller_config=true
            expect_config_value=true
            ;;
        --events)
            agent_events=()
            ;;
        --stream)
            # A caller that streams owns its own stdout shape, so this wrapper adds
            # neither a second `--stream` (oneharness refuses the repeat) nor the
            # filter that would rewrite the stream into a buffered report.
            caller_stream=true
            ;;
        --config=*)
            if [[ $caller_config == true ]]; then
                echo "oneharness-agent: --config may be provided only once; remove duplicate config arguments and retry" >&2
                exit 2
            fi
            if [[ -z ${arg#--config=} ]]; then
                echo "oneharness-agent: --config requires a non-empty path; retry with '--config=/absolute/path/to/config.toml'" >&2
                exit 2
            fi
            caller_config=true
            caller_config_path=${arg#--config=}
            ;;
    esac
done
if [[ $expect_config_value == true ]]; then
    echo "oneharness-agent: --config requires a path; retry with '--config /absolute/path/to/config.toml'" >&2
    exit 2
fi
if [[ $caller_config == true ]]; then
    if [[ ! -f "$caller_config_path" || ! -r "$caller_config_path" ]]; then
        echo "oneharness-agent: caller config is not a readable regular file: $caller_config_path; correct the path and retry" >&2
        exit 2
    fi
    # This is the judge / simulated-user side, so only its own override applies —
    # and it applies over whatever ONEHARNESS_HARNESSES the parent exported, which
    # is what keeps a worker-side selection from reaching this conversation. It is
    # checked against the caller's own config, the one this turn will run from.
    if [ -n "${ORCHESTRATOR_JUDGE_HARNESSES-}" ]; then
        apply_side_selection ORCHESTRATOR_JUDGE_HARNESSES \
            "$ORCHESTRATOR_JUDGE_HARNESSES" "$caller_config_path" || exit "$?"
    fi
    # The `agent_role` a dispatch stamps names the WORKER it dispatched, and this is
    # the other side of that conversation. oneharness merges history labels
    # CLI > env > project file, so that inherited value outranked
    # oneharness.judge.toml's own `agent_role = "judge"` and every supervisor session
    # in the store was recorded as its worker's role — which is what showed an
    # operator a strict-evaluator transcript under a row labelled "worker". This
    # branch is the one place that knows which side it is, so the key is dropped here
    # and the judge config's own label stands. Every other inherited label locates the
    # dispatch in the graph and is kept exactly as it arrived.
    drop_history_label agent_role
    # Keep the portable indirection available while oneharness resolves config.
    # The judge's explicit primary variant does not consume it and masks
    # CLAUDE_CONFIG_DIR, but oneharness may still discover and layer the project
    # config before applying the caller's --config.
    exec oneharness run "$@"
fi

if [ ! -f "$agent_config" ] || [ ! -r "$agent_config" ]; then
    echo "oneharness-agent: required agent config is not a readable regular file: $agent_config; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
if [ -n "${ORCHESTRATOR_WORKER_HARNESSES-}" ]; then
    # This is the agent side, so only its own override applies — over an ambient
    # ONEHARNESS_HARNESSES as well, since a process-wide value the parent exported
    # is exactly what an explicit per-side choice exists to displace.
    #
    # Taken verbatim: the filtering below narrows a chain nobody chose, whereas
    # dropping an identity an operator named would run a provider they did not ask
    # for. An unauthenticated one fails at the provider instead, loudly.
    apply_side_selection ORCHESTRATOR_WORKER_HARNESSES \
        "$ORCHESTRATOR_WORKER_HARNESSES" "$agent_config" || exit "$?"
elif [ -z "${ONEHARNESS_HARNESSES-}" ]; then
    # An alternate Claude subscription whose config directory does not exist is a
    # candidate this host has never set up. claude-code would still start, create
    # that directory, and report `auth` — so the chain recovers either way, but
    # substituting a filtered one keeps the dispatch from writing a config
    # directory for an account nobody has logged into.
    #
    # Only those absent candidates are dropped: every other identity keeps its
    # configured relative order, including the OTHER alternate subscription when
    # just one is missing, both Codex identities, and the primary Claude one. The
    # chain is read from the config rather than restated here, so this can only
    # ever be a subsequence of what oneharness would have selected.
    # Space-delimited on BOTH sides, so `claude-code:alternate` cannot match the
    # `claude-code:alternate2` entry by prefix and drop a candidate that is present.
    absent_alternates=" "
    [ -e "$alternate_config_dir" ] || absent_alternates="${absent_alternates}claude-code:alternate "
    [ -e "$alternate2_config_dir" ] || absent_alternates="${absent_alternates}claude-code:alternate2 "
    if [ "$absent_alternates" != " " ]; then
        substituted=
        # Read the chain into a variable first: inside a process substitution the
        # reader's own failure would be invisible here, and a config it could not
        # parse would look exactly like one that named nothing to drop.
        if configured_chain=$(config_harness_chain "$agent_config"); then
            while read -r candidate; do
                case "$absent_alternates" in
                    *" $candidate "*) continue ;;
                esac
                substituted="${substituted:+$substituted,}$candidate"
            done <<<"$configured_chain"
        else
            echo "oneharness-agent: could not read the configured harness chain from $agent_config; leaving the selection to oneharness" >&2
        fi
        # An empty result means the config declared no chain to narrow; leave the
        # selection alone rather than narrowing it on a guess.
        if [ -n "$substituted" ]; then
            export ONEHARNESS_HARNESSES="$substituted"
        fi
    fi
fi

if [ -z "${ORCHESTRATOR_AGENT_STATUS_DIR-}" ]; then
    # No status directory means no dispatch is watching, so a streamed turn would
    # have nowhere to publish and nobody to read it. `--events` carries the identical
    # transcript in the end-of-turn report, with one fewer moving part and without
    # replacing this `exec` with a filtered pipeline.
    exec oneharness run --config "$agent_config" "${agent_events[@]}" "$@"
fi

status_dir=$ORCHESTRATOR_AGENT_STATUS_DIR
case "$status_dir" in
    /*/orchestrator-watchdog-*/agent) ;;
    *)
        echo "oneharness-agent: invalid worker status directory; retry through orchestrator dispatch" >&2
        exit 2
        ;;
esac
if [ ! -d "$status_dir" ] || [ -L "$status_dir" ]; then
    echo "oneharness-agent: worker status directory is absent or unsafe; retry through orchestrator dispatch" >&2
    exit 2
fi
write_status() {
    status_name=$1
    status_value=$2
    if ! printf '%s\n' "$status_value" >"$status_dir/$status_name.tmp" ||
        ! mv "$status_dir/$status_name.tmp" "$status_dir/$status_name"; then
        echo "oneharness-agent: cannot update $status_name; retry through orchestrator dispatch" >&2
        exit 2
    fi
}
worker_pid=$$
write_status agent.pid "$worker_pid"
if ! rm -f "$status_dir/agent.done" "$status_dir/agent.failed" "$status_dir/agent.exit_code" \
    "$status_dir/agent.failure"; then
    echo "oneharness-agent: cannot reset terminal markers; retry through orchestrator dispatch" >&2
    exit 2
fi
heartbeat_sequence=0
write_status agent.heartbeat "$heartbeat_sequence"
agent_activity=$status_dir/agent.activity

# Ask oneharness itself whether this invocation can be streamed, rather than
# predicting it here: `--print-command` applies a real run's validation and spawns
# nothing, so one question covers every reason the answer might be no. The caller's
# arguments go in verbatim so it describes the real invocation, and stdin is closed
# so a `--prompt-file -` cannot eat the task this turn is about to be given.
# See docs/onejudge-integration.md, "Streaming the agent side".
stream_supported() {
    oneharness run --config "$agent_config" --stream --print-command "$@" \
        >/dev/null 2>&1 </dev/null
}

stream_events=false
if [ "$caller_stream" = false ] && [ -f "$stream_filter" ] && [ -r "$stream_filter" ] &&
    stream_supported "$@"; then
    stream_events=true
    agent_stream=(--stream)
fi

# onejudge hands the agent its task on stdin, but a non-interactive shell assigns /dev/null to
# an asynchronous list's stdin before any explicit redirection, so the backgrounded agent below
# would read an empty prompt and ask for a subtask every turn until it hit the cap. The judge,
# which reaches oneharness through the `exec` pass-throughs above, is never backgrounded and so
# always saw its task -- that asymmetry is the bug. `<&0` would only re-duplicate the /dev/null
# already on fd 0, so save the real stdin here and redirect it back explicitly below.
# llmlint: ignore[boundary_inputs_validated] this duplicates a file descriptor; the payload it
# carries is the onejudge protocol that oneharness itself parses and validates.
exec 3<&0
# A worker that dies before its first turn produces no report and no transcript, so
# the child's own stderr is the only account of why — throttling, quota exhaustion,
# an OOM kill, and a genuine crash are indistinguishable without it. Park it beside
# the terminal markers rather than letting it vanish with the process tree the
# dispatcher is about to tear down; a failing exit replays it below, a successful one
# does not. Credential values are stripped when the dispatcher reads this back.
agent_stderr=$status_dir/agent.stderr
agent_stdout=$status_dir/agent.stdout
if ! : >"$agent_stderr"; then
    echo "oneharness-agent: cannot open the agent stderr record; retry through orchestrator dispatch" >&2
    exit 2
fi
if ! : >"$agent_stdout"; then
    echo "oneharness-agent: cannot open the agent stdout record; retry through orchestrator dispatch" >&2
    exit 2
fi
stdout_fifo=$status_dir/.agent-stdout-pipe
if ! mkfifo "$stdout_fifo"; then
    echo "oneharness-agent: cannot create the agent stdout capture pipe; retry through orchestrator dispatch" >&2
    exit 2
fi
# Both readers say nothing unless the capture itself fails, and that account goes
# into the same durable record the child's own stderr does — appended, so the child
# truncating it at open cannot take the reader's reason with it. Left on the
# wrapper's own stderr it would vanish with the process tree the dispatcher tears
# down, which is the one place a failure explains itself.
if [ "$stream_events" = true ]; then
    # The streamed conduit: the same transparent stdout capture, plus the live
    # activity publication and the unwrapping onejudge's single-document parse
    # needs. Both readers write `$agent_stdout` byte for byte, so everything that
    # reads the raw record back — the quota diagnostic below, the dispatcher —
    # cannot tell which one ran.
    # llmlint: ignore[tool_output_is_signal] the stream filter is the transparent stdout side of the oneharness protocol conduit.
    "$(repo_interpreter)" "$stream_filter" "$agent_stdout" "$agent_activity" <"$stdout_fifo" 2>>"$agent_stderr" &
else
    # llmlint: ignore[tool_output_is_signal] tee is the transparent stdout side of the oneharness protocol conduit.
    tee "$agent_stdout" <"$stdout_fifo" 2>>"$agent_stderr" &
fi
capture_pid=$!
# llmlint: ignore[boundary_inputs_validated] oneharness parses and validates its own protocol input.
oneharness run --config "$agent_config" "${agent_stream[@]}" "${agent_events[@]}" "$@" <&3 >"$stdout_fifo" 2>"$agent_stderr" &
agent_pid=$!
write_status agent.child.pid "$agent_pid"
while agent_state=$(ps -o stat= -p "$agent_pid" 2>/dev/null) &&
    [ -n "$agent_state" ] &&
    [ "${agent_state#Z}" = "$agent_state" ]; do
    heartbeat_sequence=$((heartbeat_sequence + 1))
    write_status agent.heartbeat "$heartbeat_sequence"
    sleep 0.5
done
set +e
wait "$agent_pid"
exit_code=$?
wait "$capture_pid"
capture_exit_code=$?
rm -f "$stdout_fifo"
set -e
if [ "$capture_exit_code" -ne 0 ]; then
    echo "oneharness-agent: agent stdout capture failed with exit $capture_exit_code; the turn's own output above this line is all that was kept, and the reader's reason is at $agent_stderr — retry through orchestrator dispatch, which creates the status directory the capture writes into" >>"$agent_stderr"
    if [ "$exit_code" -eq 0 ]; then
        exit_code=2
    fi
fi
# Record the status before replaying the stream: the dispatcher can conclude this
# worker died the moment the child leaves the process tree, and a large stderr
# would otherwise let it reach that conclusion before the reason was written down.
write_status agent.exit_code "$exit_code"
if [ "$exit_code" -ne 0 ]; then
    if grep -Fq "was created on harness" "$agent_stderr" &&
        grep -Fq "cannot be continued on" "$agent_stderr"; then
        echo "oneharness-agent: dispatch failure: session/harness binding rejection; the named session and both harnesses are shown below; retry with a new --session or the originally bound harness" >>"$agent_stderr"
    fi
    quota_line=$(grep -E -m1 "hit your (session|usage) limit|quota exhausted|rate.?limit" "$agent_stdout" || true)
    if [ -n "$quota_line" ]; then
        echo "oneharness-agent: dispatch failure: harness $alternate_harness is out of quota; $quota_line; configure a usable fallback or retry after the stated reset time" >>"$agent_stderr"
    fi
    # Replay the child's own words only now. A turn that succeeded says everything
    # it has to say through the protocol on stdout, so its harness chatter is noise
    # here; a turn that failed leaves this stream as the only account of why. The
    # record in the status directory is written either way, so nothing is lost by
    # staying quiet on the way out.
    if ! cat "$agent_stderr" >&2; then
        # An unreadable replay must not change the child's fate, which is already
        # decided and recorded; say so and let the exit code below stand.
        echo "oneharness-agent: could not replay the agent stderr record at $agent_stderr; read it from the worker status directory instead" >&2
    fi
    echo "oneharness-agent: agent process $agent_pid exited $exit_code; awaiting dispatcher recovery" >&2
    # The stderr copy above is best-effort by design: failing a live agent turn
    # because a diagnostic copy could not be written would be strictly worse than
    # losing the copy. What must not happen is reporting a capture that stopped
    # working as "the harness said nothing", so re-check it here and say so.
    capture=""
    if ! : >>"$status_dir/agent.stderr"; then
        capture="; agent stderr capture became unwritable, so its tail may be incomplete"
    fi
    # Written before the marker the dispatcher polls for, so the reason is always
    # already there when the failure is observed.
    if [ "$exit_code" -gt 128 ]; then
        write_status agent.failure "agent harness killed by signal $((exit_code - 128))$capture"
    else
        write_status agent.failure "agent harness exited $exit_code$capture"
    fi
    write_status agent.failed "$worker_pid"
    # Stay alive so the dispatcher can observe this failure and recover the tree it
    # is about to tear down — but do nothing while waiting. The empty loop this
    # replaces pinned a whole core at 100% for the entire recovery window, on a host
    # whose every other dispatch was competing for the same cores.
    while :; do
        sleep 3600
    done
fi
write_status agent.done "$worker_pid"
exit "$exit_code"
