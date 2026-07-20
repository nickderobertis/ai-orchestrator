"""How a verified branch gets into the base branch — two strategies, one seam.

The lifecycle does the same work up to a pushed, locally-verified branch; only
the last step differs by where the repo lives:

- `GitHubMergeStrategy` opens a PR and lets GitHub merge it once the repo's
  **required (blocking) checks** are green (native auto-merge by default) — never
  off a non-required check.
- `LocalMergeStrategy` has no PR/CI to wait on: it merges the branch straight
  into the base branch with real git and pushes it to the local origin. This is
  the model for a local repo — "direct merge into main after the checks pass."

Both are `MergeStrategy`, so `run_repo_task` calls one method and stays uniform.
"""

# llmlint: ignore-file[changed_behavior_has_e2e] Real-git e2e journeys cover
# concurrent local lifecycles and every local/remote/team publication policy;
# process-level queue tests cover FIFO and crash recovery at their shared seam.

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from . import gitops
from .coordination import git_lock_identity
from .github import AutoMergeUnavailable, Check, GitHubBackend, PRStatus, PullRequest
from .merge_queue import merge_queue_turn
from .verify import run_gate
from .workspace import RepositoryType

if TYPE_CHECKING:
    # Annotation-only, and load-bearing: the run ledger imports `MergePolicy` from
    # this module, so importing the journal (which imports the ledger) for real
    # would close a cycle. Nothing here needs the journal at runtime — a strategy
    # only ever calls `append` on the sink the lifecycle injects.
    from .journal import Detail, EventKind, NodeSink

__all__ = [
    "BlockingCheckAssessment",
    "GitHubMergeStrategy",
    "LocalMergeStrategy",
    "MergeContext",
    "MergeOutcome",
    "MergePolicy",
    "MergeStrategy",
    "assess_blocking_checks",
]

MergePolicy = Literal["auto", "direct", "none"]


@dataclass
class MergeContext:
    """Everything a strategy needs once the branch is pushed and verified."""

    repo_slug: str
    clone_dir: Path
    base: str
    branch: str
    title: str
    body: str
    method: str = "squash"  # GitHub merge method; ignored by the local strategy
    policy: MergePolicy = "auto"  # GitHub only
    poll_interval: float = 15.0
    max_poll_interval: float = 120.0
    timeout: float = 3600.0
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    verify_command: list[str] | None = None
    verify_env: dict[str, str] | None = None
    gate_timeout: float | None = None
    publication_attempts: int = 3
    repository_type: RepositoryType = "single-owner"
    #: Where publication transitions are recorded, already scoped to the node the
    #: lifecycle is merging for. ``None`` outside a tracked round, where there is
    #: no journal to record into.
    #:
    #: `NodeSink` rather than the bare `JournalSink` it appends through: every kind
    #: `_record` writes is a node transition, so an unscoped sink would raise on the
    #: first one — after the branch is already pushed. The scope is the contract, so
    #: it is the type.
    journal: NodeSink | None = None
    preverified_pr: PullRequest | None = None


@dataclass
class MergeOutcome:
    outcome: str
    detail: str
    pr: PullRequest | None = None


@dataclass(frozen=True)
class BlockingCheckAssessment:
    """The shared required-check decision used before and during publication."""

    green: bool
    settled: bool
    checks: tuple[Check, ...]

    @property
    def detail(self) -> str:
        rendered = ", ".join(f"{check.name}={check.state}" for check in self.checks)
        return f"required checks: [{rendered}]" if rendered else "required checks: []"


def assess_blocking_checks(status: PRStatus) -> BlockingCheckAssessment:
    """Assess required checks, preserving the non-vacuous empty-list guard."""
    blocking = status.blocking
    return BlockingCheckAssessment(
        green=bool(blocking) and all(check.green for check in blocking),
        settled=bool(blocking) and all(check.settled for check in blocking),
        checks=blocking,
    )


class MergeStrategy(Protocol):
    def publish_and_merge(self, ctx: MergeContext) -> MergeOutcome: ...


def _record(ctx: MergeContext, kind: EventKind, detail: Detail) -> None:
    """Journal one publication transition, if this merge runs inside a tracked round.

    ``detail`` is an explicit mapping rather than ``**kwargs`` for the same reason
    `Journal.append` takes one: a payload key must never be able to collide with a
    parameter of the function carrying it.
    """
    if ctx.journal is not None:
        ctx.journal.append(kind, detail=detail)


