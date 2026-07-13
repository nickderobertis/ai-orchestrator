"""A clone/worktree pool for driving the lifecycle across arbitrary repos.

`Workspace` acquires each repo once (a clone under its root) and hands out a
fresh **worktree per branch**, so N parallel subtasks against a repo get N
isolated working directories over one shared object store — the efficient
isolation for maximizing parallelism without re-cloning. Different repos get
different clones. All git is real (`gitops`); nothing here talks to GitHub.

A repo is named loosely — ``"onejudge"`` (the default owner is filled in),
``"someone/thing"``, or a full clone URL — and normalized once at the boundary.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path

from . import gitops

__all__ = ["DEFAULT_OWNER", "RepoRef", "Workspace", "normalize_repo"]

DEFAULT_OWNER = "nickderobertis"


@dataclass(frozen=True)
class RepoRef:
    """A normalized repo reference: its ``owner/name`` slug and a clone URL.

    ``local`` marks a repo that lives on the filesystem (a path or ``file://``
    URL) rather than on GitHub, so the lifecycle merges into its default branch
    directly instead of opening a PR.
    """

    owner: str
    name: str
    url: str
    local: bool = False

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def dir_key(self) -> str:
        """A filesystem-safe key for this repo's clone directory."""
        return re.sub(r"[^A-Za-z0-9._-]+", "-", f"{self.owner}__{self.name}").strip("-")


def _looks_like_path(spec: str) -> bool:
    return (
        spec.startswith(("/", "./", "../", "~", "file://"))
        or Path(spec).expanduser().exists()
        or Path(spec).expanduser().with_suffix(".git").exists()
    )


def normalize_repo(spec: str, *, default_owner: str = DEFAULT_OWNER) -> RepoRef:
    """Resolve a loose repo spec into a `RepoRef`.

    Accepts a GitHub ``name`` (owner defaulted), ``owner/name``, or full GitHub
    URL — and a **local path** (absolute/relative, ``~``-prefixed, or ``file://``)
    or existing directory, which yields a ``local=True`` ref.
    """
    spec = spec.strip()
    if not spec:
        raise ValueError("empty repo spec")

    url_match = re.match(
        r"^(?:https://github\.com/|git@github\.com:)([^/]+)/(.+?)(?:\.git)?/?$", spec
    )
    if url_match:
        owner, name = url_match.group(1), url_match.group(2)
        return RepoRef(owner=owner, name=name, url=spec)

    if _looks_like_path(spec):
        raw = spec[len("file://") :] if spec.startswith("file://") else spec
        path = Path(raw).expanduser().resolve()
        name = path.name.removesuffix(".git")
        return RepoRef(owner="local", name=name or "repo", url=str(path), local=True)

    if "/" in spec:
        owner, _, name = spec.partition("/")
    else:
        owner, name = default_owner, spec
    name = name.removesuffix(".git")
    if not owner or not name:
        raise ValueError(f"could not parse repo spec {spec!r}")
    return RepoRef(owner=owner, name=name, url=f"https://github.com/{owner}/{name}.git")


def _safe_branch_dir(branch: str) -> str:
    """A filesystem-safe directory name derived from a branch name."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-") or "wt"


class Workspace:
    """Owns clones and worktrees under a single root directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        # Clone/worktree creation touches a repo's shared git metadata, so those
        # ops are serialized per repo (concurrent `git worktree add` on one clone
        # races on its lock). Different repos proceed in parallel; the slow part
        # (the dispatch) is never held.
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def _repo_lock(self, repo: RepoRef) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(repo.dir_key, threading.Lock())

    def clone_dir(self, repo: RepoRef) -> Path:
        return self.root / repo.dir_key / "repo"

    def _worktree_root(self, repo: RepoRef) -> Path:
        return self.root / repo.dir_key / "worktrees"

    def ensure_clone(self, repo: RepoRef, *, url: str | None = None) -> Path:
        """Clone the repo if absent (else fetch to refresh ``origin/*``)."""
        dest = self.clone_dir(repo)
        with self._repo_lock(repo):
            if (dest / ".git").exists():
                gitops.fetch(dest)
                return dest
            dest.parent.mkdir(parents=True, exist_ok=True)
            return gitops.clone(url or repo.url, dest)

    def worktree(self, repo: RepoRef, branch: str, *, base: str) -> Path:
        """Add a fresh worktree for ``branch`` cut off ``base`` (e.g. ``origin/main``).

        Any stale worktree at the target path is removed first so a re-dispatch
        starts clean.
        """
        clone = self.clone_dir(repo)
        path = self._worktree_root(repo) / _safe_branch_dir(branch)
        with self._repo_lock(repo):
            if path.exists():
                gitops.worktree_remove(clone, path)
            path.parent.mkdir(parents=True, exist_ok=True)
            # reset=True so a re-dispatch of the same branch starts clean at base
            # (removing the stale worktree leaves its branch ref behind).
            return gitops.worktree_add(clone, path, branch, base=base, reset=True)

    def remove_worktree(self, repo: RepoRef, path: str | Path) -> None:
        """Tear down a worktree once its subtask is done."""
        gitops.worktree_remove(self.clone_dir(repo), path)
