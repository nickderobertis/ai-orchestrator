#!/usr/bin/env bash
# Exit 0 when any ref update changes the paid-smoke launch path; 1 otherwise.
set -euo pipefail

launch_paths=(
  "scripts/"
  "config/oneharness.version"
  "config/onejudge.base.yaml"
  "oneharness.toml"
  "oneharness.judge.toml"
  "oneharness.orchestrator.toml"
  "oneharness.check-in.toml"
)
if [[ ${1:-} == --print-paths ]]; then
  # llmlint: ignore[tool_output_is_signal] the declared launch-path list is this query mode's product: tests/test_smoke_selector.py reads it line by line as the authoritative contract instead of duplicating the array, so one line would have to re-encode it. The selector mode below stays silent and speaks only through its exit status.
  printf '%s\n' "${launch_paths[@]}"
  exit 0
fi

comparison=${1:?comparison ref is required}
zero=0000000000000000000000000000000000000000
git rev-parse --verify --quiet "$comparison^{commit}" >/dev/null || {
  echo "pre-push-smoke-needed: comparison '$comparison' is not a commit; fetch it or choose a valid comparison base" >&2
  exit 2
}
updates=()
while IFS= read -r update; do
  read -r -a fields <<<"$update"
  (( ${#fields[@]} == 4 )) || {
    echo "pre-push-smoke-needed: Git supplied an invalid ref update; retry the push after checking repository integrity" >&2
    exit 2
  }
  local_ref=${fields[0]}
  local_sha=${fields[1]}
  remote_ref=${fields[2]}
  remote_sha=${fields[3]}
  [[ -n $local_ref && -n $remote_ref && $local_sha =~ ^[0-9a-f]{40}$ && $remote_sha =~ ^[0-9a-f]{40}$ ]] || {
    echo "pre-push-smoke-needed: Git supplied an invalid ref update; retry the push after checking repository integrity" >&2
    exit 2
  }
  updates+=("$local_sha $remote_sha")
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
  while IFS= read -r path; do
    for launch_path in "${launch_paths[@]}"; do
      if [[ $launch_path == */ && $path == "$launch_path"* ]] || [[ $path == "$launch_path" ]]; then
        exit 0
      fi
    done
  done <<<"$changed"
done
exit 1
