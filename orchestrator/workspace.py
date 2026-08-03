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
import stat
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
    ProcessStart,
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
#: Names one run's directory, and so authorizes rejoining that run's clone.
RunToken = NewType("RunToken", str)

#: Per-run state lives under a ``runs/`` level so the flat per-branch directories
#: an earlier layout created alongside it are never mistaken for run roots — and
#: are never reaped, since nothing here claims to own them.
RUNS_DIR_NAME = "runs"
CLONE_DIR_NAME = ".clone"
OWNER_RECORD_NAME = "owner.json"
#: Keep a small, useful crash history without allowing abandoned clones to grow
#: forever. This mirrors pytest's default of retaining its three newest runs.
RETAINED_INCOMPLETE_RUNS = 3


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


def validate_run_token(token: str) -> RunToken:
    """Accept a run token only if it can safely name one directory.

    The token becomes a path component under the workspace root, so a separator or
    a traversal component in it would place a run's clone somewhere the layout does
    not describe — and put the reaper's `rmtree` there with it.
    """
    if not token or token in {os.curdir, os.pardir} or not re.fullmatch(r"[A-Za-z0-9._-]+", token):
        raise WorkspaceError(
            f"run token {token!r} must be a non-empty name of letters, digits, '.', '_', or '-'"
        )
    return RunToken(token)


