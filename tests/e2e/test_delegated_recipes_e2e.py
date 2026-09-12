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
from typing import NamedTuple, cast

import plan_root_variable
import pytest

ROOT = Path(__file__).resolve().parents[2]

#: Copied into the checkout so a recipe that delegates through a script finds it.
WRAPPER_SCRIPTS = (
    "planner-verdict.sh",
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
    # A launch reads its plan through the standalone CLI this repository installs into
    # the checkout's own `.venv/bin`, so `onepipeline.sh` heals a checkout that carries
    # none before it launches. The checkout here declares no adopted release, which is
    # the case that heal no-ops in — but it has to be there to no-op.
    "onetaskgraph-install.sh",
    "credentials-env.sh",
    "ask-manager-env.sh",
    "ask-manager.sh",
    # `just plan` writes its project under the plan-authoring root this one resolves, so
    # a checkout without it cannot reach a verb at all. The root itself is stated in the
    # environment below rather than discovered, which keeps that resolution — and the
    # project the launch writes — inside this throwaway checkout.
    "plan-root-env.sh",
    # And the operational appendix every dispatched task must carry, which `just plan`
    # hands its planner as text rather than as a path into a checkout that planner cannot
    # see. It reads `config/dispatch-appendix.md`, which `_checkout` copies beside it.
    "dispatch-appendix-env.sh",
    # `just channel-reply` goes through this one, which forwards the caller's own
    # arguments and refuses only an envelope the run's pending blocking question
    # cannot use; the rule it judges by is the wrapper's own, in the helper beside it.
    "channel-reply.sh",
    "ask-manager-contract.sh",
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


#: The plan `just plan` writes and every step after it is about, in this checkout.
PLAN_PROJECT = "authoring:cursor-shape"

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
        f"uv run onepipeline start {DESIGN_PROJECT} --dag-graph off",
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


#: The whole delegation table, as `just` invocation → the command lines it must
#: produce, in order: one for nearly every recipe, and the sequence a recipe that
#: composes several verbs owes. This is the mapping this repository promises, in one
#: place: a recipe that starts naming a different verb, dropping an argument on the
#: way, or reaching only the first of the verbs it composes, fails here rather than in
#: an operator's terminal.
DELEGATIONS = (
    # The two graph flags are the recipe's own addition, and the reason it exists:
    # both ship defaulted to nothing, so a bare launch runs with no agent watching it
    # and opens its change requests with no drafted body.
    Delegation(
        "orchestrate",
        ("authoring:probe",),
        "uv run onepipeline start authoring:probe --dag-graph graphs/dag-scope.yaml"
        " --pr-author-graph graphs/pr-author.yaml",
    ),
    Delegation(
        "orchestrate",
        ("authoring:probe", "--detach"),
        "uv run onepipeline start authoring:probe --detach --dag-graph graphs/dag-scope.yaml"
        " --pr-author-graph graphs/pr-author.yaml",
    ),
    # A caller who names one keeps it: the flags refuse to be given twice, so adding
    # a default over an explicit one would break the launch outright. Per flag, so
    # naming one leaves the other's default in place.
    Delegation(
        "orchestrate",
        ("authoring:probe", "--dag-graph", "off"),
        "uv run onepipeline start authoring:probe --dag-graph off"
        " --pr-author-graph graphs/pr-author.yaml",
    ),
    Delegation(
        "orchestrate",
        ("authoring:probe", "--dag-graph=graphs/other.yaml"),
        "uv run onepipeline start authoring:probe --dag-graph=graphs/other.yaml"
        " --pr-author-graph graphs/pr-author.yaml",
    ),
    Delegation(
        "orchestrate",
        ("authoring:probe", "--pr-author-graph", "graphs/other.yaml"),
        "uv run onepipeline start authoring:probe --pr-author-graph graphs/other.yaml"
        " --dag-graph graphs/dag-scope.yaml",
    ),
    Delegation(
        "orchestrate",
        ("authoring:probe", "--pr-author-graph=graphs/other.yaml", "--dag-graph=off"),
        "uv run onepipeline start authoring:probe"
        " --pr-author-graph=graphs/other.yaml --dag-graph=off",
    ),
    # Adoption attaches a fresh driver to an intact ledger, which already records the
    # graphs its launch chose, so neither default is added to it.
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
        "uv run onepipeline start authoring:cursor-shape --dag-graph off",
        then=_tail(),
    ),
    # `--detach` hands back before the planner has written anything, so there is no plan
    # for the tail to be about and the launch prints the command that finishes it later.
    # That is the one shape where `just plan` reaches a single verb.
    Delegation(
        "plan",
        (BRIEF, "--name", "listing-api", "--max-turns", "40", "--detach"),
        "uv run onepipeline start authoring:listing-api --detach --dag-graph off",
    ),
    # The joined spelling of both, which is a separate parsing path: `--name=` decides
    # the plan path this line names, and `--max-turns=` is absorbed rather than
    # forwarded — which is exactly what its absence from this line asserts.
    Delegation(
        "plan",
        (BRIEF, "--name=listing-api", "--max-turns=40", "--detach"),
        "uv run onepipeline start authoring:listing-api --detach --dag-graph off",
    ),
    # `--no-design-doc` stops the flow after the planner, so the tail is absent here for
    # a different reason than it is absent above: there is nothing to write a document
    # about copying, rather than nothing written yet.
    Delegation(
        "plan",
        (BRIEF, "--no-design-doc"),
        "uv run onepipeline start authoring:cursor-shape --dag-graph off",
    ),
    # `--to` is the tail's own flag and reaches it rather than `onepipeline start`: the
    # destination decides nothing about the planner, and everything about where the plan
    # a person reviews ends up.
    Delegation(
        "plan",
        (BRIEF, "--to", "elsewhere"),
        "uv run onepipeline start authoring:cursor-shape --dag-graph off",
        then=_tail(destination="elsewhere"),
    ),
    # A caller who names an observer keeps it, in either spelling and including their
    # own `off`: the flag refuses to be given twice, so the default is added only when
    # neither spelling was typed. It is the *planner's* observer: the tail is a separate
    # launch of one node that reads a finished plan, so it keeps its own default.
    Delegation(
        "plan",
        (BRIEF, "--dag-graph", "graphs/dag-scope.yaml"),
        "uv run onepipeline start authoring:cursor-shape --dag-graph graphs/dag-scope.yaml",
        then=_tail(),
    ),
    Delegation(
        "plan",
        (BRIEF, "--dag-graph=graphs/other.yaml", "--detach"),
        "uv run onepipeline start authoring:cursor-shape --dag-graph=graphs/other.yaml --detach",
    ),
    # The tail on its own, which is how a plan edited after it was authored is finished:
    # the same six verbs in the same order, reached without a planner being launched at
    # all. Its published line is the design-document launch, because that is the one verb
    # of the six that starts a run.
    Delegation(
        "finish-plan",
        (BRIEF, "--to", "elsewhere"),
        "uv run orchestrator-review-plan authoring:cursor-shape",
        then=(
            "uv run orchestrator-check-plan authoring:cursor-shape",
            "uv run orchestrator-launch-gate authoring:cursor-shape-design --dag-graph off",
            "uv run onepipeline start authoring:cursor-shape-design --dag-graph off",
            "uv run orchestrator-copy-plan authoring:cursor-shape --to elsewhere",
            "uv run orchestrator-plan-locations authoring:cursor-shape --in elsewhere",
        ),
    ),
    Delegation("channel-next", ("run-1",), "uv run onepipeline next run-1"),
    # The read profile is the CLI's own default, so the recipes name no filter and
    # pass one through untouched when the caller does.
    Delegation(
        "channel-next",
        ("run-1", "--filter", "monitor"),
        "uv run onepipeline next run-1 --filter monitor",
    ),
    Delegation("channel-next", ("run-1", "--all"), "uv run onepipeline next run-1 --all"),
    Delegation(
        "monitor",
        ("run-1", "--filter", "monitor"),
        "uv run onepipeline monitor run-1 --filter monitor",
    ),
    Delegation("monitor", ("run-1", "--all"), "uv run onepipeline monitor run-1 --all"),
    Delegation(
        "channel-reply", ("run-1", "reply.json"), "uv run onepipeline reply run-1 reply.json"
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
    # The lookup in front of both landing rows is the drafter's, not the verb's: a
    # `--repo` that is not a directory may still be a registered alias.
    Delegation(
        "repo-recover",
        ("claude/work", "--repo", "/checkout"),
        "uv run onevcs recover claude/work --repo /checkout",
        before=("uv run onevcs resolve /checkout",),
    ),
    # The third landing verb, and the one that closes the gap the other two left: a
    # complete branch no session holds had neither an incomplete marker for `recover`
    # nor a local merge train for `integrate`, so landing one meant raw `git`/`gh`.
    Delegation(
        "publish-branch",
        ("claude/work", "--repo", "/checkout"),
        "uv run onevcs publish-branch claude/work --repo /checkout",
        before=("uv run onevcs resolve /checkout",),
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
        before=("uv run onevcs resolve /checkout",),
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
        "Plan project: authoring:cursor-shape\n\n"
        "## Why\nThe view cannot deep-link "
        "without it.\n\n## Acceptance criteria\n- The shape is stated.\n"
    )
    trace = checkout / "trace"
    uv = checkout / "bin/uv"
    # Records the whole command line, and the reply envelope when one is piped in:
    # a verdict recipe's product is the envelope, so a trace without it would say
    # nothing about the recipe under test. It also answers the engine's own command
    # list, because `just watch` reads that list before it delegates — asking whether
    # the installed engine has the verb at all — and a double that answered nothing
    # would make every row of this table watch a verb it had just been told is absent.
    uv.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf 'uv %s\\n' "$*" >>"$TRACE_FILE"
if [ "$*" = "run onepipeline --help" ]; then
  echo "Commands:"
  echo "  watch       Watch one run until something a supervisor has to act on happens"
  exit 0
fi
if [ ! -t 0 ]; then
  while IFS= read -r line; do printf 'stdin %s\\n' "$line" >>"$TRACE_FILE"; done
fi
exit "${FAKE_UV_EXIT:-0}"
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
    environment.pop("ONEPIPELINE_RUNS_DIR", None)
    # This suite is itself run from inside a dispatch, whose real status directory and
    # history store would otherwise reach the recipe under test. Each journey states
    # the values it wants, and the default is the operator case: none of them.
    for inherited in (
        "ORCHESTRATOR_AGENT_STATUS_DIR",
        "ONEHARNESS_HISTORY_DIR",
        "ONEHARNESS_HISTORY_LABELS",
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
        stdin=None if stdin is not None else subprocess.DEVNULL,
    )


#: The command `scripts/onepipeline.sh` puts every launch through before it reaches the
#: engine: the design-document approval gate. It is derived from the published line below
#: rather than restated per row, and that is the claim rather than a saving — **every**
#: `onepipeline start` this repository makes is gated, so a row that could name it and did
#: not would be a launch that got past.
LAUNCH_GATE = "uv run orchestrator-launch-gate"
STARTS = "uv run onepipeline start "


def _gated(published: str) -> tuple[str, ...]:
    """The gate line a launch owes, and nothing for a command line that launches nothing."""
    if not published.startswith(STARTS):
        return ()
    return (f"{LAUNCH_GATE} {published.removeprefix(STARTS)}",)


@pytest.mark.reads_recipes
@pytest.mark.parametrize("delegation", DELEGATIONS, ids=lambda row: " ".join(row.invocation))
def test_a_delegated_recipe_reaches_its_published_verb(
    tmp_path: Path, delegation: Delegation
) -> None:
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, *delegation.invocation)

    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == [
        *delegation.before,
        *_gated(delegation.published),
        delegation.published,
        *delegation.then,
    ]


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


class ReplyShape(NamedTuple):
    """One way of reaching `just channel-reply`, and what the recipe owes that shape."""

    what: str
    #: A wrapper script to remove from the checkout before running, or `None`.
    without: str | None
    #: What the caller types after the recipe name.
    arguments: tuple[str, ...]
    #: A fragment the refusal must carry, or `None` when the shape is forwarded.
    refuses: str | None


#: The shapes that are not a guarded reply, each of which has to end somewhere better
#: than a shell error. Two are checkouts missing a piece — a guard that judged with no
#: rule would pass every envelope, and a delegate that is not there cannot send one — and
#: three are inputs this recipe deliberately declines to judge, because `onepipeline
#: reply` owns its own surface and reports a usage error better than a guess here would.
REPLY_SHAPES = (
    ReplyShape(
        "a checkout with no contract helper",
        "ask-manager-contract.sh",
        ("run-1",),
        "ask-manager-contract.sh",
    ),
    ReplyShape(
        "a checkout with no onepipeline wrapper",
        "onepipeline.sh",
        ("run-1",),
        "not executable",
    ),
    ReplyShape("no run at all", None, (), None),
    ReplyShape("more arguments than the verb takes", None, ("run-1", "a.json", "b.json"), None),
    ReplyShape("an envelope file that is not there", None, ("run-1", "absent.json"), None),
)


@pytest.mark.reads_recipes
@pytest.mark.parametrize("shape", REPLY_SHAPES, ids=lambda row: row.what)
def test_the_reply_recipe_ends_every_shape_that_is_not_a_guarded_reply(
    tmp_path: Path, shape: ReplyShape
) -> None:
    """A reply the guard cannot judge reaches the verb, and a broken checkout says so.

    Both halves matter for the same reason: this recipe stands between a manager and the
    only channel they have. A missing piece has to be named — a guard with no rule would
    wave every envelope through, which is worse than no guard, and a delegate that is not
    there sends nothing — while an input the guard has no business judging has to go on
    to the verb that does, whose refusal names the argument it could not take.
    """
    checkout, trace = _checkout(tmp_path)
    if shape.without is not None:
        (checkout / "scripts" / shape.without).unlink()

    result = _run(checkout, trace, "channel-reply", *shape.arguments, stdin='{"completion":true}')

    if shape.refuses is not None:
        assert result.returncode != 0, f"{shape.what} was not refused:\n{result.stdout}"
        assert shape.refuses in result.stderr, result.stderr
        assert not trace.exists(), f"{shape.what} reached the published verb:\n{trace.read_text()}"
    else:
        assert result.returncode == 0, result.stderr
        reached = trace.read_text().splitlines()
        assert reached and reached[0].startswith("uv run onepipeline reply"), (
            f"{shape.what} was not passed on to the verb that owns it: {reached}"
        )


#: Every amendment the criteria bar refuses, and the fragment its refusal quotes. One
#: per question that bar asks of an amendment, because an amendment is criteria and the
#: only place it can be held to that bar is here — it reaches a node over the channel
#: rather than through the plan store, so `just check-plan` never sees one.
REFUSED_AMENDMENTS = (
    (
        "state that arrives after the dispatch",
        "The finished branch merges cleanly into its base and its change request's "
        "required checks pass.",
        "required checks pass",
    ),
    (
        "a mechanism where a property belongs",
        "Do not re-research it: run `just gate` and stop there.",
        "names a `just` invocation",
    ),
    (
        "a backtick run that never closes",
        "Keep the `--json form, and just report what it says.",
        "backtick run unclosed",
    ),
)


@pytest.mark.reads_recipes
@pytest.mark.parametrize("what,text,quoted", REFUSED_AMENDMENTS, ids=lambda row: row)
def test_the_reply_recipe_refuses_an_amendment_before_it_reaches_the_verb(
    tmp_path: Path, what: str, text: str, quoted: str
) -> None:
    """Each question the bar asks of an amendment, driven where a manager meets it.

    An `amend` replaces the binding text that becomes part of a node's effective task,
    so it is criteria — and criteria written in the minute after a manager reads a
    failure. What has to hold for every one of them is that the refusal happens **before
    the verb**: nothing is sent, so no other command in the envelope is applied either,
    and the trace the delegate would leave is not there.
    """
    checkout, trace = _checkout(tmp_path)
    envelope = json.dumps({"version": 2, "commands": [{"op": "amend", "id": "work", "text": text}]})

    result = _run(checkout, trace, "channel-reply", "run-1", stdin=envelope)

    assert result.returncode != 0, f"{what} was sent anyway:\n{result.stdout}"
    assert quoted in result.stderr, result.stderr
    assert "nothing was sent" in result.stderr, result.stderr
    assert not trace.exists(), f"{what} reached the published verb:\n{trace.read_text()}"


@pytest.mark.reads_recipes
def test_the_reply_recipe_sends_an_amendment_the_bar_takes(tmp_path: Path) -> None:
    """The half that makes those refusals worth having, and the way this guard goes wrong.

    A narrow guard widens quietly: an amendment stating what the finished tree must carry
    is the ordinary case a manager sends all run long, and it has to reach the verb byte
    for byte with nothing this recipe added to it — and with no judged turn spent, because
    a bare amendment is a correction to a task a review already cleared.
    """
    checkout, trace = _checkout(tmp_path)
    envelope = json.dumps(
        {
            "version": 2,
            "commands": [
                {
                    "op": "amend",
                    "id": "work",
                    "text": "The finished tree carries an assertion whose subject is the "
                    "behaviour this change adds.",
                }
            ],
        }
    )

    result = _run(checkout, trace, "channel-reply", "run-1", stdin=envelope)

    assert result.returncode == 0, result.stderr
    reached = trace.read_text().splitlines()
    assert reached and reached[0].startswith("uv run onepipeline reply"), reached


#: A `python3` that refuses the amendment check with a diagnostic of its own on stdout
#: and a status no refusal uses. The recipe tells a verdict from a broken helper by what
#: was *said* rather than by which status came back — an interpreter dying with a status
#: of its own can collide with a refusal's — so this is the shape that reaches the branch
#: neither of those two rules covers.
LOUD_BROKEN_INTERPRETER = """#!/usr/bin/env bash
echo "python3: something this recipe must not read as a verdict"
exit 4
"""


@pytest.mark.reads_recipes
def test_an_amendment_check_that_fails_loudly_is_named_rather_than_read_as_a_verdict(
    tmp_path: Path,
) -> None:
    """A helper that could not run must never be reported as the envelope's refusal.

    The recipe reads a non-zero status with an empty stdout as a broken helper, so this
    drives the other side of that: a helper that failed *and* said something. Reported as
    the check having failed to run, with the toolchain repair named — and, either way,
    the envelope does not go on unjudged.
    """
    checkout, trace = _checkout(tmp_path)
    # llmlint: ignore[e2e_not_mocked] The recipe is real; a broken interpreter is the input.
    broken = checkout / "bin" / "python3"
    broken.write_text(LOUD_BROKEN_INTERPRETER)
    broken.chmod(0o755)

    result = _run(checkout, trace, "channel-reply", "run-1", stdin='{"completion":true}')

    assert result.returncode != 0, f"an unjudged reply was sent anyway:\n{result.stdout}"
    assert "could not be judged against the criteria bar" in result.stderr, result.stderr
    assert "exited 4" in result.stderr, result.stderr
    assert "something this recipe must not read as a verdict" in result.stderr, (
        f"the interpreter's own account of what failed was dropped, so the manager holds "
        f"a refusal with no cause in it:\n{result.stderr}"
    )
    assert "just bootstrap" in result.stderr, result.stderr
    assert not trace.exists(), f"the reply reached the verb unjudged:\n{trace.read_text()}"


#: A `python3` that refuses every call, for the one failure the guard cannot recover
#: from. It stands on PATH in a checkout with no pinned interpreter beside it, which is
#: what a half-restored checkout is.
BROKEN_INTERPRETER = """#!/usr/bin/env bash
exit 3
"""


@pytest.mark.reads_recipes
def test_a_reply_that_cannot_be_judged_is_named_rather_than_sent_unjudged(
    tmp_path: Path,
) -> None:
    """A guard that could not run says so, and the envelope does not go on regardless.

    The alternative is the failure this whole recipe exists to prevent, one layer up: an
    envelope reaching the channel with nothing having judged it, and a manager told it
    was delivered. Driven by putting a `python3` on PATH that refuses, in a checkout with
    no pinned interpreter beside the wrapper — which is what a half-restored checkout is.
    """
    checkout, trace = _checkout(tmp_path)
    # llmlint: ignore[e2e_not_mocked] The recipe is real; a broken interpreter is the input.
    broken = checkout / "bin" / "python3"
    broken.write_text(BROKEN_INTERPRETER)
    broken.chmod(0o755)

    result = _run(checkout, trace, "channel-reply", "run-1", stdin='{"completion":true}')

    assert result.returncode != 0, f"an unjudged reply was sent anyway:\n{result.stdout}"
    assert "could not be judged" in result.stderr, result.stderr
    # An interpreter that dies with a status of its own can collide with a refusal's, and
    # every refusal names itself on stdout while a dead one names nothing — which is what
    # the recipe reads to tell them apart. This interpreter exits with exactly such a
    # status, so a recipe that stopped checking would refuse an unjudged reply in its own
    # voice with an empty reason where the explanation belongs.
    assert "with nothing to say for it" in result.stderr, (
        f"the recipe read a dead interpreter's exit status as one of its own refusals, "
        f"so a reply nothing judged was refused as though it had been:\n{result.stderr}"
    )
    assert "just bootstrap" in result.stderr, result.stderr
    assert not trace.exists(), f"the reply reached the verb unjudged:\n{trace.read_text()}"


@pytest.mark.reads_recipes
def test_the_reply_recipe_forwards_a_piped_envelope_untouched(tmp_path: Path) -> None:
    """A reply the recipe does not refuse reaches the verb byte for byte, on its stdin.

    The refusal this recipe adds is narrow, and the way a narrow guard goes wrong is by
    quietly widening: an envelope carrying `commands` and no `completion` is a live
    graph edit a manager sends all run long, and it has to arrive as it was written.
    Here there is no run and so nothing pending, which is the other half — a guard that
    could not read a queue must forward rather than refuse, or a manager loses the
    channel whenever the ledger is somewhere it cannot see.
    """
    checkout, trace = _checkout(tmp_path)
    envelope = '{"version":2,"author":"planner","commands":[{"op":"cancel","id":"api"}]}'

    result = _run(checkout, trace, "channel-reply", "run-1", stdin=f"{envelope}\n")

    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == [
        "uv run onepipeline reply run-1",
        f"stdin {envelope}",
    ]


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
        # The drafter's checkout lookup. A word of it reaching the invocation below
        # would be a wrapper rewriting what the caller typed.
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
        (("channel-approve", "run-1"), '{"completion": true, "reason": "approved"}'),
        (
            ("channel-reject", "run-1", "the gate never ran"),
            '{"completion": false, "reason": "the gate never ran", '
            '"message": "the gate never ran"}',
        ),
        (
            ("channel-continue", "run-1", "split the api node"),
            '{"completion": false, "reason": "split the api node", '
            '"message": "split the api node"}',
        ),
    ],
    ids=("approve", "reject", "continue"),
)
def test_a_legacy_verdict_recipe_pipes_the_envelope_reply_accepts(
    tmp_path: Path, invocation: tuple[str, ...], envelope: str
) -> None:
    """`onepipeline reply` takes one envelope; the three verdicts are how it is spelled."""
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, *invocation)

    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == [
        "uv run onepipeline reply run-1",
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
        "uv run onepipeline reply run-1",
        'stdin {"completion": false, "reason": "it said \\"no\\"; try\\nagain", '
        '"message": "it said \\"no\\"; try\\nagain"}',
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
        ("8765\n", "must hold one HOST:PORT, not '8765'"),
        ("\n", "must hold one HOST:PORT, not ''"),
    ],
    ids=("unreadable", "no-colon", "empty"),
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


