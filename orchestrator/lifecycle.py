"""Drive one subtask through a target repo's full lifecycle — and many at once.

`run_repo_task` is the unit of real work against an external repo:

    clone/worktree → dispatch (bypass mode) → commit → gated push → PR
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
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

from . import BASE_CONFIG, PERSONA_DIR, gitops
from .cli_contract import DEFAULT_ONEHARNESS_MODE, ONEHARNESS_MODES
from .config import ConfigError, load_yaml
from .coordination import LockTimeout, advisory_lock, atomic_json, git_lock_identity
from .dispatch import Report, dispatch
from .github import CliGitHubBackend, GitHubBackend, GitHubError, PullRequest
from .gitops import GitError
from .ids import GraphId
from .journal import NodeSink, NullNodeJournal
from .merge import (
    MERGE_CONFLICT_RETRY,
    GitHubMergeStrategy,
    LocalMergeStrategy,
    MergeContext,
    MergeOutcome,
    MergePolicy,
    MergeStrategy,
    assess_blocking_checks,
    classify_push_failure,
)
from .outcomes import (
    ALREADY_INTEGRATED_OUTCOME,
    SUCCESSFUL_LIFECYCLE_OUTCOMES,
    WAITING_HUMAN_OUTCOME,
    LifecycleOutcome,
)
from .plan import NODE_KINDS, NodeRun, schedule_dag
from .provenance import (
    INCOMPLETE_TRAILER,
    PR_BASE_TRAILER,
    RECOVERY_TRAILER,
    format_preserved_step_metadata,
    incomplete_commits,
    unattested_incomplete,
)
from .registry import Registry, RegistryError, merge_gate_coverage, validate_identity_key
from .runs import (
    RECORDED_RESULT_SCHEMA_VERSION,
    RESUME_MODES,
    ClaimedRound,
    RepoPlanPayload,
    RepoPlanResultItem,
    ResumeMode,
    ResumePayload,
    RetryDisposition,
    RetryLineagePayload,
    StepId,
    prepare_round,
    resolve_run_dir,
    status_counts,
    status_summary,
    write_result,
)
from .scratch import require_scratch_capacity, scratch_dispatch_guarded
from .verify import NOOP_GATE, VerifyResult, resolve_gate_template
from .workspace import (
    CACHE_ENV,
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

# Keep generated PR titles within the conventional 72-character commit-subject
# boundary. GitHub may use the PR title as the squash subject, so this is a
# release-facing constraint rather than merely a display preference.
_SUBJECT_LIMIT = 72
# The regex owns STRUCTURE only (type, optional scope, breaking `!`, description shape);
# character safety is enforced separately by `str.isprintable()` in the parser. A subject
# reaches git and `gh` as a subprocess argument, where a control character surfaces as an
# OS-level argument error (an embedded NUL raises at spawn), so rejecting non-printables is
# a correctness boundary — and delegating it to the Unicode database is complete by
# construction, where enumerating forbidden ranges (C0, then separators, then C1) was not.
_CONVENTIONAL_SUBJECT = re.compile(
    r"(?P<type>[a-z][a-z0-9-]*)"
    r"(?:\((?P<scope>[^():]+)\))?"
    r"(?P<breaking>!)?: (?P<description>\S(?:.*\S)?)"
)
_BREAKING_FOOTER = re.compile(r"(?m)^BREAKING(?: |-)CHANGE:\s*\S")
_TYPE_PRIORITY = {"feat": 0, "fix": 1, "perf": 2, "refactor": 3}

# This is the executable PR-body contract. A unit drift gate reconciles it with
# the checked-in template, pr-author persona, and lifecycle documentation.
PR_REQUIRED_SECTIONS = ("What", "Why")
PR_OPTIONAL_SECTIONS = ("Additional info",)
TASK_REQUIRED_SECTIONS = (*PR_REQUIRED_SECTIONS, "Acceptance criteria")
TASK_OPTIONAL_SECTIONS = PR_OPTIONAL_SECTIONS

# Workstream paused on a human step. This is neither success nor failure; the next
# recorded round resumes after a human attestation.
_WAITING_OUTCOME = WAITING_HUMAN_OUTCOME

CI_ITERATION_INSTRUCTIONS = """CI verification mode:
CI is the authoritative check for this task. Push your workstream branch to origin and find and
watch the CI run for that pushed head. If any required (blocking) check fails, read its logs, fix
the cause, commit and push again, and repeat until every required check is green. A passing local
gate alone is not done."""

AI_ORCHESTRATOR_IDENTITY = IdentityKey("https://github.com/nickderobertis/ai-orchestrator")


class DispatchFn(Protocol):
    """Structural boundary implemented by the real and deterministic dispatchers."""

    def __call__(
        self,
        persona: str,
        task: str,
        *,
        project_dir: str | None = None,
        oneharness_mode: str | None = None,
        use_llmlint_wrapper: bool = True,
        base_path: str | Path = BASE_CONFIG,
        persona_dir: str | Path = PERSONA_DIR,
        session: str | None = None,
        max_turns: int | None = None,
        done_when: str | None = None,
        extra_instructions: str | None = None,
        labels: Mapping[str, str] | None = None,
        env: dict[str, str] | None = None,
        cancel: threading.Event | None = None,
    ) -> Report: ...


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
    expects_no_diff: bool = False

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
    mode: ResumeMode = "pause"
    source_round: int | None = None


@dataclass
class RetryLineage:
    """Recorded fate of one validated preserved-branch retry."""

    supersedes_branch: str
    supersedes_checkpoint: str
    disposition: RetryDisposition
    reason: str | None = None
    supersedes_round: int | None = None


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
    outcome: LifecycleOutcome
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
    retry_lineage: RetryLineage | None = None
    deferred_cleanup: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.outcome in SUCCESSFUL_LIFECYCLE_OUTCOMES

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


@dataclass(frozen=True)
class _ParsedSubject:
    type: str
    scope: str | None
    breaking: bool
    description: str


def _has_breaking_signal(message: str) -> bool:
    """Detect subject and footer signals without requiring a usable subject."""
    subject = message.splitlines()[0].strip() if message.strip() else ""
    # A non-printable subject is never a usable subject (the regex's `.` would otherwise
    # match a control char), so its `!` must not count; the footer path stays independent.
    matched = _CONVENTIONAL_SUBJECT.fullmatch(subject) if subject.isprintable() else None
    return bool(matched and matched.group("breaking")) or bool(_BREAKING_FOOTER.search(message))


def _parse_conventional_subject(message: str) -> _ParsedSubject | None:
    """Parse one usable Conventional Commit message, including breaking footers."""
    subject = message.splitlines()[0].strip() if message.strip() else ""
    # `isprintable()` is False for every C0/C1 control, DEL, and Unicode separator/format
    # character (ASCII space excepted), so this one check rejects the whole class the regex
    # must not carry into a subprocess argument — completely, via the Unicode database.
    if not subject.isprintable():
        return None
    matched = _CONVENTIONAL_SUBJECT.fullmatch(subject)
    # Conventional Commits permits project-specific types, but `wip` is a branch
    # state rather than a durable change type and must not become a squash subject.
    if matched is None or matched.group("type") == "wip":
        return None
    return _ParsedSubject(
        type=matched.group("type"),
        scope=matched.group("scope"),
        breaking=_has_breaking_signal(message),
        description=matched.group("description"),
    )


def _format_conventional_subject(parsed: _ParsedSubject) -> str:
    """Format and guard one Conventional Commit subject without truncating its prefix."""
    scope = f"({parsed.scope})" if parsed.scope else ""
    prefix = f"{parsed.type}{scope}{'!' if parsed.breaking else ''}: "
    if len(prefix) >= _SUBJECT_LIMIT and parsed.scope is not None:
        # A scope is optional. Drop an exceptionally long one rather than corrupting
        # the type/breaking prefix or emitting a subject without a description.
        prefix = f"{parsed.type}{'!' if parsed.breaking else ''}: "
    available = _SUBJECT_LIMIT - len(prefix)
    description = " ".join(parsed.description.split())
    if available < 1 or not description:
        raise ConfigError("cannot format a valid Conventional Commit subject")
    if len(description) > available:
        description = description[: max(available - 1, 0)].rstrip() + "…"
    subject = prefix + description
    if (
        len(subject) > _SUBJECT_LIMIT
        or (validated := _parse_conventional_subject(subject)) is None
        or validated.breaking != parsed.breaking
    ):
        raise ConfigError(f"generated invalid Conventional Commit subject: {subject!r}")
    return subject


def _task_description(task: str) -> str:
    return task.strip().splitlines()[0] if task.strip() else "orchestrated change"


def _fallback_subject(task: str, *, breaking: bool = False) -> str:
    # Unknown work must not be guessed into a release-triggering feat/fix/perf type.
    # `chore` is deliberately conventional but non-releasing for those workflows.
    return _format_conventional_subject(
        _ParsedSubject("chore", None, breaking, _task_description(task))
    )


def _subject_from_messages(messages: list[gitops.CommitMessage], task: str) -> str:
    """Derive one semantic subject from usable agent-written commit messages."""
    breaking = any(_has_breaking_signal(commit.message) for commit in messages)
    parsed = [
        subject
        for commit in messages
        if (subject := _parse_conventional_subject(commit.message)) is not None
    ]
    match parsed:
        case []:
            return _fallback_subject(task, breaking=breaking)
        case [subject]:
            return _format_conventional_subject(
                _ParsedSubject(subject.type, subject.scope, breaking, subject.description)
            )
        case _:
            pass

    breaking_subjects = [subject for subject in parsed if subject.breaking]
    candidates = breaking_subjects or parsed
    primary = min(candidates, key=lambda subject: _TYPE_PRIORITY.get(subject.type, 4))
    common_scope = (
        primary.scope if all(subject.scope == primary.scope for subject in parsed) else None
    )
    descriptions = [primary.description]
    descriptions.extend(
        subject.description
        for subject in parsed
        if subject is not primary and subject.description not in descriptions
    )
    return _format_conventional_subject(
        _ParsedSubject(primary.type, common_scope, breaking, "; ".join(descriptions))
    )


def _default_title(worktree: Path, base: str, task: str) -> str:
    return _subject_from_messages(gitops.log_messages(worktree, base, "HEAD"), task)


def _validate_explicit_title(title: str) -> None:
    # `_parse_conventional_subject` judges `splitlines()[0]`, but the whole title becomes
    # the PR title, so a title must be exactly one line by the same definition or an
    # unvalidated suffix rides along behind the validated first line. Ask `splitlines`
    # rather than enumerating separators: it also splits VT, FF, NEL, U+2028, and U+2029.
    if (
        title != title.strip()
        or title.splitlines()[:1] != [title]
        or len(title) > _SUBJECT_LIMIT
        or _parse_conventional_subject(title) is None
    ):
        raise ConfigError(
            f"title must be a Conventional Commit subject no longer than {_SUBJECT_LIMIT} "
            "characters"
        )


@dataclass(frozen=True)
class TaskPRContext:
    what: str
    why: str


def _task_pr_context(task: str) -> TaskPRContext | None:
    """Extract PR context from structured task prose without leaking later sections."""
    sections = re.split(r"(?m)^\s*##\s+([^\n]+?)\s*$", task.strip())
    parsed = {
        name.strip().casefold(): content.strip()
        for name, content in zip(sections[1::2], sections[2::2], strict=True)
    }
    what = parsed.get(TASK_REQUIRED_SECTIONS[0].casefold(), "")
    why = parsed.get(TASK_REQUIRED_SECTIONS[1].casefold(), "")
    return TaskPRContext(what, why) if what and why else None


def _default_body(persona: str, task: str, report: Report | None) -> str:
    context = _task_pr_context(task)
    if context is not None:
        return f"## What\n{context.what}\n\n## Why\n{context.why}\n"
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


def _step_commit_message(step: Step, worktree: Path, dispatch_head: str) -> str:
    return (
        f"{_default_title(worktree, dispatch_head, step.task)}\n\n"
        f"Workstream step {step.id} (persona: {step.persona}), dispatched by ai-orchestrator."
    )


def _incomplete_commit_message(step: Step, pr_base: str) -> str:
    subject = _format_conventional_subject(
        _ParsedSubject("chore", None, False, f"{_task_description(step.task)} (incomplete step)")
    )
    return (
        f"{subject}\n\n"
        f"{format_preserved_step_metadata(step.id, step.persona)}, preserved by "
        "ai-orchestrator after the dispatch did not complete.\n\n"
        f"{INCOMPLETE_TRAILER}\n"
        f"{PR_BASE_TRAILER} {pr_base}"
    )


def _preserve_failed_retry(
    result: LifecycleResult,
    *,
    worktree: Path,
    remote_base: str,
    root_base: str,
    pr_base: str,
    lead: Step,
) -> None:
    """Record committed failed work so the next lifecycle retry resumes it."""
    if result.outcome != "sync-conflict" or result.resume is not None:
        return
    if not gitops.has_commits_ahead(worktree, remote_base):
        return
    if gitops.unmerged_paths(worktree):
        gitops.merge_abort(worktree)
    if gitops.is_dirty(worktree):
        return
    if not incomplete_commits(worktree, remote_base, "HEAD"):
        gitops.commit_empty(worktree, _incomplete_commit_message(lead, pr_base))
    result.resume = Resume(
        branch=result.branch,
        base_branch=root_base,
        pr_base=pr_base,
        checkpoint=gitops.head_sha(worktree),
        completed_steps=tuple(step.id for step in result.steps if step.status == "done"),
        mode="retry",
    )


def _workstream_branch_name(steps: list[Step]) -> str:
    lead = steps[0]
    key = "\x00".join(f"{_step_label(s)}:{s.task}" for s in steps)
    return f"ai-orchestrator/{_step_label(lead)}/{_short_hash(key)}-{uuid.uuid4().hex[:10]}"


def _workstream_body(steps: list[Step], results: list[StepResult]) -> str:
    if len(steps) == 1:
        report = results[0].report if results else None
        return _default_body(_step_label(steps[0]), steps[0].task, report)
    contexts = [_task_pr_context(step.task) for step in steps]
    if any(context is not None for context in contexts):
        fallback = {
            step.id: (
                f"**{step.id}** (`{_step_label(step)}`): "
                f"{step.task.strip().splitlines()[0] if step.task.strip() else step.id}"
            )
            for step, context in zip(steps, contexts, strict=True)
            if context is None
        }
        what = [
            context.what if context is not None else fallback[step.id]
            for step, context in zip(steps, contexts, strict=True)
        ]
        why = [
            context.why if context is not None else fallback[step.id]
            for step, context in zip(steps, contexts, strict=True)
        ]
        lines = ["## What", *[f"- {item}" for item in what], "", "## Why"]
        lines.extend(f"- {item}" for item in why)
        lines.append("")
        return "\n".join(lines)

    lines = ["## What", "This PR bundles an ordered workstream of subtasks:", ""]
    for step in steps:
        first = step.task.strip().splitlines()[0] if step.task.strip() else step.id
        lines.append(f"- **{step.id}** (`{_step_label(step)}`): {first}")
    lines += [
        "",
        "## Why",
        "Dispatched by ai-orchestrator as one PR: each step ran in the same branch, "
        "committing in turn, and the change was verified locally before merge.",
        "",
    ]
    return "\n".join(lines)


def _drafting_task(output_path: Path, remote_base: str, steps: list[Step]) -> str:
    def task_context(step: Step) -> str:
        structured = _task_pr_context(step.task)
        if structured is None:
            return "Legacy task without structured What/Why"
        return f"## What\n{structured.what}\n\n## Why\n{structured.why}"

    context = "\n\n".join(
        f"Persona: {_step_label(step)}\n"
        f"Task What/Why (the PR Why must come from this task Why):\n"
        f"{task_context(step)}\n"
        f"Original task context:\n{step.task.strip()}"
        for step in steps
    )
    what, why = PR_REQUIRED_SECTIONS
    additional = PR_OPTIONAL_SECTIONS[0]
    return f"""Draft the pull request body for the completed change in this worktree.

