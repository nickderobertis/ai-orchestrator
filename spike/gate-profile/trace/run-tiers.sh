#!/usr/bin/env bash
# The six affected tiers, three Nx slots as `just check` fills them, each its own `nx run`.
set -uo pipefail
n() { ./scripts/nx.sh run "$1" --skip-nx-cache; echo "$1 exit=$?" >&2; }
n orchestrator-e2e:test &
( n plan-tooling:test; n plan-tooling:test-docs ) &
( n orchestrator:test-docs; n orchestrator-e2e:test-recipes; n orchestrator-e2e:test-checkouts ) &
wait
