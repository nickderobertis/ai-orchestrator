"""E2E proof that a cached test verdict is keyed on the tree it judged.

The llmlint tier is not the only one whose verdict is memoized: every cached Nx
target replays a recorded answer, and `orchestrator:test` — the tier the coverage
and correctness invariants rest on — is one of them. A memo is only sound when its
key covers everything the check reads, and this suite reads far beyond
`orchestrator/` and `tests/`: it asserts on `AGENTS.md`, `docs/`, the `justfile`,
`llmlint.yml`, `scripts/`, and `.githooks/pre-push`. Keyed on a hand-listed
subset, a change to any of those replayed a green verdict for a tree whose tests
would have failed had they run.

The suite answers at three scopes, so it is keyed at three. Only a handful of tests
assert on this repository's prose, and charging every documentation edit eight
minutes for the rest bought nothing: `orchestrator:test-docs` runs those and keeps
the whole-workspace key. Narrower again, the two costliest journeys in the suite
build real worktrees and run real package installs to drive `just` recipes and
shell scripts, and read no prose and no `orchestrator/` at all:
`orchestrator:test-recipes` runs those under a key of exactly what they drive.
`orchestrator:test` runs the remainder, keyed on the workspace minus its
documentation. Every half of that claim is load bearing — each key must still
invalidate on what its tier reads, and must still replay on what it does not — so
each is proved here, along with the
cross-worktree cache check `just check` now replays through Nx as well, and the
uncached `orchestrator:coverage` step that turns what those tiers measured into
the one comparison against the floor.

These journeys drive the real `nx.json`, the real `orchestrator/project.json`
declarations, and the real `scripts/nx.sh` against a throwaway copy of this
checkout, and assert on Nx's own cache accounting.

`PYTEST_ADDOPTS` shortens the suite the target runs to collection only. That is
faithful rather than convenient: the claim under test is about the *key*, which is
computed from the declared inputs before the command runs and deliberately does
not include ambient environment. Running the whole suite three times would prove
the same thing about the same hashes, twenty-two minutes more slowly.

llmlint: ignore-file[e2e_not_mocked] Nothing is faked here. The only substitution
shortens what a real target *runs* without touching the declared inputs these
journeys are about: `PYTEST_ADDOPTS` for the Python tiers.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] Every journey here
copies the tracked tree, so no key narrower than the whole workspace could describe
one: a project of their own would carry `wholeWorkspace` too and be selected by every
diff exactly as this file's tier already is, leaving no edge to stay behind.

llmlint: ignore-file[shell_test_tiers_stay_split] The same claim as above about the
same journeys: a project scoped to the scripts these drive would still have to hash the
tree they copy.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] A marker tier keyed on
the whole workspace is this repository's own answer for a cost no project boundary can
narrow, and it is what collects every journey in this file rather than only the ones
this change adds.

llmlint: ignore-file[tests_mirror_real_usage] The user-facing surface over these is
`just check`, which cannot be driven from inside the suite it runs; the real recipe,
the real `scripts/nx-selection.sh`, and the invocations it hands Nx are driven in
`tests/e2e/test_workspace_contract_e2e.py`. These journeys take the other half —
what real Nx selects for that same selection string — and take it through the same
script rather than restating its arguments.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
from nx_inputs import (
    ASK_SEAM_PROJECT,
    ASK_SEAM_SCOPED,
    CHECKOUT_SCOPED,
    CODE_SCOPED,
    COVERAGE_SCOPED,
    DAG_UI_PROJECT,
    DAG_UI_SCOPED,
    DOCS_SCOPED,
    PLAN_STORE_INSTALL_PROJECT,
    PLAN_STORE_INSTALL_SCOPED,
    RECIPE_SCOPED,
    RUN_END_HOOKS_PROJECT,
    RUN_END_HOOKS_SCOPED,
    SELECTED_TARGETS,
    UNWATCHED_PROJECT,
    UNWATCHED_SCOPED,
    WRITEBACK_BUDGET_PROJECT,
    WRITEBACK_BUDGET_SCOPED,
    covers,
    matches,
    repository_relative_globs,
    resolve_input_globs,
)
from nx_workspace import WORKSPACE_INSTALL_MARKS, copy_checkout

from orchestrator.root import REPO_ROOT

# Copying the whole tree is this journey's premise, and the tree includes its
# prose: these belong to the whole-workspace tier by construction.
pytestmark = [*WORKSPACE_INSTALL_MARKS, pytest.mark.reads_docs]

CACHE_HIT = "read the output from the cache"
# The suite reads this file directly — tests/test_decomposition_guidance.py holds the
# manager's half of the doctrine to it — and it lives outside every project root.
PROSE_WITNESS = "AGENTS.md"
PROSE_TEXT = "Skip busywork."
PROSE_EDIT = "Skip busywork, and say which you skipped."
# Also read directly, also outside every project root, and deliberately not prose:
# this is what proves the narrowed key was narrowed by documentation alone. It is
# also the recipe tier's own witness, since driving it is what that tier is for.
CODE_WITNESS = "justfile"
CODE_TEXT = "# List available recipes."
CODE_EDIT = "# List the available recipes."
#: Python the code tier reads and the recipe tier does not: editing it must re-run
#: one and replay the other, which is the whole reason the recipe key is narrow.
PYTHON_WITNESS = "orchestrator/labels.py"
#: The fixture `scripts/check-nx-cache.sh` builds its two linked worktrees from.
FIXTURE_WITNESS = "tests/fixtures/nx-cache/src/index.ts"
#: The project every Python tier belongs to, and the tier the code suite runs in.
PROJECT = "orchestrator"
CODE_TIER = f"{PROJECT}:{CODE_SCOPED}"
#: The uncached tier that reads what that one measured and judges the floor.
COVERAGE_TIER = f"{PROJECT}:{COVERAGE_SCOPED}"
#: What every journey below shortens the Python suite to. The claim under test is
#: the *key*, computed from the declared inputs before the command runs.
COLLECT_ONLY = "--collect-only --no-cov -q"
#: The coverage journey keeps measurement on, because a restored data file is what
#: it is about; only the selection is shortened.
COLLECT_ONLY_MEASURED = "--collect-only -q"
#: Where this host publishes the resolved fixture lockfile that check shares. The
#: journeys below run under an isolated `XDG_CACHE_HOME`, so the resolution is
#: carried across rather than paid again — sharing a cache is what it is for.
SHARED_FIXTURE_RESOLUTIONS = Path("ai-orchestrator") / "nx-cache-fixture"


@dataclass(frozen=True)
class Checkout:
    """A copy of this repository wired to its own isolated Nx cache."""

    root: Path
    cache: Path

    def run(
        self, target: str, *nx_args: str, addopts: str = COLLECT_ONLY
    ) -> subprocess.CompletedProcess[str]:
        """Run ``target`` through the real wrapper and hand back what Nx reported."""
        # Cache replay is this journey's whole claim, so an ambient global cache skip
        # is dropped for the same reason scripts/check-nx-cache.sh drops it:
        # `--skip-nx-cache` on one invocation is the supported way to force one tier
        # to re-run, and a global export would silently answer a different question.
        skips = {"NX_SKIP_NX_CACHE", "NX_DISABLE_NX_CACHE"}
        ambient = {key: value for key, value in os.environ.items() if key not in skips}
        return subprocess.run(
            ["./scripts/nx.sh", "run", target, *nx_args],
            cwd=self.root,
            env={
                **ambient,
                "XDG_CACHE_HOME": str(self.cache),
                "AI_ORCHESTRATOR_NX_SHOW_OUTPUT": "1",
                # Reuse this checkout's already-synced environment rather than
                # building the copy as a distinct project.
                "UV_NO_SYNC": "1",
                "UV_PROJECT_ENVIRONMENT": str(REPO_ROOT / ".venv"),
                "PYTEST_ADDOPTS": addopts,
            },
            check=False,
            text=True,
            capture_output=True,
        )

    def ran_the_command(self, target: str, *nx_args: str, addopts: str = COLLECT_ONLY) -> bool:
        """Run ``target`` through the real wrapper; False when Nx replayed a verdict."""
        result = self.run(target, *nx_args, addopts=addopts)
        assert result.returncode == 0, result.stdout + result.stderr
        return CACHE_HIT not in result.stdout

    def resolved_project(self, project: str = PROJECT) -> dict:
        """Ask Nx itself how one project resolves, `targetDefaults` merged in."""
        result = subprocess.run(
            ["./scripts/nx.sh", "show", "project", project, "--json"],
            cwd=self.root,
            env={
                **os.environ,
                "XDG_CACHE_HOME": str(self.cache),
                "AI_ORCHESTRATOR_NX_SHOW_OUTPUT": "1",
            },
            check=False,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        rendered = next(line for line in result.stdout.splitlines() if line.startswith("{"))
        return json.loads(rendered)

    def resolved_target(self, target: str) -> dict:
        """Ask Nx itself how one target resolves, defaults and all.

        `project.json` states part of a target and `nx.json`'s `targetDefaults`
        states the rest, and only Nx merges them. A `targetDefaults` key that no
        longer matches a target name is silently inert — the target simply falls
        back to Nx's own defaults, which for `cache` means *not caching*, and for a
        tier meant to be cached that is a quiet loss rather than an error.
        """
        return self.resolved_project()["targets"][target]

    def edit(self, relative: str, old: str, new: str) -> None:
        path = self.root / relative
        text = path.read_text(encoding="utf-8")
        assert old in text, f"{relative} no longer contains the text this journey edits"
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def append(self, relative: str, line: str) -> None:
        """Change a file's content without changing what it means.

        Some witnesses are read by the very checks these journeys run — the Nx
        cache fixture is typechecked — so the edit has to move the hash and nothing
        else.
        """
        path = self.root / relative
        assert path.is_file(), f"{relative} is no longer a file this journey can witness"
        path.write_text(f"{path.read_text(encoding='utf-8')}{line}\n", encoding="utf-8")


@pytest.fixture
def checkout(tmp_path: Path) -> Checkout:
    root = tmp_path / "checkout"
    copy_checkout(root)
    # Nx resolves its workspace and its ignore rules from git, and scripts/nx.sh
    # derives the shared cache key from the repository identity.
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    cache = tmp_path / "cache"
    host = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    if (host / SHARED_FIXTURE_RESOLUTIONS).is_dir():
        shutil.copytree(host / SHARED_FIXTURE_RESOLUTIONS, cache / SHARED_FIXTURE_RESOLUTIONS)
    return Checkout(root=root, cache=cache)


def test_a_workspace_file_the_suite_reads_invalidates_the_cached_test_verdict(
    checkout: Checkout,
) -> None:
    """A tree the suite would judge differently cannot replay the recorded verdict."""
    assert checkout.ran_the_command("orchestrator:test")
    assert not checkout.ran_the_command("orchestrator:test"), (
        "an unchanged tree must replay its recorded verdict rather than re-run"
    )

    checkout.edit(CODE_WITNESS, CODE_TEXT, CODE_EDIT)

    assert checkout.ran_the_command("orchestrator:test"), (
        f"changing {CODE_WITNESS} must re-run the suite that reads it"
    )


def test_an_originless_checkout_does_not_mint_an_unused_cache_directory(
    checkout: Checkout,
) -> None:
    """A metadata-only Nx query must not leave an empty cache key behind."""
    repo_key = hashlib.sha256(str(checkout.root).encode()).hexdigest()[:16]
    cache_directory = checkout.cache / "ai-orchestrator" / "nx" / repo_key

    checkout.resolved_target("test")

    assert not cache_directory.exists(), (
        "the wrapper created a repository-key directory even though Nx stored no result"
    )


def test_editing_prose_re_runs_only_the_tier_that_reads_prose(checkout: Checkout) -> None:
    """The whole point of the split: prose invalidates the prose tier and nothing else."""
    assert checkout.ran_the_command("orchestrator:test")
    assert checkout.ran_the_command("orchestrator:test-docs")

    checkout.edit(PROSE_WITNESS, PROSE_TEXT, PROSE_EDIT)

    assert checkout.ran_the_command("orchestrator:test-docs"), (
        f"changing {PROSE_WITNESS} must re-run the tests that assert on it"
    )
    assert not checkout.ran_the_command("orchestrator:test"), (
        f"changing {PROSE_WITNESS} must not re-run a tier whose tests cannot read it"
    )


def test_editing_code_re_runs_both_tiers(checkout: Checkout) -> None:
    """Narrowing one key by documentation must not narrow it by anything else."""
    assert checkout.ran_the_command("orchestrator:test")
    assert checkout.ran_the_command("orchestrator:test-docs")

    checkout.edit(CODE_WITNESS, CODE_TEXT, CODE_EDIT)

    assert checkout.ran_the_command("orchestrator:test")
    assert checkout.ran_the_command("orchestrator:test-docs")


def test_skip_nx_cache_forces_one_tier_to_re_run_an_unchanged_tree(checkout: Checkout) -> None:
    """The documented lever for a suspect verdict is per tier and per invocation."""
    assert checkout.ran_the_command("orchestrator:test")
    assert not checkout.ran_the_command("orchestrator:test")

    assert checkout.ran_the_command("orchestrator:test", "--skip-nx-cache")

    # And only that invocation: the next one replays again, so nothing an operator
    # does to re-run one tier leaves the rest of the workspace re-running forever.
    assert not checkout.ran_the_command("orchestrator:test")


def test_the_coverage_tier_resolves_as_an_unmemoized_step_after_the_tier_that_measures(
    checkout: Checkout,
) -> None:
    """The floor is enforced by a task Nx will never replay.

    Its input is a file on disk rather than the tree, so a cached verdict here would
    be a claim about coverage data Nx does not hash. It is seconds of work; it
    re-runs. And it must wait on the tier that measures, or the total it judges is a
    total this suite never produced.
    """
    resolved = checkout.resolved_target(COVERAGE_SCOPED)

    assert resolved.get("cache") is False, resolved
    assert sorted(resolved["dependsOn"]) == [CODE_SCOPED], resolved
    for tier in (CODE_SCOPED,):
        measuring = checkout.resolved_target(tier)
        assert measuring.get("cache") is True, (
            f"{tier} measures into a file the coverage tier needs restored on a cache "
            f"hit, so it has to be cached with that file as its output: {measuring}"
        )
        data_file = measuring["outputs"][0].removeprefix("{workspaceRoot}/")
        assert data_file in resolved["options"]["command"], (
            f"{tier} writes {data_file} and the coverage tier never reads it: {resolved}"
        )


def test_the_checkout_tier_re_runs_even_when_nothing_in_the_tree_moved(
    checkout: Checkout,
) -> None:
    """The tier that reads other repositories' checkouts is never replayed.

    Every other memoized tier here is sound because its key covers what it reads.
    This one reads registered checkouts of *other* repositories, which live outside
    the workspace entirely — no `nx.json` glob can name one — so there is no key that
    would be right and it is declared uncached instead. Proven the way the property
    matters: run it twice against a tree nothing touched, and it has to run twice.
    A replay here would report on those repositories as they were when it was
    recorded, which is exactly the drift it exists to catch.
    """
    resolved = checkout.resolved_target(CHECKOUT_SCOPED)
    assert resolved.get("cache") is False, resolved

    assert checkout.ran_the_command(f"orchestrator:{CHECKOUT_SCOPED}")
    assert checkout.ran_the_command(f"orchestrator:{CHECKOUT_SCOPED}"), (
        "an unchanged tree replayed the checkout-reconciliation tier, so its verdict "
        "is a memo about repositories that have gone on changing since"
    )


def test_editing_a_recipe_input_re_runs_the_recipe_tier(checkout: Checkout) -> None:
    """The narrow key must still notice everything the recipe journeys drive."""
    assert checkout.ran_the_command("orchestrator:test-recipes")
    assert not checkout.ran_the_command("orchestrator:test-recipes"), (
        "an unchanged tree must replay its recorded verdict rather than re-run"
    )

    checkout.edit(CODE_WITNESS, CODE_TEXT, CODE_EDIT)

    assert checkout.ran_the_command("orchestrator:test-recipes"), (
        f"changing {CODE_WITNESS} must re-run the journeys that drive it"
    )


def test_editing_orchestrator_code_replays_only_the_recipe_tier(checkout: Checkout) -> None:
    """The whole point of the third tier: Python churn stops paying for Bun and Nx.

    The two costliest journeys in this suite build real worktrees and run real
    package installs, and neither reads a line of `orchestrator/`. Most commits
    here touch nothing else, so this is the case that has to replay.
    """
    assert checkout.ran_the_command("orchestrator:test")
    assert checkout.ran_the_command("orchestrator:test-recipes")

    checkout.append(PYTHON_WITNESS, "# nx cache scope journey")

    assert checkout.ran_the_command("orchestrator:test"), (
        f"changing {PYTHON_WITNESS} must re-run the tier keyed on the code"
    )
    assert not checkout.ran_the_command("orchestrator:test-recipes"), (
        f"changing {PYTHON_WITNESS} must not re-run journeys that cannot read it"
    )


def test_a_replayed_test_verdict_restores_the_coverage_data_the_floor_needs(
    checkout: Checkout,
) -> None:
    """A cache hit has to hand the floor the measurement it stood in for.

    The measuring tier is cached and the tier that judges the floor is not, so on
    every replayed commit the combine runs against data no run in this checkout
    produced. That only works because the measuring tier declares its data file as
    an Nx output and Nx restores it — an `outputs` declaration that fell off, or a
    tier that stopped writing the file it names, would leave the combine with
    nothing and turn a saving into a failed gate.

    `PYTEST_ADDOPTS` shortens the selection but, unlike every journey above, leaves
    measurement on: a restored data file is the whole subject. The floor itself is
    lowered in this copy for the same reason — a collect-only selection covers a
    fraction of the package, and `tests/test_coverage_gate.py` is where the declared
    floor's own value is proven at the real boundary.
    """
    checkout.edit("pyproject.toml", "fail_under = 100", "fail_under = 0")
    measured = {CODE_TIER: ".coverage.parallel"}
    for tier in measured:
        assert checkout.ran_the_command(tier, addopts=COLLECT_ONLY_MEASURED)
    for tier, data_file in measured.items():
        assert (checkout.root / data_file).is_file(), f"{tier} measured nothing to combine"
        (checkout.root / data_file).unlink()

    for tier, data_file in measured.items():
        assert not checkout.ran_the_command(tier, addopts=COLLECT_ONLY_MEASURED), (
            f"{tier} re-ran on an unchanged tree, so this proves nothing about replay"
        )
        assert (checkout.root / data_file).is_file(), (
            f"{tier} replayed its verdict without restoring {data_file}, so the floor "
            "is combined from data the cache dropped"
        )

    combined = checkout.run(COVERAGE_TIER, addopts=COLLECT_ONLY_MEASURED)

    assert combined.returncode == 0, combined.stdout + combined.stderr
    assert CACHE_HIT in combined.stdout, (
        f"{COVERAGE_TIER} re-ran a measuring tier, so the combine never saw a replay"
    )
    assert "TOTAL" in combined.stdout, combined.stdout + combined.stderr


def test_the_cross_worktree_cache_check_replays_until_its_own_fixture_moves(
    checkout: Checkout,
) -> None:
    """`just check` runs this through Nx, so it must replay and it must still miss.

    Two real linked worktrees and two real installs is around forty seconds cold,
    charged to every commit while it ran as a plain shell line. It reads its
    fixture, three scripts, and the root manifest — nothing else — so prose has to
    replay and the fixture has to miss.
    """
    assert checkout.ran_the_command("workspace:check-nx-cache")
    assert not checkout.ran_the_command("workspace:check-nx-cache"), (
        "an unchanged tree must replay this check rather than rebuild both worktrees"
    )

    checkout.edit(PROSE_WITNESS, PROSE_TEXT, PROSE_EDIT)

    assert not checkout.ran_the_command("workspace:check-nx-cache"), (
        f"changing {PROSE_WITNESS} must not rebuild a check that cannot read it"
    )

    checkout.append(FIXTURE_WITNESS, "// nx cache scope journey")

    assert checkout.ran_the_command("workspace:check-nx-cache"), (
        f"changing {FIXTURE_WITNESS} must re-run the check built out of it"
    )


#: Where the wrapper keys the native cache, under whatever `XDG_CACHE_HOME` names.
#: Restated from `scripts/nx.sh` because it is shell; the journeys below also assert
#: `$TMPDIR` holds no per-root directory, so a wrapper that set nothing still fails.
NATIVE_CACHE = Path("ai-orchestrator") / "nx-native"
#: What Nx names a native cache directory when nothing redirects it: one per workspace
#: root, which on this host is one per dispatch. Its absence is the point.
PER_ROOT_NATIVE_CACHE = "nx-native-file-cache-*"
#: An origin two distinct worktrees can share. Any URL; the wrapper hashes it.
SHARED_ORIGIN = "https://example.invalid/nickderobertis/ai-orchestrator.git"


def _native_cache_keys(cache: Path) -> list[str]:
    """Every per-origin native cache directory holding a copied Nx native module."""
    root = cache / NATIVE_CACHE
    if not root.is_dir():
        return []
    return sorted(entry.name for entry in root.iterdir() if any(entry.glob("*.node")))


@pytest.fixture
def scratch() -> Iterator[Path]:
    """A `$TMPDIR` of this journey's own, and a deliberately short one.

    Short because Nx opens plugin sockets under it and a Unix socket address is capped
    at 108 bytes on Linux: a scratch root under `tmp_path` is long enough on its own to
    fail every Nx invocation with *"Attempted to open socket that exceeds the maximum
    socket length"*, which says nothing about the caches these journeys are for.
    """
    root = Path(tempfile.mkdtemp(prefix="nxs-"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _nx_metadata_query(root: Path, cache: Path, scratch: Path) -> subprocess.CompletedProcess[str]:
    """One real Nx invocation, with both cache roots and `$TMPDIR` of this journey's own.

    A metadata query is enough and is the point: loading the native module is what
    mints a native cache directory, and it happens on every invocation whether or not
    a task runs.
    """
    scratch.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        ["./scripts/nx.sh", "show", "project", PROJECT, "--json"],
        cwd=root,
        env={
            **os.environ,
            "XDG_CACHE_HOME": str(cache),
            "TMPDIR": str(scratch),
            "UV_NO_SYNC": "1",
            "UV_PROJECT_ENVIRONMENT": str(REPO_ROOT / ".venv"),
        },
        check=False,
        text=True,
        capture_output=True,
    )


def _worktree_of(origin: str, root: Path) -> Path:
    """A copy of this checkout that reports ``origin`` as its own repository identity."""
    copy_checkout(root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "remote", "add", "origin", origin], cwd=root, check=True)
    return root


def test_two_worktrees_of_one_origin_share_one_native_cache_directory(
    tmp_path: Path, scratch: Path
) -> None:
    """The 22 MB native module is copied once per origin, not once per workspace root.

    Nx names that directory from the hash of the workspace root, so on a host where
    every dispatch works in a fresh worktree it mints a new one — 22 MB — per dispatch,
    under `$TMPDIR`, and removes none: 617 of them at 12.8 GiB were measured here with
    not one older than a day. Neither published sweeper owns that family, so `just
    sweep` reclaimed 0 B while the device filled and a driver died mid-supervision.

    Both halves are asserted because either alone passes for the wrong reason. That the
    two worktrees share one directory is not enough on its own — a wrapper that failed
    to set the variable and left Nx to its own default would still put both under
    `$TMPDIR` — so the scratch root each invocation ran with is read back and required
    to hold no per-root directory at all.
    """
    cache = tmp_path / "cache"
    first = _worktree_of(SHARED_ORIGIN, tmp_path / "first")
    second = _worktree_of(SHARED_ORIGIN, tmp_path / "second")

    for root in (first, second):
        queried = _nx_metadata_query(root, cache, scratch)
        assert queried.returncode == 0, queried.stdout + queried.stderr

    assert len(_native_cache_keys(cache)) == 1, (
        "two worktrees of one origin left "
        f"{_native_cache_keys(cache)} native cache directories; the whole point of "
        "keying this on the repository identity is that they share one"
    )
    assert not list(scratch.glob(PER_ROOT_NATIVE_CACHE)), (
        "Nx minted its own per-workspace-root native cache under the scratch root "
        f"anyway: {sorted(entry.name for entry in scratch.glob(PER_ROOT_NATIVE_CACHE))}. "
        "That is the unbounded family this redirection exists to end"
    )


def test_two_originless_checkouts_do_not_share_a_native_cache_directory(
    tmp_path: Path, scratch: Path
) -> None:
    """An originless copy is not a repository identity, and must not be grouped as one.

    The same guarantee the computation cache already gives: copies belonging to e2e
    journeys have no origin, they fall back to their own top-level path, and two of
    them are two different trees rather than one repository measured twice.
    """
    cache = tmp_path / "cache"
    for name in ("first", "second"):
        root = tmp_path / name
        copy_checkout(root)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        queried = _nx_metadata_query(root, cache, scratch)
        assert queried.returncode == 0, queried.stdout + queried.stderr

    assert len(_native_cache_keys(cache)) == 2, (
        "two originless checkouts resolved to "
        f"{_native_cache_keys(cache)}; a checkout with no origin is not a repository "
        "identity and must not be silently grouped with another"
    )


#: A script the recipe journeys drive and neither the prose nor the code tier reads, so
#: a diff of it is the "scripts" half of a scripts-and-recipes change.
SCRIPT_WITNESS = "scripts/nx-selection.sh"
#: The remote each copy publishes its base to. Never fetched — `scripts/comparison-base.sh`
#: reads the remote-tracking ref this fixture writes — so it only has to be a remote.
SELECTOR_ORIGIN = "https://example.invalid/nx-selection.git"
#: The name a planted witness wears: a stem this repository has nowhere, so a probe
#: file changes exactly the fileset it was planted for and nothing that already exists.
PROBE_STEM = "nx-selection-probe"
PROBE = f"{PROBE_STEM}.txt"
#: The filesets a diff-selected tier is keyed on that no diff can carry: git ignores
#: everything under them, and a change git does not report is one `nx affected` never
#: sees. Named rather than skipped in silence, so a key that starts covering an ignored
#: directory arrives as a failure here instead of as a hole in the measurement below.
UNDIFFABLE_GLOBS = frozenset({"{workspaceRoot}/scratch/**/*"})


def _comparison_overrides() -> frozenset[str]:
    """The environment names `scripts/comparison-base.sh` resolves its base through.

    The copy each journey narrows resolves its own base, so whatever the enclosing
    dispatch exported for *this* checkout is dropped: `onevcs` names one for the gate a
    lifecycle runs, and a copy asked to narrow against a branch it holds no
    remote-tracking ref for refuses — which would leave these journeys answering to
    their environment rather than to the selector. Read out of that script rather than
    restated here, because it owns which spellings win and a copy would go stale
    exactly when a new one was added.
    """
    source = (REPO_ROOT / "scripts/comparison-base.sh").read_text(encoding="utf-8")
    names = frozenset(re.findall(r"\b([A-Z][A-Z_]*_COMPARISON_(?:REMOTE|BASE))\b", source))
    assert len(names) >= 2, (
        f"scripts/comparison-base.sh names {sorted(names)} as its base overrides, which "
        "is too few to be the contract it documents; these journeys would then inherit "
        "one and narrow against a ref their copy does not have"
    )
    return names


#: Every tier a prose-only diff leaves out, which is the saving stated rather than
#: implied: each belongs to a project whose key carries no prose at all, which is why
#: it is skippable and why skipping it is equivalent to replaying it. The journey
#: derives the same set from the graph, so a target added to one of these projects
#: fails here instead of quietly joining what a documentation push stops running.
SKIPPABLE_TIERS = frozenset(
    {
        (ASK_SEAM_PROJECT, ASK_SEAM_SCOPED),
        (DAG_UI_PROJECT, DAG_UI_SCOPED),
        (PLAN_STORE_INSTALL_PROJECT, PLAN_STORE_INSTALL_SCOPED),
        (UNWATCHED_PROJECT, UNWATCHED_SCOPED),
        (WRITEBACK_BUDGET_PROJECT, WRITEBACK_BUDGET_SCOPED),
        (RUN_END_HOOKS_PROJECT, RUN_END_HOOKS_SCOPED),
    }
)


@dataclass(frozen=True)
class Selector(Checkout):
    """A checkout `just check`'s own selector can narrow, and a diff for it to narrow."""

    def _show(self, *args: str) -> set[str]:
        result = subprocess.run(
            ["./scripts/nx.sh", "show", "projects", "--json", *args],
            cwd=self.root,
            env={
                **os.environ,
                "XDG_CACHE_HOME": str(self.cache),
                "AI_ORCHESTRATOR_NX_SHOW_OUTPUT": "1",
                "UV_NO_SYNC": "1",
                "UV_PROJECT_ENVIRONMENT": str(REPO_ROOT / ".venv"),
            },
            check=False,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        rendered = next(line for line in result.stdout.splitlines() if line.startswith("["))
        return set(json.loads(rendered))

    def projects(self) -> set[str]:
        return self._show()

    def owners(self, target: str) -> set[str]:
        """Every project declaring ``target``, read from the graph rather than listed."""
        return self._show("--with-target", target)

    def deterministic_tiers(self, projects: set[str]) -> set[tuple[str, str]]:
        """Which of the deterministic tier's diff-selected targets ``projects`` declare."""
        return {
            (project, target)
            for project in projects
            for target in self.resolved_project(project)["targets"]
            if target in SELECTED_TARGETS
        }

    def selection(self) -> list[str]:
        """The Nx arguments `just check` would narrow with, from the script that decides.

        Reading them here rather than restating them is what keeps these journeys about
        the selection the recipe makes: a `scripts/nx-selection.sh` that stopped
        narrowing, or narrowed against a different ref, moves every answer below.
        """
        result = subprocess.run(
            ["./scripts/nx-selection.sh"],
            cwd=self.root,
            env={
                key: value
                for key, value in os.environ.items()
                if key not in _comparison_overrides()
            },
            check=False,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.split()

    def keyed_filesets(self) -> dict[str, set[tuple[str, str]]]:
        """Every fileset the diff-selected tiers are keyed on, and which tiers declare it.

        The inputs are Nx's own resolution of each project, so a fileset a
        `targetDefaults` entry supplies is read exactly as one a `project.json` states.
        Only the named-input expansion happens here, and that is a lookup in the same
        `nx.json` Nx just read.
        """
        named = json.loads((self.root / "nx.json").read_text(encoding="utf-8"))["namedInputs"]
        keyed: dict[str, set[tuple[str, str]]] = {}
        for project in sorted(self.projects()):
            resolved = self.resolved_project(project)
            root = resolved["root"]
            owned = "" if root in ("", ".") else f"{root}/"
            for target, declared in resolved["targets"].items():
                if target not in SELECTED_TARGETS:
                    continue
                for glob in resolve_input_globs(declared.get("inputs") or ["default"], named):
                    keyed.setdefault(glob.replace("{projectRoot}/", owned), set()).add(
                        (project, target)
                    )
        return keyed

    def project_roots(self) -> set[str]:
        """The directory each project owns, less the workspace root every path is under.

        Derived rather than listed: a project declared after this was written is one a
        diff has to be able to reach, and listing them is how it would stop being one.
        The root project is left out because the edit beside these already sits there.
        """
        roots = {self.resolved_project(project)["root"] for project in self.projects()}
        return {root for root in roots if root not in ("", ".")}

    def tracked(self) -> list[str]:
        """Every path git tracks here, in one stable order, so a witness is not a choice."""
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=self.root,
            check=True,
            text=True,
            capture_output=True,
        )
        return sorted(result.stdout.splitlines())

    def pending(self) -> set[str]:
        """Every path git reports as changed here, which is what `nx affected` reads."""
        result = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            cwd=self.root,
            check=True,
            text=True,
            capture_output=True,
        )
        return {line[3:] for line in result.stdout.splitlines()}

    @contextmanager
    def planted(self, witness: str) -> Iterator[bool]:
        """Make ``witness`` a change in this tree, and say whether git reports it.

        A file already there is changed rather than replaced, by one trailing newline:
        that moves its hash and leaves its content valid, which matters because Nx
        parses several of these while it answers.
        """
        path = self.root / witness
        original = path.read_bytes() if path.is_file() else None
        if original is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("probe\n", encoding="utf-8")
        else:
            path.write_bytes(original + b"\n")
        try:
            yield witness in self.pending()
        finally:
            if original is None:
                path.unlink()
            else:
                path.write_bytes(original)

    def selected(self, target: str | None = None) -> set[str]:
        """Which projects that selection picks for the working tree as it now stands.

        `nx affected` runs targets and `nx show projects` answers which projects it
        would run them for, spelling the same narrowing `--affected`; the base comes
        from the selection above rather than from this journey.
        """
        verb, *flags = self.selection()
        assert verb == "affected", (
            f"scripts/nx-selection.sh narrowed nothing here, answering {verb!r}; these "
            "journeys are about what it narrows to"
        )
        return self._show("--affected", *flags, *(("-t", target) if target else ()))


