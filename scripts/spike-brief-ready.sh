#!/usr/bin/env bash
# The engine awaits this advisory closeout after the detached local writer settles.
set -euo pipefail
script_dir=$(dirname -- "$0")
cd "$script_dir/.." || {
    echo "spike brief advisory: cannot enter $script_dir/..; restore the checkout, then resume the parent planning flow" >&2
    exit 0
}
[ -x .venv/bin/python ] || {
    echo "spike brief advisory: Python is absent; run 'just bootstrap', then resume the parent planning flow" >&2
    exit 0
}
# The flow reports its own failures and exits 0; a nonzero status here is the interpreter or
# the module failing to start, which is reported the same advisory way.
.venv/bin/python -m orchestrator.spike_brief_flow hook || echo "spike brief advisory: the ready hook exited $?; run 'just bootstrap', then resume the parent planning flow" >&2
