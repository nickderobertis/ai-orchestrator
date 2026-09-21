#!/usr/bin/env bash
# llmlint: ignore-file[changed_behavior_has_e2e] subprocess tests drive the wrapper's validation and forwarding paths while replacing only its paid oneharness child.
# Adapt llmlint's forced read-only judge to the container-sandboxed worker host.
set -euo pipefail

if ! command -v oneharness >/dev/null 2>&1; then
    echo "llmlint oneharness wrapper: required 'oneharness' executable was not found; run 'just bootstrap' from the repository root to install it, then retry" >&2
    exit 127
fi

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(dirname -- "$script_dir")
llmlint_config="$repo_root/oneharness.llmlint.toml"
if [ ! -f "$llmlint_config" ] || [ ! -r "$llmlint_config" ]; then
    echo "llmlint oneharness wrapper: required config is not a readable regular file: $llmlint_config; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi

if (( $# == 0 )); then
    echo "llmlint oneharness wrapper: expected oneharness arguments; invoke through 'just lint-llm' or pass 'run ...'" >&2
    exit 2
fi
if (( $# == 1 )) && [[ $1 == --version ]]; then
    exec oneharness --version
fi
if [[ $1 != run ]]; then
    echo "llmlint oneharness wrapper: expected the 'run' subcommand; invoke through 'just lint-llm' or retry with 'run ...'" >&2
    exit 2
fi

# The Codex sandbox grant this wrapper used to append as a trailing
# `-- -c 'sandbox_permissions=[...]'` now lives in `[harness.codex] args` in
# oneharness.llmlint.toml. oneharness appends a trailing HARNESS_ARG to WHICHEVER
# harness fallback selects, and this tier's chain now reaches Claude Code, where
# `-c` is claude's `--continue` boolean: the permission string would be taken as a
# positional prompt and silently replace the lint prompt rather than fail. The
# per-harness entry cannot reach a non-Codex candidate at all, so the caller's
# arguments are forwarded verbatim from here.

# Every candidate in this tier's chain names a portable indirection its variant
# maps into the child (CODEX_HOME for the second Codex identity, CLAUDE_CONFIG_DIR
# for each alternate Claude subscription), and oneharness refuses to run while one
# is unset — so export them even on a host that authenticated none of them; an
# unauthenticated identity falls through rather than hard-failing. Resolved here,
# past the argument checks above, so a rejected invocation and the `--version`
# probe never touch the filesystem.
# shellcheck source=scripts/codex-alt-home.sh
# llmlint: ignore[boundary_inputs_validated, robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at.
. "$script_dir/codex-alt-home.sh"
ensure_codex_alt_home "llmlint oneharness wrapper" || exit $?
# shellcheck source=scripts/claude-alt-config-dir.sh
# llmlint: ignore[boundary_inputs_validated, robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at.
. "$script_dir/claude-alt-config-dir.sh"
resolve_claude_alt_config_dir "llmlint oneharness wrapper" || exit $?

if oneharness "$1" --config "$llmlint_config" "${@:2}"; then
    exit 0
else
    status=$?
    echo "llmlint oneharness wrapper: oneharness failed (exit $status); inspect the error above, run 'oneharness doctor', and retry" >&2
    exit "$status"
fi
