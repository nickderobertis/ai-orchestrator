"""The throwaway checkout the delegated-recipe journeys run the real recipes in.

Shared by `tests/e2e/test_delegated_recipes_e2e.py`, the recipe tier's delegation table,
and by the one delegated journey whose answer moves with an `orchestrator/` module rather
than with `scripts/` alone: `just follow-ups`, whose journey is in
`tests/plan_tooling/test_follow_ups_recipe_e2e.py` because that project's key already
carries `orchestrator/follow_up_tickets.py`, so an edit to that module re-runs one tier
rather than every recipe journey. One definition, so the two tiers build the same checkout
and a change to it reaches both.

Only `uv` and the engine a launch reaches are doubled here, as the delegation table's
module docstring states: the recipes, the wrapper scripts and the shell they run in are
real.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import follow_up_variables
import plan_root_variable

ROOT = Path(__file__).resolve().parents[1]

#: Copied into the checkout so a recipe that delegates through a script finds it.
WRAPPER_SCRIPTS = (
    "planner-surface.sh",
    "new-persona.sh",
    "telemetry-server.sh",
    "smoke.sh",
    # The trust probe's two JSON steps, which `smoke.sh` runs.
    "smoke-probe.py",
    # Every `onepipeline` recipe goes through this one, which is where the planner's
    # identity and a launch's harness environment are established.
    "onepipeline.sh",
    # The acting-session ladder both that one and `telemetry-server.sh` source, so a
    # copied checkout can run either of them at all — and so the `--session` the read
    # API is handed is derived the way a real invocation derives it rather than by a
    # variable this suite happened to inherit.
    "launcher-session.sh",
    # `just orchestrate` adds this host's defaults through the first, and names the second
    # as both run-end hooks, refusing a checkout where it could not run.
    "orchestrate.sh",
    "run-ended.sh",
    # `just plan` writes its one-node plan through this one, and refuses to launch at
    # all unless the wrapper the seam names is there for the planner to ask questions
    # with. Every launch takes that seam, `onepipeline.sh` included, so the helper that
    # establishes it and the wrapper it names are both part of a runnable checkout.
    "plan.sh",
    # `just plan` hands over to this one once its planner has settled, and `just
    # finish-plan` is it directly: review the plan, check it, launch the document, copy
    # both up, report where they landed. The third is the grammar both of them read a
    # brief and their shared options through, and the fourth is the review step itself.
    "finish-plan.sh",
    "plan-brief.sh",
    "review-plan.sh",
    "credentials-env.sh",
    # Every board command goes through this one, which is where this checkout's own
    # credential file is read for a command that is not a launch.
    "plan-store.sh",
    "ask-manager-env.sh",
    "ask-manager.sh",
    # `just plan` writes its project under the plan-authoring root this one resolves, so
    # a checkout without it cannot reach a verb at all. The root itself is stated in the
    # environment below rather than discovered, which keeps that resolution — and the
    # project the launch writes — inside this throwaway checkout.
    "plan-root-env.sh",
    # Every launch also exports the follow-up drafting seam: the `drafts` source's root,
    # stated in the environment below for the reason the plan-authoring root is, and the
    # drafting command this helper refuses a launch without. `just follow-up` goes through
    # the third, which establishes that seam and runs the command as the manager.
    "follow-up-env.sh",
    "follow-up-draft.sh",
    "follow-up.sh",
    # `just follow-ups` writes a one-node direct project and launches it through the same
    # wrapper, having counted the run's drafts under the root `follow-up-env.sh` exports.
    "follow-ups.sh",
    # And the operational appendix every dispatched task must carry, which `just plan`
    # hands its planner as text rather than as a path into a checkout that planner cannot
    # see. It reads `config/dispatch-appendix.md`, which `delegation_checkout`
    # copies beside it.
    "dispatch-appendix-env.sh",
    # The one definition of which resolvers establish a dispatch's environment, which
    # every launch runs at driver start and names — as the hook beside it, by absolute
    # path — for the engine to re-run before each node-scope dispatch. A checkout
    # without either is refused before it delegates.
    "dispatch-env.sh",
    "dispatch-env-hook.sh",
    # The bound every waiter for the merge-queue lock queues under, derived from how long
    # this identity's gate last took. `just integrate` sources it, and so does the launch
    # wrapper for a launch and for both landing verbs, so a checkout without it is one
    # none of them can run in.
    "lock-timeout.sh",
    "claude-alt-config-dir.sh",
    "codex-alt-home.sh",
    # `just smoke` runs the agent config outside any dispatch, so it makes the node
    # scratch directory the configs map a turn's runtime directory from.
    "node-scratch-dir.sh",
    # `just repos` goes through this one, which absorbs the flag spelling.
    "repos.sh",
    # `just recoverable` goes through this one, which re-renders the resume commands
    # `onevcs` prints in their `just` form.
    "recoverable.sh",
)


#: The brief `just plan` is given in the delegation table, written into each throwaway
#: checkout by `delegation_checkout`. Its filename is what the recipe derives an unnamed
#: run from, so it is the other half of the published line the first `plan` row asserts.
BRIEF = "briefs/cursor-shape.md"


#: The plan the brief declares, which the planner authors and every step after it is
#: about, in this checkout. Its native id differs from the run name `BRIEF` derives,
#: because a launch whose run name *is* that id is refused: the project the launch
#: writes and the plan its planner authors would be one record.
PLAN_PROJECT = "authoring:listing-cursor"

#: A brief whose declared plan project is the run name its own filename derives, which
#: is the launch `just plan` refuses before it writes or launches anything.
COLLIDING_BRIEF = "briefs/cursor-colour.md"
COLLIDING_PROJECT = "authoring:cursor-colour"

#: Where a row's command line names the checkout the recipe ran in. Each journey runs in a
#: throwaway checkout of its own, so the rows spell it as this and the comparison fills it.
CHECKOUT = "@CHECKOUT@"


def at(checkout: Path, line: str) -> str:
    """One expected command line, with the checkout placeholder filled in."""
    return line.replace(CHECKOUT, str(checkout.resolve()))


#: How a launch reaches the engine: the installed binary in this checkout's own `.venv`,
#: by absolute path, rather than `uv run onepipeline`. `uv run` activates the project
#: environment for the process it starts, so it exports `VIRTUAL_ENV` naming this
#: checkout's `.venv` into the engine — and the engine hands its environment to every
#: dispatch, which is how a worker's own `uv pip install` came to write into the
#: environment every concurrent node shares (ai-orchestrator#1162). A read-only view
#: still goes through `uv run`, which is why the rows below are not all one spelling.
ENGINE = f"{CHECKOUT}/.venv/bin/onepipeline"

#: The bus configuration `scripts/onepipeline.sh` hands every launch that names none,
#: immediately after `start`, as the copied checkout's own absolute path.
BUS_CONFIG = f"--bus-config {CHECKOUT}/config/onemessagebus.yaml"
#: The dispatch-env hook the same wrapper hands every launch that names none, after the
#: bus configuration, as the copied checkout's own absolute path: the engine spawns it
#: from the launch record's directory, so a relative one would name nothing there.
DISPATCH_ENV_HOOK = f"--dispatch-env-hook {CHECKOUT}/scripts/dispatch-env-hook.sh"
#: The pool-maintenance schedule the same wrapper hands every launch that names none,
#: after the hook, as the copied checkout's own absolute path for the same reason.
MAINTENANCE_CONFIG = f"--maintenance-config {CHECKOUT}/config/onepipeline.maintenance.yaml"
#: All three, in the order the wrapper renders them, which every launch a caller named
#: none of carries. Named on every `start` without asking the engine whether it takes
#: them: every engine `config/onepipeline.version` admits does.
DEFAULTS = f"{BUS_CONFIG} {DISPATCH_ENV_HOOK} {MAINTENANCE_CONFIG}"
#: The two flags `scripts/onepipeline.sh` adds after the bus configuration, and the
#: default it renders for each.
WRAPPER_DEFAULTS = {
    "--dispatch-env-hook": DISPATCH_ENV_HOOK,
    "--maintenance-config": MAINTENANCE_CONFIG,
}
#: How the wrapper asks the installed engine which release it is, before every `start`
#: and `adopt`: the second half of the one line it prints on stderr naming the engine
#: `config/onepipeline.version` requests and the one about to run (ai-orchestrator#1217).
ENGINE_VERSION = f"{ENGINE} --version"
#: Set in a journey's environment to have the traced engine report this release rather
#: than the pin, as an engine installed by hand for a pre-landing proof does.
ENGINE_REPORTS_ENV = "FAKE_ENGINE_REPORTS"
#: The release this checkout's `config/onepipeline.version` pins, and what the traced
#: engine reports unless a journey says otherwise. Written into the checkout rather than
#: copied, so a pin bump re-keys nothing here and the pair a journey reads is one it stated.
PINNED_ENGINE = "0.40.0"


#: Where `just finish-plan` reads the design document's template from, relative to the
#: checkout it runs in. Stated here rather than read out of `scripts/finish-plan.sh`, for
#: the reason every expectation in this suite is stated: a fixture that took its shape
#: from its subject would build whatever the subject asked for and prove nothing about it.
DESIGN_TEMPLATE = "config/design-doc-template.md"

#: The operational appendix `just plan` hands its dispatch, spelled here for the reason
#: the template above is: this suite states what a runnable checkout holds rather than
#: asking its subject.
DISPATCH_APPENDIX = "config/dispatch-appendix.md"


def delegation_checkout(tmp_path: Path) -> tuple[Path, Path]:
    """A checkout the real recipes run in, with `uv` traced and nothing else doubled."""
    checkout = tmp_path / "delegation"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "bin").mkdir()
    (checkout / "personas").mkdir()
    (checkout / "config").mkdir()
    shutil.copy2(ROOT / "justfile", checkout / "justfile")
    # The one source the wrappers read the read API's address from; a checkout
    # without it is not one these recipes can run in.
    shutil.copy2(ROOT / "config/read-api.address", checkout / "config/read-api.address")
    # The operational appendix `just plan` hands its dispatch. Written rather than copied,
    # for the reason the template below is: what a launch does with it — that it exports
    # the text and refuses a checkout carrying none — is this suite's subject, and what
    # the appendix *says* is `tests/test_dispatch_appendix.py`'s. Copying the tracked one
    # would also put these journeys outside the recipe key that memoizes them.
    (checkout / DISPATCH_APPENDIX).write_text(
        "## Additional info\n\n### Operational notes\n\nWork the branch and report.\n",
        encoding="utf-8",
    )
    # The engine pin the launch wrapper names as the release a launch requests.
    (checkout / "config/onepipeline.version").write_text(f"{PINNED_ENGINE}\n", encoding="utf-8")
    for name in WRAPPER_SCRIPTS:
        copied = checkout / "scripts" / name
        shutil.copy2(ROOT / "scripts" / name, copied)
        copied.chmod(0o755)
    # The template `just finish-plan` lends its `design-doc` node a criterion out of and
    # refuses the flow without. Written rather than copied, for the reason the brief below
    # is, and with both markers because a template that lends nothing is refused by name.
    # What the criterion *says* is `tests/plan_tooling/`'s to assert against a real
    # dispatch; these journeys are about where a recipe lands and what it delegates.
    template = checkout / DESIGN_TEMPLATE
    template.write_text(
        "# A template this journey states\n\n"
        "<!-- composed-into-the-dispatch -->\n"
        "- The criterion this flow lends its design-doc node.\n"
        "<!-- end composed-into-the-dispatch -->\n",
        encoding="utf-8",
    )
    # The brief `just plan` reads. Written rather than copied from `examples/`: these
    # journeys are memoized on `recipeWorkspace`, which no document under `examples/`
    # is in, so reading one here would replay a verdict recorded before it changed.
    brief = checkout / BRIEF
    brief.parent.mkdir()
    brief.write_text(
        "## What\nDecide the cursor's shape.\n\n"
        # The plan the launch's `design-doc` node reads. A brief without one is refused
        # before anything is delegated, which for every row below would be the wrong
        # ending: what they are about is where a recipe lands, not what a brief owes.
        f"Plan project: {PLAN_PROJECT}\n\n"
        "## Why\nThe view cannot deep-link "
        "without it.\n\n## Acceptance criteria\n- The shape is stated.\n"
    )
    (checkout / COLLIDING_BRIEF).write_text(
        "## What\nDecide the cursor's colour.\n\n"
        f"Plan project: {COLLIDING_PROJECT}\n\n"
        "## Why\nThe view cannot theme "
        "without it.\n\n## Acceptance criteria\n- The colour is stated.\n"
    )
    trace = checkout / "trace"
    uv = checkout / "bin/uv"
    # Records the whole command line, and the reply envelope when one is piped in:
    # a verdict recipe's product is the envelope, so a trace without it would say
    # nothing about the recipe under test.
    uv.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf 'uv %s\\n' "$*" >>"$TRACE_FILE"
if [ ! -t 0 ]; then
  while IFS= read -r line; do printf 'stdin %s\\n' "$line" >>"$TRACE_FILE"; done
fi
exit "${FAKE_UV_EXIT:-0}"
"""
    )
    uv.chmod(0o755)
    # The engine a **launch** reaches: the installed binary in this checkout's own
    # `.venv`, which is what `scripts/onepipeline.sh` execs rather than `uv run
    # onepipeline` so that the engine — and so every dispatch under it — is not handed
    # this checkout's `VIRTUAL_ENV` (ai-orchestrator#1162). Traced the same way `uv` is,
    # under its own name, and it answers the `--version` every launch asks for the pair
    # it prints, answering the pin unless a journey names a hand-installed release
    # instead.
    engine = checkout / ".venv/bin/onepipeline"
    engine.parent.mkdir(parents=True, exist_ok=True)
    engine.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s %s\\n' "$0" "$*" >>"$TRACE_FILE"
