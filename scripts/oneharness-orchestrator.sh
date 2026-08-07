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
# the launch-path death this exists for.
#
# The four policy numbers below are `orchestrator/boundary.py`'s — DEFAULT_ATTEMPTS,
# DEFAULT_BACKOFF_SECONDS, BACKOFF_FACTOR, MAX_BACKOFF_SECONDS — restated here
# because this runs before any interpreter and cannot import them. They are held to
# that one source by a drift gate rather than by hope:
# `tests/test_oneharness_orchestrator_wrapper.py` reads both sides and fails when
# they disagree.
BOUNDARY_DEFAULT_ATTEMPTS=3
BOUNDARY_DEFAULT_BACKOFF_SECONDS=5
BOUNDARY_BACKOFF_FACTOR=2
BOUNDARY_MAX_BACKOFF_SECONDS=120
BOUNDARY_MAX_ATTEMPTS=10

# Every configured value is validated before it can reach a `sleep` or a loop bound.
# Both arrive from the environment, so `.`, `-1`, `1e9`, and an empty string all
# reach here, and each one silently disables or hangs the recovery it configures.
# An unusable value falls back to the default rather than failing the turn — this is
# how hard a *recovery* tries, and refusing to recover over a malformed number would
# be the worse error.
positive_number() {
    case "$1" in
        '' | *[!0-9.]* | *.*.* | .) return 1 ;;
    esac
    awk -v v="$1" 'BEGIN { exit !(v > 0) }'
}
boundary_attempts=${ORCHESTRATOR_BOUNDARY_ATTEMPTS:-$BOUNDARY_DEFAULT_ATTEMPTS}
boundary_backoff=${ORCHESTRATOR_BOUNDARY_BACKOFF_SECONDS:-$BOUNDARY_DEFAULT_BACKOFF_SECONDS}
case "$boundary_attempts" in
    '' | *[!0-9]*) boundary_attempts=$BOUNDARY_DEFAULT_ATTEMPTS ;;
esac
[ "$boundary_attempts" -ge 1 ] 2>/dev/null || boundary_attempts=$BOUNDARY_DEFAULT_ATTEMPTS
# "Bounded" has to stay a bound: a configured 10_000 is not a more patient policy,
# it is a run that never reports the outage it is riding out.
[ "$boundary_attempts" -le "$BOUNDARY_MAX_ATTEMPTS" ] || boundary_attempts=$BOUNDARY_MAX_ATTEMPTS
positive_number "$boundary_backoff" || boundary_backoff=$BOUNDARY_DEFAULT_BACKOFF_SECONDS

if ! captured_stdout=$(mktemp "${TMPDIR:-/tmp}/oneharness-orchestrator.XXXXXX"); then
    echo "oneharness-orchestrator: cannot create the stdout buffer the boundary retry needs under ${TMPDIR:-/tmp}; free space there or point TMPDIR at a writable directory to restore post-round retries, then retry. This turn runs unbuffered and is not retried." >&2
    exec oneharness run --config "$orchestrator_config" "$@"
fi
# `rm -f` succeeds for an already-absent path, so the only failures left are a
# directory this process can no longer write. Say so rather than leaving a buffer
# behind silently; it never changes the turn's own fate.
# shellcheck disable=SC2064  # the path is fixed here on purpose, not at trap time
trap 'rm -f "$captured_stdout" || echo "oneharness-orchestrator: could not remove the stdout buffer $captured_stdout; delete it by hand and check the permissions on ${TMPDIR:-/tmp}" >&2' EXIT

