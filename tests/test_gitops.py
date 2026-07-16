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


def test_default_branch_uses_unborn_head_as_explicit_empty_repo_rule(tmp_path) -> None:
    repo = tmp_path / "solo"
    gitops._git(["init", "-b", "fresh", str(repo)])
    assert gitops.default_branch(repo) == "fresh"


def test_default_branch_uses_sole_remote_ref_when_remote_head_missing(
    tmp_path, bare_origin
) -> None:
    clone = gitops.clone(str(bare_origin(branch="master")), tmp_path / "clone")
    gitops._git(["checkout", "--detach"], cwd=clone)
    gitops._git(["symbolic-ref", "--delete", "refs/remotes/origin/HEAD"], cwd=clone)
    assert gitops.default_branch(clone) == "master"


def test_default_branch_rejects_missing_head_with_ambiguous_refs(tmp_path, bare_origin) -> None:
    clone = gitops.clone(str(bare_origin()), tmp_path / "clone")
    gitops._git(["branch", "other", "origin/main"], cwd=clone)
    gitops.push(clone, "other", set_upstream=False)
    gitops.fetch(clone)
    gitops._git(["symbolic-ref", "--delete", "refs/remotes/origin/HEAD"], cwd=clone)
    gitops._git(["config", "--unset-all", "branch.main.remote"], cwd=clone, check=False)
    gitops._git(["config", "--unset-all", "branch.main.merge"], cwd=clone, check=False)
    with pytest.raises(gitops.GitError, match="pass an explicit base_branch"):
        gitops.default_branch(clone)


def test_default_branch_ignores_stale_remote_head(tmp_path, bare_origin) -> None:
    clone = gitops.clone(str(bare_origin(branch="master")), tmp_path / "clone")
    gitops._git(["symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/gone"], cwd=clone)
    assert gitops.default_branch(clone) == "master"


def test_default_branch_uses_checked_out_branch_upstream_on_named_remote(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin(branch="trunk")
    clone = gitops.clone(str(origin), tmp_path / "clone")
    gitops._git(["remote", "rename", "origin", "upstream"], cwd=clone)
    gitops._git(["symbolic-ref", "--delete", "refs/remotes/upstream/HEAD"], cwd=clone)

    assert gitops.default_branch(clone, remote="upstream") == "trunk"


def test_default_branch_rejects_empty_configured_remote(tmp_path) -> None:
    repo = tmp_path / "repo"
    empty_remote = tmp_path / "empty.git"
    gitops._git(["init", "-b", "trunk", str(repo)])
    gitops._git(["init", "--bare", str(empty_remote)])
    (repo / "README.md").write_text("local only\n", encoding="utf-8")
    gitops.add_all(repo)
    gitops.commit(repo, "seed local branch")
    gitops._git(["remote", "add", "origin", str(empty_remote)], cwd=repo)

    with pytest.raises(gitops.GitError, match="plausible remote branches are none"):
        gitops.default_branch(repo)


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


def test_merge_base_invalid_ref_is_not_reported_as_conflict(tmp_path, bare_origin) -> None:
    clone = gitops.clone(str(bare_origin()), tmp_path / "clone")

    with pytest.raises(gitops.GitError, match="not something we can merge"):
        gitops.merge_base_into_branch(clone, "missing-ref", message="sync missing ref")


def test_is_ancestor_rejects_invalid_ref(tmp_path, bare_origin) -> None:
    clone = gitops.clone(str(bare_origin()), tmp_path / "clone")

    with pytest.raises(gitops.GitError, match="Not a valid object name"):
        gitops.is_ancestor(clone, "missing-ref", "HEAD")


@pytest.mark.parametrize("branch", ["", "-option", "@{-1}", "feature..branch", "feature.lock"])
def test_branch_name_validation_rejects_nonliteral_refs(branch: str) -> None:
    assert not gitops.is_valid_branch_name(branch)


@pytest.mark.parametrize("branch", ["main", "feature/team-workflow", "stack-base/123"])
def test_branch_name_validation_accepts_literal_refs(branch: str) -> None:
    assert gitops.is_valid_branch_name(branch)
