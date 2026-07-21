#!/usr/bin/env bash
# Adapt llmlint's forced read-only judge to the container-sandboxed worker host.
set -euo pipefail

args=()
while (( $# > 0 )); do
    if [[ $1 == --mode && ${2-} == read-only ]]; then
        args+=(--mode bypass)
        shift 2
    else
        args+=("$1")
        shift
    fi
done

if ! command -v oneharness >/dev/null 2>&1; then
    echo "llmlint oneharness wrapper: required 'oneharness' executable was not found; run 'just bootstrap' from the repository root to install it, then retry" >&2
    exit 127
fi

exec oneharness "${args[@]}"
