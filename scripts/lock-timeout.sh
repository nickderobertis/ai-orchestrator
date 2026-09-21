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
# that is reaped, or a clone a publication makes, must not lose it. The same root
# `scripts/stop-unwatched-guard.py` keeps its own memory under, and the same rule about
# reading `XDG_STATE_HOME`: honoured only when it is absolute, which is that variable's
# own specification and keeps a relative one from putting this under whatever directory a
# hook happened to run in.
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

# Run "$@" as the local-direct gate, record how long it took, and return its own status.
#
# Recorded whatever the verdict, because the lock is held for the whole run either way
# and what a waiter has to outlast is elapsed time rather than a pass. A run that was
# killed records nothing: the elapsed time of a turn that did not finish is not what the
# next one will take. Whole seconds, because that is the grain `onevcs` reads.
record_local_direct_gate() {
    local started ended status file
    # Bash's own clock rather than a `date` process, so reading the time cannot fail.
    started=$EPOCHSECONDS
    status=0
    "$@" || status=$?
    ended=$EPOCHSECONDS
    if ! file=$(local_direct_gate_state_file); then
        printf 'lock-timeout: the gate took %ss and there is nowhere to record it, because neither XDG_STATE_HOME nor HOME is an absolute path; export one, then push again\n' \
            "$((ended - started))" >&2
        return "$status"
    fi
    # A clock that went backwards — a host correcting its time mid-gate — measures
    # nothing, and writing it would derive a bound below the default on the next
    # publication. The previous recording stands.
    # llmlint: ignore[changed_behavior_has_e2e] A clock moving backwards mid-gate is host time correction, which no journey can cause without substituting the clock itself; the branch only declines to write.
    if [ "$ended" -ge "$started" ]; then
        # Reported rather than swallowed: a gate whose duration is not being recorded
        # derives its bound from an older run forever, which is the silent half of the
        # defect this closes. It is never fatal — the gate's own verdict is what the
        # caller ran this for.
        if ! mkdir -p -- "$(dirname -- "$file")" 2>/dev/null ||
            ! printf '%s\n' "$((ended - started))" >"$file" 2>/dev/null; then
            printf 'lock-timeout: the gate took %ss and it could not be recorded at %s, so the merge-queue bound will keep deriving from an older run; make %s a writable directory (or point XDG_STATE_HOME at one), then push again\n' \
                "$((ended - started))" "$file" "$(dirname -- "$file")" >&2
        fi
    fi
    return "$status"
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
    local caller=${1:?export_lock_timeout: the name of the calling command is required, so its diagnostics stay attributable; pass it as the first argument, the way scripts/land-branch.sh passes land-branch, then retry}
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
