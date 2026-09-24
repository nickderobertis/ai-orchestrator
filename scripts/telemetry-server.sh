#!/usr/bin/env bash
# Serve the DAG Observatory from the published `onepipeline-api`.
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
# Every flag here is the published CLI's own, forwarded untouched. What this adds is
# three defaults a caller who named none would otherwise have to type, each from the
# one source this host keeps it in: the runs root every other view reads, the address
# `config/read-api.address` names, and the acting session above. `just dag-ui` is this
# same command with the published `--ui`, which serves the browser view built into the
# binary at every path the API does not own.
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
# One source for the address this API answers on, read on every path that needs a
# default — including the one that names neither the flag nor a value. Letting the
# published CLI's own default stand there would leave the address restated in a second
# place: they agree today, and an edit to that file alone would silently leave the
# server on the old port with every reader still pointed at the new.
address_file="$(dirname -- "$script_dir")/config/read-api.address"

# One pass over the caller's own words, deciding which of the three defaults are still
# this script's to supply and holding a typed `--session` to the shape a derived one is
# held to. A caller who spells a flag owns it whole: two `--bind` values would leave
# which one binds up to the CLI, and two `--session` values the same for which identity
# acts — neither readable off the command line afterwards.
#
# A flag's value is skipped only when the next word is not itself an option. Skipping
# whatever follows would let `--runs-root --session <value>` carry a session past the
# check below and reach the CLI beside the derived one — an identity nothing validated,
# spelled twice.
session_shape="1-200 characters of letters, digits, dot, underscore or hyphen"
refuse_session() {
    echo "telemetry-server: --session must be ${session_shape}, not '${1}'" >&2
    exit 2
}

runs_root_named=0
bound=0
session_named=0
words=("$@")
count=$#
at=0
while [ "$at" -lt "$count" ]; do
    argument=${words[at]}
    at=$((at + 1))
    value=
    has_value=0
    if [ "$at" -lt "$count" ]; then
        case "${words[at]}" in
            -*) ;;
            *)
                value=${words[at]}
                has_value=1
                ;;
        esac
    fi
    case "$argument" in
        --runs-root)
            runs_root_named=1
            [ "$has_value" -eq 0 ] || at=$((at + 1))
            ;;
        --runs-root=*) runs_root_named=1 ;;
        --bind)
            bound=1
            [ "$has_value" -eq 0 ] || at=$((at + 1))
            ;;
        --bind=*) bound=1 ;;
        --session)
            session_named=1
            # Nothing to act as: `--session --bind …` and a trailing `--session` are both
            # a flag with no identity given to it, and saying so names what to fix.
            [ "$has_value" -eq 1 ] || {
                echo "telemetry-server: --session needs a session id" >&2
                exit 2
            }
            # llmlint: ignore[boundary_inputs_validated] This is the validation, performed by `scripts/launcher-session.sh`'s own predicate — the one definition of the shape an acting session may take — rather than by a second check here that could disagree with it.
            launcher_session_is_usable "$value" || refuse_session "$value"
            at=$((at + 1))
            ;;
        --session=*)
            session_named=1
            # llmlint: ignore[boundary_inputs_validated] Same predicate, same reason as the separated spelling above: `scripts/launcher-session.sh` owns the shape.
            launcher_session_is_usable "${argument#--session=}" ||
                refuse_session "${argument#--session=}"
            ;;
    esac
done

supplied=()
# llmlint: ignore[boundary_inputs_validated] A default from this host's own `ONEPIPELINE_RUNS_DIR`, not a caller's input; `onepipeline-api serve` refuses a runs root it cannot read, naming it.
[ "$runs_root_named" -eq 1 ] || supplied+=(--runs-root "${ONEPIPELINE_RUNS_DIR:-runs}")
# llmlint: ignore-block[boundary_inputs_validated] `scripts/launcher-session.sh` already validated this session and deliberately keeps a malformed inherited one, so the server acts as its parent does; `tests/e2e/test_delegated_recipes_e2e.py` drives this path.
if [ "$session_named" -eq 0 ] && [ -n "${ONEPIPELINE_LAUNCHER_SESSION:-}" ]; then
    supplied+=(--session "$ONEPIPELINE_LAUNCHER_SESSION")
fi
# llmlint: ignore-end[boundary_inputs_validated]
if [ "$bound" -eq 0 ]; then
    default_address="$(tr -d '[:space:]' <"$address_file")" || {
        echo "telemetry-server: could not read $address_file; restore it and retry" >&2
        exit 2
    }
    # Checked rather than assumed: split on a colon that is not there, `--bind
    # 8765:8765` is what the published CLI would be asked to bind. The port half is
    # held to a real port and not merely to five digits, because `70000` is five
    # digits and a file holding it would reach `--bind` as an address the CLI refuses
    # for a reason that names neither this script nor the file it came from.
    if ! [[ "$default_address" =~ ^[^[:space:]:]+:[0-9]{1,5}$ ]] ||
        [ "${default_address##*:}" -gt 65535 ]; then
        echo "telemetry-server: $address_file must hold one HOST:PORT with a port in 0-65535, not '${default_address}'" >&2
        exit 2
    fi
    supplied+=(--bind "$default_address")
fi

# llmlint: ignore[boundary_inputs_validated] Forwarded untouched by design: `onepipeline-api serve` owns these flags and refuses a value it will not take, naming it.
exec uv run onepipeline-api serve "${supplied[@]+"${supplied[@]}"}" "$@"
