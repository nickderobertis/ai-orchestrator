#!/usr/bin/env bash
# Probe every configured identity with the same indirections used by dispatch.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=/dev/null
source "$repo_root/scripts/claude-alt-config-dir.sh"
# shellcheck source=/dev/null
source "$repo_root/scripts/codex-alt-home.sh"
resolve_claude_alt_config_dir oneharness-usage
ensure_codex_alt_home oneharness-usage

exec "${ONEHARNESS_BIN:-oneharness}" usage "$@"
