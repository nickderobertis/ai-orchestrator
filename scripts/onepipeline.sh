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
#     under those configs. They are established twice by one definition:
#     `scripts/dispatch-env.sh` runs the resolvers here, at driver start, and
#     `scripts/dispatch-env-hook.sh` — which every `start` names as the engine's
#     dispatch-env hook, once the installed engine carries that flag — runs the
#     same ones immediately before each node-scope dispatch, so an indirection a
#     routing change adds while a run is live reaches its next dispatch rather
#     than failing it (ai-orchestrator#1109).
#
# A launch also has to carry ORCHESTRATOR_ASK_MANAGER, the path of
# `scripts/ask-manager.sh`, the shim a dispatched agent asks through `onemessagebus ask`
# with. It is established here for the same reason the two above
# are: it reaches a dispatch only by inheritance, so the launching process is the
# last place that can put it there, and `start` and `adopt` are every shape of launch
# this repository has. `scripts/ask-manager-env.sh` owns the path and the refusal.
#
# For the same reason a launch carries the follow-up drafting seam: the absolute root of
# the `drafts` plan source and the command every party drafts a non-blocking follow-up
# with. `scripts/follow-up-env.sh` owns both names, both values and the refusal. It runs
# after the design-approval gate below: the root is resolved through the plan-store CLI
# healed before that gate, and a store that cannot be read at all is the gate's to report,
# under its own exit status, rather than this seam's.
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
# refuse on an indirection it never uses. `launching` carries this arm's answer past the
# design-approval gate below, so which verbs are a launch is decided here once.
launching=false
case "${1:-}" in
    start | adopt)
        launching=true
        # The credentials, every Claude identity's config directory and the alternate
        # Codex home, through the one definition of which resolvers establish them —
        # the same definition the dispatch-env hook named below re-runs before every
        # node-scope dispatch, so a driver's environment and a dispatch's are one list.
        dispatch_env_helper="$script_dir/dispatch-env.sh"
        if [ ! -f "$dispatch_env_helper" ] || [ ! -r "$dispatch_env_helper" ]; then
            echo "onepipeline: required helper is not a readable regular file: $dispatch_env_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
            exit 2
        fi
        # shellcheck source=scripts/dispatch-env.sh
        if ! . "$dispatch_env_helper"; then
            echo "onepipeline: the helper at $dispatch_env_helper is readable but could not be loaded; restore it from the repository or run 'just bootstrap', then retry" >&2
            exit 2
        fi
        if ! declare -F export_dispatch_environment >/dev/null; then
            echo "onepipeline: the helper at $dispatch_env_helper loaded but defines no export_dispatch_environment; restore it from the repository or run 'just bootstrap', then retry" >&2
            exit 2
        fi
        export_dispatch_environment onepipeline || exit $?
        ask_manager_helper="$script_dir/ask-manager-env.sh"
        if [ ! -f "$ask_manager_helper" ] || [ ! -r "$ask_manager_helper" ]; then
            echo "onepipeline: required helper is not a readable regular file: $ask_manager_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
            exit 2
        fi
        # shellcheck source=scripts/ask-manager-env.sh
        . "$ask_manager_helper"
        export_ask_manager onepipeline || exit $?
        ;;
esac

# What a plan is put in front of a person as is its design document, and their approval
# of that document is what gates dispatch. So a launch asks before it dispatches: this is
# the only place every shape of `just orchestrate` passes through, and refusing here is
# what makes the gate a gate rather than a habit. `adopt` is deliberately outside it —
# adoption attaches a fresh driver to a run whose plan was already gated when it started,
# and there is no project on its command line to ask about.
#
# The launch's own arguments are handed over as they were typed rather than parsed here;
# `orchestrator/design_approval.py` states why, and which of them it then asks the store
# about. Its exit status is carried out unchanged: 1 is the refusal, 2 is a plan store
# that could not be read at all.
if [ "${1:-}" = start ]; then
    uv run orchestrator-launch-gate "${@:2}" || exit "$?"
fi

