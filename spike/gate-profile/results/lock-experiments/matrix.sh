#!/usr/bin/env bash
# Hold the env lock uv uses (found by strace) for HOLD seconds, time each reader variant.
set -u
cd "$1"; LOCK=$2; HOLD=${3:-6}
variants=(
  "plain|uv run python -c pass"
  "no-sync-flag|uv run --no-sync python -c pass"
  "UV_NO_SYNC=1|env UV_NO_SYNC=1 uv run python -c pass"
  "frozen-flag|uv run --frozen python -c pass"
  "UV_FROZEN=1|env UV_FROZEN=1 uv run python -c pass"
  "locked-flag|uv run --locked python -c pass"
  "venv-bin-direct|.venv/bin/python -c pass"
  "uv-sync-locked (writer)|uv sync --locked"
)
for v in "${variants[@]}"; do
  name=${v%%|*}; cmd=${v#*|}
  flock -x "$LOCK" sleep "$HOLD" & holder=$!
  sleep 0.5
  s=$(date +%s.%N); bash -c "$cmd" >/dev/null 2>&1; rc=$?; e=$(date +%s.%N)
  wait $holder
  # unheld baseline
  s2=$(date +%s.%N); bash -c "$cmd" >/dev/null 2>&1; e2=$(date +%s.%N)
  printf '%-26s held-lock: %6.2fs (rc=%d)   unheld: %5.2fs\n' "$name" "$(echo "$e - $s" | bc)" "$rc" "$(echo "$e2 - $s2" | bc)"
done
