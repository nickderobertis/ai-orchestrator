# shellcheck shell=bash
# llmlint: ignore-file[changed_behavior_has_e2e] the wrapper subprocess tests drive every branch, at the same seam scripts/oneharness-agent.sh declares.
# The ONE source of the portable alternate-Codex home directory, sourced by
# scripts/oneharness-agent.sh, scripts/oneharness-orchestrator.sh, and
# scripts/llmlint-oneharness.sh.
#
# Every wrapper must call this, because oneharness refuses to start whenever the
# indirection a selected variant names is unset. For why it creates the directory,
# see [The second Codex identity](docs/onejudge-integration.md#the-second-codex-identity).

# This helper establishes strict mode itself rather than inheriting whatever the
# sourcing caller happened to set: the `${HOME:?}` guard below must abort the
# process, not fall through to deriving "/.codex-alt", even if some later caller
# sources this module without `set -u`.
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
        # `-w` belongs with the read checks: codex initializes state in this
        # directory (auth tokens, logs, its own tmp), so an existing but
        # unwritable home fails deep inside the child rather than here.
        if [ ! -d "$alternate_home" ] ||
            [ ! -r "$alternate_home" ] ||
            [ ! -w "$alternate_home" ] ||
            [ ! -x "$alternate_home" ]; then
            echo "$caller: alternate Codex home is not an accessible writable directory; fix its permissions, or unset the override to use the default path and retry" >&2
            return 2
        fi
    # `mkdir -m` applies the mode to the deepest directory only (SC2174), and this
    # will hold credentials, so set it explicitly once the path exists.
    # mkdir and chmod keep their own stderr: the kernel's reason (which parent, which
    # permission) is the diagnostic, and these two failures are distinct — a created
    # directory left world-readable is not a creation failure.
    elif ! mkdir -p "$alternate_home"; then
        echo "$caller: cannot create the alternate Codex home at $alternate_home; set ORCHESTRATOR_CODEX_ALT_HOME to a writable absolute directory and retry" >&2
        return 2
    elif ! chmod 700 "$alternate_home"; then
        echo "$caller: cannot restrict permissions on the alternate Codex home at $alternate_home; it holds credentials, so correct its ownership and retry" >&2
        return 2
    fi
    export ORCHESTRATOR_CODEX_ALT_HOME="$alternate_home"
}
