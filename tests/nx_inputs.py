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
#: The key `dag-ui:test` is memoized on: what the journeys over `just dag-ui` and
#: `just telemetry-server` drive and read — those two recipes and the scripts they
#: reach, the address both resolve each other through, the pins that decide which
#: published bundle and reader are installed, and the recorded runs they render.
#: Named file by file rather than by directory, and deliberately not `config/**/*`,
#: `scripts/**/*` or `orchestrator/**/*`: these are the only journeys here that launch
#: a real browser, so every glob wider than what they actually open makes an unrelated
#: edit pay for one. `orchestrator/root.py` is deliberately *not* in it, though the
#: module imports `REPO_ROOT` from there: it is a path helper nearly everything imports,
#: so covering it would start a browser for edits that cannot change what these journeys
#: observe, and `codeWorkspace` already reruns the rest of the suite over it. Its own
#: prose is excluded, as
#: `codeWorkspace` excludes the workspace's: nothing here reads this project's
#: `AGENTS.md`, so an instruction-only edit must not start a browser.
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
#: recipes those repositories define — and the installed producer whose wire format
#: `scripts/channel-serve.py` reads.
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
#: real `scripts/ask-manager.sh`, the real `onepipeline channel serve` it asks through,
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

#: The project whose test target owns the journeys that render the DAG Observatory in a
#: real browser. A project of its own for the reason `plan-tooling` and `ask-seam` are:
#: a real browser is a cost `nx affected` can only keep off an unrelated edit where it is
#: a separate project, and while these journeys sat in the orchestrator project every
#: change in the repository launched one. It declares no Python distribution either.
DAG_UI_PROJECT = "dag-ui"
#: That project's one test target. One rather than two, as `ask-seam` has one: nothing
#: here reads this repository's prose.
DAG_UI_SCOPED = "test"
#: The directory it owns, which every other project's tiers ignore. Path-selected, as
#: `PLAN_TOOLING_ROOT` is: a file added here joins this project by being here.
DAG_UI_ROOT = "tests/dag_ui"

#: The uncached tier that reads the measuring tier's coverage data and enforces the
#: declared floor against it. Deliberately unmemoized:
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
