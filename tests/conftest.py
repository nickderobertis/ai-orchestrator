"""Shared test fixtures.

What this suite proves is this repository's own configuration layer: the `just`
recipes, the harness wrappers, the llmlint tier, the Nx cache keys, and the
provisioning that installs the published CLIs everything else here delegates to.
The engines themselves are proven in their own repositories.

The environment fixtures below all exist for one reason: every worker verifies
itself by running this suite from *inside* a dispatch, so the suite inherits that
dispatch's environment, and a test's environment is the test's to state.
"""

from __future__ import annotations

import builtins
import importlib.metadata
import io
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

import plan_fixture_root
import pytest
from nx_inputs import (
    ASK_SEAM_ROOT,
    ASK_SEAM_WORKSPACE,
    CODE_WORKSPACE,
    DAG_UI_ROOT,
    DAG_UI_WORKSPACE,
    PLAN_TOOLING_ROOT,
    PLAN_TOOLING_WORKSPACE,
    RECIPE_WORKSPACE,
    covers,
    named_input_globs,
    repository_relative,
)
from registered_checkouts import listed_checkout_paths
from waits import install_default_bounds

from orchestrator.root import REPO_ROOT

# Every blocking call this suite makes gets a finite bound here, at import, and the one
# it gets says what was awaited and where that got to when it expires. `tests/e2e/waits.py`
# holds the policy and the reasoning for the ceiling; this is only where it is switched on.
#
# At import rather than in an autouse fixture, because a fixture of any scope is set up
# after the wider-scoped ones that already spend real launches — a module-scoped fixture
# would run unbounded and a session-scoped one would too, which is most of what there is
# to bound here. A call that states its own `timeout` is untouched, so nothing that
# already chose a bound, or that catches `TimeoutExpired` on purpose, changes behaviour.
install_default_bounds()

WORKSPACE_INSTALL = REPO_ROOT / "scripts" / "workspace-install.sh"
#: The directories under this checkout that git ignores and Nx therefore never hashes.
#: A read of one is toolchain state rather than the tree under judgement, so **no** input
#: declaration could cover it and holding a key to one is a demand nothing can satisfy.
#: `tests/test_nx_cache_scope.py` states the same rule from the other side, in what it
#: will accept as covered at all. Measured rather than anticipated: a session-scoped
#: fixture asking `importlib.metadata` for an installed distribution's version opens that
#: distribution's `METADATA` under `.venv`, and every journey of one project failed for
#: reading a path no key names and none could.
UNHASHED_DIRECTORIES = frozenset({".venv", "node_modules", ".git", ".nx"})
#: The marker that moves a test from its project's narrow key to that project's
#: whole-workspace one. Its one source is `pyproject.toml`'s marker registration, and
#: the `test` / `test-docs` targets of both `orchestrator` and `plan-tooling` select on
#: it. It routes between the targets of one project and never between projects: which
#: project owns a test is decided by where the test lives.
READS_DOCS_MARKER = "reads_docs"
DOCUMENTATION_DIRECTORY = "docs"
#: The marker that moves a test into the narrow recipe-scoped key.
READS_RECIPES_MARKER = "reads_recipes"


#: Every directory a project of its own owns, and the key that project's test target is
#: memoized on. Tests there are routed by *path* rather than by marker — the project
#: boundary is the tier — so the guards below ask where a test lives rather than what it
#: declares. `docs_tier` says whether that project has a second, whole-workspace target
#: for `reads_docs` to route a test into: `plan-tooling` does, for the journeys that copy
#: this checkout, and `ask-seam` does not, so a prose read there is a read outside its
#: only key rather than a routing instruction.
class OwnedProject(NamedTuple):
    """One directory-owned test project, in what the read guards need of it."""

    key: str
    docs_tier: bool


