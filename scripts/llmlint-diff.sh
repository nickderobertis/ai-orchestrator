#!/usr/bin/env bash
# Body of the cached Nx `workspace:lint-llm-diff` target: judge the branch diff
# against one resolved base commit. Run it through `just lint-llm-diff <base>`,
# which resolves the base ref to the commit this reads and keys the cache on.
#
# The base arrives as `LLMLINT_DIFF_BASE_SHA` rather than an argument because Nx
# hashes declared environment variables but not target arguments: keying and
# judging on the same value is what stops a verdict computed against one base
# from being replayed for another.
#
# `role=llmlint` stamps this tier's own harness sessions so they are separable from
# the agent/judge sessions of the work being linted (whose roles come from
# oneharness.toml / oneharness.judge.toml). It is layered over — not substituted
# for — the graph labels inherited when a dispatched agent runs its own gate, so a
# finding stays attributable to the run/round/node that provoked it. Those labels
# deliberately stay out of the cache key: they identify who asked, not what is
# judged.
set -euo pipefail

root="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
base_sha="${LLMLINT_DIFF_BASE_SHA:-}"
[[ "$base_sha" =~ ^[0-9a-f]{40,64}$ ]] || {
  echo "lint-llm-diff: LLMLINT_DIFF_BASE_SHA must be a resolved commit id; run 'just lint-llm-diff <base>' instead of this target directly" >&2
  exit 1
}
git -C "$root" rev-parse --verify --quiet "${base_sha}^{commit}" >/dev/null || {
  echo "lint-llm-diff: base commit '$base_sha' is missing from this checkout; fetch it and retry" >&2
  exit 1
}
labels="$(uv run orchestrator-history-labels role=llmlint)" || {
  echo "lint-llm-diff: could not derive harness history labels; run 'just bootstrap' and retry" >&2
  exit 1
}
[[ "$labels" =~ ^[A-Za-z0-9_]+=[^,[:space:]]*(,[A-Za-z0-9_]+=[^,[:space:]]*)*$ ]] || {
  echo "lint-llm-diff: harness history labels are not comma-separated key=value pairs; run 'just bootstrap' and retry" >&2
  exit 1
}

export PATH="$root/.venv/bin:$PATH"
export LLMLINT_ONEHARNESS_BIN="$root/scripts/llmlint-oneharness.sh"
export ONEHARNESS_HISTORY_LABELS="$labels"
# llmlint: ignore[tool_output_is_signal] The judge's per-rule report is this tier's product, and replaying it verbatim from the cache is what proves the memoized verdict.
exec llmlint --diff --diff-base "$base_sha"
