# shellcheck shell=bash
# The ONE source of this checkout's host credentials, sourced by scripts/onepipeline.sh
# and scripts/plan.sh so the names in the gitignored `.env` reach every dispatch. Only
# the launch verbs source it: a read-only view dispatches nobody, so it has no
# environment to establish and nothing to refuse over.
#
# AGENTS.md carries the operator contract — which file this is on a host that also runs
# onetaskgraph, and which of the two wins. `tests/credential_dialect.py` states the
# syntax parsed below, once, and `tests/test_credential_dialect_drift.py` holds it to
# onetaskgraph's own parser. No value is echoed, here or in a refusal.
#
# Strict mode is established here rather than inherited from the sourcing caller,
# exactly as scripts/ask-manager-env.sh and scripts/codex-alt-home.sh do: the
# resolution below must abort rather than fall through to launching a run whose
# dispatches carry half a credentials file.
set -euo pipefail

export_host_credentials() {
    # Named the way scripts/ask-manager-env.sh names its own required input: a caller
    # that forgot the label would otherwise abort on `1: unbound variable`, which says
    # nothing about which helper was called wrong.
    local caller=${1:?export_host_credentials: the name of the calling launcher is required, so its diagnostics stay attributable; pass it as the first argument, the way scripts/onepipeline.sh passes onepipeline, then retry}
    local here credentials line key value quote line_number=0
    local -a lines=()
    # From this file's own location, because the file being loaded is this checkout's —
    # never from `$PWD`, which a launcher may be invoked from anywhere.
    # llmlint: ignore[changed_behavior_has_e2e] Reachable only when this helper's own directory stops being enterable between a caller sourcing it and calling this; no journey can produce that without racing the filesystem the test itself runs on.
    if ! here=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd); then
        echo "$caller: this checkout's credential file could not be resolved; run the launch from a readable checkout, then retry" >&2
        return 2
    fi
    credentials="$here/.env"
    # An absent file is a configured host: every name may already be exported, and this
    # repository runs on hosts with no credentials to give at all.
    [ -e "$credentials" ] || return 0
    # A file that is there and unreadable is the opposite — somebody meant to supply
    # these names, and the launch would otherwise run silently without them.
    if [ ! -f "$credentials" ] || [ ! -r "$credentials" ]; then
        echo "$caller: the credential file at $credentials is not a readable regular file; fix its type or permissions, then retry" >&2
        return 2
    fi
    # Read whole, and refused when that read does not complete. A `while read` loop ends
    # identically on end-of-file and on an I/O error part way down, so a truncated read
    # would launch a run whose dispatches carry some of the file's names and not others
    # — the failure that looks exactly like a name the operator forgot to write.
    # llmlint: ignore[changed_behavior_has_e2e] Reachable only when the file stops being openable between the check above and this read — unlinked, or its device failing — which no journey can produce without racing the filesystem the test itself runs on.
    if ! mapfile -t lines <"$credentials"; then
        echo "$caller: the credential file at $credentials could not be read to the end; check the device it is on, then retry" >&2
        return 2
    fi

    for line in ${lines[@]+"${lines[@]}"}; do
        line_number=$((line_number + 1))
        # Trimmed whole, which also absorbs the carriage return of a CRLF file. A name
        # somebody indented, or a value they aligned, is still what they meant.
        line=${line#"${line%%[![:space:]]*}"}
        line=${line%"${line##*[![:space:]]}"}
        # `#` is a comment only at the start of a line: a credential may contain one, and
        # guessing where a comment begins inside a value silently truncates a token.
        if [ -z "$line" ] || [ "${line:0:1}" = "#" ]; then
            continue
        fi
        line=${line#export }
        if [[ $line != *=* ]]; then
            echo "$caller: malformed credential line $line_number in $credentials; write it as KEY=VALUE or make it a '#' comment, then retry" >&2
            return 2
        fi
        key=${line%%=*}
        value=${line#*=}
        key=${key%"${key##*[![:space:]]}"}
        if [[ ! $key =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
            echo "$caller: malformed credential name on line $line_number in $credentials; use a shell environment name such as GH_PROJECTS_TOKEN, then retry" >&2
            return 2
        fi
        value=${value#"${value%%[![:space:]]*}"}
        value=${value%"${value##*[![:space:]]}"}
        # Matching surrounding quotes are the file's, not the credential's. Checked as a
        # pair, so a token that merely ends in a quote keeps it.
        quote=${value:0:1}
        if [ "${#value}" -ge 2 ] && { [ "$quote" = '"' ] || [ "$quote" = "'" ]; } &&
            [ "${value: -1}" = "$quote" ]; then
            value=${value:1:${#value}-2}
        fi
        # A name somebody exported deliberately for one command has to beat a file
        # written once and forgotten, or the file becomes impossible to override
        # without editing it.
        if [[ ! -v $key ]]; then
            export "$key=$value"
        fi
    done
}
