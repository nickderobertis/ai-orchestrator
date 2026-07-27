# shellcheck shell=bash
# The ONE source of the portable alternate-Claude config directory.
#
# Both `[harness.claude-code.variant.alternate]` blocks (oneharness.toml for the
# worker, oneharness.orchestrator.toml for the orchestrator) map
# ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR into CLAUDE_CONFIG_DIR through `env_from`, so
# oneharness refuses to run when that indirection is unset in the parent process.
# Every wrapper that reaches oneharness derives it here rather than each keeping
# its own copy of the $HOME rule; sourced by scripts/oneharness-agent.sh and
# scripts/oneharness-orchestrator.sh.

# Both wrappers already set these before sourcing, so this changes nothing today.
# It is here so the `${HOME:?}` guard below still aborts rather than deriving
# "/.claude-alt" if some later caller sources this module without them.
set -euo pipefail

# Derive, validate, and export ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR. $1 names the
# calling wrapper so its diagnostics stay attributable.
resolve_claude_alt_config_dir() {
    local caller=$1
    local alternate_config_dir
    if [ -n "${ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR-}" ]; then
        alternate_config_dir=$ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR
    else
        : "${HOME:?$caller: HOME is required to locate the alternate Claude config; export HOME or set ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR, then retry}"
        alternate_config_dir="$HOME/.claude-alt"
    fi
    case "$alternate_config_dir" in
        /*) ;;
        *)
            echo "$caller: alternate Claude config path must be absolute; set ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR to an absolute directory and retry" >&2
            return 2
            ;;
    esac
    if [ -e "$alternate_config_dir" ] &&
        { [ ! -d "$alternate_config_dir" ] ||
            [ ! -r "$alternate_config_dir" ] ||
            [ ! -x "$alternate_config_dir" ]; }; then
        echo "$caller: alternate Claude config path is not an accessible directory; create it or fix its permissions, or unset the override to use the default path and retry" >&2
        return 2
    fi
    export ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR="$alternate_config_dir"
}
