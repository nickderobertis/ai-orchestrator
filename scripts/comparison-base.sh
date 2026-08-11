#!/usr/bin/env bash
# Resolve the remote-tracking ref used by the complete gate and publication.
set -euo pipefail

# The lifecycle names the comparison ref so the worker's gate and the publishing
# push judge the same diff — see docs/repo-lifecycle.md, "One judged diff, one
# verdict". That identity now arrives under `onevcs`'s own names: it exports
# `ONEVCS_COMPARISON_REMOTE` / `ONEVCS_COMPARISON_BASE` into the gate it runs and
# into the push it makes, where `orchestrator/verify.py` used to export
# `ORCHESTRATOR_COMPARISON_*`. Reading only the old names left the base unset on
# every lifecycle path, so each side resolved its own — two base commits, two
# independent judge rolls, and a push that could land work whose own gate had
# failed. The `ORCHESTRATOR_*` spelling still wins where it is set, because it is
# also the operator's documented override.
remote=${1:-${ORCHESTRATOR_COMPARISON_REMOTE:-${ONEVCS_COMPARISON_REMOTE:-origin}}}
base=${2:-${ORCHESTRATOR_COMPARISON_BASE:-${ONEVCS_COMPARISON_BASE:-}}}

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
    current=$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)
    if [[ -n $current ]]; then
      upstream_remote=$(git config --get "branch.$current.remote" 2>/dev/null || true)
      upstream_merge=$(git config --get "branch.$current.merge" 2>/dev/null || true)
      candidate=${upstream_merge#refs/heads/}
      if [[ $upstream_remote == "$remote" && -n $candidate ]] &&
        git show-ref --verify --quiet "refs/remotes/$remote/$candidate"; then
        base=$candidate
      fi
    fi
    if [[ -z $base ]]; then
      mapfile -t refs < <(git for-each-ref --format='%(refname:strip=3)' "refs/remotes/$remote" | sed '/^HEAD$/d')
      if (( ${#refs[@]} == 1 )); then base=${refs[0]}; else
        echo "comparison-base: cannot discover a unique base for '$remote' (found: ${refs[*]:-none}); pass it explicitly: just gate $remote <branch>" >&2
        exit 2
      fi
    fi
  fi
fi
ref="$remote/$base"
git show-ref --verify --quiet "refs/remotes/$ref" || {
  echo "comparison-base: '$ref' is missing; fetch '$remote' or choose an existing base" >&2; exit 2;
}
printf '%s\n' "$ref"
