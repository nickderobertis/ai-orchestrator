# shellcheck shell=bash
# The ONE source of the operational appendix a planning launch puts in its dispatches'
# environment, sourced by scripts/plan.sh.
#
# Every dispatched task has to carry `config/dispatch-appendix.md` verbatim, as one
# contiguous block under `## Additional info` — `just check-plan` refuses a task that
# does not — and the party that copies it in is the **planner**. A planner works in a
# worktree of its own while that file lives in the launching checkout, so naming it by a
# path relative to this repository told a planner to open a file that does not exist from
# where it stands; the refusal it met named that same path, and a manager ended up
# appending the text by hand. So the launch hands over the text itself, the way it
# already hands over the ask-manager wrapper and this checkout's credentials.
#
# Both the name and the value are asked of `orchestrator/criteria_guard.py`, which owns
# the file and the check that reads it. Nothing here composes either: what is exported
# has to be byte-for-byte what that check demands as a substring, and a second rendering
# of the same file is how the two come to differ by a trailing newline nobody can see.
#
# Strict mode is established here rather than inherited from the sourcing caller,
# exactly as scripts/ask-manager-env.sh and scripts/plan-root-env.sh do: the read below
# must abort rather than fall through to exporting an empty appendix, which would read to
# a planner as a host that asks for no operational notes at all.
set -euo pipefail

#: The read itself, deferred to the module that owns both halves. It answers the variable
#: name on its first line and the appendix text on every line after it, because a shell
#: variable cannot hold the NUL a more obvious separator would need.
#: Held to `OSError` for the reason scripts/plan-root-env.sh holds its own resolution to
#: it: a checkout whose appendix has gone missing is one sentence naming the file, rather
#: than a traceback a launcher would print verbatim above its own diagnostic.
DISPATCH_APPENDIX_PROGRAM='
import sys

from orchestrator import criteria_guard

try:
    text = criteria_guard.appendix_text()
except OSError as exc:
    sys.stderr.write(str(exc))
    raise SystemExit(1)
sys.stdout.write(criteria_guard.APPENDIX_ENV + "\n")
sys.stdout.write(text)
'

# Read, validate, and export the operational appendix. Resolved from this file's own
# location, because the appendix a dispatch is handed is the launching checkout's —
# never $PWD, which a launcher may be invoked from anywhere. $1 names the calling
# launcher so its diagnostics stay attributable.
export_dispatch_appendix() {
    # Named the way scripts/plan-root-env.sh names its own required input: a caller that
    # forgot the label would otherwise abort on `1: unbound variable`, which says nothing
    # about which helper was called wrong.
    local caller=${1:?export_dispatch_appendix: the name of the calling launcher is required, so its diagnostics stay attributable; pass it as the first argument, the way scripts/plan.sh passes plan, then retry}
    local here python answered name text
    # llmlint: ignore[changed_behavior_has_e2e] Reachable only when this helper's own directory stops being enterable between a caller sourcing it and calling this; no journey can produce that without racing the filesystem the test itself runs on.
    if ! here=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd); then
        echo "$caller: this checkout could not be resolved, so the operational appendix its dispatches must carry could not be read; run the launch from a readable checkout, then retry" >&2
        return 2
    fi
    # This checkout's own interpreter first, exactly as scripts/plan-root-env.sh chooses
    # one: the read imports this checkout's `orchestrator` package.
    python="$here/.venv/bin/python3"
    [ -x "$python" ] || python=python3
    # Run from this checkout and with it on `PYTHONPATH`, for the two reasons
    # scripts/plan-root-env.sh gives: `python -c` prepends its working directory, which
    # would otherwise import a neighbouring checkout's `orchestrator`, and `PYTHONPATH`
    # is what still answers under `PYTHONSAFEPATH`.
    if ! answered=$(cd -- "$here" && PYTHONPATH="$here${PYTHONPATH:+:$PYTHONPATH}" "$python" -c "$DISPATCH_APPENDIX_PROGRAM" 2>&1); then
        echo "$caller: the operational appendix every dispatched task must carry could not be read: ${answered:-the read reported nothing}" >&2
        echo "$caller: restore config/dispatch-appendix.md in $here, and provision this checkout with 'just bootstrap' if the read could not run at all. Then retry" >&2
        return 2
    fi
    # Split on the first newline, and answer "no newline at all" as an empty appendix
    # rather than by taking the name for the text. `$(...)` strips trailing newlines, so
    # an appendix that read back empty arrives here as the name alone — and
    # `${answered#*$'\n'}` on a string holding no newline returns that string unchanged,
    # which is how an empty appendix came to export its own variable name as its value and
    # walk straight past the guard below.
    case "$answered" in
        *$'\n'*)
            name=${answered%%$'\n'*}
            text=${answered#*$'\n'}
            ;;
        *)
            name=$answered
            text=""
            ;;
    esac
    if [ -z "$name" ] || [ -z "$text" ]; then
        echo "$caller: the operational appendix read back as empty, so a planner would be told this host asks for no operational notes at all; restore config/dispatch-appendix.md in $here, then retry" >&2
        return 2
    fi
    export "$name=$text"
}
