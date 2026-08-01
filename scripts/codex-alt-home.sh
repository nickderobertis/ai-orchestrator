# shellcheck shell=bash
# The ONE source of the portable alternate-Codex home directory.
#
# Every `[harness.codex.variant.alternate]` block maps ORCHESTRATOR_CODEX_ALT_HOME
# into CODEX_HOME through `env_from`, so oneharness refuses to run — exit 2,
# "variant environment indirection ... is not set in the parent process" — when
# that indirection is unset in the parent. Codex is a fallback candidate in all
# four role chains, so every wrapper that reaches oneharness derives it here
# rather than each keeping its own copy of the $HOME rule; sourced by
# scripts/oneharness-agent.sh, scripts/oneharness-orchestrator.sh, and
# scripts/llmlint-oneharness.sh.
#
# Why this ensures the directory EXISTS rather than skipping an absent candidate:
# oneharness distinguishes the two "not set up yet" states, and only one of them
# degrades. A home that exists but holds no credentials is classified
# `failure_kind: "auth"` and falls through to the next harness in the chain; a home
# that does not exist at all is an unclassified hard failure that falls through to
# nothing. An empty directory is therefore the state that makes the committed
# chains safe on a host with only one Codex login, and it is exactly where
# `CODEX_HOME=... codex login` would write. `--exclude` cannot be used instead: it
# filters only `--all`, not an explicit `harnesses` chain.

# Every wrapper sets these before sourcing, so this changes nothing today. It is
# here so the `${HOME:?}` guard below still aborts rather than deriving
# "/.codex-alt" if some later caller sources this module without them.
set -euo pipefail

# Derive, validate, and export ORCHESTRATOR_CODEX_ALT_HOME, CREATING the directory
# (mode 700) when it is absent — hence `ensure` rather than `resolve`: this has an
# externally visible side effect on the filesystem, for the reason above. $1 names
# the calling wrapper so its diagnostics stay attributable.
ensure_codex_alt_home() {
    local caller=$1
    local alternate_home
    if [ -n "${ORCHESTRATOR_CODEX_ALT_HOME-}" ]; then
        alternate_home=$ORCHESTRATOR_CODEX_ALT_HOME
    else
        : "${HOME:?$caller: HOME is required to locate the alternate Codex home; export HOME or set ORCHESTRATOR_CODEX_ALT_HOME, then retry}"
        alternate_home="$HOME/.codex-alt"
    fi
    case "$alternate_home" in
        /*) ;;
        *)
            echo "$caller: alternate Codex home must be absolute; set ORCHESTRATOR_CODEX_ALT_HOME to an absolute directory and retry" >&2
            return 2
            ;;
    esac
    if [ -e "$alternate_home" ]; then
        if [ ! -d "$alternate_home" ] ||
            [ ! -r "$alternate_home" ] ||
            [ ! -x "$alternate_home" ]; then
            echo "$caller: alternate Codex home is not an accessible directory; fix its permissions, or unset the override to use the default path and retry" >&2
            return 2
        fi
    # `mkdir -m` applies the mode to the deepest directory only (SC2174), and this
    # will hold credentials, so set it explicitly once the path exists.
    elif ! mkdir -p "$alternate_home" 2>/dev/null ||
        ! chmod 700 "$alternate_home" 2>/dev/null; then
        # Only an unwritable parent reaches here, which breaks far more than this
        # candidate — so say what to set rather than silently dispatching into the
        # hard-failure state described above.
        echo "$caller: cannot create the alternate Codex home at $alternate_home; set ORCHESTRATOR_CODEX_ALT_HOME to a writable absolute directory and retry" >&2
        return 2
    fi
    export ORCHESTRATOR_CODEX_ALT_HOME="$alternate_home"
}