Read the actual change with `git diff {remote_base}...HEAD` and use this context only to
understand its driver:

{context}

Write the final body, and nothing else, to this absolute path:
{output_path}

Follow `.github/pull_request_template.md`: include a terse, high-level `## {what}` describing
what changed and its impact, not a walkthrough of individual diff hunks. The `## {why}` must
state the user-facing motivation from the task's `## Why`: its impact and decision driver.
Never substitute an orchestration handoff (dispatch details, agent-turn counts, or local
verification) for that Why. Add `## {additional}` only when useful. Never paste the original
task prose verbatim.
Do not modify, stage, or commit any file in the worktree.
"""


def _valid_drafted_body(body: str) -> bool:
    """Validate the small template contract at the agent-output boundary."""
    sections = re.split(r"(?m)^## ([^\n]+)\s*$", body.strip())
    if sections[0].strip():
        return False
    names = sections[1::2]
    content = sections[2::2]
    allowed_sequences = (
        PR_REQUIRED_SECTIONS,
        (*PR_REQUIRED_SECTIONS, *PR_OPTIONAL_SECTIONS),
    )
    return tuple(names) in allowed_sequences and all(section.strip() for section in content)


def _draft_pr_body(
    *,
    worktree: Path,
    remote_base: str,
    steps: list[Step],
    fallback: str,
    dispatch_fn: DispatchFn,
    oneharness_mode: str | None,
    base_path: str | Path,
    persona_dir: str | Path,
    journal: NodeSink,
    dispatch_env: dict[str, str],
    use_llmlint_wrapper: bool = True,
) -> str:
    """Draft a diff-derived body, falling back without blocking publication."""
    journal.append("pr-drafting-started", detail={"base": remote_base})
    try:
        with tempfile.TemporaryDirectory(prefix="ai-orchestrator-pr-body-") as temp_dir:
            output_path = Path(temp_dir) / "body.md"
            report = dispatch_fn(
                "pr-author",
                _drafting_task(output_path, remote_base, steps),
                project_dir=str(worktree),
                oneharness_mode=oneharness_mode,
                use_llmlint_wrapper=use_llmlint_wrapper,
                base_path=base_path,
                persona_dir=persona_dir,
                session="pr-author",
                labels=journal.labels,
                env=dispatch_env,
            )
            drafted = (
                output_path.read_text(encoding="utf-8").strip() if output_path.is_file() else ""
            )
            if report.completed and _valid_drafted_body(drafted):
                journal.append(
                    "pr-drafting-finished",
                    detail={"completed": True, "body_length": len(drafted)},
                )
                return drafted + "\n"
            reason = "invalid or empty body" if report.completed else "dispatch did not complete"
    except Exception as exc:  # Drafting is best-effort and must never block publication.
        reason = f"drafting error: {exc}"
    journal.append("pr-drafting-finished", detail={"completed": False, "reason": reason})
    journal.append("pr-drafting-fallback", detail={"reason": reason})
    return fallback


def _should_draft_pr_body(_title: str | None, body: str | None) -> bool:
    """An explicit body opts out of automatic PR-body authorship; a title does not."""
    return body is None


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


def _pr_from_url(url: str, *, head: str, base: str) -> PullRequest | None:
    """Rebuild a stable PR identity from a recorded URL."""
    matched = _PR_URL.fullmatch(url)
    if matched is None:
        return None
    return PullRequest(int(matched.group(2)), url, matched.group(1), head, base)


def _stack_pr(anchor: StackBase) -> PullRequest | None:
    """Rebuild the stable PR identity carried by a cross-round anchor."""
    if anchor.pr is None:
        return None
    return _pr_from_url(
        anchor.pr, head=anchor.branch, base=anchor.pr_base or anchor.base_branch or ""
    )


def _ref_exists(cwd: Path, ref: str) -> bool:
    try:
        gitops.ref_sha(cwd, ref)
    except GitError:
        return False
    return True


@dataclass(frozen=True)
class ResumePrep:
    """Validated continuation point for a paused workstream."""

    worktree_base: str
    published: bool


def _validate_resume(clone: Path, resume: Resume, github: GitHubBackend | None) -> ResumePrep | str:
    """Validate that a recorded pause still describes reachable branch history."""
    published = _ref_exists(clone, f"origin/{resume.branch}")
    local = gitops.branch_exists(clone, resume.branch)
    if not published and not local:
        return (
            f"resume-failed: branch {resume.branch!r} no longer exists locally or on origin; "
            "the paused workstream's commits are unreachable"
        )
    if not _ref_exists(clone, resume.checkpoint):
        return (
            f"resume-failed: recorded checkpoint {resume.checkpoint} is missing from the "
            f"repository; branch {resume.branch!r} was rewritten"
        )
    tip = f"origin/{resume.branch}" if published else resume.branch
    if not gitops.is_ancestor(clone, resume.checkpoint, tip):
        return (
            f"resume-failed: branch {resume.branch!r} was rewritten; recorded checkpoint "
            f"{resume.checkpoint} is no longer in the history of {tip}"
        )
    if resume.mode == "retry" and not unattested_incomplete(clone, f"origin/{resume.pr_base}", tip):
        return (
            f"resume-failed: branch {resume.branch!r} does not carry valid unattested "
            "incomplete provenance; retry will use a fresh branch"
        )
    if published and local and not gitops.is_ancestor(clone, resume.branch, tip):
        return (
            f"resume-failed: local branch {resume.branch!r} has unpublished or divergent "
            f"commits and cannot be fast-forwarded safely to {tip}"
        )
    if resume.pr is not None:
        pr = _pr_from_url(resume.pr, head=resume.branch, base=resume.pr_base)
        if pr is None:
            return f"resume-failed: recorded draft {resume.pr!r} is not a GitHub pull-request URL"
        status = (github or CliGitHubBackend()).status(pr)
        if status.merged:
            return (
                f"resume-failed: draft PR {resume.pr} merged before the human-gated "
                "workstream completed"
            )
        if status.state == "CLOSED":
            return (
                f"resume-failed: draft PR {resume.pr} was closed without merging; reopen it or "
                "drop the node before continuing"
            )
        if not status.draft:
            return (
                f"resume-failed: recorded draft PR {resume.pr} is ready for review before the "
                "human-gated workstream completed; convert it back to draft or drop the node"
            )
    return ResumePrep(worktree_base=tip, published=published)


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


@dataclass
class StepRun:
    """Aggregate step-run outcome plus every step's recorded result."""

    status: str  # done | not-completed | waiting
    results: list[StepResult]
    detail: str = ""
    waiting: list[str] = field(default_factory=list)


