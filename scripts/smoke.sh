#!/usr/bin/env bash
# `just smoke` — spend one real agent-harness turn and prove the launch path works.
#
# `oneagentgraph smoke` is the published verb, but it is not the whole recipe: the
# implementation it replaced supplied three things around that turn, and a bare
# delegation dropped all three. What each one is, and what dropping it cost:
#
#   1. THE AGENT HARNESS BINARY. The published verb generates its own throwaway
#      `oneharness.toml` (`harnesses = ["claude-code"]`) and runs plain `oneharness`
#      against it, so it never sees this repository's five-identity fallback chain
#      and answers `no harness selected: pass --all or --harness <id>`.
#      `scripts/oneharness-agent.sh` is the one thing that forces this repo's agent
#      config, and it is precisely what a launch-path smoke exists to exercise.
#
#   2. AN ISOLATED WORKER STATUS DIRECTORY. This is the safety check, not a detail.
#      `scripts/oneharness-agent.sh` takes `ORCHESTRATOR_AGENT_STATUS_DIR` from its
#      environment, overwrites `agent.pid` with its own pid, and deletes the terminal
#      markers (`agent.done`, `agent.exit_code`). Run from inside a dispatch — which
#      is exactly where the pre-push hook runs this — an inherited value names the
#      *live* dispatch's status directory, so a nested turn hijacks the liveness
#      protocol its own dispatcher is watching. When that turn ends without writing
#      `agent.done`, the dispatcher reads a tracked pid that is gone with no exit
#      recorded and declares "the agent harness process vanished mid-turn without
#      recording an exit". `orchestrator-smoke` created its own directory for this
#      reason; three dispatches died to its absence. The shape below is the one
#      `scripts/oneharness-agent.sh` validates: `/*/orchestrator-watchdog-*/agent`.
#
#   3. AN ISOLATED HISTORY STORE AND THIS TIER'S OWN LABELS. The smoke judges the
#      record it just wrote, so it reads a store nothing else is writing to, and
#      stamps `role=agent` plus a per-invocation smoke id the way every other
#      harness-backed tier here stamps its sessions.
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