# One fixed line per retried attempt, so nothing a provider printed can be smuggled
# into the record the next round folds into `events.jsonl`. The classified reason
# lives on stderr, which passes through untouched above.
record_boundary_attempt() {
    [ -n "${ORCHESTRATOR_BOUNDARY_ATTEMPTS_LOG-}" ] || return 0
    # The launch creates this directory, but the exported path is the contract and
    # this is what makes it one: a caller that named a log somewhere else gets the
    # record rather than a silent nothing the next round cannot fold.
    mkdir -p "$(dirname -- "$ORCHESTRATOR_BOUNDARY_ATTEMPTS_LOG")" 2>/dev/null || true
    if ! printf '{"at":%s,"attempt":%s,"attempts":%s,"reason":"the orchestrator turn exited %s producing no output","role":"orchestrator"}\n' \
        "$(date +%s)" "$1" "$boundary_attempts" "$2" >>"$ORCHESTRATOR_BOUNDARY_ATTEMPTS_LOG"; then
        # Recorded on the notice below instead, which is printed only if the whole
        # request ultimately fails. Losing the record must not cost the recovery.
        boundary_notices="${boundary_notices}oneharness-orchestrator: could not append the retried attempt to $ORCHESTRATOR_BOUNDARY_ATTEMPTS_LOG ($(: >>"$ORCHESTRATOR_BOUNDARY_ATTEMPTS_LOG" 2>&1 || true)); the next round cannot fold it into the run journal — check that its directory exists and is writable
"
    fi
}

# The retry notices are collected rather than printed as they happen, and released
# only if the request ultimately fails. A turn that was refused once and then
# answered is a recovery that worked, and a wrapper that narrated it would put two
# alarming lines on a planner's terminal for an outcome nothing needs to act on. The
# attempts log above is the durable record either way.
boundary_notices=''
boundary_attempt=1
boundary_delay=$(awk -v d="$boundary_backoff" -v m="$BOUNDARY_MAX_BACKOFF_SECONDS" \
    'BEGIN { printf "%g", (d > m ? m : d) }')
while :; do
    # Checked before the turn, and separately from it: a buffer that cannot be
    # written makes every attempt look like a provider that answered nothing, which
    # would spend the whole retry budget on a full disk and report a quota outage.
    if ! : >"$captured_stdout"; then
        echo "oneharness-orchestrator: cannot write the stdout buffer $captured_stdout; free space on ${TMPDIR:-/tmp} or point TMPDIR at a writable directory, then rerun this turn" >&2
        exit 2
    fi
    set +e
    oneharness run --config "$orchestrator_config" "$@" >"$captured_stdout"
    boundary_status=$?
    set -e
    if [ "$boundary_status" -eq 0 ] || [ -s "$captured_stdout" ] ||
        [ "$boundary_attempt" -ge "$boundary_attempts" ]; then
        break
    fi
    record_boundary_attempt "$boundary_attempt" "$boundary_status"
    boundary_notices="${boundary_notices}oneharness-orchestrator: the orchestrator turn exited $boundary_status producing no output; retried in ${boundary_delay}s (attempt $((boundary_attempt + 1)) of $boundary_attempts). If every attempt below also failed, probe the launch path with 'just smoke' and read the provider-health block in 'just status' before relaunching this run.
"
    sleep "$boundary_delay"
    boundary_attempt=$((boundary_attempt + 1))
    boundary_delay=$(awk -v d="$boundary_delay" -v f="$BOUNDARY_BACKOFF_FACTOR" \
        -v m="$BOUNDARY_MAX_BACKOFF_SECONDS" 'BEGIN { v = d * f; printf "%g", (v > m ? m : v) }')
done
if [ "$boundary_status" -ne 0 ] && [ ! -s "$captured_stdout" ]; then
    # Said whether or not anything was retried: a single-attempt policy reaches here
    # with no notices collected, and an empty failed turn that says nothing is the
    # silence this whole path exists to end.
    printf '%s' "$boundary_notices" >&2
    echo "oneharness-orchestrator: the orchestrator turn exited $boundary_status producing no output after $boundary_attempt attempt(s) of $boundary_attempts; probe the launch path with 'just smoke' and read the provider-health block in 'just status', then adopt the run with 'just orchestrate --adopt <run-id>' once it is usable" >&2
fi
# Explicitly, rather than leaving it to strict mode: this is the answer onejudge
# parses, and a buffer that cannot be replayed has to say so rather than exit with
# the turn's own status and look like the turn itself failed.
if ! cat "$captured_stdout"; then
    echo "oneharness-orchestrator: could not replay the buffered turn from $captured_stdout; the turn ran but its answer is lost — rerun it" >&2
    exit 2
fi
exit "$boundary_status"
