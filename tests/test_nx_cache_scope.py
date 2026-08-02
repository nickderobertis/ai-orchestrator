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
two costliest read neither prose nor orchestrator code — they drive `just` recipes
and shell scripts. `test-docs` runs the prose contracts and keeps the
whole-workspace key; `test-recipes` runs the recipe journeys under the narrow key
those journeys actually read; `test` and `test-serial` run the remainder, keyed on
the workspace minus its prose and minus the front-end projects no Python test
opens. Those last two are one scope split into two tasks, not a fourth key: the
`single_threaded` tests need a process with no execnet thread in it and read
exactly what the bulk reads. What keeps the narrowed keys honest is not this file
— a static scan cannot see every read — but `tests/conftest.py`, which fails a
test the moment it opens something its own tier's key does not carry.

`coverage` is the one target here with nothing to keep honest, and deliberately:
it combines what the measuring tiers wrote and compares that total to the declared
floor, so it is uncached and there is no memo to be wrong about.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess

from conftest import READS_DOCS_MARKER, READS_RECIPES_MARKER
from nx_inputs import (
    CODE_SCOPED,
    CODE_WORKSPACE,
    COVERAGE_SCOPED,
    DOCS_SCOPED,
    NX_CACHE_CHECK,
    RECIPE_SCOPED,
    RECIPE_WORKSPACE,
    SERIAL_SCOPED,
    covers,
    named_input_globs,
)

from orchestrator import REPO_ROOT

WHOLE_WORKSPACE = "wholeWorkspace"

#: Every one of these runs from the workspace root against the whole tree — ruff
#: over `.`, shellcheck over `scripts/`, persona validation over `personas/`, and
#: the prose-contract tests, which exist to read documentation.
WORKSPACE_SCOPED = ("lint", "typecheck", "format-check", DOCS_SCOPED)
#: The tiers keyed on less than the whole workspace, and the exact globs that earn
#: it: the workspace with its documentation and its front-end projects removed,
#: and nothing else. Both halves of the Python code suite share it — they read the
#: same tree and differ only in the process shape their tests need.
CODE_KEYED = (CODE_SCOPED, SERIAL_SCOPED)
CODE_WORKSPACE_GLOBS = [
    "{workspaceRoot}/**/*",
    "!{workspaceRoot}/docs/**/*",
    "!{workspaceRoot}/**/*.md",
    "!{workspaceRoot}/apps/**/*",
    "!{workspaceRoot}/packages/**/*",
]
#: The front-end project roots the code key drops. No Python test reads them; the
#: whole-workspace tier covers the DAG contract checks that do.
FRONT_END_ROOTS = ("apps/", "packages/")


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


def _resolve(entries: list, named: dict[str, list]) -> list[str]:
    """Expand named inputs into the concrete file globs Nx will hash."""
    globs: list[str] = []
    for entry in entries:
        match entry:
            case dict():
                pass  # an env or runtime input contributes no file coverage
            case _ if entry in named:
                globs.extend(_resolve(named[entry], named))
            case _:
                globs.append(entry)
    return globs


def _effective_inputs(project_root: str, target: str) -> list[str]:
    config = _nx_config()
    project_file = REPO_ROOT / project_root / "project.json" if project_root else None
    project = json.loads((project_file or REPO_ROOT / "project.json").read_text(encoding="utf-8"))
    declared = (
        project["targets"][target].get("inputs") or config["targetDefaults"][target]["inputs"]
    )
    resolved = _resolve(declared, config["namedInputs"])
    return [
        glob.replace("{workspaceRoot}/", "").replace("{projectRoot}/", f"{project_root}/")
        for glob in resolved
    ]


def _is_documentation(relative: str) -> bool:
    return relative.startswith("docs/") or relative.endswith(".md")


def _is_front_end(relative: str) -> bool:
    return relative.startswith(FRONT_END_ROOTS)


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
    llmlint = _nx_config()["targetDefaults"]["lint-llm-diff"]["inputs"]
    assert llmlint[0] == WHOLE_WORKSPACE, (
        "the llmlint tier judges the whole workspace diff and shares this one key"
    )


