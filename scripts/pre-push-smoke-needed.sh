#!/usr/bin/env bash
# Exit 0 when any ref update changes the paid-smoke launch path; 1 otherwise.
set -euo pipefail

comparison=${1:?comparison ref is required}
zero=0000000000000000000000000000000000000000
git rev-parse --verify --quiet "$comparison^{commit}" >/dev/null || {
  echo "pre-push-smoke-needed: comparison '$comparison' is not a commit; fetch it or choose a valid comparison base" >&2
  exit 2
}
updates=()
while read -r _local_ref local_sha _remote_ref remote_sha; do
  [[ -n ${local_sha:-} ]] || continue
  [[ $local_sha =~ ^[0-9a-f]{40}$ && ${remote_sha:-$zero} =~ ^[0-9a-f]{40}$ ]] || {
    echo "pre-push-smoke-needed: Git supplied an invalid ref update; retry the push after checking repository integrity" >&2
    exit 2
  }
  updates+=("$local_sha ${remote_sha:-$zero}")
done
if (( ${#updates[@]} == 0 )); then
  updates+=("HEAD $zero")
fi

for update in "${updates[@]}"; do
  read -r local_sha remote_sha <<<"$update"
  [[ $local_sha != "$zero" ]] || continue
  git rev-parse --verify --quiet "$local_sha^{commit}" >/dev/null || {
    echo "pre-push-smoke-needed: local object '$local_sha' is not a commit; push a branch commit" >&2
    exit 2
  }
  base=$remote_sha
  [[ $base != "$zero" ]] || base=$comparison
  changed=$(git diff --name-only "$base" "$local_sha" --) || {
    echo "pre-push-smoke-needed: cannot compare '$base' with '$local_sha'; fetch the remote and retry" >&2
    exit 2
  }
  if grep -Eq '^(scripts/|config/oneharness\.version$|config/onejudge\.base\.yaml$|oneharness\.toml$|oneharness\.judge\.toml$)' <<<"$changed"; then
    exit 0
  fi
done
exit 1
