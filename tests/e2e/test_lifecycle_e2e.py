"""E2E: drive the real repo lifecycle end to end against a real bare git origin.

Git is real throughout (clone → worktree → branch → commit → push → merge on a
local bare repo). Only the two external seams are faked: the paid harness
(`writing_dispatch`, which makes a real edit) and GitHub's PR/CI decisioning
(`FakeGitHub`, which performs the merge with real git). So these prove the whole
journey — local direct-merge and the GitHub PR+auto-merge path — for real.
"""

# llmlint: ignore-file[e2e_not_mocked,tests_mirror_real_usage] these repo-lifecycle
# e2es fake ONLY the paid harness
# (the dispatch_fn seam) and GitHub's PR/CI decisioning while driving real git and the
# real merge, exactly as AGENTS.md prescribes for lifecycle tests; the real onejudge
# dispatch boundary is covered separately in test_dispatch_e2e.py.

from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import replace
from pathlib import Path
from typing import TypeVar, cast

import pytest
from fakes import FakeGitHub, make_writing_dispatch
from waits import deadline as e2e_deadline
from waits import timeout as e2e_timeout

import orchestrator.graph as graph_module
import orchestrator.lifecycle as lifecycle_module
from orchestrator import gitops
from orchestrator.coordination import LockTimeout, advisory_lock, git_lock_identity
from orchestrator.dispatch import Report
from orchestrator.github import PullRequest
from orchestrator.graph import graph_payload, parse_graph, run_graph
from orchestrator.journal import NodeJournal, NodeSink, open_journal
from orchestrator.lifecycle import (
    AI_ORCHESTRATOR_IDENTITY,
    MAX_MERGE_CONFLICT_RESOLUTIONS,
    RepoPlan,
    RepoPlanNode,
    Resume,
    StackBase,
    Step,
    load_repo_plan,
    main_plan,
    main_task,
    result_payload,
    run_repo_plan,
    run_repo_task,
)
from orchestrator.merge import GitHubMergeStrategy
from orchestrator.merge_queue import merge_queue_turn
from orchestrator.next_round import main as next_round_main
from orchestrator.provenance import (
    INCOMPLETE_TRAILER,
    PR_BASE_TRAILER,
    format_preserved_step_metadata,
    incomplete_commits,
)
from orchestrator.recover import recover_repo
from orchestrator.registry import Registry, RegistryEntry, Slug
from orchestrator.replan import next_round
from orchestrator.runs import NodeId, RunId, prepare_round, write_result
from orchestrator.workspace import IdentityKey, Workspace, normalize_repo

_T = TypeVar("_T")


def _workspace(tmp_path: Path, *origins: Path, workflow: str = "local") -> Workspace:
    """Build a registry-like resolver over real canonical test checkouts."""
    checkouts = {
        str(origin.resolve()): gitops.clone(origin, tmp_path / f"canonical-{index}")
        for index, origin in enumerate(origins)
    }
    return Workspace(
        tmp_path / "worktrees",
        resolver=lambda spec: checkouts[str(Path(spec).resolve())],
        workflow=workflow,
    )


def _per_step_dispatch(fail_step: str | None = None):
    """A dispatch_fn that writes a file named after the step id (from the session)."""

    def dispatch_fn(
        persona: str, task: str, *, project_dir: str, session: str, **_: object
    ) -> Report:
        sid = session.rsplit(":", 1)[-1]
        completed = sid != fail_step
        if completed:
            (Path(project_dir) / f"{sid}.txt").write_text(f"{persona}\n", encoding="utf-8")
        return Report(persona, 0 if completed else 1, completed, False, 2, [], {}, {}, "")

    return dispatch_fn


def _has_file(origin: Path, ref: str, path: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(origin), "cat-file", "-e", f"{ref}:{path}"],
            capture_output=True,
        ).returncode
        == 0
    )


def _tip(origin: Path, ref: str) -> str:
    return subprocess.run(
        ["git", "-C", str(origin), "rev-parse", ref], text=True, capture_output=True
    ).stdout.strip()


def _run_while_merge_turn_is_held(
    canonical: Path, operation: Callable[[], _T], *, should_wait: bool
) -> _T:
    acquired = threading.Event()
    release = threading.Event()

    def hold_turn() -> None:
        with merge_queue_turn(git_lock_identity(gitops.common_dir(canonical))):
            acquired.set()
            release.wait(e2e_timeout(10))

    with ThreadPoolExecutor(max_workers=2) as pool:
        holder = pool.submit(hold_turn)
        assert acquired.wait(e2e_timeout(5))
        publication = pool.submit(operation)
        try:
            if should_wait:
                with pytest.raises(FutureTimeout):
                    publication.result(timeout=0.2)
                release.set()
            result = publication.result(timeout=e2e_timeout(10))
        finally:
            release.set()
            holder.result(timeout=e2e_timeout(10))
    return result


def test_published_dispatch_survives_deferred_teardown_and_redispatch_reclaims_it(
    tmp_path, bare_origin, command_base, personas_dir
) -> None:
    """A real merged lifecycle self-heals after teardown leaves its worktree registered."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical")
    root = tmp_path / "worktrees"

    class ContendedTeardownWorkspace(Workspace):
        def remove_worktree(self, repo, path) -> None:
            self._release_worktree_lease(path)
            raise LockTimeout("shared .git remains busy")

    contended = ContendedTeardownWorkspace(
        root, resolver=lambda _spec: canonical, workflow="local", repo_type="single-owner"
    )
    journal = open_journal(tmp_path / "run", RunId("teardown"), 1)
    scope = NodeJournal(journal, NodeId("publish"), RunId("teardown"), 1)
    first = run_repo_task(
        str(origin),
        "complete-now write-unique-change first",
        "engineer",
        workspace=contended,
        branch="teardown-retry",
        base_path=command_base(),
        persona_dir=personas_dir,
        verify_cmd=["true"],
        journal=scope,
    )

    assert first.outcome == "merged", first.detail
    assert first.deferred_cleanup and "remove-worktree deferred" in first.deferred_cleanup[0]
    serialized = json.loads(json.dumps(result_payload(first)))
    assert serialized["outcome"] == "merged"
    assert serialized["deferred_cleanup"] == first.deferred_cleanup
    deferred = [event for event in journal.events() if event.kind == "cleanup-deferred"]
    assert deferred and deferred[0].detail["operation"] == "remove-worktree"
    orphan = gitops.worktrees(contended.clone_dir(normalize_repo(str(origin))))["teardown-retry"]
    assert orphan.exists()

    recovered = Workspace(
        root,
        resolver=lambda _spec: canonical,
        workflow="local",
        repo_type="single-owner",
        run_token=contended.run_token,
    )
    second = run_repo_task(
        str(origin),
        "complete-now write-change second",
        "engineer",
        workspace=recovered,
        branch="teardown-retry",
        base_path=command_base(),
        persona_dir=personas_dir,
        verify_cmd=["true"],
    )
    assert second.outcome == "merged", second.detail


def test_lifecycle_failure_survives_simultaneous_deferred_teardown(
    tmp_path, bare_origin, command_base, personas_dir
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-failed")

    class ContendedTeardownWorkspace(Workspace):
        def remove_worktree(self, repo, path) -> None:
            self._release_worktree_lease(path)
            raise LockTimeout("shared .git remains busy")

    workspace = ContendedTeardownWorkspace(
        tmp_path / "failed-worktrees",
        resolver=lambda _spec: canonical,
        workflow="local",
        repo_type="single-owner",
    )
    result = run_repo_task(
        str(origin),
        "should-fail write-change preserve original failure",
        "engineer",
        workspace=workspace,
        branch="failed-teardown",
        base_path=command_base(),
        persona_dir=personas_dir,
        verify_cmd=["true"],
    )

    assert result.outcome == "not-completed"
    assert "step 'main' hit the turn cap" in result.detail
    assert result.deferred_cleanup and "remove-worktree deferred" in result.deferred_cleanup[0]
    ref = normalize_repo(str(origin))
    cleanup = Workspace(
        tmp_path / "failed-worktrees",
        resolver=lambda _spec: canonical,
        run_token=workspace.run_token,
    )
    cleanup.ensure_clone(ref)
    cleanup.remove_worktree(ref, gitops.worktrees(cleanup.clone_dir(ref))["failed-teardown"])


def test_turn_cap_auto_resumes_preserved_branch_without_rerunning_completed_steps(
    tmp_path, bare_origin, command_base, personas_dir
) -> None:
    """A real onejudge cap continues its preserved workstream in place."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-auto-resume")
    prepare_runs = tmp_path / "prepare-runs.log"
    implement_runs = tmp_path / "implement-runs.log"

    result = run_repo_task(
        str(origin),
        workspace=Workspace(
            tmp_path / "auto-resume-worktrees",
            resolver=lambda _spec: canonical,
            workflow="local",
            repo_type="single-owner",
        ),
        steps=[
            Step(
                "prepare",
                "engineer",
                f"complete-after-13 write-change record-run={prepare_runs}",
            ),
            Step(
                "implement",
                "engineer",
                f"complete-now resume-after-cap write-change record-run={implement_runs}",
                deps=["prepare"],
                max_turns=1,
            ),
        ],
        branch="feature/automatic-turn-cap-resume",
        base_path=command_base(),
        persona_dir=personas_dir,
        verify_cmd=["true"],
    )

    assert result.outcome == "merged", result.detail
    assert prepare_runs.read_text(encoding="utf-8").splitlines() == ["run"] * 13
    assert implement_runs.read_text(encoding="utf-8").splitlines() == ["run", "run"]
    assert _has_file(origin, "main", ".fake-turn-cap-preserved")
    assert result.retry_lineage is not None
    assert result.retry_lineage.disposition == "recovered"


