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
#     dispatch-env hook — runs the same ones immediately before each node-scope
#     dispatch, so an indirection a routing change adds while a run is live reaches
#     its next dispatch rather than failing it (ai-orchestrator#1109).
#
# A launch also names the pool-maintenance schedule, `config/onepipeline.maintenance.yaml`,
# as `--maintenance-config`, so an idle driver maintains every registered identity's
# warm worktree slots on this host's cadence; the file's header says what the engine
# does with it. Both flags are named on every `start` without asking the engine first:
# every engine `config/onepipeline.version` admits carries them, and
# `tests/test_engine_contracts.py` holds the pin.
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
# Who is acting is `scripts/launcher-session.sh`'s to answer, sourced here and by
# `scripts/telemetry-server.sh`, which hands the same answer to the read API so a
# mutation from the browser is performed as the session that started the server.
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

# Who is acting, through the one definition of that ladder. Sourced rather than
# restated: `scripts/telemetry-server.sh` hands the same answer to the read API as
# its `--session`, and two spellings of it is how a browser's stop is refused while
# this terminal's is not.
# shellcheck source=scripts/launcher-session.sh
# llmlint: ignore[boundary_inputs_validated, robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at.
. "$script_dir/launcher-session.sh"

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
        # shellcheck source=scripts/dispatch-env.sh
        # llmlint: ignore[boundary_inputs_validated, robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at.
        . "$script_dir/dispatch-env.sh"
        export_dispatch_environment onepipeline || exit $?
        # shellcheck source=scripts/ask-manager-env.sh
        # llmlint: ignore[boundary_inputs_validated, robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at.
        . "$script_dir/ask-manager-env.sh"
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
    # shellcheck source=scripts/follow-up-env.sh
    # llmlint: ignore[boundary_inputs_validated, robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at.
    . "$script_dir/follow-up-env.sh"
    export_follow_up_drafts onepipeline || exit "$?"
fi

# Every launch names the dispatch-env hook, `scripts/dispatch-env-hook.sh` by absolute
# path, so each node-scope dispatch is handed an environment resolved at that moment —
# and the pool-maintenance schedule, `config/onepipeline.maintenance.yaml` by absolute
# path, so an idle driver sweeps every registered identity's warm worktree slots on
# this host's cadence. `start` alone, as `--bus-config` is, because `adopt` replays the
# launch record; a caller who names one keeps it, a blank value included, and each is
# kept per flag. Prepended before the bus configuration so the rendered line reads
# `start --bus-config … --dispatch-env-hook … --maintenance-config …`.
if [ "${1:-}" = start ]; then
    named_dispatch_env_hook=false
    named_maintenance_config=false
    for argument in "${@:2}"; do
        case "$argument" in
            --dispatch-env-hook | --dispatch-env-hook=*) named_dispatch_env_hook=true ;;
            --maintenance-config | --maintenance-config=*) named_maintenance_config=true ;;
        esac
    done
    # The schedule first, so the hook prepended after it lands ahead of it.
    if [ "$named_maintenance_config" = false ]; then
        set -- start --maintenance-config "${script_dir%/scripts}/config/onepipeline.maintenance.yaml" "${@:2}"
    fi
    if [ "$named_dispatch_env_hook" = false ]; then
        set -- start --dispatch-env-hook "$script_dir/dispatch-env-hook.sh" "${@:2}"
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
