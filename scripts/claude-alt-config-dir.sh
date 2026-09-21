# shellcheck shell=bash
# The ONE source of every Claude identity's config directory.
#
# Every config maps one of these four indirections into CLAUDE_CONFIG_DIR through
# `env_from` for each claude-code variant, so oneharness refuses to run when any of them
# is unset in the parent process:
#
#   ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR             $HOME/.claude-alt             claude-code:alternate
#   ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR            $HOME/.claude-alt2            claude-code:alternate2
#   ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR  $HOME/.claude-primary-backup  claude-code:primary-backup
#   ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR         $HOME/.claude                 claude-code:primary
#
# Per variable, a non-empty environment value wins, then the host's identities file
# (docs/host-setup.md, step 4), then the default.
#
# Unlike scripts/codex-alt-home.sh, this helper deliberately has NO filesystem
# side effect: claude-code classifies an absent CLAUDE_CONFIG_DIR exactly as it
# classifies an empty one — `failure_kind: "auth"`, which falls through to the
# next candidate — and creates the directory itself when it runs. See
# [The second alternate Claude subscription](docs/onejudge-integration.md#the-second-alternate-claude-subscription).

# Strict mode. The wrappers already enable it before sourcing, so this changes nothing for
# them; it is here so a later caller that sources this module without strict mode still
# refuses rather than deriving a path from an unset HOME.
set -euo pipefail

# Every Claude identity's indirection, in the order the chains name them relative to
# each other. The trust marker walks this list, so an identity added here is marked too.
CLAUDE_IDENTITY_CONFIG_VARIABLES=(
    ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR
    ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR
    ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR
    ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR
)

# What the identities file last read defined, so resolving one identity before any file
# was read consults nothing rather than an unset array.
claude_identities_file=""
claude_identities_file_names=()
claude_identities_file_values=()

# Where the parser lives, taken while this file is being sourced: a relative
# BASH_SOURCE would otherwise resolve against whatever directory a caller has moved to
# by the time it asks for the identities file.
_claude_identity_helper_dir=$(dirname -- "${BASH_SOURCE[0]}")
case $_claude_identity_helper_dir in
    /*) ;;
    *) _claude_identity_helper_dir="$PWD/$_claude_identity_helper_dir" ;;
esac

# The $HOME-relative default and the diagnostic label of one indirection. Refuses a
# name that is not one of the four, so a caller cannot resolve a directory nobody routes.
_claude_identity_default() {
    case $1 in
        ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR) printf '%s\n' ".claude-alt" "alternate Claude config" ;;
        ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR) printf '%s\n' ".claude-alt2" "second alternate Claude config" ;;
        ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR) printf '%s\n' ".claude-primary-backup" "primary-backup Claude config" ;;
        ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR) printf '%s\n' ".claude" "primary Claude config" ;;
        *) return 1 ;;
    esac
}

# The host's identities file, printed when a location can be derived at all. With
# neither an absolute XDG_CONFIG_HOME nor an absolute HOME there is no such path, and
# every identity then needs an override from the environment anyway.
claude_identities_file_path() {
    if [[ ${XDG_CONFIG_HOME-} == /* ]]; then
        printf '%s\n' "$XDG_CONFIG_HOME/ai-orchestrator/claude-identities.env"
    elif [[ ${HOME-} == /* ]]; then
        printf '%s\n' "$HOME/.config/ai-orchestrator/claude-identities.env"
    else
        return 1
    fi
}

# Read the host's identities file into `claude_identities_file_names` and
# `claude_identities_file_values`. An absent file leaves both empty; a refused one leaves
# both empty and returns non-zero, having said why. $1 names the calling wrapper.
_load_claude_identities_file() {
    local caller=$1
    claude_identities_file=""
    claude_identities_file_names=()
    claude_identities_file_values=()
    claude_identities_file=$(claude_identities_file_path) || return 0
    [ -e "$claude_identities_file" ] || return 0
    # Loaded only when there is a file to parse, so a host with none depends on nothing
    # beyond this file; the file is read by that parser and no other way. Loading it a
    # second time costs a re-read of one sibling and defines the same functions, which is
    # cheaper than a condition asking whether some caller loaded it already.
    # shellcheck source=scripts/credentials-env.sh
    # llmlint: ignore[boundary_inputs_validated, robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at.
    . "$_claude_identity_helper_dir/credentials-env.sh"
    read_env_file "$caller" "$claude_identities_file" "Claude identities" \
        "${CLAUDE_IDENTITY_CONFIG_VARIABLES[@]}" || return $?
    claude_identities_file_names=(${env_file_names[@]+"${env_file_names[@]}"})
    claude_identities_file_values=(${env_file_values[@]+"${env_file_values[@]}"})
}

# Derive, validate, and export one identity's config directory from the environment,
# then the identities file `_load_claude_identities_file` last read, then the default.
# $1 names the calling wrapper so its diagnostics stay attributable, $2 the indirection.
resolve_claude_identity_config_dir() {
    local caller=$1 variable=$2 default_leaf label config_dir="" index fix in_file=false
    local -a described=()
    if ! mapfile -t described < <(_claude_identity_default "$variable") || (( ${#described[@]} != 2 )); then
        echo "$caller: $variable is not a Claude identity's config indirection; name one of ${CLAUDE_IDENTITY_CONFIG_VARIABLES[*]}, then retry" >&2
        return 2
    fi
    default_leaf=${described[0]}
    label=${described[1]}
    fix="set $variable to an absolute directory"
    if [ -n "${!variable-}" ]; then
        config_dir=${!variable}
    else
        for index in "${!claude_identities_file_names[@]}"; do
            if [ "${claude_identities_file_names[$index]}" = "$variable" ]; then
                config_dir=${claude_identities_file_values[$index]}
                in_file=true
            fi
        done
        # A name the file writes is the file's value even when empty, so `NAME=` is
        # refused by the absolute-path rule below rather than quietly meaning the default.
        if [ "$in_file" = true ]; then
            fix="write $variable in $claude_identities_file as an absolute directory (the file expands no variables), or set it in the environment"
        elif [ -n "${HOME-}" ]; then
            config_dir="$HOME/$default_leaf"
        else
            echo "$caller: HOME is required to locate the $label; export HOME or set $variable, then retry" >&2
            return 2
        fi
    fi
    # Neither the path nor the value is echoed: an operator's override is theirs to read
    # where they wrote it, and a diagnostic names the variable that decides it.
    case "$config_dir" in
        /*) ;;
        *)
            echo "$caller: $label path must be absolute; $fix and retry" >&2
            return 2
            ;;
    esac
    if [ -e "$config_dir" ] &&
        { [ ! -d "$config_dir" ] ||
            [ ! -r "$config_dir" ] ||
            [ ! -x "$config_dir" ]; }; then
        echo "$caller: $label path named by $variable is not an accessible directory; create it or fix its permissions, or unset the override to use the default path and retry" >&2
        return 2
    fi
    export "$variable=$config_dir"
}

# Derive, validate, and export every Claude identity's config directory. $1 names the
# calling wrapper so its diagnostics stay attributable. The name is historical: it
# resolved the two alternates once, and every caller still calls it by this name.
# llmlint: ignore[names_match_behavior] The name is historical and kept by contract C2 of this run so none of its callers changes; the comment above says it now resolves all four identities.
resolve_claude_alt_config_dir() {
    local caller=$1 variable
    _load_claude_identities_file "$caller" || return $?
    for variable in "${CLAUDE_IDENTITY_CONFIG_VARIABLES[@]}"; do
        resolve_claude_identity_config_dir "$caller" "$variable" || return $?
    done
}
