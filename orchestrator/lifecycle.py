"""Drive one subtask through a target repo's full lifecycle — and many at once.

`run_repo_task` is the unit of real work against an external repo:

    clone/worktree → dispatch (bypass mode) → local gate → commit → push → PR
    → merge once the repo's blocking (required) checks are green → clean up

`run_repo_plan` layers the same DAG scheduler `run_plan` uses over that unit, so
one larger task becomes **multiple isolated PRs** — independent PRs open in
parallel; a dependent PR waits for the one it needs to merge, then branches off
the updated base. Each node gets its own worktree, so parallel agents never
collide.

Two seams are injected so the offline gate drives everything else for real:
`dispatch_fn` (the paid harness — defaults to the real `dispatch`) and the
`GitHubBackend` (GitHub's PR/CI decisioning). Git itself is always real.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

from . import BASE_CONFIG, PERSONA_DIR, gitops
from .config import ConfigError, load_yaml
from .coordination import advisory_lock
from .dispatch import Report, dispatch
from .github import CliGitHubBackend, GitHubBackend, GitHubError, PullRequest
from .gitops import GitError
from .merge import (
    GitHubMergeStrategy,
    LocalMergeStrategy,
    MergeContext,
    MergePolicy,
    MergeStrategy,
)
from .plan import NODE_KINDS, NodeRun, schedule_dag
from .provenance import INCOMPLETE_TRAILER, PR_BASE_TRAILER
from .registry import RegistryError, validate_identity_key
from .runs import (
    RepoPlanPayload,
    RepoPlanResultItem,
    prepare_round,
    resolve_run_dir,
    status_counts,
    status_summary,
    write_result,
)
from .verify import VerifyResult, detect_gate, run_gate
from .workspace import (
    IdentityKey,
    RepoRef,
    RepositoryType,
    Workflow,
    Workspace,
    WorkspaceError,
    normalize_repo,
)

# A merge only completes when the PR's blocking (required) checks are green. The
# default `auto` policy uses GitHub native auto-merge (which by construction
# gates on required checks and ignores optional ones); `direct` polls and merges
# ourselves on green required checks; `none` opens the PR and stops.
MERGE_POLICIES = ("auto", "direct", "none")
MERGE_METHODS = ("squash", "merge", "rebase")

# Outcomes that count as the subtask succeeding.
_SUCCESS_OUTCOMES = frozenset({"merged", "pr-open"})

# Workstream paused on a human step. This is neither success nor failure; the next
# recorded round resumes after a human attestation.
_WAITING_OUTCOME = "waiting-human"

DispatchFn = Callable[..., Report]


@dataclass
class Step:
    """One step within a PR workstream (shares the workstream branch)."""

    id: str
    persona: str | None = None
    task: str = ""
    kind: str = "agent"
    deps: list[str] = field(default_factory=list)
    max_turns: int | None = None
    done_when: str | None = None

    @property
    def human(self) -> bool:
        return self.kind == "human"


@dataclass
class StepResult:
    id: str
    persona: str | None
    status: str  # done | not-completed | skipped | waiting | blocked
    kind: str = "agent"
    report: Report | None = None


@dataclass(frozen=True)
class Resume:
    """Validated continuation point for a human-gated lifecycle workstream."""

    branch: str
    base_branch: str
    pr_base: str
    checkpoint: str
    completed_steps: tuple[str, ...] = ()
    pr: str | None = None


@dataclass(frozen=True)
class StackBase:
    """A dependency branch whose content has not reached the root base."""

    branch: str
    repo: str | None = None
    identity: IdentityKey | None = None
    base_branch: str | None = None
    pr: str | None = None
    pr_base: str | None = None


@dataclass(frozen=True)
class PublicationDecision:
    workflow: Workflow
    merge_policy: MergePolicy


@dataclass(frozen=True)
class SyntheticStackBase:
    branch: str


@dataclass(frozen=True)
class StackConflict:
    detail: str


StackBuildResult = SyntheticStackBase | StackConflict


@dataclass
class LifecycleResult:
    """The outcome of driving one subtask (or a step workstream) through a repo."""

    repo: str
    task: str
    persona: str
    base_branch: str
    branch: str
    outcome: str  # merged | pr-open | not-completed | gate-failed | no-changes
    #             | checks-failed | closed | timeout | error
    execution_checkout: str = ""
    publication_checkout: str = ""
    publication_identity: IdentityKey | None = None
    publication_workflow: Workflow | None = None
    repository_type: RepositoryType | None = None
    merge_policy: MergePolicy | None = None
    pr_base: str = ""
    synthetic_stack_base: str | None = None
    stack_bases: list[StackBase] = field(default_factory=list)
    pr: PullRequest | None = None
    report: Report | None = None
    verify: VerifyResult | None = None
    detail: str = ""
    steps: list[StepResult] = field(default_factory=list)
    waiting_steps: list[str] = field(default_factory=list)
    resume: Resume | None = None

    @property
    def ok(self) -> bool:
        return self.outcome in _SUCCESS_OUTCOMES

    @property
    def waiting(self) -> bool:
        return self.outcome == _WAITING_OUTCOME

    @property
    def repo_type(self) -> RepositoryType | None:
        return self.repository_type

    def summary(self) -> str:
        head = f"{self.repo} [{self.persona}] {self.branch}: {self.outcome}"
        if self.pr is not None:
            head += f" (PR #{self.pr.number})"
        if len(self.steps) > 1:
            head += f" [{sum(s.status == 'done' for s in self.steps)}/{len(self.steps)} steps]"
        if self.execution_checkout:
            head += (
                f"\n  - execution checkout: {self.execution_checkout}"
                f"\n  - publication identity: {self.publication_identity}"
                f"\n  - publication checkout: {self.publication_checkout}"
                f"\n  - repository type: {self.repository_type}"
                f"\n  - publication workflow: {self.publication_workflow}"
                f"\n  - merge policy: {self.merge_policy}"
                f"\n  - PR base: {self.pr_base}"
                f"\n  - synthetic stack base: {self.synthetic_stack_base or '-'}"
            )
        if self.detail:
            head += f"\n  - {self.detail}"
        if self.verify is not None and not self.verify.ok:
            head += f"\n  - gate: {' '.join(self.verify.command)} failed"
        if self.report is not None and self.report.assessment:
            head += f"\n  - follow-ups: {self.report.assessment}"
        return head


def _short_hash(*parts: str) -> str:
    return hashlib.sha1("\x00".join(parts).encode("utf-8")).hexdigest()[:8]


def _default_branch_name(persona: str, task: str) -> str:
    return f"ai-orchestrator/{persona}/{_short_hash(persona, task)}"


def _default_title(persona: str, task: str) -> str:
    line = task.strip().splitlines()[0] if task.strip() else "orchestrated change"
    return line[:68] + ("…" if len(line) > 68 else "")


def _default_body(persona: str, task: str, report: Report | None) -> str:
    turns = report.assistant_turns if report else 0
    return (
        "## What\n"
        f"{task.strip()}\n\n"
        "## Why\n"
        f"Dispatched by ai-orchestrator (persona: `{persona}`, {turns} agent turn(s)), "
        "verified locally and driven to green checks before merge.\n"
    )


def _step_label(step: Step) -> str:
    """Use the persona when an agent runs, otherwise the step kind."""
    return step.persona or step.kind


def _step_commit_message(step: Step) -> str:
    return (
        f"{_default_title(_step_label(step), step.task)}\n\n"
        f"Workstream step {step.id} (persona: {step.persona}), dispatched by ai-orchestrator."
    )


def _incomplete_commit_message(step: Step, pr_base: str) -> str:
    return (
        f"wip: {_default_title(_step_label(step), step.task)} (incomplete step)\n\n"
        f"Partial work from step {step.id} (persona: {step.persona}), preserved by "
        "ai-orchestrator after the dispatch did not complete.\n\n"
        f"{INCOMPLETE_TRAILER}\n"
        f"{PR_BASE_TRAILER} {pr_base}"
    )


def _workstream_branch_name(steps: list[Step]) -> str:
    lead = steps[0]
    key = "\x00".join(f"{_step_label(s)}:{s.task}" for s in steps)
    return f"ai-orchestrator/{_step_label(lead)}/{_short_hash(key)}-{uuid.uuid4().hex[:10]}"


def _workstream_body(steps: list[Step], results: list[StepResult]) -> str:
    if len(steps) == 1:
        report = results[0].report if results else None
        return _default_body(_step_label(steps[0]), steps[0].task, report)
    lines = ["## What", "This PR bundles an ordered workstream of subtasks:", ""]
    for s in steps:
        first = s.task.strip().splitlines()[0] if s.task.strip() else s.id
        lines.append(f"- **{s.id}** (`{_step_label(s)}`): {first}")
    lines += [
        "",
        "## Why",
        "Dispatched by ai-orchestrator as one PR: each step ran in the same branch, "
        "committing in turn, and the change was verified locally before merge.",
        "",
    ]
    return "\n".join(lines)


def _effective_publication(
    repo_type: RepositoryType,
    stored_workflow: Workflow | None,
    requested_workflow: Workflow | None,
    merge_policy: MergePolicy | None,
) -> PublicationDecision:
    """Resolve workflow/policy after node and command precedence has selected inputs."""
    workflow = stored_workflow or requested_workflow or "remote"
    if repo_type == "team":
        if requested_workflow == "local":
            raise RegistryError("repo_type=team cannot use workflow=local")
        workflow = "remote"
        return PublicationDecision(workflow, merge_policy or "none")
    if merge_policy == "none":
        return PublicationDecision("remote", "none")
    if workflow == "local":
        # LocalMergeStrategy has exactly one publication behavior. Report that
        # effective behavior even when a caller supplied the GitHub-only `auto`
        # spelling, instead of claiming a policy the selected strategy ignores.
        return PublicationDecision("local", "direct")
    return PublicationDecision("remote", merge_policy or "auto")


def _stack_body(anchors: list[StackBase], synthetic: str | None) -> str:
    if not anchors:
        return ""
    lines = ["", "## Stack", "This change is stacked on:"]
    for anchor in anchors:
        label = f"`{anchor.branch}`"
        if anchor.pr:
            label += f" ({anchor.pr})"
        lines.append(f"- {label}")
    if synthetic:
        lines += ["", f"Synthetic stack base: `{synthetic}`."]
    lines.append("")
    return "\n".join(lines)


def _validate_lifecycle_branch(value: str, *, field_name: str) -> None:
    """Reject non-literal branch inputs before they reach any Git command."""
    if not gitops.is_valid_branch_name(value):
        raise ConfigError(f"{field_name} {value!r} is not a valid Git branch")


def _validate_runtime_stack_bases(anchors: list[StackBase]) -> None:
    """Validate programmatic anchors as strictly as serialized plan anchors."""
    for index, anchor in enumerate(anchors):
        _validate_lifecycle_branch(anchor.branch, field_name=f"stack_bases #{index} branch")
        for field_name, value in (
            ("base_branch", anchor.base_branch),
            ("pr_base", anchor.pr_base),
        ):
            if value is not None:
                _validate_lifecycle_branch(value, field_name=f"stack_bases #{index} {field_name}")
        if anchor.repo is not None and normalize_repo(anchor.repo).slug != anchor.repo:
            raise ConfigError(f"stack_bases #{index} repo must be a normalized owner/name")
        if anchor.identity is not None:
            try:
                validate_identity_key(str(anchor.identity))
            except RegistryError as exc:
                raise ConfigError(f"stack_bases #{index}: {exc}") from exc
            github_prefix = "https://github.com/"
            if (
                anchor.repo is not None
                and str(anchor.identity).startswith(github_prefix)
                and str(anchor.identity).removeprefix(github_prefix).casefold()
                != anchor.repo.casefold()
            ):
                raise ConfigError(
                    f"stack_bases #{index} repo {anchor.repo!r} does not match identity"
                )
        if anchor.pr is not None:
            matched = re.fullmatch(r"https://github\.com/([^/]+/[^/]+)/pull/[1-9][0-9]*", anchor.pr)
            if matched is None:
                raise ConfigError(f"stack_bases #{index} pr must be a GitHub pull-request URL")
            pr_repo = matched.group(1)
            if anchor.repo is not None and anchor.repo.casefold() != pr_repo.casefold():
                raise ConfigError(
                    f"stack_bases #{index} pr repository {pr_repo!r} does not match "
                    f"repo {anchor.repo!r}"
                )
            if (
                anchor.identity is not None
                and str(anchor.identity).startswith("https://github.com/")
                and str(anchor.identity).removeprefix("https://github.com/").casefold()
                != pr_repo.casefold()
            ):
                raise ConfigError(
                    f"stack_bases #{index} pr repository {pr_repo!r} does not match identity"
                )


def _stack_pr(anchor: StackBase) -> PullRequest | None:
    """Rebuild the stable PR identity carried by a cross-round anchor."""
    if anchor.pr is None:
        return None
    matched = re.fullmatch(r"https://github\.com/([^/]+/[^/]+)/pull/([1-9][0-9]*)", anchor.pr)
    if matched is None:  # runtime validation reports this before stack resolution
        return None
    return PullRequest(
        int(matched.group(2)),
        anchor.pr,
        matched.group(1),
        anchor.branch,
        anchor.pr_base or anchor.base_branch or "",
    )


def _resolve_stack_bases(
    *,
    clone: Path,
    ref: RepoRef,
    identity: IdentityKey,
    root_base: str,
    anchors: list[StackBase],
    github: GitHubBackend | None,
) -> list[StackBase] | StackConflict:
    """Select live same-root anchors and collapse ancestry-redundant prerequisites."""
    candidates: list[StackBase] = []
    root_ref = f"origin/{root_base}"
    backend = github
    for anchor in anchors:
        same_repo = (
            anchor.identity == identity
            if anchor.identity is not None
            else anchor.repo is None or anchor.repo == ref.slug
        )
        if not same_repo:
            continue
        if anchor.base_branch is not None and anchor.base_branch != root_base:
            return StackConflict(
                detail=(
                    f"stack-conflict: prerequisite {anchor.branch!r} belongs to root "
                    f"{anchor.base_branch!r}, not task root {root_base!r}"
                )
            )

        # A root-targeting PR can land between rounds. Query its durable PR state
        # before requiring the (often auto-deleted) head branch, then refetch so
        # the local root tracking ref includes the merge we just observed.
        pr = _stack_pr(anchor)
        if pr is not None and anchor.pr_base == root_base:
            backend = backend or CliGitHubBackend()
            status = backend.status(pr)
            if status.merged:
                gitops.fetch(clone)
                continue
            if status.state == "CLOSED":
                return StackConflict(
                    detail=f"stack-conflict: prerequisite PR {anchor.pr} closed without merging"
                )

        dependency_ref = f"origin/{anchor.branch}"
        try:
            gitops.ref_sha(clone, dependency_ref)
        except GitError:
            return StackConflict(
                detail=(
                    f"stack-conflict: prerequisite branch {anchor.branch!r} is missing from origin"
                )
            )
        if gitops.is_ancestor(clone, dependency_ref, root_ref):
            continue
        candidates.append(anchor)

    resolved: list[StackBase] = []
    for candidate in candidates:
        candidate_ref = f"origin/{candidate.branch}"
        if any(
            gitops.is_ancestor(clone, candidate_ref, f"origin/{existing.branch}")
            for existing in resolved
        ):
            continue
        resolved = [
            existing
            for existing in resolved
            if not gitops.is_ancestor(clone, f"origin/{existing.branch}", candidate_ref)
        ]
        resolved.append(candidate)
    return resolved


def _run_steps(
    steps: list[Step],
    *,
    worktree: Path,
    branch: str,
    pr_base: str,
    dispatch_fn: DispatchFn,
    oneharness_mode: str | None,
    base_path: str | Path,
    persona_dir: str | Path,
) -> tuple[bool, list[StepResult], str]:
    """Run a step sub-DAG in the shared worktree, committing per step.

    Execution is **serialized in topological order** (concurrency 1): the steps
    share one working tree, so running two dispatches into it at once would corrupt
    it — deps express ordering, and each step sees its predecessors' commits. A
    step that does not complete fails the workstream and skips its dependents.
    Returns ``(all_done, per-step results, detail)``.
    """
    by_id = {s.id: s for s in steps}
    deps = {s.id: s.deps for s in steps}
    reports: dict[str, Report] = {}

    def run_step(sid: str) -> NodeRun:
        step = by_id[sid]
        report = dispatch_fn(
            step.persona,
            step.task,
            project_dir=str(worktree),
            oneharness_mode=oneharness_mode,
            base_path=base_path,
            persona_dir=persona_dir,
            session=f"{branch}:{sid}",
            max_turns=step.max_turns,
            done_when=step.done_when,
        )
        reports[sid] = report
        if not report.completed:
            if gitops.is_dirty(worktree):
                gitops.add_all(worktree)
                gitops.commit(worktree, _incomplete_commit_message(step, pr_base))
                return NodeRun(
                    "failed",
                    f"step {sid!r} hit the turn cap; partial work was committed to branch "
                    f"{branch!r}",
                    report,
                )
            return NodeRun("failed", f"step {sid!r} hit the turn cap", report)
        if gitops.is_dirty(worktree):
            gitops.add_all(worktree)
            gitops.commit(worktree, _step_commit_message(step))
        return NodeRun("done", None, report)

    runs, _order = schedule_dag(list(by_id), deps, run_step, concurrency=1)
    status_map = {"done": "done", "failed": "not-completed", "skipped": "skipped"}
    results = [
        StepResult(
            id=sid,
            persona=by_id[sid].persona,
            status=status_map.get(runs[sid].status, "skipped") if sid in runs else "skipped",
            report=reports.get(sid),
        )
        for sid in by_id
    ]
    all_done = all(r.status == "done" for r in results)
    detail = ""
    if not all_done:
        bad = next(r for r in results if r.status != "done")
        detail = runs[bad.id].error or f"step {bad.id!r} {bad.status}"
    return all_done, results, detail


def _select_merge_strategy(
    ref: RepoRef,
    merge: MergeStrategy | None,
    github: GitHubBackend | None,
    workflow: Workflow | None = None,
) -> MergeStrategy:
    if merge is not None:
        return merge
    if workflow == "local":
        return LocalMergeStrategy()
    return GitHubMergeStrategy(github or CliGitHubBackend())


def _build_synthetic_stack_base(
    ref: RepoRef,
    workspace: Workspace,
    root_base: str,
    anchors: list[StackBase],
) -> StackBuildResult:
    """Build and push a multi-parent base, or return a stack-conflict detail."""
    key = "\x00".join(anchor.branch for anchor in anchors)
    digest = _short_hash(ref.slug, root_base, key)
    branch = f"ai-orchestrator/stack-base/{digest}-{uuid.uuid4().hex[:10]}"
    worktree = workspace.worktree(ref, branch, base=f"origin/{root_base}")
    pushed = False
    try:
        for anchor in anchors:
            dependency = f"origin/{anchor.branch}"
            if gitops.is_ancestor(worktree, dependency, "HEAD"):
                continue
            if not gitops.merge_base_into_branch(
                worktree,
                dependency,
                message=f"Merge stack prerequisite {anchor.branch}",
            ):
                return StackConflict(
                    detail=(
                        f"stack-conflict: could not merge prerequisite {anchor.branch!r} "
                        f"into synthetic base from {root_base!r}"
                    )
                )
        gitops.push(worktree, branch)
        pushed = True
        return SyntheticStackBase(branch)
    finally:
        workspace.remove_worktree(ref, worktree)
        if not pushed:
            workspace.delete_branch(ref, branch)


def run_repo_task(
    repo: str,
    task: str | None = None,
    persona: str | None = None,
    *,
    workspace: Workspace,
    steps: list[Step] | None = None,
    merge: MergeStrategy | None = None,
    workflow: Workflow | None = None,
    repo_type: RepositoryType | None = None,
    execution_checkout: str | Path | None = None,
    github: GitHubBackend | None = None,
    base_branch: str | None = None,
    branch: str | None = None,
    title: str | None = None,
    body: str | None = None,
    url: str | None = None,
    verify_cmd: list[str] | None = None,
    skip_verify: bool = False,
    merge_policy: MergePolicy | None = None,
    merge_method: str = "squash",
    oneharness_mode: str | None = "bypass",
    dispatch_fn: DispatchFn = dispatch,
    base_path: str | Path = BASE_CONFIG,
    persona_dir: str | Path = PERSONA_DIR,
    max_turns: int | None = None,
    done_when: str | None = None,
    gate_timeout: float | None = None,
    publication_attempts: int = 3,
    poll_interval: float = 15.0,
    timeout: float = 3600.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    cleanup: bool = True,
    stack_bases: list[StackBase] | None = None,
    resume: Resume | None = None,
) -> LifecycleResult:
    """Take one subtask from a fresh branch to a merged change on ``repo``.

    The merge step is a `MergeStrategy`: for a GitHub repo, open a PR and let
    GitHub auto-merge on green required checks; for a **local** repo (a path), a
    direct merge into the base branch after the checks pass. It is selected only
    from affirmative ``workflow=local`` metadata (or an explicit ``merge``
    strategy); ``github`` supplies the backend for the remote path.

    ``oneharness_mode`` defaults to ``"bypass"`` (no approvals, no inner sandbox):
    the container is the sandbox, so this is the "complete tasks without approvals"
    mode. The agent runs in an isolated worktree; the change is verified with the
    repo's own gate before it is pushed, and never merges off a non-required check.

    Pass ``steps`` (a sub-DAG of `Step`s) to run **several onejudge on one PR**: they
    share the branch/worktree, run in dependency order committing in turn, and the
    result is verified and merged **once**. A single ``(persona, task)`` is the
    one-step case.
    """
    effective_steps = steps or (
        [Step("main", persona, task, max_turns=max_turns, done_when=done_when)]
        if persona and task
        else []
    )
    if not effective_steps:
        raise ConfigError("run_repo_task needs either (persona, task) or a non-empty steps list")
    lead = effective_steps[0]

    ref = normalize_repo(repo)
    result = LifecycleResult(
        repo=ref.slug,
        task=lead.task,
        persona=lead.persona or lead.kind,
        base_branch=base_branch or "",
        branch=branch or _workstream_branch_name(effective_steps),
        outcome="error",
    )
    worktree: Path | None = None
    try:
        _validate_lifecycle_branch(result.branch, field_name="branch")
        if base_branch is not None:
            _validate_lifecycle_branch(base_branch, field_name="base_branch")
        _validate_runtime_stack_bases(stack_bases or [])
        clone = workspace.ensure_clone(
            ref,
            url=url,
            base_branch=base_branch,
            execution_checkout=execution_checkout,
            repo_type=repo_type,
        )
        selection = workspace.selection(ref)
        registered_workflow = selection.workflow
        if (
            workflow is not None
            and registered_workflow is not None
            and workflow != registered_workflow
        ):
            raise RegistryError(
                f"task workflow={workflow} conflicts with repository identity workflow="
                f"{registered_workflow}; use 'just migrate-repo-workflow {repo} "
                f"--workflow {workflow}' between runs"
            )
        effective_type = repo_type or selection.repo_type
        if effective_type is None:
            raise RegistryError("repository type is unclassified; pass --repo-type explicitly")
        decision = _effective_publication(
            effective_type, registered_workflow, workflow, merge_policy
        )
        result.execution_checkout = str(selection.execution_checkout)
        result.publication_checkout = str(selection.publication_checkout)
        result.publication_identity = selection.publication_identity
        result.publication_workflow = decision.workflow
        result.repository_type = effective_type
        result.merge_policy = decision.merge_policy
        strategy = _select_merge_strategy(ref, merge, github, decision.workflow)
        root_base = base_branch or gitops.default_branch(clone)
        result.base_branch = root_base
        result.pr_base = root_base
        stack_resolution = _resolve_stack_bases(
            clone=clone,
            ref=ref,
            identity=selection.publication_identity,
            root_base=root_base,
            anchors=stack_bases or [],
            github=github,
        )
        if isinstance(stack_resolution, StackConflict):
            result.outcome = "stack-conflict"
            result.detail = stack_resolution.detail
            result.stack_bases = list(stack_bases or [])
            return result
        applicable_stack = stack_resolution
        result.stack_bases = applicable_stack
        if len(applicable_stack) > 1:
            stack_result = _build_synthetic_stack_base(ref, workspace, root_base, applicable_stack)
            if isinstance(stack_result, StackConflict):
                result.outcome = "stack-conflict"
                result.detail = stack_result.detail
                return result
            pr_base = stack_result.branch
            result.synthetic_stack_base = stack_result.branch
        elif applicable_stack:
            pr_base = applicable_stack[0].branch
        else:
            pr_base = root_base
        result.pr_base = pr_base
        branch = result.branch
        worktree = workspace.worktree(ref, branch, base=f"origin/{pr_base}")

        all_done, step_results, step_detail = _run_steps(
            effective_steps,
            worktree=worktree,
            branch=branch,
            pr_base=pr_base,
            dispatch_fn=dispatch_fn,
            oneharness_mode=oneharness_mode,
            base_path=base_path,
            persona_dir=persona_dir,
        )
        result.steps = step_results
        result.report = next(
            (r.report for r in reversed(step_results) if r.status == "done" and r.report), None
        )
        if not all_done:
            result.outcome = "not-completed"
            result.detail = f"workstream did not complete: {step_detail}"
            return result

        with advisory_lock(f"git:{gitops.common_dir(worktree)}"):
            gitops.fetch(worktree)
        remote_base = f"origin/{pr_base}"
        if not gitops.merge_base_into_branch(
            worktree,
            remote_base,
            message=f"Merge {remote_base} into {branch}",
        ):
            result.outcome = "gate-failed"
            result.detail = (
                f"sync-conflict: could not merge current {remote_base} into {branch}; "
                "merge aborted and branch was not pushed"
            )
            return result

        if not skip_verify:
            cmd = verify_cmd or detect_gate(worktree)
            if cmd is not None:
                verify = run_gate(
                    worktree,
                    cmd,
                    timeout=gate_timeout,
                    env={
                        "ORCHESTRATOR_COMPARISON_REMOTE": "origin",
                        "ORCHESTRATOR_COMPARISON_BASE": pr_base,
                    },
                )
                result.verify = verify
                if not verify.ok:
                    result.outcome = "gate-failed"
                    result.detail = f"local gate failed: {' '.join(cmd)}"
                    return result
            else:
                result.detail = "no local gate detected; relying on required CI checks"

        # Each step commits its own work in _run_steps, so the worktree is clean
        # here; if no step produced a commit, there is nothing to open a PR for.
        if not gitops.has_commits_ahead(worktree, remote_base):
            result.outcome = "no-changes"
            result.detail = "agent completed but produced no commits to open a PR"
            return result

        gitops.push(worktree, branch)
        ctx = MergeContext(
            repo_slug=ref.slug,
            clone_dir=clone,
            base=pr_base,
            branch=branch,
            title=title or _default_title(lead.persona, lead.task),
            body=(body or _workstream_body(effective_steps, step_results))
            + _stack_body(applicable_stack, result.synthetic_stack_base),
            method=merge_method,
            policy=decision.merge_policy,
            poll_interval=poll_interval,
            timeout=timeout,
            sleep=sleep,
            clock=clock,
            verify_command=None if skip_verify else (verify_cmd or detect_gate(worktree)),
            gate_timeout=gate_timeout,
            verify_env={
                "ORCHESTRATOR_COMPARISON_REMOTE": "origin",
                "ORCHESTRATOR_COMPARISON_BASE": pr_base,
            },
            publication_attempts=publication_attempts,
        )
        merge_outcome = strategy.publish_and_merge(ctx)
        if merge_outcome.outcome == "merged" and pr_base == root_base:
            workspace.fast_forward(ref, root_base)
        result.pr = merge_outcome.pr
        result.outcome = merge_outcome.outcome
        result.detail = merge_outcome.detail
        return result
    except (GitError, GitHubError, ConfigError, RegistryError, WorkspaceError) as exc:
        result.outcome = "error"
        result.detail = str(exc)
        return result
    finally:
        if cleanup and worktree is not None:
            workspace.remove_worktree(ref, worktree)


# --- multi-PR plans --------------------------------------------------------


@dataclass
class RepoPlanNode:
    """One PR in a repo-plan. Either a single (persona, task) or a `steps` workstream."""

    id: str
    repo: str
    persona: str | None = None
    task: str | None = None
    deps: list[str] = field(default_factory=list)
    base_branch: str | None = None
    branch: str | None = None
    title: str | None = None
    verify_cmd: list[str] | None = None
    skip_verify: bool = False
    merge_policy: MergePolicy | None = None
    workflow: Workflow | None = None
    repo_type: RepositoryType | None = None
    execution_checkout: str | None = None
    max_turns: int | None = None
    done_when: str | None = None
    steps: list[Step] | None = None
    stack_bases: list[StackBase] = field(default_factory=list)
    resume: Resume | None = None


@dataclass
class RepoPlan:
    tasks: list[RepoPlanNode]
    concurrency: int = 4


@dataclass
class RepoTaskResult:
    id: str
    status: str  # "done" | "failed" | "skipped"
    result: LifecycleResult | None = None
    error: str | None = None


@dataclass
class RepoPlanResult:
    results: dict[str, RepoTaskResult]
    started_order: list[str]

    @property
    def ok(self) -> bool:
        return all(r.status == "done" for r in self.results.values())

    def summary(self) -> str:
        lines = [
            f"repo-plan: {'all changes published' if self.ok else 'some subtasks did not complete'}"
        ]
        for nid, r in self.results.items():
            outcome = r.result.outcome if r.result else r.status
            detail = f" ({r.error})" if r.error else ""
            lines.append(f"  {nid}: {r.status} [{outcome}]{detail}")
        return "\n".join(lines)


def load_repo_plan(path: str | Path) -> RepoPlan:
    """Load and validate a repo-plan file (JSON or YAML); raise PlanError on any issue."""
    from .plan import PlanError

    try:
        data = load_yaml(path)
    except ConfigError as exc:
        raise PlanError(str(exc)) from exc
    return parse_repo_plan(data)


def parse_repo_plan(data: dict[str, Any]) -> RepoPlan:
    """Validate an in-memory repo-plan mapping into a `RepoPlan` (raise PlanError)."""
    from .plan import PlanError, _topological_order

    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise PlanError("repo-plan must have a non-empty 'tasks' list")
    concurrency = data.get("concurrency", 4)
    if not isinstance(concurrency, int) or isinstance(concurrency, bool) or concurrency < 1:
        raise PlanError("'concurrency' must be a positive integer")

    nodes: dict[str, RepoPlanNode] = {}
    for i, t in enumerate(raw_tasks):
        if not isinstance(t, dict):
            raise PlanError(f"task #{i} must be a mapping")
        nid = t.get("id")
        if not isinstance(nid, str) or not nid:
            raise PlanError(f"task #{i} needs a non-empty string 'id'")
        if nid in nodes:
            raise PlanError(f"duplicate task id: {nid!r}")
        nodes[nid] = parse_repo_node(nid, t)

    for nid, node in nodes.items():
        for dep in node.deps:
            if dep not in nodes:
                raise PlanError(f"task {nid!r} depends on unknown task {dep!r}")
            if dep == nid:
                raise PlanError(f"task {nid!r} depends on itself")
    _topological_order({nid: _to_plan_node(n) for nid, n in nodes.items()})  # cycle check

    return RepoPlan(tasks=list(nodes.values()), concurrency=concurrency)


def parse_repo_node(nid: str, t: dict[str, Any]) -> RepoPlanNode:
    """Validate one lifecycle node into a `RepoPlanNode`."""
    from .plan import PlanError

    if not isinstance(t.get("repo"), str) or not str(t.get("repo")).strip():
        raise PlanError(f"task {nid!r} needs a non-empty 'repo'")
    raw_steps = t.get("steps")
    if raw_steps is not None:
        node_steps = _parse_steps(nid, raw_steps)
        persona = task = None
    else:
        for key in ("persona", "task"):
            if not isinstance(t.get(key), str) or not str(t.get(key)).strip():
                raise PlanError(f"task {nid!r} needs a non-empty {key!r} (or a 'steps' list)")
        node_steps = None
        persona, task = t["persona"], t["task"]
    deps = t.get("deps", [])
    if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
        raise PlanError(f"task {nid!r} 'deps' must be a list of ids")
    policy = t.get("merge_policy")
    if policy is not None and policy not in MERGE_POLICIES:
        raise PlanError(f"task {nid!r} 'merge_policy' must be one of {MERGE_POLICIES}")
    merge_policy = cast(MergePolicy | None, policy)
    raw_workflow = t.get("workflow")
    if raw_workflow is not None and raw_workflow not in ("local", "remote"):
        raise PlanError(f"task {nid!r} 'workflow' must be 'local' or 'remote'")
    workflow = cast(Workflow | None, raw_workflow)
    raw_repo_type = t.get("repo_type")
    if raw_repo_type is not None and raw_repo_type not in ("single-owner", "team"):
        raise PlanError(f"task {nid!r} 'repo_type' must be 'single-owner' or 'team'")
    repo_type = cast(RepositoryType | None, raw_repo_type)
    if repo_type == "team" and workflow == "local":
        raise PlanError(f"task {nid!r} repo_type=team cannot use workflow=local")
    for field_name in ("base_branch", "branch"):
        raw_branch = t.get(field_name)
        if raw_branch is not None and (
            not isinstance(raw_branch, str) or not gitops.is_valid_branch_name(raw_branch)
        ):
            raise PlanError(f"task {nid!r} {field_name!r} must be a valid non-empty Git branch")
    raw_execution = t.get("execution_checkout")
    if raw_execution is not None and (
        not isinstance(raw_execution, str) or not raw_execution.strip()
    ):
        raise PlanError(f"task {nid!r} 'execution_checkout' must be a non-empty path")
    return RepoPlanNode(
        id=nid,
        repo=t["repo"],
        persona=persona,
        task=task,
        deps=list(deps),
        base_branch=t.get("base_branch"),
        branch=t.get("branch"),
        title=t.get("title"),
        verify_cmd=t.get("verify_cmd"),
        skip_verify=bool(t.get("skip_verify", False)),
        merge_policy=merge_policy,
        workflow=workflow,
        repo_type=repo_type,
        execution_checkout=raw_execution,
        max_turns=t.get("max_turns"),
        done_when=t.get("done_when"),
        steps=node_steps,
        stack_bases=_parse_stack_bases(nid, t.get("stack_bases", [])),
        resume=_parse_resume(nid, t.get("resume"), node_steps),
    )


_SHA = re.compile(r"[0-9a-f]{7,40}")
_PR_URL = re.compile(r"https://github\.com/([^/]+/[^/]+)/pull/([1-9][0-9]*)")


def _parse_resume(nid: str, raw: object, steps: list[Step] | None) -> Resume | None:
    """Validate continuation metadata from a prior human-gated lifecycle round."""
    from .plan import PlanError

    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise PlanError(f"task {nid!r} 'resume' must be a mapping")
    allowed = {"branch", "base_branch", "pr_base", "checkpoint", "completed_steps", "pr"}
    if unknown := set(raw) - allowed:
        raise PlanError(f"task {nid!r} 'resume' has unknown fields: {', '.join(sorted(unknown))}")
    for field_name in ("branch", "base_branch", "pr_base"):
        value = raw.get(field_name)
        if not isinstance(value, str) or not gitops.is_valid_branch_name(value):
            raise PlanError(f"task {nid!r} resume {field_name!r} must be a valid Git branch")
    checkpoint = raw.get("checkpoint")
    if not isinstance(checkpoint, str) or not _SHA.fullmatch(checkpoint):
        raise PlanError(f"task {nid!r} resume 'checkpoint' must be a Git commit SHA")
    completed = raw.get("completed_steps", [])
    if not isinstance(completed, list) or not all(isinstance(s, str) for s in completed):
        raise PlanError(f"task {nid!r} resume 'completed_steps' must be a list of step ids")
    known = {s.id for s in steps or []}
    if unknown_steps := set(completed) - known:
        raise PlanError(
            f"task {nid!r} resume 'completed_steps' names unknown steps: "
            f"{', '.join(sorted(unknown_steps))}"
        )
    pr = raw.get("pr")
    if pr is not None and (not isinstance(pr, str) or _PR_URL.fullmatch(pr) is None):
        raise PlanError(f"task {nid!r} resume 'pr' must be a GitHub pull-request URL")
    return Resume(
        branch=cast(str, raw["branch"]),
        base_branch=cast(str, raw["base_branch"]),
        pr_base=cast(str, raw["pr_base"]),
        checkpoint=checkpoint,
        completed_steps=tuple(completed),
        pr=pr,
    )


def _parse_stack_bases(nid: str, raw: object) -> list[StackBase]:
    from .plan import PlanError

    if not isinstance(raw, list):
        raise PlanError(f"task {nid!r} 'stack_bases' must be a list of anchors")
    anchors: list[StackBase] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise PlanError(f"task {nid!r} stack_bases #{index} must be a mapping")
        allowed = {"branch", "repo", "identity", "base_branch", "pr", "pr_base"}
        if set(item) - allowed:
            raise PlanError(f"task {nid!r} stack_bases #{index} has unknown fields")
        branch = item.get("branch")
        if not isinstance(branch, str) or not branch.strip():
            raise PlanError(f"task {nid!r} stack_bases #{index} needs a non-empty 'branch'")
        if not gitops.is_valid_branch_name(branch):
            raise PlanError(f"task {nid!r} stack_bases #{index} 'branch' is not a valid Git branch")
        for field_name in ("repo", "identity", "base_branch", "pr", "pr_base"):
            value = item.get(field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise PlanError(
                    f"task {nid!r} stack_bases #{index} {field_name!r} must be a non-empty string"
                )
        raw_repo = item.get("repo")
        if isinstance(raw_repo, str):
            try:
                normalized_repo = normalize_repo(raw_repo)
            except ValueError as exc:
                raise PlanError(f"task {nid!r} stack_bases #{index} has an invalid 'repo'") from exc
            if raw_repo != normalized_repo.slug:
                raise PlanError(
                    f"task {nid!r} stack_bases #{index} 'repo' must be a normalized owner/name"
                )
        raw_base = item.get("base_branch")
        if isinstance(raw_base, str) and not gitops.is_valid_branch_name(raw_base):
            raise PlanError(
                f"task {nid!r} stack_bases #{index} 'base_branch' is not a valid Git branch"
            )
        raw_pr_base = item.get("pr_base")
        if isinstance(raw_pr_base, str) and not gitops.is_valid_branch_name(raw_pr_base):
            raise PlanError(
                f"task {nid!r} stack_bases #{index} 'pr_base' is not a valid Git branch"
            )
        raw_identity = item.get("identity")
        if (
            isinstance(raw_repo, str)
            and isinstance(raw_identity, str)
            and raw_identity.startswith("https://github.com/")
            and raw_identity.removeprefix("https://github.com/").casefold() != raw_repo.casefold()
        ):
            raise PlanError(f"task {nid!r} stack_bases #{index} 'repo' does not match 'identity'")
        raw_pr = item.get("pr")
        if isinstance(raw_pr, str):
            matched_pr = re.fullmatch(r"https://github\.com/([^/]+/[^/]+)/pull/[1-9][0-9]*", raw_pr)
            if matched_pr is None:
                raise PlanError(
                    f"task {nid!r} stack_bases #{index} 'pr' must be a GitHub pull-request URL"
                )
            pr_repo = matched_pr.group(1)
            if isinstance(raw_repo, str) and raw_repo.casefold() != pr_repo.casefold():
                raise PlanError(
                    f"task {nid!r} stack_bases #{index} 'pr' repository does not match 'repo'"
                )
            if (
                isinstance(raw_identity, str)
                and raw_identity.startswith("https://github.com/")
                and raw_identity.removeprefix("https://github.com/").casefold()
                != pr_repo.casefold()
            ):
                raise PlanError(
                    f"task {nid!r} stack_bases #{index} 'pr' repository does not match 'identity'"
                )
        try:
            identity = (
                validate_identity_key(raw_identity) if isinstance(raw_identity, str) else None
            )
        except RegistryError as exc:
            raise PlanError(f"task {nid!r} stack_bases #{index}: {exc}") from exc
        anchors.append(
            StackBase(
                branch=branch,
                repo=item.get("repo"),
                identity=identity,
                base_branch=item.get("base_branch"),
                pr=item.get("pr"),
                pr_base=item.get("pr_base"),
            )
        )
    return anchors


_AGENT_STEP_FIELDS = ("persona", "max_turns", "done_when")


def _parse_steps(nid: str, raw_steps: object) -> list[Step]:
    """Validate a node's `steps` sub-DAG and return `Step`s (raise PlanError)."""
    from .plan import PlanError, PlanNode, _topological_order

    if not isinstance(raw_steps, list) or not raw_steps:
        raise PlanError(f"task {nid!r} 'steps' must be a non-empty list")
    steps: dict[str, Step] = {}
    for j, s in enumerate(raw_steps):
        if not isinstance(s, dict):
            raise PlanError(f"task {nid!r} step #{j} must be a mapping")
        sid = s.get("id")
        if not isinstance(sid, str) or not sid:
            raise PlanError(f"task {nid!r} step #{j} needs a non-empty string 'id'")
        if sid in steps:
            raise PlanError(f"task {nid!r} has a duplicate step id: {sid!r}")
        kind = s.get("kind", "agent")
        if kind not in NODE_KINDS:
            raise PlanError(f"task {nid!r} step {sid!r} 'kind' must be one of {NODE_KINDS}")
        if not isinstance(s.get("task"), str) or not str(s.get("task")).strip():
            raise PlanError(f"task {nid!r} step {sid!r} needs a non-empty 'task'")
        if kind == "human":
            present = [key for key in _AGENT_STEP_FIELDS if s.get(key) is not None]
            if present:
                raise PlanError(
                    f"task {nid!r} human step {sid!r} cannot set {', '.join(map(repr, present))}"
                )
        elif not isinstance(s.get("persona"), str) or not str(s.get("persona")).strip():
            raise PlanError(f"task {nid!r} step {sid!r} needs a non-empty 'persona'")
        sdeps = s.get("deps", [])
        if not isinstance(sdeps, list) or not all(isinstance(d, str) for d in sdeps):
            raise PlanError(f"task {nid!r} step {sid!r} 'deps' must be a list of ids")
        steps[sid] = Step(
            id=sid,
            persona=s.get("persona"),
            task=s["task"],
            kind=kind,
            deps=list(sdeps),
            max_turns=s.get("max_turns"),
            done_when=s.get("done_when"),
        )
    for sid, step in steps.items():
        for dep in step.deps:
            if dep not in steps:
                raise PlanError(f"task {nid!r} step {sid!r} depends on unknown step {dep!r}")
            if dep == sid:
                raise PlanError(f"task {nid!r} step {sid!r} depends on itself")
    _topological_order(  # raises PlanError on a step cycle
        {sid: PlanNode(id=sid, persona="_", task="_", deps=st.deps) for sid, st in steps.items()}
    )
    return list(steps.values())


