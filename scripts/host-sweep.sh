#!/usr/bin/env bash
# One host sweep at a time around the `just sweep` recipe: sourced by the recipe for the
# lock it takes, and run with `--detach` by session setup to start the recipe as the
# session hook's own detached job — not an engine process, which AGENTS.md's rule
# against `setsid` by hand is about. Holding one sweep of both verbs at a time is this
# repository's composition of the two, not a fill for either library. When a job starts
# and what it reports is docs/host-setup.md, "The host sweep".
#
# Everything lives under `${XDG_CACHE_HOME:-$HOME/.cache}/ai-orchestrator/sweep/`, which
# no checkout or worktree decides, so every checkout on the host shares one lock:
#   lock       the `flock` held for the whole of a sweep, on descriptor 9;
#   holder     who holds it now: `pid`, `started` and `log` (`-` for a sweep run by
#              hand, whose output is on its caller's terminal);
#   completed  the last sweep that ran for real: `status` (`done` or `failed`), `exit`,
#              `finished` (epoch seconds, the stamp the interval is read against) and
#              `log`. A `--dry-run` neither reads nor writes it;
#   sweep.log  the running or last job's output, and `sweep.failed.log` a failed job's
#              once the job after it has started.
#
# llmlint: ignore-file[tool_output_is_signal] The lines this prints are what session setup relays and an operator reads: which sweep holds the host, and where the last one's log is.

#: How long after a sweep completes a session start leaves the host unswept. The one
#: place the interval is declared; `tests/session_setup/test_host_sweep_e2e.py` reads it
#: from here.
readonly HOST_SWEEP_INTERVAL_SECONDS=3600

host_sweep_log() { printf 'host-sweep: %s\n' "$*" >&2; }

