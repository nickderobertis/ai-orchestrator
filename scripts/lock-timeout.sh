# shellcheck shell=bash
# How long a waiter queues for this host's merge-queue lock, derived from how long this
# repository's gate really takes.
#
# A `local-direct` publication runs the complete pre-push gate inside a clone it holds
# under `onevcs`'s merge-queue lock, and `onevcs` bounds a queued wait at its default
# unless `ONEVCS_LOCK_TIMEOUT_SECONDS` names one — so a gate longer than that default
# failed the sibling queued behind it (ai-orchestrator#1164).
#
# So the bound is measured rather than kept by hand: `.githooks/pre-push` records the
# gate's duration through `record_local_direct_gate`, and every process that starts a
# waiter's `onevcs` derives the bound from that recording through `export_lock_timeout`.
# One helper for every caller, because a second copy of the derivation is a waiter
# bounded differently from the publications it is queued behind.
#
# The same measurement is what the `gate-time` budget in `budgets.yaml` reads: for a
# push the hook names, `record_local_direct_gate` also records the duration against that
# push with the host conditions sampled while the gate ran, and
# `scripts/budget-gate-time.sh` reports it, so the gate is timed once and run once.
#
# Strict mode is established here rather than inherited, as the launch helpers beside it
# do: a bound that could only be half derived must abort rather than fall through to a
# waiter bounded by nothing it measured.
set -euo pipefail

#: How many of this identity's publications a waiter may be queued behind. This is the
#: stated assumption, and it is this host's own plan concurrency — eight, which is what a
#: plan of this repository is run at — so that the *last* waiter in a full queue outlasts
#: every gate ahead of it rather than only the one at the head. A host that runs a plan
#: wider than this has to raise it; a host that runs it narrower loses nothing but a
#: longer wait before a genuinely wedged lock is reported.
# llmlint: ignore[contracts_have_one_source_or_a_drift_gate] This is the one statement of the value: no file here and no flag of the pinned engine sets a plan's concurrency, so there is no second copy to derive from or gate against, and ai-orchestrator#1164's accepted fix requires it be a stated assumption beside the derivation.
LOCAL_DIRECT_GATE_QUEUE_DEPTH=8

#: Added once on top of the queue, for what the publication does around each gate — the
#: clone, the merge, the push and the landing record — which the gate's own duration does
#: not measure. Five minutes, generous on purpose: the cost of being too high is that a
#: genuinely wedged lock is reported later, and the cost of being too low is the ticket
#: this file exists for.
LOCAL_DIRECT_GATE_MARGIN_SECONDS=300

#: What `onevcs` bounds a queued wait at when nothing names one — `onevcs`'s own
#: `lock::DEFAULT_TIMEOUT_SECONDS`. The derivation never goes below it, so this helper
#: can only ever lengthen a wait, never shorten one: a host with no recording yet, or one
#: whose gate is fast, is left exactly where `onevcs` would have put it.
#: `tests/test_engine_contracts.py` holds it to the constant in both `onevcs` copies a
#: waiter here runs.
ONEVCS_DEFAULT_LOCK_TIMEOUT_SECONDS=900

#: The variable `onevcs` reads the bound out of.
ONEVCS_LOCK_TIMEOUT_VARIABLE="ONEVCS_LOCK_TIMEOUT_SECONDS"