def _to_plan_node(node: RepoPlanNode) -> Any:
    from .plan import PlanNode

    return PlanNode(id=node.id, persona=node.persona or "_", task=node.task or "_", deps=node.deps)


def run_repo_plan(
    plan: RepoPlan,
    runner: Callable[[RepoPlanNode], LifecycleResult],
    *,
    concurrency: int | None = None,
) -> RepoPlanResult:
    """Schedule the DAG, driving each node through its repo lifecycle via `runner`.

    A node is ``done`` when its `LifecycleResult.ok`, else ``failed``; a failed or
    skipped dependency skips the node (its PR is never opened against a broken
    precondition). `runner` is injected so the scheduler is unit-tested without
    git/GitHub while the e2e drives the real lifecycle.
    """
    conc = concurrency if concurrency is not None else plan.concurrency
    nodes = {n.id: n for n in plan.tasks}
    deps = {nid: nodes[nid].deps for nid in nodes}
    completed: dict[str, LifecycleResult] = {}

    def dependency_anchor(result: LifecycleResult) -> StackBase | None:
        match result.outcome:
            case "pr-open":
                branch = result.branch
            case "merged" if result.pr_base != result.base_branch:
                branch = result.pr_base
            case _:
                return None
        return StackBase(
            branch=branch,
            repo=result.repo,
            identity=result.publication_identity,
            base_branch=result.base_branch,
            pr=result.pr.url if result.pr else None,
            pr_base=result.pr_base,
        )

    def run_one(nid: str) -> NodeRun:
        node = nodes[nid]
        dynamic = [
            anchor
            for dep in node.deps
            if dep in completed
            for anchor in (dependency_anchor(completed[dep]),)
            if anchor is not None
        ]
        combined = list(node.stack_bases)
        for anchor in dynamic:
            if all(existing.branch != anchor.branch for existing in combined):
                combined.append(anchor)
        result = runner(replace(node, stack_bases=combined))
        if result.ok:
            completed[nid] = result
            return NodeRun("done", None, result)
        return NodeRun("failed", result.detail or result.outcome, result)

    runs, started_order = schedule_dag(list(nodes), deps, run_one, concurrency=conc)
    results = {
        nid: RepoTaskResult(
            nid,
            runs[nid].status,
            result=runs[nid].payload if isinstance(runs[nid].payload, LifecycleResult) else None,
            error=runs[nid].error,
        )
        for nid in nodes
        if nid in runs
    }
    return RepoPlanResult(results=results, started_order=started_order)


