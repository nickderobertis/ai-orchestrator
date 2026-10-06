"""Deterministic selection and documentation drift contracts for the paid smoke."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from conftest import git

ROOT = Path(__file__).parents[1]
SELECTOR = ROOT / "scripts/pre-push-smoke-needed.sh"


def run_selector_cases(source: Path, clone: Path) -> str:
    """Clone ``source``, make a comparison commit in the clone, and assert every case.

    The comparison base is a commit the clone makes itself rather than a remote-tracking
    ref or the history it inherited, so no case depends on which branches ``source``
    carries: a publication worktree whose only branch is the publishing one clones with
    no ``origin/main`` at all. Returns the comparison commit's id.
    """
    git("clone", "-q", str(source), str(clone))

    def commit(message: str) -> str:
        git("add", "-A", cwd=clone)
        git("commit", "-m", message, cwd=clone)
        return git("rev-parse", "HEAD", cwd=clone).strip()

    (clone / "smoke-selector-comparison.txt").write_text("comparison\n", encoding="utf-8")
    comparison = commit("test: smoke selector comparison base")

    def selected(before: str, after: str) -> bool:
        proc = subprocess.run(
            [str(SELECTOR), comparison],
            cwd=clone,
            input=f"refs/heads/main {after} refs/heads/main {before}\n",
            text=True,
            capture_output=True,
        )
        assert proc.returncode in {0, 1}, proc.stderr
        return proc.returncode == 0

    base = comparison
    (clone / "README.md").write_text("ordinary\n", encoding="utf-8")
    ordinary = commit("docs: ordinary")
    assert not selected(base, ordinary)

    previous = ordinary
    declared = subprocess.run(
        [str(SELECTOR), "--print-paths"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    assert {"oneharness.plan-review.toml", "oneharness.plan-review-whole.toml"} <= set(declared), (
        "Both review roles must select the paid launch-path smoke"
    )
    for relative in (
        f"{path}launch-smoke-probe.sh" if path.endswith("/") else path for path in declared
    ):
        target = clone / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            target.read_text(encoding="utf-8") + "\n" if target.exists() else "# probe\n"
        )
        changed = commit(f"test: touch {relative}")
        assert selected(previous, changed), relative
        previous = changed
    assert selected("0" * 40, previous)
    assert not selected(previous, "0" * 40)

    documented_lists = {
        "docs/onejudge-integration.md": re.search(
            r"when the pushed endpoint diff touches (?P<paths>.+?);\s+every other",
            (ROOT / "docs/onejudge-integration.md").read_text(encoding="utf-8"),
            re.DOTALL,
        ),
    }
    for document, match in documented_lists.items():
        assert match is not None, f"{document} must retain its documented launch-path list"
        documented = re.findall(r"`([^`]+)`", match.group("paths"))
        assert documented == declared, (
            f"{document} launch paths must exactly match pre-push-smoke-needed.sh"
        )

    invalid = subprocess.run(
        [str(SELECTOR), "not-a-revision"],
        cwd=clone,
        text=True,
        capture_output=True,
    )
    assert invalid.returncode == 2 and "not a commit" in invalid.stderr
    for update in (
        "refs/heads/main nope refs/heads/main nope\n",
        f"refs/heads/main {base}\n",
        f"refs/heads/main {base}  {'0' * 40}\n",
    ):
        malformed = subprocess.run(
            [str(SELECTOR), comparison],
            cwd=clone,
            input=update,
            text=True,
            capture_output=True,
        )
        assert malformed.returncode == 2 and "invalid ref update" in malformed.stderr
    return comparison


@pytest.mark.reads_docs
def test_smoke_selector_covers_exact_documented_launch_paths(tmp_path: Path) -> None:
    run_selector_cases(ROOT, tmp_path / "clone")


# llmlint: ignore-block[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] This
# regression case shares the helper and the reads_docs tier of the selector test above,
# which it guards; splitting it off would split one contract across two projects.
@pytest.mark.reads_docs
def test_smoke_selector_cases_run_from_a_branch_only_checkout(tmp_path: Path) -> None:
    source = tmp_path / "publication"
    git("init", "-q", "--initial-branch", "publishing", str(source))
    git("fetch", "-q", "--update-head-ok", str(ROOT), "HEAD:refs/heads/publishing", cwd=source)
    git("reset", "-q", "--hard", "publishing", cwd=source)
    assert git("for-each-ref", "--format=%(refname)", cwd=source).split() == [
        "refs/heads/publishing"
    ]

    inherited = git("rev-parse", "HEAD", cwd=source).strip()
    clone = tmp_path / "clone"
    comparison = run_selector_cases(source, clone)

    assert comparison != inherited
    assert git("rev-parse", f"{comparison}^", cwd=clone).strip() == inherited
    assert git("log", "-1", "--format=%s", comparison, cwd=clone).strip() == (
        "test: smoke selector comparison base"
    )

    assert "origin/main" not in git("branch", "-a", cwd=clone)
    refused = subprocess.run(
        [str(SELECTOR), "origin/main"],
        cwd=clone,
        input="",
        text=True,
        capture_output=True,
    )
    assert refused.returncode == 2 and "not a commit" in refused.stderr


# llmlint: ignore-end[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker]