if [ "$*" = "--version" ]; then
  echo "onepipeline ${{{ENGINE_REPORTS_ENV}:-{PINNED_ENGINE}}}"
  exit 0
fi
exit "${{FAKE_ENGINE_EXIT:-0}}"
"""
    )
    engine.chmod(0o755)
    return checkout, trace


def run_recipe(
    checkout: Path,
    trace: Path,
    *args: str,
    stdin: str | None = None,
    env: dict[str, str] | None = None,
    stdin_fd: int | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{checkout / 'bin'}{os.pathsep}{environment['PATH']}"
    environment["TRACE_FILE"] = str(trace)
    # A scratch root inside the throwaway checkout, so nothing a row runs writes into
    # the host's own `/tmp`.
    scratch = checkout / "scratch-root"
    scratch.mkdir(exist_ok=True)
    environment["TMPDIR"] = str(scratch)
    # The plan-authoring root `just plan` writes its project under. Stated rather than
    # left to be discovered, and inside this checkout: discovery reads the configuration
    # and the package of whichever tree answers, which for a checkout carrying neither is
    # the one this suite runs in — so an unstated root would put these rows' projects into
    # the real plan store and make their verdict depend on a tree they do not copy.
    # Named as the layout this repository configures — a `.plans` at the checkout's own
    # root — so a journey reading back what the recipe wrote reads the path it always did.
    plans = checkout / ".plans"
    plans.mkdir(exist_ok=True)
    environment[plan_root_variable.name()] = str(plans)
    # The follow-up drafts root every launch exports, stated for the same reason: an
    # unstated one resolves to the real `.follow-ups` of the tree this suite runs in. The
    # plugin and the command are the helper's to export and are cleared, so an enclosing
    # launch's values are not what these rows measure.
    root, plugin, command = follow_up_variables.all_names()
    follow_ups = checkout / ".follow-ups"
    follow_ups.mkdir(exist_ok=True)
    environment[root] = str(follow_ups)
    environment.pop(plugin, None)
    environment.pop(command, None)
    environment.pop("ONEPIPELINE_RUNS_DIR", None)
    # This suite is itself run from inside a dispatch, whose real status directory and
    # history store would otherwise reach the recipe under test. Each journey states
    # the values it wants, and the default is the operator case: none of them.
    for inherited in (
        "ONEHARNESS_CONFIG",
        "ONEHARNESS_HISTORY_DIR",
        "ONEHARNESS_HISTORY_LABELS",
        # The acting session, for the same reason and with sharper consequences: this
        # suite runs inside a dispatch that exports one, and `scripts/launcher-session.sh`
        # honours an already-exported value — so an inherited one would put the launching
        # manager's session into the command line every row below asserts, and every
        # verdict about identity here would be about whoever ran the suite.
        "ONEPIPELINE_LAUNCHER",
        "ONEPIPELINE_LAUNCHER_SESSION",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_SESSION_ID",
        "CODEX_THREAD_ID",
        "CODEX_SESSION_ID",
    ):
        environment.pop(inherited, None)
    environment.update(env or {})
    return subprocess.run(
        ["just", *args],
        cwd=checkout,
        env=environment,
        check=False,
        text=True,
        capture_output=True,
        input=stdin,
        # A descriptor a journey opened stands for a stdin the recipe reads from; otherwise
        # piped text, or nothing at all.
        stdin=stdin_fd
        if stdin_fd is not None
        else None
        if stdin is not None
        else subprocess.DEVNULL,
    )