# The state directory, printed; fails, saying why, when there is none to use. A relative
# `XDG_CACHE_HOME` is refused rather than ignored, as `scripts/repos-bootstrap.sh`
# refuses it, because a lock relative to whichever directory the caller stood in is one
# no other caller would find.
host_sweep_dir() {
    if [[ -z ${HOME:-} || $HOME != /* ]]; then
        host_sweep_log "HOME must name an absolute directory"
        return 2
    fi
    if [[ -n ${XDG_CACHE_HOME:-} && $XDG_CACHE_HOME != /* ]]; then
        host_sweep_log "XDG_CACHE_HOME must be an absolute path, not '$XDG_CACHE_HOME'"
        return 2
    fi
    local dir="${XDG_CACHE_HOME:-$HOME/.cache}/ai-orchestrator/sweep"
    if ! mkdir -p "$dir" 2>/dev/null; then
        host_sweep_log "cannot create $dir; repair its parent's permissions"
        return 2
    fi
    printf '%s\n' "$dir"
}

# Whether `$2` is a value this script writes under the key `$1`. The files are read back
# into reports and decisions, so a field in any other shape is read as absent.
host_sweep_valid() {
    case $1 in
        pid) [[ $2 =~ ^[1-9][0-9]*$ ]] ;;
        status) [[ $2 =~ ^(done|failed)$ ]] ;;
        # Bounded, and without a leading zero, so shell arithmetic reads them as the
        # decimals they are and cannot overflow on them.
        exit) [[ $2 =~ ^(0|[1-9][0-9]{0,2})$ ]] ;;
        finished) [[ $2 =~ ^(0|[1-9][0-9]{0,11})$ ]] ;;
        started) [[ $2 =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] ;;
        log) [[ $2 == - || $2 == /* ]] ;;
        *) return 1 ;;
    esac
}

# One field of the file `$1`, or nothing; read line by line, never sourced.
host_sweep_field() {
    local key value
    [[ -f $1 ]] || return 0
    while read -r key value; do
        if [[ $key == "$2" ]]; then
            host_sweep_valid "$key" "$value" && printf '%s\n' "$value"
            return 0
        fi
    done <"$1" 2>/dev/null || true
}

# Replaced whole through a rename, so a reader never meets half a file.
host_sweep_write() {
    local file=$1
    shift
    printf '%s\n' "$@" >"$file.new" 2>/dev/null && mv -f "$file.new" "$file"
}

# Who holds the lock, as one phrase naming the process and its log.
host_sweep_holder() {
    local pid log
    pid=$(host_sweep_field "$1/holder" pid)
    log=$(host_sweep_field "$1/holder" log)
    if [[ -z $pid ]]; then
        printf 'a holder that has not yet recorded itself'
    else
        printf 'pid %s (log: %s)' "$pid" "$(host_sweep_where "$log")"
    fi
}

# Where a sweep's output went, as a report names it.
host_sweep_where() {
    case $1 in
        -) printf "its caller's terminal, run by hand" ;;
        "") printf 'unrecorded' ;;
        *) printf '%s' "$1" ;;
    esac
}

host_sweep_now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# The recipe's half: take the host lock on descriptor 9, or say who has it and fail. A
# lock that arrived already held on descriptor 9 is the one `--job` hands the recipe it
# runs; any other caller opens the lock file itself and records itself as the holder.
host_sweep_hold() {
    HOST_SWEEP_DIR=$(host_sweep_dir) || return 2
    HOST_SWEEP_UNDER_JOB=0
    HOST_SWEEP_DRY_RUN=0
    local argument
    for argument in "$@"; do
        [[ $argument != --dry-run ]] || HOST_SWEEP_DRY_RUN=1
    done
    if { true >&9; } 2>/dev/null && [[ /dev/fd/9 -ef $HOST_SWEEP_DIR/lock ]] && flock -n 9; then
        HOST_SWEEP_UNDER_JOB=1
        return 0
    fi
    if ! exec 9>>"$HOST_SWEEP_DIR/lock"; then
        host_sweep_log "cannot open the host sweep lock $HOST_SWEEP_DIR/lock; make it a file this user can write, then run this again"
        return 2
    fi
    if ! flock -n 9; then
        host_sweep_log "another sweep holds the host lock, $(host_sweep_holder "$HOST_SWEEP_DIR"); nothing swept. Read its log, or run this again once it ends."
        return 1
    fi
    host_sweep_write "$HOST_SWEEP_DIR/holder" "pid $$" "started $(host_sweep_now_iso)" "log -" \
        || host_sweep_log "holder not recorded in $HOST_SWEEP_DIR"
}

# The recipe's other half: a sweep run by hand for real is the stamp the next session
# start reads. A job records its own ending, and a dry run records nothing.
host_sweep_record() {
    [[ $HOST_SWEEP_UNDER_JOB -eq 0 && $HOST_SWEEP_DRY_RUN -eq 0 ]] || return 0
    local status="done"
    [[ $1 -eq 0 ]] || status=failed
    host_sweep_write "$HOST_SWEEP_DIR/completed" "status $status" "exit $1" \
        "finished $(date +%s)" "log -" \
        || host_sweep_log "completion not recorded in $HOST_SWEEP_DIR"
}

if [[ ${BASH_SOURCE[0]} != "$0" ]]; then
    return 0
fi

set -euo pipefail
script_dir="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(dirname -- "$script_dir")"

usage() {
    echo "usage: host-sweep.sh --detach    (run by session setup; 'just sweep' is the sweep itself)" >&2
}

# llmlint: ignore-block[changed_behavior_has_e2e] `flock` and `setsid` ship with util-linux beside the `bash` running this, so a journey would have to build a PATH holding `bash` and coreutils but not them to prove what `command -v` answers; the journey over a PATH without `just`, the one that plausibly goes missing, drives this refusal.
for required in flock setsid just; do
    if ! command -v "$required" >/dev/null 2>&1; then
        host_sweep_log "'$required' is not on PATH, so no sweep can be started"
        exit 2
    fi
done
# llmlint: ignore-end[changed_behavior_has_e2e]
if [[ $# -ne 1 ]]; then
    usage
    exit 2
fi
dir=$(host_sweep_dir) || exit 2
log="$dir/sweep.log"

case ${1:-} in
    --job)
        # Only what `--detach` starts: the lock has to arrive already open on descriptor 9.
        if ! { true >&9; } 2>/dev/null || [[ ! /dev/fd/9 -ef $dir/lock ]] || ! flock -n 9; then
            host_sweep_log "--job runs only under the lock --detach hands it"
            exit 2
        fi
        echo "host-sweep: job $$ started $(host_sweep_now_iso)"
        status=0
        just --justfile "$repo_root/justfile" --working-directory "$repo_root" sweep </dev/null \
            || status=$?
        ending="done"
        [[ $status -eq 0 ]] || ending=failed
        echo "host-sweep: job $$ $ending $(host_sweep_now_iso), exit $status"
        host_sweep_write "$dir/completed" "status $ending" "exit $status" \
            "finished $(date +%s)" "log $log" \
            || echo "host-sweep: completion not recorded in $dir"
        exit 0
        ;;
    --detach) ;;
    *) usage; exit 2 ;;
esac

if ! exec 9>>"$dir/lock"; then
    host_sweep_log "cannot open the host sweep lock $dir/lock; no sweep started. Make it a file this user can write."
    exit 2
fi
if ! flock -n 9; then
    host_sweep_log "a sweep is running, $(host_sweep_holder "$dir"); none started"
    exit 0
fi

previous=$(host_sweep_field "$dir/completed" status)
previous_exit=$(host_sweep_field "$dir/completed" exit)
previous_log=$(host_sweep_field "$dir/completed" log)
finished=$(host_sweep_field "$dir/completed" finished)
now=$(date +%s)
# A stamp later than now is one no sweep wrote, and is read as absent rather than
# holding every start off until then.
if [[ -n $finished ]] && (( finished <= now && now - finished < HOST_SWEEP_INTERVAL_SECONDS )); then
    due=$(( (HOST_SWEEP_INTERVAL_SECONDS - (now - finished) + 59) / 60 ))
    if [[ $previous == failed ]]; then
        host_sweep_log "the last sweep failed: exit ${previous_exit:-unrecorded} (log: $(host_sweep_where "$previous_log")); the next is due in ${due} min, none started"
        exit 1
    fi
    host_sweep_log "the last sweep completed $(( (now - finished) / 60 )) min ago; the next is due in ${due} min, none started"
    exit 0
fi

# A failed job's log is kept apart before the next job's replaces it, so the line
# naming it still names its output. One that cannot be moved is appended to instead of
# truncated, so the failure it holds is never lost.
kept=$previous_log
if [[ $previous == failed && $previous_log == "$log" ]]; then
    kept="$dir/sweep.failed.log"
    mv -f -T "$log" "$kept" 2>/dev/null || kept=$log
fi
if { [[ $kept == "$log" ]] && ! : >>"$log"; } 2>/dev/null \
    || { [[ $kept != "$log" ]] && ! : >"$log"; } 2>/dev/null; then
    host_sweep_log "cannot write the sweep log $log; no sweep started"
    exit 2
fi
# `-w` keeps `$!` the job's own process whether or not `setsid` has to fork. The job
# inherits descriptor 9, so the lock stays held from here until the job exits.
setsid -w bash "$script_dir/host-sweep.sh" --job </dev/null >>"$log" 2>&1 &
job=$!
# A job that is already gone is either one that swept in an instant, which exits 0, or
# one that never started, whose status says so and whose log holds why.
# llmlint: ignore[changed_behavior_has_e2e, robust_shell] `setsid` and `bash` are checked on PATH above and the job's lock is this caller's own descriptor, so a job refusing its start needs the host to lose a binary in the instant between, and one that does so after `kill -0` answers is still named by its log and leaves no stamp, so the next start runs a sweep again; the `--job` refusal itself is driven by a journey.
if ! kill -0 "$job" 2>/dev/null && ! wait "$job"; then
    host_sweep_log "the sweep job did not start (log: $log)"
    exit 1
fi
host_sweep_write "$dir/holder" "pid $job" "started $(host_sweep_now_iso)" "log $log" \
    || host_sweep_log "holder not recorded in $dir"
if [[ $previous == failed ]]; then
    host_sweep_log "the last sweep failed: exit ${previous_exit:-unrecorded} (log: $(host_sweep_where "$kept")); started again as job $job (log: $log)"
    exit 1
fi
host_sweep_log "started job $job (log: $log)"
exit 0