@dataclass(frozen=True)
class RunOwner:
    """The process that claimed a run root, identifiable across pid reuse.

    A bare pid is not an identity: the kernel recycles pids, so a recorded pid can
    be reported live forever by an unrelated process that inherits the number.
    Pairing it with the start token the kernel stamped answers the only question
    the reaper asks.
    """

    pid: int
    process_start: ProcessStart | None
    token: RunToken

    @classmethod
    def current(cls, token: RunToken) -> RunOwner:
        return cls(os.getpid(), process_start_identity(os.getpid()), token)

    @classmethod
    def parse(cls, value: object) -> RunOwner | None:
        """Return the recorded owner, or ``None`` when it is not identifiable."""
        match value:
            # `bool` is an `int`, so the accepted shapes below would otherwise take
            # `true` for a pid and call an unidentifiable record identifiable.
            case {"pid": bool()} | {"process_start": bool()}:
                return None
            case {"pid": int(pid), "process_start": int(start), "token": str(token)}:
                return cls(pid, ProcessStart(start), RunToken(token))
            case {"pid": int(pid), "process_start": int(start)}:
                return cls(pid, ProcessStart(start), RunToken(""))
            case _:
                return None

    @classmethod
    def read(cls, run_root: Path) -> RunOwner | None:
        try:
            return cls.parse(json.loads((run_root / OWNER_RECORD_NAME).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None

    def record(self) -> dict[str, object]:
        return {"pid": self.pid, "process_start": self.process_start, "token": self.token}

    def is_live(self) -> bool:
        return self.process_start is not None and process_start_identity(self.pid) == (
            self.process_start
        )


#: A tree a killed worker left behind can still be written to while it is being
#: removed — a build daemon flushing its cache is the ordinary case — and each such
#: write can fail one `rmtree` pass on a directory that was empty a moment earlier.
#: A few passes settle that; anything surviving them is a real problem to report.
_RECLAIM_ATTEMPTS = 3


def _grant_owner_access(path: Path) -> None:
    """Restore this user's ability to delete a tree a worker made read-only.

    Only the owner bits, and only on directories: the delete needs the containing
    directory writable and searchable, and nothing here needs to widen access for
    anybody else. A path this process may not chmod at all is left alone, and the
    removal that follows reports what it could not do.
    """
    for parent, directories, _files in os.walk(path):
        for name in (parent, *(os.path.join(parent, entry) for entry in directories)):
            with suppress(OSError):
                os.chmod(name, os.stat(name).st_mode | stat.S_IRWXU)


def _remove_directory_tree(path: Path) -> None:
    """Delete a directory tree, tolerating the debris a killed worker leaves."""
    failure: OSError | None = None
    for attempt in range(_RECLAIM_ATTEMPTS):
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            return
        except PermissionError as exc:
            failure = exc
            _grant_owner_access(path)
        except OSError as exc:
            failure = exc
            time.sleep(0.1 * (attempt + 1))
        else:
            return
    raise WorkspaceError(
        f"could not reclaim worktree path {path} after {_RECLAIM_ATTEMPTS} attempts: {failure}"
    )


def _abandoned_run_is_reclaimable(run_root: Path) -> bool:
    """Whether an unoccupied run root provably holds nothing anyone could still want.

    Deleting a sibling run's tree is the exact failure this layout exists to
    prevent, so removal needs more than "its lease is free": no recorded owner may
    still be running, and the run's own clone must hold no commit that has not
    reached origin. Only the *work* side of that is conservative when unreadable —
    a clone this cannot inspect is kept. An unreadable owner record is not, because
    it names nobody to be alive; the caller's failed exclusive probe of the shared
    occupancy lease is what already proved the tree empty.
    """
    owner = RunOwner.read(run_root)
    if owner is not None and owner.is_live():
        return False
    clone = run_root / CLONE_DIR_NAME
    if not clone.is_dir():
        return not any(entry.name != OWNER_RECORD_NAME for entry in run_root.iterdir())
    return gitops.is_repo(clone) and not gitops.unpublished_branches(clone)


def _run_has_unpublished_work(run_root: Path, checkout: Path | None = None) -> bool:
    """Whether a run holds state not published or superseded elsewhere."""
    clone = run_root / CLONE_DIR_NAME
    if not clone.is_dir() or not gitops.is_repo(clone):
        return False
    unpublished = gitops.unpublished_branches(clone)
    if checkout is not None:
        unpublished = [
            branch
            for branch in unpublished
            if not (
                gitops.branch_exists(checkout, branch)
                and gitops.is_ancestor(
                    clone, gitops.ref_sha(clone, branch), gitops.ref_sha(checkout, branch)
                )
            )
        ]
    if unpublished:
        return True
    return any(
        path.resolve() != clone.resolve() and path.exists() and gitops.is_dirty(path)
        for path in gitops.worktrees(clone).values()
    )


def _run_has_dirty_worktree(run_root: Path) -> bool:
    clone = run_root / CLONE_DIR_NAME
    return (
        clone.is_dir()
        and gitops.is_repo(clone)
        and any(
            path.resolve() != clone.resolve() and path.exists() and gitops.is_dirty(path)
            for path in gitops.worktrees(clone).values()
        )
    )


def _run_work_is_superseded(run_root: Path, checkout: Path) -> bool:
    """Whether every unpublished branch has reached a same-or-newer durable ref."""
    clone = run_root / CLONE_DIR_NAME
    if not clone.is_dir() or not gitops.is_repo(clone) or _run_has_dirty_worktree(run_root):
        return False
    branches = gitops.unpublished_branches(clone)
    return bool(branches) and all(
        gitops.branch_exists(checkout, branch)
        and gitops.is_ancestor(
            clone, gitops.ref_sha(clone, branch), gitops.ref_sha(checkout, branch)
        )
        for branch in branches
    )


class Workspace:
    """Cuts task worktrees from a private clone of the selected execution checkout."""

    def __init__(
        self,
        root: str | Path,
        *,
        resolver: RepoResolver | None = None,
        workflow: Workflow | None = None,
        repo_type: RepositoryType | None = None,
        run_token: RunToken | str | None = None,
    ) -> None:
        self.root = Path(root)
        # One token per workspace, not per process: a process may drive several
        # workspaces, and giving each its own clone keeps their git metadata apart
        # for the same reason it keeps separate processes apart. Passing an existing
        # token is how a caller rejoins a run already under way — a re-dispatch that
        # has to see the worktrees and branches the first attempt left behind.
        self.run_token = validate_run_token(
            run_token if run_token is not None else f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )
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
        # Branches this run copied into the shared checkout, and so the only ones
        # it may ever withdraw from there.
        self._mirrored: dict[str, dict[str, str]] = {}
        self._adopted_worktrees: set[Path] = set()

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

    def run_root(self, repo: RepoRef) -> Path:
        """Return the directory holding this run's clone and task worktrees."""
        return self.root / repo.dir_key / RUNS_DIR_NAME / self.run_token

    def _claim_run_root(self, run_root: Path) -> None:
        """Mark this run root occupied for as long as this process lives.

        The lease is *shared*, because occupancy is what the reaper must not
        interrupt and several processes may legitimately be in one run at once — a
        re-dispatch rejoining a run under way is the case that matters. An exclusive
        lease would make that second process either fail outright or, far worse,
        work in a tree the reaper still considers free.
        """
        if run_root in self._run_leases:
            return
        run_root.mkdir(parents=True, exist_ok=True)
        lease = advisory_lock(_run_lease_identity(run_root), shared=True)
        lease.__enter__()
        self._run_leases[run_root] = lease
        atomic_json(run_root / OWNER_RECORD_NAME, RunOwner.current(self.run_token).record())

    def _reap_abandoned_runs(self, repo: RepoRef) -> None:
        """Reclaim run roots whose owner is gone and whose work all reached origin."""
        if repo.dir_key in self._reaped:
            return
        self._reaped.add(repo.dir_key)
        runs = self.root / repo.dir_key / RUNS_DIR_NAME
        mine = self.run_root(repo).resolve()
        candidates = [
            candidate
            for candidate in runs.glob("*")
            if candidate.is_dir() and not candidate.is_symlink() and candidate.resolve() != mine
        ]
        incomplete = sorted(
            (
                candidate
                for candidate in candidates
                if _run_has_dirty_worktree(candidate)
                or (
                    not _abandoned_run_is_reclaimable(candidate)
                    and _run_has_unpublished_work(candidate, self.execution_checkout(repo))
                )
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        retained = set(incomplete[:RETAINED_INCOMPLETE_RUNS])
        superseded = {
            candidate
            for candidate in candidates
            if _run_work_is_superseded(candidate, self.execution_checkout(repo))
        }
        for candidate in sorted(candidates):
            if candidate in retained:
                continue
            # Every reason to keep a candidate — a live lease, a live owner, an
            # unpublished commit, an unreadable tree — has to win, so anything that
            # fails to prove reclaimability leaves the tree exactly as it is.
            with (
                suppress(LockTimeout, OSError, gitops.GitError),
                advisory_lock(_run_lease_identity(candidate), timeout=0),
            ):
                if (
                    _abandoned_run_is_reclaimable(candidate)
                    or candidate in incomplete
                    or candidate in superseded
                ):
                    shutil.rmtree(candidate)

    def _adopt_retained_worktree(self, repo: RepoRef, branch: str) -> Path | None:
        """Claim a dead earlier run's exact worktree, including uncommitted edits."""
        runs = self.root / repo.dir_key / RUNS_DIR_NAME
        mine = self.run_root(repo).resolve()
        candidates = sorted(runs.glob("*"), key=lambda path: path.stat().st_mtime, reverse=True)
        for run_root in candidates:
            if not run_root.is_dir() or run_root.is_symlink() or run_root.resolve() == mine:
                continue
            clone = run_root / CLONE_DIR_NAME
            if not clone.is_dir() or not gitops.is_repo(clone):
                continue
            registered = gitops.worktrees(clone).get(branch)
            if registered is None or not registered.exists():
                continue
            owner = RunOwner.read(run_root)
            if owner is not None and owner.is_live():
                continue
            lease = advisory_lock(_run_lease_identity(run_root), timeout=0)
            try:
                lease.__enter__()
            except (LockTimeout, OSError):
                continue
            try:
                self._acquire_worktree_lease(clone, registered)
            except Exception:
                lease.__exit__(None, None, None)
                continue
            self._run_leases[run_root] = lease
            atomic_json(run_root / OWNER_RECORD_NAME, RunOwner.current(self.run_token).record())
            self._clones[repo.dir_key] = clone
            resolved = registered.resolve()
            self._adopted_worktrees.add(resolved)
            return resolved
        return None

    def adopted_worktree(self, path: str | Path) -> bool:
        """Whether this workspace adopted ``path`` from a provably finished run."""
        return Path(path).resolve() in self._adopted_worktrees

    def adopt_retained_worktree(self, repo: RepoRef, branch: str) -> Path | None:
        """Adopt a dead run's exact tree without creating a fresh fallback."""
        return self._adopt_retained_worktree(repo, branch)

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
        run_root = self.run_root(repo)
        self._claim_run_root(run_root)
        clone = run_root / CLONE_DIR_NAME
        if clone.is_dir() and gitops.is_repo(clone):
            return clone
        started = time.monotonic()
        gitops.clone_sharing(checkout, clone, origin=origin, base=base)
        # The clone's own working tree is never populated, so its hooks have to come
        # from the checkout it was cut from — which is the same absolute path every
        # worktree already resolved hooks through. This takes the checkout's
        # *effective* directory rather than only a tracked `.githooks`, because every
        # publishing push now leaves from this clone: dispatch admits an identity by
        # reading the checkout's effective `pre-push`, so anything that hook would
        # have gated has to be gated here or the guard is admitting on evidence that
        # never runs.
        hooks = gitops.hooks_dir(checkout)
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
        checkout = self.execution_checkout(repo)
        if not gitops.branch_exists(checkout, branch):
            return False
        return gitops.import_branch(clone, checkout, branch)

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

    def _reclaim_worktree_path(self, repo: RepoRef, clone: Path, path: Path) -> None:
        """Clear one of *this run's* worktree paths, however its worker left it.

        Git's own removal is the first choice and usually the only step, but it is
        not something the harness can depend on: it refuses a directory that is no
        longer a registered working tree, and it fails outright on the ``node_modules``
        and build caches a killed worker leaves behind. Every one of those refusals
        used to end a re-dispatch with a path an operator had to clear by hand, so
        what git declines is finished here — the registration pruned, the directory
        removed — and the dispatch goes on.

        Ownership is not assumed, it is established, and no single check establishes
        it. The path must have the shape this run lays its worktrees out in, and this
        process must hold both that run root's occupancy lease and the path's own
        exclusive lease before anything is deleted; a live sibling in the same run
        therefore keeps its tree, and another run's tree is never even a candidate.
        Reclaiming what is not ours is the one failure worse than the one this fixes,
        so any other path is refused rather than cleared.
        """
        target = path.resolve()
        if not self._matches_worktree_layout(repo, target):
            raise WorkspaceError(
                f"refusing to reclaim {path}: it does not have the shape of a worktree "
                f"this run lays out directly under {self.run_root(repo).resolve()}"
            )
        if target in gitops.locked_worktrees(clone):
            # A lock is somebody's explicit instruction that this tree must survive.
            # Git's refusal is the right answer and is left to reach the caller.
            gitops.worktree_remove(clone, path, check=True)
            return
        with suppress(gitops.GitError):
            gitops.worktree_remove(clone, path, check=True)
        if not target.exists():
            return
        _remove_directory_tree(target)
        gitops.worktree_prune(clone)

    def _matches_worktree_layout(self, repo: RepoRef, target: Path) -> bool:
        """Whether ``target`` has the shape this run lays its worktrees out in.

        A shape, and only a shape — deliberately not a claim to have created the path
        or to own it. Neither can be established here: whether git still registers a
        worktree there is the one thing that cannot be required, since the leftover
        directory of a killed worker, registration already pruned, is exactly what
        needs reclaiming. So this answers the narrow question it can, a direct child of
        this run's root named the way `_safe_branch_dir` names one, and the caller
        turns that into ownership by holding the leases that make the path this
        process's alone.

        Even as a shape it earns its keep, because mere containment is weaker than it
        looks: that would also admit every directory *inside* a worktree, which is a
        worker's own content, and recursive deletion is not something to point at a
        path on the strength of where it happens to sit.
        """
        return target.parent == self.run_root(repo).resolve() and target.name == _safe_branch_dir(
            target.name
        )

    def worktree(self, repo: RepoRef, branch: str, *, base: str) -> Path:
        """Add a fresh worktree for ``branch`` cut off ``base`` (e.g. ``origin/main``).

        Any stale worktree at the target path is removed first so a re-dispatch
        starts clean.
        """
        adopted = self._adopt_retained_worktree(repo, branch)
        if adopted is not None:
            return adopted
        clone = self.clone_dir(repo)
        path = self.run_root(repo) / _safe_branch_dir(branch)
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
                        self._reclaim_worktree_path(repo, clone, registered)
                    except Exception:
                        self._release_worktree_lease(registered)
                        raise
                    if registered.resolve() != path.resolve():
                        try:
                            self._acquire_worktree_lease(clone, path)
                        finally:
                            self._release_worktree_lease(registered)
                    active = gitops.worktrees(clone)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.resolve() not in self._worktree_leases:
                self._acquire_worktree_lease(clone, path)
            try:
                if path.exists():
                    self._reclaim_worktree_path(repo, clone, path)
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

    def mirror_branch(self, repo: RepoRef, branch: str) -> bool:
        """Copy this run's branch into the shared execution checkout.

        A run's clone is disposable and invisible to everything outside the run. The
        shared checkout is where the harness has always looked for a lifecycle branch
        — to monitor it, to resume it, to recover it — so anything worth outliving
        the run, or worth seeing from outside it, is handed over here.

        The handover only ever moves that branch forward. Two runs can be told to
        use one branch name, and rewinding the shared copy would destroy whatever
        the other one preserved under it; this run keeps its own clone's record
        instead.
        """
        clone = self._clones.get(repo.dir_key)
        checkout = self._checkouts.get(repo.dir_key)
        if clone is None or checkout is None or not gitops.branch_exists(clone, branch):
            return False
        with advisory_lock(git_lock_identity(gitops.common_dir(checkout))):
            copied = gitops.copy_branch(clone, checkout, branch)
        if copied:
            self._mirrored.setdefault(repo.dir_key, {})[branch] = gitops.ref_sha(clone, branch)
        return copied

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
                # A path this run's layout never laid out belongs to somebody else — a
                # sibling run being torn down by a workspace that never created it.
                # Git's own removal is the only thing entitled to act on it, and its
                # refusal is the answer the caller gets.
                if self._matches_worktree_layout(repo, Path(path).resolve()):
                    self._reclaim_worktree_path(repo, clone, Path(path))
                else:
                    gitops.worktree_remove(clone, path, check=True)
        finally:
            self._release_worktree_lease(path)

    def delete_branch(self, repo: RepoRef, branch: str) -> None:
        """Delete an unneeded lifecycle branch from this run, and any copy it left.

        The shared checkout holds branches from every run of this identity, and two
        runs can be told to use one branch name. So this withdraws only the exact
        commit *this* run left there: a sibling that has since preserved newer work
        under that name has moved the branch on, and the delete then declines rather
        than taking the only record of it.
        """
        clone = self.clone_dir(repo)
        with self._repo_lock(repo), advisory_lock(git_lock_identity(gitops.common_dir(clone))):
            gitops.delete_branch(clone, branch)
        checkout = self._checkouts.get(repo.dir_key)
        mirrored = self._mirrored.get(repo.dir_key, {}).get(branch)
        if checkout is None or mirrored is None:
            return
        with advisory_lock(git_lock_identity(gitops.common_dir(checkout))):
            gitops.delete_branch_at(checkout, branch, mirrored)
        self._mirrored[repo.dir_key].pop(branch, None)
