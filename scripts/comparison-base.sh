#!/usr/bin/env bash
# Resolve the remote-tracking ref used by the complete gate and publication.
set -euo pipefail

remote=${1:-${ORCHESTRATOR_COMPARISON_REMOTE:-origin}}
base=${2:-${ORCHESTRATOR_COMPARISON_BASE:-}}

git check-ref-format --allow-onelevel "refs/remotes/$remote" >/dev/null 2>&1 || {
  echo "comparison-base: '$remote' is not a valid remote name" >&2; exit 2;
}
git remote get-url "$remote" >/dev/null 2>&1 || {
  echo "comparison-base: remote '$remote' does not exist; pass a configured remote" >&2; exit 2;
}
if [[ -n $base ]]; then
  git check-ref-format --branch "$base" >/dev/null 2>&1 || {
    echo "comparison-base: '$base' is not a valid branch name" >&2; exit 2;
  }
else
  symbolic=$(git symbolic-ref --quiet --short "refs/remotes/$remote/HEAD" 2>/dev/null || true)
  if [[ $symbolic == "$remote/"* ]] && git show-ref --verify --quiet "refs/remotes/$symbolic"; then
    base=${symbolic#"$remote/"}
  else
    mapfile -t refs < <(git for-each-ref --format='%(refname:strip=3)' "refs/remotes/$remote" | sed '/^HEAD$/d')
    if (( ${#refs[@]} == 1 )); then base=${refs[0]}; else
      echo "comparison-base: cannot discover a unique base for '$remote' (found: ${refs[*]:-none}); pass it explicitly: just gate $remote <branch>" >&2
      exit 2
    fi
  fi
fi
ref="$remote/$base"
git show-ref --verify --quiet "refs/remotes/$ref" || {
  echo "comparison-base: '$ref' is missing; fetch '$remote' or choose an existing base" >&2; exit 2;
}
printf '%s\n' "$ref"