def dependency_anchor(result: LifecycleResult) -> StackBase | None:
    """Return the stack prerequisite a completed dependency leaves behind."""
    match result.outcome:
        case "pr-open":
            branch = result.branch
        case "merged" if result.pr_base != result.base_branch:
            branch = result.pr_base
        case _:
            return None
    return StackBase(
        branch=branch,
        repo=result.repo,
        identity=result.publication_identity,
        base_branch=result.base_branch,
        pr=result.pr.url if result.pr else None,
        pr_base=result.pr_base,
    )


def combine_stack_bases(
    node: RepoPlanNode, completed: Mapping[str, LifecycleResult]
) -> list[StackBase]:
    """Merge declared anchors with anchors produced by completed dependencies."""
    combined = list(node.stack_bases)
    for dep in node.deps:
        result = completed.get(dep)
        anchor = dependency_anchor(result) if result is not None else None
        if anchor is not None and all(existing.branch != anchor.branch for existing in combined):
            combined.append(anchor)
    return combined


def make_repo_runner(
    *,
    workspace: Workspace,
    github: GitHubBackend | None = None,
    base_path: str | Path,
    persona_dir: str | Path,
    merge_policy: MergePolicy | None,
    merge_method: str,
    oneharness_mode: str | None,
    skip_verify: bool,
    poll_interval: float,
    timeout: float,
    publication_attempts: int = 3,
    repo_type: RepositoryType | None = None,
) -> Callable[[RepoPlanNode], LifecycleResult]:
    """Build the production runner that drives each node through `run_repo_task`."""

    def runner(node: RepoPlanNode) -> LifecycleResult:
        return run_repo_task(
            node.repo,
            node.task,
            node.persona,
            workspace=workspace,
            steps=node.steps,
            github=github,
            workflow=node.workflow,
            repo_type=node.repo_type or repo_type,
            execution_checkout=node.execution_checkout,
            base_branch=node.base_branch,
            branch=node.branch,
            title=node.title,
            verify_cmd=node.verify_cmd,
            skip_verify=node.skip_verify or skip_verify,
            merge_policy=(node.merge_policy if node.merge_policy is not None else merge_policy),
            merge_method=merge_method,
            oneharness_mode=oneharness_mode,
            base_path=base_path,
            persona_dir=persona_dir,
            max_turns=node.max_turns,
            done_when=node.done_when,
            poll_interval=poll_interval,
            timeout=timeout,
            publication_attempts=publication_attempts,
            stack_bases=node.stack_bases,
            resume=node.resume,
        )

    return runner


