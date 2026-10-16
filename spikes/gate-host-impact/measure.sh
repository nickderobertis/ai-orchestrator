#!/usr/bin/env bash
# Spike harness (throwaway): measure what one cold pre-push gate of this checkout does to
# the host — peak temporary disk, leftovers, leaked processes — attributing only the
# gate's own root and the scratch registered to it. Run from the checkout root:
#
#     spikes/gate-host-impact/measure.sh [--remote origin] [--base main] [--out FILE]
#
# `record.py` holds every rule; `record-gate-host-impact` grows it into the recorder.
set -euo pipefail

root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
if [[ $PWD != "$root" ]]; then
    echo "measure: run from the checkout root ($root), not $PWD" >&2
    exit 2
fi
if [[ ! -x $root/.venv/bin/python ]]; then
    echo "measure: $root/.venv/bin/python is missing; run 'just session-setup' first" >&2
    exit 2
fi
if [[ -z ${ONEPIPELINE_NODE_SCRATCH_DIR:-} ]]; then
    echo "measure: ONEPIPELINE_NODE_SCRATCH_DIR is unset; the gate's root is made under it, never under /tmp" >&2
    exit 2
fi
exec "$root/.venv/bin/python" "$root/spikes/gate-host-impact/record.py" "$@"
