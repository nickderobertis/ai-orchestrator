"""E2E journeys for the integration train, using real git and a bare origin."""

# llmlint: ignore-file[e2e_not_mocked] one installed-console journey proves the CLI artifact;
# the remaining real-git journeys call its public core in-process so pytest-cov can enforce the
# repository's 95% threshold. Git, worktrees, conflicts, gates, merges, and pushes remain real.

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest
from conftest import install_pre_push_hook
from fakes import FakeGitHub, make_writing_dispatch

from orchestrator import gitops
from orchestrator.integrate import IntegrateError, integrate, main
from orchestrator.lifecycle import StackBase, run_repo_task
from orchestrator.provenance import incomplete_commits
from orchestrator.recover import main as recover_main
from orchestrator.recover import recover_repo
from orchestrator.registry import Registry
from orchestrator.workspace import Workspace

ROOT = Path(__file__).parents[2]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, text=True, capture_output=True
    ).stdout.strip()


def _branch(repo: Path, name: str, files: dict[str, str], *, start: str = "main") -> None:
    _git(repo, "checkout", "-b", name, start)
    for relative, content in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", f"add {name}")
    _git(repo, "checkout", "main")


def _clone(tmp_path: Path, origin: Path) -> Path:
    repo = tmp_path / "clone"
    subprocess.run(["git", "clone", str(origin), str(repo)], check=True, capture_output=True)
    install_pre_push_hook(repo)
    return repo


def _allow_local(repo: Path) -> None:
    Registry().register(str(repo), workflow="local")


