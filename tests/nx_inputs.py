"""The tiers Nx memoizes and the file sets it hashes, from one declaration each.

A cache key is a claim, and two places state it: the Nx configuration declares the
globs Nx hashes, and this suite enforces that the tests keyed on them read nothing
else. Restating the globs on the enforcing side would let the claim and the key drift
apart silently — a guard permitting a read the key does not cover is exactly the
false green the keys exist to prevent — so both sides resolve them from here.

A tier's key is resolved through the project graph, as Nx resolves it. Each test
project declares what it reads itself under its own named input in its `project.json`,
and every shared test-support module under `tests/` is a project of its own under
`tests/support/` whose `testSupport` input names that module and the data files it
opens. A tier takes those through its `implicitDependencies` — Nx hashes a
`{"input": "testSupport", "dependencies": true}` input across every transitive
dependency — so a helper enters a key by an edge rather than by a path copied into
each tier's list, and an edit to it invalidates exactly the tiers whose modules reach
it. `nx.json` keeps only what is shared: the whole-workspace keys and the empty
`testSupport` a project that is not a unit contributes.

The tier names live here for the same reason. A tier is named by
`orchestrator/project.json`, by the recipes that run it, and by the guards that
hold it to its key; a rename that missed one of those would leave a guard checking a
target nobody runs.
"""

from __future__ import annotations

import functools
import json
import re
import subprocess
from pathlib import Path
from typing import NamedTuple

from orchestrator.root import REPO_ROOT

#: The workspace minus its prose, which both code tiers narrow from.
CODE_WORKSPACE = "codeWorkspace"
#: The key `orchestrator:test` is memoized on: `codeWorkspace` without the journey modules
#: the projects in `E2E_JOURNEYS` own, so editing one of them replays the unit and
#: drift-gate verdict. The journeys that tier's own gates read by path come back through
#: `JOURNEYS_THE_UNIT_GATES_READ`, taken from the projects that own them rather than
#: restated here.
UNIT_WORKSPACE = "unitWorkspace"
#: The named input, declared by each journey project whose modules `orchestrator:test`'s
#: drift gates open by path — the ones holding a value to the journey that exercises it —
#: listing those modules, which that tier takes with `{"input": ..., "projects": [...]}`.
#: Taken from the projects that own the files because Nx applies a target's own negations
#: to every file its own inputs name, so the journeys excluded above could not be named
#: back in beside them. No dependency edge carries it: `orchestrator` holds
#: whole-workspace targets, so every change already selects it, and the key is what this
#: input moves.
JOURNEYS_THE_UNIT_GATES_READ = "journeysTheUnitGatesRead"
#: The orchestrator project's own named input `orchestrator:test-recipes` is keyed on:
#: what the recipe journeys drive, plus the modules that define them. The helpers those
#: modules import reach the key as the project's test-support dependencies.
RECIPE_WORKSPACE = "recipeWorkspace"
#: The key `session-setup:test` is memoized on: what the journey over this checkout's
#: own provisioning drives and reads. Named file by file rather than as `scripts/**/*`
#: or `config/**/*`: the journey runs the real `scripts/session-setup.sh`, which
#: re-provisions the project environment from the lock, so every glob wider than the
#: script, the lock and the one pin it reads makes an unrelated edit pay for that. The
#: last step that script runs — `just repos-bootstrap`, over the tracked checkout list —
#: is in the key as its script, the list reader it sources and the list itself, because
#: what the journey asserts about the siblings is decided by those three files. The one
#: module of `orchestrator/` it names is `orchestrator/root.py`, which the journey imports
#: `REPO_ROOT` from: session setup runs no other orchestrator code. The modules
#: `tests/conftest.py` imports reach the key through the `support-conftest` unit, as they
#: reach every tier's, so an edit to any other module there starts no real provisioning.
SESSION_SETUP_WORKSPACE = "sessionSetupWorkspace"
# llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This module is the domain
# that owns tier keys, one row per test project, as the rows beside this one are.
#: The named input `pre-push-provisioning`'s one test target is keyed on: the hook, the
#: session setup chain its `--provision-only` runs, and the scripts and budgets file the
#: hook runs before and after it, named file by file for the reason the key above is.
PRE_PUSH_PROVISIONING_WORKSPACE = "prePushProvisioningWorkspace"
# llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]
# llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This module is the domain
# that owns tier names, one row per test project, as the block over the ask-seam
# journeys below says: `tests/conftest.py`'s read guard and
# `tests/tree_scans/test_nx_cache_scope.py` resolve every project's key, name and
# directory from here, so the host-sweep project's row lands where each sibling's does
# rather than in `tests/host_sweep/`, which the guard reads before collecting it.
#: The key `host-sweep:test` is memoized on: the files its journeys copy into a fixture
#: repository and run there — session setup, the host sweep script it starts detached,
#: the `justfile` whose `sweep` recipe that job runs, the real `setup-llmlint.sh` and
#: `repos-bootstrap.sh` session setup hands off to, the list reader and checkout list the
#: latter reads, and every adopted pin (`config/*.version`, which the copied setup
#: verifies) — beside the lock and the modules the test imports. A key of its own rather
#: than a widening of `sessionSetupWorkspace`, so an edit to the `justfile` or a pin pays
#: for these doubled journeys and not for that project's real re-provisioning.
HOST_SWEEP_WORKSPACE = "hostSweepWorkspace"
# llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]
#: The key `project-store-race:test` is memoized on: the module under race, the
#: project's own files, and the suite modules `tests/conftest.py` imports — nothing
#: else, because the race reads a temporary root and nothing of this checkout.
PROJECT_STORE_RACE_WORKSPACE = "projectStoreRaceWorkspace"
# llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This is the repository's one catalog
# of test projects, where every project has its entry, and
# `tests/tree_scans/test_nx_cache_scope.py` fails when a project's tests fall outside the
# tiers it describes; the schema-cache entry sits beside the others' because the catalog is
# the domain.
#: The key `schema-cache:test` is memoized on: the project's own files and the suite
#: modules `tests/conftest.py` imports, the schema cache's rule among them — nothing else,
#: because its sessions write only a temporary directory of their own.
SCHEMA_CACHE_WORKSPACE = "schemaCacheWorkspace"
# llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]
# llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This is the repository's one catalog
# of test projects, where every project has its entry, and
# `tests/tree_scans/test_nx_cache_scope.py` fails when a project's tests fall outside the
# tiers it describes; the plan-store-assets and render-diagram entries sit beside the
# others' rather than in a domain of their own because the catalog is the domain.
#: The key `plan-store-assets:test` is memoized on: the lock that decides which plan-store
#: CLI answers, the store configuration it loads from this checkout, the project's own
#: files, and the suite modules `tests/conftest.py` imports — nothing else, because the
#: journey writes only temporary roots.
PLAN_STORE_ASSETS_WORKSPACE = "planStoreAssetsWorkspace"
#: The key `render-diagram:test` is memoized on: what a render reads — the recipe and its
#: script, the committed `config/mermaid/` files, `.puppeteerrc.json`, and the lockfile that
#: names mermaid-cli, Playwright and so the Chromium revision, and the font — the project's
#: own files, and the suite modules `tests/conftest.py` imports.
RENDER_DIAGRAM_WORKSPACE = "renderDiagramWorkspace"
# llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]
#: The key `unpublished-view:test` is memoized on. Files rather than trees, for the reason
#: `unwatchedWorkspace` names, and measured the same way: the tier traced under `strace -f
#: -e trace=openat,execve`, each opened path normalized and intersected with what git
#: tracks. So the key is the justfile, the two scripts the recipe reaches and the module
#: they run, the lock that decides which `onevcs` answers, and the two `config/` files and
#: two scripts the suite's shared modules read — never `scripts/**`, because an edit to a
#: script this view never reaches must not pay for its real sessions. The registry helper
#: the seeding runs, and every other shared module the journeys import, reach the key as
#: the project's test-support dependencies.
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
#: The key `unfinished:test` is memoized on. Files rather than trees, for the reason
#: `unwatchedWorkspace` names, and measured the same way: the tier traced under `strace -f
#: -e trace=openat,execve`, each opened path normalized and intersected with what git
#: tracks. So the key is the justfile, the two wrappers the recipe reaches and the two
#: modules they run, the helper that establishes the manager session, the pins that decide
#: which `onevcs` and which engine answer, the registry helper and run-root builder the
#: journeys seed from, and the suite modules `tests/conftest.py` imports with the files
#: they read. The trace also shows `uv run` opening the root README as package metadata,
#: which decides no verdict here and is left out as every sibling key leaves it; and never
#: `scripts/**` or `tests/unwatched/**`, because an edit this recipe never reaches must not
#: pay for its real sessions.
UNFINISHED_WORKSPACE = "unfinishedWorkspace"
# llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This is the repository's one
# catalog of test projects, where every project has its entry, and
# `tests/tree_scans/test_nx_cache_scope.py` fails when a project's tests fall outside the tiers it
# describes; the board-copy-approval entry sits beside the others' because the catalog is
# the domain.
#: The key `board-copy-approval:test` is memoized on. Files rather than trees, measured as
#: `unpublishedViewWorkspace` was: the tier traced under `strace -f -e trace=openat,execve`,
#: each opened path intersected with what git tracks, and the `orchestrator` modules taken
#: from the bytecode it loaded — the recipes' scripts, the plan store and review configs,
#: the templates the documents render through, the planner bar, and the `justfile` every
#: recipe is read from, which `just` reaches without an `openat` the trace records.
BOARD_COPY_APPROVAL_WORKSPACE = "boardCopyApprovalWorkspace"
# llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]

# llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] The reason above
# `HOST_SWEEP_WORKSPACE`: this module holds one row per test project.
#: The key `graceful-cancel:test` is memoized on. Files rather than trees, for the reason
#: `unwatchedWorkspace` names, and measured the same way: the tier traced under `strace -f
#: -e trace=openat,execve`, each opened path normalized and intersected with what git
#: tracks, with the `orchestrator/` modules it loads from bytecode named by their source.
#: So the key is the launch machinery a `just orchestrate` reaches, the graphs, harness
#: configs and pins the engine reads at launch, and the project's own two files; the
#: stand-ins and helpers it imports reach it as test-support dependencies.
GRACEFUL_CANCEL_WORKSPACE = "gracefulCancelWorkspace"
# llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]
# llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This module holds one row per test
# project, as the block over the ask-seam journeys says: `tests/conftest.py`'s read guard
# and `tests/tree_scans/test_nx_cache_scope.py` resolve every project's key, name and
# directory from here before collecting any of them, so this project's row cannot live
# in its own directory.
#: The key `session-open-conflict:test` is memoized on. Files rather than trees, for the
#: reason `unwatchedWorkspace` names, and measured the same way: the tier traced under
#: `strace -f -e trace=openat,execve`, each opened path normalized and intersected with
#: what git tracks, with the `orchestrator/` modules it loads from bytecode named by their
#: source. So the key is the launch machinery `just orchestrate` and `just repos-apply`
#: reach, the graphs, harness configs and pins the engine reads at launch, and the
#: project's own files; the stand-ins and helpers it imports reach it as test-support
#: dependencies.
SESSION_OPEN_CONFLICT_WORKSPACE = "sessionOpenConflictWorkspace"
# llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]
# llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This is the repository's one catalog
# of test projects, where every project has its entry, and
# `tests/tree_scans/test_nx_cache_scope.py` fails when a project's tests fall outside the
# tiers it describes; the manager-allowlist entry sits beside the others' rather than in a
# domain of its own because the catalog is the domain.
#: The key the `manager-allowlist` project's one tier is memoized on: the recipes, the
#: probe and the resolvers its wrapper sources, the source and its two renderings, the
#: chain the probe reads its identities from, and the modules the journeys import. Files
#: rather than trees, for the reason `unwatchedWorkspace` names.
MANAGER_ALLOWLIST_WORKSPACE = "managerAllowlistWorkspace"
# llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]
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
#: for that. No module of `orchestrator/` is named in it: the one the journeys import,
#: `orchestrator/root.py`, reaches the key with every module `tests/conftest.py` imports
#: through the `support-conftest` unit, because an import is part of every tier's verdict.
#: Its own prose is excluded, as
#: `codeWorkspace` excludes the workspace's: nothing here reads this project's
#: `AGENTS.md`, so an instruction-only edit must not start them.
DAG_UI_WORKSPACE = "dagUiWorkspace"
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
#: The directory every host-tool journey over this repository's plan surface lives under —
#: `just check-plan`, `just review-plan`, `just plan`, `just finish-plan`, `just
#: follow-ups`, the registered check script, the installed engine and a real `oneharness
#: run` — and which every other project's tiers ignore. Not a project itself, as
#: `ASK_SEAM_ROOT` is not: each subject under it is a project in a directory of its own,
#: because while one key covered every journey here, an edit any one of them read re-ran
#: all of them. None of them declares a Python distribution: there is one `pyproject.toml`
#: and one `uv.lock` in this repository and each runs that workspace's own pytest over its
#: directory, so the Nx project graph gains targets rather than the Python workspace
#: gaining members.
PLAN_TOOLING_ROOT = "tests/plan_tooling"
#: Each subject project's target keyed on what its journeys read.
PLAN_TOOLING_SCOPED = "test"
#: The second target of a subject project holding journeys that build a **copy** of this
#: checkout: keyed on the whole workspace because copying the tracked tree is reading all
#: of it, and collecting exactly the tests that carry `reads_docs`, which in a
#: directory-owned project marks a test that copies the checkout or scans every tracked
#: document and nothing else. A target of the project that owns them rather than a marker
#: handing them to another project's tier, so their cost stays charged to the code `nx
#: affected` selects that project for.
PLAN_TOOLING_DOCS_SCOPED = "test-docs"


