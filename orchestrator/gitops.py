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

import os
import subprocess
from pathlib import Path
from typing import NamedTuple

__all__ = [
    "Commit",
    "GitError",
    "NothingToCommit",
    "add_all",
    "checkout",
    "branch_exists",
    "branches",
    "clone",
    "commit",
    "common_dir",
    "configure_repo_hooks",
    "current_branch",
    "default_branch",
    "delete_branch",
    "fetch",
    "hooks_dir",
    "has_commits_ahead",
    "head_sha",
    "is_bare",
    "is_ancestor",
    "is_dirty",
    "is_repo",
    "is_valid_branch_name",
    "log_delta",
    "merge",
    "merge_base_into_branch",
    "merge_ff_only",
    "merge_abort",
    "push",
    "ref_sha",
    "remotes",
    "remote_url",
    "reset_hard",
    "worktree_add",
    "worktree_add_detached",
    "worktree_add_existing",
    "worktrees",
    "worktree_remove",
]


class GitError(Exception):
    """A git command exited non-zero (carries git's own stderr)."""


class NothingToCommit(GitError):
    """A requested commit had no tree change to record."""


def common_dir(cwd: str | Path) -> Path:
    """Return the canonical shared git common directory for any linked worktree."""
    proc = _git(["rev-parse", "--git-common-dir"], cwd=cwd)
    value = Path(proc.stdout.strip())
    if not value.is_absolute():
        value = Path(cwd) / value
    return value.resolve()


def configure_repo_hooks(cwd: str | Path) -> Path | None:
    """Point this checkout at its tracked ``.githooks`` directory, when present.

    Git clone does not copy repository-local config. Configuring the execution
    clone is therefore required before ``git worktree add`` for a tracked
    post-checkout hook to run while the new worktree is populated.
    """
    hooks = (Path(cwd) / ".githooks").resolve()
    if not hooks.is_dir():
        return None
    _git(["config", "core.hooksPath", str(hooks)], cwd=cwd)
    return hooks


def hooks_dir(cwd: str | Path) -> Path:
    """Return Git's effective hooks directory for this checkout."""
    value = Path(_git(["rev-parse", "--git-path", "hooks"], cwd=cwd).stdout.strip())
    if not value.is_absolute():
        value = Path(cwd) / value
    return value.resolve()