def _integrate(repo: Path, *branches: str, extra: tuple[str, ...] = ()) -> dict[str, object]:
    proc = subprocess.run(
        [
            "orchestrator-integrate",
            "--repo",
            str(repo),
            "--gate",
            "true",
            "--format",
            "json",
            *extra,
            *branches,
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(proc.stdout)


def _recover_cli(
    repo: Path, branch: str, workspace: Path, *, gate: str = "true", extra: tuple[str, ...] = ()
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "orchestrator-repo-recover",
            branch,
            "--repo",
            str(repo),
            "--workspace",
            str(workspace),
            "--gate",
            gate,
            "--format",
            "json",
            *extra,
        ],
        text=True,
        capture_output=True,
    )


def test_merge_train_pushes_all_branches_and_rerun_is_idempotent(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    repo = _clone(tmp_path, origin)
    _allow_local(repo)
    _branch(repo, "claude/a", {"a.txt": "a\n"})
    _branch(repo, "claude/b", {"b.txt": "b\n"})

    result = integrate(repo, ["claude/a", "claude/b"], gate_command=["true"], push=True)
    assert [item.status for item in result.branches] == ["merged", "merged"]
    assert result.base_advanced and result.pushed
    assert _git(origin, "show", "main:a.txt") == "a"
    assert _git(origin, "show", "main:b.txt") == "b"

    rerun = integrate(repo, ["claude/a", "claude/b"], gate_command=["true"], push=True)
    assert [item.status for item in rerun.branches] == ["already-merged", "already-merged"]
    assert not rerun.base_advanced and not rerun.pushed


def test_push_hook_receives_integration_comparison_with_ambiguous_remote_heads(
    tmp_path, bare_origin
) -> None:
    gate = """set shell := ["bash", "-euo", "pipefail", "-c"]
set positional-arguments

gate remote base:
    @test "$1/$2" = "origin/main"
    @test "$ORCHESTRATOR_COMPARISON_REMOTE/$ORCHESTRATOR_COMPARISON_BASE" = "origin/main"
    @printf '%s\\n' "$1/$2" > "$(git rev-parse --git-dir)/integration-hook-gate-ran"
"""
    origin = bare_origin({"justfile": gate})
    repo = _clone(tmp_path, origin)
    _allow_local(repo)
    _branch(repo, "claude/publish", {"published.txt": "published\n"})
    _branch(repo, "remote-feature", {"remote.txt": "remote\n"})
    _git(repo, "push", "origin", "remote-feature")
    _git(repo, "fetch", "origin")
    _git(repo, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
    _git(repo, "config", "core.hooksPath", str(ROOT / ".githooks"))

    resolved = subprocess.run(
        [str(ROOT / "scripts/comparison-base.sh"), "origin"],
        cwd=repo,
        env={**os.environ, "ORCHESTRATOR_COMPARISON_BASE": ""},
        text=True,
        capture_output=True,
    )
    assert resolved.returncode == 0
    assert resolved.stdout.strip() == "origin/main"

    result = integrate(
        repo,
        ["claude/publish"],
        gate_command=["true"],
        push=True,
    )

    assert result.base_advanced and result.pushed
    assert _git(origin, "show", "main:published.txt") == "published"
    git_dir = Path(_git(repo, "rev-parse", "--git-dir"))
    marker = (git_dir if git_dir.is_absolute() else repo / git_dir) / "integration-hook-gate-ran"
    assert marker.read_text(encoding="utf-8") == "origin/main\n"


def test_conflict_is_skipped_and_train_continues(tmp_path, bare_origin) -> None:
    origin = bare_origin({"shared.txt": "old\n"})
    repo = _clone(tmp_path, origin)
    _allow_local(repo)
    _branch(repo, "claude/conflict", {"shared.txt": "branch\n"})
    (repo / "shared.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "shared.txt")
    _git(repo, "commit", "-m", "advance base")
    _branch(repo, "claude/good", {"good.txt": "good\n"})

    result = integrate(repo, ["claude/conflict", "claude/good"], gate_command=["true"])
    assert [(item.status, item.reason) for item in result.branches] == [
        ("skipped", "conflict"),
        ("merged", None),
    ]
    assert (repo / "shared.txt").read_text(encoding="utf-8") != "branch\n"
    assert (repo / "good.txt").is_file()


def test_gate_failure_is_skipped_and_green_branch_merges(tmp_path, bare_origin) -> None:
    repo = _clone(tmp_path, bare_origin())
    _allow_local(repo)
    _branch(repo, "claude/red", {"FAIL": "red\n"})
    _branch(repo, "claude/green", {"green.txt": "green\n"})

    result = integrate(
        repo,
        ["claude/red", "claude/green"],
        gate_command=["sh", "-c", "test ! -f FAIL"],
    )
    assert [(item.status, item.reason) for item in result.branches] == [
        ("skipped", "gate-failed"),
        ("merged", None),
    ]
    assert not (repo / "FAIL").exists()
    assert (repo / "green.txt").is_file()


def test_refresh_updates_discovered_branch_without_merging(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    repo = _clone(tmp_path, origin)
    _branch(repo, "claude/in-flight", {"feature.txt": "feature\n"})
    updater = tmp_path / "updater"
    subprocess.run(["git", "clone", str(origin), str(updater)], check=True, capture_output=True)
    (updater / "base.txt").write_text("base\n", encoding="utf-8")
    _git(updater, "add", "base.txt")
    _git(updater, "commit", "-m", "advance remote base")
    _git(updater, "push", "origin", "main")
    base_tip = _git(origin, "rev-parse", "main")

    result = integrate(repo, refresh=True)
    assert [(item.branch, item.status) for item in result.branches] == [
        ("claude/in-flight", "updated")
    ]
    assert not result.base_advanced
    assert _git(repo, "merge-base", "--is-ancestor", base_tip, "claude/in-flight") == ""
    assert not (repo / "feature.txt").exists()


def test_dirty_candidate_worktree_is_rejected(tmp_path, bare_origin) -> None:
    repo = _clone(tmp_path, bare_origin())
    _allow_local(repo)
    _branch(repo, "claude/dirty", {"feature.txt": "feature\n"})
    candidate = tmp_path / "candidate"
    _git(repo, "worktree", "add", str(candidate), "claude/dirty")
    (candidate / "untracked").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(IntegrateError, match="candidate worktree.*is dirty"):
        integrate(repo, ["claude/dirty"], gate_command=["true"])

    assert _git(repo, "status", "--porcelain") == ""
    _git(repo, "worktree", "remove", "--force", str(candidate))


def test_base_merge_failure_is_aborted_and_skipped(tmp_path, bare_origin) -> None:
    repo = _clone(tmp_path, bare_origin())
    _allow_local(repo)
    _branch(repo, "claude/racing", {"shared.txt": "candidate\n"})
    quoted_repo = shlex.quote(str(repo))
    gate = [
        "sh",
        "-c",
        (
            f"printf 'concurrent\\n' > {quoted_repo}/shared.txt && "
            f"git -C {quoted_repo} add shared.txt && "
            f"git -C {quoted_repo} commit -m 'advance base during gate'"
        ),
    ]

    result = integrate(repo, ["claude/racing"], gate_command=gate)

    assert [(item.status, item.reason) for item in result.branches] == [("skipped", "not-ready")]
    assert (repo / "shared.txt").read_text(encoding="utf-8") == "concurrent\n"
    assert _git(repo, "status", "--porcelain") == ""


def test_installed_console_entry_runs_real_git_journey(tmp_path, bare_origin) -> None:
    repo = _clone(tmp_path, bare_origin())
    _allow_local(repo)
    _branch(repo, "claude/cli", {"cli.txt": "cli\n"})

    result = _integrate(repo, "claude/cli")

    assert result["branches"] == [{"branch": "claude/cli", "status": "merged", "reason": None}]
    assert (repo / "cli.txt").read_text(encoding="utf-8") == "cli\n"


def test_cli_readable_success(tmp_path, bare_origin, capsys) -> None:
    repo = _clone(tmp_path, bare_origin())
    _allow_local(repo)
    _branch(repo, "claude/readable", {"readable.txt": "readable\n"})

    assert (
        main(
            [
                "--repo",
                str(repo),
                "--gate",
                "true",
                "claude/readable",
            ]
        )
        == 0
    )
    assert "claude/readable: merged" in capsys.readouterr().out


def test_remote_incomplete_integration_is_immutable_then_recovers_via_pr(
    tmp_path, bare_origin, capsys
) -> None:
    """Regression: direct push is rejected before mutation; recovery uses the PR path."""
    origin = bare_origin()
    canonical = _clone(tmp_path, origin)
    workspace = Workspace(
        tmp_path / "dispatch-worktrees",
        resolver=lambda _spec: canonical,
    )
    incomplete = run_repo_task(
        str(canonical),
        "Write partial work and genuinely stop incomplete.",
        "engineer",
        workspace=workspace,
        dispatch_fn=make_writing_dispatch(filename="partial.txt", completed=False),
        recorded_gate=["true"],
        repo_type="single-owner",
    )
    assert incomplete.outcome == "not-completed"
    Registry().register(str(canonical), workflow="remote", repo_type="single-owner")
    local_before = _git(canonical, "rev-parse", "main")
    origin_before = _git(origin, "rev-parse", "main")
    branch_before = _git(canonical, "rev-parse", incomplete.branch)

    assert (
        main(
            [
                "--repo",
                str(canonical),
                "--push",
                incomplete.branch,
            ]
        )
        == 2
    )
    error = capsys.readouterr().err
    assert "workflow=remote" in error and "repo-recover" in error
    assert _git(canonical, "rev-parse", "main") == local_before
    assert _git(origin, "rev-parse", "main") == origin_before
    assert _git(canonical, "rev-parse", incomplete.branch) == branch_before
    assert (
        subprocess.run(
            ["git", "-C", str(origin), "show-ref", "--verify", f"refs/heads/{incomplete.branch}"],
            capture_output=True,
        ).returncode
        != 0
    )

    marker = next(iter(incomplete_commits(canonical, "origin/main", incomplete.branch)))
    github = FakeGitHub(origin)
    recovered = recover_repo(
        str(canonical),
        incomplete.branch,
        workspace_root=tmp_path / "recovery-worktrees",
        recorded_gate=["true"],
        github=github,
    )
    assert recovered.ok and recovered.outcome == "merged"
    assert recovered.pr and recovered.pr.startswith("https://github.com/")
    assert _git(origin, "show", "main:partial.txt").startswith("change by")
    message = _git(canonical, "log", "-1", "--format=%B", incomplete.branch)
    assert f"Orchestrator-Recovered-Incomplete: {marker}" in message
    # GitHub squashes the branch away, so the PR body is what carries the attestation
    # onto `main`; the branch keeps the commit.
    assert f"Orchestrator-Recovered-Incomplete: {marker}" in github.published(recovered.pr).body


def test_publication_carries_one_trailer_per_marker_and_drops_unbacked_claims(
    tmp_path, bare_origin
) -> None:
    """A twice-preserved branch, published once.

    Two markers, attested across two commits that overlap and that also carry a
    trailer naming a commit no marker exists for. Branch messages are agent-written,
    so a trailer copied verbatim onto the base branch would let any well-spelled line
    claim a recovery that never happened. The publication commit gets one trailer per
    marker the history actually carries, in marker order, and nothing else.
    """
    origin = bare_origin()
    repo = _clone(tmp_path, origin)
    _allow_local(repo)
    base_before = _git(repo, "rev-parse", "main")

    _branch(repo, "claude/twice-preserved", {"first.txt": "first\n"})
    _git(repo, "checkout", "claude/twice-preserved")
    _git(repo, "commit", "--amend", "-m", "feat: land the first half")
    _git(
        repo,
        "commit",
        "--allow-empty",
        "-m",
        "chore: first half (incomplete step)\n\nOrchestrator-Status: incomplete",
    )
    first_marker = _git(repo, "rev-parse", "HEAD")
    (repo / "second.txt").write_text("second\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "fix: land the second half")
    _git(
        repo,
        "commit",
        "--allow-empty",
        "-m",
        "chore: second half (incomplete step)\n\nOrchestrator-Status: incomplete",
    )
    second_marker = _git(repo, "rev-parse", "HEAD")
    _git(
        repo,
        "commit",
        "--allow-empty",
        "-m",
        "chore: attest verified recovery of preserved work\n\n"
        f"Orchestrator-Recovered-Incomplete: {first_marker}\n"
        # No marker carries this sha; it attests nothing.
        f"Orchestrator-Recovered-Incomplete: {'d' * 40}",
    )
    _git(
        repo,
        "commit",
        "--allow-empty",
        "-m",
        "chore: attest verified recovery of preserved work\n\n"
        # Restates the first marker alongside the one it adds.
        f"Orchestrator-Recovered-Incomplete: {first_marker}\n"
        f"Orchestrator-Recovered-Incomplete: {second_marker}",
    )
    _git(repo, "checkout", "main")

    recovered = recover_repo(
        repo,
        "claude/twice-preserved",
        workspace_root=tmp_path / "twice-preserved-worktrees",
        recorded_gate=["true"],
    )
    assert recovered.ok and recovered.outcome == "merged", recovered.detail

    published = gitops.log_messages(repo, base_before, "origin/main")
    assert len(published) == 1, [commit.message.splitlines()[0] for commit in published]
    trailers = [
        line
        for line in published[0].message.splitlines()
        if line.startswith("Orchestrator-Recovered-Incomplete:")
    ]
    assert trailers == [
        f"Orchestrator-Recovered-Incomplete: {first_marker}",
        f"Orchestrator-Recovered-Incomplete: {second_marker}",
    ]
    assert _git(origin, "show", "main:second.txt") == "second"


def test_incomplete_history_needs_recovery_attestation_even_after_normal_commit(
    tmp_path, bare_origin
) -> None:
    repo = _clone(tmp_path, bare_origin())
    _allow_local(repo)
    _branch(repo, "claude/incomplete", {"partial.txt": "partial\n"})
    _git(repo, "checkout", "claude/incomplete")
    _git(
        repo,
        "commit",
        "--amend",
        "-m",
        "wip: preserved (incomplete step)\n\nOrchestrator-Status: incomplete",
    )
    (repo / "ordinary.txt").write_text("ordinary\n", encoding="utf-8")
    _git(repo, "add", "ordinary.txt")
    _git(repo, "commit", "-m", "fix: ordinary follow-up")
    _git(repo, "checkout", "main")

    result = integrate(repo, ["claude/incomplete"], gate_command=["true"])

    ((status, reason),) = [(item.status, item.reason) for item in result.branches]
    assert status == "skipped"
    # Naming the other verb is not enough: the skip has to hand over the command
    # that actually publishes this branch, for this repository.
    assert reason is not None
    assert reason.startswith("incomplete-provenance (1 unattested commit(s))")
    assert f"just repo-recover claude/incomplete --repo {repo}" in reason
    assert not result.base_advanced


def test_registered_remote_refresh_is_allowed_without_base_mutation(tmp_path, bare_origin) -> None:
    repo = _clone(tmp_path, bare_origin())
    Registry().register(str(repo), workflow="remote", repo_type="single-owner")
    _branch(repo, "claude/refresh-only", {"feature.txt": "feature\n"})
    base_before = _git(repo, "rev-parse", "main")

    result = integrate(repo, ["claude/refresh-only"], refresh=True)

    assert result.branches[0].status == "updated"
    assert not result.base_advanced and _git(repo, "rev-parse", "main") == base_before


def test_team_identity_refuses_direct_integration(tmp_path, bare_origin) -> None:
    repo = _clone(tmp_path, bare_origin())
    Registry().register(str(repo), workflow="remote", repo_type="team")
    _branch(repo, "feature/team", {"team.txt": "team\n"})

    with pytest.raises(IntegrateError, match="repo_type=team"):
        integrate(repo, ["feature/team"], gate_command=["true"])


def test_permitted_integration_gate_receives_selected_comparison(tmp_path, bare_origin) -> None:
    repo = _clone(tmp_path, bare_origin())
    _allow_local(repo)
    _branch(repo, "claude/env", {"feature.txt": "feature\n"})
    result = integrate(
        repo,
        ["claude/env"],
        gate_command=[
            "sh",
            "-c",
            'test "$ORCHESTRATOR_COMPARISON_REMOTE/$ORCHESTRATOR_COMPARISON_BASE" = origin/main',
        ],
    )
    assert result.branches[0].status == "merged"


def test_repo_recover_cli_uses_explicit_local_workflow(tmp_path, bare_origin, capsys) -> None:
    origin = bare_origin()
    repo = _clone(tmp_path, origin)
    _allow_local(repo)
    _branch(repo, "claude/local-recovery", {"partial.txt": "partial\n"})
    _git(repo, "checkout", "claude/local-recovery")
    _git(
        repo,
        "commit",
        "--amend",
        "-m",
        "wip: partial (incomplete step)\n\nOrchestrator-Status: incomplete",
    )
    _git(repo, "checkout", "main")

    assert (
        recover_main(
            [
                "claude/local-recovery",
                "--repo",
                str(repo),
                "--gate",
                "true",
                "--workspace",
                str(tmp_path / "recover-cli-worktrees"),
                "--format",
                "json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["workflow"] == "local" and payload["outcome"] == "merged"
    # The documented JSON field, not only the human line: this is what a caller
    # parsing the result reads to find the merge path's own gate run.
    assert "verdict: passed" in Path(payload["gate_log"]).read_text(encoding="utf-8")
    assert payload["repo_type"] == "single-owner" and payload["merge_policy"] == "direct"
    assert payload["base"] == payload["pr_base"] == "main"
    assert payload["synthetic_stack_base"] is None
    assert _git(origin, "show", "main:partial.txt") == "partial"

    _branch(repo, "claude/text-recovery", {"text.txt": "text\n"}, start="main")
    _git(repo, "checkout", "claude/text-recovery")
    _git(repo, "commit", "--amend", "-m", "wip: text preserved (incomplete step)")
    _git(repo, "checkout", "main")
    assert (
        recover_main(
            [
                "claude/text-recovery",
                "--repo",
                str(repo),
                "--gate",
                "true",
                "--workspace",
                str(tmp_path / "recover-text-worktrees"),
            ]
        )
        == 0
    )
    assert "claude/text-recovery: merged" in capsys.readouterr().out


def test_repo_recover_command_adopts_a_branch_from_an_explicit_execution_checkout(
    tmp_path, bare_origin
) -> None:
    """The installed command, for the case that used to be unrecoverable.

    A branch that reaches publication on its first attempt has never been pushed,
    so it exists only where the work was done. Driving the installed argument
    route here is what proves the operator's way out of that, and that the reported
    result names the preserved merge-path gate output.
    """
    origin = bare_origin()
    repo = _clone(tmp_path, origin)
    _allow_local(repo)
    execution = tmp_path / "execution-checkout"
    subprocess.run(["git", "clone", str(origin), str(execution)], check=True, capture_output=True)
    _branch(execution, "claude/execution-only", {"partial.txt": "partial\n"})
    _git(execution, "checkout", "claude/execution-only")
    _git(
        execution,
        "commit",
        "--amend",
        "-m",
        "wip: partial (incomplete step)\n\nOrchestrator-Status: incomplete",
    )
    _git(execution, "checkout", "main")
    assert not gitops.branch_exists(repo, "claude/execution-only")

    proc = subprocess.run(
        [
            "orchestrator-repo-recover",
            "claude/execution-only",
            "--repo",
            str(repo),
            "--execution-checkout",
            str(execution),
            "--gate",
            "true",
            "--workspace",
            str(tmp_path / "execution-only-cli-worktrees"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    printed = proc.stdout
    assert proc.returncode == 0, proc.stderr
    assert "claude/execution-only: merged" in printed
    gate_log = printed.rsplit("merge-path gate output: ", 1)[1].strip()
    assert "verdict: passed" in Path(gate_log).read_text(encoding="utf-8")
    assert _git(origin, "show", "main:partial.txt") == "partial"


def test_repo_recover_command_rejects_an_execution_checkout_of_another_repository(
    tmp_path, bare_origin
) -> None:
    repo = _clone(tmp_path, bare_origin())
    _allow_local(repo)
    stranger = tmp_path / "stranger"
    subprocess.run(
        ["git", "clone", str(bare_origin()), str(stranger)], check=True, capture_output=True
    )

    proc = subprocess.run(
        [
            "orchestrator-repo-recover",
            "claude/anything",
            "--repo",
            str(repo),
            "--execution-checkout",
            str(stranger),
            "--gate",
            "true",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 2
    assert "is not a git checkout of the repository identity" in proc.stderr


def test_team_recovery_default_opens_pr_without_polling(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    repo = _clone(tmp_path, origin)
    Registry().register(str(repo), workflow="remote", repo_type="team")
    _branch(repo, "feature/team-recovery", {"team.txt": "partial\n"})
    _git(repo, "checkout", "feature/team-recovery")
    _git(
        repo,
        "commit",
        "--amend",
        "-m",
        "wip: team recovery\n\nOrchestrator-Status: incomplete",
    )
    _git(repo, "checkout", "main")

    result = recover_repo(
        repo,
        "feature/team-recovery",
        workspace_root=tmp_path / "team-recovery-worktrees",
        recorded_gate=["true"],
        github=FakeGitHub(origin, fail_checks=True),
    )

    assert result.ok and result.outcome == "pr-open"
    assert result.repo_type == "team" and result.merge_policy == "none"
    assert result.workflow == "remote" and result.pr_base == "main"
    with pytest.raises(subprocess.CalledProcessError):
        _git(origin, "show", "main:team.txt")


def test_team_recovery_preserves_recorded_linear_stack_base(tmp_path, bare_origin) -> None:
    """Recovering a partial child must not publish its unresolved parent into root."""
    origin = bare_origin()
    repo = _clone(tmp_path, origin)
    Registry().register(str(repo), workflow="remote", repo_type="team")
    workspace = Workspace(tmp_path / "stacked-dispatch-worktrees")
    github = FakeGitHub(origin)
    parent = run_repo_task(
        str(repo),
        "parent",
        "engineer",
        workspace=workspace,
        github=github,
        branch="feature/recovery-parent",
        dispatch_fn=make_writing_dispatch(filename="parent.txt"),
        recorded_gate=["true"],
    )
    assert parent.outcome == "pr-open" and parent.pr is not None
    child = run_repo_task(
        str(repo),
        "partial child",
        "engineer",
        workspace=workspace,
        github=github,
        branch="feature/recovery-child",
        dispatch_fn=make_writing_dispatch(filename="child.txt", completed=False),
        recorded_gate=["true"],
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
    assert child.outcome == "not-completed" and child.pr_base == parent.branch

    with pytest.raises(ValueError, match="conflicts with preserved branch metadata"):
        recover_repo(
            repo,
            child.branch,
            workspace_root=tmp_path / "wrong-stacked-recovery-worktrees",
            base=child.base_branch,
            pr_base=child.base_branch,
            recorded_gate=["true"],
            github=github,
        )

    recovered = recover_repo(
        repo,
        child.branch,
        workspace_root=tmp_path / "stacked-recovery-worktrees",
        base=child.base_branch,
        recorded_gate=["true"],
        github=github,
    )

    assert recovered.outcome == "pr-open" and recovered.pr_base == parent.branch
    assert github._prs[2].base == parent.branch
    assert _git(origin, "show", f"{child.branch}:parent.txt").startswith("change by")
    assert _git(origin, "show", f"{child.branch}:child.txt").startswith("change by")
    with pytest.raises(subprocess.CalledProcessError):
        _git(origin, "show", "main:parent.txt")


def test_repo_recover_cli_requires_registration(tmp_path, bare_origin, capsys) -> None:
    repo = _clone(tmp_path, bare_origin())
    assert recover_main(["missing", "--repo", str(repo), "--gate", "true"]) == 2
    assert "not registered" in capsys.readouterr().err


def test_repo_recover_rejects_branch_without_incomplete_provenance(tmp_path, bare_origin) -> None:
    repo = _clone(tmp_path, bare_origin())
    _allow_local(repo)
    _branch(repo, "claude/ordinary", {"ordinary.txt": "ordinary\n"})
    with pytest.raises(ValueError, match="no lifecycle-preserved incomplete provenance"):
        recover_repo(
            repo,
            "claude/ordinary",
            workspace_root=tmp_path / "ordinary-recovery-worktrees",
            recorded_gate=["true"],
        )


def test_repo_recover_separates_nothing_to_recover_from_the_wrong_verb(
    tmp_path, bare_origin
) -> None:
    """A branch with no commits ahead is a different problem from a complete one."""
    repo = _clone(tmp_path, bare_origin())
    _allow_local(repo)
    _git(repo, "branch", "claude/nothing-ahead", "main")

    with pytest.raises(ValueError, match="no commits ahead of origin/main"):
        recover_repo(
            repo,
            "claude/nothing-ahead",
            workspace_root=tmp_path / "nothing-ahead-worktrees",
            recorded_gate=["true"],
        )


@pytest.mark.parametrize("workflow", ["local", "remote"])
def test_repo_recover_reports_remote_base_sync_conflict(
    tmp_path, bare_origin, workflow: str
) -> None:
    origin = bare_origin({"shared.txt": "seed\n"})
    repo = _clone(tmp_path, origin)
    Registry().register(str(repo), workflow=workflow, repo_type="single-owner")
    _branch(repo, "claude/conflicted-recovery", {"shared.txt": "partial\n"})
    _git(repo, "checkout", "claude/conflicted-recovery")
    _git(repo, "commit", "--amend", "-m", "wip: old preserved (incomplete step)")
    _git(repo, "checkout", "main")
    updater = tmp_path / "recovery-conflict-updater"
    subprocess.run(["git", "clone", str(origin), str(updater)], check=True, capture_output=True)
    (updater / "shared.txt").write_text("remote\n", encoding="utf-8")
    _git(updater, "add", "shared.txt")
    _git(updater, "commit", "-m", "advance shared on remote")
    _git(updater, "push", "origin", "main")

    recovered = _recover_cli(
        repo,
        "claude/conflicted-recovery",
        tmp_path / f"conflicted-{workflow}-recovery-worktrees",
    )
    result = json.loads(recovered.stdout)

    assert recovered.returncode == 1
    assert result["outcome"] == "sync-conflict"
    assert "resolve the conflict" in result["detail"]
    repeated = recover_repo(
        repo,
        "claude/conflicted-recovery",
        workspace_root=tmp_path / f"conflicted-{workflow}-coverage-worktrees",
        recorded_gate=["true"],
    )
    assert repeated.outcome == "sync-conflict"


def test_repo_recover_rejects_missing_branch_and_missing_gate(tmp_path, bare_origin) -> None:
    repo = _clone(tmp_path, bare_origin())
    _allow_local(repo)
    with pytest.raises(ValueError, match="not a valid Git branch"):
        recover_repo(
            repo,
            "bad..branch",
            workspace_root=tmp_path / "invalid-recovery-worktrees",
            recorded_gate=["true"],
        )
    _branch(repo, "claude/conflicting-base", {"partial.txt": "partial\n"})
    invalid_base = _recover_cli(
        repo,
        "claude/conflicting-base",
        tmp_path / "invalid-pr-base-recovery-worktrees",
        extra=("--pr-base", "bad..base"),
    )
    assert invalid_base.returncode == 2
    assert "pr_base 'bad..base' is not a valid Git branch" in invalid_base.stderr
    with pytest.raises(ValueError, match="pr_base .* is not a valid Git branch"):
        recover_repo(
            repo,
            "claude/conflicting-base",
            pr_base="bad..base",
            workspace_root=tmp_path / "invalid-pr-base-coverage-worktrees",
            recorded_gate=["true"],
        )
    _git(repo, "checkout", "claude/conflicting-base")
    _git(
        repo,
        "commit",
        "--amend",
        "-m",
        "wip: conflicting base (incomplete step)\n\n"
        "Orchestrator-Status: incomplete\n"
        "Orchestrator-PR-Base: parent/one\n"
        "Orchestrator-PR-Base: parent/two",
    )
    _git(repo, "checkout", "main")
    with pytest.raises(ValueError, match="records conflicting PR bases"):
        recover_repo(
            repo,
            "claude/conflicting-base",
            workspace_root=tmp_path / "conflicting-base-worktrees",
            recorded_gate=["true"],
        )

    _branch(repo, "claude/empty-base", {"partial.txt": "partial\n"})
    _git(repo, "checkout", "claude/empty-base")
    _git(
        repo,
        "commit",
        "--amend",
        "-m",
        "wip: empty base\n\nOrchestrator-Status: incomplete\nOrchestrator-PR-Base:",
    )
    _git(repo, "checkout", "main")
    with pytest.raises(ValueError, match="records an empty PR base"):
        recover_repo(
            repo,
            "claude/empty-base",
            workspace_root=tmp_path / "empty-base-worktrees",
            recorded_gate=["true"],
        )

    with pytest.raises(ValueError, match="does not exist"):
        recover_repo(
            repo,
            "claude/missing",
            workspace_root=tmp_path / "missing-recovery-worktrees",
            recorded_gate=["true"],
        )

    _branch(repo, "claude/no-gate", {"partial.txt": "partial\n"})
    _git(repo, "checkout", "claude/no-gate")
    _git(repo, "commit", "--amend", "-m", "wip: old preserved (incomplete step)")
    _git(repo, "checkout", "main")
    with pytest.raises(ValueError, match="identity has a no-op gate"):
        recover_repo(
            repo,
            "claude/no-gate",
            workspace_root=tmp_path / "no-gate-recovery-worktrees",
        )


def test_repo_recover_accepts_existing_matching_attestation(tmp_path, bare_origin) -> None:
    origin = bare_origin()
    repo = _clone(tmp_path, origin)
    _allow_local(repo)
    Registry.migrate_identity_gate(str(repo), "test {base} = origin/main")
    _branch(repo, "claude/attested", {"partial.txt": "partial\n"})
    _git(repo, "checkout", "claude/attested")
    _git(repo, "commit", "--amend", "-m", "wip: old preserved (incomplete step)")
    incomplete_sha = _git(repo, "rev-parse", "HEAD")
    _git(
        repo,
        "commit",
        "--allow-empty",
        "-m",
        f"chore: existing recovery\n\nOrchestrator-Recovered-Incomplete: {incomplete_sha}",
    )
    attested_tip = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "main")

    result = recover_repo(
        repo,
        "claude/attested",
        workspace_root=tmp_path / "attested-recovery-worktrees",
    )

    assert result.ok
    assert (
        subprocess.run(
            ["git", "-C", str(origin), "merge-base", "--is-ancestor", attested_tip, "main"]
        ).returncode
        == 1
    )
    assert _git(origin, "show", "main:partial.txt") == "partial"
