#!/usr/bin/env bash
# Shared, sourceable implementation for marking alternate-Claude workspaces trusted.
set -euo pipefail

mark_alternate_claude_trust() {
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
  local updated root
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
    rm -f "$updated"
    return 1
  fi
  if ! jq -e 'type == "object" and ((.projects // {}) | type == "object")' "$updated" \
    >/dev/null; then
    echo "alternate-claude-workspace-trust: $config_path must contain a JSON object with an optional projects object; repair or replace it, then retry" >&2
    rm -f "$updated"
    return 1
  fi
  for root in "$@"; do
    if ! jq --arg root "$root" \
      '.projects = (.projects // {}) | .projects[$root] = ((.projects[$root] // {}) + {hasTrustDialogAccepted: true})' \
      "$updated" >"${updated}.next"; then
      echo "alternate-claude-workspace-trust: cannot add $root to $config_path; verify the JSON and available disk space, then retry" >&2
      rm -f "$updated" "${updated}.next"
      return 1
    fi
    if ! mv "${updated}.next" "$updated"; then
      echo "alternate-claude-workspace-trust: cannot advance the temporary config for $config_path; fix directory permissions, then retry" >&2
      rm -f "$updated" "${updated}.next"
      return 1
    fi
  done
  if cmp -s "$config_path" "$updated"; then
    rm -f "$updated"
  else
    if ! chmod --reference="$config_path" "$updated"; then
      echo "alternate-claude-workspace-trust: cannot preserve permissions for $config_path; fix file ownership, then retry" >&2
      rm -f "$updated"
      return 1
    fi
    if ! mv "$updated" "$config_path"; then
      echo "alternate-claude-workspace-trust: cannot atomically replace $config_path; fix directory permissions, then retry" >&2
      rm -f "$updated"
      return 1
    fi
  fi
}

mark_alternate_claude_workspaces() {
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
  SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
  # shellcheck source=scripts/claude-alt-config-dir.sh
  source "$SCRIPT_DIR/claude-alt-config-dir.sh"
  mark_alternate_claude_workspaces alternate-claude-workspace-trust "$@"
fi
