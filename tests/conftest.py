"""Shared test fixtures.

The e2e fixtures build a onejudge base whose provider is `command`, pointed at
`tests/e2e/fake_backend.py`. That swaps only the paid model/harness for a
deterministic double; everything else (the merge, the effective config, the real
`onejudge` CLI and its loop) runs for real.
"""

# llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] The adopted version files
# remain authoritative targets. These e2e fixtures deliberately permit only the immediately
# preceding onejudge release during the one-step 0.3.3->0.3.4 bootstrap:
# installing the target binaries inside their own running lifecycle would replace and crash
# the shared supervisor. session-setup restores exact-match enforcement after publication.

from __future__ import annotations

import importlib.metadata
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

# Re-exported rather than defined here: `leak_guard` is a self-contained pytest
# plugin, so a session that loads it with `-p leak_guard` — which is how the guard's
# own e2e drives a real session — gets exactly the fixtures this suite runs under.
from leak_guard import resource_leak_guard, session_leak_guard  # noqa: F401

from orchestrator import BASE_CONFIG, PERSONA_DIR, REPO_ROOT, gitops
from orchestrator.config import load_yaml
from orchestrator.environment import CHANNEL_ENV_PREFIX, COMPARISON_ENV_PREFIX

FAKE_BACKEND = REPO_ROOT / "tests" / "e2e" / "fake_backend.py"
WORKSPACE_INSTALL = REPO_ROOT / "scripts" / "workspace-install.sh"


@pytest.fixture(scope="session")
def workspace_install() -> None:
    """Provision the locked workspace install the real-Nx journeys drive.

    Nx runs from `node_modules/.bin`, which a freshly created worktree does not
    have. Skipping used to be the answer, which quietly withdrew the journeys that
    prove Nx's cache accounting from every bare `pytest` run — exactly where a
    worker looks first, and exactly where a green run then meant less than the
    gate's. The step asked for here is the one `scripts/nx.sh` already performs and
    `just bootstrap` forces, so it is free once the workspace is provisioned and
    asks no operator to run Bun by hand.

    Session-scoped because it is one idempotent step for a whole run: the first
    journey to ask for it pays the Bun install, and every later one finds it done.
    """
    result = subprocess.run([WORKSPACE_INSTALL], text=True, capture_output=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        pytest.fail(f"could not provision the workspace Nx install: {detail}")


def install_pre_push_hook(checkout: Path, body: str = "exit 0") -> Path:
    """Install a real `pre-push` hook: the merge-path gate production requires.

    Nothing here is faked — Git runs this hook for real on every push out of the
    checkout and its worktrees, which is exactly the boundary the lifecycle now
    relies on instead of running the gate itself.
    """
    hooks = gitops.hooks_dir(checkout)
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-push"
    hook.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    hook.chmod(0o755)
    return hook


@pytest.fixture(autouse=True)
def _cover_real_git_clone_merge_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every checkout the suite clones the coverage dispatch demands.

    Real checkouts carry hooks their operator installed; test fixtures clone bare
    origins that carry none. Rather than repeat the installation in every fixture,
    hang it off the real `gitops.clone` so the default checkout is a covered one.
    Tests that need the uncovered or failing case override the hook themselves.
    """
    clone = gitops.clone

    def covered_clone(*args: object, **kwargs: object) -> Path:
        checkout = clone(*args, **kwargs)
        install_pre_push_hook(checkout)
        return checkout

    monkeypatch.setattr(gitops, "clone", covered_clone)


@pytest.fixture(autouse=True)
def _isolate_orchestrator_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep nested real CLI tests off the parent orchestrator's live channel."""
    for key in tuple(os.environ):
        if key.startswith(CHANNEL_ENV_PREFIX):
            monkeypatch.delenv(key)


@pytest.fixture(autouse=True)
def _isolate_gate_comparison_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the enclosing dispatch's comparison base out of the suite's own pushes.

    The lifecycle exports one comparison identity to every process judging a change,
    so this suite run inside a dispatch — which is how every worker verifies itself —
    inherits `ORCHESTRATOR_COMPARISON_BASE` from the branch it is proving. Git hands a
    `pre-push` hook the whole environment, so a test push that deliberately carries no
    publication base silently arrived carrying the outer branch's, and the journeys
    asserting which pushes name a base failed on the inherited value rather than on
    anything they did. A test's environment is the test's to state.
    """
    for key in tuple(os.environ):
        if key.startswith(COMPARISON_ENV_PREFIX):
            monkeypatch.delenv(key)


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


@pytest.fixture(autouse=True)
def _isolate_orchestrator_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep the suite off the real ``~/.ai-orchestrator`` state and dev checkouts.

    A default ``Registry``/``Workspace`` reads the real registry file, and
    ``resolve()`` scans ``$HOME`` for a matching checkout — which could find and then
    mutate the real canonical checkout. Point both the state root and the disk-search
    roots at throwaway temp dirs so no test can read or write the developer's state.
    """
    base = tmp_path_factory.mktemp("ao-state")
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(base / ".ai-orchestrator"))
    search = base / "search"
    search.mkdir()
    monkeypatch.setenv("AI_ORCHESTRATOR_SEARCH_ROOTS", str(search))


