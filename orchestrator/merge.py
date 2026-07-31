"""How a branch gets through its merge-path gate into base — two strategies, one seam.

The lifecycle does the same work up to a branch push; only
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
from .coordination import GitLockIdentity, git_lock_identity
from .github import AutoMergeUnavailable, Check, GitHubBackend, PRStatus, PullRequest
from .merge_queue import merge_queue_turn
from .outcomes import ALREADY_INTEGRATED_OUTCOME, LifecycleOutcome
from .provenance import attestation_trailers
from .redaction import redact
from .verify import (
    VerifyResult,
    comparison_env,
    record_merge_path_failure,
    record_merge_path_verification,
)
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
    "adopt_or_create_pr",
    "assess_blocking_checks",
    "classify_push_failure",
]

MergePolicy = Literal["auto", "direct", "none"]
MERGE_CONFLICT_RETRY: Literal["merge-conflict-retry"] = "merge-conflict-retry"


@dataclass(frozen=True)
class PullRequestResolution:
    pr: PullRequest
    created: bool


def adopt_or_create_pr(
    github: GitHubBackend,
    repo: str,
    *,
    head: str,
    base: str,
    head_sha: str,
    title: str,
    body: str,
    draft: bool = False,
) -> PullRequestResolution:
    """Adopt a matching publication when possible, otherwise create one."""
    lookup = getattr(github, "adoptable_pr", None)
    existing = lookup(repo, head=head, base=base, head_sha=head_sha) if lookup else None
    if existing is not None:
        return PullRequestResolution(existing, created=False)
    if draft:
        created = github.create_pr(repo, head=head, base=base, title=title, body=body, draft=True)
    else:
        created = github.create_pr(repo, head=head, base=base, title=title, body=body)
    return PullRequestResolution(created, created=True)


def classify_push_failure(exc: gitops.GitError) -> LifecycleOutcome:
    """Classify a push rejection using the diagnostics emitted by Git hooks."""
    detail = str(exc).casefold()
    gate_markers = ("pre-push", "gate:", "gate failed", "gate rejected")
    return "gate-failed" if any(marker in detail for marker in gate_markers) else "error"


@dataclass
class MergeContext:
    """Everything a strategy needs once the branch is pushed through its merge path."""

    repo_slug: str
    clone_dir: Path
    base: str
    branch: str
    title: str
    body: str
    head_sha: str = ""
    #: What the merge queue serializes on. It has to name the *repository*, not the
    #: clone the merge is built in: every run now builds in a clone of its own, and
    #: a per-run identity would hand each contender its own empty queue. ``None``
    #: falls back to the clone, which is correct only where one is shared.
    queue_identity: GitLockIdentity | None = None
    method: str = "squash"  # GitHub merge method; ignored by the local strategy
    policy: MergePolicy = "auto"  # GitHub only
    poll_interval: float = 15.0
    max_poll_interval: float = 120.0
    timeout: float = 3600.0
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
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
    #: The repository's complete gate, recorded alongside the publication push it
    #: verifies. Empty where required PR checks stand in for a pre-push hook.
    gate_command: tuple[str, ...] = ()
    preverified_pr: PullRequest | None = None
    local_prepare: Callable[[], MergeOutcome | None] | None = None
    #: The workstream environment every publishing push carries. The merge path is
    #: the verifier now, so the `pre-push` hook running the repository's gate has to
    #: see the same comparison base and build cache the worker's own gate saw —
    #: otherwise it re-judges a memoized verdict against a base the worker never had.
    #: `None` where a caller has no workstream, which resolves the base as Git would.
    push_env: dict[str, str] | None = None


def _publication_message(ctx: MergeContext) -> str:
    """The squash commit's message: the change's subject, plus what it recovered.

    Squashing collapses the branch, so the `chore: attest verified recovery of
    preserved work` commit — the record that a step was left incomplete and a green
    gate recovered it — would never reach the base branch. Its trailers ride on the
    publication commit instead, which is what keeps the base branch squash-merged
    and the attestation preserved at the same time.
    """
    trailers = attestation_trailers(ctx.clone_dir, f"origin/{ctx.base}", f"origin/{ctx.branch}")
    if not trailers:
        return ctx.title
    return ctx.title + "\n\n" + "\n".join(trailers)


@dataclass
class MergeOutcome:
    outcome: LifecycleOutcome
    detail: str
    pr: PullRequest | None = None
    #: Evidence from the merge path's own gate run, when this outcome came from a
    #: gated push. It supersedes any earlier record on the same lifecycle result:
    #: a branch push that passed says nothing about the publication push that did
    #: not.
    verification: VerifyResult | None = None


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


def _queue_identity(ctx: MergeContext) -> GitLockIdentity:
    """Name the repository whose base branch this merge queue protects."""
    if ctx.queue_identity is not None:
        return ctx.queue_identity
    return git_lock_identity(gitops.common_dir(ctx.clone_dir))


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
) -> tuple[LifecycleOutcome, str]:
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
            with merge_queue_turn(_queue_identity(ctx)):
                return self._publish_and_merge(ctx)
        return self._publish_and_merge(ctx)

    def _publish_and_merge(self, ctx: MergeContext) -> MergeOutcome:
        created = False
        if ctx.preverified_pr is not None:
            pr = ctx.preverified_pr
        else:
            resolution = adopt_or_create_pr(
                self._github,
                ctx.repo_slug,
                head=ctx.branch,
                base=ctx.base,
                head_sha=ctx.head_sha,
                title=ctx.title,
                body=ctx.body,
            )
            pr = resolution.pr
            created = resolution.created
        if created:
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
    """Merge the branch straight into base through a gated push (the local path).

    The branch is already pushed to the local origin by the lifecycle. The merge is
    built in a detached scratch worktree and pushed to the base ref, leaving the
    canonical checkout untouched. The lifecycle subsequently fast-forwards that
    checkout after either local or remote merging succeeds.
    """

    def publish_and_merge(self, ctx: MergeContext) -> MergeOutcome:
        if ctx.publication_attempts < 1:
            raise ValueError("publication_attempts must be at least 1")
        with merge_queue_turn(_queue_identity(ctx)):
            return self._publish_and_merge(ctx)

    def _publish_and_merge(self, ctx: MergeContext) -> MergeOutcome:
        if ctx.local_prepare is not None:
            prepared = ctx.local_prepare()
            if prepared is not None:
                return prepared
        verified: VerifyResult | None = None
        # What each lost attempt actually saw. "Lost a race on all N attempts" is
        # an assertion about a cause; these are the observations behind it, and
        # without them a base being advanced by something else, a genuine
        # concurrent publisher, and a synthetic rejection all read the same.
        races: list[str] = []
        for attempt in range(1, ctx.publication_attempts + 1):
            with tempfile.TemporaryDirectory(prefix="orchestrator-merge-") as parent:
                scratch = Path(parent) / "worktree"
                gitops.fetch(ctx.clone_dir)
                base_sha = gitops.ref_sha(ctx.clone_dir, f"origin/{ctx.base}")
                gitops.worktree_add_detached(ctx.clone_dir, scratch, f"origin/{ctx.base}")
                try:
                    gitops.merge_squash(
                        scratch, f"origin/{ctx.branch}", message=_publication_message(ctx)
                    )
                except gitops.NothingToCommit:
                    gitops.worktree_remove(ctx.clone_dir, scratch)
                    pr = _local_publication_ref(ctx)
                    _record(
                        ctx,
                        "publication-finished",
                        {
                            "pr": pr.url,
                            "branch": ctx.branch,
                            "base": ctx.base,
                            "outcome": ALREADY_INTEGRATED_OUTCOME,
                        },
                    )
                    return MergeOutcome(
                        outcome=ALREADY_INTEGRATED_OUTCOME,
                        detail=(
                            f"verified content from {ctx.branch} was already present "
                            f"on {ctx.base}; no publication commit was needed"
                        ),
                        pr=pr,
                    )
                except Exception:
                    gitops.worktree_remove(ctx.clone_dir, scratch)
                    raise
                try:
                    # Dispatch refuses identities without merge-path gate coverage.
                    # This detached tree is pushed directly below, so the repository's
                    # executable pre-push hook verifies this identical tree.
                    gitops.fetch(ctx.clone_dir)
                    label = f"publication push {ctx.branch} -> {ctx.base}"
                    try:
                        observed = gitops.ref_sha(ctx.clone_dir, f"origin/{ctx.base}")
                        if observed != base_sha:
                            raise gitops.GitError(
                                f"! [rejected] verified merge -> {ctx.base} (fetch first); "
                                f"verified against {base_sha}, {ctx.base} is now {observed}"
                            )
                        pushed = gitops.push(
                            scratch,
                            f"HEAD:{ctx.base}",
                            set_upstream=False,
                            # The hook that verifies this tree must judge it against
                            # the base it is being published onto, which for a stacked
                            # workstream is not the remote HEAD it would discover.
                            env=ctx.push_env or comparison_env(ctx.base),
                        )
                    except gitops.GitError as exc:
                        if not _is_push_race(exc):
                            detail = redact(str(exc))
                            outcome = classify_push_failure(exc)
                            # A lost race is retried below and is not a gate verdict,
                            # so only a real rejection records one.
                            verification = _record_verification(
                                ctx, label=label, ok=False, output=exc.output
                            )
                            return MergeOutcome(
                                outcome=outcome,
                                detail=(
                                    "repository pre-push gate rejected the rebuilt local "
                                    f"publication: {detail}"
                                    if outcome == "gate-failed"
                                    else "rebuilt local publication push failed: " + detail
                                )
                                + _evidence(verification),
                                verification=verification,
                            )
                        races.append(f"attempt {attempt}: {redact(str(exc))}")
                        if attempt == ctx.publication_attempts:
                            log_path = _record_failure(
                                ctx,
                                label=label,
                                outcome="publication-retries-exhausted",
                                output="\n".join(races) + "\n",
                            )
                            return MergeOutcome(
                                outcome="publication-retries-exhausted",
                                detail=(
                                    "local base publication lost a concurrent push race "
                                    f"on all {ctx.publication_attempts} attempts; "
                                    f"last: {races[-1]}"
                                )
                                + _evidence_at(log_path),
                            )
                        continue
                    verified = _record_verification(ctx, label=label, ok=True, output=pushed)
                    break
                finally:
                    gitops.worktree_remove(ctx.clone_dir, scratch)
        pr = _local_publication_ref(ctx)
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
            verification=verified,
        )


def _record_verification(
    ctx: MergeContext, *, label: str, ok: bool, output: str
) -> VerifyResult | None:
    """Preserve one gated publication push, when this merge runs in a tracked round.

    An empty ``gate_command`` means no repository gate runs at this push, so there
    is no verdict here to bracket or record.
    """
    if ctx.journal is None or not ctx.gate_command:
        return None
    ctx.journal.append("verification-started", detail={"label": label})
    return record_merge_path_verification(
        ctx.journal,
        label=label,
        command=list(ctx.gate_command),
        ok=ok,
        output=output,
    )


def _record_failure(ctx: MergeContext, *, label: str, outcome: str, output: str) -> str | None:
    """Preserve a publication that ended before any gate ruled on it."""
    if ctx.journal is None:
        return None
    return record_merge_path_failure(ctx.journal, label=label, outcome=outcome, output=output)


def _evidence(verification: VerifyResult | None) -> str:
    """Name the preserved gate log this outcome points at, if one was written."""
    return _evidence_at(verification.log_path if verification is not None else None)


def _evidence_at(path: str | None) -> str:
    return f" — full merge-path log: {path}" if path else ""


def _local_publication_ref(ctx: MergeContext) -> PullRequest:
    """Build the stable synthetic publication reference used by local workflows."""
    return PullRequest(
        number=0,
        url=f"local:{ctx.repo_slug}#{ctx.branch}",
        repo=ctx.repo_slug,
        head=ctx.branch,
        base=ctx.base,
    )


def _is_push_race(exc: gitops.GitError) -> bool:
    """Classify the rejection emitted by git for a stale non-force ref update."""
    message = str(exc).lower()
    return "[rejected]" in message and ("non-fast-forward" in message or "fetch first" in message)
