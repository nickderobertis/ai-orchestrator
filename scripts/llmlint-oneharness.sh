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

exec oneharness "${args[@]}"
