#!/usr/bin/env bash
# The `gate-time` budget's measurement: how long this push's complete gate took, as
# `.githooks/pre-push` already measured it.
#
# The hook times the gate once, in `record_local_direct_gate` (`scripts/lock-timeout.sh`),
# and records that duration against the push it is gating, with the peaks of the host
# conditions it sampled while the gate ran. This reads that record and writes it as
# onebudgetspec's result, to the file `ONEBUDGETSPEC_RESULT` names: the duration as the
# value, and `dispatches_max` and `load1_max` as returned conditions, each `unknown` where
# no sample could read it. It never times or runs the gate itself.
#
# The push is the one `ORCHESTRATOR_GATE_PUSH` names, which the hook hands to the check
# alone. A record for any other push — an earlier one, or a recording that failed — is
# refused rather than reported, since it would measure a gate this push never ran.
set -euo pipefail

fail() {
  echo "budget-gate-time: $1" >&2
  exit 1
}

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd) ||
  fail "the directory holding $0 cannot be entered, so the helper that says where the gate is recorded cannot be read; make that checkout readable and push again"
# shellcheck source=scripts/lock-timeout.sh
# llmlint: ignore[robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at — the reason scripts/onepipeline.sh gives for its own.
. "$script_dir/lock-timeout.sh"

#: What every refusal for a missing or unusable record ends with: the record is the
#: hook's, so the remedy is always the hook's own recording, run again.
again="the hook records it as it runs the gate, so repair what kept it from recording (its lock-timeout line above says) and push again"

push=${ORCHESTRATOR_GATE_PUSH:-}
[ -n "$push" ] || fail "no push is named in ORCHESTRATOR_GATE_PUSH; gate-time is measured only by .githooks/pre-push, from the gate it has just run for that push, so push to measure it"
[[ $push =~ ^[A-Za-z0-9._-]+$ ]] || fail "ORCHESTRATOR_GATE_PUSH=$push is not a push the hook names, which is letters, digits, dots, dashes and underscores; push to measure gate-time rather than naming a push by hand"
result=${ONEBUDGETSPEC_RESULT:-}
[ -n "$result" ] || fail "ONEBUDGETSPEC_RESULT names no result file; run this as the gate-time budget's command, through onebudgetspec check"
record=$(local_direct_gate_push_file) || fail "neither XDG_STATE_HOME nor HOME is an absolute path, so there is nowhere this push's gate could have been recorded; export an absolute one and push again"
recorded=$(cat -- "$record" 2>/dev/null) || fail "no gate could be read at $record, so this push's gate duration is unknown; $again"

recorded_push='' seconds='' dispatches_max='' load1_max=''
while IFS='=' read -r key value; do
  case $key in
    push) recorded_push=$value ;;
    seconds) seconds=$value ;;
    dispatches_max) dispatches_max=$value ;;
    load1_max) load1_max=$value ;;
  esac
done <<<"$recorded"

[ "$recorded_push" = "$push" ] || fail "the gate recorded at $record is for an earlier push (${recorded_push:-none named}), not this one ($push), so this push's gate duration is unknown; $again"
[[ $seconds =~ ^[0-9]{1,9}$ ]] || fail "the gate recorded at $record for this push names no whole number of seconds (${seconds:-nothing}); $again"
# As a JSON number, which a leading zero is not.
seconds=$((10#$seconds))
[[ $dispatches_max =~ ^([0-9]+|unknown)$ ]] || fail "the gate recorded at $record names dispatches_max=${dispatches_max:-nothing}, which is neither a count nor unknown; $again"
[[ $load1_max =~ ^([0-9]+(\.[0-9]+)?|unknown)$ ]] || fail "the gate recorded at $record names load1_max=${load1_max:-nothing}, which is neither a load average nor unknown; $again"

printf '{"value": %s, "detail": "the complete gate as .githooks/pre-push ran it for push %s", "conditions": {"dispatches_max": "%s", "load1_max": "%s"}}\n' \
  "$seconds" "$push" "$dispatches_max" "$load1_max" >"$result" ||
  fail "the result could not be written to $result, the file onebudgetspec named; check that its directory is writable and has free space, then push again"