#: A `uv` that accepts the reply and appends whatever the journey told it to. It stands
#: where the published verb stands, which is the only place a journey can present a
#: second `reached` word: the engine writes `worker` or `supervisor` only by reaching a
#: live two-party conversation, and this suite's stand-in provider runs none.
JOURNALLING_UV = """#!/usr/bin/env bash
set -euo pipefail
printf 'uv %s\\n' "$*" >>"$TRACE_FILE"
if [ ! -t 0 ]; then cat >/dev/null; fi
if [ -n "${JOURNAL_APPEND:-}" ]; then cat -- "$JOURNAL_APPEND" >>"$JOURNAL_FILE"; fi
printf '%s\\n' '{"reply":0,"state":"applied"}'
"""

#: A `uv` whose reply is **accepted and still queued**, at whichever exit status the
#: journey names. Both halves are the point. The engine's word for that state has always
#: been the receipt's `state`, and the status beside it has been spelled two ways: `1`
#: while a queued envelope shared its status with nothing else, and `0` since the engine
#: ruled that a non-zero status from this verb is a rejection to correct. So the recipe's
#: advice cannot be keyed on the status, and this double is what says it is not.
QUEUEING_UV = """#!/usr/bin/env bash
set -euo pipefail
printf 'uv %s\n' "$*" >>"$TRACE_FILE"
if [ ! -t 0 ]; then cat >/dev/null; fi
printf '%s\n' '{"reply":'"${FAKE_REPLY_STATUS:-0}"',"state":"queued","commands":"queued"}'
exit "${FAKE_REPLY_STATUS:-0}"
"""


