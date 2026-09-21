"""Every delegated recipe reaches the published CLI it promises, as it promises.

This repository is a configuration layer over four published CLIs, and the `just`
recipes are the seam an operator and a planner actually touch: the names and
argument shapes are what every habit and every doc reference here depends on, and
they are meant to outlive the implementation behind them. What a wrapper owes is
exactly one thing — that the verb it names is reached, with the arguments the
recipe promised — and that is what these journeys hold it to.

They run the real `just` recipes and the real wrapper scripts. Only the published
CLI itself is doubled, at the boundary below the seam under test: each engine is
proven in its own repository, and running a real `onepipeline start` here would
launch agents.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] Every
test in this module is `reads_recipes`, so the property is file-wide rather than about any
one row. That marker is this repository's tier mechanism rather than a shortcut around
one: it runs four tiers over one Nx project, keyed on four `nx.json` named inputs, and
`orchestrator:test-recipes` is a target of that same project keyed on `recipeWorkspace` —
the justfile, `scripts/**` and the modules that collect these tests, which is exactly what
this table drives. `tests/conftest.py` fails a marked test that opens anything outside that
key and `tests/test_nx_cache_scope.py` holds the four selectors to a partition of the
suite, so the tier is enforced rather than declared. A project per delegated recipe is also
the opposite of what this file is for: `tests/AGENTS.md` states that a delegated recipe's
journey *is* its row here, in one table, so that a recipe which starts naming a different
verb fails in one place rather than in an operator's terminal.

llmlint: ignore-file[e2e_not_mocked,tests_mirror_real_usage] The published CLIs are
the boundary these wrappers delegate *to*, so a double there is what makes the
delegation observable; the recipes, the wrapper scripts, and the shell they run in are
all real. The recorded argv is the whole subject here rather than a private detail —
what a recipe *promises* is the verb it reaches and the arguments it reaches it with,
and no user-visible result can distinguish `oneagentgraph sweep --min-age-hours 4`
from `oneagentgraph sweep` on a host where both find nothing. What each verb then does
with those arguments is proven against the real verb in the journeys that own it, and
for this recipe that is `tests/e2e/test_sweep_e2e.py`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

import follow_up_variables
import plan_root_variable
import pytest
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

ROOT = Path(__file__).resolve().parents[2]

#: Copied into the checkout so a recipe that delegates through a script finds it.
WRAPPER_SCRIPTS = (
    "planner-surface.sh",
    "new-persona.sh",
    "telemetry-server.sh",
    "smoke.sh",
    # `smoke.sh` names this one as the agent harness; the journeys below assert the
    # path it hands down, so the file it names has to be the real one.
    "oneharness-agent.sh",
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
    # see. It reads `config/dispatch-appendix.md`, which `_checkout` copies beside it.
    "dispatch-appendix-env.sh",
    # The one definition of which resolvers establish a dispatch's environment, which
    # every launch runs at driver start and names — as the hook beside it, by absolute
    # path — for the engine to re-run before each node-scope dispatch. A checkout
    # without either is refused before it delegates.
    "dispatch-env.sh",
    "dispatch-env-hook.sh",
    "claude-alt-config-dir.sh",
    "codex-alt-home.sh",
    # `just repos` goes through this one, which absorbs the flag spelling.
    "repos.sh",
    # The two landing recipes go through this one, which reads the branch and `--repo`
    # for the drafter and forwards everything else; the drafter itself is what it
    # names, and a checkout without it would delegate through a wrapper that cannot
    # run. `just recoverable` goes through the third, which re-renders the resume
    # commands `onevcs` prints in their `just` form.
    "land-branch.sh",
    "draft-pr-body.sh",
    "recoverable.sh",
    # `just sweep` goes through this one, which composes the two published sweep
    # verbs and writes the trailer neither of them can.
    "sweep.sh",
    # The two supervisory views go through these two, which pass the published view
    # through untouched and add this host's own readings — free space, and every live
    # rendezvous — beside it out of the filter below. A checkout without all three
    # would delegate through a wrapper that cannot run.
    "status.sh",
    "host.sh",
    "supervision-readings.py",
    # `just watch` goes through this one, which asks the installed engine whether it
    # has the verb at all and pipes its machine-readable form through the renderer
    # beside it — so a checkout without either would delegate through a wrapper that
    # cannot run.
    "watch-run.sh",
    "watch-render.py",
)


#: The brief `just plan` is given below, written into each throwaway checkout by
#: `_checkout`. Its filename is what the recipe derives an unnamed run from, so it is
#: the other half of the published line the first `plan` row asserts.
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

#: The run the design-document launch is made under, derived from the flow's own name.
DESIGN_PROJECT = "authoring:cursor-shape-design"

#: The source `just finish-plan` copies into when the caller names none, which is the
#: board this repository plans against. A literal here for the reason
#: `tests/plan_tooling/test_copy_plan_recipe_e2e.py` spells it as one: importing the
#: constant would make this table assert that it equals itself.
THE_BOARD = "plans"


def _tail(destination: str = THE_BOARD, project: str = PLAN_PROJECT) -> tuple[str, ...]:
    """Every command line the tail of the planning flow reaches, in order.

    Stated once because it is one sequence: `just plan` hands over to `just finish-plan`
    rather than repeating its steps, so a row that reached a different set of verbs here
    would be the two entry points having drifted apart. The order is the whole contract —
    the review is before the check, the check is before the launch that writes the
    document, and the copy is after both — because a document written from unreviewed
    content is the failure this ordering exists to prevent.
    """
    return (
        f"uv run orchestrator-review-plan {project}",
        f"uv run orchestrator-check-plan {project}",
        f"uv run orchestrator-launch-gate {DESIGN_PROJECT} --dag-graph off",
        START_HELP,
        f"uv run onepipeline start {DEFAULTS} {DESIGN_PROJECT} --dag-graph off",
        f"uv run orchestrator-copy-plan {project} --to {destination}",
        f"uv run orchestrator-plan-locations {project} --in {destination}",
    )


class Delegation(NamedTuple):
    """One promise this repository makes: a recipe an operator types, and where it lands.

    The two sides are named because they are different things — the operating
    surface, which is meant to outlive the implementation, and the published command
    line behind it today. A row read as `[0]` and `[1]` invites a third element that
    has to be positioned rather than named.
    """

    recipe: str
    #: What an operator types after the recipe name.
    arguments: tuple[str, ...]
    #: The one command line that invocation must produce, whole.
    published: str
    #: The further command lines a recipe that composes several verbs must produce,
    #: in order, after the one above. Empty for every recipe that reaches exactly one.
    then: tuple[str, ...] = ()
    #: The command lines a recipe reads *before* the one it delegates to, in order.
    #: Separate from `then` so that a read which started landing something would show
    #: up as the ordering it is rather than as a second delegation.
    before: tuple[str, ...] = ()

    @property
    def invocation(self) -> tuple[str, ...]:
        """The `just` invocation, as it is typed."""
        return (self.recipe, *self.arguments)


#: Where a row's command line names the checkout the recipe ran in. Each journey runs in a
#: throwaway checkout of its own, so the rows spell it as this and the comparison fills it.
CHECKOUT = "@CHECKOUT@"
#: The run-end hook `just orchestrate` names, as the absolute path it renders.
RUN_ENDED = f"{CHECKOUT}/scripts/run-ended.sh"
#: Both hooks as a launch the caller named neither of carries them, in the order added.
HOOKS = f"--success-hook {RUN_ENDED} --failure-hook {RUN_ENDED}"
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
#: none of carries.
DEFAULTS = f"{BUS_CONFIG} {DISPATCH_ENV_HOOK} {MAINTENANCE_CONFIG}"
#: The two flags the wrapper asks the engine about before naming either, and the
#: default it renders for each.
GUARDED_DEFAULTS = {
    "--dispatch-env-hook": DISPATCH_ENV_HOOK,
    "--maintenance-config": MAINTENANCE_CONFIG,
}
#: How the wrapper asks the installed engine whether `start` takes the hook flag and
#: the schedule flag, before it names either: one read every launch a caller left one of
#: them unnamed for owes, after the gate.
START_HELP = "uv run onepipeline start --help"
#: Set in a journey's environment to have the traced `uv` answer that question as the
#: engine this host ran before the hook — with neither `--dispatch-env-hook` nor
#: `--maintenance-config` in it.
ENGINE_WITHOUT_HOOK_ENV = "FAKE_ENGINE_WITHOUT_DISPATCH_ENV_HOOK"
#: Set to have it answer as the engine this host ran until the pool's adoption: the hook
#: listed, and no `--maintenance-config`.
ENGINE_WITHOUT_MAINTENANCE_ENV = "FAKE_ENGINE_WITHOUT_MAINTENANCE_CONFIG"
#: Set to a status to have that question fail, as an engine that cannot start answers it.
ENGINE_START_HELP_EXIT_ENV = "FAKE_ENGINE_START_HELP_EXIT"


#: The whole delegation table, as `just` invocation → the command lines it must
#: produce, in order: one for nearly every recipe, and the sequence a recipe that
#: composes several verbs owes. This is the mapping this repository promises, in one
#: place: a recipe that starts naming a different verb, dropping an argument on the
#: way, or reaching only the first of the verbs it composes, fails here rather than in
#: an operator's terminal.
DELEGATIONS = (
    # The two graph flags and the two hooks are the recipe's own addition, and the reason
    # it exists: all four ship defaulted to nothing, so a bare launch runs with no agent
    # watching it, opens its change requests with no drafted body, and ends with nothing
    # verifying what it drafted. The hooks name the run-ended script by its absolute path
    # in the checkout the recipe ran in.
    Delegation(
        "orchestrate",
        ("authoring:probe",),
        f"uv run onepipeline start {DEFAULTS} authoring:probe --dag-graph graphs/dag-scope.yaml"
        f" --pr-author-graph graphs/pr-author.yaml {HOOKS}",
    ),
    Delegation(
        "orchestrate",
        ("authoring:probe", "--detach"),
        f"uv run onepipeline start {DEFAULTS} authoring:probe --detach"
        " --dag-graph graphs/dag-scope.yaml"
        f" --pr-author-graph graphs/pr-author.yaml {HOOKS}",
    ),
    # A caller who names one keeps it: the flags refuse to be given twice, so adding
    # a default over an explicit one would break the launch outright. Per flag, so
    # naming one leaves the others' defaults in place.
    Delegation(
        "orchestrate",
        ("authoring:probe", "--dag-graph", "off"),
        f"uv run onepipeline start {DEFAULTS} authoring:probe --dag-graph off"
        f" --pr-author-graph graphs/pr-author.yaml {HOOKS}",
    ),
    Delegation(
        "orchestrate",
        ("authoring:probe", "--dag-graph=graphs/other.yaml"),
        f"uv run onepipeline start {DEFAULTS} authoring:probe --dag-graph=graphs/other.yaml"
        f" --pr-author-graph graphs/pr-author.yaml {HOOKS}",
    ),
    Delegation(
        "orchestrate",
        ("authoring:probe", "--pr-author-graph", "graphs/other.yaml"),
        f"uv run onepipeline start {DEFAULTS} authoring:probe --pr-author-graph graphs/other.yaml"
        f" --dag-graph graphs/dag-scope.yaml {HOOKS}",
    ),
    Delegation(
        "orchestrate",
        ("authoring:probe", "--pr-author-graph=graphs/other.yaml", "--dag-graph=off"),
        f"uv run onepipeline start {DEFAULTS} authoring:probe"
        f" --pr-author-graph=graphs/other.yaml --dag-graph=off {HOOKS}",
    ),
    # Each hook is kept per flag too, in either spelling and including a blank value,
    # which is how a caller says this launch has none.
    Delegation(
        "orchestrate",
        ("authoring:probe", "--success-hook", "/elsewhere/on-success"),
        f"uv run onepipeline start {DEFAULTS} authoring:probe"
        " --success-hook /elsewhere/on-success"
        " --dag-graph graphs/dag-scope.yaml --pr-author-graph graphs/pr-author.yaml"
        f" --failure-hook {RUN_ENDED}",
    ),
    Delegation(
        "orchestrate",
        ("authoring:probe", "--failure-hook=", "--success-hook="),
        f"uv run onepipeline start {DEFAULTS} authoring:probe --failure-hook= --success-hook="
        " --dag-graph graphs/dag-scope.yaml --pr-author-graph graphs/pr-author.yaml",
    ),
    # The dispatch-env hook and the maintenance schedule are `scripts/onepipeline.sh`'s
    # own defaults rather than the recipe's, and each is kept per flag the same way: a
    # caller who names one, in either spelling and including the blank value that says
    # this launch has none, keeps it, while the bus configuration and the other default
    # beside it are still added.
    Delegation(
        "orchestrate",
        ("authoring:probe", "--dispatch-env-hook", "/elsewhere/refresh-env"),
        f"uv run onepipeline start {BUS_CONFIG} {MAINTENANCE_CONFIG} authoring:probe"
        " --dispatch-env-hook /elsewhere/refresh-env"
        f" --dag-graph graphs/dag-scope.yaml --pr-author-graph graphs/pr-author.yaml {HOOKS}",
    ),
    Delegation(
        "orchestrate",
        ("authoring:probe", "--dispatch-env-hook="),
        f"uv run onepipeline start {BUS_CONFIG} {MAINTENANCE_CONFIG} authoring:probe"
        " --dispatch-env-hook="
        f" --dag-graph graphs/dag-scope.yaml --pr-author-graph graphs/pr-author.yaml {HOOKS}",
    ),
    Delegation(
        "orchestrate",
        ("authoring:probe", "--maintenance-config", "/elsewhere/schedule.yaml"),
        f"uv run onepipeline start {BUS_CONFIG} {DISPATCH_ENV_HOOK} authoring:probe"
        " --maintenance-config /elsewhere/schedule.yaml"
        f" --dag-graph graphs/dag-scope.yaml --pr-author-graph graphs/pr-author.yaml {HOOKS}",
    ),
    Delegation(
        "orchestrate",
        ("authoring:probe", "--maintenance-config="),
        f"uv run onepipeline start {BUS_CONFIG} {DISPATCH_ENV_HOOK} authoring:probe"
        " --maintenance-config="
        f" --dag-graph graphs/dag-scope.yaml --pr-author-graph graphs/pr-author.yaml {HOOKS}",
    ),
    # Naming both, in either spelling, is the one launch that asks the engine nothing.
    Delegation(
        "orchestrate",
        ("authoring:probe", "--dispatch-env-hook=", "--maintenance-config="),
        f"uv run onepipeline start {BUS_CONFIG} authoring:probe"
        " --dispatch-env-hook= --maintenance-config="
        f" --dag-graph graphs/dag-scope.yaml --pr-author-graph graphs/pr-author.yaml {HOOKS}",
    ),
    # Adoption attaches a fresh driver to an intact ledger, which already records the
    # graphs, hooks and dispatch-env hook its launch chose, so no default is added to it.
    Delegation("orchestrate", ("--adopt", "run-1"), "uv run onepipeline adopt run-1"),
    # `just plan` writes the plan it launches, so the argument its published line
    # carries is a path this recipe generated rather than one the caller typed. Both
    # of its own flags are absorbed here — `--name` decides that path and `--max-turns`
    # goes into the node — and everything else reaches `onepipeline start` untouched.
    #
    # `--dag-graph off` is the observer default it adds, and it is the whole of what a
    # planning launch differs from a bare `onepipeline start` by: the journal, the
    # ownership row, the surfaces and the DAG UI place are all that verb's own, and an
    # observer would only add a monitor comparing the run against the plan it has not
    # written yet.
    #
    # What follows that launch is the tail, and every row of it is `just finish-plan`'s
    # own: this recipe hands over rather than repeating those steps, which is what keeps
    # the two entry points one sequence.
    Delegation(
        "plan",
        (BRIEF,),
        f"uv run onepipeline start {DEFAULTS} authoring:cursor-shape --dag-graph off",
        then=_tail(),
    ),
    # `--detach` hands back before the planner has written anything, so there is no plan
    # for the tail to be about and the launch prints the command that finishes it later.
    # That is the one shape where `just plan` reaches a single verb.
    Delegation(
        "plan",
        (BRIEF, "--name", "listing-api", "--max-turns", "40", "--detach"),
        f"uv run onepipeline start {DEFAULTS} authoring:listing-api --detach --dag-graph off",
    ),
    # The joined spelling of both, which is a separate parsing path: `--name=` decides
    # the plan path this line names, and `--max-turns=` is absorbed rather than
    # forwarded — which is exactly what its absence from this line asserts.
    Delegation(
        "plan",
        (BRIEF, "--name=listing-api", "--max-turns=40", "--detach"),
        f"uv run onepipeline start {DEFAULTS} authoring:listing-api --detach --dag-graph off",
    ),
    # `--no-design-doc` stops the flow after the planner, so the tail is absent here for
    # a different reason than it is absent above: there is nothing to write a document
    # about copying, rather than nothing written yet.
    Delegation(
        "plan",
        (BRIEF, "--no-design-doc"),
        f"uv run onepipeline start {DEFAULTS} authoring:cursor-shape --dag-graph off",
    ),
    # `--to` is the tail's own flag and reaches it rather than `onepipeline start`: the
    # destination decides nothing about the planner, and everything about where the plan
    # a person reviews ends up.
    Delegation(
        "plan",
        (BRIEF, "--to", "elsewhere"),
        f"uv run onepipeline start {DEFAULTS} authoring:cursor-shape --dag-graph off",
        then=_tail(destination="elsewhere"),
    ),
    # A caller who names an observer keeps it, in either spelling and including their
    # own `off`: the flag refuses to be given twice, so the default is added only when
    # neither spelling was typed. It is the *planner's* observer: the tail is a separate
    # launch of one node that reads a finished plan, so it keeps its own default.
    Delegation(
        "plan",
        (BRIEF, "--dag-graph", "graphs/dag-scope.yaml"),
        f"uv run onepipeline start {DEFAULTS} authoring:cursor-shape"
        " --dag-graph graphs/dag-scope.yaml",
        then=_tail(),
    ),
    Delegation(
        "plan",
        (BRIEF, "--dag-graph=graphs/other.yaml", "--detach"),
        f"uv run onepipeline start {DEFAULTS} authoring:cursor-shape"
        " --dag-graph=graphs/other.yaml --detach",
    ),
    # The tail on its own, which is how a plan edited after it was authored is finished:
    # the same six verbs in the same order, reached without a planner being launched at
    # all. Its published line is the design-document launch, because that is the one verb
    # of the six that starts a run.
    Delegation(
        "finish-plan",
        (BRIEF, "--to", "elsewhere"),
        "uv run orchestrator-review-plan authoring:listing-cursor",
        then=(
            "uv run orchestrator-check-plan authoring:listing-cursor",
            "uv run orchestrator-launch-gate authoring:cursor-shape-design --dag-graph off",
            START_HELP,
            f"uv run onepipeline start {DEFAULTS} authoring:cursor-shape-design --dag-graph off",
            "uv run orchestrator-copy-plan authoring:listing-cursor --to elsewhere",
            "uv run orchestrator-plan-locations authoring:listing-cursor --in elsewhere",
        ),
    ),
    Delegation("channel-next", ("run-1",), "uv run onepipeline next run-1"),
    # The read profile is the CLI's own default, so the recipes name no filter and
    # pass one through untouched when the caller does.
    Delegation(
        "channel-next",
        ("run-1", "--filter", "detailed"),
        "uv run onepipeline next run-1 --filter detailed",
    ),
    Delegation("channel-next", ("run-1", "--all"), "uv run onepipeline next run-1 --all"),
    Delegation(
        "monitor",
        ("run-1", "--filter", "detailed"),
        "uv run onepipeline monitor run-1 --filter detailed",
    ),
    Delegation("monitor", ("run-1", "--all"), "uv run onepipeline monitor run-1 --all"),
    # A verdict named as a file is `onemessagebus reply`, which binds it to the pending
    # question; the table's runner writes the envelope a row names. A commands-only envelope
    # is `onemessagebus send replies` instead, and an envelope file that cannot be read is
    # refused before the bus; the `test_the_reply_recipe_*` journeys below state both.
    Delegation(
        "channel-reply",
        ("run-1", "reply.json"),
        "uv run onemessagebus reply surfaces --config config/onemessagebus.yaml "
        "--transport-dir runs/run-1/channel --file reply.json",
    ),
    # The text is handed over on the verb's stdin rather than as `--message`, so no
    # prose this recipe was given is ever a command-line word — the hazard the two
    # supervisory members are told about at length.
    Delegation(
        "channel-surface",
        ("run-1", "a status update"),
        "uv run onepipeline surface --kind check-in run-1",
        then=("stdin a status update",),
    ),
    Delegation("stop", ("run-1", "--force"), "uv run onepipeline stop run-1 --force"),
    Delegation("runs", ("--mine",), "uv run onepipeline runs --mine"),
    Delegation("status", ("run-1",), "uv run onepipeline status run-1"),
    Delegation(
        "unwatched",
        ("--session", "manager-1"),
        "uv run onepipeline unwatched --session manager-1",
    ),
    Delegation("host", (), "uv run onepipeline host"),
    Delegation("monitor", ("run-1",), "uv run onepipeline monitor run-1"),
    # The watch recipe reads the engine's own command list before it delegates, so an
    # engine without the verb is refused in this repository's own words rather than by
    # whatever the engine says to an unknown subcommand. What it forwards is the
    # caller's arguments and nothing else: the verb writes both forms unconditionally —
    # the operator's lines on standard error, the machine-readable one on standard
    # output — so there is nothing to ask for.
    Delegation(
        "watch",
        ("run-1", "--timeout", "600", "--tick-interval", "60"),
        "uv run onepipeline watch run-1 --timeout 600 --tick-interval 60",
        before=("uv run onepipeline --help",),
    ),
    Delegation("results", ("run-1",), "uv run onepipeline results run-1"),
    Delegation("transcript", ("run-1",), "uv run onepipeline transcript run-1"),
    # The optional node operand, which is what turns a whole run's transcript into one
    # dispatch's. `tests/e2e/test_transcript_recipe_e2e.py` proves the narrowing against
    # a recorded run; this row is what proves the recipe passes the operand at all.
    Delegation(
        "transcript",
        ("run-1", "node-a"),
        "uv run onepipeline transcript run-1 node-a",
    ),
    Delegation("goals", (), "uv run onepipeline goals"),
    Delegation(
        "telemetry", ("run-1", "--breakdown"), "uv run onepipeline telemetry run-1 --breakdown"
    ),
    Delegation("history", (), "uv run oneagentgraph history"),
    Delegation("history-show", ("oh:abc123",), "uv run oneagentgraph history show oh:abc123"),
    Delegation("smoke", (), "uv run oneagentgraph smoke"),
    # The one recipe here that reaches two verbs. Both are named because a sweep that
    # silently dropped one would report a clean host while a family filled the disk,
    # and both carry the options, because an age floor that meant one thing to one
    # family and another to the next would be worse than no floor. The floor is on
    # every row, the bare one included: both verbs default to 24 hours, which on this
    # host reclaimed nothing at all, so the recipe passes its own 4 and each verb has
    # to be told. Moving that default is a deliberate edit to these rows.
    Delegation(
        "sweep",
        ("--dry-run",),
        "uv run oneagentgraph sweep --dry-run --min-age-hours 4",
        then=("uv run onevcs sweep --dry-run --min-age-hours 4",),
    ),
    Delegation(
        "sweep",
        ("--min-age-hours", "0"),
        "uv run oneagentgraph sweep --min-age-hours 0",
        then=("uv run onevcs sweep --min-age-hours 0",),
    ),
    Delegation(
        "sweep",
        (),
        "uv run oneagentgraph sweep --min-age-hours 4",
        then=("uv run onevcs sweep --min-age-hours 4",),
    ),
    Delegation("validate-personas", (), "uv run oneagentgraph persona validate personas"),
    Delegation("register-repo", ("/checkout",), "uv run onevcs register /checkout"),
    Delegation("repos", (), "uv run onevcs repos"),
    Delegation(
        "repo-policy",
        ("/checkout",),
        "uv run onevcs rules check /checkout",
    ),
    # The published flag is spelled differently; the recipe keeps the spelling the
    # planner doctrine names and the wrapper absorbs the difference.
    Delegation("repos", ("--audit-gate-coverage",), "uv run onevcs repos --audit-gates"),
    # The two lookups in front of both landing rows are the drafter's, not the verb's.
    # The first asks which publication policy the identity resolves, because
    # `local-direct` opens no change request and so is never drafted for; the second
    # turns a `--repo` that is not a directory into one, since it may be a registered
    # alias. Both answer nothing here — the traced `uv` prints none — so drafting
    # proceeds, which is the fallthrough each of those reads is written to take.
    Delegation(
        "repo-recover",
        ("claude/work", "--repo", "/checkout"),
        "uv run onevcs recover claude/work --repo /checkout",
        before=(
            "uv run onevcs rules check /checkout",
            "uv run onevcs resolve /checkout",
        ),
    ),
    # The third landing verb, and the one that closes the gap the other two left: a
    # complete branch no session holds had neither an incomplete marker for `recover`
    # nor a local merge train for `integrate`, so landing one meant raw `git`/`gh`.
    Delegation(
        "publish-branch",
        ("claude/work", "--repo", "/checkout"),
        "uv run onevcs publish-branch claude/work --repo /checkout",
        before=(
            "uv run onevcs rules check /checkout",
            "uv run onevcs resolve /checkout",
        ),
    ),
    # Both optional flags reach the verb. That they arrive as the *words* they were
    # typed as is a separate claim this trace cannot make — it joins argv with spaces —
    # so `test_the_publish_branch_recipe_forwards_a_title_as_one_word` makes it.
    Delegation(
        "publish-branch",
        (
            "claude/work",
            "--repo",
            "/checkout",
            "--title",
            "Add the thing",
            "--policy",
            "change-open",
        ),
        "uv run onevcs publish-branch claude/work --repo /checkout "
        "--title Add the thing --policy change-open",
        before=(
            "uv run onevcs rules check /checkout",
            "uv run onevcs resolve /checkout",
        ),
    ),
    # The two escapes from drafting, which are the recipe's own additions to the verb's
    # argument list rather than `onevcs` options. `--no-draft` is consumed here — the
    # verb has no such option and would refuse the whole invocation — and a caller's own
    # body is forwarded untouched. That no turn is spent for either is a claim this
    # trace cannot make; `tests/e2e/test_publish_branch_e2e.py` makes it against a real
    # drafting seam. What it does say is that neither spends the checkout lookup either.
    Delegation(
        "publish-branch",
        ("claude/work", "--repo", "/checkout", "--no-draft"),
        "uv run onevcs publish-branch claude/work --repo /checkout",
    ),
    Delegation(
        "repo-recover",
        ("claude/work", "--repo", "/checkout", "--body-file", "/tmp/body.md"),
        "uv run onevcs recover claude/work --repo /checkout --body-file /tmp/body.md",
    ),
    Delegation("recoverable", (), "uv run onevcs recoverable"),
    # Scoped to one identity from wherever it runs. The wrapper reads its arguments only
    # for `--json`, so `--repo` has to arrive at the verb whole, beside its value.
    Delegation(
        "recoverable",
        ("--repo", "/checkout"),
        "uv run onevcs recoverable --repo /checkout",
    ),
    # The two reads that close the gap the landing verbs left. `status` is the only
    # way to ask what became of a piece of work — a run's own row dates its answer to
    # the settlement — and `import` is the only way to make work a landing verb cannot
    # see reachable. The recipe names differ from the published verbs because `just
    # status` is already `onepipeline status` and a bare `just import` says nothing
    # about what is imported; both forward their arguments untouched.
    Delegation(
        "work-status",
        ("https://github.com/o/r/pull/1",),
        "uv run onevcs status https://github.com/o/r/pull/1",
    ),
    Delegation("work-status", ("claude/work", "--json"), "uv run onevcs status claude/work --json"),
    Delegation(
        "import-branch",
        ("claude/work", "--repo", "/checkout"),
        "uv run onevcs import claude/work --repo /checkout",
    ),
    Delegation(
        "import-branch",
        ("claude/work", "--repo", "/checkout", "--from", "/run/clone", "--as", "claude/work-2"),
        "uv run onevcs import claude/work --repo /checkout --from /run/clone --as claude/work-2",
    ),
    Delegation(
        "integrate",
        ("claude/a", "claude/b", "--push"),
        "uv run onevcs integrate claude/a claude/b --push",
    ),
    Delegation("sync", ("main",), "uv run onevcs sync main"),
)


#: Where `just finish-plan` reads the design document's template from, relative to the
#: checkout it runs in. Stated here rather than read out of `scripts/finish-plan.sh`, for
#: the reason every expectation in this suite is stated: a fixture that took its shape
#: from its subject would build whatever the subject asked for and prove nothing about it.
DESIGN_TEMPLATE = "config/design-doc-template.md"

#: The operational appendix `just plan` hands its dispatch, spelled here for the reason
#: the template above is: this suite states what a runnable checkout holds rather than
#: asking its subject.
DISPATCH_APPENDIX = "config/dispatch-appendix.md"

#: The pool-maintenance schedule every launch names, spelled here for the same reason.
MAINTENANCE_SCHEDULE = "config/onepipeline.maintenance.yaml"


def _checkout(tmp_path: Path) -> tuple[Path, Path]:
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
    # The pool-maintenance schedule every launch names by absolute path. Written rather
    # than copied, for the reason the appendix above is: that a launch names the file
    # and refuses a checkout without one is this suite's subject, and what the tracked
    # schedule *says* is the engine's to read.
    (checkout / MAINTENANCE_SCHEDULE).write_text(
        "version: 1\ndefault:\n  every: 7d\nrules: []\n", encoding="utf-8"
    )
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
    # nothing about the recipe under test. It also answers the engine's own command
    # list, because `just watch` reads that list before it delegates — asking whether
    # the installed engine has the verb at all — and a double that answered nothing
    # would make every row of this table watch a verb it had just been told is absent.
    # And it answers `start --help` the same way, because every launch asks it whether
    # the engine takes `--dispatch-env-hook` and `--maintenance-config` before naming
    # either: both listed unless the journey says the engine lacks one — the engine
    # before the hook lacked both, the engine this host ran until the pool's adoption
    # lacked the schedule — which the two `..._to_an_engine_without_the_flag` journeys
    # drive.
    uv.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf 'uv %s\\n' "$*" >>"$TRACE_FILE"
if [ "$*" = "run onepipeline --help" ]; then
  echo "Commands:"
  echo "  watch       Watch one run until something a supervisor has to act on happens"
  exit 0
fi
if [ "$*" = "run onepipeline start --help" ]; then
  echo "Usage: onepipeline start [OPTIONS] <PROJECT>"
  echo "Options:"
  echo "      --bus-config <PATH>"
  if [ -z "${{{ENGINE_WITHOUT_HOOK_ENV}:-}}" ]; then
    echo "      --dispatch-env-hook <COMMAND>"
    if [ -z "${{{ENGINE_WITHOUT_MAINTENANCE_ENV}:-}}" ]; then
      echo "      --maintenance-config <FILE>"
    fi
  fi
  exit "${{{ENGINE_START_HELP_EXIT_ENV}:-0}}"
fi
if [ ! -t 0 ]; then
  while IFS= read -r line; do printf 'stdin %s\\n' "$line" >>"$TRACE_FILE"; done
fi
exit "${{FAKE_UV_EXIT:-0}}"
"""
    )
    uv.chmod(0o755)
    return checkout, trace


