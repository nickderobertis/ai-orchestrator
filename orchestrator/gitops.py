"""Real git operations for the repo lifecycle: clone, worktree, branch, push.

Every function shells out to the real ``git`` binary (no git library) so the
lifecycle is exercised against genuine git — in tests a local *bare* repo stands
in for the remote, so clone/branch/commit/push/merge all run for real without a
network or GitHub. A non-zero git exit raises `GitError` carrying git's stderr.

The lifecycle isolates parallel subtasks with **worktrees**: one clone per repo,
a fresh worktree (its own working directory, shared object store) per branch, so
concurrent agents never collide in a single tree. See `workspace.py`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import NamedTuple

__all__ = [
    "Commit",
    "GitError",
    "add_all",
    "checkout",
    "branch_exists",
    "branches",
    "clone",
    "commit",
    "current_branch",
    "default_branch",
    "fetch",
    "has_commits_ahead",
    "head_sha",
    "is_bare",
    "is_ancestor",
    "is_dirty",
    "is_repo",
    "log_delta",
    "merge",
    "merge_ff_only",
    "merge_abort",
    "push",
    "ref_sha",
    "remotes",
    "remote_url",
    "reset_hard",
    "worktree_add",
    "worktree_add_existing",
    "worktrees",
    "worktree_remove",
]


class GitError(Exception):
    """A git command exited non-zero (carries git's own stderr)."""


def _git(
    args: list[str],
    *,
    cwd: str | Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        text=True,
        capture_output=True,
    )
    if check and proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip() or '<no output>'}"
        )
    return proc


def clone(url: str, dest: str | Path, *, depth: int | None = None) -> Path:
    """Clone ``url`` into ``dest`` (created if absent); return the clone path."""
    args = ["clone"]
    if depth is not None:
        args += ["--depth", str(depth)]
    args += [url, str(dest)]
    _git(args)
    return Path(dest)


def fetch(cwd: str | Path, *, remote: str = "origin", prune: bool = True) -> None:
    """Update remote-tracking refs so branches cut off ``origin/*`` are current."""
    args = ["fetch", remote]
    if prune:
        args.append("--prune")
    _git(args, cwd=cwd)


def default_branch(cwd: str | Path, *, remote: str = "origin") -> str:
    """The remote's default branch (e.g. ``main``), read from ``origin/HEAD``.

    Falls back to ``main`` when the symbolic ref is not set (a freshly created
    bare remote with no HEAD), which keeps offline tests robust.
    """
    proc = _git(["symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD"], cwd=cwd, check=False)
    ref = proc.stdout.strip()
    prefix = f"{remote}/"
    if ref.startswith(prefix):
        return ref[len(prefix) :]
    return "main"


def worktree_add(
    cwd: str | Path, path: str | Path, branch: str, *, base: str, reset: bool = False
) -> Path:
    """Create ``branch`` off ``base`` checked out in a new worktree at ``path``.

    ``base`` is any commit-ish (typically ``origin/<default>``). The worktree has
    its own working directory but shares the clone's object store, so this is the
    cheap per-branch isolation the lifecycle uses for parallel subtasks. ``reset``
    uses ``-B`` (create-or-reset the branch to ``base``) so a re-dispatch of the
    same branch starts clean instead of failing on an existing branch.
    """
    flag = "-B" if reset else "-b"
    _git(["worktree", "add", flag, branch, str(path), base], cwd=cwd)
    return Path(path)


def worktree_add_existing(cwd: str | Path, path: str | Path, branch: str) -> Path:
    """Check out an existing local ``branch`` in a new worktree."""
    _git(["worktree", "add", str(path), branch], cwd=cwd)
    return Path(path)


def worktree_remove(cwd: str | Path, path: str | Path, *, force: bool = True) -> None:
    """Remove a worktree created by `worktree_add` (best-effort cleanup)."""
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(path))
    _git(args, cwd=cwd, check=False)


def add_all(cwd: str | Path) -> None:
    """Stage every change in the worktree (``git add -A``)."""
    _git(["add", "-A"], cwd=cwd)


def is_dirty(cwd: str | Path) -> bool:
    """True if the worktree has staged or unstaged changes."""
    proc = _git(["status", "--porcelain"], cwd=cwd)
    return bool(proc.stdout.strip())


def commit(cwd: str | Path, message: str) -> str:
    """Commit staged changes with ``message``; return the new HEAD sha."""
    _git(["commit", "-m", message], cwd=cwd)
    return head_sha(cwd)


def head_sha(cwd: str | Path) -> str:
    """The current HEAD commit sha."""
    return _git(["rev-parse", "HEAD"], cwd=cwd).stdout.strip()