class QueuedStatus(NamedTuple):
    """One status a queued receipt has arrived at, and what that status was."""

    status: int
    #: For the failure message, so a reader knows which release's spelling failed.
    what: str


#: Both spellings, driven as rows rather than as one number so neither is what this
#: journey is about.
QUEUED_STATUSES = (
    QueuedStatus(0, "the status the engine gives an accepted envelope it has not reconciled"),
    QueuedStatus(1, "the status it gave that envelope before a non-zero one meant a rejection"),
)


@pytest.mark.reads_recipes
@pytest.mark.parametrize("arrival", QUEUED_STATUSES, ids=lambda row: str(row.status))
def test_a_queued_reply_is_told_what_it_waits_for_whatever_status_it_arrives_at(
    tmp_path: Path, arrival: QueuedStatus
) -> None:
    """The one sentence that tells `queued` from `applied`, read off the word not the number.

    `queued` and `applied` differ by one word in the receipt, and the difference is the
    whole state: a live run passes through the first in a second, and a run whose driver
    has died stays in it forever. This recipe's job is to say which of those a manager is
    looking at and what to do about it, and it used to decide that from the exit status —
    which the engine has since changed under it, on the ground that a non-zero status from
    this verb is a rejection to correct.

    A branch still keyed on the number would simply stop firing: the advice would vanish
    on the release that made it most worth printing, and silently, because a queued reply
    at exit 0 reads exactly like an applied one. So both statuses are driven against the
    same receipt, and the sentence is owed under each.
    """
    checkout, trace = _checkout(tmp_path)
    (checkout / "bin/uv").write_text(QUEUEING_UV)

    result = _run(
        checkout,
        trace,
        "channel-reply",
        "run-1",
        stdin='{"completion":true,"reason":"read"}',
        env={"FAKE_REPLY_STATUS": str(arrival.status)},
    )

    assert result.returncode == arrival.status, f"{result.stdout}{result.stderr}"
    assert "they stay queued until something is driving that run" in result.stderr, (
        f"a reply the engine reported {arrival.what} said nothing about what it is "
        f"waiting for, so a manager cannot tell it from an applied one:\n{result.stderr}"
    )
    assert "just orchestrate --adopt run-1" in result.stderr, (
        f"the sentence reports a state without the action that answers it:\n{result.stderr}"
    )
    assert _verb_answer(result)["state"] == "queued", (
        f"the verb's own answer is no longer the whole of this recipe's stdout:\n{result.stdout}"
    )


