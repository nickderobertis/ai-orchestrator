"""E2E proof that a cached test verdict is keyed on the tree it judged.

The llmlint tier is not the only one whose verdict is memoized: every cached Nx
target replays a recorded answer, and `orchestrator:test` — the tier the coverage
and correctness invariants rest on — is one of them. A memo is only sound when its
key covers everything the check reads, and this suite reads far beyond
`orchestrator/` and `tests/`: it asserts on `AGENTS.md`, `docs/`, the `justfile`,
`llmlint.yml`, `scripts/`, `.githooks/pre-push`, and `apps/dag-ui/vite.config.ts`.
Keyed on a hand-listed subset, a change to any of those replayed a green verdict
for a tree whose tests would have failed had they run.

The suite answers at three scopes, so it is keyed at three. Only a handful of tests
assert on this repository's prose, and charging every documentation edit eight
minutes for the rest bought nothing: `orchestrator:test-docs` runs those and keeps
the whole-workspace key. Narrower again, the two costliest journeys in the suite
build real worktrees and run real package installs to drive `just` recipes and
shell scripts, and read no prose and no `orchestrator/` at all:
`orchestrator:test-recipes` runs those under a key of exactly what they drive.
`orchestrator:test` and `orchestrator:test-serial` run the remainder, keyed on the
workspace minus its documentation and minus the front-end projects no Python test
opens — one scope split into two tasks so the serial invocation the
`single_threaded` tests need blocks nothing. Every half of
that claim is load bearing — each key must still invalidate on what its tier reads,
and must still replay on what it does not — so each is proved here, along with the
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

The browser tier is keyed the same way and for the same reason. `dag-ui:test` is
vitest plus two Playwright configs, and it named all of `orchestrator/**/*` while
running one door into it — the fixture server Playwright starts, which imports the
read API — so every commit to a command-side module the served process never loads
charged a real browser. `dagUiServerSurface` states what that door reaches, and
both halves are proved below.

llmlint: ignore-file[e2e_not_mocked] Nothing is faked here. The only substitutions
shorten what a real target *runs* without touching the declared inputs these
journeys are about: `PYTEST_ADDOPTS` for the Python tiers, and `Checkout.shorten`
for the browser tier, which has no such lever and would otherwise spend half an
hour of vitest and Playwright proving something about hashes computed before its
command starts.
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
    BROWSER_PROJECT,
    BROWSER_SCOPED,
    CODE_SCOPED,
    COVERAGE_SCOPED,
    SERIAL_SCOPED,
)
from nx_workspace import copy_checkout, requires_workspace_install

from orchestrator import REPO_ROOT

# Copying the whole tree is this journey's premise, and the tree includes its
# prose: these belong to the whole-workspace tier by construction.
pytestmark = [requires_workspace_install, pytest.mark.reads_docs]

CACHE_HIT = "read the output from the cache"
# The suite reads this file directly — tests/test_smoke_selector.py holds its
# documented launch-path list against scripts/pre-push-smoke-needed.sh — and it
# lives outside every project root.
PROSE_WITNESS = "AGENTS.md"
PROSE_TEXT = "`oneharness.orchestrator.toml`; ordinary pushes"
PROSE_EDIT = "`oneharness.orchestrator.toml`, `docs/probe.md`; ordinary pushes"
# Also read directly, also outside every project root, and deliberately not prose:
# this is what proves the narrowed key was narrowed by documentation alone. It is
# also the recipe tier's own witness, since driving it is what that tier is for.
CODE_WITNESS = "justfile"
CODE_TEXT = "# List available recipes."
CODE_EDIT = "# List the available recipes."
#: Python the code tier reads and the recipe tier does not: editing it must re-run
#: one and replay the other, which is the whole reason the recipe key is narrow.
PYTHON_WITNESS = "orchestrator/lifecycle.py"
#: A front-end project the Python suite never opens. Its contract is checked by the
#: whole-workspace tier, so editing it must re-run that tier and replay the code one.
FRONT_END_WITNESS = "apps/dag-ui/vite.config.ts"
#: The fixture `scripts/check-nx-cache.sh` builds its two linked worktrees from.
FIXTURE_WITNESS = "tests/fixtures/nx-cache/src/index.ts"
#: Python the browser tier's fixture server imports, so editing it must re-run that
#: tier — and Python nothing the served process loads imports, so editing that one
#: must replay it. `orchestrator.status` is a command-side verb: `just status`
#: renders with it and no HTTP route reaches it.
SERVED_WITNESS = "orchestrator/timeline.py"
UNSERVED_WITNESS = "orchestrator/status.py"
#: The two files outside `orchestrator/` the browser tier runs: the fixture server
#: Playwright starts, and the harness history store that server shells out to.
FIXTURE_SERVER_WITNESS = "apps/dag-ui/e2e/fixtures/serve_fixture.py"
FAKE_HARNESS_WITNESS = "tests/e2e/fake_oneharness.py"
#: The browser tier itself: vitest plus two Playwright configs.
BROWSER_TIER = f"{BROWSER_PROJECT}:{BROWSER_SCOPED}"
#: The project every Python tier belongs to, and the two tiers the code suite runs
#: in. They share `codeWorkspace` and are separate Nx tasks, so neither waits for
#: the other and each has to notice everything the suite reads on its own.
PROJECT = "orchestrator"
CODE_TIER = f"{PROJECT}:{CODE_SCOPED}"
SERIAL_TIER = f"{PROJECT}:{SERIAL_SCOPED}"
#: The uncached tier that combines what those two measured and judges the floor.
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

    def shorten(self, project_root: str, target: str) -> None:
        """Replace one target's command, leaving the inputs that key it untouched.

        The same substitution `PYTEST_ADDOPTS` makes for the Python tiers, reached
        the only way a target with no such lever offers: the browser tier is vitest
        and two Playwright configs — two and a half minutes, five servers, a real
        browser — and the four runs the journeys below need would prove the same
        thing about the same hashes half an hour more slowly. Nx computes a task's
        hash from its declared inputs, which is what these journeys are about, and
        this leaves every one of them exactly as the repository declares it.
        """
        path = self.root / project_root / "project.json"
        project = json.loads(path.read_text(encoding="utf-8"))
        assert target in project["targets"], f"{project_root} declares no {target} target"
        project["targets"][target]["command"] = "true"
        path.write_text(f"{json.dumps(project, indent=2)}\n", encoding="utf-8")

    def append(self, relative: str, line: str) -> None:
        """Change a file's content without changing what it means.

        Some witnesses are read by the very checks these journeys run — the Nx
        cache fixture is typechecked, `vite.config.ts` is held against a contract —
        so the edit has to move the hash and nothing else.
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