def _git(
    args: list[str],
    *,
    cwd: str | Path | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        text=True,
        capture_output=True,
        env={**os.environ, **env} if env is not None else None,
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
    """Return the remote's default branch, refusing to guess when ambiguous.

    The remote HEAD is authoritative when it names an existing tracking ref. If
    it is unavailable, an upstream configured on the checked-out branch or a
    sole remote branch is sufficient.  The only no-ref fallback is an unborn
    repository: its symbolic local HEAD is Git's explicit initial-branch choice.
    """
    proc = _git(["symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD"], cwd=cwd, check=False)
    ref = proc.stdout.strip()
    prefix = f"{remote}/"
    if (
        ref.startswith(prefix)
        and _git(
            ["show-ref", "--verify", "--quiet", f"refs/remotes/{ref}"], cwd=cwd, check=False
        ).returncode
        == 0
    ):
        return ref[len(prefix) :]

    current = _git(
        ["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=cwd, check=False
    ).stdout.strip()
    if current:
        upstream_remote = _git(
            ["config", "--get", f"branch.{current}.remote"], cwd=cwd, check=False
        ).stdout.strip()
        upstream_merge = _git(
            ["config", "--get", f"branch.{current}.merge"], cwd=cwd, check=False
        ).stdout.strip()
        candidate = upstream_merge.removeprefix("refs/heads/")
        if (
            upstream_remote == remote
            and candidate
            and _git(
                ["show-ref", "--verify", "--quiet", f"refs/remotes/{remote}/{candidate}"],
                cwd=cwd,
                check=False,
            ).returncode
            == 0
        ):
            return candidate

    refs = _git(
        ["for-each-ref", "--format=%(refname:strip=3)", f"refs/remotes/{remote}"],
        cwd=cwd,
        check=False,
    ).stdout.splitlines()
    candidates = sorted(ref for ref in refs if ref and ref != "HEAD")
    if len(candidates) == 1:
        return candidates[0]
    if not candidates and current:
        remote_exists = _git(["remote", "get-url", remote], cwd=cwd, check=False).returncode == 0
        unborn = _git(["rev-parse", "--verify", "HEAD"], cwd=cwd, check=False).returncode != 0
        if unborn or not remote_exists:
            return current
    detail = ", ".join(candidates) if candidates else "none"
    raise GitError(
        f"cannot determine default branch for remote {remote!r}: origin/HEAD is missing or stale "
        f"and plausible remote branches are {detail}; pass an explicit base_branch"
    )


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


def worktree_add_detached(cwd: str | Path, path: str | Path, ref: str) -> Path:
    """Check out ``ref`` detached in a new scratch worktree."""
    _git(["worktree", "add", "--detach", str(path), ref], cwd=cwd)
    return Path(path)


def worktree_remove(
    cwd: str | Path, path: str | Path, *, force: bool = True, check: bool = False
) -> None:
    """Remove a worktree, optionally surfacing Git's refusal to the caller."""
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(path))
    _git(args, cwd=cwd, check=check)


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


def commit_empty(cwd: str | Path, message: str) -> str:
    """Create an explicit metadata-only commit and return its SHA."""
    _git(["commit", "--allow-empty", "-m", message], cwd=cwd)
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


def delete_branch(cwd: str | Path, branch: str) -> None:
    """Delete one exact local branch after its worktree has been removed."""
    _git(["branch", "-D", branch], cwd=cwd)


def is_valid_branch_name(branch: str) -> bool:
    """Validate a literal local branch name with Git's authoritative ref parser."""
    if not branch or branch.startswith("-"):
        return False
    return _git(["check-ref-format", f"refs/heads/{branch}"], check=False).returncode == 0


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


def stale_worktree_branches(cwd: str | Path) -> dict[str, Path]:
    """Map branches whose registered worktrees Git reports as safely prunable."""
    proc = _git(["worktree", "list", "--porcelain"], cwd=cwd)
    result: dict[str, Path] = {}
    path: Path | None = None
    branch: str | None = None
    for line in [*proc.stdout.splitlines(), ""]:
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree "))
            branch = None
        elif line.startswith("branch refs/heads/"):
            branch = line.removeprefix("branch refs/heads/")
        elif line.startswith("prunable ") and path is not None and branch is not None:
            result[branch] = path
        elif not line:
            path = None
            branch = None
    return result


def worktree_prune(cwd: str | Path) -> None:
    """Remove registrations Git considers prunable without an expiry delay."""
    _git(["worktree", "prune", "--expire", "now"], cwd=cwd)


def has_commits_ahead(cwd: str | Path, base: str) -> bool:
    """True if the current branch has commits ``base`` does not (something to PR)."""
    proc = _git(["rev-list", "--count", f"{base}..HEAD"], cwd=cwd)
    return int(proc.stdout.strip() or "0") > 0


class Commit(NamedTuple):
    """One commit's short SHA and subject line, for display."""

    sha: str
    subject: str


class CommitMessage(NamedTuple):
    """One commit's full SHA and message, for provenance validation."""

    sha: str
    message: str


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


def commit_detail(cwd: str | Path, sha: str) -> str:
    """Return the durable human-readable commit metadata and patch for ``sha``."""
    return _git(
        ["show", "--no-ext-diff", "--format=fuller", "--stat", "--patch", sha],
        cwd=cwd,
    ).stdout


def log_messages(cwd: str | Path, base: str, branch: str) -> list[CommitMessage]:
    """Return full commit messages in ``branch`` but not ``base``, oldest first."""
    proc = _git(["log", "--reverse", "--format=%H%x00%B%x00%x1e", f"{base}..{branch}"], cwd=cwd)
    commits: list[CommitMessage] = []
    for record in proc.stdout.split("\x1e"):
        value = record.strip("\n\x00")
        if not value:
            continue
        sha, separator, message = value.partition("\0")
        if separator:
            commits.append(CommitMessage(sha, message.rstrip()))
    return commits


def is_ancestor(cwd: str | Path, ancestor: str, descendant: str) -> bool:
    """Whether ``ancestor`` is reachable from ``descendant``."""
    proc = _git(["merge-base", "--is-ancestor", ancestor, descendant], cwd=cwd, check=False)
    if proc.returncode not in (0, 1):
        raise GitError(proc.stderr.strip() or "git merge-base failed")
    return proc.returncode == 0


def merge_base_into_branch(
    cwd: str | Path,
    base: str,
    *,
    message: str,
    abort_on_conflict: bool = True,
) -> bool:
    """Merge ``base`` into the checked-out branch, optionally retaining conflicts.

    Return ``False`` only when the merge conflicts. Other git failures remain
    errors so callers do not mistake an invalid ref or broken repository for a
    normal synchronization conflict.
    """
    try:
        merge(cwd, base, message=message, no_ff=False)
    except GitError:
        unmerged = _git(["diff", "--name-only", "--diff-filter=U"], cwd=cwd, check=False)
        if unmerged.returncode != 0 or not unmerged.stdout.strip():
            raise
        if abort_on_conflict:
            merge_abort(cwd)
        return False
    return True


def unmerged_paths(cwd: str | Path) -> list[str]:
    """Return paths that still have unresolved merge stages."""
    return [
        path
        for path in _git(["diff", "--name-only", "--diff-filter=U"], cwd=cwd).stdout.splitlines()
        if path
    ]


def push(
    cwd: str | Path,
    branch: str,
    *,
    remote: str = "origin",
    set_upstream: bool = True,
    force: bool = False,
    env: dict[str, str] | None = None,
) -> None:
    """Push ``branch`` to ``remote`` with an optional environment overlay."""
    args = ["push"]
    if set_upstream:
        args.append("--set-upstream")
    if force:
        args.append("--force-with-lease")
    args += [remote, branch]
    _git(args, cwd=cwd, env=env)


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

    ``--no-ff`` by default so the merge is represented by an explicit merge commit
    even when a fast-forward is possible.
    """
    args = ["merge", "--no-edit", "-m", message]
    if no_ff:
        args.append("--no-ff")
    args.append(ref)
    _git(args, cwd=cwd)
    return head_sha(cwd)


def merge_squash(cwd: str | Path, ref: str, *, message: str) -> str:
    """Squash-merge ``ref``, commit ``message``, and return the new HEAD sha."""
    _git(["merge", "--squash", ref], cwd=cwd)
    if not is_dirty(cwd):
        raise NothingToCommit(
            f"squash merge of {ref!r} produced no tree change; its content is already present"
        )
    _git(["commit", "-m", message], cwd=cwd)
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
