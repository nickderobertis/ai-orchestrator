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

from orchestrator import REPO_ROOT

#: The key `orchestrator:test` and `orchestrator:test-serial` are memoized on: the
#: workspace minus its prose and minus the front-end projects no Python test reads.
CODE_WORKSPACE = "codeWorkspace"
#: The key `orchestrator:test-recipes` is memoized on: what the recipe journeys
#: drive, plus the modules that define and collect them.
RECIPE_WORKSPACE = "recipeWorkspace"
#: The key `workspace:check-nx-cache` is memoized on: what that script reads.
NX_CACHE_CHECK = "nxCacheCheck"

#: The tier that runs the bulk of the Python suite across xdist workers.
CODE_SCOPED = "test"
#: The tier that runs the `single_threaded` tests, in a process carrying no execnet
#: receiver thread. Split out of `test` so it blocks nothing, and keyed on the same
#: `codeWorkspace`: a different process shape, not a different tree.
SERIAL_SCOPED = "test-serial"
#: The tier that runs the tests asserting on this repository's own prose.
DOCS_SCOPED = "test-docs"
#: The tier that drives this repository's `just` recipes and shell scripts.
RECIPE_SCOPED = "test-recipes"
#: The uncached tier that combines every measuring tier's coverage data and
#: enforces the declared floor against the combined total. Deliberately unmemoized:
#: it is seconds of work, and a floor that always runs is one no replay can skip.
COVERAGE_SCOPED = "coverage"


def nx_config() -> dict:
    return json.loads((REPO_ROOT / "nx.json").read_text(encoding="utf-8"))


def named_input_globs(name: str, *, project_root: str = "") -> list[str]:
    """Expand one named input into repository-relative globs.

    Named inputs may reference other named inputs, and non-file entries (an `env`
    or `runtime` input) contribute no file coverage at all, so they are dropped
    rather than treated as paths.
    """
    named = nx_config()["namedInputs"]

    def resolve(entries: list) -> list[str]:
        globs: list[str] = []
        for entry in entries:
            match entry:
                case dict():
                    pass  # an env or runtime input contributes no file coverage
                case _ if entry in named:
                    globs.extend(resolve(named[entry]))
                case _:
                    globs.append(entry)
        return globs

    return [
        glob.replace("{workspaceRoot}/", "").replace("{projectRoot}/", f"{project_root}/")
        for glob in resolve(named[name])
    ]


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