def test_the_serial_tier_is_keyed_on_the_same_tree_as_the_bulk_it_left(
    checkout: Checkout,
) -> None:
    """Splitting the code suite in two split its cache key claim in two with it.

    `single_threaded` tests read the same tree as the bulk they were split out of —
    the split is about the process they need, not about what they open — so this
    tier gets the same `codeWorkspace` key and has to behave the same way at both
    edges: prose it cannot read replays, Python it can read misses.
    """
    assert checkout.ran_the_command(SERIAL_TIER)
    assert not checkout.ran_the_command(SERIAL_TIER), (
        "an unchanged tree must replay its recorded verdict rather than re-run"
    )

    checkout.edit(PROSE_WITNESS, PROSE_TEXT, PROSE_EDIT)

    assert not checkout.ran_the_command(SERIAL_TIER), (
        f"changing {PROSE_WITNESS} must not re-run a tier whose tests cannot read it"
    )

    checkout.append(PYTHON_WITNESS, "# nx cache scope journey")

    assert checkout.ran_the_command(SERIAL_TIER), (
        f"changing {PYTHON_WITNESS} must re-run the tier keyed on the code"
    )


def test_neither_half_of_the_code_suite_waits_for_the_other(checkout: Checkout) -> None:
    """The point of the split: running one tier must not consume the other.

    The two invocations used to be chained by `&&` inside one target, so a serial
    failure meant the parallel half never reported at all and re-running the
    one-second serial test dragged three minutes of parallel suite with it. As
    separate tasks neither is the other's dependency, which is exactly what running
    one and finding the other still cold demonstrates.
    """
    assert checkout.ran_the_command(SERIAL_TIER)

    assert checkout.ran_the_command(CODE_TIER), (
        f"{CODE_TIER} replayed a verdict it never recorded, so {SERIAL_TIER} ran it"
    )