def _run(
    checkout: Path,
    trace: Path,
    *args: str,
    stdin: str | None = None,
    status_dir: Path | None = None,
    env: dict[str, str] | None = None,
    stdin_fd: int | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{checkout / 'bin'}{os.pathsep}{environment['PATH']}"
    environment["TRACE_FILE"] = str(trace)
    # `just sweep` reports on the pre-adoption worktree root, and this host's
    # own is 41 GB: pointing it at a path inside the throwaway checkout keeps these
    # journeys off it.
    environment["AI_ORCHESTRATOR_HOME"] = str(checkout / "ai-orchestrator-home")
    # The same, for the other root it reports on. This host's `/tmp` is 83 GiB across
    # 26,624 entries and walking it costs ~30s, which every row here would pay.
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
        "ORCHESTRATOR_AGENT_STATUS_DIR",
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
    if status_dir is not None:
        environment["ORCHESTRATOR_AGENT_STATUS_DIR"] = str(status_dir)
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


#: The command `scripts/onepipeline.sh` puts every launch through before it reaches the
#: engine: the design-document approval gate. It is derived from the published line below
#: rather than restated per row, and that is the claim rather than a saving — **every**
#: `onepipeline start` this repository makes is gated, so a row that could name it and did
#: not would be a launch that got past.
LAUNCH_GATE = "uv run orchestrator-launch-gate"
STARTS = "uv run onepipeline start "


def _gated(published: str) -> tuple[str, ...]:
    """What a launch owes before its own line, and nothing for one that launches nothing.

    The gate first, over the launch as typed; then the wrapper's one question to the
    engine about the hook flag and the schedule flag, which a launch that named both —
    in either spelling — never asks.
    """
    if not published.startswith(STARTS):
        return ()
    typed = published.removeprefix(STARTS)
    # The wrapper adds its defaults after the gate has read the launch as typed, so each
    # one is stripped on its own: a row that named its own hook carries only the bus
    # configuration and the schedule ahead of what was typed.
    for default in (BUS_CONFIG, *GUARDED_DEFAULTS.values()):
        typed = typed.removeprefix(f"{default} ")
    owed = [f"{LAUNCH_GATE} {typed}"]
    named = {
        flag for flag in GUARDED_DEFAULTS if any(word.startswith(flag) for word in typed.split())
    }
    if named != set(GUARDED_DEFAULTS):
        owed.append(START_HELP)
    return tuple(owed)


#: The envelope a row naming a reply file hands the recipe: a verdict, so it is
#: `onemessagebus reply` it reaches.
REPLY_ENVELOPE = '{"version":3,"completion":true,"message":"main"}'


@pytest.mark.reads_recipes
@pytest.mark.parametrize("delegation", DELEGATIONS, ids=lambda row: " ".join(row.invocation))
def test_a_delegated_recipe_reaches_its_published_verb(
    tmp_path: Path, delegation: Delegation
) -> None:
    checkout, trace = _checkout(tmp_path)
    # A row naming an envelope file names one a manager wrote: `channel-reply` reads it to
    # choose its verb, and refuses a file it cannot read before reaching the bus.
    for argument in delegation.arguments:
        if argument.endswith(".json"):
            (checkout / argument).write_text(REPLY_ENVELOPE, encoding="utf-8")

    result = _run(checkout, trace, *delegation.invocation)

    assert result.returncode == 0, result.stderr
    expected = [
        *delegation.before,
        *_gated(delegation.published),
        delegation.published,
        *delegation.then,
    ]
    assert trace.read_text().splitlines() == [
        line.replace(CHECKOUT, str(checkout.resolve())) for line in expected
    ]


@pytest.mark.reads_recipes
def test_a_launch_names_no_schedule_to_an_engine_without_the_flag(tmp_path: Path) -> None:
    """An engine whose `start --help` lists no `--maintenance-config` is handed none.

    That engine is the one `config/onepipeline.version` pinned until the pool's adoption
    — it takes the hook and refuses the schedule as an unknown argument — so a wrapper
    that named the schedule unconditionally would refuse every launch on it. The launch
    is rendered as it was before the schedule existed: the gate, the question, and a line
    carrying the bus configuration and the hook.
    """
    checkout, trace = _checkout(tmp_path)

    result = _run(
        checkout,
        trace,
        "orchestrate",
        "authoring:probe",
        env={ENGINE_WITHOUT_MAINTENANCE_ENV: "1"},
    )

    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == [
        line.replace(CHECKOUT, str(checkout.resolve()))
        for line in (
            f"{LAUNCH_GATE} authoring:probe --dag-graph graphs/dag-scope.yaml"
            f" --pr-author-graph graphs/pr-author.yaml {HOOKS}",
            START_HELP,
            f"uv run onepipeline start {BUS_CONFIG} {DISPATCH_ENV_HOOK} authoring:probe"
            f" --dag-graph graphs/dag-scope.yaml --pr-author-graph graphs/pr-author.yaml {HOOKS}",
        )
    ]


@pytest.mark.reads_recipes
def test_a_launch_is_refused_when_the_schedule_it_would_name_is_missing(tmp_path: Path) -> None:
    """A checkout without the schedule is refused before the engine is reached.

    The engine refuses a launch naming a file it cannot read before the run is minted,
    so the wrapper says which file and where first, rather than handing the engine a
    path to refuse.
    """
    checkout, trace = _checkout(tmp_path)
    (checkout / MAINTENANCE_SCHEDULE).unlink()

    result = _run(checkout, trace, "orchestrate", "authoring:probe")

    assert result.returncode == 2, result.stdout + result.stderr
    assert "the pool-maintenance schedule is not a readable file at" in result.stderr
    assert str(checkout.resolve() / MAINTENANCE_SCHEDULE) in result.stderr, result.stderr
    traced = trace.read_text().splitlines()
    assert traced[-1] == START_HELP, traced
    assert not any(line.startswith(STARTS) for line in traced[:-1]), (
        f"a refused launch reached the engine:\n{traced}"
    )


@pytest.mark.reads_recipes
def test_a_launch_names_no_hook_to_an_engine_without_the_flag(tmp_path: Path) -> None:
    """An engine whose `start --help` lists neither flag is handed neither.

    That engine is the one `config/onepipeline.version` pinned until the adoption that
    carried the hook, and it refuses an unknown argument outright — so a wrapper that
    named the hook unconditionally would refuse every launch here. The launch is rendered
    exactly as it was before the hook existed: the gate, the question, and a line
    carrying the bus configuration alone.
    """
    checkout, trace = _checkout(tmp_path)

    result = _run(
        checkout, trace, "orchestrate", "authoring:probe", env={ENGINE_WITHOUT_HOOK_ENV: "1"}
    )

    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == [
        line.replace(CHECKOUT, str(checkout.resolve()))
        for line in (
            f"{LAUNCH_GATE} authoring:probe --dag-graph graphs/dag-scope.yaml"
            f" --pr-author-graph graphs/pr-author.yaml {HOOKS}",
            START_HELP,
            f"uv run onepipeline start {BUS_CONFIG} authoring:probe"
            f" --dag-graph graphs/dag-scope.yaml --pr-author-graph graphs/pr-author.yaml {HOOKS}",
        )
    ]


@pytest.mark.reads_recipes
def test_a_launch_is_refused_when_the_engine_cannot_say_what_start_takes(
    tmp_path: Path,
) -> None:
    """An engine that cannot answer `start --help` is reported as that, not as lacking the flag.

    Read as a positive question for the reason `scripts/watch-run.sh` reads its verb list
    that way: a probe failing for any other reason — a broken install — would otherwise
    launch without the hook and leave the next dispatch to fail on the environment the
    hook exists to refresh.
    """
    checkout, trace = _checkout(tmp_path)

    result = _run(
        checkout, trace, "orchestrate", "authoring:probe", env={ENGINE_START_HELP_EXIT_ENV: "3"}
    )

    assert result.returncode == 2, result.stdout
    assert "could not be asked what 'start' takes" in result.stderr, result.stderr
    assert "a dispatch-env hook or a maintenance schedule" in result.stderr, result.stderr
    assert "just bootstrap" in result.stderr, result.stderr
    traced = trace.read_text().splitlines()
    assert traced[-1] == START_HELP, traced
    assert not any(line.startswith(STARTS) for line in traced[:-1]), (
        f"a refused launch reached the engine:\n{traced}"
    )


def test_the_orchestrate_recipe_refuses_a_checkout_whose_run_end_hook_cannot_run(
    tmp_path: Path,
) -> None:
    """A launch whose hook could not start when the run ends is refused before it starts.

    The engine spawns the hook only once the run has ended, and a hook that cannot start
    is recorded as `could-not-start` in a log nobody is reading for it — the run's
    follow-ups would go unverified with nothing said at launch.
    """
    checkout, trace = _checkout(tmp_path)
    (checkout / "scripts" / "run-ended.sh").chmod(0o644)

    result = _run(checkout, trace, "orchestrate", "authoring:probe")

    assert result.returncode == 2, result.stdout + result.stderr
    assert "the run-end hook is not an executable file at" in result.stderr, result.stderr
    assert not trace.exists() or trace.read_text() == "", trace.read_text()


#: The two task records a whole `just plan` writes under the local authoring source, one
#: per launch: the planner node in the project the recipe generates, and the `design-doc`
#: node in the project its tail generates — which reads the finished plan and writes the
#: document a person reviews it as.
GENERATED_TASKS = (
    ".plans/tasks/cursor-shape/plan.md",
    ".plans/tasks/cursor-shape-design/design-doc.md",
)


class Placement(NamedTuple):
    """Where one generated node's record says its dispatch works, in the record's terms."""

    #: The record's own top-level `repositories`: the normalized origin of a hosted
    #: repository, and empty for one that list cannot hold.
    repositories: tuple[str, ...]
    #: What the reserved `onepipeline.repo` key carries, which is a local checkout's
    #: alias and never a hosted repository's — or `None` when the key is absent.
    reserved: str | None
    #: The registered execution checkout, which has no field of its own to move into and
    #: stays an alias on `onepipeline.execution_checkout`.
    execution: str | None


#: What the launch has to say about where the planner works. A manager reading the
#: receipt is the one who decides what their brief may ask it to leave behind, and it is
#: the only place the constraints are put in front of them at the moment they apply.
DIRECT_SAYS = (
    "may write only to gitignored paths, may not commit, and may not leave the base branch"
)

#: The three flags the flow's grammar used to carry, each refused by name now: the first
#: two would compose a lifecycle node, which the adopted engine settles `failed` as
#: `empty-branch` for committing nothing, and the third named the only shape there is.
RETIRED_PLACEMENT_FLAGS = (
    ("--repo", "other"),
    ("--execution-checkout", "other-isolated"),
    ("--direct",),
)


def _placed(task_record: str) -> Placement | None:
    """Where one generated record says its dispatch works, read off the record itself."""
    repositories = re.search(r"^repositories: (\[.*\])$", task_record, re.MULTILINE)
    reserved = re.search(r'"onepipeline.repo": "([^"]+)"', task_record)
    execution = re.search(r'"onepipeline.execution_checkout": "([^"]+)"', task_record)
    if repositories is None and reserved is None and execution is None:
        return None
    return Placement(
        tuple(json.loads(repositories.group(1))) if repositories else (),
        reserved.group(1) if reserved else None,
        execution.group(1) if execution else None,
    )


@pytest.mark.reads_recipes
def test_the_plan_recipe_writes_both_of_its_nodes_as_direct_nodes(tmp_path: Path) -> None:
    """Where the dispatched planner works is decided by the document, before any launch.

    `tests/e2e/test_plan_recipe_e2e.py` drives the shape all the way into a real dispatch
    and reads the directory out of the run's journal; this is the cheap half — the
    documents themselves, with the published CLI doubled so no planner is dispatched to
    prove a field. Each node must carry **neither** placement field: a `repo` would make
    it a lifecycle node, which the adopted engine settles `failed` as `empty-branch` for
    committing nothing, and `execution_checkout` without a `repo` names a clone nothing
    is cut from.

    Both nodes are read, because the placement is a property of the *flow* rather than of
    one launch: the two are written by two launches, which is exactly the seam a
    placement could reappear at.
    """
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, "plan", BRIEF)

    assert result.returncode == 0, result.stderr
    for generated in GENERATED_TASKS:
        task_record = (checkout / generated).read_text(encoding="utf-8")
        placed = _placed(task_record)
        assert placed is None, (
            f"`just plan` wrote {generated} placed at {placed}, so that dispatch would be "
            f"a lifecycle node rather than a direct one: {task_record}"
        )
    assert DIRECT_SAYS in result.stderr, (
        f"the launch said nothing about where this planner works, which is where its "
        f"constraints are stated at all:\n{result.stderr}"
    )


