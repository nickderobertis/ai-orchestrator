#!/usr/bin/env bash
# One source for the environment that selects this repository's llmlint runtime.
#
# Sourced by both ends of the cached tier — scripts/llmlint-judge.sh, which judges,
# and scripts/llmlint-fingerprint.sh, which keys the cache on the judge
# configuration. That sharing is the point: `llmlint config` renders
# LLMLINT_ONEHARNESS_BIN into its output, so a fingerprint that read the caller's
# value instead would hash one judged diff to a different key per dispatch, and the
# non-deterministic judge would re-roll every round.
#
# `.venv/bin` is prepended rather than made the only source: scripts/setup-llmlint.sh
# installs llmlint with `uv tool` into ~/.local/bin, so llmlint itself usually comes
# from the inherited PATH. Both ends resolve it from that same PATH, so they cannot
# disagree about which llmlint the key describes.
#
# llmlint: ignore-file[boundary_inputs_validated] Neither value this function reads
# crosses a trust boundary. `$1` is the repository root each caller resolved for
# itself from its own `$0` — never an argument an operator or a config supplies —
# and both callers refuse to run at all when that resolution fails. The inherited
# `PATH` is deliberately kept rather than replaced: `scripts/setup-llmlint.sh`
# installs llmlint outside the checkout, so validating or narrowing it here would
# make the judge and the fingerprint resolve different binaries, which is the split
# key this helper exists to prevent. The function is these three lines, so this is
# file-scoped only because there is no smaller scope to name.
set -euo pipefail

llmlint_runtime_env() {
  local root=$1
  export PATH="$root/.venv/bin:$PATH"
  export LLMLINT_ONEHARNESS_BIN="$root/scripts/llmlint-oneharness.sh"
}