def test_the_coverage_tier_resolves_as_an_unmemoized_step_after_both(
    checkout: Checkout,
) -> None:
    """The floor is enforced by a task Nx will never replay, once both tiers exist.

    Its inputs are two files on disk rather than the tree, so a cached verdict here
    would be a claim about coverage data Nx does not hash. It is seconds of work;
    it re-runs. And it must wait on every tier that measures, or the combined total
    it judges is a total the whole suite never produced.
    """
    resolved = checkout.resolved_target(COVERAGE_SCOPED)

    assert resolved.get("cache") is False, resolved
    assert sorted(resolved["dependsOn"]) == sorted([CODE_SCOPED, SERIAL_SCOPED]), resolved
    for tier in (CODE_SCOPED, SERIAL_SCOPED):
        measuring = checkout.resolved_target(tier)
        assert measuring.get("cache") is True, (
            f"{tier} measures into a file the coverage tier needs restored on a cache "
            f"hit, so it has to be cached with that file as its output: {measuring}"
        )
        data_file = measuring["outputs"][0].removeprefix("{workspaceRoot}/")
        assert data_file in resolved["options"]["command"], (
            f"{tier} writes {data_file} and the coverage tier never reads it: {resolved}"
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


def test_editing_a_front_end_project_replays_the_python_code_tier(checkout: Checkout) -> None:
    """No Python test opens `apps/` or `packages/`, so no Python tier is keyed on them.

    The DAG state contract does read them, and it runs in the whole-workspace tier,
    which is what keeps this narrowing from dropping the check on the floor.
    """
    assert checkout.ran_the_command("orchestrator:test")
    assert checkout.ran_the_command("orchestrator:test-docs")

    checkout.append(FRONT_END_WITNESS, "// nx cache scope journey")

    assert checkout.ran_the_command("orchestrator:test-docs"), (
        f"changing {FRONT_END_WITNESS} must re-run the tier that checks its contract"
    )
    assert not checkout.ran_the_command("orchestrator:test"), (
        f"changing {FRONT_END_WITNESS} must not re-run a tier whose tests never open it"
    )


def test_editing_python_the_browser_tier_never_loads_replays_it(checkout: Checkout) -> None:
    """The case this narrowing exists for: backend churn stops paying for a browser.

    The tier reaches this repository's Python through one door — the fixture server
    Playwright starts, which imports the read API — and most commits here touch
    modules that door never opens. Keyed on all of `orchestrator/**/*`, every one of
    them charged vitest and two Playwright configs for a verdict that could not have
    differed.
    """
    checkout.shorten(f"apps/{BROWSER_PROJECT}", BROWSER_SCOPED)

    assert checkout.ran_the_command(BROWSER_TIER)
    assert not checkout.ran_the_command(BROWSER_TIER), (
        "an unchanged tree must replay its recorded verdict rather than re-run"
    )

    checkout.append(UNSERVED_WITNESS, "# nx cache scope journey")

    assert not checkout.ran_the_command(BROWSER_TIER), (
        f"changing {UNSERVED_WITNESS} must not re-run a tier that never loads it"
    )


def test_the_browser_tier_still_re_runs_on_everything_it_does_load(checkout: Checkout) -> None:
    """The other half of the claim, and the half that keeps the narrowing sound.

    A key that covers less than its check reads fails *open*: the tier would report
    a pass for a served contract it never exercised. So each of the three things it
    genuinely runs — a module the read API imports, the fixture that builds and
    serves the run directory, and the fake harness that fixture shells out to — has
    to still miss.
    """
    checkout.shorten(f"apps/{BROWSER_PROJECT}", BROWSER_SCOPED)
    assert checkout.ran_the_command(BROWSER_TIER)

    for witness in (SERVED_WITNESS, FIXTURE_SERVER_WITNESS, FAKE_HARNESS_WITNESS):
        checkout.append(witness, "# nx cache scope journey")
        assert checkout.ran_the_command(BROWSER_TIER), (
            f"changing {witness} must re-run the browser tier that runs it"
        )


def test_a_replayed_test_verdict_restores_the_coverage_data_the_floor_needs(
    checkout: Checkout,
) -> None:
    """A cache hit has to hand the floor the measurement it stood in for.

    The two measuring tiers are cached and the tier that judges the floor is not, so
    on every replayed commit the combine runs against data no run in this checkout
    produced. That only works because each measuring tier declares its data file as
    an Nx output and Nx restores it — an `outputs` declaration that fell off, or a
    tier that stopped writing the file it names, would leave the combine with
    nothing and turn a saving into a failed gate.

    `PYTEST_ADDOPTS` shortens the selection but, unlike every journey above, leaves
    measurement on: a restored data file is the whole subject. The floor itself is
    lowered in this copy for the same reason — a collect-only selection covers a
    fraction of the package, and `tests/test_coverage_gate.py` is where the declared
    floor's own value is proven at the real boundary.
    """
    checkout.edit("pyproject.toml", "fail_under = 95", "fail_under = 0")
    measured = {CODE_TIER: ".coverage.parallel", SERIAL_TIER: ".coverage.serial"}
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