class SubjectProject(NamedTuple):
    """One directory-owned test project of a family split by subject, as the guards name it."""

    #: The Nx project, which is how `nx affected` charges its tiers to what selects them.
    project: str
    #: The directory it owns: its modules, the helpers only they import, and its
    #: `project.json`.
    root: str
    #: The named input its `project.json` declares, which its narrow target is memoized on
    #: with the `testSupport` of every unit it depends on; the whole workspace for a
    #: project whose only target is the whole-workspace one.
    key: str
    #: Whether it has a whole-workspace `test-docs` target for `reads_docs` to route a test
    #: into.
    docs_tier: bool
    #: The target its key belongs to: `test`, or `test-docs` for a project that has no
    #: narrow target at all.
    target: str = PLAN_TOOLING_SCOPED


# llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This module is the domain
# that owns tier names, one row per test project, as the block over the ask-seam journeys
# says: the read guard and `tests/tree_scans/test_nx_cache_scope.py` resolve each project's
# name, directory and key from here before collecting any of them.
#: One project per subject of the plan surface, in what each is keyed on. Each key was
#: measured rather than kept by habit: the spike's traced run of the tiers these journeys
#: came from — `plan-tooling:test` and `plan-tooling:test-docs` under `strace -f -e
#: trace=openat,execve`, each opened path intersected with what git tracks and attributed to
#: the test its worker was running — for what a child process reads, and an in-process
#: record of every `open` per test for what the journeys read themselves, each without the
#: tests that copy the checkout, whose reads are everything. A family stays a tree where a
#: test can name a path in it or reads every file of it: `scripts/` for the check-and-review
#: journeys, one of which reads every script, and for the follow-up journeys, which copy the
#: whole tree into the mirror checkout their recipe runs in; `personas/` and `graphs/` for
#: the check-and-review journeys, whose plans name a persona by path relative to `graphs/`;
#: and `config/`, `templates/` and `orchestrator/` everywhere, because the engine and the
#: plan store a launch reaches read pins and templates no test names, and a child running
#: `python -m orchestrator…` reads bytecode a trace cannot map to its source. Elsewhere a
#: family is the files measured. The repository's own `README.md`, which `uv run` opens as
#: package metadata, decides no verdict here and is left out as every sibling key leaves it.
PLAN_TOOLING_PROJECTS: tuple[SubjectProject, ...] = (
    SubjectProject(
        "plan-tooling-check-and-review",
        f"{PLAN_TOOLING_ROOT}/check_and_review",
        "planToolingCheckAndReviewWorkspace",
        docs_tier=True,
    ),
    SubjectProject(
        "plan-tooling-follow-ups",
        f"{PLAN_TOOLING_ROOT}/follow_ups",
        "planToolingFollowUpsWorkspace",
        docs_tier=True,
    ),
    SubjectProject(
        "plan-tooling-plan-flow",
        f"{PLAN_TOOLING_ROOT}/plan_flow",
        "planToolingPlanFlowWorkspace",
        docs_tier=True,
    ),
    SubjectProject(
        "plan-tooling-plan-copy",
        f"{PLAN_TOOLING_ROOT}/plan_copy",
        "planToolingPlanCopyWorkspace",
        docs_tier=False,
    ),
)

#: The named input a whole-workspace target is keyed on, which `nx.json` declares.
WHOLE_WORKSPACE = "wholeWorkspace"
#: One project per subject of this repository's drift gates — the tests that hold its
#: prose to the code, configuration and releases it describes — each in a directory of its
#: own directly under `tests/`. While `orchestrator:test-docs` collected them all on the
#: whole workspace, a typo in any document re-ran every one; each is keyed on the documents
#: it reads and the code it reconciles them with. Each key was measured: an in-process
#: record of every `open` per module and the spike's traced run of `orchestrator:test-docs`
#: under `strace -f -e trace=openat,execve` for what a child reads, each intersected with
#: what git tracks. A document is named file by file, so an edit to one selects only the
#: gates that read it; a family stays a tree where a test globs it or can name any path in
#: it — `config/`, `templates/` and `orchestrator/`, whose modules the gates import, and
#: `tests/**/test_*.py` with `tests/test_*.py` where a gate checks that a test module a
#: document names exists. A root test module a gate imports is named file by file, and a
#: shared helper reaches a key through its unit's edge, never through such a glob.
#: `tree-scans` is the one project whose every module copies this checkout or scans every
#: tracked document or Python module, so its one target is keyed on the whole workspace.
DRIFT_GATE_PROJECTS: tuple[SubjectProject, ...] = (
    SubjectProject("dispatch-bar", "tests/dispatch_bar", "dispatchBarWorkspace", docs_tier=False),
    SubjectProject("family-scans", "tests/family_scans", "familyScansWorkspace", docs_tier=False),
    SubjectProject(
        "manager-guidance", "tests/manager_guidance", "managerGuidanceWorkspace", docs_tier=True
    ),
    SubjectProject(
        "plan-guidance", "tests/plan_guidance", "planGuidanceWorkspace", docs_tier=False
    ),
    SubjectProject("release-pins", "tests/release_pins", "releasePinsWorkspace", docs_tier=False),
    SubjectProject(
        "tree-scans",
        "tests/tree_scans",
        WHOLE_WORKSPACE,
        docs_tier=True,
        target=PLAN_TOOLING_DOCS_SCOPED,
    ),
)
# llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]

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
    #: The named input its `project.json` declares, which its one target is memoized on
    #: with the `testSupport` of every unit it depends on.
    key: str


# llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This module is the domain
# that owns tier names: the Nx project a journey is, its directory and its key are read by
# `tests/conftest.py`'s read guard and `tests/tree_scans/test_nx_cache_scope.py`, and
# resolving them from one row each is what keeps a renamed journey from leaving a guard
# checking a project nobody runs, as the module docstring says.
#: One memoization unit per journey, in what each is keyed on. Every key names files
#: rather than trees — `tests/tree_scans/test_nx_cache_scope.py` refuses a `config/**/*` or a
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
        "ask-seam-settled-correlated-ruling",
        f"{ASK_SEAM_ROOT}/settled_correlated_ruling",
        "askSeamSettledCorrelatedRuling",
    ),
    AskSeamJourney(
        "ask-seam-structural-reply", f"{ASK_SEAM_ROOT}/structural_reply", "askSeamStructuralReply"
    ),
)
# llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]

#: The project whose test target owns the journeys over this repository's composition of
#: the DAG Observatory — the two recipes, the proxy behind them, and what the published
#: reader answers. A project of its own for the reason each plan-tooling subject and each
#: ask-seam journey is:
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
#: each project under `PLAN_TOOLING_ROOT` is: a file added here joins this project by being here.
DAG_UI_ROOT = "tests/dag_ui"

#: The project whose test target owns the journeys over `onepipeline unwatched` and the
#: `Stop` hook that reads it — the real hook script, the real recipe, the installed engine
#: they ask, and the launch shapes whose runs it has to name. A project of its own for the
#: reason the plan-tooling subjects, the ask-seam journeys and `dag-ui` are: a watch armed
#: for real, a process killed and left unreaped, this checkout's own project-environment
#: lock held, and three real launches are a cost `nx affected` can only keep off an
#: unrelated edit where it is a separate project. It declares no Python distribution either,
#: for the same reason.
UNWATCHED_PROJECT = "unwatched"
#: That project's one test target. One rather than two, as each ask-seam journey has one:
#: nothing here reads this repository's prose.
UNWATCHED_SCOPED = "test"
#: The directory it owns, which every other project's tiers ignore. Path-selected, as
#: each project under `PLAN_TOOLING_ROOT` is: a file added here joins this project by being here.
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
#: each project under `PLAN_TOOLING_ROOT` is: a file added here joins this project by being here.
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
#: each project under `PLAN_TOOLING_ROOT` is: a file added here joins this project by being here.
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

# llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This module is the domain
# that owns tier names, one row per test project, as the rows beside this one are.
#: The project whose test target owns the journey over the tracked `pre-push` hook's
#: provisioning: the real hook in a copy nothing provisioned, as a publication's
#: worktree is, running session setup's `--provision-only` before its gate. Not a
#: module of `session-setup`, so an edit to the hook's scripts rebuilds only this
#: project's toolchain and none of that project's.
PRE_PUSH_PROVISIONING_PROJECT = "pre-push-provisioning"
#: That project's one test target: nothing here reads this repository's prose.
PRE_PUSH_PROVISIONING_SCOPED = "test"
#: The directory it owns, which every other project's tiers ignore.
PRE_PUSH_PROVISIONING_ROOT = "tests/pre_push_provisioning"
# llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]

# llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] The reason above
# `HOST_SWEEP_WORKSPACE`: this module holds one row per test project.
#: The project whose test target owns the journeys over the host sweep session setup
#: starts detached: the real `scripts/session-setup.sh` and `scripts/host-sweep.sh` in a
#: fixture repository, never waited on, one at a time and at most hourly, one of them
#: running the real `onevcs` and `oneagentgraph` over sandboxed state roots. A project of
#: its own for the reason `unwatched` is: those journeys wait out detached jobs and a
#: real sweep, a cost `nx affected` can only keep off an unrelated edit where it is a
#: separate project.
HOST_SWEEP_PROJECT = "host-sweep"
#: That project's one test target: nothing here reads this repository's prose.
HOST_SWEEP_SCOPED = "test"
#: The directory it owns, which every other project's tiers ignore.
HOST_SWEEP_ROOT = "tests/host_sweep"
# llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]

#: The project whose one target owns the journeys that run the real
#: `scripts/session-setup.sh` in a fixture repository `tests/e2e/provisioning.py` builds,
#: where its real `uv sync` installs the adopted published tools from PyPI: the setup
#: journeys themselves, and the one that reads what the installed engine binary links. A
#: project of its own, not a second target of `session-setup`, because Nx's edges and
#: `nx affected` both work per project: an edit only these journeys reach would otherwise
#: select the project the deterministic tier runs.
SESSION_SETUP_PYPI_PROJECT = "session-setup-pypi"
#: That project's one target. Memoized on the pins and the files its journeys copy and
#: run, which decide what they install, and deliberately outside `SELECTED_TARGETS` and
#: `UNCONDITIONAL_TARGETS`: `just check` never reaches an outside service, and `just test`
#: and `just upgrade` run it.
SESSION_SETUP_PYPI_SCOPED = "test-pypi"
#: The named input its own files are declared under in its `project.json`.
SESSION_SETUP_PYPI_WORKSPACE = "sessionSetupPypiWorkspace"
#: The directory it owns, which every other project's tiers ignore.
SESSION_SETUP_PYPI_ROOT = "tests/session_setup_pypi"

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
#: each project under `PLAN_TOOLING_ROOT` is: a file added here joins this project by being here.
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

# llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This is the repository's one catalog
# of test projects, where every project has its entry, and
# `tests/tree_scans/test_nx_cache_scope.py` fails when a project's tests fall outside the
# tiers it describes; the schema-cache entry sits beside the others' because the catalog is
# the domain.
#: The project whose test target owns the journeys over the one schema cache a session
#: warms: real multi-worker sessions, each test starting a pytest of its own, read through
#: their temporary directory during and after the session. A project of its own because
#: every journey pays for whole sessions, which `nx affected` can only keep off an
#: unrelated edit where it is a separate project.
SCHEMA_CACHE_PROJECT = "schema-cache"
#: That project's one test target: nothing here reads this repository's prose.
SCHEMA_CACHE_SCOPED = "test"
#: The directory it owns, which every other project's tiers ignore.
SCHEMA_CACHE_ROOT = "tests/schema_cache"
# llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]

# llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This is the repository's one catalog
# of test projects, where every project has its entry, and
# `tests/tree_scans/test_nx_cache_scope.py` fails when a project's tests fall outside the
# tiers it describes; the plan-store-assets and render-diagram entries sit beside the
# others' rather than in a domain of their own because the catalog is the domain.
#: The project whose test target owns the journey over a document's image assets: the
#: locked plan-store CLI creating one with `--asset`, copying it into a second `local-md`
#: source and reading both back. A project of its own because it drives a real host tool,
#: a cost `nx affected` can only keep off an unrelated edit where it is a separate project.
PLAN_STORE_ASSETS_PROJECT = "plan-store-assets"
#: That project's one test target: nothing here reads this repository's prose.
PLAN_STORE_ASSETS_SCOPED = "test"
#: The directory it owns, which every other project's tiers ignore.
PLAN_STORE_ASSETS_ROOT = "tests/plan_store_assets"
#: The project whose test target owns the journeys over `just render-diagram`: a real
#: Chromium drawing a Mermaid source in the locked font, and every refusal. A project of its
#: own for the reason the one above is.
RENDER_DIAGRAM_PROJECT = "render-diagram"
#: That project's one test target: nothing here reads this repository's prose.
RENDER_DIAGRAM_SCOPED = "test"
#: The directory it owns, which every other project's tiers ignore.
RENDER_DIAGRAM_ROOT = "tests/render_diagram"
# llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]

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

#: The project whose test target owns the journeys over `just unfinished`: the real
#: recipe, wrappers and modules over a scratch `onevcs` registry of labelled sessions and
#: a built runs root. A project of its own for the reason `unwatched` is: real `onevcs`
#: sessions, real `git`, the installed engine and a real `just` per assertion are a cost
#: `nx affected` can only keep off an unrelated edit where it is a separate project.
UNFINISHED_PROJECT = "unfinished"
#: That project's test target: nothing here reads this repository's prose.
UNFINISHED_SCOPED = "test"
# llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This is the repository's one
# catalog of test projects, where every project has its entry, and
# `tests/tree_scans/test_nx_cache_scope.py` fails when a project's tests fall outside the tiers it
# describes; the unfinished project's timed tier sits beside its other because the catalog
# is the domain.
#: Its uncached target timing the own-sessions read at scale, the one journey its `test`
#: deselects: the figure budget `own-sessions-read-seconds` reports, which a memo would
#: replay from another moment.
UNFINISHED_HOST_SCOPED = "test-host"
# llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]
#: The directory it owns, which every other project's tiers ignore.
UNFINISHED_ROOT = "tests/unfinished"

# llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This is the repository's one
# catalog of test projects, where every project has its entry, and
# `tests/tree_scans/test_nx_cache_scope.py` fails when a project's tests fall outside the tiers it
# describes; the board-copy-approval entry sits beside the others' because the catalog is
# the domain.
#: The project whose test target owns the journeys approving a design document on the
#: `plans` board: the real `just copy-plan`, `just approve-design` and launch gate, the
#: installed plan store and engine, against the GitHub Projects double. A project of its
#: own for the reason `unwatched` is: a real recipe chain per assertion is a cost `nx
#: affected` can only keep off an unrelated edit where it is a separate project.
BOARD_COPY_APPROVAL_PROJECT = "board-copy-approval"
#: That project's one test target: nothing here reads this repository's prose.
BOARD_COPY_APPROVAL_SCOPED = "test"
#: The directory it owns, which every other project's tiers ignore.
BOARD_COPY_APPROVAL_ROOT = "tests/e2e/board_copy_approval"
# llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]

# llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] The reason above
# `HOST_SWEEP_WORKSPACE`: this module holds one row per test project.
#: The project whose test target owns the journey over a `cancel` whose dispatch ends
#: inside its grace period: a real launch through `just orchestrate`, cancelled over the
#: real channel and waited to settlement. A project of its own for the
#: reason `unwatched` is: a real launch waited to settlement is a cost `nx affected` can
#: only keep off an unrelated edit where it is a separate project.
GRACEFUL_CANCEL_PROJECT = "graceful-cancel"
#: That project's one test target: nothing here reads this repository's prose.
GRACEFUL_CANCEL_SCOPED = "test"
#: The directory it owns, which every other project's tiers ignore.
GRACEFUL_CANCEL_ROOT = "tests/graceful_cancel"
# llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]
# llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] The reason above
# `SESSION_OPEN_CONFLICT_WORKSPACE`: this module holds one row per test project, read before any
# project is collected.
#: The project whose test target owns the journey over a continued branch whose base moved
#: to conflict with it: two real launches through `just orchestrate`, one whose worker
#: concludes the merge and one that spends the node's publication budget. A project of its
#: own for the reason `unwatched` is: real launches waited to settlement are a cost `nx
#: affected` can only keep off an unrelated edit where it is a separate project.
SESSION_OPEN_CONFLICT_PROJECT = "session-open-conflict"
#: That project's one test target: nothing here reads this repository's prose.
SESSION_OPEN_CONFLICT_SCOPED = "test"
#: The directory it owns, which every other project's tiers ignore.
SESSION_OPEN_CONFLICT_ROOT = "tests/session_open_conflict"
# llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]

# llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This is the repository's one catalog
# of test projects, where every project has its entry, and
# `tests/tree_scans/test_nx_cache_scope.py` fails when a project's tests fall outside the
# tiers it describes; the manager-allowlist entry sits beside the others' rather than in a
# domain of its own because the catalog is the domain.
#: The project whose test target owns the journeys over `just sync-allowlist` and `just
#: probe-allowlist`: the real recipes, the pinned `oneharness sync` and the probe's
#: scratch project, with the paid tools doubled. A project of its own because each drives
#: real host tools, a cost `nx affected` can only keep off an unrelated edit where it is a
#: separate project.
MANAGER_ALLOWLIST_PROJECT = "manager-allowlist"
#: That project's one test target: nothing here reads this repository's prose.
MANAGER_ALLOWLIST_SCOPED = "test"
#: The directory it owns, which every other project's tiers ignore.
MANAGER_ALLOWLIST_ROOT = "tests/manager_allowlist"
# llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]

# llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This module holds one row per
# test project, as the block over the ask-seam journeys says: the read guard and
# `tests/tree_scans/test_nx_cache_scope.py` resolve every project's name, directory and
# key from here, so the journey projects' rows sit beside their siblings' rather than in
# `tests/e2e/`.
#: The directory the journeys over this repository's launches, landings, recipes, budgets
#: and workspace live under. The shared helpers there are test-support units' and stay where
#: pytest's `pythonpath` imports them from; every journey module is a directory-owned
#: project's, one directory per family of journeys keyed on what that family reads, beside
#: the three nested projects named above, and the root's own `e2e-root` project collects
#: the few modules directly under it.
E2E_ROOT = "tests/e2e"
#: The project at that root, which collects the journey modules directly under it.
E2E_ROOT_PROJECT = "e2e-root"


class JourneyProject(NamedTuple):
    """One directory-owned journey project under `tests/e2e/`, as the guards name it."""

    #: The Nx project, which is how `nx affected` charges a journey to what selects it.
    project: str
    #: The directory it owns: its modules, the data they read, and its `project.json`.
    root: str
    #: The named input its `project.json` declares, which its `test` target is memoized on
    #: with the `testSupport` of every unit it depends on.
    key: str


#: One project per family of journeys, each keyed on what that family reads. These were
#: one project, `orchestrator-e2e`, keyed on every tracked path outside `tests/` but prose,
#: so almost any change re-ran all of them; the families and their reads come from the
#: `spike-gate-profile` tier trace (`strace -f -e trace=openat,execve` over the old tiers,
#: each read attributed to the test whose xdist worker made it and intersected with what
#: git tracks), and each key is the union of its narrow tests' traced reads with the paths
#: their modules name. Imports come from the project graph, as every key's do. A family is
#: kept as a tree where a journey can name a path in it rather than only read a fixed one —
#: `personas/` for the launch journeys whose subject is a persona, `scripts/` for the budget
#: journeys, which copy it whole, and for the launch journeys, one of which reads every
#: file a launch surface tracks — and `orchestrator/` is a tree in every key here but
#: `suite-helpers`', because the scripts these journeys run invoke the package's modules,
#: which Python reads as bytecode a trace of tracked files cannot see. A test marked
#: `reads_checkouts` runs in its project's uncached `test-checkouts` target and a
#: `reads_docs` one — a journey that copies this checkout — in its project's `test-docs`
#: target keyed on the whole workspace, which only `workspace-copies` declares.
E2E_JOURNEYS: tuple[JourneyProject, ...] = (
    JourneyProject("spike-brief", f"{E2E_ROOT}/spike_brief", "spikeBriefWorkspace"),
    JourneyProject(
        "publish-and-landing", f"{E2E_ROOT}/publish_and_landing", "publishAndLandingWorkspace"
    ),
    JourneyProject(
        "launch-monitor-observer",
        f"{E2E_ROOT}/launch_monitor_observer",
        "launchMonitorObserverWorkspace",
    ),
    JourneyProject("budget-gate", f"{E2E_ROOT}/budget_gate", "budgetGateWorkspace"),
    JourneyProject(
        "registry-and-recipes", f"{E2E_ROOT}/registry_and_recipes", "registryAndRecipesWorkspace"
    ),
    JourneyProject("workspace-copies", f"{E2E_ROOT}/workspace_copies", "workspaceCopiesWorkspace"),
    JourneyProject("suite-helpers", f"{E2E_ROOT}/suite_helpers", "suiteHelpersWorkspace"),
    # The journey modules left directly under `tests/e2e/`, which the families above leave
    # alone: today the registry installer's, kept at its path because the rules file
    # `config/onevcs.rules.yml` names it there, and a change to that file is one a sibling's
    # drift refuses. The nested projects own their own directories, so this one collects
    # its modules by name and keys on them alone.
    JourneyProject(E2E_ROOT_PROJECT, E2E_ROOT, "e2eRootWorkspace"),
)
#: The journey project whose journeys copy this checkout — and so read every file it tracks
#: — which is why it is the one with a `test-docs` target keyed on the whole workspace.
WORKSPACE_COPIES_PROJECT = "workspace-copies"
#: The data file `orchestrator:test` measures into.
UNIT_COVERAGE_DATA = ".coverage.parallel"
#: The one project whose targets a marker routes a unit or drift-gate test between: every
#: test module outside a directory-owned project is the orchestrator project's.
MARKER_TIER_ROOT = "orchestrator"


def coverage_data(project: str) -> str:
    """The data file a journey project's `test` target measures `orchestrator/` into."""
    return f".coverage.{project}"


# llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]


#: The uncached tier that reads the measuring tiers' coverage data and enforces the
#: declared floor against it. Deliberately unmemoized:
#: it is seconds of work, and a floor that always runs is one no replay can skip.
COVERAGE_SCOPED = "coverage"

#: The targets that measure a project's own `budgets.yaml`, which a project declares only
#: when it holds one: `nx.json`'s `targetDefaults` states their commands and memos, so a
#: project opts in with `"budgets": {}, "budgets-host": {}`. The memoized `budgets` runs
#: its deterministic budgets only when a change touches it; every budget labelled `host` —
#: an `elapsed` one, or one reading the host — is left to the unmemoized `budgets-host`,
#: which therefore runs whatever the diff. `tests/test_budgets_files.py` holds the file
#: and the targets together.
BUDGET_SCOPED = "budgets"
BUDGET_HOST_SCOPED = "budgets-host"
#: The uncached tier that validates every `budgets.yaml` in the tree, whichever project
#: holds it, so a malformed one never reaches a push however narrow the diff.
BUDGETS_VALIDATION_SCOPED = "validate-budgets"

