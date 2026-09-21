# shellcheck shell=bash
# The ONE definition of which resolvers establish a dispatch's environment — the `.env`
# credentials, every Claude identity's config directory, the alternate Codex home —
# sourced by scripts/onepipeline.sh at driver start and by scripts/dispatch-env-hook.sh
# before every node-scope dispatch (ai-orchestrator#1109). One table serves both so the
# two lists cannot drift; `tests/test_dispatch_env_hook.py` holds both callers to it.
#
# Strict mode is established here rather than inherited, as the resolvers below do: a
# launch whose environment could only be half established must abort.
set -euo pipefail

# The resolvers, in the order they run, as `<helper>:<function>`. Each helper is this
# file's neighbour and each function takes the calling launcher's name as its one
# argument. The order is the order scripts/onepipeline.sh has always run them in, and it
# is load-bearing in one place: the credentials come first because the Claude identities
# file is parsed by the same `read_env_file` the credentials helper defines.
DISPATCH_ENVIRONMENT_RESOLVERS=(
    "credentials-env.sh:export_host_credentials"
    "claude-alt-config-dir.sh:resolve_claude_alt_config_dir"
    "codex-alt-home.sh:ensure_codex_alt_home"
)

# Every variable the resolvers established on the last call, in resolver order, so a
# caller that has to hand them on — the hook — reads which rather than guessing. Empty
# until `export_dispatch_environment` has run to the end.
dispatch_environment_names=()

# Run every resolver above, in order, each attributing its diagnostics to $1, the name
# of the calling launcher. A resolver's own refusal is returned as it stands; a helper
# that cannot be sourced is a broken checkout, which the shell itself says on the line
# it fails the source at.
export_dispatch_environment() {
    # Named the way scripts/ask-manager-env.sh names its own required input: a caller
    # that forgot the label would otherwise abort on `1: unbound variable`, which says
    # nothing about which helper was called wrong.
    local caller=${1:?export_dispatch_environment: the name of the calling launcher is required, so its diagnostics stay attributable; pass it as the first argument, the way scripts/onepipeline.sh passes onepipeline, then retry}
    local here resolver helper function
    dispatch_environment_names=()
    # From this file's own location, because the helpers being loaded are this
    # checkout's — never from `$PWD`, which a launcher may be invoked from anywhere.
    # llmlint: ignore[changed_behavior_has_e2e] Reachable only when this helper's own directory stops being enterable between a caller sourcing it and calling this; no journey can produce that without racing the filesystem the test itself runs on.
    if ! here=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd); then
        echo "$caller: this checkout's dispatch environment could not be resolved; run the launch from a readable checkout, then retry" >&2
        return 2
    fi
    for resolver in "${DISPATCH_ENVIRONMENT_RESOLVERS[@]}"; do
        helper=${resolver%%:*}
        function=${resolver#*:}
        # shellcheck disable=SC1090  # the helper is named by the table above
        # llmlint: ignore[boundary_inputs_validated, robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at.
        . "$here/$helper"
        "$function" "$caller" || return $?
        # What each resolver established, read off the resolver's own record of it: the
        # credentials helper's parser leaves the names its file defines in
        # `env_file_names`, the identities helper declares its four indirections, and
        # the Codex helper exports exactly one.
        case $function in
            export_host_credentials)
                # shellcheck disable=SC2154  # left by read_env_file in credentials-env.sh
                dispatch_environment_names+=(${env_file_names[@]+"${env_file_names[@]}"})
                ;;
            resolve_claude_alt_config_dir)
                dispatch_environment_names+=("${CLAUDE_IDENTITY_CONFIG_VARIABLES[@]}")
                ;;
            ensure_codex_alt_home)
                dispatch_environment_names+=(ORCHESTRATOR_CODEX_ALT_HOME)
                ;;
        esac
    done
}
