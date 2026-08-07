#!/usr/bin/env bash
# Probe every configured identity with the same indirections used by dispatch.
#
# The probe is read-only, but it still has to resolve each variant's `env_from`
# source: oneharness refuses to start when a selected variant's indirection is
# unset, so a usage call that skipped these helpers would report every claude-code
# and codex variant as unavailable rather than as the capacity it has.
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
alt_config_helper="$script_dir/claude-alt-config-dir.sh"
if [ ! -f "$alt_config_helper" ] || [ ! -r "$alt_config_helper" ]; then
    echo "oneharness-usage: required helper is not a readable regular file: $alt_config_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
# shellcheck source=scripts/claude-alt-config-dir.sh
. "$alt_config_helper"
codex_home_helper="$script_dir/codex-alt-home.sh"
if [ ! -f "$codex_home_helper" ] || [ ! -r "$codex_home_helper" ]; then
    echo "oneharness-usage: required helper is not a readable regular file: $codex_home_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
# shellcheck source=scripts/codex-alt-home.sh
. "$codex_home_helper"
resolve_claude_alt_config_dir oneharness-usage
ensure_codex_alt_home oneharness-usage

exec "${ONEHARNESS_BIN:-oneharness}" usage "$@"