@pytest.fixture
def bare_origin(tmp_path: Path) -> Callable[..., Path]:
    """Return a factory that seeds a bare git 'origin' (a real remote, no network).

    The bare repo has one commit on ``main`` plus any extra ``files`` (relpath →
    content). A bare remote accepts pushes to any branch, which is what the
    lifecycle's push + merge need.
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
def installed_onejudge_version(adopted_onejudge_version: str) -> str:
    """Validate the CLI version, including the one allowed transition window."""
    found = shutil.which("onejudge")
    if not found:
        pytest.fail("onejudge not on PATH — run 'just bootstrap' (the e2e gate needs it)")
    version = subprocess.run([found, "--version"], text=True, capture_output=True, check=False)
    match = re.fullmatch(r"onejudge ([0-9]+\.[0-9]+\.[0-9]+)", version.stdout.strip())
    installed = match.group(1) if version.returncode == 0 and match is not None else None
    allowed = {adopted_onejudge_version}
    if adopted_onejudge_version == "0.3.4":
        allowed.add("0.3.3")
    if installed not in allowed:
        actual = version.stdout.strip() or version.stderr.strip() or "<no version output>"
        pytest.fail(
            f"wrong onejudge on PATH: expected one of {sorted(allowed)!r}, "
            f"got {actual!r} from {found} — "
            "run 'just bootstrap'"
        )
    assert installed is not None
    return installed


@pytest.fixture(scope="session")
def onejudge_bin(installed_onejudge_version: str) -> str:
    """Resolve the validated real onejudge CLI used by the e2e suite."""
    found = shutil.which("onejudge")
    assert found is not None
    return found


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
    allowed = {adopted_oneharness_version}
    actual_version = version.stdout.strip().removeprefix("oneharness ")
    if version.returncode != 0 or actual_version not in allowed:
        actual = version.stdout.strip() or version.stderr.strip() or "<no version output>"
        pytest.fail(
            f"wrong oneharness on PATH: expected one of {sorted(allowed)!r}, "
            f"got {actual!r} from {found} — "
            "run 'just bootstrap'"
        )
    return str(found)


@pytest.fixture
def command_base(tmp_path: Path) -> Callable[..., Path]:
    """Return a factory that writes a `command`-provider base config.

    Derived from the real base so the shared agent preamble is genuine; only the
    provider is swapped to the fake backend and the turn cap lowered for speed.
    """

    def _make(max_turns: int = 4) -> Path:
        base = load_yaml(BASE_CONFIG)
        base["provider"] = {"kind": "command", "command": [sys.executable, str(FAKE_BACKEND)]}
        base.setdefault("user", {})["max_turns"] = max_turns
        base["session"] = "e2e"
        path = tmp_path / "command.base.yaml"
        path.write_text(yaml.safe_dump(base, sort_keys=False), encoding="utf-8")
        return path

    return _make


@pytest.fixture
def personas_dir() -> Path:
    return PERSONA_DIR