# --- CLI -------------------------------------------------------------------


def _read_task(value: str | None) -> str:
    if value is None or value == "-":
        return sys.stdin.read()
    return value


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def add_lifecycle_args(parser: argparse.ArgumentParser) -> None:
    """Add flags shared by commands that can drive repo lifecycle nodes."""
    parser.add_argument("--base", type=Path, default=BASE_CONFIG, dest="base_config")
    parser.add_argument("--persona-dir", type=Path, default=PERSONA_DIR)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.home() / ".ai-orchestrator" / "worktrees",
        help="root directory for isolated task worktrees",
    )
    parser.add_argument("--merge-policy", choices=MERGE_POLICIES, default=None)
    parser.add_argument("--repo-type", choices=("single-owner", "team"), default=None)
    parser.add_argument("--merge-method", choices=MERGE_METHODS, default="squash")
    parser.add_argument(
        "--oneharness-mode",
        default="bypass",
        choices=["read-only", "plan", "default", "edit", "auto", "bypass"],
        help="approval/sandbox mode for the harness (default: bypass — the "
        "no-approval mode; the container is the sandbox)",
    )
    parser.add_argument("--skip-verify", action="store_true", help="skip the local gate")
    parser.add_argument("--poll-interval", type=float, default=15.0)
    parser.add_argument("--timeout", type=float, default=3600.0)
    parser.add_argument(
        "--publication-attempts",
        type=_positive_int,
        default=3,
        help="maximum verified attempts to publish a local base (default: 3)",
    )
    parser.add_argument("--format", choices=["human", "json"], default="human")
    parser.add_argument("-o", "--output", type=Path, default=None)


