#!/usr/bin/env bash
# Backward-compatible source path; the implementation is alternate-Claude-only.
set -euo pipefail
if ! TRUST_HELPER_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd); then
  echo "claude-workspace-trust: cannot resolve its script directory; restore directory access, then retry" >&2
  false
fi
# The compatibility shim resolves this fixed sibling path at runtime.
# shellcheck source=scripts/alternate-claude-workspace-trust.sh
source "$TRUST_HELPER_DIR/alternate-claude-workspace-trust.sh" \
  || { echo "claude-workspace-trust: cannot load the alternate-Claude trust helper; restore scripts/alternate-claude-workspace-trust.sh, then retry" >&2; false; }
