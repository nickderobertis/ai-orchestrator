#!/usr/bin/env bash
# The `design-doc-dag-natural-width` budget's command: `design_doc_dag.py` beside this file, run on
# this checkout's locked interpreter with this checkout on its import path.
set -euo pipefail

root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd) || {
    echo "design-doc-dag-natural-width: the checkout holding $0 cannot be entered; make it readable and retry" >&2
    exit 1
}
python="$root/.venv/bin/python"
[ -x "$python" ] || {
    echo "design-doc-dag-natural-width: $python is not this checkout's locked interpreter; provision the checkout with 'just bootstrap' and retry" >&2
    exit 1
}
PYTHONPATH="$root" exec "$python" "$root/orchestrator/budgets/design_doc_dag.py" natural-width
