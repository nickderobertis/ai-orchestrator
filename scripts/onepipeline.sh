#!/usr/bin/env bash
# llmlint: ignore-file[changed_behavior_has_e2e] tests/e2e/launch_monitor_observer/test_orchestrate_launch_e2e.py drives a real launch and its planner verbs through this wrapper; tests/e2e/registry_and_recipes/test_delegated_recipes_e2e.py holds the command line each recipe renders through it.
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
#     `scripts/dispatch-env-hook.sh` — which the launch config every `start` names
#     declares as the engine's dispatch-env hook — runs the same ones immediately before each node-scope
#     dispatch, so an indirection a routing change adds while a run is live reaches
#     its next dispatch rather than failing it (ai-orchestrator#1109).
#   * A launch also carries ONEVCS_LOCK_TIMEOUT_SECONDS, derived by
#     `scripts/lock-timeout.sh` from how long this identity's gate last took: the
#     `onevcs` the engine links is what a dispatch publishes through, and a
#     `local-direct` publication holds the merge-queue lock for the whole of that gate
#     (ai-orchestrator#1164).
#   * A launch says which engine it runs: one line on stderr naming the release
#     `config/onepipeline.version` requests and the version the binary about to run
#     reports, so a proof meant for another engine can be read against the pair it
#     really ran on (ai-orchestrator#1217). Printed only; nothing here installs,
#     restores, or refuses on a mismatch.
#   * A launch's environment is **built** rather than inherited. The engine hands a
#     dispatch whatever the driver started with, so a planner session's `VIRTUAL_ENV`
#     — the canonical checkout's `.venv` — used to reach every worker, and an ordinary
#     `uv pip install` in a worktree wrote into the environment every concurrent node
#     and the manager share (ai-orchestrator#1162). `scripts/dispatch-env.sh` carries
#     the whitelist and `construct_dispatch_environment` applies it, here, in the
#     launch arms alone: the engine's dispatch-env hook can only *add* to a driver's
#     environment, so this is the last place a name can be taken out of one, and a
#     read-only view keeps the environment it was given.
#
# A launch also names this host's launch config, `config/onepipeline.launch.yaml`, which
# states every launch-level setting the engine reads from one — the bus configuration, the
# dispatch-env hook, the pool-maintenance schedule, the template root, the rendered-only
# check, the run-end hooks, the drafting graph, the launch check and the plan-edit hook — so no flag for
# any of them is injected here; the document's header says what each is for.
#
# A launch also has to carry ORCHESTRATOR_ASK_MANAGER, the path of
# `scripts/ask-manager.sh`, which a dispatched agent asks its manager through — the
# engine's own `onepipeline ask`. It is established here for the same reason the two above
# are: it reaches a dispatch only by inheritance, so the launching process is the
# last place that can put it there, and `start` and `adopt` are every shape of launch
# this repository has. `scripts/ask-manager-env.sh` owns the path and the refusal.
#
# For the same reason a launch carries `ORCHESTRATOR_FOLLOW_UP_DRAFT`, the command every
# party drafts a non-blocking follow-up with.
#
# A `plan check` is given the rendered-only answer its project launches under, chosen per
# project: `false` for a spikes project, which launches under
# `config/onepipeline.workflows.yaml`, and `true` for every other, which launches under
# `config/onepipeline.launch.yaml`. A spikes project is one whose own record, read from the
# plan store, carries `onetaskgraph.template` provenance whose `template` is this checkout's
# `templates/planning/spikes.md.j2`, absolute as the store records it; its name decides
# nothing. That per-project injection is an interim workaround, restating the workflows
# document's `require_rendered` here because `plan check` takes no `--launch-config`;
# https://github.com/nickderobertis/onepipeline/issues/1782 retires it. Until then the
# `plan check` journeys in `tests/e2e/launch_monitor_observer/test_onetaskgraph_host_e2e.py`
# read both documents' `require_rendered` and fail when either stops being the answer
# injected here.
#
# Who is acting is `scripts/launcher-session.sh`'s to answer, sourced here and by
# `scripts/telemetry-server.sh`, which hands the same answer to the read API so a
# mutation from the browser is performed as the session that started the server.
set -euo pipefail
# Session setup provisions this checkout, so no `uv run` here syncs it, however it is run.
export UV_NO_SYNC=1

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