def test_real_git_teardown_refusal_is_deferred_after_publication(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-locked-cleanup")
    workspace = Workspace(
        tmp_path / "locked-cleanup-worktrees",
        resolver=lambda _spec: canonical,
        workflow="local",
        repo_type="single-owner",
    )

    def locking_dispatch(persona, task, *, project_dir, **kwargs):
        worktree = Path(project_dir)
        (worktree / "locked-cleanup.txt").write_text("published\n", encoding="utf-8")
        run_clone = workspace.clone_dir(normalize_repo(str(origin)))
        subprocess.run(["git", "-C", str(run_clone), "worktree", "lock", str(worktree)], check=True)
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    result = run_repo_task(
        str(origin),
        "publish before real Git cleanup refusal",
        "engineer",
        workspace=workspace,
        branch="locked-cleanup",
        dispatch_fn=locking_dispatch,
        verify_cmd=["true"],
    )

    assert result.outcome == "merged", result.detail
    assert result.deferred_cleanup and "locked working tree" in result.deferred_cleanup[0]
    run_clone = workspace.clone_dir(normalize_repo(str(origin)))
    orphan = gitops.worktrees(run_clone)["locked-cleanup"]
    subprocess.run(["git", "-C", str(run_clone), "worktree", "unlock", str(orphan)], check=True)
    workspace.remove_worktree(normalize_repo(str(origin)), orphan)


def test_synthetic_stack_teardown_contention_is_deferred_with_real_git(
    tmp_path, bare_origin
) -> None:
    """The other lifecycle teardown site preserves its real synthetic push outcome."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-stack")

    class ContendedStackWorkspace(Workspace):
        def remove_worktree(self, repo, path) -> None:
            raise LockTimeout("shared .git remains busy")

    workspace = ContendedStackWorkspace(
        tmp_path / "stack-worktrees",
        resolver=lambda _spec: canonical,
        workflow="local",
        repo_type="single-owner",
    )
    ref = normalize_repo(str(origin))
    workspace.ensure_clone(ref)
    result = lifecycle_module.LifecycleResult(
        ref.slug, "stack", "engineer", "main", "stack", "error"
    )

    built = lifecycle_module._build_synthetic_stack_base(
        ref,
        workspace,
        "main",
        [StackBase("main")],
        result=result,
        journal=lifecycle_module.NullNodeJournal(),
    )

    assert isinstance(built, lifecycle_module.SyntheticStackBase)
    assert _tip(origin, f"refs/heads/{built.branch}")
    assert result.deferred_cleanup and "remove-worktree deferred" in result.deferred_cleanup[0]
    cleanup = Workspace(
        tmp_path / "stack-worktrees",
        resolver=lambda _spec: canonical,
        run_token=workspace.run_token,
    )
    cleanup.ensure_clone(ref)
    cleanup.remove_worktree(ref, gitops.worktrees(cleanup.clone_dir(ref))[built.branch])


def test_failed_synthetic_stack_defers_worktree_and_branch_cleanup_with_real_git(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    writer = gitops.clone(origin, tmp_path / "stack-writer")
    for branch, content in (("stack-left", "left\n"), ("stack-right", "right\n")):
        subprocess.run(["git", "checkout", "-B", branch, "origin/main"], cwd=writer, check=True)
        (writer / "conflict.txt").write_text(content, encoding="utf-8")
        gitops.add_all(writer)
        gitops.commit(writer, f"test: create {branch}")
        gitops.push(writer, branch, set_upstream=False)
    canonical = gitops.clone(origin, tmp_path / "canonical-conflict")

    class ContendedCleanupWorkspace(Workspace):
        def remove_worktree(self, repo, path) -> None:
            raise LockTimeout("worktree cleanup busy")

        def delete_branch(self, repo, branch) -> None:
            raise LockTimeout("branch cleanup busy")

    workspace = ContendedCleanupWorkspace(
        tmp_path / "conflict-worktrees",
        resolver=lambda _spec: canonical,
        workflow="local",
        repo_type="single-owner",
    )
    ref = normalize_repo(str(origin))
    workspace.ensure_clone(ref)
    result = lifecycle_module.LifecycleResult(
        ref.slug, "stack", "engineer", "main", "stack", "error"
    )

    built = lifecycle_module._build_synthetic_stack_base(
        ref,
        workspace,
        "main",
        [StackBase("stack-left"), StackBase("stack-right")],
        result=result,
        journal=lifecycle_module.NullNodeJournal(),
    )

    assert isinstance(built, lifecycle_module.StackConflict)
    assert [detail.split(" deferred", 1)[0] for detail in result.deferred_cleanup] == [
        "remove-worktree",
        "delete-branch",
    ]
    run_clone = workspace.clone_dir(ref)
    synthetic = next(
        branch
        for branch in gitops.worktrees(run_clone)
        if branch.startswith("ai-orchestrator/stack-base/")
    )
    cleanup = Workspace(
        tmp_path / "conflict-worktrees",
        resolver=lambda _spec: canonical,
        run_token=workspace.run_token,
    )
    cleanup.ensure_clone(ref)
    cleanup.remove_worktree(ref, gitops.worktrees(run_clone)[synthetic])
    cleanup.delete_branch(ref, synthetic)


def test_identity_cache_and_repo_post_checkout_hook_are_wired_across_dispatches(
    tmp_path, bare_origin, command_base, personas_dir
) -> None:
    """Real worktree creation runs repo hooks and reuses one identity cache."""
    origin = bare_origin()
    hook_author = gitops.clone(origin, tmp_path / "hook-author")
    hook_marker = tmp_path / "post-checkout-runs"
    hooks = hook_author / ".githooks"
    hooks.mkdir()
    hook = hooks / "post-checkout"
    hook.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$PWD\" >> {shlex.quote(str(hook_marker))}\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    gitops.add_all(hook_author)
    gitops.commit(hook_author, "test: add target post-checkout hook")
    gitops.push(hook_author, "main", set_upstream=False)

    workspace = _workspace(tmp_path, origin)
    observed_caches: list[Path] = []

    for number in (1, 2):
        result = run_repo_task(
            str(origin),
            f"complete-now capture-cache-env write-unique-change {number}\n",
            "engineer",
            workspace=workspace,
            base_path=command_base(),
            persona_dir=personas_dir,
            repo_type="single-owner",
            workflow="local",
            branch=f"cache-hook-{number}",
            verify_cmd=[
                "sh",
                "-c",
                'test -d "$ORCHESTRATOR_CACHE_DIR" && '
                'case "$ORCHESTRATOR_CACHE_DIR" in /*) true;; *) false;; esac',
            ],
        )
        assert result.outcome == "merged", result.detail
        publication_checkout = Path(
            workspace.selection(normalize_repo(str(origin))).publication_checkout
        )
        observed_caches.append(
            Path((publication_checkout / "CACHE_ENV.txt").read_text(encoding="utf-8"))
        )

    assert observed_caches[0] == observed_caches[1]
    assert observed_caches[0].parent.name == "cache"
    assert observed_caches[0].parent.parent == Path(os.environ["AI_ORCHESTRATOR_HOME"])
    lifecycle_worktrees = [
        path
        for path in hook_marker.read_text(encoding="utf-8").splitlines()
        if Path(path).name.startswith("cache-hook-")
    ]
    assert len(lifecycle_worktrees) == 2


def test_real_lifecycle_dispatch_drafts_pr_bodies_and_preserves_fallbacks(
    tmp_path, bare_origin, command_base, personas_dir
) -> None:
    """The real onejudge boundary authors single/workstream bodies and safely falls back."""
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin, workflow="remote")
    github = FakeGitHub(origin)
    base = command_base(max_turns=2)

    single_task = (
        "## What\ncomplete-now write-change the single lifecycle behavior.\n\n"
        "## Why\nGive users the requested capability.\n\n"
        "## Acceptance criteria\n- The branch publishes through the real lifecycle.\n\n"
        "## Additional info\nderive-task-why"
    )
    single = run_repo_task(
        "acme/widget",
        single_task,
        "engineer",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-single",
        base_path=base,
        persona_dir=personas_dir,
        verify_cmd=["true"],
    )
    assert single.pr is not None, (single.outcome, single.detail)
    single_body = github._prs[single.pr.number].body
    assert single.outcome == "pr-open"
    assert single_body == (
        "## What\nAdds the completed behavior from the branch diff.\n\n"
        "## Why\nGive users the requested capability.\n"
    )
    assert single_task not in single_body

    workstream = run_repo_task(
        "acme/widget",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-workstream",
        steps=[
            Step("implement", "engineer", "complete-now write-change raw implementation handoff"),
            Step(
                "verify",
                "engineer",
                "complete-now capture-cache-env raw verification handoff",
                deps=["implement"],
            ),
        ],
        base_path=base,
        persona_dir=personas_dir,
        verify_cmd=["true"],
    )
    workstream_body = github._prs[workstream.pr.number].body
    assert workstream.outcome == "pr-open"
    assert workstream_body.startswith("## What\nAdds the completed behavior from the branch diff.")
    assert "raw implementation handoff" not in workstream_body

    structured_draft = run_repo_task(
        "acme/widget",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-structured-workstream",
        steps=[
            Step(
                "implement",
                "engineer",
                "## What\ncomplete-now write-change the drafted API.\n\n"
                "## Why\nLet users call the drafted API.\n\n"
                "## Acceptance criteria\n- The drafted API is available.\n\n"
                "## Additional info\nderive-workstream-why",
            ),
            Step(
                "compatibility",
                "engineer",
                "complete-now write-unique-change preserve legacy callers",
                deps=["implement"],
            ),
            Step(
                "verify",
                "engineer",
                "## What\ncomplete-now capture-cache-env verify the drafted API.\n\n"
                "## Why\nKeep the drafted API reliable.\n\n"
                "## Acceptance criteria\n- The drafted API is verified.",
                deps=["compatibility"],
            ),
        ],
        base_path=base,
        persona_dir=personas_dir,
        verify_cmd=["true"],
    )
    assert structured_draft.outcome == "pr-open"
    assert structured_draft.pr is not None
    assert github._prs[structured_draft.pr.number].body == (
        "## What\nAdds the completed behavior from the branch diff.\n\n"
        "## Why\nLet users call the drafted API while keeping it reliable.\n"
    )

    fallback_task = (
        "## What\ncomplete-now write-change with a deterministic fallback.\n\n"
        "## Why\nKeep the user's publication context when drafting-fails.\n\n"
        "## Acceptance criteria\n- The fallback is published.\n\n"
        "## Additional info\nThis must not appear in the PR body."
    )
    fallback = run_repo_task(
        "acme/widget",
        fallback_task,
        "engineer",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-fallback",
        base_path=base,
        persona_dir=personas_dir,
        verify_cmd=["true"],
    )
    assert fallback.outcome == "pr-open"
    assert github._prs[fallback.pr.number].body == (
        "## What\ncomplete-now write-change with a deterministic fallback.\n\n"
        "## Why\nKeep the user's publication context when drafting-fails.\n"
    )

    structured_workstream = run_repo_task(
        "acme/widget",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-structured-workstream-fallback",
        steps=[
            Step(
                "implement",
                "engineer",
                "## What\ncomplete-now write-change the API.\n\n"
                "## Why\nLet users call the API.\n\n"
                "## Acceptance criteria\n- The API is available.",
            ),
            Step(
                "legacy",
                "engineer",
                "complete-now write-unique-change legacy compatibility step",
                deps=["implement"],
            ),
            Step(
                "verify",
                "engineer",
                "## What\ncomplete-now capture-cache-env verify the API.\n\n"
                "## Why\nPrevent regressions when drafting-fails.\n\n"
                "## Acceptance criteria\n- The API is verified.",
                deps=["legacy"],
            ),
        ],
        base_path=base,
        persona_dir=personas_dir,
        verify_cmd=["true"],
    )
    assert structured_workstream.outcome == "pr-open"
    assert github._prs[structured_workstream.pr.number].body == (
        "## What\n- complete-now write-change the API.\n"
        "- **legacy** (`engineer`): complete-now write-unique-change legacy compatibility step\n"
        "- complete-now capture-cache-env verify the API.\n\n"
        "## Why\n- Let users call the API.\n"
        "- **legacy** (`engineer`): complete-now write-unique-change legacy compatibility step\n"
        "- Prevent regressions when drafting-fails.\n"
    )

    empty_task = (
        "## What\ncomplete-now write-change drafting-empty empty fallback handoff.\n\n"
        "## Why\nPreserve structured context after empty drafting output.\n\n"
        "## Acceptance criteria\n- The deterministic fallback is published.\n\n"
        "## Additional info\nThis orchestration detail must not appear."
    )
    empty = run_repo_task(
        "acme/widget",
        empty_task,
        "engineer",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-empty",
        base_path=base,
        persona_dir=personas_dir,
        verify_cmd=["true"],
    )
    assert empty.outcome == "pr-open"
    assert github._prs[empty.pr.number].body == (
        "## What\ncomplete-now write-change drafting-empty empty fallback handoff.\n\n"
        "## Why\nPreserve structured context after empty drafting output.\n"
    )

    invalid_task = "complete-now write-change drafting-invalid invalid fallback handoff"
    invalid = run_repo_task(
        "acme/widget",
        invalid_task,
        "engineer",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-invalid",
        base_path=base,
        persona_dir=personas_dir,
        verify_cmd=["true"],
    )
    assert invalid.outcome == "pr-open"
    assert invalid_task in github._prs[invalid.pr.number].body
    assert "nonempty malformed drafting output" not in github._prs[invalid.pr.number].body

    structured_invalid_task = (
        "## What\ncomplete-now write-change drafting-invalid structured invalid fallback.\n\n"
        "## Why\nPreserve structured context after malformed drafting output.\n\n"
        "## Acceptance criteria\n- The deterministic fallback is published.\n\n"
        "## Additional info\nThis orchestration detail must not appear."
    )
    structured_invalid = run_repo_task(
        "acme/widget",
        structured_invalid_task,
        "engineer",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-structured-invalid",
        base_path=base,
        persona_dir=personas_dir,
        verify_cmd=["true"],
    )
    assert structured_invalid.outcome == "pr-open"
    assert github._prs[structured_invalid.pr.number].body == (
        "## What\ncomplete-now write-change drafting-invalid structured invalid fallback.\n\n"
        "## Why\nPreserve structured context after malformed drafting output.\n"
    )

    error_task = "complete-now write-change drafting-errors error fallback handoff"
    error = run_repo_task(
        "acme/widget",
        error_task,
        "engineer",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-error",
        base_path=base,
        persona_dir=personas_dir,
        verify_cmd=["true"],
    )
    assert error.outcome == "pr-open"
    assert error_task in github._prs[error.pr.number].body

    structured_error_task = (
        "## What\ncomplete-now write-change drafting-errors structured error fallback.\n\n"
        "## Why\nPreserve structured context after a drafting exception.\n\n"
        "## Acceptance criteria\n- The deterministic fallback is published.\n\n"
        "## Additional info\nThis orchestration detail must not appear."
    )
    structured_error = run_repo_task(
        "acme/widget",
        structured_error_task,
        "engineer",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-structured-error",
        base_path=base,
        persona_dir=personas_dir,
        verify_cmd=["true"],
    )
    assert structured_error.outcome == "pr-open"
    assert github._prs[structured_error.pr.number].body == (
        "## What\ncomplete-now write-change drafting-errors structured error fallback.\n\n"
        "## Why\nPreserve structured context after a drafting exception.\n"
    )

    explicit = run_repo_task(
        "acme/widget",
        "complete-now write-change explicit body handoff",
        "engineer",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-explicit",
        body="## What\nSupplied body.\n\n## Why\nSupplied reason.\n",
        base_path=base,
        persona_dir=personas_dir,
        verify_cmd=["true"],
    )
    assert explicit.outcome == "pr-open"
    assert github._prs[explicit.pr.number].body == (
        "## What\nSupplied body.\n\n## Why\nSupplied reason.\n"
    )

    title_task = "complete-now write-change explicit title handoff"
    titled = run_repo_task(
        "acme/widget",
        title_task,
        "engineer",
        workspace=workspace,
        github=github,
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        branch="draft-title",
        title="feat: use supplied title",
        base_path=base,
        persona_dir=personas_dir,
        verify_cmd=["true"],
    )
    assert titled.outcome == "pr-open"
    assert github._prs[titled.pr.number].title == "feat: use supplied title"
    assert "Adds the completed behavior from the branch diff" in github._prs[titled.pr.number].body
    assert title_task not in github._prs[titled.pr.number].body


def test_empty_orchestrator_home_fails_at_lifecycle_cache_boundary(
    tmp_path, bare_origin, monkeypatch
) -> None:
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin)
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", "")

    with pytest.raises(ValueError, match="AI_ORCHESTRATOR_HOME must not be empty"):
        run_repo_task(
            str(origin),
            "never dispatched",
            "engineer",
            workspace=workspace,
            dispatch_fn=_per_step_dispatch(),
            repo_type="single-owner",
            workflow="local",
            branch="empty-home",
        )


def _advance_origin(tmp_path: Path, origin: Path, filename: str, content: str) -> str:
    """Commit one concurrent base-branch change through a separate real clone."""
    checkout = gitops.clone(origin, tmp_path / f"advance-{filename.replace('/', '-')}")
    path = checkout / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    gitops.add_all(checkout)
    sha = gitops.commit(checkout, f"advance main with {filename}")
    gitops.push(checkout, "main", set_upstream=False)
    return sha


def test_repo_plan_ledger_and_guided_next_round(
    tmp_path, bare_origin, command_base, personas_dir, capsys
) -> None:
    """The real CLI + onejudge + git lifecycle records and retries an unresolved node."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-ledger")
    Registry().register(str(canonical), workflow="local")
    runs_dir = tmp_path / "runs"
    plan_path = tmp_path / "repo-plan.json"
    first_plan = {
        "name": "ledger e2e",
        "tasks": [
            {
                "id": "change",
                "repo": str(canonical),
                "persona": "engineer",
                "task": "should-fail write-change: preserve this partial attempt",
                "verify_cmd": ["true"],
                "workflow": "local",
                "repo_type": "single-owner",
            }
        ],
    }
    plan_path.write_text(json.dumps(first_plan), encoding="utf-8")
    common = [
        "--base",
        str(command_base()),
        "--persona-dir",
        str(personas_dir),
        "--workspace",
        str(tmp_path / "workspace"),
        "--format",
        "json",
        # The node override must beat this command-wide override and preserve
        # the registered local publication path.
        "--repo-type",
        "team",
    ]
    rc = main_plan([str(plan_path), "--run", "fixed-run", "--runs-dir", str(runs_dir), *common])
    captured = capsys.readouterr()
    assert rc == 1 and json.loads(captured.out)["results"]["change"]["status"] == "failed"
    first_result = json.loads((runs_dir / "fixed-run" / "round-01" / "result.json").read_text())
    assert first_result["schema_version"] == 5
    preserved_branch = first_result["results"]["change"]["branch"]
    preserved_checkpoint = first_result["results"]["change"]["resume"]["checkpoint"]
    assert first_result["results"]["change"]["resume"]["mode"] == "retry"
    first = runs_dir / "fixed-run" / "round-01"
    assert json.loads((first / "plan.json").read_text()) == first_plan
    assert (first / "result.json").is_file()
    follow_up = "- Add a regression test for the adjacent edge case."
    assert "just next-round fixed-run [edits.json]" in captured.err

    edits = tmp_path / "edits.json"
    edits.write_text(
        json.dumps({"retry": {"change": {"task": "complete-now write-change: finish"}}}),
        encoding="utf-8",
    )
    rc = next_round_main(["fixed-run", str(edits), "--runs-dir", str(runs_dir), *common])
    captured = capsys.readouterr()
    second = runs_dir / "fixed-run" / "round-02"
    assert rc == 0 and (second / "plan.json").is_file() and (second / "result.json").is_file()
    second_result = json.loads((second / "result.json").read_text())
    assert second_result["results"]["change"]["status"] == "done"
    assert second_result["results"]["change"]["follow_ups"] == follow_up
    publication = second_result["results"]["change"]
    successful_gate_log = Path(publication["artifacts"]["gate_log"])
    assert successful_gate_log.is_file()
    assert publication["branch"] == preserved_branch
    assert publication["retry_lineage"] == {
        "supersedes_branch": preserved_branch,
        "supersedes_checkpoint": preserved_checkpoint,
        "disposition": "recovered",
        "supersedes_round": 1,
    }
    assert publication["repository_type"] == publication["repo_type"] == "single-owner"
    assert publication["publication_workflow"] == publication["workflow"] == "local"
    assert publication["merge_policy"] == "direct"
    assert publication["base_branch"] == publication["pr_base"] == "main"
    assert publication["synthetic_stack_base"] is None and publication["stack_bases"] == []
    assert follow_up in captured.out
    indexed = subprocess.run(
        ["just", "telemetry", "--runs-dir", str(runs_dir), "--all"],
        cwd=Path(__file__).parents[2],
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert indexed.returncode == 0, indexed.stderr
    telemetry = json.loads(indexed.stdout)
    assert telemetry["metrics"]["recovered_branches"] == 1

    assert telemetry["metrics"]["green_to_publication_seconds"]
    listed = subprocess.run(
        ["just", "runs", "--runs-dir", str(runs_dir)],
        cwd=Path(__file__).parents[2],
        text=True,
        capture_output=True,
        check=True,
    )
    runs_output = listed.stdout
    assert follow_up in runs_output
    assert f"just results fixed-run --runs-dir {runs_dir}" in runs_output
    assert "nothing to iterate" in captured.err

    unrecorded_plan = {
        **first_plan,
        "tasks": [
            {
                **first_plan["tasks"][0],
                "task": "complete-now write-unique-change: unrecorded schema output",
            }
        ],
    }
    plan_path.write_text(json.dumps(unrecorded_plan), encoding="utf-8")
    assert main_plan([str(plan_path), "--no-record", *common]) == 0
    unrecorded = json.loads(capsys.readouterr().out)
    assert unrecorded["schema_version"] == 5 and "round" not in unrecorded


def test_lifecycle_records_verified_change_already_integrated_on_base(
    tmp_path, bare_origin, command_base, personas_dir, capsys
) -> None:
    """The real CLI, provider seam, and local publisher reconcile an early landing."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-already-integrated")
    Registry().register(str(canonical), workflow="local")
    plan_path = tmp_path / "already-integrated.json"
    plan_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "change",
                        "repo": str(canonical),
                        "persona": "engineer",
                        "task": (
                            "complete-now write-change publish-change-to-base: "
                            "land before lifecycle closeout"
                        ),
                        "verify_cmd": ["true"],
                        "workflow": "local",
                        "repo_type": "single-owner",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    runs_dir = tmp_path / "runs"

    rc = main_plan(
        [
            str(plan_path),
            "--run",
            "already-integrated",
            "--runs-dir",
            str(runs_dir),
            "--base",
            str(command_base()),
            "--persona-dir",
            str(personas_dir),
            "--workspace",
            str(tmp_path / "workspace"),
            "--format",
            "json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    result = payload["results"]["change"]
    assert rc == 0, json.dumps(result, indent=2)
    assert payload["ok"] is True
    assert result["status"] == "done" and result["outcome"] == "already-integrated"
    assert "already present on main" in result["detail"]
    assert (canonical / "CHANGE.txt").read_text(encoding="utf-8") == "change from fake agent\n"
    recorded = json.loads(
        (runs_dir / "already-integrated" / "round-01" / "result.json").read_text()
    )
    assert recorded["results"]["change"]["outcome"] == "already-integrated"


def test_lifecycle_records_gate_failure_for_change_already_integrated_on_base(
    tmp_path, bare_origin, command_base, personas_dir, capsys
) -> None:
    """An early landing remains a gate failure when its real closeout gate fails."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-integrated-gate-failure")
    Registry().register(str(canonical), workflow="local")
    plan_path = tmp_path / "integrated-gate-failure.json"
    plan_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "change",
                        "repo": str(canonical),
                        "persona": "engineer",
                        "task": (
                            "complete-now write-change publish-change-to-base: "
                            "land invalid work before lifecycle closeout"
                        ),
                        "verify_cmd": ["false"],
                        "workflow": "local",
                        "repo_type": "single-owner",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    runs_dir = tmp_path / "runs"

    rc = main_plan(
        [
            str(plan_path),
            "--run",
            "integrated-gate-failure",
            "--runs-dir",
            str(runs_dir),
            "--base",
            str(command_base()),
            "--persona-dir",
            str(personas_dir),
            "--workspace",
            str(tmp_path / "workspace"),
            "--format",
            "json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    result = payload["results"]["change"]
    assert rc == 1
    assert payload["ok"] is False
    assert result["status"] == "failed" and result["outcome"] == "gate-failed"
    assert "already-integrated change failed local gate: false" in result["detail"]
    assert not (canonical / "CHANGE.txt").exists()
    assert _has_file(origin, "main", "CHANGE.txt")
    recorded = json.loads(
        (runs_dir / "integrated-gate-failure" / "round-01" / "result.json").read_text()
    )
    assert recorded["results"]["change"]["outcome"] == "gate-failed"


def test_lifecycle_fast_forwards_checkout_after_publication_race_is_already_integrated(
    tmp_path, bare_origin, command_base, personas_dir, capsys
) -> None:
    """The real local publisher reconciles a base advance during its gate."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-publication-race")
    Registry().register(str(canonical), workflow="local")
    plan_path = tmp_path / "publication-race.json"
    plan_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "change",
                        "repo": str(canonical),
                        "persona": "engineer",
                        "task": "complete-now write-change: race local publication",
                        "verify_cmd": [
                            "sh",
                            "-c",
                            "git symbolic-ref -q HEAD >/dev/null && "
                            "git push origin HEAD:main || true",
                        ],
                        "workflow": "local",
                        "repo_type": "single-owner",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    runs_dir = tmp_path / "runs"

    rc = main_plan(
        [
            str(plan_path),
            "--run",
            "publication-race",
            "--runs-dir",
            str(runs_dir),
            "--base",
            str(command_base()),
            "--persona-dir",
            str(personas_dir),
            "--workspace",
            str(tmp_path / "workspace"),
            "--format",
            "json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    result = payload["results"]["change"]
    assert rc == 0, json.dumps(result, indent=2)
    assert payload["ok"] is True
    assert result["status"] == "done" and result["outcome"] == "already-integrated"
    assert "no publication commit was needed" in result["detail"]
    assert (canonical / "CHANGE.txt").read_text(encoding="utf-8") == "change from fake agent\n"
    assert gitops.head_sha(canonical) == _tip(origin, "main")
    recorded = json.loads((runs_dir / "publication-race" / "round-01" / "result.json").read_text())
    assert recorded["results"]["change"]["outcome"] == "already-integrated"


# --- local repo: direct merge into main after checks -----------------------


@pytest.mark.parametrize(
    ("identity", "expected_wrapper", "human_pause"),
    [
        (AI_ORCHESTRATOR_IDENTITY, True, False),
        (AI_ORCHESTRATOR_IDENTITY, True, True),
        ("https://github.com/nickderobertis/llmlint", False, False),
        ("https://github.com/nickderobertis/llmlint", False, True),
    ],
)
def test_lifecycle_scopes_llmlint_wrapper_from_resolved_repository_identity(
    tmp_path, bare_origin, identity, expected_wrapper, human_pause
) -> None:
    """Resolved identity reaches worker and PR-author dispatches over real git."""

    class IdentityWorkspace(Workspace):
        def selection(self, repo):
            return replace(super().selection(repo), publication_identity=IdentityKey(identity))

    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical")
    seen: list[tuple[str, bool]] = []

    def writing_dispatch(persona, task, *, project_dir, use_llmlint_wrapper, **_):
        seen.append((persona, use_llmlint_wrapper))
        if persona == "pr-author":
            output = task.split(
                "Write the final body, and nothing else, to this absolute path:\n", 1
            )[1].splitlines()[0]
            Path(output).write_text(
                "## What\nRoutes the wrapper.\n\n## Why\nKeeps target gates isolated.\n",
                encoding="utf-8",
            )
            return Report(persona, 0, True, False, 1, [], {}, {}, "")
        Path(project_dir, "change.txt").write_text("change\n", encoding="utf-8")
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    result = run_repo_task(
        "acme/widget",
        "change wrapper routing" if not human_pause else None,
        "engineer" if not human_pause else None,
        workspace=IdentityWorkspace(
            tmp_path / "worktrees",
            resolver=lambda _spec: canonical,
            workflow="remote",
            repo_type="single-owner",
        ),
        url=str(origin),
        workflow="remote",
        repo_type="single-owner",
        merge_policy="none",
        github=FakeGitHub(origin),
        steps=(
            [
                Step("prepare", "engineer", "change wrapper routing"),
                Step("approve", task="Approve routing.", kind="human", deps=["prepare"]),
            ]
            if human_pause
            else None
        ),
        verify_cmd=["true"],
        dispatch_fn=writing_dispatch,
    )

    assert result.outcome == ("waiting-human" if human_pause else "pr-open")
    assert seen == [("engineer", expected_wrapper), ("pr-author", expected_wrapper)]


def test_registered_aliases_drive_real_lifecycle_without_a_stray_clone(
    tmp_path, bare_origin, command_base, personas_dir, capsys, monkeypatch
) -> None:
    """Repo and execution aliases reach the real onejudge lifecycle boundary."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical")
    safety = gitops.clone(origin, tmp_path / "safety")
    registry = Registry()
    registry.register(str(canonical), workflow="local")
    registry.register(str(safety))

    result = run_repo_task(
        "local/canonical",
        "complete-now write-change alias lifecycle",
        "engineer",
        workspace=Workspace(tmp_path / "worktrees"),
        execution_checkout="local/safety",
        base_path=command_base(),
        persona_dir=personas_dir,
        verify_cmd=["true"],
    )

    assert result.ok and result.outcome == "merged", result.detail
    assert Path(result.publication_checkout) == canonical
    assert Path(result.execution_checkout) == safety
    assert not (Path(os.environ["AI_ORCHESTRATOR_HOME"]) / "repos").exists()

    plan = tmp_path / "alias-plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "tasks": [
                    {
                        "id": "alias-node",
                        "repo": "local/canonical",
                        "persona": "engineer",
                        "task": "complete-now write-unique-change alias plan lifecycle",
                        "execution_checkout": "local/safety",
                        "verify_cmd": ["true"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    planned_rc = graph_module.main(
        [
            str(plan),
            "--no-record",
            "--workspace",
            str(tmp_path / "plan-worktrees"),
            "--base",
            str(command_base()),
            "--persona-dir",
            str(personas_dir),
            "--format",
            "json",
        ]
    )
    planned = json.loads(capsys.readouterr().out)

    assert planned_rc == 0
    planned_result = planned["results"]["alias-node"]
    assert planned_result["outcome"] == "merged"
    assert Path(planned_result["publication_checkout"]) == canonical
    assert Path(planned_result["execution_checkout"]) == safety
    assert not (Path(os.environ["AI_ORCHESTRATOR_HOME"]) / "repos").exists()

    remote_origin = bare_origin()
    remote_checkout = gitops.clone(remote_origin, tmp_path / "crozier")
    remote_registry = Registry()
    remote_registry.entries[Slug("nickderobertis/crozier")] = RegistryEntry(
        str(remote_checkout.resolve()),
        str(remote_origin),
        "remote",
        "single-owner",
    )
    remote_registry.save()
    monkeypatch.setattr(lifecycle_module, "CliGitHubBackend", lambda: FakeGitHub(remote_origin))
    remote_plan = tmp_path / "remote-alias-plan.json"
    remote_plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "tasks": [
                    {
                        "id": "remote-alias",
                        "repo": "nickderobertis/crozier",
                        "persona": "engineer",
                        "task": "complete-now write-change registered remote alias",
                        "verify_cmd": ["true"],
                        "merge_policy": "none",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    remote_rc = graph_module.main(
        [
            str(remote_plan),
            "--no-record",
            "--workspace",
            str(tmp_path / "remote-plan-worktrees"),
            "--base",
            str(command_base()),
            "--persona-dir",
            str(personas_dir),
            "--format",
            "json",
        ]
    )
    remote = json.loads(capsys.readouterr().out)["results"]["remote-alias"]

    assert remote_rc == 0
    assert remote["outcome"] == "pr-open"
    assert Path(remote["publication_checkout"]) == remote_checkout
    assert remote["workflow"] == "remote" and remote["repo_type"] == "single-owner"


def test_local_identity_executes_in_safety_clone_and_publishes_without_pr(
    tmp_path, bare_origin
) -> None:
    """The self-dispatch safety clone does not alter the identity's local workflow."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "ai-orchestrator")
    safety = gitops.clone(origin, tmp_path / "ai-orchestrator-isolated")
    registry = Registry()
    registry.register(str(canonical), workflow="local")
    registry.register(str(safety))
    github = FakeGitHub(origin)

    result = run_repo_task(
        str(canonical),
        "Change the git subsystem from an isolated safety clone.",
        "engineer",
        workspace=Workspace(tmp_path / "worktrees"),
        execution_checkout=safety,
        github=github,
        dispatch_fn=make_writing_dispatch(filename="git-subsystem-change.txt"),
        verify_cmd=["true"],
    )

    assert result.ok and result.outcome == "merged", result.detail
    assert github._n == 0
    assert Path(result.execution_checkout) == safety
    assert Path(result.publication_checkout) == canonical
    assert result.publication_workflow == "local"
    assert result.publication_identity == str(origin).removesuffix(".git")
    assert _has_file(origin, "main", "git-subsystem-change.txt")
    assert gitops.head_sha(canonical) == _tip(origin, "main")
    rendered = result.summary()
    assert f"execution checkout: {safety}" in rendered
    assert "publication workflow: local" in rendered


def test_safety_clone_refuses_publication_checkout_on_nonroot_branch(tmp_path, bare_origin) -> None:
    """A safety-clone run must not fast-forward whichever canonical branch is active."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-nonroot")
    safety = gitops.clone(origin, tmp_path / "safety-nonroot")
    Registry().register(str(canonical), workflow="local")
    Registry().register(str(safety))
    subprocess.run(
        ["git", "-C", str(canonical), "switch", "-c", "operator/wip"],
        check=True,
        capture_output=True,
    )
    dispatched: list[str] = []

    def dispatch_fn(persona: str, task: str, **_: object) -> Report:
        dispatched.append(task)
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    result = run_repo_task(
        str(canonical),
        "Do not mutate the operator branch.",
        "engineer",
        workspace=Workspace(tmp_path / "nonroot-worktrees"),
        execution_checkout=safety,
        dispatch_fn=dispatch_fn,
        verify_cmd=["true"],
    )

    assert result.outcome == "error"
    assert "publication checkout" in result.detail and "root branch 'main'" in result.detail
    assert dispatched == []
    assert gitops.current_branch(canonical) == "operator/wip"
    assert _tip(origin, "main") == gitops.head_sha(canonical)


def test_registered_remote_identity_keeps_pr_flow(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-remote")
    Registry().register(str(canonical), workflow="remote", repo_type="single-owner")
    github = FakeGitHub(origin)

    result = run_repo_task(
        str(canonical),
        "Publish this identity through review.",
        "engineer",
        workspace=Workspace(tmp_path / "remote-worktrees"),
        github=github,
        dispatch_fn=make_writing_dispatch(filename="reviewed.txt"),
        verify_cmd=["true"],
        sleep=lambda _: None,
    )

    assert result.ok and result.outcome == "merged", result.detail
    assert result.publication_workflow == "remote"
    assert result.pr is not None and result.pr.number == 1
    assert github._n == 1
    assert _has_file(origin, "main", "reviewed.txt")
    assert result.repository_type == "single-owner"
    assert result.merge_policy == "auto"
    assert result.pr_base == "main"


def test_verify_via_ci_iterates_real_dispatch_then_requires_green_branch_ci(
    tmp_path, bare_origin, command_base, personas_dir, monkeypatch, capsys
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-ci")
    Registry().register(str(canonical), workflow="remote", repo_type="single-owner")
    push_log = tmp_path / "ci-pushes.log"
    hook = origin / "hooks" / "post-receive"
    hook.write_text(
        "#!/bin/sh\n"
        "while read old new ref; do\n"
        f'  git show "$new:CI_STATE.txt" 2>/dev/null >> {shlex.quote(str(push_log))} || true\n'
        "done\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    github = FakeGitHub(origin)
    monkeypatch.setattr(lifecycle_module, "CliGitHubBackend", lambda: github)

    green_rc = main_task(
        [
            str(canonical),
            "engineer",
            "ci-iterate exercise authoritative CI",
            "--verify-via-ci",
            "--workspace",
            str(tmp_path / "ci-worktrees"),
            "--base",
            str(command_base(max_turns=2)),
            "--persona-dir",
            str(personas_dir),
            "--poll-interval",
            "0",
            "--format",
            "json",
        ]
    )
    green = json.loads(capsys.readouterr().out)

    assert green_rc == 0 and green["outcome"] == "merged", green["detail"]
    assert push_log.read_text(encoding="utf-8").splitlines()[:2] == ["RED", "GREEN"]
    assert _has_file(origin, "main", "CI_STATE.txt")
    assert github._n == 1  # CI pre-verification and publication reuse one PR

    red_github = FakeGitHub(origin, fail_checks=True)
    red = run_repo_task(
        str(canonical),
        "complete-now write-change leave CI red",
        "engineer",
        workspace=Workspace(tmp_path / "red-worktrees"),
        github=red_github,
        verify_via_ci=True,
        base_path=command_base(max_turns=2),
        persona_dir=personas_dir,
        sleep=lambda _: None,
    )

    assert red.outcome == "not-completed"
    assert "ci=FAILURE" in red.detail
    assert red.pr is not None


def test_verify_via_ci_real_cli_rejects_local_and_tracked_node_can_opt_out(
    tmp_path, bare_origin, command_base, personas_dir, monkeypatch, capsys
) -> None:
    local_origin = bare_origin()
    local_checkout = gitops.clone(local_origin, tmp_path / "local-ci-checkout")
    Registry().register(str(local_checkout), workflow="local", repo_type="single-owner")

    rejected = main_task(
        [
            str(local_checkout),
            "engineer",
            "complete-now write-change",
            "--verify-via-ci",
            "--workspace",
            str(tmp_path / "local-cli-worktrees"),
            "--base",
            str(command_base(max_turns=2)),
            "--persona-dir",
            str(personas_dir),
            "--format",
            "json",
        ]
    )
    rejected_payload = json.loads(capsys.readouterr().out)
    assert rejected == 1
    assert "remote GitHub/PR workflow" in rejected_payload["detail"]

    remote_origin = bare_origin()
    remote_checkout = gitops.clone(remote_origin, tmp_path / "remote-ci-checkout")
    Registry().register(str(remote_checkout), workflow="remote", repo_type="single-owner")
    github = FakeGitHub(remote_origin, fail_checks=True)
    monkeypatch.setattr(lifecycle_module, "CliGitHubBackend", lambda: github)
    plan = tmp_path / "verify-via-ci-plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "tasks": [
                    {
                        "id": "opt-out",
                        "repo": str(remote_checkout),
                        "persona": "engineer",
                        "task": "complete-now write-change",
                        "verify_via_ci": False,
                        "merge_policy": "none",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    graph_rc = graph_module.main(
        [
            str(plan),
            "--verify-via-ci",
            "--no-record",
            "--workspace",
            str(tmp_path / "graph-worktrees"),
            "--base",
            str(command_base(max_turns=2)),
            "--persona-dir",
            str(personas_dir),
            "--format",
            "json",
        ]
    )
    graph_payload_result = json.loads(capsys.readouterr().out)
    assert graph_rc == 0
    assert graph_payload_result["results"]["opt-out"]["outcome"] == "pr-open"

    inherited_github = FakeGitHub(remote_origin)
    monkeypatch.setattr(lifecycle_module, "CliGitHubBackend", lambda: inherited_github)
    inherited_plan = tmp_path / "inherited-verify-via-ci-plan.json"
    inherited_plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "tasks": [
                    {
                        "id": "ci-default",
                        "repo": str(remote_checkout),
                        "persona": "engineer",
                        "task": "complete-now write-change",
                        "verify_cmd": ["false"],
                        "merge_policy": "none",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    inherited_rc = graph_module.main(
        [
            str(inherited_plan),
            "--verify-via-ci",
            "--no-record",
            "--workspace",
            str(tmp_path / "inherited-graph-worktrees"),
            "--base",
            str(command_base(max_turns=2)),
            "--persona-dir",
            str(personas_dir),
            "--format",
            "json",
        ]
    )
    inherited_payload = json.loads(capsys.readouterr().out)
    assert inherited_rc == 0
    assert inherited_payload["results"]["ci-default"]["outcome"] == "pr-open"

    node_opt_in_plan = tmp_path / "node-verify-via-ci-plan.json"
    node_opt_in_plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "tasks": [
                    {
                        "id": "ci-node-opt-in",
                        "repo": str(remote_checkout),
                        "persona": "engineer",
                        "task": "complete-now write-unique-change node CI opt-in",
                        "verify_via_ci": True,
                        "verify_cmd": ["false"],
                        "merge_policy": "none",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    node_opt_in_rc = graph_module.main(
        [
            str(node_opt_in_plan),
            "--no-record",
            "--workspace",
            str(tmp_path / "node-opt-in-worktrees"),
            "--base",
            str(command_base(max_turns=2)),
            "--persona-dir",
            str(personas_dir),
            "--format",
            "json",
        ]
    )
    node_opt_in_payload = json.loads(capsys.readouterr().out)
    assert node_opt_in_rc == 0
    assert node_opt_in_payload["results"]["ci-node-opt-in"]["outcome"] == "pr-open"


@pytest.mark.parametrize(
    ("check_states", "expected_detail"),
    [
        (("PENDING",), "ci=PENDING"),
        ((None,), "required checks: []"),
    ],
)
def test_verify_via_ci_rejects_unsettled_or_absent_required_checks(
    tmp_path, bare_origin, check_states, expected_detail
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-unsettled-ci")
    Registry().register(str(canonical), workflow="remote", repo_type="single-owner")

    result = run_repo_task(
        str(canonical),
        "Leave authoritative CI incomplete.",
        "engineer",
        workspace=Workspace(tmp_path / "unsettled-ci-worktrees"),
        github=FakeGitHub(origin, check_states=check_states),
        verify_via_ci=True,
        dispatch_fn=make_writing_dispatch(filename="unsettled.txt"),
        sleep=lambda _: None,
    )

    assert result.outcome == "not-completed"
    assert expected_detail in result.detail


def test_team_default_opens_ready_for_review_pr_without_polling(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-team")
    Registry().register(str(canonical), workflow="remote", repo_type="team")
    github = FakeGitHub(origin, fail_checks=True)

    result = run_repo_task(
        str(canonical),
        "Open a team-owned change for review.",
        "engineer",
        workspace=Workspace(tmp_path / "team-worktrees"),
        github=github,
        dispatch_fn=make_writing_dispatch(filename="team.txt"),
        verify_cmd=["true"],
    )

    assert result.ok and result.outcome == "pr-open", result.detail
    assert result.repository_type == "team"
    assert result.publication_workflow == "remote" and result.merge_policy == "none"
    assert result.pr is not None and result.pr.base == "main"
    assert not _has_file(origin, "main", "team.txt")
    assert _has_file(origin, result.branch, "team.txt")


def test_team_explicit_auto_merges_remote_pr(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-team-auto")
    Registry().register(str(canonical), workflow="remote", repo_type="team")

    result = run_repo_task(
        str(canonical),
        "Merge an explicitly automated team change.",
        "engineer",
        workspace=Workspace(tmp_path / "team-auto-worktrees"),
        github=FakeGitHub(origin),
        dispatch_fn=make_writing_dispatch(filename="team-auto.txt"),
        verify_cmd=["true"],
        merge_policy="auto",
        sleep=lambda _: None,
    )

    assert result.ok and result.outcome == "merged", result.detail
    assert result.merge_policy == "auto" and result.publication_workflow == "remote"
    assert _has_file(origin, "main", "team-auto.txt")


@pytest.mark.parametrize(("repo_type", "should_wait"), [("single-owner", True), ("team", False)])
def test_remote_lifecycle_routes_only_single_owner_auto_merge_through_queue(
    tmp_path, bare_origin, repo_type, should_wait
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / f"canonical-routing-{repo_type}")
    Registry().register(str(canonical), workflow="remote", repo_type=repo_type)

    result = _run_while_merge_turn_is_held(
        canonical,
        lambda: run_repo_task(
            str(canonical),
            "Publish an automated remote change.",
            "engineer",
            workspace=Workspace(tmp_path / f"routing-{repo_type}-worktrees"),
            github=FakeGitHub(origin),
            dispatch_fn=make_writing_dispatch(filename=f"routing-{repo_type}.txt"),
            verify_cmd=["true"],
            merge_policy="auto",
            sleep=lambda _: None,
        ),
        should_wait=should_wait,
    )

    assert result.ok and result.outcome == "merged", result.detail
    assert result.repository_type == repo_type and result.merge_policy == "auto"
    assert _has_file(origin, "main", f"routing-{repo_type}.txt")


@pytest.mark.parametrize(("repo_type", "should_wait"), [("single-owner", True), ("team", False)])
def test_remote_recovery_routes_only_single_owner_auto_merge_through_queue(
    tmp_path, bare_origin, repo_type, should_wait
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / f"canonical-recovery-routing-{repo_type}")
    Registry().register(str(canonical), workflow="remote", repo_type=repo_type, gate="true")
    branch = f"feature/recovery-routing-{repo_type}"
    preserved = run_repo_task(
        str(canonical),
        "Preserve an incomplete remote change.",
        "engineer",
        workspace=Workspace(tmp_path / f"preserve-routing-{repo_type}-worktrees"),
        branch=branch,
        dispatch_fn=make_writing_dispatch(
            filename=f"recovery-routing-{repo_type}.txt", completed=False
        ),
        verify_cmd=["true"],
    )
    assert preserved.outcome == "not-completed" and preserved.resume is not None

    recovered = _run_while_merge_turn_is_held(
        canonical,
        lambda: recover_repo(
            canonical,
            branch,
            workspace_root=tmp_path / f"recovery-routing-{repo_type}-worktrees",
            github=FakeGitHub(origin),
            verify_cmd=["true"],
            merge_policy="auto",
        ),
        should_wait=should_wait,
    )

    assert recovered.ok and recovered.outcome == "merged", recovered.detail
    assert recovered.repo_type == repo_type and recovered.merge_policy == "auto"
    assert _has_file(origin, "main", f"recovery-routing-{repo_type}.txt")


def test_local_single_owner_none_opens_pr_without_mutating_stored_workflow(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-local-none")
    registry = Registry()
    registry.register(str(canonical), workflow="local", repo_type="single-owner")

    result = run_repo_task(
        str(canonical),
        "Temporarily publish local work for review.",
        "engineer",
        workspace=Workspace(tmp_path / "local-none-worktrees"),
        github=FakeGitHub(origin),
        dispatch_fn=make_writing_dispatch(filename="local-none.txt"),
        verify_cmd=["true"],
        merge_policy="none",
    )

    assert result.ok and result.outcome == "pr-open", result.detail
    assert result.publication_workflow == "remote" and result.merge_policy == "none"
    stored = Registry().identity_for_checkout(canonical)
    assert stored is not None and stored.workflow == "local"
    assert not _has_file(origin, "main", "local-none.txt")


@pytest.mark.parametrize(
    (
        "repo_type",
        "identity_workflow",
        "merge_policy",
        "expected_workflow",
        "expected_policy",
        "expected_outcome",
    ),
    [
        ("single-owner", "local", None, "local", "direct", "merged"),
        ("single-owner", "local", "auto", "local", "direct", "merged"),
        ("single-owner", "local", "direct", "local", "direct", "merged"),
        ("single-owner", "local", "none", "remote", "none", "pr-open"),
        ("single-owner", "remote", None, "remote", "auto", "merged"),
        ("single-owner", "remote", "auto", "remote", "auto", "merged"),
        ("single-owner", "remote", "direct", "remote", "direct", "merged"),
        ("single-owner", "remote", "none", "remote", "none", "pr-open"),
        ("team", "local", None, "remote", "none", "pr-open"),
        ("team", "local", "auto", "remote", "auto", "merged"),
        ("team", "local", "direct", "remote", "direct", "merged"),
        ("team", "local", "none", "remote", "none", "pr-open"),
        ("team", "remote", None, "remote", "none", "pr-open"),
        ("team", "remote", "auto", "remote", "auto", "merged"),
        ("team", "remote", "direct", "remote", "direct", "merged"),
        ("team", "remote", "none", "remote", "none", "pr-open"),
    ],
)
def test_public_lifecycle_type_workflow_policy_matrix(
    tmp_path,
    bare_origin,
    repo_type,
    identity_workflow,
    merge_policy,
    expected_workflow,
    expected_policy,
    expected_outcome,
) -> None:
    """Every supported decision reaches the expected real Git publication boundary."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-policy-matrix")
    stored_type = "single-owner" if identity_workflow == "local" else repo_type
    registry = Registry()
    registry.register(str(canonical), workflow=identity_workflow, repo_type=stored_type)
    github = FakeGitHub(origin, fail_checks=expected_outcome == "pr-open")

    result = run_repo_task(
        str(canonical),
        "Exercise one effective publication decision.",
        "engineer",
        workspace=Workspace(tmp_path / "policy-matrix-worktrees"),
        github=github,
        repo_type=repo_type,
        merge_policy=merge_policy,
        dispatch_fn=make_writing_dispatch(filename="matrix.txt"),
        verify_cmd=["true"],
        sleep=lambda _: None,
    )

    assert result.ok and result.outcome == expected_outcome, result.detail
    assert result.repository_type == repo_type
    assert result.publication_workflow == expected_workflow
    assert result.merge_policy == expected_policy
    assert (github._n == 0) is (expected_workflow == "local")
    assert _has_file(origin, "main", "matrix.txt") is (expected_outcome == "merged")
    rendered = result.summary()
    assert f"repository type: {repo_type}" in rendered
    assert f"publication workflow: {expected_workflow}" in rendered
    assert f"merge policy: {expected_policy}" in rendered
    if repo_type == "team" and identity_workflow == "local":
        stored = Registry().identity_for_checkout(canonical)
        assert stored is not None
        assert stored.repo_type == "single-owner" and stored.workflow == "local"


def test_local_repo_direct_merge(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    prior_base = _tip(origin, "main")
    ws = _workspace(tmp_path, origin)
    result = run_repo_task(
        str(origin),  # a local path → local direct-merge strategy is auto-selected
        "Add a change file.",
        "engineer",
        workspace=ws,
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["true"],  # explicit gate so the test never depends on make/just
    )
    assert result.ok, result.detail
    assert result.outcome == "merged"
    assert result.base_branch == "main"
    assert _has_file(origin, "main", "feature.txt")  # the change really landed on origin main
    canonical = ws.execution_checkout(normalize_repo(str(origin)))
    assert gitops.current_branch(canonical) == "main"
    assert gitops.head_sha(canonical) == _tip(origin, "main")
    landed = _tip(origin, "main")
    assert (
        subprocess.run(
            ["git", "-C", str(origin), "rev-list", "--count", f"{prior_base}..{landed}"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        == "1"
    )
    assert (
        subprocess.run(
            ["git", "-C", str(origin), "show", "-s", "--format=%P", landed],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        == prior_base
    )
    assert (
        subprocess.run(
            ["git", "-C", str(origin), "show", "-s", "--format=%s", landed],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        == "chore: Add a change file."
    )
    branch_commits = {
        commit.sha for commit in gitops.log_delta(canonical, prior_base, result.branch)
    }
    base_commits = set(
        subprocess.run(
            ["git", "-C", str(origin), "rev-list", "main"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.splitlines()
    )
    assert landed not in branch_commits
    assert branch_commits.isdisjoint(base_commits)


def test_local_merge_gate_does_not_hold_the_shared_git_lock(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    ws = _workspace(tmp_path, origin)
    verification_started = tmp_path / "verification-started"
    verification_release = tmp_path / "verification-release"
    gate = [
        "sh",
        "-c",
        "if git symbolic-ref -q HEAD >/dev/null; then exit 0; fi; "
        f"touch {shlex.quote(str(verification_started))}; "
        f"while test ! -f {shlex.quote(str(verification_release))}; do sleep 0.01; done",
    ]

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            run_repo_task,
            str(origin),
            "Add a change while publication verification pauses.",
            "engineer",
            workspace=ws,
            dispatch_fn=make_writing_dispatch(filename="feature.txt"),
            verify_cmd=gate,
        )
        deadline = e2e_deadline(10)
        while not verification_started.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert verification_started.exists(), "publication verification did not start"

        clone = ws.clone_dir(normalize_repo(str(origin)))
        identity = git_lock_identity(gitops.common_dir(clone))
        try:
            with advisory_lock(identity, timeout=0.5):
                gitops.fetch(clone)
        finally:
            verification_release.touch()
        result = future.result(timeout=e2e_timeout(10))

    assert result.ok and result.outcome == "merged", result.detail
    assert _has_file(origin, "main", "feature.txt")


def test_local_repo_non_main_default_and_gate_context(tmp_path, bare_origin) -> None:
    origin = bare_origin(branch="master")
    ws = _workspace(tmp_path, origin)
    result = run_repo_task(
        str(origin),
        "Add a portable change.",
        "engineer",
        workspace=ws,
        dispatch_fn=make_writing_dispatch(filename="portable.txt"),
        verify_cmd=[
            "sh",
            "-c",
            'test "$ORCHESTRATOR_COMPARISON_REMOTE/$ORCHESTRATOR_COMPARISON_BASE" = origin/master',
        ],
    )
    assert result.ok, result.detail
    assert result.base_branch == "master"
    assert _has_file(origin, "master", "portable.txt")


def test_base_advance_during_verification_rebuilds_and_reverifies_local_merge(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    initial = _tip(origin, "main")
    ready = tmp_path / "publisher-b-ready"
    verification_log = tmp_path / "publication-gates.log"
    quoted_ready = shlex.quote(str(ready))
    quoted_log = shlex.quote(str(verification_log))
    quoted_origin = shlex.quote(str(origin))
    gate = [
        "sh",
        "-c",
        "if git symbolic-ref -q HEAD >/dev/null; then exit 0; fi; "
        f"if test -f machine-b.txt && test ! -f machine-a.txt; then "
        f"touch {quoted_ready}; "
        f'while test "$(git ls-remote {quoted_origin} refs/heads/main | cut -f1)" = {initial}; '
        "do sleep 0.01; done; "
        f"echo b-stale >> {quoted_log}; "
        f"elif test -f machine-b.txt; then echo b-rebuilt >> {quoted_log}; "
        f"else while test ! -f {quoted_ready}; do sleep 0.01; done; "
        f"echo a >> {quoted_log}; fi",
    ]

    def publish(machine: str):
        workspace_root = tmp_path / machine
        ws = _workspace(workspace_root, origin)
        return run_repo_task(
            str(origin),
            f"Publish from {machine}.",
            "engineer",
            workspace=ws,
            branch=f"feature/{machine}",
            dispatch_fn=make_writing_dispatch(filename=f"{machine}.txt"),
            verify_cmd=gate,
            publication_attempts=3,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, ("machine-a", "machine-b")))

    assert all(result.ok and result.outcome == "merged" for result in results)
    assert _has_file(origin, "main", "machine-a.txt")
    assert _has_file(origin, "main", "machine-b.txt")
    publication_gates = verification_log.read_text(encoding="utf-8").splitlines()
    # Publisher B verifies once against the initial base, observes A's base advance,
    # then rebuilds and verifies a second time before its merge may be pushed.
    assert publication_gates.count("b-stale") == 1
    assert publication_gates.count("b-rebuilt") == 1


def test_local_repo_gate_failure_blocks_merge(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    result = run_repo_task(
        str(origin),
        "Add a change that fails the gate.",
        "engineer",
        workspace=_workspace(tmp_path, origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["sh", "-c", "printf 'lint tier: bad import\\n'; exit 1"],
    )
    assert not result.ok
    assert result.outcome == "gate-failed"
    assert result.pr is None
    assert "lint tier: bad import" in result.detail
    assert _tip(origin, "main") == before  # origin main untouched


def test_local_repo_syncs_advanced_base_before_gate(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    ws = _workspace(tmp_path, origin)
    writing_dispatch = make_writing_dispatch(filename="feature.txt")
    advanced_sha = ""

    def dispatch_after_base_advances(
        persona: str, task: str, *, project_dir: str, **kwargs: object
    ) -> Report:
        nonlocal advanced_sha
        report = writing_dispatch(persona, task, project_dir=project_dir, **kwargs)
        advanced_sha = _advance_origin(tmp_path, origin, "base.txt", "new base\n")
        return report

    result = run_repo_task(
        str(origin),
        "Add a feature while main advances.",
        "engineer",
        workspace=ws,
        dispatch_fn=dispatch_after_base_advances,
        verify_cmd=[
            "sh",
            "-c",
            "test -f base.txt && git merge-base --is-ancestor origin/main HEAD",
        ],
    )

    assert result.ok and result.outcome == "merged"
    assert result.verify is not None and result.verify.ok
    assert _has_file(origin, "main", "base.txt")
    assert _has_file(origin, "main", "feature.txt")
    assert (
        subprocess.run(
            ["git", "-C", str(origin), "merge-base", "--is-ancestor", advanced_sha, "main"]
        ).returncode
        == 0
    )


def test_local_conflict_resolves_outside_queue_then_requeues_and_merges(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin({"shared.txt": "original\n"})
    workspace = _workspace(tmp_path, origin)
    dispatched = threading.Barrier(2)
    resolution_calls: list[str] = []

    def concurrent_dispatch(
        persona: str, task: str, *, project_dir: str, **kwargs: object
    ) -> Report:
        path = Path(project_dir) / "shared.txt"
        if "Resolve the content conflict" in task:
            assert "<<<<<<<" in path.read_text(encoding="utf-8")
            resolution_calls.append(str(kwargs["session"]))
            path.write_text("first branch\nsecond branch\n", encoding="utf-8")
            gitops.add_all(project_dir)
        else:
            content = "first branch\n" if "first" in task else "second branch\n"
            path.write_text(content, encoding="utf-8")
            dispatched.wait(timeout=e2e_timeout(10))
        return Report(persona, 0, True, False, 2, [], {}, {}, "")

    def run(name: str):
        return run_repo_task(
            str(origin),
            f"Edit the shared file from the {name} local run.",
            "engineer",
            branch=f"feature/{name}-local-conflict",
            workspace=workspace,
            dispatch_fn=concurrent_dispatch,
            verify_cmd=["git", "diff", "--check", "origin/main...HEAD"],
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(run, "first")
        second_future = pool.submit(run, "second")
        results = [
            first_future.result(timeout=e2e_timeout(30)),
            second_future.result(timeout=e2e_timeout(30)),
        ]

    assert [result.outcome for result in results] == ["merged", "merged"]
    assert len(resolution_calls) == 1
    assert resolution_calls[0].endswith(":main")
    final = subprocess.run(
        ["git", "-C", str(origin), "show", "main:shared.txt"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    assert final == "first branch\nsecond branch\n"


@pytest.mark.parametrize("resolver_commits", [False, True])
def test_local_conflict_incomplete_resolver_preserves_branch(
    tmp_path, bare_origin, resolver_commits: bool
) -> None:
    origin = bare_origin({"shared.txt": "original\n"})
    initial = make_writing_dispatch(filename="shared.txt", content="preserved")

    def dispatch_fn(persona: str, task: str, *, project_dir: str, **kwargs: object) -> Report:
        if "Resolve the content conflict" not in task:
            report = initial(persona, task, project_dir=project_dir, **kwargs)
            _advance_origin(tmp_path, origin, "shared.txt", "advanced\n")
            return report
        if resolver_commits:
            path = Path(project_dir) / "shared.txt"
            path.write_text("advanced\npreserved by engineer\n", encoding="utf-8")
            gitops.add_all(project_dir)
        return Report(persona, 1, False, False, 2, [], {}, {}, "")

    result = run_repo_task(
        str(origin),
        "Create a conflicting local edit.",
        "engineer",
        workspace=_workspace(tmp_path, origin),
        dispatch_fn=dispatch_fn,
        verify_cmd=["true"],
    )

    assert result.outcome == "sync-conflict"
    assert "did not complete" in result.detail
    assert not _has_file(origin, result.branch, "shared.txt")


def test_local_conflict_retry_resumes_committed_branch(tmp_path, bare_origin) -> None:
    origin = bare_origin({"shared.txt": "original\n"})
    initial = make_writing_dispatch(filename="shared.txt", content="preserved")
    resolutions = 0
    initial_runs = 0
    finish_resolution = False

    def dispatch_fn(persona: str, task: str, *, project_dir: str, **kwargs: object) -> Report:
        nonlocal initial_runs, resolutions
        if "Resolve the content conflict" not in task:
            initial_runs += 1
            report = initial(persona, task, project_dir=project_dir, **kwargs)
            _advance_origin(tmp_path, origin, "shared.txt", "advanced\n")
            return report
        resolutions += 1
        if finish_resolution:
            (Path(project_dir) / "shared.txt").write_text("advanced\npreserved\n", encoding="utf-8")
            gitops.add_all(project_dir)
        return Report(persona, 0, True, False, 2, [], {}, {}, "")

    workspace = _workspace(tmp_path, origin)
    result = run_repo_task(
        str(origin),
        "Create a persistently conflicting local edit.",
        "engineer",
        workspace=workspace,
        dispatch_fn=dispatch_fn,
        verify_cmd=["true"],
    )

    assert result.outcome == "sync-conflict"
    assert resolutions == MAX_MERGE_CONFLICT_RESOLUTIONS
    assert f"after {MAX_MERGE_CONFLICT_RESOLUTIONS} resolve-and-requeue cycles" in result.detail
    assert result.resume is not None and result.resume.mode == "retry"
    preserved_branch = result.branch
    preserved_checkpoint = result.resume.checkpoint
    prior_plan = {
        "tasks": [
            {
                "id": "change",
                "repo": str(origin),
                "persona": "engineer",
                "task": "Create a persistently conflicting local edit.",
                "verify_cmd": ["true"],
            }
        ]
    }
    retry_plan = next_round(
        prior_plan,
        {"round": 1, "results": {"change": {"status": "failed", **result_payload(result)}}},
        {"retry": {"change": {}}},
    )
    assert retry_plan["tasks"][0]["resume"]["checkpoint"] == preserved_checkpoint
    retry_plan_path = tmp_path / "retry-plan.json"
    retry_plan_path.write_text(json.dumps(retry_plan), encoding="utf-8")
    parsed_retry = load_repo_plan(retry_plan_path).tasks[0]
    assert parsed_retry.resume is not None
    assert parsed_retry.resume.completed_steps == ("main",)

    finish_resolution = True
    retried = run_repo_task(
        str(origin),
        "Create a persistently conflicting local edit.",
        "engineer",
        workspace=workspace,
        dispatch_fn=dispatch_fn,
        verify_cmd=["true"],
        resume=parsed_retry.resume,
    )

    assert retried.outcome == "merged", retried.detail
    assert retried.branch == preserved_branch
    assert initial_runs == 1
    assert gitops.is_ancestor(
        workspace.clone_dir(normalize_repo(str(origin))), preserved_checkpoint, preserved_branch
    )
    assert _has_file(origin, "main", "shared.txt")


def test_failed_lifecycle_without_commits_retries_fresh(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin)

    def no_work(persona: str, task: str, *, project_dir: str, **kwargs: object) -> Report:
        return Report(persona, 1, False, False, 1, [], {}, {}, "")

    failed = run_repo_task(
        str(origin),
        "Fail before producing work.",
        "engineer",
        workspace=workspace,
        dispatch_fn=no_work,
        verify_cmd=["true"],
    )

    assert failed.outcome == "not-completed"
    assert failed.resume is None
    retry_plan = next_round(
        {
            "tasks": [
                {
                    "id": "change",
                    "repo": str(origin),
                    "persona": "engineer",
                    "task": "Fail before producing work.",
                    "verify_cmd": ["true"],
                }
            ]
        },
        {"round": 1, "results": {"change": {"status": "failed", **result_payload(failed)}}},
        {"retry": {"change": {}}},
    )
    assert "resume" not in retry_plan["tasks"][0]

    retried = run_repo_task(
        str(origin),
        "Produce work on the fresh retry.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="fresh.txt", content="fresh"),
        verify_cmd=["true"],
    )

    assert retried.outcome == "merged", retried.detail
    assert retried.branch != failed.branch
    assert _has_file(origin, "main", "fresh.txt")


def test_local_repo_registry_gate_verifies_real_worktree(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "registered")
    marker = tmp_path / "gate-ran"
    Registry().register(
        str(canonical),
        workflow="local",
        repo_type="single-owner",
        gate=(
            f'sh -c \'test "$1" = origin/main && test -f feature.txt '
            f"&& touch {marker}' -- {{base}}"
        ),
    )
    result = run_repo_task(
        str(canonical),
        "Add a change verified by the identity gate.",
        "engineer",
        workspace=Workspace(tmp_path / "worktrees"),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
    )
    assert result.ok and result.outcome == "merged"
    assert result.verify is not None and result.verify.ok
    assert marker.exists()
    assert _has_file(origin, "main", "feature.txt")


def test_local_repo_registry_gate_failure_stops_publication(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "registered")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner", gate="false")
    result = run_repo_task(
        str(canonical),
        "Add a change rejected by the identity gate.",
        "engineer",
        workspace=Workspace(tmp_path / "worktrees"),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
    )
    assert result.outcome == "gate-failed"
    assert result.verify is not None and not result.verify.ok
    assert not _has_file(origin, "main", "feature.txt")


def test_registered_complete_gate_catches_strict_tier_before_publish_then_allows_clean_work(
    tmp_path, bare_origin
) -> None:
    gate_log = tmp_path / "complete-gate.log"
    origin = bare_origin(
        {
            "Makefile": (
                ".PHONY: check strict-tier gate\n"
                "check:\n"
                f"\t@printf 'check\\n' >> {shlex.quote(str(gate_log))}\n"
                "\t@test -f README.md\n"
                "strict-tier:\n"
                f"\t@printf 'strict-tier\\n' >> {shlex.quote(str(gate_log))}\n"
                "\t@! grep -R -F complete-gate-finding -- feature.txt 2>/dev/null\n"
                "gate: check strict-tier\n"
            )
        }
    )
    base_before_failure = _tip(origin, "main")
    canonical = gitops.clone(origin, tmp_path / "registered-complete-gate")
    Registry().register(
        str(canonical), workflow="local", repo_type="single-owner", gate="make gate"
    )
    workspace = Workspace(tmp_path / "complete-gate-worktrees")

    rejected = run_repo_task(
        str(canonical),
        "Add a change rejected only by the strict gate tier.",
        "engineer",
        workspace=workspace,
        branch="strict-tier-failure",
        dispatch_fn=make_writing_dispatch(filename="feature.txt", content="complete-gate-finding"),
    )

    assert rejected.outcome == "gate-failed"
    assert rejected.verify is not None and not rejected.verify.ok
    assert gate_log.read_text(encoding="utf-8").splitlines() == ["check", "strict-tier"]
    assert _tip(origin, "main") == base_before_failure
    assert not _has_file(origin, "main", "feature.txt")

    published = run_repo_task(
        str(canonical),
        "Add a change accepted by the complete gate.",
        "engineer",
        workspace=workspace,
        branch="complete-gate-success",
        dispatch_fn=make_writing_dispatch(filename="feature.txt", content="clean change"),
    )

    assert published.ok and published.outcome == "merged", published.detail
    assert published.verify is not None and published.verify.ok
    assert gate_log.read_text(encoding="utf-8").splitlines() == [
        "check",
        "strict-tier",
        "check",
        "strict-tier",
        "check",
        "strict-tier",
    ]
    assert _tip(origin, "main") != base_before_failure
    assert _has_file(origin, "main", "feature.txt")


def test_bazel_affected_candidate_runs_in_lifecycle_worktree(
    tmp_path, bare_origin, monkeypatch
) -> None:
    origin = bare_origin({"WORKSPACE.bazel": ""})
    canonical = gitops.clone(origin, tmp_path / "registered")
    tools = tmp_path / "bin"
    tools.mkdir()
    bazel_diff = tools / "bazel-diff"
    bazel_diff.write_text(
        "#!/usr/bin/env python3\nimport pathlib, sys\n"
        "pathlib.Path(sys.argv[sys.argv.index('--output') + 1]).write_text('//:affected\\n')\n",
        encoding="utf-8",
    )
    bazel = tools / "bazel"
    bazel.write_text(
        '#!/bin/sh\ntest "$1" = test && test "$2" = -- && test "$3" = //:affected\n',
        encoding="utf-8",
    )
    bazel_diff.chmod(0o755)
    bazel.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}:{os.environ['PATH']}")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    result = run_repo_task(
        str(canonical),
        "Add a Bazel-affected change.",
        "engineer",
        workspace=Workspace(tmp_path / "worktrees"),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
    )
    assert result.ok and result.verify is not None and result.verify.ok


def test_local_repo_noop_registry_gate_surfaces_unproven_warning(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "registered")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")

    result = run_repo_task(
        str(canonical),
        "Add a change with an explicit no-op identity gate.",
        "engineer",
        workspace=Workspace(tmp_path / "worktrees"),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
    )

    assert result.ok and result.outcome == "merged"
    assert "gate: no-op -- pushed unproven" in result.detail
    assert result.verify is None


# llmlint: ignore[e2e_not_mocked] GitHub decisioning is the suite's documented external seam.
def test_remote_human_checkpoint_noop_gate_surfaces_unproven_warning(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "registered")
    Registry().register(str(canonical), workflow="remote", repo_type="single-owner")
    result = run_repo_task(
        str(canonical),
        workspace=Workspace(tmp_path / "worktrees"),
        github=FakeGitHub(origin),
        steps=[
            Step("prepare", "engineer", "prepare a draft checkpoint"),
            Step("approve", task="Approve the checkpoint.", kind="human", deps=["prepare"]),
        ],
        body="## What\nPrepare a checkpoint.\n\n## Why\nAwait approval.\n",
        dispatch_fn=_per_step_dispatch(),
    )
    assert result.outcome == "waiting-human" and result.pr is not None
    assert "gate: no-op -- pushed unproven" in result.detail


# llmlint: ignore[e2e_not_mocked] GitHub decisioning is the suite's documented external seam.
def test_remote_human_checkpoint_registry_gate_blocks_draft(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "registered")
    Registry().register(
        str(canonical),
        workflow="remote",
        repo_type="single-owner",
        gate="sh -c 'printf human-pause-gate-failed; exit 1'",
    )
    github = FakeGitHub(origin)
    result = run_repo_task(
        str(canonical),
        workspace=Workspace(tmp_path / "worktrees"),
        github=github,
        steps=[
            Step("prepare", "engineer", "prepare a rejected draft checkpoint"),
            Step("approve", task="Approve the checkpoint.", kind="human", deps=["prepare"]),
        ],
        body="## What\nPrepare a checkpoint.\n\n## Why\nAwait approval.\n",
        dispatch_fn=_per_step_dispatch(),
    )
    assert result.outcome == "gate-failed" and result.pr is None
    assert result.verify is not None and not result.verify.ok
    assert "human-pause-gate-failed" in result.detail


def test_lifecycle_without_explicit_or_registry_gate_errors(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    result = run_repo_task(
        str(origin),
        "Attempt an unconfigured lifecycle.",
        "engineer",
        workspace=_workspace(tmp_path, origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
    )
    assert result.outcome == "error"
    assert "no verification gate is configured" in result.detail


def test_skip_verify_bypasses_the_gate(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    result = run_repo_task(
        str(origin),
        "Add a change with the gate skipped.",
        "engineer",
        workspace=_workspace(tmp_path, origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        skip_verify=True,  # no local gate runs at all
        verify_cmd=["false"],  # would fail if it ran — proving it is skipped
    )
    assert result.ok and result.outcome == "merged"
    assert result.verify is None
    assert _has_file(origin, "main", "feature.txt")


def test_agent_not_completed_stops_early(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    ws = _workspace(tmp_path, origin)
    result = run_repo_task(
        str(origin),
        "Task the agent will not finish.",
        "engineer",
        workspace=ws,
        dispatch_fn=make_writing_dispatch(filename="partial.txt", completed=False),
        verify_cmd=["true"],
    )
    assert result.outcome == "not-completed"
    assert not result.ok
    assert "hit the turn cap" in result.detail
    assert result.resume is not None

    clone = ws.clone_dir(normalize_repo(str(origin)))
    assert _has_file(clone, result.branch, "partial.txt")
    subject = subprocess.run(
        ["git", "-C", str(clone), "log", "-1", "--format=%s", result.branch],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    assert subject.startswith("chore:") and "incomplete step" in subject
    assert not _has_file(origin, "main", "partial.txt")
    assert not _has_file(origin, result.branch, "partial.txt")  # incomplete work is not pushed


def test_retry_with_invalid_incomplete_provenance_records_fresh_branch_fallback(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin)
    first = run_repo_task(
        str(origin),
        "Preserve partial work.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="partial.txt", completed=False),
        verify_cmd=["true"],
    )
    assert first.resume is not None and first.resume.mode == "retry"
    recovery_worktree = workspace.worktree(
        normalize_repo(str(origin)), first.branch, base="origin/main"
    )
    gitops.commit_empty(
        recovery_worktree,
        "test: invalidate retry provenance\n\n"
        f"Orchestrator-Recovered-Incomplete: {first.resume.checkpoint}",
    )
    workspace.remove_worktree(normalize_repo(str(origin)), recovery_worktree)

    second = run_repo_task(
        str(origin),
        "Complete on a safe branch.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="complete.txt"),
        verify_cmd=["true"],
        resume=first.resume,
    )

    assert second.ok and second.branch != first.branch
    assert second.retry_lineage is not None
    assert second.retry_lineage.disposition == "abandoned"
    assert "does not carry valid unattested incomplete provenance" in (
        second.retry_lineage.reason or ""
    )


def test_preserved_retry_without_complete_gate_remains_unpublished(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin)
    first = run_repo_task(
        str(origin),
        "Preserve partial work.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="partial.txt", completed=False),
        verify_cmd=["true"],
    )
    assert isinstance(first.resume, Resume)

    retried = run_repo_task(
        str(origin),
        "Finish without a gate.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="complete.txt"),
        skip_verify=True,
        resume=first.resume,
    )

    assert retried.outcome == "not-completed" and not retried.ok
    assert "cannot be recovered without a successful complete gate" in retried.detail
    assert not _has_file(origin, "main", "complete.txt")


def test_preserved_retry_with_failing_complete_gate_remains_unpublished(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    workspace = _workspace(tmp_path, origin)
    first = run_repo_task(
        str(origin),
        "Preserve partial work.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="partial.txt", completed=False),
        verify_cmd=["true"],
    )
    assert isinstance(first.resume, Resume)

    retried = run_repo_task(
        str(origin),
        "Finish with a red gate.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="complete.txt"),
        verify_cmd=["false"],
        resume=first.resume,
    )

    assert retried.outcome == "gate-failed" and not retried.ok
    assert retried.verify is not None and not retried.verify.ok
    assert not _has_file(origin, "main", "complete.txt")


def test_clean_committed_partial_work_is_marked_and_recoverable(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-clean-partial")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    workspace = Workspace(tmp_path / "clean-partial-worktrees")
    partial_shas: list[str] = []

    def committing_dispatch(persona: str, task: str, *, project_dir: str, **_: object) -> Report:
        worktree = Path(project_dir)
        partial = worktree / "partial.txt"
        if not partial.exists():
            partial.write_text("agent-owned partial work\n", encoding="utf-8")
            gitops.add_all(worktree)
            partial_shas.append(gitops.commit(worktree, "wip: agent commits partial work"))
        assert not gitops.is_dirty(worktree)
        return Report(persona, 1, False, False, 2, [], {}, {}, "")

    result = run_repo_task(
        str(canonical),
        "Commit partial work, then hit the turn cap.",
        "engineer",
        workspace=workspace,
        branch="feature/clean-committed-partial",
        dispatch_fn=committing_dispatch,
        verify_cmd=["true"],
    )

    assert result.outcome == "not-completed" and not result.ok
    assert "hit the turn cap" in result.detail
    assert result.resume is not None and result.retry_lineage is not None
    assert result.branch not in gitops.worktrees(canonical)
    assert partial_shas and gitops.is_ancestor(canonical, partial_shas[0], result.branch)
    marker_sha = gitops.ref_sha(canonical, result.branch)
    marker_message = gitops.log_messages(canonical, partial_shas[0], result.branch)[0].message
    assert INCOMPLETE_TRAILER in marker_message
    assert f"{PR_BASE_TRAILER} main" in marker_message
    assert (
        subprocess.run(
            ["git", "-C", str(canonical), "diff-tree", "--quiet", f"{marker_sha}^", marker_sha]
        ).returncode
        == 0
    )
    assert not _has_file(origin, "main", "partial.txt")
    assert not _has_file(origin, result.branch, "partial.txt")

    gate_log = tmp_path / "clean-partial-recovery-gates.log"
    gate = [
        "sh",
        "-c",
        f"echo gate >> {shlex.quote(str(gate_log))}; test -f partial.txt",
    ]
    recovered = recover_repo(
        canonical,
        result.branch,
        workspace_root=tmp_path / "clean-partial-recovery-worktrees",
        verify_cmd=gate,
    )

    assert recovered.ok and recovered.outcome == "merged"
    assert recovered.workflow == "local" and recovered.pr_base == "main"
    assert result.branch not in gitops.worktrees(canonical)
    assert gate_log.read_text(encoding="utf-8").splitlines() == ["gate", "gate"]
    attestation = gitops.log_messages(canonical, marker_sha, result.branch)
    assert len(attestation) == 1
    assert f"Orchestrator-Recovered-Incomplete: {marker_sha}" in attestation[0].message
    assert not gitops.is_ancestor(canonical, attestation[0].sha, "origin/main")
    assert _has_file(origin, "main", "partial.txt")


@pytest.mark.parametrize(
    ("failure", "expected_outcome"),
    [("conflict", "sync-conflict"), ("gate", "gate-failed")],
)
def test_remote_stacked_recovery_failures_preserve_synthetic_base(
    tmp_path, bare_origin, failure: str, expected_outcome: str
) -> None:
    origin = bare_origin({"shared.txt": "root\n"})
    canonical = gitops.clone(origin, tmp_path / f"canonical-stacked-recovery-{failure}")
    Registry().register(str(canonical), workflow="remote", repo_type="team")
    synthetic = f"ai-orchestrator/stack-base/recovery-{failure}"
    branch = f"feature/stacked-recovery-{failure}"
    subprocess.run(["git", "branch", synthetic, "main"], cwd=canonical, check=True)
    gitops.push(canonical, synthetic, set_upstream=False)
    subprocess.run(["git", "checkout", "-b", branch, synthetic], cwd=canonical, check=True)
    (canonical / "shared.txt").write_text("preserved\n", encoding="utf-8")
    gitops.add_all(canonical)
    metadata = format_preserved_step_metadata("main", "engineer")
    gitops.commit(
        canonical,
        "chore: preserve stacked recovery (incomplete step)\n\n"
        f"{metadata}, preserved by ai-orchestrator after the dispatch did not complete.\n\n"
        f"{INCOMPLETE_TRAILER}\n{PR_BASE_TRAILER} {synthetic}",
    )
    gitops.push(canonical, branch, set_upstream=False)
    subprocess.run(["git", "checkout", synthetic], cwd=canonical, check=True)
    if failure == "conflict":
        (canonical / "shared.txt").write_text("advanced\n", encoding="utf-8")
        gitops.add_all(canonical)
        gitops.commit(canonical, "advance synthetic stack base")
        gitops.push(canonical, synthetic, set_upstream=False)
    subprocess.run(["git", "checkout", "main"], cwd=canonical, check=True)

    recovered = recover_repo(
        canonical,
        branch,
        workspace_root=tmp_path / f"stacked-recovery-{failure}-worktrees",
        github=FakeGitHub(origin),
        verify_cmd=["false" if failure == "gate" else "true"],
    )

    assert recovered.outcome == expected_outcome
    assert recovered.pr_base == synthetic
    assert recovered.synthetic_stack_base == synthetic


def test_local_recovery_conflict_resumes_worker_then_requeues(tmp_path, bare_origin) -> None:
    origin = bare_origin({"shared.txt": "original\n"})
    canonical = gitops.clone(origin, tmp_path / "canonical-recovery-conflict")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    partial = run_repo_task(
        str(canonical),
        "Preserve a conflicting edit.",
        "engineer",
        workspace=Workspace(tmp_path / "recovery-conflict-initial"),
        branch="feature/recovery-conflict",
        dispatch_fn=make_writing_dispatch(
            filename="shared.txt", content="preserved branch", completed=False
        ),
        verify_cmd=["true"],
    )
    assert partial.outcome == "not-completed"
    _advance_origin(tmp_path, origin, "shared.txt", "advanced base\n")
    sessions: list[str] = []

    def resolving_dispatch(
        persona: str, task: str, *, project_dir: str, session: str, **_: object
    ) -> Report:
        path = Path(project_dir) / "shared.txt"
        assert "Resolve the content conflict" in task
        assert "<<<<<<<" in path.read_text(encoding="utf-8")
        sessions.append(session)
        path.write_text("advanced base\npreserved branch by engineer\n", encoding="utf-8")
        gitops.add_all(project_dir)
        return Report(persona, 0, True, False, 2, [], {}, {}, "")

    recovered = recover_repo(
        canonical,
        partial.branch,
        workspace_root=tmp_path / "recovery-conflict-worktrees",
        verify_cmd=["git", "diff", "--check", "origin/main...HEAD"],
        dispatch_fn=resolving_dispatch,
    )

    assert recovered.outcome == "merged"
    assert sessions == [f"{partial.branch}:main"]
    assert (
        subprocess.run(
            ["git", "-C", str(origin), "show", "main:shared.txt"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
        == "advanced base\npreserved branch by engineer\n"
    )


@pytest.mark.parametrize("resolver_commits", [False, True])
def test_local_recovery_incomplete_resolver_preserves_branch(
    tmp_path, bare_origin, resolver_commits: bool
) -> None:
    origin = bare_origin({"shared.txt": "original\n"})
    canonical = gitops.clone(origin, tmp_path / "canonical-incomplete-recovery")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    partial = run_repo_task(
        str(canonical),
        "Preserve a conflicting recovery edit.",
        "engineer",
        workspace=Workspace(tmp_path / "incomplete-recovery-initial"),
        dispatch_fn=make_writing_dispatch(
            filename="shared.txt", content="preserved", completed=False
        ),
        verify_cmd=["true"],
    )
    _advance_origin(tmp_path, origin, "shared.txt", "advanced\n")

    def incomplete_dispatch(persona: str, task: str, *, project_dir: str, **_: object) -> Report:
        if resolver_commits:
            path = Path(project_dir) / "shared.txt"
            path.write_text("advanced\npreserved by engineer\n", encoding="utf-8")
            gitops.add_all(project_dir)
        return Report(persona, 1, False, False, 2, [], {}, {}, "")

    recovered = recover_repo(
        canonical,
        partial.branch,
        workspace_root=tmp_path / "incomplete-recovery-worktrees",
        verify_cmd=["true"],
        dispatch_fn=incomplete_dispatch,
    )

    assert recovered.outcome == "sync-conflict"
    assert "did not complete" in recovered.detail
    assert gitops.branch_exists(canonical, partial.branch)


def test_local_recovery_conflict_resolution_exhaustion_is_bounded(tmp_path, bare_origin) -> None:
    origin = bare_origin({"shared.txt": "original\n"})
    canonical = gitops.clone(origin, tmp_path / "canonical-exhausted-recovery")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    partial = run_repo_task(
        str(canonical),
        "Preserve a persistently conflicting recovery edit.",
        "engineer",
        workspace=Workspace(tmp_path / "exhausted-recovery-initial"),
        dispatch_fn=make_writing_dispatch(
            filename="shared.txt", content="preserved", completed=False
        ),
        verify_cmd=["true"],
    )
    _advance_origin(tmp_path, origin, "shared.txt", "advanced\n")
    resolutions = 0

    def unresolved_dispatch(persona: str, task: str, **_: object) -> Report:
        nonlocal resolutions
        resolutions += 1
        return Report(persona, 0, True, False, 2, [], {}, {}, "")

    recovered = recover_repo(
        canonical,
        partial.branch,
        workspace_root=tmp_path / "exhausted-recovery-worktrees",
        verify_cmd=["true"],
        dispatch_fn=unresolved_dispatch,
    )

    assert recovered.outcome == "sync-conflict"
    assert resolutions == MAX_MERGE_CONFLICT_RESOLUTIONS
    assert f"after {MAX_MERGE_CONFLICT_RESOLUTIONS} resolve-and-requeue cycles" in recovered.detail


@pytest.mark.parametrize(
    ("step_id", "persona", "expected"),
    [("bad/step", "engineer", "step metadata"), ("main", "Engineer!", "persona metadata")],
)
def test_local_recovery_rejects_invalid_worker_metadata(
    tmp_path, bare_origin, step_id: str, persona: str, expected: str
) -> None:
    origin = bare_origin({"shared.txt": "original\n"})
    label = expected.split()[0]
    canonical = gitops.clone(origin, tmp_path / f"canonical-invalid-{label}")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    partial = run_repo_task(
        str(canonical),
        "Preserve work with metadata that will be corrupted.",
        "engineer",
        workspace=Workspace(tmp_path / f"invalid-{label}-initial"),
        dispatch_fn=make_writing_dispatch(
            filename="shared.txt", content="preserved", completed=False
        ),
        verify_cmd=["true"],
    )
    subprocess.run(["git", "checkout", partial.branch], cwd=canonical, check=True)
    subprocess.run(
        [
            "git",
            "commit",
            "--amend",
            "-m",
            "chore: corrupted preserved metadata\n\n"
            f"Partial work from step {step_id} (persona: {persona}), preserved by "
            "ai-orchestrator after the dispatch did not complete.\n\n"
            f"{INCOMPLETE_TRAILER}\n{PR_BASE_TRAILER} main",
        ],
        cwd=canonical,
        check=True,
    )
    subprocess.run(["git", "checkout", "main"], cwd=canonical, check=True)
    _advance_origin(tmp_path, origin, "shared.txt", "advanced\n")

    recovered = recover_repo(
        canonical,
        partial.branch,
        workspace_root=tmp_path / f"invalid-{label}-recovery",
        verify_cmd=["true"],
    )

    assert recovered.outcome == "sync-conflict"
    assert expected in recovered.detail


def test_cooperative_real_dispatch_cancellation_preserves_and_recovers_branch(
    tmp_path, bare_origin, command_base, personas_dir
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-cancelled")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    workspace = Workspace(tmp_path / "cancelled-worktrees")
    cancel = threading.Event()
    witness = tmp_path / "cancelled.ticks"

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            run_repo_task,
            str(canonical),
            f"slow-branch {witness} write-change",
            "engineer",
            workspace=workspace,
            base_path=command_base(),
            persona_dir=personas_dir,
            branch="feature/cooperative-cancel",
            verify_cmd=["test", "-f", "CHANGE.txt"],
            cancel=cancel,
        )
        deadline = e2e_deadline(15)
        while time.monotonic() < deadline:
            ticks = witness.read_text(encoding="utf-8").count("tick") if witness.exists() else 0
            changes = list((tmp_path / "cancelled-worktrees").rglob("CHANGE.txt"))
            if ticks >= 3 and changes:
                break
            time.sleep(0.02)
        else:
            pytest.fail("real dispatch did not produce partial work before cancellation")
        cancel.set()
        result = future.result(timeout=e2e_timeout(15))

    assert result.outcome == "not-completed"
    assert isinstance(result.resume, Resume)
    assert result.resume.checkpoint == gitops.ref_sha(canonical, result.branch)
    assert result.branch not in gitops.worktrees(canonical)
    assert incomplete_commits(canonical, "origin/main", result.branch)

    recovered = recover_repo(
        canonical,
        result.branch,
        workspace_root=tmp_path / "cancelled-recovery-worktrees",
        verify_cmd=["test", "-f", "CHANGE.txt"],
    )
    assert recovered.ok and recovered.outcome == "merged"
    assert _has_file(origin, "main", "CHANGE.txt")


def test_cancellation_during_verification_preserves_before_publication(
    tmp_path, bare_origin, command_base, personas_dir
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-verification-cancel")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    cancel = threading.Event()
    gate_started = tmp_path / "verification.started"
    branch = "feature/verification-cancel"
    gate = [
        "sh",
        "-c",
        f"touch {shlex.quote(str(gate_started))}; sleep 1; test -f CHANGE.txt",
    ]

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            run_repo_task,
            str(canonical),
            "complete-now write-change",
            "engineer",
            workspace=Workspace(tmp_path / "verification-cancel-worktrees"),
            base_path=command_base(),
            persona_dir=personas_dir,
            branch=branch,
            verify_cmd=gate,
            cancel=cancel,
        )
        deadline = e2e_deadline(15)
        while time.monotonic() < deadline and not gate_started.exists():
            time.sleep(0.02)
        assert gate_started.exists()
        cancel.set()
        result = future.result(timeout=e2e_timeout(15))

    assert result.outcome == "not-completed"
    assert result.detail.startswith("cancelled cooperatively after verification")
    assert isinstance(result.resume, Resume)
    assert result.resume.checkpoint == gitops.ref_sha(canonical, branch)
    assert incomplete_commits(canonical, "origin/main", branch)
    assert not _has_file(origin, "main", "CHANGE.txt")

    recovered = recover_repo(
        canonical,
        branch,
        workspace_root=tmp_path / "verification-cancel-recovery",
        verify_cmd=["test", "-f", "CHANGE.txt"],
    )
    assert recovered.ok and recovered.outcome == "merged"
    assert _has_file(origin, "main", "CHANGE.txt")


def test_cancellation_after_publication_starts_finishes_authoritatively(
    tmp_path, bare_origin, command_base, personas_dir
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-publication-cancel")
    Registry().register(str(canonical), workflow="local", repo_type="single-owner")
    push_started = tmp_path / "publication.started"
    hook = origin / "hooks" / "pre-receive"
    hook.write_text(
        f"#!/bin/sh\ntouch {shlex.quote(str(push_started))}\nsleep 1\nexit 0\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    cancel = threading.Event()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            run_repo_task,
            str(canonical),
            "complete-now write-change",
            "engineer",
            workspace=Workspace(tmp_path / "publication-cancel-worktrees"),
            base_path=command_base(),
            persona_dir=personas_dir,
            branch="feature/publication-cancel",
            verify_cmd=["test", "-f", "CHANGE.txt"],
            cancel=cancel,
        )
        deadline = e2e_deadline(15)
        while time.monotonic() < deadline and not push_started.exists():
            time.sleep(0.02)
        assert push_started.exists()
        cancel.set()
        result = future.result(timeout=e2e_timeout(20))

    assert cancel.is_set()
    assert result.ok and result.outcome == "merged"
    assert result.resume is None
    assert _has_file(origin, "main", "CHANGE.txt")


def test_no_changes_produces_no_pr(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    result = run_repo_task(
        str(origin),
        "A task the agent completes without editing anything.",
        "reviewer",
        workspace=_workspace(tmp_path, origin),
        dispatch_fn=make_writing_dispatch(filename=None),
        verify_cmd=["true"],
    )
    assert result.outcome == "no-changes"
    assert not result.ok


def test_resumed_branch_setup_round_trips_through_telemetry_cli(tmp_path, bare_origin) -> None:
    """A tracked retry journals and surfaces setup for its existing branch worktree."""
    origin = bare_origin()
    workspace = _workspace(tmp_path / "workspace", origin)
    partial = run_repo_task(
        str(origin),
        "Preserve work for a real retry.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="partial.txt", completed=False),
        verify_cmd=["true"],
    )
    assert isinstance(partial.resume, Resume)

    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "existing-branch"
    _, round_dir = prepare_round(
        run_dir,
        {"tasks": [{"id": "retry", "task": "Continue the preserved branch."}]},
    )
    journal = open_journal(run_dir, RunId("existing-branch"), 1)

    def lifecycle_runner(node: RepoPlanNode, *, journal: NodeSink | None = None):
        return run_repo_task(
            node.repo,
            node.task,
            node.persona,
            workspace=workspace,
            dispatch_fn=make_writing_dispatch(filename="completed.txt"),
            verify_cmd=["true"],
            resume=partial.resume,
            journal=journal,
        )

    result = run_graph(
        parse_graph(
            {
                "tasks": [
                    {
                        "id": "retry",
                        "repo": str(origin),
                        "persona": "engineer",
                        "task": "Continue the preserved branch.",
                    }
                ]
            }
        ),
        agent_runner=lambda _node: (_ for _ in ()).throw(
            AssertionError("this graph contains no direct agent")
        ),
        lifecycle_runner=lifecycle_runner,
        journal=journal,
        run_id=RunId("existing-branch"),
        round_number=1,
    )
    assert result.ok
    write_result(round_dir, graph_payload(result))

    setup_events = [event for event in journal.events() if event.kind == "setup-finished"]
    assert any(event.detail["operation"] == "worktree" for event in setup_events)

    indexed = subprocess.run(
        ["just", "telemetry", "--runs-dir", str(runs_dir), "--all"],
        cwd=Path(__file__).parents[2],
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert indexed.returncode == 0, indexed.stderr
    observed = json.loads(indexed.stdout)["runs"][0]["timing"]
    assert observed["setup_seconds"] > 0


def test_real_lifecycle_outcomes_round_trip_through_telemetry_cli(tmp_path, bare_origin) -> None:
    """Produce every classification and metric from real git lifecycle journeys."""
    runs_dir = tmp_path / "runs"

    def run_recorded(
        run_id: str,
        *,
        origin: Path,
        github: FakeGitHub | None = None,
        dispatch_fn=None,
        verify_cmd: list[str] | None = None,
        timeout: float = 3600.0,
        clock=None,
        resume: Resume | None = None,
        workspace: Workspace | None = None,
    ):
        run_dir = runs_dir / run_id
        _, round_dir = prepare_round(run_dir, {"tasks": [{"id": "ship", "task": run_id}]})
        journal = open_journal(run_dir, RunId(run_id), 1)
        node = NodeJournal(journal, NodeId("ship"), RunId(run_id), 1)
        result = run_repo_task(
            "acme/widget" if github is not None else str(origin),
            run_id,
            "engineer",
            workspace=workspace or _workspace(tmp_path / run_id, origin),
            merge=GitHubMergeStrategy(github) if github is not None else None,
            url=str(origin),
            dispatch_fn=dispatch_fn or make_writing_dispatch(filename="change.txt"),
            verify_cmd=verify_cmd or ["true"],
            timeout=timeout,
            clock=clock or __import__("time").monotonic,
            sleep=lambda _seconds: None,
            journal=node,
            resume=resume,
        )
        item = result_payload(result)
        item.update(
            {
                "kind": "agent",
                "status": "done" if result.ok or result.outcome == "no-changes" else "failed",
            }
        )
        write_result(
            round_dir,
            {
                "ok": result.ok,
                "state": "complete" if result.ok else "failed",
                "started_order": ["ship"],
                "results": {"ship": item},
            },
        )
        return result

    run_recorded("gate", origin=bare_origin(), verify_cmd=["false"])
    checks_origin = bare_origin()
    run_recorded(
        "checks",
        origin=checks_origin,
        github=FakeGitHub(checks_origin, fail_checks=True),
    )
    timeout_origin = bare_origin()
    ticks = iter((0.0, 100.0))
    run_recorded(
        "timeout",
        origin=timeout_origin,
        github=FakeGitHub(timeout_origin, auto_completes=False, check_states=("PENDING",)),
        timeout=1.0,
        clock=lambda: next(ticks),
    )

    class ClosedGitHub(FakeGitHub):
        def status(self, pr):
            self._prs[pr.number].closed = True
            return super().status(pr)

    closed_origin = bare_origin()
    run_recorded(
        "publication",
        origin=closed_origin,
        github=ClosedGitHub(closed_origin),
    )
    run_recorded(
        "no-diff",
        origin=bare_origin(),
        dispatch_fn=make_writing_dispatch(filename=None),
    )

    reused_origin = bare_origin()
    reused_workspace = _workspace(tmp_path / "reused-workspace", reused_origin)
    partial = run_repo_task(
        str(reused_origin),
        "Preserve work for a real retry.",
        "engineer",
        workspace=reused_workspace,
        dispatch_fn=make_writing_dispatch(filename="partial.txt", completed=False),
        verify_cmd=["true"],
    )
    assert isinstance(partial.resume, Resume)
    reused = run_recorded(
        "reused",
        origin=reused_origin,
        workspace=reused_workspace,
        resume=partial.resume,
        verify_cmd=["false"],
    )
    assert reused.retry_lineage is not None
    assert reused.retry_lineage.disposition == "reused"

    abandoned_origin = bare_origin()
    abandoned_workspace = _workspace(tmp_path / "abandoned-workspace", abandoned_origin)
    abandoned_partial = run_repo_task(
        str(abandoned_origin),
        "Preserve work before invalidating its provenance.",
        "engineer",
        workspace=abandoned_workspace,
        dispatch_fn=make_writing_dispatch(filename="partial.txt", completed=False),
        verify_cmd=["true"],
    )
    assert isinstance(abandoned_partial.resume, Resume)
    recovery_worktree = abandoned_workspace.worktree(
        normalize_repo(str(abandoned_origin)), abandoned_partial.branch, base="origin/main"
    )
    gitops.commit_empty(
        recovery_worktree,
        "test: invalidate retry provenance\n\n"
        f"Orchestrator-Recovered-Incomplete: {abandoned_partial.resume.checkpoint}",
    )
    abandoned_workspace.remove_worktree(normalize_repo(str(abandoned_origin)), recovery_worktree)
    abandoned = run_recorded(
        "abandoned",
        origin=abandoned_origin,
        workspace=abandoned_workspace,
        resume=abandoned_partial.resume,
    )
    assert abandoned.retry_lineage is not None
    assert abandoned.retry_lineage.disposition == "abandoned"

    indexed = subprocess.run(
        ["just", "telemetry", "--runs-dir", str(runs_dir), "--all"],
        cwd=Path(__file__).parents[2],
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert indexed.returncode == 0, indexed.stderr
    payload = json.loads(indexed.stdout)
    failures = {
        run["run_id"]: run["failure"]["class"] for run in payload["runs"] if "failure" in run
    }
    assert failures == {
        "gate": "gate",
        "checks": "checks",
        "timeout": "timeout",
        "publication": "publication",
        "reused": "gate",
    }
    assert payload["metrics"]["no_diff_dispatches"] == 1
    assert payload["metrics"]["retry_branch_reuses"] == 1
    assert payload["metrics"]["abandoned_branches"] == 1


# --- GitHub repo: PR + auto-merge on required checks -----------------------


def test_github_auto_merge_on_required_checks(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    github = FakeGitHub(origin, required=("ci",))
    workspace = _workspace(tmp_path, origin)
    result = run_repo_task(
        "acme/widget",  # a GitHub-style slug → GitHub strategy
        "Add a feature file.",
        "engineer",
        workspace=workspace,
        merge=GitHubMergeStrategy(github),
        url=str(origin),  # but clone/push the real bare repo
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["true"],
        sleep=lambda _: None,
    )
    assert result.ok
    assert result.outcome == "merged"
    assert result.pr is not None and result.pr.number == 1
    assert _has_file(origin, "main", "feature.txt")
    canonical = workspace.execution_checkout(normalize_repo("acme/widget"))
    assert gitops.head_sha(canonical) == _tip(origin, "main")


def test_github_pr_title_comes_from_agent_commit_subject(tmp_path, bare_origin) -> None:
    """A real branch commit, not task prose, supplies the release-facing PR title."""
    origin = bare_origin()
    github = FakeGitHub(origin, required=("ci",))

    def committing_dispatch(persona: str, task: str, *, project_dir: str, **_: object) -> Report:
        worktree = Path(project_dir)
        (worktree / "capture.txt").write_text("preserved output\n", encoding="utf-8")
        gitops.add_all(worktree)
        gitops.commit(worktree, "fix(capture): preserve failed session output")
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    result = run_repo_task(
        "acme/widget",
        "Fix a silent session-capture failure in oneharness, and the unfaithful behavior.",
        "engineer",
        workspace=_workspace(tmp_path, origin),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=committing_dispatch,
        verify_cmd=["true"],
        sleep=lambda _: None,
    )

    assert result.ok and result.pr is not None
    assert github._prs[result.pr.number].title == "fix(capture): preserve failed session output"
    merged_subject = subprocess.run(
        ["git", "-C", str(origin), "log", "-1", "--format=%s", "main"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    assert merged_subject == "fix(capture): preserve failed session output"


def test_github_second_run_reuses_open_pr_and_merges(tmp_path, bare_origin) -> None:
    class FindOrCreateFakeGitHub(FakeGitHub):
        def create_pr(
            self, repo: str, *, head: str, base: str, title: str, body: str, draft: bool = False
        ) -> PullRequest:
            for number, state in self._prs.items():
                if state.head == head and not state.merged:
                    return PullRequest(
                        number=number,
                        url=f"https://github.com/{repo}/pull/{number}",
                        repo=repo,
                        head=head,
                        base=base,
                    )
            return super().create_pr(
                repo, head=head, base=base, title=title, body=body, draft=draft
            )

    origin = bare_origin()
    github = FindOrCreateFakeGitHub(origin)
    workspace = _workspace(tmp_path, origin)
    branch = "orchestrator/continue-pr"
    first = run_repo_task(
        "acme/widget",
        "Start a feature.",
        "engineer",
        workspace=workspace,
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        branch=branch,
        dispatch_fn=make_writing_dispatch(filename="first.txt"),
        verify_cmd=["true"],
        merge_policy="none",
    )
    assert first.outcome == "pr-open"
    assert first.pr is not None

    continue_dispatch = make_writing_dispatch(filename="second.txt")

    def resume_open_pr(persona: str, task: str, *, project_dir: str, **kwargs: object) -> Report:
        if persona == "pr-author":
            return cast(Report, continue_dispatch(persona, task, project_dir=project_dir, **kwargs))
        subprocess.run(
            ["git", "reset", "--hard", f"origin/{branch}"],
            cwd=project_dir,
            check=True,
            capture_output=True,
        )
        return cast(Report, continue_dispatch(persona, task, project_dir=project_dir, **kwargs))

    second = run_repo_task(
        "acme/widget",
        "Continue the feature.",
        "engineer",
        workspace=workspace,
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        branch=branch,
        dispatch_fn=resume_open_pr,
        verify_cmd=["true"],
        sleep=lambda _: None,
    )
    assert second.ok and second.outcome == "merged"
    assert second.pr is not None and second.pr.number == first.pr.number
    assert github._n == 1
    assert _has_file(origin, "main", "first.txt")
    assert _has_file(origin, "main", "second.txt")


def test_github_auto_merge_unavailable_falls_back_to_direct(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    github = FakeGitHub(origin, auto_available=False)  # repo forbids native auto-merge
    result = run_repo_task(
        "acme/widget",
        "Add a feature file.",
        "engineer",
        workspace=_workspace(tmp_path, origin),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["true"],
        sleep=lambda _: None,
    )
    assert result.ok and result.outcome == "merged"
    assert _has_file(origin, "main", "feature.txt")


def test_github_direct_fallback_fails_fast_when_merge_returns_unmerged(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    github = FakeGitHub(origin, auto_available=False, direct_completes=False)
    sleeps: list[float] = []

    result = run_repo_task(
        "acme/widget",
        "Add a feature whose direct remote merge stalls.",
        "engineer",
        workspace=_workspace(tmp_path, origin, workflow="remote"),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["true"],
        repo_type="single-owner",
        merge_policy="auto",
        sleep=sleeps.append,
        timeout=10_000.0,
    )

    assert not result.ok and result.outcome == "error"
    assert "ci=SUCCESS" in result.detail
    assert sleeps == []
    assert _tip(origin, "main") == before


def test_github_direct_fallback_polls_only_while_merge_is_in_progress(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    github = FakeGitHub(
        origin,
        auto_available=False,
        direct_completes=False,
        merge_progress_states=(False, False, True, False),
    )
    sleeps: list[float] = []

    result = run_repo_task(
        "acme/widget",
        "Add a feature whose queued direct merge stops progressing.",
        "engineer",
        workspace=_workspace(tmp_path, origin, workflow="remote"),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["true"],
        repo_type="single-owner",
        merge_policy="auto",
        sleep=sleeps.append,
        timeout=10_000.0,
    )

    assert not result.ok and result.outcome == "error"
    assert "ci=SUCCESS" in result.detail
    assert sleeps == [15.0]
    assert _tip(origin, "main") == before


def test_github_direct_policy_polls_an_in_progress_merge_until_completion(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    github = FakeGitHub(
        origin,
        direct_completes=False,
        direct_merge_status_poll=4,
        merge_in_progress=True,
    )
    sleeps: list[float] = []

    result = run_repo_task(
        "acme/widget",
        "Add a feature whose direct merge completes asynchronously.",
        "engineer",
        workspace=_workspace(tmp_path, origin),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["true"],
        merge_policy="direct",
        sleep=sleeps.append,
        timeout=10_000.0,
    )

    assert result.ok and result.outcome == "merged"
    assert sleeps == [15.0]
    assert _has_file(origin, "main", "feature.txt")


def test_github_required_check_failure_blocks_merge(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    github = FakeGitHub(origin, fail_checks=True)
    sleeps: list[float] = []
    result = run_repo_task(
        "acme/widget",
        "Add a feature file.",
        "engineer",
        workspace=_workspace(tmp_path, origin),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["true"],
        merge_policy="auto",
        sleep=sleeps.append,
        timeout=10_000.0,
    )
    assert not result.ok
    assert result.outcome == "checks-failed"
    assert sleeps == []
    assert github.status_polls == 2
    assert _tip(origin, "main") == before  # required check failed → nothing merged


def test_github_pending_required_check_keeps_polling_then_merges(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    github = FakeGitHub(origin, check_states=("PENDING", "PENDING", "SUCCESS"))
    sleeps: list[float] = []

    result = run_repo_task(
        "acme/widget",
        "Add a feature after CI settles.",
        "engineer",
        workspace=_workspace(tmp_path, origin),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["true"],
        sleep=sleeps.append,
        timeout=60.0,
    )

    assert result.ok and result.outcome == "merged"
    assert sleeps == [15.0]
    assert github.status_polls >= 3
    assert _has_file(origin, "main", "feature.txt")


def test_github_settled_checks_fail_when_native_auto_merge_stalls(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    github = FakeGitHub(origin, required=("ci",), auto_completes=False)
    sleeps: list[float] = []

    result = run_repo_task(
        "acme/widget",
        "Add a feature whose remote merge stalls.",
        "engineer",
        workspace=_workspace(tmp_path, origin, workflow="remote"),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["true"],
        repo_type="single-owner",
        merge_policy="auto",
        sleep=sleeps.append,
        timeout=10_000.0,
    )

    assert result.outcome == "error", result.detail
    assert not result.ok
    assert "[ci=SUCCESS]" in result.detail
    assert sleeps == []
    assert _tip(origin, "main") == before


def test_github_waits_for_required_checks_to_be_reported_then_merges(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    # The first status read checks whether the new PR is a draft; the merge poll
    # then observes no reported checks, pending CI, and finally green CI.
    github = FakeGitHub(
        origin,
        check_states=(None, None, "PENDING", "PENDING", "PENDING", "PENDING", "SUCCESS"),
    )
    sleeps: list[float] = []

    result = run_repo_task(
        "acme/widget",
        "Add a feature after GitHub reports and settles CI.",
        "engineer",
        workspace=_workspace(tmp_path, origin, workflow="remote"),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["true"],
        repo_type="single-owner",
        merge_policy="auto",
        sleep=sleeps.append,
        timeout=60.0,
    )

    assert result.ok and result.outcome == "merged"
    assert sleeps == [15.0, 30.0, 60.0, 120.0, 120.0]
    assert github.status_polls == 7
    assert _has_file(origin, "main", "feature.txt")


def test_github_unreported_required_checks_wait_until_timeout(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    github = FakeGitHub(origin, check_states=(None,), auto_completes=False)
    sleeps: list[float] = []
    ticks = iter([0.0, 0.0, 100.0])

    result = run_repo_task(
        "acme/widget",
        "Add a feature whose required checks are never reported.",
        "engineer",
        workspace=_workspace(tmp_path, origin, workflow="remote"),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["true"],
        repo_type="single-owner",
        merge_policy="auto",
        sleep=sleeps.append,
        clock=lambda: next(ticks),
        timeout=10.0,
    )

    assert not result.ok and result.outcome == "timeout"
    assert sleeps == [15.0]
    assert github.status_polls == 3
    assert _tip(origin, "main") == before


def test_github_direct_merge_waits_when_post_merge_checks_disappear(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    github = FakeGitHub(
        origin,
        auto_available=False,
        direct_completes=False,
        direct_merge_status_poll=4,
        check_states=("SUCCESS", "SUCCESS", None, "SUCCESS"),
    )
    sleeps: list[float] = []

    result = run_repo_task(
        "acme/widget",
        "Add a feature despite a transient empty post-merge check response.",
        "engineer",
        workspace=_workspace(tmp_path, origin, workflow="remote"),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["true"],
        repo_type="single-owner",
        merge_policy="auto",
        sleep=sleeps.append,
        timeout=60.0,
    )

    assert result.ok and result.outcome == "merged"
    assert sleeps == [15.0]
    assert github.status_polls == 4
    assert _has_file(origin, "main", "feature.txt")


def test_github_auto_policy_polls_an_in_progress_merge_until_completion(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    github = FakeGitHub(
        origin,
        auto_completes=False,
        auto_merge_status_poll=3,
        merge_progress_states=(False, True, True),
    )
    sleeps: list[float] = []

    result = run_repo_task(
        "acme/widget",
        "Add a feature whose native auto-merge completes asynchronously.",
        "engineer",
        workspace=_workspace(tmp_path, origin, workflow="remote"),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["true"],
        repo_type="single-owner",
        merge_policy="auto",
        sleep=sleeps.append,
        timeout=10_000.0,
    )

    assert result.ok and result.outcome == "merged"
    assert sleeps == [15.0]
    assert _has_file(origin, "main", "feature.txt")


def test_github_in_progress_merge_uses_timeout_as_backstop(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    github = FakeGitHub(origin, direct_completes=False, merge_in_progress=True)
    sleeps: list[float] = []
    ticks = iter([0.0, 0.0, 100.0])

    result = run_repo_task(
        "acme/widget",
        "Add a feature whose direct merge never finishes processing.",
        "engineer",
        workspace=_workspace(tmp_path, origin),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["true"],
        merge_policy="direct",
        sleep=sleeps.append,
        clock=lambda: next(ticks),
        timeout=10.0,
    )

    assert not result.ok and result.outcome == "timeout"
    assert sleeps == [15.0]
    assert _tip(origin, "main") == before


def test_remote_human_workstream_draft_checkpoint_and_safe_resume(tmp_path, bare_origin) -> None:
    """Remote pauses sync, gate, reuse one draft, and reject unsafe resumes."""

    class TrackingGitHub(FakeGitHub):
        def __init__(self, origin: Path) -> None:
            super().__init__(origin)
            self.created: list[int] = []
            self.reused: list[int] = []
            self.readied: list[int] = []

        def create_pr(
            self,
            repo: str,
            *,
            head: str,
            base: str,
            title: str,
            body: str,
            draft: bool = False,
        ) -> PullRequest:
            for number, state in self._prs.items():
                if (
                    state.head == head
                    and state.base == base
                    and not state.closed
                    and not state.merged
                ):
                    self.reused.append(number)
                    return PullRequest(
                        number=number,
                        url=f"https://github.com/{repo}/pull/{number}",
                        repo=repo,
                        head=head,
                        base=base,
                    )
            pr = super().create_pr(repo, head=head, base=base, title=title, body=body, draft=draft)
            self.created.append(pr.number)
            return pr

        def mark_ready(self, pr: PullRequest) -> None:
            self.readied.append(pr.number)
            super().mark_ready(pr)

    origin = bare_origin()
    subprocess.run(
        ["git", "-C", str(origin), "config", "receive.denyNonFastForwards", "true"],
        check=True,
    )
    canonical = gitops.clone(origin, tmp_path / "remote-human-canonical")
    workspace = Workspace(
        tmp_path / "remote-human-worktrees",
        resolver=lambda _: canonical,
        workflow="remote",
        repo_type="single-owner",
    )
    github = TrackingGitHub(origin)

    empty = run_repo_task(
        "acme/widget",
        workspace=workspace,
        url=str(origin),
        github=github,
        steps=[Step("approve", task="Approve before work starts.", kind="human")],
        branch="feature/empty-human-pause",
        verify_cmd=["true"],
    )
    assert empty.outcome == "waiting-human" and empty.resume is not None
    assert empty.pr is None and empty.resume.pr is None and github.created == []
    assert not _has_file(origin, empty.branch, "README.md")

    failed_gate = run_repo_task(
        "acme/widget",
        workspace=workspace,
        url=str(origin),
        github=github,
        steps=[
            Step("prepare-bad", "engineer", "prepare a rejected checkpoint"),
            Step("reject", task="Reject this checkpoint.", kind="human", deps=["prepare-bad"]),
        ],
        branch="feature/rejected-human-pause",
        dispatch_fn=_per_step_dispatch(),
        verify_cmd=["false"],
    )
    assert failed_gate.outcome == "gate-failed"
    assert failed_gate.pr is None and failed_gate.resume is None and github.created == []
    assert not _has_file(origin, failed_gate.branch, "prepare-bad.txt")

    dispatched: list[str] = []
    advanced_sha: str | None = None

    def writing_step(
        persona: str, task: str, *, project_dir: str, session: str, **_: object
    ) -> Report:
        nonlocal advanced_sha
        if persona == "pr-author":
            output = task.split(
                "Write the final body, and nothing else, to this absolute path:\n", 1
            )[1].splitlines()[0]
            Path(output).write_text(
                "## What\nPrepares and publishes the remote work.\n\n"
                "## Why\nSupports the gated workstream.\n",
                encoding="utf-8",
            )
            return Report(persona, 0, True, False, 1, [], {}, {}, "")
        sid = session.rsplit(":", 1)[-1]
        dispatched.append(sid)
        (Path(project_dir) / f"{sid}.txt").write_text(task + "\n", encoding="utf-8")
        if sid == "prepare" and advanced_sha is None:
            advanced_sha = _advance_origin(tmp_path, origin, "base.txt", "advanced base\n")
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    gate_log = tmp_path / "remote-human-gates.log"
    gate = [
        "sh",
        "-c",
        f"echo gate >> {shlex.quote(str(gate_log))}; test -f base.txt && "
        'test -f prepare.txt && test -d "$ORCHESTRATOR_CACHE_DIR"',
    ]
    steps = [
        Step("prepare", "engineer", "prepare remote work"),
        Step("approve", task="Approve the checkpoint.", kind="human", deps=["prepare"]),
        Step("implement", "engineer", "implement after approval", deps=["approve"]),
        Step("release", task="Release the implementation.", kind="human", deps=["implement"]),
        Step("finalize", "engineer", "finalize publication", deps=["release"]),
    ]
    paused = run_repo_task(
        "acme/widget",
        workspace=workspace,
        url=str(origin),
        github=github,
        steps=steps,
        branch="feature/remote-human-resume",
        dispatch_fn=writing_step,
        verify_cmd=gate,
        sleep=lambda _: None,
    )

    assert paused.outcome == "waiting-human" and paused.resume is not None
    assert advanced_sha is not None
    assert paused.pr is not None and paused.resume.pr == paused.pr.url
    assert github.created == [paused.pr.number] and github._prs[paused.pr.number].draft
    assert dispatched == ["prepare"]
    assert gate_log.read_text(encoding="utf-8").splitlines() == ["gate"]
    assert paused.resume.checkpoint == _tip(origin, paused.branch)
    assert gitops.is_ancestor(canonical, advanced_sha, paused.resume.checkpoint)
    assert _tip(origin, "main") == advanced_sha

    second = run_repo_task(
        "acme/widget",
        workspace=workspace,
        url=str(origin),
        github=github,
        steps=steps,
        dispatch_fn=writing_step,
        verify_cmd=gate,
        sleep=lambda _: None,
        resume=replace(
            paused.resume,
            completed_steps=(*paused.resume.completed_steps, "approve"),
        ),
    )

    assert second.outcome == "waiting-human" and second.resume is not None
    assert second.pr is not None and second.pr.number == paused.pr.number
    assert github.created == [paused.pr.number] and github.reused == []
    assert github._prs[paused.pr.number].draft
    assert dispatched == ["prepare", "implement"]
    assert gate_log.read_text(encoding="utf-8").splitlines() == ["gate", "gate"]
    assert gitops.is_ancestor(canonical, paused.resume.checkpoint, second.resume.checkpoint)
    assert second.resume.checkpoint == _tip(origin, second.branch)

    final_resume = replace(
        second.resume,
        completed_steps=(*second.resume.completed_steps, "release"),
    )
    github._prs[paused.pr.number].closed = True
    closed = run_repo_task(
        "acme/widget",
        workspace=workspace,
        url=str(origin),
        github=github,
        steps=steps,
        dispatch_fn=writing_step,
        verify_cmd=gate,
        resume=final_resume,
    )
    assert closed.outcome == "resume-failed" and "closed without merging" in closed.detail
    assert dispatched == ["prepare", "implement"]
    github._prs[paused.pr.number].closed = False

    github._prs[paused.pr.number].draft = False
    prematurely_ready = run_repo_task(
        "acme/widget",
        workspace=workspace,
        url=str(origin),
        github=github,
        steps=steps,
        dispatch_fn=writing_step,
        verify_cmd=gate,
        resume=final_resume,
    )
    assert prematurely_ready.outcome == "resume-failed" and "ready for review" in (
        prematurely_ready.detail
    )
    assert dispatched == ["prepare", "implement"]
    github._prs[paused.pr.number].draft = True

    saved_tip = _tip(origin, second.branch)
    subprocess.run(
        ["git", "-C", str(origin), "update-ref", f"refs/heads/{second.branch}", advanced_sha],
        check=True,
    )
    rewritten = run_repo_task(
        "acme/widget",
        workspace=workspace,
        url=str(origin),
        github=github,
        steps=steps,
        dispatch_fn=writing_step,
        verify_cmd=gate,
        resume=final_resume,
    )
    assert rewritten.outcome == "resume-failed" and "rewritten" in rewritten.detail
    assert dispatched == ["prepare", "implement"]
    subprocess.run(
        ["git", "-C", str(origin), "update-ref", f"refs/heads/{second.branch}", saved_tip],
        check=True,
    )

    rogue = workspace.worktree(
        normalize_repo("acme/widget"), second.branch, base=f"origin/{second.branch}"
    )
    (rogue / "unpublished.txt").write_text("must not enter the resumed draft\n", encoding="utf-8")
    gitops.add_all(rogue)
    gitops.commit(rogue, "unpublished local branch mutation")
    workspace.remove_worktree(normalize_repo("acme/widget"), rogue)
    local_ahead = run_repo_task(
        "acme/widget",
        workspace=workspace,
        url=str(origin),
        github=github,
        steps=steps,
        dispatch_fn=writing_step,
        verify_cmd=gate,
        resume=final_resume,
    )
    assert local_ahead.outcome == "resume-failed" and "unpublished or divergent" in (
        local_ahead.detail
    )
    assert dispatched == ["prepare", "implement"]
    subprocess.run(
        [
            "git",
            "-C",
            str(workspace.clone_dir(normalize_repo("acme/widget"))),
            "update-ref",
            f"refs/heads/{second.branch}",
            saved_tip,
        ],
        check=True,
    )

    completed = run_repo_task(
        "acme/widget",
        workspace=workspace,
        url=str(origin),
        github=github,
        steps=steps,
        dispatch_fn=writing_step,
        verify_cmd=gate,
        sleep=lambda _: None,
        resume=final_resume,
    )

    assert completed.ok and completed.outcome == "merged", completed.detail
    assert completed.pr is not None and completed.pr.number == paused.pr.number
    assert github.created == [paused.pr.number]
    assert github.reused == [paused.pr.number]
    assert github.readied == [paused.pr.number]
    assert not github._prs[paused.pr.number].draft
    assert dispatched == ["prepare", "implement", "finalize"]
    assert gate_log.read_text(encoding="utf-8").splitlines() == ["gate", "gate", "gate"]
    assert gitops.is_ancestor(canonical, second.resume.checkpoint, _tip(origin, completed.branch))
    assert _has_file(origin, "main", "prepare.txt")
    assert _has_file(origin, "main", "implement.txt")
    assert _has_file(origin, "main", "finalize.txt")


# --- multi-PR: one larger task across coordinated PRs ----------------------


def test_multi_pr_dag_across_repos(tmp_path, bare_origin) -> None:
    repo_x = bare_origin()
    repo_y = bare_origin()
    ws = _workspace(tmp_path, repo_x, repo_y)

    def runner(node: RepoPlanNode, *, journal: NodeSink | None = None):
        return run_repo_task(
            node.repo,
            node.task,
            node.persona,
            workspace=ws,
            dispatch_fn=make_writing_dispatch(filename=f"{node.id}.txt"),
            verify_cmd=["true"],
            journal=journal,
        )

    plan = RepoPlan(
        tasks=[
            RepoPlanNode("a", str(repo_x), "engineer", "part A on repo X"),
            RepoPlanNode("b", str(repo_y), "engineer", "part B on repo Y"),
            RepoPlanNode("c", str(repo_x), "reviewer", "part C on repo X", deps=["a"]),
        ],
        concurrency=3,
    )
    result = run_repo_plan(plan, runner)

    assert result.ok
    assert all(r.status == "done" for r in result.results.values())
    order = result.started_order
    assert order.index("a") < order.index("c")  # dependent waits for its dep
    # Each PR's change landed on its repo's main; c branched off a's merged base.
    assert _has_file(repo_x, "main", "a.txt")
    assert _has_file(repo_x, "main", "c.txt")
    assert _has_file(repo_y, "main", "b.txt")


def test_linear_team_stack_targets_open_dependency_and_includes_pr_link(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-linear-stack")
    Registry().register(str(canonical), workflow="remote", repo_type="team")
    workspace = Workspace(tmp_path / "linear-stack-worktrees")
    github = FakeGitHub(origin)

    def runner(node: RepoPlanNode, *, journal: NodeSink | None = None):
        return run_repo_task(
            node.repo,
            node.task,
            node.persona,
            workspace=workspace,
            github=github,
            branch=node.branch,
            dispatch_fn=make_writing_dispatch(filename=f"{node.id}.txt"),
            verify_cmd=["true"],
            stack_bases=node.stack_bases,
            journal=journal,
        )

    result = run_repo_plan(
        RepoPlan(
            [
                RepoPlanNode(
                    "parent", str(canonical), "engineer", "parent", branch="feature/parent"
                ),
                RepoPlanNode(
                    "child",
                    str(canonical),
                    "engineer",
                    "child",
                    deps=["parent"],
                    branch="feature/child",
                ),
            ]
        ),
        runner,
    )

    assert result.ok
    parent = result.results["parent"].result
    child = result.results["child"].result
    assert parent is not None and child is not None
    assert parent.outcome == child.outcome == "pr-open"
    assert child.pr_base == parent.branch
    assert child.pr is not None and child.pr.base == parent.branch
    assert parent.pr is not None and parent.pr.url in github._prs[2].body
    assert _has_file(origin, child.branch, "parent.txt")
    assert _has_file(origin, child.branch, "child.txt")
    assert not _has_file(origin, "main", "parent.txt")


def test_cross_round_human_gate_preserves_open_same_repo_stack(tmp_path, bare_origin) -> None:
    """Attesting a human between two repo nodes retains the parent's real PR base."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-human-stack")
    Registry().register(str(canonical), workflow="remote", repo_type="team")
    workspace = Workspace(tmp_path / "human-stack-worktrees")
    github = FakeGitHub(origin)

    plan_mapping = {
        "tasks": [
            {
                "id": "parent",
                "repo": str(canonical),
                "persona": "engineer",
                "task": "parent",
                "branch": "feature/human-stack-parent",
            },
            {
                "id": "approval",
                "kind": "human",
                "task": "Approve the parent before the child starts.",
                "deps": ["parent"],
            },
            {
                "id": "child",
                "repo": str(canonical),
                "persona": "engineer",
                "task": "child",
                "branch": "feature/human-stack-child",
                "deps": ["approval"],
            },
        ]
    }

    def lifecycle_runner(node: RepoPlanNode, *, journal: NodeSink | None = None):
        return run_repo_task(
            node.repo,
            node.task,
            node.persona,
            workspace=workspace,
            github=github,
            branch=node.branch,
            dispatch_fn=make_writing_dispatch(filename=f"{node.id}.txt"),
            verify_cmd=["true"],
            stack_bases=node.stack_bases,
            journal=journal,
        )

    def no_direct_agent(_node):
        raise AssertionError("this graph contains no direct agent")

    first = run_graph(
        parse_graph(plan_mapping),
        agent_runner=no_direct_agent,
        lifecycle_runner=lifecycle_runner,
    )
    first_payload = graph_payload(first)
    parent = first.results["parent"].lifecycle
    assert first.state == "waiting" and parent is not None and parent.outcome == "pr-open"
    assert first.results["approval"].status == "waiting"
    assert first.results["child"].status == "blocked"

    continued_mapping = next_round(
        plan_mapping,
        first_payload,
        {"complete_human": ["approval"]},
    )
    child_mapping = continued_mapping["tasks"][0]
    assert child_mapping["id"] == "child" and child_mapping["deps"] == []
    assert child_mapping["stack_bases"][0]["branch"] == parent.branch

    second = run_graph(
        parse_graph(continued_mapping),
        agent_runner=no_direct_agent,
        lifecycle_runner=lifecycle_runner,
    )
    child = second.results["child"].lifecycle
    assert second.ok and child is not None and child.outcome == "pr-open"
    assert child.pr_base == parent.branch
    assert child.pr is not None and child.pr.base == parent.branch
    assert _has_file(origin, child.branch, "parent.txt")
    assert _has_file(origin, child.branch, "child.txt")
    assert not _has_file(origin, "main", "parent.txt")


def test_cross_round_root_merged_pr_anchor_is_dropped_after_squash(tmp_path, bare_origin) -> None:
    """A root-landed squash is detected from PR state even without commit ancestry."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-squash-stack")
    Registry().register(str(canonical), workflow="remote", repo_type="team")
    workspace = Workspace(tmp_path / "squash-stack-worktrees")
    github = FakeGitHub(origin)
    parent = run_repo_task(
        str(canonical),
        "parent",
        "engineer",
        workspace=workspace,
        github=github,
        branch="feature/squashed-parent",
        dispatch_fn=make_writing_dispatch(filename="parent.txt"),
        verify_cmd=["true"],
    )
    assert parent.outcome == "pr-open" and parent.pr is not None

    merger = gitops.clone(origin, tmp_path / "squash-merger")
    subprocess.run(
        ["git", "-C", str(merger), "merge", "--squash", f"origin/{parent.branch}"],
        check=True,
        capture_output=True,
    )
    gitops.commit(merger, "squash parent")
    gitops.push(merger, "main", set_upstream=False)
    github._prs[parent.pr.number].merged = True
    gitops.fetch(canonical)
    assert not gitops.is_ancestor(canonical, f"origin/{parent.branch}", "origin/main")

    child = run_repo_task(
        str(canonical),
        "child",
        "engineer",
        workspace=workspace,
        github=github,
        branch="feature/post-squash-child",
        dispatch_fn=make_writing_dispatch(filename="child.txt"),
        verify_cmd=["true"],
        stack_bases=[
            StackBase(
                parent.branch,
                repo=parent.repo,
                identity=parent.publication_identity,
                base_branch=parent.base_branch,
                pr=parent.pr.url,
                pr_base=parent.pr_base,
            )
        ],
    )

    assert child.outcome == "pr-open" and child.pr_base == "main"
    assert child.stack_bases == []
    assert child.pr is not None and child.pr.base == "main"
    assert _has_file(origin, child.branch, "parent.txt")


def test_legacy_untyped_anchor_from_other_repo_is_scheduling_only(tmp_path, bare_origin) -> None:
    left_origin = bare_origin()
    right_origin = bare_origin()
    left = gitops.clone(left_origin, tmp_path / "legacy-anchor-left")
    right = gitops.clone(right_origin, tmp_path / "legacy-anchor-right")
    registry = Registry()
    registry.register(str(left), workflow="remote", repo_type="team")
    registry.register(str(right), workflow="remote", repo_type="team")
    left_result = run_repo_task(
        str(left),
        "left",
        "engineer",
        workspace=Workspace(tmp_path / "legacy-left-worktrees"),
        github=FakeGitHub(left_origin),
        branch="feature/legacy-left",
        dispatch_fn=make_writing_dispatch(filename="left.txt"),
        verify_cmd=["true"],
    )
    assert left_result.outcome == "pr-open"

    right_result = run_repo_task(
        str(right),
        "right",
        "engineer",
        workspace=Workspace(tmp_path / "legacy-right-worktrees"),
        github=FakeGitHub(right_origin),
        branch="feature/legacy-right",
        dispatch_fn=make_writing_dispatch(filename="right.txt"),
        verify_cmd=["true"],
        stack_bases=[StackBase(left_result.branch, repo=left_result.repo)],
    )

    assert right_result.outcome == "pr-open" and right_result.pr_base == "main"
    assert right_result.stack_bases == []
    assert _has_file(right_origin, right_result.branch, "right.txt")


def test_stack_anchor_for_different_root_fails_before_dispatch(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-root-mismatch")
    Registry().register(str(canonical), workflow="remote", repo_type="team")
    workspace = Workspace(tmp_path / "root-mismatch-worktrees")
    github = FakeGitHub(origin)
    parent = run_repo_task(
        str(canonical),
        "parent",
        "engineer",
        workspace=workspace,
        github=github,
        branch="feature/root-mismatch-parent",
        dispatch_fn=make_writing_dispatch(filename="parent.txt"),
        verify_cmd=["true"],
    )
    dispatched: list[str] = []

    def dispatch_fn(persona: str, task: str, **_: object) -> Report:
        dispatched.append(task)
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    child = run_repo_task(
        str(canonical),
        "child",
        "engineer",
        workspace=workspace,
        github=github,
        branch="feature/root-mismatch-child",
        dispatch_fn=dispatch_fn,
        verify_cmd=["true"],
        stack_bases=[
            StackBase(
                parent.branch,
                repo=parent.repo,
                identity=parent.publication_identity,
                base_branch="release",
            )
        ],
    )

    assert child.outcome == "stack-conflict" and child.pr_base == "main"
    assert "belongs to root 'release'" in child.detail
    assert dispatched == []


def test_closed_and_missing_stack_anchors_fail_before_dispatch(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-invalid-anchors")
    Registry().register(str(canonical), workflow="remote", repo_type="team")
    workspace = Workspace(tmp_path / "invalid-anchor-worktrees")
    github = FakeGitHub(origin)
    parent = run_repo_task(
        str(canonical),
        "parent",
        "engineer",
        workspace=workspace,
        github=github,
        branch="feature/closed-parent",
        dispatch_fn=make_writing_dispatch(filename="parent.txt"),
        verify_cmd=["true"],
    )
    assert parent.pr is not None
    github._prs[parent.pr.number].closed = True
    anchor = StackBase(
        parent.branch,
        repo=parent.repo,
        identity=parent.publication_identity,
        base_branch=parent.base_branch,
        pr=parent.pr.url,
        pr_base=parent.pr_base,
    )

    closed = run_repo_task(
        str(canonical),
        "closed child",
        "engineer",
        workspace=workspace,
        github=github,
        branch="feature/closed-child",
        dispatch_fn=make_writing_dispatch(filename="closed.txt"),
        verify_cmd=["true"],
        stack_bases=[anchor],
    )
    missing = run_repo_task(
        str(canonical),
        "missing child",
        "engineer",
        workspace=workspace,
        github=github,
        branch="feature/missing-child",
        dispatch_fn=make_writing_dispatch(filename="missing.txt"),
        verify_cmd=["true"],
        stack_bases=[
            StackBase(
                "feature/deleted-parent",
                repo=parent.repo,
                identity=parent.publication_identity,
                base_branch=parent.base_branch,
            )
        ],
    )

    assert closed.outcome == "stack-conflict" and "closed without merging" in closed.detail
    assert missing.outcome == "stack-conflict" and "missing from origin" in missing.detail


def test_multi_parent_stack_uses_synthetic_base_and_child_only_diff(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-multi-stack")
    Registry().register(str(canonical), workflow="remote", repo_type="team")
    workspace = Workspace(tmp_path / "multi-stack-worktrees")
    github = FakeGitHub(origin)

    def runner(node: RepoPlanNode, *, journal: NodeSink | None = None):
        return run_repo_task(
            node.repo,
            node.task,
            node.persona,
            workspace=workspace,
            github=github,
            branch=node.branch,
            dispatch_fn=make_writing_dispatch(filename=f"{node.id}.txt"),
            verify_cmd=["true"],
            stack_bases=node.stack_bases,
            journal=journal,
        )

    result = run_repo_plan(
        RepoPlan(
            [
                RepoPlanNode("left", str(canonical), "engineer", "left", branch="feature/left"),
                RepoPlanNode("right", str(canonical), "engineer", "right", branch="feature/right"),
                RepoPlanNode(
                    "child",
                    str(canonical),
                    "engineer",
                    "child",
                    deps=["left", "right"],
                    branch="feature/combined-child",
                ),
            ],
            concurrency=2,
        ),
        runner,
    )

    child = result.results["child"].result
    assert result.ok and child is not None and child.outcome == "pr-open"
    synthetic = child.synthetic_stack_base
    assert synthetic is not None and synthetic.startswith("ai-orchestrator/stack-base/")
    assert child.pr_base == synthetic and child.pr is not None and child.pr.base == synthetic
    assert _has_file(origin, synthetic, "left.txt")
    assert _has_file(origin, synthetic, "right.txt")
    changed = subprocess.run(
        ["git", "-C", str(origin), "diff", "--name-only", synthetic, child.branch],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.splitlines()
    assert changed == ["child.txt"]
    assert all(state.head != synthetic for state in github._prs.values())


def test_multi_parent_stack_deduplicates_ancestor_prerequisites(tmp_path, bare_origin) -> None:
    """A declared ancestor after its descendant is a safe no-op in stack assembly."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-ancestry-stack")
    Registry().register(str(canonical), workflow="remote", repo_type="team")
    workspace = Workspace(tmp_path / "ancestry-stack-worktrees")
    github = FakeGitHub(origin)

    def runner(node: RepoPlanNode, *, journal: NodeSink | None = None):
        return run_repo_task(
            node.repo,
            node.task,
            node.persona,
            workspace=workspace,
            github=github,
            branch=node.branch,
            dispatch_fn=make_writing_dispatch(filename=f"{node.id}.txt"),
            verify_cmd=["true"],
            stack_bases=node.stack_bases,
            journal=journal,
        )

    result = run_repo_plan(
        RepoPlan(
            [
                RepoPlanNode(
                    "ancestor",
                    str(canonical),
                    "engineer",
                    "ancestor",
                    branch="feature/ancestor",
                ),
                RepoPlanNode(
                    "descendant",
                    str(canonical),
                    "engineer",
                    "descendant",
                    deps=["ancestor"],
                    branch="feature/descendant",
                ),
                RepoPlanNode(
                    "child",
                    str(canonical),
                    "engineer",
                    "child",
                    deps=["descendant", "ancestor"],
                    branch="feature/ancestry-child",
                ),
            ]
        ),
        runner,
    )

    ancestor = result.results["ancestor"].result
    descendant = result.results["descendant"].result
    child = result.results["child"].result
    assert result.ok and ancestor is not None and descendant is not None and child is not None
    assert child.synthetic_stack_base is None
    assert child.pr is not None and child.pr.base == descendant.branch
    assert ancestor.pr is not None and descendant.pr is not None
    assert ancestor.pr.url not in github._prs[3].body
    assert descendant.pr.url in github._prs[3].body


def test_stack_conflict_aborts_before_child_dispatch_and_skips_descendant(
    tmp_path, bare_origin
) -> None:
    origin = bare_origin({"shared.txt": "root\n"})
    canonical = gitops.clone(origin, tmp_path / "canonical-conflict-stack")
    Registry().register(str(canonical), workflow="remote", repo_type="team")
    workspace = Workspace(tmp_path / "conflict-stack-worktrees")
    github = FakeGitHub(origin)
    dispatched: list[str] = []

    def writing_dispatch(persona: str, task: str, *, project_dir: str, **_: object) -> Report:
        dispatched.append(task)
        path = Path(project_dir) / "shared.txt"
        path.write_text(f"{task}\n", encoding="utf-8")
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    def runner(node: RepoPlanNode, *, journal: NodeSink | None = None):
        return run_repo_task(
            node.repo,
            node.task,
            node.persona,
            workspace=workspace,
            github=github,
            branch=node.branch,
            dispatch_fn=writing_dispatch,
            verify_cmd=["true"],
            stack_bases=node.stack_bases,
            journal=journal,
        )

    result = run_repo_plan(
        RepoPlan(
            [
                RepoPlanNode(
                    "left",
                    str(canonical),
                    "engineer",
                    "left",
                    branch="feature/conflict-left",
                ),
                RepoPlanNode(
                    "right",
                    str(canonical),
                    "engineer",
                    "right",
                    branch="feature/conflict-right",
                ),
                RepoPlanNode(
                    "child",
                    str(canonical),
                    "engineer",
                    "child",
                    deps=["left", "right"],
                    branch="feature/conflict-child",
                ),
                RepoPlanNode(
                    "descendant",
                    str(canonical),
                    "engineer",
                    "descendant",
                    deps=["child"],
                ),
            ],
            concurrency=2,
        ),
        runner,
    )

    child = result.results["child"].result
    assert child is not None and child.outcome == "stack-conflict"
    assert result.results["child"].status == "failed"
    assert result.results["descendant"].status == "skipped"
    assert "child" not in dispatched and "descendant" not in dispatched
    refs = subprocess.run(
        [
            "git",
            "-C",
            str(origin),
            "for-each-ref",
            "--format=%(refname:short)",
            "refs/heads/ai-orchestrator/stack-base",
        ],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    assert not refs.strip()
    assert not any(
        branch.startswith("ai-orchestrator/stack-base/") for branch in gitops.branches(canonical)
    )


# --- workstream: several onejudge on ONE PR -------------------------------


def test_local_human_workstream_removes_worktree_and_resumes_same_branch(
    tmp_path, bare_origin
) -> None:
    """A local agent → human → agent workstream publishes only after resume."""
    origin = bare_origin()
    before = _tip(origin, "main")
    workspace = _workspace(tmp_path, origin)
    dispatched: list[str] = []

    def writing_step(
        persona: str, task: str, *, project_dir: str, session: str, **_: object
    ) -> Report:
        sid = session.rsplit(":", 1)[-1]
        dispatched.append(sid)
        (Path(project_dir) / f"{sid}.txt").write_text(f"{task} by {persona}\n", encoding="utf-8")
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    gate_log = tmp_path / "local-human-gates.log"
    gate = [
        "sh",
        "-c",
        f"echo gate >> {shlex.quote(str(gate_log))}; test -f prepare.txt && test -f finalize.txt",
    ]
    steps = [
        Step("prepare", "engineer", "prepare the change"),
        Step("approve", task="Approve the prepared change.", kind="human", deps=["prepare"]),
        Step("finalize", "engineer", "finalize the change", deps=["approve"]),
    ]

    paused = run_repo_task(
        str(origin),
        workspace=workspace,
        steps=steps,
        branch="feature/local-human-resume",
        dispatch_fn=writing_step,
        verify_cmd=gate,
    )

    assert paused.outcome == "waiting-human" and paused.resume is not None
    assert dispatched == ["prepare"]
    assert [step.status for step in paused.steps] == ["done", "waiting", "blocked"]
    assert paused.waiting_steps == ["approve"]
    assert paused.resume.branch == paused.branch == "feature/local-human-resume"
    assert paused.resume.base_branch == paused.resume.pr_base == "main"
    assert paused.resume.completed_steps == ("prepare",)
    clone = workspace.clone_dir(normalize_repo(str(origin)))
    assert paused.resume.checkpoint == gitops.ref_sha(clone, paused.branch)
    assert gitops.branch_exists(clone, paused.branch)
    assert not _has_file(origin, paused.branch, "prepare.txt")
    assert _tip(origin, "main") == before
    assert not gate_log.exists()
    worktrees = subprocess.run(
        ["git", "-C", str(clone), "worktree", "list", "--porcelain"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    assert "local-human-resume" not in worktrees

    resume = replace(
        paused.resume,
        completed_steps=(*paused.resume.completed_steps, "approve"),
    )
    completed = run_repo_task(
        str(origin),
        workspace=workspace,
        steps=steps,
        dispatch_fn=writing_step,
        verify_cmd=gate,
        resume=resume,
    )

    assert completed.ok and completed.outcome == "merged", completed.detail
    assert completed.branch == paused.branch
    assert dispatched == ["prepare", "finalize"]
    assert [step.status for step in completed.steps] == ["done", "done", "done"]
    assert _has_file(origin, "main", "prepare.txt")
    assert _has_file(origin, "main", "finalize.txt")
    assert gate_log.read_text(encoding="utf-8").splitlines() == ["gate", "gate"]
    final_worktrees = subprocess.run(
        ["git", "-C", str(clone), "worktree", "list", "--porcelain"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    assert "local-human-resume" not in final_worktrees


def test_workstream_multiple_onejudge_one_pr(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    steps = [
        Step("impl", "engineer", "implement the feature"),
        Step("test", "engineer", "add tests", deps=["impl"]),
        Step("docs", "docs-writer", "document it", deps=["impl"]),
    ]
    result = run_repo_task(
        str(origin),
        workspace=_workspace(tmp_path, origin),
        steps=steps,
        dispatch_fn=_per_step_dispatch(),
        verify_cmd=["true"],
    )
    assert result.ok and result.outcome == "merged"
    assert len(result.steps) == 3 and all(s.status == "done" for s in result.steps)
    step_subjects = [
        commit.message.splitlines()[0]
        for commit in gitops.log_messages(origin, before, result.branch)
    ]
    assert step_subjects == [
        "chore: implement the feature",
        "chore: add tests",
        "chore: document it",
    ]
    # All three steps' changes landed on origin main via ONE merge of ONE branch.
    for sid in ("impl", "test", "docs"):
        assert _has_file(origin, "main", f"{sid}.txt")


def test_step_commit_subject_comes_from_agent_commits(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")

    def partly_committing_dispatch(
        persona: str, task: str, *, project_dir: str, **_: object
    ) -> Report:
        worktree = Path(project_dir)
        (worktree / "committed.txt").write_text("agent commit\n", encoding="utf-8")
        gitops.add_all(worktree)
        gitops.commit(worktree, "fix(capture): retain failed output")
        (worktree / "remaining.txt").write_text("remaining step work\n", encoding="utf-8")
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    result = run_repo_task(
        str(origin),
        "Repair capture using task prose that is not a commit subject.",
        "engineer",
        workspace=_workspace(tmp_path, origin),
        dispatch_fn=partly_committing_dispatch,
        verify_cmd=["true"],
    )

    assert result.ok
    subjects = [
        commit.message.splitlines()[0]
        for commit in gitops.log_messages(origin, before, result.branch)
    ]
    assert subjects == [
        "fix(capture): retain failed output",
        "fix(capture): retain failed output",
    ]


def test_workstream_step_failure_stops_and_skips_dependents(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    workspace = _workspace(tmp_path, origin)
    steps = [
        Step("impl", "engineer", "implement"),
        Step("test", "engineer", "add tests", deps=["impl"]),
    ]
    result = run_repo_task(
        str(origin),
        workspace=workspace,
        steps=steps,
        dispatch_fn=_per_step_dispatch(fail_step="impl"),  # first step hits the turn cap
        verify_cmd=["true"],
    )
    assert not result.ok and result.outcome == "not-completed"
    by_id = {s.id: s.status for s in result.steps}
    assert by_id["impl"] == "not-completed" and by_id["test"] == "skipped"
    assert _tip(origin, "main") == before  # nothing pushed or merged
    clone = workspace.clone_dir(normalize_repo(str(origin)))
    assert gitops.ref_sha(clone, result.branch) == gitops.ref_sha(clone, "origin/main")
    assert incomplete_commits(clone, "origin/main", result.branch) == set()


def test_multi_pr_failure_skips_dependents(tmp_path, bare_origin) -> None:
    repo_x = bare_origin()
    ws = _workspace(tmp_path, repo_x)

    def runner(node: RepoPlanNode, *, journal: NodeSink | None = None):
        # node 'a' fails its gate; 'b' depends on it and must be skipped.
        return run_repo_task(
            node.repo,
            node.task,
            node.persona,
            workspace=ws,
            dispatch_fn=make_writing_dispatch(filename=f"{node.id}.txt"),
            verify_cmd=["false"] if node.id == "a" else ["true"],
            journal=journal,
        )

    plan = RepoPlan(
        tasks=[
            RepoPlanNode("a", str(repo_x), "engineer", "failing part"),
            RepoPlanNode("b", str(repo_x), "reviewer", "dependent part", deps=["a"]),
        ],
        concurrency=2,
    )
    result = run_repo_plan(plan, runner)
    assert not result.ok
    assert result.results["a"].status == "failed"
    assert result.results["b"].status == "skipped"
