#!/usr/bin/env bash
# Backward-compatible source path; the implementation is alternate-Claude-only.
set -euo pipefail
# shellcheck source=scripts/alternate-claude-workspace-trust.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/alternate-claude-workspace-trust.sh" \
  || { echo "claude-workspace-trust: cannot load the alternate-Claude trust helper; restore scripts/alternate-claude-workspace-trust.sh, then retry" >&2; false; }
