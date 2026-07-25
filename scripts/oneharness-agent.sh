#!/usr/bin/env bash
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

if [ "${1-}" != "run" ]; then
    echo "oneharness-agent: expected the 'run' subcommand" >&2
    exit 2
fi
shift

for arg in "$@"; do
    case "$arg" in
        --config | --config=*)
            exec oneharness run "$@"
            ;;
    esac
done

if [ -z "${ORCHESTRATOR_AGENT_STATUS_DIR-}" ]; then
    exec oneharness run --config "$repo_root/oneharness.toml" "$@"
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
if ! rm -f "$status_dir/agent.done" "$status_dir/agent.failed"; then
    echo "oneharness-agent: cannot reset terminal markers; retry through orchestrator dispatch" >&2
    exit 2
fi
heartbeat_sequence=0
write_status agent.heartbeat "$heartbeat_sequence"

# llmlint: ignore[tool_output_is_signal] oneharness stdout is the provider protocol payload
# consumed by onejudge; suppressing or replacing it would break the real provider boundary.
oneharness run --config "$repo_root/oneharness.toml" "$@" &
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
if [ "$exit_code" -ne 0 ]; then
    echo "oneharness-agent: agent process $agent_pid exited $exit_code; awaiting dispatcher recovery" >&2
    write_status agent.failed "$worker_pid"
    while :; do
        :
    done
fi
write_status agent.done "$worker_pid"
exit "$exit_code"
