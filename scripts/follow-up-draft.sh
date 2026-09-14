#!/usr/bin/env bash
# The command every dispatch, the monitor and the pacemaker drafts a non-blocking
# follow-up with, reached through `$ORCHESTRATOR_FOLLOW_UP_DRAFT`, and the one
# `just follow-up` runs for the manager:
#
#     "$ORCHESTRATOR_FOLLOW_UP_DRAFT" --title TITLE --repository HOST/OWNER/NAME \
#         [--path PATH]... [--as node|monitor|pacemaker|manager] [--member NAME] \
#         [--run RUN-ID] < body.md
#
# Everything a draft is — the flags, the body's headings, what is stamped, the refusals,
# and the `--help` that states all of it — is `orchestrator/follow_up_drafts.py`'s. This
# only puts that module in front of the caller from wherever the caller is: a worktree of
# another repository, a graph member's scratch, or this checkout. So it resolves the
# checkout from its own location and imports the package from there, with Python's safe
# path on: `python -c` otherwise puts the caller's working directory first on the import
# path, and a worker of this repository works in a checkout whose own `orchestrator` is a
# different revision of the one this command belongs to.
#
# The root's environment name is read out of scripts/follow-up-env.sh, its one
# composition, and handed to the module rather than spelled there. The process is
# replaced rather than forked, so the draft's own process descends directly from the
# caller's — the ancestry the module resolves a node from.
set -euo pipefail

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on.
if ! here=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd); then
    echo "follow-up-draft: the checkout this command belongs to could not be resolved; run it by its path in a readable checkout, then retry" >&2
    exit 2
fi
helper="$here/scripts/follow-up-env.sh"
if [ ! -f "$helper" ] || [ ! -r "$helper" ]; then
    echo "follow-up-draft: required helper is not a readable regular file: $helper; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
# shellcheck source=scripts/follow-up-env.sh
if ! . "$helper"; then
    echo "follow-up-draft: the helper at $helper is readable but could not be loaded; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi

python="$here/.venv/bin/python3"
[ -x "$python" ] || python=python3
if ! command -v "$python" >/dev/null 2>&1; then
    echo "follow-up-draft: no Python interpreter was found, neither $here/.venv/bin/python3 nor python3 on PATH, so no draft can be written; provision this checkout with 'just bootstrap', then retry" >&2
    exit 2
fi

# The import is guarded inside the program rather than probed first, so a checkout whose
# package cannot be imported is one sentence with its repair instead of a traceback.
program='
import sys

try:
    from orchestrator import follow_up_drafts
except ImportError as exc:
    sys.stderr.write(
        f"follow-up-draft: this checkout'"'"'s orchestrator package could not be imported ({exc}); "
        "provision the checkout with '"'"'just bootstrap'"'"', then retry\n"
    )
    raise SystemExit(2)

raise SystemExit(follow_up_drafts.main(sys.argv[2:], root_env=sys.argv[1]))
'

export PYTHONSAFEPATH=1
export PYTHONPATH="$here${PYTHONPATH:+:$PYTHONPATH}"
exec "$python" -c "$program" "$FOLLOW_UP_DRAFTS_ROOT_ENV" "$@"
