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
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from nx_inputs import (
    CHECKOUT_SCOPED,
    CODE_SCOPED,
    COVERAGE_SCOPED,
)
from nx_workspace import WORKSPACE_INSTALL_MARKS, copy_checkout

from orchestrator.root import REPO_ROOT

# Copying the whole tree is this journey's premise, and the tree includes its
# prose: these belong to the whole-workspace tier by construction.
pytestmark = [*WORKSPACE_INSTALL_MARKS, pytest.mark.reads_docs]

CACHE_HIT = "read the output from the cache"
# The suite reads this file directly — tests/test_smoke_selector.py holds its
# documented launch-path list against scripts/pre-push-smoke-needed.sh — and it
# lives outside every project root.
PROSE_WITNESS = "AGENTS.md"
PROSE_TEXT = "`oneharness.check-in.toml`; ordinary pushes"
PROSE_EDIT = "`oneharness.check-in.toml`, `docs/probe.md`; ordinary pushes"
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

    def resolved_target(self, target: str) -> dict:
        """Ask Nx itself how one target resolves, defaults and all.

        `project.json` states part of a target and `nx.json`'s `targetDefaults`
        states the rest, and only Nx merges them. A `targetDefaults` key that no
        longer matches a target name is silently inert — the target simply falls
        back to Nx's own defaults, which for `cache` means *not caching*, and for a
        tier meant to be cached that is a quiet loss rather than an error.
        """
        result = subprocess.run(
            ["./scripts/nx.sh", "show", "project", PROJECT, "--json"],
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
        return json.loads(rendered)["targets"][target]

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
