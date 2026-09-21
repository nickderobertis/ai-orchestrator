#!/usr/bin/env bash
# `just smoke` — spend real harness turns proving the launch path works.
#
# Two turns, both through plain `oneharness` under this repository's agent config:
#
#   1. `oneagentgraph smoke`, which runs `oneharness run` in a throwaway directory and
#      judges the history record it wrote. `ONEHARNESS_CONFIG` names `oneharness.toml`
#      as the user-level config, which is what puts this repository's six-identity chain
#      under that turn rather than whatever the host's own config names; the history
#      store is one nothing else writes to, because the smoke judges the record it just
#      wrote, stamped with this tier's own labels.
#   2. The trust probe: claude-code driven as a dispatch drives it — `-p`, bypass mode,
#      a dispatch identity's config directory — in a fresh directory nothing has
#      trusted, whose `.claude/settings.json` names a `SessionStart` hook writing a
#      marker. It fails when no claude-code candidate ran, and when the marker is
#      absent after the turn: that hook running in an untrusted directory is the
#      property that lets nothing here mark workspaces trusted (docs/host-setup.md,
#      step 5).
#
# Both need every indirection the configs' `env_from` names: the identities, which
# `scripts/dispatch-env.sh` establishes exactly as a launch does, and the node scratch
# directory a turn's runtime directory is mapped from, which the engine hands a dispatch
# and `scripts/node-scratch-dir.sh` makes — under this run's own root — for a smoke run
# outside one. Extra arguments reach `oneagentgraph smoke` (`--dir` chooses where to
# spend its turn). Everything this script creates is removed on the way out.
set -euo pipefail

script_dir="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
repo_root="$(dirname -- "$script_dir")"

fail() {
    echo "smoke: $*" >&2
    exit 1
}

# shellcheck source=scripts/dispatch-env.sh
. "$script_dir/dispatch-env.sh" \
    || fail "cannot load $script_dir/dispatch-env.sh; restore it from the repository, then retry"
export_dispatch_environment smoke

case "${TMPDIR:-/tmp}" in
    /*) ;;
    *) fail "TMPDIR '${TMPDIR}' is relative, and every directory this smoke makes under it is read from another working directory; set TMPDIR to an absolute path, then retry" ;;
esac
smoke_root="$(mktemp -d "${TMPDIR:-/tmp}/orchestrator-smoke-XXXXXXXX")" \
    || fail "cannot create a scratch directory under ${TMPDIR:-/tmp}; make it writable or point TMPDIR at a writable absolute directory, then retry"
# Cleanup is not the verdict: a scratch directory that will not go is reported with what
# to do about it, and the smoke's own exit status stands.
trap 'rm -rf -- "$smoke_root" 2>/dev/null || echo "smoke: could not remove $smoke_root; fix its permissions and remove it by hand" >&2' EXIT
history_dir="$smoke_root/history"
# llmlint: ignore[changed_behavior_has_e2e] Reachable only when the directory this run created one line above stops being writable before the next; no journey can produce that without racing the filesystem it runs on. The unwritable-TMPDIR journey drives the `mktemp` arm above.
mkdir -p -- "$history_dir" \
    || fail "cannot create the history store $history_dir; free space or fix permissions under ${TMPDIR:-/tmp}, then retry"
# shellcheck source=scripts/node-scratch-dir.sh
. "$script_dir/node-scratch-dir.sh" \
    || fail "cannot load $script_dir/node-scratch-dir.sh; restore it from the repository, then retry"
TMPDIR="$smoke_root" ensure_node_scratch_dir smoke
export ONEHARNESS_CONFIG="$repo_root/oneharness.toml"
export ONEHARNESS_HISTORY_DIR="$history_dir"
export ONEHARNESS_HISTORY_LABELS="role=smoke,smoke=${smoke_root##*-}"

uv run oneagentgraph smoke "$@" \
    || fail "the agent chain's turn did not pass (oneagentgraph's verdict is above); restore the identity or harness it names, then retry"

# The trust probe's directory: fresh, a git repository as a checkout is, and carrying the
# hook. `scripts/smoke-probe.py` writes the settings and names the claude-code identities
# in the agent chain's own order, read from `oneharness.toml` — naming only those is
# what keeps a codex candidate from answering for the probe.
probe_dir="$smoke_root/untrusted"
marker="$smoke_root/session-start.marker"
report="$smoke_root/trust-probe.json"
# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this run's own scratch directory, made and written to above, stops being writable mid-run, or git stops running between the smoke's two turns; no journey can produce either without racing the filesystem or the toolchain it runs on.
mkdir -p -- "$probe_dir/.claude" \
    || fail "cannot create the trust probe's directory $probe_dir; free space or fix permissions under ${TMPDIR:-/tmp}, then retry"
git -C "$probe_dir" init --quiet \
    || fail "git could not initialise the trust probe's directory $probe_dir; check that git runs here, then retry"
claude_candidates="$(uv run python "$script_dir/smoke-probe.py" prepare "$ONEHARNESS_CONFIG" \
    "$probe_dir/.claude/settings.json" "$marker")" \
    || fail "could not prepare the trust probe (its own message is above); correct it, then retry"

probe_status=0
uv run oneharness run --config "$ONEHARNESS_CONFIG" --harness "$claude_candidates" \
    --cwd "$probe_dir" --mode bypass --format json --compact \
    --prompt "Reply with the single word: ok" >"$report" || probe_status=$?
ran="$(uv run python "$script_dir/smoke-probe.py" ran "$report")" || {
    cat -- "$report" >&2
    fail "the trust probe's report (above) is not a oneharness report naming which candidate ran (oneharness exited $probe_status); rerun the smoke, and if it repeats, run the probe's oneharness command by hand"
}
case ",$claude_candidates," in
    *",$ran,"*) ;;
    *)
        cat -- "$report" >&2
        fail "trust probe failed: no claude-code candidate ran (oneharness exited $probe_status; ran: ${ran:-none}), so nothing was proven about an untrusted directory; log in or wait out a claude-code identity's quota, then retry"
        ;;
esac
if [ "$probe_status" -ne 0 ]; then
    cat -- "$report" >&2
    fail "trust probe failed: the $ran turn exited $probe_status; read its report above, then retry"
fi
if [ ! -e "$marker" ]; then
    fail "trust probe failed: $ran ran in untrusted $probe_dir under bypass mode and its SessionStart hook never wrote $marker, so a dispatch's own session setup would not run in a fresh worktree; pin claude-code back to a release that runs it (docs/host-setup.md, step 5, records the measurement) before dispatching"
fi
echo "smoke: trust probe passed: $ran ran the SessionStart hook in an untrusted directory (marker $marker written)" >&2
