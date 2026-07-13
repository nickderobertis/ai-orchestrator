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

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import gitops
from .github import AutoMergeUnavailable, GitHubBackend, PullRequest

__all__ = [
    "GitHubMergeStrategy",
    "LocalMergeStrategy",
    "MergeContext",
    "MergeOutcome",
    "MergeStrategy",
]


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
    policy: str = "auto"  # auto | direct | none (GitHub only)
    poll_interval: float = 15.0
    timeout: float = 3600.0
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic


@dataclass
class MergeOutcome:
    outcome: str
    detail: str
    pr: PullRequest | None = None


class MergeStrategy(Protocol):
    def publish_and_merge(self, ctx: MergeContext) -> MergeOutcome: ...


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
    while True:
        status = github.status(pr)
        if status.merged:
            return "merged", f"{detail}; merged"
        if status.state == "CLOSED":
            return "closed", f"{detail}; PR was closed without merging"
        if status.blocking_failed:
            failed = ", ".join(c.name for c in status.blocking if c.red)
            return "checks-failed", f"required checks failed: {failed}"
        if policy == "direct" and status.blocking_green:
            github.merge(pr, method=ctx.method)
            if github.status(pr).merged:
                return "merged", f"{detail}; merged"
        if ctx.clock() - start >= ctx.timeout:
            return "timeout", f"{detail}; timed out after {ctx.timeout}s awaiting checks"
        ctx.sleep(ctx.poll_interval)


class GitHubMergeStrategy:
    """Open a PR and merge it once required checks are green (the remote path)."""

    def __init__(self, github: GitHubBackend) -> None:
        self._github = github

    def publish_and_merge(self, ctx: MergeContext) -> MergeOutcome:
        pr = self._github.create_pr(
            ctx.repo_slug, head=ctx.branch, base=ctx.base, title=ctx.title, body=ctx.body
        )
        outcome, detail = _drive_github_merge(self._github, pr, ctx)
        return MergeOutcome(outcome=outcome, detail=detail, pr=pr)


class LocalMergeStrategy:
    """Merge the verified branch straight into base with real git (the local path).

    The branch is already pushed to the local origin by the lifecycle. Here we sync
    the clone's base to ``origin/base``, merge the branch in, and push base back —
    so the local origin's default branch advances. A bare origin accepts this
    directly; a non-bare origin needs ``receive.denyCurrentBranch=updateInstead``.
    """

    def publish_and_merge(self, ctx: MergeContext) -> MergeOutcome:
        gitops.checkout(ctx.clone_dir, ctx.base)
        gitops.reset_hard(ctx.clone_dir, f"origin/{ctx.base}")
        gitops.merge(ctx.clone_dir, ctx.branch, message=ctx.title)
        gitops.push(ctx.clone_dir, ctx.base, set_upstream=False)
        pr = PullRequest(
            number=0,
            url=f"local:{ctx.repo_slug}#{ctx.branch}",
            repo=ctx.repo_slug,
            head=ctx.branch,
            base=ctx.base,
        )
        return MergeOutcome(
            outcome="merged",
            detail=f"local direct-merge of {ctx.branch} into {ctx.base} after checks",
            pr=pr,
        )
