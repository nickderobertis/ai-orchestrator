"""The tiers Nx memoizes and the file sets it hashes, from one declaration each.

A cache key is a claim, and two places state it: `nx.json` declares the globs Nx
hashes, and this suite enforces that the tests keyed on them read nothing else.
Restating the globs on the enforcing side would let the claim and the key drift
apart silently — a guard permitting a read the key does not cover is exactly the
false green the keys exist to prevent — so both sides resolve them from here.

The tier names live here for the same reason. A tier is named by
`orchestrator/project.json`, by the recipes that run it, by the guards that hold
it to its key, and by the journeys that prove that key against real Nx; a rename
that missed one of those would leave a guard checking a target nobody runs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import NamedTuple

from orchestrator.root import REPO_ROOT

#: The key `orchestrator:test` is memoized on: the workspace minus its prose.
CODE_WORKSPACE = "codeWorkspace"
#: The key `orchestrator:test-recipes` is memoized on: what the recipe journeys
#: drive, plus the modules that define and collect them.
RECIPE_WORKSPACE = "recipeWorkspace"
#: The key `plan-tooling:test` is memoized on: what the host-tool journeys over this
#: repository's plan surface drive and read. Narrower than the whole workspace by the
#: prose those journeys never open, which is what makes editing `docs/` free of them.
PLAN_TOOLING_WORKSPACE = "planToolingWorkspace"
#: The key `session-setup:test` is memoized on: what the journey over this checkout's
#: own provisioning drives and reads. Named file by file rather than as `scripts/**/*`
#: or `config/**/*`: the journey runs the real `scripts/session-setup.sh`, which
#: re-provisions the project environment from the lock, so every glob wider than the
#: script, the lock and the one pin it reads makes an unrelated edit pay for that. The
#: last step that script runs — `just repos-bootstrap`, over the tracked checkout list —
#: is in the key as its script, the list reader it sources and the list itself, because
#: what the journey asserts about the siblings is decided by those three files.
SESSION_SETUP_WORKSPACE = "sessionSetupWorkspace"
#: The key `project-store-race:test` is memoized on: the module under race, the
#: project's own files, and the suite modules `tests/conftest.py` imports — nothing
#: else, because the race reads a temporary root and nothing of this checkout.
PROJECT_STORE_RACE_WORKSPACE = "projectStoreRaceWorkspace"
#: The key `unpublished-view:test` is memoized on. Files rather than trees, for the reason
#: `unwatchedWorkspace` names, and measured the same way: the tier traced under `strace -f
#: -e trace=openat,execve`, each opened path normalized and intersected with what git
#: tracks. So the key is the justfile, the two scripts the recipe reaches and the module
#: they run, the lock that decides which `onevcs` answers, the registry helper the seeding
#: runs, and the suite modules `tests/conftest.py` imports with the two `config/` files and
#: two scripts they read — never `scripts/**`, because an edit to a script this view never
#: reaches must not pay for its real sessions.
UNPUBLISHED_VIEW_WORKSPACE = "unpublishedViewWorkspace"

#: The key the `unwatched` project's one tier is memoized on, and it names files rather
#: than trees because these journeys arm real
#: watches, kill real processes, hold this checkout's project-environment lock and spend
#: three real launches, so every path in the key that they never read makes an unrelated
#: edit pay for all of that. What they read was measured rather than guessed — the whole
#: tier traced under `strace -f -e trace=openat,execve`, its opened paths intersected
#: with what git tracks — and the key is that set: the hook and the recipe it is the
#: consuming half of, every pin in `config/` (which engine answers, and what session
#: setup would install), the launch machinery a `just orchestrate` reaches through
#: `scripts/onepipeline.sh`, the two graphs and two harness configs the engine reads at
#: launch, and the modules the tests import. The conftest guard holds the in-process
#: half of that to this key; the subprocess half is the trace, so a launch path that
#: starts reading a new file is a re-take of it rather than a glob to widen.
UNWATCHED_WORKSPACE = "unwatchedWorkspace"
#: The key the `merge-policy` project's one tier is memoized on. Files rather than trees,
#: for the reason `unwatchedWorkspace` names: every journey there spends a real launch,
#: so every path in the key they never read makes an unrelated edit pay for it. Measured
#: the same way — the tier traced under `strace -f -e trace=openat,execve`, its opened
#: paths intersected with what git tracks — so the key is the launch machinery a refused
#: `just orchestrate` reaches through `scripts/onepipeline.sh`, every pin in `config/`,
#: the modules the tests import, and the three documents whose restated vocabulary the
#: journeys hold to the launcher: a prose-only diff of one of those selects this project,
#: because its verdict is about that prose.
MERGE_POLICY_WORKSPACE = "mergePolicyWorkspace"
#: The key the `writeback-budget` project's one tier is memoized on. Files rather than
#: trees, for the reason `unwatchedWorkspace` names: its journey spends a real launch and
#: holds two store copies past a minute each, so every path in the key it never reads
#: makes an unrelated edit pay for several minutes. Measured the same way — the tier
#: traced under `strace -f -e trace=openat,execve`, its opened paths intersected with what
#: git tracks — so the key is the launch machinery a `just orchestrate` reaches through
#: `scripts/onepipeline.sh`, every pin in `config/` (which engine answers is the verdict),
#: the graphs and harness configs the engine reads at launch, the plan-store stand-in the
#: engine spawns, and the modules the test imports.
WRITEBACK_BUDGET_WORKSPACE = "writebackBudgetWorkspace"
#: The key the `run-end-hooks` project's one tier is memoized on. Files rather than trees,
#: for the reason `unwatchedWorkspace` names, and measured the same way: the tier traced
#: under `strace -f -e trace=openat,execve`, each opened path normalized, a relative one
#: resolved against the checkout and a bytecode file mapped to its source, then intersected
#: with what git tracks. So the key is the launch machinery `just orchestrate` reaches, the
#: hook and the `just follow-ups` chain it runs, every pin in `config/`, the graphs, harness
#: configs, persona and task template the two launches open, the six package modules they
#: import, and the modules the test imports. Two entries the trace cannot show stay:
#: `scripts/ask-manager.sh`, which a launch only tests with `-x` and refuses without, and
#: the test modules and `tests/conftest.py`, which pytest opens as rewritten bytecode.
RUN_END_HOOKS_WORKSPACE = "runEndHooksWorkspace"
#: The key the `host-views` project's one tier is memoized on. Files rather than trees,
#: for the reason `unwatchedWorkspace` names: its real-launch control spends a whole
#: `just orchestrate`, so every path in the key nothing here reads makes an unrelated
#: edit pay for that launch. Measured rather than guessed — the tier traced under
#: `strace -f -e trace=openat,execve`, each opened path resolved against this checkout
#: and a bytecode file mapped to its source, then intersected with what git tracks — so
#: the key is the two recipes and the launch machinery they reach through
#: `scripts/onepipeline.sh`, every pin in `config/`, the graphs, personas and harness
#: configs the engine opens at launch, the example records the control copies and
#: launches from, and the modules the test imports. A launch path that starts reading a
#: new file is a re-take of that measurement rather than a glob to widen.
HOST_VIEWS_WORKSPACE = "hostViewsWorkspace"
#: The key `dag-ui:test` is memoized on: what the journeys over `just dag-ui` and
#: `just telemetry-server` drive and read — those two recipes, the one script they both
#: reach and the acting-session ladder it sources, the address that script binds from,
#: the pin that decides which reader and which built-in bundle are installed, and the
#: recorded runs they render.
#: Named file by file rather than by directory, and deliberately not `config/**/*`,
#: `scripts/**/*` or `orchestrator/**/*`: these journeys start a real server per test,
#: so every glob wider than what they actually open makes an unrelated edit pay
#: for that. `orchestrator/root.py` is deliberately *not* in it, though the
#: module imports `REPO_ROOT` from there: it is a path helper nearly everything imports,
#: so covering it would start those servers for edits that cannot change what these
#: journeys observe, and `codeWorkspace` already reruns the rest of the suite over it.
#: Its own prose is excluded, as
#: `codeWorkspace` excludes the workspace's: nothing here reads this project's
#: `AGENTS.md`, so an instruction-only edit must not start them.
DAG_UI_WORKSPACE = "dagUiWorkspace"
#: The key `workspace:check-nx-cache` is memoized on: what that script reads.
NX_CACHE_CHECK = "nxCacheCheck"
#: The tier that runs the bulk of the Python suite across xdist workers.
CODE_SCOPED = "test"
#: The tier that runs the tests asserting on this repository's own prose.
DOCS_SCOPED = "test-docs"
#: The tier that drives this repository's `just` recipes and shell scripts.
RECIPE_SCOPED = "test-recipes"
#: The uncached tier that reconciles this repository's configuration against what
#: lives outside the workspace: the registered checkouts of the repositories it routes
#: — a rule's gate and a repo-specific persona's review bar are both held to the
#: recipes those repositories define — and the adopted releases' own source, which the
#: names this repository restates from the engines and the bus are held to.
#: Deliberately unmemoized: no `nx.json` key could cover either, so any memo would be a
#: verdict on whatever they looked like when it was recorded.
CHECKOUT_SCOPED = "test-checkouts"
#: The project whose test target owns the host-tool journeys over the plan surface —
#: `just check-plan`, `just review-plan`, the registered check script, the installed
#: engine and a real `oneharness run`. A project rather than a marker tier of the
#: orchestrator project, because what those journeys cost and what answers them are both
#: different from the Python suite beside them, and `nx affected` can only tell the two
#: apart where they are two projects. It declares **no Python distribution**: there is
#: one `pyproject.toml` and one `uv.lock` in this repository and this project runs that
#: workspace's own pytest over a directory, so the Nx project graph gains a target
#: rather than the Python workspace gaining a member.
PLAN_TOOLING_PROJECT = "plan-tooling"
#: That project's own test target, keyed on the narrow set its journeys read.
PLAN_TOOLING_SCOPED = "test"
#: That project's second target: the journeys that build a **copy** of this checkout,
#: keyed on the whole workspace because copying the tracked tree is reading all of it.
#: A target of the project that owns them rather than a marker handing them to another
#: project's tier — the two costs are two keys, and a tier whose tests live in one
#: project while another project's target collects them is a cost `nx affected` cannot
#: attribute to the code that moved it.
PLAN_TOOLING_DOCS_SCOPED = "test-docs"
#: The directory that project owns, which every other project's tiers ignore.
#: Path-selected rather than marker-selected: the project boundary is what routes these,
#: so a file added here joins this project by being here, and which of the project's two
#: targets runs it is the only thing its markers decide.
PLAN_TOOLING_ROOT = "tests/plan_tooling"

#: The directory every host-tool journey over the ask seam lives under — the real
#: `scripts/ask-manager.sh`, the installed engine's `onepipeline ask` it execs, and
#: the real launches that decide what a dispatch is given to ask with — and which every
#: other project's tiers ignore. Not a project itself: each journey below is one, in a
#: directory of its own under this one, because each spends a real launch and a key two
#: journeys shared made every edit one of them read pay for the other's launch too.
ASK_SEAM_ROOT = "tests/ask_seam"
#: Each journey project's one test target. One rather than two, deliberately: nothing
#: here reads this repository's prose, and an empty tier is a partition the suite gate
#: cannot check.
ASK_SEAM_SCOPED = "test"


class AskSeamJourney(NamedTuple):
    """One host-tool journey over the ask seam, as the guards and the graph name it."""

    #: The Nx project, which is how `nx affected` charges the launch to what selects it.
    project: str
    #: The directory it owns: its module and its `project.json`, and nothing else.
    root: str
    #: The `nx.json` named input its one target is memoized on.
    key: str


# llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This module is the domain
# that owns tier names: the Nx project a journey is, its directory and its key are read by
# `tests/conftest.py`'s read guard, `tests/test_nx_cache_scope.py` and the real-Nx
# selection journeys, and resolving them from one row each is what keeps a renamed journey
# from leaving a guard checking a project nobody runs, as the module docstring says.
#: One memoization unit per journey, in what each is keyed on. Every key names files
#: rather than trees — `tests/test_nx_cache_scope.py` refuses a `config/**/*` or a
#: `scripts/**/*` in one — because a journey here spends a real launch, and a path in its
#: key it never reads makes an unrelated edit pay for that launch. Each was measured
#: rather than guessed: the module run under the target's own command with every open of
#: a file under this checkout recorded through inotify, bytecode mapped to its source,
#: the set intersected with what git tracks; the journey's own module and every suite
#: module it imports are then required by the gate, and `tests/conftest.py` holds the
#: in-process half to the key on every run. The subprocess half — the pins, graphs and
#: harness configs a launch opens through the engine — is the measurement, so a launch
#: path that starts reading a new file is a re-take of it rather than a glob to widen.
ASK_SEAM_JOURNEYS: tuple[AskSeamJourney, ...] = (
    AskSeamJourney("ask-seam-ask-manager", f"{ASK_SEAM_ROOT}/ask_manager", "askSeamAskManager"),
    AskSeamJourney(
        "ask-seam-bus-resolution", f"{ASK_SEAM_ROOT}/bus_resolution", "askSeamBusResolution"
    ),
    AskSeamJourney(
        "ask-seam-channel-reply", f"{ASK_SEAM_ROOT}/channel_reply", "askSeamChannelReply"
    ),
    AskSeamJourney(
        "ask-seam-follow-up-drafts-launch",
        f"{ASK_SEAM_ROOT}/follow_up_drafts_launch",
        "askSeamFollowUpDraftsLaunch",
    ),
    AskSeamJourney("ask-seam-launch", f"{ASK_SEAM_ROOT}/launch", "askSeamLaunch"),
    AskSeamJourney(
        "ask-seam-planner-fallback-ask",
        f"{ASK_SEAM_ROOT}/planner_fallback_ask",
        "askSeamPlannerFallbackAsk",
    ),
    AskSeamJourney(
        "ask-seam-structural-reply", f"{ASK_SEAM_ROOT}/structural_reply", "askSeamStructuralReply"
    ),
)
# llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]

#: The project whose test target owns the journeys over this repository's composition of
#: the DAG Observatory — the two recipes, the proxy behind them, and what the published
#: reader answers. A project of its own for the reason `plan-tooling` and each ask-seam
#: journey are:
#: two real servers per test is a cost `nx affected` can only keep off an unrelated edit
#: where it is a separate project, and while these journeys sat in the orchestrator
#: project every change in the repository paid it. It declares no Python distribution
#: either. Asserting that the *bundle* renders is not here and must not come back: this
#: tier is inside the merge path, and nothing in this repository provisions a browser.
DAG_UI_PROJECT = "dag-ui"
#: That project's one test target. One rather than two, as each ask-seam journey has one:
#: nothing here reads this repository's prose.
DAG_UI_SCOPED = "test"
#: The directory it owns, which every other project's tiers ignore. Path-selected, as
#: `PLAN_TOOLING_ROOT` is: a file added here joins this project by being here.
DAG_UI_ROOT = "tests/dag_ui"

#: The project whose test target owns the journeys over `onepipeline unwatched` and the
#: `Stop` hook that reads it — the real hook script, the real recipe, the installed engine
#: they ask, and the launch shapes whose runs it has to name. A project of its own for the
#: reason `plan-tooling`, the ask-seam journeys and `dag-ui` are: a watch armed for real, a
#: process killed and left unreaped, this checkout's own project-environment lock held, and
#: three real launches are a cost `nx affected` can only keep off an unrelated edit where
#: it is a separate project. It declares no Python distribution either, for the same reason.
UNWATCHED_PROJECT = "unwatched"
#: That project's one test target. One rather than two, as each ask-seam journey has one:
#: nothing here reads this repository's prose.
UNWATCHED_SCOPED = "test"
#: The directory it owns, which every other project's tiers ignore. Path-selected, as
#: `PLAN_TOOLING_ROOT` is: a file added here joins this project by being here.
UNWATCHED_ROOT = "tests/unwatched"

#: The project whose test target owns the journeys that hold the `merge_policy` vocabulary
#: this repository's prose restates to the one the installed launcher accepts — a real
#: `just orchestrate` per policy, read for its refusal. A project of its own for the
#: reason `unwatched` is: each journey spends a real launch, a cost `nx affected` can
#: only keep off an unrelated edit where it is a separate project.
MERGE_POLICY_PROJECT = "merge-policy"
#: That project's one test target. One rather than two: what its journeys read of this
#: repository's prose is three named documents, carried in its key by name.
MERGE_POLICY_SCOPED = "test"
#: The directory it owns, which every other project's tiers ignore. Path-selected, as
#: `PLAN_TOOLING_ROOT` is: a file added here joins this project by being here.
MERGE_POLICY_ROOT = "tests/merge_policy"

#: The project whose test target owns the journeys that hold the adopted engine's
#: settlement write-back to what it does on a real launch — a copy held past the
#: sixty-second floor and past the deadline its items earn, and a projection carrying only
#: the nodes that changed and waiting for the graph after a refusal. A project of its own for
#: the reason `unwatched` is: a real launch and minutes of held copies are a cost
#: `nx affected` can only keep off an unrelated edit where it is a separate project.
WRITEBACK_BUDGET_PROJECT = "writeback-budget"
#: That project's one test target. One rather than two, as `unwatched` has one: nothing
#: here reads this repository's prose.
WRITEBACK_BUDGET_SCOPED = "test"
#: The directory it owns, which every other project's tiers ignore. Path-selected, as
#: `PLAN_TOOLING_ROOT` is: a file added here joins this project by being here.
WRITEBACK_BUDGET_ROOT = "tests/writeback_budget"

#: The project whose test target owns the journey that fires this host's run-end hooks
#: through `just orchestrate` on the installed engine: a completed run launching its
#: follow-up run, a failed one launching nothing, and each beside a hookless twin. A project
#: of its own for the reason `unwatched` is: several real launches and a follow-up run
#: waited out are a cost `nx affected` can only keep off an unrelated edit here.
RUN_END_HOOKS_PROJECT = "run-end-hooks"
#: That project's one test target: nothing here reads this repository's prose.
RUN_END_HOOKS_SCOPED = "test"
#: The directory it owns, which every other project's tiers ignore.
RUN_END_HOOKS_ROOT = "tests/run_end_hooks"

#: The project whose test target owns the journey over this checkout's own session
#: setup: the real `scripts/session-setup.sh`, verifying the plan-store CLI the lock
#: installs and creating the plan root. A project of its own for the reason `unwatched`
#: is: a real re-provisioning of the project environment is a cost `nx affected` can
#: only keep off an unrelated edit where it is a separate project.
SESSION_SETUP_PROJECT = "session-setup"
#: That project's one test target: nothing here reads this repository's prose.
SESSION_SETUP_SCOPED = "test"
#: The directory it owns, which every other project's tiers ignore.
SESSION_SETUP_ROOT = "tests/session_setup"

#: The project whose test target owns the journeys over `just status` and `just host` —
#: the two views a supervisor reads a run and this host through, driven as real recipes
#: over the installed engine, one of them over a run root a real launch wrote. A project
#: of its own for the reason `unwatched` is: driving this repository's recipes and the
#: engine's command surface for real, a launch among them, is a cost `nx affected` can
#: only keep off an unrelated edit where it is a separate project. While these sat in the
#: orchestrator project, `reads_recipes` and `reads_docs` split them across two of that
#: project's targets and every prose edit in the repository paid for the launch.
HOST_VIEWS_PROJECT = "host-views"
#: That project's one test target. One rather than two: the only prose its key carries is
#: the example records the launch copies, which are data the journey reads rather than
#: this repository's prose about itself.
HOST_VIEWS_SCOPED = "test"
#: The directory it owns, which every other project's tiers ignore. Path-selected, as
#: `PLAN_TOOLING_ROOT` is: a file added here joins this project by being here.
HOST_VIEWS_ROOT = "tests/host_views"

#: The project whose test target owns the two-process replacement race over
#: `orchestrator/project_store.py`: writer processes replacing one project's records
#: for a fixed span of the clock while a reader process reads the root back through
#: the module's own reader. A project of its own for the reason `unwatched` is, at a
#: smaller scale: a race bounded by the clock costs the same on every host and every
#: edit, so keeping it behind its own edge is what lets `nx affected` charge it to a
#: change of the store rather than to every change of `orchestrator/`.
PROJECT_STORE_RACE_PROJECT = "project-store-race"
#: That project's one test target: nothing here reads this repository's prose.
PROJECT_STORE_RACE_SCOPED = "test"
#: The directory it owns, which every other project's tiers ignore.
PROJECT_STORE_RACE_ROOT = "tests/project_store_race"

#: The project whose test target owns the journey over `just unpublished`: the real
#: recipe, wrapper and module over a scratch `onevcs` registry holding a branch in every
#: state the view distinguishes. A project of its own for the reason `unwatched` is: real
#: `onevcs` sessions, real `git` and a real `just` per assertion are a cost `nx affected`
#: can only keep off an unrelated edit where it is a separate project.
UNPUBLISHED_VIEW_PROJECT = "unpublished-view"
#: That project's one test target: nothing here reads this repository's prose.
UNPUBLISHED_VIEW_SCOPED = "test"
#: The directory it owns, which every other project's tiers ignore.
UNPUBLISHED_VIEW_ROOT = "tests/e2e/unpublished_view"

#: The uncached tier that reads the measuring tier's coverage data and enforces the
#: declared floor against it. Deliberately unmemoized:
#: it is seconds of work, and a floor that always runs is one no replay can skip.
COVERAGE_SCOPED = "coverage"

#: The deterministic tier's targets `just check` lets the diff select, in the order the
#: recipe names them. Every one is memoized, which is what makes skipping an unselected
#: project equivalent to replaying it; `tests/test_nx_cache_scope.py` refuses one that
#: is not.
SELECTED_TARGETS = (
    "format-check",
    "lint",
    "typecheck",
    CODE_SCOPED,
    DOCS_SCOPED,
    RECIPE_SCOPED,
)
#: The deterministic tier's targets no diff can select, so `just check` runs them over
#: every project every time: their subjects are what lives outside this workspace, and
#: what the tiers beside them just measured. Both are uncached for those same reasons,
#: so the two lists partition along the line the memo argument draws.
UNCONDITIONAL_TARGETS = (CHECKOUT_SCOPED, COVERAGE_SCOPED)


def nx_config() -> dict:
    return json.loads((REPO_ROOT / "nx.json").read_text(encoding="utf-8"))


def resolve_input_globs(entries: list, named: dict[str, list]) -> list[str]:
    """Expand a target's declared inputs into the file globs Nx will hash.

    Named inputs may reference other named inputs, and non-file entries (an `env`
    or `runtime` input) contribute no file coverage at all, so they are dropped
    rather than treated as paths. The globs come back in Nx's own spelling —
    `{workspaceRoot}`, `{projectRoot}`, and a leading `!` — because which of those a
    glob wears is what decides whether a change to it selects the project.
    """
    globs: list[str] = []
    for entry in entries:
        match entry:
            case dict():
                pass  # an env or runtime input contributes no file coverage
            case _ if entry in named:
                globs.extend(resolve_input_globs(named[entry], named))
            case _:
                globs.append(entry)
    return globs


def repository_relative_globs(globs: list[str], *, project_root: str = "") -> list[str]:
    """Rewrite Nx's own glob spelling into paths relative to the repository root."""
    return [
        glob.replace("{workspaceRoot}/", "").replace("{projectRoot}/", f"{project_root}/")
        for glob in globs
    ]


