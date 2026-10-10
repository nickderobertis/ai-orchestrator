#!/usr/bin/env bash
# Copy one measured run from $GATE_PROFILE_HOME/runs/<label> onto the branch, raw per-test
# JSONL compressed. Run from the branch (not during a measurement: an untracked file in the
# measured tree would change every wholeWorkspace hash).
set -euo pipefail
label=$1
src=${GATE_PROFILE_HOME:-$HOME/.cache/gate-profile-spike}/runs/$label
dst=$(git rev-parse --show-toplevel)/spike/gate-profile/results/runs/$label
rm -rf "$dst"; mkdir -p "$dst"
for f in summary.json meta.json meta-in.json phases.json samples.jsonl procs.jsonl stdout.log stderr.log; do
  [[ -f $src/$f ]] && cp "$src/$f" "$dst/"
done
[[ -d $src/nx-runs ]] && cp -r "$src/nx-runs" "$dst/"
[[ -d $src/logs ]] && cp -r "$src/logs" "$dst/"
[[ -f $src/pytest-tests.json ]] && gzip -c "$src/pytest-tests.json" >"$dst/pytest-tests.json.gz"
[[ -d $src/pytest ]] && tar -C "$src" -czf "$dst/pytest-raw.tar.gz" pytest
du -sh "$dst"