_add_common_args = add_lifecycle_args


def emit(rendered: str, output: Path | None) -> None:
    """Write rendered output to a file or stdout."""
    if output:
        output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


_emit = emit


def result_payload(result: LifecycleResult) -> dict[str, Any]:
    """Serialize one lifecycle outcome for JSON output and the run ledger."""
    return {
        "repo": result.repo,
        "execution_checkout": result.execution_checkout,
        "publication_checkout": result.publication_checkout,
        "publication_identity": result.publication_identity,
        "repo_type": result.repository_type,
        "repository_type": result.repository_type,
        "publication_workflow": result.publication_workflow,
        "workflow": result.publication_workflow,
        "merge_policy": result.merge_policy,
        "branch": result.branch,
        "base_branch": result.base_branch,
        "pr_base": result.pr_base,
        "synthetic_stack_base": result.synthetic_stack_base,
        "stack_bases": [
            {
                "branch": anchor.branch,
                "repo": anchor.repo,
                "identity": anchor.identity,
                "base_branch": anchor.base_branch,
                "pr": anchor.pr,
                "pr_base": anchor.pr_base,
            }
            for anchor in result.stack_bases
        ],
        "outcome": result.outcome,
        "ok": result.ok,
        "pr": result.pr.url if result.pr else None,
        "detail": result.detail,
        "follow_ups": result.report.assessment if result.report else None,
        "steps": [
            {"id": s.id, "kind": s.kind, "persona": s.persona, "status": s.status}
            for s in result.steps
        ],
        "waiting_steps": list(result.waiting_steps),
        "resume": resume_payload(result.resume),
    }