@pytest.mark.reads_recipes
@pytest.mark.parametrize("flag", RETIRED_PLACEMENT_FLAGS, ids=lambda row: row[0])
def test_the_plan_recipe_refuses_a_retired_placement_flag_by_name(
    tmp_path: Path, flag: tuple[str, ...]
) -> None:
    """A caller who read an older shape of this flow is told which shape every launch gets.

    Refused before anything is written or launched, and refused by this recipe rather than
    by `onepipeline start`, which has never heard of any of these and would report an
    unknown argument that says nothing about why the flag is gone.
    """
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, "plan", BRIEF, *flag)

    assert result.returncode != 0, result.stdout
    assert "no longer an option of the planning flow" in result.stderr, result.stderr
    assert not trace.exists() or "onepipeline start" not in trace.read_text(encoding="utf-8"), (
        f"a refused launch still reached the engine:\n{trace.read_text(encoding='utf-8')}"
    )
    for generated in GENERATED_TASKS:
        assert not (checkout / generated).exists(), f"a refused launch wrote {generated}"


class Collision(NamedTuple):
    """One launch whose run name is its brief's declared plan project's native id."""

    what: str
    invocation: tuple[str, ...]
    #: The run name the launch would have used, which is also that native id.
    run: str
    #: The brief's declared plan project, qualified.
    project: str