def test_the_marker_that_routes_a_test_to_its_tier_means_the_same_thing_everywhere() -> None:
    """Reconcile the four places the marker name is independently written down.

    Each marker names a routing decision, not a label: pytest registers it,
    `conftest.py` enforces it, and the Nx targets select on it. Those declarations
    are written separately and nothing else compares them, so a rename that missed
    one would leave a tier silently selecting nothing — and a `test` tier that ran
    the prose contracts anyway, keyed on a workspace without prose, is the false
    green this whole file exists to prevent.
    """
    manifest = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    registered = re.findall(r'^\s*"(\w+):', manifest, flags=re.MULTILINE)
    for marker in (READS_DOCS_MARKER, READS_RECIPES_MARKER):
        assert marker in registered, (
            f"pytest must register {marker!r} in [tool.pytest.ini_options] markers"
        )

    targets = json.loads((REPO_ROOT / "orchestrator/project.json").read_text(encoding="utf-8"))[
        "targets"
    ]
    # The code suite runs in more than one invocation — the parallel bulk and the
    # serial `single_threaded` remainder, now separate targets — so every one of
    # their selectors has to exclude both narrower tiers, not merely the first one
    # written down.
    code_selectors = [
        selector
        for target in CODE_KEYED
        for selector in re.findall(r"-m '([^']+)'", targets[target]["command"])
    ]
    assert len(code_selectors) == len(CODE_KEYED), code_selectors
    excluded = f"not {READS_DOCS_MARKER} and not {READS_RECIPES_MARKER}"
    assert all(selector.startswith(excluded) for selector in code_selectors), code_selectors

    # And the selectors have to partition: a test is in exactly one tier.
    for marker, target in ((READS_DOCS_MARKER, DOCS_SCOPED), (READS_RECIPES_MARKER, RECIPE_SCOPED)):
        assert f"-m {marker}" in targets[target]["command"]
        marked = re.search(r"-m '?(not )?(\w+)'?", targets[target]["command"])
        assert marked is not None and marked.group(1) is None


