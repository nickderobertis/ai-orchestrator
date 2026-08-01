#!/usr/bin/env bash
# llmlint: ignore-file[changed_behavior_has_e2e] subprocess tests drive the wrapper's validation and forwarding paths while replacing only its paid oneharness child.
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
    echo "llmlint oneharness wrapper: required config is not a readable regular file: $llmlint_config; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi

if (( $# == 0 )); then
    echo "llmlint oneharness wrapper: expected oneharness arguments; invoke through 'just lint-llm' or pass 'run ...'" >&2
    exit 2
fi
if (( $# == 1 )) && [[ $1 == --version ]]; then
    exec oneharness --version
fi
if [[ $1 != run ]]; then
    echo "llmlint oneharness wrapper: expected the 'run' subcommand; invoke through 'just lint-llm' or retry with 'run ...'" >&2
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
    # llmlint: ignore[least_privilege_grants] Codex exposes only coarse disk/network grants; llmlint needs repository reads and its model endpoint, with the outer container as boundary.
    args+=(-- -c 'sandbox_permissions=["disk-full-read-access","network-full-access"]')
fi

# This tier's only backup for an exhausted quota is a second Codex identity, whose
# variant maps this portable value into CODEX_HOME. oneharness refuses to run when
# the indirection is unset, so export it even on a host that never authenticated
# one; an empty home is the state that falls through rather than hard-failing.
# Resolved here, past the argument checks above, so a rejected invocation and the
# `--version` probe never touch the filesystem.
codex_alt_helper="$script_dir/codex-alt-home.sh"
if [ ! -f "$codex_alt_helper" ] || [ ! -r "$codex_alt_helper" ]; then
    echo "llmlint oneharness wrapper: required helper is not a readable regular file: $codex_alt_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
# shellcheck source=scripts/codex-alt-home.sh
. "$codex_alt_helper"
ensure_codex_alt_home "llmlint oneharness wrapper" || exit $?

if oneharness "${args[@]:0:1}" --config "$llmlint_config" "${args[@]:1}"; then
    exit 0
else
    status=$?
    echo "llmlint oneharness wrapper: oneharness failed (exit $status); inspect the error above, run 'oneharness doctor', and retry" >&2
    exit "$status"
fi
