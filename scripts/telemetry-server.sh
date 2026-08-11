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
# So this renders `--bind` from that file on every path, including the one that names
# neither half. Letting the published CLI's own default stand there would leave the
# address restated in a second place: they agree today, and an edit to this file alone
# would silently leave the API on the old port with the view still proxying to the new.
address_file="$(dirname -- "$script_dir")/config/read-api.address"

runs_root="${ONEPIPELINE_RUNS_DIR:-runs}"
host=""
port=""
bound=0
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
        # A caller who spells the published flag itself owns the whole address, and
        # gets it: two `--bind` values would leave which one binds up to the CLI.
        --bind | --bind=*)
            bound=1
            args+=("$1")
            shift
            ;;
        *)
            args+=("$1")
            shift
            ;;
    esac
done

# Validated here rather than left to the CLI, because these are joined into one
# `HOST:PORT` word: a host carrying a colon or a port that is not a number would
# reach `--bind` as an address neither this script nor the caller meant.
[ -z "$host" ] || [[ "$host" =~ ^([A-Za-z0-9._-]+|\[[0-9A-Fa-f:.]+\])$ ]] || {
    echo "telemetry-server: --host must be a hostname, an IPv4 address, or a bracketed IPv6 address, not '${host}'" >&2
    exit 2
}
[ -z "$port" ] || { [[ "$port" =~ ^[0-9]{1,5}$ ]] && [ "$port" -le 65535 ]; } || {
    echo "telemetry-server: --port must be a port number in 0-65535, not '${port}'" >&2
    exit 2
}

if [ "$bound" -eq 0 ]; then
    default_address="$(tr -d '[:space:]' <"$address_file")" || {
        echo "telemetry-server: could not read $address_file; restore it and retry" >&2
        exit 2
    }
    # Checked rather than assumed, as `scripts/dag-ui-server.js` checks the same
    # file: split on a colon that is not there, `--bind 8765:8765` is what the
    # published CLI would be asked to bind.
    [[ "$default_address" =~ ^[^[:space:]:]+:[0-9]{1,5}$ ]] || {
        echo "telemetry-server: $address_file must hold one HOST:PORT, not '${default_address}'" >&2
        exit 2
    }
    args+=(--bind "${host:-${default_address%%:*}}:${port:-${default_address##*:}}")
elif [ -n "$host" ] || [ -n "$port" ]; then
    echo "telemetry-server: --bind names the whole address; drop --host/--port or drop --bind" >&2
    exit 2
fi

exec uv run onepipeline-api serve --runs-root "$runs_root" "${args[@]}"
