"""Recover lifecycle-preserved branches through their registered workflow."""

# llmlint: ignore-file[protocol_based_seams] recover_repo's injectable dispatch_fn
# is typed with lifecycle.DispatchFn, which is a structural typing.Protocol.

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from . import BASE_CONFIG, PERSONA_DIR, gitops
from .config import ConfigError
from .coordination import git_lock_identity
from .dispatch import Report, dispatch, scoped_session
from .github import CliGitHubBackend, GitHubBackend, GitHubError
from .lifecycle import (
    DEFAULT_LIFECYCLE_STEP_MAX_TURNS,
    MAX_MERGE_CONFLICT_RESOLUTIONS,
    DispatchFn,
    _attestation_body,
    _default_title,
    _effective_publication,
)
from .merge import (
    MERGE_CONFLICT_RETRY,
    GitHubMergeStrategy,
    LocalMergeStrategy,
    MergeContext,
    MergeOutcome,
    MergePolicy,
    classify_push_failure,
)
from .personas import persona_path
from .provenance import (
    attest_recovery,
    incomplete_commits,
    parse_preserved_step_metadata,
    recorded_pr_base,
)
from .redaction import redact
from .registry import Registry, RegistryEntry, RegistryError, Slug, merge_gate_coverage
from .verify import (
    NOOP_GATE,
    append_gate_log,
    comparison_env,
    format_merge_path_record,
    resolve_gate_template,
)
from .workspace import IdentityKey, RepoRef, RepositoryType, Workspace, WorkspaceError

_STEP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# What a recovery publishes when no commit on the preserved branch names the change.
# It is the body's `## What` line too, so the subject and the body cannot drift.
_RECOVERY_DESCRIPTION = "Recover lifecycle-preserved work through its merge-path gate."


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
    #: Where this recovery's merge-path gate run was preserved. Three consecutive
    #: recoveries of one branch previously reported three different causes with one
    #: sentence; this is the evidence that tells them apart.
    gate_log: str | None = None

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


def _search_checkouts(
    registry: Registry,
    identity: IdentityKey,
    origin: str,
    publication: Path,
    override: str | Path | None,
) -> list[Path]:
    """Every checkout a preserved branch of this identity could be in, best first."""
    ordered: list[Path] = [publication]
    if override is not None:
        explicit = registry.checkout_path(override).expanduser().resolve()
        # An operator-supplied path is a trust boundary: reading a branch out of
        # some other repository's checkout would publish work from the wrong tree.
        if not registry.is_checkout_of(explicit, origin):
            raise RegistryError(
                f"execution checkout {explicit} is not a git checkout of the repository "
                f"identity {str(identity)!r}; pass the checkout the preserved work was done in"
            )
        if explicit not in ordered:
            ordered.append(explicit)
    for candidate in registry.entries.values():
        path = Path(candidate.path).expanduser().resolve()
        matched = registry.identity_for_checkout(path)
        if path not in ordered and matched is not None and matched.identity == identity:
            ordered.append(path)
    return ordered


def _adopt_preserved_branch(publication: Path, candidates: list[Path], branch: str) -> None:
    """Make ``branch`` visible in the publication checkout, as a ref and nothing more.

    Fetching a ref into the publication checkout keeps its invariant intact; what
    it must never do is check the branch out and work in it.
    """
    if gitops.branch_exists(publication, branch):
        return
    for source in candidates[1:]:
        if gitops.branch_exists(source, branch) and gitops.import_branch(
            publication, source, branch
        ):
            return
    searched = ", ".join(str(path) for path in candidates)
    raise RegistryError(
        f"preserved branch {branch!r} does not exist in any registered checkout of this "
        f"identity (searched {searched}); pass --execution-checkout PATH if the work was "
        "done in a checkout this identity does not know about"
    )


def _wrong_verb_detail(worktree: Path, publication: Path, branch: str, remote_base: str) -> str:
    """Name the verb this branch actually belongs to, instead of just refusing it."""
    if not gitops.has_commits_ahead(worktree, remote_base):
        return (
            f"branch {branch!r} has no commits ahead of {remote_base}; there is nothing to "
            "recover — its work already reached the base, or none was ever committed"
        )
    return (
        f"branch {branch!r} carries no lifecycle-preserved incomplete provenance: it has "
        f"commits ahead of {remote_base}, and all of them are complete. 'repo-recover' "
        "publishes interrupted work; publish a completed branch with 'just integrate "
        f"{branch} --repo {shlex.quote(str(publication))}' or through its lifecycle/PR path"
    )


