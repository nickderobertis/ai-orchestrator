"""Real command-surface journeys for comparison-base selection."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from conftest import git

ROOT = Path(__file__).parents[2]


def _resolve(
    repo: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    # Both spellings are cleared by default: the journeys below that care about one
    # of them set it, and the rest are about discovery with neither named.
    return subprocess.run(
        [str(ROOT / "scripts/comparison-base.sh"), *args],
        cwd=repo,
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "ORCHESTRATOR_COMPARISON_BASE": "",
            "ONEVCS_COMPARISON_BASE": "",
            "ONEVCS_COMPARISON_REMOTE": "",
            **(env or {}),
        },
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


def test_comparison_base_takes_the_base_the_lifecycle_names(tmp_path, bare_origin) -> None:
    """One judged diff, one verdict — and `onevcs` is what names the diff now.

    The lifecycle exports the comparison ref into the gate it runs and the push it
    makes so the worker's verdict and the publishing push judge the same base. That
    export moved to `onevcs`'s own spelling when the recipes became a thin shell over
    it, and reading only `ORCHESTRATOR_COMPARISON_*` left the base unset on every
    lifecycle path: each side then resolved its own base, which is two diffs and two
    independent rolls of a non-deterministic judge.
    """
    origin = bare_origin(branch="main")
    clone = tmp_path / "clone"
    git("clone", str(origin), str(clone))
    git("branch", "release", "origin/main", cwd=clone)
    git("push", "origin", "release", cwd=clone)
    git("fetch", "--prune", "origin", cwd=clone)

    resolved = _resolve(clone, env={"ONEVCS_COMPARISON_BASE": "release"})

    assert resolved.returncode == 0, resolved.stderr
    assert resolved.stdout.strip() == "origin/release"


def test_an_explicit_operator_base_still_beats_the_lifecycles(tmp_path, bare_origin) -> None:
    """The `ORCHESTRATOR_*` spelling is also the documented operator override."""
    origin = bare_origin(branch="main")
    clone = tmp_path / "clone"
    git("clone", str(origin), str(clone))
    git("branch", "release", "origin/main", cwd=clone)
    git("push", "origin", "release", cwd=clone)
    git("fetch", "--prune", "origin", cwd=clone)

    resolved = _resolve(
        clone,
        env={"ORCHESTRATOR_COMPARISON_BASE": "main", "ONEVCS_COMPARISON_BASE": "release"},
    )

    assert resolved.returncode == 0, resolved.stderr
    assert resolved.stdout.strip() == "origin/main"


def test_the_pre_push_hook_reads_the_base_the_lifecycle_exported(tmp_path) -> None:
    """The hook is where the worker's verdict is replayed rather than re-rolled."""
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
            "ORCHESTRATOR_COMPARISON_BASE": "",
            "ONEVCS_COMPARISON_BASE": "invalid..base",
        },
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 2
    # It reached the resolver as the base, rather than being discarded for discovery.
    assert "comparison remote=upstream base=invalid..base" in proc.stderr