# Lifecycle work commonly includes repository orientation, implementation, and a
# complete gate in one agent step. The lifecycle-specific budget gives each
# segment room for those phases while bounded continuations prevent a permanently
# stuck agent from looping forever. Direct dispatches retain the lean shared default
# from onejudge.base.yaml, and an explicit per-step max_turns still wins.
DEFAULT_LIFECYCLE_STEP_MAX_TURNS = 24
MAX_AUTOMATIC_STEP_RESUMES = 2
MAX_MERGE_CONFLICT_RESOLUTIONS = 2


def persist_report_artifacts(journal: NodeSink, report: Report, *, session: str) -> None:
    """Persist a dispatch's raw report and stable oneharness correlation pointer."""
    directory = journal.artifact_dir
    labels = journal.labels
    if directory is None:
        return
    report_path = (directory / "worker-report.json").resolve()
    session_path = (directory / "oneharness-session.json").resolve()
    atomic_json(report_path, report.raw or {})
    run_id = labels.get("run_id")
    round_value = labels.get("round")
    node = labels.get("node")
    typed_id = None
    if run_id and round_value and node:
        typed_id = str(GraphId(run_id, int(round_value), node))
    atomic_json(
        session_path,
        {
            "session": session,
            "typed_id": typed_id,
            "labels": dict(labels),
        },
    )
    report.artifacts["worker_report"] = str(report_path)
    report.artifacts["oneharness_session"] = str(session_path)


