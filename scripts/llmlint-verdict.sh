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
# Refusing to guess is the point of the guards below: a missing or unreadable
# record must never be read as a clean run.
#
# llmlint: ignore-file[changed_behavior_has_e2e] Both replay paths and the refusal
# to accept an incomplete record run end to end in tests/e2e/test_llmlint_cache_e2e.py.
# llmlint: ignore-file[tool_output_is_signal] Emitting the verdict is this script's
# entire job: the judge's per-rule report is the tier's product, and the one line of
# provenance above it is what tells an operator whether the report was just paid for
# or replayed. A quiet success here would delete the tier's result.
set -euo pipefail

root="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)" || {
  echo "lint-llm-diff: could not locate the repository from this script; reinstall the checkout and retry" >&2
  exit 1
}
verdict="$root/.nx/llmlint-diff"

[[ -r "$verdict/status" && -r "$verdict/report" ]] || {
  echo "lint-llm-diff: no recorded verdict in $verdict; rerun 'just lint-llm-diff <base> --skip-nx-cache'" >&2
  exit 1
}
status="$(<"$verdict/status")"
[[ "$status" =~ ^[01]$ ]] || {
  echo "lint-llm-diff: recorded verdict status '$status' is not a judged 0 or 1; rerun 'just lint-llm-diff <base> --skip-nx-cache'" >&2
  exit 1
}

if [[ -e "$verdict.judged" ]]; then
  echo "lint-llm-diff: judged this diff (Nx cache miss)" >&2
else
  echo "lint-llm-diff: replayed the recorded verdict (Nx cache hit)" >&2
fi
cat "$verdict/report"
exit "$status"
