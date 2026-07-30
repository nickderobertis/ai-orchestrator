"""A cached verdict may only stand in for a verdict on the tree it was keyed on.

Every cached Nx target memoizes an answer. Replaying one is sound exactly when the
key covers everything the check reads, and unsound the moment it does not: a file
the check reads but the key ignores is a tree that can change its answer without
changing its hash, which is a green verdict for a tree that would have failed.

`tests/e2e/test_nx_cache_scope_e2e.py` proves the behaviour against real Nx. These
assertions keep the declarations that behaviour rests on from narrowing again —
including as the suite grows new reads, which is how the subset drifted out of
date in the first place.
"""

from __future__ import annotations

import json
import re
import subprocess

from orchestrator import REPO_ROOT

WHOLE_WORKSPACE = "wholeWorkspace"

#: Every one of these runs from the workspace root against the whole tree — ruff
#: over `.`, shellcheck over `scripts/`, persona validation over `personas/`, and
#: pytest over a suite that reads documentation, recipes, hooks and app config.
WORKSPACE_SCOPED = ("test", "lint", "typecheck", "format-check")


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


def _covers(globs: list[str], relative: str) -> bool:
    for glob in globs:
        pattern = re.escape(glob).replace(r"\*\*/\*", ".*").replace(r"\*", "[^/]*")
        if re.fullmatch(pattern, relative):
            return True
    return False


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


def test_every_repository_path_the_suite_reads_is_part_of_the_test_key() -> None:
    """A new test that reads a new path must not be able to replay a stale verdict."""
    globs = _effective_inputs("orchestrator", "test")
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
    assert not uncovered, (
        "orchestrator:test reads these repository paths but is not keyed on them, "
        f"so a change to one replays a stale verdict: {uncovered}"
    )


def test_the_e2e_witness_still_names_a_path_outside_the_project_roots() -> None:
    """The journey's witness must stay a file no project-root glob would cover."""
    project_scoped = _resolve(["default"], _nx_config()["namedInputs"])
    globs = [glob.replace("{workspaceRoot}/", "") for glob in project_scoped]
    globs = [glob.replace("{projectRoot}/", "orchestrator/") for glob in globs]
    assert not _covers(globs, "AGENTS.md")
    assert (REPO_ROOT / "AGENTS.md").exists()