# Who is acting, through the one definition of that ladder. Sourced rather than
# restated: `scripts/telemetry-server.sh` hands the same answer to the read API as
# its `--session`, and two spellings of it is how a browser's stop is refused while
# this terminal's is not.
# shellcheck source=scripts/launcher-session.sh
# llmlint: ignore[boundary_inputs_validated, robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at.
. "$script_dir/launcher-session.sh"

# Every verb names this host's template root, so `template list`, `resolve` and `check`
# and a launch's `start` and `adopt` read one registration; `scripts/template-env.sh` owns
# the name and the refusal. A launch establishes it again through the dispatch-environment
# resolvers below, which is how the dispatch-env hook carries it to every dispatch.
# shellcheck source=scripts/template-env.sh
# llmlint: ignore[boundary_inputs_validated, robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at.
. "$script_dir/template-env.sh"
export_template_root onepipeline || exit "$?"

# Only a launch starts harnesses; a read-only view must not create a directory or
# refuse on an indirection it never uses. `launching` carries this arm's answer below, so
# which verbs are a launch is decided here once.
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
        # Then build the launch's environment from that helper's whitelist — after the
        # resolvers, because what they established is kept by name.
        construct_dispatch_environment onepipeline || exit $?
        # And the merge-queue bound the engine's linked `onevcs` publishes under; the
        # header says why, and `scripts/lock-timeout.sh` how it is derived.
        # shellcheck source=scripts/lock-timeout.sh
        # llmlint: ignore[boundary_inputs_validated, robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at.
        . "$script_dir/lock-timeout.sh"
        export_lock_timeout onepipeline || exit $?
        # shellcheck source=scripts/ask-manager-env.sh
        # llmlint: ignore[boundary_inputs_validated, robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at.
        . "$script_dir/ask-manager-env.sh"
        export_ask_manager onepipeline || exit $?
        ;;
esac

# The three landing verbs are no launch, but each lands through the same merge-queue
# lock a dispatch's publication waits on, so each carries its bound. The two that
# publish also spend a drafting turn under `oneharness.pr-author.toml`, whose chain's
# `env_from` names the same identity indirections a dispatch's do, so they carry those
# too; `integrate` drafts nothing and carries nothing else a launch does.
#
# The two review verbs carry both for the same reasons. `review` drafts each owed reply
# under `oneharness.review-reply.toml`, and both readjust a run's drafts first — a restack
# that pushes through that same lock, and that dispatches the draft's own worker where
# the merge conflicts — so on a run nothing drives they do in this process what a driver
# would do with a launch's environment.
if [ "${1:-}" = publish-branch ] || [ "${1:-}" = repo-recover ] \
    || [ "${1:-}" = review ] || [ "${1:-}" = readjust ]; then
    # shellcheck source=scripts/dispatch-env.sh
    # llmlint: ignore[boundary_inputs_validated, robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at.
    . "$script_dir/dispatch-env.sh"
    export_dispatch_environment onepipeline || exit $?
fi
if [ "${1:-}" = publish-branch ] || [ "${1:-}" = repo-recover ] || [ "${1:-}" = integrate ] \
    || [ "${1:-}" = review ] || [ "${1:-}" = readjust ]; then
    # shellcheck source=scripts/lock-timeout.sh
    # llmlint: ignore[boundary_inputs_validated, robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at.
    . "$script_dir/lock-timeout.sh"
    export_lock_timeout onepipeline || exit $?
fi

# The follow-up drafting command every dispatch and the monitor draft with, this checkout's
# own `scripts/follow-up-draft.sh` over `onefollowup draft`; it reaches a dispatch only by
# inheritance, so the launch is the last place that can put it there. Beside it, the owner
# `onefollowup.yaml`'s review sink names in the context it hands the engine — the launching
# manager's session, at onefollowup's environment layer — without which onefollowup refuses
# every verb, a dispatch's draft and a follow-up run's settle among them.
if [ "$launching" = true ]; then
    export ORCHESTRATOR_FOLLOW_UP_DRAFT="$script_dir/follow-up-draft.sh"
    if [ -z "${ONEFOLLOWUP_HOOKS__OWNER__SESSION:-}" ] && [ -n "${ONEPIPELINE_LAUNCHER_SESSION:-}" ]; then
        export ONEFOLLOWUP_HOOKS__OWNER__SESSION="$ONEPIPELINE_LAUNCHER_SESSION"
    fi
fi