@pytest.fixture
def selector(checkout: Checkout) -> Selector:
    """`checkout` with its tree committed and published, which is what a diff is against.

    Published because the ref the selector resolves is the one the judged tier and the
    publishing push already use, so a copy with no remote-tracking base is a copy it
    correctly declines to narrow. The edits each journey then makes stay uncommitted,
    which is the state a worker's own gate runs in.
    """
    for args in (
        ("config", "user.name", "test"),
        ("config", "user.email", "test.invalid"),
        ("remote", "add", "origin", SELECTOR_ORIGIN),
        ("add", "-A"),
        ("-c", "commit.gpgsign=false", "commit", "-qm", "base"),
    ):
        subprocess.run(["git", *args], cwd=checkout.root, check=True, capture_output=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=checkout.root,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", base],
        cwd=checkout.root,
        check=True,
        capture_output=True,
    )
    return Selector(root=checkout.root, cache=checkout.cache)


def test_a_prose_only_diff_still_selects_every_tier_whose_subject_is_prose(
    selector: Selector,
) -> None:
    """The case the selector is least obviously safe in, and so the one to take first.

    Nothing that reads this repository's prose belongs to a project the prose sits in —
    `AGENTS.md` is in no project root at all — so a selector that picked projects by
    ownership would leave the prose tiers out of a documentation-only push and the gate
    would stop checking the very thing that changed.
    """
    selector.edit(PROSE_WITNESS, PROSE_TEXT, PROSE_EDIT)

    assert selector.selected(DOCS_SCOPED) == selector.owners(DOCS_SCOPED), (
        f"editing {PROSE_WITNESS} must select every project that runs a {DOCS_SCOPED} tier"
    )