#: Every way a run name reaches the declared project's id: given with `--name` in either
#: spelling, derived from a brief named after its plan, and derived under the opt-out,
#: which drops the requirement to declare a plan but not the collision of one declared.
COLLISIONS = (
    Collision("a derived name", (COLLIDING_BRIEF,), "cursor-colour", COLLIDING_PROJECT),
    Collision(
        "a given name",
        (BRIEF, "--name", "listing-cursor"),
        "listing-cursor",
        PLAN_PROJECT,
    ),
    Collision(
        "a joined given name, detached",
        (BRIEF, "--name=listing-cursor", "--detach"),
        "listing-cursor",
        PLAN_PROJECT,
    ),
    Collision(
        "a derived name with no design document",
        (COLLIDING_BRIEF, "--no-design-doc"),
        "cursor-colour",
        COLLIDING_PROJECT,
    ),
)


@pytest.mark.reads_recipes
@pytest.mark.parametrize("collision", COLLISIONS, ids=lambda row: row.what)
def test_the_plan_recipe_refuses_a_run_named_as_the_plan_it_authors(
    tmp_path: Path, collision: Collision
) -> None:
    """A planning run and the plan it authors are never launched as one project.

    The launch writes its own project as `authoring:<run>`, and the planner authors the
    project the brief declares. Named alike, they are one record: the planner's plan
    lands over the launch's project, and the run's settlement write-back then rewrites
    that plan with the planning run's goal and node. So the launch is refused before it
    writes a record or reaches the engine, naming both values and the remedy.
    """
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, "plan", *collision.invocation)

    assert result.returncode != 0, f"{collision.what} launched:\n{result.stderr}"
    stated = [line for line in result.stderr.splitlines() if line.startswith("plan: ")]
    assert len(stated) == 1, result.stderr
    (refusal,) = stated
    assert f"the run name '{collision.run}'" in refusal, refusal
    native = collision.project.split(":", 1)[1]
    assert f"'{native}', the native id of the brief's plan project '{collision.project}'" in (
        refusal
    ), refusal
    assert "; pass --name with a run name other than" in refusal, refusal
    traced = trace.read_text(encoding="utf-8") if trace.exists() else ""
    assert "onepipeline start" not in traced, f"a refused launch reached the engine:\n{traced}"
    written = sorted(str(one.relative_to(checkout)) for one in (checkout / ".plans").rglob("*"))
    assert written == [], f"a refused launch wrote plan records: {written}"


@pytest.mark.reads_recipes
def test_the_plan_recipe_launches_a_brief_named_after_its_plan_under_another_name(
    tmp_path: Path,
) -> None:
    """The remedy the refusal names works: the same brief under another `--name` launches.

    Its planning project is written under the name given, and the plan the brief declares
    is left for the planner to author, so the two are separate records.
    """
    checkout, trace = _checkout(tmp_path)
    run = "cursor-colour-planning"

    result = _run(checkout, trace, "plan", COLLIDING_BRIEF, "--name", run, "--detach")

    assert result.returncode == 0, result.stderr
    published = (
        f"uv run onepipeline start {DEFAULTS} authoring:{run} --detach --dag-graph off".replace(
            CHECKOUT, str(checkout.resolve())
        )
    )
    assert published in trace.read_text(encoding="utf-8").splitlines(), trace.read_text()
    assert (checkout / ".plans" / "projects" / f"{run}.md").is_file(), result.stderr
    native = COLLIDING_PROJECT.split(":", 1)[1]
    assert not (checkout / ".plans" / "projects" / f"{native}.md").exists(), (
        f"the launch wrote the plan its planner authors, {COLLIDING_PROJECT}, itself"
    )


@pytest.mark.reads_recipes
def test_the_plan_recipe_launches_a_run_named_as_a_plan_in_another_source(
    tmp_path: Path,
) -> None:
    """A declared plan in another source is another record, whatever its native id.

    The launch writes `authoring:<run>`, so a brief declaring `plans:<run>` names a
    project in a different store: the two cannot overwrite each other, and the launch
    proceeds under the name its filename derives.
    """
    checkout, trace = _checkout(tmp_path)
    native = COLLIDING_PROJECT.split(":", 1)[1]
    elsewhere = f"plans:{native}"
    (checkout / COLLIDING_BRIEF).write_text(
        "## What\nDecide the cursor's colour.\n\n"
        f"Plan project: {elsewhere}\n\n"
        "## Why\nThe view cannot theme without it.\n\n"
        "## Acceptance criteria\n- The colour is stated.\n"
    )

    result = _run(checkout, trace, "plan", COLLIDING_BRIEF, "--detach")

    assert result.returncode == 0, result.stderr
    assert "the native id of the brief's plan project" not in result.stderr, result.stderr
    published = (
        f"uv run onepipeline start {DEFAULTS} authoring:{native} --detach --dag-graph off"
    ).replace(CHECKOUT, str(checkout.resolve()))
    assert published in trace.read_text(encoding="utf-8").splitlines(), trace.read_text()
    assert (checkout / ".plans" / "projects" / f"{native}.md").is_file(), result.stderr


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    ("declaration", "names"),
    (
        (f"Plan project: {PLAN_PROJECT}\nPlan project: {COLLIDING_PROJECT}", "names 2 plan"),
        ("Plan project: cursor-colour", "is not a qualified id"),
    ),
    ids=("two plan projects", "an unqualified plan project"),
)
def test_the_plan_recipe_opt_out_still_refuses_a_plan_project_it_cannot_compare(
    tmp_path: Path, declaration: str, names: str
) -> None:
    """`--no-design-doc` drops the need to declare a plan, not the reading of one declared.

    The opt-out's planner still authors into whatever the brief declares, so a declaration
    this launch could not read would be one it could not compare with its run name: a
    second line naming the run's own project would pass unseen. So an unusable one is
    refused under the opt-out as it is without it, before anything is written or launched.
    """
    checkout, trace = _checkout(tmp_path)
    (checkout / COLLIDING_BRIEF).write_text(
        f"## What\nDecide the cursor's colour.\n\n{declaration}\n\n"
        "## Why\nThe view cannot theme without it.\n\n"
        "## Acceptance criteria\n- The colour is stated.\n"
    )

    result = _run(checkout, trace, "plan", COLLIDING_BRIEF, "--no-design-doc")

    assert result.returncode != 0, f"an unusable declaration launched:\n{result.stderr}"
    assert names in result.stderr, result.stderr
    traced = trace.read_text(encoding="utf-8") if trace.exists() else ""
    assert "onepipeline start" not in traced, f"a refused launch reached the engine:\n{traced}"
    written = sorted(str(one.relative_to(checkout)) for one in (checkout / ".plans").rglob("*"))
    assert written == [], f"a refused launch wrote plan records: {written}"


#: A commands-only envelope, the shape a manager sends all run long: no verdict to bind.
LIVE_EDIT = (
    '{"version":2,"author":"planner","commands":[{"op":"cancel","id":"api","reason":"park"}]}'
)


@pytest.mark.reads_recipes
def test_the_reply_recipe_sends_a_live_edit_rather_than_binding_it(tmp_path: Path) -> None:
    """Commands with no verdict and no correlation are `onemessagebus send replies`.

    `onemessagebus reply` binds a reply to a pending question and refuses one with nothing
    to bind to, which is most of a run for a live edit; `send replies` is routed to the
    run's `commands` queue by the layout and judged by the same validators. The envelope
    reaches the bus byte for byte on stdin, piped or named as a file.
    """
    checkout, trace = _checkout(tmp_path)

    piped = _run(checkout, trace, "channel-reply", "run-1", stdin=f"{LIVE_EDIT}\n")

    assert piped.returncode == 0, piped.stderr
    assert trace.read_text().splitlines() == [
        "uv run onemessagebus send replies --config config/onemessagebus.yaml "
        "--transport-dir runs/run-1/channel",
        f"stdin {LIVE_EDIT}",
    ]

    trace.unlink()
    (checkout / "edit.json").write_text(LIVE_EDIT, encoding="utf-8")
    named = _run(checkout, trace, "channel-reply", "run-1", "edit.json")

    assert named.returncode == 0, named.stderr
    assert trace.read_text().splitlines() == [
        "uv run onemessagebus send replies --config config/onemessagebus.yaml "
        "--transport-dir runs/run-1/channel --file edit.json",
    ]