def _run_steps(
    steps: list[Step],
    *,
    worktree: Path,
    branch: str,
    pr_base: str,
    dispatch_fn: DispatchFn,
    oneharness_mode: str | None,
    use_llmlint_wrapper: bool,
    base_path: str | Path,
    persona_dir: str | Path,
    journal: NodeSink,
    dispatch_env: dict[str, str],
    extra_instructions: str | None = None,
    completed: frozenset[str] = frozenset(),
    cancel: threading.Event | None = None,
) -> StepRun:
    """Run a step sub-DAG in the shared worktree, committing per step.

    Execution is **serialized in topological order** (concurrency 1): the steps
    share one working tree, so running two dispatches into it at once would corrupt
    it. A step that does not complete fails the workstream and skips dependents.
    A ``human`` step pauses the workstream and blocks its dependents.
    """
    by_id = {s.id: s for s in steps}
    deps = {s.id: s.deps for s in steps}
    reports: dict[str, Report] = {}

    def run_step(sid: str) -> NodeRun:
        step = by_id[sid]
        log = journal.for_step(StepId(sid))
        if sid in completed:
            # Attested in an earlier round; it settles without running again.
            log.append("step-settled", detail={"status": "done", "step_kind": step.kind})
            return NodeRun("done", None, None)
        if step.human:
            log.append("human-waiting", detail={"step_kind": step.kind})
            return NodeRun("waiting", f"step {sid!r} is awaiting human action", None)
        if step.expects_no_diff:
            log.append(
                "step-settled",
                detail={"status": "done", "step_kind": step.kind, "outcome": "no-changes"},
            )
            return NodeRun("done", None, None)
        log.append("step-started", detail={"step_kind": step.kind, "persona": step.persona})
        dispatch_head = gitops.head_sha(worktree)
        report = dispatch_fn(
            cast(str, step.persona),
            step.task,
            project_dir=str(worktree),
            oneharness_mode=oneharness_mode,
            use_llmlint_wrapper=use_llmlint_wrapper,
            base_path=base_path,
            persona_dir=persona_dir,
            session=f"{branch}:{sid}",
            max_turns=step.max_turns or DEFAULT_LIFECYCLE_STEP_MAX_TURNS,
            done_when=step.done_when,
            extra_instructions=extra_instructions,
            labels=log.labels,
            env=dispatch_env,
            cancel=cancel,
        )
        persist_report_artifacts(log, report, session=f"{branch}:{sid}")
        reports[sid] = report
        if not report.completed:
            # llmlint: ignore[changed_behavior_has_e2e] The live orchestrator e2e kills this
            # lifecycle worker and proves worker-died enters not-completed retry/exhaustion.
            # Existing real-git lifecycle journeys cover this same shared preservation branch
            # with dirty and agent-committed partial work; duplicating paid-agent authoring
            # inside the kill journey would replace an additional layer under test.
            failure = report.outcome or "hit the turn cap"
            preserved = False
            if gitops.is_dirty(worktree):
                gitops.add_all(worktree)
                gitops.commit(worktree, _incomplete_commit_message(step, pr_base))
                preserved = True
            else:
                dispatch_committed = gitops.head_sha(worktree) != dispatch_head
                ahead_of_pr_base = gitops.has_commits_ahead(worktree, f"origin/{pr_base}")
                already_marked = bool(incomplete_commits(worktree, dispatch_head, "HEAD"))
                if dispatch_committed and ahead_of_pr_base and not already_marked:
                    gitops.commit_empty(worktree, _incomplete_commit_message(step, pr_base))
                    preserved = True
                elif dispatch_committed and ahead_of_pr_base:
                    preserved = True
            log.append(
                "step-settled",
                detail={
                    "status": "not-completed",
                    "step_kind": step.kind,
                    "turns": report.assistant_turns,
                    "preserved": preserved,
                    **({"outcome": report.outcome} if report.outcome else {}),
                },
            )
            if preserved:
                return NodeRun(
                    "failed",
                    f"step {sid!r} {failure}; partial work was committed to branch {branch!r}",
                    report,
                )
            return NodeRun("failed", f"step {sid!r} {failure}", report)
        if gitops.is_dirty(worktree):
            gitops.add_all(worktree)
            gitops.commit(worktree, _step_commit_message(step, worktree, dispatch_head))
        log.append(
            "step-settled",
            detail={"status": "done", "step_kind": step.kind, "turns": report.assistant_turns},
        )
        return NodeRun("done", None, report)

    runs, _order = schedule_dag(list(by_id), deps, run_step, concurrency=1)
    status_map = {"failed": "not-completed"}
    results = [
        StepResult(
            id=sid,
            persona=by_id[sid].persona,
            kind=by_id[sid].kind,
            status=(
                status_map.get(runs[sid].status, runs[sid].status) if sid in runs else "skipped"
            ),
            report=reports.get(sid),
        )
        for sid in by_id
    ]
    waiting = [r.id for r in results if r.status == "waiting"]
    unfinished = [r for r in results if r.status in ("not-completed", "skipped")]
    if unfinished:
        bad = unfinished[0]
        return StepRun(
            status="not-completed",
            results=results,
            detail=runs[bad.id].error or f"step {bad.id!r} {bad.status}",
            waiting=waiting,
        )
    if waiting:
        return StepRun(
            status="waiting",
            results=results,
            detail="awaiting human action on " + ", ".join(repr(sid) for sid in waiting),
            waiting=waiting,
        )
    return StepRun(status="done", results=results)


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
    *,
    result: LifecycleResult,
    journal: NodeSink,
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
        workspace.mirror_branch(ref, branch)
        pushed = True
        return SyntheticStackBase(branch)
    finally:
        _best_effort_cleanup(
            result,
            journal,
            operation="remove-worktree",
            target=str(worktree),
            cleanup=lambda: workspace.remove_worktree(ref, worktree),
        )
        if not pushed:
            # llmlint: ignore[changed_behavior_has_e2e] The real-git synthetic-stack
            # conflict e2e exercises both deferred teardown operations on this path.
            _best_effort_cleanup(
                result,
                journal,
                operation="delete-branch",
                target=branch,
                cleanup=lambda: workspace.delete_branch(ref, branch),
            )


def _best_effort_cleanup(
    result: LifecycleResult,
    journal: NodeSink,
    *,
    operation: str,
    target: str,
    cleanup: Callable[[], None],
) -> None:
    """Record teardown failures without changing the lifecycle's real outcome."""
    try:
        cleanup()
    except (LockTimeout, WorkspaceError, GitError) as exc:
        detail = f"{operation} deferred for {target}: {exc}"
        result.deferred_cleanup.append(detail)
        journal.append(
            "cleanup-deferred",
            detail={"operation": operation, "target": target, "error": str(exc)},
        )


def _push_failure(exc: GitError, *, branch: str) -> MergeOutcome:
    """Turn a hook/transport rejection into a durable lifecycle outcome."""
    detail = str(exc)
    outcome = classify_push_failure(exc)
    return MergeOutcome(
        outcome,
        (
            f"repository pre-push gate rejected publication of {branch!r}: {detail}"
            if outcome == "gate-failed"
            else f"push of {branch!r} failed before publication completed: {detail}"
        ),
    )


def _pause_at_human_step(
    result: LifecycleResult,
    step_run: StepRun,
    *,
    worktree: Path,
    branch: str,
    root_base: str,
    pr_base: str,
    steps: list[Step],
    workflow: Workflow,
    github: GitHubBackend | None,
    title: str | None,
    body: str | None,
    applicable_stack: list[StackBase],
    recorded_pr: str | None,
    journal: NodeSink,
    cache_env: dict[str, str],
    dispatch_fn: DispatchFn,
    oneharness_mode: str | None,
    use_llmlint_wrapper: bool,
    base_path: str | Path,
    persona_dir: str | Path,
) -> LifecycleResult:
    """Preserve a human-gated workstream and record how to continue it."""
    lead = steps[0]
    result.waiting_steps = list(step_run.waiting)
    result.outcome = _WAITING_OUTCOME
    result.detail = f"workstream paused: {step_run.detail}"
    completed = tuple(r.id for r in step_run.results if r.status == "done")

    def pause(checkpoint: str, pr_url: str | None) -> LifecycleResult:
        result.resume = Resume(
            branch=branch,
            base_branch=root_base,
            pr_base=pr_base,
            checkpoint=checkpoint,
            completed_steps=completed,
            pr=pr_url,
        )
        return result

    remote_base = f"origin/{pr_base}"
    with advisory_lock(f"git:{gitops.common_dir(worktree)}"):
        gitops.fetch(worktree)
    checkpoint = gitops.head_sha(worktree)
    if workflow == "local" or not gitops.has_commits_ahead(worktree, remote_base):
        return pause(checkpoint, recorded_pr)

    if not gitops.merge_base_into_branch(
        worktree, remote_base, message=f"Merge {remote_base} into {branch}"
    ):
        result.outcome = "gate-failed"
        result.detail = (
            f"sync-conflict: could not merge current {remote_base} into {branch}; merge aborted "
            "and no draft was published"
        )
        return result
    checkpoint = gitops.head_sha(worktree)
    try:
        gitops.push(worktree, branch)
    except GitError as exc:
        failed = _push_failure(exc, branch=branch)
        result.outcome = failed.outcome
        result.detail = failed.detail
        return result
    reused = _pr_from_url(recorded_pr, head=branch, base=pr_base) if recorded_pr else None
    if reused is not None:
        pr = reused
    else:
        fallback_body = _workstream_body(steps, step_run.results)
        pr_body = body or fallback_body
        if workflow == "remote" and _should_draft_pr_body(title, body):
            pr_body = _draft_pr_body(
                worktree=worktree,
                remote_base=remote_base,
                steps=steps,
                fallback=fallback_body,
                dispatch_fn=dispatch_fn,
                oneharness_mode=oneharness_mode,
                use_llmlint_wrapper=use_llmlint_wrapper,
                base_path=base_path,
                persona_dir=persona_dir,
                journal=journal,
                dispatch_env=cache_env,
            )
        pr = (github or CliGitHubBackend()).create_pr(
            result.repo,
            head=branch,
            base=pr_base,
            title=title or _default_title(worktree, remote_base, lead.task),
            body=pr_body + _stack_body(applicable_stack, result.synthetic_stack_base),
            draft=True,
        )
        # Only the branch that actually opens one records it; resuming reuses the
        # draft an earlier round already journaled.
        journal.append(
            "pr-created",
            detail={
                "repo": result.repo,
                "pr": pr.url,
                "number": pr.number,
                "base": pr_base,
                "draft": True,
            },
        )
    result.pr = pr
    return pause(checkpoint, pr.url)


