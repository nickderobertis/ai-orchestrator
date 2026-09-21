#!/usr/bin/env bash
# Serve the DAG Observatory's API from the published `onepipeline-api`.
#
# **It is not a read-only API any more, and that is why this script names a session.**
# From the adopted release every post-launch verb is a route — the channel and its
# reply, `attest`, `stop`, `adopt`, `watch` — and the server performs each one as **one
# acting session**, the `--session` it was started with. So this hands it the identity
# `scripts/onepipeline.sh` gives every `onepipeline` recipe, through the one definition
# of that ladder, and a stop issued from the browser is then this manager's stop.
#
# An **unattributed server owns nothing**: started with no session it is refused every
# stop it does not force, and `GET /api/v2/unwatched` reports no run — which reads from
# the browser as a host with nothing to supervise rather than as a server that cannot
# say who it is. So the session is passed whenever this host can name one, and omitted
# rather than invented when it cannot.
#
# Three differences are absorbed here. The published verb names its runs root
# `--runs-root` and requires one, where the recipe's `--runs-dir` was optional and
# defaulted to the same runs directory every other view reads — so the default is
# supplied from `ONEPIPELINE_RUNS_DIR`, which is what `just runs` and `just status`
# read. It takes one `--bind HOST:PORT` where the recipe took `--host` and
# `--port` separately. And it takes `--session`, which the recipe had no spelling for
# at all and which this supplies rather than asking an operator to.
set -euo pipefail

script_dir="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
# Who this server acts as, derived exactly as every `onepipeline` recipe derives it.
launcher_session_helper="$script_dir/launcher-session.sh"
if [ ! -f "$launcher_session_helper" ] || [ ! -r "$launcher_session_helper" ]; then
    echo "telemetry-server: required helper is not a readable regular file: $launcher_session_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
# shellcheck source=scripts/launcher-session.sh
if ! . "$launcher_session_helper"; then
    echo "telemetry-server: the helper at $launcher_session_helper is readable but could not be loaded; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
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
    # published CLI would be asked to bind. The port half is held to a real port and
    # not merely to five digits, the same way `--port` above is: `70000` is five
    # digits, and a file holding it would reach `--bind` as an address the CLI refuses
    # for a reason that names neither this script nor the file it came from.
    [[ "$default_address" =~ ^[^[:space:]:]+:[0-9]{1,5}$ ]] &&
        [ "${default_address##*:}" -le 65535 ] || {
        echo "telemetry-server: $address_file must hold one HOST:PORT with a port in 0-65535, not '${default_address}'" >&2
        exit 2
    }
    args+=(--bind "${host:-${default_address%%:*}}:${port:-${default_address##*:}}")
elif [ -n "$host" ] || [ -n "$port" ]; then
    echo "telemetry-server: --bind names the whole address; drop --host/--port or drop --bind" >&2
    exit 2
fi

# Named only when this host could resolve one, and never invented: a `--session` the
# engine cannot match against a run's recorded launcher owns exactly as little as no
# session at all, and inventing one would make an unattributable server look attributed.
# A caller who spelled `--session` itself owns the whole identity and keeps it, the way
# a caller who spelled `--bind` keeps the whole address — but it is held to the same
# shape the derived one is, through the same `launcher_session_is_usable`, because it
# becomes the same ownership credential: a value the ladder would refuse must not reach
# the API by being typed rather than derived.
session=()
named=0
expect_session=0
for argument in "${args[@]+"${args[@]}"}"; do
    caller_session=
    if [ "$expect_session" -eq 1 ]; then
        # The next word is an option, not a value: a `--session` at the end of the
        # caller's words is followed by the `--bind` this script appended above.
        case "$argument" in
            -*) break ;;
        esac
        caller_session=$argument
        expect_session=0
    else
        case "$argument" in
            --session) named=1; expect_session=1; continue ;;
            --session=*) named=1; caller_session=${argument#--session=} ;;
            *) continue ;;
        esac
    fi
    launcher_session_is_usable "$caller_session" || {
        echo "telemetry-server: --session must be 1-200 characters of letters, digits, dot, underscore or hyphen, not '${caller_session}'" >&2
        exit 2
    }
done
[ "$expect_session" -eq 0 ] || {
    echo "telemetry-server: --session needs a session id" >&2
    exit 2
}
# llmlint: ignore-block[boundary_inputs_validated] This value was already validated by `scripts/launcher-session.sh`, sourced above, and a malformed inherited one was reported there and deliberately kept. That helper's own directive explains why keeping it is right: it is the credential the parent launched its runs under, so leaving it off here would make this server unattributed while its parent is attributed, and split one run's ownership. Re-validating here and dropping it would reverse that decision for this one caller. It reaches the API as one argv element and is never interpreted by a shell. `tests/e2e/test_delegated_recipes_e2e.py` drives this path.
if [ "$named" -eq 0 ] && [ -n "${ONEPIPELINE_LAUNCHER_SESSION:-}" ]; then
    session=(--session "$ONEPIPELINE_LAUNCHER_SESSION")
fi
# llmlint: ignore-end[boundary_inputs_validated]

exec uv run onepipeline-api serve --runs-root "$runs_root" \
    "${session[@]+"${session[@]}"}" "${args[@]}"
