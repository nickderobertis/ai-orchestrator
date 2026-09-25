"""A cached verdict may only stand in for a verdict on the tree it was keyed on.

Every cached Nx target memoizes an answer. Replaying one is sound exactly when the
key covers everything the check reads, and unsound the moment it does not: a file
the check reads but the key ignores is a tree that can change its answer without
changing its hash, which is a green verdict for a tree that would have failed.

`tests/e2e/test_nx_cache_scope_e2e.py` proves the behaviour against real Nx. These
assertions keep the declarations that behaviour rests on from narrowing again —
including as the suite grows new reads, which is how the subset drifted out of
date in the first place.

The Python suite is keyed at three scopes rather than one, because "keyed on
everything it reads" and "keyed on the whole workspace" are not the same
requirement. Only a handful of tests assert on this repository's prose, and the
costliest read neither prose nor orchestrator code — they drive `just` recipes and
shell scripts. `test-docs` runs the prose contracts and keeps the whole-workspace
key; `test-recipes` runs the recipe journeys under the narrow key those journeys
actually read; `test` runs the remainder, keyed on the workspace minus its prose.
What keeps the narrowed keys honest is not this file — a static scan cannot see
every read — but `tests/conftest.py`, which fails a test the moment it opens
something its own tier's key does not carry.

Two targets here are keyed on nothing, because no key could be right. `coverage`
reads what the measuring tier wrote and compares that total to the declared floor,
so it is uncached and there is no memo to be wrong about. `test-checkouts`
reconciles this repository's configuration against the registered checkouts of the
repositories it routes: those live outside the workspace, so no `nx.json` glob
could name one, and a memoized verdict would describe whatever they looked like
when it was recorded. `conftest.py` holds that boundary from the other side, failing
an unmarked test that opens one.
"""

from __future__ import annotations

import ast
import functools
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest
from conftest import (
    READS_CHECKOUTS_MARKER,
    READS_DOCS_MARKER,
    READS_RECIPES_MARKER,
    REAL_PLAN_STORE_ROOTS_MARKER,
)
from nx_inputs import (
    ASK_SEAM_JOURNEYS,
    ASK_SEAM_ROOT,
    ASK_SEAM_SCOPED,
    CHECKOUT_SCOPED,
    CODE_SCOPED,
    CODE_WORKSPACE,
    COVERAGE_SCOPED,
    DAG_UI_ROOT,
    DAG_UI_SCOPED,
    DOCS_SCOPED,
    FROM_DEPENDENCIES,
    HOST_VIEWS_ROOT,
    HOST_VIEWS_SCOPED,
    MERGE_POLICY_ROOT,
    MERGE_POLICY_SCOPED,
    NX_CACHE_CHECK,
    PLAN_TOOLING_DOCS_SCOPED,
    PLAN_TOOLING_PROJECT,
    PLAN_TOOLING_ROOT,
    PLAN_TOOLING_SCOPED,
    PROJECT_STORE_RACE_ROOT,
    PROJECT_STORE_RACE_SCOPED,
    RECIPE_SCOPED,
    RUN_END_HOOKS_ROOT,
    RUN_END_HOOKS_SCOPED,
    SELECTED_TARGETS,
    SESSION_SETUP_PYPI_PROJECT,
    SESSION_SETUP_PYPI_ROOT,
    SESSION_SETUP_PYPI_SCOPED,
    SESSION_SETUP_ROOT,
    SESSION_SETUP_SCOPED,
    SUPPORT_ROOT,
    TEST_SUPPORT,
    UNCONDITIONAL_TARGETS,
    UNPUBLISHED_VIEW_ROOT,
    UNPUBLISHED_VIEW_SCOPED,
    UNWATCHED_ROOT,
    UNWATCHED_SCOPED,
    WRITEBACK_BUDGET_ROOT,
    WRITEBACK_BUDGET_SCOPED,
    covers,
    dependency_roots,
    matches,
    named_input_globs,
    project_declarations,
    repository_relative,
    repository_relative_globs,
    resolve_input_globs,
    target_input_globs,
)

from orchestrator.root import REPO_ROOT

WHOLE_WORKSPACE = "wholeWorkspace"
#: Every one of these runs from the workspace root against the whole tree — ruff
#: over `.`, shellcheck over `scripts/`, persona validation over `personas/`, and
#: the prose-contract tests, which exist to read documentation.
WORKSPACE_SCOPED = ("lint", "typecheck", "format-check", DOCS_SCOPED)
#: The tiers keyed on less than the whole workspace, and the exact globs that earn
#: it: the workspace with its documentation removed, and nothing else.
CODE_KEYED = (CODE_SCOPED,)
CODE_WORKSPACE_GLOBS = [
    "{workspaceRoot}/**/*",
    "!{workspaceRoot}/docs/**/*",
    "!{workspaceRoot}/**/*.md",
]


def _tracked() -> frozenset[str]:
    """Repository content, which is all Nx ever hashes.

    A test that reaches into `.venv`, `node_modules`, or `.git` is reading
    toolchain state rather than the tree under judgement. Nx skips ignored paths
    entirely, so no input declaration could cover them and claiming otherwise would
    make this guard's coverage claim false.
    """
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    return frozenset(filter(None, listing.split("\0")))


def _nx_config() -> dict:
    return json.loads((REPO_ROOT / "nx.json").read_text(encoding="utf-8"))


def _effective_inputs(project_root: str, target: str) -> list[str]:
    """What one target hashes, resolved through the project graph as Nx resolves it."""
    return target_input_globs(project_root, target)


def _named_repository_paths(text: str, tracked: frozenset[str]) -> set[str]:
    """Every tracked repository path ``text`` names as a literal.

    Two shapes, because a file names a repository path both ways: joined onto the
    root at the call site, and written as a bare relative literal something else
    joins later. Reading only the first shape missed every path a fixture copies
    from a tuple — which is where the front-end reads live.

    The two are trusted differently, and have to be. A literal joined onto the root
    *is* a repository path, so a directory named that way counts as everything under
    it. A bare literal is only a repository path when it happens to look like one,
    and a single word that happens to match a directory — a node called
    `orchestrator`, a label called `tests` — is a coincidence, not a read; expanding
    those swept the whole tree into one browser fixture's reads.
    """
    named: set[str] = set()
    for match in re.finditer(r'(?:REPO_ROOT|ROOT)\s*/\s*"([^"]+)"(\s*/\s*"[^"]+")*', text):
        rooted = "/".join(re.findall(r'"([^"]+)"', match.group(0)))
        named |= {path for path in tracked if path == rooted or path.startswith(f"{rooted}/")}
    named |= {
        match.group(1) for match in re.finditer(r'"([^"\n]+)"', text) if match.group(1) in tracked
    }
    return named


def _is_documentation(relative: str) -> bool:
    return relative.startswith("docs/") or relative.endswith(".md")


def test_workspace_scoped_targets_are_keyed_on_the_whole_workspace() -> None:
    """A target that reads the whole tree cannot be memoized against a slice of it."""
    named = _nx_config()["namedInputs"]
    assert named[WHOLE_WORKSPACE] == ["{workspaceRoot}/**/*"]

    project = json.loads((REPO_ROOT / "orchestrator/project.json").read_text(encoding="utf-8"))
    for target in WORKSPACE_SCOPED:
        assert project["targets"][target]["inputs"] == [WHOLE_WORKSPACE], (
            f"orchestrator:{target} runs from the workspace root over the whole tree, "
            "so its cached verdict must be keyed on the whole workspace"
        )
    # The plan-tooling project's second target is the same claim about a second
    # project: the journeys it collects copy this checkout, so what they read is
    # everything git tracks and no narrower key could describe their verdict.
    host_tools = json.loads(
        (REPO_ROOT / f"{PLAN_TOOLING_ROOT}/project.json").read_text(encoding="utf-8")
    )
    assert host_tools["targets"][PLAN_TOOLING_DOCS_SCOPED]["inputs"] == [WHOLE_WORKSPACE], (
        f"{PLAN_TOOLING_PROJECT}:{PLAN_TOOLING_DOCS_SCOPED} collects the journeys that "
        "copy this checkout, so its cached verdict must be keyed on the whole workspace"
    )
    llmlint = _nx_config()["targetDefaults"]["lint-llm-diff"]["inputs"]
    assert llmlint[0] == WHOLE_WORKSPACE, (
        "the llmlint tier judges the whole workspace diff and shares this one key"
    )


#: The recipe whose project selection the two lists above partition.
DETERMINISTIC_RECIPE = "check"
#: How that recipe hands Nx the selection `scripts/nx-selection.sh` decided, and how it
#: names the tiers it never lets a diff decide. Matched literally, because what makes
#: the narrowing sound is which of the two invocations a target is named by.
SELECTED_INVOCATION = './scripts/nx.sh "${selected[@]}" -t '
UNCONDITIONAL_INVOCATION = "./scripts/nx.sh run-many -t "
#: Where that recipe takes its selection from. One source, so the base a narrowed gate
#: judges against cannot be decided in two places.
SELECTION_SOURCE = "selection=$(./scripts/nx-selection.sh)"


def _project_declarations() -> dict[str, dict]:
    """Every project Nx knows here, keyed by the root its declaration sits at.

    Derived from what git tracks rather than listed, so a project added, moved, or
    dropped reaches these guards by existing.
    """
    declarations: dict[str, dict] = {}
    for tracked in sorted(_tracked()):
        if Path(tracked).name == "project.json":
            parent = str(Path(tracked).parent)
            declarations[parent if parent != "." else ""] = json.loads(
                (REPO_ROOT / tracked).read_text(encoding="utf-8")
            )
    return declarations


def _memoizes(target: str, declaration: dict) -> bool:
    """Whether Nx would replay ``target`` rather than run it, defaults included.

    A `targetDefaults` entry that names no `cache` is Nx's own default, which is not
    to cache — so a target nothing declares is read as unmemoized rather than assumed
    memoized, because that is the direction in which a wrong answer here is safe.
    """
    declared = declaration["targets"][target]
    if "cache" in declared:
        return bool(declared["cache"])
    return bool(_nx_config()["targetDefaults"].get(target, {}).get("cache", False))


def _owners(target: str) -> list[tuple[str, dict]]:
    return [
        (root, declaration)
        for root, declaration in _project_declarations().items()
        if target in declaration["targets"]
    ]