@pytest.mark.reads_recipes
def test_the_reply_recipe_binds_a_verdict_and_a_named_correlation(tmp_path: Path) -> None:
    """A verdict, or a caller naming the question, is `onemessagebus reply surfaces`.

    The correlation is forwarded as the caller typed it, so the bus binds the reply to that
    question and no other; an envelope carrying commands beside it goes the same way,
    because `--correlation` is a statement about which question it answers.
    """
    checkout, trace = _checkout(tmp_path)
    verdict = '{"version":3,"completion":true,"message":"main"}'

    bound = _run(
        checkout, trace, "channel-reply", "run-1", "--correlation", "c-1", stdin=f"{verdict}\n"
    )

    assert bound.returncode == 0, bound.stderr
    assert trace.read_text().splitlines() == [
        "uv run onemessagebus reply surfaces --config config/onemessagebus.yaml "
        "--transport-dir runs/run-1/channel --correlation c-1",
        f"stdin {verdict}",
    ]

    trace.unlink()
    steered = _run(
        checkout, trace, "channel-reply", "run-1", "--correlation", "c-1", stdin=f"{LIVE_EDIT}\n"
    )

    assert steered.returncode == 0, steered.stderr
    assert trace.read_text().splitlines()[0].startswith("uv run onemessagebus reply surfaces")


@pytest.mark.reads_recipes
def test_the_reply_recipe_names_an_envelope_file_it_cannot_read(tmp_path: Path) -> None:
    """An envelope file that cannot be read is refused by name, and the bus is not reached.

    Which verb an envelope needs is read off its content, so a file that cannot be opened
    has no content to read. Sending it anyway would leave the bus to report something else
    as the fault; the recipe names the file and stops instead.
    """
    checkout, trace = _checkout(tmp_path)

    unreadable = _run(checkout, trace, "channel-reply", "run-1", "absent.json")

    assert unreadable.returncode == 2, f"{unreadable.stdout}{unreadable.stderr}"
    assert "the envelope file 'absent.json' could not be read" in unreadable.stderr, (
        unreadable.stderr
    )
    traced = trace.read_text(encoding="utf-8") if trace.exists() else ""
    assert "onemessagebus" not in traced, f"an unreadable envelope reached the bus:\n{traced}"


@pytest.mark.reads_recipes
def test_the_reply_recipe_names_an_envelope_it_cannot_read_from_stdin(tmp_path: Path) -> None:
    """A stdin that cannot be read is refused by name, and the bus is not reached.

    A directory handed over as stdin opens and then fails every read. That is a read that
    failed rather than an empty envelope, so the recipe says so instead of sending nothing.
    """
    checkout, trace = _checkout(tmp_path)
    unreadable = os.open(checkout, os.O_RDONLY)
    try:
        refused = _run(checkout, trace, "channel-reply", "run-1", stdin_fd=unreadable)
    finally:
        os.close(unreadable)

    assert refused.returncode == 2, f"{refused.stdout}{refused.stderr}"
    assert "the envelope could not be read from stdin" in refused.stderr, refused.stderr
    traced = trace.read_text(encoding="utf-8") if trace.exists() else ""
    assert "onemessagebus" not in traced, f"an unreadable envelope reached the bus:\n{traced}"