def test_a_scripts_and_recipes_only_diff_still_selects_the_tier_that_drives_them(
    selector: Selector,
) -> None:
    """Neither witness is in a project root either, and the journeys that drive them are."""
    selector.edit(CODE_WITNESS, CODE_TEXT, CODE_EDIT)
    selector.append(SCRIPT_WITNESS, "# probe")

    assert selector.selected(RECIPE_SCOPED) == selector.owners(RECIPE_SCOPED), (
        f"editing {CODE_WITNESS} and {SCRIPT_WITNESS} must select every project that "
        f"runs a {RECIPE_SCOPED} tier"
    )


def test_a_code_only_diff_still_selects_the_code_tier_and_the_coverage_read(
    selector: Selector,
) -> None:
    """The floor is only enforceable where the tier that measures it was selected too."""
    selector.append(PYTHON_WITNESS, "# probe")

    assert PROJECT in selector.selected(CODE_SCOPED), (
        f"editing {PYTHON_WITNESS} must select the tier that runs over it"
    )
    assert selector.selected(COVERAGE_SCOPED) == selector.owners(COVERAGE_SCOPED), (
        "the read that judges the floor must be selected by a diff of the code it measures"
    )


def test_a_prose_only_diff_leaves_out_only_tiers_that_would_have_replayed(
    selector: Selector,
) -> None:
    """The narrowing this change is for, and the argument that makes it sound.

    The tiers that stop running are named rather than counted — derived from the graph
    and reconciled against the list above — and each is then driven against the edited
    tree: it replays, so not running it checks nothing less than running it would have.
    Recording both together is what separates a selector that is cheaper from one that
    merely checks less.
    """
    for project, target in SKIPPABLE_TIERS:
        assert selector.ran_the_command(f"{project}:{target}")

    selector.edit(PROSE_WITNESS, PROSE_TEXT, PROSE_EDIT)

    skipped = selector.projects() - selector.selected()
    assert selector.deterministic_tiers(skipped) == SKIPPABLE_TIERS, (
        f"a diff of {PROSE_WITNESS} alone stopped running "
        f"{sorted(selector.deterministic_tiers(skipped))}; the tiers that may stop "
        "running are the ones whose keys carry no prose"
    )
    for project, target in sorted(SKIPPABLE_TIERS):
        assert not selector.ran_the_command(f"{project}:{target}"), (
            f"{project}:{target} is left out of this diff's selection, and the licence "
            "for that is that it would have replayed a verdict recorded for this tree"
        )


