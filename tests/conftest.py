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
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from leak_guard import ResourceLeakGuard

from orchestrator import BASE_CONFIG, PERSONA_DIR, REPO_ROOT, gitops
from orchestrator.config import load_yaml
from orchestrator.environment import CHANNEL_ENV_PREFIX

FAKE_BACKEND = REPO_ROOT / "tests" / "e2e" / "fake_backend.py"


@pytest.fixture(autouse=True)
def _cover_real_git_clone_merge_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make general lifecycle fixtures satisfy the production dispatch guard."""
    clone = gitops.clone

    def covered_clone(*args: object, **kwargs: object) -> Path:
        checkout = clone(*args, **kwargs)
        hook = checkout / ".git" / "hooks" / "pre-push"
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        hook.chmod(0o755)
        return checkout

    monkeypatch.setattr(gitops, "clone", covered_clone)


@pytest.fixture(autouse=True)
def _isolate_orchestrator_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep nested real CLI tests off the parent orchestrator's live channel."""
    for key in tuple(os.environ):
        if key.startswith(CHANNEL_ENV_PREFIX):
            monkeypatch.delenv(key)


@pytest.fixture(autouse=True)
def resource_leak_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request: pytest.FixtureRequest
) -> Iterator[ResourceLeakGuard]:
    """Reap complete subprocess trees and report test-owned resource leaks."""
    original_popen = subprocess.Popen
    e2e_test = "e2e" in Path(str(request.node.path)).parts
    guard = ResourceLeakGuard(popen=original_popen)

    def tracked_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
        return guard.spawn(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", tracked_popen)
    yield guard
    if e2e_test:
        for git_file in tmp_path.rglob(".git"):
            if guard.is_linked_worktree(git_file.parent):
                guard.register_worktree(git_file.parent)
    guard.finish()


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