@pytest.mark.reads_recipes
def test_an_applied_reply_is_not_told_it_is_waiting_for_anything(tmp_path: Path) -> None:
    """The other side, so the sentence above is about `queued` rather than about replying.

    An advice line printed on every acceptance is one a manager stops reading, and it
    would be worse than none: the state it is about is the one they have to act on.
    """
    checkout, trace = _checkout(tmp_path)
    (checkout / "bin/uv").write_text(JOURNALLING_UV)

    result = _run(checkout, trace, "channel-reply", "run-1", stdin='{"completion":true}')

    assert result.returncode == 0, f"{result.stdout}{result.stderr}"
    assert _verb_answer(result)["state"] == "applied"
    assert "they stay queued" not in result.stderr, result.stderr


#: The run every row below replies to, and where its journal lives under the checkout.
JOURNALLED_RUN = "run-1"

#: The node the notes are addressed to, and the two notes themselves. The earlier one is
#: what a recipe reading the journal by recency would answer with.
NOTED_NODE = "api"
EARLIER_NOTE = {
    "op": "note",
    "id": NOTED_NODE,
    "addressee": "worker",
    "text": "the note sent before this one",
}
THIS_NOTE = {
    "op": "note",
    "id": NOTED_NODE,
    "addressee": "worker",
    "text": "the note this reply carries",
}