#: Records argv one word per line rather than as one joined string. The shared trace
#: above joins with spaces, which cannot tell `--title "Add the thing"` from three
#: separate words — and that is exactly the difference a quoting bug in a wrapper makes.
ARGV_RECORDING_UV = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$@" >>"$TRACE_FILE"
"""


@pytest.mark.reads_recipes
def test_the_publish_branch_recipe_forwards_a_title_as_one_word(tmp_path: Path) -> None:
    """A change request's title is prose, so it reaches the verb as one argument.

    `--title` is the one argument here that will routinely carry spaces, and a recipe
    that let the shell re-split it would not fail — `onevcs` would take the first word
    as the title and then refuse `the` as an unexpected argument, or, worse, accept a
    truncated title. The shared delegation trace joins argv back into one string and so
    cannot see the difference; this one records a word per line.
    """
    checkout, trace = _checkout(tmp_path)
    (checkout / "bin/uv").write_text(ARGV_RECORDING_UV)
    title = "Add the thing"

    result = _run(
        checkout,
        trace,
        "publish-branch",
        "claude/work",
        "--repo",
        "/checkout",
        "--title",
        title,
    )

    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == [
        # The drafter's policy read and its checkout lookup. A word of either reaching
        # the invocation below would be a wrapper rewriting what the caller typed.
        "run",
        "onevcs",
        "rules",
        "check",
        "/checkout",
        "run",
        "onevcs",
        "resolve",
        "/checkout",
        "run",
        "onevcs",
        "publish-branch",
        "claude/work",
        "--repo",
        "/checkout",
        "--title",
        title,
    ]


@pytest.mark.reads_recipes
def test_lint_llm_validate_reaches_the_validator(tmp_path: Path) -> None:
    """The validation-only recipe reaches llmlint without entering a judging path."""
    checkout, trace = _checkout(tmp_path)
    llmlint = checkout / "bin" / "llmlint"
    llmlint.write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\nprintf \'llmlint %s\\n\' "$*" >>"$TRACE_FILE"\n'
    )
    llmlint.chmod(0o755)

    result = _run(checkout, trace, "lint-llm-validate", "docs/guide.md")

    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == ["llmlint validate docs/guide.md"]


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    ("invocation", "envelope"),
    [
        (("channel-approve", "run-1"), '{"version":3,"completion":true,"reason":"approved"}'),
        (
            ("channel-reject", "run-1", "the gate never ran"),
            '{"version":3,"completion":false,"reason":"the gate never ran",'
            '"message":"the gate never ran"}',
        ),
        (
            ("channel-continue", "run-1", "split the api node"),
            '{"version":3,"completion":false,"reason":"split the api node",'
            '"message":"split the api node"}',
        ),
    ],
    ids=("approve", "reject", "continue"),
)
def test_a_verdict_recipe_sends_its_envelope_through_the_reply_recipe(
    tmp_path: Path, invocation: tuple[str, ...], envelope: str
) -> None:
    """The three verdicts are spellings of one envelope, and `channel-reply` sends it.

    So a verdict reaches the channel by the one route every reply takes — the bus's
    `reply surfaces` over this host's configuration and the run's own channel — rather
    than by a second path beside it.
    """
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, *invocation)

    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == [
        "uv run onemessagebus reply surfaces --config config/onemessagebus.yaml "
        "--transport-dir runs/run-1/channel",
        f"stdin {envelope}",
    ]


@pytest.mark.reads_recipes
def test_a_verdict_message_reaches_the_envelope_as_json_rather_than_as_text(
    tmp_path: Path,
) -> None:
    """A rejection is prose the planner typed, and prose contains quotes and newlines.

    Interpolated into a template, either one produces an envelope the CLI refuses —
    or, worse, one it accepts with the reason truncated at the first quote.
    """
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, "channel-reject", "run-1", 'it said "no"; try\nagain')

    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == [
        "uv run onemessagebus reply surfaces --config config/onemessagebus.yaml "
        "--transport-dir runs/run-1/channel",
        'stdin {"version":3,"completion":false,"reason":"it said \\"no\\"; try\\nagain",'
        '"message":"it said \\"no\\"; try\\nagain"}',
    ]


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    ("invocation", "reason"),
    [
        (("channel-approve", "run-1", "an extra message"), "approve does not accept a message"),
        (("channel-reject", "run-1"), "reject requires a message"),
        (("channel-continue", "run-1"), "continue requires a message"),
    ],
    ids=("approve-with-message", "reject-without", "continue-without"),
)
def test_a_verdict_recipe_refuses_a_shape_the_verdict_does_not_have(
    tmp_path: Path, invocation: tuple[str, ...], reason: str
) -> None:
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, *invocation)

    assert result.returncode == 2, result.stdout
    assert reason in result.stderr
    assert not trace.exists(), "a refused verdict must not reach the channel at all"


@pytest.mark.reads_recipes
def test_the_telemetry_server_recipe_supplies_the_runs_root_the_other_views_read(
    tmp_path: Path,
) -> None:
    """The published verb requires a runs root; the recipe's was optional.

    So the wrapper defaults it to the same directory `just runs` and `just status`
    read, which is what keeps the API serving the runs the planner is looking at.
    The address is supplied on this path too, from `config/read-api.address`: leaving
    the published CLI's own default to stand there is what would let `just dag-ui`
    and this recipe stop finding each other after that file moved.
    """
    checkout, trace = _checkout(tmp_path)
    address = (ROOT / "config/read-api.address").read_text(encoding="utf-8").strip()

    assert _run(checkout, trace, "telemetry-server").returncode == 0
    assert trace.read_text().splitlines() == [
        f"uv run onepipeline-api serve --runs-root runs --bind {address}",
    ]


@pytest.mark.reads_recipes
def test_the_telemetry_server_recipe_binds_the_address_its_one_source_moved_to(
    tmp_path: Path,
) -> None:
    """Move `config/read-api.address` and the no-flag invocation follows it.

    The assertion above would hold against a hardcoded default that happened to
    match; this is the one that fails if the recipe stops reading the file.
    """
    checkout, trace = _checkout(tmp_path)
    (checkout / "config/read-api.address").write_text("0.0.0.0:19000\n", encoding="utf-8")

    assert _run(checkout, trace, "telemetry-server").returncode == 0
    assert trace.read_text().splitlines() == [
        "uv run onepipeline-api serve --runs-root runs --bind 0.0.0.0:19000",
    ]


@pytest.mark.reads_recipes
def test_the_telemetry_server_recipe_leaves_an_explicit_bind_alone(tmp_path: Path) -> None:
    """A caller spelling the published flag owns the whole address, and gets it."""
    checkout, trace = _checkout(tmp_path)

    assert _run(checkout, trace, "telemetry-server", "--bind", "0.0.0.0:19000").returncode == 0
    assert trace.read_text().splitlines() == [
        "uv run onepipeline-api serve --runs-root runs --bind 0.0.0.0:19000",
    ]


@pytest.mark.reads_recipes
def test_the_telemetry_server_recipe_hands_the_api_the_session_it_derives(
    tmp_path: Path,
) -> None:
    """The acting session reaches `--session`, derived the way every recipe derives it.

    This is what makes a mutation from the browser attributable: the API performs
    `stop`, `adopt` and `unwatched` as one acting session, so a server started with
    none owns nothing and is refused every stop it does not force. The identity is
    given the way an operator's shell gives it — the harness variable
    `scripts/launcher-session.sh` reads — rather than as
    `ONEPIPELINE_LAUNCHER_SESSION` directly, so what is asserted is the ladder running
    and not a value passed through.
    """
    checkout, trace = _checkout(tmp_path)
    address = (ROOT / "config/read-api.address").read_text(encoding="utf-8").strip()

    result = _run(
        checkout,
        trace,
        "telemetry-server",
        env={"CLAUDE_CODE_SESSION_ID": "a-manager-session"},
    )

    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == [
        "uv run onepipeline-api serve --runs-root runs --session a-manager-session "
        f"--bind {address}",
    ]


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    "spelling",
    [("--session", "an-explicit-session"), ("--session=an-explicit-session",)],
    ids=("space", "equals"),
)
def test_the_telemetry_server_recipe_leaves_an_explicit_session_alone(
    tmp_path: Path, spelling: tuple[str, ...]
) -> None:
    """A caller spelling `--session` owns the identity, in either spelling.

    Both spellings, because the recipe decides whether to add its own by scanning the
    caller's arguments: a scan that recognised only the separated form would hand the
    CLI two `--session` values and leave which one acts up to the binary, which is the
    one outcome nobody could read off the command line afterwards. The derived session
    is set here too, so what this shows is the caller's winning rather than there
    having been nothing to win against.
    """
    checkout, trace = _checkout(tmp_path)
    address = (ROOT / "config/read-api.address").read_text(encoding="utf-8").strip()

    result = _run(
        checkout,
        trace,
        "telemetry-server",
        *spelling,
        env={"CLAUDE_CODE_SESSION_ID": "the-derived-session"},
    )

    assert result.returncode == 0, result.stderr
    rendered = trace.read_text().splitlines()
    assert rendered == [
        f"uv run onepipeline-api serve --runs-root runs {' '.join(spelling)} --bind {address}",
    ]
    assert rendered[0].count("--session") == 1, (
        f"the recipe rendered {rendered[0]!r}, which names `--session` more than once; "
        "which of the two acts is then the CLI's to decide and unreadable from here"
    )
    assert "the-derived-session" not in rendered[0]


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    ("spelling", "reason"),
    [
        (("--session", "a session"), "--session must be 1-200 characters"),
        (("--session=a;b",), "--session must be 1-200 characters"),
        (("--session",), "--session needs a session id"),
    ],
    ids=("space", "metacharacter", "missing-value"),
)
def test_the_telemetry_server_recipe_refuses_a_caller_session_that_is_not_one(
    tmp_path: Path, spelling: tuple[str, ...], reason: str
) -> None:
    """A typed `--session` is held to the shape a derived one is, before the API sees it.

    It becomes the same ownership credential, so a value `scripts/launcher-session.sh`
    would refuse to derive must not reach `onepipeline-api serve` by being typed instead.
    """
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, "telemetry-server", *spelling)

    assert result.returncode == 2, result.stdout
    assert reason in result.stderr, result.stderr
    assert not trace.exists(), "a refused session must not reach the API at all"


@pytest.mark.reads_recipes
def test_the_telemetry_server_recipe_names_no_session_when_it_can_resolve_none(
    tmp_path: Path,
) -> None:
    """An unattributable host gets no flag rather than an invented identity.

    A `--session` the engine cannot match against a run's recorded launcher owns
    exactly as little as no session at all, so inventing one would buy nothing and
    would make an unattributed server read as attributed — which is the reading an
    operator would act on. `_checkout` clears every variable the ladder reads, so this
    is the default case rather than one this row arranges.
    """
    checkout, trace = _checkout(tmp_path)
    address = (ROOT / "config/read-api.address").read_text(encoding="utf-8").strip()

    assert _run(checkout, trace, "telemetry-server").returncode == 0
    assert trace.read_text().splitlines() == [
        f"uv run onepipeline-api serve --runs-root runs --bind {address}",
    ]


@pytest.mark.reads_recipes
def test_the_telemetry_server_recipe_keeps_a_malformed_inherited_session_and_says_so(
    tmp_path: Path,
) -> None:
    """An inherited identity of the wrong shape is reported, and still the one acted as.

    `scripts/launcher-session.sh` keeps an exported `ONEPIPELINE_LAUNCHER_SESSION` even
    when it fails the shape check, because the run it names was launched under it by
    the process that exported it: dropping it would leave this server unattributed while
    its parent is attributed. So through a real caller the value reaches `--session`
    unchanged, the harness variable set beside it is *not* what wins, and the finding
    reaches stderr naming who needs repairing rather than passing silently.
    """
    checkout, trace = _checkout(tmp_path)
    address = (ROOT / "config/read-api.address").read_text(encoding="utf-8").strip()

    result = _run(
        checkout,
        trace,
        "telemetry-server",
        env={
            "ONEPIPELINE_LAUNCHER_SESSION": "inherited;session",
            "CLAUDE_CODE_SESSION_ID": "the-derived-session",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "the inherited ONEPIPELINE_LAUNCHER_SESSION is not the shape" in result.stderr
    assert "it is kept" in result.stderr
    assert trace.read_text().splitlines() == [
        "uv run onepipeline-api serve --runs-root runs --session inherited;session "
        f"--bind {address}",
    ]


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    ("invocation", "caller"),
    [(("telemetry-server",), "telemetry-server"), (("runs",), "onepipeline")],
    ids=("telemetry-server", "onepipeline"),
)
@pytest.mark.parametrize(
    ("helper_text", "refusal"),
    [
        (None, "required helper is not a readable regular file"),
        # Present and readable, and failing as it is sourced — the way a truncated or
        # hand-edited copy fails — which is the other of the two refusals.
        ("return 1\n", "is readable but could not be loaded"),
    ],
    ids=("missing", "unloadable"),
)
def test_a_caller_that_cannot_load_the_session_helper_refuses_naming_it(
    tmp_path: Path,
    invocation: tuple[str, ...],
    caller: str,
    helper_text: str | None,
    refusal: str,
) -> None:
    """Both callers of the acting-session ladder refuse, by name, when it cannot load.

    Bash's own diagnostic for a failed `.` names neither the recipe nor a way out, and
    an identity that could not be established must not reach the CLI as none at all —
    that would be an unattributed server or view presented as an ordinary one.
    """
    checkout, trace = _checkout(tmp_path)
    helper = checkout / "scripts" / "launcher-session.sh"
    if helper_text is None:
        helper.unlink()
    else:
        helper.write_text(helper_text, encoding="utf-8")

    result = _run(checkout, trace, *invocation)

    assert result.returncode != 0, result.stdout
    assert any(
        line.startswith(f"{caller}: ") and refusal in line for line in result.stderr.splitlines()
    ), result.stderr
    assert "launcher-session.sh" in result.stderr
    assert "just bootstrap" in result.stderr
    assert not trace.exists(), "nothing may reach the CLI without an identity decided"


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    ("invocation", "reason"),
    [
        (
            ("telemetry-server", "--port", "not-a-port"),
            "--port must be a port number in 0-65535",
        ),
        (("telemetry-server", "--port", "70000"), "--port must be a port number in 0-65535"),
        (("telemetry-server", "--host", "127.0.0.1:8765"), "--host must be a hostname"),
        (
            ("telemetry-server", "--bind", "0.0.0.0:19000", "--port", "9000"),
            "--bind names the whole address",
        ),
    ],
    ids=("port-word", "port-range", "host-with-port", "bind-and-port"),
)
def test_the_telemetry_server_recipe_refuses_an_address_half_it_cannot_join(
    tmp_path: Path, invocation: tuple[str, ...], reason: str
) -> None:
    """`--host` and `--port` are joined into one `HOST:PORT` word before they leave.

    So a host carrying its own colon, or a port that is not a number, would reach
    `--bind` as an address neither this script nor the caller meant — and the CLI
    would report a bind failure for a value it was never given.
    """
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, *invocation)

    assert result.returncode == 2, result.stdout
    assert reason in result.stderr
    assert not trace.exists(), "a refused invocation must not reach the read API at all"


@pytest.mark.reads_recipes
def test_the_telemetry_server_recipe_renders_host_and_port_as_one_bind(
    tmp_path: Path,
) -> None:
    """Two flags became one, and the recipe keeps taking both."""
    checkout, trace = _checkout(tmp_path)

    assert _run(checkout, trace, "telemetry-server", "--port", "9000").returncode == 0
    assert (
        _run(
            checkout, trace, "telemetry-server", "--runs-dir", "/elsewhere", "--host", "0.0.0.0"
        ).returncode
        == 0
    )

    assert trace.read_text().splitlines() == [
        "uv run onepipeline-api serve --runs-root runs --bind 127.0.0.1:9000",
        "uv run onepipeline-api serve --runs-root /elsewhere --bind 0.0.0.0:8765",
    ]


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    ("invocation", "delegated"),
    [
        (
            ("telemetry-server", "--runs-dir=/elsewhere"),
            "uv run onepipeline-api serve --runs-root /elsewhere --bind 127.0.0.1:8765",
        ),
        (
            ("telemetry-server", "--runs-root=/elsewhere"),
            "uv run onepipeline-api serve --runs-root /elsewhere --bind 127.0.0.1:8765",
        ),
        (
            ("telemetry-server", "--port=9000"),
            "uv run onepipeline-api serve --runs-root runs --bind 127.0.0.1:9000",
        ),
        (
            ("telemetry-server", "--host=0.0.0.0"),
            "uv run onepipeline-api serve --runs-root runs --bind 0.0.0.0:8765",
        ),
    ],
    ids=("runs-dir", "runs-root", "port", "host"),
)
def test_the_telemetry_server_recipe_takes_a_flag_in_either_spelling(
    tmp_path: Path, invocation: tuple[str, ...], delegated: str
) -> None:
    """`--flag value` and `--flag=value` are one flag, and an operator types both."""
    checkout, trace = _checkout(tmp_path)

    assert _run(checkout, trace, *invocation).returncode == 0
    assert trace.read_text().splitlines() == [delegated]


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    ("flag", "reason"),
    [
        ("--runs-dir", "--runs-dir needs a directory"),
        ("--host", "--host needs an address"),
        ("--port", "--port needs a port"),
    ],
    ids=("runs-dir", "host", "port"),
)
def test_the_telemetry_server_recipe_names_the_flag_it_was_given_nothing_for(
    tmp_path: Path, flag: str, reason: str
) -> None:
    """A flag with its value missing must not be forwarded as if it had one."""
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, "telemetry-server", flag)

    assert result.returncode == 2, result.stdout
    assert reason in result.stderr
    assert not trace.exists(), "a refused invocation must not reach the read API at all"


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    "invocation",
    [("channel-surface", "run-1"), ("channel-surface", "run-1", "-")],
    ids=("no-text", "dash"),
)
def test_a_surface_with_no_text_is_read_from_stdin(
    tmp_path: Path, invocation: tuple[str, ...]
) -> None:
    """How an agent pipes a long update in: no text, or `-`, means read it."""
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, *invocation, stdin="a long update\nover two lines\n")

    assert result.returncode == 0, result.stderr
    # The whole update reaches the CLI rather than its first line, and it reaches it on
    # stdin: the trace records the command line, then one `stdin` line per line piped
    # through to the verb.
    assert trace.read_text().splitlines() == [
        "uv run onepipeline surface --kind check-in run-1",
        "stdin a long update",
        "stdin over two lines",
    ]


@pytest.mark.reads_recipes
def test_a_surface_with_nothing_to_say_is_refused(tmp_path: Path) -> None:
    """An empty update would reset the planner's pacemaker while saying nothing."""
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, "channel-surface", "run-1", stdin="   \n")

    assert result.returncode == 2, result.stdout
    assert "status update must be a non-empty string" in result.stderr
    assert not trace.exists()


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (None, "could not read"),
        ("8765\n", "must hold one HOST:PORT with a port in 0-65535, not '8765'"),
        ("\n", "must hold one HOST:PORT with a port in 0-65535, not ''"),
        # Five digits and not a port. The shape check alone accepted this, so the
        # published CLI was the first thing to say so — about an address neither this
        # script nor the file it came from was named in.
        ("127.0.0.1:70000\n", "with a port in 0-65535, not '127.0.0.1:70000'"),
    ],
    ids=("unreadable", "no-colon", "empty", "port-out-of-range"),
)
def test_the_telemetry_server_recipe_refuses_an_address_file_that_is_not_one(
    tmp_path: Path, content: str | None, reason: str
) -> None:
    """Split on a colon that is not there, `--bind 8765:8765` is what would be asked for.

    The file is this repository's, not an operator's, so a broken one is a repair to
    name rather than a value to pass on — and only an invocation that needs the
    default ever reads it.
    """
    checkout, trace = _checkout(tmp_path)
    address = checkout / "config/read-api.address"
    if content is None:
        address.unlink()
    else:
        address.write_text(content, encoding="utf-8")

    result = _run(checkout, trace, "telemetry-server", "--port", "9000")

    assert result.returncode == 2, result.stdout
    assert reason in result.stderr
    assert not trace.exists(), "a refused invocation must not reach the read API at all"


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    "spelling", ["--persona-dir {dir}", "--persona-dir={dir}"], ids=("space", "equals")
)
def test_the_new_persona_recipe_scaffolds_where_it_is_told_to(
    tmp_path: Path, spelling: str
) -> None:
    """A persona tree elsewhere is what a scratch persona is drafted in."""
    checkout, trace = _checkout(tmp_path)
    elsewhere = tmp_path / "scratch" / "personas"

    result = _run(
        checkout, trace, "new-persona", "draft", *spelling.format(dir=elsewhere).split(" ")
    )

    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == ["uv run oneagentgraph persona new draft"]
    assert elsewhere.is_dir(), "the named directory is where the CLI has to have run"


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    ("invocation", "reason"),
    [
        (("new-persona", "--persona-dir"), "--persona-dir needs a directory"),
        (("new-persona",), "usage: new-persona.sh"),
    ],
    ids=("no-directory", "no-name"),
)
def test_the_new_persona_recipe_refuses_an_invocation_it_cannot_act_on(
    tmp_path: Path, invocation: tuple[str, ...], reason: str
) -> None:
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, *invocation)

    assert result.returncode == 2, result.stdout
    assert reason in result.stderr
    assert not trace.exists()


@pytest.mark.reads_recipes
def test_the_new_persona_recipe_scaffolds_into_this_repositorys_persona_tree(
    tmp_path: Path,
) -> None:
    """The published verb writes into the working directory, so the wrapper picks it."""
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, "new-persona", "crozier/corpus")

    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == ["uv run oneagentgraph persona new crozier/corpus"]
    # The recipe's contract is where the file lands, so the directory the CLI ran in
    # is the thing to assert: the persona tree, not wherever the operator invoked it.
    assert (checkout / "personas").is_dir()


@pytest.mark.reads_recipes
def test_the_replan_recipe_says_where_its_derivation_went(tmp_path: Path) -> None:
    """`replan` has no successor verb, and the recipe says so rather than doing something else.

    The engine reconciles a live desired graph continuously, so there is no
    between-rounds step for a derivation to happen in: a change to the plan is a live
    edit on the running graph. The recipe names the verb that sends one and reaches no
    CLI at all, because there is none to reach.
    """
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, "replan", "authoring:probe", "result.json")

    assert result.returncode == 2
    assert "just channel-reply" in result.stderr
    assert not trace.exists()


def _live_dispatch_status_dir(tmp_path: Path) -> Path:
    """A live dispatch's status directory, in the shape the agent wrapper validates.

    `scripts/oneharness-agent.sh` accepts only `/*/orchestrator-watchdog-*/agent`, and
    the two markers below are the ones its dispatcher watches to decide whether the
    agent it launched is still alive.
    """
    status_dir = tmp_path / "orchestrator-watchdog-live" / "agent"
    status_dir.mkdir(parents=True)
    (status_dir / "agent.pid").write_text("111111\n", encoding="utf-8")
    (status_dir / "agent.done").write_text("111111\n", encoding="utf-8")
    return status_dir


