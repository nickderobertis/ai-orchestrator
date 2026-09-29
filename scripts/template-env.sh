# shellcheck shell=bash
# The ONE source of the two names this host's task templates travel under, sourced by
# scripts/dispatch-env.sh (a launch's driver start and the dispatch-env hook before every
# node-scope dispatch) and by scripts/onepipeline.sh for every verb it runs.
#
#   * ONEPIPELINE_TEMPLATE_ROOT is this checkout's tracked `templates/`, absolute: the host
#     root `onepipeline template` reads `templates.yaml` and each `<name>.md.j2` from, which
#     is where this host registers its own names and overrides `plan-task`.
#   * ONETASKGRAPH_INTERACTIVE=false keeps a dispatched agent's `onetaskgraph` from waiting
#     on a prompt nobody can answer. It is set for an automated path and nowhere a person's
#     own CLI reads — no tracked configuration names it — so a person running
#     `onetaskgraph` in this checkout keeps the product's interactive default.
#
# Strict mode is established here rather than inherited, as the other resolvers do.
set -euo pipefail

# Export ONEPIPELINE_TEMPLATE_ROOT as this checkout's `templates/`, resolved from this
# file's own location rather than from `$PWD`, because a launcher may be invoked from
# anywhere. $1 names the calling launcher so its diagnostics stay attributable.
export_template_root() {
    local caller="${1:?export_template_root: the name of the calling launcher is required, so its diagnostics stay attributable; pass it as the first argument, the way scripts/onepipeline.sh passes onepipeline, then retry}"
    local root
    # llmlint: ignore[changed_behavior_has_e2e] Reachable only when this helper's own directory stops being enterable between a caller sourcing it and calling this; no journey can produce that without racing the filesystem the test itself runs on.
    if ! root=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd); then
        echo "$caller: this checkout could not be resolved, so its template root cannot be named; run the launch from a readable checkout, then retry" >&2
        return 2
    fi
    root="$root/templates"
    if [ ! -r "$root/templates.yaml" ]; then
        echo "$caller: this checkout's template root $root carries no readable templates.yaml, so onepipeline would register none of this host's templates; restore templates/ from this checkout's history, then retry" >&2
        return 2
    fi
    export ONEPIPELINE_TEMPLATE_ROOT="$root"
}

export_noninteractive_plan_store() {
    : "${1:?export_noninteractive_plan_store: the name of the calling launcher is required, so its diagnostics stay attributable; pass it as the first argument, then retry}"
    export ONETASKGRAPH_INTERACTIVE=false
}
