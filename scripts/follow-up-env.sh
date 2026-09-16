# shellcheck shell=bash
# The ONE source of the follow-up drafting seam every launch puts in its dispatches'
# environment, sourced by scripts/onepipeline.sh for `start` and `adopt`, by
# scripts/follow-up.sh for `just follow-up`, and by scripts/follow-up-draft.sh for the
# name of the root it is handed.
#
# A draft is an unverified, non-blocking follow-up stored as a local Markdown task in
# the `drafts` plan source (`orchestrator/follow_up_drafts.py` states the record). Every
# party that may write one — a dispatched worker, the monitor, the pacemaker — runs
# somewhere other than the launching checkout: a worktree of its own, or a graph member's
# scratch. `onetaskgraph.yaml` roots `drafts` at the relative `.follow-ups`, which the
# adopted store resolves against the directory of the document it read that root from —
# and the document a party in a worktree reads is that worktree's own copy, so each of
# them would write where nothing reads, into a directory reclaimed with the worktree. A
# party whose scratch holds no configuration file at all has no such document to resolve
# against. So the launch resolves the root once, here, and exports three names:
#
#   * the store's own environment-layer spelling of `sources.drafts.config.root`, holding
#     the absolute directory `orchestrator/plan_store.py` resolves that source to;
#   * `sources.drafts.plugin` beside it, because a party reading drafts back through the
#     store runs from a directory whose configuration may declare no `drafts` source at
#     all — a graph member's scratch has no configuration file, and a worker in another
#     repository reads that repository's — and a source the environment names without a
#     plugin is refused by the store as incomplete;
#   * `ORCHESTRATOR_FOLLOW_UP_DRAFT`, the path of `scripts/follow-up-draft.sh` in this
#     checkout, which is the command every briefing tells a party to draft with.
#
# A name the environment already defines wins for the two store settings, the precedence
# scripts/plan-root-env.sh gives the authoring root, and the value is still resolved and
# validated through the store, which reads that environment layer too. It is a helper
# rather than lines in each caller because the names, the values and the refusals are one
# decision, and `tests/test_follow_up_draft_composition.py` refuses a second composition
# of any of the three names anywhere in this repository's tracked code.
#
# Strict mode is established here rather than inherited from the sourcing caller, exactly
# as scripts/plan-root-env.sh and scripts/ask-manager-env.sh do: a resolution that failed
# must abort the launch rather than fall through to dispatches that draft nowhere.
set -euo pipefail

#: The plan source drafts are stored in, and the one the names below are composed from.
FOLLOW_UP_DRAFTS_SOURCE="drafts"

#: How `onetaskgraph` names that source's root and plugin at its environment layer: the
#: dotted setting path upper-cased, with `__` between segments.
FOLLOW_UP_DRAFTS_ROOT_ENV="ONETASKGRAPH_SOURCES__DRAFTS__CONFIG__ROOT"
FOLLOW_UP_DRAFTS_PLUGIN_ENV="ONETASKGRAPH_SOURCES__DRAFTS__PLUGIN"

#: The plugin that source is served by, which is the one a draft is a file of.
FOLLOW_UP_DRAFTS_PLUGIN="local-md"

#: The variable a party drafts through.
FOLLOW_UP_DRAFT_ENV="ORCHESTRATOR_FOLLOW_UP_DRAFT"

#: The resolution itself, deferred to the module that already performs it for the
#: authoring root. Held to `OSError` so a source this checkout cannot draft into is one
#: line naming the source and the path rather than a traceback.
FOLLOW_UP_DRAFTS_ROOT_PROGRAM='
import sys

from orchestrator import plan_store

try:
    sys.stdout.write(str(plan_store.ensure_writable_source_root(sys.argv[1])))
except OSError as exc:
    sys.stderr.write(str(exc))
    raise SystemExit(1)
'

# Resolve, validate, and export the drafting seam. Resolved from this file's own
# location, because the store and the command are this checkout's — never from `$PWD` or
# `PATH`, which a launcher may be invoked from anywhere with. $1 names the calling
# launcher so its diagnostics stay attributable.
export_follow_up_drafts() {
    local caller=${1:?export_follow_up_drafts: the name of the calling launcher is required, so its diagnostics stay attributable; pass it as the first argument, the way scripts/onepipeline.sh passes onepipeline, then retry}
    local here python root wrapper
    # llmlint: ignore[changed_behavior_has_e2e] Reachable only when this helper's own directory stops being enterable between a caller sourcing it and calling this; no journey can produce that without racing the filesystem the test itself runs on.
    if ! here=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd); then
        echo "$caller: this checkout could not be resolved, so the follow-up drafting seam could not be established; run the launch from a readable checkout, then retry" >&2
        return 2
    fi
    # All three, because what is exported is a command an agent runs: `-x` alone accepts a
    # directory of that name, and a script that cannot be read cannot be run.
    wrapper="$here/scripts/follow-up-draft.sh"
    if [ ! -f "$wrapper" ] || [ ! -r "$wrapper" ] || [ ! -x "$wrapper" ]; then
        echo "$caller: the follow-up drafting command is not an executable file at $wrapper; restore it from the repository and 'chmod +x' it, so the agents this launches can record what they leave out of scope" >&2
        return 2
    fi
    python="$here/.venv/bin/python3"
    [ -x "$python" ] || python=python3
    # Run from this checkout, with it on `PYTHONPATH` and its own `.venv/bin` first on
    # `PATH`: the package the resolution imports has to be this checkout's, and the
    # plan-store CLI it spawns is installed into that directory, which a launcher's own
    # search path need not carry. Both streams are captured because the refusal below is
    # the diagnostic.
    if ! root=$(cd -- "$here" && PATH="$here/.venv/bin:$PATH" PYTHONPATH="$here${PYTHONPATH:+:$PYTHONPATH}" "$python" -c "$FOLLOW_UP_DRAFTS_ROOT_PROGRAM" "$FOLLOW_UP_DRAFTS_SOURCE" 2>&1); then
        echo "$caller: the '$FOLLOW_UP_DRAFTS_SOURCE' plan source could not be resolved to a writable root: ${root:-the resolution reported nothing}" >&2
        echo "$caller: repair that source in $here/onetaskgraph.yaml, or point $FOLLOW_UP_DRAFTS_ROOT_ENV at a directory this launch may write into; if the resolution could not run at all, provision this checkout with 'just bootstrap'. Then retry" >&2
        return 2
    fi
    if [[ ! -v $FOLLOW_UP_DRAFTS_ROOT_ENV ]]; then
        export "$FOLLOW_UP_DRAFTS_ROOT_ENV=$root"
    fi
    if [[ ! -v $FOLLOW_UP_DRAFTS_PLUGIN_ENV ]]; then
        export "$FOLLOW_UP_DRAFTS_PLUGIN_ENV=$FOLLOW_UP_DRAFTS_PLUGIN"
    fi
    export "$FOLLOW_UP_DRAFT_ENV=$wrapper"
}
