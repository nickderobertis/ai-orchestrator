"""Unit tests for gitops — driven against a real (local, bare) git repo."""

from __future__ import annotations

import pytest

from orchestrator import gitops


def test_full_git_cycle(tmp_path, bare_origin) -> None:
    origin = bare_origin({"file.txt": "hi\n"})
    assert gitops.is_bare(origin)

    clone = gitops.clone(str(origin), tmp_path / "clone")
    assert (clone / ".git").exists()
    assert not gitops.is_bare(clone)
    assert gitops.default_branch(clone) == "main"
    assert not gitops.has_commits_ahead(clone, "origin/main")  # fresh clone is even

    gitops.fetch(clone, prune=False)
    wt = gitops.worktree_add(clone, tmp_path / "wt", "feat", base="origin/main")
    assert gitops.current_branch(wt) == "feat"
    assert not gitops.is_dirty(wt)

    (wt / "new.txt").write_text("x\n", encoding="utf-8")
    assert gitops.is_dirty(wt)
    gitops.add_all(wt)
    sha = gitops.commit(wt, "add new")
    assert sha == gitops.head_sha(wt)
    assert gitops.has_commits_ahead(wt, "origin/main")

    gitops.push(wt, "feat")
    gitops.checkout(clone, "main")
    gitops.reset_hard(clone, "origin/main")
    merged = gitops.merge(clone, "feat", message="merge feat")
    assert merged == gitops.head_sha(clone)
    gitops.push(clone, "main", set_upstream=False)

    gitops.worktree_remove(clone, wt)
    assert not wt.exists()


def test_clone_with_depth(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    clone = gitops.clone(str(origin), tmp_path / "shallow", depth=1)
    assert (clone / ".git").exists()


def test_default_branch_falls_back_to_main(tmp_path) -> None:
    # A repo with no remote has no origin/HEAD; default_branch falls back to main.
    repo = tmp_path / "solo"
    gitops._git(["init", "-b", "main", str(repo)])
    assert gitops.default_branch(repo) == "main"


def test_force_push(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    clone = gitops.clone(str(origin), tmp_path / "clone")
    (clone / "a.txt").write_text("1\n", encoding="utf-8")
    gitops.add_all(clone)
    gitops.commit(clone, "c1")
    gitops.push(clone, "main", set_upstream=False)
    (clone / "a.txt").write_text("2\n", encoding="utf-8")
    gitops.add_all(clone)
    gitops.commit(clone, "c2 amend-ish")
    gitops.push(clone, "main", set_upstream=False, force=True)


def test_merge_fast_forward_without_no_ff(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    clone = gitops.clone(str(origin), tmp_path / "clone")
    wt = gitops.worktree_add(clone, tmp_path / "wt", "feat", base="origin/main")
    (wt / "x.txt").write_text("1\n", encoding="utf-8")
    gitops.add_all(wt)
    gitops.commit(wt, "c")
    gitops.checkout(clone, "main")
    gitops.merge(clone, "feat", message="ff merge", no_ff=False)
    assert (clone / "x.txt").exists()
    gitops.worktree_remove(clone, wt, force=False)


def test_git_error_on_non_repo(tmp_path) -> None:
    with pytest.raises(gitops.GitError, match="git rev-parse"):
        gitops.head_sha(tmp_path)
