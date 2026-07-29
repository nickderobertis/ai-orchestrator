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
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(dirname -- "$script_dir")
# The worker config maps this portable, non-secret parent value into
# CLAUDE_CONFIG_DIR only for its alternate-subscription child; the derivation is
# shared with the orchestrator wrapper so the two roles cannot drift apart.
alt_config_helper="$script_dir/claude-alt-config-dir.sh"
if [ ! -f "$alt_config_helper" ] || [ ! -r "$alt_config_helper" ]; then
    echo "oneharness-agent: required helper is not a readable regular file: $alt_config_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
# shellcheck source=scripts/claude-alt-config-dir.sh
. "$alt_config_helper"
resolve_claude_alt_config_dir oneharness-agent || exit $?
alternate_config_dir=$ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR
agent_config="$repo_root/oneharness.toml"

if [ "${1-}" != "run" ]; then
    echo "oneharness-agent: expected the 'run' subcommand; invoke through onejudge dispatch or retry as 'scripts/oneharness-agent.sh run ...'" >&2
    exit 2
fi
shift

caller_config=false
caller_config_path=
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
if [ ! -e "$alternate_config_dir" ] && [ -z "${ONEHARNESS_HARNESSES-}" ]; then
    # A host with only its primary Claude identity must not fail before fallback:
    # skip the absent alternate candidate and dispatch directly through Codex.
    export ONEHARNESS_HARNESSES=codex
fi

if [ -z "${ORCHESTRATOR_AGENT_STATUS_DIR-}" ]; then
    exec oneharness run --config "$agent_config" "$@"
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
if ! rm -f "$status_dir/agent.done" "$status_dir/agent.failed" "$status_dir/agent.exit_code"; then
    echo "oneharness-agent: cannot reset terminal markers; retry through orchestrator dispatch" >&2
    exit 2
fi
heartbeat_sequence=0
write_status agent.heartbeat "$heartbeat_sequence"

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
# the child's own stderr is the only account of why. Park it beside the terminal
# markers rather than letting it vanish with the process tree the dispatcher is
# about to tear down; it is replayed below so onejudge still receives it.
agent_stderr=$status_dir/agent.stderr
if ! : >"$agent_stderr"; then
    echo "oneharness-agent: cannot open the agent stderr record; retry through orchestrator dispatch" >&2
    exit 2
fi
# llmlint: ignore[tool_output_is_signal, boundary_inputs_validated] this wrapper is a transparent
# conduit for that protocol in both directions, exactly as the `exec` pass-throughs above are.
oneharness run --config "$agent_config" "$@" <&3 2>"$agent_stderr" &
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
set -e
# Record the status before replaying the stream: the dispatcher can conclude this
# worker died the moment the child leaves the process tree, and a large stderr
# would otherwise let it reach that conclusion before the reason was written down.
write_status agent.exit_code "$exit_code"
# llmlint: ignore[tool_output_is_signal] this replays the child's own stream to the
# caller unread; the conduit stays transparent, it just also keeps a copy.
if ! cat "$agent_stderr" >&2; then
    # An unreadable replay must not change the child's fate, which is already
    # decided and recorded; say so and let the exit code below stand.
    echo "oneharness-agent: could not replay the agent stderr record at $agent_stderr; read it from the worker status directory instead" >&2
fi
if [ "$exit_code" -ne 0 ]; then
    echo "oneharness-agent: agent process $agent_pid exited $exit_code; awaiting dispatcher recovery" >&2
    write_status agent.failed "$worker_pid"
    while :; do
        :
    done
fi
write_status agent.done "$worker_pid"
exit "$exit_code"