#: Every place the parallel worker contract is independently written down. It is a
#: contract because the number was chosen by measurement against this host — see
#: `docs/repo-lifecycle.md` — and a recipe that quietly drifted to a different one
#: would stop being evidence for the tier the gate actually runs.
PARALLEL_SITES = (
    ("orchestrator/project.json", CODE_SCOPED),
    ("orchestrator/project.json", DOCS_SCOPED),
    ("orchestrator/project.json", RECIPE_SCOPED),
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
    """The worker count and distribution are one contract, written in three places.

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


def _collected(selector: str) -> set[str]:
    """Every test id pytest selects for one marker expression, from a real collection.

    No extra ``-q``: the repository's own ``addopts`` already carries one, and a
    second turns the listing into per-file counts that cannot be compared as sets.
    """
    collected = subprocess.run(
        ["uv", "run", "pytest", "--collect-only", "--no-cov", "-m", selector],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert collected.returncode == 0, collected.stdout + collected.stderr
    return {line.strip() for line in collected.stdout.splitlines() if "::" in line}


def test_the_code_tiers_parallel_and_serial_invocations_partition_it() -> None:
    """The code suite runs in two tiers, so neither may drop a test on the floor.

    `single_threaded` splits the code suite because those tests need a process with
    no execnet thread in it, and everything else runs across xdist workers. Two
    commands selecting on one marker is exactly the shape that loses a test in
    silence: a typo in either expression leaves tests that no invocation collects,
    and a suite that runs fewer tests reports the same green as one that runs them
    all. Separate targets make that easier to get wrong, not harder — nothing
    chains them any more — so the partition is derived from the real targets and
    checked against real collections rather than read off the JSON.
    """
    targets = json.loads((REPO_ROOT / "orchestrator/project.json").read_text(encoding="utf-8"))[
        "targets"
    ]
    selectors = [
        selector
        for target in CODE_KEYED
        for selector in re.findall(r"-m '([^']+)'", targets[target]["command"])
    ]
    assert len(selectors) == 2, f"the code suite no longer runs two invocations: {selectors}"

    parts = [_collected(selector) for selector in selectors]
    assert not parts[0] & parts[1], (
        f"both code-tier invocations collect {sorted(parts[0] & parts[1])[:5]}"
    )
    whole = _collected(f"not {READS_DOCS_MARKER} and not {READS_RECIPES_MARKER}")
    assert parts[0] | parts[1] == whole, (
        "the code suite's tiers no longer cover it: "
        f"{sorted(whole - (parts[0] | parts[1]))[:5]} is collected by neither"
    )
    # Both halves have to be non-empty, or the split is silently doing nothing and
    # the marker it rests on could have been deleted without anything noticing.
    assert all(parts), [len(part) for part in parts]


def test_the_code_only_test_key_drops_prose_and_the_front_end_and_nothing_else() -> None:
    """The narrowed key is narrowed by exactly two things, both of which have a tier."""
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
    assert missed and all(_is_documentation(path) or _is_front_end(path) for path in missed), (
        f"only documentation and the front-end projects may fall outside this key: {missed}"
    )
    # Both halves have to be real, or one exclusion is silently doing nothing.
    assert any(_is_documentation(path) for path in missed)
    assert any(_is_front_end(path) for path in missed)
    # The whole-workspace tier is what covers the rest, so it must actually exist.
    assert DOCS_SCOPED in project["targets"]


def test_the_coverage_tier_is_unmemoized_and_waits_for_every_measuring_tier() -> None:
    """The floor is enforced once, on data no tier can be missing from.

    Splitting the code suite into two targets split its coverage data with it, so
    the enforced total is now assembled by a third target rather than by appending
    inside one command. Two things make that sound, and both are declarations
    rather than conventions: the tier waits on every measuring tier, so it can
    never report on a subset; and it is uncached, so a replayed test verdict still
    pays for a fresh combine and a fresh comparison against the floor.
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
    # And every measuring tier has to actually write the data it combines, under a
    # name of its own — one shared file is how the two invocations were chained.
    written = {
        project["targets"][target]["outputs"][0].removeprefix("{workspaceRoot}/")
        for target in CODE_KEYED
    }
    assert len(written) == len(CODE_KEYED), f"the measuring tiers share a data file: {written}"
    for data_file in written:
        assert data_file in coverage["command"], (
            f"{data_file} is measured but never combined, so its lines do not count "
            f"towards the enforced floor: {coverage['command']}"
        )


def test_every_repository_path_the_suite_reads_is_part_of_a_test_key() -> None:
    """A new test that reads a new path must not be able to replay a stale verdict."""
    globs = _effective_inputs("orchestrator", CODE_SCOPED)
    tracked = _tracked()
    read: set[str] = set()
    for source in sorted(REPO_ROOT.joinpath("tests").rglob("*.py")):
        text = source.read_text(encoding="utf-8")
        # Two shapes, because a suite names a repository path both ways: joined onto
        # the root at the call site, and listed as a bare relative literal a helper
        # joins later. Reading only the first shape missed every path a fixture
        # copies from a tuple — which is where the front-end reads live.
        candidates = {
            "/".join(re.findall(r'"([^"]+)"', match.group(0)))
            for match in re.finditer(r'(?:REPO_ROOT|ROOT)\s*/\s*"([^"]+)"(\s*/\s*"[^"]+")*', text)
        }
        candidates |= {match.group(1) for match in re.finditer(r'"([^"\n]+)"', text)}
        for candidate in candidates:
            if candidate in tracked or any(path.startswith(f"{candidate}/") for path in tracked):
                read.add(candidate)

    # A guard that found nothing would pass silently forever.
    assert {"AGENTS.md", "justfile", "scripts/session-setup.sh"} <= read, read
    uncovered = sorted(path for path in read if not covers(globs, path))
    assert all(_is_documentation(path) or _is_front_end(path) for path in uncovered), (
        "orchestrator:test reads these repository paths but is not keyed on them, "
        f"so a change to one replays a stale verdict: {uncovered}"
    )
    # What the suite reads outside this key is not exempt, only keyed elsewhere:
    # `test-docs` is keyed on the whole workspace, so it covers every path here —
    # and `conftest.py` is what holds each of those reads to that tier.
    assert "AGENTS.md" in uncovered, (
        "the sentinel prose read moved; keep a real one here or this guard stops guarding"
    )
    assert "apps/dag-ui/vite.config.ts" in uncovered, (
        "the sentinel front-end read moved; keep a real one here or this guard stops guarding"
    )


def test_the_e2e_witnesses_still_name_paths_outside_the_project_roots() -> None:
    """Each journey's witness must stay a file no project-root glob would cover.

    One witness per tier, and they have to differ in exactly the way the two keys
    do: the prose witness proves the whole-workspace tier notices documentation,
    and the code witness proves the narrowed tier still notices everything else.
    """
    project_scoped = _resolve(["default"], _nx_config()["namedInputs"])
    globs = [glob.replace("{workspaceRoot}/", "") for glob in project_scoped]
    globs = [glob.replace("{projectRoot}/", "orchestrator/") for glob in globs]
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
    assert not covers(globs, "orchestrator/lifecycle.py")
