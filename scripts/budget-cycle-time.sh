#!/usr/bin/env bash
# The `cycle-time` budget reads the adopted engine's per-change telemetry.
# `docs/budgets.md` defines the environment this host's success hook supplies.
# It writes the selected node's cycle_seconds and a detail naming its run, node and
# landing to onebudgetspec's result file. An absent or unmeasured cycle fails with
# the reason, rather than estimating a duration.
set -euo pipefail

fail() {
    echo "budget-cycle-time: $1" >&2
    exit 1
}

# Reads the telemetry document on stdin; argv is the run, the node and the result file.
PROGRAM='
import json, math, sys
from datetime import datetime

run, node, result = sys.argv[1:4]
try:
    document = json.load(sys.stdin)
except ValueError as broken:
    sys.exit(f"the per-change telemetry the engine reported for run {run} is not JSON ({broken})")
changes = document.get("changes") if isinstance(document, dict) else None
if not isinstance(changes, list) or type(document.get("schema_version")) is not int or document.get("schema_version") != 1 or document.get("run_id") != run:
    sys.exit(f"the per-change telemetry the engine reported for run {run} holds no changes")
matched = [change for change in changes if isinstance(change, dict) and change.get("node") == node]
if not matched:
    sys.exit(f"run {run} recorded no change whose node is {node}, so it has no cycle to report")
change = matched[0]
landing, cycle = change.get("landing"), change.get("cycle_seconds")
if not isinstance(landing, str) or not landing:
    sys.exit(
        f"the change of {node} in run {run} has not landed (its outcome is "
        f"{change.get("outcome")!r}), so its cycle was not measured"
    )
if type(cycle) not in (int, float) or not math.isfinite(cycle) or cycle < 0:
    sys.exit(
        f"the cycle of {node} in run {run} was not measured: its landing {landing} records no "
        "time it landed at, which the engine reads as unknown rather than estimating it"
    )
for field in ("dispatched_at", "landed_at"):
    stamp = change.get(field)
    try:
        parsed = datetime.fromisoformat(stamp) if isinstance(stamp, str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        sys.exit(f"the measured cycle of {node} in run {run} holds no valid {field} timestamp")
detail = (
    f"run {run}, node {node}: from its first dispatch at {change.get("dispatched_at")} to its "
    f"landing {landing} at {change.get("landed_at")}"
)
with open(result, "w", encoding="utf-8") as written:
    json.dump({"value": cycle, "detail": detail}, written)
'

run=${ONEPIPELINE_RUN_ID:-}
[ -n "$run" ] ||
    fail "no run is named in ONEPIPELINE_RUN_ID; cycle-time is measured only by 'just follow-ups' over a run that landed the change, so run that"
node=${ONEPIPELINE_NODE_ID:-}
[ -n "$node" ] ||
    fail "no node is named in ONEPIPELINE_NODE_ID; cycle-time measures one landed change, which 'just follow-ups' names; run that recipe to name it"
result=${ONEBUDGETSPEC_RESULT:-}
[ -n "$result" ] || fail "ONEBUDGETSPEC_RESULT names no result file; run this as the cycle-time budget's command, through onebudgetspec check"

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd) ||
    fail "the directory holding $0 cannot be entered, so the interpreter beside it cannot be found; make that checkout readable"
python="$script_dir/../.venv/bin/python3"
[ -x "$python" ] || fail "no interpreter is installed at $python to read the telemetry with; run 'just bootstrap' in that checkout"

document=$(onepipeline telemetry "$run" --changes --json) ||
    fail "the engine could not report run $run's per-change telemetry (its diagnostic is above); read it with 'just status $run'"
printf '%s' "$document" | "$python" -c "$PROGRAM" "$run" "$node" "$result" ||
    fail "no cycle of $node in run $run was written to $result (the reason is above); read 'onepipeline telemetry $run --changes --json' and repair the missing evidence before retrying"