_result_payload = result_payload


def resume_payload(resume: Resume | None) -> dict[str, Any] | None:
    """Serialize continuation metadata for a paused lifecycle workstream."""
    if resume is None:
        return None
    return {
        "branch": resume.branch,
        "base_branch": resume.base_branch,
        "pr_base": resume.pr_base,
        "checkpoint": resume.checkpoint,
        "completed_steps": list(resume.completed_steps),
        "pr": resume.pr,
    }


def main_task(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drive one subtask through a repo's lifecycle (clone→gate→PR/merge)."
    )
    parser.add_argument("repo", help="repo (name, owner/name, GitHub URL, or LOCAL path)")
    parser.add_argument("persona", help="persona name (see personas/)")
    parser.add_argument("task", nargs="?", default=None, help="the task ('-'/omitted reads stdin)")
    parser.add_argument("--base-branch", default=None, help="branch to target (default: repo HEAD)")
    parser.add_argument("--branch", default=None, help="feature branch name (default: derived)")
    parser.add_argument("--title", default=None)
    parser.add_argument(
        "--execution-checkout",
        type=Path,
        default=None,
        help=(
            "exact isolated clone used to create the task worktree; publication identity and "
            "workflow still come from REPO"
        ),
    )
    parser.add_argument("--max-turns", type=int, default=None)
    parser.add_argument("--done-when", default=None)
    _add_common_args(parser)
    args = parser.parse_args(argv)

    result = run_repo_task(
        args.repo,
        _read_task(args.task),
        args.persona,
        workspace=Workspace(args.workspace),
        base_branch=args.base_branch,
        branch=args.branch,
        title=args.title,
        execution_checkout=args.execution_checkout,
        repo_type=args.repo_type,
        verify_cmd=None,
        skip_verify=args.skip_verify,
        merge_policy=args.merge_policy,
        merge_method=args.merge_method,
        oneharness_mode=args.oneharness_mode,
        base_path=args.base_config,
        persona_dir=args.persona_dir,
        max_turns=args.max_turns,
        done_when=args.done_when,
        poll_interval=args.poll_interval,
        publication_attempts=args.publication_attempts,
        timeout=args.timeout,
    )
    rendered = (
        json.dumps(_result_payload(result), indent=2) if args.format == "json" else result.summary()
    )
    _emit(rendered, args.output)
    return 0 if result.ok else 1