OWNED_PROJECTS = {
    PLAN_TOOLING_ROOT: OwnedProject(key=PLAN_TOOLING_WORKSPACE, docs_tier=True),
    ASK_SEAM_ROOT: OwnedProject(key=ASK_SEAM_WORKSPACE, docs_tier=False),
    DAG_UI_ROOT: OwnedProject(key=DAG_UI_WORKSPACE, docs_tier=False),
}
#: The marker that moves a test out of every memoized tier and into the uncached one.
#: Its subject is another repository — its checkout, or the merge path it publishes
#: through — which lives outside this workspace and so outside every `nx.json` key.
READS_CHECKOUTS_MARKER = "reads_checkouts"

#: Every spelling of the gate-comparison identity `scripts/comparison-base.sh` and
#: `.githooks/pre-push` read. Both are live: `onevcs` exports the `ONEVCS_*` pair on
#: every lifecycle path and the `ORCHESTRATOR_*` pair is the operator's override, so
#: dropping one prefix alone leaves the suite inheriting the other. Restated here
#: rather than imported — the exporting side is a published CLI — and reconciled
#: against those readers by `tests/test_dispatch_environment_contract.py`.
COMPARISON_ENV_PREFIXES = ("ORCHESTRATOR_COMPARISON_", "ONEVCS_COMPARISON_")
#: The dispatch ownership stamp `scripts/oneharness-agent.sh` branches on: with one
#: exported it streams into that directory, without one it takes its `--events`
#: branch. `tests/e2e/test_quota_fallthrough_e2e.py` drives the second branch.
AGENT_STATUS_DIR_ENV = "ORCHESTRATOR_AGENT_STATUS_DIR"
#: Every variable a per-side harness or model choice reaches a child through, as
#: `scripts/oneharness-agent.sh` resolves them. The wrapper turns its own per-side
#: variables into oneharness's process-wide ones, so dropping only the first pair
#: would leave the resolved value the suite actually inherited in place.
DISPATCH_SELECTION_ENV = (
    "ORCHESTRATOR_WORKER_HARNESSES",
    "ORCHESTRATOR_JUDGE_HARNESSES",
    "ORCHESTRATOR_WORKER_MODEL",
    "ORCHESTRATOR_JUDGE_MODEL",
    "ONEHARNESS_HARNESSES",
    "ONEHARNESS_MODEL",
)


@pytest.fixture(scope="session", autouse=True)
def _shared_plan_fixture_root() -> None:
    """Hold this process's reader lock on the shared local-md fixture root.

    Nx runs this repository's test tiers as concurrent processes over one configured
    `test-fixtures` root, so a record removed by one of them is a record another is
    walking. Every process takes the shared lock before it runs anything, which is
    what makes a sweep possible at all — see `tests/plan_fixture_root.py` for the
    refusal a removal mid-walk produces and the gate it has already failed.
    """
    plan_fixture_root.sweep_dead_owners()
    plan_fixture_root.hold_shared()


@pytest.fixture(scope="session")
def workspace_install() -> None:
    """Provision `node_modules` once per session, as `scripts/nx.sh` would.

    A journey that copies this checkout and runs Nx in the copy needs the install to
    have happened here first; paying for it once per session is what keeps that from
    being a per-journey cost.
    """
    result = subprocess.run([str(WORKSPACE_INSTALL)], text=True, capture_output=True)
    if result.returncode != 0:
        pytest.fail(f"workspace-install failed: {result.stderr or result.stdout}")


@pytest.fixture(autouse=True)
def _isolate_gate_comparison_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the enclosing dispatch's comparison base out of the suite's own pushes.

    The publication path exports one comparison identity to every process judging a
    change, so this suite run inside a dispatch inherits `ONEVCS_COMPARISON_BASE` from
    the branch it is proving. Git hands a `pre-push` hook the whole environment, so a
    test push that deliberately carries no publication base would silently arrive
    carrying the outer branch's.
    """
    for key in tuple(os.environ):
        if key.startswith(COMPARISON_ENV_PREFIXES):
            monkeypatch.delenv(key)


@pytest.fixture(autouse=True)
def _isolate_dispatch_attribution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the enclosing dispatch's ownership stamp out of the suite's own processes."""
    monkeypatch.delenv(AGENT_STATUS_DIR_ENV, raising=False)