#: The deterministic tier's targets `just check` lets the diff select, in the order the
#: recipe names them.
SELECTED_TARGETS = (
    "format-check",
    "lint",
    "typecheck",
    CODE_SCOPED,
    DOCS_SCOPED,
    RECIPE_SCOPED,
    BUDGET_SCOPED,
)
#: The deterministic tier's targets no diff can select, so `just check` runs them over
#: every project every time: their subjects are what lives outside this workspace, what
#: the tiers beside them just measured, every budgets file in the tree, and the host a
#: `host` budget reads.
UNCONDITIONAL_TARGETS = (
    CHECKOUT_SCOPED,
    COVERAGE_SCOPED,
    BUDGETS_VALIDATION_SCOPED,
    BUDGET_HOST_SCOPED,
)


def nx_config() -> dict:
    return json.loads((REPO_ROOT / "nx.json").read_text(encoding="utf-8"))


#: The named input a test-support unit declares its own files under: the module, and
#: every data file it opens. `nx.json` declares it empty, so a project that is not a unit
#: contributes nothing when a dependent takes it from the graph.
TEST_SUPPORT = "testSupport"
#: How a tier's cached target takes every unit it depends on, transitively, into its key.
FROM_DEPENDENCIES = {"input": TEST_SUPPORT, "dependencies": True}
#: The directory each test-support unit is declared in, one directory per unit. The
#: modules themselves stay where pytest's `pythonpath` imports them from — `tests/` and
#: `tests/e2e/` — and each unit names its module by path: an Nx project owns a directory,
#: and one directory per helper module under the import path would be forty entries of
#: `pythonpath` and a rewrite of every stand-in path the journeys execute.
SUPPORT_ROOT = "tests/support"


def declaration_path(root: str) -> str:
    """The repository-relative path of the `project.json` declaring the project at ``root``."""
    return f"{root}/project.json" if root else "project.json"


@functools.cache
def project_declaration(root: str) -> dict:
    """The declaration of the project at ``root``, read from its own `project.json` alone.

    The one loader every reader of the graph goes through. The read guard asks it only for
    the declarations its owning project's key resolution reaches — that project's own, and
    each one an input of its key names — so a tier's key can declare exactly the
    `project.json` files its tests open, which a listing of every project would defeat. A
    root with no declaration answers an empty one, as a project Nx does not know.
    """
    declared = REPO_ROOT / declaration_path(root)
    return json.loads(declared.read_text(encoding="utf-8")) if declared.is_file() else {}


