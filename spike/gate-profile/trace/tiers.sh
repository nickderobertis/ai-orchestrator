#!/usr/bin/env bash
# Spike-only (item 6): trace the affected tiers through their own Nx commands, as `just check`
# runs them three at a time, under one `strace -f -e trace=openat,execve,...` whose output is
# streamed through filter.py (no raw trace on disk; execve and tracked-file openat records kept).
# Run through `measure.sh cmd ... -- bash -c '"$GATE_PROFILE_HARNESS"/trace/tiers.sh'` so the
# profiling plugin records which test each xdist worker ran when. Writes $GATE_PROFILE_RUN_OUT/tier-reads/.
#
# strace -f waits for every traced descendant, and some journeys leave a server running after
# their tier ends, so once run-tiers.sh reports every tier done (and a grace period passes) the
# strace this script started is ended by its own PID; what was still traced is recorded first.
set -uo pipefail
root=$(git rev-parse --show-toplevel); cd "$root"
here=${GATE_PROFILE_HARNESS:?run through measure.sh cmd}/trace
out=${GATE_PROFILE_RUN_OUT:?}/tier-reads; mkdir -p "$out"; rm -f "$out/tiers.done"
git ls-files >"$out/tracked.txt"
export ORCHESTRATOR_PRESERVED_LOGS="$root/.logs/nx.log" TIERS_DONE="$out/tiers.done"
strace -f -ttt -qq -s 256 -e signal=none -e status=successful \
  -e trace=openat,execve,clone,clone3,fork,vfork,chdir \
  -o "|python3 -I $here/filter.py $out $root" \
  bash "$here/run-tiers.sh" &
sp=$!
until [ -f "$out/tiers.done" ] || ! kill -0 "$sp" 2>/dev/null; do sleep 10; done
for _ in $(seq 1 12); do kill -0 "$sp" 2>/dev/null || break; sleep 5; done
if kill -0 "$sp" 2>/dev/null; then
  for p in /proc/[0-9]*; do
    [ "$(awk '/^TracerPid/{print $2}' "$p/status" 2>/dev/null)" = "$sp" ] &&
      echo "${p#/proc/} $(tr '\0' ' ' <"$p/cmdline" 2>/dev/null | cut -c1-300)"
  done >"$out/still-traced-at-end.txt"
  kill -KILL "$sp"
fi
wait "$sp"
for _ in $(seq 1 60); do [ -f "$out/reads.json" ] && break; sleep 5; done
