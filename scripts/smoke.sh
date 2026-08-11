#!/usr/bin/env bash
# `just smoke` — spend one real agent-harness turn and prove the launch path works.
#
# `oneagentgraph smoke` runs plain `oneharness` against a config it generates itself,
# and takes the caller's environment as-is, so the three values below are the recipe
# rather than ceremony around it:
#
#   1. The agent harness. `scripts/oneharness-agent.sh` is what forces this
#      repository's five-identity chain, which is the launch path being proven.
#   2. A status directory of this run's own — the safety check. The pre-push hook
#      runs this from inside a dispatch, and the wrapper claims `agent.pid` and
#      clears the terminal markers in whatever directory it is handed: an inherited
#      one is the live dispatch's, so the nested turn hijacks the liveness protocol
#      its own dispatcher is watching and that dispatch dies mid-turn. The shape
#      created below is the one the wrapper validates,
#      `/*/orchestrator-watchdog-*/agent`.
#   3. A history store nothing else writes to, because the smoke judges the record it
#      just wrote, plus this tier's own labels.
#
# Extra arguments reach `oneagentgraph smoke` (`--dir` chooses where to spend the
# turn). Everything this wrapper creates is removed on the way out.
set -euo pipefail

script_dir="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"

# `mktemp -d` gives the `/tmp/orchestrator-watchdog-*` parent; `agent` is the child
# the wrapper requires. Nothing claims the watchdog ownership lock, which is what
# keeps a concurrent sweep from treating this as a finished dispatch's leavings.
smoke_root="$(mktemp -d "${TMPDIR:-/tmp}/orchestrator-watchdog-smoke-XXXXXXXX")"
status_dir="$smoke_root/agent"
history_dir="$smoke_root/history"
mkdir -p -- "$status_dir" "$history_dir"
trap 'rm -rf -- "$smoke_root"' EXIT

ONEAGENTGRAPH_ONEHARNESS_BIN="$script_dir/oneharness-agent.sh" \
    ORCHESTRATOR_AGENT_STATUS_DIR="$status_dir" \
    ONEHARNESS_HISTORY_DIR="$history_dir" \
    ONEHARNESS_HISTORY_LABELS="role=smoke,smoke=${smoke_root##*-}" \
    uv run oneagentgraph smoke "$@"
