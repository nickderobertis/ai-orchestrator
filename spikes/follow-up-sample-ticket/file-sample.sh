#!/usr/bin/env bash
# File or refresh the sample follow-up ticket, its board fields and its sample evidence comment
# on `followups`, through the store, and print what the store answered at each step.
# Throwaway (spike-sample-ticket). Run from the checkout root:
#
#   spikes/follow-up-sample-ticket/file-sample.sh
#
# The board credential is established by a plan-store wrapper, `PLAN_STORE` (default: the
# canonical checkout's `scripts/plan-store.sh`, which loads that checkout's credential for one
# command and never prints it); the store binary and `onetaskgraph.yaml` are this checkout's.
set -euo pipefail

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
root=$(CDPATH='' cd -- "$here/../.." && pwd)
cd "$root"
PLAN_STORE=${PLAN_STORE:-/home/whoop/ai-orchestrator/scripts/plan-store.sh}
store() { "$PLAN_STORE" ./.venv/bin/onetaskgraph "$@"; }
source_id=follow-up-sample:follow-up-collapsed-agent-sections/sample-ticket
marker=$(tail -n 1 "$here/comment.md")
out=${ONEPIPELINE_NODE_SCRATCH_DIR:-$(mktemp -d)}

echo "== build the local Markdown source"
./.venv/bin/python "$here/build_source.py"

echo "== create any missing board field (adds only)"
store sources fields followups --apply

echo "== copy the sample onto followups"
store --json task copy "$source_id" --to followups | tee "$out/copy.json"
echo
target=$(./.venv/bin/python -c '
import json, sys
def find(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("destination", "target") and isinstance(v, str) and v.startswith("followups:"):
                return v
            got = find(v)
            if got:
                return got
    elif isinstance(o, list):
        for v in o:
            got = find(v)
            if got:
                return got
print(find(json.load(open(sys.argv[1]))) or "")' "$out/copy.json")
[ -n "$target" ] || { echo "file-sample: the copy answered no followups id; read $out/copy.json" >&2; exit 1; }
echo "sample item: $target"

echo "== add or refresh the sample evidence comment"
store --json task comment list "$target" > "$out/comments.json"
existing=$(./.venv/bin/python -c '
import json, sys
marker = sys.argv[2]
def walk(o):
    if isinstance(o, dict):
        if isinstance(o.get("body"), str) and o["body"].rstrip().endswith(marker) and "id" in o:
            yield o["id"]
        for v in o.values():
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)
print(next(walk(json.load(open(sys.argv[1]))), ""))' "$out/comments.json" "$marker")
if [ -n "$existing" ]; then
    store --json task comment edit "$target" "$existing" --body-file "$here/comment.md"
else
    store --json task comment add "$target" --body-file "$here/comment.md"
fi
echo

echo "== the sample as the store reads it back"
store task show "$target"
