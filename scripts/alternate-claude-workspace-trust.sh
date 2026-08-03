#!/usr/bin/env bash
# Shared, sourceable implementation for marking alternate-Claude workspaces trusted.
set -euo pipefail

mark_alternate_claude_trust() (
  if (( $# < 2 )); then
    echo "alternate-claude-workspace-trust: configuration path and at least one workspace path are required" >&2
    return 2
  fi
  local config_path=$1
  shift
  [ -f "$config_path" ] || return 0
  if ! command -v jq >/dev/null 2>&1; then
    echo "alternate-claude-workspace-trust: jq is unavailable; install jq or add it to PATH, then retry" >&2
    return 1
  fi
  if ! command -v flock >/dev/null 2>&1; then
    echo "alternate-claude-workspace-trust: flock is unavailable; install util-linux or add flock to PATH, then retry" >&2
    return 1
  fi
  local updated root trust_filter
  cleanup_trust_files() {
    if ! rm -f "$@"; then
      echo "alternate-claude-workspace-trust: cannot remove temporary files for $config_path; fix directory permissions, then retry" >&2
      return 1
    fi
  }
  if ! exec 9>"${config_path}.trust.lock"; then
    echo "alternate-claude-workspace-trust: cannot open the lock beside $config_path; fix directory permissions, then retry" >&2
    return 1
  fi
  if ! flock 9; then
    echo "alternate-claude-workspace-trust: cannot lock $config_path; verify flock and filesystem locking, then retry" >&2
    return 1
  fi
  [ -f "$config_path" ] || return 0
  if ! updated=$(mktemp "${config_path}.trust.XXXXXX"); then
    echo "alternate-claude-workspace-trust: cannot create a temporary file beside $config_path; fix directory permissions, then retry" >&2
    return 1
  fi
  if ! jq '.' "$config_path" >"$updated"; then
    echo "alternate-claude-workspace-trust: $config_path is not valid JSON; repair or replace it, then retry" >&2
    cleanup_trust_files "$updated" || return 1
    return 1
  fi
  if ! jq -e 'type == "object" and ((.projects // {}) | type == "object")' "$updated" \
    >/dev/null; then
    echo "alternate-claude-workspace-trust: $config_path must contain a JSON object with an optional projects object; repair or replace it, then retry" >&2
    cleanup_trust_files "$updated" || return 1
    return 1
  fi
  for root in "$@"; do
    if [[ -z $root || $root != /* ]]; then
      echo "alternate-claude-workspace-trust: workspace path must be a nonempty absolute path: '$root'" >&2
      cleanup_trust_files "$updated" || return 1
      return 2
    fi
  done
  # llmlint: ignore[contracts_have_one_source_or_a_drift_gate] hasTrustDialogAccepted mirrors an external vendor configuration format with no importable source, so a drift gate is not constructible here.
  trust_filter=".projects = (.projects // {}) | .projects[\$root] = ((.projects[\$root] // {}) + {hasTrustDialogAccepted: true})"
  for root in "$@"; do
    if ! jq --arg root "$root" \
      "$trust_filter" \
      "$updated" >"${updated}.next"; then
      echo "alternate-claude-workspace-trust: cannot add $root to $config_path; verify the JSON and available disk space, then retry" >&2
      cleanup_trust_files "$updated" "${updated}.next" || return 1
      return 1
    fi
    if ! mv "${updated}.next" "$updated"; then
      echo "alternate-claude-workspace-trust: cannot advance the temporary config for $config_path; fix directory permissions, then retry" >&2
      cleanup_trust_files "$updated" "${updated}.next" || return 1
      return 1
    fi
  done
  local comparison_status
  if cmp -s "$config_path" "$updated"; then
    cleanup_trust_files "$updated" || return 1
  else
    comparison_status=$?
    if (( comparison_status != 1 )); then
      echo "alternate-claude-workspace-trust: cannot compare $config_path with its updated configuration; verify both files are readable, then retry" >&2
      cleanup_trust_files "$updated" || return 1
      return 1
    fi
    if ! chmod --reference="$config_path" "$updated"; then
      echo "alternate-claude-workspace-trust: cannot preserve permissions for $config_path; fix file ownership, then retry" >&2
      cleanup_trust_files "$updated" || return 1
      return 1
    fi
    if ! mv "$updated" "$config_path"; then
      echo "alternate-claude-workspace-trust: cannot atomically replace $config_path; fix directory permissions, then retry" >&2
      cleanup_trust_files "$updated" || return 1
      return 1
    fi
  fi
)

mark_alternate_claude_workspaces() {
  if (( $# < 2 )); then
    echo "alternate-claude-workspace-trust: caller name and at least one workspace path are required" >&2
    return 2
  fi
  local caller=$1
  shift
  if ! resolve_claude_alt_config_dir "$caller"; then
    echo "$caller: alternate Claude config resolution failed; correct the configured paths, then retry; continuing" >&2
    return 0
  fi
  local config_path
  for config_path in \
    "$ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR/.claude.json" \
    "$ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR/.claude.json"; do
    mark_alternate_claude_trust "$config_path" "$@" \
      || echo "$caller: alternate Claude workspace trust setup failed for $config_path; correct the preceding error, then retry; continuing" >&2
  done
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  if ! SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd); then
    echo "alternate-claude-workspace-trust: cannot resolve its script directory; restore directory access, then retry" >&2
    exit 1
  fi
  # The standalone entry point resolves this fixed sibling path at runtime.
  # shellcheck source=scripts/claude-alt-config-dir.sh
  source "$SCRIPT_DIR/claude-alt-config-dir.sh" \
    || { echo "alternate-claude-workspace-trust: cannot load the alternate config resolver; restore scripts/claude-alt-config-dir.sh, then retry" >&2; false; }
  mark_alternate_claude_workspaces alternate-claude-workspace-trust "$@"
fi
