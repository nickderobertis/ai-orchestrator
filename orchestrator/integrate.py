"""Integrate completed workstream branches into a local base branch.

Candidates are first merged with the current base in their own worktrees and
verified there.  A passing candidate then fast-forwards the base.  Failed
candidates are restored after conflicts and do not stop the rest of the train.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import shlex
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from . import gitops
from .verify import run_gate

__all__ = ["IntegrateError", "IntegrationResult", "BranchResult", "integrate", "plan"]


class IntegrateError(Exception):
    """Invalid integration request or unsafe repository state."""


Status = Literal["merged", "updated", "skipped", "already-merged"]


@dataclass(frozen=True)
class BranchResult:
    branch: str
    status: Status
    reason: str | None = None


@dataclass(frozen=True)
class IntegrationResult:
    base: str
    branches: tuple[BranchResult, ...]
    base_advanced: bool
    pushed: bool


def plan(candidates: list[str], base: str, already_merged: set[str]) -> list[BranchResult]:
    """Validate ordered candidates and mark ancestry no-ops without side effects."""
    seen: set[str] = set()
    result: list[BranchResult] = []
    for branch in candidates:
        if branch == base:
            raise IntegrateError(f"base branch {base!r} cannot also be a candidate")
        if branch in seen:
            raise IntegrateError(f"duplicate candidate branch: {branch!r}")
        seen.add(branch)
        status: Status = "already-merged" if branch in already_merged else "updated"
        result.append(BranchResult(branch, status))
    return result


def _discover(repo: Path, base: str, pattern: str) -> list[str]:
    checked_out = set(gitops.worktrees(repo))
    matching = {branch for branch in gitops.branches(repo) if fnmatch.fnmatch(branch, pattern)}
    return sorted((checked_out | matching) - {base})


def _candidate_worktree(repo: Path, branch: str) -> tuple[Path, Path | None]:
    existing = gitops.worktrees(repo).get(branch)
    if existing is not None:
        return existing, None
    parent = Path(tempfile.mkdtemp(prefix="orchestrator-integrate-"))
    path = parent / "worktree"
    gitops.worktree_add_existing(repo, path, branch)
    return path, parent


def _remove_candidate_worktree(repo: Path, path: Path, temporary_parent: Path | None) -> None:
    if temporary_parent is None:
        return
    gitops.worktree_remove(repo, path)
    temporary_parent.rmdir()


def integrate(
    repo: str | Path,
    candidates: list[str] | None = None,
    *,
    base: str | None = None,
    pattern: str = "claude/*",
    gate_command: list[str] | None = None,
    refresh: bool = False,
    push: bool = False,
    remote: str = "origin",
) -> IntegrationResult:
    """Run a merge train (or update-only refresh) against a real git repository."""
    root = Path(repo).resolve()
    base = base or gitops.current_branch(root)
    if gitops.current_branch(root) != base:
        raise IntegrateError(f"repository must have base branch {base!r} checked out")
    if gitops.is_dirty(root):
        raise IntegrateError("base worktree is dirty")
    gate_command = ["just", "gate"] if gate_command is None else gate_command
    if not gate_command:
        raise IntegrateError("gate command must not be empty")
    if remote.startswith("-"):
        raise IntegrateError(f"invalid remote name: {remote!r}")
    if push and remote not in gitops.remotes(root):
        raise IntegrateError(f"unknown git remote: {remote!r}")

    selected = list(candidates) if candidates else _discover(root, base, pattern)
    unknown = [branch for branch in selected if not gitops.branch_exists(root, branch)]
    if unknown:
        raise IntegrateError(f"unknown local branch: {unknown[0]!r}")
    merged = {branch for branch in selected if gitops.is_ancestor(root, branch, base)}
    planned = plan(selected, base, merged)
    initial_base = gitops.head_sha(root)
    results: list[BranchResult] = []

    for item in planned:
        if item.status == "already-merged":
            results.append(item)
            continue
        branch = item.branch
        worktree, temporary_parent = _candidate_worktree(root, branch)
        try:
            if gitops.is_dirty(worktree):
                raise IntegrateError(f"candidate worktree for {branch!r} is dirty")
            if not gitops.merge_base_into_branch(
                worktree,
                base,
                message=f"Merge {base} into {branch}",
            ):
                results.append(BranchResult(branch, "skipped", "conflict"))
                continue

            if refresh:
                results.append(BranchResult(branch, "updated"))
                continue
            if not run_gate(worktree, gate_command).ok:
                results.append(BranchResult(branch, "skipped", "gate-failed"))
                continue
            try:
                gitops.merge_ff_only(root, branch)
            except gitops.GitError:
                gitops.merge_abort(root)
                results.append(BranchResult(branch, "skipped", "not-ready"))
                continue
            results.append(BranchResult(branch, "merged"))
        finally:
            _remove_candidate_worktree(root, worktree, temporary_parent)

    advanced = gitops.head_sha(root) != initial_base
    pushed = False
    if push and advanced:
        gitops.push(root, base, remote=remote, set_upstream=False)
        pushed = True
    return IntegrationResult(base, tuple(results), advanced, pushed)


def _render_readable(result: IntegrationResult) -> str:
    lines = [f"Integration train for {result.base}:"]
    if not result.branches:
        lines.append("  no candidate branches discovered")
    for branch in result.branches:
        detail = f" ({branch.reason})" if branch.reason else ""
        lines.append(f"  {branch.branch}: {branch.status}{detail}")
    lines.append(f"Base advanced: {'yes' if result.base_advanced else 'no'}")
    lines.append(f"Pushed: {'yes' if result.pushed else 'no'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Update, verify, and integrate workstream branches."
    )
    parser.add_argument("branches", nargs="*", help="ordered local branches (default: discover)")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base", help="base branch (default: currently checked out)")
    parser.add_argument("--pattern", default="claude/*", help="auto-discovery branch glob")
    parser.add_argument(
        "--gate", default="just gate", help="gate command, parsed like a shell line"
    )
    parser.add_argument("--refresh", action="store_true", help="update candidates without merging")
    parser.add_argument("--push", action="store_true", help="push an advanced base to origin")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    try:
        result = integrate(
            args.repo,
            args.branches or None,
            base=args.base,
            pattern=args.pattern,
            gate_command=shlex.split(args.gate),
            refresh=args.refresh,
            push=args.push,
            remote=args.remote,
        )
    except (IntegrateError, gitops.GitError) as exc:
        print(f"integrate: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(asdict(result), indent=2))
    else:
        print(_render_readable(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
