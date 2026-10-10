#!/usr/bin/env bash
# Spike-only: trace one pytest selection under strace (openat,execve), then summarise
# (a) the tracked files it read, root or any copy, and (b) the slowest processes it spawned.
#   trace.sh LABEL NODEID...   -> results/traces/LABEL.{json,log}; raw strace kept under $GATE_PROFILE_HOME/traces
set -uo pipefail
label=$1; shift
root=$(git rev-parse --show-toplevel); here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
raw=${GATE_PROFILE_HOME:-$HOME/.cache/gate-profile-spike}/traces/$label; mkdir -p "$raw"
out=$root/spike/gate-profile/results/traces; mkdir -p "$out"
cd "$root"
s=$(date +%s.%N)
strace -f -ttt -s 256 -e trace=openat,execve -o "$raw/strace.txt" .venv/bin/python -m pytest -p no:cacheprovider -q "$@" >"$out/$label.log" 2>&1; rc=$?
e=$(date +%s.%N)
git ls-files >"$raw/tracked.txt"
.venv/bin/python -I "$here/summarise.py" "$raw/strace.txt" "$raw/tracked.txt" "$root" --label "$label" --rc "$rc" --wall "$(echo "$e - $s" | bc)" --nodeids "$@" >"$out/$label.json"
echo "$label rc=$rc wall=$(echo "$e - $s" | bc)s"