# Which `onepipeline` every verb runs: the binary `bun.lock` installs from npmjs.com into
# this checkout's `node_modules`, at the release `config/onepipeline.version` pins, by
# absolute path through its installed command. Its environment is built above so that no
# checkout's `VIRTUAL_ENV` reaches a dispatch, and `scripts/dispatch-env.sh` puts this
# checkout's `.venv/bin` and `node_modules/.bin` on the PATH it builds. A read-only view
# gets the checkout's `.venv/bin` in front of its PATH here instead, so a CLI the engine
# spawns — `oneagentgraph health` among them — is this checkout's pinned one. A `flow` is
# left the PATH it was given, as a launch is: its program is a launcher that resolves its
# own tools, and that PATH is the caller's to choose.
engine="${script_dir%/scripts}/node_modules/.bin/onepipeline"
if [ ! -x "$engine" ]; then
    echo "onepipeline: this checkout has no executable engine at $engine; run 'just bootstrap' to install the pinned releases from bun.lock, then retry" >&2
    exit 2
fi
[ "$launching" = true ] || [ "${1:-}" = flow ] || export PATH="${script_dir%/scripts}/.venv/bin${PATH:+:$PATH}"
if [ "$launching" = true ]; then
    # The engine this launch requests and the one it is about to run, as one line on
    # stderr — stdout is what `monitor` prints and where an attached launch writes its
    # settlement record. Printed, never acted on: whether the two should differ is the
    # manager's call (the header says why), so a mismatch refuses nothing and a binary
    # that cannot say its version is reported as that. Both values are read from outside
    # this script, so they are cut to one line of printable text before they reach a
    # terminal.
    engine_pin_file="${script_dir%/scripts}/config/onepipeline.version"
    requested_engine=unknown
    if [ -r "$engine_pin_file" ]; then
        read -r requested_engine <"$engine_pin_file" || true
        requested_engine=${requested_engine:-unknown}
    fi
    if reported_engine=$("$engine" --version 2>/dev/null) && [ -n "$reported_engine" ]; then
        # `onepipeline 0.40.0` names the binary before its version; the version is the
        # last word of its first line, and the whole line is kept if it has only one.
        reported_engine=${reported_engine%%$'\n'*}
        reported_engine=${reported_engine##* }
    else
        reported_engine="unknown (it did not answer --version)"
    fi
    requested_engine=${requested_engine//[[:cntrl:]]/}
    requested_engine=${requested_engine:0:80}
    reported_engine=${reported_engine//[[:cntrl:]]/}
    reported_engine=${reported_engine:0:80}
    echo "onepipeline: engine requested ${requested_engine} (config/onepipeline.version), about to run ${reported_engine} ($engine)" >&2
fi

# Every launch names this host's launch config, `config/onepipeline.launch.yaml`, and no
# flag that document carries: the bus configuration, the dispatch-env hook, the
# pool-maintenance schedule, the template root, the rendered-only check, the run-end hooks,
# the drafting graph, the launch check and the plan-edit hook are the document's to state, and the engine
# reads them once at the launch and retains them in the launch record. A caller who names a
# setting's own flag keeps theirs — the engine lets a flag beat the document's key, a blank
# one included — and one who names a launch config of their own keeps that instead. `start`
# alone, because `adopt` replays the launch record.
#
# A `start` runs from this checkout's root, because that is the launch directory the
# engine resolves the document's relative paths against and spawns its hooks in; a caller's
# own references are already spelled from there (`graphs/dag-scope.yaml`, a project id).
if [ "${1:-}" = start ]; then
    named_launch_config=false
    for argument in "${@:2}"; do
        case "$argument" in
            --launch-config | --launch-config=*) named_launch_config=true ;;
        esac
    done
    if [ "$named_launch_config" = false ]; then
        set -- start --launch-config "${script_dir%/scripts}/config/onepipeline.launch.yaml" "${@:2}"
    fi
    # llmlint: ignore[changed_behavior_has_e2e] Reachable only when the checkout this script was just resolved inside stops being enterable mid-run; a journey producing that would have to remove the checkout it is running from.
    cd -- "${script_dir%/scripts}" || {
        echo "onepipeline: could not enter the checkout at ${script_dir%/scripts}, which is the launch directory its launch config is resolved against; run the launch from a readable checkout, then retry" >&2
        exit 2
    }
fi

# A flow keeps a planner channel of its own, which `ask` raises a launcher's questions on
# when no run is named, so it is kept under the same bus configuration a run's is.
# `just plan` and `just finish-plan` run one. Only the flags before `--` are the verb's;
# everything after it is the program the flow runs, so a `--bus-config` there is not this
# one. A caller who names one keeps theirs.
if [ "${1:-} ${2:-}" = "flow run" ]; then
    named_bus_config=false
    for argument in "${@:3}"; do
        case "$argument" in
            --) break ;;
            --bus-config | --bus-config=*) named_bus_config=true ;;
        esac
    done
    if [ "$named_bus_config" = false ]; then
        set -- flow run --bus-config "${script_dir%/scripts}/config/onemessagebus.yaml" "${@:3}"
    fi
