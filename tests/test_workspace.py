"""Unit tests for repo-spec normalization and the clone/worktree pool."""

from __future__ import annotations

import pytest

from orchestrator import gitops
from orchestrator.workspace import (
    DEFAULT_OWNER,
    RepoRef,
    Workspace,
    WorkspaceError,
    _safe_branch_dir,
    normalize_repo,
)


def test_normalize_bare_name_defaults_owner() -> None:
    ref = normalize_repo("onejudge")
    assert ref.owner == DEFAULT_OWNER and ref.name == "onejudge"
    assert not ref.local
    assert ref.url == f"https://github.com/{DEFAULT_OWNER}/onejudge.git"
    assert ref.slug == f"{DEFAULT_OWNER}/onejudge"


def test_normalize_owner_name() -> None:
    ref = normalize_repo("someone/thing.git")
    assert ref.owner == "someone" and ref.name == "thing" and not ref.local


@pytest.mark.parametrize(
    "spec",
    ["https://github.com/o/n", "https://github.com/o/n.git", "git@github.com:o/n.git"],
)
def test_normalize_github_urls(spec: str) -> None:
    ref = normalize_repo(spec)
    assert ref.owner == "o" and ref.name == "n" and not ref.local


def test_normalize_existing_path_is_local(tmp_path) -> None:
    d = tmp_path / "myrepo"
    d.mkdir()
    ref = normalize_repo(str(d))
    assert ref.local and ref.owner == "local" and ref.name == "myrepo"
    assert ref.url == str(d)


def test_normalize_relative_and_file_url(tmp_path) -> None:
    assert normalize_repo("./nope-dir").local  # ./ prefix is treated as a path
    assert normalize_repo("file:///tmp/x.git").local


def test_normalize_dir_key_is_filesystem_safe() -> None:
    assert normalize_repo("o/n").dir_key == "o__n"
    assert "/" not in normalize_repo("https://github.com/a/b").dir_key


def test_normalize_rejects_empty_and_unparseable() -> None:
    with pytest.raises(ValueError, match="empty"):
        normalize_repo("   ")
    with pytest.raises(ValueError, match="could not parse"):
        normalize_repo("owner/")  # non-path, empty name


def test_safe_branch_dir() -> None:
    assert _safe_branch_dir("ai/feat x") == "ai-feat-x"
    assert _safe_branch_dir("///") == "wt"


def test_workspace_clone_worktree_lifecycle(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    ref = normalize_repo(str(origin))
    canonical = gitops.clone(origin, tmp_path / "canonical")
    ws = Workspace(tmp_path / "worktrees", resolver=lambda _: canonical)

    clone = ws.ensure_clone(ref)
    assert clone == canonical
    assert not str(ws._worktree_root(ref)).startswith(str(canonical))
    # A second resolution reuses and refreshes the canonical checkout.
    assert ws.ensure_clone(ref) == clone
    # The per-repo lock is memoized.
    assert ws._repo_lock(ref) is ws._repo_lock(ref)

    wt = ws.worktree(ref, "feat", base="origin/main")
    assert gitops.current_branch(wt) == "feat"
    # An active branch belongs to its run and is never reset out from under it.
    with pytest.raises(RuntimeError, match="branch 'feat' is active.*resume"):
        ws.worktree(ref, "feat", base="origin/main")

    ws.remove_worktree(ref, wt)
    assert not wt.exists()


def test_workspace_fast_forwards_canonical_before_cutting_worktree(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical")
    writer = gitops.clone(origin, tmp_path / "writer")
    (writer / "new.txt").write_text("current\n", encoding="utf-8")
    gitops.add_all(writer)
    gitops.commit(writer, "feat: advance main")
    gitops.push(writer, "main", set_upstream=False)

    ref = normalize_repo(str(origin))
    ws = Workspace(tmp_path / "worktrees", resolver=lambda _: canonical)
    ws.ensure_clone(ref)
    worktree = ws.worktree(ref, "feat/current", base="origin/main")

    assert (canonical / "new.txt").read_text(encoding="utf-8") == "current\n"
    assert (worktree / "new.txt").read_text(encoding="utf-8") == "current\n"


def test_workspace_refuses_dirty_execution_checkout(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "dirty-canonical")
    (canonical / "operator.txt").write_text("uncommitted\n", encoding="utf-8")
    ws = Workspace(tmp_path / "worktrees", resolver=lambda _: canonical)

    with pytest.raises(WorkspaceError, match="execution checkout.*dirty"):
        ws.ensure_clone(normalize_repo(str(origin)))


def test_repo_ref_defaults() -> None:
    ref = RepoRef(owner="o", name="n", url="u")
    assert ref.local is False