def _deterministic_recipe_body() -> str:
    lines = (REPO_ROOT / "justfile").read_text(encoding="utf-8").splitlines()
    start = lines.index(f"{DETERMINISTIC_RECIPE}:") + 1
    body: list[str] = []
    for line in lines[start:]:
        if line and not line.startswith((" ", "\t")):
            break
        body.append(line)
    return "\n".join(body)


def test_every_tier_a_diff_can_deselect_is_one_a_memo_would_have_answered() -> None:
    """Narrowing is only sound where skipping a tier and replaying it are the same thing.

    Nx leaves a project out when no changed file matched an input of any of its
    targets, which is exactly the condition under which each of those targets would
    have replayed a memo recorded for this tree. That equivalence is what licenses the
    deterministic tier to run over fewer projects than it has — and it is an argument
    about *cached* targets only. An unmemoized tier reached by a diff-driven selection
    is not replayed when its project is left out; it simply does not run, and the gate
    quietly stops checking what it checks.
    """
    for target in SELECTED_TARGETS:
        owners = _owners(target)
        assert owners, f"the deterministic tier selects {target}, which no project declares"
        for root, declaration in owners:
            assert _memoizes(target, declaration), (
                f"{root or 'workspace'}:{target} is a tier a diff can deselect, so a "
                "project Nx leaves out has to be one this target would have replayed "
                "for; an unmemoized tier belongs in UNCONDITIONAL_TARGETS instead"
            )

    for target in UNCONDITIONAL_TARGETS:
        owners = _owners(target)
        assert owners, f"the deterministic tier always runs {target}, which no project declares"
        for root, declaration in owners:
            assert not _memoizes(target, declaration), (
                f"{root or 'workspace'}:{target} is memoized, so the diff could decide it "
                "and it no longer earns a place among the tiers that always run"
            )


def test_the_deterministic_recipe_splits_its_tiers_along_that_same_line() -> None:
    """The recipe is the selection; these two lists only describe it if it says so."""
    body = _deterministic_recipe_body()
    assert set(SELECTED_TARGETS).isdisjoint(UNCONDITIONAL_TARGETS)
    assert SELECTION_SOURCE in body, (
        "the base a narrowed gate judges against comes from scripts/nx-selection.sh"
    )
    assert f"{SELECTED_INVOCATION}{','.join(SELECTED_TARGETS)}" in body
    assert f"{UNCONDITIONAL_INVOCATION}{','.join(UNCONDITIONAL_TARGETS)}" in body
    # Each phase runs whatever the one before it returned, so one run reports every
    # failing target: no Nx invocation is chained to the next by `&&`, and each records
    # its own failure for the combined status.
    invocations = re.findall(r"\./scripts/nx\.sh [^|]+", body)
    assert len(invocations) == 3, invocations
    assert "&& ./scripts/nx.sh" not in body
    assert body.count('| redact_secrets >>"$log" || failed+=(') == len(invocations), (
        "every phase of the deterministic recipe records its own failure and lets the "
        "next phase run"
    )
    named = [target for group in re.findall(r"-t ([\w,-]+)", body) for target in group.split(",")]
    assert sorted(named) == sorted([*SELECTED_TARGETS, *UNCONDITIONAL_TARGETS]), (
        "every target the deterministic tier runs is either one a diff may deselect or "
        "one it never may, and which of the two decides whether skipping it is sound"
    )


def test_the_marker_that_routes_a_test_to_its_tier_means_the_same_thing_everywhere() -> None:
    """Reconcile the places the marker name is independently written down.

    Each marker names a routing decision, not a label: pytest registers it,
    `conftest.py` enforces it, and the Nx targets select on it. Those declarations
    are written separately and nothing else compares them, so a rename that missed
    one would leave a tier silently selecting nothing — and a `test` tier that ran
    the prose contracts anyway, keyed on a workspace without prose, is the false
    green this whole file exists to prevent.

    A marker routes a test between the targets of the project whose directory holds
    it, and never out of that project: which project owns a test is decided by where
    it lives, so the cost of running it is charged to the code `nx affected` would
    select for it. So every orchestrator tier ignores the directory the host-tool
    project owns, and that project selects between its own two keys with the same
    marker the orchestrator project uses between its four.
    """
    manifest = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # A registration is `"<name>: ..."` or, for one taking arguments, `"<name>(...): ..."`.
    registered = re.findall(r'^\s*"(\w+)[(:]', manifest, flags=re.MULTILINE)
    # Every marker `tests/conftest.py` names, not the tier-routing ones alone: an
    # unregistered marker is silently no marker at all, so the fixture that consults it
    # would simply never fire. `real_plan_store_roots` is the opt-out from the plan-store
    # root isolation, and a module that declared it and got the isolation anyway would
    # fail on assertions about a directory it never chose.
    for marker in (
        READS_DOCS_MARKER,
        READS_RECIPES_MARKER,
        READS_CHECKOUTS_MARKER,
        REAL_PLAN_STORE_ROOTS_MARKER,
    ):
        assert marker in registered, (
            f"pytest must register {marker!r} in [tool.pytest.ini_options] markers"
        )

    targets = json.loads((REPO_ROOT / "orchestrator/project.json").read_text(encoding="utf-8"))[
        "targets"
    ]
    # Every code-keyed selector has to exclude both narrower tiers, not merely the
    # first one written down: a tier that dropped one of them would run those tests
    # twice and key the second run on a tree they do not read.
    code_selectors = [
        selector
        for target in CODE_KEYED
        for selector in re.findall(r"-m '([^']+)'", targets[target]["command"])
    ]
    assert len(code_selectors) == len(CODE_KEYED), code_selectors
    excluded = (
        f"not {READS_DOCS_MARKER} and not {READS_RECIPES_MARKER} and not {READS_CHECKOUTS_MARKER}"
    )
    assert all(selector.startswith(excluded) for selector in code_selectors), code_selectors

    # And the selectors have to partition: a test is in exactly one tier.
    for marker, target in (
        (READS_DOCS_MARKER, DOCS_SCOPED),
        (READS_RECIPES_MARKER, RECIPE_SCOPED),
        (READS_CHECKOUTS_MARKER, CHECKOUT_SCOPED),
    ):
        assert f"-m {marker}" in targets[target]["command"]
        marked = re.search(r"-m '?(not )?(\w+)'?", targets[target]["command"])
        assert marked is not None and marked.group(1) is None

    # The same marker routes inside the project that owns the host-tool journeys, and
    # there it chooses between that project's own two targets rather than handing a
    # test to another project's tier. Both halves are asserted together because only
    # the pair partitions: a project whose narrow target deselects a marker no target
    # of its own collects has stopped running those tests, and one whose targets both
    # collect them runs them twice against two different keys.
    host_tools = json.loads(
        (REPO_ROOT / f"{PLAN_TOOLING_ROOT}/project.json").read_text(encoding="utf-8")
    )["targets"]
    assert f"-m 'not {READS_DOCS_MARKER}'" in host_tools[PLAN_TOOLING_SCOPED]["command"]
    assert f"-m {READS_DOCS_MARKER}" in host_tools[PLAN_TOOLING_DOCS_SCOPED]["command"]
    # And no other project may collect that directory, or its cost is charged to a key
    # that does not describe it.
    for target in (DOCS_SCOPED, RECIPE_SCOPED, CHECKOUT_SCOPED, *CODE_KEYED):
        assert f"--ignore={PLAN_TOOLING_ROOT}" in targets[target]["command"], (
            f"orchestrator:{target} collects {PLAN_TOOLING_ROOT}, which the "
            f"{PLAN_TOOLING_PROJECT} project owns"
        )


#: Every place the parallel worker contract is independently written down. It is a
#: contract because the number was chosen by measurement against this host — see
#: `docs/repo-lifecycle.md` — and a recipe that quietly drifted to a different one
#: would stop being evidence for the tier the gate actually runs.
PARALLEL_SITES = (
    (f"{PLAN_TOOLING_ROOT}/project.json", PLAN_TOOLING_SCOPED),
    (f"{PLAN_TOOLING_ROOT}/project.json", PLAN_TOOLING_DOCS_SCOPED),
    (f"{SESSION_SETUP_PYPI_ROOT}/project.json", SESSION_SETUP_PYPI_SCOPED),
    ("orchestrator/project.json", CODE_SCOPED),
    ("orchestrator/project.json", DOCS_SCOPED),
    ("orchestrator/project.json", RECIPE_SCOPED),
    ("orchestrator/project.json", CHECKOUT_SCOPED),
    ("justfile", "test-e2e"),
)


