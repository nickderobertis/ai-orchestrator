# shellcheck shell=bash
# The ONE source of BOTH portable alternate-Claude config directories.
#
# Every config's `[harness.claude-code.variant.alternate]` and
# `[harness.claude-code.variant.alternate2]` block maps
# ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR / ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR into
# CLAUDE_CONFIG_DIR through `env_from`, so oneharness refuses to run when either
# indirection is unset in the parent process. Every wrapper that reaches
# oneharness derives them here rather than each keeping its own copy of the $HOME
# rule; sourced by scripts/oneharness-agent.sh, scripts/oneharness-orchestrator.sh,
# and scripts/llmlint-oneharness.sh — every role's chain now names both.
#
# Unlike scripts/codex-alt-home.sh, this helper deliberately has NO filesystem
# side effect: claude-code classifies an absent CLAUDE_CONFIG_DIR exactly as it
# classifies an empty one — `failure_kind: "auth"`, which falls through to the
# next candidate — and creates the directory itself when it runs. See
# [The second alternate Claude subscription](docs/onejudge-integration.md#the-second-alternate-claude-subscription).

# The wrappers already set these before sourcing, so this changes nothing today.
# It is here so the `${HOME:?}` guard below still aborts rather than deriving
# "/.claude-alt" if some later caller sources this module without them.
set -euo pipefail

# Derive, validate, and export one alternate-Claude config directory. $1 names the
# calling wrapper so its diagnostics stay attributable, $2 is the human label used
# in those diagnostics, $3 the variable that both overrides and receives the path,
# and $4 the $HOME-relative default.
_resolve_one_claude_config_dir() {
    local caller=$1 label=$2 variable=$3 default_leaf=$4
    local config_dir
    if [ -n "${!variable-}" ]; then
        config_dir=${!variable}
    else
        : "${HOME:?$caller: HOME is required to locate the $label; export HOME or set $variable, then retry}"
        config_dir="$HOME/$default_leaf"
    fi
    case "$config_dir" in
        /*) ;;
        *)
            echo "$caller: $label path must be absolute; set $variable to an absolute directory and retry" >&2
            return 2
            ;;
    esac
    if [ -e "$config_dir" ] &&
        { [ ! -d "$config_dir" ] ||
            [ ! -r "$config_dir" ] ||
            [ ! -x "$config_dir" ]; }; then
        echo "$caller: $label path is not an accessible directory; create it or fix its permissions, or unset the override to use the default path and retry" >&2
        return 2
    fi
    export "$variable=$config_dir"
}

# Derive, validate, and export both alternate-Claude config directories. $1 names
# the calling wrapper so its diagnostics stay attributable.
resolve_claude_alt_config_dir() {
    local caller=$1
    _resolve_one_claude_config_dir \
        "$caller" "alternate Claude config" ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR .claude-alt || return $?
    _resolve_one_claude_config_dir \
        "$caller" "second alternate Claude config" ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR .claude-alt2
}