@scratch_dispatch_guarded
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
    recorded_gate: list[str] | None = None,
    verify_via_ci: bool = False,
    merge_policy: MergePolicy | None = None,
    merge_method: str = "squash",
    oneharness_mode: str | None = "bypass",
    dispatch_fn: DispatchFn = dispatch,
    base_path: str | Path = BASE_CONFIG,
    persona_dir: str | Path = PERSONA_DIR,
    max_turns: int | None = None,
    done_when: str | None = None,
    publication_attempts: int = 3,
    poll_interval: float = 15.0,
    timeout: float = 3600.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    cleanup: bool = True,
    stack_bases: list[StackBase] | None = None,
    resume: Resume | None = None,
    journal: NodeSink | None = None,
    cancel: threading.Event | None = None,
) -> LifecycleResult:
    """Take one subtask from a fresh branch to a merged change on ``repo``.

    The merge step is a `MergeStrategy`: for a GitHub repo, open a PR and let
    GitHub auto-merge on green required checks; for a **local** repo (a path), a
    direct merge into the base branch after the checks pass. It is selected only
    from affirmative ``workflow=local`` metadata (or an explicit ``merge``
    strategy); ``github`` supplies the backend for the remote path.

    ``oneharness_mode`` defaults to ``"bypass"`` (no approvals, no inner sandbox):
    the container is the sandbox, so this is the "complete tasks without approvals"
    mode. The agent runs in an isolated worktree; the change is verified by the
    repository's own merge path — its ``pre-push`` hook, or its required PR status
    checks — which dispatch refuses to start without. It never merges off a
    non-required check.

    Pass ``steps`` (a sub-DAG of `Step`s) to run **several onejudge on one PR**: they
    share the branch/worktree, run in dependency order committing in turn, and the
    result is published and merged **once**. A single ``(persona, task)`` is the
    one-step case.

    ``journal`` is this node's scope in a tracked round: transitions are recorded
    against it as they happen, and every dispatch it makes is labelled with the
    same coordinates. It defaults to a no-op, because a bare ``just repo-task``
    belongs to no graph — and because observation must never decide an outcome.
    """
    log: NodeSink = journal if journal is not None else NullNodeJournal()
    require_scratch_capacity()
    effective_steps = steps or (
        [Step("main", persona, task, max_turns=max_turns, done_when=done_when)]
        if persona and task
        else []
    )
    if not effective_steps:
        raise ConfigError("run_repo_task needs either (persona, task) or a non-empty steps list")
    lead = effective_steps[0]

    ref = workspace.repo_ref(repo)
    result = LifecycleResult(
        repo=ref.slug,
        task=lead.task,
        persona=lead.persona or lead.kind,
        base_branch=base_branch or (resume.base_branch if resume else ""),
        branch=resume.branch if resume else (branch or _workstream_branch_name(effective_steps)),
        outcome="error",
    )
    worktree: Path | None = None
    try:
        if title is not None:
            _validate_explicit_title(title)
        _validate_lifecycle_branch(result.branch, field_name="branch")
        if base_branch is not None:
            _validate_lifecycle_branch(base_branch, field_name="base_branch")
        _validate_runtime_stack_bases(stack_bases or [])
        clone = workspace.ensure_clone(
            ref,
            url=url,
            base_branch=base_branch or (resume.base_branch if resume else None),
            execution_checkout=execution_checkout,
            repo_type=repo_type,
        )
        selection = workspace.selection(ref)
        use_llmlint_wrapper = (
            str(selection.publication_identity).casefold() == AI_ORCHESTRATOR_IDENTITY
        )
        cache_env = {CACHE_ENV: str(workspace.ensure_cache_dir(ref))}
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
        ci_backend = github or (CliGitHubBackend() if verify_via_ci else None)
        if verify_via_ci and (
            decision.workflow != "remote"
            or ci_backend is None
            or not ci_backend.supports_ci(ref.slug)
        ):
            raise ConfigError(
                "--verify-via-ci requires a remote GitHub/PR workflow with a GitHub origin; "
                "register or migrate this repository to a remote workflow before dispatch"
            )
        # The lifecycle no longer runs the repo's gate itself, so the merge path must.
        # Inspect the *execution* checkout: every publishing push (the branch push and
        # the local strategy's detached publication push) originates in a worktree of
        # it, and Git resolves hooks through the shared common dir, so its `pre-push`
        # is the hook that will actually run.
        coverage = merge_gate_coverage(
            selection.publication_identity,
            selection.execution_checkout,
            workflow=decision.workflow,
            github=github,
        )
        if not coverage.meets_coverage_criteria:
            raise RegistryError(
                f"lifecycle dispatch refused for identity {coverage.identity}: "
                f"{coverage.coverage_gap}; run 'just repos --audit-gate-coverage' "
                "and repair the merge-path gate before dispatch"
            )
        result.execution_checkout = str(selection.execution_checkout)
        result.publication_checkout = str(selection.publication_checkout)
        result.publication_identity = selection.publication_identity
        result.publication_workflow = decision.workflow
        result.repository_type = effective_type
        result.merge_policy = decision.merge_policy
        strategy = _select_merge_strategy(ref, merge, github, decision.workflow)
        local_publication = isinstance(strategy, LocalMergeStrategy)
        root_base = (
            base_branch or (resume.base_branch if resume else None) or gitops.default_branch(clone)
        )
        if resume is not None and root_base != resume.base_branch:
            result.outcome = "resume-failed"
            result.detail = (
                f"resume-failed: requested base {root_base!r} does not match recorded "
                f"base {resume.base_branch!r}"
            )
            return result
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
        if resume is not None:
            pr_base = resume.pr_base
        elif len(applicable_stack) > 1:
            stack_result = _build_synthetic_stack_base(
                ref,
                workspace,
                root_base,
                applicable_stack,
                result=result,
                journal=log,
            )
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
        gate_template = selection.gate
        if recorded_gate is not None:
            resolved_recorded_gate = recorded_gate
        elif gate_template == NOOP_GATE:
            resolved_recorded_gate = None
        elif gate_template is not None:
            resolved_recorded_gate = resolve_gate_template(gate_template, f"origin/{pr_base}")
        elif verify_via_ci:
            resolved_recorded_gate = None
        else:
            raise ConfigError(
                "no verification gate is configured; register or migrate the identity gate"
            )
        # The merge path is the only verifier now, so record what dispatch saw of it.
        # A hook rejection arrives late, as `git push` output; this record is what lets
        # a reader tell "the gate ran and failed" from "nothing was ever going to run",
        # without re-deriving the identity's coverage after the fact.
        log.append(
            "merge-gate-coverage",
            detail={
                "identity": str(coverage.identity),
                "checkout": str(selection.execution_checkout),
                "pre_push_hook": coverage.hook or "",
                "required_checks": list(coverage.required_checks),
                "required_checks_status": coverage.github_status,
                "expected_gate": list(resolved_recorded_gate or []),
            },
        )
        branch = result.branch
        worktree_base = f"origin/{pr_base}"
        prepared: ResumePrep | None = None
        if resume is not None:
            workspace.adopt_preserved_branch(ref, resume.branch)
            validated = _validate_resume(clone, resume, github)
            if isinstance(validated, str):
                invalid_provenance = "valid unattested incomplete provenance" in validated
                if resume.mode != "retry" or not invalid_provenance:
                    result.outcome = "resume-failed"
                    result.detail = validated
                    return result
                abandoned = resume
                resume = None
                result.branch = branch = _workstream_branch_name(effective_steps)
                result.retry_lineage = RetryLineage(
                    abandoned.branch,
                    abandoned.checkpoint,
                    "abandoned",
                    validated,
                    abandoned.source_round,
                )
            else:
                prepared = validated
                worktree_base = prepared.worktree_base
                if resume.mode == "retry":
                    result.retry_lineage = RetryLineage(
                        resume.branch,
                        resume.checkpoint,
                        "reused",
                        supersedes_round=resume.source_round,
                    )
        worktree = workspace.worktree(ref, branch, base=worktree_base)
        if resume is not None and prepared is not None and prepared.published:
            try:
                gitops.merge_ff_only(worktree, f"origin/{branch}")
            except GitError as exc:
                result.outcome = "resume-failed"
                result.detail = (
                    f"resume-failed: local branch {branch!r} diverged from origin and cannot be "
                    f"fast-forwarded: {exc}"
                )
                return result
        log.append(
            "branch-discovered",
            detail={
                "repo": ref.slug,
                "branch": branch,
                "base_branch": root_base,
                "pr_base": pr_base,
                "synthetic_stack_base": result.synthetic_stack_base,
                "resumed": resume is not None,
            },
        )
        remote_base = f"origin/{pr_base}"

        def preserve_cancelled(stage: str) -> bool:
            """Durably hand off completed partial work at cooperative checkpoints."""
            if cancel is None or not cancel.is_set():
                return False
            if gitops.is_dirty(worktree):
                gitops.add_all(worktree)
                gitops.commit(worktree, _incomplete_commit_message(lead, pr_base))
            elif gitops.has_commits_ahead(worktree, remote_base) and not incomplete_commits(
                worktree, remote_base, "HEAD"
            ):
                gitops.commit_empty(worktree, _incomplete_commit_message(lead, pr_base))
            if gitops.has_commits_ahead(worktree, remote_base):
                result.resume = Resume(
                    branch=branch,
                    base_branch=root_base,
                    pr_base=pr_base,
                    checkpoint=gitops.head_sha(worktree),
                    completed_steps=tuple(
                        step.id for step in result.steps if step.status == "done"
                    ),
                    mode="retry",
                )
            result.outcome = "not-completed"
            result.detail = f"cancelled cooperatively {stage}; partial work preserved"
            return True

        completed_step_ids = set(resume.completed_steps) if resume else set()
        prior_step_results: dict[str, StepResult] = {}
        automatic_resumes = 0
        workstream_start_head = gitops.head_sha(worktree)
        while True:
            step_run = _run_steps(
                effective_steps,
                worktree=worktree,
                branch=branch,
                pr_base=pr_base,
                dispatch_fn=dispatch_fn,
                oneharness_mode=oneharness_mode,
                use_llmlint_wrapper=use_llmlint_wrapper,
                base_path=base_path,
                persona_dir=persona_dir,
                journal=log,
                dispatch_env=cache_env,
                extra_instructions=CI_ITERATION_INSTRUCTIONS if verify_via_ci else None,
                completed=frozenset(completed_step_ids),
                cancel=cancel,
            )
            for step_result in step_run.results:
                previous = prior_step_results.get(step_result.id)
                if step_result.report is None and previous is not None:
                    step_result.report = previous.report
                prior_step_results[step_result.id] = step_result
                if step_result.status == "done":
                    completed_step_ids.add(step_result.id)
            if (
                step_run.status != "not-completed"
                or cancel is not None
                and cancel.is_set()
                or not incomplete_commits(worktree, remote_base, "HEAD")
                or automatic_resumes >= MAX_AUTOMATIC_STEP_RESUMES
            ):
                break
            if result.retry_lineage is None:
                result.retry_lineage = RetryLineage(
                    branch,
                    gitops.head_sha(worktree),
                    "reused",
                )
            automatic_resumes += 1
        step_run.results = [prior_step_results[step.id] for step in effective_steps]
        result.steps = step_run.results
        result.report = next(
            (r.report for r in reversed(step_run.results) if r.status == "done" and r.report), None
        )
        if step_run.status == "not-completed":
            result.outcome = "not-completed"
            prefix = "cancelled cooperatively" if cancel is not None and cancel.is_set() else None
            result.detail = (
                f"{prefix}; {step_run.detail}"
                if prefix is not None
                else f"workstream did not complete: {step_run.detail}"
            )
            if incomplete_commits(worktree, remote_base, "HEAD"):
                result.resume = Resume(
                    branch=branch,
                    base_branch=root_base,
                    pr_base=pr_base,
                    checkpoint=gitops.head_sha(worktree),
                    completed_steps=tuple(
                        step.id for step in step_run.results if step.status == "done"
                    ),
                    mode="retry",
                )
            return result
        if step_run.status == "waiting":
            paused = _pause_at_human_step(
                result,
                step_run,
                worktree=worktree,
                branch=branch,
                root_base=root_base,
                pr_base=pr_base,
                steps=effective_steps,
                workflow=decision.workflow,
                github=github,
                title=title,
                body=body,
                applicable_stack=applicable_stack,
                recorded_pr=resume.pr if resume else None,
                journal=log,
                cache_env=cache_env,
                dispatch_fn=dispatch_fn,
                oneharness_mode=oneharness_mode,
                use_llmlint_wrapper=use_llmlint_wrapper,
                base_path=base_path,
                persona_dir=persona_dir,
            )
            return paused
        if step_run.status != "done":
            result.outcome = "error"
            result.detail = f"unexpected step status: {step_run.status}"
            return result

        if preserve_cancelled("after dispatch"):
            return result

        if not local_publication:
            with advisory_lock(f"git:{gitops.common_dir(worktree)}"):
                gitops.fetch(worktree)
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

        if preserve_cancelled("before publication"):
            return result

        if not local_publication and (
            result.retry_lineage and result.retry_lineage.disposition == "reused"
        ):
            missing = sorted(unattested_incomplete(worktree, remote_base, "HEAD"))
            if missing:
                trailers = "\n".join(f"{RECOVERY_TRAILER} {sha}" for sha in missing)
                gitops.commit_empty(
                    worktree,
                    "chore: attest verified recovery of preserved work\n\n" + trailers,
                )
                result.retry_lineage.disposition = "recovered"
        if (
            not local_publication
            and result.retry_lineage
            and unattested_incomplete(worktree, remote_base, "HEAD")
        ):
            result.outcome = "not-completed"
            result.detail = (
                "preserved retry completed but cannot be recovered without a successful "
                "complete gate; retry with the repository gate enabled"
            )
            return result

        # Each step commits its own work in _run_steps, so the worktree is clean
        # here; if no step produced a commit, there is nothing to open a PR for.
        if not gitops.has_commits_ahead(worktree, remote_base):
            if (
                local_publication
                and gitops.head_sha(worktree) != workstream_start_head
                and gitops.is_ancestor(worktree, "HEAD", remote_base)
            ):
                result.outcome = ALREADY_INTEGRATED_OUTCOME
                result.detail = (
                    f"verified worktree HEAD was already present on {pr_base}; "
                    "no publication commit was needed"
                )
                log.append(
                    "publication-finished",
                    detail={
                        "branch": branch,
                        "base": pr_base,
                        "outcome": ALREADY_INTEGRATED_OUTCOME,
                    },
                )
                if pr_base == root_base:
                    workspace.fast_forward(ref, root_base)
                return result
            result.outcome = "no-changes"
            result.detail = "agent completed but produced no commits to open a PR"
            return result

        fallback_body = _workstream_body(effective_steps, step_run.results)
        pr_body = body or fallback_body
        if decision.workflow == "remote" and _should_draft_pr_body(title, body):
            pr_body = _draft_pr_body(
                worktree=worktree,
                remote_base=remote_base,
                steps=effective_steps,
                fallback=fallback_body,
                dispatch_fn=dispatch_fn,
                oneharness_mode=oneharness_mode,
                use_llmlint_wrapper=use_llmlint_wrapper,
                base_path=base_path,
                persona_dir=persona_dir,
                journal=log,
                dispatch_env=cache_env,
            )

        # llmlint: ignore[changed_behavior_has_e2e] no blocking external operation exists between
        # the tested post-dispatch checkpoint and this final race-closing check: only local
        # result/metadata construction runs here. Cancellation during the reachable blocking
        # publication stage below is covered with a real delayed git receive hook.
        if preserve_cancelled("before publication"):
            return result
        # Publication is the lifecycle's commit point: cancellation is cooperative
        # only at the checkpoints above. Once push/publication begins, it runs to an
        # authoritative outcome so a late request cannot strand a pushed branch or
        # report already-merged work as discarded.
        if not local_publication:
            # The remote path's gate rejection also arrives here, from the hook that
            # qualified this identity for dispatch, and it must read as a gate
            # failure rather than a raw Git error before any PR exists.
            try:
                gitops.push(worktree, branch)
            except GitError as exc:
                failed = _push_failure(exc, branch=branch)
                result.outcome = failed.outcome
                result.detail = failed.detail
                return result
            workspace.mirror_branch(ref, branch)
        preverified_pr: PullRequest | None = None
        if verify_via_ci:
            backend = cast(GitHubBackend, ci_backend)
            preverified_pr = backend.create_pr(
                ref.slug,
                head=branch,
                base=pr_base,
                title=title or _default_title(worktree, remote_base, lead.task),
                body=pr_body + _stack_body(applicable_stack, result.synthetic_stack_base),
            )
            log.append(
                "pr-created",
                detail={
                    "repo": ref.slug,
                    "pr": preverified_pr.url,
                    "number": preverified_pr.number,
                    "base": pr_base,
                    "draft": False,
                },
            )
            status = backend.status(preverified_pr)
            assessment = assess_blocking_checks(status)
            log.append(
                "pr-checks-observed",
                detail={
                    "repo": ref.slug,
                    "pr": preverified_pr.url,
                    "state": status.state,
                    "merged": status.merged,
                    "merge_state_status": status.merge_state_status,
                    "blocking": [
                        {"name": check.name, "state": check.state} for check in assessment.checks
                    ],
                },
            )
            if not assessment.green:
                result.pr = preverified_pr
                result.outcome = "not-completed"
                qualifier = "settled but not green" if assessment.settled else "not settled"
                result.detail = f"authoritative CI {qualifier}; {assessment.detail}"
                return result

        def synchronize_and_push_local_publication() -> MergeOutcome | None:
            """Synchronize and push while holding this local queue turn.

            The executable pre-push hook required at dispatch runs the complete
            repository gate on this exact branch tree.
            """
            gitops.fetch(worktree)
            if not gitops.merge_base_into_branch(
                worktree,
                remote_base,
                message=f"Merge {remote_base} into {branch}",
                abort_on_conflict=False,
            ):
                return MergeOutcome(
                    MERGE_CONFLICT_RETRY,
                    f"current {remote_base} conflicts with {branch}",
                )
            if preserve_cancelled("before publication"):
                return MergeOutcome(result.outcome, result.detail)
            if result.retry_lineage:
                missing = sorted(unattested_incomplete(worktree, remote_base, "HEAD"))
                if missing:
                    trailers = "\n".join(f"{RECOVERY_TRAILER} {sha}" for sha in missing)
                    gitops.commit_empty(
                        worktree,
                        "chore: attest verified recovery of preserved work\n\n" + trailers,
                    )
                    result.retry_lineage.disposition = "recovered"
            if result.retry_lineage and unattested_incomplete(worktree, remote_base, "HEAD"):
                return MergeOutcome(
                    "not-completed",
                    "preserved retry completed but cannot be recovered without a successful "
                    "complete gate; retry with the repository gate enabled",
                )
            try:
                gitops.push(worktree, branch)
            except GitError as exc:
                return _push_failure(exc, branch=branch)
            workspace.mirror_branch(ref, branch)
            return None

        ctx = MergeContext(
            repo_slug=ref.slug,
            clone_dir=clone,
            queue_identity=git_lock_identity(
                gitops.common_dir(selection.publication_checkout),
            ),
            base=pr_base,
            branch=branch,
            title=title or _default_title(worktree, remote_base, lead.task),
            body=pr_body + _stack_body(applicable_stack, result.synthetic_stack_base),
            method=merge_method,
            policy=decision.merge_policy,
            poll_interval=poll_interval,
            timeout=timeout,
            sleep=sleep,
            clock=clock,
            publication_attempts=publication_attempts,
            repository_type=effective_type,
            journal=log,
            preverified_pr=preverified_pr,
            local_prepare=(synchronize_and_push_local_publication if local_publication else None),
        )
        merge_resolutions = 0
        while True:
            merge_outcome = strategy.publish_and_merge(ctx)
            if merge_outcome.outcome != MERGE_CONFLICT_RETRY:
                break
            if merge_resolutions >= MAX_MERGE_CONFLICT_RESOLUTIONS:
                merge_outcome = MergeOutcome(
                    "sync-conflict",
                    "base conflict remained unresolved after "
                    f"{MAX_MERGE_CONFLICT_RESOLUTIONS} resolve-and-requeue cycles; "
                    f"preserved branch {branch!r} requires manual recovery",
                )
                break
            merge_resolutions += 1
            resolution_task = (
                "## What\n"
                f"Resolve the content conflict between this branch and {remote_base}. "
                "Preserve both the completed branch work and the current base behavior, "
                "then commit the resolution.\n\n"
                "## Why\n"
                "The base advanced after the original work completed; resolving on the "
                "preserved branch salvages that work before it re-enters the merge queue.\n\n"
                "## Acceptance criteria\n"
                "- The branch merges the current base without conflicts.\n"
                "- Existing completed work is preserved.\n"
                "- The repository gate remains green.\n"
            )
            report = dispatch_fn(
                cast(str, lead.persona),
                resolution_task,
                project_dir=str(worktree),
                oneharness_mode=oneharness_mode,
                base_path=base_path,
                persona_dir=persona_dir,
                session=f"{branch}:{lead.id}",
                max_turns=lead.max_turns or DEFAULT_LIFECYCLE_STEP_MAX_TURNS,
                done_when="The conflict is resolved, committed, and the gate is green.",
                labels=log.labels,
                env=cache_env,
                cancel=cancel,
            )
            persist_report_artifacts(
                log,
                report,
                session=f"{branch}:{lead.id}",
            )
            unresolved = gitops.unmerged_paths(worktree)
            if unresolved:
                gitops.merge_abort(worktree)
                if not report.completed:
                    merge_outcome = MergeOutcome(
                        "sync-conflict",
                        "conflict-resolution worker did not complete; preserved branch "
                        f"{branch!r} requires manual recovery",
                    )
                    break
                continue
            if gitops.is_dirty(worktree):
                gitops.add_all(worktree)
                gitops.commit(
                    worktree,
                    f"fix: resolve {pr_base} integration conflict",
                )
            if not report.completed:
                merge_outcome = MergeOutcome(
                    "sync-conflict",
                    "conflict-resolution worker did not complete; preserved branch "
                    f"{branch!r} requires manual recovery",
                )
                break
        if merge_outcome.outcome in {"merged", ALREADY_INTEGRATED_OUTCOME} and pr_base == root_base:
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
        if worktree is not None:
            _preserve_failed_retry(
                result,
                worktree=worktree,
                remote_base=f"origin/{result.pr_base}",
                root_base=result.base_branch,
                pr_base=result.pr_base,
                lead=lead,
            )
        if cleanup and worktree is not None:
            _best_effort_cleanup(
                result,
                log,
                operation="remove-worktree",
                target=str(worktree),
                cleanup=lambda: workspace.remove_worktree(ref, worktree),
            )


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
    recorded_gate: list[str] | None = None
    verify_via_ci: bool | None = None
    merge_policy: MergePolicy | None = None
    workflow: Workflow | None = None
    repo_type: RepositoryType | None = None
    execution_checkout: str | None = None
    max_turns: int | None = None
    done_when: str | None = None
    expects_no_diff: bool = False
    steps: list[Step] | None = None
    stack_bases: list[StackBase] = field(default_factory=list)
    resume: Resume | None = None


