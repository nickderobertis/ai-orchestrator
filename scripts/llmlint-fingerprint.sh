#!/usr/bin/env bash
# Fingerprint the llmlint judge configuration for Nx's cache key.
#
# Declared as the `lint-llm-diff` target's `runtime` input, so a recorded verdict
# is invalidated by anything that changes what the judge would ask — including the
# two things no tracked file records: the *installed* llmlint version, and the
# resolved content of a pinned plugin fetched from outside this repository.
# `llmlint config` prints the effective merged config (this repo's llmlint.yml
# plus every plugin's resolved rules), so one hash covers all of them.
#
# Absolute paths are folded out so two checkouts of the same repository share
# cache entries; only the repository root is path-dependent in that output.
#
# Run it by hand to see the current judge fingerprint — the answer to "why did the
# cache miss when nothing in the tree changed?". Nx treats a failing runtime input
# as no contribution and still runs the task, so these diagnostics are for that
# direct run; the tier stays safe either way, because an llmlint that cannot report
# its version or resolve its config also cannot judge the diff, and Nx never caches
# that failure.
#
# llmlint: ignore-file[changed_behavior_has_e2e] What this script decides — that a
# changed plugin rule source or a changed installed llmlint version invalidates a
# recorded verdict, and that an llmlint which cannot report its version or resolve
# its config is named rather than hashed — runs end to end in
# tests/e2e/test_llmlint_cache_e2e.py. What remains are host-failure guards on the
# checkout layout and sha256sum; simulating those is the guard's job, not a journey's.
set -euo pipefail

root="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)" || {
  echo "llmlint fingerprint: could not locate the repository from this script; reinstall the checkout and retry" >&2
  exit 1
}
version="$(llmlint --version)" || {
  echo "llmlint fingerprint: 'llmlint --version' failed; run 'just setup-llmlint' and retry" >&2
  exit 1
}
cd "$root" || {
  echo "llmlint fingerprint: could not enter '$root'; repair its permissions and retry" >&2
  exit 1
}
config="$(llmlint config)" || {
  echo "llmlint fingerprint: 'llmlint config' failed; repair llmlint.yml or its plugin pins and retry" >&2
  exit 1
}
digest="$(printf '%s\n%s\n' "$version" "${config//"$root"/\{root\}}" | sha256sum)" || {
  echo "llmlint fingerprint: could not hash the judge configuration; verify sha256sum is available and retry" >&2
  exit 1
}
printf '%s\n' "${digest%% *}" || {
  echo "llmlint fingerprint: could not write the fingerprint; check the receiving stream and retry" >&2
  exit 1
}