def test_a_diff_reaching_every_project_leaves_none_of_them_out(selector: Selector) -> None:
    """Narrowing must never become skipping: touch them all and the tier runs them all."""
    selector.edit(CODE_WITNESS, CODE_TEXT, CODE_EDIT)
    for root in selector.project_roots():
        (selector.root / root / PROBE).write_text("probe\n", encoding="utf-8")

    assert selector.selected() == selector.projects(), (
        "a diff that reaches every project must leave the deterministic tier nothing to skip"
    )


def _witness_for(fileset: str) -> str:
    """The repository-relative path a change matching ``fileset`` would sit at.

    Two shapes, and a third fails rather than being guessed at: a literal path, and a
    `**/` glob whose tail names what the file is called. Guessing wrong would plant a
    witness the fileset does not cover, and the journey below would then report a tier
    as reachable on the strength of a path that never reaches it.
    """
    relative = fileset.removeprefix("!").replace("{workspaceRoot}/", "")
    if "*" not in relative:
        return relative
    prefix, separator, tail = relative.rpartition("**/")
    assert separator and "*" not in prefix and tail.count("*") == 1, (
        f"{fileset} is a shape these journeys cannot plant a witness for; teach "
        "_witness_for its shape rather than leaving the paths it covers unmeasured"
    )
    return f"{prefix}{tail.replace('*', PROBE_STEM)}"


