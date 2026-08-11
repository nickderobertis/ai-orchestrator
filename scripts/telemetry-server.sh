#!/usr/bin/env bash
# Serve the read-only DAG telemetry API from the published `onepipeline-api`.
#
# Two differences are absorbed here. The published verb names its runs root
# `--runs-root` and requires one, where the recipe's `--runs-dir` was optional and
# defaulted to the same runs directory every other view reads — so the default is
# supplied from `ONEPIPELINE_RUNS_DIR`, which is what `just runs` and `just status`
# read. And it takes one `--bind HOST:PORT` where the recipe took `--host` and
# `--port` separately.
set -euo pipefail

runs_root="${ONEPIPELINE_RUNS_DIR:-runs}"
host=""
port=""
args=()

while [ "$#" -gt 0 ]; do
    case "$1" in
        --runs-dir | --runs-root)
            [ "$#" -ge 2 ] || { echo "telemetry-server: $1 needs a directory" >&2; exit 2; }
            runs_root="$2"
            shift 2
            ;;
        --runs-dir=* | --runs-root=*)
            runs_root="${1#*=}"
            shift
            ;;
        --host)
            [ "$#" -ge 2 ] || { echo "telemetry-server: --host needs an address" >&2; exit 2; }
            host="$2"
            shift 2
            ;;
        --host=*)
            host="${1#*=}"
            shift
            ;;
        --port)
            [ "$#" -ge 2 ] || { echo "telemetry-server: --port needs a port" >&2; exit 2; }
            port="$2"
            shift 2
            ;;
        --port=*)
            port="${1#*=}"
            shift
            ;;
        *)
            args+=("$1")
            shift
            ;;
    esac
done

if [ -n "$host" ] || [ -n "$port" ]; then
    args+=(--bind "${host:-127.0.0.1}:${port:-8765}")
fi

exec uv run onepipeline-api serve --runs-root "$runs_root" "${args[@]}"
