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

# Two refusal statuses, because callers do two different things with them. This tree
# having no base to offer — no such remote, no unique branch to discover — is the state
# a fresh copy is in, and `scripts/nx-selection.sh` answers it by selecting every
# project. A base that was *named* and is not usable is not that state: nothing narrows,
# and checking everything in silence would leave a misconfigured comparison identity
# looking like a fresh copy while the gate it is exported for refuses the same value.
# `tests/e2e/test_workspace_contract_e2e.py` and `tests/e2e/test_gate_selection_e2e.py`
# reconcile both against the callers that branch on them. Neither is named after the
# comparison identity they are about: `tests/test_dispatch_environment_contract.py`
# discovers a third *published* spelling of that identity by scanning this file for
# one, and a local constant wearing that shape would be found as one.
readonly NO_BASE_AVAILABLE=2
readonly NAMED_BASE_UNUSABLE=3

named_remote=${1:-${ORCHESTRATOR_COMPARISON_REMOTE:-${ONEVCS_COMPARISON_REMOTE:-}}}
remote=${named_remote:-origin}
base=${2:-${ORCHESTRATOR_COMPARISON_BASE:-${ONEVCS_COMPARISON_BASE:-}}}

git check-ref-format --allow-onelevel "refs/remotes/$remote" >/dev/null 2>&1 || {
  echo "comparison-base: '$remote' is not a valid remote name; pass one 'git remote' lists: just gate <remote> <branch>" >&2; exit "$NAMED_BASE_UNUSABLE";
}
git remote get-url "$remote" >/dev/null 2>&1 || {
  # A remote somebody named and this checkout does not have is a value to repair, where
  # the default `origin` being absent is a copy of the tree that simply has no base.
  if [[ -n $named_remote ]]; then
    echo "comparison-base: remote '$remote' does not exist; pass one 'git remote' lists: just gate <remote> <branch>" >&2
    exit "$NAMED_BASE_UNUSABLE"
  fi
  echo "comparison-base: this checkout has no 'origin' to compare against; add one, or name the remote: just gate <remote> <branch>" >&2
  exit "$NO_BASE_AVAILABLE"
}
if [[ -n $base ]]; then
  git check-ref-format --branch "$base" >/dev/null 2>&1 || {
    echo "comparison-base: '$base' is not a valid branch name; pass one '$remote' has: just gate $remote <branch>" >&2; exit "$NAMED_BASE_UNUSABLE";
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
      # Read, then filtered, rather than `mapfile < <(git … | sed …)`, whose status is
      # `mapfile`'s and so is always success. A read this cannot complete is answered
      # deliberately rather than assumed away: git has already said why on stderr, and
      # a repository whose refs cannot be listed has no unique base to offer, which is
      # what the refusal below says and how it is repaired.
      listed=$(git for-each-ref --format='%(refname:strip=3)' "refs/remotes/$remote" || true)
      refs=()
      while IFS= read -r candidate_ref; do
        if [[ -n $candidate_ref && $candidate_ref != HEAD ]]; then refs+=("$candidate_ref"); fi
      done <<<"$listed"
      if (( ${#refs[@]} == 1 )); then base=${refs[0]}; else
        echo "comparison-base: cannot discover a unique base for '$remote' (found: ${refs[*]:-none}); pass it explicitly: just gate $remote <branch>" >&2
        exit "$NO_BASE_AVAILABLE"
      fi
    fi
  fi
fi
ref="$remote/$base"
git show-ref --verify --quiet "refs/remotes/$ref" || {
  echo "comparison-base: '$ref' is missing; fetch '$remote' or choose an existing base" >&2; exit "$NAMED_BASE_UNUSABLE";
}
printf '%s\n' "$ref"
