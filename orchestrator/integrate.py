"""Integrate completed workstream branches into a local base branch.

Candidates are first merged with the current base in their own worktrees and
verified there.  A passing candidate is then **squash-published**: its verified
tree becomes one commit on the base, built in a detached scratch worktree that the
base checkout fast-forwards onto.  Failed candidates are restored after conflicts
and do not stop the rest of the train.

Squashing rather than fast-forwarding the branch itself is what keeps this verb on
the same base-history contract as the lifecycle: provenance commits are branch
state, so a recovered incomplete step reaches the base as an
`Orchestrator-Recovered-Incomplete:` trailer on that one commit and never as the
marker and attestation commits themselves.
"""

# llmlint: ignore-file[changed_behavior_has_e2e] Integrate e2e tests drive real
# git trains through this entry point; process-level merge-queue tests prove the
# FIFO/crash semantics of the single shared serialization seam.

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
from .coordination import git_lock_identity
from .lifecycle import _default_title
from .merge_queue import merge_queue_turn
from .provenance import attestation_trailers, unattested_incomplete
from .registry import Registry, RegistryError
from .verify import comparison_env, run_gate

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


def _publication_message(worktree: Path, remote_base: str, branch: str) -> str:
    """One commit message for the candidate's verified tree.

    The subject is synthesized from the branch's own authored commits, which already
    excludes provenance; the attestation follows as trailers so squashing does not
    drop the record that a step was left incomplete and a green gate cleared it.
    """
    # Task prose stands in only when no commit on the branch can name the change. A
    # branch name is not such a name, and a long one leaves no room for a subject at all.
    title = _default_title(worktree, remote_base, "Integrate a verified branch.")
    trailers = attestation_trailers(worktree, remote_base, "HEAD")
    if not trailers:
        return title
    return title + "\n\n" + "\n".join(trailers)


def _squash_publish(repo: Path, base: str, branch: str, *, message: str) -> bool:
    """Land the candidate as one commit on ``base``; False when it adds no content.

    Built detached and fast-forwarded onto the base checkout, so the checkout an
    operator has open is only ever advanced, never committed into.
    """
    parent = Path(tempfile.mkdtemp(prefix="orchestrator-integrate-publish-"))
    scratch = parent / "worktree"
    gitops.worktree_add_detached(repo, scratch, base)
    try:
        try:
            gitops.merge_squash(scratch, branch, message=message)
        except gitops.NothingToCommit:
            return False
        gitops.merge_ff_only(repo, gitops.head_sha(scratch))
        return True
    finally:
        gitops.worktree_remove(repo, scratch)
        parent.rmdir()


def _integrate_locked(
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
    requested = Path(repo).resolve()
    registry = Registry()
    registered = registry.entry_for_checkout(requested)
    root = Path(registered[1].path) if registered is not None else requested
    workflow = registered[1].workflow if registered is not None else "remote"
    identity = registry.identity_for_checkout(root) if registered is not None else None
    if identity is not None and identity.repo_type == "team":
        raise IntegrateError(
            "direct integration refused (repo_type=team); use the repo lifecycle/PR path"
        )
    if workflow == "remote" and (not refresh or push):
        remediation = (
            "registered workflow=remote"
            if registered is not None
            else "no affirmative local workflow"
        )
        raise IntegrateError(
            f"direct integration refused ({remediation}); use 'just repo-recover --repo "
            f"{shlex.quote(str(root))} <branch>' for preserved work, or the repo lifecycle/PR path"
        )
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
    integration_env = comparison_env(base, remote=remote)

    for item in planned:
        if item.status == "already-merged":
            results.append(item)
            continue
        branch = item.branch
        incomplete = unattested_incomplete(root, f"{remote}/{base}", branch)
        if incomplete and not refresh:
            results.append(
                BranchResult(
                    branch,
                    "skipped",
                    # The other verb by name and by runnable command: an operator who
                    # reached for this one has to be told which one this branch is for.
                    f"incomplete-provenance ({len(incomplete)} unattested commit(s)); this "
                    f"branch belongs to 'just repo-recover {branch} --repo "
                    f"{shlex.quote(str(root))}'",
                )
            )
            continue
        worktree, temporary_parent = _candidate_worktree(root, branch)
        try:
            if gitops.is_dirty(worktree):
                raise IntegrateError(f"candidate worktree for {branch!r} is dirty")
            gitops.fetch(worktree, remote=remote)
            remote_base = f"{remote}/{base}"
            if not gitops.merge_base_into_branch(
                worktree,
                remote_base,
                message=f"Merge {remote_base} into {branch}",
            ):
                results.append(BranchResult(branch, "skipped", "conflict"))
                continue
            if not refresh and not gitops.merge_base_into_branch(
                worktree,
                base,
                message=f"Merge integration train {base} into {branch}",
            ):
                results.append(BranchResult(branch, "skipped", "conflict"))
                continue

            if refresh:
                results.append(BranchResult(branch, "updated"))
                continue
            message = _publication_message(worktree, remote_base, branch)
            # Kept deliberately, unlike the lifecycle's own gate runs: the merge path
            # does not subsume this one. Each candidate lands on the local base
            # below, before the single optional push, so without this run unverified
            # commits reach the local base and a later hook rejection can no longer
            # say which branch of the train broke it.
            if not run_gate(worktree, gate_command, env=integration_env).ok:
                results.append(BranchResult(branch, "skipped", "gate-failed"))
                continue
            # The candidate merged the base in above, so the base is contained in the
            # verified tree — unless something advanced it since, which is exactly the
            # state this squash must not silently reconcile: the tree that would land
            # is not the tree the gate just judged.
            if not gitops.is_ancestor(root, gitops.head_sha(root), branch):
                results.append(BranchResult(branch, "skipped", "not-ready"))
                continue
            if not _squash_publish(root, base, branch, message=message):
                results.append(BranchResult(branch, "already-merged"))
                continue
            results.append(BranchResult(branch, "merged"))
        finally:
            _remove_candidate_worktree(root, worktree, temporary_parent)

    advanced = gitops.head_sha(root) != initial_base
    pushed = False
    if push and advanced:
        gitops.push(
            root,
            base,
            remote=remote,
            set_upstream=False,
            env=integration_env,
        )
        pushed = True
    return IntegrationResult(base, tuple(results), advanced, pushed)


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
    """Serialize a complete integration mutation against the repository identity."""
    root = Path(repo).resolve()
    with merge_queue_turn(git_lock_identity(gitops.common_dir(root))):
        return _integrate_locked(
            root,
            candidates,
            base=base,
            pattern=pattern,
            gate_command=gate_command,
            refresh=refresh,
            push=push,
            remote=remote,
        )


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
    except (IntegrateError, RegistryError, gitops.GitError) as exc:
        print(f"integrate: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(asdict(result), indent=2))
    else:
        print(_render_readable(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
