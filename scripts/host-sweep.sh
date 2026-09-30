#!/usr/bin/env bash
# llmlint: ignore-file[new_code_lands_in_a_project] The root `project.json`, the `workspace` project, is the nearest project definition covering `scripts/`, as it is for every sibling script here, and `tests/host_sweep/project.json` names this file as an input; the rule is misapplied, and a shebang leaves no line for a narrower directive.
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
#   holder     who holds it now: `pid` and `log` (`-` for a sweep run by
#              hand, whose output is on its caller's terminal);
#   completed  the last sweep that ran for real: `status` (`done` or `failed`), `exit`,
#              `finished` (epoch seconds, the stamp the interval is read against) and
#              `log`. A `--dry-run` neither reads nor writes it;
#   job        the `token` `--detach` hands the job it starts, which is how that job and
#              the recipe it runs know the lock on descriptor 9 was handed to them. The
#              job removes it once it has recorded its ending, so one a start finds with
#              the lock free is a job that never ran or never finished;
#   sweep.log  the running or last job's output, and `sweep.failed.log` a failed job's
#              once the job after it has started.

#: How long after a sweep completes a session start leaves the host unswept. The one
#: place the interval is declared; `tests/host_sweep/test_host_sweep_e2e.py` reads it
#: from here.
readonly HOST_SWEEP_INTERVAL_SECONDS=3600
#: How far ahead of a start's clock a completion stamp may be and still be the sweep that
#: just ran: a clock corrected by stepping (NTP, or a VM agent setting the guest's time
#: every few seconds) can move back past the second a job's stamp was written in.
readonly HOST_SWEEP_CLOCK_STEP_SECONDS=60
#: The status `flock -E` gives contention, so no other failure of `flock` reads as a
#: holder: sysexits' EX_TEMPFAIL, which says "held, try later".
readonly HOST_SWEEP_CONTENDED=75

host_sweep_log() { printf 'host-sweep: %s\n' "$*" >&2; }

# The operating system's own reason, off the end of the error line `$1` a failed command
# wrote (`mkdir: cannot create directory 'x': Not a directory` is `Not a directory`), so a
# refusal says why rather than guessing.
host_sweep_cause() { printf '%s' "${1##*: }"; }

