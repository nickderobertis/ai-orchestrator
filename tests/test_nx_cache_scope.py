"""A cached verdict may only stand in for a verdict on the tree it was keyed on.

Every cached Nx target memoizes an answer. Replaying one is sound exactly when the
key covers everything the check reads, and unsound the moment it does not: a file
the check reads but the key ignores is a tree that can change its answer without
changing its hash, which is a green verdict for a tree that would have failed.

`tests/e2e/test_nx_cache_scope_e2e.py` proves the behaviour against real Nx. These
assertions keep the declarations that behaviour rests on from narrowing again —
including as the suite grows new reads, which is how the subset drifted out of
date in the first place.

The Python suite is keyed at two scopes rather than one, because "keyed on
everything it reads" and "keyed on the whole workspace" are not the same
requirement. Only a handful of tests assert on this repository's prose; charging
every documentation edit eight minutes for the rest bought nothing. `test-docs`
runs those tests and keeps the whole-workspace key; `test` runs the remainder and
is keyed on the workspace minus its prose. What keeps that second key honest is
not this file — a static scan cannot see every read — but `tests/conftest.py`,
which fails an undeclared test the moment it opens this checkout's own
documentation.
"""

from __future__ import annotations

import json
import re
import subprocess

from conftest import READS_DOCS_MARKER

from orchestrator import REPO_ROOT

WHOLE_WORKSPACE = "wholeWorkspace"
CODE_WORKSPACE = "codeWorkspace"

#: Every one of these runs from the workspace root against the whole tree — ruff
#: over `.`, shellcheck over `scripts/`, persona validation over `personas/`, and
#: the prose-contract tests, which exist to read documentation.
WORKSPACE_SCOPED = ("lint", "typecheck", "format-check", "test-docs")
#: The one target keyed on less than the whole workspace, and the exact globs that
#: earn it: the workspace with its documentation removed, and nothing else.
CODE_SCOPED = "test"
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


def _resolve(entries: list, named: dict[str, list]) -> list[str]:
    """Expand named inputs into the concrete file globs Nx will hash."""
    globs: list[str] = []
    for entry in entries:
        if isinstance(entry, dict):
            continue  # an env/runtime input contributes no file coverage
        if entry in named:
            globs.extend(_resolve(named[entry], named))
        else:
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


def _matches(glob: str, relative: str) -> bool:
    pattern = re.escape(glob).replace(r"\*\*/\*", ".*").replace(r"\*", "[^/]*")
    return re.fullmatch(pattern, relative) is not None


def _covers(globs: list[str], relative: str) -> bool:
    """Apply Nx's file-set semantics: a later `!` glob removes what an earlier one added."""
    included = any(_matches(glob, relative) for glob in globs if not glob.startswith("!"))
    excluded = any(_matches(glob[1:], relative) for glob in globs if glob.startswith("!"))
    return included and not excluded


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
    llmlint = _nx_config()["targetDefaults"]["lint-llm-diff"]["inputs"]
    assert llmlint[0] == WHOLE_WORKSPACE, (
        "the llmlint tier judges the whole workspace diff and shares this one key"
    )


def test_the_marker_that_routes_a_test_to_its_tier_means_the_same_thing_everywhere() -> None:
    """Reconcile the four places the marker name is independently written down.

    `reads_docs` names a routing decision, not a label: pytest registers it,
    `conftest.py` enforces it, and the two Nx targets select on it. Those four
    declarations are written separately and nothing else compares them, so a rename
    that missed one would leave a tier silently selecting nothing — and a `test`
    tier that ran the prose contracts anyway, keyed on a workspace without prose,
    is the false green this whole file exists to prevent.
    """
    manifest = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    registered = re.findall(r'^\s*"(\w+):', manifest, flags=re.MULTILINE)
    assert READS_DOCS_MARKER in registered, (
        f"pytest must register {READS_DOCS_MARKER!r} in [tool.pytest.ini_options] markers"
    )

    targets = json.loads((REPO_ROOT / "orchestrator/project.json").read_text(encoding="utf-8"))[
        "targets"
    ]
    assert f"-m 'not {READS_DOCS_MARKER}'" in targets[CODE_SCOPED]["command"]
    assert f"-m {READS_DOCS_MARKER}" in targets["test-docs"]["command"]

    # And the two selectors have to partition: a test is in exactly one tier.
    marked = re.search(r"-m '?(not )?(\w+)'?", targets["test-docs"]["command"])
    assert marked is not None and marked.group(1) is None


def test_the_code_only_test_key_is_the_workspace_with_its_prose_removed() -> None:
    """The one narrowed key is narrowed by exactly the documentation, and no further."""
    named = _nx_config()["namedInputs"]
    assert named[CODE_WORKSPACE] == CODE_WORKSPACE_GLOBS, (
        "orchestrator:test replays a verdict for every tracked path this key covers, "
        "so narrowing it past documentation would memoize a claim about code it never read"
    )

    project = json.loads((REPO_ROOT / "orchestrator/project.json").read_text(encoding="utf-8"))
    assert project["targets"][CODE_SCOPED]["inputs"] == [CODE_WORKSPACE]

    globs = _effective_inputs("orchestrator", CODE_SCOPED)
    missed = sorted(path for path in _tracked() if not _covers(globs, path))
    assert missed and all(_is_documentation(path) for path in missed), (
        f"only documentation may fall outside the code-only test key: {missed}"
    )
    # The prose tier is what covers the rest, so it must actually still exist.
    assert "test-docs" in project["targets"]


def test_every_repository_path_the_suite_reads_is_part_of_a_test_key() -> None:
    """A new test that reads a new path must not be able to replay a stale verdict."""
    globs = _effective_inputs("orchestrator", CODE_SCOPED)
    tracked = _tracked()
    read: set[str] = set()
    for source in sorted(REPO_ROOT.joinpath("tests").rglob("*.py")):
        text = source.read_text(encoding="utf-8")
        for match in re.finditer(r'(?:REPO_ROOT|ROOT)\s*/\s*"([^"]+)"(\s*/\s*"[^"]+")*', text):
            candidate = "/".join(re.findall(r'"([^"]+)"', match.group(0)))
            if candidate in tracked or any(path.startswith(f"{candidate}/") for path in tracked):
                read.add(candidate)

    # A guard that found nothing would pass silently forever.
    assert {"AGENTS.md", "justfile", "scripts/session-setup.sh"} <= read, read
    uncovered = sorted(path for path in read if not _covers(globs, path))
    assert all(_is_documentation(path) for path in uncovered), (
        "orchestrator:test reads these repository paths but is not keyed on them, "
        f"so a change to one replays a stale verdict: {uncovered}"
    )
    # Prose the suite reads is not exempt, only keyed elsewhere: `test-docs` is
    # keyed on the whole workspace, so it covers every path in `uncovered`.
    assert "AGENTS.md" in uncovered, (
        "the sentinel prose read moved; keep a real one here or this guard stops guarding"
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
        assert not _covers(globs, witness)
        assert (REPO_ROOT / witness).exists()

    code_globs = _effective_inputs("orchestrator", CODE_SCOPED)
    assert not _covers(code_globs, "AGENTS.md")
    assert _covers(code_globs, "justfile")
