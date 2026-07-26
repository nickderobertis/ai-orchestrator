#!/usr/bin/env bash
# Adapt llmlint's forced read-only judge to the container-sandboxed worker host.
set -euo pipefail

if ! command -v oneharness >/dev/null 2>&1; then
    echo "llmlint oneharness wrapper: required 'oneharness' executable was not found; run 'just bootstrap' from the repository root to install it, then retry" >&2
    exit 127
fi

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(dirname -- "$script_dir")
llmlint_config="$repo_root/oneharness.llmlint.toml"
if [ ! -f "$llmlint_config" ] || [ ! -r "$llmlint_config" ]; then
    echo "llmlint oneharness wrapper: required config is not a readable regular file: $llmlint_config" >&2
    exit 2
fi

if (( $# == 0 )); then
    echo "llmlint oneharness wrapper: expected oneharness arguments" >&2
    exit 2
fi
if (( $# == 1 )) && [[ $1 == --version ]]; then
    exec oneharness --version
fi
if [[ $1 != run ]]; then
    echo "llmlint oneharness wrapper: expected the 'run' subcommand" >&2
    exit 2
fi

args=()
read_only=false
while (( $# > 0 )); do
    if [[ $1 == --mode && ${2-} == read-only ]]; then
        args+=(--mode read-only)
        read_only=true
        shift 2
    else
        args+=("$1")
        shift
    fi
done

# Codex's read-only filesystem sandbox is usable on this host, but its default
# network isolation asks bubblewrap to configure loopback in a namespace that the
# outer container forbids. Grant network only: Codex retains its OS-enforced
# read-only filesystem while avoiding that unsupported namespace operation.
if [[ $read_only == true ]]; then
    args+=(-- -c 'sandbox_permissions=["disk-full-read-access","network-full-access"]')
fi

if oneharness "${args[@]:0:1}" --config "$llmlint_config" "${args[@]:1}"; then
    exit 0
else
    status=$?
    echo "llmlint oneharness wrapper: oneharness failed (exit $status); inspect the error above, run 'oneharness doctor', and retry" >&2
    exit "$status"
fi