#: What `scripts/oneharness-agent.sh` does to whatever `ORCHESTRATOR_AGENT_STATUS_DIR`
#: names, reduced to the two writes that matter here: it claims `agent.pid` for itself
#: and clears the terminal markers. The real wrapper is doubled at this one point
#: because it then blocks in its heartbeat loop waiting on a harness turn; the
#: hijacking it is being held to happens before that, and this reproduces it exactly.
STATUS_CLAIMING_UV = """#!/usr/bin/env bash
set -euo pipefail
printf 'uv %s\\n' "$*" >>"$TRACE_FILE"
printf 'bin %s\\n' "${ONEAGENTGRAPH_ONEHARNESS_BIN:-<unset>}" >>"$TRACE_FILE"
printf 'status %s\\n' "${ORCHESTRATOR_AGENT_STATUS_DIR:-<unset>}" >>"$TRACE_FILE"
printf 'history %s\\n' "${ONEHARNESS_HISTORY_DIR:-<unset>}" >>"$TRACE_FILE"
printf 'labels %s\\n' "${ONEHARNESS_HISTORY_LABELS:-<unset>}" >>"$TRACE_FILE"
if [ -n "${ORCHESTRATOR_AGENT_STATUS_DIR:-}" ]; then
  printf '%s\\n' "$$" >"$ORCHESTRATOR_AGENT_STATUS_DIR/agent.pid"
  rm -f "$ORCHESTRATOR_AGENT_STATUS_DIR/agent.done"
fi
"""


@pytest.mark.reads_recipes
def test_smoke_spends_its_turn_on_this_repositorys_agent_harness(tmp_path: Path) -> None:
    """The published verb generates its own config, which declares none of these identities.

    `oneagentgraph smoke` writes a throwaway `oneharness.toml` naming plain
    `claude-code` and runs plain `oneharness` against it, so a bare delegation answers
    `no harness selected` and never exercises the launch path the smoke exists to
    prove. `scripts/oneharness-agent.sh` is what forces this repository's agent
    config, and the recipe owes it.
    """
    checkout, trace = _checkout(tmp_path)
    (checkout / "bin/uv").write_text(STATUS_CLAIMING_UV)

    result = _run(checkout, trace, "smoke")

    assert result.returncode == 0, result.stderr
    traced = trace.read_text().splitlines()
    assert traced[0] == "uv run oneagentgraph smoke"
    assert traced[1] == f"bin {checkout / 'scripts/oneharness-agent.sh'}"


@pytest.mark.reads_recipes
def test_smoke_does_not_hijack_the_live_dispatch_it_runs_inside(tmp_path: Path) -> None:
    """The regression three dead dispatches paid for.

    The pre-push hook runs `just smoke` whenever the launch path changed, and a
    dispatched agent pushes from inside its own dispatch — so this recipe runs with
    that dispatch's `ORCHESTRATOR_AGENT_STATUS_DIR` in its environment.
    `scripts/oneharness-agent.sh` takes that value as-is, claims `agent.pid` for
    itself and clears the terminal markers, so a smoke that passes its caller's value
    down hands the nested turn the liveness protocol its own dispatcher is watching.
    When that turn ends without writing `agent.done`, the dispatcher reads a tracked
    pid that is gone with no exit recorded and kills the tree: "the agent harness
    process vanished mid-turn without recording an exit".
    """
    checkout, trace = _checkout(tmp_path)
    (checkout / "bin/uv").write_text(STATUS_CLAIMING_UV)
    live = _live_dispatch_status_dir(tmp_path)

    result = _run(checkout, trace, "smoke", status_dir=live)

    assert result.returncode == 0, result.stderr
    handed_down = next(
        line.removeprefix("status ")
        for line in trace.read_text().splitlines()
        if line.startswith("status ")
    )
    # Isolated, and in the shape the real wrapper accepts — a smoke that picked any
    # other shape would be refused by `scripts/oneharness-agent.sh` rather than run.
    assert handed_down != str(live)
    assert Path(handed_down).name == "agent"
    assert Path(handed_down).parent.name.startswith("orchestrator-watchdog-")
    # The live dispatch's own protocol is exactly as it was.
    assert (live / "agent.pid").read_text(encoding="utf-8") == "111111\n"
    assert (live / "agent.done").read_text(encoding="utf-8") == "111111\n"
    # And the isolated directory did not outlive the run.
    assert not Path(handed_down).exists()


@pytest.mark.reads_recipes
def test_smoke_reads_back_a_history_store_nothing_else_is_writing_to(tmp_path: Path) -> None:
    """The smoke judges the record it just wrote, so it must be the only writer.

    `orchestrator-smoke` pointed the turn at its own history directory and stamped it
    with this tier's own labels. Left inheriting the ambient store, the smoke reads
    back whichever dispatch on this host wrote last and judges that instead.
    """
    checkout, trace = _checkout(tmp_path)
    (checkout / "bin/uv").write_text(STATUS_CLAIMING_UV)
    ambient = tmp_path / "ambient-history"
    ambient.mkdir()

    result = _run(
        checkout,
        trace,
        "smoke",
        env={"ONEHARNESS_HISTORY_DIR": str(ambient), "ONEHARNESS_HISTORY_LABELS": "role=agent"},
    )

    assert result.returncode == 0, result.stderr
    traced = dict(line.split(" ", 1) for line in trace.read_text().splitlines() if " " in line)
    assert traced["history"] != str(ambient)
    assert Path(traced["history"]).name == "history"
    assert traced["labels"].startswith("role=smoke,")


@pytest.mark.reads_recipes
def test_the_channel_surface_recipe_refuses_an_invocation_it_cannot_act_on(
    tmp_path: Path,
) -> None:
    """A surface with no run id names no channel, so it is a usage error, not an empty call."""
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, "channel-surface")

    assert result.returncode == 2, result.stdout
    assert "usage: planner-surface.sh <run-id> [text]" in result.stderr
    assert not trace.exists()


#: The credential this checkout supplies from its own gitignored `.env`, and what stands
#: in for it below. Not a real token, and never asserted as one — what is asserted is
#: that the name arrived at the command the recipe delegates to.
BOARD_CREDENTIAL = "GH_PROJECTS_TOKEN"
PLANTED_CREDENTIAL = "not-a-real-token-planted-by-this-journey"

#: Records whether the board credential reached the delegated command, which the shared
#: trace cannot say: it records argv, and a credential is not on one.
CREDENTIAL_RECORDING_UV = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "${GH_PROJECTS_TOKEN-<unset>}" >>"$TRACE_FILE"
"""


class BoardRecipe(NamedTuple):
    """One recipe that reads the plan store, and the arguments that make it read."""

    recipe: str
    arguments: tuple[str, ...]


#: Which board recipes read the plan store, and the argument each takes. `just plans` is
#: the reader, the other three are this repository's own commands over the same store.
BOARD_RECIPES = (
    BoardRecipe("plans", ("project", "list")),
    BoardRecipe("check-plan", ("plans:example",)),
    BoardRecipe("copy-plan", ("authoring:example",)),
    BoardRecipe("approve-design", ("plans:example",)),
)


@pytest.fixture
def without_the_board_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the credential the enclosing dispatch carries, for the run of one journey.

    Every worker verifies itself by running this suite from inside a dispatch, and a
    launch exports this name into it — so a journey that inherited it would be asserting
    about the host's own credential rather than about the one the checkout under test
    supplies. `scripts/credentials-env.sh` never overrides a name the process already
    defines, deliberately, which is exactly what would make that inheritance invisible.
    """
    monkeypatch.delenv(BOARD_CREDENTIAL, raising=False)


def _board_checkout(tmp_path: Path, credentials: str | None) -> tuple[Path, Path]:
    """A checkout whose board commands are traced for the credential they were handed."""
    checkout, trace = _checkout(tmp_path)
    # Written rather than copied: this repository's own `onetaskgraph.yaml` is outside
    # `recipeWorkspace`, so a journey in this tier that read it would replay a verdict
    # recorded before it changed. What it states here is the shape `scripts/plan-store.sh`
    # reads a credential name out of; that the *real* source still spells it `token_env`
    # and still names this variable is `tests/test_plan_source_roots.py`'s to hold.
    (checkout / "onetaskgraph.yaml").write_text(
        "default_sources: [plans]\n"
        "sources:\n"
        "  plans:\n"
        "    plugin: github-projects\n"
        "    config:\n"
        f"      token_env: {BOARD_CREDENTIAL}\n",
        encoding="utf-8",
    )
    if credentials is not None:
        (checkout / ".env").write_text(credentials, encoding="utf-8")
    (checkout / "bin/uv").write_text(CREDENTIAL_RECORDING_UV, encoding="utf-8")
    (checkout / "bin/uv").chmod(0o755)
    # `just plans` reads the CLI out of this checkout's own environment rather than
    # through `uv`, so the recorder stands there too.
    store = checkout / ".venv" / "bin" / "onetaskgraph"
    store.parent.mkdir(parents=True)
    store.write_text(CREDENTIAL_RECORDING_UV, encoding="utf-8")
    store.chmod(0o755)
    return checkout, trace


@pytest.mark.parametrize("board", BOARD_RECIPES, ids=[board.recipe for board in BOARD_RECIPES])
@pytest.mark.reads_recipes
@pytest.mark.usefixtures("without_the_board_credential")
def test_a_board_recipe_is_handed_the_credential_this_checkout_supplies(
    tmp_path: Path, board: BoardRecipe
) -> None:
    """The half of the credential seam that was missing, driven through the real recipe.

    `scripts/credentials-env.sh` is the one source of what this checkout supplies, and
    for a long time only the launch verbs called it — so a **dispatch** was handed every
    name in the file and the commands that read the plan store were handed none. Each of
    these refused with onetaskgraph's own `environment variable GH_PROJECTS_TOKEN is
    missing or empty` on a host where that file was configured correctly.
    """
    checkout, trace = _board_checkout(tmp_path, f"{BOARD_CREDENTIAL}={PLANTED_CREDENTIAL}\n")

    result = _run(checkout, trace, board.recipe, *board.arguments)

    assert result.returncode == 0, result.stderr
    assert trace.read_text(encoding="utf-8").splitlines()[-1] == PLANTED_CREDENTIAL, (
        f"`just {board.recipe}` delegated without the board credential this checkout "
        f"supplies; the store it reads would refuse it as missing"
    )


@pytest.mark.reads_recipes
@pytest.mark.usefixtures("without_the_board_credential")
def test_a_board_command_that_fails_without_a_credential_names_the_file_that_supplies_one(
    tmp_path: Path,
) -> None:
    """The refusal an operator could not place, answered where it is answerable.

    The store's own words are `environment variable GH_PROJECTS_TOKEN is missing or
    empty`, and this repository's wrapper used to add a pointer at the *source* — which
    on the host where this happened was configured correctly. What was absent was the
    credential, and the file this checkout would have taken it from is the one thing
    neither of them named.
    """
    checkout, trace = _board_checkout(tmp_path, None)
    (checkout / "bin/uv").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    (checkout / "bin/uv").chmod(0o755)

    result = _run(checkout, trace, "check-plan", "plans:example")

    assert result.returncode != 0
    assert str(checkout / ".env") in result.stderr, (
        f"the refusal named no environment file, so an operator reading it learns only "
        f"that a variable is absent:\n{result.stderr}"
    )
    assert BOARD_CREDENTIAL in result.stderr, result.stderr
    assert PLANTED_CREDENTIAL not in result.stderr, "a credential value reached a diagnostic"


@pytest.mark.reads_recipes
@pytest.mark.usefixtures("without_the_board_credential")
def test_a_board_command_that_fails_with_its_credential_present_gets_no_note_and_its_own_status(
    tmp_path: Path,
) -> None:
    """The wrapper answers one refusal and stays out of every other failure.

    A store that fails for any reason but a missing credential — a bad query, a board
    it cannot reach — has said what it has to say. The wrapper's note exists only for
    the refusal an operator cannot place, so with the credential supplied it must add
    nothing, and the exit status the operator reads must be the command's own rather
    than one the wrapper composed.
    """
    checkout, trace = _board_checkout(tmp_path, f"{BOARD_CREDENTIAL}={PLANTED_CREDENTIAL}\n")
    (checkout / "bin/uv").write_text("#!/usr/bin/env bash\nexit 7\n", encoding="utf-8")
    (checkout / "bin/uv").chmod(0o755)

    result = _run(checkout, trace, "check-plan", "plans:example")

    assert result.returncode == 7, (
        f"the command's own status was not passed through: {result.returncode}\n{result.stderr}"
    )
    assert "plan-store:" not in result.stderr, (
        f"the wrapper added a credential note to a failure that was not about one:\n{result.stderr}"
    )
    assert PLANTED_CREDENTIAL not in result.stderr, "a credential value reached a diagnostic"


@pytest.mark.reads_recipes
@pytest.mark.usefixtures("without_the_board_credential")
def test_a_board_refusal_tells_an_existing_env_file_apart_from_a_missing_one(
    tmp_path: Path,
) -> None:
    """The other half of that diagnostic, and the one an operator is likelier to hit.

    A checkout that has an `.env` but no entry for this name is a different repair from a
    checkout that has no `.env` at all — add a line, rather than create a file — and the
    two were one untested branch apart. Naming the wrong one sends somebody to create a
    file that is already there and reads as the pointer being wrong about the checkout.
    """
    checkout, trace = _board_checkout(tmp_path, "SOMETHING_ELSE=unrelated\n")
    (checkout / "bin/uv").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    (checkout / "bin/uv").chmod(0o755)

    result = _run(checkout, trace, "check-plan", "plans:example")

    assert result.returncode != 0
    assert "defines no such name" in result.stderr, (
        f"an `.env` that exists but lacks the name was reported as a missing file, so the "
        f"repair named is to create one that is already there:\n{result.stderr}"
    )
    assert "does not exist" not in result.stderr, result.stderr
    assert str(checkout / ".env") in result.stderr, result.stderr
    assert BOARD_CREDENTIAL in result.stderr, result.stderr