#: What the earlier note's outcome is recorded as, so a row whose own outcome differs
#: fails loudly if the wrong one is read.
EARLIER_REACHED = "carried"


def _committed_record(command: dict[str, object], reached: str | None) -> str:
    """One `edit-committed` line, in the shape a real run's journal is read to carry.

    Not a second source for that shape: the ask-seam journey named above drives the same
    reader over a journal the real engine wrote, so a wire change fails there while this
    stays self-consistent. What this file adds is the cases that engine cannot be made to
    produce — a second disposition word, and a journal missing this note's outcome while
    carrying an earlier one's.

    `reached` of `None` is the edit committed with no `note-delivered` operation at all,
    which is the other way a correlated read comes up empty, and it must read as "not
    recorded" rather than fall through to somebody else's outcome.
    """
    operations = (
        []
        if reached is None
        else [
            {
                "kind": "note-delivered",
                "node": command["id"],
                "addressee": command["addressee"],
                "text": command["text"],
                "reached": reached,
            }
        ]
    )
    return json.dumps(
        {
            "v": 1,
            "ts": "2026-09-04T12:00:00.000Z",
            "stream": "U-TEST-1",
            "seq": 0,
            "source": "pipeline",
            "kind": "edit-committed",
            "labels": {"run_id": JOURNALLED_RUN},
            "payload": {"author": "planner", "command": command, "operations": operations},
        }
    )