class LifecycleRunner(Protocol):
    """How the tracked graph drives one lifecycle node.

    ``journal`` is keyword-only with a default so a caller that has no round to
    record into — `run_repo_plan` reached directly, a test driving one node — calls
    this the same way it always has.
    """

    def __call__(
        self,
        node: RepoPlanNode,
        *,
        journal: NodeSink | None = None,
        cancel: threading.Event | None = None,
    ) -> LifecycleResult: ...


class RepoAliasResolver(Protocol):
    """Registry operations needed to validate lifecycle repository inputs."""

    def repo_ref(self, spec: str) -> RepoRef: ...

    def checkout_path(self, spec: str | Path) -> Path: ...


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


def validate_repo_aliases(node: RepoPlanNode, registry: RepoAliasResolver) -> None:
    """Fail before dispatch when a lifecycle node names an unknown local alias."""
    from .plan import PlanError

    try:
        registry.repo_ref(node.repo)
        if node.execution_checkout is not None:
            registry.checkout_path(node.execution_checkout)
    except RegistryError as exc:
        raise PlanError(f"task {node.id!r}: {exc}") from exc


def parse_repo_node(nid: str, t: dict[str, Any]) -> RepoPlanNode:
    """Validate one lifecycle node into a `RepoPlanNode`."""
    from .plan import PlanError, _expects_no_diff

    if not isinstance(t.get("repo"), str) or not str(t.get("repo")).strip():
        raise PlanError(f"task {nid!r} needs a non-empty 'repo'")
    expects_no_diff = _expects_no_diff(nid, t)
    raw_steps = t.get("steps")
    if expects_no_diff and raw_steps is not None:
        raise PlanError(f"task {nid!r} expects_no_diff cannot also set 'steps'")
    if raw_steps is not None:
        node_steps = _parse_steps(nid, raw_steps)
        persona = task = None
    elif expects_no_diff:
        node_steps = None
        persona = None
        task = t.get("task")
        if not isinstance(task, str) or not task.strip():
            raise PlanError(f"task {nid!r} needs a non-empty 'task'")
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
    raw_title = t.get("title")
    if raw_title is not None:
        if not isinstance(raw_title, str):
            raise PlanError(f"task {nid!r} 'title' must be a Conventional Commit subject")
        try:
            _validate_explicit_title(raw_title)
        except ConfigError as exc:
            raise PlanError(f"task {nid!r} {exc}") from exc
    raw_execution = t.get("execution_checkout")
    if raw_execution is not None and (
        not isinstance(raw_execution, str) or not raw_execution.strip()
    ):
        raise PlanError(f"task {nid!r} 'execution_checkout' must be a non-empty path")
    raw_verify_via_ci = t.get("verify_via_ci")
    if raw_verify_via_ci is not None and not isinstance(raw_verify_via_ci, bool):
        raise PlanError(f"task {nid!r} 'verify_via_ci' must be a boolean")
    resume = _parse_resume(nid, t.get("resume"), node_steps)
    if resume is not None:
        if resume.mode == "pause" and (
            not node_steps or not any(step.human for step in node_steps)
        ):
            raise PlanError(
                f"task {nid!r} pause resume requires a steps workstream with a human step"
            )
        for field_name, recorded in (
            ("branch", resume.branch),
            ("base_branch", resume.base_branch),
        ):
            explicit = t.get(field_name)
            if explicit is not None and explicit != recorded:
                raise PlanError(f"task {nid!r} {field_name!r} conflicts with resume {field_name!r}")
        if resume.pr is not None:
            ref = normalize_repo(t["repo"])
            matched = _PR_URL.fullmatch(resume.pr)
            if (
                not ref.local
                and matched is not None
                and matched.group(1).casefold() != ref.slug.casefold()
            ):
                raise PlanError(f"task {nid!r} resume 'pr' repository does not match 'repo'")
    return RepoPlanNode(
        id=nid,
        repo=t["repo"],
        persona=persona,
        task=task,
        deps=list(deps),
        base_branch=t.get("base_branch"),
        branch=t.get("branch"),
        title=raw_title,
        # `verify_cmd` is the pre-merge-path spelling: it named the command the
        # lifecycle ran, and now names the one the round records.
        recorded_gate=t.get("recorded_gate", t.get("verify_cmd")),
        verify_via_ci=raw_verify_via_ci,
        merge_policy=merge_policy,
        workflow=workflow,
        repo_type=repo_type,
        execution_checkout=raw_execution,
        max_turns=t.get("max_turns"),
        done_when=t.get("done_when"),
        expects_no_diff=expects_no_diff,
        steps=node_steps,
        stack_bases=_parse_stack_bases(nid, t.get("stack_bases", [])),
        resume=resume,
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
    allowed = {
        "branch",
        "base_branch",
        "pr_base",
        "checkpoint",
        "completed_steps",
        "pr",
        "mode",
        "source_round",
    }
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
    if len(set(completed)) != len(completed):
        raise PlanError(f"task {nid!r} resume 'completed_steps' must be unique")
    known = {s.id for s in steps} if steps is not None else {"main"}
    if unknown_steps := set(completed) - known:
        raise PlanError(
            f"task {nid!r} resume 'completed_steps' names unknown steps: "
            f"{', '.join(sorted(unknown_steps))}"
        )
    completed_set = set(completed)
    for step in steps or []:
        if step.id in completed_set and (missing := set(step.deps) - completed_set):
            raise PlanError(
                f"task {nid!r} resume completed step {step.id!r} is missing completed "
                f"dependencies: {', '.join(sorted(missing))}"
            )
    pr = raw.get("pr")
    if pr is not None and (not isinstance(pr, str) or _PR_URL.fullmatch(pr) is None):
        raise PlanError(f"task {nid!r} resume 'pr' must be a GitHub pull-request URL")
    mode = raw.get("mode", "pause")
    if mode not in RESUME_MODES:
        raise PlanError(f"task {nid!r} resume 'mode' must be 'pause' or 'retry'")
    source_round = raw.get("source_round")
    if source_round is not None and (
        not isinstance(source_round, int) or isinstance(source_round, bool) or source_round < 1
    ):
        raise PlanError(f"task {nid!r} resume 'source_round' must be a positive integer")
    return Resume(
        branch=cast(str, raw["branch"]),
        base_branch=cast(str, raw["base_branch"]),
        pr_base=cast(str, raw["pr_base"]),
        checkpoint=checkpoint,
        completed_steps=tuple(completed),
        pr=pr,
        mode=cast(ResumeMode, mode),
        source_round=source_round,
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


_AGENT_STEP_FIELDS = ("persona", "max_turns", "done_when", "expects_no_diff")


def _parse_steps(nid: str, raw_steps: object) -> list[Step]:
    """Validate a node's `steps` sub-DAG and return `Step`s (raise PlanError)."""
    from .plan import PlanError, PlanNode, _expects_no_diff, _topological_order

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
            if "/" in sid:
                raise PlanError(
                    f"task {nid!r} human step {sid!r} cannot contain '/': that separator is "
                    "reserved for NODE_ID/STEP_ID references"
                )
            present = [key for key in _AGENT_STEP_FIELDS if key in s]
            if present:
                raise PlanError(
                    f"task {nid!r} human step {sid!r} cannot set {', '.join(map(repr, present))}"
                )
        else:
            expects_no_diff = _expects_no_diff(nid, s, step=sid)
            if not expects_no_diff and (
                not isinstance(s.get("persona"), str) or not str(s.get("persona")).strip()
            ):
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
            expects_no_diff=expects_no_diff if kind == "agent" else False,
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
    runner: LifecycleRunner,
    *,
    concurrency: int | None = None,
) -> RepoPlanResult:
    """Run an all-lifecycle plan through the canonical tracked graph executor."""
    from .graph import Graph, GraphNode, run_graph

    def no_direct(
        node: Any,
        *,
        labels: Mapping[str, str] | None = None,
        cancel: threading.Event | None = None,
    ) -> Report:
        raise AssertionError("a repo plan has no direct agent nodes")

    result = run_graph(
        Graph(
            tasks=[
                GraphNode(
                    id=node.id, task=node.task or "", deps=node.deps, repo=node.repo, lifecycle=node
                )
                for node in plan.tasks
            ],
            concurrency=plan.concurrency,
        ),
        agent_runner=no_direct,
        lifecycle_runner=runner,
        concurrency=concurrency,
    )
    return RepoPlanResult(
        results={
            nid: RepoTaskResult(nid, item.status, result=item.lifecycle, error=item.error)
            for nid, item in result.results.items()
        },
        started_order=result.started_order,
    )


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
    verify_via_ci: bool = False,
    poll_interval: float,
    timeout: float,
    publication_attempts: int = 3,
    repo_type: RepositoryType | None = None,
) -> LifecycleRunner:
    """Build the production runner that drives each node through `run_repo_task`."""

    def runner(
        node: RepoPlanNode,
        *,
        journal: NodeSink | None = None,
        cancel: threading.Event | None = None,
    ) -> LifecycleResult:
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
            recorded_gate=node.recorded_gate,
            verify_via_ci=(node.verify_via_ci if node.verify_via_ci is not None else verify_via_ci),
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
            journal=journal,
            cancel=cancel,
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
        default=DEFAULT_ONEHARNESS_MODE,
        choices=list(ONEHARNESS_MODES),
        help="approval/sandbox mode for the harness (default: bypass — the "
        "no-approval mode; the container is the sandbox)",
    )
    parser.add_argument(
        "--verify-via-ci",
        action="store_true",
        help="make required CI checks on the pushed branch authoritative",
    )
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
    artifacts: dict[str, str] = {}
    if result.verify is not None and result.verify.log_path:
        artifacts["gate_log"] = result.verify.log_path
    if len(result.steps) == 1 and result.steps[0].report is not None:
        report_artifacts = result.steps[0].report.artifacts
        if worker_report := report_artifacts.get("worker_report"):
            artifacts["worker_report"] = worker_report
        if oneharness_session := report_artifacts.get("oneharness_session"):
            artifacts["oneharness_session"] = oneharness_session
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
        **({"artifacts": artifacts} if artifacts else {}),
        **({"deferred_cleanup": result.deferred_cleanup} if result.deferred_cleanup else {}),
        "follow_ups": result.report.assessment if result.report else None,
        "steps": [
            {
                "id": s.id,
                "kind": s.kind,
                "persona": s.persona,
                "status": s.status,
                # llmlint: ignore[changed_behavior_has_e2e] The pinned report-v4 producer cannot
                # emit this additive report-v5 field; direct report propagation uses the same
                # Report adapter and the CLI E2E proves its persisted consumer contract.
                **({"telemetry": s.report.telemetry} if s.report and s.report.telemetry else {}),
                **({"artifacts": s.report.artifacts} if s.report and s.report.artifacts else {}),
            }
            for s in result.steps
        ],
        "waiting_steps": list(result.waiting_steps),
        "resume": resume_payload(result.resume),
        **(
            {"retry_lineage": retry_lineage_payload(result.retry_lineage)}
            if result.retry_lineage
            else {}
        ),
    }