#: A second board source's credential name, beside the one every journey above uses.
#: Not a name this host defines, so nothing inherits it into the checkout under test.
SECOND_BOARD_CREDENTIAL = "PLAN_STORE_SECOND_BOARD_TOKEN"


@pytest.mark.reads_recipes
@pytest.mark.usefixtures("without_the_board_credential")
def test_a_board_refusal_names_each_absent_credential_once_and_no_present_one(
    tmp_path: Path,
) -> None:
    """Every name the store's sources configure, each once, and only the ones missing.

    `onetaskgraph.yaml` can configure several sources, two of them on one credential
    and a third on another, and the wrapper reads every `token_env` out of it rather
    than assuming one. What that has to come to for an operator: one note per name the
    process does not hold, none for a name it does, and no name twice however many
    sources share it.
    """
    checkout, trace = _board_checkout(tmp_path, None)
    (checkout / "onetaskgraph.yaml").write_text(
        "default_sources: [plans]\n"
        "sources:\n"
        "  plans:\n"
        "    plugin: github-projects\n"
        "    config:\n"
        f"      token_env: {BOARD_CREDENTIAL}\n"
        "  archive:\n"
        "    plugin: github-projects\n"
        "    config:\n"
        f"      token_env: {BOARD_CREDENTIAL}\n"
        "  second:\n"
        "    plugin: github-projects\n"
        "    config:\n"
        f"      token_env: {SECOND_BOARD_CREDENTIAL}\n",
        encoding="utf-8",
    )
    (checkout / "bin/uv").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    (checkout / "bin/uv").chmod(0o755)

    result = _run(
        checkout,
        trace,
        "check-plan",
        "plans:example",
        env={SECOND_BOARD_CREDENTIAL: PLANTED_CREDENTIAL},
    )

    assert result.returncode != 0
    notes = [line for line in result.stderr.splitlines() if line.startswith("plan-store:")]
    assert notes == [
        f"plan-store: {BOARD_CREDENTIAL} is not set in this environment, and this checkout "
        f"supplies it from {checkout / '.env'}, which does not exist; create that file with "
        f"'{BOARD_CREDENTIAL}=<value>' in it, then retry"
    ], (
        f"two sources on one absent name and a third on a present one should leave one "
        f"note naming the absent one:\n{result.stderr}"
    )
    assert PLANTED_CREDENTIAL not in result.stderr, "a credential value reached a diagnostic"


@pytest.mark.reads_recipes
@pytest.mark.usefixtures("without_the_board_credential")
def test_a_board_refusal_tells_an_empty_credential_line_apart_from_an_absent_one(
    tmp_path: Path,
) -> None:
    """The third repair, and the one the other two notes would each get wrong.

    `GH_PROJECTS_TOKEN=` is a line the file *has*, so "defines no such name" sends
    somebody to add a second copy of it, and the value is still empty afterwards. What
    is wrong is the value, and that is what the note has to say — without saying what
    the value is, which is nothing here and a credential everywhere else.
    """
    checkout, trace = _board_checkout(tmp_path, f"{BOARD_CREDENTIAL}=\n")
    (checkout / "bin/uv").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    (checkout / "bin/uv").chmod(0o755)

    result = _run(checkout, trace, "check-plan", "plans:example")

    assert result.returncode != 0
    assert "without a value" in result.stderr, (
        f"a credential line with an empty value was reported as a missing line, so the "
        f"repair named is to add one that is already there:\n{result.stderr}"
    )
    assert "defines no such name" not in result.stderr, result.stderr
    assert "does not exist" not in result.stderr, result.stderr
    assert str(checkout / ".env") in result.stderr, result.stderr
    assert BOARD_CREDENTIAL in result.stderr, result.stderr


@pytest.mark.reads_recipes
@pytest.mark.usefixtures("without_the_board_credential")
def test_a_board_refusal_says_an_exported_empty_name_beats_the_files_value(
    tmp_path: Path,
) -> None:
    """The one shape where the file is right and the environment is what is empty.

    `scripts/credentials-env.sh` never overrides a name the process already defines, so
    a name exported empty for one command beats a file that supplies a real value — and
    the store then refuses the same `missing or empty`. The note has to say the value is
    what to fix, where it is set, rather than send somebody to a file that is correct.
    """
    checkout, trace = _board_checkout(tmp_path, f"{BOARD_CREDENTIAL}={PLANTED_CREDENTIAL}\n")
    (checkout / "bin/uv").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    (checkout / "bin/uv").chmod(0o755)

    result = _run(checkout, trace, "check-plan", "plans:example", env={BOARD_CREDENTIAL: ""})

    assert result.returncode != 0
    assert "already exported empty" in result.stderr, (
        f"an exported-empty name over a file that supplies a value was reported as the "
        f"file's fault:\n{result.stderr}"
    )
    assert "defines no such name" not in result.stderr, result.stderr
    assert PLANTED_CREDENTIAL not in result.stderr, "a credential value reached a diagnostic"


#: A recipe line of this repository's justfile: a name at column one, followed by its
#: parameters or its colon. A body line is indented and a comment starts with `#`.
RECIPE_HEADER = re.compile(r"^([A-Za-z0-9_-]+)(?:\s[^:]*)?:")


def _recipes_through_the_plan_store_wrapper() -> frozenset[str]:
    """Every recipe whose body runs `scripts/plan-store.sh`, read off the justfile."""
    recipe = None
    through = set()
    for line in (ROOT / "justfile").read_text(encoding="utf-8").splitlines():
        header = RECIPE_HEADER.match(line)
        if header:
            recipe = header.group(1)
        elif recipe and line.startswith(" ") and "scripts/plan-store.sh" in line:
            through.add(recipe)
    return frozenset(through)


@pytest.mark.reads_recipes
def test_every_recipe_through_the_plan_store_wrapper_is_a_board_recipe_here() -> None:
    """`BOARD_RECIPES` is an inventory, so the justfile is what it is held to.

    A recipe added to go through the wrapper and not added here would delegate with, or
    without, the credential unobserved; one renamed would leave a row driving a recipe
    that no longer exists.
    """
    listed = frozenset(board.recipe for board in BOARD_RECIPES)
    through = _recipes_through_the_plan_store_wrapper()
    assert through, "no recipe goes through scripts/plan-store.sh, so this inventory is stale"
    assert listed == through, (
        f"BOARD_RECIPES names {sorted(listed)} but the justfile routes {sorted(through)} "
        f"through scripts/plan-store.sh"
    )


@pytest.mark.reads_recipes
def test_the_plan_store_wrapper_refuses_to_run_nothing(tmp_path: Path) -> None:
    """Handed no command, it says what it is for rather than exiting 0 having done nothing.

    Every recipe names one, so this is the wrapper called by hand or by a recipe edited
    down to the wrapper alone — and a wrapper that established a credential and then
    returned success would read as the command having run.
    """
    checkout, trace = _board_checkout(tmp_path, f"{BOARD_CREDENTIAL}={PLANTED_CREDENTIAL}\n")

    result = subprocess.run(
        [str(checkout / "scripts" / "plan-store.sh")],
        cwd=checkout,
        env={**os.environ, "TRACE_FILE": str(trace)},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
        check=False,
    )

    assert result.returncode == 2, result.stderr
    assert "expected a plan-store command" in result.stderr, result.stderr
    assert not trace.exists(), "the wrapper ran something it was never handed"


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    "sabotage",
    ["missing", "unloadable"],
    ids=["helper-missing", "helper-unloadable"],
)
def test_a_board_recipe_refuses_when_the_credentials_helper_cannot_be_loaded(
    tmp_path: Path, sabotage: str
) -> None:
    """The one source of the names is gone or broken, and the command does not run.

    Running it anyway would hand the store whatever this process happened to hold, which
    on a launching session is the host's own credential — the inheritance the wrapper
    exists to replace with the checkout's. So the refusal comes before the command, and
    it names the helper and the repair rather than the store's own `missing or empty`.
    """
    checkout, trace = _board_checkout(tmp_path, f"{BOARD_CREDENTIAL}={PLANTED_CREDENTIAL}\n")
    helper = checkout / "scripts" / "credentials-env.sh"
    match sabotage:
        case "missing":
            helper.unlink()
        case "unloadable":
            # A file `.` fails on rather than one whose function then fails: a syntax
            # error is what an interrupted edit leaves behind.
            helper.write_text("export_host_credentials() {\n", encoding="utf-8")

    result = _run(checkout, trace, "check-plan", "plans:example")

    assert result.returncode != 0
    assert str(helper) in result.stderr, (
        f"the refusal did not name the helper that could not be loaded:\n{result.stderr}"
    )
    assert "just bootstrap" in result.stderr, result.stderr
    assert not trace.exists(), (
        "the command ran without the checkout's credentials established, so the store "
        "was handed whatever this process held"
    )


#: The task template `just follow-ups` composes its node's task from. Written rather than
#: copied, for the reason the design template above is: what the template *says* is
#: `tests/plan_tooling/`'s to assert against a real dispatch, and these journeys are about
#: where the recipe lands and what it delegates. Every placeholder the composer requires is
#: in it, because a template missing one is refused before anything is delegated.
FOLLOW_UPS_TEMPLATE = "config/follow-up-task.md"
FOLLOW_UPS_TEMPLATE_TEXT = (
    "Verify run @RUN@ onto @BOARD@ from @DRAFTS_ROOT@; validate with @VALIDATE@ in @CHECKOUT@.\n"
    "Decide each status with @BOARD_STATUS@, and read the store with @PLAN_STORE@.\n"
    "Assume the accepted items' fixes — @ACCEPTED_STATUSES@ — listed by @ACCEPTED_FILTER@.\n"
    "@STATUS_VOCABULARY@\n@TICKET_CONTRACT@\n@COMMENT_CONTRACT@\n@REDISPATCH@\n@FEEDBACK@\n"
)


@pytest.mark.reads_recipes
def test_the_follow_ups_recipe_launches_one_direct_node_under_its_graph_on_a_free_run_id(
    tmp_path: Path,
) -> None:
    """`just follow-ups run-1` writes `authoring:run-1-follow-ups` and launches it, gated.

    One direct node naming `graphs/follow-up.yaml`, launched on `--dag-graph off` with no
    other flag. A second launch while a run root already holds the first id launches the
    same project under the next free id, which the project's `name` — the run id the engine
    mints — carries.
    """
    checkout, trace = _checkout(tmp_path)
    (checkout / FOLLOW_UPS_TEMPLATE).write_text(FOLLOW_UPS_TEMPLATE_TEXT, encoding="utf-8")
    # The plan-store CLI this checkout is provisioned with, which is the only program the
    # recipe will write into the composed task: it names its own checkout's or refuses, so
    # a checkout without one launches nothing at all. Linked to the installed CLI rather
    # than written, because the recipe requires an executable and the task carries its path.
    store = checkout / ".venv" / "bin" / "onetaskgraph"
    store.parent.mkdir(parents=True)
    store.symlink_to(ONETASKGRAPH_BIN)
    drafts = checkout / ".follow-ups" / "tasks" / "run-1" / "drafts"
    drafts.mkdir(parents=True)
    (drafts / "20260101T000000Z-noticed.md").write_text("a draft\n", encoding="utf-8")
    launched = (
        "uv run orchestrator-launch-gate authoring:run-1-follow-ups --dag-graph off",
        START_HELP,
        f"uv run onepipeline start {DEFAULTS} authoring:run-1-follow-ups --dag-graph off".replace(
            CHECKOUT, str(checkout.resolve())
        ),
    )

    first = _run(checkout, trace, "follow-ups", "run-1")

    assert first.returncode == 0, first.stderr
    assert trace.read_text().splitlines() == list(launched)
    project = (checkout / ".plans/projects/run-1-follow-ups.md").read_text(encoding="utf-8")
    assert 'title: "run-1-follow-ups"' in project
    assert '"orchestrator.plan-kind": {"kind": "follow-ups", "nodes": ["follow-ups"]}' in project
    node = (checkout / ".plans/tasks/run-1-follow-ups/follow-ups.md").read_text(encoding="utf-8")
    assert '"onepipeline.agent_graph": "graphs/follow-up.yaml"' in node
    assert '"onepipeline.persona": "../personas/follow-up.yaml"' in node
    front_matter = node.split("---\n", 2)[1]
    assert '"onepipeline.repo"' not in front_matter, "a direct node names no repository"
    assert "repositories:" not in front_matter, "a direct node names no repository"
    assert f"Verify run run-1 onto followups from {checkout / '.follow-ups'}" in node
    # The plan store the composed task names: this checkout's own, spelled in full. Never
    # the one on the search path, which answers about whichever checkout provisioned it —
    # the resolution two real runs were made wrong by.
    assert f"read the store with {store}" in node, node
    assert shutil.which("onetaskgraph", path=os.environ["PATH"]) != str(store), (
        "this journey's search path already resolves to the checkout's own store, so the "
        "assertion above would hold however the recipe resolved it"
    )

    (checkout / "runs" / "run-1-follow-ups").mkdir(parents=True)
    second = _run(checkout, trace, "follow-ups", "run-1")

    assert second.returncode == 0, second.stderr
    assert trace.read_text().splitlines() == [*launched, *launched]
    project = (checkout / ".plans/projects/run-1-follow-ups.md").read_text(encoding="utf-8")
    assert 'title: "run-1-follow-ups-2"' in project
