#!/usr/bin/env bash
# Spike-only: run orchestrator-e2e:test, plan-tooling:test and orchestrator:test-docs at once,
# as `just check`'s first phase starts them (Nx runs three tasks at a time and starts these
# three first), each through the real scripts/nx.sh. Each wrapper writes its own preserved
# log (.logs/nx.<pid>.log), because the label is pre-claimed. Exit status is the worst one.
set -uo pipefail
root=$(git rev-parse --show-toplevel); cd "$root"
export ORCHESTRATOR_PRESERVED_LOGS="$root/.logs/nx.log"
pids=()
for task in orchestrator-e2e:test plan-tooling:test orchestrator:test-docs; do
  ./scripts/nx.sh run "$task" & pids+=($!)
done
worst=0
for p in "${pids[@]}"; do wait "$p"; s=$?; (( s > worst )) && worst=$s; done
exit "$worst"