def ref_sha(cwd: str | Path, ref: str) -> str:
    """Resolve ``ref`` to its commit SHA."""
    return _git(["rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=cwd).stdout.strip()


def current_branch(cwd: str | Path) -> str:
    """The checked-out branch name."""
    return _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd).stdout.strip()


def branches(cwd: str | Path) -> list[str]:
    """Return local branch names in git's deterministic ref order."""
    proc = _git(["for-each-ref", "--format=%(refname:short)", "refs/heads"], cwd=cwd)
    return [line for line in proc.stdout.splitlines() if line]


def branch_exists(cwd: str | Path, branch: str) -> bool:
    """Whether ``branch`` names an exact local branch."""
    proc = _git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=cwd, check=False)
    return proc.returncode == 0


def worktrees(cwd: str | Path) -> dict[str, Path]:
    """Map checked-out local branches to their worktree paths."""
    proc = _git(["worktree", "list", "--porcelain"], cwd=cwd)
    result: dict[str, Path] = {}
    path: Path | None = None
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree "))
        elif line.startswith("branch refs/heads/") and path is not None:
            result[line.removeprefix("branch refs/heads/")] = path
    return result


def has_commits_ahead(cwd: str | Path, base: str) -> bool:
    """True if the current branch has commits ``base`` does not (something to PR)."""
    proc = _git(["rev-list", "--count", f"{base}..HEAD"], cwd=cwd)
    return int(proc.stdout.strip() or "0") > 0


class Commit(NamedTuple):
    """One commit's short SHA and subject line, for display."""

    sha: str
    subject: str


def log_delta(cwd: str | Path, base: str, branch: str) -> list[Commit]:
    """Return short SHA and subject for commits in ``branch`` but not ``base``."""
    proc = _git(
        ["log", "--format=%h%x00%s", f"{base}..{branch}"],
        cwd=cwd,
    )
    commits: list[Commit] = []
    for line in proc.stdout.splitlines():
        sha, separator, subject = line.partition("\0")
        if separator:
            commits.append(Commit(sha, subject))
    return commits


def is_ancestor(cwd: str | Path, ancestor: str, descendant: str) -> bool:
    """Whether ``ancestor`` is reachable from ``descendant``."""
    proc = _git(["merge-base", "--is-ancestor", ancestor, descendant], cwd=cwd, check=False)
    if proc.returncode not in (0, 1):
        raise GitError(proc.stderr.strip() or "git merge-base failed")
    return proc.returncode == 0


def push(
    cwd: str | Path,
    branch: str,
    *,
    remote: str = "origin",
    set_upstream: bool = True,
    force: bool = False,
) -> None:
    """Push ``branch`` to ``remote`` (sets upstream by default)."""
    args = ["push"]
    if set_upstream:
        args.append("--set-upstream")
    if force:
        args.append("--force-with-lease")
    args += [remote, branch]
    _git(args, cwd=cwd)


def remotes(cwd: str | Path) -> list[str]:
    """Return configured remote names."""
    return [line for line in _git(["remote"], cwd=cwd).stdout.splitlines() if line]


def remote_url(cwd: str | Path, *, remote: str = "origin") -> str:
    """Return the configured URL for ``remote``."""
    value = _git(["remote", "get-url", remote], cwd=cwd).stdout.strip()
    if not value or "\0" in value or "\n" in value or "\r" in value:
        raise GitError(f"git remote {remote!r} returned an invalid URL")
    return value


def is_repo(cwd: str | Path) -> bool:
    """Whether ``cwd`` is the working tree of a non-bare git repository."""
    proc = _git(["rev-parse", "--is-inside-work-tree"], cwd=cwd, check=False)
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def checkout(cwd: str | Path, ref: str) -> None:
    """Check out ``ref`` (a branch/commit) in the working tree at ``cwd``."""
    _git(["checkout", ref], cwd=cwd)


def reset_hard(cwd: str | Path, ref: str) -> None:
    """Hard-reset the current branch to ``ref`` (used to sync a clone's base)."""
    _git(["reset", "--hard", ref], cwd=cwd)


def merge(cwd: str | Path, ref: str, *, message: str, no_ff: bool = True) -> str:
    """Merge ``ref`` into the checked-out branch; return the new HEAD sha.

    ``--no-ff`` by default so the merge is an explicit, revertable commit even when
    a fast-forward is possible — the local equivalent of a squash/merge PR.
    """
    args = ["merge", "--no-edit", "-m", message]
    if no_ff:
        args.append("--no-ff")
    args.append(ref)
    _git(args, cwd=cwd)
    return head_sha(cwd)


def merge_ff_only(cwd: str | Path, ref: str) -> str:
    """Fast-forward the current branch to ``ref`` or raise `GitError`."""
    _git(["merge", "--ff-only", ref], cwd=cwd)
    return head_sha(cwd)


def merge_abort(cwd: str | Path) -> None:
    """Abort an in-progress conflicted merge, restoring the pre-merge tree."""
    _git(["merge", "--abort"], cwd=cwd, check=False)


def is_bare(cwd: str | Path) -> bool:
    """True if the repo at ``cwd`` is bare (has no working tree)."""
    proc = _git(["rev-parse", "--is-bare-repository"], cwd=cwd, check=False)
    return proc.stdout.strip() == "true"
