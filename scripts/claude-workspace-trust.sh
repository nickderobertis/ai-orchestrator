#!/usr/bin/env bash
# Shared, sourceable implementation for marking Claude workspaces trusted.

mark_alternate_claude_trust() {
  local config_path=$1
  shift
  [ -f "$config_path" ] || return 0
  if ! command -v jq >/dev/null 2>&1; then
    echo "claude-workspace-trust: cannot mark alternate Claude workspaces trusted: jq is unavailable" >&2
    return 1
  fi
  if ! command -v flock >/dev/null 2>&1; then
    echo "claude-workspace-trust: cannot mark alternate Claude workspaces trusted: flock is unavailable" >&2
    return 1
  fi
  local updated root
  exec 9>"${config_path}.trust.lock" || return 1
  flock 9 || return 1
  [ -f "$config_path" ] || return 0
  updated=$(mktemp "${config_path}.trust.XXXXXX") || return 1
  if ! jq '.' "$config_path" >"$updated"; then
    echo "claude-workspace-trust: alternate Claude config is not valid JSON: $config_path" >&2
    rm -f "$updated"
    return 1
  fi
  for root in "$@"; do
    if ! jq --arg root "$root" \
      '.projects = (.projects // {}) | .projects[$root] = ((.projects[$root] // {}) + {hasTrustDialogAccepted: true})' \
      "$updated" >"${updated}.next"; then
      rm -f "$updated" "${updated}.next"
      return 1
    fi
    if ! mv "${updated}.next" "$updated"; then
      rm -f "$updated" "${updated}.next"
      return 1
    fi
  done
  if cmp -s "$config_path" "$updated"; then
    rm -f "$updated"
  else
    if ! chmod --reference="$config_path" "$updated"; then
      rm -f "$updated"
      return 1
    fi
    if ! mv "$updated" "$config_path"; then
      rm -f "$updated"
      return 1
    fi
  fi
}

mark_alternate_claude_workspaces() {
  local caller=$1
  shift
  if ! resolve_claude_alt_config_dir "$caller"; then
    echo "$caller: alternate Claude config resolution failed; continuing" >&2
    return 0
  fi
  local config_path
  for config_path in \
    "$ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR/.claude.json" \
    "$ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR/.claude.json"; do
    mark_alternate_claude_trust "$config_path" "$@" \
      || echo "$caller: alternate Claude workspace trust setup failed; continuing" >&2
  done
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  set -u
  SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
  # shellcheck source=scripts/claude-alt-config-dir.sh
  source "$SCRIPT_DIR/claude-alt-config-dir.sh"
  mark_alternate_claude_workspaces claude-workspace-trust "$@"
fi
