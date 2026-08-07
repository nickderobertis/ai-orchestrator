"""One invariant, checked across the whole settlement domain.

**A settlement that preserves committed branch work records resume metadata.** The
round fold carries a preserved branch into the next round only when the recorded
result names one (`orchestrator.replan`: ``status in _PRESERVING_STATUSES and resume is
not None``), so an outcome that settles finished commits without recording a
continuation silently discards the branch and the next round re-derives it. That is not
hypothetical: a merge-path gate rejection and a publication that refused its own commit
subject — both after every step had settled ``done`` — cost one planner three
hand-written branch pins in a single run.

The gap was possible because recording was written per remembered outcome. So this
tests the *domain* instead: every member of `LifecycleOutcome` either records a
complete, validatable continuation for a branch carrying commits, or appears below with
a stated reason it cannot. A new outcome is eligible by construction — the production
classification is a subtraction — and the only way back to the old behaviour is to add
one to the exception set, which this file refuses until somebody justifies it here.

Real git throughout: a real bare origin, a real clone, real commits, and the recorded
pin handed to the lifecycle's own `_validate_resume`, which is what the next round runs
it through.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import git

import orchestrator.lifecycle as lc
from orchestrator import gitops
from orchestrator.outcomes import (
    LIFECYCLE_OUTCOMES,
    PRESERVATION_ELIGIBLE_OUTCOMES,
    PRESERVATION_INELIGIBLE_OUTCOMES,
    REJECTED_CONTENT_OUTCOMES,
    LifecycleOutcome,
)
from orchestrator.provenance import unattested_incomplete

#: Why each outcome may settle without recording a continuation. Restated here rather
#: than imported, so the production set and this justification have to be changed
#: together: an outcome quietly moved out of the eligible side fails this file.
UNPRESERVED_REASONS: dict[LifecycleOutcome, str] = {
    "merged": "the work landed on the base; there is nothing left to continue",
    "already-integrated": "the branch content was already on the base",
    "pr-open": "the change is published and the node is done, not held",
    "no-changes": "no commit was produced, so no branch carries work",
    "waiting-human": "the pause records a continuation of its own, with its own steps",
    "resume-failed": "the recorded pin is what failed; re-pinning it repeats the refusal",
    "merge-conflict-retry": "an internal publication-loop signal, never a settled run",
}

LEAD = lc.Step("main", "engineer", "## What\nDo the thing.\n")


@pytest.fixture
def preserved_branch(tmp_path: Path, bare_origin: Callable[..., Path]) -> Path:
    """A real clone whose checked-out branch carries one commit over ``origin/main``."""
    clone = gitops.clone(bare_origin(), tmp_path / "clone")
    git("checkout", "-b", "ai-orchestrator/engineer/preserved", "origin/main", cwd=clone)
    (clone / "preserved.txt").write_text("finished work\n", encoding="utf-8")
    gitops.add_all(clone)
    gitops.commit(clone, "feat: finished work the settlement has to keep")
    return clone


def _settled(outcome: LifecycleOutcome, branch: str) -> lc.LifecycleResult:
    """One settled workstream whose single step completed before the ending."""
    return lc.LifecycleResult(
        repo="local/repo",
        task=LEAD.task,
        persona="engineer",
        base_branch="main",
        branch=branch,
        outcome=outcome,
        steps=[lc.StepResult("main", "engineer", "done")],
    )


def _record(result: lc.LifecycleResult, clone: Path) -> None:
    lc._record_preserved_resume(
        result,
        worktree=clone,
        remote_base="origin/main",
        root_base="main",
        pr_base="main",
        lead=LEAD,
    )


def test_the_exception_set_is_exactly_the_outcomes_justified_here() -> None:
    """Nothing leaves the eligible side without a reason written down for it."""
    assert set(UNPRESERVED_REASONS) == PRESERVATION_INELIGIBLE_OUTCOMES
    assert set(UNPRESERVED_REASONS) <= LIFECYCLE_OUTCOMES
    assert all(reason.strip() for reason in UNPRESERVED_REASONS.values())
    # The classification has to be a partition of the domain, or an outcome could be
    # neither eligible nor justified — which is the state the gap lived in.
    assert PRESERVATION_ELIGIBLE_OUTCOMES | PRESERVATION_INELIGIBLE_OUTCOMES == LIFECYCLE_OUTCOMES
    assert not (PRESERVATION_ELIGIBLE_OUTCOMES & PRESERVATION_INELIGIBLE_OUTCOMES)
    assert REJECTED_CONTENT_OUTCOMES <= PRESERVATION_ELIGIBLE_OUTCOMES


@pytest.mark.parametrize("outcome", sorted(PRESERVATION_ELIGIBLE_OUTCOMES))
def test_an_eligible_settlement_with_commits_records_a_pin_the_next_round_can_adopt(
    outcome: LifecycleOutcome, preserved_branch: Path
) -> None:
    """Complete metadata, and metadata the lifecycle's own validation accepts."""
    branch = gitops.current_branch(preserved_branch)
    result = _settled(outcome, branch)

    _record(result, preserved_branch)

    resume = result.resume
    assert resume is not None, f"{outcome} discarded a branch carrying committed work"
    assert resume.branch == branch
    assert resume.base_branch == "main"
    assert resume.pr_base == "main"
    assert resume.checkpoint == gitops.head_sha(preserved_branch)
    # `retry` is the mode that demands unattested incomplete provenance, so it is
    # claimed only for a branch that has some — every other preserved branch is whole
    # and continues under `continue`.
    marked = bool(unattested_incomplete(preserved_branch, "origin/main", "HEAD"))
    assert resume.mode == ("retry" if marked else "continue")
    # The pin is only worth recording if the round that reads it can use it, so it is
    # put through the same preconditions that round applies rather than merely inspected.
    assert isinstance(lc._validate_resume(preserved_branch, resume, None), lc.ResumePrep)


