#!/usr/bin/env bash
# Force the orchestrator's agent config; target-project discovery must not override it.
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(dirname -- "$script_dir")

if [ "${1-}" != "run" ]; then
    echo "oneharness-agent: expected the 'run' subcommand" >&2
    exit 2
fi
shift
exec oneharness run --config "$repo_root/oneharness.toml" "$@"
