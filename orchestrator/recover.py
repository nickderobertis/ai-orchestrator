"""Recover lifecycle-preserved branches through their registered workflow."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from . import gitops
from .github import CliGitHubBackend, GitHubBackend, GitHubError
from .lifecycle import _default_title, _effective_publication
from .merge import GitHubMergeStrategy, LocalMergeStrategy, MergeContext, MergePolicy
from .provenance import (
    RECOVERY_TRAILER,
    incomplete_commits,
    recorded_pr_base,
    unattested_incomplete,
)
from .registry import Registry, RegistryEntry, RegistryError, Slug
from .verify import NOOP_GATE, resolve_gate_template, run_gate
from .workspace import RepoRef, RepositoryType, Workspace, WorkspaceError


@dataclass(frozen=True)
class RecoveryResult:
    repo: str
    branch: str
    base: str
    workflow: str
    repo_type: RepositoryType
    merge_policy: MergePolicy
    outcome: str
    detail: str
    pr: str | None = None
    pr_base: str = ""
    synthetic_stack_base: str | None = None

    def __post_init__(self) -> None:
        if not self.pr_base:
            object.__setattr__(self, "pr_base", self.base)

    @property
    def ok(self) -> bool:
        return self.outcome in {"merged", "pr-open"}


def _registered(repo: str | Path, registry: Registry) -> tuple[Slug, RegistryEntry]:
    value = str(repo)
    direct = registry.entries.get(Slug(value))
    matched = (Slug(value), direct) if direct is not None else registry.entry_for_checkout(value)
    if matched is None:
        raise RegistryError(
            f"repository {value!r} is not registered; register it with an explicit workflow "
            "before recovery"
        )
    return matched


def recover_repo(
    repo: str | Path,
    branch: str,
    *,
    registry: Registry | None = None,
    workspace_root: str | Path | None = None,
    base: str | None = None,
    pr_base: str | None = None,
    verify_cmd: list[str] | None = None,
    github: GitHubBackend | None = None,
    merge_policy: MergePolicy | None = None,
    repo_type: RepositoryType | None = None,
    merge_method: str = "squash",
    cleanup: bool = True,
) -> RecoveryResult:
    """Verify and publish a preserved branch through its registered workflow."""
    registry = registry or Registry()
    slug, entry = _registered(repo, registry)
    clone = Path(entry.path)
    identity = registry.identity_for_checkout(clone, repo_type=repo_type)
    if identity is None:
        raise RegistryError(f"registered checkout {clone} has no repository identity")
    decision = _effective_publication(identity.repo_type, identity.workflow, None, merge_policy)
    owner, name = str(slug).split("/", 1)
    ref = RepoRef(owner, name, entry.origin)
    workspace = Workspace(
        workspace_root or Path.home() / ".ai-orchestrator" / "recovery-worktrees",
        resolver=lambda _spec: clone,
    )
    target = base or gitops.default_branch(clone)
    for field_name, value in (("branch", branch), ("base", target)):
        if not gitops.is_valid_branch_name(value):
            raise RegistryError(f"{field_name} {value!r} is not a valid Git branch")
    worktree: Path | None = None
    try:
        workspace.ensure_clone(ref, base_branch=target)
        if not gitops.branch_exists(clone, branch):
            raise RegistryError(f"preserved branch {branch!r} does not exist in {clone}")
        recorded_base = recorded_pr_base(clone, f"origin/{target}", branch)
        if pr_base is not None and recorded_base is not None and pr_base != recorded_base:
            raise RegistryError(
                f"requested pr_base={pr_base!r} conflicts with preserved branch metadata "
                f"pr_base={recorded_base!r}"
            )
        publication_base = pr_base or recorded_base or target
        if not gitops.is_valid_branch_name(publication_base):
            raise RegistryError(f"pr_base {publication_base!r} is not a valid Git branch")
        worktree = workspace.worktree(ref, branch, base=f"origin/{publication_base}")
        if gitops.is_dirty(worktree):
            raise RegistryError(f"preserved branch worktree for {branch!r} is dirty")
        remote_base = f"origin/{publication_base}"
        if not incomplete_commits(worktree, remote_base, branch):
            raise RegistryError(
                f"branch {branch!r} has no lifecycle-preserved incomplete provenance"
            )
        if not gitops.merge_base_into_branch(
            worktree, remote_base, message=f"Merge {remote_base} into {branch} for recovery"
        ):
            return RecoveryResult(
                str(slug),
                branch,
                target,
                decision.workflow,
                identity.repo_type,
                decision.merge_policy,
                "sync-conflict",
                f"merge current {remote_base} into {branch!r}, resolve the conflict, then retry",
                pr_base=publication_base,
                synthetic_stack_base=(
                    publication_base
                    if publication_base.startswith("ai-orchestrator/stack-base/")
                    else None
                ),
            )
        command = verify_cmd or (
            resolve_gate_template(identity.gate, remote_base)
            if identity.gate != NOOP_GATE
            else None
        )
        env = {
            "ORCHESTRATOR_COMPARISON_REMOTE": "origin",
            "ORCHESTRATOR_COMPARISON_BASE": publication_base,
        }
        if command is None:
            raise RegistryError(
                "repository identity has a no-op gate; migrate it or pass --gate for recovery"
            )
        verified = run_gate(worktree, command, env=env)
        if not verified.ok:
            return RecoveryResult(
                str(slug),
                branch,
                target,
                decision.workflow,
                identity.repo_type,
                decision.merge_policy,
                "gate-failed",
                f"recovery gate failed; fix {branch!r} in its preserved branch and retry",
                pr_base=publication_base,
                synthetic_stack_base=(
                    publication_base
                    if publication_base.startswith("ai-orchestrator/stack-base/")
                    else None
                ),
            )
        missing = sorted(unattested_incomplete(worktree, remote_base, branch))
        if missing:
            trailers = "\n".join(f"{RECOVERY_TRAILER} {sha}" for sha in missing)
            gitops.commit_empty(
                worktree,
                "chore: attest verified recovery of preserved work\n\n" + trailers,
            )
        gitops.push(worktree, branch)
        strategy = (
            LocalMergeStrategy()
            if decision.workflow == "local"
            else GitHubMergeStrategy(github or CliGitHubBackend())
        )
        context = MergeContext(
            repo_slug=str(slug),
            clone_dir=clone,
            base=publication_base,
            branch=branch,
            title=_default_title(worktree, remote_base, f"Recover preserved branch {branch}"),
            body=(
                "## What\nRecover lifecycle-preserved work after explicit verification.\n\n"
                "## Why\nThe original dispatch did not complete; this branch now carries "
                "a verified recovery attestation.\n"
            ),
            method=merge_method,
            policy=decision.merge_policy,
            repository_type=identity.repo_type,
            verify_command=command,
            verify_env=env,
        )
        published = strategy.publish_and_merge(context)
        if published.outcome == "merged" and publication_base == target:
            workspace.fast_forward(ref, target)
        return RecoveryResult(
            str(slug),
            branch,
            target,
            decision.workflow,
            identity.repo_type,
            decision.merge_policy,
            published.outcome,
            published.detail,
            published.pr.url if published.pr else None,
            pr_base=publication_base,
            synthetic_stack_base=(
                publication_base
                if publication_base.startswith("ai-orchestrator/stack-base/")
                else None
            ),
        )
    finally:
        if cleanup and worktree is not None:
            workspace.remove_worktree(ref, worktree)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify and publish a lifecycle-preserved branch through its registered workflow."
        )
    )
    parser.add_argument("branch")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base")
    parser.add_argument(
        "--pr-base",
        help="recorded PR/stack base for a preserved stacked branch (default: --base)",
    )
    parser.add_argument(
        "--gate", help="gate command, parsed like a shell line (default: auto-detect)"
    )
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--merge-policy", choices=("auto", "direct", "none"), default=None)
    parser.add_argument("--repo-type", choices=("single-owner", "team"), default=None)
    parser.add_argument("--merge-method", choices=("squash", "merge", "rebase"), default="squash")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    try:
        result = recover_repo(
            args.repo,
            args.branch,
            workspace_root=args.workspace,
            base=args.base,
            pr_base=args.pr_base,
            verify_cmd=shlex.split(args.gate) if args.gate else None,
            merge_policy=args.merge_policy,
            repo_type=args.repo_type,
            merge_method=args.merge_method,
        )
    except (RegistryError, gitops.GitError, GitHubError, WorkspaceError, ValueError) as exc:
        print(f"repo-recover: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(asdict(result), indent=2))
    else:
        print(
            f"{result.repo} {result.branch}: {result.outcome} "
            f"[type={result.repo_type} workflow={result.workflow} "
            f"merge_policy={result.merge_policy} pr_base={result.pr_base} "
            f"synthetic_stack_base={result.synthetic_stack_base or '-'}] — {result.detail}"
        )
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
