# shellcheck shell=bash
# llmlint: ignore-file[changed_behavior_has_e2e] the wrapper subprocess tests drive every branch, at the same seam scripts/oneharness-agent.sh declares.
# The ONE source of ONEPIPELINE_NODE_SCRATCH_DIR for a caller that is not a dispatch.
#
# Inside a dispatch this variable is the **engine's**, and it names that node's own
# scratch directory — absolute, writable, unique to the dispatch, and reclaimed with it.
# `oneharness.toml` and `oneharness.judge.toml` map it into `XDG_RUNTIME_DIR` for every
# candidate, so a dispatched turn's own runtime directory is inside the work it is doing
# rather than the manager session's, which on this host is mounted `noexec` — and a
# recipe runner that writes a shebang recipe's body under `XDG_RUNTIME_DIR` and execs it
# then fails every such recipe with a permission error.
#
# So this helper never overrides a value that is already set: in a dispatch the engine's
# answer is the right one and the only one that names *this* node's scratch. What it
# does is supply one where there is none, because `oneharness` refuses to start a variant
# whose `env_from` indirection is unset — the same reason scripts/codex-alt-home.sh
# creates a directory rather than leaving the candidate to fail. A wrapper spawning those
# configs by hand is the caller that needs it.
#
# Strict mode is established here rather than inherited from the sourcing caller, exactly
# as scripts/codex-alt-home.sh does: the `${TMPDIR:-/tmp}` derivation below must abort
# rather than fall through to a directory nothing created.
set -euo pipefail

#: Where a caller outside a dispatch gets one. Under the host's temporary directory
#: rather than the checkout, because this is per-invocation scratch a turn writes into
#: and not repository state — and because the checkout may be read-only in a
#: publication's own clone.
ORCHESTRATOR_NODE_SCRATCH_PREFIX=orchestrator-node-scratch

# Establish ONEPIPELINE_NODE_SCRATCH_DIR, creating a directory only when there is none
# to inherit — hence `ensure` rather than `resolve`, as with the Codex home. $1 names the
# calling wrapper so its diagnostics stay attributable.
ensure_node_scratch_dir() {
    local caller=${1:?ensure_node_scratch_dir: the name of the calling wrapper is required, so its diagnostics stay attributable; pass it as the first argument, then retry}
    local scratch
    if [ -n "${ONEPIPELINE_NODE_SCRATCH_DIR-}" ]; then
        # A dispatch's own, from the engine. Validated rather than trusted, because a
        # variant whose mapped path does not exist is not a refusal: `oneharness` falls
        # that candidate through as though the identity had no credentials, which walks
        # the whole chain and reports an auth problem nobody has.
        scratch=$ONEPIPELINE_NODE_SCRATCH_DIR
        case "$scratch" in
            /*) ;;
            *)
                echo "$caller: ONEPIPELINE_NODE_SCRATCH_DIR must be absolute, got '$scratch'; unset it to have one made, or correct it, then retry" >&2
                return 2
                ;;
        esac
        if [ ! -d "$scratch" ] || [ ! -r "$scratch" ] || [ ! -w "$scratch" ] || [ ! -x "$scratch" ]; then
            echo "$caller: ONEPIPELINE_NODE_SCRATCH_DIR '$scratch' is not an accessible writable directory, and a turn's runtime directory is mapped from it; fix its permissions, or unset it to have one made, then retry" >&2
            return 2
        fi
        export ONEPIPELINE_NODE_SCRATCH_DIR="$scratch"
        return 0
    fi
    # `mktemp` answers relative to its template, so a relative `TMPDIR` would yield a
    # relative scratch — which this helper refuses when it is *inherited*, above, and
    # would otherwise export itself. A runtime directory is read by a turn whose working
    # directory is not this one, so relative is wrong wherever it came from, and it is
    # refused before anything is made under it rather than made and then removed.
    case "${TMPDIR:-/tmp}" in
        /*) ;;
        *)
            echo "$caller: TMPDIR '${TMPDIR:-/tmp}' is relative, so a scratch directory made under it would be too, and a turn's runtime directory must be absolute; set TMPDIR to an absolute path, or set ONEPIPELINE_NODE_SCRATCH_DIR to a writable absolute directory, then retry" >&2
            return 2
            ;;
    esac
    # No option terminator: the template begins with `/` by the check above, so it cannot
    # read as an option, and the bare form is the one every mktemp shares.
    if ! scratch=$(mktemp -d "${TMPDIR:-/tmp}/$ORCHESTRATOR_NODE_SCRATCH_PREFIX-XXXXXXXX"); then
        echo "$caller: cannot create a scratch directory under ${TMPDIR:-/tmp} for this turn's runtime directory; make that directory writable, or set ONEPIPELINE_NODE_SCRATCH_DIR to a writable absolute directory, then retry" >&2
        return 2
    fi
    # It holds whatever a turn puts in its runtime directory, so it is this user's alone
    # for the same reason the alternate Codex home is.
    if ! chmod 700 "$scratch"; then
        echo "$caller: cannot restrict permissions on the scratch directory at $scratch; correct its ownership and retry" >&2
        return 2
    fi
    export ONEPIPELINE_NODE_SCRATCH_DIR="$scratch"
}