fi

# `plan check` reads no launch config, so it is given its project's answer, as the header
# says, in the forms this host's own callers name it in. Any other form, a store read that
# fails, or a record without the spikes template's provenance keeps `true`, so nothing
# that goes wrong here weakens the check, and the engine's own load then reports a project
# it cannot read. A caller who names the flag, or
# exports ONEPIPELINE_REQUIRE_RENDERED, keeps their answer — on a `start` as well, where the
# engine lets either beat the document.
plan_loading_words=0
case "${1:-} ${2:-}" in
    "start "*) plan_loading_words=1 ;;
    "plan check") plan_loading_words=2 ;;
esac
if [ "$plan_loading_words" -gt 0 ]; then
    case "${ONEPIPELINE_REQUIRE_RENDERED:-}" in
        "" | true | false) ;;
        *)
            echo "onepipeline: ONEPIPELINE_REQUIRE_RENDERED is '${ONEPIPELINE_REQUIRE_RENDERED//[[:cntrl:]]/}', which is neither true nor false, so whether this plan is held to its tasks being their template's rendering cannot be read from it; remove it from the environment, which holds it, then retry" >&2
            exit 2
            ;;
    esac
fi
if [ "$plan_loading_words" -eq 2 ] && [ -z "${ONEPIPELINE_REQUIRE_RENDERED:-}" ]; then
    named_require_rendered=false
    for argument in "${@:3}"; do
        case "$argument" in
            --require-rendered | --require-rendered=*) named_require_rendered=true ;;
        esac
    done
    # Only the forms this host's own callers produce are read: `just check-plan`'s, a bare
    # `plan check <project>`, and the planning and finishing flows' `check` steps, the same
    # followed by `--check <path>` pairs. Any other form — an option before the project,
    # any other option after it — leaves the project unread, which keeps `true`.
    checked_project=""
    if [ "$#" -ge 3 ] && [ "${3#-}" = "$3" ]; then
        recognized_form=true
        for ((check_index = 4; check_index <= $#; check_index += 2)); do
            check_value_index=$((check_index + 1))
            if [ "${!check_index}" != --check ] || [ "$check_value_index" -gt "$#" ] \
                || [ "${!check_value_index#-}" != "${!check_value_index}" ]; then
                recognized_form=false
                break
            fi
        done
        [ "$recognized_form" = false ] || checked_project=$3
    fi
    if [ "$named_require_rendered" = false ]; then
        require_rendered=true
        if [ -n "$checked_project" ]; then
            spikes_template="${script_dir%/scripts}/templates/planning/spikes.md.j2"
            # llmlint: ignore[tool_output_is_signal] A failed read is answered by keeping `true`, the strict check, and the engine's own load of the same project reports why the store could not read it, so its stderr here would only say it twice.
            recorded_template=$("${script_dir%/scripts}/.venv/bin/onetaskgraph" project show "$checked_project" --json --no-interactive 2>/dev/null |
                jq -r '.items[0].item.metadata["onetaskgraph.template"].template // empty' 2>/dev/null) || recorded_template=""
            if [ "$recorded_template" = "$spikes_template" ]; then
                require_rendered=false
            fi
        fi
        set -- plan check --require-rendered "$require_rendered" "${@:3}"
    fi
fi

# Every verb runs the binary resolved above; a launch's line above reported it.
# llmlint: ignore[tool_output_is_signal] This process is replaced by onepipeline, so what a run or a view reports is onepipeline's own to report; a line added here would corrupt the streams `monitor` and an attached launch are.
# `execfail` so that an engine the check above found executable and the kernel still
# refuses — a missing interpreter, a wrong architecture — is explained here rather than
# by the shell's bare error. errexit is off so the line after a failed exec runs.
shopt -s execfail
set +e
exec "$engine" "$@"
exec_status=$?
echo "onepipeline: $engine is executable but could not be run (status $exec_status); run 'just bootstrap' to reinstall the pinned releases from bun.lock, then retry" >&2
exit "$exec_status"