def test_every_fileset_a_diff_selected_tier_is_keyed_on_still_selects_that_tier(
    selector: Selector,
) -> None:
    """The general form of the three journeys above: every fileset, not three shapes.

    Which projects Nx picks is decided by two locators at once — one reading the roots
    projects own, one reading the `{workspaceRoot}` filesets their targets declare —
    and no Nx contract promises either. So what this narrowing rests on is measured
    rather than argued: plant a change at each fileset a diff-selected tier is keyed
    on, ask the real selector, and require every tier that reads that path to be among
    the projects it picked. A release that stopped reading one of those locators drops
    a tier a diff reaches, and fails here.

    A `!` fileset is asked the same question, because Nx never extracts one: an
    exclusion cannot narrow what selects a project, and the journey after this one is
    what says the exclusion still buys the replay it was written for.
    """
    keyed = selector.keyed_filesets()
    # A tier keyed on nothing would be measured by nothing, and this journey would
    # then pass by asking about a graph it never read.
    assert {project for tiers in keyed.values() for project, _ in tiers} == {
        project for target in SELECTED_TARGETS for project in selector.owners(target)
    }, f"some project running a diff-selected target declared no fileset: {sorted(keyed)}"

    undiffable: set[str] = set()
    for fileset, tiers in sorted(keyed.items()):
        witness = _witness_for(fileset)
        with selector.planted(witness) as reported_by_git:
            if not reported_by_git:
                undiffable.add(fileset)
                continue
            selected = selector.selected()
        dropped = {project for project, _ in tiers} - selected
        assert not dropped, (
            f"a diff of {witness} alone left {sorted(dropped)} out of the selection, "
            f"and {sorted(tiers)} is keyed on {fileset} — so the deterministic tier "
            "would stop checking a path that changed"
        )

    assert undiffable == UNDIFFABLE_GLOBS, (
        f"{sorted(undiffable)} could not be measured because git reports no change "
        "under them; a fileset that leaves the measurement has to be one no diff can "
        "carry, and has to say so here"
    )