_result_payload = result_payload


def retry_lineage_payload(lineage: RetryLineage) -> RetryLineagePayload:
    """Serialize retry lineage through its checked boundary contract."""
    payload = RetryLineagePayload(
        supersedes_branch=lineage.supersedes_branch,
        supersedes_checkpoint=lineage.supersedes_checkpoint,
        disposition=lineage.disposition,
    )
    if lineage.reason:
        payload["reason"] = lineage.reason
    if lineage.supersedes_round is not None:
        payload["supersedes_round"] = lineage.supersedes_round
    return payload


def resume_payload(resume: Resume | None) -> ResumePayload | None:
    """Serialize continuation metadata for a paused lifecycle workstream."""
    if resume is None:
        return None
    payload = ResumePayload(
        branch=resume.branch,
        base_branch=resume.base_branch,
        pr_base=resume.pr_base,
        checkpoint=resume.checkpoint,
        completed_steps=list(resume.completed_steps),
        pr=resume.pr,
    )
    if resume.mode != "pause":
        payload["mode"] = resume.mode
    if resume.source_round is not None:
        payload["source_round"] = resume.source_round
    return payload


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

    registry = Registry()
    try:
        registry.repo_ref(args.repo)
        if args.execution_checkout is not None:
            registry.checkout_path(args.execution_checkout)
    except RegistryError as exc:
        parser.error(str(exc))

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
        recorded_gate=None,
        verify_via_ci=args.verify_via_ci,
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
        registry = Registry()
        for node in plan.tasks:
            validate_repo_aliases(node, registry)
        run_dir = (
            resolve_run_dir(args.runs_dir, plan_mapping, args.plan, args.run)
            if not args.no_record
            else None
        )
    except (ConfigError, PlanError) as exc:
        print(f"repo-plan: {exc}", file=sys.stderr)
        return 2

    round_record: ClaimedRound | None = None
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
        verify_via_ci=args.verify_via_ci,
        poll_interval=args.poll_interval,
        timeout=args.timeout,
        publication_attempts=args.publication_attempts,
        repo_type=args.repo_type,
    )
    result = run_repo_plan(plan, runner, concurrency=args.concurrency)

    payload: RepoPlanPayload = {
        "schema_version": RECORDED_RESULT_SCHEMA_VERSION,
        **({"round": round_record.number} if round_record is not None else {}),
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
