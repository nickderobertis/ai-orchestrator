#!/usr/bin/env bash
# Probe every configured identity with the same indirections used by dispatch.
#
# The probe is read-only, but it still has to resolve each variant's `env_from`
# source: oneharness refuses to start when a selected variant's indirection is
# unset, so a usage call that skipped these helpers would report every claude-code
# and codex variant as unavailable rather than as the capacity it has.
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=scripts/claude-alt-config-dir.sh
# llmlint: ignore[boundary_inputs_validated, robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at.
. "$script_dir/claude-alt-config-dir.sh"
# shellcheck source=scripts/codex-alt-home.sh
# llmlint: ignore[boundary_inputs_validated, robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at.
. "$script_dir/codex-alt-home.sh"
resolve_claude_alt_config_dir oneharness-usage
ensure_codex_alt_home oneharness-usage

exec "${ONEHARNESS_BIN:-oneharness}" usage "$@"
