#!/usr/bin/env bash
# Exit 0 when any ref update changes the paid-smoke launch path; 1 otherwise.
set -euo pipefail

comparison=${1:?comparison ref is required}
zero=0000000000000000000000000000000000000000
updates=()
while read -r _local_ref local_sha _remote_ref remote_sha; do
  [[ -n ${local_sha:-} ]] || continue
  updates+=("$local_sha ${remote_sha:-$zero}")
done
if (( ${#updates[@]} == 0 )); then
  updates+=("HEAD $zero")
fi

for update in "${updates[@]}"; do
  read -r local_sha remote_sha <<<"$update"
  [[ $local_sha != "$zero" ]] || continue
  base=$remote_sha
  [[ $base != "$zero" ]] || base=$comparison
  if git diff --name-only "$base" "$local_sha" -- |
    grep -Eq '^(scripts/|config/oneharness\.version$|config/onejudge\.base\.yaml$|oneharness\.toml$|oneharness\.judge\.toml$)'; then
    exit 0
  fi
done
exit 1
