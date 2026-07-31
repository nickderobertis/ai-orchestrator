"""E2E: every publishing push tells its merge-path gate which base to judge.

The lifecycle does not run the repository's gate any more — the `pre-push` hook
does, on every publishing push. A hook left to discover its own comparison base
resolves the remote HEAD, which is the repository default rather than the base the
push is actually publishing onto. For a memoized gate tier that is a second,
different question: the worker cleared a verdict keyed on its own base, and the
hook judges a key the worker never saw and can no longer clear.

`tests/e2e/test_gate_verdict_consistency_e2e.py` proves what that costs on the
ordinary path — without the identity, a branch whose own gate *failed* merges.
These journeys pin the same contract on the three publishing pushes that path does
not reach: the human-pause checkpoint, the synthetic stacked base, and recovery.
Each installs a real `pre-push` hook that records what Git handed it, so the
assertion is on the environment the hook actually ran with.
"""

# llmlint: ignore-file[e2e_not_mocked] these repo-lifecycle e2es fake ONLY the paid
# harness (the dispatch_fn seam) and GitHub's PR/CI decisioning while driving real
# git, the real pre-push hook, and the real merge, exactly as AGENTS.md prescribes
# for lifecycle tests. The boundary under test here — what environment Git hands
# the hook on a publishing push — is entirely real.

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from conftest import install_pre_push_hook
from fakes import FakeGitHub, make_writing_dispatch

from orchestrator import gitops
from orchestrator.lifecycle import StackBase, Step, run_repo_task
from orchestrator.provenance import (
    INCOMPLETE_TRAILER,
    PR_BASE_TRAILER,
    format_preserved_step_metadata,
)
from orchestrator.recover import recover_repo
from orchestrator.registry import Registry
from orchestrator.workspace import Workspace


@pytest.fixture(autouse=True)
def _only_the_lifecycle_names_a_comparison_base(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the hook record only what the lifecycle passed, never what leaked in.

    These journeys read one variable out of the environment Git handed the hook,
    and a child process inherits that environment from this one. The lifecycle
    exports the comparison identity to every dispatch, so a worker running the
    suite has `ORCHESTRATOR_COMPARISON_BASE` set — and then *every* push records
    it, including the local `mirror_branch` handover that names no publication
    base at all.

    That is worse than the noise it adds. A publication push that stopped passing
    the identity would still be recorded with the leaked value, so in the one
    environment the lifecycle actually runs in, these assertions could no longer
    fail for their intended reason. Clearing the inherited value is what keeps
    every recorded base attributable to the push that set it.
    """
    for name in ("ORCHESTRATOR_COMPARISON_BASE", "ORCHESTRATOR_COMPARISON_REMOTE"):
        monkeypatch.delenv(name, raising=False)


def _recording_hook(checkout: Path, log: Path) -> None:
    """Install a real hook that records the comparison base Git handed it."""
    install_pre_push_hook(
        checkout,
        f'printf "%s\\n" "${{ORCHESTRATOR_COMPARISON_BASE:-<unset>}}" >>"{log}"',
    )


def _publication_bases(log: Path) -> list[str]:
    """The comparison bases recorded, in push order, by the publishing pushes.

    A run also hands its branch back to the shared execution checkout
    (`Workspace.mirror_branch`), which is a local copy rather than a publication
    onto a base; it now fetches from the destination instead of pushing, so it
    reaches no pre-push hook and records nothing. `<unset>` is dropped anyway,
    because the thing being asserted is that no push *silently* loses the identity:
    a publishing push that did would drop out of this list and shorten it, which is
    what the assertions below are counting.
    """
    recorded = log.read_text(encoding="utf-8").split() if log.exists() else []
    return [base for base in recorded if base != "<unset>"]


def test_human_checkpoint_push_names_the_base_it_publishes_onto(tmp_path, bare_origin) -> None:
    """The draft a human reviews was gated against the branch's own base."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-human")
    log = tmp_path / "checkpoint-bases.log"
    _recording_hook(canonical, log)
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
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        recorded_gate=["true"],
    )

    assert result.outcome == "waiting-human", result.detail
    assert _publication_bases(log) == ["main"]


def test_synthetic_stack_base_push_names_the_root_it_publishes_onto(tmp_path, bare_origin) -> None:
    """A multi-parent base is published onto the root, so that is what gates it."""
    origin = bare_origin()
    canonical = gitops.clone(origin, tmp_path / "canonical-stack")
    Registry().register(str(canonical), workflow="remote", repo_type="team")
    anchors = ["feature-one", "feature-two"]
    for anchor in anchors:
        subprocess.run(["git", "checkout", "-q", "-b", anchor, "main"], cwd=canonical, check=True)
        (canonical / f"{anchor}.txt").write_text(f"{anchor}\n", encoding="utf-8")
        gitops.add_all(canonical)
        gitops.commit(canonical, f"feat: {anchor}")
        gitops.push(canonical, anchor, set_upstream=False)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=canonical, check=True)
    log = tmp_path / "stack-bases.log"
    _recording_hook(canonical, log)

    result = run_repo_task(
        str(canonical),
        "Build on two prerequisites.",
        "engineer",
        workspace=Workspace(tmp_path / "stack-worktrees"),
        github=FakeGitHub(origin),
        stack_bases=[StackBase(anchor) for anchor in anchors],
        dispatch_fn=make_writing_dispatch(filename="feature.txt"),
        recorded_gate=["true"],
    )

    assert result.synthetic_stack_base is not None, result.detail
    # The synthetic base push comes first and is gated against the root; the branch
    # push that follows is gated against the synthetic base it stacks on.
    assert _publication_bases(log) == ["main", result.synthetic_stack_base]


def test_recovery_push_names_the_preserved_branch_stack_base(tmp_path, bare_origin) -> None:
    """Recovery republishes onto the stack its preserved branch recorded."""
    origin = bare_origin({"shared.txt": "root\n"})
    canonical = gitops.clone(origin, tmp_path / "canonical-recovery")
    Registry().register(str(canonical), workflow="remote", repo_type="team")
    synthetic = "ai-orchestrator/stack-base/recovery-base"
    branch = "feature/preserved-recovery"
    subprocess.run(["git", "branch", synthetic, "main"], cwd=canonical, check=True)
    gitops.push(canonical, synthetic, set_upstream=False)
    subprocess.run(["git", "checkout", "-q", "-b", branch, synthetic], cwd=canonical, check=True)
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
    subprocess.run(["git", "checkout", "-q", "main"], cwd=canonical, check=True)
    log = tmp_path / "recovery-bases.log"
    _recording_hook(canonical, log)

    recovered = recover_repo(
        canonical,
        branch,
        workspace_root=tmp_path / "recovery-worktrees",
        github=FakeGitHub(origin),
        recorded_gate=["true"],
    )

    assert recovered.pr_base == synthetic, recovered.detail
    # Not "main": a recovery gated against the repository default would judge a diff
    # the preserved branch never had, and re-roll a verdict nobody can clear.
    assert _publication_bases(log) == [synthetic]
