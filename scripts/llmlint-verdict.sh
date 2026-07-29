#!/usr/bin/env bash
# Replay the verdict recorded by the cached Nx `workspace:lint-llm-diff` target.
#
# This is where the tier's enforcement lives. The target itself always exits 0 so
# that Nx will cache a judged failure (Nx caches successful tasks only); the
# findings and the judged status ride in its declared output instead. Reading them
# back here means a replayed failure and a freshly judged one are the same event
# to `just lint-llm-diff`, `just gate`, and the pre-push hook: same findings, same
# non-zero exit.
#
# Provenance comes from a marker the target writes outside its declared output, so
# a cache restore cannot reproduce it — no parsing of Nx's own wording.
#
# An unusable record is a hard error, not a silent cache miss. Re-judging from here
# would spend a model call from a replay path that the operator asked to be cheap,
# and re-judging is exactly what this tier exists to avoid; reporting one is the
# target's job, not the reader's. So every way the record can fail to answer
# "what was the verdict?" — absent, unreadable, or not a judged status — stops with
# a diagnostic naming the file and the failure, and exits UNUSABLE_RECORD. That
# status is distinct from the judge's own 0 and 1 precisely so a broken record can
# never be mistaken for a clean tree or for findings.
#
# llmlint: ignore-file[changed_behavior_has_e2e] Both replay paths and every refusal
# — absent, unreadable, and unjudged records — run end to end in
# tests/e2e/test_llmlint_cache_e2e.py.
# llmlint: ignore-file[tool_output_is_signal] Emitting the verdict is this script's
# entire job: the judge's per-rule report is the tier's product, and the one line of
# provenance above it is what tells an operator whether the report was just paid for
# or replayed. A quiet success here would delete the tier's result.
set -euo pipefail

# Neither of the judge's own statuses, so no caller can confuse a broken record
# with a verdict about the diff.
readonly UNUSABLE_RECORD=2

root="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)" || {
  echo "lint-llm-diff: could not locate the repository from this script; reinstall the checkout and retry" >&2
  exit 1
}
verdict="$root/.nx/llmlint-diff"

[[ -r "$verdict/status" && -r "$verdict/report" ]] || {
  echo "lint-llm-diff: no recorded verdict in $verdict; rerun 'just lint-llm-diff <base> --skip-nx-cache'" >&2
  exit "$UNUSABLE_RECORD"
}
# `cat`, not `$(<file)`: the builtin redirection yields an empty string for an
# unreadable path without failing, which would reach the check below as a bogus
# "status ''" rather than as the read error it is.
status="$(cat -- "$verdict/status")" || {
  echo "lint-llm-diff: could not read the recorded verdict status from '$verdict/status'; rerun 'just lint-llm-diff <base> --skip-nx-cache'" >&2
  exit "$UNUSABLE_RECORD"
}
[[ "$status" =~ ^[01]$ ]] || {
  echo "lint-llm-diff: recorded verdict status '$status' in '$verdict/status' is not a judged 0 or 1; rerun 'just lint-llm-diff <base> --skip-nx-cache'" >&2
  exit "$UNUSABLE_RECORD"
}

# One line, because "green" is a single claim: this verdict, about this diff,
# against this base commit. The base rides in from the recipe that resolved and
# keyed on it; a run that somehow lost it says so rather than implying a base.
base="${LLMLINT_DIFF_BASE_SHA:-<unresolved>}"
if [[ -e "$verdict.judged" ]]; then
  echo "lint-llm-diff: judged this diff against base $base (Nx cache miss)" >&2
else
  echo "lint-llm-diff: replayed the recorded verdict for base $base (Nx cache hit)" >&2
fi
# A partial read is as unusable as none: the findings the operator acts on must be
# the whole recorded report, so a truncated one exits here instead of falling
# through to the recorded status.
cat -- "$verdict/report" || {
  echo "lint-llm-diff: could not read the recorded findings from '$verdict/report'; the report above may be truncated, so rerun 'just lint-llm-diff <base> --skip-nx-cache'" >&2
  exit "$UNUSABLE_RECORD"
}
exit "$status"
