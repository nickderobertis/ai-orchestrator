"""Real command-surface journeys for comparison-base selection."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from conftest import git

ROOT = Path(__file__).parents[2]


def _resolve(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ROOT / "scripts/comparison-base.sh"), *args],
        cwd=repo,
        text=True,
        capture_output=True,
        env={**os.environ, "ORCHESTRATOR_COMPARISON_BASE": ""},
    )


def test_comparison_base_uses_upstream_without_remote_head(tmp_path, bare_origin) -> None:
    origin = bare_origin(branch="main")
    clone = tmp_path / "clone"
    git("clone", str(origin), str(clone))
    assert _resolve(clone).stdout.strip() == "origin/main"

    for branch in ("ai-orchestrator/first", "ai-orchestrator/second"):
        git("branch", branch, "origin/main", cwd=clone)
        git("push", "origin", branch, cwd=clone)
    git("fetch", "--prune", "origin", cwd=clone)
    git("symbolic-ref", "--delete", "refs/remotes/origin/HEAD", cwd=clone)

    resolved = _resolve(clone)
    assert resolved.returncode == 0
    assert resolved.stdout.strip() == "origin/main"


def test_comparison_base_requires_explicit_base_when_ambiguous(tmp_path, bare_origin) -> None:
    origin = bare_origin(branch="main")
    clone = tmp_path / "clone"
    git("clone", str(origin), str(clone))
    git("branch", "release", "origin/main", cwd=clone)
    git("push", "origin", "release", cwd=clone)
    git("fetch", "--prune", "origin", cwd=clone)
    git("symbolic-ref", "--delete", "refs/remotes/origin/HEAD", cwd=clone)
    git("checkout", "--detach", "origin/main", cwd=clone)

    ambiguous = _resolve(clone)
    assert ambiguous.returncode == 2
    assert "just gate origin <branch>" in ambiguous.stderr
    assert _resolve(clone, "origin", "main").stdout.strip() == "origin/main"


def test_pre_push_hook_clears_git_environment_and_forwards_comparison(tmp_path) -> None:
    clone = tmp_path / "clone"
    git("clone", str(ROOT), str(clone))
    shutil.copy2(ROOT / ".githooks/pre-push", clone / ".githooks/pre-push")
    git("remote", "rename", "origin", "upstream", cwd=clone)
    proc = subprocess.run(
        [str(clone / ".githooks/pre-push"), "upstream", str(ROOT)],
        cwd=clone,
        env={
            **os.environ,
            "GIT_DIR": str(ROOT / ".git"),
            "GIT_WORK_TREE": str(ROOT),
            "ORCHESTRATOR_COMPARISON_BASE": "invalid..base",
        },
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 2
    assert "comparison remote=upstream base=invalid..base" in proc.stderr
    assert "comparison-base: 'invalid..base' is not a valid branch name" in proc.stderr
