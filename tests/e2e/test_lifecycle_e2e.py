"""E2E: drive the real repo lifecycle end to end against a real bare git origin.

Git is real throughout (clone → worktree → branch → commit → push → merge on a
local bare repo). Only the two external seams are faked: the paid harness
(`writing_dispatch`, which makes a real edit) and GitHub's PR/CI decisioning
(`FakeGitHub`, which performs the merge with real git). So these prove the whole
journey — local direct-merge and the GitHub PR+auto-merge path — for real.
"""

# llmlint: ignore-file[e2e_not_mocked] the repo-lifecycle e2e fakes ONLY the paid harness
# (the dispatch_fn seam) and GitHub's PR/CI decisioning while driving real git and the
# real merge, exactly as AGENTS.md prescribes for lifecycle tests; the real onejudge
# dispatch boundary is covered separately in test_dispatch_e2e.py.

from __future__ import annotations

import json
import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest
from fakes import FakeGitHub, make_writing_dispatch

from orchestrator import gitops
from orchestrator.dispatch import Report
from orchestrator.github import PullRequest
from orchestrator.lifecycle import (
    RepoPlan,
    RepoPlanNode,
    StackBase,
    Step,
    main_plan,
    run_repo_plan,
    run_repo_task,
)
from orchestrator.merge import GitHubMergeStrategy
from orchestrator.next_round import main as next_round_main
from orchestrator.next_round import main_runs
from orchestrator.registry import Registry
from orchestrator.workspace import Workspace, normalize_repo


