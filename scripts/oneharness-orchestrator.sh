#!/usr/bin/env bash
# llmlint: ignore-file[changed_behavior_has_e2e] subprocess tests drive every wrapper branch, at the same seam scripts/oneharness-agent.sh declares.
# Force the orchestrator's own agent config, and make its process self-sufficient.
#
# `launch_orchestrator` pins this wrapper as the launched onejudge process's
# oneharness binary. Without it that process resolves `oneharness.toml` by upward
# discovery from the repo root — the worker chain, which puts this supervisory
# role in front of workers for the alternate Claude subscriptions — and dies before
# its first turn because nothing exported the alternate config directories that the
# claude-code variants' `env_from` indirections name.
#
# The orchestrator's judge side is the planner channel (a command provider), so
# only agent turns reach here; a caller that already chose a `--config` is still
# passed through untouched, because oneharness rejects a duplicate `--config`.
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(dirname -- "$script_dir")
alt_config_helper="$script_dir/claude-alt-config-dir.sh"
if [ ! -f "$alt_config_helper" ] || [ ! -r "$alt_config_helper" ]; then
    echo "oneharness-orchestrator: required helper is not a readable regular file: $alt_config_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
# shellcheck source=scripts/claude-alt-config-dir.sh
. "$alt_config_helper"
resolve_claude_alt_config_dir oneharness-orchestrator || exit $?
# This chain's middle candidate is a second Codex identity, whose variant maps this
# portable value into CODEX_HOME; oneharness refuses to run when the indirection is
# unset, so it must be exported even on a host that never authenticated one.
codex_alt_helper="$script_dir/codex-alt-home.sh"
if [ ! -f "$codex_alt_helper" ] || [ ! -r "$codex_alt_helper" ]; then
    echo "oneharness-orchestrator: required helper is not a readable regular file: $codex_alt_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
# shellcheck source=scripts/codex-alt-home.sh
. "$codex_alt_helper"
ensure_codex_alt_home oneharness-orchestrator || exit $?
orchestrator_config="$repo_root/oneharness.orchestrator.toml"

if [ "${1-}" != "run" ]; then
    echo "oneharness-orchestrator: expected the 'run' subcommand; invoke through 'just orchestrate' or retry as 'scripts/oneharness-orchestrator.sh run ...'" >&2
    exit 2
fi
shift

# llmlint: ignore[boundary_inputs_validated] onejudge is the only caller; this
# scan just detects whether it already selected a config, which oneharness itself
# then validates.
for arg in "$@"; do
    case "$arg" in
        --config | --config=*)
            exec oneharness run "$@"
            ;;
    esac
done

if [ ! -f "$orchestrator_config" ] || [ ! -r "$orchestrator_config" ]; then
    echo "oneharness-orchestrator: required orchestrator config is not a readable regular file: $orchestrator_config; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi

exec oneharness run --config "$orchestrator_config" "$@"
