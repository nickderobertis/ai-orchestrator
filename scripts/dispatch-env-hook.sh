#!/usr/bin/env bash
# The engine's dispatch-env hook, which scripts/onepipeline.sh names on every `start`.
# The engine runs it immediately before every node-scope dispatch — never for the
# observer graph — with the driver's environment, no arguments and nothing on stdin,
# and overlays the one document it prints on that dispatch's environment;
# onepipeline's `docs/contract.md` (**Dispatch-env hook**) is the contract. What this
# host does with it: re-run the resolvers the driver ran at start through
# scripts/dispatch-env.sh, the one definition of which, and print what they
# established as `{"version": 1, "env": {"NAME": "value", ...}}` — so an indirection
# a routing change adds while a run is live reaches its next dispatch
# (ai-orchestrator#1109). Stderr is the resolvers' own diagnostics, which never echo a
# value; the engine keeps it under the run and never records a printed value.
set -euo pipefail

fail() {
    echo "dispatch-env-hook: $1; $2" >&2
    exit 2
}

# The engine spawns the command itself with no arguments, so an argument means a caller
# that mistook this for a verb: refused rather than ignored, because a hook that printed
# a document in answer to `--help` would look like a hook that ran.
if [ "$#" -ne 0 ]; then
    fail "this hook takes no arguments and was given $#" \
        "name it whole as 'onepipeline start --dispatch-env-hook $0', which scripts/onepipeline.sh does for every launch"
fi

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on.
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P) || fail "this hook could not resolve the checkout it belongs to" \
    "name it by an absolute path in a readable checkout"

# shellcheck source=scripts/dispatch-env.sh
# llmlint: ignore[boundary_inputs_validated, robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at.
. "$script_dir/dispatch-env.sh"
export_dispatch_environment dispatch-env-hook || exit $?

# Sets `encoded` to $1 as a JSON string, escaped with builtins alone, byte-wise under
# the C locale so a value in any encoding passes through as it was written. The same
# escaping scripts/ask-manager.sh gives a question, for the same reason: nothing else
# this hook may depend on is guaranteed to be on a dispatch's PATH.
store_json_string_in_encoded() {
    local LC_ALL=C text=$1 code hex control
    text=${text//\\/\\\\}
    text=${text//\"/\\\"}
    text=${text//$'\n'/\\n}
    text=${text//$'\r'/\\r}
    text=${text//$'\t'/\\t}
    for ((code = 1; code < 32; code++)); do
        printf -v hex '%02x' "$code"
        printf -v control '%b' "\\x$hex"
        text=${text//"$control"/\\u00$hex}
    done
    printf -v encoded '"%s"' "$text"
}

# Whether a value is text the document can carry: well-formed UTF-8, which is what JSON
# is. The same byte-wise builtin match scripts/ask-manager.sh gives a question — one
# alternative per well-formed sequence, so an overlong form, a surrogate and a truncated
# sequence all fail — because the engine reads a malformed document as a hook that
# failed, naming nothing about which value was the trouble.
well_formed_utf8() {
    local LC_ALL=C tail pattern
    printf -v tail '[%b-%b]' '\x80' '\xbf'
    printf -v pattern '^([%b-%b]|[%b-%b]%s|%b[%b-%b]%s|[%b-%b%b%b]%s%s|%b[%b-%b]%s|%b[%b-%b]%s%s|[%b-%b]%s%s%s|%b[%b-%b]%s%s)*$' \
        '\x01' '\x7f' \
        '\xc2' '\xdf' "$tail" \
        '\xe0' '\xa0' '\xbf' "$tail" \
        '\xe1' '\xec' '\xee' '\xef' "$tail" "$tail" \
        '\xed' '\x80' '\x9f' "$tail" \
        '\xf0' '\x90' '\xbf' "$tail" "$tail" \
        '\xf1' '\xf3' "$tail" "$tail" "$tail" \
        '\xf4' '\x80' '\x8f' "$tail" "$tail"
    # llmlint: ignore[robust_shell] A `[[ =~ ]]` right-hand side must stay unquoted; quoting makes bash match the pattern literally.
    [[ $1 =~ $pattern ]]
}

# One member per variable the resolvers established, in the order they established
# them and each once, because a JSON object naming a member twice is a malformed
# document to the engine and `.env` may spell a name twice or name one the identity
# helpers also export. A name the resolvers recorded and then left unset — a credential
# the file names with no value — is printed as the empty string it is, because the
# document says what the driver-start arm exported and that arm exported it empty.
members=()
printed=" "
for name in ${dispatch_environment_names[@]+"${dispatch_environment_names[@]}"}; do
    case $printed in
        *" $name "*) continue ;;
    esac
    printed+="$name "
    # Refused by the variable's name and never its bytes: the value is a credential or a
    # path an operator wrote where they can read it themselves.
    if ! well_formed_utf8 "${!name-}"; then
        fail "the value of $name is not well-formed UTF-8, which the hook's document cannot carry" \
            "correct it where it is set — this checkout's .env, the identities file or the environment — then retry"
    fi
    store_json_string_in_encoded "${!name-}"
    members+=("\"$name\": $encoded")
done
# llmlint: ignore[contracts_have_one_source_or_a_drift_gate] The source of this document's shape is the engine that reads it, onepipeline's docs/contract.md (**Dispatch-env hook**), and the installed engine does not carry it yet: there is no released copy to reconcile against until that adoption, whose journeys in tests/e2e/test_orchestrate_launch_e2e.py drive the real reader.
document='{"version": 1, "env": {'
separator=
for member in ${members[@]+"${members[@]}"}; do
    document+="$separator$member"
    separator=', '
done
document+='}}'
printf '%s\n' "$document"