def _worker_contracts(path: str, target: str) -> list[tuple[str, str]]:
    """Every ``-n N --dist MODE`` pair one declaration carries."""
    if path.endswith(".json"):
        text = json.loads((REPO_ROOT / path).read_text(encoding="utf-8"))["targets"][target][
            "command"
        ]
    else:
        recipe = re.search(
            rf"^{re.escape(target)}:\n((?:[ \t]+.*\n?)+)",
            (REPO_ROOT / path).read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
        assert recipe is not None, f"no {target} recipe in {path}"
        text = recipe.group(1)
    return re.findall(r"-n (\d+) --dist (\w+)", text)


def test_every_parallel_declaration_names_the_same_worker_contract() -> None:
    """The worker count and distribution are one contract, written in eight places.

    Nothing derives them from a shared value — pytest takes them as command-line
    flags and Nx targets are literal commands — so the reconciliation has to be a
    gate rather than a definition. Without it `just test-e2e` could drift to a
    different count than the tier `just gate` runs, and the inner loop would stop
    being evidence about the thing the gate judges.
    """
    found = {site: _worker_contracts(*site) for site in PARALLEL_SITES}
    empty = [site for site, pairs in found.items() if not pairs]
    assert not empty, f"these declarations carry no '-n N --dist MODE': {empty}"

    contracts = {pair for pairs in found.values() for pair in pairs}
    assert len(contracts) == 1, f"the parallel worker contract has drifted apart: {found}"


#: The expression a journey runs *this* checkout's own provisioning through, rather
#: than the script's bare name, which appears in prose all over this suite and names a
#: copy under `tmp_path` in every provisioning journey but one.
OWN_PROVISIONING = 'REPO_ROOT / "scripts" / "session-setup.sh"'

#: The decorator that takes this checkout's install and joins the group serialising
#: access to it, and the module-level tuple that spreads the same pair.
SHARED_INSTALL_DECORATOR = "shares_workspace_install"

#: What begins driving a run: `onepipeline start` drives a DAG to settlement and
#: `adopt` attaches a fresh driver to one. Written out here rather than parsed out of
#: the launcher's shell, and reconciled against it by
#: :func:`test_the_launch_verbs_this_scan_looks_for_are_the_launchers_own` — the drift
#: gate, so a verb added to or dropped from `scripts/onepipeline.sh` fails there rather
#: than leaving this scan quietly looking for a launch surface that has moved. Which
#: scripts and which recipes *reach* those verbs stays derived, so a launcher added,
#: renamed, or dropped anywhere on that path moves the answer on its own.
LAUNCH_VERBS = ("start", "adopt")

REACHES_A_LAUNCH = re.compile(rf"onepipeline\.sh\"?\s+(?:{'|'.join(LAUNCH_VERBS)})\b")

#: The launcher whose own `case` arm decides which verbs are a launch, and the shape of
#: a `case` arm in it. The arm is matched rather than the verbs, so the gate below reads
#: whatever that script now lists instead of looking for what this module expects.
LAUNCHER = "scripts/onepipeline.sh"
CASE_ARM = re.compile(r"^ +([a-z][a-z-]*(?: *\| *[a-z][a-z-]*)*)\)$", re.MULTILINE)

#: The `just` recipe this repository documents as *the* launch, asserted below so that a
#: `JUST_RECIPE` which has stopped matching recipe bodies fails on a named absence
#: rather than on an empty set that also looks like a launch surface having gone.
DOCUMENTED_LAUNCH_RECIPE = "orchestrate"

JUST_RECIPE = re.compile(r"^([a-z][a-z0-9-]*)[^\n:]*:\n((?:[ \t]+.*\n?)*)", re.MULTILINE)


def _launching_recipes() -> frozenset[str]:
    """Every `just` recipe whose body reaches a launch, directly or through one script.

    Two levels rather than every level, because `just plan` reaches the engine through
    a script of its own rather than on its recipe line, and that is as far as this
    follows: a recipe reaching a launch through a second script it does not itself name
    is not found here. That bound is what the scan can establish from the files it
    reads, and it is stated because the answer reads like a complete one. A module that
    types one of these recipe names is a reader
    of the toolchain the writers above rewrite: every step of the round trip it then
    waits on is a `just` recipe blocking on `uv run`, which waits on the very lock a
    journey re-provisioning this checkout holds.
    """
    indirect = {
        f"scripts/{script.name}"
        for script in sorted(REPO_ROOT.joinpath("scripts").glob("*.sh"))
        if REACHES_A_LAUNCH.search(script.read_text(encoding="utf-8"))
    }
    launches = re.compile(
        "|".join([REACHES_A_LAUNCH.pattern, *(re.escape(path) for path in sorted(indirect))])
    )
    recipes = JUST_RECIPE.findall((REPO_ROOT / "justfile").read_text(encoding="utf-8"))
    return frozenset(name for name, body in recipes if launches.search(body))


def _names_a_launch(tree: ast.AST, recipes: frozenset[str]) -> bool:
    """Whether this tree's syntax names one of those recipes in a `just` invocation.

    Named for what it can establish, which is that the verb is *written* in one of those
    shapes rather than that it is reached. It reads syntax, so it cannot tell an argv
    list that is executed from one that is only built — and it deliberately does not try:
    a false positive pins one test to a worker that was already carrying the module,
    and a false negative is the defect this whole check exists to catch.

    Read from the syntax rather than from a name, so that neither half is a convention
    a module can drift out of. Both spellings the journeys use are the same shape:
    `just("orchestrate", ...)` and `_just("orchestrate", ...)` pass the verb as the
    first positional argument of a call to a just-runner, and `_attached(["just",
    "plan", ...])` passes it as the second word of an argv list. Matching the verb as a
    bare string instead would match every line of prose that names one.
    """
    for node in ast.walk(tree):
        match node:
            case ast.Call(
                func=ast.Name(id=str(called)) | ast.Attribute(attr=str(called)),
                args=[ast.Constant(value=str(verb)), *_],
            ) if called.lstrip("_") == "just" and verb in recipes:
                return True
            case ast.List(elts=[ast.Constant(value="just"), ast.Constant(value=str(verb)), *_]) if (
                verb in recipes
            ):
                return True
    return False


def _modules_naming_a_launch(root: str, recipes: frozenset[str]) -> list[str]:
    """Every test module under `root` whose syntax names a launch, by file name.

    Per module rather than per test, because a launch is not a property of the body that
    types it. `asked` in `tests/ask_seam/ask_manager/test_ask_manager_e2e.py` is
    function-scoped and spends a real `just orchestrate` for every test that names it,
    refusal journeys included; the launch fixtures in
    `tests/ask_seam/launch/test_launch_ask_seam_e2e.py` are module-scoped, so they run once
    per worker that receives *any* test from there. In
    both directions the module is what carries the cost, and a test that looks inert
    beside them is scattered by `--dist loadgroup` onto a worker where it launches a run
    anyway.
    """
    return [
        module.name
        for module in sorted(REPO_ROOT.joinpath(root).rglob("test_*.py"))
        if _names_a_launch(ast.parse(module.read_text(encoding="utf-8")), recipes)
    ]


#: A pytest plugin that records the xdist group each *collected* test resolves to.
#: Read from a collection rather than from the source, because the group is what an
#: item carries rather than how it is spelled: a decorator, a module `pytestmark`, and
#: a constant one file assigns from another all arrive here identically, and a scan for
#: any one spelling would pass a suite that had drifted into the others.
GROUP_DUMP_PLUGIN = """
import os


def pytest_collection_modifyitems(session, config, items):
    with open(os.environ["ORCHESTRATOR_GROUP_LOG"], "w", encoding="utf-8") as log:
        for item in items:
            mark = item.get_closest_marker("xdist_group")
            group = mark.args[0] if mark is not None and mark.args else ""
            log.write(f"{item.nodeid}\\t{group}\\n")
"""

#: Where that plugin writes what it saw.
GROUP_LOG_ENV = "ORCHESTRATOR_GROUP_LOG"


def _collected_groups(tmp_path: Path) -> dict[str, str]:
    """Every collected test id, and the xdist group it resolves to — from real pytest."""
    plugins = tmp_path / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    (plugins / "group_dump.py").write_text(GROUP_DUMP_PLUGIN, encoding="utf-8")
    log = tmp_path / "groups.tsv"
    collected = subprocess.run(
        ["uv", "run", "pytest", "--collect-only", "--no-cov", "-p", "group_dump"],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(plugins),
            GROUP_LOG_ENV: str(log),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert collected.returncode == 0, collected.stdout + collected.stderr
    recorded = log.read_text(encoding="utf-8").splitlines()
    return dict(line.split("\t", 1) for line in recorded if line)


def _reprovisioning_tests() -> list[str]:
    """Every test whose body runs *this* checkout's own provisioning, as `<module>::<name>`."""
    found: list[str] = []
    # Every journey module of the suite, not one directory of them: a journey that
    # re-provisions this checkout constrains scheduling wherever it lives, and the
    # journeys now span three test projects. Plan-root resolution is the one host-tool
    # journey whose established module name predates the `_e2e` suffix convention.
    modules = set(REPO_ROOT.joinpath("tests").rglob("test_*_e2e.py"))
    modules.add(REPO_ROOT / "tests" / "plan_tooling" / "test_plan_root_env.py")
    for module in sorted(modules):
        source = module.read_text(encoding="utf-8")
        if OWN_PROVISIONING not in source:
            continue
        for function in ast.walk(ast.parse(source)):
            if not isinstance(function, ast.FunctionDef) or not function.name.startswith("test_"):
                continue
            if OWN_PROVISIONING in (ast.get_source_segment(source, function) or ""):
                found.append(f"{module.name}::{function.name}")
    return found


#: A path every worker of the suite shares one copy of. A fixture that mutates one is
#: the reason a whole module has to land on a single worker, whatever its individual
#: tests do — which is what `_module_scope_checkout_writers` looks for.
SHARED_CHECKOUT_ROOT = "REPO_ROOT"


def _module_scope_checkout_writers() -> dict[str, str]:
    """Journey modules whose *setup* mutates this checkout, by module name and fixture.

    Read from the fixture rather than from the tests, because that is where this
    constraint comes from and the tests are what hides it. A `scope="module"` fixture
    runs once per **worker that receives any test from the module**, not once per
    module: `--dist loadgroup` scatters the tests that name no group, so a module whose
    setup writes a shared path has that setup racing itself across workers while every
    test in it looks independent.
    """
    found: dict[str, str] = {}
    for module in sorted(REPO_ROOT.joinpath("tests").rglob("test_*_e2e.py")):
        source = module.read_text(encoding="utf-8")
        for fixture in ast.walk(ast.parse(source)):
            if not isinstance(fixture, ast.FunctionDef):
                continue
            for decorator in fixture.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                keywords = {
                    keyword.arg: keyword.value for keyword in decorator.keywords if keyword.arg
                }
                scope = keywords.get("scope")
                autouse = keywords.get("autouse")
                broad = isinstance(scope, ast.Constant) and scope.value in {"module", "session"}
                always = isinstance(autouse, ast.Constant) and autouse.value is True
                body = ast.get_source_segment(source, fixture) or ""
                if broad and always and SHARED_CHECKOUT_ROOT in body:
                    found[module.name] = fixture.name
    return found


def test_the_launch_verbs_this_scan_looks_for_are_the_launchers_own() -> None:
    """`LAUNCH_VERBS` is a restatement, so this is the gate that keeps it honest.

    `scripts/onepipeline.sh` decides which verbs are a launch — its own comment calls
    them "every shape of launch this repository has" — and it decides it in a `case`
    arm, which is where the credentials and the ask seam are exported. Reading that arm
    here rather than re-deriving the scan from it keeps the shell out of
    :data:`REACHES_A_LAUNCH`, and still fails the day a third shape of launch is added
    or one of these two stops being one.

    The arm is required to be the file's only one, because "the first arm" would make
    which arm was read a silent choice: a `case` added above it would move this gate
    onto a different question while it went on passing.
    """
    launcher = REPO_ROOT / LAUNCHER
    arms = CASE_ARM.findall(launcher.read_text(encoding="utf-8"))
    assert len(arms) == 1, (
        f"{LAUNCHER} carries {len(arms)} `case` arm(s) — {arms} — where this gate reads "
        "exactly one, so which of them decides a launch is no longer unambiguous"
    )

    declared = frozenset(verb.strip() for verb in arms[0].split("|"))
    assert declared == frozenset(LAUNCH_VERBS), (
        f"{LAUNCHER} treats {sorted(declared)} as a launch while this module scans for "
        f"{sorted(LAUNCH_VERBS)}; correct `LAUNCH_VERBS`, because a verb missing from it "
        "leaves every journey that spends that launch free to scatter across workers"
    )


def test_a_module_whose_setup_writes_this_checkout_lands_on_one_worker(
    tmp_path: Path,
) -> None:
    """An autouse module-scoped fixture constrains its whole module, not its callers.

    `repository_credentials_file` in `tests/ask_seam/launch/test_launch_ask_seam_e2e.py`
    adds a name to this checkout's own `.env` and puts the original bytes back afterwards. It is
    `scope="module"` and `autouse`, so it runs once per worker that receives *any* test
    from that module — and its create is `O_EXCL`, so two workers reaching it together
    means one of them raises `FileExistsError` before its test starts.

    That is not hypothetical. The five tests of that module which named no group were
    scattered across the other workers by `--dist loadgroup`, and every one of them
    errored in setup on a publication clone — where, unlike a developer's checkout,
    there is no `.env` for the fixture to append to, so all four workers took the
    creating branch at once. The gate refused the push and the branch could not land.

    The rule the older reasoning got wrong was to ask what each test's *body* does:
    refusal journeys launch no run and wait on no deadline, so leaving them ungrouped
    read as free. Setup is what they share, and setup is not free.
    """
    writers = _module_scope_checkout_writers()
    assert writers, (
        "no journey module declares an autouse module-scoped fixture that touches "
        f"{SHARED_CHECKOUT_ROOT}; this scan is looking for the shape that forces a "
        "module onto one worker, and finding none means it has stopped matching"
    )

    groups = _collected_groups(tmp_path)
    for module, fixture in sorted(writers.items()):
        collected = {node: group for node, group in groups.items() if f"/{module}::" in node}
        assert collected, f"{module} declares {fixture} but collected no tests"

        scattered = sorted(node for node, group in collected.items() if not group)
        assert not scattered, (
            f"{module}::{fixture} is autouse and module-scoped, so it runs on every "
            f"worker that receives a test from {module}; these name no xdist group and "
            f"so are scattered across workers by `--dist loadgroup`, running that "
            f"fixture concurrently against one shared checkout: {scattered}"
        )

        named = sorted(set(collected.values()))
        assert len(named) == 1, (
            f"{module} resolves to {named}. `--dist loadgroup` serialises one group "
            f"name and never two, so {fixture} would still run on two workers at once"
        )


def test_the_toolchain_writers_and_readers_are_collected_into_one_xdist_group(
    tmp_path: Path,
) -> None:
    """One group name, or the constraint is not a constraint.

    `uv` holds an **exclusive** lock on `<root>/.venv`, and every `just` recipe in this
    suite reaches its tool through `uv run`, which waits on that lock for as long as a
    holder keeps it. A journey running this checkout's own `session-setup.sh` takes that
    lock and rewrites the `.venv/bin` other workers resolve their tools from; the
    deadline-based channel journeys are what waits on it, one `just` recipe at a time.

    `--dist loadgroup` co-locates the tests that share a group *name* and says nothing
    about two different names — those run on two workers at once. So a writer in one
    group and a reader in another are exactly as concurrent as if neither declared
    anything, which is what this asserts against: not that each side declares *a*
    group, but that every one of them resolves to the same one.

    Read off a real collection, so the assertion is about the items the scheduler will
    see rather than about how any of them happens to be spelled. The readers are found
    the same way — which modules name a launching `just` recipe, over the recipes
    derived from the `justfile` — so what may scatter is decided by something this check
    reads.

    Inside a module that names one, nothing may scatter, whatever its tests look like.
    The premise this replaces allowed an ungrouped test there on the ground that the
    refusal journeys launch no run: their bodies do not, and their fixtures do. Asking
    what a test's *body* does is the same mistake
    :func:`test_a_module_whose_setup_writes_this_checkout_lands_on_one_worker` records.
    """
    groups = _collected_groups(tmp_path)
    recipes = _launching_recipes()
    assert DOCUMENTED_LAUNCH_RECIPE in recipes, (
        f"`just {DOCUMENTED_LAUNCH_RECIPE}` is the launch this repository documents, and "
        f"this scan did not find it among {sorted(recipes)}: either the recipe stopped "
        f"reaching /{REACHES_A_LAUNCH.pattern}/, or `JUST_RECIPE` has stopped reading "
        "recipe bodies out of the justfile"
    )

    launching = _modules_naming_a_launch(ASK_SEAM_ROOT, recipes)
    assert launching, (
        f"no test module under {ASK_SEAM_ROOT} names {sorted(recipes)}; that is the whole "
        "of what those journeys do, so finding none means this scan has stopped matching "
        "rather than that the constraint has lifted"
    )

    readers = {
        node: group
        for node, group in groups.items()
        if any(f"/{module}::" in node for module in launching)
    }
    assert readers, (
        f"{launching} name a launch but collected no tests, so nothing there is "
        "serialised against the journeys that re-provision this checkout"
    )

    scattered = sorted(node for node, group in readers.items() if not group)
    assert not scattered, (
        f"these are collected from {launching}, which launch runs through `just "
        f"{'`/`just '.join(sorted(recipes))}`, and declare no xdist group — so "
        f"`--dist loadgroup` scatters them across workers, where each spends a real "
        f"launch beside the journeys that are polling `just` recipes through the `uv` "
        f"lock it holds: {scattered}"
    )

    writers = {
        node: group
        for node, group in groups.items()
        for named in _reprovisioning_tests()
        if node.split("[", 1)[0].endswith(named)
    }
    assert writers, (
        "no collected test provisions this checkout, so nothing here is holding the "
        f"lock these journeys wait on; the scan looks for {OWN_PROVISIONING}"
    )

    unscheduled = sorted(node for node, group in writers.items() if not group)
    assert not unscheduled, (
        f"{unscheduled} re-provision this checkout and declare no xdist group at all, so "
        f"they run wherever the scheduler puts them; declare @{SHARED_INSTALL_DECORATOR}"
    )

    named = set(readers.values()) | set(writers.values())
    assert len(named) == 1, (
        f"the journeys that re-provision this checkout and the journeys that wait on "
        f"`uv run` while they do resolve to {sorted(named)}. `--dist loadgroup` "
        "serialises one group name and never two, so more than one name here leaves a "
        "writer free to run beside a reader on another worker — which is the state this "
        "constraint was added to end. Readers: "
        f"{sorted(set(readers.values()))}; writers: {sorted(set(writers.values()))}"
    )


#: The flags that decide how a tier *runs* rather than what it collects. Dropped before
#: a target's own arguments are replayed as a collection, because `-n 4` and a coverage
#: report change nothing about which tests a selector names.
_RUNNER_FLAGS = frozenset({"-n", "--dist"})


def _selection(command: str) -> list[str]:
    """The arguments that decide what one tier collects, from its real command.

    Taken from the command rather than from its `-m` expression alone, because a tier
    selects by path as well: the `plan-tooling` project and each ask-seam journey's
    project name a directory and every orchestrator tier ignores them all. A partition
    derived from the markers alone would report the orchestrator tiers covering the
    suite while the directories the host-tool projects own were collected by nobody.
    """
    _, _, tail = command.partition("uv run pytest ")
    assert tail, command
    selection: list[str] = []
    skip = False
    for argument in shlex.split(tail):
        if skip:
            skip = False
        elif argument in _RUNNER_FLAGS:
            skip = True
        elif not argument.startswith(("--cov", "--no-cov")):
            selection.append(argument)
    return selection


def _collected(selection: list[str]) -> set[str]:
    return set(_collected_once(tuple(selection)))


@functools.cache
def _collected_once(selection: tuple[str, ...]) -> frozenset[str]:
    """Every test id one tier's own arguments select, from a real collection.

    No extra ``-q``: the repository's own ``addopts`` already carries one, and a
    second turns the listing into per-file counts that cannot be compared as sets.
    """
    collected = subprocess.run(
        ["uv", "run", "pytest", "--collect-only", "--no-cov", *selection],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert collected.returncode == 0, collected.stdout + collected.stderr
    return frozenset(line.strip() for line in collected.stdout.splitlines() if "::" in line)


#: Every tier that runs part of this suite, as the file and target that declares it.
#: Every one of them is a target of the project whose
#: directory holds the tests it collects: the `plan-tooling` project owns the host-tool
#: journeys over the plan surface in two targets — one keyed on what they read, one on
#: the whole workspace for the journeys that copy this checkout — each ask-seam journey's
#: project owns that one journey in one target, the `dag-ui` project owns the
#: journeys over this repository's composition of the Observatory in one, the
#: `session-setup` project owns the journey over this checkout's own provisioning in
#: one, the `session-setup-pypi` project owns the journeys that install the published
#: tools from PyPI in one, the
#: `unwatched` project owns the journeys over the verb a `Stop` hook reads and the hook
#: itself in one, the `merge-policy` project owns the journeys that hold
#: the restated `merge_policy` vocabulary to the launcher in one, the `writeback-budget`
#: project owns the journey that holds the adopted engine to the copy deadline its items
#: earn in one, the `run-end-hooks` project owns the journey that fires the run-end hooks
#: through a real launch in one, the `project-store-race` project owns the clock-bounded
#: replacement race over the record store in one, the `unpublished-view` project owns the
#: journey over `just unpublished` in one, the `host-views` project owns the journeys
#: over `just status` and `just host` in one, and the orchestrator project owns the rest
#: in four.
SUITE_TIERS = (
    (f"{PLAN_TOOLING_ROOT}/project.json", PLAN_TOOLING_SCOPED),
    (f"{PLAN_TOOLING_ROOT}/project.json", PLAN_TOOLING_DOCS_SCOPED),
    (f"{SESSION_SETUP_ROOT}/project.json", SESSION_SETUP_SCOPED),
    (f"{SESSION_SETUP_PYPI_ROOT}/project.json", SESSION_SETUP_PYPI_SCOPED),
    *((f"{journey.root}/project.json", ASK_SEAM_SCOPED) for journey in ASK_SEAM_JOURNEYS),
    (f"{DAG_UI_ROOT}/project.json", DAG_UI_SCOPED),
    (f"{UNWATCHED_ROOT}/project.json", UNWATCHED_SCOPED),
    (f"{MERGE_POLICY_ROOT}/project.json", MERGE_POLICY_SCOPED),
    (f"{WRITEBACK_BUDGET_ROOT}/project.json", WRITEBACK_BUDGET_SCOPED),
    (f"{RUN_END_HOOKS_ROOT}/project.json", RUN_END_HOOKS_SCOPED),
    (f"{PROJECT_STORE_RACE_ROOT}/project.json", PROJECT_STORE_RACE_SCOPED),
    (f"{UNPUBLISHED_VIEW_ROOT}/project.json", UNPUBLISHED_VIEW_SCOPED),
    (f"{HOST_VIEWS_ROOT}/project.json", HOST_VIEWS_SCOPED),
    ("orchestrator/project.json", CODE_SCOPED),
    ("orchestrator/project.json", DOCS_SCOPED),
    ("orchestrator/project.json", RECIPE_SCOPED),
    ("orchestrator/project.json", CHECKOUT_SCOPED),
)


def test_every_tier_of_the_suite_partitions_it_between_them() -> None:
    """Every selection partitions one suite: no test may be collected twice or not at all.

    The tiers exist because they are keyed on different trees, and a test lands in
    exactly one of them — in the project whose directory holds it, and then in the
    target of that project whose key matches what it reads. That is the shape that
    loses a test in silence: a typo in any selection, or a directory a project claims
    and no tier collects, leaves tests no invocation runs, and a suite that runs fewer
    tests reports the same green as one that runs them all. So the partition is derived
    from the real commands — paths, ignores and markers alike — and checked against real
    collections rather than read off the JSON.
    """
    parts = [
        _collected(
            _selection(
                json.loads((REPO_ROOT / path).read_text(encoding="utf-8"))["targets"][target][
                    "command"
                ]
            )
        )
        for path, target in SUITE_TIERS
    ]

    for first in range(len(parts)):
        for second in range(first + 1, len(parts)):
            overlap = parts[first] & parts[second]
            assert not overlap, f"two tiers both collect {sorted(overlap)[:5]}"
    whole = _collected([])
    collected = set().union(*parts)
    assert collected == whole, (
        f"the tiers no longer cover the suite: {sorted(whole - collected)[:5]} is "
        "collected by none of them"
    )
    assert all(parts), [len(part) for part in parts]


def test_the_code_only_test_key_drops_prose_and_nothing_else() -> None:
    """The narrowed key is narrowed by exactly one thing, which has a tier of its own."""
    named = _nx_config()["namedInputs"]
    assert named[CODE_WORKSPACE] == CODE_WORKSPACE_GLOBS, (
        "orchestrator:test replays a verdict for every tracked path this key covers, "
        "so narrowing it further would memoize a claim about code it never read"
    )

    project = json.loads((REPO_ROOT / "orchestrator/project.json").read_text(encoding="utf-8"))
    for target in CODE_KEYED:
        assert project["targets"][target]["inputs"] == [CODE_WORKSPACE], (
            f"orchestrator:{target} runs part of the Python code suite, so it must be "
            "keyed on everything that suite reads"
        )

    globs = _effective_inputs("orchestrator", CODE_SCOPED)
    missed = sorted(path for path in _tracked() if not covers(globs, path))
    assert missed and all(_is_documentation(path) for path in missed), (
        f"only documentation may fall outside this key: {missed}"
    )
    # The whole-workspace tier is what covers the rest, so it must actually exist.
    assert DOCS_SCOPED in project["targets"]


def test_the_coverage_tier_is_unmemoized_and_waits_for_the_tier_that_measures() -> None:
    """The floor is enforced once, on data the tier that measured it actually wrote.

    Two things make that sound, and both are declarations rather than conventions:
    the tier waits on the measuring tier, so it can never report on a subset; and it
    is uncached, so a replayed test verdict still pays for a fresh comparison against
    the floor.
    """
    project = json.loads((REPO_ROOT / "orchestrator/project.json").read_text(encoding="utf-8"))
    coverage = project["targets"][COVERAGE_SCOPED]

    assert sorted(coverage["dependsOn"]) == sorted(CODE_KEYED), (
        "the coverage tier must wait on every tier that measures, or the floor is "
        f"evaluated against part of the suite: {coverage['dependsOn']}"
    )
    assert _nx_config()["targetDefaults"][COVERAGE_SCOPED] == {"cache": False}, (
        "a memoized floor could be replayed for a tree it never measured; this tier "
        "is seconds of work and is deliberately re-run every time"
    )
    # And the measuring tier has to actually write the data this one reads, or the
    # floor is judged against whatever an earlier run happened to leave behind.
    for target in CODE_KEYED:
        data_file = project["targets"][target]["outputs"][0].removeprefix("{workspaceRoot}/")
        assert data_file in coverage["command"], (
            f"{data_file} is measured but never read, so its lines do not count "
            f"towards the enforced floor: {coverage['command']}"
        )


def test_every_repository_path_the_suite_reads_is_part_of_a_test_key() -> None:
    """A new test that reads a new path must not be able to replay a stale verdict."""
    globs = _effective_inputs("orchestrator", CODE_SCOPED)
    tracked = _tracked()
    read: set[str] = set()
    for source in sorted(REPO_ROOT.joinpath("tests").rglob("*.py")):
        read |= _named_repository_paths(source.read_text(encoding="utf-8"), tracked)

    # A guard that found nothing would pass silently forever.
    assert {"AGENTS.md", "justfile", "scripts/session-setup.sh"} <= read, read
    uncovered = sorted(path for path in read if not covers(globs, path))
    assert all(_is_documentation(path) for path in uncovered), (
        "orchestrator:test reads these repository paths but is not keyed on them, "
        f"so a change to one replays a stale verdict: {uncovered}"
    )
    # What the suite reads outside this key is not exempt, only keyed elsewhere:
    # `test-docs` is keyed on the whole workspace, so it covers every path here —
    # and `conftest.py` is what holds each of those reads to that tier.
    assert "AGENTS.md" in uncovered, (
        "the sentinel prose read moved; keep a real one here or this guard stops guarding"
    )


def test_the_e2e_witnesses_still_name_paths_outside_the_project_roots() -> None:
    """Each journey's witness must stay a file no project-root glob would cover.

    One witness per tier, and they have to differ in exactly the way the two keys
    do: the prose witness proves the whole-workspace tier notices documentation,
    and the code witness proves the narrowed tier still notices everything else.
    """
    globs = repository_relative_globs(
        resolve_input_globs(["default"], _nx_config()["namedInputs"]), project_root="orchestrator"
    )
    for witness in ("AGENTS.md", "justfile"):
        assert not covers(globs, witness)
        assert (REPO_ROOT / witness).exists()

    code_globs = _effective_inputs("orchestrator", CODE_SCOPED)
    assert not covers(code_globs, "AGENTS.md")
    assert covers(code_globs, "justfile")


def _suite_modules() -> dict[str, str]:
    """Every module under `tests/` by the bare name an import would use."""
    return {
        source.stem: str(source.relative_to(REPO_ROOT))
        for source in sorted(REPO_ROOT.joinpath("tests").rglob("*.py"))
    }


def _imported_suite_modules(relative: str, modules: dict[str, str]) -> set[str]:
    """The `tests/` modules ``relative`` reaches by import, transitively.

    Imports are how a tier reads code the file-read guard cannot see: `pytest`
    resolves `from waits import timeout` through the import machinery rather than
    `open`, so a helper that drifted outside the key would change the tier's answer
    without changing its hash and nothing at runtime would notice.
    """
    reached: set[str] = set()
    pending = [relative]
    while pending:
        current = pending.pop()
        if current in reached:
            continue
        reached.add(current)
        tree = ast.parse((REPO_ROOT / current).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module.split(".")[0]]
            pending.extend(modules[name] for name in names if name in modules)
    return reached - {relative}


def _direct_imports(relative: str, modules: dict[str, str]) -> set[str]:
    """The `tests/` modules ``relative`` imports itself, by the bare name pytest resolves."""
    tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
    reached: set[str] = set()
    for node in ast.walk(tree):
        match node:
            case ast.Import(names=aliases):
                names = [alias.name.split(".")[0] for alias in aliases]
            case ast.ImportFrom(module=str() as module, level=0):
                names = [module.split(".")[0]]
            case _:
                continue
        reached |= {modules[name] for name in names if name in modules}
    return reached - {relative}


def _unit_declarations() -> dict[str, dict]:
    """Every test-support unit, by the root its declaration sits at."""
    return {
        root: declared
        for root, declared in project_declarations().items()
        if root.startswith(f"{SUPPORT_ROOT}/")
    }


def _tier_project_roots() -> list[str]:
    """Every project under `tests/` whose targets collect tests, which is every one there
    that is not a unit; a module under one of these is that project's alone."""
    return [
        root
        for root in project_declarations()
        if root.startswith("tests/") and not root.startswith(f"{SUPPORT_ROOT}/")
    ]


def _shared_helpers() -> list[str]:
    """Every module under `tests/` that is not a test and that no tier project owns."""
    roots = _tier_project_roots()
    return sorted(
        relative
        for relative in _suite_modules().values()
        if not Path(relative).name.startswith("test_")
        and not any(relative.startswith(f"{root}/") for root in roots)
    )


def _unit_owners() -> dict[str, str]:
    """Each tracked path under `tests/` a unit carries, and the one unit that carries it."""
    tracked = _tracked()
    owners: dict[str, str] = {}
    for root, declared in _unit_declarations().items():
        globs = named_input_globs(TEST_SUPPORT, project_root=root)
        for path in tracked:
            if path.startswith("tests/") and covers(globs, path):
                assert path not in owners, (
                    f"{path} is carried by both {owners[path]} and {declared['name']}; a file "
                    "under tests/ is one unit's, or an edit to it invalidates tiers that "
                    "reach only the other"
                )
                owners[path] = declared["name"]
    return owners


def _direct_units(source: str, owners: dict[str, str]) -> set[str]:
    """The units ``source`` reaches itself: each module it imports, each stand-in it names."""
    tracked = _tracked()
    named = _direct_imports(source, _suite_modules()) | _stand_ins_named({source}, tracked)
    return {owners[path] for path in named if path in owners}


def _unit_closure(names: set[str]) -> set[str]:
    """``names`` and every unit they depend on, through the declared edges."""
    declared = {
        unit["name"]: unit.get("implicitDependencies", []) for unit in _unit_declarations().values()
    }
    reached: set[str] = set()
    pending = list(names)
    while pending:
        name = pending.pop()
        if name not in reached:
            reached.add(name)
            pending.extend(declared[name])
    return reached


def _reached_units(declaring: list[str]) -> set[str]:
    """Every unit a tier collecting ``declaring`` reaches, with `tests/conftest.py`.

    The tier's own sources are its test modules and every module of its own project they
    import — a helper under the project's root, which is no unit's — and from each of
    those the units it imports or names as a stand-in, then everything those depend on.
    """
    owners = _unit_owners()
    modules = _suite_modules()
    sources = {"tests/conftest.py", *declaring}
    for module in list(sources):
        sources |= _imported_suite_modules(module, modules)
    own = {source for source in sources if source not in owners}
    direct: set[str] = set()
    for source in own | {"tests/conftest.py"}:
        direct |= _direct_units(source, owners)
    if "tests/conftest.py" in owners:
        direct.add(owners["tests/conftest.py"])
    return _unit_closure(direct)


def _dependency_names(root: str) -> set[str]:
    return {project_declarations()[dependency]["name"] for dependency in dependency_roots(root)}


def _taken_units(root: str, target: str) -> set[str] | None:
    """The units whose `testSupport` this target hashes, or `None` when it takes none."""
    declared = project_declarations()[root]["targets"][target]
    inputs = declared.get("inputs") or _nx_config()["targetDefaults"].get(target, {}).get(
        "inputs", []
    )
    taken: set[str] | None = None
    units = {unit["name"] for unit in _unit_declarations().values()}
    for entry in inputs:
        match entry:
            case {"input": str() as name, "dependencies": True} if name == TEST_SUPPORT:
                taken = (taken or set()) | (_dependency_names(root) & units)
            case {"input": str() as name, "projects": str() as listed} if name == TEST_SUPPORT:
                taken = (taken or set()) | {listed}
            case {"input": str() as name, "projects": list() as listed} if name == TEST_SUPPORT:
                taken = (taken or set()) | set(listed)
    return taken


def _tier_modules(path: str, target: str) -> list[str]:
    """The test modules one tier collects, from a real collection of its own command."""
    command = json.loads((REPO_ROOT / path).read_text(encoding="utf-8"))["targets"][target][
        "command"
    ]
    return sorted({test_id.split("::")[0] for test_id in _collected(_selection(command))})


def test_every_shared_helper_is_one_test_support_unit() -> None:
    """A helper under `tests/` reaches a tier's key by an edge, so it has to be a node.

    Every module there that is not a test and that no tier project owns is carried by
    exactly one unit, each unit is declared in a directory of its own named for what it
    carries, and each has the one target that makes an edit to its files a change `nx
    affected` attributes to it: Nx's own locator reads only a target's inputs, never a
    project's named inputs, so a unit with no target would be a node no diff could touch.
    """
    owners = _unit_owners()
    helpers = _shared_helpers()
    assert "tests/conftest.py" in helpers and "tests/e2e/waits.py" in helpers, helpers
    unowned = [helper for helper in helpers if helper not in owners]
    assert not unowned, (
        f"{unowned} are shared test-support modules no unit under {SUPPORT_ROOT}/ carries, "
        "so a tier importing one would reach it through no edge at all"
    )
    assert _nx_config()["namedInputs"][TEST_SUPPORT] == [], (
        f"nx.json's {TEST_SUPPORT} is what a project that is not a unit contributes to a "
        "dependent's key, which is nothing"
    )
    for root, declared in _unit_declarations().items():
        stem = Path(root).name
        assert Path(root).parent == Path(SUPPORT_ROOT), root
        assert declared["name"] == f"support-{stem.replace('_', '-')}", (root, declared["name"])
        own = declared["namedInputs"][TEST_SUPPORT][0].removeprefix("{workspaceRoot}/")
        assert Path(own.removesuffix("/**/*")).stem.replace("-", "_") == stem, (
            f"{root} is named for {stem} and carries {own} first; a unit is named for the "
            "module or stand-in tree it is"
        )
        assert declared["targets"] == {
            "test-support": {"executor": "nx:noop", "inputs": [TEST_SUPPORT]}
        }, f"{root} declares {declared['targets']}"


def test_each_unit_depends_on_exactly_what_its_own_module_reaches() -> None:
    """The unit graph is the import graph, so a tier's key is its imports' closure.

    Each unit's edges are the units its module imports or names as a stand-in — no more,
    so an edit to a helper it never reaches stays out of its dependents' keys, and no
    fewer, so one it does reach is never left out — and its `testSupport` carries every
    tracked file the module names joined onto the root, because what a helper opens is
    part of what it answers.
    """
    owners = _unit_owners()
    tracked = _tracked()
    for root, declared in _unit_declarations().items():
        carried = named_input_globs(TEST_SUPPORT, project_root=root)
        modules = [
            path
            for path, owner in owners.items()
            if owner == declared["name"] and path.endswith(".py")
        ]
        reached: set[str] = set()
        for module in modules:
            reached |= _direct_units(module, owners)
            opened = sorted(
                path
                for path in _named_files((REPO_ROOT / module).read_text(encoding="utf-8"), tracked)
                if not covers(carried, path)
            )
            assert not opened, f"{declared['name']} does not carry {opened}, which {module} opens"
        reached.discard(declared["name"])
        assert sorted(declared.get("implicitDependencies", [])) == sorted(reached), (
            f"{declared['name']} depends on {declared.get('implicitDependencies', [])} while "
            f"its module reaches {sorted(reached)}"
        )


@pytest.mark.parametrize(
    ("path", "target"), SUITE_TIERS, ids=[f"{path}:{target}" for path, target in SUITE_TIERS]
)
def test_every_tier_is_keyed_on_exactly_the_units_its_modules_reach(path: str, target: str) -> None:
    """A tier replays soundly only while every helper its modules reach is in its key.

    Imports are how a tier reads code the file-read guard cannot see: `pytest` resolves
    `from waits import timeout` through the import machinery rather than `open`, so a
    helper outside the key would change the tier's answer without changing its hash. So
    every unit a tier's modules reach — through `tests/conftest.py`, which runs around
    every test, and through what each helper reaches in turn — is a dependency of the
    tier's project, whatever the tier's key, and nothing else here can make it one.

    Where the tier takes its units into its key through the graph, it takes exactly those:
    one it never reaches would make an unrelated edit re-run it. A project whose one
    narrow target reaches all its edges takes them as `dependencies`; one with two narrow
    targets — `orchestrator`, whose `test-recipes` reaches fewer helpers than its other
    tiers — names each such target's closure with `projects`, because Nx's edges are per
    project and the other targets' helpers are not this one's. A tier keyed on a whole
    tree carries every unit's files by glob already.
    And every module of the tier's own that its tests import — one no unit carries, a
    test module another tier owns among them — is in its key as well.
    """
    root = str(Path(path).parent)
    declaring = _tier_modules(path, target)
    assert declaring, f"{root}:{target} collects nothing; the tier would run empty"
    reached = _reached_units(declaring)
    assert "support-conftest" in reached, (
        "a tier that reached no unit would leave this guard holding nothing"
    )

    missing = sorted(reached - _dependency_names(root))
    assert not missing, (
        f"{root}:{target} collects modules that reach {missing}, which {path} does not "
        "depend on, so editing one replays a verdict recorded before it moved"
    )

    taken = _taken_units(root, target)
    declaration = project_declarations()[root]
    if taken is not None:
        assert taken == reached, (
            f"{root}:{target} takes {sorted(taken - reached)} it never reaches and misses "
            f"{sorted(reached - taken)} it does"
        )
        owners = _unit_owners()
        modules = _suite_modules()
        own = {"tests/conftest.py", *declaring}
        for module in list(own):
            own |= _imported_suite_modules(module, modules)
        globs = _effective_inputs(root, target)
        unkeyed = sorted(
            module for module in own if module not in owners and not covers(globs, module)
        )
        assert not unkeyed, (
            f"{root}:{target} imports {unkeyed}, which no unit carries and its own key "
            "does not name, so editing one replays a verdict recorded before it moved"
        )
    elif _memoizes(target, declaration):
        globs = _effective_inputs(root, target)
        files = {owned for owned, unit in _unit_owners().items() if unit in reached}
        uncovered = sorted(owned for owned in files if not covers(globs, owned))
        assert not uncovered, (
            f"{root}:{target} takes no unit through the graph and its own key misses {uncovered}"
        )


def test_each_project_depends_on_nothing_its_tiers_do_not_reach() -> None:
    """An edge no tier needs is an edit to an unrelated helper selecting this project."""
    by_project: dict[str, set[str]] = {}
    for path, target in SUITE_TIERS:
        root = str(Path(path).parent)
        by_project.setdefault(root, set()).update(_reached_units(_tier_modules(path, target)))
    for root, reached in sorted(by_project.items()):
        surplus = sorted(_dependency_names(root) - reached)
        assert not surplus, f"{root} depends on {surplus}, which none of its tiers reaches"


def _declared_filesets() -> list[tuple[str, str]]:
    """Every file glob any Nx configuration here names, with where it is named."""
    found: list[tuple[str, str]] = []

    def walk(entries: list, where: str) -> None:
        for entry in entries:
            if isinstance(entry, str) and entry.lstrip("!").startswith(
                ("{workspaceRoot}/", "{projectRoot}/")
            ):
                found.append((entry, where))

    # `nx.json`'s own `{projectRoot}` globs name no directory until a project resolves
    # them, which is where each project's are read below.
    for name, entries in _nx_config()["namedInputs"].items():
        walk([entry for entry in entries if "{projectRoot}" not in entry], f"nx.json:{name}")
    for root, declared in project_declarations().items():
        where = f"{root or '.'}/project.json"
        # A project's own `{projectRoot}` names its directory, so it is read as the
        # workspace path it resolves to rather than dropped.
        owned = f"{{workspaceRoot}}/{root}/" if root else "{workspaceRoot}/"
        for name, entries in declared.get("namedInputs", {}).items():
            walk([entry.replace("{projectRoot}/", owned) for entry in entries], f"{where}:{name}")
        for target, spec in declared.get("targets", {}).items():
            walk(
                [
                    entry.replace("{projectRoot}/", owned)
                    for entry in spec.get("inputs", [])
                    if isinstance(entry, str)
                ],
                f"{where}:{target}",
            )
    return found


def test_no_configuration_names_a_path_that_does_not_exist() -> None:
    """A key naming a file that is not there covers nothing, and reads as if it did.

    `nx.json` keyed `session-setup:test` on `tests/nx_workspace.py`, a path with no file
    behind it, while the helper it meant sat under `tests/e2e/`. So every literal
    path any configuration names is a file git tracks, every glob covers something, and
    every project an input or an edge names is one Nx has.
    """
    tracked = _tracked()
    for entry, where in _declared_filesets():
        relative = entry.lstrip("!").replace("{workspaceRoot}/", "")
        if "*" in relative:
            # A family under a directory git ignores — draft personas a journey writes and
            # reads back — covers nothing tracked by construction, and is declared so the
            # read guard admits it; any other glob has to cover something.
            ignored = (
                subprocess.run(
                    ["git", "check-ignore", "-q", relative.split("*")[0].rstrip("/")],
                    cwd=REPO_ROOT,
                    check=False,
                ).returncode
                == 0
            )
            assert ignored or any(matches(relative, path) for path in tracked), (
                f"{where} names {entry}, which covers nothing git tracks"
            )
        else:
            assert relative in tracked, f"{where} names {entry}, which git does not track"
    names = {declared["name"] for declared in project_declarations().values()}
    for root, declared in project_declarations().items():
        named = set(declared.get("implicitDependencies", []))
        for spec in declared.get("targets", {}).values():
            for entry in spec.get("inputs", []):
                if isinstance(entry, dict) and "projects" in entry:
                    listed = entry["projects"]
                    named |= {listed} if isinstance(listed, str) else set(listed)
                    assert named <= _dependency_names(root), (
                        f"{root or '.'}/project.json keys a target on {sorted(named)} that "
                        "its project does not depend on, so no edge carries that key"
                    )
        assert named <= names, f"{root or '.'}/project.json names {sorted(named - names)}"


#: The named inputs `nx.json` itself declares: the keys every project shares, and the
#: empty `testSupport` a project that is not a unit contributes. A tier's own key is
#: declared in its own `project.json`, where it reads as that project's.
SHARED_NAMED_INPUTS = frozenset(
    {"default", "sharedGlobals", WHOLE_WORKSPACE, CODE_WORKSPACE, NX_CACHE_CHECK, TEST_SUPPORT}
)


def test_nx_json_carries_no_tier_inventory_and_no_key_lists_a_helper_by_glob() -> None:
    """Helpers reach a key through an edge, never through a list a tier keeps of them.

    `nx.json` declares only what every project shares. And no glob in a tier's own key
    or a unit's own files covers a shared helper module: a directory glob standing in for
    the list — `tests/e2e/**/*` for the helpers there — is still a hand-kept approximation
    of the graph, and invalidates every tier keyed on it whenever that directory grows.
    """
    assert set(_nx_config()["namedInputs"]) == SHARED_NAMED_INPUTS, sorted(
        set(_nx_config()["namedInputs"]) - SHARED_NAMED_INPUTS
    )
    helpers = _shared_helpers()
    whole = {WHOLE_WORKSPACE, CODE_WORKSPACE, "default"}
    for entry, where in _declared_filesets():
        if where.startswith("nx.json:") and where.removeprefix("nx.json:") in whole:
            continue
        relative = entry.lstrip("!").replace("{workspaceRoot}/", "")
        if "*" not in relative or entry.startswith("!"):
            continue
        swept = [helper for helper in helpers if matches(relative, helper)]
        assert not swept, f"{where} reaches helper modules {swept} by the glob {entry}"


#: The one module of `orchestrator/` the session-setup journeys import, directly and
#: through `tests/conftest.py`, and so the one their keys carry.
SESSION_SETUP_ORCHESTRATOR_IMPORT = "orchestrator/root.py"


def test_the_pypi_journeys_are_a_project_the_check_never_selects() -> None:
    """The journeys that install from PyPI stay behind an edge of their own.

    They re-provision a fixture repository and fetch the adopted releases from PyPI, so
    they are the one target of the `session-setup-pypi` project — which `just test` and
    `just upgrade` run and no selection `just check` makes can reach — keyed on what they
    copy and run. A project rather than a second target of `session-setup`, because Nx's
    edges and `nx affected` are per project. The one module of `orchestrator/` either
    session-setup project is keyed on is `orchestrator/root.py`, which every one of their
    modules imports `REPO_ROOT` from, so an edit to any other leaves both alone; both are
    keyed on `scripts/session-setup.sh`, which both run; and no other project collects the
    directory the PyPI journeys live in.
    """
    assert SESSION_SETUP_PYPI_SCOPED not in (*SELECTED_TARGETS, *UNCONDITIONAL_TARGETS)
    declared = project_declarations()[SESSION_SETUP_PYPI_ROOT]
    assert declared["name"] == SESSION_SETUP_PYPI_PROJECT
    assert set(declared["targets"]) == {SESSION_SETUP_PYPI_SCOPED}, declared["targets"]
    assert (
        f"pytest {SESSION_SETUP_PYPI_ROOT} "
        in declared["targets"][SESSION_SETUP_PYPI_SCOPED]["command"]
    )
    assert _memoizes(SESSION_SETUP_PYPI_SCOPED, declared)
    assert set(project_declarations()[SESSION_SETUP_ROOT]["targets"]) == {SESSION_SETUP_SCOPED}
    orchestrator = project_declarations()["orchestrator"]["targets"]
    for target in (CODE_SCOPED, DOCS_SCOPED, RECIPE_SCOPED, CHECKOUT_SCOPED):
        assert f"--ignore={SESSION_SETUP_PYPI_ROOT} " in orchestrator[target]["command"], target

    recipes = (REPO_ROOT / "justfile").read_text(encoding="utf-8")
    for recipe in ("test *nx_args", "upgrade"):
        body = re.search(rf"^{re.escape(recipe)}:\n\s+(.+)$", recipes, re.MULTILINE)
        assert body is not None, recipe
        assert re.search(rf"-t [\w,-]*\b{SESSION_SETUP_PYPI_SCOPED}\b", body.group(1)), (
            f"`just {recipe.split()[0]}` is a recipe that runs the PyPI journeys, and it no "
            "longer names them"
        )
    package = sorted(path for path in _tracked() if path.startswith("orchestrator/"))
    for root, target in (
        (SESSION_SETUP_ROOT, SESSION_SETUP_SCOPED),
        (SESSION_SETUP_PYPI_ROOT, SESSION_SETUP_PYPI_SCOPED),
    ):
        globs = _effective_inputs(root, target)
        assert covers(globs, "scripts/session-setup.sh"), root
        keyed = [path for path in package if covers(globs, path)]
        assert keyed == [SESSION_SETUP_ORCHESTRATOR_IMPORT], (
            f"{root} is keyed on {keyed} of orchestrator/; the one module its journeys import "
            f"is {SESSION_SETUP_ORCHESTRATOR_IMPORT}"
        )


def test_the_recipe_key_stays_inside_the_code_key() -> None:
    """A recipe test could otherwise read something the code-key guard permits and
    this tier does not carry."""
    code_globs = _effective_inputs("orchestrator", CODE_SCOPED)
    recipe_globs = _effective_inputs("orchestrator", RECIPE_SCOPED)
    outside = sorted(
        path for path in _tracked() if covers(recipe_globs, path) and not covers(code_globs, path)
    )
    assert not outside, f"the recipe key reaches outside the code key: {outside}"


#: How a journey names one of the suite's shared stand-ins: `project_fixtures.helper`
#: resolves the name under `tests/e2e/`, and a journey that names one executes it —
#: the paid provider's stand-in, or the directory of refusing shims that guards the
#: identities it cannot reach — so the key has to carry it although nothing imports it.
STAND_IN = re.compile(r'\bhelper\(\s*"([^"]+)"\s*\)')
#: Where those stand-ins live, as `project_fixtures.HELPERS` resolves them.
STAND_INS = "tests/e2e"


def _named_files(text: str, tracked: frozenset[str]) -> set[str]:
    """Every tracked *file* ``text`` names as a complete literal path.

    Narrower than :func:`_named_repository_paths` on purpose, in three ways. A literal
    naming a directory — `REPO_ROOT / "scripts"` joined onto a name decided at run time —
    is not a read of everything under it. A bare literal is not counted at all, because
    in a journey it is as often the *content* of what the journey writes — a drafted
    follow-up naming the script it is about — as a path it opens. And a journey's own
    module is the one source read, because a shared helper's literals are a table as often
    as a read: `tests/published_tools.py` names every pin in `config/` and opens none of
    them for a journey that only asks it for a binary's path.
    """
    named: set[str] = set()
    for match in re.finditer(r'(?:REPO_ROOT|ROOT)\s*/\s*"([^"]+)"(\s*/\s*"[^"]+")*', text):
        rooted = "/".join(re.findall(r'"([^"]+)"', match.group(0)))
        if rooted in tracked:
            named.add(rooted)
    return named


def _ask_seam_declarations() -> dict[str, dict]:
    """Every project declared under the ask-seam directory, by the root it sits at."""
    return {
        root: declaration
        for root, declaration in _project_declarations().items()
        if root.startswith(f"{ASK_SEAM_ROOT}/")
    }


def _journey_module(root: str) -> str:
    """The one test module a journey project owns, or a failure naming what it found."""
    modules = sorted(
        str(module.relative_to(REPO_ROOT)) for module in REPO_ROOT.joinpath(root).glob("test_*.py")
    )
    assert len(modules) == 1, (
        f"{root} is one journey's project and owns {modules}; one memoization unit is one "
        "journey, so a second module there is a second project"
    )
    return modules[0]


def _journey_sources(module: str, modules: dict[str, str]) -> set[str]:
    """The journey's module and every `tests/` module it reaches by import.

    `tests/conftest.py` and what it imports are in every journey's process, so they
    are here too — the fixtures it installs run around every test, whatever the test
    imports for itself.
    """
    return (
        {module, "tests/conftest.py"}
        | _imported_suite_modules(module, modules)
        | _imported_suite_modules("tests/conftest.py", modules)
    )


def _stand_ins_named(sources: set[str], tracked: frozenset[str]) -> set[str]:
    """Every tracked path under `tests/` the journey's sources name as a stand-in.

    A symlink in a named tree counts as its target: `tests/e2e/no-paid-provider/` holds
    one link per provider binary, all to `tests/e2e/no_paid_provider.py`, and what a
    fall-through executes is that file.
    """
    named: set[str] = set()
    for source in sources:
        for match in STAND_IN.finditer((REPO_ROOT / source).read_text(encoding="utf-8")):
            rooted = f"{STAND_INS}/{match.group(1)}"
            found = {path for path in tracked if path == rooted or path.startswith(f"{rooted}/")}
            named |= found
            for path in found:
                if (REPO_ROOT / path).is_symlink():
                    target = repository_relative((REPO_ROOT / path).resolve())
                    if target in tracked:
                        named.add(target)
    return named


def test_the_ask_seam_registry_is_the_tree_of_journey_projects() -> None:
    """`ASK_SEAM_JOURNEYS` restates what the tree declares, so this holds the two together.

    A journey added under the directory without a row here would be a project the
    partition check collects and the read guard holds to no key; a row here without a
    project would be a key nothing runs under.
    """
    declared = _ask_seam_declarations()
    assert declared, f"no project is declared under {ASK_SEAM_ROOT}/"
    assert {journey.root for journey in ASK_SEAM_JOURNEYS} == set(declared), (
        f"tests/nx_inputs.py lists {sorted(journey.root for journey in ASK_SEAM_JOURNEYS)} "
        f"while {ASK_SEAM_ROOT}/ declares {sorted(declared)}"
    )
    for journey in ASK_SEAM_JOURNEYS:
        declaration = declared[journey.root]
        assert declaration["name"] == journey.project, (journey, declaration["name"])
        assert set(declaration["targets"]) == {ASK_SEAM_SCOPED}, (
            f"{journey.project} declares {sorted(declaration['targets'])}; one journey is "
            "one memoization unit, and a second target there would be a second key"
        )
        assert declaration["targets"][ASK_SEAM_SCOPED]["inputs"] == [
            journey.key,
            FROM_DEPENDENCIES,
        ], (
            f"{journey.project}:{ASK_SEAM_SCOPED} is keyed on "
            f"{declaration['targets'][ASK_SEAM_SCOPED].get('inputs')}, not on its own "
            f"named input {journey.key} and the test-support units it depends on"
        )
        assert journey.key in declaration.get("namedInputs", {}), (
            f"{journey.root}/project.json declares no {journey.key}"
        )
        # And the target collects exactly the directory the project owns.
        assert f"pytest {journey.root} " in declaration["targets"][ASK_SEAM_SCOPED]["command"]
    assert not (REPO_ROOT / ASK_SEAM_ROOT / "project.json").exists(), (
        f"{ASK_SEAM_ROOT}/project.json is the one shared target this layout replaced; a "
        "project there would collect every journey under one key again"
    )


def test_each_ask_seam_journey_is_keyed_on_what_it_reads_and_nothing_wider() -> None:
    """One expensive journey, one key, and the key reaches only what that journey opens.

    Every journey here spends a real launch, so a path in its key it never reads makes an
    unrelated edit pay for that launch — and a key shared between journeys made every one
    of them pay for a file only one read. Three things are held, in the direction each
    can fail safely. The key is *no wider* than the journey: every glob names one tracked
    file, or one fixture tree of stand-ins under `tests/e2e/`, so no `config/**/*` or
    `scripts/**/*` can carry a whole directory for one file in it; none of it is prose;
    and every `tests/` module it carries is one the journey imports, names as a stand-in,
    or runs under — never a sibling journey's module, never a helper it never opens. And
    the key is *no narrower* than what a static reading can see: the journey's own module,
    every suite module it reaches by import, and every tracked file its own module names
    joined onto the root, because a journey reads, copies or runs what it names. What the
    launch reaches beyond that — the pins, graphs and harness configs the engine opens —
    was measured, as `tests/nx_inputs.py` says, and `tests/conftest.py` holds the
    in-process half of it to the key on every run; a file the key carries that no
    measurement showed is the one drift this cannot see.
    """
    tracked = _tracked()
    modules = _suite_modules()
    for journey in ASK_SEAM_JOURNEYS:
        module = _journey_module(journey.root)
        globs = _effective_inputs(journey.root, ASK_SEAM_SCOPED)
        for glob in globs:
            assert not glob.startswith("!"), (
                f"{journey.key} excludes {glob}; a key of named files has nothing to exclude"
            )
            if "*" in glob:
                tree = glob.removesuffix("/**/*")
                assert (
                    glob.endswith("/**/*") and tree.startswith(f"{STAND_INS}/") and "*" not in tree
                ), (
                    f"{journey.key} carries {glob}: a tree in a journey's key is a fixture of "
                    f"stand-ins under {STAND_INS}/, never a directory of this repository"
                )
                assert any(path.startswith(f"{tree}/") for path in tracked), (
                    f"{glob} covers nothing"
                )
                assert not any(
                    Path(path).name.startswith("test_")
                    for path in tracked
                    if path.startswith(f"{tree}/")
                ), f"{glob} covers test modules, so it is not a tree of stand-ins"
            else:
                assert glob in tracked, f"{journey.key} names {glob}, which git does not track"

        covered = {path for path in tracked if covers(globs, path)}
        # A Markdown file under `config/` is a template a tool reads — the dispatch appendix,
        # the design-document template — not prose about this repository.
        prose = sorted(
            path for path in covered if _is_documentation(path) and not path.startswith("config/")
        )
        assert not prose, f"{journey.key} reaches prose the journey never opens: {prose}"

        sources = _journey_sources(module, modules)
        missing = sorted(sources - covered)
        assert not missing, (
            f"{journey.key} does not carry {missing}, which {module} imports or runs under, "
            "so editing one replays a verdict recorded before it moved"
        )
        literal = _named_files((REPO_ROOT / module).read_text(encoding="utf-8"), tracked)
        unread = sorted(path for path in literal if not covers(globs, path))
        assert not unread, (
            f"{journey.key} does not carry {unread}, which {module} names by path and so "
            "reads, copies or runs"
        )

        # A data file one of those sources opens by its path is read in the journey's own
        # process — the layout document `tests/onejudge_bundle.py` seeds every schema cache
        # from, through `tests/conftest.py` — and is no sibling's module, so the key may
        # carry it. Test modules are still admitted only by import, as above.
        opened = {
            path
            for source in sources
            for path in _named_files((REPO_ROOT / source).read_text(encoding="utf-8"), tracked)
            if not path.endswith(".py")
        }
        allowed = sources | _stand_ins_named(sources, tracked) | opened
        surplus = sorted(
            path for path in covered if path.startswith("tests/") and path not in allowed
        )
        assert not surplus, (
            f"{journey.key} reaches {surplus}, which {module} neither imports, names as a "
            "stand-in, nor runs under — a sibling's module or a helper it never opens, and "
            "an edit there would pay for this journey's launch"
        )


def test_the_nx_cache_check_key_covers_every_repository_path_that_check_reads() -> None:
    """`just check` replays this check, so its key has to carry what it opens.

    The check builds two linked worktrees out of the fixture and drives the real
    `scripts/nx.sh` in both. Every one of those inputs is named in the script as
    `$root/<path>`, which is what makes the reconciliation mechanical rather than a
    reviewer's reading of a shell script.
    """
    assert _nx_config()["targetDefaults"]["check-nx-cache"]["inputs"] == [NX_CACHE_CHECK], (
        "the check has to be keyed on its own named input, or the reconciliation below "
        "is checking globs the target does not actually hash"
    )

    script = "scripts/check-nx-cache.sh"
    text = (REPO_ROOT / script).read_text(encoding="utf-8")
    named = {match.group(1) for match in re.finditer(r'"\$root/([^"]+)"', text)}
    assert {"package.json", "scripts/nx.sh", "tests/fixtures/nx-cache"} <= named, named

    tracked = _tracked()
    read = {script}
    for candidate in named:
        if candidate in tracked:
            read.add(candidate)
        else:
            read |= {path for path in tracked if path.startswith(f"{candidate}/")}

    globs = _effective_inputs("", "check-nx-cache")
    uncovered = sorted(path for path in read if not covers(globs, path))
    assert not uncovered, (
        "workspace:check-nx-cache reads these repository paths but is not keyed on "
        f"them, so a change to one replays a stale verdict: {uncovered}"
    )
    # Narrow on purpose: a key that had quietly widened to the whole workspace would
    # pass the assertion above and replay nothing.
    assert not covers(globs, "AGENTS.md")
    assert not covers(globs, "orchestrator/labels.py")


def test_the_cache_fixture_ignores_what_the_wrapper_chain_writes_into_it() -> None:
    """The fixture's cross-worktree hit must not turn on what a wrapper wrote first.

    `scripts/nx.sh` provisions before it reaches Nx, and provisioning writes a
    preserved log into the tree it is provisioning. Bun's carries its own
    millisecond stamp, so two worktrees' copies of it differ — and Nx hashes an
    untracked file nothing ignores. Measured in the fixture: one differing file
    under that directory took a 100% cache hit to 0%. So the fixture has to ignore
    the state this repository ignores, or the contract it proves holds only while
    no wrapper writes anything on its way in.
    """
    preserved = (REPO_ROOT / "scripts/preserved-log.sh").read_text(encoding="utf-8")
    directory = re.search(r'dir="\$root/([^"]+)"', preserved)
    assert directory is not None, (
        "scripts/preserved-log.sh no longer names the directory it writes into as "
        '`dir="$root/<name>"`; this gate reads it from there rather than restating it'
    )
    rule = f"/{directory.group(1)}/"

    rules = REPO_ROOT / "tests/fixtures/nx-cache/.gitignore"
    assert rules.is_file(), f"{rules} is gone, so the fixture ignores nothing a wrapper writes"
    ignored = rules.read_text(encoding="utf-8")

    assert rule in ignored.splitlines(), (
        f"the cache fixture does not ignore {rule}, which every `scripts/nx.sh` in it "
        "writes into before Nx hashes anything"
    )
