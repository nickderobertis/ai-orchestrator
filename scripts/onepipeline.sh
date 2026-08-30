#!/usr/bin/env bash
# llmlint: ignore-file[changed_behavior_has_e2e] tests/e2e/test_orchestrate_launch_e2e.py drives a real launch and its planner verbs through this wrapper; tests/e2e/test_delegated_recipes_e2e.py holds the command line each recipe renders through it.
# The one entry point every `onepipeline` recipe goes through, so a planner's
# identity and a launch's environment are established in one place.
#
# `onepipeline` reads two things out of the environment that nothing else in this
# repository would otherwise set, and both are load-bearing:
#
#   * ONEPIPELINE_LAUNCHER / ONEPIPELINE_LAUNCHER_SESSION decide **who owns a
#     run**. Without them every launch records `unknown`, `just runs --mine`
#     lists nothing, and `just stop` refuses every run on this host as
#     unattributable — the whole ownership doctrine in AGENTS.md, inert. They are
#     read by the reader too, not just the launcher, which is why every recipe
#     comes through here rather than only the launching one: ownership is a
#     comparison, and a view that did not identify itself matches nothing.
#   * A launch additionally has to carry the portable indirections every
#     oneharness config's `env_from` names. oneharness refuses to start when one
#     is unset, and the launched graph runs the orchestrator and every worker
#     under those configs.
#
# A launch also has to carry ORCHESTRATOR_ASK_MANAGER, the path of
# `scripts/ask-manager.sh`. It is established here for the same reason the two above
# are: it reaches a dispatch only by inheritance, so the launching process is the
# last place that can put it there, and `start` and `adopt` are every shape of launch
# this repository has. `scripts/ask-manager-env.sh` owns the path and the refusal.
#
# Detection is from the exported environment and never from process ancestry,
# and a session nothing identifies stays unidentified: a run misattributed to a
# planner who did not launch it is worse than one attributed to nobody.
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

# An already-exported identity wins: a dispatch nested inside a launch inherits
# its planner's, and re-deriving it here from the harness the *dispatch* runs
# under would reassign the run to the worker mid-flight.
if [ -z "${ONEPIPELINE_LAUNCHER_SESSION:-}" ]; then
    # Ordered, and read in order, so a session nested inside another resolves to
    # the first harness that claims it. `CODEX_HOME` is deliberately not a marker:
    # it is ambient configuration a developer may export in a shell profile, so a
    # plain shell would claim to be codex.
    claude_session=${CLAUDE_CODE_SESSION_ID:-${CLAUDE_SESSION_ID:-}}
    codex_session=${CODEX_THREAD_ID:-${CODEX_SESSION_ID:-}}
    if [ -n "$claude_session" ]; then
        export ONEPIPELINE_LAUNCHER=claude-code
        export ONEPIPELINE_LAUNCHER_SESSION="$claude_session"
    elif [ -n "$codex_session" ]; then
        export ONEPIPELINE_LAUNCHER=codex
        export ONEPIPELINE_LAUNCHER_SESSION="$codex_session"
    fi
fi

# Only a launch starts harnesses; a read-only view must not create a directory or
# refuse on an indirection it never uses.
case "${1:-}" in
    start | adopt)
        credentials_helper="$script_dir/credentials-env.sh"
        if [ ! -f "$credentials_helper" ] || [ ! -r "$credentials_helper" ]; then
            echo "onepipeline: required helper is not a readable regular file: $credentials_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
            exit 2
        fi
        # shellcheck source=scripts/credentials-env.sh
        if ! . "$credentials_helper"; then
            echo "onepipeline: the credentials helper at $credentials_helper is readable but could not be loaded; restore it from the repository or run 'just bootstrap', then retry" >&2
            exit 2
        fi
        export_host_credentials onepipeline || exit $?
        alt_config_helper="$script_dir/claude-alt-config-dir.sh"
        if [ ! -f "$alt_config_helper" ] || [ ! -r "$alt_config_helper" ]; then
            echo "onepipeline: required helper is not a readable regular file: $alt_config_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
            exit 2
        fi
        # shellcheck source=scripts/claude-alt-config-dir.sh
        . "$alt_config_helper"
        resolve_claude_alt_config_dir onepipeline || exit $?
        codex_alt_helper="$script_dir/codex-alt-home.sh"
        if [ ! -f "$codex_alt_helper" ] || [ ! -r "$codex_alt_helper" ]; then
            echo "onepipeline: required helper is not a readable regular file: $codex_alt_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
            exit 2
        fi
        # shellcheck source=scripts/codex-alt-home.sh
        . "$codex_alt_helper"
        ensure_codex_alt_home onepipeline || exit $?
        ask_manager_helper="$script_dir/ask-manager-env.sh"
        if [ ! -f "$ask_manager_helper" ] || [ ! -r "$ask_manager_helper" ]; then
            echo "onepipeline: required helper is not a readable regular file: $ask_manager_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
            exit 2
        fi
        # shellcheck source=scripts/ask-manager-env.sh
        . "$ask_manager_helper"
        export_ask_manager onepipeline || exit $?
        # A launch reads its plan out of a onetaskgraph project, which `onepipeline`
        # spawns the standalone CLI for. That CLI is installed into this checkout's
        # own `.venv/bin` — the environment `uv run` below puts first on the search
        # path — rather than a directory the whole host shares, so a fresh worktree or
        # publication clone arrives with none and the launch resolves whatever copy
        # some other checkout left on `PATH`, or nothing at all. Healed here because
        # this is the launching process and nothing else on this path runs Nx, whose
        # own wrapper performs the same self-heal for every target. A read-only view
        # reads no plan, so it stays outside this case exactly as the helpers above do.
        if ! "$script_dir/onetaskgraph-install.sh"; then
            echo "onepipeline: this checkout's plan store CLI could not be provisioned, and a launch reads its plan through it; the diagnostic above names the failing step, and 'just session-setup' performs the same install" >&2
            exit 2
        fi
        ;;
esac

# llmlint: ignore[tool_output_is_signal] This process is replaced by onepipeline, so what a run or a view reports is onepipeline's own to report; a line added here would corrupt the streams `monitor` and an attached launch are.
exec uv run onepipeline "$@"