@pytest.mark.parametrize("outcome", sorted(PRESERVATION_ELIGIBLE_OUTCOMES))
def test_a_continuation_reruns_the_steps_whose_output_the_merge_path_refused(
    outcome: LifecycleOutcome, preserved_branch: Path
) -> None:
    """A rejected tree must not be republished by a continuation that skips every step."""
    result = _settled(outcome, gitops.current_branch(preserved_branch))

    _record(result, preserved_branch)

    assert result.resume is not None
    expected = () if outcome in REJECTED_CONTENT_OUTCOMES else ("main",)
    assert result.resume.completed_steps == expected


@pytest.mark.parametrize("outcome", sorted(PRESERVATION_INELIGIBLE_OUTCOMES))
def test_an_outcome_justified_as_ineligible_records_nothing(
    outcome: LifecycleOutcome, preserved_branch: Path
) -> None:
    result = _settled(outcome, gitops.current_branch(preserved_branch))

    _record(result, preserved_branch)

    assert result.resume is None, UNPRESERVED_REASONS[outcome]


def test_a_settlement_never_overwrites_a_continuation_its_own_path_recorded(
    preserved_branch: Path,
) -> None:
    """The paths that know which steps still have to run keep their own answer."""
    result = _settled("not-completed", gitops.current_branch(preserved_branch))
    result.resume = lc.Resume(
        branch=result.branch,
        base_branch="main",
        pr_base="main",
        checkpoint="deadbeef",
        completed_steps=("main",),
        mode="retry",
    )

    _record(result, preserved_branch)

    assert result.resume.checkpoint == "deadbeef"


def test_a_branch_with_no_commits_over_its_base_records_no_continuation(
    tmp_path: Path, bare_origin: Callable[..., Path]
) -> None:
    """There is nothing to continue, and a pin to an empty branch would fail validation."""
    clone = gitops.clone(bare_origin(), tmp_path / "empty")
    git("checkout", "-b", "ai-orchestrator/engineer/empty", "origin/main", cwd=clone)
    result = _settled("gate-failed", "ai-orchestrator/engineer/empty")

    _record(result, clone)

    assert result.resume is None


def test_an_uncommitted_tree_is_left_for_the_step_that_owns_it(preserved_branch: Path) -> None:
    """Only history is recorded here; committing a dirty tree belongs to the step."""
    (preserved_branch / "half-done.txt").write_text("in progress\n", encoding="utf-8")
    result = _settled("gate-failed", gitops.current_branch(preserved_branch))

    _record(result, preserved_branch)

    assert result.resume is None
