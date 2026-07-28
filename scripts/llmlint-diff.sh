#!/usr/bin/env bash
# Body of the cached Nx `workspace:lint-llm-diff` target: judge the branch diff
# against one resolved base commit. Run it through `just lint-llm-diff <base>`,
# which resolves the base ref to the commit this reads and keys the cache on.
#
# A verdict is recorded, not raised. Nx caches a task only when it succeeds, so a
# judged failure returned as a non-zero exit could never be replayed — the branch
# would re-roll a non-deterministic judge on every gate run, which is the whole
# defect this tier is meant to close. Instead the judge's report and its status
# land in the target's declared output, this script exits 0, and
# `scripts/llmlint-verdict.sh` replays both for the operator. Enforcement is
# unchanged: a recorded failure still fails `just lint-llm-diff`, `just gate`, and
# the pre-push hook, and it fails identically whether it was judged or replayed.
#
# Only llmlint's own verdict statuses (0 clean, 1 findings) are recorded. Anything
# else is the tool failing rather than judging, so it propagates and stays
# uncached: a broken run must never become a sticky verdict.
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
#
# llmlint: ignore-file[changed_behavior_has_e2e] Every judged and replayed journey
# this script has — clean and failing verdicts, each invalidation case, a refused
# base, unusable labels, and a tool that exits without judging — runs end to end in
# tests/e2e/test_llmlint_cache_e2e.py. What remains are host-failure guards on
# rm/mkdir/write; simulating a broken filesystem is the guard's job, not a journey's.
set -euo pipefail

root="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)" || {
  echo "lint-llm-diff: could not locate the repository from this script; reinstall the checkout and retry" >&2
  exit 1
}
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

verdict="$root/.nx/llmlint-diff"
rm -rf "$verdict" || {
  echo "lint-llm-diff: could not clear '$verdict'; repair its permissions and retry" >&2
  exit 1
}
mkdir -p "$verdict" || {
  echo "lint-llm-diff: could not create '$verdict'; repair its parent permissions and retry" >&2
  exit 1
}

# Opened before the judge runs, and appended to afterwards: a redirection that
# fails here would otherwise surface as exit 1 and be recorded as a findings
# verdict, caching a filesystem fault as a judgement of the diff.
: >"$verdict/report" || {
  echo "lint-llm-diff: could not open '$verdict/report' for the judge report; free disk space and retry" >&2
  exit 1
}

status=0
llmlint --diff --diff-base "$base_sha" >>"$verdict/report" 2>&1 || status=$?
if ((status > 1)); then
  cat "$verdict/report" >&2 || echo "lint-llm-diff: the failed run left no readable report" >&2
  rm -rf "$verdict" || {
    echo "lint-llm-diff: could not discard the incomplete record in '$verdict'; remove it by hand before retrying" >&2
    exit 1
  }
  echo "lint-llm-diff: llmlint exited $status without reaching a verdict; repair the judge toolchain and retry" >&2
  exit "$status"
fi

# Written last, so a record counts as complete only once its status is on disk.
printf '%s\n' "$status" >"$verdict/status" || {
  echo "lint-llm-diff: could not record the verdict status in '$verdict'; free disk space and retry" >&2
  exit 1
}
# Outside the declared output, so a cache restore cannot reproduce it: its presence
# is what tells the verdict replay that this run paid for a judge call.
: >"$verdict.judged" || {
  echo "lint-llm-diff: could not mark '$verdict.judged'; repair its parent permissions and retry" >&2
  exit 1
}
