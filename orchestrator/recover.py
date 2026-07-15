"""Recover lifecycle-preserved branches through their registered workflow."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from . import gitops
from .coordination import advisory_lock
from .github import CliGitHubBackend, GitHubBackend, GitHubError
from .merge import GitHubMergeStrategy, LocalMergeStrategy, MergeContext
from .provenance import RECOVERY_TRAILER, incomplete_commits, unattested_incomplete
from .registry import Registry, RegistryEntry, RegistryError, Slug
from .verify import detect_gate, run_gate
from .workspace import RepoRef, Workspace


@dataclass(frozen=True)
class RecoveryResult:
    repo: str
    branch: str
    base: str
    workflow: str
    outcome: str
    detail: str
    pr: str | None = None

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
    verify_cmd: list[str] | None = None,
    github: GitHubBackend | None = None,
    merge_policy: str = "auto",
    merge_method: str = "squash",
    cleanup: bool = True,
) -> RecoveryResult:
    """Verify and publish a preserved branch through its registered workflow."""
    registry = registry or Registry()
    slug, entry = _registered(repo, registry)
    clone = Path(entry.path)
    owner, name = str(slug).split("/", 1)
    ref = RepoRef(owner, name, entry.origin)
    workspace = Workspace(
        workspace_root or Path.home() / ".ai-orchestrator" / "recovery-worktrees",
        resolver=lambda _spec: clone,
    )
    target = base or gitops.default_branch(clone)
    worktree: Path | None = None
    try:
        with advisory_lock(f"git:{gitops.common_dir(clone)}"):
            gitops.fetch(clone)
        if not gitops.branch_exists(clone, branch):
            raise RegistryError(f"preserved branch {branch!r} does not exist in {clone}")
        worktree = workspace.worktree(ref, branch, base=f"origin/{target}")
        if gitops.is_dirty(worktree):
            raise RegistryError(f"preserved branch worktree for {branch!r} is dirty")
        remote_base = f"origin/{target}"
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
                entry.workflow,
                "sync-conflict",
                f"merge current {remote_base} into {branch!r}, resolve the conflict, then retry",
            )
        command = verify_cmd or detect_gate(worktree)
        env = {
            "ORCHESTRATOR_COMPARISON_REMOTE": "origin",
            "ORCHESTRATOR_COMPARISON_BASE": target,
        }
        if command is None:
            raise RegistryError("no lifecycle gate detected; pass --gate with the repository gate")
        verified = run_gate(worktree, command, env=env)
        if not verified.ok:
            return RecoveryResult(
                str(slug),
                branch,
                target,
                entry.workflow,
                "gate-failed",
                f"recovery gate failed; fix {branch!r} in its preserved branch and retry",
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
            if entry.workflow == "local"
            else GitHubMergeStrategy(github or CliGitHubBackend())
        )
        context = MergeContext(
            repo_slug=str(slug),
            clone_dir=clone,
            base=target,
            branch=branch,
            title=f"Recover preserved branch {branch}",
            body=(
                "## What\nRecover lifecycle-preserved work after explicit verification.\n\n"
                "## Why\nThe original dispatch did not complete; this branch now carries "
                "a verified recovery attestation.\n"
            ),
            method=merge_method,
            policy=merge_policy,
            verify_command=command,
            verify_env=env,
        )
        published = strategy.publish_and_merge(context)
        if published.outcome == "merged":
            workspace.fast_forward(ref, target)
        return RecoveryResult(
            str(slug),
            branch,
            target,
            entry.workflow,
            published.outcome,
            published.detail,
            published.pr.url if published.pr else None,
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
        "--gate", help="gate command, parsed like a shell line (default: auto-detect)"
    )
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--merge-policy", choices=("auto", "direct", "none"), default="auto")
    parser.add_argument("--merge-method", choices=("squash", "merge", "rebase"), default="squash")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    try:
        result = recover_repo(
            args.repo,
            args.branch,
            workspace_root=args.workspace,
            base=args.base,
            verify_cmd=shlex.split(args.gate) if args.gate else None,
            merge_policy=args.merge_policy,
            merge_method=args.merge_method,
        )
    except (RegistryError, gitops.GitError, GitHubError, ValueError) as exc:
        print(f"repo-recover: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(asdict(result), indent=2))
    else:
        print(f"{result.repo} {result.branch}: {result.outcome} — {result.detail}")
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
