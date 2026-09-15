# shellcheck shell=bash
# The ONE source of this checkout's host credentials, sourced by scripts/onepipeline.sh
# and scripts/plan.sh so the names in the gitignored `.env` reach every dispatch. Only
# the launch verbs source it: a read-only view dispatches nobody, so it has no
# environment to establish and nothing to refuse over.
#
# It is also the ONE parser of that file's dialect. `read_env_file` takes a path and the
# names it admits, which is how scripts/claude-alt-config-dir.sh reads the host's Claude
# identities file without a second copy of these rules.
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

# Parse one file in the credentials dialect. $1 names the calling script so its
# diagnostics stay attributable, $2 is the file's absolute path, $3 the noun its
# diagnostics call it by ("credential"), and every further argument a name the file
# admits; with none, every name is admitted. The names and values it defines are left, in
# file order, in the parallel arrays `env_file_names` and `env_file_values` — nothing is
# exported, because whether the environment or the file wins is each caller's rule.
read_env_file() {
    local caller=${1:?read_env_file: the name of the calling script is required, so its diagnostics stay attributable; pass it as the first argument, then retry}
    local path=${2:?read_env_file: the path of the file to read is required as the second argument}
    local noun=${3:?read_env_file: the noun the file is called by in diagnostics is required as the third argument}
    shift 3
    local -a admitted=("$@") lines=()
    local line key value quote name line_number=0 known
    env_file_names=()
    env_file_values=()
    # Relative would resolve against whichever directory the caller happens to be in.
    case $path in
        /*) ;;
        *)
            echo "$caller: the $noun file path handed to read_env_file must be absolute; pass it resolved from the caller's own location, then retry" >&2
            return 2
            ;;
    esac
    # An absent file is a configured host: every name may already be exported, and this
    # repository runs on hosts with no such file to give at all.
    [ -e "$path" ] || return 0
    # A file that is there and unreadable is the opposite — somebody meant to supply
    # these names, and the caller would otherwise run silently without them.
    if [ ! -f "$path" ] || [ ! -r "$path" ]; then
        echo "$caller: the $noun file at $path is not a readable regular file; fix its type or permissions, then retry" >&2
        return 2
    fi
    # Read whole, and refused when that read does not complete. A `while read` loop ends
    # identically on end-of-file and on an I/O error part way down, so a truncated read
    # would launch a run whose dispatches carry some of the file's names and not others
    # — the failure that looks exactly like a name the operator forgot to write.
    # llmlint: ignore[changed_behavior_has_e2e] Reachable only when the file stops being openable between the check above and this read — unlinked, or its device failing — which no journey can produce without racing the filesystem the test itself runs on.
    if ! mapfile -t lines <"$path"; then
        echo "$caller: the $noun file at $path could not be read to the end; check the device it is on, then retry" >&2
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
            echo "$caller: malformed $noun line $line_number in $path; write it as KEY=VALUE or make it a '#' comment, then retry" >&2
            return 2
        fi
        key=${line%%=*}
        value=${line#*=}
        key=${key%"${key##*[![:space:]]}"}
        if [[ ! $key =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
            echo "$caller: malformed $noun name on line $line_number in $path; use a shell environment name such as GH_PROJECTS_TOKEN, then retry" >&2
            return 2
        fi
        # A name the caller does not admit is refused where it is written rather than
        # skipped: a misspelt name that silently did nothing reads, far away, exactly like
        # a default the operator never meant to keep. The name is printed; its value never.
        if (( ${#admitted[@]} > 0 )); then
            known=false
            for name in "${admitted[@]}"; do
                [ "$name" = "$key" ] && known=true
            done
            if [[ $known != true ]]; then
                echo "$caller: line $line_number in $path names $key, which the $noun file does not admit; it admits only ${admitted[*]}, so remove or correct that line, then retry" >&2
                return 2
            fi
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
        env_file_names+=("$key")
        env_file_values+=("$value")
    done
}

export_host_credentials() {
    # Named the way scripts/ask-manager-env.sh names its own required input: a caller
    # that forgot the label would otherwise abort on `1: unbound variable`, which says
    # nothing about which helper was called wrong.
    local caller=${1:?export_host_credentials: the name of the calling launcher is required, so its diagnostics stay attributable; pass it as the first argument, the way scripts/onepipeline.sh passes onepipeline, then retry}
    local here index key
    # Which names the file defines, whether or not each was exported, for a caller that
    # has to place a refusal: `scripts/plan-store.sh` tells a name the file lacks from one
    # it defines without a value. Set here, by the one parser of that file, rather than
    # matched again by a second one. Empty until the file has been read whole.
    # shellcheck disable=SC2034  # read by the caller that sourced this helper
    credential_file_names=""
    # From this file's own location, because the file being loaded is this checkout's —
    # never from `$PWD`, which a launcher may be invoked from anywhere.
    # llmlint: ignore[changed_behavior_has_e2e] Reachable only when this helper's own directory stops being enterable between a caller sourcing it and calling this; no journey can produce that without racing the filesystem the test itself runs on.
    if ! here=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd); then
        echo "$caller: this checkout's credential file could not be resolved; run the launch from a readable checkout, then retry" >&2
        return 2
    fi
    read_env_file "$caller" "$here/.env" credential || return $?

    for index in "${!env_file_names[@]}"; do
        key=${env_file_names[$index]}
        # A name somebody exported deliberately for one command has to beat a file
        # written once and forgotten, or the file becomes impossible to override
        # without editing it.
        if [[ ! -v $key ]]; then
            export "$key=${env_file_values[$index]}"
        fi
    done
    # shellcheck disable=SC2034  # read by the caller that sourced this helper
    credential_file_names="${env_file_names[*]+"${env_file_names[*]}"}"
}