# The follow-up drafting seam, after the gate and for the launch shapes the arm above
# decided; the header says why it waits for the gate.
if [ "$launching" = true ]; then
    follow_up_helper="$script_dir/follow-up-env.sh"
    if [ ! -f "$follow_up_helper" ] || [ ! -r "$follow_up_helper" ]; then
        echo "onepipeline: required helper is not a readable regular file: $follow_up_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
        exit 2
    fi
    # shellcheck source=scripts/follow-up-env.sh
    if ! . "$follow_up_helper"; then
        echo "onepipeline: the follow-up drafting helper at $follow_up_helper is readable but could not be loaded; restore it from the repository or run 'just bootstrap', then retry" >&2
        exit 2
    fi
    export_follow_up_drafts onepipeline || exit "$?"
fi

# Every launch names the dispatch-env hook, `scripts/dispatch-env-hook.sh` by absolute
# path, so each node-scope dispatch is handed an environment resolved at that moment.
# `start` alone, as `--bus-config` is, because `adopt` replays the launch record; a
# caller who names one keeps it, a blank value included. Named exactly when the
# installed engine's own `start --help` lists the flag — a release before the hook
# refuses it as an unknown argument, which would refuse every launch — and asked as a
# positive question the way scripts/watch-run.sh asks for a verb, so an engine that
# cannot be asked is reported as that rather than read as lacking the flag. Prepended
# before the bus configuration so the rendered line reads `start --bus-config …
# --dispatch-env-hook …`.
if [ "${1:-}" = start ]; then
    named_dispatch_env_hook=false
    for argument in "${@:2}"; do
        case "$argument" in
            --dispatch-env-hook | --dispatch-env-hook=*) named_dispatch_env_hook=true ;;
        esac
    done
    if [ "$named_dispatch_env_hook" = false ]; then
        if ! start_help=$(uv run onepipeline start --help 2>&1); then
            said=$(printf '%s' "$start_help" | tr -d '\000-\010\013\014\016-\037\177' | tr '\n' ' ' | cut -c1-500)
            echo "onepipeline: the onepipeline installed here could not be asked what 'start' takes, so this cannot tell whether it runs a dispatch-env hook. It said: ${said:-nothing at all}. Repair the installation — 'just bootstrap' reinstalls the pinned releases — and retry." >&2
            exit 2
        fi
        if grep -qE -- '^[[:space:]]+--dispatch-env-hook([[:space:]]|$)' <<<"$start_help"; then
            dispatch_env_hook="$script_dir/dispatch-env-hook.sh"
            if [ ! -f "$dispatch_env_hook" ] || [ ! -x "$dispatch_env_hook" ]; then
                echo "onepipeline: the dispatch-env hook is not an executable file at $dispatch_env_hook, so a run launched now would refuse every dispatch it made; restore it from the repository and 'chmod +x' it, then retry" >&2
                exit 2
            fi
            set -- start --dispatch-env-hook "$dispatch_env_hook" "${@:2}"
        fi
    fi
fi

# And every launch keeps its channel under this host's bus configuration, so the engine applies
# the same validators, author grants and codec constants that `just channel-reply`, the ask
# shim and the observer's judge side read. Named here because `start` is the one verb every
# launch shape reaches — `just orchestrate`, `just plan` and the design-document launch —
# and `adopt` takes none because an adopted run keeps the configuration its launch record
# retained. A caller who names one keeps theirs.
if [ "${1:-}" = start ]; then
    named_bus_config=false
    for argument in "${@:2}"; do
        case "$argument" in
            --bus-config | --bus-config=*) named_bus_config=true ;;
        esac
    done
    if [ "$named_bus_config" = false ]; then
        set -- start --bus-config "${script_dir%/scripts}/config/onemessagebus.yaml" "${@:2}"
    fi
fi

# llmlint: ignore[tool_output_is_signal] This process is replaced by onepipeline, so what a run or a view reports is onepipeline's own to report; a line added here would corrupt the streams `monitor` and an attached launch are.
exec uv run onepipeline "$@"