def named_input_globs(name: str, *, project_root: str = "") -> list[str]:
    """Expand one named input into repository-relative globs."""
    named = nx_config()["namedInputs"]
    return repository_relative_globs(
        resolve_input_globs(named[name], named), project_root=project_root
    )


def matches(glob: str, relative: str) -> bool:
    pattern = re.escape(glob).replace(r"\*\*/\*", ".*").replace(r"\*", "[^/]*")
    return re.fullmatch(pattern, relative) is not None


def covers(globs: list[str], relative: str) -> bool:
    """Apply Nx's file-set semantics: a later `!` glob removes what an earlier one added."""
    included = any(matches(glob, relative) for glob in globs if not glob.startswith("!"))
    excluded = any(matches(glob[1:], relative) for glob in globs if glob.startswith("!"))
    return included and not excluded


def repository_relative(named: str | bytes | Path) -> str | None:
    """Return the repository-relative path ``named`` refers to, or ``None``.

    Only this checkout's own content counts. A throwaway copy of the tree — which
    is what every workspace journey builds — lives outside `REPO_ROOT` and is not
    the tree any cache key here describes.
    """
    if isinstance(named, bytes):
        named = named.decode("utf-8", "replace")
    try:
        return str(Path(named).resolve().relative_to(REPO_ROOT))
    except (OSError, ValueError):
        return None