def main_plan(argv: list[str] | None = None) -> int:
    from .plan import PlanError

    parser = argparse.ArgumentParser(
        description="Run a repo-plan: many isolated PRs across repos, coordinated by a DAG."
    )
    parser.add_argument("plan", type=Path, help="repo-plan file (JSON or YAML)")
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--run", default=None, help="record into this validated run id")
    parser.add_argument("--no-record", action="store_true", help="do not record this round")
    parser.add_argument("--recover", action="store_true", help="claim an abandoned running round")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"), help="run ledger root")
    _add_common_args(parser)
    args = parser.parse_args(argv)

    try:
        plan_mapping = load_yaml(args.plan)
        plan = parse_repo_plan(plan_mapping)
        run_dir = (
            resolve_run_dir(args.runs_dir, plan_mapping, args.plan, args.run)
            if not args.no_record
            else None
        )
    except (ConfigError, PlanError) as exc:
        print(f"repo-plan: {exc}", file=sys.stderr)
        return 2

    round_record: tuple[int, Path] | None = None
    if run_dir is not None:
        try:
            round_record = prepare_round(run_dir, plan_mapping, recover=args.recover)
        except ConfigError as exc:
            print(f"repo-plan: could not claim run: {exc}", file=sys.stderr)
            return 2

    runner = make_repo_runner(
        workspace=Workspace(args.workspace),
        base_path=args.base_config,
        persona_dir=args.persona_dir,
        merge_policy=args.merge_policy,
        merge_method=args.merge_method,
        oneharness_mode=args.oneharness_mode,
        skip_verify=args.skip_verify,
        poll_interval=args.poll_interval,
        timeout=args.timeout,
        publication_attempts=args.publication_attempts,
        repo_type=args.repo_type,
    )
    result = run_repo_plan(plan, runner, concurrency=args.concurrency)

    payload: RepoPlanPayload = {
        "ok": result.ok,
        "started_order": result.started_order,
        "results": {
            nid: cast(
                RepoPlanResultItem,
                {
                    "status": r.status,
                    **({} if r.result is None else _result_payload(r.result)),
                    "error": r.error,
                },
            )
            for nid, r in result.results.items()
        },
    }
    rendered = json.dumps(payload, indent=2) if args.format == "json" else result.summary()
    _emit(rendered, args.output)
    if run_dir is not None and round_record is not None:
        try:
            number, round_dir = round_record
            write_result(round_dir, payload)
        except ConfigError as exc:
            print(f"repo-plan: could not record run: {exc}", file=sys.stderr)
            return 2
        _print_continuation(run_dir.name, number, round_dir, payload, args.runs_dir)
    return 0 if result.ok else 1


def _print_continuation(
    run_id: str,
    number: int,
    round_dir: Path,
    payload: RepoPlanPayload,
    runs_dir: Path,
) -> None:
    """Print the repo-plan continuation guidance without contaminating stdout."""
    print(
        f"Round {number:02d} recorded -> {round_dir}/  ({status_summary(payload)})",
        file=sys.stderr,
    )
    counts = status_counts(payload)
    if counts and counts["done"] == sum(counts.values()):
        print("All nodes are done; there is nothing to iterate.", file=sys.stderr)
        return
    print("Iterate: write an edits.json (retry/split/add/drop), then", file=sys.stderr)
    suffix = "" if runs_dir == Path("runs") else f" --runs-dir {runs_dir}"
    print(f"  just next-round {run_id} [edits.json]{suffix}", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main_task())