@pytest.fixture(autouse=True)
def _isolate_dispatch_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep an enclosing dispatch's per-side choice — by any of its names — out of this suite.

    A dispatch launched against a chosen harness or model exports that choice to
    everything it runs, including the worker's own gate, which is this suite. The
    journeys that assert what a side selects would then be reading the outer run's
    choice instead of their own.
    """
    for key in DISPATCH_SELECTION_ENV:
        monkeypatch.delenv(key, raising=False)


def git(*args: str, cwd: str | Path | None = None) -> str:
    """Run a real git command in a test, failing loudly; return stdout."""
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr or proc.stdout}")
    return proc.stdout


@pytest.fixture(autouse=True)
def _no_nx_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a test's own Nx invocations from leaving a background daemon behind.

    Nx's daemon deliberately outlives the command that starts it, so a test shelling
    out to a `just` recipe that reaches Nx leaves one running per session. It buys a
    test nothing — the computation cache is on disk either way — and the developer
    loop that does want one runs outside this process.
    """
    monkeypatch.setenv("NX_DAEMON", "false")


def _outside_the_code_key(file: object, globs: list[str]) -> str | None:
    """Return the repository-relative path ``file`` names when `codeWorkspace` omits it.

    Only this checkout's own content counts. A throwaway copy of the tree — which is
    what every workspace journey reads — lives outside `REPO_ROOT` and is not the
    tree any cache key here describes.
    """
    if not isinstance(file, str | os.PathLike):
        return None
    try:
        named = os.fspath(file)
    except TypeError:
        return None
    if isinstance(named, bytes):
        named = named.decode("utf-8", "replace")
    # Every open in the suite passes through here, so decide on a substring before
    # paying for a syscall. The code key drops exactly one thing: this repository's
    # prose.
    if not (named.endswith(".md") or DOCUMENTATION_DIRECTORY in named):
        return None
    relative = repository_relative(named)
    if relative is None or covers(globs, relative):
        return None
    return relative


def _outside_a_key(file: object, globs: list[str]) -> str | None:
    """The repository path ``file`` names when no glob in ``globs`` covers it.

    `None` for anything a cache key has no business describing: a value that is not a
    path at all, one outside this checkout, and one under a directory Nx never hashes.
    """
    if not isinstance(file, str | os.PathLike):
        return None
    try:
        named = os.fspath(file)
    except TypeError:
        return None
    relative = repository_relative(named)
    if relative is None or relative.split("/", 1)[0] in UNHASHED_DIRECTORIES:
        return None
    return None if covers(globs, relative) else relative