def test_a_path_a_tier_excludes_from_its_key_is_one_that_tier_replays_for(
    selector: Selector,
) -> None:
    """What a `!` fileset buys, given that it buys nothing from the selector.

    Nx extracts only the positive `{workspaceRoot}` filesets, so an exclusion cannot
    keep a project out of a selection — the journey above asks the excluded paths the
    same question every other path is asked, and they answer the same way. What an
    exclusion does buy is the other half, and it is the half this narrowing needs: the
    tier replays for a change at that path. So whether the selector picks that project
    or drops it, the verdict is the one already recorded for this tree either way, and
    an Nx that started honouring exclusions in its selection would take nothing away.

    Each tier is asked about a path its key *includes* first. That control is what
    makes the answer worth having: a tier that replayed for both would be one nothing
    had reached, and would report an exclusion working when the instrument was dead.
    """
    per_tier: dict[tuple[str, str], list[str]] = {}
    for fileset, tiers in selector.keyed_filesets().items():
        for tier in tiers:
            per_tier.setdefault(tier, []).append(fileset)
    excluding = {
        tier: repository_relative_globs(filesets)
        for tier, filesets in per_tier.items()
        if any(fileset.startswith("!") for fileset in filesets)
    }
    assert excluding, "no tier excludes anything from its key, so this journey proves nothing"

    tracked = selector.tracked()
    for (project, target), globs in sorted(excluding.items()):
        tier = f"{project}:{target}"
        included = next((path for path in tracked if covers(globs, path)), None)
        assert included, f"{tier} is keyed on nothing this tree contains: {globs}"

        assert selector.ran_the_command(tier)
        with selector.planted(included):
            assert selector.ran_the_command(tier), (
                f"{tier} replayed for a change to {included}, which its key covers, so "
                "nothing below would tell an honoured exclusion from an unread tree"
            )

        for negated in sorted(glob for glob in globs if glob.startswith("!")):
            excluded = next((path for path in tracked if matches(negated[1:], path)), None)
            assert excluded, (
                f"{tier} excludes {negated} from its key, and this tree holds no path "
                "that fileset names, so the exclusion cannot be measured — and buys "
                "nothing until it can"
            )
            with selector.planted(excluded):
                assert not selector.ran_the_command(tier), (
                    f"{tier} re-ran for a change to {excluded}, which its key excludes "
                    f"through {negated}; an exclusion that does not buy the replay buys "
                    "nothing at all"
                )


def test_a_diff_of_the_workspace_configuration_selects_every_project(
    selector: Selector,
) -> None:
    """`nx.json` is the one path the selector treats as reaching everything.

    It is where the filesets themselves are declared, so a change to it can move what
    every other path selects — and Nx answers a change to it with the whole workspace
    rather than with the projects that happen to name it. Measured because the
    narrowing would be unsound without it: the recipe hands Nx a base and no exception
    list, so a tier whose key moved has to be re-run by the selection itself.
    """
    selector.append("nx.json", "")

    assert selector.selected() == selector.projects(), (
        "a diff of nx.json must leave the deterministic tier nothing to skip: it is "
        "where every other tier's key is declared"
    )
