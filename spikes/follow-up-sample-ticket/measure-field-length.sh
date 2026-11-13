#!/usr/bin/env bash
# Find the longest ProjectV2 text-field value the store will write, by projecting values of
# growing length onto the sample's `Owning runs` field through the store's `task metadata set`,
# then bisecting between the longest accepted and the shortest refused until they are within
# 100 characters. If 60,000 characters is accepted, it stops: no limit below it. Every length
# tried is appended to `$here/measurements.tsv` with the store's own answer. The realistic
# value is put back at the end. Throwaway (spike-sample-ticket). Run from the checkout root:
#
#   spikes/follow-up-sample-ticket/measure-field-length.sh followups:<native id of the sample>
set -euo pipefail

target=${1:?pass the sample'"'"'s followups qualified id, as file-sample.sh printed it}
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
root=$(CDPATH='' cd -- "$here/../.." && pwd)
cd "$root"
PLAN_STORE=${PLAN_STORE:-/home/whoop/ai-orchestrator/scripts/plan-store.sh}
store() { "$PLAN_STORE" ./.venv/bin/onetaskgraph "$@"; }
log="$here/measurements.tsv"
out=${ONEPIPELINE_NODE_SCRATCH_DIR:-$(mktemp -d)}
[ -f "$log" ] || printf 'length\texit\tanswer\n' > "$log"

# The sample's record with `owning_runs` replaced by a value of exactly N characters: run
# names, one per line, as a long run list would be rendered.
record_of_length() {
    ./.venv/bin/python - "$1" <<'EOF'
import json, sys
sys.path.insert(0, "spikes/follow-up-sample-ticket")
import build_source as b
n = int(sys.argv[1])
run = b.RUN
value = "\n".join([run] * (n // (len(run) + 1) + 1))[:n]
assert len(value) == n
print(json.dumps(b.record(value)))
EOF
}

try() {
    local n=$1 code answer
    record_of_length "$n" > "$out/record.$n.json"
    set +e
    store --json task metadata set "$target" orchestrator.follow-up-sample "$(cat "$out/record.$n.json")" \
        > "$out/answer.$n.json" 2> "$out/answer.$n.err"
    code=$?
    set -e
    answer=$(./.venv/bin/python -c '
import json, sys
text = open(sys.argv[1]).read()
try:
    d = json.loads(text)
except ValueError:
    print(" ".join(open(sys.argv[2]).read().split())[:400]); sys.exit()
f = d.get("failure") if isinstance(d, dict) else None
print(" ".join((json.dumps(f) if f else "accepted: " + json.dumps(d)[:200]).split())[:600])' \
        "$out/answer.$n.json" "$out/answer.$n.err")
    printf '%s\t%s\t%s\n' "$n" "$code" "$answer" >> "$log"
    echo "length $n: exit $code: $answer"
    return "$code"
}

top=60000
if try "$top"; then
    echo "60000 accepted: no limit found below 60,000 characters"
else
    lo=1000
    try "$lo" || { echo "even $lo was refused; read $log" >&2; exit 1; }
    hi=$top
    while [ $((hi - lo)) -gt 100 ]; do
        mid=$(((lo + hi) / 2))
        if try "$mid"; then lo=$mid; else hi=$mid; fi
    done
    echo "longest accepted: $lo; shortest refused: $hi"
fi

echo "== put the realistic value back"
store --json task metadata set "$target" orchestrator.follow-up-sample \
    "$(./.venv/bin/python -c 'import json,sys; sys.path.insert(0,"spikes/follow-up-sample-ticket"); import build_source as b; print(json.dumps(b.record()))')"
echo
