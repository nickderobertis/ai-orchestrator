"""A cached verdict may only stand in for a verdict on the tree it was keyed on.

Every cached Nx target memoizes an answer. Replaying one is sound exactly when the
key covers everything the check reads, and unsound the moment it does not: a file
the check reads but the key ignores is a tree that can change its answer without
changing its hash, which is a green verdict for a tree that would have failed.

How Nx replays a key is Nx's own behaviour; these assertions read the declarations
that behaviour rests on and keep them from narrowing again — including as the suite
grows new reads, which is how the subset drifted out of date in the first place.

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
import tempfile
from pathlib import Path
from unittest import mock

import nx_inputs
import pytest
from conftest import (
    OWNED_PROJECTS,
    READS_CHECKOUTS_MARKER,
    READS_DOCS_MARKER,
    READS_RECIPES_MARKER,
    REAL_PLAN_STORE_ROOTS_MARKER,
)
from nx_inputs import (
    ASK_SEAM_JOURNEYS,
    ASK_SEAM_ROOT,
    ASK_SEAM_SCOPED,
    BOARD_COPY_APPROVAL_HOST_SCOPED,
    BOARD_COPY_APPROVAL_ROOT,
    BOARD_COPY_APPROVAL_SCOPED,
    BUDGET_HOST_SCOPED,
    BUDGET_SCOPED,
    CHECKOUT_SCOPED,
    CODE_SCOPED,
    CODE_WORKSPACE,
    COVERAGE_SCOPED,
    DAG_UI_ROOT,
    DAG_UI_SCOPED,
    DOCS_SCOPED,
    DRIFT_GATE_PROJECTS,
    E2E_JOURNEYS,
    E2E_ROOT,
    FROM_DEPENDENCIES,
    GRACEFUL_CANCEL_ROOT,
    GRACEFUL_CANCEL_SCOPED,
    HOST_SWEEP_ROOT,
    HOST_SWEEP_SCOPED,
    HOST_VIEWS_ROOT,
    HOST_VIEWS_SCOPED,
    JOURNEYS_THE_UNIT_GATES_READ,
    MANAGER_ALLOWLIST_ROOT,
    MANAGER_ALLOWLIST_SCOPED,
    MARKER_TIER_ROOT,
    MERGE_POLICY_ROOT,
    MERGE_POLICY_SCOPED,
    PLAN_STORE_ASSETS_ROOT,
    PLAN_STORE_ASSETS_SCOPED,
    PLAN_STORE_DEPENDENCY_READ_ROOT,
    PLAN_STORE_DEPENDENCY_READ_SCOPED,
    PLAN_TOOLING_DOCS_SCOPED,
    PLAN_TOOLING_PROJECTS,
    PLAN_TOOLING_ROOT,
    PLAN_TOOLING_SCOPED,
    PRE_PUSH_PROVISIONING_ROOT,
    PRE_PUSH_PROVISIONING_SCOPED,
    PROJECT_STORE_RACE_ROOT,
    PROJECT_STORE_RACE_SCOPED,
    RECIPE_SCOPED,
    RENDER_DIAGRAM_ROOT,
    RENDER_DIAGRAM_SCOPED,
    RUN_END_HOOKS_ROOT,
    RUN_END_HOOKS_SCOPED,
    SCHEMA_CACHE_ROOT,
    SCHEMA_CACHE_SCOPED,
    SESSION_OPEN_CONFLICT_ROOT,
    SESSION_OPEN_CONFLICT_SCOPED,
    SESSION_SETUP_PYPI_ROOT,
    SESSION_SETUP_PYPI_SCOPED,
    SESSION_SETUP_ROOT,
    SESSION_SETUP_SCOPED,
    SUPPORT_ROOT,
    TEST_SUPPORT,
    UNFINISHED_HOST_SCOPED,
    UNFINISHED_ROOT,
    UNFINISHED_SCOPED,
    UNIT_COVERAGE_DATA,
    UNIT_WORKSPACE,
    UNPUBLISHED_VIEW_ROOT,
    UNPUBLISHED_VIEW_SCOPED,
    UNWATCHED_ROOT,
    UNWATCHED_SCOPED,
    WORKSPACE_COPIES_PROJECT,
    WRITEBACK_BUDGET_ROOT,
    WRITEBACK_BUDGET_SCOPED,
    catalogued_roots,
    coverage_data,
    covers,
    declaration_path,
    dependency_roots,
    matches,
    named_input_globs,
    project_declarations,
    project_root,
    repository_relative,
    target_input_globs,
)

from orchestrator.root import REPO_ROOT

WHOLE_WORKSPACE = "wholeWorkspace"
#: Every directory-owned project of a family split by subject, and the directory each
#: family sits under, which every orchestrator tier ignores.
SUBJECT_PROJECTS = (*PLAN_TOOLING_PROJECTS, *DRIFT_GATE_PROJECTS)
SUBJECT_ROOTS = (PLAN_TOOLING_ROOT, *(subject.root for subject in DRIFT_GATE_PROJECTS))
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

# Every test module of the suite is this gate's subject — it reads and collects the e2e
# journeys as well as the unit modules — so it runs in `tree-scans:test-docs`, keyed on the
# whole workspace: no narrower key covers every test project's modules, and a journey edit
# has to re-run the gate that holds it.
# llmlint: ignore[test_tiers_split_by_project_not_by_marker] The marker names the key this
# gate reads — every module of the suite — rather than a cost tier; no project could own a
# gate whose subject is every project's tests.
pytestmark = pytest.mark.reads_docs


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
    # A subject project's whole-workspace target is the same claim about another project:
    # the tests it collects copy this checkout or scan every tracked document, so what they
    # read is everything git tracks and no narrower key could describe their verdict. And it
    # is the only target of such a project keyed on the whole workspace.
    for subject in SUBJECT_PROJECTS:
        targets = project_declarations()[subject.root]["targets"]
        assert (PLAN_TOOLING_DOCS_SCOPED in targets) == subject.docs_tier, (
            subject.project,
            sorted(targets),
        )
        if subject.docs_tier:
            assert targets[PLAN_TOOLING_DOCS_SCOPED]["inputs"] == [WHOLE_WORKSPACE], (
                f"{subject.project}:{PLAN_TOOLING_DOCS_SCOPED} collects the tests that copy "
                "this checkout, so its cached verdict must be keyed on the whole workspace"
            )
        assert not [
            target
            for target, spec in targets.items()
            if target != PLAN_TOOLING_DOCS_SCOPED and WHOLE_WORKSPACE in spec.get("inputs", [])
        ], f"{subject.project} keys a narrow target on {WHOLE_WORKSPACE}"
    # The journeys are their own projects' and never the orchestrator project's, so its
    # whole-workspace tier ignores their directory; and the one journey project keying a
    # target on the whole workspace is the one whose journeys copy this checkout, in its
    # own `test-docs` target. A whole-workspace key anywhere else in them would select
    # that project for every diff, which is what splitting the journeys exists to avoid.
    assert f"--ignore={E2E_ROOT} " in project["targets"][DOCS_SCOPED]["command"]
    for journey in E2E_JOURNEYS:
        targets = project_declarations()[journey.root]["targets"]
        whole = sorted(
            target
            for target, spec in targets.items()
            if WHOLE_WORKSPACE in spec.get("inputs", [])
            or (target == DOCS_SCOPED and "inputs" not in spec)
        )
        expected = [DOCS_SCOPED] if journey.project == WORKSPACE_COPIES_PROJECT else []
        assert whole == expected, (
            f"{journey.project} keys {whole} on the whole workspace; only the `test-docs` "
            f"tier of {WORKSPACE_COPIES_PROJECT}, whose journeys copy this checkout, may be"
        )
        if DOCS_SCOPED in targets:
            assert targets[DOCS_SCOPED]["inputs"] == [WHOLE_WORKSPACE], journey.project
    llmlint = _nx_config()["targetDefaults"]["lint-llm-diff"]["inputs"]
    assert llmlint[0] == WHOLE_WORKSPACE, (
        "the llmlint tier judges the whole workspace diff and shares this one key"
    )


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


def test_the_marker_that_routes_a_test_to_its_tier_means_the_same_thing_everywhere() -> None:
    """Reconcile the places the marker name is independently written down.

    Each marker names a routing decision, not a label: pytest registers it,
    `conftest.py` enforces it, and the Nx targets select on it. Those declarations
    are written separately and nothing else compares them, so a rename that missed
    one would leave a tier silently selecting nothing — and a `test` tier that ran
    the prose contracts anyway, keyed on a workspace without prose, is the false
    green this whole file exists to prevent.

    A marker routes a test between the targets of the project whose directory holds
    it, and never out of that project — save a `reads_docs` journey directly under
    `tests/e2e/`, which `orchestrator:test-docs` collects, as the assertions below on the
    two marker-routed projects state: which project owns a test is decided by where it
    lives, so the cost of running it is charged to the code `nx affected` would
    select for it. So every orchestrator tier ignores the directories the subject
    projects own, and a subject project with a whole-workspace `test-docs` target selects
    between its own two keys with `reads_docs`, a marker the orchestrator project uses
    among its four.
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

    # The same four markers route a unit or drift-gate test between the orchestrator
    # project's four targets.
    targets = json.loads(
        (REPO_ROOT / MARKER_TIER_ROOT / "project.json").read_text(encoding="utf-8")
    )["targets"]
    # Every code-keyed selector has to exclude both narrower tiers, not merely the first
    # one written down: a tier that dropped one of them would run those tests twice and
    # key the second run on a tree they do not read.
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
        assert f"-m {marker}" in targets[target]["command"], target
        marked = re.search(r"-m '?(not )?(\w+)'?", targets[target]["command"])
        assert marked is not None and marked.group(1) is None

    # Which project owns a journey is where it lives: every orchestrator tier ignores the
    # journeys' directory, or names only modules outside it, so none charges their cost to
    # its own key. The recipe tier names the modules holding its marked tests, so that what
    # it imports is what its key carries.
    for target in (RECIPE_SCOPED, CHECKOUT_SCOPED, DOCS_SCOPED, *CODE_KEYED):
        assert not _collects_under(targets[target]["command"], E2E_ROOT), (
            f"orchestrator:{target} collects {E2E_ROOT}, whose journeys are their own projects'"
        )

    # Inside a journey project a marker chooses between that project's own targets, and
    # only a marker that project has a target for: its `test` deselects exactly the
    # markers its other targets collect, each of those collects its marker alone, and
    # every target collects the project's own directory.
    for journey in E2E_JOURNEYS:
        declared = project_declarations()[journey.root]["targets"]
        routed = [
            marker
            for marker, target in (
                (READS_DOCS_MARKER, DOCS_SCOPED),
                (READS_CHECKOUTS_MARKER, CHECKOUT_SCOPED),
            )
            if target in declared
        ]
        selector = re.findall(r"-m '([^']+)'", declared[CODE_SCOPED]["command"])
        assert selector == (
            [" and ".join(f"not {marker}" for marker in routed)] if routed else []
        ), (
            journey.project,
            selector,
        )
        for marker, target in (
            (READS_DOCS_MARKER, DOCS_SCOPED),
            (READS_CHECKOUTS_MARKER, CHECKOUT_SCOPED),
        ):
            if target in declared:
                assert f"-m {marker} " in declared[target]["command"], (journey.project, target)
        assert set(declared) <= {CODE_SCOPED, DOCS_SCOPED, CHECKOUT_SCOPED}, journey.project
        # A family collects its own directory; the root project, whose directory holds the
        # families too, names each of its own modules instead.
        collects = (
            [f"pytest {' '.join(_journey_modules(journey.root))} "]
            if journey.root == E2E_ROOT
            else [f"pytest {journey.root} "]
        )
        for target, spec in declared.items():
            assert all(part in spec["command"] for part in collects), (journey.project, target)

    # The same marker routes inside each subject project that has a whole-workspace
    # target, and there it chooses between that project's own two targets rather than handing a
    # test to another project's tier. Both halves are asserted together because only
    # the pair partitions: a project whose narrow target deselects a marker no target
    # of its own collects has stopped running those tests, and one whose targets both
    # collect them runs them twice against two different keys.
    # A subject project with no whole-workspace target selects on no marker at all: it
    # collects its whole directory under its one key.
    for subject in SUBJECT_PROJECTS:
        host_tools = project_declarations()[subject.root]["targets"]
        for target in _test_tiers(subject.root):
            assert f"pytest {subject.root} " in host_tools[target]["command"], (
                subject.project,
                target,
            )
        if not subject.docs_tier:
            assert " -m " not in host_tools[subject.target]["command"], subject.project
        elif subject.target == PLAN_TOOLING_SCOPED:
            assert f"-m 'not {READS_DOCS_MARKER}'" in host_tools[PLAN_TOOLING_SCOPED]["command"]
            assert f"-m {READS_DOCS_MARKER}" in host_tools[PLAN_TOOLING_DOCS_SCOPED]["command"]
        else:
            assert set(host_tools) == {PLAN_TOOLING_DOCS_SCOPED}, (subject.project, host_tools)
            assert f"-m {READS_DOCS_MARKER}" in host_tools[PLAN_TOOLING_DOCS_SCOPED]["command"]
    # And no other project may collect those directories, or their cost is charged to a key
    # that does not describe it.
    targets = json.loads((REPO_ROOT / "orchestrator/project.json").read_text(encoding="utf-8"))[
        "targets"
    ]
    for target in (DOCS_SCOPED, RECIPE_SCOPED, CHECKOUT_SCOPED, *CODE_KEYED):
        for owned in SUBJECT_ROOTS:
            assert not _collects_under(targets[target]["command"], owned), (
                f"orchestrator:{target} collects {owned}, which a project of its own owns"
            )