# Where the last gate duration is kept: host-local, outside every checkout, because it is
# a fact about this machine's hardware and load rather than about any branch — a worktree
# that is reaped, or a clone a publication makes, must not lose it. `XDG_STATE_HOME` is
# honoured only when it is absolute, which is that variable's own specification and keeps
# a relative one from putting this under whatever directory a hook happened to run in.
#
# Fails, printing nothing, when neither that nor HOME is an absolute path: there is then
# no host-local place to keep it, and a relative one would be whatever directory a hook
# ran in.
local_direct_gate_state_file() {
    local root=${XDG_STATE_HOME:-}
    case $root in
        /*) ;;
        *) root="${HOME:-}/.local/state" ;;
    esac
    case $root in
        /?*) printf '%s\n' "$root/ai-orchestrator/local-direct-gate-seconds" ;;
        *) return 1 ;;
    esac
}

# Where one push's own gate is recorded, beside the duration above: the push it was
# gated for, the same duration, and the host conditions sampled while it ran.
# `scripts/budget-gate-time.sh` reads it as the `gate-time` budget's measurement, and
# refuses one recorded for any other push. Fails as `local_direct_gate_state_file` does.
local_direct_gate_push_file() {
    local file
    file=$(local_direct_gate_state_file) || return 1
    printf '%s\n' "${file%/*}/local-direct-gate-push"
}

#: How often the host is sampled while a push's gate runs, in seconds, unless
#: `ORCHESTRATOR_GATE_SAMPLE_SECONDS` names another. A sample of the dispatch count costs
#: one read of `just host`, a few seconds of CPU on a host holding many run roots, so it
#: is taken a minute apart: a gate of this repository runs for tens of minutes, and what
#: the manager judges from the samples is how busy the host stayed.
LOCAL_DIRECT_GATE_SAMPLE_SECONDS=60

# Sample the host into directory $1 until stopped: each running-dispatch count, from the
# same `scripts/budget-dispatches.sh` the root budgets file's `dispatches` condition runs,
# appended to `$1/dispatches`, and each 1-minute load average, from
# `ORCHESTRATOR_GATE_LOADAVG` (`/proc/loadavg` by default), to `$1/load1`. A reading
# that fails is skipped rather than written, so a quantity no sample could read is one
# whose file stays empty. Two loops, so a slow view never delays the load samples; their
# pids are kept in `GATE_SAMPLER_PIDS`, in this shell, for `stop_gate_sampling`.
GATE_SAMPLER_PIDS=()
start_gate_sampling() {
    local samples=$1 every=${ORCHESTRATOR_GATE_SAMPLE_SECONDS:-$LOCAL_DIRECT_GATE_SAMPLE_SECONDS}
    # A value `sleep` would refuse would end each loop after its first sample, leaving a
    # whole gate described by its first moments; and zero would spin.
    if ! [[ $every =~ ^[0-9]+(\.[0-9]+)?$ ]] || [ -z "${every//[0.]/}" ]; then
        echo "lock-timeout: ORCHESTRATOR_GATE_SAMPLE_SECONDS=$every is not a positive number of seconds, so the host is sampled every ${LOCAL_DIRECT_GATE_SAMPLE_SECONDS}s instead; unset it, or export a positive number, before the next push" >&2
        every=$LOCAL_DIRECT_GATE_SAMPLE_SECONDS
    fi
    gate_sample_loop "$samples/dispatches" "$every" "${BASH_SOURCE[0]%/*}/budget-dispatches.sh" &
    GATE_SAMPLER_PIDS+=("$!")
    gate_sample_loop "$samples/load1" "$every" gate_sample_load &
    GATE_SAMPLER_PIDS+=("$!")
}

# Append each reading command "${@:3}" prints to file $1, every $2 seconds, until stopped.
#
# A reading that fails is skipped, and what it said is appended to `$1.why`, one line per
# failure in `$1.failed`, which `record_gate_push` reports whenever any reading failed. A sample that cannot be written
# ends the loop, saying so: the peak is then taken over
# what was written, or is `unknown` when nothing can be read. Nothing the loop starts holds
# the caller's output: it is stopped mid-nap or mid-read, and a child left holding the
# hook's stdout or stderr would keep whoever reads them to their end — a publication
# capturing the hook — waiting until that child finished.
gate_sample_loop() {
    local file=$1 every=$2 value written child=
    shift 2
    # Each reading and each nap is a child the loop waits on, so a stop that arrives
    # mid-read or mid-nap ends that child too rather than waiting it out; the reading's
    # descriptors are replaced with `exec`, so it holds no copy of the caller's.
    trap '[ -z "$child" ] || kill "$child" 2>/dev/null; exit 143' TERM
    while :; do
        (exec </dev/null >"$file.reading" 2>>"$file.why" && "$@") &
        child=$!
        if wait "$child" && value=$(<"$file.reading"); then
            written=$file
        else
            value=failed written=$file.failed
        fi
        printf '%s\n' "$value" >>"$written" || {
            echo "lock-timeout: a sample could not be written to $written, so what is recorded for this gate covers only the samples before it; check that the temporary directory is writable and has free space before the next push" >&2
            return 1
        }
        sleep "$every" >/dev/null 2>&1 &
        child=$!
        # llmlint: ignore[changed_behavior_has_e2e] `start_gate_sampling` validates the interval before any loop starts, so no journey can make this `sleep` refuse it; the branch keeps a loop from spinning if it ever did.
        wait "$child" || {
            echo "lock-timeout: sampling stopped because 'sleep $every' failed, so the peak recorded for this gate covers only the samples before it; export ORCHESTRATOR_GATE_SAMPLE_SECONDS as a positive number of seconds before the next push" >&2
            return 1
        }
    done
}

# Print the 1-minute load average `ORCHESTRATOR_GATE_LOADAVG` (`/proc/loadavg` by
# default) holds, or fail, saying why, when it cannot be read or does not begin with one.
gate_sample_load() {
    local source=${ORCHESTRATOR_GATE_LOADAVG:-/proc/loadavg} load=
    if ! read -r load _ <"$source"; then
        echo "the load average source $source could not be read"
        return 1
    fi >&2
    if ! [[ $load =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
        echo "the load average source $source begins with '$load', which is no load average" >&2
        return 1
    fi
    printf '%s\n' "$load"
}

# Report the readings of quantity $1 that failed while the gate ran, from its samples'
# file $3, beside the peak $2 recorded for it, with what repairs them, $4.
gate_sample_failures() {
    local failed why
    [ -s "$3.failed" ] || return 0
    failed=$(wc -l <"$3.failed") || failed=some
    why=$(tail -n 3 -- "$3.why" 2>/dev/null) || why=
    why=${why:-nothing}
    if [ "$2" = unknown ]; then
        echo "lock-timeout: no $1 sample could be read while the gate ran, so it is recorded as unknown; the last failed reading said: ${why//$'\n'/ }; $4" >&2
    else
        echo "lock-timeout: ${failed// /} $1 sample(s) could not be read while the gate ran, so its peak $2 covers only the readings that succeeded; the last failed reading said: ${why//$'\n'/ }; $4" >&2
    fi
}

# Stop the loops `start_gate_sampling` started — by the pids it kept, and no others — so
# nothing read after the gate ended is written as a sample of it.
stop_gate_sampling() {
    local pid
    for pid in "${GATE_SAMPLER_PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    done
    GATE_SAMPLER_PIDS=()
}

# The largest sample in file $1, as it was read, or `unknown` when it holds none or cannot
# be read.
gate_sample_peak() {
    local peak=
    if [ -s "$1" ]; then
        if ! peak=$(awk 'NR == 1 || $1 + 0 > peak + 0 { peak = $1 } END { print peak }' "$1"); then
            echo "lock-timeout: the samples at $1 could not be read back, so their peak is recorded as unknown; check that the temporary directory is readable before the next push" >&2
            peak=
        fi
    fi
    printf '%s\n' "${peak:-unknown}"
}

# Run "$@" as the local-direct gate, record how long it took, and return its own status.
#
# Recorded whatever the verdict, because the lock is held for the whole run either way
# and what a waiter has to outlast is elapsed time rather than a pass. A run that was
# killed records nothing: the elapsed time of a turn that did not finish is not what the
# next one will take. Whole seconds, because that is the grain `onevcs` reads.
#
# When the calling shell names the push being gated in `ORCHESTRATOR_GATE_PUSH`, the
# host is sampled while the gate runs, and the same duration is recorded again with that
# push and the samples' peaks at `local_direct_gate_push_file`. `.githooks/pre-push` sets
# it without exporting it, so no process the gate starts — a test that pushes, say —
# inherits a push to record for.
record_local_direct_gate() {
    local started ended status file push=${ORCHESTRATOR_GATE_PUSH:-} samples=
    if [ -n "$push" ]; then
        if samples=$(mktemp -d 2>/dev/null); then
            start_gate_sampling "$samples"
        # llmlint: ignore[changed_behavior_has_e2e] No journey can fail this `mktemp` alone: it and the budget check's own result file both read TMPDIR, so the case that makes this fail also fails the check that would report it; the branch only records the conditions as unknown and says so.
        else
            samples=
            echo "lock-timeout: no directory could be made for the host samples, so this gate's dispatches_max and load1_max are recorded as unknown; make TMPDIR (or /tmp) a writable directory with free space before the next push" >&2
        fi
    fi
    # Bash's own clock rather than a `date` process, so reading the time cannot fail.
    started=$EPOCHSECONDS
    status=0
    "$@" || status=$?
    ended=$EPOCHSECONDS
    if [ -n "$samples" ]; then
        stop_gate_sampling
    fi
    if ! file=$(local_direct_gate_state_file); then
        printf 'lock-timeout: the gate took %ss and there is nowhere to record it, because neither XDG_STATE_HOME nor HOME is an absolute path; export one, then push again\n' \
            "$((ended - started))" >&2
        file=
    fi
    # A clock that went backwards — a host correcting its time mid-gate — measures
    # nothing, and writing it would derive a bound below the default on the next
    # publication. The previous recording stands.
    # llmlint: ignore[changed_behavior_has_e2e] A clock moving backwards mid-gate is host time correction, which no journey can cause without substituting the clock itself; the branch only declines to write.
    if [ -n "$file" ] && [ "$ended" -ge "$started" ]; then
        # Reported rather than swallowed: a gate whose duration is not being recorded
        # derives its bound from an older run forever, which is the silent half of the
        # defect this closes. It is never fatal — the gate's own verdict is what the
        # caller ran this for.
        if ! mkdir -p -- "$(dirname -- "$file")" 2>/dev/null ||
            ! printf '%s\n' "$((ended - started))" >"$file" 2>/dev/null; then
            printf 'lock-timeout: the gate took %ss and it could not be recorded at %s, so the merge-queue bound will keep deriving from an older run; make %s a writable directory (or point XDG_STATE_HOME at one), then push again\n' \
                "$((ended - started))" "$file" "$(dirname -- "$file")" >&2
        fi
        if [ -n "$push" ]; then
            record_gate_push "$push" "$((ended - started))" "$samples"
        fi
    fi
    if [ -n "$samples" ] && ! rm -rf -- "$samples" 2>/dev/null; then
        echo "lock-timeout: the host samples at $samples could not be removed; remove that directory by hand" >&2
    fi
    return "$status"
}

# Record push $1's gate as taking $2 seconds, with the peaks of the samples in directory
# $3 (empty for none), at `local_direct_gate_push_file`. Written whole and then moved into
# place, so a reader never sees half of one push's record beside half of another's.
# Reported rather than fatal, like the duration above: a push whose record is missing is
# refused by its `gate-time` check, naming this file, rather than here.
record_gate_push() {
    local push=$1 seconds=$2 samples=$3 file dispatches=unknown load=unknown
    if [ -n "$samples" ]; then
        dispatches=$(gate_sample_peak "$samples/dispatches")
        load=$(gate_sample_peak "$samples/load1")
        gate_sample_failures dispatches_max "$dispatches" "$samples/dispatches" \
            "run 'just host' to see the view the count is read from, and repair what it reports"
        gate_sample_failures load1_max "$load" "$samples/load1" \
            "point ORCHESTRATOR_GATE_LOADAVG at a readable load average, or unset it for /proc/loadavg"
    fi
    if ! file=$(local_direct_gate_push_file) ||
        ! printf 'push=%s\nseconds=%s\ndispatches_max=%s\nload1_max=%s\n' \
            "$push" "$seconds" "$dispatches" "$load" >"$file.$$" 2>/dev/null ||
        ! mv -f -- "$file.$$" "$file" 2>/dev/null; then
        rm -f -- "$file.$$" 2>/dev/null || true
        printf 'lock-timeout: the gate took %ss and it could not be recorded for this push at %s, so its gate-time budget cannot be checked; make %s a writable directory (or point XDG_STATE_HOME at one), then push again\n' \
            "$seconds" "$file" "${file%/*}" >&2
    fi
}

# Print the bound a waiter should queue under, in whole seconds.
#
# `duration * depth + margin`, floored at what `onevcs` would use on its own. A state
# file that is absent, unreadable, or does not hold a positive whole number of seconds is
# read as no recording at all and yields that floor — the three are one answer, because
# each of them is this host not knowing yet how long its gate takes.
resolve_lock_timeout_seconds() {
    local file recorded derived
    file=$(local_direct_gate_state_file) || file=
    recorded=
    if [ -n "$file" ] && [ -r "$file" ]; then
        read -r recorded <"$file" || recorded=
    fi
    # At most six digits — eleven and a half days — because a longer "duration" is no
    # gate this host has run, and one long enough would overflow the arithmetic below
    # into a bound that has nothing to do with it.
    case ${recorded:-} in
        '' | *[!0-9]*) recorded=0 ;;
    esac
    if [ "${#recorded}" -gt 6 ]; then
        recorded=0
    fi
    recorded=$((10#$recorded))
    derived=$((recorded * LOCAL_DIRECT_GATE_QUEUE_DEPTH + LOCAL_DIRECT_GATE_MARGIN_SECONDS))
    if [ "$derived" -lt "$ONEVCS_DEFAULT_LOCK_TIMEOUT_SECONDS" ]; then
        derived=$ONEVCS_DEFAULT_LOCK_TIMEOUT_SECONDS
    fi
    printf '%s\n' "$derived"
}

# Export that bound for every `onevcs` this process starts, attributing any diagnostic to
# $1, the name of the calling command.
#
# A caller who already named one keeps it: an operator raising the bound by hand for a
# wait they know is legitimate — which is what `docs/repo-lifecycle.md` has always told
# them to do — is a decision this must not overrule. An empty value is the same
# statement as an unset one here, because `onevcs` refuses an empty one outright and a
# waiter refused before it queues is worse than one bounded by the default.
export_lock_timeout() {
    local caller=${1:?export_lock_timeout: the name of the calling command is required, so its diagnostics stay attributable; pass it as the first argument, the way scripts/onepipeline.sh passes onepipeline, then retry}
    local named=${!ONEVCS_LOCK_TIMEOUT_VARIABLE:-}
    if [ -n "$named" ]; then
        # Kept only if it is a bound at all: `onevcs` refuses anything but a positive
        # number of seconds, and a caller is better told here, naming where the bound
        # came from, than by whichever verb reads it first.
        # llmlint: ignore-block[boundary_inputs_validated] No upper bound is checked here on purpose: the largest bound onevcs accepts is whatever an `Instant` on this platform can reach from now, which `bound_for` in onevcs's crates/onevcs/src/git.rs asks the platform for and refuses past, naming this variable, before any command runs — a ceiling restated here would be a second copy that drifts from the one onevcs enforces.
        local usable=true
        case $named in
            *[!0-9.]* | .* | *. | *.*.*) usable=false ;;
        esac
        # Zero, however it is spelled, is a number and still no bound.
        [ -n "${named//[0.]/}" ] || usable=false
        # llmlint: ignore-end[boundary_inputs_validated]
        if [ "$usable" = false ]; then
            echo "$caller: $ONEVCS_LOCK_TIMEOUT_VARIABLE=$named is not a positive number of seconds, which onevcs refuses; export one, or unset it to have the bound derived from this host's last gate, then retry" >&2
            return 2
        fi
        return 0
    fi
    local derived
    if ! derived=$(resolve_lock_timeout_seconds); then
        echo "$caller: the merge-queue bound could not be derived, so a waiter would queue under $ONEVCS_LOCK_TIMEOUT_VARIABLE's default while this identity's gate runs longer; check that ${XDG_STATE_HOME:-$HOME/.local/state} is readable, then retry" >&2
        return 2
    fi
    export "$ONEVCS_LOCK_TIMEOUT_VARIABLE=$derived"
}