def _workspace(tmp_path: Path, *origins: Path) -> Workspace:
    """Build a registry-like resolver over real canonical test checkouts."""
    checkouts = {
        str(origin.resolve()): gitops.clone(origin, tmp_path / f"canonical-{index}")
        for index, origin in enumerate(origins)
    }
    return Workspace(
        tmp_path / "worktrees",
        resolver=lambda spec: checkouts[str(Path(spec).resolve())],
        workflow="local",
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
                "persona": "backend-engineer",
                "task": "should-fail write-change: preserve this partial attempt",
                "skip_verify": True,
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
    assert publication["repository_type"] == publication["repo_type"] == "single-owner"
    assert publication["publication_workflow"] == publication["workflow"] == "local"
    assert publication["merge_policy"] == "direct"
    assert publication["base_branch"] == publication["pr_base"] == "main"
    assert publication["synthetic_stack_base"] is None and publication["stack_bases"] == []
    assert follow_up in captured.out
    assert main_runs(["--runs-dir", str(runs_dir)]) == 0
    assert follow_up in capsys.readouterr().out
    assert "nothing to iterate" in captured.err


# --- local repo: direct merge into main after checks -----------------------


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
        "backend-engineer",
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
        "backend-engineer",
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
        "backend-engineer",
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


def test_team_default_opens_ready_for_review_pr_without_polling(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-team")
    Registry().register(str(canonical), workflow="remote", repo_type="team")
    github = FakeGitHub(origin, fail_checks=True)

    result = run_repo_task(
        str(canonical),
        "Open a team-owned change for review.",
        "backend-engineer",
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
        "backend-engineer",
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
        "backend-engineer",
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
        "backend-engineer",
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
    ws = _workspace(tmp_path, origin)
    result = run_repo_task(
        str(origin),  # a local path → local direct-merge strategy is auto-selected
        "Add a change file.",
        "backend-engineer",
        workspace=ws,
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["true"],  # explicit gate so the test never depends on make/just
    )
    assert result.ok, result.detail
    assert result.outcome == "merged"
    assert result.base_branch == "main"
    assert _has_file(origin, "main", "feature.txt")  # the change really landed on origin main
    canonical = ws.clone_dir(normalize_repo(str(origin)))
    assert gitops.current_branch(canonical) == "main"
    assert gitops.head_sha(canonical) == _tip(origin, "main")


def test_local_repo_non_main_default_and_gate_context(tmp_path, bare_origin) -> None:
    origin = bare_origin(branch="master")
    ws = _workspace(tmp_path, origin)
    result = run_repo_task(
        str(origin),
        "Add a portable change.",
        "backend-engineer",
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


def test_competing_local_publishers_rebuild_and_reverify_after_push_race(
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
            "backend-engineer",
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
    assert verification_log.read_text(encoding="utf-8").splitlines().count("b-stale") == 1
    assert verification_log.read_text(encoding="utf-8").splitlines().count("b-rebuilt") == 1


def test_local_repo_gate_failure_blocks_merge(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    result = run_repo_task(
        str(origin),
        "Add a change that fails the gate.",
        "backend-engineer",
        workspace=_workspace(tmp_path, origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["false"],  # gate fails → never pushes or merges
    )
    assert not result.ok
    assert result.outcome == "gate-failed"
    assert result.pr is None
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
        "backend-engineer",
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


def test_local_repo_sync_conflict_aborts_before_gate_or_push(tmp_path, bare_origin) -> None:
    origin = bare_origin({"shared.txt": "original\n"})
    branch_dispatch = make_writing_dispatch(filename="shared.txt", content="agent")

    def conflicting_dispatch(
        persona: str, task: str, *, project_dir: str, **kwargs: object
    ) -> Report:
        report = branch_dispatch(persona, task, project_dir=project_dir, **kwargs)
        _advance_origin(tmp_path, origin, "shared.txt", "concurrent base\n")
        return report

    result = run_repo_task(
        str(origin),
        "Edit the same file as a concurrent main change.",
        "backend-engineer",
        workspace=_workspace(tmp_path, origin),
        dispatch_fn=conflicting_dispatch,
        verify_cmd=["sh", "-c", "touch GATE_RAN && false"],
    )

    assert not result.ok and result.outcome == "gate-failed"
    assert "sync-conflict" in result.detail
    assert result.verify is None
    assert not _has_file(origin, result.branch, "shared.txt")
    assert not _has_file(origin, "main", "GATE_RAN")
    assert (
        subprocess.run(
            ["git", "-C", str(origin), "show", "main:shared.txt"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
        == "concurrent base\n"
    )


def test_local_repo_no_gate_detected_proceeds(tmp_path, bare_origin) -> None:
    # The seed repo has no recognized gate; with no explicit verify_cmd the
    # lifecycle notes that and relies on downstream checks, still merging.
    origin = bare_origin()
    result = run_repo_task(
        str(origin),
        "Add a change with no detectable local gate.",
        "backend-engineer",
        workspace=_workspace(tmp_path, origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        # no verify_cmd → detect_gate finds nothing (only README in the repo)
    )
    assert result.ok and result.outcome == "merged"
    assert "no local gate" in result.detail or result.verify is None
    assert _has_file(origin, "main", "feature.txt")


def test_skip_verify_bypasses_the_gate(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    result = run_repo_task(
        str(origin),
        "Add a change with the gate skipped.",
        "backend-engineer",
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
        "backend-engineer",
        workspace=ws,
        dispatch_fn=make_writing_dispatch(filename="partial.txt", completed=False),
        verify_cmd=["true"],
    )
    assert result.outcome == "not-completed"
    assert not result.ok
    assert f"committed to branch '{result.branch}'" in result.detail

    clone = ws.clone_dir(normalize_repo(str(origin)))
    assert _has_file(clone, result.branch, "partial.txt")
    subject = subprocess.run(
        ["git", "-C", str(clone), "log", "-1", "--format=%s", result.branch],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    assert subject.startswith("wip:") and "incomplete step" in subject
    assert not _has_file(origin, "main", "partial.txt")
    assert not _has_file(origin, result.branch, "partial.txt")  # incomplete work is not pushed


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


# --- GitHub repo: PR + auto-merge on required checks -----------------------


def test_github_auto_merge_on_required_checks(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    github = FakeGitHub(origin, required=("ci",))
    workspace = _workspace(tmp_path, origin)
    result = run_repo_task(
        "acme/widget",  # a GitHub-style slug → GitHub strategy
        "Add a feature file.",
        "backend-engineer",
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
    canonical = workspace.clone_dir(normalize_repo("acme/widget"))
    assert gitops.head_sha(canonical) == _tip(origin, "main")


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
        "backend-engineer",
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
        "backend-engineer",
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
        "backend-engineer",
        workspace=_workspace(tmp_path, origin),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["true"],
        sleep=lambda _: None,
    )
    assert result.ok and result.outcome == "merged"
    assert _has_file(origin, "main", "feature.txt")


def test_github_required_check_failure_blocks_merge(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    github = FakeGitHub(origin, fail_checks=True)
    result = run_repo_task(
        "acme/widget",
        "Add a feature file.",
        "backend-engineer",
        workspace=_workspace(tmp_path, origin),
        merge=GitHubMergeStrategy(github),
        url=str(origin),
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        verify_cmd=["true"],
        sleep=lambda _: None,
        timeout=5.0,
    )
    assert not result.ok
    assert result.outcome == "checks-failed"
    assert _tip(origin, "main") == before  # required check failed → nothing merged


# --- multi-PR: one larger task across coordinated PRs ----------------------


def test_multi_pr_dag_across_repos(tmp_path, bare_origin) -> None:
    repo_x = bare_origin()
    repo_y = bare_origin()
    ws = _workspace(tmp_path, repo_x, repo_y)

    def runner(node: RepoPlanNode):
        return run_repo_task(
            node.repo,
            node.task,
            node.persona,
            workspace=ws,
            dispatch_fn=make_writing_dispatch(filename=f"{node.id}.txt"),
            verify_cmd=["true"],
        )

    plan = RepoPlan(
        tasks=[
            RepoPlanNode("a", str(repo_x), "backend-engineer", "part A on repo X"),
            RepoPlanNode("b", str(repo_y), "backend-engineer", "part B on repo Y"),
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

    def runner(node: RepoPlanNode):
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
        )

    result = run_repo_plan(
        RepoPlan(
            [
                RepoPlanNode(
                    "parent", str(canonical), "backend-engineer", "parent", branch="feature/parent"
                ),
                RepoPlanNode(
                    "child",
                    str(canonical),
                    "backend-engineer",
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
        "backend-engineer",
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
        "backend-engineer",
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
        "backend-engineer",
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
        "backend-engineer",
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
        "backend-engineer",
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
        "backend-engineer",
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
        "backend-engineer",
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
        "backend-engineer",
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
        "backend-engineer",
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

    def runner(node: RepoPlanNode):
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
        )

    result = run_repo_plan(
        RepoPlan(
            [
                RepoPlanNode(
                    "left", str(canonical), "backend-engineer", "left", branch="feature/left"
                ),
                RepoPlanNode(
                    "right", str(canonical), "backend-engineer", "right", branch="feature/right"
                ),
                RepoPlanNode(
                    "child",
                    str(canonical),
                    "backend-engineer",
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

    def runner(node: RepoPlanNode):
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
        )

    result = run_repo_plan(
        RepoPlan(
            [
                RepoPlanNode(
                    "ancestor",
                    str(canonical),
                    "backend-engineer",
                    "ancestor",
                    branch="feature/ancestor",
                ),
                RepoPlanNode(
                    "descendant",
                    str(canonical),
                    "backend-engineer",
                    "descendant",
                    deps=["ancestor"],
                    branch="feature/descendant",
                ),
                RepoPlanNode(
                    "child",
                    str(canonical),
                    "backend-engineer",
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

    def runner(node: RepoPlanNode):
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
        )

    result = run_repo_plan(
        RepoPlan(
            [
                RepoPlanNode(
                    "left",
                    str(canonical),
                    "backend-engineer",
                    "left",
                    branch="feature/conflict-left",
                ),
                RepoPlanNode(
                    "right",
                    str(canonical),
                    "backend-engineer",
                    "right",
                    branch="feature/conflict-right",
                ),
                RepoPlanNode(
                    "child",
                    str(canonical),
                    "backend-engineer",
                    "child",
                    deps=["left", "right"],
                    branch="feature/conflict-child",
                ),
                RepoPlanNode(
                    "descendant",
                    str(canonical),
                    "backend-engineer",
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


def test_workstream_multiple_onejudge_one_pr(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    steps = [
        Step("impl", "backend-engineer", "implement the feature"),
        Step("test", "test-engineer", "add tests", deps=["impl"]),
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
    # All three steps' changes landed on origin main via ONE merge of ONE branch.
    for sid in ("impl", "test", "docs"):
        assert _has_file(origin, "main", f"{sid}.txt")


def test_workstream_step_failure_stops_and_skips_dependents(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    before = _tip(origin, "main")
    steps = [
        Step("impl", "backend-engineer", "implement"),
        Step("test", "test-engineer", "add tests", deps=["impl"]),
    ]
    result = run_repo_task(
        str(origin),
        workspace=_workspace(tmp_path, origin),
        steps=steps,
        dispatch_fn=_per_step_dispatch(fail_step="impl"),  # first step hits the turn cap
        verify_cmd=["true"],
    )
    assert not result.ok and result.outcome == "not-completed"
    by_id = {s.id: s.status for s in result.steps}
    assert by_id["impl"] == "not-completed" and by_id["test"] == "skipped"
    assert _tip(origin, "main") == before  # nothing pushed or merged


def test_multi_pr_failure_skips_dependents(tmp_path, bare_origin) -> None:
    repo_x = bare_origin()
    ws = _workspace(tmp_path, repo_x)

    def runner(node: RepoPlanNode):
        # node 'a' fails its gate; 'b' depends on it and must be skipped.
        return run_repo_task(
            node.repo,
            node.task,
            node.persona,
            workspace=ws,
            dispatch_fn=make_writing_dispatch(filename=f"{node.id}.txt"),
            verify_cmd=["false"] if node.id == "a" else ["true"],
        )

    plan = RepoPlan(
        tasks=[
            RepoPlanNode("a", str(repo_x), "backend-engineer", "failing part"),
            RepoPlanNode("b", str(repo_x), "reviewer", "dependent part", deps=["a"]),
        ],
        concurrency=2,
    )
    result = run_repo_plan(plan, runner)
    assert not result.ok
    assert result.results["a"].status == "failed"
    assert result.results["b"].status == "skipped"