def recover_repo(
    repo: str | Path,
    branch: str,
    *,
    registry: Registry | None = None,
    workspace_root: str | Path | None = None,
    base: str | None = None,
    pr_base: str | None = None,
    execution_checkout: str | Path | None = None,
    recorded_gate: list[str] | None = None,
    github: GitHubBackend | None = None,
    merge_policy: MergePolicy | None = None,
    repo_type: RepositoryType | None = None,
    merge_method: str = "squash",
    cleanup: bool = True,
    dispatch_fn: DispatchFn = dispatch,
    oneharness_mode: str | None = "bypass",
    base_path: str | Path = BASE_CONFIG,
    persona_dir: str | Path = PERSONA_DIR,
) -> RecoveryResult:
    """Publish a preserved branch through its registered merge-path gate."""
    registry = registry or Registry()
    slug, entry = _registered(repo, registry)
    clone = Path(entry.path)
    identity = registry.identity_for_checkout(clone, repo_type=repo_type)
    if identity is None:
        raise RegistryError(f"registered checkout {clone} has no repository identity")
    decision = _effective_publication(identity.repo_type, identity.workflow, None, merge_policy)
    # Recovery no longer runs the gate itself, so refuse the same way dispatch does
    # rather than publishing preserved work that nothing will verify. Recovery
    # worktrees are cut from `clone`, so its hooks are the ones Git will run, and
    # the effective workflow decides whether required PR checks can stand in.
    coverage = merge_gate_coverage(
        identity.identity, clone, workflow=decision.workflow, github=github
    )
    if not coverage.meets_coverage_criteria:
        raise RegistryError(
            f"recovery refused for identity {coverage.identity}: {coverage.coverage_gap}; "
            "run 'just repos --audit-gate-coverage' and repair the merge-path gate "
            "before recovering preserved work"
        )
    owner, name = str(slug).split("/", 1)
    ref = RepoRef(owner, name, entry.origin)
    # Recovery shares the lifecycle root so it can adopt the exact tree a killed
    # dispatch left behind, including edits that never became a Git object.
    recovery_root = Path(workspace_root or Path.home() / ".ai-orchestrator" / "worktrees")
    workspace = Workspace(recovery_root, resolver=lambda _spec: clone)
    target = base or gitops.default_branch(clone)
    for field_name, value in (("branch", branch), ("base", target)):
        if not gitops.is_valid_branch_name(value):
            raise RegistryError(f"{field_name} {value!r} is not a valid Git branch")
    worktree: Path | None = None
    preserved_gate_log: str | None = None
    # A lifecycle branch only reaches the publication checkout once something has
    # already pushed it, so a first-attempt publication lives *only* in the
    # execution checkout the work was done in. Adopt it there rather than reporting
    # it missing, which is what fires on exactly the branches that succeed early.
    _adopt_preserved_branch(
        clone,
        _search_checkouts(registry, identity.identity, entry.origin, clone, execution_checkout),
        branch,
    )
    try:
        run_clone = workspace.ensure_clone(ref, base_branch=target)
        recorded_base = recorded_pr_base(clone, f"origin/{target}", branch)
        if pr_base is not None and recorded_base is not None and pr_base != recorded_base:
            raise RegistryError(
                f"requested pr_base={pr_base!r} conflicts with preserved branch metadata "
                f"pr_base={recorded_base!r}"
            )
        publication_base = pr_base or recorded_base or target
        synthetic_stack_base = (
            publication_base if publication_base.startswith("ai-orchestrator/stack-base/") else None
        )
        if not gitops.is_valid_branch_name(publication_base):
            raise RegistryError(f"pr_base {publication_base!r} is not a valid Git branch")
        worktree = workspace.worktree(ref, branch, base=f"origin/{publication_base}")
        if workspace.adopted_worktree(worktree) and gitops.is_dirty(worktree):
            gitops.add_all(worktree)
            gitops.commit(
                worktree,
                "chore: recover interrupted work (incomplete step)\n\n"
                "Orchestrator-Status: incomplete\n"
                f"Orchestrator-PR-Base: {publication_base}",
            )
        remote_base = f"origin/{publication_base}"
        if not incomplete_commits(worktree, remote_base, branch):
            raise RegistryError(_wrong_verb_detail(worktree, clone, branch, remote_base))
        # The merge path verifies the recovery, but an identity that cannot even name
        # its complete bar has nothing to hand a resolver worker or a reader of the
        # recovery attestation, so recovery still refuses a no-op gate.
        resolved_recorded_gate = recorded_gate or (
            resolve_gate_template(identity.gate, remote_base)
            if identity.gate != NOOP_GATE
            else None
        )
        if resolved_recorded_gate is None:
            raise RegistryError(
                "repository identity has a no-op gate; migrate it or pass --gate for recovery"
            )
        # The pre-push hook is what verifies this recovery, so it has to judge the
        # base the preserved branch actually publishes onto — a stack's parent, not
        # the remote HEAD it would otherwise discover.
        push_env = comparison_env(publication_base)

        # Only a push a pre-push hook gates carries a verdict; where required PR
        # checks are the coverage instead, they decide after this push, not at it.
        merge_path_gate = list(resolved_recorded_gate) if coverage.hook else []

        def preserve(*, ok: bool, output: str) -> str:
            """Keep this recovery's merge-path gate run and name where it landed."""
            nonlocal preserved_gate_log
            if not merge_path_gate:
                return ""
            preserved_gate_log = append_gate_log(
                recovery_root / "gate-logs" / branch.replace("/", "-"),
                format_merge_path_record(
                    label=f"recovery push {branch}",
                    command=merge_path_gate,
                    ok=ok,
                    output=output,
                ),
            )
            return f" — full merge-path gate output: {preserved_gate_log}"

        def attest_and_push() -> MergeOutcome | None:
            # Recovery always publishes through a push or a PR. The executable
            # pre-push hook / required PR checks are therefore authoritative.
            attest_recovery(worktree, remote_base, branch)
            try:
                pushed = gitops.push(worktree, branch, env=push_env)
            except gitops.GitError as exc:
                outcome = classify_push_failure(exc)
                detail = redact(str(exc))
                evidence = preserve(ok=False, output=exc.output)
                return MergeOutcome(
                    outcome,
                    (
                        f"repository pre-push gate rejected recovery of {branch!r}: {detail}"
                        if outcome == "gate-failed"
                        else f"recovery push of {branch!r} failed: {detail}"
                    )
                    + evidence,
                )
            preserve(ok=True, output=pushed)
            workspace.mirror_branch(ref, branch)
            return None

        def synchronize_attest_and_push_local_recovery() -> MergeOutcome | None:
            gitops.fetch(worktree)
            if not gitops.merge_base_into_branch(
                worktree,
                remote_base,
                message=f"Merge {remote_base} into {branch} for recovery",
                abort_on_conflict=False,
            ):
                return MergeOutcome(
                    MERGE_CONFLICT_RETRY,
                    f"current {remote_base} conflicts with preserved branch {branch}",
                )
            return attest_and_push()

        if decision.workflow != "local":
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
                    f"merge current {remote_base} into {branch!r}, resolve the conflict, "
                    "then retry",
                    pr_base=publication_base,
                    synthetic_stack_base=synthetic_stack_base,
                    gate_log=preserved_gate_log,
                )
            failed = attest_and_push()
            if failed is not None:
                return RecoveryResult(
                    str(slug),
                    branch,
                    target,
                    decision.workflow,
                    identity.repo_type,
                    decision.merge_policy,
                    failed.outcome,
                    failed.detail,
                    pr_base=publication_base,
                    synthetic_stack_base=synthetic_stack_base,
                    gate_log=preserved_gate_log,
                )
        strategy = (
            LocalMergeStrategy()
            if decision.workflow == "local"
            else GitHubMergeStrategy(github or CliGitHubBackend())
        )
        context = MergeContext(
            repo_slug=str(slug),
            clone_dir=run_clone,
            queue_identity=git_lock_identity(gitops.common_dir(clone)),
            base=publication_base,
            branch=branch,
            # The branch's own commits name the change first; this stands in only when
            # none of them can. It names the recovery rather than echoing the branch,
            # which is not a name for a change and pushed the subject past its limit.
            title=_default_title(worktree, remote_base, _RECOVERY_DESCRIPTION),
            body=(
                f"## What\n{_RECOVERY_DESCRIPTION}\n\n"
                "## Why\nThe original dispatch did not complete; this branch now carries "
                "a verified recovery attestation.\n"
                + _attestation_body(worktree, remote_base, branch)
            ),
            method=merge_method,
            policy=decision.merge_policy,
            repository_type=identity.repo_type,
            gate_command=tuple(merge_path_gate),
            local_prepare=(
                synchronize_attest_and_push_local_recovery if decision.workflow == "local" else None
            ),
        )
        published = strategy.publish_and_merge(context)
        if published.verification is not None and published.verification.log_path:
            preserved_gate_log = published.verification.log_path
        conflict_resolutions = 0
        while published.outcome == MERGE_CONFLICT_RETRY:
            if conflict_resolutions >= MAX_MERGE_CONFLICT_RESOLUTIONS:
                published = MergeOutcome(
                    "sync-conflict",
                    "base conflict remained unresolved after "
                    f"{MAX_MERGE_CONFLICT_RESOLUTIONS} resolve-and-requeue cycles; "
                    f"preserved branch {branch!r} requires manual recovery",
                )
                break
            conflict_resolutions += 1
            preserved = incomplete_commits(worktree, remote_base, branch)
            messages = [
                commit.message
                for commit in gitops.log_messages(worktree, remote_base, branch)
                if commit.sha in preserved
            ]
            metadata = next(
                (
                    parsed
                    for message in messages
                    if (parsed := parse_preserved_step_metadata(message))
                ),
                None,
            )
            if metadata is None:
                gitops.merge_abort(worktree)
                published = MergeOutcome(
                    "sync-conflict",
                    f"preserved branch {branch!r} has no resumable worker metadata; "
                    "resolve the conflict manually, then retry",
                )
                break
            step_id, persona = metadata.step_id, metadata.persona
            if not _STEP_ID.fullmatch(step_id):
                gitops.merge_abort(worktree)
                published = MergeOutcome(
                    "sync-conflict",
                    f"preserved branch {branch!r} has invalid resumable step metadata",
                )
                break
            try:
                persona_path(persona, Path(persona_dir))
            except ValueError:
                gitops.merge_abort(worktree)
                published = MergeOutcome(
                    "sync-conflict",
                    f"preserved branch {branch!r} has invalid resumable persona metadata",
                )
                break
            task = (
                "## What\n"
                f"Resolve the content conflict between this preserved branch and {remote_base}, "
                "preserve both sets of behavior, and commit the resolution.\n\n"
                "## Why\nThe base advanced while this completed partial work awaited recovery.\n\n"
                "## Acceptance criteria\n"
                "- The current merge conflict is fully resolved and committed.\n"
                "- Preserved work and current base behavior both remain.\n"
                "- The repository gate remains green.\n"
            )
            report: Report = dispatch_fn(
                persona,
                task,
                project_dir=str(worktree),
                oneharness_mode=oneharness_mode,
                base_path=base_path,
                persona_dir=persona_dir,
                session=f"{scoped_session(branch, worktree)}:{step_id}",
                max_turns=DEFAULT_LIFECYCLE_STEP_MAX_TURNS,
                done_when="The conflict is resolved, committed, and the gate is green.",
                # The resolver proves its resolution with the same gate the push will
                # run, so it must resolve the same comparison base.
                env=push_env,
            )
            if gitops.unmerged_paths(worktree):
                gitops.merge_abort(worktree)
                if not report.completed:
                    published = MergeOutcome(
                        "sync-conflict",
                        "conflict-resolution worker did not complete; preserved branch "
                        f"{branch!r} requires manual recovery",
                    )
                    break
                published = strategy.publish_and_merge(context)
                continue
            if gitops.is_dirty(worktree):
                gitops.add_all(worktree)
                gitops.commit(worktree, f"fix: resolve {publication_base} recovery conflict")
            if not report.completed:
                published = MergeOutcome(
                    "sync-conflict",
                    "conflict-resolution worker did not complete; preserved branch "
                    f"{branch!r} requires manual recovery",
                )
                break
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
            gate_log=preserved_gate_log,
        )
    finally:
        if cleanup and worktree is not None:
            workspace.remove_worktree(ref, worktree)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=("Publish a lifecycle-preserved branch through its registered merge-path gate.")
    )
    parser.add_argument("branch")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base")
    parser.add_argument(
        "--pr-base",
        help="recorded PR/stack base for a preserved stacked branch (default: --base)",
    )
    parser.add_argument(
        "--gate",
        help="the repository's complete gate command, parsed like a shell line, recorded "
        "as the bar this recovery is held to; the merge path is what runs it "
        "(default: the registered identity gate)",
    )
    parser.add_argument(
        "--execution-checkout",
        help="checkout the preserved work was done in, when it is not one this "
        "identity has registered; its branch is fetched into the publication checkout",
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
            execution_checkout=args.execution_checkout,
            recorded_gate=shlex.split(args.gate) if args.gate else None,
            merge_policy=args.merge_policy,
            repo_type=args.repo_type,
            merge_method=args.merge_method,
        )
    except (
        RegistryError,
        gitops.GitError,
        GitHubError,
        WorkspaceError,
        # A branch whose commits and recovery prose all overrun the subject limit has no
        # publishable subject; that refusal is the operator's to act on, not a traceback.
        ConfigError,
        ValueError,
    ) as exc:
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
        if result.gate_log:
            print(f"  merge-path gate output: {result.gate_log}")
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