# The state directory, printed; fails, saying why, when there is none to use. A relative
# `XDG_CACHE_HOME` is refused rather than ignored, as `scripts/repos-bootstrap.sh`
# refuses it, because a lock relative to whichever directory the caller stood in is one
# no other caller would find.
host_sweep_dir() {
    if [[ -z ${HOME:-} || $HOME != /* ]]; then
        host_sweep_log "HOME must name an absolute directory; export it as one, then start a session again"
        return 2
    fi
    if [[ -n ${XDG_CACHE_HOME:-} && $XDG_CACHE_HOME != /* ]]; then
        host_sweep_log "XDG_CACHE_HOME must be an absolute path, not '$XDG_CACHE_HOME'; export an absolute one or unset it, then start a session again"
        return 2
    fi
    # The holder and completion records are one `key value` per line, so a path carrying a
    # newline would write a record that reads back as other keys.
    local dir="${XDG_CACHE_HOME:-$HOME/.cache}/ai-orchestrator/sweep"
    if [[ $dir == *$'\n'* ]]; then
        host_sweep_log "XDG_CACHE_HOME must not contain a newline, nor HOME when it stands in; export a single-line path, then start a session again"
        return 2
    fi
    local failure
    if ! failure=$(mkdir -p -- "$dir" 2>&1); then
        host_sweep_log "cannot create $dir: $(host_sweep_cause "$failure"); repair the path its parent names"
        return 2
    fi
    printf '%s\n' "$dir"
}

# Whether `$2` is a value this script writes under the key `$1` in the state directory
# `$3`. The files are read back into reports and decisions, so a field in any other shape
# is read as absent, and a log is only ever this directory's own or a hand-run's `-`.
host_sweep_valid() {
    case $1 in
        pid) [[ $2 =~ ^[1-9][0-9]*$ ]] ;;
        status) [[ $2 =~ ^(done|failed)$ ]] ;;
        # Without a leading zero, so shell arithmetic reads them as the decimals they are
        # and cannot overflow on them; an exit only up to 255, all a process can return.
        exit) [[ $2 =~ ^(0|[1-9][0-9]?|1[0-9][0-9]|2[0-4][0-9]|25[0-5])$ ]] ;;
        finished) [[ $2 =~ ^(0|[1-9][0-9]{0,11})$ ]] ;;
        log) [[ $2 == - || $2 == "$3/sweep.log" ]] ;;
        token) [[ $2 =~ ^[1-9][0-9]*-[0-9]+-[0-9]+$ ]] ;;
        *) return 1 ;;
    esac
}

# Splits the record line `$1` into its caller's `key` and `value` at its first space and
# nowhere else, so a line a sweep never writes — padded, tab-separated, or with no value —
# keeps the stray whitespace that makes its key or value fail `host_sweep_valid`.
host_sweep_split() {
    key=${1%% *}
    value=""
    [[ $1 != *" "* ]] || value=${1#* }
}

# The whole of the record `$1` into the caller's `record`, or a failure when it is not a
# file this user can read or reading it failed: taken through `cat`, whose status tells a
# read error from the end of the file, which `read` in a loop over the file cannot. The
# `x` keeps the trailing newlines command substitution would strip.
host_sweep_read() {
    [[ -f $1 && -r $1 ]] || return 1
    record=$(cat -- "$1" 2>/dev/null && printf x) || return 1
    record=${record%x}
}

# One field of the file `$1`, or nothing; read line by line, never sourced. Fails for a
# record that is there and cannot be read, which is not an absent one: an unreadable
# completion read as none would start a sweep every session.
host_sweep_field() {
    local line key value record
    [[ -e $1 ]] || return 0
    host_sweep_read "$1" || return 1
    # `|| [[ -n $line ]]` reads a last line that has no newline, which `read` alone drops.
    while IFS= read -r line || [[ -n $line ]]; do
        host_sweep_split "$line"
        if [[ $key == "$2" ]]; then
            host_sweep_valid "$key" "$value" "${1%/*}" && printf '%s\n' "$value"
            return 0
        fi
    done < <(printf '%s' "$record")
}

# What makes the record `$1` one no sweep wrote, printed, or a failure when nothing does:
# a key other than the ones its writer writes (`$2` onward), one twice or missing, or a
# value in a shape it never writes, or a record that is there and cannot be read. The
# caller names the record and the fault rather than trusting any field of it.
host_sweep_fault() {
    local file=$1 line key value seen=" " expected record
    shift
    expected=" $* "
    [[ -e $file ]] || return 1
    if ! host_sweep_read "$file"; then
        printf 'it cannot be read'
        return 0
    fi
    while IFS= read -r line || [[ -n $line ]]; do
        host_sweep_split "$line"
        if [[ $expected != *" $key "* ]]; then
            printf "it carries '%s', a key no sweep writes there" "$key"
            return 0
        fi
        if [[ $seen == *" $key "* ]]; then
            printf "it carries '%s' twice" "$key"
            return 0
        fi
        seen+="$key "
        if ! host_sweep_valid "$key" "$value" "${file%/*}"; then
            printf "its '%s' is not a value a sweep writes" "$key"
            return 0
        fi
    done < <(printf '%s' "$record")
    for key in "$@"; do
        if [[ $seen != *" $key "* ]]; then
            printf "it lacks '%s'" "$key"
            return 0
        fi
    done
    return 1
}

# Replaced whole through a rename, so a reader never meets half a file. A failure leaves
# the operating system's reason in `host_sweep_failure`, which each caller's line carries
# beside its remedy.
host_sweep_failure=""
host_sweep_write() {
    local file=$1 failure
    shift
    if ! failure=$({ printf '%s\n' "$@" >"$file.new" && mv -f "$file.new" "$file"; } 2>&1); then
        host_sweep_failure=$(host_sweep_cause "$failure")
        return 1
    fi
}

host_sweep_holder() {
    local pid log fault
    if ! pid=$(host_sweep_field "$1/holder" pid) || ! log=$(host_sweep_field "$1/holder" log); then
        printf 'a holder whose record %s cannot be read' "$1/holder"
    elif fault=$(host_sweep_fault "$1/holder" pid log); then
        printf 'a holder whose record %s no sweep wrote: %s' "$1/holder" "$fault"
    elif [[ -z $pid ]]; then
        printf 'a holder that has not yet recorded itself'
    else
        printf 'pid %s (log: %s)' "$pid" "$(host_sweep_where "$log")"
    fi
}

# The time in epoch seconds, from bash itself rather than an external `date`, so every
# stamp this script writes and compares is one it can always read.
host_sweep_now() { printf '%(%s)T\n' -1; }

host_sweep_where() {
    case $1 in
        -) printf "its caller's terminal, run by hand" ;;
        "") printf 'unrecorded' ;;
        *) printf '%s' "$1" ;;
    esac
}

# Takes the lock in the state directory `$1`, open on descriptor 9, without waiting:
# 0 taken, 1 another holder has it, 2 `flock` failed some other way, which is said here
# and never read as a holder. `-E` gives contention a status of its own, so every other
# non-zero one is a failure.
host_sweep_lock() {
    local status=0
    flock -n -E "$HOST_SWEEP_CONTENDED" 9 || status=$?
    case $status in
        0) return 0 ;;
        "$HOST_SWEEP_CONTENDED") return 1 ;;
    esac
    # llmlint: ignore-block[changed_behavior_has_e2e] Only a `flock` that fails without contention reaches here, and no journey can produce one without doubling `flock`, which this repository's journeys never double; contention and success are both driven for real.
    host_sweep_log "flock failed on $1/lock with exit $status, which is not another sweep holding it; nothing swept. Repair util-linux's flock on this host, then run this again."
    return 2
    # llmlint: ignore-end[changed_behavior_has_e2e]
}

# Whether this process is the job `--detach` started in the state directory `$1`: it
# carries the token that start recorded, and descriptor 9 is open on that lock and held.
# A descriptor alone is not enough, since any caller can open one on the lock file.
# Fails 1 when it is not, and 2 when `flock` itself failed.
host_sweep_is_job() {
    local recorded
    [[ -n ${HOST_SWEEP_JOB_TOKEN:-} ]] || return 1
    ! host_sweep_fault "$1/job" token >/dev/null || return 1
    recorded=$(host_sweep_field "$1/job" token) || return 1
    [[ $HOST_SWEEP_JOB_TOKEN == "$recorded" ]] || return 1
    { true >&9; } 2>/dev/null && [[ /dev/fd/9 -ef $1/lock ]] || return 1
    host_sweep_lock "$1"
}

# The recipe's half: take the host lock on descriptor 9, or say who has it and fail. A
# lock handed to the job `--detach` started, with its token, is the one `--job` hands the
# recipe it runs; any other caller opens the lock file itself and records itself as the
# holder.
host_sweep_hold() {
    HOST_SWEEP_DIR=$(host_sweep_dir) || return 2
    HOST_SWEEP_UNDER_JOB=0
    HOST_SWEEP_DRY_RUN=0
    local argument job=0 held=0
    # Any spelling of the flag, a malformed one included, is a rehearsal: the verbs judge
    # the arguments, and one they refuse must not leave a stamp holding sweeps off.
    for argument in "$@"; do
        [[ $argument != --dry-run* ]] || HOST_SWEEP_DRY_RUN=1
    done
    host_sweep_is_job "$HOST_SWEEP_DIR" || job=$?
    case $job in
        0) HOST_SWEEP_UNDER_JOB=1; return 0 ;;
        1) ;;
        *) return 2 ;;
    esac
    if ! exec 9>>"$HOST_SWEEP_DIR/lock"; then
        host_sweep_log "cannot open the host sweep lock $HOST_SWEEP_DIR/lock; make it a file this user can write, then run this again"
        return 2
    fi
    host_sweep_lock "$HOST_SWEEP_DIR" || held=$?
    case $held in
        0) ;;
        1)
            host_sweep_log "another sweep holds the host lock, $(host_sweep_holder "$HOST_SWEEP_DIR"); nothing swept. Read its log, or run this again once it ends."
            return 1
            ;;
        *) return 2 ;;
    esac
    host_sweep_write "$HOST_SWEEP_DIR/holder" "pid $$" "log -" \
        || host_sweep_log "holder not recorded in $HOST_SWEEP_DIR ($host_sweep_failure), so a sweep refused beside this one cannot name it; make it writable by this user"
}

# The recipe's other half: a sweep run by hand for real is the stamp the next session
# start reads. A job records its own ending, and a dry run records nothing.
host_sweep_record() {
    [[ $HOST_SWEEP_UNDER_JOB -eq 0 && $HOST_SWEEP_DRY_RUN -eq 0 ]] || return 0
    local status="done"
    [[ $1 -eq 0 ]] || status=failed
    host_sweep_write "$HOST_SWEEP_DIR/completed" "status $status" "exit $1" \
        "finished $(host_sweep_now)" "log -" \
        || host_sweep_log "completion not recorded in $HOST_SWEEP_DIR ($host_sweep_failure), so the next session start sweeps again; make it writable by this user"
}

if [[ ${BASH_SOURCE[0]} != "$0" ]]; then
    return 0
fi

set -euo pipefail
# llmlint: ignore[robust_shell, tool_output_is_signal] A directory this script was just read from that `cd` cannot enter is a broken host; `cd` names the path and the reason in its own error, which is the whole repair, and `set -e` ends the start there before anything is launched.
script_dir="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(dirname -- "$script_dir")"

usage() {
    echo "host-sweep: refused arguments: $*" >&2
    echo "usage: host-sweep.sh --detach    (run by session setup; 'just sweep' is the sweep itself)" >&2
}

# The job is the recipe, so a host without `just` is refused before anything starts; a
# job that cannot be launched — `setsid` missing or failing — leaves its `job` record
# behind, and the next start names it with its log; and `flock`'s one failure other than
# contention is reported by `host_sweep_lock`.
if ! command -v just >/dev/null 2>&1; then
    host_sweep_log "'just' is not on PATH, so no sweep can be started; install it or put it on PATH, then start a session again"
    exit 2
fi
if [[ $# -ne 1 ]]; then
    usage "$@"
    exit 2
fi
dir=$(host_sweep_dir) || exit 2
log="$dir/sweep.log"

case ${1:-} in
    --job)
        # Only what `--detach` starts: its token, and the lock open on descriptor 9.
        job=0
        host_sweep_is_job "$dir" || job=$?
        if [[ $job -eq 1 ]]; then
            host_sweep_log "--job runs only under the lock --detach hands it; run 'host-sweep.sh --detach' to start a job, or 'just sweep' to sweep in the foreground"
        fi
        [[ $job -eq 0 ]] || exit 2
        status=0
        just --justfile "$repo_root/justfile" --working-directory "$repo_root" sweep </dev/null \
            || status=$?
        ending="done"
        if [[ $status -ne 0 ]]; then
            ending=failed
            echo "host-sweep: job $$ failed, exit $status; the output above says why, and 'just sweep' runs it again by hand"
        fi
        # The `job` record goes only once the ending is recorded, so a job that dies first
        # is named by the next start rather than lost.
        if host_sweep_write "$dir/completed" "status $ending" "exit $status" \
            "finished $(host_sweep_now)" "log $log"; then
            if ! removed=$(rm -f -- "$dir/job" 2>&1); then
                echo "host-sweep: job $$ recorded its ending but could not remove $dir/job: $(host_sweep_cause "$removed"); every session start names this log until it is gone, so remove it by hand"
            fi
        else
            echo "host-sweep: completion not recorded in $dir ($host_sweep_failure), so the next session start sweeps again; make $dir writable by this user"
        fi
        exit 0
        ;;
    --detach) ;;
    *) usage "$@"; exit 2 ;;
esac

if ! exec 9>>"$dir/lock"; then
    host_sweep_log "cannot open the host sweep lock $dir/lock; no sweep started. Make it a file this user can write."
    exit 2
fi
held=0
host_sweep_lock "$dir" || held=$?
if [[ $held -eq 1 ]]; then
    host_sweep_log "a sweep is running, $(host_sweep_holder "$dir"); none started"
    exit 0
fi
[[ $held -eq 0 ]] || exit 2

if ! previous=$(host_sweep_field "$dir/completed" status) \
    || ! previous_exit=$(host_sweep_field "$dir/completed" exit) \
    || ! previous_log=$(host_sweep_field "$dir/completed" log) \
    || ! finished=$(host_sweep_field "$dir/completed" finished); then
    host_sweep_log "cannot read the completion record $dir/completed: $(host_sweep_cause "$(cat -- "$dir/completed" 2>&1 >/dev/null)"); so whether a sweep is due is unknown, none started. Make it a file this user can read, or remove it to sweep at the next session start."
    exit 2
fi
# The record is read whole: a record no sweep wrote, a missing field, or a status its
# exit contradicts is refused by name and read as none, so a stamp is never believed
# beside a record no sweep wrote, and the start sweeps.
fault=""
if fault=$(host_sweep_fault "$dir/completed" status exit finished log); then
    :
elif [[ -e $dir/completed ]] \
    && { [[ $previous == "done" && $previous_exit != 0 ]] \
        || [[ $previous == failed && $previous_exit == 0 ]]; }; then
    fault="its status contradicts its exit"
fi
# What this start must also say is carried to the one line it ends on, so a start that
# goes ahead still prints a single line.
notes=""
if [[ -n $fault ]]; then
    notes+="; refused the completion record $dir/completed, which no sweep wrote: $fault; read as none"
    previous="" previous_exit="" previous_log="" finished=""
fi
# A `job` record with the lock free is a job an earlier start launched that never
# recorded an ending: it could not start — `setsid` missing or failing — or it was killed.
# Its output, the launch's own error included, is in the log.
unfinished=0
[[ ! -e $dir/job ]] || unfinished=1
host_sweep_say() { host_sweep_log "$*$notes"; }
now=$(host_sweep_now)
# A stamp further ahead of now than a clock step explains is one no sweep wrote, and is
# read as absent rather than holding every start off until then; one within a step is
# the sweep that just ran.
elapsed=$(( now > finished ? now - finished : 0 ))
if [[ -n $finished ]] && (( finished <= now + HOST_SWEEP_CLOCK_STEP_SECONDS \
    && elapsed < HOST_SWEEP_INTERVAL_SECONDS )); then
    due=$(( (HOST_SWEEP_INTERVAL_SECONDS - elapsed + 59) / 60 ))
    if [[ $unfinished -eq 1 ]]; then
        host_sweep_say "the last sweep job never finished cleanly: it could not start, was killed, or could not clear its record (log: $log); the next is due in ${due} min, none started; read that log — 'setsid: command not found' there means util-linux's setsid must be installed or put on PATH — and run 'just sweep' once its cause is fixed rather than waiting"
        exit 1
    fi
    if [[ $previous == failed ]]; then
        host_sweep_say "the last sweep failed: exit ${previous_exit:-unrecorded} (log: $(host_sweep_where "$previous_log")); the next is due in ${due} min, none started; read that log, and run 'just sweep' once its cause is fixed rather than waiting"
        exit 1
    fi
    host_sweep_say "the last sweep completed $(( elapsed / 60 )) min ago; the next is due in ${due} min, none started"
    exit 0
fi

# A failed or unfinished job's log is kept apart before the next job's replaces it, so
# the line naming it still names its output. One that cannot be moved is appended to instead of
# truncated, so the failure it holds is never lost.
kept=$previous_log
[[ $unfinished -eq 0 ]] || kept=$log
if [[ $kept == "$log" ]] && [[ $unfinished -eq 1 || $previous == failed ]]; then
    kept="$dir/sweep.failed.log"
    mv -f -T "$log" "$kept" 2>/dev/null || kept=$log
fi
if [[ $kept == "$log" ]]; then
    written=$({ : >>"$log"; } 2>&1) || written="failed: $written"
else
    written=$({ : >"$log"; } 2>&1) || written="failed: $written"
fi
if [[ $written == failed:* ]]; then
    host_sweep_say "cannot write the sweep log $log: $(host_sweep_cause "$written"); no sweep started. Make it a file this user can write."
    exit 2
fi
# Unguessable enough to tell this start's job from a caller that merely opened the lock.
token="$$-$RANDOM$RANDOM-$RANDOM$RANDOM"
if ! host_sweep_write "$dir/job" "token $token"; then
    host_sweep_say "cannot record the job token in $dir ($host_sweep_failure); no sweep started. Make it writable by this user."
    exit 2
fi
# `-w` keeps `$!` the job's own process whether or not `setsid` has to fork. The job
# inherits descriptor 9, so the lock stays held from here until the job exits. The start
# returns at once and never waits on it: the job records its own ending, and a launch
# that fails — whose error lands in the log — leaves the `job` record the next start
# names.
# llmlint: ignore-block[robust_shell] The launch's own failure is not lost to the background: its error goes to the log, and the `job` record it leaves makes the next session start name that log.
HOST_SWEEP_JOB_TOKEN=$token setsid -w bash "$script_dir/host-sweep.sh" --job </dev/null >>"$log" 2>&1 &
job=$!
# llmlint: ignore-end[robust_shell]
host_sweep_write "$dir/holder" "pid $job" "log $log" \
    || notes+="; holder not recorded in $dir ($host_sweep_failure), so a sweep refused beside this one cannot name it; make it writable by this user"
if [[ $unfinished -eq 1 ]]; then
    host_sweep_say "the last sweep job never finished cleanly: it could not start, was killed, or could not clear its record (log: $kept); started again as job $job (log: $log); read that log for why, since a repeat of its cause fails this job too — 'setsid: command not found' there means util-linux's setsid must be installed or put on PATH"
    exit 1
fi
if [[ $previous == failed ]]; then
    host_sweep_say "the last sweep failed: exit ${previous_exit:-unrecorded} (log: $(host_sweep_where "$kept")); started again as job $job (log: $log); read the failed log for why, since a repeat of its cause fails this job too"
    exit 1
fi
host_sweep_say "started job $job (log: $log)"
exit 0