@functools.cache
def project_declarations() -> dict[str, dict]:
    """Every project Nx reads here, keyed by the root its `project.json` sits at.

    Derived from what git would commit — tracked or not yet added — rather than listed,
    so a project added, moved, or dropped reaches every reader of the graph by existing.
    The workspace root's own project is keyed by the empty string. Only a reader whose key
    covers every declaration may ask for this — the whole-workspace drift gates that hold
    the static catalog to it; the read guard and the key resolution go through
    :func:`project_declaration` and :func:`project_root` instead.
    """
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--"]
        + [":(glob)**/project.json"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    declarations: dict[str, dict] = {}
    for relative in sorted(filter(None, listing.split("\0"))):
        if (REPO_ROOT / relative).is_file():
            parent = str(Path(relative).parent)
            root = "" if parent == "." else parent
            declarations[root] = project_declaration(root)
    return declarations


#: The name of the project at the workspace root, which declares the workspace-wide targets.
WORKSPACE_PROJECT = "workspace"
#: The prefix every test-support unit's project name carries, ahead of its directory under
#: :data:`SUPPORT_ROOT` spelled with hyphens.
SUPPORT_PREFIX = "support-"


# llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This module is the domain that
# owns the test-project catalog, one row per project, as the blocks over those rows say; the
# read guard resolves a project's root from here rather than by listing every declaration,
# which is what keeps each tier's key to the declarations its tests open.
def catalogued_roots() -> dict[str, str]:
    """Every project the static catalog names, by its Nx name, other than the units.

    The workspace root's project, the orchestrator project, and every directory-owned test
    project whose row sits above. A unit's root follows from its name, so the units are not
    rows. `tests/tree_scans/test_nx_cache_scope.py` holds this, with the
    unit rule, to the projects git lists.
    """
    return {
        # The workspace root's own project, which holds the workspace-wide targets.
        WORKSPACE_PROJECT: "",
        MARKER_TIER_ROOT: MARKER_TIER_ROOT,
        BOARD_COPY_APPROVAL_PROJECT: BOARD_COPY_APPROVAL_ROOT,
        DAG_UI_PROJECT: DAG_UI_ROOT,
        GRACEFUL_CANCEL_PROJECT: GRACEFUL_CANCEL_ROOT,
        HOST_SWEEP_PROJECT: HOST_SWEEP_ROOT,
        HOST_VIEWS_PROJECT: HOST_VIEWS_ROOT,
        MANAGER_ALLOWLIST_PROJECT: MANAGER_ALLOWLIST_ROOT,
        MERGE_POLICY_PROJECT: MERGE_POLICY_ROOT,
        PLAN_STORE_ASSETS_PROJECT: PLAN_STORE_ASSETS_ROOT,
        PROJECT_STORE_RACE_PROJECT: PROJECT_STORE_RACE_ROOT,
        RENDER_DIAGRAM_PROJECT: RENDER_DIAGRAM_ROOT,
        RUN_END_HOOKS_PROJECT: RUN_END_HOOKS_ROOT,
        SCHEMA_CACHE_PROJECT: SCHEMA_CACHE_ROOT,
        SESSION_OPEN_CONFLICT_PROJECT: SESSION_OPEN_CONFLICT_ROOT,
        PRE_PUSH_PROVISIONING_PROJECT: PRE_PUSH_PROVISIONING_ROOT,
        SESSION_SETUP_PROJECT: SESSION_SETUP_ROOT,
        SESSION_SETUP_PYPI_PROJECT: SESSION_SETUP_PYPI_ROOT,
        UNFINISHED_PROJECT: UNFINISHED_ROOT,
        UNPUBLISHED_VIEW_PROJECT: UNPUBLISHED_VIEW_ROOT,
        UNWATCHED_PROJECT: UNWATCHED_ROOT,
        WRITEBACK_BUDGET_PROJECT: WRITEBACK_BUDGET_ROOT,
        **{
            row.project: row.root
            for row in (
                *E2E_JOURNEYS,
                *PLAN_TOOLING_PROJECTS,
                *DRIFT_GATE_PROJECTS,
                *ASK_SEAM_JOURNEYS,
            )
        },
    }


# llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]


def project_root(name: str) -> str:
    """The root of the project Nx knows as ``name``, from the static catalog alone.

    A unit's root is its name's directory under :data:`SUPPORT_ROOT`; any other project an
    input or an edge names is a row of :func:`catalogued_roots`. Resolved without listing
    the graph, so resolving a key opens no declaration it does not then read.
    """
    if name.startswith(SUPPORT_PREFIX):
        return f"{SUPPORT_ROOT}/{name.removeprefix(SUPPORT_PREFIX).replace('-', '_')}"
    roots = catalogued_roots()
    assert name in roots, (
        f"{name!r} is named by an input or an edge but is no row of the static catalog in "
        "tests/nx_inputs.py; add its row there"
    )
    return roots[name]


def dependency_roots(root: str) -> list[str]:
    """The roots of every project ``root`` depends on, transitively, in a stable order.

    Only declared edges: nothing in this workspace infers one, so `implicitDependencies`
    is the whole of the graph a `dependencies` input walks.
    """
    reached: set[str] = set()
    pending = list(project_declaration(root).get("implicitDependencies", []))
    while pending:
        name = pending.pop()
        dependency = project_root(name)
        if dependency in reached:
            continue
        reached.add(dependency)
        pending.extend(project_declaration(dependency).get("implicitDependencies", []))
    return sorted(reached)


def named_inputs(root: str = "") -> dict[str, list]:
    """The named inputs a target of the project at ``root`` resolves against.

    A project's own `namedInputs` override `nx.json`'s of the same name, which is how a
    unit's `testSupport` replaces the empty one and how a tier's key is its own.
    """
    declared = project_declaration(root).get("namedInputs", {})
    return {**nx_config()["namedInputs"], **declared}


class Key(list[str]):
    """A target's key: every glob it hashes, and the separate filesets Nx hashes them as.

    Nx applies a `!` glob to the files of the context that declares it — the target's own
    inputs, or one dependency's — and never across them: a `{"input": ..., "projects":
    ...}` entry is hashed in that project's context, so the target's own negations do not
    reach it. That is measured on the pinned Nx with `nx show target inputs --check`, and
    it is how `orchestrator:test` drops the journeys yet keeps the few its gates read. So
    a key is the flat list every reader iterates, with :func:`covers` asking each fileset
    on its own.
    """

    def __init__(self, filesets: list[list[str]]) -> None:
        super().__init__(glob for fileset in filesets for glob in fileset)
        self.filesets = tuple(tuple(fileset) for fileset in filesets)


def project_input_globs(entries: list, root: str = "") -> list[str]:
    """Expand one project's declared inputs into the repository-relative globs Nx hashes.

    A `dependencies` input is expanded in each transitive dependency's own context — its
    own named inputs, its own root — and a `projects` input in each project it names and
    no further, which is what Nx does with each; every other non-file entry contributes no
    file coverage, as in :func:`resolve_input_globs`.
    """
    return [glob for fileset in project_input_filesets(entries, root) for glob in fileset]


def project_input_filesets(entries: list, root: str = "") -> list[list[str]]:
    """The same expansion as :func:`project_input_globs`, one fileset per context.

    The first fileset is the project's own inputs; each dependency or named project a
    `dependencies` or `projects` entry reaches adds the filesets of its own context.
    """
    named = named_inputs(root)
    own: list[str] = []
    others: list[list[str]] = []
    for entry in entries:
        match entry:
            case {"input": str() as name, "dependencies": True}:
                for dependency in dependency_roots(root):
                    others.extend(project_input_filesets([name], dependency))
            case {"input": str() as name, "projects": str() | list() as named_projects}:
                # Each project named, and only those: Nx does not walk their edges.
                listed = [named_projects] if isinstance(named_projects, str) else named_projects
                for project in listed:
                    others.extend(project_input_filesets([name], project_root(project)))
            case dict():
                pass  # an env or runtime input contributes no file coverage
            case str() if entry in named:
                nested = project_input_filesets(named[entry], root)
                own.extend(nested[0])
                others.extend(nested[1:])
            case _:
                own.extend(repository_relative_globs([entry], project_root=root))
    return [own, *others]


def target_input_globs(root: str, target: str) -> Key:
    """Every repository-relative glob the target ``target`` of the project at ``root``
    hashes, `targetDefaults` and the project graph included, as a :class:`Key`."""
    return _target_input_globs(root, target)


@functools.cache
def _target_input_globs(root: str, target: str) -> Key:
    # Cached because every test's read guard asks for one, and the answer is a property
    # of the declarations this process started with.
    declared = project_declaration(root)["targets"][target].get("inputs") or nx_config()[
        "targetDefaults"
    ].get(target, {}).get("inputs", ["default"])
    return Key(project_input_filesets(declared, root))


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
    owned = f"{project_root}/" if project_root else ""
    return [glob.replace("{workspaceRoot}/", "").replace("{projectRoot}/", owned) for glob in globs]


def named_input_globs(name: str, *, project_root: str = "") -> list[str]:
    """Expand one named input, as the project at ``project_root`` declares it, into
    repository-relative globs."""
    return project_input_globs([name], project_root)


def matches(glob: str, relative: str) -> bool:
    pattern = re.escape(glob).replace(r"\*\*/\*", ".*").replace(r"\*", "[^/]*")
    return re.fullmatch(pattern, relative) is not None


def covers(globs: list[str], relative: str) -> bool:
    """Apply Nx's file-set semantics: a `!` glob removes what the others of its fileset
    added, and a :class:`Key` covers what any one of its filesets does."""
    if isinstance(globs, Key):
        return any(covers(list(fileset), relative) for fileset in globs.filesets)
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