#: The field the verb's answer carries the note outcomes in, and what an unreadable
#: journal is said in instead. `notes_unread` is its own field because nobody having
#: looked and nothing having been decided are opposite states.
NOTES_FIELD = "notes"
NOTES_UNREAD_FIELD = "notes_unread"


def _verb_answer(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    """The one line a successful reply prints, which is the verb's answer and nothing else."""
    printed = result.stdout.strip().splitlines()
    assert len(printed) == 1, (
        f"a successful reply printed {len(printed)} line(s) where the verb's own answer "
        f"is the whole of the success output:\n{result.stdout}"
    )
    # `cast` rather than a validating read: each journey asserts the shape it is about.
    return cast(dict[str, object], json.loads(printed[0]))


#: What the recipe is driven over, and the property it is driven for: it reports the word
#: it finds rather than a word it knows. So the rows are deliberately not this engine's
#: `Reached` vocabulary — restating an enum a doubled journal cannot reconcile would be a
#: second source for it — but a pair that differ, plus one no release has ever written.
#: Passing that third row is what says a value added upstream reaches the manager instead
#: of being dropped for not being on a list. The last row is the note whose outcome is not
#: journalled when the recipe looks, which reads back as `None` rather than as an earlier
#: note's word.
#: `tests/ask_seam/test_channel_reply_e2e.py` is where the shape itself is reconciled: it
#: drives the same reader over an `edit-committed` the real engine wrote on a real run, so
#: a wire change fails there rather than passing here.
NOTE_DISPOSITIONS = ("worker", "carried", "sideways", None)


@pytest.mark.reads_recipes
@pytest.mark.parametrize("reached", NOTE_DISPOSITIONS, ids=lambda row: str(row))
def test_the_reply_recipe_reports_this_notes_own_disposition_and_never_an_earlier_ones(
    tmp_path: Path, reached: str | None
) -> None:
    """The correlation, driven against a journal that already carries an earlier outcome.

    `onepipeline reply` answers `delivered` whatever the envelope carried, and what became
    of the note is written only to the run's journal — so the recipe reads it back. What
    it must never do is read it by recency: the journal here already holds an earlier
    note's outcome, and answering with that would tell a manager their note reached a
    dispatch when nothing had yet decided that it did.

    Every row sends the same envelope through the real recipe against the same journal,
    and only what is journalled for *this* note differs. The last row journals nothing for
    it, which is the case a recipe reading the newest outcome, the last, or the only one
    gets wrong.
    """
    checkout, trace = _checkout(tmp_path)
    (checkout / "bin/uv").write_text(JOURNALLING_UV)
    journal = checkout / "runs" / JOURNALLED_RUN / "events.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text(_committed_record(EARLIER_NOTE, EARLIER_REACHED) + "\n", encoding="utf-8")
    appended = tmp_path / "appended.jsonl"
    appended.write_text(
        "" if reached is None else _committed_record(THIS_NOTE, reached) + "\n",
        encoding="utf-8",
    )

    result = _run(
        checkout,
        trace,
        "channel-reply",
        JOURNALLED_RUN,
        stdin=json.dumps({"version": 2, "commands": [THIS_NOTE]}),
        env={
            "ONEPIPELINE_RUNS_DIR": str(checkout / "runs"),
            "JOURNAL_APPEND": str(appended),
            "JOURNAL_FILE": str(journal),
        },
    )

    assert result.returncode == 0, f"{result.stdout}{result.stderr}"
    answer = _verb_answer(result)
    assert answer["state"] == "applied", (
        f"the verb's own answer did not survive the merge:\n{result.stdout}"
    )
    assert answer[NOTES_FIELD] == [{"node": NOTED_NODE, "reached": reached}], (
        f"a note the engine journalled as {reached!r} was not answered that way against "
        f"its own node. A `reached` where this row journalled none is the EARLIER note's, "
        f"which is what reading this journal by recency, by its last entry, or by its only "
        f"entry does — and what a manager would then act on:\n{result.stdout}"
    )
    assert "channel-reply:" not in result.stderr, (
        f"the recipe printed a status line of its own beside the verb's answer:\n{result.stderr}"
    )


#: The second note an envelope carries, addressed to a node of its own so the two lines
#: the report joins are told apart by what they name rather than by their order.
OTHER_NODE = "worker"
OTHER_NOTE = {
    "op": "note",
    "id": OTHER_NODE,
    "addressee": "supervisor",
    "text": "the second note this reply carries",
}


@pytest.mark.reads_recipes
def test_the_reply_recipe_reports_every_note_one_envelope_carried(tmp_path: Path) -> None:
    """Two notes in one envelope read back as two, correlated one for one.

    A manager sends several notes in one reply, and each has its own fate: the engine
    commits them separately and journals an outcome per note. So the report has to carry
    both, matched to the note each belongs to rather than to the order they were
    journalled in — here the second note's outcome is written first, and the earlier
    reply's outcome sits above them both.
    """
    checkout, trace = _checkout(tmp_path)
    (checkout / "bin/uv").write_text(JOURNALLING_UV)
    journal = checkout / "runs" / JOURNALLED_RUN / "events.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text(_committed_record(EARLIER_NOTE, EARLIER_REACHED) + "\n", encoding="utf-8")
    appended = tmp_path / "appended.jsonl"
    # The second note's outcome first, so a reader that paired them by position would
    # report each under the other's node.
    appended.write_text(
        _committed_record(OTHER_NOTE, "supervisor")
        + "\n"
        + _committed_record(THIS_NOTE, "carried")
        + "\n",
        encoding="utf-8",
    )

    result = _run(
        checkout,
        trace,
        "channel-reply",
        JOURNALLED_RUN,
        stdin=json.dumps({"version": 2, "commands": [THIS_NOTE, OTHER_NOTE]}),
        env={
            "ONEPIPELINE_RUNS_DIR": str(checkout / "runs"),
            "JOURNAL_APPEND": str(appended),
            "JOURNAL_FILE": str(journal),
        },
    )

    assert result.returncode == 0, f"{result.stdout}{result.stderr}"
    assert _verb_answer(result)[NOTES_FIELD] == [
        {"node": NOTED_NODE, "reached": "carried"},
        {"node": OTHER_NODE, "reached": "supervisor"},
    ], (
        f"a two-note reply did not answer each note its own outcome against its own node, "
        f"in the order the envelope sent them. Pairing them by the order they were "
        f"journalled would report each under the other's node, and the earlier reply's "
        f"{EARLIER_REACHED!r} appearing at all is that outcome claimed by one of "
        f"them:\n{result.stdout}"
    )


#: A `uv` that accepts the reply and answers something that is not a JSON object, which
#: is the one shape the note outcomes cannot be merged into.
UNPARSEABLE_RECEIPT_UV = """#!/usr/bin/env bash
set -euo pipefail
printf 'uv %s\\n' "$*" >>"$TRACE_FILE"
if [ ! -t 0 ]; then cat >/dev/null; fi
printf '%s\\n' 'accepted'
"""


@pytest.mark.reads_recipes
def test_a_receipt_with_nowhere_to_carry_the_outcome_is_handed_back_alone(
    tmp_path: Path,
) -> None:
    """Success is one line or none, including where there is nothing to merge into.

    The outcomes ride inside the verb's own answer, so an answer that is not a JSON object
    has nowhere to carry them. What must not happen is the recipe making up the difference
    with a second line of its own: the verb keeps its answer whole, the recipe adds
    nothing, and the reply still succeeds.
    """
    checkout, trace = _checkout(tmp_path)
    (checkout / "bin/uv").write_text(UNPARSEABLE_RECEIPT_UV)
    journal = checkout / "runs" / JOURNALLED_RUN / "events.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text(_committed_record(EARLIER_NOTE, EARLIER_REACHED) + "\n", encoding="utf-8")

    result = _run(
        checkout,
        trace,
        "channel-reply",
        JOURNALLED_RUN,
        stdin=json.dumps({"version": 2, "commands": [THIS_NOTE]}),
        env={"ONEPIPELINE_RUNS_DIR": str(checkout / "runs")},
    )

    assert result.returncode == 0, (
        f"an answer this could not merge into failed the reply, which was already sent:"
        f"\n{result.stdout}{result.stderr}"
    )
    assert result.stdout.splitlines() == ["accepted"], (
        f"the verb's own answer did not reach the caller whole:\n{result.stdout}"
    )
    assert "channel-reply:" not in result.stderr, (
        f"the recipe added a line of its own beside an answer it could not merge into, so "
        f"success is two lines where it is one or none:\n{result.stderr}"
    )


#: A `uv` that accepts the reply and then takes the journal away, which is the one thing
#: that can happen between the offset being taken and the outcome being read.
UNREADABLE_JOURNAL_UV = """#!/usr/bin/env bash
set -euo pipefail
printf 'uv %s\\n' "$*" >>"$TRACE_FILE"
if [ ! -t 0 ]; then cat >/dev/null; fi
chmod 000 -- "$JOURNAL_FILE"
printf '%s\\n' '{"reply":0,"state":"applied"}'
"""


@pytest.mark.reads_recipes
def test_a_journal_the_recipe_cannot_read_is_said_rather_than_read_as_no_outcome(
    tmp_path: Path,
) -> None:
    """A broken read and an undecided note are opposite states, and must not share a word.

    The reply is accepted and the journal is then unreadable, so the recipe has nothing
    to correlate against. Reporting that as "no outcome recorded yet" would tell a manager
    the engine had not decided the note's fate when the truth is that nobody looked — the
    same silence this whole report exists to end, one layer further in.
    """
    checkout, trace = _checkout(tmp_path)
    (checkout / "bin/uv").write_text(UNREADABLE_JOURNAL_UV)
    journal = checkout / "runs" / JOURNALLED_RUN / "events.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text(_committed_record(EARLIER_NOTE, EARLIER_REACHED) + "\n", encoding="utf-8")

    try:
        result = _run(
            checkout,
            trace,
            "channel-reply",
            JOURNALLED_RUN,
            stdin=json.dumps({"version": 2, "commands": [THIS_NOTE]}),
            env={
                "ONEPIPELINE_RUNS_DIR": str(checkout / "runs"),
                "JOURNAL_FILE": str(journal),
            },
        )
    finally:
        # Restored so the temporary tree can be cleaned up by whoever owns it.
        journal.chmod(0o644)

    assert result.returncode == 0, (
        f"an unreadable journal failed the reply itself, which was already sent:"
        f"\n{result.stdout}{result.stderr}"
    )
    answer = _verb_answer(result)
    assert "could not be read" in str(answer.get(NOTES_UNREAD_FIELD)), (
        f"the answer does not say the journal was unreadable, so a note nobody could look "
        f"up is indistinguishable from one whose fate nothing had decided — which are "
        f"opposite states:\n{result.stdout}"
    )
    assert answer[NOTES_FIELD] == [{"node": NOTED_NODE, "reached": None}], (
        f"the note this reply sent was not answered, or was answered with the earlier "
        f"reply's {EARLIER_REACHED!r}:\n{result.stdout}"
    )


#: A `python3` that refuses the outcome read and answers every other call, so the
#: envelope is judged and sent exactly as it would be and only the read back fails. It
#: stands on PATH in a checkout with no pinned interpreter beside it, which is what a
#: half-restored checkout is.
#:
#: Keyed on the invocation that fails rather than on the ones that must not, because the
#: recipe runs a helper per question it asks of an envelope and the set of them grows: it
#: judges the amendments, judges the rendezvous, and only then reads each note's fate
#: back. A stand-in that named the passing shapes would start refusing a new one the day
#: a question was added, and this journey would fail about that instead of about its own
#: subject. The outcome read is the one taking the journal, the offset and the verb's own
#: answer beside the program — four arguments after `-c`, where no other call here has
#: more than two.
HALF_BROKEN_INTERPRETER = """#!/usr/bin/env bash
if [ "$#" -eq 5 ]; then exit 4; fi
exit 0
"""


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] This suite predates
# this change and is already behind a narrow edge — `recipeWorkspace`, which names the
# recipes and scripts these journeys drive and nothing else of this repository — so an
# unrelated orchestrator change does not pay for it. Moving the whole file into an Nx
# project of its own is a workspace-graph change with its own key, its own conftest
# routing and its own reason to exist; this change edits one stand-in constant inside it
# and is not where that belongs.


