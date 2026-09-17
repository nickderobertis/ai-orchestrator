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
#: The key `ask-seam:test` is memoized on: what the host-tool journeys over the seam a
#: dispatched agent asks its manager through drive and read. Narrower than the whole
#: workspace by the prose those journeys never open, and by the e2e helpers they never
#: import — named one by one rather than as a directory, so a journey added beside them
#: does not silently start invalidating this tier.
ASK_SEAM_WORKSPACE = "askSeamWorkspace"
#: The key `session-setup:test` is memoized on: what the journey over this checkout's
#: own provisioning drives and reads. Named file by file rather than as `scripts/**/*`
#: or `config/**/*`: the journey runs the real `scripts/session-setup.sh`, which
#: re-provisions the project environment from the lock, so every glob wider than the
#: script, the lock and the one pin it reads makes an unrelated edit pay for that.
SESSION_SETUP_WORKSPACE = "sessionSetupWorkspace"

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
#: The key `dag-ui:test` is memoized on: what the journeys over `just dag-ui` and
#: `just telemetry-server` drive and read — those two recipes and the scripts they
#: reach, the address both resolve each other through, the pins that decide which
#: published bundle and reader are installed, and the recorded runs they render.
#: Named file by file rather than by directory, and deliberately not `config/**/*`,
#: `scripts/**/*` or `orchestrator/**/*`: these journeys start two real servers per
#: test, so every glob wider than what they actually open makes an unrelated edit pay
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

#: The project whose test target owns the host-tool journeys over the ask seam — the
#: real `scripts/ask-manager.sh`, the real `onemessagebus ask` it hands the question to,
#: and the real launches that decide what a dispatch is given to ask with. A project of
#: its own for the reason `plan-tooling` is one: every journey here spends a real launch,
#: which is a cost `nx affected` can only keep off an unrelated edit where it is a
#: separate project. It declares no Python distribution either, for the same reason.
ASK_SEAM_PROJECT = "ask-seam"
#: That project's one test target. One rather than two, deliberately: nothing here reads
#: this repository's prose, and an empty tier is a partition the suite gate cannot check.
ASK_SEAM_SCOPED = "test"
#: The directory it owns, which every other project's tiers ignore. Path-selected, as
#: `PLAN_TOOLING_ROOT` is: a file added here joins this project by being here.
ASK_SEAM_ROOT = "tests/ask_seam"

#: The project whose test target owns the journeys over this repository's composition of
#: the DAG Observatory — the two recipes, the proxy behind them, and what the published
#: reader answers. A project of its own for the reason `plan-tooling` and `ask-seam` are:
#: two real servers per test is a cost `nx affected` can only keep off an unrelated edit
#: where it is a separate project, and while these journeys sat in the orchestrator
#: project every change in the repository paid it. It declares no Python distribution
#: either. Asserting that the *bundle* renders is not here and must not come back: this
#: tier is inside the merge path, and nothing in this repository provisions a browser.
DAG_UI_PROJECT = "dag-ui"
#: That project's one test target. One rather than two, as `ask-seam` has one: nothing
#: here reads this repository's prose.
DAG_UI_SCOPED = "test"
#: The directory it owns, which every other project's tiers ignore. Path-selected, as
#: `PLAN_TOOLING_ROOT` is: a file added here joins this project by being here.
DAG_UI_ROOT = "tests/dag_ui"

#: The project whose test target owns the journeys over `onepipeline unwatched` and the
#: `Stop` hook that reads it — the real hook script, the real recipe, the installed engine
#: they ask, and the launch shapes whose runs it has to name. A project of its own for the
#: reason `plan-tooling`, `ask-seam` and `dag-ui` are: a watch armed for real, a process
#: killed and left unreaped, this checkout's own project-environment lock held, and three
#: real launches are a cost `nx affected` can only keep off an unrelated edit where it is a
#: separate project. It declares no Python distribution either, for the same reason.
UNWATCHED_PROJECT = "unwatched"
#: That project's one test target. One rather than two, as `ask-seam` has one: nothing here
#: reads this repository's prose.
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