def _collects_under(command: str, root: str) -> bool:
    """Whether a tier's command collects anything under ``root``.

    A tier naming modules or directories collects exactly those; one naming none collects
    the suite, less each directory it ignores.
    """
    selection = _selection(command)
    paths = [
        argument
        for index, argument in enumerate(selection)
        if not argument.startswith("-") and (index == 0 or selection[index - 1] != "-m")
    ]
    if not paths:
        return f"--ignore={root} " not in command
    return any(
        path == root or path.startswith(f"{root}/") or root.startswith(f"{path.rstrip('/')}/")
        for path in paths
    )


def _test_tiers(root: str) -> list[str]:
    """The targets of the subject project at ``root`` that run pytest.

    A subject project holding a `budgets.yaml` declares the two budget targets beside its
    tiers; they run onebudgetspec over what a tier recorded, collect no test, and are held
    with their file by `tests/test_budgets_files.py`.
    """
    return [
        target
        for target in project_declarations()[root]["targets"]
        if target not in (BUDGET_SCOPED, BUDGET_HOST_SCOPED)
    ]


#: Every place the parallel worker contract is independently written down. It is a
#: contract because the number was chosen by measurement against this host — see
#: `docs/repo-lifecycle.md` — and a recipe that quietly drifted to a different one
#: would stop being evidence for the tier the gate actually runs.
PARALLEL_SITES = (
    *(
        (f"{subject.root}/project.json", target)
        for subject in SUBJECT_PROJECTS
        for target in sorted(_test_tiers(subject.root))
    ),
    (f"{SESSION_SETUP_PYPI_ROOT}/project.json", SESSION_SETUP_PYPI_SCOPED),
    ("orchestrator/project.json", CODE_SCOPED),
    ("orchestrator/project.json", DOCS_SCOPED),
    ("orchestrator/project.json", RECIPE_SCOPED),
    ("orchestrator/project.json", CHECKOUT_SCOPED),
    *(
        (f"{journey.root}/project.json", target)
        for journey in E2E_JOURNEYS
        for target in project_declarations()[journey.root]["targets"]
    ),
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
            rf"^{re.escape(target)}:(?:[ \t]+[A-Za-z_][\w-]*)*[ \t]*\n((?:[ \t]+.*\n?)+)",
            (REPO_ROOT / path).read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
        assert recipe is not None, f"no {target} recipe in {path}"
        text = recipe.group(1)
    return re.findall(r"-n (\d+) --dist (\w+)", text)


def test_every_parallel_declaration_names_the_same_worker_contract() -> None:
    """The worker count and distribution are one contract, written in eleven places.

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


PARALLELISM_INVARIANT = re.compile(
    r"\*\*The gate runs (\w+) Nx tasks at once, each across (\w+) xdist workers\*\*"
)
NUMBER_WORDS = dict(
    zip(
        ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"),
        range(1, 11),
        strict=True,
    )
)


def test_agents_md_states_the_parallelism_the_gate_runs() -> None:
    """AGENTS.md names the gate's Nx slots and xdist width, so both are held to it.

    The slot count was chosen by measurement and kept on the manager's ruling, and
    the sentence that says so is what a later change has to answer; a figure there
    that no longer matched `nx.json` or the worker contract would argue for a setting
    the gate no longer runs.
    """
    stated = PARALLELISM_INVARIANT.search((REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8"))
    assert stated is not None, "AGENTS.md no longer states the gate's parallelism invariant"
    slots, workers = (NUMBER_WORDS.get(word) for word in stated.groups())

    configured = json.loads((REPO_ROOT / "nx.json").read_text(encoding="utf-8")).get("parallel")
    assert slots == configured, (
        f"AGENTS.md says the gate runs {stated[1]} Nx tasks at once, but nx.json's "
        f"`parallel` is {configured}; change both together"
    )
    widths = {int(n) for site in PARALLEL_SITES for n, _ in _worker_contracts(*site)}
    assert widths == {workers}, (
        f"AGENTS.md says each Nx task runs across {stated[2]} xdist workers, but the "
        f"test targets declare -n {sorted(widths)}; change both together"
    )


#: A pytest plugin that records, for each *collected* test, the xdist group it resolves
#: to. Read from a collection rather than from the source, because a group is what an
#: item carries rather than how it is spelled: a decorator, a module `pytestmark`, and a
#: constant one file assigns from another all arrive here identically, and a scan for any
#: one spelling would pass a suite that had drifted into the others.
COLLECTION_DUMP_PLUGIN = """
import json
import os


def pytest_collection_modifyitems(session, config, items):
    with open(os.environ["ORCHESTRATOR_COLLECTION_LOG"], "w", encoding="utf-8") as log:
        for item in items:
            mark = item.get_closest_marker("xdist_group")
            log.write(json.dumps({
                "node": item.nodeid,
                "group": mark.args[0] if mark is not None and mark.args else "",
            }) + "\\n")
"""

#: Where that plugin writes what it saw.
COLLECTION_LOG_ENV = "ORCHESTRATOR_COLLECTION_LOG"


@functools.cache
def _collected_items() -> dict[str, str]:
    """Every collected test id, and the xdist group it resolves to — from one real collection."""
    with tempfile.TemporaryDirectory() as scratch:
        plugins = Path(scratch)
        (plugins / "collection_dump.py").write_text(COLLECTION_DUMP_PLUGIN, encoding="utf-8")
        log = plugins / "collected.jsonl"
        collected = subprocess.run(
            ["uv", "run", "pytest", "--collect-only", "--no-cov", "-p", "collection_dump"],
            cwd=REPO_ROOT,
            env={**os.environ, "PYTHONPATH": str(plugins), COLLECTION_LOG_ENV: str(log)},
            text=True,
            capture_output=True,
            check=False,
        )
        assert collected.returncode == 0, collected.stdout + collected.stderr
        recorded = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return {row["node"]: row["group"] for row in recorded}


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


def test_a_module_whose_setup_writes_this_checkout_lands_on_one_worker() -> None:
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

    The group is the module's own, named for it and carried by no other module: it
    serialises one fixture's write to one path, and protects no toolchain.
    """
    writers = _module_scope_checkout_writers()
    assert writers, (
        "no journey module declares an autouse module-scoped fixture that touches "
        f"{SHARED_CHECKOUT_ROOT}; this scan is looking for the shape that forces a "
        "module onto one worker, and finding none means it has stopped matching"
    )

    groups = _collected_items()
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
        elsewhere = sorted(
            node for node, group in groups.items() if group in named and f"/{module}::" not in node
        )
        assert not elsewhere, (
            f"{module}'s group {named} is carried by other modules too, so it schedules "
            f"them beside {fixture} rather than naming one module's fixture: {elsewhere}"
        )


#: The flags that decide how a tier *runs* rather than what it collects. Dropped before
#: a target's own arguments are replayed as a collection, because `-n 4` and a coverage
#: report change nothing about which tests a selector names.
_RUNNER_FLAGS = frozenset({"-n", "--dist"})


def _selection(command: str) -> list[str]:
    """The arguments that decide what one tier collects, from its real command.

    Taken from the command rather than from its `-m` expression alone, because a tier selects
    by path as well: each `plan-tooling-*` and drift-gate project and each ask-seam journey's
    project name a directory and every orchestrator tier ignores them all. A partition derived
    from the markers alone would report the orchestrator tiers covering the suite while the
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
#: directory holds the tests it collects: the `plan-tooling-*` projects own the host-tool
#: journeys over the plan surface, one per subject under `tests/plan_tooling/`, each in a
#: target keyed on what its journeys read and, where some copy this checkout, a second on
#: the whole workspace; the drift-gate projects own the root `reads_docs` gates by subject,
#: each keyed on the documents and code it reconciles; each ask-seam journey's
#: project owns that one journey in one target, the `dag-ui` project owns the
#: journeys over this repository's composition of the Observatory in one, the
#: `session-setup` project owns the journey over this checkout's own provisioning in
#: one, the `host-sweep` project owns the journeys over the host sweep session setup
#: starts detached in one, the `session-setup-pypi` project owns the journeys that
#: install the published tools from PyPI in one, the
#: `unwatched` project owns the journeys over the verb a `Stop` hook reads and the hook
#: itself in one, the `merge-policy` project owns the journeys that hold
#: the restated `merge_policy` vocabulary to the launcher in one, the `writeback-budget`
#: project owns the journeys that hold the adopted engine's write-back to targeted
#: updates and one board item per lineage in one, the `run-end-hooks` project owns the
#: journey that fires the run-end hooks through a real launch in one, the
#: `project-store-race` project owns the clock-bounded replacement race over the record
#: store in one, the `schema-cache` project owns the journeys over the one schema cache a
#: session warms in one, the `plan-store-assets` project owns the journey over a document's image
#: assets in one, the `render-diagram` project owns the journeys over `just render-diagram`
#: in one, the `unpublished-view` project owns the
#: journey over `just unpublished` in one, the `unfinished` project owns the journeys over
#: `just unfinished` in one, the `graceful-cancel` project owns the journey over a cancel
#: whose dispatch ends inside its grace period in one, the `manager-allowlist` project owns the
#: journeys over `just sync-allowlist` and `just probe-allowlist` in one, the `host-views`
#: project owns the journeys
#: over `just status` and `just host` in one, the `pre-push-provisioning` project owns the
#: journey over the tracked `pre-push` hook's provisioning in one, the
#: `session-open-conflict` project owns the journey over a continued branch's conflict with
#: its moved base in one, each project in
#: `E2E_JOURNEYS` owns one family of the journeys under `tests/e2e/` — in a `test` target,
#: an uncached `test-checkouts` one where its journeys read another repository's checkout,
#: and a whole-workspace `test-docs` one where they copy this checkout — and the
#: orchestrator project owns the unit modules in four — the fourth, `test-docs`, keyed on
#: the whole workspace for the one drift gate left directly under `tests/`,
#: `tests/test_dispatch_appendix.py`.
SUITE_TIERS = (
    *(
        (f"{subject.root}/project.json", target)
        for subject in SUBJECT_PROJECTS
        for target in sorted(_test_tiers(subject.root))
    ),
    (f"{SESSION_SETUP_ROOT}/project.json", SESSION_SETUP_SCOPED),
    # llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This is the repository's one
    # catalog of test projects, where every project has its entry; the pre-push-provisioning
    # entry sits beside the others' because the catalog is the domain.
    (f"{PRE_PUSH_PROVISIONING_ROOT}/project.json", PRE_PUSH_PROVISIONING_SCOPED),
    # llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]
    # llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This is the repository's one
    # catalog of test projects, where every project has its entry, and
    # `tests/tree_scans/test_nx_cache_scope.py` fails when a project's tests fall outside
    # the tiers it describes; the host-sweep entry sits beside the others' because the
    # catalog is the domain.
    (f"{HOST_SWEEP_ROOT}/project.json", HOST_SWEEP_SCOPED),
    # llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]
    (f"{SESSION_SETUP_PYPI_ROOT}/project.json", SESSION_SETUP_PYPI_SCOPED),
    *((f"{journey.root}/project.json", ASK_SEAM_SCOPED) for journey in ASK_SEAM_JOURNEYS),
    (f"{DAG_UI_ROOT}/project.json", DAG_UI_SCOPED),
    (f"{UNWATCHED_ROOT}/project.json", UNWATCHED_SCOPED),
    (f"{MERGE_POLICY_ROOT}/project.json", MERGE_POLICY_SCOPED),
    (f"{WRITEBACK_BUDGET_ROOT}/project.json", WRITEBACK_BUDGET_SCOPED),
    (f"{RUN_END_HOOKS_ROOT}/project.json", RUN_END_HOOKS_SCOPED),
    (f"{PROJECT_STORE_RACE_ROOT}/project.json", PROJECT_STORE_RACE_SCOPED),
    # llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This is the repository's one
    # catalog of test projects, where every project has its entry, and
    # `tests/tree_scans/test_nx_cache_scope.py` fails when a project's tests fall outside the tiers
    # it describes; the schema-cache entry sits beside the others' because the catalog is the
    # domain.
    (f"{SCHEMA_CACHE_ROOT}/project.json", SCHEMA_CACHE_SCOPED),
    # llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]
    # llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This is the repository's one
    # catalog of test projects, where every project has its entry, and
    # `tests/tree_scans/test_nx_cache_scope.py` fails when a project's tests fall outside
    # the tiers it describes; the plan-store-assets, plan-store-dependency-read and
    # render-diagram entries sit beside the others' rather than in a domain of their own
    # because the catalog is the domain.
    (f"{PLAN_STORE_ASSETS_ROOT}/project.json", PLAN_STORE_ASSETS_SCOPED),
    (f"{PLAN_STORE_DEPENDENCY_READ_ROOT}/project.json", PLAN_STORE_DEPENDENCY_READ_SCOPED),
    (f"{RENDER_DIAGRAM_ROOT}/project.json", RENDER_DIAGRAM_SCOPED),
    # llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]
    (f"{UNPUBLISHED_VIEW_ROOT}/project.json", UNPUBLISHED_VIEW_SCOPED),
    (f"{UNFINISHED_ROOT}/project.json", UNFINISHED_SCOPED),
    # llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This is the repository's one
    # catalog of test projects, where every project has its entry, and
    # `tests/tree_scans/test_nx_cache_scope.py` fails when a project's tests fall outside
    # the tiers it describes; the unfinished project's timed tier sits beside its other
    # because the catalog is the domain.
    (f"{UNFINISHED_ROOT}/project.json", UNFINISHED_HOST_SCOPED),
    # llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]
    # llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This is the repository's one
    # catalog of test projects, where every project has its entry, and
    # `tests/tree_scans/test_nx_cache_scope.py` fails when a project's tests fall outside
    # the tiers it describes; the board-copy-approval entry sits beside the others' because
    # the catalog is the domain.
    (f"{BOARD_COPY_APPROVAL_ROOT}/project.json", BOARD_COPY_APPROVAL_SCOPED),
    (f"{BOARD_COPY_APPROVAL_ROOT}/project.json", BOARD_COPY_APPROVAL_HOST_SCOPED),
    # llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]
    # llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This is the repository's one
    # catalog of test projects, where every project has its entry, and
    # `tests/tree_scans/test_nx_cache_scope.py` fails when a project's tests fall outside
    # the tiers it describes; the graceful-cancel entry sits beside the others' because the
    # catalog is the domain.
    (f"{GRACEFUL_CANCEL_ROOT}/project.json", GRACEFUL_CANCEL_SCOPED),
    # llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]
    # llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This is the repository's one
    # catalog of test projects, where every project has its entry, and
    # `tests/tree_scans/test_nx_cache_scope.py` fails when a project's tests fall outside
    # the tiers it describes; the manager-allowlist entry sits beside the others' rather
    # than in a domain of its own because the catalog is the domain.
    (f"{MANAGER_ALLOWLIST_ROOT}/project.json", MANAGER_ALLOWLIST_SCOPED),
    # llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]
    (f"{HOST_VIEWS_ROOT}/project.json", HOST_VIEWS_SCOPED),
    # llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This is the repository's one
    # catalog of test projects, which this module partitions the whole suite across, so every
    # project's entry sits here; the session-open-conflict entry is one row of it.
    (f"{SESSION_OPEN_CONFLICT_ROOT}/project.json", SESSION_OPEN_CONFLICT_SCOPED),
    # llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]
    ("orchestrator/project.json", CODE_SCOPED),
    ("orchestrator/project.json", DOCS_SCOPED),
    ("orchestrator/project.json", RECIPE_SCOPED),
    ("orchestrator/project.json", CHECKOUT_SCOPED),
    # llmlint: ignore-block[code_lands_in_the_domain_that_owns_it] This is the repository's one
    # catalog of test projects, which this module partitions the whole suite across, so every
    # project's entry sits here; each journey project's tiers are rows of it.
    *(
        (f"{journey.root}/project.json", target)
        for journey in E2E_JOURNEYS
        for target in project_declarations()[journey.root]["targets"]
    ),
    # llmlint: ignore-end[code_lands_in_the_domain_that_owns_it]
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


#: What takes a session's temporary root back out of `tests/pytest_temp_root.py`'s hands:
#: a base named outright, a conftest not loaded or cut off above `tests/`, and the
#: environment pytest and `tempfile` read for where the shared per-user root lives.
_TEMP_ROOT_OVERRIDES = ("--basetemp", "--noconftest", "--confcutdir", "-p no:")
_TEMP_ROOT_VARIABLES = ("PYTEST_DEBUG_TEMPROOT=", "TMPDIR=", "PYTEST_ADDOPTS=")
#: The options of a selection that take their value as the next argument.
_VALUED_OPTIONS = frozenset({"-m", "-k", "-p", "--deselect", "--ignore"})


def test_every_tier_keeps_the_temporary_root_its_conftest_claims() -> None:
    """Every tier's session takes a root of its own rather than the host's shared one.

    The claim is `tests/conftest.py`'s, so a tier reaches it only by running from the
    workspace root and collecting under `tests/` — naming paths there, or none and taking
    `testpaths` — with nothing on its command line or in its environment that names a base
    or a root instead. `tests/test_pytest_temp_root.py` drives what the claim does; this
    holds every real command to reaching it.
    """
    for path, target in SUITE_TIERS:
        declared = json.loads((REPO_ROOT / path).read_text(encoding="utf-8"))["targets"][target]
        command = declared["command"]
        where = f"{path} {target}"
        assert declared.get("options", {}).get("cwd", ".") == ".", where
        overridden = [o for o in (*_TEMP_ROOT_OVERRIDES, *_TEMP_ROOT_VARIABLES) if o in command]
        assert not overridden, f"{where} names its own temporary root: {overridden}"
        selection = _selection(command)
        named = [
            argument
            for previous, argument in zip(["", *selection[:-1]], selection, strict=True)
            if not argument.startswith("-") and previous not in _VALUED_OPTIONS
        ]
        outside = [argument for argument in named if not argument.startswith("tests/")]
        assert not outside, f"{where} collects outside the conftest that claims a root: {outside}"


def _root_of(project: str) -> str:
    """The root of the project Nx knows as ``project``."""
    return next(
        root for root, declared in project_declarations().items() if declared["name"] == project
    )


def _owning_project_root(path: str) -> str | None:
    """The test project a tracked path belongs to: the deepest project root holding it.

    The journey families nest inside `tests/e2e/`, which is a project of its own for the
    journey modules directly under it; every other file there is a shared helper or its
    data, which a test-support unit carries, so it is no test project's.
    """
    holding = [
        root
        for root in project_declarations()
        if root.startswith("tests/")
        and not root.startswith(f"{SUPPORT_ROOT}/")
        and path.startswith(f"{root}/")
    ]
    root = max(holding, key=len, default=None)
    if root == E2E_ROOT and not re.fullmatch(rf"{E2E_ROOT}/test_[^/]+\.py", path):
        return None
    return root


def _journey_modules(root: str) -> list[str]:
    """The test modules the project at ``root`` owns, by `_owning_project_root`."""
    return sorted(
        path
        for path in _tracked()
        if Path(path).name.startswith("test_")
        and path.endswith(".py")
        and _owning_project_root(path) == root
    )


def _unit_gate_journey_projects() -> list[str]:
    """The journey projects declaring the journeys `orchestrator:test`'s gates read by path."""
    return sorted(
        journey.project
        for journey in E2E_JOURNEYS
        if JOURNEYS_THE_UNIT_GATES_READ
        in project_declarations()[journey.root].get("namedInputs", {})
    )


def test_the_code_only_test_key_drops_prose_and_nothing_else() -> None:
    """The shared code key is narrowed by exactly one thing, which has a tier of its own.

    The unit tier narrows it once more, by the journey modules the projects under
    `tests/e2e/` own, taking back the ones its own gates open by path from the projects
    that own them.
    """
    named = _nx_config()["namedInputs"]
    assert named[CODE_WORKSPACE] == CODE_WORKSPACE_GLOBS, (
        "both code tiers replay a verdict for every tracked path this key covers, "
        "so narrowing it further would memoize a claim about code they never read"
    )

    project = json.loads((REPO_ROOT / "orchestrator/project.json").read_text(encoding="utf-8"))
    taking = _unit_gate_journey_projects()
    assert taking, f"no journey project declares {JOURNEYS_THE_UNIT_GATES_READ}"
    assert project["targets"][CODE_SCOPED]["inputs"] == [
        UNIT_WORKSPACE,
        {"input": JOURNEYS_THE_UNIT_GATES_READ, "projects": taking},
    ], project["targets"][CODE_SCOPED]["inputs"]
    assert project["namedInputs"][UNIT_WORKSPACE][0] == CODE_WORKSPACE

    globs = _effective_inputs("orchestrator", CODE_SCOPED)
    taken_back = {
        path
        for name in taking
        for path in named_input_globs(JOURNEYS_THE_UNIT_GATES_READ, project_root=_root_of(name))
    }
    missed = sorted(path for path in _tracked() if not covers(globs, path))
    journeys = [path for path in missed if path.startswith(f"{E2E_ROOT}/")]
    assert missed and all(
        _is_documentation(path) or path.startswith(f"{E2E_ROOT}/") for path in missed
    ), f"only documentation and the journeys may fall outside the unit key: {missed}"
    assert journeys and not any(path in taken_back for path in journeys), journeys
    # And every module a journey project owns is out of the unit key unless taken back.
    owned = sorted(path for journey in E2E_JOURNEYS for path in _journey_modules(journey.root))
    stray = [path for path in owned if path not in journeys and path not in taken_back]
    assert owned and not stray, f"the unit key covers journey modules it does not read: {stray}"
    # The whole-workspace tier is what covers the rest, so it must actually exist.
    assert DOCS_SCOPED in project["targets"]


def test_the_journey_key_leaves_out_every_other_test_project() -> None:
    """No other test project's files, and no unit or drift-gate module, are in a journey key.

    Each journey project's key names what its journeys read, so this holds that what it
    keeps never grows to cover another test project's directory or a module directly under
    `tests/` other than through a unit carrying one a journey imports — the edge that is
    how a diff of that module selects the journeys.
    """
    tracked = _tracked()
    owners = _unit_owners()
    unit_modules = [path for path in tracked if re.fullmatch(r"tests/test_[^/]+\.py", path)]
    assert unit_modules
    for journey in E2E_JOURNEYS:
        globs = _effective_inputs(journey.root, CODE_SCOPED)
        held: dict[str, list[str]] = {}
        for path in tracked:
            root = _owning_project_root(path)
            # Another project's declaration is graph a journey may read — a module of the
            # journeys that copy this checkout reads the measuring projects' at import — and
            # a read the key then has to name; its tests and data are what stay out.
            if Path(path).name == "project.json":
                continue
            if root is not None and root != journey.root and covers(globs, path):
                held.setdefault(root, []).append(path)
        assert not held, (
            f"{journey.project}:{CODE_SCOPED} is keyed on other projects' files: {held}"
        )
        keyed = sorted(path for path in unit_modules if covers(globs, path))
        assert all(path in owners for path in keyed), (journey.project, keyed)
        assert not any(covers(list(globs.filesets[0]), path) for path in unit_modules), (
            journey.project
        )


#: The flag that makes a pytest target measure `orchestrator/` in process, and so one whose
#: data the coverage tier has to combine.
MEASURES = "--cov=orchestrator"


def _measuring() -> dict[tuple[str, str], str | None]:
    """Every target measuring `orchestrator/` in process, and the data file it writes into."""
    found: dict[tuple[str, str], str | None] = {}
    for declared in project_declarations().values():
        for target, spec in declared.get("targets", {}).items():
            command = spec.get("command", "")
            if MEASURES in command.split():
                data = re.search(r"\bCOVERAGE_FILE=(\S+) ", command)
                found[(declared["name"], target)] = data.group(1) if data else None
    return found


def test_the_coverage_tier_is_unmemoized_and_waits_for_the_tier_that_measures() -> None:
    """The floor is enforced once, on data the tiers that measured it actually wrote.

    Two things make that sound, and both are declarations rather than conventions: the
    tier waits on every measuring tier, so it can never report on a subset; and it is
    uncached, so a replayed test verdict still pays for a fresh comparison against the
    floor. The measuring set is read off the project declarations — every target whose
    command measures `orchestrator/` — so a project that measures and is missing from the
    combine fails here, and so does a combined file no project writes.
    """
    project = json.loads((REPO_ROOT / "orchestrator/project.json").read_text(encoding="utf-8"))
    coverage = project["targets"][COVERAGE_SCOPED]
    measuring = _measuring()
    journeys = {journey.project for journey in E2E_JOURNEYS}
    assert ("orchestrator", CODE_SCOPED) in measuring, measuring
    assert {name for name, _ in measuring} & journeys, (
        "no journey project measures orchestrator/ in process, so the floor reads the unit "
        "tier alone"
    )

    assert coverage["dependsOn"] == [
        CODE_SCOPED,
        *(
            {"projects": [name], "target": target}
            for name, target in sorted(measuring)
            if name != "orchestrator"
        ),
    ], (
        "the coverage tier must wait on every tier that measures, or the floor is "
        f"evaluated against part of the suite: {coverage['dependsOn']}"
    )
    assert _nx_config()["targetDefaults"][COVERAGE_SCOPED] == {"cache": False}, (
        "a memoized floor could be replayed for a tree it never measured; this tier "
        "is seconds of work and is deliberately re-run every time"
    )
    combined = re.search(r"coverage combine --keep ((?:\S+ )+)&&", coverage["command"])
    assert combined is not None, coverage["command"]
    read = combined.group(1).split()
    # And each measuring tier has to actually write the data this one reads, or the floor
    # is judged against whatever an earlier run happened to leave behind.
    for (name, target), data_file in sorted(measuring.items()):
        expected = UNIT_COVERAGE_DATA if name == "orchestrator" else coverage_data(name)
        assert data_file == expected, (name, target, data_file)
        spec = project_declarations()[_root_of(name)]["targets"][target]
        assert f"{{workspaceRoot}}/{data_file}" in spec.get("outputs", []), (name, target)
        assert data_file in read, (
            f"{data_file} is measured by {name}:{target} but never read, so its lines do not "
            f"count towards the enforced floor: {coverage['command']}"
        )
    assert sorted(read) == sorted(data for data in measuring.values() if data), (
        f"the coverage tier combines {read}, and a file no measuring target writes is one an "
        "earlier run left behind"
    )


#: The two tiers that write under this checkout's configured `authoring` root in one Nx
#: run: the run-end hooks journey seeds a plan project there and reads it back byte for
#: byte, and the ask-seam launch journeys opt into that root with
#: `real_plan_store_roots("authoring", ...)` and launch `just plan`, whose closeout
#: records a planner pass on every project that appeared under it during its window.
#: Run together, the second rewrites the first's seed mid-journey.
AUTHORING_ROOT_WRITERS = (RUN_END_HOOKS_ROOT, f"{ASK_SEAM_ROOT}/launch")


def test_the_tiers_writing_the_configured_authoring_root_never_run_together() -> None:
    """One of the two is declared exclusive, so Nx never schedules them side by side.

    A scheduling constraint rather than an assertion loosened in either journey: each
    journey's reading is right about its own subject, and what made them disagree was
    only that one Nx invocation ran both at once. `parallelism: false` on a target is
    Nx's own declaration that the task runs with no other task beside it, which is the
    narrowest scheduling constraint Nx declares between two projects. The opt-in is read
    back too, so the pair stays the pair that actually shares the root.
    """
    launch_module = REPO_ROOT / AUTHORING_ROOT_WRITERS[1] / "test_launch_ask_seam_e2e.py"
    assert re.search(
        r"real_plan_store_roots\(\s*\"authoring\"", launch_module.read_text(encoding="utf-8")
    ), f"{launch_module} no longer opts into the configured authoring root; re-read the pair"

    exclusive = [
        root
        for root in AUTHORING_ROOT_WRITERS
        if json.loads((REPO_ROOT / root / "project.json").read_text(encoding="utf-8"))["targets"][
            "test"
        ].get("parallelism")
        is False
    ]
    assert exclusive, (
        f'neither of {AUTHORING_ROOT_WRITERS} declares `"parallelism": false` on its test '
        "target, so one Nx run can schedule both while each writes the configured "
        "authoring root the other reads back"
    )


def test_every_repository_path_the_suite_reads_is_part_of_a_test_key() -> None:
    """A new test that reads a new path must not be able to replay a stale verdict.

    Read across the unit tier's key and every journey project's, because a module names
    paths for whichever tier collects it; `conftest.py` holds each test's own reads to its
    own tier's key. A module of a subject project — a plan-tooling subject or a drift gate —
    is held to its own project's keys: every tracked path it names is in the key of one of
    that project's targets, so a document it reads selects it and one it never names does
    not.
    """
    keys = (
        _effective_inputs("orchestrator", CODE_SCOPED),
        *(_effective_inputs(journey.root, CODE_SCOPED) for journey in E2E_JOURNEYS),
    )
    tracked = _tracked()
    read: set[str] = set()
    unkeyed: dict[str, list[str]] = {}
    for source in sorted(REPO_ROOT.joinpath("tests").rglob("*.py")):
        named = _named_repository_paths(source.read_text(encoding="utf-8"), tracked)
        relative = str(source.relative_to(REPO_ROOT))
        subject = next(
            (one for one in SUBJECT_PROJECTS if relative.startswith(f"{one.root}/")), None
        )
        if subject is None:
            read |= named
            continue
        own = [_effective_inputs(subject.root, target) for target in _test_tiers(subject.root)]
        missing = sorted(path for path in named if not any(covers(key, path) for key in own))
        if missing:
            unkeyed[relative] = missing
    assert not unkeyed, (
        "these subject-project modules name repository paths no key of their own project "
        f"covers, so a change to one replays a stale verdict: {unkeyed}"
    )

    # A guard that found nothing would pass silently forever.
    assert {"AGENTS.md", "justfile", "scripts/session-setup.sh"} <= read, read
    uncovered = sorted(path for path in read if not any(covers(key, path) for key in keys))
    assert all(_is_documentation(path) for path in uncovered), (
        "the code tiers read these repository paths but neither is keyed on them, "
        f"so a change to one replays a stale verdict: {uncovered}"
    )
    # What the suite reads outside these keys is not exempt, only keyed elsewhere:
    # `test-docs` is keyed on the whole workspace, so it covers every path here —
    # and `conftest.py` is what holds each of those reads to that tier.
    assert "README.md" in uncovered, (
        "the sentinel prose read moved; keep a real one here or this guard stops guarding"
    )


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


#: The package under test, which every tier imports through `tests/conftest.py` at least.
PACKAGE = "orchestrator"


def _package_module(dotted: str) -> str | None:
    """The tracked file importing ``dotted`` executes, when it is one of the package's."""
    for candidate in (f"{dotted.replace('.', '/')}.py", f"{dotted.replace('.', '/')}/__init__.py"):
        if (REPO_ROOT / candidate).is_file():
            return candidate
    return None


def _package_modules(sources: set[str]) -> set[str]:
    """Every module of the package ``sources`` reach by import, transitively.

    The same read the suite-module closure makes, for the package rather than for
    `tests/`: an import resolves through the import machinery rather than `open`, so a
    package module a tier imports and its key leaves out would change its answer without
    changing its hash. An import inside a function counts as much as one at the top,
    because a key slightly wider than the run is the safe direction.
    """
    reached: set[str] = set()
    pending = list(sources)
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        tree = ast.parse((REPO_ROOT / current).read_text(encoding="utf-8"))
        within = current.startswith(f"{PACKAGE}/")
        for node in ast.walk(tree):
            dotted: list[str] = []
            match node:
                case ast.Import(names=aliases):
                    dotted = [alias.name for alias in aliases]
                case ast.ImportFrom(module=module, level=0, names=aliases) if module:
                    dotted = [module, *(f"{module}.{alias.name}" for alias in aliases)]
                case ast.ImportFrom(module=module, level=1, names=aliases) if within:
                    base = f"{PACKAGE}.{module}" if module else PACKAGE
                    dotted = [base, *(f"{base}.{alias.name}" for alias in aliases)]
            for name in dotted:
                if name != PACKAGE and not name.startswith(f"{PACKAGE}."):
                    continue
                found = {_package_module(PACKAGE), _package_module(name)} - {None}
                reached |= {module for module in found if module is not None}
                pending.extend(module for module in found if module is not None)
    return reached


def _tier_package_imports(path: str, target: str) -> set[str]:
    """The package modules one tier imports: through its test modules, every `tests/`
    module they reach, and `tests/conftest.py`."""
    modules = _suite_modules()
    sources = {"tests/conftest.py", *_tier_modules(path, target)}
    for module in list(sources):
        sources |= _imported_suite_modules(module, modules)
    return _package_modules(sources)


def _unit_declarations() -> dict[str, dict]:
    """Every test-support unit, by the root its declaration sits at."""
    return {
        root: declared
        for root, declared in project_declarations().items()
        if root.startswith(f"{SUPPORT_ROOT}/")
    }


def _tier_project_roots() -> list[str]:
    """Every project under `tests/` whose targets collect tests, which is every one there
    that is not a unit; a module under one of these is that project's alone.

    Except the journeys' root: `tests/e2e/` is also where pytest's `pythonpath` imports
    the shared helpers from, so a module there that is not a test is a unit's, exactly
    as one directly under `tests/` is.
    """
    return [
        root
        for root in project_declarations()
        if root.startswith("tests/")
        and not root.startswith(f"{SUPPORT_ROOT}/")
        and root != E2E_ROOT
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
    # A tier's own test modules are its own even where a unit also carries one for the
    # journeys that import it, so what they reach counts for the tier that collects them.
    own = {source for source in sources if source not in owners} | set(declaring)
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
    """The test modules one tier imports, from a real collection of its own command.

    Its marker expression aside: pytest imports every module under the paths it collects
    before a marker deselects a test, so a module whose every test another tier runs is
    still imported here, and what it imports at module level — and what that reads — is
    still part of this tier's verdict.
    """
    command = json.loads((REPO_ROOT / path).read_text(encoding="utf-8"))["targets"][target][
        "command"
    ]
    selection = _selection(command)
    unmarked = [
        argument
        for index, argument in enumerate(selection)
        if argument != "-m" and (index == 0 or selection[index - 1] != "-m")
    ]
    return sorted({test_id.split("::")[0] for test_id in _collected(unmarked)})


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
            imported = sorted(
                path for path in _package_modules({module}) if not covers(carried, path)
            )
            assert not imported, (
                f"{declared['name']} does not carry {imported}, which {module} imports"
            )
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
    # The package modules the tier's sources import, which each unit carries for its own
    # module and the project's key for the rest — an uncached tier's key included, which
    # its guard holds its tests to as a memoized tier's holds them.
    globs = _effective_inputs(root, target)
    unimported = sorted(
        module for module in _tier_package_imports(path, target) if not covers(globs, module)
    )
    assert not unimported, (
        f"{root}:{target} imports {unimported}, which its key does not cover; add each "
        "to the `testSupport` of the unit whose module imports it, or to the project's "
        "own key, or editing one replays a verdict recorded before it moved"
    )
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
    else:
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
        # A project with a target keyed on the whole workspace is selected by every
        # change, so a key it takes from a project it does not depend on still reaches
        # it: that is how `orchestrator:test` takes back the journeys its gates read,
        # with no edge from the package to its own journeys.
        always_selected = any(
            spec.get("inputs") == [WHOLE_WORKSPACE] for spec in declared.get("targets", {}).values()
        )
        for spec in declared.get("targets", {}).values():
            for entry in spec.get("inputs", []):
                if isinstance(entry, dict) and "projects" in entry:
                    listed = entry["projects"]
                    named |= {listed} if isinstance(listed, str) else set(listed)
                    assert always_selected or named <= _dependency_names(root), (
                        f"{root or '.'}/project.json keys a target on {sorted(named)} that "
                        "its project does not depend on, so no edge carries that key"
                    )
        assert named <= names, f"{root or '.'}/project.json names {sorted(named - names)}"


#: The named inputs `nx.json` itself declares: the keys every project shares — `production`
#: among them, what a project's budgets take of each dependency they measure — and the
#: empty `testSupport` a project that is not a unit contributes. A tier's own key is
#: declared in its own `project.json`, where it reads as that project's.
SHARED_NAMED_INPUTS = frozenset(
    {
        "default",
        "production",
        "sharedGlobals",
        WHOLE_WORKSPACE,
        CODE_WORKSPACE,
        TEST_SUPPORT,
    }
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


def test_the_recipe_key_stays_inside_the_code_key() -> None:
    """A recipe test could otherwise read something the code-key guard permits and
    this tier does not carry: the orchestrator project's recipe key inside its own code
    key."""
    code_globs = _effective_inputs(MARKER_TIER_ROOT, CODE_SCOPED)
    # The recipe tier's own files; the units it takes are held to what its modules
    # reach by `test_every_tier_is_keyed_on_exactly_the_units_its_modules_reach`.
    recipe_globs = list(_effective_inputs(MARKER_TIER_ROOT, RECIPE_SCOPED).filesets[0])
    outside = sorted(
        path for path in _tracked() if covers(recipe_globs, path) and not covers(code_globs, path)
    )
    assert not outside, f"the orchestrator recipe key reaches outside its code key: {outside}"


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
        # A Markdown file under `config/` or `templates/` is a template a tool reads — the
        # dispatch appendix, the design-document template — not prose about this repository.
        prose = sorted(
            path
            for path in covered
            if _is_documentation(path) and not path.startswith(("config/", "templates/"))
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
        # And the declarations the read guard opens to resolve this journey's key, which
        # `test_every_key_covers_the_declarations_its_resolution_reads` holds the key to.
        resolved = {
            declaration_path(at) for at in _declarations_read(journey.root, ASK_SEAM_SCOPED)
        }
        allowed = sources | _stand_ins_named(sources, tracked) | opened | resolved
        surplus = sorted(
            path for path in covered if path.startswith("tests/") and path not in allowed
        )
        assert not surplus, (
            f"{journey.key} reaches {surplus}, which {module} neither imports, names as a "
            "stand-in, nor runs under — a sibling's module or a helper it never opens, and "
            "an edit there would pay for this journey's launch"
        )


def test_every_project_root_is_in_the_static_catalog() -> None:
    """The read guard resolves projects from the static catalog, so the catalog is complete.

    It decides which project owns a module from `OWNED_PROJECTS`, and resolves a project an
    input or an edge names by the unit naming rule or a row of `catalogued_roots`, never by
    listing every declaration: a listing would open every `project.json` in every test
    process, which no narrow key covers. So every project git lists must be reachable that
    way. A test project is an `OWNED_PROJECTS` row or the orchestrator project, a unit is
    its name's directory under `tests/support/`, and every name an edge or input uses
    resolves to the root git lists for it.
    """
    declared = project_declarations()
    missing = sorted(
        root
        for root in declared
        if root.startswith("tests/")
        and not root.startswith(f"{SUPPORT_ROOT}/")
        and root not in OWNED_PROJECTS
        and root != E2E_ROOT
    )
    assert not missing, (
        "these test projects are no row of the static catalog the read guard owns modules "
        f"from; add each to OWNED_PROJECTS in tests/conftest.py: {missing}"
    )
    unrowed = sorted(
        declaration["name"]
        for declaration in declared.values()
        if not declaration["name"].startswith("support-")
        and declaration["name"] not in catalogued_roots()
    )
    assert not unrowed, (
        f"these projects are no row of catalogued_roots in tests/nx_inputs.py: {unrowed}"
    )
    misresolved = {
        name: (project_root(name), root)
        for root, declaration in declared.items()
        for name in (declaration["name"],)
        if project_root(name) != root
    }
    assert not misresolved, f"the static catalog resolves these names elsewhere: {misresolved}"
    named = {
        name
        for declaration in declared.values()
        for name in (
            *declaration.get("implicitDependencies", []),
            *(
                project
                for spec in declaration.get("targets", {}).values()
                for entry in spec.get("inputs") or []
                if isinstance(entry, dict) and "projects" in entry
                for project in (
                    [entry["projects"]] if isinstance(entry["projects"], str) else entry["projects"]
                )
            ),
        )
    }
    by_name = {declaration["name"]: root for root, declaration in declared.items()}
    unresolved = sorted(name for name in named if project_root(name) != by_name.get(name))
    assert not unresolved, (
        f"an edge or input names these, and the catalog misplaces them: {unresolved}"
    )


def _declarations_read(root: str, target: str) -> set[str]:
    """Every declaration resolving ``root``'s ``target`` key opens, as the read guard does.

    The real loader runs under a spy that only records which root each call asked for.
    """
    with mock.patch.object(
        nx_inputs, "project_declaration", wraps=nx_inputs.project_declaration
    ) as loader:
        nx_inputs._target_input_globs.__wrapped__(root, target)
    return {call.args[0] for call in loader.call_args_list}


def test_every_key_covers_the_declarations_its_resolution_reads() -> None:
    """A tier's key names each `project.json` its read guard opens to resolve that key.

    The guard opens the owning project's declaration and every one its key's inputs reach
    — each transitive dependency's for a `dependencies` input, each named project's for a
    `projects` one — and an edit to any of them can change what the guard holds a test to.
    So each is in the key, or that edit replays a verdict the guard would now refuse. The
    set is measured off the resolver itself rather than restated, for every test tier of
    every project — `orchestrator`'s marker-routed tiers as much as a directory-owned
    project's, and an uncached tier as much as a memoized one: an uncached tier has no
    replay to make stale, but its key is still what its guard holds its tests to, and a
    key that drifts from what they read is one the day it is cached replays wrongly.
    """
    uncovered: dict[str, list[str]] = {}
    for root, declaration in sorted(project_declarations().items()):
        if root.startswith(f"{SUPPORT_ROOT}/"):
            continue
        for target in declaration.get("targets", {}):
            if not target.startswith("test"):
                continue
            key = target_input_globs(root, target)
            missing = sorted(
                declaration_path(at)
                for at in _declarations_read(root, target)
                if not covers(key, declaration_path(at))
            )
            if missing:
                uncovered[f"{declaration['name']}:{target}"] = missing
    assert not uncovered, (
        "these keys omit declarations their read guard opens; a unit carries its own "
        "`{projectRoot}/project.json` in `testSupport`, and a project its own in its key: "
        f"{uncovered}"
    )
