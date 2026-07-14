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

exec oneharness run --config "$repo_root/oneharness.toml" "$@"
