"""Real command-surface journeys for comparison-base selection."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from orchestrator import gitops

ROOT = Path(__file__).parents[2]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


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


def test_pre_push_hook_forwards_git_remote_and_explicit_base(tmp_path) -> None:
    clone = gitops.clone(str(ROOT), tmp_path / "clone")
    gitops._git(["remote", "rename", "origin", "upstream"], cwd=clone)
    proc = subprocess.run(
        [str(clone / ".githooks/pre-push"), "upstream", str(ROOT)],
        cwd=clone,
        env={
            **os.environ,
            "ORCHESTRATOR_COMPARISON_BASE": "invalid..base",
        },
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 2
    assert "comparison remote=upstream base=invalid..base" in proc.stderr
    assert "comparison-base: 'invalid..base' is not a valid branch name" in proc.stderr


def test_pre_push_hook_clears_repository_local_git_environment(tmp_path: Path) -> None:
    clone = gitops.clone(str(ROOT), tmp_path / "clone")
    tools = tmp_path / "tools"
    tools.mkdir()
    _write_executable(tools / "llmlint", "#!/bin/sh\nexit 0\n")
    _write_executable(
        tools / "just",
        """#!/bin/sh
{
  printf 'args=%s\\n' "$*"
  printf 'git_dir=%s\\n' "${GIT_DIR-unset}"
  printf 'git_work_tree=%s\\n' "${GIT_WORK_TREE-unset}"
  git rev-parse --show-toplevel
} >"$TEST_HOOK_RESULT"
""",
    )
    result = tmp_path / "hook-result"
    proc = subprocess.run(
        [str(ROOT / ".githooks/pre-push"), "origin", str(ROOT)],
        cwd=clone,
        env={
            **os.environ,
            "PATH": f"{tools}:{os.environ['PATH']}",
            "GIT_DIR": str(ROOT / ".git"),
            "GIT_WORK_TREE": str(ROOT),
            "ORCHESTRATOR_COMPARISON_BASE": "",
            "TEST_HOOK_RESULT": str(result),
        },
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert result.read_text(encoding="utf-8").splitlines() == [
        "args=gate origin ",
        "git_dir=unset",
        "git_work_tree=unset",
        str(clone),
    ]
