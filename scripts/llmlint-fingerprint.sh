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
set -euo pipefail

root="$(git rev-parse --show-toplevel)" || {
  echo "llmlint fingerprint: run from a Git checkout and retry 'just lint-llm-diff'" >&2
  exit 1
}
version="$(llmlint --version)" || {
  echo "llmlint fingerprint: 'llmlint --version' failed; run 'just setup-llmlint' and retry" >&2
  exit 1
}
config="$(cd "$root" && llmlint config)" || {
  echo "llmlint fingerprint: 'llmlint config' failed; repair llmlint.yml or its plugin pins and retry" >&2
  exit 1
}
printf '%s\n%s\n' "$version" "${config//"$root"/\{root\}}" | sha256sum | cut -d' ' -f1
