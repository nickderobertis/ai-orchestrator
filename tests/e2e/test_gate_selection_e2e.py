"""Real command-surface journeys for comparison-base selection."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from orchestrator import gitops

ROOT = Path(__file__).parents[2]


def _resolve(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ROOT / "scripts/comparison-base.sh"), *args],
        cwd=repo,
        text=True,
        capture_output=True,
        env={**os.environ, "ORCHESTRATOR_COMPARISON_BASE": ""},
    )


def test_comparison_base_discovers_non_main_and_accepts_explicit_base(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin(branch="master")
    clone = gitops.clone(str(origin), tmp_path / "clone")
    assert _resolve(clone).stdout.strip() == "origin/master"

    gitops._git(["branch", "release", "origin/master"], cwd=clone)
    gitops.push(clone, "release", set_upstream=False)
    gitops.fetch(clone)
    gitops._git(["symbolic-ref", "--delete", "refs/remotes/origin/HEAD"], cwd=clone)
    ambiguous = _resolve(clone)
    assert ambiguous.returncode == 2
    assert "just gate origin <branch>" in ambiguous.stderr
    assert _resolve(clone, "origin", "master").stdout.strip() == "origin/master"


def test_pre_push_hook_forwards_git_remote_and_explicit_base(tmp_path, bare_origin) -> None:
    clone = gitops.clone(str(bare_origin(branch="master")), tmp_path / "clone")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "just.args"
    fake = bindir / "just"
    fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{log}"\n', encoding="utf-8")
    fake.chmod(0o755)
    proc = subprocess.run(
        [str(ROOT / ".githooks/pre-push"), "upstream", str(bare_origin())],
        cwd=clone,
        env={
            **os.environ,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "ORCHESTRATOR_COMPARISON_BASE": "master",
        },
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0
    assert log.read_text(encoding="utf-8").splitlines() == ["gate", "upstream", "master"]
