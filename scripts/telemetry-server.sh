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

script_dir="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
# One source for the address this API answers on: `scripts/dag-ui-server.js` proxies
# to it, and `just dag-ui` finds `just telemetry-server` only while the two agree.
# Read where it is needed rather than up here, so an invocation that names its own
# address does not depend on a file it never consults.
address_file="$(dirname -- "$script_dir")/config/read-api.address"

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
    default_address="$(tr -d '[:space:]' <"$address_file")" || {
        echo "telemetry-server: could not read $address_file; restore it and retry" >&2
        exit 2
    }
    args+=(--bind "${host:-${default_address%%:*}}:${port:-${default_address##*:}}")
fi

exec uv run onepipeline-api serve --runs-root "$runs_root" "${args[@]}"