@pytest.fixture(autouse=True)
def _code_key_reads_are_declared(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hold a test to the cache key its tier is memoized on.

    `orchestrator:test` is keyed on less than the workspace so that editing prose
    stops charging for a suite that would return the same verdict. That key is only
    sound while the tests it covers genuinely ignore what it drops, and "genuinely"
    cannot be a reviewer's recollection: a test that quietly starts asserting on
    `AGENTS.md` would replay a green verdict for a tree whose tests would have failed.

    So the declaration is enforced where it is made. An undeclared test that opens
    what the key drops fails here and is told to join the whole-workspace tier
    instead, which is the direction this decision has to fail in. A read from inside
    a child process is out of reach — but a journey that hands a real tool the whole
    tree copies it first, and copying is itself a read.

    `reads_checkouts` satisfies this too, and for the reason the rule is about rather
    than by exception: that marker puts a test in the *uncached* tier, so there is no
    memoized verdict for a dropped input to make stale. Demanding `reads_docs` beside
    it would put the same test back into a whole-workspace tier that memoizes a
    verdict depending on another repository's checkout — which is the false green the
    checkout guard below exists to prevent. A test reconciling this repository's prose
    against an engine's own source needs exactly one of these markers, and it is that
    one.
    """
    if request.node.get_closest_marker(READS_DOCS_MARKER) is not None:
        return
    if request.node.get_closest_marker(READS_CHECKOUTS_MARKER) is not None:
        return
    # A test of a directory-owned project is keyed on that project's own input rather
    # than on this one; the guard below this one is what holds it to that key.
    if _owned_project(request) is not None:
        return
    # Resolved before the wrapper is installed: reading the declaration through the
    # guard that consults it is a loop waiting for its first prose-shaped path.
    globs = named_input_globs(CODE_WORKSPACE)
    opener = builtins.open

    # `Any` throughout because this stands in for `open` itself: its signature is a
    # stack of overloads whose return type is chosen by the `mode` and `buffering`
    # arguments, and every caller in the suite must keep the type it already had.
    # Restating those overloads here would narrow real call sites to satisfy a
    # wrapper that only inspects the first argument and forwards the rest untouched.
    def guarded(file: Any, *args: Any, **kwargs: Any) -> Any:
        uncovered = _outside_the_code_key(file, globs)
        if uncovered is not None:
            raise AssertionError(
                f"{request.node.name} reads {uncovered}, which the code-only test key "
                f"does not cover; mark it @pytest.mark.{READS_DOCS_MARKER} so it runs "
                "in the whole-workspace tier"
            )
        return opener(file, *args, **kwargs)

    # `pathlib` reaches the same function through the `io` module rather than
    # `builtins`, so a guard on one alone would miss every `Path.read_text`.
    monkeypatch.setattr(builtins, "open", guarded)
    monkeypatch.setattr(io, "open", guarded)


def _owned_project(request: pytest.FixtureRequest) -> OwnedProject | None:
    """The directory-owned project this test belongs to, or `None` for a marker tier."""
    module = repository_relative(request.node.path)
    if module is None:
        return None
    for directory, owned in OWNED_PROJECTS.items():
        if module.startswith(f"{directory}/"):
            return owned
    return None


@pytest.fixture(autouse=True)
def _owned_project_reads_are_declared(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hold a directory-owned project to the key its verdict is memoized on.

    Those projects exist because their journeys cost what a host tool costs — the
    installed engine, the `just` recipes, a real launch, a real `oneharness run` — and
    are answered by a much narrower set of files than the workspace: this repository's
    configuration, personas, scripts and modules, and not its prose. A narrower claim
    needs the same enforcement the recipe tier gets, for the same reason: a read outside
    the key is a file that can change that project's answer without changing its hash.

    The routing is by directory rather than by marker, which is the whole point of the
    project — so a file added there is held to its key by being there, with nothing to
    declare and nothing that can be forgotten. The one exception is a journey that
    builds a **copy** of this checkout: copying is itself a read of everything git
    tracks, so those carry `reads_docs` and are collected by their project's *own*
    whole-workspace target instead, where that is exactly what their verdict depends
    on. The marker moves such a journey between one project's two keys rather than out
    of the project, so its cost stays charged to the code `nx affected` selects it for —
    which is why it is honoured only where that second target exists. Where it does not,
    a prose read is a read outside the project's only key and fails here.
    """
    owned = _owned_project(request)
    if owned is None:
        return
    if owned.docs_tier and request.node.get_closest_marker(READS_DOCS_MARKER):
        return
    globs = named_input_globs(owned.key)
    opener = builtins.open

    # `Any` for the same reason the two guards around it use it: this stands in for
    # `open` itself, whose return type is chosen by arguments this forwards untouched.
    def guarded(file: Any, *args: Any, **kwargs: Any) -> Any:
        uncovered = _outside_a_key(file, globs)
        if uncovered is not None:
            raise AssertionError(
                f"{request.node.name} reads {uncovered}, which the {owned.key} key does "
                f"not cover; add the path to that key in nx.json, or this project "
                f"replays a verdict recorded before the file it depends on last moved"
            )
        return opener(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded)
    monkeypatch.setattr(io, "open", guarded)


@pytest.fixture(autouse=True)
def _recipe_reads_are_declared(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hold the recipe tier to the narrow key its verdict is memoized on.

    `orchestrator:test-recipes` exists because the costliest journeys in this suite
    drive `just` recipes and shell scripts and read nothing else of this repository —
    so a commit that touches neither may replay their verdict instead of paying for
    them again. That is a much narrower claim than the code-only key makes, and a
    narrower claim needs stricter enforcement, not looser: any read outside
    `recipeWorkspace` is a file that can change this tier's answer without changing
    its hash.

    So the same enforcement `reads_docs` gets, against the same declaration Nx
    hashes rather than a restatement of it — a marked test that opens anything else
    in this checkout fails here, naming the path and the tier it belongs in. The
    test module itself is checked too, because a marked test in an unkeyed file
    would replay a verdict recorded before the test was written.
    """
    if request.node.get_closest_marker(READS_RECIPES_MARKER) is None:
        return
    globs = named_input_globs(RECIPE_WORKSPACE)
    module = repository_relative(request.node.path)
    assert module is not None and covers(globs, module), (
        f"{request.node.name} is declared @pytest.mark.{READS_RECIPES_MARKER} from {module}, "
        f"which nx.json's {RECIPE_WORKSPACE} does not cover; add the module to that key "
        "or the tier replays a verdict recorded before this test existed"
    )
    opener = builtins.open

    # `Any` for the same reason the documentation guard uses it: this stands in for
    # `open` itself, whose return type is chosen by arguments this wrapper forwards
    # untouched.
    def guarded(file: Any, *args: Any, **kwargs: Any) -> Any:
        uncovered = _outside_a_key(file, globs)
        if uncovered is not None:
            raise AssertionError(
                f"{request.node.name} reads {uncovered}, which the recipe test key "
                f"does not cover; drop @pytest.mark.{READS_RECIPES_MARKER} so it runs "
                "in a tier keyed on that path"
            )
        return opener(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded)
    monkeypatch.setattr(io, "open", guarded)


def _registered_checkout_roots() -> tuple[str, ...]:
    """Every directory the tracked list says another repository is checked out in.

    Resolved from the same list the guards that reconcile against those checkouts
    read, so the tier boundary and the tests it routes cannot disagree about which
    directories are outside this workspace. This checkout is excluded: the list names
    `ai-orchestrator` too, and reading *this* tree is what the keys already describe.
    """
    return tuple(
        sorted(f"{path}{os.sep}" for path in listed_checkout_paths() if str(path) != str(REPO_ROOT))
    )


#: Resolved once: the guard consults it on every open in the suite.
CHECKOUT_ROOTS = _registered_checkout_roots()


@pytest.fixture(autouse=True)
def _checkout_reads_are_declared(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hold a test that reads another repository's checkout to the uncached tier.

    Every memoized tier here is keyed on this workspace, and a registered checkout is
    not in it — no `nx.json` glob could name one. So a test that reads one and is
    memoized anyway records a verdict about a repository that goes on changing
    afterwards, and replays it as though it still held. That is the same false green
    the two guards above prevent, from the one direction they cannot see: the path is
    outside the repository entirely, so `repository_relative` returns nothing for it.

    The uncached tier is where such a test belongs, and the marker is what puts it
    there — so an undeclared read fails here, naming the checkout and the marker.
    """
    if request.node.get_closest_marker(READS_CHECKOUTS_MARKER) is not None:
        return
    opener = builtins.open

    # `Any` for the reason the guards above use it: this stands in for `open`, whose
    # return type is chosen by arguments it forwards untouched.
    def guarded(file: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(file, str | os.PathLike):
            named = os.fspath(file)
            if isinstance(named, bytes):
                named = named.decode("utf-8", "replace")
            if named.startswith(CHECKOUT_ROOTS):
                raise AssertionError(
                    f"{request.node.name} reads {named}, a registered checkout of another "
                    f"repository; no cache key covers it, so mark the test "
                    f"@pytest.mark.{READS_CHECKOUTS_MARKER} to run it in the uncached tier"
                )
        return opener(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded)
    monkeypatch.setattr(io, "open", guarded)


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give git a committer identity via env so test commits never depend on ~/.gitconfig."""
    for key, val in {
        "GIT_AUTHOR_NAME": "ai-orchestrator-test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "ai-orchestrator-test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }.items():
        monkeypatch.setenv(key, val)


@pytest.fixture
def bare_origin(tmp_path: Path) -> Callable[..., Path]:
    """Return a factory that seeds a bare git 'origin' (a real remote, no network).

    The bare repo has one commit on ``main`` plus any extra ``files`` (relpath →
    content). A bare remote accepts pushes to any branch.
    """
    counter = {"n": 0}

    def _make(files: dict[str, str] | None = None, *, branch: str = "main") -> Path:
        counter["n"] += 1
        seed = tmp_path / f"seed-{counter['n']}"
        git("init", "-b", branch, str(seed))
        (seed / "README.md").write_text("seed\n", encoding="utf-8")
        for rel, content in (files or {}).items():
            p = seed / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        git("add", "-A", cwd=seed)
        git("commit", "-m", "init", cwd=seed)
        bare = tmp_path / f"origin-{counter['n']}.git"
        git("clone", "--bare", str(seed), str(bare))
        return bare

    return _make


@pytest.fixture(scope="session")
def adopted_onejudge_version() -> str:
    """Read and validate the repository's exact onejudge version declaration."""
    adopted = (REPO_ROOT / "config" / "onejudge.version").read_text(encoding="utf-8").strip()
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", adopted) is None:
        pytest.fail(f"config/onejudge.version must contain one semantic version, got {adopted!r}")
    return adopted


@pytest.fixture(scope="session")
def adopted_oneharness_version() -> str:
    """Read and validate the repository's exact oneharness version declaration."""
    adopted = (REPO_ROOT / "config" / "oneharness.version").read_text(encoding="utf-8").strip()
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", adopted) is None:
        pytest.fail(f"config/oneharness.version must contain one semantic version, got {adopted!r}")
    return adopted


@pytest.fixture(scope="session")
def oneharness_bin(adopted_oneharness_version: str) -> str:
    """Resolve the worktree-local oneharness and require the exact adopted release."""
    found = REPO_ROOT / ".venv" / "bin" / "oneharness"
    if not found.is_file():
        pytest.fail(f"worktree-local oneharness missing at {found} — run 'just bootstrap'")
    try:
        distribution_version = importlib.metadata.version("oneharness-cli")
    except importlib.metadata.PackageNotFoundError:
        pytest.fail("oneharness-cli is not installed in the project environment")
    if distribution_version != adopted_oneharness_version:
        pytest.fail(
            "wrong worktree-local oneharness-cli distribution: "
            f"expected {adopted_oneharness_version!r}, got {distribution_version!r}"
        )
    version = subprocess.run([found, "--version"], text=True, capture_output=True, check=False)
    actual_version = version.stdout.strip().removeprefix("oneharness ")
    if version.returncode != 0 or actual_version != adopted_oneharness_version:
        actual = version.stdout.strip() or version.stderr.strip() or "<no version output>"
        pytest.fail(
            f"wrong oneharness on PATH: expected {adopted_oneharness_version!r}, "
            f"got {actual!r} from {found} — run 'just bootstrap'"
        )
    return str(found)


@pytest.fixture(scope="session")
def oneagentgraph_bin() -> str:
    """Resolve the worktree-local oneagentgraph and require the exact adopted release.

    That the pin, `pyproject.toml`, the lockfile, and this binary all agree is
    `tests/test_published_tools.py`'s subject; this only refuses to hand a journey the
    wrong binary, the same way `oneharness_bin` does.
    """
    adopted = (REPO_ROOT / "config" / "oneagentgraph.version").read_text(encoding="utf-8").strip()
    found = REPO_ROOT / ".venv" / "bin" / "oneagentgraph"
    if not found.is_file():
        pytest.fail(f"worktree-local oneagentgraph missing at {found} — run 'just bootstrap'")
    version = subprocess.run([found, "--version"], text=True, capture_output=True, check=False)
    reported = version.stdout.strip().removeprefix("oneagentgraph ")
    if version.returncode != 0 or reported != adopted:
        actual = version.stdout.strip() or version.stderr.strip() or "<no version output>"
        pytest.fail(
            f"wrong oneagentgraph on PATH: expected {adopted!r}, got {actual!r} from "
            f"{found} — run 'just bootstrap'"
        )
    return str(found)
