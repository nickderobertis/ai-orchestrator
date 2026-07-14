#!/usr/bin/env bash
# Run one repo lifecycle task with the preferred harness available, then make
# any commits preserved from an incomplete agent run visible to the operator.
set -uo pipefail

readonly NODE_BIN="$HOME/.local/node/bin"
export PATH="$NODE_BIN:$PATH"

# Record dispatched runs to oneharness history (viewable with `oneharness history`)
# even against repos whose oneharness.toml doesn't enable it: the env override beats
# onejudge's `--config`, which otherwise skips history. Respect an explicit value.
export ONEHARNESS_HISTORY="${ONEHARNESS_HISTORY:-1}"

usage() {
  cat <<'EOF'
Usage: repo-task-auto <repo> <persona> [task] [options]

Omit task (or pass -) to read it from stdin. All arguments are forwarded to
orchestrator-repo-task.
EOF
}

if (( $# == 0 )); then
  usage
  exit 0
fi
if [[ $1 == "-h" || $1 == "--help" ]]; then
  uv run orchestrator-repo-task --help
  exit $?
fi

workspace="$HOME/.ai-orchestrator/workspaces"
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  case ${args[i]} in
    --workspace)
      if ((i + 1 < ${#args[@]})); then
        workspace=${args[i + 1]}
      fi
      ;;
    --workspace=*) workspace=${args[i]#--workspace=} ;;
  esac
done

result_file=$(mktemp)
trap 'rm -f "$result_file"' EXIT

uv run orchestrator-repo-task "$@" --format json --output "$result_file"
dispatch_status=$?

if ! jq -e 'type == "object" and (.outcome | type == "string")' \
  "$result_file" >/dev/null 2>&1; then
  printf 'repo-task-auto: dispatch failed without a valid JSON result\n' >&2
  ((dispatch_status != 0)) || dispatch_status=1
  exit "$dispatch_status"
fi

outcome=$(jq -r '.outcome' "$result_file")
detail=$(jq -r '.detail // ""' "$result_file")
branch=$(jq -r '.branch' "$result_file")
base_branch=$(jq -r '.base_branch' "$result_file")
repo=$(jq -r '.repo' "$result_file")
repo_key=$(printf '%s' "$repo" | sed -E 's|/|__|; s|[^A-Za-z0-9._-]+|-|g; s|^-+||; s|-+$||')
clone="$workspace/$repo_key/repo"

printf 'outcome: %s\n' "$outcome"
printf 'detail: %s\n' "${detail:-none}"

if [[ -d $clone/.git ]] && git -C "$clone" show-ref --verify --quiet "refs/heads/$branch"; then
  base_ref="origin/$base_branch"
  if ! git -C "$clone" rev-parse --verify --quiet "$base_ref^{commit}" >/dev/null; then
    base_ref=$base_branch
  fi
  commit_count=$(git -C "$clone" rev-list --count "$base_ref..$branch" 2>/dev/null || printf '?')
  printf 'agent left %s commit(s) on branch %s; inspect with ' "$commit_count" "$branch"
  printf '%s' '`'
  printf 'git -C %q log %q..%q' "$clone" "$base_ref" "$branch"
  printf '%s\n' '`'
else
  printf 'branch %s was not found in lifecycle clone %s; no commit delta available\n' \
    "$branch" "$clone"
fi

if [[ $outcome == "error" ]]; then
  ((dispatch_status != 0)) || dispatch_status=1
  exit "$dispatch_status"
fi
exit 0
