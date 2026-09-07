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
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

from conftest import READS_CHECKOUTS_MARKER, READS_DOCS_MARKER, READS_RECIPES_MARKER
from nx_inputs import (
    ASK_SEAM_ROOT,
    ASK_SEAM_SCOPED,
    CHECKOUT_SCOPED,
    CODE_SCOPED,
    CODE_WORKSPACE,
    COVERAGE_SCOPED,
    DAG_UI_ROOT,
    DAG_UI_SCOPED,
    DOCS_SCOPED,
    NX_CACHE_CHECK,
    PLAN_STORE_INSTALL_ROOT,
    PLAN_STORE_INSTALL_SCOPED,
    PLAN_TOOLING_DOCS_SCOPED,
    PLAN_TOOLING_PROJECT,
    PLAN_TOOLING_ROOT,
    PLAN_TOOLING_SCOPED,
    RECIPE_SCOPED,
    RECIPE_WORKSPACE,
    SELECTED_TARGETS,
    UNCONDITIONAL_TARGETS,
    covers,
    named_input_globs,
    repository_relative_globs,
    resolve_input_globs,
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
    config = _nx_config()
    project_file = REPO_ROOT / project_root / "project.json" if project_root else None
    project = json.loads((project_file or REPO_ROOT / "project.json").read_text(encoding="utf-8"))
    declared = (
        project["targets"][target].get("inputs") or config["targetDefaults"][target]["inputs"]
    )
    return repository_relative_globs(
        resolve_input_globs(declared, config["namedInputs"]), project_root=project_root
    )


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
    registered = re.findall(r'^\s*"(\w+):', manifest, flags=re.MULTILINE)
    for marker in (READS_DOCS_MARKER, READS_RECIPES_MARKER, READS_CHECKOUTS_MARKER):
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
    """The worker count and distribution are one contract, written in seven places.

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
    types it. `asked` in `tests/ask_seam/test_ask_manager_e2e.py` is function-scoped and
    spends a real `just orchestrate` for every test that names it, refusal journeys
    included; the launch fixtures in `tests/ask_seam/test_launch_ask_seam_e2e.py` are
    module-scoped, so they run once per worker that receives *any* test from there. In
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
    # journeys now span three test projects. Still the `_e2e` naming rather than every
    # test module, because a unit test that *reads* `session-setup.sh` runs none of it.
    for module in sorted(REPO_ROOT.joinpath("tests").rglob("test_*_e2e.py")):
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

    `repository_credentials_file` in `tests/ask_seam/test_launch_ask_seam_e2e.py` adds a
    name to this checkout's own `.env` and puts the original bytes back afterwards. It is
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
    selects by path as well: the `plan-tooling` and `ask-seam` projects each name a
    directory and every orchestrator tier ignores both. A partition derived from
    the markers alone would report the orchestrator tiers covering the suite while the
    directories the host-tool projects own were collected by nobody.
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
    return {line.strip() for line in collected.stdout.splitlines() if "::" in line}


#: Every tier that runs part of this suite, as the file and target that declares it.
#: Nine, across five projects, and every one of them is a target of the project whose
#: directory holds the tests it collects: the `plan-tooling` project owns the host-tool
#: journeys over the plan surface in two targets — one keyed on what they read, one on
#: the whole workspace for the journeys that copy this checkout — the `ask-seam` project
#: owns the host-tool journeys over the ask seam in one, the `dag-ui` project owns the
#: journeys that render the Observatory in a real browser in one, the
#: `plan-store-install` project owns the journeys that race real installs for a real lock
#: in one, and the orchestrator project owns the rest in four.
SUITE_TIERS = (
    (f"{PLAN_TOOLING_ROOT}/project.json", PLAN_TOOLING_SCOPED),
    (f"{PLAN_TOOLING_ROOT}/project.json", PLAN_TOOLING_DOCS_SCOPED),
    (f"{ASK_SEAM_ROOT}/project.json", ASK_SEAM_SCOPED),
    (f"{DAG_UI_ROOT}/project.json", DAG_UI_SCOPED),
    (f"{PLAN_STORE_INSTALL_ROOT}/project.json", PLAN_STORE_INSTALL_SCOPED),
    ("orchestrator/project.json", CODE_SCOPED),
    ("orchestrator/project.json", DOCS_SCOPED),
    ("orchestrator/project.json", RECIPE_SCOPED),
    ("orchestrator/project.json", CHECKOUT_SCOPED),
)


def test_every_tier_of_the_suite_partitions_it_between_them() -> None:
    """Nine selections, one suite: no test may be collected twice or not at all.

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


def test_the_recipe_key_covers_every_module_that_routes_a_test_into_it() -> None:
    """A recipe-tier test in an unkeyed file would replay a verdict predating it.

    The tier's whole claim is that a commit touching none of `justfile`,
    `scripts/`, `package.json`, `bun.lock`, or the fixtures may replay its verdict.
    That is only true while the modules *defining* those tests are in the key too —
    a marker moved into a file the key does not carry, or a helper imported from
    one, is a test that can change its own answer without changing its hash.
    """
    globs = _effective_inputs("orchestrator", RECIPE_SCOPED)
    modules = _suite_modules()
    declaring = sorted(
        str(source.relative_to(REPO_ROOT))
        for source in sorted(REPO_ROOT.joinpath("tests").rglob("test_*.py"))
        if f"pytest.mark.{READS_RECIPES_MARKER}" in source.read_text(encoding="utf-8")
    )
    assert declaring, f"nothing declares {READS_RECIPES_MARKER}; the tier would run empty"

    needed = {"tests/conftest.py", *declaring}
    for module in declaring:
        needed |= _imported_suite_modules(module, modules)
    needed |= _imported_suite_modules("tests/conftest.py", modules)
    uncovered = sorted(path for path in needed if not covers(globs, path))
    assert not uncovered, (
        f"orchestrator:{RECIPE_SCOPED} collects these modules but is not keyed on them, "
        f"so editing one replays a stale verdict: {uncovered}"
    )

    # And the narrower key must stay inside the wider one, or a recipe test could
    # read something the code-key guard permits and this tier does not carry.
    code_globs = named_input_globs(CODE_WORKSPACE)
    outside = sorted(
        path
        for path in _tracked()
        if covers(named_input_globs(RECIPE_WORKSPACE), path) and not covers(code_globs, path)
    )
    assert not outside, f"the recipe key reaches outside the code key: {outside}"


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
