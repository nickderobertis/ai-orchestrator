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

# A caller that streams owns its own stdout shape, and the retry below buffers, so
# a streamed turn passes straight through. Nothing in this repository streams the
# orchestrator side today; this is what keeps that true rather than assumed.
for arg in "$@"; do
    if [ "$arg" = "--stream" ]; then
        exec oneharness run --config "$orchestrator_config" "$@"
    fi
done

# Why this role retries at all: the request that follows a round is made with the
# quota that round just spent, and one refusal used to kill the process that owned
# the whole run. Two runs were orphaned in one night that way. Bounded, and only
# where it is safe: an attempt that produced ANY stdout has already answered
# onejudge — which parses this process's stdout as exactly one document — so it is
# never asked again. Only an attempt that produced nothing is, which is precisely
# the launch-path death this exists for. The defaults are
# `orchestrator.boundary.DEFAULT_ATTEMPTS` / `DEFAULT_BACKOFF_SECONDS`.
boundary_attempts=${ORCHESTRATOR_BOUNDARY_ATTEMPTS:-3}
boundary_backoff=${ORCHESTRATOR_BOUNDARY_BACKOFF_SECONDS:-5}
case "$boundary_attempts" in
    '' | *[!0-9]*) boundary_attempts=3 ;;
esac
[ "$boundary_attempts" -ge 1 ] 2>/dev/null || boundary_attempts=3
case "$boundary_backoff" in
    '' | *[!0-9.]* | *.*.*) boundary_backoff=5 ;;
esac

if ! captured_stdout=$(mktemp "${TMPDIR:-/tmp}/oneharness-orchestrator.XXXXXX"); then
    echo "oneharness-orchestrator: cannot create the stdout buffer the boundary retry needs; retrying is disabled for this turn" >&2
    exec oneharness run --config "$orchestrator_config" "$@"
fi
trap 'rm -f "$captured_stdout"' EXIT

# One fixed line per retried attempt, so nothing a provider printed can be smuggled
# into the record the next round folds into `events.jsonl`. The classified reason
# lives on stderr, which passes through untouched above.
record_boundary_attempt() {
    [ -n "${ORCHESTRATOR_BOUNDARY_ATTEMPTS_LOG-}" ] || return 0
    printf '{"at":%s,"attempt":%s,"attempts":%s,"reason":"the orchestrator turn exited %s producing no output","role":"orchestrator"}\n' \
        "$(date +%s)" "$1" "$boundary_attempts" "$2" >>"$ORCHESTRATOR_BOUNDARY_ATTEMPTS_LOG" || true
}

boundary_attempt=1
boundary_delay=$boundary_backoff
while :; do
    set +e
    oneharness run --config "$orchestrator_config" "$@" >"$captured_stdout"
    boundary_status=$?
    set -e
    if [ "$boundary_status" -eq 0 ] || [ -s "$captured_stdout" ] ||
        [ "$boundary_attempt" -ge "$boundary_attempts" ]; then
        break
    fi
    record_boundary_attempt "$boundary_attempt" "$boundary_status"
    echo "oneharness-orchestrator: the orchestrator turn exited $boundary_status producing no output; retrying in ${boundary_delay}s (attempt $((boundary_attempt + 1)) of $boundary_attempts)" >&2
    sleep "$boundary_delay"
    boundary_attempt=$((boundary_attempt + 1))
    boundary_delay=$(awk -v d="$boundary_delay" 'BEGIN { v = d * 2; if (v > 120) v = 120; printf "%g", v }')
done
cat "$captured_stdout"
exit "$boundary_status"
