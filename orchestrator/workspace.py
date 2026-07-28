"""A checkout/worktree pool for driving repo lifecycles.

`Workspace` independently resolves the checkout used for execution and the
repository identity used for publication. It hands out a fresh **worktree per
branch**, cut from a clone that belongs to this run alone.

That per-run clone is what keeps concurrent orchestrators out of each other's
way. Git's worktree registry, its ref store, and its own locks are all properties
of one clone, so sharing a clone between runs means sharing the machinery that
adds, prunes, and removes worktrees — and one run's cleanup can then reach a
sibling's live tree. Each run gets its own clone instead, made with ``--shared``
against the identity's execution checkout so it borrows that object store rather
than copying it: distinct registries and distinct locks, at the cost of little
more than a set of refs.

A repo is named loosely — ``"onejudge"`` (the default owner is filled in),
``"someone/thing"``, or a full clone URL — and normalized once at the boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NewType, Protocol

from . import gitops
from .coordination import (
    LockTimeout,
    advisory_lock,
    atomic_json,
    git_lock_identity,
    observe_harness,
    process_start_identity,
)

if TYPE_CHECKING:
    from .registry import Registry

__all__ = [
    "DEFAULT_OWNER",
    "CACHE_ENV",
    "IdentityKey",
    "RepoRef",
    "RepositoryType",
    "Workflow",
    "Workspace",
    "WorkspaceError",
    "WorkspaceSelection",
    "normalize_repo",
]

DEFAULT_OWNER = "nickderobertis"
CACHE_ENV = "ORCHESTRATOR_CACHE_DIR"
Workflow = Literal["local", "remote"]
RepositoryType = Literal["single-owner", "team"]
IdentityKey = NewType("IdentityKey", str)

#: Per-run state lives under a ``runs/`` level so the flat per-branch directories
#: an earlier layout created alongside it are never mistaken for run roots — and
#: are never reaped, since nothing here claims to own them.
RUNS_DIR_NAME = "runs"
CLONE_DIR_NAME = ".clone"
OWNER_RECORD_NAME = "owner.json"


class WorkspaceError(RuntimeError):
    """A checkout is unsafe to mutate or cannot satisfy a lifecycle operation."""


class RepoResolver(Protocol):
    """Resolve a repository spec to its canonical local checkout."""

    def __call__(self, spec: str) -> Path: ...


@dataclass(frozen=True)
class WorkspaceSelection:
    """The checkout roles and identity-level publication decision for one repo."""

    publication_checkout: Path
    execution_checkout: Path
    publication_identity: IdentityKey
    workflow: Workflow | None
    repo_type: RepositoryType | None
    gate: str | None


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


def _run_lease_identity(run_root: Path) -> str:
    return f"workspace-run:{run_root.resolve()}"


def _run_owner_is_live(run_root: Path) -> bool:
    """Whether the process that created this run root is still running.

    A bare pid cannot answer that — the kernel recycles pids — so the recorded
    owner pairs it with the start token the kernel stamped on that process. An
    absent or unreadable record answers "not identifiable", which is safe here only
    because the caller has already proven the run's lease is unheld.
    """
    try:
        record = json.loads((run_root / OWNER_RECORD_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    pid, start = record.get("pid"), record.get("process_start")
    if not isinstance(pid, int) or isinstance(pid, bool) or not isinstance(start, int):
        return False
    return process_start_identity(pid) == start


def _abandoned_run_is_reclaimable(run_root: Path) -> bool:
    """Whether an unowned run root provably holds nothing anyone could still want.

    Deleting a sibling run's tree is the exact failure this layout exists to
    prevent, so removal needs more than "its owner is gone": the run must also hold
    no commit that has not reached its origin. Anything this cannot read, it keeps.
    """
    if _run_owner_is_live(run_root):
        return False
    clone = run_root / CLONE_DIR_NAME
    if not clone.is_dir():
        return not any(entry.name != OWNER_RECORD_NAME for entry in run_root.iterdir())
    return gitops.is_repo(clone) and not gitops.unpublished_branches(clone)


class Workspace:
    """Cuts task worktrees from a private clone of the selected execution checkout."""

    def __init__(
        self,
        root: str | Path,
        *,
        resolver: RepoResolver | None = None,
        workflow: Workflow | None = None,
        repo_type: RepositoryType | None = None,
        run_token: str | None = None,
    ) -> None:
        self.root = Path(root)
        # One token per workspace, not per process: a process may drive several
        # workspaces, and giving each its own clone keeps their git metadata apart
        # for the same reason it keeps separate processes apart. Passing an existing
        # token is how a caller rejoins a run already under way — a re-dispatch that
        # has to see the worktrees and branches the first attempt left behind.
        self.run_token = run_token or f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._repo_type = repo_type
        self._workflow: Callable[[RepoRef], Workflow | None]
        self._registry: Registry | None = None
        if resolver is None:
            # Lazy import avoids registry -> workspace normalization becoming an
            # import cycle.
            from .registry import Registry

            registry = Registry()
            self._registry = registry
            resolver = registry.resolve

            def registered_workflow(repo: RepoRef) -> Workflow | None:
                selected = self._selections.get(repo.dir_key)
                return selected.workflow if selected is not None else None

            self._workflow = registered_workflow
        else:
            self._workflow = lambda repo: workflow
        self._resolver = resolver
        self._checkouts: dict[str, Path] = {}
        self._clones: dict[str, Path] = {}
        self._selections: dict[str, WorkspaceSelection] = {}
        # Clone/worktree creation touches a repo's git metadata, so those ops are
        # serialized per repo (concurrent `git worktree add` on one clone races on
        # its lock). Different repos proceed in parallel; the slow part (the
        # dispatch) is never held.
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._worktree_leases: dict[Path, AbstractContextManager[None]] = {}
        # Held until this process exits, which is the point: the kernel releases it
        # on death, so a crashed run's tree becomes reclaimable without anyone
        # having to decide that it crashed.
        self._run_leases: dict[Path, AbstractContextManager[None]] = {}
        self._reaped: set[str] = set()

    def workflow(self, repo: RepoRef) -> Workflow | None:
        """Return the registered workflow, if the resolver exposes registry metadata."""
        return self._workflow(repo)

    def repo_ref(self, spec: str) -> RepoRef:
        """Normalize a repo spec through the registry when one backs this workspace."""
        return self._registry.repo_ref(spec) if self._registry is not None else normalize_repo(spec)

    def selection(self, repo: RepoRef) -> WorkspaceSelection:
        """Return the resolved checkout roles and publication decision."""
        try:
            return self._selections[repo.dir_key]
        except KeyError as exc:
            raise RuntimeError(f"repository {repo.slug} has not been resolved") from exc

    def _repo_lock(self, repo: RepoRef) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(repo.dir_key, threading.Lock())

    def clone_dir(self, repo: RepoRef) -> Path:
        """Return this run's private clone (kept as a lifecycle compatibility name)."""
        try:
            return self._clones[repo.dir_key]
        except KeyError as exc:
            raise RuntimeError(f"repository {repo.slug} has not been resolved") from exc

    def execution_checkout(self, repo: RepoRef) -> Path:
        """Return the shared checkout this run's clone borrows its objects from."""
        try:
            return self._checkouts[repo.dir_key]
        except KeyError as exc:
            raise RuntimeError(f"repository {repo.slug} has not been resolved") from exc

    def _worktree_root(self, repo: RepoRef) -> Path:
        return self.root / repo.dir_key / RUNS_DIR_NAME / self.run_token

    def _claim_run_root(self, run_root: Path) -> None:
        run_root.mkdir(parents=True, exist_ok=True)
        lease = advisory_lock(_run_lease_identity(run_root), timeout=0)
        lease.__enter__()
        self._run_leases[run_root] = lease
        owner = process_start_identity(os.getpid())
        atomic_json(
            run_root / OWNER_RECORD_NAME,
            {"pid": os.getpid(), "process_start": owner, "token": self.run_token},
        )

    def _reap_abandoned_runs(self, repo: RepoRef) -> None:
        """Reclaim run roots whose owner is gone and whose work all reached origin."""
        if repo.dir_key in self._reaped:
            return
        self._reaped.add(repo.dir_key)
        runs = self.root / repo.dir_key / RUNS_DIR_NAME
        mine = self._worktree_root(repo).resolve()
        for candidate in sorted(runs.glob("*")):
            if not candidate.is_dir() or candidate.is_symlink() or candidate.resolve() == mine:
                continue
            # Every reason to keep a candidate — a live lease, a live owner, an
            # unpublished commit, an unreadable tree — has to win, so anything that
            # fails to prove reclaimability leaves the tree exactly as it is.
            with (
                suppress(LockTimeout, OSError, gitops.GitError),
                advisory_lock(_run_lease_identity(candidate), timeout=0),
            ):
                if _abandoned_run_is_reclaimable(candidate):
                    shutil.rmtree(candidate)

    def _worktree_lease_identity(self, clone: Path, path: Path) -> str:
        return f"worktree:{gitops.common_dir(clone)}:{path.resolve()}"

    def _acquire_worktree_lease(self, clone: Path, path: Path) -> None:
        lease = advisory_lock(self._worktree_lease_identity(clone, path), timeout=0)
        lease.__enter__()
        self._worktree_leases[path.resolve()] = lease

    def _release_worktree_lease(self, path: str | Path) -> None:
        lease = self._worktree_leases.pop(Path(path).resolve(), None)
        if lease is not None:
            lease.__exit__(None, None, None)

    def ensure_cache_dir(self, repo: RepoRef) -> Path:
        """Create and return this repository identity's persistent build cache."""
        identity = self.selection(repo).publication_identity
        digest = hashlib.sha256(str(identity).encode("utf-8")).hexdigest()
        override = os.environ.get("AI_ORCHESTRATOR_HOME")
        if override is not None and not override.strip():
            raise WorkspaceError("AI_ORCHESTRATOR_HOME must not be empty")
        state_root = Path(override) if override else Path.home() / ".ai-orchestrator"
        cache = (state_root / "cache" / digest).resolve()
        cache.mkdir(parents=True, exist_ok=True)
        return cache

    @staticmethod
    def _assert_publication_ready(checkout: Path, branch: str) -> None:
        if gitops.is_dirty(checkout):
            raise WorkspaceError(
                f"publication checkout {checkout} is dirty; clean it before dispatch"
            )
        current = gitops.current_branch(checkout)
        if current != branch:
            raise WorkspaceError(
                f"publication checkout {checkout} has branch {current!r} checked out; "
                f"check out root branch {branch!r} before dispatch"
            )

    def ensure_clone(
        self,
        repo: RepoRef,
        *,
        url: str | None = None,
        base_branch: str | None = None,
        execution_checkout: str | Path | None = None,
        repo_type: RepositoryType | None = None,
    ) -> Path:
        """Fast-forward the selected execution checkout and return this run's clone."""
        with self._repo_lock(repo):
            if self._registry is not None:
                selected = self._registry.select(
                    repo.slug if repo.local else repo.url,
                    execution_checkout=execution_checkout,
                    repo_type=repo_type or self._repo_type,
                )
                selection = WorkspaceSelection(
                    publication_checkout=selected.publication_checkout,
                    execution_checkout=selected.execution_checkout,
                    publication_identity=selected.identity,
                    workflow=selected.workflow,
                    repo_type=selected.repo_type,
                    gate=selected.gate,
                )
                checkout = selection.execution_checkout
            else:
                publication = self._resolver(url or repo.url)
                checkout = (
                    Path(execution_checkout).expanduser().resolve()
                    if execution_checkout is not None
                    else publication
                )
                selection = WorkspaceSelection(
                    publication_checkout=publication,
                    execution_checkout=checkout,
                    publication_identity=IdentityKey(url or repo.url),
                    workflow=self._workflow(repo),
                    repo_type=(
                        repo_type
                        or self._repo_type
                        or ("single-owner" if self._workflow(repo) == "local" else None)
                    ),
                    gate=None,
                )
            if not checkout.is_dir() or not gitops.is_repo(checkout):
                raise RuntimeError(f"execution checkout {checkout} is not a git checkout")
            self._checkouts[repo.dir_key] = checkout
            self._selections[repo.dir_key] = selection
            # Deliberately outside every exclusive section: a fetch is idempotent
            # and can take as long as the network does, and holding the shared
            # checkout for it is what turned one slow origin into every other
            # run's failed dispatch.
            started = time.monotonic()
            gitops.fetch(checkout)
            observe_harness(
                "setup-finished",
                {"operation": "fetch", "seconds": max(0.0, time.monotonic() - started)},
            )
            base = base_branch or gitops.default_branch(checkout)
            with advisory_lock(git_lock_identity(gitops.common_dir(checkout))):
                if gitops.is_dirty(checkout):
                    raise WorkspaceError(
                        f"execution checkout {checkout} is dirty; clean it before dispatch"
                    )
                publication = selection.publication_checkout
                if gitops.common_dir(publication) == gitops.common_dir(checkout):
                    self._assert_publication_ready(publication, base)
                else:
                    with advisory_lock(git_lock_identity(gitops.common_dir(publication))):
                        self._assert_publication_ready(publication, base)
                gitops.checkout(checkout, base)
                gitops.merge_ff_only(checkout, f"origin/{base}")
                gitops.configure_repo_hooks(checkout)
                gitops.retain_objects_for_borrowers(checkout)
                origin = gitops.remote_url(checkout)
                clone = self._ensure_run_clone(repo, checkout, origin=origin, base=base)
            # This clone belongs to this run alone, so its own fetch — the one that
            # actually has to reach origin's branches — contends with nothing, and
            # neither does sweeping the run roots of dead siblings.
            gitops.fetch(clone)
            self._reap_abandoned_runs(repo)
            self._clones[repo.dir_key] = clone
            self.ensure_cache_dir(repo)
            return clone

    def _ensure_run_clone(self, repo: RepoRef, checkout: Path, *, origin: str, base: str) -> Path:
        clone = self._worktree_root(repo) / CLONE_DIR_NAME
        if clone.is_dir() and gitops.is_repo(clone):
            return clone
        self._claim_run_root(self._worktree_root(repo))
        started = time.monotonic()
        gitops.clone_sharing(checkout, clone, origin=origin, base=base)
        # The clone's own working tree is never populated, so a tracked hooks
        # directory has to come from the checkout it was cut from — which is the
        # same absolute path every worktree already resolved hooks through.
        hooks = (checkout / ".githooks").resolve()
        if hooks.is_dir():
            gitops.set_hooks_path(clone, hooks)
        observe_harness(
            "setup-finished",
            {"operation": "run-clone", "seconds": max(0.0, time.monotonic() - started)},
        )
        return clone

    def _adopt_preserved_branch(self, repo: RepoRef, clone: Path, branch: str) -> bool:
        if gitops.branch_exists(clone, branch):
            return True
        return gitops.import_branch(clone, self.execution_checkout(repo), branch)

    def adopt_preserved_branch(self, repo: RepoRef, branch: str) -> bool:
        """Bring a branch an earlier run preserved into this run's clone.

        This clone was cut fresh from the shared checkout's remote-tracking refs, so
        a branch that never reached origin is not in it. Adopting one is what lets a
        resume or a recovery continue that work rather than start a new branch of
        the same name off the base. Returns whether the branch is now present.
        """
        clone = self.clone_dir(repo)
        with self._repo_lock(repo), advisory_lock(git_lock_identity(gitops.common_dir(clone))):
            return self._adopt_preserved_branch(repo, clone, branch)

    def worktree(self, repo: RepoRef, branch: str, *, base: str) -> Path:
        """Add a fresh worktree for ``branch`` cut off ``base`` (e.g. ``origin/main``).

        Any stale worktree at the target path is removed first so a re-dispatch
        starts clean.
        """
        clone = self.clone_dir(repo)
        path = self._worktree_root(repo) / _safe_branch_dir(branch)
        with self._repo_lock(repo), advisory_lock(git_lock_identity(gitops.common_dir(clone))):
            self._adopt_preserved_branch(repo, clone, branch)
            active = gitops.worktrees(clone)
            if branch in active:
                registered = active[branch]
                stale = gitops.stale_worktree_branches(clone)
                if branch in stale or not registered.exists():
                    gitops.worktree_prune(clone)
                    active = gitops.worktrees(clone)
                if branch in active:
                    try:
                        self._acquire_worktree_lease(clone, registered)
                    except LockTimeout:
                        raise RuntimeError(
                            f"branch {branch!r} is active in {active[branch]}; use a unique run "
                            "or resume that worktree explicitly"
                        ) from None
                    try:
                        gitops.worktree_remove(clone, registered, check=True)
                    except Exception:
                        self._release_worktree_lease(registered)
                        raise
                    if registered.resolve() != path.resolve():
                        try:
                            self._acquire_worktree_lease(clone, path)
                        finally:
                            self._release_worktree_lease(registered)
                    active = gitops.worktrees(clone)
            if path.exists():
                raise RuntimeError(
                    f"worktree path {path} already exists; inspect and remove it only after "
                    "confirming its run is abandoned"
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.resolve() not in self._worktree_leases:
                self._acquire_worktree_lease(clone, path)
            try:
                started = time.monotonic()
                if gitops.branch_exists(clone, branch):
                    result = gitops.worktree_add_existing(clone, path, branch)
                else:
                    result = gitops.worktree_add(clone, path, branch, base=base, reset=False)
                observe_harness(
                    "setup-finished",
                    {"operation": "worktree", "seconds": max(0.0, time.monotonic() - started)},
                )
                return result
            except Exception:
                self._release_worktree_lease(path)
                raise

    def fast_forward(self, repo: RepoRef, branch: str) -> None:
        """Fetch and fast-forward the caller-selected publication checkout."""
        selected = self._selections.get(repo.dir_key)
        checkout = (
            selected.publication_checkout if selected is not None else self._resolver(repo.url)
        )
        with self._repo_lock(repo), advisory_lock(git_lock_identity(gitops.common_dir(checkout))):
            self._assert_publication_ready(checkout, branch)
            gitops.fetch(checkout)
            gitops.merge_ff_only(checkout, f"origin/{branch}")

    def mirror_branch(self, repo: RepoRef, branch: str) -> None:
        """Copy this run's branch into the shared execution checkout.

        A run's clone is disposable and invisible to everything outside the run. The
        shared checkout is where the harness has always looked for a lifecycle branch
        — to monitor it, to resume it, to recover it — so anything worth outliving
        the run, or worth seeing from outside it, is handed over here.
        """
        clone = self._clones.get(repo.dir_key)
        checkout = self._checkouts.get(repo.dir_key)
        if clone is None or checkout is None or not gitops.branch_exists(clone, branch):
            return
        with advisory_lock(git_lock_identity(gitops.common_dir(checkout))):
            gitops.copy_branch(clone, checkout, branch)

    def _mirror_worktree_branch(self, repo: RepoRef, clone: Path, path: Path) -> None:
        branch = next(
            (
                name
                for name, at in gitops.worktrees(clone).items()
                if at.resolve() == path.resolve()
            ),
            None,
        )
        if branch is not None:
            self.mirror_branch(repo, branch)

    def remove_worktree(self, repo: RepoRef, path: str | Path) -> None:
        """Tear down a worktree once its subtask is done."""
        clone = self.clone_dir(repo)
        try:
            with advisory_lock(git_lock_identity(gitops.common_dir(clone))):
                self._mirror_worktree_branch(repo, clone, Path(path))
                gitops.worktree_remove(clone, path, check=True)
        finally:
            self._release_worktree_lease(path)

    def delete_branch(self, repo: RepoRef, branch: str) -> None:
        """Delete an unneeded lifecycle branch from this run and the shared checkout."""
        clone = self.clone_dir(repo)
        with self._repo_lock(repo), advisory_lock(git_lock_identity(gitops.common_dir(clone))):
            gitops.delete_branch(clone, branch)
        checkout = self._checkouts.get(repo.dir_key)
        if checkout is None:
            return
        with advisory_lock(git_lock_identity(gitops.common_dir(checkout))):
            gitops.delete_branch(checkout, branch, check=False)
