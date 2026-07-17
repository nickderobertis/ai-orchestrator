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

import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from . import gitops
from .coordination import advisory_lock
from .github import AutoMergeUnavailable, GitHubBackend, PullRequest
from .verify import run_gate

if TYPE_CHECKING:
    # Annotation-only, and load-bearing: the run ledger imports `MergePolicy` from
    # this module, so importing the journal (which imports the ledger) for real
    # would close a cycle. Nothing here needs the journal at runtime — a strategy
    # only ever calls `append` on the sink the lifecycle injects.
    from .journal import Detail, EventKind, NodeSink

__all__ = [
    "GitHubMergeStrategy",
    "LocalMergeStrategy",
    "MergeContext",
    "MergeOutcome",
    "MergePolicy",
    "MergeStrategy",
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
    timeout: float = 3600.0
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    verify_command: list[str] | None = None
    verify_env: dict[str, str] | None = None
    gate_timeout: float | None = None
    publication_attempts: int = 3
    #: Where publication transitions are recorded, already scoped to the node the
    #: lifecycle is merging for. ``None`` outside a tracked round, where there is
    #: no journal to record into.
    #:
    #: `NodeSink` rather than the bare `JournalSink` it appends through: every kind
    #: `_record` writes is a node transition, so an unscoped sink would raise on the
    #: first one — after the branch is already pushed. The scope is the contract, so
    #: it is the type.
    journal: NodeSink | None = None


@dataclass
class MergeOutcome:
    outcome: str
    detail: str
    pr: PullRequest | None = None


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
            return "merged", f"{detail}; merged"
        if status.state == "CLOSED":
            return "closed", f"{detail}; PR was closed without merging"
        if status.blocking_failed:
            failed = ", ".join(c.name for c in status.blocking if c.red)
            return "checks-failed", f"required checks failed: {failed}"
        if policy == "direct" and status.blocking_green:
            github.merge(pr, method=ctx.method)
            if github.status(pr).merged:
                _record(
                    ctx,
                    "pr-merged",
                    {"repo": ctx.repo_slug, "pr": pr.url, "number": pr.number},
                )
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
        identity = f"git:{gitops.common_dir(ctx.clone_dir)}"
        with advisory_lock(identity):
            for attempt in range(1, ctx.publication_attempts + 1):
                gitops.fetch(ctx.clone_dir)
                with tempfile.TemporaryDirectory(prefix="orchestrator-merge-") as parent:
                    scratch = Path(parent) / "worktree"
                    gitops.worktree_add_detached(ctx.clone_dir, scratch, f"origin/{ctx.base}")
                    try:
                        gitops.merge(scratch, f"origin/{ctx.branch}", message=ctx.title)
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
                        try:
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
        _record(ctx, "pr-merged", {"pr": pr.url, "branch": ctx.branch, "base": ctx.base})
        return MergeOutcome(
            outcome="merged",
            detail=f"local direct-merge of {ctx.branch} into {ctx.base} after checks",
            pr=pr,
        )


def _is_push_race(exc: gitops.GitError) -> bool:
    """Classify the rejection emitted by git for a stale non-force ref update."""
    message = str(exc).lower()
    return "[rejected]" in message and ("non-fast-forward" in message or "fetch first" in message)
