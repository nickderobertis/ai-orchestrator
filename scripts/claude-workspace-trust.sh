#!/usr/bin/env bash
# Backward-compatible source path; the implementation is alternate-Claude-only.
# shellcheck source=scripts/alternate-claude-workspace-trust.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/alternate-claude-workspace-trust.sh"
