# shellcheck shell=bash
# The ONE source of the plan-authoring root a planning launch puts in its dispatches'
# environment, sourced by scripts/plan.sh.
#
# `onetaskgraph.yaml` roots this repository's `authoring` source at the relative
# `.plans`, so every process resolves it against its own working directory — and a
# planning launch dispatches its planner into a worktree of its own. Left alone, the
# plan that planner authors lands in that worktree's own copy of the directory, which
# nothing outside the worktree reads and which is reclaimed with the worktree. What has
# kept planning working is dispatched planners guessing the launching checkout's
# absolute path correctly; nothing configured that and nothing checked it.
#
# So the launch resolves the root once, here, and exports it. The name is the plan
# store's own configuration-layer spelling of `sources.authoring.config.root` — that
# layer's key separator is a doubled underscore, and `onetaskgraph config show` reports
# the setting as coming from the environment once it is set, which is how a reader
# confirms it took. The value is whatever `orchestrator/plan_store.py` resolves that
# source to, rather than a directory name joined onto a checkout path: that resolution
# reads the store's own layered configuration, so a root moved in `onetaskgraph.yaml`
# or overridden per launch moves this with it.
#
# It is a helper rather than a line in the launcher for the reason its two siblings are:
# what the name is, what the value is, and what happens when the root cannot be resolved
# are one decision, and a second copy of any of them is a launch path that can drift
# into exporting a different directory from the one this checkout reads.
#
# Strict mode is established here rather than inherited from the sourcing caller,
# exactly as scripts/ask-manager-env.sh and scripts/credentials-env.sh do: the
# resolution below must abort rather than fall through to launching a run whose
# planner writes where nothing will look for it.
set -euo pipefail

#: The plan source a planning launch authors into, and the source name the environment
#: variable below is composed from. `scripts/plan.sh` writes the project it generates
#: into this same source.
PLAN_AUTHORING_SOURCE="authoring"

#: How `onetaskgraph` names that source's root at its environment layer: the dotted
#: setting path `sources.authoring.config.root`, upper-cased, with `__` between
#: segments. This is the one place that name is composed, and
#: `tests/test_plan_root_composition.py` is what refuses a second one anywhere in this
#: repository's tracked code.
PLAN_AUTHORING_ROOT_ENV="ONETASKGRAPH_SOURCES__AUTHORING__CONFIG__ROOT"

#: The resolution itself, deferred to the module that already performs it. Held to
#: `OSError` so a source this checkout cannot plan into is one line naming the source
#: and the path, rather than a traceback a launcher would print verbatim.
PLAN_AUTHORING_ROOT_PROGRAM='
import sys

from orchestrator import plan_store

try:
    sys.stdout.write(str(plan_store.ensure_writable_source_root(sys.argv[1])))
except OSError as exc:
    sys.stderr.write(str(exc))
    raise SystemExit(1)
'

# Resolve, validate, and export the plan-authoring root. Resolved from this file's own
# location, because the store being read is the one this checkout configures — never
# from `$PWD`, which a launcher may be invoked from anywhere. $1 names the calling
# launcher so its diagnostics stay attributable.
export_plan_authoring_root() {
    # Named the way scripts/credentials-env.sh names its own required input: a caller
    # that forgot the label would otherwise abort on `1: unbound variable`, which says
    # nothing about which helper was called wrong.
    local caller=${1:?export_plan_authoring_root: the name of the calling launcher is required, so its diagnostics stay attributable; pass it as the first argument, the way scripts/plan.sh passes plan, then retry}
    local here python root
    # llmlint: ignore[changed_behavior_has_e2e] Reachable only when this helper's own directory stops being enterable between a caller sourcing it and calling this; no journey can produce that without racing the filesystem the test itself runs on.
    if ! here=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd); then
        echo "$caller: this checkout could not be resolved, so the plan store it configures could not be read; run the launch from a readable checkout, then retry" >&2
        return 2
    fi
    # This checkout's own interpreter first, exactly as scripts/plan.sh chooses one:
    # the resolution imports this checkout's `orchestrator` package and spawns the
    # plan-store CLI that `uv run` puts first on the search path.
    python="$here/.venv/bin/python3"
    [ -x "$python" ] || python=python3
    # Run from this checkout and with it on `PYTHONPATH`, because the package read has
    # to be this checkout's whatever directory the launcher was invoked from — and those
    # are two mechanisms rather than one said twice. `python -c` prepends its working
    # directory, which would otherwise import a *neighbouring* checkout's `orchestrator`
    # ahead of the path below; `PYTHONPATH` is what still answers when an environment
    # sets `PYTHONSAFEPATH` and there is no prepended directory at all.
    #
    # Both streams are captured because the refusal below is the diagnostic: a source
    # served by a plugin nothing here writes, a root that is not a directory, one that
    # cannot be created, and one this launch may not write into are each answered by
    # that program in one sentence.
    if ! root=$(cd -- "$here" && PYTHONPATH="$here${PYTHONPATH:+:$PYTHONPATH}" "$python" -c "$PLAN_AUTHORING_ROOT_PROGRAM" "$PLAN_AUTHORING_SOURCE" 2>&1); then
        echo "$caller: the '$PLAN_AUTHORING_SOURCE' plan source could not be resolved to a writable root: ${root:-the resolution reported nothing}" >&2
        # The line above is the resolution's own sentence and says which of the two
        # this was; this one has to answer both, because the resolution can fail before
        # it reads any configuration at all — an interpreter, a package, or the
        # plan-store CLI this checkout has not provisioned.
        echo "$caller: repair that source in $here/onetaskgraph.yaml, or point $PLAN_AUTHORING_ROOT_ENV at a directory this launch may write into; if the resolution could not run at all, provision this checkout with 'just bootstrap'. Then retry" >&2
        return 2
    fi
    # A root somebody pointed this launch at deliberately has to beat the one this
    # checkout configures, or the override becomes impossible to make without editing
    # tracked configuration — the precedence scripts/credentials-env.sh gives a name
    # already in the environment. It is still resolved and validated above, because
    # that resolution reads the environment layer too: what was checked is the value
    # the dispatch will actually read.
    if [[ ! -v $PLAN_AUTHORING_ROOT_ENV ]]; then
        export "$PLAN_AUTHORING_ROOT_ENV=$root"
    fi
}
