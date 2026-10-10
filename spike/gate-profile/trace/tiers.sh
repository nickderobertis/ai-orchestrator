#!/usr/bin/env bash
# Spike-only (item 6): trace the affected tiers through their own Nx commands, as `just check`
# runs them three at a time, under one `strace -f` whose output is streamed through filter.py
# (no raw trace on disk). Run through `measure.sh cmd ... -- <harness>/trace/tiers.sh` so the
# profiling plugin records which test each xdist worker ran when. Writes $GATE_PROFILE_RUN_OUT/tier-reads/.
set -uo pipefail
root=$(git rev-parse --show-toplevel); cd "$root"
here=${GATE_PROFILE_HARNESS:?run through measure.sh cmd}/trace
out=${GATE_PROFILE_RUN_OUT:?}/tier-reads; mkdir -p "$out"
git ls-files >"$out/tracked.txt"
export ORCHESTRATOR_PRESERVED_LOGS="$root/.logs/nx.log"
strace -f -ttt -qq -e signal=none -e status=successful \
  -e trace=openat,clone,clone3,fork,vfork,chdir \
  -o "|python3 -I $here/filter.py $out $root" \
  bash "$here/run-tiers.sh"
