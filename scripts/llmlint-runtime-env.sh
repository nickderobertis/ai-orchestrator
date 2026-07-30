#!/usr/bin/env bash
# One source for the environment that selects this repository's llmlint runtime.
set -euo pipefail

llmlint_runtime_env() {
  local root=$1
  export PATH="$root/.venv/bin:$PATH"
  export LLMLINT_ONEHARNESS_BIN="$root/scripts/llmlint-oneharness.sh"
}