def _drive_github_merge(
    github: GitHubBackend, pr: PullRequest, ctx: MergeContext
) -> tuple[str, str]:
    """Merge a PR per ``ctx.policy``, gating only on required checks."""
    if ctx.policy == "none":
        return "pr-open", f"PR #{pr.number} opened; auto-merge disabled by policy"

    policy = ctx.policy
    detail = "merging directly on green required checks"
    if policy == "auto":
        try:
            github.enable_auto_merge(pr, method=ctx.method)
            detail = "native auto-merge enabled (gates on required checks)"
        except AutoMergeUnavailable:
            policy = "direct"
            detail = "native auto-merge unavailable; merging directly on green required checks"

    start = ctx.clock()
    delay = ctx.poll_interval
    direct_merge_requested = False
    while True:
        status = github.status(pr)
        _record(
            ctx,
            "pr-checks-observed",
            {
                "repo": ctx.repo_slug,
                "pr": pr.url,
                "state": status.state,
                "merged": status.merged,
                "merge_state_status": status.merge_state_status,
                "blocking": [{"name": c.name, "state": c.state} for c in status.blocking],
            },
        )
        if status.merged:
            _record(
                ctx,
                "pr-merged",
                {"repo": ctx.repo_slug, "pr": pr.url, "number": pr.number},
            )
            _record(
                ctx,
                "publication-finished",
                {"repo": ctx.repo_slug, "pr": pr.url, "branch": ctx.branch, "base": ctx.base},
            )
            return "merged", f"{detail}; merged"
        if status.state == "CLOSED":
            return "closed", f"{detail}; PR was closed without merging"
        if status.blocking_failed:
            failed = ", ".join(c.name for c in status.blocking if c.red)
            return "checks-failed", f"required checks failed: {failed}"
        assessment = assess_blocking_checks(status)
        blocking_settled = assessment.settled
        if policy == "direct" and assessment.green and not direct_merge_requested:
            github.merge(pr, method=ctx.method)
            direct_merge_requested = True
            post_merge = github.status(pr)
            if post_merge.merged:
                _record(
                    ctx,
                    "pr-merged",
                    {"repo": ctx.repo_slug, "pr": pr.url, "number": pr.number},
                )
                _record(
                    ctx,
                    "publication-finished",
                    {"repo": ctx.repo_slug, "pr": pr.url, "branch": ctx.branch, "base": ctx.base},
                )
                return "merged", f"{detail}; merged"
            post_merge_blocking_settled = bool(post_merge.blocking) and all(
                check.settled for check in post_merge.blocking
            )
            if post_merge_blocking_settled and not post_merge.merge_in_progress:
                states = ", ".join(f"{check.name}={check.state}" for check in post_merge.blocking)
                return (
                    "error",
                    f"{detail}; required checks settled but PR remains unmerged: [{states}]",
                )
            if ctx.clock() - start >= ctx.timeout:
                return "timeout", f"{detail}; timed out after {ctx.timeout}s awaiting checks"
            ctx.sleep(delay)
            delay = min(ctx.max_poll_interval, delay * 2)
            continue
        if (
            blocking_settled
            and not status.merge_in_progress
            and (policy == "auto" or direct_merge_requested)
        ):
            states = ", ".join(f"{check.name}={check.state}" for check in status.blocking)
            return (
                "error",
                f"{detail}; required checks settled but PR remains unmerged: [{states}]",
            )
        if ctx.clock() - start >= ctx.timeout:
            return "timeout", f"{detail}; timed out after {ctx.timeout}s awaiting checks"
        ctx.sleep(delay)
        delay = min(ctx.max_poll_interval, delay * 2)


class GitHubMergeStrategy:
    """Open a PR and merge it once required checks are green (the remote path)."""

    def __init__(self, github: GitHubBackend) -> None:
        self._github = github

    def publish_and_merge(self, ctx: MergeContext) -> MergeOutcome:
        if ctx.repository_type == "single-owner" and ctx.policy != "none":
            identity = git_lock_identity(gitops.common_dir(ctx.clone_dir))
            with merge_queue_turn(identity):
                return self._publish_and_merge(ctx)
        return self._publish_and_merge(ctx)

    def _publish_and_merge(self, ctx: MergeContext) -> MergeOutcome:
        pr = ctx.preverified_pr or self._github.create_pr(
            ctx.repo_slug, head=ctx.branch, base=ctx.base, title=ctx.title, body=ctx.body
        )
        if ctx.preverified_pr is None:
            _record(
                ctx,
                "pr-created",
                {
                    "repo": ctx.repo_slug,
                    "pr": pr.url,
                    "number": pr.number,
                    "base": ctx.base,
                    "draft": False,
                },
            )
        if self._github.status(pr).draft:
            self._github.mark_ready(pr)
            _record(
                ctx,
                "pr-ready",
                {"repo": ctx.repo_slug, "pr": pr.url, "number": pr.number},
            )
        outcome, detail = _drive_github_merge(self._github, pr, ctx)
        return MergeOutcome(outcome=outcome, detail=detail, pr=pr)


