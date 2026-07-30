"""Unit tests for repo-spec normalization and the clone/worktree pool."""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from orchestrator import gitops
from orchestrator.workspace import (
    DEFAULT_OWNER,
    OWNER_RECORD_NAME,
    RepoRef,
    RunOwner,
    Workspace,
    WorkspaceError,
    _abandoned_run_is_reclaimable,
    _remove_directory_tree,
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
    # The run works in its own clone of the canonical checkout, never in it.
    assert clone != canonical
    assert ws.execution_checkout(ref) == canonical
    assert gitops.remote_url(clone) == gitops.remote_url(canonical)
    assert not str(ws.run_root(ref)).startswith(str(canonical))
    # A second resolution reuses this run's clone and refreshes the canonical one.
    assert ws.ensure_clone(ref) == clone
    # The per-repo lock is memoized.
    assert ws._repo_lock(ref) is ws._repo_lock(ref)

    wt = ws.worktree(ref, "feat", base="origin/main")
    assert gitops.current_branch(wt) == "feat"
    # An active branch belongs to its run and is never reset out from under it.
    with pytest.raises(RuntimeError, match="branch 'feat' is active.*resume"):
        ws.worktree(ref, "feat", base="origin/main")

    # A crashed teardown can leave Git's registration after its directory is gone.
    shutil.rmtree(wt)
    replacement = ws.worktree(ref, "feat", base="origin/main")
    assert replacement == wt
    assert gitops.current_branch(replacement) == "feat"

    ws.remove_worktree(ref, replacement)
    assert not replacement.exists()


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


def test_workspace_reclaims_an_unregistered_path_collision(tmp_path, bare_origin) -> None:
    """A directory a killed worker left at this run's worktree path is cleared.

    Git will not help here and says so: a directory it has no registration for is
    refused outright, which is asserted first so this cannot quietly become a test
    of a path git was willing to remove all along.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical")
    ref = normalize_repo(str(origin))
    ws = Workspace(tmp_path / "worktrees", resolver=lambda _: canonical)
    clone = ws.ensure_clone(ref)
    collision = ws.run_root(ref) / _safe_branch_dir("feat")
    (collision / "node_modules" / ".cache").mkdir(parents=True)
    (collision / "node_modules" / ".cache" / "blob").write_text("stale\n", encoding="utf-8")
    (collision / "node_modules").chmod(0o500)

    with pytest.raises(gitops.GitError, match="is not a working tree"):
        gitops.worktree_remove(clone, collision, check=True)

    worktree = ws.worktree(ref, "feat", base="origin/main")

    assert worktree == collision
    assert (worktree / "README.md").is_file()
    assert not (worktree / "node_modules").exists()


def test_teardown_finishes_a_removal_git_refuses_instead_of_deferring_it(
    tmp_path, bare_origin
) -> None:
    """The refusal that kept deferring cleanup across branches now clears the path.

    A worktree whose registration is gone but whose directory is not — what a killed
    worker leaves behind — makes ``git worktree remove --force`` fail with ``is not a
    working tree``. Teardown used to surface that as deferred cleanup and leave the
    directory for an operator; it now finishes the removal itself.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-refused")
    ref = normalize_repo(str(origin))
    ws = Workspace(tmp_path / "worktrees", resolver=lambda _: canonical)
    clone = ws.ensure_clone(ref)
    worktree = ws.worktree(ref, "feat", base="origin/main")
    (worktree / "node_modules").mkdir()
    (worktree / "node_modules" / "blob").write_text("stale\n", encoding="utf-8")
    # Disown the tree exactly as a pruned registration does, leaving the directory.
    shutil.rmtree(gitops.common_dir(clone) / "worktrees")
    with pytest.raises(gitops.GitError, match="is not a working tree"):
        gitops.worktree_remove(clone, worktree, check=True)

    ws.remove_worktree(ref, worktree)

    assert not worktree.exists()


def test_workspace_refuses_to_reclaim_a_path_outside_its_run_root(tmp_path, bare_origin) -> None:
    """Ownership is the precondition: another run's tree is never a candidate."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical")
    ref = normalize_repo(str(origin))
    ws = Workspace(tmp_path / "worktrees", resolver=lambda _: canonical)
    clone = ws.ensure_clone(ref)
    stranger = tmp_path / "another-run" / "feat"
    (stranger / "work").mkdir(parents=True)

    with pytest.raises(
        WorkspaceError, match="does not have the shape of a worktree this run lays out"
    ):
        ws._reclaim_worktree_path(ref, clone, stranger)

    assert (stranger / "work").is_dir()


def test_workspace_refuses_to_reclaim_a_directory_inside_one_of_its_worktrees(
    tmp_path, bare_origin
) -> None:
    """Sitting under the run root is not enough: content inside a tree is not a tree.

    Everything a worker creates lives below this run's root, so containment alone
    would make a worker's own source directory a candidate for recursive deletion.
    Only the slots this layout lays out — direct children of the run root — are.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-nested")
    ref = normalize_repo(str(origin))
    ws = Workspace(tmp_path / "worktrees", resolver=lambda _: canonical)
    clone = ws.ensure_clone(ref)
    worktree = ws.worktree(ref, "feat", base="origin/main")
    nested = worktree / "src"
    nested.mkdir()
    (nested / "work.py").write_text("real work\n", encoding="utf-8")

    with pytest.raises(
        WorkspaceError, match="does not have the shape of a worktree this run lays out"
    ):
        ws._reclaim_worktree_path(ref, clone, nested)

    assert (nested / "work.py").is_file()


def test_a_locked_worktree_survives_the_reclaim_and_keeps_gits_refusal(
    tmp_path, bare_origin
) -> None:
    """A lock is an instruction to leave a tree alone, and it outranks reclaiming.

    Real `git worktree lock` on a real worktree: the reclaim must not delete it, and
    the caller must get the refusal Git gives rather than a silently cleared path.
    """
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-locked")
    ref = normalize_repo(str(origin))
    ws = Workspace(tmp_path / "worktrees", resolver=lambda _: canonical)
    clone = ws.ensure_clone(ref)
    worktree = ws.worktree(ref, "feat", base="origin/main")
    (worktree / "work.txt").write_text("in progress\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(clone), "worktree", "lock", str(worktree)],
        check=True,
        capture_output=True,
    )

    with pytest.raises(gitops.GitError, match="locked working tree"):
        ws._reclaim_worktree_path(ref, clone, worktree)

    assert (worktree / "work.txt").is_file()
    assert worktree.resolve() in gitops.locked_worktrees(clone)


def test_a_worktree_path_that_cannot_be_reclaimed_says_so(tmp_path) -> None:
    """The one leftover this cannot clear is named, not silently worked around.

    A tree whose *parent* denies this user is the shape that made a re-dispatch
    unrecoverable without ``sudo`` — root-owned container files — and no amount of
    restoring the owner bits below it helps. It has to reach an operator as itself.
    """
    parent = tmp_path / "sealed"
    target = parent / "worktree"
    target.mkdir(parents=True)
    (target / "leftover").write_text("stale\n", encoding="utf-8")
    parent.chmod(0o500)
    try:
        with pytest.raises(WorkspaceError, match="could not reclaim worktree path"):
            _remove_directory_tree(target)
    finally:
        parent.chmod(0o700)

    # The path is still occupied, which is exactly why the caller must hear about it
    # rather than go on and fail later on `git worktree add`.
    assert target.is_dir()


def test_workspace_refuses_dirty_execution_checkout(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "dirty-canonical")
    (canonical / "operator.txt").write_text("uncommitted\n", encoding="utf-8")
    ws = Workspace(tmp_path / "worktrees", resolver=lambda _: canonical)

    with pytest.raises(WorkspaceError, match="execution checkout.*dirty"):
        ws.ensure_clone(normalize_repo(str(origin)))


def test_failed_worktree_add_releases_lease_for_retry(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-add-failure")
    ref = normalize_repo(str(origin))
    ws = Workspace(tmp_path / "worktrees", resolver=lambda _: canonical)
    ws.ensure_clone(ref)

    with pytest.raises(gitops.GitError):
        ws.worktree(ref, "feat", base="origin/missing")

    worktree = ws.worktree(ref, "feat", base="origin/main")
    assert worktree.exists()
    ws.remove_worktree(ref, worktree)


def test_workspace_rejects_invalid_execution_checkout(tmp_path) -> None:
    invalid = tmp_path / "not-a-repo"
    invalid.mkdir()
    workspace = Workspace(tmp_path / "worktrees", resolver=lambda _: invalid)

    with pytest.raises(RuntimeError, match="not a git checkout"):
        workspace.ensure_clone(normalize_repo("o/r"))


def test_publication_readiness_rejects_dirty_and_wrong_branch(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    checkout = gitops.clone(origin, tmp_path / "publication-readiness")
    (checkout / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(WorkspaceError, match="publication checkout.*dirty"):
        Workspace._assert_publication_ready(checkout, "main")

    (checkout / "dirty.txt").unlink()
    with pytest.raises(WorkspaceError, match="branch 'main'.*root branch 'other'"):
        Workspace._assert_publication_ready(checkout, "other")


def test_cache_rejects_empty_state_home(tmp_path, bare_origin, monkeypatch) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-cache")
    ref = normalize_repo(str(origin))
    workspace = Workspace(tmp_path / "worktrees", resolver=lambda _: canonical)
    workspace.ensure_clone(ref)
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", "")

    with pytest.raises(WorkspaceError, match="must not be empty"):
        workspace.ensure_cache_dir(ref)


def test_run_clone_borrows_the_shared_object_store_instead_of_copying_it(
    tmp_path, bare_origin
) -> None:
    """Per-run clones are only affordable because they cost refs, not objects."""
    origin = bare_origin({f"payload-{index}.txt": "x" * 4096 for index in range(64)})
    canonical = gitops.clone(origin, tmp_path / "canonical-shared")
    ref = normalize_repo(str(origin))
    ws = Workspace(tmp_path / "worktrees", resolver=lambda _: canonical)

    clone = ws.ensure_clone(ref)

    alternates = clone / ".git" / "objects" / "info" / "alternates"
    assert alternates.read_text(encoding="utf-8").strip() == str(
        (canonical / ".git" / "objects").resolve()
    )
    # The lender must never expire an object a live borrower still reads through it.
    assert gitops.config_value(canonical, "gc.auto") == "0"
    assert gitops.config_value(canonical, "gc.pruneExpire") == "never"
    # The pointer is the whole object store: a second concurrent run duplicates no
    # history at all, which is what makes one clone per run affordable.
    objects = clone / ".git" / "objects"
    assert [
        str(entry.relative_to(objects)) for entry in sorted(objects.rglob("*")) if entry.is_file()
    ] == ["info/alternates"]


def test_reclaim_predicate_keeps_every_run_root_it_cannot_clear(tmp_path, bare_origin) -> None:
    """The one predicate that authorizes deleting a run's tree, exercised directly."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-reclaim")
    ref = normalize_repo(str(origin))
    ws = Workspace(tmp_path / "worktrees", resolver=lambda _: canonical)
    ws.ensure_clone(ref)
    run_root = ws.run_root(ref)
    owner = run_root / OWNER_RECORD_NAME

    # This process wrote the record and is still alive.
    assert not _abandoned_run_is_reclaimable(run_root)

    owner.write_text(json.dumps({"pid": os.getpid(), "process_start": "not-a-token"}), "utf-8")
    assert _abandoned_run_is_reclaimable(run_root)

    worktree = ws.worktree(ref, "feat/unpublished", base="origin/main")
    (worktree / "only-here.txt").write_text("never pushed\n", encoding="utf-8")
    gitops.add_all(worktree)
    gitops.commit(worktree, "feat: work that exists nowhere else")
    assert not _abandoned_run_is_reclaimable(run_root)

    ws.remove_worktree(ref, worktree)
    empty = tmp_path / "empty-run"
    empty.mkdir()
    assert _abandoned_run_is_reclaimable(empty)
    (empty / "leftover").mkdir()
    assert not _abandoned_run_is_reclaimable(empty)


@pytest.mark.parametrize("token", ["", "..", ".", "a/b", "../escape", "with space", "sub\\dir"])
def test_run_token_that_could_name_another_directory_is_refused(tmp_path, token: str) -> None:
    """The token becomes a path component, and the reaper's rmtree follows it."""
    with pytest.raises(WorkspaceError, match="run token"):
        Workspace(tmp_path / "worktrees", resolver=lambda _: tmp_path, run_token=token)


@pytest.mark.parametrize(
    "record",
    ["[1, 2]", '"text"', "null", '{"pid": true, "process_start": 1}', '{"pid": 1}', "{ not json"],
)
def test_unreadable_owner_record_is_not_mistaken_for_a_live_owner(tmp_path, record: str) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    (run_root / OWNER_RECORD_NAME).write_text(record, encoding="utf-8")

    assert RunOwner.read(run_root) is None
    assert _abandoned_run_is_reclaimable(run_root)


def test_unresolved_repository_names_itself_in_every_lookup(tmp_path) -> None:
    ws = Workspace(tmp_path / "worktrees", resolver=lambda _: tmp_path)
    ref = normalize_repo("o/r")

    for lookup in (ws.clone_dir, ws.execution_checkout, ws.selection):
        with pytest.raises(RuntimeError, match="o/r has not been resolved"):
            lookup(ref)


def test_legacy_flat_worktree_directory_does_not_block_a_run(tmp_path, bare_origin) -> None:
    """An earlier layout's leftovers sit beside the run tree, not in its way."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-legacy")
    ref = normalize_repo(str(origin))
    legacy = tmp_path / "worktrees" / ref.dir_key / _safe_branch_dir("feat")
    gitops.worktree_add(canonical, legacy, "feat", base="origin/main")
    ws = Workspace(tmp_path / "worktrees", resolver=lambda _: canonical)

    ws.ensure_clone(ref)
    worktree = ws.worktree(ref, "feat", base="origin/main")

    assert worktree != legacy
    assert legacy.exists() and gitops.worktrees(canonical)["feat"] == legacy
    ws.remove_worktree(ref, worktree)
    gitops.worktree_remove(canonical, legacy)


def test_repo_ref_defaults() -> None:
    ref = RepoRef(owner="o", name="n", url="u")
    assert ref.local is False