@pytest.mark.reads_recipes
def test_an_outcome_read_that_could_not_run_is_named_rather_than_passed_off_as_none(
    tmp_path: Path,
) -> None:
    """A read that never happened must not look like a note with nothing to report.

    The reply is sent and accepted, and the helper that reads each note's fate back then
    cannot run at all. Swallowing that hands the manager the bare transport receipt —
    which is byte for byte what a reply carrying no note at all answers with — so the one
    thing they would conclude is that there was no outcome to report. That is this
    recipe's own defect worn one layer in, and the whole reason it reads the journal.

    So it is named, with where the outcome can still be read. Not refused: the envelope
    is already on the channel, so the verb's own answer and exit status stay the
    caller's, and stdout carries that answer and nothing else.
    """
    checkout, trace = _checkout(tmp_path)
    (checkout / "bin/uv").write_text(JOURNALLING_UV)
    # llmlint: ignore[e2e_not_mocked] The recipe is real; a half-broken interpreter is the input.
    broken = checkout / "bin" / "python3"
    broken.write_text(HALF_BROKEN_INTERPRETER)
    broken.chmod(0o755)
    journal = checkout / "runs" / JOURNALLED_RUN / "events.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text(_committed_record(EARLIER_NOTE, EARLIER_REACHED) + "\n", encoding="utf-8")

    result = _run(
        checkout,
        trace,
        "channel-reply",
        JOURNALLED_RUN,
        stdin=json.dumps({"version": 2, "commands": [THIS_NOTE]}),
        env={"ONEPIPELINE_RUNS_DIR": str(checkout / "runs")},
    )

    assert result.returncode == 0, (
        f"a failed outcome read failed the reply itself, which was already sent:"
        f"\n{result.stdout}{result.stderr}"
    )
    assert result.stdout.strip().splitlines() == ['{"reply":0,"state":"applied"}'], (
        f"the verb's own answer did not reach the caller whole and alone:\n{result.stdout}"
    )
    assert "could not be read back" in result.stderr, (
        f"the recipe swallowed a failed outcome read, so the manager holds a receipt with "
        f"no note outcome in it and nothing saying why — which is what a reply carrying no "
        f"note at all answers with:\n{result.stderr}"
    )
    assert "the reply itself was sent" in result.stderr, (
        f"the diagnostic does not say the envelope reached the channel, so a manager "
        f"reading it cannot tell whether to send it again:\n{result.stderr}"
    )
    assert f"just monitor {JOURNALLED_RUN}" in result.stderr, (
        f"the diagnostic names no way to read the outcome that was recorded anyway, so "
        f"the manager is told their read failed and nothing else:\n{result.stderr}"
    )


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
