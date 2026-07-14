#!/usr/bin/env bash
# Run one repo lifecycle task with the preferred harness available, then make
# any commits preserved from an incomplete agent run visible to the operator.
# llmlint: ignore-file[robust_shell, boundary_inputs_validated, work_goes_through_command_surface] this IS a command-surface recipe (`just repo-task-auto`) wrapping the `orchestrator-repo-task` entry point to add env setup + post-run reporting. It deliberately omits `set -e` so it can still report the preserved-commit delta AFTER a non-zero (not-completed) dispatch — the very case it exists for; failure paths are checked explicitly. The fields it consumes are this project's own orchestrator-repo-task JSON result (a trusted internal boundary), and the branch is guarded by `git show-ref` before any ref use.
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
  printf 'repo-task-auto: dispatch produced no valid JSON result. See the orchestrator-repo-task diagnostics above, or re-run just repo-task <args> directly to surface the error.\n' >&2
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

# One concise line: the outcome, plus — when the run left commits on an unmerged
# branch (the not-completed case) — where to find and inspect them.
report="repo-task-auto: $outcome"
[[ -n $detail ]] && report+=" ($detail)"
if [[ -d $clone/.git ]] && git -C "$clone" show-ref --verify --quiet "refs/heads/$branch"; then
  base_ref="origin/$base_branch"
  git -C "$clone" rev-parse --verify --quiet "$base_ref^{commit}" >/dev/null || base_ref=$base_branch
  commit_count=$(git -C "$clone" rev-list --count "$base_ref..$branch" 2>/dev/null || printf '?')
  report+=" — $commit_count commit(s) on $branch: git -C $clone log $base_ref..$branch"
fi
printf '%s\n' "$report"

if [[ $outcome == "error" ]]; then
  ((dispatch_status != 0)) || dispatch_status=1
  exit "$dispatch_status"
fi
exit 0