class LocalMergeStrategy:
    """Merge the verified branch straight into base with real git (the local path).

    The branch is already pushed to the local origin by the lifecycle. The merge is
    built in a detached scratch worktree and pushed to the base ref, leaving the
    canonical checkout untouched. The lifecycle subsequently fast-forwards that
    checkout after either local or remote merging succeeds.
    """

    def publish_and_merge(self, ctx: MergeContext) -> MergeOutcome:
        if ctx.publication_attempts < 1:
            raise ValueError("publication_attempts must be at least 1")
        identity = git_lock_identity(gitops.common_dir(ctx.clone_dir))
        with merge_queue_turn(identity):
            return self._publish_and_merge(ctx)

    def _publish_and_merge(self, ctx: MergeContext) -> MergeOutcome:
        for attempt in range(1, ctx.publication_attempts + 1):
            with tempfile.TemporaryDirectory(prefix="orchestrator-merge-") as parent:
                scratch = Path(parent) / "worktree"
                gitops.fetch(ctx.clone_dir)
                base_sha = gitops.ref_sha(ctx.clone_dir, f"origin/{ctx.base}")
                gitops.worktree_add_detached(ctx.clone_dir, scratch, f"origin/{ctx.base}")
                try:
                    gitops.merge_squash(scratch, f"origin/{ctx.branch}", message=ctx.title)
                except Exception:
                    gitops.worktree_remove(ctx.clone_dir, scratch)
                    raise
                try:
                    if ctx.verify_command is not None:
                        _record(
                            ctx,
                            "verification-started",
                            {"command": list(ctx.verify_command), "attempt": attempt},
                        )
                        verified = run_gate(
                            scratch,
                            ctx.verify_command,
                            timeout=ctx.gate_timeout,
                            env=ctx.verify_env,
                        )
                        _record(
                            ctx,
                            "verification-finished",
                            {
                                "ok": verified.ok,
                                "command": list(verified.command),
                                "attempt": attempt,
                            },
                        )
                        if not verified.ok:
                            return MergeOutcome(
                                outcome="gate-failed",
                                detail="rebuilt local publication failed verification",
                            )
                    gitops.fetch(ctx.clone_dir)
                    try:
                        if gitops.ref_sha(ctx.clone_dir, f"origin/{ctx.base}") != base_sha:
                            raise gitops.GitError(
                                f"! [rejected] verified merge -> {ctx.base} (fetch first)"
                            )
                        gitops.push(scratch, f"HEAD:{ctx.base}", set_upstream=False)
                    except gitops.GitError as exc:
                        if not _is_push_race(exc):
                            raise
                        if attempt == ctx.publication_attempts:
                            return MergeOutcome(
                                outcome="publication-retries-exhausted",
                                detail=(
                                    "local base publication lost a concurrent push race "
                                    f"on all {ctx.publication_attempts} attempts"
                                ),
                            )
                        continue
                    break
                finally:
                    gitops.worktree_remove(ctx.clone_dir, scratch)
        pr = PullRequest(
            number=0,
            url=f"local:{ctx.repo_slug}#{ctx.branch}",
            repo=ctx.repo_slug,
            head=ctx.branch,
            base=ctx.base,
        )
        # No `pr-created` counterpart: this path never opened one. The identity
        # above is synthesized so the result has a stable ref to name, and claiming
        # a PR was created for it would put a transition in the journal that never
        # happened.
        _record(
            ctx,
            "publication-finished",
            {"pr": pr.url, "branch": ctx.branch, "base": ctx.base},
        )
        return MergeOutcome(
            outcome="merged",
            detail=f"local direct-merge of {ctx.branch} into {ctx.base} after checks",
            pr=pr,
        )


def _is_push_race(exc: gitops.GitError) -> bool:
    """Classify the rejection emitted by git for a stale non-force ref update."""
    message = str(exc).lower()
    return "[rejected]" in message and ("non-fast-forward" in message or "fetch first" in message)
