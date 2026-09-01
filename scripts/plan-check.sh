#!/usr/bin/env bash
# The executable `onepipeline plan check` is handed this repository's own plan checks as.
#
# The verb spawns a registered check directly — no shell, no `uv run` — so this file is
# what turns a path into an interpreter. It reads the loaded plan on stdin, answers the
# refusals on stdout, and exits 0 whether or not it refused, exactly as the contract in
# `orchestrator/plan_check.py` states; a non-zero exit means the check could not be run,
# which the verb reports separately rather than as an accept.
#
# `ORCHESTRATOR_PYTHON` is what the wrapper that spawned the verb sets, so the check runs
# on the same interpreter the wrapper does — a command that resolved this package cannot
# then spawn a check that fails to import it. Absent, this checkout's own
# `.venv/bin/python3` is preferred over whatever `python3` a caller's PATH resolves, for
# the reason AGENTS.md gives about every reader of `<root>/.venv/bin`: falling through to
# another checkout's toolchain is how a gate came to answer for a tree that was not the
# one in front of it. `PYTHONPATH` is set to this checkout's root, and to nothing
# else, so the package imports from the tree being checked rather than from an installed
# copy of another one or from anything a caller left on that variable. The modules it
# reaches are stdlib-only, so this runs under a bare `python3` too — which is
# what lets an operator, and a test, invoke this file directly.
set -euo pipefail

root=$(CDPATH='' cd "$(dirname "$0")/.." 2>/dev/null && pwd) || {
    echo "plan-check: cannot resolve this checkout's root from $0; run this check by its path inside the repository, or let 'just check-plan' register it" >&2
    exit 2
}

python=${ORCHESTRATOR_PYTHON:-}
if [ -z "$python" ]; then
    if [ -x "$root/.venv/bin/python3" ]; then
        python="$root/.venv/bin/python3"
    else
        python=python3
    fi
fi
# A path has to name an executable *regular file*: a directory carries the execute bit
# for traversal, so a `-x` check alone accepts one that can never be spawned.
if case "$python" in */*) true ;; *) false ;; esac; then
    interpreter_ok() { [ -f "$python" ] && [ -x "$python" ]; }
else
    interpreter_ok() { command -v "$python" >/dev/null 2>&1; }
fi
interpreter_ok || {
    echo "plan-check: '$python' is not an executable interpreter; run 'just bootstrap' from $root to provision this checkout's own, or set ORCHESTRATOR_PYTHON to one" >&2
    exit 2
}

# Set rather than prepended to: an inherited `PYTHONPATH` is an untrusted value from
# whatever spawned the verb, and every entry of it is a directory this interpreter would
# import from. An empty entry means the working directory — which for a spawned check is
# wherever the operator ran the verb — so honouring one would put an attacker-writable
# tree on the module path of a program that decides whether a plan may launch. Nothing
# here needs it: the modules this reaches are stdlib and this checkout's own.
PYTHONPATH="$root"
export PYTHONPATH

exec "$python" -m orchestrator.plan_check
